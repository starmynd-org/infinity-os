#!/usr/bin/env python3
"""The adapter/store seam, exercised end to end and printed. Task 0103.

Run it against a repository holding the commits, never against the brain's main:

    python3 adapter/tools/lineage_join_proof.py \
      --repo /mnt/c/Users/you/repos/your-brain \
      --sha <resolved-sha> --sha <unresolved-sha> --sha <ambiguous-sha>

THE SHAS MUST BE DURABLE, WHICH IS NEW SINCE TASK 0349 AND IS NOT SOMETHING THIS TOOL CAN WAIVE.
`project()` refuses a `git_ref` no declared durable ref reaches (`REF_NOT_DURABLE`) and there is no
flag that opens that gate, here or anywhere. The invocation above used to name a scratch worktree
holding commits made by `touch add` on a `scratch/...` branch; every one of those is refused today,
correctly, because a store row asserting such a commit is exactly the thing task 0297 measured 26
of. Book the receipts onto `receipts/durable` first (`brain-adapter receipt book` now refuses
anything else) and pass the shas from there.

`--allow-undeclared-repo` is the second gate and it IS waivable, loudly. `policy/durable-refs.json`
declares repositories by root commit, so a fixture repo -- a bare `mkdtemp` one, say -- is refused
(`UNDECLARED_REPO`) unless the flag is passed. Passing it accepts that the row will outlive the
commit it names, and `store_projection` says so on stderr every time. There is one other way past
this gate and it is not a waiver: DECLARE the fixture, keyed by its own root commit, in a policy
file scoped to the run. `work_item_note_proof.py` has done that since task 0358 and passes no flag,
and `adapter/tests/test_adapter.py::TempRepo._declare` is the same move; a declaration with an
empty `book_refs` buys classification and no permission, so `assert_bookable` still refuses. A
worktree or a clone of the brain shares the brain's root commit and so needs no flag either.

What it demonstrates, in order:

1. `entity resolve` on three references, one per adapter status, and the three columns each
   produces. The mapping is `store_join.lineage_columns()` and nothing here reimplements it.
2. A real `brain.work_item` row written through the EXISTING `post` verb carrying the whole
   resolved TRIPLE -- `produced_by`, `produced_by_ref` and `resolution_status`, spread from
   `lineage_columns()` and asserted rather than printed. This is the half of the seam that
   needed no new code at all, and printing it beside the receipt rows is what shows the half
   that did.
3. Each committed receipt PROJECTED out of git into `brain.receipt` and `brain.touch` through
   `store.apply("lineage project")`. git is read with `git show <sha>:<path>`, so the rows come
   from the commit and never from the working tree.
4. The projection re-run, to show it inserts nothing the second time.
5. `brain.lineage_column_drift`, which must be empty.

IT DOES NOT WRITE UNLESS ASKED, AND THAT IS NEW SINCE TASK 0097. `--dry-run` is the DEFAULT and
`--write` is the opt-in. A proof tool's normal mode must not mutate the thing it is proving
against, and this one did: every run posted a live `brain.work_item` row, four of them reached
live `brain` (`0013`, `0081`, `0082`, `0083`), migration 26's backfill raised `agent_claimable` on
all four because they carry `actor_type = 'ai'` and a non-operator `posted_by`, and on 2026-08-18
T2, T4 and T5 each claimed one within 71 minutes of the cutover. Three terminals, three rows with
an empty brief and an empty workdir. A row with an empty brief is indistinguishable from real work
at claim time, which is why the fix is not "post a tidier row" but "do not post one by default".

What each mode does:

    (default)   resolves, reads git, and PRINTS the rows it would write. Nothing is written.
    --write     writes them. The `work_item` row is posted `agent_claimable=False` EXPLICITLY,
                titled `FIXTURE`, carries a brief saying what it is, and is cancelled by the run
                that made it -- on every exit path, including a projection that refuses -- so no
                human has to disposition it off the board afterwards.

`agent_claimable=False` is passed rather than left to `post`'s default on purpose. The default
covers the parentless case only -- `transitions.py` takes `claimable = p_claimable` when nobody
says -- so a future caller that ran this under a claimable parent would inherit `true` and leak
again. An explicit `False` is honoured under any parent; only an explicit `True` under a held
parent is refused.

When it writes, it writes to the store and to nothing else. It never writes git: the receipts it
projects were committed by `touch add` beforehand.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
_ROOT = next(p for p in _HERE.parents if (p / "store" / "__init__.py").exists())
sys.path[:0] = [str(_ROOT), str(_ROOT / "adapter"), str(_ROOT / "engine")]

import store  # noqa: E402
from brain_adapter import config  # noqa: E402
from brain_adapter.index import EntityIndex  # noqa: E402
from brain_adapter.store_join import lineage_columns, producer_stamp, read_promotion  # noqa: E402
from brain_adapter.store_projection import project  # noqa: E402


#: The title and the brief the `--write` row carries. A fixture row that does not SAY it is a
#: fixture costs a human a provenance check each time one appears, and four of them have now been
#: dispositioned by hand. `FIXTURE` leads the title because `swarm ls` shows titles and not briefs.
FIXTURE_TITLE = ("FIXTURE (lineage_join_proof.py --write): the whole lineage triple through the "
                 "existing post verb")

FIXTURE_BRIEF = """\
THIS ROW IS A FIXTURE AND NOT WORK. Do not claim it, do not do it, nothing is waiting on it.

`adapter/tools/lineage_join_proof.py --write` posted it to demonstrate step 2 of the adapter/store
seam: that `post` carries all three lineage columns -- produced_by, produced_by_ref and
resolution_status -- from `brain_adapter.store_join.lineage_columns()` onto a real brain.work_item
row with no new code. It is posted agent_claimable=False and cancelled by the same run.

Task 0097 is why it says so. Four earlier runs left rows 0013, 0081, 0082 and 0083 on live
`brain` with an empty brief, an empty workdir and agent_claimable raised by migration 26's
backfill, and T2, T4 and T5 each claimed one on 2026-08-18.
"""


def rule(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def show(label: str, row) -> None:
    print(f"\n-- {label}")
    print(json.dumps(row, indent=2, default=str, sort_keys=True))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True,
                    help="the repository holding the commits. Its root commit is what "
                         "policy/durable-refs.json is keyed on, so a worktree or clone of the "
                         "brain counts as the brain and a fixture repo does not")
    ap.add_argument("--sha", action="append", required=True)
    ap.add_argument("--agent", default="T5")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", dest="write", action="store_false", default=False,
                      help="THE DEFAULT. Resolve, read the commits, and print every row this run "
                           "WOULD write, without writing any of them. It exists as a flag so it "
                           "can be said out loud in a runbook; passing it changes nothing, "
                           "because not writing is what happens when nobody says otherwise")
    mode.add_argument("--write", dest="write", action="store_true",
                      help="actually write: one fixture brain.work_item row plus the projected "
                           "brain.receipt and brain.touch rows. The work_item row is posted "
                           "agent_claimable=False, titled FIXTURE, carries a brief saying what "
                           "it is, and is cancelled on the way out")
    ap.add_argument("--allow-undeclared-repo", action="store_true",
                    help="project from a repository policy/durable-refs.json does not declare, "
                         "which is every fixture repo. The rows will outlive the commits they "
                         "name. It does NOT waive REF_NOT_DURABLE: the shas must still be "
                         "reachable from a durable ref inside whatever repo --repo names")
    args = ap.parse_args()

    brain = config.brain_root()
    idx = EntityIndex.build(brain)

    # ---------------------------------------------------------------- 1. the three states
    rule("1. entity resolve -> the three lineage columns. Four states, three shown here; the "
         "fourth is producer_stamp() below, which is a NAME and not a resolution at all.")
    for ref in ("surface-boundary", "[[no-such-node-t5-lineage-probe]]", "accountability-chart"):
        res = idx.resolve(ref)
        show(f"{res.status}: {ref!r}", lineage_columns(res))
    show("no attempt made: producer_stamp('d3-ingest')", producer_stamp("d3-ingest"))

    # ------------------------------------------- 2. the existing verb, unchanged, still works
    rule("2. THE EXISTING VERB. `post` carries the WHOLE triple, so a resolved reference lands on "
         "a real brain.work_item row with no new code.")
    from swarm_engine import transitions as _engine_verbs  # noqa: F401  (registers `post`)

    # `post` reads SWARM_PARENT_TASK from the environment when no parent is passed, and the
    # swarm runner exports it into every terminal. So any tool run inside a terminal inherits
    # that terminal's task as its parent -- and refuses outright when, as here, the bus is the
    # file-based one and no matching brain.work_item row exists. Cleared explicitly: this proof
    # posts a standalone row, and an implicit parent would attach it to whatever task happened
    # to be running.
    os.environ.pop("SWARM_PARENT_TASK", None)

    resolved = idx.resolve("surface-boundary")
    cols = lineage_columns(resolved)

    # SPREAD, NOT SUBSCRIPTED, and the difference is the whole point of the step. This call used
    # to pass `produced_by=lineage_columns(resolved)["produced_by"]` and drop the other two, so
    # every row it wrote carried `resolution_status` NULL -- "no attempt was made" -- when an
    # attempt had been made AND had succeeded. That is exactly the collapse `store_join.py`'s
    # four-states section and `post`'s own docstring exist to warn about, and the only caller in
    # the repo demonstrating it was the tool whose job is to demonstrate the opposite.
    # `transitions.py` names the correct form: store.apply("post", ..., **lineage_columns(res)).
    post_kwargs = dict(
        actor=args.agent, title=FIXTURE_TITLE, body=FIXTURE_BRIEF,
        lane="adapter", posted_by=args.agent, priority=0, actor_type="ai",
        # Explicit, not inherited. See the module docstring: `post`'s default only protects the
        # parentless case, and this tool is the reason that distinction cost three terminals.
        agent_claimable=False,
        **cols,
    )

    if not args.write:
        show("the brain.work_item row this run WOULD post -- NOTHING WAS WRITTEN", post_kwargs)
        print("\n   Dry run, which is the default. Pass --write to land it. All three lineage\n"
              "   columns are above, so the resolution that succeeded is recorded as having\n"
              "   succeeded rather than as never attempted.")
        work_item_id = None
    else:
        item = store.apply("post", **post_kwargs)
        work_item_id = item["id"]
        with store.read() as s:
            row = s.one(
                "SELECT id, title, lane, state, produced_by, produced_by_ref, resolution_status, "
                "       agent_claimable, left(brief, 60) AS brief_head "
                "FROM brain.work_item WHERE id = %s", (work_item_id,))
        show("brain.work_item, written through store.apply('post')", row)

        # ASSERTED, NOT PRINTED. A printed column is a column somebody has to read and compare;
        # the previous version of this step printed a NULL `resolution_status` under a heading
        # saying the seam worked, and it was read that way for two days.
        for col, want in cols.items():
            if row[col] != want:
                raise SystemExit(
                    f"the seam did not hold: post() was given {col}={want!r} and the row came "
                    f"back {row[col]!r}. That is the whole claim of step 2 and it is false.")
        if row["agent_claimable"]:
            raise SystemExit(
                "refusing to continue: the fixture row came back agent_claimable=true. A "
                "terminal can claim it, which is task 0097's defect reopened.")
        print(f"\n   asserted: all three lineage columns round-tripped ({', '.join(cols)}), and "
              f"\n   agent_claimable came back false, so no terminal can claim this row.")

    # STEPS 3 TO 5 RUN UNDER A `finally` THAT CLOSES THE FIXTURE ROW. Not decoration:
    # `project()` refuses a sha no durable ref reaches and refuses an undeclared repo, so a
    # run that dies in step 3 is the ORDINARY failure of this tool, not the exotic one. If
    # the cancel lived only on the success path, every such run would leave exactly the row
    # task 0097 is about -- open, in `inbox`, nobody's -- and would leave it precisely when
    # the operator is least likely to go looking.
    try:
        # ---------------------------------------------------------------- 3. the projection
        rule("3. THE PROJECTION. git -> store, through one registered transition. Each receipt is "
             "read out of its COMMIT with `git show <sha>:<path>`, never off the working tree.")
        for sha in args.sha:
            parsed = read_promotion(Path(args.repo), sha)
            print(f"\n-- git {sha[:12]}  {parsed.path}")
            print(f"   receipt id      : {parsed.entity_id}")
            print(f"   lineage in git  : {parsed.lineage_columns()}")
            print(f"   touch edges     : {len(parsed.touches)}")
            # The git half above is a pure read and runs in both modes; only the store half is gated.
            # Reading the commit and printing what it holds is most of what this step demonstrates.
            if not args.write:
                print(f"   projected       : NOT PROJECTED (dry run). --write would insert "
                      f"{len(parsed.touches)} touch row(s) and one receipt row.")
                continue
            out = project(Path(args.repo), sha, actor=args.agent, work_item_id=work_item_id,
                          allow_undeclared_repo=args.allow_undeclared_repo)
            print(f"   projected       : {out['touches_inserted']} touch row(s) inserted, "
                  f"already_projected={out['already_projected']}")

        # ---------------------------------------------------------------- 4. the rows
        rule("4. THE ROWS, read back out of the store.")
        with store.read() as s:
            show("brain.receipt (all four lineage states are distinguishable here)", s.query(
                "SELECT id, action, subject_type, subject_id, produced_by, produced_by_ref, "
                "       resolution_status, git_ref, booked_by "
                "  FROM brain.receipt ORDER BY id"))
            show("brain.touch (a projection: every row names the commit it came from)", s.query(
                "SELECT id, subject_type, subject_id, entity_id, component_type, orient_role, "
                "       role, git_ref, produced_by, resolution_status "
                "  FROM brain.touch ORDER BY id"))
            show("store/reads.py::missing_lineage -- a producer with no touch edges", s.query(
                "SELECT w.id, w.produced_by FROM brain.work_item w "
                " WHERE w.produced_by IS NOT NULL "
                "   AND NOT EXISTS (SELECT 1 FROM brain.touch t "
                "                    WHERE t.subject_type = 'work_item' AND t.subject_id = w.id)"))

        # ---------------------------------------------------------------- 5. idempotency
        rule("5. RE-PROJECTED. A projection can be re-run as often as anyone likes; if it were not "
             "idempotent, reconciling against git would double the table it was checking.")
        if not args.write:
            print("   nothing was projected, so there is nothing to re-project. Idempotency is a "
                  "property of the write path\n   and cannot be shown without writing: --write.")
        for sha in args.sha if args.write else ():
            out = project(Path(args.repo), sha, actor=args.agent, work_item_id=work_item_id,
                          allow_undeclared_repo=args.allow_undeclared_repo)
            print(f"   {sha[:12]}: inserted={out['touches_inserted']} "
                  f"already_projected={out['already_projected']}")

    finally:
        # A non-claimable row still sits in `inbox`, where `swarm ls` prints it `[OPERATOR]`
        # and a human has to decide what it is. That happened four times before task 0097.
        # The row's whole job is done the moment its columns were read back and asserted, so
        # the run that made it closes it and the board carries no open item nobody posted.
        if work_item_id:
            rule("6. THE FIXTURE CLOSES ITSELF. It was never work; leaving it in `inbox` "
                 "would make a human disposition it, which is the cost this tool has "
                 "already charged four times.")
            store.apply("cancel", id=work_item_id, agent=args.agent,
                        reason="fixture row from lineage_join_proof.py --write; the seam it "
                               "proves was asserted in the same run. Never work, nothing was "
                               "waiting on it, nothing to disposition.")
            with store.read() as s:
                print("   ", s.one("SELECT id, state, agent_claimable FROM brain.work_item "
                                   "WHERE id = %s", (work_item_id,)))

    with store.read() as s:
        rule("7. THE INVARIANT. brain.lineage_column_drift must be empty: every produced_by in "
             "the schema has its ref and its status beside it.")
        print("   drift rows:", s.query("SELECT * FROM brain.lineage_column_drift"))
        print("   touch rows total:", s.scalar("SELECT count(*) FROM brain.touch"))
        print("   touch rows with a git_ref:",
              s.scalar("SELECT count(*) FROM brain.touch WHERE git_ref IS NOT NULL"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
