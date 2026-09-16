-- migration 53: an intake item records WHO wrote it and WHEN IT HAPPENED, so an inbox fed by
-- nine mailboxes and a meeting library can be sorted and attributed at all.
--
-- S1, wave 1, 2026-09-01. Ledger number 53 was ALLOCATED by the wave-1 admiral and announced in
-- `outputs/2026-09-01-sprints/S1/ALLOCATIONS.md` before this file was written, because four
-- commanders were writing migrations on the same day into three schema directories.
--
-- THE LEDGER TRAP, RESTATED SO THE NEXT READER CANNOT FALL INTO IT: the four-digit filename
-- prefix is NOT the ledger version. `queue/schema/0017_queue_open_carries_impact.sql` declares
-- version 49 and is the fourth highest applied row. This file's prefix and its version happen to
-- agree; that is a coincidence of the `migrations/` directory and not a rule. The number that
-- matters is the one in the INSERT at the foot of this file.
--
-- ------------------------------------------------------------------ what is missing, measured
--
-- Measured on the live `brain` store 2026-09-01 through `store.read()`:
--
--     brain.objective            6 rows in its entire life, 24 columns
--     of those 6                 4 are the runtime's own heartbeat, 1 a dropped document,
--                                1 a Monday plan hand-fed on 2026-08-17
--     from a communication channel   ZERO
--
-- The 24 columns carry `name`, `body`, `source_name`, `source_signature`, `origin`, the voice
-- media block, and the two clocks `taken_in_at` and `accepted_at`. There is **no author and no
-- occurred-at**. That was survivable while the only producer was a drop folder whose files the
-- operator wrote himself: he knew who wrote it, and when it arrived was near enough to when it
-- happened.
--
-- It stops being survivable this week. S1 measured nine live IMAP mailboxes (~5,000 unread) and a
-- meeting library of 68, and both carry the two facts this table cannot hold:
--
--   * WHO. An email's `From` and a meeting's organiser. Without a column, an inbox of nine
--     mailboxes cannot answer "who is waiting on me", which is the first question anyone asks of
--     an inbox. `produced_by` exists and is NULL on every row and always has been; it is the
--     runtime's own actor field, not the human sender's, and overloading it would put two
--     different facts in one column.
--   * WHEN IT HAPPENED, as distinct from when we took it in. A message written at 09:00 and swept
--     at 14:00 is a 09:00 message. `taken_in_at` is the sweep's clock, and sorting an inbox by it
--     puts a five-hour-old email above a five-minute-old one purely because the sweep ran.
--
-- ------------------------------------------------------------------ WHY ONLY TWO COLUMNS
--
-- The vision's canonical payload is six fields: source, timestamp, author, content, attachments,
-- metadata. Four already land: source -> `source_name`, content -> `body`, and the producer's own
-- id -> `source_signature`, which is half of the existing dedup pair and is exactly an external
-- id by another name. This file adds the two that have nowhere to go AND that anyone would ever
-- sort or filter an inbox by.
--
-- ATTACHMENTS AND METADATA ARE DELIBERATELY NOT COLUMNS HERE, and the reason is a measurement
-- rather than a preference: **the `brain` schema contains zero json and zero jsonb columns across
-- all of its tables.** Checked, not assumed. `brain.event`, the most payload-shaped table in the
-- store, carries `payload_summary text` and `payload_ref text` and no blob. Introducing the first
-- JSON column in the schema is a storage-class decision for the whole store, it belongs to the
-- operator and not to an intake sprint, and it would be a strange thing to smuggle in under a
-- column named `metadata`.
--
-- So they are carried where this door already carries declared facts: **as a YAML front matter
-- block at the head of `body`**. That is not a stopgap invented here. `_declared_origin()` in
-- `engine/swarm_engine/transitions.py` already parses `origin:` out of exactly such a block, and
-- the n8n heartbeat file on disk already writes one. Nothing is dropped: every field a connector
-- sends is stored and readable. What they do not get is an index, which is correct, because
-- nobody sorts an inbox by an attachment list.
--
-- ------------------------------------------------------------------ THE TWO CHECKS, AND WHY
--
-- Both refuse the exact shape a real connector produces when it is wrong, and both are watched
-- refusing at the foot of this file rather than asserted.
--
--   1. `objective_occurred_at_sane_ck`. A date header that fails to parse becomes the unix epoch
--      in most libraries, silently. A 1970 row sorts to the top of a time-ordered inbox and STAYS
--      there, and nothing about it looks like an error -- it looks like a very old message. S1
--      measured this as a live hazard: mail in these nine mailboxes carries malformed `Date`
--      headers, and the connector has to choose something. Refusing 1970 at the table means the
--      connector is told at the door instead of quietly poisoning the sort order.
--
--   2. `objective_author_not_blank_ck`. An empty string is not an unknown author, and collapsing
--      the two would repeat migration 50's mistake in a new column. NULL means "nobody said";
--      `''` means a producer said the author is nothing, which is never true. Migration 50 chose
--      exactly this asymmetry for `origin` and refused an unrecognised value rather than folding
--      it, on the grounds that the fold is toward the unsafe direction.
--
-- ------------------------------------------------------------------ WHAT THIS FILE DOES NOT DO
--
-- **It writes nothing into either new column, and nothing reads them yet.** Unlike migration 50,
-- there is no backfill: there is nothing to backfill to. Six rows exist, four are a heartbeat
-- whose author is a workflow and whose occurred-at is its own file stamp, and inventing values
-- for the other two would be exactly the fabrication this lane's own doctrine forbids. The
-- columns land empty and fill from the next real message onward.
--
-- **The `intake` transition cannot yet write them, and that is an ESCALATION and not an
-- oversight.** `engine/swarm_engine/transitions.py:1841` is the only writer of this table and it
-- is OUTSIDE the S1 lane's ownership boundary for this sprint, while S2 is editing the same file
-- for the observation and disposition transitions. Two lanes in one file is the collision the
-- whole ownership map exists to prevent, so S1 stopped rather than editing it. The minimal change
-- is staged, unapplied, as a patch beside this file's announcement in
-- `outputs/2026-09-01-sprints/S1/`, for the admiral to sequence against S2.
--
-- Until that lands, both facts still reach the store in the body's front matter, so **no message
-- is lost by applying this file late, and none is lost by not applying it at all.** That is why
-- it is safe to hold.
--
-- ------------------------------------------------------------------ APPLY ORDER, NOT RUN BY S1
--
-- S1 DID NOT APPLY THIS. Carve-out 3: a commander proves a migration on scratch and hands over
-- the apply order. Proven on a scratch database built from the ledger, never against `brain`.
--
-- APPLY AS: the bootstrap superuser (`postgres`), against `brain`, via
-- `store/bin/apply-migration.sh`. NOT as `brain_owner`: measured 2026-08-31, all tables in the
-- `brain` schema are owned by `postgres`, and applying as `brain_owner` dies mid-file on
-- `must be owner of table work_item`. Every apply doc in this repo said otherwise and was wrong.
--
-- REVERSIBLE IN TWO STATEMENTS, and they are here so nobody has to derive them under pressure:
--
--     ALTER TABLE brain.objective DROP COLUMN IF EXISTS author;
--     ALTER TABLE brain.objective DROP COLUMN IF EXISTS occurred_at;
--
-- (Dropping the columns drops their constraints and the index with them.)

\set ON_ERROR_STOP on

DO $$
DECLARE taken text;
BEGIN
  SELECT name INTO taken FROM brain.schema_migration WHERE version = 53;
  IF taken IS NOT NULL AND taken <> '0053_an_intake_item_carries_who_and_when' THEN
    RAISE EXCEPTION 'ledger version 53 is already held by %', taken
      USING HINT = 'Renumber this file to the next free version and re-run. The admiral allocated '
                   '53 to S1, 54 to S2, 55 to S3 and 56 to S4 on 2026-09-01.';
  END IF;
END $$;

BEGIN;

-- ------------------------------------------------------------------ who wrote it

ALTER TABLE brain.objective
  ADD COLUMN IF NOT EXISTS author text;

ALTER TABLE brain.objective
  DROP CONSTRAINT IF EXISTS objective_author_not_blank_ck;
ALTER TABLE brain.objective
  ADD CONSTRAINT objective_author_not_blank_ck
    CHECK (author IS NULL OR btrim(author) <> '');

COMMENT ON COLUMN brain.objective.author IS
  'WHO PRODUCED THE CONTENT, as the source names them: an email From header, a Slack user id, a '
  'meeting organiser''s address, a person''s name. DECLARED by the connector at the door and never '
  'inferred. NULL means nobody said; an empty string is refused, because "" is not an unknown '
  'author, it is a producer asserting the author is nothing. This is NOT produced_by, which is the '
  'runtime''s own actor field and is NULL on every row that has ever existed.';

-- ------------------------------------------------------------------ when it happened

ALTER TABLE brain.objective
  ADD COLUMN IF NOT EXISTS occurred_at timestamptz;

-- 2000-01-01 rather than 1970-01-01, so that a bad parse landing on the epoch is caught along
-- with anything else that fell off the front of the calendar. Nothing legitimate in an intake
-- system predates this store, and a row that genuinely does can carry NULL and say so in its body.
ALTER TABLE brain.objective
  DROP CONSTRAINT IF EXISTS objective_occurred_at_sane_ck;
ALTER TABLE brain.objective
  ADD CONSTRAINT objective_occurred_at_sane_ck
    CHECK (occurred_at IS NULL OR occurred_at > timestamptz '2000-01-01 00:00:00+00');

COMMENT ON COLUMN brain.objective.occurred_at IS
  'WHEN THE THING HAPPENED, which is not when we took it in. An email written at 09:00 and swept '
  'at 14:00 carries 09:00 here and 14:00 in taken_in_at. Sorting an inbox by taken_in_at ranks by '
  'when the sweep ran, which is a fact about the runtime and not about the work. NULL means the '
  'source did not say or said something unparseable; the CHECK refuses anything at or before '
  '2000-01-01 because a Date header that fails to parse becomes the unix epoch in most libraries, '
  'and a 1970 row sorts to the top of a time-ordered inbox forever while looking merely old.';

-- The sort the inbox actually performs: newest first, over what is still waiting. Partial, because
-- an accepted objective is off the surface and does not need to be in the index.
CREATE INDEX IF NOT EXISTS objective_inbox_occurred_at_idx
  ON brain.objective (occurred_at DESC)
  WHERE state = 'inbox';

-- ------------------------------------------------------------------ the proof, watched running
--
-- SIX CHECKS, AND TWO OF THEM ARE THE CONSTRAINTS WATCHED REFUSING. A gate nobody has seen fail
-- is not a gate, and this repo has seven recorded instances of exactly that. The two INSERTs
-- below are expected to raise `check_violation`; if either one SUCCEEDS, this block raises,
-- because a constraint that accepts what it was written to refuse is worse than no constraint --
-- it is a promise the next reader will rely on.

DO $$
DECLARE n int := 0; probe_id bigint;
BEGIN
  -- 1. the columns exist and are nullable, which is what "lands empty and fills later" requires
  PERFORM 1 FROM information_schema.columns
    WHERE table_schema = 'brain' AND table_name = 'objective'
      AND column_name = 'author' AND is_nullable = 'YES';
  IF NOT FOUND THEN RAISE EXCEPTION 'author is missing or NOT NULL'; END IF;
  n := n + 1;

  PERFORM 1 FROM information_schema.columns
    WHERE table_schema = 'brain' AND table_name = 'objective'
      AND column_name = 'occurred_at' AND is_nullable = 'YES'
      AND data_type = 'timestamp with time zone';
  IF NOT FOUND THEN RAISE EXCEPTION 'occurred_at is missing, NOT NULL, or not timestamptz'; END IF;
  n := n + 1;

  -- 2. a legitimate row is ACCEPTED. The positive control, and it is the half that is usually
  --    skipped: without it, a constraint that refused EVERYTHING would pass every refusal test
  --    below and look like a working gate.
  INSERT INTO brain.objective (name, state, author, occurred_at)
    VALUES ('migration 53 probe, never committed', 'inbox',
            'someone@example.com', timestamptz '2026-09-01 09:14:22+00')
    RETURNING id INTO probe_id;
  n := n + 1;

  -- 3. NULL on both is still legal, because six existing rows have neither and must survive
  INSERT INTO brain.objective (name, state, author, occurred_at)
    VALUES ('migration 53 null probe, never committed', 'inbox', NULL, NULL);
  n := n + 1;

  -- 4. THE AUTHOR CHECK, WATCHED REFUSING. An empty author is not an unknown one.
  BEGIN
    INSERT INTO brain.objective (name, state, author)
      VALUES ('migration 53 blank-author probe, never committed', 'inbox', '   ');
    RAISE EXCEPTION 'objective_author_not_blank_ck accepted a blank author';
  EXCEPTION WHEN check_violation THEN
    n := n + 1;
  END;

  -- 5. THE CLOCK CHECK, WATCHED REFUSING. This is the epoch a failed date parse produces.
  BEGIN
    INSERT INTO brain.objective (name, state, occurred_at)
      VALUES ('migration 53 epoch probe, never committed', 'inbox',
              timestamptz '1970-01-01 00:00:00+00');
    RAISE EXCEPTION 'objective_occurred_at_sane_ck accepted the unix epoch';
  EXCEPTION WHEN check_violation THEN
    n := n + 1;
  END;

  -- Nothing this block wrote survives it. The probes exist to watch the constraints work, not to
  -- leave rows behind, and a migration that seeded its own test data into a live inbox would be
  -- putting fiction on the operator's board.
  DELETE FROM brain.objective WHERE name LIKE 'migration 53 %probe, never committed';

  RAISE NOTICE 'migration 53: % of 6 checks passed, including BOTH constraints watched refusing '
               'and a positive control that proves they are not refusing everything. '
               'brain.objective holds % row(s); % inbox; % with an author; % with an occurred_at.',
               n,
               (SELECT count(*) FROM brain.objective),
               (SELECT count(*) FROM brain.objective WHERE state = 'inbox'),
               (SELECT count(*) FROM brain.objective WHERE author IS NOT NULL),
               (SELECT count(*) FROM brain.objective WHERE occurred_at IS NOT NULL);
  IF n <> 6 THEN RAISE EXCEPTION 'migration 53: expected 6 checks, ran %', n; END IF;
END $$;

INSERT INTO brain.schema_migration (version, name)
  VALUES (53, '0053_an_intake_item_carries_who_and_when')
  ON CONFLICT (version) DO NOTHING;

COMMIT;
