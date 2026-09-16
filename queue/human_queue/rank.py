"""The human-queue ranking, and the decomposition that has to explain it.

`entities/rules/priority-model.md` is the starting point and this module makes three changes to
it. None of them is a preference:

1. THE EFFORT TERM IS DROPPED. `effort` is the *agent's* token cost
   (`entities/rules/signal-vocabulary.md`). Subtracting it from a *human* ranking is a category
   error: it would sort the operator's attention by how many tokens an agent spent. Human cost
   is handled by tiering, which is a different axis and not a term in the sum.

2. THE UNBLOCK TERM IS MARGINAL AND TRANSITIVE, computed over the `depends_on` DAG rather than
   read off a producer's self-declared level:

       U(i) = Σ over j directly blocked by i of:  (1 / b(j)) · (v(j) + γ · U(j))

   `b(j)` divides because an item waiting on three things is only marginally unblocked by
   resolving one of them: claiming the whole of j's value for i would rank three blockers as if
   each single-handedly released j. `γ = 0.5` discounts depth because a six-deep chain is
   speculative -- the far end may be cancelled before anything reaches it. Memoized over the
   DAG, and cycle-safe, because `depends_on` is free text and a cycle is a data error rather
   than an impossibility.

3. THE BUMP IS A DECAYING ADDITIVE TERM, NOT A PIN. A pin accumulates into a second manual
   queue the score no longer governs, and an absolute override fights every recompute. A
   contribution with a ~24h half life composes with recomputation instead. Every bump carries a
   required reason, because **every bump is a labelled disagreement between the operator and the
   model, and that log is the weight-tuning dataset.**

4. THE DEADLINE JUMP IS GONE, AND IT IS THE ONLY ONE OF THE FOUR THAT WAS FOUND BY BREAKING.
   `priority-model.md` carries a deadline band above the score. This system has no deadline: no
   due-date column exists, so the band was read off `urgency == high`, which is already a term
   in the sum. A band is absolute, so that second reading of one signal outranked every other
   term including the operator's bump. See `jump_band`, and `queue/RAISED.md` item 5, where the
   divergence is written down for the rule's owner rather than settled here.

The declared `dependency_unblocking` signal is NOT dropped and NOT summed. It is reported beside
the computed U so the difference is visible: a producer declaring `high` on an item that blocks
nothing is a measurable miscalibration, the same discipline as the `not fast` demote, and
silently overriding it would destroy the evidence.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

from swarm_engine.signals import (
    IMPACT_BAND,        # noqa: F401  re-exported: the console names the bands
    IMPACT_CEILING,     # noqa: F401
    IMPACT_FLOOR,       # noqa: F401
    impact_magnitude,
)

GAMMA = 0.5                 # depth discount. A six-deep chain is speculative.
BUMP_HALF_LIFE_H = 24.0     # the operator's disagreement is worth ~a day, then the model resumes.
LIVE_IDLE_MINUTES = 3       # an agent parked this long on an item is burning fleet wall-clock.

# The priority band's value, used as v(j) in the unblock walk. `work_item.priority` is
# ascending-urgent: 0 is P0. Bounded on purpose, so one deep chain of P0s cannot produce an
# unblock weight that dwarfs every other term in the sum.
BAND_VALUE = {0: 3.0, 1: 2.0, 2: 1.0}
BAND_DEFAULT = 0.5

DEFAULT_WEIGHTS = {
    # THE SPINE. `w_core` multiplies the whole `urgency x impact` product, so turning it down
    # turns the queue's own value judgement down relative to the modifiers below rather than
    # relative to one half of itself. It replaced `w_urgency` and `w_stakes`, which could not
    # both exist once the two they weighted stopped being separate terms.
    "w_core": 1.0,
    # THE MODIFIERS. Each is added ON TOP of the spine rather than competing with it as a peer.
    "w_charter_alignment": 1.0,
    "w_unblock": 1.0,
    "w_age_per_day": 0.25,
    # NO w_effort. Its absence is the point and is asserted by test_rank.py, because a weight
    # that reappears in config would silently restore the category error.
}

# A config written before task 0434 says `w_stakes` and `w_urgency`. Neither names a term any
# more, and dropping them silently would make an operator's existing tuning stop applying with
# nothing said about it. Both map onto the spine they are now inside, which is the honest
# translation: someone who turned stakes down was turning down how much the value half mattered,
# and the value half is now a factor of `w_core`.
WEIGHT_ALIASES = {"w_stakes": "w_core", "w_urgency": "w_core"}

LEVEL_VALUE = {"low": 0.0, "medium": 1.0, "high": 2.0}
TERMINAL_STATES = ("done", "cancelled", "archived")

# ---------------------------------------------------------------- the spine, and why it multiplies
#
# HIS ASK WAS `urgency x importance` AND THIS IS THE FIRST VERSION OF IT THAT WORKS. The first
# attempt multiplied `urgency` by `stakes`, measured the Shape tier collapse from 19 distinct
# scores to 3, and concluded the product was wrong. The product was never the problem. `stakes` is
# CATEGORICAL: five accepted words folding onto three levels. Two coarse scales multiplied together
# produce a handful of cells, and no arrangement of them produces a fine ordering.
#
# `impact` is continuous, so the product has somewhere to go. The fold is
# `brain.impact_magnitude` in SQL and `impact_magnitude` below, and the two are held equal by
# `test_impact_parity`, for the reason `signals.py` gives about its own two foldings: two folds
# that drift would order the queue differently depending on which one was asked.
#
# NEITHER FACTOR MAY BE ZERO, and that is the property the whole shape rests on. A zero in a
# product annihilates: a `low` urgency row would score zero on the spine however much money is on
# it, and money would stop being able to raise anything. So urgency runs 1 to 3 rather than the
# 0 to 2 the additive sum used, and the magnitude floors at 0.25 rather than at 0. Asserted, not
# assumed, by `test_no_factor_is_ever_zero`.
URGENCY_FACTOR = {"low": 1.0, "medium": 2.0, "high": 3.0}

# THE FOLD IS IMPORTED AND NOT RESTATED. `swarm_engine.signals` is where this vocabulary's Python
# half lives, beside the SQL parity test that keeps it honest, and this module already imports
# `signal_level` from there. A second copy here would be a third implementation of one fold, and
# the whole argument in that file is that two is already one too many.


def _lv(level) -> float:
    return LEVEL_VALUE.get((level or "low"), 0.0)


def item_magnitude(item: dict) -> float:
    """The magnitude for one queue row, preferring the store's own number.

    THE ORDER OF PREFERENCE IS THE WHOLE SCHEMA-TOLERANCE STORY IN THREE LINES. `impact_magnitude`
    is what ledger 49 puts on `brain.queue_open`; `impact` then `stakes` is the same COALESCE the
    view performs, done here for a store that predates it. A store at ledger 47 ranks identically
    to a store at ledger 49 for every row that carries no typed impact, which is every row that
    exists on 2026-08-31, and that is what makes this migration safe to apply whenever he likes
    rather than in the same breath as the code.
    """
    got = item.get("impact_magnitude")
    if got is not None:
        return float(got)
    return impact_magnitude(item.get("impact") or item.get("stakes"))


def urgency_factor(item: dict) -> float:
    return URGENCY_FACTOR.get((item.get("urgency") or "low"), URGENCY_FACTOR["low"])


def resolve_weights(weights=None) -> dict:
    """The weights actually in force, defaults folded under the caller's overrides and the aliases
    translated. One function, so every caller that needs to KNOW the weights and every caller that
    needs to APPLY them read the same answer."""
    w = dict(DEFAULT_WEIGHTS)
    for k, v in (weights or {}).items():
        k = WEIGHT_ALIASES.get(k, k)
        if k not in w:
            continue
        try:
            w[k] = float(v)
        except (TypeError, ValueError):
            pass                    # a mistyped weight falls back to the default for that one key
    return w


def band_value(priority) -> float:
    try:
        return BAND_VALUE.get(int(priority), BAND_DEFAULT)
    except (TypeError, ValueError):
        return BAND_DEFAULT


def parse_deps(raw) -> list:
    """`depends_on` is a comma-separated text column. Ported from the engine's own SQL split."""
    return [p.strip().zfill(4) for p in str(raw or "").split(",") if p.strip()]


class Dag:
    """The `depends_on` graph, with U(i) memoized and cycles survived rather than assumed away.

    Built from whole `work_item` rows so `b(j)` counts only the blockers that are actually
    unresolved: an item waiting on three things, two of them done, is one resolution from
    running, and treating it as a third of a release would understate the last blocker.
    """

    def __init__(self, rows):
        self.item = {}
        self.dependents = {}
        for r in rows:
            rid = r["id"]
            self.item[rid] = {
                "state": r.get("state") or "",
                "priority": r.get("priority"),
                "deps": parse_deps(r.get("depends_on")),
            }
        for rid, meta in self.item.items():
            for dep in meta["deps"]:
                self.dependents.setdefault(dep, []).append(rid)
        self._u = {}
        self.cycles = []

    def is_open(self, rid) -> bool:
        meta = self.item.get(rid)
        return bool(meta) and meta["state"] not in TERMINAL_STATES

    def unresolved_blockers(self, rid) -> list:
        meta = self.item.get(rid)
        if not meta:
            return []
        return [d for d in meta["deps"] if self.is_open(d) or d not in self.item]

    def blocked(self, rid) -> bool:
        """A blocked item is never dispatched. It waits on its blocker; it does not queue."""
        return bool(self.unresolved_blockers(rid))

    def unblock_weight(self, rid, _stack=None) -> float:
        if rid in self._u:
            return self._u[rid]
        stack = _stack or []
        if rid in stack:
            # A dependency cycle. Contributing zero rather than recursing is the only choice
            # that terminates, and the cycle is RECORDED so `queue doctor` reports it: a cycle
            # is a data error someone has to fix, not a curiosity of the walk.
            cyc = stack[stack.index(rid):] + [rid]
            if cyc not in self.cycles:
                self.cycles.append(cyc)
            return 0.0
        total = 0.0
        for j in self.dependents.get(rid, []):
            if not self.is_open(j):
                continue                      # resolving i does nothing for an item already done
            b = max(1, len(self.unresolved_blockers(j)))
            v = band_value(self.item[j]["priority"])
            total += (1.0 / b) * (v + GAMMA * self.unblock_weight(j, stack + [rid]))
        if not _stack:
            self._u[rid] = total
        return total

    def explain(self, rid) -> list:
        """The per-dependent breakdown `queue why` prints. One line per item this one releases."""
        out = []
        for j in self.dependents.get(rid, []):
            if not self.is_open(j):
                continue
            b = max(1, len(self.unresolved_blockers(j)))
            v = band_value(self.item[j]["priority"])
            u_j = self.unblock_weight(j, [rid])
            out.append({"id": j, "blockers": b, "band_value": v, "downstream_u": round(u_j, 3),
                        "contribution": round((1.0 / b) * (v + GAMMA * u_j), 3)})
        return sorted(out, key=lambda r: -r["contribution"])


def bump_contribution(bumps, now=None) -> tuple:
    """Sum the decaying bumps, and say which ones are still alive.

    Half life, not expiry: a bump that vanished at a cliff would make the queue reorder itself
    at a moment nothing happened, which is exactly the unexplainable jump the explainability
    contract in `entities/rules/priority-model.md` exists to prevent.
    """
    now = now or datetime.now(timezone.utc)
    total, live = 0.0, []
    for b in bumps:
        created = b["created_at"]
        half = float(b.get("half_life_hours") or BUMP_HALF_LIFE_H)
        age_h = max(0.0, (now - created).total_seconds() / 3600.0)
        share = float(b["delta"]) * (0.5 ** (age_h / half))
        total += share
        if abs(share) >= 0.05:
            live.append({"delta": float(b["delta"]), "age_hours": round(age_h, 2),
                         "half_life_hours": half, "now_worth": round(share, 3),
                         "reason": b.get("reason", ""), "by": b.get("created_by", "")})
    return total, live


def score(item: dict, u: float, bump: float, weights=None, now=None) -> dict:
    """A SPINE AND ITS MODIFIERS. The dict IS the explanation; the number is a projection of it.

    THE SPINE IS A PRODUCT AND THE MODIFIERS ARE ADDED TO IT. `core = urgency x impact` is his
    ask 4 in one expression, and the three terms beside it are modifiers rather than peers: they
    adjust an item's place around the value the spine gave it, and none of them can carry an item
    on its own the way a peer term in a flat sum could. That distinction is not cosmetic. Under the
    old six-term sum a `low` urgency, `low` stakes row that had been waiting three weeks outscored
    a `high`/`high` row filed this morning, purely on age, and there was no way to express that
    age should nudge rather than decide.

    THE DECOMPOSITION SURVIVES THE CHANGE, and it had to: `why` is what makes the ordering
    falsifiable, and a product with no reported parts explains nothing. So `core` reports the two
    factors that produced it alongside the number, and the sum of the reported terms is still
    exactly the score.

    Reversibility and confidence are deliberately absent. In the operator's model they decide
    surfacing, not ordering (`entities/rules/surfacing-policy.md`), and reversibility is already
    load-bearing as the Decide floor. Putting a signal in two places would let a reversible item
    outrank an irreversible one twice for one reason.

    CHARTER ALIGNMENT IS STILL HERE, UNWEIGHTED AND UNTOUCHED, AND THAT IS A DELIBERATE
    NON-DECISION. It is the only signal in this sum that is not about the work's own value, and
    whether it belongs in a score at all is an OPEN QUESTION FOR THE OPERATOR, recorded in
    `05-PROJECT-PLAN.md` under 1C. Removing it would have been a lane deciding a ranking question
    on his behalf. Note that its INFLUENCE has moved even though its weight has not: it scores 0
    to 2 against a spine that now reaches 12 rather than 4, so it went from roughly half the
    spine's range to roughly a sixth. That is a consequence of the spine changing scale, it is
    reported here rather than buried, and it is part of what he is being asked to rule on.
    """
    w = resolve_weights(weights)
    now = now or datetime.now(timezone.utc)
    surfaced = item.get("surfaced_at") or item.get("created") or now
    age_days = max(0.0, (now - surfaced).total_seconds() / 86400.0)
    urg, mag = urgency_factor(item), item_magnitude(item)
    terms = {
        "core": w["w_core"] * urg * mag,
        "charter": w["w_charter_alignment"] * _lv(item.get("charter_alignment")),
        "unblock": w["w_unblock"] * u,
        "age": w["w_age_per_day"] * age_days,
        "bump": bump,
    }
    return {"terms": {k: round(v, 3) for k, v in terms.items()},
            # The two factors, reported. `why` prints them, and a reader who wants to check the
            # spine by hand can multiply them and get `core` back.
            "core_factors": {"urgency": round(urg, 3), "impact": round(mag, 3)},
            "impact_magnitude": round(mag, 3),
            "score": round(math.fsum(terms.values()), 3),
            "age_days": round(age_days, 3)}


def jump_band(item: dict, now=None) -> tuple:
    """The hard rule that sits ON TOP of the score, as a sort band with its reason.

    ONE band, and the test for whether a fact belongs here is whether it is ALREADY IN THE SUM.
    A live agent parked on an item is not: `agent_idle_since` appears in no term of `score()`,
    it grows in wall-clock rather than in signal space, and it is the only condition where queue
    order costs the fleet money by the minute. So it overrides, and it is the only thing that
    does. Everything else is band 2 and is ordered by score alone.

    BAND 1 IS RETIRED (task 0281) AND THE NUMBERING KEEPS ITS HOLE. It returned 1 for
    `urgency == "high"` and called that a deadline. There is no deadline in this system:
    `brain.work_item` has no due-date column, so the sole input was `urgency`, which is ALREADY
    worth `w_urgency * 2.0` in the sum. Counting one signal twice would be the error `score()`
    names in its own docstring; what made it fatal is that the second count was ABSOLUTE. `rank`
    sorts by band before score, so no score, no unblock weight and NO BUMP could cross it.

    Measured on the live store on 2026-08-17: the operator bumped 0070 by +2, its score went
    5.751 -> 7.739, and it stayed at #6 underneath a 5.011 whose only advantage was
    `urgency=high`. The bump is his one lever over ranking, and a band derived from a term
    already in the sum is what took it away from him.

    The hole is left rather than compacted because `band` is part of the item dict published to
    the console. Renumbering band 2 to band 1 would change what every rendered band value means,
    for a tidy nobody asked for.
    """
    now = now or datetime.now(timezone.utc)
    idle_since = item.get("agent_idle_since")
    if idle_since:
        mins = (now - idle_since).total_seconds() / 60.0
        if mins >= LIVE_IDLE_MINUTES:
            return 0, f"{item.get('idle_agent') or 'an agent'} has been waiting {int(mins)}m"
    return 2, ""


def rank(items, dag: Dag, bumps_by_key=None, weights=None, now=None) -> list:
    """Order one tier's items. The hard rule first, then the score, then id.

    THIS IS THE ONLY RANKING IN THE LANE. `queue list` and `queue why` both reach it through
    `reads.queue`, and `why` therefore cannot report a rank that `list` does not render. A second
    ordering anywhere -- in a view's ORDER BY, in the console, in `why` -- is the defect, not a
    convenience: two implementations of one ranking is how they drift, and the operator finds it
    by being told he is #6 while looking at a list that puts him fourth.

    Five properties, and the last two are the ones a rewrite tends to lose:

    - the sort key is `(band, -score, id)`, and `score` is the WHOLE score, bump included. The
      bump is not applied after the sort and there is no second, unbumped score anywhere;
    - a blocked item is never dispatched; it is filtered out before ranking and counted;
    - roots run before their dependents, repaired after sorting rather than assumed from the
      score, because a dependent with a large bump could otherwise outrank its own blocker and
      the operator would be shown work that cannot start;
    - ties break DETERMINISTICALLY BY ID. `priority-model.md` breaks them by age, and age is
      already a term in this sum, so breaking ties by it again would count age twice. The
      divergence is recorded in queue/RAISED.md rather than taken silently;
    - the single oldest item in each tier is marked `reserved_oldest` and is always rendered,
      which converts starvation from a statistic into something the operator declines on purpose.
    """
    now = now or datetime.now(timezone.utc)
    bumps_by_key = bumps_by_key or {}
    out = []
    for it in items:
        key = (it["source_type"], it["source_id"])
        wid = it.get("work_item_id")
        u = dag.unblock_weight(wid) if wid else 0.0
        bump_total, live = bump_contribution(bumps_by_key.get(key, []), now=now)
        s = score(it, u, bump_total, weights=weights, now=now)
        band, band_reason = jump_band(it, now=now)
        out.append({**it, **s, "unblock_weight": round(u, 3), "bumps": live,
                    "band": band, "band_reason": band_reason,
                    "declared_unblocking": it.get("dependency_unblocking"),
                    "unblocks_directly": len([j for j in dag.dependents.get(wid, [])
                                              if dag.is_open(j)]) if wid else 0})

    out.sort(key=lambda r: (r["band"], -r["score"], str(r["source_id"])))
    out = _roots_first(out, dag)
    # The oldest item is IDENTIFIED here and MARKED by the caller, which is the only place that
    # knows the window. A badge on an item that was going to be rendered anyway says "this was
    # rescued from starvation" about an item nothing was starving, and a surface that cries wolf
    # once stops being read.
    for i, r in enumerate(out, 1):
        r["rank"] = i
    return out


def _roots_first(ordered, dag: Dag) -> list:
    """Move any item that sorts above one of its own blockers back below it.

    A stable repair pass rather than a re-sort: the score ordering is what the operator was
    promised, and this only fixes the pairs where the ordering would show a dependent first.
    """
    pos = {r["source_id"]: i for i, r in enumerate(ordered)}
    changed = True
    guard = 0
    while changed and guard < len(ordered) + 2:
        changed, guard = False, guard + 1
        for i, r in enumerate(list(ordered)):
            wid = r.get("work_item_id")
            if not wid:
                continue
            for dep in dag.unresolved_blockers(wid):
                j = pos.get(dep)
                if j is not None and j > i:
                    ordered.insert(j, ordered.pop(i))
                    pos = {x["source_id"]: k for k, x in enumerate(ordered)}
                    changed = True
                    break
            if changed:
                break
    return ordered
