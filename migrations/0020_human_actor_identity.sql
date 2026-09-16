-- migration 20: the operator's own work is a LOGIN, not a flag
--
-- THE HOLE THIS CLOSES. `brain.work_item.actor_type` has no default and is nullable, and no
-- surface set it: `swarm post` had fifteen flags and none of them named an actor, and
-- `queue classify` sets the membrane fields and not this one. So every item posted through any
-- CLI landed `actor_type = NULL`, `brain.queue_open`'s second arm
-- (`WHERE w.actor_type = 'human'`) matched nothing the operator had posted, and the console's
-- own guard at web/rooms.py then correctly refused `done` on all of it. Measured 2026-08-17,
-- task 0267: the operator asked to load his real day into the console and there was no door.
--
-- WHY THE OBVIOUS FIX IS THE WRONG ONE. The obvious fix is `swarm post --actor-type human`.
-- That hands every agent in the fleet the ability to manufacture a row the console will let a
-- human `done` -- agent work laundered into the human partition. It is the same forgery the
-- `done` prohibition exists to stop, arriving from the other side, and it would be worse than
-- the gap because the gap at least refuses everybody.
--
-- SO HUMAN-NESS IS ESTABLISHED, NOT ASSERTED. This file is migration 10 applied to a second
-- question. There, a listener's identity was `current_setting('brain.subscriber')` -- a string
-- the client sends -- and one listener used it to move another's cursor from 10 to 4242. The
-- repair was three parts, and all three are repeated here:
--
--   1. `brain.human_role` -- a mapping from a database LOGIN ROLE to the human it is. No role
--      but `brain_owner` may read it and nobody at all may write it. It is filled by
--      `store/bin/provision-operator.sh`, which is also the only thing that mints the role's
--      password, because a committed file must never carry a value.
--
--   2. `brain.current_human()` -- `session_user` in, human name out, NULL for a role with no
--      mapping. `session_user` is fixed at authentication and no SQL a client can send changes
--      it. `SET ROLE` changes `current_user` and not this.
--
--   3. `work_item_human_actor_is_a_login` -- a trigger that refuses to let a row BECOME
--      `actor_type = 'human'` in a session that is not one. `brain_runtime` is the role every
--      agent surface connects as; it has no mapping row, it cannot read the table that would
--      give it one, and it cannot write one. So the refusal is the database's, not the
--      application's, and it holds for a hand-written INSERT as much as for `store.apply`.
--
-- WHAT THE TRIGGER DELIBERATELY DOES NOT DO. It fires only on the TRANSITION INTO human: an
-- INSERT that lands `'human'`, or an UPDATE that changes some other value to `'human'`. Ordinary
-- work on a row that is ALREADY the operator's -- the console's `done`, a `queue defer`, a rank
-- recompute -- runs as `brain_runtime` and must keep working. Establishing the attribute is the
-- privileged act; living with it afterwards is not.
--
-- THE RESIDUAL, STATED RATHER THAN HIDDEN. This is the same residual `store/SECRETS.md` already
-- records for listeners: on a `local-attended` host every credential file sits in one `0700`
-- directory under one UID, so an OS process that can read the operator's secret file can connect
-- as the operator. The database no longer takes a client's word about who it is; file
-- permissions are what separate the operator from an agent running as the same user. What is
-- closed absolutely is the case that actually exists -- an agent process holding the
-- `brain_runtime` credential, which is what every agent surface is configured with, cannot
-- produce this row by any route, and `queue/tests/test_human_actor_identity.py` proves it by
-- trying four of them.
--
-- ADJACENT AND NOT DONE HERE: task 0260 (`accepted_by` has no trigger, so an agent can forge an
-- acceptance directly in the column) wants exactly parts 1 and 2 of this file and a trigger of
-- its own shape. They are built here as a shared primitive for that reason. See the COMMENT on
-- brain.current_human().
--
-- APPLYING THIS IS TWO STEPS, NOT ONE, for the same reason migration 10 was. This file creates
-- no login and mints no password: a committed artifact carries references, never values. After
-- it lands, in the same maintenance window:
--
--     store/bin/provision-operator.sh --db brain
--
-- Until that runs there is no human role at all, so `actor_type = 'human'` is refused to
-- everybody including the operator. That is the fail-closed direction.

\set ON_ERROR_STOP on

BEGIN;

-- ---------------------------------------------------------------- the mapping

CREATE TABLE IF NOT EXISTS brain.human_role (
  role_name    name PRIMARY KEY,
  human        text NOT NULL UNIQUE,
  granted_at   timestamptz NOT NULL DEFAULT now(),
  granted_by   text NOT NULL DEFAULT '',
  produced_by  text
);

COMMENT ON TABLE brain.human_role IS
  'One login role, one human. The runtime role cannot read this table and cannot write it, which '
  'is the entire mechanism: the thing that says you are a human is not reachable from where an '
  'agent is. Filled by store/bin/provision-operator.sh over the bootstrap superuser connection.';

-- No grants. brain_owner reads it through the default privileges migration 2 set; the runtime,
-- producer and subscriber roles get nothing, and the REVOKE is written out so an auditor sees
-- the intent rather than inferring it from an absence.
REVOKE ALL ON brain.human_role FROM PUBLIC;
REVOKE ALL ON brain.human_role FROM brain_runtime, brain_producer, brain_subscriber;
GRANT SELECT ON brain.human_role TO brain_owner;

-- ---------------------------------------------------------------- who am I

CREATE OR REPLACE FUNCTION brain.current_human() RETURNS text
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = brain, pg_temp AS $$
  SELECT human FROM brain.human_role WHERE role_name = session_user
$$;

ALTER FUNCTION brain.current_human() OWNER TO brain_owner;

-- SECURITY DEFINER because the caller must NOT be able to read the mapping table: it answers
-- exactly one question, "is this connection a human, and which one", and returns nothing about
-- anybody else. EXECUTE is public because the answer is already public to the caller -- it is
-- the caller.
GRANT EXECUTE ON FUNCTION brain.current_human() TO PUBLIC;

COMMENT ON FUNCTION brain.current_human() IS
  'The human this connection IS, from session_user, or NULL. The one place any gate should ask '
  'that question. Task 0260 (forged brain.work_item.accepted_by) needs the same answer and must '
  'call this rather than build a second notion of who is human, or the two drift and the weaker '
  'one becomes the real policy.';

-- ---------------------------------------------------------------- the gate

CREATE OR REPLACE FUNCTION brain.work_item_human_actor_is_a_login() RETURNS trigger
  LANGUAGE plpgsql AS $$
BEGIN
  -- Only the transition INTO human is privileged. Everything else -- an agent row, a NULL, a
  -- row that was already the operator's and is now being marked done -- returns untouched.
  IF NEW.actor_type IS DISTINCT FROM 'human' THEN
    RETURN NEW;
  END IF;
  IF TG_OP = 'UPDATE' AND OLD.actor_type IS NOT DISTINCT FROM 'human' THEN
    RETURN NEW;
  END IF;
  IF brain.current_human() IS NOT NULL THEN
    RETURN NEW;
  END IF;

  RAISE EXCEPTION 'refusing to mark work item % as actor_type=human: this connection is %, '
                  'which is not a human login',
                  COALESCE(NEW.id, '(new)'), session_user
    USING HINT = 'actor_type=human is what lets the console `done` a row, and an agent that '
                 'could set it could hand its own work to the operator to sign off -- the '
                 'forgery web/rooms.py refuses, arriving from the other side. It is therefore '
                 'established by WHICH LOGIN writes the row, not by a flag anyone may pass. '
                 'The operator''s surfaces connect as a role mapped in brain.human_role; '
                 'provision one with store/bin/provision-operator.sh. An agent holding the '
                 'brain_runtime credential has no route to this row and is not supposed to.',
        ERRCODE = 'insufficient_privilege';
END $$;

COMMENT ON FUNCTION brain.work_item_human_actor_is_a_login() IS
  'The human/agent partition, enforced where it is computable. web/rooms.py reads actor_type to '
  'decide whether `done` from the console is legitimate; this is what stops the column being '
  'writable by the party the guard exists to constrain.';

DROP TRIGGER IF EXISTS work_item_human_actor_is_a_login ON brain.work_item;
CREATE TRIGGER work_item_human_actor_is_a_login
  BEFORE INSERT OR UPDATE ON brain.work_item
  FOR EACH ROW
  EXECUTE FUNCTION brain.work_item_human_actor_is_a_login();

-- Not `BEFORE UPDATE OF actor_type`. That form fires on the columns an UPDATE NAMES, so a
-- statement that sets every column from a record -- which is what a restore-shaped repair or a
-- future ORM would emit -- can carry actor_type without naming it in a way the narrow form
-- catches. The unconditional trigger returns on its first line for every row that is not
-- becoming human, which is nearly all of them.

-- ---------------------------------------------------------------- provisioning, minus the secret

CREATE OR REPLACE FUNCTION brain.provision_human_role(p_role name, p_human text)
  RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = brain, pg_temp AS $$
BEGIN
  IF p_role !~ '^brain_operator$' AND p_role !~ '^brain_human_[a-z0-9_]+$' THEN
    RAISE EXCEPTION 'human role % must be brain_operator or brain_human_<slug>', p_role;
  END IF;
  IF p_human !~ '^[a-z0-9][a-z0-9-]*$' THEN
    RAISE EXCEPTION 'human % is not a slug', p_human;
  END IF;

  -- MEMBERSHIP, NOT A GRANT LIST. A human login needs every privilege the app role has plus one
  -- attribute it must never have, so it is granted `brain_runtime` rather than a copy of
  -- migration 2's table list. A copied list drifts from the schema the day a lane adds a table,
  -- and the drift shows up as the operator's console failing on one verb at a time.
  EXECUTE format('GRANT CONNECT ON DATABASE %I TO %I', current_database(), p_role);
  EXECUTE format('GRANT brain_runtime TO %I', p_role);

  -- The same bound migration 12 sets on the four named roles. It cannot name this one: it is
  -- created after that migration ran. `store/session.py` also sets it per connection, and
  -- neither is redundant -- this one covers a psql session that never opens Python.
  EXECUTE format('ALTER ROLE %I IN DATABASE %I SET statement_timeout = %L',
                 p_role, current_database(), '15s');

  INSERT INTO brain.human_role (role_name, human, granted_by)
       VALUES (p_role, p_human, session_user)
  ON CONFLICT (role_name) DO UPDATE SET human      = EXCLUDED.human,
                                        granted_at = now(),
                                        granted_by = EXCLUDED.granted_by;
  RETURN format('%s is %s', p_role, p_human);
END $$;

-- SUPERUSER ONLY, for two reasons rather than one: this function runs GRANT, so EXECUTE on it is
-- EXECUTE on the grant list, AND an INSERT into brain.human_role is the act of becoming human.
-- A role that could call this could make itself the operator, which is the whole defect.
REVOKE EXECUTE ON FUNCTION brain.provision_human_role(name, text) FROM PUBLIC;

INSERT INTO brain.schema_migration (version, name) VALUES (20, '0020_human_actor_identity')
  ON CONFLICT (version) DO NOTHING;

COMMIT;
