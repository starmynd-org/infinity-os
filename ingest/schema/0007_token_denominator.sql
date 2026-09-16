-- D3-owned, BOTH profiles: `session` gains the denominator its four token sums were taken over.
--
-- Task 0441. `ingest/transcript.scan()` summed `message.usage` once per RECORD. The harness
-- writes one assistant MESSAGE as several records and repeats the COMPLETED usage block on every
-- one of them, so every `tokens_*` value this store holds from before this migration is inflated.
-- Re-measured 2026-08-29 over all 1496 files in ~/.claude/projects, population and scanned equal,
-- gap 0:
--
--     assistant records carrying usage   174,060
--     distinct message.id                 83,779      record/message ratio 2.0776
--     message.id present                 174,060 of 174,060   (the key is total; no fallback fired)
--     priced via budget/price_card.py    $27,749.37 naive  vs  $12,508.47 deduped  =  2.2184x
--
-- WHY THREE COLUMNS AND NOT ZERO. Fixing `scan()` alone makes new rows right and leaves old rows
-- wrong and INDISTINGUISHABLE from right ones, which is the worse of the two states: a stored
-- number nobody can date is a number that gets believed. `tokens_source` is NULL on every row
-- written before this and 'message-usage-deduped' on every row written after, so the inflated
-- rows are identifiable by query rather than by memory of when the fix landed.
--
-- `tokens_messages` is the denominator the sums actually cover. `tokens_usage_records` is the
-- denominator the old code used. Carrying both means a reader can see the ratio for a single
-- session instead of taking the corpus figure above on faith, and a row where they are equal is
-- a row where dedup changed nothing -- which is a fact about that session, not a missing check.
--
-- NO PRICE IS ADDED HERE. `cost_usd` stays NULL and `budget/price_card.py` remains the single
-- pricing authority. This migration makes the token basis correct; it does not spend it.
--
-- BACKFILL. Existing rows are NOT rewritten by this migration, because the correct value can only
-- come from re-reading the transcript and some of those files have since aged out of the harness's
-- retention sweep. Re-running `ingest backfill` over the files still on disk restores them and
-- stamps `tokens_source`; the rows it cannot reach keep a NULL `tokens_source` and are therefore
-- readable as "summed per record, inflated by roughly 2.2x, basis no longer on disk".

ALTER TABLE session ADD COLUMN IF NOT EXISTS tokens_messages       integer;
ALTER TABLE session ADD COLUMN IF NOT EXISTS tokens_usage_records  integer;
ALTER TABLE session ADD COLUMN IF NOT EXISTS tokens_source         text
    CHECK (tokens_source IS NULL OR tokens_source = 'message-usage-deduped');
