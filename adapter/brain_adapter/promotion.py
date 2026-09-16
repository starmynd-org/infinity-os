"""The one place this adapter writes to git.

`receipt book` and `touch add` both land here. There is one transition, one
implementation, one audit point, per the narrow waist in `D00-shared-context.md`. Nothing
else in the adapter calls `git commit`, and the MCP server's tools are wrappers over the
verbs above this, never a parallel path.

The rules this enforces are not this module's to decide
(`knowledge/ai-architecture/concepts/surface-boundary.md`,
`_system/promotion-path-rules.md`, `_system/wager-ledger-rules.md`):

- The only legal bridge from the runtime plane to the truth plane is a visible, typed
  event recorded in git. An invisible substrate write that becomes authoritative meaning
  is never a promotion; it is a second source of truth.
- No agent self-approves canon. The decider on every acceptance is a human. A receipt
  records who decided; it never decides.
- The runtime never owns knowledge. Reads are free. Writes go through here.
- A promotion is not made until it is somewhere that survives. `durability.py` holds that gate
  and `policy/durable-refs.json` holds the declaration; both run inside `promote()` below, once
  before anything is staged and once over the finished commit. Task 0297 measured what this
  program did without it: 0 of 26 stored `git_ref`s were ancestors of a durable ref, because
  refusing `main` with no other durable destination leaves only branches nobody keeps.

Every refusal below is typed and loud. A promotion that cannot be made legally is refused,
never downgraded into a quieter write that happens to succeed.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# The promotion taxonomy of surface-boundary.md. The first two never cross into git at
# all; the rest are the paths that do, each operator-gated at its own transition.
PROMOTION_KINDS = {
    "read-only": "no promotion; nothing is written",
    "runtime-state-write": "stays in the runtime plane; never reaches git",
    "draft-to-canon-candidate-to-canon": "operator-gated commit or PR into canon",
    "run-to-memory": "a reviewed lesson becomes a memory node",
    "intake-capture-to-node": "the intake three-layer path",
    "closeout-to-planning-truth": "session and swarm closeout, distilled",
}

# Kinds that must never produce a commit, because they do not cross the plane boundary.
NON_CROSSING_KINDS = {"read-only", "runtime-state-write"}

# This lane proves the mechanism against a scratch branch and never lands on the brain's
# trunk. Landing is a human act on a reviewed branch, which is what "no agent
# self-approves canon" means in practice.
PROTECTED_BRANCHES = {"main", "master", "trunk"}

# Paths whose content is produced by a generator and compared by full text. A booking
# path that targets one of these is a defect in the generator, raised, never patched here.
GENERATED_PREFIXES = (
    "knowledge/example-department/metrics/",
    "outputs/crm-corpus/generated/",
)

# Canon is the operator-gate surface. Writing here without an approval record is refused.
CANON_MARKERS = ("/canon/",)


class PromotionRefused(Exception):
    """A refusal with a reason. Never caught and downgraded inside this module."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass
class Lineage:
    """What makes a receipt attributable. Absent keys are refused; null values are not.

    `caused_by_event_id` and `approval_ref` are null when no fabric event caused the
    action and no human approval exists. The contract
    (`entities/rules/result-and-escalation-contract.md`) requires the fields to be
    present so that "flagged event, no approval" is detectable by join rather than by
    narrative. A missing key would make the breach invisible; an explicit null does not.
    """

    produced_by: str | None
    produced_by_ref: str | None = None
    resolution_status: str = "resolved"
    caused_by_event_id: str | None = None
    approval_ref: str | None = None
    actor_type: str | None = None  # human | ai | hybrid
    session_id: str | None = None
    work_item_id: str | None = None
    canonical_task: str | None = None

    def to_dict(self) -> dict:
        return {
            "produced_by": self.produced_by,
            "produced_by_ref": self.produced_by_ref,
            "resolution_status": self.resolution_status,
            "caused_by_event_id": self.caused_by_event_id,
            "approval_ref": self.approval_ref,
            "actor_type": self.actor_type,
            "session_id": self.session_id,
            "work_item_id": self.work_item_id,
            "canonical_task": self.canonical_task,
        }


@dataclass
class Promotion:
    kind: str
    paths: list[Path]
    message: str
    lineage: Lineage
    canon_touching: bool = False
    external: bool = False


@dataclass
class PromotionResult:
    sha: str
    branch: str
    files_in_commit: list[str] = field(default_factory=list)
    kind: str = ""
    # Which durable ref this commit is reachable from, and how strongly. Never blank on a
    # success: `promote` refuses rather than returning a result whose durability is unknown.
    durability_tier: str = ""
    durable_ref: str = ""

    def to_dict(self) -> dict:
        return {"sha": self.sha, "branch": self.branch,
                "files_in_commit": self.files_in_commit, "kind": self.kind,
                "durability_tier": self.durability_tier, "durable_ref": self.durable_ref}


def git(repo: Path, *args: str, check: bool = True) -> str:
    res = subprocess.run(["git", "-C", str(repo), *args],
                         capture_output=True, text=True, check=False)
    if check and res.returncode != 0:
        raise PromotionRefused("GIT_FAILED", f"git {' '.join(args)}: {res.stderr.strip()}")
    return res.stdout


def current_branch(repo: Path) -> str:
    return git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()


def promote(repo: Path, promotion: Promotion, expect_branch: str | None = None) -> PromotionResult:
    """The single transition. Every guard runs before anything is staged."""
    repo = Path(repo).resolve()

    if promotion.kind not in PROMOTION_KINDS:
        raise PromotionRefused(
            "UNKNOWN_PROMOTION_KIND",
            f"{promotion.kind!r} is not in the taxonomy of surface-boundary.md: "
            f"{sorted(PROMOTION_KINDS)}",
        )
    if promotion.kind in NON_CROSSING_KINDS:
        raise PromotionRefused(
            "NOT_A_PROMOTION",
            f"{promotion.kind!r} does not cross into the truth plane. Writing it to git "
            f"would make a runtime record into authoritative meaning, which is a second "
            f"source of truth, not a promotion.",
        )
    if not promotion.paths:
        raise PromotionRefused("NO_PATHS", "a promotion with no files is not an event")
    if not (repo / ".git").exists() and not (repo / ".git").is_file():
        raise PromotionRefused("NOT_A_REPO", f"{repo} is not a git repository or worktree")

    branch = current_branch(repo)
    if branch in PROTECTED_BRANCHES:
        raise PromotionRefused(
            "PROTECTED_BRANCH",
            f"refusing to commit to {branch!r}. A promotion is proven on a branch and "
            f"landed by a human. No agent self-approves canon.",
        )
    if expect_branch and branch != expect_branch:
        raise PromotionRefused(
            "WRONG_BRANCH", f"on {branch!r}, expected {expect_branch!r}")

    # The durability gate, BEFORE anything is staged. Refusing main was never enough on its own:
    # "no agent self-approves canon" plus no other durable destination is what made every receipt
    # this program booked provisional (task 0297: 0 of 26 durable). The import is local because
    # `durability` imports this module for `PromotionRefused` and `PROTECTED_BRANCHES`.
    from .durability import assert_bookable, assert_durable

    pol = assert_bookable(repo, branch)

    rels: list[str] = []
    for p in promotion.paths:
        p = Path(p).resolve()
        try:
            rel = p.relative_to(repo).as_posix()
        except ValueError:
            raise PromotionRefused("PATH_OUTSIDE_REPO", f"{p} is not inside {repo}") from None
        if not p.exists():
            raise PromotionRefused("PATH_MISSING", f"{rel} does not exist on disk")
        for prefix in GENERATED_PREFIXES:
            if rel.startswith(prefix):
                raise PromotionRefused(
                    "GENERATED_FILE",
                    f"{rel} is produced by a generator and compared by full text. The "
                    f"fix belongs in the generator; this lane raises it rather than "
                    f"patching the output.",
                )
        if any(m in f"/{rel}" for m in CANON_MARKERS) and not promotion.lineage.approval_ref:
            raise PromotionRefused(
                "CANON_WITHOUT_APPROVAL",
                f"{rel} is canon and the promotion carries no approval_ref. The decider "
                f"on every acceptance is a human.",
            )
        rels.append(rel)

    lin = promotion.lineage
    if lin.produced_by is None and lin.resolution_status == "resolved":
        raise PromotionRefused(
            "LINEAGE_INCOHERENT",
            "produced_by is null but resolution_status says 'resolved'. A null id with a "
            "resolved status is how a fabricated attribution gets believed later.",
        )
    if promotion.canon_touching and not lin.approval_ref:
        raise PromotionRefused(
            "CANON_TOUCHING_WITHOUT_APPROVAL",
            "canon_touching promotions require an approval_ref naming the human decider.",
        )

    # Stage exactly these paths, by name. Never `git add -A`: the brain working tree
    # carries another session's in-flight modifications and they are not ours.
    git(repo, "add", "--", *rels)
    body = _commit_message(promotion, rels)
    git(repo, "commit", "--only", "-m", body, "--", *rels)
    sha = git(repo, "rev-parse", "HEAD").strip()

    # Verify rather than assume: the commit must contain our paths and nothing else.
    in_commit = [l for l in git(repo, "show", "--pretty=", "--name-only", sha).splitlines() if l.strip()]
    unexpected = sorted(set(in_commit) - set(rels))
    if unexpected:
        raise PromotionRefused(
            "COMMIT_SWEPT_EXTRA_FILES",
            f"commit {sha[:12]} contains files this promotion did not write: {unexpected}",
        )
    missing = sorted(set(rels) - set(in_commit))
    if missing:
        raise PromotionRefused(
            "COMMIT_MISSING_FILES",
            f"commit {sha[:12]} is missing {missing} (already committed and unchanged?)",
        )

    # Verify the OBJECT, not the branch name that was checked above. A name is a claim; ancestry
    # is the fact, and this is the sentence "the receipt is booked" is allowed to rest on.
    tier, dref = assert_durable(repo, sha, pol)

    return PromotionResult(sha=sha, branch=branch, files_in_commit=sorted(in_commit),
                           kind=promotion.kind, durability_tier=tier, durable_ref=dref)


def _commit_message(promotion: Promotion, rels: list[str]) -> str:
    """The commit message is part of the visible, typed event, not decoration."""
    lin = promotion.lineage
    lines = [promotion.message, ""]
    lines.append(f"promotion-kind: {promotion.kind}")
    lines.append(f"produced-by: {lin.produced_by if lin.produced_by else 'null'}")
    if lin.produced_by is None:
        lines.append(f"produced-by-unresolved-ref: {lin.produced_by_ref}")
        lines.append(f"resolution-status: {lin.resolution_status}")
    lines.append(f"caused-by-event-id: {lin.caused_by_event_id or 'null'}")
    lines.append(f"approval-ref: {lin.approval_ref or 'null'}")
    if lin.actor_type:
        lines.append(f"actor-type: {lin.actor_type}")
    if lin.work_item_id:
        lines.append(f"work-item: {lin.work_item_id}")
    if lin.canonical_task:
        lines.append(f"canonical-task: {lin.canonical_task}")
    if lin.session_id:
        lines.append(f"session: {lin.session_id}")
    lines.append(f"external: {str(promotion.external).lower()}")
    lines.append(f"canon-touching: {str(promotion.canon_touching).lower()}")
    return "\n".join(lines)
