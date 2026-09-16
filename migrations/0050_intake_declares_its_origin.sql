-- migration 50: an intake item says WHO PUT IT THERE, so the badge can count only what is
-- genuinely waiting on a human.
--
-- Row 0438, his ask 7's quality condition. Written 2026-08-31.
--
-- ------------------------------------------------------------------ the condition he attached
--
-- He granted the intake badge on 2026-08-28 (*"I do want intake to have a badge and an unread
-- count"*), overruling MUST-NOT-BUILD item 7 for that surface only, ON THE CONDITION that it
-- counts what is genuinely waiting on him. IT CURRENTLY FAILS THAT CONDITION. Measured on the
-- live store 2026-08-31:
--
--     id 2  intake-heartbeat-2026-08-29   inbox   <- the runtime's own daily heartbeat
--     id 3  how-intake-works-now          inbox   <- actually his
--     id 4  intake-heartbeat-2026-08-30   inbox   <- the runtime's own daily heartbeat
--
-- The badge reads 3. One of those three is waiting on a human. A badge that is two thirds noise
-- on its third day is a badge that stops being read, which is the incident item 7 was written
-- against arriving by a different route than the one it was written about.
--
-- ------------------------------------------------------------------ WHY A COLUMN AND NOT A RULE
--
-- A LANE ALREADY REFUSED THE OBVIOUS FIX AND WAS RIGHT TO. The cheap version is to filter
-- `source_name NOT LIKE 'intake-heartbeat-%'` in the render layer. That is a machine INFERRING
-- what a row is from what it happens to be called, forever, in the layer furthest from the fact.
-- It breaks the day the workflow is renamed, it hides any future human row that happens to match,
-- and nothing anywhere records that the hiding is happening.
--
-- `produced_by` already exists on this table and is the natural home for the fact. It is NULL on
-- every row, has always been NULL on every row, and nothing writes it, so reading it would be a
-- filter over a column that never has a value: a rule that can only ever say "human", which is
-- the answer it gives today by accident.
--
-- So the origin is DECLARED BY THE WRITER at the door, and this migration is where it goes.
--
-- ------------------------------------------------------------------ UNDECLARED MEANS HUMAN
--
-- AND THAT IS THE CONSERVATIVE DIRECTION, WHICH IS THE WHOLE REASON IT IS THE DEFAULT. The two
-- ways to be wrong are not symmetric:
--
--   * an undeclared MACHINE row counted as human  ->  the badge reads one too high. He looks, he
--     sees a heartbeat, he accepts it. Cost: a glance.
--   * an undeclared HUMAN row counted as machine  ->  something waiting on him NEVER SURFACES.
--     Cost: the thing the badge exists to prevent.
--
-- This is `signals.py`'s first rule applied to a new field: a missing signal is conservative,
-- never convenient. A machine that wants to be quiet has to say so.
--
-- ------------------------------------------------------------------ THE ONE-TIME BACKFILL
--
-- THIS FILE DOES EDIT ROWS, unlike migration 48, and the difference is worth stating because the
-- thing it does is the thing the paragraph above says not to do.
--
-- A one-time repair with a stated basis is not the same as an inference rule that runs forever.
-- The rule was refused because it would decide EVERY FUTURE ROW from its name, silently, in the
-- render layer, with no record. This backfill decides TWO ROWS THAT ALREADY EXIST, once, in a
-- file the operator reads before he applies it, and asserts out loud how many it touched. After
-- it runs, nothing consults a name again: the n8n workflow declares itself and the sweep records
-- the declaration.
--
-- IT IS ALSO REVERSIBLE IN ONE STATEMENT and that statement is here:
--
--     UPDATE brain.objective SET origin = NULL WHERE source_name LIKE 'intake-heartbeat-%';
--
-- IF THE COUNT IS NOT WHAT THIS FILE EXPECTS IT SAYS SO AND STILL APPLIES. The count is a NOTICE
-- and not an exception, because this file may be applied to a store with three heartbeats or with
-- none, and refusing to add a column because a data repair found a different number of rows than
-- a comment predicted would be a migration failing over its own footnote.
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
  SELECT name INTO taken FROM brain.schema_migration WHERE version = 50;
  IF taken IS NOT NULL AND taken <> '0050_intake_declares_its_origin' THEN
    RAISE EXCEPTION 'ledger version 50 is already held by %', taken
      USING HINT = 'Renumber this file to the next free version and re-run.';
  END IF;
END $$;

BEGIN;

-- ------------------------------------------------------------------ the column

ALTER TABLE brain.objective
  ADD COLUMN IF NOT EXISTS origin text;

ALTER TABLE brain.objective
  DROP CONSTRAINT IF EXISTS objective_origin_check;
ALTER TABLE brain.objective
  ADD CONSTRAINT objective_origin_check
    CHECK (origin IS NULL OR origin IN ('human', 'machine'));

COMMENT ON COLUMN brain.objective.origin IS
  'Who put this here: ''human'' or ''machine'', DECLARED at the intake door by the writer and '
  'never inferred from the name. NULL means undeclared, which READS AS HUMAN everywhere, because '
  'the two ways to be wrong are not symmetric: an undeclared machine row costs him a glance, and '
  'an undeclared human row would never surface at all.';

-- ------------------------------------------------------------------ the read, in one place
--
-- ONE FUNCTION AND NOT A COALESCE COPIED INTO THREE QUERIES. The badge counts it, the intake room
-- lists it, and the suite asserts over it. Three copies of `COALESCE(origin, 'human')` is three
-- places for the default to drift, and the default IS the safety property here.

CREATE OR REPLACE FUNCTION brain.objective_origin(raw text) RETURNS text
  LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE WHEN lower(btrim(COALESCE(raw, ''))) = 'machine' THEN 'machine' ELSE 'human' END
$$;

COMMENT ON FUNCTION brain.objective_origin(text) IS
  'Fold an origin onto ''human'' or ''machine''. Anything unset or unreadable is HUMAN, which is '
  'the conservative direction: it surfaces. Only an explicit ''machine'' is quiet.';

GRANT EXECUTE ON FUNCTION brain.objective_origin(text) TO brain_runtime, brain_subscriber;

-- ------------------------------------------------------------------ what is waiting on a human
--
-- THE VIEW IS THE BADGE'S ONE DEFINITION. `web/model.py::intake_waiting_count` counts rows from
-- here rather than writing the predicate itself, so the number in the shell and the number the
-- store would give cannot come apart.

CREATE OR REPLACE VIEW brain.objective_waiting AS
  SELECT o.*, brain.objective_origin(o.origin) AS origin_resolved
    FROM brain.objective o
   WHERE o.state = 'inbox'
     AND brain.objective_origin(o.origin) = 'human';

COMMENT ON VIEW brain.objective_waiting IS
  'Intake items genuinely waiting on a human: state inbox AND a resolved origin of human. This is '
  'the badge''s definition and the only one. The heartbeat keeps landing in brain.objective and '
  'keeps NOT appearing here, which is the point: its absence on a given day is the signal that '
  'the push path died, and it can only be that signal if it is still being written.';

GRANT SELECT ON brain.objective_waiting TO brain_runtime, brain_subscriber;

-- ------------------------------------------------------------------ the one-time backfill

DO $$
DECLARE n int;
BEGIN
  UPDATE brain.objective
     SET origin = 'machine'
   WHERE origin IS NULL
     AND source_name LIKE 'intake-heartbeat-%';
  GET DIAGNOSTICS n = ROW_COUNT;
  RAISE NOTICE 'migration 50: one-time backfill marked % existing heartbeat row(s) as machine. '
               'Undo with: UPDATE brain.objective SET origin = NULL WHERE source_name LIKE '
               '''intake-heartbeat-%%'';', n;
END $$;

-- ------------------------------------------------------------------ the proof, watched running

DO $$
DECLARE n int := 0; bad int;
BEGIN
  -- undeclared reads as human, which is the safety property
  IF brain.objective_origin(NULL) <> 'human' THEN
    RAISE EXCEPTION 'an undeclared origin did not read as human'; END IF;
  n := n + 1;
  IF brain.objective_origin('') <> 'human' OR brain.objective_origin('  ') <> 'human'
     OR brain.objective_origin('garbage') <> 'human' THEN
    RAISE EXCEPTION 'an unreadable origin did not read as human'; END IF;
  n := n + 1;
  IF brain.objective_origin('machine') <> 'machine'
     OR brain.objective_origin('MACHINE') <> 'machine' THEN
    RAISE EXCEPTION 'an explicit machine origin did not read as machine'; END IF;
  n := n + 1;

  -- THE CHECK, WATCHED REFUSING. A third word is not a quieter machine, it is a typo, and a typo
  -- that stored would read as human forever with nobody told.
  BEGIN
    INSERT INTO brain.objective (name, origin)
      VALUES ('migration 50 probe, never committed', 'robot');
    RAISE EXCEPTION 'objective_origin_check accepted the value ''robot''';
  EXCEPTION WHEN check_violation THEN
    n := n + 1;
  END;

  -- the view agrees with the predicate, over the whole table rather than over a remembered row
  SELECT count(*) INTO bad FROM brain.objective_waiting
   WHERE state <> 'inbox' OR origin_resolved <> 'human';
  IF bad <> 0 THEN RAISE EXCEPTION '% row(s) in objective_waiting break its own predicate', bad;
  END IF;
  n := n + 1;

  RAISE NOTICE 'migration 50: % of 5 checks passed, including the CHECK watched refusing. '
               'brain.objective holds % row(s); % inbox; % waiting on a human.',
               n,
               (SELECT count(*) FROM brain.objective),
               (SELECT count(*) FROM brain.objective WHERE state = 'inbox'),
               (SELECT count(*) FROM brain.objective_waiting);
  IF n <> 5 THEN RAISE EXCEPTION 'migration 50: expected 5 checks, ran %', n; END IF;
END $$;

INSERT INTO brain.schema_migration (version, name)
  VALUES (50, '0050_intake_declares_its_origin')
  ON CONFLICT (version) DO NOTHING;

COMMIT;
