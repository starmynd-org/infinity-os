"""The write half of the seam: one transition, and it can only run downhill from git.

WHY THIS IS A TRANSITION AND NOT A FOURTH ADAPTER VERB
------------------------------------------------------
Task 0103 says: *"No new transition. Writing lineage goes through the existing verbs. If you
find yourself adding a fourth adapter verb or an INSERT outside `store.apply`, stop and post to
slot 7."* Both of those escapes were checked and neither was available, so this was posted to
slot 7 rather than decided quietly. What the measurement found:

- **`brain.touch` has no existing verb.** Nothing in the repo writes it. `grep -rn "INTO
  brain.touch"` returns nothing and `store/reads.py` only SELECTs from it -- migration 4's own
  comment says the same. So "writing lineage goes through the existing verbs" has no verb to go
  through for the half of lineage that is a set rather than a scalar.
- **No existing verb carries the lineage triple.** `post` and `artifact` take `produced_by` and
  stop there; ingest's and budget's transitions do the same. A row written through them can
  never say `ambiguous`, because there is no parameter for it. That is not a defect in their
  code -- the two columns did not exist until migration 5 -- but it means the resolved case is
  the only one the existing verbs can express today.

So this module adds exactly ONE transition. It is not a fourth verb of the adapter (the adapter
still has three, and all three write git); it is the store-side loader that the projection in
migration 4 always implied and nobody had written. Every rule the ban was protecting still
holds: one function per state change, registered once, called through `store.apply`, no INSERT
anywhere else, one verb one transaction.

THE DIRECTION IS ENFORCED, NOT DOCUMENTED
-----------------------------------------
WAGER-7 puts the component touch-edges in git and makes this table a projection. Three things
make that structural here rather than a comment somebody can violate next week:

1. **`brain.touch.git_ref` is NOT NULL** (migration 5) and this transition is the only writer.
   A touch-edge cannot exist in the store without naming the commit it came from.
2. **The transition refuses anything that is not a full sha.** Not a branch, not a tag, not
   HEAD: those move, and a projection whose source can move is not reconcilable.
3. **This module never writes git and never reads a table to build a receipt.** The parsing
   half (`store_join.py`) imports no database driver at all, so the adapter keeps working with
   Postgres stopped. That is the "losing the store costs zero knowledge" invariant expressed as
   an import graph rather than as a promise.

Drop `brain.touch` and `brain.receipt` and re-run this loader over the same commits and you get
the same rows back. That is what makes it a projection.

WHAT IT REFUSES
---------------
- a `git_ref` that is not a sha
- an edge that names NO far end at all: neither an `entity_id` nor an `entity_ref`. The whole
  projection fails rather than landing the rest and dropping that edge, because a silently
  partial projection reads as a complete one.
- a lineage triple that contradicts itself, one layer before the CHECK constraint does.

WHAT IT WILL NOT FAIL FOR, AND WHY THAT IS A RULE AND NOT A SHRUG (task 0149)
----------------------------------------------------------------------------
**A missing work item never costs a projection.** The optional `work_item_id` puts one courtesy
line on that item's thread, and `brain.thread.work_item_id` is a foreign key -- so the note used
to take the receipt row and every touch row down with it when the store did not hold the item.
The note is now written only against an item the store confirms it has, the status comes back in
the result, and a skip says so on stderr. `_announce` carries the argument.

**The receipt's own `work_item_id` is no longer promoted into that foreign key.** It names a
different registry from `brain.work_item.id` and the two collide numerically in live data, so
the key was as likely to be satisfied by the WRONG task as to be violated. `project_parsed`
carries that argument. Neither change invents a row, and neither loses a fact: the work item is
on the receipt row as `subject_type`/`subject_id` either way.

WHAT IT NO LONGER REFUSES, AND WHY THAT CHANGED (task 0114)
-----------------------------------------------------------
This module used to refuse the WHOLE receipt on meeting an edge whose `entity_id` was null --
which `touch add --allow-unresolved` can commit, because git can carry a component the brain
index could not name. `brain.touch.entity_id` was NOT NULL and the alternative to refusing was
inventing an id, so refusing was right. The cost was that a receipt booked with
`--allow-unresolved` could never be projected AT ALL, not even the resolved edges beside the
unresolved one.

Migration 9 moved the store instead. `entity_id` is nullable, `entity_ref` and
`entity_resolution_status` sit beside it exactly as `produced_by_ref` and `resolution_status`
sit beside `produced_by`, and a CHECK refuses both an id with no far end and an id written down
beside 'unresolved' or 'ambiguous'. So the null now PROJECTS, carrying the raw reference, and
the projection is total over what git can hold.

**Nothing here invents an id, and the null is what makes inventing one unnecessary.** An
unresolved edge is cleared by naming the component and booking a FOLLOW-ON receipt that
resolves (receipts are append-only, WAGER-13a), never by an UPDATE that fills in the column --
an id that exists only in the store is reconcilable against nothing. `brain.touch_unresolved`
is where those edges stay visible.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

from .index import STATUS_AMBIGUOUS, STATUS_RESOLVED, STATUS_UNRESOLVED
from .promotion import PromotionRefused
from .store_join import ProjectedReceipt, is_sha, read_promotion

# `store` lives at the repo root, one level above `adapter/`. Located rather than assumed: this
# module is imported from a package two directories down, and the alternative is requiring every
# caller to have gotten its cwd right.
_ROOT = next((p for p in Path(__file__).resolve().parents if (p / "store" / "__init__.py").exists()),
             None)
if _ROOT and str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import store  # noqa: E402  (after the path bootstrap above, on purpose)

PROJECTION_VERB = "lineage project"


@store.transition(PROJECTION_VERB, role="runtime")
def _lineage_project(ctx, *, git_ref: str, entity_id: str, path: str, moment: str,
                     subject_type: str, subject_id: str, lineage: dict, touches: list,
                     external: bool = False, canon_touching: bool = False,
                     title: str = "", work_item_id: str | None = None,
                     git_repo: str | None = None, git_repo_ref: str | None = None):
    """Load one committed promotion into the store. git -> store, and never the reverse.

    One transaction for the receipt row and every touch row together: a receipt that landed
    without its edges would read as a promotion that touched nothing, which is a lineage claim
    that is worse than the absent row.

    Idempotent by construction. Re-projecting the same commit inserts nothing and returns the
    row that is already there, so a reconciliation pass can be run as often as anyone likes
    and a crash mid-project is recovered by simply running it again.
    """
    if not is_sha(git_ref):
        raise PromotionRefused(
            "NOT_A_SHA",
            f"git_ref {git_ref!r} is not a commit sha. A projection names the immutable commit "
            f"it came from; a branch or a tag moves and a row pointing at one cannot be "
            f"reconciled against git afterwards.")

    # Pre-flight, before the first INSERT. The transaction would roll back either way, but
    # checking every edge up front is what makes the refusal a statement about the RECEIPT
    # rather than about whichever edge happened to be inserted fifth.
    unprojectable = [t for t in touches if not t.get("entity_id") and not t.get("entity_ref")]
    if unprojectable:
        raise PromotionRefused(
            "UNPROJECTABLE_TOUCH",
            f"{len(unprojectable)} edge(s) in {git_ref[:12]} name no far end at all: neither an "
            f"entity_id nor an entity_ref. An edge that points at nothing is not an edge. "
            f"Refusing the WHOLE projection rather than landing the rest, because a partial "
            f"projection is indistinguishable from a complete one on read. An UNRESOLVED "
            f"component is fine and projects with its raw reference (migration 9); this is a "
            f"receipt that recorded no reference to preserve.")

    unexplained = [t for t in touches
                   if not t.get("entity_id")
                   and t.get("resolution_status") not in (STATUS_UNRESOLVED, STATUS_AMBIGUOUS)]
    if unexplained:
        raise PromotionRefused(
            "TOUCH_UNRESOLVED_WITHOUT_REASON",
            f"{len(unexplained)} edge(s) in {git_ref[:12]} carry a null entity_id without saying "
            f"which failure it was (refs: {[t.get('entity_ref') for t in unexplained]}). "
            f"'unresolved' and 'ambiguous' are distinct on purpose -- ambiguous has candidates "
            f"and is FIXABLE by disambiguating, unresolved has none -- and collapsing them "
            f"destroys that. `receipt book` always writes one of the two beside a null id, so a "
            f"receipt missing it was hand-written or is damaged; read the commit rather than "
            f"picking a status here.")

    # Which repository the sha lives in (task 0256, migration 19). Written when the store has the
    # columns and skipped, loudly, when it does not. See `_repo_identity_columns` for why this is
    # a runtime check rather than a hard requirement on migration 19.
    attribution = _repo_identity_columns(ctx, git_ref, git_repo, git_repo_ref)

    existing = ctx.one("SELECT * FROM brain.receipt WHERE git_ref = %s", (git_ref,))
    if existing:
        # No note on this path, and that is not an oversight. The note announces the ACT of
        # projecting; re-running the loader is not a second act, and `already_projected` is how
        # a re-run reports itself. The cost is that a note skipped because the work item was
        # absent is never written later, once the item lands -- accepted, because the
        # alternative is a loader whose output depends on when you run it.
        return {"receipt": existing, "touches_inserted": 0, "touches_already_present":
                len(touches), "already_projected": True, "thread_note": "already-projected"}

    receipt_row = ctx.one(
        "INSERT INTO brain.receipt "
        "  (action, subject_type, subject_id, detail, booked_by, approval_ref, git_ref, "
        "   external, canon_touching, actor_type, produced_by, produced_by_ref, "
        "   resolution_status" + attribution.columns + ") "
        "VALUES (%(action)s, %(subject_type)s, %(subject_id)s, %(detail)s, %(booked_by)s, "
        "        %(approval_ref)s, %(git_ref)s, %(external)s, %(canon_touching)s, "
        "        %(actor_type)s, %(produced_by)s, %(produced_by_ref)s, %(resolution_status)s"
        + attribution.placeholders + ") "
        "RETURNING *",
        attribution.params | {
            "action": moment or "result-produced",
            "subject_type": subject_type,
            "subject_id": subject_id,
            "detail": f"{title} [{path}]".strip(),
            "booked_by": ctx.actor,
            "approval_ref": lineage.get("approval_ref"),
            "git_ref": git_ref,
            "external": external,
            "canon_touching": canon_touching,
            "actor_type": lineage.get("actor_type"),
            "produced_by": lineage.get("produced_by"),
            "produced_by_ref": lineage.get("produced_by_ref"),
            "resolution_status": lineage.get("resolution_status"),
        },
    )

    inserted = 0
    for t in touches:
        # ON CONFLICT on the edge's identity, which migration 4 widened to match git's and made
        # NULLS NOT DISTINCT so that two D00-shaped rows still collide. DO NOTHING rather than
        # DO UPDATE: the first commit to state an edge is the one that states it, and a later
        # receipt repeating the same edge is not a correction.
        row = ctx.one(
            "INSERT INTO brain.touch "
            "  (subject_type, subject_id, entity_id, entity_ref, entity_resolution_status, "
            "   component_type, orient_role, role, "
            "   git_ref, produced_by, produced_by_ref, resolution_status"
            + attribution.columns + ") "
            "VALUES (%(subject_type)s, %(subject_id)s, %(entity_id)s, %(entity_ref)s, "
            "        %(entity_resolution_status)s, %(component_type)s, "
            "        %(orient_role)s, %(role)s, %(git_ref)s, %(produced_by)s, "
            "        %(produced_by_ref)s, %(resolution_status)s"
            + attribution.placeholders + ") "
            "ON CONFLICT ON CONSTRAINT touch_edge_key DO NOTHING "
            "RETURNING *",
            attribution.params | {
                "subject_type": t.get("subject_type") or subject_type,
                "subject_id": t.get("subject_id") or subject_id,
                "entity_id": t.get("entity_id"),
                # The far end as git wrote it. Copied, never derived: `entity_ref` is what the
                # receipt asked for and `entity_id` is what the index answered, and the whole
                # point of keeping both is that a later reader can tell them apart.
                "entity_ref": t.get("entity_ref"),
                # `_entity_status` rather than a bare `t.get(...)`: git omits the status on a
                # resolved edge, and the CHECK requires an unresolved edge to state its reason.
                "entity_resolution_status": _entity_status(t),
                "component_type": t.get("component_type"),
                "orient_role": t.get("orient_role"),
                "role": t.get("role") or "load-bearing",
                "git_ref": git_ref,
                # The edge's producer is the receipt that committed it. `entity_id` is the
                # component that was TOUCHED; `produced_by` is what RECORDED the touching.
                #
                # 'resolved' here is not an assumption. The id was read out of the `id:`
                # frontmatter of a file that exists at `path` in commit `git_ref`, which is a
                # stronger check than an index lookup: an index is a snapshot and can be stale,
                # while the commit is the node. Nothing is fabricated and nothing is guessed.
                "produced_by": entity_id,
                "produced_by_ref": path,
                "resolution_status": "resolved",
            },
        )
        if row:
            inserted += 1

    return {
        "receipt": receipt_row,
        "touches_inserted": inserted,
        "touches_already_present": len(touches) - inserted,
        "already_projected": False,
        "thread_note": _announce(ctx, work_item_id, git_ref, entity_id, inserted),
        # Reported so a caller can tell an ATTRIBUTED projection from an unattributed one without
        # querying the store back. `false` here is the state that produced task 0256: a row whose
        # git_ref could have come from anywhere.
        "attributed": attribution.present,
        "git_repo": git_repo if attribution.present else None,
    }


def _announce(ctx, work_item_id: str | None, git_ref: str, entity_id: str, inserted: int) -> str:
    """Put a line on the work item's thread, if there is a work item to put it on.

    THE NOTE IS A NOTIFICATION AND THE PROJECTION MUST NOT DEPEND ON IT (task 0149). It used to
    be a bare `ctx.thread(...)`, and `brain.thread.work_item_id` has a foreign key to
    `brain.work_item`, so a receipt naming a work item the store does not hold raised
    `psycopg2.errors.ForeignKeyViolation` -- a RAW DRIVER exception, past every caller catching
    the documented `PromotionRefused` -- and rolled back the receipt row and every touch row with
    it. Measured on the live store: 5 receipts and 7 touches before, 5 and 7 after.

    Best-effort rather than a refusal, for reasons this module already argues elsewhere:

    1. **The note carries no lineage fact.** The work item is ALREADY on the receipt row, in
       `subject_type`/`subject_id`, as free text with no foreign key -- so a projection whose
       note was skipped has lost exactly nothing. Compare the touch-edge case above, where
       refusing was right because the alternative was inventing an id.
    2. **A projection is a fact about git.** Making it depend on the store's queue being
       populated breaks "drop the tables, re-run the loader, get the same rows back", which is
       the sentence at the top of this file that defines what a projection IS. `brain.thread`
       is `ON DELETE CASCADE` and the engine's own note (`swarm_engine/transitions.py:1204`)
       says the retention sweep removes work items -- so even a CORRECT id goes missing in time,
       and a loader that crashes on it is not re-runnable.
    3. A refusal would make a receipt booked with `--work-item-id` unprojectable ENTIRELY,
       which is the exact defect migration 9 had just finished removing one seam over.

    SKIPPED IS NOT SILENT. The status is in the return value and a line goes to stderr, because
    a courtesy that vanishes without a word is how the next lane inherits this same hour.

    The existence check is a pre-flight SELECT and not a `try`/`except` around the INSERT,
    matching `swarm_engine/transitions.py:385`. That is not a style preference: a Postgres error
    aborts the whole transaction, so catching the violation still leaves every later statement --
    including the COMMIT -- failing.
    """
    if not work_item_id:
        return "not-requested"
    if not ctx.scalar("SELECT 1 FROM brain.work_item WHERE id = %s", (work_item_id,)):
        # "is committing", not "landed": this runs INSIDE the transaction, and a line claiming a
        # commit that has not happened yet is the confidently-wrong shape the runtime exists to
        # avoid. What is true here is that nothing was abandoned.
        print(f"store_projection: git {git_ref[:12]} (receipt {entity_id}) is projecting WITHOUT a "
              f"thread note: brain.work_item has no row {work_item_id!r}. The receipt row and its "
              f"touch rows are in this transaction and commit with it; only the notification is "
              f"dropped. Nothing is invented to make the note fit.", file=sys.stderr)
        return "no-such-work-item"
    ctx.thread(
        work_item_id, "artifact",
        f"lineage projected from git {git_ref[:12]}: receipt {entity_id}, "
        f"{inserted} touch-edge(s). git is the source (WAGER-7); these rows are a copy.")
    return "written"


@dataclass(frozen=True)
class _Attribution:
    """The repo-identity half of an INSERT, or nothing at all on a store older than migration 19."""

    columns: str = ""        # ", git_repo, git_repo_ref" or ""
    placeholders: str = ""   # ", %(git_repo)s, %(git_repo_ref)s" or ""
    params: dict = field(default_factory=dict)
    present: bool = False


def _repo_identity_columns(ctx, git_ref: str, git_repo: str | None,
                           git_repo_ref: str | None) -> _Attribution:
    """Add `git_repo`/`git_repo_ref` to the INSERTs, if the store has them yet (task 0256).

    WHY THIS IS A RUNTIME CHECK AND NOT A HARD REQUIREMENT ON MIGRATION 19.

    A projection is a fact about git, and `lineage project` is the ONLY route from a booked
    receipt into the store. If this module simply named the two new columns, then every store
    that has not applied migration 19 yet -- which on 2026-08-17 includes the live `brain`
    database, whose ledger stops at version 18 -- would answer `column "git_repo" does not exist`
    and projection would stop working entirely. Trading a working loader for an attributed one is
    a worse store than the one this task set out to fix, and it would arrive as a raw
    `psycopg2.errors.UndefinedColumn` past every caller that catches `PromotionRefused`.

    So: attributed where the store can hold it, unattributed and LOUD where it cannot, and the
    same code does both. The cost is one `information_schema` lookup per projection, inside the
    transaction that is about to write, so the answer cannot go stale between the check and the
    INSERT.

    SKIPPED IS NEVER SILENT, for the reason `_announce` gives one seam over: a courtesy that
    vanishes without a word is how the next lane inherits this same hour. A store missing the
    columns says so on stderr and names the migration that fixes it.

    NOTHING IS BACKFILLED, and the `already_projected` path above deliberately returns before this
    matters. Re-running the loader over a row booked before migration 19 does NOT fill its
    `git_repo` in: this module has no UPDATE and gains none here. Those 26 rows were booked when
    no repository was recorded, so their repository is genuinely unknown, and writing "the brain"
    into them would invent a fact -- one the reconciliation proves would be FALSE for at least the
    four shas that were still readable in `/tmp` fixture repos. The way a row gets attributed is
    the way every other fact gets into this store: a receipt is booked, and it is projected.
    """
    have = {
        r["table_name"]
        for r in ctx.execute(
            "SELECT table_name FROM information_schema.columns "
            "WHERE table_schema='brain' AND column_name='git_repo' "
            "  AND table_name IN ('receipt','touch')"
        )
    }
    if have < {"receipt", "touch"}:
        print(f"store_projection: git {git_ref[:12]} is projecting UNATTRIBUTED. brain.receipt and "
              f"brain.touch do not both carry `git_repo` on this store (found: "
              f"{sorted(have) or 'neither'}), so the repository this commit came from cannot be "
              f"recorded and the row will be indistinguishable from one booked in /tmp. Apply "
              f"migrations/0019_git_ref_repo_identity.sql. The receipt and its edges are written "
              f"either way; only the attribution is dropped.", file=sys.stderr)
        return _Attribution()
    return _Attribution(
        columns=", git_repo, git_repo_ref",
        placeholders=", %(git_repo)s, %(git_repo_ref)s",
        params={"git_repo": git_repo, "git_repo_ref": git_repo_ref},
        present=True,
    )


def _entity_status(t: dict) -> str:
    """What the edge says about its own far end, in `brain.touch.entity_resolution_status`.

    THE KEY NAMES DO NOT LINE UP AND THE MISMATCH IS THE POINT. Inside a touch block git calls
    this field `resolution_status`, because there it is the only status on the row. In the
    store the row carries TWO: `resolution_status` is about `produced_by` -- what RECORDED the
    touching -- and `entity_resolution_status` is about `entity_id` -- what was TOUCHED. Reading
    one for the other is the mistake migration 9's column comments exist to prevent, so the
    translation happens here, once, in a named function rather than inline.

    A resolved edge is stamped 'resolved' rather than left NULL. git omits the field there
    (`Touch.to_yaml_row` writes it only beside a null id), but the edge naming a component id IS
    the resolution, and NULL means something else on this column: predates migration 9, or never
    said. Callers are pre-flighted above, so a null id always arrives with its reason.
    """
    if t.get("entity_id"):
        return STATUS_RESOLVED
    return t.get("resolution_status")


def project(repo: Path, sha: str, actor: str = "adapter",
            work_item_id: str | None = None, allow_undeclared_repo: bool = False) -> dict:
    """Read one committed promotion out of git and load it. The whole seam, in one call.

    THE SECOND HALF OF THE DURABILITY GATE, and it is not a duplicate of the one in `promote()`.
    That one guards the door this lane owns. This one guards the ROW, and the row is what asserts
    the commit exists: every one of the 26 provisional `git_ref`s task 0297 measured arrived here,
    and nine of them were committed with raw `git commit` by a fixture builder that never called
    `promote()` at all (`adapter/tools/work_item_note_proof.py`). A gate only at the door would
    still let those through. A store row naming a commit nobody keeps is the defect; this is where
    the store stops accepting one.
    """
    from .durability import durability_of, policy_for

    parsed = read_promotion(repo, sha)
    pol = policy_for(repo)
    if not pol.matched and not allow_undeclared_repo:
        # A DURABLE REF IN A REPOSITORY NOBODY KEEPS IS NOT DURABLE, and the branch name proves
        # nothing: any `mkdtemp` repo can `git init -b receipts/durable`. Every one of the 26
        # provisional rows came from a repository the operation never declared -- five fixture
        # repos, two under /tmp -- so this is the shape of the original defect, not a hypothetical.
        # The escape exists and is loud, because a fixture proving the projection door is a real
        # use and hiding it behind a silent default is how the door stopped being watched.
        raise PromotionRefused(
            "UNDECLARED_REPO",
            f"{repo} is not a repository {pol.source} declares (identity {pol.identity}). A ref "
            f"is only as durable as the repository holding it. Pass allow_undeclared_repo / "
            f"--allow-undeclared-repo if this is a fixture and you accept that the row will "
            f"outlive the commit it names.")
    tier, dref = durability_of(repo, parsed.git_ref, pol)
    if tier is None:
        raise PromotionRefused(
            "REF_NOT_DURABLE",
            f"{parsed.git_ref[:12]} is not reachable from any durable ref in {repo} "
            f"({[c['ref'] for c in pol.durable_refs()] or 'none declared exist here'}, per "
            f"{pol.source}). Refusing to write a row that asserts a commit nobody keeps: 20 of "
            f"the 26 rows already in brain.receipt/brain.touch say exactly that, and 6 of those "
            f"point at nothing anywhere on this machine. Land the commit on a durable ref first.")
    if not pol.matched:
        print(f"store_projection: PROJECTING FROM AN UNDECLARED REPOSITORY ({repo}). The ref is "
              f"durable there ({tier} / {dref}) and that repository is not one "
              f"{pol.source} declares, so nothing keeps it. This row will outlive the commit it "
              f"names. Migration 19 records the repository identity on the row, so the row can at "
              f"least say where it came from -- which the 26 rows booked before it cannot.",
              file=sys.stderr)
    return project_parsed(parsed, actor=actor, work_item_id=work_item_id)


def project_parsed(parsed: ProjectedReceipt, actor: str = "adapter",
                   work_item_id: str | None = None) -> dict:
    """Load a parsed receipt. `work_item_id` is a `brain.work_item` id and only the CALLER has one.

    THE RECEIPT'S OWN `work_item_id` IS NOT PASSED HERE, AND THAT IS THE FIX, NOT AN OMISSION
    (task 0149). This used to fall back to `parsed.lineage.get("work_item_id")` when the caller
    passed none. Those two strings look identical -- four digits -- and name different registries:

      * `brain.work_item.id` has `DEFAULT next_item_id()`, a STORE-LOCAL sequence, and its only
        writer is the `post` verb (`swarm_engine/transitions.py:416`), which never supplies one.
      * `lineage.work_item_id` is whatever queue booked the receipt. Tonight that is the file bus
        at `~/.swarm`, which allocates its own numbers.

    Measured on the live store: the store's work items `0013, 0026, 0027, 0029, 0030, 0031, 0032,
    0033, 0037` ALL exist on the file bus too, as entirely different tasks -- store `0013` is
    "0103 seam proof: a resolved produced_by...", bus `0013` is "Measure the client AIOS
    self-service path...". So the foreign-key crash 0149 was filed about was the LUCKY half. The
    unlucky half is the key being satisfied BY COINCIDENCE: a receipt booked against bus `0037`
    would have written its lineage note onto the store's unrelated task `0037` and exited 0.

    0149 bans fixing this by inserting a `brain.work_item` row to satisfy the key, because that
    fabricates queue state. Letting a colliding number satisfy it is the same fabrication with no
    INSERT, so the assignment is the thing that stops. A caller that knows which registry its id
    came from still passes it and still gets the note; `lineage project --work-item-id` and
    `tools/lineage_join_proof.py` both do exactly that. Nothing is lost from the store either
    way: the work item is on the receipt row as `subject_type`/`subject_id` regardless, put there
    by `_subject_of` below, and it stays in git.

    `brain.work_item.canonical_task` is the column a foreign task id belongs in. It is NULL on
    every row today, so there is no way to match a bus id to a store row -- and asserting an
    identity that cannot be checked is what this refuses to do.
    """
    subject_type, subject_id = _subject_of(parsed)
    result = store.apply(
        PROJECTION_VERB,
        actor=actor,
        git_ref=parsed.git_ref,
        entity_id=parsed.entity_id,
        path=parsed.path,
        moment=parsed.moment,
        subject_type=subject_type,
        subject_id=subject_id,
        lineage=parsed.lineage_columns() | {
            "actor_type": parsed.lineage.get("actor_type"),
            "approval_ref": parsed.lineage.get("approval_ref"),
        },
        touches=parsed.touches,
        external=parsed.external,
        canon_touching=parsed.canon_touching,
        title=parsed.title,
        work_item_id=work_item_id,
        # Taken off the parsed receipt, which got it from the repository `read_promotion` was
        # pointed at. NOT recomputed here and NOT defaulted to the configured brain: this function
        # also serves `parse_receipt`-built receipts that never had a repository, and defaulting
        # would write "the brain" onto a row nobody proved was the brain's -- the exact fabrication
        # migration 19's NULL exists to avoid.
        git_repo=parsed.git_repo,
        git_repo_ref=parsed.git_repo_ref,
    )
    # Reported, not used. A reader of this dict sees the work item the RECEIPT named beside the
    # note the STORE did or did not write, which is the whole registry question in two adjacent
    # keys instead of an absence nobody can see.
    result["lineage_work_item_id"] = parsed.lineage.get("work_item_id")
    return result


def _subject_of(parsed: ProjectedReceipt) -> tuple[str, str]:
    """What the receipt is about, taken from its edges and falling back to itself.

    The edges carry the subject the receipt declared, so reading it from them keeps the receipt
    row and its touch rows pointing at the same thing. A receipt with no edges is about itself.
    """
    for t in parsed.touches:
        if t.get("subject_type") and t.get("subject_id"):
            return t["subject_type"], t["subject_id"]
    return "receipt", parsed.entity_id
