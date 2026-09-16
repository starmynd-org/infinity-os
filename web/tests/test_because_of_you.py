"""Because-of-you counts artifacts once and sums each run once.

The block's whole job is making operator leverage legible, so it is the worst place in the
codebase for a figure that is off by a factor. It used to read both numbers out of

    FROM brain.artifact a FULL JOIN brain.run r ON r.work_item_id = a.work_item_id

which is a cartesian product WITHIN one item: `count(*)` returns artifacts x runs, and every
run's duration is summed once per artifact. It renders as zeros in a seeded store, where
`brain.run` holds a couple of sub-second rows, so the defect is invisible until the first night
the fleet does real work and the one figure that is supposed to prove leverage inflates it.

The shape that catches it is 3 artifacts and 2 runs on ONE item, with the two runs given
DIFFERENT, known durations. The multiply is then not a coincidence any arithmetic could
reproduce: the true reading is 3 artifacts and 3.0 minutes, the FULL JOIN reads 6 and 9.0.

The other two tests are here because narrowing the FULL JOIN to a plain JOIN is the obvious
repair and is not one. Run against this file it fails all three: the product is the join itself,
not its outer-ness, so 3 artifacts and 2 runs still read as 6 -- and it additionally drops the
two cases the FULL JOIN was written to serve. Both are ordinary. An attempt that fails before
writing anything still burned agent minutes; an artifact recorded outside any tracked run is
still a thing the operator's action produced.

Run:  web/tests/test-because-of-you.sh

That wrapper, and not `python3 -m web.tests.test_because_of_you` against the shared scratch.
This suite reads THE OPERATOR'S LAST 8 ACTIONS, and a sibling lane posting as `operator` into
brain_scratch buries the probe below that limit -- which reads as three failures about the
arithmetic and is nothing of the kind. The wrapper builds a database this file owns.
"""

from __future__ import annotations

import os as _os
import sys as _sys
import traceback

_R = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
for _p in (_R, _os.path.join(_R, "engine"), _os.path.join(_R, "queue")):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

import store                                                            # noqa: E402
from store.transitions import _scratch_registry                         # noqa: E402
from web import model                                                   # noqa: E402
from swarm_engine import transitions as _t                              # noqa: E402,F401

PASS, FAIL = [], []


def check(name, fn):
    try:
        fn()
        PASS.append(name)
        print(f"ok    {name}")
    except AssertionError as exc:
        FAIL.append((name, str(exc)))
        print(f"FAIL  {name}\n      {exc}")
    except Exception:                                                   # noqa: BLE001
        FAIL.append((name, traceback.format_exc()))
        print(f"ERROR {name}\n{traceback.format_exc()}")


# `post` inherits SWARM_PARENT_TASK from the environment, and an agent running this suite from
# inside a claimed task has it set to that task's id -- which does not exist in the scratch
# database, so every seed dies on "no such parent task" and the suite reads as three errors about
# something other than the arithmetic. engine/tests/run-all.sh unsets it for the same reason;
# doing it here as well means the file is runnable by hand from a working terminal.
_os.environ.pop("SWARM_PARENT_TASK", None)


def _guard():
    """Every test here writes. Never against the live store."""
    if _os.environ.get("BRAIN_PG_DB", "brain") == "brain":
        raise AssertionError("refusing to run against the live store; set BRAIN_PG_DB")


# How far back the operator's action is moved so that everything seeded after it lands inside
# the block's window. It has to exceed the longest run seeded below, because a run is dated from
# its START: a 120-second run written now() began two minutes ago, and against an operator action
# stamped now() it falls OUTSIDE `ts > the operator acted` and reads as zero minutes. That is a
# true reading of a false setup, and it cost one confusing red on the first run of this file.
#
# Kept SMALL for a second reason. _because_of_you() reads the operator's last 8 actions, so an
# action back-dated an hour loses its place to any newer operator row and the block returns
# nothing for the probe at all -- which reads as a failure of the arithmetic and is not one.
# Five minutes clears the longest run here with room and stays at the top of that list.
OPERATOR_ACTED_AGO = 300

PROBE_TITLE = "because-of-you arithmetic probe"


def _seed(*, artifacts: int, runs: tuple) -> str:
    """One item posted BY the operator, then `artifacts` artifacts and `runs` runs on it.

    `runs` is a tuple of durations in seconds, written with explicit timestamps rather than
    through `run start` / `run end`. Those two both stamp `now()` at each end and would leave
    rows a fraction of a second long -- which is exactly why this defect rendered as `0 agent
    minutes` in a seeded store and went unseen. Real, distinct durations are the whole point:
    they make a doubled sum arithmetically impossible to mistake for a rounding artefact.

    The item is posted by the operator so the `post` row lands in `brain.thread` as one of the
    four kinds _because_of_you() walks, and that row is then back-dated so the window it opens
    contains the runs.
    """
    # Every probe this file has ever seeded is moved outside the block's 36-hour window FIRST.
    # Three tests x every run of the file accumulate operator `post` rows in the scratch store,
    # and eight of them is all it takes to push the row a test is actually asserting on off the
    # end of `LIMIT 8`. The file then goes red on its own history rather than on the code. Only
    # rows this file wrote are touched, matched by title; no other lane's probe moves.
    with _scratch_registry():
        @store.transition("test.retire_prior_probes")
        def _retire(ctx, *, title, who):
            ctx.execute(
                "UPDATE brain.thread SET ts = now() - interval '48 hours' "
                " WHERE kind = 'post' AND from_agent = %s AND work_item_id IN "
                "       (SELECT id FROM brain.work_item WHERE title = %s)", (who, title))

        store.apply("test.retire_prior_probes", title=PROBE_TITLE, who=model.OPERATOR)

    out = store.apply("post", title=PROBE_TITLE, lane="console",
                      posted_by=model.OPERATOR, body="seeded by web/tests/test_because_of_you.py")
    tid = out["id"] if isinstance(out, dict) else out
    assert tid, "post returned no task id"

    for i in range(artifacts):
        store.apply("artifact", id=tid, path=f"/tmp/because-of-you-probe-{tid}-{i}",
                    kind="created", agent="T-because-test", exists=False)

    assert all(s < OPERATOR_ACTED_AGO for s in runs), \
        "a seeded run is longer than the window this helper opens; raise OPERATOR_ACTED_AGO"

    with _scratch_registry():
        @store.transition("test.seed_run")
        def _seed_run(ctx, *, id, attempt, seconds):
            ctx.execute(
                "INSERT INTO brain.run (work_item_id, attempt, agent, started_at, ended_at, "
                "                       outcome) "
                "VALUES (%s, %s, 'T-because-test', now() - make_interval(secs => %s), now(), "
                "        'done')",
                (id, attempt, seconds))

        @store.transition("test.backdate_operator_action")
        def _backdate(ctx, *, id, seconds):
            ctx.execute(
                "UPDATE brain.thread SET ts = now() - make_interval(secs => %s) "
                " WHERE work_item_id = %s AND kind = 'post' AND from_agent = %s",
                (seconds, id, model.OPERATOR))

        for n, seconds in enumerate(runs, start=1):
            store.apply("test.seed_run", id=tid, attempt=n, seconds=float(seconds))
        store.apply("test.backdate_operator_action", id=tid, seconds=float(OPERATOR_ACTED_AGO))
    return tid


def _block(tid: str) -> dict:
    """The one row _because_of_you() built for this item. Read through the real function."""
    with store.read() as s:
        rows = model._because_of_you(s)
    mine = [r for r in rows if r["task"] == tid]
    assert mine, (
        f"_because_of_you() returned no row for {tid}. It reads the operator's last 8 actions in "
        f"36 hours; if another lane posted 8 items as the operator while this ran, that is why.")
    return mine[0]


def test_three_artifacts_and_two_runs_read_as_three_and_one_sum():
    """THE GATE. 3 artifacts, 2 runs of 60s and 120s: 3 artifacts, 3.0 minutes.

    Under the FULL JOIN this returns 6 artifacts and 9.0 minutes -- each artifact row pairing
    with each run row, so the count is 3x2 and every duration is added three times.
    """
    _guard()
    tid = _seed(artifacts=3, runs=(60, 120))
    got = _block(tid)
    assert got["artifacts"] == 3, (
        f"seeded 3 artifacts and 2 runs, the block reads {got['artifacts']} artifacts. "
        f"{'That is artifacts x runs: the join is still multiplying.' if got['artifacts'] == 6 else ''}")
    assert got["minutes"] == 3.0, (
        f"seeded runs of 60s and 120s, one sum is 3.0 minutes, the block reads {got['minutes']}. "
        f"{'That is 3.0 x 3 artifacts: each run is being summed once per artifact.' if got['minutes'] == 9.0 else ''}")


def test_runs_with_no_artifacts_still_report_their_minutes():
    """What the FULL JOIN was for. An attempt that produced nothing still burned agent time."""
    _guard()
    tid = _seed(artifacts=0, runs=(90, 90))
    got = _block(tid)
    assert got["artifacts"] == 0, f"no artifacts were seeded, the block reads {got['artifacts']}"
    assert got["minutes"] == 3.0, (
        f"two 90s runs on an item with no artifacts should read 3.0 minutes, got {got['minutes']}."
        f" A plain JOIN drops the runs entirely and reads 0, which would make a failed attempt "
        f"look free.")


def test_artifacts_with_no_runs_are_still_counted():
    """The mirror. Rows recorded by hand or by a surface never went through a tracked run."""
    _guard()
    tid = _seed(artifacts=3, runs=())
    got = _block(tid)
    assert got["artifacts"] == 3, (
        f"3 artifacts on an item with no runs should read 3, got {got['artifacts']}. A plain "
        f"JOIN reads 0 and the operator's own action looks like it produced nothing.")
    assert got["minutes"] == 0.0, f"no runs were seeded, the block reads {got['minutes']} minutes"


def main() -> int:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            check(name, fn)
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    _sys.exit(main())
