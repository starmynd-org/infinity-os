"""Read immutable Git objects through Git Bash; never traverse a moving checkout."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shlex
import subprocess

import yaml

GIT_BASH = "C:/Program Files/Git/bin/bash.exe"
SHA = re.compile(r"[0-9a-f]{40}\Z")


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def git(repo: str, *args: str, data: bytes | None = None) -> bytes:
    if not Path(repo).is_absolute():
        raise ValueError("repository must be an absolute path")
    command = shlex.join(["git", "-C", repo, *args])
    return subprocess.run([GIT_BASH, "-lc", command], input=data,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          check=True).stdout


class UniqueLoader(yaml.SafeLoader):
    """Duplicate YAML keys are malformed evidence, never last-write-wins."""


def unique_mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, (str, int, float, bool)) or key in result:
            raise ValueError("duplicate or non-scalar YAML key")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


def frontmatter(text: str) -> dict:
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        return {}
    try:
        end = lines.index("---", 1)
    except ValueError as exc:
        raise ValueError("unterminated frontmatter") from exc
    if end > 2000:
        raise ValueError("frontmatter exceeds 2000 lines")
    obj = yaml.load("\n".join(lines[1:end]), Loader=UniqueLoader)
    if not isinstance(obj, dict):
        raise ValueError("frontmatter must be an object")
    return obj


def eligible(repo_id: str, path: str) -> bool:
    parts = PurePosixPath(path).parts
    if not path.endswith(".md") or any(p in {"archive", "support", "_examples"} for p in parts):
        return False
    if repo_id == "brain":
        return parts[0] in {"knowledge", "entities", "workflows", "_system"}
    return (parts[0] in {"docs", "roles"} or
            path in {"README.md", "web/MUST-NOT-BUILD.md", "queue/README.md"})


@dataclass(frozen=True)
class Source:
    repo_id: str
    commit: str
    path: str
    blob: str
    raw: bytes

    def __post_init__(self):
        if self.repo_id not in {"brain", "runtime"} or not SHA.fullmatch(self.commit):
            raise ValueError("source needs a known repository and a full frozen SHA")
        if (not self.path or PurePosixPath(self.path).is_absolute() or
                any(p in {".", ".."} for p in self.path.split("/")) or
                any(c in self.path for c in "\\:#?\r\n\0")):
            raise ValueError("source path must be a safe repository-relative path")
        if not SHA.fullmatch(self.blob) or not isinstance(self.raw, bytes):
            raise ValueError("source needs a Git blob id and raw bytes")

    @property
    def key(self):
        return f"{self.repo_id}:{self.path}"

    def describe(self):
        return {"key": self.key, "repo": self.repo_id, "commit": self.commit,
                "path": self.path, "blob": self.blob, "sha256": digest(self.raw)}


def read_snapshot(repo_id: str, repo: str, commit: str) -> tuple[list[Source], dict]:
    if not SHA.fullmatch(commit):
        raise ValueError("freeze a full commit SHA before inventory")
    # ls-tree enumerates the commit object, never worktree files or symlink targets.
    entries = []
    for entry in git(repo, "ls-tree", "-rlz", commit).split(b"\0"):
        if not entry:
            continue
        meta, name = entry.split(b"\t", 1)
        mode, kind, blob, size = meta.decode("ascii").split()
        entries.append((mode, kind, blob, int(size) if size != "-" else 0,
                        name.decode("utf-8")))
    selected = [e for e in entries if e[0] in {"100644", "100755"} and
                eligible(repo_id, e[4])]
    if not selected:
        raise ValueError("zero candidate sources; inventory cannot claim success")
    if any(e[3] > 2_000_000 for e in selected):
        raise ValueError("source larger than bounded reader permits")
    raw_batch = git(repo, "cat-file", "--batch", data=
                    ("\n".join(e[2] for e in selected) + "\n").encode("ascii"))
    sources, offset = [], 0
    for mode, kind, blob, size, path in selected:
        end = raw_batch.index(b"\n", offset)
        actual_blob, actual_kind, actual_size = raw_batch[offset:end].decode("ascii").split()
        if (actual_blob, actual_kind, int(actual_size)) != (blob, "blob", size):
            raise ValueError("Git batch object identity/size mismatch")
        raw = raw_batch[end + 1:end + 1 + size]
        offset = end + 2 + size
        if len(raw) != size or raw_batch[offset - 1:offset] != b"\n":
            raise ValueError("truncated Git object stream")
        sources.append(Source(repo_id, commit, path, blob, raw))
    if offset != len(raw_batch):
        raise ValueError("unexpected trailing Git object bytes")
    return sources, {
        "repo": repo_id, "path": repo, "commit": commit,
        "denominator": len(entries), "predicate": "all entries from git ls-tree -rlz COMMIT",
        "regular_files": sum(e[0] in {"100644", "100755"} for e in entries),
        "markdown_files": sum(e[4].endswith(".md") for e in entries),
        "candidate_sources": len(sources),
        "candidate_predicate": "regular Markdown in the declared allowlist; no archive/support/_examples",
    }


def inventory(sources: list[Source]) -> dict:
    accepted, rejected, typed, edges = [], [], Counter(), []
    for source in sources:
        try:
            text = source.raw.decode("utf-8-sig")
            meta = frontmatter(text)
            for field in ("id", "type"):
                if field in meta and not isinstance(meta[field], str):
                    raise ValueError(f"{field} must be a string")
            declared = meta.get("edges", [])
            if not isinstance(declared, list):
                raise ValueError("edges must be an array")
            source_edges = []
            for edge in declared:
                if not isinstance(edge, dict) or not all(
                    isinstance(edge.get(k), str) and edge[k].strip() for k in ("target", "relation")
                ):
                    raise ValueError("edge needs nonempty target and relation")
                source_edges.append({"source": source.key, "target": edge["target"],
                                     "relation": edge["relation"]})
            record = source.describe() | {"node_id": meta.get("id"), "type": meta.get("type"),
                                        "lines": len(text.splitlines()), "declared_edges": len(source_edges)}
            accepted.append(record)
            edges.extend(source_edges)
            if meta.get("id") and meta.get("type"):
                typed[meta["type"]] += 1
        except (UnicodeError, ValueError, yaml.YAMLError, RecursionError) as exc:
            # Parser text can contain source content. Emit the class, not the document.
            rejected.append(source.describe() | {"reason": type(exc).__name__,
                            "detail": str(exc) if type(exc) is ValueError else "unparseable source; inspect the named frozen blob"})
    if not accepted:
        raise ValueError("zero readable sources; inventory cannot claim success")
    return {"denominator": len(sources), "predicate": "candidate sources parsed with UniqueLoader",
            "readable_sources": len(accepted), "refused_sources": len(rejected),
            "typed_node_denominator": sum(typed.values()), "node_types": dict(sorted(typed.items())),
            "declared_edge_occurrences": len(edges),
            "edge_predicate": "frontmatter edges with explicit nonempty target/relation; not resolved graph connections",
            "sources": accepted, "refusals": rejected}
