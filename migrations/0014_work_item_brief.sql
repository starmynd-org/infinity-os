-- migration 14: the posted brief stops being destroyed by the work finishing
--
-- Task 0138, child of 0118. Found by D9 (0118) during the acceptance run and independently by
-- D7's console, which surfaced it on /task/<id> under "Definition of done" and posted it to
-- crosstalk slot 7. Two lanes on one defect.
--
-- Additive and re-runnable: adds one column, one function, one trigger, backfills only where the
-- original text provably survives, drops nothing and edits no applied file. `0001_initial.sql`
-- stays exactly as it is; an applied migration is history, and history is amended by a later
-- migration.
--
-- ================================================================= what was broken
--
-- `brain.work_item` had exactly ONE free-text column, `result`, and two different facts were
-- written into it by turns:
--
--   post    result := the posted body       -- the work order. What to do.
--   done    result := the agent's summary   -- the report. What happened.
--   fail    result := the failure reason    -- and requeues to inbox in the same statement.
--   block   result := the block reason
--   cancel  result := the cancellation reason
--
-- The second write does not amend the first, it erases it. After `done` the posted brief exists
-- nowhere: not in another column, and not on the thread, because the thread's `post` event stores
-- the task TITLE and never the body.
--
-- Measured for this task on 2026-08-16 against a scratch database built from migrations 1-13,
-- and re-measured rather than taken from the brief:
--
--     post --lane engine -f -  <<< 'THE POSTED BRIEF. If this sentence is missing after a
--                                   done+reopen, the work order was destroyed.'      -- 0001
--     claim --agent T1
--     done 0001 --summary 'a summary that is not the brief' --agent T1
--     show 0001                                  -- result: a summary that is not the brief
--     SELECT count(*) FROM brain.thread WHERE text LIKE '%THE POSTED BRIEF%';   -- 0
--
-- `done` ALONE is the destroyer. The brief for this task named `done` + `reopen`, but the reopen
-- is not part of the loss, only part of the consequence: it makes the loss DISPATCHABLE.
-- `engine/bin/swarm-run` builds an agent's prompt from `swarm show "$TASK_ID"`, so after
--
--     reopen 0001 --reason 'rejected: you did not verify' --from admiral
--     show 0001    -- state inbox, attempts 0/2, result: a summary that is not the brief
--
-- attempt 2 is dispatched against attempt 1's summary: the task is worked from the text it was
-- rejected FOR, and the instruction it was rejected for missing is gone. `reopen` is the
-- rejection verb the whole human-acceptance design rests on (D00 rule 3).
--
-- AND `fail` IS THE SECOND DOOR, which the brief did not name and which opens far more often.
-- `fail`'s requeue branch writes the reason into `result` and sets `state = 'inbox'` in one
-- statement, so the same destruction happens with no operator in the loop at all --
-- `engine/bin/swarm-run` reconciles EVERY unreported run to `fail`. Measured the same way:
--
--     post ... 'SECOND POSTED BRIEF. fail requeues, so attempt 2 reads whatever result holds.'
--     claim --agent T1
--     fail 0002 --reason 'attempt 1 ran out of road, next attempt must use the other API' --agent T1
--     show 0002    -- state inbox, result: attempt 1 ran out of road, next attempt must use ...
--
-- It also violates D00 rule 11, "store full text, truncate only renderings", which this port
-- otherwise honours carefully: `_finish` in `engine/swarm_engine/transitions.py` explicitly
-- refuses the file bus's 2000-byte cut, and `engine/swarm_engine/render.py` exists so that only
-- renderings shorten. Storing the brief and then overwriting it is the same rule broken by a
-- different mechanism -- not a truncation, a replacement -- and it cannot be un-done either.
--
-- ================================================================= what this migration changes
--
-- 1. `brain.work_item.brief`, a column of its own. The two facts stop sharing an address:
--    `brief` is the work order and `result` is the latest report.
--
-- 2. A write-once trigger, `work_item_brief_write_once`. The brief is what an attempt is
--    dispatched against, so a verb that quietly replaced it would reintroduce this defect one
--    verb over -- which is exactly how it got here, since `done` overwriting `result` was never
--    an intended act either. The rule is one-directional and it is enforced by the table, not by
--    a test:
--
--      empty -> anything     ALLOWED. A row whose brief was destroyed before this migration can
--                            still be repaired, and a flow that scopes a task after posting it
--                            can still fill one in.
--      non-empty -> other    REFUSED, naming both texts' lengths.
--      non-empty -> empty    REFUSED. Blanking is the destruction this migration exists to stop.
--      non-empty -> itself   ALLOWED, so a re-run or an idempotent write is not a failure.
--
--    Belt and braces with the verb layer, in the direction migration 12 established: `post` is
--    the only writer of `brief` and nothing else names the column, so this trigger is what
--    catches the routes no verb owns -- a future verb, a hand-written UPDATE, a lane that adds a
--    re-brief tomorrow. It does NOT catch a restore (`pg_restore` and
--    `session_replication_role = replica` run with triggers disabled, by design), which is why
--    the guarantee is stated as a COMMENT on the column as well.
--
-- 3. A backfill that recovers the brief ONLY where it provably still exists, and says out loud
--    how many rows it could not recover.
--
--    A never-claimed row's `result` can only be the posted body, because every verb that writes
--    `result` goes through `_hold` and therefore requires a claim. So "never claimed" is the
--    discriminator -- but NOT via `claimed_at IS NULL AND attempts = 0`, which is the obvious
--    test and is wrong: `reopen` sets `claimed_by = ''`, `claimed_at = NULL`, `finished_at =
--    NULL` and `attempts = 0`, so a reopened row is indistinguishable from a fresh one on every
--    one of those columns. That is the precise shape of this defect, and copying its blind spot
--    into the repair would silently promote a rejected summary to a work order.
--
--    `brain.thread` is append-only and no verb deletes from it, so the trail is the durable
--    evidence. A row with no `claim`, `done`, `fail`, `block` or `cancel` event has never had
--    `result` written by anything but `post`.
--
--    Rows that HAVE been through a finisher are left with `brief = ''`. Their briefs are gone and
--    nothing in this database can reconstruct them; inventing one from the summary would be
--    fabrication, and an empty brief that reads as empty is the honest state. The NOTICE at the
--    end names them so the operator knows the size of the hole rather than discovering it one
--    task at a time.
--
-- WHY 14. Read from `brain.schema_migration`, never from `ls`: this repo's migration files live in
-- three directories (`migrations/`, `queue/schema/`, `budget/schema/`) and the ledger is the only
-- truth. Measured 2026-08-16T16:5xZ: the live store `brain` is applied through 9 (1..9, no gaps),
-- and 10, 11, 12, 13 are claimed by files not yet applied there --
-- `migrations/0010_subscriber_identity` = 10, `queue/schema/0009_null_branch_act_scan` = 11 (its
-- own lane moved it from 10 after both files claimed 10), `migrations/0012_parent_cycle_guard` =
-- 12, `migrations/0013_thread_budget_kind` = 13. So 14 is the next free version. Note that `ls
-- migrations/` shows a GAP at 11 and would mislead you into taking it. The guard below fails
-- loudly rather than silently if a third lane took 14 first.

\set ON_ERROR_STOP on

DO $$
DECLARE taken text;
BEGIN
  SELECT name INTO taken FROM brain.schema_migration WHERE version = 14;
  IF taken IS NOT NULL AND taken <> '0014_work_item_brief' THEN
    RAISE EXCEPTION 'schema version 14 is already held by %, not 0014_work_item_brief. Pick the '
                    'next version by reading brain.schema_migration, never by listing a '
                    'directory -- this repo''s migration files live in three directories and '
                    'migrations/ has a visible gap at 11 that is NOT free.', taken;
  END IF;
END $$;

BEGIN;

SET search_path TO brain, public;

-- ---------------------------------------------------------------- the column

-- NOT NULL DEFAULT '' matches `result` next to it, so "no brief" is one value rather than two
-- (NULL and '') that every reader would have to handle alike. On this table '' already means
-- absent: `host`, `depends_on`, `claimed_by`, `blocked_on` and `result` all carry that
-- convention, and `post` writes '' for a task posted with no body.
ALTER TABLE brain.work_item ADD COLUMN IF NOT EXISTS brief text NOT NULL DEFAULT '';

COMMENT ON COLUMN brain.work_item.brief IS
  'The posted work order, written once by `post` and never overwritten. This column exists '
  'because it used to share `result` with the agent''s report: `post` wrote the body there and '
  '`done`, `fail`, `block` and `cancel` each replaced it, so a reopened or requeued task was '
  'dispatched against the summary it had been rejected FOR and the instruction was gone (task '
  '0138, measured 2026-08-16). `brief` is what to do; `result` is what most recently happened. '
  'Write-once is enforced by the work_item_brief_write_once trigger, which permits '''' -> text '
  'so a destroyed brief can still be repaired, and refuses every replacement of a non-empty one. '
  'A trigger cannot cover a restore, which runs with triggers disabled, so treat any code path '
  'that UPDATEs this column as a defect regardless of what the table lets through. Empty means '
  'either the task was posted with no body or its brief was destroyed before migration 14; it '
  'never means the brief is elsewhere.';

-- ---------------------------------------------------------------- write once, enforced

CREATE OR REPLACE FUNCTION brain.work_item_brief_write_once() RETURNS trigger
  LANGUAGE plpgsql AS $$
BEGIN
  -- Filling in an absent brief is a repair, not an overwrite. Every other change is refused.
  IF COALESCE(OLD.brief, '') = '' OR NEW.brief = OLD.brief THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'refusing to overwrite the posted brief of %: it holds % characters and this '
                  'UPDATE would replace them with % characters',
                  OLD.id, length(OLD.brief), length(COALESCE(NEW.brief, ''))
    USING HINT = 'brain.work_item.brief is the work order an attempt is dispatched against, and '
                 'it is write-once for a measured reason: it used to share the `result` column '
                 'with the agent''s report, so finishing a task erased the instructions for it '
                 'and `reopen` then handed attempt 2 the summary attempt 1 was rejected for. '
                 'Report outcomes into `result` via done/fail/block/cancel. If the brief itself '
                 'is genuinely wrong, that is a new task, not an edit: the trail must show which '
                 'work order each attempt actually ran against.';
END $$;

COMMENT ON FUNCTION brain.work_item_brief_write_once() IS
  'The second gate on the posted brief. `post` is the only writer of brain.work_item.brief and no '
  'other verb names the column, so this catches the routes no verb owns -- a future verb, a '
  'hand-written UPDATE, a lane that adds a re-brief tomorrow. It cannot catch a restore, which '
  'runs with triggers disabled; the COMMENT on the column carries the rule for that case.';

DROP TRIGGER IF EXISTS work_item_brief_write_once ON brain.work_item;
CREATE TRIGGER work_item_brief_write_once
  BEFORE UPDATE OF brief ON brain.work_item
  FOR EACH ROW
  EXECUTE FUNCTION brain.work_item_brief_write_once();

-- ---------------------------------------------------------------- recover what survives

-- ONLY rows no finisher has ever touched. `brain.thread` is append-only and no verb deletes from
-- it, so its trail is the evidence; the work_item columns are not, because `reopen` resets
-- claimed_by, claimed_at, finished_at and attempts and leaves a rejected row looking exactly like
-- a fresh one. Deriving the test from those columns instead would promote rejected summaries to
-- work orders, which is this defect wearing the repair's clothes.
--
-- `claim` is the sufficient condition on its own: every verb that writes `result` calls `_hold`,
-- which requires the caller to hold the row, which requires a claim. The finishers are listed
-- alongside it anyway so the predicate states what it is protecting against without the reader
-- having to know `_hold`.
WITH recoverable AS (
  SELECT w.id
    FROM brain.work_item w
   WHERE w.brief = ''
     AND w.result <> ''
     AND NOT EXISTS (SELECT 1 FROM brain.thread t
                      WHERE t.work_item_id = w.id
                        AND t.kind IN ('claim', 'done', 'fail', 'block', 'cancel'))
)
UPDATE brain.work_item w
   SET brief = w.result
  FROM recoverable r
 WHERE w.id = r.id;

-- ---------------------------------------------------------------- say what cannot be recovered

DO $$
DECLARE
  recovered  integer;
  lost_ids   text;
  lost_count integer;
BEGIN
  SELECT count(*) INTO recovered FROM brain.work_item WHERE brief <> '';

  SELECT count(*), string_agg(id, ', ' ORDER BY id)
    INTO lost_count, lost_ids
    FROM brain.work_item w
   WHERE w.brief = ''
     AND EXISTS (SELECT 1 FROM brain.thread t
                  WHERE t.work_item_id = w.id
                    AND t.kind IN ('claim', 'done', 'fail', 'block', 'cancel'));

  RAISE NOTICE 'migration 14: % row(s) had their posted brief recovered from result (never '
               'claimed, so result could only be the body).', recovered;

  IF lost_count > 0 THEN
    RAISE NOTICE 'migration 14: % row(s) have NO recoverable brief and are left empty: %. Their '
                 'posted bodies were overwritten by done/fail/block/cancel before this migration '
                 'and are not in this database. They are NOT reconstructed from the summary: '
                 'that would be fabrication, and a work order invented from a report is worse '
                 'than an absent one. Re-post or re-brief any of these that still needs working.',
                 lost_count, lost_ids;
  END IF;
END $$;

INSERT INTO brain.schema_migration (version, name)
VALUES (14, '0014_work_item_brief') ON CONFLICT (version) DO NOTHING;

COMMIT;
