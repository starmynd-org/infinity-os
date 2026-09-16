-- Task 0302: `event_outbox.emit_status` had a `failed` value in the CHECK and no writer.
--
-- `events.drain` only ever left a refused row `pending`, so an envelope that can NEVER emit
-- was indistinguishable from one waiting its turn, and the drain is FIFO. That is the
-- starvation shape task 0287 documented at `AFTER_COMMIT_DRAIN_LIMIT` and could not close:
--
--     "the drain is FIFO, so N envelopes that can never emit sit at the head and starve
--      everything behind them. There is no attempt counter in `event_outbox` to skip them
--      with."
--
-- This file adds the counter, and `events.drain` now spends it.
--
-- ## Why a counter and not a classifier
--
-- The obvious alternative was to read the refusal and decide "this one is permanent" --
-- specifically, to treat `ForeignKeyViolation on event_session_id_fkey` as terminal on sight.
-- That would be wrong, and the seven rows that motivated this task are the counterexample:
-- they name five real sessions that registered into `d3_scratch` before the BRAIN_PROFILE
-- flip and never got a `brain.session` row. The FK refusal is permanent only for as long as
-- those sessions are missing. Reconcile them and the very same envelopes emit correctly, with
-- their original `occurred_at`. A classifier would have written that judgement into the store
-- as fact; a counter records only what actually happened -- "this was attempted N times and
-- refused N times" -- which is true regardless of why.
--
-- ## `failed` is terminal for the DRAIN, not for the envelope
--
-- Nothing here deletes a row or drops a payload. A `failed` row keeps its full envelope, its
-- attempt count and the last refusal verbatim; it is simply out of the FIFO head. `ingest
-- events requeue` puts it back. That is what makes marking an envelope `failed` a
-- non-destructive act rather than a decision to forget it, and it is the reason this migration
-- could land without first settling whether those five sessions get reconciled.
--
-- Apply to every database the verbs write to:
--     psql -d brain      -f ingest/schema/0003_event_outbox_terminal.sql
--     psql -d d3_scratch -f ingest/schema/0003_event_outbox_terminal.sql
-- `ingest/bin/ingest init-schema` applies every schema/*.sql in order, so a fresh database
-- gets this one without a second step.

CREATE SCHEMA IF NOT EXISTS ingest;
SET search_path TO ingest, public;

-- How many times the drain has HANDED THIS ENVELOPE TO THE VERB. Counts successes too: a row
-- that emitted on its third try records 3, because "it took three attempts" is a fact worth
-- keeping and zeroing it on success would hide a flapping emit surface.
ALTER TABLE event_outbox ADD COLUMN IF NOT EXISTS attempts integer NOT NULL DEFAULT 0;

-- Two timestamps, not one, and neither is `emitted_at`. `first_attempted_at` is how long an
-- envelope has been in trouble; `last_attempted_at` is whether anything is still trying. A
-- single column would answer neither question on its own.
ALTER TABLE event_outbox ADD COLUMN IF NOT EXISTS first_attempted_at timestamptz;
ALTER TABLE event_outbox ADD COLUMN IF NOT EXISTS last_attempted_at  timestamptz;

-- When the drain gave up. NULL on every non-`failed` row, and cleared again by a requeue, so
-- `failed_at IS NOT NULL` and `emit_status = 'failed'` can never disagree.
ALTER TABLE event_outbox ADD COLUMN IF NOT EXISTS failed_at timestamptz;

-- How many times this row has been requeued after a give-up. A row that keeps coming back is
-- a different problem from one that failed once, and without this the requeue erases its own
-- evidence by resetting `attempts`.
ALTER TABLE event_outbox ADD COLUMN IF NOT EXISTS requeues integer NOT NULL DEFAULT 0;

-- The pending index now leads with `attempts`, because the drain's ORDER BY changed with it.
-- FRESH ENVELOPES OUTRANK RETRIES; FIFO IS PRESERVED WITHIN AN ATTEMPT COUNT. This is the
-- half of the starvation fix that works immediately: without it, a row at the head that is
-- going to fail five times still consumes a drain slot on each of the next five sessions
-- before it terminates. With it, a row that has already been refused once yields to every
-- envelope that has not been tried, so a backlog behind an undeliverable row clears on the
-- next drain rather than five sessions later.
DROP INDEX IF EXISTS event_outbox_pending_idx;
CREATE INDEX IF NOT EXISTS event_outbox_pending_idx ON event_outbox (emit_status, attempts, seq);

-- The two columns cannot disagree. Stated at the store rather than in the drain, because the
-- drain is not the only thing that has ever written this table by hand.
ALTER TABLE event_outbox DROP CONSTRAINT IF EXISTS event_outbox_failed_at_agrees;
ALTER TABLE event_outbox ADD CONSTRAINT event_outbox_failed_at_agrees
    CHECK ((emit_status = 'failed') = (failed_at IS NOT NULL));
