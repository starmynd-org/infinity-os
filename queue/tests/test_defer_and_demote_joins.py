#!/usr/bin/env python3
"""The two joins nobody owned: a defer's wake condition, and the tier a demote demotes FROM.

Task 0158, from T5's measured UX review in task 0129. Both bugs have the same shape and it is
worth naming, because the shape is what a rewrite reintroduces: **D6b owns the write, D7 owns
the read, and the join between them was never a test.** Both verbs wrote a correct, durable,
well-constrained row. Neither row changed what the operator saw next.

WHAT THE EXISTING SUITE COULD NOT CATCH, and why. `test_the_not_fast_demote_logs_a_miss_against_
the_producer` in `test_queue_mechanics.py` demotes a work_item that has been fully classified:
`item_class`, `template_id`, `prepared_context_link` and `recommended_option` are all set on
`brain.queue_item`. In exactly that case the overlay alone carries every tier input, so the
demote's private tier computation agrees with the read's by coincidence and the miss row reads
`decide -> judge`. Every case where a tier input lives somewhere OTHER than the overlay -- a
question's option derived from its stated default, an arm's `default_item_class`, a question's
reversibility inherited from the task it hangs off -- disagreed silently. That is one test
passing forever over a classifier that was wrong for three of the four arms.

Run:  python3 queue/tests/test_defer_and_demote_joins.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "queue"))
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT))

os.environ.setdefault("BRAIN_PG_DB", os.environ.get("QUEUE_SCRATCH_DB", "brain_queue_scratch"))
os.environ.pop("SWARM_PARENT_TASK", None)

# Build or reconcile the scratch database BEFORE this suite asserts anything. Task 0212. Running a
# suite by itself is what a brief asks for, and by itself this one used to die in reset() on a
# TRUNCATE against a database nobody had built.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _scratch_preflight import reconcile                 # noqa: E402
reconcile(os.environ["BRAIN_PG_DB"])

import store                                             # noqa: E402
from human_queue import reads                            # noqa: E402
from human_queue import transitions as queue_transitions  # noqa: E402,F401
from swarm_engine import transitions as engine           # noqa: E402,F401

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


def reset():
    subprocess.run([SCRATCH, "psql", "-q", "-c",
                    "TRUNCATE brain.queue_item, brain.queue_defer, brain.queue_bump, "
                    "brain.queue_calibration, brain.queue_default_event, brain.recommendation, "
                    "brain.thread, brain.question, brain.agent, brain.work_item CASCADE; "
                    "SELECT setval('brain.item_id_seq', 1, false);"],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _sql(sql: str):
    """Direct psql, for TEST SETUP ONLY: moving a clock backwards.

    There is no verb for `pretend two hours passed`, and there should not be one. The alternative
    is a two-hour test.
    """
    subprocess.run([SCRATCH, "psql", "-q", "-c", sql], check=True, capture_output=True)


# --------------------------------------------------------------------------- defer

def test_a_timed_defer_ends_when_its_time_comes():
    """`+2h` must mean absent for two hours, not absent forever.

    The receipt the console prints is "it wakes when the condition is met". Nothing in this
    system calls `queue wake` except a human typing it at the CLI -- measured by grep across
    every .py, .sh and .md in the repo, which returns the transition, the CLI subcommand that
    wraps it, and one docstring listing the verbs. So if the READ does not honour the wake
    condition, no wake condition is ever honoured, and every timed defer is a permanent
    disappearance wearing a timer.
    """
    reset()
    # `workdir` is required of any agent-claimable row since task 0100: an empty one made
    # the runner cd to the agent profile's own directory. `/tmp` because these fixtures
    # never run anything there, only post; the gate asks for absolute, not for existing.
    t = store.apply("post", title="approve the restated deck", lane="client", workdir="/tmp",
                    signals={"reversibility": "high"}, agent_claimable=True)
    store.apply("claim", agent="T5", lanes=("client",))
    store.apply("done", id=t["id"], summary="drafted", agent="T5")
    before = reads.queue(window=50)
    check("the finished item is in the queue to start with",
          before["totals"]["open"] == 1 and before["totals"]["deferred"] == 0,
          str(before["totals"]))

    store.apply("queue defer", source_type="work_item", source_id=t["id"], kind="until-time",
                wake_at=datetime.now(timezone.utc) + timedelta(hours=2), by="operator")
    during = reads.queue(window=50)
    check("deferred until +2h it leaves the tier and the count drops",
          during["totals"]["open"] == 0 and during["totals"]["deferred"] == 1,
          str(during["totals"]))
    check("and it is counted and named, never silently dropped",
          [i["source_id"] for i in during["deferred_items"]] == [t["id"]])

    # The clock, moved. Equivalent to waiting two hours and one minute.
    _sql("UPDATE brain.queue_defer SET wake_at = now() - interval '1 minute', "
         "created_at = now() - interval '2 hours'")
    after = reads.queue(window=50)
    check("WHEN THE WAKE TIME PASSES IT COMES BACK: the count returns",
          after["totals"]["open"] == 1 and after["totals"]["deferred"] == 0,
          str(after["totals"]))
    ids = [i["source_id"] for tier in after["tiers"].values() for i in tier["items"]]
    check("and it is on a tier again, which is the only place the operator looks",
          ids == [t["id"]], str(ids))


def test_an_elapsed_defer_still_counts_toward_the_third():
    """The write counts every defer ever made. The read must count the same ones.

    `queue defer` refuses the third by counting EVERY prior row of a postponing kind, woken or
    not. The console's `defers` used to count only rows with `woke_at IS NULL`, so the two halves
    disagreed the moment a defer elapsed: the card offered the time chips back, the operator
    tapped one, and the verb refused with `deferred 2 times already`. A control that is offered
    and then refused is worse than one that was never offered.
    """
    reset()
    t = store.apply("post", title="the twice-deferred item", lane="ops", actor_type="human")
    for _ in range(2):
        store.apply("queue defer", source_type="work_item", source_id=t["id"], kind="until-time",
                    wake_at=datetime.now(timezone.utc) + timedelta(hours=2), by="operator")
    _sql("UPDATE brain.queue_defer SET wake_at = now() - interval '1 minute'")
    q = reads.queue(window=50, include_deferred=True)
    item = [i for tier in q["tiers"].values() for i in tier["items"] if i["source_id"] == t["id"]]
    check("both elapsed defers are still on the card", item and item[0]["defers"] == 2,
          str(item[0]["defers"]) if item else "item missing")

    err = ""
    try:
        store.apply("queue defer", source_type="work_item", source_id=t["id"], kind="until-time",
                    wake_at=datetime.now(timezone.utc) + timedelta(hours=2), by="operator")
    except Exception as e:                                          # noqa: BLE001
        err = str(e)
    check("and the verb still refuses the third, so read and write agree",
          "third defer" in err or "deferred 2 times" in err, err)


def test_a_defer_until_a_question_ends_when_the_question_lands():
    """"wakes when it lands" is a claim about a work_item's state. It is checkable, so check it.

    This is the kind no ordinary task manager can have: the deferral posts an agent task and the
    item comes back CHEAPER rather than older. That promise is only kept if the read notices the
    task landed.
    """
    reset()
    t = store.apply("post", title="decide the pricing tier", lane="client", actor_type="human")
    out = store.apply("queue defer", source_type="work_item", source_id=t["id"],
                      kind="until-question", question="what does Mick currently pay?",
                      lane="client", by="operator")
    wake_task = out["wake_task"]
    check("the defer posted the task that will unblock it", bool(wake_task), str(out))
    during = reads.queue(window=50)
    check("while that task is open the item is out of the tier",
          t["id"] not in [i["source_id"] for tier in during["tiers"].values()
                          for i in tier["items"]], str(during["totals"]))

    store.apply("claim", agent="T9", lanes=("client",))
    store.apply("done", id=wake_task, summary="he pays 8k", agent="T9")
    after = reads.queue(window=50)
    check("WHEN THE TASK LANDS THE ITEM COMES BACK",
          t["id"] in [i["source_id"] for tier in after["tiers"].values()
                      for i in tier["items"]], str(after["totals"]))


def test_an_until_event_defer_is_still_only_woken_explicitly():
    """The one wake condition the store cannot evaluate, left alone and said out loud.

    An until-event defer names an event type that nothing in this repo emits into a table the
    read could join. Guessing would be worse than waiting: `doctor()` already reports one that
    has not fired in 14 days as a `dead-wake-condition`, which is the honest handling.
    """
    reset()
    t = store.apply("post", title="ship when the contract is signed", lane="client",
                    actor_type="human")
    store.apply("queue defer", source_type="work_item", source_id=t["id"], kind="until-event",
                wake_event_type="contract.signed", by="operator")
    q = reads.queue(window=50)
    check("an until-event defer holds the item until something wakes it",
          q["totals"]["open"] == 0 and q["totals"]["deferred"] == 1, str(q["totals"]))
    with store.read("runtime") as s:
        did = s.scalar("SELECT id FROM brain.queue_defer WHERE woke_at IS NULL")
    store.apply("queue wake", defer_id=did, by="system")
    q2 = reads.queue(window=50)
    check("and an explicit wake returns it", q2["totals"]["open"] == 1, str(q2["totals"]))


# --------------------------------------------------------------------------- demote

def test_the_demote_demotes_from_the_tier_the_operator_actually_SAW():
    """A question in Decide, demoted. The miss must read `decide -> judge`, and it must move.

    This is T5's q0016, reproduced. The question reaches Decide the way every question does: its
    stated default IS its recommended option (`reads.queue`), a template makes it actionable, and
    the reversibility of the task it hangs off clears the floor. NONE of those three inputs is on
    `brain.queue_item`, so a demote that computed the current tier from the overlay alone saw an
    item with no option, no class and no reversibility, called it Shape, and demoted Shape to
    Shape. The operator watched a Decide item and the dataset recorded `shape -> shape`: a
    calibration MISS row that cannot express a miss, in the table whose entire purpose is
    measuring producer miscalibration.
    """
    reset()
    t = store.apply("post", title="the pricing change", lane="client",
                    signals={"reversibility": "high"})
    q = store.apply("ask", question="ship the 12 percent uplift on the September cycle?",
                    agent="T2", task=t["id"], default="yes, ship it")
    qid = q["id"]
    store.apply("queue classify", source_type="question", source_id=qid,
                template_id="playbook-price-change")
    before = reads.why("question", qid)
    check("the question is in Decide: stated default, template, reversible task",
          before["tier"] == "decide", f"{before['tier']}: {before['tier_reason']}")

    out = store.apply("queue demote", source_type="question", source_id=qid, by="operator")
    check("one tap moves it to Judge and not past it to Shape",
          out["was"] == "decide" and out["tier"] == "judge", str(out))
    with store.read("runtime") as s:
        miss = s.one("SELECT * FROM brain.queue_calibration WHERE kind = 'not-fast'")
    check("the miss row records the tier it was DECLARED in and the tier it MOVED to",
          miss and miss["declared"] == "decide" and miss["observed"] == "judge", str(miss))
    check("THE TWO DIFFER, which is the only way a miss row can express a miss",
          miss["declared"] != miss["observed"], str(miss))
    check("and it is logged against the producer who declared it", miss["producer"] == "T2",
          str(miss["producer"]))

    after = reads.why("question", qid)
    check("the next read puts it in Judge with the demote as its reason",
          after["tier"] == "judge" and "not fast" in (after["tier_reason"] or ""),
          f"{after['tier']}: {after['tier_reason']}")


def test_the_demote_reads_the_arms_own_default_item_class():
    """A finished task is `review` by the ARM it came from, and nothing writes that to the overlay.

    `brain.queue_open` types arm 1 as `default_item_class = 'review'`; `review` is in
    NEVER_DECIDE_CLASSES, because a review means open the artifact and evaluate it whatever else
    the item carries. So this item is Judge on the screen even though it has an option, a context
    link and a reversible signal. A demote reading the overlay alone saw no class at all, called
    it DECIDE, and demoted Decide to Judge: the item did not move, and the dataset recorded a
    producer missing a call the producer had not made.
    """
    reset()
    t = store.apply("post", title="rebuilt the margin table", lane="data", workdir="/tmp",
                    signals={"reversibility": "high"}, agent_claimable=True)
    store.apply("claim", agent="T7", lanes=("data",))
    store.apply("done", id=t["id"], summary="rebuilt", agent="T7")
    store.apply("queue classify", source_type="work_item", source_id=t["id"],
                template_id="playbook-accept-table", prepared_context_link="outputs/margin.md",
                recommended_option="accept: 12 of 12 months tie")
    before = reads.why("work_item", t["id"])
    check("it is Judge, from the arm's class and not from the overlay",
          before["tier"] == "judge" and "review" in (before["tier_reason"] or ""),
          f"{before['tier']}: {before['tier_reason']}")

    out = store.apply("queue demote", source_type="work_item", source_id=t["id"])
    check("`not fast` on a Judge item moves it to Shape", out["was"] == "judge"
          and out["tier"] == "shape", str(out))
    with store.read("runtime") as s:
        miss = s.one("SELECT * FROM brain.queue_calibration WHERE kind = 'not-fast'")
    check("and the miss reads judge -> shape, not decide -> judge",
          miss["declared"] == "judge" and miss["observed"] == "shape", str(miss))
    after = reads.why("work_item", t["id"])
    check("the item is in Shape on the next read", after["tier"] == "shape", after["tier_reason"])


def test_not_fast_on_a_shape_item_is_refused_rather_than_logged_as_a_zero_miss():
    """Shape is the bottom. A demote from it moves nothing, so it must not write a miss row.

    `demote_target('shape')` is `'shape'`, so the old path wrote `declared == observed == 'shape'`
    into `brain.queue_calibration` -- a row in the producer-miscalibration dataset recording a
    miscalibration of zero, indistinguishable from a producer who got it exactly right. That is
    the row T5 measured on q0016. The verb now refuses and says what the operator actually wants.
    """
    reset()
    t = store.apply("post", title="rethink the pricing model", lane="strategy",
                    actor_type="human")
    check("an unprepared item is Shape", reads.why("work_item", t["id"])["tier"] == "shape")
    err = ""
    try:
        store.apply("queue demote", source_type="work_item", source_id=t["id"])
    except Exception as e:                                          # noqa: BLE001
        err = str(e)
    check("`not fast` on a Shape item is refused", "no tier below" in err, err or "it succeeded")
    check("and it names the two things that ARE available",
          "defer" in err and "decline" in err, err)
    with store.read("runtime") as s:
        n = s.scalar("SELECT count(*) FROM brain.queue_calibration WHERE kind = 'not-fast'")
    check("NO ZERO-MISS ROW REACHES THE CALIBRATION DATASET", n == 0, str(n))


def test_the_fully_classified_work_item_still_behaves():
    """The case the old suite covered, kept: the fix must not move the one that already worked."""
    reset()
    t = store.apply("post", title="approve the copy", lane="content", workdir="/tmp",
                    signals={"reversibility": "high"}, agent_claimable=True)
    store.apply("claim", agent="T5", lanes=("content",))
    store.apply("done", id=t["id"], summary="drafted", agent="T5")
    store.apply("queue classify", source_type="work_item", source_id=t["id"],
                item_class="approval", template_id="playbook-approve-copy",
                prepared_context_link="outputs/copy.md", recommended_option="approve")
    check("it starts in Decide", reads.why("work_item", t["id"])["tier"] == "decide")
    out = store.apply("queue demote", source_type="work_item", source_id=t["id"])
    check("and still demotes decide -> judge", out["was"] == "decide" and out["tier"] == "judge",
          str(out))


def main():
    for fn in (test_a_timed_defer_ends_when_its_time_comes,
               test_an_elapsed_defer_still_counts_toward_the_third,
               test_a_defer_until_a_question_ends_when_the_question_lands,
               test_an_until_event_defer_is_still_only_woken_explicitly,
               test_the_demote_demotes_from_the_tier_the_operator_actually_SAW,
               test_the_demote_reads_the_arms_own_default_item_class,
               test_not_fast_on_a_shape_item_is_refused_rather_than_logged_as_a_zero_miss,
               test_the_fully_classified_work_item_still_behaves):
        print(f"=== {fn.__name__} ===")
        fn()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
