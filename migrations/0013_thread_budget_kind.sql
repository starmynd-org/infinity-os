-- migration 13: a spend stop reads as a spend stop on the task thread
--
-- Task 0120 (store lane), child of 0110. Additive and re-runnable: it widens one CHECK, corrects
-- the classification of the rows that CHECK had no word for, and touches nothing else.
-- `0001_initial.sql` stays exactly as it is; an applied migration is history, and history is
-- amended by a later migration (the rule migration 12 already runs on).
--
-- ================================================================= what was broken
--
-- `budget/transitions.py` filed its task-thread line as kind `budget`. Migration 1 carries
--
--   CHECK (kind IN ('post','claim','note','msg','ask','answer','done','block','fail','reopen',
--                   'cancel','artifact','heartbeat','reap','set','accept','event'))
--
-- and `budget` is not in it. So every `budget stop` carrying a `work_item_id` raised
-- CheckViolation and rolled the WHOLE transaction back -- no incident row, no stop recorded,
-- nothing for `budget halt` to find. That is the exact call `budget.enforcer.RunGuard._stop`
-- makes on a live task, so the budget-stop-against-a-task path was dead at the store level while
-- every test above it passed: none of them passed a `work_item_id` that exists, so none of them
-- reached the branch.
--
-- D4 found it while wiring the brake (task 0110) and unblocked it by writing the line as `note`,
-- a word the store already had. That is not wrong, it is just mute: a reader filtering the thread
-- by kind cannot tell a spend stop from an ordinary note. This migration is the answer the store
-- lane owes back.
--
-- ================================================================= ONE kind, not two
--
-- 0120 asks whether this should be one word or two, because `brain.budget_incident.kind` already
-- distinguishes `hard_stop`, `manual_stop` and `blocked_dispatch`. It is ONE, `budget`, for three
-- measured reasons:
--
--   1. TWO SPELLINGS OF ONE FACT DRIFT. The discrimination already exists, typed, in
--      `budget_incident.kind` with CHECKs that refuse a row claiming a warning stopped a run
--      (`0003_budget.sql:217`). A thread row carrying its own copy of that distinction is a second
--      place for it to be got wrong, with no constraint tying the two together. This is the same
--      argument migration 12 makes for one ancestry walk instead of two.
--
--   2. THE SECOND WORD WOULD HAVE NO WRITER. `blocked_dispatch` is filed by `budget note`
--      (`budget/transitions.py:316`), which writes NO thread line at all -- measured, not
--      assumed: `ctx.thread` appears exactly once in that file, inside `budget_stop`. Adding a
--      vocabulary term today for a caller that does not exist is schema written on a guess.
--
--   3. `kind` HERE IS A VERB, not a subtype. Every one of the seventeen existing words is a verb
--      the fleet dispatches: post, claim, note, msg, ask, answer, done, block, fail, reopen,
--      cancel, artifact, heartbeat, reap, set, accept, event. `budget` is the verb family; which
--      budget thing happened is the incident row's job, and the text already leads with it
--      ("budget hard_stop on work_item:0037: ..."), so the line still reads without a join.
--
-- If a dispatch refusal ever wants a thread line, it takes THIS kind with its own leading word
-- ("budget blocked_dispatch on ..."), and `kind = 'budget'` stays the one filter that answers
-- "did money touch this task". Splitting later is another one-line migration; un-splitting is not.
--
-- ================================================================= the reader that must learn it
--
-- `web/model.py:323` builds the item screen's trail from a HARD ALLOWLIST of kinds. A word that
-- is not in that list is DROPPED, not rendered unknown, so widening this CHECK without widening
-- that list would have moved the budget line off `note` and straight off the screen. Fixed in the
-- same change and noted on task 0120. `web/model.py:552` (crosstalk) needs nothing: unknown kinds
-- fall through `.get(kind, 'rep')` and render. `brain.feed` passes `kind` through unfiltered.
--
-- Widening a CHECK only ever accepts MORE rows than before, so no existing row can become invalid
-- and no revalidation is required. The one row this database already holds is corrected below.

BEGIN;

SET search_path TO brain, public;

-- FAIL FAST RATHER THAN QUEUE. `ALTER TABLE ... ADD CONSTRAINT` takes ACCESS EXCLUSIVE, and a
-- lock request that waits does not wait quietly: every reader of `brain.thread` that arrives
-- behind it queues too, so a migration blocked on one long transaction stops `show`, `feed` and
-- the console for the whole fleet. Five seconds, then this whole transaction aborts having
-- changed nothing and you run it again. `SET LOCAL` so it dies with the transaction.
SET LOCAL lock_timeout = '5s';

-- ---------------------------------------------------------------- the vocabulary
--
-- DROP IF EXISTS then ADD, rather than an ALTER that does not exist: Postgres 16 has no
-- `ADD CONSTRAINT IF NOT EXISTS`, and this pair is re-runnable. `thread_kind_check` is the name
-- Postgres generated for migration 1's inline CHECK; confirmed against the live store, not
-- guessed (migrations/SCHEMA-AS-APPLIED.txt:896).
--
-- The eighteen words below are migration 1's seventeen in their original order plus `budget` at
-- the end. Nothing is removed. If you are adding a nineteenth, add it here, and check
-- `web/model.py` in the same commit.

ALTER TABLE brain.thread DROP CONSTRAINT IF EXISTS thread_kind_check;

ALTER TABLE brain.thread ADD CONSTRAINT thread_kind_check
  CHECK (kind IN ('post','claim','note','msg','ask','answer','done','block',
                  'fail','reopen','cancel','artifact','heartbeat','reap',
                  'set','accept','event','budget'));

COMMENT ON COLUMN brain.thread.kind IS
  'The verb that produced the event. Eighteen words since migration 13, which added ''budget'': '
  'one word for the whole budget family, because brain.budget_incident.kind already types '
  'hard_stop / manual_stop / blocked_dispatch and two spellings of one fact drift. Filter '
  'kind = ''budget'' for "did money touch this task". Readers with their own allowlist of kinds '
  '(web/model.py trail) must learn any word added here or they will silently drop it.';

-- ---------------------------------------------------------------- correct what was already filed
--
-- The rows written as `note` while the vocabulary was missing are budget stops. They are moved,
-- not rewritten: the text is untouched and the timestamps are untouched, only the word that
-- classifies them changes to the one the store now has.
--
-- THE PREDICATE IS A JOIN, NOT A PROSE MATCH. `budget_stop` inserts the incident and writes the
-- thread line in ONE transaction, and both columns default to `now()`, which is
-- transaction_timestamp() and therefore identical for the pair. So `t.ts = i.occurred_at` on the
-- same `work_item_id` identifies the pair exactly. Measured on the live store before this file
-- was written: thread seq 47 and budget_incident id 90 both stamped 13:32:15.387075+00 on 0037.
-- The text prefix is kept as a second, redundant condition -- a hand-typed `swarm note` that
-- happens to begin "budget hard_stop on " still cannot match unless it also shares a transaction
-- timestamp with a real incident on the same task.
--
-- Guarded on `to_regclass` because migration 3 lives in `budget/schema/` and a database built
-- from `migrations/` alone does not have `budget_incident`. There, there is nothing to correct
-- and this block does nothing rather than failing.

DO $$
DECLARE moved integer := 0;
BEGIN
  IF to_regclass('brain.budget_incident') IS NOT NULL THEN
    WITH corrected AS (
      UPDATE brain.thread t SET kind = 'budget'
       WHERE t.kind = 'note'
         AND (t.text LIKE 'budget hard_stop on %' OR t.text LIKE 'budget manual_stop on %')
         AND EXISTS (SELECT 1 FROM brain.budget_incident i
                      WHERE i.work_item_id = t.work_item_id
                        AND i.occurred_at  = t.ts
                        AND i.kind IN ('hard_stop', 'manual_stop'))
      RETURNING 1)
    SELECT count(*) INTO moved FROM corrected;
  END IF;
  RAISE NOTICE 'migration 13: % thread row(s) reclassified note -> budget', moved;
END $$;

-- ---------------------------------------------------------------- prove it, in the migration
--
-- A migration that says it widened a vocabulary and did not is exactly the class of thing this
-- store's tests exist to catch, so it catches itself. Reads the constraint back out of the
-- catalog: the new word present, and every one of migration 1's seventeen still present.

DO $$
DECLARE
  def     text;
  missing text;
BEGIN
  SELECT pg_get_constraintdef(c.oid) INTO def
    FROM pg_constraint c
    JOIN pg_class     t ON t.oid = c.conrelid
    JOIN pg_namespace n ON n.oid = t.relnamespace
   WHERE n.nspname = 'brain' AND t.relname = 'thread' AND c.conname = 'thread_kind_check';

  IF def IS NULL THEN
    RAISE EXCEPTION 'migration 13: thread_kind_check is not on brain.thread after this migration';
  END IF;

  SELECT string_agg(w, ', ') INTO missing
    FROM unnest(ARRAY['post','claim','note','msg','ask','answer','done','block','fail','reopen',
                      'cancel','artifact','heartbeat','reap','set','accept','event','budget']) AS w
   WHERE def NOT LIKE '%''' || w || '''%';

  IF missing IS NOT NULL THEN
    RAISE EXCEPTION 'migration 13: thread_kind_check lost or never gained: %. Definition is %',
                    missing, def;
  END IF;
END $$;

INSERT INTO brain.schema_migration (version, name)
VALUES (13, '0013_thread_budget_kind') ON CONFLICT (version) DO NOTHING;

COMMIT;
