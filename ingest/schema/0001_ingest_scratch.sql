-- D3 scratch schema: session registration and the transcript index.
--
-- This is NOT migration 1. D1 owns migration 1 and this lane never edits it. This file
-- exists so D3 can build in wave 1 without waiting for crosstalk slot 1. When slot 1 lands,
-- repointing is BRAIN_DSN + BRAIN_SCHEMA in ingest/ingest/config.py plus whatever column
-- renames D1's `session` and `transcript` require; the verbs in ingest/ingest/verbs.py are
-- the only place that names a column.
--
-- Frozen rule this schema exists to honour (D00): `transcript` stores a pointer and a hash,
-- never the blob. There is no content column here and there must never be one.

CREATE SCHEMA IF NOT EXISTS ingest;
SET search_path TO ingest, public;

-- ---------------------------------------------------------------------------
-- session
-- ---------------------------------------------------------------------------
-- session_key, not session_id, is the primary key. Measured over the live corpus: a
-- subagent transcript carries its PARENT's sessionId, so session_id is not unique across
-- files. A subagent's key is '<parent_session_id>:<agent_id>'.
CREATE TABLE IF NOT EXISTS session (
    session_key            text PRIMARY KEY,
    session_id             text,
    agent_id               text,
    kind                   text NOT NULL CHECK (kind IN ('main','subagent')),

    harness                text NOT NULL DEFAULT 'claude-code',
    harness_version        text,
    entrypoint             text,
    model                  text,
    model_source           text,
    permission_mode        text,
    git_branch             text,
    is_sidechain           boolean,

    -- Where it ran. project_root_dir is the path-encoded directory name kept verbatim,
    -- because the decode is lossy and the raw name is the only recoverable input.
    project_root_dir       text,
    workdir                text,
    workdir_source         text CHECK (workdir_source IN ('record-cwd','decoded-dirname','hook-arg')),
    workdir_host_id        text,   -- which host `workdir` is absolute on

    -- Lifecycle. Three distinct facts, deliberately not collapsed into one:
    --   started_at        first record timestamp, or the hook's start time
    --   last_activity_at  last record timestamp in the transcript. A LOWER BOUND on the end.
    --   ended_at          an end was actually OBSERVED, by `session end` or a hook
    -- A backfilled session has last_activity_at and a NULL ended_at, because nothing
    -- witnessed it stop. Collapsing the two would report a clean end time for 1,123 sessions
    -- nobody ever saw finish, which is the confidently-wrong shape this program exists to
    -- avoid. state is 'unknown' for those, never 'open' and never 'ended'.
    started_at             timestamptz,
    last_activity_at       timestamptz,
    ended_at               timestamptz,
    time_source            text,
    end_reason             text,
    state                  text NOT NULL DEFAULT 'open' CHECK (state IN ('open','ended','unknown')),
    -- Which filesystem class `workdir` lives on. /mnt/c is drvfs and is meaningful only on
    -- this host; a later VPS move breaks it.
    workdir_path_class     text CHECK (workdir_path_class IN ('wsl-ext4','wsl-drvfs')),

    parent_session_key     text,
    parent_source          text CHECK (parent_source IN ('path+field','path','field','hook-arg')),
    workflow_id            text,

    turns_user             integer,
    turns_assistant        integer,
    turns_source           text,
    tokens_input           bigint,
    tokens_output          bigint,
    tokens_cache_read      bigint,
    tokens_cache_creation  bigint,
    cost_usd               numeric(12,6),   -- NULL = not recorded. Never read as zero.
    cost_source            text,

    stated_goal            text,
    stated_goal_source     text,            -- 'first-user-prompt' | 'ai-title' | 'hook-arg' | NULL
    stated_goal_tier       text,            -- see ingest/goal.py

    -- D00 lineage columns, carried now so a later migration does not have to touch every row.
    produced_by            text,
    actor_type             text CHECK (actor_type IN ('human','ai','hybrid')),

    registration_source    text NOT NULL CHECK (registration_source IN ('hook','backfill','manual')),
    registered_at          timestamptz NOT NULL DEFAULT now(),
    updated_at             timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS session_started_at_idx  ON session (started_at);
CREATE INDEX IF NOT EXISTS session_parent_idx      ON session (parent_session_key);
CREATE INDEX IF NOT EXISTS session_workdir_idx     ON session (workdir);
CREATE INDEX IF NOT EXISTS session_state_idx       ON session (state);

-- ---------------------------------------------------------------------------
-- An observed end is a ratchet (task 0136)
-- ---------------------------------------------------------------------------
-- `ended_at IS NULL` is read everywhere as "this session is still open", and a stale-session
-- sweeper built on that predicate would reopen sessions that were correctly closed. So an
-- end, once OBSERVED, must never be able to go back to NULL, and `state='ended'` must never
-- be able to go back to 'open' or 'unknown'.
--
-- verbs.py already refuses both: `session register` does not carry ended_at in its ON CONFLICT
-- list and `session end` only COALESCEs onto it. This trigger is the same rule stated where it
-- also binds a writer that is not verbs.py: a hand-run UPDATE, a future importer, another
-- lane's tool. It restores rather than aborts, because the hook that reaches this table must
-- never take an operator's session down with it; the WARNING lands in the postgres log.
--
-- HONEST LIMIT, because a guard that oversells itself is worse than none: this survives no
-- DDL. `DROP SCHEMA ingest CASCADE` takes the trigger, the function and every row with it,
-- which is precisely how the 2026-08-16T12:08Z hook-proof row was actually lost. Rebuilding
-- this registry from backfill alone discards every hook-observed fact in it, because a
-- transcript records no end.
CREATE OR REPLACE FUNCTION session_observed_facts_monotonic() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.ended_at IS NOT NULL AND NEW.ended_at IS NULL THEN
        RAISE WARNING 'ingest.session %: refused to clear an observed ended_at (%)',
                      OLD.session_key, OLD.ended_at;
        NEW.ended_at := OLD.ended_at;
    END IF;
    IF OLD.state = 'ended' AND NEW.state IS DISTINCT FROM 'ended' THEN
        RAISE WARNING 'ingest.session %: refused to reopen an ended session (state -> %)',
                      OLD.session_key, NEW.state;
        NEW.state := 'ended';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS session_observed_facts_monotonic ON session;
CREATE TRIGGER session_observed_facts_monotonic
    BEFORE UPDATE ON session
    FOR EACH ROW EXECUTE FUNCTION session_observed_facts_monotonic();

-- ---------------------------------------------------------------------------
-- transcript
-- ---------------------------------------------------------------------------
-- A pointer and a hash. No blob column, ever.
--
-- host_id is not decoration. `/mnt/c/Users/...` is meaningful only on this WSL instance;
-- v1 is local and a later VPS move would otherwise break every pointer silently. A row
-- without host_id is a pointer to nowhere.
CREATE TABLE IF NOT EXISTS transcript (
    transcript_id   bigserial PRIMARY KEY,
    session_key     text REFERENCES session(session_key) ON DELETE CASCADE,
    kind            text NOT NULL,   -- 'main' | 'subagent' | 'workflow-journal'

    host_id         text NOT NULL,
    path            text NOT NULL,   -- absolute ON host_id, never on any other host
    path_class      text NOT NULL CHECK (path_class IN ('wsl-ext4','wsl-drvfs')),
    windows_path    text,            -- the drvfs path's Windows spelling, when it has one

    bytes           bigint NOT NULL,
    sha256          char(64) NOT NULL,
    line_count      integer NOT NULL,
    bad_json_lines  integer NOT NULL DEFAULT 0,
    first_ts        timestamptz,
    last_ts         timestamptz,

    indexed_at      timestamptz NOT NULL DEFAULT now(),
    verified_at     timestamptz,
    -- Widened to five values by 0005_verify_appended.sql (task 0330): `appended`, the verdict
    -- for a file whose stored hash still holds as a PREFIX of what is on disk. This line is
    -- left as it shipped -- the migration after it is what is in force.
    verify_result   text CHECK (verify_result IN ('match','mismatch','missing','unreadable')),
    verify_sha256   char(64),        -- what the re-hash produced on a mismatch or an append

    UNIQUE (host_id, path)
);

CREATE INDEX IF NOT EXISTS transcript_session_idx ON transcript (session_key);
CREATE INDEX IF NOT EXISTS transcript_sha_idx     ON transcript (sha256);

-- ---------------------------------------------------------------------------
-- event_outbox
-- ---------------------------------------------------------------------------
-- NOT the event fabric. D5 owns `event` and the `event emit` verb, and D00 forbids a
-- producer from INSERTing into `event`. Until D5's verb exists on this box, session start
-- and end land here in exactly the shape D5 consumes, and `ingest events drain` replays
-- them through `event emit` once it does. Nothing downstream should subscribe to this table.
CREATE TABLE IF NOT EXISTS event_outbox (
    seq           bigserial PRIMARY KEY,
    event_type    text NOT NULL,
    occurred_at   timestamptz NOT NULL,
    subject_type  text NOT NULL,
    subject_id    text NOT NULL,
    produced_by   text,
    payload       jsonb NOT NULL,
    emit_status   text NOT NULL DEFAULT 'pending' CHECK (emit_status IN ('pending','emitted','failed')),
    emitted_at    timestamptz,
    emit_detail   text
);

CREATE INDEX IF NOT EXISTS event_outbox_pending_idx ON event_outbox (emit_status, seq);
