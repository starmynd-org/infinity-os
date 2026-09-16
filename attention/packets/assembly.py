"""Assembling a packet from captured evidence, and asking when it cannot.

The assembler's job is to do the thinking BEFORE interrupting the human, and to be honest when it
could not finish. Two outputs, and it always produces both:

    packet          -- as complete as the evidence allows
    clarifications  -- the exact questions that would complete it

A CLARIFICATION IS NOT A DEFAULT. When the evidence does not say who should own the work, the
assembler asks; it does not put the operator's name in because that is usually right. A packet
completed by guessing is worse than an incomplete one, because the guess is invisible after the
fact while an open question is not.

CONFLICTING EVIDENCE IS SURFACED, NOT RESOLVED. When two captured items disagree, the assembler
records both, marks the claim uncertain and raises a clarification. Picking a winner is a judgment
call with consequences, and the human is the one holding the consequences.

EXTERNAL CONTENT NEVER BECOMES AN INSTRUCTION. Evidence flagged by the capture layer arrives here
already wrapped; the assembler copies the flags onto the `EvidenceRef` and reads the text only as
material to quote. There is no code path from an evidence excerpt to a field the executor obeys.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from .completeness import Completeness, check
from .model import (
    UNKNOWN,
    Alternative,
    Authority,
    Claim,
    EvidenceRef,
    ExternalAction,
    PacketError,
    ProposedChange,
    Risk,
    WorkPacket,
)


@dataclass(frozen=True)
class Clarification:
    """One question that must be answered before the packet can be decided on."""

    field_name: str
    question: str
    why_it_matters: str
    blocking: bool = True


@dataclass(frozen=True)
class Assembled:
    packet: WorkPacket
    clarifications: tuple[Clarification, ...]
    completeness: Completeness

    @property
    def ready_for_decision(self) -> bool:
        return self.completeness.can_execute and not any(c.blocking for c in self.clarifications)


@dataclass(frozen=True)
class EvidenceItem:
    """What the assembler is handed: one captured event, already read out of the journal."""

    capture_id: str
    source_key: str
    revision: int
    excerpt: str
    brains_audience: tuple[str, ...] = ()
    flags: tuple[str, ...] = ()
    #: Structured facts the interpretation stage extracted. Values may disagree between items;
    #: that disagreement is the input to the conflict check below.
    facts: dict[str, Any] = field(default_factory=dict)

    def as_ref(self) -> EvidenceRef:
        return EvidenceRef(
            capture_id=self.capture_id,
            source_key=self.source_key,
            revision=self.revision,
            excerpt=self.excerpt,
            brains_audience=self.brains_audience,
            flags=self.flags,
        )


def _packet_id(objective: str, evidence: Sequence[EvidenceItem]) -> str:
    joined = "\x1f".join([objective, *sorted(e.capture_id for e in evidence)])
    return "pkt_" + hashlib.sha256(joined.encode("utf-8")).hexdigest()[:32]


def find_conflicts(evidence: Sequence[EvidenceItem]) -> dict[str, list[tuple[str, Any]]]:
    """Facts on which the captured items disagree, with who said what."""
    seen: dict[str, list[tuple[str, Any]]] = {}
    for item in evidence:
        for key, value in item.facts.items():
            seen.setdefault(key, []).append((item.capture_id, value))
    return {
        key: pairs
        for key, pairs in seen.items()
        if len({repr(v) for _, v in pairs}) > 1
    }


def assemble(
    *,
    objective: str,
    evidence: Sequence[EvidenceItem],
    deliverables: Sequence[str] = (),
    destination: str = "",
    acceptance_criteria: Sequence[str] = (),
    proposed_owner: str = "",
    authority_required: Authority = Authority.UNKNOWN,
    risk: Risk = Risk.UNKNOWN,
    expected_cost: str = UNKNOWN,
    effort: str = UNKNOWN,
    urgency: str = "normal",
    proposed_changes: Sequence[ProposedChange] = (),
    external_actions: Sequence[ExternalAction] = (),
    alternatives: Sequence[Alternative] = (),
    rollback: str = "",
    audience: Sequence[str] = (),
    rationale: Sequence[Claim] = (),
    known_capture_ids: Sequence[str] | None = None,
) -> Assembled:
    """Build the packet and the list of things that stop it being decidable."""
    if not evidence:
        raise PacketError("a packet is assembled from captured evidence; none was supplied")

    conflicts = find_conflicts(evidence)
    claims = list(rationale)
    clarifications: list[Clarification] = []

    for fact, pairs in sorted(conflicts.items()):
        values = ", ".join(f"{cid}={value!r}" for cid, value in pairs)
        claims.append(
            Claim(
                text=f"Sources disagree about {fact}: {values}.",
                cites=tuple(cid for cid, _ in pairs),
                uncertain=True,
                uncertainty_note=f"{fact} is contradicted across captured evidence and was not "
                "resolved automatically.",
            )
        )
        clarifications.append(
            Clarification(
                field_name=fact,
                question=f"Which is correct for {fact}? ({values})",
                why_it_matters="The packet cannot state this as fact while its own evidence "
                "contradicts itself.",
            )
        )

    packet = WorkPacket(
        packet_id=_packet_id(objective, evidence),
        objective=objective,
        rationale=tuple(claims),
        evidence=tuple(e.as_ref() for e in evidence),
        deliverables=tuple(deliverables),
        destination=destination,
        acceptance_criteria=tuple(acceptance_criteria),
        proposed_owner=proposed_owner,
        authority_required=authority_required,
        risk=risk,
        expected_cost=expected_cost,
        effort=effort,
        urgency=urgency,
        proposed_changes=tuple(proposed_changes),
        external_actions=tuple(external_actions),
        alternatives=tuple(alternatives),
        rollback=rollback,
        audience=tuple(audience),
    )

    completeness = check(packet, known_capture_ids=known_capture_ids)

    # Every blocking completeness finding becomes a QUESTION, never a filled-in default.
    asked = {c.field_name for c in clarifications}
    for finding in completeness.blocking:
        if finding.field_name in asked:
            continue
        clarifications.append(
            Clarification(
                field_name=finding.field_name or finding.rule,
                question=_question_for(finding.rule),
                why_it_matters=finding.message,
            )
        )
        asked.add(finding.field_name)

    return Assembled(packet=packet, clarifications=tuple(clarifications), completeness=completeness)


_QUESTIONS = {
    "objective-missing": "What is this packet trying to achieve?",
    "acceptance-missing": "How will we know this is done and done well?",
    "deliverables-missing": "What should be produced?",
    "destination-missing": "Where does the deliverable go?",
    "owner-missing": "Who or what should do this?",
    "source-missing": "Which captured evidence supports this?",
    "authority-unknown": "What kind of approval does this need: internal, external or financial?",
    "impact-unknown": "What is the risk if this goes wrong?",
    "authority-too-weak": "This sends something outside the system. Confirm the authority level.",
    "rollback-missing": "If this goes wrong, how do we undo or contain it?",
    "cost-unknown-for-financial": "What is the expected cost?",
    "information-missing": "The listed open questions need answers before this can be decided.",
    "citation-dangling": "A claim cites evidence the packet does not carry. Which item is meant?",
    "evidence-unknown-to-journal": "Cited evidence is not in the capture journal. Where is it from?",
    "audience-violation": "This cites evidence not cleared for the packet's audience.",
}


def _question_for(rule: str) -> str:
    return _QUESTIONS.get(rule, f"Resolve: {rule}")
