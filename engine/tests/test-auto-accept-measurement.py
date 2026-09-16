#!/usr/bin/env python3
"""The week's disagreement report measures agreement, not whether the operator has clicked yet.

Task 0139, out of D9's acceptance audit (0118). The defect, on the live store, on the only data
point that existed at the time:

    0027 was measured `auto-accept WOULD have accepted this`, then accepted by the operator from
    the console -- perfect agreement -- and the report said
        {"n": 1, "agree": 0, "false_reject": ["0027"], "disagreement_rate": 1.0}

Because `rule_would_accept` was recomputed at READ time from `brain.auto_accept_candidate`, whose
`WHERE` carries `AND w.accepted_at IS NULL`. The acceptance removed the row, so the rule read as
having said no, and every human acceptance scored as a `false_reject`.

Every check below asserts the PROPERTY, not the implementation: score the verdict that was
recorded when the item was done, and never let an act that happens after the measurement change
what the measurement says. The first two would fail against the shipped code.

Run: python3 engine/tests/test-auto-accept-measurement.py   (scratch database, never `brain`)
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

# Reconcile this database to `migrations/` before anything below asserts anything. Task 0153.
# `run-all.sh` does this once for the whole run (task 0148); a suite run BY ITSELF did not, and a
# brief that asks a lane to prove one behaviour asks for exactly that. 11 passed / 9 failed against
# a schema three migrations behind is not a red suite, it is a red suite about nothing.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _scratch_preflight import reconcile              # noqa: E402
reconcile(os.environ["BRAIN_PG_DB"])
os.environ.pop("SWARM_PARENT_TASK", None)
os.environ.pop("ENGINE_AUTO_ACCEPT", None)         # the flag must come from the store, not the env

import store                                        # noqa: E402
from swarm_engine import accept, reads, transitions  # noqa: E402,F401  (registers every verb)

SCRATCH = str(ROOT / "engine/bin/scratch-db.sh")
SWARM = str(ROOT / "engine/bin/swarm")

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


def sh(*args):
    return subprocess.run([SWARM, *args], capture_output=True, text=True, env={**os.environ})


def eligible_task(title):
    """An item the rule WOULD accept: not external, not canon-touching, reversible."""
    return store.apply("post", lane="t", title=title, agent_claimable=True, workdir="/tmp",
                       signals={"reversibility": "reversible"})["id"]


def done(tid, agent="A1", summary="finished"):
    """Claim then report done. `--agent` is not optional: a verb may only act on what it holds."""
    store.apply("claim", agent=agent, lanes=["*"])
    store.apply("done", id=tid, summary=summary, agent=agent)


# ------------------------------------------------------------------ the defect itself

def test_acceptance_is_agreement_not_a_false_reject():
    """THE REGRESSION. The rule says yes, the human says yes: that is agreement.

    Against the shipped code this returned agree=0, false_reject=[tid], rate=1.0.
    """
    reset()
    tid = eligible_task("the rule and the human agree")
    done(tid)
    truth("the rule recorded a verdict at done time",
          accept.would_accept(tid)["eligible"] is True, str(accept.would_accept(tid)))

    before = accept.disagreement_report()
    eq("before the human decides, nothing is scored", before["scored"], 0)
    eq("and the rate is None, NOT 0.0 -- a clean rate from an empty test is D00's named failure",
       before["disagreement_rate"], None)
    eq("the undecided item is pending", before["pending"], [tid])
    eq("pending is NOT a rejection, so false_accept is empty", before["false_accept"], [])

    store.apply("accept work", id=tid, by="operator", as_operator=True)

    r = accept.disagreement_report()
    eq("after the human accepts, the verdict is scored", r["scored"], 1)
    eq("the rule and the human AGREED", r["agree"], 1)
    eq("the acceptance is not a false reject", r["false_reject"], [])
    eq("nor a false accept", r["false_accept"], [])
    eq("the disagreement rate is 0.0 because one comparison was made and it matched",
       r["disagreement_rate"], 0.0)
    truth("the acceptance did NOT change what the rule was recorded as saying",
          accept.measurements()[0]["rule_would_accept"] is True, str(accept.measurements()))
    eq("and the item left brain.auto_accept_candidate, which is why the old reader broke",
       [c["id"] for c in reads.auto_accept_candidates() if c["id"] == tid], [])


def test_false_accept_is_the_direction_that_matters():
    """The rule says yes, the human sends it back. THIS is the failure mode worth measuring.

    Against the shipped code `false_accept` could only ever hold items the human had not yet
    accepted, so it counted the backlog and this real disagreement was indistinguishable from it.
    """
    reset()
    tid = eligible_task("the rule is wrong and a human catches it")
    done(tid)
    store.apply("reopen", id=tid, reason="not what I asked for", agent="operator")

    r = accept.disagreement_report()
    eq("a rejection of an eligible item is a FALSE ACCEPT", r["false_accept"], [tid])
    eq("it is scored", r["scored"], 1)
    eq("nothing agreed", r["agree"], 0)
    eq("and the rate says so", r["disagreement_rate"], 1.0)
    eq("the rejecter is named", accept.measurements()[0]["decided_by"], "operator")


def test_false_reject_is_the_other_direction():
    """The rule says no, the human accepts anyway. Real disagreement, opposite sign."""
    reset()
    tid = store.apply("post", lane="t", title="canon, which the rule never accepts",
                      agent_claimable=True, workdir="/tmp",
                      canon_touching=True, signals={"reversibility": "reversible"})["id"]
    done(tid)
    eq("the rule declined it", accept.would_accept(tid)["eligible"], False)
    store.apply("accept work", id=tid, by="operator", auto=False, as_operator=True)

    r = accept.disagreement_report()
    eq("a human accepting what the rule declined is a false reject", r["false_reject"], [tid])
    eq("not a false accept", r["false_accept"], [])
    eq("and the rate is 100% over the one comparison", r["disagreement_rate"], 1.0)


def test_a_withdrawal_is_not_a_rejection():
    """`cancel` renders no verdict on the work. Scoring it as one puts dead items in the rate."""
    reset()
    tid = eligible_task("done, then withdrawn")
    done(tid)
    store.apply("cancel", id=tid, reason="the objective changed")

    r = accept.disagreement_report()
    eq("the cancelled item is withdrawn", r["withdrawn"], [tid])
    eq("it is not scored", r["scored"], 0)
    eq("not a false accept", r["false_accept"], [])
    eq("and there is no rate to report", r["disagreement_rate"], None)


def test_two_done_cycles_are_two_data_points():
    """Done, rejected, done again, accepted: the rule evaluated twice and the human decided twice.

    The pairing is per verdict, so the second cycle's acceptance cannot be read as agreement with
    the first cycle's verdict, and the first cycle's rejection stays on the record.
    """
    reset()
    tid = eligible_task("two cycles")
    done(tid)
    store.apply("reopen", id=tid, reason="try again", agent="operator")
    done(tid, agent="A2", summary="better")
    store.apply("accept work", id=tid, by="operator", as_operator=True)

    ms = accept.measurements()
    eq("two verdicts were recorded, one per done", len(ms), 2)
    eq("the first was rejected", ms[0]["human"], "rejected")
    eq("the second was accepted", ms[1]["human"], "accepted")
    r = accept.disagreement_report()
    eq("both are scored", r["scored"], 2)
    eq("one agreed", r["agree"], 1)
    eq("the rejection is still a false accept", r["false_accept"], [tid])
    eq("the rate is over two comparisons, not one item", r["disagreement_rate"], 0.5)


def test_a_runner_wall_after_a_rejection_is_not_a_second_rejection():
    """`reopen` is ALSO the subscription-refusal verb (D00 rule 6), and it must not double-count.

    The refusal path only fires on an ACTIVE task, so it can never be the first deciding event
    after a `done`. Taking only the first one is what keeps a wall out of the score.
    """
    reset()
    tid = eligible_task("rejected, then walled")
    done(tid)
    store.apply("reopen", id=tid, reason="not what I asked for", agent="operator")
    store.apply("claim", agent="A1", lanes=["*"])
    store.apply("reopen", id=tid, agent="A1",
                reason="subscription rate limit, not a task failure: the engine refused before "
                       "doing any work")

    ms = accept.measurements()
    eq("still one verdict, because there was one done", len(ms), 1)
    eq("paired with the HUMAN's rejection, not the wall", ms[0]["decided_by"], "operator")
    eq("scored once", accept.disagreement_report()["scored"], 1)


# ------------------------------------------------------------------ the writer's record

def test_the_rules_own_acceptance_is_not_scored_as_a_human_agreeing():
    """Flag ON: the rule accepts. There is no human decision, so there is nothing to compare."""
    reset()
    tid = eligible_task("the rule acts")
    store.apply("auto accept flag", on=True, by="test", note="proving the acting path records")
    truth("the flag is on", accept.enabled(), str(accept.enabled()))
    done(tid)

    eq("the rule accepted it", reads.task(tid)["accepted_by"], "auto-accept")
    ms = accept.measurements()
    eq("the acting case records a verdict too, so the report can see it", len(ms), 1)
    truth("marked as the rule's own act", ms[0]["rule_acted"] is True, str(ms[0]))
    r = accept.disagreement_report()
    eq("it is counted in the population", r["n"], 1)
    eq("but NOT scored as a human agreeing with the rule", r["scored"], 0)
    eq("and it is named as the rule's own", r["rule_acted"], [tid])
    store.apply("auto accept flag", on=False, by="test")


def test_the_recorded_label_states_the_flags_real_value():
    """The trail must not say `rule DISABLED` while the rule is enabled and merely declining."""
    reset()
    tid = store.apply("post", lane="t", title="external, which the rule declines",
                      agent_claimable=True, workdir="/tmp",
                      external=True, signals={"reversibility": "reversible"})["id"]
    store.apply("auto accept flag", on=True, by="test", note="proving the label")
    done(tid)
    store.apply("auto accept flag", on=False, by="test")

    notes = [e["text"] for e in reads.thread(tid) if accept.MEASURE_TAG in e["text"]]
    eq("one verdict recorded", len(notes), 1)
    truth("labelled ENABLED, because the flag was on when it declined",
          notes[0].startswith(accept.ENABLED_PREFIX), notes[0])
    truth("and it says why it declined", "external" in notes[0], notes[0])
    eq("the rule did not accept it", reads.task(tid)["accepted_at"], None)


def test_the_shipped_disabled_wording_still_scores():
    """The live thread already carries verdicts in the shipped wording. A reformat would lose them.

    Asserted against the exact string read off the live store on 2026-08-16 (`0027` seq 30), so a
    future edit to the note text cannot silently orphan the measurements already recorded.
    """
    LIVE = "[auto-accept measurement, rule DISABLED] auto-accept WOULD have accepted this"
    eq("the writer still composes the shipped string byte for byte",
       accept.DISABLED_PREFIX + accept.VERDICT_YES, LIVE)
    truth("and the reader recognises it", LIVE.startswith(accept.MEASURE_TAG), LIVE)


# ------------------------------------------------------------------ the flag has a writer

def test_the_flag_is_written_by_a_verb_not_by_raw_sql():
    reset()
    eq("it ships disabled", accept.enabled(), False)
    r = store.apply("auto accept flag", on=True, by="operator", note="one week measured")
    eq("the verb turns it on", r["enabled"], True)
    eq("and enabled() agrees", accept.enabled(), True)
    with store.read("runtime") as s:
        row = s.one("SELECT value, set_by, note FROM brain.runtime_flag WHERE key = %s",
                    (accept.FLAG,))
    eq("the row records who", row["set_by"], "operator")
    eq("and why", row["note"], "one week measured")

    store.apply("auto accept flag", on=False, by="operator")
    eq("off is an UPDATE, not a DELETE: the record survives", accept.enabled(), False)
    with store.read("runtime") as s:
        truth("the row is still there, saying who turned it off",
              s.one("SELECT set_by FROM brain.runtime_flag WHERE key = %s",
                    (accept.FLAG,))["set_by"] == "operator")

    try:
        store.apply("auto accept flag", on=True, by="operator", note="   ")
        bad("enabling with no reason is refused")
    except RuntimeError as e:
        truth("enabling with no reason is refused", "refusing to enable" in str(e), str(e))
    eq("so it is still off", accept.enabled(), False)


def test_the_cli_reads_the_measurement():
    """Nothing read `disagreement_report()` before this verb existed. The gate was unreadable."""
    reset()
    tid = eligible_task("readable from the CLI")
    done(tid)
    store.apply("accept work", id=tid, by="operator", as_operator=True)

    r = sh("auto-accept")
    eq("`swarm auto-accept` exits 0", r.returncode, 0)
    truth("it says the flag is disabled", "DISABLED" in r.stdout, r.stdout)
    truth("and prints the rate rather than hiding it", "disagreement rate" in r.stdout, r.stdout)
    r = sh("auto-accept", "--json", "--full")
    truth("--json --full carries every recorded verdict", '"measurements"' in r.stdout,
          r.stdout[:300])
    truth("and the agreement is in it", '"agree": 1' in r.stdout, r.stdout[:300])


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    print(f"test-auto-accept-measurement.py  --  task 0139, against {os.environ['BRAIN_PG_DB']}")
    for t in TESTS:
        print(f"\n{t.__name__}")
        first = ((t.__doc__ or "").strip().splitlines() or [""])[0]
        if first:
            print(f"  {first}")
        try:
            t()
        except Exception as e:                                   # noqa: BLE001
            bad(f"{t.__name__} raised", f"{e.__class__.__name__}: {e}")
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
