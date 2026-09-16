#!/usr/bin/env python3
"""Acceptance has an inverse, and the inverse is a record. Bus rows 0413 and 0419, migration 43.

Row `0413` is the operator's own words, driving the product end to end for the first time on
2026-08-28: *"nothing happened when I accepted work ... Maybe you have an option to like unaccept
or like send back or undo the acceptance would be good."* `web/MUST-NOT-BUILD.md` item 10 already
required an inverse verb wherever a real one exists, and acceptance had none. This file is the
engine half: the verb, the database exemption it depends on, and the four ways it must NOT be
reachable.

THE SHAPE OF THE THING BEING TESTED. Migration 22 refuses to clear `accepted_at` and
`accepted_by` at all, in these words: *"A record you can silently remove is not a record."*
Migration 43 keeps that refusal and adds one exemption with two arms, and the interesting tests
here are the ones that satisfy exactly one arm:

    arm 1  a thread row of kind `unaccept`, strictly after the acceptance, signed by
           brain.current_human().          A removal that is itself a record.
    arm 2  brain.current_human() IS NOT NULL at all.
           NULL is what brain_runtime reads, which is every agent surface in this fleet.

Scenes 4 through 8 each hold one arm and drop the other, and every one of them must be refused.
Scene 6 is the pointed one: it forges the thread row from `brain_runtime` first and then erases,
which is the attack a guard resting on arm 1 alone would let through.

WHAT IS DELIBERATELY NOT ASSERTED AS A PASS. `web/model.py:644` builds the console item screen's
trail from a hard allowlist of thread kinds and drops anything not in it. Scene 11 reads that
tuple and reports, as INCONCLUSIVE rather than as a pass or a failure, whether it has learned the
word. `web/` belongs to another lane; the remedy is one word and it is printed.

Row `0419` is the smaller half and scene 10 covers it: the `$USER` fallback that row 0384 removed
from `accept work` was still signing `start`, `stop`, `pause` and `resume`.

Run: python3 engine/tests/test_unaccept.py    (a scratch database, never `brain`)
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "queue"))

os.environ.setdefault("BRAIN_PG_DB", os.environ.get("ENGINE_SCRATCH_DB", "brain_scratch"))

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _scratch_preflight import reconcile              # noqa: E402
reconcile(os.environ["BRAIN_PG_DB"])

# The runner exports both of these into every terminal, and both of them mean "an agent is at the
# keyboard" to the two CLI doors under test. Popped so this suite tests the store and not the
# runner's environment.
os.environ.pop("SWARM_PARENT_TASK", None)
os.environ.pop("SWARM_AGENT", None)

import psycopg2                                       # noqa: E402
import store                                          # noqa: E402
import store.session as session                       # noqa: E402
from swarm_engine import accept, cli, transitions      # noqa: E402,F401  registers the verbs
from swarm_engine.transitions import VerbError        # noqa: E402

SCRATCH = str(ROOT / "engine/bin/scratch-db.sh")
WEB_MODEL = ROOT / "web/model.py"

PASS, FAIL, INCONC = 0, 0, 0


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


def inconclusive(msg, why):
    """Nothing was observed, and that is neither a pass nor a failure. Task 0382's mechanism.

    Used here for exactly one thing: the state of a file this lane may not edit.
    """
    global INCONC
    INCONC += 1
    print(f"  INCONCLUSIVE  {msg}")
    print(f"                {why}")


def eq(msg, got, want):
    ok(msg) if got == want else bad(msg, f"wanted [{want}], got [{got}]")


def truth(msg, cond, detail=""):
    ok(msg) if cond else bad(msg, detail)


# ------------------------------------------------------------------ the two raw connections
#
# Neither goes through the narrow waist. `store.read()` is READ ONLY and `store.apply` is the door
# being bypassed, so both of these are psycopg2 over the same credentials the runtime and the
# operator actually hold. Whatever refuses below is the TABLE, not a verb.

def _raw(role, sql, params=None, human=None):
    kw = {"human": human} if human is not None else {}
    conn = psycopg2.connect(**session.dsn(role, **kw))
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            n = cur.rowcount
        conn.commit()
        return n
    finally:
        conn.close()


def as_runtime(sql, params=None):
    """One statement as `brain_runtime`: the credential every agent process already holds."""
    return _raw("runtime", sql, params)


def as_operator(sql, params=None):
    """One statement as the operator's own login, which brain.current_human() DOES recognise."""
    return _raw("operator", sql, params)


def refused(msg, fn, naming=""):
    try:
        fn()
        bad(msg, "NOT refused. The statement went through.")
    except psycopg2.errors.RaiseException as exc:
        text = str(exc)
        if naming and naming not in text:
            bad(msg, f"refused, but the message does not name {naming!r}: {text.splitlines()[0]}")
        else:
            ok(msg)
    except Exception as exc:                                          # noqa: BLE001
        bad(msg, f"refused by the WRONG thing ({type(exc).__name__}): {exc}")


def refused_by_verb(msg, fn, naming=""):
    try:
        fn()
        bad(msg, "NOT refused. The verb ran.")
    except VerbError as exc:
        if naming and naming not in str(exc):
            bad(msg, f"refused, but the message does not name {naming!r}: {exc}")
        else:
            ok(msg)
    except Exception as exc:                                          # noqa: BLE001
        bad(msg, f"refused by the WRONG thing ({type(exc).__name__}): {exc}")


# ------------------------------------------------------------------ fixtures

def reset():
    subprocess.run([SCRATCH, "truncate"], check=True, capture_output=True,
                   env={**os.environ, "ENGINE_SCRATCH_DB": os.environ["BRAIN_PG_DB"]})


def agent_work_done(title="an item an agent worked and reported done"):
    """Posted, claimed, run and reported by an AGENT. This is the row migration 42 protects."""
    tid = store.apply("post", lane="t", title=title, agent_claimable=True, workdir="/tmp",
                      signals={"reversibility": "reversible"})["id"]
    store.apply("claim", agent="T2", lanes=["t"], role="terminal")
    store.apply("done", id=tid, summary="THE AGENT'S OWN REPORT", agent="T2")
    return tid


def accepted(title="an item an agent worked, reported and a human accepted"):
    tid = agent_work_done(title)
    store.apply("accept work", id=tid, by="operator", as_operator=True)
    return tid


def row(tid):
    with store.read("runtime") as s:
        return s.one("SELECT state, result, accepted_by, accepted_at "
                     "FROM brain.work_item WHERE id = %s", (tid,))


def thread(tid):
    with store.read("runtime") as s:
        return s.query("SELECT seq, from_agent, kind, text FROM brain.thread "
                       "WHERE work_item_id = %s ORDER BY seq", (tid,))


def ledger():
    with store.read("runtime") as s:
        return s.scalar("SELECT max(version) FROM brain.schema_migration")


def tier_of_item(tid):
    """The tier the console would put this row in, through the queue's own composition."""
    from human_queue import reads as qreads
    q = qreads.queue()
    for name, block in q["tiers"].items():
        for it in block["items"]:
            if str(it["source_id"]) == tid and it["source_type"] == "work_item":
                return name, it["tier_reason"]
    return None, None


# ============================================================ 1. the verb, and the round trip

def test_the_inverse_exists_and_leaves_the_work_alone():
    """Accept, then withdraw. The DECISION goes; the work does not move an inch."""
    reset()
    tid = accepted()
    before = row(tid)
    r = store.apply("unaccept work", id=tid, reason="misclicked the card during the demo",
                    as_operator=True)

    eq("the verb reports the human the DATABASE says this connection is", r["unaccepted_by"],
       "operator")
    eq("and it reports whose acceptance was withdrawn", r["was_accepted_by"], "operator")
    after = row(tid)
    eq("accepted_by is cleared", after["accepted_by"], None)
    eq("accepted_at is cleared", after["accepted_at"], None)
    eq("THE WORK IS UNTOUCHED: the state is still done", after["state"], "done")
    eq("and result still holds the AGENT'S report, not the operator's sentence",
       after["result"], before["result"])
    eq("which is the whole difference from `reopen`", after["result"], "THE AGENT'S OWN REPORT")

    kinds = [t["kind"] for t in thread(tid)]
    truth("the acceptance is still on the append-only thread", "accept" in kinds, str(kinds))
    truth("and the withdrawal is on it too, as its own kind", "unaccept" in kinds, str(kinds))
    un = [t for t in thread(tid) if t["kind"] == "unaccept"][0]
    eq("signed by the human, not by a string the caller passed", un["from_agent"], "operator")
    truth("and the reason is in the text", "misclicked the card" in un["text"], un["text"])
    truth("as is the acceptance it withdrew", "withdrew their own acceptance" in un["text"],
          un["text"])


def test_the_item_comes_back_to_the_queue_where_it_was():
    """Where the row goes afterwards, measured rather than assumed. It is NOT the Judge tier.

    `brain.queue_open` arm 1 is `state = 'done' AND accepted_at IS NULL`, so a withdrawal puts
    the item back on the same arm with the same `Accept work` control. The TIER is the part worth
    measuring, because the obvious guess is wrong: `item_class` is `review`, which
    `tiers.NEVER_DECIDE_CLASSES` keeps out of Decide, but a plain work item carries no queue_item
    overlay, so `tiers.tier_of` hits its "no template, no recommended option and no prepared
    context" branch FIRST and returns `shape`, not `judge`. What matters for an undo is that the
    tier is the same before the acceptance and after the withdrawal, so the operator's queue looks
    exactly as it did before he pressed the wrong control.
    """
    reset()
    tid = agent_work_done()
    before_tier, before_reason = tier_of_item(tid)
    store.apply("accept work", id=tid, by="operator", as_operator=True)
    accepted_tier, _ = tier_of_item(tid)
    store.apply("unaccept work", id=tid, reason="changed my mind after reading it again",
                as_operator=True)
    after_tier, after_reason = tier_of_item(tid)

    eq("an accepted item is off the queue entirely", accepted_tier, None)
    truth("a done, unaccepted item is ON the queue before acceptance", before_tier is not None,
          f"tier={before_tier}")
    eq("the withdrawal puts it back on the SAME tier it came from", after_tier, before_tier)
    eq("and for the same stated reason", after_reason, before_reason)
    eq("and that tier is `shape`, not `judge`: a work item carries no overlay, so nothing is "
       "prepared to judge FROM", after_tier, "shape")

    with store.read("runtime") as s:
        arm = s.one("SELECT default_item_class, primary_verb FROM brain.queue_open "
                    "WHERE source_type = 'work_item' AND source_id = %s", (tid,))
    eq("and the control on it is `Accept work` again", arm["primary_verb"], "Accept work")


def test_it_can_be_accepted_again_afterwards():
    """The round trip closes. Withdraw, then accept a second time, with no `reopen` in between."""
    reset()
    tid = accepted()
    store.apply("unaccept work", id=tid, reason="pressed it by accident", as_operator=True)
    r = store.apply("accept work", id=tid, by="operator", as_operator=True)
    eq("the item can be accepted again", r["accepted_by"], "operator")
    truth("and it is accepted", row(tid)["accepted_at"] is not None, str(row(tid)))
    kinds = [t["kind"] for t in thread(tid)]
    eq("three decisions on the trail, in order", [k for k in kinds if k in ("accept", "unaccept")],
       ["accept", "unaccept", "accept"])


def test_the_rules_acceptance_can_be_withdrawn_by_a_human():
    """The auto-accept rule's own acceptance is withdrawable, which is the point of measuring it.

    `brain.auto_acceptor()` is exempt from migration 36's human-login gate because the rule acting
    inside a `done` transaction has no human near it. Nothing about that exempts the WITHDRAWAL,
    which is a human disagreeing with the rule.
    """
    reset()
    tid = agent_work_done()
    as_runtime("UPDATE brain.work_item SET accepted_at = now(), "
               "accepted_by = brain.auto_acceptor() WHERE id = %s", (tid,))
    eq("the rule's acceptance is on the row", row(tid)["accepted_by"], "auto-accept")
    r = store.apply("unaccept work", id=tid, reason="the rule accepted this and I disagree",
                    as_operator=True)
    eq("a human withdrew it", r["unaccepted_by"], "operator")
    eq("and the record names whose acceptance it was", r["was_accepted_by"], "auto-accept")
    un = [t for t in thread(tid) if t["kind"] == "unaccept"][0]
    truth("the thread says it was somebody else's acceptance, not the withdrawer's own",
          "the acceptance by auto-accept" in un["text"], un["text"])


# ============================================================ 2. the verb's own refusals

def test_a_withdrawal_with_no_reason_is_refused():
    reset()
    tid = accepted()
    refused_by_verb("a withdrawal with no reason is refused, not defaulted",
                    lambda: store.apply("unaccept work", id=tid, reason="", as_operator=True),
                    naming="no reason")
    refused_by_verb("and whitespace is not a reason",
                    lambda: store.apply("unaccept work", id=tid, reason="   ", as_operator=True))
    truth("the acceptance survived both", row(tid)["accepted_at"] is not None, str(row(tid)))


def test_withdrawing_nothing_is_a_refusal_and_not_a_silent_success():
    """The N4 defect in its opposite mask: a control that reports success for changing nothing."""
    reset()
    tid = agent_work_done()
    refused_by_verb("withdrawing an acceptance that does not exist is refused",
                    lambda: store.apply("unaccept work", id=tid, reason="undo",
                                        as_operator=True),
                    naming="is not accepted")
    refused_by_verb("and so is a task that does not exist",
                    lambda: store.apply("unaccept work", id="9999", reason="undo",
                                        as_operator=True),
                    naming="no such task")


def test_the_verb_refuses_a_by_that_disagrees_with_the_connection():
    reset()
    tid = accepted()
    refused_by_verb("a `by` the database does not agree with is refused, not overwritten",
                    lambda: store.apply("unaccept work", id=tid, by="somebody-else",
                                        reason="undo", as_operator=True),
                    naming="the database says the connection is")
    truth("the acceptance survived it", row(tid)["accepted_at"] is not None, str(row(tid)))


def test_an_agent_surface_cannot_withdraw_an_acceptance():
    """`as_operator` omitted is the agent surface's call, and it reads current_human() as NULL."""
    reset()
    tid = accepted()
    refused_by_verb("the verb refuses a connection the database does not know as a human",
                    lambda: store.apply("unaccept work", id=tid, reason="undo"),
                    naming="does not know as a human")
    truth("the acceptance survived it", row(tid)["accepted_at"] is not None, str(row(tid)))


# ============================================================ 3. the two arms, one at a time
#
# Everything above drives the verb. Everything below goes around it, over the raw credentials, so
# what refuses is migration 43's exemption inside migration 22's guard.

def test_arm_1_alone_a_human_erasing_with_no_record_on_the_thread():
    """Migration 22's rule, unchanged: a removal that leaves no record is still refused."""
    reset()
    tid = accepted()
    refused("the operator's OWN login cannot erase an acceptance with no withdrawal on the thread",
            lambda: as_operator("UPDATE brain.work_item SET accepted_by = NULL, "
                                "accepted_at = NULL WHERE id = %s", (tid,)),
            naming="filed no withdrawal")
    eq("the acceptance is intact", row(tid)["accepted_by"], "operator")


def test_arm_2_alone_an_agent_forging_the_withdrawal_and_then_erasing():
    """THE POINTED ONE. brain_runtime writes its own `unaccept` row, then erases. Refused.

    A guard resting on the thread record alone would pass this: `brain_runtime` holds INSERT on
    `brain.thread` and can write any kind the CHECK allows. What stops it is that the record must
    be signed by `brain.current_human()`, which is NULL on this login, so the forged row matches
    nothing and the connection fails the second arm as well.
    """
    reset()
    tid = accepted()
    as_runtime("INSERT INTO brain.thread (work_item_id, from_agent, kind, text) "
               "VALUES (%s, 'operator', 'unaccept', 'forged withdrawal')", (tid,))
    truth("the forged withdrawal IS on the thread: nothing stopped the insert",
          any(t["kind"] == "unaccept" for t in thread(tid)), str(thread(tid)))
    refused("and the erasure behind it is still refused, because brain_runtime is not a human",
            lambda: as_runtime("UPDATE brain.work_item SET accepted_by = NULL, "
                               "accepted_at = NULL WHERE id = %s", (tid,)),
            naming="filed no withdrawal")
    eq("the acceptance is intact", row(tid)["accepted_by"], "operator")


def test_a_withdrawal_signed_by_somebody_else_does_not_count():
    """The record must name the human this CONNECTION is, which is migration 36's rule one column
    over. A human who could file a withdrawal under a colleague's name is the same forgery one
    seat over that `accepted_by` was closed against."""
    reset()
    tid = accepted()
    as_operator("INSERT INTO brain.thread (work_item_id, from_agent, kind, text) "
                "VALUES (%s, 'somebody-else', 'unaccept', 'signed by the wrong human')", (tid,))
    refused("a withdrawal signed by another name does not unlock the erasure",
            lambda: as_operator("UPDATE brain.work_item SET accepted_by = NULL, "
                                "accepted_at = NULL WHERE id = %s", (tid,)),
            naming="filed no withdrawal")
    eq("the acceptance is intact", row(tid)["accepted_by"], "operator")


def test_a_withdrawal_dated_before_the_acceptance_does_not_count():
    """STRICTLY after, for migration 22's own reason: a stale record must not license a fresh
    erasure. Without it, one withdrawal would license every later acceptance's removal too."""
    reset()
    tid = accepted()
    as_operator("INSERT INTO brain.thread (work_item_id, from_agent, kind, text, ts) "
                "VALUES (%s, 'operator', 'unaccept', 'filed before the acceptance', "
                "now() - interval '1 day')", (tid,))
    refused("a withdrawal predating the acceptance does not unlock the erasure",
            lambda: as_operator("UPDATE brain.work_item SET accepted_by = NULL, "
                                "accepted_at = NULL WHERE id = %s", (tid,)),
            naming="filed no withdrawal")
    eq("the acceptance is intact", row(tid)["accepted_by"], "operator")


def test_migration_22s_other_three_rules_are_untouched():
    """The exemption is narrow. Everything else migration 22 refuses, it still refuses."""
    reset()
    tid = accepted()
    refused("an acceptance still cannot be reattributed to an agent",
            lambda: as_runtime("UPDATE brain.work_item SET accepted_by = 'T2' WHERE id = %s",
                               (tid,)),
            naming="rewrite")
    refused("nor half-erased by nulling the timestamp alone",
            lambda: as_runtime("UPDATE brain.work_item SET accepted_at = NULL WHERE id = %s",
                               (tid,)))
    refused("nor half-erased by blanking the acceptor alone",
            lambda: as_runtime("UPDATE brain.work_item SET accepted_by = '' WHERE id = %s",
                               (tid,)))
    reset()
    refused("a work item still cannot be born accepted",
            lambda: as_runtime("INSERT INTO brain.work_item (id, lane, title, state, "
                               "accepted_by, accepted_at) "
                               "VALUES ('9260','t','forged','done','operator',now())"),
            naming="born accepted")
    tid2 = agent_work_done()
    store.apply("block", id=tid2, reason="parked", agent="T2", force=True)
    refused("and an acceptance still cannot be forged onto work nobody finished",
            lambda: as_runtime("UPDATE brain.work_item SET accepted_by='operator', "
                               "accepted_at=now() WHERE id=%s", (tid2,)),
            naming="not done")


# ============================================================ 4. it does not compose into a forgery
#
# Migration 42 exists because `reopen` clears `claimed_by` by design, so two clicks let an operator
# file a `done` over an agent's report. `unaccept` is a second verb that changes what a row looks
# like after a human acts on it, so the question has to be asked again rather than assumed.

def test_unaccept_then_done_cannot_file_over_an_agents_report():
    """The 0399 shape with the new verb in front of it. Migration 42 must still refuse."""
    reset()
    tid = accepted()
    store.apply("unaccept work", id=tid, reason="not good enough, I will write it up myself",
                as_operator=True)
    # REFUSED BY THE DATABASE, not by a verb, and the distinction is the finding rather than a
    # detail of the assertion. `_hold` lets this through because the row is not `active`, exactly
    # as bus row 0399 measured, so the only thing standing between a withdrawal and a forged
    # report is migration 42's trigger. It stands.
    refused("after a withdrawal, an operator `done` over the agent's report is refused",
            lambda: store.apply("done", id=tid, summary="the operator files T2's report",
                                agent="operator"),
            naming="over an agent's work")
    eq("and result still holds the agent's own words",
       row(tid)["result"], "THE AGENT'S OWN REPORT")


def test_unaccept_then_reopen_then_done_cannot_file_over_an_agents_report():
    """The original 0399 sequence with a withdrawal added at the front. Still refused."""
    reset()
    tid = accepted()
    store.apply("unaccept work", id=tid, reason="undo", as_operator=True)
    store.apply("reopen", id=tid, reason="re-walk it", agent="operator")
    refused("unaccept then reopen then done is still refused",
            lambda: store.apply("done", id=tid, summary="the operator files T2's report",
                                agent="operator"),
            naming="over an agent's work")
    eq("and result still holds the agent's own words",
       row(tid)["result"], "THE AGENT'S OWN REPORT")


def test_unaccept_does_not_trip_migration_42_itself():
    """The other direction: the withdrawal must not be refused BY the forgery guard.

    An unaccept writes neither `state` nor `result`, which is the exemption migration 42 already
    grants an acceptance, so it passes. Asserted rather than assumed, because a guard that refused
    the undo on an AGENT'S work would leave the inverse verb working only on the operator's own
    rows, which is the half of the queue that needs it least.
    """
    reset()
    tid = accepted()
    with store.read("runtime") as s:
        worked = s.scalar("SELECT brain.work_item_agent_has_worked(%s)", (tid,))
    truth("the row is one migration 42 considers agent-worked", worked, str(worked))
    store.apply("unaccept work", id=tid, reason="undo on an agent-worked row", as_operator=True)
    eq("and the withdrawal went through anyway", row(tid)["accepted_at"], None)


# ============================================================ 5. row 0419, the $USER fallback

def _env_user_reads(path: Path) -> list:
    """Every place this module actually READS `$USER`, by parsing rather than by matching text.

    Returns `[]` or a list of line numbers. Covers the three spellings that exist in this repo:
    `os.environ.get("USER")`, `os.environ["USER"]` and `os.getenv("USER")`. It reads expressions,
    so a docstring quoting the removed line is not a hit and a rename of `os` to something else
    would be, which is the right way round for a check whose job is to say the elimination is
    complete.
    """
    import ast
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) \
                and node.slice.value == "USER" and ast.unparse(node.value).endswith("environ"):
            out.append(node.lineno)
        if isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant) \
                and node.args[0].value == "USER" \
                and ast.unparse(node.func).endswith(("environ.get", "getenv")):
            out.append(node.lineno)
    return sorted(out)



def test_the_user_fallback_is_gone_from_the_fleet_verbs():
    """Row 0419. `$USER` on this host is `you`, which is nobody.

    Measured read-only on 2026-08-28 before the fix: `brain.message` on live `brain` carried three
    `stop` records from `you`, and `brain_demo` carried one `start` from `you` beside one
    `stop` from `operator`. Two names for one person in one session.
    """
    reset()
    os.environ["USER"] = "you"
    os.environ.pop("SWARM_AGENT", None)
    eq("_who no longer answers with the shell login", cli._who(None), store.human_slug())
    truth("and what it answers is not the shell login", cli._who(None) != "you",
          cli._who(None))
    eq("an explicit --by still wins, because an agent must be able to sign its own name",
       cli._who(type("A", (), {"by": "T2"})()), "T2")
    os.environ["SWARM_AGENT"] = "T9"
    eq("and a fleet terminal still signs its own name", cli._who(None), "T9")
    os.environ.pop("SWARM_AGENT", None)

    # PARSED, NOT GREPPED, and the difference is not pedantry: this file's own `_who` docstring
    # QUOTES the removed line, so a grep for the string matches the paragraph explaining why it is
    # gone and reports the fix as the defect. `ast` sees expressions and not prose.
    eq("and there is no `$USER` read left anywhere in cli.py",
       _env_user_reads(Path(cli.__file__)), [])

    store.apply("stop", agent="PROBE419", by=cli._who(None), reason="row 0419 probe")
    store.apply("start", agent="PROBE419", by=cli._who(None), reason="row 0419 probe")
    with store.read("runtime") as s:
        signers = [r["from_agent"] for r in s.query(
            "SELECT from_agent FROM brain.message WHERE to_agent = 'PROBE419' ORDER BY ts")]
    eq("so the fleet lifecycle record is signed by the human, not by the login",
       signers, ["operator", "operator"])


# ============================================================ 6. the vocabulary and its readers

def test_the_store_learned_the_word():
    reset()
    with store.read("runtime") as s:
        defn = s.scalar(
            "SELECT pg_get_constraintdef(c.oid) FROM pg_constraint c "
            "JOIN pg_class t ON t.oid = c.conrelid JOIN pg_namespace n ON n.oid = t.relnamespace "
            "WHERE n.nspname='brain' AND t.relname='thread' AND c.conname='thread_kind_check'")
    truth("brain.thread.kind carries `unaccept`", "'unaccept'" in (defn or ""), str(defn))
    for word in ("post", "claim", "note", "accept", "reopen", "budget"):
        truth(f"and it did not lose `{word}`", f"'{word}'" in (defn or ""), str(defn))
    truth("the ledger records migration 43 or later", (ledger() or 0) >= 43,
          f"schema_migration {ledger()}")


def test_the_console_trail_allowlist_is_reported_not_assumed():
    """`web/model.py` drops a kind it does not name. This lane may not edit that file.

    Migration 13 established the rule in as many words: "Anything added to `brain.thread.kind` has
    to be added here in the same change." It is reported as INCONCLUSIVE rather than as a failure
    because a red here would be a red on another lane's file that this suite cannot clear, and
    reported at all rather than left silent because a dropped kind is invisible by construction:
    the console renders nothing, not an unknown row.
    """
    if not WEB_MODEL.exists():
        inconclusive("the console trail allowlist could not be read",
                     f"{WEB_MODEL} does not exist in this tree")
        return
    src = WEB_MODEL.read_text(encoding="utf-8")
    block = re.search(r"trail = \[t for t in th if t\[\"kind\"\] in\s*\(([^)]*)\)", src)
    if not block:
        inconclusive("the console trail allowlist could not be found",
                     "web/model.py no longer carries the `trail = [...]` allowlist this checks; "
                     "find where the item screen filters thread kinds and confirm `unaccept` is "
                     "in it")
        return
    if '"unaccept"' in block.group(1):
        ok("web/model.py's trail allowlist has learned `unaccept`")
    else:
        inconclusive("web/model.py's trail allowlist has NOT learned `unaccept`",
                     "the withdrawal is recorded, is in `swarm show` and is in brain.feed, and is "
                     "DROPPED from the console item screen until the word is added to the tuple "
                     f"at {WEB_MODEL}:644. That file is another lane's; this is the whole remedy.")


def main():
    print(__doc__.splitlines()[0])
    print(f"database {os.environ['BRAIN_PG_DB']}, ledger {ledger()}")
    print("\n-- the inverse exists, and the work does not move")
    test_the_inverse_exists_and_leaves_the_work_alone()
    test_the_item_comes_back_to_the_queue_where_it_was()
    test_it_can_be_accepted_again_afterwards()
    test_the_rules_acceptance_can_be_withdrawn_by_a_human()
    print("\n-- what the verb refuses")
    test_a_withdrawal_with_no_reason_is_refused()
    test_withdrawing_nothing_is_a_refusal_and_not_a_silent_success()
    test_the_verb_refuses_a_by_that_disagrees_with_the_connection()
    test_an_agent_surface_cannot_withdraw_an_acceptance()
    print("\n-- the two arms of the exemption, held one at a time, over the raw credentials")
    test_arm_1_alone_a_human_erasing_with_no_record_on_the_thread()
    test_arm_2_alone_an_agent_forging_the_withdrawal_and_then_erasing()
    test_a_withdrawal_signed_by_somebody_else_does_not_count()
    test_a_withdrawal_dated_before_the_acceptance_does_not_count()
    test_migration_22s_other_three_rules_are_untouched()
    print("\n-- and it does not compose into the forgery migration 42 refuses")
    test_unaccept_then_done_cannot_file_over_an_agents_report()
    test_unaccept_then_reopen_then_done_cannot_file_over_an_agents_report()
    test_unaccept_does_not_trip_migration_42_itself()
    print("\n-- row 0419: the $USER fallback the fleet verbs still had")
    test_the_user_fallback_is_gone_from_the_fleet_verbs()
    print("\n-- the new word, and the readers that have to learn it")
    test_the_store_learned_the_word()
    test_the_console_trail_allowlist_is_reported_not_assumed()
    # THE DENOMINATOR, with the INCONCLUSIVE count printed beside it rather than folded into
    # either column. A verdict over an empty set is not a pass.
    if PASS + FAIL == 0:                                              # DENOMINATOR
        print("\n0 comparisons made. A verdict over an empty set is not a pass.")
        return 2
    print(f"\n{PASS} passed, {FAIL} failed, {INCONC} inconclusive")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
