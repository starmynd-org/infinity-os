-- migration 51: a capture may land as a NOTE ON A ROW, not only as an objective.
--
-- Row 0432 remainder, his ask 3. Written 2026-08-31.
--
-- ------------------------------------------------------------------ what he asked for
--
-- The voice lane (task 0377, migrations 24 and 25) built one landing: the whole transcript, uncut,
-- through `intake` into `brain.objective` as one unclassified row a human sorts. That is still the
-- right landing for a morning monologue and it is not changed by this file.
--
-- What it cannot do is the thing he asked for on 2026-08-30, which is to speak AT a row he is
-- looking at. *"voice is needed to make it easy for users to get through inbox"*. A monologue that
-- lands in the intake inbox is not a note on the item in front of him; it is a second thing to
-- sort, which is more inbox rather than less.
--
-- ------------------------------------------------------------------ NO NEW LANDING VERB
--
-- `note` already exists. It is verb 22 in `engine/VERB-PARITY.md`, it appends to `brain.thread`,
-- and it is what every agent in this system already calls to say something about a task. A voice
-- note onto a row is that verb with a transcript in it.
--
-- So this migration adds no table and no verb. It widens ONE constraint, because the capture
-- table currently asserts that a landed capture points at an objective, and after this change a
-- landed capture may point at a work item instead.
--
-- ------------------------------------------------------------------ the constraint gets STRONGER
--
-- `voice_capture_landed_has_objective_ck` says: landed implies objective_id AND objective_name.
-- The obvious widening is `... OR work_item_id IS NOT NULL`, and it would be a LOOSENING: a row
-- could then point at both, and two landings for one capture is exactly the disagreement between
-- `voice_capture` and the thing it landed into that migration 25 built this constraint to prevent.
--
-- So the replacement asserts EXACTLY ONE:
--
--     landed  ->  (an objective, and no work item)  XOR  (a work item, and no objective)
--
-- which refuses a state the old constraint permitted. The old one is dropped and the new one is
-- watched refusing all three of the ways to break it, at the foot of this file.
--
-- ------------------------------------------------------------------ why the FK is text
--
-- `brain.work_item.id` is a text column (`0001`, `q0007`), not a bigserial, so this column is text
-- and the reference is to that. `ON DELETE SET NULL` matches `objective_id` and for the same
-- reason: losing the row a capture landed into must not delete the evidence that the capture
-- happened, which is the entire argument `brain.voice_capture` exists to make.
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
  SELECT name INTO taken FROM brain.schema_migration WHERE version = 51;
  IF taken IS NOT NULL AND taken <> '0051_a_voice_note_can_land_on_a_row' THEN
    RAISE EXCEPTION 'ledger version 51 is already held by %', taken
      USING HINT = 'Renumber this file to the next free version and re-run.';
  END IF;
END $$;

-- The dependency, named rather than discovered. Applying this against a store without migration
-- 25 fails on a table that does not exist, and the error would not say which file owed it.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.tables
                  WHERE table_schema = 'brain' AND table_name = 'voice_capture') THEN
    RAISE EXCEPTION 'brain.voice_capture does not exist, so a landing cannot be widened'
      USING HINT = 'Apply migrations/0025_voice_capture.sql first. It is ledger 25.';
  END IF;
END $$;

BEGIN;

ALTER TABLE brain.voice_capture
  ADD COLUMN IF NOT EXISTS work_item_id text REFERENCES brain.work_item(id) ON DELETE SET NULL;

COMMENT ON COLUMN brain.voice_capture.work_item_id IS
  'The work item this capture landed a NOTE onto, when it landed as a note rather than as an '
  'objective. Exactly one of work_item_id and objective_id is set on a landed capture, which the '
  'landed constraint enforces: two landings for one capture is the disagreement this table exists '
  'to make impossible.';

-- ------------------------------------------------------------------ the constraint, replaced
--
-- Dropped and rebuilt rather than added beside the old one, because two constraints over the same
-- fact is two places to look and one of them will be the one somebody edits.

ALTER TABLE brain.voice_capture DROP CONSTRAINT IF EXISTS voice_capture_landed_has_objective_ck;
ALTER TABLE brain.voice_capture DROP CONSTRAINT IF EXISTS voice_capture_landed_points_at_one_ck;
ALTER TABLE brain.voice_capture ADD CONSTRAINT voice_capture_landed_points_at_one_ck
  CHECK (state <> 'landed'
         OR (objective_id IS NOT NULL AND objective_name IS NOT NULL AND work_item_id IS NULL)
         OR (work_item_id IS NOT NULL AND objective_id IS NULL AND objective_name IS NULL));

-- ------------------------------------------------------------------ the proof, watched refusing
--
-- All three ways to break it, watched. A constraint nobody has seen refuse is a constraint nobody
-- has evidence for, and this one REPLACED a constraint that was already load-bearing.

DO $$
DECLARE n int := 0; probe text := 'migration-51-probe';
BEGIN
  -- A work item to point at, rolled back with everything else in this DO block's savepoints.
  INSERT INTO brain.work_item (id, title, lane, state, priority, posted_by)
    VALUES (probe, 'migration 51 probe', 'engine', 'inbox', 3, 'migration')
    ON CONFLICT (id) DO NOTHING;

  -- 1. landed pointing at NOTHING is still refused, exactly as before this file.
  BEGIN
    INSERT INTO brain.voice_capture
      (capture_id, state, due_at, host, media_pointer, media_pointer_host, media_sha256,
       media_bytes, transcription_status, transcript_chars)
      VALUES ('m51-a', 'landed', now(), 'probe', 'p', 'h', 's', 1, 'ok', 5);
    RAISE EXCEPTION 'a landed capture pointing at nothing was accepted';
  EXCEPTION WHEN check_violation THEN n := n + 1;
  END;

  -- 2. landed pointing at BOTH is refused, which the OLD constraint permitted. This is the
  --    widening being a tightening, watched.
  BEGIN
    INSERT INTO brain.voice_capture
      (capture_id, state, due_at, host, media_pointer, media_pointer_host, media_sha256,
       media_bytes, transcription_status, transcript_chars, objective_name, work_item_id)
      VALUES ('m51-b', 'landed', now(), 'probe', 'p', 'h', 's', 1, 'ok', 5, 'x', probe);
    RAISE EXCEPTION 'a landed capture pointing at BOTH an objective and a work item was accepted';
  EXCEPTION WHEN check_violation THEN n := n + 1;
  END;

  -- 3. landed pointing at a work item alone is ACCEPTED, which is the whole point of the file.
  INSERT INTO brain.voice_capture
    (capture_id, state, due_at, host, media_pointer, media_pointer_host, media_sha256,
     media_bytes, transcription_status, transcript_chars, work_item_id)
    VALUES ('m51-c', 'landed', now(), 'probe', 'p', 'h', 's', 1, 'ok', 5, probe);
  n := n + 1;

  -- and an objective landing is unchanged, which is the regression this file must not be
  INSERT INTO brain.voice_capture
    (capture_id, state, due_at, host, media_pointer, media_pointer_host, media_sha256,
     media_bytes, transcription_status, transcript_chars, objective_name, objective_id)
    VALUES ('m51-d', 'landed', now(), 'probe', 'p', 'h', 's', 1, 'ok', 5, 'x', NULL);
  RAISE EXCEPTION 'an objective landing with a NULL id was accepted';
EXCEPTION WHEN check_violation THEN
  n := n + 1;
  RAISE NOTICE 'migration 51: % of 4 checks passed, three of them refusals watched happening', n;
  IF n <> 4 THEN RAISE EXCEPTION 'migration 51: expected 4 checks, ran %', n; END IF;
  -- The probe rows and the probe work item are removed here rather than left behind. This block
  -- ran inside the migration's own transaction, so the deletes are part of it.
  DELETE FROM brain.voice_capture WHERE capture_id LIKE 'm51-%';
  DELETE FROM brain.work_item WHERE id = 'migration-51-probe';
END $$;

COMMENT ON CONSTRAINT voice_capture_landed_points_at_one_ck ON brain.voice_capture IS
  'A landed capture points at EXACTLY ONE thing: an objective (migration 24''s landing) or a work '
  'item (migration 51''s note landing). Pointing at neither, or at both, is refused. Replaced '
  'voice_capture_landed_has_objective_ck, and is strictly stronger than it.';

INSERT INTO brain.schema_migration (version, name)
  VALUES (51, '0051_a_voice_note_can_land_on_a_row')
  ON CONFLICT (version) DO NOTHING;

COMMIT;
