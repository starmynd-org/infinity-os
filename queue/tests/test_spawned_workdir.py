#!/usr/bin/env python3
"""Where the work this lane SPAWNS runs, when the operator types no `--workdir`. Task 0118.

Two verbs here create work items -- `recommend accept` and `queue defer --kind until-question` --
and both hand `post` a workdir the operator usually does not type. Task 0100 then gated `post`:
a row the fleet may claim must carry an absolute workdir, because an empty one is not a lenient
boundary but no boundary at all (the runner falls through to the agent profile's own directory,
which is the parent of every repo on the box). Both verbs inherit `agent_claimable` from the row
they are about, so on the day 0100 landed both started REFUSING outright against any fleet source:
`recommend accept` rolled back the acceptance along with the spawn, which loses a human decision
rather than merely failing to act on it.

WHAT IS ASSERTED HERE IS THE SOURCE OF THE VALUE, not that a value exists. `_spawned_workdir`
inherits the deferred row's / the subject task's own workdir, and the case that would pass with a
hardcoded constant in the verb is the one this suite is written to fail: every test below uses a
DISTINCT path per source, so a constant fallback shows up as the wrong tree rather than as green.

Run:  python3 queue/tests/test_spawned_workdir.py
      (against the queue scratch database, never `brain`)
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "queue"))
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT))

os.environ.setdefault("BRAIN_PG_DB", os.environ.get("QUEUE_SCRATCH_DB", "brain_queue_scratch"))
os.environ.pop("SWARM_PARENT_TASK", None)      # the live runner exports this; tests post roots

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _scratch_preflight import reconcile                  # noqa: E402
reconcile(os.environ["BRAIN_PG_DB"])

import store                                              # noqa: E402
from human_queue import transitions as queue_transitions  # noqa: E402,F401
from human_queue.transitions import QueueError            # noqa: E402
from swarm_engine import transitions as engine            # noqa: E402,F401

SCRATCH = str(ROOT / "queue/bin/queue-scratch-db.sh")
PASS, FAIL = 0, 0

# Two trees that are not each other, and neither is this repo. A verb that reached for a constant
# -- the repo root, $HOME, the cwd -- passes nothing below, which is the whole point of using two.
ACME = "/srv/acme"
ENGINE = "/srv/engine-tree"


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ok    {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def reset():
    subprocess.run([SCRATCH, "psql", "-q", "-c",
                    "TRUNCATE brain.queue_item, brain.queue_defer, brain.queue_bump, "
                    "brain.queue_calibration, brain.queue_default_event, brain.recommendation, "
                    "brain.thread, brain.question, brain.agent, brain.work_item CASCADE; "
                    "SELECT setval('brain.item_id_seq', 1, false);"],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _sql(sql: str):
    """Direct psql, for TEST SETUP ONLY: building the row shape `post` will no longer create.

    A claimable row with an empty workdir is exactly what task 0100 stopped anyone from posting,
    and it is also what every such row created BEFORE 0100 still looks like. There is no verb that
    makes one now -- `post` refuses it and `set` refuses both halves of it -- so the only honest
    way to test the verb's behaviour against a legacy row is to write one the way history did.
    """
    subprocess.run([SCRATCH, "psql", "-q", "-c", sql], check=True, capture_output=True)


def work_item(tid):
    with store.read("runtime") as s:
        return s.one("SELECT * FROM brain.work_item WHERE id = %s", (tid,))


def refuses(fn, *a, **kw):
    try:
        fn(*a, **kw)
    except Exception as e:                                # noqa: BLE001 -- the message IS the test
        return str(e)
    return ""


# --------------------------------------------------------------- queue defer --kind until-question

def test_a_defer_question_runs_where_the_deferred_row_runs():
    """The task that unblocks an item is work in that item's tree, so it inherits that tree."""
    reset()
    src = store.apply("post", title="restate the Acme margin table", lane="data",
                      workdir=ACME, agent_claimable=True)
    out = store.apply("queue defer", source_type="work_item", source_id=src["id"],
                      kind="until-question", question="which export is authoritative?",
                      by="operator")
    check("the defer still spawns its task at all -- 0100 made this branch refuse outright",
          bool(out["wake_task"]), str(out))
    spawned = work_item(out["wake_task"])
    check("and the spawned task carries the DEFERRED ROW'S workdir, not a constant",
          spawned["workdir"] == ACME, f"workdir={spawned['workdir']!r}")
    check("it is claimable, which is why the workdir was required in the first place",
          spawned["agent_claimable"] is True, f"agent_claimable={spawned['agent_claimable']!r}")
    check("and it is parented to the row it unblocks", spawned["parent"] == src["id"],
          f"parent={spawned['parent']!r}")


def test_a_typed_workdir_beats_the_inheritance():
    """A recommendation is often "do this over in the other repo". Only the operator knows."""
    reset()
    src = store.apply("post", title="restate the Acme margin table", lane="data",
                      workdir=ACME, agent_claimable=True)
    out = store.apply("queue defer", source_type="work_item", source_id=src["id"],
                      kind="until-question", question="does the engine emit it?",
                      workdir=ENGINE, by="operator")
    check("the typed --workdir wins over the source's own",
          work_item(out["wake_task"])["workdir"] == ENGINE,
          f"workdir={work_item(out['wake_task'])['workdir']!r}")


def test_the_operators_own_row_still_defers_with_no_workdir_anywhere():
    """His queue is most of this table and none of it is dispatched. Nothing here may require a
    directory of a row that never cd's anywhere -- that would break his queue to guard a door his
    rows do not use."""
    reset()
    src = store.apply("post", title="decide the pricing tier", lane="client")
    check("the source is his: held, and carrying no workdir",
          work_item(src["id"])["agent_claimable"] is False
          and work_item(src["id"])["workdir"] == "", str(work_item(src["id"])["workdir"]))
    out = store.apply("queue defer", source_type="work_item", source_id=src["id"],
                      kind="until-question", question="what does he currently pay?", by="operator")
    spawned = work_item(out["wake_task"])
    check("it spawns, with no workdir and no refusal", spawned["workdir"] == "",
          f"workdir={spawned['workdir']!r}")
    check("because what a held row spawns is held too", spawned["agent_claimable"] is False,
          f"agent_claimable={spawned['agent_claimable']!r}")


# --------------------------------------------------------------------------- recommend accept

def test_an_acceptance_runs_where_its_subject_runs():
    """The failure this fixes is not a wrong workdir. It is an ACCEPTANCE THAT DID NOT LAND."""
    reset()
    subject = store.apply("post", title="Acme margin restate", lane="data",
                          workdir=ACME, agent_claimable=True)
    r = store.apply("recommend", text="restate the 2025 margin table",
                    proposed_action="rebuild the 2025 margin table from the source export",
                    subject_type="work_item", subject_id=subject["id"],
                    template_id="playbook-restate-a-metric")
    # as_operator: task 0290 made the decider a database login. `_login_for` reads this
    # keyword off the `apply` call, so every acceptance site has to pass it.
    out = store.apply("recommend accept", id=r["id"], by="operator", lane="data",
                      as_operator=True)
    check("the acceptance lands and spawns", bool(out["spawned_work_item"]), str(out))
    spawned = work_item(out["spawned_work_item"])
    check("the spawned work runs in THE SUBJECT'S tree", spawned["workdir"] == ACME,
          f"workdir={spawned['workdir']!r}")
    check("and it is claimable, inherited from the subject exactly as the hard flags are",
          spawned["agent_claimable"] is True, f"agent_claimable={spawned['agent_claimable']!r}")


def test_an_acceptance_takes_a_typed_workdir_over_the_subjects():
    reset()
    subject = store.apply("post", title="Acme margin restate", lane="data",
                          workdir=ACME, agent_claimable=True)
    r = store.apply("recommend", text="fix the emitter instead",
                    proposed_action="fix the emitter in the engine tree",
                    subject_type="work_item", subject_id=subject["id"])
    out = store.apply("recommend accept", id=r["id"], by="operator", lane="engine",
                      workdir=ENGINE, as_operator=True)
    check("the decider's --workdir is what the spawned work gets",
          work_item(out["spawned_work_item"])["workdir"] == ENGINE,
          f"workdir={work_item(out['spawned_work_item'])['workdir']!r}")


def test_a_recommendation_about_nothing_still_accepts():
    """A recommendation with no work_item subject has nothing to inherit from, and needs nothing:
    what it spawns has no parent, so it is held, so the gate does not apply to it."""
    reset()
    r = store.apply("recommend", text="write the weekly note",
                    proposed_action="write the weekly note")
    out = store.apply("recommend accept", id=r["id"], by="operator", lane="queue",
                      as_operator=True)
    spawned = work_item(out["spawned_work_item"])
    check("it spawned with no workdir and no refusal", spawned["workdir"] == "",
          f"workdir={spawned['workdir']!r}")
    check("and it is held, which is what makes that legal",
          spawned["agent_claimable"] is False, f"agent_claimable={spawned['agent_claimable']!r}")


# ------------------------------------------------- the row that predates the gate: refuse, by name

def _legacy_claimable_row(title="a row from before the gate", lane="data"):
    """A claimable row with an empty workdir -- the shape 0100 stopped anyone from creating."""
    _sql("INSERT INTO brain.work_item (title, lane, agent_claimable) "
         f"VALUES ('{title}', '{lane}', true)")
    with store.read("runtime") as s:
        return s.scalar("SELECT id FROM brain.work_item WHERE title = %s", (title,))


def test_a_claimable_source_with_no_workdir_is_refused_by_name():
    """Nothing to inherit is not a licence to guess. The refusal has to name the flag AND the row,
    because the operator cannot tell from the engine's own message whether to fix his command or
    fix the source."""
    reset()
    src = _legacy_claimable_row()
    check("the fixture really is the legacy shape",
          work_item(src)["agent_claimable"] is True and work_item(src)["workdir"] == "",
          str(dict(work_item(src))))
    msg = refuses(store.apply, "queue defer", source_type="work_item", source_id=src,
                  kind="until-question", question="what is missing?", by="operator")
    check("the defer is refused", bool(msg), "NOT REFUSED")
    check("the refusal names the flag", "--workdir" in msg, msg[:200])
    check("and names the row that had nothing to give", src in msg, msg[:200])
    with store.read("runtime") as s:
        n = s.scalar("SELECT count(*) FROM brain.queue_defer WHERE source_id = %s", (src,))
        spawned = s.scalar("SELECT count(*) FROM brain.work_item WHERE parent = %s", (src,))
    check("and NOTHING was written: the refusal rolled the whole transaction back",
          int(n) == 0 and int(spawned) == 0, f"defers={n} children={spawned}")


def test_the_acceptance_that_cannot_inherit_stays_OPEN():
    """The difference between a refusal and a lost decision. If the recommendation were marked
    accepted while the spawn refused, the operator would have decided once and be unable to decide
    again -- `recommend accept` refuses a recommendation that is not open."""
    reset()
    src = _legacy_claimable_row(title="a subject from before the gate")
    r = store.apply("recommend", text="do the thing", proposed_action="do the thing",
                    subject_type="work_item", subject_id=src)
    msg = refuses(store.apply, "recommend accept", id=r["id"], by="operator", lane="data",
                  as_operator=True)
    check("the acceptance is refused", "--workdir" in msg, msg[:200] or "NOT REFUSED")
    with store.read("runtime") as s:
        row = s.one("SELECT state, spawned_work_item FROM brain.recommendation WHERE id = %s",
                    (r["id"],))
    check("and the recommendation is STILL OPEN, so the decision can be made again with a flag",
          row["state"] == "open" and not row["spawned_work_item"], str(dict(row)))
    out = store.apply("recommend accept", id=r["id"], by="operator", lane="data",
                      workdir=ENGINE, as_operator=True)
    check("which it is: the same acceptance with --workdir lands",
          work_item(out["spawned_work_item"])["workdir"] == ENGINE, str(out))


def main():
    for fn in (test_a_defer_question_runs_where_the_deferred_row_runs,
               test_a_typed_workdir_beats_the_inheritance,
               test_the_operators_own_row_still_defers_with_no_workdir_anywhere,
               test_an_acceptance_runs_where_its_subject_runs,
               test_an_acceptance_takes_a_typed_workdir_over_the_subjects,
               test_a_recommendation_about_nothing_still_accepts,
               test_a_claimable_source_with_no_workdir_is_refused_by_name,
               test_the_acceptance_that_cannot_inherit_stays_OPEN):
        print(f"=== {fn.__name__} ===")
        fn()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
