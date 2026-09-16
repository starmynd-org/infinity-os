"""The Scope elicitation flow: the drafter, the price, and the widening it no longer needs.

The design guide's open item 3 said the flow was specified and not built. This is what it takes
to say it is built: not that the screen renders, but that the three things the guide asks the
flow to do actually happen.

  1. The drafter proposes rows the operator can attack, and where it cannot honestly name a
     verifier it says so rather than inventing one.
  2. The price the screen quotes is computed from what the operator KEPT, not from what was
     drafted, and an unverifiable check is what makes it non-zero.
  3. The definition survives the work finishing. It used to cost a widening to get that: `post`
     wrote the body into `work_item.result`, `done` overwrote the same column, so the flow held
     `note` on its verb allowlist purely to copy the definition onto the append-only thread.
     Migration 14 gave the work order `work_item.brief`, write-once and overwritten by no verb,
     so the body survives on its own. The test that matters still runs `done` -- it just reads
     `brief` afterwards, and asserts the duplicate and the grant are both GONE.

THIS FILE NAMED ITS OWN EXIT CONDITION and task 0169 took it. The previous version asserted the
body LOSES its definition after `done`, with the message "If the store was fixed, this workaround
should be removed rather than left as dead weight." The store was fixed, so the four tests that
pinned the workaround now pin its removal instead. That is the direction of travel to preserve:
these tests exist to stop `note` drifting back into the Scope room, not to protect the copy.

Run:  BRAIN_PG_DB=<scratch> python3 -m web.tests.test_scoping

The runner exports SWARM_PARENT_TASK into the agent environment and `post` reads it as its
default parent, so a scratch store with no such row would fail every posting test with "no such
parent task". This file drops the variable at import (see below), so the command above is correct
from inside a claimed swarm task as well as from a bare shell -- no `env -u` wrapper needed.
"""

from __future__ import annotations

import os as _os
import sys as _sys
import traceback

_R = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
for _p in (_R, _os.path.join(_R, "engine"), _os.path.join(_R, "queue")):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

import store                                                            # noqa: E402
from web import claims as C, rooms, scoping                             # noqa: E402
from swarm_engine import transitions as _t                              # noqa: E402,F401
from swarm_engine import accept as _a                                   # noqa: E402,F401

# `post` inherits SWARM_PARENT_TASK from the environment, and an agent running this suite from
# inside a claimed task has it set to that task's id -- a row on the file bus and not in the
# scratch database this suite builds, so the two tests that post die on "no such parent task"
# and the suite is red exactly when the fleet is the thing running it. Every suite in
# queue/tests/ and engine/tests/ drops it for the same reason; the tests here post roots.
_os.environ.pop("SWARM_PARENT_TASK", None)

PASS, FAIL = [], []

CONCRETE = ("restate amazon_sales_traffic in gold_starmynd.infinite_ledger from 2026-01-01, "
            "check it against the export with `scripts/backfill_st.py`, within 2 percent")
VAGUE = "we need the Acme impressions thing sorted before Mick asks again"


def check(name, fn):
    try:
        fn()
        PASS.append(name)
        print(f"ok    {name}")
    except AssertionError as exc:
        FAIL.append((name, str(exc)))
        print(f"FAIL  {name}\n      {exc}")
    except Exception:                                                   # noqa: BLE001
        FAIL.append((name, traceback.format_exc()))
        print(f"ERROR {name}\n{traceback.format_exc()}")


def test_the_drafter_reads_the_intent_for_things_that_can_be_measured():
    rows = scoping.draft(CONCRETE)
    blob = " ".join(r["claim"] + r["check"] for r in rows)
    for needle in ("gold_starmynd.infinite_ledger", "scripts/backfill_st.py", "2026-01-01"):
        assert needle in blob, f"the drafter did not notice {needle!r} in the intent"
    assert all(r["could_pass_wrong"] for r in rows), \
        "a row with no statement of how it could pass while wrong has not been thought about"


def test_a_vague_intent_gets_a_row_that_admits_it_cannot_be_checked():
    rows = scoping.draft(VAGUE)
    human = [r for r in rows if r["verifier"] == scoping.VERIFY_HUMAN]
    assert human, "a vague intent produced no verify-human row; the drafter invented a verifier"
    assert human[0]["why_unverifiable"], "the row is unverifiable and does not say why"
    assert not human[0]["prove_without_you"], \
        "the drafter filled in 'how will an agent prove this' for an intent naming nothing"


def test_the_price_counts_what_was_kept_not_what_was_drafted():
    rows = scoping.draft(VAGUE)
    before = len(scoping.unverifiable(rows))
    assert before >= 1, "the vague intent should carry at least one unverifiable check"
    for r in rows:
        if r["verifier"] == scoping.VERIFY_HUMAN:
            r["kept"] = False
    assert scoping.unverifiable(rows) == [], "killing the unverifiable rows did not lower the count"
    assert "all machine-verifiable" in scoping.price(rows) or "all of them machine-verifiable" \
        in scoping.price(rows), scoping.price(rows)


def test_a_verify_ai_row_with_no_proof_counts_as_unverifiable():
    """The worse of the two failures: it claims a machine verifier and does not name one."""
    rows = [{"claim": "it works", "verifier": scoping.VERIFY_AI, "prove_without_you": "",
             "kept": True}]
    assert scoping.unverifiable(rows), \
        "a verify-ai row with an empty 'prove this without you' was counted as verifiable"


def test_the_rendered_body_states_the_auto_accept_disqualification():
    body = scoping.render(scoping.draft(CONCRETE))
    assert "disqualified from auto-accept" in body, (
        "an agent-drafted definition that does not say it disqualifies auto-accept is the "
        "loophole the guide names: an agent validating against a definition it wrote itself")


def test_the_rendered_body_parses_back_as_definition_of_done_rows():
    """The flow's output has to be the review view's input, or the loop does not close."""
    rows = scoping.draft(CONCRETE)
    parsed = C.definition_of_done(scoping.render(rows))
    kept = [r for r in rows if r["kept"]]
    assert len(parsed) == len(kept), f"wrote {len(kept)} rows, the review view reads {len(parsed)}"
    assert all(p["verifier"] in ("verify-ai", "verify-human") for p in parsed), \
        [p["verifier"] for p in parsed]


def test_the_scope_room_no_longer_needs_note():
    """The widening is GONE, and no room picked it up. Named in rooms.py's own comment.

    `note` was on the Scope room's allowlist for exactly one caller: the durable copy of the
    definition of done. Migration 14 removed the reason, task 0169 removed the grant. This
    asserts the narrowing in both directions -- the set is down to three verbs, and `note` did
    not reappear anywhere else while it was being moved.
    """
    assert "note" not in rooms.allowed("scope"), (
        "`note` is back on the Scope room's allowlist. It was there to copy the definition of "
        "done onto the thread because `done` destroyed the body; migration 14 made the body "
        "survive on its own, so a grant with no caller is a widening with no reason attached. "
        "If a new caller genuinely needs it, say so in rooms.py rather than restoring it here.")
    assert rooms.allowed("scope") == frozenset({"post", "intake", "accept"}), (
        f"the Scope room's verb set is {sorted(rooms.allowed('scope'))}, expected "
        "['accept', 'intake', 'post']")
    # THE QUEUE IS CARVED OUT, BY THE OPERATOR, ON 2026-08-31, AND THE CARVE-OUT IS NARROW.
    #
    # This loop's point was that `note` LEFT rather than MOVED: a verb quietly reappearing one room
    # over is a widening wearing a narrowing's clothes, and that is still what it checks for Brief,
    # Fleet and Study. The Queue is different now and the difference is a ruling rather than a
    # drift: MUST-NOT-BUILD item 5 was overruled by him for the voice note, in his own words, with
    # a condition that pays item 5's incident rather than waiving it. `web/MUST-NOT-BUILD.md` item
    # 5 carries the statement, the condition and its three checks.
    #
    # WHAT THIS TEST STILL OWES, AND IT IS THE HALF THAT MATTERS. The grant is one verb in one
    # room for one caller. So the carve-out is written as an exact set rather than as a hole: if
    # `note` ever appears in a SECOND room, or if the Queue's grant is ever widened to another verb
    # this file was protecting, this goes red exactly as it did before.
    noted = {r for r in rooms.ROOMS if "note" in rooms.allowed(r)}
    assert noted == {"queue"}, (
        f"`note` is reachable from {sorted(noted)}. It is granted to the QUEUE ALONE, by the "
        f"operator's overrule of MUST-NOT-BUILD item 5 on 2026-08-31, for the voice note and "
        f"nothing else. A second room means it moved again rather than being granted once.")
    for room in ("brief", "fleet", "study"):
        assert "note" not in rooms.allowed(room), \
            f"{room} gained `note` as the Scope room lost it; it was supposed to go, not move"
    assert rooms.allowed("study") == frozenset(), "Study is no longer at zero verbs"


def test_scope_post_no_longer_duplicates_the_definition_onto_the_thread():
    """The allowlist stops the verb; this stops the call site coming back without it.

    The two have to be checked separately. Dropping `note` from ROOM_VERBS makes the dispatch
    raise rather than silently write, so a restored duplicate would fail loudly -- but only if
    something exercises that path, and the point of the removal is that nothing does.
    """
    import inspect
    from web import actions
    src = inspect.getsource(actions.scope_post)
    assert 'rooms.dispatch(room, "note"' not in src, (
        "scope_post writes the durable thread copy again. That copy existed because `done` "
        "overwrote the posted body in `work_item.result`; migration 14 gave the work order its "
        "own write-once column and the copy has nothing left to protect.")


def test_the_definition_survives_done_in_the_body_itself():
    """The measured claim, re-measured against the fixed store. The inversion of the old test.

    This used to assert the body LOST its definition after `done` and that a thread copy carried
    it instead. Migration 14 is the fix that assertion was waiting for, so it now runs the same
    verbs and asserts the opposite: `brief` still holds the definition after `done`, `result`
    holds the summary instead of the work order, and NO thread copy was written -- the last of
    those being what proves the duplicate was removed rather than merely gone unused.
    """
    if _os.environ.get("BRAIN_PG_DB", "brain") == "brain":
        raise AssertionError("refusing to run against the live store; set BRAIN_PG_DB")
    from web import actions
    rows = scoping.draft(CONCRETE)
    out = actions.scope_post("scope", intent=CONCRETE, lane="qc", rows=rows, operator="operator")
    tid = out["task"]
    assert tid, "scope_post returned no task id"

    with store.read() as s:
        row = s.one("SELECT brief, result FROM brain.work_item WHERE id=%s", (tid,))
    assert C.definition_of_done(row["brief"]), "the definition is not in `brief` at post time"
    assert row["result"] == "", (
        f"a freshly posted task already has a result: {row['result']!r}. `post` stopped writing "
        "the body into `result` in task 0169; `result` means the latest report and a task "
        "nothing has happened to yet has none.")

    store.apply("claim", agent="T-scope-test", lanes=["qc"], role="worker")
    store.apply("done", id=tid, agent="T-scope-test", summary="did the thing, trust me")

    with store.read() as s:
        after = s.one("SELECT brief, result FROM brain.work_item WHERE id=%s", (tid,))
        notes = [r["text"] for r in s.query(
            "SELECT text FROM brain.thread WHERE work_item_id=%s AND kind='note'", (tid,))]
    assert C.definition_of_done(after["brief"]), (
        "the body lost its definition of done across `done`. That is the defect migration 14 "
        "exists to stop, and it is back.")
    assert after["brief"] == row["brief"], "`done` altered `brief`; the column is write-once"
    assert after["result"] == "did the thing, trust me", (
        f"`done` did not land its summary in `result`: {after['result']!r}")
    assert not [n for n in notes if C.definition_of_done(n)], (
        "the definition of done was duplicated onto the thread. That copy was a workaround for "
        "the body not surviving `done`; the body survives now, and a second copy of a work order "
        "is a second thing to disagree with the first.")


def test_the_review_view_reads_the_brief_column():
    """The console screen, on the item shape that used to defeat it: scoped, then finished."""
    if _os.environ.get("BRAIN_PG_DB", "brain") == "brain":
        raise AssertionError("refusing to run against the live store; set BRAIN_PG_DB")
    from web import actions, model
    out = actions.scope_post("scope", intent=CONCRETE, lane="qc",
                             rows=scoping.draft(CONCRETE), operator="operator")
    tid = out["task"]
    store.apply("claim", agent="T-scope-test-2", lanes=["qc"], role="worker")
    store.apply("done", id=tid, agent="T-scope-test-2", summary="no definition in here")
    rv = model.review(tid)
    assert rv["dod"], "the review view shows no definition of done for a scoped, finished item"
    assert not rv["dod_note"], (
        f"the screen is still apologising for a fixed defect: {rv['dod_note']!r}")
    assert C.definition_of_done(rv["brief"]), (
        "`review` no longer passes the work order to the template as `brief`. The detail page "
        "renders `rv.brief` in its own panel precisely so it cannot drift back onto `result`.")
    assert "dod_from" not in rv, (
        "`dod_from` is back. It labelled the definition as recovered FROM THE THREAD, which was "
        "only ever true while the body was being destroyed; it is now read straight from "
        "`brief` and there is no second location to disambiguate.")
    assert rv["item"]["result"] == "no definition in here", (
        "the item's `result` is not the agent's summary, which is the only thing it should ever "
        "hold now")


def main() -> int:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            check(name, fn)
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    _sys.exit(main())
