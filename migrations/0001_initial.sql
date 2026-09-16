-- migration 1: the store
--
-- Derived from the live system on 2026-08-16, not transcribed from prose. The derivation and
-- its method are in migrations/COVERAGE.md; read that first if a table here looks arbitrary.
--
-- Nineteen tables: the sixteen frozen in D00, plus `touch` (D00 requires it by name), plus
-- `event_rollup` (the operator-accepted fabric design's owned-runtime-state list, and the
-- reason the retention contract has a 400-day tier), plus `runtime_flag` (found by enumeration:
-- the fleet-wide PAUSE switch has six verbs writing to it and no table in the frozen sixteen).
--
-- Three properties are load-bearing and none is the obvious default:
--
--   1. NO TEXT COLUMN TRUNCATES. Every free-text field is `text`. The two write paths that cut
--      today (result at 2000, thread text at 3000) destroy the string, and nothing downstream
--      can un-cut them. Rendering is where things get shortened.
--   2. A MISSING SIGNAL IS CONSERVATIVE, NEVER THE MIDDLE. Validation is strict on write and
--      tolerant on read: a garbage value is refused at insert, an unset value reads as the
--      conservative end. Defaulting to medium would treat every unassessed task as average.
--   3. `question` AND `work_item` SHARE ONE SEQUENCE. q0004 and task 0004 never coexist.
--
-- Idempotent enough to re-run on a scratch database. Not idempotent against a populated one:
-- migration 2 is a migration, not a re-run of this.

BEGIN;

CREATE SCHEMA IF NOT EXISTS brain;
SET search_path TO brain, public;

-- ---------------------------------------------------------------- shared id allocation
--
-- One sequence, drawn by work_item and question alike. This is what makes `q0004` and task
-- `0004` mutually exclusive rather than merely unlikely. Do not give questions their own serial.

CREATE SEQUENCE IF NOT EXISTS brain.item_id_seq AS bigint START 1;

CREATE OR REPLACE FUNCTION brain.next_item_id() RETURNS text
  LANGUAGE sql VOLATILE AS $$ SELECT lpad(nextval('brain.item_id_seq')::text, 4, '0') $$;

CREATE OR REPLACE FUNCTION brain.next_question_id() RETURNS text
  LANGUAGE sql VOLATILE AS $$ SELECT 'q' || lpad(nextval('brain.item_id_seq')::text, 4, '0') $$;

-- ---------------------------------------------------------------- the signal vocabulary
--
-- The operator's canon writes signals in a richer per-signal vocabulary. Accept it and fold it
-- onto low/medium/high, so a brief written against the canon parses with no translation step and
-- no second vocabulary to keep in sync. Strict on write is the CHECK; tolerant on read is
-- signal_level(), which never raises and never returns the middle for an unknown.

CREATE OR REPLACE FUNCTION brain.signal_level(field text, raw text) RETURNS text
  LANGUAGE sql IMMUTABLE AS $$
  SELECT COALESCE(
    CASE lower(btrim(COALESCE(raw, '')))
      WHEN 'low' THEN 'low' WHEN 'medium' THEN 'medium' WHEN 'high' THEN 'high'
      ELSE NULL END,
    CASE field || ':' || lower(btrim(COALESCE(raw, '')))
      WHEN 'stakes:none'             THEN 'low'
      WHEN 'stakes:critical'         THEN 'high'
      WHEN 'reversibility:reversible' THEN 'high'
      WHEN 'reversibility:costly'    THEN 'medium'
      WHEN 'reversibility:irreversible' THEN 'low'
      WHEN 'urgency:none'            THEN 'low'
      WHEN 'urgency:soon'            THEN 'medium'
      WHEN 'urgency:deadline'        THEN 'high'
      WHEN 'urgency:decaying'        THEN 'high'
      WHEN 'effort:small'            THEN 'low'
      WHEN 'effort:large'            THEN 'high'
      ELSE NULL END,
    -- Unset, or unreadable, means conservative. Stakes and reversibility are set by the
    -- operator's canon outright; the rest take the value that cannot lift a task up the queue
    -- on its own, so an unassessed task never overtakes an assessed one by saying nothing.
    CASE field
      WHEN 'stakes' THEN 'high'
      WHEN 'reversibility' THEN 'low'   -- low reversibility IS costly-or-irreversible here
      WHEN 'effort' THEN 'high'
      ELSE 'low' END)
$$;

COMMENT ON FUNCTION brain.signal_level(text, text) IS
  'Tolerant on read. Folds the canon vocabulary onto low/medium/high and returns the '
  'conservative value for anything unset or unreadable. Never raises, never returns medium '
  'for an unknown.';

-- Strict on write. NULL and '' are permitted (they mean "never assessed", which the file bus
-- also permits and which reads as conservative); a value outside both vocabularies is refused.
CREATE OR REPLACE FUNCTION brain.signal_ok(field text, raw text) RETURNS boolean
  LANGUAGE sql IMMUTABLE AS $$
  SELECT raw IS NULL OR btrim(raw) = ''
      OR lower(btrim(raw)) IN ('low','medium','high')
      OR (field = 'stakes'         AND lower(btrim(raw)) IN ('none','critical'))
      OR (field = 'reversibility'  AND lower(btrim(raw)) IN ('reversible','costly','irreversible'))
      OR (field = 'urgency'        AND lower(btrim(raw)) IN ('none','soon','deadline','decaying'))
      OR (field = 'effort'         AND lower(btrim(raw)) IN ('small','large'))
$$;

-- `actor_type` wherever work completes. effectiveness-points requires it on every award and it
-- is not derivable from a producing-entity id.
CREATE DOMAIN brain.actor_type AS text
  CHECK (VALUE IS NULL OR VALUE IN ('human', 'ai', 'hybrid'));

-- =================================================================== DISPATCH (8 frozen)

-- ---------------------------------------------------------------- work_item
--
-- All twenty-nine columns read off a live task, plus the three D00 adds that v1 does not yet
-- use and that would each be a migration across every table later.

CREATE TABLE IF NOT EXISTS brain.work_item (
  id                     text PRIMARY KEY DEFAULT brain.next_item_id(),
  title                  text NOT NULL,
  lane                   text NOT NULL,
  host                   text NOT NULL DEFAULT '',
  state                  text NOT NULL DEFAULT 'inbox'
                           CHECK (state IN ('inbox','active','done','blocked','cancelled')),
  priority               integer NOT NULL DEFAULT 0,
  created                timestamptz NOT NULL DEFAULT now(),
  posted_by              text NOT NULL DEFAULT '',
  claimed_by             text NOT NULL DEFAULT '',
  claimed_at             timestamptz,
  finished_at            timestamptz,
  workdir                text NOT NULL DEFAULT '',
  depends_on             text NOT NULL DEFAULT '',
  parent                 text REFERENCES brain.work_item(id) DEFERRABLE INITIALLY DEFERRED,

  -- the seven levelled signals. Nullable on purpose: unset is a real state and it means
  -- conservative, never zero and never medium.
  stakes                 text CHECK (brain.signal_ok('stakes', stakes)),
  reversibility          text CHECK (brain.signal_ok('reversibility', reversibility)),
  urgency                text CHECK (brain.signal_ok('urgency', urgency)),
  dependency_unblocking  text CHECK (brain.signal_ok('dependency_unblocking', dependency_unblocking)),
  effort                 text CHECK (brain.signal_ok('effort', effort)),
  confidence             text CHECK (brain.signal_ok('confidence', confidence)),
  charter_alignment      text CHECK (brain.signal_ok('charter_alignment', charter_alignment)),

  -- the two hard flags. NOT NULL with no permissive default: a producer that omits one fails
  -- rather than emitting a permissive row by forgetting a field.
  external               boolean NOT NULL DEFAULT false,
  canon_touching         boolean NOT NULL DEFAULT false,

  attempts               integer NOT NULL DEFAULT 0,
  max_attempts           integer NOT NULL DEFAULT 2,
  session_id             text NOT NULL DEFAULT '',
  blocked_on             text NOT NULL DEFAULT '',
  result                 text NOT NULL DEFAULT '',   -- text. Never cut. 0037 semantics.
  artifacts              text NOT NULL DEFAULT '',   -- the rendered summary line, not the record

  -- D00's three adds, free now and a migration across every table later
  canonical_task         text,        -- '<project-slug>#<task-id>' on the git planning ladder
  wager_ref              text,
  actor_type             brain.actor_type,

  produced_by            text,        -- entity id; text until D2 publishes slot 3. See NOTE below.
  accepted_at            timestamptz, -- `done` is the agent's report. Acceptance is a separate act.
  accepted_by            text
);

COMMENT ON COLUMN brain.work_item.reversibility IS
  'The column the auto-accept rule queries. Without it the eligibility test reads NULL, defaults '
  'conservative, nothing is ever eligible, and a one-week measurement reports a perfect 0 percent '
  'disagreement rate from a test that evaluated nothing.';
COMMENT ON COLUMN brain.work_item.canonical_task IS
  'Resolves to the git planning ladder. `parent` is a parent work item and `produced_by` is a '
  'producing entity; neither anchors to the ladder.';
COMMENT ON COLUMN brain.work_item.accepted_at IS
  '`done` means the agent reported it finished. Acceptance is a separate act and `reopen` is the '
  'rejection verb. Narrowing WHO accepts never narrows WHETHER it was recorded.';

-- The claim path. `claim` scans inbox and orders by the queue score, so the partial index on
-- inbox is the one that matters; the others are for ls/board/brief.
CREATE INDEX IF NOT EXISTS work_item_claimable_idx
  ON brain.work_item (lane, priority DESC, created) WHERE state = 'inbox';
CREATE INDEX IF NOT EXISTS work_item_state_idx      ON brain.work_item (state);
CREATE INDEX IF NOT EXISTS work_item_claimed_by_idx ON brain.work_item (claimed_by) WHERE state = 'active';
CREATE INDEX IF NOT EXISTS work_item_parent_idx     ON brain.work_item (parent);
CREATE INDEX IF NOT EXISTS work_item_canonical_idx  ON brain.work_item (canonical_task);

-- ---------------------------------------------------------------- run
--
-- One row per attempt. The stream stays a file, same pointer-and-hash posture as `transcript`,
-- because `show --full`'s forensic recovery reads it and a blob column would double the store.

CREATE TABLE IF NOT EXISTS brain.run (
  id                 bigserial PRIMARY KEY,
  work_item_id       text NOT NULL REFERENCES brain.work_item(id) ON DELETE CASCADE,
  attempt            integer NOT NULL,
  agent              text NOT NULL DEFAULT '',
  host               text NOT NULL DEFAULT '',
  pid                integer,
  started_at         timestamptz NOT NULL DEFAULT now(),
  ended_at           timestamptz,
  exit_code          integer,
  outcome            text,          -- done | fail | block | cancel | timeout | killed
  session_id         text NOT NULL DEFAULT '',
  stream_pointer     text,          -- absolute path to runs/<id>-attempt<N>.stream.jsonl
  stream_pointer_host text NOT NULL DEFAULT '',   -- which host that path is absolute ON
  stream_sha256      text,
  stream_bytes       bigint,
  actor_type         brain.actor_type,
  produced_by        text,
  UNIQUE (work_item_id, attempt)
);

-- ---------------------------------------------------------------- question
--
-- Shares the id sequence with work_item. Backs ask, answer, reanswer, questions, board, brief,
-- doctor, and fail's attempts-exhausted path.

CREATE TABLE IF NOT EXISTS brain.question (
  id                     text PRIMARY KEY DEFAULT brain.next_question_id(),
  asked_by               text NOT NULL DEFAULT '',
  work_item_id           text REFERENCES brain.work_item(id) ON DELETE SET NULL,
  asked_at               timestamptz NOT NULL DEFAULT now(),
  text                   text NOT NULL,               -- never cut
  default_if_unanswered  text NOT NULL DEFAULT '',
  answer                 text,                        -- never cut
  answered_at            timestamptz,
  amended_from           text,                        -- reanswer keeps the earlier answer
  amended_at             timestamptz,
  produced_by            text,
  CHECK (id ~ '^q[0-9]{4,}$')
);

CREATE INDEX IF NOT EXISTS question_open_idx ON brain.question (asked_at) WHERE answer IS NULL;
CREATE INDEX IF NOT EXISTS question_task_idx ON brain.question (work_item_id);

COMMENT ON COLUMN brain.question.default_if_unanswered IS
  'Required by the CLI: it is what turns operator silence into a usable answer instead of a '
  'stalled lane.';

-- ---------------------------------------------------------------- thread
--
-- One row per thread event. Backs note, show, feed, and every verb that writes a thread event,
-- which is most of them.

CREATE TABLE IF NOT EXISTS brain.thread (
  seq            bigserial PRIMARY KEY,
  work_item_id   text NOT NULL REFERENCES brain.work_item(id) ON DELETE CASCADE,
  ts             timestamptz NOT NULL DEFAULT now(),
  from_agent     text NOT NULL DEFAULT '',
  to_agent       text NOT NULL DEFAULT '',
  kind           text NOT NULL
                   CHECK (kind IN ('post','claim','note','msg','ask','answer','done','block',
                                   'fail','reopen','cancel','artifact','heartbeat','reap',
                                   'set','accept','event')),
  text           text NOT NULL DEFAULT '',    -- never cut. The 3000-byte cut does not port.
  produced_by    text
);

CREATE INDEX IF NOT EXISTS thread_item_idx ON brain.thread (work_item_id, seq);
CREATE INDEX IF NOT EXISTS thread_ts_idx   ON brain.thread (ts);

-- ---------------------------------------------------------------- message
--
-- Agent mailboxes. The `.read` pointer is NOT here: it is one integer per mailbox owner and
-- lives on `agent`, because a pointer per message row would be a row that means nothing.

CREATE TABLE IF NOT EXISTS brain.message (
  seq            bigserial PRIMARY KEY,
  ts             timestamptz NOT NULL DEFAULT now(),
  from_agent     text NOT NULL DEFAULT '',
  to_agent       text NOT NULL,
  kind           text NOT NULL DEFAULT 'msg',
  work_item_id   text REFERENCES brain.work_item(id) ON DELETE SET NULL,
  text           text NOT NULL,               -- never cut
  produced_by    text
);

CREATE INDEX IF NOT EXISTS message_box_idx ON brain.message (to_agent, seq);

-- ---------------------------------------------------------------- artifact
--
-- The MISSING cross-check lives here: a record whose path is not on disk NOW is a finding, not
-- a formatting problem. `exists_at_record` is what the CLI stamped when the claim was made;
-- re-statting at read time is what catches a file that was claimed and never written.

CREATE TABLE IF NOT EXISTS brain.artifact (
  seq              bigserial PRIMARY KEY,
  work_item_id     text NOT NULL REFERENCES brain.work_item(id) ON DELETE CASCADE,
  ts               timestamptz NOT NULL DEFAULT now(),
  first_seen       timestamptz NOT NULL DEFAULT now(),
  agent            text NOT NULL DEFAULT '',
  path             text NOT NULL,
  path_host        text NOT NULL DEFAULT '',   -- which host `path` is absolute ON
  kind             text NOT NULL DEFAULT 'created'
                     CHECK (kind IN ('created','modified','deleted','report','finding','external')),
  note             text NOT NULL DEFAULT '',   -- never cut
  exists_at_record boolean NOT NULL DEFAULT false,
  actor_type       brain.actor_type,
  produced_by      text
);

CREATE INDEX IF NOT EXISTS artifact_item_idx ON brain.artifact (work_item_id, seq);
CREATE INDEX IF NOT EXISTS artifact_path_idx ON brain.artifact (path);

COMMENT ON COLUMN brain.artifact.first_seen IS
  'The MINIMUM stamp in the (path, kind) group, never the first record''s stamp. A host clock '
  'that steps backwards between two writes is a hazard this operation has already been bitten by.';

-- ---------------------------------------------------------------- agent
--
-- Liveness, plus the four per-agent state homes the enumeration found: the STOP switch, the
-- planner tick fingerprint, the tick timestamp, and the mailbox read pointer.

CREATE TABLE IF NOT EXISTS brain.agent (
  name              text PRIMARY KEY,
  role              text NOT NULL DEFAULT 'terminal',
  status            text NOT NULL DEFAULT '',
  work_item_id      text REFERENCES brain.work_item(id) ON DELETE SET NULL,
  pid               integer,
  host              text NOT NULL DEFAULT '',
  updated           timestamptz NOT NULL DEFAULT now(),
  stopped_at        timestamptz,          -- agents/<a>.STOP
  tick_fingerprint  text NOT NULL DEFAULT '',
  tick_at           timestamptz,          -- agents/<a>.tick
  inbox_read_seq    bigint NOT NULL DEFAULT 0,   -- messages/<a>.read
  produced_by       text
);

COMMENT ON COLUMN brain.agent.host IS
  'From the caller or from config, never from uname(). The CLI relays, so on a relaying host '
  'uname() is the BUS host and doctor''s mismatch warning becomes a permanent false positive.';

-- ---------------------------------------------------------------- objective
--
-- Backs objectives, accept, intake, tick. `source_signature` is the intake dedup key
-- (`<size>:<mtime>`), which lives in objectives/.intake-seen.json today.

CREATE TABLE IF NOT EXISTS brain.objective (
  id                bigserial PRIMARY KEY,
  name              text NOT NULL UNIQUE,
  state             text NOT NULL DEFAULT 'inbox' CHECK (state IN ('inbox','accepted')),
  body              text NOT NULL DEFAULT '',     -- never cut
  bytes             bigint,
  source_name       text,
  source_signature  text,                          -- '<size>:<mtime>' from the drop folder
  taken_in_at       timestamptz NOT NULL DEFAULT now(),
  accepted_at       timestamptz,
  actor_type        brain.actor_type,
  produced_by       text
);

-- =================================================================== RUNTIME AND LEDGER (8 frozen)

-- ---------------------------------------------------------------- session

CREATE TABLE IF NOT EXISTS brain.session (
  id                text PRIMARY KEY,
  harness           text NOT NULL DEFAULT '',   -- claude | codex | ...
  agent             text NOT NULL DEFAULT '',
  role              text NOT NULL DEFAULT '',
  host              text NOT NULL DEFAULT '',
  workdir           text NOT NULL DEFAULT '',
  workdir_host      text NOT NULL DEFAULT '',   -- which host `workdir` is absolute ON
  stated_goal       text,                        -- captured at register; see D3 slot 4
  parent_session_id text REFERENCES brain.session(id) ON DELETE SET NULL,
  work_item_id      text REFERENCES brain.work_item(id) ON DELETE SET NULL,
  caused_by_event_id bigint,                     -- FK added after `event` exists, below
  started_at        timestamptz NOT NULL DEFAULT now(),
  ended_at          timestamptz,
  actor_type        brain.actor_type,
  produced_by       text
);

CREATE INDEX IF NOT EXISTS session_parent_idx ON brain.session (parent_session_id);
CREATE INDEX IF NOT EXISTS session_item_idx   ON brain.session (work_item_id);

-- ---------------------------------------------------------------- transcript
--
-- A POINTER AND A HASH, NEVER THE BLOB. `pointer_host` is not optional: v1 is local-attended
-- and a later move to a VPS breaks every absolute path silently otherwise.

CREATE TABLE IF NOT EXISTS brain.transcript (
  id            bigserial PRIMARY KEY,
  session_id    text REFERENCES brain.session(id) ON DELETE SET NULL,
  pointer       text NOT NULL,
  pointer_host  text NOT NULL,
  sha256        text NOT NULL,
  bytes         bigint,
  harness       text NOT NULL DEFAULT '',
  indexed_at    timestamptz NOT NULL DEFAULT now(),
  verified_at   timestamptz,
  verified_ok   boolean,
  produced_by   text,
  UNIQUE (pointer, pointer_host)
);

COMMENT ON TABLE brain.transcript IS
  'Pointer and hash only. Supersedes decisions/session-transcript-posture.md, which says store '
  'full copies under sessions/logs/. That supersession is D8''s to record; this table is not '
  'evidence that it was recorded.';
COMMENT ON COLUMN brain.transcript.pointer_host IS
  'v1 is local-attended. Every pointer is absolute on THIS host and a VPS move invalidates all '
  'of them. Recording which host makes that failure loud instead of silent.';

-- ---------------------------------------------------------------- event
--
-- Append-only. Retention-bounded: 14 days machine, 90 days standard and consequential, 400 days
-- for rollups. `payload_summary` has a 4096-byte ceiling that REJECTS rather than truncates:
-- the full payload lives behind `payload_ref`, so nothing is destroyed. That is the difference
-- between the fabric's volume contract and the write paths this store exists to replace.

CREATE TABLE IF NOT EXISTS brain.event (
  event_seq        bigserial PRIMARY KEY,
  event_id         uuid NOT NULL UNIQUE DEFAULT gen_random_uuid(),
  type             text NOT NULL,
  occurred_at      timestamptz NOT NULL DEFAULT now(),
  emitted_at       timestamptz NOT NULL DEFAULT now(),
  department       text NOT NULL DEFAULT '',
  lane             text NOT NULL DEFAULT '',
  subject_type     text NOT NULL DEFAULT '',
  subject_id       text NOT NULL DEFAULT '',
  payload_summary  text NOT NULL DEFAULT ''
                     CHECK (octet_length(payload_summary) <= 4096),
  payload_ref      text,        -- where the untruncated payload is, when there is more
  retention_class  text NOT NULL DEFAULT 'standard'
                     CHECK (retention_class IN ('machine','standard','consequential')),
  -- hard flags, NOT NULL with no permissive default. A producer that omits one fails the insert
  -- rather than emitting a permissive event by forgetting a field.
  external         boolean NOT NULL,
  canon_touching   boolean NOT NULL,
  work_item_id     text REFERENCES brain.work_item(id) ON DELETE SET NULL,
  session_id       text REFERENCES brain.session(id) ON DELETE SET NULL,
  actor_type       brain.actor_type,
  produced_by      text
);

CREATE INDEX IF NOT EXISTS event_type_idx    ON brain.event (type, event_seq);
CREATE INDEX IF NOT EXISTS event_subject_idx ON brain.event (subject_type, subject_id);
CREATE INDEX IF NOT EXISTS event_sweep_idx   ON brain.event (retention_class, occurred_at);

COMMENT ON TABLE brain.event IS
  'The event table is complete, ordered, timestamped and queryable, which makes it feel more '
  'authoritative than anything reconciled from git. It is not. It has no assignee, priority, due '
  'date or status column by construction, and proposing one is a contract change.';
COMMENT ON COLUMN brain.event.payload_summary IS
  'Ceiling REJECTS at 4096 bytes, it does not truncate. Anything larger goes behind payload_ref. '
  'Porting a destructive cut into a store whose pitch is that nothing is lost would be the worst '
  'kind of faithful.';

ALTER TABLE brain.session
  ADD CONSTRAINT session_caused_by_event_fk
  FOREIGN KEY (caused_by_event_id) REFERENCES brain.event(event_seq) ON DELETE SET NULL;

-- ---------------------------------------------------------------- event_rollup
--
-- Day-grain aggregate of machine-volume events, 400-day retention. Not in D00's frozen sixteen;
-- required by the accepted fabric design's owned-runtime-state list and by the fact that D00's
-- own retention line has a 400-day tier that nothing else could be about.

CREATE TABLE IF NOT EXISTS brain.event_rollup (
  day          date NOT NULL,
  type         text NOT NULL,
  department   text NOT NULL DEFAULT '',
  producer     text NOT NULL DEFAULT '',
  n            bigint NOT NULL DEFAULT 0,
  rolled_at    timestamptz NOT NULL DEFAULT now(),
  produced_by  text,
  PRIMARY KEY (day, type, department, producer)
);

-- ---------------------------------------------------------------- observation

CREATE TABLE IF NOT EXISTS brain.observation (
  id            bigserial PRIMARY KEY,
  event_id      bigint REFERENCES brain.event(event_seq) ON DELETE SET NULL,
  subject_type  text NOT NULL DEFAULT '',
  subject_id    text NOT NULL DEFAULT '',
  kind          text NOT NULL DEFAULT '',
  text          text NOT NULL DEFAULT '',     -- never cut
  status        text NOT NULL DEFAULT 'open',
  observed_at   timestamptz NOT NULL DEFAULT now(),
  closed_at     timestamptz,
  actor_type    brain.actor_type,
  produced_by   text
);

-- ---------------------------------------------------------------- disposition
--
-- EF-3: event_id is REQUIRED and observation_id is NULLABLE. A disposition always answers an
-- occurrence; it does not always answer a standing observation.

CREATE TABLE IF NOT EXISTS brain.disposition (
  id              bigserial PRIMARY KEY,
  event_id        bigint NOT NULL REFERENCES brain.event(event_seq) ON DELETE RESTRICT,
  observation_id  bigint REFERENCES brain.observation(id) ON DELETE SET NULL,
  verdict         text NOT NULL,
  rationale       text NOT NULL DEFAULT '',   -- never cut
  decided_at      timestamptz NOT NULL DEFAULT now(),
  decided_by      text NOT NULL DEFAULT '',
  wager_ref       text,                        -- doctrine attaches wagers at disposition
  actor_type      brain.actor_type,
  produced_by     text
);

CREATE INDEX IF NOT EXISTS disposition_event_idx ON brain.disposition (event_id);

-- ---------------------------------------------------------------- recommendation
--
-- requires_human is NOT NULL with no default. A recommendation that forgot to say whether a
-- human must decide is not a safe recommendation, it is an unanswered question.

CREATE TABLE IF NOT EXISTS brain.recommendation (
  id              bigserial PRIMARY KEY,
  subject_type    text NOT NULL DEFAULT '',
  subject_id      text NOT NULL DEFAULT '',
  text            text NOT NULL,               -- never cut
  rationale       text NOT NULL DEFAULT '',    -- never cut
  requires_human  boolean NOT NULL,
  state           text NOT NULL DEFAULT 'open'
                    CHECK (state IN ('open','accepted','rejected','expired')),
  cites_session_id text REFERENCES brain.session(id) ON DELETE SET NULL,
  cites_event_id  bigint REFERENCES brain.event(event_seq) ON DELETE SET NULL,
  created_at      timestamptz NOT NULL DEFAULT now(),
  decided_at      timestamptz,
  decided_by      text,
  actor_type      brain.actor_type,
  produced_by     text
);

COMMENT ON COLUMN brain.recommendation.requires_human IS
  'NOT NULL with no default, on purpose. No agent self-approves canon and the decider on every '
  'acceptance is a human; a NULL here would read as false at exactly the wrong moment.';

-- ---------------------------------------------------------------- receipt
--
-- EF-5: caused_by_event_id and approval_ref. A receipt is written at the moment the action
-- happens and outlives the event row by design, so the FK is SET NULL rather than CASCADE:
-- the retention sweep must never take a receipt with it.

CREATE TABLE IF NOT EXISTS brain.receipt (
  id                 bigserial PRIMARY KEY,
  action             text NOT NULL,
  subject_type       text NOT NULL DEFAULT '',
  subject_id         text NOT NULL DEFAULT '',
  detail             text NOT NULL DEFAULT '',   -- never cut
  booked_at          timestamptz NOT NULL DEFAULT now(),
  booked_by          text NOT NULL DEFAULT '',
  caused_by_event_id bigint REFERENCES brain.event(event_seq) ON DELETE SET NULL,
  approval_ref       text,
  git_ref            text,        -- the commit that IS the promotion
  external           boolean NOT NULL DEFAULT false,
  canon_touching     boolean NOT NULL DEFAULT false,
  actor_type         brain.actor_type,
  produced_by        text
);

CREATE INDEX IF NOT EXISTS receipt_event_idx ON brain.receipt (caused_by_event_id);

COMMENT ON TABLE brain.receipt IS
  'The receipt is evidence in the runtime; the git commit is the promotion. A Postgres trigger '
  'must never write to git.';

-- ---------------------------------------------------------------- subscriber_cursor
--
-- EF-23: declaration_commit and quarantined_at. Listener health is LAG, not liveness:
-- max(event_seq) - last_seq. pg_isready proves the server is up and nothing about the listeners,
-- which bind no port and are therefore outside the port registry entirely.

CREATE TABLE IF NOT EXISTS brain.subscriber_cursor (
  subscriber          text PRIMARY KEY,
  last_seq            bigint NOT NULL DEFAULT 0,
  updated_at          timestamptz NOT NULL DEFAULT now(),
  declaration_commit  text NOT NULL DEFAULT '',
  quarantined_at      timestamptz,
  quarantine_reason   text NOT NULL DEFAULT '',
  produced_by         text
);

CREATE OR REPLACE VIEW brain.subscriber_lag AS
  SELECT c.subscriber,
         c.last_seq,
         COALESCE((SELECT max(event_seq) FROM brain.event), 0) AS head_seq,
         COALESCE((SELECT max(event_seq) FROM brain.event), 0) - c.last_seq AS lag,
         c.quarantined_at IS NOT NULL AS quarantined,
         c.updated_at
    FROM brain.subscriber_cursor c;

-- =================================================================== LINEAGE

-- ---------------------------------------------------------------- touch
--
-- `produced_by` on every table stays and is NOT sufficient on its own. The wager ledger scores
-- which components SHAPED an action -- agent plus skill plus rule plus playbook -- which is a
-- set, not a scalar.

CREATE TABLE IF NOT EXISTS brain.touch (
  id            bigserial PRIMARY KEY,
  subject_type  text NOT NULL,
  subject_id    text NOT NULL,
  entity_id     text NOT NULL,
  role          text NOT NULL DEFAULT '',
  touched_at    timestamptz NOT NULL DEFAULT now(),
  produced_by   text,
  UNIQUE (subject_type, subject_id, entity_id, role)
);

CREATE INDEX IF NOT EXISTS touch_subject_idx ON brain.touch (subject_type, subject_id);
CREATE INDEX IF NOT EXISTS touch_entity_idx  ON brain.touch (entity_id);

-- =================================================================== FLEET STATE

-- ---------------------------------------------------------------- runtime_flag
--
-- Found by enumeration, not named in the frozen sixteen. `PAUSE` is one fleet-wide switch that
-- six verbs read or write (pause, resume, paused, claim, tick, status) and it is not per-agent,
-- so it cannot be a column on `agent`. D6a's budget switches land here too.

CREATE TABLE IF NOT EXISTS brain.runtime_flag (
  key          text PRIMARY KEY,
  value        text NOT NULL DEFAULT '',
  set_at       timestamptz NOT NULL DEFAULT now(),
  set_by       text NOT NULL DEFAULT '',
  note         text NOT NULL DEFAULT '',
  produced_by  text
);

COMMENT ON TABLE brain.runtime_flag IS
  'Fleet-level switches. `fleet_paused` is the ported PAUSE file. Budget switches (D6a) belong '
  'here rather than in a table of their own.';

-- =================================================================== VIEWS THE VERBS NEED

-- `feed` is a union of threads, messages and questions in time order. It is a view because it is
-- three tables read one way, not a fourth table to keep in sync.
CREATE OR REPLACE VIEW brain.feed AS
  SELECT t.ts, t.from_agent AS from_who, t.to_agent AS to_who, t.kind,
         t.work_item_id, t.text
    FROM brain.thread t
  UNION ALL
  SELECT m.ts, m.from_agent, m.to_agent, 'msg', m.work_item_id, m.text
    FROM brain.message m
  UNION ALL
  SELECT q.asked_at, q.asked_by, 'operator', 'ask', q.work_item_id, q.text
    FROM brain.question q
  UNION ALL
  SELECT q.answered_at, 'operator', q.asked_by, 'answer', q.work_item_id, q.answer
    FROM brain.question q WHERE q.answered_at IS NOT NULL;

-- `signals` and `why` resolve hard flags by OR up the WHOLE parent chain, on every read rather
-- than stamped at post time: raising a flag on a parent raises it on every descendant,
-- retroactively. Holding for exactly one hop is flag laundering.
CREATE OR REPLACE VIEW brain.work_item_signals AS
  WITH RECURSIVE chain(id, ancestor, external, canon_touching) AS (
    SELECT w.id, w.id, w.external, w.canon_touching FROM brain.work_item w
    UNION ALL
    SELECT c.id, p.id, p.external, p.canon_touching
      FROM chain c JOIN brain.work_item p ON p.id = (SELECT parent FROM brain.work_item WHERE id = c.ancestor)
  )
  SELECT w.id,
         bool_or(c.external)       AS external,
         bool_or(c.canon_touching) AS canon_touching,
         brain.signal_level('stakes', w.stakes)                               AS stakes,
         brain.signal_level('reversibility', w.reversibility)                 AS reversibility,
         brain.signal_level('urgency', w.urgency)                             AS urgency,
         brain.signal_level('dependency_unblocking', w.dependency_unblocking) AS dependency_unblocking,
         brain.signal_level('effort', w.effort)                               AS effort,
         brain.signal_level('confidence', w.confidence)                       AS confidence,
         brain.signal_level('charter_alignment', w.charter_alignment)         AS charter_alignment
    FROM brain.work_item w JOIN chain c ON c.id = w.id
   GROUP BY w.id, w.stakes, w.reversibility, w.urgency, w.dependency_unblocking,
            w.effort, w.confidence, w.charter_alignment;

-- The auto-accept eligibility test. SHIPS DISABLED, behind one week of measurement: this view
-- says who WOULD be eligible, and nothing reads it to act yet. It is here so the measurement has
-- something to measure -- a test that evaluated nothing is what this whole schema exists to stop.
CREATE OR REPLACE VIEW brain.auto_accept_candidate AS
  SELECT w.id, s.external, s.canon_touching, s.reversibility, w.finished_at
    FROM brain.work_item w JOIN brain.work_item_signals s ON s.id = w.id
   WHERE w.state = 'done'
     AND w.accepted_at IS NULL
     AND s.external = false
     AND s.canon_touching = false
     AND s.reversibility = 'high';   -- 'high' reversibility IS reversible in this vocabulary

-- =================================================================== RETENTION
--
-- Owned by the owner role. The only paths that delete a row are migrations and this sweep.

CREATE OR REPLACE FUNCTION brain.sweep_events()
  RETURNS TABLE(retention_class text, deleted bigint)
  LANGUAGE plpgsql SECURITY INVOKER AS $$
BEGIN
  RETURN QUERY
  WITH gone AS (
    DELETE FROM brain.event e
     WHERE (e.retention_class = 'machine'       AND e.occurred_at < now() - interval '14 days')
        OR (e.retention_class = 'standard'      AND e.occurred_at < now() - interval '90 days')
        OR (e.retention_class = 'consequential' AND e.occurred_at < now() - interval '90 days')
    RETURNING e.retention_class AS rc)
  SELECT g.rc, count(*) FROM gone g GROUP BY g.rc;
END $$;

CREATE OR REPLACE FUNCTION brain.sweep_rollups() RETURNS bigint
  LANGUAGE plpgsql SECURITY INVOKER AS $$
DECLARE n bigint;
BEGIN
  DELETE FROM brain.event_rollup WHERE day < (current_date - 400);
  GET DIAGNOSTICS n = ROW_COUNT;
  RETURN n;
END $$;

COMMENT ON FUNCTION brain.sweep_events() IS
  '14 days machine, 90 days standard and consequential. The numbers are the contract, not an '
  'intention. Receipts survive the sweep: receipt.caused_by_event_id is ON DELETE SET NULL.';

-- =================================================================== SCHEMA VERSION

CREATE TABLE IF NOT EXISTS brain.schema_migration (
  version     integer PRIMARY KEY,
  name        text NOT NULL,
  applied_at  timestamptz NOT NULL DEFAULT now()
);

INSERT INTO brain.schema_migration (version, name) VALUES (1, '0001_initial')
  ON CONFLICT (version) DO NOTHING;

COMMIT;

-- NOTE for D2, slot 3. `produced_by` is `text` on every table, not a foreign key, and that is a
-- decision this migration could not make: slot 3 had not been posted when this was written. Text
-- survives either answer. If entity ids turn out to be stable across a rename, migration 2 can
-- add the FK; if they do not, a FK would have been wrong and this column stays as it is.
