"""Triage: decide what a rule may do to an item, and record why, every time.

FOUR DISPOSITIONS, and there is no fifth:

    auto-handled  an exact approved ACTIVE rule version matched, within its granted authority
    shadow-only   a shadow rule would have matched; NOTHING was applied; the answer is recorded
    to-human      ambiguous, out of authority, out of scope, or no rule matched (the residue)
    blocked       a rule matched but asked for more than it was granted -- recorded loudly

THE AUTHORITY GATE. `grant` is what the operator gave this triage run; `authority_required` is
what the rule's definition asks for. A rule that asks for more is BLOCKED, never downgraded and
never quietly allowed. This is the "rules cannot grant broader source/capability permissions"
property, and it is checked on every single item rather than at activation time -- because a grant
can be narrowed after a rule was activated, and the item in front of us is what matters.

AMBIGUITY IS NOT RESOLVED BY PRIORITY. When two active rules match and propose different actions,
the item goes to a human. Picking the higher-priority rule would be inventing a decision nobody
made, and the operator is the one who has to live with it.

EVERY AUTO-HANDLED ITEM GETS A RECEIPT. That is the acceptance line "all handled items have
receipts", and it is what makes retirement recoverable: `replay.py` finds the affected items by
reading the receipts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

from .definitions import RuleDefinition
from .lifecycle import RuleRecord, RuleRegistry, RuleState, utcnow
from .permissions import hard_flags_force_review, permits_required, self_approved


class Disposition(str, Enum):
    AUTO_HANDLED = "auto-handled"
    SHADOW_ONLY = "shadow-only"
    TO_HUMAN = "to-human"
    BLOCKED = "blocked"

    @property
    def applied(self) -> bool:
        return self is Disposition.AUTO_HANDLED


_AUTHORITY_RANK = {"none": 0, "internal": 1, "external": 2, "financial": 3, "unknown": 99}


def _rank(name: str) -> int:
    return _AUTHORITY_RANK.get(name, 99)


@dataclass(frozen=True)
class AuthorityGrant:
    """What this triage run is allowed to do. Narrower than, or equal to, what the operator gave."""

    max_authority: str = "internal"
    sources: tuple[str, ...] = ()
    brains: tuple[str, ...] = ()
    #: Permission names this run holds, when the caller works in permission sets rather than
    #: authority levels. Empty means "the levels above are the whole model", which is what the
    #: in-repo rule fixtures use.
    permissions: tuple[str, ...] = ()

    @staticmethod
    def granted_permissions_of(rule: RuleDefinition) -> tuple[str, ...]:
        """A rule's own permission set. Read from the definition; never computed from the item."""
        return tuple(getattr(rule, "granted_permissions", ()) or ())

    def permits(self, rule: RuleDefinition, item: Mapping[str, Any]) -> tuple[bool, str]:
        # THE INTERSECTION FIRST, because CAP14-REV-011 checks for it in the code and not only in
        # the results: effective = route INTERSECT granted, and the required set is tested against
        # the effective set. Consulting the rule's grants alone is the defect it plants.
        route = item.get("route_permissions")
        required = item.get("required_permissions")
        if route is not None and required is not None:
            allowed, lacked = permits_required(route, self.granted_permissions_of(rule), required)
            if not allowed:
                return False, (
                    f"rule_cannot_broaden: the route never granted {sorted(lacked)}; a rule "
                    "narrows what its route already allowed and can never add to it"
                )
        if _rank(rule.authority_required) > _rank(self.max_authority):
            return False, (
                f"rule asks for {rule.authority_required} authority; this run grants "
                f"{self.max_authority}"
            )
        source = item.get("source_key") or item.get("source") or ""
        if rule.source_scope and source not in rule.source_scope:
            return False, f"item source {source!r} is outside the rule's own source scope"
        if self.sources and source not in self.sources:
            return False, f"item source {source!r} is outside this run's granted sources"
        outside = [b for b in rule.brains_audience if self.brains and b not in self.brains]
        if outside:
            return False, f"rule cites Brains outside the grant: {outside}"
        return True, ""


@dataclass(frozen=True)
class TriageReceipt:
    """Proof of what happened to one item, and why. Written for every disposition."""

    item_id: str
    disposition: Disposition
    decided_at: str
    rule_id: str | None = None
    rule_version: int | None = None
    rule_version_hash: str | None = None
    ruleset_digest: str | None = None
    action: str | None = None
    reason: str = ""
    candidates: tuple[str, ...] = ()
    overridden_by: str | None = None
    override_action: str | None = None

    @property
    def auto(self) -> bool:
        return self.disposition.applied


class TriageEngine:
    """Applies the registry to items. Holds the receipt log and the override audit."""

    def __init__(self, registry: RuleRegistry, source, grant: AuthorityGrant | None = None):
        self.registry = registry
        self.source = source
        self.grant = grant or AuthorityGrant()
        self.receipts: list[TriageReceipt] = []

    # -- the decision ----------------------------------------------------------------------------

    def triage(self, item: Mapping[str, Any]) -> TriageReceipt:
        item_id = str(item["item_id"])
        digest = self.source.digest()

        # THE HARD FLAGS GATE BEFORE ANY RULE IS CONSULTED. `signal-vocabulary` and
        # `surfacing-policy` in this estate both say external and canon_touching are gates and not
        # weights: anything carrying either always surfaces, whatever a rule would have said. Doing
        # it here rather than inside the rule loop means no rule can be written that skips it.
        canon_rules = [
            r for r in self.registry.all_records()
            if getattr(r.definition, "canon_touching", False) and r.definition.matches(item)
        ]
        if canon_rules or hard_flags_force_review(item, item.get("parent")):
            return self._record(TriageReceipt(
                item_id=item_id, disposition=Disposition.TO_HUMAN, decided_at=utcnow(),
                ruleset_digest=digest,
                reason="hard_flag_always_surfaces: external or canon_touching is a gate, not a "
                       "weight, and inherited by OR from any parent",
            ))

        active = [r for r in self.registry.active() if r.definition.matches(item)]
        shadow = [
            r for r in self.registry.all_records()
            if r.state is RuleState.SHADOW and r.definition.matches(item)
        ]

        permitted, blocked = [], []
        for record in active:
            if self_approved(getattr(record.definition, "authored_by", None),
                             record.approved_by):
                blocked.append((record, "self_approval: an agent-authored rule needs a human "
                                        "approver, and this one has none"))
                continue
            ok, why = self.grant.permits(record.definition, item)
            (permitted if ok else blocked).append((record, why))

        if blocked and not permitted:
            record, why = blocked[0]
            return self._record(TriageReceipt(
                item_id=item_id, disposition=Disposition.BLOCKED, decided_at=utcnow(),
                rule_id=record.definition.rule_id, rule_version=record.definition.version,
                rule_version_hash=record.definition.version_hash, ruleset_digest=digest,
                reason=why, candidates=tuple(r.definition.rule_id for r, _ in blocked),
            ))

        if len(permitted) > 1:
            actions = {r.definition.proposed_action for r, _ in permitted}
            if len(actions) > 1:
                return self._record(TriageReceipt(
                    item_id=item_id, disposition=Disposition.TO_HUMAN, decided_at=utcnow(),
                    ruleset_digest=digest,
                    reason="ambiguous: matching rules propose different actions",
                    candidates=tuple(sorted(r.definition.rule_id for r, _ in permitted)),
                ))

        if permitted:
            record = permitted[0][0]
            d = record.definition
            return self._record(TriageReceipt(
                item_id=item_id, disposition=Disposition.AUTO_HANDLED, decided_at=utcnow(),
                rule_id=d.rule_id, rule_version=d.version, rule_version_hash=d.version_hash,
                ruleset_digest=digest, action=d.proposed_action,
                reason="exact approved active rule version",
            ))

        if shadow:
            d = shadow[0].definition
            return self._record(TriageReceipt(
                item_id=item_id, disposition=Disposition.SHADOW_ONLY, decided_at=utcnow(),
                rule_id=d.rule_id, rule_version=d.version, rule_version_hash=d.version_hash,
                ruleset_digest=digest, action=d.proposed_action,
                reason="shadow evaluation only; nothing was applied",
                candidates=tuple(sorted(r.definition.rule_id for r in shadow)),
            ))

        inactive = [
            r for r in self.registry.all_records()
            if r.definition.matches(item) and not r.auto_handles
            and r.state is not RuleState.SHADOW
        ]
        if inactive:
            first = inactive[0]
            # TWO DIFFERENT FACTS, and an operator needs them apart. A rule sitting in ACTIVE whose
            # approved hash does not match its current definition was EDITED and is waiting for a
            # human to re-approve it; a proposed or retired rule was never or is no longer meant to
            # handle anything. Reporting both as "not active" hides the one that has an action.
            if first.state is RuleState.ACTIVE:
                reason = (
                    "unapproved_rule_version: this rule is active but its approved version does "
                    "not match its current definition, so it reverts to review until re-approved"
                )
            else:
                reason = f"rule_not_active: {first.state.value} rules do not handle items"
            return self._record(TriageReceipt(
                item_id=item_id, disposition=Disposition.TO_HUMAN, decided_at=utcnow(),
                rule_id=first.definition.rule_id,
                rule_version=first.definition.version,
                ruleset_digest=digest,
                reason=reason,
                candidates=tuple(sorted(r.definition.rule_id for r in inactive)),
            ))
        return self._record(TriageReceipt(
            item_id=item_id, disposition=Disposition.TO_HUMAN, decided_at=utcnow(),
            ruleset_digest=digest, reason="residue: no active or shadow rule matched",
        ))

    # -- override audit -----------------------------------------------------------------------------

    def override(self, item_id: str, *, by: str, action: str, note: str = "") -> TriageReceipt:
        """A human disagrees with what a rule did. Audited; it does not change the rule.

        Correction is a learning SIGNAL, not a permission change: the override is recorded against
        the exact rule version so `evaluation.py` can count it, and the rule keeps whatever
        authority it already had until a human promotes or retires it.
        """
        if by.strip().lower() in ("", "agent", "system", "auto"):
            raise ValueError("an override needs a named human")
        original = self.last_for(item_id)
        if original is None:
            raise ValueError(f"no triage receipt for {item_id} to override")
        receipt = TriageReceipt(
            item_id=item_id, disposition=original.disposition, decided_at=utcnow(),
            rule_id=original.rule_id, rule_version=original.rule_version,
            rule_version_hash=original.rule_version_hash,
            ruleset_digest=original.ruleset_digest, action=original.action,
            reason=note or "operator override", overridden_by=by, override_action=action,
        )
        self.receipts.append(receipt)
        return receipt

    # -- reads ---------------------------------------------------------------------------------------

    def _record(self, receipt: TriageReceipt) -> TriageReceipt:
        self.receipts.append(receipt)
        return receipt

    def last_for(self, item_id: str) -> TriageReceipt | None:
        for r in reversed(self.receipts):
            if r.item_id == item_id:
                return r
        return None

    def auto_handled(self) -> tuple[TriageReceipt, ...]:
        return tuple(r for r in self.receipts if r.auto and r.overridden_by is None)

    def overrides(self) -> tuple[TriageReceipt, ...]:
        return tuple(r for r in self.receipts if r.overridden_by is not None)

    def handled_by(self, rule_id: str, version: int | None = None) -> tuple[TriageReceipt, ...]:
        return tuple(
            r for r in self.receipts
            if r.rule_id == rule_id and r.auto and (version is None or r.rule_version == version)
        )
