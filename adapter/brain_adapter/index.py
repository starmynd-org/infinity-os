"""The entity index: a brain path becomes a stable id, and back.

The id and alias discipline is `_system/stable-id-and-alias-rules.md` in the brain. This
module consumes those rules, it does not define them:

- Rule 1: every node-bearing file carries an `id` in frontmatter.
- Rule 4: the filename is normally the id with its type prefix stripped.
- Rule 5: when the id and the filename diverge, `aliases` MUST carry both names.
- Rule 6: the id is permanent; moving a file never changes it, and a rename adds the old
  filename to `aliases` so `[[old-name]]` keeps resolving.
- Rule 7: ids are unique repo-wide.

Rule 6 is why `produced_by` can hold an id at all: the path is not the handle, the id is.

Resolution never invents. A reference that does not land on exactly one node comes back
as `unresolved` or `ambiguous` with its candidates, and callers must handle that -- see
`Resolution.produced_by()`, which returns None rather than a plausible-looking id.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import frontmatter

# Directories that hold no brain nodes. `.git` matters for speed; the rest are tool state.
SKIP_DIRS = {
    ".git",
    ".obsidian",
    ".pytest_cache",
    "node_modules",
    "__pycache__",
    ".venv-kokoro",
}

# Frontmatter-exempt navigational files, per namespace-index-schema.md and
# agent-load-order.md. They are real retrieval targets but carry no `id`, so they are
# indexed by path only and never claim an id.
NAV_BASENAMES = {"INDEX.md", "README.md", "AGENTS.md", "CLAUDE.md", "llms.txt"}

_NORMALIZE = re.compile(r"[^a-z0-9]+")

STATUS_RESOLVED = "resolved"
STATUS_AMBIGUOUS = "ambiguous"
STATUS_UNRESOLVED = "unresolved"


def normalize(ref: str) -> str:
    """Loose form used only as the last matching pass. Never used to mint an id."""
    return _NORMALIZE.sub("-", ref.strip().lower()).strip("-")


@dataclass
class Node:
    entity_id: str | None
    path: str  # repo-relative, POSIX separators
    aliases: list[str] = field(default_factory=list)
    node_type: str | None = None
    namespace: str | None = None
    summary: str | None = None
    has_frontmatter: bool = False

    @property
    def stem(self) -> str:
        return self.path.rsplit("/", 1)[-1].rsplit(".", 1)[0]

    def to_dict(self) -> dict:
        return {
            "entity_id": self.entity_id,
            "path": self.path,
            "aliases": self.aliases,
            "type": self.node_type,
            "namespace": self.namespace,
            "summary": self.summary,
            "has_frontmatter": self.has_frontmatter,
        }

    @staticmethod
    def from_dict(d: dict) -> "Node":
        return Node(
            entity_id=d.get("entity_id"),
            path=d["path"],
            aliases=d.get("aliases") or [],
            node_type=d.get("type"),
            namespace=d.get("namespace"),
            summary=d.get("summary"),
            has_frontmatter=bool(d.get("has_frontmatter")),
        )


@dataclass
class Resolution:
    """The result of one resolution attempt. Carries its own failure, loudly."""

    ref: str
    status: str
    entity_id: str | None = None
    path: str | None = None
    matched_by: str | None = None
    candidates: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == STATUS_RESOLVED

    def produced_by(self) -> dict:
        """The value a caller writes into a `produced_by` column.

        On failure `produced_by` is None -- a real NULL, never a fabricated id -- and the
        raw reference is preserved beside it so the failure is visible in the row rather
        than only in a log nobody reads.
        """
        if self.ok:
            return {
                "produced_by": self.entity_id,
                "produced_by_ref": self.ref,
                "resolution_status": STATUS_RESOLVED,
            }
        return {
            "produced_by": None,
            "produced_by_ref": self.ref,
            "resolution_status": self.status,
            "resolution_candidates": [c["entity_id"] for c in self.candidates if c.get("entity_id")],
        }

    def to_dict(self) -> dict:
        d = {
            "ref": self.ref,
            "status": self.status,
            "entity_id": self.entity_id,
            "path": self.path,
            "matched_by": self.matched_by,
        }
        if self.candidates:
            d["candidates"] = self.candidates
        return d


class EntityIndex:
    """An in-memory index over one brain working tree.

    Reading canon is free (surface-boundary.md, truth plane). This class only reads.
    """

    CACHE_VERSION = 2

    def __init__(self, brain_root: Path, nodes: list[Node] | None = None):
        self.brain_root = Path(brain_root).resolve()
        self.nodes: list[Node] = nodes or []
        self.by_id: dict[str, Node] = {}
        self.by_path: dict[str, Node] = {}
        self.by_alias: dict[str, list[Node]] = {}
        self.by_stem: dict[str, list[Node]] = {}
        self.by_norm: dict[str, list[Node]] = {}
        self.duplicate_ids: dict[str, list[str]] = {}
        if self.nodes:
            self._reindex()

    # ---- build -----------------------------------------------------------------

    @classmethod
    def build(cls, brain_root: Path, cache_dir: Path | None = None, use_cache: bool = True) -> "EntityIndex":
        brain_root = Path(brain_root).resolve()
        sig = _tree_signature(brain_root)
        cache_path = None
        if cache_dir and use_cache:
            cache_dir = Path(cache_dir)
            cache_path = cache_dir / f"entity-index-{sig}.json"
            if cache_path.exists():
                try:
                    raw = json.loads(cache_path.read_text(encoding="utf-8"))
                    if raw.get("version") == cls.CACHE_VERSION:
                        return cls(brain_root, [Node.from_dict(n) for n in raw["nodes"]])
                except (OSError, ValueError, KeyError):
                    pass  # a bad cache is rebuilt, never trusted

        nodes = [_read_node(brain_root, rel) for rel in _list_files(brain_root)]
        idx = cls(brain_root, nodes)

        if cache_path is not None:
            try:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(
                    json.dumps({"version": cls.CACHE_VERSION, "signature": sig,
                                "nodes": [n.to_dict() for n in nodes]}),
                    encoding="utf-8",
                )
            except OSError:
                pass
        return idx

    def _reindex(self) -> None:
        for n in self.nodes:
            self.by_path[n.path] = n
            if n.entity_id:
                prior = self.by_id.get(n.entity_id)
                if prior is not None:
                    self.duplicate_ids.setdefault(n.entity_id, [prior.path]).append(n.path)
                    # Same id, several homes: keep the canonical one, not the first seen.
                    self.by_id[n.entity_id] = _canonical_home([prior, n])
                else:
                    self.by_id[n.entity_id] = n
            for a in n.aliases:
                self.by_alias.setdefault(a, []).append(n)
            self.by_stem.setdefault(n.stem, []).append(n)
            keys = {normalize(n.stem)}
            if n.entity_id:
                keys.add(normalize(n.entity_id))
            for a in n.aliases:
                keys.add(normalize(a))
            for k in keys:
                if k:
                    self.by_norm.setdefault(k, []).append(n)

    # ---- resolve ---------------------------------------------------------------

    def resolve(self, ref: str) -> Resolution:
        """Turn a reference into a stable id. Passes run most-exact first."""
        raw = (ref or "").strip()
        if not raw:
            return Resolution(ref=ref or "", status=STATUS_UNRESOLVED, matched_by="empty-ref")

        # `[[wikilink]]` and `[[link|display]]` are how the brain writes references.
        cleaned = raw
        if cleaned.startswith("[[") and cleaned.endswith("]]"):
            cleaned = cleaned[2:-2]
        cleaned = cleaned.split("|", 1)[0].split("#", 1)[0].strip()

        # 1. exact id
        node = self.by_id.get(cleaned)
        if node:
            return self._hit(raw, node, "id")

        # 2. exact repo-relative path
        for cand in (cleaned, cleaned + ".md", cleaned.rstrip("/") + "/INDEX.md"):
            cand = cand.lstrip("./")
            node = self.by_path.get(cand)
            if node:
                return self._hit(raw, node, "path")

        # An absolute path inside the brain is a path reference too.
        if cleaned.startswith("/"):
            try:
                rel = Path(cleaned).resolve().relative_to(self.brain_root).as_posix()
                node = self.by_path.get(rel)
                if node:
                    return self._hit(raw, node, "path")
            except (ValueError, OSError):
                pass

        # 3. alias (Rule 5 makes this the rename-survival path)
        hits = self.by_alias.get(cleaned)
        if hits:
            return self._one_or_ambiguous(raw, _dedupe(hits), "alias")

        # 4. filename stem (Rule 4: filename is the id minus its type prefix)
        hits = self.by_stem.get(cleaned)
        if hits:
            return self._one_or_ambiguous(raw, _dedupe(hits), "filename")

        # 5. normalized last pass. Loose matching, still never invents an id.
        hits = self.by_norm.get(normalize(cleaned))
        if hits:
            return self._one_or_ambiguous(raw, _dedupe(hits), "normalized")

        return Resolution(ref=raw, status=STATUS_UNRESOLVED, matched_by=None)

    def reverse(self, entity_id: str) -> Resolution:
        """The inverse: a stable id becomes the path it currently lives at."""
        node = self.by_id.get((entity_id or "").strip())
        if node:
            return self._hit(entity_id, node, "id")
        return Resolution(ref=entity_id or "", status=STATUS_UNRESOLVED)

    def _hit(self, ref: str, node: Node, matched_by: str) -> Resolution:
        return Resolution(
            ref=ref,
            status=STATUS_RESOLVED,
            entity_id=node.entity_id,
            path=node.path,
            matched_by=matched_by,
        )

    def _one_or_ambiguous(self, ref: str, hits: list[Node], matched_by: str) -> Resolution:
        """Collapse only what is genuinely one entity. Never narrow by preference.

        Several files may carry the SAME id: `sync-adapters.sh` mirrors `entities/` into
        `.claude/` and `.codex/`, and `tools/*/overlay/` shadows real nodes. Those are one
        entity with several homes, so canonical-home precedence picks the real one.

        Several files matching the same NAME with different ids are several entities, and
        collapsing them by preferring whichever happens to carry an id is how
        `[[agent-load-order]]` silently resolves to another namespace's copy. That is the
        confidently-wrong failure, so it returns ambiguous instead.
        """
        ids = {h.entity_id for h in hits}
        if len(ids) == 1 and None not in ids:
            return self._hit(ref, _canonical_home(hits), matched_by)
        if len(hits) == 1:
            node = hits[0]
            if not node.entity_id:
                # A real file carrying no id. `_system/` rule files, `INDEX.md` and
                # `canon/agent-load-order.md` are validator-exempt by design. The path is
                # a truthful answer; an id would be an invention.
                return Resolution(
                    ref=ref, status=STATUS_UNRESOLVED, path=node.path,
                    matched_by=f"{matched_by}-no-id",
                    candidates=[node.to_dict()],
                )
            return self._hit(ref, node, matched_by)
        return Resolution(
            ref=ref,
            status=STATUS_AMBIGUOUS,
            matched_by=matched_by,
            candidates=[h.to_dict() for h in hits],
        )


# Homes that hold a generated copy of a node that lives somewhere else. Ordered worst
# first; a node outside all of these is the canonical home. `sync-adapters.sh` produces
# the harness mirrors, and `tools/*/overlay/` is the starter-export shadow.
_MIRROR_HOMES = (".codex/", ".claude/", "tools/")


def _canonical_home(nodes: list[Node]) -> Node:
    """Of several files carrying the SAME id, the one that is not a generated copy."""
    def rank(n: Node) -> tuple[int, str]:
        for i, prefix in enumerate(_MIRROR_HOMES):
            if n.path.startswith(prefix):
                return (len(_MIRROR_HOMES) - i, n.path)
        return (0, n.path)

    return min(nodes, key=rank)


def _dedupe(nodes: list[Node]) -> list[Node]:
    seen, out = set(), []
    for n in nodes:
        if n.path not in seen:
            seen.add(n.path)
            out.append(n)
    return out


def _list_files(brain_root: Path) -> list[str]:
    """Markdown on disk, listed through git so the ordering is deterministic.

    `--cached --others --exclude-standard` is deliberate. Tracked-only would miss a node
    that has been authored but not committed, and would keep listing a path that has been
    moved on disk but not yet staged -- which is exactly the state a rename passes
    through. The `exists()` filter drops the stale cached entries.
    """
    try:
        res = subprocess.run(
            ["git", "-C", str(brain_root), "ls-files", "-z",
             "--cached", "--others", "--exclude-standard", "*.md"],
            capture_output=True, check=True, timeout=180,
        )
        seen, files = set(), []
        for p in res.stdout.decode("utf-8", "replace").split("\0"):
            if p and p not in seen and (brain_root / p).is_file():
                seen.add(p)
                files.append(p)
        if files:
            return files
    except (subprocess.SubprocessError, OSError):
        pass
    out = []
    for dirpath, dirnames, filenames in os.walk(brain_root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if fn.endswith(".md"):
                out.append(Path(dirpath, fn).relative_to(brain_root).as_posix())
    return sorted(out)


def _read_node(brain_root: Path, rel: str) -> Node:
    fm = frontmatter.read(brain_root / rel)
    aliases = list(fm.aliases)
    if fm.id and fm.id not in aliases:
        aliases.append(fm.id)
    return Node(
        entity_id=fm.id,
        path=rel,
        aliases=aliases,
        node_type=fm.get("type"),
        namespace=fm.get("namespace"),
        summary=fm.get("summary"),
        has_frontmatter=fm.present,
    )


def _tree_signature(brain_root: Path) -> str:
    """HEAD plus the dirty-file list. A dirty tree gets its own cache key."""
    parts = []
    for cmd in (["rev-parse", "HEAD"], ["status", "--porcelain"]):
        try:
            res = subprocess.run(
                ["git", "-C", str(brain_root)] + cmd,
                capture_output=True, text=True, timeout=120,
            )
            parts.append(res.stdout)
        except (subprocess.SubprocessError, OSError):
            parts.append("")
    return hashlib.sha1("\n".join(parts).encode("utf-8")).hexdigest()[:16]
