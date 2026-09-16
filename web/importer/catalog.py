"""Parser for the frozen runtime plus brain architecture catalog."""

from __future__ import annotations

import csv
import hashlib
import io
import json
from collections import Counter
from dataclasses import dataclass
from typing import Any

from .errors import ImportRefusal
from .frontmatter import decode_utf8
from .model import Coverage, Loss, Outcome, PortableEntity


CATALOG_MAPPING = {
    "brain_command": "Command",
    "cli_command": "Command",
    "agent": "Agent",
    "brain_skill": "Skill",
    "brain_rule": "Rule",
    "attention_rule": "Rule",
    "brain_workflow": "Workflow",
    "workflow": "Workflow",
    "routine": "Workflow",
    "mcp_tool": "Tool",
    "doctrine_doc": "Knowledge",
    "knowledge_graph": "Knowledge",
    "memory": "Memory",
}


@dataclass(frozen=True)
class CatalogResult:
    outcomes: list[Outcome]
    coverage: Coverage
    runtime_component_types: int
    runtime_members: int
    brain_graphs: int
    source_digests: dict[str, str]
    member_predicate: str


def _read_tsv(data: bytes, member: str, required: set[str]) -> list[dict[str, str]]:
    text = decode_utf8(data, member)
    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    if reader.fieldnames is None or not required.issubset(reader.fieldnames):
        raise ImportRefusal(
            "EXPORT-INCOMPLETE",
            member,
            f"catalog columns do not include {sorted(required)}",
            "export the complete architecture catalog at one revision",
        )
    return [dict(row) for row in reader]


def _drop(member: str, component_type: str, detail: str) -> Outcome:
    loss = Loss(
        code="RUNTIME-IMPLEMENTATION-DETAIL",
        field="component_type_id",
        disposition="dropped",
        why=(
            f"{component_type} is runtime implementation or history, not one of the eleven "
            "portable durable entity types"
        ),
        source_value_present=bool(detail),
    )
    return Outcome(member=member, disposition="dropped", losses=(loss,))


def _portable(
    *,
    member: str,
    component_type: str,
    detail: str,
    definition: dict[str, str],
    source_revision: str,
    source_digest: str,
    catalog_identity: str,
) -> Outcome:
    entity_type = CATALOG_MAPPING[component_type]
    loss = Loss(
        code="CATALOG-SUMMARY-ONLY",
        field="body",
        disposition="review_required",
        why=(
            "the catalog preserves identity and derived detail, not the complete source definition; "
            "activation needs the corresponding direct export"
        ),
        source_value_present=bool(detail),
    )
    entity = PortableEntity(
        entity_id=f"architecture:{catalog_identity}",
        entity_type=entity_type,
        name=member,
        summary=definition.get("what_it_is") or definition.get("display_name") or member,
        body=detail or definition.get("what_it_is") or member,
        source_ref={
            "producer": "architecture-catalog/1.0",
            "source_revision": source_revision,
            "member": catalog_identity,
            "sha256": source_digest,
        },
        activation="review_required",
        authority_required="unknown",
        extensions={
            "runtime_component_type": component_type,
            "catalog_detail": detail,
            "catalog_definition": definition,
        },
        losses=(loss,),
    )
    return Outcome(
        member=catalog_identity,
        disposition="review_required",
        entity=entity,
        losses=(loss,),
    )


def parse_catalog(
    *,
    components_data: bytes,
    members_data: bytes,
    graphs_data: bytes,
    source_revision: str,
) -> CatalogResult:
    components = _read_tsv(
        components_data,
        "docs/architecture/components.tsv",
        {"component_type_id", "member_count", "display_name", "what_it_is"},
    )
    members = _read_tsv(
        members_data,
        "docs/architecture/members.tsv",
        {"component_type_id", "member", "detail"},
    )
    try:
        graph_payload: Any = json.loads(decode_utf8(graphs_data, "docs/wiki/graphs.json"))
    except json.JSONDecodeError as exc:
        raise ImportRefusal(
            "MALFORMED-JSON",
            "docs/wiki/graphs.json",
            f"graph catalog is not valid JSON at line {exc.lineno}",
            "re-export the graph catalog from its source revision",
        ) from exc
    except (ValueError, RecursionError) as exc:
        raise ImportRefusal(
            "MALFORMED-JSON",
            "docs/wiki/graphs.json",
            f"graph catalog cannot be parsed safely: {exc}",
            "re-export bounded valid JSON from the frozen source revision",
        ) from exc
    if not isinstance(graph_payload, dict) or not isinstance(graph_payload.get("graphs"), list):
        raise ImportRefusal(
            "EXPORT-INCOMPLETE",
            "docs/wiki/graphs.json",
            "graph catalog does not contain a graphs array",
            "export graphs.json with its declared graph inventory",
        )

    definitions: dict[str, dict[str, str]] = {}
    for row in components:
        component_type = row["component_type_id"].strip()
        if not component_type or component_type in definitions:
            raise ImportRefusal(
                "DUPLICATE-ID-CONFLICT",
                component_type or "components.tsv",
                "component type identity is empty or duplicated",
                "emit each non-empty component type exactly once",
            )
        definitions[component_type] = row

    observed = Counter(row["component_type_id"].strip() for row in members)
    undeclared = sorted(set(observed) - set(definitions))
    if undeclared:
        raise ImportRefusal(
            "UNKNOWN-ENTITY-TYPE",
            "docs/architecture/members.tsv",
            f"members name component types absent from components.tsv: {undeclared}",
            "regenerate both catalog files from the same source revision",
        )
    for component_type, definition in definitions.items():
        try:
            declared = int(definition["member_count"])
        except ValueError as exc:
            raise ImportRefusal(
                "COUNT-MISMATCH",
                component_type,
                "member_count is not an integer",
                "regenerate components.tsv with a numeric denominator",
            ) from exc
        if observed[component_type] != declared:
            raise ImportRefusal(
                "COUNT-MISMATCH",
                component_type,
                f"components.tsv declares {declared} members but members.tsv contains {observed[component_type]}",
                "regenerate both files together from one frozen revision",
            )

    graphs = graph_payload["graphs"]
    declared_graphs = graph_payload.get("denominator_graphs")
    if declared_graphs != len(graphs):
        raise ImportRefusal(
            "COUNT-MISMATCH",
            "docs/wiki/graphs.json",
            f"denominator_graphs is {declared_graphs!r} but the graphs array contains {len(graphs)}",
            "regenerate graphs.json with its denominator",
        )

    digests = {
        "docs/architecture/components.tsv": hashlib.sha256(components_data).hexdigest(),
        "docs/architecture/members.tsv": hashlib.sha256(members_data).hexdigest(),
        "docs/wiki/graphs.json": hashlib.sha256(graphs_data).hexdigest(),
    }
    outcomes: list[Outcome] = []
    for row_number, row in enumerate(members, start=2):
        component_type = row["component_type_id"].strip()
        member = row["member"].strip()
        catalog_identity = f"{component_type}:{member}@members.tsv:{row_number}"
        if not member:
            refusal = ImportRefusal(
                "MISSING-REQUIRED-FIELD",
                catalog_identity,
                "catalog member name is empty",
                "emit a non-empty member name; its frozen row is the catalog identity",
            )
            outcomes.append(
                Outcome(member=catalog_identity, disposition="refused", refusal=refusal)
            )
            continue
        if component_type in CATALOG_MAPPING:
            outcomes.append(
                _portable(
                    member=member,
                    component_type=component_type,
                    detail=row["detail"],
                    definition=definitions[component_type],
                    source_revision=source_revision,
                    source_digest=digests["docs/architecture/members.tsv"],
                    catalog_identity=catalog_identity,
                )
            )
        else:
            outcomes.append(_drop(catalog_identity, component_type, row["detail"]))

    seen_graph_slugs: set[str] = set()
    for graph in graphs:
        if not isinstance(graph, dict) or not str(graph.get("slug") or "").strip():
            refusal = ImportRefusal(
                "MISSING-REQUIRED-FIELD",
                "knowledge_graph",
                "graph entry has no stable slug",
                "export every graph with a stable slug",
            )
            outcomes.append(Outcome(member="knowledge_graph:<missing>", disposition="refused", refusal=refusal))
            continue
        slug = str(graph["slug"])
        if slug in seen_graph_slugs:
            refusal = ImportRefusal(
                "DUPLICATE-ID-CONFLICT",
                f"knowledge_graph:{slug}",
                "graph slug is duplicated in the frozen catalog",
                "emit one graph inventory entry per stable slug",
            )
            outcomes.append(
                Outcome(member=f"knowledge_graph:{slug}", disposition="refused", refusal=refusal)
            )
            continue
        seen_graph_slugs.add(slug)
        detail = json.dumps(graph, sort_keys=True, separators=(",", ":"))
        outcomes.append(
            _portable(
                member=slug,
                component_type="knowledge_graph",
                detail=detail,
                definition={
                    "display_name": "Knowledge graph",
                    "what_it_is": "A namespace-level derived map of knowledge nodes and their links.",
                },
                source_revision=source_revision,
                source_digest=digests["docs/wiki/graphs.json"],
                catalog_identity=f"knowledge_graph:{slug}",
            )
        )

    control_fired = observed.get("table", 0) > 0 and len(graphs) > 0
    coverage = Coverage.from_outcomes(outcomes, control_fired=control_fired)
    expected_denominator = len(members) + len(graphs)
    if coverage.denominator != expected_denominator:
        raise ImportRefusal(
            "COUNT-MISMATCH",
            "architecture-catalog/1.0",
            f"expected {expected_denominator} outcomes but emitted {coverage.denominator}",
            "emit one outcome for every runtime member and brain graph",
        )
    return CatalogResult(
        outcomes=outcomes,
        coverage=coverage,
        runtime_component_types=len(components),
        runtime_members=len(members),
        brain_graphs=len(graphs),
        source_digests=digests,
        member_predicate=(
            "every data row in docs/architecture/members.tsv plus every entry in the "
            "docs/wiki/graphs.json graphs array"
        ),
    )
