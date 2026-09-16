#!/usr/bin/env python3
"""THE CONSOLE'S `done` GUARD IS A CHECK ON THE ROW, NOT A RESTATEMENT OF THE ARM. Task 0427.

`web/rooms.py::assert_allowed_on` is the guard the `sprint-swarm-app.md` prohibition is actually
about: an operator `done` on an agent's work item fabricates THAT AGENT'S report, and no
allowlist entry can separate the two cases because both are the verb `done`.

WHAT IT WAS. Until this task it read `item['actor_type']` and permitted `done` where that was
`'human'`. `item` is not a `brain.work_item` row -- it is the console CARD built by
`web/model.py::_card`, and the card's `actor_type` was stamped there BY THE CARD:
`KIND_BY_ARM[('work_item', 'Mark my task done')] = 'human'`, then `card.update(_VERBS[kind])`,
then `_VERBS['human']['actor_type'] = 'human'`. The value the guard tested was therefore decided
by WHICH ARM OF `brain.queue_open` THE ROW ARRIVED ON, and `brain.work_item.actor_type` was never
read on this path at all. Since `queue/schema/0013` that arm is `NOT w.agent_claimable`, so the
guard was that predicate restated in Python: it could refuse nothing the arm admitted and admit
nothing the arm refused. Two guards on paper, one predicate in fact.

WHY THE SUITES DID NOT CATCH IT, which is the more useful half. Every test that exercised the
guard HAND-BUILT the item dict -- `{"id": tid, "actor_type": w.get("actor_type")}`, read off the
ROW -- and that dict is not the one the console passes. They proved a property the console did
not have, and all of them were green throughout. So the checks below go through
`model.find_item`, the same call `web/app.py` makes, and the one test here that builds a dict by
hand builds a LIE and asserts it is refused.

WHAT IT IS NOW:

    permit `done` when   NOT agent_claimable   AND   no agent holds the row

read from `brain.work_item` by `rooms._row_behind`. Test 5 is the one that makes "reads the row"
a measurement rather than a claim: it holds ONE card object fixed, changes the ROW underneath it,
and shows the answer change.

Run:  QUEUE_SCRATCH_DB=brain_scratch_t4_0427 BRAIN_PG_DB=brain_scratch_t4_0427 \
      python3 web/tests/test_done_reads_the_row.py
      (against a scratch database, never `brain`)
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _p in ("queue", "engine"):
    sys.path.insert(0, str(ROOT / _p))
sys.path.insert(0, str(ROOT))

os.environ.setdefault("BRAIN_PG_DB", os.environ.get("QUEUE_SCRATCH_DB", "brain_queue_scratch"))
os.environ.pop("SWARM_PARENT_TASK", None)      # the live runner exports this; tests post roots

sys.path.insert(0, str(ROOT / "queue/tests"))
from _scratch_preflight import reconcile                              # noqa: E402
reconcile(os.environ["BRAIN_PG_DB"])

import store                                                          # noqa: E402
from swarm_engine import transitions as engine                        # noqa: E402,F401
from web import model as web_model, rooms as web_rooms                # noqa: E402

SCRATCH = str(ROOT / "queue/bin/queue-scratch-db.sh")
PASS, FAIL = 0, 0


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
    except Exception as e:                                            # noqa: BLE001
        return str(e) or e.__class__.__name__


def su(sql: str) -> str:
    """Superuser psql, FOR SETUP ONLY -- never to prove a rule.

    One shape needs it: `agent_claimable` LOWERED on a row an agent already holds. Migration 26
    permits that to everybody, deliberately, as the safe direction of taking work back from the
    fleet, and no verb spells it today. This manufactures the state, never the verdict.
    """
    r = subprocess.run([SCRATCH, "psql", "-tAq", "-c", sql], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or f"psql exited {r.returncode}")
    return r.stdout.strip()


def reset():
    su("TRUNCATE brain.queue_item, brain.queue_defer, brain.queue_bump, brain.queue_calibration, "
       "brain.queue_default_event, brain.recommendation, brain.thread, brain.question, "
       "brain.agent, brain.work_item CASCADE; SELECT setval('brain.item_id_seq', 1, false);")


def row(tid: str) -> dict:
    with store.read("runtime") as s:
        return s.one("SELECT * FROM brain.work_item WHERE id = %s", (tid,)) or {}


def guard(card, **kw) -> str:
    return refused(web_rooms.assert_allowed_on, "queue", "done", card, **kw)


# --------------------------------------------------- 1. the residual 0013 could not close

def test_a_row_an_agent_holds_is_refused_even_on_his_own_queue():
    """THE CASE THE OLD GUARD PERMITTED, and the reason this task exists.

    A fleet row an agent has claimed, whose `agent_claimable` is then lowered to false: it lands
    on `queue_open` arm 2 with a live agent parked on it, and the console renders it as the
    operator's own task. Measured on 2026-08-18 against the guard as it stood: PERMITTED.

    The forgery did not land on the day, and where it was stopped is worth knowing because it is
    not this file: `engine/swarm_engine/transitions.py::_hold` refuses a non-holder on an
    `active` row, and `web/actions.py` passes `agent=<operator>`. So the console permitted a
    write the store then rejected -- the inversion of `web/rooms.py`'s own rule that a room's
    writes are refused before `store.apply` is reached. Both layers refuse it now, and this one
    refuses it first and in words.
    """
    reset()
    r = store.apply("post", title="A fleet task an agent claimed, then taken back",
                    lane="engine", posted_by="T9", agent_claimable=True, workdir="/tmp")
    tid = r["id"]
    store.apply("claim", agent="T-0427-holder", lanes=["*"], host="test")
    w = row(tid)
    check("the agent holds it", w.get("state") == "active"
          and (w.get("claimed_by") or "") == "T-0427-holder",
          f"state={w.get('state')!r} claimed_by={w.get('claimed_by')!r}")

    su(f"UPDATE brain.work_item SET agent_claimable = false WHERE id = '{tid}'")
    w = row(tid)
    check("taken back from the fleet while the agent is still on it",
          w.get("agent_claimable") is False and (w.get("claimed_by") or "") == "T-0427-holder")

    card = web_model.find_item(tid)
    check("and the console DOES render it as his own task -- the card is not the defect",
          bool(card) and card.get("kind") == "human" and card.get("label") == "Mark my task done",
          f"kind={(card or {}).get('kind')!r}")
    msg = guard(card)
    check("THE GUARD REFUSES IT. This is the repair", msg != "",
          "PERMITTED -- an operator `done` here fabricates T-0427-holder's report")
    check("naming the holder whose report it would fabricate", "T-0427-holder" in msg, msg[:200])
    check("and naming the column it read rather than the one the card carried",
          "claimed_by=" in msg and "agent_claimable=" in msg, msg[:200])
    return tid


# --------------------------------------------------- 2. and his own work still goes through

def test_his_own_row_is_permitted_and_done_lands():
    """0068's live shape: his row, `actor_type` NULL because the human mark was missed.

    The guard must not have become a ban. This is the row `queue/schema/0013` was written for,
    and `actor_type` -- the column the old guard turned on -- is NULL on it.
    """
    reset()
    r = store.apply("post", title="Double-check the exported QC data against the warehouse",
                    lane="qc", posted_by="operator",
                    body="0068's shape: posted in a batch where the human mark was missed")
    tid = r["id"]
    w = row(tid)
    check("actor_type is NULL, which is what the old guard would have been asked about",
          w.get("actor_type") is None, f"actor_type={w.get('actor_type')!r}")
    card = web_model.find_item(tid)
    check("the console renders it as his own task", (card or {}).get("kind") == "human")
    check("and the card carries NO actor_type at all any more -- the stamp is gone",
          "actor_type" not in (card or {}),
          f"card still stamps actor_type={(card or {}).get('actor_type')!r}")
    check("the guard PERMITS `done` on it", guard(card) == "", guard(card))

    res = web_rooms.dispatch("queue", "done", item=card, actor="operator", id=tid,
                             summary="checked against the warehouse, two rows differ",
                             agent="operator")
    check("and it lands through the write door, not just past the guard",
          row(tid).get("state") == "done", f"state={row(tid).get('state')!r} res={res}")


# --------------------------------------------------- 3. an agent's finished work

def test_an_agents_finished_work_is_refused():
    """Arm 1's rows carry `Accept work`, and `accept work` is the verb for them.

    A `done` here would overwrite the agent's own report in `work_item.result` with the
    operator's sentence. The card would not offer it; a crafted POST would.
    """
    reset()
    store.apply("post", title="An agent task that will be reported done", lane="engine",
                posted_by="T9", agent_claimable=True, workdir="/tmp")
    got = store.apply("claim", agent="T-0427-worker", lanes=["*"], host="test")
    tid = got["id"]
    store.apply("done", id=tid, summary="the agent's own report", agent="T-0427-worker")
    card = web_model.find_item(tid)
    check("it is on his queue as work to ACCEPT, not to finish",
          (card or {}).get("kind") == "review" and card.get("verb") == "accept work",
          f"kind={(card or {}).get('kind')!r}")
    check("the guard refuses `done` on it", guard(card) != "", "PERMITTED")
    check("and refuses it just the same when a crafted request claims actor_type human",
          guard({"id": tid, "actor_type": "human"}) != "", "PERMITTED ON A HAND-BUILT DICT")


# --------------------------------------------------- 4. a row the fleet may still take

def test_a_claimable_row_is_refused_however_the_request_is_dressed():
    """No card exists for it, so the only way here is a crafted POST. It must not work."""
    reset()
    r = store.apply("post", title="A fleet task, posted for agents", lane="engine",
                    posted_by="T9", agent_claimable=True, workdir="/tmp")
    tid = r["id"]
    check("the console shows him no card for it", web_model.find_item(tid) is None)
    msg = guard({"id": tid, "actor_type": "human", "kind": "human"})
    check("and the guard refuses it anyway", msg != "", "PERMITTED")
    check("because it read agent_claimable off the row", "agent_claimable=True" in msg, msg[:200])


# --------------------------------------------------- 5. the row decides, not the card

def test_the_same_card_gets_a_different_answer_when_the_row_changes():
    """THE MEASUREMENT THAT MAKES "READS THE ROW" A FACT RATHER THAN A DESCRIPTION.

    One card object, built once by `model.find_item` and never rebuilt. The row underneath it is
    changed. If the guard were still deciding from the card the answer could not move, and that
    is exactly what the old one did: `queue/tests/test_queue_open_is_the_complement.py` pushed
    live 0068's shape through this same path and the guard permitted it for a reason that had
    nothing to do with the row.
    """
    reset()
    r = store.apply("post", title="His row, until an agent is put on it", lane="ops",
                    posted_by="operator")
    tid = r["id"]
    card = web_model.find_item(tid)
    check("card in hand, guard permits", guard(card) == "", guard(card))

    su(f"UPDATE brain.work_item SET claimed_by = 'T-0427-late', state = 'active' "
       f"WHERE id = '{tid}'")
    msg = guard(card)
    check("THE SAME CARD OBJECT is now refused, because the row moved", msg != "",
          "PERMITTED -- the guard is still deciding from the card")
    check("and the refusal names the agent that appeared under it", "T-0427-late" in msg,
          msg[:200])

    su(f"UPDATE brain.work_item SET claimed_by = '', state = 'inbox' WHERE id = '{tid}'")
    check("and permitted again when the row is released, same card throughout",
          guard(card) == "", guard(card))


# --------------------------------------------------- 6. the guard and the verb, one row

def test_the_guard_checks_the_row_the_verb_will_act_on():
    """`dispatch` is handed the card and the verb's kwargs separately, so the ids can differ."""
    reset()
    mine = store.apply("post", title="His own row, the one on screen", lane="ops",
                       posted_by="operator")["id"]
    theirs = store.apply("post", title="A fleet row, the one a crafted POST would name",
                         lane="engine", posted_by="T9", agent_claimable=True,
                         workdir="/tmp")["id"]
    card = web_model.find_item(mine)
    msg = guard(card, acting_on=theirs)
    check("a card for one row and a verb aimed at another is refused", msg != "", "PERMITTED")
    check("and the refusal names both ids", mine in msg and theirs in msg, msg[:200])
    check("a `done` naming a row that does not exist is refused",
          guard({"id": "4242", "actor_type": "human"}) != "", "PERMITTED")
    check("and one naming no row at all is refused", guard(None) != "", "PERMITTED")


# --------------------------------------------------- 7. the stamp does not come back

def test_the_card_no_longer_stamps_a_column_it_did_not_measure():
    """A tripwire on `_VERBS`, because the defect was one dictionary entry and could return.

    A card that carries a column name it never read is how the next guard gets written against
    the card again. `_card` may say what a row IS on the surface -- kind, label, verb -- and may
    not report a database column it did not read.
    """
    for kind, spec in web_model._VERBS.items():
        check(f"_VERBS[{kind!r}] stamps no actor_type", "actor_type" not in spec,
              f"{kind} still stamps actor_type={spec.get('actor_type')!r}")
    src = (ROOT / "web/rooms.py").read_text()
    check("and web/rooms.py reads no actor_type off the item", "item.get('actor_type')" not in src
          and 'item.get("actor_type")' not in src,
          "the guard is reading the card's actor_type again")
    check("it reads brain.work_item instead", "FROM brain.work_item WHERE id = %s" in src,
          "rooms.py no longer reads the row")


if __name__ == "__main__":
    print(f"the console's `done` guard reads the row  ({os.environ['BRAIN_PG_DB']})")
    print("\n-- the residual queue/schema/0013 named: an agent holds it")
    test_a_row_an_agent_holds_is_refused_even_on_his_own_queue()
    print("\n-- and his own work still goes through")
    test_his_own_row_is_permitted_and_done_lands()
    print("\n-- an agent's finished work is `accept work`, not `done`")
    test_an_agents_finished_work_is_refused()
    print("\n-- a row the fleet may still take")
    test_a_claimable_row_is_refused_however_the_request_is_dressed()
    print("\n-- THE ROW DECIDES: one card, three answers")
    test_the_same_card_gets_a_different_answer_when_the_row_changes()
    print("\n-- the guard and the verb are about one row")
    test_the_guard_checks_the_row_the_verb_will_act_on()
    print("\n-- the stamp does not come back")
    test_the_card_no_longer_stamps_a_column_it_did_not_measure()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
