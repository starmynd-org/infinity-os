-- migration 29: an image is a POINTER, A HASH AND A HOST, and its absence is a finding.
--
-- Task 0167, lane V5, 2026-08-18. The smallest table in this schema and it has one job: make
-- "there is a picture on this item" a claim that can be CHECKED rather than a file somebody
-- remembers putting somewhere.
--
-- THE POSTURE IS INHERITED, NOT INVENTED. `brain.transcript` (migration 1) and
-- `brain.voice_capture` (migration 25) already store a pointer, a host and a sha256 and never
-- the blob, and migration 1 states the reason on `transcript.pointer_host`: v1 is local-attended,
-- every pointer is absolute on THIS host, and a later VPS move invalidates all of them. Recording
-- which host makes that failure loud instead of silent. An image is the same shape of object as a
-- transcript -- a file on a disk that a row makes a claim about -- so it gets the same columns
-- rather than a third opinion about where content lives.
--
-- WHAT MAKES THIS TABLE WORTH EXISTING RATHER THAN A `kind='image'` ROW IN `brain.artifact`.
-- `artifact` records that a path was PRODUCED, once, with `exists_at_record` stamped at write
-- time; it is an append-only log of events on the filesystem. An attachment is a CURRENT claim
-- about an item that the console re-verifies on every render, and it needs three things
-- `artifact` has no column for: the sha256 to re-check against, a lifecycle (a landing that can
-- get stuck, a failure that says which stage), and the one-image-per-item rule below. Writing
-- those into `artifact` would change what every existing artifact row means.
--
-- THE ROW IS WRITTEN BEFORE THE BYTES ARE TRUSTED. Same rule as migration 25, one incident
-- later. `image attach` commits `state='landing'` with a `due_at` BEFORE the file is read, so a
-- process killed between the read and the hash leaves a row that is STUCK rather than leaving
-- nothing at all. An attach whose only evidence is the attachment produces no evidence when it
-- produces no attachment, and that is the 2026-08-17 0-byte voice save wearing different clothes.
--
-- AND ON FAILURE, NOTHING IS ATTESTED. The design's copy for a failed attach is verbatim
-- `nothing was recorded ... no pointer was written` (V10-C SPEC section 3.3c, rendered in
-- outputs/2026-08-18-V10-design/synthesis/prototype.html:748). `image_attachment_failed_ck`
-- below is that sentence enforced by the database: in state `failed`, `pointer`, `pointer_host`,
-- `sha256` and `bytes` are ALL NULL and `failed_stage` is NOT NULL. What survives the failure is
-- the ATTEMPT -- which file was asked for, on which host, and where it died -- and an attempt is
-- not an attachment. That is what lets the console keep a failed attach on screen until the
-- operator acts on it without the row ever claiming an image is there.
--
-- ONE IMAGE, IN PLACE. `image_attachment_one_live_idx` is a partial unique index over
-- (subject_type, subject_id) WHERE state IN ('landing','attached'). "No gallery, no viewer, no
-- thumbnails" is the brief's instruction to the renderer; this is the same instruction to the
-- table, so a second lane cannot make a gallery possible by writing a second row.
--
-- NO BLOB, AND THE ABSENCE IS CHECKED RATHER THAN PROMISED. There is no `bytea` and no `oid`
-- column here, and on 2026-08-18 there was none anywhere in schema `brain`
-- (`information_schema.columns WHERE data_type IN ('bytea','oid')` returned 0 rows on the live
-- store). `web/tests/test_images.py::test_no_blob_column_anywhere_in_the_schema` asserts that
-- over the whole schema rather than over this table, because the way image bytes get into
-- Postgres is not somebody adding them here, it is somebody adding them somewhere else.
--
-- APPLY AS: brain owner, against `brain`. The Claude Code permission classifier refuses agent
-- writes to the live database (V00), so this file was proven on a clone and is handed to the
-- operator to apply. See web/docs/APPLY-IMAGES.md for the clone, the exact lines and the proof.

\set ON_ERROR_STOP on

-- The filename prefix is a per-lane counter and it lies. The version below is the key. Read at
-- 2026-08-18T18:35Z: the live ledger is at 27, and 28 is already SPOKEN FOR by
-- queue/schema/0013_queue_open_shows_what_the_fleet_will_not_take.sql, which is written and not
-- yet applied -- so scanning `brain.schema_migration` alone would have handed this file 28 and
-- broken the other lane's apply. Scan all four schema directories (migrations/, budget/schema/,
-- queue/schema/, voice/schema/) AND the ledger, never one of them.
DO $$
DECLARE taken text;
BEGIN
  SELECT name INTO taken FROM brain.schema_migration WHERE version = 29;
  IF taken IS NOT NULL AND taken <> '0029_image_attachment' THEN
    RAISE EXCEPTION 'schema version 29 is already held by %, not 0029_image_attachment. Renumber '
                    'by reading brain.schema_migration AND scanning all four schema directories '
                    '(migrations/, budget/schema/, queue/schema/, voice/schema/), never by '
                    'listing one of them.', taken;
  END IF;
END $$;

BEGIN;

SET search_path TO brain, public;

CREATE TABLE IF NOT EXISTS brain.image_attachment (
  id              bigserial PRIMARY KEY,

  -- Chosen by the caller BEFORE the file is opened, so the log line, the store row and the
  -- console's receipt all carry the same string and a human can join them by eye.
  attach_id       text NOT NULL UNIQUE,

  -- WHAT THE IMAGE IS ON. Deliberately polymorphic and deliberately not a FK: the brief names
  -- three different homes (a work item, a queue item, a voice note) and a queue item is itself
  -- two tables. `brain.event` already carries (subject_type, subject_id) with no FK; this table
  -- goes one better with the resolver trigger below, because V00's rule is that an unresolvable
  -- entity gets refused rather than invented.
  subject_type    text NOT NULL CHECK (subject_type IN
                    ('work_item','question','recommendation','voice_capture','objective')),
  subject_id      text NOT NULL,

  --   landing   the row exists, the bytes have not been attested. NOT terminal; past `due_at`
  --             it is STUCK, which is reported as a failure rather than as pending.
  --   attached  a pointer, a host, a sha256 and a byte count, all measured. Terminal, good.
  --   failed    terminal, bad, and it says which stage. NOTHING is attested (see the CHECK).
  --   detached  the operator removed the pointer. The file on disk is untouched, and the row
  --             is superseded rather than deleted -- a record you can silently erase is not one.
  state           text NOT NULL CHECK (state IN ('landing','attached','failed','detached')),

  opened_at       timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),

  -- The same load-bearing column migration 25 introduced, for the same reason. A landing that is
  -- not terminal after this instant is stuck, and stuck is a number on a health line instead of
  -- a thing nobody happens to check.
  due_at          timestamptz NOT NULL,

  -- THE REQUEST, AND IT IS NOT AN ATTESTATION. What the operator pointed at, kept so a failed
  -- attach can say which file it was about. It carries no hash and nothing renders it as an
  -- image: `pointer` below is the only column this system treats as a claim that a file is
  -- there, and the failed-state CHECK keeps it NULL.
  requested_path  text NOT NULL,
  requested_host  text NOT NULL,

  -- THE ATTESTATION. Every one of these is measured from the bytes, after reading them.
  pointer         text,
  pointer_host    text,
  sha256          text,
  bytes           bigint,
  -- Detected from the file's MAGIC BYTES, never from its extension. An extension is a claim the
  -- filename makes about itself; the first eight bytes are the fact. V5 owns this vocabulary
  -- (DESIGN-SYSTEM.md section 10 leaves MIME and size limits to this lane) and it is closed:
  -- the four formats every browser renders without a plugin.
  mime            text CHECK (mime IS NULL OR mime IN
                    ('image/png','image/jpeg','image/gif','image/webp')),
  attached_at     timestamptz,
  attached_by     text NOT NULL DEFAULT '',
  actor_type      brain.actor_type,

  -- THE FAILURE, and it names the stage rather than saying "error".
  failed_stage    text CHECK (failed_stage IS NULL OR failed_stage IN ('read','hash','record')),
  failed_reason   text,

  detached_at     timestamptz,
  detached_by     text,

  -- THE OPERATOR ACTED ON A FAILED ATTACH. The design requires a failed attach to persist until
  -- acted on -- "a failed attach that fades is a swallow" -- which means acting on it has to be
  -- a recorded act rather than a dismissal the console remembers for seven seconds. Stamped by
  -- `image dismissed`, and the row STAYS in state `failed`: the evidence that the attempt was
  -- made and died is exactly what this table exists to keep, so discarding hides the finding
  -- from the card and destroys nothing.
  dismissed_at    timestamptz,
  dismissed_by    text,

  produced_by     text
);

-- `attached` means all four measurements exist. A row asserting an image with no hash to check
-- it against is exactly the confidently-wrong record this schema exists to prevent.
ALTER TABLE brain.image_attachment DROP CONSTRAINT IF EXISTS image_attachment_attested_ck;
ALTER TABLE brain.image_attachment ADD CONSTRAINT image_attachment_attested_ck CHECK (
  state NOT IN ('attached','detached')
  OR (pointer IS NOT NULL AND pointer_host IS NOT NULL AND sha256 IS NOT NULL
      AND bytes IS NOT NULL AND mime IS NOT NULL AND attached_at IS NOT NULL));

-- The design's own words, enforced: `nothing was recorded ... no pointer was written`.
ALTER TABLE brain.image_attachment DROP CONSTRAINT IF EXISTS image_attachment_failed_ck;
ALTER TABLE brain.image_attachment ADD CONSTRAINT image_attachment_failed_ck CHECK (
  state <> 'failed'
  OR (pointer IS NULL AND pointer_host IS NULL AND sha256 IS NULL AND bytes IS NULL
      AND failed_stage IS NOT NULL));

-- A landing has attested nothing yet either. Without this a caller could write the pointer at
-- open time and the `landing` state would be a decoration over an already-made claim.
ALTER TABLE brain.image_attachment DROP CONSTRAINT IF EXISTS image_attachment_landing_ck;
ALTER TABLE brain.image_attachment ADD CONSTRAINT image_attachment_landing_ck CHECK (
  state <> 'landing'
  OR (pointer IS NULL AND sha256 IS NULL AND bytes IS NULL AND failed_stage IS NULL));

ALTER TABLE brain.image_attachment DROP CONSTRAINT IF EXISTS image_attachment_detached_ck;
ALTER TABLE brain.image_attachment ADD CONSTRAINT image_attachment_detached_ck CHECK (
  state <> 'detached' OR detached_at IS NOT NULL);

-- Only a failed attempt is dismissed. An ATTACHED image is removed by `image detached`, which
-- is a different act on a different row and produces a different receipt; letting one column
-- serve both would make "the operator dealt with a failure" and "the operator removed a picture"
-- indistinguishable in the one table a later reader would count them from.
ALTER TABLE brain.image_attachment DROP CONSTRAINT IF EXISTS image_attachment_dismissed_ck;
ALTER TABLE brain.image_attachment ADD CONSTRAINT image_attachment_dismissed_ck CHECK (
  dismissed_at IS NULL OR state = 'failed');

-- 64 lowercase hex. A hash column that accepts anything is a hash column that will one day hold
-- the word `unknown`, and every reader downstream will compare against it.
ALTER TABLE brain.image_attachment DROP CONSTRAINT IF EXISTS image_attachment_sha_ck;
ALTER TABLE brain.image_attachment ADD CONSTRAINT image_attachment_sha_ck CHECK (
  sha256 IS NULL OR sha256 ~ '^[0-9a-f]{64}$');

-- A zero-byte image is the 2026-08-17 voice save again. It is a failed attach, not an attachment.
ALTER TABLE brain.image_attachment DROP CONSTRAINT IF EXISTS image_attachment_bytes_ck;
ALTER TABLE brain.image_attachment ADD CONSTRAINT image_attachment_bytes_ck CHECK (
  bytes IS NULL OR bytes > 0);

-- ONE IMAGE, IN PLACE. The no-gallery rule, made structural.
CREATE UNIQUE INDEX IF NOT EXISTS image_attachment_one_live_idx
  ON brain.image_attachment (subject_type, subject_id)
  WHERE state IN ('landing','attached');

CREATE INDEX IF NOT EXISTS image_attachment_subject_idx
  ON brain.image_attachment (subject_type, subject_id, id);

-- ---------------------------------------------------------------- the subject resolver
--
-- V00: "Never fabricate a pointer or an id. An unresolvable entity gets NULL plus the raw ref
-- plus a status and a non-zero exit." There is no NULL available here -- an attachment with no
-- subject is not a degraded attachment, it is an orphan -- so the unresolvable case is refused
-- at the table. Five subject types, five id types, one function, and it runs on INSERT only:
-- a subject row deleted later is a dangling ref for the console to render as a finding, which is
-- the same posture the file on disk gets, and turning it into an UPDATE-time refusal would make
-- `image detached` fail on exactly the rows that most need detaching.

CREATE OR REPLACE FUNCTION brain.image_attachment_subject_exists() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE found boolean;
BEGIN
  CASE NEW.subject_type
    WHEN 'work_item' THEN
      SELECT EXISTS (SELECT 1 FROM brain.work_item WHERE id = NEW.subject_id) INTO found;
    WHEN 'question' THEN
      SELECT EXISTS (SELECT 1 FROM brain.question WHERE id = NEW.subject_id) INTO found;
    WHEN 'recommendation' THEN
      IF NEW.subject_id ~ '^[0-9]+$' THEN
        SELECT EXISTS (SELECT 1 FROM brain.recommendation
                        WHERE id = NEW.subject_id::bigint) INTO found;
      ELSE
        found := false;
      END IF;
    WHEN 'voice_capture' THEN
      SELECT EXISTS (SELECT 1 FROM brain.voice_capture
                      WHERE capture_id = NEW.subject_id) INTO found;
    WHEN 'objective' THEN
      IF NEW.subject_id ~ '^[0-9]+$' THEN
        SELECT EXISTS (SELECT 1 FROM brain.objective
                        WHERE id = NEW.subject_id::bigint) INTO found;
      ELSE
        found := false;
      END IF;
    ELSE
      found := false;
  END CASE;
  IF NOT found THEN
    RAISE EXCEPTION 'no brain.% row with id %. An image is attached TO something; a row naming a '
                    'subject that does not exist is an orphan, and this schema refuses an '
                    'invented pointer the same way it refuses an invented id.',
                    NEW.subject_type, NEW.subject_id
      USING ERRCODE = 'foreign_key_violation';
  END IF;
  RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS image_attachment_subject_exists ON brain.image_attachment;
CREATE TRIGGER image_attachment_subject_exists
  BEFORE INSERT ON brain.image_attachment
  FOR EACH ROW EXECUTE FUNCTION brain.image_attachment_subject_exists();

-- ---------------------------------------------------------------- health
--
-- The same shape as `brain.voice_capture_health` and for the same reason: the outage this table
-- can have is a LANDING that never terminated, and nothing raises on it.

CREATE OR REPLACE VIEW brain.image_attachment_health AS
SELECT a.attach_id, a.subject_type, a.subject_id, a.state, a.opened_at, a.due_at,
       a.requested_path, a.requested_host, a.pointer, a.pointer_host, a.sha256, a.bytes,
       a.failed_stage, a.failed_reason, a.dismissed_at,
       CASE
         WHEN a.state = 'attached' THEN 'ok'
         WHEN a.state = 'detached' THEN 'detached'
         WHEN a.state = 'failed' AND a.dismissed_at IS NOT NULL THEN 'failed-dismissed'
         WHEN a.state = 'failed'   THEN 'failed'
         WHEN a.due_at < now()     THEN 'stuck'
         ELSE 'landing'
       END AS verdict
  FROM brain.image_attachment a;

COMMENT ON TABLE brain.image_attachment IS
  'A pointer, a host, a sha256 and a byte count. NEVER the blob, and never in an event payload '
  '(brain.event.payload_summary has a 4096-byte ceiling enforced by refusal, so an image in one '
  'would be refused rather than truncated -- but the rule here is the posture, not the ceiling). '
  'Absence is a finding, not a blank: the console re-verifies pointer and hash at render time '
  'and renders MISSING red and CHANGED amber.';
COMMENT ON COLUMN brain.image_attachment.requested_path IS
  'The REQUEST, not an attestation. What the operator pointed at, kept so a failed attach can '
  'name the file it was about. `pointer` is the only column that claims a file is there.';
COMMENT ON COLUMN brain.image_attachment.pointer_host IS
  'Which host `pointer` is absolute ON. Same reason as brain.transcript.pointer_host: v1 is '
  'local-attended and a VPS move has to break loudly rather than silently.';
COMMENT ON COLUMN brain.image_attachment.mime IS
  'From the magic bytes, never the extension. Closed vocabulary, V5 (task 0167).';
COMMENT ON VIEW brain.image_attachment_health IS
  'verdict=stuck is the outage shape: a landing past its due_at. Nothing raises on it, which is '
  'why it is a row here rather than an exception somewhere.';

-- ---------------------------------------------------------------- grants
--
-- No DELETE to anyone but the owner, for the reason migration 25 gives: the row is the evidence
-- that an attach was attempted, and evidence a caller can silently empty is not evidence.
GRANT SELECT, INSERT, UPDATE ON brain.image_attachment      TO brain_runtime;
GRANT USAGE, SELECT ON brain.image_attachment_id_seq        TO brain_runtime;
GRANT SELECT ON brain.image_attachment_health               TO brain_runtime;

-- Conditional for the reason migrations 23 and 25 are: `brain_operator` exists only on a host
-- where store/bin/provision-operator.sh has run.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'brain_operator') THEN
    EXECUTE 'GRANT SELECT, INSERT, UPDATE ON brain.image_attachment TO brain_operator';
    EXECUTE 'GRANT USAGE, SELECT ON brain.image_attachment_id_seq TO brain_operator';
    EXECUTE 'GRANT SELECT ON brain.image_attachment_health TO brain_operator';
  END IF;
END $$;

INSERT INTO brain.schema_migration (version, name)
VALUES (29, '0029_image_attachment') ON CONFLICT (version) DO NOTHING;

COMMIT;
