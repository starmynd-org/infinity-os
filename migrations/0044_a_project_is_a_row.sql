-- migration 44: a project is a row, not a text prefix
--
-- LEDGER VERSION 44, read from brain.schema_migration and not from this directory. Measured
-- 2026-08-28 before this file was named: live `brain` is at 42, `brain_demo` at 43 and
-- `brain_scratch` at 43. 39 is a hole lane E left, which migration 43 says is not ours to fill,
-- so 44 is the next free number and it is free on all three. Per queue/schema/0015's rule: pick
-- the version by READING the ledger, and scan all three schema directories as well, because the
-- filename prefix is a per-lane counter and it lies.
--
-- Bus row `0429`, which cites row `0421` item 6. Additive and re-runnable: one new table, one new
-- nullable column, one new FK, one new CHECK, one partial index, one trigger, one view, grants and
-- comments. It drops nothing, edits no applied file, changes no column type and BACKFILLS NOTHING.
--
-- NOT APPLIED TO LIVE when this line was written. Migration 19 in this directory carries an
-- "applied clean on live" sentence that was never true, so treat any such claim, including this
-- one, as a thing to re-read from `brain.schema_migration` before relying on it.
--
-- ================================================================= WHAT IS OPEN, AND THE BILL
--
-- Row `0421` item 6 is the operator's, from the 2026-08-28 demo walkthrough
-- (`outputs/2026-08-27-DEMO/OPERATOR-FEEDBACK.md:678`). He asked for a board view for projects
-- with `in progress / hold / ice / blocked`, and said what moving a project to hold has to DO:
--
--   "there's certain projects where I kind of just wanted the AI to take a break with it. And
--    then I realized later that it worked on it for like eight hours with eight terminals and I
--    ran out of API tokens very quickly."
--
-- That is an incident with a bill attached, not a preference, and it is what got prohibition 3
-- (no kanban) overruled for this one board on 2026-08-28. `web/MUST-NOT-BUILD.md` item 3 carries
-- the ruling and it also sets the build order, in as many words:
--
--   "Build order is still entity, then verb, then board: shipping the board over the string
--    prefix would render four columns that cannot hold anything, which is item 2's own
--    disabled-affordance failure."
--
-- THIS FILE IS THE ENTITY AND ONLY THE ENTITY. The verb and the board are separate rows.
--
-- ================================================================= WHY A TABLE AND NOT THE
-- ================================================================= PREFIX THAT WAS ALREADY THERE
--
-- Before this migration a project was a text prefix inside a nullable column:
-- `brain.work_item.canonical_task`, `migrations/0001_initial.sql:147`, shaped
-- `'<project-slug>#<task-id>'`, carrying `work_item_canonical_idx` and nothing else. There is no
-- row, so there is nothing to hold a state, so there is nothing for a board column to move and
-- nothing for a hold to be recorded ON.
--
-- MEASURED 2026-08-28, and it is worse than "weak entity": the prefix is EMPTY. `count(canonical_
-- task)` is 0 out of 345 work items in live `brain`, 0 of 57 in `brain_demo`, 0 of 4 in
-- `brain_scratch`. Three of three databases, 406 work items, zero carrying a project prefix. So
-- `web/app.py:813-817`, which resolves `/sprint/<slug>` with `canonical_task LIKE '<slug>#%'`,
-- renders an empty table for every slug that exists. A board over the prefix would not have shown
-- four empty columns; it would have shown no projects at all.
--
-- The useful consequence of that measurement is that THERE IS NOTHING TO BACKFILL. This migration
-- needs no data migration and no reconciliation pass, and that is a fact read off the store rather
-- than an assumption. A store that DOES carry prefixes gets no rows from this file either: the
-- table starts empty everywhere, on purpose, because inventing a project row out of a string is
-- exactly the guess this entity exists to stop.
--
-- `canonical_task` KEEPS ITS MEANING AND IS NOT REPURPOSED. Its own comment says it "Resolves to
-- the git planning ladder", and `adapter/brain_adapter/store_projection.py:478` calls it "the
-- column a foreign task id belongs in". A foreign id and a local entity are two different facts
-- and collapsing them would leave a work item unable to say both. What this file adds is a
-- constraint that stops them DISAGREEING; see `work_item_project_matches_canonical` below.
--
-- ================================================================= THE FOUR STATES ARE HIS WORDS
--
-- `in progress / hold / ice / blocked`, from the walkthrough, stored as `in_progress`, `hold`,
-- `ice`, `blocked`. The underscore is the only edit and it is migration 34's rule about slugs: a
-- value that has to be quoted is a value that will one day be quoted wrong. The display strings
-- are the operator's four and belong to whatever renders the board.
--
-- THERE IS NO FIFTH STATE AND NO TERMINAL ONE, deliberately. He named four; a `done` or
-- `archived` that nobody asked for would be a vocabulary this schema invented, and the first
-- reader to see it would treat it as his. The consequence is real and is stated rather than
-- hidden: a project never leaves the board on its own. That is the board's problem to solve, it
-- is posted as a child of row 0429, and it is not solved by guessing here.
--
-- ================================================================= THE ASYMMETRY, WHICH IS
-- ================================================================= MIGRATION 35'S AND NOT NEW
--
-- Migration 35 states the rule for routines and migration 26 states it for `agent_claimable`.
-- Same shape one level up:
--
--   MOVING A PROJECT TO REST     hold, ice or blocked. Needs NOTHING. A kill switch that can be
--                                refused is not a kill switch, and refusing it would make the
--                                operator ask an agent's permission to stop the agent.
--   MOVING BETWEEN REST STATES   hold -> ice. Needs nothing. It is still resting.
--   RETURNING TO `in_progress`   Needs a HUMAN LOGIN and a reason. This is the permissive
--                                direction: it is the statement that puts agents back on the
--                                work, and it is the direction the eight-terminal bill came
--                                from. An agent that can lift its own project's hold is not
--                                being held.
--   DELETING A RESTING PROJECT   Needs a human login. Otherwise delete-and-recreate is a resume
--                                with no record, which is the same escape by a different door.
--   CREATING ONE                 Needs nothing. A project that did not exist a moment ago
--                                throttled nothing, so its birth cannot RELEASE anything; it can
--                                only add a place for a throttle to live.
--
-- ONE NOTION OF HUMAN. `brain.current_human()`, migration 20, reading `session_user`, which is
-- fixed at authentication and unreachable from SQL. Migration 20's comment is the instruction:
-- never build a second notion of who is human beside the first, "or the two drift and the weaker
-- one becomes the real policy". Measured on this host: `brain.human_role` maps `brain_operator`
-- to `operator`, and `brain.current_human()` from `brain_runtime` -- the credential every agent
-- surface in this fleet holds -- returns NULL.
--
-- ================================================================= WHAT THIS DOES NOT CLOSE,
-- ================================================================= NAMED RATHER THAN DISCOVERED
--
--   * NOTHING THROTTLES YET. This is the entity. No reader anywhere consults `brain.project.state`
--     and the claim path does not know the column exists. Setting a project to `hold` today
--     records an intention and stops nothing. That is not a hidden gap: it is the build order
--     `web/MUST-NOT-BUILD.md` item 3 sets, and the reason the verb and the board are separate
--     rows is that the verb must not ship before the thing it promises. The enforcement path it
--     will extend already exists and is proven: `budget stop` takes a scope from
--     ["fleet","agent","lane","work_item"] and `_LANE_BUDGET_CTE`
--     (`engine/swarm_engine/transitions.py:146`) excludes stopped lanes from the claim path in
--     SQL by `NOT EXISTS`. The finding behind row 0429 is a GRAIN MISMATCH and not an absence: a
--     lane is a kind of work, so three projects in `exec` share one switch and a project spanning
--     two lanes has none. `project` is the fifth scope on a proven path, not a new mechanism.
--
--   * THE JOIN IS AN ESCAPE HATCH AND IT IS DELIBERATELY LEFT OPEN. `brain.work_item.project` is
--     writable by `brain_runtime`, because `post` is an agent verb and a task has to be able to
--     say which project it belongs to. So an agent can move a work item OFF a held project in one
--     UPDATE and out from under whatever throttle later reads this column. That hole is NOT
--     closed here on purpose: the right place to close it is where the throttle is defined, in
--     one predicate, and a second trigger on `brain.work_item` -- the hottest table in this store
--     -- bought before its consumer exists is a cost with no measurement behind it. It is carried
--     into the verb row's definition of done rather than left for someone to find.
--
--   * A HOST WITH NO OPERATOR CREDENTIAL. Where store/bin/provision-operator.sh has never run,
--     brain.current_human() answers NULL for every login including brain_owner, so a project that
--     is put to rest there can be neither resumed nor deleted. That is not an oversight and it is
--     not new: migration 35 refuses even the owner the right to arm a routine, on the deliberate
--     rule that there is ONE notion of human in this schema and not two, and store/session.py
--     says of that host "on that host nobody is the operator". The break-glass is to provision
--     the credential, not to add a second door. Watched failing, from brain_owner as well as from
--     brain_runtime, in engine/tests/test_project_entity.py.
--
--   * A RESTORE. Triggers are disabled during one, exactly as migration 22 already says. So is a
--     TRUNCATE, which is why `scratch-db.sh truncate` can empty this table between tests.
--
--   * THE CONSOLE. `web/app.py:813` still resolves `/sprint/<slug>` by the `canonical_task`
--     prefix and knows nothing about this table. `web/` is another lane's file. Nothing breaks:
--     that room reads a column this migration does not touch, and it rendered empty before this
--     file and renders exactly as empty after it.
--
-- ================================================================= ROLLBACK, STATED
--
--     BEGIN;
--       DROP VIEW    IF EXISTS brain.project_open_work;
--       DROP INDEX   IF EXISTS brain.work_item_project_idx;
--       ALTER TABLE  brain.work_item DROP CONSTRAINT IF EXISTS work_item_project_matches_canonical;
--       ALTER TABLE  brain.work_item DROP COLUMN IF EXISTS project;
--       DROP TRIGGER IF EXISTS project_state_is_a_control ON brain.project;
--       DROP FUNCTION IF EXISTS brain.project_state_is_a_control();
--       DROP TABLE   IF EXISTS brain.project;
--       DELETE FROM brain.schema_migration WHERE version = 44;
--     COMMIT;
--
-- `DROP COLUMN project` is the half that loses data, and on a store where anything has been filed
-- against a project it loses the only record of which. Read `SELECT count(project) FROM
-- brain.work_item` before running it and decide with the number in front of you.

\set ON_ERROR_STOP on

DO $$
DECLARE taken text;
BEGIN
  SELECT name INTO taken FROM brain.schema_migration WHERE version = 44;
  IF taken IS NOT NULL AND taken <> '0044_a_project_is_a_row' THEN
    RAISE EXCEPTION 'schema version 44 is already held by %, not 0044_a_project_is_a_row. '
                    'Renumber from brain.schema_migration, never from ls: this repo keeps '
                    'migrations in three directories, the filename prefix is a per-lane counter, '
                    'and on 2026-08-17 versions 19, 20 and 21 were ALL claimed by files applied '
                    'nowhere. Scan all three directories as well as the ledger.',
                    taken;
  END IF;
END $$;

BEGIN;

SET search_path TO brain, public;

-- FAIL FAST RATHER THAN QUEUE. Migration 13's reasoning, unchanged: the ALTER TABLE below takes
-- ACCESS EXCLUSIVE on `brain.work_item`, the table every claim locks rows in, and a lock request
-- that waits does not wait quietly.
SET LOCAL lock_timeout = '5s';

-- ---------------------------------------------------------------- the entity

CREATE TABLE IF NOT EXISTS brain.project (
  -- THE SLUG IS THE KEY, not a surrogate id, for three reasons that all point the same way: it is
  -- already the thing before the '#' in `canonical_task`, it is already the thing after
  -- `/sprint/` in the console's URL, and a foreign key spelled `w.project = p.slug` needs no join
  -- to read. Same regex as `brain.routine.name` (migration 34) so `swarm project hold infinity-os`
  -- needs no quoting, and it excludes '#' by construction, which is what keeps a slug from
  -- swallowing the separator `canonical_task` is built on.
  slug              text PRIMARY KEY
                      CHECK (slug ~ '^[a-z0-9][a-z0-9-]{0,62}$'),

  title             text NOT NULL CHECK (btrim(title) <> ''),

  -- The operator's four words. `in progress` is stored `in_progress`; see the header.
  state             text NOT NULL DEFAULT 'in_progress'
                      CHECK (state IN ('in_progress', 'hold', 'ice', 'blocked')),

  -- WHY THE REASON IS NOT OPTIONAL ON A RESTING PROJECT. `queue bump` already refuses a bump with
  -- no reason, and migration 34 refuses a disabled routine with no reason, on the same argument:
  -- a switch with no reason is a switch nobody can audit six weeks later, and the whole question
  -- anyone asks about a held project is who held it and why.
  state_reason      text NOT NULL DEFAULT '',

  -- Set by the trigger, never by the caller. Migration 36's measured lesson: six forged acceptor
  -- names including zzz-not-a-person were written from brain_runtime before `accepted_by` became
  -- the DATABASE's answer about the connection rather than a string the caller picks.
  state_changed_at  timestamptz NOT NULL DEFAULT now(),
  state_changed_by  text NOT NULL DEFAULT '',

  created_at        timestamptz NOT NULL DEFAULT now(),
  created_by        text NOT NULL DEFAULT '',

  CONSTRAINT project_rest_has_a_reason
    CHECK (state = 'in_progress' OR btrim(state_reason) <> '')
);

-- The board reads this order and the throttle reads the resting set. Partial, because a healthy
-- fleet has most projects in progress and the interesting scan is the short one.
CREATE INDEX IF NOT EXISTS project_resting_idx
  ON brain.project (state, slug) WHERE state <> 'in_progress';

COMMENT ON TABLE brain.project IS
  'One row per project. Bus row 0429, citing row 0421 item 6. Before migration 44 a project was a '
  'text prefix inside the nullable brain.work_item.canonical_task and there was no row to hold a '
  'state, so there was nothing for a board column to move and nothing for a hold to be recorded '
  'on. Starts EMPTY on every store and is backfilled from nothing: measured 2026-08-28, '
  'count(canonical_task) was 0 across 345 live work items, 57 demo and 4 scratch, so there was no '
  'prefix to promote. web/MUST-NOT-BUILD.md item 3 sets the build order this file is step one of: '
  'entity, then verb, then board.';
COMMENT ON COLUMN brain.project.slug IS
  'The project''s handle, and the same string that appears before the ''#'' in '
  'brain.work_item.canonical_task and after /sprint/ in the console URL. Primary key rather than '
  'a surrogate id so the join reads as w.project = p.slug. The regex excludes ''#'', which is '
  'what stops a slug from swallowing canonical_task''s separator.';
COMMENT ON COLUMN brain.project.state IS
  'The operator''s four words from the 2026-08-28 walkthrough: in progress / hold / ice / '
  'blocked. NOTHING READS THIS COLUMN YET. Setting a project to `hold` today records an intention '
  'and throttles no agent: the verb that will make it act, and the claim-path gate it extends '
  '(engine/swarm_engine/transitions.py:146, _LANE_BUDGET_CTE, which already excludes budget-'
  'stopped LANES by NOT EXISTS), are a separate bus row on purpose. Do not build a board over '
  'this column before that lands -- four columns that cannot hold anything is exactly the '
  'disabled-affordance failure web/MUST-NOT-BUILD.md item 2 names, and the incident behind the '
  'whole feature is an operator believing work was resting while it ran eight terminals wide.';
COMMENT ON COLUMN brain.project.state_reason IS
  'NOT NULL and non-empty whenever the project is not in progress, by project_rest_has_a_reason, '
  'and required by the trigger on the way BACK to in_progress as well. Both directions are '
  'consequential and both are decisions somebody has to be able to read later.';
COMMENT ON COLUMN brain.project.created_by IS
  'The connection that created the project, as the DATABASE names it, on the same rule as '
  'state_changed_by: brain.current_human() where there is one, session_user where there is not. '
  'OVERWRITTEN by the trigger rather than defaulted when blank, so the two `who` columns on this '
  'table are the same KIND of fact and a reader does not have to know which one is trustworthy.';
COMMENT ON COLUMN brain.project.state_changed_by IS
  'brain.current_human() where the connection is a human login, and session_user where it is not. '
  'Written by brain.project_state_is_a_control() and never by the caller, for migration 36''s '
  'reason: a name the caller picks is a name the caller can forge. A row stamped `brain_runtime` '
  'here is an agent moving a project to REST, which is permitted; a return to in_progress can '
  'only ever carry a human.';

-- ---------------------------------------------------------------- the control, as a trigger
--
-- Migration 35's asymmetry, one level up. The full table is in this file's header; the code below
-- is that table and nothing else. BEFORE, and on all three of INSERT / UPDATE / DELETE, because
-- two of the five rules are about the rows that do not survive the statement.

CREATE OR REPLACE FUNCTION brain.project_state_is_a_control() RETURNS trigger
  LANGUAGE plpgsql AS $$
DECLARE
  who text;
  HINT_WHY constant text :=
    'Row 0421 item 6: a project''s state is the operator''s spending control, asked for after a '
    'held project ran eight terminals wide overnight and exhausted his API budget. Stopping is '
    'available to everybody, because a kill switch that can be refused is not one. STARTING again '
    'is the direction that spends money, so it is a human login''s to make, exactly as migration '
    '35 rules for arming a routine and migration 26 for raising agent_claimable.';
BEGIN
  -- ------------------------------------------------------------ DELETE: not a silent resume
  --
  -- Deleting a resting project and recreating it is a return to `in_progress` with no record of
  -- the return, so it is gated the same way the return itself is. Deleting one that is already in
  -- progress releases nothing and is left alone. The FK from work_item is NO ACTION, so a project
  -- with work filed against it cannot be deleted at all until that work is unpointed -- which is
  -- the escape hatch this file's header names and does not close.
  IF TG_OP = 'DELETE' THEN
    IF OLD.state = 'in_progress' THEN
      RETURN OLD;
    END IF;
    who := brain.current_human();
    IF who IS NULL THEN
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
  --
  -- A project that did not exist a moment ago was throttling nothing, so its birth cannot release
  -- anything. It can be born resting, and `project_rest_has_a_reason` requires the reason when it
  -- is. `created_by` defaults to the same answer rather than to a caller-supplied string.
  IF TG_OP = 'INSERT' THEN
    NEW.state_changed_at := now();
    NEW.state_changed_by := COALESCE(brain.current_human(), session_user::text);
    -- OVERWRITTEN, not defaulted. Written first as "fill it in when the caller left it blank",
    -- and engine/tests/test_project_entity.py caught that on its first run: a caller supplying
    -- created_by = 'zzz-not-a-person' kept it. Two `who` columns side by side where one is the
    -- database's answer and the other is whatever was typed is worse than either alone, because
    -- nothing on the row tells a reader which is which. Same rule as state_changed_by, same
    -- reason, migration 36's.
    NEW.created_by := NEW.state_changed_by;
    RETURN NEW;
  END IF;

  -- ------------------------------------------------------------ UPDATE
  --
  -- An UPDATE that does not move the state is not a control action. Editing the title, or
  -- correcting the reason on a project that is already on hold, leaves the stamp alone: rewriting
  -- state_changed_at on a title edit would make "when did this go on hold" unanswerable.
  IF NEW.state IS NOT DISTINCT FROM OLD.state THEN
    RETURN NEW;
  END IF;

  -- The one gated direction. Everything else -- in_progress -> hold, hold -> ice, ice -> blocked
  -- -- falls straight through, because every one of them is a project coming to rest or staying
  -- there.
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
  'Migration 35''s asymmetry applied to a project. Coming to rest (hold / ice / blocked) is open '
  'to every connection; returning to in_progress needs brain.current_human() and a reason; '
  'deleting a resting project needs a human, because delete-and-recreate is a resume with no '
  'record of the resume. It also STAMPS state_changed_at and state_changed_by rather than '
  'trusting them, which is migration 36''s rule for accepted_by. It does not make `hold` throttle '
  'anything: no reader consults brain.project.state yet, and the verb that will is a separate bus '
  'row under 0429.';

DROP TRIGGER IF EXISTS project_state_is_a_control ON brain.project;
CREATE TRIGGER project_state_is_a_control
  BEFORE INSERT OR UPDATE OR DELETE ON brain.project
  FOR EACH ROW EXECUTE FUNCTION brain.project_state_is_a_control();

-- ---------------------------------------------------------------- the join
--
-- NULLABLE, and it stays nullable. Most work in this store is not project work -- 345 of 345 live
-- rows carry no project today -- and a NOT NULL here would have needed a fabricated default,
-- which is a project row invented out of nothing on every task the fleet posts.
--
-- ON UPDATE / ON DELETE are both left at NO ACTION, spelled out here because both defaults are
-- deliberate. A slug rename would have to be a considered act rather than a cascade, since the
-- same string is written into `canonical_task` prefixes and into console URLs that this FK cannot
-- reach; and a project with work filed against it should refuse deletion rather than orphan it.

ALTER TABLE brain.work_item
  ADD COLUMN IF NOT EXISTS project text REFERENCES brain.project(slug);

-- The two answers to "which project is this" must not disagree. `canonical_task` keeps its own
-- meaning -- a foreign task id on the git planning ladder -- and this constraint does not force
-- one to imply the other: either may be NULL and the row passes. It bites only when a row asserts
-- BOTH and they name different projects, which is the drift that would otherwise leave two
-- readers with two answers and no way to tell which is stale. split_part is IMMUTABLE, which is
-- what makes it legal in a CHECK at all.
ALTER TABLE brain.work_item DROP CONSTRAINT IF EXISTS work_item_project_matches_canonical;
ALTER TABLE brain.work_item ADD CONSTRAINT work_item_project_matches_canonical
  CHECK (project IS NULL
      OR canonical_task IS NULL
      OR split_part(canonical_task, '#', 1) = project);

-- (project, state) rather than (project): every read this column exists for is per-project counts
-- BY state -- the board's columns, and "how much is actually running under this hold". Partial,
-- because the column is NULL on essentially every row and indexing those buys nothing.
CREATE INDEX IF NOT EXISTS work_item_project_idx
  ON brain.work_item (project, state) WHERE project IS NOT NULL;

COMMENT ON COLUMN brain.work_item.project IS
  'The project this work belongs to, as a foreign key into brain.project (migration 44, bus row '
  '0429). NOT the same fact as canonical_task, which is a foreign task id on the git planning '
  'ladder; work_item_project_matches_canonical stops the two DISAGREEING without making either '
  'imply the other. Writable by brain_runtime because `post` is an agent verb, and that is a '
  'known escape hatch from any future per-project throttle: an agent can move a row off a held '
  'project in one UPDATE. Closing it belongs with the throttle, in one predicate, not in a second '
  'trigger on this table bought before its consumer exists.';

-- ---------------------------------------------------------------- the denominator, as a view
--
-- Not a policy view and deliberately not named one. It says which projects are at rest and how
-- much work is under each, which is the number any claim about a throttle has to be made against:
-- "this project is on hold" is a state, and "and 14 items are still active under it" is the
-- measurement that says whether the hold did anything. WHICH resting states throttle is the verb
-- row's decision and this view does not pre-empt it -- it reports all four.
CREATE OR REPLACE VIEW brain.project_open_work AS
  SELECT p.slug,
         p.title,
         p.state,
         p.state_reason,
         p.state_changed_at,
         p.state_changed_by,
         count(w.id) FILTER (WHERE w.state IN ('inbox', 'active', 'blocked')) AS open_items,
         count(w.id) FILTER (WHERE w.state = 'active')                        AS active_items,
         count(w.id)                                                          AS all_items
    FROM brain.project p
    LEFT JOIN brain.work_item w ON w.project = p.slug
   GROUP BY p.slug, p.title, p.state, p.state_reason, p.state_changed_at, p.state_changed_by;

COMMENT ON VIEW brain.project_open_work IS
  'One row per project with its state and the work filed against it. LEFT JOIN, so a project with '
  'no work still appears with zeroes -- a board that dropped empty projects would hide exactly '
  'the ones somebody just created. `open_items` counts inbox, active and blocked, which is every '
  'non-terminal work_item state. Reports all four project states rather than a throttled subset: '
  'the operator asked for `hold` to throttle and said nothing about `ice` or `blocked`, and '
  'deciding that here would put a policy nobody chose behind a name that reads like a fact.';

-- ---------------------------------------------------------------- grants
--
-- brain_runtime gets SELECT, INSERT and UPDATE and NOT DELETE. It has to read the table (any
-- future claim gate), attach work to a project, and bring a project to REST -- all three are the
-- open directions. Removing a project outright is the one destructive verb here and it stays with
-- the owner and the operator; the trigger's DELETE arm is the second gate and neither trusts the
-- other.

GRANT SELECT, INSERT, UPDATE ON brain.project           TO brain_runtime;
GRANT SELECT                  ON brain.project_open_work TO brain_runtime;
GRANT SELECT                  ON brain.project, brain.project_open_work TO brain_subscriber;

-- Conditional for the reason migrations 23, 25, 29 and 34 are: `brain_operator` exists only on a
-- host where store/bin/provision-operator.sh has run.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'brain_operator') THEN
    EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON brain.project TO brain_operator';
    EXECUTE 'GRANT SELECT ON brain.project_open_work TO brain_operator';
  END IF;
END $$;

-- ---------------------------------------------------------------- prove it, in the migration
--
-- Migration 13's rule: a migration that says it created a thing and did not is exactly the class
-- of thing this store's checks exist to catch, so it catches itself. Every verdict below carries
-- the count of things it actually compared, and a denominator of zero raises rather than passes.

DO $$
DECLARE
  def       text;
  missing   text;
  n_states  integer;
  n_checked integer := 0;
BEGIN
  -- ---- the four states are in the CHECK, counted rather than eyeballed
  SELECT pg_get_constraintdef(c.oid) INTO def
    FROM pg_constraint c
    JOIN pg_class     t ON t.oid = c.conrelid
    JOIN pg_namespace n ON n.oid = t.relnamespace
   WHERE n.nspname = 'brain' AND t.relname = 'project' AND c.conname = 'project_state_check';

  IF def IS NULL THEN
    RAISE EXCEPTION 'migration 44: brain.project has no state CHECK after this migration';
  END IF;

  SELECT count(*), string_agg(w, ', ') FILTER (WHERE def NOT LIKE '%''' || w || '''%')
    INTO n_states, missing
    FROM unnest(ARRAY['in_progress', 'hold', 'ice', 'blocked']) AS w;

  IF n_states <> 4 THEN                                              -- DENOMINATOR
    RAISE EXCEPTION 'migration 44: compared % state words, expected 4. A verdict over an empty '
                    'set is not a pass.', n_states;
  END IF;
  IF missing IS NOT NULL THEN
    RAISE EXCEPTION 'migration 44: the state CHECK is missing: %. Definition is %', missing, def;
  END IF;
  RAISE NOTICE 'migration 44: state vocabulary ok, 4 of 4 words present in %', def;
  n_checked := n_checked + 4;

  -- ---- the join column, its foreign key, its coherence check and its index
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                  WHERE table_schema = 'brain' AND table_name = 'work_item'
                    AND column_name = 'project') THEN
    RAISE EXCEPTION 'migration 44: brain.work_item.project was not added';
  END IF;
  n_checked := n_checked + 1;

  IF NOT EXISTS (SELECT 1 FROM pg_constraint c
                   JOIN pg_class t ON t.oid = c.conrelid
                   JOIN pg_namespace n ON n.oid = t.relnamespace
                   JOIN pg_class r ON r.oid = c.confrelid
                  WHERE n.nspname = 'brain' AND t.relname = 'work_item'
                    AND c.contype = 'f' AND r.relname = 'project') THEN
    RAISE EXCEPTION 'migration 44: brain.work_item.project has no foreign key to brain.project. '
                    'ADD COLUMN IF NOT EXISTS skips the REFERENCES clause when the column is '
                    'already there, so a store that gained the column some other way lands here.';
  END IF;
  n_checked := n_checked + 1;

  IF NOT EXISTS (SELECT 1 FROM pg_constraint c
                   JOIN pg_class t ON t.oid = c.conrelid
                   JOIN pg_namespace n ON n.oid = t.relnamespace
                  WHERE n.nspname = 'brain' AND t.relname = 'work_item'
                    AND c.conname = 'work_item_project_matches_canonical') THEN
    RAISE EXCEPTION 'migration 44: work_item_project_matches_canonical is not installed';
  END IF;
  n_checked := n_checked + 1;

  IF NOT EXISTS (SELECT 1 FROM pg_indexes
                  WHERE schemaname = 'brain' AND indexname = 'work_item_project_idx') THEN
    RAISE EXCEPTION 'migration 44: work_item_project_idx is not installed';
  END IF;
  n_checked := n_checked + 1;

  -- ---- the control is actually ON the table, not merely defined
  --
  -- A CREATE FUNCTION with no trigger behind it is the shape of every guard this repo has found
  -- claiming to hold something it never saw.
  IF NOT EXISTS (SELECT 1 FROM pg_trigger tg
                   JOIN pg_class c ON c.oid = tg.tgrelid
                   JOIN pg_namespace n ON n.oid = c.relnamespace
                  WHERE n.nspname = 'brain' AND c.relname = 'project'
                    AND NOT tg.tgisinternal
                    AND tg.tgname = 'project_state_is_a_control') THEN
    RAISE EXCEPTION 'migration 44: project_state_is_a_control is not on brain.project';
  END IF;
  n_checked := n_checked + 1;

  IF position('current_human' in
              pg_get_functiondef('brain.project_state_is_a_control()'::regprocedure)) = 0 THEN
    RAISE EXCEPTION 'migration 44: project_state_is_a_control() does not consult '
                    'brain.current_human(), so the permissive direction is ungated';
  END IF;
  n_checked := n_checked + 1;

  IF to_regclass('brain.project_open_work') IS NULL THEN
    RAISE EXCEPTION 'migration 44: brain.project_open_work is not installed';
  END IF;
  n_checked := n_checked + 1;

  IF n_checked <> 11 THEN                                            -- DENOMINATOR
    RAISE EXCEPTION 'migration 44: made % checks, expected 11. A verdict over a short set is not '
                    'a pass.', n_checked;
  END IF;
  RAISE NOTICE 'migration 44: 11 of 11 structural checks passed';
END $$;

INSERT INTO brain.schema_migration (version, name)
     VALUES (44, '0044_a_project_is_a_row')
  ON CONFLICT (version) DO NOTHING;

COMMIT;
