-- migration 40: config gets a home in the store, and every change to it gets a name and a time
--
-- THE GAP THIS CLOSES, measured rather than asserted. `docs/CONFIG-PROVENANCE.md` (task 0273)
-- recorded it in three checked facts and then built only the read half:
--
--   1. Nothing in this codebase writes `~/.brain-runtime/config.json`. `engine/swarm_engine/
--      config.py` reads it and has no write path, so every change to it is a human editing a
--      file by hand.
--   2. `~/.brain-runtime` is not a git repository. There is no history to read.
--   3. On 2026-08-18 that file changed three times in one day (model, lanes, agent set) with no
--      record of who, when or why, and a lane had to open an investigation (task 0215, q0217)
--      to establish an answer a `set_by` column would have carried for free.
--
-- The fingerprint task 0273 shipped tells a reader THAT the file changed. It cannot tell them
-- who or why, because nothing recorded it. This file is the other half: an override layer in the
-- store, written only through `store.apply`, with the attribution taken from the LOGIN rather
-- than from anything the caller passes.
--
-- WHY AN OVERRIDE LAYER AND NOT A REPLACEMENT. `config.py` argues at length that `config.json`
-- stays a file because it resolves `{**defaults, **agent}` and PASSES UNKNOWN KEYS THROUGH, and
-- that tolerance is what let `config_dirs` ship with no CLI change. Moving config into columns
-- would either freeze the key set or reimplement JSON in SQL. So the file keeps its job and this
-- table sits on top of it, exactly as `engine/swarm_engine/fleet_config.py` already does for the
-- console's per-agent picker:
--
--     resolution order:   code default  <  config file  <  store override
--
-- fleet_config's own argument, one scope wider: "A config key is edited by whoever is editing
-- config, which during a build is an agent; a runtime_flag write is a verb call that lands on
-- the thread and is visible in `swarm status`."
--
-- WHY NOT `brain.runtime_flag`, which needs no migration at all. Because it has no history. It
-- is one row per key with ON CONFLICT DO UPDATE, so it holds the CURRENT value and its setter
-- and forgets every earlier one. The measured failure was three edits in one day: a table that
-- keeps only the third answers none of the questions that investigation had to ask.
--
-- THE ATTRIBUTION IS THE LOGIN, NOT THE ARGUMENT. `brain.current_human()` (migration 20) reads
-- `session_user`, which is fixed at authentication and unreachable from SQL. The triggers below
-- OVERWRITE `set_by` and `changed_by` with its answer rather than checking a value the caller
-- supplied, which is the shape `queue/schema/0015_recommendation_human_login.sql` already uses
-- for `decided_by`. A record of who changed config that the changer can spell is not a record.
--
-- WHAT IS DELIBERATELY NOT HERE. No DELETE grant, anywhere, for anybody but the owner: this
-- schema's standing rule is that migrations and the retention sweep are the only paths that
-- delete a row. `config unset` is therefore an UPDATE that clears `active`, and the history row
-- it writes carries `new_value IS NULL`. An unset that vanished from the trail would be the one
-- change nobody could audit.

\set ON_ERROR_STOP on

BEGIN;

-- ---------------------------------------------------------------- the current values

CREATE TABLE IF NOT EXISTS brain.config_setting (
  scope       text        NOT NULL,
  key         text        NOT NULL,
  value       text        NOT NULL,
  active      boolean     NOT NULL DEFAULT true,
  set_at      timestamptz NOT NULL DEFAULT now(),
  set_by      text        NOT NULL DEFAULT '',
  note        text        NOT NULL DEFAULT '',
  PRIMARY KEY (scope, key)
);

COMMENT ON TABLE brain.config_setting IS
  'The store half of the config resolution order (code default < file < this). One row per '
  '(scope, key). scope is ''fleet'' for a fleet-wide key, ''agent:<name>'' for one agent''s, and '
  '''roster'' for an entry in the agent roster whose key is the agent name. value is JSON text, '
  'because the file it overrides is JSON and reimplementing JSON in columns is exactly what '
  'engine/swarm_engine/config.py argues against. active=false is an unset: the row stays so the '
  'trail stays.';

COMMENT ON COLUMN brain.config_setting.set_by IS
  'Written by the trigger from brain.current_human(), never from an argument. A caller cannot '
  'spell this field.';

-- ---------------------------------------------------------------- the trail

CREATE TABLE IF NOT EXISTS brain.admin_change (
  change_seq  bigserial   PRIMARY KEY,
  changed_at  timestamptz NOT NULL DEFAULT now(),
  changed_by  text        NOT NULL DEFAULT '',
  verb        text        NOT NULL,
  scope       text        NOT NULL DEFAULT '',
  key         text        NOT NULL DEFAULT '',
  old_value   text,
  new_value   text,
  note        text        NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS admin_change_scope_key ON brain.admin_change (scope, key, change_seq);
CREATE INDEX IF NOT EXISTS admin_change_at        ON brain.admin_change (changed_at);

COMMENT ON TABLE brain.admin_change IS
  'Append-only. One row per admin verb that changed something, whatever it changed: a config '
  'key, a roster entry, a human login. old_value NULL means the key was not set before; '
  'new_value NULL means it was unset. This is the history brain.runtime_flag cannot keep, and '
  'the reason a runtime_flag row was not enough: it holds the latest answer and forgets the '
  'three edits that made the day interesting.';

-- ---------------------------------------------------------------- the gate

CREATE OR REPLACE FUNCTION brain.config_setting_is_a_human() RETURNS trigger
  LANGUAGE plpgsql AS $$
DECLARE who text;
BEGIN
  who := brain.current_human();
  IF who IS NULL THEN
    RAISE EXCEPTION 'refusing to write config key %.% from this connection: % is not a human '
                    'login', NEW.scope, NEW.key, session_user
      USING HINT = 'A config key decides what model a terminal runs, which lanes it may claim '
                   'from, and how the queue is ordered. An agent that could write one could '
                   're-point the fleet, so the write is established by WHICH LOGIN makes it, '
                   'exactly as migration 20 establishes actor_type=human. The operator''s '
                   'surfaces connect as a role mapped in brain.human_role; provision one with '
                   'store/bin/provision-operator.sh, or call the verb with as_operator=True, '
                   'which opens that login. brain_runtime has no route here and is not '
                   'supposed to.',
          ERRCODE = 'insufficient_privilege';
  END IF;
  -- Not a check on a supplied value: an OVERWRITE. See the header.
  NEW.set_by := who;
  NEW.set_at := now();
  RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS config_setting_is_a_human ON brain.config_setting;
CREATE TRIGGER config_setting_is_a_human
  BEFORE INSERT OR UPDATE ON brain.config_setting
  FOR EACH ROW EXECUTE FUNCTION brain.config_setting_is_a_human();

CREATE OR REPLACE FUNCTION brain.admin_change_is_a_human() RETURNS trigger
  LANGUAGE plpgsql AS $$
DECLARE who text;
BEGIN
  who := brain.current_human();
  IF who IS NULL THEN
    RAISE EXCEPTION 'refusing to write an admin trail row from this connection: % is not a '
                    'human login', session_user
      USING HINT = 'The trail is what makes an admin verb auditable. A row in it that names a '
                   'human the database does not recognise would be worse than no row at all.',
          ERRCODE = 'insufficient_privilege';
  END IF;
  NEW.changed_by := who;
  NEW.changed_at := now();
  RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS admin_change_is_a_human ON brain.admin_change;
CREATE TRIGGER admin_change_is_a_human
  BEFORE INSERT ON brain.admin_change
  FOR EACH ROW EXECUTE FUNCTION brain.admin_change_is_a_human();

-- Append-only as a TRIGGER and not only as a missing grant. The grants below withhold UPDATE and
-- DELETE from every role that is not the owner, and the owner is the role that applies
-- migrations and runs the sweep. A trail the owner can quietly rewrite is a trail whose worst
-- case is the case it exists for, so the refusal is unconditional and the owner is refused too.
-- A retention sweep that ever needs to trim this table drops the trigger in its own migration,
-- which is a reviewable act rather than an accident.
CREATE OR REPLACE FUNCTION brain.admin_change_is_append_only() RETURNS trigger
  LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'brain.admin_change is append-only: % is refused on change_seq %',
                  TG_OP, COALESCE(OLD.change_seq, -1)
    USING HINT = 'Record the correction as a NEW row. An audit trail that can be edited answers '
                 'the question "what happened" with "whatever the last editor wanted".',
        ERRCODE = 'insufficient_privilege';
END $$;

DROP TRIGGER IF EXISTS admin_change_is_append_only ON brain.admin_change;
CREATE TRIGGER admin_change_is_append_only
  BEFORE UPDATE OR DELETE ON brain.admin_change
  FOR EACH ROW EXECUTE FUNCTION brain.admin_change_is_append_only();

-- ---------------------------------------------------------------- grants
--
-- SELECT to the runtime so every surface can READ resolved config with the ordinary app login,
-- which is what keeps `swarm config` working for an agent. INSERT and UPDATE reach only the
-- human logins in practice, because the triggers above refuse a connection brain.current_human()
-- does not recognise, and brain_operator holds these grants by membership in brain_runtime.
--
-- Granting INSERT to brain_runtime and then refusing it in a trigger is deliberate belt and
-- braces in that order: the grant is what lets the operator's login (a MEMBER of brain_runtime)
-- reach the table at all, and the trigger is what stops brain_runtime itself from writing. A
-- grant-only gate would have to name every human role at provisioning time and would drift the
-- first time one was added.

REVOKE ALL ON brain.config_setting FROM PUBLIC;
REVOKE ALL ON brain.admin_change   FROM PUBLIC;

GRANT SELECT, INSERT, UPDATE ON brain.config_setting TO brain_runtime;
GRANT SELECT, INSERT         ON brain.admin_change   TO brain_runtime;
GRANT USAGE ON SEQUENCE brain.admin_change_change_seq_seq TO brain_runtime;

GRANT ALL PRIVILEGES ON brain.config_setting TO brain_owner;
GRANT ALL PRIVILEGES ON brain.admin_change   TO brain_owner;
GRANT USAGE ON SEQUENCE brain.admin_change_change_seq_seq TO brain_owner;

-- ---------------------------------------------------------------- what a reader asks first

CREATE OR REPLACE VIEW brain.config_in_force AS
  SELECT scope, key, value, set_at, set_by, note
    FROM brain.config_setting
   WHERE active
   ORDER BY scope, key;

COMMENT ON VIEW brain.config_in_force IS
  'The store layer only. It is NOT resolved config: the file underneath it is the other half, '
  'and `swarm config` is the surface that folds the two. A reader who takes this view for the '
  'answer will be wrong about every key the file sets and the store does not, which is most of '
  'them.';

GRANT SELECT ON brain.config_in_force TO brain_runtime, brain_owner;

INSERT INTO brain.schema_migration (version, name)
     VALUES (40, '0040_admin_config_provenance')
  ON CONFLICT (version) DO NOTHING;

COMMIT;
