"""Measuring a rule before trusting it, and recovering safely when it was wrong.

TWO JOBS.

1. `evaluate` turns a shadow rule's recorded answers plus the operator's actual decisions into the
   numbers a promotion decision needs: agreements, false positives, false negatives, and the
   denominator. The denominator is reported explicitly because a rule that "was right 4 times" out
   of 4 observed items is a different claim from one right 4 times out of 400, and a promotion made
   on the first number is a guess.

2. `plan_replay` is retirement recovery. Retiring a rule does not undo what it did, so this finds
   every item that rule auto-handled from the RECEIPTS and produces a plan to route them back into
   human review -- with DUPLICATE-EFFECT PROTECTION, which is the part that matters: an item whose
   effect was already applied must not have it applied a second time by the replay. Each entry says
   whether re-running its action is safe, and items whose action was irreversible are flagged for
   human attention instead of being re-run.

`propose_from_repetition` is the entry to the ladder: it observes that the same decision was made
on similar items N times and PROPOSES a rule. It never activates one; a proposal is a suggestion
to a human, and that is the whole graduation path.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from .definitions import RuleDefinition
from .lifecycle import RuleRegistry, RuleState
from .triage import Disposition, TriageReceipt

#: Actions whose effect cannot simply be repeated. A replay never re-runs one.
IRREVERSIBLE_ACTIONS = frozenset({"send", "publish", "pay", "delete", "archive-at-source"})


@dataclass(frozen=True)
class RuleEvaluation:
    rule_id: str
    version: int
    observed: int
    agreed: int
    false_positives: int
    missed: int

    @property
    def eligible_denominator(self) -> int:
        return self.observed

    @property
    def precision(self) -> float | None:
        """None when nothing was observed. A rate with no denominator is not a rate."""
        decided = self.agreed + self.false_positives
        return None if decided == 0 else self.agreed / decided

    def summary(self) -> dict[str, Any]:
        return {
            "rule": f"{self.rule_id} v{self.version}",
            "observed": self.observed,
            "agreed": self.agreed,
            "false_positives": self.false_positives,
            "missed": self.missed,
            "precision": self.precision,
            "eligible_denominator": self.eligible_denominator,
            "promotable": self.promotable(),
        }

    def promotable(self, *, min_observed: int = 5, min_precision: float = 1.0) -> bool:
        """Advisory only. A human still promotes; this just says whether the evidence is there."""
        if self.observed < min_observed or self.precision is None:
            return False
        return self.precision >= min_precision


def evaluate(
    receipts: Sequence[TriageReceipt],
    operator_decisions: Mapping[str, str],
    rule_id: str,
    version: int,
) -> RuleEvaluation:
    """Compare what a rule said with what the operator actually did."""
    observed = agreed = false_positives = missed = 0
    for r in receipts:
        actual = operator_decisions.get(r.item_id)
        if actual is None:
            continue
        matched = r.rule_id == rule_id and r.rule_version == version and r.disposition in (
            Disposition.SHADOW_ONLY, Disposition.AUTO_HANDLED
        )
        if matched:
            observed += 1
            if r.override_action is not None:
                false_positives += 1
            elif r.action == actual:
                agreed += 1
            else:
                false_positives += 1
        elif r.disposition is Disposition.TO_HUMAN:
            # The rule did not fire but the operator took the action the rule proposes.
            missed += 1 if actual == _action_of(receipts, rule_id, version) else 0
    return RuleEvaluation(rule_id, version, observed, agreed, false_positives, missed)


def _action_of(receipts: Sequence[TriageReceipt], rule_id: str, version: int) -> str | None:
    for r in receipts:
        if r.rule_id == rule_id and r.rule_version == version and r.action:
            return r.action
    return None


@dataclass(frozen=True)
class RepetitionObservation:
    """N similar items decided the same way. The evidence behind a proposal."""

    conditions: dict[str, Any]
    action: str
    item_ids: tuple[str, ...]

    @property
    def count(self) -> int:
        return len(self.item_ids)


def observe_repetition(
    items: Sequence[Mapping[str, Any]],
    operator_decisions: Mapping[str, str],
    *,
    on_fields: Sequence[str],
    min_count: int = 3,
) -> tuple[RepetitionObservation, ...]:
    """Find (condition, action) pairs the operator has repeated at least `min_count` times."""
    groups: dict[tuple, list[str]] = defaultdict(list)
    for item in items:
        item_id = str(item["item_id"])
        action = operator_decisions.get(item_id)
        if action is None:
            continue
        key = tuple((f, item.get(f)) for f in on_fields) + (("__action__", action),)
        groups[key].append(item_id)

    out = []
    for key, ids in sorted(groups.items(), key=lambda kv: str(kv[0])):
        if len(ids) < min_count:
            continue
        conditions = {f: v for f, v in key if f != "__action__"}
        action = dict(key)["__action__"]
        out.append(RepetitionObservation(conditions, action, tuple(sorted(ids))))
    return tuple(out)


def propose_from_repetition(
    registry: RuleRegistry,
    source,
    observation: RepetitionObservation,
    *,
    rule_id: str,
    scope: str,
    authority_required: str = "internal",
    source_scope: tuple[str, ...] = (),
):
    """Turn an observation into a PROPOSED rule. It cannot arrive in any other state."""
    definition = RuleDefinition(
        rule_id=rule_id,
        version=1,
        scope=scope,
        description=f"Observed {observation.count} times: {observation.action}",
        conditions=observation.conditions,
        proposed_action=observation.action,
        authority_required=authority_required,
        source_scope=source_scope,
        evidence_refs=observation.item_ids,
    )
    source.add(definition)
    record = registry.propose(definition, reason=f"observed {observation.count} times",
                              evidence=observation.item_ids)
    assert record.state is RuleState.PROPOSED
    assert not record.auto_handles
    return record


# -- retirement recovery -------------------------------------------------------------------------


@dataclass(frozen=True)
class ReplayEntry:
    item_id: str
    original_action: str | None
    effect_already_applied: bool
    safe_to_reapply: bool
    route: str          # "human-review" | "human-review-with-warning"
    note: str


@dataclass(frozen=True)
class ReplayPlan:
    rule_id: str
    version: int
    entries: tuple[ReplayEntry, ...]

    @property
    def needs_warning(self) -> tuple[ReplayEntry, ...]:
        return tuple(e for e in self.entries if not e.safe_to_reapply)

    def summary(self) -> dict[str, Any]:
        return {
            "rule": f"{self.rule_id} v{self.version}",
            "items": len(self.entries),
            "already_applied": sum(1 for e in self.entries if e.effect_already_applied),
            "unsafe_to_reapply": len(self.needs_warning),
            "all_routed_to_human": all(e.route.startswith("human-review") for e in self.entries),
        }


def plan_replay(
    receipts: Sequence[TriageReceipt],
    rule_id: str,
    version: int,
    *,
    irreversible: frozenset[str] = IRREVERSIBLE_ACTIONS,
) -> ReplayPlan:
    """Every item this rule version auto-handled, routed back to review, exactly once each.

    Duplicate-effect protection is the `seen` set plus `safe_to_reapply`: an item appears once in
    the plan however many receipts it has, and an item whose action already left the system is
    never marked safe to re-run.
    """
    entries: list[ReplayEntry] = []
    seen: set[str] = set()
    for r in receipts:
        if r.rule_id != rule_id or r.rule_version != version:
            continue
        if r.disposition is not Disposition.AUTO_HANDLED:
            continue
        if r.item_id in seen:
            continue
        seen.add(r.item_id)
        applied = r.overridden_by is None
        unsafe = (r.action or "") in irreversible
        entries.append(
            ReplayEntry(
                item_id=r.item_id,
                original_action=r.action,
                effect_already_applied=applied,
                safe_to_reapply=not unsafe,
                route="human-review-with-warning" if unsafe else "human-review",
                note=(
                    f"{r.action!r} already left the system; do not re-run it, show the human what "
                    "happened" if unsafe else "no external effect; safe to decide again"
                ),
            )
        )
    return ReplayPlan(rule_id, version, tuple(entries))
