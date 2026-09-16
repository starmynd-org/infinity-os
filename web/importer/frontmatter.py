"""Strict, dependency-free parsing of the top level of Markdown frontmatter."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from .errors import ImportRefusal
from .model import Loss, Outcome, PortableEntity


_TOP_LEVEL = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):(?:[ \t]*(.*))?$")
_ANY_KEY = re.compile(
    r"^[ \t-]*(?P<quote>[\"']?)(?P<key>[A-Za-z_][A-Za-z0-9_-]*)(?P=quote):"
    r"(?:[ \t]*(?P<value>.*))?$"
)
_CREDENTIAL_KEY = re.compile(
    r"(?:^|[_-])(password|secret|token|api[_-]?key|apikey|credentials?)(?:$|[_-])",
    re.IGNORECASE,
)
_SIMPLE_FLOW_PAIR = re.compile(
    r"\s*(?P<quote>[\"']?)(?P<key>[A-Za-z_][A-Za-z0-9_-]*)(?P=quote)\s*:\s*"
    r"(?P<value>[^,{}\[\]]+?)\s*(?P<separator>,|$)"
)


@dataclass(frozen=True)
class MarkdownDocument:
    fields: dict[str, Any]
    raw_frontmatter: str
    body: str


def _reject_duplicate_object_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for key, value in pairs:
        if key in fields:
            raise ValueError(f"duplicate object key {key!r}")
        fields[key] = value
    return fields


def _simple_flow_mapping(value: str) -> dict[str, Any] | None:
    if not (value.startswith("{") and value.endswith("}")):
        return None
    inner = value[1:-1]
    if not inner.strip():
        return {}
    fields: dict[str, Any] = {}
    position = 0
    while position < len(inner):
        match = _SIMPLE_FLOW_PAIR.match(inner, position)
        if match is None:
            return None
        key = match.group("key")
        raw_value = match.group("value").strip()
        if key in fields or not raw_value:
            return None
        parsed = _scalar(raw_value)
        if isinstance(parsed, (dict, list)):
            return None
        fields[key] = parsed
        position = match.end()
    return fields


def decode_utf8(data: bytes, member: str) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ImportRefusal(
            "NOT-UTF8",
            member,
            f"the member is not valid UTF-8 at byte {exc.start}",
            "export the source as UTF-8 without replacement characters",
        ) from exc


def _scalar(value: str) -> Any:
    value = value.strip()
    if not value:
        return None
    if value in {"true", "false", "null"}:
        return {"true": True, "false": False, "null": None}[value]
    if value.startswith('"') and value.endswith('"'):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value[1:-1]
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1].replace("''", "'")
    if (value.startswith("[") and value.endswith("]")) or (
        value.startswith("{") and value.endswith("}")
    ):
        try:
            return json.loads(
                value.replace("'", '"'), object_pairs_hook=_reject_duplicate_object_keys
            )
        except json.JSONDecodeError:
            simple_mapping = _simple_flow_mapping(value)
            if simple_mapping is not None:
                return simple_mapping
            return value
    if re.fullmatch(r"-?[0-9]+", value):
        return int(value)
    if re.fullmatch(r"-?[0-9]+\.[0-9]+", value):
        return float(value)
    return value


def _has_credential_value(frontmatter: str) -> str | None:
    lines = frontmatter.splitlines()
    for index, line in enumerate(lines):
        match = _ANY_KEY.match(line)
        if match is None:
            continue
        key = match.group("key")
        value = match.group("value")
        value = (value or "").strip()
        if value.startswith(("[", "{")):
            try:
                inline_value = _scalar(value)
            except (ValueError, RecursionError):
                inline_value = value
            inline_credential = _parsed_credential_path(inline_value, (key,))
            if inline_credential is not None:
                return inline_credential
        lowered = key.lower()
        if lowered.endswith("_ref") or lowered.endswith("_refs"):
            continue
        if not _CREDENTIAL_KEY.search(key):
            continue
        if value and value not in {"null", "[]", "{}"}:
            return key
        parent_indent = len(line) - len(line.lstrip(" \t-"))
        for child_line in lines[index + 1 :]:
            if not child_line.strip() or child_line.lstrip().startswith("#"):
                continue
            child_indent = len(child_line) - len(child_line.lstrip(" \t-"))
            if child_indent <= parent_indent:
                break
            child_match = _ANY_KEY.match(child_line)
            if child_match is None:
                if child_line.strip() not in {"-", "null", "[]", "{}"}:
                    return key
                continue
            child_value = (child_match.group("value") or "").strip()
            if child_value and child_value not in {"null", "[]", "{}"}:
                return key
    return None


def _unvalidated_inline_collection(frontmatter: str) -> str | None:
    for line in frontmatter.splitlines():
        if not line.startswith((" ", "\t", "-")):
            continue
        match = _ANY_KEY.match(line)
        if match is None:
            continue
        key = match.group("key")
        value = (match.group("value") or "").strip()
        if not value.startswith(("[", "{")):
            continue
        try:
            parsed = _scalar(value)
        except (ValueError, RecursionError):
            return key
        if isinstance(parsed, str):
            return key
    return None


def _parsed_credential_path(value: Any, path: tuple[str, ...] = ()) -> str | None:
    if isinstance(value, dict):
        for raw_key, child in value.items():
            key = str(raw_key)
            child_path = (*path, key)
            lowered = key.lower()
            is_reference = lowered.endswith("_ref") or lowered.endswith("_refs")
            if (
                _CREDENTIAL_KEY.search(key)
                and not is_reference
                and child not in (None, "", [], {})
            ):
                return ".".join(child_path)
            found = _parsed_credential_path(child, child_path)
            if found is not None:
                return found
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found = _parsed_credential_path(child, (*path, str(index)))
            if found is not None:
                return found
    return None


def parse_markdown(data: bytes, member: str) -> MarkdownDocument:
    text = decode_utf8(data, member)
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ImportRefusal(
            "MALFORMED-FRONTMATTER",
            member,
            "the file does not begin with a YAML frontmatter fence",
            "place a complete frontmatter block before the Markdown body",
        )
    closing = next((index for index in range(1, len(lines)) if lines[index].strip() == "---"), None)
    if closing is None:
        raise ImportRefusal(
            "MALFORMED-FRONTMATTER",
            member,
            "the opening frontmatter fence has no closing fence",
            "close the frontmatter block before the Markdown body",
        )
    raw = "\n".join(lines[1:closing])
    unvalidated_inline = _unvalidated_inline_collection(raw)
    if unvalidated_inline is not None:
        raise ImportRefusal(
            "MALFORMED-FRONTMATTER",
            member,
            f"frontmatter field {unvalidated_inline!r} uses unsupported inline collection syntax",
            "use JSON-shaped inline syntax or a simple {identifier: scalar} mapping",
        )
    credential_key = _has_credential_value(raw)
    if credential_key is not None:
        raise ImportRefusal(
            "CREDENTIAL-IN-EXPORT",
            member,
            f"credential-bearing field {credential_key!r} carries a value",
            "replace the value with a stable secret reference id",
        )
    fields: dict[str, Any] = {}
    for line in lines[1:closing]:
        match = _TOP_LEVEL.match(line)
        if match is None:
            continue
        key, value = match.groups()
        if key in fields:
            raise ImportRefusal(
                "MALFORMED-FRONTMATTER",
                member,
                f"top-level frontmatter key {key!r} appears more than once",
                "emit every top-level field exactly once",
            )
        raw_value = (value or "").strip()
        try:
            parsed = _scalar(raw_value)
        except (ValueError, RecursionError) as exc:
            raise ImportRefusal(
                "MALFORMED-FRONTMATTER",
                member,
                f"top-level frontmatter value for {key!r} cannot be parsed safely: {exc}",
                "export a bounded UTF-8 scalar or inline collection",
            ) from exc
        if raw_value.startswith(("[", "{")) and isinstance(parsed, str):
            raise ImportRefusal(
                "MALFORMED-FRONTMATTER",
                member,
                f"top-level frontmatter value for {key!r} has malformed scalar syntax",
                "export a valid quoted scalar or JSON-shaped inline collection",
            )
        fields[key] = parsed
    parsed_credential = _parsed_credential_path(fields)
    if parsed_credential is not None:
        raise ImportRefusal(
            "CREDENTIAL-IN-EXPORT",
            member,
            f"credential-bearing field {parsed_credential!r} carries a value",
            "replace the value with a stable secret reference id",
        )
    body = "\n".join(lines[closing + 1 :]).strip()
    return MarkdownDocument(fields=fields, raw_frontmatter=raw, body=body)


_SEMANTIC_FIELDS = {
    "Command": ("description",),
    "Agent": ("name", "description"),
    "Skill": ("description",),
    "Rule": (),
    "Workflow": (),
    "Tool": (),
    "Knowledge": ("namespace",),
    "Data": (),
    "Memory": (),
    "Output": (),
    "Project": (),
}


def markdown_outcome(
    *,
    data: bytes,
    member: str,
    producer: str,
    source_revision: str,
) -> Outcome:
    document = parse_markdown(data, member)
    entity_type = document.fields.get("type")
    if not isinstance(entity_type, str) or entity_type not in _SEMANTIC_FIELDS:
        raise ImportRefusal(
            "UNKNOWN-ENTITY-TYPE",
            member,
            f"frontmatter type {entity_type!r} is not one of the eleven portable types",
            "map the source type explicitly or exclude it from the export manifest",
        )
    entity_id = str(document.fields.get("id") or "").strip()
    if not entity_id:
        raise ImportRefusal(
            "MISSING-REQUIRED-FIELD",
            member,
            f"{entity_type} is missing stable frontmatter id",
            "add a stable id before exporting the member",
        )
    if not document.body:
        raise ImportRefusal(
            "MISSING-REQUIRED-FIELD",
            member,
            f"{entity_type} has an empty body",
            "export the definition, not only its metadata",
        )

    losses: list[Loss] = []
    for field_name in _SEMANTIC_FIELDS[entity_type]:
        if not str(document.fields.get(field_name) or "").strip():
            losses.append(
                Loss(
                    code="SOURCE-SEMANTIC-GAP",
                    field=field_name,
                    disposition="review_required",
                    why=f"the {entity_type} source does not declare {field_name}",
                    source_value_present=False,
                )
            )

    known = {
        "id",
        "aliases",
        "type",
        "namespace",
        "lifecycle_state",
        "summary",
        "confidence",
        "retrieval_class",
        "export_class",
        "name",
        "description",
        "tools",
        "edges",
        "created",
        "runtime",
        "verified_at",
        "verified_by",
    }
    extensions = {key: value for key, value in document.fields.items() if key not in known}
    summary = str(document.fields.get("summary") or document.fields.get("description") or "").strip()
    if not summary:
        heading = next(
            (line.lstrip("# ").strip() for line in document.body.splitlines() if line.startswith("#")),
            "",
        )
        summary = heading or f"Imported {entity_type.lower()} {entity_id}"
        losses.append(
            Loss(
                code="SUMMARY-DERIVED",
                field="summary",
                disposition="review_required",
                why="the source carried no summary; the importer derived one from the first heading",
                source_value_present=False,
            )
        )

    name = str(document.fields.get("name") or "").strip()
    if not name:
        name = PurePosixPath(member).stem
    digest = hashlib.sha256(data).hexdigest()
    activation = "review_required" if losses else "disabled"
    disposition = "review_required" if losses else "imported"
    entity = PortableEntity(
        entity_id=entity_id,
        entity_type=entity_type,
        name=name,
        summary=summary,
        body=document.body,
        source_ref={
            "producer": producer,
            "source_revision": source_revision,
            "member": member,
            "sha256": digest,
        },
        activation=activation,
        authority_required="unknown",
        extensions={
            "frontmatter": document.fields,
            "raw_frontmatter": document.raw_frontmatter,
            **extensions,
        },
        losses=tuple(losses),
    )
    return Outcome(
        member=member,
        disposition=disposition,
        entity=entity,
        losses=tuple(losses),
    )
