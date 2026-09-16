-- The rollback migration 0071 states in its header, as a runnable file. Scratch only:
--     ENGINE_SCRATCH_DB=<your scratch> engine/bin/scratch-db.sh psql -v ON_ERROR_STOP=1 -f - \
--         < migrations/tests/rollback-0071.sql
-- No loss: one trigger and one function.
\set ON_ERROR_STOP on
BEGIN;
  DROP TRIGGER IF EXISTS approval_decider_is_the_login ON brain.approval;
  DROP FUNCTION IF EXISTS brain.approval_decider_is_the_login();
  DELETE FROM brain.schema_migration WHERE version = 71;
COMMIT;
SELECT 'rolled back 71; ledger now ' || max(version) AS verdict FROM brain.schema_migration;
