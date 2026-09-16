#!/usr/bin/env python3
"""The join: a question raised through ANY surface becomes an event. Task 0140.

D9's acceptance run (`outputs/2026-08-16-D9-acceptance/ACCEPTANCE-RUN.md`, hop 8) measured this
against the live store with the paging subscriber listening:

    before q0028:  max(event_seq)=54  count(event)=52
    after  q0028:  max(event_seq)=54  count(event)=52

Both lanes had passed their own definition of done. `engine/swarm_engine/cli.py:_page` printed one
line on stderr and said so; `fabric/producers/questions.py` read
`$SWARM_HOME/operator/open/<qid>.json`, the file bus, and could not see a row in `brain.question`.
Each half was correct against a different bus and nobody owned the join.

This file is what stops it reopening, and it tests the property rather than the wiring:

  **A question that reaches `brain.question` reaches `brain.event`, whoever raised it.**

That phrasing is deliberate. A test that asserted "cli.py calls the producer" would pass while
the MCP server, the console and `fail`'s own out-of-attempts question all paged nobody -- which is
exactly the defect one layer up. So every test below goes through `store.apply` and NONE of them
imports the producer, because the join is at the narrow waist and a caller is not supposed to know
it is there.

Run: python3 engine/tests/test_question_paging_join.py   (against the scratch database, never
`brain`)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
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
# The hooks are on by default. Say so explicitly here anyway: a suite that inherited
# BRAIN_AFTER_COMMIT_HOOKS=0 from a shell would report the seam as fixed while measuring nothing.
os.environ["BRAIN_AFTER_COMMIT_HOOKS"] = "1"

import store                                                     # noqa: E402
from swarm_engine import transitions                             # noqa: E402,F401  (registers)
from swarm_engine.transitions import VerbError                   # noqa: E402

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


def events(**where):
    sql = "SELECT * FROM brain.event"
    params = []
    if where:
        sql += " WHERE " + " AND ".join(f"{k} = %s" for k in where)
        params = list(where.values())
    sql += " ORDER BY event_seq"
    with store.read("runtime") as s:
        return s.query(sql, params)


def payload(row) -> dict:
    return json.loads(row["payload_summary"] or "{}")


def task(**post_kw) -> str:
    post_kw.setdefault("agent_claimable", True)   # migration 26: these are fleet rows
    post_kw.setdefault("workdir", "/tmp")         # task 0100: a fleet row names its tree
    return store.apply("post", lane="join", title="a task", posted_by="commander",
                       **post_kw)["id"]


# ================================================================ the seam itself

def test_ask_emits_a_question_raised_event():
    """The measurement D9 ran, inverted: the count moves."""
    reset()
    before = len(events())
    tid = task()
    q = store.apply("ask", question="which branch do I cut from?", agent="T1", task=tid,
                    default="cut from main")
    after = events(type="question.raised")
    eq("the store had no events before the ask", before, 0)
    eq("one question.raised event exists after it", len(after), 1)
    if not after:
        return
    e = after[0]
    eq("it names the question", e["subject_id"], q["id"])
    eq("its subject_type is question", e["subject_type"], "question")
    eq("it joins to the asking task through work_item_id (the FK, not a string in a summary)",
       e["work_item_id"], tid)
    p = payload(e)
    eq("the payload carries the question text a human has to read", p.get("question"),
       "which branch do I cut from?")
    eq("and the stated default, which is what lets him decide from the lock screen",
       p.get("default_if_unanswered"), "cut from main")
    eq("and who asked", p.get("from"), "T1")
    # The message says "a file-bus path" rather than spelling the path out. `test_contract.py`
    # walks the AST of every engine python file for a string literal naming the live bus in a
    # call argument, and it cannot tell a path from a sentence about a path, so the literal here
    # turned the isolation rule permanently red (task 0175). Keep the wording; the rule it trips
    # is the one that stops the store port from quietly reading the file bus, and a gate that is
    # always 1-failed is one people learn to read past.
    truth("payload_ref points at the bus the question is actually on",
          e["payload_ref"] == f"brain.question:{q['id']}",
          f"payload_ref was {e['payload_ref']!r}; a file-bus path would send a human to a "
          f"file that does not exist for a store question")


def test_the_producer_reads_the_store_and_not_the_file_bus():
    """The direct falsifier of the original defect, with the file bus emptied out from under it.

    If the producer were still reading `$SWARM_HOME/operator/open/<qid>.json`, this event would
    carry an empty question and an empty default -- the shape it had all morning -- rather than
    failing loudly. So the assertion is on the CONTENTS, not on the row count.
    """
    reset()
    with tempfile.TemporaryDirectory() as empty:
        old = os.environ.get("SWARM_HOME")
        os.environ["SWARM_HOME"] = empty          # a file bus with nothing in it at all
        try:
            tid = task()
            store.apply("ask", question="the store is the only place this text exists",
                        agent="T4", task=tid, default="hold")
        finally:
            if old is None:
                os.environ.pop("SWARM_HOME", None)
            else:
                os.environ["SWARM_HOME"] = old
    rows = events(type="question.raised")
    eq("an event was still emitted with no file bus present", len(rows), 1)
    if rows:
        eq("and it carries the text, which only Postgres had",
           payload(rows[0]).get("question"), "the store is the only place this text exists")


def test_the_hard_flag_travels_ef7():
    """A question raised from a flagged task is a flagged event. Both directions, not just one."""
    reset()
    ext = task(external=True)
    plain = task()
    qe = store.apply("ask", question="send the client the correction?", agent="T1", task=ext,
                     default="hold")
    qp = store.apply("ask", question="rebuild the view?", agent="T1", task=plain,
                     default="rebuild")
    by_qid = {e["subject_id"]: e for e in events(type="question.raised")}
    truth("both questions produced events", len(by_qid) == 2, f"got {sorted(by_qid)}")
    if len(by_qid) != 2:
        return
    eq("the flagged task's question is flagged (EF-7 inherits by OR)",
       by_qid[qe["id"]]["external"], True)
    eq("and the unflagged task's question is NOT, so the gate is a gate and not a wall",
       by_qid[qp["id"]]["external"], False)


def test_fail_out_of_attempts_pages_too():
    """The question nobody types. A CLI call site would have missed exactly this one.

    It is also the 3am one: a task that burns its last attempt at 02:00 raises a question no
    human is present to see, which is the case the always-on claim rests on.
    """
    reset()
    tid = task(max_attempts=1)
    store.apply("claim", agent="T1", lanes=["join"])
    r = store.apply("fail", id=tid, reason="cannot reach the API", agent="T1")
    truth("the verb raised a question for itself", bool(r.get("question")), f"fail returned {r}")
    rows = events(type="question.raised")
    eq("and that question produced an event", len(rows), 1)
    if rows and r.get("question"):
        eq("naming the auto-raised question", rows[0]["subject_id"], r["question"])


def test_answer_emits_the_retraction():
    """`question.answered` exists so a pager can retract instead of re-paging."""
    reset()
    tid = task()
    q = store.apply("ask", question="which branch?", agent="T1", task=tid, default="main")
    store.apply("answer", qid=q["id"], text="cut from main")
    rows = events(type="question.answered")
    eq("one question.answered event", len(rows), 1)
    if rows:
        eq("naming the question that was answered", rows[0]["subject_id"], q["id"])


# ================================================================ the hook's own contract

def test_a_failing_hook_cannot_fail_the_verb():
    """A pager that is down must not turn a landed `ask` into an error the caller retries.

    The retry is the damage: it raises the same question a second time, so the operator gets two
    pages for one decision and the second one has a different id.
    """
    reset()
    tid = task()

    def explode(verb, result, kwargs):
        raise RuntimeError("the pager is down")

    store.transitions.after_commit("ask", explode)
    try:
        q = store.apply("ask", question="does this land?", agent="T1", task=tid, default="hold")
        truth("the verb returned normally with a hook raising underneath it", bool(q.get("id")))
        with store.read("runtime") as s:
            row = s.one("SELECT id FROM brain.question WHERE id = %s", (q["id"],))
        truth("and the question row is really there", bool(row))
        eq("and the good hook still ran, because one bad hook does not cancel the others",
           len(events(type="question.raised")), 1)
    except Exception as e:                                        # noqa: BLE001
        bad("the verb returned normally with a hook raising underneath it", repr(e))
    finally:
        store.transitions._AFTER_COMMIT["ask"].remove(explode)


def test_a_rolled_back_verb_pages_nobody():
    """After commit means after commit. A question that does not exist must not page a human."""
    reset()
    before = len(events())
    try:
        store.apply("ask", question="about a task that is not there", agent="T1",
                    task="9999", default="hold")
        bad("asking against a missing task is refused")
    except VerbError:
        ok("asking against a missing task is refused")
    eq("and nothing was emitted for the question that never existed", len(events()), before)


def test_the_join_is_at_the_waist_and_not_in_a_surface():
    """Structure, because the failure this file exists for is architectural.

    Nothing above imports the producer. The tests still pass, which is only possible if the join
    is somewhere every caller already goes through. This pins that, and pins the corollary: no
    surface may call the producer directly, because a second call site is a second path and the
    two would drift the way the two buses did.
    """
    src = (ROOT / "store/transitions.py").read_text(encoding="utf-8")
    truth("store/transitions.py names the question producer as a hook module",
          "fabric.producers.questions" in src,
          "the lazy import list is how every surface gets the join without naming it")
    truth("and it runs hooks only after commit",
          "_run_after_commit(verb, result, kwargs)" in src
          and src.index("conn.commit()") < src.index("_run_after_commit(verb, result, kwargs)"))
    surfaces = ["engine/swarm_engine/cli.py", "mcp/tools.py", "web/actions.py"]
    offenders = [f for f in surfaces
                 if (ROOT / f).exists()
                 and "producers.questions" in (ROOT / f).read_text(encoding="utf-8")]
    eq("no surface calls the producer directly", offenders, [])
    truth("`fabric.producers.questions` is importable, or every hook is silently a no-op",
          _producer_imports(), "the hook module failed to import; questions would page nobody")


def _producer_imports() -> bool:
    """In a SUBPROCESS, deliberately.

    Importing the producer here would register its hooks into this process, and then every test
    that ran after this one would pass whether or not `store` wires the join at all. A check that
    repairs the thing it is checking is worse than no check: it turns the suite green in exactly
    the configuration the suite exists to catch. Measured -- with `_HOOK_MODULES` emptied, an
    in-process import took this file from 6 failures to 5 and made the last test meaningless.
    """
    r = subprocess.run([sys.executable, "-c", "import fabric.producers.questions"],
                       cwd=str(ROOT), capture_output=True, text=True)
    return r.returncode == 0


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print("test_question_paging_join.py  --  a question raised anywhere becomes an event (0140)")
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
