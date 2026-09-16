#!/usr/bin/env python3
"""Seed a scratch store with rows written by the REAL verbs, for rendering the console.

Not fixtures. Every row here is created by `store.apply(verb, ...)`, so what the console renders
is what the engine writes -- including the parts that are inconvenient. This docstring used to
name one of those: `done` replacing the posted brief in `work_item.result`, a real defect this
script did not paper over. Migration 14 fixed it, and task 0169 moved every surface onto the new
column, so the seeded rows now carry `brief` (the work order, write-once) and `result` (the
latest report, empty until a finisher runs) as two separate facts. The principle is unchanged and
is the reason this file uses the verbs: when the store is wrong, what renders here is wrong in
the same way, which is how the last one was found.

Refuses to run against the live `brain` database. Point it at a scratch one:

    BRAIN_PG_DB=brain_console python3 -m web.bin.seed_demo
"""

from __future__ import annotations

import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (_REPO, os.path.join(_REPO, "engine"), os.path.join(_REPO, "queue")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import store                                                            # noqa: E402
from swarm_engine import transitions as T                               # noqa: E402,F401
from swarm_engine import accept as A                                    # noqa: E402,F401
try:
    from human_queue import transitions as HQ                           # noqa: E402,F401
except ImportError:
    HQ = None

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if os.environ.get("BRAIN_PG_DB", "brain") == "brain":
    sys.exit("refusing to seed the live store. Set BRAIN_PG_DB to a scratch database.")

# `post` reads SWARM_PARENT_TASK from the environment when no parent is passed, and this script is
# normally run FROM a swarm terminal, which exports it. Inheriting it makes every seeded item a
# child of whatever task happened to run the seed, and the first `post` dies outright with
# "no such parent task: 0209" because that task does not exist in a scratch store. The seed's
# items are its own, so the inheritance is dropped here rather than by every caller remembering:
# `test_browser._reseed()` already popped it for its subprocess and a direct call did not, which
# is exactly the drift this line removes. Task 0209.
os.environ.pop("SWARM_PARENT_TASK", None)


def post(**kw) -> str:
    return store.apply("post", **kw)["id"]


def claim_the_row(agent: str, lane: str, expect: str) -> None:
    """`claim`, and then CHECK IT TOOK THE ROW THIS SCRIPT MEANT, which it did not for eight days.

    ROW 0391, 2026-08-27. `store.apply("claim", agent=..., lanes=[...])` names no task: it hands
    out the next claimable row and returns nothing to say it found none. Migration 26 gave
    `brain.work_item.agent_claimable` `NOT NULL DEFAULT false` -- *"a row nobody has classified is
    the operator's"* -- and every `post()` above passes the flag to nobody, so from that migration
    onward every claim in this file matched zero rows and did nothing. Measured on a freshly
    seeded store: `claimed_by` was `''` on all seven work items, and the two rows this file calls
    "a real agent report" were the OPERATOR's own work that happened to be `done`.

    WHAT THAT COST, AT THE SURFACE. `brain.queue_open`'s second arm is
    `WHERE NOT w.agent_claimable AND state IN ('inbox','active')`. `send back` dispatches `reopen`,
    which sets `state='inbox'` and touches `agent_claimable` not at all -- so a send back on one of
    these rows handed it straight back to the operator's own arm of the queue, under the same card
    id, now offering `Mark my task done`. The card did not leave, and `test_browser.py`'s
    `a gated verb leaves a receipt` reported that as a console defect for as long as it ran.
    Control arm, measured the same afternoon: the identical verb on a row posted with
    `agent_claimable=True`, claimed and finished by an agent, removes the card
    (`outputs/2026-08-27-G-console-defects/repro-0391-agent-owned-control.txt`).

    THE CHECK, AND NOT ONLY THE FLAG. Setting `agent_claimable=True` above fixes today's rows; a
    claim that silently matches nothing is the shape that let this sit unnoticed, and the shape
    will come back the next time a lane or a signal changes what is claimable. This file's own
    docstring says its whole method is that "when the store is wrong, what renders here is wrong
    in the same way, which is how the last one was found" -- a verb that quietly does nothing
    breaks exactly that. So the claim now has to land on the row it was written for, by name.
    """
    got = store.apply("claim", agent=agent, lanes=[lane], role="worker")
    got_id = (got or {}).get("id")
    if got_id != expect:
        sys.exit(
            f"seed-demo: `claim` by {agent} on lane {lane!r} took {got_id!r}, not {expect!r}. "
            f"This script's fixtures are built by the real verbs, so a claim that takes the wrong "
            f"row -- or none, which is what a store below migration 26 or a row left "
            f"`agent_claimable=false` produces -- seeds a review nobody worked and a console that "
            f"renders it as the operator's own task. Refusing to seed a store that misdescribes "
            f"the fleet.")


def main() -> int:
    # --- a review item: a real agent report with claims that do and do not carry evidence ----
    # `agent_claimable=True` because this is FLEET WORK and the flag is what says so. See
    # `claim_the_row` above for the eight days this row spent describing itself as an agent's
    # report while the store recorded it as the operator's own.
    review = post(
        title="Review the QC reconciliation harness",
        lane="qc", posted_by="commander", priority=4,
        workdir=REPO, agent_claimable=True,
        signals={"stakes": "high", "reversibility": "high", "urgency": "decaying",
                 "dependency_unblocking": "high", "effort": "high", "confidence": "medium",
                 "charter_alignment": "high"},
        canon_touching="true",
        body="## Definition of done\n"
             "- [x] every check maps to a source contract (verify-ai)\n"
             "- [x] no fixture-backed row counts as a pass (verify-ai)\n"
             "- [ ] Mick can reproduce the number in his view (verify-human)\n",
        canonical_task="infinity-os-consolidation#0088")
    claim_the_row("T4", "qc", review)
    store.apply("run start", id=review, attempt=1, agent="T4", host="wsl", session_id="s-demo-1")
    store.apply("artifact", id=review, path=f"{REPO}/migrations/COVERAGE.md", kind="report",
                agent="T4", exists=True, note="the verb-to-table coverage this review walked")
    store.apply("artifact", id=review, path=f"{REPO}/outputs/2026-08-16-qc/FINDINGS.md",
                kind="created", agent="T4", exists=True,
                note="recorded as present; it is not on disk, which is the point of the check")
    store.apply(
        "done", id=review, agent="T4",
        summary=(
            "- 79 correctness checks enumerated, listed in `migrations/COVERAGE.md`\n"
            "- 8 pass against real ground truth, output at "
            f"{REPO}/migrations/COVERAGE.md\n"
            "- 2 of those 8 are tautological: they compare a view against its own rollup\n"
            "- 0 of 27 plausibility checks ever produced a row, `psql -c \"select count(*)\"`\n"
            "- the remaining 71 are correctly marked NO BASIS\n"
            "- all checks verified and the harness is working as expected\n"))
    store.apply("run end", id=review, attempt=1, exit_code=0, outcome="done")

    # --- a second review whose round 1 was improper, so the trail has a REOPEN in it ---------
    trail = post(title="Reviewer flagged its own round-1 review as improper", lane="review",
                 posted_by="commander", priority=4, workdir=REPO, agent_claimable=True,
                 signals={"stakes": "high", "reversibility": "high", "urgency": "soon",
                          "dependency_unblocking": "medium", "effort": "medium",
                          "confidence": "high", "charter_alignment": "high"})
    claim_the_row("T6", "review", trail)
    store.apply("done", id=trail, agent="T6",
                summary="Round 1. Evidence path checks out. Accepting.")
    store.apply("reopen", id=trail, agent="operator",
                reason="Round 1 accepted on a path that does not exist. Six minutes for a "
                       "canon-touching review with eight evidence paths. Re-walk it and say "
                       "which paths you actually opened.")
    # THE RE-CLAIM AFTER THE REOPEN, which is the whole point of this fixture: `reopen`
    # puts the row back in `inbox` with `agent_claimable` untouched, so the fleet takes it
    # again and round 2 is a real second run rather than a second `done` on a row nobody
    # ever held.
    claim_the_row("T6", "review", trail)
    store.apply("artifact", id=trail, path=f"{REPO}/store/narrow_waist.md", kind="finding",
                agent="T6", exists=True, note="the argument the re-walk checked against")
    store.apply(
        "done", id=trail, agent="T6",
        summary=("- round 2 re-walked every evidence path: 8 paths checked, 1 missing\n"
                 "- round 1 accepted on a path that does not exist, see "
                 "`store/narrow_waist.md`\n"
                 "- seven of eight paths are real; the missing one carries the tautology claim\n"
                 "- every prior green in this lane is now suspect\n"))

    # --- questions, one with a stated default and one without --------------------------------
    blocked = post(title="Rotate the SP-API refresh token", lane="qc", posted_by="T3",
                   external="true", priority=5, workdir=REPO,
                   signals={"stakes": "high", "reversibility": "low", "urgency": "deadline",
                            "dependency_unblocking": "high", "effort": "low",
                            "confidence": "medium", "charter_alignment": "high"})
    store.apply("claim", agent="T3", lanes=["qc"], role="worker")
    store.apply("heartbeat", agent="T3", status="working", task=blocked, host="wsl")
    # The default here is a NULL BRANCH AND NOTHING ELSE, and it has to be: `blocked` is
    # `external`, so `brain.question_default_null_branch` (migration 0011) governs what silence
    # may ship. This seed used to say "stage only, no rotation", which the gate refuses -- not for
    # a verb, but for the residue `rotation`, a nominalised act naming the very thing the row is
    # about. Saying less is the fix: "no action" carries the same promise and names nothing.
    # Measured on brain_0209 at schema_migration 31: `stage only, no action` returns act_verbs {},
    # residue {}, bare_imperatives {} and default_is_null_branch_gated t. Task 0209.
    store.apply("ask", agent="T3", task=blocked,
                question="Rotate the SP-API refresh token now, or stage it for Monday?",
                default="stage only, no action")

    second = post(title="Confirm the brand pack slug", lane="brand", posted_by="T2",
                  priority=3, workdir=REPO)
    store.apply("claim", agent="T2", lanes=["brand"], role="worker")
    store.apply("ask", agent="T2", task=second,
                question="Confirm the Hooli brand pack slug: hooli-core?",
                default="use hooli-core")

    store.apply("ask", agent="admiral",
                question="OK to add a third Claude account to the rings?",
                default="")

    # --- the operator's OWN work: actor_type human, which is what makes `done` legitimate ----
    post(title="Call Mick back about the 2025 margin story", lane="client",
         posted_by="operator", actor_type="human", priority=4, workdir=REPO,
         signals={"stakes": "high", "reversibility": "high", "urgency": "decaying",
                  "dependency_unblocking": "low", "effort": "low", "confidence": "high",
                  "charter_alignment": "high"})

    # --- an already-answered default, so the Brief's DEFAULTED block has a real row ----------
    old = post(title="Hold the VSL or soften the homepage copy", lane="content",
               posted_by="T5", priority=2, workdir=REPO)
    store.apply("claim", agent="T5", lanes=["content"], role="worker")
    q = store.apply("ask", agent="T5", task=old,
                    question="Hold the VSL, or soften the homepage copy?",
                    default="hold the VSL, soften the homepage copy")
    store.apply("answer", qid=q["id"], text="hold the VSL, soften the homepage copy",
                requeue=False)

    # --- a recommendation, which is the queue's fourth arm and had never rendered -------------
    # `state` is 'open' on write and the CHECK on the column allows no other opening value. The
    # console read `WHERE state = 'pending'` until 2026-08-16, so this arm returned zero rows on
    # every screen it has ever drawn. Seeded here so the arm is rendered rather than assumed, with
    # a rationale, because the card renders a recommendation with no counterargument as the
    # finding it is and a seed with none would exercise only the finding.
    store.apply("recommend", subject_type="work_item", subject_id=review,
                text="Retire the 27 plausibility checks that have never produced a row",
                rationale="Against: they are the only checks that would catch a silent upstream "
                          "schema change, and never firing is what a passing check looks like. "
                          "Retiring them trades a real if unproven guard for a shorter list.",
                produced_by="T4", by="T4", requires_human=True)

    # --- the membrane: the fields the TIER is computed from -----------------------------------
    # The tier is derived, never declared (`human_queue/tiers.py`), so an item reaches Decide only
    # where a producer actually prepared the context and stated a recommended option, and only
    # where the item is reversible. Nothing below sets a tier; `queue classify` cannot.
    #
    # Without these rows this seed renders as 0 Decide / 0 Judge / 6 Shape, because an item with
    # no membrane row has "nothing to act or evaluate from" by that module's definition. That is
    # the classifier working, and it is also what an unprepared fleet's queue looks like.
    if HQ is not None:
        # A review is prepared context by construction: the report, its artifacts and the trail
        # are the thing to open. No recommended option, because accept-or-send-back is the
        # operator's call and a producer recommending its own acceptance is the rubber stamp.
        for wid in (review, trail):
            store.apply("queue classify", source_type="work_item", source_id=wid,
                        item_class="review", prepared_context_link=f"/task/{wid}", by="T4")

        # The fast lane, earned rather than declared: reversible, a stated default (which IS a
        # recommended option in everything but name), and a prepared context to act from.
        fast = post(title="Ship the Tuesday digest with the new subject line", lane="content",
                    posted_by="T5", priority=2, workdir=REPO,
                    signals={"stakes": "low", "reversibility": "high", "urgency": "soon",
                             "dependency_unblocking": "low", "effort": "low",
                             "confidence": "high", "charter_alignment": "medium"})
        store.apply("claim", agent="T5", lanes=["content"], role="worker")
        fastq = store.apply("ask", agent="T5", task=fast,
                            question="Send Tuesday's digest with the A subject line?",
                            default="send with A, the B variant loses on every past open rate")
        store.apply("queue classify", source_type="question", source_id=fastq["id"],
                    item_class="approval", prepared_context_link=f"/task/{fast}",
                    counterargument="B has never been tested on a Tuesday send.", by="T5")

    store.apply("heartbeat", agent="T1", status="working", host="wsl")
    store.apply("heartbeat", agent="T5", status="idle", host="wsl")

    with store.read() as s:
        print("work_item", s.scalar("SELECT count(*) FROM brain.work_item"),
              "question", s.scalar("SELECT count(*) FROM brain.question"),
              "thread", s.scalar("SELECT count(*) FROM brain.thread"),
              "artifact", s.scalar("SELECT count(*) FROM brain.artifact"),
              "agent", s.scalar("SELECT count(*) FROM brain.agent"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
