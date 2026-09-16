-- D3-owned, BOTH profiles: `transcript_absence.age_basis` gains the three `parent-session-*`
-- values, so a keyless pointer can be dated by the session that owns its directory.
--
-- Task 0359, handed on from 0335. Task 0324 derives a file's age from its SESSION row. A
-- workflow journal is indexed with a NULL session key on purpose -- it is not a session and
-- claims no key, see `verbs.transcript_index` -- so there was no row to read and the classifier
-- returned `unknown-age`, correctly and permanently. Measured on the live store
-- 2026-08-17T23:4x and again 2026-08-18T00:20:
--
--     checked=1346 match=1283 appended=2 mismatch=0 missing=61 unreadable=0
--     missing_by_classification: aged-out=60 unexplained=0 unknown-age=1
--
-- That single 1 exits `transcript verify` 1, which holds `brain-transcript-verify.service`
-- `failed` -- every day, forever, for a pointer nothing would ever be able to date. 0324's own
-- comment says what that costs: "a scheduled verify that went red every day for a sweep running
-- on schedule is a verb whose exit code stops being read."
--
-- WHY A WIDENING AND NOT A NEW COLUMN. `age_basis` is already the column that says where the
-- timestamp came from; it had four legal values and now has seven. The three new ones are the
-- existing ladder read off the CONTAINING session instead of the pointer's own, and they are
-- spelled differently on purpose: a row aged against its parent must never be readable as a row
-- that had a timestamp of its own. `classification` is untouched -- `aged-out`, `unexplained`
-- and `unknown-age` still mean exactly what 0004 says they mean, and `unknown-age` is still what
-- a pointer gets when its path names no session or that session has no timestamp either.
--
-- WHAT MAKES THE PARENT'S TIMESTAMP ADMISSIBLE. The harness's retention sweep takes a session's
-- directory tree, not a file. Measured the same day: the one gone journal is one of EIGHT
-- pointers that left `.../c83ff56d-.../subagents/workflows/wf_435c4513-de5/` in the same sweep,
-- and its seven siblings all classify `aged-out` from timestamps between 2026-07-17T18:45:48Z
-- and 19:12:34Z. The age was not missing from the store, it was one path component away.
--
-- NO BACKFILL IS NEEDED OR WANTED. `verbs._ABSENCE_RECORD` upserts `classification`,
-- `age_basis` and `aged_from` on every observation, so the next `transcript verify` pass
-- restates the open rows itself, from measurement, in the same transaction that observes them.
-- `first_observed_absent_at` is still never touched, so the date of death survives the
-- reclassification. Nothing here writes a row.
--
-- Apply to every database the verbs write to:
--     psql -d brain      -f ingest/schema/0006_absence_parent_age_basis.sql
--     psql -d d3_scratch -f ingest/schema/0006_absence_parent_age_basis.sql
-- `ingest/bin/ingest init-schema` applies every schema/*.sql in order, so a fresh database gets
-- 0004's narrow CHECK and then this one without a second step.

-- Guarded and nested for the same reason 0005 is: `'ingest.transcript_absence'::regclass` RAISES
-- on a database that does not have the table rather than returning NULL, so the existence test
-- cannot share an AND with the constraint lookup. Idempotent -- re-running drops and re-adds the
-- same constraint.
DO $$
BEGIN
    IF to_regclass('ingest.transcript_absence') IS NOT NULL THEN
        IF EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conrelid = 'ingest.transcript_absence'::regclass
                     AND conname = 'transcript_absence_age_basis_check') THEN
            ALTER TABLE ingest.transcript_absence
                DROP CONSTRAINT transcript_absence_age_basis_check;
        END IF;
        ALTER TABLE ingest.transcript_absence
            ADD CONSTRAINT transcript_absence_age_basis_check
            CHECK (age_basis IN (
                -- the pointer's own session, unchanged since 0004
                'session-ended-at', 'session-last-activity-at', 'session-started-at',
                -- the session whose DIRECTORY holds the pointer, for a pointer that has no
                -- session of its own (task 0359). Same ladder, weaker claim, said out loud.
                'parent-session-ended-at', 'parent-session-last-activity-at',
                'parent-session-started-at',
                -- nothing to age against at all. Still reachable, still exits the verb 1.
                'none'));
    END IF;
END $$;
