#!/usr/bin/env python3
"""Defer and `not fast`, checked at the surface the operator actually reads.

`queue/tests/test_defer_and_demote_joins.py` proves `human_queue.reads`. This proves
`web.model.queue_view()`, and the distinction is the whole reason task 0158 existed. T5's finding
in task 0129 was not "the module is wrong", it was **"the Decide count still read 1 and card-q0007
was still on the list"** -- a sentence about the tier lists and the counts rendered beside them.
Both verbs wrote a correct, durable, well-constrained row; neither row changed this function's
output. A module-level test would have gone green over exactly that.

Run:  python3 web/tests/test_defer_and_demote_at_the_surface.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for p in ("queue", "engine", "web"):
    sys.path.insert(0, str(ROOT / p))
sys.path.insert(0, str(ROOT))

os.environ.setdefault("BRAIN_PG_DB", os.environ.get("QUEUE_SCRATCH_DB", "brain_queue_scratch"))
os.environ.pop("SWARM_PARENT_TASK", None)

import store                                              # noqa: E402
from human_queue import transitions as qt                 # noqa: E402,F401
from swarm_engine import transitions as engine            # noqa: E402,F401
from web import actions, model                            # noqa: E402

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


def sql(s):
    subprocess.run([SCRATCH, "psql", "-q", "-c", s], check=True, capture_output=True)


def reset():
    sql("TRUNCATE brain.queue_item, brain.queue_defer, brain.queue_bump, "
        "brain.queue_calibration, brain.queue_default_event, brain.recommendation, "
        "brain.thread, brain.question, brain.agent, brain.work_item CASCADE; "
        "SELECT setval('brain.item_id_seq', 1, false);")


def _decidable_question():
    """A question the console renders in Decide, built the way a producer actually builds one.

    Nothing here sets a tier. The stated default becomes the recommended option, the template
    makes it actionable, and the reversibility of the task it hangs off clears the floor. NONE of
    those three lives on `brain.queue_item`, which is the point of the fixture.
    """
    t = store.apply("post", title="the pricing change", lane="client",
                    signals={"reversibility": "high"})
    q = store.apply("ask", question="ship the 12 percent uplift?", agent="T2", task=t["id"],
                    default="yes, ship it")
    store.apply("queue classify", source_type="question", source_id=q["id"],
                template_id="playbook-price-change")
    return q["id"]


def _on_tiers(v):
    return {k: [c["id"] for c in v["tiers"][k]] for k in ("decide", "judge", "shape")}


def test_a_timed_defer_leaves_the_tier_and_the_count_and_then_comes_back():
    reset()
    qid = _decidable_question()
    v0 = model.queue_view()
    check("the question renders in Decide to start with",
          _on_tiers(v0)["decide"] == [qid] and v0["totals"]["decide"] == 1,
          str(v0["totals"]))

    store.apply("queue defer", source_type="question", source_id=qid, kind="until-time",
                wake_at=datetime.now(timezone.utc) + timedelta(hours=2), by="operator",
                acknowledge_default=True)
    v1 = model.queue_view()
    check("deferred +2h, THE CARD LEAVES THE LIST AND THE DECIDE COUNT DROPS",
          _on_tiers(v1)["decide"] == [] and v1["totals"]["decide"] == 0, str(v1["totals"]))
    check("and it is named in deferred_items rather than silently subtracted",
          [i["source_id"] for i in v1["deferred_items"]] == [qid])

    sql("UPDATE brain.queue_defer SET wake_at = now() - interval '1 minute'")
    v2 = model.queue_view()
    check("WHEN THE WAKE TIME PASSES THE CARD AND THE COUNT BOTH RETURN",
          _on_tiers(v2)["decide"] == [qid] and v2["totals"]["decide"] == 1, str(v2["totals"]))


def test_not_fast_moves_the_card_and_the_miss_row_can_express_a_miss():
    reset()
    qid = _decidable_question()
    card = [c for c in model.queue_view()["tiers"]["decide"] if c["id"] == qid][0]
    check("the card says Decide, with its reason", card["tier"] == "decide",
          card["tier_reason"])

    one = store.apply("queue demote", source_type="question", source_id=qid, by="operator")
    v1 = model.queue_view()
    check("one tap: out of Decide and into Judge, not past it",
          _on_tiers(v1)["decide"] == [] and _on_tiers(v1)["judge"] == [qid], str(_on_tiers(v1)))

    two = store.apply("queue demote", source_type="question", source_id=qid, by="operator")
    v2 = model.queue_view()
    check("A DEMOTED ITEM APPEARS IN SHAPE AND NOT IN DECIDE ON THE NEXT READ",
          _on_tiers(v2)["shape"] == [qid] and _on_tiers(v2)["decide"] == [],
          str(_on_tiers(v2)))

    with store.read("runtime") as s:
        rows = s.query("SELECT producer, declared, observed FROM brain.queue_calibration "
                       "WHERE kind = 'not-fast' ORDER BY id")
    check("both miss rows record the tier declared and the tier moved to",
          [(r["declared"], r["observed"]) for r in rows] == [("decide", "judge"),
                                                            ("judge", "shape")], str(rows))
    check("AND THEY DIFFER, in every row",
          all(r["declared"] != r["observed"] for r in rows), str(rows))
    check("logged against the producer that declared it",
          all(r["producer"] == "T2" for r in rows), str(rows))
    check("the verb returned the move it made, which is what the receipt is built from",
          (one["was"], one["tier"], two["was"], two["tier"]) == ("decide", "judge", "judge",
                                                                "shape"), str((one, two)))

    err = ""
    try:
        store.apply("queue demote", source_type="question", source_id=qid, by="operator")
    except Exception as e:                                          # noqa: BLE001
        err = str(e)
    check("a third tap is refused: Shape is the bottom", "no tier below" in err, err)
    with store.read("runtime") as s:
        n = s.scalar("SELECT count(*) FROM brain.queue_calibration WHERE kind = 'not-fast'")
    check("so no zero-miss row reaches the calibration dataset", n == 2, str(n))


def test_the_receipt_says_what_will_actually_bring_the_item_back():
    """One sentence per kind. The old one promised a wake for all five and delivered none."""
    check("until-time names the time it returns on its own",
          "returns to its tier on its own at 18:18Z"
          in actions._wake_promise("until-time", {}, "18:18Z"))
    check("until-question names the task, and that it comes back cheaper",
          "0042 lands" in actions._wake_promise("until-question", {"wake_task": "0042"}, ""))
    check("until-event SAYS IT CANNOT SEE THE EVENT rather than promising a wake",
          "nothing in this system can see that event fire"
          in actions._wake_promise("until-event", {}, ""))
    check("and it points at the check that catches a dead one",
          "doctor" in actions._wake_promise("until-event", {}, ""))
    check("a decline is not described as a wake at all",
          "returned to the producer" in actions._wake_promise("decline", {}, ""))


def main():
    for fn in (test_a_timed_defer_leaves_the_tier_and_the_count_and_then_comes_back,
               test_not_fast_moves_the_card_and_the_miss_row_can_express_a_miss,
               test_the_receipt_says_what_will_actually_bring_the_item_back):
        print(f"=== {fn.__name__} ===")
        fn()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
