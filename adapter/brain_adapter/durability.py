"""Whether a commit is somewhere that survives, and the refusal when it is not.

    "Losing the entire store must cost live queue position and history, and ZERO KNOWLEDGE."

Task 0297 measured that invariant and it was false: of 26 distinct `git_ref`s in `brain.receipt`
and `brain.touch`, ZERO were ancestors of a durable ref. Not because anything failed -- because
nothing ever asked. `promote()` committed to whatever branch it was pointed at, returned the sha,
and reported success. A scratch branch and the brain's ledger were the same thing to it, so every
receipt this program had ever booked was provisional and none of them knew it.

This module is the thing that asks. Three states, and the middle one is the whole point:

    mirrored   an ancestor of a remote-tracking ref. Survives losing this machine.
    canon      an ancestor of main. What a fresh clone gets. A HUMAN lands it, never an agent.
    ledger     an ancestor of a ref DECLARED in policy/durable-refs.json, in this repository.
               Survives scratch-branch deletion, worktree removal, /tmp, and gc. It does not
               survive a re-clone.

`ledger` exists because the two guarantees below are both real and they pull in opposite
directions. D2's `promote()` refuses `main` unconditionally -- no agent self-approves canon --
and the invariant wants the ref to survive. "Refuse main" plus no other durable destination is
what produced 26 provisional receipts. A declared, append-only branch that is not `main`
satisfies both: the adapter appends, a human merges.

WHAT `ledger` DOES NOT CLAIM. It is the floor the brief for 0349 names -- lose every scratch
branch, every worktree and all of /tmp and it costs zero receipts -- and it is not the ceiling.
A receipt at `ledger` still lives in exactly one working copy. `tier` is reported on every
booking and in every census line for that reason: a number that says DURABLE without saying at
which tier is the same overstatement this module exists to end.

THE DECLARATION IS DATA AND LIVES IN ONE FILE. `policy/durable-refs.json` is read here, by
`store/bin/brain-receipt-reconcile.py` and by `store/bin/brain-git-ref-census.py`. There is no
command-line flag that adds a durable ref: widening the set is a reviewed diff to a checked-in
file, not an argument an agent can pass at the moment it wants a green.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .promotion import PROTECTED_BRANCHES, PromotionRefused

#: Where the declaration lives, and the env var that points a test at its own copy.
ENV_POLICY = "BRAIN_DURABLE_POLICY"
DEFAULT_POLICY = Path(__file__).resolve().parents[2] / "policy" / "durable-refs.json"

MIRRORED, CANON, LEDGER = "mirrored", "canon", "ledger"
TIER_RANK = {LEDGER: 1, CANON: 2, MIRRORED: 3}


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=False)


@dataclass
class RepoPolicy:
    """One repository's declared refs, already expanded against what exists on disk."""

    identity: str | None
    name: str
    canon_refs: list[str] = field(default_factory=list)
    ledger_refs: list[str] = field(default_factory=list)
    book_refs: list[str] = field(default_factory=list)
    tombstones: dict | None = None
    matched: bool = False          # True when the repo's identity was named explicitly
    source: str = ""
    remotes: frozenset[str] = frozenset()

    def is_remote_ref(self, ref: str) -> bool:
        """A remote-tracking ref: the only kind that proves the commit left this machine.

        Decided against this repository's actual remotes, never against the string "origin". A
        repo with no remote has no mirrored tier, and saying otherwise would be the confidently
        wrong half of a durability claim.
        """
        if ref.startswith("refs/remotes/"):
            return True
        head, _, rest = ref.partition("/")
        return bool(rest) and head in self.remotes

    def durable_refs(self) -> list[dict]:
        """Every declared ref that exists here, best tier first. Tier is per-ref, not per-repo."""
        out = [{"ref": r, "tier": MIRRORED if self.is_remote_ref(r) else CANON}
               for r in self.canon_refs]
        out += [{"ref": r, "tier": MIRRORED if self.is_remote_ref(r) else LEDGER}
                for r in self.ledger_refs]
        return sorted(out, key=lambda d: -TIER_RANK[d["tier"]])


def load_policy(path: str | Path | None = None) -> dict:
    """The declaration, or a refusal that names the file. A missing policy is never a default.

    A policy file that cannot be read must not silently become "nothing is durable" (every
    booking refused, which reads as a broken adapter) nor "everything is durable" (every booking
    allowed, which is the defect). It is a typed refusal naming the path.
    """
    p = Path(path or os.environ.get(ENV_POLICY) or DEFAULT_POLICY)
    try:
        return json.loads(p.read_text(encoding="utf-8")) | {"_path": str(p)}
    except FileNotFoundError:
        raise PromotionRefused(
            "NO_DURABILITY_POLICY",
            f"{p} does not exist. Which refs are durable is a declaration, not a default: "
            f"guessing it here is how 26 receipts landed on branches nobody kept.") from None
    except json.JSONDecodeError as exc:
        raise PromotionRefused("BAD_DURABILITY_POLICY", f"{p}: {exc}") from None


def repo_identity(repo: Path) -> str | None:
    """Root commits of HEAD, sorted and comma-joined -- migration 19's identity, recomputed.

    Recomputed rather than imported from `store_join`, for the same reason the reconciler and the
    census each recompute it: three implementations agreeing is checkable, an import is assumed.
    """
    p = _git(repo, "rev-list", "--max-parents=0", "HEAD")
    if p.returncode != 0:
        return None
    roots = sorted({ln.strip() for ln in p.stdout.splitlines() if ln.strip()})
    return ",".join(roots) or None


def expand_refs(repo: Path, patterns: list[str]) -> list[str]:
    """Declared ref patterns -> the refs that actually exist here.

    A pattern ending in `*` is expanded with `for-each-ref`; anything else is kept only if it
    resolves. A named-but-absent ref is dropped rather than carried, because `merge-base
    --is-ancestor <sha> <missing-ref>` fails for a reason that has nothing to do with the sha.
    """
    out: list[str] = []
    for pat in patterns or []:
        if pat.endswith("*"):
            p = _git(repo, "for-each-ref", "--format=%(refname)", pat)
            out += [ln.strip() for ln in p.stdout.splitlines() if ln.strip()]
        elif _git(repo, "rev-parse", "--verify", "--quiet", f"{pat}^{{commit}}").returncode == 0:
            out.append(pat)
    return out


def policy_for(repo: Path, path: str | Path | None = None) -> RepoPolicy:
    """The declared policy for THIS repository, keyed by identity, falling back to the default.

    Keyed by root commit rather than by filesystem path: every one of the 20 refs task 0297 found
    dangling came from a path that no longer exists, and a policy keyed on paths would have the
    same lifetime as the thing it was describing.
    """
    doc = load_policy(path)
    ident = repo_identity(repo)
    entry = (doc.get("repos") or {}).get(ident or "", None)
    base = doc.get("default") or {}
    matched = entry is not None
    entry = entry or base

    return RepoPolicy(
        remotes=frozenset(ln.strip() for ln in _git(repo, "remote").stdout.splitlines()
                          if ln.strip()),
        identity=ident,
        name=entry.get("name") or ("(default policy)" if not matched else "?"),
        canon_refs=expand_refs(repo, entry.get("canon_refs") or base.get("canon_refs") or []),
        ledger_refs=expand_refs(repo, entry.get("ledger_refs") or base.get("ledger_refs") or []),
        book_refs=list(entry.get("book_refs") or base.get("book_refs") or []),
        tombstones=entry.get("tombstones"),
        matched=matched,
        source=doc.get("_path", ""),
    )


def durability_of(repo: Path, sha: str, pol: RepoPolicy | None = None) -> tuple[str | None, str | None]:
    """(tier, ref) for a commit, best tier first. (None, None) when nothing durable contains it.

    `git cat-file` resolving is NOT durability and conflating them is how this stayed invisible
    for 26 rows: a commit on a scratch branch resolves right up until the branch is deleted.
    """
    pol = pol or policy_for(repo)
    if _git(repo, "cat-file", "-e", f"{sha}^{{commit}}").returncode != 0:
        return (None, None)
    for cand in pol.durable_refs():
        if _git(repo, "merge-base", "--is-ancestor", sha, cand["ref"]).returncode == 0:
            return (cand["tier"], cand["ref"])
    return (None, None)


def assert_bookable(repo: Path, branch: str, pol: RepoPolicy | None = None) -> RepoPolicy:
    """The write-time gate. Refuses BEFORE anything is staged, loudly, naming the way out.

    This is the whole fix in one function: a booking onto a ref nobody keeps is refused at write
    time instead of discovered at census time, months later, as a row asserting a commit that no
    longer exists.

    IT CHECKS THE REPOSITORY BEFORE IT CHECKS THE BRANCH, and that order is the correction task
    0349 needed twice. The first cut of this gate compared only the branch NAME against
    `book_refs`, and `book_refs` fell back to the policy's `default` block for a repository the
    declaration had never heard of. So `mktemp -d && git init -b receipts/durable` booked at tier
    `ledger`, exit 0, into /tmp -- the exact silent-provisional-write this module exists to end,
    reproduced against the shipped gate on 2026-08-17. A branch name is a string anyone can spell;
    identity is the root commit, which is why `default.book_refs` is now empty and an undeclared
    repository is refused here rather than classified by a fallback.
    """
    pol = pol or policy_for(repo)

    illegal = sorted(set(pol.book_refs) & PROTECTED_BRANCHES)
    if illegal:
        raise PromotionRefused(
            "POLICY_REFUSED",
            f"{pol.source} names {illegal} as bookable. A protected branch is never bookable: "
            f"the decider on every acceptance is a human and a config file does not get to "
            f"change that.")
    if not pol.matched:
        raise PromotionRefused(
            "UNDECLARED_REPO",
            f"refusing to book into a repository {pol.source} does not name (identity "
            f"{(pol.identity or '?')[:12]}). Durability is a property of the REPOSITORY, not of a "
            f"branch name: `git init -b {branch}` in a mkdtemp directory produces a branch spelled "
            f"exactly like the ledger and a commit that dies with the directory. The way in is a "
            f"reviewed diff adding this identity to the declaration, never a flag at the call "
            f"site that wants a green.")
    if not pol.book_refs:
        raise PromotionRefused(
            "NO_BOOKABLE_REF",
            f"{pol.source} declares no bookable ref for this repository ({pol.name}). A "
            f"promotion with nowhere durable to land is refused, never written somewhere else.")
    if branch not in pol.book_refs:
        raise PromotionRefused(
            "NOT_DURABLE",
            f"refusing to book onto {branch!r}: it is not a durable ref. This repository books "
            f"onto {pol.book_refs} (declared in {pol.source}). A commit on a scratch branch, in a "
            f"worktree or under /tmp is provisional -- 26 of 26 receipts booked that way before "
            f"2026-08-17 are on branches and directories that are gone or going. Create/checkout "
            f"{pol.book_refs[0]!r} and book there; a human merges it to main.")
    return pol


def assert_durable(repo: Path, sha: str, pol: RepoPolicy | None = None) -> tuple[str, str]:
    """Post-commit verification. The sha must actually be reachable from a durable ref.

    The pre-flight above checks the branch NAME. This checks the OBJECT, after the write, because
    a name is a claim and reachability is the fact. They can differ: a concurrent checkout, a
    branch deleted between the two calls, a policy naming a ref that stopped existing mid-run.
    """
    pol = pol or policy_for(repo)
    tier, ref = durability_of(repo, sha, pol)
    if tier is None:
        raise PromotionRefused(
            "DURABILITY_UNVERIFIED",
            f"{sha[:12]} was committed but is not reachable from any durable ref "
            f"({[c['ref'] for c in pol.durable_refs()] or 'none exist here'}). The commit is in "
            f"this repository's object store and this is NOT reported as a booking: a success "
            f"here is exactly the silent provisional write task 0349 was filed to end.")
    return tier, ref


def tombstoned_refs(repo: Path, pol: RepoPolicy | None = None) -> dict[str, dict]:
    """Refs the operation has recorded as unrecoverable, read from git, keyed by full sha.

    A tombstone is an honest close, and it is only honest under two conditions this enforces:

      * it is READ FROM A DURABLE REF, never from the working tree. A file on disk can be written
        by whatever wanted the number to look better this minute.
      * it can never cover a commit that still resolves. `verify_tombstones` rejects those, so a
        tombstone cannot be used to retire a FRAGILE ref that someone did not want to deal with.

    It records that a pointer is dead and what the loss was. It never invents a replacement ref --
    the same rule the unresolvable-entity path follows: a fabricated pointer is believed later,
    which is worse than a null.
    """
    pol = pol or policy_for(repo)
    spec = pol.tombstones
    if not spec:
        return {}
    p = _git(repo, "show", f"{spec['ref']}:{spec['path']}")
    if p.returncode != 0:
        return {}
    try:
        doc = json.loads(p.stdout)
    except json.JSONDecodeError:
        return {}
    out = {}
    for row in doc.get("tombstoned") or []:
        sha = (row.get("git_ref") or "").strip()
        if sha and row.get("reason") and row.get("assessed_loss"):
            out[sha] = row | {"_from": f"{spec['ref']}:{spec['path']}"}
    return out


def verify_tombstones(repo: Path, stones: dict[str, dict]) -> list[str]:
    """The shas a tombstone claims are gone but which this repository still holds.

    Returned rather than raised: the caller reports it. A tombstone over a live commit is not a
    corpus finding, it is a defect in the tombstone, and the two must not read the same.
    """
    return sorted(sha for sha in stones
                  if _git(repo, "cat-file", "-e", f"{sha}^{{commit}}").returncode == 0)
