-- migration 5: `produced_by_ref` and `resolution_status` beside every `produced_by`,
--              and `git_ref` NOT NULL on `brain.touch`
--
-- This is the store half of the adapter/store seam (task 0103, parent 0093). D2 closed its
-- report with "the adapter is not yet wired to the store -- it emits the produced_by dict and
-- the touch rows, and whoever connects them owns that join." The join was not a wiring bug.
-- Two of the three columns D2 emits did not exist.
--
-- MEASURED BEFORE WRITING THIS, against the live store at schema version 4:
--
--   22 tables in schema brain carry `produced_by`  (the 19 from migration 1 plus
--   budget_policy / budget_charge / budget_incident from migration 3)
--    0 tables carry `produced_by_ref`
--    0 tables carry `resolution_status`
--
-- So `Resolution.produced_by()` in adapter/brain_adapter/index.py returns a three-key dict
-- whose second and third keys had nowhere to land, and any caller wiring it up would have had
-- to drop them. Dropping them collapses `ambiguous` and `unresolved` into "produced_by is
-- NULL", which is exactly the distinction D2 built on purpose.
--
-- WHY 5. Read from `brain.schema_migration`, never from `ls migrations/`, per the trap task
-- 0101 recorded: the ledger held 1 0001_initial, 2 0002_roles, 3 0003_budget (whose file lives
-- in budget/schema/), 4 0004_touch_wager_2c. Version 5 was free. The guard below turns a
-- collision into a raise instead of the silent skip that trap describes.
--
-- ===================================================================================
-- THE FOUR STATES. THREE ARE THE ADAPTER'S; THE FOURTH IS THE ABSENCE OF AN ATTEMPT.
-- ===================================================================================
--
--   'resolved'    the reference landed on exactly one brain node. `produced_by` is that
--                 node's stable id and has been verified to exist.
--   'ambiguous'   the reference landed on several nodes with DIFFERENT ids. `produced_by`
--                 is NULL. Distinct from 'unresolved' on purpose: an ambiguous reference has
--                 candidates and is fixable by disambiguating; an unresolved one has none.
--   'unresolved'  the reference landed on nothing. `produced_by` is NULL.
--   NULL          no resolution was ever attempted. This is the fourth state and it is not a
--                 synonym for 'unresolved'. `brain.session` already holds 1,131 rows with
--                 produced_by = 'd3-ingest', a producer NAME rather than a brain entity id.
--                 Those rows never made a claim about the brain, and scoring them as dangling
--                 lineage would be a measurement error, not a finding.
--
-- `produced_by_ref` carries the raw string that was resolved, on every status including
-- 'resolved'. On a failure it is the whole of what is left: the row says what was asked for
-- rather than only that the answer was nothing. A NULL with no ref is a dead end for whoever
-- has to reconcile it later.
--
-- NEVER A FABRICATED ID. That rule lives in three places now and they agree by construction:
-- Resolution.produced_by() returns None on failure, promote() refuses LINEAGE_INCOHERENT, and
-- the CHECK below refuses the same row for a superuser, which application validation cannot.
--
-- ===================================================================================
-- `produced_by` STAYS TEXT. NO FOREIGN KEY, NO CASE CONSTRAINT.
-- ===================================================================================
--
-- Both were settled with evidence in D-CROSSTALK slot 3 and neither is reopened here:
-- 189 duplicate ids at brain HEAD c7919b93 means there is no unique key an FK could point at,
-- and 1,577 of 6,086 id-bearing nodes carry uppercase `BOYD-*` ids, so a lowercase CHECK would
-- reject a quarter of the corpus. This migration adds no constraint on `produced_by` itself.
-- The only CHECKs added are on `resolution_status`, which is a closed vocabulary this repo
-- owns, and on the coherence between the two, which is a lie-detector rather than a format.
--
-- ===================================================================================
-- `brain.touch.git_ref`: THE PROJECTION MADE STRUCTURAL RATHER THAN DOCUMENTED
-- ===================================================================================
--
-- Migration 4 states in three places that git is the source of touch-edges (WAGER-7) and that
-- this table is a projection. It states it and nothing enforces it: as applied, a row could be
-- inserted into brain.touch that exists nowhere in git, and the table would silently become
-- authoritative for a lineage edge. That is the exact shape of the failure the invariant
-- "losing the whole store costs queue position and history and ZERO KNOWLEDGE" is about.
--
-- So: `git_ref` is NOT NULL with no default. A touch row cannot exist without naming the
-- commit it was projected from. Rebuild the table from those commits and you get it back;
-- drop the table and you lose nothing but the copy. The direction of truth is now a column
-- constraint, not a comment, and a lane that tries to invent a touch-edge in the store is
-- refused by Postgres with a message that names the rule.
--
-- Safe to apply as NOT NULL: brain.touch was verified EMPTY (0 rows) immediately before this
-- ran, the same check migration 4 made, and the guard below re-makes it rather than trusting
-- that finding to still hold.
--
-- ===================================================================================
--
-- Style follows migrations 1 and 4: strict on write, tolerant on read, CHECK constraints
-- rather than application validation, unset means the conservative end and never the zero.
-- Additive and re-runnable: every ADD COLUMN is IF NOT EXISTS and every constraint is dropped
-- by name before it is added.

\set ON_ERROR_STOP on

BEGIN;

SET search_path TO brain, public;

-- ---------------------------------------------------------------- version guard
--
-- 0101's trap, closed. `INSERT ... ON CONFLICT (version) DO NOTHING` at the foot of every
-- migration means a file claiming a taken version applies its DDL and then silently skips its
-- ledger row, leaving the store one migration ahead of what it reports. This raises instead.
-- The ON CONFLICT at the foot is kept so that re-running THIS file is still a no-op.

DO $$
DECLARE taken text;
BEGIN
  SELECT name INTO taken FROM brain.schema_migration WHERE version = 5;
  IF taken IS NOT NULL AND taken <> '0005_lineage_resolution' THEN
    RAISE EXCEPTION
      '0005_lineage_resolution: version 5 is already held by %. Another lane took this number '
      'while this file was being written. Renumber this migration from brain.schema_migration '
      '(never from ls migrations/) and re-run.', taken;
  END IF;
END $$;

-- ---------------------------------------------------------------- the two lineage columns
--
-- Driven off information_schema rather than a hand-written table list, and that is the point
-- rather than a shortcut. The invariant this migration establishes is:
--
--     WHEREVER `produced_by` EXISTS, `produced_by_ref` AND `resolution_status` EXIST BESIDE IT.
--
-- A hand-written list of 22 tables is a list that goes stale the first time a lane adds a
-- twenty-third. Stated as a loop over the live catalog, the invariant is checkable in one
-- query (see brain.lineage_column_drift below) and a new table either satisfies it or shows up
-- in that view. It also means this file covers D6a's three budget tables without reaching into
-- another lane's migration to edit it.

DO $$
DECLARE t record;
BEGIN
  FOR t IN
    SELECT c.table_name
      FROM information_schema.columns c
      JOIN information_schema.tables tb
        ON tb.table_schema = c.table_schema AND tb.table_name = c.table_name
     WHERE c.table_schema = 'brain'
       AND c.column_name  = 'produced_by'
       AND tb.table_type  = 'BASE TABLE'
     ORDER BY c.table_name
  LOOP
    EXECUTE format(
      'ALTER TABLE brain.%I ADD COLUMN IF NOT EXISTS produced_by_ref text', t.table_name);
    EXECUTE format(
      'ALTER TABLE brain.%I ADD COLUMN IF NOT EXISTS resolution_status text', t.table_name);

    -- The closed vocabulary. NULL is permitted and means "no attempt was made", the same
    -- latitude migration 4 gives orient_role and migration 1 gives actor_type.
    EXECUTE format(
      'ALTER TABLE brain.%I DROP CONSTRAINT IF EXISTS %I', t.table_name,
      t.table_name || '_resolution_status_enum');
    EXECUTE format(
      'ALTER TABLE brain.%I ADD CONSTRAINT %I CHECK ('
      '  resolution_status IS NULL'
      '  OR resolution_status IN (''resolved'', ''ambiguous'', ''unresolved''))',
      t.table_name, t.table_name || '_resolution_status_enum');

    -- The lie-detector. Only two combinations are refused, and each of them is a row that
    -- asserts something untrue about the brain:
    --
    --   resolved   + produced_by NULL      claims a node was found and names none
    --   ambiguous  + produced_by NOT NULL  had several candidates and picked one anyway
    --   unresolved + produced_by NOT NULL  found nothing and wrote an id: a FABRICATED id,
    --                                      the failure D2's whole unresolvable path exists
    --                                      to prevent, and the one that is believed later
    --
    -- Everything else passes, including `resolution_status IS NULL` with any produced_by.
    -- That tolerance is deliberate and load-bearing: it is what keeps this migration additive
    -- for the lanes that stamp a producer name without ever consulting the brain (ingest's
    -- 1,131 'd3-ingest' rows), instead of breaking their inserts the moment it applies.
    EXECUTE format(
      'ALTER TABLE brain.%I DROP CONSTRAINT IF EXISTS %I', t.table_name,
      t.table_name || '_lineage_coherent');
    EXECUTE format(
      'ALTER TABLE brain.%I ADD CONSTRAINT %I CHECK ('
      '  CASE resolution_status'
      '    WHEN ''resolved''   THEN produced_by IS NOT NULL'
      '    WHEN ''ambiguous''  THEN produced_by IS NULL'
      '    WHEN ''unresolved'' THEN produced_by IS NULL'
      '    ELSE true'
      '  END)',
      t.table_name, t.table_name || '_lineage_coherent');

    EXECUTE format(
      'COMMENT ON COLUMN brain.%I.produced_by_ref IS %L', t.table_name,
      'The raw reference that was resolved, kept on every status. On a failure this is all '
      'that is left of the claim: the row says what was asked for, not merely that the answer '
      'was nothing. Written by adapter/brain_adapter/store_join.py from Resolution.produced_by().');
    EXECUTE format(
      'COMMENT ON COLUMN brain.%I.resolution_status IS %L', t.table_name,
      'resolved | ambiguous | unresolved | NULL. NULL means NO ATTEMPT WAS MADE and is NOT a '
      'synonym for unresolved -- a produced_by stamped as a producer name (ingest''s '
      '''d3-ingest'') never made a claim about the brain. ambiguous is distinct from '
      'unresolved on purpose: ambiguous has candidates, unresolved has none.');
  END LOOP;
END $$;

-- ---------------------------------------------------------------- touch: git_ref, NOT NULL

DO $$
DECLARE n bigint;
BEGIN
  SELECT count(*) INTO n FROM brain.touch;
  IF n > 0 THEN
    RAISE EXCEPTION
      '0005_lineage_resolution: brain.touch holds % row(s) and git_ref is being added NOT NULL. '
      'Those rows predate the projection loader and cannot name the commit they came from, '
      'which means nothing can tell whether they exist in git at all. Reconcile them against '
      'git first (git holds the touch-edges, WAGER-7), then re-run.', n;
  END IF;
END $$;

ALTER TABLE brain.touch ADD COLUMN IF NOT EXISTS git_ref text;
ALTER TABLE brain.touch ALTER COLUMN git_ref SET NOT NULL;

-- A sha, not a branch name and not a tag. A branch moves, and a projection whose source can
-- move is not reconcilable. 40 hex for sha1, 64 for sha256 so this does not have to be
-- migrated again when git's default changes.
ALTER TABLE brain.touch DROP CONSTRAINT IF EXISTS touch_git_ref_is_a_sha;
ALTER TABLE brain.touch ADD CONSTRAINT touch_git_ref_is_a_sha CHECK (
  git_ref ~ '^[0-9a-f]{40}$' OR git_ref ~ '^[0-9a-f]{64}$'
);

COMMENT ON COLUMN brain.touch.git_ref IS
  'The commit this edge was PROJECTED FROM. NOT NULL with no default, which is what turns '
  'migration 4''s comment into a rule: a touch-edge cannot exist in this table without '
  'existing in git first (WAGER-7). Drop this table and rebuild it from these commits and '
  'nothing is lost, which is the "losing the store costs zero knowledge" invariant holding '
  'rather than being asserted. A sha and never a branch name: a branch moves and a projection '
  'whose source can move is not reconcilable.';

-- ---------------------------------------------------------------- the drift check
--
-- The invariant above, as a query anyone can run. Empty means every produced_by in the schema
-- has its ref and its status beside it. A row here is a table a later migration added without
-- the pair, which is the drift this file exists to prevent recurring.

CREATE OR REPLACE VIEW brain.lineage_column_drift AS
  SELECT c.table_name,
         bool_or(c.column_name = 'produced_by_ref')   AS has_ref,
         bool_or(c.column_name = 'resolution_status') AS has_status
    FROM information_schema.columns c
    JOIN information_schema.tables tb
      ON tb.table_schema = c.table_schema AND tb.table_name = c.table_name
   WHERE c.table_schema = 'brain'
     AND tb.table_type  = 'BASE TABLE'
     AND c.column_name IN ('produced_by', 'produced_by_ref', 'resolution_status')
   GROUP BY c.table_name
  HAVING bool_or(c.column_name = 'produced_by')
     AND NOT (bool_or(c.column_name = 'produced_by_ref')
              AND bool_or(c.column_name = 'resolution_status'));

COMMENT ON VIEW brain.lineage_column_drift IS
  'Tables carrying produced_by without produced_by_ref and resolution_status beside it. Empty '
  'is the invariant migration 5 establishes. A row means a later migration added a table with '
  'half a lineage record, which collapses ambiguous into unresolved for that table.';

GRANT SELECT ON brain.lineage_column_drift TO brain_runtime;

-- No new grants otherwise. 0002_roles grants are TABLE-level
-- (GRANT SELECT, INSERT, UPDATE ON <table list> TO brain_runtime), so new columns inherit.
-- Verified rather than assumed: grep GRANT migrations/0002_roles.sql shows no column lists.

INSERT INTO brain.schema_migration (version, name) VALUES (5, '0005_lineage_resolution')
  ON CONFLICT (version) DO NOTHING;

COMMIT;
