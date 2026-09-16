#!/usr/bin/env python3
"""A cancelled task leaves nothing in the operator's queue. Task 0159.

THE COST, MEASURED RATHER THAN PREDICTED. Task 0147 cancelled seven fabricated rows on the live
bus on 2026-08-18. Six carried an already-answered question; 0139 carried an unanswered one,
q0140, and cancelling the task did not close it. `swarm ls --lane fabric` printed `0139 cancelled`
while `swarm questions` went on printing `q0140  T2  on 0139  Q: cut from main or from the release
branch?`, between two real decisions the operator actually owed. At 16:47Z HE ANSWERED IT --
'CUT FROM MAIN. Same as q0130 ... THIS IS AN ADMIRAL DECISION.' -- a question raised by a test,
about a release branch that does not exist, on a task that had been cancelled four and a half
hours earlier. The whole system exists to protect that attention and it spent some.

Nothing else could have cleared it. `answer` and `reanswer` take no actor and always record the
identity `operator`, and task 0147 existed BECAUSE a tool was manufacturing operator-signed
answers on the live bus; a grep for `withdraw|retract_question|question.withdrawn|close_question`
across engine/, store/ and queue/ returned nothing that applied to a question.

So the fix is a THIRD terminal state -- withdrawn -- and every test below asserts a property of
it rather than the shape of the code:

  1. cancel takes the task's open questions out of the queue
  2. and does it WITHOUT writing an answer or the identity `operator`
  3. an already-answered question is left exactly as the operator left it
  4. `withdraw` is signed, and an anonymous one is refused inside a fleet terminal
  5. an answer arriving after a withdrawal is refused, not silently recorded
  6. the human queue's own view (`brain.queue_open`) drops it too, and so does the
     pending-default scan, because firing a default is an ACT taken on a cancelled task's behalf
  7. one `question.withdrawn` event per question, so the pager can retract the page it sent

Run: python3 engine/tests/test_cancel_withdraws_questions.py  (against a SCRATCH store, never
`brain`)
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine"))
# The human queue is on the path because `checkpoints.due()` is one of the five sites that
# read `withdrawn_at`, and it is the one that decides something on the OPERATOR'S behalf. It
# lives in another lane's directory; the scene below still has to run it, because a site that
# no suite exercises against a pre-31 store is exactly how this shipped.
sys.path.insert(0, str(ROOT / "queue"))
sys.path.insert(0, str(ROOT))

# ISOLATION, FAIL-CLOSED. Task 0147/0158: `setdefault` is a no-op when the name is already set and
# every agent terminal in this fleet exports BRAIN_PG_DB=brain, so the idiom this replaces wrote
# the LIVE bus. Assignment, both names -- `store` reads BRAIN_PG_DB and `scratch-db.sh` reads
# ENGINE_SCRATCH_DB, and binding one of the two is how the truncate resets one database while the
# writes land in another (task 0141) -- and then a refusal, because a default is one edit away
# from being gone.
os.environ["BRAIN_PG_DB"] = os.environ.get("ENGINE_SCRATCH_DB") or "brain_scratch"
os.environ["ENGINE_SCRATCH_DB"] = os.environ["BRAIN_PG_DB"]
if os.environ["BRAIN_PG_DB"] == "brain":
    sys.exit("test_cancel_withdraws_questions.py: refusing to run against the live store 'brain'. "
             "This suite posts tasks, asks questions, cancels and truncates.")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _scratch_preflight import reconcile              # noqa: E402
reconcile(os.environ["BRAIN_PG_DB"])
os.environ.pop("SWARM_PARENT_TASK", None)
os.environ["BRAIN_AFTER_COMMIT_HOOKS"] = "1"

import psycopg2                                                  # noqa: E402
import store                                                     # noqa: E402
from store import reads as store_reads                           # noqa: E402
from store import schema as db_schema                            # noqa: E402
from store import session                                        # noqa: E402
from swarm_engine import reads, transitions                      # noqa: E402,F401  (registers)
from swarm_engine.transitions import VerbError                   # noqa: E402
from human_queue import checkpoints                              # noqa: E402

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


def task(**kw) -> str:
    kw.setdefault("agent_claimable", True)
    kw.setdefault("workdir", "/tmp")
    return store.apply("post", lane="fabric", title="a fabricated row", posted_by="T1", **kw)["id"]


def question_row(qid: str) -> dict:
    with store.read("runtime") as s:
        return s.one("SELECT * FROM brain.question WHERE id = %s", (qid,))


def open_qids() -> list:
    """What `swarm questions` prints, through the same read the CLI calls."""
    return [q["id"] for q in reads.questions()]


def events(**where):
    sql = "SELECT * FROM brain.event"
    params = []
    if where:
        sql += " WHERE " + " AND ".join(f"{k} = %s" for k in where)
        params = list(where.values())
    sql += " ORDER BY event_seq"
    with store.read("runtime") as s:
        return s.query(sql, params)


# ================================================================ the defect itself

def test_cancel_takes_the_question_out_of_the_operators_queue():
    """The 0147 reproduction, inverted: after the cancel, `swarm questions` is empty."""
    reset()
    tid = task()
    store.apply("claim", agent="T2", lanes=["fabric"])
    q = store.apply("ask", question="cut from main or from the release branch?", agent="T2",
                    task=tid, default="")
    eq("before the cancel the question is in the queue", open_qids(), [q["id"]])
    r = store.apply("cancel", id=tid, reason="fabricated row", agent="T1", force=True)
    eq("the task is cancelled", r["state"], "cancelled")
    eq("and the question is no longer in the operator's queue", open_qids(), [])
    eq("and the cancel says which questions it took, rather than doing it silently",
       r.get("withdrew"), [q["id"]])


def test_it_is_not_an_answer_and_nobody_signed_it_operator():
    """The constraint the whole task turns on: NOT `answer`, NOT the identity `operator`.

    Closing q0140 by answering it would have been task 0147's own defect with a nicer motive.
    """
    reset()
    tid = task()
    store.apply("claim", agent="T2", lanes=["fabric"])
    q = store.apply("ask", question="which branch?", agent="T2", task=tid, default="")
    store.apply("cancel", id=tid, reason="fabricated row", agent="T1", force=True)
    row = question_row(q["id"])
    eq("`answer` is still NULL, so nothing downstream can read a decision here", row["answer"],
       None)
    eq("and `answered_at` is NULL too", row["answered_at"], None)
    truth("it is withdrawn", row["withdrawn_at"] is not None)
    eq("signed by the hand that cancelled, not by the operator", row["withdrawn_by"], "T1")
    truth("and the reason names the cancel", "cancelled" in (row["withdrawn_reason"] or ""),
          row["withdrawn_reason"])
    with store.read("runtime") as s:
        note = s.one("SELECT text FROM brain.thread WHERE work_item_id = %s AND kind = 'note' "
                     "AND text LIKE %s ORDER BY seq DESC", (tid, "%withdrawn%"))
    truth("and the task's thread records it, so the act is not invisible", bool(note),
          "no thread note naming the withdrawal")


def test_an_answered_question_is_left_exactly_as_the_operator_left_it():
    """A cancel does not get to erase a decision a human made."""
    reset()
    tid = task()
    store.apply("claim", agent="T2", lanes=["fabric"])
    q = store.apply("ask", question="which branch?", agent="T2", task=tid, default="")
    store.apply("answer", qid=q["id"], text="CUT FROM MAIN")
    store.apply("cancel", id=tid, reason="no longer needed", agent="T1", force=True)
    row = question_row(q["id"])
    eq("the answer is untouched", row["answer"], "CUT FROM MAIN")
    eq("and it was not also withdrawn -- a question is answered OR withdrawn, never both",
       row["withdrawn_at"], None)


def test_the_database_refuses_both_at_once_whatever_a_future_writer_does():
    """The CHECKs, because the transitions are not the only thing that will ever write this table.

    Straight at the table over `brain_runtime`'s own credential -- no verb, no VerbError, no
    narrow waist in the path. Whatever refuses here is Postgres.
    """
    reset()
    tid = task()
    store.apply("claim", agent="T2", lanes=["fabric"])
    q = store.apply("ask", question="which branch?", agent="T2", task=tid, default="")
    store.apply("answer", qid=q["id"], text="CUT FROM MAIN")

    def as_runtime(sql, params=None):
        conn = psycopg2.connect(**session.dsn("runtime"))
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
            conn.commit()
        finally:
            conn.close()

    try:
        as_runtime("UPDATE brain.question SET withdrawn_at = now(), withdrawn_by = 'X' "
                   "WHERE id = %s", (q["id"],))
        bad("the TABLE refuses a question that is both answered and withdrawn")
    except psycopg2.errors.CheckViolation as e:                   # noqa: BLE001
        truth("the TABLE refuses a question that is both answered and withdrawn",
              "question_not_both_ck" in str(e), str(e).splitlines()[0])

    q2 = store.apply("ask", question="and the tag?", agent="T2", task=tid, default="")
    try:
        as_runtime("UPDATE brain.question SET withdrawn_at = now() WHERE id = %s", (q2["id"],))
        bad("and it refuses an UNSIGNED withdrawal, whoever writes it")
    except psycopg2.errors.CheckViolation as e:                   # noqa: BLE001
        truth("and it refuses an UNSIGNED withdrawal, whoever writes it",
              "question_withdrawn_signed_ck" in str(e), str(e).splitlines()[0])


# ================================================================ the agent-signed verb

def test_withdraw_is_signed_and_an_unnamed_one_is_refused_in_a_terminal():
    """An agent may retire its own question. It may not do it anonymously.

    The refusal is the point: an unnamed withdrawal would be filed as `operator`, which is the
    unattributed write this verb was built to replace.
    """
    reset()
    tid = task()
    store.apply("claim", agent="T2", lanes=["fabric"])
    q = store.apply("ask", question="which branch?", agent="T2", task=tid, default="")
    # BOTH variables, because `_caller` reads three sources in order: the flag, `SWARM_AGENT`,
    # and -- only outside a fleet terminal -- the operator. A terminal that exports SWARM_AGENT
    # is already named and its withdrawal is signed with that name, which is the ordinary case
    # and the reason this suite has to clear the variable to reach the refusal at all. Measured
    # while writing it: run from a live terminal with SWARM_AGENT=T3, the "anonymous" call
    # landed signed `T3`.
    os.environ["SWARM_PARENT_TASK"] = "0159"
    saved_agent = os.environ.pop("SWARM_AGENT", None)
    try:
        store.apply("withdraw", qid=q["id"], agent="", reason="stopped mattering")
        bad("an anonymous withdrawal from a fleet terminal is refused")
    except VerbError as e:
        truth("an anonymous withdrawal from a fleet terminal is refused", "signed" in str(e),
              str(e))
    finally:
        os.environ.pop("SWARM_PARENT_TASK", None)
        if saved_agent is not None:
            os.environ["SWARM_AGENT"] = saved_agent
    eq("so the question is still open, because the refusal was real", open_qids(), [q["id"]])

    r = store.apply("withdraw", qid=q["id"], agent="T2", reason="I answered it myself")
    eq("named, it lands", r["withdrawn_by"], "T2")
    eq("and the question leaves the queue", open_qids(), [])
    eq("`questions --withdrawn` still shows it: a withdrawal is not a deletion",
       [x["id"] for x in reads.questions(withdrawn=True, since_hours=24)], [q["id"]])
    with store.read("runtime") as sess:
        st = sess.scalar("SELECT state FROM brain.work_item WHERE id = %s", (tid,))
    eq("and the task it belonged to is NOT requeued by a withdrawal: it stays blocked", st,
       "blocked")


def test_withdraw_refuses_an_answered_question_and_a_second_withdrawal():
    reset()
    tid = task()
    store.apply("claim", agent="T2", lanes=["fabric"])
    q = store.apply("ask", question="which branch?", agent="T2", task=tid, default="")
    store.apply("answer", qid=q["id"], text="CUT FROM MAIN")
    try:
        store.apply("withdraw", qid=q["id"], agent="T2", reason="tidying")
        bad("a withdrawal cannot take back a decision the operator made")
    except VerbError as e:
        truth("a withdrawal cannot take back a decision the operator made",
              "already answered" in str(e), str(e))

    q2 = store.apply("ask", question="and the tag?", agent="T2", task=tid, default="")
    store.apply("withdraw", qid=q2["id"], agent="T2", reason="stopped mattering")
    try:
        store.apply("withdraw", qid=q2["id"], agent="T5", reason="tidying")
        bad("and a second withdrawal is refused rather than re-signed by whoever ran last")
    except VerbError as e:
        truth("and a second withdrawal is refused rather than re-signed by whoever ran last",
              "already withdrawn" in str(e), str(e))
    eq("so the first hand keeps the record", question_row(q2["id"])["withdrawn_by"], "T2")


def test_an_answer_arriving_after_a_withdrawal_is_refused():
    """The 16:47 incident, at the door. An operator answering a withdrawn question is a decision
    spent on nothing, so the verb refuses and says who withdrew it and why."""
    reset()
    tid = task()
    store.apply("claim", agent="T2", lanes=["fabric"])
    q = store.apply("ask", question="cut from main or from the release branch?", agent="T2",
                    task=tid, default="")
    store.apply("cancel", id=tid, reason="fabricated row", agent="T1", force=True)
    try:
        store.apply("answer", qid=q["id"], text="CUT FROM MAIN")
        bad("answering a withdrawn question is refused")
    except VerbError as e:
        truth("answering a withdrawn question is refused", "withdrawn by T1" in str(e), str(e))
    eq("and no answer was recorded", question_row(q["id"])["answer"], None)


# ================================================================ the other queue, and the pager

def test_the_human_queue_view_drops_it_too():
    """`swarm questions` is not the only surface. `brain.queue_open` arm 3 is the operator's CARD,
    and `brain.queue_pending_default` is what a checkpoint FIRES."""
    reset()
    tid = task()
    store.apply("claim", agent="T2", lanes=["fabric"])
    q = store.apply("ask", question="which branch?", agent="T2", task=tid,
                    default="cut from main")   # a stated default, so it is pending-default eligible
    with store.read("runtime") as s:
        before_card = s.query("SELECT source_id FROM brain.queue_open WHERE source_type='question'")
        before_def = s.query("SELECT question_id FROM brain.queue_pending_default")
    eq("before: the question is a card in the human queue", [r["source_id"] for r in before_card],
       [q["id"]])
    eq("before: and its default is eligible to fire at the next checkpoint",
       [r["question_id"] for r in before_def], [q["id"]])
    store.apply("cancel", id=tid, reason="fabricated row", agent="T1", force=True)
    with store.read("runtime") as s:
        after_card = s.query("SELECT source_id FROM brain.queue_open WHERE source_type='question'")
        after_def = s.query("SELECT question_id FROM brain.queue_pending_default")
    eq("after: no card", [r["source_id"] for r in after_card], [])
    eq("after: and nothing for silence to decide on a task that no longer exists",
       [r["question_id"] for r in after_def], [])


def test_one_withdrawn_event_per_question_so_the_page_can_be_taken_back():
    """The page outlives the question otherwise. `pages.log` retraction is driven by an event, and
    until this task the only event that could cancel a page was `question.answered` -- which is
    why q0140's page stood all afternoon and was finally cleared by the operator answering it."""
    reset()
    tid = task()
    store.apply("claim", agent="T2", lanes=["fabric"])
    q1 = store.apply("ask", question="which branch?", agent="T2", task=tid, default="")
    q2 = store.apply("ask", question="and the tag?", agent="T2", task=tid, default="")
    store.apply("cancel", id=tid, reason="fabricated row", agent="T1", force=True)
    rows = events(type="question.withdrawn")
    eq("one event per question withdrawn", sorted(r["subject_id"] for r in rows),
       sorted([q1["id"], q2["id"]]))
    if rows:
        p = json.loads(rows[0]["payload_summary"] or "{}")
        eq("carrying the hand that withdrew it", p.get("withdrawn_by"), "T1")
        eq("and it is an AI act, never a human one: nobody decided anything",
           rows[0]["actor_type"], "ai")
    eq("and no question.answered event was emitted, because nothing was answered",
       len(events(type="question.answered")), 0)


def test_the_pager_treats_a_withdrawal_as_a_retraction():
    """The subscriber half, in-process and with no database: the page is cancelled, not repeated.

    Property, not wiring: a pager that FORWARDED withdrawals would double the volume on the one
    channel whose stated failure mode is being muted for carrying traffic.
    """
    from subscribers.operator_paging import memory, policy       # noqa: E402
    truth("question.withdrawn is a retraction type", "question.withdrawn" in policy.RETRACTS)
    m = memory.PageMemory(log_path=Path(os.devnull))
    m.record_page(qid="q0140", event_seq=18, sent_at="2026-08-18T12:00:00Z")
    row = {"type": "question.withdrawn", "subject_id": "q0140",
           "payload_summary": json.dumps({"qid": "q0140", "withdrawn_by": "T1"})}
    prior = [m.outstanding("q0140")]
    d = policy.decision(row, prior=prior)
    truth("a withdrawal with a page outstanding sends a retraction", d["page"] and d["retract"], d)
    text = policy.render_retraction(row, prior)
    truth("and the retraction says WITHDRAWN, not answered -- nobody decided it",
          "withdrawn, NOT answered" in text, text)
    truth("and it is keyed so a restart can read it back as a cancellation",
          memory._PAGE_LINE.match("2026-08-18T12:05:00Z  " + text.splitlines()[0]) is not None,
          text.splitlines()[0])
    d2 = policy.decision(row, prior=[])
    eq("a withdrawal with NO page outstanding sends nothing (the volume bound)", d2["page"], False)


def test_end_to_end_the_page_is_taken_back_by_the_cancel():
    """cancel -> question.withdrawn -> the real consumer -> a retraction line in pages.log.

    The one test here that would have caught the whole defect end to end. q0140's page was raised
    at 11:58:04Z, its task was cancelled at ~12:10Z, and the page stood on the operator's phone
    until 16:47:30Z -- four hours and forty-nine minutes -- because the only event that could
    cancel a page was `question.answered` and nobody had answered it. What finally took it back
    was the answer itself, which is the cost rather than the fix.

    Skips rather than fails where the subscriber is not provisioned, the way the paging suite
    does: the property above stays checkable on a box without that role.
    """
    from subscribers.operator_paging import consumer, memory, policy, transport
    import tempfile
    reset()
    with tempfile.TemporaryDirectory() as d:
        log, drop = Path(d) / "pages.log", Path(d) / "drop.txt"
        old_log, old_drop = transport.LOCAL_LOG, transport.WINDOWS_DROP
        old_quiet = policy.QUIET_START_HOUR, policy.QUIET_END_HOUR
        transport.LOCAL_LOG, transport.WINDOWS_DROP = log, drop
        policy.QUIET_START_HOUR, policy.QUIET_END_HOUR = 3, 4     # pinned; the ask states no default
        try:
            tid = task()
            store.apply("claim", agent="T2", lanes=["fabric"])
            q = store.apply("ask", question="cut from main or from the release branch?",
                            agent="T2", task=tid, default="")
            c = consumer.OperatorPaging(transports=[transport.FileTransport()],
                                        page_memory=memory.PageMemory(log_path=log),
                                        log=lambda **kw: None)
            try:
                c.start()
            except Exception as exc:                              # noqa: BLE001
                print(f"  SKIP  the operator-paging subscriber is not provisioned on "
                      f"{os.environ['BRAIN_PG_DB']} ({type(exc).__name__}). "
                      f"store/bin/provision-subscriber.sh --db {os.environ['BRAIN_PG_DB']} "
                      f"--subscriber operator-paging")
                return
            try:
                c.drain()
                eq("the raised question paged the operator", c.paged, 1)
                store.apply("cancel", id=tid, reason="fabricated row", agent="T1", force=True)
                c.drain()
            finally:
                c.stop()
            eq("and the CANCEL took the page back", c.retracted, 1)
            eq("nothing landed on the never-page list", c.never, 0)
            text = log.read_text(encoding="utf-8")
            truth(f"the retraction is in pages.log for {q['id']}",
                  f"[question.withdrawn] {q['id']}" in text, text[-400:])
            truth("and it does not tell the operator his question was answered",
                  f"[question.answered] {q['id']}" not in text, text[-400:])
        finally:
            transport.LOCAL_LOG, transport.WINDOWS_DROP = old_log, old_drop
            policy.QUIET_START_HOUR, policy.QUIET_END_HOUR = old_quiet


# ================================================ the ledger this code will actually meet

#: The pre-31 fixture's database name, derived from the suite's own scratch name so two lanes
#: running this suite at once do not build the same one.
PRE31_DB = os.environ["BRAIN_PG_DB"] + "_pre31"

#: The version a schema file records, read out of its own INSERT and never off its filename --
#: `queue/schema/0014` is ledger 30 and `migrations/0029` is ledger 29, so the prefix is a
#: per-lane counter and sorting by it builds the wrong database. Same key `scratch-db.sh` uses.
_LEDGER_ROW = re.compile(r"INSERT INTO brain\.schema_migration \(version, *name\) *VALUES *\( *(\d+)")

#: Every lane that numbers into `brain.schema_migration`, matching `scratch-db.sh`'s own default.
_SCHEMA_DIRS = ("migrations", "budget/schema", "queue/schema")


def _recorded_version(path: Path):
    m = _LEDGER_ROW.search(" ".join(path.read_text(encoding="utf-8").split()))
    return int(m.group(1)) if m else None


def build_pre31_store(db: str) -> str:
    """A REAL store at ledger 30, built by the real applier from this tree minus migration 31.

    Not a store edited into shape. Dropping the columns out of a finished database would take the
    two views that select them with it, so the test would then be measuring `UndefinedTable` --
    a different absence, in a different place, from the one 119 of this host's 130 stores are
    actually in. This copies `scratch-db.sh` and every schema directory to a temp tree, removes
    every file whose OWN ledger row is 31 or higher, and runs that copy's `create`. The result is
    the store as it stood the moment before this task's migration, and the fixture stays honest
    when migration 32 lands.

    ~6s to build, so it is built per run rather than left lying around: this host already carries
    119 databases below the tip and a fixture nobody drops is the 120th.
    """
    tmp = Path(tempfile.mkdtemp(prefix="pre31-0159-"))
    (tmp / "engine/bin").mkdir(parents=True)
    shutil.copy2(ROOT / "engine/bin/scratch-db.sh", tmp / "engine/bin/scratch-db.sh")
    for d in _SCHEMA_DIRS:
        dst = tmp / d
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(ROOT / d, dst)
        for f in sorted(dst.glob("[0-9][0-9][0-9][0-9]_*.sql")):
            v = _recorded_version(f)
            if v is not None and v >= 31:
                f.unlink()
    env = {**os.environ, "ENGINE_SCRATCH_DB": db, "BRAIN_PG_DB": db}
    r = subprocess.run([str(tmp / "engine/bin/scratch-db.sh"), "create"],
                       env=env, capture_output=True, text=True)
    shutil.rmtree(tmp, ignore_errors=True)
    if r.returncode != 0:
        raise RuntimeError(f"could not build the pre-31 fixture: {r.stderr.strip()[-400:]}")
    # The builder's own "ready: N tables, schema_migration V" line, not its last line: it
    # prints the operator-login mapping after it, and the ledger it reports is the fact
    # this fixture is claiming.
    said = [ln for ln in r.stdout.splitlines() if "ready:" in ln]
    return said[-1].strip() if said else r.stdout.strip().splitlines()[-1]


def drop_store(db: str) -> None:
    subprocess.run([SCRATCH, "drop"], env={**os.environ, "ENGINE_SCRATCH_DB": db},
                   capture_output=True)


def test_a_store_below_ledger_31_still_serves_every_path_that_served_before_it():
    """The outage this feature caused, as a test. `docs/SCHEMA-TOLERANCE.md`, rules 1, 2, 3 and 5.

    On 2026-08-18 this code shipped into a tree whose live store was at ledger 27, and
    `transitions.py` read `q["withdrawn_at"]` out of a `SELECT *`. The query SUCCEEDED and the
    dict had no key, so `swarm answer` -- the operator's one verb -- raised `KeyError` for him and
    for every agent with eleven questions open in front of him, and `swarm questions`, `status`
    and `board` raised `UndefinedColumn` beside it. Every suite was green the whole time, because
    every suite ran at the tip. That is the hole this scene closes: it is the only test here that
    runs against a store WITHOUT the column, and rule 3 says a green run at the tip is not
    evidence of anything about the other 119 stores on this host.

    The asymmetry it pins down is rule 5. A path that served before migration 31 keeps serving and
    falls back to the pre-31 answer, which is a fact rather than a degradation: with nowhere to
    record a withdrawal, no question in that store has ever been withdrawn. `withdraw` is the one
    exception and refuses in a sentence, because it did not exist before 31, so nothing regresses
    when it stops -- and an agent told its question was withdrawn when nothing was written would
    walk away from a question still sitting in front of the operator.
    """
    print(f"  ..    building the pre-31 fixture {PRE31_DB} (~6s)")
    ready = build_pre31_store(PRE31_DB)
    live = os.environ["BRAIN_PG_DB"]
    try:
        os.environ["BRAIN_PG_DB"] = PRE31_DB
        db_schema._CACHE.clear()

        with store.read("runtime") as s:
            ledger = s.scalar("SELECT max(version) FROM brain.schema_migration")
            cols = s.scalar("SELECT count(*) FROM information_schema.columns WHERE "
                            "table_schema = 'brain' AND table_name = 'question' AND "
                            "column_name LIKE 'withdrawn%'")
        eq(f"the fixture is a real store at ledger 30 ({ready})", ledger, 30)
        eq("and brain.question genuinely has no withdrawn_* column", cols, 0)
        eq("so the probe every site asks says no", db_schema.question_withdrawal(), False)

        tid = task()
        store.apply("claim", agent="T2", lanes=["fabric"])
        q_answer = store.apply("ask", question="pre-31: which branch?", agent="T2",
                               task=tid, default="pre-31 default")
        q_stand = store.apply("ask", question="pre-31: and the other one?", agent="T2",
                              task=tid, default="pre-31 default")

        # THE READS. Each of these raised UndefinedColumn against a pre-31 store before this fix,
        # and `unpaged_questions` is the one behind `swarm status` and `swarm board`, which is why
        # one predicate took every agent's situational picture down rather than one panel.
        eq("`swarm questions` lists the open ones instead of raising",
           sorted(open_qids()), sorted([q_answer["id"], q_stand["id"]]))
        eq("`swarm status`/`board`'s unpaged detector runs", type(reads.unpaged_questions(0)), list)
        eq("the console's own open-questions read runs",
           len(store_reads.open_questions()), 2)
        eq("and the human queue's default scan, which decides on the operator's behalf, runs",
           type(checkpoints.due()), list)

        # THE VERB THE OPERATOR USES. This is the KeyError site, and `.get()` is the whole fix.
        store.apply("answer", qid=q_answer["id"], text="pre-31 answer")
        eq("`answer` records the answer instead of raising KeyError",
           question_row(q_answer["id"])["answer"], "pre-31 answer")

        # THE NEW SURFACE. Loud, in a sentence, naming the migration and the apply line.
        try:
            store.apply("withdraw", qid=q_stand["id"], agent="T2", reason="pre-31")
            bad("`withdraw` refuses on a store that cannot record a withdrawal")
        except VerbError as e:
            truth("`withdraw` refuses on a store that cannot record a withdrawal",
                  "0031_question_withdrawal.sql" in str(e) and "withdrawn_at" in str(e), str(e))
        except KeyError as e:
            bad("`withdraw` refuses on a store that cannot record a withdrawal",
                f"raised KeyError({e}) rather than saying why")

        # CANCEL, WHICH SERVED BEFORE THE CASCADE EXISTED AND MAY NOT STOP SERVING. It also may
        # not pretend: the question is still in the operator's queue and the row says so.
        r = store.apply("cancel", id=tid, reason="pre-31 cancel", agent="T2", force=True)
        eq("`cancel` still cancels the task", reads.task(tid)["state"], "cancelled")
        eq("it claims no withdrawal it did not make", r.get("withdrew"), [])
        eq("it names what it left standing instead", r.get("left_standing"), [q_stand["id"]])
        eq("and the question really is still in the operator's queue, which is the honest state",
           open_qids(), [q_stand["id"]])
        note = [t for t in reads.thread(tid) if "STILL IN THE OPERATOR'S QUEUE" in t["text"]]
        truth("the thread says so where the next reader is standing", len(note) == 1,
              str([t["text"][:90] for t in reads.thread(tid)]))
        truth("and it names the migration that would have closed it",
              bool(note) and "0031_question_withdrawal.sql" in note[0]["text"],
              note[0]["text"] if note else "")
    finally:
        os.environ["BRAIN_PG_DB"] = live
        db_schema._CACHE.clear()
        drop_store(PRE31_DB)


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print("test_cancel_withdraws_questions.py  --  a cancelled task leaves the operator's queue "
          "clean (0159)")
    print(f"  store: {os.environ['BRAIN_PG_DB']} (scratch)\n")
    for t in tests:
        print(f"{t.__name__.replace('test_', '')}: "
              f"{t.__doc__.splitlines()[0] if t.__doc__ else ''}")
        try:
            t()
        except Exception:                                         # noqa: BLE001
            import traceback
            bad(f"{t.__name__} raised", traceback.format_exc().splitlines()[-1])
        print()
    print(f"{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
