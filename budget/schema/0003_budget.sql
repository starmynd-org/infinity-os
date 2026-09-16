-- migration 3: budget enforcement
--
-- The spend brake. D1 asked whether budget state fits `runtime_flag`; the answer, with its
-- measurements, is in D-CROSSTALK slot 1. Short version: `runtime_flag` is `key text PRIMARY KEY`
-- with an untyped `value text`, and a budget needs three things a flat key/value cannot give it --
-- an append-only stream of breaches (many rows per scope), typed fields with real ranges, and a
-- spend meter with an idempotency key. So: three tables, in this file, additive. Nothing in
-- `0001_initial.sql` or `0002_roles.sql` is edited.
--
-- The one property this file exists to make structural is on `budget_incident`:
--
--     cause text NOT NULL DEFAULT 'budget' CHECK (cause = 'budget')
--
-- A subscription refusal is not a breach. It is a window that reopens on its own, it costs
-- nothing, and the task goes back unspent. A budget stop is the operator's money and means stop.
-- Conflating them is expensive in both directions: a refusal filed as a breach strands a healthy
-- fleet, and a breach filed as a refusal retries into real spend. That CHECK means the breach
-- table cannot physically hold a refusal, so no code path can file one there however it is
-- written -- the same reasoning that makes the four roles grants rather than if-statements.
--
-- Time base is UTC everywhere, matching the `date -u` convention the commander set at 11:52Z.
-- A 'day' window is a UTC calendar day; a host in UTC+3 must not get a brake that resets three
-- hours early.

\set ON_ERROR_STOP on

BEGIN;

SET search_path TO brain, public;

-- Version 3 is claimed here. If another lane already took 3 under a different name, stop loudly:
-- two migrations sharing a version is the kind of thing that is discovered a week later.
DO $$
DECLARE existing text;
BEGIN
  SELECT name INTO existing FROM brain.schema_migration WHERE version = 3;
  IF existing IS NOT NULL AND existing <> '0003_budget' THEN
    RAISE EXCEPTION 'schema version 3 is already applied as %, not 0003_budget. Renumber this '
                    'migration rather than applying it over another lane''s.', existing;
  END IF;
END $$;

-- ---------------------------------------------------------------- vocabularies
--
-- Enums rather than free text, because every one of these is read by a branch. A typo in a text
-- column becomes a branch that silently never fires, and the branch that never fires here is the
-- one that stops spending.

DO $$ BEGIN
  CREATE TYPE brain.budget_scope AS ENUM ('fleet', 'agent', 'lane', 'work_item');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- 'total' is lifetime-of-policy, which is what a one-night ceiling wants. 'day' is a UTC calendar
-- day. 'rolling_24h' is the honest form for a fleet that runs across midnight.
DO $$ BEGIN
  CREATE TYPE brain.budget_period AS ENUM ('day', 'rolling_24h', 'total');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- The typed breach kinds. `warn` is not a stop and is recorded separately so that "we warned and
-- kept spending" can never be read as "we stopped".
DO $$ BEGIN
  CREATE TYPE brain.budget_incident_kind AS ENUM (
    'warn',              -- crossed warn_percent, still running. Not a stop.
    'hard_stop',         -- crossed the ceiling with hard_stop_enabled. A run was killed.
    'soft_breach',       -- crossed the ceiling with hard_stop_enabled = false. Recorded, not stopped.
    'manual_stop',       -- `budget stop`. The operator's act, no spend basis needed.
    'blocked_dispatch',  -- preflight refused to start a run at all. The cheapest stop there is.
    'resumed'            -- a manual stop lifted by `budget resume`. Closes the pair.
  );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ---------------------------------------------------------------- budget_policy
--
-- Ported from Paperclip's `budget_policies`: scopeType/scopeId generality, warnPercent,
-- hardStopEnabled. `period` and `limit_usd` are added because a ceiling without a window and a
-- number is not a ceiling.

CREATE TABLE IF NOT EXISTS brain.budget_policy (
  id                bigserial PRIMARY KEY,
  scope_type        brain.budget_scope  NOT NULL,
  -- '' for fleet. A scope_id on a fleet policy would be a second fleet.
  scope_id          text                NOT NULL DEFAULT '',
  period            brain.budget_period NOT NULL DEFAULT 'day',
  limit_usd         numeric(12,4)       NOT NULL CHECK (limit_usd >= 0),
  warn_percent      integer             NOT NULL DEFAULT 80
                                        CHECK (warn_percent > 0 AND warn_percent <= 100),
  -- The difference between a brake and a dashboard. Default true: a budget that does not stop
  -- is the state this lane exists to end.
  hard_stop_enabled boolean             NOT NULL DEFAULT true,
  state             text                NOT NULL DEFAULT 'active'
                                        CHECK (state IN ('active', 'retired')),
  -- Lifetime-of-policy windows measure from here, so raising a ceiling does not reset the meter.
  effective_from    timestamptz         NOT NULL DEFAULT now(),
  set_at            timestamptz         NOT NULL DEFAULT now(),
  set_by            text                NOT NULL DEFAULT '',
  note              text                NOT NULL DEFAULT '',
  retired_at        timestamptz,
  retired_by        text                NOT NULL DEFAULT '',
  actor_type        brain.actor_type,
  produced_by       text,
  CHECK (scope_type <> 'fleet' OR scope_id = '')
);

-- One active policy per (scope, period). A second one is not a stricter budget, it is two
-- answers to one question, and the enforcement path would have to pick.
CREATE UNIQUE INDEX IF NOT EXISTS budget_policy_active_idx
  ON brain.budget_policy (scope_type, scope_id, period) WHERE state = 'active';

COMMENT ON TABLE brain.budget_policy IS
  'The ceiling. Ported from Paperclip budget_policies. One active row per (scope_type, scope_id, '
  'period); superseded rows are retired, never deleted, so `budget status --history` can show '
  'what the ceiling was when a breach happened.';

-- ---------------------------------------------------------------- budget_charge
--
-- The meter. `run` has no cost column, so before this table nothing in the store recorded what a
-- run spent, and a ceiling with nothing to compare against is a warning.
--
-- A charge carries dimensions rather than a scope, because one charge rolls up into every scope
-- that covers it: agent T2's dollar is also the budget lane's dollar and also the fleet's.

CREATE TABLE IF NOT EXISTS brain.budget_charge (
  id           bigserial PRIMARY KEY,
  charged_at   timestamptz   NOT NULL DEFAULT now(),
  usd          numeric(12,6) NOT NULL CHECK (usd >= 0),
  agent        text          NOT NULL DEFAULT '',
  lane         text          NOT NULL DEFAULT '',
  work_item_id text          REFERENCES brain.work_item(id),
  run_id       bigint        REFERENCES brain.run(id),
  session_id   text          NOT NULL DEFAULT '',
  -- 'run_json' is the engine's own total_cost_usd. 'stream' is an increment read off a live run.
  -- 'manual' is the operator. 'estimate' is a pre-charge that a real one supersedes.
  source       text          NOT NULL DEFAULT 'run_json'
                             CHECK (source IN ('run_json', 'stream', 'manual', 'estimate')),
  source_ref   text          NOT NULL,
  note         text          NOT NULL DEFAULT '',
  actor_type   brain.actor_type,
  produced_by  text
);

-- Idempotency, and it is the reason this is a table rather than a column on `run`. Reconciliation
-- re-reads a run.json; a long run is charged incrementally while still running. Without this
-- constraint a re-read double-charges, and a spend brake that over-counts stops a healthy fleet
-- exactly as expensively as one that under-counts lets it burn.
CREATE UNIQUE INDEX IF NOT EXISTS budget_charge_source_idx
  ON brain.budget_charge (source, source_ref);

CREATE INDEX IF NOT EXISTS budget_charge_window_idx ON brain.budget_charge (charged_at);
CREATE INDEX IF NOT EXISTS budget_charge_agent_idx  ON brain.budget_charge (agent, charged_at);
CREATE INDEX IF NOT EXISTS budget_charge_lane_idx   ON brain.budget_charge (lane, charged_at);

COMMENT ON TABLE brain.budget_charge IS
  'The spend meter. A zero charge is recorded, not skipped: a run that cost nothing is evidence, '
  'and it is how a subscription refusal proves it spent nothing rather than being assumed to.';

-- ---------------------------------------------------------------- budget_incident
--
-- Typed breach records, first class. Not a log line: it joins to run and work_item, it is
-- queryable by kind, and every row states what was actually DONE about it.

CREATE TABLE IF NOT EXISTS brain.budget_incident (
  id            bigserial PRIMARY KEY,
  occurred_at   timestamptz NOT NULL DEFAULT now(),
  kind          brain.budget_incident_kind NOT NULL,
  policy_id     bigint REFERENCES brain.budget_policy(id),
  scope_type    brain.budget_scope NOT NULL,
  scope_id      text NOT NULL DEFAULT '',
  period        brain.budget_period,
  window_start  timestamptz,
  window_end    timestamptz,
  spend_usd     numeric(12,6) NOT NULL DEFAULT 0,
  limit_usd     numeric(12,4),
  -- Stored, not derived on read: the ceiling can be raised afterwards and the row must keep
  -- saying what the breach looked like at the time.
  percent_used  numeric(8,2),

  -- THE COLUMN THIS TABLE EXISTS FOR.
  -- A subscription refusal is not a breach. This CHECK is why no code path can file one here,
  -- however that code is written. See docs at the top of this file, and budget/halt.py.
  cause         text NOT NULL DEFAULT 'budget' CHECK (cause = 'budget'),

  -- What was actually done. 'none' is a legitimate value only for `warn` and `soft_breach`.
  action_taken  text NOT NULL DEFAULT 'none'
                CHECK (action_taken IN ('none', 'warned', 'run_stopped', 'dispatch_refused',
                                        'fleet_stopped', 'agent_stopped', 'resumed')),
  -- 'charge' = found while metering. 'preflight' = found before a run started. 'manual' = the
  -- operator. Distinguishing them is how "we caught it before spending" is measurable.
  detected_by   text NOT NULL DEFAULT 'charge'
                CHECK (detected_by IN ('charge', 'preflight', 'manual', 'sweep')),

  -- What the dispatcher should do with the task. A budget stop is NOT `fail` (the lane did
  -- nothing wrong) and NOT `reopen` (that returns it to the queue to be claimed and spent
  -- again -- the money retry loop). See D-CROSSTALK for the handoff to D4.
  prescribed_disposition text NOT NULL DEFAULT 'none'
                CHECK (prescribed_disposition IN ('none', 'block', 'hold_dispatch')),

  agent         text NOT NULL DEFAULT '',
  lane          text NOT NULL DEFAULT '',
  work_item_id  text   REFERENCES brain.work_item(id),
  run_id        bigint REFERENCES brain.run(id),
  session_id    text NOT NULL DEFAULT '',
  engine_pid    integer,
  signal_sent   text NOT NULL DEFAULT '',
  detail        text NOT NULL DEFAULT '',

  -- A stop does not clear itself. That is the whole difference from a refusal, and it is a
  -- nullable timestamp rather than a convention.
  cleared_at    timestamptz,
  cleared_by    text NOT NULL DEFAULT '',
  clear_ref     text NOT NULL DEFAULT '',

  actor_type    brain.actor_type,
  produced_by   text,

  -- A warn that claims it stopped something, or a hard stop that claims it did nothing, is a
  -- record that lies. Refuse both at the database.
  CHECK (kind <> 'warn' OR action_taken IN ('none', 'warned')),
  CHECK (kind <> 'hard_stop' OR action_taken IN ('run_stopped', 'dispatch_refused',
                                                 'fleet_stopped', 'agent_stopped')),
  CHECK (scope_type <> 'fleet' OR scope_id = '')
);

CREATE INDEX IF NOT EXISTS budget_incident_open_idx
  ON brain.budget_incident (scope_type, scope_id) WHERE cleared_at IS NULL;
CREATE INDEX IF NOT EXISTS budget_incident_kind_idx ON brain.budget_incident (kind, occurred_at);
CREATE INDEX IF NOT EXISTS budget_incident_run_idx  ON brain.budget_incident (run_id);

COMMENT ON TABLE brain.budget_incident IS
  'Typed breach records. `cause` is CHECKed to ''budget'', so a subscription refusal cannot be '
  'filed here by any code path: a refusal reopens on its own and costs nothing, a breach is the '
  'operator''s money and does not.';

COMMENT ON COLUMN brain.budget_incident.cause IS
  'Always ''budget''. The CHECK is the structural half of "a budget stop is provably distinct '
  'from a subscription refusal": rate-limit halts are recorded by the runner as a reopen and '
  'never reach this table.';

-- ---------------------------------------------------------------- the windowing function
--
-- One definition of "the window", used by the status view, the gate view and the enforcer alike.
-- Two definitions is how a brake fires at a different number than the dashboard shows.

CREATE OR REPLACE FUNCTION brain.budget_window_start(p brain.budget_policy, at timestamptz)
  RETURNS timestamptz LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE p.period
           WHEN 'day'         THEN date_trunc('day', at AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'
           WHEN 'rolling_24h' THEN at - interval '24 hours'
           WHEN 'total'       THEN p.effective_from
         END
$$;

-- Does a charge fall inside a policy's scope? One definition, so agent/lane/fleet rollup cannot
-- disagree between the view and the enforcer.
CREATE OR REPLACE FUNCTION brain.budget_charge_in_scope(
    scope brain.budget_scope, scope_id text,
    c_agent text, c_lane text, c_work_item text)
  RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE scope
           WHEN 'fleet'     THEN true
           WHEN 'agent'     THEN c_agent = scope_id
           WHEN 'lane'      THEN c_lane = scope_id
           WHEN 'work_item' THEN c_work_item = scope_id
         END
$$;

-- ---------------------------------------------------------------- budget_state
--
-- What `budget status` reads. One row per active policy: the window, the spend in it, the
-- percent, and the two booleans that matter.

CREATE OR REPLACE VIEW brain.budget_state AS
SELECT
  p.id                AS policy_id,
  p.scope_type, p.scope_id, p.period,
  p.limit_usd, p.warn_percent, p.hard_stop_enabled, p.set_at, p.set_by, p.note,
  brain.budget_window_start(p, now())                       AS window_start,
  now()                                                     AS window_end,
  COALESCE(s.spend, 0)::numeric(12,6)                       AS spend_usd,
  CASE WHEN p.limit_usd = 0 THEN NULL
       ELSE round(COALESCE(s.spend, 0) * 100 / p.limit_usd, 2) END AS percent_used,
  (COALESCE(s.spend, 0) >= p.limit_usd)                     AS over_limit,
  (COALESCE(s.spend, 0) >= p.limit_usd * p.warn_percent / 100.0) AS over_warn,
  -- Over the ceiling with the brake armed. This is the boolean the gate acts on.
  (COALESCE(s.spend, 0) >= p.limit_usd AND p.hard_stop_enabled) AS stopping,
  -- An uncleared manual stop, which has no spend basis and does not clear itself.
  EXISTS (SELECT 1 FROM brain.budget_incident i
           WHERE i.kind = 'manual_stop' AND i.cleared_at IS NULL
             AND i.scope_type = p.scope_type AND i.scope_id = p.scope_id) AS manually_stopped
FROM brain.budget_policy p
LEFT JOIN LATERAL (
  SELECT sum(c.usd) AS spend
    FROM brain.budget_charge c
   WHERE c.charged_at >= brain.budget_window_start(p, now())
     AND brain.budget_charge_in_scope(p.scope_type, p.scope_id, c.agent, c.lane, c.work_item_id)
) s ON true
WHERE p.state = 'active';

COMMENT ON VIEW brain.budget_state IS
  'One row per active policy with its current window spend. `budget status` renders this; the '
  'enforcer decides from it. One definition of the window, so the brake and the dashboard cannot '
  'disagree about the number.';

-- A manual stop on a scope with no policy still has to stop things. This view carries those, so
-- the gate never has to look in two places.
CREATE OR REPLACE VIEW brain.budget_open_stop AS
SELECT i.id AS incident_id, i.scope_type, i.scope_id, i.kind, i.occurred_at, i.detail, i.agent,
       i.prescribed_disposition
  FROM brain.budget_incident i
 WHERE i.cleared_at IS NULL
   AND i.kind IN ('manual_stop', 'hard_stop');

-- ---------------------------------------------------------------- grants
--
-- New tables inherit nothing: `0002_roles.sql` grants to brain_runtime by explicit table list,
-- and ALTER DEFAULT PRIVILEGES was set for owner only. DELETE is granted to nobody, keeping
-- migration 2's posture: only migrations and the retention sweep delete a row.

GRANT ALL PRIVILEGES ON brain.budget_policy, brain.budget_charge, brain.budget_incident
  TO brain_owner;
GRANT ALL PRIVILEGES ON SEQUENCE brain.budget_policy_id_seq, brain.budget_charge_id_seq,
                                 brain.budget_incident_id_seq TO brain_owner;

GRANT SELECT, INSERT, UPDATE ON
  brain.budget_policy, brain.budget_charge, brain.budget_incident TO brain_runtime;
GRANT USAGE ON SEQUENCE brain.budget_policy_id_seq, brain.budget_charge_id_seq,
                        brain.budget_incident_id_seq TO brain_runtime;
GRANT SELECT ON brain.budget_state, brain.budget_open_stop TO brain_runtime;

GRANT EXECUTE ON FUNCTION brain.budget_window_start(brain.budget_policy, timestamptz)
  TO brain_runtime;
GRANT EXECUTE ON FUNCTION brain.budget_charge_in_scope(brain.budget_scope, text, text, text, text)
  TO brain_runtime;

-- The producer role gets nothing here. A spend brake is not on the event bus.

INSERT INTO brain.schema_migration (version, name) VALUES (3, '0003_budget')
  ON CONFLICT (version) DO NOTHING;

COMMIT;
