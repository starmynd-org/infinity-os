-- 0015_recommendation_human_login.sql -- V9 BLOCKER 1: the decider on an acceptance becomes a
-- database LOGIN instead of a string the caller picked.  Schema version 32.
--
-- Task 0290, child of the V9 acceptance run (0222).  Additive and re-runnable: it CREATE OR
-- REPLACEs one function, re-creates the trigger that already points at it, and rewrites two
-- COMMENTs.  No table is altered, no column is added, nothing is dropped.
--
-- READ THE LEDGER BEFORE APPLYING THIS.  `brain.schema_migration` on live held 31 as its maximum
-- at 2026-08-19T02:2xZ and no file in `migrations/`, `budget/schema/` or `queue/schema/` records
-- 32.  That was true when this was written and four lanes write this tree; a number free ten
-- minutes ago is not a number free now.  The guard below refuses rather than applying its DDL
-- against a taken version, which matters because every migration ends ON CONFLICT DO NOTHING and
-- would otherwise leave the store one migration ahead of what the ledger reports.
--
-- ----------------------------------------------------------------------------------------
-- WHAT WAS BROKEN
-- ----------------------------------------------------------------------------------------
--
-- V2 invariant 1 is "a drafted option cannot dispatch without a human choosing it."  It did not
-- hold.  `brain.recommendation_human_decider()` as shipped in `0007_queue.sql` and
-- `queue/human_queue/transitions.py::recommend_accept` between them made three tests, and NONE
-- of the three asks who is connected:
--
--   1. the decider string is non-empty
--   2. the decider is NOT a name present in `brain.agent`
--   3. `SWARM_PARENT_TASK` is not set in the calling process's environment
--
-- Test 2 fails OPEN by construction.  It refuses the names that ARE agents, so every name that is
-- not one passes -- and the agent picks the name.  MEASURED by task 0222 on scratch
-- `brain_t2_0222_path`, from a `brain_runtime` connection with SWARM_PARENT_TASK cleared, against
-- real drafted options:
--
--   by='T2'   (before T2 had a brain.agent row) -> DISPATCHED, spawned 0003, actor_type='human'
--   by='T2'   (after a heartbeat registered it) -> refused, "T2 is a registered agent"
--   by='Andrew'                                 -> DISPATCHED, spawned 0004
--   by='zzz-not-a-person'                       -> DISPATCHED, spawned 0005, actor_type='human'
--
-- The contrast that names the fix was measured in the SAME process on the SAME connection:
--
--   UPDATE brain.work_item     SET actor_type='human' ...  -> REFUSED, "not a human login"
--   UPDATE brain.recommendation SET decided_by=..., actor_type='human' ... -> rowcount 1
--
-- V00 records as already proven that human identity is a database login and not a flag.
-- Migration 20 applied that to `work_item` through `brain.human_role` and `session_user`.  It was
-- never applied to `recommendation`, and `recommendation` is where invariant 1 lives.
--
-- ----------------------------------------------------------------------------------------
-- WHAT THIS FILE ADDS, AND WHY IT IS TWO CHECKS RATHER THAN ONE
-- ----------------------------------------------------------------------------------------
--
-- IS THIS CONNECTION A HUMAN AT ALL.  `brain.current_human()` answers from `session_user`, which
-- is fixed at authentication and which no SQL a client can send changes -- `SET ROLE` moves
-- `current_user` and not this.  `brain_runtime`, the login every agent surface connects as, has
-- no mapping row, cannot read the table that would give it one (migration 20 REVOKEs it), and
-- cannot write one.  So the answer is NULL for every agent process and the acceptance is refused
-- whatever `decided_by` says.  This is the gate.
--
-- AND IS THE RECORDED DECIDER THE ONE THE DATABASE SEES.  A human login accepting under someone
-- else's name is a false record rather than a forged one, and the record of what the operator
-- accepted is the primary artefact of the whole dogfooding programme
-- (`engine/swarm_engine/accept.py:disagreement_report` scores against it).  So the name must be
-- the database's answer.  `engine/swarm_engine/steering.py::_the_human` already refuses this
-- exact disagreement for the steering mark, in the same words, and this is that rule applied to
-- the second place a human's identity is recorded.
--
-- ORDER IS DELIBERATE AND IS NOT COSMETIC.  The empty-decider, registered-agent and
-- actor_type-must-be-human refusals keep their existing position AHEAD of the two new ones, so
-- that a caller who is wrong in one of the old ways still gets the old sentence.  Three suites
-- assert on those exact strings from a SUPERUSER connection -- which has no `brain.human_role`
-- row either, so a login check placed first would have replaced every one of those messages with
-- "not a human login" and made three green suites red about the wrong thing:
--   queue/tests/test_no_self_execution.py  routes 3, 4 and 6
--   web/tests/test_option_dispatch.py      route (e)
-- Being refused for the most specific true reason is the property; "refused" alone is not.
--
-- THE `brain.agent` TEST STAYS, as defence in depth and no longer as the gate.  It covers the one
-- case the login check cannot see: a human login typing a fleet agent's name.
--
-- ----------------------------------------------------------------------------------------
-- WHAT THIS DOES **NOT** CLOSE, stated rather than implied
-- ----------------------------------------------------------------------------------------
--
-- THE HOST RESIDUAL, which is the same one `store/SECRETS.md` records for every role and which
-- migration 20's header states for `work_item`.  On a `local-attended` host every credential file
-- sits in one 0700 directory under one UID, so an OS process that can read the operator's secret
-- can connect as the operator.  This trigger is exactly as strong as `actor_type = 'human'` on
-- `brain.work_item` is, and no stronger.  What is closed absolutely is the case that actually
-- exists: a process holding the `brain_runtime` credential -- which is what every agent surface
-- is configured with -- has no route to an accepted recommendation by any decider name.
--
-- `recommend reject` IS NOT GATED HERE.  A rejection dispatches nothing, so it is not invariant
-- 1, and widening this file to cover it would have changed a verb no measurement was reported
-- against.  It is adjacent work and it is named on task 0290's thread rather than done quietly.
--
-- THE STATE COLUMN IS ONE STATEMENT DEEP, the same residual migration 22 records: nothing here
-- guards `brain.recommendation.state` transitions in general, only the transition INTO
-- `accepted`.  That is the transition invariant 1 is about.
--
-- ============================================================ THE SECOND HALF OF THE RULE BELOW,
-- ============================================================ ADDED 2026-08-29, BUS ROW 0408
--
-- The RAISE below carries this repo's migration-numbering rule and is quoted for it in
-- `docs/WHY-IT-IS-LIKE-THIS.md` section 15 and in the 2026-08-27 commander brief:
--
--     "Pick the next version by reading brain.schema_migration, never by listing a directory."
--
-- That sentence is right and it is NOT SUFFICIENT, and nothing below is being changed, because
-- this file is applied on live (version 32, applied_at 2026-08-27) and `docs/CHANGING-IT.md`
-- forbids editing an applied migration.  The correction is stated here, in a comment, and it is
-- carried for real in `docs/CHANGING-IT.md` and in `engine/bin/scratch-db.sh ledger`:
--
--     TAKE max(version) + 1.  NEVER THE LOWEST FREE NUMBER.
--
-- The rule as written stops a COLLISION with a version that is held.  It does not stop a HOLE,
-- and reading the ledger is exactly what makes a hole look available.  Measured on live `brain`
-- 2026-08-27 and re-measured 2026-08-29: 41 rows, max 42, and 39 absent -- because the 2026-08-27
-- commander brief assigned lane E "36 to 39" and lane E used three of the four.  A lane obeying
-- the sentence above perfectly reads that ledger, sees 39 unheld, and takes it.
--
-- A file at a hole does not apply NOTHING, which is the failure this guard was written against.
-- It applies ONE thing in TWO different places.  `scratch-db.sh::migration_list` orders by
-- recorded version and `cmd_migrate` applies only what a store has not recorded, so a file at 39
-- applies BETWEEN 38 and 40 on a fresh `create` and AFTER 40, 41 and 42 on a `migrate` of a store
-- that already recorded them.  The fresh build is the order every suite in this repo tests
-- against, so the suites can go green on an order live will never see.
--
-- 39 is now fenced by `migrations/0039_a_reserved_number_is_not_a_migration.sql`, which carries no
-- DDL and so has nothing to order against 40, 41 and 42.  The hole is closed; the rule is what
-- stops the next one.

\set ON_ERROR_STOP on

DO $$
DECLARE taken text;
BEGIN
  SELECT name INTO taken FROM brain.schema_migration WHERE version = 32;
  IF taken IS NOT NULL AND taken <> '0015_recommendation_human_login' THEN
    RAISE EXCEPTION 'schema version 32 is already held by %, not 0015_recommendation_human_login. '
                    'Pick the next version by reading brain.schema_migration, never by listing a '
                    'directory.', taken;
  END IF;
END $$;

BEGIN;

-- This file is meaningless without migration 20: `brain.current_human()` is the whole mechanism.
-- Refusing here beats installing a trigger that calls a function that does not exist, which would
-- turn every acceptance into an UndefinedFunction at the moment the operator tried to decide.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                  WHERE n.nspname = 'brain' AND p.proname = 'current_human') THEN
    RAISE EXCEPTION 'brain.current_human() does not exist, so migration 20 has not been applied '
                    'to this database. Apply migrations/0020_human_actor_identity.sql first: this '
                    'file is that migration applied to a second table.';
  END IF;
END $$;

CREATE OR REPLACE FUNCTION brain.recommendation_human_decider() RETURNS trigger AS $$
DECLARE
  is_agent boolean;
  the_human text;
BEGIN
  IF TG_OP = 'INSERT' THEN
    IF NEW.state = 'accepted' THEN
      RAISE EXCEPTION 'a recommendation may not be born accepted (id would be %)', NEW.id
        USING HINT = 'INSERT it open, then accept it through `recommend accept`, which is the '
                     'one transition that records a human decider.';
    END IF;
    IF NEW.spawned_work_item IS NOT NULL THEN
      RAISE EXCEPTION 'a recommendation may not be born linked to executed work'
        USING HINT = 'The link is written by `recommend accept` and by nothing else.';
    END IF;
    RETURN NEW;
  END IF;

  IF NEW.state = 'accepted' AND OLD.state IS DISTINCT FROM 'accepted' THEN
    IF NEW.decided_by IS NULL OR btrim(NEW.decided_by) = '' THEN
      RAISE EXCEPTION 'recommendation % cannot be accepted with no decider recorded', NEW.id
        USING HINT = 'Acceptance is a human act. Record who decided.';
    END IF;
    IF NEW.decided_at IS NULL THEN
      RAISE EXCEPTION 'recommendation % cannot be accepted with no decided_at', NEW.id;
    END IF;
    SELECT EXISTS (SELECT 1 FROM brain.agent WHERE name = NEW.decided_by) INTO is_agent;
    IF is_agent THEN
      RAISE EXCEPTION 'recommendation % cannot be accepted by %, which is a registered agent',
                      NEW.id, NEW.decided_by
        USING HINT = 'D00 contract rule 4: the decider on every acceptance is a human. An agent '
                     'that believes this recommendation is right may say so on the thread; it '
                     'may not accept it.';
    END IF;
    IF NEW.actor_type IS DISTINCT FROM 'human'::brain.actor_type THEN
      RAISE EXCEPTION 'recommendation % accepted with actor_type %, which must be human',
                      NEW.id, COALESCE(NEW.actor_type::text, 'null')
        USING HINT = 'effectiveness-points slices on actor_type; an acceptance booked as ai '
                     'would both break the gate and corrupt the leverage measurement.';
    END IF;

    -- THE GATE (migration 32). Everything above is a string the caller chose; this is not.
    the_human := brain.current_human();
    IF the_human IS NULL THEN
      RAISE EXCEPTION 'refusing to accept recommendation %: this connection is %, which is not a '
                      'human login', NEW.id, session_user
        USING HINT = 'Acceptance is the one act that turns a proposal into executed work, so who '
                     'performed it is established by WHICH LOGIN wrote the row and not by the '
                     'decided_by string, which any caller can pick. The old test refused names '
                     'present in brain.agent, which fails open: every name that is not an agent '
                     'passed, and the agent picks the name. The operator''s surfaces connect as '
                     'a role mapped in brain.human_role; provision one with '
                     'store/bin/provision-operator.sh. A process holding the brain_runtime '
                     'credential has no route to this row and is not supposed to.',
            ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.decided_by <> the_human THEN
      RAISE EXCEPTION 'recommendation % records % as the decider and this connection is %. '
                      'Refusing rather than storing either one: the decided_by column is the '
                      'record of who chose this work.', NEW.id, NEW.decided_by, the_human
        USING HINT = 'Write brain.current_human() into decided_by, or accept from the login whose '
                     'name you meant. `recommend accept` does the first.',
            ERRCODE = 'insufficient_privilege';
    END IF;
  END IF;

  IF NEW.spawned_work_item IS NOT NULL AND OLD.spawned_work_item IS DISTINCT FROM NEW.spawned_work_item THEN
    IF NEW.state <> 'accepted' THEN
      RAISE EXCEPTION 'recommendation % is % and cannot be linked to executed work %',
                      NEW.id, NEW.state, NEW.spawned_work_item
        USING HINT = 'Executed work exists only downstream of a human acceptance.';
    END IF;
  END IF;

  RETURN NEW;
END $$ LANGUAGE plpgsql;

-- Re-created rather than assumed: CREATE OR REPLACE FUNCTION leaves the trigger pointing at the
-- new body, so this is belt and braces on a database where 0007 was applied by hand or partially.
DROP TRIGGER IF EXISTS recommendation_human_decider ON brain.recommendation;
CREATE TRIGGER recommendation_human_decider
  BEFORE INSERT OR UPDATE ON brain.recommendation
  FOR EACH ROW EXECUTE FUNCTION brain.recommendation_human_decider();

COMMENT ON FUNCTION brain.recommendation_human_decider() IS
  'Program success criterion: there must be no code path by which a recommendation becomes '
  'executed work without a human decider. Since migration 32 "a human" means a login mapped in '
  'brain.human_role, read through brain.current_human(), and not a decided_by string the caller '
  'picked -- the brain.agent test that preceded it failed open by construction. Proven by '
  'queue/tests/test_no_self_execution.py and engine/tests/test_recommendation_human_login.py.';

COMMENT ON COLUMN brain.recommendation.spawned_work_item IS
  'The executed work. Only `recommend accept` writes it, and trigger recommendation_human_decider '
  'refuses it on any row not accepted by a human -- where human is a database login since '
  'migration 32, not a name the accepting process chose.';

INSERT INTO brain.schema_migration (version, name)
     VALUES (32, '0015_recommendation_human_login')
  ON CONFLICT (version) DO NOTHING;

COMMIT;
