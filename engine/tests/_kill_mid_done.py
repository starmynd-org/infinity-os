#!/usr/bin/env python3
"""A `done` that is killed after its writes and before its commit. Used by rule 10's test.

The kill is `os.kill(os.getpid(), SIGKILL)` from inside the transition body, after `ctx.execute`
has run the row update and `ctx.thread` has inserted the thread event. SIGKILL rather than an
exception on purpose: an exception would take the `except: conn.rollback()` path in `store.apply`,
which proves that the rollback handler works and NOT that the database is what guarantees
atomicity. A killed process runs no handler at all, the connection dies with it, and Postgres
rolls the transaction back because it never saw a COMMIT.

That distinction is the whole point of the test. The file bus got all-or-nothing from the kernel;
here it has to come from the transaction, and only an uncatchable kill tests the transaction.
"""

import os
import signal
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT))

import store                              # noqa: E402
from swarm_engine import transitions      # noqa: E402,F401


@store.transition("done killed mid-transaction")
def done_killed(ctx, *, id, summary):
    ctx.execute(
        "UPDATE brain.work_item SET result = %s, finished_at = now(), state = 'done' WHERE id = %s",
        (summary, id))
    ctx.actor = "T1"
    ctx.thread(id, "done", summary)
    # Both writes have happened inside the transaction. Neither has committed.
    sys.stdout.write("writes done, not committed; killing self now\n")
    sys.stdout.flush()
    os.kill(os.getpid(), signal.SIGKILL)
    raise AssertionError("unreachable: SIGKILL cannot be caught")


if __name__ == "__main__":
    store.apply("done killed mid-transaction", id=sys.argv[1], summary="THIS MUST NOT SURVIVE")
