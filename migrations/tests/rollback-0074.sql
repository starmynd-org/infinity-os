-- The rollback of migration 0074: the body and comment in effect before 74, which are migration
-- 48's, verbatim. Scratch only:
--     ENGINE_SCRATCH_DB=<your scratch> engine/bin/scratch-db.sh psql -v ON_ERROR_STOP=1 -f - \
--         < migrations/tests/rollback-0074.sql
-- No loss.
--
-- CORRECTED BY MIGRATION 77'S COMMIT. This file used to restore migration 6's body, which is what
-- 0074's header names. But migration 48 had redefined the function after 6, so that "rollback"
-- also dropped 48's two impact clauses and left `impact` refusing money, critical and none. The
-- body before 74 is 48's, and that is what a rollback of 74 has to put back.
--
-- On a store at 77, this puts back 48's body, which already carries the impact clauses 77
-- restores. So impact stays correct, and only 74's urgency exclusion is undone, which is this
-- file's job. Re-applying 0074's file afterwards re-drops impact; apply 0077 again after it.
\set ON_ERROR_STOP on
BEGIN;
CREATE OR REPLACE FUNCTION brain.signal_ok(field text, raw text) RETURNS boolean
  LANGUAGE sql IMMUTABLE AS $$
  SELECT raw IS NULL OR btrim(raw) = ''
      OR lower(btrim(raw)) IN ('low','medium','high')
      OR (field = 'stakes'         AND lower(btrim(raw)) IN ('none','critical'))
      OR (field = 'reversibility'  AND lower(btrim(raw)) IN ('reversible','costly','irreversible'))
      OR (field = 'urgency'        AND lower(btrim(raw)) IN ('none','soon','deadline','decaying'))
      OR (field = 'effort'         AND lower(btrim(raw)) IN ('small','large'))
      OR (field IN ('dependency_unblocking','confidence')
          AND brain.signal_numeric(raw) IS NOT NULL)
      -- Migration 48. The bands, plus money, and the ambiguous band between them is refused.
      OR (field = 'impact' AND lower(btrim(raw)) IN ('none','critical'))
      OR (field = 'impact' AND brain.signal_numeric(raw) IS NOT NULL
          AND (brain.signal_numeric(raw) = 0 OR brain.signal_numeric(raw) >= 100))
$$;
COMMENT ON FUNCTION brain.signal_ok(text, text) IS
  'Strict on write. NULL and '''' mean never assessed and are permitted. Since migration 6 a '
  'decimal literal is permitted for dependency_unblocking and confidence. Since migration 48 '
  '`impact` takes a band or an amount of money, and REFUSES a bare number in (0, 100) because '
  'that reads as both money and a 1-to-5 rating and the two order the row differently.';
DELETE FROM brain.schema_migration WHERE version = 74;
COMMIT;
SELECT 'rolled back 74; ledger now ' || max(version) || '; signal_ok(urgency, high) is now ' ||
       brain.signal_ok('urgency', 'high') || '; signal_ok(impact, 100) is now ' ||
       brain.signal_ok('impact', '100') AS verdict FROM brain.schema_migration;
