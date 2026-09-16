"""Filesystem export envelope, digest, path, and completeness enforcement."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from .errors import ImportRefusal
from .frontmatter import markdown_outcome
from .model import Coverage, Loss, Outcome
from .workflow import parse_workflow_json


SUPPORTED_PRODUCERS = {
    "brain-directory/1.0",
    "claude-code-directory/1.0",
    "codex-directory/1.0",
}


@dataclass(frozen=True)
class ExportMember:
    path: str
    sha256: str


@dataclass(frozen=True)
class ProducerControl:
    name: str
    fired: bool
    evidence: str


@dataclass(frozen=True)
class ExportEnvelope:
    producer: str
    source_root_id: str
    generated_at: str
    complete: bool
    members: tuple[ExportMember, ...]
    source_revision: str
    missing: tuple[str, ...]
    control: ProducerControl
    empty_reason: str = ""


@dataclass(frozen=True)
class DirectoryResult:
    outcomes: list[Outcome]
    coverage: Coverage
    notices: tuple[ImportRefusal, ...]


def _refuse(code: str, member: str, why: str, instead: str) -> ImportRefusal:
    return ImportRefusal(code, member, why, instead)


def parse_envelope(payload: Any, member: str = "estate-export.json") -> ExportEnvelope:
    if not isinstance(payload, dict):
        raise _refuse(
            "MALFORMED-JSON",
            member,
            "export manifest is not a JSON object",
            "write one ESTATE/1.0 manifest object",
        )
    if payload.get("contract_version") != "ESTATE/1.0":
        raise _refuse(
            "UNKNOWN-PRODUCER",
            member,
            f"unsupported contract version {payload.get('contract_version')!r}",
            "export using ESTATE/1.0",
        )
    producer = str(payload.get("producer") or "")
    if producer not in SUPPORTED_PRODUCERS:
        raise _refuse(
            "UNKNOWN-PRODUCER",
            member,
            f"unsupported producer {producer!r}",
            "use a supported on-disk producer or add an explicit parser",
        )
    raw_members = payload.get("members")
    if not isinstance(raw_members, list):
        raise _refuse(
            "MISSING-REQUIRED-FIELD",
            member,
            "members is absent or not an array",
            "declare every exported member and its SHA-256",
        )
    declared_count = payload.get("member_count")
    if declared_count != len(raw_members):
        raise _refuse(
            "COUNT-MISMATCH",
            member,
            f"member_count is {declared_count!r} but members contains {len(raw_members)} entries",
            "derive member_count from the same member list",
        )
    members: list[ExportMember] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_members):
        if not isinstance(raw, dict):
            raise _refuse(
                "MISSING-REQUIRED-FIELD",
                f"{member}#members[{index}]",
                "member entry is not an object",
                "declare path and sha256 for each member",
            )
        path = str(raw.get("path") or "")
        digest = str(raw.get("sha256") or "")
        candidate = PurePosixPath(path.replace("\\", "/"))
        if not path or candidate.is_absolute() or ".." in candidate.parts:
            raise _refuse(
                "PATH-ESCAPE",
                path or f"{member}#members[{index}]",
                "member path is absolute, empty, or traverses above the export root",
                "declare a normalized relative path below the export root",
            )
        normalized = candidate.as_posix()
        if normalized in seen:
            raise _refuse(
                "DUPLICATE-ID-CONFLICT",
                normalized,
                "the same export path is declared more than once",
                "declare every member path exactly once",
            )
        if not re.fullmatch(r"[0-9A-Fa-f]{64}", digest):
            raise _refuse(
                "MISSING-REQUIRED-FIELD",
                normalized,
                "member SHA-256 is absent or not 64 hexadecimal characters",
                "record the SHA-256 of the exported bytes",
            )
        seen.add(normalized)
        members.append(ExportMember(normalized, digest.lower()))

    complete = payload.get("complete")
    if not isinstance(complete, bool):
        raise _refuse(
            "MISSING-REQUIRED-FIELD",
            member,
            "complete is absent or not boolean",
            "state whether the producer exported its entire declared scope",
        )
    raw_missing = payload.get("missing")
    if not isinstance(raw_missing, list) or not all(isinstance(item, str) for item in raw_missing):
        raise _refuse(
            "MISSING-REQUIRED-FIELD",
            member,
            "missing is absent or not an array of names",
            "use an empty array for a complete export or name every missing item",
        )
    if complete and raw_missing:
        raise _refuse(
            "EXPORT-INCOMPLETE",
            member,
            "the export claims complete while naming missing material",
            "mark the export partial or include the missing material",
        )
    if not complete and not raw_missing:
        raise _refuse(
            "PARTIAL-EXPORT",
            member,
            "the export claims partial but does not name what is missing",
            "name every missing scope or mark the export complete",
        )
    raw_control = payload.get("control")
    if not isinstance(raw_control, dict):
        raise _refuse(
            "MISSING-REQUIRED-FIELD",
            member,
            "producer positive control is absent or not an object",
            "include control with non-empty name, fired boolean, and evidence",
        )
    control_name = str(raw_control.get("name") or "").strip()
    control_evidence = str(raw_control.get("evidence") or "").strip()
    control_fired = raw_control.get("fired")
    if not control_name or not control_evidence or not isinstance(control_fired, bool):
        raise _refuse(
            "MISSING-REQUIRED-FIELD",
            member,
            "producer positive control lacks name, boolean fired, or evidence",
            "report the producer-specific sentinel and how it was observed",
        )
    if not control_fired:
        raise _refuse(
            "COUNT-MISMATCH",
            member,
            f"producer positive control {control_name!r} did not fire",
            "repair the exporter and rerun before trusting its denominator",
        )
    empty_reason = str(payload.get("empty_reason") or "").strip()
    if not members and not empty_reason:
        raise _refuse(
            "COUNT-MISMATCH",
            member,
            "zero members cannot be distinguished from a failed exporter",
            "include a non-empty empty_reason and the producer control result",
        )
    required_strings = ("source_root_id", "generated_at", "source_revision")
    for field_name in required_strings:
        if not str(payload.get(field_name) or "").strip():
            raise _refuse(
                "MISSING-REQUIRED-FIELD",
                member,
                f"required export field {field_name!r} is empty",
                f"include a non-empty {field_name}",
            )
    if not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?Z",
        str(payload["generated_at"]),
    ):
        raise _refuse(
            "MISSING-REQUIRED-FIELD",
            member,
            "generated_at is not a UTC timestamp with a Z suffix",
            "emit the immutable export time in RFC 3339 UTC form",
        )
    return ExportEnvelope(
        producer=producer,
        source_root_id=str(payload["source_root_id"]),
        generated_at=str(payload["generated_at"]),
        complete=complete,
        members=tuple(members),
        source_revision=str(payload["source_revision"]),
        missing=tuple(raw_missing),
        control=ProducerControl(
            name=control_name,
            fired=control_fired,
            evidence=control_evidence,
        ),
        empty_reason=empty_reason,
    )


def _confined(root: Path, relative: str) -> Path:
    root = root.resolve()
    target = (root / Path(*PurePosixPath(relative).parts)).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise _refuse(
            "PATH-ESCAPE",
            relative,
            "member resolves outside the export root, including through a symlink",
            "copy the member into the export root and regenerate the manifest",
        ) from exc
    return target


def verify_stable_bytes(
    *, member: str, first: bytes, second: bytes, expected_sha256: str
) -> bytes:
    if first != second:
        raise _refuse(
            "SOURCE-MOVED-DURING-IMPORT",
            member,
            "member bytes changed between the first and second read",
            "freeze the export and rerun from its immutable copy",
        )
    digest = hashlib.sha256(first).hexdigest()
    if digest != expected_sha256.lower():
        raise _refuse(
            "DIGEST-MISMATCH",
            member,
            f"manifest SHA-256 {expected_sha256.lower()} does not match bytes {digest}",
            "regenerate the export or restore the declared member bytes",
        )
    return first


def _partial(outcome: Outcome, missing: tuple[str, ...]) -> Outcome:
    if outcome.entity is None or outcome.disposition not in {"imported", "review_required"}:
        return outcome
    loss = Loss(
        code="PARTIAL-EXPORT",
        field="export.missing",
        disposition="review_required",
        why=f"the producer says material is missing: {', '.join(missing)}",
        source_value_present=True,
    )
    entity = replace(
        outcome.entity,
        activation="review_required",
        losses=(*outcome.entity.losses, loss),
    )
    return replace(
        outcome,
        disposition="review_required",
        entity=entity,
        losses=(*outcome.losses, loss),
    )


def import_directory(
    root: Path,
    *,
    reader: Callable[[Path], bytes] | None = None,
) -> DirectoryResult:
    root = root.resolve()
    manifest_path = root / "estate-export.json"
    try:
        raw_manifest = manifest_path.read_bytes()
    except OSError as exc:
        raise _refuse(
            "EXPORT-INCOMPLETE",
            "estate-export.json",
            f"manifest cannot be read: {exc}",
            "place estate-export.json at the export root",
        ) from exc
    try:
        payload = json.loads(raw_manifest.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise _refuse(
            "MALFORMED-JSON",
            "estate-export.json",
            "manifest is not valid UTF-8 JSON",
            "write one UTF-8 ESTATE/1.0 manifest object",
        ) from exc
    envelope = parse_envelope(payload)
    read = reader or (lambda path: path.read_bytes())
    declared_paths = {member.path for member in envelope.members}
    outcomes: list[Outcome] = []
    entity_ids: set[str] = set()
    for member in envelope.members:
        path = _confined(root, member.path)
        if not path.is_file():
            refusal = _refuse(
                "EXPORT-INCOMPLETE",
                member.path,
                "manifest declares a member that is absent",
                "restore the file or mark the export partial and name it as missing",
            )
            outcomes.append(Outcome(member=member.path, disposition="refused", refusal=refusal))
            continue
        try:
            data = verify_stable_bytes(
                member=member.path,
                first=read(path),
                second=read(path),
                expected_sha256=member.sha256,
            )
            if member.path.endswith(".md"):
                outcome = markdown_outcome(
                    data=data,
                    member=member.path,
                    producer=envelope.producer,
                    source_revision=envelope.source_revision,
                )
            elif member.path.endswith(".json"):
                companion = member.path[:-5] + ".md"
                if companion not in declared_paths:
                    raise _refuse(
                        "MISSING-PAIR",
                        member.path,
                        f"deterministic workflow companion {companion!r} is not declared",
                        "export the JSON and companion Markdown together",
                    )
                parse_workflow_json(data, member.path)
                loss = Loss(
                    code="WORKFLOW-SUPPORTING-FILE",
                    field="json",
                    disposition="dropped",
                    why="the JSON is validated and retained by its companion Workflow entity",
                    source_value_present=True,
                )
                outcome = Outcome(member=member.path, disposition="dropped", losses=(loss,))
            else:
                raise _refuse(
                    "UNKNOWN-ENTITY-TYPE",
                    member.path,
                    "file extension has no parser in this producer version",
                    "export Markdown entities or paired deterministic workflow JSON",
                )
            if outcome.entity is not None:
                if outcome.entity.entity_id in entity_ids:
                    raise _refuse(
                        "DUPLICATE-ID-CONFLICT",
                        member.path,
                        f"stable id {outcome.entity.entity_id!r} already appeared in this export",
                        "give distinct entities distinct stable ids",
                    )
                entity_ids.add(outcome.entity.entity_id)
            if not envelope.complete:
                outcome = _partial(outcome, envelope.missing)
            outcomes.append(outcome)
        except ImportRefusal as refusal:
            outcomes.append(Outcome(member=member.path, disposition="refused", refusal=refusal))

    notices: tuple[ImportRefusal, ...] = ()
    if not envelope.complete:
        notices = (
            _refuse(
                "PARTIAL-EXPORT",
                "estate-export.json",
                f"producer named missing material: {', '.join(envelope.missing)}",
                "review every retained member and acquire the missing export separately",
            ),
        )
    coverage = Coverage.from_outcomes(outcomes, control_fired=envelope.control.fired)
    return DirectoryResult(outcomes=outcomes, coverage=coverage, notices=notices)
