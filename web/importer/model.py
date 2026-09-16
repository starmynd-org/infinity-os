"""Portable records and denominator-bearing coverage reports."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any

from .errors import ImportRefusal


ENTITY_TYPES = (
    "Command",
    "Agent",
    "Skill",
    "Rule",
    "Workflow",
    "Tool",
    "Knowledge",
    "Data",
    "Memory",
    "Output",
    "Project",
)

DISPOSITIONS = ("imported", "review_required", "dropped", "refused")
AUTHORITIES = ("none", "internal", "external", "financial", "unknown")


@dataclass(frozen=True)
class Loss:
    code: str
    field: str
    disposition: str
    why: str
    source_value_present: bool

    def __post_init__(self) -> None:
        if not self.code or not self.field or not self.why:
            raise ValueError("loss code, field, and why must be non-empty")
        if self.disposition not in {"review_required", "dropped"}:
            raise ValueError("a loss must force review or declare an explicit drop")


@dataclass(frozen=True)
class PortableEntity:
    entity_id: str
    entity_type: str
    name: str
    summary: str
    body: str
    source_ref: dict[str, str]
    activation: str = "disabled"
    authority_required: str = "unknown"
    dependencies: tuple[str, ...] = ()
    extensions: dict[str, Any] = field(default_factory=dict)
    losses: tuple[Loss, ...] = ()

    def __post_init__(self) -> None:
        if self.entity_type not in ENTITY_TYPES:
            raise ValueError(f"unsupported portable entity type: {self.entity_type}")
        if self.activation not in {"disabled", "review_required"}:
            raise ValueError("an imported entity cannot be active")
        if self.authority_required not in AUTHORITIES:
            raise ValueError("authority_required is outside the closed vocabulary")
        if not all((self.entity_id.strip(), self.name.strip(), self.summary.strip(), self.body.strip())):
            raise ValueError("portable entity identity and meaning must be non-empty")
        required_source = {"producer", "source_revision", "member", "sha256"}
        if not required_source.issubset(self.source_ref):
            raise ValueError("portable entity source_ref is incomplete")


@dataclass(frozen=True)
class Outcome:
    member: str
    disposition: str
    entity: PortableEntity | None = None
    losses: tuple[Loss, ...] = ()
    refusal: ImportRefusal | None = None

    def __post_init__(self) -> None:
        if self.disposition not in DISPOSITIONS:
            raise ValueError(f"unknown disposition: {self.disposition}")
        if self.disposition in {"imported", "review_required"} and self.entity is None:
            raise ValueError("portable outcomes require an entity")
        if self.disposition == "refused" and self.refusal is None:
            raise ValueError("refused outcomes require a refusal")
        if self.disposition == "imported" and self.entity and self.entity.activation != "disabled":
            raise ValueError("an imported outcome must remain disabled")
        if (
            self.disposition == "review_required"
            and self.entity
            and self.entity.activation != "review_required"
        ):
            raise ValueError("a review-required outcome must remain review-required")

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "member": self.member,
            "disposition": self.disposition,
            "losses": [asdict(loss) for loss in self.losses],
        }
        if self.entity is not None:
            result["entity"] = asdict(self.entity)
        if self.refusal is not None:
            result["refusal"] = self.refusal.as_dict()
        return result


@dataclass(frozen=True)
class Coverage:
    denominator: int
    counts: dict[str, int]
    control_fired: bool
    predicate: str

    @classmethod
    def from_outcomes(cls, outcomes: list[Outcome], *, control_fired: bool) -> "Coverage":
        members = [outcome.member for outcome in outcomes]
        if len(set(members)) != len(members):
            raise ImportRefusal(
                "COUNT-MISMATCH",
                "coverage",
                "one member appears in more than one outcome",
                "emit exactly one outcome per stable member identity",
            )
        counts = Counter(outcome.disposition for outcome in outcomes)
        normalized = {name: counts.get(name, 0) for name in DISPOSITIONS}
        denominator = len(outcomes)
        if sum(normalized.values()) != denominator:
            raise ImportRefusal(
                "COUNT-MISMATCH",
                "coverage",
                "the four outcome counts do not equal the member denominator",
                "emit exactly one outcome for every declared member",
            )
        if not control_fired:
            raise ImportRefusal(
                "COUNT-MISMATCH",
                "coverage-control",
                "the parser did not observe its positive control",
                "run against an export containing the declared control member",
            )
        return cls(
            denominator=denominator,
            counts=normalized,
            control_fired=control_fired,
            predicate="exactly one imported, review_required, dropped, or refused outcome per declared member",
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def require_declared_losses(
    *, member: str, omitted_fields: set[str], losses: tuple[Loss, ...]
) -> None:
    """Refuse any source field the adapter omitted without naming the loss."""

    declared = {loss.field for loss in losses}
    undeclared = sorted(omitted_fields - declared)
    if undeclared:
        raise ImportRefusal(
            "UNDECLARED-LOSS",
            member,
            f"source fields would be omitted without loss records: {undeclared}",
            "retain each field or emit one explicit loss record for it",
        )
