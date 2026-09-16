-- The rollback migration 0070 states in its header, as a runnable file so it is executed rather
-- than read. Scratch only: apply with
--     ENGINE_SCRATCH_DB=<your scratch> engine/bin/scratch-db.sh psql -v ON_ERROR_STOP=1 -f - \
--         < migrations/tests/rollback-0070.sql
-- Run BEFORE rollback-0069.sql: brain.repo_access_receipt references brain.workspace.
-- Loss, stated: every repo-host link and every receipt on the store. Both re-derivable.
\set ON_ERROR_STOP on
BEGIN;
  DROP TRIGGER IF EXISTS approval_decider_reaches_the_workspace ON brain.approval;
  DROP FUNCTION IF EXISTS brain.approval_decider_reaches_the_workspace();
  DROP FUNCTION IF EXISTS brain.current_workspace_access(text);
  DROP FUNCTION IF EXISTS brain.workspaces_visible_to(text);
  DROP FUNCTION IF EXISTS brain.human_reaches(text, text);
  DROP FUNCTION IF EXISTS brain.human_reach_report(text, text);
  DROP TRIGGER IF EXISTS repo_access_receipt_append_only ON brain.repo_access_receipt;
  DROP TRIGGER IF EXISTS repo_access_receipt_is_a_reading ON brain.repo_access_receipt;
  DROP FUNCTION IF EXISTS brain.repo_access_receipt_append_only();
  DROP FUNCTION IF EXISTS brain.repo_access_receipt_is_a_reading();
  DROP TABLE IF EXISTS brain.repo_access_receipt;
  DROP TRIGGER IF EXISTS human_repo_host_identity_is_a_human_act ON brain.human_repo_host_identity;
  DROP FUNCTION IF EXISTS brain.human_repo_host_identity_is_a_human_act();
  DROP TABLE IF EXISTS brain.human_repo_host_identity;
  DROP FUNCTION IF EXISTS brain.repo_access_receipt_ttl();
  DELETE FROM brain.schema_migration WHERE version = 70;
COMMIT;
SELECT 'rolled back 70; ledger now ' || max(version) AS verdict FROM brain.schema_migration;
