# Applying migration 29 to the live `brain`

**APPLIED TO LIVE 2026-08-18 at approximately 19:00Z, by the commander, from a plain shell rather
than a fleet terminal. Nothing in this document is left to run.** Independently re-measured by T2
on task 0191 at 2026-08-18T22:40:49Z by reading the ledger rather than an exit code; the numbers are under
"What was actually applied" below.

**THIS DOCUMENT WAS WRITTEN AGAINST LEDGER HEAD 30. LIVE IS AT 31.** A document about a migration
is only readable next to the ledger it was written beside, so this one names its own. Every
version number below was true while the head in the tree was 30
(`queue/schema/0014_queue_item_options`); the head moved to 31
(`migrations/0031_question_withdrawal`) the same night, and it will move again. Before trusting a
number here, run `SELECT max(version) FROM brain.schema_migration` and read a mismatch as this
document being old, not the store being wrong. Any successor document must carry the same line.

The Claude Code permission classifier refuses agent writes to the live `brain` database. That is
the rule and it is not to be routed around: prove it on a clone, hand over the exact line. Four
migrations were applied that way in v1 and two more in V2, and it has worked every time. It
worked here too, and the record is worth keeping: D3 was refused on 0191 even for a rollback-only
rehearsal that commits nothing, did not route around it, and asked (q0199). The commander answered
by running the line himself from outside the fleet.

## The one line

Run as the brain owner:

```bash
psql -d brain -v ON_ERROR_STOP=1 -f migrations/0029_image_attachment.sql
```

Then confirm by READING THE LEDGER, not by a successful exit. A `psql` that exits 0 having
applied nothing is the failure these four queries exist to catch:

```bash
psql -d brain -tAc "SELECT version, name FROM brain.schema_migration WHERE version = 29"
psql -d brain -tAc "SELECT count(*) FROM brain.image_attachment"
psql -d brain -tAc "SELECT count(*) FROM information_schema.columns
                     WHERE table_schema='brain' AND table_name='image_attachment'"
psql -d brain -tAc "SELECT count(*) FROM information_schema.columns
                     WHERE table_schema='brain' AND data_type IN ('bytea','oid')"
```

Expected: `29|0029_image_attachment`, `0`, `27`, and **`0`**. (27, not 25: migration 15 adds
`produced_by_ref` and `resolution_status` to any table carrying `produced_by`, by construction,
and it does that to this one too.) The last one is the lane's whole
posture as a query — no image bytes anywhere in the schema — and it was `0` before this migration
and must still be `0` after it.

Re-running the file today is a no-op, not a second apply, so a reader who missed the status line
above and ran it anyway did no damage: the guard at `migrations/0029_image_attachment.sql:62-72`
raises only if version 29 is held by a DIFFERENT name, the table is `CREATE TABLE IF NOT EXISTS`,
and the ledger row is `INSERT ... ON CONFLICT (version) DO NOTHING`.

## What was actually applied, and the second source directory nobody expected

The chain was deeper than the ledger gap looked. Four files went in, in this order:

    queue/schema/0013_queue_open_shows_what_the_fleet_will_not_take.sql   -> ledger 28
    migrations/0029_image_attachment.sql                                  -> ledger 29
    queue/schema/0014_queue_item_options.sql                              -> ledger 30
    migrations/0031_question_withdrawal.sql                               -> ledger 31

**28 AND 30 WERE NEVER MISSING MIGRATIONS. THEY LIVE IN `queue/schema/`, NOT `migrations/`.** A
reader who lists `migrations/` sees 0025, 0026, 0027, 0029 and no 0028 and concludes a number was
skipped or a file was lost. Neither happened: two directories with independent filenames feed one
ledger, `brain.schema_migration`, so a filename number and a ledger version are different things
and `migrations/0029` is the only file where the two happen to agree. `engine/bin/scratch-db.sh`
already knows this and globs all of them; a human reading one directory does not.

There are **THREE** such directories, not the four this document said in its first draft and that
`migrations/0029_image_attachment.sql:60,68` still says in its comments:

| directory | files | writes `brain.schema_migration` |
|---|---|---|
| `migrations/` | 21 | yes, holds 0029 |
| `queue/schema/` | 8 | yes, holds ledger 28 and 30 |
| `budget/schema/` | 2 | yes |
| `voice/schema/` | **0** | exists and is empty |
| `ingest/schema/` | 6 | **no**: grepped all six, not one mentions `schema_migration` |

The comment inside 0029 is left as written on purpose: that file has been applied to live and its
text is history, not documentation to maintain.

**Verified on live `brain` by T2 at 2026-08-18T22:40:49Z**, as bootstrap superuser, reading the
ledger and not an exit code (the stamp is `date -u` printed on the same command line as the query,
not an estimate):

    ledger max = 31, ledger rows = 31, brain base tables = 35
      31|0031_question_withdrawal   30|0014_queue_item_options
      29|0029_image_attachment      28|0013_queue_open_shows_what_the_fleet_will_not_take
    brain.image_attachment          : exists, 27 columns, 0 rows
    bytea/oid columns in schema brain: 0        <- the pointer-not-blob posture held on live
    brain.queue_item_option         : exists
    brain.question.withdrawn_at     : exists

All four values this document predicts (`29|0029_image_attachment`, `0`, `27`, `0`) reproduce
exactly. A fresh `engine/bin/scratch-db.sh ensure` on a new name built the identical shape at
ledger 31, and all ten console rooms plus six detail rooms served 200 against live afterwards.

One thing worth keeping from the apply: `queue/schema/0014` **refused to run** while 0013 was
absent, rather than degrading. Re-running it over the older arm would have restored
`w.actor_type = 'human'` and hidden the operator's unclassified rows from him again. A migration
that refuses is the behaviour to copy.

## The version number, and the race it survived

Read on the live store at **2026-08-18T18:35Z**: `brain.schema_migration` was at **27**. Version
**28** was already spoken for by `queue/schema/0013_queue_open_shows_what_the_fleet_will_not_take.sql`,
which is written and **not yet applied**, so the ledger alone would have handed this file 28.
Version **30** was taken by `queue/schema/0014_queue_item_options.sql` (V3, task 0165) at some
point during this task's afternoon — that one appeared in the tree while this file was being
written. Scan `brain.schema_migration` **AND** ~~all four schema directories (`migrations/`,
`budget/schema/`, `queue/schema/`, `voice/schema/`)~~ every schema directory before taking the
next number, and re-scan immediately before writing the `INSERT`, not at the start of the task.
The file carries its own guard: it raises rather than applies if 29 turns out to be held by
another name.

> **Corrected 2026-08-18, between 22:33Z and 22:40Z, T2 on task 0191.** It is THREE directories, not four, and the
> ones to scan are `migrations/`, `queue/schema/` and `budget/schema/`. `voice/schema/` exists
> and holds zero `.sql` files; `ingest/schema/` holds six and none of them writes
> `brain.schema_migration`, so it is correctly excluded. Also: **31 was not the next number by
> the time anyone read this.** 31 went to `migrations/0031_question_withdrawal.sql` the same
> night and live carries it. Take the next number from a scan you ran yourself, never from this
> paragraph.

**Apply order.** 28, 29 and 30 are independent — none references anything the others create — but
the ledger is one sequence and applying it out of order leaves a gap a later reader has to
explain. If 28 is still unapplied when you run this, apply it first.

> **What actually happened, 2026-08-18 ~19:00Z.** The commander applied 28, then 29, then 30,
> then 31, in that order, and the sequence has no gap. `queue/schema/0014` (ledger 30) enforced
> the order itself: it refused to run while 28 was absent instead of degrading. See "What was
> actually applied" above.

## What was proven, and how

Built from an empty database with every lane's schema in ledger order, 2026-08-18:

```bash
export ENGINE_SCRATCH_DB=brain_t2_0167v5 BRAIN_PG_DB=brain_t2_0167v5
engine/bin/scratch-db.sh create
```

```
scratch database brain_t2_0167v5 ready: 35 tables, schema_migration 30
scratch database brain_t2_0167v5: operator login mapped, brain.human_role 1 row(s)
```

`35` tables, up from 34: `brain.image_attachment` (34 already included V3's `queue_item_options`).
The migration then carried the whole lane's traffic: six attachments across three subject kinds,
a deleted file rendered as a MISSING finding, a rewritten file rendered as CHANGED, four failed
attaches, a detach and a re-attach, and `web/tests/test_images.py` at **93 assertions, 0 failed**.

## What it changes, and what it cannot break

It adds ONE table, one view, one function, one trigger and three grants. It **alters no existing
table and no existing column**, so every reader of every other relation is untouched literally
and not just semantically. `brain_runtime` and (where provisioned) `brain_operator` gain SELECT,
INSERT and UPDATE on the new table; **nobody but the owner gains DELETE**, for the reason
migration 25 gives — the row is the evidence that an attach was attempted, and evidence a caller
can silently empty is not evidence.

The one thing to know before applying: `image_attachment_one_live_idx` is a partial UNIQUE index
over `(subject_type, subject_id) WHERE state IN ('landing','attached')`. It enforces the brief's
one-image-in-place rule at the table, so a future lane that wants a gallery has to change the
schema in a diff someone reviews rather than by writing a second row.

## Rolling it back

```sql
BEGIN;
DROP VIEW IF EXISTS brain.image_attachment_health;
DROP TABLE IF EXISTS brain.image_attachment;              -- CASCADEs the trigger
DROP FUNCTION IF EXISTS brain.image_attachment_subject_exists();
DELETE FROM brain.schema_migration WHERE version = 29;
COMMIT;
```

Nothing else in the schema references it, so this is a clean drop — **and it destroys every
attachment row**, which is the pointers and the hashes, not the images. The images are files on
disk and this statement does not touch one of them. That asymmetry is the posture working: a
rollback of the store costs the index, never the content.
