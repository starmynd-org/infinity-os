"""Reconcile the scratch database to `migrations/` before a suite asserts anything. Task 0153.

Task 0148 put this in `run-all.sh`, which was the right place and not the only one. A suite run
BY ITSELF still ran against whatever the shared `brain_scratch` happened to be, and running one
suite by itself is what a brief asks for: a lane is told to prove one behaviour, not eleven.

The cost of the gap, measured rather than argued. 2026-08-16, task 0139: T6 ran
`test-auto-accept-measurement.py` directly and read 11 passed / 9 FAILED, every one of the nine
`UndefinedColumn: column "produced_by_ref" of relation "work_item" does not exist`, none of them a
defect in the code under test. That is worse than a red suite. It is a red suite that is ABOUT
NOTHING, and it costs whoever reads it a full verification cycle before they learn that. The same
skew runs the other way and is quieter: a database behind the tree can PASS code the live store
would reject, because the constraint that would have caught it has not been applied yet.

`migrate` and not `create`: create does `DROP DATABASE ... WITH (FORCE)`, which would kill a
sibling lane's suite mid-run. Several lanes share this tree and this database. migrate applies
only versions the ledger does not record and is a no-op on a current database, so it is safe to
call from every suite, every run, including concurrently.

Not conditional, not `|| true`, and not "warn and carry on". A migration in the tree that will not
apply is a real finding, and the place to report it is here, in one legible line, rather than
forty assertion failures down the page that all say the same thing in a language about the code.

Deliberately re-run per suite rather than once per `run-all.sh`: lanes commit migrations while the
suites are running, so a reconcile at minute zero does not speak for minute nine.

It also refuses when BRAIN_PG_DB and ENGINE_SCRATCH_DB name different databases, which is the same
defect wearing a second face: reconciling one database and asserting against another. See the
comment on that check for why refusing is safe for every caller that exists.
"""
import os
import subprocess
import sys
from pathlib import Path

_SCRATCH = str(Path(__file__).resolve().parents[2] / "engine/bin/scratch-db.sh")

#: The live store's name, duplicated from `store/schema.py:60`'s default rather than imported,
#: because importing `store` here would open a connection during a preflight whose whole job is to
#: decide whether connecting is safe. If that default ever changes, this must change with it; the
#: cost of the duplication is one grep and the cost of the import is a preflight that connects to
#: the database it is trying to protect.
_LIVE_DB = "brain"


def _suggested_scratch_db():
    """A database name it is always safe to TRUNCATE, for the refusal's remedy line. Task 0353.

    THIS FUNCTION EXISTS BECAUSE THE REMEDY USED TO ECHO THE CALLER'S OWN `BRAIN_PG_DB` BACK AT IT.
    In the branch that prints it, `db != target` holds by construction, so `db` is BY DEFINITION
    not the scratch database -- and in any fleet terminal `db` is `brain`, the live store. An agent
    pasting that line, which is the single most likely thing an agent does with a remedy, exported
    `ENGINE_SCRATCH_DB=brain` and the suite's own `reset()` then TRUNCATEd the live bus:
    `work_item`, `thread`, `artifact`, all of it. The guard was right and its advice was the hazard.

    So the remedy never echoes an input. It derives a name that cannot be the live store:

    * `brain_<agent>_<task>` when the caller is a fleet terminal, which is the per-lane idiom
      already documented at `engine/tests/test-surface-doors.py:32`. Per-lane and not shared, so
      one lane's `reset()` cannot empty a sibling's database mid-run.
    * `brain_scratch` otherwise, which is `scratch-db.sh`'s own default.

    Neither can collide with `_LIVE_DB`: both carry a suffix. The assertion below is kept anyway,
    because a guard whose correctness rests on a naming convention should say so out loud rather
    than rely on the next reader noticing the underscore.
    """
    agent = "".join(c for c in os.environ.get("SWARM_AGENT", "").strip().lower()
                    if c.isalnum())
    task = "".join(c for c in os.environ.get("SWARM_PARENT_TASK", "").strip().lower()
                   if c.isalnum())
    name = "brain_%s_%s" % (agent, task) if agent and task else "brain_scratch"
    # Not reachable by either branch above. Present so that a future edit which makes it reachable
    # fails loudly here instead of quietly in a TRUNCATE.
    assert name != _LIVE_DB, "the remedy line must never name the live store"
    return name


def reconcile(db=None):
    """Bring the scratch database up to `migrations/`, or exit 1 saying why it could not.

    Prints one line on success, which is the point: a reader of the suite output can see WHICH
    schema the result below is about, instead of inferring it.
    """
    # The two names have to agree before anything is done to either. The engine reads BRAIN_PG_DB;
    # scratch-db.sh reads ENGINE_SCRATCH_DB and defaults it to brain_scratch. Set only the first
    # and the halves come apart: this preflight would migrate and report on one database while
    # every assertion below ran against another, which is this task's defect wearing a second
    # face. Worse, the suite's own reset() TRUNCATEs whatever ENGINE_SCRATCH_DB names, so a lane
    # that exported BRAIN_PG_DB alone would empty the shared scratch under a sibling mid-run.
    # Every real caller already exports both (run-all.sh derives one from the other; the two
    # budget suites set both), so refusing costs nothing a correct invocation was doing.
    target = os.environ.get("ENGINE_SCRATCH_DB", "brain_scratch")
    if db and db != target:
        # The remedy names a DERIVED scratch database, never `db` and never `target`. Task 0353:
        # in this branch `db != target`, so neither of them is known-safe to TRUNCATE -- `db` is
        # the live store in every fleet terminal, and `target` is whatever the caller happened to
        # export, which the mismatch itself says is wrong. See `_suggested_scratch_db`.
        safe = _suggested_scratch_db()
        sys.stderr.write(
            "preflight: BRAIN_PG_DB is %s but scratch-db.sh would act on %s. Not running this\n"
            "suite: the schema would be reconciled on one database and asserted on the other, and\n"
            "reset() would TRUNCATE %s, which may be another lane's -- or, if it is %s, the live\n"
            "bus. Point BOTH names at a scratch database of your own, and build it first:\n"
            "    engine/bin/scratch-db.sh create   # with ENGINE_SCRATCH_DB set as below\n"
            "    ENGINE_SCRATCH_DB=%s BRAIN_PG_DB=%s <suite>\n"
            "DO NOT resolve this by exporting %s into both names: that runs the suite against the\n"
            "store it is meant to be isolated from, and reset() empties it.\n"
            % (db, target, target, _LIVE_DB, safe, safe, _LIVE_DB))
        sys.exit(1)

    r = subprocess.run([_SCRATCH, "migrate"], capture_output=True, text=True)
    if r.returncode != 0:
        sys.stdout.write(r.stdout)
        sys.stderr.write(r.stderr)
        sys.stderr.write(
            "\npreflight: could not reconcile %s to migrations/. Not running this suite: every\n"
            "result below would be about the schema, not about the code. If the database was\n"
            "never built, `engine/bin/scratch-db.sh create` builds it (it DROPs, so do not point\n"
            "it at a database another lane is using).\n" % (db or "the scratch database"))
        sys.exit(1)
    # Every line indented, not just the first: `migrate` prints one line per migration it applies,
    # and a half-indented block reads as two things happening rather than one.
    for line in r.stdout.strip().splitlines():
        sys.stdout.write("  preflight: " + line + "\n")
