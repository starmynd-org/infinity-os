-- D3-owned, profile-independent: transcripts whose FILE is gone, and whether that is normal.
--
-- Task 0324, handed on from 0318. `queries.filesystem_report` (task 0323) already counts the
-- direction -- 61 pointers on 2026-08-17 whose file is not on disk -- but a count is not a
-- meaning. 61 reads identically whether a retention sweep aged the files out on schedule or
-- somebody deleted a transcript of a session that ended this morning. One of those is nothing
-- and the other is an incident, and a report that cannot tell them apart makes the operator
-- open all 61 or none of them.
--
-- WHAT WAS MEASURED, 2026-08-17, before this table existed. All 61 dead pointers belong to
-- sessions started 2026-07-15 (24), 07-16 (24) and 07-17 (12), and to no later day. The oldest
-- file still under ~/.claude/projects is dated 2026-07-19. So the corpus has a hard date cut:
-- everything before 07-19 is gone and nothing from 07-19 on is missing. That is a retention
-- sweep on a ~30-day horizon, not loss. `~/.claude/settings.json` sets no `cleanupPeriodDays`,
-- so the harness default is what is deleting them, and the cohort ages out again every day.
--
-- WHY A ROW AND NOT A DELETE. The brief this table answers says it in one line: the pointer,
-- the hash and the byte count are the only surviving record that those transcripts ever
-- existed. Deleting the `transcript` row to make the gap go away destroys that evidence and
-- silently shrinks every denominator in the coverage report. The `transcript` row stays; THIS
-- table says what happened to the file it points at.
--
-- WHY NOT `verified_ok = false`. D1's `transcript.verified_ok` is a boolean, so `mismatch`
-- (the file changed under a stored hash -- corruption, worth waking up for) and `missing` (the
-- file reached its designed end of life) collapse into the same `false`. `transcript verify`
-- now writes `verified_at = now(), verified_ok = NULL` for a gone file: checked, and not a
-- pass or a fail, because there was nothing to hash. The meaning lands here instead.
--
--     SELECT classification, count(*) FROM ingest.transcript_absence
--      WHERE returned_at IS NULL GROUP BY 1;
--
-- Lives in D3's own `ingest` schema on BOTH profiles, exactly like `event_outbox` and
-- `transcript_orphan`: on the brain profile the search_path is `brain, ingest, public`, so
-- `session` and `transcript` resolve to D1's migration 1 while this resolves to D3's
-- namespace. D3 does not widen D1's schema from this lane.
--
-- Apply to every database the verbs write to:
--     psql -d brain      -f ingest/schema/0004_transcript_absence.sql
--     psql -d d3_scratch -f ingest/schema/0004_transcript_absence.sql
-- `ingest/bin/ingest init-schema` applies every schema/*.sql in order.

CREATE SCHEMA IF NOT EXISTS ingest;
SET search_path TO ingest, public;

CREATE TABLE IF NOT EXISTS transcript_absence (
    id              bigserial PRIMARY KEY,

    pointer         text NOT NULL,
    pointer_host    text NOT NULL,

    -- The session the transcript was keyed to, kept verbatim and deliberately NOT a foreign
    -- key: this row must outlive anything, and a keyless pointer (a workflow journal) is a
    -- legitimate NULL rather than a gap. See `verbs.transcript_index`.
    session_key     text,

    -- The evidence that survives the file. Copied off the `transcript` row at the moment the
    -- absence was observed so that this table is readable on its own, without a join to a row
    -- somebody might later restate.
    sha256          text NOT NULL,
    bytes           bigint,

    -- Enumerated, not free text, and the three values are the three different things the
    -- operator does:
    --   aged-out     the session's last known activity is older than `retention_days`. The
    --                file reached its designed end of life. Nobody acts on this.
    --   unexplained  the session is NEWER than the horizon and the file is gone anyway. This
    --                is the one worth reading. It is the incident shape.
    --   unknown-age  no timestamp survives for this pointer, so it cannot be placed on either
    --                side of the horizon. Reported as its own thing rather than guessed into
    --                one of the other two, because guessing here is how a real deletion gets
    --                filed as routine.
    classification  text NOT NULL CHECK (classification IN
                        ('aged-out', 'unexplained', 'unknown-age')),

    -- The horizon in force when the classification was made, and the timestamp it was compared
    -- against. Stored rather than recomputed: raising the retention horizon later must not
    -- silently restate what last month's rows meant.
    retention_days  integer NOT NULL,
    age_basis       text NOT NULL CHECK (age_basis IN
                        ('session-ended-at', 'session-last-activity-at',
                         'session-started-at', 'none')),
    aged_from       timestamptz,

    first_observed_absent_at timestamptz NOT NULL DEFAULT now(),
    last_observed_absent_at  timestamptz NOT NULL DEFAULT now(),
    times_observed           integer NOT NULL DEFAULT 1,

    -- Set when a later verify FINDS the file again -- a restore from backup, or a pointer that
    -- was never dead and a mount that was. Without this the count could only grow, and a
    -- monotonically growing loss count is itself a number that misleads. Same rule as
    -- `transcript_orphan.resolved_at`.
    returned_at     timestamptz,

    UNIQUE (pointer_host, pointer)
);

CREATE INDEX IF NOT EXISTS transcript_absence_open_idx
    ON transcript_absence (classification) WHERE returned_at IS NULL;
CREATE INDEX IF NOT EXISTS transcript_absence_key_idx
    ON transcript_absence (session_key);
