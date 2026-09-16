-- migration 31: a question can be WITHDRAWN, by name, and a withdrawn question leaves the
-- operator's queue without anybody answering it.
--
-- Task 0159, lane fabric, 2026-08-18. Ledger version 31 (30 is queue/schema/0014); the file
-- prefix is this lane's counter and the recorded version is the key, per engine/bin/scratch-db.sh.
--
-- ================================================================= WHAT WAS OPEN
--
-- `cancel` closed a task and left every question it had raised standing. Measured on the live
-- store during task 0147: seven fabricated rows were cancelled, one of them (0139) carried an
-- unanswered question q0140, and after the cancel `swarm ls --lane fabric` printed
-- `0139 cancelled` while `swarm questions` still printed `q0140 T2 on 0139`. Reproduced on a
-- scratch store on 2026-08-18 against the code as committed: post, ask, cancel, and the question
-- is still in the queue.
--
-- The cost is not untidiness and it is now measured rather than predicted. At 16:47Z on
-- 2026-08-18 THE OPERATOR ANSWERED q0140 -- a question about a release branch that does not
-- exist, asked by a test, on a task that had been cancelled four and a half hours earlier. He
-- answered it because it was in his queue and because answering was the only act this schema
-- offered. The whole system exists to protect that attention, and it spent some.
--
-- ================================================================= WHY NOT `answer`
--
-- Because `answer` writes the identity `operator`, and it is the only verb that could clear an
-- open question. Task 0147 existed BECAUSE a tool was manufacturing operator-signed answers on
-- the live bus; closing an orphan that way is the same act with a nicer motive, and the record
-- afterwards would say a human decided something no human ever read.
--
-- So withdrawal is a THIRD terminal state for a question, beside open and answered, and it
-- carries the name of whoever performed it. `withdrawn_by` is NOT NULL-empty-refused by a CHECK
-- below: an unsigned withdrawal is exactly the anonymous act this lane spent 0147 removing.
--
-- ================================================================= WHAT IT DOES NOT DO
--
-- It does not delete, and it does not touch `answer`. A withdrawn question keeps its text, its
-- asker, its stated default and its `question.raised` event. The operator can still read it
-- (`swarm questions --withdrawn`); what changes is that nothing asks him to decide it.
--
-- Additive and re-runnable: three columns, two CHECKs, one partial index, three COMMENTs, and
-- two CREATE OR REPLACE VIEWs that narrow one arm each. It drops nothing and edits no applied
-- file.

BEGIN;

-- ---------------------------------------------------------------- the three columns

ALTER TABLE brain.question
  ADD COLUMN IF NOT EXISTS withdrawn_at     timestamptz,
  ADD COLUMN IF NOT EXISTS withdrawn_by     text NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS withdrawn_reason text;

-- A question is answered OR withdrawn OR open. Never two of them: "the operator decided this and
-- also nobody needed it" is not a state, it is two records of the same question disagreeing.
-- The transitions enforce it too (`withdraw` refuses an answered question, `answer` refuses a
-- withdrawn one); this is the copy that holds for a surface written next year.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'question_not_both_ck') THEN
    ALTER TABLE brain.question
      ADD CONSTRAINT question_not_both_ck CHECK (answered_at IS NULL OR withdrawn_at IS NULL);
  END IF;
END $$;

-- A withdrawal is SIGNED or it does not happen. The whole argument for the verb is that the
-- record afterwards names the hand that acted; a withdrawal with an empty `withdrawn_by` would
-- be the unattributed write this replaces.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'question_withdrawn_signed_ck') THEN
    ALTER TABLE brain.question
      ADD CONSTRAINT question_withdrawn_signed_ck
      CHECK (withdrawn_at IS NULL OR btrim(withdrawn_by) <> '');
  END IF;
END $$;

-- The open-question index, narrowed. `question_open_idx` (migration 1) is LEFT IN PLACE rather
-- than dropped: it is still a correct index for `answer IS NULL`, several queue views read that
-- predicate for their own reasons, and dropping an index another lane's plan may be sitting on
-- is not the kind of change a column addition gets to make.
CREATE INDEX IF NOT EXISTS question_open_not_withdrawn_idx
  ON brain.question (asked_at) WHERE answer IS NULL AND withdrawn_at IS NULL;

COMMENT ON COLUMN brain.question.withdrawn_at IS
  'When this question stopped needing an answer WITHOUT being answered -- normally because the '
  'task that raised it was cancelled. A withdrawn question leaves swarm questions and the human '
  'queue and keeps everything else it had.';
COMMENT ON COLUMN brain.question.withdrawn_by IS
  'Who withdrew it, by name. Never the string written by a verb that guessed: `withdraw` refuses '
  'to run unattributed, and the CHECK above refuses an empty one from any other writer.';
COMMENT ON COLUMN brain.question.withdrawn_reason IS
  'Why, in the words of whoever acted. For the cancel cascade this is the cancel reason, so the '
  'question and the task carry the same sentence.';

-- ---------------------------------------------------------------- the human queue's own views
--
-- `brain.queue_open` arm 3 and `brain.queue_pending_default` both select `q.answer IS NULL`, so
-- a withdrawn question would leave `swarm questions` and stay on the operator's CARD -- and,
-- worse, stay eligible in `queue_pending_default`, where a checkpoint can FIRE its default. That
-- would be an automatic act taken on behalf of a task that no longer exists.
--
-- Arms 1, 2 and 4 below are queue/schema/0014's, reproduced byte for byte. The only change in
-- this file is the one added line in arm 3. Column names, types and order are unchanged, which
-- is what CREATE OR REPLACE VIEW requires.
--
-- FOR THE NEXT LANE TO TOUCH queue/schema: this view now has a predicate that lives in
-- migrations/, not in your directory. Re-issuing 0014's body from a later queue migration would
-- silently take it back out, and the symptom is a cancelled task's question reappearing on the
-- operator's card. Carry `AND q.withdrawn_at IS NULL` forward.
--
-- BOTH RE-ISSUES ARE GUARDED ON THE VIEW ALREADY EXISTING, and the guard is not caution, it is
-- the difference between this file applying and not applying. There is a SECOND schema profile:
-- `scratch-db.sh` takes `SCRATCH_SCHEMA_DIRS=migrations`, which builds a store from this
-- directory alone with no queue lane in it at all, and `engine/tests/test-lane-budget-gate.sh`
-- scene 7 builds one on every run to prove `claim` degrades rather than raising against a store
-- with no budget tables. In that profile `brain.queue_item_option` (queue/schema/0014, ledger 30)
-- and `brain.default_is_null_branch` (queue/schema/0009, ledger 11) do not exist, so the two
-- bodies below cannot compile -- and because this file is ONE transaction, the failure took the
-- whole migration with it. Measured 2026-08-19 before the guard:
--     psql:<stdin>:175: ERROR:  relation "brain.queue_item_option" does not exist
--     LINE 65:      AND NOT EXISTS (SELECT 1 FROM brain.queue_item_option o
-- and the engine suite red at "7. a second store was built ... scratch-db.sh create failed".
--
-- Skipping is the correct act and not a fallback: a view this store never created is a view this
-- file has nothing to narrow. What it costs is stated rather than hidden -- if a store built
-- migrations-only is LATER brought up on the full directory list, queue/schema/0007 and 0014
-- create these two views from their own bodies, without the predicate, and this file will not
-- re-run because version 31 is already in the ledger. No exception can catch that
-- (`docs/SCHEMA-TOLERANCE.md` rule 6: a body-only replacement is silent in both directions), so
-- the NOTICE below is the only thing that will ever say it, and it says it at build time.

DO $guard$
BEGIN
IF to_regclass('brain.queue_open') IS NULL THEN
  RAISE NOTICE 'brain.queue_open does not exist here (a migrations-only store); migration 31 has no queue view to narrow. If this store is ever given queue/schema, re-apply the queue_open body from THIS file: 0014''s own body omits AND q.withdrawn_at IS NULL.';
ELSE
EXECUTE $qopen$
CREATE OR REPLACE VIEW brain.queue_open AS
  -- 1. finished agent work awaiting a human acceptance. `done` means the agent reported it
  --    finished; acceptance is a separate act (D00 contract rule 3).
  SELECT 'work_item'::text AS source_type, w.id AS source_id,
         w.title, w.lane AS source_lane, w.claimed_by AS producer,
         'review'::text AS default_item_class, 'Accept work'::text AS primary_verb,
         w.priority, w.finished_at AS surfaced_at, w.created,
         s.external, s.canon_touching, s.reversibility, s.urgency, s.stakes,
         s.charter_alignment, s.dependency_unblocking, s.confidence,
         w.depends_on, w.id AS work_item_id, w.session_id
    FROM brain.work_item w
    JOIN brain.work_item_signals s ON s.id = w.id
   WHERE w.state = 'done' AND w.accepted_at IS NULL
  UNION ALL
  -- 2. THE OPERATOR'S OWN QUEUE, which is everything the fleet may not take (queue schema 0013).
  SELECT 'work_item', w.id, w.title, w.lane, w.posted_by,
         'blocker', 'Mark my task done',
         w.priority, w.created, w.created,
         s.external, s.canon_touching, s.reversibility, s.urgency, s.stakes,
         s.charter_alignment, s.dependency_unblocking, s.confidence,
         w.depends_on, w.id, w.session_id
    FROM brain.work_item w
    JOIN brain.work_item_signals s ON s.id = w.id
   WHERE NOT w.agent_claimable AND w.state IN ('inbox', 'active')
  UNION ALL
  -- 3. open questions. These are where a stated default lives, so this arm is what the
  --    null-branch gate protects. Withdrawn is not open (migration 31, task 0159).
  SELECT 'question', q.id, q.text, coalesce(w.lane, ''), q.asked_by,
         'approval', 'Accept default',
         coalesce(w.priority, 1), q.asked_at, q.asked_at,
         coalesce(s.external, false), coalesce(s.canon_touching, false),
         s.reversibility, s.urgency, s.stakes, s.charter_alignment,
         s.dependency_unblocking, s.confidence,
         coalesce(w.depends_on, ''), q.work_item_id, ''
    FROM brain.question q
    LEFT JOIN brain.work_item w ON w.id = q.work_item_id
    LEFT JOIN brain.work_item_signals s ON s.id = q.work_item_id
   WHERE q.answer IS NULL
     AND q.withdrawn_at IS NULL
  UNION ALL
  -- 4. open recommendations that require a human. Requiring one is the norm and not the
  --    exception: `recommend accept` refuses an agent decider whatever this column says.
  --
  --    EXCEPT A DRAFTED OPTION, which is a recommendation that already has a card: its item's.
  --    Without this term a four-option item arrives as five queue cards, four of them proposals
  --    the operator has already been shown in one place, and the component built to make a
  --    choice small would be the thing making the queue big. The option is not hidden -- it is
  --    rendered on `brain.queue_item`'s card by `web/model.py` and it is still accepted through
  --    `recommend accept` like any other recommendation.
  SELECT 'recommendation', r.id::text, r.text, '', coalesce(r.produced_by, ''),
         'approval', 'Approve',
         2, r.created_at, r.created_at,
         coalesce(s.external, false), coalesce(s.canon_touching, false),
         s.reversibility, s.urgency, s.stakes, s.charter_alignment,
         s.dependency_unblocking, s.confidence,
         coalesce(w.depends_on, ''),
         CASE WHEN r.subject_type = 'work_item' THEN r.subject_id END,
         coalesce(r.cites_session_id, '')
    FROM brain.recommendation r
    LEFT JOIN brain.work_item w
           ON r.subject_type = 'work_item' AND w.id = r.subject_id
    LEFT JOIN brain.work_item_signals s
           ON r.subject_type = 'work_item' AND s.id = r.subject_id
   WHERE r.state = 'open'
     AND NOT EXISTS (SELECT 1 FROM brain.queue_item_option o
                      WHERE o.recommendation_id = r.id)
$qopen$;
END IF;

-- queue/schema/0007's body, with the same one line added. A withdrawn question has no pending
-- default, because there is nothing left for silence to decide. Same guard, same reason: its body
-- calls brain.default_is_null_branch(), which the queue lane creates.
IF to_regclass('brain.queue_pending_default') IS NULL THEN
  RAISE NOTICE 'brain.queue_pending_default does not exist here (a migrations-only store); migration 31 has no pending-default view to narrow. Same carry-forward note as above applies to queue/schema/0007''s body.';
ELSE
EXECUTE $qpend$
CREATE OR REPLACE VIEW brain.queue_pending_default AS
  SELECT q.id AS question_id, q.work_item_id, q.asked_by AS producer, q.asked_at,
         q.default_if_unanswered AS default_text,
         coalesce(s.external, false) AS external,
         coalesce(s.canon_touching, false) AS canon_touching,
         brain.default_is_null_branch(q.default_if_unanswered) AS null_branch,
         (coalesce(s.external, false) OR coalesce(s.canon_touching, false)) AS gated,
         e.fired_at, e.checkpoint
    FROM brain.question q
    LEFT JOIN brain.work_item_signals s ON s.id = q.work_item_id
    LEFT JOIN brain.queue_default_event e ON e.question_id = q.id
   WHERE q.answer IS NULL
     AND q.withdrawn_at IS NULL
     AND coalesce(btrim(q.default_if_unanswered), '') <> ''
$qpend$;
END IF;
END
$guard$;

INSERT INTO brain.schema_migration (version, name)
VALUES (31, '0031_question_withdrawal') ON CONFLICT (version) DO NOTHING;

COMMIT;
