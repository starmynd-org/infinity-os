"""Read a Claude Code transcript file and return facts about it. Pure read, no writes.

Every field this returns is either measured from the file or None. Nothing is inferred from
timing, nothing is defaulted to zero, and every derived field carries a `*_source` saying
where it came from. A null here means "not in the file", never "zero".

Corpus taxonomy, measured 2026-08-16 over all 1130 files in ~/.claude/projects:

    <root>/<uuid>.jsonl                                     main session      390
    <root>/<uuid>/subagents/agent-<id>.jsonl                subagent          627
    <root>/<uuid>/subagents/workflows/<wf>/agent-<id>.jsonl workflow subagent  106
    <root>/<uuid>/subagents/workflows/<wf>/journal.jsonl    workflow journal     7

The workflow journal is not a session and is not indexed as one.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field, asdict
from typing import Any, Iterator

# Record types that represent a real conversational turn, as opposed to harness bookkeeping
# (attachment, mode, ai-title, queue-operation, file-history-*, pr-link, bridge-session).
TURN_TYPES = {"user", "assistant"}

AGENT_FILE_RE = re.compile(r"^agent-([0-9a-f]+)\.jsonl$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)

KIND_MAIN = "main"
KIND_SUBAGENT = "subagent"
KIND_WORKFLOW_JOURNAL = "workflow-journal"


@dataclass
class TranscriptFacts:
    # --- pointer, never the blob ---
    path: str
    bytes: int
    sha256: str
    line_count: int
    bad_json_lines: int

    # --- identity ---
    kind: str
    session_id: str | None = None          # the harness session uuid
    agent_id: str | None = None            # subagent id, None for a main session
    session_key: str | None = None         # unique across the corpus; see below
    parent_session_id: str | None = None
    parent_source: str | None = None       # 'path+field' | 'path' | 'field' | None
    workflow_id: str | None = None

    # --- provenance ---
    harness: str = "claude-code"
    harness_version: str | None = None
    entrypoint: str | None = None
    model: str | None = None
    model_source: str | None = None
    permission_mode: str | None = None
    git_branch: str | None = None
    is_sidechain: bool | None = None

    # --- where it ran ---
    project_root_dir: str | None = None    # the path-encoded directory name, verbatim
    workdir: str | None = None
    workdir_source: str | None = None      # 'record-cwd' | 'decoded-dirname' | None
    workdir_decode_matches_cwd: bool | None = None  # None when one side is missing

    # --- when ---
    started_at: str | None = None
    ended_at: str | None = None
    time_source: str | None = None         # 'record-timestamps' | None

    # --- volume ---
    turns_user: int = 0
    turns_assistant: int = 0
    turns_source: str = "counted-records"
    tokens_input: int | None = None
    tokens_output: int | None = None
    tokens_cache_read: int | None = None
    tokens_cache_creation: int | None = None
    # THE DENOMINATOR THE FOUR SUMS ABOVE WERE TAKEN OVER. Not decoration: `usage_records`
    # exceeds `messages` on almost every real session, because the harness writes one assistant
    # message as several records and repeats the completed usage block on each. A reader who can
    # see both can tell a deduped figure from a summed-per-record one; a reader who sees only the
    # tokens cannot. `tokens_source` is NULL on every row written before this dedup landed, which
    # is what makes those rows identifiable as inflated rather than merely old.
    tokens_messages: int | None = None        # distinct message ids the sums cover
    tokens_usage_records: int | None = None   # records that carried a usage block
    tokens_source: str | None = None          # 'message-usage-deduped' | None
    cost_usd: float | None = None
    cost_source: str | None = None

    # --- goal candidates, raw. Adjudication lives in goal.py ---
    ai_title: str | None = None
    first_user_text: str | None = None
    away_summary: str | None = None

    record_types: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def decode_project_dir(name: str) -> str:
    """Decode a Claude Code project-root directory name back to a filesystem path.

    Claude Code encodes the cwd by replacing every non-alphanumeric run with '-', so
    '/mnt/c/Users/you/repos' becomes '-mnt-c-Users-you-repos'. The transform is not
    injective: a path containing a literal '-', '.', '_' or ' ' encodes identically to one
    containing '/'. This function takes the most common reading (every '-' was a separator)
    and callers must treat the result as a guess. The authoritative workdir is the `cwd`
    field inside the file; this is only the fallback, and `workdir_decode_matches_cwd`
    reports how often the guess is right.
    """
    return "/" + name.lstrip("-").replace("-", "/")


def encode_project_dir(path: str) -> str:
    """The forward transform, used to test the decode rather than to store anything."""
    return re.sub(r"[^a-zA-Z0-9]", "-", path)


def classify(path: str, projects_root: str) -> tuple[str, dict[str, str | None]]:
    """Decide what a file is from its path alone."""
    rel = os.path.relpath(os.path.abspath(path), os.path.abspath(projects_root))
    parts = rel.split(os.sep)
    info: dict[str, str | None] = {
        "project_root_dir": parts[0] if parts else None,
        "path_parent_session_id": None,
        "path_agent_id": None,
        "workflow_id": None,
    }
    base = parts[-1]
    if len(parts) == 2:
        return KIND_MAIN, info
    if len(parts) >= 4 and parts[2] == "subagents":
        info["path_parent_session_id"] = parts[1] if UUID_RE.match(parts[1]) else None
        if len(parts) == 6 and parts[3] == "workflows":
            info["workflow_id"] = parts[4]
        if base == "journal.jsonl":
            return KIND_WORKFLOW_JOURNAL, info
        m = AGENT_FILE_RE.match(base)
        if m:
            info["path_agent_id"] = m.group(1)
            return KIND_SUBAGENT, info
    return KIND_MAIN, info


def containing_session_id(pointer: str) -> str | None:
    """The session whose DIRECTORY holds this pointer, read from the path and nothing else.

    `classify` needs a projects root because it decides what a file *is*. This does not: every
    file the harness writes below a session lives under `<session-id>/subagents/...`, so the
    component before `subagents` names the session that owns the directory. That is all this
    returns, and it returns it for a path alone -- which matters because the one caller
    (`verbs._classify_absence`) is looking at a pointer whose FILE IS GONE. There is nothing
    left to read, no projects root in scope, and the store row cannot name the session or the
    caller would not be asking.

    It does NOT check the shape of what it returns. A uuid on the live corpus, a fixture name in
    the tests, either way the caller's `SELECT ... WHERE id = %s` is the test: a name no session
    row answers to leaves the age underivable, which is the honest answer and the one already
    encoded as `unknown-age`. Guessing from the shape of a string would add a second, weaker
    test in front of the real one.

    The LAST `subagents` component is the one taken. Nothing the harness writes nests a second
    one, but a project-root directory is the operator's own path with every non-alphanumeric run
    replaced by '-', so it is the one component here that could carry the word by accident.
    """
    parts = os.path.abspath(pointer).split(os.sep)
    for i in range(len(parts) - 1, 0, -1):
        if parts[i] == "subagents":
            return parts[i - 1] or None
    return None


def _iter_records(path: str) -> Iterator[tuple[dict | None, str]]:
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                yield json.loads(raw), raw
            except (ValueError, RecursionError):
                yield None, raw


def _text_of(message: Any) -> str | None:
    """Flatten a message content field to plain text, or None if it has none."""
    if isinstance(message, str):
        return message
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks = [c.get("text", "") for c in content
                  if isinstance(c, dict) and c.get("type") == "text"]
        joined = "\n".join(x for x in chunks if x)
        return joined or None
    return None


def scan(path: str, projects_root: str, first_user_chars: int = 8000) -> TranscriptFacts:
    """Read one transcript file end to end and return what is actually in it."""
    kind, pinfo = classify(path, projects_root)

    sha = hashlib.sha256()
    size = 0
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            sha.update(block)
            size += len(block)

    f = TranscriptFacts(
        path=os.path.abspath(path),
        bytes=size,
        sha256=sha.hexdigest(),
        line_count=0,
        bad_json_lines=0,
        kind=kind,
        project_root_dir=pinfo["project_root_dir"],
        workflow_id=pinfo["workflow_id"],
    )

    types: dict[str, int] = {}
    tok = {"input": 0, "output": 0, "cache_read": 0, "cache_creation": 0}
    # ONE ASSISTANT MESSAGE IS WRITTEN AS SEVERAL RECORDS, and every one of them repeats the
    # COMPLETED `message.usage` block -- not a partial, not a delta, the same finished numbers.
    # So `+=` per record counts each message's tokens once per record. Measured 2026-08-29 over
    # all 1496 files in ~/.claude/projects: 174,060 assistant records carrying usage are 83,779
    # distinct messages, and priced through `budget/price_card.py` the naive sum reads $27,749.37
    # against a deduped $12,508.47 -- a 2.22x overstatement. `message.id` was present on 174,060
    # of 174,060 of those records, so the key is total and the fallbacks below never fired once.
    usage_seen: set[str] = set()
    usage_records = 0
    saw_usage = False
    timestamps: list[str] = []
    field_session_id: str | None = None
    field_agent_id: str | None = None
    cwd: str | None = None

    for rec, _raw in _iter_records(path):
        f.line_count += 1
        if rec is None:
            f.bad_json_lines += 1
            continue
        rtype = rec.get("type", "?")
        types[rtype] = types.get(rtype, 0) + 1

        if field_session_id is None and rec.get("sessionId"):
            field_session_id = rec["sessionId"]
        if field_agent_id is None and rec.get("agentId"):
            field_agent_id = rec["agentId"]
        if cwd is None and rec.get("cwd"):
            cwd = rec["cwd"]
        if f.harness_version is None and rec.get("version"):
            f.harness_version = rec["version"]
        if f.entrypoint is None and rec.get("entrypoint"):
            f.entrypoint = rec["entrypoint"]
        if f.git_branch is None and rec.get("gitBranch"):
            f.git_branch = rec["gitBranch"]
        if f.is_sidechain is None and isinstance(rec.get("isSidechain"), bool):
            f.is_sidechain = rec["isSidechain"]
        if rec.get("permissionMode"):
            f.permission_mode = rec["permissionMode"]
        if rtype == "ai-title" and rec.get("aiTitle"):
            f.ai_title = rec["aiTitle"]
        if rtype == "system" and rec.get("subtype") == "away_summary" and rec.get("content"):
            f.away_summary = rec["content"]

        ts = rec.get("timestamp")
        if isinstance(ts, str):
            timestamps.append(ts)

        if rtype == "user":
            f.turns_user += 1
            if f.first_user_text is None:
                txt = _text_of(rec.get("message"))
                if txt:
                    f.first_user_text = txt[:first_user_chars]
        elif rtype == "assistant":
            f.turns_assistant += 1
            msg = rec.get("message")
            if isinstance(msg, dict):
                # '<synthetic>' is the harness's placeholder on messages it generated itself
                # (interrupts, injected errors). It is not a model the session ran on, so it
                # never wins over a real id; it is only kept if nothing real ever appears.
                m = msg.get("model")
                if m and (f.model is None or (f.model == "<synthetic>" and m != "<synthetic>")):
                    f.model = m
                    f.model_source = "assistant-message"
                u = msg.get("usage")
                if isinstance(u, dict):
                    saw_usage = True
                    usage_records += 1
                    # `requestId` agreed with `message.id` on every record of the corpus and is
                    # the fallback. The line-number sentinel is the last resort and exists so an
                    # unkeyable record is counted ONCE rather than dropped: a key we cannot form
                    # is a reason not to dedup that record, never a reason to lose its tokens.
                    key = msg.get("id") or rec.get("requestId") or f"#line{f.line_count}"
                    if key not in usage_seen:
                        usage_seen.add(key)
                        tok["input"] += int(u.get("input_tokens") or 0)
                        tok["output"] += int(u.get("output_tokens") or 0)
                        tok["cache_read"] += int(u.get("cache_read_input_tokens") or 0)
                        tok["cache_creation"] += int(u.get("cache_creation_input_tokens") or 0)

    f.record_types = types

    if saw_usage:
        f.tokens_input = tok["input"]
        f.tokens_output = tok["output"]
        f.tokens_cache_read = tok["cache_read"]
        f.tokens_cache_creation = tok["cache_creation"]
        f.tokens_messages = len(usage_seen)
        f.tokens_usage_records = usage_records
        f.tokens_source = "message-usage-deduped"
    # cost_usd stays None on purpose: no Claude Code transcript record carries a USD figure.
    # Deriving one needs a price table keyed by model and date, which this lane does not own.
    # A null here means "not recorded", and must not be read as zero.

    if timestamps:
        timestamps.sort()
        f.started_at, f.ended_at = timestamps[0], timestamps[-1]
        f.time_source = "record-timestamps"

    # identity
    if kind == KIND_SUBAGENT:
        # Measured over all 740 files under subagents/: the `sessionId` field in a subagent
        # transcript carries the PARENT session's uuid, and the subagent's own id is
        # `agentId`. 733/733 agent-*.jsonl files agreed with the path on both.
        f.agent_id = pinfo["path_agent_id"] or field_agent_id
        f.parent_session_id = pinfo["path_parent_session_id"] or field_session_id
        from_path = pinfo["path_parent_session_id"]
        f.parent_source = ("path+field" if from_path and from_path == field_session_id
                           else "path" if from_path else "field" if field_session_id else None)
        f.session_id = None
        f.session_key = f"{f.parent_session_id}:{f.agent_id}" if f.parent_session_id and f.agent_id else None
    elif kind == KIND_MAIN:
        f.session_id = field_session_id
        # A main session's own uuid is also its filename; prefer the field, fall back to the name.
        if f.session_id is None:
            stem = os.path.basename(path)[: -len(".jsonl")]
            if UUID_RE.match(stem):
                f.session_id = stem
        f.session_key = f.session_id
    else:  # workflow journal
        f.parent_session_id = pinfo["path_parent_session_id"]
        f.parent_source = "path" if f.parent_session_id else None
        f.session_key = None

    # workdir
    if cwd:
        f.workdir, f.workdir_source = cwd, "record-cwd"
    elif f.project_root_dir:
        f.workdir, f.workdir_source = decode_project_dir(f.project_root_dir), "decoded-dirname"
    if cwd and f.project_root_dir:
        f.workdir_decode_matches_cwd = (decode_project_dir(f.project_root_dir) == cwd)

    return f


def walk(projects_root: str) -> list[str]:
    """Every transcript file under the projects root.

    Note for anyone extending this: on this host `find` is aliased to `bfs`, which rejects
    the leading-dash project-root directory names outright. Walk in Python.
    """
    out: list[str] = []
    for dirpath, _dirnames, filenames in os.walk(projects_root):
        for name in filenames:
            if name.endswith(".jsonl"):
                out.append(os.path.join(dirpath, name))
    return sorted(out)
