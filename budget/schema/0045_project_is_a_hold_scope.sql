-- migration 45: a project is a scope a hold can name
--
-- Task 0430 (Sprint P2), depends on 0429 (Sprint P1). Additive, re-runnable, and it changes no
-- existing row in any table.
--
-- ================================================================= the incident, in his words
--
-- web/MUST-NOT-BUILD.md item 3 was overruled by the operator on 2026-08-28 against this:
--
--     "there's certain projects where I kind of just wanted the AI to take a break with it. And
--      then I realized later that it worked on it for like eight hours with eight terminals and
--      I ran out of API tokens very quickly."
--
-- A latched hold already exists and is already enforced. `budget stop` writes a `manual_stop`
-- that only `budget resume` lifts, and `_LANE_BUDGET_CTE` (engine/swarm_engine/transitions.py)
-- excludes stopped lanes from the claim path in SQL, so a stopped lane is never handed out and
-- nothing is released and no attempt is charged. Latching, the incident record, the resume-only
-- exit and the enforcement are all built and all proven by engine/tests/test-lane-budget-gate.sh.
--
-- WHAT WAS MISSING IS THE SCOPE, and it is a grain mismatch rather than an absence. The scopes
-- were fleet, agent, lane and work_item. A LANE IS NOT A PROJECT: lanes are kinds of work, so
-- three projects running in `exec` shared one switch and a project spanning two lanes had none.
-- The control existed at every grain except his. This file adds his grain and nothing else.
--
-- ================================================================= the two objects
--
-- 1. `project` joins brain.budget_scope, so `budget stop project <slug>` can be typed at all.
-- 2. `brain.work_item_project(brain.work_item)` answers "which project is this row in", ONCE,
--    for every reader. That function is the seam with task 0429 and is discussed at length below.
--
-- And one refusal, which is the part that keeps the new scope from lying: brain.budget_policy is
-- given a CHECK that physically cannot hold a project CEILING. See "the meterless scope" below.
--
-- ================================================================= ALTER TYPE in a transaction
--
-- `ALTER TYPE ... ADD VALUE` inside BEGIN/COMMIT is allowed from PostgreSQL 12 on (this store is
-- 16.10) with one restriction: THE NEW VALUE MAY NOT BE USED IN THE SAME TRANSACTION. Migration
-- 21 sidestepped that by not using its new value at all. This file does use it, twice, so both
-- uses are written against `scope::text` rather than against the enum:
--
--     CASE scope::text WHEN 'project' THEN ...        not   CASE scope WHEN 'project'
--     CHECK (scope_type::text <> 'project')           not   CHECK (scope_type <> 'project')
--
-- With the cast, 'project' is a text literal the parser never coerces to brain.budget_scope, so
-- neither statement is an "unsafe use of new value" and the BEGIN/COMMIT shape every migration in
-- this tree carries is kept rather than special-cased. Migration 21 set that precedent for the
-- CHECK form: `CHECK (kind::text <> 'meter_mismatch' OR ...)`.
--
-- The same cast is what lets every READER of these tables (budget/reads.py, the claim CTE) ask
-- about scope_type = 'project' against a store where this migration has NOT been applied. An enum
-- literal there would not return empty, it would RAISE `invalid input value for enum
-- brain.budget_scope: "project"`, and in the claim statement that is the entire fleet unable to
-- take work because a budget migration is one version behind. Casting is not a style choice here.
--
-- ================================================================= the meterless scope
--
-- brain.budget_charge carries dimensions, not scopes: `agent`, `lane`, `work_item_id`. IT CARRIES
-- NO PROJECT. So `brain.budget_charge_in_scope` cannot roll a dollar up into a project, and a
-- project row in `brain.budget_state` would report spend_usd = 0.00 for every window, forever,
-- against whatever ceiling the operator set. `over_limit` false, `stopping` false, no warn, no
-- breach. A ceiling that never fires and always looks healthy.
--
-- That is the exact failure this program calls tolerance-widening, arriving by construction
-- instead of by edit: a check whose comparison set is empty reads as a pass. So this migration
-- refuses to let the state exist:
--
--     brain.budget_policy gains  CHECK (scope_type::text <> 'project')
--
-- A project CEILING cannot be stored, by any code path, however that code is written. It is the
-- same argument as `cause = 'budget'` on budget_incident, which migration 3 says is the property
-- that whole table exists for: make the lie unstorable rather than discouraged.
--
-- A project HOLD is untouched by this and is the whole point of the file. `budget stop` writes to
-- budget_incident, which has no such CHECK, needs no policy and has no spend basis -- migration 3
-- built it that way on purpose ("`budget stop --scope lane` needs no policy and has no spend, so
-- it appears in NEITHER the meter nor `budget_state`"). The hold is what the operator asked for.
-- The ceiling is not, and would have been a dashboard reading zero.
--
-- TO LIFT THE REFUSAL LATER, and it is two changes and not a licence: give brain.budget_charge a
-- project dimension (or teach budget_charge_in_scope a lateral lookup through work_item_id), THEN
-- drop this constraint in the same migration. Never the second without the first.
--
-- ================================================================= REQUIRES MIGRATION 44
--
-- This file depends on `migrations/0044_a_project_is_a_row.sql` (bus row 0429, Sprint P1) and
-- says so with a hard RAISE below rather than degrading quietly. 44 creates `brain.project` and
-- the join `brain.work_item.project text REFERENCES brain.project(slug)`; without them the
-- function defined here has nothing to read and the claim-path gate has nothing to anti-join
-- against. `engine/bin/scratch-db.sh` orders by the version each file records, so 44 lands first
-- on every store it builds.
--
-- WHY NOT THE canonical_task PREFIX, which is what this file was first written against. Measured
-- 2026-08-28 before 44 existed: `brain.work_item.canonical_task` is non-empty on 0 of 345 rows on
-- live `brain`, and task 0429 re-measured 0 of 406 across three databases. A gate over
-- `split_part(canonical_task, '#', 1)` would compute an empty project for every row in every
-- database, exclude nothing, forever, and report success -- a check whose comparison set is zero
-- reading as a pass. The prefix is not a weak source. It is an empty one.
--
-- 44's `work_item_project_matches_canonical` is what makes reading ONLY `w.project` safe: a row
-- may carry either, or neither, but if it carries both they must name the same project. So there
-- is no second answer this function could be missing.
--
-- IT TAKES THE WHOLE ROW, not `w.project`. A `(text)` signature would pin every call site to
-- today's column name; a `(brain.work_item)` signature lets the body follow the source if it
-- moves again, with one CREATE OR REPLACE and no caller edited. Adding a column to
-- brain.work_item does not invalidate it: the function is resolved by name.
--
-- WHAT A GREEN SUITE HERE DOES AND DOES NOT SAY, because the two are easy to conflate. What is
-- proven is the MECHANISM: engine/tests/test-project-budget-gate.sh creates real projects, files
-- real work against them, holds them, and measures that `claim` does not hand those rows out.
-- What is NOT proven is anything about live data, because there is none -- `brain.project` starts
-- empty on every store, by measurement rather than by choice. The gate is armed and the world is
-- empty. Those are separate facts.
--
-- IT IS STABLE, NOT IMMUTABLE. Reading its argument's columns would be immutable, and today that
-- is all it does. It is declared STABLE anyway, because 0429's version joins brain.project and a
-- table read inside an IMMUTABLE function is a lie the planner is entitled to cache. Declaring
-- the weaker volatility now means re-pointing the body is a `CREATE OR REPLACE` and not a DROP
-- (which would fail: the claim path and budget/reads.py both depend on it). STABLE still inlines
-- inside a query, so the claim pays nothing for the choice.

\set ON_ERROR_STOP on

BEGIN;

SET search_path TO brain, public;

-- Version 45 is claimed here. 0429 took 44 (its 17:19Z measurement: live `brain` was at 42,
-- brain_demo and brain_scratch at 43, and version 39 is lane E's hole). Filenames are not
-- versions in this tree and gaps are normal: read brain.schema_migration, never `ls`.
DO $$
DECLARE existing text;
BEGIN
  SELECT name INTO existing FROM brain.schema_migration WHERE version = 45;
  IF existing IS NOT NULL AND existing <> '0045_project_is_a_hold_scope' THEN
    RAISE EXCEPTION 'schema version 45 is already applied as %, not 0045_project_is_a_hold_scope. '
                    'Renumber this migration rather than applying it over another lane''s.', existing;
  END IF;
END $$;

-- THE DEPENDENCY, CHECKED RATHER THAN ASSUMED. Named as two separate conditions because they fail
-- for different reasons and the remedy differs: a missing table means 44 was never applied, and a
-- missing column on a table that exists means 44 was applied to a store where `ADD COLUMN IF NOT
-- EXISTS` found a `project` column already there under another definition -- which 44's own
-- verification block warns about at its foot.
DO $$
BEGIN
  IF to_regclass('brain.project') IS NULL THEN
    RAISE EXCEPTION 'migration 45 requires migration 44: brain.project does not exist'
      USING HINT = 'Apply migrations/0044_a_project_is_a_row.sql first. A project hold has '
                   'nothing to hold without the row that carries a project''s state. '
                   '`engine/bin/scratch-db.sh ledger` prints the order.';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                  WHERE table_schema = 'brain' AND table_name = 'work_item'
                    AND column_name = 'project') THEN
    RAISE EXCEPTION 'migration 45 requires migration 44: brain.work_item has no project column'
      USING HINT = 'brain.project exists but the join to work_item does not, so no work item can '
                   'be attributed to a project and every hold would withhold nothing. Re-read '
                   'migrations/0044_a_project_is_a_row.sql before applying this.';
  END IF;
END $$;

-- ---------------------------------------------------------------- the scope
--
-- IF NOT EXISTS makes the file re-runnable, which is what the scratch database builder and every
-- rerun of the suite depend on.
ALTER TYPE brain.budget_scope ADD VALUE IF NOT EXISTS 'project';

-- ---------------------------------------------------------------- which project is a row in
--
-- The seam. Read the "seam with task 0429" section above before changing this body; the argument
-- for its signature and its volatility is there and is not restated here.
--
-- ONE COLUMN, NOT A COALESCE OVER TWO. `coalesce(w.project, split_part(w.canonical_task,'#',1))`
-- is the tempting body and it is wrong: it would answer for rows whose `canonical_task` names a
-- project that has no `brain.project` row, so a hold typed against that slug would be enforced
-- against work the operator cannot see on any board and cannot resume from one. 44's
-- `work_item_project_matches_canonical` already guarantees the two can never DISAGREE, so reading
-- only the foreign key loses no correct answer -- it only declines to invent projects that do not
-- exist as rows.
--
-- NULL for a row in no project, and that needs no special case anywhere: NULL never equals a
-- stopped scope_id, so the anti-join in the claim path lets the row through, which is right. It
-- is also the common case by a wide margin -- 0 of 345 live rows carried a project on 2026-08-28.
CREATE OR REPLACE FUNCTION brain.work_item_project(w brain.work_item)
  RETURNS text LANGUAGE sql STABLE AS $$
  SELECT w.project
$$;

COMMENT ON FUNCTION brain.work_item_project(brain.work_item) IS
  'Which project a work item belongs to, defined ONCE for every reader: the claim path''s project '
  'hold (engine/swarm_engine/transitions.py), budget/reads.py, and any board. It reads '
  'brain.work_item.project, the foreign key migration 44 added, and NOT the canonical_task '
  'prefix, which was measured empty on 2026-08-28 (0 of 345 rows on brain, 0 of 406 across three '
  'databases) and which 44''s work_item_project_matches_canonical already keeps from disagreeing '
  'with this column. Do not re-derive a project anywhere else: a second definition is how a hold '
  'and a board come to disagree about what is held, which is the incident this whole feature was '
  'built against.';

-- ---------------------------------------------------------------- the meter cannot be faked
--
-- Two halves, and neither is sufficient alone.
--
-- FIRST, the function is made TOTAL. Its CASE had no ELSE, so on an unrecognised scope it returned
-- NULL, and NULL in the view's LATERAL `WHERE brain.budget_charge_in_scope(...)` filters every
-- charge out. Adding an enum value without touching this function would therefore have produced a
-- project policy metering 0.00 in silence -- the failure arriving through the exact door the enum
-- comment in migration 3 warns about ("a typo in a text column becomes a branch that silently
-- never fires, and the branch that never fires here is the one that stops spending").
--
-- 'project' answers FALSE rather than NULL. Not because false is right -- a project's charges do
-- exist, budget_charge simply has no column to recognise them by -- but because false is READABLE:
-- a scope that matches no charge meters exactly 0.00, which is a number an operator can see is
-- wrong, where NULL is an empty result set that looks like an idle project.
--
-- SECOND, and this is the half that matters, the state is made UNREACHABLE below, so this arm is
-- a backstop that nothing can call rather than a behaviour anyone relies on. `CASE scope::text`
-- rather than `CASE scope` is required, not stylistic: see "ALTER TYPE in a transaction" above.
CREATE OR REPLACE FUNCTION brain.budget_charge_in_scope(
    scope brain.budget_scope, scope_id text,
    c_agent text, c_lane text, c_work_item text)
  RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE scope::text
           WHEN 'fleet'     THEN true
           WHEN 'agent'     THEN c_agent = scope_id
           WHEN 'lane'      THEN c_lane = scope_id
           WHEN 'work_item' THEN c_work_item = scope_id
           WHEN 'project'   THEN false
           ELSE false
         END
$$;

-- A project CEILING is unstorable. The argument is in "the meterless scope" above; the short
-- version is that brain.budget_charge has no project dimension, so a project ceiling would report
-- 0.00 spend forever and never fire. NOT VALID then VALIDATE, following migration 21: the table
-- can hold no such row yet (the enum value did not exist a moment ago), so validation is a
-- formality, and doing it in two steps keeps the pattern honest against a database that somehow
-- does. `state = 'retired'` is not exempted -- a retired project ceiling would be an artefact of
-- a state this constraint says never existed.
ALTER TABLE brain.budget_policy
  ADD CONSTRAINT budget_policy_no_project_ceiling
  CHECK (scope_type::text <> 'project') NOT VALID;

ALTER TABLE brain.budget_policy VALIDATE CONSTRAINT budget_policy_no_project_ceiling;

COMMENT ON CONSTRAINT budget_policy_no_project_ceiling ON brain.budget_policy IS
  'A project HOLD is built and enforced at the claim; a project CEILING is not, because '
  'brain.budget_charge carries agent/lane/work_item and no project, so the spend would meter '
  '0.00 forever against any limit. Refusing the row is fail-closed. To lift this: give '
  'budget_charge a project dimension FIRST, then drop this constraint in that same migration.';

-- ---------------------------------------------------------------- grants
--
-- brain_runtime is the role the claim path and budget/reads.py connect as, and both call
-- work_item_project. Without this grant the project arm of the claim raises permission denied
-- inside the statement that hands out every task, which is the fleet, not a budget.
GRANT EXECUTE ON FUNCTION brain.work_item_project(brain.work_item) TO brain_runtime;
GRANT EXECUTE ON FUNCTION brain.work_item_project(brain.work_item) TO brain_owner;

INSERT INTO brain.schema_migration (version, name)
VALUES (45, '0045_project_is_a_hold_scope')
ON CONFLICT (version) DO NOTHING;

COMMIT;
