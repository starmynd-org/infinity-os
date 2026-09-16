#!/usr/bin/env python3
"""The posted work order survives the work finishing. Task 0138, child of 0118.

D00 rule 11 is "store full text, truncate only renderings", and this is the half of it that is
not about truncation. `brain.work_item` had exactly ONE free-text column, `result`, and two
different facts took turns in it: `post` wrote the work order there and `done`, `fail`, `block`
and `cancel` each replaced it with the report. The second write did not amend the first, it
erased it, and no other row in the database held a copy -- the thread's `post` event stores the
TITLE, never the body.

The consequence is not a cosmetic one. `engine/bin/swarm-run` builds a terminal's entire prompt
from `swarm show "$TASK_ID"`, so:

  * `reopen` -- the rejection verb the human-acceptance design rests on (D00 rule 3) -- sent
    attempt 2 back into the queue carrying attempt 1's SUMMARY as its work order. The task was
    re-worked from the text it had been rejected FOR, and the instruction it was rejected for
    missing no longer existed.
  * `fail` did the same with no operator in the loop at all, and it is the wider door: the runner
    reconciles EVERY unreported run to `fail`.

Migration 14 gives the work order a column of its own, write-once at the table.

WHAT THESE TESTS PIN, deliberately at the level of the rule rather than the implementation:

  1. Each finisher separately (`done`, `fail`, `block`, `cancel`) leaves `brief` byte-identical
     while `result` moves. Four separate assertions, not one, because `fail` reaches its own
     UPDATE statement and would have been missed by a test that only exercised `done` -- as the
     original report was: it named `done` + `reopen` and the `fail` door was found by measuring.
  2. The full round trip: post -> claim -> done -> reopen -> the brief is still dispatchable, and
     `swarm show` (the literal prompt builder) prints it. Asserted against the SUBPROCESS output,
     because a store-level assertion would pass even if the renderer dropped the column.
  3. Write-once is enforced by the TABLE, not by the absence of a caller. A direct UPDATE is
     refused.
  4. Nothing is truncated on the way in (rule 11's other half), over the 2000-char render limit.
  5. A task posted with no body does not acquire a fabricated brief.

Run: python3 engine/tests/test_brief_survives_finishers.py    (scratch database, never `brain`)
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
os.environ.pop("SWARM_PARENT_TASK", None)          # the live runner exports this; tests post roots

import store                                        # noqa: E402
from swarm_engine import reads, transitions          # noqa: E402,F401

SWARM = str(ROOT / "engine/bin/swarm")
SCRATCH = str(ROOT / "engine/bin/scratch-db.sh")

# The sentinel is a sentence rather than a token on purpose: it has to be recognisable in a
# rendered prompt, which is where the defect actually bit.
BRIEF = ("THE POSTED BRIEF. If this sentence is missing after a done+reopen, the work order was "
         "destroyed.\nStep 1: do the thing. Step 2: verify the thing.\n"
         "DO NOT close this by widening a test.")

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
    ok(msg) if got == want else bad(msg, f"wanted [{want!r}], got [{got!r}]")


def truth(msg, cond, detail=""):
    ok(msg) if cond else bad(msg, detail)


def reset():
    subprocess.run([SCRATCH, "truncate"], check=True, capture_output=True)


def sh(*args):
    return subprocess.run([SWARM, *args], capture_output=True, text=True, env={**os.environ})


def psql(sql):
    return subprocess.run([SCRATCH, "psql", "-tAc", sql], capture_output=True, text=True)


def post(body=BRIEF, **kw):
    kw.setdefault("lane", "t")
    kw.setdefault("title", "a task with a work order")
    kw.setdefault("agent_claimable", True)   # migration 26: these are fleet rows
    kw.setdefault("workdir", "/tmp")         # task 0100: a fleet row names its tree
    return store.apply("post", body=body, **kw)["id"]


# ------------------------------------------------------------------ 1. every finisher, separately

def test_each_finisher_leaves_the_brief_alone():
    """`done`, `fail`, `block` and `cancel` all write `result`. None of them may touch `brief`.

    One case per verb rather than one loop with a shared fixture, because the four take three
    different code paths: `done`, `block` and `cancel` go through `_finish`, while `fail`'s
    requeue branch has an UPDATE of its own. The original report named only `done`; `fail` was
    found by measuring each verb instead of trusting that one stood for all of them.
    """
    for verb, kwargs, moved_to in (
            ("done",   {"summary": "a summary that is not the brief"},
             "a summary that is not the brief"),
            ("fail",   {"reason": "attempt 1 ran out of road, use the other API next time"},
             "attempt 1 ran out of road, use the other API next time"),
            ("block",  {"reason": "blocked: the credential does not exist yet"},
             "blocked: the credential does not exist yet"),
            ("cancel", {"reason": "cancelled: the client withdrew the request"},
             "cancelled: the client withdrew the request"),
    ):
        reset()
        tid = post(title=f"{verb} must not eat the brief")
        store.apply("claim", agent="A1", lanes=["*"])
        # `cancel` on live work is --force by design; the others are the holder reporting.
        store.apply(verb, id=tid, agent="A1", force=(verb == "cancel"), **kwargs)
        t = reads.task(tid)
        eq(f"`{verb}` leaves the brief byte-identical", t["brief"], BRIEF)
        eq(f"`{verb}` still records its own text in `result`", t["result"], moved_to)
        truth(f"`{verb}` did not merely leave both columns equal (the report DID land)",
              t["result"] != t["brief"], f"result and brief are both {t['result']!r}")


# ------------------------------------------------------------------ 2. the round trip, rendered

def test_reopen_dispatches_attempt_two_against_the_brief():
    """The whole defect, end to end, asserted on what `swarm-run` would actually put in a prompt.

    `bin/swarm-run` does `"$SWARM" show "$TASK_ID"` between its banners, so this subprocess call
    is not a proxy for the prompt: it is the prompt. A store-level assertion would stay green if
    the renderer stopped printing the column.
    """
    reset()
    tid = post(title="rejected once, re-dispatched")
    store.apply("claim", agent="A1", lanes=["*"])
    store.apply("done", id=tid, agent="A1", summary="a summary that is not the brief")
    store.apply("reopen", id=tid, agent="operator", reason="rejected: you did not verify")

    t = reads.task(tid)
    eq("the task is back in the queue", t["state"], "inbox")
    eq("unspent, as `reopen` promises", t["attempts"], 0)
    eq("and its work order is intact in the store", t["brief"], BRIEF)

    r = sh("show", tid)
    truth("the prompt `swarm-run` builds contains the work order",
          "DO NOT close this by widening a test." in r.stdout,
          f"show printed: {r.stdout[:400]}")
    truth("and labels it as the work order rather than as a result",
          "brief (the posted work order)" in r.stdout, r.stdout[:400])
    truth("the rejected summary is still visible, marked as NOT the work order",
          "NOT the work order" in r.stdout and "a summary that is not the brief" in r.stdout,
          r.stdout[:400])

    # The regression this whole task exists to prevent, stated as the thing that must NOT be true:
    # the summary standing alone where the instructions should be.
    body = r.stdout.split("thread", 1)[0]
    truth("the summary does NOT stand in for the brief anywhere above the thread",
          "DO NOT close this by widening a test." in body, body[:400])


def test_fail_requeues_with_the_brief_not_the_failure_reason():
    """`fail` is the door the report did not name, and the runner opens it automatically.

    Every unreported run reconciles to `fail`, so this path needs no operator and fires far more
    often than `reopen`.
    """
    reset()
    tid = post(title="requeued by the runner, not by a human")
    store.apply("claim", agent="A1", lanes=["*"])
    store.apply("fail", id=tid, agent="A1", reason="the session hit a wall; retry with fewer reads")
    t = reads.task(tid)
    eq("`fail` requeued it", t["state"], "inbox")
    eq("and attempt 2's work order is the posted one", t["brief"], BRIEF)
    r = sh("show", tid)
    truth("the re-dispatched prompt carries the work order, not just the failure reason",
          "Step 1: do the thing." in r.stdout, r.stdout[:400])


# ------------------------------------------------------------------ 3. the table enforces it

def test_write_once_is_enforced_by_the_table():
    """"The defect is a column, not an assertion" -- so the guarantee is not this test's to keep.

    `post` is the only writer and no other verb names the column, so what this pins is the gate
    that catches the routes no verb owns: a future verb, a hand-written UPDATE, a lane that adds
    a re-brief tomorrow. Same posture as migration 12's parent-cycle trigger.
    """
    reset()
    tid = post(title="the table refuses the overwrite")

    r = psql(f"UPDATE brain.work_item SET brief = 'a replacement work order' WHERE id = '{tid}'")
    truth("a direct UPDATE of a non-empty brief is refused by the database",
          r.returncode != 0 and "refusing to overwrite the posted brief" in (r.stderr + r.stdout),
          f"rc={r.returncode} out={(r.stdout + r.stderr)[:300]}")
    eq("and the brief is unchanged after the refusal", reads.task(tid)["brief"], BRIEF)

    r = psql(f"UPDATE brain.work_item SET brief = '' WHERE id = '{tid}'")
    truth("blanking it is refused too -- that is the destruction, spelled differently",
          r.returncode != 0, f"rc={r.returncode} out={(r.stdout + r.stderr)[:300]}")

    r = psql(f"UPDATE brain.work_item SET brief = brief WHERE id = '{tid}'")
    truth("an idempotent rewrite of the same text is NOT an error",
          r.returncode == 0, f"rc={r.returncode} out={(r.stdout + r.stderr)[:300]}")

    # The one permitted direction: a row whose brief was destroyed before migration 14, or one
    # scoped after it was posted, can still be filled in. Without this the pre-existing rows
    # would be unrepairable, and a guard that makes damage permanent is not a fix.
    empty = post(title="posted with no body", body="")
    r = psql(f"UPDATE brain.work_item SET brief = 'filled in later' WHERE id = '{empty}'")
    truth("filling in an ABSENT brief is allowed, so a destroyed one stays repairable",
          r.returncode == 0, f"rc={r.returncode} out={(r.stdout + r.stderr)[:300]}")


# ------------------------------------------------------------------ 4. rule 11's other half

def test_the_brief_is_stored_whole():
    """Store full text; truncate only renderings. Over the render block limit, on purpose."""
    reset()
    big = "B" * 4000 + "\nTHE LAST LINE OF THE BRIEF, 4000 characters in."
    tid = post(title="a long work order", body=big)
    store.apply("claim", agent="A1", lanes=["*"])
    store.apply("done", id=tid, agent="A1", summary="short summary")

    eq("every byte of a 4000+ char brief is in the row, after `done`",
       len(reads.task(tid)["brief"]), len(big))
    eq("byte for byte", reads.task(tid)["brief"], big)

    r = sh("show", tid)
    truth("the default rendering marks the cut rather than pretending it is whole",
          "more chars" in r.stdout or "SHORTENED" in r.stdout, r.stdout[-500:])
    r = sh("show", tid, "--full")
    truth("`--full` prints the last line, so the whole text is reachable from the CLI",
          "THE LAST LINE OF THE BRIEF, 4000 characters in." in r.stdout, r.stdout[-300:])


# ------------------------------------------------------------------ 5. no invented briefs

def test_a_bodyless_post_gets_no_brief():
    """Empty must read as empty. A fabricated work order is worse than an absent one."""
    reset()
    tid = post(title="no body was posted", body="")
    eq("a task posted with no body has an empty brief", reads.task(tid)["brief"], "")
    store.apply("claim", agent="A1", lanes=["*"])
    store.apply("done", id=tid, agent="A1", summary="did the thing")
    eq("and `done` does not back-fill one from the summary", reads.task(tid)["brief"], "")
    r = sh("show", tid)
    truth("the rendering does not print an empty brief heading",
          "brief (the posted work order)" not in r.stdout, r.stdout[:300])


TESTS = [test_each_finisher_leaves_the_brief_alone,
         test_reopen_dispatches_attempt_two_against_the_brief,
         test_fail_requeues_with_the_brief_not_the_failure_reason,
         test_write_once_is_enforced_by_the_table,
         test_the_brief_is_stored_whole,
         test_a_bodyless_post_gets_no_brief]


def main():
    print("the posted brief survives the work finishing (task 0138)")
    for t in TESTS:
        print(f"\n{t.__name__}")
        try:
            t()
        except Exception as e:                                    # noqa: BLE001
            bad(f"{t.__name__} raised", f"{type(e).__name__}: {e}")
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
