-- migration 15: the lineage pair by CONSTRUCTION, not by a sweep somebody has to re-run
--
-- Task 0137 (store lane), child of 0113, grandchild of 0103. Additive and re-runnable: one
-- function, one event trigger, one view, and a backfill of whatever the sweep has already
-- missed. It creates no table, drops no data, and edits no applied migration.
-- `0005_lineage_resolution.sql` stays exactly as it is; an applied migration is history, and
-- history is amended by a later migration (the rule migrations 12 and 13 already run on).
--
-- ================================================================= what is actually broken
--
-- Migration 5 establishes the invariant this file exists to make hold:
--
--     WHEREVER `produced_by` EXISTS, `produced_by_ref` AND `resolution_status` EXIST BESIDE IT.
--
-- and it establishes it by iterating `information_schema.columns` at apply time. That was the
-- right call for the 22 tables that existed at 2026-08-16T12:31:07Z and it is STRUCTURALLY
-- UNABLE to cover a table created after it. A catalog sweep is a snapshot. It cannot hold an
-- invariant about tables that do not exist yet, and every migration written afterwards is one
-- more chance for a lane to create `produced_by` alone without ever knowing there was a rule.
--
-- FOUR INSTANCES IN THE FIRST FIVE HOURS. Not a hypothetical: this is the measured record of
-- migration 5's first afternoon.
--
--   1. `brain.queue_defer`. Migration 7 landed 24 minutes after migration 5 and created it with
--      `produced_by` and neither companion. Point-fixed by migration 8.
--
--   2. `brain_budget_wire`'s three budget tables. `engine/tests/test-budget-wiring.sh` applies
--      `budget/schema/0003_budget.sql` AFTER `bin/scratch-db.sh create`, so migration 5 swept a
--      database where `budget_policy`, `budget_charge` and `budget_incident` did not exist yet
--      and quietly covered nothing -- while `brain.schema_migration` cheerfully reported version
--      5 as applied. THE LEDGER SAID THE MIGRATION RAN AND THE COLUMNS WERE NOT THERE. That
--      suite went 8 red the moment task 0113 taught the verbs to write the pair. Point-fixed in
--      that file, which now re-applies migration 5 after migration 3.
--
--   3. `brain.subscriber_role`. Migration 10 created it with `produced_by` and neither companion,
--      AFTER both point fixes above had landed. Measured on a freshly built scratch database at
--      2026-08-16T17:35Z, four hours after this task was posted:
--
--        $ SELECT * FROM brain.lineage_column_drift;   -- against brain_scratch
--           table_name    | has_ref | has_status
--         -----------------+---------+------------
--          subscriber_role | f       | f
--
--      That is the thesis of this file proving itself while the task sat in the queue. The two
--      point fixes held perfectly and the MECHANISM did not, because there was no mechanism:
--      there was a sweep, and the next migration after it was born without the pair again.
--
--   4. `brain.queue_item`, on the LIVE store, and this one the drift view cannot see at all.
--      Migration 7 gave it all three COLUMNS, so `lineage_column_drift` is silent about it, and
--      it carries NO `queue_item_lineage_coherent` constraint. Measured against live `brain`:
--      of the 24 tables holding `produced_by`, it is the only one without the lie-detector.
--      It also declares `resolution_status text NOT NULL DEFAULT 'resolved'`, which deletes the
--      fourth state (NULL, meaning no resolution was ever attempted) that migrations 5 and 8
--      both spend paragraphs preserving. NOT REPAIRED HERE: dropping that default changes the
--      queue lane's write path and is theirs to decide. Filed as its own task. The table holds
--      0 rows, so nothing is corrupt yet.
--
-- Instances 1, 2 and 3 were each found AFTER the fact, by a human running the view. Instance 4
-- was not found by the view at all. Three point fixes and a blind spot is what a snapshot buys.
--
-- ================================================================= the shape of the answer
--
-- A rule that has to be remembered is not a rule, it is a habit, and this repo has now measured
-- the habit failing three times in five hours across three different lanes. So:
--
--   `brain.add_lineage_columns(regclass)`   the primitive. Idempotent, adds only what is
--                                           missing, callable by hand from any later migration.
--
--   EVENT TRIGGER `brain_lineage_columns`   the construction. Fires on ddl_command_end for
--                                           CREATE TABLE / ALTER TABLE in schema `brain` and
--                                           calls the primitive for any table that has
--                                           `produced_by` without both companions.
--
-- The trigger is the load-bearing half and the function alone would not have been enough. The
-- brief for this task offered "a reusable function that any later migration calls" as one
-- option, and "any later migration calls" is exactly the remembering that already failed three
-- times. A lane writing migration 16 does not have to read this file, know this rule exists, or
-- call anything. It writes `CREATE TABLE brain.whatever (... produced_by text ...)` and the pair
-- is there when the statement returns.
--
-- `brain.lineage_column_drift` stops being the thing that finds each new gap after the fact and
-- becomes the assertion that the mechanism is working. Its meaning is UNCHANGED -- three callers
-- read it (`adapter/tools/lineage_join_proof.py`, `adapter/tools/verb_lineage_proof.py`,
-- `queue/bin/queue-scratch-db.sh`) and none of them should have to learn a new shape -- and
-- `brain.lineage_drift` is added beside it for the half instance 4 lives in.
--
-- ================================================================= WHY THE TRIGGER DOES NOT
--                                                                   BREAK MIGRATION 5's REPLAY
--
-- This is the one interaction that had to be got right, and getting it wrong would have turned
-- `test-budget-wiring.sh` red for a NEW reason while fixing the old one.
--
-- Migration 5's loop, per table, is: ADD COLUMN IF NOT EXISTS x2, then DROP CONSTRAINT IF EXISTS
-- + ADD CONSTRAINT, twice. Every one of those is an `ALTER TABLE`, so every one of them fires
-- ddl_command_end. A trigger that re-added the constraints on any ALTER would race migration 5's
-- own DROP/ADD cycle: the trigger would re-add `x_resolution_status_enum` immediately after
-- migration 5 dropped it, and migration 5's next statement would die with
-- `constraint "x_resolution_status_enum" for relation "x" already exists`.
--
-- So THE TRIGGER ACTS ONLY WHEN A COLUMN IS MISSING. If both companions are already present it
-- returns without touching constraints, which is precisely the state migration 5 is in by the
-- time it starts dropping them (it adds the columns first). Traced statement by statement for a
-- table that has neither column, which is the worst case:
--
--   ADD COLUMN produced_by_ref     -> trigger: resolution_status missing, so it completes the
--                                     table (both columns, both constraints)
--   ADD COLUMN resolution_status   -> no-op; trigger: complete, returns
--   DROP CONSTRAINT ..._enum       -> trigger: complete, returns.  <-- the one that mattered
--   ADD CONSTRAINT ..._enum        -> succeeds
--   DROP CONSTRAINT ..._coherent   -> trigger: complete, returns
--   ADD CONSTRAINT ..._coherent    -> succeeds
--
-- The same trace covers `queue/schema/0008_queue_defer_lineage.sql`, which has the same shape.
-- Measured, not reasoned: migration 5 and migration 8 are both re-applied against a database
-- carrying this trigger in `store/test_lineage_by_construction.sh`, and both are still no-ops.
--
-- RE-ENTRANCY. The trigger's own ALTERs fire ddl_command_end again. Both functions save and
-- restore `brain.in_lineage_trigger` rather than forcing it off, so a nested call cannot clear
-- the flag out from under the caller that set it. Transaction-local (`set_config(..., true)`),
-- so it cannot leak into another statement or another session.
--
-- ================================================================= NOT SECURITY DEFINER
--
-- Deliberate, and cheaper than it looks. Only two roles can create a table in schema `brain` --
-- measured off the catalog rather than the DDL text:
--
--   SELECT nspacl FROM pg_namespace WHERE nspname='brain';
--   -> {postgres=UC/postgres, brain_owner=UC/postgres, brain_producer=U/..., brain_subscriber=U/...,
--       brain_runtime=U/...}                        only postgres and brain_owner hold C.
--
-- Both of them own whatever they create, so both can ALTER it as themselves. SECURITY DEFINER
-- would buy nothing and would put a DDL-executing function behind a privilege boundary, which is
-- a surface this does not need. `search_path` is still pinned on both functions, because an
-- event trigger function runs under whatever search_path the DDL's session happened to have.
--
-- CREATE EVENT TRIGGER requires superuser. Every path that applies a migration in this repo runs
-- as `postgres` (`engine/bin/scratch-db.sh:su_psql`, `queue/bin/queue-scratch-db.sh`), so this
-- applies wherever the other fourteen do.
--
-- ================================================================= WHY 15
--
-- Read from `brain.schema_migration` and from every file's own INSERT, never from `ls
-- migrations/`, per the trap task 0101 recorded. The ledger and the tree agree on 1..14 taken:
-- 3 is `budget/schema/0003_budget.sql`, 7 and 8 are `queue/schema/`, and 11 is
-- `queue/schema/0009_null_branch_act_scan.sql` -- whose FILENAME says 9 while its INSERT says 11,
-- which is a fourth reason a directory listing is the wrong source. `migrations/0009_*` is the
-- real 9. The guard below raises rather than applying its DDL and silently skipping its ledger
-- row, which is what `ON CONFLICT (version) DO NOTHING` alone would do.

\set ON_ERROR_STOP on

-- ---------------------------------------------------------------- version guard
--
-- Before BEGIN, so a refusal lands nothing partial.

DO $$
DECLARE taken text;
BEGIN
  SELECT name INTO taken FROM brain.schema_migration WHERE version = 15;
  IF taken IS NOT NULL AND taken <> '0015_lineage_columns_by_construction' THEN
    RAISE EXCEPTION
      'schema version 15 is already held by %, not 0015_lineage_columns_by_construction. Pick '
      'the next version by reading brain.schema_migration AND every file''s own INSERT, never by '
      'listing a directory -- four of this repo''s migration files live outside migrations/ and '
      'one of them records a version its filename does not.', taken;
  END IF;
END $$;

BEGIN;

SET search_path TO brain, public;

-- ================================================================ the primitive
--
-- Everything migration 5's loop body does to one table, as a function anybody can call, and
-- IDEMPOTENT BY EXISTENCE CHECK rather than by DROP-then-ADD. That difference is not style: the
-- trigger calls this, and a DROP-then-ADD here would be visible to a concurrent reader as a
-- window in which the lie-detector does not exist, and would re-validate every row on a table
-- that already satisfied it.
--
-- Returns TRUE if it changed anything, so a migration can log rather than guess.

CREATE OR REPLACE FUNCTION brain.add_lineage_columns(tbl regclass) RETURNS boolean
  LANGUAGE plpgsql
  SET search_path = pg_catalog, pg_temp
AS $fn$
DECLARE
  nsp     text;
  rel     text;
  kind    "char";
  qual    text;
  changed boolean := false;
  guard   text;
BEGIN
  SELECT n.nspname, c.relname, c.relkind INTO nsp, rel, kind
    FROM pg_catalog.pg_class c
    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
   WHERE c.oid = tbl;

  IF nsp IS NULL THEN
    RAISE EXCEPTION 'brain.add_lineage_columns: % does not resolve to a relation.', tbl;
  END IF;

  IF kind NOT IN ('r', 'p') THEN
    RAISE EXCEPTION
      'brain.add_lineage_columns: %.% has relkind %, which is not an ordinary or partitioned '
      'table. A view or a foreign table cannot carry the pair, and adding it to the wrong object '
      'would put the invariant somewhere nothing enforces it.', nsp, rel, kind;
  END IF;

  -- `produced_by` is the LANE's decision and this function never invents it. The invariant is
  -- about what must exist BESIDE it, so a table without it is a caller error rather than a
  -- table to quietly widen.
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_attribute a
                  WHERE a.attrelid = tbl AND a.attnum > 0 AND NOT a.attisdropped
                    AND a.attname = 'produced_by') THEN
    RAISE EXCEPTION
      'brain.add_lineage_columns: %.% has no produced_by column. This function adds the two '
      'companions to a producer column that already exists; it does not decide that a table '
      'records a producer at all.', nsp, rel;
  END IF;

  -- Postgres truncates identifiers at 63 bytes SILENTLY. A relname long enough to truncate
  -- would get a constraint under a name that is not the convention, and the convention is the
  -- entire reason `brain.lineage_drift` can find these in one query. Refuse instead.
  -- 23 is octet_length('_resolution_status_enum'), the longer of the two suffixes.
  IF pg_catalog.octet_length(rel) + 23 > 63 THEN
    RAISE EXCEPTION
      'brain.add_lineage_columns: %.% is % bytes; the constraint name %_resolution_status_enum '
      'would be truncated at 63 and would no longer match the convention brain.lineage_drift '
      'searches on. Shorten the table name.', nsp, rel, pg_catalog.octet_length(rel), rel;
  END IF;

  qual := pg_catalog.format('%I.%I', nsp, rel);

  -- Suppress our own event trigger for the DDL below, saving and RESTORING rather than forcing
  -- it off: this function is called both directly and from inside the trigger, and a nested
  -- call that cleared the flag would let the outer call's remaining ALTERs re-enter.
  guard := coalesce(pg_catalog.current_setting('brain.in_lineage_trigger', true), 'off');
  PERFORM pg_catalog.set_config('brain.in_lineage_trigger', 'on', true);

  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_attribute a
                  WHERE a.attrelid = tbl AND a.attnum > 0 AND NOT a.attisdropped
                    AND a.attname = 'produced_by_ref') THEN
    EXECUTE pg_catalog.format('ALTER TABLE %s ADD COLUMN produced_by_ref text', qual);
    changed := true;
  END IF;

  -- NULLABLE, NO DEFAULT, and that is the fourth state being kept alive rather than an omission.
  -- NULL means NO RESOLUTION WAS EVER ATTEMPTED and is not a synonym for 'unresolved'. A
  -- `NOT NULL DEFAULT 'resolved'` here would delete that distinction and would additionally make
  -- the coherence CHECK below refuse every insert that omits a producer -- which is exactly the
  -- shape `brain.queue_item` is in today.
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_attribute a
                  WHERE a.attrelid = tbl AND a.attnum > 0 AND NOT a.attisdropped
                    AND a.attname = 'resolution_status') THEN
    EXECUTE pg_catalog.format('ALTER TABLE %s ADD COLUMN resolution_status text', qual);
    changed := true;
  END IF;

  -- The closed vocabulary. Names match migration 5's convention on the other 24 tables.
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_constraint k
                  WHERE k.conrelid = tbl AND k.contype = 'c'
                    AND k.conname = rel || '_resolution_status_enum') THEN
    EXECUTE pg_catalog.format(
      'ALTER TABLE %s ADD CONSTRAINT %I CHECK ('
      '  resolution_status IS NULL'
      '  OR resolution_status IN (''resolved'', ''ambiguous'', ''unresolved''))',
      qual, rel || '_resolution_status_enum');
    changed := true;
  END IF;

  -- The lie-detector, verbatim from migration 5. Only two combinations are refused and each of
  -- them is a row that asserts something untrue about the brain:
  --
  --   resolved   + produced_by NULL      claims a node was found and names none
  --   ambiguous  + produced_by NOT NULL  had several candidates and picked one anyway
  --   unresolved + produced_by NOT NULL  found nothing and wrote an id: a FABRICATED id
  --
  -- `resolution_status IS NULL` with any produced_by passes, and that tolerance is load-bearing:
  -- it is what keeps this additive for the lanes that stamp a producer NAME without ever
  -- consulting the brain (ingest's 1,131 'd3-ingest' rows) instead of breaking their inserts.
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_constraint k
                  WHERE k.conrelid = tbl AND k.contype = 'c'
                    AND k.conname = rel || '_lineage_coherent') THEN
    EXECUTE pg_catalog.format(
      'ALTER TABLE %s ADD CONSTRAINT %I CHECK ('
      '  CASE resolution_status'
      '    WHEN ''resolved''   THEN produced_by IS NOT NULL'
      '    WHEN ''ambiguous''  THEN produced_by IS NULL'
      '    WHEN ''unresolved'' THEN produced_by IS NULL'
      '    ELSE true'
      '  END)',
      qual, rel || '_lineage_coherent');
    changed := true;
  END IF;

  EXECUTE pg_catalog.format('COMMENT ON COLUMN %s.produced_by_ref IS %L', qual,
    'The raw reference that was resolved, kept on every status. On a failure this is all that is '
    'left of the claim: the row says what was asked for, not merely that the answer was nothing. '
    'Written by adapter/brain_adapter/store_join.py from Resolution.produced_by(). Created by '
    'brain.add_lineage_columns(), which the brain_lineage_columns event trigger calls for you.');
  EXECUTE pg_catalog.format('COMMENT ON COLUMN %s.resolution_status IS %L', qual,
    'resolved | ambiguous | unresolved | NULL. NULL means NO ATTEMPT WAS MADE and is NOT a '
    'synonym for unresolved -- a produced_by stamped as a producer name (ingest''s ''d3-ingest'') '
    'never made a claim about the brain. ambiguous is distinct from unresolved on purpose: '
    'ambiguous has candidates and is fixed by disambiguating, unresolved has none.');

  PERFORM pg_catalog.set_config('brain.in_lineage_trigger', guard, true);
  RETURN changed;
END
$fn$;

COMMENT ON FUNCTION brain.add_lineage_columns(regclass) IS
  'Adds produced_by_ref, resolution_status and migration 5''s two CHECKs beside an existing '
  'produced_by column, adding only what is missing. Raises if the table has no produced_by: this '
  'completes a lineage record, it does not decide that a table keeps one. You do not normally '
  'call this -- the brain_lineage_columns event trigger does -- but a migration that wants the '
  'pair on a table it did not just create can call it directly.';

-- brain_owner can create tables in schema brain, so it can land in this function through the
-- trigger. Event triggers do not check EXECUTE, but a direct call from an owner-run migration
-- would, and failing there would be a confusing way to learn about a grant.
GRANT EXECUTE ON FUNCTION brain.add_lineage_columns(regclass) TO brain_owner;

-- ================================================================ the construction
--
-- ddl_command_end rather than ddl_command_start: at start the table does not exist yet and its
-- columns cannot be read. At end the DDL is complete and visible to the catalog, and we are
-- still inside the same transaction -- so a table and its lineage pair are created together or
-- neither is. There is no window in which a `produced_by` exists alone, not even a short one.

CREATE OR REPLACE FUNCTION brain.lineage_columns_event_trigger() RETURNS event_trigger
  LANGUAGE plpgsql
  SET search_path = pg_catalog, pg_temp
AS $fn$
DECLARE
  cmd   record;
  guard text;
BEGIN
  -- Our own ALTERs fire this again. Save and restore; see the header.
  IF coalesce(pg_catalog.current_setting('brain.in_lineage_trigger', true), 'off') = 'on' THEN
    RETURN;
  END IF;
  guard := coalesce(pg_catalog.current_setting('brain.in_lineage_trigger', true), 'off');
  PERFORM pg_catalog.set_config('brain.in_lineage_trigger', 'on', true);

  -- Joined through pg_class rather than filtered on d.object_type, because an ALTER TABLE can
  -- report itself as 'table' or as 'table column' depending on the subcommand and both carry the
  -- table's oid in objid. The join drops every non-relation object for free.
  FOR cmd IN
    SELECT DISTINCT d.objid
      FROM pg_event_trigger_ddl_commands() d
      JOIN pg_catalog.pg_class c     ON c.oid = d.objid
      JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
     WHERE n.nspname = 'brain'
       AND c.relkind IN ('r', 'p')
  LOOP
    -- ONLY WHEN A COLUMN IS MISSING. This condition is what keeps migration 5 and migration 8
    -- re-runnable against a database carrying this trigger: by the time either of them starts
    -- dropping and re-adding constraints, both columns exist, so this returns without racing
    -- them. See the header for the statement-by-statement trace.
    IF EXISTS (SELECT 1 FROM pg_catalog.pg_attribute a
                WHERE a.attrelid = cmd.objid AND a.attnum > 0 AND NOT a.attisdropped
                  AND a.attname = 'produced_by')
       AND NOT (EXISTS (SELECT 1 FROM pg_catalog.pg_attribute a
                         WHERE a.attrelid = cmd.objid AND a.attnum > 0 AND NOT a.attisdropped
                           AND a.attname = 'produced_by_ref')
            AND EXISTS (SELECT 1 FROM pg_catalog.pg_attribute a
                         WHERE a.attrelid = cmd.objid AND a.attnum > 0 AND NOT a.attisdropped
                           AND a.attname = 'resolution_status')) THEN
      PERFORM brain.add_lineage_columns(cmd.objid::regclass);
      RAISE NOTICE
        'brain_lineage_columns: added produced_by_ref and resolution_status to %', cmd.objid::regclass;
    END IF;
  END LOOP;

  PERFORM pg_catalog.set_config('brain.in_lineage_trigger', guard, true);
END
$fn$;

COMMENT ON FUNCTION brain.lineage_columns_event_trigger() IS
  'Fired by the brain_lineage_columns event trigger. Completes the lineage record of any table '
  'in schema brain that gained a produced_by without both companions, in the same transaction as '
  'the DDL that created it.';

-- No IF NOT EXISTS on CREATE EVENT TRIGGER, so drop by name first to keep this file re-runnable.
-- Event trigger names are database-wide, not schema-qualified; the `brain_` prefix is the only
-- namespacing available.
DROP EVENT TRIGGER IF EXISTS brain_lineage_columns;
CREATE EVENT TRIGGER brain_lineage_columns
  ON ddl_command_end
  WHEN TAG IN ('CREATE TABLE', 'CREATE TABLE AS', 'SELECT INTO', 'ALTER TABLE')
  EXECUTE FUNCTION brain.lineage_columns_event_trigger();

COMMENT ON EVENT TRIGGER brain_lineage_columns IS
  'Migration 5''s invariant, held by construction instead of by a sweep. A table in schema brain '
  'that gains produced_by gets produced_by_ref, resolution_status and both CHECKs in the same '
  'transaction, whether or not the lane that wrote the migration has ever heard of this rule. '
  'brain.lineage_column_drift is now the assertion that this is working rather than the thing '
  'that finds each new gap after the fact.';

-- ================================================================ the backfill
--
-- Everything the sweep has already missed. Driven off migration 5's own view, so this file has
-- no hardcoded list to go stale, and it is a no-op on a database that is already clean.

DO $$
DECLARE t record; n int := 0;
BEGIN
  FOR t IN SELECT table_name FROM brain.lineage_column_drift ORDER BY table_name LOOP
    PERFORM brain.add_lineage_columns(format('brain.%I', t.table_name)::regclass);
    RAISE NOTICE '0015: completed the lineage record of brain.%', t.table_name;
    n := n + 1;
  END LOOP;
  IF n = 0 THEN
    RAISE NOTICE '0015: no column drift to backfill.';
  END IF;
END $$;

-- ================================================================ the fuller assertion
--
-- `brain.lineage_column_drift` is left EXACTLY as migration 5 defined it. Three callers read it
-- and widening what it returns would silently change what they assert.
--
-- This is the same question asked of the whole lineage record rather than only its columns,
-- because instance 4 above lives in the gap between the two: `brain.queue_item` carries all
-- three columns and no `queue_item_lineage_coherent`, so the column view is silent while the
-- lie-detector -- the constraint that refuses a FABRICATED id from a superuser at a psql prompt,
-- which application validation cannot -- is simply absent on that table.
--
-- NAME-MATCHED ON PURPOSE, and it will flag a table that carries an equivalent constraint under
-- a different name. `queue_item` does exactly that for the vocabulary CHECK: migration 7 wrote
-- it inline, so Postgres named it `queue_item_resolution_status_check` and it does the same job.
-- Flagging it is still right. The convention IS the invariant here -- it is what lets one query
-- answer the question for the whole schema, which is the property migration 8's comment leans on
-- and the property a sweep-and-hope has already lost three times.

CREATE OR REPLACE VIEW brain.lineage_drift AS
  SELECT * FROM (
    SELECT c.relname::text AS table_name,
           EXISTS (SELECT 1 FROM pg_attribute a
                    WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
                      AND a.attname = 'produced_by_ref')          AS has_ref,
           EXISTS (SELECT 1 FROM pg_attribute a
                    WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
                      AND a.attname = 'resolution_status')        AS has_status,
           EXISTS (SELECT 1 FROM pg_constraint k
                    WHERE k.conrelid = c.oid AND k.contype = 'c'
                      AND k.conname = c.relname || '_resolution_status_enum') AS has_enum_check,
           EXISTS (SELECT 1 FROM pg_constraint k
                    WHERE k.conrelid = c.oid AND k.contype = 'c'
                      AND k.conname = c.relname || '_lineage_coherent')       AS has_coherent_check
      FROM pg_class c
      JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE n.nspname = 'brain'
       AND c.relkind IN ('r', 'p')
       AND EXISTS (SELECT 1 FROM pg_attribute a
                    WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
                      AND a.attname = 'produced_by')
  ) d
   WHERE NOT (has_ref AND has_status AND has_enum_check AND has_coherent_check);

COMMENT ON VIEW brain.lineage_drift IS
  'The whole lineage record, not only its columns: every table in schema brain carrying '
  'produced_by without produced_by_ref, resolution_status, <table>_resolution_status_enum and '
  '<table>_lineage_coherent. A superset of brain.lineage_column_drift, which stays as migration 5 '
  'defined it because three tools assert on its exact shape. Empty is the invariant the '
  'brain_lineage_columns event trigger holds. A row means either a table that predates the '
  'trigger or a constraint someone dropped by hand.';

GRANT SELECT ON brain.lineage_drift TO brain_runtime;

-- ---------------------------------------------------------------- assert, do not hope
--
-- The column invariant is asserted HARD: this migration exists to make it hold, and a version 15
-- row in the ledger over a database where it does not hold would be exactly the lie instance 2
-- is about ("the ledger says the migration ran; the columns are not there").

DO $$
DECLARE drifted text;
BEGIN
  SELECT string_agg(table_name, ', ' ORDER BY table_name) INTO drifted
    FROM brain.lineage_column_drift;
  IF drifted IS NOT NULL THEN
    RAISE EXCEPTION
      '0015_lineage_columns_by_construction: brain.lineage_column_drift is STILL not empty after '
      'the backfill (%). The backfill is driven off that same view, so this means '
      'brain.add_lineage_columns refused a table rather than that one was missed -- read the '
      'error it raised. Refusing to record version 15 over a store where the invariant does not '
      'hold.', drifted;
  END IF;
END $$;

-- The constraint half is a WARNING and not a raise, and the asymmetry is deliberate. The only
-- table it currently names is `brain.queue_item`, whose repair means dropping
-- `resolution_status NOT NULL DEFAULT 'resolved'` -- a change to the queue lane's write path,
-- filed as its own task under 0137. Raising here would make this migration unappliable to the
-- live store until another lane's decision lands, which would leave the trigger uninstalled and
-- the recurrence unfixed for the sake of a table holding 0 rows.

DO $$
DECLARE drifted text;
BEGIN
  SELECT string_agg(table_name, ', ' ORDER BY table_name) INTO drifted FROM brain.lineage_drift;
  IF drifted IS NOT NULL THEN
    RAISE WARNING
      '0015: brain.lineage_drift is not empty: %. These tables carry produced_by without the '
      'full record (both columns AND <table>_resolution_status_enum AND <table>_lineage_coherent). '
      'Every table created from here on gets all four by construction; these predate the trigger.',
      drifted;
  ELSE
    RAISE NOTICE '0015: brain.lineage_drift is empty; the full lineage record holds schema-wide.';
  END IF;
END $$;

-- No other grants. 0002_roles grants are TABLE-level, so columns this adds inherit, and the two
-- views are granted above. Verified against pg_attribute.attacl rather than
-- information_schema.column_privileges, which EXPANDS a table-level grant into one row per
-- column per privilege and so can never be 0 (the trap migration 8 records).

INSERT INTO brain.schema_migration (version, name)
VALUES (15, '0015_lineage_columns_by_construction') ON CONFLICT (version) DO NOTHING;

COMMIT;
