-- migration 46: work does not leave a resting project
--
-- LEDGER VERSION 46, read from `brain.schema_migration` and from `engine/bin/scratch-db.sh
-- ledger`, not from this directory. Measured 2026-08-29 by lane B2: the ledger printed
-- "45 versions read, 1..45, no holes. NEXT VERSION IS 46". The filename prefix is a per-lane
-- counter and it lies; the version is the number in this file's own INSERT at the foot.
--
-- Bus row `0444`, child of `0429`. Sibling of migration 44 (the entity) and of
-- `budget/schema/0045_project_is_a_hold_scope.sql` (the claim-path gate, task 0430).
--
-- Additive and re-runnable: one function, one trigger, one comment rewrite. It drops nothing,
-- adds no column, changes no type and backfills nothing.
--
-- NOT APPLIED TO LIVE when this line was written, and no agent may apply it. `docs/CHANGING-IT.md`
-- and the `apply-to-live.py` runbook both make applying to live the operator's own act, and
-- `engine/bin/scratch-db.sh` refuses the database `brain` by name at its own line 71. This file
-- was built and proven on `brain_b2`, a per-lane scratch database at ledger 45.
--
-- ================================================================= THE HOLE
--
-- Migration 44's own header names it under "what this does not close":
--
--   "THE JOIN IS AN ESCAPE HATCH AND IT IS DELIBERATELY LEFT OPEN. brain.work_item.project is
--    writable by brain_runtime, because `post` is an agent verb and a task has to be able to say
--    which project it belongs to. So an agent can move a work item OFF a held project in one
--    UPDATE and out from under whatever throttle later reads this column."
--
-- One statement, no login, no record:
--
--     UPDATE brain.work_item SET project = NULL WHERE id = '<the task it wants>'
--
-- `engine/tests/test_project_entity.py` scene 20 asserted that hatch as CURRENTLY TRUE, so that
-- the day somebody closed it the suite would go red and say the documentation was stale. This is
-- that day. Scene 20 is not narrowed; it is INVERTED, and it now watches the refusal.
--
-- WHY IT MATTERS MORE THAN IT SOUNDS. The whole feature exists against one incident, the
-- operator's own:
--
--   "there's certain projects where I kind of just wanted the AI to take a break with it. And
--    then I realized later that it worked on it for like eight hours with eight terminals and I
--    ran out of API tokens very quickly."
--
-- A hold that can be stepped around is not a hold. Task 0430's `_PROJECT_BUDGET_CTE` withholds
-- work whose project is at rest; without this migration an agent clears that gate by unpointing
-- the row, and the operator's board still says `hold` while the money still goes out. The board
-- would then be the second, invisible state machine that `web/MUST-NOT-BUILD.md` item 3 feared,
-- reached by the exact door the overrule was supposed to close.
--
-- ================================================================= WHY IN THE DATABASE
--
-- Migration 44 said the right place was "where the throttle is defined, in one predicate", and
-- called a second trigger on `brain.work_item` the fallback. Lane B2 took the other branch and
-- the reason is a measurement rather than a preference.
--
-- THE PREDICATE FORM CANNOT REACH THIS CASE. The suggestion on row 0444 was to have the gate ask
-- about "the project a row was HELD BY when it was claimed, not the one it names now". But the
-- claim path only ever reads rows in `inbox`, and an unclaimed row has no project it was held by:
-- there is no earlier fact to fall back on. The escape happens BEFORE the claim, precisely so
-- that the claim sees a row in no project, and no predicate evaluated at claim time can see a
-- column that was rewritten a second earlier. The seam has nothing to remember.
--
-- AND AN APPLICATION GATE IS NOT ON EVERY PATH. Migration 42 states this in its own words about
-- acceptance: `web/rooms.py` is not on every path, and `swarm done <id> --agent operator` reached
-- an identical forgery with none of it. The same is true here twice over. `projects.py::project_
-- attach` is one writer; `transitions.py::set_field` builds `UPDATE brain.work_item SET {key}`
-- from a caller-supplied key; `post` writes the column directly; and psql is always there. A rule
-- enforced in the one writer somebody remembered is a rule enforced nowhere.
--
-- THE COST MIGRATION 44 WAS RIGHT TO WORRY ABOUT, and how it is paid. `brain.work_item` is the
-- hottest table in this store and every claim locks rows in it. So the trigger is scoped twice:
-- `BEFORE UPDATE OF project` means Postgres does not even consider it unless `project` appears in
-- the SET list, and the `WHEN` clause means it does not fire unless the value actually changes.
-- The claim's own UPDATE writes state, claimed_by, claimed_at and attempts, so it never mentions
-- `project` and never reaches this. Measured on `brain_b2` at ledger 46, over the same 12-claimer
-- shape the contention gate uses, the claim path was unchanged; the numbers are in
-- `outputs/2026-08-29-commander/lane-B2-report.md`.
--
-- ================================================================= THE RULE, IN ONE SENTENCE
--
-- WORK DOES NOT LEAVE A RESTING PROJECT EXCEPT BY A HUMAN'S HAND.
--
--   moving work OFF a resting project     needs brain.current_human(). It is a partial resume:
--                                         it puts that item back in the claim path, which is the
--                                         direction the eight-terminal bill came from, and
--                                         migration 44 already gates that direction on a human
--                                         for the project as a whole.
--   moving work ONTO any project          open to everybody, resting or not. Filing work INTO a
--                                         hold cannot escape it; it can only add to it. This is
--                                         `post --project` and `swarm project attach`, and both
--                                         are agent verbs on purpose.
--   moving work off an IN PROGRESS one    open to everybody. Nothing is being escaped, because
--                                         nothing is holding it.
--   any UPDATE that does not touch        never reaches this trigger at all. That is `claim`,
--   `project`                             `done`, `block`, `fail`, `reopen`, `release`, `set`
--                                         on any other key, and every other verb in the engine.
--
-- WHY A HUMAN IS EXEMPT RATHER THAN REFUSED TOO. It is his board. Migration 44's asymmetry is the
-- precedent and this is the same shape one level down: stopping is free and starting needs the
-- operator, because a kill switch that can be refused is not a kill switch and an agent that can
-- lift its own project's hold is not being held. Moving an item out from under a hold IS lifting
-- the hold, for that item.
--
-- WHAT THIS DOES NOT CLOSE, named rather than discovered:
--
--   * DELETING the work item. `brain_runtime` holds no DELETE on `brain.work_item`, and a deleted
--     row is never claimed anyway, so it is not an escape from the throttle. It is checked below
--     rather than assumed, and if that grant ever changes the check goes red.
--   * A NEW work item posted into no project while a project is held. That is new work, not work
--     that left. `post` is an agent verb by design and the operator's control over new work is
--     `agent_claimable` (migration 26) and the lane and fleet brakes, not this.
--   * `canonical_task`. It is a foreign task id on the git planning ladder and no gate reads it.
--     `work_item_project_matches_canonical` already stops the two answers disagreeing.
--   * The board itself. This is the enforcement half; the console view is bus row 0436.

BEGIN;

SET search_path TO brain, public;

-- FAIL FAST RATHER THAN QUEUE. Migration 13's and 44's reasoning, unchanged: CREATE TRIGGER takes
-- a strong lock on `brain.work_item`, the table every claim locks rows in, and a lock request
-- that waits does not wait quietly.
SET LOCAL lock_timeout = '5s';

-- ---------------------------------------------------------------- the control

CREATE OR REPLACE FUNCTION brain.work_item_does_not_leave_a_resting_project()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  was_state text;
  HINT_WHY  constant text :=
    'Bus row 0444, migration 46. The hold exists because of one incident: work the operator '
    'believed was resting ran eight terminals wide and the bill was the notification. A hold '
    'that can be stepped around in one UPDATE is not a hold.';
BEGIN
  -- Filing work INTO a project cannot escape a hold, so the only interesting direction is OFF.
  IF OLD.project IS NULL THEN
    RETURN NEW;
  END IF;

  -- ONE NOTION OF HUMAN, migration 20's, reading `session_user`, which is fixed at authentication
  -- and unreachable from SQL. Migration 20's instruction is to never build a second notion beside
  -- the first "or the two drift and the weaker one becomes the real policy". `brain_runtime`, the
  -- credential every agent surface in this fleet holds, gets NULL here.
  IF brain.current_human() IS NOT NULL THEN
    RETURN NEW;
  END IF;

  SELECT p.state INTO was_state FROM brain.project p WHERE p.slug = OLD.project;

  -- A project row that is gone, or in progress, is holding nothing.
  IF was_state IS NULL OR was_state = 'in_progress' THEN
    RETURN NEW;
  END IF;

  RAISE EXCEPTION 'refusing to move work item % off project %, which is %: this connection (%) '
                  'is not a human login',
                  OLD.id, OLD.project, was_state, session_user
    USING HINT = 'Moving an item out from under a hold is lifting the hold for that item, and '
                 'that is the direction that spends money. Return the project to in_progress '
                 'first with `swarm project resume ' || OLD.project || ' --reason ''...''`, from '
                 'a login brain.current_human() recognises, and move the work after. Filing work '
                 'INTO a project needs nothing at all. ' || HINT_WHY;
END $$;

COMMENT ON FUNCTION brain.work_item_does_not_leave_a_resting_project() IS
  'Closes the one-UPDATE exit migration 44 left open and named. brain.work_item.project is '
  'writable by brain_runtime because `post` is an agent verb, so before this an agent cleared '
  'task 0430''s claim-path hold with `UPDATE brain.work_item SET project = NULL`. Migration 35''s '
  'asymmetry, one level down: moving work ONTO a project is open to everybody because it cannot '
  'escape a hold, and moving it OFF a RESTING project needs brain.current_human() because doing '
  'so lifts the hold for that item. Never fires unless `project` is in the SET list AND its value '
  'actually changes, so no claim, done, block, fail, reopen or release reaches it.';

DROP TRIGGER IF EXISTS work_item_does_not_leave_a_resting_project ON brain.work_item;
CREATE TRIGGER work_item_does_not_leave_a_resting_project
  BEFORE UPDATE OF project ON brain.work_item
  FOR EACH ROW
  WHEN (OLD.project IS DISTINCT FROM NEW.project)
  EXECUTE FUNCTION brain.work_item_does_not_leave_a_resting_project();

COMMENT ON COLUMN brain.work_item.project IS
  'The project this work belongs to, as a foreign key into brain.project (migration 44, bus row '
  '0429). NOT the same fact as canonical_task, which is a foreign task id on the git planning '
  'ladder; work_item_project_matches_canonical stops the two DISAGREEING without making either '
  'imply the other. Writable by brain_runtime because `post` is an agent verb. THE ESCAPE HATCH '
  'THAT ONCE MADE THAT DANGEROUS IS CLOSED as of migration 46 (bus row 0444): '
  'work_item_does_not_leave_a_resting_project refuses any non-human connection that moves a row '
  'OFF a project which is hold, ice or blocked. Filing work INTO a project is still open to every '
  'connection, because it can only add to a hold and never escape one.';

-- ---------------------------------------------------------------- prove it, in the migration
--
-- Migration 13's rule, and migration 44's shape: a migration that says it created a thing and did
-- not is exactly the class of thing this store's checks exist to catch, so it catches itself.
-- Every verdict carries the count of things it actually compared, and a denominator of zero
-- raises rather than passes.

DO $$
DECLARE
  n_checked integer := 0;
  refused   boolean;
  moved     boolean;
  filed     boolean;
  filed_why text := '';
BEGIN
  -- ---- the control is ON the hottest table, not merely defined beside it
  IF NOT EXISTS (SELECT 1 FROM pg_trigger tg
                   JOIN pg_class c ON c.oid = tg.tgrelid
                   JOIN pg_namespace n ON n.oid = c.relnamespace
                  WHERE n.nspname = 'brain' AND c.relname = 'work_item'
                    AND NOT tg.tgisinternal
                    AND tg.tgname = 'work_item_does_not_leave_a_resting_project') THEN
    RAISE EXCEPTION 'migration 46: the trigger is not on brain.work_item. A CREATE FUNCTION with '
                    'no trigger behind it is the shape of every guard this repo has found '
                    'claiming to hold something it never saw.';
  END IF;
  n_checked := n_checked + 1;

  -- ---- it is scoped to the project column, which is what keeps the claim path free
  IF NOT EXISTS (SELECT 1 FROM pg_trigger tg
                   JOIN pg_class c ON c.oid = tg.tgrelid
                   JOIN pg_namespace n ON n.oid = c.relnamespace
                   JOIN pg_attribute a ON a.attrelid = c.oid
                                      AND a.attnum = ANY (
                                            SELECT unnest(string_to_array(
                                                     replace(tg.tgattr::text, ' ', ''),
                                                     ' '))::smallint)
                  WHERE n.nspname = 'brain' AND c.relname = 'work_item'
                    AND tg.tgname = 'work_item_does_not_leave_a_resting_project'
                    AND a.attname = 'project') THEN
    RAISE EXCEPTION 'migration 46: the trigger is not scoped to the project column. Without '
                    'UPDATE OF project it fires on every claim, and migration 44 was right that '
                    'an unscoped trigger on this table is a cost with no measurement behind it.';
  END IF;
  n_checked := n_checked + 1;

  -- ---- it consults the one notion of human rather than inventing a second
  IF position('current_human' in
              pg_get_functiondef(
                'brain.work_item_does_not_leave_a_resting_project()'::regprocedure)) = 0 THEN
    RAISE EXCEPTION 'migration 46: the guard does not consult brain.current_human(), so either '
                    'it refuses the operator his own board or it invents a second notion of who '
                    'is human beside migration 20''s.';
  END IF;
  n_checked := n_checked + 1;

  -- ---- brain_runtime still holds no DELETE on work_item, which is why deletion is not gated
  --      here. Checked rather than assumed: if the grant ever changes, this goes red and the
  --      "what this does not close" paragraph above becomes a lie that somebody has to fix.
  IF has_table_privilege('brain_runtime', 'brain.work_item', 'DELETE') THEN
    RAISE EXCEPTION 'migration 46: brain_runtime now holds DELETE on brain.work_item. This '
                    'migration''s header says deletion is not an escape BECAUSE that grant does '
                    'not exist. Re-decide before applying.';
  END IF;
  n_checked := n_checked + 1;

  -- ---- AND IT ACTUALLY REFUSES. Everything above is structure; this is behaviour, watched
  --      happening inside the migration, because a guard nobody has watched fail is not a guard.
  --
  --      THE CLEANUP IS A ROLLBACK AND NOT A DELETE, and that is migration 44 holding rather than
  --      a trick: `project_state_is_a_control` refuses to DELETE a resting project from any
  --      connection `brain.current_human()` does not recognise, and the migration credential is
  --      `postgres`, which it does not. Written first with a DELETE, and 44 refused it, which is
  --      44 working exactly as designed. So the whole fixture lives in a subtransaction that is
  --      unwound by a sentinel exception. PL/pgSQL VARIABLES SURVIVE A CAUGHT EXCEPTION while the
  --      rows do not, which is what makes the verdicts readable afterwards and leaves no
  --      m46-selftest row on any store this file is ever applied to.
  --
  --      `SET LOCAL ROLE brain_runtime` borrows the runtime's GRANTS, and what makes the arms
  --      below meaningful is a second fact measured rather than assumed: SET ROLE changes
  --      `current_user` and NOT `session_user`. `brain.current_human()` reads session_user, which
  --      migration 20 chose precisely because it is fixed at authentication and unreachable from
  --      SQL, so this connection stays `postgres` to it -- a connection the database does not
  --      recognise as a human, which is exactly the class this guard is about. A self test run
  --      from a login current_human() DID recognise would be exempt and would prove nothing.
  BEGIN
    INSERT INTO brain.project (slug, title, state, state_reason)
         VALUES ('m46-selftest', 'migration 46 self test', 'hold', 'proving the guard');
    INSERT INTO brain.work_item (id, title, lane, state, project)
         VALUES ('m46self', 'migration 46 self test', 'test', 'inbox', 'm46-selftest');
    INSERT INTO brain.work_item (id, title, lane, state)
         VALUES ('m46free', 'migration 46 self test, no project', 'test', 'inbox');

    -- the refused direction: OFF a held project, as an agent
    BEGIN
      SET LOCAL ROLE brain_runtime;
      UPDATE brain.work_item SET project = NULL WHERE id = 'm46self';
      RESET ROLE;
      refused := false;
    EXCEPTION WHEN OTHERS THEN
      RESET ROLE;
      refused := true;
    END;
    SELECT project IS NULL INTO moved FROM brain.work_item WHERE id = 'm46self';

    -- the permitted direction: INTO a held project, as an agent
    BEGIN
      SET LOCAL ROLE brain_runtime;
      UPDATE brain.work_item SET project = 'm46-selftest' WHERE id = 'm46free';
      RESET ROLE;
      filed := true;
    EXCEPTION WHEN OTHERS THEN
      RESET ROLE;
      filed := false;
      filed_why := SQLERRM;
    END;

    RAISE EXCEPTION 'migration 46 self test complete' USING ERRCODE = 'ZZ046';
  EXCEPTION
    WHEN SQLSTATE 'ZZ046' THEN
      NULL;                       -- the fixture rows are gone; the three verdicts are not
    WHEN OTHERS THEN
      RESET ROLE;
      RAISE EXCEPTION 'migration 46: could not arm the behavioural check: %', SQLERRM;
  END;

  IF NOT refused THEN
    RAISE EXCEPTION 'migration 46: brain_runtime moved a work item off a HELD project and was '
                    'not refused. The trigger is installed and does not hold.';
  END IF;
  n_checked := n_checked + 1;

  IF moved THEN
    RAISE EXCEPTION 'migration 46: the UPDATE raised and the row moved anyway.';
  END IF;
  n_checked := n_checked + 1;

  -- ---- and it lets the permitted direction through, or it is a wall and not a gate
  IF NOT filed THEN
    RAISE EXCEPTION 'migration 46: brain_runtime could not file work INTO a held project: %. '
                    'Filing work into a hold cannot escape it, so that direction must stay open '
                    'or `swarm project attach` and `post --project` both break.', filed_why;
  END IF;
  n_checked := n_checked + 1;

  IF EXISTS (SELECT 1 FROM brain.project WHERE slug = 'm46-selftest') THEN
    RAISE EXCEPTION 'migration 46: the self test left its fixture behind. The subtransaction did '
                    'not unwind and this store now carries a project nobody created.';
  END IF;

  IF n_checked <> 7 THEN                                             -- DENOMINATOR
    RAISE EXCEPTION 'migration 46: made % checks, expected 7. A verdict over a short set is not '
                    'a pass.', n_checked;
  END IF;
  RAISE NOTICE 'migration 46: 7 of 7 checks passed, including the refusal watched happening';
END $$;

INSERT INTO brain.schema_migration (version, name)
     VALUES (46, '0046_work_does_not_leave_a_resting_project')
  ON CONFLICT (version) DO NOTHING;

COMMIT;
