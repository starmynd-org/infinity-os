#!/usr/bin/env python3
"""The rest of the lane: tiers, the ranking, typed defers, calibration, and the falsifier.

Every ranking assertion here is computed BY HAND in the test and compared to the code, rather
than asserting the code agrees with itself. An assertion that reads `assert rank(x) == rank(x)`
passes forever and proves nothing, and a scoring function is the easiest place in a system for
that shape to hide.

Run:  python3 queue/tests/test_queue_mechanics.py
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
from human_queue import checkpoints, rank, reads, tiers  # noqa: E402
from human_queue import transitions as queue_transitions  # noqa: E402,F401
from swarm_engine import accept as engine_accept         # noqa: E402,F401
from swarm_engine import transitions as engine           # noqa: E402,F401

SCRATCH = str(ROOT / "queue/bin/queue-scratch-db.sh")
PASS, FAIL = 0, 0
NOW = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)


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
        return str(e) or e.__class__.__name__


def reset():
    subprocess.run([SCRATCH, "psql", "-q", "-c",
                    "TRUNCATE brain.queue_item, brain.queue_defer, brain.queue_bump, "
                    "brain.queue_calibration, brain.queue_default_event, brain.recommendation, "
                    "brain.thread, brain.question, brain.agent, brain.work_item CASCADE; "
                    "SELECT setval('brain.item_id_seq', 1, false);"],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# --------------------------------------------------------------------------- tiers

def test_tiers_sort_cognitive_mode():
    prepared = {"item_class": "approval", "template_id": "playbook-x",
                "prepared_context_link": "outputs/brief.md", "recommended_option": "approve it"}
    check("prepared + reversible is Decide",
          tiers.tier_of(prepared, "high")["tier"] == "decide")
    t = tiers.tier_of(prepared, "medium")
    check("THE FLOOR: the same item, costly-to-reverse, is never Decide",
          t["tier"] == "judge" and t["floor_applied"], str(t))
    t = tiers.tier_of(prepared, None)
    check("an UNSET reversibility is also refused Decide, because unset means conservative",
          t["tier"] == "judge" and t["floor_applied"], str(t))
    check("a review is Judge even fully prepared",
          tiers.tier_of({**prepared, "item_class": "review"}, "high")["tier"] == "judge")
    check("nothing prepared is Shape",
          tiers.tier_of({"item_class": "blocker"}, "high")["tier"] == "shape")
    check("a scope item is Shape however much is attached",
          tiers.tier_of({**prepared, "item_class": "scope"}, "high")["tier"] == "shape")
    check("the demote target of Decide is Judge", tiers.demote_target("decide") == "judge")
    check("every tier carries a stated reason",
          all(tiers.tier_of(prepared, r)["reason"] for r in ("high", "medium", None)))


# --------------------------------------------------------------------------- the unblock walk

def test_unblock_weight_is_marginal_and_transitive():
    """Hand-computed. The shape:  A blocks B; B blocks C; B also waits on X.

        v(C) = band 2 -> 1.0 ; C has 1 unresolved blocker (B)  -> U(B) += 1/1 * (1.0 + 0.5*U(C))
        U(C) = 0 (blocks nothing)                              -> U(B) = 1.0
        v(B) = band 1 -> 2.0 ; B has 2 unresolved blockers (A, X)
        U(A) = 1/2 * (2.0 + 0.5 * 1.0) = 1.25
    """
    rows = [{"id": "0001", "state": "inbox", "priority": 1, "depends_on": ""},        # A
            {"id": "0002", "state": "inbox", "priority": 1, "depends_on": "0001,0009"},  # B
            {"id": "0003", "state": "inbox", "priority": 2, "depends_on": "0002"},    # C
            {"id": "0009", "state": "inbox", "priority": 3, "depends_on": ""}]        # X
    d = rank.Dag(rows)
    check("U(C) = 0: it blocks nothing", d.unblock_weight("0003") == 0.0)
    check("U(B) = 1.0", abs(d.unblock_weight("0002") - 1.0) < 1e-9, d.unblock_weight("0002"))
    check("U(A) = 1.25, halved because B waits on two things",
          abs(d.unblock_weight("0001") - 1.25) < 1e-9, d.unblock_weight("0001"))

    rows2 = [r.copy() for r in rows]
    rows2[3]["state"] = "done"                    # X resolves; B now waits only on A
    d2 = rank.Dag(rows2)
    check("resolving the OTHER blocker doubles A's marginal weight to 2.5",
          abs(d2.unblock_weight("0001") - 2.5) < 1e-9, d2.unblock_weight("0001"))

    rows3 = [r.copy() for r in rows]
    rows3[2]["state"] = "done"                    # C is done; it can no longer be released
    d3 = rank.Dag(rows3)
    check("a DONE dependent contributes nothing: U(A) drops to 1.0",
          abs(d3.unblock_weight("0001") - 1.0) < 1e-9, d3.unblock_weight("0001"))

    cyc = rank.Dag([{"id": "0001", "state": "inbox", "priority": 1, "depends_on": "0002"},
                    {"id": "0002", "state": "inbox", "priority": 1, "depends_on": "0001"}])
    u = cyc.unblock_weight("0001")
    check("a dependency CYCLE terminates instead of recursing forever", isinstance(u, float))
    check("and the cycle is recorded as a finding", len(cyc.cycles) >= 1, str(cyc.cycles))


def test_the_effort_term_is_gone():
    check("no weight named effort exists",
          not any("effort" in k for k in rank.DEFAULT_WEIGHTS),
          str(list(rank.DEFAULT_WEIGHTS)))
    s = rank.score({"urgency": "high", "stakes": "high", "charter_alignment": "low",
                    "effort": "high", "surfaced_at": NOW}, u=0.0, bump=0.0, now=NOW)
    check("effort is absent from the decomposition", "effort" not in s["terms"], str(s["terms"]))
    check("a high-effort item scores the same as a low-effort one, all else equal",
          s["score"] == rank.score({"urgency": "high", "stakes": "high",
                                    "charter_alignment": "low", "effort": "low",
                                    "surfaced_at": NOW}, u=0.0, bump=0.0, now=NOW)["score"])


def test_the_bump_decays_and_never_pins():
    bumps = [{"delta": 4.0, "half_life_hours": 24.0, "created_at": NOW - timedelta(hours=24),
              "reason": "Mick is waiting", "created_by": "operator"}]
    total, live = rank.bump_contribution(bumps, now=NOW)
    check("one half life halves it", abs(total - 2.0) < 1e-9, total)
    total_2, _ = rank.bump_contribution(
        [{**bumps[0], "created_at": NOW - timedelta(hours=48)}], now=NOW)
    check("two half lives quarter it", abs(total_2 - 1.0) < 1e-9, total_2)
    total_3, live_3 = rank.bump_contribution(
        [{**bumps[0], "created_at": NOW - timedelta(days=7)}], now=NOW)
    check("a week later it is worth under 0.05 and drops out of the live list",
          total_3 < 0.05 and not live_3, f"{total_3} {live_3}")
    check("the live entry carries its label, which is the point of the log",
          live[0]["reason"] == "Mick is waiting")
    down, _ = rank.bump_contribution([{**bumps[0], "delta": -4.0}], now=NOW)
    check("bump-down is the negative and decays the same way", abs(down + 2.0) < 1e-9, down)


def test_hard_rules_sit_on_top_of_the_score():
    """The band is for facts the SUM DOES NOT CARRY. Task 0281 is what that sentence cost.

    A band is absolute: `rank` sorts by it before it looks at the score. So a band read off a
    signal that is already a term outranks every other term at once, including the bump, and the
    operator's only lever over ordering stops working while still reporting a number.
    """
    idle = {"agent_idle_since": NOW - timedelta(minutes=20), "idle_agent": "T3", "urgency": "low"}
    band, why = rank.jump_band(idle, now=NOW)
    check("a live agent idle on an item jumps to band 0", band == 0 and "T3" in why, why)
    check("and it is a fact no term of the sum carries",
          "agent_idle_since" not in rank.score({"surfaced_at": NOW, **idle}, u=0.0, bump=0.0,
                                               now=NOW)["terms"])
    band, why = rank.jump_band({"urgency": "high"}, now=NOW)
    check("URGENCY BUYS NO BAND: it is worth +2 in the sum and nothing on top of it",
          band == 2 and why == "", f"band {band} {why!r}")
    check("so a high-urgency item is beaten by anything that outscores it",
          rank.score({"urgency": "medium", "surfaced_at": NOW}, u=0.0, bump=3.0,
                     now=NOW)["score"]
          > rank.score({"urgency": "high", "surfaced_at": NOW}, u=0.0, bump=0.0,
                       now=NOW)["score"])
    band, _ = rank.jump_band({"urgency": "medium"}, now=NOW)
    check("everything else is band 2, ordered by score alone", band == 2)


# --------------------------------------------------------------------------- through the store

def test_the_queue_ranks_and_explains_a_real_item():
    reset()
    root = store.apply("post", title="Rebuild the 2025 margin source table", lane="data",
                       priority=1, signals={"urgency": "decaying", "stakes": "high",
                                            "charter_alignment": "high", "effort": "high",
                                            "reversibility": "high"})
    mid = store.apply("post", title="Restate the margin story", lane="data", priority=1,
                      depends_on=root["id"])
    store.apply("post", title="Send Mick the restated deck", lane="client", priority=2,
                depends_on=mid["id"])
    store.apply("done", id=root["id"], summary="rebuilt", agent="T2")
    store.apply("queue classify", source_type="work_item", source_id=root["id"],
                item_class="review", prepared_context_link="outputs/margin/REPORT.md",
                recommended_option="accept: 12 of 12 months tie to the export")
    w = reads.why("work_item", root["id"])
    check("the finished task is in the human queue awaiting acceptance", w["found"])
    check("it is Judge: a review means open the artifact", w["tier"] == "judge", w["tier_reason"])
    check("U is positive because it blocks a chain", w["unblock_weight"] > 0, w["unblock_weight"])
    check("the decomposition has no effort term", "effort" not in w["terms"], str(w["terms"]))
    check("the unblock detail names the item it releases",
          w["unblock_detail"] and w["unblock_detail"][0]["id"] == mid["id"],
          str(w["unblock_detail"]))
    print(f"      score {w['score']} = "
          + ", ".join(f"{k} {v:+g}" for k, v in w["terms"].items() if v))
    print(f"      U = {w['unblock_weight']} from {w['unblock_detail']}")

    store.apply("queue bump", source_type="work_item", source_id=root["id"], delta=3.0,
                reason="Mick asked twice", by="operator")
    w2 = reads.why("work_item", root["id"])
    check("the bump lands in the decomposition as its own term",
          w2["terms"]["bump"] > 2.9, str(w2["terms"]))
    check("and it is labelled with the disagreement",
          w2["bump_detail"][0]["reason"] == "Mick asked twice")
    check("the score moved by the bump and nothing else",
          abs((w2["score"] - w["score"]) - w2["terms"]["bump"]) < 0.02,
          f"{w['score']} -> {w2['score']}")


def test_a_blocked_item_is_never_dispatched_and_roots_come_first():
    reset()
    root = store.apply("post", title="the blocker", lane="ops", actor_type="human")
    dep = store.apply("post", title="the dependent", lane="ops", actor_type="human",
                      depends_on=root["id"])
    q = reads.queue(window=50)
    ids = [i["source_id"] for i in q["tiers"]["shape"]["items"]]
    check("the blocked item is not in the rendered queue", dep["id"] not in ids, str(ids))
    check("it is counted, not silently dropped",
          q["totals"]["blocked"] == 1 and q["blocked"][0]["source_id"] == dep["id"],
          str(q["totals"]))
    check("its blocker is named", q["blocked"][0]["blocked_on"] == [root["id"]])


def test_the_reserved_oldest_slot_and_the_window():
    """The starved item is old AND low-scoring, which is the only shape that actually starves.

    Eight urgent items and one four-day-old item with no urgency: the aging term (0.25/day) is
    deliberately too small to lift it, which is the point -- an aging term alone lets a low
    signal rot for weeks. The reserved slot is what converts that from a statistic into
    something the operator has to decline on purpose.
    """
    reset()
    for i in range(8):
        store.apply("post", title=f"urgent operator task {i}", lane="ops", actor_type="human",
                    signals={"urgency": "deadline"})
    old = store.apply("post", title="the starved one", lane="ops", actor_type="human")
    subprocess.run([SCRATCH, "psql", "-q", "-c",
                    f"UPDATE brain.work_item SET created = now() - interval '4 days' "
                    f"WHERE id = '{old['id']}'"], check=True, stdout=subprocess.DEVNULL)
    q = reads.queue(window=3)
    shape = q["tiers"]["shape"]
    ranked = [i["source_id"] for i in shape["items"]]
    check("the starved item ranks last on score alone", old["id"] not in ranked[:3], str(ranked))
    check("the window shows 3 plus the reserved oldest", len(shape["items"]) == 4,
          str(len(shape["items"])))
    check("the total is reported beside the window", shape["total"] == 9, str(shape["total"]))
    check("and what is not shown is counted", shape["hidden"] == 5, str(shape["hidden"]))
    check("the reserved item is the starved one, marked so it can be rendered as such",
          shape["items"][-1].get("reserved_oldest") is True
          and shape["items"][-1]["source_id"] == old["id"], str(shape["items"][-1]["source_id"]))


# ------------------------------------------------- the bump has to move the item, not the score

def _three_items_a_bump_can_reorder():
    """Two neighbours at 1.5 and a target at 1.0, hand-computed rather than read off the code.

    THE ARITHMETIC CHANGED UNDER THIS HELPER ON 2026-08-31 AND THE NUMBERS ARE RESTATED RATHER
    THAN LOOSENED. Row 0434 replaced the additive `urgency + stakes` with the product
    `urgency x impact`, so the neighbours moved from 2.0 to 1.5 and the target stayed at 1.0:

        neighbours  urgency deadline (folds high)  -> factor 3.0 ;  stakes low -> magnitude 0.5 ;  core 1.5
        target      urgency soon (folds medium)    -> factor 2.0 ;  stakes low -> magnitude 0.5 ;  core 1.0

    `stakes` is still what the magnitude reads, because these rows carry no `impact` and the fold
    falls back to it, which is the no-backfill promise being exercised here by accident and on
    purpose. Charter is low and contributes nothing; nothing depends on anything so U is 0; all
    three are seconds old so the aging term is under 0.001 and only breaks the tie between the two
    neighbours, oldest first.

    THE ONE THING THAT DID NOT CHANGE IS THE PROPERTY UNDER TEST: the target is still last on
    score alone, and the gap a bump has to cross is still real. Widening the tolerance to make the
    old constants pass would have been the failure this file's own header warns about.

    Returns them in the order the score puts them.
    """
    reset()
    sig = {"stakes": "low", "charter_alignment": "low"}
    a = store.apply("post", title="neighbour A, urgency deadline", lane="ops", actor_type="human",
                    signals={**sig, "urgency": "deadline"})
    b = store.apply("post", title="neighbour B, urgency deadline", lane="ops", actor_type="human",
                    signals={**sig, "urgency": "deadline"})
    t = store.apply("post", title="record the audio walkthrough", lane="ops", actor_type="human",
                    signals={**sig, "urgency": "soon"})
    return a["id"], b["id"], t["id"]


def _shape_order():
    q = reads.queue(tier="shape", window=50)
    return [(i["source_id"], i["score"]) for i in q["tiers"]["shape"]["items"]]


def test_a_bump_lifts_the_item_above_the_neighbours_it_outscores():
    """Task 0281, the operator's own report: the score moved and the order did not.

    The neighbours are high-urgency and the target is not, which is exactly the shape that used
    to be unreachable: `jump_band` read a band off `urgency == high`, `rank` sorts by band before
    score, and so a 2.0 sat above a 4.0 forever. The assertion is on the ORDER, because an
    assertion on the score alone is the one that passed all the way through the defect.
    """
    a, b, t = _three_items_a_bump_can_reorder()
    before = _shape_order()
    check("before the bump the target is last, on score alone",
          [i for i, _ in before] == [a, b, t], str(before))
    # 1.0 against two 1.5s since row 0434. Was 1.0 against two 2.0s under the additive sum; the
    # helper's docstring carries both and the arithmetic for each.
    check("and it is last at 1.0 against two 1.5s", abs(dict(before)[t] - 1.0) < 0.01
          and abs(dict(before)[a] - 1.5) < 0.01, str(before))
    store.apply("queue bump", source_type="work_item", source_id=t, delta=3.0,
                reason="Operator wants the audio walkthrough done first", by="operator")
    after = _shape_order()
    check("the bump lands in the score: 1.0 + 3.0 = 4.0",
          abs(dict(after)[t] - 4.0) < 0.01, str(after))
    check("AND THE RANK MOVES WITH IT: the bumped item is now first",
          [i for i, _ in after] == [t, a, b], str(after))
    check("no neighbour's score changed, so the move is the bump and nothing else",
          abs(dict(after)[a] - dict(before)[a]) < 1e-9
          and abs(dict(after)[b] - dict(before)[b]) < 1e-9, str(after))
    check("the rank the item reports is the position it is rendered at",
          reads.why("work_item", t)["rank"] == 1)
    print(f"      before {before}\n      after  {after}")


def test_the_bump_decays_back_below_them_after_the_half_lives():
    """The other half of the same property: it lifts, and then it lets go.

    A lever that lifts and never lets go is a pin, and a pin accumulates into a second manual
    queue the score no longer governs. Four half lives is chosen so the arithmetic is exact:
    3.0 * 0.5^4 = 0.1875, which puts the target back at 1.1875 and below both 2.0s.
    """
    a, b, t = _three_items_a_bump_can_reorder()
    store.apply("queue bump", source_type="work_item", source_id=t, delta=3.0,
                half_life_hours=24.0, reason="Operator wants it done first", by="operator")
    check("it is first while the bump is fresh", _shape_order()[0][0] == t, str(_shape_order()))
    subprocess.run([SCRATCH, "psql", "-q", "-c",
                    "UPDATE brain.queue_bump SET created_at = now() - interval '96 hours'"],
                   check=True, stdout=subprocess.DEVNULL)
    after = _shape_order()
    check("four half lives later the bump is worth 3.0/16 = 0.1875",
          abs(dict(after)[t] - 1.1875) < 0.01, str(after))
    check("SO IT FALLS BACK BELOW THEM. The bump is temporary, not a pin",
          [i for i, _ in after] == [a, b, t], str(after))
    with store.read("runtime") as s:
        check("and the row survives the decay: the labelled disagreement stays in the tuning "
              "dataset after it stops moving the queue",
              s.scalar("SELECT count(*) FROM brain.queue_bump") == 1)
    print(f"      decayed to {dict(after)[t]}, back under {dict(after)[a]}")


def test_why_and_list_are_one_ranking_path_not_two():
    """Agreement is not the proof. Two implementations that happen to match, match today.

    So this bends the one ranking and checks `why` bends with it. If `why` computed a rank of its
    own -- which is what the operator's report looked like from outside, `why` saying #6 while
    the list rendered a different order -- it would report the unbent number here and fail.
    """
    a, b, t = _three_items_a_bump_can_reorder()
    store.apply("queue bump", source_type="work_item", source_id=t, delta=3.0,
                reason="Operator wants it done first", by="operator")
    listed = {i["source_id"]: (tier, i["rank"], i["score"])
              for tier, block in reads.queue(window=50)["tiers"].items()
              for i in block["items"]}
    check("all three are rendered", set(listed) == {a, b, t}, str(sorted(listed)))
    for sid, (tier, r, sc) in sorted(listed.items()):
        w = reads.why("work_item", sid)
        check(f"why and list agree on {sid}: #{r} in {tier} at {sc}",
              (w["tier"], w["rank"], w["score"]) == (tier, r, sc),
              f"why says #{w.get('rank')} in {w.get('tier')} at {w.get('score')}")

    real = rank.rank

    def upside_down(items, dag, **kw):
        out = list(reversed(real(items, dag, **kw)))
        for i, r in enumerate(out, 1):
            r["rank"] = i
        return out

    rank.rank = upside_down
    try:
        bent_list = {i["source_id"]: i["rank"]
                     for block in reads.queue(window=50)["tiers"].values()
                     for i in block["items"]}
        bent_why = {sid: reads.why("work_item", sid)["rank"] for sid in listed}
    finally:
        rank.rank = real
    check("bending ONE function turns the rendered list upside down",
          bent_list[t] == 3 and bent_list[b] == 1, str(bent_list))
    check("AND `why` IS BENT WITH IT, because it is the same function and not a copy",
          bent_why == bent_list, f"why {bent_why} vs list {bent_list}")
    check("the real ranking is restored afterwards", reads.why("work_item", t)["rank"] == 1)


# --------------------------------------------------------------------------- defer

def test_every_defer_is_typed_and_the_third_one_refuses():
    reset()
    t = store.apply("post", title="a thing to decide", lane="ops", actor_type="human")
    check("an untyped defer is not expressible",
          "unknown defer kind" in refuses(store.apply, "queue defer", source_type="work_item",
                                          source_id=t["id"], kind="later"))
    check("until-time with no time is refused by the database",
          refuses(store.apply, "queue defer", source_type="work_item", source_id=t["id"],
                  kind="until-time") != "")
    d1 = store.apply("queue defer", source_type="work_item", source_id=t["id"],
                     kind="until-time", wake_at=NOW + timedelta(hours=2), label="+2h")
    check("defer 1 lands", d1["n"] == 1)
    d2 = store.apply("queue defer", source_type="work_item", source_id=t["id"],
                     kind="until-time", wake_at=NOW + timedelta(hours=12), label="tomorrow AM")
    check("defer 2 lands and warns what the next one does",
          d2["n"] == 2 and "refused" in d2["warning"], str(d2))
    msg = refuses(store.apply, "queue defer", source_type="work_item", source_id=t["id"],
                  kind="until-time", wake_at=NOW + timedelta(days=1))
    check("THE THIRD DEFER REFUSES TO BE A DEFER",
          "mis-scoped, not mis-timed" in msg, msg[:140] or "NOT REFUSED")
    check("and declining is still open, because the fork is decline or Shape",
          store.apply("queue defer", source_type="work_item", source_id=t["id"],
                      kind="decline", reason="this was never mine to decide")["kind"] == "decline")


def test_defer_with_question_posts_an_agent_task():
    reset()
    t = store.apply("post", title="pick the pricing tier", lane="ops", actor_type="human")
    before = store.apply("post", title="marker", lane="ops")["id"]
    d = store.apply("queue defer", source_type="work_item", source_id=t["id"],
                    kind="until-question", lane="data",
                    question="what did the last three clients actually pay?")
    check("it posted an agent task to produce the missing input", d["wake_task"] is not None)
    with store.read("runtime") as s:
        spawned = s.one("SELECT * FROM brain.work_item WHERE id = %s", (d["wake_task"],))
    check("the task names what is missing",
          "what did the last three clients actually pay?" in spawned["title"], spawned["title"])
    check("the deferred item is its parent, so hard flags would inherit",
          spawned["parent"] == t["id"], str(spawned["parent"]))
    check("the deferral made the item cheaper rather than older",
          int(before) < int(d["wake_task"]))


def test_deferring_past_a_silence_window_is_an_interstitial():
    reset()
    t = store.apply("post", title="client mail", lane="client", external="true")
    q = store.apply("ask", question="send it?", agent="T1", task=t["id"],
                    default="hold until I answer")
    msg = refuses(store.apply, "queue defer", source_type="question", source_id=q["id"],
                  kind="until-time", wake_at=NOW + timedelta(hours=6))
    check("deferring past a pending default is refused until it is acknowledged",
          "Silence will ship" in msg, msg[:160] or "NOT REFUSED")
    check("the refusal names what silence will ship", "hold until I answer" in msg)
    ok = store.apply("queue defer", source_type="question", source_id=q["id"],
                     kind="until-time", wake_at=NOW + timedelta(hours=6),
                     acknowledge_default=True)
    check("and an acknowledged defer goes through", ok["n"] == 1)


def test_dead_wake_conditions_are_findings():
    reset()
    t = store.apply("post", title="waiting on an event that never comes", lane="ops",
                    actor_type="human")
    store.apply("queue defer", source_type="work_item", source_id=t["id"], kind="until-event",
                wake_event_type="acme.export.arrived")
    subprocess.run([SCRATCH, "psql", "-q", "-c",
                    "UPDATE brain.queue_defer SET created_at = now() - interval '20 days'"],
                   check=True, stdout=subprocess.DEVNULL)
    findings = [f for f in reads.doctor()["findings"] if f["kind"] == "dead-wake-condition"]
    check("a wake condition that has not fired in 14 days is a finding", len(findings) == 1,
          str(findings))
    check("and it is high severity, because it is the graveyard",
          findings[0]["severity"] == "high")


# --------------------------------------------------------------------------- calibration

def test_the_not_fast_demote_logs_a_miss_against_the_producer():
    reset()
    plain = store.apply("post", title="approve the copy, reversibility unstated", lane="content")
    store.apply("queue classify", source_type="work_item", source_id=plain["id"],
                item_class="approval", template_id="playbook-approve-copy",
                prepared_context_link="outputs/copy.md", recommended_option="approve")
    store.apply("done", id=plain["id"], summary="drafted", agent="T5")
    check("THE FLOOR AGAIN, through the store: an unstated reversibility never reaches Decide",
          reads.why("work_item", plain["id"])["tier"] == "judge")
    # `agent_claimable=True` is load-bearing, not decoration. Migration 26 defaults it false and
    # `claim` filters on it, so without the keyword the claim below returns None SILENTLY, the row
    # keeps claimed_by='', and the demote logs its miss against `posted_by` -- producer 'operator'
    # instead of 'T5'. Nothing else in this test notices: `done` succeeds on an unclaimed row.
    # `workdir` is required of any agent-claimable row since task 0100: an empty one made
    # the runner cd to the agent profile's own directory. `/tmp` because these fixtures
    # never run anything there, only post; the gate asks for absolute, not for existing.
    t = store.apply("post", title="approve the copy", lane="content", workdir="/tmp",
                    signals={"reversibility": "high"}, agent_claimable=True)
    store.apply("claim", agent="T5", lanes=("content",))
    store.apply("done", id=t["id"], summary="drafted", agent="T5")
    store.apply("queue classify", source_type="work_item", source_id=t["id"],
                item_class="approval", template_id="playbook-approve-copy",
                prepared_context_link="outputs/copy.md", recommended_option="approve")
    before = reads.why("work_item", t["id"])
    check("it starts in Decide", before["tier"] == "decide", before["tier_reason"])
    out = store.apply("queue demote", source_type="work_item", source_id=t["id"])
    check("one tap moves it to Judge", out["tier"] == "judge", str(out))
    check("and it names the producer it was logged against", out["producer"] == "T5", str(out))
    with store.read("runtime") as s:
        miss = s.one("SELECT * FROM brain.queue_calibration WHERE kind = 'not-fast'")
    check("the calibration miss is a row, not a note", miss is not None)
    check("it records what was declared and what was observed",
          miss["declared"] == "decide" and miss["observed"] == "judge", str(miss))
    after = reads.why("work_item", t["id"])
    check("the item now reads as Judge with the demote as its reason",
          after["tier"] == "judge" and "not fast" in after["tier_reason"], after["tier_reason"])
    print(f"      calibration miss: producer {miss['producer']}, "
          f"{miss['declared']} -> {miss['observed']}, {miss['note']}")


def test_a_declared_unblocking_that_the_dag_contradicts_is_reported():
    reset()
    t = store.apply("post", title="blocks nothing at all", lane="ops", actor_type="human",
                    signals={"dependency_unblocking": "high"})
    w = reads.why("work_item", t["id"])
    check("the declared level is kept, not overwritten", w["declared_unblocking"] == "high")
    check("the computed U is what ranks it", w["unblock_weight"] == 0.0)
    check("and the disagreement is reported as a calibration note",
          w["declared_vs_computed"] and "calibration miss" in w["declared_vs_computed"],
          str(w["declared_vs_computed"]))
    print(f"      {w['declared_vs_computed']}")


# --------------------------------------------------------------------------- measurement

def test_the_falsifier_is_instrumented_per_template():
    reset()
    a = store.apply("recommend", text="restate A", template_id="playbook-restate-a-metric")
    b = store.apply("recommend", text="restate B", template_id="playbook-restate-a-metric")
    c = store.apply("recommend", text="rewrite the charter", template_id="playbook-dead-one")
    d = store.apply("recommend", text="rewrite it again", template_id="playbook-dead-one")
    # as_operator: since task 0290 an acceptance is written by the operator LOGIN, and
    # `_login_for` reads this keyword off the `apply` call rather than off the signature.
    store.apply("recommend accept", id=a["id"], by="operator", execute=False,
                as_operator=True)
    # ...and since task 0313 a REJECTION is written by the operator login too. The rate below is
    # computed from `brain.queue_acted_on`, which counts by STATE and groups by TEMPLATE, reading
    # neither decided_by nor actor_type -- so a rejection any agent could write is a falsifier any
    # agent could move. `_login_for` reads this keyword off the `apply` call, not the signature.
    store.apply("recommend reject", id=b["id"], by="operator", reason="already true",
                as_operator=True)
    store.apply("recommend reject", id=c["id"], by="operator", reason="not a real problem",
                as_operator=True)
    store.apply("recommend reject", id=d["id"], by="operator", reason="still not one",
                as_operator=True)
    r = reads.acted_on()
    by_t = {row["template_id"]: row for row in r["per_template"]}
    check("the rate is grouped by template so pruning can target a dead playbook",
          set(by_t) == {"playbook-restate-a-metric", "playbook-dead-one"}, str(set(by_t)))
    check("the live playbook reads 1 of 2",
          float(by_t["playbook-restate-a-metric"]["acted_on_rate"]) == 0.5)
    check("the dead one reads 0 of 2",
          float(by_t["playbook-dead-one"]["acted_on_rate"]) == 0.0
          and int(by_t["playbook-dead-one"]["raised"]) == 2)
    check("the overall rate is reported with the falsifier threshold beside it",
          abs(r["acted_on_rate"] - 0.25) < 0.01 and r["falsifier_threshold"] == 0.30, str(r))
    check("and below the threshold it says so in words, rather than reporting a number",
          "BELOW THE FALSIFIER" in r["verdict"], r["verdict"])
    print(f"      acted-on {r['acted_on_rate']}: {r['verdict']}")


def test_depth_and_clearance_refuse_to_invent_an_eta():
    reset()
    for i in range(4):
        store.apply("post", title=f"operator task {i}", lane="ops", actor_type="human")
    d = reads.depth_and_clearance(days=7)
    check("depth is measured", d["depth"]["open"] == 4, str(d["depth"]))
    check("with nothing ever cleared the ETA is None, not zero and not a guess",
          d["eta_days"]["all"] is None and not d["measured"], str(d))
    t = store.apply("post", title="operator task done", lane="ops", actor_type="human")
    store.apply("done", id=t["id"], summary="done", agent="operator")
    store.apply("accept work", id=t["id"], by="operator", as_operator=True)
    d2 = reads.depth_and_clearance(days=7)
    check("once something clears, the rate is measured and the ETA follows",
          d2["measured"] and d2["eta_days"]["all"] is not None, str(d2["eta_days"]))
    print(f"      cleared {d2['cleared']} in 7d = {d2['per_day']}/day, "
          f"eta all {d2['eta_days']['all']}d")


def test_checkpoints_give_a_full_interval_of_silence():
    b = checkpoints.checkpoint_bounds(datetime(2026, 8, 16, 19, 30, tzinfo=timezone.utc))
    check("at 19:30 the current mark is 19:00", b["label"] == "19:00", str(b))
    check("and the eligibility cutoff is the 07:00 before it, a full interval earlier",
          b["previous"] == datetime(2026, 8, 16, 7, 0, tzinfo=timezone.utc), str(b["previous"]))
    b2 = checkpoints.checkpoint_bounds(datetime(2026, 8, 16, 3, 0, tzinfo=timezone.utc))
    check("before the first mark of the day it rolls back to yesterday",
          b2["label"] == "19:00" and b2["current"].day == 15, str(b2))
    reset()
    t = store.apply("post", title="a thing", lane="ops")
    store.apply("ask", question="now?", agent="T1", task=t["id"], default="hold")
    due_now = checkpoints.due(datetime.now(timezone.utc))
    check("a question asked seconds ago does not fire at this checkpoint", not due_now,
          f"{len(due_now)} eligible")
    later = checkpoints.due(datetime.now(timezone.utc) + timedelta(days=1))
    check("a day later it is eligible", len(later) == 1, str(len(later)))
    dry = checkpoints.fire_due(datetime.now(timezone.utc) + timedelta(days=1))
    check("and firing is a DRY RUN unless asked otherwise", dry["dry_run"] is True)
    with store.read("runtime") as s:
        check("so nothing was decided by the dry run",
              s.scalar("SELECT count(*) FROM brain.queue_default_event") == 0)


def test_points_are_read_only_and_report_absence_honestly():
    p = reads.points()
    check("the ledger reads from git, where it lives", p["ledger"].endswith("earn-events.jsonl"))
    if p["exists"]:
        check("it is sliced by actor_type, per the metric definition",
              set(p["by_actor_type"]) <= {"human", "ai", "hybrid", "unrecorded"},
              str(p["by_actor_type"]))
        check("polarity is neutral, not higher-better", p["polarity"] == "neutral")
        check("and the thin history is stated rather than dressed up",
              "total, ever" in p["history_warning"], p["history_warning"])
        print(f"      {p['events']} events, {p['points']} points, "
              f"last {p['last_earned_at'][:10]}, by actor {p['by_actor_type']}")
    else:
        check("a missing ledger reports absence, never a score of zero",
              "not a zero" in p["note"], p["note"])
    missing = reads.points(ledger="/nonexistent/earn-events.jsonl")
    check("and an absent ledger is never reported as 0 points",
          missing["exists"] is False and "not a zero" in missing["note"])


def main():
    print("test_queue_mechanics.py  --  tiers, ranking, defers, calibration, measurement")
    print(f"  store: {os.environ['BRAIN_PG_DB']} (scratch)\n")
    for fn in (test_tiers_sort_cognitive_mode,
               test_unblock_weight_is_marginal_and_transitive,
               test_the_effort_term_is_gone,
               test_the_bump_decays_and_never_pins,
               test_hard_rules_sit_on_top_of_the_score,
               test_the_queue_ranks_and_explains_a_real_item,
               test_a_blocked_item_is_never_dispatched_and_roots_come_first,
               test_the_reserved_oldest_slot_and_the_window,
               test_a_bump_lifts_the_item_above_the_neighbours_it_outscores,
               test_the_bump_decays_back_below_them_after_the_half_lives,
               test_why_and_list_are_one_ranking_path_not_two,
               test_every_defer_is_typed_and_the_third_one_refuses,
               test_defer_with_question_posts_an_agent_task,
               test_deferring_past_a_silence_window_is_an_interstitial,
               test_dead_wake_conditions_are_findings,
               test_the_not_fast_demote_logs_a_miss_against_the_producer,
               test_a_declared_unblocking_that_the_dag_contradicts_is_reported,
               test_the_falsifier_is_instrumented_per_template,
               test_depth_and_clearance_refuse_to_invent_an_eta,
               test_checkpoints_give_a_full_interval_of_silence,
               test_points_are_read_only_and_report_absence_honestly):
        print(f"=== {fn.__name__} ===")
        fn()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
