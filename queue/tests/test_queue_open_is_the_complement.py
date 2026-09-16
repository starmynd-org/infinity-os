#!/usr/bin/env python3
"""THE OPERATOR'S QUEUE IS EVERYTHING THE FLEET MAY NOT TAKE, AND THERE IS NO THIRD STATE.

Task 0421, out of 0414. `brain.queue_open`'s second arm -- the one whose `primary_verb` is
`Mark my task done`, which IS his own queue on the console -- read `w.actor_type = 'human'`.
That column has a meaningful NULL ("unset is a real state", migration 1), and on live `brain` on
2026-08-18 two of the operator's fifteen open rows carried it:

    0068  qc         Double-check the exported QC data against the warehouse
    0071  marketing  Unify the multiple presentations and incorporate the feedback

Fifteen rows in `inbox` posted by him, thirteen on `queue_open`. 0068 is the input to 0069
*Send the double-checked QC result to the client*: the row that says check it before you send it was
the one he could not see.

`queue/schema/0013` replaces that arm with `NOT w.agent_claimable`, the exact complement of the
predicate `claim` applies (`engine/swarm_engine/reads.py`, `AND w.agent_claimable`). This suite
asserts the partition rather than the fix: every `work_item` in `inbox` or `active` is on
EXACTLY ONE of the two queues, never neither and never both, whatever `actor_type` says.

The one-term predicate is not a shortcut. Migration 26's `work_item_human_is_never_agent_claimable`
coerces `agent_claimable := false` on every `actor_type = 'human'` row, so the brief's proposed
`actor_type = 'human' OR NOT agent_claimable` reduces to its second term -- and test 3 below
proves the coercion rather than trusting the migration comment for it.

Run:  QUEUE_SCRATCH_DB=brain_scratch_t4_0421 BRAIN_PG_DB=brain_scratch_t4_0421 \
        python3 queue/tests/test_queue_open_is_the_complement.py
      (against a scratch database, never `brain`)
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
from _scratch_preflight import reconcile                          # noqa: E402
reconcile(os.environ["BRAIN_PG_DB"])

import store                                                      # noqa: E402
from swarm_engine import transitions as engine                    # noqa: E402

from web import model as web_model, rooms as web_rooms            # noqa: E402

SCRATCH = str(ROOT / "queue/bin/queue-scratch-db.sh")
SECRETS = Path(os.environ.get("BRAIN_SECRET_DIR", str(Path.home() / ".brain-postgres-secrets")))
PASS, FAIL = 0, 0

# The predicate this file replaced, kept verbatim so the regression is asserted against the real
# thing rather than against a paraphrase of it.
OLD_ARM = "w.actor_type = 'human' AND w.state IN ('inbox', 'active')"
NEW_ARM = "NOT w.agent_claimable AND w.state IN ('inbox', 'active')"


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ok    {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")
    return ok


def refused(fn, *a, **kw) -> str:
    """Run something that must be refused. Returns the message, or '' if it was NOT refused."""
    try:
        fn(*a, **kw)
        return ""
    except Exception as e:                                        # noqa: BLE001
        return str(e) or e.__class__.__name__


def reset():
    subprocess.run([SCRATCH, "psql", "-q", "-c",
                    "TRUNCATE brain.queue_item, brain.queue_defer, brain.queue_bump, "
                    "brain.queue_calibration, brain.queue_default_event, brain.recommendation, "
                    "brain.thread, brain.question, brain.agent, brain.work_item CASCADE; "
                    "SELECT setval('brain.item_id_seq', 1, false);"],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def row(tid: str) -> dict:
    with store.read("runtime") as s:
        return s.one("SELECT * FROM brain.work_item WHERE id = %s", (tid,)) or {}


def on_his_queue(tid: str) -> str:
    """The arm this row reaches the console through, or '' for no surface at all."""
    with store.read("runtime") as s:
        r = s.one("SELECT primary_verb FROM brain.queue_open WHERE source_id = %s "
                  "AND source_type = 'work_item'", (tid,))
    return (r or {}).get("primary_verb") or ""


def matched_by(where: str) -> set:
    with store.read("runtime") as s:
        return {r["id"] for r in s.query(
            f"SELECT w.id FROM brain.work_item w WHERE {where}")}


def provisioned() -> bool:
    """Is there an operator login on this host at all? Reported, never skipped silently."""
    return (SECRETS / "brain-postgres-role-operator").is_file()


# ------------------------------------------------------- 1. the row that was on no surface

def test_the_unclassified_operator_row_reaches_his_console():
    """0068's and 0071's shape, reproduced: his row, posted with no actor_type at all.

    This is the exact defect. An operator row that nobody marked `human` is `actor_type NULL` and
    `agent_claimable false`, and under the old arm it matched neither queue.
    """
    reset()
    r = store.apply("post", title="Double-check the exported QC data against the warehouse",
                    lane="qc", posted_by="operator",
                    body="0068's shape: his row, posted in a batch where the human mark was missed")
    tid = r["id"]
    w = row(tid)

    check("it reproduces the live shape: actor_type NULL", w.get("actor_type") is None,
          f"actor_type={w.get('actor_type')!r}")
    check("and NOT agent-claimable, by the column's default and not by anyone remembering",
          w.get("agent_claimable") is False, f"agent_claimable={w.get('agent_claimable')!r}")

    check("THE OLD ARM DID NOT MATCH IT -- the defect, asserted rather than described",
          tid not in matched_by(OLD_ARM))
    check("the new arm does", tid in matched_by(NEW_ARM))
    check("so it is on his queue, as his own task",
          on_his_queue(tid) == "Mark my task done", f"arm={on_his_queue(tid)!r}")
    return tid


def test_the_console_permits_done_on_it():
    """The half of the fix that lives outside the view, verified through the REAL card path.

    `web/rooms.py::assert_allowed_on` reads `item['actor_type']`, and `item` is the console CARD
    from `web/model.py::_card`, not the work_item row: `_card` looks `kind` up in `KIND_BY_ARM`
    on `(source_type, primary_verb)` and then stamps `actor_type` from `_VERBS[kind]`. So the
    card's actor_type is decided by WHICH ARM the row arrived on. This asserts that end to end
    through `model.find_item` rather than by hand-building the dict, because a hand-built dict is
    what would hide the difference.
    """
    tid = test_the_unclassified_operator_row_reaches_his_console()
    card = web_model.find_item(tid)
    check("the console finds the row at all", bool(card), "find_item returned None")
    if not card:
        return
    check("it renders as his own task, not as an agent's finished work",
          card.get("kind") == "human" and card.get("label") == "Mark my task done",
          f"kind={card.get('kind')!r} label={card.get('label')!r}")
    check("and the console's guard PERMITS `done` on it",
          refused(web_rooms.assert_allowed_on, "queue", "done", card) == "",
          refused(web_rooms.assert_allowed_on, "queue", "done", card))

    res = store.apply("done", id=tid, summary="checked against the warehouse, two rows differ",
                      agent="operator", actor="operator")
    check("`done` lands and the row leaves his queue",
          row(tid)["state"] == "done" and on_his_queue(tid) != "Mark my task done",
          f"state={row(tid)['state']} arm={on_his_queue(tid)!r} res={res}")


# ------------------------------------------------------- 2. and it is still not the fleet's

def test_the_fleet_still_cannot_take_it():
    """Visible to him must not mean claimable by an agent. `claim` is untouched by 0013."""
    tid = test_the_unclassified_operator_row_reaches_his_console()
    got = store.apply("claim", agent="T-test-0421", lanes=["*"], host="test")
    took = (got or {}).get("id") if isinstance(got, dict) else None
    check("an agent with lanes ['*'] is handed NOTHING", took != tid,
          f"claim handed out {took!r}")
    check("and the row is still in inbox, unclaimed, attempts unspent",
          row(tid)["state"] == "inbox" and not (row(tid).get("claimed_by") or ""),
          f"state={row(tid)['state']} claimed_by={row(tid).get('claimed_by')!r}")


# ------------------------------------------------------- 3. the thirteen still land

def test_a_human_marked_row_is_unaffected():
    """The thirteen rows the old arm DID match must still be there, and by the SAME term.

    This is what makes the one-term predicate honest: migration 26's trigger coerces every
    `actor_type='human'` row to `agent_claimable=false`, so `NOT agent_claimable` is a superset
    of `actor_type='human'` by construction. Proved here, not read off a comment.
    """
    reset()
    if not provisioned():
        check("SKIPPED-AS-FAILURE: no operator login on this host, so `actor_type='human'` "
              "cannot be written and this claim is UNPROVEN here", False,
              f"missing {SECRETS}/brain-postgres-role-operator")
        return
    r = store.apply("post", title="Renew the business insurance before it lapses",
                    lane="ops", posted_by="operator", actor_type="human",
                    body="a decision, not an agent")
    tid = r["id"]
    w = row(tid)
    check("the row is his by the login, not by a flag", w.get("actor_type") == "human")
    check("the trigger COERCED it out of the fleet's reach: agent_claimable false",
          w.get("agent_claimable") is False, f"agent_claimable={w.get('agent_claimable')!r}")
    check("the old arm matched it", tid in matched_by(OLD_ARM))
    check("the new arm matches it too -- no row the old predicate showed him is lost",
          tid in matched_by(NEW_ARM))
    check("and it is on his queue", on_his_queue(tid) == "Mark my task done")

    # The coercion, driven from the other side: ask for human AND claimable in one post.
    # `workdir` is required of any agent-claimable row since task 0100: an empty one made
    # the runner cd to the agent profile's own directory. `/tmp` because these fixtures
    # never run anything there, only post; the gate asks for absolute, not for existing.
    r2 = store.apply("post", title="A row that asks to be both", lane="ops", workdir="/tmp",
                     posted_by="operator", actor_type="human", agent_claimable=True)
    check("a row posted human AND agent-claimable is coerced, not accepted",
          row(r2["id"]).get("agent_claimable") is False,
          f"agent_claimable={row(r2['id']).get('agent_claimable')!r}")


# ------------------------------------------------------- 4. fleet work stays off his console

def test_fleet_work_is_not_on_his_queue():
    """The widening must not push the fleet's backlog onto the operator."""
    reset()
    r = store.apply("post", title="A fleet task, posted for agents", lane="engine",
                    posted_by="T9", agent_claimable=True, workdir="/tmp")
    tid = r["id"]
    check("it is the fleet's", row(tid).get("agent_claimable") is True)
    check("the old arm did not show it to him", tid not in matched_by(OLD_ARM))
    check("and the new arm does not either", tid not in matched_by(NEW_ARM))
    check("so it is on no arm of his queue", on_his_queue(tid) == "",
          f"arm={on_his_queue(tid)!r}")


# ------------------------------------------------------- 5. THE PARTITION

def test_every_open_row_is_on_exactly_one_queue():
    """The property 0421 asked for, asserted over a board carrying all four shapes at once.

    Not "the two rows appear": that is one case. This asserts there is no third state -- for
    every work_item in `inbox` or `active`, `on his queue` and `claimable by an agent` are
    complements. A row on neither is the defect this task was posted for; a row on both would be
    the fleet and the operator racing the same work.
    """
    reset()
    shapes = [
        ("his, marked human", dict(lane="ops", posted_by="operator", actor_type="human")),
        ("his, unmarked (0068's shape)", dict(lane="qc", posted_by="operator")),
        ("his, unmarked (0071's shape)", dict(lane="marketing", posted_by="operator")),
        ("the fleet's, posted for agents", dict(lane="engine", posted_by="T9",
                                                agent_claimable=True, workdir="/tmp")),
        ("an agent's, --for-agents forgotten", dict(lane="engine", posted_by="T9")),
    ]
    ids = {}
    for i, (name, kw) in enumerate(shapes):
        if kw.get("actor_type") == "human" and not provisioned():
            check(f"SKIPPED-AS-FAILURE: cannot post {name!r} without an operator login", False)
            continue
        ids[name] = store.apply("post", title=f"partition row {i}: {name}", **kw)["id"]

    with store.read("runtime") as s:
        open_rows = {r["id"]: r for r in s.query(
            "SELECT id, agent_claimable FROM brain.work_item "
            "WHERE state IN ('inbox','active')")}
        his = {r["source_id"] for r in s.query(
            "SELECT source_id FROM brain.queue_open "
            "WHERE source_type = 'work_item' AND primary_verb = 'Mark my task done'")}

    neither = [i for i, r in open_rows.items() if i not in his and not r["agent_claimable"]]
    both = [i for i, r in open_rows.items() if i in his and r["agent_claimable"]]
    check(f"{len(open_rows)} open rows and NONE on neither queue", not neither, str(neither))
    check("and none on both", not both, str(both))
    check("his queue is exactly the not-agent-claimable set",
          his == {i for i, r in open_rows.items() if not r["agent_claimable"]},
          f"his={sorted(his)}")
    expected_his = 4 if provisioned() else 3
    check(f"which is {expected_his} of {len(open_rows)} here: the three of his plus the "
          f"forgotten fleet row", len(his) == expected_his, f"len={len(his)}")

    # The forgotten fleet row lands on HIS queue on purpose, and that is the widening's one
    # behaviour change beyond the two rows. Named here so nobody discovers it as a surprise.
    forgotten = ids.get("an agent's, --for-agents forgotten")
    check("a fleet row posted without --for-agents reaches the operator, which is the point: "
          "no agent will ever take it", forgotten in his, f"{forgotten!r} not in {sorted(his)}")


# ------------------------------------------------------- 6. the complement, stated as SQL

def test_the_arm_is_literally_the_complement_of_claims_predicate():
    """Read the applied view back out of the catalog. A comment claiming this is not evidence."""
    with store.read("runtime") as s:
        ddl = s.scalar("SELECT pg_get_viewdef('brain.queue_open'::regclass, true)")
    body = " ".join((ddl or "").split())
    check("the applied arm 2 predicate is NOT w.agent_claimable",
          "WHERE NOT w.agent_claimable" in body, body[-400:])
    check("and no arm of the applied view reads actor_type any more",
          "actor_type" not in body, body[-400:])

    # The tripwire, on the file that actually carries it. `_AGENT_CLAIMABLE` is interpolated
    # into `claim`'s FOR UPDATE SKIP LOCKED statement; if it stops being `AND w.agent_claimable`
    # this view is no longer its complement and 0421's property is gone silently.
    claim_src = (ROOT / "engine/swarm_engine/transitions.py").read_text()
    check("claim's own predicate is the same column, positive",
          '_AGENT_CLAIMABLE = """\n               AND w.agent_claimable"""' in claim_src,
          "engine/swarm_engine/transitions.py no longer defines _AGENT_CLAIMABLE as "
          "`AND w.agent_claimable` -- if claim's predicate moved, brain.queue_open arm 2 is no "
          "longer its complement and every row that fell between them is invisible again")
    check("and it is interpolated into claim's own locking statement",
          "{_NOT_HELD_BY_A_LIVE_AGENT}{_AGENT_CLAIMABLE}" in claim_src,
          "_AGENT_CLAIMABLE is defined but no longer reaches claim's WHERE clause")


if __name__ == "__main__":
    print(f"queue_open is the complement of claim  ({os.environ['BRAIN_PG_DB']})")
    print("\n-- the row that was on no surface")
    test_the_unclassified_operator_row_reaches_his_console()
    print("\n-- and the console will let him finish it")
    test_the_console_permits_done_on_it()
    print("\n-- and the fleet still cannot take it")
    test_the_fleet_still_cannot_take_it()
    print("\n-- the rows the old arm showed him are all still there")
    test_a_human_marked_row_is_unaffected()
    print("\n-- fleet work stays off his console")
    test_fleet_work_is_not_on_his_queue()
    print("\n-- THE PARTITION: no row on neither queue, no row on both")
    test_every_open_row_is_on_exactly_one_queue()
    print("\n-- the complement, read back out of the catalog")
    test_the_arm_is_literally_the_complement_of_claims_predicate()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
