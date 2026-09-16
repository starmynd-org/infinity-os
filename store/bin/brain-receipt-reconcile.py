#!/usr/bin/env python3
"""Reconcile every `git_ref` in the store against the repository that is supposed to hold it.

    "Losing the entire store must cost live queue position and history, and ZERO KNOWLEDGE."

`store/bin/brain-drop-test.sh` is what proves that invariant, and it proves it on a mechanism
rather than on a corpus: it seeds its OWN three receipts from real canon commits into a scratch
database, drops the database, and recovers those three. It never reads the receipts actually in
the store. So the drop test can pass while every real row in `brain.receipt` points at a commit
that no longer exists anywhere. Measured 2026-08-16T22:46Z: it did, for 20 of 26 rows.

This walks the real corpus instead. It is READ-ONLY by construction -- it opens the store through
`store.session.read()`, which Postgres has put in READ ONLY mode, and it only ever runs
`git cat-file` and `git merge-base`, never a write.

## Resolving is not durability, and conflating them is how this stayed invisible

A commit that `git cat-file -e` accepts may still be unreachable from any ref the operator would
not delete. A promotion proven on a scratch branch resolves today and is gone the moment that
branch is deleted and gc runs, which is the normal end of a scratch branch. So a two-state
resolves/dangles check reports a fragile ref as healthy. Three states, not two:

    DURABLE   an ancestor of a durable ref: `main` and `origin/main`, plus whatever
              `policy/durable-refs.json` declares for this repository (task 0349 added the
              booking ledger `receipts/durable` and the salvage namespace). The tier differs
              and the report says which -- a commit on the ledger survives branch cleanup, a
              deleted worktree, /tmp and gc, and does not survive a re-clone.
    FRAGILE   the object exists, but only on a branch outside the durable set. It is one
              `git branch -D` plus a gc away from LOST, and nothing today announces that.
    TOMBSTONED  the object is gone AND the operation recorded that on a durable ref, with the
              loss it assessed and no replacement ref. It is not a pass; it is a dead pointer
              somebody wrote down. `--no-tombstones` scores these LOST again.
    LOST      no such object in this repository. The evidence the receipt points at is gone,
              or it was never in this repository at all.

## Why LOST rows exist at all, which is the defect this reports rather than fixes

`brain.receipt` and `brain.touch` carry `git_ref` as a bare sha and **no column naming the
repository**. `brain-adapter receipt book --repo X` accepts any git repository and
`brain-adapter lineage project --repo X <sha>` will then record that sha. A receipt booked into
`/tmp/0149-cli-zf9k78n8` is indistinguishable in the store from one booked into the brain. Four
of the 20 LOST shas were still readable in two throwaway `/tmp` fixture repos when this was
written; the rest went with their repositories.

## The attribution axis, added when 0256 landed

Migration 19 gave `brain.receipt` and `brain.touch` a `git_repo` column holding the ROOT COMMIT
of the repository a `git_ref` came from, and `store_projection.py` writes it. That splits LOST
into three findings that need three different responses, where before there was one:

    LOST, attributed to THIS repo     real knowledge loss. The store says the brain held this
                                      commit and the brain does not. Nothing else in the program
                                      can tell you this.
    LOST, attributed ELSEWHERE        the commit was never this repository's. Not a durability
                                      failure of the brain, and provably so rather than assumed.
    LOST, UNATTRIBUTED                the row predates migration 19 and cannot say where its
                                      commit was supposed to be. The 26 rows in the live store on
                                      2026-08-17 are all of this kind.

The exit code does NOT soften for any of them. A LOST ref is exit 1 whatever its attribution,
because "we cannot tell whose it was" is not evidence that it was fine. The breakdown says what
to DO about each; it never says one of them is acceptable.

## Exit codes

    0   every git_ref in the store is DURABLE, or DURABLE and TOMBSTONED with nothing
        unaccounted for. The headline says which, and never reports a tombstone as a durable ref.
    1   at least one is LOST, or a tombstone covers a commit this repository still holds
    2   none are LOST but at least one is FRAGILE (pass --strict to make this exit 1)
    3   the store or the repository could not be read; nothing was measured

Exit 3 is separate on purpose. "I looked and everything is fine" and "I could not look" must
never share an exit code: that is how a checker that stopped working reads as a green.

Usage:

    store/bin/brain-receipt-reconcile.py [--repo PATH] [--durable-ref REF]... [--strict] [--json]
    store/bin/brain-receipt-reconcile.py --refs-from FILE   # score shas with no store to read

`--refs-from` is how `brain-drop-test.sh` scores the store's own receipts AFTER dropping the
database: the refs are read out first, the store is destroyed, and the same classifier runs on the
list. Rows sourced that way carry no `git_repo`, so every one scores UNATTRIBUTED -- which is the
truth about a bare sha list and not a softening.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# `store` lives one level above `bin/`. Located rather than assumed, so this runs from anywhere.
_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))

from store import session  # noqa: E402  (path must be set first)

DEFAULT_BRAIN = os.environ.get(
    "BRAIN_ROOT", "/mnt/c/Users/you/repos/your-brain"
)

#: Refs an operator does not delete in the normal course of work. A commit reachable from one of
#: these survives scratch-branch cleanup and a fresh clone. `origin/main` is included when it
#: exists because a ref that reached the remote survives losing this working copy entirely, which
#: is the stronger half of the invariant.
#:
#: These two are the FLOOR and no longer the whole set. Task 0349 added `policy/durable-refs.json`,
#: which declares the booking ledger and the salvage namespace per repository, because "durable"
#: could not mean `main` alone: `promote()` refuses `main` unconditionally, so a set containing
#: only `main` made every legal booking destination a non-durable one by construction. The policy
#: is read below and the refs it adds are printed with their tier, never folded into one number.
DEFAULT_DURABLE_REFS = ("main", "origin/main")

#: The declaration, read as data. This script imports no adapter code (see `this_repo_identity`),
#: so it parses the same JSON with its own reader rather than importing `brain_adapter.durability`.
POLICY_PATH = Path(os.environ.get("BRAIN_DURABLE_POLICY")
                   or _REPO_ROOT / "policy" / "durable-refs.json")

#: Every table carrying a `git_ref`. Extended by hand rather than discovered, so that a new table
#: with a `git_ref` is a deliberate addition here and not a silent omission. Verified against
#: information_schema at startup, and a table this list is missing is reported, never skipped.
GIT_REF_TABLES = ("receipt", "touch")

DURABLE, FRAGILE, LOST, TOMBSTONED = "DURABLE", "FRAGILE", "LOST", "TOMBSTONED"


class Unmeasurable(RuntimeError):
    """The store or the repository could not be read. Exit 3, never a green."""


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
    )


def resolve_durable_refs(repo: Path, wanted: tuple[str, ...]) -> list[str]:
    """The durable refs that actually exist here.

    A ref that is named but absent is reported by the caller rather than silently dropped: if
    `main` does not exist, every commit scores FRAGILE for a reason that has nothing to do with
    the commits, and that is a fact about the check, not about the corpus.
    """
    present = []
    for ref in wanted:
        if git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").returncode == 0:
            present.append(ref)
    return present


def refs_summary(refs: list[str], keep: int = 4) -> str:
    """A readable durable set. Collapses a namespace to `prefix/* (n)` instead of listing it.

    The salvage namespace is one ref per rescued commit, so the honest set is 24 names long and
    printing all of them buries the two that matter. Collapsed, never truncated: a set that
    silently dropped members would misreport the very thing this line exists to disclose.
    """
    singles = [r for r in refs if not r.startswith("refs/")]
    grouped: dict[str, int] = {}
    for r in refs:
        if r.startswith("refs/"):
            grouped["/".join(r.split("/")[:-1]) + "/*"] = grouped.get(
                "/".join(r.split("/")[:-1]) + "/*", 0) + 1
    parts = singles[:keep] + ([f"(+{len(singles) - keep} more)"] if len(singles) > keep else [])
    parts += [f"{k} ({n} refs)" for k, n in sorted(grouped.items())]
    return ", ".join(parts)


def policy_refs(repo: Path) -> tuple[list[str], dict | None, str]:
    """The declared refs for THIS repository and where its tombstones live. Data, not logic.

    Keyed by root commit, exactly as migration 19 keys `git_repo`, because every one of the 20
    dangling refs measured in 0297 came from a filesystem path that no longer exists. A policy
    keyed on paths would have the same lifetime as the thing it describes.

    A policy that cannot be read is NOT a silent fall back to the built-in floor: the caller
    reports the reason, because "the declaration is missing" and "the declaration says main" are
    different facts and only one of them is a durability finding.
    """
    try:
        doc = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [], None, f"policy unreadable ({POLICY_PATH}): {exc}"
    ident = this_repo_identity(repo)
    entry = (doc.get("repos") or {}).get(ident or "") or doc.get("default") or {}
    pats = list(entry.get("canon_refs") or []) + list(entry.get("ledger_refs") or [])
    refs: list[str] = []
    for pat in pats:
        if pat.endswith("*"):
            refs += [ln.strip() for ln in
                     git(repo, "for-each-ref", "--format=%(refname)", pat).stdout.splitlines()
                     if ln.strip()]
        else:
            refs.append(pat)
    return refs, entry.get("tombstones"), ""


def read_tombstones(repo: Path, spec: dict | None) -> tuple[dict[str, dict], list[str]]:
    """Refs recorded as unrecoverable, read FROM A DURABLE REF, plus the ones that are a defect.

    Two properties make a tombstone an honest close rather than a way to make a red go away:

      * it is read with `git show <durable-ref>:<path>`, never off the working tree. A file on
        disk can be written by whatever wanted the number to look better this minute; a file on
        the ledger is in a commit with an author and a message.
      * it can never cover a commit that still resolves. Those are returned separately and
        reported as a defect IN THE TOMBSTONE, because a tombstone over a live commit is somebody
        retiring a FRAGILE ref they did not want to deal with.
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
    stones = {}
    for row in doc.get("tombstoned") or []:
        sha = (row.get("git_ref") or "").strip()
        if sha and row.get("reason") and row.get("assessed_loss"):
            stones[sha] = row
    live = sorted(s for s in stones
                  if git(repo, "cat-file", "-e", f"{s}^{{commit}}").returncode == 0)
    return stones, live


def classify(repo: Path, sha: str, durable_refs: list[str],
             tombstones: dict[str, dict] | None = None) -> tuple[str, list[str]]:
    """One sha -> (state, the branches that contain it).

    TOMBSTONED is checked only after the object is found to be absent, and that order is the
    guarantee: a tombstone can never upgrade a ref that still resolves, so it cannot be used to
    silence a FRAGILE one.
    """
    if git(repo, "cat-file", "-e", f"{sha}^{{commit}}").returncode != 0:
        if sha in (tombstones or {}):
            return TOMBSTONED, []
        return LOST, []
    for ref in durable_refs:
        if git(repo, "merge-base", "--is-ancestor", sha, ref).returncode == 0:
            return DURABLE, [ref]
    out = git(repo, "branch", "-a", "--contains", sha).stdout
    branches = sorted({ln.strip(" *+").strip() for ln in out.splitlines() if ln.strip()})
    return FRAGILE, branches


def this_repo_identity(repo: Path) -> str | None:
    """The identity of the repository being reconciled against, in `git_repo`'s own terms.

    Computed exactly the way `store_join.repo_identity` computes what it WRITES -- sorted root
    commits, comma-joined, from `HEAD`'s ancestry -- and deliberately not imported from it: this
    script is in `store/` and imports no adapter code, so the two agreeing is a fact that can be
    checked rather than an assumption inherited through an import. If they ever disagree, every
    row reads as attributed-elsewhere, which is loud rather than silent.
    """
    p = git(repo, "rev-list", "--max-parents=0", "HEAD")
    if p.returncode != 0:
        return None
    roots = sorted({ln.strip() for ln in p.stdout.splitlines() if ln.strip()})
    return ",".join(roots) or None


#: What a row can say about which repository its `git_ref` belongs to.
MINE, FOREIGN, UNATTRIBUTED = "attributed-here", "attributed-elsewhere", "unattributed"


def attribution_of(git_repo: str | None, mine: str | None) -> str:
    """A row's `git_repo` against this repository's identity. Three answers, never two.

    UNATTRIBUTED IS NOT A SYNONYM FOR FOREIGN and collapsing them would be the same mistake as
    collapsing `unresolved` into `ambiguous` one seam over. A row with no `git_repo` is one this
    check cannot place; a row with a different `git_repo` is one this check placed somewhere else.
    The first is a gap in the record, the second is a fact.
    """
    if not git_repo:
        return UNATTRIBUTED
    return MINE if mine and git_repo == mine else FOREIGN


def collect_rows(tables: tuple[str, ...]) -> tuple[list[dict], list[str]]:
    """Every (table, id, git_ref, git_repo) in the store, plus tables this list did not know about.

    `git_repo` is SELECTed only where migration 19 has been applied, and the column list is built
    per table from `information_schema` rather than assumed. A store at version 18 reconciles
    exactly as it did before and every row scores UNATTRIBUTED, which is the truth about that
    store; a hard-coded `SELECT git_repo` would instead make the checker itself fail with
    `UndefinedColumn` and exit 3, reporting "I could not look" at a store that is merely older.
    """
    rows: list[dict] = []
    try:
        with session.read("owner") as s:
            # BASE TABLE only. `brain.touch_unresolved` is a VIEW over `brain.touch`, so a
            # discovery query that does not filter on table_type both double-counts its rows and
            # nags forever that a "table" is missing from GIT_REF_TABLES. Storage, not projection.
            found = {
                r["table_name"]
                for r in s.query(
                    "SELECT DISTINCT c.table_name FROM information_schema.columns c "
                    "JOIN information_schema.tables t "
                    "  ON t.table_schema = c.table_schema AND t.table_name = c.table_name "
                    "WHERE c.table_schema='brain' AND c.column_name='git_ref' "
                    "  AND t.table_type='BASE TABLE'"
                )
            }
            attributable = {
                r["table_name"]
                for r in s.query(
                    "SELECT DISTINCT table_name FROM information_schema.columns "
                    "WHERE table_schema='brain' AND column_name='git_repo'"
                )
            }
            for t in tables:
                if t not in found:
                    continue
                cols = "id, git_ref" + (", git_repo, git_repo_ref" if t in attributable else "")
                for r in s.query(
                    f"SELECT {cols} FROM brain.{t} WHERE git_ref IS NOT NULL ORDER BY id"
                ):
                    rows.append({"table": t, "id": r["id"], "git_ref": r["git_ref"],
                                 "git_repo": r.get("git_repo"),
                                 "git_repo_ref": r.get("git_repo_ref")})
            unknown = sorted(found - set(tables))
    except Exception as exc:  # the store is the thing being measured; failing to read it is exit 3
        raise Unmeasurable(f"could not read the store: {exc.__class__.__name__}: {exc}") from None
    return rows, unknown


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default=DEFAULT_BRAIN,
                    help="the repository a git_ref is expected to live in (default $BRAIN_ROOT)")
    ap.add_argument("--durable-ref", action="append", default=None, metavar="REF",
                    help="a ref an operator does not delete (repeatable; default main, origin/main)")
    ap.add_argument("--strict", action="store_true",
                    help="treat FRAGILE as a failure too (exit 1 instead of 2)")
    ap.add_argument("--no-tombstones", action="store_true",
                    help="ignore the recorded-gone list and score those refs LOST. The harsher "
                         "reading, kept available so the tombstoned count can never be mistaken "
                         "for a corpus that is clean")
    ap.add_argument("--refs-from", metavar="FILE",
                    help="classify the shas in FILE (one per line, '-' for stdin) instead of "
                         "reading the store. For scoring a corpus AFTER the store is gone.")
    ap.add_argument("--json", action="store_true", help="machine-readable on stdout")
    args = ap.parse_args(argv)

    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    repo = Path(args.repo).resolve()
    if not (repo / ".git").exists():
        print(f"reconcile: {repo} is not a git repository or worktree", file=sys.stderr)
        return 3

    declared, tomb_spec, policy_error = policy_refs(repo)
    if args.durable_ref:
        wanted = tuple(args.durable_ref)
    else:
        # The floor plus the declaration, deduplicated in order. `--durable-ref` still REPLACES
        # rather than extends, so a caller can measure against `main` alone and see the tier
        # difference for itself.
        wanted = tuple(dict.fromkeys(DEFAULT_DURABLE_REFS + tuple(declared)))
    durable_refs = resolve_durable_refs(repo, wanted)
    missing_refs = [r for r in wanted if r not in durable_refs]
    tombstones, tombstones_over_live = ({}, []) if args.no_tombstones \
        else read_tombstones(repo, tomb_spec)
    if not durable_refs:
        print(f"reconcile: none of {list(wanted)} exist in {repo}. Nothing can score DURABLE, so "
              f"this would report a corpus-wide failure that is really a failure of the check.",
              file=sys.stderr)
        return 3

    if args.refs_from:
        # The store is gone and these are the shas that were in it. Same classifier, no store.
        #
        # This exists so `brain-drop-test.sh` stage 4 -- the half that runs AFTER the database is
        # dropped -- can score the store's REAL receipts instead of the three fixtures it planted,
        # without reimplementing DURABLE/FRAGILE/LOST in bash. A second implementation is a second
        # thing that can disagree with the gate, and the disagreement would surface as a green.
        try:
            src = sys.stdin if args.refs_from == "-" else open(args.refs_from)
            with src as fh:
                shas = [ln.strip() for ln in fh if ln.strip() and not ln.startswith("#")]
        except OSError as exc:
            print(f"reconcile: could not read {args.refs_from}: {exc}", file=sys.stderr)
            return 3
        if not shas:
            # An empty ref list must never score as a clean corpus. "I measured nothing" is the
            # exact shape of green this checker exists to refuse.
            print(f"reconcile: {args.refs_from} held no git_refs. Nothing was measured.",
                  file=sys.stderr)
            return 3
        rows = [{"table": "(refs-from)", "id": None, "git_ref": s, "git_repo": None,
                 "git_repo_ref": None} for s in shas]
        unknown_tables = []
    else:
        try:
            rows, unknown_tables = collect_rows(GIT_REF_TABLES)
        except Unmeasurable as exc:
            print(f"reconcile: {exc}", file=sys.stderr)
            return 3

    verdicts: dict[str, tuple[str, list[str]]] = {}
    for sha in sorted({r["git_ref"] for r in rows}):
        verdicts[sha] = classify(repo, sha, durable_refs, tombstones)

    mine = this_repo_identity(repo)
    for r in rows:
        state, branches = verdicts[r["git_ref"]]
        r["state"], r["branches"] = state, branches
        r["attribution"] = attribution_of(r.get("git_repo"), mine)

    by_state = {s: [r for r in rows if r["state"] == s]
                for s in (DURABLE, FRAGILE, TOMBSTONED, LOST)}
    distinct = {s: sorted({r["git_ref"] for r in by_state[s]}) for s in by_state}

    # A sha's attribution, taken from the rows that carry it. Worst-case wins when the rows
    # disagree: if ANY row claims the commit is this repository's, a LOST commit is this
    # repository's problem, and reading the other row instead would let one unattributed copy
    # excuse an attributed one.
    attrib_of_sha = {
        sha: (MINE if any(r["attribution"] == MINE for r in rows if r["git_ref"] == sha)
              else FOREIGN if any(r["attribution"] == FOREIGN for r in rows if r["git_ref"] == sha)
              else UNATTRIBUTED)
        for sha in verdicts
    }
    lost_by_attribution = {
        a: [sha for sha in distinct[LOST] if attrib_of_sha[sha] == a]
        for a in (MINE, FOREIGN, UNATTRIBUTED)
    }

    result = {
        "measured_at": stamp,
        "repo": str(repo),
        "repo_identity": mine,
        "durable_refs": durable_refs,
        "durable_refs_absent": missing_refs,
        "durable_refs_declared_in_policy": declared,
        "policy_path": str(POLICY_PATH),
        "policy_error": policy_error,
        "tombstones_read": len(tombstones),
        "tombstones_over_a_live_commit": tombstones_over_live,
        "source": f"refs-from:{args.refs_from}" if args.refs_from else "store",
        "tables_walked": [] if args.refs_from else list(GIT_REF_TABLES),
        "tables_with_git_ref_not_walked": unknown_tables,
        "rows": len(rows),
        "distinct_refs": len(verdicts),
        "rows_by_state": {s: len(by_state[s]) for s in by_state},
        "distinct_by_state": {s: len(distinct[s]) for s in distinct},
        "distinct_by_attribution": {
            a: len([sha for sha in verdicts if attrib_of_sha[sha] == a])
            for a in (MINE, FOREIGN, UNATTRIBUTED)
        },
        "lost_refs": distinct[LOST],
        "lost_by_attribution": {a: lost_by_attribution[a] for a in lost_by_attribution},
        "fragile_refs": {sha: verdicts[sha][1] for sha in distinct[FRAGILE]},
        "tombstoned_refs": distinct[TOMBSTONED],
    }

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"=== receipt/touch git_ref reconciliation  {stamp} ===")
        print(f"  repo         {repo}")
        print(f"  durable refs {refs_summary(durable_refs)}"
              + (f"   (named but absent: {', '.join(missing_refs)})" if missing_refs else ""))
        # The durable SET is printed before any count that uses it. A number that says DURABLE
        # without saying against what is the overstatement this whole file exists to refuse.
        print(f"  declared in  {POLICY_PATH}"
              + (f"  ** {policy_error} **" if policy_error else ""))
        if tombstones:
            print(f"  tombstones   {len(tombstones)} read from "
                  f"{tomb_spec['ref']}:{tomb_spec['path']} (a ref, not the working tree)")
        if args.no_tombstones:
            print("  tombstones   IGNORED (--no-tombstones): every recorded-gone ref scores LOST")
        print(f"  walked       {args.refs_from} (a sha list, no store)" if args.refs_from
              else f"  walked       {', '.join(GIT_REF_TABLES)}")
        if unknown_tables:
            print(f"  NOT WALKED   brain.{', brain.'.join(unknown_tables)} also carry git_ref and "
                  f"this checker does not know about them. Add them to GIT_REF_TABLES.")
        print()
        print(f"  {len(rows)} rows, {len(verdicts)} distinct git_refs")
        for s in (DURABLE, FRAGILE, TOMBSTONED, LOST):
            print(f"    {s:<10} {len(by_state[s]):>4} rows   {len(distinct[s]):>4} distinct")
        print()
        print(f"  attribution (migration 19; this repo is {mine or 'UNKNOWN'})")
        for a in (MINE, FOREIGN, UNATTRIBUTED):
            print(f"    {a:<21} {result['distinct_by_attribution'][a]:>4} distinct")
        if result["distinct_by_attribution"][UNATTRIBUTED] and args.refs_from:
            # A bare sha list has no `git_repo` BY CONSTRUCTION. Printing migration 19's
            # explanation here would blame the corpus for a limit of the input format.
            print("    unattributed = a sha list carries no git_repo. This says nothing about the")
            print("    rows those shas came from; run without --refs-from for the attribution axis.")
        elif result["distinct_by_attribution"][UNATTRIBUTED]:
            print("    unattributed = booked before migration 19 recorded a repository. It is NOT")
            print("    a claim that those commits are this repo's; nothing can say where they were")
            print("    supposed to be, which is the state task 0256 was filed about.")
        if distinct[FRAGILE]:
            print()
            print("  FRAGILE -- the object exists but is reachable only from these branches, so it")
            print("  dies at 'git branch -D' plus a gc. Not durable evidence.")
            for sha in distinct[FRAGILE]:
                print(f"    {sha[:12]}  {', '.join(verdicts[sha][1]) or '(no branch contains it)'}")
        if distinct[TOMBSTONED]:
            print()
            print("  TOMBSTONED -- the object is not here and the operation has RECORDED that, on a")
            print("  durable ref, with the loss it assessed. This is not a pass: it is a dead")
            print("  pointer somebody wrote down instead of leaving for the next census to find.")
            for sha in distinct[TOMBSTONED]:
                st = tombstones.get(sha, {})
                print(f"    {sha[:12]}  {st.get('assessed_loss', '')[:88]}")
        if tombstones_over_live:
            print()
            print("  TOMBSTONE DEFECT -- these are recorded as gone and this repository HOLDS them.")
            print("  A tombstone may never cover a commit that still resolves; that is how a")
            print("  FRAGILE ref gets retired by whoever did not want to deal with it.")
            for sha in tombstones_over_live:
                print(f"    {sha[:12]}")
        if distinct[LOST]:
            print()
            print("  LOST -- no such object in this repository. Either the commit was gc'd, or it")
            print("  was never here: 'receipt book --repo X' accepts any repository and the store")
            print("  records no repo identity, so a foreign sha is indistinguishable from a local")
            print("  one. See task 0256.")
            for sha in distinct[LOST]:
                where = sorted({r["table"] for r in by_state[LOST] if r["git_ref"] == sha})
                origin = (args.refs_from if args.refs_from
                          else f"in brain.{', brain.'.join(where)}")
                print(f"    {sha[:12]}  {attrib_of_sha[sha]:<20} {origin}")
            if lost_by_attribution[MINE]:
                print(f"    -> {len(lost_by_attribution[MINE])} of these are attributed to THIS "
                      f"repository. That is real knowledge loss, not a foreign sha.")
        print()
        if distinct[LOST]:
            print(f"RECONCILE FAILED: {len(distinct[LOST])} of {len(verdicts)} git_refs are LOST "
                  f"({len(lost_by_attribution[MINE])} attributed here, "
                  f"{len(lost_by_attribution[FOREIGN])} attributed elsewhere, "
                  f"{len(lost_by_attribution[UNATTRIBUTED])} unattributed). "
                  f"The 'zero knowledge' invariant is false for them today.")
        elif tombstones_over_live:
            print(f"RECONCILE FAILED: {len(tombstones_over_live)} tombstone(s) cover a commit this "
                  f"repository still holds. The corpus may be fine; the record of it is not.")
        elif distinct[FRAGILE]:
            print(f"RECONCILE {'FAILED' if args.strict else 'WARNED'}: nothing is LOST, but "
                  f"{len(distinct[FRAGILE])} of {len(verdicts)} git_refs are FRAGILE.")
        elif distinct[TOMBSTONED]:
            print(f"RECONCILE PASSED WITH TOMBSTONES: {len(distinct[DURABLE])} of "
                  f"{len(verdicts)} git_refs are ancestors of "
                  f"{refs_summary(durable_refs)}, and "
                  f"{len(distinct[TOMBSTONED])} are recorded gone with the loss assessed. Nothing "
                  f"is unaccounted for. Re-run with --no-tombstones to score those as LOST.")
        else:
            print(f"RECONCILE PASSED: all {len(verdicts)} git_refs are ancestors of "
                  f"{refs_summary(durable_refs)}.")

    # A tombstone over a live commit fails ahead of everything else. It is the one finding that
    # says the RECORD is wrong rather than the corpus, and a wrong record cannot be used to grade
    # a corpus at all.
    if tombstones_over_live:
        return 1
    if distinct[LOST]:
        return 1
    if distinct[FRAGILE]:
        return 1 if args.strict else 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
