-- 0008_queue_defer_lineage.sql -- `produced_by_ref` and `resolution_status` beside
--                                `brain.queue_defer.produced_by`.
--
-- Additive and re-runnable. It adds two columns and two CHECK constraints to one table, creates
-- nothing, drops nothing, and edits no file another lane owns. `0007_queue.sql` is applied and
-- stays exactly as it is: an applied migration is history, and history is amended by a later
-- migration rather than by editing the file that already ran.
--
-- WHY THIS EXISTS. Migration 5 (`migrations/0005_lineage_resolution.sql`, task 0103) establishes
-- the invariant:
--
--     WHEREVER `produced_by` EXISTS, `produced_by_ref` AND `resolution_status` EXIST BESIDE IT.
--
-- and ships `brain.lineage_column_drift` so that a later migration adding half a lineage record
-- is caught rather than discovered. Migration 7 landed 24 minutes after migration 5 and created
-- `brain.queue_defer` with `produced_by` and neither of the other two. The view did its job:
--
--   $ SELECT * FROM brain.lineage_column_drift;
--    table_name  | has_ref | has_status
--   -------------+---------+------------
--    queue_defer | f       | f
--
-- Measured against the live store at 13:36Z on 2026-08-16, and again by T5 at 12:56Z, two
-- minutes apart, so it is not a mid-migration snapshot.
--
-- WHAT IS ACTUALLY LOST WITHOUT THIS. `produced_by` alone can only say the answer was nothing.
-- It cannot say what was asked for, and it cannot tell `ambiguous` from `unresolved` -- both are
-- `produced_by IS NULL`. Those are different repairs: an ambiguous reference has candidates and
-- is fixed by disambiguating; an unresolved one has none and is fixed by creating the node or
-- correcting the string. A deferral is exactly the row where that matters, because a defer is
-- the queue's record of something deliberately not done yet, read later by someone who was not
-- there.
--
-- WHY 8. Read from `brain.schema_migration`, never from `ls`. The ledger held 1 0001_initial,
-- 2 0002_roles, 3 0003_budget (file at `budget/schema/`), 4 0004_touch_wager_2c,
-- 5 0005_lineage_resolution, 6 0006_signal_numeric_vocabulary, 7 0007_queue (file HERE, at
-- `queue/schema/`). Three of the eight migration files are not in `migrations/`, which is why a
-- directory listing is the wrong source. 0007 itself was written as 0005 and was refused by its
-- own version guard; the same guard is below, and it raises rather than skipping its ledger row.
--
-- THE FOURTH STATE IS NULL, AND IT IS KEPT. `resolution_status` is nullable with no default,
-- matching migration 5 on the other 22 tables. NULL means no resolution was ever attempted and
-- is not a synonym for 'unresolved': a row that never made a claim about the brain is not a row
-- whose claim failed, and scoring it as dangling lineage would be a measurement error rather
-- than a finding. A `NOT NULL DEFAULT 'resolved'` here would delete that distinction and would
-- additionally make the coherence CHECK below refuse every insert that omits a producer.
--
-- `produced_by` STAYS TEXT. No foreign key and no case constraint, settled with evidence in
-- D-CROSSTALK slot 3 and not reopened: 189 duplicate ids at brain HEAD means there is no unique
-- key an FK could point at, and 1,577 of 6,086 id-bearing nodes carry uppercase `BOYD-*` ids.
-- The only CHECKs added are on `resolution_status`, a closed vocabulary this repo owns, and on
-- the coherence between the two, which is a lie-detector rather than a format.
--
-- Task 0115, parent 0103.

\set ON_ERROR_STOP on

-- ---------------------------------------------------------------- version guard
--
-- Before BEGIN, so a refusal lands nothing partial. Every migration ends with ON CONFLICT DO
-- NOTHING, so a file claiming a taken version would otherwise apply its DDL and silently skip
-- its ledger row, leaving the store one migration ahead of what the ledger reports.

DO $$
DECLARE taken text;
BEGIN
  SELECT name INTO taken FROM brain.schema_migration WHERE version = 8;
  IF taken IS NOT NULL AND taken <> '0008_queue_defer_lineage' THEN
    RAISE EXCEPTION 'schema version 8 is already held by %, not 0008_queue_defer_lineage. Pick '
                    'the next version by reading brain.schema_migration, never by listing a '
                    'directory -- three of this repo''s migration files are not in migrations/.',
                    taken;
  END IF;
END $$;

BEGIN;

SET search_path TO brain, public;

-- ---------------------------------------------------------------- pre-flight, not an assertion
--
-- The coherence CHECK below is added VALIDATED, so it is evaluated against every existing row.
-- `brain.queue_defer` was measured at 0 rows immediately before this ran, which makes that free;
-- this re-makes the check at apply time rather than trusting that finding to still hold on a
-- store several lanes are writing to at once. If rows have landed since, they cannot be
-- retro-scored from here -- a row whose producer was never recorded has no ref to recover -- so
-- this raises and says so instead of guessing a status for them.
--
-- SCOPED TO THE FIRST APPLICATION, and that scoping is not a softening. Measured: guarding on
-- the row count alone made this file raise on every re-run once the table had rows, which
-- contradicts the re-runnable property stated at the top and would have turned a legitimate
-- replay into a failure. Once the columns exist the backfill question is already settled, so the
-- guard has nothing left to protect and steps aside.

DO $$
DECLARE n bigint;
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
              WHERE table_schema='brain' AND table_name='queue_defer'
                AND column_name IN ('produced_by_ref','resolution_status')) THEN
    RETURN;  -- already applied; this is a replay, not a first backfill
  END IF;

  SELECT count(*) INTO n FROM brain.queue_defer;
  IF n > 0 THEN
    RAISE EXCEPTION
      '0008_queue_defer_lineage: brain.queue_defer holds % row(s), which did not exist when this '
      'file was written. Every existing row will get resolution_status NULL (no resolution was '
      'ever attempted), which is honest but is a claim about rows this file did not see. '
      'Re-read those rows, decide their status deliberately, then remove this guard.', n;
  END IF;
END $$;

-- ---------------------------------------------------------------- the two lineage columns

ALTER TABLE brain.queue_defer ADD COLUMN IF NOT EXISTS produced_by_ref   text;
ALTER TABLE brain.queue_defer ADD COLUMN IF NOT EXISTS resolution_status text;

COMMENT ON COLUMN brain.queue_defer.produced_by_ref IS
  'The raw reference string that was resolved, carried on every status including ''resolved''. '
  'On a failure it is the whole of what is left: the row says what was ASKED FOR rather than '
  'only that the answer was nothing. A NULL produced_by with no ref beside it is a dead end for '
  'whoever has to reconcile this deferral later.';

COMMENT ON COLUMN brain.queue_defer.resolution_status IS
  'resolved | ambiguous | unresolved, or NULL for the fourth state: no resolution was ever '
  'attempted. NULL is NOT a synonym for ''unresolved''. ''ambiguous'' (candidates exist, fix by '
  'disambiguating) is distinct from ''unresolved'' (nothing matched, fix by creating the node or '
  'correcting the string) on purpose -- both carry produced_by IS NULL, so without this column '
  'they collapse into one and the repair is no longer readable off the row.';

-- ---------------------------------------------------------------- the two CHECKs
--
-- Dropped by name before being added, so this file is re-runnable. Names match migration 5's
-- convention on the other 22 tables (<table>_resolution_status_enum, <table>_lineage_coherent),
-- which is what lets one query find the tables that are missing them.

ALTER TABLE brain.queue_defer DROP CONSTRAINT IF EXISTS queue_defer_resolution_status_enum;
ALTER TABLE brain.queue_defer ADD  CONSTRAINT queue_defer_resolution_status_enum CHECK (
  resolution_status IS NULL OR resolution_status IN ('resolved','ambiguous','unresolved')
);

-- NEVER A FABRICATED ID, and never a producer claimed for a resolution that did not produce one.
-- This refuses the superuser at a psql prompt, which application validation cannot: the same
-- posture as migration 2's grants, migration 4's enums and migration 7's two gates.
ALTER TABLE brain.queue_defer DROP CONSTRAINT IF EXISTS queue_defer_lineage_coherent;
ALTER TABLE brain.queue_defer ADD  CONSTRAINT queue_defer_lineage_coherent CHECK (
  CASE resolution_status
    WHEN 'resolved'   THEN produced_by IS NOT NULL
    WHEN 'ambiguous'  THEN produced_by IS NULL
    WHEN 'unresolved' THEN produced_by IS NULL
    ELSE true
  END
);

-- No grants. 0007_queue's grant on this table is TABLE-level
-- (GRANT SELECT, INSERT, UPDATE ON ... brain.queue_defer ... TO brain_runtime), so new columns
-- inherit. Verified rather than assumed, against the catalog and not the DDL text:
--
--   SELECT a.attname FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid
--     JOIN pg_namespace n ON n.oid = c.relnamespace
--    WHERE n.nspname='brain' AND c.relname='queue_defer' AND a.attnum > 0
--      AND a.attacl IS NOT NULL;                             -> 0 rows: no column ACL exists.
--   SELECT relacl FROM pg_class ... -> {... brain_runtime=arw/postgres}: the grant is on the
--                                      relation, so a column added here is covered by it.
--
-- DO NOT use information_schema.column_privileges for this. It EXPANDS a table-level grant into
-- one row per column per privilege, so it reports 187 rows for this table and would read as 187
-- column-level grants. It cannot distinguish the two and can never be 0. `pg_attribute.attacl`
-- is the only place a real column-level grant shows up.

INSERT INTO brain.schema_migration (version, name)
VALUES (8, '0008_queue_defer_lineage') ON CONFLICT (version) DO NOTHING;

COMMIT;
