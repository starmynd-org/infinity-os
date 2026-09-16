"""Pure planning for the disabled build swarm described in SWARM-DESIGN.md."""

from __future__ import annotations

from dataclasses import dataclass

from .errors import ImportRefusal
from .model import Coverage, Outcome


TYPE_ORDER = (
    "Knowledge",
    "Data",
    "Memory",
    "Tool",
    "Skill",
    "Rule",
    "Command",
    "Workflow",
    "Agent",
    "Project",
    "Output",
)


@dataclass(frozen=True)
class BuildStage:
    order: int
    agent: str
    purpose: str
    member_ids: tuple[str, ...]
    activation: str = "disabled"


def plan_swarm(outcomes: list[Outcome], coverage: Coverage) -> list[BuildStage]:
    if coverage.denominator != len(outcomes) or sum(coverage.counts.values()) != len(outcomes):
        raise ImportRefusal(
            "COUNT-MISMATCH",
            "swarm-plan",
            "the supplied outcomes do not equal the measured import denominator",
            "plan only from the complete ESTATE/1.0 outcome ledger",
        )
    if outcomes and not coverage.control_fired:
        raise ImportRefusal(
            "COUNT-MISMATCH",
            "swarm-plan-control",
            "the importer positive control did not fire",
            "rerun the producer-specific parser before planning builders",
        )
    retained = [outcome for outcome in outcomes if outcome.entity is not None]
    stages = [
        BuildStage(
            0,
            "estate-integrity-reviewer",
            "verify identities, source references, named losses, and denominator coverage",
            tuple(outcome.entity.entity_id for outcome in retained if outcome.entity),
        )
    ]
    unresolved = tuple(
        outcome.entity.entity_id
        for outcome in retained
        if outcome.entity and outcome.entity.authority_required == "unknown"
    )
    if unresolved:
        stages.append(
            BuildStage(
                1,
                "estate-authority-reviewer",
                "resolve unknown authority before approval",
                unresolved,
            )
        )
    next_order = 2
    for entity_type in TYPE_ORDER:
        member_ids = tuple(
            outcome.entity.entity_id
            for outcome in retained
            if outcome.entity and outcome.entity.entity_type == entity_type
        )
        if member_ids:
            stages.append(
                BuildStage(
                    next_order,
                    f"estate-{entity_type.lower()}-builder",
                    f"propose disabled {entity_type} artifacts from portable records",
                    member_ids,
                )
            )
            next_order += 1
    for agent, purpose in (
        ("estate-dependency-reviewer", "check references and dependency cycles"),
        ("estate-safety-reviewer", "check secrets, activation, tool boundaries, and queue isolation"),
        ("estate-acceptance-reviewer", "prepare decidable ITEM/1.0 review or approval proposals"),
    ):
        stages.append(
            BuildStage(
                next_order,
                agent,
                purpose,
                tuple(outcome.entity.entity_id for outcome in retained if outcome.entity),
            )
        )
        next_order += 1
    return stages
