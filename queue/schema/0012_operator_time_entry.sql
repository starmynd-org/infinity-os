-- 0012_operator_time_entry.sql -- brain.time_entry: the operator's stopwatch, append-only.
--
-- LEDGER VERSION 23. The filename prefix is this lane's counter and it lies by four; the number
-- that matters is the one in the INSERT at the bottom. Read from `brain.schema_migration`, never
-- from `ls`: this repo keeps migrations in three directories and `engine/bin/scratch-db.sh`
-- orders by the recorded version for exactly that reason. Measured on the live `brain` database
-- 2026-08-17T12:2xZ, task 0279: the ledger holds 1..18, 20, 21, 22. 19 is a real gap -- the file
-- `migrations/0019_git_ref_repo_identity.sql` claims in its own header to have applied clean and
-- it did not. So max(version) = 22 and 23 is free. Re-read it immediately before you apply this.
--
-- Additive. It creates ONE table, TWO views, TWO indexes, ONE trigger and its function, and
-- grants. It alters no existing table, drops nothing, and no existing object references it.
--
-- ========================================================================= WHY THIS TABLE
--
-- This system measures AGENT cost to absurd precision: `budget/price_card.py` fits a four-rate
-- card against 103 runs and reproduces the billed total. It measures the OPERATOR's time not at
-- all. The entire program exists to buy back his review hours and there was no column anywhere
-- that recorded one.
--
-- Two things that were assertions become facts the moment rows land here:
--
--   1. `queue depth` / `queue eta`. They divide depth by clearance. The clearance they had was
--      an ITEM COUNT over a window -- honest as far as it goes, but it cannot answer "how many
--      hours of your time is this backlog", because it has no minutes in it. It also silently
--      averages over days he never opened the queue.
--   2. The tier ceilings. `Decide` claims a two-minute ceiling and nobody had ever put a
--      stopwatch on one. `queue demote` already logs a calibration miss against the producer when
--      the operator taps `not fast`; with real durations that miss stops being an opinion.
--
-- ========================================================================= APPEND-ONLY, AND WHY
--
-- A record you can silently rewrite is not a record. That is migration 22's rule for acceptances
-- and it holds here for the same reason and one more: this table is a MEASUREMENT INPUT. The
-- moment a stopped interval can be edited, every number computed from it is a number whose
-- provenance nobody can reconstruct -- and the numbers computed from it are the ones the operator
-- will use to decide whether this whole queue layer is worth his morning.
--
-- So: a stopped entry is never edited. A correction is a NEW ROW that names the row it corrects
-- (`corrects`), and both rows stay. The measurement reads the correction and ignores the
-- superseded original; an auditor reads both and can see what changed and why (`correction_reason`
-- is required by a CHECK, not by a convention).
--
-- The one UPDATE this table permits is the stop itself: a RUNNING row (stopped_at IS NULL) may
-- have `stopped_at`, `ended_how` and `note` written once. Everything else on the row is immutable
-- from birth, and after the stop the whole row is. DELETE is refused outright and the runtime role
-- holds no DELETE grant either, which is this schema's standing posture (migration 2, 0003, 0007).
--
-- ========================================================================= THE THREE POLICIES
--
-- Each of these was a real fork and each is enforced here as well as in the verb, because a gate
-- that exists in one place is a gate one bug away from being absent
-- (queue/human_queue/transitions.py:152 makes the same argument about `recommendation`).
--
-- 1. A FORGOTTEN RUNNING TIMER IS `abandoned`, NOT A FOURTEEN-HOUR MEASUREMENT.
--
--    An unbounded entry is the single most likely way this feature produces a confidently wrong
--    number: one closed laptop and the mean minutes-per-item is wrong by two orders of magnitude,
--    in a direction that makes the queue look catastrophically expensive.
--
--    The rule: past `brain.time_entry_cap()` (4 hours) the trigger FORCES `ended_how =
--    'abandoned'`, whatever the caller passed. Abandoned entries stay in the ledger, are never
--    counted in any mean, and are counted in coverage as NOT measured.
--
--    Why forced rather than refused: a refusal would strand the running row forever, because this
--    table has no other way to close one. The stop always lands; what the cap decides is the
--    LABEL, and the label tells the truth about what the row is.
--
--    Why not clamp to four hours and count it: that would be inventing a number. A clamped entry
--    is indistinguishable in the mean from a real four-hour session and there is no evidence
--    anywhere that the operator worked four hours on it. Excluding it loses a data point;
--    clamping it manufactures one.
--
--    Why four hours: the operator's own stated clearing budget is 30-60 minutes a day and
--    `Decide` claims a two-minute ceiling. A manual stopwatch that has run four hours on one
--    queue item is far more likely a closed laptop than a work session. THE COST OF THE BOUND,
--    stated: a genuinely long session cannot be recorded as one entry. Record it as several, or
--    accept the `abandoned` label and the exclusion. That is a real limitation, not a rounding.
--
-- 2. TWO TIMERS AT ONCE ARE REFUSED.
--
--    `time_entry_one_running_per_person` is a partial UNIQUE index on `who` where the entry is
--    still running. The verb refuses first, in words, and names what is already running.
--
--    Why refuse: the queue's entire premise is that CONTEXT SWITCHING IS THE COST, and he works
--    one thing at a time by design. Beyond the doctrine there is an arithmetic reason that is not
--    a matter of taste -- overlapping intervals make the measured minutes exceed the elapsed
--    wall-clock, so "he spent 90 minutes clearing 3 items" could be reported for an hour in which
--    he sat down once. A clearance rate built from that is exactly the confidently-wrong number
--    this table exists to prevent.
--
-- 3. A TIMER IS NEVER REQUIRED.
--
--    Nothing in this schema, and nothing in any verb, obliges an item to be timed. An untimed
--    item is normal and always will be: he will clear things at a keyboard he did not start a
--    timer on, and a system that nagged him for one would collect compliance entries rather than
--    measurements.
--
--    The consequence has to be carried by the READ, not hidden by it: `brain.time_entry_effective`
--    exists so a caller can compute "measured over N of M" rather than averaging what it happens
--    to have and implying it saw everything. `queue depth` prints that coverage next to every
--    figure derived from here, and prints nothing at all for a tier with no measured entries --
--    no fallback to another tier's mean, no global average wearing a per-tier label.
--
-- ========================================================================= WHAT THIS DOES NOT DO
--
-- STATED PLAINLY BECAUSE A GUARD THAT OVERSELLS ITSELF IS WORSE THAN NO GUARD.
--
--   * It cannot tell whether the operator was actually working during a running entry. It records
--     that a stopwatch was running, which is a claim about a stopwatch. The four-hour cap bounds
--     how wrong that claim can get; it does not make it true.
--   * It cannot see time he spent without starting a timer. That is what coverage is for, and
--     coverage is reported rather than assumed away.
--   * OS-level and activity-monitor tracking is deliberately NOT here. It is a separate,
--     larger, privacy-sensitive piece of work. What a later lane needs from this one is written
--     down in `queue/README.md` under "for a future activity-monitor lane".
--   * A restore is not covered and cannot be: `pg_restore` and `session_replication_role =
--     replica` run with triggers disabled by design. That is why the guarantee is repeated as a
--     COMMENT on the table, where a reader of the schema finds it. Migration 14 and 22 make the
--     same point for the same reason.

\set ON_ERROR_STOP on

DO $$
DECLARE taken text;
BEGIN
  SELECT name INTO taken FROM brain.schema_migration WHERE version = 23;
  IF taken IS NOT NULL AND taken <> '0012_operator_time_entry' THEN
    RAISE EXCEPTION 'schema version 23 is already held by %, not 0012_operator_time_entry. '
                    'Renumber by reading brain.schema_migration AND scanning all three schema '
                    'directories (migrations/, budget/schema/, queue/schema/), never by listing '
                    'one of them: every file here writes ON CONFLICT (version) DO NOTHING, so '
                    'the loser of a duplicate applies its DDL and skips its ledger row.',
                    taken;
  END IF;
END $$;

BEGIN;

SET search_path TO brain, public;

-- ------------------------------------------------------------------ the cap, as a function
--
-- A function rather than a literal in five places. The trigger reads it, the views read it, and
-- `queue/human_queue/time_ledger.py` reads it out of the database rather than holding its own
-- copy -- so there is no parity test needed to keep a Python constant and a SQL constant equal,
-- which is the drift `queue/README.md` already warns about for the POSTPONING tuple.

CREATE OR REPLACE FUNCTION brain.time_entry_cap() RETURNS interval
  LANGUAGE sql IMMUTABLE AS $$ SELECT interval '4 hours' $$;

COMMENT ON FUNCTION brain.time_entry_cap() IS
  'Past this, a running timer is a forgotten timer. Enforced by trigger time_entry_append_only, '
  'which FORCES ended_how = ''abandoned'' rather than refusing the stop (a refusal would strand '
  'the row: nothing else can close it) and rather than clamping the duration (a clamp invents a '
  'measurement). Read by Python out of here, so the constant exists once.';

-- ------------------------------------------------------------------ the ledger

CREATE TABLE IF NOT EXISTS brain.time_entry (
  id            bigserial PRIMARY KEY,

  -- The same (source_type, source_id) pair every other table in this lane keys on. No foreign
  -- key, for the same reason `brain.queue_item` has none: `source_id` addresses three different
  -- tables with two different key types, and a question id is text while a recommendation id is
  -- a bigint rendered as text. The verb resolves the row and refuses an id that does not exist,
  -- which is where that check belongs.
  source_type   text NOT NULL CHECK (source_type IN ('work_item','question','recommendation')),
  source_id     text NOT NULL CHECK (btrim(source_id) <> ''),

  -- WHOSE time. Not `actor_type`: this table is the operator's stopwatch and an agent has no
  -- business in it, but the column is a name rather than a boolean because a second human (a
  -- collaborator, a client on a call) is a thing that will happen and a schema that assumed one
  -- person would have to be migrated to admit them. The one-running-timer rule is per person for
  -- the same reason.
  who           text NOT NULL CHECK (btrim(who) <> ''),

  started_at    timestamptz NOT NULL DEFAULT now(),
  stopped_at    timestamptz,

  -- How it ended. NULL exactly while it is running.
  --   stopped    a real measured interval. The only kind any mean counts.
  --   abandoned  forgotten, or over the cap. In the ledger, out of every mean, counted in
  --              coverage as NOT measured.
  ended_how     text CHECK (ended_how IS NULL OR ended_how IN ('stopped','abandoned')),

  -- THE TIER AS IT WAS WHEN THE STOPWATCH STARTED, snapshotted on purpose. `queue demote` moves
  -- an item's tier and does it precisely on the items whose duration is most interesting -- the
  -- `not fast` tap. Reading the tier live at measurement time would attribute a Decide item's
  -- three minutes to Judge, which is the tier it was moved to BECAUSE it took three minutes, and
  -- the Decide ceiling would then measure clean forever. Same argument as
  -- `brain.queue_default_event.null_branch`: record the verdict at the time, not today's rule
  -- about yesterday's row.
  tier_at_start text CHECK (tier_at_start IS NULL OR tier_at_start IN ('decide','judge','shape')),

  note          text NOT NULL DEFAULT '',

  -- The correction chain. A stopped entry is never edited; it is superseded by a new row that
  -- names it here. Both rows survive.
  corrects          bigint REFERENCES brain.time_entry(id),
  correction_reason text NOT NULL DEFAULT '',

  created_at    timestamptz NOT NULL DEFAULT now(),

  -- The duration, computed and stored, so no reader can disagree about it. Immutable expression,
  -- so Postgres accepts it as GENERATED: timestamptz subtraction and extract-from-interval are
  -- both immutable. NULL while running, which is the honest answer -- a running entry has no
  -- duration, it has an elapsed, and elapsed is a read.
  seconds       numeric GENERATED ALWAYS AS
                  (EXTRACT(EPOCH FROM (stopped_at - started_at))) STORED,

  -- Both halves of an ending, or neither. Migration 22's COHERENT rule, and it exists for the
  -- same reason: half an ending leaves every reader to pick which column to believe, and the
  -- measurement would pick differently from the console.
  CONSTRAINT time_entry_ending_coherent CHECK ((stopped_at IS NULL) = (ended_how IS NULL)),
  CONSTRAINT time_entry_not_backwards   CHECK (stopped_at IS NULL OR stopped_at >= started_at),
  -- A correction with no reason teaches nobody, and this is the one row shape whose whole job is
  -- to explain a disagreement with an earlier record. Same rule `queue bump` holds for its reason.
  CONSTRAINT time_entry_correction_reasoned
    CHECK (corrects IS NULL OR btrim(correction_reason) <> ''),
  CONSTRAINT time_entry_no_self_correction CHECK (corrects IS NULL OR corrects <> id)
);

COMMENT ON TABLE brain.time_entry IS
  'The operator''s stopwatch: one row per timed interval on one queue item. APPEND-ONLY. A '
  'stopped entry is never edited -- trigger time_entry_append_only refuses UPDATE on it and '
  'refuses DELETE on any row -- and a correction is a NEW row naming the one it corrects, so '
  'both survive and an auditor can see what changed and why. The single UPDATE permitted is the '
  'stop of a running row. Past brain.time_entry_cap() a stop is forced to ended_how=''abandoned'' '
  'and leaves every mean: a forgotten timer must not be able to poison the clearance figure, and '
  'clamping it to the cap would invent a measurement rather than lose one. A timer is NEVER '
  'required, so every figure derived from this table reports its own coverage (measured over N '
  'of M) rather than averaging what it has and implying it saw everything. A trigger cannot '
  'cover a restore, which runs with triggers disabled, so treat ANY code path that UPDATEs a '
  'stopped row as a defect regardless of what the table lets through.';

COMMENT ON COLUMN brain.time_entry.tier_at_start IS
  'The tier the item sat in when the stopwatch started, snapshotted. `queue demote` moves the '
  'tier of exactly the items whose duration matters most (the `not fast` tap), so reading the '
  'tier live would attribute a Decide item''s overrun to Judge and the Decide ceiling would '
  'measure clean forever.';

COMMENT ON COLUMN brain.time_entry.ended_how IS
  '`stopped` is a measured interval and the only kind any mean counts. `abandoned` is a forgotten '
  'timer -- explicitly declared, or past brain.time_entry_cap() and forced by the trigger. It '
  'stays in the ledger, leaves every mean, and counts in coverage as NOT measured.';

COMMENT ON COLUMN brain.time_entry.corrects IS
  'The entry this row supersedes. A stopped entry is never edited (migration 22''s rule: a record '
  'you can silently rewrite is not a record), so a correction is a new row and both survive. '
  'brain.time_entry_effective drops the superseded original from the measurement; a reader of the '
  'table still sees it and correction_reason says why it was wrong.';

-- ------------------------------------------------------------------ one timer at a time

CREATE UNIQUE INDEX IF NOT EXISTS time_entry_one_running_per_person
  ON brain.time_entry (who) WHERE stopped_at IS NULL;

COMMENT ON INDEX brain.time_entry_one_running_per_person IS
  'Two timers at once are refused. The queue''s premise is that context switching IS the cost, '
  'and beyond the doctrine, overlapping intervals let measured minutes exceed elapsed '
  'wall-clock -- a clearance rate built from that is arithmetically wrong, not merely untidy. '
  'The verb refuses first and names what is running; this refuses the psql prompt.';

-- One correction per entry. A chain is fine (a correction may itself be corrected) but two rows
-- correcting the SAME row make "which one supersedes it" a question with two answers, and
-- `time_entry_effective` would have to pick one silently.
CREATE UNIQUE INDEX IF NOT EXISTS time_entry_corrected_once
  ON brain.time_entry (corrects) WHERE corrects IS NOT NULL;

CREATE INDEX IF NOT EXISTS time_entry_source_idx
  ON brain.time_entry (source_type, source_id, started_at);
CREATE INDEX IF NOT EXISTS time_entry_window_idx
  ON brain.time_entry (stopped_at) WHERE stopped_at IS NOT NULL;

-- ------------------------------------------------------------------ the append-only guard

CREATE OR REPLACE FUNCTION brain.time_entry_append_only() RETURNS trigger
  LANGUAGE plpgsql AS $$
DECLARE
  target brain.time_entry%ROWTYPE;
  HINT_WHY constant text :=
    'brain.time_entry is append-only because it is a MEASUREMENT INPUT: the moment a stopped '
    'interval can be edited, every figure computed from it has a provenance nobody can '
    'reconstruct, and those figures are what the operator will use to decide whether this queue '
    'layer is worth his morning. Correct it by inserting a new entry with `corrects` set and a '
    'reason -- `queue time correct` -- which leaves both rows. Same rule migration 22 holds for '
    'brain.work_item.accepted_by.';
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'refusing to delete time entry % (% % , % -> %)',
                    OLD.id, OLD.source_type, OLD.source_id, OLD.started_at,
                    COALESCE(OLD.stopped_at::text, 'running')
      USING HINT = 'Nothing deletes from this ledger. A wrong entry is superseded, not removed. '
                   || HINT_WHY;
  END IF;

  IF TG_OP = 'INSERT' THEN
    IF NEW.started_at > now() + interval '1 minute' THEN
      RAISE EXCEPTION 'refusing a time entry that starts in the future (started_at %)',
                      NEW.started_at
        USING HINT = 'A stopwatch measures the past. The minute of slack is for clock skew '
                     'between a client and the database, not for scheduling.';
    END IF;

    IF NEW.corrects IS NULL THEN
      -- AN ORDINARY ENTRY IS BORN RUNNING. A hand-written row that arrives already stopped is
      -- exactly the silently-invented interval this table exists to prevent: nobody ran a
      -- stopwatch, and no reader could tell it from one that did. If the interval is real and
      -- was missed, it is a correction and it names what it corrects.
      IF NEW.stopped_at IS NOT NULL THEN
        RAISE EXCEPTION 'refusing a time entry born stopped (% %, % -> %)',
                        NEW.source_type, NEW.source_id, NEW.started_at, NEW.stopped_at
          USING HINT = 'Start it with `queue time start` and stop it with `queue time stop`. An '
                       'interval nobody actually timed is indistinguishable in the mean from one '
                       'somebody did, which is how a measured number becomes a made-up one. If '
                       'you are restating an interval that WAS timed, set `corrects` and a '
                       'reason. ' || HINT_WHY;
      END IF;
      RETURN NEW;
    END IF;

    -- A CORRECTION. Born stopped, because it restates a finished interval, and the row it
    -- corrects must exist and be finished too -- correcting a running timer is not a correction,
    -- it is a stop.
    IF NEW.stopped_at IS NULL THEN
      RAISE EXCEPTION 'a correction of entry % must state the interval it is correcting to',
                      NEW.corrects
        USING HINT = 'A correction restates a finished interval, so it is born with both ends. '
                     || HINT_WHY;
    END IF;
    SELECT * INTO target FROM brain.time_entry WHERE id = NEW.corrects;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'no time entry % to correct', NEW.corrects;
    END IF;
    IF target.stopped_at IS NULL THEN
      RAISE EXCEPTION 'time entry % is still running, so it is not wrong yet', NEW.corrects
        USING HINT = 'Stop it first. `queue time stop` closes a running entry; a correction '
                     'supersedes a closed one.';
    END IF;
    IF NEW.source_type <> target.source_type OR NEW.source_id <> target.source_id THEN
      RAISE EXCEPTION 'a correction of % must be about the same item (% % , not % %)',
                      NEW.corrects, target.source_type, target.source_id,
                      NEW.source_type, NEW.source_id
        USING HINT = 'Correcting the item as well as the interval would silently move measured '
                     'minutes between two items and two tiers. Abandon the wrong entry and start '
                     'a new one on the right item instead.';
    END IF;

  ELSE  -- UPDATE
    -- ------------------------------------------------------------ a stopped entry is frozen
    IF OLD.stopped_at IS NOT NULL THEN
      RAISE EXCEPTION 'refusing to rewrite stopped time entry %: % % , % -> % (% , %s)',
                      OLD.id, OLD.source_type, OLD.source_id, OLD.started_at, OLD.stopped_at,
                      OLD.ended_how, OLD.seconds
        USING HINT = 'A stopped entry is never edited. ' || HINT_WHY;
    END IF;

    -- ------------------------------------------------------------ a running entry may only stop
    IF NEW.stopped_at IS NULL THEN
      RAISE EXCEPTION 'the only update a running time entry accepts is its stop (entry %)', OLD.id
        USING HINT = 'Nothing else on a running row may move: it is the row an auditor uses to '
                     'tell a real interval from a manufactured one. ' || HINT_WHY;
    END IF;
    -- IS DISTINCT FROM throughout, never `<>`: `<>` on a nullable column yields NULL, the IF
    -- takes the false branch, and the guard silently passes exactly the write it was added for.
    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.source_type IS DISTINCT FROM OLD.source_type
       OR NEW.source_id IS DISTINCT FROM OLD.source_id
       OR NEW.who IS DISTINCT FROM OLD.who
       OR NEW.started_at IS DISTINCT FROM OLD.started_at
       OR NEW.corrects IS DISTINCT FROM OLD.corrects
       OR NEW.correction_reason IS DISTINCT FROM OLD.correction_reason
       OR NEW.tier_at_start IS DISTINCT FROM OLD.tier_at_start
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
      RAISE EXCEPTION 'a stop may write stopped_at, ended_how and note on entry % and nothing '
                      'else', OLD.id
        USING HINT = 'Moving started_at, who, the item or the tier snapshot after the fact is '
                     'editing the measurement, not ending it. ' || HINT_WHY;
    END IF;
  END IF;

  -- ------------------------------------------------------------ THE CAP, on every ending
  --
  -- INSERT of a correction and UPDATE that stops a running row both land here, and both are
  -- subject to it: a correction claiming six hours is no more a stopwatch reading than a
  -- forgotten timer is. FORCED rather than refused, because refusing the UPDATE would strand the
  -- running row forever -- nothing else in this schema can close one.
  IF NEW.stopped_at IS NOT NULL
     AND NEW.stopped_at - NEW.started_at > brain.time_entry_cap()
     AND NEW.ended_how IS DISTINCT FROM 'abandoned' THEN
    RAISE NOTICE 'time entry % ran % (cap %), so it is recorded as abandoned and left out of '
                 'every mean. A forgotten timer must not be able to poison the clearance figure, '
                 'and clamping it to the cap would invent a measurement rather than lose one.',
                 COALESCE(NEW.id, 0), NEW.stopped_at - NEW.started_at, brain.time_entry_cap();
    NEW.ended_how := 'abandoned';
  END IF;

  RETURN NEW;
END $$;

COMMENT ON FUNCTION brain.time_entry_append_only() IS
  'The gate on the operator''s stopwatch. Refuses DELETE on any row, refuses UPDATE on a stopped '
  'row, permits exactly one UPDATE on a running row (its stop, writing stopped_at, ended_how and '
  'note and nothing else), refuses an ordinary entry born stopped, and forces ended_how = '
  '''abandoned'' past brain.time_entry_cap(). The verbs in queue/human_queue/time_ledger.py '
  'refuse first and in words; this refuses a psql prompt. It cannot cover a restore, which runs '
  'with triggers disabled.';

DROP TRIGGER IF EXISTS time_entry_append_only ON brain.time_entry;
CREATE TRIGGER time_entry_append_only
  BEFORE INSERT OR UPDATE OR DELETE ON brain.time_entry
  FOR EACH ROW
  EXECUTE FUNCTION brain.time_entry_append_only();

-- ------------------------------------------------------------------ what the measurement reads
--
-- TWO VIEWS AND NOT ONE, because "every entry, annotated" and "the entries a mean may count" are
-- two questions and serving both off one row set is the bug `reads._defers` documents at length:
-- conflating suppression with counting was wrong in both directions.

CREATE OR REPLACE VIEW brain.time_entry_annotated AS
  SELECT e.*,
         (e.stopped_at IS NULL)                                        AS running,
         EXISTS (SELECT 1 FROM brain.time_entry c WHERE c.corrects = e.id) AS superseded,
         (e.stopped_at IS NULL AND now() - e.started_at > brain.time_entry_cap())
                                                                       AS over_cap,
         COALESCE(e.seconds, EXTRACT(EPOCH FROM (now() - e.started_at))) AS elapsed_seconds
    FROM brain.time_entry e;

COMMENT ON VIEW brain.time_entry_annotated IS
  'Every entry, with the three facts a surface needs and must not re-derive: whether it is still '
  'running, whether a correction supersedes it, and whether a RUNNING one is already past the '
  'cap (which is what `queue time status` shows the operator before the stop forces the '
  'abandoned label on it).';

CREATE OR REPLACE VIEW brain.time_entry_effective AS
  SELECT * FROM brain.time_entry_annotated
   WHERE ended_how = 'stopped' AND NOT superseded;

COMMENT ON VIEW brain.time_entry_effective IS
  'The entries a mean may count: stopped, not abandoned, not superseded by a correction. This is '
  'the ONLY row set any number derived from the stopwatch is computed over, and the difference '
  'between its count and the count of items cleared in the same window is the coverage every '
  'such number has to report. Averaging over this set while implying it saw everything is the '
  'exact failure this lane treats as a finding.';

-- ------------------------------------------------------------------ grants
--
-- New tables inherit no grants: migration 2 grants by explicit table list. DELETE to nobody, the
-- standing posture of this schema -- and here it is a second lock on the append-only rule, since
-- the trigger cannot run during a restore but a missing grant still stops an ordinary session.

GRANT SELECT, INSERT, UPDATE ON brain.time_entry TO brain_runtime;
GRANT USAGE, SELECT ON brain.time_entry_id_seq TO brain_runtime;
GRANT SELECT ON brain.time_entry_annotated, brain.time_entry_effective
  TO brain_runtime, brain_subscriber;
GRANT EXECUTE ON FUNCTION brain.time_entry_cap() TO brain_runtime, brain_subscriber;

-- The operator's own login (migration 20) reads and writes his own stopwatch. He is the only
-- human this table is about, and `queue time start` runs as `runtime` like every other queue verb
-- -- these grants exist so that a console running under his credential is not refused a read of
-- his own minutes.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'brain_operator') THEN
    EXECUTE 'GRANT SELECT, INSERT, UPDATE ON brain.time_entry TO brain_operator';
    EXECUTE 'GRANT USAGE, SELECT ON brain.time_entry_id_seq TO brain_operator';
    EXECUTE 'GRANT SELECT ON brain.time_entry_annotated, brain.time_entry_effective '
            'TO brain_operator';
    EXECUTE 'GRANT EXECUTE ON FUNCTION brain.time_entry_cap() TO brain_operator';
  END IF;
END $$;

INSERT INTO brain.schema_migration (version, name)
VALUES (23, '0012_operator_time_entry') ON CONFLICT (version) DO NOTHING;

COMMIT;
