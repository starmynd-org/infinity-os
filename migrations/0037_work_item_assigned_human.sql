-- migration 37: whose queue is whose, once there is more than one human
--
-- LEDGER VERSION 37, read from brain.schema_migration rather than from this directory's
-- filenames. Live `brain` is at 33; 34 and 35 are lane C's, 40 and 41 are lane F's, 36 is this
-- lane's acceptance gate.
--
-- ================================================================= the question, and its scope
--
-- Row 0384's brief: "With several, they need a SUBJECT: who may accept work, who may answer a
-- question, who may see which lanes, whose queue is whose."
--
-- THIS FILE ANSWERS EXACTLY ONE OF THOSE FOUR, "whose queue is whose", and the other three are
-- answered with a deliberate NO in
-- `outputs/2026-08-27-E-0384-multi-user/IDENTITY-POLICY.md` part 3. The short version, because a
-- future reader will arrive here first: any human on this instance may accept any work, answer
-- any question and see every lane. Several named humans on one self-hosted instance is a shared
-- workspace, not a tenancy, and a visibility boundary between colleagues is the tenancy boundary
-- row 0386 decision 1 kept out in full. What is per-human is ATTRIBUTION, which migration 36
-- makes the database's answer, and now ASSIGNMENT, which is this file.
--
-- ASSIGNMENT IS NOT PERMISSION. Nothing here grants anything and nothing here refuses an act on
-- the strength of an assignment. It answers "whose attention does this row want", which is the
-- only one of the four questions that has no answer at all once a second human exists.
--
-- ================================================================= the rule it preserves
--
-- The bus already knows that a row nobody classified is the operator's. Queue schema 0013 says
-- it at length: the operator's own arm of `brain.queue_open` was `w.actor_type = 'human'` until
-- that migration, `actor_type` has a meaningful NULL, and two of his fifteen live rows answered
-- "nobody said" and appeared on no surface he reads. The repair was `NOT w.agent_claimable`,
-- which has no unset state.
--
-- So THE DEFAULT ASSIGNEE IS THE OPERATOR, expressed as a COALESCE rather than as a backfill:
--
--     COALESCE(w.assigned_human, brain.default_assignee()) = <the human asking>
--
-- No column is backfilled, no row is touched, and the property that makes this safe to land on a
-- store with 291 work items is checkable rather than asserted:
--
--     with zero assignments, brain.queue_open_for('operator') IS brain.queue_open, row for row
--
-- `engine/tests/test_multi_user_subject.py` asserts that equality with both counts on the line.
--
-- ================================================================= why a roster check
--
-- `assigned_human` must name a human `brain.human_roster()` recognises. Without that check a
-- typo assigns a row to a ghost and it leaves EVERY queue: not the operator's, because it is
-- assigned, and not anybody's, because nobody is that. That is the anti-graveyard failure
-- `queue doctor` exists to catch ("a queue's real failure is not a bad ranking, it is items that
-- leave the count and never come back") arriving through a new door, and it is the same shape as
-- the string that gated acceptance until migration 36.
--
-- ================================================================= rollback, stated and executed
--
--     BEGIN;
--       DROP FUNCTION IF EXISTS brain.queue_open_for(text);
--       DROP TRIGGER IF EXISTS work_item_assigned_human_is_a_human ON brain.work_item;
--       DROP FUNCTION IF EXISTS brain.work_item_assigned_human_is_a_human();
--       DROP FUNCTION IF EXISTS brain.default_assignee();
--       ALTER TABLE brain.work_item DROP COLUMN IF EXISTS assigned_human;
--       DELETE FROM brain.schema_migration WHERE version = 37;
--     COMMIT;
--
-- The column DROP is the only destructive line in this lane's rollback, and it destroys only
-- assignments, which are recoverable from `brain.thread` (the verb writes a thread event on
-- every assignment, on purpose, so the rollback is not silent). Run, and re-applied, on
-- brain_lane_e. See PROOF.md section 4.

\set ON_ERROR_STOP on

BEGIN;

-- ---------------------------------------------------------------- the default assignee

CREATE OR REPLACE FUNCTION brain.default_assignee() RETURNS text
  LANGUAGE sql IMMUTABLE AS $$ SELECT 'operator'::text $$;

COMMENT ON FUNCTION brain.default_assignee() IS
  'Whose an unassigned row is. The bus''s own rule, from queue schema 0013: a row nobody '
  'classified is the operator''s. A function rather than a literal in the view and the verb, so '
  'that changing it is one edit a reviewer can see. It is NOT a general default owner and '
  'nothing grants on it: it is the value brain.queue_open_for COALESCEs to.';

-- ---------------------------------------------------------------- the column

ALTER TABLE brain.work_item ADD COLUMN IF NOT EXISTS assigned_human text;

COMMENT ON COLUMN brain.work_item.assigned_human IS
  'Which human this row wants the attention of, or NULL meaning brain.default_assignee(). NOT a '
  'permission: any human on this instance may act on any row, per '
  'outputs/2026-08-27-E-0384-multi-user/IDENTITY-POLICY.md part 3. Written only by the `queue '
  'assign` verb, only from a human login, and only with a name brain.human_roster() recognises, '
  'because a row assigned to a ghost leaves every queue and never comes back.';

-- ---------------------------------------------------------------- the gate

CREATE OR REPLACE FUNCTION brain.work_item_assigned_human_is_a_human() RETURNS trigger
  LANGUAGE plpgsql AS $$
DECLARE who text;
BEGIN
  -- Only a CHANGE to the assignment is privileged. An ordinary write on an already-assigned
  -- row -- a `done`, a rank recompute, a `set` -- passes straight through, which is migration
  -- 20's own asymmetry: establishing the attribute is the privileged act, living with it is not.
  IF TG_OP = 'UPDATE'
     AND NEW.assigned_human IS NOT DISTINCT FROM OLD.assigned_human THEN
    RETURN NEW;
  END IF;
  IF TG_OP = 'INSERT' AND NEW.assigned_human IS NULL THEN
    RETURN NEW;
  END IF;

  who := brain.current_human();
  IF who IS NULL THEN
    RAISE EXCEPTION 'refusing to change the assignment of %: this connection is %, which is not '
                    'a human login', COALESCE(NEW.id, '(new)'), session_user
      USING ERRCODE = 'insufficient_privilege',
            HINT = 'Assignment says whose attention a row wants, so it is a human deciding about '
                   'humans. An agent that could assign could move the operator''s work off his '
                   'own queue. Run it as a human: `queue assign <id> --to <slug>`, which opens a '
                   'login mapped in brain.human_role.';
  END IF;

  -- NULL is always allowed: it is not a name, it is the absence of one, and it means the row
  -- goes back to brain.default_assignee(). A human may always hand a row back.
  IF NEW.assigned_human IS NOT NULL
     AND NOT EXISTS (SELECT 1 FROM brain.human_roster() r WHERE r.human = NEW.assigned_human)
  THEN
    RAISE EXCEPTION 'refusing to assign % to %: this database does not know that human',
                    COALESCE(NEW.id, '(new)'), NEW.assigned_human
      USING ERRCODE = 'foreign_key_violation',
            HINT = 'A row assigned to a name nobody holds leaves EVERY queue: not the '
                   'operator''s, because it is assigned, and not anybody''s, because nobody is '
                   'that. `swarm admin human list` shows who exists on this instance and '
                   '`swarm admin human provision <slug>` adds one, up to the stated ceiling of '
                   'brain.human_login_ceiling().';
  END IF;

  RETURN NEW;
END $$;

COMMENT ON FUNCTION brain.work_item_assigned_human_is_a_human() IS
  'Two refusals, and neither is a permission check. A non-human connection may not move work '
  'between humans, and no connection may assign work to somebody who does not exist. Composes '
  'brain.current_human() and brain.human_roster() rather than building a second notion of who '
  'is human, which migration 20''s COMMENT names as the way two notions drift and the weaker '
  'one becomes the real policy.';

DROP TRIGGER IF EXISTS work_item_assigned_human_is_a_human ON brain.work_item;
CREATE TRIGGER work_item_assigned_human_is_a_human
  BEFORE INSERT OR UPDATE OF assigned_human ON brain.work_item
  FOR EACH ROW
  EXECUTE FUNCTION brain.work_item_assigned_human_is_a_human();

-- ---------------------------------------------------------------- whose queue is whose

-- GUARDED 2026-09-07 FOR A MIGRATIONS-ONLY STORE. Terminal 04, decision 22, reviewed by T08.
--
-- WHY A LANDED LEDGER ENTRY IS BEING EDITED, which is not something this repo does lightly.
-- `brain.queue_open` is a VIEW, and it lives in `queue/schema`, not here. This function names it as
-- a RETURN TYPE, so on a store built from `migrations/` alone the type does not exist and the whole
-- migration dies:
--
--     ERROR:  type "brain.queue_open" does not exist
--
-- That is not cosmetic. `scratch-db.sh create` with `SCRATCH_SCHEMA_DIRS=migrations` cannot get
-- past 0037, so EVERY scene in the repository that needs a store without the queue tables is
-- blocked, and two of them report only `scratch-db.sh create failed` -- one cause behind two of the
-- eight failing signatures in the engine suite (decision 10).
--
-- Only 0037 itself can fix this. A later migration cannot help: the failure happens while 0037 is
-- applying, before any later file is read.
--
-- Migration 31 already meets exactly this situation, three directories over, and degrades with a
-- stated NOTICE rather than dying. This is that pattern, applied here, and nothing else changes:
-- where `brain.queue_open` exists the function is created with the identical body it has always
-- had, so a store that already applied 0037 is unaffected and a full build is byte-identical in
-- behaviour. Where the view is absent the function is skipped and the reason is printed, because a
-- store with no queue view has no queue to narrow.
DO $guard$
BEGIN
IF to_regclass('brain.queue_open') IS NULL THEN
  RAISE NOTICE 'brain.queue_open does not exist here (a migrations-only store); migration 37 has no queue view to narrow, so brain.queue_open_for(text) is not created. If this store is ever given queue/schema, re-apply the queue_open_for body from THIS file: nothing else in 0037 depends on it.';
ELSE
EXECUTE $qfor$
CREATE OR REPLACE FUNCTION brain.queue_open_for(p_human text)
  RETURNS SETOF brain.queue_open
  LANGUAGE sql STABLE AS $fn$
  SELECT q.*
    FROM brain.queue_open q
    LEFT JOIN brain.work_item w ON w.id = q.work_item_id
   WHERE COALESCE(w.assigned_human, brain.default_assignee()) = p_human
$fn$
$qfor$;
EXECUTE 'GRANT EXECUTE ON FUNCTION brain.queue_open_for(text) TO PUBLIC';
END IF;
END $guard$;

DO $guardc$
BEGIN
IF to_regclass('brain.queue_open') IS NOT NULL THEN
EXECUTE $c$COMMENT ON FUNCTION brain.queue_open_for(text) IS
  'brain.queue_open, narrowed to one human. NOT A VISIBILITY BOUNDARY: it is a filter any caller '
  'may run for any human, and brain.queue_open itself is unchanged and still shows everything. '
  'The LEFT JOIN and the COALESCE are the whole of the rule: a row with no work item, or a work '
  'item nobody assigned, belongs to brain.default_assignee(). That is queue schema 0013''s own '
  'rule, so with zero assignments this function returns brain.queue_open row for row, which '
  'engine/tests/test_multi_user_subject.py asserts with both counts printed.'$c$;
END IF;
END $guardc$;

INSERT INTO brain.schema_migration (version, name)
     VALUES (37, '0037_work_item_assigned_human')
  ON CONFLICT (version) DO NOTHING;

COMMIT;
