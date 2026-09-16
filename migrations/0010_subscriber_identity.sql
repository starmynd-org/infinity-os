-- migration 10: a subscriber's identity is its database role, and a cursor's history is a column
--
-- D1 was right that "its own cursor row" is not expressible as a grant and needs RLS. The
-- implementation trusted the wrong thing. `subscriber_own_row` keyed on
-- `current_setting('brain.subscriber')`, which is a CLIENT-SETTABLE GUC, and every listener shared
-- the one `brain_subscriber` login. Measured on 2026-08-16 (task 0119, item 6), as brain_subscriber:
--
--     SELECT set_config('brain.subscriber','n8n-bridge',false);
--     UPDATE brain.subscriber_cursor SET last_seq = 4242 WHERE subscriber='n8n-bridge';
--     -- rowcount 1; n8n-bridge's cursor went 10 -> 4242
--
-- The policy stopped a listener that forgot to identify itself. It provided no isolation between
-- listeners, because identity was self-asserted. If a subscriber can name itself, it has no
-- identity.
--
-- This migration replaces the self-assertion with three things the client cannot set:
--
--   1. `brain.subscriber_role` -- a mapping from a database login role to the one subscriber it
--      is. No role but `brain_owner` may read it and nobody at all may write it. It is filled by
--      `store/bin/provision-subscriber.sh`, which is also the only thing that mints the role's
--      password, because a committed file must never carry a value.
--
--   2. `brain.current_subscriber()` -- `session_user` in, subscriber name out, NULL for a role
--      with no mapping. `session_user` is fixed at authentication and no SQL a client can send
--      changes it. `SET ROLE` changes `current_user` and not this.
--
--   3. `subscriber_cursor.high_water_seq` -- the furthest this cursor has EVER been, maintained by
--      a trigger. `event ack` already takes GREATEST, so a cursor that has moved backwards is a
--      cursor somebody moved around the verb, and that fact now survives in the row instead of
--      being erased by the write that caused it.
--
-- AFTER THIS MIGRATION, `brain.subscriber` THE GUC AUTHORISES NOTHING. Setting it is a no-op.
-- The shared `brain_subscriber` login still connects and still reads `event`; it maps to no
-- subscriber, so it reads and writes ZERO cursor rows. That is the fail-closed direction.
--
-- APPLYING THIS IS TWO STEPS, NOT ONE. A subscriber with no row in `brain.subscriber_role` cannot
-- advance its own cursor, on purpose. Run, in the same maintenance window, for every live listener:
--
--     store/bin/provision-subscriber.sh --db brain --subscriber operator-paging
--
-- A listener left unprovisioned fails closed and says so; it does not quietly fall back to the
-- shared credential, because a fallback to the shared credential is this whole defect wearing a
-- deprecation notice.

\set ON_ERROR_STOP on

BEGIN;

-- ---------------------------------------------------------------- the mapping

CREATE TABLE IF NOT EXISTS brain.subscriber_role (
  role_name    name PRIMARY KEY,
  subscriber   text NOT NULL UNIQUE,
  granted_at   timestamptz NOT NULL DEFAULT now(),
  granted_by   text NOT NULL DEFAULT '',
  produced_by  text
);

COMMENT ON TABLE brain.subscriber_role IS
  'One login role, one subscriber. The subscriber cannot read this table and cannot write it, '
  'which is the entire difference between this and the GUC it replaces: the thing that says who '
  'you are is not reachable from where you are.';

-- No grants. brain_owner reads it through the default privileges migration 2 set; the subscriber
-- and runtime roles get nothing, and the REVOKE is written out so an auditor sees the intent
-- rather than inferring it from an absence.
REVOKE ALL ON brain.subscriber_role FROM PUBLIC;
REVOKE ALL ON brain.subscriber_role FROM brain_subscriber, brain_producer, brain_runtime;
GRANT SELECT ON brain.subscriber_role TO brain_owner;

-- ---------------------------------------------------------------- who am I

CREATE OR REPLACE FUNCTION brain.current_subscriber() RETURNS text
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = brain, pg_temp AS $$
  SELECT subscriber FROM brain.subscriber_role WHERE role_name = session_user
$$;

ALTER FUNCTION brain.current_subscriber() OWNER TO brain_owner;

-- SECURITY DEFINER because the caller must NOT be able to read the mapping table: it answers
-- exactly one question, "who is this connection", and returns nothing about anybody else. EXECUTE
-- is public because the answer is already public to the caller -- it is the caller.
GRANT EXECUTE ON FUNCTION brain.current_subscriber() TO PUBLIC;

COMMENT ON FUNCTION brain.current_subscriber() IS
  'The subscriber this connection IS, from session_user. NULL for an unmapped role, and NULL '
  'matches no row in the policy below, so an unprovisioned or shared login reads and writes '
  'nothing rather than everything.';

-- ---------------------------------------------------------------- the policy that replaces it

DROP POLICY IF EXISTS subscriber_own_row ON brain.subscriber_cursor;
CREATE POLICY subscriber_own_row ON brain.subscriber_cursor
  FOR ALL
  USING      (subscriber = (SELECT brain.current_subscriber()))
  WITH CHECK (subscriber = (SELECT brain.current_subscriber()));

COMMENT ON POLICY subscriber_own_row ON brain.subscriber_cursor IS
  'Keyed on the database role through brain.subscriber_role, never on a setting the client sends. '
  'The policy is TO PUBLIC rather than TO brain_subscriber because each listener now logs in '
  'under its own role, and a role the policy does not name would be a role the policy does not '
  'constrain.';

-- ---------------------------------------------------------------- the cursor's own history

ALTER TABLE brain.subscriber_cursor
  ADD COLUMN IF NOT EXISTS high_water_seq bigint NOT NULL DEFAULT 0;

UPDATE brain.subscriber_cursor SET high_water_seq = GREATEST(high_water_seq, last_seq);

CREATE OR REPLACE FUNCTION brain.subscriber_cursor_high_water() RETURNS trigger
  LANGUAGE plpgsql AS $$
BEGIN
  NEW.high_water_seq := GREATEST(COALESCE(NEW.high_water_seq, 0),
                                 COALESCE(NEW.last_seq, 0),
                                 COALESCE(OLD.high_water_seq, 0),
                                 COALESCE(OLD.last_seq, 0));
  RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS subscriber_cursor_high_water ON brain.subscriber_cursor;
CREATE TRIGGER subscriber_cursor_high_water
  BEFORE INSERT OR UPDATE ON brain.subscriber_cursor
  FOR EACH ROW EXECUTE FUNCTION brain.subscriber_cursor_high_water();

COMMENT ON COLUMN brain.subscriber_cursor.high_water_seq IS
  'The furthest this cursor has ever been. It never decreases. `event ack` takes GREATEST, so '
  'last_seq < high_water_seq means the cursor was moved by something other than the verb, and '
  'that is a finding rather than a reading.';

-- ---------------------------------------------------------------- the lag view tells the truth

-- `security_invoker` so RLS on the base table reaches the reader. Before this, one subscriber
-- SELECTed brain.subscriber_lag and got EVERY listener's position (0119 item 6, minor third),
-- because a view without it runs as its definer and the policy never applied.
--
-- The three impossible conditions are computed HERE, next to the subtraction that produces them,
-- so every reader of the lag signal sees them and not only the one module that remembered to
-- check. Columns are appended, never reordered: CREATE OR REPLACE VIEW requires it.
CREATE OR REPLACE VIEW brain.subscriber_lag WITH (security_invoker = true) AS
  SELECT c.subscriber,
         c.last_seq,
         COALESCE((SELECT max(event_seq) FROM brain.event), 0) AS head_seq,
         COALESCE((SELECT max(event_seq) FROM brain.event), 0) - c.last_seq AS lag,
         c.quarantined_at IS NOT NULL AS quarantined,
         c.updated_at,
         c.high_water_seq,
         c.quarantine_reason,
         -- a cursor past the head of the bus: it has "seen" events that do not exist
         c.last_seq > COALESCE((SELECT max(event_seq) FROM brain.event), 0) AS ahead_of_head,
         -- a cursor that went backwards: something wrote it outside `event ack`
         c.last_seq < c.high_water_seq                                      AS moved_backwards
    FROM brain.subscriber_cursor c;

COMMENT ON VIEW brain.subscriber_lag IS
  'The health signal, and its own corruption checks. A negative lag is impossible; ahead_of_head '
  'and moved_backwards say which impossibility. security_invoker is load-bearing: without it this '
  'view hands every listener every other listener''s cursor.';

-- ---------------------------------------------------------------- provisioning, minus the secret

CREATE OR REPLACE FUNCTION brain.provision_subscriber_role(p_role name, p_subscriber text)
  RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = brain, pg_temp AS $$
BEGIN
  IF p_role !~ '^brain_sub_[a-z0-9_]+$' THEN
    RAISE EXCEPTION 'subscriber role % must be named brain_sub_<slug>', p_role;
  END IF;
  IF p_subscriber !~ '^[a-z0-9][a-z0-9-]*$' THEN
    RAISE EXCEPTION 'subscriber % is not a slug', p_subscriber;
  END IF;

  -- The grant list lives HERE, in the migration, and not in the shell script that calls it. A
  -- grant list in a script drifts from the schema it grants on, and the drift is invisible until
  -- a listener cannot read the bus at three in the morning.
  EXECUTE format('GRANT CONNECT ON DATABASE %I TO %I', current_database(), p_role);
  EXECUTE format('GRANT USAGE  ON SCHEMA brain TO %I', p_role);
  EXECUTE format('GRANT SELECT ON brain.event TO %I', p_role);
  EXECUTE format('GRANT SELECT ON brain.subscriber_lag TO %I', p_role);
  EXECUTE format('GRANT SELECT, INSERT, UPDATE ON brain.subscriber_cursor TO %I', p_role);
  EXECUTE format('GRANT EXECUTE ON FUNCTION brain.signal_level(text, text) TO %I', p_role);

  -- The same bound migration 12 sets on the four named roles. It cannot name these: they are
  -- created after it runs, one per listener. `store/session.py` also sets it per connection, and
  -- neither is redundant -- this one covers a psql session that never opens Python.
  EXECUTE format('ALTER ROLE %I IN DATABASE %I SET statement_timeout = %L',
                 p_role, current_database(), '60s');

  INSERT INTO brain.subscriber_role (role_name, subscriber, granted_by)
       VALUES (p_role, p_subscriber, session_user)
  ON CONFLICT (role_name) DO UPDATE SET subscriber = EXCLUDED.subscriber,
                                        granted_at = now(),
                                        granted_by = EXCLUDED.granted_by;
  RETURN format('%s is %s', p_role, p_subscriber);
END
$$;

-- SUPERUSER ONLY. This function runs GRANT, so EXECUTE on it is EXECUTE on the grant list. It is
-- called by store/bin/provision-subscriber.sh over the bootstrap superuser connection, the same
-- one that applies this file, and by nothing else.
REVOKE EXECUTE ON FUNCTION brain.provision_subscriber_role(name, text) FROM PUBLIC;

INSERT INTO brain.schema_migration (version, name) VALUES (10, '0010_subscriber_identity')
  ON CONFLICT (version) DO NOTHING;

COMMIT;
