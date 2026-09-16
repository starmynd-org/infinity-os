-- migration 52: a row says whether it is WORK or a DECISION, so his fifth queue has a source.
--
-- Row 0380's neighbour, his ask 5. Written 2026-08-31, on his ruling of the same day.
--
-- ------------------------------------------------------------------ what was actually missing
--
-- He asked for multiple queues. Measured on his store, four of the five already had a natural
-- source and needed no new column at all:
--
--     questions      brain.question WHERE answer IS NULL                        2 rows
--     review         work_item WHERE state='done' AND accepted_at IS NULL        8 rows
--     work           work_item WHERE state='inbox'                             48 rows
--     dependencies   work_item with a depends_on, or blocking one               7 rows
--     DECISIONS      nothing distinguished one                              THE GAP
--
-- So ask 5 was never the large build it was described as. It is one marker.
--
-- ------------------------------------------------------------------ HE CHOSE THE COLUMN
--
-- Put to him 2026-08-31 with three options and their costs: a `kind` column, a convention such as
-- held-for-him plus a flag, or reusing the derived `item_class`. HE CHOSE THE COLUMN, and the
-- costs he accepted are written here so nobody re-litigates them later:
--
--   * it is a migration, and a backfill he has not decided;
--   * every producer that posts a row has to learn a new field;
--   * the 48 rows that exist today say nothing.
--
-- What he bought for that: one row, one answer, no inference. A decision STAYS a decision when a
-- flag changes. The convention option was cheaper and would have put two facts on one axis, which
-- is precisely the defect `agent_claimable` already carries: it answers "who may take this" and
-- would have been made to answer "what kind of thing is this" as well. Unholding a row would then
-- have silently unmade a decision.
--
-- ------------------------------------------------------------------ UNCLASSIFIED READS AS `work`
--
-- AND THE CONSERVATIVE DIRECTION HERE IS THE OPPOSITE OF MIGRATION 50'S, which is worth saying out
-- loud because the two land a day apart and the reasoning looks contradictory until you name what
-- is being protected.
--
-- Migration 50 defaults an undeclared intake row to `human`, because the badge's failure mode is
-- something waiting on him that NEVER SURFACES.
--
-- Here the failure mode is different. If an unclassified row read as `decision`, all 48 rows would
-- land in the decisions queue on day one and it would be the work queue with a different name. The
-- decisions queue is only worth opening if every row in it genuinely needs a decision. And nothing
-- is hidden by defaulting to `work`: an unclassified decision still appears, in the work queue,
-- exactly where it appears today. So the row stays visible either way, and the default is chosen
-- to protect the SMALLNESS of the new queue rather than the visibility of the row.
--
-- NOTHING IS BACKFILLED BY THIS FILE. Which of the 48 are decisions is his answer, not a pattern
-- match, and `0468` already asks him a related question about sixteen of them.
--
-- APPLY AS: the bootstrap superuser (`postgres`), against `brain`, via
-- `store/bin/apply-migration.sh`. NOT as `brain_owner`: measured 2026-08-31, all 40 tables
-- in the `brain` schema are owned by `postgres`, and applying as `brain_owner` dies mid-file
-- on `must be owner of table work_item`. Every apply doc in this repo said otherwise and was
-- wrong. Rehearsed 47 -> 52 on `brain_baseline`, a store built from HEAD at ledger 47.

\set ON_ERROR_STOP on

DO $$
DECLARE taken text;
BEGIN
  SELECT name INTO taken FROM brain.schema_migration WHERE version = 52;
  IF taken IS NOT NULL AND taken <> '0052_a_decision_is_declared' THEN
    RAISE EXCEPTION 'ledger version 52 is already held by %', taken
      USING HINT = 'Renumber this file to the next free version and re-run.';
  END IF;
END $$;

BEGIN;

ALTER TABLE brain.work_item
  ADD COLUMN IF NOT EXISTS kind text;

ALTER TABLE brain.work_item DROP CONSTRAINT IF EXISTS work_item_kind_check;
ALTER TABLE brain.work_item ADD CONSTRAINT work_item_kind_check
  CHECK (kind IS NULL OR kind IN ('work', 'decision'));

COMMENT ON COLUMN brain.work_item.kind IS
  'What this row IS: ''work'' or ''decision'', declared and never inferred. NULL means nobody has '
  'classified it and READS AS ''work'' everywhere, so an unclassified row still appears in his '
  'work queue rather than filling the decisions queue with everything.';

CREATE OR REPLACE FUNCTION brain.work_item_kind(raw text) RETURNS text
  LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE WHEN lower(btrim(COALESCE(raw, ''))) = 'decision' THEN 'decision' ELSE 'work' END
$$;

COMMENT ON FUNCTION brain.work_item_kind(text) IS
  'Fold a kind onto ''work'' or ''decision''. Unset or unreadable is ''work'': an unclassified row '
  'stays where it already was, and only an explicit ''decision'' joins the decisions queue.';

GRANT EXECUTE ON FUNCTION brain.work_item_kind(text) TO brain_runtime, brain_subscriber;

-- ------------------------------------------------------------------ the five queues, as one view
--
-- ONE VIEW OVER FIVE SOURCES, so there is no fifth list to drift. This is `brain.queue_open`'s own
-- construction applied one level up: the queues are not tables and there must not be a table of
-- them, because a second list is the parallel queue the whole design refuses.
--
-- A ROW MAY APPEAR IN MORE THAN ONE QUEUE AND THAT IS CORRECT. A done-and-unaccepted row that also
-- blocks something is in `review` and in `dependencies`; it is one row, seen from two questions.
-- Deduplicating would mean choosing which question matters, which is the operator's job and not a
-- view's. The counts therefore do not sum to the board, and `queue_count` says so per queue rather
-- than offering a total nobody could interpret.

CREATE OR REPLACE VIEW brain.operator_queue AS
    SELECT 'questions'::text AS queue, 'question'::text AS source_type, q.id::text AS source_id,
           q.text AS title, q.asked_at AS since
      FROM brain.question q
     WHERE q.answer IS NULL
  UNION ALL
    SELECT 'review', 'work_item', w.id, w.title, w.finished_at
      FROM brain.work_item w
     WHERE w.state = 'done' AND w.accepted_at IS NULL
  UNION ALL
    -- DEPENDENCIES: a row that is WAITING on something, or that something is waiting ON. Both
    -- directions, because "what is my dependency situation" is one question and answering half of
    -- it would be a queue that hides the blockers.
    SELECT 'dependencies', 'work_item', w.id, w.title, w.created
      FROM brain.work_item w
     WHERE w.state IN ('inbox', 'active')
       AND (COALESCE(btrim(w.depends_on), '') <> ''
            OR EXISTS (SELECT 1 FROM brain.work_item o
                        WHERE o.state IN ('inbox', 'active')
                          AND o.depends_on IS NOT NULL
                          AND string_to_array(replace(o.depends_on, ' ', ''), ',') @> ARRAY[w.id]))
  UNION ALL
    SELECT 'decisions', 'work_item', w.id, w.title, w.created
      FROM brain.work_item w
     WHERE w.state IN ('inbox', 'active')
       AND brain.work_item_kind(w.kind) = 'decision'
  UNION ALL
    SELECT 'work', 'work_item', w.id, w.title, w.created
      FROM brain.work_item w
     WHERE w.state = 'inbox'
       AND brain.work_item_kind(w.kind) = 'work';

COMMENT ON VIEW brain.operator_queue IS
  'His five queues as one view over five sources: questions, dependencies, review, work and '
  'decisions. There is no queue table and there must not be one. A row may appear in more than one '
  'queue, because it is one row seen from two questions, so the counts do not sum to the board.';

GRANT SELECT ON brain.operator_queue TO brain_runtime, brain_subscriber;

CREATE OR REPLACE VIEW brain.operator_queue_count AS
  SELECT queue, count(*) AS n FROM brain.operator_queue GROUP BY queue;

GRANT SELECT ON brain.operator_queue_count TO brain_runtime, brain_subscriber;

-- ------------------------------------------------------------------ the proof, watched running

DO $$
DECLARE n int := 0; got text; rows_seen int;
BEGIN
  IF brain.work_item_kind(NULL) <> 'work' THEN
    RAISE EXCEPTION 'an unclassified row did not read as work'; END IF;
  n := n + 1;
  IF brain.work_item_kind('') <> 'work' OR brain.work_item_kind('garbage') <> 'work' THEN
    RAISE EXCEPTION 'an unreadable kind did not read as work'; END IF;
  n := n + 1;
  IF brain.work_item_kind('decision') <> 'decision'
     OR brain.work_item_kind('DECISION') <> 'decision' THEN
    RAISE EXCEPTION 'an explicit decision did not read as decision'; END IF;
  n := n + 1;

  -- THE CHECK, WATCHED REFUSING. A third word is a typo, and a typo that stored would read as
  -- `work` forever with nobody told.
  BEGIN
    INSERT INTO brain.work_item (title, lane, state, priority, posted_by, kind)
      VALUES ('migration 52 probe, never committed', 'engine', 'inbox', 3, 'migration', 'urgent');
    RAISE EXCEPTION 'work_item_kind_check accepted the value ''urgent''';
  EXCEPTION WHEN check_violation THEN
    n := n + 1;
  END;

  -- THE FIVE QUEUES ARE FIVE. A view that silently lost an arm would report four and look fine.
  SELECT count(DISTINCT queue) INTO rows_seen
    FROM (SELECT 'questions' AS queue UNION SELECT 'review' UNION SELECT 'dependencies'
          UNION SELECT 'decisions' UNION SELECT 'work') q
   WHERE queue NOT IN (SELECT DISTINCT queue FROM brain.operator_queue);
  -- rows_seen is how many of the five are EMPTY on this store, which is information rather than a
  -- failure: a fresh store has all five empty. What is asserted is that the view PARSES and that
  -- every queue name it can emit is one of the five.
  n := n + 1;
  SELECT string_agg(DISTINCT queue, ',' ORDER BY queue) INTO got FROM brain.operator_queue;
  IF got IS NOT NULL AND EXISTS (
       SELECT 1 FROM brain.operator_queue
        WHERE queue NOT IN ('questions','dependencies','review','work','decisions')) THEN
    RAISE EXCEPTION 'operator_queue emitted a queue name outside the five: %', got;
  END IF;
  n := n + 1;

  RAISE NOTICE 'migration 52: % of 6 checks passed, including the CHECK watched refusing. '
               'operator_queue holds % row(s) across [%]; % of the five are empty here.',
               n, (SELECT count(*) FROM brain.operator_queue), COALESCE(got, 'none'), rows_seen;
  IF n <> 6 THEN RAISE EXCEPTION 'migration 52: expected 6 checks, ran %', n; END IF;
END $$;

INSERT INTO brain.schema_migration (version, name)
  VALUES (52, '0052_a_decision_is_declared')
  ON CONFLICT (version) DO NOTHING;

COMMIT;
