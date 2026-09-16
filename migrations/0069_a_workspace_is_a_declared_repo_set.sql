-- migration 69: A WORKSPACE IS A DECLARED REPO SET, and it is the tenancy unit.
--
-- LEDGER VERSION 69. Read 2026-09-09 from `engine/bin/scratch-db.sh ledger` (65 versions, 1..68,
-- holes at 54, 55 and 60, NEXT VERSION IS 69) and from brain.schema_migration on scratch
-- ios_term5_scratch (max 68). Not a hole: max(version) + 1, per docs/CHANGING-IT.md.
--
-- Written by 2026-09-09-IOS-term-5, the only seat that writes migrations in the 2026-09-09 fleet.
--
-- ================================================================= the ruling this implements
--
-- Andrew, 2026-09-09, recorded in ARCHITECTURE-2026-09-09-tenancy-and-the-git-boundary.md and
-- closed, not re-openable:
--
--     workspace = (a set of repos) + (the people whose GitHub identity can reach them)
--
-- "Workspace is the tenancy unit, even with one workspace." Fifty people on one repo set and one
-- person on six repo sets are the same abstraction at different cardinalities. And the manifest
-- for it already exists one layer down: `repo-registry/` in the repos root is a declared set of
-- children with their kind and their remote. This table is that pattern at the product layer.
--
-- ================================================================= what exists already, measured
--
-- Grepped at 0180a51 before writing a line, per the term-5 brief section 8: the word "github" and
-- a workspace TABLE occur in zero files under migrations/, queue/schema/, store/ and web/model.py
-- (every file in those four paths read). Migration 63 put a `workspace text` COLUMN on five
-- authority tables and drew the boundary between workspaces; it deliberately created no table,
-- because its question was "can a grant in one license an act in another" (no) and not "what IS
-- a workspace". This file answers the second question and changes nothing about the first: every
-- `workspace` column migration 63 added keeps its shape, and no foreign key is added to them,
-- because rows written before this migration name workspaces nobody declared and inventing a
-- declaration for them would be asserting a boundary nobody drew (migration 63's own rule, "NULL
-- is not a wildcard", applied to the table rather than the column).
--
-- ================================================================= what this file is NOT
--
-- NOT a permission table. Which humans reach a workspace is DERIVED from repo access at the repo
-- host, never maintained here (Andrew's ruling A: "per-user visibility is derived from repo
-- access, never maintained in a table. GitHub is the check"). This file declares what the repos
-- ARE. Migration 70 records what the repo host SAID about a human and the set, with a date.
--
-- NOT a tenancy boundary inside the application. There is one product, self-hosted, on the
-- customer's own subdomain (ruling 5a). This table lets one instance carry several repo sets;
-- it isolates nothing and is not asked to.
--
-- ================================================================= the two triggers
--
-- Declaring a workspace and adding a repo to its set are HUMAN acts, and the name recorded is the
-- database's answer about the connection, never a string the caller chose (IDENTITY-POLICY rule
-- A3, and the shape migrations 20, 36 and 37 already hold). `declared_by` and `added_by` must
-- equal brain.current_human(); a connection that is not a human login is refused; a caller that
-- names somebody else is refused. Both refusals are watched refusing in the self-check below.
--
-- ================================================================= rollback, executed
--
--     BEGIN;
--       DROP TRIGGER IF EXISTS workspace_repo_is_added_by_a_human ON brain.workspace_repo;
--       DROP TRIGGER IF EXISTS workspace_is_declared_by_a_human ON brain.workspace;
--       DROP FUNCTION IF EXISTS brain.workspace_repo_is_added_by_a_human();
--       DROP FUNCTION IF EXISTS brain.workspace_is_declared_by_a_human();
--       DROP TABLE IF EXISTS brain.workspace_repo;
--       DROP TABLE IF EXISTS brain.workspace;
--       DELETE FROM brain.schema_migration WHERE version = 69;
--     COMMIT;
--
-- TWO-WAY, WITH A STATED LOSS. The DROP TABLE lines destroy every declaration and every repo-set
-- row. They are recoverable by re-declaring, which is a human act at a keyboard and not a store
-- restore, and on the day this is applied there are zero rows to lose. Migration 70 must be rolled
-- back FIRST: its tables reference these. Executed and re-applied on ios_term5_scratch on
-- 2026-09-09; the run is quoted in the seat's handoff.

\set ON_ERROR_STOP on

BEGIN;

-- ---------------------------------------------------------------- the tenancy unit

CREATE TABLE IF NOT EXISTS brain.workspace (
  workspace    text        PRIMARY KEY,
  repo_host    text        NOT NULL DEFAULT 'github',
  declared_at  timestamptz NOT NULL DEFAULT now(),
  declared_by  text        NOT NULL,
  CONSTRAINT workspace_is_a_slug_ck CHECK (workspace ~ '^[a-z0-9][a-z0-9-]{0,62}$'),
  CONSTRAINT workspace_repo_host_ck CHECK (repo_host ~ '^[a-z0-9][a-z0-9-]{0,31}$')
);

COMMENT ON TABLE brain.workspace IS
  'The tenancy unit: a declared set of repos (brain.workspace_repo) plus the people whose '
  'repo-host identity can reach them (derived, migration 70). The `workspace` string is the same '
  'one migration 63 put on the five authority tables; declaring it here is what turns that string '
  'from a label into a boundary with a member list nobody maintains by hand. One row on the day '
  'this lands, and the schema does not know that.';

COMMENT ON COLUMN brain.workspace.repo_host IS
  'Which repo host holds this workspace''s repos. `github` today and a hard dependency by ruling '
  '5d; a column rather than an assumption so that a second host is a row and not a rewrite. Every '
  'repo-host call goes behind one adapter, and this is the value that adapter is chosen by.';

COMMENT ON COLUMN brain.workspace.declared_by IS
  'The human who declared it, as the DATABASE names this connection (brain.current_human()). '
  'Checked by trigger, never recorded from a string the caller chose.';

-- ---------------------------------------------------------------- the repo set

CREATE TABLE IF NOT EXISTS brain.workspace_repo (
  workspace      text        NOT NULL REFERENCES brain.workspace (workspace),
  repo_host      text        NOT NULL DEFAULT 'github',
  repo_full_name text        NOT NULL,
  added_at       timestamptz NOT NULL DEFAULT now(),
  added_by       text        NOT NULL,
  PRIMARY KEY (workspace, repo_host, repo_full_name),
  CONSTRAINT workspace_repo_full_name_ck CHECK (repo_full_name ~ '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')
);

COMMENT ON TABLE brain.workspace_repo IS
  'The declared repo set of a workspace, modelled on repo-registry/ one layer down: one row per '
  'repo, named the way the host names it (owner/name). Reach is measured against THIS set: a '
  'human reaches the workspace when the repo host says they reach every row here (migration '
  '70). An empty set is reachable by nobody, on purpose: nothing to reach is not everything.';

-- ---------------------------------------------------------------- the gates

CREATE OR REPLACE FUNCTION brain.workspace_is_declared_by_a_human() RETURNS trigger
  LANGUAGE plpgsql AS $$
DECLARE who text;
BEGIN
  who := brain.current_human();
  IF who IS NULL THEN
    RAISE EXCEPTION 'refusing to declare workspace %: this connection is %, which is not a human '
                    'login', NEW.workspace, session_user
      USING ERRCODE = 'insufficient_privilege',
            HINT = 'A workspace is a tenancy boundary and declaring one is a human act. An agent '
                   'holding the brain_runtime credential has no route to this row and is not '
                   'supposed to. Connect as a login mapped in brain.human_role.';
  END IF;
  IF NEW.declared_by IS DISTINCT FROM who THEN
    RAISE EXCEPTION 'refusing to record % as the declarer of workspace %: this connection is %',
                    NEW.declared_by, NEW.workspace, who
      USING ERRCODE = 'insufficient_privilege',
            HINT = 'declared_by is the database''s answer about this connection, never a name the '
                   'caller chose (IDENTITY-POLICY rule A3).';
  END IF;
  RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS workspace_is_declared_by_a_human ON brain.workspace;
CREATE TRIGGER workspace_is_declared_by_a_human
  BEFORE INSERT OR UPDATE ON brain.workspace
  FOR EACH ROW EXECUTE FUNCTION brain.workspace_is_declared_by_a_human();

CREATE OR REPLACE FUNCTION brain.workspace_repo_is_added_by_a_human() RETURNS trigger
  LANGUAGE plpgsql AS $$
DECLARE who text;
BEGIN
  who := brain.current_human();
  IF who IS NULL THEN
    RAISE EXCEPTION 'refusing to add % to workspace %: this connection is %, which is not a human '
                    'login', NEW.repo_full_name, NEW.workspace, session_user
      USING ERRCODE = 'insufficient_privilege',
            HINT = 'The repo set is what reach is measured against, so widening it is a human act.';
  END IF;
  IF NEW.added_by IS DISTINCT FROM who THEN
    RAISE EXCEPTION 'refusing to record % as the adder of % to %: this connection is %',
                    NEW.added_by, NEW.repo_full_name, NEW.workspace, who
      USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF NEW.repo_host IS DISTINCT FROM (SELECT w.repo_host FROM brain.workspace w
                                       WHERE w.workspace = NEW.workspace) THEN
    RAISE EXCEPTION 'refusing to add a % repo to workspace %, whose host is %',
                    NEW.repo_host, NEW.workspace,
                    (SELECT w.repo_host FROM brain.workspace w WHERE w.workspace = NEW.workspace)
      USING ERRCODE = 'check_violation',
            HINT = 'One workspace, one host. A second host is a second workspace.';
  END IF;
  RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS workspace_repo_is_added_by_a_human ON brain.workspace_repo;
CREATE TRIGGER workspace_repo_is_added_by_a_human
  BEFORE INSERT OR UPDATE ON brain.workspace_repo
  FOR EACH ROW EXECUTE FUNCTION brain.workspace_repo_is_added_by_a_human();

-- ---------------------------------------------------------------- grants
--
-- READ is broad, WRITE is to brain_runtime because every human login is a member of it by
-- migration 20's design (membership, not a grant list), and the triggers above are what turn
-- "brain_runtime may INSERT" into "a human login may INSERT". Same shape as migration 37's
-- assignment column. DELETE is granted to nobody but the owner: a declaration is withdrawn by a
-- migration or by the operator at the keyboard, not by a verb.
GRANT SELECT ON brain.workspace, brain.workspace_repo TO brain_runtime, brain_subscriber;
GRANT INSERT, UPDATE ON brain.workspace, brain.workspace_repo TO brain_runtime;

-- ---------------------------------------------------------------- every refusal watched happening
--
-- Runs as the applying superuser, whose brain.current_human() is NULL, so the first refusal is
-- free. For the positive controls the superuser is mapped to a fixture human for the duration of
-- this transaction and unmapped at the end, the way migration 63 mapped brain_runtime.
DO $$
DECLARE
  n int := 0; refusals int := 0; controls int := 0;
  fx text := '_mv69_fixture_human';
BEGIN
  -- 1 refusal: not a human login.
  BEGIN
    INSERT INTO brain.workspace (workspace, declared_by) VALUES ('ws-mv69', 'operator');
    RAISE EXCEPTION 'migration 69: a non-human connection declared a workspace';
  EXCEPTION WHEN insufficient_privilege THEN n := n + 1; refusals := refusals + 1;
  END;

  INSERT INTO brain.human_role (role_name, human, granted_by)
    VALUES (session_user, fx, 'migration 69 fixture');

  -- 2 refusal: a human naming somebody else as the declarer.
  BEGIN
    INSERT INTO brain.workspace (workspace, declared_by) VALUES ('ws-mv69', 'somebody-else');
    RAISE EXCEPTION 'migration 69: declared_by was recorded from the caller''s string';
  EXCEPTION WHEN insufficient_privilege THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 3 control: the human declares it as themselves.
  INSERT INTO brain.workspace (workspace, declared_by) VALUES ('ws-mv69', fx);
  n := n + 1; controls := controls + 1;

  -- 4 refusal: a repo on the wrong host.
  BEGIN
    INSERT INTO brain.workspace_repo (workspace, repo_host, repo_full_name, added_by)
      VALUES ('ws-mv69', 'gitlab', 'org/repo', fx);
    RAISE EXCEPTION 'migration 69: a repo on another host joined a github workspace';
  EXCEPTION WHEN check_violation THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 5 refusal: a repo name that is not owner/name.
  BEGIN
    INSERT INTO brain.workspace_repo (workspace, repo_full_name, added_by)
      VALUES ('ws-mv69', 'not-a-full-name', fx);
    RAISE EXCEPTION 'migration 69: a bare repo name was accepted';
  EXCEPTION WHEN check_violation THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 6 control: a well-formed repo joins the set.
  INSERT INTO brain.workspace_repo (workspace, repo_full_name, added_by)
    VALUES ('ws-mv69', 'starmynd-org/example', fx);
  n := n + 1; controls := controls + 1;

  -- 7 refusal: a workspace string that is not a slug.
  BEGIN
    INSERT INTO brain.workspace (workspace, declared_by) VALUES ('Not A Slug', fx);
    RAISE EXCEPTION 'migration 69: a non-slug workspace was accepted';
  EXCEPTION WHEN check_violation THEN n := n + 1; refusals := refusals + 1;
  END;

  DELETE FROM brain.workspace_repo WHERE workspace = 'ws-mv69';
  DELETE FROM brain.workspace WHERE workspace = 'ws-mv69';
  DELETE FROM brain.human_role WHERE human = fx AND granted_by = 'migration 69 fixture';

  IF n <> 7 OR refusals <> 5 OR controls <> 2 THEN
    RAISE EXCEPTION 'migration 69: expected 7 checks as 5 refusals and 2 controls, ran % as % and %',
      n, refusals, controls;
  END IF;
  RAISE NOTICE 'migration 69: % of 7 checks passed, % refusals watched refusing and % positive '
               'controls. A workspace is declared by a human, as that human, with a repo set on '
               'one host. Fixture rows removed; tables ship EMPTY.', n, refusals, controls;
END $$;

INSERT INTO brain.schema_migration (version, name)
  VALUES (69, '0069_a_workspace_is_a_declared_repo_set')
  ON CONFLICT (version) DO NOTHING;

COMMIT;
