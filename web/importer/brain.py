"""Direct parser over portable, node-bearing files in a frozen brain commit."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from .errors import ImportRefusal
from .frontmatter import decode_utf8, markdown_outcome
from .git_source import GitTree
from .model import Coverage, Loss, Outcome
from .workflow import parse_workflow_json


ROOTS = (
    "entities",
    "workflows",
    "automations/n8n",
    "tools",
    "knowledge",
    "data",
    "memory",
    "outputs",
    "projects",
)

_TYPE_LINE = re.compile(r'^type:\s*["\']?([A-Za-z]+)["\']?\s*$', re.MULTILINE)
_ELEVEN = {
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
}


@dataclass(frozen=True)
class BrainResult:
    outcomes: list[Outcome]
    coverage: Coverage
    tracked_blobs_examined: int
    entity_types: dict[str, int]
    inventory_sha256: str
    member_predicate: str


def _frontmatter_type(data: bytes, member: str) -> str | None:
    text = decode_utf8(data, member)
    if not text.startswith("---"):
        return None
    closing = text.find("\n---", 3)
    if closing < 0:
        return "<malformed>"
    match = _TYPE_LINE.search(text[3:closing])
    return match.group(1) if match else None


def parse_brain_tree(tree: GitTree) -> BrainResult:
    refs = [
        ref
        for ref in tree.list_blobs(*ROOTS)
        if ref.path.endswith((".md", ".json"))
    ]
    blobs = {path: data for path, data in tree.read_many(refs)}
    node_paths: list[str] = []
    node_types: dict[str, str] = {}
    for path, data in blobs.items():
        if not path.endswith(".md"):
            continue
        kind = _frontmatter_type(data, path)
        if kind == "<malformed>":
            node_paths.append(path)
            node_types[path] = kind
        elif kind in _ELEVEN:
            node_paths.append(path)
            node_types[path] = kind

    deterministic_json = {
        path for path in blobs if path.startswith("automations/n8n/") and path.endswith(".json")
    }
    deterministic_md = {
        path for path in node_paths if path.startswith("automations/n8n/") and path.endswith(".md")
    }
    outcomes: list[Outcome] = []
    entity_ids: dict[str, tuple[str, bytes]] = {}
    for path in sorted(node_paths):
        try:
            if node_types[path] == "<malformed>":
                raise ImportRefusal(
                    "MALFORMED-FRONTMATTER",
                    path,
                    "the opening frontmatter fence has no closing fence",
                    "close the frontmatter block before the Markdown body",
                )
            if path.startswith("automations/n8n/"):
                json_path = path[:-3] + ".json"
                if json_path not in deterministic_json:
                    raise ImportRefusal(
                        "MISSING-PAIR",
                        path,
                        f"deterministic workflow JSON {json_path!r} is absent",
                        "export the companion Markdown and JSON together",
                    )
                parse_workflow_json(blobs[json_path], json_path)
            outcome = markdown_outcome(
                data=blobs[path],
                member=path,
                producer="brain-directory/1.0",
                source_revision=tree.revision,
            )
            if outcome.entity is not None:
                earlier = entity_ids.get(outcome.entity.entity_id)
                if earlier is not None:
                    earlier_path, earlier_bytes = earlier
                    if earlier_bytes == blobs[path]:
                        loss = Loss(
                            code="BYTE-IDENTICAL-ADAPTER",
                            field="entity_id",
                            disposition="dropped",
                            why=f"byte-identical duplicate of canonical member {earlier_path}",
                            source_value_present=True,
                        )
                        outcomes.append(
                            Outcome(member=path, disposition="dropped", losses=(loss,))
                        )
                        continue
                    raise ImportRefusal(
                        "DUPLICATE-ID-CONFLICT",
                        path,
                        f"stable id {outcome.entity.entity_id!r} has non-identical definitions",
                        "keep one canonical node and regenerate byte-identical adapters from it",
                    )
                entity_ids[outcome.entity.entity_id] = (path, blobs[path])
            outcomes.append(outcome)
        except ImportRefusal as refusal:
            outcomes.append(Outcome(member=path, disposition="refused", refusal=refusal))

    for json_path in sorted(deterministic_json):
        md_path = json_path[:-5] + ".md"
        if md_path in deterministic_md:
            loss = Loss(
                code="WORKFLOW-SUPPORTING-FILE",
                field="json",
                disposition="dropped",
                why=f"validated and represented by companion Workflow entity {md_path}",
                source_value_present=True,
            )
            outcomes.append(Outcome(member=json_path, disposition="dropped", losses=(loss,)))
            continue
        refusal = ImportRefusal(
            "MISSING-PAIR",
            json_path,
            f"deterministic workflow Markdown {md_path!r} is absent",
            "export the JSON and companion Markdown together",
        )
        outcomes.append(Outcome(member=json_path, disposition="refused", refusal=refusal))

    by_type: dict[str, int] = {}
    for outcome in outcomes:
        if outcome.entity is not None:
            entity_type = outcome.entity.entity_type
            by_type[entity_type] = by_type.get(entity_type, 0) + 1
    control_fired = by_type.get("Agent", 0) > 0 and by_type.get("Skill", 0) > 0
    coverage = Coverage.from_outcomes(outcomes, control_fired=control_fired)
    return BrainResult(
        outcomes=outcomes,
        coverage=coverage,
        tracked_blobs_examined=len(refs),
        entity_types=dict(sorted(by_type.items())),
        inventory_sha256=hashlib.sha256(
            b"".join(
                ref.path.encode("utf-8") + b"\0" + ref.oid.encode("ascii") + b"\n"
                for ref in sorted(refs, key=lambda item: item.path)
            )
        ).hexdigest(),
        member_predicate=(
            "Markdown blob below the scanned roots whose top-level frontmatter type is one of "
            "the eleven portable types, plus every JSON blob below automations/n8n"
        ),
    )
