"""The completeness validator: the gate that stops an incomplete proposal from executing.

The acceptance line is "incomplete packets cannot execute". That is enforced here and nowhere
else, so this module returns a decision object rather than a boolean — a human needs to see WHICH
requirement failed, and a packet that fails on "no rollback for an irreversible action" is a
different conversation from one that fails on "nobody proposed an owner".

WHY BLOCKING AND ADVISORY ARE SEPARATE. A missing rollback for an irreversible external action is
blocking; a missing alternative is advisory. Collapsing them either lets dangerous packets through
or trains the operator to click past warnings. Only `blocking` findings set `can_execute` False.

WHAT THIS VALIDATOR REFUSES TO DO. It does not fill anything in. A validator that supplies a
default acceptance criterion has invented the thing the human was supposed to decide, and the
packet then looks complete while being empty. Missing input becomes a CLARIFICATION REQUEST
(`clarification.py`), which goes back to a human or to the assembling agent — never a default.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from .model import UNKNOWN, Authority, Risk, WorkPacket


@dataclass(frozen=True)
class Finding:
    rule: str
    message: str
    blocking: bool
    field_name: str = ""


@dataclass(frozen=True)
class Completeness:
    packet_id: str
    version_hash: str
    findings: tuple[Finding, ...]

    @property
    def blocking(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.blocking)

    @property
    def advisory(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if not f.blocking)

    @property
    def can_execute(self) -> bool:
        return not self.blocking

    @property
    def complete(self) -> bool:
        return not self.findings

    def summary(self) -> dict:
        return {
            "packet_id": self.packet_id,
            "version_hash": self.version_hash,
            "can_execute": self.can_execute,
            "blocking": [f.rule for f in self.blocking],
            "advisory": [f.rule for f in self.advisory],
        }


def _blank(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return len(value) == 0


def check(packet: WorkPacket, *, known_capture_ids: Sequence[str] | None = None) -> Completeness:
    """Evaluate one packet. Pure: it reads the packet and returns findings."""
    f: list[Finding] = []

    # -- the things a human cannot decide without -------------------------------------------
    if _blank(packet.objective):
        f.append(Finding("objective-missing", "The packet has no objective.", True, "objective"))
    if _blank(packet.acceptance_criteria):
        f.append(Finding(
            "acceptance-missing",
            "No acceptance criteria: nobody can tell whether this was done.", True,
            "acceptance_criteria",
        ))
    if _blank(packet.deliverables):
        f.append(Finding("deliverables-missing", "Nothing is proposed to be produced.", True,
                         "deliverables"))
    if _blank(packet.destination):
        f.append(Finding("destination-missing", "The deliverable has nowhere to go.", True,
                         "destination"))
    if _blank(packet.proposed_owner):
        f.append(Finding("owner-missing", "No proposed owner, agent, workflow or swarm.", True,
                         "proposed_owner"))

    # -- evidence ----------------------------------------------------------------------------
    if _blank(packet.evidence):
        f.append(Finding(
            "source-missing",
            "No evidence is cited. A proposal with no source cannot be checked against what "
            "actually happened.", True, "evidence",
        ))
    cited = packet.cited_capture_ids()
    held = {e.capture_id for e in packet.evidence}
    dangling = cited - held
    if dangling:
        f.append(Finding(
            "citation-dangling",
            f"Claims cite evidence the packet does not carry: {sorted(dangling)}.", True,
            "rationale",
        ))
    if known_capture_ids is not None:
        unknown_refs = held - set(known_capture_ids)
        if unknown_refs:
            f.append(Finding(
                "evidence-unknown-to-journal",
                f"Evidence not present in the capture journal: {sorted(unknown_refs)}.", True,
                "evidence",
            ))

    # -- authority and consequence -------------------------------------------------------------
    if packet.authority_required.blocks_execution:
        f.append(Finding(
            "authority-unknown",
            "Required authority is unknown. Unknown authority is never treated as 'none'.", True,
            "authority_required",
        ))
    if packet.risk is Risk.UNKNOWN:
        f.append(Finding(
            "impact-unknown",
            "Risk is unknown. This is reviewable, but it cannot execute unattended.", True, "risk",
        ))
    if packet.external_actions and packet.authority_required in (Authority.NONE, Authority.INTERNAL):
        f.append(Finding(
            "authority-too-weak",
            f"The packet proposes {len(packet.external_actions)} external action(s) but asks for "
            f"only '{packet.authority_required.value}' authority.", True, "authority_required",
        ))
    if packet.has_irreversible_action() and _blank(packet.rollback):
        f.append(Finding(
            "rollback-missing",
            "An irreversible action is proposed with no rollback, recovery or containment note.",
            True, "rollback",
        ))

    # -- cost, effort, dependencies -------------------------------------------------------------
    if packet.expected_cost == UNKNOWN and packet.authority_required is Authority.FINANCIAL:
        f.append(Finding(
            "cost-unknown-for-financial",
            "A financial packet with an unknown expected cost cannot be approved.", True,
            "expected_cost",
        ))
    if packet.missing_information:
        f.append(Finding(
            "information-missing",
            f"{len(packet.missing_information)} open question(s) remain: "
            f"{list(packet.missing_information)}.", True, "missing_information",
        ))

    # -- advisory ---------------------------------------------------------------------------------
    if _blank(packet.alternatives):
        f.append(Finding("alternatives-absent",
                         "No alternative was recorded; the human sees one option only.", False,
                         "alternatives"))
    if packet.expected_cost == UNKNOWN:
        f.append(Finding("cost-unknown", "Expected cost is unknown.", False, "expected_cost"))
    if packet.effort == UNKNOWN:
        f.append(Finding("effort-unknown", "Effort is unknown.", False, "effort"))
    if _blank(packet.rationale):
        f.append(Finding("rationale-thin", "No rationale claims were recorded.", False,
                         "rationale"))

    # -- audience -----------------------------------------------------------------------------------
    for e in packet.evidence:
        for audience in packet.audience:
            if e.brains_audience and not e.visible_to(audience):
                f.append(Finding(
                    "audience-violation",
                    f"Evidence {e.capture_id} is not cleared for audience {audience!r}; "
                    "cross-Brain context requires an authorised audience.", True, "audience",
                ))
                break

    # -- injected content -----------------------------------------------------------------------------
    flagged = [e for e in packet.evidence if e.flags]
    if flagged:
        f.append(Finding(
            "external-directive-present",
            "Evidence contains instruction-shaped external content "
            f"({sorted({fl for e in flagged for fl in e.flags})}). It is data; the human should "
            "see it flagged before deciding.", False, "evidence",
        ))

    return Completeness(packet.packet_id, packet.version_hash, tuple(f))
