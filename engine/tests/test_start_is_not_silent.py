#!/usr/bin/env python3
"""`start` releases a stop, or says it did not. Both directions, and the exit code of each.

THE INCIDENT, 2026-08-18 ~22:15Z to ~00:20Z, from the post of task 0253. The commander stopped T4
and T5, then ran `swarm start --agent T4` and `swarm start --agent T5` to bring them back, read
what came out as success, and went on. `brain.agent.stopped_at` was still set for both. The runner
respects that column, correctly, so `swarm-fleet up T4 T5` did not bring them back either. Only a
direct `UPDATE brain.agent SET stopped_at = NULL` released them, and T5 claimed 0249 seconds later.
Six minutes of two terminals believed to be coming back and not coming back.

WHAT THIS FILE ASSERTS, and what it deliberately does not. The ported `start` did clear the column
-- `INSERT ... ON CONFLICT DO UPDATE SET stopped_at = NULL` -- and did print. What it could not do
was tell the two cases apart: `start` on a stopped agent and `start` on a running one printed the
same `X started` and exited the same 0. That is the shape the incident is really about, and it is
the shape a verb of last resort can least afford, because the operator runs `start` exactly when
he already believes something is stuck and will stop looking the moment the line reads like a
success. The INSERT half had a second edge: a name that had never existed got a row, so a typo put
a phantom terminal into `swarm status` that nothing had ever heartbeated as.

So: three outcomes, three exits. 0 released, 2 there was nothing to release, 1 no such agent.

`stopped_at` MEANS exactly what it meant before and the runner reads it exactly as before. This is
the CLI learning to report, not the lifecycle changing. `test_stop_is_unchanged` is that sentence
asserted rather than promised.

Run: ENGINE_SCRATCH_DB=<db> BRAIN_PG_DB=<db> python3 engine/tests/test_start_is_not_silent.py
"""

from __future__ import annotations

import io
import os
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT))

os.environ.setdefault("BRAIN_PG_DB", os.environ.get("ENGINE_SCRATCH_DB", "brain_scratch"))

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _scratch_preflight import reconcile              # noqa: E402
reconcile(os.environ["BRAIN_PG_DB"])
os.environ.pop("SWARM_PARENT_TASK", None)

import store                                                      # noqa: E402
from swarm_engine import cli, reads                               # noqa: E402

OK, ERR, EMPTY = 0, 1, 2

PASS, FAIL = 0, 0


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


def start(name, reason="", by="test"):
    """Run the CLI verb the operator runs. Returns (exit code, what it printed)."""
    class A:
        agent = name
    A.reason, A.by = reason, by
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cli.cmd_start(A())
    return rc, buf.getvalue().strip()


def stop(name, reason="setting up a start test", by="test"):
    """`--reason` became REQUIRED on `stop` in task 0273, so this helper supplies one.

    An unsigned stop is refused by both the parser and the transition now, because a stop with no
    reason is indistinguishable on the board from an agent that died -- see
    `test_stop_leaves_a_trail.py` for the incident and the measurement.
    """
    class A:
        agent = name
    A.reason, A.by = reason, by
    buf, err = io.StringIO(), io.StringIO()
    with redirect_stdout(buf), redirect_stderr(err):
        rc = cli.cmd_stop(A())
    return rc, buf.getvalue().strip()


def reset():
    """Truncate the scratch store, exactly as the sibling suites do.

    Not optional and not cosmetic. Without it `test_stop_is_unchanged` posts a task, releases it
    back to `inbox`, and leaves it there; the NEXT run posts a second one, `claim` correctly hands
    back the OLDER row, and the assertion on `got["id"] == tid` fails on a fix that is working.
    A suite whose green depends on being run once is not evidence, and this one was written to be
    evidence about a verb that reported success it had not earned.
    """
    import subprocess
    subprocess.run([str(ROOT / "engine/bin/scratch-db.sh"), "truncate"],
                   check=True, capture_output=True)


def row(name):
    """The `brain.agent` row itself, or None. `reads.agents()` is the fleet, filtered."""
    return next((a for a in reads.agents() if a["name"] == name), None)


def register(name):
    """A real agent exists because it heartbeated, which is the only thing that makes the row."""
    store.apply("heartbeat", agent=name, status="idle")


def test_a_stopped_agent_is_released_and_told_so():
    """the direction that was already right, pinned so the fix cannot cost it"""
    reset()
    register("ZA")
    stop("ZA")
    truth("setup: ZA is stopped", reads.agent_stopped("ZA"))

    rc, out = start("ZA", reason="the release under test")
    eq("exit 0", rc, OK)
    truth("says which agent and what happened", out.startswith("ZA started"), out)
    truth("...and, since task 0273, who released it", "by test" in out, out)
    truth("and the column is actually clear -- the store, not the print",
          not reads.agent_stopped("ZA"))


def test_an_agent_that_was_not_stopped_is_not_reported_as_started():
    """THE INCIDENT'S SHAPE: a second `start` must not look like the first"""
    reset()
    register("ZB")
    stop("ZB")
    first_rc, first_out = start("ZB")
    second_rc, second_out = start("ZB")

    truth("the two runs do not print the same thing",
          first_out != second_out, f"both printed [{first_out}]")
    truth("the two runs do not exit the same",
          first_rc != second_rc, f"both exited {first_rc}")
    eq("the no-op exits 2, so a script can branch on it", second_rc, EMPTY)
    truth("...and says plainly that nothing changed",
          "not stopped" in second_out and "Nothing changed" in second_out, second_out)
    truth("the agent is still there and still running", not reads.agent_stopped("ZB"))
    truth("...and the row was not removed either", row("ZB") is not None)


def test_a_name_that_is_not_an_agent_is_refused_and_invents_nothing():
    """the typo case: `start --agent T$` used to add T$ to the fleet"""
    reset()
    truth("setup: NOSUCH is not an agent", row("NOSUCH") is None)

    rc, out = start("NOSUCH")
    eq("exit 1 -- a name that is not an agent is an error, not a no-op", rc, ERR)
    truth("says the name is unknown", "no agent named NOSUCH" in out, out)
    truth("NO phantom row: `start` does not register anyone",
          row("NOSUCH") is None,
          "start created brain.agent.NOSUCH; it would now show in `swarm status`")


def test_stop_is_unchanged():
    """the constraint: this task is a CLI defect, not a lifecycle redesign"""
    reset()
    register("ZC")
    rc, out = stop("ZC", reason="the constraint check")
    eq("stop still exits 0", rc, OK)
    # The LINE changed in task 0273 and the LIFECYCLE did not, which is the distinction this
    # test was always making. It now carries the actor and the reason, and it still begins the
    # same way, so an operator reading for `ZC stopped` still finds it.
    truth("stop still prints the agent and what happened to it",
          out.startswith("ZC stopped"), out)
    truth("...and now signs it", "by test: the constraint check" in out, out)
    truth("stop still sets the column the runner reads", reads.agent_stopped("ZC"))

    # The whole point of the column: a stopped agent claims nothing. If this ever goes green
    # while the runner has stopped honouring `stopped_at`, the fix above became a redesign.
    tid = store.apply("post", lane="engine", posted_by="test", agent_claimable=True,
                      workdir="/tmp", title="a task a stopped agent must not take")["id"]
    truth("a stopped agent still claims nothing",
          store.apply("claim", agent="ZC", lanes=["engine"]) is None)
    start("ZC")
    got = store.apply("claim", agent="ZC", lanes=["engine"])
    truth("and once started it claims again -- the release is real, end to end",
          got is not None and got["id"] == tid, str(got))
    store.apply("cancel", id=tid, agent="ZC", reason="test teardown", force=True)


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print("test_start_is_not_silent.py  --  a verb that silently does nothing is worse than one "
          "that fails")
    print(f"  store: {os.environ['BRAIN_PG_DB']} (scratch)\n")
    for t in tests:
        print(f"{t.__name__.replace('test_', '')}: "
              f"{t.__doc__.splitlines()[0] if t.__doc__ else ''}")
        try:
            t()
        except Exception:                                        # noqa: BLE001
            import traceback
            bad(f"{t.__name__} raised", traceback.format_exc().splitlines()[-1])
        print()
    print(f"{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
