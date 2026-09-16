-- queue schema 0013, ledger version 28: the operator's queue becomes the complement of the
-- fleet's, by construction rather than by two lists agreeing.
--
-- Task 0421, posted by T3 out of task 0414 and handed to the queue lane because widening what
-- the operator's console shows him is this lane's call. Additive and re-runnable: one
-- CREATE OR REPLACE VIEW, one refreshed COMMENT, one ledger row. It drops nothing, edits no
-- applied file, adds no column and changes no column type. `queue/schema/0007_queue.sql` is
-- untouched, which is the same rule `queue/schema/0009_null_branch_act_scan.sql` followed when
-- it replaced views 0007 had shipped.
--
-- DEPENDS ON migrations/0026_work_item_agent_claimable.sql (ledger version 26) and REFUSES
-- without it, at the top of the transaction, with a sentence instead of `ERROR: column
-- w.agent_claimable does not exist`.
--
-- ================================================================= WHAT WAS OPEN
--
-- `brain.queue_open`'s second arm -- the arm whose `primary_verb` is `Mark my task done` and
-- which IS the operator's own queue on his console -- read
--
--     WHERE w.actor_type = 'human' AND w.state IN ('inbox', 'active')
--
-- MEASURED on live `brain` on 2026-08-18 by T4, read-only as `brain_owner`, re-measured rather
-- than inherited from the brief:
--
--   select count(*) from brain.work_item where state='inbox' and posted_by='operator';   -- 15
--   select count(*) from brain.queue_open q join brain.work_item w on w.id=q.source_id
--                   where w.state='inbox' and w.posted_by='operator';                    -- 13
--
-- Two of the operator's fifteen open rows were on no surface he reads:
--
--   0068  qc         Double-check the exported QC data against the warehouse   11:56:40.946
--   0071  marketing  Unify the multiple presentations and incorporate the ...  11:56:57.715
--
-- Both carry `actor_type IS NULL` (verified: `actor_type is null` is true, not an empty string).
-- They are not strays from an older era. 0067 (human), 0068 (NULL) and 0069 (human) were created
-- 11:56:40.224, 11:56:40.946 and 11:56:41.897 on 2026-08-17: one 1.7-second posting batch in
-- which the human mark was set, missed, and set again.
--
-- AND 0068 IS THE INPUT TO 0069, *Send the double-checked QC result to the client*. The row that
-- says check it before you send it is the one row of the pair he could not see.
--
-- ================================================================= WHY THE PREDICATE IS ONE
-- ================================================================= TERM AND NOT TWO
--
-- 0421's brief proposed `w.actor_type = 'human' OR NOT w.agent_claimable`. On measurement the
-- first term is redundant, and writing it anyway would be worse than noise.
--
-- Migration 26's trigger `work_item_human_is_never_agent_claimable` COERCES
-- `agent_claimable := false` on every row where `actor_type = 'human'`, on INSERT and on UPDATE.
-- So `actor_type = 'human'` IMPLIES `NOT agent_claimable` is an invariant of the database, not a
-- convention, and the two-term disjunction reduces to its second term.
--
-- Writing both terms would assert that the two notions are independent and could disagree.
-- Migration 20's COMMENT on `brain.current_human()` is explicit that a second notion of who is
-- human must never be built beside the first, "or the two drift and the weaker one becomes the
-- real policy". A view whose predicate is written as if they could differ is exactly the shape
-- that invites someone to make them differ.
--
-- So the arm reads `NOT w.agent_claimable`, and that is the EXACT COMPLEMENT of the predicate
-- `claim` applies: `engine/swarm_engine/transitions.py`, `_AGENT_CLAIMABLE` at line 242,
-- `AND w.agent_claimable`, interpolated at line 725 into the same FOR UPDATE SKIP LOCKED
-- statement as its row lock. One column decides both questions, in opposite directions.
-- "Held from the fleet" and "on the operator's queue" are now the same set by construction, and
-- there is no third state a row can be in. That is the property 0421 asked for; the one-term
-- form is what makes it a property rather than a coincidence of two predicates being maintained
-- in step.
--
-- `agent_claimable` is `NOT NULL DEFAULT false` (migration 26), so `NOT w.agent_claimable` is
-- total: it is never NULL, and there is no row it silently drops. That matters here in a way it
-- would not on a nullable column -- a NULL would evaluate the arm to NULL, exclude the row, and
-- reproduce the invisibility this file exists to end, in a form no test would notice.
--
-- ================================================================= WHAT THIS ADDS, MEASURED
--
-- Not "should be small": counted. Simulating migration 26's own backfill rule on live `brain`
-- (raise to true only where `actor_type IS DISTINCT FROM 'human'` AND `posted_by` is neither
-- `operator` nor empty), the rows in `state IN ('inbox','active')` that are NOT
-- `actor_type='human'` and NOT agent-claimable are EXACTLY 0068 and 0071. Zero others. His
-- console goes from 13 of his 15 open rows to 15 of 15, and gains nothing else. Whole table at
-- that moment: 18 claimable, 16 held, of 34.
--
-- GOING FORWARD the arm also admits a row a FLEET surface posted without `--for-agents`, because
-- that row is `agent_claimable = false` and no agent will ever take it. That is intended and it
-- is the cheap direction: such a row is already announced three times over -- `swarm post`
-- prints HELD FROM THE FLEET at the moment it is created
-- (`engine/swarm_engine/cli.py:175`), `swarm ls` marks it `[OPERATOR]`, and `swarm doctor`
-- reports it as posted-for-nobody. Adding it to the one surface the operator actually reads is
-- what turns three agent-facing warnings into something a human sees. A row nobody will do
-- belongs in front of the person who decides whether it gets done.
--
-- ================================================================= WHAT THIS DOES NOT DO
--
-- It does not reclassify 0068 or 0071, or edit any operator row's content. 0421's brief forbids
-- it without asking him first and this file touches no row at all.
--
-- It does not change which rows an agent may claim. `claim`'s predicate is untouched by this
-- file; the set it admits is identical before and after.
--
-- It does not widen `web/rooms.py::assert_allowed_on`, and does not need to. That guard reads
-- `item['actor_type']`, but `item` is the CONSOLE CARD built by `web/model.py::_card`, whose
-- `actor_type` is stamped from `_VERBS[kind]` (`web/model.py:214`) after `kind` is looked up in
-- `KIND_BY_ARM` on `(source_type, primary_verb)`. A row reaching the console through THIS arm
-- carries `primary_verb = 'Mark my task done'`, so it is kind `human`, so the card carries
-- `actor_type = 'human'` whatever the row's column says, and `done` is permitted. The guard is
-- therefore a restatement of this arm rather than an independent check on it -- which is a real
-- finding about that guard, raised to the bus as adjacent work rather than repaired here.
--
-- The residual, stated rather than hidden: a row an agent has already claimed
-- (`state = 'active'`) whose `agent_claimable` is then LOWERED to false -- which migration 26
-- permits to everybody, deliberately, as the safe direction -- lands on this arm while an agent
-- is still parked on it, and the console would let the operator `done` it, fabricating that
-- agent's report. Zero rows on live are in that state today (measured: `state = 'active'` is
-- empty, and no `inbox` row carries a claimer), and the case cannot arise without a deliberate
-- act of taking work back from the fleet. The place to close it is the console guard reading the
-- ROW instead of the arm, which is the same adjacent task.

\set ON_ERROR_STOP on

BEGIN;

-- ---------------------------------------------------------------- refuse before 26, in words

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                  WHERE table_schema = 'brain' AND table_name = 'work_item'
                    AND column_name = 'agent_claimable') THEN
    RAISE EXCEPTION 'brain.work_item.agent_claimable does not exist, so queue_open cannot be '
                    'defined as the complement of what the fleet may claim'
      USING HINT = 'Apply migrations/0026_work_item_agent_claimable.sql (ledger version 26) '
                   'first. engine/bin/scratch-db.sh orders by RECORDED version, not filename, '
                   'so 26 precedes this file''s 28 automatically; a hand-applied run out of '
                   'order is what this check is for. Refusing here rather than letting the '
                   'CREATE OR REPLACE fail with 42703, because that error names a column and '
                   'not a missing migration.';
  END IF;
END $$;

-- ---------------------------------------------------------------- the view, arm 2 widened
--
-- Reproduced whole because CREATE OR REPLACE VIEW has no partial form. Arms 1, 3 and 4 are
-- byte-identical to `queue/schema/0007_queue.sql`; the ONLY change in this file is arm 2's
-- WHERE clause. Column names, types and ORDER are unchanged, which is what CREATE OR REPLACE
-- requires and what keeps every reader of this view working across the swap.

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
  -- 2. THE OPERATOR'S OWN QUEUE, which is now defined as everything the fleet may not take.
  --
  --    Until queue schema 0013 this read `w.actor_type = 'human'`, and that column has a
  --    meaningful NULL -- migration 1's own comment, "unset is a real state" -- so two of his
  --    fifteen live rows answered "nobody said" and appeared on no surface he reads.
  --    `agent_claimable` is NOT NULL DEFAULT false and has no unset state: every row answers,
  --    and a row that was never classified answers "the operator's". Migration 26's
  --    `work_item_human_is_never_agent_claimable` trigger coerces every `actor_type = 'human'`
  --    row to `agent_claimable = false`, so this one term still admits all thirteen rows the
  --    old predicate admitted, and this arm is the exact complement of `claim`'s
  --    `AND w.agent_claimable`.
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
  --    null-branch gate protects.
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
  UNION ALL
  -- 4. open recommendations that require a human. Requiring one is the norm and not the
  --    exception: `recommend accept` refuses an agent decider whatever this column says.
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
   WHERE r.state = 'open';

COMMENT ON VIEW brain.queue_open IS
  'The human queue: one view over four arms of three existing tables. There is no queue table '
  'and there must not be one -- a second list is the parallel queue the whole design refuses. '
  'Arm 2, the operator''s own work, is NOT w.agent_claimable: the exact complement of the '
  'predicate engine claim applies, so every work_item in inbox or active is on exactly one of '
  'the two queues and never on neither. It read w.actor_type = ''human'' until queue schema '
  '0013 (task 0421), and that column''s NULL -- a real state meaning nobody said -- left two of '
  'the operator''s fifteen live rows, 0068 and 0071, on no surface at all.';

INSERT INTO brain.schema_migration (version, name)
     VALUES (28, '0013_queue_open_shows_what_the_fleet_will_not_take')
  ON CONFLICT (version) DO NOTHING;

COMMIT;
