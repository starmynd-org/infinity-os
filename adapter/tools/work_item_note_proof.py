#!/usr/bin/env python3
"""The projection's thread note, exercised against a live store. Task 0149.

`adapter/tests/test_store_join.py` runs with Postgres stopped on purpose, so the constraint this
task is about is not reachable from it: `brain.thread.work_item_id REFERENCES brain.work_item(id)`
is a fact the database holds, and only the database can be asked. This is the companion the write
half needs, in the same place and for the same reason as `lineage_join_proof.py` (task 0103).

It proves the four paths a projection can take through `store_projection._announce`:

  A. a work item the store does NOT hold  -> the projection LANDS, note skipped, said out loud
  B. a work item the store DOES hold      -> the projection lands and the note is on its thread
  C. no work item asked for at all        -> the projection lands, `not-requested`
  D. the same commit projected twice      -> `already-projected`, nothing inserted

Before 0149, A and C both raised `psycopg2.errors.ForeignKeyViolation` -- a raw driver exception,
past every caller catching the documented `PromotionRefused` -- and rolled the receipt row and
every touch row back with it. C is the one that surprises: the caller passed nothing, and
`project_parsed` fell back to the id the RECEIPT named.

    python3 adapter/tools/work_item_note_proof.py [--work-item ID] [--clean]

IT BUILDS ITS OWN COMMITS, in a throwaway git repo under `~/.cache` (see `--scratch`, and the
comment on `root` in `main` for why it is not the system temp directory), and that is load-bearing
rather than convenient:

  * **Each case needs its OWN commit.** The loader is idempotent and the `already_projected`
    early return fires BEFORE the note is considered at all, so a case C that re-used case A's
    commit reports `already-projected` and passes without reaching the code it exists to test.
    Written that way first, and caught only by asking what the assertion would have done against
    the unfixed module.
  * **Each case needs an edge the store does not already hold.** `touch_edge_key` is UNIQUE on
    (subject_type, subject_id, entity_key, component_type, orient_role, role), so a fixture
    restating a known edge lands its receipt, inserts zero touch rows, and sails past a
    "touch count went up" assertion. `orient_role` has only four legal values, so varying THAT
    across runs gives this tool four runs and then silent nonsense. The run token below goes in
    `subject_id` instead, which is free text, so every run states genuinely new edges.

The scratch repo is git the tool made; it never writes the brain's. **It is KEPT by default, and
that is not tidiness in reverse.** Each run writes three `brain.receipt` rows and three
`brain.touch` rows permanently -- the same cost `lineage_join_proof.py` has, and the reason both
live in `tools/` rather than in the test suite -- and every one of those rows carries the
commit's sha in `git_ref`. Deleting the repo makes those rows name a commit that exists nowhere,
which is precisely the reconciliation this module's whole doctrine is built on. The first version
of this tool deleted it. `--clean` is there for a run you have decided to throw away, and it says
what it is throwing away.

WHAT TASKS 0349 AND 0358 CHANGED HERE, AND WHY THE FIXTURE IS STILL A THROWAWAY REPO
------------------------------------------------------------------------------------
`project()` refuses two things it used to accept, and this tool tripped both. The branch is
`receipts/durable` rather than `scratch/0149-<token>` because a git_ref that is not reachable from
a declared durable ref is refused outright (`REF_NOT_DURABLE`, no escape), and the `default` entry
in `policy/durable-refs.json` names that branch for a repository the declaration has never heard
of. The second refusal is `UNDECLARED_REPO`, because a fixture repo under `~/.cache` is by
construction not a repository the shipped policy declares.

0349 answered that second one with `allow_undeclared_repo=True` at every call site. Task 0358
replaced the waiver with a DECLARATION: `declare_fixture` below writes a policy file naming this
run's fixture by its own root commit and points `BRAIN_DURABLE_POLICY` at it, the way
`adapter/tests/test_adapter.py::TempRepo._declare` does. The four projections then go through the
gate in the configuration production uses -- matched policy, no escape kwarg -- instead of through
the hole cut for them.

THE DECLARATION IS CLASSIFICATION, NOT PERMISSION, and that distinction is the whole reason this
is not a loosening. The entry names `ledger_refs` and leaves `book_refs` EMPTY, so
`durability.assert_bookable` still refuses this repository (`NO_BOOKABLE_REF`) and there is still
no path by which a `~/.cache` fixture books a receipt. `policy/durable-refs.json` draws that same
line in its own `rules` block for `default.ledger_refs`: reading a ref is not writing one. The
shipped policy is never edited, the run policy lives inside the run directory and dies with it,
and `store/bin/brain-git-ref-census.py` and `store/bin/brain-receipt-reconcile.py` both read the
SHIPPED file -- so every row this tool writes is still counted as coming from a repository the
operation does not declare. Migration 19 records the repository identity on the row either way;
nothing about what the store holds changed here.

WHAT DID CHANGE IS WHERE THE NOISE COMES FROM. `store_projection` printed PROJECTING FROM AN
UNDECLARED REPOSITORY on each of the four calls; that line is gone because the repository is now
declared, so `declare_fixture` prints the same fact once, at the moment it declares. Deleting that
print would leave a fixture that writes to the live store and says nothing about it, which is the
exact shape of the defect this whole gate exists to end.

None of it makes the fixture durable and none of it is meant to. **This tool is the origin of 9
of the 26 provisional rows task 0297 measured, including the 6 that now point at nothing anywhere:
`work_item:9149-6a81f96a-*` and `work_item:9149-6a81fb08-*`, whose repos are gone and which are
tombstoned on `receipts/durable`.** The declaration is loud in exactly the way those rows were not:
`declare_fixture` states at the top of every run that it just declared a throwaway repository and
that nothing keeps it, and migration 19 records the repo identity on the row, so a future
census can say where the commit was supposed to be. What is fixed is that the row now says it, not
that the fixture became permanent. Run with `--clean` and the rows this run wrote are orphans
again, on purpose and on the record.

The fixture is NOT booked through `promote()`, and that is a measured limit rather than an
oversight: `promote()` calls `assert_bookable()`, which refuses an undeclared repository with no
flag to override it, so no code path exists that would let a `~/.cache` fixture book through the
door. Booking there would mean writing into the operator's real brain, which is the one thing this
tool has never done.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_HERE = Path(__file__).resolve()
_ROOT = next(p for p in _HERE.parents if (p / "store" / "__init__.py").exists())
sys.path[:0] = [str(_ROOT), str(_ROOT / "adapter"), str(_ROOT / "engine")]

import store  # noqa: E402
from brain_adapter.durability import ENV_POLICY, load_policy, repo_identity  # noqa: E402
from brain_adapter.promotion import Lineage  # noqa: E402
from brain_adapter.receipt import Receipt, Touch  # noqa: E402
from brain_adapter.store_join import read_promotion  # noqa: E402
from brain_adapter.store_projection import project  # noqa: E402

#: An id the store cannot hold. Four digits, because the whole point of 0149 is that the SHAPE is
#: all these two registries share, and high enough that `next_item_id()` will not reach it here.
#: Checked against the store before it is used rather than assumed.
ABSENT = "9149"

#: The component every fixture edge names. A real entity id from the brain's own canon, so no
#: fixture ever writes a component that does not exist -- the receipt is synthetic, the far end
#: it points at is not.
COMPONENT = "knowledge-ai-architecture-surface-boundary"


def rule(n: str, title: str) -> None:
    print(f"\n{'=' * 78}\n{n}. {title}\n{'=' * 78}")


def counts(s) -> dict:
    return {t: s.scalar(f"SELECT count(*) FROM brain.{t}") for t in ("receipt", "touch", "thread")}


def _git(repo: Path, *args: str) -> str:
    p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if p.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed in {repo}:\n{p.stderr}")
    return p.stdout.strip()


def build_fixture(repo: Path, token: str, names_work_item: str) -> dict:
    """Three receipts, three commits, three distinct edges. Returns {case: sha}.

    Every receipt's lineage NAMES a work item (`names_work_item`), which is what makes case C a
    test rather than a tautology: the old fallback reached for exactly this field.
    """
    (repo / "departments" / "warner-sandbox" / "receipts").mkdir(parents=True, exist_ok=True)
    # `receipts/durable`, not `scratch/0149-<token>`, since task 0349. `project()` refuses a
    # git_ref that no declared durable ref reaches (`REF_NOT_DURABLE`) and there is no flag that
    # opens that gate; the `default` block of `policy/durable-refs.json` names this branch as a
    # ledger ref for any repository the declaration does not know. The token no longer needs to be
    # in the branch name because each run gets its own fixture repo, and it still rides in the slug
    # and in `subject_id`, which is where it was doing the work that mattered.
    _git(repo, "init", "-q", "-b", "receipts/durable")
    _git(repo, "config", "user.email", "proof@0149")
    _git(repo, "config", "user.name", "work_item_note_proof")

    shas = {}
    for case, orient_role in (("A", "tradition"), ("B", "previous-experience"),
                              ("C", "analysis-synthesis")):
        slug = f"0149-proof-{token}-{case.lower()}"
        receipt = Receipt(
            department="warner-sandbox", date="2026-08-16", slug=slug,
            title=f"0149 thread-note proof, case {case}", moment="result-produced",
            summary="Evidence for task 0149. Not work; a fixture this tool committed itself.",
            lineage=Lineage(produced_by=COMPONENT, produced_by_ref="surface-boundary",
                            resolution_status="resolved", actor_type="ai",
                            work_item_id=names_work_item),
            touches=[Touch(entity_id=COMPONENT, component_type="knowledge",
                           orient_role=orient_role)],
            # The run token rides in the SUBJECT, not in `orient_role`, so consecutive runs state
            # edges that differ in a column with no four-value vocabulary to run out of.
            subject_type="work_item", subject_id=f"{names_work_item}-{token}-{case.lower()}",
        )
        target = repo / "departments" / "warner-sandbox" / "receipts" / f"2026-08-16-{slug}.md"
        target.write_text(receipt.render(), encoding="utf-8")
        _git(repo, "add", str(target.relative_to(repo)))
        _git(repo, "commit", "-qm", f"0149 proof fixture, case {case}")
        shas[case] = _git(repo, "rev-parse", "HEAD")
    return shas


def declare_fixture(repo: Path, run_dir: Path, token: str) -> Path:
    """Name THIS fixture in its own policy file, keyed by the root commit it actually has.

    Task 0358. A fixture earns its way past `UNDECLARED_REPO` the same way the brain did -- by
    being declared, in data, under an identity nobody can spell by accident -- rather than by a
    kwarg at the call site that waives the gate. `adapter/tests/test_adapter.py::TempRepo._declare`
    is the same move for the same reason; this is its one live-store sibling.

    THE ENTRY IS DELIBERATELY UNBOOKABLE. `book_refs` is empty, so `assert_bookable` refuses this
    repository (`NO_BOOKABLE_REF`) and `promote()` / `receipt book` still cannot reach it. What the
    entry buys is CLASSIFICATION: `project()` may read a commit here and say which ref keeps it.
    `policy/durable-refs.json` already draws exactly that line for `default.ledger_refs` -- "it is
    CLASSIFICATION, not permission ... reading a ref is not writing one" -- and this stays on the
    same side of it. A fixture that could book would be writing the operator's real brain, which is
    the one thing this tool has never done.

    IT EXTENDS THE DECLARATION IN EFFECT, IT DOES NOT REPLACE IT. `load_policy()` reads whatever
    `BRAIN_DURABLE_POLICY` already names, or the shipped file; the copy written here is that
    document plus one entry. So pointing the process at the copy takes nothing away from anything
    else running in it, and the shipped file is never edited -- widening THAT is a reviewed diff,
    which is the property the policy file exists to have.

    The file is written into the run directory and NOT into the fixture repo: a policy inside the
    repo would be an untracked file in a tree this tool commits into by hand, and it would survive
    a `--clean` that deleted only the repo.
    """
    ident = repo_identity(repo)
    if not ident:
        raise SystemExit(f"no root commit readable in {repo}; the fixture must be committed "
                         f"before it can be declared (identity IS the root commit).")
    doc = load_policy()                       # the declaration in effect: env, else the shipped file
    source = doc.get("_path", "?")
    if ident in (doc.get("repos") or {}):
        # A fresh `git init` cannot collide with a declared root commit, so this firing means the
        # fixture is not fresh -- pointed at a real repository by --scratch, most likely. Refuse
        # rather than silently rewrite a real repository's entry.
        raise SystemExit(f"{repo} already has an entry in {source} (identity {ident[:12]}). This "
                         f"tool declares fixtures it built itself; it will not restate a "
                         f"repository the operation already declares.")

    doc = {k: v for k, v in doc.items() if k != "_path"}
    doc["repos"] = dict(doc.get("repos") or {})
    doc["repos"][ident] = {
        "name": f"0149 proof fixture {token} -- THROWAWAY, run-scoped",
        "what_this_is": [
            f"Written by {Path(__file__).name} at {token} for one run, at {repo}.",
            "Declared so project() reads this commit through its normal matched-policy path",
            "instead of through allow_undeclared_repo. book_refs is EMPTY on purpose: this is",
            "classification, not permission, and assert_bookable still refuses this repository.",
            "It is NOT durable. It is a temp directory. If you are reading this file somewhere",
            "other than the run directory it was written in, it has outlived its purpose and",
            "the only correct thing to do with it is delete it.",
        ],
        # AN EXPLICIT [] HERE IS INHERITANCE, NOT A REFUSAL. `policy_for` reads
        # `entry.get(k) or base.get(k)`, and [] is falsy, so each of these three falls back to the
        # `default` block. `canon_refs` is harmless -- the fixture has no main/master and
        # `expand_refs` drops what does not resolve. `book_refs` is the one that carries weight:
        # this entry is unbookable because `default.book_refs` is EMPTY in the shipped policy, not
        # because the [] below is honoured. Measured, not assumed: `assert_bookable` against a
        # fixture declared this way raises NO_BOOKABLE_REF. If `default.book_refs` is ever
        # populated, re-check this line first. Posted as swarm task 0381.
        "canon_refs": [],
        "ledger_refs": ["receipts/durable"],
        "book_refs": [],
        "tombstones": None,
    }
    path = run_dir / "durable-refs.json"
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    os.environ[ENV_POLICY] = str(path)
    print(f"declared     : {path}\n"
          f"               (a copy of {source} plus this fixture, keyed by root commit "
          f"{ident[:12]})")
    # The loud line, once, in the tool. Until 0358 `store_projection` printed the equivalent on
    # every projection because the repo was undeclared. It is declared now, so it prints nothing,
    # and a fixture that writes the live store and says nothing about it is what this replaces.
    print(f"\n  THIS RUN DECLARES A THROWAWAY REPOSITORY. The declaration lives in the run\n"
          f"  directory and dies with it, book_refs is empty so nothing here can book, and\n"
          f"  {Path(source).name} is untouched -- so brain-git-ref-census.py and\n"
          f"  brain-receipt-reconcile.py still count every row this run writes as coming from a\n"
          f"  repository the operation does not declare. The rows outlive the commits they name.")
    return path


def pick_work_item(explicit: str | None, agent: str) -> str:
    """A work item the store REALLY holds, for case B. Its id comes from the store, never from us.

    `--work-item` exists because `post` is currently broken on the live store: it writes
    `brain.work_item.brief`, and `migrations/0014_work_item_brief.sql` is not in the ledger
    (task 0201). So this falls back to an existing row rather than making case B unreachable, and
    it REFUSES to invent one either way -- 0149's whole point is that a `work_item` row conjured
    to satisfy a foreign key is fabricated queue state.
    """
    if explicit:
        with store.read() as s:
            if not s.scalar("SELECT 1 FROM brain.work_item WHERE id = %s", (explicit,)):
                raise SystemExit(f"--work-item {explicit!r} is not in brain.work_item. Case B "
                                 f"needs a row that exists; this tool will not create one.")
        return explicit
    try:
        from swarm_engine import transitions as _verbs  # noqa: F401  (registers `post`)
        os.environ.pop("SWARM_PARENT_TASK", None)  # else this inherits whatever task is running
        item = store.apply("post", actor=agent, lane="adapter", posted_by=agent, priority=0,
                           title="0149 proof: a work item the store really holds "
                                 "(evidence, not work)")
        return item["id"]
    except Exception as exc:  # noqa: BLE001 -- reported in full, then turned into an instruction
        raise SystemExit(
            f"could not post a work item for case B ({exc.__class__.__name__}: {exc}).\n"
            f"If that is task 0201 (post writes work_item.brief, migration 0014 unapplied), pass "
            f"an existing row instead:\n    --work-item <id from brain.work_item>") from None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work-item", help="an existing brain.work_item id to use for case B")
    ap.add_argument("--agent", default="T2")
    ap.add_argument("--scratch", help="where to put this run's directory, which holds the "
                                      "fixture repo and its policy file (default ~/.cache)")
    ap.add_argument("--clean", action="store_true",
                    help="delete this run's directory afterwards, fixture repo and policy file "
                         "both. This ORPHANS the git_ref on every row the run just wrote; the "
                         "default keeps it for that reason")
    args = ap.parse_args()

    token = f"{int(time.time()):x}"
    # ~/.cache, not the system temp directory, and for the same reason the repo is kept at all:
    # this host clears /tmp (it is a tmpfs, and a WSL restart empties it), so a run's commits
    # would vanish while the rows naming them stayed. It is also where the seam's other scratch
    # worktrees already live. --scratch overrides it.
    root = Path(args.scratch).expanduser() if args.scratch else Path.home() / ".cache"
    root.mkdir(parents=True, exist_ok=True)
    # The run directory holds TWO things since task 0358: the fixture repo and the policy file
    # that declares it. The policy sits beside the repo rather than inside it so it is never an
    # untracked file in a tree this tool commits into by hand, and so `--clean` takes both.
    run_dir = Path(tempfile.mkdtemp(prefix=f"0149-proof-{token}-", dir=str(root)))
    scratch = run_dir / "repo"
    scratch.mkdir()
    try:
        with store.read() as s:
            before = counts(s)
            held = s.scalar("SELECT count(*) FROM brain.work_item")
            if s.scalar("SELECT 1 FROM brain.work_item WHERE id = %s", (ABSENT,)):
                raise SystemExit(f"brain.work_item holds {ABSENT!r}, so case A would prove "
                                 f"nothing. Change ABSENT to an id the store does not hold.")
        print(f"store before : {before}, work_item rows={held}")
        print(f"run dir      : {run_dir}")
        print(f"scratch repo : {scratch}")
        print(f"run token    : {token}")

        wid = pick_work_item(args.work_item, args.agent)
        # Every fixture receipt names ABSENT in its own lineage. Case C then measures what the
        # loader does with the id the RECEIPT carries when the caller passes nothing at all.
        shas = build_fixture(scratch, token, names_work_item=ABSENT)
        # AFTER the commits and BEFORE the first projection: identity is the root commit, so
        # there is nothing to declare until one exists, and `project()` reads the declaration.
        declare_fixture(scratch, run_dir, token)
        print(f"case B item  : {wid}")
        for case, sha in shas.items():
            print(f"  fixture {case}  : {sha[:12]}")

        # ------------------------------------------------------------------ A
        rule("A", f"A WORK ITEM THE STORE DOES NOT HOLD ({ABSENT}). The projection lands anyway.")
        # No `allow_undeclared_repo` since task 0358: `declare_fixture` above named this repo by
        # its root commit, so all four projections take the same matched-policy path a projection
        # out of the brain takes. `REF_NOT_DURABLE` is still live and still unwaivable.
        out = project(scratch, shas["A"], actor=args.agent, work_item_id=ABSENT)
        with store.read() as s:
            after = counts(s)
        print(f"  thread_note      : {out['thread_note']}")
        print(f"  touches_inserted : {out['touches_inserted']}")
        print(f"  counts           : {before} -> {after}")
        assert out["thread_note"] == "no-such-work-item", out["thread_note"]
        assert after["receipt"] == before["receipt"] + 1, "the receipt row must have landed"
        assert out["touches_inserted"] == 1, "the touch row must have landed beside it"
        assert after["touch"] == before["touch"] + 1
        assert after["thread"] == before["thread"], "no note against an item the store lacks"

        # THE FACT IS NOT LOST, which is the argument `_announce` makes and this measures. The
        # work item is on the receipt row as free text with no foreign key, so a skipped note
        # costs zero knowledge -- unlike the touch-edge case, where refusing was right because
        # the alternative was inventing an id.
        with store.read() as s:
            row = s.one("SELECT subject_type, subject_id FROM brain.receipt WHERE git_ref = %s",
                        (shas["A"],))
        print(f"  the work item, still recorded : subject_type={row['subject_type']!r} "
              f"subject_id={row['subject_id']!r}")
        print(f"  the id the RECEIPT named      : {out['lineage_work_item_id']!r} "
              f"(reported, never written into the foreign key)")
        assert row["subject_id"], "the receipt row must still say what it was about"

        # ------------------------------------------------------------------ B
        rule("B", "A WORK ITEM THE STORE DOES HOLD. The note lands on its thread.")
        with store.read() as s:
            pre = counts(s)
        out = project(scratch, shas["B"], actor=args.agent, work_item_id=wid)
        with store.read() as s:
            post = counts(s)
            note = s.one("SELECT kind, from_agent, text FROM brain.thread WHERE work_item_id = %s "
                         "ORDER BY seq DESC LIMIT 1", (wid,))
        print(f"  thread_note : {out['thread_note']}")
        print(f"  counts      : {pre} -> {post}")
        print(f"  the line, read back: kind={note['kind']!r} from={note['from_agent']!r}")
        print(f"    {note['text']}")
        assert out["thread_note"] == "written", out["thread_note"]
        assert post["thread"] == pre["thread"] + 1, "exactly one note, on the item that exists"
        assert shas["B"][:12] in note["text"], "the note must name the commit it came from"

        # ------------------------------------------------------------------ C
        rule("C", "NO WORK ITEM ASKED FOR. This raised before 0149 too, via the lineage fallback.")
        parsed = read_promotion(scratch, shas["C"])
        named = parsed.lineage.get("work_item_id")
        print(f"  the receipt's own lineage names : {named!r}")
        assert named == ABSENT, "case C proves nothing unless the receipt NAMES a missing item"
        with store.read() as s:
            pre = counts(s)
        out = project(scratch, shas["C"], actor=args.agent)          # no work_item_id at all
        with store.read() as s:
            post = counts(s)
        print(f"  thread_note          : {out['thread_note']}")
        print(f"  lineage_work_item_id : {out['lineage_work_item_id']!r} (reported, not used)")
        print(f"  counts               : {pre} -> {post}")
        assert out["already_projected"] is False, "case C must run on an unprojected commit"
        assert out["thread_note"] == "not-requested", out["thread_note"]
        assert out["lineage_work_item_id"] == named
        assert post["receipt"] == pre["receipt"] + 1, "the projection must land"
        assert post["thread"] == pre["thread"], "the receipt's own id is not a store id"

        # ------------------------------------------------------------------ D
        rule("D", "IDEMPOTENT. Re-projecting inserts nothing and writes no second note.")
        with store.read() as s:
            pre = counts(s)
        out = project(scratch, shas["B"], actor=args.agent, work_item_id=wid)
        with store.read() as s:
            post = counts(s)
        print(f"  already_projected : {out['already_projected']}")
        print(f"  thread_note       : {out['thread_note']}")
        print(f"  counts            : {pre} -> {post}")
        assert out["already_projected"] is True
        assert out["thread_note"] == "already-projected", out["thread_note"]
        assert post == pre, "a re-run must insert nothing, INCLUDING no second note"

        print("\nALL FOUR PATHS HELD.")
        return 0
    finally:
        if args.clean:
            shutil.rmtree(run_dir, ignore_errors=True)
            print(f"\nrun directory DELETED ({run_dir}), fixture repo and policy file both. The "
                  f"brain.receipt and brain.touch rows this run wrote now name commits that exist "
                  f"nowhere.")
        else:
            print(f"\nrun directory kept at {run_dir} -- the rows this run wrote name its commits "
                  f"in git_ref, so deleting it makes them unreconcilable. --clean to drop it. The "
                  f"policy file in there declares a temp directory and is worthless outside this "
                  f"run; nothing reads it unless BRAIN_DURABLE_POLICY is pointed at it by hand.")


if __name__ == "__main__":
    raise SystemExit(main())
