"""Decide, Judge, Shape: three tiers that sort COGNITIVE MODE, not minutes.

    Decide   act from prepared context, nothing opened
    Judge    open one thing, evaluate, give feedback
    Shape    generate: scope, rethink, decide something novel

Minutes are the symptom. The real variable is **whether the item evicts a loaded context**,
which is why the operator's rhythm of dropping from slow work to fast work exists at all. Two
five-minute items are not interchangeable: one answered from a card, the other needing a repo
open, cost the same clock and very different attention.

`human_minutes_est` DOES NOT EXIST AND THIS MODULE DOES NOT INVENT IT. `effort` in
`entities/rules/signal-vocabulary.md` is the *agent's* token cost; the system has no field for
human cost. Adding one is an edit to the queue-item shape in
`entities/rules/operator-human-queue-contract.md`, which is operator-gated. It is raised in
`queue/RAISED.md` and not made here. Until it exists the tier is computed from five inputs that
do exist, and the fifth one is a floor rather than a term:

    item_class · template_id null-or-not · prepared_context_link · recommended_option
    · the reversibility floor

THE FLOOR IS NOT A TIE-BREAK. An irreversible item never sits in Decide whatever its estimate,
because the failure mode of a fast lane is not slowness, it is an irreversible act taken from a
card in eleven seconds. Reversibility is `low` when unset, so an unassessed item cannot reach
Decide by omission either.
"""

from __future__ import annotations

TIERS = ("decide", "judge", "shape")

TIER_ACT = {
    "decide": "act from prepared context, nothing opened",
    "judge": "open one thing, evaluate",
    "shape": "generate: scope, rethink, decide something novel",
}

# A review means open the artifact and evaluate it. That is Judge by definition, whatever else
# the item carries: an approval with a prepared recommendation can be decided from the card, a
# review of the work behind it cannot.
NEVER_DECIDE_CLASSES = ("review", "scope", "assumption")
SHAPE_CLASSES = ("scope", "assumption")


# THE ONLY CEILING ANYONE HAS EVER CLAIMED, and the other two are None ON PURPOSE.
#
# `Decide` is described everywhere in this program as a two-minute lane, and until task 0279 built
# `brain.time_entry` nobody had put a stopwatch on one, so it was an assertion. It is now a claim
# with a number attached that `reads.tier_ceilings()` measures against real entries.
#
# `Judge` and `Shape` are None because NOBODY HAS CLAIMED A NUMBER FOR THEM. Filling these in with
# a plausible ten and thirty minutes would manufacture a claim in order to have something to
# measure, and the measurement would then report agreement with a number this file invented. The
# honest read is "unclaimed", and when the operator states one it goes here.
#
# Note what this is NOT: it is not an input to `tier_of`. Tiers sort cognitive mode, not minutes
# (see the docstring above), and the day this dict starts deciding a tier is the day the module
# has quietly acquired the `human_minutes_est` field it says it does not invent.
TIER_CEILING_SECONDS = {"decide": 120, "judge": None, "shape": None}


def _has(v) -> bool:
    return bool(v) and str(v).strip() != ""


def tier_inputs(open_row: dict, overlay: dict, default_text: str | None = None) -> dict:
    """The inputs `tier_of` reads, composed from the ARM'S ROW and the membrane overlay.

    ONE COMPOSITION, TWO CALLERS, AND THE SECOND ONE IS WHY THIS FUNCTION EXISTS (task 0158).
    `reads.queue()` built these fields inline and `queue demote` computed the item's current tier
    from `brain.queue_item` alone. That looks equivalent and is not: THREE OF THE FIVE INPUTS DO
    NOT LIVE ON THE OVERLAY.

      * `item_class` falls back to the arm's own `default_item_class` -- a finished task is
        `review` because of the arm it came from, and nothing writes that to the overlay.
      * a question's `recommended_option` is DERIVED from its stated default, below. The producer
        has already said what happens if the operator says nothing, which is a recommended option
        in everything but name.
      * `reversibility` is a signal on the row (for a question, inherited from the task it hangs
        off), and the floor is computed from it by the caller.

    Measured on 2026-08-16: a question the console rendered in Decide was demoted with a computed
    current tier of Judge, so it skipped Judge, landed in Shape, and wrote a calibration miss
    reading `judge -> shape` against a producer whose item the operator had seen in Decide. The
    row was durable, well-constrained and wrong, in the table whose entire purpose is measuring
    producer miscalibration.

    The derivation order matters and is the reason this is a function rather than a dict literal:
    the default becomes the recommended option BEFORE the tier is computed, so an answerable
    question lands in Judge rather than in Shape with the work that has nothing prepared, and it
    is never written over a real option the membrane set.
    """
    out = {
        "item_class": overlay.get("item_class") or open_row.get("default_item_class"),
        "template_id": overlay.get("template_id"),
        "prepared_context_link": overlay.get("prepared_context_link"),
        "recommended_option": overlay.get("recommended_option"),
        "tier_override": overlay.get("tier_override"),
        "tier_override_reason": overlay.get("tier_override_reason"),
    }
    if not out["recommended_option"] and _has(default_text):
        out["recommended_option"] = f"the stated default: {default_text}"
    return out


def tier_of(item: dict, reversibility_level: str | None) -> dict:
    """One tier, with the reason it landed there. The reason is not decoration.

    D7 renders `why this position`, and a tier with no stated reason is the shape that becomes
    unfalsifiable: an operator who cannot see why an item is in the fast lane cannot tell a
    correct placement from a producer's optimism. `reversibility_level` is already folded to
    low/medium/high by `swarm_engine.signals.signal_level`, and `None` means unset, which this
    function treats exactly as `low`.
    """
    override = (item.get("tier_override") or "").strip().lower()
    if override in TIERS:
        return {"tier": override, "reason": item.get("tier_override_reason")
                or "set by the operator", "floor_applied": False, "overridden": True}

    item_class = (item.get("item_class") or "").strip().lower()
    template = _has(item.get("template_id"))
    context = _has(item.get("prepared_context_link"))
    option = _has(item.get("recommended_option"))
    reversible = (reversibility_level or "low") == "high"

    if item_class in SHAPE_CLASSES:
        return {"tier": "shape", "reason": f"item_class {item_class}: this is generative work",
                "floor_applied": False, "overridden": False}

    if not template and not option and not context:
        return {"tier": "shape",
                "reason": "no template, no recommended option and no prepared context: nothing "
                          "to act or evaluate from, so it is generative",
                "floor_applied": False, "overridden": False}

    decidable = option and (context or template) and item_class not in NEVER_DECIDE_CLASSES
    if decidable and reversible:
        return {"tier": "decide",
                "reason": "recommended option present, "
                          + ("prepared context" if context else "procedural template")
                          + ", and reversible",
                "floor_applied": False, "overridden": False}

    if decidable and not reversible:
        # The floor. Reported by name, because "why is this not in my fast lane" is a question
        # the operator will ask and the honest answer is a signal value, not a heuristic.
        return {"tier": "judge",
                "reason": f"reversibility floor: {reversibility_level or 'unset'} is not "
                          f"reversible, so it never sits in Decide however cheap it looks",
                "floor_applied": True, "overridden": False}

    missing = [n for n, v in (("recommended option", option),
                              ("prepared context or template", context or template)) if not v]
    reason = "review: open the artifact and evaluate" if item_class == "review" else (
        "missing " + " and ".join(missing) if missing else "evaluate before acting")
    return {"tier": "judge", "reason": reason, "floor_applied": False, "overridden": False}


def demote_target(tier: str) -> str:
    """The `not fast` button. One tap, Decide to Judge, and it logs a calibration miss.

    Fast-lane trust is the entire asset: one ambush a week and the lane stops being used. The
    stopwatch decides what is cheap for the human, never the producing agent, so the correction
    is made system side and surfaced in that producer's rollup rather than absorbed silently.
    """
    return {"decide": "judge", "judge": "shape"}.get(tier, "shape")
