-- migration 17: a producer NAME gets a column of its own, so `produced_by` means an entity id
--               and nothing else
--
-- Task 0142 (ingest lane, parent 0118), executing the operator's answer to q0171. That answer
-- overturns, deliberately and with reasons, a shape three files state on purpose. This header
-- records what was overturned, by whom, and why, because the next reader will otherwise find
-- migration 5 and `store_join.producer_stamp()` arguing the opposite and have no way to tell
-- which of us is the mistake.
--
-- ===================================================================================
-- WHAT 0142 CLAIMED, WHAT WAS ACTUALLY THERE, AND WHAT IS BEING FIXED INSTEAD
-- ===================================================================================
--
-- 0142 was posted as "produced_by = 'd3-ingest' on 2,269 rows is a producer name in an
-- entity-id column", and it argued the rows were a pre-migration-5 backfill artifact that the
-- adapter's own resolver refuses to write. T5 measured that premise false and refused to
-- execute the task, which was correct: `adapter/brain_adapter/store_join.py:101` is a function
-- `producer_stamp()` whose entire job is to emit exactly that shape, migration 5 names it as
-- the fourth state (`0005_lineage_resolution.sql:34-40`) and calls the tolerance for it
-- "deliberate and load-bearing" (`:166-168`), and migration 9 cites `d3-ingest` by name as the
-- reason 5 tolerates on `produced_by` what 9 refuses on `entity_id` (`:219-222`). Both fixes
-- 0142 offered would have written a falsehood: `resolution_status = 'unresolved'` asserts an
-- attempt that never happened, and minting a brain entity for `d3-ingest` puts a runtime label
-- into the knowledge corpus.
--
-- THE FOURTH STATE IS NOT BEING DELETED BY THIS FILE. `resolution_status IS NULL` still means
-- "no resolution was ever attempted" and still is not a synonym for 'unresolved'. Every row
-- this migration moves keeps `resolution_status` NULL and `produced_by_ref` NULL, because both
-- are still true afterwards: nobody asked, and there was no reference to preserve. Migration 5
-- is not amended, contradicted, or re-run.
--
-- What the operator bought instead is a KIND discriminator, which the store did not have. T5's
-- sweep of all 24 base tables carrying `produced_by` found the fourth-state shape
-- (`produced_by` NOT NULL, `produced_by_ref` NULL, `resolution_status` NULL) worn by two
-- mutually exclusive kinds of value with nothing in the columns separating them. Re-measured
-- against live `brain` at 2026-08-16T18:2xZ before this file was written:
--
--     produced_by                                  rows  tables  in
--     d3-ingest                                    2269       2  session, transcript
--     knowledge-ai-architecture-surface-boundary      9       4  budget_incident, event,
--                                                                session, work_item
--
-- The first resolves to nothing (`brain-adapter entity resolve d3-ingest` exits 3; the string
-- appears in zero files in the brain, not even as prose). The second resolves to
-- `knowledge/ai-architecture/concepts/surface-boundary.md`, exit 0. So `resolution_status`
-- cannot tell a lineage walker whether to walk: nine rows wearing NULL status ARE walkable and
-- 2,269 are not, and there is NO rule a walker could follow that would be right on both. That
-- is why D9's acceptance walk scored two rows FAIL-WRONG, and it is why "leave it, the NULL
-- status announces itself" was not taken.
--
-- The operator's reasoning for overturning, from the q0171 answer, kept here verbatim in
-- substance: walking lineage with no human reading prose at any hop is one of the five success
-- criteria this program is judged on. Under "change nothing", every status-NULL row is
-- permanently un-walkable BY RULE, which does not leave that criterion unmet, it makes it
-- unmeasurable, and it throws the nine real entities out with the 2,269. After this migration
-- `produced_by IS NOT NULL` means "an entity id" unconditionally, which is the property the
-- walk needs, and the residual ambiguity shrinks to nine rows that carry a real id nobody
-- verified: smaller, and honest.
--
-- ===================================================================================
-- WHY THE COLUMN IS NAMED `produced_by_producer` AND NOT `producer`
-- ===================================================================================
--
-- `producer` was the obvious name and it is a trap that was walked into and backed out of.
-- `brain.event_rollup` ALREADY has a column called `producer` (`0001_initial.sql:453`,
-- `producer text NOT NULL DEFAULT ''`), and it is part of that table's PRIMARY KEY
-- (day, type, department, producer). It is a GROUPING KEY -- whose events this day-row rolls
-- up -- not a statement about who wrote the row. `event_rollup` is one of the 24 tables
-- carrying `produced_by`, so the loop below, written with `ADD COLUMN IF NOT EXISTS producer`,
-- would have silently no-opped there and quietly given a primary-key column a second meaning:
-- the exact "one column, two kinds of value" defect this migration exists to remove, recreated
-- inside the fix. Measured: of the 24, `event_rollup` is the only one with a `producer`
-- column, and ZERO tables in schema `brain` carry `produced_by_producer`.
--
-- The name also keeps the `produced_by_*` prefix family, so it sorts beside `produced_by_ref`
-- in `\d` and reads as what it is: the `produced_by` slot, holding a producer name.
--
-- ===================================================================================
-- WHY 17
-- ===================================================================================
--
-- Read from `brain.schema_migration` AND from every file's own INSERT, never from
-- `ls migrations/`, per the trap task 0101 recorded and migration 15 restates. The operator's
-- answer says "migration 10"; 10 is TAKEN and so are 11 through 16. Measured 2026-08-16T18:2xZ:
--
--   ledger (applied):  1..9, then 16 = `queue/schema/0010_queue_item_lineage_coherent.sql`,
--                      applied 17:49:05Z -- AFTER T5 blocked this task at 17:09, so T5's own
--                      trap note ("the ledger is at version 9") was already stale when written.
--   claimed in files but unapplied:  10 `migrations/0010_subscriber_identity`,
--                      11 `queue/schema/0009_null_branch_act_scan` (FILENAME 0009, ledger row
--                      ELEVEN -- a fourth reason a directory listing is the wrong source),
--                      12, 13, 14, 15.
--
-- Union of ledger and file claims is 1..16 with no holes. 17 is the first free number. The
-- guard below RAISES rather than applying its DDL and silently skipping its ledger row, which
-- is what `ON CONFLICT (version) DO NOTHING` alone does and is how migration 7 took a number
-- out from under another lane earlier today.
--
-- ===================================================================================
-- WHAT ELSE THIS TOUCHES, STATED RATHER THAN LEFT TO BE DISCOVERED
-- ===================================================================================
--
-- 1. `ingest/ingest/events.py:33` sets `PRODUCER = 'd3-ingest'` and `build()` puts it in the
--    event envelope's `produced_by`. That envelope lands in `ingest.event_outbox`, schema
--    `ingest`, which this migration does not govern and does not touch (measured: 0 rows, and
--    `BRAIN_EVENT_EMIT` is unset, so nothing drains it). If D5's `event emit` verb is ever
--    wired to pass that envelope field through to `fabric/emit.py`'s `produced_by` argument,
--    the CHECK below will REFUSE the insert. That refusal is correct and is the point: the
--    envelope's producer name belongs in `produced_by_producer`, and a loud failure at that
--    seam is better than the silent re-creation of this defect. Posted as its own task.
--
-- 2. `migrations/0015_lineage_columns_by_construction.sql` (version 15, ON DISK AND UNAPPLIED)
--    installs an event trigger that adds `produced_by_ref` and `resolution_status` to any new
--    table carrying `produced_by`. It knows nothing about `produced_by_producer`, so a table
--    created after 15 applies will get the pair and not this column. `brain.producer_column_
--    drift` below is what makes that visible; teaching `brain.add_lineage_columns()` about the
--    new column is D1's file to edit, not this lane's, and is posted as its own task.
--
-- Style follows migrations 1, 4, 5 and 15: strict on write, tolerant on read, CHECK constraints
-- rather than application validation, additive and re-runnable, and every claim verified inside
-- the transaction before COMMIT rather than asserted in a comment.

\set ON_ERROR_STOP on

-- ---------------------------------------------------------------- version guard
--
-- Before BEGIN, so a refusal lands nothing partial. RAISES on collision; it does not skip.

DO $$
DECLARE taken text;
BEGIN
  SELECT name INTO taken FROM brain.schema_migration WHERE version = 17;
  IF taken IS NOT NULL AND taken <> '0017_producer_column' THEN
    RAISE EXCEPTION
      'schema version 17 is already held by %, not 0017_producer_column. Another lane took '
      'this number while this file was being written. Pick the next version by reading '
      'brain.schema_migration AND every file''s own INSERT (grep -rn "INSERT INTO '
      'brain.schema_migration" -A3 across migrations/, budget/schema/ and queue/schema/), '
      'never by listing a directory: four of this repo''s migration files live outside '
      'migrations/ and one records a version its filename does not.', taken;
  END IF;
END $$;

BEGIN;

-- A migration that cannot get its locks in five seconds aborts having changed nothing, rather
-- than queueing behind a long reader and blocking every writer behind it. Re-run it.
SET LOCAL lock_timeout = '5s';

SET search_path TO brain, public;

-- ---------------------------------------------------------------- the declared producer names
--
-- One place a producer name is declared, so the backfill, the CHECK and the drift view below
-- cannot disagree about what a producer name is, and so adding a second producer is a row
-- rather than a re-read of three separate hardcoded lists.
--
-- `d3-ingest` is the only one that exists: `grep -rn 'PRODUCER' --include='*.py'` across the
-- repo returns exactly one constant, `ingest/ingest/events.py:33`.

CREATE TABLE IF NOT EXISTS brain.producer_name (
  name        text PRIMARY KEY,
  component   text NOT NULL,
  declared_by text NOT NULL,
  note        text NOT NULL DEFAULT ''
);

COMMENT ON TABLE brain.producer_name IS
  'Runtime component names that may appear in produced_by_producer and MUST NEVER appear in '
  'produced_by. A producer name is not a brain entity id: it resolves to nothing, on purpose, '
  'because it was never a claim about the brain. Adding a row here does not retro-fit the '
  'CHECK constraints migration 17 generated -- see brain.producer_in_entity_column, which is '
  'the view that catches a name declared after the fact.';

INSERT INTO brain.producer_name (name, component, declared_by, note) VALUES
  ('d3-ingest', 'ingest', 'ingest/ingest/events.py:33',
   'Stamps brain.session and brain.transcript on every registration. 2,269 rows carried this '
   'in produced_by until migration 17 moved them.')
ON CONFLICT (name) DO NOTHING;

-- ---------------------------------------------------------------- the column
--
-- Driven off information_schema rather than a hand-written list of 24 tables, the way migration
-- 5 drove its own loop, and for the same reason it gave: a hand list is how the twenty-fifth
-- table gets missed. Operator condition 2 of four.

DO $$
DECLARE t record; n int := 0;
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
      'ALTER TABLE brain.%I ADD COLUMN IF NOT EXISTS produced_by_producer text', t.table_name);
    EXECUTE format(
      'COMMENT ON COLUMN brain.%I.produced_by_producer IS %L', t.table_name,
      'The RUNTIME COMPONENT that wrote this row, when no claim about the brain was made. '
      'Never an entity id and never resolvable: brain-adapter entity resolve exits 3 on every '
      'value here, by design. This column exists so that produced_by NOT NULL means "an entity '
      'id" unconditionally (migration 17). resolution_status stays NULL beside it and still '
      'means "no resolution was ever attempted", which migration 5 named the fourth state and '
      'which this migration does not delete.');
    n := n + 1;
  END LOOP;
  RAISE NOTICE 'migration 17: produced_by_producer present on % table(s) carrying produced_by', n;
END $$;

-- ---------------------------------------------------------------- refuse the mixed shape first
--
-- Before moving anything, refuse a row that would make the move lossy. A producer name sitting
-- beside a non-NULL ref or status is not the fourth state: it is a resolution that landed on a
-- producer name, which would mean either the resolver accepted one (the widening 0142 itself
-- forbids) or somebody hand-wrote the row. Either way this migration must not quietly flatten
-- it, so it raises and says which table and how many.
--
-- Measured before writing: zero such rows. This guard exists because "measured at 18:2xZ" and
-- "true when this applies" are different claims, and other lanes are writing right now.

-- AND IT DOES NOT ASSUME MIGRATION 5's INVARIANT HOLDS. This was written assuming that
-- wherever `produced_by` exists its companions exist beside it, which is what migration 5
-- established -- and migration 15 exists precisely because a sweep cannot hold that for tables
-- created afterwards. Measured the hard way: `brain.subscriber_role` was created on live by
-- migration 10 at 18:48:39Z, fourteen minutes after this file first applied, with `produced_by`
-- and NEITHER companion, and the first re-run died on `column x.produced_by_ref does not exist`
-- and rolled back. So every predicate below is built per table from what that table actually
-- has. A table with no `produced_by_ref` and no `resolution_status` cannot contradict a
-- producer stamp, so for it the check is simply whether the name is there.

DO $$
DECLARE t record; bad bigint; total bigint := 0; where_ text := ''; pred text;
BEGIN
  FOR t IN
    SELECT c.table_name,
           EXISTS (SELECT 1 FROM information_schema.columns r
                    WHERE r.table_schema = 'brain' AND r.table_name = c.table_name
                      AND r.column_name = 'produced_by_ref')        AS has_ref,
           EXISTS (SELECT 1 FROM information_schema.columns s
                    WHERE s.table_schema = 'brain' AND s.table_name = c.table_name
                      AND s.column_name = 'resolution_status')      AS has_status
      FROM information_schema.columns c
      JOIN information_schema.tables tb
        ON tb.table_schema = c.table_schema AND tb.table_name = c.table_name
     WHERE c.table_schema = 'brain'
       AND c.column_name  = 'produced_by'
       AND tb.table_type  = 'BASE TABLE'
     ORDER BY c.table_name
  LOOP
    pred := '';
    IF t.has_ref    THEN pred := 'x.produced_by_ref IS NOT NULL'; END IF;
    IF t.has_status THEN
      pred := CASE WHEN pred = '' THEN '' ELSE pred || ' OR ' END || 'x.resolution_status IS NOT NULL';
    END IF;
    IF pred = '' THEN
      -- No companions on this table, so nothing can contradict a producer stamp here.
      CONTINUE;
    END IF;

    EXECUTE format(
      'SELECT count(*) FROM brain.%I x WHERE x.produced_by IN (SELECT name FROM '
      'brain.producer_name) AND (%s)',
      t.table_name, pred) INTO bad;
    IF bad > 0 THEN
      total := total + bad;
      where_ := where_ || format('%s=%s ', t.table_name, bad);
    END IF;
  END LOOP;
  IF total > 0 THEN
    RAISE EXCEPTION
      'migration 17: % row(s) hold a declared producer name in produced_by WITH a non-NULL '
      'produced_by_ref or resolution_status (%). That is not the fourth state and this '
      'migration will not flatten it: moving the name would drop a resolution claim that '
      'something asserted. Reconcile those rows first, then re-run. Nothing has been changed.',
      total, trim(where_);
  END IF;
END $$;

-- ---------------------------------------------------------------- the move
--
-- The 2,269 rows, and any other table that acquired one while this file was being written.
-- Same loop, same source of truth. `produced_by_ref` and `resolution_status` are NOT written:
-- they are already NULL (the guard above proved it) and they stay NULL, because both are still
-- true. THIS IS THE ONE POINT WHERE 0142's OWN FIX SHAPE (a) MUST NOT BE FOLLOWED: setting
-- resolution_status = 'unresolved' here would assert on 2,269 rows an attempt that never
-- happened.
--
-- Idempotent: after it runs, produced_by no longer matches, so a re-run moves 0 rows.
--
-- OPERATOR CONDITION 3 OF FOUR IS ENFORCED HERE, IN THE SAME BLOCK AS THE UPDATE, and that is
-- deliberate. The condition is "prove the real-entity rows still resolve after the move, since
-- they are the rows that matter and the ones a bulk UPDATE is most likely to flatten". The
-- strongest form of that inside SQL is not a count of the nine rows that happened to exist on
-- 2026-08-16: it is counting every row carrying a NON-producer produced_by before the UPDATE
-- and asserting the same count after. Same block so the two counts share a snapshot and cannot
-- be separated by another lane's write.
--
-- It was written the other way first -- "expected at least 9" -- and that version RAISED on a
-- freshly built scratch database, where the correct answer is zero. A migration that only
-- applies to the one store it was written against is not a migration. The before/after form
-- holds on an empty database (0 = 0), on live (9 = 9), and on whatever the store grows into,
-- and it never has to be edited again.

DO $$
DECLARE
  t record; moved bigint; total bigint := 0; detail text := '';
  n bigint; kept_before bigint := 0; kept_after bigint := 0;
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
    -- every row whose produced_by is a real value and NOT a declared producer name: the rows
    -- this UPDATE must not touch, counted before it runs.
    EXECUTE format(
      'SELECT count(*) FROM brain.%I x WHERE x.produced_by IS NOT NULL AND x.produced_by '
      'NOT IN (SELECT name FROM brain.producer_name)', t.table_name) INTO n;
    kept_before := kept_before + n;

    EXECUTE format(
      'UPDATE brain.%I x SET produced_by_producer = x.produced_by, produced_by = NULL '
      ' WHERE x.produced_by IN (SELECT name FROM brain.producer_name)', t.table_name);
    GET DIAGNOSTICS moved = ROW_COUNT;
    IF moved > 0 THEN
      total := total + moved;
      detail := detail || format('%s=%s ', t.table_name, moved);
    END IF;

    EXECUTE format(
      'SELECT count(*) FROM brain.%I x WHERE x.produced_by IS NOT NULL AND x.produced_by '
      'NOT IN (SELECT name FROM brain.producer_name)', t.table_name) INTO n;
    kept_after := kept_after + n;
  END LOOP;

  IF kept_after <> kept_before THEN
    RAISE EXCEPTION
      'migration 17: % row(s) carried a non-producer produced_by before the move and % after. '
      'The bulk UPDATE flattened rows it had no business touching -- these are the rows that '
      'carry a REAL, RESOLVABLE brain entity id. Rolling back.', kept_before, kept_after;
  END IF;

  RAISE NOTICE 'migration 17: moved % row(s) produced_by -> produced_by_producer (%)',
    total, COALESCE(NULLIF(trim(detail), ''), 'none: already applied, or no producer-stamped rows');
  RAISE NOTICE 'migration 17: % row(s) carrying a non-producer produced_by, unchanged by the move',
    kept_after;
END $$;

-- ---------------------------------------------------------------- the lie-detector
--
-- What makes the new meaning structural rather than a convention somebody has to remember. It
-- refuses exactly the declared producer names in `produced_by`, and it cannot refuse a real
-- entity id: `d3-ingest` resolves to nothing and appears in zero files in the brain, so no node
-- can legitimately carry it.
--
-- Generated from `brain.producer_name` AT APPLY TIME, which is the honest limit of this
-- constraint and is stated rather than hidden: a producer name declared after this runs is NOT
-- in these CHECKs. `brain.producer_in_entity_column` below is the query that catches that case,
-- the same way `brain.lineage_column_drift` catches migration 5's equivalent limit.
--
-- Dropped by name before it is added, so re-running is a no-op and re-generating after a new
-- producer is declared is one re-run of this file.

DO $$
DECLARE t record; names text;
BEGIN
  SELECT string_agg(quote_literal(name), ', ' ORDER BY name) INTO names FROM brain.producer_name;
  IF names IS NULL THEN
    RAISE EXCEPTION 'migration 17: brain.producer_name is empty, so no CHECK can be generated. '
                    'That is not an empty-set no-op, it is a missing INSERT above.';
  END IF;

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
    EXECUTE format('ALTER TABLE brain.%I DROP CONSTRAINT IF EXISTS %I',
                   t.table_name, t.table_name || '_produced_by_is_not_a_producer');
    EXECUTE format(
      'ALTER TABLE brain.%I ADD CONSTRAINT %I CHECK (produced_by IS NULL OR produced_by NOT IN (%s))',
      t.table_name, t.table_name || '_produced_by_is_not_a_producer', names);
  END LOOP;
END $$;

-- ---------------------------------------------------------------- the two drift checks
--
-- Both are the same shape as migration 5's `lineage_column_drift`: the invariant, as a query
-- anyone can run, empty meaning it holds.

CREATE OR REPLACE VIEW brain.producer_column_drift AS
  SELECT c.table_name
    FROM information_schema.columns c
    JOIN information_schema.tables tb
      ON tb.table_schema = c.table_schema AND tb.table_name = c.table_name
   WHERE c.table_schema = 'brain'
     AND tb.table_type  = 'BASE TABLE'
     AND c.column_name IN ('produced_by', 'produced_by_producer')
   GROUP BY c.table_name
  HAVING bool_or(c.column_name = 'produced_by')
     AND NOT bool_or(c.column_name = 'produced_by_producer');

COMMENT ON VIEW brain.producer_column_drift IS
  'Tables carrying produced_by without produced_by_producer beside it. Empty is the invariant '
  'migration 17 establishes. A row means a later migration created a table with produced_by and '
  'no place to put a producer name, so that lane''s next producer-stamped row goes back into '
  'the entity-id column. Migration 15''s event trigger adds the ref/status pair by construction '
  'and does NOT know about this column, which is the known gap this view exists to surface.';

GRANT SELECT ON brain.producer_column_drift TO brain_runtime;
GRANT SELECT ON brain.producer_name TO brain_runtime;

-- The other direction: a declared producer name sitting in produced_by anywhere. This one has
-- to be a function rather than a view, because the table list is only known at call time.

CREATE OR REPLACE FUNCTION brain.producer_in_entity_column()
  RETURNS TABLE (table_name text, produced_by text, rows bigint)
  LANGUAGE plpgsql
  SET search_path = pg_catalog, brain, pg_temp
AS $fn$
DECLARE t record;
BEGIN
  FOR t IN
    SELECT c.table_name AS tn
      FROM information_schema.columns c
      JOIN information_schema.tables tb
        ON tb.table_schema = c.table_schema AND tb.table_name = c.table_name
     WHERE c.table_schema = 'brain'
       AND c.column_name  = 'produced_by'
       AND tb.table_type  = 'BASE TABLE'
     ORDER BY c.table_name
  LOOP
    RETURN QUERY EXECUTE format(
      'SELECT %L::text, x.produced_by, count(*)::bigint FROM brain.%I x '
      ' WHERE x.produced_by IN (SELECT name FROM brain.producer_name) GROUP BY 2', t.tn, t.tn);
  END LOOP;
END $fn$;

COMMENT ON FUNCTION brain.producer_in_entity_column() IS
  'Every row in schema brain holding a DECLARED producer name in produced_by. Empty is the '
  'invariant migration 17 establishes. The per-table CHECK constraints refuse this shape for '
  'the names declared when 17 applied; this function also catches a name added to '
  'brain.producer_name afterwards, which no CHECK generated earlier can see.';

GRANT EXECUTE ON FUNCTION brain.producer_in_entity_column() TO brain_runtime;

-- ---------------------------------------------------------------- verify before committing
--
-- The migration proves its own work rather than asserting it, and the proof is inside the same
-- transaction, so a failure rolls the whole thing back. The third assertion -- that a bulk
-- UPDATE did not flatten a row carrying a real entity id -- is made in the move block above,
-- where it can compare a before and an after inside one snapshot.
--
-- Nothing here hardcodes a count. Every assertion is a shape, so this file applies unchanged to
-- live, to a rehearsal copy, and to an empty database built by `engine/bin/scratch-db.sh`.

DO $$
DECLARE n bigint; drift bigint;
BEGIN
  -- 1. no declared producer name is left in produced_by anywhere.
  SELECT COALESCE(sum(rows), 0) INTO n FROM brain.producer_in_entity_column();
  IF n <> 0 THEN
    RAISE EXCEPTION 'migration 17: % row(s) still hold a producer name in produced_by after the '
                    'move. Rolling back.', n;
  END IF;

  -- 2. every table carrying produced_by has the new column beside it.
  SELECT count(*) INTO drift FROM brain.producer_column_drift;
  IF drift <> 0 THEN
    RAISE EXCEPTION 'migration 17: % table(s) carry produced_by without produced_by_producer '
                    'after the loop ran. Rolling back.', drift;
  END IF;
END $$;

-- No new grants otherwise. 0002_roles grants are TABLE-level, so a new column inherits; the
-- same thing migration 5 verified rather than assumed (`grep GRANT migrations/0002_roles.sql`
-- shows no column lists). The two objects created above are new and are granted explicitly.

INSERT INTO brain.schema_migration (version, name) VALUES (17, '0017_producer_column')
  ON CONFLICT (version) DO NOTHING;

COMMIT;
