-- The rollback migration 0077 states in its header: migration 74's body and comment, verbatim. That
-- brings the defect 77 closes BACK (impact refuses money, critical and none), which is what undoing
-- 77 means. Scratch only:
--     ENGINE_SCRATCH_DB=<your scratch> engine/bin/scratch-db.sh psql -v ON_ERROR_STOP=1 -f - \
--         < migrations/tests/rollback-0077.sql
-- Refuses unless 77 is the latest ledger row, so nothing applied after it is undone underneath it.
-- No row is lost. A row written with money, critical or none while 77 stood stays, and refuses its
-- next UPDATE until 77 is applied again.
\set ON_ERROR_STOP on
BEGIN;
DO $$
DECLARE head integer; occupied text;
BEGIN
  SELECT max(version) INTO head FROM brain.schema_migration;
  SELECT name INTO occupied FROM brain.schema_migration WHERE version = 77;
  IF head IS DISTINCT FROM 77
     OR occupied IS DISTINCT FROM '0077_impact_takes_money_again_after_74' THEN
    RAISE EXCEPTION 'rollback77 requires exact latest migration 77, got head % and name %',
      head, occupied
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
END $$;
CREATE OR REPLACE FUNCTION brain.signal_ok(field text, raw text) RETURNS boolean
  LANGUAGE sql IMMUTABLE AS $$
  SELECT raw IS NULL OR btrim(raw) = ''
      -- Migration 74: the generic three-level clause no longer covers urgency, whose vocabulary
      -- is the four words below and nothing else. Every other line is migration 6's.
      OR (field <> 'urgency' AND lower(btrim(raw)) IN ('low','medium','high'))
      OR (field = 'stakes'         AND lower(btrim(raw)) IN ('none','critical'))
      OR (field = 'reversibility'  AND lower(btrim(raw)) IN ('reversible','costly','irreversible'))
      OR (field = 'urgency'        AND lower(btrim(raw)) IN ('none','soon','deadline','decaying'))
      OR (field = 'effort'         AND lower(btrim(raw)) IN ('small','large'))
      OR (field IN ('dependency_unblocking','confidence')
          AND brain.signal_numeric(raw) IS NOT NULL)
$$;
COMMENT ON FUNCTION brain.signal_ok(text, text) IS
  'Strict on write. NULL and '''' mean never assessed and are permitted. Since migration 6 a '
  'decimal literal is permitted for dependency_unblocking and confidence and nothing else. '
  'Since migration 74 urgency admits exactly none, soon, deadline and decaying: the generic '
  'low/medium/high clause does not cover it, because the surface that renders it refuses those '
  'words and a row the store accepted was a 500 waiting to happen.';
DELETE FROM brain.schema_migration WHERE version = 77;
COMMIT;
SELECT 'rolled back 77; ledger now ' || max(version) || '; signal_ok(impact, 100) is now ' ||
       brain.signal_ok('impact', '100') || '; signal_ok(urgency, high) is now ' ||
       brain.signal_ok('urgency', 'high') AS verdict FROM brain.schema_migration;
