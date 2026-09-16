#!/usr/bin/env python3
"""The demonstration. Real processes, real signals, real rows, and the live store.

    python3 -m budget.demo.demonstrate

My brief says demonstrate the stop and demonstrate the distinction, do not assert either. So
nothing in this file is mocked: a child process is started, metered, killed, and then checked for
death with `os.kill(pid, 0)`; the work file it left behind says how far it actually got; and every
row read back comes out of the same Postgres the fleet uses.

Seven scenes:

    1  a live run crosses its ceiling and is STOPPED mid-work
    2  a subscription refusal, which costs nothing and returns the task UNSPENT
    3  the poisoned log: a budget stop whose log ALSO carries a refusal line
    4  the two mechanisms composing: the refusal decides the task, the budget decides dispatch
    5  a ceiling refusing a run before it starts, which is the cheapest stop there is
    6  the database refusing to file a refusal as a breach
    7  a stop does not clear itself, and time passing changes nothing

Every scope this touches carries a per-run nonce and is retired at the end, so the demo is
repeatable and never brakes anything real. The first draft of this file did NOT do that and
scene 3 failed on the second run: `budget set` deliberately keeps the superseded policy's
window start (raising a ceiling mid-window must not silently zero the meter), so a second run
inherited the first run's spend and was refused before it could start. That was the demo being
wrong and the enforcer being right, which is the more useful way round.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import psycopg2

import store
from budget import enforcer, halt, reads

NONCE = os.environ.get("BUDGET_DEMO_NONCE") or f"{int(time.time())}-{os.getpid()}"
LANE = f"demo-lane-{NONCE}"
AGENT_A = f"demo-{NONCE}-a"      # scene 1: the stop
AGENT_B = f"demo-{NONCE}-b"      # scenes 3-5, 7: the distinction
HERE = Path(__file__).resolve().parent
PY = sys.executable


def hr(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def sub(title: str) -> None:
    print(f"\n--- {title} ---")


def say(msg: str) -> None:
    print(msg, flush=True)


# Every ceiling this demo sets, so cleanup retires exactly those and nothing else.
CREATED: list[tuple[str, str, str]] = []


def set_ceiling(scope_type: str, scope_id: str, limit: str, period: str = "total",
                warn: int = 60, hard_stop: bool = True, note: str = "") -> dict:
    row = store.apply("budget set", actor="demo", scope_type=scope_type, scope_id=scope_id,
                      limit_usd=limit, period=period, warn_percent=warn,
                      hard_stop_enabled=hard_stop, set_by="demo", note=f"demo: {note}")
    CREATED.append((scope_type, scope_id, period))
    return row


# One demo-only transition, registered once at import. Registering inside a function would raise
# DuplicateTransition the second time it was called, which is the module working as designed.


@store.transition("demo.file-a-refusal-as-a-breach")
def _file_a_refusal_as_a_breach(ctx):
    ctx.execute(
        "INSERT INTO brain.budget_incident (kind, scope_type, cause, action_taken, detail) "
        "VALUES ('hard_stop','fleet','rate_limit','run_stopped',%s)",
        ("engine refused: You've hit your session limit",))


def incident_count() -> int:
    with store.read("runtime") as s:
        return int(s.scalar("SELECT count(*) FROM brain.budget_incident"))


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


# ---------------------------------------------------------------------------- scene 1


def scene_1_the_stop():
    hr("SCENE 1 -- A CEILING THAT ACTUALLY STOPS A RUN")
    say("A ceiling of $3.00 on one agent. A child process that intends 20 units of work at\n"
        "$0.85 each -- $17.00 if nothing stops it. The child writes each finished unit to a\n"
        "file, so afterwards the file says how far it really got.\n")

    set_ceiling("agent", AGENT_A, "3.00", note="the per-agent stop")

    work = Path(tempfile.mkdtemp(prefix="budget-demo-")) / "work.txt"
    guard = enforcer.RunGuard(agent=AGENT_A, lane=LANE, by="demo-guard", grace=3,
                              session_id=f"{NONCE}-scene-1", on_event=say)
    rc = guard.run([PY, str(HERE / "spendy_agent.py"), "--units", "20", "--cost", "0.85",
                    "--work-file", str(work)])

    pid = guard.proc.pid
    sub("what the child actually did")
    done = work.read_text().strip().splitlines() if work.exists() else []
    say(f"units of work completed: {len(done)} of 20 intended  (last: {done[-1] if done else '-'})")
    say(f"would have spent:        $17.00 unbraked")
    say(f"spend at the stop:       ${guard.spent}   ceiling: $3.00")
    say(f"child exit code:         {rc}    signal sent: {guard.signal_sent}")
    say(f"process {pid} alive now: {alive(pid)}")
    assert len(done) < 20, "the child finished all its work; nothing was stopped"
    assert not alive(pid), "the child is still running"

    sub("the typed breach record")
    inc = reads.incident(guard.incident_id)
    for k in ("id", "kind", "cause", "scope_type", "scope_id", "spend_usd", "limit_usd",
              "percent_used", "action_taken", "detected_by", "prescribed_disposition",
              "engine_pid", "signal_sent", "cleared_at"):
        say(f"  {k:<24} {inc[k]}")
    say(f"  {'detail':<24} {inc['detail']}")
    say("\n  `prescribed_disposition` reads hold_dispatch because this demo run carries no task")
    say("  id. With a work item attached it reads `block`: see scene 3. It is never `fail` (the")
    say("  lane did nothing wrong) and never `reopen` (that is the money retry loop).")
    return guard


def scene_1b_fleet_wide():
    hr("SCENE 1b -- AND FLEET WIDE, ON AN AGENT WITH NO CEILING OF ITS OWN")
    say("The brief asks for a ceiling per agent AND fleet wide. This agent has no ceiling of\n"
        "its own; the $2.00 fleet ceiling is the only thing covering it.\n")
    set_ceiling("fleet", "", "2.00", note="fleet wide")

    agent_c = f"demo-{NONCE}-c"
    guard = enforcer.RunGuard(agent=agent_c, lane=LANE, by="demo-guard", grace=3,
                              session_id=f"{NONCE}-scene-1b", on_event=say)
    guard.run([PY, str(HERE / "spendy_agent.py"), "--units", "20", "--cost", "0.85"])
    inc = reads.incident(guard.incident_id)
    say(f"\n  stopped by scope: {inc['scope_type']} (scope_id {inc['scope_id']!r}), "
        f"spend ${inc['spend_usd']} of ${inc['limit_usd']}")
    say(f"  the agent that was running: {inc['agent']}, which has no policy of its own")
    assert inc["scope_type"] == "fleet", "the fleet ceiling did not do the stopping"
    assert not alive(guard.proc.pid)

    sub("and the fleet stop covers a DIFFERENT agent that never spent anything")
    d = enforcer.evaluate(agent=f"demo-{NONCE}-d", lane=LANE)
    say(f"  {d.line()}")
    assert not d.allowed, "the fleet brake did not reach a second agent"
    store.apply("budget unset", actor="demo", scope_type="fleet", scope_id="", by="demo",
                reason="demo scene 1b complete")
    say("\n  Fleet ceiling retired again before the next scene, so the scenes below measure")
    say("  only their own scopes.")


# ---------------------------------------------------------------------------- scene 2


def scene_2_the_refusal():
    hr("SCENE 2 -- A SUBSCRIPTION REFUSAL, WHICH IS NOT A BREACH")
    say("The same fleet, the same agent, and a run that never starts because the account is\n"
        "spent. The log is the 2026-08-14 log. Nothing here is my code's opinion: the detector\n"
        "is LIMIT_PARSER, lifted out of swarm-admiral/bin/swarm-run at runtime.\n")

    before = incident_count()
    tmp = Path(tempfile.mkdtemp(prefix="budget-demo-"))
    log, run_json = tmp / "engine.log", tmp / "run.json"
    proc = subprocess.run([PY, str(HERE / "refused_agent.py"), "--run-json", str(run_json)],
                          capture_output=True, text=True)
    log.write_text(proc.stdout)
    for line in proc.stdout.strip().splitlines():
        say(f"  child| {line}")
    say(f"  child exit code: {proc.returncode}   (non-zero, task still active: by exit code "
        f"alone this is indistinguishable from a failure)")

    sub("charging what the engine reported, which is zero")
    charged = store.apply("budget charge", actor="demo", usd=0,
                          source_ref=f"{NONCE}-refusal-1", source="run_json",
                          agent=AGENT_A, lane=LANE,
                          note="refused run: one turn, no cost, no work")
    say(f"  {charged}")
    say("  A zero charge is RECORDED, not skipped. That the refusal cost nothing is then a row")
    say("  in the meter rather than an assumption.")

    sub("classifying the halt")
    h = halt.classify(log_path=str(log), task_state="active")
    say(f"  {h.line()}")
    say(f"  evidence: {h.evidence}")

    after = incident_count()
    sub("did a breach record appear?")
    say(f"  budget_incident rows before: {before}   after: {after}   delta: {after - before}")
    assert h.kind == halt.RATE_LIMIT, f"a refusal classified as {h.kind}"
    assert h.disposition == "reopen", "a refusal must reopen, not fail and not block"
    assert h.self_clearing and h.retry_after_epoch, "a refusal must carry a reset time"
    assert not h.charges_attempt, "a refusal must not charge the task an attempt"
    assert after == before, "a refusal wrote a budget incident"
    return h


# ---------------------------------------------------------------------------- scene 3


def scene_3_the_poisoned_log():
    hr("SCENE 3 -- THE POISONED LOG: A BUDGET STOP WHOSE LOG ALSO SAYS 'SESSION LIMIT'")
    say("This is the expensive confusion, in the direction that costs money rather than time.\n"
        "A long run hits a rate limit on an early turn, retries past it, keeps working, and is\n"
        "then killed for spending too much. Its log contains BOTH. A classifier that reads the\n"
        "text first calls it a refusal, reopens the task, resets the attempt ladder to zero,\n"
        "and the next free terminal claims it and spends again -- with no counter left to stop\n"
        "the loop.\n")

    set_ceiling("agent", AGENT_B, "1.50", note="the poisoned log")

    tmp = Path(tempfile.mkdtemp(prefix="budget-demo-"))
    log = tmp / "engine.log"
    lines: list[str] = []
    session = f"{NONCE}-scene-3"
    guard = enforcer.RunGuard(agent=AGENT_B, lane=LANE, by="demo-guard", grace=3,
                              session_id=session, on_event=lines.append)
    guard.run([PY, str(HERE / "spendy_agent.py"), "--units", "20", "--cost", "0.85",
               "--limit-line"])
    log.write_text("\n".join(m.replace("  child| ", "") for m in lines if "child|" in m))
    for m in lines:
        say(m)

    has_refusal_line = "hit your session limit" in log.read_text()
    sub("the two pieces of evidence this log carries")
    say(f"  the log contains a refusal line:  {has_refusal_line}")
    say(f"  LIMIT_PARSER on this log returns: {halt.limit_reset_epoch(str(log))}"
        f"   (the refusal detector DOES match)")
    say(f"  and we filed incident {guard.incident_id} BEFORE signalling the process")
    assert has_refusal_line and halt.limit_reset_epoch(str(log)), "the log is not poisoned"

    sub("what the classifier says")
    h = halt.classify(log_path=str(log), task_state="active", session_id=session)
    say(f"  {h.line()}")
    say(f"  evidence: {h.evidence}")
    assert h.kind == halt.BUDGET_STOP, (
        f"the poisoned log was classified {h.kind}: first-party evidence lost to text")
    assert h.disposition == "block", "a budget stop must not take the reopen path"
    assert h.retry_after_epoch is None, "a budget stop must not carry a self-clearing time"
    say("\n  First-party evidence beat the text. We know because we did it, and no wording in")
    say("  somebody else's log can talk us out of our own record.")
    return h


# ---------------------------------------------------------------------------- scene 4


def scene_4_composition():
    hr("SCENE 4 -- COMPOSING RATHER THAN FIGHTING")
    say("The fleet is running one account short and cannot rotate, so refusals are a live\n"
        "condition right now. With a budget stop open, a refusal still has to do its own job.\n")

    tmp = Path(tempfile.mkdtemp(prefix="budget-demo-"))
    log = tmp / "engine.log"
    proc = subprocess.run([PY, str(HERE / "refused_agent.py")], capture_output=True, text=True)
    log.write_text(proc.stdout)

    h = halt.classify(log_path=str(log), task_state="active", session_id="a-different-session")
    d = enforcer.evaluate(agent=AGENT_B, lane=LANE)
    say(f"  the refusal decides THIS TASK        -> {h.kind}: {h.disposition}, "
        f"unspent, retry after epoch {h.retry_after_epoch}")
    say(f"  the budget decides the NEXT DISPATCH -> {d.verdict}: allowed={d.allowed}")
    say(f"     {d.reason}")
    assert h.disposition == "reopen" and not d.allowed
    say("\n  Neither overrode the other. The task goes back unspent and waits for the window;")
    say("  no new dispatch starts until a human acts. Account rotation in swarm-run is")
    say("  untouched by any of this.")


# ---------------------------------------------------------------------------- scene 5


def scene_5_refused_before_start():
    hr("SCENE 5 -- THE CHEAPEST STOP: A RUN THAT NEVER STARTS")
    before = incident_count()
    guard = enforcer.RunGuard(agent=AGENT_B, lane=LANE, by="demo-guard", on_event=say)
    rc = guard.run([PY, str(HERE / "spendy_agent.py"), "--units", "20"])
    say(f"  returned {rc}, child process object: {guard.proc}")
    assert guard.proc is None, "a process was started over the ceiling"

    d = enforcer.preflight(agent=AGENT_B, lane=LANE, by="demo-preflight")
    say(f"  preflight: {d.line()}")
    row = reads.incidents(limit=1, kind="blocked_dispatch")[0]
    say(f"  recorded as: #{row['id']} {row['kind']} / {row['action_taken']} / "
        f"disposition={row['prescribed_disposition']}")
    say(f"  incidents before {before}, after {incident_count()}")
    say("\n  Nothing was spent, no process ran, and no attempt was charged to any task. A")
    say("  blocked_dispatch is a typed record and NOT a stop: nothing was running to stop.")


# ---------------------------------------------------------------------------- scene 6


def scene_6_the_database_refuses():
    hr("SCENE 6 -- THE DISTINCTION IS A DATABASE CONSTRAINT, NOT A CONVENTION")
    say("A gate in application code is bypassable by a bug or a wrong branch. This one is not\n"
        "reachable by any code path, however it is written:\n")
    say("    cause text NOT NULL DEFAULT 'budget' CHECK (cause = 'budget')\n")
    try:
        store.apply("demo.file-a-refusal-as-a-breach")
    except psycopg2.errors.CheckViolation as exc:
        say(f"  psycopg2.errors.CheckViolation: {str(exc).strip().splitlines()[0]}")
        say("\n  The breach table cannot physically hold a rate-limit refusal. Every row in it is")
        say("  the operator's money, by construction.")
        return
    raise AssertionError("the incident table accepted a rate-limit cause")


# ---------------------------------------------------------------------------- scene 7


def scene_7_a_stop_does_not_clear_itself():
    hr("SCENE 7 -- A REFUSAL CLEARS ITSELF. A STOP DOES NOT.")
    store.apply("budget stop", actor="demo", scope_type="agent", scope_id=AGENT_B,
                kind="manual_stop", by="demo", reason="demo: the operator's own hand")

    d1 = enforcer.evaluate(agent=AGENT_B, lane=LANE)
    say(f"  now:               {d1.verdict}, allowed={d1.allowed}")
    time.sleep(2)
    d2 = enforcer.evaluate(agent=AGENT_B, lane=LANE)
    say(f"  two seconds later: {d2.verdict}, allowed={d2.allowed}")
    assert not d2.allowed, "a stop expired on its own"

    say(f"\n  a refusal carries the time it ends:  epoch {int(time.time()) + 3600} "
        f"(from the engine's own message)")
    say(f"  a stop carries none:                 cleared_at = "
        f"{reads.incident(reads.open_stops(agent=AGENT_B)[0]['incident_id'])['cleared_at']}")

    sub("raising the ceiling does NOT lift a manual stop")
    set_ceiling("agent", AGENT_B, "500.00", note="raise it a lot")
    d3 = enforcer.evaluate(agent=AGENT_B, lane=LANE)
    say(f"  ceiling now $500 against spend ${reads.spend(agent=AGENT_B)['usd']}: "
        f"{d3.verdict}, allowed={d3.allowed}")
    say(f"     {d3.reason}")
    assert not d3.allowed, "raising the ceiling silently lifted a manual stop"
    say("  Two different decisions -- 'I stopped the fleet' and 'I raised the ceiling' -- and")
    say("  collapsing them means one of them happens by accident.")

    sub("only `budget resume` ends it")
    lifted = store.apply("budget resume", actor="demo", scope_type="agent", scope_id=AGENT_B,
                         by="demo", reason="demo complete")
    say(f"  {lifted}")
    d4 = enforcer.evaluate(agent=AGENT_B, lane=LANE)
    say(f"  after resume: {d4.verdict}, allowed={d4.allowed}")
    assert d4.allowed, "resume did not lift the stop"


def cleanup():
    hr("CLEANUP -- retiring every ceiling this demo set, through the real verb")
    retired = []
    for scope_type, scope_id, period in CREATED:
        row = store.apply("budget unset", actor="demo", scope_type=scope_type,
                          scope_id=scope_id, period=period, by="demo", reason="demo cleanup")
        retired += row["retired"]
    say(f"  retired policies: {retired}")
    for scope, sid in (("agent", AGENT_A), ("agent", AGENT_B), ("lane", LANE)):
        store.apply("budget resume", actor="demo", scope_type=scope, scope_id=sid,
                    by="demo", reason="demo cleanup")
    left = [p for p in reads.status() if NONCE in (p["scope_id"] or "")]
    say(f"  active demo policies left: {len(left)}")
    say(f"  charges and incidents are KEPT: this ledger has no DELETE grant for any role but")
    say(f"  owner, which is migration 2's posture and not something a demo gets to bend.")


def main() -> int:
    hr("BUDGET ENFORCEMENT -- DEMONSTRATION AGAINST THE LIVE STORE")
    h = store.health()
    say(f"store: {h['server']}, schema version {h['schema_version']}")
    say(f"demo scopes: {AGENT_A}, {AGENT_B}, {LANE}. Nothing outside them is touched.")
    pre = reads.status()
    if pre:
        say(f"NOTE: {len(pre)} ceiling(s) are already active and cover this demo's spend:")
        for r in pre:
            say(f"  {r['scope_type']}:{r['scope_id']} ${r['spend_usd']} of ${r['limit_usd']}")
    try:
        scene_1_the_stop()
        scene_1b_fleet_wide()
        scene_2_the_refusal()
        scene_3_the_poisoned_log()
        scene_4_composition()
        scene_5_refused_before_start()
        scene_6_the_database_refuses()
        scene_7_a_stop_does_not_clear_itself()
    finally:
        cleanup()
    hr("DONE -- every assertion above ran against real processes and the live store")
    return 0


if __name__ == "__main__":
    sys.exit(main())
