"""Rule definitions and the B01 seam.

B01 owns the Brain-side rule and knowledge interface and has not published. `RuleDefinitionPort`
is the seam proposed to that owner (Terminal 02); `FixtureRuleSource` is the local implementation
that makes T02 testable before B01 lands. Swapping in the real one is one class.

THREE PROPERTIES THE SEAM MUST HOLD, and each is asserted in the suite because T02's authority
gate is built on them:

1. **A rule definition can never widen permission.** `authority_required` and `brains_audience`
   are INPUTS to a decision, never outputs of it. A rule that matches an item cannot thereby give
   itself a source, an audience or an approval level it was not already granted.
2. **Versions are immutable and addressable.** Editing a definition produces a new version. Only
   an exact `(rule_id, version)` can ever auto-handle anything, so a silent edit cannot inherit
   the previous version's approval.
3. **`digest()` covers the whole definition set in force**, so an evaluation receipt can pin what
   it was evaluated against.

`match` is deliberately opaque to B01: the Brain says what a rule MEANS and what authority it
needs; the matching predicate belongs to the triage engine.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol, Sequence

SEAM_DRAFT_VERSION = "ruledefinition-draft-0.1.0+cap05"

#: Actions whose effect leaves the system. A rule proposing one of these may not declare less than
#: `external` authority. The list is deliberately the same one `evaluation.IRREVERSIBLE_ACTIONS`
#: uses for replay safety: an action that cannot be un-done is an action that needed real
#: authority before it was done.
EFFECTFUL_ACTIONS = frozenset({"send", "publish", "pay", "delete", "archive-at-source"})


class RuleDefinitionError(ValueError):
    pass


@dataclass(frozen=True)
class RuleDefinition:
    """One rule, at one version. Immutable."""

    rule_id: str
    version: int
    scope: str
    description: str
    #: Field/value conditions. Every condition must hold for the rule to match.
    conditions: Mapping[str, Any]
    proposed_action: str
    #: The authority this rule's action needs. It is checked AGAINST the grant; it never IS one.
    authority_required: str = "internal"
    #: Which Brains the rule may cite. Also an input, never a grant.
    brains_audience: tuple[str, ...] = ()
    #: Sources the rule may act on. A rule cannot act on a source outside this set.
    source_scope: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    #: A SECOND AXIS, not a level, and NOT a thing that can be granted. T08-REV-018 gap 2 asked
    #: for the axis; CAP14-REV-022 then ruled how it works, and the contract's mechanism is
    #: canonical: `canon_touching` plus `surfacing: human`, with the flag never lowered (AR-I3).
    #: There is no `canon_touching_granted` on an approval, and my first fixture wrongly assumed
    #: one. So a canon-touching rule ALWAYS surfaces to a human and no grant lifts that, which is
    #: the same treatment an item carrying the flag already gets. Unreachable today because
    #: nothing writes canon from a rule; it lands before anything can.
    canon_touching: bool = False

    def __post_init__(self) -> None:
        if self.version < 1:
            raise RuleDefinitionError("rule versions start at 1")
        if not self.conditions:
            raise RuleDefinitionError(
                f"rule {self.rule_id} has no conditions and would match everything"
            )
        # UNDER-DECLARATION IS REFUSED AT CONSTRUCTION, T08-REV-018 gap 3. An action that leaves
        # the system cannot be declared as needing only internal authority: the declaration is an
        # input to the gate, so a rule that under-declares is a rule that walks through it. Caught
        # here rather than at match time, because a rule that cannot be built cannot be activated.
        if self.proposed_action in EFFECTFUL_ACTIONS and self.authority_required in (
            "none", "internal",
        ):
            raise RuleDefinitionError(
                f"rule {self.rule_id} proposes {self.proposed_action!r}, which leaves the system, "
                f"but declares only {self.authority_required!r} authority. An effectful action "
                "requires at least 'external'; declaring less is how a rule walks through the gate "
                "that reads the declaration."
            )

    @property
    def key(self) -> tuple[str, int]:
        return (self.rule_id, self.version)

    @property
    def version_hash(self) -> str:
        blob = json.dumps(
            {
                "rule_id": self.rule_id,
                "version": self.version,
                "scope": self.scope,
                "conditions": dict(sorted(self.conditions.items())),
                "proposed_action": self.proposed_action,
                "authority_required": self.authority_required,
                "brains_audience": sorted(self.brains_audience),
                "source_scope": sorted(self.source_scope),
                "canon_touching": self.canon_touching,
            },
            sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        return "rule_" + hashlib.sha256(blob).hexdigest()[:32]

    def matches(self, item: Mapping[str, Any]) -> bool:
        """Every condition must hold. A rule with no conditions cannot exist (see __post_init__)."""
        return all(item.get(k) == v for k, v in self.conditions.items())

    def revise(self, **changes: Any) -> "RuleDefinition":
        data = {f: getattr(self, f) for f in self.__dataclass_fields__}
        data.update(changes)
        data["version"] = self.version + 1
        return RuleDefinition(**data)


class RuleDefinitionPort(Protocol):
    """What T02 needs from B01. Read-only: rule machinery never writes to a Brain."""

    def get(self, rule_id: str, version: int) -> RuleDefinition | None: ...

    def list_for_scope(self, scope: str) -> Sequence[RuleDefinition]: ...

    def audience_for(self, rule_id: str) -> tuple[str, ...]: ...

    def digest(self) -> str: ...


class FixtureRuleSource:
    """Local implementation of the seam. Replace with B01's when it publishes."""

    def __init__(self, definitions: Sequence[RuleDefinition] = ()):
        self._defs: dict[tuple[str, int], RuleDefinition] = {d.key: d for d in definitions}

    def add(self, definition: RuleDefinition) -> RuleDefinition:
        if definition.key in self._defs:
            raise RuleDefinitionError(
                f"{definition.rule_id} v{definition.version} already exists; a change makes a new "
                "version, it does not mutate one"
            )
        self._defs[definition.key] = definition
        return definition

    def get(self, rule_id: str, version: int) -> RuleDefinition | None:
        return self._defs.get((rule_id, version))

    def list_for_scope(self, scope: str) -> tuple[RuleDefinition, ...]:
        return tuple(d for d in self._defs.values() if d.scope == scope)

    def versions_of(self, rule_id: str) -> tuple[RuleDefinition, ...]:
        return tuple(sorted((d for d in self._defs.values() if d.rule_id == rule_id),
                            key=lambda d: d.version))

    def audience_for(self, rule_id: str) -> tuple[str, ...]:
        versions = self.versions_of(rule_id)
        return versions[-1].brains_audience if versions else ()

    def digest(self) -> str:
        blob = json.dumps(sorted(d.version_hash for d in self._defs.values()),
                          separators=(",", ":")).encode("utf-8")
        return "ruleset_" + hashlib.sha256(blob).hexdigest()[:32]


def seam_digest() -> str:
    """Digest of the proposed seam's shape, for pinning in both handoff folders."""
    shape = {
        "version": SEAM_DRAFT_VERSION,
        "port": ["get", "list_for_scope", "audience_for", "digest"],
        "RuleDefinition": [
            "rule_id", "version", "scope", "description", "conditions", "proposed_action",
            "authority_required", "brains_audience", "source_scope", "evidence_refs",
        ],
        "properties": [
            "a definition is an input to authority, never a grant of it",
            "versions are immutable and addressable; an edit is a new version",
            "digest() covers the whole definition set in force",
        ],
    }
    return "seam_" + hashlib.sha256(
        json.dumps(shape, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:32]
