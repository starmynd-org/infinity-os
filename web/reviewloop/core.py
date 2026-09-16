"""The self-correcting attention loop.

Git contains authored behaviour. Postgres contains the decision in flight. This module
prepares the seam between them without writing queue state and without publishing Git state.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import difflib
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import Iterable, Sequence


ITEM_VERSION = "ITEM/1.0"
C01_VERSION = "0.1.0-draft.5"
TRIVIAL_OPTIONS = {"acknowledge", "dismiss", "noted"}
FORBIDDEN_ITEM_FIELDS = {"rank", "score", "points", "position", "tier", "badge", "unread", "count"}
OPERATIONAL_DATA_DIRECTORIES = {"queue", "receipts", "events", "sessions", "transcripts", "run-logs"}
OPERATIONAL_RECORD_KINDS = {"queue-row", "receipt", "event", "session", "transcript", "run-log"}
SENSITIVE_VALUE_KEYS = {
    "api_key",
    "access_token",
    "refresh_token",
    "password",
    "secret",
    "credential",
    "private_key",
    "authority",
    "entitlement",
}
ALLOWED_GIT_COMMANDS = {
    "add",
    "branch",
    "commit",
    "diff",
    "grep",
    "log",
    "ls-tree",
    "rev-parse",
    "show",
    "status",
    "worktree",
}


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_repo_path(value: str) -> str:
    raw = str(value or "").replace("\\", "/")
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise ValueError(f"target path must be a repository-relative path: {value!r}")
    return path.as_posix()


def classify_target(value: str) -> str:
    """Classify an authored path using the architecture ruling.

    The order is material. A draft inside a governed directory remains governed, and a
    migration whose name contains DRAFT remains a migration.
    """

    path = normalize_repo_path(value)
    parts = PurePosixPath(path).parts
    lower_parts = tuple(part.lower() for part in parts)
    name = lower_parts[-1]

    if "migrations" in lower_parts:
        return "migration"
    if (
        (lower_parts[0] == "web" and name.startswith("must-not-build"))
        or name in {"agents.md", "claude.md"}
        or "repo-registry" in lower_parts
        or "decisions" in lower_parts
        or "secret-registry" in lower_parts
        or "secret_registry" in lower_parts
        or name.startswith("secret-registry.")
        or name.startswith("secret_registry.")
    ):
        return "governed"
    if "drafts" in lower_parts or re.search(r"(^|[-_.])draft([-_.]|$)", name, re.IGNORECASE):
        return "draft"
    return "authored"


def _schema_key(value: str) -> str:
    return value.strip().lower().replace("-", "_").replace(".", "_")


def _record_kind(value: str) -> str:
    return re.sub(r"[\s_]+", "-", value.strip().lower())


def _structured_keys(value) -> Iterable[str]:
    if isinstance(value, dict):
        for key, nested in value.items():
            yield _schema_key(str(key))
            yield from _structured_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _structured_keys(nested)


def inspect_proposal_payload(target_path: str, replacement: str) -> Refusal | None:
    """Apply the finite structured payload boundary declared by REVIEWLOOP/1.0.

    This deliberately does not claim semantic secret detection over arbitrary prose. It covers
    operational data targets and explicit JSON, YAML-like, or environment-style sensitive keys.
    """

    path = normalize_repo_path(target_path)
    lower_parts = tuple(part.lower() for part in PurePosixPath(path).parts)
    if len(lower_parts) >= 2 and lower_parts[0] == "data" and lower_parts[1] in OPERATIONAL_DATA_DIRECTORIES:
        return Refusal(
            "OPERATIONAL-STATE-REFUSES",
            path,
            "the target is a declared operational-state data path, which belongs outside authored Git behaviour",
            "write operational state through its runtime owner instead of proposing it as authored material",
        )

    duplicate_json_members: list[str] = []

    def reject_duplicate_members(pairs):
        parsed_object = {}
        seen = set()
        for key, nested in pairs:
            member = str(key)
            if member in seen:
                duplicate_json_members.append(member)
            seen.add(member)
            parsed_object[key] = nested
        return parsed_object

    parsed = None
    try:
        parsed = json.loads(replacement, object_pairs_hook=reject_duplicate_members)
    except (json.JSONDecodeError, TypeError):
        pass
    if duplicate_json_members:
        members = sorted(set(duplicate_json_members))
        return Refusal(
            "DUPLICATE-JSON-MEMBER-REFUSES",
            path,
            f"the complete JSON contains repeated member(s): {', '.join(members)}",
            "use unique JSON member names so admission inspects the same unambiguous structure that is emitted",
        )
    if isinstance(parsed, dict) and _record_kind(str(parsed.get("record_kind", ""))) in OPERATIONAL_RECORD_KINDS:
        return Refusal(
            "OPERATIONAL-STATE-REFUSES",
            path,
            "the replacement declares an operational record kind, which belongs outside authored Git behaviour",
            "write operational state through its runtime owner instead of proposing it as authored material",
        )

    found_keys = set(_structured_keys(parsed)) if parsed is not None else set()
    for line in replacement.splitlines():
        match = re.match(r"^\s*([A-Za-z][A-Za-z0-9_.-]*)\s*[:=]", line)
        if match:
            found_keys.add(_schema_key(match.group(1)))
    forbidden = sorted(found_keys.intersection(SENSITIVE_VALUE_KEYS))
    if forbidden:
        return Refusal(
            "SENSITIVE-PAYLOAD-REFUSES",
            path,
            f"the replacement assigns forbidden structured field(s): {', '.join(forbidden)}",
            "store the sensitive value or authority outside Git and propose only a stable reference",
        )
    return None


@dataclasses.dataclass(frozen=True)
class Refusal:
    code: str
    target_path: str
    why: str
    instead: str
    evidence: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


class ReviewLoopError(RuntimeError):
    def __init__(self, refusal: Refusal):
        super().__init__(f"{refusal.code}: {refusal.why}")
        self.refusal = refusal


@dataclasses.dataclass(frozen=True)
class ReviewRule:
    target_path: str
    needle: str
    replacement: str
    title: str
    why: str
    rationale: str
    novelty_queries: tuple[str, ...]
    named_reviewer: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "target_path", normalize_repo_path(self.target_path))
        if not self.needle:
            raise ValueError("review rule needle must not be empty")
        if not self.replacement:
            raise ValueError("review rule replacement must not be empty")
        if not self.novelty_queries or any(not value.strip() for value in self.novelty_queries):
            raise ValueError("every review rule needs at least one non-empty novelty query")


@dataclasses.dataclass(frozen=True)
class Proposal:
    proposal_id: str
    target_path: str
    target_class: str
    base_ref: str
    base_sha: str
    base_content_sha256: str
    replacement_content_sha256: str
    replacement_content: str
    patch: str
    title: str
    rationale: str
    producer_actor: str
    required_reviewer_class: str
    named_reviewer: str | None
    novelty: tuple[dict, ...]
    attention_item: dict

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclasses.dataclass(frozen=True)
class AcceptedDecision:
    proposal_id: str
    decision: str
    decided_by: str
    decided_at: str
    actor_kind: str = "human"


@dataclasses.dataclass(frozen=True)
class MaterializationResult:
    status: str
    proposal_id: str
    branch: str | None = None
    commit: str | None = None
    worktree: str | None = None
    missing: str | None = None
    conflict_item: dict | None = None

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


class GitRepository:
    """A local-only Git adapter with no remote or merge command in its vocabulary."""

    def __init__(self, root: str | os.PathLike[str]):
        resolved = Path(root).resolve()
        if not resolved.is_absolute():
            raise ValueError("repository root must be absolute")
        self.root = resolved
        self._run("rev-parse", "--is-inside-work-tree")

    def _run(
        self,
        command: str,
        *args: str,
        ok_codes: tuple[int, ...] = (0,),
        root: Path | None = None,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        if command not in ALLOWED_GIT_COMMANDS:
            raise ValueError(f"git command is not available to reviewloop: {command}")
        result = subprocess.run(
            ["git", "-C", str(root or self.root), command, *map(str, args)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            env={**os.environ, **(env or {})},
        )
        if result.returncode not in ok_codes:
            detail = result.stderr.strip() or result.stdout.strip() or "no diagnostic"
            raise RuntimeError(f"git {command} failed with {result.returncode}: {detail}")
        return result

    def resolve(self, ref: str) -> str:
        return self._run("rev-parse", f"{ref}^{{commit}}").stdout.strip()

    def read_text(self, ref: str, target_path: str) -> str:
        path = normalize_repo_path(target_path)
        return self._run("show", f"{ref}:{path}").stdout

    def tracked_files(self, ref: str) -> tuple[str, ...]:
        sha = self.resolve(ref)
        lines = self._run("ls-tree", "-r", "--name-only", sha).stdout.splitlines()
        return tuple(line for line in lines if line)

    def grep(self, ref: str, query: str) -> tuple[dict, ...]:
        """Run the required pre-proposal repository grep at an immutable commit."""

        sha = self.resolve(ref)
        result = self._run("grep", "-n", "-F", "-e", query, sha, "--", ok_codes=(0, 1))
        if result.returncode == 1:
            return ()
        prefix = sha + ":"
        matches = []
        for line in result.stdout.splitlines():
            body = line[len(prefix):] if line.startswith(prefix) else line
            bits = body.split(":", 2)
            if len(bits) != 3:
                raise RuntimeError(f"could not parse git grep result: {line}")
            matches.append({"path": bits[0], "line": int(bits[1]), "text": bits[2]})
        return tuple(matches)


def _option(option_id: str, label: str, does: str, reversibility: str, inverse: dict) -> dict:
    return {
        "option_id": option_id,
        "label": label,
        "does": does,
        "kind": "agent-helps" if option_id != "open-both" else "human-does",
        "reversibility": reversibility,
        "inverse": inverse,
        "recommended": False,
    }


def _proposal_options() -> list[dict]:
    return [
        _option(
            "accept-change",
            "Prepare the local branch",
            "Prepares the reviewed diff as a commit on a new local branch and does not publish it.",
            "reversible",
            {
                "exists": True,
                "verb": "withdraw proposal",
                "does": "Withdraws the unmerged proposal branch without changing published history.",
            },
        ),
        _option(
            "reject-change",
            "Reject this change",
            "Records why this proposal should not change the authored material.",
            "costly",
            {
                "exists": False,
                "why": "a rejection is a recorded review decision; a later change needs a new proposal",
            },
        ),
        _option(
            "send-back",
            "Send back for revision",
            "Returns the proposal with feedback and changes no repository content.",
            "reversible",
            {
                "exists": True,
                "verb": "resume review",
                "does": "Resumes review after a revised proposal is supplied.",
            },
        ),
    ]


def _conflict_options() -> list[dict]:
    return [
        _option(
            "use-proposed",
            "Prepare the proposed version",
            "Asks the agent to reconcile both versions while preserving the proposed intent.",
            "costly",
            {
                "exists": True,
                "verb": "restore current version",
                "does": "Prepares a follow-up diff that restores the version current at conflict time.",
            },
        ),
        _option(
            "keep-current",
            "Keep the current version",
            "Rejects this proposal and leaves the current target unchanged.",
            "reversible",
            {
                "exists": False,
                "why": "keeping the current version makes no repository change to reverse",
            },
        ),
        _option(
            "open-both",
            "Open both versions",
            "Opens the current and proposed versions for a human to compare before deciding.",
            "reversible",
            {"exists": True, "verb": "close comparison", "does": "Closes the comparison without changing either version."},
        ),
    ]


def _attention_item(
    *,
    item_id: str,
    workspace: str,
    producer_actor: str,
    created_at: str,
    title: str,
    why: str,
    kind: str,
    target_class: str,
    options: list[dict],
    provenance_what: str,
) -> dict:
    governed = target_class == "governed"
    item = {
        "contract_version": ITEM_VERSION,
        "c01_version": C01_VERSION,
        "item_id": item_id,
        "version": 1,
        "workspace": workspace,
        "producer": {
            "source": "attention",
            "actor": {"actor_id": producer_actor, "kind": "worker", "workspace": workspace},
        },
        "created_at": created_at,
        "title": title[:200],
        "why": why[:500],
        "kind": kind,
        "options": options,
        "signals": {
            "stakes": "high" if governed else "medium",
            "reversibility": "costly" if governed else "reversible",
            "urgency": "none",
            "dependency_unblocking": 0,
            "effort": "small",
            "confidence": 1.0,
            "charter_alignment": "high",
        },
        "impact": {"status": "unknown"},
        "freshness": {"read_at": created_at, "interval_seconds": 86400},
        "provenance": {
            "source_label": "Review Loop",
            "zone": "UTC",
            "steps": [{"at": created_at, "what": provenance_what, "by": producer_actor}],
        },
        "readable": True,
        "external": False,
        "canon_touching": governed,
        "authority_required": "internal" if governed else "none",
        "tier_hint": "review" if governed else "create",
    }
    if governed:
        item["surfacing"] = "human"
    validate_attention_item(item)
    return item


def validate_attention_item(item: dict) -> None:
    forbidden = FORBIDDEN_ITEM_FIELDS.intersection(item)
    if forbidden:
        raise ValueError(f"proposal item contains forbidden row fields: {sorted(forbidden)}")
    has_options = bool(item.get("options"))
    has_closed = bool(item.get("closed"))
    if has_options == has_closed:
        raise ValueError("proposal item must carry exactly one of options or closed")
    if has_options:
        real = 0
        for option in item["options"]:
            if "inverse" not in option:
                raise ValueError(f"option {option.get('option_id')} has no inverse declaration")
            if option.get("option_id", "").lower() not in TRIVIAL_OPTIONS and option.get("label", "").lower() not in TRIVIAL_OPTIONS:
                real += 1
        if real == 0:
            raise ValueError("proposal item is not decidable: every option is an acknowledgement")
    if item.get("canon_touching") and item.get("surfacing") != "human":
        raise ValueError("canon-touching proposal must surface to a human")


def _reviewer_class(target_class: str) -> str:
    if target_class == "governed":
        return "named-human"
    if target_class == "draft":
        return "workspace-reviewer"
    return "repository-maintainer"


def _build_proposal(
    *,
    rule: ReviewRule,
    base_ref: str,
    base_sha: str,
    content: str,
    replacement: str,
    target_class: str,
    workspace: str,
    producer_actor: str,
    created_at: str,
    novelty: tuple[dict, ...],
) -> Proposal:
    patch = "".join(
        difflib.unified_diff(
            content.splitlines(keepends=True),
            replacement.splitlines(keepends=True),
            fromfile=f"a/{rule.target_path}",
            tofile=f"b/{rule.target_path}",
        )
    )
    if not patch:
        raise ReviewLoopError(
            Refusal(
                "EMPTY-DIFF",
                rule.target_path,
                "the candidate replacement produces no diff",
                "change the rule or remove it from the pass",
            )
        )
    digest_input = "\0".join((base_sha, rule.target_path, patch, producer_actor))
    proposal_id = "reviewloop-" + sha256_text(digest_input)[:16]
    item = _attention_item(
        item_id=proposal_id,
        workspace=workspace,
        producer_actor=producer_actor,
        created_at=created_at,
        title=rule.title,
        why=rule.why,
        kind="proposal",
        target_class=target_class,
        options=_proposal_options(),
        provenance_what=f"reviewed {rule.target_path} at {base_sha[:12]} and prepared a local-only diff",
    )
    return Proposal(
        proposal_id=proposal_id,
        target_path=rule.target_path,
        target_class=target_class,
        base_ref=base_ref,
        base_sha=base_sha,
        base_content_sha256=sha256_text(content),
        replacement_content_sha256=sha256_text(replacement),
        replacement_content=replacement,
        patch=patch,
        title=rule.title,
        rationale=rule.rationale,
        producer_actor=producer_actor,
        required_reviewer_class=_reviewer_class(target_class),
        named_reviewer=rule.named_reviewer,
        novelty=novelty,
        attention_item=item,
    )


def run_pass(
    repository: GitRepository,
    *,
    base_ref: str,
    workspace: str,
    producer_actor: str,
    rules: Sequence[ReviewRule],
    invocation: str,
    run_at: str | None = None,
) -> dict:
    """Run one full, declared pass and return denominator-first evidence."""

    created_at = run_at or utc_now()
    base_sha = repository.resolve(base_ref)
    tracked_files = repository.tracked_files(base_sha)
    proposals: list[Proposal] = []
    refusals: list[Refusal] = []
    suppressions: list[Refusal] = []
    candidates = 0

    for rule in rules:
        content = repository.read_text(base_sha, rule.target_path)
        occurrences = content.count(rule.needle)
        if occurrences == 0:
            continue
        candidates += 1
        if occurrences != 1:
            refusals.append(
                Refusal(
                    "AMBIGUOUS-TARGET",
                    rule.target_path,
                    f"the target needle occurs {occurrences} times, so the proposed diff is not uniquely located",
                    "use a rule whose target occurs exactly once",
                )
            )
            continue

        target_class = classify_target(rule.target_path)
        if target_class == "migration":
            refusals.append(
                Refusal(
                    "MIGRATION-REFUSES",
                    rule.target_path,
                    "migrations are append-only and an existing migration cannot be proposed as a conflict resolution",
                    "scope a new append-only migration through the migration owner",
                )
            )
            continue
        if target_class == "governed" and not str(rule.named_reviewer or "").strip():
            refusals.append(
                Refusal(
                    "NAMED-HUMAN-REQUIRED",
                    rule.target_path,
                    "a governed-file proposal has no named human reviewer",
                    "name the human who will review the governed diff",
                )
            )
            continue

        replacement = content.replace(rule.needle, rule.replacement, 1)
        payload_refusal = inspect_proposal_payload(rule.target_path, replacement)
        if payload_refusal is not None:
            refusals.append(payload_refusal)
            continue

        novelty_rows = []
        existing = []
        for query in rule.novelty_queries:
            matches = repository.grep(base_sha, query)
            novelty_rows.append(
                {
                    "query": query,
                    "invocation": f"git grep -n -F -e <query> {base_sha} --",
                    "tracked_file_denominator": len(tracked_files),
                    "matches": list(matches),
                }
            )
            existing.extend(match for match in matches if match["path"] != rule.target_path)
        if existing:
            evidence = tuple(f"{row['path']}:{row['line']}" for row in existing)
            suppressions.append(
                Refusal(
                    "EXISTING-ANSWER-FOUND",
                    rule.target_path,
                    "the repository already contains the thing this rule was about, so emitting another proposal would re-litigate it",
                    "read the matched source and revise or retire the rule",
                    evidence=evidence,
                )
            )
            continue

        try:
            proposals.append(
                _build_proposal(
                    rule=rule,
                    base_ref=base_ref,
                    base_sha=base_sha,
                    content=content,
                    replacement=replacement,
                    target_class=target_class,
                    workspace=workspace,
                    producer_actor=producer_actor,
                    created_at=created_at,
                    novelty=tuple(novelty_rows),
                )
            )
        except ReviewLoopError as exc:
            refusals.append(exc.refusal)

    proposed = len(proposals)
    report = {
        "measurement": {
            "measured_at": created_at,
            "store": f"git:{repository.root}",
            "invocation": invocation,
            "base_ref": base_ref,
            "base_sha": base_sha,
            "denominator": {
                "review_targets": len(rules),
                "tracked_files_per_novelty_query": len(tracked_files),
            },
            "predicate": (
                "propose only when the declared target contains one unique needle, every pre-proposal "
                "repository grep has no match outside that target, the target is not a migration, a "
                "governed target names its human reviewer, and the complete candidate content passes "
                "the declared finite structured payload boundary"
            ),
        },
        "counts": {
            "reviewed": len(rules),
            "candidates": candidates,
            "proposed": proposed,
            "suppressed_existing_answer": len(suppressions),
            "refused": len(refusals),
        },
        "proposals_per_pass": [proposed],
        "proposals": [proposal.to_dict() for proposal in proposals],
        "suppressions": [refusal.to_dict() for refusal in suppressions],
        "refusals": [refusal.to_dict() for refusal in refusals],
        "confirmation": {
            "proposal_denominator": proposed,
            "confirmed_by_non_author": 0,
            "fraction": "not-measurable" if proposed == 0 else f"0/{proposed} pending review",
        },
        "false_positive_rate": {
            "decided_proposal_denominator": 0,
            "declined": 0,
            "rate": "not-measurable",
            "why": "no proposal in this pass has a human decision",
        },
        "stopping_condition": {
            "stop": proposed == 0,
            "predicate": "stop when a full pass produces no proposal a non-author confirms",
            "reason": (
                "the full pass emitted no proposal"
                if proposed == 0
                else "pending non-author decisions; this pass cannot stop yet"
            ),
        },
        "boundary": {
            "pushes": 0,
            "governed_file_writes": 0,
            "queue_store_writes": 0,
        },
    }
    return report


def summarize_decisions(proposals: Sequence[Proposal], decisions: Sequence[AcceptedDecision]) -> dict:
    by_id = {proposal.proposal_id: proposal for proposal in proposals}
    if len(by_id) != len(proposals):
        raise ValueError("proposal denominator contains the same proposal more than once")
    decided = 0
    declined = 0
    confirmed = 0
    seen_decisions: set[str] = set()
    for decision in decisions:
        proposal = by_id.get(decision.proposal_id)
        if proposal is None:
            raise ValueError(f"decision names an unknown proposal: {decision.proposal_id}")
        if decision.proposal_id in seen_decisions:
            raise ValueError(f"more than one decision names proposal: {decision.proposal_id}")
        seen_decisions.add(decision.proposal_id)
        if decision.decision not in {"accept-change", "reject-change", "send-back"}:
            raise ValueError(f"decision is not one of the proposal row's options: {decision.decision}")
        decided += 1
        non_author_human = decision.actor_kind == "human" and decision.decided_by != proposal.producer_actor
        if decision.decision == "accept-change" and non_author_human:
            confirmed += 1
        if decision.decision == "reject-change":
            declined += 1
    return {
        "proposal_denominator": len(proposals),
        "decided_proposal_denominator": decided,
        "confirmed_by_non_author": confirmed,
        "confirmed_fraction": "not-measurable" if not proposals else f"{confirmed}/{len(proposals)}",
        "declined": declined,
        "false_positive_rate": "not-measurable" if decided == 0 else declined / decided,
    }


def _conflict_item(proposal: Proposal, current_sha: str, current_content: str, decided_at: str) -> dict:
    why = (
        f"{proposal.target_path} changed after proposal {proposal.proposal_id} was prepared. "
        "Last-write-wins is refused, so a person must choose how the two versions are reconciled."
    )
    item = _attention_item(
        item_id="conflict-" + sha256_text(proposal.proposal_id + current_sha)[:16],
        workspace=proposal.attention_item["workspace"],
        producer_actor=proposal.producer_actor,
        created_at=decided_at,
        title=f"Resolve concurrent edits to {proposal.target_path}",
        why=why,
        kind="exception",
        target_class=proposal.target_class,
        options=_conflict_options(),
        provenance_what=(
            f"compared proposal base {proposal.base_sha[:12]} with current {current_sha[:12]}; "
            f"content hashes {proposal.base_content_sha256[:12]} and {sha256_text(current_content)[:12]} differ"
        ),
    )
    return item


def materialize_accepted(
    repository: GitRepository,
    proposal: Proposal,
    decision: AcceptedDecision,
    *,
    worktree_path: str | os.PathLike[str],
) -> MaterializationResult:
    """Prepare an accepted proposal as a local branch and stop before publication."""

    if decision.proposal_id != proposal.proposal_id or decision.decision != "accept-change":
        raise ReviewLoopError(
            Refusal(
                "HUMAN-ACCEPTANCE-REQUIRED",
                proposal.target_path,
                "the decision does not accept this proposal",
                "record an accept-change decision from a human reviewer",
            )
        )
    if decision.actor_kind != "human":
        raise ReviewLoopError(
            Refusal(
                "HUMAN-ACCEPTANCE-REQUIRED",
                proposal.target_path,
                "acceptance is not attributed to a human",
                "review and accept the proposal through a human identity",
            )
        )
    if decision.decided_by == proposal.producer_actor:
        raise ReviewLoopError(
            Refusal(
                "HUMAN-ACCEPTANCE-REQUIRED",
                proposal.target_path,
                "acceptance is attributed to the proposal producer, so it is not a non-author review",
                "route the proposal to a different human reviewer",
            )
        )
    if proposal.target_class == "governed" and decision.decided_by != proposal.named_reviewer:
        raise ReviewLoopError(
            Refusal(
                "WRONG-GOVERNED-REVIEWER",
                proposal.target_path,
                f"the governed proposal names {proposal.named_reviewer!r}, not {decision.decided_by!r}",
                "route the decision to the named human reviewer",
            )
        )
    if proposal.target_class == "migration":
        raise ReviewLoopError(
            Refusal(
                "MIGRATION-REFUSES",
                proposal.target_path,
                "an existing migration cannot be materialized or conflict-resolved",
                "scope a new append-only migration through the migration owner",
            )
        )

    current_sha = repository.resolve(proposal.base_ref)
    current_content = repository.read_text(current_sha, proposal.target_path)
    if sha256_text(current_content) != proposal.base_content_sha256:
        return MaterializationResult(
            status="conflict-attention-item",
            proposal_id=proposal.proposal_id,
            conflict_item=_conflict_item(proposal, current_sha, current_content, decision.decided_at),
            missing="A human conflict decision is required before any local branch is prepared.",
        )

    destination = Path(worktree_path).resolve()
    if destination.exists():
        raise ReviewLoopError(
            Refusal(
                "WORKTREE-EXISTS",
                proposal.target_path,
                f"the requested proposal worktree already exists: {destination}",
                "choose a new empty absolute worktree path",
            )
        )
    branch = "reviewloop/" + proposal.proposal_id.removeprefix("reviewloop-")
    repository._run("worktree", "add", "-b", branch, str(destination), current_sha)
    target = (destination / PurePosixPath(proposal.target_path)).resolve()
    if destination not in target.parents:
        raise RuntimeError("target escaped the proposal worktree")
    target.write_text(proposal.replacement_content, encoding="utf-8", newline="")
    repository._run("add", "--", proposal.target_path, root=destination)
    staged = repository._run("diff", "--cached", "--quiet", ok_codes=(0, 1), root=destination)
    if staged.returncode == 0:
        raise ReviewLoopError(
            Refusal(
                "EMPTY-DIFF",
                proposal.target_path,
                "the accepted proposal produces no staged change",
                "withdraw the stale proposal",
            )
        )
    message = (
        f"reviewloop: {proposal.title}\n\n"
        f"Proposal: {proposal.proposal_id}\n"
        f"Accepted-by: {decision.decided_by}\n"
        "Publication: not performed"
    )
    repository._run(
        "commit",
        "-m",
        message,
        root=destination,
        env={
            "GIT_AUTHOR_NAME": "Review Loop",
            "GIT_AUTHOR_EMAIL": "reviewloop@local",
            "GIT_COMMITTER_NAME": "Review Loop",
            "GIT_COMMITTER_EMAIL": "reviewloop@local",
        },
    )
    commit = repository._run("rev-parse", "HEAD", root=destination).stdout.strip()
    return MaterializationResult(
        status="prepared-local-branch",
        proposal_id=proposal.proposal_id,
        branch=branch,
        commit=commit,
        worktree=str(destination),
        missing=(
            "Push and pull request publication are not built. A human must publish the branch "
            "and open or review the pull request."
        ),
    )


def write_json(path: str | os.PathLike[str], value: dict) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="")
