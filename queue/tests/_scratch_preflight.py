"""Build or reconcile the queue scratch database before a suite asserts anything. Task 0212.

The engine lane already had `engine/tests/_scratch_preflight.py` (task 0153) and the argument for
it is the same one directory down, with one addition it does not need: the engine's shared scratch
usually EXISTS, so `migrate` is enough. The queue suites named a database nobody had built, and
what that looks like is not a clear message. Measured 2026-08-16 on `brain_t3_0212_queue`: all
four suites in this directory ran a few assertions green out of an in-process ranking table and
then died in `reset()` with

    subprocess.CalledProcessError: Command '[... 'psql', '-q', '-c', "TRUNCATE brain.queue_item,
    ..."]' returned non-zero exit status 2

which says nothing about a missing database and nothing about the code under test. Worse, the
suites exit 0 on that path when run from a shell loop, because the traceback goes to stderr and
the loop reads the last command's status.

`ensure` and not `create`: create does `DROP DATABASE ... WITH (FORCE)`, which would kill a
sibling lane's suite mid-run, and several lanes share this tree. ensure builds only when the
database is genuinely absent and otherwise applies just the versions the ledger does not record,
so it is safe to call from every suite, every run, including concurrently.

Not conditional, not `|| true`. A migration in the tree that will not apply is a real finding, and
the place to report it is here, in one legible line, rather than as N assertion failures below
that are all the same sentence in a language about the code.
"""
import os
import subprocess
import sys
from pathlib import Path

_SCRATCH = str(Path(__file__).resolve().parents[2] / "queue/bin/queue-scratch-db.sh")

#: The live store. Duplicated from `store/schema.py` rather than imported, for the reason the
#: engine's copy of this file gives: importing `store` inside a preflight opens a connection to the
#: database the preflight exists to protect.
_LIVE_DB = "brain"


def _suggested_scratch_db():
    """A database name it is always safe to TRUNCATE, for the refusal's remedy line.

    THE QUEUE LANE HAD THE SAME DEFECT TASK 0353 FIXED IN THE ENGINE LANE, AND KEPT IT FOR A DAY
    LONGER BECAUSE 0353's SWEEP COUNTED THE WRONG POPULATION. Task 0378. That sweep reported "19
    files carry an import of _scratch_preflight; 1 site formats the remedy", and it was right
    about the file it
    looked at: `engine/tests/_scratch_preflight.py`. It counted IMPORTERS OF ONE FILE rather than
    OCCURRENCES OF THE DEFECT, and this file -- a separate copy, in a sibling directory, with its
    own `reconcile` -- was never in the population. The denominator was honest and the wrong one.

    What it left printing, at this file's own line 48, was `% (db, target, target, db, db)`: both
    exports filled with the caller's `BRAIN_PG_DB`, which in any fleet terminal is `brain`. Two
    lines above it the same message says `reset()` would TRUNCATE that name. So an agent pasting
    the remedy -- the single most likely thing anyone does with a remedy -- emptied `queue_item`,
    `queue_defer`, `queue_open` and the rest of the live queue.

    Same fix as the engine's: the remedy never echoes an input. In this branch `db != target` holds
    by construction, so NEITHER is known-safe -- `db` is the live store in a fleet terminal, and
    `target` is whatever the caller exported, which the mismatch itself says is wrong.
    """
    agent = "".join(c for c in os.environ.get("SWARM_AGENT", "").strip().lower() if c.isalnum())
    task = "".join(c for c in os.environ.get("SWARM_PARENT_TASK", "").strip().lower()
                   if c.isalnum())
    name = "brain_queue_%s_%s" % (agent, task) if agent and task else "brain_queue_scratch"
    # Not reachable by either branch above. Present so that a future edit which makes it reachable
    # fails loudly here instead of quietly inside a TRUNCATE.
    assert name != _LIVE_DB, "the remedy line must never name the live store"
    return name


def reconcile(db=None):
    """Bring the queue scratch database up to the tree, or exit 1 saying why it could not."""
    # BRAIN_PG_DB is what the store connects to; QUEUE_SCRATCH_DB is what the builder acts on.
    # Set only the first and the halves come apart: this would build and report on one database
    # while every assertion below ran against another, and `reset()` would TRUNCATE the builder's
    # one, which may be a sibling lane's. Every real caller exports both or neither (the suites
    # derive BRAIN_PG_DB from QUEUE_SCRATCH_DB at import), so refusing costs a correct invocation
    # nothing.
    target = os.environ.get("QUEUE_SCRATCH_DB", "brain_queue_scratch")
    if db and db != target:
        # The remedy names a DERIVED scratch database, never `db` and never `target`. See
        # `_suggested_scratch_db`: in this branch neither input is known-safe to TRUNCATE.
        safe = _suggested_scratch_db()
        sys.stderr.write(
            "preflight: BRAIN_PG_DB is %s but queue-scratch-db.sh would act on %s. Not running\n"
            "this suite: the schema would be built on one database and asserted on the other, and\n"
            "reset() would TRUNCATE %s, which may be another lane's -- or, if it is %s, the live\n"
            "queue. Point BOTH names at a scratch database of your own, and build it first:\n"
            "    queue/bin/queue-scratch-db.sh ensure   # with QUEUE_SCRATCH_DB set as below\n"
            "    QUEUE_SCRATCH_DB=%s BRAIN_PG_DB=%s <suite>\n"
            "DO NOT resolve this by exporting %s into both names: that runs the suite against the\n"
            "database you were connected to, and reset() empties it.\n"
            % (db, target, target, _LIVE_DB, safe, safe, _LIVE_DB))
        sys.exit(1)

    r = subprocess.run([_SCRATCH, "ensure"], capture_output=True, text=True)
    if r.returncode != 0:
        sys.stdout.write(r.stdout)
        sys.stderr.write(r.stderr)
        sys.stderr.write(
            "\npreflight: could not bring %s up to the tree. Not running this suite: every result\n"
            "below would be about the schema, not about the code.\n" % (db or target))
        sys.exit(1)
    # Only the last line: `ensure` on a cold database prints every psql NOTICE the migrations
    # raise, and forty lines of "trigger does not exist, skipping" above a suite header reads as
    # something having gone wrong.
    tail = [ln for ln in r.stdout.strip().splitlines() if ln.startswith("queue scratch ")]
    for line in tail[-1:] or r.stdout.strip().splitlines()[-1:]:
        sys.stdout.write("  preflight: " + line + "\n")
