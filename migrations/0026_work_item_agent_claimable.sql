-- migration 26: the operator's own work stops being claimable by the fleet
--
-- Task 0414, posted by the commander as the one thing blocking the V1 cutover. Additive and
-- re-runnable: one column, two functions, two triggers, one view, four COMMENTs, and a
-- one-time backfill that names every row it raises. It drops nothing, edits no applied file
-- and changes no column type.
--
-- NOT YET APPLIED TO LIVE when this line was written. Migration 19 in this same directory
-- carries an "applied clean on live" sentence that was never true, so treat any such claim,
-- including the one T3 adds at the bottom of this file, as a thing to re-read from
-- `brain.schema_migration` before you rely on it.
--
-- ================================================================= WHAT WAS OPEN
--
-- `brain.work_item` is both the fleet's dispatch queue and the OPERATOR'S OWN QUEUE.
-- `engine/swarm_engine/transitions.py::claim` selected on `state='inbox'`, a lane match,
-- `_DEPS_MET`, the lane budget gate and `_NOT_HELD_BY_A_LIVE_AGENT`, and had NO ACTOR
-- PREDICATE. Every terminal in the shipped fleet config is `"lanes": ["*"]`. Measured on live
-- `brain` on 2026-08-18 before this file was written:
--
--   select count(*) from brain.work_item where state='inbox';                    -- 24
--   select count(*) from brain.work_item where state='inbox'
--                                          and posted_by='operator';             -- 15
--
-- Fifteen of the twenty-four rows the first post-cutover poll would have ranked are the
-- operator's own day: 0058..0073 less 0064. Among them 0061, a credential rotation,
-- 0066, a contract to send to a client, and 0069, a QC result to send to a client. Two rotate live credentials or send external
-- mail on the first agent that reads them.
--
-- ================================================================= WHY A NEW COLUMN AND NOT
-- ================================================================= `actor_type`
--
-- `actor_type` is the obvious reuse and it is WRONG HERE, on measurement rather than on taste.
-- Of the fifteen live operator rows, THIRTEEN carry `actor_type='human'` and TWO CARRY NULL:
--
--   0068  qc         Double-check the exported QC data against the warehouse   created 11:56:40.946
--   0071  marketing  Unify the multiple presentations and incorporate the ...  created 11:56:57.715
--
-- and they are not strays from some older era. 0067 (human), 0068 (NULL) and 0069 (human) were
-- created 11:56:40.224, 11:56:40.946 and 11:56:41.897 on 2026-08-17: one 1.7-second batch in
-- which the human mark was set, missed, and set again. The flag had ALREADY been forgotten
-- twice in the operator's real day load before anyone proposed depending on it. A predicate
-- reading `actor_type IS DISTINCT FROM 'human'` hands 0068 and 0069's own input row to the
-- fleet, and 0068 is the row 0069 mails to a client.
--
-- The deeper reason is in migration 1's own comment on the levelled signals: "unset is a real
-- state". `actor_type` is nullable by design, it records what an actor IS, and its NULL means
-- "nobody said". Claimability must have NO unset state: every row must answer the question, and
-- a row that has not been asked must answer NO.
--
-- `posted_by` was rejected because it is free text any caller sets and because a repost
-- reclassifies a row silently -- and the wrong answer here mails a contract. A declared
-- operator-lane set was rejected because it fails open the moment the operator invents a lane,
-- which is the same defect as narrowing `lanes` in the fleet config, moved one file over.
--
-- ================================================================= DEFAULT FALSE IS THE DESIGN
--
-- `NOT NULL DEFAULT false`. A row nobody has classified is the operator's.
--
-- The asymmetry is the whole point and it is deliberate: the flag that can be forgotten now
-- lives on the AGENT side of the fence. Forget it on a fleet task and the task sits in `inbox`,
-- visible in `swarm ls` as `[OPERATOR]`, reported by `swarm doctor` as posted-for-nobody, and
-- named by `swarm claim --explain`. Forget it on an operator task -- which is what actually
-- happened, twice, on 2026-08-17 -- and under the old shape an agent rotates a credential.
-- One of those two failures announces itself.
--
-- ================================================================= THE TWO TRIGGERS
--
-- 1. `work_item_human_is_never_agent_claimable` coerces `agent_claimable := false` on any row
--    that is `actor_type='human'`. Migration 20's COMMENT on `brain.current_human()` says a
--    second notion of who is human must never be built beside the first, "or the two drift and
--    the weaker one becomes the real policy". This is what stops the drift: the thirteen rows
--    already marked human are refused by BOTH predicates and cannot be un-refused by one.
--
-- 2. `work_item_agent_claimable_raise_is_a_human` refuses an UPDATE that moves an existing row
--    from false to true unless `brain.current_human()` is non-NULL. `brain_runtime` is the login
--    every agent surface connects as and it has no `brain.human_role` mapping, so an agent
--    cannot promote the operator's row into its own queue. Lowering (true -> false) is allowed
--    to everybody: taking work back from the fleet is the safe direction and refusing it would
--    make the operator ask an agent's permission to keep his own task.
--
--    DELIBERATELY NOT COVERED, and stated rather than hidden: an INSERT may carry `true` from
--    any login, because that is exactly what an agent posting fleet work does. So an agent can
--    create ITS OWN claimable row; it cannot make the operator's row claimable. The residual is
--    an agent that copies an operator task into a new row and works the copy. That is a forgery
--    of a different shape from this one and it is not closed here.
--
-- ================================================================= THE BACKFILL, WHICH INFERS
-- ================================================================= EXACTLY ONCE
--
-- Rows that predate this column carry no answer, and there is no evidence available except
-- `actor_type` and `posted_by`. So the backfill infers, once, in the fail-closed direction, and
-- prints every id it raises. After it runs nothing infers again: the column is authoritative and
-- a repost cannot reclassify anything, which is the objection that ruled `posted_by` out as the
-- ONGOING mechanism.
--
-- A row is raised to `true` only when BOTH hold:
--   * `actor_type IS DISTINCT FROM 'human'`, and
--   * `posted_by` is not the operator and is not empty.
-- On live `brain` at authoring time that is 18 rows of 34, of which 9 are in `inbox`
-- (0029 0030 0031 0032 0037 0074 0081 0082 0083) -- the adapter and queue backlog. Everything
-- else, including every row whose poster is unknown, stays false.

\set ON_ERROR_STOP on

BEGIN;

-- ---------------------------------------------------------------- the column

-- Added NULLABLE first ON PURPOSE. `ADD COLUMN ... NOT NULL DEFAULT <expr>` evaluates the
-- default ONCE, in the migration's own session, and writes that one answer to every existing
-- row -- so any default clever enough to read the writing login would stamp the whole table
-- with the migration runner's identity, and the operator's fifteen rows would land `true`.
-- Three statements, in this order, is the only shape that lets the backfill below decide.
ALTER TABLE brain.work_item ADD COLUMN IF NOT EXISTS agent_claimable boolean;

-- ---------------------------------------------------------------- the one-time backfill

DO $$
DECLARE
  raised   text;
  n_raised int;
  n_held   int;
BEGIN
  IF EXISTS (SELECT 1 FROM brain.work_item WHERE agent_claimable IS NULL) THEN
    UPDATE brain.work_item SET agent_claimable = false WHERE agent_claimable IS NULL;

    UPDATE brain.work_item
       SET agent_claimable = true
     WHERE agent_claimable = false
       AND actor_type IS DISTINCT FROM 'human'
       AND posted_by <> ''
       AND posted_by <> 'operator';

    SELECT count(*), string_agg(id, ' ' ORDER BY id) INTO n_raised, raised
      FROM brain.work_item WHERE agent_claimable;
    SELECT count(*) INTO n_held FROM brain.work_item WHERE NOT agent_claimable;
    RAISE NOTICE 'agent_claimable backfill: % row(s) raised to agent-claimable (%), % row(s) held for the operator',
                 n_raised, COALESCE(raised, '(none)'), n_held;
  END IF;
END $$;

ALTER TABLE brain.work_item ALTER COLUMN agent_claimable SET DEFAULT false;
ALTER TABLE brain.work_item ALTER COLUMN agent_claimable SET NOT NULL;

COMMENT ON COLUMN brain.work_item.agent_claimable IS
  'May a fleet agent take this row? Read by engine claim inside the same statement as its row '
  'lock. NOT NULL DEFAULT false: a row nobody classified is the operator''s, because a wrong '
  '"agent" rotates a live credential and mails a client while a wrong "human" is a row that '
  'sits still and is reported by ls, doctor and claim --explain. Not derived from actor_type: '
  'two of the operator''s fifteen live inbox rows carried actor_type NULL on 2026-08-18.';

-- The claim path already has a partial index on inbox. This one covers the guard so the
-- candidate scan after the cutover reads only the rows an agent may actually have.
CREATE INDEX IF NOT EXISTS work_item_agent_claimable_idx
  ON brain.work_item (lane, priority, created)
  WHERE state = 'inbox' AND agent_claimable;

-- ---------------------------------------------------------------- trigger 1: one notion of human

CREATE OR REPLACE FUNCTION brain.work_item_human_is_never_agent_claimable() RETURNS trigger
  LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.actor_type IS NOT DISTINCT FROM 'human' THEN
    NEW.agent_claimable := false;
  END IF;
  RETURN NEW;
END $$;

COMMENT ON FUNCTION brain.work_item_human_is_never_agent_claimable() IS
  'Coerces, never refuses. A human-actor row that arrives asking to be agent-claimable is a '
  'caller that has not thought about it, and the safe answer costs nothing. Refusing instead '
  'would make marking a row human fail on rows the fleet had already been offered, which is a '
  'reason not to mark it.';

DROP TRIGGER IF EXISTS work_item_human_is_never_agent_claimable ON brain.work_item;
CREATE TRIGGER work_item_human_is_never_agent_claimable
  BEFORE INSERT OR UPDATE ON brain.work_item
  FOR EACH ROW
  EXECUTE FUNCTION brain.work_item_human_is_never_agent_claimable();

-- ---------------------------------------------------------------- trigger 2: only a human raises

CREATE OR REPLACE FUNCTION brain.work_item_agent_claimable_raise_is_a_human() RETURNS trigger
  LANGUAGE plpgsql AS $$
BEGIN
  -- Only the transition false -> true is privileged. An unchanged value, and the lowering
  -- direction, return untouched -- including every ordinary claim, done, fail and reopen.
  IF NEW.agent_claimable IS NOT DISTINCT FROM OLD.agent_claimable THEN
    RETURN NEW;
  END IF;
  IF NOT NEW.agent_claimable THEN
    RETURN NEW;
  END IF;
  IF brain.current_human() IS NOT NULL THEN
    RETURN NEW;
  END IF;

  RAISE EXCEPTION 'refusing to make work item % agent-claimable: this connection is %, which is '
                  'not a human login', NEW.id, session_user
    USING HINT = 'This row was posted as the operator''s own work, or by a caller that named no '
                 'actor. Handing it to the fleet is a decision only he makes, because the rows '
                 'this protects rotate live credentials and send client mail. An agent that '
                 'believes a row is fleet work should POST one (an INSERT may carry '
                 'agent_claimable=true), not promote his. The operator raises one with '
                 '`swarm set <id> agent_claimable true --as-operator`, from a shell that holds '
                 'the operator credential -- the flag opens his login, it does not grant it.',
        ERRCODE = 'insufficient_privilege';
END $$;

COMMENT ON FUNCTION brain.work_item_agent_claimable_raise_is_a_human() IS
  'The claimability partition, enforced where it is computable. engine claim reads '
  'agent_claimable to decide whether dispatch may hand a row out; this is what stops the column '
  'being writable by the party the guard exists to constrain. Same shape and same primitive as '
  'migration 20''s work_item_human_actor_is_a_login, deliberately, so there is one notion of '
  'human in this schema and not two.';

DROP TRIGGER IF EXISTS work_item_agent_claimable_raise_is_a_human ON brain.work_item;
CREATE TRIGGER work_item_agent_claimable_raise_is_a_human
  BEFORE UPDATE ON brain.work_item
  FOR EACH ROW
  EXECUTE FUNCTION brain.work_item_agent_claimable_raise_is_a_human();

-- Not `BEFORE UPDATE OF agent_claimable`, for migration 20's reason: that form fires on the
-- columns a statement NAMES, so an UPDATE that sets every column from a record carries the
-- value past the narrow form. The unconditional trigger returns on its first line for every row
-- whose value is unchanged, which is every claim, done, fail, block and reopen in the system.

-- ---------------------------------------------------------------- the operator's own view

CREATE OR REPLACE VIEW brain.work_item_claimability AS
  SELECT w.id, w.lane, w.state, w.posted_by, w.title,
         w.agent_claimable,
         w.actor_type,
         CASE WHEN w.agent_claimable THEN 'fleet'
              WHEN w.actor_type IS NOT DISTINCT FROM 'human' THEN 'operator (human actor)'
              ELSE 'operator (unclassified, held by default)' END AS whose
    FROM brain.work_item w;

COMMENT ON VIEW brain.work_item_claimability IS
  'Which rows dispatch may hand out and which it may not, in one place, so the answer the '
  'operator reads is the same column claim reads rather than a second rendering of it. The '
  'third `whose` value is the one to look at: it is a row nobody has classified, and it is held.';

GRANT SELECT ON brain.work_item_claimability TO brain_runtime, brain_owner;

INSERT INTO brain.schema_migration (version, name) VALUES (26, '0026_work_item_agent_claimable')
  ON CONFLICT (version) DO NOTHING;

COMMIT;
