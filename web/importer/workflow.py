"""Validation for deterministic workflow exports."""

from __future__ import annotations

import json
import re
from typing import Any

from .errors import ImportRefusal
from .frontmatter import decode_utf8


_SECRET_KEY = re.compile(
    r"(?:^|[_-])(password|secret|token|api[_-]?key|apikey|credentials?)(?:$|[_-])",
    re.IGNORECASE,
)


def _stable_reference(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and bool(value)
        and set(value).issubset({"id", "name"})
        and isinstance(value.get("id"), str)
        and bool(value["id"].strip())
        and ("name" not in value or isinstance(value["name"], str))
    )


def _credential_reference_shape(key: str, value: Any) -> bool:
    lowered = key.lower()
    if lowered.endswith("_ref") or lowered.endswith("_refs"):
        return True
    if lowered == "credential":
        return _stable_reference(value)
    if lowered == "credentials" and isinstance(value, dict) and bool(value):
        return all(_stable_reference(reference) for reference in value.values())
    return False


def _credential_path(value: Any, path: tuple[str, ...] = ()) -> str | None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = (*path, str(key))
            if (
                _SECRET_KEY.search(str(key))
                and not _credential_reference_shape(str(key), child)
                and child not in (None, "", [], {})
            ):
                return ".".join(child_path)
            found = _credential_path(child, child_path)
            if found is not None:
                return found
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found = _credential_path(child, (*path, str(index)))
            if found is not None:
                return found
    return None


def parse_workflow_json(data: bytes, member: str) -> dict[str, Any]:
    try:
        payload = json.loads(decode_utf8(data, member))
    except json.JSONDecodeError as exc:
        raise ImportRefusal(
            "MALFORMED-JSON",
            member,
            f"workflow JSON is invalid at line {exc.lineno}, column {exc.colno}",
            "export valid JSON from the deterministic workflow runtime",
        ) from exc
    except (ValueError, RecursionError) as exc:
        raise ImportRefusal(
            "MALFORMED-JSON",
            member,
            f"workflow JSON cannot be parsed safely: {exc}",
            "export bounded valid JSON from the deterministic workflow runtime",
        ) from exc
    if not isinstance(payload, dict):
        raise ImportRefusal(
            "MALFORMED-JSON",
            member,
            "workflow export is not a JSON object",
            "export one workflow object with nodes and connections",
        )
    nodes = payload.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise ImportRefusal(
            "EMPTY-WORKFLOW",
            member,
            "workflow nodes are absent or empty",
            "export the real workflow after at least one node exists",
        )
    if not isinstance(payload.get("connections"), dict):
        raise ImportRefusal(
            "MALFORMED-JSON",
            member,
            "workflow connections are absent or not an object",
            "export a workflow object containing its connections map",
        )
    credential_path = _credential_path(payload)
    if credential_path is not None:
        raise ImportRefusal(
            "CREDENTIAL-IN-EXPORT",
            member,
            f"credential-bearing JSON field {credential_path!r} carries a value",
            "replace credential values with stable credential reference metadata",
        )
    return payload
