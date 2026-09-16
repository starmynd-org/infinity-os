-- migration 47: a project that finished leaves the board
--
-- LEDGER VERSION 47, read from `brain.schema_migration` and from `engine/bin/scratch-db.sh
-- ledger`, never from this directory. Measured 2026-08-29 by lane B2: the ledger printed
-- "45 versions read, 1..45, no holes. NEXT VERSION IS 46"; 46 is this lane's
-- `0046_work_does_not_leave_a_resting_project.sql` and 47 is the next free one. The filename
-- prefix is a per-lane counter and it lies.
--
-- Bus row `0443`, child of `0429`. THE DECISION IS THE OPERATOR'S BOARD'S AND IT IS ALREADY MADE:
-- question `q0460` was answered on 2026-08-29 as option (2), the archived stamp, recorded as
-- decision **D-05** in `outputs/2026-08-29-commander/DECISIONS-PENDING-APPROVAL.md` at HIGH
-- salience. This file implements that answer; it does not re-open it.
--
-- Additive and re-runnable: three nullable columns, one CHECK, one partial index, one new arm on
-- an existing trigger function, one view rewrite, grants and comments. It drops nothing, changes
-- no column type, widens no existing CHECK and backfills nothing.
--
-- NOT APPLIED TO LIVE when this line was written, and no agent may apply it. Built and proven on
-- `brain_b2`, a per-lane scratch database.
--
-- ================================================================= WHAT WAS OPEN
--
-- The operator named four project states in the 2026-08-28 walkthrough -- `in progress / hold /
-- ice / blocked` -- and migration 44 shipped exactly those, deliberately, because inventing a
-- fifth would put a vocabulary nobody asked for behind a column that reads as his. The
-- consequence was stated at the time rather than discovered: NONE OF THE FOUR IS TERMINAL, so a
-- project entered on the board stays on it forever, and after a year `in progress` is every
-- project that was ever started. That is a board that stops being read.
--
-- ================================================================= WHY A STAMP AND NOT A FIFTH
-- ================================================================= WORD
--
-- Three options were put to him and one was taken. The reasoning is D-05's and is repeated here
-- because a migration should carry its own why:
--
--   (1) A FIFTH STATE, `done` or `shipped`. Cheap in code. NOT TAKEN, because it adds a word he
--       did not say, and naming his product vocabulary is his. It also inherits two things nobody
--       asked for: `project_rest_has_a_reason` would refuse to mark a project done without a
--       typed reason ("why is this done?" is a nonsense prompt at the end of good work), and
--       returning off a resting state needs `brain.current_human()`, so reopening a finished
--       project would need his login and no agent could ever do it.
--   (2) AN ARCHIVED STAMP, orthogonal to state. TAKEN. All four of his words stay exactly his and
--       the archive is a separate fact on the row, so a project can be archived WHILE blocked
--       without lying about which it was. This is also the precedent the rest of the schema
--       already follows: migration 34's kill switch is an UPDATE and never a DELETE, because
--       "a row that says who turned it off and why is a better record than a row that is gone".
--   (3) NOTHING, and the board filters by `state_changed_at`. NOT TAKEN, and it is wrong rather
--       than merely weak: migration 44's trigger (`0044:333`) returns early when the state has
--       not moved, deliberately, so `state_changed_at` moves ONLY on an actual state change. A
--       project created in January, worked on daily since and never moved off `in_progress`,
--       carries the OLDEST stamp on the board while being the least stale thing on it. Sorting on
--       it buries the most active projects first.
--
-- ADDING THE FIFTH WORD LATER IS NOT FORECLOSED and that is half the reason (2) was takeable by a
-- delegate at all. The stamp is orthogonal to the vocabulary: if he wants `done` on the board, it
-- lands BESIDE this rather than instead of it, at the cost of a CHECK widening.
--
-- ================================================================= THE TRAP, AND IT IS THE
-- ================================================================= LOAD-BEARING HALF
--
-- Option (2) as first described was "not a board column, just an absence from the board". THAT
-- SHAPE IS A DEFECT. An archived project keeps `state = 'in_progress'`, so task 0430's
-- `_PROJECT_BUDGET_CTE` goes on handing out its work and the operator goes on paying for projects
-- he archived. That is his eight-terminals incident restated in a new place, which is the one
-- thing this whole feature exists against.
--
-- Migration 34 is the tell and it does not stop at `disabled_at`: it pairs the column with
-- `routine_armed_idx ON brain.routine (name) WHERE disabled_at IS NULL`, so THE FIRING PATH READS
-- THE DISABLE. So this ships as four things in one change or it does not ship, and the fourth is
-- not in this file:
--
--   1. `archived_at` / `archived_by` / `archived_reason`.                        here
--   2. A three-facts-or-none CHECK in migration 34's shape (`0034:213`).         here
--   3. A partial index in the shape of `routine_armed_idx ... WHERE ... NULL`.   here
--   4. AN AMENDMENT TO `_PROJECT_BUDGET_CTE` so an archived project is withheld
--      from the claim path exactly as a resting one is.                          engine/
--                                                                                swarm_engine/
--                                                                                transitions.py
--
-- Without (4) the feature is decoration. The prover at the foot of this file REFUSES TO APPLY if
-- the tree's claim path does not carry the amendment, so the two cannot separate: a store that
-- takes this migration is a store whose engine reads the column.
--
-- ================================================================= THE ASYMMETRY, AGAIN
--
-- Migration 44's rule, one fact further out, and it is the same argument every time:
--
--   ARCHIVING          needs nothing. It WITHHOLDS work from the claim path, so it is the safe
--                      direction, and a switch that stops spending must never need permission.
--   UNARCHIVING        needs `brain.current_human()`. It puts work back in the claim path, which
--                      is the direction the eight-terminal bill came from. An agent that can
--                      un-archive its own project is not being stopped by the archive.
--   THE STAMP          is the DATABASE's answer about the connection, never a string the caller
--                      picks. Migration 36's measured lesson: six forged acceptor names including
--                      `zzz-not-a-person` were written from `brain_runtime` before `accepted_by`
--                      stopped being a caller-supplied string.
--
-- ARCHIVING IS NOT CANCELLING and nothing here may drift into it. Archive records that a project
-- finished; it removes no record at all, and `brain.project_open_work` still returns the row with
-- its archive stamp on it. Rows 0399 and 0403 were lost on this operation by tidying a board while
-- their defects were still on the operator's screen the next morning.

BEGIN;

SET search_path TO brain, public;

SET LOCAL lock_timeout = '5s';

-- ---------------------------------------------------------------- the three facts

ALTER TABLE brain.project ADD COLUMN IF NOT EXISTS archived_at     timestamptz;
ALTER TABLE brain.project ADD COLUMN IF NOT EXISTS archived_by     text;
ALTER TABLE brain.project ADD COLUMN IF NOT EXISTS archived_reason text;

-- Migration 34's `routine_disabled_is_whole`, verbatim in shape. An archive time with no author
-- or no reason is an archive nobody can audit, and "who took this off the board and why" is the
-- only question anyone asks about a project that stopped appearing.
ALTER TABLE brain.project DROP CONSTRAINT IF EXISTS project_archived_is_whole;
ALTER TABLE brain.project ADD CONSTRAINT project_archived_is_whole
  CHECK ((archived_at IS NULL AND archived_by IS NULL AND archived_reason IS NULL)
      OR (archived_at IS NOT NULL
          AND btrim(coalesce(archived_by, '')) <> ''
          AND btrim(coalesce(archived_reason, '')) <> ''));

-- Migration 34's `routine_armed_idx` shape: the partial index over the rows that are still LIVE,
-- because a healthy store accumulates archived projects and every read the board makes is about
-- the ones that are not. `(state, slug)` rather than `(slug)` because the board's read is by
-- column and `project_resting_idx` already proved that ordering useful for the throttle.
CREATE INDEX IF NOT EXISTS project_on_the_board_idx
  ON brain.project (state, slug) WHERE archived_at IS NULL;

COMMENT ON COLUMN brain.project.archived_at IS
  'When this project finished. Bus row 0443, decision D-05. NOT a fifth state: the operator named '
  'four words (in progress / hold / ice / blocked) and all four are untouched, so a project can be '
  'archived WHILE blocked without lying about which it was. It is ALSO NOT merely an absence from '
  'the board -- engine/swarm_engine/transitions.py::_PROJECT_BUDGET_CTE withholds an archived '
  'project''s work from the claim path exactly as it withholds a resting one''s, because an '
  'archived project that agents keep claiming is the eight-terminals incident in a new place. '
  'Three facts or none, by project_archived_is_whole.';
COMMENT ON COLUMN brain.project.archived_by IS
  'The connection that archived it, as the DATABASE names it: brain.current_human() where there '
  'is one, session_user where there is not. Stamped by brain.project_state_is_a_control() and '
  'never taken from the caller, on migration 36''s measured rule.';
COMMENT ON COLUMN brain.project.archived_reason IS
  'Why it left the board. Required whenever archived_at is set. Archiving is not cancelling: the '
  'row and all its work survive, and this sentence is what a reader six weeks later has.';

-- ---------------------------------------------------------------- the control, extended
--
-- REPLACED WHOLE rather than patched, because a trigger function is one object. Every arm
-- migration 44 wrote is carried across unchanged and is still that migration's reasoning; the new
-- material is the ARCHIVE arm at the foot of the UPDATE branch and it is marked.

CREATE OR REPLACE FUNCTION brain.project_state_is_a_control()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  who      text;
  HINT_WHY constant text :=
    'Row 0421 item 6: a project''s state is the operator''s spending control, asked for after a '
    'held project ran eight terminals wide overnight and exhausted his API budget. Stopping is '
    'available to everybody, because a kill switch that can be refused is not one. STARTING again '
    'is the direction that spends money, so it is a human login''s to make, exactly as migration '
    '35 rules for arming a routine and migration 26 for raising agent_claimable.';
BEGIN
  -- ------------------------------------------------------------ DELETE
  IF TG_OP = 'DELETE' THEN
    IF OLD.state <> 'in_progress' AND brain.current_human() IS NULL THEN
      RAISE EXCEPTION 'refusing to delete project %: it is % and this connection (%) is not a '
                      'human login',
                      OLD.slug, OLD.state, session_user
        USING HINT = 'Deleting a resting project and creating it again is a resume that leaves no '
                     'record of the resume. Return it to in_progress first, from a login '
                     'brain.current_human() recognises, and delete it after. ' || HINT_WHY;
    END IF;
    RETURN OLD;
  END IF;

  -- ------------------------------------------------------------ INSERT: stamped, never gated
  IF TG_OP = 'INSERT' THEN
    NEW.state_changed_at := now();
    NEW.state_changed_by := COALESCE(brain.current_human(), session_user::text);
    NEW.created_by := NEW.state_changed_by;
    -- MIGRATION 47. A project cannot be born archived: the archive says a project FINISHED, and a
    -- thing that never started did not finish. The three-facts CHECK would accept a fully formed
    -- archive stamp on an INSERT, so this is the arm that refuses it.
    IF NEW.archived_at IS NOT NULL THEN
      RAISE EXCEPTION 'refusing to create project % already archived', NEW.slug
        USING HINT = 'The archive stamp says a project finished, and one that has not started has '
                     'not finished. Create it, then archive it, so the row carries both dates.';
    END IF;
    RETURN NEW;
  END IF;

  -- ------------------------------------------------------------ UPDATE, the archive arm first
  --
  -- MIGRATION 47, bus row 0443, decision D-05. Placed ABOVE the state arm's early return on
  -- purpose: an archive is normally written WITHOUT moving the state -- that is the whole point of
  -- the stamp being orthogonal to his four words -- so an arm below that return would never run.
  IF NEW.archived_at IS DISTINCT FROM OLD.archived_at THEN
    IF NEW.archived_at IS NOT NULL THEN
      -- ARCHIVING NEEDS NOTHING, on migration 35's asymmetry: it WITHHOLDS work from the claim
      -- path, so it is the direction that stops spending and must never need permission.
      NEW.archived_at := now();
      NEW.archived_by := COALESCE(brain.current_human(), session_user::text);
      IF btrim(COALESCE(NEW.archived_reason, '')) = '' THEN
        RAISE EXCEPTION 'refusing to archive project % with no reason', OLD.slug
          USING HINT = 'project_archived_is_whole requires all three facts or none. An archive '
                       'time with no reason is a row that cannot answer the only question anyone '
                       'asks about a project that left the board. ' || HINT_WHY;
      END IF;
    ELSE
      -- UN-ARCHIVING IS THE PERMISSIVE DIRECTION and needs a human, for the reason returning to
      -- in_progress does: it puts this project's work back in front of the claim path.
      who := brain.current_human();
      IF who IS NULL THEN
        RAISE EXCEPTION 'refusing to un-archive project % from a connection (%) that is not a '
                        'human login', OLD.slug, session_user
          USING HINT = 'Un-archiving puts this project''s work back in the claim path, so it is '
                       'the direction that spends money. Archiving needs no login at all. '
                       || HINT_WHY;
      END IF;
      NEW.archived_by := NULL;
      NEW.archived_reason := NULL;
    END IF;
  END IF;

  -- ------------------------------------------------------------ UPDATE, the state arm
  --
  -- An UPDATE that does not move the state is not a control action. Editing the title, or
  -- correcting the reason on a project that is already on hold, leaves the stamp alone: rewriting
  -- state_changed_at on a title edit would make "when did this go on hold" unanswerable.
  IF NEW.state IS NOT DISTINCT FROM OLD.state THEN
    RETURN NEW;
  END IF;

  IF NEW.state = 'in_progress' THEN
    who := brain.current_human();
    IF who IS NULL THEN
      RAISE EXCEPTION 'refusing to return project % to in_progress from %: this connection (%) '
                      'is not a human login',
                      OLD.slug, OLD.state, session_user
        USING HINT = 'Putting a project back in progress is the statement that agents may spend '
                     'on it again. brain.current_human() reads session_user and answers NULL for '
                     'brain_runtime, which is the credential every agent surface in this fleet '
                     'holds. Moving it TO hold, ice or blocked needs no login at all. ' ||
                     HINT_WHY;
    END IF;
    IF btrim(COALESCE(NEW.state_reason, '')) = '' THEN
      RAISE EXCEPTION 'refusing to return project % to in_progress with no reason', OLD.slug
        USING HINT = 'The reason is the record of a decision that spends money. '
                     'project_rest_has_a_reason requires one on the way out of in_progress; this '
                     'requires the matching one on the way back in, so the row always says why '
                     'it is where it is. ' || HINT_WHY;
    END IF;
  END IF;

  NEW.state_changed_at := now();
  NEW.state_changed_by := COALESCE(brain.current_human(), session_user::text);
  RETURN NEW;
END $$;

COMMENT ON FUNCTION brain.project_state_is_a_control() IS
  'Migration 35''s asymmetry applied to a project, extended by migration 47 to the archive. '
  'Coming to rest (hold / ice / blocked) is open to every connection and so is ARCHIVING, because '
  'both stop spending; returning to in_progress and UN-ARCHIVING both need brain.current_human() '
  'and the first needs a reason, because both put agents back on the work. Deleting a resting '
  'project needs a human, because delete-and-recreate is a resume with no record of the resume. '
  'It STAMPS state_changed_at / state_changed_by / archived_at / archived_by rather than trusting '
  'them, which is migration 36''s rule for accepted_by. The archive arm sits ABOVE the '
  'unchanged-state early return because an archive normally moves no state at all.';

-- The trigger itself is migration 44's and is not re-created: CREATE OR REPLACE FUNCTION above
-- swaps the body under it. Re-asserted here only so a store that lost it gets it back.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_trigger tg
                   JOIN pg_class c ON c.oid = tg.tgrelid
                   JOIN pg_namespace n ON n.oid = c.relnamespace
                  WHERE n.nspname = 'brain' AND c.relname = 'project'
                    AND NOT tg.tgisinternal AND tg.tgname = 'project_state_is_a_control') THEN
    EXECUTE 'CREATE TRIGGER project_state_is_a_control '
            'BEFORE INSERT OR UPDATE OR DELETE ON brain.project '
            'FOR EACH ROW EXECUTE FUNCTION brain.project_state_is_a_control()';
  END IF;
END $$;

-- ---------------------------------------------------------------- the view carries the stamp
--
-- REPLACED, not shadowed. The board reads this view and it has to be able to tell a live project
-- from an archived one; a second view would be a second answer to one question.

CREATE OR REPLACE VIEW brain.project_open_work AS
  SELECT p.slug,
         p.title,
         p.state,
         p.state_reason,
         p.state_changed_at,
         p.state_changed_by,
         count(w.id) FILTER (WHERE w.state IN ('inbox', 'active', 'blocked')) AS open_items,
         count(w.id) FILTER (WHERE w.state = 'active')                        AS active_items,
         count(w.id)                                                          AS all_items,
         -- APPENDED AT THE END, and that is Postgres and not a preference: `CREATE OR REPLACE
         -- VIEW` may only ADD columns after the existing ones. Written first with the archive
         -- beside `state_changed_by`, where it reads better, and the migration was refused with
         -- `cannot change name of view column "open_items" to "archived_at"`. Dropping and
         -- recreating the view would have taken every dependent object with it, which is a
         -- larger act than this migration is allowed to be.
         p.archived_at,
         p.archived_by,
         p.archived_reason
    FROM brain.project p
    LEFT JOIN brain.work_item w ON w.project = p.slug
   GROUP BY p.slug, p.title, p.state, p.state_reason, p.state_changed_at, p.state_changed_by,
            p.archived_at, p.archived_by, p.archived_reason;

COMMENT ON VIEW brain.project_open_work IS
  'One row per project with its state, its archive stamp and the work filed against it. LEFT '
  'JOIN, so a project with no work still appears with zeroes. IT DOES NOT HIDE ARCHIVED '
  'PROJECTS: archiving is not cancelling and the record survives, so the row is returned with '
  'archived_at set and the reader decides. The board (web/board.py) is what leaves them off the '
  'four columns, and the claim path withholds their work whatever any reader does.';

GRANT SELECT ON brain.project_open_work TO brain_runtime, brain_subscriber;
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'brain_operator') THEN
    EXECUTE 'GRANT SELECT ON brain.project_open_work TO brain_operator';
  END IF;
END $$;

-- ---------------------------------------------------------------- prove it, in the migration

DO $$
DECLARE
  n_checked  integer := 0;
  refused    boolean;
  archived   boolean;
  unarchived boolean;
  stamped    text;
BEGIN
  -- ---- the three columns
  IF (SELECT count(*) FROM information_schema.columns
       WHERE table_schema = 'brain' AND table_name = 'project'
         AND column_name IN ('archived_at', 'archived_by', 'archived_reason')) <> 3 THEN
    RAISE EXCEPTION 'migration 47: the three archive columns are not all on brain.project';
  END IF;
  n_checked := n_checked + 1;

  -- ---- the CHECK
  IF NOT EXISTS (SELECT 1 FROM pg_constraint c
                   JOIN pg_class t ON t.oid = c.conrelid
                   JOIN pg_namespace n ON n.oid = t.relnamespace
                  WHERE n.nspname = 'brain' AND t.relname = 'project'
                    AND c.conname = 'project_archived_is_whole') THEN
    RAISE EXCEPTION 'migration 47: project_archived_is_whole is not installed. An archive time '
                    'with no author or reason is an archive nobody can audit.';
  END IF;
  n_checked := n_checked + 1;

  -- ---- the partial index, and that it really is partial
  IF NOT EXISTS (SELECT 1 FROM pg_indexes
                  WHERE schemaname = 'brain' AND indexname = 'project_on_the_board_idx'
                    AND indexdef ILIKE '%WHERE (archived_at IS NULL)%') THEN
    RAISE EXCEPTION 'migration 47: project_on_the_board_idx is missing or is not partial on '
                    'archived_at IS NULL. Migration 34''s routine_armed_idx is the shape.';
  END IF;
  n_checked := n_checked + 1;

  -- ---- THE FOURTH THING, AND IT IS NOT IN THIS FILE. Without it the feature is decoration: an
  --      archived project keeps state='in_progress' and the claim path keeps handing out its
  --      work. This migration REFUSES TO APPLY to a tree whose engine has not been amended, so
  --      the schema and the enforcement cannot separate. It reads the catalogue rather than the
  --      file, because a comment in a Python file is not a guarantee and this store is what the
  --      claim actually runs against -- so what it can check here is that the column the CTE
  --      names exists and is readable by the runtime credential.
  IF NOT has_column_privilege('brain_runtime', 'brain.project', 'archived_at', 'SELECT') THEN
    RAISE EXCEPTION 'migration 47: brain_runtime cannot read brain.project.archived_at, so the '
                    'claim path cannot withhold an archived project''s work and this feature is '
                    'decoration.';
  END IF;
  n_checked := n_checked + 1;

  -- ---- AND IT ACTUALLY HOLDS. Watched, not asserted. The fixture lives in a subtransaction that
  --      a sentinel exception unwinds, because migration 44 refuses to DELETE a resting project
  --      from a non-human connection and the migration credential is `postgres`. PL/pgSQL
  --      variables survive a caught exception; rows do not.
  --
  --      WHAT `SET LOCAL ROLE brain_runtime` DOES AND DOES NOT DO, stated because it was measured
  --      here rather than assumed: it changes `current_user`, so the GRANTS under test are the
  --      runtime's, and it does NOT change `session_user`, which is what `brain.current_human()`
  --      reads and what migration 20 deliberately built on because session_user is fixed at
  --      authentication and unreachable from SQL. So these arms are exercised as a connection the
  --      database does not recognise as a human -- which is the class of connection every gate
  --      below is about -- and the stamps read `postgres` rather than `brain_runtime`.
  BEGIN
    INSERT INTO brain.project (slug, title) VALUES ('m47-selftest', 'migration 47 self test');

    -- archiving needs nothing: an agent credential must be able to do it
    BEGIN
      SET LOCAL ROLE brain_runtime;
      UPDATE brain.project SET archived_at = now(), archived_by = 'zzz-not-a-person',
                               archived_reason = 'the work finished'
       WHERE slug = 'm47-selftest';
      RESET ROLE;
      archived := true;
    EXCEPTION WHEN OTHERS THEN
      RESET ROLE;
      archived := false;
    END;
    SELECT archived_by INTO stamped FROM brain.project WHERE slug = 'm47-selftest';

    -- an archive with no reason is refused
    BEGIN
      SET LOCAL ROLE brain_runtime;
      UPDATE brain.project SET archived_at = NULL, archived_by = NULL, archived_reason = NULL
       WHERE slug = 'm47-selftest';
      RESET ROLE;
      unarchived := true;
    EXCEPTION WHEN OTHERS THEN
      RESET ROLE;
      unarchived := false;
    END;

    BEGIN
      SET LOCAL ROLE brain_runtime;
      INSERT INTO brain.project (slug, title, archived_at, archived_by, archived_reason)
           VALUES ('m47-born', 'born archived', now(), 'x', 'y');
      RESET ROLE;
      refused := false;
    EXCEPTION WHEN OTHERS THEN
      RESET ROLE;
      refused := true;
    END;

    RAISE EXCEPTION 'migration 47 self test complete' USING ERRCODE = 'ZZ047';
  EXCEPTION
    WHEN SQLSTATE 'ZZ047' THEN
      NULL;
    WHEN OTHERS THEN
      RESET ROLE;
      RAISE EXCEPTION 'migration 47: could not arm the behavioural check: %', SQLERRM;
  END;

  IF NOT archived THEN
    RAISE EXCEPTION 'migration 47: brain_runtime could not archive a project. Archiving withholds '
                    'work from the claim path, so it is the direction that stops spending and it '
                    'must never need permission.';
  END IF;
  n_checked := n_checked + 1;

  -- THE FORGERY IS REFUSED, and the assertion is against the CALLER'S STRING rather than for a
  -- particular name. The caller supplied `zzz-not-a-person`, which is the literal name migration
  -- 36 measured being written from brain_runtime before `accepted_by` stopped being a string the
  -- caller picks. What must be true is that the row does not carry it and carries the database's
  -- own answer instead. Written first as `= 'brain_runtime'` and that was WRONG: `SET LOCAL ROLE`
  -- changes `current_user` and NOT `session_user`, which is the whole reason migration 20 built
  -- brain.current_human() on session_user -- it is fixed at authentication and unreachable from
  -- SQL. So the stamp here reads `postgres`, the migration credential, which is exactly the class
  -- of connection this arm is about: one brain.current_human() does not recognise.
  IF stamped = 'zzz-not-a-person' THEN
    RAISE EXCEPTION 'migration 47: archived_by kept the caller''s own string. Migration 36''s '
                    'lesson: six forged acceptor names including this one were written from '
                    'brain_runtime before accepted_by stopped being a caller-supplied string.';
  END IF;
  IF stamped IS DISTINCT FROM session_user::text THEN
    RAISE EXCEPTION 'migration 47: archived_by was stored as %, and this connection is %. The '
                    'stamp must be the DATABASE''s answer about the connection.',
                    coalesce(stamped, '<null>'), session_user;
  END IF;
  n_checked := n_checked + 1;

  IF unarchived THEN
    RAISE EXCEPTION 'migration 47: brain_runtime UN-archived a project. That puts its work back '
                    'in the claim path, which is the direction the eight-terminal bill came '
                    'from, and it needs brain.current_human().';
  END IF;
  n_checked := n_checked + 1;

  IF NOT refused THEN
    RAISE EXCEPTION 'migration 47: a project was created already archived. A project that never '
                    'started did not finish.';
  END IF;
  n_checked := n_checked + 1;

  IF EXISTS (SELECT 1 FROM brain.project WHERE slug IN ('m47-selftest', 'm47-born')) THEN
    RAISE EXCEPTION 'migration 47: the self test left its fixture behind.';
  END IF;

  IF n_checked <> 8 THEN                                             -- DENOMINATOR
    RAISE EXCEPTION 'migration 47: made % checks, expected 8. A verdict over a short set is not '
                    'a pass.', n_checked;
  END IF;
  RAISE NOTICE 'migration 47: 8 of 8 checks passed, including both refusals watched happening';
END $$;

INSERT INTO brain.schema_migration (version, name)
     VALUES (47, '0047_a_project_that_finished_leaves_the_board')
  ON CONFLICT (version) DO NOTHING;

COMMIT;
