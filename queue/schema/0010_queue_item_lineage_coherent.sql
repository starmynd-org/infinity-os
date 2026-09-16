-- 0010_queue_item_lineage_coherent.sql -- the two NAMED lineage constraints on
--                                        `brain.queue_item`, and the fourth state restored.
--
-- Additive and re-runnable. It touches one table, creates nothing, drops nothing another lane
-- owns, and edits no applied migration file. `0007_queue.sql` stays exactly as it is: an applied
-- migration is history, and history is amended by a later migration rather than by editing the
-- file that already ran.
--
-- WHY THIS EXISTS, AND WHY NO VIEW CAUGHT IT. Migration 5 (`migrations/0005_lineage_resolution.sql`,
-- task 0103) establishes the invariant
--
--     WHEREVER `produced_by` EXISTS, `produced_by_ref` AND `resolution_status` EXIST BESIDE IT
--
-- and ships `brain.lineage_column_drift` to catch a later migration that adds half a lineage
-- record. That view checks that the three COLUMNS exist. It does not check that the two CHECK
-- constraints exist. `brain.queue_item` is the table that falls through that gap, and it is
-- invisible to the view BY CONSTRUCTION: migration 7 created it with all three columns, so it
-- has never appeared in the drift query and never will.
--
-- Measured against the live `brain` at 17:38Z on 2026-08-16, by the constraint half of the
-- invariant rather than the column half:
--
--     24 tables carry produced_by
--     23 carry BOTH <tbl>_resolution_status_enum AND <tbl>_lineage_coherent
--      1 carries the columns and NEITHER named constraint      -> queue_item, this file
--
-- and `SELECT * FROM brain.lineage_column_drift` returned 0 rows at the same moment. So the view
-- reported the schema clean while the one table below could not hold an honest lineage row.
--
-- WHAT `queue_item` HAD. One system-named check, the enum and nothing more:
--
--     queue_item_resolution_status_check ::
--       CHECK (resolution_status = ANY (ARRAY['resolved','ambiguous','unresolved']))
--
-- plus `resolution_status text NOT NULL DEFAULT 'resolved'` with `produced_by` left nullable
-- (`queue/schema/0007_queue.sql:306`).
--
-- THE SYSTEM-NAMED CHECK IS NOT WRONG, IT IS INVISIBLE. A CHECK constraint in Postgres passes
-- when its expression evaluates to NULL, so `resolution_status = ANY (...)` would have admitted
-- SQL NULL perfectly well on its own -- the NOT NULL is what forbade the fourth state, not the
-- check. What the system name costs is discovery: migration 5's convention is what lets ONE
-- query find every table missing the pair, and a constraint Postgres named cannot be found by
-- it. That is why this file drops a working constraint and adds an equivalent one under the
-- conventional name. Nothing about the enum's meaning changes.
--
-- WHAT WAS MEASURED, not argued. All four run as the SUPERUSER at a psql prompt on a scratch, so
-- it is the database deciding and not application code:
--
--   A  INSERT (source_type, source_id) only          -> ACCEPTED, resolution_status = 'resolved',
--                                                       produced_by NULL
--   B  resolution_status = NULL                      -> REFUSED by the NOT NULL
--   C  produced_by NULL + 'resolved', explicit       -> ACCEPTED
--   D  produced_by 'd3-ingest' + 'unresolved'        -> ACCEPTED
--
-- A is the one that matters, because A is `queue/human_queue/transitions.py:83`, the statement
-- run for EVERY item that enters the queue:
--
--     INSERT INTO brain.queue_item (source_type, source_id) VALUES (%s, %s) RETURNING *
--
-- It names no producer, and the row it stores asserts that a resolution occurred. Not a bug a
-- caller has to commit: what happens when a caller says nothing. `_mark_disposed` at line 612 is
-- the same shape. `brain.queue_item` holds 0 rows today, so nothing is corrupt yet; that stops
-- being true the first time the queue is populated.
--
-- C and D are the two incoherent directions the other 23 tables refuse by name -- a producer
-- claimed for a resolution that produced nothing, and a resolution claimed with no producer.
-- D is not in this task's brief; it was found by running the case.
--
-- THE FOURTH STATE IS NULL, AND IT IS RESTORED HERE. `resolution_status` becomes nullable with
-- no default, matching migration 5 on the other 23 tables. NULL means no resolution was ever
-- attempted and is NOT a synonym for 'unresolved': a row that never made a claim about the brain
-- is not a row whose claim failed, and scoring it as dangling lineage would be a measurement
-- error rather than a finding. `brain.session` holds 1,131 rows with `produced_by = 'd3-ingest'`
-- -- a producer NAME, not a resolved node -- which is exactly the shape that needs the fourth
-- state to be readable. `NOT NULL DEFAULT 'resolved'` deletes that distinction, and combined
-- with the coherence CHECK below it would additionally refuse every insert that omits a
-- producer, which is currently all of them.
--
-- WHAT DROPPING THE NOT NULL CHANGES FOR CALLERS, stated plainly because it is the
-- operator-visible part of this file. A writer that omits `resolution_status` gets NULL after
-- this instead of 'resolved'. Checked before writing this, not assumed: `resolution_status`
-- appears ZERO times in any Python file under `queue/` or `web/`. The eight sites that touch
-- `brain.queue_item` never name it, `reads.py:48` pulls `SELECT *` into an overlay dict that no
-- downstream reader consults for it, and `store/transitions.py` has no generic lineage stamp
-- that would fill it. On this table the column is written only by its DEFAULT and read by
-- nobody, so there is no reader to break -- and the value it held was never checked by anything,
-- which is why an automatic 'resolved' went unnoticed for as long as it did.
--
-- `produced_by` STAYS TEXT. No foreign key and no case constraint, settled with evidence in
-- D-CROSSTALK slot 3 and not reopened: 189 duplicate ids at brain HEAD means there is no unique
-- key an FK could point at, and 1,577 of 6,086 id-bearing nodes carry uppercase `BOYD-*` ids.
-- The only CHECKs here are on `resolution_status`, a closed vocabulary this repo owns, and on
-- the coherence between the two, which is a lie-detector rather than a format.
--
-- WHY LEDGER VERSION 16, AND WHY THE FILE IS NUMBERED 0010. Read from `brain.schema_migration`,
-- never from `ls` -- and tonight the ledger alone is ALSO not enough. The ledger held 1..9 at
-- 17:38Z (max 9, `0009_touch_unresolved_component`), so the naive next number is 10 and it is
-- wrong: migration files exist on disk UNAPPLIED and each already claims a version in its own
-- INSERT --
--
--     migrations/0010_subscriber_identity           claims 10
--     queue/schema/0009_null_branch_act_scan        claims 11   (file 0009, ledger 11)
--     migrations/0012_parent_cycle_guard            claims 12
--     migrations/0013_thread_budget_kind            claims 13
--     migrations/0014_work_item_brief               claims 14
--     migrations/0015_lineage_columns_by_construction claims 15
--
-- The union of the ledger and `grep -rn 'INSERT INTO brain.schema_migration' -A3
-- migrations/*.sql queue/schema/*.sql budget/schema/*.sql` is the real answer; the ledger alone
-- tells you what LANDED, not what is in flight.
--
-- THIS FILE WAS WRITTEN AS 15 AND THE GUARD BELOW CAUGHT IT. At 17:38Z the union made 15 free
-- and it was proven on scratch as 15. At 17:47Z, rebuilding scratch, both arms failed with
-- `schema version 15 is already held by 0015_lineage_columns_by_construction`: task 0137's file
-- had appeared on disk in the nine minutes between. Nothing had landed on live, so nothing
-- needed repairing -- but without the guard this file would have applied its DDL and silently
-- skipped its ledger row, because every migration here ends `ON CONFLICT DO NOTHING`. That
-- leaves the store one migration ahead of what the ledger reports, which is the exact class of
-- lie migration 15 was written about. Third occurrence tonight: 0007 was written as 0005 and
-- refused by its own guard, and T1's 0008 became 0009 during task 0115.
--
-- MIGRATION 15 AND THIS FILE ARE COMPLEMENTARY, not competing. 0137's migration 15 installs an
-- event trigger that gives every table created FROM THEN ON the full lineage record by
-- construction, and adds `brain.lineage_drift` (the columns AND the two named constraints, a
-- superset of migration 5's `brain.lineage_column_drift`). It deliberately does NOT repair
-- `brain.queue_item`: its constraint-half assertion is a WARNING rather than a RAISE precisely
-- so that dropping this table's `NOT NULL DEFAULT 'resolved'` -- a change to the queue lane's
-- write path -- stays this task's call. Its trigger cannot race the DROP/ADD cycle below either,
-- because it acts only when a COLUMN is missing and `queue_item` has all three. Once both are
-- applied, `SELECT * FROM brain.lineage_drift` is empty, which is a strictly stronger statement
-- than the one this file could make alone.
--
-- The FILE keeps this directory's local sequence (0007, 0008, 0009, 0010) rather than being
-- named 0015, and that is deliberate even though it looks untidy beside a
-- `migrations/0010_subscriber_identity.sql` that claims a different version.
-- `queue/bin/queue-scratch-db.sh` applies `queue/schema/[0-9][0-9][0-9][0-9]_*.sql` in `ls`
-- order and depends on file order BEING apply order. Numbering this 0015 would sort it correctly
-- today and silently wrong the moment the queue lane adds its next file as 0010 or 0011. A
-- confusing name costs a reader a minute; a broken apply order costs a wrong schema. 0009 in
-- this directory already claims ledger 11, so the divergence is the established convention here
-- and not something this file invents.
--
-- Task 0143, parent 0115, grandparent 0103.

\set ON_ERROR_STOP on

-- ---------------------------------------------------------------- version guard
--
-- Before BEGIN, so a refusal lands nothing partial. Every migration ends with ON CONFLICT DO
-- NOTHING, so a file claiming a taken version would otherwise apply its DDL and silently skip
-- its ledger row, leaving the store one migration ahead of what the ledger reports.

DO $$
DECLARE taken text;
BEGIN
  SELECT name INTO taken FROM brain.schema_migration WHERE version = 16;
  IF taken IS NOT NULL AND taken <> '0010_queue_item_lineage_coherent' THEN
    RAISE EXCEPTION 'schema version 16 is already held by %, not 0010_queue_item_lineage_coherent. '
                    'Pick the next version from the UNION of brain.schema_migration and the '
                    'version literals in the unapplied migration files on disk -- never by '
                    'listing a directory, and never from the ledger alone: three of this repo''s '
                    'migration files are not in migrations/, and six more were written but not '
                    'applied when this file was. This guard has already fired once for real: '
                    'this file was written as 15 and 0015_lineage_columns_by_construction took '
                    'that number nine minutes later.',
                    taken;
  END IF;
END $$;

BEGIN;

SET search_path TO brain, public;

-- ---------------------------------------------------------------- pre-flight, not an assertion
--
-- The coherence CHECK below is added VALIDATED, so it is evaluated against every existing row.
-- `brain.queue_item` was measured at 0 rows immediately before this ran, which makes that free;
-- this re-makes the check at apply time rather than trusting that finding to still hold on a
-- store several lanes are writing to at once.
--
-- It guards on the rows that would actually VIOLATE, not on the row count. That is a sharper
-- test and not a softer one: a row with 'resolved' and a real producer is coherent and this file
-- has nothing to say about it, while a row with 'resolved' and no producer is precisely the lie
-- the DEFAULT wrote, and it cannot be retro-scored from here -- there is no ref to recover and
-- no record of what was asked for. So this raises and names the count rather than guessing a
-- status for rows it did not see, and rather than letting Postgres fail with a bare constraint
-- violation that says nothing about why.
--
-- SCOPED TO THE FIRST APPLICATION. Once `queue_item_lineage_coherent` exists the question is
-- already settled and every surviving row has passed it, so on a replay the guard has nothing
-- left to protect and steps aside. Measured on 0008: a guard that fires on every re-run once the
-- table has rows contradicts the re-runnable property claimed at the top of the file.

DO $$
DECLARE n bigint;
BEGIN
  IF EXISTS (SELECT 1 FROM pg_constraint
              WHERE conrelid = 'brain.queue_item'::regclass
                AND conname  = 'queue_item_lineage_coherent') THEN
    RETURN;  -- already applied; this is a replay, not a first pass
  END IF;

  SELECT count(*) INTO n FROM brain.queue_item
   WHERE resolution_status = 'resolved' AND produced_by IS NULL;
  IF n > 0 THEN
    RAISE EXCEPTION
      '0010_queue_item_lineage_coherent: brain.queue_item holds % row(s) with '
      'resolution_status = ''resolved'' and produced_by NULL. Those rows did not exist when this '
      'file was written (the table was measured at 0 rows). Every one of them was stamped '
      '''resolved'' by the column DEFAULT rather than by a resolution that happened, so the '
      'coherence CHECK below would refuse them and it would be RIGHT to. They cannot be scored '
      'from here: there is no produced_by_ref recording what was asked for. Read them, decide '
      'each status deliberately -- NULL, meaning no resolution was ever attempted, is the likely '
      'honest answer for a row written by a caller that named no producer -- then re-run.', n;
  END IF;

  SELECT count(*) INTO n FROM brain.queue_item
   WHERE resolution_status IN ('ambiguous','unresolved') AND produced_by IS NOT NULL;
  IF n > 0 THEN
    RAISE EXCEPTION
      '0010_queue_item_lineage_coherent: brain.queue_item holds % row(s) with '
      'resolution_status in (''ambiguous'',''unresolved'') and a non-null produced_by. A '
      'resolution that found nothing cannot also name what it produced. Reconcile these by hand '
      'before this constraint can be added.', n;
  END IF;
END $$;

-- ---------------------------------------------------------------- the fourth state
--
-- Order matters: the DEFAULT goes before the enum is replaced, so no window exists in which a
-- concurrent insert can take 'resolved' under the new coherence rule without naming a producer.
-- Both statements are unconditional and both are no-ops on a replay, which is what makes this
-- file re-runnable without an IF EXISTS dance.

ALTER TABLE brain.queue_item ALTER COLUMN resolution_status DROP DEFAULT;
ALTER TABLE brain.queue_item ALTER COLUMN resolution_status DROP NOT NULL;

COMMENT ON COLUMN brain.queue_item.resolution_status IS
  'resolved | ambiguous | unresolved, or NULL for the fourth state: no resolution was ever '
  'attempted. NULL is NOT a synonym for ''unresolved''. It was unrepresentable here until '
  'migration 16 -- this column was NOT NULL DEFAULT ''resolved'', so an insert naming no '
  'producer asserted a resolution that never occurred, which is what every other table in this '
  'schema refuses. ''ambiguous'' (candidates exist, fix by disambiguating) is distinct from '
  '''unresolved'' (nothing matched, fix by creating the node or correcting the string) on '
  'purpose -- both carry produced_by IS NULL, so without this column they collapse into one and '
  'the repair is no longer readable off the row.';

COMMENT ON COLUMN brain.queue_item.produced_by_ref IS
  'The raw reference string that was resolved, carried on every status including ''resolved''. '
  'On a failure it is the whole of what is left: the row says what was ASKED FOR rather than '
  'only that the answer was nothing.';

-- ---------------------------------------------------------------- the two CHECKs
--
-- Dropped by name before being added, so this file is re-runnable. Names match migration 5's
-- convention on the other 23 tables (<table>_resolution_status_enum, <table>_lineage_coherent),
-- which is the whole point: that convention is what lets one query find the tables missing them,
-- and a system-generated name is invisible to it.
--
-- `queue_item_resolution_status_check` is 0007's inline, system-named check. Dropping it is not
-- a loosening -- the constraint replacing it is the same vocabulary, explicitly admitting NULL
-- (which the old one already admitted implicitly, since a CHECK passes when its expression is
-- NULL). IF EXISTS so a replay, or a store where 0007 was applied under a different Postgres
-- naming, does not fail here.

ALTER TABLE brain.queue_item DROP CONSTRAINT IF EXISTS queue_item_resolution_status_check;

ALTER TABLE brain.queue_item DROP CONSTRAINT IF EXISTS queue_item_resolution_status_enum;
ALTER TABLE brain.queue_item ADD  CONSTRAINT queue_item_resolution_status_enum CHECK (
  resolution_status IS NULL OR resolution_status IN ('resolved','ambiguous','unresolved')
);

-- NEVER A FABRICATED ID, and never a producer claimed for a resolution that did not produce one.
-- This refuses the superuser at a psql prompt, which application validation cannot: the same
-- posture as migration 2's grants, migration 4's enums, migration 7's two gates and migration
-- 8's pair on queue_defer.
ALTER TABLE brain.queue_item DROP CONSTRAINT IF EXISTS queue_item_lineage_coherent;
ALTER TABLE brain.queue_item ADD  CONSTRAINT queue_item_lineage_coherent CHECK (
  CASE resolution_status
    WHEN 'resolved'   THEN produced_by IS NOT NULL
    WHEN 'ambiguous'  THEN produced_by IS NULL
    WHEN 'unresolved' THEN produced_by IS NULL
    ELSE true
  END
);

-- No grants and no new columns. All three lineage columns already exist from 0007, and 0007's
-- grant on this table is TABLE-level, so nothing here needs a grant. Verified against the
-- catalog rather than the DDL text:
--
--   SELECT a.attname FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid
--     JOIN pg_namespace n ON n.oid = c.relnamespace
--    WHERE n.nspname='brain' AND c.relname='queue_item' AND a.attnum > 0
--      AND a.attacl IS NOT NULL;                             -> 0 rows: no column ACL exists.
--
-- DO NOT use information_schema.column_privileges for this. It EXPANDS a table-level grant into
-- one row per column per privilege, so it can never read 0 and would look like a wall of
-- column-level grants. `pg_attribute.attacl` is the only place a real one shows up.

INSERT INTO brain.schema_migration (version, name)
VALUES (16, '0010_queue_item_lineage_coherent') ON CONFLICT (version) DO NOTHING;

COMMIT;
