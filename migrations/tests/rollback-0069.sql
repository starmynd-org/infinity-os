-- The rollback migration 0069 states in its header, as a runnable file so it is executed rather
-- than read. Scratch only: apply with
--     ENGINE_SCRATCH_DB=<your scratch> engine/bin/scratch-db.sh psql -v ON_ERROR_STOP=1 -f - \
--         < migrations/tests/rollback-0069.sql
-- Run AFTER rollback-0070.sql. Loss, stated: every workspace declaration and repo-set row.
\set ON_ERROR_STOP on
BEGIN;
  DROP TRIGGER IF EXISTS workspace_repo_is_added_by_a_human ON brain.workspace_repo;
  DROP TRIGGER IF EXISTS workspace_is_declared_by_a_human ON brain.workspace;
  DROP FUNCTION IF EXISTS brain.workspace_repo_is_added_by_a_human();
  DROP FUNCTION IF EXISTS brain.workspace_is_declared_by_a_human();
  DROP TABLE IF EXISTS brain.workspace_repo;
  DROP TABLE IF EXISTS brain.workspace;
  DELETE FROM brain.schema_migration WHERE version = 69;
COMMIT;
SELECT 'rolled back 69; ledger now ' || max(version) AS verdict FROM brain.schema_migration;
