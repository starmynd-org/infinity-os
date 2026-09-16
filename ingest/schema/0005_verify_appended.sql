-- D3-owned, scratch profile only: `transcript verify` gains a fifth verdict, `appended`.
--
-- Task 0330. The 2026-08-16T12:20:40Z backfill sweep hashed transcripts whose sessions were
-- still writing to them. A hash taken against an open append-only log is stale the moment it
-- is taken, and it was recorded as if it were authoritative. Measured on this host
-- 2026-08-17: ten indexed rows re-hashed differently and ALL TEN were pure growth -- hashing
-- the first `bytes` of each file on disk reproduced the stored sha256 exactly, and in all ten
-- the byte at the cut was a newline. So the next scheduled verify was going to report ten
-- files as `mismatch`, which is the one verdict that means somebody rewrote recorded history.
--
-- `appended` is decided by evidence, not by timing: the stored hash is re-tested as a PREFIX
-- of the file on disk. If it holds, every byte this lane recorded is still there unchanged and
-- the file only got longer. If it does not, recorded bytes moved and the verdict stays
-- `mismatch`. See `transcript_verify` in ingest/verbs.py for why the two timing-based
-- alternatives (skip sessions with no `ended_at`; flag hashes taken against an open file) were
-- measured and rejected.
--
-- WHY THIS IS A WIDENING AND NOT A NEW COLUMN. `verify_result` is already the column that
-- holds the verdict verbatim on this profile; it just had four legal values and now has five.
-- The brain profile needs no migration at all: `verified_ok` is D1's nullable boolean and
-- `appended` maps onto the NULL that task 0324 established -- checked, and neither a pass nor
-- a fail. D3 does not widen D1's schema from this lane, and did not have to.
--
-- Apply to every database the verbs write to:
--     psql -d d3_scratch -f ingest/schema/0005_verify_appended.sql
-- Running it against `brain` is harmless: the guard below finds no such constraint there and
-- the statement is skipped, because D1's `transcript` has no `verify_result` column.
-- `ingest/bin/ingest init-schema` applies every schema/*.sql in order, so a fresh database
-- gets this one without a second step.

-- The two conditions are NESTED and not ANDed on purpose: SQL does not promise to short-circuit
-- AND, and `'ingest.transcript'::regclass` RAISES on a database that has no such table rather
-- than returning NULL. Written as one condition this file failed against `brain`, which is
-- precisely the database the guard exists to skip.
DO $$
BEGIN
    IF to_regclass('ingest.transcript') IS NOT NULL THEN
        IF EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conrelid = 'ingest.transcript'::regclass
                     AND conname = 'transcript_verify_result_check') THEN
            ALTER TABLE ingest.transcript DROP CONSTRAINT transcript_verify_result_check;
            ALTER TABLE ingest.transcript ADD CONSTRAINT transcript_verify_result_check
                CHECK (verify_result IN ('match','appended','mismatch','missing','unreadable'));
        END IF;
    END IF;
END $$;
