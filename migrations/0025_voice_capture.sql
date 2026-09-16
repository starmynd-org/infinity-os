-- migration 25: the capture attempt becomes a row, so that a recording that never happened is
-- a thing you can COUNT rather than a thing nobody noticed.
--
-- Task 0377, lane V2, 2026-08-18. Companion to migration 24, which made voice an intake FORMAT
-- on `brain.objective`. This one is the other half and it is the half the lane actually exists
-- for.
--
-- THE INCIDENT THIS TABLE IS SHAPED BY. On 2026-08-17 the operator's daily monologue saved as
-- 0 bytes. He believed it had saved. Nobody found out until a human opened the file by hand.
-- Every part of that is silence, and silence has one structural cause: the ONLY record of the
-- recording was the recording. When the artefact is the only evidence the attempt happened, an
-- attempt that produces nothing produces no evidence either, and there is nothing left to alarm
-- on. You cannot count an absence you never wrote down.
--
-- So the rule this table implements is one sentence: THE ROW IS WRITTEN BEFORE THE AUDIO EXISTS.
-- `voice open` inserts `state='recording'` and a `due_at` and commits BEFORE the microphone is
-- opened. From that instant on there are only three possible outcomes and all three are visible:
-- the capture reaches `landed`, or it reaches `failed` and says at which stage, or it sits in a
-- non-terminal state past `due_at` and is STUCK. The third one is the outage shape and it is the
-- one no error handler catches, because nothing raised.
--
-- MEASURED, and this is not hypothetical: on 2026-08-18 between roughly 01:00Z and 07:00Z this
-- host lost its network. Thirty swarm tasks stopped, for six hours, and paged nobody -- because
-- a task that has BEGUN and not finished raises no exception anywhere. That is the same failure
-- as the 0-byte save wearing different clothes, and `verdict = 'stuck'` in the view below is
-- what makes it a number on a health line instead of something a human happens to check.
--
-- WHY A NEW TABLE IS NOT A SECOND DOOR. V00 freezes: one transition function per state change,
-- exposed as a verb, called by every surface. The thing it forbids is a SECOND path into an
-- EXISTING state change -- a recorder that INSERTs its own objective, say. A capture attempt's
-- lifecycle is not an existing state change; it is a new one, and a new state change gets one
-- new transition each, in `voice/voice_capture/transitions.py`, registered in the same
-- `store.transition` registry as every other verb in this system and reachable through
-- `store.apply` from any surface. The LANDING, which IS an existing state change, adds no verb
-- at all: it calls `intake`, the function that already exists, with more keyword arguments.
--
-- APPLY AS: brain owner, against `brain`. The Claude Code permission classifier refuses agent
-- writes to the live database (V00), so this file was proven on a clone and is handed to the
-- operator to apply. See voice/docs/APPLY.md for the clone, the exact lines and the proof.

\set ON_ERROR_STOP on

-- The prefix in the filename is a per-lane counter and it lies. The recorded version below is
-- the key, and two lanes reaching for the same number is the collision that matters, because
-- every file here writes ON CONFLICT (version) DO NOTHING: the loser applies its DDL and skips
-- its ledger row, so it re-applies forever and the ledger never catches up. Same guard queue's
-- migration 23 carries, same reason.
DO $$
DECLARE taken text;
BEGIN
  SELECT name INTO taken FROM brain.schema_migration WHERE version = 25;
  IF taken IS NOT NULL AND taken <> '0025_voice_capture' THEN
    RAISE EXCEPTION 'schema version 25 is already held by %, not 0025_voice_capture. Renumber by '
                    'reading brain.schema_migration AND scanning all three schema directories '
                    '(migrations/, budget/schema/, queue/schema/), never by listing one of them.',
                    taken;
  END IF;
END $$;

BEGIN;

SET search_path TO brain, public;

-- ------------------------------------------------------------------ the ledger of attempts

CREATE TABLE IF NOT EXISTS brain.voice_capture (
  id                   bigserial PRIMARY KEY,

  -- Chosen by the caller BEFORE anything is recorded, so the audio file, the store row and the
  -- log line all carry the same string and a human can join them by eye at 3am.
  capture_id           text NOT NULL UNIQUE,

  opened_at            timestamptz NOT NULL DEFAULT now(),
  updated_at           timestamptz NOT NULL DEFAULT now(),

  -- recording  -> the row exists and the microphone is (or should be) open. NOT terminal.
  -- recorded   -> a file exists and PASSED the save check. Not just "the recorder returned 0".
  -- retained   -> the audio has been moved to durable storage and re-hashed THERE.
  -- transcribed-> a transcription engine ran and stated an outcome, including "I heard nothing".
  -- landed     -> an objective row exists and points at this capture. Terminal, good.
  -- failed     -> terminal, bad, and it must say which stage and why (check `failed_states_ck`).
  state                text NOT NULL CHECK (state IN
                         ('recording','recorded','retained','transcribed','landed','failed')),

  -- THE LOAD-BEARING COLUMN OF THIS WHOLE TABLE, and the only one with no analogue anywhere
  -- else in this schema. Written at open time by the caller, which is the only party that knows
  -- how long it asked the microphone for. A capture that is not terminal after this instant is
  -- STUCK, and stuck is reported as a failure rather than as pending. Without it, a process
  -- killed between `open` and `recorded` leaves a row that reads "in flight" forever and is
  -- indistinguishable, to every reader, from one that is genuinely still recording.
  due_at               timestamptz NOT NULL,

  source               text NOT NULL DEFAULT 'mic' CHECK (source IN ('mic','file')),
  host                 text NOT NULL,
  requested_seconds    integer,

  -- Where the recorder wrote it. VOLATILE by design (a temp directory), which is exactly why
  -- `retain` is a stage of its own rather than an implementation detail of recording.
  scratch_pointer      text,

  -- The retained audio. SAME POSTURE AS brain.transcript AND brain.objective: a pointer and a
  -- hash, never the blob. `media_pointer_host` is not optional, for the reason migration 1
  -- gives about `transcript.pointer_host` -- an absolute path means something only on the host
  -- it is absolute on, and a later VPS move has to break loudly.
  media_pointer        text,
  media_pointer_host   text,
  media_sha256         text,
  media_bytes          bigint,
  media_kind           text,
  media_duration_s     numeric,

  -- The same four-value vocabulary migration 24 puts on `brain.objective`, deliberately spelled
  -- out again rather than shared through a domain: this row exists for captures that never
  -- reached an objective at all, and a FK to a vocabulary is not what makes them agree -- the
  -- landing transition copying one to the other is.
  --   ok           text the engine stands behind
  --   partial      text for SOME of the audio, and the engine said so
  --   null         the engine ran and could not make out the speech. NO TEXT.
  --   unavailable  no engine ran. NO TEXT.
  transcription_engine text,
  transcription_status text CHECK (transcription_status IS NULL
                         OR transcription_status IN ('ok','partial','null','unavailable')),

  -- Kept because a transcript is not a boolean. The SAPI baseline on this host returns 0.646 on
  -- clean synthesised speech, so "it transcribed" and "it transcribed well" are different facts
  -- and a reader who cannot tell them apart will trust the wrong one.
  transcription_confidence numeric,
  transcript_chars     integer,

  -- The landing, once there is one.
  objective_id         bigint REFERENCES brain.objective(id) ON DELETE SET NULL,
  objective_name       text,

  -- The failure, when there is one. `failure_stage` is closed, because the four stages are the
  -- four different things the operator does about it.
  failure_stage        text CHECK (failure_stage IS NULL
                         OR failure_stage IN ('save','upload','transcribe','land')),
  failure_detail       text NOT NULL DEFAULT '',
  failed_at            timestamptz,

  actor_type           brain.actor_type,
  produced_by          text
);

-- ------------------------------------------------------------------ the CHECKs

-- 1. A FAILURE THAT CANNOT SAY WHAT FAILED IS REFUSED. This is the one that stops a caller from
--    "handling" an error by parking the row in a terminal state with an empty reason, which is
--    the swallow the hook does and which the brief names as the defect being closed.
ALTER TABLE brain.voice_capture DROP CONSTRAINT IF EXISTS voice_capture_failed_states_ck;
ALTER TABLE brain.voice_capture ADD CONSTRAINT voice_capture_failed_states_ck
  CHECK (state <> 'failed'
         OR (failure_stage IS NOT NULL AND btrim(failure_detail) <> '' AND failed_at IS NOT NULL));

-- 2. A state that CLAIMS the audio is durable must be able to point at it. `retained` without a
--    pointer is the 0-byte save with better manners.
ALTER TABLE brain.voice_capture DROP CONSTRAINT IF EXISTS voice_capture_retained_has_media_ck;
ALTER TABLE brain.voice_capture ADD CONSTRAINT voice_capture_retained_has_media_ck
  CHECK (state NOT IN ('retained','transcribed','landed')
         OR (media_pointer IS NOT NULL AND media_pointer_host IS NOT NULL
             AND media_sha256 IS NOT NULL AND media_bytes IS NOT NULL AND media_bytes > 0));

-- 3. A state that claims a transcription stage ran must carry its verdict, including the two
--    verdicts that mean "no words".
ALTER TABLE brain.voice_capture DROP CONSTRAINT IF EXISTS voice_capture_transcribed_has_status_ck;
ALTER TABLE brain.voice_capture ADD CONSTRAINT voice_capture_transcribed_has_status_ck
  CHECK (state NOT IN ('transcribed','landed') OR transcription_status IS NOT NULL);

-- 4. NEVER FABRICATE A TRANSCRIPTION, asserted here as well as on `brain.objective`, because
--    this row is the one that survives when no objective was ever written. `null` and
--    `unavailable` mean the engine produced nothing, so a positive character count under either
--    is a fabrication however it got there, and `ok`/`partial` assert there IS text, so a zero
--    count under those is the 0-byte save arriving through the transcription door.
ALTER TABLE brain.voice_capture DROP CONSTRAINT IF EXISTS voice_capture_no_words_no_chars_ck;
ALTER TABLE brain.voice_capture ADD CONSTRAINT voice_capture_no_words_no_chars_ck
  CHECK (transcription_status IS NULL
         OR (transcription_status IN ('null','unavailable')
             AND COALESCE(transcript_chars, 0) = 0)
         OR (transcription_status IN ('ok','partial')
             AND transcript_chars IS NOT NULL AND transcript_chars > 0));

-- 5. `landed` means an objective exists. A landing that cannot name what it landed into is not
--    a landing, and this is what makes `voice_capture` unable to disagree with `objective`.
ALTER TABLE brain.voice_capture DROP CONSTRAINT IF EXISTS voice_capture_landed_has_objective_ck;
ALTER TABLE brain.voice_capture ADD CONSTRAINT voice_capture_landed_has_objective_ck
  CHECK (state <> 'landed' OR (objective_id IS NOT NULL AND objective_name IS NOT NULL));

CREATE INDEX IF NOT EXISTS voice_capture_state_idx     ON brain.voice_capture (state);
CREATE INDEX IF NOT EXISTS voice_capture_opened_at_idx ON brain.voice_capture (opened_at DESC);
CREATE INDEX IF NOT EXISTS voice_capture_due_at_idx    ON brain.voice_capture (due_at)
  WHERE state NOT IN ('landed','failed');

-- ------------------------------------------------------------------ the counted surface
--
-- One verdict per capture, computed in the database rather than in whichever caller happens to
-- be asking. Two readers that each classify "stuck" for themselves will eventually disagree
-- about it, and the day they disagree is the day the health line is wrong in the reassuring
-- direction.
--
-- `landed-without-words` is deliberately NOT folded into `failed`. The audio is retained and the
-- objective exists; what is missing is the text. That is recoverable by re-transcribing later
-- with a better engine, and calling it `failed` would either hide the recovery or make the
-- failure count un-clearable. It is still counted, and `voice health` still exits non-zero on
-- it, because on a machine whose only local engine scores 0.646 this will be the common case and
-- the operator has to be told, not protected from it.

CREATE OR REPLACE VIEW brain.voice_capture_health AS
SELECT
  c.capture_id,
  c.state,
  c.opened_at,
  c.due_at,
  c.host,
  c.source,
  CASE
    WHEN c.state = 'failed'                                          THEN 'failed'
    WHEN c.state <> 'landed' AND now() > c.due_at                    THEN 'stuck'
    WHEN c.state = 'landed'
     AND c.transcription_status IN ('null','unavailable')            THEN 'landed-without-words'
    WHEN c.state = 'landed'                                          THEN 'ok'
    ELSE                                                                  'in-flight'
  END AS verdict,
  c.failure_stage,
  c.failure_detail,
  c.transcription_engine,
  c.transcription_status,
  c.transcription_confidence,
  c.transcript_chars,
  c.media_pointer,
  c.media_pointer_host,
  c.media_sha256,
  c.media_bytes,
  c.media_duration_s,
  c.objective_id,
  c.objective_name,
  -- How long it has been un-terminal. NULL once it is terminal, so a reader cannot accidentally
  -- render an age for a capture that finished last week.
  CASE WHEN c.state IN ('landed','failed') THEN NULL
       ELSE now() - c.opened_at END AS open_for
FROM brain.voice_capture c;

COMMENT ON TABLE brain.voice_capture IS
  'One row per capture ATTEMPT, written before the audio exists. That order is the whole point: '
  'when the artefact is the only evidence the attempt happened, an attempt that produces nothing '
  'produces no evidence and cannot be counted. 2026-08-17, the operator''s monologue saved as '
  '0 bytes and nobody noticed. Task 0377.';
COMMENT ON COLUMN brain.voice_capture.due_at IS
  'After this instant a non-terminal capture is STUCK, not pending. The outage shape: on '
  '2026-08-18 a six-hour network loss stopped 30 tasks and raised nothing anywhere, because work '
  'that has begun and not finished throws no exception. A deadline is what turns that into a '
  'number.';
COMMENT ON COLUMN brain.voice_capture.transcription_status IS
  'ok | partial | null | unavailable. A CHECK refuses a positive transcript_chars under '
  'null/unavailable and a zero one under ok/partial. Unintelligible audio is a NULL, not a '
  'guess: D3 measured that rule on stated_goal and this is it made structural.';
COMMENT ON VIEW brain.voice_capture_health IS
  'The counted surface. One verdict per capture -- ok, in-flight, stuck, failed, '
  'landed-without-words -- computed here so that two readers cannot disagree about what stuck '
  'means. `voice health` renders this and exits non-zero on anything but ok/in-flight.';

-- ------------------------------------------------------------------ grants

GRANT SELECT, INSERT, UPDATE ON brain.voice_capture TO brain_runtime;
GRANT USAGE, SELECT ON brain.voice_capture_id_seq   TO brain_runtime;
GRANT SELECT ON brain.voice_capture_health          TO brain_runtime;

-- No DELETE, to anyone but the owner. A capture row is the evidence that an attempt was made,
-- and an evidence table a caller can silently empty is not evidence: V00's rule that a record
-- you can silently rewrite is not a record, applied to the one table whose entire job is to
-- outlive the thing it describes.

-- The operator's own console reads this and nothing else needs to. Conditional for the same
-- reason queue's migration 23 is: `brain_operator` exists only on a host where
-- store/bin/provision-operator.sh has run, and a migration that assumed it would fail on every
-- other host.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'brain_operator') THEN
    EXECUTE 'GRANT SELECT, INSERT, UPDATE ON brain.voice_capture TO brain_operator';
    EXECUTE 'GRANT USAGE, SELECT ON brain.voice_capture_id_seq TO brain_operator';
    EXECUTE 'GRANT SELECT ON brain.voice_capture_health TO brain_operator';
  END IF;
END $$;

INSERT INTO brain.schema_migration (version, name)
VALUES (25, '0025_voice_capture') ON CONFLICT (version) DO NOTHING;

COMMIT;
