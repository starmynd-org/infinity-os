-- The rollback migration 0073 states in its header, as a runnable file. Scratch only:
--     ENGINE_SCRATCH_DB=<your scratch> engine/bin/scratch-db.sh psql -v ON_ERROR_STOP=1 -f - \
--         < migrations/tests/rollback-0073.sql
-- LOSS, STATED: the credential pointers and the service-role mappings; re-derivable by
-- re-provisioning. Restores migration 70's receipt trigger body from 70's own text and re-grants
-- INSERT on the receipt to brain_runtime, which is 70's stated residual 2 coming back.
\set ON_ERROR_STOP on
BEGIN;
  CREATE OR REPLACE FUNCTION brain.repo_access_receipt_is_a_reading() RETURNS trigger
    LANGUAGE plpgsql AS $$
  DECLARE set_size bigint;
  BEGIN
    IF NEW.checked_at > now() THEN
      RAISE EXCEPTION 'refusing a receipt dated in the future (%): a reading cannot be taken later '
                      'than now, and a future date is the one way to stretch one', NEW.checked_at
        USING ERRCODE = 'check_violation';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM brain.human_roster() r WHERE r.human = NEW.human) THEN
      RAISE EXCEPTION 'refusing a receipt for %: this database does not know that human', NEW.human
        USING ERRCODE = 'foreign_key_violation',
              HINT = 'Identity is both halves. A host account that reaches every repo still reaches '
                     'nothing here until Postgres maps a login to that human.';
    END IF;
    SELECT count(*) INTO set_size FROM brain.workspace_repo r WHERE r.workspace = NEW.workspace;
    IF NEW.repos_checked <> set_size THEN
      RAISE EXCEPTION 'refusing a receipt over % repos for workspace %, whose declared set holds %: '
                      'the reading was taken over a different set', NEW.repos_checked, NEW.workspace,
                      set_size
        USING ERRCODE = 'check_violation',
              HINT = 'The adapter reads brain.workspace_repo and asks about every row. A receipt '
                     'over fewer or more repos than that is about some other workspace.';
    END IF;
    RETURN NEW;
  END $$;
  GRANT INSERT ON brain.repo_access_receipt TO brain_runtime;
  DROP FUNCTION IF EXISTS brain.provision_workspace_service_role(name, text);
  DROP FUNCTION IF EXISTS brain.current_workspace_service();
  DROP TABLE IF EXISTS brain.workspace_service_role;
  DROP TRIGGER IF EXISTS workspace_service_credential_is_declared_by_a_human
    ON brain.workspace_service_credential;
  DROP FUNCTION IF EXISTS brain.workspace_service_credential_is_declared_by_a_human();
  DROP TABLE IF EXISTS brain.workspace_service_credential;
  DELETE FROM brain.schema_migration WHERE version = 73;
COMMIT;
SELECT 'rolled back 73; ledger now ' || max(version) AS verdict FROM brain.schema_migration;
