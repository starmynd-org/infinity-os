-- migration 35: arming a routine is a human act. Disarming one is not.
--
-- Lane C, bus row 0376, 2026-08-27. Companion to migration 34, and the half that makes the kill
-- switch a kill switch rather than a convention.
--
-- ================================================================= THE ASYMMETRY, AND ITS SOURCE
--
-- Migration 26 already states this rule for `work_item.agent_claimable`, in its own words:
--
--   'Lowering (true -> false) is allowed to everybody: taking work back from the fleet is the
--    safe direction and refusing it would make the operator ask an agent's permission to keep his
--    own task.'
--
-- A routine has the same shape one level up, and the consequence is larger. A standing scheduled
-- dispatch is a PERSISTENT foothold: a routine created at 03:00 posts work every day thereafter,
-- with no human between the timer and the verb, which is exactly the property that made routines
-- worth building and exactly the property that makes creating one consequential. Accepting one
-- recommendation dispatches one task and is gated on a human login by migration 32. Creating one
-- routine dispatches a task a day forever, and until this file it was gated on nothing.
--
-- So:
--
--   CREATING a routine        needs a human login. It is the permissive direction.
--   RE-ARMING a disabled one  needs a human login. Same direction, same reason.
--   CHANGING what it posts    needs a human login. An edit to `lane`, `workdir`, `title`, the
--                             cadence or the flags is a new routine wearing the old one's name.
--   DISABLING one             needs NOTHING. A kill switch that can be refused is not a kill
--                             switch, and refusing it would make the operator ask an agent's
--                             permission to stop the agent's routine.
--
-- ================================================================= ONE NOTION OF HUMAN
--
-- `brain.current_human()`, migration 20, reading `session_user`, which is fixed at authentication
-- and unreachable from SQL. Migration 20's own COMMENT is the instruction being followed here:
-- a second notion of who is human must never be built beside the first, 'or the two drift and the
-- weaker one becomes the real policy'. This file therefore builds no test of its own.
--
-- Lane E owns `brain.human_role` and its successor under row 0384. This trigger composes
-- `brain.current_human()` and does not read `human_role` directly, so whatever lane E does to the
-- roster arrives here for free. Raised in crosstalk.
--
-- ================================================================= WHAT THIS DOES NOT CLOSE
--
-- The verb layer is the FIRST gate and this is the second, the same two-gate shape migration 32
-- uses on `recommend accept`, and neither trusts the other. Neither closes an operator who
-- creates a routine he later regrets: that is what `routine disable` is for, and it is
-- deliberately available to everyone.
--
-- It also does not gate `brain.routine_run`. An occurrence is written by the tick, running as
-- `brain_runtime` from a systemd timer with no human anywhere near it, which is the entire point.
-- What bounds THAT write is `routine_run_one_per_slot` and the fact that the tick can only fire
-- routines a human armed.
--
-- ================================================================= ROLLBACK
--
--   BEGIN;
--   DROP TRIGGER IF EXISTS routine_arming_is_a_human ON brain.routine;
--   DROP FUNCTION IF EXISTS brain.routine_arming_is_a_human();
--   DELETE FROM brain.schema_migration WHERE version = 35;
--   COMMIT;
--
-- Nothing else in the system references either name. Migration 34 is unaffected and a store that
-- has 34 without 35 is a working store with one gate instead of two.

\set ON_ERROR_STOP on

BEGIN;

SET search_path TO brain, public;

CREATE OR REPLACE FUNCTION brain.routine_arming_is_a_human() RETURNS trigger
  LANGUAGE plpgsql AS $$
DECLARE
  what text;
BEGIN
  IF TG_OP = 'INSERT' THEN
    what := 'create';
  ELSE
    -- DISABLING IS FREE, and it is checked first so that the safe direction is never one
    -- condition away from being refused.
    IF NEW.disabled_at IS NOT NULL AND OLD.disabled_at IS NULL THEN
      RETURN NEW;
    END IF;
    -- A no-op UPDATE, and an edit to the disablement record of an already-disabled routine, are
    -- both the safe direction too.
    IF NEW.disabled_at IS NULL AND OLD.disabled_at IS NOT NULL THEN
      what := 're-arm';
    ELSIF (NEW.name, NEW.title, NEW.lane, NEW.body, NEW.workdir, NEW.parent, NEW.priority,
           NEW.max_attempts, NEW.agent_claimable, NEW.external, NEW.canon_touching,
           NEW.period_minutes, NEW.anchor_at, NEW.misfire_grace_minutes, NEW.skip_if_open)
          IS DISTINCT FROM
          (OLD.name, OLD.title, OLD.lane, OLD.body, OLD.workdir, OLD.parent, OLD.priority,
           OLD.max_attempts, OLD.agent_claimable, OLD.external, OLD.canon_touching,
           OLD.period_minutes, OLD.anchor_at, OLD.misfire_grace_minutes, OLD.skip_if_open) THEN
      what := 'change what';
    ELSE
      RETURN NEW;
    END IF;
  END IF;

  IF brain.current_human() IS NOT NULL THEN
    RETURN NEW;
  END IF;

  RAISE EXCEPTION 'refusing to % routine %: this connection is %, which is not a human login',
                  what, COALESCE(NEW.name, '(new)'), session_user
    USING HINT = 'A routine is a standing scheduled dispatch: it posts work every period, '
                 'forever, with no human between the timer and the verb. Arming one is therefore '
                 'at least as consequential as accepting a recommendation, which migration 32 '
                 'already gates on a human login. Run this from a shell holding the operator '
                 'credential (store/bin/provision-operator.sh), which `swarm routine add '
                 '--as-operator` opens for you. DISABLING a routine needs none of this and never '
                 'will: a kill switch that can be refused is not a kill switch.',
        ERRCODE = 'insufficient_privilege';
END $$;

COMMENT ON FUNCTION brain.routine_arming_is_a_human() IS
  'Arming is privileged, disarming is free. Same asymmetry and same primitive as migration 26''s '
  'work_item_agent_claimable_raise_is_a_human, deliberately, so there is one notion of human in '
  'this schema and not two.';

-- Not `BEFORE UPDATE OF <columns>`, for migration 20 and 26's reason: that form fires on the
-- columns a statement NAMES, so an UPDATE that sets every column from a record carries the value
-- past the narrow form. The unconditional trigger returns on its first branch for a disable and
-- for a no-op, which is every write this table sees in normal operation.
DROP TRIGGER IF EXISTS routine_arming_is_a_human ON brain.routine;
CREATE TRIGGER routine_arming_is_a_human
  BEFORE INSERT OR UPDATE ON brain.routine
  FOR EACH ROW
  EXECUTE FUNCTION brain.routine_arming_is_a_human();

INSERT INTO brain.schema_migration (version, name)
VALUES (35, '0035_routine_arming_is_a_human') ON CONFLICT (version) DO NOTHING;

COMMIT;
