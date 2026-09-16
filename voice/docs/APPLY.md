# Applying migrations 24 and 25 to the live `brain`

**Proven on a clone. NOT applied to live. Applying it is the operator's, per V00.**

The Claude Code permission classifier refuses agent writes to the live `brain` database. That is
the rule and it is not to be routed around: prove it on a clone, hand over the exact line. Four
migrations were applied that way in v1 and it worked every time.

## What was proven, and how

Built from an empty database with every lane's schema in ledger order, 2026-08-18:

```bash
export ENGINE_SCRATCH_DB=brain_t4_0377 BRAIN_PG_DB=brain_t4_0377
engine/bin/scratch-db.sh create
```

```
scratch database brain_t4_0377 ready: 33 tables, schema_migration 25
scratch database brain_t4_0377: operator login mapped, brain.human_role 1 row(s)
```

`33` tables, up from 32: `brain.voice_capture`. `schema_migration 25`, up from 23. Both
migrations then carried the whole lane's traffic: 19 captures, 66 demonstration assertions, the
Monday monologue, and a 5m16s recording landing as an objective. Neither has been rolled back and
nothing else in the tree changed to accommodate them.

```
 24  migrations/0024_objective_voice_intake.sql
 25  migrations/0025_voice_capture.sql
```

## The two lines

Run as the brain owner, in this order. 24 first: 25's constraints reference nothing in 24, but
the ledger is one sequence and applying it out of order leaves a gap a later reader has to
explain.

```bash
psql -d brain -v ON_ERROR_STOP=1 -f migrations/0024_objective_voice_intake.sql
psql -d brain -v ON_ERROR_STOP=1 -f migrations/0025_voice_capture.sql
```

Then confirm, and confirm by reading the ledger rather than by a successful exit:

```bash
psql -d brain -tAc "SELECT version, name FROM brain.schema_migration WHERE version >= 24"
psql -d brain -tAc "SELECT count(*) FROM brain.voice_capture"
psql -d brain -tAc "SELECT column_name FROM information_schema.columns
                     WHERE table_schema='brain' AND table_name='objective'
                       AND column_name='transcription_status'"
```

Expected: two ledger rows, `0`, and one column name. A `psql` that exits 0 having applied nothing
is the failure mode these three queries exist to catch.

## What they change, and what they cannot break

**24** adds nine nullable columns and three CHECKs to `brain.objective`. Existing text intake is
untouched **literally, not just semantically**: `intake_format` defaults to `'text'`, every other
column is nullable, all three CHECKs pass for a row with all nine NULL, and the `intake`
transition still runs a **byte-identical INSERT** for a text objective with no capture id
(`engine/swarm_engine/transitions.py`). That last part is deliberate: a text intake that started
naming columns which do not exist on live would break the drop-folder path that works today, in
order to serve a format that is not in use yet.

**25** creates `brain.voice_capture` and `brain.voice_capture_health`, grants
`SELECT, INSERT, UPDATE` to `brain_runtime` (and to `brain_operator` where that role exists),
and grants **no DELETE to anyone but the owner**. A capture row is the evidence an attempt was
made, and an evidence table a caller can silently empty is not evidence.

Both files are one transaction with a top-level `BEGIN`, so a file that fails applies nothing.
Both are re-runnable: `ADD COLUMN IF NOT EXISTS`, `CREATE TABLE IF NOT EXISTS`,
`DROP CONSTRAINT IF EXISTS` before each `ADD CONSTRAINT`, and
`INSERT ... ON CONFLICT (version) DO NOTHING`.

## The numbering, and the thing that went wrong before

The file now numbered **24** was written on 2026-08-18 as `0023_objective_voice_intake.sql` and
**recorded no `brain.schema_migration` row at all**. `engine/bin/scratch-db.sh` reads each file's
version out of its own INSERT and refuses a file that records none, so **every door of that
script returned rc=1 for as long as it sat in the working tree** -- for every lane, not only this
one. Measured before the fix: `scratch-db.sh ledger` rc=1 and `scratch-db.sh ensure` rc=1, both
printing `0023_objective_voice_intake.sql records no brain.schema_migration row.` Any suite run
in this tree in that window read as a harness-owned red.

The `0023` prefix also collided: `queue/schema/0012_operator_time_entry.sql` records ledger
version 23. Prefixes are per-lane counters and they lie; the recorded version is the key. Both
files now carry a `DO $$` guard that refuses to apply if another name already holds their number.

**Next free ledger version at 2026-08-18T08:00Z was 26.**

## Rolling back

Nothing here needs a rollback to be safe -- 24's columns are nullable and 25's table is new and
unreferenced -- but if the operator wants one:

```sql
BEGIN;
DROP VIEW  IF EXISTS brain.voice_capture_health;
DROP TABLE IF EXISTS brain.voice_capture;
DELETE FROM brain.schema_migration WHERE version = 25;
COMMIT;
```

24 is deliberately **not** given a rollback. Dropping those columns would drop the pointer and
the hash of every retained recording, which is the one thing in this design that cannot be
recomputed from anything else. If 24 has to go, the audio pointers come out to a file first, and
that is a decision, not a script.
