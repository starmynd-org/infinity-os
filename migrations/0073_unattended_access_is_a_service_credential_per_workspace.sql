-- migration 73: UNATTENDED ACCESS IS A SERVICE CREDENTIAL SCOPED PER WORKSPACE, and the receipt
-- of a repo-host check is written by that credential and by nothing else.
--
-- LEDGER VERSION 73. max(version) + 1 after 72, read from `engine/bin/scratch-db.sh ledger` on
-- 2026-09-09. Not a hole. Depends on 69 (brain.workspace) and 70 (brain.repo_access_receipt).
--
-- Written by 2026-09-09-IOS-term-5. Closes migration 70's stated residual 2.
--
-- ================================================================= the ruling this implements
--
-- Andrew, 2026-09-09, ARCHITECTURE-2026-09-09-tenancy-and-the-git-boundary.md, "the three
-- formerly open items, all ruled", item 1, his words: "a service credential scoped per workspace,
-- and that's the answer." Settled. And on secrets, the shape the catalogue takes: "Git holds the
-- POINTER and the METADATA. Never the value." This file holds a pointer and a mapping. It holds no
-- value and cannot: a committed artefact carries references, never values, which is migration 10's
-- and 20's rule and store/SECRETS.md's.
--
-- WHAT IS NOT HERE, on purpose: the per-brain secret CATALOGUE (name, backend, link or path, the
-- StarMynd link that embeds, click-to-open). That is a governed file in each brain, beside
-- repo-registry/**, per the governed-files ruling in the same document. A table for it would put
-- what the system knows into Postgres against the git/Postgres boundary. This seat put that reading
-- to the Admiral at 20260909T124906Z and proceeded on it; if the reading is overruled, this file
-- is unaffected, because a runtime pointer is Postgres's whichever way the catalogue goes.
--
-- ================================================================= what this closes, measured
--
-- Migration 70 granted INSERT on brain.repo_access_receipt to brain_runtime and said in its header
-- (residual 2): "A RECEIPT IS AS TRUSTWORTHY AS THE LOGIN THAT WROTE IT, and today that is
-- brain_runtime", the login every agent holds. Measured 2026-09-09 on ios_term5_scratch at ledger
-- 72 by migrations/tests/probe_name_attribution_siblings.py: from brain_runtime, a receipt saying
-- lane-e-two reaches 1 of 1 repos in a workspace LANDED (receipt_seq 26). An agent could therefore
-- widen any human's reach with one INSERT. After this file:
--
--   * INSERT on brain.repo_access_receipt is REVOKED from brain_runtime.
--   * A receipt is accepted only from the login brain.workspace_service_role maps to THAT
--     workspace, checked by trigger, so a workspace's service login cannot write another
--     workspace's receipts either. Both refusals are watched below.
--
-- ================================================================= the two tables, and the function
--
--   brain.workspace_service_credential   ONE row per workspace: which backend holds the
--                                        workspace's service secret, and the REFERENCE by which
--                                        that backend is asked for it. Declared by a human, as
--                                        that human. The value is never here, and a CHECK refuses
--                                        strings shaped like the common token formats as a
--                                        tripwire, stated as a tripwire and not a guarantee.
--
--   brain.workspace_service_role         the login role that IS a workspace's service, the way
--                                        brain.human_role is the login that IS a human (20) and
--                                        brain.subscriber_role the login that IS a listener (10).
--                                        Owner-only, unwritable by the runtime, filled by the
--                                        provisioning function over the bootstrap connection.
--
--   brain.current_workspace_service()    session_user in, workspace out, or NULL. Fixed at
--                                        authentication; no SQL a client sends changes it.
--
--   brain.provision_workspace_service_role(p_role, p_workspace)
--                                        superuser only: grants exactly what the adapter needs
--                                        (CONNECT, SELECT on the workspace tables, INSERT on the
--                                        receipt, USAGE on its sequence, EXECUTE on the reach
--                                        functions) and records the mapping. It mints no role and
--                                        no password: that is the provisioner script's act at a
--                                        keyboard, exactly as store/bin/provision-human.sh does
--                                        for a human, and the script is not this seat's file.
--
-- ================================================================= what it does NOT close
--
-- 1. THE PROVISIONER SCRIPT does not exist yet. Until it does, no workspace has a service login,
--    no adapter can write a receipt, and nobody reaches any declared workspace. Fail-closed, and
--    the test that proves this file mints its own throwaway role on the scratch cluster to get
--    past that, drops it after, and says so.
-- 2. THE HOST RESIDUAL, as 10, 20, 36, 63, 71 state. A process that can read the service secret
--    file is the service. Unchanged.
-- 3. THE CATALOGUE, per the top of this header, is a governed git file and not here.
--
-- ================================================================= rollback, executed
--
-- migrations/tests/rollback-0073.sql: drops the trigger's new rule by restoring 70's function body
-- from its own text, re-grants INSERT to brain_runtime, drops the function, the two tables and the
-- ledger row. LOSS, STATED: the credential pointers and the role mappings. Both are re-derivable
-- by re-provisioning, which is an operator's act. Executed and re-applied on ios_term5_scratch on
-- 2026-09-09.

\set ON_ERROR_STOP on

BEGIN;

-- ---------------------------------------------------------------- the pointer

CREATE TABLE IF NOT EXISTS brain.workspace_service_credential (
  workspace    text        PRIMARY KEY REFERENCES brain.workspace (workspace),
  backend      text        NOT NULL,
  secret_ref   text        NOT NULL,
  declared_at  timestamptz NOT NULL DEFAULT now(),
  declared_by  text        NOT NULL,
  CONSTRAINT workspace_service_credential_backend_ck
    CHECK (backend IN ('local-attended', '1password', 'google-secret-manager', 'starmynd-connect')),
  CONSTRAINT workspace_service_credential_ref_is_a_reference_ck
    CHECK (btrim(secret_ref) <> '' AND secret_ref !~ '\s' AND length(secret_ref) <= 512),
  -- A TRIPWIRE, NOT A GUARANTEE: the common shapes of a token VALUE. A reference is a name, a
  -- path or a URL into a backend; nothing that starts like these is one of those.
  CONSTRAINT workspace_service_credential_ref_is_not_a_value_ck
    CHECK (secret_ref !~ '^(ghp_|github_pat_|gho_|ghu_|ghs_|ghr_|sk-|AKIA|xox[abp]-|glpat-|-----BEGIN)')
);

COMMENT ON TABLE brain.workspace_service_credential IS
  'Where a workspace''s SERVICE secret lives: the backend and the reference by which it is '
  'asked for. Never the value. One row per workspace; declared by a human, as that human. The '
  'per-brain secret CATALOGUE is a governed git file and is not this table.';

CREATE OR REPLACE FUNCTION brain.workspace_service_credential_is_declared_by_a_human() RETURNS trigger
  LANGUAGE plpgsql AS $$
DECLARE who text;
BEGIN
  who := brain.current_human();
  IF who IS NULL THEN
    RAISE EXCEPTION 'refusing to declare the service credential of workspace %: this connection is '
                    '%, which is not a human login', NEW.workspace, session_user
      USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF NEW.declared_by IS DISTINCT FROM who THEN
    RAISE EXCEPTION 'refusing to record % as the declarer: this connection is %', NEW.declared_by, who
      USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS workspace_service_credential_is_declared_by_a_human
  ON brain.workspace_service_credential;
CREATE TRIGGER workspace_service_credential_is_declared_by_a_human
  BEFORE INSERT OR UPDATE ON brain.workspace_service_credential
  FOR EACH ROW EXECUTE FUNCTION brain.workspace_service_credential_is_declared_by_a_human();

GRANT SELECT ON brain.workspace_service_credential TO brain_runtime, brain_subscriber;
GRANT INSERT, UPDATE ON brain.workspace_service_credential TO brain_runtime;

-- ---------------------------------------------------------------- the mapping: which login IS a workspace's service

CREATE TABLE IF NOT EXISTS brain.workspace_service_role (
  role_name   name        PRIMARY KEY,
  workspace   text        NOT NULL UNIQUE REFERENCES brain.workspace (workspace),
  granted_at  timestamptz NOT NULL DEFAULT now(),
  granted_by  text        NOT NULL DEFAULT ''
);

COMMENT ON TABLE brain.workspace_service_role IS
  'One login role, one workspace''s service. Migration 20''s mechanism applied to a service: the '
  'runtime cannot read this table and cannot write it, so the thing that says a connection is a '
  'workspace''s service is not reachable from where an agent is. Filled by '
  'brain.provision_workspace_service_role over the bootstrap superuser connection.';

REVOKE ALL ON brain.workspace_service_role FROM PUBLIC;
REVOKE ALL ON brain.workspace_service_role FROM brain_runtime, brain_producer, brain_subscriber;
GRANT SELECT ON brain.workspace_service_role TO brain_owner;

CREATE OR REPLACE FUNCTION brain.current_workspace_service() RETURNS text
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = brain, pg_temp AS $$
  SELECT workspace FROM brain.workspace_service_role WHERE role_name = session_user
$$;

ALTER FUNCTION brain.current_workspace_service() OWNER TO brain_owner;
GRANT EXECUTE ON FUNCTION brain.current_workspace_service() TO PUBLIC;

COMMENT ON FUNCTION brain.current_workspace_service() IS
  'The workspace this connection is the SERVICE of, from session_user, or NULL. The one place a '
  'gate should ask that question. Public because the answer is about the caller.';

CREATE OR REPLACE FUNCTION brain.provision_workspace_service_role(p_role name, p_workspace text)
  RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = brain, pg_temp AS $$
BEGIN
  IF p_role !~ '^brain_ws_[a-z0-9_]+$' THEN
    RAISE EXCEPTION 'service role % must be brain_ws_<slug>', p_role;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM brain.workspace w WHERE w.workspace = p_workspace) THEN
    RAISE EXCEPTION 'workspace % is not declared; declare it first (migration 69)', p_workspace;
  END IF;
  EXECUTE format('GRANT CONNECT ON DATABASE %I TO %I', current_database(), p_role);
  EXECUTE format('GRANT USAGE ON SCHEMA brain TO %I', p_role);
  EXECUTE format('GRANT SELECT ON brain.workspace, brain.workspace_repo, '
                 'brain.human_repo_host_identity, brain.repo_access_receipt, '
                 'brain.workspace_service_credential TO %I', p_role);
  EXECUTE format('GRANT INSERT ON brain.repo_access_receipt TO %I', p_role);
  EXECUTE format('GRANT USAGE ON SEQUENCE brain.repo_access_receipt_receipt_seq_seq TO %I', p_role);
  EXECUTE format('GRANT EXECUTE ON FUNCTION brain.human_reach_report(text, text), '
                 'brain.human_reaches(text, text), brain.workspaces_visible_to(text), '
                 'brain.human_roster() TO %I', p_role);
  EXECUTE format('ALTER ROLE %I IN DATABASE %I SET statement_timeout = %L',
                 p_role, current_database(), '15s');
  INSERT INTO brain.workspace_service_role (role_name, workspace, granted_by)
       VALUES (p_role, p_workspace, session_user)
  ON CONFLICT (role_name) DO UPDATE SET workspace = EXCLUDED.workspace, granted_at = now(),
                                        granted_by = EXCLUDED.granted_by;
  RETURN format('%s is the service of %s', p_role, p_workspace);
END $$;

-- SUPERUSER ONLY, for migration 20's two reasons: this runs GRANT, and an INSERT into the mapping
-- is the act of becoming a workspace's service.
REVOKE EXECUTE ON FUNCTION brain.provision_workspace_service_role(name, text) FROM PUBLIC;

-- ---------------------------------------------------------------- the receipt writer

REVOKE INSERT ON brain.repo_access_receipt FROM brain_runtime;

-- Migration 70's function, with ONE rule added at the top and everything else byte-for-byte as
-- 70 wrote it. 70 is this seat's own file on this branch and has been applied to no store but
-- scratch; the replace is stated here so 70's header stays true about what its trigger does on
-- a store at 70, and this header says what it does from 73 on.
CREATE OR REPLACE FUNCTION brain.repo_access_receipt_is_a_reading() RETURNS trigger
  LANGUAGE plpgsql AS $$
DECLARE set_size bigint; svc text;
BEGIN
  svc := brain.current_workspace_service();
  IF svc IS NULL THEN
    RAISE EXCEPTION 'refusing a receipt for workspace % from %: this connection is not a '
                    'workspace''s service login', NEW.workspace, session_user
      USING ERRCODE = 'insufficient_privilege',
            HINT = 'A receipt is what the repo host said, and only the workspace''s own service '
                   'credential may record it (Andrew, 2026-09-09: unattended access is a service '
                   'credential scoped per workspace). Provision one with '
                   'brain.provision_workspace_service_role over the bootstrap connection.';
  END IF;
  IF svc <> NEW.workspace THEN
    RAISE EXCEPTION 'refusing a receipt for workspace % from the service of %: a service '
                    'credential is scoped to its own workspace', NEW.workspace, svc
      USING ERRCODE = 'insufficient_privilege';
  END IF;
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

-- ---------------------------------------------------------------- every refusal watched happening
--
-- As the applying superuser, mapped for this transaction to a fixture human (for the declarations)
-- and to a fixture workspace's service (for the receipts), the way 63, 70 and 72 map fixtures.
DO $$
DECLARE
  n int := 0; refusals int := 0; controls int := 0; rejected boolean := false;
  fx text := '_mv73_fixture_human'; ws_a text := 'ws-mv73-a'; ws_b text := 'ws-mv73-b';
BEGIN
  INSERT INTO brain.human_role (role_name, human, granted_by)
    VALUES (session_user, fx, 'migration 73 fixture');
  INSERT INTO brain.workspace (workspace, declared_by) VALUES (ws_a, fx), (ws_b, fx);
  INSERT INTO brain.workspace_repo (workspace, repo_full_name, added_by)
    VALUES (ws_a, 'starmynd-org/a', fx), (ws_b, 'starmynd-org/b', fx);
  INSERT INTO brain.human_repo_host_identity (human, host_login, linked_by)
    VALUES (fx, 'mv73-login', fx);

  -- 1 refusal: a receipt from a connection that is no workspace's service.
  BEGIN
    INSERT INTO brain.repo_access_receipt (human, host_login, workspace, reaches, repos_checked,
                                           repos_reached)
      VALUES (fx, 'mv73-login', ws_a, true, 1, 1);
    RAISE EXCEPTION 'migration 73: a receipt from a non-service connection was NOT refused';
  EXCEPTION WHEN insufficient_privilege THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 2 refusal: a value-shaped secret reference.
  BEGIN
    INSERT INTO brain.workspace_service_credential (workspace, backend, secret_ref, declared_by)
      VALUES (ws_a, 'local-attended', 'ghp_0123456789abcdef', fx);
    RAISE EXCEPTION 'migration 73: a token-shaped secret_ref was accepted';
  EXCEPTION WHEN check_violation THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 3 control: a reference lands, declared by the human as themselves.
  INSERT INTO brain.workspace_service_credential (workspace, backend, secret_ref, declared_by)
    VALUES (ws_a, 'local-attended', 'brain-postgres-ws-ws-mv73-a', fx);
  n := n + 1; controls := controls + 1;

  -- 4 refusal: an unknown backend.
  BEGIN
    INSERT INTO brain.workspace_service_credential (workspace, backend, secret_ref, declared_by)
      VALUES (ws_b, 'a-text-file-on-the-desktop', 'somewhere', fx);
    RAISE EXCEPTION 'migration 73: an unknown backend was accepted';
  EXCEPTION WHEN check_violation THEN n := n + 1; refusals := refusals + 1;
  END;

  -- Map THIS session as ws_a's service (a fixture: the real mapping is a login role of its own).
  INSERT INTO brain.workspace_service_role (role_name, workspace, granted_by)
    VALUES (session_user, ws_a, 'migration 73 fixture');

  -- 5 control: ws_a's service records a receipt for ws_a.
  INSERT INTO brain.repo_access_receipt (human, host_login, workspace, reaches, repos_checked,
                                         repos_reached)
    VALUES (fx, 'mv73-login', ws_a, true, 1, 1);
  n := n + 1; controls := controls + 1;

  -- 6 refusal: ws_a's service records a receipt for ws_b.
  BEGIN
    INSERT INTO brain.repo_access_receipt (human, host_login, workspace, reaches, repos_checked,
                                           repos_reached)
      VALUES (fx, 'mv73-login', ws_b, true, 1, 1);
    RAISE EXCEPTION 'migration 73: a service wrote a receipt for another workspace';
  EXCEPTION WHEN insufficient_privilege THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 7 control: 70's other rules still hold under the new function (the set-size check).
  BEGIN
    INSERT INTO brain.repo_access_receipt (human, host_login, workspace, reaches, repos_checked,
                                           repos_reached)
      VALUES (fx, 'mv73-login', ws_a, true, 2, 2);
    RAISE EXCEPTION 'migration 73: the set-size rule from 70 was lost in the replace';
  EXCEPTION WHEN check_violation THEN n := n + 1; controls := controls + 1;
  END;

  -- 8 refusal: the provisioning function refuses a role name outside its pattern.
  BEGIN
    PERFORM brain.provision_workspace_service_role('brain_runtime', ws_a);
  EXCEPTION WHEN raise_exception THEN rejected := true;
  END;
  IF NOT rejected THEN
    RAISE EXCEPTION 'migration 73: brain_runtime was accepted as a workspace service role';
  END IF;
  n := n + 1; refusals := refusals + 1;

  ALTER TABLE brain.repo_access_receipt DISABLE TRIGGER repo_access_receipt_append_only;
  DELETE FROM brain.repo_access_receipt WHERE workspace IN (ws_a, ws_b);
  ALTER TABLE brain.repo_access_receipt ENABLE TRIGGER repo_access_receipt_append_only;
  DELETE FROM brain.workspace_service_role WHERE granted_by = 'migration 73 fixture';
  DELETE FROM brain.workspace_service_credential WHERE workspace IN (ws_a, ws_b);
  DELETE FROM brain.human_repo_host_identity WHERE human = fx;
  DELETE FROM brain.workspace_repo WHERE workspace IN (ws_a, ws_b);
  DELETE FROM brain.workspace WHERE workspace IN (ws_a, ws_b);
  DELETE FROM brain.human_role WHERE human = fx AND granted_by = 'migration 73 fixture';

  IF n <> 8 OR refusals <> 5 OR controls <> 3 THEN
    RAISE EXCEPTION 'migration 73: expected 8 checks as 5 refusals and 3 controls, ran % as % and %',
      n, refusals, controls;
  END IF;
  RAISE NOTICE 'migration 73: % of 8 checks passed, % refusals watched refusing and % positive '
               'controls. A receipt from a non-service login, a receipt across workspaces, a '
               'token-shaped reference, an unknown backend and a runtime role posing as a service '
               'were refused; a service wrote its own workspace''s receipt and 70''s set-size rule '
               'survived the replace. Fixture rows removed; tables ship EMPTY.',
               n, refusals, controls;
END $$;

INSERT INTO brain.schema_migration (version, name)
  VALUES (73, '0073_unattended_access_is_a_service_credential_per_workspace')
  ON CONFLICT (version) DO NOTHING;

COMMIT;
