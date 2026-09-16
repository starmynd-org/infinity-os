-- migration 27: what the transcript may be USED for, which is not the same question as whether
-- there is one.
--
-- Task 0405, lane V2/ingest, 2026-08-18. Companion to migrations 24 and 25, which recorded
-- WHETHER an engine produced text (`transcription_status`) and WHICH engine did
-- (`transcription_engine`). This one records what that text is worth, and it exists because
-- those two facts measurably came apart on this host.
--
-- THE MEASUREMENT THIS COLUMN IS SHAPED BY. `windows-sapi` returned `transcription_status='ok'`
-- -- text it stood behind, mean confidence 0.79 -- on a transcript of the operator's real
-- 2026-08-17 monologue in which SIX OF SIXTEEN key proper nouns were destroyed. A client's
-- name came out as "a lively" and then "gladly". `TLDV`, `VSL`, `QC` and `starmynd.com` were
-- gone entirely. `Infinity OS` became "Infiniti OS".
--
-- Status was not lying. The transcript WAS whole and the engine DID stand behind it. Status
-- answers "are there words and are they complete". It does not answer "may I resolve a client
-- name out of this", and on 2026-08-18 those had different answers. A reader who had only
-- `status='ok'` would have believed the names.
--
-- AND THE NAMES ARE THE PART THAT GETS BELIEVED. A proper noun turned into a different real
-- English word does not read as an error, it reads as a fact. That is the same defect class as
-- a fabricated transcription -- the rule this lane was built on, *a fabricated goal is worse
-- than a null one, because it will be believed later* -- arriving through a door that
-- `transcription_status` was never watching.
--
-- THE VOCABULARY IS CLOSED, and each value is a MEASUREMENT on a named host and date rather than
-- a claim about the software:
--
--   names-reliable  proper nouns survive. Safe to resolve an entity from this body.
--                   whisper.cpp small.en, measured 2026-08-18: WER 0.0138, 15 of 16.
--   shape-only      the shape of the day survives and THE NAMES IN IT DO NOT. Safe for "what was
--                   Monday about", unsafe as input to anything that looks a name up.
--                   windows-sapi, measured 2026-08-18: WER 0.1481, 8 of 16.
--   unmeasured      nobody has scored this engine on this host. Assume neither of the above.
--
-- WHY IT IS STORED AND NOT DERIVED FROM `transcription_engine`. It looks derivable and it is
-- not: it is a historical fact about the transcript, not a property of the engine name. When an
-- engine is re-measured, or its model is swapped for a larger one, the class of transcripts
-- ALREADY WRITTEN must not silently change underneath a reader who trusted it. A derived value
-- would rewrite the past every time the present was re-measured.
--
-- NULLABLE, and null means "written before this column existed". Not backfilled to a guess:
-- every row already in the table was produced by `windows-sapi`, but backfilling `shape-only`
-- would state a measurement that was not taken on that row's audio, and inventing a measurement
-- is the exact habit this whole lane refuses.

BEGIN;

ALTER TABLE brain.voice_capture
  ADD COLUMN IF NOT EXISTS transcription_accuracy_class text;

ALTER TABLE brain.voice_capture
  DROP CONSTRAINT IF EXISTS voice_capture_accuracy_class_ck;

ALTER TABLE brain.voice_capture
  ADD CONSTRAINT voice_capture_accuracy_class_ck CHECK (
    transcription_accuracy_class IS NULL
    OR transcription_accuracy_class IN ('names-reliable','shape-only','unmeasured'));

-- A CLASS WITHOUT WORDS IS A CLAIM ABOUT NOTHING. Under `null`/`unavailable` the body is empty
-- by the constraints migration 25 already carries, so a row asserting that its absent text is
-- `names-reliable` is incoherent, and incoherent rows are what a later reader averages.
ALTER TABLE brain.voice_capture
  DROP CONSTRAINT IF EXISTS voice_capture_no_words_no_class_ck;

ALTER TABLE brain.voice_capture
  ADD CONSTRAINT voice_capture_no_words_no_class_ck CHECK (
    transcription_status IS NULL
    OR transcription_status NOT IN ('null','unavailable')
    OR transcription_accuracy_class IS NULL);

COMMENT ON COLUMN brain.voice_capture.transcription_accuracy_class IS
  'What this transcript may be USED for, measured on this host rather than claimed. '
  '`transcription_status` says whether there are words and whether they are whole; this says '
  'whether you may resolve a client or product name out of them. They came apart on 2026-08-18: '
  'windows-sapi returned status=''ok'' on a body with 6 of 16 key proper nouns destroyed, '
  'including the operator''s own client. See voice/docs/ENGINES.md for the numbers.';

INSERT INTO brain.schema_migration (version, name)
  VALUES (27, '0027_transcription_accuracy_class')
  ON CONFLICT (version) DO NOTHING;

COMMIT;
