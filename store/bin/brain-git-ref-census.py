#!/usr/bin/env python3
"""The attribution census: of every `git_ref` in the store, how many are still knowledge?

    "Losing the entire store must cost live queue position and history, and ZERO KNOWLEDGE."

`brain-receipt-reconcile.py` answers ONE question and answers it as a gate: is this sha an
ancestor of a durable ref IN THE BRAIN? That is the right question for a gate and it is not the
whole question, because it produces a single undifferentiated number -- "20 LOST" -- that mixes
three findings needing three different responses:

    a scratch branch deleted after a proof      expected, and costs no knowledge
    a commit that was never pushed              recoverable, the object is still on this machine
    a pointer at nothing anywhere               knowledge that is actually gone

This walks the same corpus and separates them, on the two axes the invariant actually has:

  AXIS 1, THE POINTER.  Does the sha resolve to a real commit object, in a NAMED repository,
  reachable from a ref that still exists? Searched across every git repository under the search
  roots, not just the brain -- because `receipt book --repo X` accepted any repository, so
  "not in the brain" and "nowhere" are different facts and the reconciler cannot tell them apart.

  AXIS 2, THE ARTIFACT.  Every receipt's `detail` ends in the canon path it claims to have
  produced, e.g. `[departments/warner-sandbox/receipts/2026-08-16-d9-rerun-acceptance.md]`. The
  invariant is about KNOWLEDGE, not about pointers: a dangling sha whose file is on `main` costs
  nothing, and a resolvable sha whose file exists nowhere durable is not evidence of anything.
  So the path is checked independently of the sha.

A ref is only knowledge-durable when BOTH axes land in the durable repo. Reporting axis 1 alone
overstates the health of a sha that resolves on a scratch branch, and understates nothing --
which is why this exists beside the reconciler and does not replace it.

READ-ONLY by construction: `store.session.read()` for the store, and `git cat-file`,
`git for-each-ref`, `git merge-base`, `git log` for the repositories. Nothing here writes.

    store/bin/brain-git-ref-census.py [--repo BRAIN] [--search-root DIR]... [--json]

Exit 0 when the census was produced, 3 when it could not be (the store or the brain repo could
not be read). It is a MEASUREMENT, not a gate: the gate is the reconciler, and giving this one an
opinionated exit code would create a second gate that can disagree with the first.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))

from store import session  # noqa: E402  (path must be set first)
from store.durability_tier import (  # noqa: E402
    RemoteUnreachable, measure as measure_tier, policy_ledger_refs,
)

DEFAULT_BRAIN = os.environ.get(
    "BRAIN_ROOT", "/mnt/c/Users/you/repos/your-brain"
)

#: Where to look for a commit that is not in the brain. Deliberately wide: the whole point is to
#: distinguish "gone" from "somewhere else", and a narrow search would report every foreign sha as
#: gone, which is the confidently-wrong zero this file exists to prevent.
DEFAULT_SEARCH_ROOTS = ("/mnt/c/Users/you/repos", "/home/you/.cache", "/tmp")

#: Refs an operator does not delete in the normal course of work, checked per repository. `master`
#: is included for the search set because a foreign repo may not use `main`; the brain's own
#: durable set is the reconciler's, and the two agreeing on the brain is checked, not assumed.
#:
#: The floor, extended per repository by `policy/durable-refs.json` (task 0349). It has to be
#: extended somewhere: `promote()` refuses `main` unconditionally, so a durable set of `main`
#: alone means no legal booking destination is durable and the census can only ever report zero.
DURABLE_REF_NAMES = ("main", "origin/main", "master", "origin/master")

#: The one declaration, read as data by three tools. Parsed here with this file's own reader:
#: `store/` imports no adapter code, so the three agreeing stays checkable rather than inherited.
POLICY_PATH = Path(os.environ.get("BRAIN_DURABLE_POLICY")
                   or _REPO_ROOT / "policy" / "durable-refs.json")

GIT_REF_TABLES = ("receipt", "touch")

#: A receipt's detail ends in the canon path it claims to have produced.
DETAIL_PATH = re.compile(r"\[([^\[\]]+\.md)\]\s*$")


class Unmeasurable(RuntimeError):
    """The store or the brain repository could not be read. Exit 3, never a green census."""


def _refs_summary(refs: list[str], keep: int = 4) -> str:
    """The durable set, printed before any count that uses it, collapsed but never truncated."""
    singles = [r for r in refs if not r.startswith("refs/")]
    grouped: dict[str, int] = {}
    for r in refs:
        if r.startswith("refs/"):
            k = "/".join(r.split("/")[:-1]) + "/*"
            grouped[k] = grouped.get(k, 0) + 1
    parts = singles[:keep] + ([f"(+{len(singles) - keep} more)"] if len(singles) > keep else [])
    return ", ".join(parts + [f"{k} ({n} refs)" for k, n in sorted(grouped.items())]) or "(none)"


def git(repo, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
    )


def find_repos(roots: tuple[str, ...], depth: int = 4) -> list[Path]:
    """Every git repository under the search roots, deduplicated and sorted.

    `.git` may be a directory (a clone) or a file (a linked worktree). Both are searched: three of
    the four scratch branches in this corpus live in linked worktrees under ~/.cache, and skipping
    the file form would report their commits as gone while they sit on disk.
    """
    found: set[Path] = set()
    for root in roots:
        if not Path(root).is_dir():
            continue
        p = subprocess.run(
            ["find", root, "-maxdepth", str(depth), "-name", ".git"],
            capture_output=True, text=True, check=False,
        )
        for line in p.stdout.splitlines():
            line = line.strip()
            if line:
                found.add(Path(line).parent)
    return sorted(found)


def policy_doc() -> dict:
    try:
        return json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def declared_refs_of(repo: Path, doc: dict) -> tuple[list[str], dict | None]:
    """What `policy/durable-refs.json` declares for THIS repository, expanded against disk.

    Keyed by repo identity (root commits), which is the only key that survives the repository
    moving -- and every one of the 20 dangling refs in the first census came from a path that no
    longer exists. A repository the policy does not name gets the `default` entry, so a fixture
    repo is measured against the same rule as the brain instead of against nothing.
    """
    if not doc:
        return [], None
    entry = (doc.get("repos") or {}).get(repo_identity(repo) or "") or doc.get("default") or {}
    out: list[str] = []
    for pat in list(entry.get("canon_refs") or []) + list(entry.get("ledger_refs") or []):
        if pat.endswith("*"):
            out += [ln.strip() for ln in
                    git(repo, "for-each-ref", "--format=%(refname)", pat).stdout.splitlines()
                    if ln.strip()]
        else:
            out.append(pat)
    return out, entry.get("tombstones")


def durable_refs_of(repo: Path, declared: list[str] | None = None) -> list[str]:
    wanted = list(dict.fromkeys(list(DURABLE_REF_NAMES) + list(declared or [])))
    return [
        r for r in wanted
        if git(repo, "rev-parse", "--verify", "--quiet", f"{r}^{{commit}}").returncode == 0
    ]


def tombstones_of(repo: Path, spec: dict | None) -> tuple[dict[str, dict], list[str]]:
    """Recorded-gone refs, read from a durable ref, plus any that cover a commit still here.

    Same two rules the reconciler enforces, implemented separately for the same reason the two
    implement `classify` separately: two checkers that agree is a fact, and one importing the
    other is an assumption. A tombstone is never allowed to change what AXIS 1 says about a sha;
    it is a fourth verdict, applied only where axis 1 already said GONE.
    """
    if not spec:
        return {}, []
    p = git(repo, "show", f"{spec['ref']}:{spec['path']}")
    if p.returncode != 0:
        return {}, []
    try:
        doc = json.loads(p.stdout)
    except json.JSONDecodeError:
        return {}, []
    stones = {(r.get("git_ref") or "").strip(): r for r in (doc.get("tombstoned") or [])
              if (r.get("git_ref") or "").strip() and r.get("reason") and r.get("assessed_loss")}
    live = sorted(s for s in stones
                  if git(repo, "cat-file", "-e", f"{s}^{{commit}}").returncode == 0)
    return stones, live


def repo_identity(repo: Path) -> str | None:
    """Root commits of `HEAD`, sorted and comma-joined. Migration 19's identity, recomputed.

    Same construction as `store_join.repo_identity` and `brain-receipt-reconcile.this_repo_
    identity`, and deliberately recomputed rather than imported, so that three independent
    implementations agreeing is a checkable fact rather than an assumption travelling by import.
    """
    p = git(repo, "rev-list", "--max-parents=0", "HEAD")
    if p.returncode != 0:
        return None
    roots = sorted({ln.strip() for ln in p.stdout.splitlines() if ln.strip()})
    return ",".join(roots) or None


#: What axis 1 can say about one sha, worst to best.
GONE, UNREFERENCED, FRAGILE, DURABLE = "GONE", "UNREFERENCED", "FRAGILE", "DURABLE"
_RANK = {GONE: 0, UNREFERENCED: 1, FRAGILE: 2, DURABLE: 3}


def locate(sha: str, repos: list[Path], durable: dict[str, list[str]]) -> list[dict]:
    """Every repository on this machine that holds this commit object, and how firmly.

    UNREFERENCED is kept separate from FRAGILE on purpose. An object no ref contains is not
    "reachable from a branch that still exists" -- it is alive only until the next `git gc`, and
    calling that FRAGILE would let a reflog-only object read the same as one on a named branch.
    """
    sightings = []
    for repo in repos:
        if git(repo, "cat-file", "-e", f"{sha}^{{commit}}").returncode != 0:
            continue
        state, refs = UNREFERENCED, []
        for ref in durable[str(repo)]:
            if git(repo, "merge-base", "--is-ancestor", sha, ref).returncode == 0:
                state, refs = DURABLE, [ref]
                break
        if state != DURABLE:
            out = git(repo, "for-each-ref", "--contains", sha, "--format=%(refname:short)").stdout
            refs = sorted({ln.strip() for ln in out.splitlines() if ln.strip()})
            if refs:
                state = FRAGILE
        subj = git(repo, "log", "-1", "--format=%s", sha).stdout.strip()
        sightings.append({"repo": str(repo), "state": state, "refs": refs, "subject": subj})
    return sorted(sightings, key=lambda s: -_RANK[s["state"]])


def artifact_state(brain: Path, path: str | None, durable: list[str] | None = None) -> dict:
    """Axis 2: where the file the receipt claims to have produced actually is.

    `--all` is history under every ref, not just `main`. A file that exists only under `--all` is
    on a scratch branch and dies with it, which is the same fragility as axis 1 and has to be
    reported as such rather than folded into "it exists".

    ON-DURABLE sits between the two and is the state task 0349 created: the file is in the tree of
    a durable ref that is not `main`, so `git show <ref>:<path>` returns its bytes with every
    scratch branch, worktree and /tmp gone. It is deliberately NOT the same word as ON-MAIN. The
    file is not on `main`; a human merging the ledger is what would put it there, and reporting the
    two as one state would be this census claiming a human decision that has not happened.
    """
    if not path:
        return {"path": None, "state": "NO-PATH-IN-DETAIL"}
    on_main = git(brain, "cat-file", "-e", f"main:{path}").returncode == 0
    # `cat-file -e <ref>:<path>`, not `git log -- <path>`. Two reasons, and the second is why this
    # was rewritten mid-task: a tree lookup is O(1) per ref where a pathspec walk is the whole
    # history of every ref named, which for a path that is in NONE of them (the tombstoned six)
    # walks all of main for each one and took the census from 3 minutes past 10. And it is the
    # stronger statement: the file is IN THE TREE that ref points at, not merely somewhere behind
    # it, so `git show <ref>:<path>` returns its bytes with no archaeology.
    others = [r for r in (durable or []) if r not in ("main", "origin/main")]
    durable_hits = [r for r in others
                    if git(brain, "cat-file", "-e", f"{r}:{path}").returncode == 0]
    commits_all = len([ln for ln in git(brain, "log", "--all", "--format=%H", "--", path)
                       .stdout.splitlines() if ln.strip()])
    in_worktree = (brain / path).exists()
    if on_main:
        state = "ON-MAIN"
    elif durable_hits:
        state = "ON-DURABLE"
    elif commits_all:
        state = "SCRATCH-ONLY"
    elif in_worktree:
        state = "UNCOMMITTED"
    else:
        state = "ABSENT"
    return {"path": path, "state": state, "on_main": on_main,
            "durable_refs_holding_it": durable_hits,
            "commits_under_any_ref": commits_all, "in_worktree": in_worktree}


def classify(axis1: str, sightings: list[dict], artifact: dict, brain: str,
             tombstoned: dict | None = None) -> tuple[str, str]:
    """The three-way distinction the brief asks for, derived from evidence rather than from story.

    The categories are about the COST of the dangle, which is why the artifact axis participates:
    a sha that resolves nowhere but whose file is on `main` costs no knowledge, and no amount of
    pointer archaeology changes that.
    """
    if artifact["state"] == "ON-MAIN":
        return ("ARTIFACT-DURABLE",
                "the sha does not resolve in the brain, but the file the receipt claims to have "
                "produced is on main. The pointer is broken; no knowledge is lost.")
    if axis1 == DURABLE:
        return ("DURABLE", "ancestor of a durable ref.")
    if axis1 == FRAGILE:
        where = ", ".join(f"{s['repo']}::{','.join(s['refs'])}" for s in sightings
                          if s["state"] == FRAGILE)
        if any(s["repo"] == brain for s in sightings):
            return ("FRAGILE-IN-BRAIN",
                    f"object is in the brain, reachable only from {where}. One `git branch -D` "
                    f"plus a gc from GONE.")
        return ("RECOVERABLE-ELSEWHERE",
                f"never reached the brain, but the object is still on this machine at {where}. "
                f"Recoverable today by fetching that repository; gone when it is deleted.")
    if axis1 == UNREFERENCED:
        where = ", ".join(s["repo"] for s in sightings)
        return ("RECOVERABLE-UNREFERENCED",
                f"the object exists in {where} but no ref contains it. Alive until the next gc.")
    if tombstoned:
        return ("TOMBSTONED",
                f"no repository under the search roots holds this commit, and the operation has "
                f"RECORDED that on a durable ref rather than leaving it for the next census: "
                f"{tombstoned.get('assessed_loss', '')} It is not counted as live lineage and it "
                f"is not counted as durable either; a dead pointer written down is still dead.")
    return ("GONE",
            "no repository under the search roots holds this commit, and the file it names is "
            "not in the brain under any ref. The evidence this receipt points at is gone.")


def collect_rows() -> list[dict]:
    rows: list[dict] = []
    try:
        with session.read("owner") as s:
            attributable = {
                r["table_name"] for r in s.query(
                    "SELECT DISTINCT table_name FROM information_schema.columns "
                    "WHERE table_schema='brain' AND column_name='git_repo'")
            }
            for t in GIT_REF_TABLES:
                extra = ", git_repo, git_repo_ref" if t in attributable else ""
                detail = ", detail, booked_by, booked_at" if t == "receipt" else ""
                for r in s.query(
                    f"SELECT id, git_ref{extra}{detail} FROM brain.{t} "
                    f"WHERE git_ref IS NOT NULL ORDER BY id"
                ):
                    rows.append({
                        "table": t, "id": r["id"], "git_ref": r["git_ref"],
                        "git_repo": r.get("git_repo"), "git_repo_ref": r.get("git_repo_ref"),
                        "detail": r.get("detail"), "booked_by": r.get("booked_by"),
                        "booked_at": str(r["booked_at"]) if r.get("booked_at") else None,
                    })
    except Exception as exc:
        raise Unmeasurable(f"could not read the store: {exc.__class__.__name__}: {exc}") from None
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default=DEFAULT_BRAIN, help="the brain repository (default $BRAIN_ROOT)")
    ap.add_argument("--search-root", action="append", default=None, metavar="DIR",
                    help="where to hunt for a commit that is not in the brain (repeatable)")
    ap.add_argument("--remote", default=None, metavar="NAME_OR_URL",
                    help="ask this remote which refs it holds and report durability_tier. "
                         "Without it every row's tier is null, because a tier nobody measured "
                         "is not a tier -- it is a guess with a field name.")
    ap.add_argument("--json", action="store_true", help="machine-readable on stdout")
    args = ap.parse_args(argv)

    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    brain = Path(args.repo).resolve()
    if not (brain / ".git").exists():
        print(f"census: {brain} is not a git repository or worktree", file=sys.stderr)
        return 3

    # The brain is mutated by a live cross-host git loop, so a census is true as of one HEAD and
    # not one day. Stamp it, and stamp it again at the end so a HEAD that moved mid-run is visible
    # rather than silently averaged across two trees.
    head_before = git(brain, "rev-parse", "HEAD").stdout.strip()

    roots = tuple(args.search_root) if args.search_root else DEFAULT_SEARCH_ROOTS
    repos = find_repos(roots)
    doc = policy_doc()
    declared = {str(r): declared_refs_of(r, doc) for r in repos}
    durable = {str(r): durable_refs_of(r, declared[str(r)][0]) for r in repos}
    stones, stones_over_live = tombstones_of(brain, declared.get(str(brain), ([], None))[1])

    try:
        rows = collect_rows()
    except Unmeasurable as exc:
        print(f"census: {exc}", file=sys.stderr)
        return 3

    details: dict[str, str] = {}
    for r in rows:
        if r["table"] == "receipt" and r["detail"]:
            details.setdefault(r["git_ref"], r["detail"])

    refs = sorted({r["git_ref"] for r in rows})
    census = []
    for sha in refs:
        sightings = locate(sha, repos, durable)
        axis1 = sightings[0]["state"] if sightings else GONE
        m = DETAIL_PATH.search(details.get(sha, "") or "")
        art = artifact_state(brain, m.group(1) if m else None, durable.get(str(brain)))
        verdict, why = classify(axis1, sightings, art, str(brain), stones.get(sha))
        census.append({
            "git_ref": sha,
            "detail": details.get(sha),
            "booked_by": next((r["booked_by"] for r in rows
                               if r["git_ref"] == sha and r.get("booked_by")), None),
            "booked_at": next((r["booked_at"] for r in rows
                               if r["git_ref"] == sha and r.get("booked_at")), None),
            "tables": sorted({r["table"] for r in rows if r["git_ref"] == sha}),
            "rows": len([r for r in rows if r["git_ref"] == sha]),
            "git_repo": next((r["git_repo"] for r in rows
                              if r["git_ref"] == sha and r.get("git_repo")), None),
            "axis1_pointer": axis1,
            "sightings": sightings,
            "axis2_artifact": art,
            "verdict": verdict,
            "why": why,
        })

    # durability_tier. Only asked when a remote is named, and `mirrored` is granted only from
    # what that remote answers in THIS run. A push that exited 0 is not consulted and cannot be:
    # the evidence is `git ls-remote`, and a remote-tracking ref under refs/remotes/ is excluded
    # on purpose, because it is a file on this disk that outlives the remote being deleted.
    tier_block: dict | None = None
    if args.remote:
        pats, prov = policy_ledger_refs(brain, POLICY_PATH)
        try:
            tier_block = measure_tier(brain, args.remote, refs, pats, prov)
            tiers = {r["git_ref"]: r for r in tier_block["rows"]}
            for c in census:
                t = tiers.get(c["git_ref"])
                c["durability_tier"] = t["durability_tier"] if t else None
                c["durability_evidence"] = t["remote_evidence"] if t else None
        except RemoteUnreachable as exc:
            tier_block = {"remote": args.remote, "measured": False, "error": str(exc),
                          "note": "no tier assigned; an unanswered remote is an absent "
                                  "measurement, not a verdict of 'none'"}
            for c in census:
                c["durability_tier"] = None
                c["durability_evidence"] = None

    head_after = git(brain, "rev-parse", "HEAD").stdout.strip()
    by_verdict: dict[str, list[str]] = {}
    for c in census:
        by_verdict.setdefault(c["verdict"], []).append(c["git_ref"])

    result = {
        "measured_at": stamp,
        "brain": str(brain),
        "brain_head_before": head_before,
        "brain_head_after": head_after,
        "brain_head_moved_during_census": head_before != head_after,
        "brain_identity": repo_identity(brain),
        "durable_refs_used_for_the_brain": durable.get(str(brain), []),
        "durable_refs_declared_in_policy": declared.get(str(brain), ([], None))[0],
        "policy_path": str(POLICY_PATH),
        "tombstones_read": len(stones),
        "tombstones_over_a_live_commit": stones_over_live,
        "search_roots": list(roots),
        "repos_searched": len(repos),
        "rows": len(rows),
        "distinct_refs": len(refs),
        "by_axis1": {s: len([c for c in census if c["axis1_pointer"] == s])
                     for s in (DURABLE, FRAGILE, UNREFERENCED, GONE)},
        "by_axis2": {s: len([c for c in census if c["axis2_artifact"]["state"] == s])
                     for s in ("ON-MAIN", "ON-DURABLE", "SCRATCH-ONLY", "UNCOMMITTED", "ABSENT",
                               "NO-PATH-IN-DETAIL")},
        "by_verdict": {k: len(v) for k, v in sorted(by_verdict.items())},
        "durability_tier": tier_block,
        "by_durability_tier": ({t: len([c for c in census if c.get("durability_tier") == t])
                                for t in ("canon", "mirrored", "ledger", "none")}
                               if tier_block and tier_block.get("rows") is not None else None),
        "census": census,
    }

    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    print(f"=== git_ref census  {stamp} ===")
    print(f"  brain          {brain}")
    print(f"  brain HEAD     {head_before}"
          + ("  ** MOVED to " + head_after + " DURING THIS CENSUS **"
             if head_before != head_after else ""))
    print(f"  brain identity {result['brain_identity']}")
    print(f"  durable set    {_refs_summary(durable.get(str(brain), []))}")
    print(f"  declared in    {POLICY_PATH}")
    if stones:
        print(f"  tombstones     {len(stones)} read from a durable ref, never the working tree")
    if stones_over_live:
        print(f"  ** TOMBSTONE DEFECT: {len(stones_over_live)} tombstone(s) cover a commit this "
              f"repository still holds: {', '.join(s[:12] for s in stones_over_live)} **")
    print(f"  searched       {len(repos)} git repositories under {', '.join(roots)}")
    print()
    print(f"  {len(rows)} rows, {len(refs)} distinct git_refs in the live store")
    print()
    print("  AXIS 1, the pointer: does the sha resolve, anywhere on this machine?")
    for s in (DURABLE, FRAGILE, UNREFERENCED, GONE):
        print(f"    {s:<13} {result['by_axis1'][s]:>3} of {len(refs)}")
    print()
    if tier_block is None:
        print("  DURABILITY TIER: NOT MEASURED. Pass --remote to ask a remote which of these it "
              "holds.\n    Axis 1 DURABLE means 'a ref on THIS MACHINE still reaches it', which "
              "is true of\n    a commit that has never left this laptop. It is not the word "
              "'mirrored'.")
    elif not tier_block.get("rows"):
        print(f"  DURABILITY TIER: NOT MEASURED. {tier_block.get('error')}")
        print(f"    {tier_block.get('note')}")
    else:
        bt = result["by_durability_tier"]
        print(f"  DURABILITY TIER, measured by asking {tier_block['remote']}: it named "
              f"{tier_block['remote_refs_advertised']} refs and "
              f"{tier_block['shas_compared']} of these shas were compared against them")
        for t in ("canon", "mirrored", "ledger", "none"):
            print(f"    {t:<10} {bt[t]:>3} of {len(refs)}")
        print(f"    evidence   {tier_block['evidence_grades']}")
    print()
    print("  AXIS 2, the artifact: is the file the receipt names still in the brain?")
    for s in ("ON-MAIN", "ON-DURABLE", "SCRATCH-ONLY", "UNCOMMITTED", "ABSENT",
              "NO-PATH-IN-DETAIL"):
        print(f"    {s:<18} {result['by_axis2'][s]:>3} of {len(refs)}")
    print()
    print("  VERDICT, the two axes together")
    for k, v in sorted(result["by_verdict"].items(), key=lambda kv: -kv[1]):
        print(f"    {k:<26} {v:>3} of {len(refs)}")
    print()
    print("  item by item")
    for c in census:
        print(f"    {c['git_ref'][:12]}  {c['verdict']:<26} {c['axis1_pointer']:<12} "
              f"artifact={c['axis2_artifact']['state']}")
        print(f"        {c['why']}")
        for s in c["sightings"]:
            print(f"        seen: {s['repo']} [{s['state']}] "
                  f"{', '.join(s['refs']) or '(no ref contains it)'}  \"{s['subject']}\"")
        if c["axis2_artifact"]["path"]:
            print(f"        names: {c['axis2_artifact']['path']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
