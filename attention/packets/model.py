"""The Work Packet: a proposal complete enough for a human to decide on in one pass.

Section 7 of the end-to-end flow lists what a packet must contain. This module makes that list a
type rather than a convention, because "fully scoped before human approval" is not a property you
can achieve by remembering to fill things in.

THREE DESIGN RULES, each of which closes a specific failure the review found:

1. **A packet is immutable and content-addressed.** `version_hash` is derived from the substantive
   fields, so editing anything material produces a NEW version rather than mutating one. Approval
   binds to a specific hash (see `approval.py`), which is what makes "approval applies to a
   specific immutable packet version" enforceable instead of aspirational.

2. **Every substantive claim carries a citation or an explicit uncertainty.** `Claim` cannot be
   constructed with neither. An agent's confident prose with no evidence behind it is the thing
   that gets believed later and is wrong, so the type refuses to represent it.

3. **Unknowns are first-class.** `UNKNOWN` is a real value for impact, cost and effort, and it is
   NOT the same as zero or as low. A packet that says "impact: unknown" is honest and reviewable;
   one that quietly defaults to "low" is neither.

Nothing here approves, dispatches or executes. T01 stops at the approval boundary by construction:
there is no method on a packet that runs it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence

CONTRACT_DRAFT_VERSION = "workpacket-draft-0.1.0+cap05"

#: The value that means "we do not know", distinct from zero, low, or absent.
UNKNOWN = "unknown"


class Risk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNKNOWN = "unknown"


class Authority(str, Enum):
    """How strong an approval this packet needs. Derived from consequence, never from confidence."""

    NONE = "none"                    # nothing leaves the system
    INTERNAL = "internal"            # internal records change
    EXTERNAL = "external"            # something is sent, published or promised
    FINANCIAL = "financial"          # money or a binding commitment moves
    UNKNOWN = "unknown"              # not yet determined; blocks execution

    @property
    def blocks_execution(self) -> bool:
        return self is Authority.UNKNOWN


class PacketError(ValueError):
    """The packet cannot be represented. Never silently repaired."""


@dataclass(frozen=True)
class EvidenceRef:
    """A pointer into the capture journal, with the audience that evidence is cleared for.

    `capture_id` is I01's derived id, so a packet's evidence is addressable and re-readable rather
    than summarised into the packet and lost. `brains_audience` travels from the source manifest;
    a packet may not cite evidence to an audience the evidence was never cleared for.
    """

    capture_id: str
    source_key: str
    revision: int
    excerpt: str = ""
    brains_audience: tuple[str, ...] = ()
    external: bool = True
    flags: tuple[str, ...] = ()

    def visible_to(self, audience: str) -> bool:
        return audience in self.brains_audience


@dataclass(frozen=True)
class Claim:
    """One substantive statement in a packet. Cited, or explicitly uncertain. Never neither."""

    text: str
    cites: tuple[str, ...] = ()
    uncertain: bool = False
    uncertainty_note: str = ""

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise PacketError("a claim with no text is not a claim")
        if not self.cites and not self.uncertain:
            raise PacketError(
                f"claim {self.text[:60]!r} cites no evidence and is not marked uncertain; "
                "an uncited confident claim is the defect this type exists to prevent"
            )
        if self.uncertain and not self.uncertainty_note.strip():
            raise PacketError("an uncertain claim must say what is uncertain about it")


@dataclass(frozen=True)
class ProposedChange:
    """A change this packet proposes to something durable. Not applied by anything here."""

    target_kind: str      # task | party | project | brain | repo
    target_id: str
    summary: str
    reversible: bool = True


@dataclass(frozen=True)
class ExternalAction:
    """Something that leaves the system. Always requires authority proportionate to its risk.

    `action_id` EXISTS BECAUSE AN APPROVAL MUST NAME WHAT IT AUTHORISES. C01's AD-I2 binds a
    decision to specific action ids, and until this field existed an approval could only say "this
    packet version", so a packet with three actions was approved as an indivisible lump. That was a
    real gap in T01 rather than a mapping inconvenience.

    It is DERIVED, not assigned, for the same reason `capture_id` is: two processes that see the
    same action agree on its id without coordinating, and an action that is materially edited gets
    a different id, so an approval naming the old one no longer matches. Bookkeeping cannot drift
    from content because there is no bookkeeping.

    `reversibility` REPLACES a boolean that could not say what it needed to. C01's enum has three
    values and `reversible=False` collapsed `costly` and `irreversible` into one, which is exactly
    the distinction an approver needs: "expensive to undo" and "cannot be undone" call for
    different decisions. `reversible` survives as a derived property so existing callers and the
    completeness rules keep working.
    """

    kind: str             # send | publish | pay | modify-live-system
    target: str
    summary: str
    #: C01 `provider.operation`, e.g. `mail.send`. Empty means this lane has not classified it yet,
    #: which the projection refuses rather than guessing a capability name.
    capability: str = ""
    reversibility: str = "irreversible"
    canon_touching: bool = False
    #: Set only to preserve an id issued elsewhere. Left empty it is derived from the content.
    action_id: str = ""

    def __post_init__(self) -> None:
        if self.reversibility not in ("reversible", "costly", "irreversible"):
            raise PacketError(
                f"reversibility {self.reversibility!r} is not one of reversible, costly, "
                "irreversible; an unrecognised value would be read as a risk level nobody set"
            )

    @property
    def reversible(self) -> bool:
        """Kept so `has_irreversible_action` and existing callers do not change meaning."""
        return self.reversibility == "reversible"

    def derived_id(self, index: int = 0) -> str:
        """Stable for identical content; different the moment the action materially changes.

        `index` disambiguates two genuinely identical actions in one packet, which is legitimate
        (send the same notice to the same target twice) and must not collapse to one id.
        """
        if self.action_id:
            return self.action_id
        joined = "\x1f".join(
            (self.kind, self.target, self.summary, self.capability, self.reversibility,
             str(self.canon_touching), str(index))
        )
        return "act_" + hashlib.sha256(joined.encode("utf-8")).hexdigest()[:24]


@dataclass(frozen=True)
class Alternative:
    """A rejected option, kept so the human can see what was considered and why it lost."""

    summary: str
    why_not: str


@dataclass(frozen=True)
class WorkPacket:
    """An immutable proposal. Its `version_hash` is its identity for approval."""

    packet_id: str
    objective: str
    rationale: tuple[Claim, ...]
    evidence: tuple[EvidenceRef, ...]
    deliverables: tuple[str, ...]
    destination: str
    acceptance_criteria: tuple[str, ...]
    proposed_owner: str
    authority_required: Authority
    risk: Risk
    expected_cost: str          # a number, a range, or UNKNOWN
    effort: str                 # a band, or UNKNOWN
    urgency: str
    dependencies: tuple[str, ...] = ()
    missing_information: tuple[str, ...] = ()
    proposed_changes: tuple[ProposedChange, ...] = ()
    external_actions: tuple[ExternalAction, ...] = ()
    alternatives: tuple[Alternative, ...] = ()
    rollback: str = ""
    audience: tuple[str, ...] = ()
    version: int = 1
    supersedes_hash: str | None = None
    contract_version: str = CONTRACT_DRAFT_VERSION

    #: Fields that change the MEANING of the packet. Editing one of these produces a new version
    #: and voids any approval bound to the old hash. Presentation-only fields are excluded on
    #: purpose: re-rendering a packet must not invalidate a decision a human already made.
    SUBSTANTIVE = (
        "objective", "deliverables", "destination", "acceptance_criteria", "proposed_owner",
        "authority_required", "risk", "expected_cost", "effort", "urgency", "dependencies",
        "proposed_changes", "external_actions", "rollback", "audience",
    )

    def _substantive_view(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name in self.SUBSTANTIVE:
            value = getattr(self, name)
            if isinstance(value, Enum):
                out[name] = value.value
            elif isinstance(value, tuple):
                out[name] = [asdict(v) if hasattr(v, "__dataclass_fields__") else v for v in value]
            else:
                out[name] = value
        out["packet_id"] = self.packet_id
        out["evidence"] = sorted(e.capture_id for e in self.evidence)
        return out

    @property
    def version_hash(self) -> str:
        blob = json.dumps(
            self._substantive_view(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return "wp_" + hashlib.sha256(blob).hexdigest()[:40]

    def revise(self, **changes: Any) -> "WorkPacket":
        """Produce the NEXT version. The original object is untouched and stays readable."""
        unknown = set(changes) - set(self.__dataclass_fields__)
        if unknown:
            raise PacketError(f"unknown packet fields: {sorted(unknown)}")
        data = {f: getattr(self, f) for f in self.__dataclass_fields__}
        data.update(changes)
        data["version"] = self.version + 1
        data["supersedes_hash"] = self.version_hash
        return WorkPacket(**data)

    def action_ids(self) -> tuple[str, ...]:
        """Every action's id, in declaration order, with the index that disambiguates duplicates.

        This is what an approval names under AD-I2. It is derived from the actions themselves, so
        a packet cannot carry an action the list does not know about and cannot list an action it
        does not carry.
        """
        return tuple(a.derived_id(i) for i, a in enumerate(self.external_actions))

    def cited_capture_ids(self) -> set[str]:
        return {c for claim in self.rationale for c in claim.cites}

    def has_irreversible_action(self) -> bool:
        return any(not a.reversible for a in self.external_actions) or any(
            not c.reversible for c in self.proposed_changes
        )

    def to_wire(self) -> dict[str, Any]:
        d = asdict(self)
        d["authority_required"] = self.authority_required.value
        d["risk"] = self.risk.value
        d["version_hash"] = self.version_hash
        return d
