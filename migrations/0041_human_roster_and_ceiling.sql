-- migration 41: the human roster gets a read door, and the twelve-login ceiling gets a floor
--
-- TWO THINGS, and they are one thing. The commander's decision of 2026-08-27 (row 0386,
-- decision 2) is that identity stays ONE POSTGRES LOGIN PER HUMAN, with a stated ceiling of
-- twelve, and that `brain.current_human()` keeps reading `session_user`. A dozen logins is only
-- administrable if the roster can be READ by the surface that administers it and if the ceiling
-- is somewhere a hand cannot quietly walk past.
--
-- WHAT WAS MEASURED, on live `brain` on 2026-08-27, before anything here was written:
--
--     brain_runtime  CANNOT read brain.human_role  (permission denied for table human_role)
--     brain_operator CANNOT read brain.human_role  (permission denied for table human_role)
--     brain_owner    CAN read it, 1 row
--     brain.current_human() = 'operator' as brain_operator, NULL as brain_owner and brain_runtime
--
-- So there is no single login that can both read the roster and pass the human gate. The one
-- login that can read it is not a human, and the one login that is a human cannot read it. An
-- admin surface that lists humans therefore had no door at all, and lane E (multi-user) is built
-- on top of a list it could not obtain.
--
-- WHY A FUNCTION AND NOT A GRANT. Migration 20's sentence is "No role but brain_owner may read
-- it and nobody at all may write it", and the load-bearing half of that is the WRITE. The gate
-- is that an agent cannot put itself in this table, cannot read it to find out it is not in it,
-- and cannot call `brain.provision_human_role` to get in. None of that rests on the list of
-- names being secret: the role names are already derivable (`brain_operator`, and
-- `brain_human_<slug>` by the pattern that function enforces), and knowing a role name buys
-- nothing without its password, which lives in a 0600 file and is in no column here.
--
-- So the read is exposed as a FUNCTION rather than by granting SELECT on the table, for one
-- reason worth the extra object: a function returns the columns it names. `brain.human_role`
-- may later grow a column that is not for every caller, and a grant made today would carry it.
-- `brain.human_roster()` names four columns and a new one does not join them by accident.
--
-- THE CEILING IS A TRIGGER, NOT A CONVENTION. The decision says a thirteenth login reopens the
-- decision and is never a quiet migration. A number written in a planning document is walked
-- past by the person in a hurry, which is the only person who ever provisions a thirteenth
-- login. It is enforced on brain.human_role itself rather than inside the provisioning function,
-- so it also holds for a hand-written INSERT over the bootstrap superuser connection, which is
-- the route a hurry actually takes.

\set ON_ERROR_STOP on

BEGIN;

-- ---------------------------------------------------------------- the ceiling

CREATE OR REPLACE FUNCTION brain.human_login_ceiling() RETURNS integer
  LANGUAGE sql IMMUTABLE AS $$ SELECT 12 $$;

COMMENT ON FUNCTION brain.human_login_ceiling() IS
  'Twelve. The commander''s decision of 2026-08-27, row 0386 decision 2: one Postgres login per '
  'human, ceiling twelve, and a thirteenth REOPENS THE DECISION rather than being a quiet '
  'migration. A function rather than a literal in three places, so raising it is one edit in one '
  'migration that a reviewer can see.';

CREATE OR REPLACE FUNCTION brain.human_role_ceiling() RETURNS trigger
  LANGUAGE plpgsql AS $$
DECLARE n integer; cap integer;
BEGIN
  cap := brain.human_login_ceiling();
  SELECT count(*) INTO n FROM brain.human_role;
  IF n >= cap THEN
    RAISE EXCEPTION 'refusing to map % as a human: brain.human_role already holds % of a stated '
                    'ceiling of %', NEW.role_name, n, cap
      USING HINT = 'This is not a capacity limit, it is a decision boundary. The operator ruled '
                   'on 2026-08-27 (row 0386, decision 2) that identity is one Postgres login per '
                   'human with a ceiling of twelve, and that a thirteenth REOPENS the decision. '
                   'Raising it is a migration that replaces brain.human_login_ceiling(), which '
                   'is a reviewable act. Removing a human who has left is the other answer: '
                   'SELECT brain.revoke_human_role(''brain_human_<slug>'').',
          ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END $$;

-- INSERT only. An UPDATE of an existing mapping (which is what `provision_human_role` does on
-- conflict when a human is re-provisioned) changes no count and must not be refused: refusing it
-- would break re-running the provisioner, which is documented as idempotent.
DROP TRIGGER IF EXISTS human_role_ceiling ON brain.human_role;
CREATE TRIGGER human_role_ceiling
  BEFORE INSERT ON brain.human_role
  FOR EACH ROW EXECUTE FUNCTION brain.human_role_ceiling();

-- ---------------------------------------------------------------- the read door

CREATE OR REPLACE FUNCTION brain.human_roster()
  RETURNS TABLE (role_name name, human text, granted_at timestamptz, granted_by text)
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = brain, pg_temp AS $$
  SELECT role_name, human, granted_at, granted_by FROM brain.human_role ORDER BY human
$$;

ALTER FUNCTION brain.human_roster() OWNER TO brain_owner;

REVOKE EXECUTE ON FUNCTION brain.human_roster() FROM PUBLIC;
GRANT  EXECUTE ON FUNCTION brain.human_roster() TO brain_runtime, brain_owner;

COMMENT ON FUNCTION brain.human_roster() IS
  'Who this database recognises as a human, for the admin surface that administers them. Four '
  'columns, no credential and no reference to one. SECURITY DEFINER because brain.human_role is '
  'owner-only and the login that administers it is the operator, who is not the owner. What '
  'migration 20 protects is the WRITE: an agent still cannot insert itself here, cannot call '
  'brain.provision_human_role, and gains nothing from a name whose password it does not hold.';

-- ---------------------------------------------------------------- taking one away

CREATE OR REPLACE FUNCTION brain.revoke_human_role(p_role name) RETURNS text
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = brain, pg_temp AS $$
DECLARE who text; gone text;
BEGIN
  -- session_user, not current_user: SECURITY DEFINER changes the latter and not the former, so
  -- brain.current_human() still answers about the login that CALLED this, which is the point.
  who := brain.current_human();
  IF who IS NULL THEN
    RAISE EXCEPTION 'refusing to revoke %: this connection (%) is not a human login',
                    p_role, session_user
      USING ERRCODE = 'insufficient_privilege',
            HINT = 'Removing a human is an administrative act and is attributed to the human who '
                   'performs it. Call it through `swarm admin human revoke`, which opens the '
                   'operator login.';
  END IF;

  IF p_role = 'brain_operator' THEN
    RAISE EXCEPTION 'refusing to revoke brain_operator'
      USING ERRCODE = 'insufficient_privilege',
            HINT = 'brain_operator is the login every operator surface writes through and the '
                   'one this system administers itself from: web/actions.py passes '
                   'as_operator=True on every console write, and the admin verbs open it too. '
                   'Revoking it mid-session is a silent outage that also removes the door you '
                   'would use to undo it. If the operator is genuinely leaving, that is a '
                   'migration and a handover, not a verb.';
  END IF;

  DELETE FROM brain.human_role WHERE role_name = p_role RETURNING human INTO gone;
  IF gone IS NULL THEN
    RAISE EXCEPTION 'no human role named %', p_role
      USING ERRCODE = 'no_data_found';
  END IF;
  RETURN gone;
END $$;

ALTER FUNCTION brain.revoke_human_role(name) OWNER TO brain_owner;

-- DELETE on brain.human_role reaches the owner and nobody else, which is this schema's standing
-- rule (migration 2: "DELETE is granted NOWHERE below"). The function above runs as the owner
-- and is the only route to it, and it gates on the CALLER being a human first.
GRANT DELETE ON brain.human_role TO brain_owner;

REVOKE EXECUTE ON FUNCTION brain.revoke_human_role(name) FROM PUBLIC;
GRANT  EXECUTE ON FUNCTION brain.revoke_human_role(name) TO brain_runtime, brain_owner;

COMMENT ON FUNCTION brain.revoke_human_role(name) IS
  'A DEMOTION, NOT A LOCKOUT, and the difference is worth stating because a reader will assume '
  'the stronger one. This removes the mapping, so the login stops being a human: it can no '
  'longer write work_item.actor_type=''human'', can no longer write brain.config_setting, and '
  'brain.current_human() returns NULL for it. It does NOT disable the login and it cannot: '
  'ALTER ROLE ... NOLOGIN needs CREATEROLE, brain_owner is NOCREATEROLE on purpose (migration 2) '
  'and the store holds no superuser connection by design. The role remains a member of '
  'brain_runtime and can still do what any agent can do. A full lockout is ALTER ROLE ... '
  'NOLOGIN plus removing the credential file, which is the privileged half that '
  'store/bin/provision-human.sh owns.';

INSERT INTO brain.schema_migration (version, name)
     VALUES (41, '0041_human_roster_and_ceiling')
  ON CONFLICT (version) DO NOTHING;

COMMIT;
