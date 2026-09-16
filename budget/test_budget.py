"""The tests that make the spend brake a property rather than a promise.

    python3 -m budget.test_budget

Run against the live local store, like `store/test_narrow_waist.py`. Most of what is asserted
here is a REFUSAL: what the database will not store, what the classifier will not conclude, and
what the guard will not let a process go on doing. A suite that only checked the happy path would
pass just as well against a budget that records costs and never stops anything, which is exactly
the state this lane exists to end.

The four properties, and the tests that hold them:

    a ceiling stops a run           test_the_guard_kills_a_live_process
                                    test_the_guard_stops_a_run_it_did_not_start
                                    test_the_guard_files_nothing_for_a_run_that_ends_under_its_ceiling
                                    test_the_guard_does_not_read_a_zombie_as_a_live_run
                                    test_the_guard_never_signals_its_own_process_group
                                    test_a_run_over_the_ceiling_never_starts
                                    test_the_fleet_ceiling_stops_an_agent_with_no_policy
    a stop != a refusal             test_the_incident_table_refuses_a_rate_limit_cause
                                    test_a_refusal_classifies_as_reopen_and_writes_no_incident
                                    test_a_budget_stop_classifies_as_block
                                    test_first_party_evidence_beats_a_poisoned_log
                                    test_the_two_halts_disagree_on_every_field_that_matters
    the meter cannot lie            test_a_charge_is_idempotent_on_its_source_ref
                                    test_a_warning_is_not_a_stop
                                    test_a_soft_breach_is_recorded_as_a_soft_breach
                                    test_hard_stop_is_disarmed_by_hard_stop_enabled_false
    the operator stays in charge    test_a_stop_does_not_clear_itself
                                    test_raising_the_ceiling_does_not_lift_a_manual_stop
                                    test_a_manual_stop_needs_no_policy
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
from budget.transitions import BudgetError

PASS, FAIL = [], []
NONCE = f"test-{int(time.time())}-{os.getpid()}"
HERE = Path(__file__).resolve().parent
PY = sys.executable
CREATED: list[tuple[str, str, str]] = []


def check(name, fn):
    try:
        fn()
    except AssertionError as exc:
        FAIL.append(f"{name}: {exc}")
        print(f"FAIL  {name}\n      {exc}")
    except Exception as exc:  # noqa: BLE001 - an unexpected error is a failure, loudly
        FAIL.append(f"{name}: unexpected {exc.__class__.__name__}: {exc}")
        print(f"FAIL  {name}\n      unexpected {exc.__class__.__name__}: {exc}")
    else:
        PASS.append(name)
        print(f"ok    {name}")


def scope(tag: str) -> str:
    return f"{NONCE}-{tag}"


def ceiling(scope_type: str, scope_id: str, limit: str, hard_stop: bool = True,
            warn: int = 60, period: str = "total") -> None:
    store.apply("budget set", actor="test", scope_type=scope_type, scope_id=scope_id,
                limit_usd=limit, period=period, warn_percent=warn,
                hard_stop_enabled=hard_stop, set_by="test", note="test")
    CREATED.append((scope_type, scope_id, period))


def refusal_log() -> str:
    tmp = Path(tempfile.mkdtemp(prefix="budget-test-")) / "engine.log"
    proc = subprocess.run([PY, str(HERE / "demo" / "refused_agent.py")],
                          capture_output=True, text=True)
    tmp.write_text(proc.stdout)
    return str(tmp)


def incident_count() -> int:
    with store.read("runtime") as s:
        return int(s.scalar("SELECT count(*) FROM brain.budget_incident"))


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


# ------------------------------------------------------------------ a ceiling stops a run


def test_the_guard_kills_a_live_process():
    agent = scope("kill")
    ceiling("agent", agent, "1.00")
    guard = enforcer.RunGuard(agent=agent, by="test", grace=2, session_id=scope("kill-s"))
    rc = guard.run([PY, str(HERE / "demo" / "spendy_agent.py"), "--units", "50",
                    "--cost", "0.60", "--delay", "0.05"])
    assert guard.proc is not None, "no process was started"
    assert not alive(guard.proc.pid), "the child survived the stop"
    assert rc < 0 or rc != 0, f"the child exited cleanly rc={rc}: it was not stopped"
    assert guard.incident_id, "a run was killed with no incident recorded"
    assert guard.spent < 50 * 0.60, "the child was allowed to spend its whole intent"


def _sleeper(seconds: int = 60) -> subprocess.Popen:
    """A live process the guard did NOT start, in its own group, like `timeout` gives the runner."""
    return subprocess.Popen([PY, "-c", f"import time; time.sleep({seconds})"],
                            start_new_session=True)


def test_the_guard_stops_a_run_it_did_not_start():
    """The runner's shape. It owns its engine because of the fifo, so the guard only adopts a pid.

    The sequence is the fleet case, in order: the run is ALLOWED when it starts, a sibling's
    charge takes the shared meter over while it is live, and the guard reaches it mid-flight.
    Before `watch()` existed the ceiling braked only between runs and this run finished.
    """
    agent = scope("watch")
    sid = scope("watch-sid")
    ceiling("agent", agent, "1.00")
    child = _sleeper()
    try:
        assert enforcer.evaluate(agent=agent).allowed, "the run was already stopped before it began"
        # A SIBLING terminal finishes and charges. Nothing about this run changed.
        store.apply("budget charge", actor="test", usd="5.00", source_ref=scope("watch-sibling"),
                    source="manual", agent=agent)
        guard = enforcer.RunGuard(agent=agent, by="test", grace=2)
        stopped = guard.watch(child.pid, poll=0.2, session_probe=lambda: sid)
        assert stopped, "the guard let a live run continue past the ceiling"
        # Reaped, not signal-probed. A killed child of THIS process is a zombie until it is
        # waited on, and `alive()` reads a zombie as running -- the same trap `_alive()` carries
        # a /proc state check for.
        rc = child.wait(timeout=10)
        assert rc != 0, f"the child exited cleanly rc={rc}: it was not stopped"
        assert guard.incident_id, "a run was killed with no incident recorded"
        # The session id is the ONLY key reconciliation has: a killed run writes no result event
        # and the runner has no run id to pass. Without it the stop is misfiled as a failure.
        assert guard.session_id == sid, f"the guard stopped with session [{guard.session_id}]"
        h = halt.classify(log_path=None, task_state="active", session_id=sid)
        assert h.kind == halt.BUDGET_STOP, f"reconciliation would call this {h.kind}"
        assert h.disposition == "block", f"disposition {h.disposition}"
    finally:
        child.kill()


def test_the_guard_files_nothing_for_a_run_that_ends_under_its_ceiling():
    """The control. A watcher that stopped everything would pass the test above just as well."""
    agent = scope("watch-clean")
    ceiling("agent", agent, "10.00")
    before = incident_count()
    child = _sleeper(1)
    guard = enforcer.RunGuard(agent=agent, by="test", grace=2)
    stopped = guard.watch(child.pid, poll=0.2)
    assert not stopped, "the guard stopped a run that never crossed anything"
    assert guard.incident_id is None, "an incident was filed for a run that ended on its own"
    assert incident_count() == before, "the watcher wrote to the incident table anyway"


def test_the_guard_does_not_read_a_zombie_as_a_live_run():
    """`os.kill(pid, 0)` succeeds on a process that has exited and not yet been reaped.

    Found by this suite hanging. An adopted pid is not the guard's to reap, so between the engine
    exiting and its parent collecting it the pid is a zombie -- present in /proc, signalable, and
    finished. A guard that reads that as alive polls the meter forever and outlives the run it was
    guarding, which is the one state in which a stale pid can be recycled under it.
    """
    child = _sleeper(0)
    time.sleep(0.5)          # it has exited. Deliberately NOT waited on, so it stays a zombie.
    assert alive(child.pid), "it was reaped already, so there is no zombie and this proves nothing"
    guard = enforcer.RunGuard(agent=scope("zombie"), by="test")
    guard.pid = child.pid
    assert not guard._alive(), "the guard read a zombie as a live run and would poll forever"
    child.wait()


def test_the_guard_never_signals_its_own_process_group():
    """The one way this could go badly wrong: killpg on the group the RUNNER is in.

    `run()` starts its child with start_new_session so the group is always the child's alone, but
    `watch()` adopts a pid somebody else started, and bash without job control leaves a plain
    background child in the SHELL's process group. A guard that killpg'd that would take the
    runner down with the engine it was guarding.
    """
    guard = enforcer.RunGuard(agent=scope("target"), by="test")
    guard.pid = os.getpid()                      # deliberately: our own group
    group, target, label = guard._target()
    assert not group, f"the guard would have signalled its own process group ({label})"
    assert target == os.getpid(), f"target {target} is not the pid"

    other = _sleeper(30)                         # its own session, so a group IS the right target
    try:
        guard.pid = other.pid
        group, target, label = guard._target()
        assert group, f"the guard would have signalled only the pid, missing the tree ({label})"
        assert target == os.getpgid(other.pid), f"target {target} is not the child's group"
    finally:
        other.kill()


def test_a_run_over_the_ceiling_never_starts():
    agent = scope("preflight")
    ceiling("agent", agent, "0.10")
    store.apply("budget charge", actor="test", usd="0.50", source_ref=scope("preflight-1"),
                source="manual", agent=agent)
    guard = enforcer.RunGuard(agent=agent, by="test")
    guard.run([PY, str(HERE / "demo" / "spendy_agent.py"), "--units", "5"])
    assert guard.proc is None, "a process was started over the ceiling"


def test_the_fleet_ceiling_stops_an_agent_with_no_policy():
    """Per agent AND fleet wide. An agent with no policy of its own is still covered."""
    agent = scope("fleet-covered")
    d_before = enforcer.evaluate(agent=agent)
    assert d_before.allowed, "the test scope was already stopped before the fleet ceiling"
    ceiling("fleet", "", "0.01")
    store.apply("budget charge", actor="test", usd="0.50", source_ref=scope("fleet-1"),
                source="manual", agent=agent)
    try:
        d = enforcer.evaluate(agent=agent)
        assert not d.allowed, "the fleet ceiling did not reach an agent with no policy"
        assert d.stopping[0]["scope_type"] == "fleet", f"stopped by {d.stopping[0]['scope_type']}"
    finally:
        # A live fleet ceiling would brake every other lane's work. Remove it immediately.
        store.apply("budget unset", actor="test", scope_type="fleet", scope_id="", by="test",
                    reason="test cleanup, inline")


# ------------------------------------------------------------------ a stop is not a refusal


def test_the_incident_table_refuses_a_rate_limit_cause():
    """The structural half. Not reachable by any code path, however that code is written."""
    try:
        store.apply("test.file-a-refusal-as-a-breach")
    except psycopg2.errors.CheckViolation:
        return
    raise AssertionError("brain.budget_incident accepted cause='rate_limit'")


def test_a_refusal_classifies_as_reopen_and_writes_no_incident():
    before = incident_count()
    h = halt.classify(log_path=refusal_log(), task_state="active")
    assert h.kind == halt.RATE_LIMIT, f"classified {h.kind}"
    assert h.disposition == "reopen", f"disposition {h.disposition}: a refusal is not a failure"
    assert h.self_clearing is True, "a refusal must clear itself"
    assert h.charges_attempt is False, "a refusal must not charge the task an attempt"
    assert h.retry_after_epoch and h.retry_after_epoch > time.time(), "no reset time"
    assert h.incident_id is None, "a refusal produced a breach record"
    assert incident_count() == before, "a refusal wrote a row to budget_incident"


def test_a_budget_stop_classifies_as_block():
    agent = scope("classify")
    ceiling("agent", agent, "0.50")
    session = scope("classify-s")
    guard = enforcer.RunGuard(agent=agent, by="test", grace=2, session_id=session)
    guard.run([PY, str(HERE / "demo" / "spendy_agent.py"), "--units", "10", "--cost", "0.60",
               "--delay", "0.05"])
    h = halt.classify(log_path=None, task_state="active", session_id=session)
    assert h.kind == halt.BUDGET_STOP, f"classified {h.kind}"
    assert h.disposition == "block", (
        f"disposition {h.disposition}: `fail` charges a lane that did nothing wrong, `reopen` "
        f"returns the task to be claimed and spent again")
    assert h.self_clearing is False, "a budget stop must not expire on its own"
    assert h.retry_after_epoch is None, "a budget stop must not carry a retry time"


def test_first_party_evidence_beats_a_poisoned_log():
    """The expensive direction: a breach misread as a refusal retries into real money."""
    agent = scope("poison")
    ceiling("agent", agent, "0.50")
    session = scope("poison-s")
    lines: list[str] = []
    guard = enforcer.RunGuard(agent=agent, by="test", grace=2, session_id=session,
                              on_event=lines.append)
    guard.run([PY, str(HERE / "demo" / "spendy_agent.py"), "--units", "10", "--cost", "0.60",
               "--delay", "0.05", "--limit-line"])
    log = Path(tempfile.mkdtemp(prefix="budget-test-")) / "engine.log"
    log.write_text("\n".join(m.replace("  child| ", "") for m in lines if "child|" in m))

    assert halt.limit_reset_epoch(str(log)), "the log is not poisoned; the test proves nothing"
    h = halt.classify(log_path=str(log), task_state="active", session_id=session)
    assert h.kind == halt.BUDGET_STOP, (
        f"a killed run whose log mentions a session limit classified as {h.kind}. Reopen resets "
        f"attempts to 0, so the task would be re-claimed and spend again with no counter left.")


def test_the_two_halts_disagree_on_every_field_that_matters():
    """Not "different enough": different on disposition, self-clearing and the retry clock."""
    agent = scope("compare")
    ceiling("agent", agent, "0.50")
    session = scope("compare-s")
    guard = enforcer.RunGuard(agent=agent, by="test", grace=2, session_id=session)
    guard.run([PY, str(HERE / "demo" / "spendy_agent.py"), "--units", "10", "--cost", "0.60",
               "--delay", "0.05"])
    stop = halt.classify(task_state="active", session_id=session)
    refusal = halt.classify(log_path=refusal_log(), task_state="active")

    assert stop.kind != refusal.kind
    assert stop.disposition == "block" and refusal.disposition == "reopen"
    assert stop.self_clearing is False and refusal.self_clearing is True
    assert stop.retry_after_epoch is None and refusal.retry_after_epoch is not None
    assert stop.incident_id is not None and refusal.incident_id is None
    assert not (stop.is_refusal or refusal.is_budget_stop), "the two predicates overlap"


def test_a_refusal_charges_nothing():
    agent = scope("refusal-cost")
    before = reads.spend(agent=agent)["usd"]
    store.apply("budget charge", actor="test", usd=0, source_ref=scope("refusal-cost-1"),
                source="run_json", agent=agent, note="refused run: one turn, no cost")
    after = reads.spend(agent=agent)["usd"]
    assert float(after) == float(before), "a refused run moved the meter"
    rows = [r for r in reads.incidents(limit=200) if r["agent"] == agent]
    assert not rows, "a refused run produced an incident"


# ------------------------------------------------------------------ the meter cannot lie


def test_a_charge_is_idempotent_on_its_source_ref():
    agent = scope("idem")
    ref = scope("idem-ref")
    first = store.apply("budget charge", actor="test", usd="1.00", source_ref=ref,
                        source="run_json", agent=agent)
    second = store.apply("budget charge", actor="test", usd="1.00", source_ref=ref,
                         source="run_json", agent=agent)
    assert first["charged"] is True and second["charged"] is False
    assert second["duplicate_of"] == first["charge_id"]
    assert float(reads.spend(agent=agent)["usd"]) == 1.00, "re-reading a run double-charged"


def test_a_warning_is_not_a_stop():
    agent = scope("warn")
    ceiling("agent", agent, "1.00", warn=50)
    store.apply("budget charge", actor="test", usd="0.60", source_ref=scope("warn-1"),
                source="manual", agent=agent)
    d = enforcer.evaluate(agent=agent)
    assert d.verdict == "warn" and d.allowed, f"a warning stopped work: {d.line()}"
    # And the database refuses a row that claims otherwise.
    try:
        store.apply("test.file-a-warning-that-claims-it-stopped-a-run")
    except psycopg2.errors.CheckViolation:
        return
    raise AssertionError("budget_incident accepted kind='warn' with action_taken='run_stopped'")


def test_hard_stop_is_disarmed_by_hard_stop_enabled_false():
    """A measurement is not a brake, and must not pretend to be one."""
    agent = scope("soft")
    ceiling("agent", agent, "0.50", hard_stop=False)
    store.apply("budget charge", actor="test", usd="2.00", source_ref=scope("soft-1"),
                source="manual", agent=agent)
    rows = reads.status("agent", agent)
    assert rows[0]["over_limit"] is True, "the meter did not notice"
    assert rows[0]["stopping"] is False, "a policy with hard_stop_enabled=false stopped work"
    d = enforcer.evaluate(agent=agent)
    assert d.allowed, "a soft ceiling refused a dispatch"


def test_a_soft_breach_is_recorded_as_a_soft_breach():
    """Crossing a disarmed ceiling is recorded honestly, and the run is NOT stopped.

    The first draft put this branch inside the stop path, where it was unreachable: a disarmed
    policy never makes evaluate() return `stop`, so the record was never written and a soft
    breach looked like an ordinary warning forever.
    """
    agent = scope("softrec")
    ceiling("agent", agent, "0.50", hard_stop=False, warn=50)
    guard = enforcer.RunGuard(agent=agent, by="test", grace=2, session_id=scope("softrec-s"))
    rc = guard.run([PY, str(HERE / "demo" / "spendy_agent.py"), "--units", "3", "--cost", "0.60",
                    "--delay", "0.05"])
    assert rc == 0, f"a disarmed ceiling stopped the run (rc={rc})"
    assert guard.incident_id is None, "a disarmed ceiling filed a hard stop"
    rows = [r for r in reads.incidents(limit=200, kind="soft_breach") if r["agent"] == agent]
    assert rows, "crossing a disarmed ceiling recorded no soft_breach"
    assert rows[0]["action_taken"] == "none", "a soft breach claims to have acted"


# ------------------------------------------------------------------ the operator stays in charge


def test_a_stop_does_not_clear_itself():
    agent = scope("latch")
    store.apply("budget stop", actor="test", scope_type="agent", scope_id=agent,
                kind="manual_stop", by="test", reason="test")
    assert not enforcer.evaluate(agent=agent).allowed
    time.sleep(1.5)
    assert not enforcer.evaluate(agent=agent).allowed, "a stop expired with the passage of time"
    store.apply("budget resume", actor="test", scope_type="agent", scope_id=agent, by="test")
    assert enforcer.evaluate(agent=agent).allowed, "resume did not lift the stop"


def test_raising_the_ceiling_does_not_lift_a_manual_stop():
    agent = scope("raise")
    ceiling("agent", agent, "1.00")
    store.apply("budget stop", actor="test", scope_type="agent", scope_id=agent,
                kind="manual_stop", by="test", reason="test")
    ceiling("agent", agent, "10000.00")
    d = enforcer.evaluate(agent=agent)
    assert not d.allowed, "changing the ceiling silently lifted the operator's own stop"
    store.apply("budget resume", actor="test", scope_type="agent", scope_id=agent, by="test")


def test_a_manual_stop_needs_no_policy():
    """`budget stop` on a scope with no ceiling still stops it.

    Reading only `budget_state` misses this, because a scope with no policy has no row there.
    """
    agent = scope("nopolicy")
    assert enforcer.evaluate(agent=agent).allowed
    store.apply("budget stop", actor="test", scope_type="agent", scope_id=agent,
                kind="manual_stop", by="test", reason="test")
    assert not enforcer.evaluate(agent=agent).allowed, "a stop on an unbudgeted scope did nothing"
    store.apply("budget resume", actor="test", scope_type="agent", scope_id=agent, by="test")


# ------------------------------------------------------------------ the waist holds


def test_the_verbs_are_registered_once_each():
    reg = store.registered()
    for verb in ("budget set", "budget unset", "budget charge", "budget stop", "budget resume",
                 "budget note", "budget outcome"):
        assert verb in reg, f"{verb} is not registered; some surface is writing its own SQL"
    assert reg["budget stop"]["role"] == "runtime"


def test_budget_state_cannot_be_written_through_the_read_path():
    with store.read("runtime") as s:
        try:
            s.query("UPDATE brain.budget_policy SET limit_usd = 999999")
        except Exception as exc:
            assert "read-only" in str(exc).lower() or "ReadOnly" in exc.__class__.__name__
            return
    raise AssertionError("a ceiling was raised through store.read()")


def test_a_fleet_policy_refuses_a_scope_id():
    try:
        store.apply("budget set", actor="test", scope_type="fleet", scope_id="somebody",
                    limit_usd="1.00", set_by="test")
    except BudgetError:
        return
    raise AssertionError("a scoped fleet policy was accepted; that is a second fleet")


# ------------------------------------------------------------------ fixtures


@store.transition("test.file-a-refusal-as-a-breach")
def _file_a_refusal(ctx):
    ctx.execute("INSERT INTO brain.budget_incident (kind, scope_type, cause, action_taken) "
                "VALUES ('hard_stop','fleet','rate_limit','run_stopped')")


@store.transition("test.file-a-warning-that-claims-it-stopped-a-run")
def _file_a_lying_warning(ctx):
    ctx.execute("INSERT INTO brain.budget_incident (kind, scope_type, action_taken) "
                "VALUES ('warn','fleet','run_stopped')")


def cleanup():
    for scope_type, scope_id, period in CREATED:
        store.apply("budget unset", actor="test", scope_type=scope_type, scope_id=scope_id,
                    period=period, by="test", reason="test cleanup")
    for row in reads.open_stops():
        if NONCE in (row["scope_id"] or ""):
            store.apply("budget resume", actor="test", scope_type=row["scope_type"],
                        scope_id=row["scope_id"], by="test", reason="test cleanup")
    left = [p for p in reads.status() if NONCE in (p["scope_id"] or "")]
    if left:
        print(f"WARNING: {len(left)} test ceiling(s) left active: "
              f"{[p['scope_id'] for p in left]}")
    return len(left)


def main() -> int:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            check(name, fn)
    leftover = cleanup()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed"
          + (f", {leftover} ceilings left active" if leftover else ""))
    return 1 if FAIL or leftover else 0


if __name__ == "__main__":
    sys.exit(main())
