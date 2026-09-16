#!/usr/bin/env python3
"""AN OPERATOR `done` IS NEVER FILED OVER AN AGENT'S REPORT. Bus row 0399, wave 3 lane J.

WHAT WAS OPEN, and it took two clicks and no crafted request. `web/rooms.py::assert_allowed_on`
permitted `done` where `NOT agent_claimable AND coalesce(claimed_by,'') = ''`, and both of those
are statements about a row's FUTURE:

  * `agent_claimable` says whether the fleet may TAKE it. `swarm set <id> agent_claimable false`
    is a registered act meaning "I am taking this back from the fleet" and says nothing about who
    has already worked it. `transitions.py::set_field` names the incident in its own comment: on
    2026-08-18 the admiral lowered that flag on 0103 sixty seconds after T5 claimed it.
  * `claimed_by` says whether an agent is holding it AT THIS INSTANT, and `reopen` clears it,
    deliberately and correctly, as part of sending work back.

So: press `Send back` on an agent's finished work item in the Judge tier. The server runs
`reopen`. The card stays on screen -- it is still open work, and with `agent_claimable` false
`brain.queue_open` hands it back on the OPERATOR'S arm -- and its primary control has silently
changed from `Accept work` to `Mark my task done`. Press that, type a summary, and
`brain.work_item.result`, the column every later surface reads as WHAT THE AGENT REPORTED, holds
the operator's sentence instead. The row leaves the queue as done and the rework he asked for two
seconds earlier never happens.

Reproduced in a real browser 2 of 2 at commit 6b5fa01, on the repo's own seeded rows 0001 and
0002, at ledger 41 and again at ledger 33. `outputs/2026-08-27-J-0399/`.

THIS SUITE IS THAT PATH, WITH NO BROWSER: `actions.send_back` then `actions.mark_my_task_done`,
which are the two functions the write door calls, so the sequence under test is the operator's and
not a hand-built dict. Scene 1 IS lane H's two clicks.

THE PREDICATE. "Has an agent already worked this row", three arms, in `rooms.agent_work_on`:
    1. a `brain.run` row exists
    2. a `claim` on the append-only `brain.thread`
    3. a `done`/`fail`/`block` on the thread filed by a name that is neither empty nor the human
       this console is acting as
Arms 1 and 2 are identity-free and are ALSO enforced by the database, in
`migrations/0042_done_is_never_filed_over_an_agents_report.sql`, because `web/rooms.py` is not on
every path: `swarm done <id> --agent operator` reaches the same forgery with none of it (measured,
scene 7). Arm 3 needs to know who is asking, which a trigger does not, so it stays here. Scene 8
asserts the two implementations agree on the arms they share, over every row in the table, so they
cannot drift into two policies with the weaker one winning.

Run:  QUEUE_SCRATCH_DB=brain_scratch_j0399 BRAIN_PG_DB=brain_scratch_j0399 \
      python3 -m web.tests.test_done_is_not_a_forgery
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
os.environ.pop("SWARM_PARENT_TASK", None)

if os.environ["BRAIN_PG_DB"] in ("brain", "brain_scratch"):
    sys.exit("test_done_is_not_a_forgery: this suite TRUNCATEs. It never runs against "
             f"{os.environ['BRAIN_PG_DB']!r}.")

sys.path.insert(0, str(ROOT / "queue/tests"))
from _scratch_preflight import reconcile                                # noqa: E402
reconcile(os.environ["BRAIN_PG_DB"])

import store                                                            # noqa: E402
from swarm_engine import transitions as engine                          # noqa: E402,F401
from web import actions as web_actions                                  # noqa: E402
from web import model as web_model, rooms as web_rooms                  # noqa: E402

SCRATCH = str(ROOT / "queue/bin/queue-scratch-db.sh")
OPERATOR = "operator"
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
    except Exception as e:                                              # noqa: BLE001
        return str(e) or e.__class__.__name__


def su(sql: str) -> str:
    r = subprocess.run([SCRATCH, "psql", "-tAq", "-c", sql], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or f"psql exited {r.returncode}")
    return r.stdout.strip()


def reset():
    su("TRUNCATE brain.queue_item, brain.queue_defer, brain.queue_bump, brain.queue_calibration, "
       "brain.queue_default_event, brain.recommendation, brain.thread, brain.question, "
       "brain.run, brain.agent, brain.work_item CASCADE; "
       "SELECT setval('brain.item_id_seq', 1, false);")


def row(tid: str) -> dict:
    with store.read("runtime") as s:
        return s.one("SELECT * FROM brain.work_item WHERE id = %s", (tid,)) or {}


def ledger() -> int:
    with store.read("runtime") as s:
        return int(s.one("SELECT coalesce(max(version), 0) AS v FROM brain.schema_migration")["v"])


def trigger_present() -> bool:
    with store.read("runtime") as s:
        return bool(s.one("SELECT count(*) AS n FROM pg_trigger "
                          "WHERE tgname = 'work_item_done_is_not_a_forgery'")["n"])


def an_agents_finished_work(agent="T6", *, with_a_run=True) -> str:
    """A row an agent claimed, ran and reported, then taken back from the fleet.

    Every step is a registered verb. `set agent_claimable false` is how the seven exposed rows on
    live `brain` came to carry that value over completed agent work.
    """
    tid = store.apply("post", title="Recheck the restatement against the export", lane="qc",
                      posted_by="commander", agent_claimable=True, workdir="/tmp")["id"]
    store.apply("claim", agent=agent, lanes=["*"], host="test")
    if with_a_run:
        store.apply("run start", id=tid, attempt=1, agent=agent, host="test", session_id="s-j")
    store.apply("done", id=tid, agent=agent,
                summary="- round 2 re-walked every evidence path: 8 checked, 1 missing")
    if with_a_run:
        store.apply("run end", id=tid, attempt=1, exit_code=0, outcome="done")
    store.apply("set", id=tid, key="agent_claimable", value="false", agent="operator")
    return tid


def his_own_row(title="Send the QC result to the client") -> str:
    return store.apply("post", title=title, lane="ops", posted_by=OPERATOR,
                       actor_type="human")["id"]


# ------------------------------------------------------- 1. lane H's two clicks, as filed

def test_send_back_then_mark_my_task_done_is_refused():
    """THE REPRODUCTION. Two calls, in the order the two buttons sit on the card.

    `send_back` dispatches `reopen`, which empties `claimed_by`. `mark_my_task_done` dispatches
    `done`. Before row 0399 both went through and `work_item.result` came out holding the
    operator's sentence.
    """
    reset()
    tid = an_agents_finished_work()
    before = row(tid)
    check("the fixture is an agent's finished report",
          before["state"] == "done" and before["claimed_by"] == "T6"
          and not before["agent_claimable"],
          f"state={before['state']!r} claimed_by={before['claimed_by']!r} "
          f"agent_claimable={before['agent_claimable']!r}")
    report = before["result"]

    card = web_model.find_item(tid)
    check("CLICK 1: the card offers `Send back` before it is pressed",
          card is not None and card.get("reject_verb") == "reopen",
          f"kind={card and card.get('kind')!r}")
    web_actions.send_back("queue", item=card, operator=OPERATOR,
                          reason="Round 1 accepted a path that is not on disk. Re-walk it.")
    after_reopen = row(tid)
    check("reopen cleared claimed_by, which is the term the old guard turned on",
          after_reopen["state"] == "inbox" and after_reopen["claimed_by"] == ""
          and not after_reopen["agent_claimable"])

    card2 = web_model.find_item(tid)
    check("CLICK 2: the card no longer OFFERS `Mark my task done`",
          bool(card2 and card2.get("done_refused")),
          f"kind={card2 and card2.get('kind')!r} done_refused="
          f"{card2 and card2.get('done_refused')!r}")
    check("and the card says WHY, in words, naming the agent",
          "T6" in str(card2 and card2.get("done_refused")),
          str(card2 and card2.get("done_refused"))[:160])

    msg = refused(web_actions.mark_my_task_done, "queue", item=card2, operator=OPERATOR,
                  summary="I looked at it myself and it is fine. Closing it out.")
    check("and the write door refuses it whatever the card renders", msg != "", "PERMITTED")
    check("the refusal names the evidence", "worked by an agent" in msg, msg[:200])
    check("and names the verb that was meant", "accept work" in msg.lower(), msg[:200])

    end = row(tid)
    check("the agent's report is still in work_item.result, byte for byte",
          end["result"] == report, f"{end['result'][:60]!r}")
    check("and the row is still in the queue, waiting to be worked again",
          end["state"] == "inbox", f"state={end['state']!r}")


# ------------------------------------------------------- 2. the other arm, or it is a ban

def test_his_own_task_still_reaches_the_verb():
    """A GUARD THAT REFUSES EVERYTHING IS A BAN. 158 of the 172 exposed rows on live `brain` are
    the operator's own work and `Mark my task done` is CORRECT on every one of them."""
    reset()
    mine = his_own_row()
    card = web_model.find_item(mine)
    check("his own row is on the queue as his", card is not None and card.get("kind") == "human",
          f"kind={card and card.get('kind')!r}")
    check("and nothing is stamped on it", not (card or {}).get("done_refused"),
          str((card or {}).get("done_refused"))[:120])
    out = web_actions.mark_my_task_done("queue", item=card, operator=OPERATOR,
                                        summary="Read it, wrote the note, filed it. 3 of 3.")
    check("the verb runs", out.get("verb") == "done", str(out)[:120])
    w = row(mine)
    check("and HIS summary is what landed in result",
          w["state"] == "done" and w["result"].startswith("Read it, wrote the note"),
          f"{w['state']!r} {w['result'][:50]!r}")


# ------------------------------------------------------- 3. the loop migration 22 protects

def test_his_own_done_reopen_done_loop_survives():
    """done -> reopen -> done AGAIN, on his own row, all three by him.

    This is the case an identity-free version of arm 3 would break, and it is why arm 3 is in
    Python and not in the trigger. Migration 22 wrote the argument at length for `accept`; the
    same sequence has to work for `done`.
    """
    reset()
    mine = his_own_row("A thing I will redo")
    web_actions.mark_my_task_done("queue", item=web_model.find_item(mine), operator=OPERATOR,
                                  summary="First pass: read it, made the note.")
    web_actions.undo_done("queue", item={"id": mine}, operator=OPERATOR)
    check("undo put it back in his queue", row(mine)["state"] == "inbox", row(mine)["state"])
    card = web_model.find_item(mine)
    check("and the card still offers the verb after HIS OWN done and reopen",
          card is not None and not card.get("done_refused"),
          str((card or {}).get("done_refused"))[:140])
    web_actions.mark_my_task_done("queue", item=card, operator=OPERATOR,
                                  summary="Second pass, and this one is right.")
    check("the second done lands", row(mine)["result"].startswith("Second pass"),
          row(mine)["result"][:50])


# ------------------------------------------------------- 4. the third arm, which the trigger has not

def test_a_row_another_party_reported_on_is_refused_with_no_run_and_no_claim():
    """ARM 3. A row worked by something that never went through the runner.

    Live `brain` carries seven of these: 0371-0375, 0379 and 0382, this program's own lane
    subagents, reported by `lane-B`, with zero `brain.run` rows and zero claims. `brain.agent`
    does not know that name either, which is why "the reporter is a registered agent" was
    rejected as the predicate: it sees 4 of the 172 where this sees 11.
    """
    reset()
    tid = store.apply("post", title="A row a subagent worked without the runner", lane="web",
                      posted_by=OPERATOR)["id"]
    store.apply("done", id=tid, agent="lane-B", summary="lane-B's report, filed by lane-B.")
    store.apply("reopen", id=tid, reason="not what I asked for", agent=OPERATOR)
    w = row(tid)
    check("no run and no claim on it",
          not w["claimed_by"] and not w["agent_claimable"] and w["state"] == "inbox")
    with store.read("runtime") as s:
        n = s.one("SELECT (SELECT count(*) FROM brain.run WHERE work_item_id=%s) AS runs, "
                  "(SELECT count(*) FROM brain.thread WHERE work_item_id=%s AND kind='claim') "
                  "AS claims", (tid, tid))
    check("measured: 0 runs, 0 claims, so arms 1 and 2 see nothing",
          n["runs"] == 0 and n["claims"] == 0, str(n))
    msg = refused(web_rooms.assert_allowed_on, "queue", "done", {"id": tid}, actor=OPERATOR)
    check("arm 3 refuses it anyway: somebody else already reported on this row", msg != "",
          "PERMITTED")
    check("and names the reporter", "lane-B" in msg, msg[:180])


# ------------------------------------------------------- 5. the agent's own report, and acceptance

def test_the_agent_reporting_its_own_work_is_never_refused():
    """The exemption, read off the row: only the HOLDER of an `active` row reaches `_finish`."""
    reset()
    tid = store.apply("post", title="Fleet work an agent will report on", lane="engine",
                      posted_by="T9", agent_claimable=True, workdir="/tmp")["id"]
    store.apply("claim", agent="T-0399", lanes=["*"], host="test")
    check("the agent holds it", row(tid)["state"] == "active", row(tid)["state"])
    out = store.apply("done", id=tid, agent="T-0399", summary="T-0399's own report.")
    check("its own `done` goes through", out.get("state") == "done", str(out)[:120])
    check("and its words are in result", row(tid)["result"] == "T-0399's own report.",
          row(tid)["result"][:60])


def test_accept_work_on_an_agents_row_is_never_refused():
    """THE WORST AVAILABLE FALSE BAN: the operator unable to accept an agent's work BECAUSE an
    agent did it. `accept work` writes accepted_by and accepted_at and touches neither `state`
    nor `result`, which is what the trigger's third test is for."""
    reset()
    tid = an_agents_finished_work(agent="T7")
    msg = refused(store.apply, "accept work", id=tid, by=OPERATOR, as_operator=True)
    check("`accept work` is accepted", msg == "", msg[:220])
    w = row(tid)
    check("and the acceptance is recorded", w.get("accepted_by") == OPERATOR
          and w.get("accepted_at") is not None,
          f"accepted_by={w.get('accepted_by')!r}")


# ------------------------------------------------------- 6. a row nobody has worked YET

def test_the_two_older_terms_still_refuse_what_they_refused():
    """0399 ADDS A TERM, IT DOES NOT REPLACE TWO. A row the fleet may still take, and a row an
    agent is holding, are both refused before any evidence is read: they are the FUTURE tense of
    the same question and neither is subsumed by the past tense."""
    reset()
    claimable = store.apply("post", title="Fleet work nobody has touched yet", lane="engine",
                            posted_by="T9", agent_claimable=True, workdir="/tmp")["id"]
    msg = refused(web_rooms.assert_allowed_on, "queue", "done", {"id": claimable})
    check("a claimable row with no history at all is still refused", msg != "", "PERMITTED")
    check("and the refusal is the agent_claimable one", "the fleet may claim it" in msg, msg[:160])

    # RESET FIRST, AND THE CLAIM IS PINNED TO THE ROW IT MUST TAKE. `claim` hands out the next
    # claimable row, not a named one, and the row posted above is still claimable -- so without
    # this the claim below took THAT row and this scene asserted a rule about a row nobody held.
    # It read as a regression in the guard and was a defect in the fixture, which is the exact
    # fragility `web/tests/run-all.sh` already filed against `test_runfeed_browser.py`.
    reset()
    held = store.apply("post", title="Fleet work an agent is holding", lane="engine",
                       posted_by="T9", agent_claimable=True, workdir="/tmp")["id"]
    got = store.apply("claim", agent="T-holder", lanes=["*"], host="test")
    check("the claim took the row this scene is about", (got or {}).get("id") == held,
          f"claim returned {(got or {}).get('id')!r}, wanted {held!r}")
    su(f"UPDATE brain.work_item SET agent_claimable = false WHERE id = '{held}'")
    msg = refused(web_rooms.assert_allowed_on, "queue", "done", {"id": held})
    check("a row an agent is holding is still refused (task 0427's residual)", msg != "",
          "PERMITTED")
    check("and the refusal names the holder", "T-holder" in msg, msg[:160])


# ------------------------------------------------------- 7. the database, for every caller

def test_the_database_refuses_it_too_with_no_console_in_the_path():
    """`web/rooms.py` IS NOT ON EVERY PATH, and this is the measurement that says so.

    `swarm done <id> --agent operator` on the same reopened row reaches `store.apply` directly.
    `_hold` does not refuse it: it gates `active` rows only, and `reopen` has just moved the row
    to `inbox`. Task 0377's lesson is that a Python check the database does not back holds only
    for polite callers, so migration 42 puts the floor in a trigger.
    """
    reset()
    tid = an_agents_finished_work(agent="T8")
    store.apply("reopen", id=tid, reason="re-walk it", agent=OPERATOR)
    report = row(tid)["result"]
    msg = refused(store.apply, "done", id=tid, agent=OPERATOR,
                  summary="CLI: the operator files T8's report for it.")
    if trigger_present():
        check(f"the trigger refuses it at ledger {ledger()}, with no rooms.py in the path",
              msg != "", "PERMITTED")
        check("and the refusal names the row and the evidence",
              tid in msg and "worked by an agent" in msg, msg[:220])
        check("the report survived", row(tid)["result"] == report, row(tid)["result"][:50])
    else:
        # A LEDGER THAT RECORDS 42 AND A DATABASE WITH NO TRIGGER IS A RED, NOT A SHRUG. Every
        # migration writes ON CONFLICT (version) DO NOTHING, so once the row is there `migrate`
        # will never put the trigger back and the store reports a clean build for a floor that
        # is not under it. Below 42 there is genuinely nothing to measure and the console guard
        # is the whole fix, which is the state live `brain` is in today at ledger 33.
        at_42 = ledger() >= 42
        check(f"the trigger is absent and the ledger records {ledger()}: below 42, so this is "
              f"NOT MEASURED rather than red. The console guard is what closes it on this "
              f"store, and scene 1 proved that", not at_42,
              f"ledger {ledger()} records migration 42 and the trigger is GONE. Re-apply "
              f"migrations/0042_done_is_never_filed_over_an_agents_report.sql by hand: "
              f"`migrate` will not, because the ledger row is already there.")
        check("and the CLI forgery it would have refused is open on this store",
              msg == "" or "worked by an agent" not in msg,
              "something else refused it; read the message: " + msg[:200])


# ------------------------------------------------------- 8. two implementations, one policy

def test_the_python_predicate_and_the_database_function_do_not_drift():
    """Arms 1 and 2 are written twice: once in `rooms._AGENT_WORK`, once in
    `brain.work_item_agent_has_worked`. Migration 20's warning about two notions of who is human
    applies here word for word -- the two drift and the weaker one becomes the real policy -- so
    this compares them over EVERY row in the table rather than over an example.

    Arm 3 is deliberately Python-only and is excluded from the comparison by construction: the
    acting name is passed as the reporter, so no row can match it.
    """
    reset()
    ids = [an_agents_finished_work(agent="T6"), his_own_row(), his_own_row("another of his")]
    tid = store.apply("post", title="Fleet work an agent is holding", lane="engine",
                      posted_by="T9", agent_claimable=True, workdir="/tmp")["id"]
    store.apply("claim", agent="T-drift", lanes=["*"], host="test")
    ids.append(tid)
    if not trigger_present():
        check(f"the database half is absent and the ledger records {ledger()}: below 42, so "
              f"there is nothing to compare and this is NOT MEASURED rather than red",
              ledger() < 42,
              f"ledger {ledger()} records migration 42 and brain.work_item_agent_has_worked is "
              f"GONE. Re-apply the migration file by hand.")
        return
    with store.read("runtime") as s:
        db = {str(r["id"]): bool(r["worked"]) for r in s.query(
            "SELECT id, brain.work_item_agent_has_worked(id) AS worked FROM brain.work_item")}
    py = web_rooms.agent_work_on(list(db), acting_as="nobody-is-called-this")
    disagree = [i for i in db
                if bool(py[i]["runs"] or py[i]["claims"]) != db[i]]
    check(f"the two implementations agree on {len(db)} of {len(db)} rows in the table",
          not disagree, f"they disagree on {disagree}")
    check("and they are not both trivially false: the fixture has agent-worked rows",
          any(db.values()), f"{sum(db.values())} of {len(db)} read as worked")


if __name__ == "__main__":
    print(f"an operator `done` is never filed over an agent's report -- row 0399  "
          f"({os.environ['BRAIN_PG_DB']}, ledger {ledger()}, "
          f"trigger {'present' if trigger_present() else 'ABSENT'})")
    print("\n-- lane H's two clicks: Send back, then the verb the card offered next")
    test_send_back_then_mark_my_task_done_is_refused()
    print("\n-- and his own task still reaches the verb, or this is a ban")
    test_his_own_task_still_reaches_the_verb()
    print("\n-- his own done -> reopen -> done loop survives")
    test_his_own_done_reopen_done_loop_survives()
    print("\n-- arm 3: a row somebody else reported on, with no run and no claim")
    test_a_row_another_party_reported_on_is_refused_with_no_run_and_no_claim()
    print("\n-- the agent reporting its own work is never refused")
    test_the_agent_reporting_its_own_work_is_never_refused()
    print("\n-- accept work on an agent's row is never refused")
    test_accept_work_on_an_agents_row_is_never_refused()
    print("\n-- the two older terms still refuse what they refused")
    test_the_two_older_terms_still_refuse_what_they_refused()
    print("\n-- the database refuses it too, with no console in the path")
    test_the_database_refuses_it_too_with_no_console_in_the_path()
    print("\n-- the Python predicate and the database function do not drift")
    test_the_python_predicate_and_the_database_function_do_not_drift()
    print(f"\n{PASS} passed, {FAIL} failed  ({PASS + FAIL} assertions)")
    if PASS + FAIL == 0:                                                # DENOMINATOR
        print("0 comparisons made. A verdict over an empty set is not a pass, and this suite "
              "over an empty set would report row 0399 closed having tested nothing.")
        sys.exit(2)
    sys.exit(1 if FAIL else 0)
