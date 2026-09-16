"""The seam: what the adapter emits, in the shape the store's columns take.

D2 built the git side and D1 built the store side, and neither owned the join between them.
This module is the git half of that join and it holds **no database code at all** -- not as
tidiness, as the invariant:

    LOSING THE WHOLE STORE COSTS QUEUE POSITION AND HISTORY AND ZERO KNOWLEDGE.

Everything here reads git and returns plain dicts. `psycopg2` is not imported, the store is not
imported, and no function here needs a database to be reachable. The adapter keeps resolving
entities and booking receipts with Postgres stopped, because the knowledge never lived there.
The write half is `store_projection.py`, and it is a separate module precisely so this one can
be imported without it.

DIRECTION OF TRUTH, WHICH IS THE THING TO GET RIGHT
---------------------------------------------------
`_system/wager-ledger-rules.md` WAGER-7: *"git holds the contracts, the per-item receipts, the
immutable pre-registered wagers, **the component touch-edges**, and the promoted verdict
diagnoses."*

So the arrow is one-way and this module only ever points it that way:

    git commit  --parse-->  dicts  --store.apply-->  brain.receipt / brain.touch
    git commit  <--never--------------------------  the store

There is no function here that reads the store, and none that writes git. `receipt book` and
`touch add` write git; this reads back what they committed. If a future edit adds a path from a
table into a receipt, the store has become authoritative for lineage and the invariant above is
false rather than merely untested.

The receipts are read with `git show <sha>:<path>`, from the COMMIT, never from the working
tree. A working-tree read would project whatever happens to be on disk, including an uncommitted
edit, which is the same failure wearing different clothes.

THE FOUR STATES
---------------
`Resolution.status` has three values and the absence of a resolution is the fourth. They are not
interchangeable and collapsing any pair of them destroys a distinction someone measured:

    resolved     landed on exactly one node; produced_by is that node's verified id
    ambiguous    landed on several nodes with different ids; produced_by is NULL, and the
                 candidates are recoverable, so this is FIXABLE by disambiguating
    unresolved   landed on nothing; produced_by is NULL and there is nothing to disambiguate
    (absent)     no resolution was attempted; resolution_status is SQL NULL

The fourth is not pedantry. `brain.session` held 1,131 rows stamped with `d3-ingest`, a producer
NAME rather than a brain entity id. Those rows never made a claim about the brain, so scoring them
beside genuinely dangling lineage would report a failure that did not happen. `producer_stamp()`
below is how a caller says that explicitly.

Since migration 17 (task 0142) that name lands in a FOURTH column, `produced_by_producer`, and
`produced_by` is NULL on those rows: 2,269 of them were moved there, and the writers were changed
in the same commit. The fourth STATE is unchanged -- `resolution_status IS NULL` still means "no
attempt was made" -- but the fourth COLUMN is what makes it usable, because a NULL status alone
could not tell a lineage walker whether to walk: nine rows wearing it carried a real, resolvable
entity id and 2,269 carried a component name, and no single rule was right on both.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .index import STATUS_AMBIGUOUS, STATUS_RESOLVED, STATUS_UNRESOLVED, EntityIndex, Resolution
from .promotion import PromotionRefused

# The closed vocabulary, matched to `<table>_resolution_status_enum` in
# migrations/0005_lineage_resolution.sql. None is the fourth state and is not in this set.
RESOLUTION_STATUSES = frozenset({STATUS_RESOLVED, STATUS_AMBIGUOUS, STATUS_UNRESOLVED})

_SHA = re.compile(r"^[0-9a-f]{40}$|^[0-9a-f]{64}$")
_TOUCH_BLOCK = re.compile(r"```yaml\n(touches:\n.*?)```", re.DOTALL)
_LINEAGE_ROW = re.compile(r"^\|\s*`([a-z_]+)`\s*\|\s*`([^`]*)`\s*\|\s*$", re.MULTILINE)
_FRONTMATTER_ID = re.compile(r'^id:\s*"?([^"\n]+)"?\s*$', re.MULTILINE)


# ---------------------------------------------------------------------------------------
# the mapping: one Resolution in, three columns out
# ---------------------------------------------------------------------------------------

def lineage_columns(res: Resolution) -> dict:
    """`Resolution` -> the three lineage columns, carrying all four states.

    This is deliberately a thin wrapper over `Resolution.produced_by()` rather than a second
    implementation of it. D2 owns what a resolution means; this owns only which column each
    piece lands in, and the two must never be able to disagree about the failure case.

    `resolution_candidates` is dropped here on purpose and it is the one lossy step in the
    seam. There is no column for it, and adding one would be a list-valued column that only
    ambiguous rows ever fill. The candidates stay in the git receipt, which is the source; the
    row carries `resolution_status = 'ambiguous'` plus the raw ref, which is enough to re-run
    the resolution and get the candidates back. Stated rather than left to be discovered.
    """
    emitted = res.produced_by()
    cols = {
        "produced_by": emitted["produced_by"],
        "produced_by_ref": emitted["produced_by_ref"],
        "resolution_status": emitted["resolution_status"],
    }
    _assert_coherent(cols)
    return cols


def producer_stamp(name: str) -> dict:
    """The fourth state, said out loud: a producer NAME, with no claim about the brain.

    `d3-ingest` is not a failed resolution, it is not a resolution. The status stays SQL NULL so
    a reconciliation pass can tell "never asked" from "asked and found nothing", and
    `produced_by_ref` stays NULL because there was no reference to preserve.

    MIGRATION 17 CHANGED WHICH COLUMN THE NAME LANDS IN, and nothing else here. It used to
    return `produced_by = name`, which is what this function was originally for and what
    migration 5 tolerated on purpose. That tolerance turned out to cost more than it saved: the
    shape (`produced_by` set, ref NULL, status NULL) was worn BOTH by 2,269 producer-stamped
    rows that resolve to nothing AND by 9 rows carrying a real, resolvable entity id, so
    `resolution_status` could not tell a lineage walker whether to walk. `produced_by` carried a
    VERIFICATION discriminator where it also needed a KIND one. The name now lands in
    `produced_by_producer`, `produced_by` is NULL, and `produced_by IS NOT NULL` means "an
    entity id" unconditionally. See `migrations/0017_producer_column.sql`, and note that the
    fourth state itself is untouched: status stays NULL and still means "no attempt was made".
    """
    return {"produced_by": None, "produced_by_producer": name or None,
            "produced_by_ref": None, "resolution_status": None}


def resolve_lineage(index: EntityIndex, ref: str | None) -> tuple[dict, Resolution | None]:
    """`entity resolve` and the columns it produces, in one call. Never raises on a miss.

    A miss is a row that says so, not an exception: the caller still writes its row, and the
    row carries the failure. The non-zero exit code belongs to the CLI (3 unresolved,
    4 ambiguous) and is preserved there; a library that raised would make the failure invisible
    to anything that caught it.
    """
    if not ref:
        return producer_stamp(""), None
    res = index.resolve(ref)
    return lineage_columns(res), res


def _assert_coherent(cols: dict) -> None:
    """The same rule the CHECK constraint enforces, one layer earlier.

    Both layers exist on purpose. The CHECK binds a superuser and an INSERT this module never
    saw; this one names the calling code in the traceback. Neither is redundant with the other
    and neither is trusted by the other.
    """
    status, pb = cols["resolution_status"], cols["produced_by"]
    if status is None:
        return
    if status not in RESOLUTION_STATUSES:
        raise PromotionRefused("BAD_RESOLUTION_STATUS",
                               f"{status!r} not in {sorted(RESOLUTION_STATUSES)}")
    if status == STATUS_RESOLVED and pb is None:
        raise PromotionRefused("LINEAGE_INCOHERENT",
                               "resolution_status 'resolved' with produced_by NULL")
    if status != STATUS_RESOLVED and pb is not None:
        raise PromotionRefused(
            "FABRICATED_ID",
            f"resolution_status {status!r} with produced_by {pb!r}. A reference that did not "
            f"resolve never yields an id. A fabricated id is believed later, which is worse "
            f"than a null.")


# ---------------------------------------------------------------------------------------
# reading a promotion back out of git
# ---------------------------------------------------------------------------------------

@dataclass
class ProjectedReceipt:
    """One committed receipt, parsed. Everything the store needs and nothing it decides."""

    git_ref: str
    path: str
    entity_id: str
    lineage: dict = field(default_factory=dict)
    touches: list[dict] = field(default_factory=list)
    external: bool = False
    canon_touching: bool = False
    title: str = ""
    moment: str = ""      # the WAGER-14a moment, which is what `brain.receipt.action` records

    # Which repository `git_ref` lives in (task 0256, migration 19). Filled by `read_promotion`,
    # which has a repo to ask; NULL from `parse_receipt`, which is given a body and no repository
    # and must not guess one. A NULL here means "this row cannot say where its commit was supposed
    # to be", which is exactly the state the 26 pre-migration-19 rows are in -- it is never a claim
    # that the commit is the brain's.
    git_repo: str | None = None
    git_repo_ref: str | None = None

    def lineage_columns(self) -> dict:
        """The three columns as the receipt committed them."""
        cols = {
            "produced_by": self.lineage.get("produced_by"),
            "produced_by_ref": self.lineage.get("produced_by_ref"),
            "resolution_status": self.lineage.get("resolution_status"),
        }
        _assert_coherent(cols)
        return cols

    def unresolved_touches(self) -> list[dict]:
        """Edges whose component the brain could not name. They PROJECT; they are not errors.

        `touch add --allow-unresolved` commits these, because git can carry a component that did
        not resolve. Since migration 9 `brain.touch.entity_id` is nullable and the raw reference
        lands in `entity_ref` beside it, so the store carries them too and the projection is
        total over what git can hold. They stay visible in `brain.touch_unresolved`.
        """
        return [t for t in self.touches if not t.get("entity_id")]

    def unprojectable_touches(self) -> list[dict]:
        """Edges the store still cannot hold: the ones that name NO far end at all.

        Narrowed by task 0114. This used to mean "entity_id is null", back when
        `brain.touch.entity_id` was NOT NULL and a null was the whole problem; migration 9 moved
        that case to `unresolved_touches()` above. What is left is an edge carrying neither an
        id nor a reference, which points at nothing and is not an edge. `store_projection.py`
        refuses the whole receipt on one.
        """
        return [t for t in self.touches
                if not t.get("entity_id") and not t.get("entity_ref")]


def read_promotion(repo: Path, sha: str) -> ProjectedReceipt:
    """Read one committed receipt out of git. The commit, never the working tree."""
    repo = Path(repo).resolve()
    sha = _full_sha(repo, sha)
    files = [f for f in _git(repo, "show", "--name-only", "--format=", sha).split("\n") if f]
    if len(files) != 1:
        raise PromotionRefused(
            "COMMIT_SWEPT_EXTRA_FILES",
            f"{sha[:12]} contains {len(files)} files: {files}. `promote()` writes exactly one "
            f"file per promotion so there is one audit point, and a projection of a commit "
            f"that swept other work would attribute that work to this receipt.")
    path = files[0]
    body = _git(repo, "show", f"{sha}:{path}")
    parsed = parse_receipt(body, git_ref=sha, path=path)
    # Stamped here rather than inside `parse_receipt`, because this is the only function of the two
    # that has a repository to ask. `parse_receipt` is given a body and a sha and nothing else, and
    # a repository it inferred would be a guess in the column whose whole purpose is that it is not.
    parsed.git_repo, parsed.git_repo_ref = repo_identity(repo, sha)
    return parsed


def parse_receipt(body: str, git_ref: str, path: str = "") -> ProjectedReceipt:
    """Parse a rendered receipt. Split out from `read_promotion` so it is testable without git."""
    m = _FRONTMATTER_ID.search(body)
    if not m:
        raise PromotionRefused("RECEIPT_HAS_NO_ID",
                               f"{path or '<receipt>'} carries no `id:` in its frontmatter, so "
                               f"there is nothing to attribute the projected rows to.")
    entity_id = m.group(1).strip()

    lineage = {}
    for key, value in _LINEAGE_ROW.findall(body):
        lineage[key] = None if value == "null" else value

    touches: list[dict] = []
    block = _TOUCH_BLOCK.search(body)
    if block:
        parsed = yaml.safe_load(block.group(1)) or {}
        for row in parsed.get("touches") or []:
            touches.append({
                "subject_type": row.get("subject_type"),
                "subject_id": row.get("subject_id"),
                "entity_id": row.get("entity_id"),
                "component_type": row.get("component_type"),
                "orient_role": row.get("orient_role"),
                "role": row.get("role") or "load-bearing",
                "entity_ref": row.get("entity_ref"),
                "resolution_status": row.get("resolution_status"),
            })

    title = ""
    heading = re.search(r"^# Receipt:\s*(.+)$", body, re.MULTILINE)
    if heading:
        title = heading.group(1).strip()

    moment = ""
    booked = re.search(r"WAGER-14a moment `([a-z-]+)`", body)
    if booked:
        moment = booked.group(1)

    return ProjectedReceipt(
        git_ref=git_ref,
        path=path,
        entity_id=entity_id,
        lineage=lineage,
        touches=touches,
        external=str(lineage.get("external", "")).lower() == "true",
        canon_touching=str(lineage.get("canon_touching", "")).lower() == "true",
        title=title,
        moment=moment,
    )


def is_sha(value: str | None) -> bool:
    return bool(value) and bool(_SHA.match(value))


# ---------------------------------------------------------------------------------------
# which repository a git_ref belongs to (task 0256)
# ---------------------------------------------------------------------------------------

def repo_identity(repo: Path, sha: str | None = None) -> tuple[str | None, str | None]:
    """`(git_repo, git_repo_ref)` for the repository a commit lives in.

    WHY THIS EXISTS. `brain.receipt.git_ref` was a bare sha with no column naming the repository,
    and `receipt book --repo X` accepts any git repository, so a receipt booked into
    `/tmp/0149-cli-zf9k78n8` was indistinguishable in the store from one booked into the brain.
    Measured on the live store 2026-08-16: 20 of 26 distinct `git_ref`s resolved in NO repository
    on this machine, and four of those were still readable in two throwaway `/tmp` fixture repos --
    which is the cause caught in the act rather than inferred. Nothing could tell "the brain lost
    a commit" from "that commit was never the brain's", and those two need completely different
    responses.

    IDENTITY IS THE ROOT COMMIT, NOT A URL AND NOT A PATH.

      * A remote URL is absent for a local-only repo and changes when an org is renamed (this
        operation renamed one this month).
      * A filesystem path is meaningless the moment the repo moves, and every one of the 20 LOST
        shas came from a path that no longer exists.
      * A root commit is immutable, survives rename, move and re-clone, and two lanes computing
        it independently get the same string by construction rather than by spelling a name the
        same way.

    Measured on the brain repo 2026-08-17: `git rev-list --max-parents=0 main`,
    `... --all`, and `... <a scratch-branch sha>` all return exactly
    `8ef5ef63285c9c9acc2c5bdeb526e17faf7598a9`. So the identity is stable across branches, which
    is what makes it usable on a receipt booked on a scratch branch.

    `sha` scopes the walk to THAT COMMIT'S ancestry when given. That is deliberate rather than
    convenient: `--all` would fold in the roots of every scratch branch currently checked out, so
    the identity of a repository would change as branches came and went, and an identity that
    moves is not an identity.

    A repository with several root commits (a grafted or subtree-merged history) yields all of
    them, sorted and comma-joined, rather than whichever git happened to print first. Sorted
    because unsorted output makes two runs of the same check disagree.

    WHAT THIS IDENTITY IS NOT: A UNIQUENESS GUARANTEE. A commit sha is taken over the tree, the
    parents, the author, the committer, the message AND the timestamps, so two repositories whose
    first commit is byte-identical and lands in the same second get the SAME root commit and are
    therefore the same repository by this measure. Found by a test rather than by reasoning:
    `test_two_repositories_are_never_the_same_repository` failed on its first run because two
    freshly `git init`-ed fixture repos both produced
    `a6db5e6fc996cadbf1f1bd4e77c52d7973ba14f6`, and the collision is pinned as its own test now.

    For the case this column exists to answer, that is acceptable and mostly correct: a fork and
    its parent SHOULD share an identity, because a commit reachable in one is history the other
    also holds. The case it gets wrong is two unrelated repositories created in the same second
    from the same template, which is a fixture pattern rather than a working repository. It is
    stated here because an identity whose collision mode is undocumented is one somebody will
    later assume it does not have.

    `git_repo_ref` is the human-legible name beside it -- the `origin` remote URL when there is
    one, otherwise the resolved path. It is a LABEL for reading an incident, never the key, and
    nothing joins on it. NULL when there is nothing honest to put there.

    Returns `(None, None)` rather than raising when git cannot answer. A projection is about the
    receipt; failing it because the repository could not be labelled would trade a complete row
    for no row at all, and the NULL says exactly what happened.
    """
    repo = Path(repo).resolve()
    args = ["rev-list", "--max-parents=0"] + ([sha] if sha else ["HEAD"])
    try:
        roots = sorted({ln.strip() for ln in _git(repo, *args).splitlines() if ln.strip()})
    except PromotionRefused:
        return None, None
    if not roots:
        return None, None
    try:
        label = _git(repo, "remote", "get-url", "origin").strip() or str(repo)
    except PromotionRefused:
        label = str(repo)
    return ",".join(roots), label


def _full_sha(repo: Path, sha: str) -> str:
    full = _git(repo, "rev-parse", f"{sha}^{{commit}}")
    if not is_sha(full):
        raise PromotionRefused("NOT_A_COMMIT", f"{sha!r} did not resolve to a commit sha")
    return full


def _git(repo: Path, *args: str) -> str:
    p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if p.returncode != 0:
        raise PromotionRefused("GIT_FAILED",
                               f"git {' '.join(args)} exited {p.returncode}: {p.stderr.strip()}")
    return p.stdout.rstrip("\n")
