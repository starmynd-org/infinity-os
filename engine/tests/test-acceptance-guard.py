#!/usr/bin/env python3
"""Migration 22: the column behind the `accept work` door. Task 0260, child of the D9 re-run.

`accept work` guards WHO may accept and it guards it well. `brain.work_item.accepted_by` had no
guard at all, so every refusal the verb makes was reachable by going around it -- and going
around it needs nothing exotic. It needs `brain_runtime`, which is the credential every agent
process already holds in order to call `store.apply` at all.

**Every check in this file uses that credential, over a direct psycopg2 connection.** Not
`store.read()`, which Postgres puts in READ ONLY; not `store.apply`, which is the door being
bypassed. A test that drove the verb would have passed against the code before migration 22,
because the verb was never the defect.

Measured on scratch `brain_t4_0260` at 2026-08-17T10:43Z, BEFORE the migration, all four at
`rowcount=1`:

    UPDATE brain.work_item SET accepted_by='T2'                    a human's acceptance
                                                                   reattributed to an agent
    UPDATE brain.work_item SET accepted_by='operator',
           accepted_at=now() WHERE id='0002'                       forged onto a BLOCKED task
    UPDATE brain.work_item SET accepted_by='', accepted_at=NULL    erased
    INSERT INTO brain.work_item (... accepted_by, accepted_at)     a work item BORN accepted

The fourth was not in the report and was found while reproducing it.

THE LEGITIMATE PATHS MATTER AS MUCH AS THE REFUSALS and half of this file is about them. A guard
that also breaks `accept work`, the auto-accept rule's own write, or the accept -> reopen ->
done -> accept loop is not a fix, and the third of those is the one a strict write-once rule
would have quietly taken away: it is two real human decisions, and D00 rule 3 says narrowing WHO
accepts never narrows WHETHER it was recorded.

Run: python3 engine/tests/test-acceptance-guard.py   (a scratch database, never `brain`)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT))

os.environ.setdefault("BRAIN_PG_DB", os.environ.get("ENGINE_SCRATCH_DB", "brain_scratch"))

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _scratch_preflight import reconcile              # noqa: E402
reconcile(os.environ["BRAIN_PG_DB"])

# The runner exports both of these into every terminal and `post` reads SWARM_PARENT_TASK as a
# parent id, which does not exist in a scratch database. Popped so this suite tests the store and
# not the runner's environment.
os.environ.pop("SWARM_PARENT_TASK", None)
os.environ.pop("SWARM_AGENT", None)

import psycopg2                                       # noqa: E402
import store                                          # noqa: E402
import store.session as session                       # noqa: E402
from swarm_engine import accept, transitions          # noqa: E402,F401  registers the verbs

SCRATCH = str(ROOT / "engine/bin/scratch-db.sh")

PASS, FAIL, INCONC = 0, 0, 0


def inconclusive(msg, why):
    """The rule under test does not exist on THIS store, so nothing was observed. Task 0382.

    Not a pass: a rule you could not have watched break has not been watched holding
    (roles/terminal.md). Not a failure either: a store below the migration never claimed it.
    Counted separately and printed on the banner so it cannot be read as either.
    """
    global INCONC
    INCONC += 1
    print(f"  INCONCLUSIVE  {msg}")
    print(f"                {why}")


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


# ------------------------------------------------------------------ the attacker's connection

def as_runtime(sql, params=None):
    """One statement as `brain_runtime`, committed. Raises whatever Postgres raises.

    This is the whole premise of the file. `store.session.dsn("runtime")` resolves the same
    credential `store.apply` uses, and psycopg2 opens it directly, so nothing in the narrow waist
    is in the path -- no verb, no VerbError, no `_hold`. Whatever refuses below is the table.
    """
    conn = psycopg2.connect(**session.dsn("runtime"))
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            n = cur.rowcount
        conn.commit()
        return n
    finally:
        conn.close()


def refused(msg, sql, params=None, naming=""):
    """Assert the table refuses this statement, and that the refusal says something usable."""
    try:
        n = as_runtime(sql, params)
        bad(msg, f"NOT refused: rowcount={n}. The statement went through.")
    except psycopg2.errors.RaiseException as exc:
        text = str(exc)
        if naming and naming not in text:
            bad(msg, f"refused, but the message does not name {naming!r}: {text.splitlines()[0]}")
        else:
            ok(msg)
    except Exception as exc:                                          # noqa: BLE001
        bad(msg, f"refused by the WRONG thing ({type(exc).__name__}): {exc}")


def allowed(msg, sql, params=None):
    try:
        as_runtime(sql, params)
        ok(msg)
    except Exception as exc:                                          # noqa: BLE001
        bad(msg, f"refused, and this one had to go through: {str(exc).splitlines()[0]}")


# ------------------------------------------------------------------ fixtures

def reset():
    import subprocess
    subprocess.run([SCRATCH, "truncate"], check=True, capture_output=True,
                   env={**os.environ, "ENGINE_SCRATCH_DB": os.environ["BRAIN_PG_DB"]})


def finished_task(title="an item an agent reported done"):
    """Claimed, run, reported done by an agent. NOT accepted: that is the point."""
    tid = store.apply("post", lane="t", title=title, agent_claimable=True, workdir="/tmp",
                      signals={"reversibility": "reversible"})["id"]
    store.apply("claim", agent="T2", lanes=["t"], role="terminal")
    store.apply("done", id=tid, summary="the agent says it finished", agent="T2")
    return tid


def blocked_task(title="an item nobody finished"):
    tid = store.apply("post", lane="t", title=title, agent_claimable=True,
                      workdir="/tmp")["id"]
    store.apply("claim", agent="T2", lanes=["t"], role="terminal")
    store.apply("block", id=tid, reason="parked", agent="T2")
    return tid


def row(tid):
    with store.read("runtime") as s:
        return s.one("SELECT state, accepted_by, accepted_at FROM brain.work_item WHERE id = %s",
                     (tid,))


# ============================================================ 1. the four measured routes

def test_a_humans_acceptance_cannot_be_reattributed_to_an_agent():
    """Route 1. The verb refuses `by='T2'`; the column used to accept it by the back door."""
    reset()
    tid = finished_task()
    store.apply("accept work", id=tid, by="operator", as_operator=True)
    before = row(tid)
    refused("an UPDATE cannot reattribute the operator's acceptance to an agent",
            "UPDATE brain.work_item SET accepted_by='T2' WHERE id=%s", (tid,),
            naming="rewrite")
    eq("and the row is untouched: still the operator", row(tid)["accepted_by"], "operator")
    eq("and accepted_at did not move either", row(tid)["accepted_at"], before["accepted_at"])


def test_an_acceptance_cannot_be_forged_onto_work_nobody_finished():
    """Route 2, the one that fabricates a decision no human ever made."""
    reset()
    tid = blocked_task()
    refused("an acceptance cannot be forged onto a BLOCKED task",
            "UPDATE brain.work_item SET accepted_by='operator', accepted_at=now() WHERE id=%s",
            (tid,), naming="not done")
    eq("the blocked task is still unaccepted", row(tid)["accepted_at"], None)

    # Every non-done state, not just the one that was measured. A guard proven on one value of a
    # column is a guard that may be reading that value and not the rule.
    inbox = store.apply("post", lane="t", title="never claimed", agent_claimable=True,
                        workdir="/tmp")["id"]
    refused("nor onto an item still in the inbox",
            "UPDATE brain.work_item SET accepted_by='operator', accepted_at=now() WHERE id=%s",
            (inbox,), naming="not done")
    store.apply("claim", agent="T2", lanes=["t"], role="terminal")
    refused("nor onto an item an agent is working right now",
            "UPDATE brain.work_item SET accepted_by='operator', accepted_at=now() WHERE id=%s",
            (inbox,), naming="not done")


def test_an_acceptance_cannot_be_erased():
    """Route 3. `web/actions.py` states this rule in prose; now the table holds it."""
    reset()
    tid = finished_task()
    store.apply("accept work", id=tid, by="operator", as_operator=True)
    refused("an acceptance cannot be erased",
            "UPDATE brain.work_item SET accepted_by='', accepted_at=NULL WHERE id=%s", (tid,),
            naming="erase")
    refused("nor half-erased by nulling the timestamp alone",
            "UPDATE brain.work_item SET accepted_at=NULL WHERE id=%s", (tid,))
    refused("nor half-erased by blanking the acceptor alone",
            "UPDATE brain.work_item SET accepted_by='' WHERE id=%s", (tid,))
    got = row(tid)
    eq("the acceptance survived all three", got["accepted_by"], "operator")
    truth("with its timestamp", got["accepted_at"] is not None, str(got))


def test_a_work_item_cannot_be_born_accepted():
    """Route 4, NOT in the report and found while reproducing it.

    A row inserted straight into the table with an acceptance on it: no `post`, no `claim`, no
    `done`, no thread and no run. The other three routes at least leave a real task behind that
    an auditor could go and read.
    """
    reset()
    refused("a work item cannot be INSERTed already accepted",
            "INSERT INTO brain.work_item (id, lane, title, state, accepted_by, accepted_at) "
            "VALUES ('9260','t','forged','done','operator',now())", naming="born accepted")
    refused("nor with only an acceptor on it",
            "INSERT INTO brain.work_item (id, lane, title, state, accepted_by) "
            "VALUES ('9261','t','forged','done','operator')", naming="born accepted")
    refused("nor with only a timestamp on it",
            "INSERT INTO brain.work_item (id, lane, title, state, accepted_at) "
            "VALUES ('9262','t','forged','done',now())", naming="born accepted")
    with store.read("runtime") as s:
        n = s.scalar("SELECT count(*) FROM brain.work_item WHERE id IN ('9260','9261','9262')")
    eq("none of the three rows exists", n, 0)


# ============================================================ 2. the rules the routes imply

def test_half_an_acceptance_is_refused():
    """`by` with no `at`, or `at` with no `by`. One fact, two columns, they move together.

    This is also what catches route 1 in its OTHER shape -- against a row that was never accepted
    -- where there is no prior acceptance to rewrite.
    """
    reset()
    tid = finished_task()
    refused("an acceptor with no timestamp is refused",
            "UPDATE brain.work_item SET accepted_by='operator' WHERE id=%s", (tid,),
            naming="half an acceptance")
    refused("a timestamp with no acceptor is refused",
            "UPDATE brain.work_item SET accepted_at=now() WHERE id=%s", (tid,),
            naming="half an acceptance")
    eq("so the item is still unaccepted", row(tid)["accepted_at"], None)


def test_the_column_refuses_an_agent_acceptor_even_when_both_halves_are_written():
    """The verb's own rule, held a second time by the table. D00 rule 4.

    `claim` and `heartbeat` write every name the fleet works under into `brain.agent`; T2 has
    claimed twice by the time this runs, so the membership test is a real question about the
    acceptor rather than a name blocklist.
    """
    reset()
    tid = finished_task()
    refused("a coherent, done-state acceptance BY AN AGENT is still refused",
            "UPDATE brain.work_item SET accepted_by='T2', accepted_at=now() WHERE id=%s", (tid,),
            naming="fleet agent")
    eq("nothing was recorded", row(tid)["accepted_by"], None)


# ============================================================ 3. the paths that must still work

def test_the_verb_still_accepts_finished_work():
    """The happy path, plus the property migration 36 added. Task 0382.

    This test used to pass `by="andrew"`, which worked ONLY because the acceptance door failed
    open on any name that was not a registered agent. Lane E measured that door on 2026-08-27:
    6 of 6 forged names were accepted into `accepted_by`, `zzz-not-a-person` among them, while
    `brain.recommendation.decided_by` refused 0 of 6 -- the two doors disagreed on 6 of 6 names.

    So renaming the literal is not enough. A test whose only reason for choosing that name was
    the defect has to say what it now covers, or it is a test edited until it passed. The
    forged-name scene below is that: it asserts the NEW rule rather than surviving it.
    """
    reset()
    tid = finished_task()
    res = store.apply("accept work", id=tid, by="operator", as_operator=True)
    eq("`accept work` still records the human", res["accepted_by"], "operator")
    got = row(tid)
    eq("on the row", got["accepted_by"], "operator")
    truth("with a timestamp", got["accepted_at"] is not None, str(got))
    eq("and acceptance still does not change state (D00 rule 3)", got["state"], "done")

    # THE OTHER HALF, and the reason this test is not simply a rename. A name that is not a
    # human login must not reach `accepted_by`. Below migration 36 this scene is INCONCLUSIVE
    # rather than passing or failing, because the rule does not exist there yet and asserting it
    # would be asserting against a store that never claimed it.
    reset()
    tid2 = finished_task()
    ledger = 0
    with store.read("runtime") as s2:
        ledger = int(s2.scalar("SELECT coalesce(max(version), 0) FROM brain.schema_migration")
                     or 0)
    forged = "zzz-not-a-person"
    try:
        store.apply("accept work", id=tid2, by=forged, as_operator=True)
        landed = row(tid2)["accepted_by"]
    except Exception as exc:                                          # noqa: BLE001
        landed = None
        detail = str(exc).splitlines()[0]
    else:
        detail = f"it landed as {landed!r}"
    if ledger < 36:
        inconclusive(f"a forged acceptor is refused (ledger {ledger}, below 36)",
                     f"migration 36 is what closes this door and this store does not have it. "
                     f"The call {detail}. Not a pass and not a failure.")
    else:
        truth(f"a forged acceptor never reaches accepted_by (ledger {ledger}, tried {forged!r})",
              landed != forged, detail)


def test_the_auto_accept_rule_can_still_write_its_own_acceptance():
    """The rule ships DISABLED. Turned on here for one task, because a guard that blocks the
    thing the week's measurement is measuring would be found in production and not in a test.

    `auto-accept` is not a registered agent, the item is `done` by the time
    `measure_in_transaction` runs (it is called from inside `done`, after `_finish` has set the
    state), and both columns are written in one statement. Three rules, satisfied by
    construction rather than by exemption -- there is no special case for this name in the
    trigger.
    """
    reset()
    os.environ["ENGINE_AUTO_ACCEPT"] = "1"
    try:
        tid = store.apply("post", lane="t", title="reversible, not external, not canon",
                          agent_claimable=True, workdir="/tmp",
                          signals={"reversibility": "reversible"})["id"]
        store.apply("claim", agent="T2", lanes=["t"], role="terminal")
        res = store.apply("done", id=tid, summary="finished", agent="T2")
        truth("the rule acted", res["auto_accept"].get("acted") is True, str(res["auto_accept"]))
        eq("and the acceptance landed", row(tid)["accepted_by"], "auto-accept")
    finally:
        os.environ.pop("ENGINE_AUTO_ACCEPT", None)


def test_accept_reopen_done_accept_again_is_permitted():
    """The loop D00 rule 3 is made of, and the one a strict write-once rule would have killed.

    Send-back then re-acceptance is TWO human decisions, not an edit of one. The trigger permits
    the replacement only because `brain.thread` -- append-only, deleted by no verb -- carries a
    `reopen` strictly after the acceptance being replaced. A rogue UPDATE has no such event.

    IF THIS TEST IS FAILING, the test is not the thing that is wrong. Something simplified the
    REWRITE branch of the trigger towards write-once, and this is the standing refusal of that
    change -- read "DO NOT SIMPLIFY THIS TO WRITE-ONCE" at the top of
    migrations/0022_work_item_acceptance_guard.sql, which says what breaks and why it would only
    surface at the console on a night the operator sent work back. Do not relax this test, delete
    it, or narrow its window to get green.
    """
    reset()
    tid = finished_task()
    store.apply("accept work", id=tid, by="operator", as_operator=True)
    first = row(tid)["accepted_at"]
    store.apply("reopen", id=tid, reason="on second read this is not what I asked for",
                agent="operator")
    eq("the send-back requeued it", row(tid)["state"], "inbox")
    truth("and left the first acceptance visible rather than clearing it",
          row(tid)["accepted_at"] == first, str(row(tid)))
    store.apply("claim", agent="T2", lanes=["t"], role="terminal")
    store.apply("done", id=tid, summary="reworked", agent="T2")
    try:
        store.apply("accept work", id=tid, by="operator", as_operator=True)
        ok("the operator can accept it a second time")
    except Exception as exc:                                          # noqa: BLE001
        bad("the operator can accept it a second time", str(exc).splitlines()[0])
    # THE ASSERTION THAT USED TO STAND HERE IS NOW VACUOUS, AND THIS IS WHY IT WAS REPLACED.
    # Task 0382, ruling on lane E's migration-36 patch.
    #
    # It read `eq("and the second decision is what the row records now",
    # row(tid)["accepted_by"], "andrew")`, and it discriminated because the FIRST acceptance was
    # `operator` and the SECOND was `andrew`: two different names, so the comparison could only
    # pass if the second write had landed. Migration 36 refuses `andrew` -- correctly, it is not
    # a login -- and renaming it to `operator` makes both acceptances the same name. The
    # comparison would then pass over a row the second acceptance never touched, INCLUDING in
    # the case where the verb silently did nothing.
    #
    # A rename that turns a live assertion into a tautology is worse than a red, because nothing
    # says it happened. The property this test is actually about survives the rename: a SECOND
    # acceptance is a second decision, so it carries a NEW timestamp.
    second = row(tid)["accepted_at"]
    truth("and the second decision is what the row records now: accepted_at MOVED past the "
          f"first acceptance ({first} -> {second})",
          second is not None and first is not None and second > first,
          f"first={first} second={second}. Same value means the second acceptance did not "
          f"write, and comparing accepted_by cannot see that any more: both decisions are now "
          f"the same login by construction.")
    eq("and it is still a human login on the row", row(tid)["accepted_by"], "operator")

    # ... and the exception does not become a loophole. With no NEW reopen after the second
    # acceptance, the rewrite door is shut again.
    refused("but a rewrite with no reopen after it is refused again",
            "UPDATE brain.work_item SET accepted_by='someone-else', accepted_at=now() WHERE id=%s",
            (tid,), naming="rewrite")


def test_the_ordinary_verbs_are_untouched():
    """`BEFORE UPDATE OF accepted_by, accepted_at` means every other write skips the trigger.

    Named explicitly because the cheap way to write this guard -- `BEFORE UPDATE` with no column
    list -- would run this function on every claim, every heartbeat-driven write and every
    `set`, and would put a `brain.thread` subquery in the path of the claim loop.
    """
    reset()
    tid = store.apply("post", lane="t", title="an ordinary task's ordinary life",
                      agent_claimable=True, workdir="/tmp")["id"]
    store.apply("claim", agent="T2", lanes=["t"], role="terminal")
    store.apply("fail", id=tid, reason="attempt 1 ran out of road", agent="T2")
    store.apply("claim", agent="T2", lanes=["t"], role="terminal")
    store.apply("done", id=tid, summary="attempt 2 finished it", agent="T2")
    store.apply("reopen", id=tid, reason="not quite", agent="operator")
    store.apply("claim", agent="T2", lanes=["t"], role="terminal")
    store.apply("block", id=tid, reason="waiting on the operator", agent="T2")
    eq("post -> claim -> fail -> claim -> done -> reopen -> claim -> block all ran",
       row(tid)["state"], "blocked")
    allowed("and a plain UPDATE that names neither acceptance column still goes through",
            "UPDATE brain.work_item SET host='somewhere' WHERE id=%s", (tid,))


def test_writing_the_same_acceptance_again_is_not_a_failure():
    """Idempotence, migration 14's precedent. A re-run must not be an error."""
    reset()
    tid = finished_task()
    store.apply("accept work", id=tid, by="operator", as_operator=True)
    at = row(tid)["accepted_at"]
    allowed("re-writing the identical acceptance is a no-op, not a refusal",
            "UPDATE brain.work_item SET accepted_by='operator', accepted_at=%s WHERE id=%s",
            (at, tid))
    eq("and it is still the same acceptance", row(tid)["accepted_at"], at)


# ============================================================ 4. the guard is actually installed

def test_the_trigger_is_on_the_table_the_operator_writes():
    """The finding underneath the finding: `recommendation` was guarded and `work_item` was not.

    `brain.recommendation` holds zero rows on live and nothing produces one. The trigger had gone
    on the empty table.
    """
    with store.read("runtime") as s:
        names = [r["tgname"] for r in s.query(
            "SELECT t.tgname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname='brain' AND c.relname='work_item' AND NOT t.tgisinternal "
            "ORDER BY t.tgname")]
        ver = s.scalar("SELECT max(version) FROM brain.schema_migration")
    truth("work_item_acceptance_guard is on brain.work_item",
          "work_item_acceptance_guard" in names, f"triggers: {names}")
    truth("alongside the two that were already there",
          {"work_item_brief_write_once", "work_item_parent_acyclic"} <= set(names),
          f"triggers: {names}")
    truth("and the ledger records migration 22 or later", ver >= 22, f"schema_migration {ver}")


def main():
    print(__doc__.splitlines()[0])
    print(f"database {os.environ['BRAIN_PG_DB']}")
    print("\n-- the four routes measured at rowcount=1 before migration 22")
    test_a_humans_acceptance_cannot_be_reattributed_to_an_agent()
    test_an_acceptance_cannot_be_forged_onto_work_nobody_finished()
    test_an_acceptance_cannot_be_erased()
    test_a_work_item_cannot_be_born_accepted()
    print("\n-- the rules those routes imply")
    test_half_an_acceptance_is_refused()
    test_the_column_refuses_an_agent_acceptor_even_when_both_halves_are_written()
    print("\n-- and everything legitimate still works")
    test_the_verb_still_accepts_finished_work()
    test_the_auto_accept_rule_can_still_write_its_own_acceptance()
    test_accept_reopen_done_accept_again_is_permitted()
    test_the_ordinary_verbs_are_untouched()
    test_writing_the_same_acceptance_again_is_not_a_failure()
    print("\n-- the guard is where the operator writes, not where nothing does")
    test_the_trigger_is_on_the_table_the_operator_writes()
    # THE DENOMINATOR, and the INCONCLUSIVE count beside it rather than folded into either
    # column. This file was on policy/denominator-baseline.txt as debt; the guard below is the
    # four-line form the lint greps for. Task 0382.
    if PASS + FAIL == 0:                                              # DENOMINATOR
        print("\n0 comparisons made. A verdict over an empty set is not a pass.")
        return 2
    print(f"\n{PASS} passed, {FAIL} failed, {INCONC} inconclusive")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
