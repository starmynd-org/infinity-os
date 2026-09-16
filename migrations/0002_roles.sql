-- migration 2: the four roles
--
-- A gate in listener code is bypassable by a bug or a wrong branch. A gate as a database grant
-- is not bypassable by application logic at all. That is the whole argument for this file.
--
-- Passwords are NOT here. This file is committed; the values are not. Each role's password is
-- bound at apply time from a `secret_ref` (see store/SECRETS.md) via psql variables, so the
-- committed artifact contains a reference and never a value.
--
-- Run with:
--   psql -v owner_pw="$(resolve brain-postgres-role-owner)" ... -f 0002_roles.sql
--
-- NONE of these four is a superuser. The bootstrap superuser is `postgres` and it is used only
-- to apply migrations. An `owner` that was also the initdb superuser would bypass every grant
-- below and the least-privilege split would be decorative.

\set ON_ERROR_STOP on

BEGIN;

-- ---------------------------------------------------------------- role creation

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'brain_owner') THEN
    CREATE ROLE brain_owner LOGIN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'brain_producer') THEN
    CREATE ROLE brain_producer LOGIN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'brain_subscriber') THEN
    CREATE ROLE brain_subscriber LOGIN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'brain_runtime') THEN
    CREATE ROLE brain_runtime LOGIN;
  END IF;
END $$;

ALTER ROLE brain_owner      WITH PASSWORD :'owner_pw'      NOSUPERUSER NOCREATEDB NOCREATEROLE;
ALTER ROLE brain_producer   WITH PASSWORD :'producer_pw'   NOSUPERUSER NOCREATEDB NOCREATEROLE;
ALTER ROLE brain_subscriber WITH PASSWORD :'subscriber_pw' NOSUPERUSER NOCREATEDB NOCREATEROLE;
ALTER ROLE brain_runtime    WITH PASSWORD :'runtime_pw'    NOSUPERUSER NOCREATEDB NOCREATEROLE;

-- Nobody gets the PUBLIC schema's default create right, and nobody connects to a database they
-- were not granted. Postgres 15+ already revokes CREATE on public; this is belt and braces.
REVOKE ALL ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON DATABASE brain FROM PUBLIC;
GRANT CONNECT ON DATABASE brain TO brain_owner, brain_producer, brain_subscriber, brain_runtime;

-- ---------------------------------------------------------------- owner
--
-- DDL, the retention sweep, the rollup. Migrations and destructive sweeps are the only paths
-- that delete a row, and both of them are this role.

GRANT USAGE, CREATE ON SCHEMA brain TO brain_owner;
GRANT ALL PRIVILEGES ON ALL TABLES    IN SCHEMA brain TO brain_owner;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA brain TO brain_owner;
GRANT EXECUTE ON ALL FUNCTIONS        IN SCHEMA brain TO brain_owner;
ALTER DEFAULT PRIVILEGES IN SCHEMA brain GRANT ALL ON TABLES    TO brain_owner;
ALTER DEFAULT PRIVILEGES IN SCHEMA brain GRANT ALL ON SEQUENCES TO brain_owner;

-- ---------------------------------------------------------------- producer
--
-- INSERT on `event` ONLY. NO SELECT. A compromised producer cannot enumerate other departments'
-- occurrences: it can add to the bus and it cannot read the bus.
--
-- INSERT on a table with a bigserial needs USAGE on that sequence, and nothing else.

GRANT USAGE ON SCHEMA brain TO brain_producer;
GRANT INSERT ON brain.event TO brain_producer;
GRANT USAGE  ON SEQUENCE brain.event_event_seq_seq TO brain_producer;

-- ---------------------------------------------------------------- subscriber
--
-- THE SAFETY-BEARING ROLE. SELECT on `event`, LISTEN, and SELECT/INSERT/UPDATE on
-- subscriber_cursor -- restricted to its OWN row by the RLS policy below.
--
-- NO WRITE PATH TO `event` AT ALL. Not INSERT, not UPDATE, not DELETE. A subscriber whose own
-- gate logic is wrong still cannot write to the bus, and the attempt raises a permission error
-- that is loud rather than silent. This is the grant that does not collapse under simplification.
--
-- LISTEN needs no grant in Postgres; it is available to any role that can connect. Stated here
-- so a reader does not go looking for a GRANT LISTEN that does not exist.

GRANT USAGE  ON SCHEMA brain TO brain_subscriber;
GRANT SELECT ON brain.event TO brain_subscriber;
GRANT SELECT ON brain.subscriber_lag TO brain_subscriber;
GRANT SELECT, INSERT, UPDATE ON brain.subscriber_cursor TO brain_subscriber;

-- "its OWN subscriber_cursor row" is a real restriction, not a convention. Without RLS the grant
-- above lets any subscriber advance any other subscriber's cursor, which silently skips events
-- for a listener that is working correctly -- the hardest possible failure to see.
ALTER TABLE brain.subscriber_cursor ENABLE ROW LEVEL SECURITY;
ALTER TABLE brain.subscriber_cursor FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS subscriber_own_row ON brain.subscriber_cursor;
CREATE POLICY subscriber_own_row ON brain.subscriber_cursor
  FOR ALL TO brain_subscriber
  USING      (subscriber = current_setting('brain.subscriber', true))
  WITH CHECK (subscriber = current_setting('brain.subscriber', true));

-- owner and runtime read and administer every cursor; they are not the constrained party.
DROP POLICY IF EXISTS cursor_admin ON brain.subscriber_cursor;
CREATE POLICY cursor_admin ON brain.subscriber_cursor
  FOR ALL TO brain_owner, brain_runtime USING (true) WITH CHECK (true);

COMMENT ON POLICY subscriber_own_row ON brain.subscriber_cursor IS
  'Each listener sets brain.subscriber to its own name at connect. An unset setting matches no '
  'row, so a listener that forgot to identify itself reads and writes nothing rather than '
  'everything.';

-- ---------------------------------------------------------------- runtime
--
-- The app role. DML on the work, session and queue tables. Explicitly NOT on `event`: an event
-- producer calls `event emit`, which connects as `brain_producer`. That is what keeps the
-- producer role's INSERT-only grant meaningful rather than decorative -- if runtime could insert
-- events too, the asymmetry would exist on paper only.
--
-- DELETE is granted NOWHERE below. Migrations and the retention sweep are the only paths that
-- delete a row, and both are `owner`.

GRANT USAGE ON SCHEMA brain TO brain_runtime;

GRANT SELECT, INSERT, UPDATE ON
  brain.work_item, brain.run, brain.question, brain.thread, brain.message,
  brain.artifact, brain.agent, brain.objective, brain.session, brain.transcript,
  brain.observation, brain.disposition, brain.recommendation, brain.receipt,
  brain.touch, brain.runtime_flag, brain.subscriber_cursor, brain.event_rollup
  TO brain_runtime;

-- read-only on the ledger's source of truth
GRANT SELECT ON brain.event TO brain_runtime;
GRANT SELECT ON brain.schema_migration TO brain_runtime;
GRANT SELECT ON brain.feed, brain.work_item_signals, brain.auto_accept_candidate,
                brain.subscriber_lag TO brain_runtime;

GRANT USAGE ON ALL SEQUENCES IN SCHEMA brain TO brain_runtime;
GRANT EXECUTE ON FUNCTION brain.signal_level(text, text)  TO brain_runtime, brain_subscriber;
GRANT EXECUTE ON FUNCTION brain.signal_ok(text, text)     TO brain_runtime;
GRANT EXECUTE ON FUNCTION brain.next_item_id()            TO brain_runtime;
GRANT EXECUTE ON FUNCTION brain.next_question_id()        TO brain_runtime;

-- The sweep deletes, so only owner may execute it. REVOKE from PUBLIC is required: a plain
-- CREATE FUNCTION grants EXECUTE to PUBLIC by default, which would hand every role the one
-- operation this schema reserves.
REVOKE EXECUTE ON FUNCTION brain.sweep_events()  FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION brain.sweep_rollups() FROM PUBLIC;
GRANT  EXECUTE ON FUNCTION brain.sweep_events()  TO brain_owner;
GRANT  EXECUTE ON FUNCTION brain.sweep_rollups() TO brain_owner;

INSERT INTO brain.schema_migration (version, name) VALUES (2, '0002_roles')
  ON CONFLICT (version) DO NOTHING;

COMMIT;
