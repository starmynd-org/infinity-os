-- migration 38: a human and an agent may not have the same name
--
-- LEDGER VERSION 38, read from brain.schema_migration and not from this directory.
--
-- ================================================================= a live denial of service
--
-- THIS IS NOT A MULTI-USER PROBLEM. It is a live defect on the store today, found while
-- measuring row 0384's subject, and multi-user makes it twelve times wider. Measured on scratch
-- `brain_lane_e` on 2026-08-27, quoted in full in
-- `outputs/2026-08-27-E-0384-multi-user/PROOF.md` section 2:
--
--     operator login: ('operator', 'brain_operator')
--     BEFORE poisoning: the operator accepts 0013 -> ('operator',)
--     brain.agent rows before: 0
--     agent INSERT name='operator': ('operator',) (rowcount 1 )
--     AFTER poisoning: the operator is REFUSED on 0014:
--         refusing to record operator as the acceptor of 0014: operator is a fleet agent
--
-- ONE INSERT, from `brain_runtime`, which is the credential every agent surface in this fleet
-- already holds, and the operator can never accept work again from his own login. Migration 22's
-- HUMAN rule -- "the acceptor may not be a registered agent" -- is correct and is what fires,
-- and it fires because the two namespaces overlap and one of them is writable by the party the
-- rule exists to constrain. `claim`, `heartbeat`, `agent ready`, `tick` and `stop` all write
-- `brain.agent` rows keyed by a name the caller chooses
-- (engine/swarm_engine/transitions.py:833, :1512, :1572, :1846, :1933).
--
-- Under row 0386 decision 2 there are up to twelve human names, so after multi-user there are
-- twelve strings whose registration as an agent silently locks a human out of his own console.
-- It costs one statement, it needs no secret the fleet does not have, and nothing pages.
--
-- MEASURED ON LIVE BEFORE WRITING THIS: brain.agent holds 11 names (D1, D2, D3, D9A, D9R, T1,
-- T2, T3, T4, T5, admiral), brain.human_role holds 1 (operator), and the collision count is
-- 0 of 1. This migration therefore lands on live without touching a row, and the check that says
-- so is repeated by the suite rather than trusted from this comment.
--
-- ================================================================= both directions, on purpose
--
-- One trigger would close the attack and leave the accident. `brain.agent` refusing a human's
-- name stops the deliberate lockout; `brain.human_role` refusing an agent's name stops the
-- operator provisioning `swarm admin human provision t2` one evening and finding that T2's next
-- heartbeat is refused, or that his own acceptances are, depending on which table won the race.
-- Two tables, two triggers, one rule, and neither one trusts the other.
--
-- ================================================================= what it does NOT do
--
-- It does not rename anything and it refuses no EXISTING row. Both triggers fire on INSERT and
-- on an UPDATE that changes the name, so a store that already carries a collision keeps it and
-- keeps failing the way it failed before. That is deliberate: a migration that deleted a live
-- `brain.agent` row would delete a fleet member's identity to fix a name clash, and the repair
-- for an existing collision is a decision (rename the agent, or revoke the human), not a DELETE
-- a migration takes on its own. `engine/tests/test_multi_user_subject.py` prints the live
-- collision count with its denominator so an existing one cannot be missed.
--
-- ================================================================= rollback, stated and executed
--
--     BEGIN;
--       DROP TRIGGER IF EXISTS agent_name_is_not_a_human ON brain.agent;
--       DROP TRIGGER IF EXISTS human_role_name_is_not_an_agent ON brain.human_role;
--       DROP FUNCTION IF EXISTS brain.agent_name_is_not_a_human();
--       DROP FUNCTION IF EXISTS brain.human_role_name_is_not_an_agent();
--       DELETE FROM brain.schema_migration WHERE version = 38;
--     COMMIT;
--
-- Additive: two functions, two triggers. No column, no grant, no row. Run, and re-applied, on
-- brain_lane_e. See PROOF.md section 4.

\set ON_ERROR_STOP on

BEGIN;

-- ---------------------------------------------------------------- an agent may not be a human

CREATE OR REPLACE FUNCTION brain.agent_name_is_not_a_human() RETURNS trigger
  LANGUAGE plpgsql AS $$
BEGIN
  IF EXISTS (SELECT 1 FROM brain.human_roster() r WHERE r.human = NEW.name) THEN
    RAISE EXCEPTION 'refusing to register an agent named %: that is a human on this instance',
                    NEW.name
      USING ERRCODE = 'unique_violation',
            HINT = 'brain.work_item_acceptance_guard refuses an acceptance by any name in '
                   'brain.agent, so registering an agent under a human''s name locks that human '
                   'out of accepting work from his own login, permanently, for the cost of one '
                   'INSERT from brain_runtime. Measured 2026-08-27 on scratch. Name the agent '
                   'something else: the fleet picks its own names and the humans do not.';
  END IF;
  RETURN NEW;
END $$;

COMMENT ON FUNCTION brain.agent_name_is_not_a_human() IS
  'Humans and agents share one namespace and this is the half of the rule an AGENT can reach. '
  'Reads brain.human_roster(), migration 41''s SECURITY DEFINER door, because brain_runtime -- '
  'the login that writes every brain.agent row -- cannot read brain.human_role and must not be '
  'able to.';

DROP TRIGGER IF EXISTS agent_name_is_not_a_human ON brain.agent;
CREATE TRIGGER agent_name_is_not_a_human
  BEFORE INSERT OR UPDATE OF name ON brain.agent
  FOR EACH ROW
  EXECUTE FUNCTION brain.agent_name_is_not_a_human();

-- ---------------------------------------------------------------- a human may not be an agent

CREATE OR REPLACE FUNCTION brain.human_role_name_is_not_an_agent() RETURNS trigger
  LANGUAGE plpgsql AS $$
BEGIN
  IF EXISTS (SELECT 1 FROM brain.agent a WHERE a.name = NEW.human) THEN
    RAISE EXCEPTION 'refusing to map % as a human: that is a registered fleet agent', NEW.human
      USING ERRCODE = 'unique_violation',
            HINT = 'The mapping would land and the human would then be unable to accept work: '
                   'brain.work_item_acceptance_guard refuses an acceptance by any name in '
                   'brain.agent. Pick a slug the fleet has not taken. `swarm agents` lists them.';
  END IF;
  RETURN NEW;
END $$;

COMMENT ON FUNCTION brain.human_role_name_is_not_an_agent() IS
  'The other half, and it exists to stop an ACCIDENT rather than an attack: provisioning a human '
  'under a name the fleet already uses would mint a login that cannot accept work, and the '
  'failure would arrive on the operator''s first Accept rather than at provisioning time.';

DROP TRIGGER IF EXISTS human_role_name_is_not_an_agent ON brain.human_role;
CREATE TRIGGER human_role_name_is_not_an_agent
  BEFORE INSERT OR UPDATE OF human ON brain.human_role
  FOR EACH ROW
  EXECUTE FUNCTION brain.human_role_name_is_not_an_agent();

INSERT INTO brain.schema_migration (version, name)
     VALUES (38, '0038_one_namespace_for_humans_and_agents')
  ON CONFLICT (version) DO NOTHING;

COMMIT;
