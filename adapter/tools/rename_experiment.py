#!/usr/bin/env python3
"""Directly test what survives a rename, by renaming real nodes in a scratch worktree.

History gave a small sample (9 detected renames, 3 with ids on both sides). This runs the
experiment instead of inferring it: take N real nodes across namespaces, move each to a
new directory AND a new filename, rebuild the index over the mutated tree, and ask three
questions of every node:

  1. does `resolve(id)` still return it?              (id-as-handle)
  2. does `resolve(old-filename)` still return it?    (Rule 5/6 alias survival)
  3. does `reverse(id)` return the NEW path?          (the inverse tracks the move)

Run this only against a scratch worktree. It mutates files. It restores them at the end
and verifies the restore with `git status`.

Usage: python3 tools/rename_experiment.py --worktree /path/to/scratch-worktree
"""

from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from brain_adapter.index import EntityIndex  # noqa: E402


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=False).stdout


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--worktree", required=True)
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--json")
    args = ap.parse_args()

    wt = Path(args.worktree).resolve()
    branch = git(wt, "rev-parse", "--abbrev-ref", "HEAD").strip()
    if branch in ("main", "master"):
        print(f"refusing to mutate branch {branch!r}", file=sys.stderr)
        return 2
    dirty = [l for l in git(wt, "status", "--porcelain").splitlines() if l.strip()]
    if dirty:
        print(f"refusing: worktree is not clean ({len(dirty)} entries)", file=sys.stderr)
        return 2

    before = EntityIndex.build(wt, cache_dir=None, use_cache=False)

    # Sample across namespaces so the result is not one namespace's habit.
    pool = [n for n in before.nodes
            if n.entity_id and n.path.startswith("knowledge/") and "/archive/" not in n.path]
    by_ns: dict[str, list] = {}
    for n in pool:
        by_ns.setdefault(n.path.split("/")[1], []).append(n)
    rng = random.Random(20260816)
    chosen = []
    for ns in sorted(by_ns):
        rng.shuffle(by_ns[ns])
        chosen.extend(by_ns[ns][:1])
    rng.shuffle(chosen)
    chosen = chosen[: args.n]

    staging = wt / "knowledge" / "_renamed_experiment"
    staging.mkdir(parents=True, exist_ok=True)
    moves = []
    for i, n in enumerate(chosen):
        src = wt / n.path
        dst = staging / f"moved-{i:03d}-{n.stem}-renamed.md"
        src.rename(dst)
        moves.append((n, dst.relative_to(wt).as_posix()))

    after = EntityIndex.build(wt, cache_dir=None, use_cache=False)

    rows = []
    for n, newpath in moves:
        by_id = after.resolve(n.entity_id)
        by_old_name = after.resolve(n.stem)
        rev = after.reverse(n.entity_id)
        rows.append({
            "id": n.entity_id,
            "old_path": n.path,
            "new_path": newpath,
            "resolve_by_id_still_works": by_id.ok and by_id.path == newpath,
            "resolve_by_old_filename_status": by_old_name.status,
            "resolve_by_old_filename_lands_here": by_old_name.ok and by_old_name.path == newpath,
            "old_filename_in_aliases": n.stem in n.aliases,
            "reverse_returns_new_path": rev.ok and rev.path == newpath,
        })

    # Restore. The worktree must come back exactly as it was.
    for n, newpath in moves:
        (wt / newpath).rename(wt / n.path)
    try:
        staging.rmdir()
    except OSError:
        pass
    restored_clean = not [l for l in git(wt, "status", "--porcelain").splitlines() if l.strip()]

    n_total = len(rows)
    report = {
        "worktree": str(wt),
        "branch": branch,
        "nodes_renamed": n_total,
        "namespaces_covered": len({r["old_path"].split("/")[1] for r in rows}),
        "resolve_by_id_survived": sum(1 for r in rows if r["resolve_by_id_still_works"]),
        "reverse_tracked_the_move": sum(1 for r in rows if r["reverse_returns_new_path"]),
        "old_filename_still_resolves_here": sum(1 for r in rows if r["resolve_by_old_filename_lands_here"]),
        "old_filename_in_aliases": sum(1 for r in rows if r["old_filename_in_aliases"]),
        "old_filename_status_breakdown": _tally(r["resolve_by_old_filename_status"] for r in rows),
        "worktree_restored_clean": restored_clean,
        "rows": rows,
    }
    text = json.dumps(report, indent=2)
    if args.json:
        Path(args.json).write_text(text, encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "rows"}, indent=2))
    return 0 if restored_clean else 1


def _tally(items) -> dict:
    out: dict[str, int] = {}
    for i in items:
        out[i] = out.get(i, 0) + 1
    return out


if __name__ == "__main__":
    raise SystemExit(main())
