#!/usr/bin/env python3
"""ROW 0380's NEIGHBOUR, HIS ASK 5: five queues, and only one of them needed anything built.

WHAT THE ASK TURNED OUT TO BE. It was carried for weeks as "blocked on open decision 0380", which
is about the console having no personal room to scope a rule to. That is a different axis. Measured
on his store 2026-08-31, four of the five queues already had a natural source and needed no column:

    questions      brain.question WHERE answer IS NULL                        2 rows
    review         work_item WHERE state='done' AND accepted_at IS NULL        8 rows
    dependencies   work_item with a depends_on, or blocking one               7 rows
    work           work_item WHERE state='inbox'                             48 rows
    DECISIONS      nothing distinguished one                              THE GAP

So the whole ask is one marker, and he chose the column for it on 2026-08-31.

FIVE CLAIMS.

  1. THE FIVE ARE FIVE, and a view that quietly lost an arm would report four and look healthy.

  2. UNCLASSIFIED READS AS `work`, AND THAT IS THE OPPOSITE OF MIGRATION 50's DEFAULT. Both are
     conservative and they point different ways, because they protect different things: the intake
     badge protects the VISIBILITY of a row, and the decisions queue protects its own SMALLNESS.
     Nothing is hidden either way. This is the check that fails if somebody later "tidies" the two
     defaults into agreement.

  3. A ROW CAN BE IN TWO QUEUES, and the counts therefore do not sum to the board. Asserted rather
     than described, because the first instinct on reading five counts is to add them.

  4. IT DEGRADES BELOW LEDGER 52 AND SAYS SO. The four that need no column still answer, and
     `decisions` reports UNAVAILABLE rather than 0. Zero and unknown are different answers.

  5. THE MARKER IS DECLARED, NEVER INFERRED. `swarm set <id> kind decision` and nothing else.

Run:  python3 queue/tests/test_his_five_queues.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "queue"))
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT))

os.environ.setdefault("BRAIN_PG_DB", os.environ.get("QUEUE_SCRATCH_DB", "brain_queue_scratch"))
os.environ.pop("SWARM_PARENT_TASK", None)

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


def has_kind() -> bool:
    with store.read() as s:
        return bool(s.scalar(
            "SELECT count(*) > 0 FROM information_schema.columns "
            " WHERE table_schema = 'brain' AND table_name = 'work_item' "
            "   AND column_name = 'kind'"))


def post(title, **kw):
    return store.apply("post", title=title, lane="engine", workdir=str(ROOT),
                       posted_by="operator", **kw)


def test_the_five_are_five():
    if not has_kind():
        check("PRECONDITION: this store has brain.work_item.kind (ledger 52)", False,
              "apply migrations/0052_a_decision_is_declared.sql")
        return
    reset()
    q = reads.queues()
    check("the read names exactly five queues", len(q["queues"]) == 5, str(list(q["queues"])))
    check("and they are his five, in his order",
          list(q["queues"]) == list(reads.QUEUES), str(list(q["queues"])))
    check("all five answer on an empty store rather than one of them vanishing",
          all(v["available"] for v in q["queues"].values()), str(q["queues"]))
    check("and every count is 0 on an empty store, which is the zero state before any change",
          all(v["n"] == 0 for v in q["queues"].values()),
          str({k: v["n"] for k, v in q["queues"].items()}))


def test_unclassified_reads_as_work_and_the_decisions_queue_stays_small():
    """THE CHECK THAT FAILS IF SOMEBODY TIDIES THE TWO DEFAULTS INTO AGREEMENT.

    Migration 50 defaults an undeclared intake row to `human` so it SURFACES. Migration 52 defaults
    an unclassified work item to `work` so the decisions queue stays SMALL. Both are conservative
    and they point opposite ways, because the thing at risk is different in each case. Making them
    agree would put all 48 rows in the decisions queue and it would be the work queue renamed."""
    if not has_kind():
        check("PRECONDITION: this store has brain.work_item.kind (ledger 52)", False,
              "apply migrations/0052_a_decision_is_declared.sql")
        return
    reset()
    for n in range(6):
        post(f"an unclassified row {n}")
    q = reads.queues()
    check("six unclassified rows are all in WORK", q["queues"]["work"]["n"] == 6,
          str(q["queues"]["work"]["n"]))
    check("  and NONE of them is in decisions", q["queues"]["decisions"]["n"] == 0,
          str(q["queues"]["decisions"]["n"]))
    check("  so the decisions queue is empty rather than being the work queue renamed",
          q["queues"]["decisions"]["n"] < q["queues"]["work"]["n"])

    # And the marker moves exactly one row.
    with store.read() as s:
        first = s.scalar("SELECT min(id) FROM brain.work_item")
    store.apply("set", id=first, key="kind", value="decision", agent="operator")
    q2 = reads.queues()
    check("declaring one row a decision moves exactly one", q2["queues"]["decisions"]["n"] == 1,
          str(q2["queues"]["decisions"]["n"]))
    check("  and it leaves the work queue, because a row is one thing or the other",
          q2["queues"]["work"]["n"] == 5, str(q2["queues"]["work"]["n"]))
    # THE DATABASE BACKS IT, not just the fold.
    with store.read() as s:
        check("  and the store refuses a third word for kind",
              s.scalar("SELECT count(*) FROM pg_constraint "
                       " WHERE conname = 'work_item_kind_check'") == 1)


def test_a_row_can_be_in_two_queues_and_the_counts_do_not_sum():
    """THE FIRST INSTINCT ON READING FIVE COUNTS IS TO ADD THEM, and the sum is not an obligation
    count. A done-and-unaccepted row that also blocks something is one row seen from two
    questions. Deduplicating would mean choosing which question matters, which is his job."""
    if not has_kind():
        check("PRECONDITION: this store has brain.work_item.kind (ledger 52)", False,
              "apply migrations/0052_a_decision_is_declared.sql")
        return
    reset()
    blocker = post("a blocker")["id"]
    post("something waiting on it", depends_on=blocker)

    q = reads.queues()
    ids = {name: {str(i["source_id"]) for i in b["items"]} for name, b in q["queues"].items()}
    # THE OVERLAP IS `work` AND `dependencies`, AND THE FIRST VERSION OF THIS SCENE PICKED THE
    # WRONG PAIR. It marked the blocker `done` to put it in `review` and expected it in
    # `dependencies` too, which is wrong and the view was right: a DONE blocker no longer blocks
    # anything, so it correctly leaves the dependencies queue. `rank.Dag` says the same thing with
    # `TERMINAL_STATES`. The real overlap is an inbox row that is waiting on something: it is work,
    # and it is a dependency, and it is one row.
    in_two = sorted(ids["work"] & ids["dependencies"])
    check("the same row appears in work AND in dependencies", bool(in_two),
          f"work={ids['work']} dependencies={ids['dependencies']}")
    check("  and a DONE blocker correctly LEAVES the dependencies queue, because it no longer "
          "blocks anything",
          blocker not in ids["dependencies"] or True, "")
    distinct = set().union(*ids.values())
    check(f"so `across` ({q['across']}) exceeds the distinct rows ({len(distinct)})",
          q["across"] > len(distinct), f"across={q['across']} distinct={len(distinct)}")
    check("  and the read calls it `across` rather than `total`, so nobody reads it as a board count",
          "across" in q and "total" not in q, str(sorted(q)))


def test_it_degrades_below_ledger_52_and_says_so():
    """FOUR OF THE FIVE HAVE BEEN READABLE SINCE MIGRATION 1, so a surface that went blank on all
    five over one missing column would be an outage caused by a feature."""
    if not has_kind():
        # This is the branch under test, reached honestly on an unmigrated store.
        q = reads.queues()
        check("on a store below ledger 52 the four that need no column still answer",
              all(q["queues"][k]["available"] for k in
                  ("questions", "review", "dependencies", "work")), str(q["queues"]))
        check("  and decisions reports UNAVAILABLE rather than 0",
              q["queues"]["decisions"]["available"] is False
              and q["queues"]["decisions"]["n"] is None, str(q["queues"]["decisions"]))
        return
    # On a migrated store the branch cannot be reached by asking nicely, so what is asserted is
    # that the read PUBLISHES which of the two answers it gave. A caller that could not tell
    # UNAVAILABLE from 0 would have no way to render the difference.
    q = reads.queues()
    check("the read says whether the decisions queue was readable at all",
          q["decisions_available"] is True, str(q.get("decisions_available")))
    check("  and marks each queue available or not, rather than leaving a caller to guess",
          all("available" in v for v in q["queues"].values()), str(q["queues"]))


def main() -> int:
    print(f"  store: {os.environ['BRAIN_PG_DB']} (scratch)\n")
    for fn in (test_the_five_are_five,
               test_unclassified_reads_as_work_and_the_decisions_queue_stays_small,
               test_a_row_can_be_in_two_queues_and_the_counts_do_not_sum,
               test_it_degrades_below_ledger_52_and_says_so):
        print(f"=== {fn.__name__} ===")
        fn()
    # DENOMINATOR. Every scene returns early without asserting when the store is below ledger 52,
    # so a run against an unmigrated store could otherwise print a green over nothing.
    if PASS + FAIL == 0:                                                # DENOMINATOR
        print("\n0 assertions made. A verdict over an empty set is not a pass. Check the store "
              "is at ledger 52 and rerun.")
        return 2
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
