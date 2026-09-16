#!/usr/bin/env python3
"""Row 0434, his ask 4: `impact` is continuous, the spine is a product, and the weights arrive.

THREE THINGS ARE UNDER TEST AND THEY FAILED IN THREE DIFFERENT WAYS BEFORE THIS FILE EXISTED.

1. **The signal was too coarse to multiply.** He asked for urgency x importance. A lane built the
   product over `stakes`, measured the Shape tier collapse to 3 distinct scores, and filed the
   product as the wrong shape. `stakes` folds five words onto three levels, so the product had at
   most nine cells. `impact` is continuous and the collapse goes away.

2. **The weights never reached the ranking.** `reads.queue()` called `rank.rank()` with no
   `weights=` argument, so every console render used `DEFAULT_WEIGHTS` whatever the operator had
   set. `swarm admin config set signals.*` moved the fleet's claim order and nothing else. This
   file asserts the lever moves the list, which is the only assertion that would have caught it.

3. **Two folds, one vocabulary.** The magnitude exists in SQL and in Python. `signals.py`'s own
   header says why that is dangerous and this file holds them equal on every value in both
   vocabularies, the same way `test_signal_parity` holds the level fold equal.

EVERY NUMBER HERE IS COMPUTED BY HAND AND COMPARED TO THE CODE, which is `test_queue_mechanics`'s
own rule and worth restating: an assertion that reads `assert score(x) == score(x)` passes forever
and proves nothing, and a scoring function is the easiest place in a system for that to hide.

Run:  python3 queue/tests/test_impact_and_the_weights.py
"""

from __future__ import annotations

import math
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _scratch_preflight import reconcile                 # noqa: E402
reconcile(os.environ["BRAIN_PG_DB"])

import store                                             # noqa: E402
from human_queue import rank, reads                      # noqa: E402
from human_queue import transitions as queue_transitions  # noqa: E402,F401
from swarm_engine import signals                         # noqa: E402
from swarm_engine import transitions as engine           # noqa: E402,F401

SCRATCH = str(ROOT / "queue/bin/queue-scratch-db.sh")
PASS, FAIL = 0, 0
NOW = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ok    {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def refuses(fn, *a, **kw) -> str:
    try:
        fn(*a, **kw)
        return ""
    except Exception as e:                                          # noqa: BLE001
        return str(e)


def reset():
    subprocess.run([SCRATCH, "psql", "-q", "-c",
                    "TRUNCATE brain.queue_item, brain.queue_defer, brain.queue_bump, "
                    "brain.queue_calibration, brain.queue_default_event, brain.recommendation, "
                    "brain.thread, brain.question, brain.agent, brain.work_item CASCADE; "
                    "SELECT setval('brain.item_id_seq', 1, false);"],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def has_impact_column() -> bool:
    with store.read() as s:
        return bool(s.scalar(
            "SELECT count(*) > 0 FROM information_schema.columns "
            " WHERE table_schema = 'brain' AND table_name = 'work_item' "
            "   AND column_name = 'impact'"))


def post(title, **kw):
    return store.apply("post", title=title, lane="engine", workdir=str(ROOT),
                       posted_by="operator", **kw)


# --------------------------------------------------------------------- the fold, and its parity

def test_the_money_anchors_are_where_the_design_says():
    """One decade of money per band step. These three numbers are the whole scale."""
    for money, band in (("1000", "medium"), ("10000", "high"), ("100000", "critical")):
        check(f"impact={money} folds exactly onto the band `{band}`",
              rank.impact_magnitude(money) == rank.impact_magnitude(band),
              f"{rank.impact_magnitude(money)} vs {rank.impact_magnitude(band)}")
    # Computed by hand: log10(45000/100) = log10(450) = 2.6532...
    want = math.log10(450.0)
    check("impact=45000 folds to log10(450), computed here rather than read back",
          abs(rank.impact_magnitude("45000") - want) < 1e-9,
          f"{rank.impact_magnitude('45000')} vs {want}")
    check("the ceiling clamps at 4.0 and does not run away with one large number",
          rank.impact_magnitude("100000000") == 4.0, str(rank.impact_magnitude("100000000")))


def test_no_factor_is_ever_zero():
    """THE PROPERTY THE WHOLE SPINE RESTS ON. A zero factor annihilates a product: a `low` urgency
    row would score nothing on the spine however much money is on it, and money would stop being
    able to raise anything at all."""
    worst = ["", "  ", None, "none", "0", "0.0", "low", "garbage", "-5", "1e-9"]
    zeroes = [v for v in worst if rank.impact_magnitude(v) <= 0]
    check(f"no impact value in {len(worst)} adversarial cases folds to zero", not zeroes,
          f"these did: {zeroes}")
    lows = [u for u in ("low", "medium", "high", "", None) if rank.urgency_factor({"urgency": u}) <= 0]
    check("no urgency level folds to zero either", not lows, f"these did: {lows}")
    check("and `low` urgency is 1.0 rather than the 0.0 the additive sum used, which is the change",
          rank.urgency_factor({"urgency": "low"}) == 1.0)


def test_python_and_sql_fold_impact_identically():
    """TWO IMPLEMENTATIONS OF ONE FOLD, held equal. `signals.py`'s header states the cost of drift:
    a task explained one way and ordered another, surfacing as an unreproducible ordering bug."""
    if not has_impact_column():
        check("PRECONDITION: this store has brain.work_item.impact (ledger 48)", False,
              "apply migrations/0048_impact_is_continuous.sql to this scratch store")
        return
    cases = (["none", "low", "medium", "high", "critical", "", "  ", "garbage", "LOW", "High"]
             + ["0", "100", "316", "1000", "4500", "10000", "45000", "100000", "1e6", "99999999"])
    mismatch = []
    with store.read() as s:
        for v in cases:
            sql = s.scalar("SELECT brain.impact_magnitude(%s)", (v,))
            py = rank.impact_magnitude(v)
            if abs(float(sql) - py) > 1e-9:
                mismatch.append(f"{v!r}: sql {sql} vs python {py}")
        # NULL is its own case and is the conservative one.
        sql_null = float(s.scalar("SELECT brain.impact_magnitude(NULL)"))
    check(f"parity: python and SQL fold all {len(cases)} impact values identically",
          not mismatch, "; ".join(mismatch))
    check("parity on NULL, which is the conservative branch both have to agree on",
          abs(sql_null - rank.impact_magnitude(None)) < 1e-9,
          f"sql {sql_null} vs python {rank.impact_magnitude(None)}")


def test_the_ambiguous_number_is_refused_rather_than_guessed():
    """`impact=3` reads as three dollars and as three out of five, and the two order the row very
    differently. Only the writer knows which was meant and only the writer is present to be asked."""
    for bad in ("3", "1", "50", "99.9"):
        msg = refuses(signals.validate_signal, "impact", bad)
        check(f"impact={bad} is refused", bool(msg), "it was ACCEPTED")
        if msg:
            check(f"  and the refusal for {bad} names both readings",
                  "MONEY" in msg and "rating" in msg, msg[:90])
    for good in ("0", "100", "250000", "low", "critical"):
        check(f"impact={good} is accepted", not refuses(signals.validate_signal, "impact", good))


# --------------------------------------------------------------------- the spine

def test_the_spine_is_a_product_and_the_modifiers_are_added():
    """Hand-computed. `core = w_core * urgency_factor * impact_magnitude`, everything else added."""
    item = {"urgency": "medium", "impact": "10000", "charter_alignment": "high",
            "surfaced_at": NOW - timedelta(days=2)}
    s = rank.score(item, u=0.5, bump=0.0, now=NOW)
    # by hand: urgency medium = 2.0, impact 10000 = 2.0, so core = 4.0
    #          charter high = 2.0 ; unblock = 0.5 ; age = 0.25 * 2 = 0.5
    #          total = 4.0 + 2.0 + 0.5 + 0.5 = 7.0
    check("core is urgency x impact and equals 4.0 by hand", s["terms"]["core"] == 4.0,
          str(s["terms"]))
    check("the whole score is 7.0 by hand", s["score"] == 7.0, str(s))
    check("the reported factors multiply back to the reported core",
          abs(s["core_factors"]["urgency"] * s["core_factors"]["impact"]
              - s["terms"]["core"]) < 1e-9, str(s["core_factors"]))
    check("the reported terms still sum to the score, so `why` can still explain it",
          abs(math.fsum(s["terms"].values()) - s["score"]) < 1e-9, str(s))


def test_money_separates_rows_that_the_categorical_signal_could_not():
    """HIS ASK, MEASURED. Seven rows identical in every categorical signal get one score between
    them; the same seven with money on them get seven."""
    base = {"urgency": "medium", "charter_alignment": "high", "surfaced_at": NOW}
    same = [rank.score({**base, "stakes": "high"}, 0.0, 0.0, now=NOW)["score"] for _ in range(7)]
    check("seven rows with identical bands produce ONE distinct score", len(set(same)) == 1,
          str(sorted(set(same))))
    monies = ["500", "2500", "9000", "12000", "45000", "150000", "800"]
    spread = [rank.score({**base, "impact": m}, 0.0, 0.0, now=NOW)["score"] for m in monies]
    check("the same seven with money on them produce SEVEN distinct scores",
          len(set(spread)) == 7, str(sorted(set(spread))))
    check("and they order by the money, largest first",
          [m for _, m in sorted(zip(spread, monies), reverse=True)][0] == "150000",
          str(sorted(zip(spread, monies), reverse=True)))


def test_a_row_with_no_impact_ranks_exactly_as_its_stakes_did():
    """THE NO-BACKFILL PROMISE, asserted rather than described. Not one row is edited by migration
    48, so every row that has never been given an impact has to keep the score it had."""
    for band in ("low", "medium", "high", "critical", "none", None):
        with_stakes = rank.score({"urgency": "medium", "stakes": band, "surfaced_at": NOW},
                                 0.0, 0.0, now=NOW)
        with_impact = rank.score({"urgency": "medium", "impact": band, "surfaced_at": NOW},
                                 0.0, 0.0, now=NOW)
        check(f"stakes={band} and impact={band} score identically",
              with_stakes["score"] == with_impact["score"],
              f"{with_stakes['score']} vs {with_impact['score']}")
    # And the store's own number wins over the fallback when it is present, which is what makes a
    # store at ledger 49 and a store at ledger 47 rank the same untouched row the same way.
    check("the view's own magnitude is preferred when the column is there",
          rank.item_magnitude({"impact_magnitude": 3.0, "stakes": "low"}) == 3.0)
    check("and the stakes fallback is used when it is not",
          rank.item_magnitude({"stakes": "low"}) == rank.impact_magnitude("low"))


# --------------------------------------------------------------------- the weights, and the lever

def test_the_weights_reach_the_ranking():
    """THE DEFECT THAT WENT UNNOTICED BECAUSE NOTHING ASSERTED IT. `rank.rank` took a `weights`
    argument that `reads.queue` never passed, so the operator's lever moved the fleet's claim
    order and left his own queue exactly where it was."""
    items = [{"source_type": "work_item", "source_id": "0001", "work_item_id": "0001",
              "urgency": "medium", "impact": "10000", "charter_alignment": "high",
              "surfaced_at": NOW}]
    dag = rank.Dag([])
    flat = rank.rank(list(items), dag, now=NOW)[0]["score"]
    turned_up = rank.rank(list(items), dag, weights={"w_core": 3.0}, now=NOW)[0]["score"]
    check("rank honours a weights argument at all", turned_up != flat,
          f"{flat} vs {turned_up}")
    # by hand: core goes 1.0*2*2 = 4.0 -> 3.0*2*2 = 12.0, so the score rises by exactly 8.0
    check("and it honours it by exactly the arithmetic, not approximately",
          abs((turned_up - flat) - 8.0) < 1e-9, f"delta {turned_up - flat}")
    check("a pre-0434 config key still applies, aliased onto the spine it is now inside",
          rank.resolve_weights({"w_stakes": 3.0})["w_core"] == 3.0,
          str(rank.resolve_weights({"w_stakes": 3.0})))
    check("a mistyped weight falls back to that one default rather than wedging the queue",
          rank.resolve_weights({"w_core": "not a number"})["w_core"] == 1.0)
    check("an unknown key is ignored rather than added",
          "w_nonsense" not in rank.resolve_weights({"w_nonsense": 5.0}))


def test_the_config_lever_demonstrably_moves_his_order():
    """END TO END, THROUGH THE READ THE CONSOLE ACTUALLY CALLS. Not `rank` with an argument: the
    whole chain from a config override to the order of the rows on the page."""
    if not has_impact_column():
        check("PRECONDITION: this store has brain.work_item.impact (ledger 48)", False,
              "apply migrations/0048_impact_is_continuous.sql to this scratch store")
        return
    reset()
    # Two rows built so that the two weights disagree about which comes first. `a` wins on the
    # spine; `b` wins on charter alignment. Turning charter up has to swap them.
    a = post("A: worth more money, no charter alignment",
             signals={"urgency": "soon", "impact": "20000", "charter_alignment": "low"})["id"]
    b = post("B: worth less money, high charter alignment",
             signals={"urgency": "soon", "impact": "1000", "charter_alignment": "high"})["id"]

    def order(weights):
        v = reads.queue(window=100)
        pool = [i for t in v["tiers"].values() for i in t["items"]]
        dag = rank.Dag([])
        ranked = rank.rank(pool, dag, weights=weights, now=v["now"])
        return [str(i["source_id"]) for i in ranked]

    default_order = order(None)
    check(f"under the default weights {a} (more money) sorts above {b}",
          default_order.index(a) < default_order.index(b), str(default_order))
    tuned = order(rank.resolve_weights({"w_charter_alignment": 12.0}))
    check(f"with charter turned up to 12 the order INVERTS and {b} sorts above {a}",
          tuned.index(b) < tuned.index(a), str(tuned))
    # AND THE READ ITSELF RESOLVES WEIGHTS, which is the line that was missing. Not "a weights
    # argument works" but "the function the console calls goes and gets them".
    v = reads.queue(window=100)
    check("reads.queue publishes the weights it ranked with", isinstance(v.get("weights"), dict),
          str(v.get("weights")))
    check("and they are a complete set rather than a partial override",
          set(v["weights"]) == set(rank.DEFAULT_WEIGHTS), str(v.get("weights")))


def test_the_resolution_floor_is_held():
    """HIS DEFINITION OF DONE, MEASURED: the Shape tier keeps at least the resolution it had. The
    floor named in the plan is 17 distinct scores over 30 items; the live board measured 19 on
    2026-08-30 under the additive sum and 18 under this spine, both above it.

    THE DENOMINATOR IS PRINTED. A distinctness ratio over a handful of rows says nothing, so this
    builds the 30 it needs rather than asserting over whatever happens to be in the scratch store.
    """
    if not has_impact_column():
        check("PRECONDITION: this store has brain.work_item.impact (ledger 48)", False,
              "apply migrations/0048_impact_is_continuous.sql to this scratch store")
        return
    reset()
    urg = ["none", "soon", "deadline"]
    imp = ["low", "medium", "high", "critical", "none"]
    cha = ["low", "medium", "high"]
    made = 0
    for n in range(30):
        post(f"row {n}", signals={"urgency": urg[n % 3], "impact": imp[n % 5],
                                  "charter_alignment": cha[(n // 3) % 3]})
        made += 1
    v = reads.queue(window=1000)
    pool = [i for t in v["tiers"].values() for i in t["items"]]
    scores = {i["score"] for i in pool}
    check(f"the denominator is real: {made} rows posted, {len(pool)} ranked", len(pool) == made,
          f"posted {made}, ranked {len(pool)}")
    check(f"distinct scores {len(scores)} over {len(pool)} items is at or above the floor of 17",
          len(scores) >= 17, f"{len(scores)} distinct: {sorted(scores)}")
    # AND MONEY BEATS BANDS, which is the reason the signal was changed at all.
    reset()
    for n in range(30):
        post(f"money row {n}", signals={"urgency": urg[n % 3], "impact": str(500 + n * 1731),
                                        "charter_alignment": cha[(n // 3) % 3]})
    v2 = reads.queue(window=1000)
    pool2 = [i for t in v2["tiers"].values() for i in t["items"]]
    money_scores = {i["score"] for i in pool2}
    check(f"the same 30 rows with money typed give {len(money_scores)} distinct scores, "
          f"more than the {len(scores)} the bands gave",
          len(money_scores) > len(scores), f"{len(money_scores)} vs {len(scores)}")


def test_the_charter_off_floor_is_what_ruling_4_turns_on():
    """THE MEASUREMENT THE OPERATOR'S RULING 4 TURNS ON, and the one the floor test above cannot make.

    `test_the_resolution_floor_is_held` asserts the floor with `charter_alignment` ON. Ruling 4 is
    *drop `charter_alignment`*, and the argument that blocked it was measured on BANDS: without
    charter the queue was reported to fall to 10 distinct scores against its own floor of 17. So
    the number that decides the ruling is not the one asserted above; it is **money without
    charter**, and until 2026-09-02 nobody had measured it. A gate that cannot fail, guarding the
    decision in front of the operator, is the defect class this programme has recorded seven times.

    NO SCORING SITE IS TOUCHED AND CHARTER IS NOT REMOVED. It is weighted `0.0` through
    `rank.resolve_weights`, the same door `test_the_config_lever_demonstrably_moves_his_order`
    uses. This file measures the ruling; it does not pre-empt it, and dropping the term is the
    operator's act and not this suite's.

    THREE THINGS ARE ASSERTED THAT A DISTINCT-COUNT ALONE WOULD HIDE:

    1. **That the override is REAL.** If a `0.0` weight were swallowed anywhere between
       `resolve_weights` and the sum, all four numbers would still print and two of them would be
       quietly identical to the other two. So two rows differing ONLY in charter are scored under
       both weightings: they must separate under the default and land EQUAL under the override.
    2. **That the clock is not doing the separating.** `score` carries `w_age_per_day * age_days`
       and 30 rows posted seconds apart have 30 different ages, so each count is re-taken with the
       age term removed and the two must agree.
    3. **THE BANDS CLAUSE IS THE CANARY AND IS DELIBERATELY NOT TRIMMED.** Watched under a planted
       defect: with the override silently not applying, the MONEY clauses still pass, because 30
       distinct over 30 items is saturated and cannot go higher. Only the bands reproduction
       clause catches it. Anyone tempted to reduce this case to "just the number ruling 4 needs"
       would be deleting the only clause that can report that the instrument has stopped working.
    """
    if not has_impact_column():
        check("PRECONDITION: this store has brain.work_item.impact (ledger 48)", False,
              "apply migrations/0048_impact_is_continuous.sql to this scratch store")
        return

    on = rank.resolve_weights(None)
    off = rank.resolve_weights({"w_charter_alignment": 0.0})
    check("a 0.0 charter weight survives resolve_weights rather than being swallowed as falsy",
          off["w_charter_alignment"] == 0.0, str(off))
    check("and turning charter off changes ONLY that key",
          {k: v for k, v in off.items() if k != "w_charter_alignment"}
          == {k: v for k, v in on.items() if k != "w_charter_alignment"}, f"{off} vs {on}")

    # THE INSTRUMENT, PROVEN BEFORE ANY NUMBER FROM IT IS BELIEVED. Two rows, identical but for
    # charter: the override is real only if it collapses the gap between them to nothing.
    reset()
    lo = post("charter low", signals={"urgency": "soon", "impact": "10000",
                                      "charter_alignment": "low"})["id"]
    hi = post("charter high", signals={"urgency": "soon", "impact": "10000",
                                       "charter_alignment": "high"})["id"]
    v = reads.queue(window=100)
    two = [i for t in v["tiers"].values() for i in t["items"]]

    def scored(weights):
        return {str(i["source_id"]): i["score"]
                for i in rank.rank(list(two), rank.Dag([]), weights=weights, now=v["now"])}

    s_on, s_off = scored(on), scored(off)
    check(f"with charter ON, two rows differing only in charter score differently "
          f"({s_on.get(lo)} vs {s_on.get(hi)})", s_on.get(lo) != s_on.get(hi), str(s_on))
    check(f"with charter OFF they score IDENTICALLY ({s_off.get(lo)} vs {s_off.get(hi)}), which is "
          f"what proves the override reaches the sum rather than merely being accepted",
          s_off.get(lo) == s_off.get(hi), str(s_off))

    # ------------------------------------------------------------------ the four cases
    urg = ["none", "soon", "deadline"]
    imp = ["low", "medium", "high", "critical", "none"]
    cha = ["low", "medium", "high"]
    n_distinct = {}
    for money in (False, True):
        label = "money" if money else "bands"
        reset()
        made = 0
        for n in range(30):
            sig = ({"urgency": urg[n % 3], "impact": str(500 + n * 1731),
                    "charter_alignment": cha[(n // 3) % 3]} if money else
                   {"urgency": urg[n % 3], "impact": imp[n % 5],
                    "charter_alignment": cha[(n // 3) % 3]})
            post(f"{label} row {n}", signals=sig)
            made += 1
        v = reads.queue(window=1000)
        pool = [i for t in v["tiers"].values() for i in t["items"]]
        check(f"{label}: the denominator is real: {made} rows posted, {len(pool)} ranked",
              len(pool) == made, f"posted {made}, ranked {len(pool)}")
        for tag, w in (("ON", on), ("OFF", off)):
            ranked = rank.rank(list(pool), rank.Dag([]), weights=w, now=v["now"])
            n_distinct[(label, tag)] = len({i["score"] for i in ranked})
            ageless = len({round(i["score"] - i["terms"]["age"], 3) for i in ranked})
            check(f"{label}, charter {tag}: {n_distinct[(label, tag)]} distinct scores over "
                  f"{len(pool)} items, and the CLOCK manufactures none of it",
                  n_distinct[(label, tag)] == ageless,
                  f"{n_distinct[(label, tag)]} distinct, but {ageless} once the age term is "
                  f"removed: the separation is coming from when the rows were posted")
        # FIDELITY. If this path scored differently from the read the floor above is asserted
        # over, these numbers would be about a lookalike rather than about his queue.
        check(f"{label}: charter-ON through rank.rank equals what reads.queue itself scored "
              f"({n_distinct[(label, 'ON')]} vs {len({i['score'] for i in pool})})",
              n_distinct[(label, "ON")] == len({i["score"] for i in pool}),
              "this measurement path differs from the one the floor is asserted over")

    # ------------------------------------------------------------------ the two clauses
    #
    # 17 is hardcoded here exactly as it is in the floor test above: it is the floor named in the
    # plan, and deriving it from anything would make it agree with itself.
    b_off, m_on, m_off = n_distinct[("bands", "OFF")], n_distinct[("money", "ON")], \
        n_distinct[("money", "OFF")]
    check(f"THE CANARY: bands WITHOUT charter fall below the floor ({b_off} distinct over 30, "
          f"floor 17), which is the argument that blocked ruling 4",
          b_off < 17,
          f"bands/charter-off measured {b_off}, which does NOT reproduce the argument ruling 4 "
          f"was blocked on. Either the override has stopped applying or the spine has changed; "
          f"the money clause below cannot tell you, because 30 over 30 is saturated")
    check(f"RULING 4: money WITHOUT charter holds the floor ({m_off} distinct over 30, floor 17), "
          f"so dropping charter_alignment is safe once impact is typed",
          m_off >= 17,
          f"money/charter-off measured {m_off}, BELOW the floor of 17: typed impact does NOT make "
          f"dropping charter safe, and ruling 4 must not ship on this measurement")
    check(f"and dropping charter costs money-typed rows nothing worth having ({m_on} -> {m_off})",
          m_off >= m_on - 2, f"{m_on} -> {m_off}")


def test_the_console_sentence_says_what_the_ranking_does():
    """The strip above the column header is built from the weights and must name the product."""
    sys.path.insert(0, str(ROOT))
    from web import model
    ot = model.order_terms()
    check("the sentence names the product", "urgency x impact" in ot["sentence"], ot["sentence"])
    check("it no longer names `stakes`, which is no longer a term",
          "stakes" not in ot["sentence"], ot["sentence"])
    check("it still names the operator's bump, his one lever over the order",
          "bump" in ot["sentence"], ot["sentence"])
    check("it is still one line", len(ot["sentence"]) <= 80, f"{len(ot['sentence'])} chars")
    unnamed = [k for k in rank.DEFAULT_WEIGHTS if k not in ot["weights"]]
    check("every weight the ranking sums is one the sentence knows how to name", not unnamed,
          str(unnamed))
    tuned = model.order_terms({"w_charter_alignment": 2.5})
    check("and it reports the weights it was handed rather than the defaults",
          tuned["weights"]["w_charter_alignment"] == 2.5, str(tuned["weights"]))


def main() -> int:
    print(f"  store: {os.environ['BRAIN_PG_DB']} (scratch)\n")
    for fn in (test_the_money_anchors_are_where_the_design_says,
               test_no_factor_is_ever_zero,
               test_python_and_sql_fold_impact_identically,
               test_the_ambiguous_number_is_refused_rather_than_guessed,
               test_the_spine_is_a_product_and_the_modifiers_are_added,
               test_money_separates_rows_that_the_categorical_signal_could_not,
               test_a_row_with_no_impact_ranks_exactly_as_its_stakes_did,
               test_the_weights_reach_the_ranking,
               test_the_config_lever_demonstrably_moves_his_order,
               test_the_resolution_floor_is_held,
               test_the_charter_off_floor_is_what_ruling_4_turns_on,
               test_the_console_sentence_says_what_the_ranking_does):
        print(f"=== {fn.__name__} ===")
        fn()
    # DENOMINATOR. Every scene above can return early on a missing precondition, and a run
    # where all twelve did would otherwise print `0 passed, 0 failed` and exit 0: the exact green
    # this file exists to refuse, printed by the file that refuses it. Task 0292's rule.
    if PASS + FAIL == 0:                                                # DENOMINATOR
        print("\n0 assertions made. A verdict over an empty set is not a pass, and it is not a "
              "fail either. Check the store is at ledger 49 and rerun.")
        return 2
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
