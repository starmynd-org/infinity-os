"""The brake itself: a gate that refuses a dispatch, and a guard that kills a live run.

A ceiling that records a cost is a dashboard. A ceiling that prints a warning is a dashboard with
a red number on it. This file is the part that stops.

Two enforcement points, because they catch different things:

    preflight()   before an engine starts. The cheapest stop there is: nothing is spent, no
                  attempt is charged, and the task was never claimed.
    RunGuard      while it runs. A run that starts under the ceiling and crosses it mid-flight
                  gets SIGTERM, then SIGKILL if it ignores that.

RunGuard has two shapes, and which one applies is decided by who owns the process:

    run()     starts the child itself, reads its stdout, meters every cost line it prints, and
              stops it on the increment that crosses. This is the demo and the test shape.
    watch()   adopts a pid somebody else started and re-reads the METER on a timer. This is the
              runner shape, and it exists because the runner cannot hand its engine over: the
              named fifo between the engine and the stream formatter is what makes `$!` the
              ENGINE rather than the formatter, so the shutdown trap reaches a real engine and
              `$?` is a real engine exit code. A guard that re-parented the engine would quietly
              break both. So the guard stands beside the run instead of around it.

The ordering rule inside the guard, which is the one thing to keep if this file is ever rewritten:
**record the incident, then signal.** A crash between the two leaves a stop that is recorded but
not carried out, and the next preflight refuses anyway because the gate is derived from the meter.
The other order leaves money spent, a process dead, and no record of why -- which the runner then
reads as an unexplained failure and charges the lane an attempt it did not earn.
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import time
from dataclasses import dataclass, field
from decimal import Decimal

import store

from . import price_card, reads

# What a metered child prints. In production the runner reads `total_cost_usd` off the engine's
# stream-json result event; this regex is the same value arriving on a line, so the guard has one
# code path whether it is watching a real engine or a harness.
COST_LINE = re.compile(r"\[cost\]\s+\$?([0-9]+(?:\.[0-9]+)?)")

GRACE_SECONDS = float(os.environ.get("BUDGET_STOP_GRACE", "5"))


def _proc_stat(pid: int) -> tuple[str, str] | None:
    """`(state, start time)` for a pid, or None when it cannot be read.

    Both fields exist for the same reason: an adopted pid is a number, and a guard needs it to be
    an identity. The start time says the number was not recycled onto a different process. The
    state says whether there is still a process behind it at all -- `os.kill(pid, 0)` succeeds on
    a ZOMBIE, which is what a pid is between exiting and being reaped by its parent, so signal
    probing alone reports a finished run as a live one. The runner's engine is reaped promptly
    because bash sits in `wait`, but "promptly" is not "always", and a guard that believes a dead
    run is alive polls the meter forever and outlives what it was guarding.

    Read past the comm field with `rindex(")")` because a process name may itself contain spaces
    and parentheses. State is field 3, start time field 22, and after the comm those are offsets
    0 and 19.
    """
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8", errors="replace") as fh:
            data = fh.read()
        fields = data[data.rindex(")") + 2:].split()
        return fields[0], fields[19]
    except (OSError, ValueError, IndexError):
        return None


@dataclass
class Decision:
    """Allowed or not, and the reason, in the operator's words rather than a boolean."""
    allowed: bool
    verdict: str                      # 'allow' | 'warn' | 'stop'
    reason: str = ""
    stopping: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    open_stops: list = field(default_factory=list)
    # Set when the refusal was decided and its audit row could not be written. Empty on every
    # healthy path. See `preflight`: the money is protected either way, the COUNT is what is lost.
    record_failed: str = ""

    def line(self) -> str:
        return f"{'ALLOW' if self.allowed else 'STOP '}  {self.verdict}: {self.reason}"


def evaluate(agent: str = "", lane: str = "", work_item_id: str = "") -> Decision:
    """Read-only: is this unit of work allowed to spend right now, and why.

    Reads two things, and reading only the first is the mistake worth naming: `budget_state`
    carries spend against ceilings, and `budget_open_stop` carries manual stops, which have no
    ceiling and no spend. A scope with `budget stop` typed against it and no policy set is
    invisible to the first query alone.
    """
    policies = reads.covering_policies(agent, lane, work_item_id)
    stops = reads.open_stops(agent, lane, work_item_id)

    stopping = [p for p in policies if p["stopping"]]
    warnings = [p for p in policies if p["over_warn"] and not p["stopping"]]
    # A spend-derived stop and an open incident are two ways to be stopped; a manual stop is
    # reported separately because raising the ceiling does not clear it.
    manual = [s for s in stops if s["kind"] == "manual_stop"]

    if manual:
        s = manual[0]
        return Decision(False, "stop", open_stops=stops, stopping=stopping, warnings=warnings,
                        reason=(f"manual budget stop on {s['scope_type']}"
                                f"{':' + s['scope_id'] if s['scope_id'] else ''} since "
                                f"{s['occurred_at']:%Y-%m-%dT%H:%M:%SZ} -- {s['detail'] or 'no reason given'}. "
                                f"This does not clear itself; `budget resume` is the only way out."))
    if stopping:
        p = stopping[0]
        return Decision(False, "stop", stopping=stopping, warnings=warnings, open_stops=stops,
                        reason=(f"{p['scope_type']}{':' + p['scope_id'] if p['scope_id'] else ''} "
                                f"spend ${p['spend_usd']} of ${p['limit_usd']} ceiling "
                                f"({p['percent_used']}%) over the {p['period']} window"))
    if warnings:
        p = warnings[0]
        return Decision(True, "warn", warnings=warnings, stopping=stopping, open_stops=stops,
                        reason=(f"{p['scope_type']}{':' + p['scope_id'] if p['scope_id'] else ''} "
                                f"at {p['percent_used']}% of ${p['limit_usd']} "
                                f"(warn at {p['warn_percent']}%)"))
    if not policies:
        return Decision(True, "allow", reason="no ceiling covers this work")
    return Decision(True, "allow", reason=(
        f"{len(policies)} ceiling(s), highest use "
        f"{max((p['percent_used'] or 0) for p in policies)}%"))


def preflight(agent: str = "", lane: str = "", work_item_id: str = "",
              by: str = "budget-preflight", record: bool = True) -> Decision:
    """The dispatch gate. Call this BEFORE claiming a task and starting an engine.

    A refused dispatch is recorded as `blocked_dispatch`, which is a typed incident and not a
    stop: nothing was running, so nothing was stopped. That distinction is what makes "we caught
    it before spending" countable afterwards.

    THE DECISION OUTLIVES ITS OWN AUDIT ROW. Task 0301. The write below used to be un-caught, so
    any error in it propagated out of this function -- and `budget/cli.py` turns every exception
    into exit 2, which is not the exit 3 the runner stops on. A refusal that was correctly DECIDED
    was therefore thrown away because the row COUNTING it could not be written, and the runner
    dispatched, claimed and spent. Reproduced against a healthy store by adding one CHECK
    constraint to `brain.budget_incident` for six seconds: the suite went 58/4 with the money
    spent (`test-budget-wiring.sh` scene 5, $0.42 charged past a $0.01 ceiling).
    The two failures are not equal and the code now says which is which: an unwritable incident
    costs the operator a NUMBER, an un-returned stop costs the operator MONEY.
    """
    decision = evaluate(agent, lane, work_item_id)
    if not decision.allowed and record:
        p = (decision.stopping or [None])[0]
        try:
            store.apply(
                "budget note", actor=by,
                kind="blocked_dispatch",
                scope_type=p["scope_type"] if p else (
                    decision.open_stops[0]["scope_type"] if decision.open_stops else "fleet"),
                scope_id=(p["scope_id"] if p else (
                    decision.open_stops[0]["scope_id"] if decision.open_stops else "")),
                policy_id=p["policy_id"] if p else None,
                period=p["period"] if p else None,
                spend_usd=p["spend_usd"] if p else 0,
                limit_usd=p["limit_usd"] if p else None,
                percent_used=p["percent_used"] if p else None,
                action_taken="dispatch_refused", detected_by="preflight",
                agent=agent, lane=lane, work_item_id=work_item_id or None,
                detail=decision.reason,
            )
        except Exception as exc:                   # noqa: BLE001 - the refusal stands regardless
            # Not swallowed: carried. The caller reports it (see `budget check`), so the operator
            # reads "refused, and this refusal is NOT in the count" rather than nothing at all.
            decision.record_failed = f"{exc.__class__.__name__}: {exc}"
    return decision


def charge_and_check(*, usd, source_ref: str, source: str = "stream", agent: str = "",
                     lane: str = "", work_item_id: str | None = None, run_id: int | None = None,
                     session_id: str = "", by: str = "budget-guard",
                     note: str = "") -> tuple[dict, Decision]:
    """Meter one increment, then ask the same question the gate asks.

    Charging and deciding are deliberately two calls to two verbs rather than one clever verb: the
    charge is a state change that must land whatever the decision turns out to be, and a decision
    that rolled back its own evidence would under-count the very spend it is judging.
    """
    charged = store.apply("budget charge", actor=by, usd=usd, source_ref=source_ref,
                          source=source, agent=agent, lane=lane, work_item_id=work_item_id,
                          run_id=run_id, session_id=session_id, note=note)
    return charged, evaluate(agent, lane, work_item_id or "")


class RunGuard:
    """Runs a child process under a ceiling and kills it when the ceiling is crossed.

    Not a wrapper that reports afterwards. The child is started in its own process group, so a
    stop reaches the whole tree rather than just the shell that fronted it -- an engine that
    spawned tools would otherwise keep running under a dead parent, still spending.
    """

    def __init__(self, *, agent: str = "", lane: str = "", work_item_id: str | None = None,
                 run_id: int | None = None, session_id: str = "", by: str = "budget-guard",
                 grace: float = GRACE_SECONDS, on_event=None):
        self.agent, self.lane, self.work_item_id = agent, lane, work_item_id
        self.run_id, self.session_id, self.by = run_id, session_id, by
        self.grace = grace
        self.on_event = on_event or (lambda *_: None)
        self.proc: subprocess.Popen | None = None
        # The guarded process, whether we started it (`run`) or adopted it (`watch`). Everything
        # that signals reads THIS and not `self.proc`, because `watch` has no Popen to read.
        self.pid: int | None = None
        self._born: str | None = None      # set by watch(), which adopts rather than starts
        self.charges: list[dict] = []
        self.spent = Decimal("0")
        # session id -> how many MID_STEP increments of that session's derived spend are already
        # on the meter. Per session, because one stream file can carry two of them.
        self._mid_level: dict[str, int] = {}
        self.incident_id: int | None = None
        self.stopped = False
        self.warned = False
        self.soft_breached = False
        self.signal_sent = ""
        self.exit_code: int | None = None
        self.output: list[str] = []

    # -------------------------------------------------------------- running

    def run(self, argv: list[str], cwd: str | None = None, env: dict | None = None) -> int:
        """Start the child, meter it, stop it if it crosses. Returns its exit code."""
        gate = evaluate(self.agent, self.lane, self.work_item_id or "")
        if not gate.allowed:
            # The ceiling stops a run that never begins. Nothing is spent and nothing is charged.
            self.stopped = True
            self.exit_code = -1
            self._say(f"REFUSED BEFORE START: {gate.reason}")
            return self.exit_code

        self.proc = subprocess.Popen(
            argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, start_new_session=True,   # its own process group
        )
        self.pid = self.proc.pid
        self._say(f"started pid={self.proc.pid} pgid={os.getpgid(self.proc.pid)}: {' '.join(argv)}")

        seq = 0
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            line = line.rstrip("\n")
            self.output.append(line)
            self._say(f"  child| {line}")
            m = COST_LINE.search(line)
            if not m:
                continue
            seq += 1
            decision = self._meter(Decimal(m.group(1)), seq)
            if not decision.allowed:
                self._stop(decision)
                break

        rc = self.proc.wait()
        self.exit_code = rc
        self._say(f"child exited rc={rc}")
        return rc

    # -------------------------------------------------------------- watching

    def watch(self, pid: int, *, poll: float = 5.0, session_probe=None, stream: str = "",
              derive: bool = True) -> bool:
        """Guard a process this object did NOT start. True if it stopped it, False if it ended.

        WHAT THIS CATCHES. The meter is shared: fleet and agent ceilings are summed across every
        terminal. So one thing that takes a live run over its ceiling is NOT that run -- it is a
        sibling terminal finishing and charging, or the operator typing `budget stop`. Both move
        the meter under a run that is already in flight, and before this loop existed neither
        reached it: the ceiling braked between runs, so with six terminals on long tasks one
        over-spend became six.

        AND, SINCE 0251, A RUN CROSSING ITS CEILING ON ITS OWN SPEND. This docstring used to say
        that was impossible, and the sentence is worth keeping in view because it was half right
        and the wrong half stopped anyone from trying:

            "Claude Code's stream-json carries `total_cost_usd` on the terminal `result` event and
            nowhere else ... Turning those into dollars needs a price table keyed by model and
            date, which no lane here owns."

        The first clause is true and has now been verified twice, over 153 stream files rather
        than 40: no USD-bearing key appears anywhere except on `type=result`. The second clause
        was an assumption. `budget/price_card.py` owns that price table now -- the published Opus
        5 list rates, reproduced against Claude Code's own billed `total_cost_usd` to $0.00000000
        on 162 of 165 completed sessions in `~/.swarm/runs`, with all three exceptions named and
        classified rather than absorbed. So `--stream` no longer just yields a session id: it
        yields dollars, every poll, and this loop charges them.

        What that buys, in the numbers that motivated it: 31 of 146 completed runs individually
        cost more than $10 and the largest single run was $34.55. All of that a lone terminal
        could spend unobserved before this.

        `derive=False` turns the derivation off and leaves the sibling-terminal brake alone. It is
        the switch to reach for if the card ever goes stale in a hurry -- the end-of-run truth-up
        still lands the full, correct amount, so a fleet running with derivation off is metered
        exactly as it was before, just later.

        `session_probe` is called while the session id is still unknown and returns one or "".
        The runner learns its session id from the engine's own output, so the guard cannot be
        given it at launch. A stop MUST carry one of the two keys `halt.budget_stop_for_run`
        matches on, or it is reconciled as an ordinary task failure; `run_id` is the one available
        at launch (`swarm run-start` prints it), and on an engine that writes no stream-json it is
        the only one there will ever be. The probe still runs when a run id is held: two keys cost
        nothing and the session id is what survives a run row being reused.
        """
        self.pid = pid
        # Pin the pid to the process that holds it NOW. An adopted pid is not ours to reap, so
        # between the engine exiting and the runner's `wait` collecting it the number can be
        # recycled onto something unrelated -- and a guard that then SIGKILLed a process group is
        # worse than a guard that never fired. A changed start time reads as "it exited".
        self._born = (_proc_stat(pid) or (None, None))[1]
        started = time.time()
        while self._alive():
            if not self.session_id and session_probe is not None:
                self.session_id = session_probe() or ""
            time.sleep(poll)
            if not self._alive():
                break
            # BEFORE the decision, not after. A poll that reads the stream, finds this run has
            # crossed the ceiling on its own spend, and then evaluates against a meter it has not
            # yet moved would let the run keep going for one more interval -- and on a run that
            # spends $34 the interval is the whole point.
            if stream and derive:
                self._meter_stream(stream)
            decision = evaluate(self.agent, self.lane, self.work_item_id or "")
            if decision.allowed:
                continue
            # Last chance to learn the session id. The warning fires only when NEITHER key is
            # held, because that is the condition that actually misfiles: a stop with no key the
            # runner can match, so `halt` finds no first-party evidence and reads the kill as an
            # ordinary task failure. A run id alone is a complete key.
            if not self.session_id and session_probe is not None:
                self.session_id = session_probe() or ""
            if not self.session_id and self.run_id is None:
                self._say("  WARNING: stopping a run with neither a run id nor a session id. "
                          "`budget halt` matches first-party evidence on run_id or session_id, so "
                          "this stop will not be matched to its run and may be misfiled as a task "
                          "failure. Pass --run from `swarm run-start`.")
            self._say(f"  the meter moved under a live run after {int(time.time() - started)}s: "
                      f"{decision.reason}")
            self._stop(decision, detected_by="sweep")
            return True
        self._say("the guarded run ended on its own; no ceiling was crossed while it ran")
        return False

    def _alive(self) -> bool:
        """Is the guarded process still running, whether we started it or adopted it."""
        if self.proc is not None:
            return self.proc.poll() is None
        if self.pid is None:
            return False
        stat = _proc_stat(self.pid)
        if stat is None:
            try:
                os.kill(self.pid, 0)   # no /proc: fall back to the signal probe alone
            except ProcessLookupError:
                return False
            except PermissionError:
                pass                   # alive, just not ours to signal
            return True
        state, born = stat
        if state == "Z":
            return False               # exited, waiting to be reaped. Not something to signal.
        if self._born is not None and born != self._born:
            return False               # the number was recycled onto a different process
        return True

    # -------------------------------------------------------------- metering

    def _meter(self, amount: Decimal, seq: int) -> Decision:
        ref = f"{self.session_id or self.agent or 'run'}:{self.run_id or 0}:{seq}"
        return self._charge(amount, ref, f"increment {seq}", session_id=self.session_id)

    # ---------------------------------------------------------- deriving spend from the stream

    # What one mid-run charge is worth. The guard charges in fixed steps rather than "whatever has
    # accrued since the last poll", and the reason is idempotency under the one condition this
    # lane has actually measured: two terminals running the same (task, attempt) share one stream
    # file (task 0244), so two guards can be reading the same bytes at different instants.
    #
    # Keyed on a STEP INDEX, every guard that reads the same stream derives the same (ref, amount)
    # pairs no matter when it polls, so the second one's charges dedup on (source, source_ref) and
    # the meter lands once. Keyed on a poll sequence instead -- ref `sid:mid:1`, amount "whatever
    # accrued" -- the two guards would agree on the ref and disagree on the amount, and the ledger
    # would take whichever raced there first plus the other's next increment on top. That is an
    # OVER-charge, and an over-counting brake stops a healthy fleet exactly as expensively as an
    # under-counting one lets it burn.
    #
    # The same property makes a guard restart harmless: it re-derives steps 1..N, every one of
    # them dedups, and it picks up where the ledger already is. The cost is granularity -- the
    # meter tracks a live run to within one step, and the remainder lands at end of run through
    # the truth-up. 25c against ceilings written in dollars, and 138 rows on the most expensive
    # run this fleet has ever produced ($34.55).
    MID_STEP = Decimal("0.25")

    def _meter_stream(self, stream: str) -> None:
        """Read the live stream, derive what each session has spent, and charge the difference.

        Charges EVERY session in the file, not just this guard's own. A file holding two sessions
        means two terminals ran one attempt, and the spend of the one that is not ours is still
        the operator's money moving under a live ceiling. Each charge carries its own session id
        rather than the guard's, so the ledger attributes it correctly.
        """
        try:
            derived = price_card.derive_stream(stream)
        except Exception as exc:                      # noqa: BLE001 - a brake never dies reading
            # The stream is written by another process and read while it is being appended to. A
            # reader that raised here would kill the guard and take the sibling-terminal brake
            # down with it, which is strictly worse than missing one poll of derivation.
            self._say(f"  WARNING: could not derive spend from {stream}: {exc!r}. "
                      f"The ceiling still brakes on other terminals' charges; this run's own "
                      f"spend will land at end of run.")
            return

        for sid, usd in sorted(derived.items()):
            if not sid:
                continue
            level = int(usd / self.MID_STEP)
            done = self._mid_level.get(sid, 0)
            if level <= done:
                continue
            for step in range(done + 1, level + 1):
                self._charge(self.MID_STEP, f"{sid}:mid:{step}",
                             f"mid-run step {step} (derived ${usd} so far)", session_id=sid)
            self._mid_level[sid] = level
            self._say(f"  derived ${usd} spent so far by session {sid[:8]}; "
                      f"metered {level} x ${self.MID_STEP} of it")

    def _charge(self, amount: Decimal, ref: str, note: str, session_id: str = "") -> Decision:
        charged, decision = charge_and_check(
            usd=amount, source_ref=ref, source="stream", agent=self.agent, lane=self.lane,
            work_item_id=self.work_item_id, run_id=self.run_id,
            session_id=session_id or self.session_id, by=self.by, note=note)
        self.charges.append(charged)
        self.spent += amount
        self._say(f"  charged ${amount} (running ${self.spent}) -> {decision.line()}")

        # A ceiling crossed with hard_stop_enabled=false is a measurement, not a brake, and it is
        # recorded as `soft_breach` rather than left to look like an ordinary warning. This lives
        # here rather than in _stop() because a soft policy never makes evaluate() return `stop`
        # -- an earlier draft put the branch in _stop() where it was unreachable, so the record
        # was never written at all.
        soft = [q for q in decision.warnings if q["over_limit"] and not q["hard_stop_enabled"]]
        if soft and not self.soft_breached:
            self.soft_breached = True
            q = soft[0]
            store.apply("budget note", actor=self.by, kind="soft_breach",
                        scope_type=q["scope_type"], scope_id=q["scope_id"],
                        policy_id=q["policy_id"], period=q["period"], spend_usd=q["spend_usd"],
                        limit_usd=q["limit_usd"], percent_used=q["percent_used"],
                        action_taken="none", detected_by="charge", agent=self.agent,
                        lane=self.lane, work_item_id=self.work_item_id, run_id=self.run_id,
                        session_id=self.session_id,
                        detail="ceiling crossed with hard_stop_enabled=false: "
                               "recorded, deliberately not stopped")
            self._say(f"  SOFT BREACH: {q['scope_type']}:{q['scope_id']} is over ${q['limit_usd']}"
                      f" and hard_stop_enabled is false, so this is recorded, not stopped")

        if decision.verdict == "warn" and not self.warned:
            self.warned = True
            p = decision.warnings[0]
            store.apply("budget note", actor=self.by, kind="warn",
                        scope_type=p["scope_type"], scope_id=p["scope_id"],
                        policy_id=p["policy_id"], period=p["period"], spend_usd=p["spend_usd"],
                        limit_usd=p["limit_usd"], percent_used=p["percent_used"],
                        action_taken="warned", detected_by="charge", agent=self.agent,
                        lane=self.lane, work_item_id=self.work_item_id, run_id=self.run_id,
                        session_id=self.session_id, detail=decision.reason)
            self._say(f"  WARN filed (a warning is not a stop): {decision.reason}")
        return decision

    # -------------------------------------------------------------- stopping

    def _stop(self, decision: Decision, detected_by: str = "charge") -> None:
        # `stopping` is empty only for a manual stop, which carries no policy row. Its scope still
        # comes off the stop itself rather than defaulting to `fleet`: an operator who types
        # `budget stop agent T2` and reads back an incident scoped to the whole fleet has been
        # told something untrue about what was stopped.
        p = (decision.stopping or [None])[0]
        s = (decision.open_stops or [None])[0]

        # RECORD FIRST, THEN SIGNAL. See the module docstring.
        incident = store.apply(
            "budget stop", actor=self.by, kind="hard_stop",
            scope_type=p["scope_type"] if p else (s["scope_type"] if s else "fleet"),
            scope_id=p["scope_id"] if p else (s["scope_id"] if s else ""),
            policy_id=p["policy_id"] if p else None,
            period=p["period"] if p else None,
            spend_usd=p["spend_usd"] if p else self.spent,
            limit_usd=p["limit_usd"] if p else None,
            percent_used=p["percent_used"] if p else None,
            action_taken="run_stopped", detected_by=detected_by,
            agent=self.agent, lane=self.lane, work_item_id=self.work_item_id,
            run_id=self.run_id, session_id=self.session_id,
            engine_pid=self.pid,
            reason=decision.reason, by=self.by)
        self.incident_id = incident["id"]
        self.stopped = True
        self._say(f"  HARD STOP: incident {self.incident_id} filed BEFORE the signal. "
                  f"{decision.reason}")

        self.signal_sent = self._kill()
        store.apply("budget outcome", actor=self.by, incident_id=self.incident_id,
                    signal_sent=self.signal_sent,
                    detail=f"engine pid {self.pid if self.pid else '?'} "
                           f"stopped with {self.signal_sent}; spend at stop ${self.spent}")

    def _target(self):
        """Where a signal goes: the whole process group, or just the pid. Never our own group.

        `run()` starts its child with `start_new_session`, so the group is always the child's
        alone and this always picks the group. `watch()` adopts a pid somebody else started, and
        bash without job control leaves a plain background child in the SHELL's process group --
        killpg on that would take the runner down with the engine it is guarding. The engine is
        normally fronted by `timeout`, which does call setpgid, so the group path is the one that
        runs; this branch is the guarantee, not the expectation.
        """
        pgid = os.getpgid(self.pid)
        if pgid == os.getpgid(0):
            return (False, self.pid, f"pid {self.pid} only "
                                     f"(it shares this process group, so the group is not a target)")
        return (True, pgid, f"process group {pgid}")

    def _kill(self) -> str:
        """SIGTERM the process group, escalate to SIGKILL. Reports what was actually sent."""
        if self.pid is None or not self._alive():
            return "none (already exited)"
        try:
            group, target, label = self._target()
        except ProcessLookupError:
            return "none (already exited)"
        send = os.killpg if group else os.kill
        send(target, signal.SIGTERM)
        self._say(f"  SIGTERM -> {label}")
        deadline = time.time() + self.grace
        while time.time() < deadline:
            if not self._alive():
                return "SIGTERM"
            time.sleep(0.05)
        try:
            send(target, signal.SIGKILL)
        except ProcessLookupError:
            return "SIGTERM"
        self._say(f"  child ignored SIGTERM for {self.grace}s, SIGKILL -> {label}")
        return "SIGTERM,SIGKILL"

    def _say(self, msg: str) -> None:
        self.on_event(msg)
