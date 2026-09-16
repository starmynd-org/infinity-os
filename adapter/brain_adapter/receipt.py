"""`receipt book` and `touch add`: the two verbs that produce a promotion.

Both build a document and hand it to `promotion.promote()`. Neither calls git. That is
the narrow waist: one transition, one implementation, one audit point.

Where a receipt lives and what it carries is not decided here:

- `entities/rules/result-and-escalation-contract.md`: "Every consequential action writes
  an append-only receipt to `departments/<slug>/receipts/`", and it is the shape of record
  for `caused_by_event_id` and `approval_ref`.
- `_system/wager-ledger-rules.md` WAGER-14a: the four moments a receipt is booked --
  disposition-created, wager-registered, result-produced, and consequential-event-emitted
  (any event carrying `external: true` or `canon_touching: true`).
- WAGER-7: git holds the per-item receipts and the component touch-edges.
- WAGER-13a: receipts are append-only. A status change is a new receipt, never an
  in-place edit to an existing one.

The frontmatter shape matches the receipts already in the brain (for example
`departments/devops-platform/receipts/2026-07-20-cortana-conversational-lane-v1.md`) so
`_system/validate.sh` accepts it without a new exemption.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .promotion import Lineage, Promotion, PromotionRefused, PromotionResult, promote

# WAGER-14a. A receipt booked at any other moment is a judgement nobody asked for.
BOOKING_MOMENTS = {
    "disposition-created",
    "wager-registered",
    "result-produced",
    "consequential-event-emitted",
}

# WAGER-2c enums. Validated rather than accepted as free text.
ORIENT_ROLES = {"tradition", "previous-experience", "analysis-synthesis", "substrate"}
TOUCH_ROLES = {"load-bearing", "incidental"}
COMPONENT_TYPES = {"agent", "skill", "rule", "playbook", "workflow", "command",
                   "knowledge", "tool", "namespace", "department"}

_SLUG_OK = re.compile(r"^[a-z0-9][a-z0-9-]*$")


@dataclass
class Touch:
    """One component-touch edge, WAGER-2c shape.

    `D00` glosses this as `(subject_type, subject_id, entity_id, role)`. That drops
    `orient_role`, which is the field the wager ledger scores by. This carries the
    superset; the 4-tuple projects out of it, and the reverse does not.
    """

    entity_id: str
    component_type: str
    orient_role: str
    role: str = "load-bearing"
    entity_ref: str | None = None
    resolution_status: str = "resolved"

    def validate(self) -> None:
        if self.orient_role not in ORIENT_ROLES:
            raise PromotionRefused("BAD_ORIENT_ROLE",
                                   f"{self.orient_role!r} not in {sorted(ORIENT_ROLES)}")
        if self.role not in TOUCH_ROLES:
            raise PromotionRefused("BAD_TOUCH_ROLE",
                                   f"{self.role!r} not in {sorted(TOUCH_ROLES)}")
        if self.component_type not in COMPONENT_TYPES:
            raise PromotionRefused("BAD_COMPONENT_TYPE",
                                   f"{self.component_type!r} not in {sorted(COMPONENT_TYPES)}")
        if self.entity_id is None and self.resolution_status == "resolved":
            raise PromotionRefused("TOUCH_INCOHERENT",
                                   "null entity_id with resolution_status 'resolved'")

    def to_yaml_row(self, subject_type: str, subject_id: str) -> str:
        eid = f'"{self.entity_id}"' if self.entity_id else "null"
        parts = [
            f"  - subject_type: \"{subject_type}\"",
            f"    subject_id: \"{subject_id}\"",
            f"    entity_id: {eid}",
            f"    component_type: \"{self.component_type}\"",
            f"    orient_role: \"{self.orient_role}\"",
            f"    role: \"{self.role}\"",
        ]
        if self.entity_id is None:
            parts.append(f"    entity_ref: \"{self.entity_ref}\"")
            parts.append(f"    resolution_status: \"{self.resolution_status}\"")
        return "\n".join(parts)


@dataclass
class Receipt:
    department: str
    date: str            # YYYY-MM-DD, supplied by the caller, never invented here
    slug: str
    title: str
    moment: str
    summary: str
    lineage: Lineage
    what_happened: str = ""
    artifacts: list[str] = field(default_factory=list)
    touches: list[Touch] = field(default_factory=list)
    external: bool = False
    canon_touching: bool = False
    namespace: str = "ai-architecture"
    promotion_kind: str = "closeout-to-planning-truth"

    # What the touch edges are ABOUT, which is not always this receipt.
    #
    # These were missing, and their absence was a defect the store join surfaced rather than a
    # gap: `render()` hardcoded ("receipt", own-id) as every edge's subject, so `touch add
    # --subject-type disposition --subject-id d-42` wrote 'd-42' into the prose and the
    # receipt's OWN id into the machine-readable block. The edges pointed at the record of the
    # edges. WAGER-2c's subject is `disposition_id` OR `wager_id`; neither arm could be
    # expressed, and a `brain.touch` projection of such a receipt is self-referential and
    # cannot be scored.
    #
    # Unset keeps the old behaviour exactly -- subject_type 'receipt', subject_id this
    # receipt's id -- so every receipt already committed still renders identically.
    subject_type: str | None = None
    subject_id: str | None = None

    def subject(self) -> tuple[str, str]:
        return (self.subject_type or "receipt", self.subject_id or self.entity_id())

    # ---- validation ------------------------------------------------------------

    def validate(self, repo: Path) -> None:
        if self.moment not in BOOKING_MOMENTS:
            raise PromotionRefused(
                "BAD_BOOKING_MOMENT",
                f"{self.moment!r} is not one of WAGER-14a's four moments: "
                f"{sorted(BOOKING_MOMENTS)}",
            )
        if not _SLUG_OK.match(self.department):
            raise PromotionRefused("BAD_DEPARTMENT", f"{self.department!r} is not a slug")
        if not _SLUG_OK.match(self.slug):
            raise PromotionRefused("BAD_SLUG", f"{self.slug!r} is not a slug")
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", self.date):
            raise PromotionRefused("BAD_DATE", f"{self.date!r} is not YYYY-MM-DD")
        home = repo / "departments" / self.department / "receipts"
        if not home.is_dir():
            raise PromotionRefused(
                "NO_RECEIPT_HOME",
                f"{home.relative_to(repo)} does not exist. A department's receipt home is "
                f"declared by that department, not created by the runtime.",
            )
        if self.path(repo).exists():
            raise PromotionRefused(
                "RECEIPT_EXISTS",
                f"{self.path(repo).relative_to(repo)} already exists. Receipts are "
                f"append-only (WAGER-13a): book a new one, never edit this.",
            )
        # WAGER-14a's fourth moment is defined by the hard flags; refuse the mismatch.
        if self.moment == "consequential-event-emitted" and not (self.external or self.canon_touching):
            raise PromotionRefused(
                "NOT_CONSEQUENTIAL",
                "the consequential-event-emitted moment requires external or "
                "canon_touching to be true; neither is set.",
            )
        for t in self.touches:
            t.validate()

    # ---- rendering -------------------------------------------------------------

    def entity_id(self) -> str:
        return f"receipt-{self.department}-{self.date}-{self.slug}"

    def path(self, repo: Path) -> Path:
        return repo / "departments" / self.department / "receipts" / f"{self.date}-{self.slug}.md"

    def render(self) -> str:
        eid = self.entity_id()
        lin = self.lineage
        head = [
            "---",
            f'id: "{eid}"',
            f'aliases: ["{eid}"]',
            'type: "Doc"',
            f'namespace: "{self.namespace}"',
            'lifecycle_state: "research"',
            f'summary: "{_yaml_scalar(self.summary)}"',
            "confidence: 0.9",
            'retrieval_class: "ephemeral"',
            'export_class: "internal"',
            f'created: "{self.date}"',
            "departments:",
            f'  - "{self.department}"',
            "---",
            "",
            f"# Receipt: {self.title} ({self.date})",
            "",
            f"Append-only receipt booked at the WAGER-14a moment `{self.moment}`, per "
            f"`_system/wager-ledger-rules.md` and the shape of record in "
            f"`entities/rules/result-and-escalation-contract.md`.",
            "",
            "## Lineage",
            "",
            "| Field | Value |",
            "|---|---|",
            f"| `produced_by` | {_cell(lin.produced_by)} |",
            f"| `produced_by_ref` | {_cell(lin.produced_by_ref)} |",
            f"| `resolution_status` | {_cell(lin.resolution_status)} |",
            f"| `caused_by_event_id` | {_cell(lin.caused_by_event_id)} |",
            f"| `approval_ref` | {_cell(lin.approval_ref)} |",
            f"| `actor_type` | {_cell(lin.actor_type)} |",
            f"| `work_item_id` | {_cell(lin.work_item_id)} |",
            f"| `canonical_task` | {_cell(lin.canonical_task)} |",
            f"| `session_id` | {_cell(lin.session_id)} |",
            f"| `external` | `{str(self.external).lower()}` |",
            f"| `canon_touching` | `{str(self.canon_touching).lower()}` |",
            "",
        ]
        if lin.produced_by is None:
            head += [
                f"**`produced_by` is null because the producing entity did not resolve** "
                f"(`resolution_status: {lin.resolution_status}`). The raw reference is "
                f"preserved in `produced_by_ref` above. A fabricated id would be believed "
                f"later, which is worse than a null.",
                "",
            ]
        if lin.caused_by_event_id and not lin.approval_ref and (self.external or self.canon_touching):
            head += [
                "**Flagged event with no approval record.** This combination has no benign "
                "reading and is detectable by join, per the shape of record. It is stated "
                "here rather than left to narrative.",
                "",
            ]

        head += ["## What happened", "", (self.what_happened or self.summary).rstrip(), ""]

        if self.artifacts:
            head += ["## Artifacts", ""]
            head += [f"- `{a}`" for a in self.artifacts]
            head.append("")

        head += [
            "## Touches",
            "",
            "Component-touch edges in the `_system/wager-ledger-rules.md` WAGER-2c shape. "
            "Git holds the touch-edge (WAGER-7); a runtime table over these is a "
            "projection, never the source. No score lives here (WAGER-8).",
            "",
        ]
        if self.touches:
            subject_type, subject_id = self.subject()
            head += ["```yaml", "touches:"]
            for t in self.touches:
                head.append(t.to_yaml_row(subject_type, subject_id))
            head += ["```", ""]
        else:
            head += ["_No component touches recorded for this action._", ""]

        head += [
            "## Boundary",
            "",
            "This receipt is a promotion event, the only legal bridge from the runtime "
            "plane to the truth plane per "
            "`knowledge/ai-architecture/concepts/surface-boundary.md`. It records who "
            "decided; it does not decide. No agent self-approves canon.",
            "",
        ]
        return "\n".join(head)


def book(repo: Path, receipt: Receipt, expect_branch: str | None = None,
         extra_paths: list[Path] | None = None) -> tuple[PromotionResult, Path]:
    """The `receipt book` verb. Validates, writes the file, promotes through the one door."""
    repo = Path(repo).resolve()
    receipt.validate(repo)
    target = receipt.path(repo)
    target.write_text(receipt.render(), encoding="utf-8")

    paths = [target] + [Path(p) for p in (extra_paths or [])]
    promotion = Promotion(
        kind=receipt.promotion_kind,
        paths=paths,
        message=f"receipt({receipt.department}): {receipt.title}",
        lineage=receipt.lineage,
        canon_touching=receipt.canon_touching,
        external=receipt.external,
    )
    try:
        result = promote(repo, promotion, expect_branch=expect_branch)
    except PromotionRefused:
        # A refused promotion leaves no half-written file behind. The refusal is the
        # outcome, not a partially-booked receipt somebody finds later.
        if target.exists():
            target.unlink()
        raise
    return result, target


def _cell(v) -> str:
    return f"`{v}`" if v not in (None, "") else "`null`"


def _yaml_scalar(s: str) -> str:
    return (s or "").replace('"', "'").replace("\n", " ").strip()
