"""What the spine can and cannot run here, measured rather than asserted.

TWO JOBS, AND THE FIRST ONE IS A REFUSAL.

1. **The database guard.** The packet's constraint is a disposable database only. This module
   refuses any database whose name does not contain `scratch`, and refuses `brain` by name
   regardless. It also refuses the shared default `brain_scratch`, which is not a name anyone
   chose: it is the store every lane's suite reads, and `scratch-db.sh` records the day an agent
   dropped it at ledger 30 by running the script to find out what its verbs were.

2. **The relation census.** Stages 3 to 5 belong to R01 and R02. This module connects to the
   named scratch database and reports which of the required relations exist, by name. **Print the
   denominator**: an absence reported without naming the thing that would have been there is not a
   finding.

   IT WAS WRITTEN WHEN THOSE TABLES WERE ABSENT AND IT EARNED ITS KEEP WHEN THEY ARRIVED. On the
   Admiral's ruling the R integration head was merged into this branch, and the census immediately
   reported four of its six names ABSENT for relations that DO exist under other names. The names
   are corrected below; the mechanism is what caught the error, which is the argument for having
   it rather than a paragraph of prose.

Nothing here writes. There is no CREATE, no INSERT and no migration in this file, so pointing it at
a database cannot change one.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

#: What the spine needs from R01 and R02, by relation name.
#: WHAT EACH LEDGER VERSION MAKES AVAILABLE. Terminal 04's advice, and it is the second correction
#: this census has taken: "do not census by relation name; read `brain.schema_migration` for
#: max(version) and branch on it."
#:
#: THE FIRST CORRECTION was that four of my five names were wrong, taken from packet prose because
#: the schema was missing. THE SECOND was worse and T04 caught it before it reached a receipt: I
#: fixed the names by reading the merged tree, found `brain.receipt`, and ticked the box. **That is
#: a different subsystem** -- the brain's general action-receipt table. The execution receipt is the
#: settled `effect_attempt` row itself. A census that finds `receipt` and ticks it produces a
#: plausible figure about the wrong subject, which is worse than reporting absent.
#:
#: A version number cannot be right about the wrong subject in that way: it is one fact, read from
#: the ledger the migrations themselves write.
LEDGER_CAPABILITIES: tuple[tuple[int, str], ...] = (
    (57, "R01 authority_grant and authority_revocation, with the authority_in_force view"),
    (58, "R02 execution_lease, with the execution_lease_live view"),
    (59, "R02 effect_attempt: an effect is reserved before it happens"),
    (61, "R01 approval, and the roster trigger that refuses a decider this store does not know"),
    (63, "R01 authority scoped to a workspace"),
    (64, "R02 settle proves it held the lease"),
    (66, "R01 approval_spendable: single-use spend via consumed_state"),
    (67, "R02 completeness and effect_state, and outcome renamed succeeded -> success"),
)

#: What the spine's stages need, by the ledger version that provides it. A stage whose version is
#: not reached is WAITING ON A RANGE, which is a different state from a stand-in: nobody has to
#: write it, it has to land.
STAGE_REQUIREMENTS: tuple[tuple[str, int], ...] = (
    ("3 approval recorded (R01)", 61),
    ("4 lease and reservation (R02)", 59),
    ("5 receipt settled", 64),
    ("5 receipt carries completeness", 67),
)

#: Corroboration only, and deliberately NOT a checklist. `receipt` is absent from this tuple on
#: purpose: it exists and it is the wrong subsystem.
#:
#: AND A LIST LIKE THIS CAN NEVER BE A COVERAGE CLAIM, which is CAP14's finding against T04's census
#: and the reason the STAGE GATES ABOVE ASK THE LEDGER INSTEAD. Migration 66 enforces single use
#: with five `ADD CONSTRAINT`, a partial unique index and a trigger -- not one `ADD COLUMN` -- so a
#: census of relations, or even of columns, sees the things those constraints operate on and calls
#: a store covered while the enforcement is absent. Across 65 to 67 there are 54 DDL statements; a
#: column census sees 9, and none of the 18 that ENFORCE rather than declare.
#:
#: So an enumeration is another hand-written list and inherits the same failure one remove out.
#: Membership of a migration version covers its constraints, indexes and triggers by construction,
#: because the version is what was applied. That is why `STAGE_REQUIREMENTS` is keyed on versions
#: and this tuple is corroboration a reader may sanity-check against, nothing more.
CORROBORATING_RELATIONS: tuple[tuple[str, str], ...] = (
    ("R01", "authority_grant"),
    ("R01", "authority_revocation"),
    ("R01", "approval"),
    ("R02", "execution_lease"),
    ("R02", "effect_attempt"),
)

#: The shared scratch store every lane's suite reads. Not a name anyone chose for this run.
SHARED_DEFAULT = "brain_scratch"


class UnsafeDatabase(RuntimeError):
    """The named database is not one this spine may touch."""


def guard_database_name(name: str | None) -> str:
    """Return the name, or refuse it. The refusal is the point of this function."""
    if not name or not name.strip():
        raise UnsafeDatabase(
            "no database was named. This spine has no default: a run against a store nobody "
            "named is how the shared scratch database was dropped once already"
        )
    name = name.strip()
    if name == "brain":
        raise UnsafeDatabase("refusing the live store 'brain'")
    if "scratch" not in name:
        raise UnsafeDatabase(
            "refusing " + repr(name) + ": a spine run writes rows, so the database name must "
            "contain 'scratch' and be disposable"
        )
    if name == SHARED_DEFAULT:
        raise UnsafeDatabase(
            "refusing the shared " + repr(SHARED_DEFAULT) + ": every lane's suite reads it, so a "
            "spine run there would leave its rows in other lanes' evidence. Name a private one"
        )
    return name


@dataclass(frozen=True)
class Census:
    """What the ledger says this store can do, and whether a connection happened at all.

    `ledger` is the authority. `present` corroborates it by relation name, and a disagreement
    between the two is itself worth seeing: it means either a migration was applied by hand or the
    names have moved again.
    """

    database: str
    connected: bool
    ledger: int | None = None
    #: Every version this store has RECORDED. Membership, not magnitude: see `has`.
    recorded: frozenset = frozenset()
    present: tuple[str, ...] = ()
    absent: tuple[str, ...] = ()
    error: str = ""

    def has(self, version: int) -> bool:
        """Is this exact version recorded? NOT `max >= version`.

        Magnitude lies in a tree with holes, and this lane's own tree is one: it carries 1..64 plus
        migration 68, with 65 to 67 on Terminal 04's unmerged branch. Asked by magnitude, the store
        answered "67 yes" and reported every stage ready, because MY migration had raised the
        maximum past versions that were never applied.
        """
        return version in self.recorded

    @property
    def can_run_real_runtime_stages(self) -> bool:
        """Every stage except the one waiting on 67. Read from the requirements, not restated."""
        return self.connected and all(
            self.has(v) for stage, v in STAGE_REQUIREMENTS if "completeness" not in stage
        )

    def rows(self) -> list[str]:
        out = [
            f"database        {self.database}",
            f"connected       {'yes' if self.connected else 'no'}"
            + (f"  ({self.error})" if self.error else ""),
            f"ledger          max={self.ledger if self.ledger is not None else 'UNKNOWN'} "
            f"recorded={len(self.recorded)}"
            + (f" holes={sorted(set(range(1, self.ledger + 1)) - self.recorded)}"
               if self.ledger else ""),
        ]
        # THE SAME CORRECTION, TWICE MORE. CAP14-REV-046 named the relation list; the two loops
        # here had the identical defect and were not named. Unconnected, `has()` is False for every
        # version, so this printed `NO` and `WAITING ON 67` -- both of which state a measurement
        # nobody took. Fixing only the line that was reported would have left the same error in the
        # two places above it, which is how a finding gets closed without being fixed.
        unknown = not self.connected
        out.append("capabilities, by the version that provides each:")
        for version, what in LEDGER_CAPABILITIES:
            mark = "?   " if unknown else ("yes " if self.has(version) else "NO  ")
            out.append(f"  {version:>3}  {mark} {what}")
        out.append("stages:")
        for stage, version in STAGE_REQUIREMENTS:
            if unknown:
                state = "NOT ASKED"
            else:
                state = "ready" if self.has(version) else f"WAITING ON {version}"
            out.append(f"  {stage:<34} needs {version:>3}  {state}")
        out.append("corroborating relations (NOT a checklist; `receipt` is deliberately absent, "
                   "it is a different subsystem):")
        for lane, rel in CORROBORATING_RELATIONS:
            # SPINE-CENSUS-01 (CAP14-REV-046). A relation is only ABSENT if somebody LOOKED and did
            # not find it. With no connection nobody looked, and printing ABSENT there states a
            # measurement that was never taken -- the exact error this file was built to avoid,
            # surviving in the renderer after the model had been fixed. The model always kept
            # `connected` separate; only this line was reading it as a result.
            if not self.connected:
                status = "NOT ASKED"
            else:
                status = "present" if rel in self.present else "ABSENT"
            out.append(f"  {lane}  {rel:<22} {status}")
        return out


def census(database: str | None = None, **connect_kwargs: Any) -> Census:
    """Connect read-only and report which required relations exist.

    A connection failure is reported as a census with `connected=False`, not raised: "I could not
    look" and "I looked and found nothing" are different answers and a caller must be able to tell
    them apart.
    """
    name = guard_database_name(database or os.environ.get("SPINE_SCRATCH_DB"))
    try:
        import psycopg2
    except ImportError as exc:  # pragma: no cover - environment, not logic
        return Census(name, False, error=f"psycopg2 is not importable: {exc}",
                      absent=tuple(r for _, r in CORROBORATING_RELATIONS))

    params = {
        "dbname": name,
        "host": connect_kwargs.pop("host", os.environ.get("PGHOST", "127.0.0.1")),
        "port": connect_kwargs.pop("port", os.environ.get("PGPORT", "5432")),
    }
    user = connect_kwargs.pop("user", os.environ.get("PGUSER"))
    if user:
        params["user"] = user
    password = connect_kwargs.pop("password", os.environ.get("PGPASSWORD"))
    if password:
        params["password"] = password
    params.update(connect_kwargs)

    wanted = [r for _, r in CORROBORATING_RELATIONS]
    try:
        conn = psycopg2.connect(**params)
    except Exception as exc:  # noqa: BLE001 - any connection failure is the same answer here
        return Census(name, False, error=str(exc).strip().splitlines()[0], absent=tuple(wanted))

    try:
        with conn.cursor() as cur:
            cur.execute("SELECT version FROM brain.schema_migration")
            recorded = frozenset(row[0] for row in cur.fetchall())
            ledger = max(recorded) if recorded else None
            cur.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_name = ANY(%s)",
                (wanted,),
            )
            found = {row[0] for row in cur.fetchall()}
    finally:
        conn.close()

    return Census(name, True, ledger=ledger, recorded=recorded,
                  present=tuple(r for r in wanted if r in found),
                  absent=tuple(r for r in wanted if r not in found))
