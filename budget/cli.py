"""`budget set`, `budget status`, `budget stop`, and the three that make them work.

A thin wrapper. Argument parsing and rendering, nothing else: every state change here is one
`store.apply(verb, ...)` call, so the console, the MCP server and an n8n webhook can be equally
thin over the same verbs without any of them being able to invent a transition. If a reader finds
business logic in this file, that is the bug.

Rendering truncates. The store does not: `budget status --json` prints whole values, and no text
column in migration 3 cuts anything.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal

import store

from . import enforcer, halt, price_card, reads

# Exit codes, because the runner branches on them rather than on stdout.
EXIT_OK = 0
EXIT_USAGE = 2
EXIT_STOPPED = 3        # a budget stop. Distinct from...
EXIT_REFUSAL = 4        # ...a subscription refusal, which is a different condition entirely.
EXIT_MISMATCH = 5       # the meter disagrees with the engine's own billing. Not a stop: nothing
                        # was refused and nothing was killed. See `budget selfcheck`.


def _json_default(o):
    if isinstance(o, Decimal):
        return float(o)
    if isinstance(o, datetime):
        return o.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return str(o)


def _dump(obj) -> None:
    print(json.dumps(obj, indent=2, default=_json_default))


def _bar(pct) -> str:
    if pct is None:
        return "          "
    n = min(10, int(float(pct) / 10))
    return "#" * n + "." * (10 - n)


# --------------------------------------------------------------------------- verbs


def cmd_set(a) -> int:
    row = store.apply("budget set", actor=a.by, scope_type=a.scope, scope_id=a.id,
                      limit_usd=a.limit, period=a.period, warn_percent=a.warn,
                      hard_stop_enabled=not a.no_hard_stop, set_by=a.by, note=a.note)
    if a.json:
        _dump(row)
        return EXIT_OK
    print(f"ceiling set: {row['scope_type']}{':' + row['scope_id'] if row['scope_id'] else ''} "
          f"${row['limit_usd']} per {row['period']}, warn at {row['warn_percent']}%, "
          f"hard stop {'ON' if row['hard_stop_enabled'] else 'OFF'}")
    if row["acknowledged_incidents"]:
        print(f"  closed open hard_stop record(s) {row['acknowledged_incidents']} for this scope")
    if row["superseded_policy_id"]:
        print(f"  supersedes policy {row['superseded_policy_id']} (retired, not deleted, so a "
              f"breach keeps saying what the ceiling was at the time)")
    if not row["hard_stop_enabled"]:
        print("  NOTE: hard stop is OFF. This ceiling measures and does not brake.")
    return EXIT_OK


def cmd_unset(a) -> int:
    row = store.apply("budget unset", actor=a.by, scope_type=a.scope, scope_id=a.id,
                      period=a.period, by=a.by, reason=a.reason)
    print(f"retired {row['count']} ceiling(s): {row['retired'] or 'none were set'}")
    if row["acknowledged_incidents"]:
        print(f"  closed open hard_stop record(s) {row['acknowledged_incidents']}: revisiting "
              f"the ceiling is the answer to a spend-derived stop")
    print("  Retired, not deleted: a past breach keeps saying what the ceiling was at the time.")
    return EXIT_OK


def cmd_status(a) -> int:
    rows = reads.status(a.scope, a.id)
    if a.json:
        _dump({"policies": rows, "open_stops": reads.open_stops(),
               "recent_incidents": reads.incidents(limit=10)})
        return EXIT_OK
    if not rows:
        print("no ceilings set. Nothing is braked.")
    else:
        print(f"{'SCOPE':<22} {'PERIOD':<12} {'SPEND':>10} {'CEILING':>10} {'USE':>7}  "
              f"{'':<10} STATE")
        for r in rows:
            scope = r["scope_type"] + (f":{r['scope_id']}" if r["scope_id"] else "")
            state = ("STOPPED" if r["stopping"] else
                     "OVER (soft)" if r["over_limit"] else
                     "warn" if r["over_warn"] else "ok")
            if r["manually_stopped"]:
                state = "STOPPED (manual)"
            print(f"{scope:<22} {r['period']:<12} {float(r['spend_usd']):>10.4f} "
                  f"{float(r['limit_usd']):>10.4f} {str(r['percent_used'] or '-'):>6}%  "
                  f"{_bar(r['percent_used']):<10} {state}")
    stops = reads.open_stops()
    if stops:
        print("\nOPEN STOPS (a manual_stop latches until `budget resume`; a hard_stop stays")
        print("open until the ceiling for that scope is revisited):")
        for s in stops:
            print(f"  #{s['incident_id']} {s['kind']} {s['scope_type']}"
                  f"{':' + s['scope_id'] if s['scope_id'] else ''} at "
                  f"{s['occurred_at']:%Y-%m-%dT%H:%M:%SZ} -- {s['detail']}")
    inc = reads.incidents(limit=a.incidents)
    if inc:
        print(f"\nLAST {len(inc)} INCIDENTS (cause is always 'budget'; a rate-limit refusal "
              f"cannot be filed here):")
        for i in inc:
            print(f"  #{i['id']:<4} {i['occurred_at']:%H:%M:%SZ} {i['kind']:<17} "
                  f"{i['action_taken']:<17} {i['detected_by']:<10} {i['detail'][:60]}")
    return EXIT_OK


def cmd_stop(a) -> int:
    row = store.apply("budget stop", actor=a.by, scope_type=a.scope, scope_id=a.id,
                      kind="manual_stop", by=a.by, reason=a.reason, detected_by="manual")
    print(f"STOPPED {a.scope}{':' + a.id if a.id else ''} -- incident {row['id']}, "
          f"cause={row['cause']}, disposition={row['prescribed_disposition']}")
    print("  This does not clear itself. `budget resume` is the only way out, on purpose:")
    print("  a subscription window reopens by itself, the operator's money does not.")
    return EXIT_OK


def cmd_resume(a) -> int:
    row = store.apply("budget resume", actor=a.by, scope_type=a.scope, scope_id=a.id,
                      by=a.by, reason=a.reason)
    print(f"lifted {row['count']} manual stop(s): {row['lifted'] or 'none were open'}")
    return EXIT_OK


def cmd_check(a) -> int:
    """The dispatch gate. Exit 3 means stop. Call before claiming, not after."""
    d = enforcer.preflight(agent=a.agent, lane=a.lane, work_item_id=a.task or "",
                           by=a.by, record=not a.dry_run)
    if a.json:
        _dump({"allowed": d.allowed, "verdict": d.verdict, "reason": d.reason,
               "record_failed": d.record_failed})
    else:
        print(d.line())
    # Task 0301: said out loud, and AFTER the verdict, because the verdict is the part that
    # protects the money. The runner captures this text, so an uncountable refusal is legible in
    # the run log instead of being a blocked_dispatch row nobody can find.
    if d.record_failed:
        sys.stdout.flush()      # so the verdict is above this in the runner's captured 2>&1, not below
        print(f"  the refusal STANDS and was NOT recorded: {d.record_failed}. `budget incidents "
              f"--kind blocked_dispatch` will under-count by one.", file=sys.stderr)
    return EXIT_OK if d.allowed else EXIT_STOPPED


def cmd_charge(a) -> int:
    row = store.apply("budget charge", actor=a.by, usd=a.usd, source_ref=a.ref, source=a.source,
                      agent=a.agent, lane=a.lane, work_item_id=a.task, session_id=a.session,
                      note=a.note, truth_up=a.truth_up)
    if row.get("truth_up"):
        print(f"truth-up: ${row['authoritative_usd']} billed for session {a.session or '<none>'}, "
              f"${row['already_charged_usd']} already metered mid-run")
        if row["over_charged_usd"]:
            print(f"  WARNING: the meter already holds ${row['over_charged_usd']} MORE than the "
                  f"engine says this session cost. The remainder clamps at zero (this ledger "
                  f"models no refund) and the overshoot is left standing so it is countable. "
                  f"`budget selfcheck` says whether the card or the stream is responsible.")
    if row["charged"]:
        print(f"charged ${row['usd']} (charge {row['charge_id']})")
    else:
        print(f"already charged as {row['duplicate_of']} (${row['usd']}); "
              f"idempotent on (source, source_ref), so this is a no-op rather than a double count")
    d = enforcer.evaluate(a.agent, a.lane, a.task or "")
    print(d.line())
    return EXIT_OK if d.allowed else EXIT_STOPPED


def cmd_incidents(a) -> int:
    rows = reads.incidents(limit=a.limit, kind=a.kind, open_only=a.open)
    if a.json:
        _dump(rows)
        return EXIT_OK
    for i in rows:
        print(f"#{i['id']:<4} {i['occurred_at']:%Y-%m-%dT%H:%M:%SZ} {i['kind']:<17} "
              f"cause={i['cause']:<8} {i['action_taken']:<17} "
              f"disp={i['prescribed_disposition']:<14} {i['detail'][:70]}")
    if not rows:
        print("no incidents")
    return EXIT_OK


def cmd_outcome(a) -> int:
    """Attach what was observed to the incident that predicted it. Task 0141.

    The incident row is written BEFORE the signal is sent, so until this lands it states an
    intention: `action_taken` says what the enforcer meant to do. `budget outcome` is what turns
    it into a report -- which signal was actually sent, whether the process died, what it exited
    with -- and until now the verb had no surface at all. `enforcer.py:390` called it in-process
    and nothing else could: an incident recorded by a guard that was itself killed, or by a path
    that never reached its own reporting line, stayed an intention forever with no way to close
    it out. That is the shape D00 warns about: a ledger that reads as complete because the
    unfinished rows look exactly like the finished ones.
    """
    row = store.apply("budget outcome", actor=a.by, incident_id=a.incident_id,
                      signal_sent=a.signal, detail=a.detail, engine_pid=a.pid)
    if a.json:
        _dump(row)
        return EXIT_OK
    print(f"#{row['id']} {row['kind']}: signal_sent={row['signal_sent'] or '(none)'} "
          f"action_taken={row['action_taken']}")
    return EXIT_OK


def cmd_guard(a) -> int:
    """Stand beside a live engine and stop it when the meter moves under it. Exit 3 = stopped it.

    The runner starts its engine itself and then hands the pid here, rather than letting the guard
    start it. That is not a preference: `engine/bin/swarm-run` puts a named fifo between the engine
    and its stream formatter precisely so `$!` is the ENGINE and not the formatter, which is what
    makes the shutdown trap reach a real engine and `$?` a real engine exit code. A guard that
    started the engine would own it, and both properties would go quietly dead.

    THE STOP MUST CARRY A KEY, or it is worse than no stop: `budget halt` matches first-party
    evidence on run_id or session_id, and an unmatched stop is reconciled as an ordinary task
    failure, charging an attempt to a lane that did nothing wrong. There are two keys and they
    are not interchangeable:

        --run     the `brain.run` row id, which `swarm run-start` now prints. Known at LAUNCH,
                  so it covers a run stopped in its first second, and it is the only key at all
                  on an engine that writes no stream-json (codex).
        --stream  the live stream-json, out of which the guard reads the session id the engine
                  invents and announces in its own first event. Cannot be passed in at launch.

    Pass both when both exist. The guard refuses to be flown blind: with neither, it says so.
    """
    def session_probe() -> str:
        """The first `session_id` the engine announced. Line one in practice; cheap to re-read."""
        if not a.stream:
            return ""
        try:
            with open(a.stream, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        sid = json.loads(line).get("session_id") or ""
                    except ValueError:
                        continue        # a torn line while the formatter is mid-write
                    if sid:
                        return sid
        except OSError:
            pass                        # not written yet: ask again on the next tick
        return ""

    guard = enforcer.RunGuard(agent=a.agent, lane=a.lane, work_item_id=a.task or None,
                              run_id=a.run, session_id=a.session, by=a.by, grace=a.grace,
                              on_event=lambda m: print(f"[budget-guard] {m}", flush=True))
    if a.run is None and not a.session and not a.stream:
        print("[budget-guard] WARNING: no --run, no --session and no --stream, so a stop filed "
              "here would carry no key. `budget halt` would find no first-party evidence and "
              "reconcile the kill as an ordinary task failure, charging the lane an attempt it "
              "did not earn. Pass --run (from `swarm run-start`) at minimum.", flush=True)
    stopped = guard.watch(a.pid, poll=a.interval, session_probe=session_probe,
                          stream=a.stream, derive=not a.no_derive_spend)
    if not stopped:
        return EXIT_OK
    print(f"[budget-guard] STOPPED pid {a.pid} with {guard.signal_sent}. "
          f"Incident {guard.incident_id} was filed BEFORE the signal, carrying run "
          f"{guard.run_id or '<none>'} and session {guard.session_id or '<none>'}, which is how "
          f"reconciliation knows this was the operator's money and not a subscription wall.")
    return EXIT_STOPPED


def cmd_selfcheck(a) -> int:
    """Did the price card tell the truth about this run? Exit 5 when it did not.

    THIS IS THE CONDITION THE OPERATOR PUT ON MID-RUN DERIVATION, and it is worth restating in
    the place that implements it: "if the delta is not zero, that is a finding with its own
    incident row, not a log line". The guard spends a run's ceiling against a number this lane
    computed. The engine then reports what it was actually billed. If those two disagree and
    nobody is told, the brake is braking against fiction.

    THE PART THAT TOOK THE MEASURING, and the reason this is not one assert. A naive
    `derived == total_cost_usd` fires on roughly 1.4% of runs on day one, for a reason that is not
    card rot: haiku is billed into the total and never appears in the stream at all (8 sessions in
    the current corpus, $0.512804 across all of them, every one reproduced to the cent as a named
    residual). A loud check that cries wolf gets muted, and a muted check is worse than no check.
    So `budget/price_card.py` compares against the billing of the models the stream basis can
    actually see, reports the absent-model remainder by name, and only calls something a finding
    when it survives that.

    Two verdicts are findings, and they are separated by a measurement rather than a threshold:

        basis_incomplete  the mid-run token counts disagree with the tokens `modelUsage` says
                          were billed. The engine did not stream what it charged for. Fix the
                          stream. Do NOT refit the card against this.
        card_drift        the token counts agree exactly and the dollars do not. Refit the card.

    Getting those two the wrong way round is expensive in both directions -- a refit against an
    incomplete basis bakes the under-report into the rates -- which is why the classifier reads
    `modelUsage`'s own token counts instead of inferring from the size of the gap.

    Exit 5 rather than 3: a mismatch is not a budget stop, nothing was refused, and the runner
    must not confuse the two.
    """
    checks = price_card.check_stream(a.stream)
    if not checks:
        print("[budget-selfcheck] no sessions in the stream; nothing to check")
        return EXIT_OK

    findings = 0
    for c in checks:
        print(f"[budget-selfcheck] {c.line()}")
        if not c.is_finding:
            continue
        findings += 1
        row = store.apply(
            "budget note", actor=a.by, kind="meter_mismatch",
            scope_type="agent" if a.agent else "fleet", scope_id=a.agent,
            spend_usd=c.billed_usd or 0, action_taken="warned", detected_by="charge",
            agent=a.agent, lane=a.lane, work_item_id=a.task or None, run_id=a.run,
            session_id=c.session_id,
            detail=(f"{c.verdict}: card derived ${c.derived_usd} from the message_delta basis, "
                    f"engine billed ${c.billed_usd} (${c.streamed_billed_usd} of it to the "
                    f"streamed model(s) {c.streamed_models}, ${c.residual_usd} to {c.absent_models}). "
                    f"Delta ${c.delta_usd}. {c.detail}"))
        print(f"[budget-selfcheck] FINDING filed as incident {row['id']} (kind meter_mismatch, "
              f"stops nothing). {c.verdict} is a {'card' if c.verdict == 'card_drift' else 'stream'} "
              f"problem: {c.detail}")
    if a.json:
        _dump([{"session_id": c.session_id, "verdict": c.verdict, "finding": c.is_finding,
                "derived_usd": str(c.derived_usd),
                "billed_usd": None if c.billed_usd is None else str(c.billed_usd),
                "residual_usd": str(c.residual_usd), "delta_usd": str(c.delta_usd)}
               for c in checks])
    if not findings:
        print(f"[budget-selfcheck] {len(checks)} session(s) checked, no findings: the card and "
              f"the engine's own billing agree")
    return EXIT_MISMATCH if findings else EXIT_OK


def cmd_halt(a) -> int:
    """Classify how a run ended: budget stop, subscription refusal, or task failure.

    This is the verb the runner asks. It is one call rather than two checks in two places,
    because two places is how the two conditions get confused.
    """
    h = halt.classify(log_path=a.log, task_state=a.state, run_id=a.run, session_id=a.session)
    if a.json:
        _dump({"kind": h.kind, "reason": h.reason, "disposition": h.disposition,
               "self_clearing": h.self_clearing, "charges_attempt": h.charges_attempt,
               "retry_after_epoch": h.retry_after_epoch, "incident_id": h.incident_id,
               "evidence": h.evidence})
    else:
        print(h.line())
        print(f"  evidence: {h.evidence}")
    return {halt.BUDGET_STOP: EXIT_STOPPED, halt.RATE_LIMIT: EXIT_REFUSAL}.get(h.kind, EXIT_OK)


# --------------------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="budget",
        description="The spend brake. A ceiling that stops a run, per agent and fleet wide.",
        epilog="A budget stop is the operator's money and means STOP (exit 3). A subscription "
               "refusal is a window that reopens on its own and means WAIT, task unspent "
               "(exit 4). `budget halt` is the one place that tells them apart.")
    sub = p.add_subparsers(dest="cmd", required=True)

    # `set` and `unset` take FOUR scopes where `stop`/`resume`/`status` take five, and the odd one
    # out is `project`. It is a hold scope, not a ceiling scope: brain.budget_charge has no project
    # dimension, so a project ceiling would meter 0.00 against the operator's limit forever and
    # never fire. Refused in three places on purpose -- argparse here so the shell says it first,
    # `budget/transitions.py:_check_ceiling_scope` so every OTHER caller of the verb gets the same
    # sentence, and `budget_policy_no_project_ceiling` (migration 45) so no code path can store it
    # however that code is written. The last one is the guarantee; these two are the message.
    s = sub.add_parser("set", help="set a ceiling for a scope (not `project`: hold-only)")
    s.add_argument("scope", choices=["fleet", "agent", "lane", "work_item"])
    s.add_argument("id", nargs="?", default="", help="agent name, lane name or task id")
    s.add_argument("--limit", required=True, type=float, help="ceiling in USD")
    s.add_argument("--period", default="day", choices=["day", "rolling_24h", "total"])
    s.add_argument("--warn", type=int, default=80, help="warn percent (default 80)")
    s.add_argument("--no-hard-stop", action="store_true",
                   help="measure without braking. Records a soft_breach instead of stopping")
    s.add_argument("--note", default="")
    s.add_argument("--by", default="operator")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_set)

    s = sub.add_parser("unset", help="remove a ceiling (retires it, never deletes)")
    s.add_argument("scope", choices=["fleet", "agent", "lane", "work_item"])
    s.add_argument("id", nargs="?", default="")
    s.add_argument("--period", default=None, choices=["day", "rolling_24h", "total"])
    s.add_argument("--reason", default="")
    s.add_argument("--by", default="operator")
    s.set_defaults(fn=cmd_unset)

    s = sub.add_parser("status", help="ceilings, spend, open stops and recent incidents")
    s.add_argument("scope", nargs="?",
                   choices=["fleet", "agent", "lane", "project", "work_item"])
    s.add_argument("id", nargs="?", default=None)
    s.add_argument("--incidents", type=int, default=5)
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("stop", help="stop a scope now. Does not clear itself")
    s.add_argument("scope", choices=["fleet", "agent", "lane", "project", "work_item"])
    s.add_argument("id", nargs="?", default="")
    s.add_argument("--reason", default="")
    s.add_argument("--by", default="operator")
    s.set_defaults(fn=cmd_stop)

    s = sub.add_parser("resume", help="lift a manual stop. The only way one ever ends")
    s.add_argument("scope", choices=["fleet", "agent", "lane", "project", "work_item"])
    s.add_argument("id", nargs="?", default="")
    s.add_argument("--reason", default="")
    s.add_argument("--by", default="operator")
    s.set_defaults(fn=cmd_resume)

    s = sub.add_parser("check", help="the dispatch gate: exit 3 when this work may not spend")
    s.add_argument("--agent", default="")
    s.add_argument("--lane", default="")
    s.add_argument("--task", default="")
    s.add_argument("--by", default="budget-preflight")
    s.add_argument("--dry-run", action="store_true", help="do not record a blocked_dispatch")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_check)

    s = sub.add_parser("charge", help="meter spend. Idempotent on (source, source_ref)")
    s.add_argument("usd", type=float)
    s.add_argument("--ref", required=True, help="the idempotency key")
    s.add_argument("--source", default="manual",
                   choices=["run_json", "stream", "manual", "estimate"])
    s.add_argument("--agent", default="")
    s.add_argument("--lane", default="")
    s.add_argument("--task", default=None)
    s.add_argument("--session", default="")
    s.add_argument("--note", default="")
    s.add_argument("--by", default="operator")
    s.add_argument("--truth-up", action="store_true", dest="truth_up",
                   help="USD is the authoritative total for --session; charge only what is not "
                        "already metered against it. This is what makes the runner's end-of-run "
                        "charge compose with the guard's mid-run increments instead of doubling "
                        "them")
    s.set_defaults(fn=cmd_charge)

    s = sub.add_parser("incidents", help="the typed breach records")
    s.add_argument("--limit", type=int, default=20)
    s.add_argument("--kind", default=None)
    s.add_argument("--open", action="store_true")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_incidents)

    s = sub.add_parser("outcome", help="record what an incident's prediction actually did")
    s.add_argument("incident_id", type=int, help="the id `budget incidents` prints")
    s.add_argument("--signal", default="", help="the signal actually sent, e.g. TERM or KILL")
    s.add_argument("--detail", default="", help="appended to the incident's detail, never over it")
    s.add_argument("--pid", type=int, default=None, help="the engine pid this was observed on")
    s.add_argument("--by", default="operator")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_outcome)

    s = sub.add_parser("guard", help="watch a live run and stop it: exit 3 when it was stopped")
    s.add_argument("--pid", type=int, required=True, help="the engine pid, already running")
    s.add_argument("--run", type=int, default=None,
                   help="the brain.run row id `swarm run-start` prints. The key a stop is matched "
                        "back on, known at launch and the only one a codex run ever has")
    s.add_argument("--stream", default="", help="the run's stream.jsonl, read for the session id")
    s.add_argument("--agent", default="")
    s.add_argument("--lane", default="")
    s.add_argument("--task", default="")
    s.add_argument("--session", default="", help="only when the caller already knows it")
    s.add_argument("--interval", type=float, default=15.0, help="seconds between meter reads")
    s.add_argument("--grace", type=float, default=enforcer.GRACE_SECONDS,
                   help="seconds between SIGTERM and SIGKILL")
    s.add_argument("--by", default="budget-guard")
    s.add_argument("--no-derive-spend", action="store_true",
                   help="do not derive this run's own spend from --stream. The sibling-terminal "
                        "brake still works and the full cost still lands at end of run; this is "
                        "the switch to reach for if the price card goes stale")
    s.set_defaults(fn=cmd_guard)

    s = sub.add_parser("selfcheck",
                       help="did the price card match the engine's own billing for this run? "
                            "exit 5 when it did not")
    s.add_argument("--stream", required=True, help="the run's stream.jsonl")
    s.add_argument("--agent", default="")
    s.add_argument("--lane", default="")
    s.add_argument("--task", default=None)
    s.add_argument("--run", type=int, default=None)
    s.add_argument("--json", action="store_true")
    s.add_argument("--by", default="budget-selfcheck")
    s.set_defaults(fn=cmd_selfcheck)

    s = sub.add_parser("halt", help="budget stop, subscription refusal, or task failure?")
    s.add_argument("--log", default=None, help="the engine log")
    s.add_argument("--state", default="active", help="the bus state of the task")
    s.add_argument("--run", type=int, default=None)
    s.add_argument("--session", default="")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_halt)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except Exception as exc:                       # noqa: BLE001 - the CLI reports, never traces
        print(f"budget: {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
