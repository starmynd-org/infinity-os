-- migration 24: voice becomes an intake FORMAT, not a subsystem.
--
-- Task 0377, lane V2. Written by T5 on 2026-08-18 as `0023_objective_voice_intake.sql`,
-- RENUMBERED to 24 by T4 the same day and the reason is worth keeping, because it broke every
-- lane in this tree and not only this one:
--
--   The file recorded NO `brain.schema_migration` row. `engine/bin/scratch-db.sh` reads each
--   file's version out of its own INSERT and `die`s when a file records none, so EVERY door of
--   that script -- create, migrate, ensure, ledger -- returned rc=1 for as long as this file sat
--   in the working tree. Measured 2026-08-18 before the fix: `scratch-db.sh ledger` rc=1,
--   `scratch-db.sh ensure` rc=1, both printing "0023_objective_voice_intake.sql records no
--   brain.schema_migration row." A harness that refuses at every door makes every suite in the
--   repo unreadable, and the cause is in a migration one lane had not applied yet.
--
--   The 0023 PREFIX also collided: `queue/schema/0012_operator_time_entry.sql` records ledger
--   version 23. Prefixes are per-lane counters and lie (scratch-db.sh says so at length); the
--   recorded version is the key. 24 was the next free one at 2026-08-18T08:00Z.
--
-- The narrow waist (D00/V00) says one transition function per state change, exposed as a verb,
-- called by every surface. A voice note is an INPUT FORMAT and not a new kind of thing, so it
-- lands through the transition that already exists -- `intake`, engine/swarm_engine/
-- transitions.py -- and this migration gives that transition somewhere to put the facts a
-- spoken note carries that a typed one does not. No new table, no new verb, no second door.
--
-- Nine nullable columns and three CHECKs. Existing text intake is untouched: every column is
-- nullable, `intake_format` defaults to 'text', and the CHECKs are written so that a row with
-- all nine NULL passes each of them.
--
-- APPLY AS: brain owner, against `brain`. The Claude Code permission classifier refuses agent
-- writes to the live database (V00), so this file was proven on a clone and is handed to the
-- operator to apply. The clone and its proof are named in voice/docs/APPLY.md.

BEGIN;

-- ------------------------------------------------------------------ the format discriminator
--
-- 'text'  the drop-folder path that has always existed.
-- 'voice' a recording. `media_*` then describes the audio and MUST be populated (check 2).

ALTER TABLE brain.objective
  ADD COLUMN IF NOT EXISTS intake_format text NOT NULL DEFAULT 'text';

-- ------------------------------------------------------------------ the audio, pointed at
--
-- SAME POSTURE AS brain.transcript, AND FOR THE SAME REASON: a pointer and a hash, never the
-- blob. `media_pointer_host` is not optional for the reason migration 1 gives about
-- `transcript.pointer_host` -- an absolute path is only meaningful on the host it is absolute
-- ON, and a later VPS move must break loudly rather than silently.

ALTER TABLE brain.objective
  ADD COLUMN IF NOT EXISTS media_pointer      text,
  ADD COLUMN IF NOT EXISTS media_pointer_host text,
  ADD COLUMN IF NOT EXISTS media_sha256       text,
  ADD COLUMN IF NOT EXISTS media_bytes        bigint,
  ADD COLUMN IF NOT EXISTS media_kind         text;

-- ------------------------------------------------------------------ the transcription's own honesty
--
-- `transcription_status` is the load-bearing column and it is why this migration exists at all.
--
--   ok           the engine returned text it stands behind. `body` is the transcript.
--   partial      the engine returned text for SOME of the audio and said so. `body` is that
--                text and it is INCOMPLETE. A reader must not treat it as the whole note.
--   null         the engine ran and could not make out the speech. `body` IS EMPTY.
--   unavailable  no engine ran at all -- none installed, or it crashed. `body` IS EMPTY.
--
-- D3 measured this rule on `stated_goal`: "a fabricated goal is worse than a null one, because
-- it will be believed later." Check 3 makes it structural instead of conventional. A path that
-- guesses at unintelligible audio cannot store the guess: the INSERT is refused.

ALTER TABLE brain.objective
  ADD COLUMN IF NOT EXISTS transcription_engine text,
  ADD COLUMN IF NOT EXISTS transcription_status text,
  ADD COLUMN IF NOT EXISTS media_duration_s     numeric;

-- ------------------------------------------------------------------ the three CHECKs

-- 1. The vocabulary is closed. A typo'd status must not read as a fourth state nobody handles.
ALTER TABLE brain.objective DROP CONSTRAINT IF EXISTS objective_intake_format_ck;
ALTER TABLE brain.objective ADD CONSTRAINT objective_intake_format_ck
  CHECK (intake_format IN ('text', 'voice'));

ALTER TABLE brain.objective DROP CONSTRAINT IF EXISTS objective_transcription_status_ck;
ALTER TABLE brain.objective ADD CONSTRAINT objective_transcription_status_ck
  CHECK (transcription_status IS NULL
         OR transcription_status IN ('ok', 'partial', 'null', 'unavailable'));

-- 2. A voice row cannot lose its audio. If the format says voice, the pointer, the host, the
--    hash and the status are all present. This is what stops a landing path from recording the
--    words and forgetting the recording -- which is one half of the 2026-08-17 incident.
ALTER TABLE brain.objective DROP CONSTRAINT IF EXISTS objective_voice_needs_media_ck;
ALTER TABLE brain.objective ADD CONSTRAINT objective_voice_needs_media_ck
  CHECK (intake_format <> 'voice'
         OR (media_pointer IS NOT NULL AND media_pointer_host IS NOT NULL
             AND media_sha256 IS NOT NULL AND transcription_status IS NOT NULL));

-- 3. NEVER FABRICATE A TRANSCRIPTION. A status of 'null' or 'unavailable' means the engine
--    produced nothing, and a row that says so while carrying a body is a fabrication however it
--    got there. The table refuses it. `btrim` so that whitespace is not a loophole.
--    The mirror half: 'ok' and 'partial' assert there IS text, so an empty body under either is
--    refused too -- that is the 0-byte save arriving through the other door.
ALTER TABLE brain.objective DROP CONSTRAINT IF EXISTS objective_null_transcript_has_no_body_ck;
ALTER TABLE brain.objective ADD CONSTRAINT objective_null_transcript_has_no_body_ck
  CHECK (transcription_status IS NULL
         OR (transcription_status IN ('null', 'unavailable') AND btrim(body) = '')
         OR (transcription_status IN ('ok', 'partial')       AND btrim(body) <> ''));

COMMENT ON COLUMN brain.objective.intake_format IS
  'text | voice. Voice is an input FORMAT, not a subsystem: both land through the same `intake` '
  'transition. Task 0377.';
COMMENT ON COLUMN brain.objective.media_pointer IS
  'Absolute path to the retained audio ON media_pointer_host. A pointer and a hash, never the '
  'blob -- the posture brain.transcript states and the reason it states it.';
COMMENT ON COLUMN brain.objective.transcription_status IS
  'ok | partial | null | unavailable. A CHECK refuses a non-empty body under null/unavailable '
  'and an empty one under ok/partial. Unintelligible audio is a NULL, not a guess: D3 measured '
  'that rule on stated_goal and this is it made structural.';

INSERT INTO brain.schema_migration (version, name)
VALUES (24, '0024_objective_voice_intake') ON CONFLICT (version) DO NOTHING;

COMMIT;
