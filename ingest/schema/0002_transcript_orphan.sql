-- D3-owned, profile-independent: the count of transcripts that could not be keyed to a session.
--
-- Task 0228. The installed SessionEnd hook indexed a transcript whose session had never been
-- registered and hit the FK on `transcript` (`transcript_session_key_fkey` on the scratch
-- profile, `transcript_session_id_fkey` on the brain profile). The hook swallows every failure
-- by design, so the transcript was neither indexed nor counted: it was silent, which is the
-- one outcome this lane forbids. "A registry with unknown gaps is worse than no registry at
-- all, because it invites false conclusions from counts."
--
-- The transition now writes the transcript with a NULL session key -- the pointer and the hash
-- are true facts about a file that exists, and losing them because the session row is missing
-- would discard evidence -- and records the key it could not honour HERE. So the gap is a row
-- someone can count, with the key preserved rather than dropped:
--
--     SELECT count(*) FROM ingest.transcript_orphan WHERE resolved_at IS NULL;
--
-- Lives in D3's own `ingest` schema on BOTH profiles, exactly like `event_outbox`: on the
-- brain profile the search_path is `brain, ingest, public`, so `session` and `transcript`
-- resolve to D1's migration 1 while this table resolves to D3's namespace. D3 does not widen
-- D1's schema from this lane.
--
-- Apply to every database the verbs write to:
--     psql -d brain      -f ingest/schema/0002_transcript_orphan.sql
--     psql -d d3_scratch -f ingest/schema/0002_transcript_orphan.sql
-- `ingest/bin/ingest init-schema` applies every schema/*.sql in order, so a fresh database
-- gets this one without a second step.

CREATE SCHEMA IF NOT EXISTS ingest;
SET search_path TO ingest, public;

CREATE TABLE IF NOT EXISTS transcript_orphan (
    id             bigserial PRIMARY KEY,

    -- The key the caller claimed, kept verbatim. It is the whole point of the row: dropping
    -- it would leave "some transcript somewhere had no session", which nobody can act on.
    -- Deliberately NOT a foreign key -- the row exists precisely because the referent does not.
    session_key    text NOT NULL,

    pointer        text NOT NULL,
    pointer_host   text NOT NULL,

    -- Enumerated, not free text. One value today because one cause has been measured; a new
    -- cause is a migration, so the list stays an honest account of what has actually happened.
    reason         text NOT NULL CHECK (reason IN ('session-not-registered')),

    first_seen_at  timestamptz NOT NULL DEFAULT now(),
    last_seen_at   timestamptz NOT NULL DEFAULT now(),
    times_seen     integer NOT NULL DEFAULT 1,

    -- Set when a later index call DOES write a session key for this pointer, which is what a
    -- backfill sweep does once the missing session is reconstructed. Without this the count
    -- could only grow, and a monotonically growing gap count is itself a number that misleads.
    resolved_at    timestamptz,

    UNIQUE (pointer_host, pointer)
);

CREATE INDEX IF NOT EXISTS transcript_orphan_open_idx
    ON transcript_orphan (last_seen_at) WHERE resolved_at IS NULL;
CREATE INDEX IF NOT EXISTS transcript_orphan_key_idx
    ON transcript_orphan (session_key);
