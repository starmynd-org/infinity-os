#!/usr/bin/env python3
"""Measure whether a resolved id survives a file rename, against real git history.

This answers the D-CROSSTALK slot 3 question with evidence rather than by quoting
Rule 6 at it. Three independent tests:

  A. git-detected renames. For each `R` record in history, read the `id` frontmatter of
     the old blob and the new blob and compare them.
  B. moves git recorded as delete + add. Rename detection misses a move whose content
     also changed a lot. For every .md path ever deleted, read the id it carried at the
     moment of deletion and ask whether that id lives somewhere in the tree today, at a
     different path.
  C. the alias mechanism in the current tree. Rule 5 says a file whose id does not match
     its filename MUST alias both names. Measure compliance, because that is what keeps
     an inbound `[[old-name]]` resolving after a rename.

Usage: python3 tools/rename_stability.py [--brain PATH] [--json OUT]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from brain_adapter import config, frontmatter  # noqa: E402
from brain_adapter.index import EntityIndex  # noqa: E402

ID_RE = re.compile(rb'^id:[ \t]*"?([^"\n\r]+?)"?[ \t]*$', re.MULTILINE)


def git(brain: Path, *args: str, binary: bool = False):
    res = subprocess.run(["git", "-C", str(brain), *args], capture_output=True, check=False)
    if binary:
        return res.stdout
    return res.stdout.decode("utf-8", "replace")


def blob_id(brain: Path, rev: str, path: str) -> str | None:
    """The `id:` frontmatter of one blob, read straight out of the object database."""
    raw = git(brain, "show", f"{rev}:{path}", binary=True)
    if not raw.startswith(b"---"):
        return None
    end = raw.find(b"\n---", 3)
    head = raw[:end] if end > 0 else raw[:4096]
    m = ID_RE.search(head)
    return m.group(1).decode("utf-8", "replace").strip() if m else None


def test_a(brain: Path) -> dict:
    """git-detected renames: id before vs id after."""
    out = git(brain, "log", "--all", "-M30%", "--diff-filter=R",
              "--name-status", "--format=COMMIT %H", "--", "*.md")
    commit, rows = None, []
    for line in out.splitlines():
        if line.startswith("COMMIT "):
            commit = line.split()[1]
        elif line.startswith("R") and "\t" in line:
            parts = line.split("\t")
            if len(parts) >= 3:
                rows.append((commit, parts[1], parts[2]))

    results = []
    for commit, old, new in rows:
        before = blob_id(brain, f"{commit}^", old)
        after = blob_id(brain, commit, new)
        results.append({
            "commit": commit[:12],
            "old_path": old,
            "new_path": new,
            "basename_changed": old.rsplit("/", 1)[-1] != new.rsplit("/", 1)[-1],
            "id_before": before,
            "id_after": after,
            "id_stable": before is not None and before == after,
            "both_have_id": before is not None and after is not None,
        })
    with_id = [r for r in results if r["both_have_id"]]
    return {
        "renames_found": len(results),
        "renames_with_id_on_both_sides": len(with_id),
        "id_stable": sum(1 for r in with_id if r["id_stable"]),
        "id_changed": sum(1 for r in with_id if not r["id_stable"]),
        "basename_changing": sum(1 for r in results if r["basename_changed"]),
        "rows": results,
    }


def test_b(brain: Path, idx: EntityIndex, limit: int | None = None) -> dict:
    """Moves git recorded as delete + add: does the id live elsewhere today?"""
    out = git(brain, "log", "--all", "--diff-filter=D", "--name-status",
              "--format=COMMIT %H", "--", "*.md")
    commit, deletions = None, []
    seen = set()
    for line in out.splitlines():
        if line.startswith("COMMIT "):
            commit = line.split()[1]
        elif line.startswith("D\t"):
            path = line.split("\t", 1)[1]
            key = (commit, path)
            if key not in seen:
                seen.add(key)
                deletions.append((commit, path))
    if limit:
        deletions = deletions[:limit]

    survived_moved, survived_same_path, gone, no_id = [], 0, 0, 0
    for commit, path in deletions:
        # The blob as it stood in the parent of the deleting commit.
        eid = blob_id(brain, f"{commit}^", path)
        if not eid:
            no_id += 1
            continue
        node = idx.by_id.get(eid)
        if node is None:
            gone += 1
        elif node.path == path:
            survived_same_path += 1  # deleted on one branch, alive on another
        else:
            survived_moved.append({"id": eid, "was": path, "now": node.path,
                                   "deleted_in": commit[:12],
                                   "basename_changed": path.rsplit("/", 1)[-1] != node.path.rsplit("/", 1)[-1]})
    return {
        "deletion_records_examined": len(deletions),
        "deleted_blobs_carrying_an_id": len(deletions) - no_id,
        "id_alive_today_at_a_different_path": len(survived_moved),
        "id_alive_today_at_the_same_path": survived_same_path,
        "id_not_in_the_tree_today": gone,
        "of_the_moved_how_many_changed_basename": sum(1 for r in survived_moved if r["basename_changed"]),
        "sample": survived_moved[:25],
    }


def test_c(brain: Path, idx: EntityIndex) -> dict:
    """Rule 5 alias discipline in the current tree."""
    PREFIXES = ("knowledge-", "memory-", "data-", "output-", "project-", "metric-",
                "skill-", "agent-", "workflow-", "command-", "rule-", "namespace-",
                "intake-", "department-", "decision-", "doc-", "tool-", "receipt-")
    diverged, aliased, not_aliased = 0, 0, []
    for n in idx.nodes:
        if not n.entity_id:
            continue
        tail = n.entity_id
        for p in PREFIXES:
            if tail.startswith(p):
                tail = tail[len(p):]
                break
        if tail == n.stem:
            continue
        diverged += 1
        if n.stem in n.aliases:
            aliased += 1
        else:
            not_aliased.append({"id": n.entity_id, "path": n.path, "aliases": n.aliases[:4]})
    return {
        "id_bearing_nodes": sum(1 for n in idx.nodes if n.entity_id),
        "id_tail_differs_from_filename": diverged,
        "and_filename_is_in_aliases": aliased,
        "and_filename_is_not_in_aliases": len(not_aliased),
        "alias_compliance_pct": round(100.0 * aliased / diverged, 1) if diverged else None,
        "sample_noncompliant": not_aliased[:10],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--brain")
    ap.add_argument("--json")
    ap.add_argument("--limit-deletions", type=int, default=None)
    args = ap.parse_args()

    brain = config.brain_root(args.brain)
    idx = EntityIndex.build(brain, config.cache_dir())

    report = {
        "brain": str(brain),
        "head": git(brain, "rev-parse", "HEAD").strip()[:12],
        "commits_in_history": int(git(brain, "rev-list", "--count", "HEAD").strip() or 0),
        "test_a_git_detected_renames": test_a(brain),
        "test_b_delete_add_moves": test_b(brain, idx, args.limit_deletions),
        "test_c_alias_discipline": test_c(brain, idx),
    }
    text = json.dumps(report, indent=2)
    if args.json:
        Path(args.json).write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
