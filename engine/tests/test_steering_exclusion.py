#!/usr/bin/env python3
"""Property 4 of the take-control overrule, proven rather than asserted.

    "A steered run is MARKED steered, and excluded from auto-accept eligibility and from the
     disagreement measurement. A run the operator steered is no longer an independent
     self-report -- it is co-authored. Without this, the two-week measurement quietly measures
     his own steering back to him."   -- V4 brief, which calls this the property easiest to forget

The shape of every check below is the same and it is the shape that makes it a proof rather than
a demonstration: build a task the rule WOULD accept, measure that it would, then steer it and
measure the same task again. A test that only ever showed an ineligible task staying ineligible
would pass against code where the exclusion does not exist.

The strongest one is `test_the_flag_on_does_not_accept_a_steered_run`: it turns auto-accept ON,
which is the only state in which the exclusion has teeth, and asserts the row is not accepted.

Run: python3 engine/tests/test_steering_exclusion.py     (scratch database, never `brain`)
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT))

os.environ.setdefault("BRAIN_PG_DB", os.environ.get("ENGINE_SCRATCH_DB", "brain_scratch"))

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _scratch_preflight import reconcile              # noqa: E402
reconcile(os.environ["BRAIN_PG_DB"])
os.environ.pop("SWARM_PARENT_TASK", None)
os.environ.pop("ENGINE_AUTO_ACCEPT", None)      # the flag comes from the store, never the env

import store                                            # noqa: E402
from swarm_engine import accept, steering, transitions  # noqa: E402,F401  (registers the verbs)

SCRATCH = str(ROOT / "engine/bin/scratch-db.sh")

PASS, FAIL = 0, 0


def ok(msg):
    global PASS
    PASS += 1
    print(f"  ok    {msg}")


def bad(msg, detail=""):
    global FAIL
    FAIL += 1
    print(f"  FAIL  {msg}")
    if detail:
        print(f"        {detail}")


def eq(msg, got, want):
    ok(msg) if got == want else bad(msg, f"wanted [{want}], got [{got}]")


def truth(msg, cond, detail=""):
    ok(msg) if cond else bad(msg, detail)


def reset():
    subprocess.run([SCRATCH, "truncate"], check=True, capture_output=True)


def eligible_task(title):
    """An item the rule WOULD accept: not external, not canon-touching, reversible."""
    return store.apply("post", lane="t", title=title, agent_claimable=True, workdir="/tmp",
                       signals={"reversibility": "reversible"})["id"]


def claimed(title, agent="T-probe"):
    tid = eligible_task(title)
    store.apply("claim", agent=agent, lanes=["*"])
    return tid


def thread_of(tid):
    with store.read() as s:
        return s.query("SELECT seq, from_agent, kind, text FROM brain.thread "
                       "WHERE work_item_id = %s ORDER BY seq", (tid,))


def accepted_at(tid):
    with store.read() as s:
        return s.scalar("SELECT accepted_at FROM brain.work_item WHERE id = %s", (tid,))


# ------------------------------------------------------------------ the mark itself

def test_seizing_a_run_marks_it_and_the_mark_is_on_the_thread():
    """Condition 2 of the overrule: the record is what makes `show --full` reconstructable."""
    reset()
    tid = claimed("a run the operator seizes")
    truth("before anything is seized the run is NOT steered", steering.is_steered(tid) is False)

    out = store.apply("steer take", id=tid, agent="T-probe", by="operator", actor="operator", as_operator=True)
    eq("the verb reports the terminal it seized", out["agent"], "T-probe")
    truth("the run is now marked steered", steering.is_steered(tid) is True)

    marks = [t for t in thread_of(tid) if t["text"].startswith(steering.STEER_TAG)]
    eq("exactly one steering row landed on the thread", len(marks), 1)
    eq("and it is attributed to the operator, not to the terminal",
       marks[0]["from_agent"], "operator")
    truth("the row carries the consequence the operator was shown before he seized it",
          steering.CONSEQUENCE in marks[0]["text"], marks[0]["text"])

    again = store.apply("steer take", id=tid, agent="T-probe", by="operator", actor="operator", as_operator=True)
    truth("seizing twice is idempotent rather than a second mark", again["already"] is True)
    eq("so the thread still carries one steering row",
       len([t for t in thread_of(tid) if t["text"].startswith(steering.SEIZED)]), 1)


def test_release_does_not_unmark_the_run():
    """The mark is a fact about how the work was produced, not a mode."""
    reset()
    tid = claimed("a run seized and then released")
    store.apply("steer take", id=tid, agent="T-probe", by="operator", actor="operator", as_operator=True)
    truth("control is held", steering.active_steering(tid) is not None)
    store.apply("steer release", id=tid, by="operator", actor="operator", as_operator=True)
    truth("control is no longer held", steering.active_steering(tid) is None)
    truth("AND THE RUN IS STILL MARKED STEERED", steering.is_steered(tid) is True)


def test_a_run_nobody_is_running_cannot_be_seized():
    """Condition 1: a mode entered deliberately, per terminal -- over a real run.

    Without this a steering session over an unclaimed row would mark the task steered, removing
    it from the measurement, for a conversation that reached no terminal.
    """
    reset()
    tid = eligible_task("nobody has claimed this")
    try:
        store.apply("steer take", id=tid, agent="T-probe", by="operator", actor="operator", as_operator=True)
        bad("seizing an unclaimed row was refused")
    except Exception as exc:                                            # noqa: BLE001
        truth("seizing an unclaimed row is refused", "no run here" in str(exc), str(exc)[:200])
    truth("and nothing was marked", steering.is_steered(tid) is False)


# ------------------------------------------------------------------ property 4, eligibility

def test_the_same_task_is_eligible_before_steering_and_ineligible_after():
    """THE PROOF. One task, measured twice, with steering the only thing that changed."""
    reset()
    tid = claimed("eligible until the operator takes the wheel")
    store.apply("done", id=tid, summary="reported", agent="T-probe")

    before = accept.would_accept(tid)
    truth("the rule WOULD have accepted this task", before["eligible"] is True, str(before))

    store.apply("claim", agent="T-probe", lanes=["*"])          # nothing left to claim; harmless
    store.apply("reopen", id=tid, reason="sending it back so it can be steered on the retry",
                agent="operator")
    store.apply("claim", agent="T-probe", lanes=["*"])
    store.apply("steer take", id=tid, agent="T-probe", by="operator", actor="operator", as_operator=True)
    store.apply("done", id=tid, summary="reported again, this time co-authored", agent="T-probe")

    after = accept.would_accept(tid)
    truth("the SAME task is now ineligible", after["eligible"] is False, str(after))
    truth("and the reason names steering rather than one of the three signals",
          after["reasons"] == [accept.STEERED_REASON], str(after["reasons"]))


def test_the_flag_on_does_not_accept_a_steered_run():
    """The exclusion with teeth: auto-accept ENABLED, and the steered row is still not accepted.

    The control in the same transaction-shaped world is the second task, which IS accepted -- so
    a failure here cannot be "the flag never worked".
    """
    reset()
    store.apply("auto accept flag", on=True, by="operator",
                note="test only: proving the steered exclusion holds where it can be seen")
    truth("auto-accept is ON for this check", accept.enabled() is True)

    control = claimed("an ordinary run the rule may accept")
    store.apply("done", id=control, summary="reported", agent="T-probe")
    truth("the control task WAS accepted automatically", accepted_at(control) is not None)

    steered = claimed("a steered run the rule may not accept", agent="T-probe2")
    store.apply("steer take", id=steered, agent="T-probe2", by="operator", actor="operator", as_operator=True)
    store.apply("done", id=steered, summary="reported", agent="T-probe2")
    truth("THE STEERED TASK WAS NOT ACCEPTED", accepted_at(steered) is None)

    notes = [t["text"] for t in thread_of(steered)]
    truth("the trail says why, in the steering vocabulary",
          any(t.startswith(steering.EXCLUDED) for t in notes), str(notes))
    truth("and NO auto-accept verdict was recorded for it",
          not any(t.startswith(accept.MEASURE_TAG) for t in notes), str(notes))

    store.apply("auto accept flag", on=False, by="operator")


# ------------------------------------------------------------------ property 4, the measurement

def test_a_steered_task_is_not_scored_by_the_weeks_report():
    """The number the operator will read in two weeks, with and without a steered run in it."""
    reset()
    plain = claimed("an ordinary run, measured and decided")
    store.apply("done", id=plain, summary="reported", agent="T-probe")
    store.apply("accept work", id=plain, by="operator", as_operator=True)

    base = accept.disagreement_report()
    eq("one comparison is scored", base["scored"], 1)
    eq("the rule and the human agreed", base["agree"], 1)
    eq("nothing is excluded yet", base["steered_excluded"], [])

    steered = claimed("a steered run, decided the same way", agent="T-probe2")
    store.apply("steer take", id=steered, agent="T-probe2", by="operator", actor="operator", as_operator=True)
    store.apply("done", id=steered, summary="reported", agent="T-probe2")
    store.apply("accept work", id=steered, by="operator", as_operator=True)

    r = accept.disagreement_report()
    eq("the steered task is NAMED as excluded, not silently dropped",
       r["steered_excluded"], [steered])
    eq("the scored count did NOT move", r["scored"], base["scored"])
    eq("nor did the agreement count", r["agree"], base["agree"])
    eq("the steered task appears in no measurement row",
       [m for m in accept.measurements() if m["id"] == steered], [])
    eq("and the rate is still the one comparison's rate",
       r["disagreement_rate"], base["disagreement_rate"])


def test_an_agent_cannot_steer_its_own_work_out_of_the_measurement():
    """The forgery guard. `note` is a verb the fleet holds; the mark is not.

    An agent writing the tag has written a note. It has not excluded itself from anything.
    """
    reset()
    tid = claimed("an agent tries to mark its own run steered")
    store.apply("note", id=tid, text=steering.SEIZED + "by T-probe · T-probe · attempt 1.",
                agent="T-probe", actor="T-probe")
    truth("the tag is on the thread",
          any(t["text"].startswith(steering.SEIZED) for t in thread_of(tid)))
    truth("AND IT MARKS NOTHING: the run is not steered", steering.is_steered(tid) is False)

    store.apply("done", id=tid, summary="reported", agent="T-probe")
    truth("so the rule still evaluates it", accept.would_accept(tid)["eligible"] is True,
          str(accept.would_accept(tid)))
    store.apply("accept work", id=tid, by="operator", as_operator=True)
    r = accept.disagreement_report()
    eq("and the week's report still scores it", r["scored"], 1)
    eq("with the agent's tag excluded from the exclusion list", r["steered_excluded"], [])

    try:
        store.apply("steer take", id=tid, agent="T-probe", by="T-probe", actor="T-probe")
        bad("an agent calling the verb from its own login is refused")
    except Exception as exc:                                            # noqa: BLE001
        truth("an agent calling the verb from its own login is refused",
              "does not know as a human" in str(exc), str(exc)[:200])
    try:
        # And with the operator login open, claiming to be somebody else is refused too: the
        # recorded name is the database's answer, so a mark can never say a name the connection
        # is not.
        store.apply("steer take", id=tid, agent="T-probe", by="T-probe", actor="T-probe",
                    as_operator=True)
        bad("claiming a different name than the connection holds is refused")
    except Exception as exc:                                            # noqa: BLE001
        truth("claiming a different name than the connection holds is refused",
              "the database says the connection is" in str(exc), str(exc)[:200])


def test_both_directions_of_a_steering_session_land_on_the_thread():
    """Condition 2, through the verb that already exists. No private message path was built."""
    reset()
    tid = claimed("a conversation held under control")
    store.apply("steer take", id=tid, agent="T-probe", by="operator", actor="operator", as_operator=True)
    store.apply("msg", to="T-probe", task=tid, text="stop reading and run the test",
                agent="operator", actor="operator")
    store.apply("note", id=tid, text="running it now", agent="T-probe", actor="T-probe")

    rows = thread_of(tid)
    truth("the operator's word is on the thread",
          any(r["kind"] == "msg" and r["from_agent"] == "operator" for r in rows), str(rows))
    truth("the terminal's answer is on the thread",
          any(r["kind"] == "note" and r["from_agent"] == "T-probe" for r in rows), str(rows))
    with store.read() as s:
        box = s.query("SELECT to_agent, text FROM brain.message WHERE work_item_id = %s", (tid,))
    eq("and the operator's word also reached the terminal's mailbox, "
       "because `msg` is the verb that was reused rather than reimplemented",
       [b["to_agent"] for b in box], ["T-probe"])


def test_the_runtime_login_cannot_write_a_mark_at_all():
    """The guard that matters most: identity comes from the DATABASE, not from `by=`.

    This is the call an agent process would make. It opens no operator login, so
    `brain.current_human()` is NULL and the verb refuses before anything is written -- which is
    the hole the old argument-only check left, since `by="operator"` is a string anyone can type.
    """
    reset()
    tid = claimed("an agent process typing by=operator")
    try:
        store.apply("steer take", id=tid, agent="T-probe", by="operator", actor="operator")
        bad("a call that did not open the operator login is refused")
    except Exception as exc:                                            # noqa: BLE001
        truth("a call that did not open the operator login is refused",
              "does not know as a human" in str(exc), str(exc)[:200])
    truth("and the run is not marked", steering.is_steered(tid) is False)


def main():
    print(f"steering exclusion, against {os.environ['BRAIN_PG_DB']}")
    for fn in (test_seizing_a_run_marks_it_and_the_mark_is_on_the_thread,
               test_release_does_not_unmark_the_run,
               test_a_run_nobody_is_running_cannot_be_seized,
               test_the_same_task_is_eligible_before_steering_and_ineligible_after,
               test_the_flag_on_does_not_accept_a_steered_run,
               test_a_steered_task_is_not_scored_by_the_weeks_report,
               test_an_agent_cannot_steer_its_own_work_out_of_the_measurement,
               test_both_directions_of_a_steering_session_land_on_the_thread,
               test_the_runtime_login_cannot_write_a_mark_at_all):
        print(f"\n{fn.__name__}")
        print(f"  {(fn.__doc__ or '').strip().splitlines()[0]}")
        fn()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
