#!/usr/bin/env python3
"""A task the fleet may claim has to say WHERE, and `set` may not take that back.

Task 0100. The workdir is D00 rule 7's boundary: the one directory a terminal is not allowed to
leave. `post` accepted `workdir=''` without a word, and `engine/bin/swarm-run` then had nothing to
`cd` to but the AGENT PROFILE's own directory -- `str(Path.home())` in the shipped DEFAULT_CONFIG
(`swarm_engine/config.py`), `/mnt/c/Users/you/repos` in the live fleet config on 2026-08-18.
Both are PARENTS of every repo on the box, so a brief's relative paths resolve somewhere real and
wrong instead of failing. That is not a boundary that is merely misplaced; it is the absence of
one, wearing a boundary's clothes.

MEASURED, task 0051 (2026-08-16): the agent was handed workdir `/home/you`, found no
`outputs/` tree there at all, had to infer the right repo out of the brief's prose, and created an
orphan `/home/you/outputs` while probing. On 2026-08-18 nineteen live inbox rows carried an
empty workdir, four of them agent-claimable.

REFUSED RATHER THAN DEFAULTED, and this suite asserts both halves of that sentence:

  * refused, because no default is correct. A client task and an `engine` task live in different
    repos, so one fallback trades a wrong tree for a wrong tree and hides it behind a value that
    looks deliberate. `test_the_guard_does_not_invent_a_default` is that, asserted.
  * scoped to `agent_claimable`, because `brain.work_item` is the operator's own queue as well as
    the fleet's and 15 of those 19 rows were his. His rows are never dispatched to a terminal and
    never `cd` anywhere. Requiring a directory of them would break his queue to guard a door they
    do not use.

AND AT BOTH DOORS. `set agent_claimable true` on a workdir-less row, and `set workdir ''` on a row
the fleet may already claim, each rebuild the refused state one field at a time. This codebase has
already been walked out through exactly that gap once -- `post` refused to lower `external` while
`set` allowed it, and two individually-compliant hops laundered the flag (task 0146). A guard at
one verb is a guard with a door beside it.

Run: python3 engine/tests/test_workdir_gate.py   (against the scratch database, never `brain`)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT))

os.environ.setdefault("BRAIN_PG_DB", os.environ.get("ENGINE_SCRATCH_DB", "brain_scratch"))

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _scratch_preflight import reconcile              # noqa: E402
reconcile(os.environ["BRAIN_PG_DB"])
os.environ.pop("SWARM_PARENT_TASK", None)
os.environ.pop("SWARM_AGENT", None)

import store                                                      # noqa: E402
from swarm_engine.transitions import VerbError                    # noqa: E402

PASS, FAIL = 0, 0
# Unique per run: this suite shares a scratch database with sibling suites and does not truncate it.
LANE = f"wdg{os.getpid()}"
WD = "/tmp"          # absolute, and it exists, which is all the guard asks of a value


def ok(msg):
    global PASS
    PASS += 1
    print(f"  ok    {msg}")


def bad(msg, detail=""):
    global FAIL
    FAIL += 1
    print(f"  FAIL  {msg}")
    if detail:
        print(f"        {detail}")


def eq(msg, got, want):
    ok(msg) if got == want else bad(msg, f"wanted [{want}], got [{got}]")


def truth(msg, cond, detail=""):
    ok(msg) if cond else bad(msg, detail)


def refuses(msg, fn, *, wanted):
    """The call must RAISE, not return a quiet no-op. Returns the exception text."""
    try:
        r = fn()
    except VerbError as e:
        ok(msg) if wanted in str(e) else bad(msg, f"refused, but not for the right reason: {e}")
        return str(e)
    bad(msg, f"NOT refused: the verb returned {r!r}")
    return ""


def post(**kw):
    kw.setdefault("lane", LANE)
    kw.setdefault("posted_by", "T1")
    kw.setdefault("title", "workdir gate fixture")
    return store.apply("post", **kw)


def row(tid, col="workdir"):
    with store.read() as s:
        return s.scalar(f"SELECT {col} FROM brain.work_item WHERE id = %s", (tid,))


def count_in_lane():
    with store.read() as s:
        return s.scalar("SELECT count(*) FROM brain.work_item WHERE lane = %s", (LANE,))


# ================================================================ post

def test_a_fleet_task_with_no_workdir_is_refused():
    """The defect itself: `swarm post --for-agents` with no `--workdir` used to be accepted."""
    before = count_in_lane()
    msg = refuses("post refuses an agent-claimable task with no workdir",
                  lambda: post(title="fleet work, nowhere", agent_claimable=True),
                  wanted="no workdir")
    # A refusal that still wrote the row is not a refusal. `store.apply` rolls the transaction
    # back on any exception, and this is that sentence asserted rather than assumed.
    eq("and nothing was written", count_in_lane(), before)
    truth("and the refusal names the flag that fixes it", "--workdir" in msg, msg)
    truth("and says why there is no default to fall back on",
          "no default worth guessing" in msg, msg)


def test_the_same_task_with_a_workdir_is_accepted():
    """The guard has to be a gate and not a wall: the correct call still goes through."""
    r = post(title="fleet work, somewhere", agent_claimable=True, workdir=WD)
    eq("post accepts an agent-claimable task that names its workdir", row(r["id"]), WD)
    truth("and the row really is claimable", bool(r["agent_claimable"]), str(r))


def test_the_operators_own_row_may_still_have_no_workdir():
    """His queue is in this table too, and 15 of the 19 live empty-workdir rows were his.

    A held row is read by a human and dispatched to nobody, so it never `cd`s anywhere. Refusing
    it would have broken `swarm post` for every one of a video to record, a personal document to
    renew and thirteen others, to close a door those rows do not walk through.
    """
    r = post(title="the operator's own, no directory involved", posted_by="operator")
    eq("a held row with no workdir is still accepted", row(r["id"]), "")
    eq("and it is held, which is what makes that safe", bool(r["agent_claimable"]), False)


def test_a_relative_workdir_is_refused_too():
    """`swarm-run` does a bare `cd "$RUN_DIR"`, so a relative path is the same bug plus a step."""
    msg = refuses("post refuses a relative workdir on a fleet task",
                  lambda: post(title="relative", agent_claimable=True, workdir="outputs/today"),
                  wanted="relative")
    truth("and quotes the value it refused", "outputs/today" in msg, msg)
    r = post(title="tilde is fine", agent_claimable=True, workdir="~/work")
    eq("but `~` is accepted, because the runner expands it", row(r["id"]), "~/work")


def test_a_child_posted_by_a_terminal_is_refused_the_same_way():
    """The four live rows this cost were exactly this shape.

    0101, 0103, 0104 and 0105 were posted on 2026-08-18 by T1, T5 and T4 handing off adjacent
    work with `--parent`. `--parent` carries `agent_claimable` down, so none of them passed a
    flag and all four landed claimable with `workdir=''`.
    """
    parent = post(title="fleet parent", agent_claimable=True, workdir=WD)["id"]
    refuses("a --parent child that inherits claimability inherits the requirement too",
            lambda: post(title="adjacent work, handed off", parent=parent, posted_by="T4"),
            wanted="no workdir")
    # And it is NOT silently inherited from the parent, which is the tempting wrong fix: the
    # child of an `engine` task is routinely in another repo, and copying the parent's tree down
    # would put it in this one with a value that reads deliberate.
    kid = post(title="adjacent work, told where", parent=parent, posted_by="T4",
               workdir="/tmp/elsewhere")
    eq("and a child that names its own tree keeps that tree, not its parent's",
       row(kid["id"]), "/tmp/elsewhere")


def test_the_guard_does_not_invent_a_default():
    """WHAT FAILURE WOULD HAVE LOOKED LIKE: every task defaulting to the runtime repo.

    That is not a smaller version of the fix, it is the same defect relocated. A client task
    would then run in `infinity-os` rather than in home -- one wrong tree for another,
    and the second one looks deliberate, so nobody reads it as a bug. Asserted by reading the
    store: no accepted row acquired a workdir nobody passed.
    """
    r = post(title="held, and left alone", posted_by="operator")
    eq("a held row is left with the empty string, not filled in", row(r["id"]), "")
    truth("and the repo root is nowhere in it", str(ROOT) not in (row(r["id"]) or ""), str(ROOT))


# ================================================================ set, the second door

def test_set_cannot_raise_claimability_onto_a_workdir_less_row():
    """Hop one of the two-hop launder: post it held, then let it out."""
    tid = post(title="held today, fleet tomorrow", posted_by="operator")["id"]
    eq("the row starts held and directionless", (bool(row(tid, "agent_claimable")), row(tid)),
       (False, ""))
    refuses("`set agent_claimable true` is refused while the workdir is empty",
            lambda: store.apply("set", id=tid, key="agent_claimable", value="true",
                                agent="T4", as_operator=True),
            wanted="no workdir")
    eq("and the refusal rolled back: the row is still held",
       bool(row(tid, "agent_claimable")), False)
    # The ordered pair the guard actually permits: say where, THEN hand it to the fleet.
    store.apply("set", id=tid, key="workdir", value=WD, agent="T4")
    store.apply("set", id=tid, key="agent_claimable", value="true", agent="T4",
                as_operator=True)
    eq("but workdir first, then claimable, goes through",
       (bool(row(tid, "agent_claimable")), row(tid)), (True, WD))


def test_set_cannot_blank_the_workdir_of_a_fleet_row():
    """Hop two, from the other side: leave it claimable and take the boundary away."""
    tid = post(title="fleet work with a home", agent_claimable=True, workdir=WD)["id"]
    refuses("`set workdir ''` is refused on a row the fleet may claim",
            lambda: store.apply("set", id=tid, key="workdir", value="", agent="T4"),
            wanted="no workdir")
    eq("and the refusal rolled back: the workdir is untouched", row(tid), WD)
    refuses("a relative one is refused there too",
            lambda: store.apply("set", id=tid, key="workdir", value="../elsewhere", agent="T4"),
            wanted="relative")
    eq("and that rolled back as well", row(tid), WD)


def test_set_may_still_blank_the_workdir_of_a_held_row():
    """The negative case, without which the guard is a wall around the operator's own queue."""
    tid = post(title="his own", posted_by="operator", workdir="/tmp/somewhere")["id"]
    store.apply("set", id=tid, key="workdir", value="", agent="T4")
    eq("a held row's workdir may be cleared", row(tid), "")
    # And lowering claimability is always open -- taking work back from the fleet is the safe
    # direction, and it must not be gated behind naming a directory.
    fleet = post(title="fleet work, recalled", agent_claimable=True, workdir=WD)["id"]
    store.apply("set", id=fleet, key="agent_claimable", value="false", agent="T4")
    eq("and a fleet row may always be taken back from the fleet",
       bool(row(fleet, "agent_claimable")), False)


# ================================================================ the runner, structurally

def test_the_runner_no_longer_relocates_a_workdir_less_task():
    """The other half of the fix, asserted the way `test_contract.py` rule 7 asserts its half.

    `swarm-run` is bash and this suite is Python against a store, so this reads the file rather
    than running a fleet. That is enough for the regression that matters: the deleted line is one
    line, and it is the kind of line a later edit puts back as a convenience without noticing that
    the convenience IS the defect. The runner's own header has promised "a missing workdir FAILS
    rather than relocating" since it was ported; until task 0100 that promise covered only a
    workdir that was set and absent from this host, never one nobody set at all.
    """
    runner = (ROOT / "engine/bin/swarm-run").read_text(encoding="utf-8")
    # CODE LINES ONLY. The first version of this assertion searched the whole file and failed on
    # the comment that QUOTES the deleted line, three lines below the deletion -- a guard that
    # cannot tell an assignment from a description of one is a guard that has to be weakened or
    # worked around, and both of those end with it asserting nothing.
    live = [ln for ln in runner.splitlines() if not ln.lstrip().startswith("#")]
    truth("the fallback to the agent profile's own directory is gone",
          not [ln for ln in live if 'RUN_DIR="${WORKDIR' in ln],
          "swarm-run still assigns RUN_DIR from the profile WORKDIR: "
          + "; ".join(ln.strip() for ln in live if 'RUN_DIR="${WORKDIR' in ln))
    truth("and the comment that explains why is still there to be read",
          'RUN_DIR="${WORKDIR' in runner, "the explanation was deleted with the line")
    truth("and the empty case refuses instead",
          "has no workdir at all, refusing rather than relocating" in runner, "")
    truth("and the refusal tells the reader how to repair the row",
          "swarm set $TASK_ID workdir /abs/path" in runner, "")
    truth("the workdir-does-not-exist refusal is still there, unchanged",
          "does not exist on this host, refusing" in runner, "")


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print("test_workdir_gate.py  --  a claimable task names its tree, at both doors (task 0100)")
    print(f"  store: {os.environ['BRAIN_PG_DB']} (scratch)\n")
    for t in tests:
        print(f"{t.__name__.replace('test_', '')}: "
              f"{t.__doc__.splitlines()[0] if t.__doc__ else ''}")
        try:
            t()
        except Exception:                                         # noqa: BLE001
            import traceback
            bad(f"{t.__name__} raised", traceback.format_exc().strip().splitlines()[-1])
        print()
    print(f"{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
