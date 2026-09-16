-- migration 34: routines. The recurrence that CREATES work, as an object the operator can list,
-- disable and audit, with the double fire made impossible by a unique index rather than unlikely
-- by a Python check.
--
-- Lane C, bus row 0376, 2026-08-27. `PLAN.md:255` retires the incumbent board once budgets AND
-- routines land natively. Budgets landed as migration 3. This is the other half, and it has been
-- "in no lane" for over a year because the same plan leaves routines on systemd timers, which
-- made the condition unsatisfiable as literally written.
--
-- THIS MIGRATION IS THE READING THAT MAKES IT SATISFIABLE WITHOUT REINTERPRETING IT: the ROUTINE
-- is an object in the store, so it can be listed, disabled, and shown to have fired; the CLOCK
-- stays a systemd --user timer, exactly where the plan puts it. Nothing here is a scheduler
-- process. There is no loop, no daemon and no second notion of what time it is.
--
-- ================================================================= LEDGER 34, NOT 32
--
-- The commander's lane assignment reads "highest existing migration is 0031" and gives lane C
-- 0032 to 0035. That is the highest FILENAME in `migrations/`. The LEDGER is global across three
-- schema directories and its highest applied version on live `brain` is 33:
--
--   32  queue/schema/0015_recommendation_human_login.sql      applied 2026-08-19T03:44:45Z
--   33  queue/schema/0016_recommendation_rejection_login.sql  applied 2026-08-19T03:44:45Z
--
-- `engine/bin/scratch-db.sh` reads the recorded version, never the filename, and refuses two
-- files claiming one number: "Every file writes ON CONFLICT (version) DO NOTHING, so the loser
-- would apply nothing and still report success." A file claiming 32 would therefore have applied
-- NOTHING against live `brain` and reported a clean build. So this file takes 34, stays inside
-- lane C's assigned numeric band, and leaves 35 spare. Raised in crosstalk for lanes E and F,
-- whose bands (36 to 39, 40 to 43) are free in the ledger and need no change.
--
-- ================================================================= WHY NOT brain.queue_defer
--
-- Full reasoning in `outputs/2026-08-27-C-0376-routines/OBJECT-DECISION.md`. The short form is
-- that `queue_defer` answers "when does THIS EXISTING item come back", keyed to a source row that
-- already exists, one shot, and its third occurrence is REFUSED on purpose ("three deferrals
-- means mis-scoped, not mis-timed"). A routine answers "when does a NEW item get created", is
-- keyed to nothing, is standing, and is unbounded. No row in the world could be described by both
-- tables, so there is no fact with two homes and no second source of truth.
--
-- ================================================================= THE ONE INSTRUMENT THAT WORKS
--
-- Row 0377 fixed `recommend accept` with SELECT ... FOR UPDATE, and that instrument is WRONG
-- here. `FOR UPDATE` locks a row that exists; a recommendation exists before anyone accepts it,
-- so the losers queue on it, wake, re-read and are refused. A routine occurrence DOES NOT EXIST
-- until the first fire creates it, so there is nothing to lock and eight callers all find no row
-- for slot k and all proceed.
--
-- The only instrument that refuses a row that is not there yet is a UNIQUE INDEX. The index entry
-- is taken at INSERT time, the second inserter blocks on the first inserter's uncommitted entry,
-- and gets 23505 unique_violation when the first commits. That is `routine_run_one_per_slot`
-- below.
--
-- ORDER INSIDE THE TRANSITION: THE POST FIRST, THE OCCURRENCE ROW SECOND, and this is the one
-- design note most likely to be read as backwards. `store.apply` runs the whole transition in ONE
-- transaction, so the loser's 23505 aborts its post as well as its occurrence row: nothing it
-- wrote commits and there is no work item to orphan. The property being claimed is therefore not
-- "a duplicate post never executes", it is the stronger and checkable one, NO TRANSACTION THAT
-- POSTS A DUPLICATE CAN COMMIT.
--
-- Claiming the slot first is impossible without making the audit lie: `routine_run_fired_has_work`
-- requires the work item id, Postgres CHECK constraints CANNOT be deferred (only UNIQUE, PRIMARY
-- KEY, EXCLUDE and FOREIGN KEY can), so a claim-first row would have to be written with a false
-- outcome and corrected by an UPDATE, on a table this file deliberately grants no UPDATE on.
--
-- Post-first also has a positive argument, not only an absence of objections. When `post` itself
-- REFUSES -- a bad workdir, a parent that no longer resolves -- the slot row is never written, so
-- the slot stays unconsumed and the next tick tries again and refuses again, out loud. Claim-first
-- would consume the slot and the routine would fail SILENTLY until its next period. A routine that
-- is broken should say so every tick until a hand disables it.
--
-- The measured cost is a gap in `brain.item_id_seq` per loser, which is what any rolled-back post
-- already costs and is invisible to every reader.
--
-- Measured on `brain_lane_c`, 8 concurrent processes on 8 connections, one routine, one slot:
--   WITHOUT the index   8 fired, 8 work items, 7 orphans   (the index dropped, as a control arm)
--   WITH the index      1 fired, 1 work item, 7 refused by Postgres with SQLSTATE 23505
-- `engine/tests/test_routines.py` scenes 3 and 4 are that measurement and quote both arms.
--
-- ================================================================= CADENCE IS TWO COLUMNS
--
-- `period_minutes` and `anchor_at`. Slot k is anchor_at + k * period_minutes, and `scheduled_for`
-- is the largest slot at or before now. Daily is 1440; weekly is 10080; the two checkpoints this
-- system already has, 07:00 and 19:00, are 720 anchored at 07:00, which is the test of whether
-- the model can express the one routine that already exists. It can.
--
-- MONTHLY IS NOT EXPRESSIBLE AND THIS SAYS SO rather than approximating it. Calendar months are
-- not a fixed number of minutes and a period that pretended otherwise would drift by three days a
-- year. A monthly routine is a real request and it is a second migration with a different column.
--
-- Everything is UTC, which is what this store is in, so no daylight-saving arithmetic exists
-- anywhere in this design.
--
-- ================================================================= NO produced_by, DELIBERATELY
--
-- Neither table carries `produced_by`, so the `brain_lineage_columns` event trigger correctly
-- does nothing to them and `brain.lineage_drift` correctly ignores them. A routine is written by
-- a hand at a console; nothing resolved it out of the brain's entity graph. Adding the triple
-- would mean writing `resolution_status = NULL` forever, which reads as "no attempt was made" and
-- is true, but it would also put four constraints on a table whose producer question was never
-- asked. The work item a routine POSTS still carries whatever lineage `post` is given.
--
-- ================================================================= ROLLBACK
--
-- Additive. Two tables, one function, two views, no ALTER of anything that already exists, no
-- backfill, no trigger on an existing table. Nothing in this file changes the behaviour of a
-- single existing verb. To undo it completely:
--
--   BEGIN;
--   DROP VIEW IF EXISTS brain.routine_due;
--   DROP VIEW IF EXISTS brain.routine_armed;
--   DROP TABLE IF EXISTS brain.routine_run;
--   DROP TABLE IF EXISTS brain.routine;
--   DROP FUNCTION IF EXISTS brain.routine_slot(timestamptz, integer, timestamptz);
--   DELETE FROM brain.schema_migration WHERE version = 34;
--   COMMIT;
--
-- and the store is byte-identical to its pre-34 shape apart from sequence oids. The DELETE is
-- safe here and is NOT safe in general: it is only correct because no later migration depends on
-- 34. Verified on `brain_lane_c` before this file was proposed for `brain`.

\set ON_ERROR_STOP on

BEGIN;

SET search_path TO brain, public;

-- ---------------------------------------------------------------- the slot arithmetic
--
-- One formula, in the database, so the verb, the view and any surface that wants to preview a
-- routine all get the same answer. Two callers at the same instant compute the same slot, which
-- is what makes the uniqueness constraint below the WHOLE of the idempotency story rather than
-- half of it.
--
-- IMMUTABLE is correct and load bearing: `extract(epoch FROM (a - b))` on two timestamptz values
-- goes through interval subtraction, which does not consult the session TimeZone. The STABLE
-- spelling `extract(epoch FROM some_timestamptz)` does, and using it here would have made this
-- function unusable in an index expression later and would have been a lie about its purity.

CREATE OR REPLACE FUNCTION brain.routine_slot(anchor_at timestamptz, period_minutes integer,
                                              at_time timestamptz)
  RETURNS timestamptz LANGUAGE sql IMMUTABLE STRICT AS $$
  SELECT anchor_at
       + make_interval(mins => period_minutes)
         * floor(extract(epoch FROM (at_time - anchor_at))
                 / (period_minutes::double precision * 60))
$$;

COMMENT ON FUNCTION brain.routine_slot(timestamptz, integer, timestamptz) IS
  'The largest scheduled slot at or before at_time, for a routine anchored at anchor_at with a '
  'period of period_minutes. Pure arithmetic on two stored columns: nothing here decides what '
  'time it is, so there is no second clock. Below anchor_at it returns a slot EARLIER than the '
  'anchor, which brain.routine_due excludes, so a routine cannot fire before it exists.';

-- ---------------------------------------------------------------- the definition

CREATE TABLE IF NOT EXISTS brain.routine (
  id               bigserial PRIMARY KEY,

  -- The operator's handle for it, and the argument to the kill switch. Lowercase and
  -- hyphenated so `swarm routine disable morning-brief` needs no quoting at 07:00.
  name             text NOT NULL UNIQUE
                     CHECK (name ~ '^[a-z0-9][a-z0-9-]{0,62}$'),

  -- What it posts. These are `post`'s arguments, stored, because a routine IS a stored call to
  -- `post` on a clock. They are not a new dispatch vocabulary.
  title            text NOT NULL CHECK (btrim(title) <> ''),
  lane             text NOT NULL CHECK (btrim(lane) <> ''),
  body             text NOT NULL DEFAULT '',
  workdir          text NOT NULL DEFAULT '',
  parent           text REFERENCES brain.work_item(id),
  priority         integer NOT NULL DEFAULT 3,
  max_attempts     integer NOT NULL DEFAULT 2 CHECK (max_attempts >= 1),
  agent_claimable  boolean NOT NULL DEFAULT false,

  -- The two hard flags, declared HERE because a routine may have no parent to inherit from. When
  -- `parent` IS set, `post` ORs the whole chain on top of these and can only raise them.
  external         boolean NOT NULL DEFAULT false,
  canon_touching   boolean NOT NULL DEFAULT false,

  -- cadence
  period_minutes   integer NOT NULL CHECK (period_minutes >= 1),
  anchor_at        timestamptz NOT NULL,

  -- NULL means "the period", which means the current slot always fires. An operator who wants
  -- "only within 30 minutes of 07:00" sets 30, and the slots that miss start appearing in
  -- routine_run as `missed` instead of vanishing into nobody's memory.
  misfire_grace_minutes integer CHECK (misfire_grace_minutes IS NULL
                                       OR misfire_grace_minutes >= 1),

  -- A daily routine whose task is never closed is thirty open rows in a month. Default true.
  skip_if_open     boolean NOT NULL DEFAULT true,

  created_at       timestamptz NOT NULL DEFAULT now(),
  created_by       text NOT NULL DEFAULT '',

  -- THE KILL SWITCH, as data. An UPDATE and never a DELETE, for the reason `resume` gives about
  -- the PAUSE file: brain_runtime holds DELETE on nothing, and a row that says who turned it off
  -- and why is a better record than a row that is gone.
  disabled_at      timestamptz,
  disabled_by      text,
  disabled_reason  text,

  -- `post` REFUSES an agent-claimable task with no absolute workdir (task 0100), and a routine
  -- fires with no human in the loop, so that refusal would land at 03:20 in a journal nobody
  -- reads. The gate moves to WRITE time, where a human is awake to read it. This is the
  -- constraint that stops an unbuilt routine becoming a fabricated pointer.
  CONSTRAINT routine_fleet_work_has_an_absolute_workdir
    CHECK (NOT agent_claimable OR workdir ~ '^(/|~)'),

  -- A disablement is three facts or none. A `disabled_at` with no reason is a kill switch nobody
  -- can audit, and "who turned this off and why" is the only question anyone asks about one.
  CONSTRAINT routine_disabled_is_whole
    CHECK ((disabled_at IS NULL AND disabled_by IS NULL AND disabled_reason IS NULL)
        OR (disabled_at IS NOT NULL
            AND btrim(coalesce(disabled_by, '')) <> ''
            AND btrim(coalesce(disabled_reason, '')) <> ''))
);

CREATE INDEX IF NOT EXISTS routine_armed_idx ON brain.routine (name) WHERE disabled_at IS NULL;

COMMENT ON TABLE brain.routine IS
  'A stored call to `post`, on a clock. One row per routine. The clock itself is a systemd --user '
  'timer calling `swarm routine tick --fire`; nothing in this schema decides what time it is. '
  'Disabling a routine is one UPDATE on this row and it is the per-routine kill switch PLAN.md '
  'names as a retirement condition.';
COMMENT ON COLUMN brain.routine.anchor_at IS
  'Slot zero. Every occurrence is anchor_at + k * period_minutes, so 07:00 and 19:00 daily is '
  'period 720 anchored at 07:00. A routine cannot fire before its anchor.';
COMMENT ON COLUMN brain.routine.skip_if_open IS
  'When the routine''s last posted work item is still inbox, active or blocked, the slot is '
  'recorded as `skipped` rather than posted. The occurrence row is still written, so the slot is '
  'consumed and the audit shows the routine ran and chose not to post.';
COMMENT ON COLUMN brain.routine.disabled_reason IS
  'NOT NULL whenever disabled_at is, by routine_disabled_is_whole. A kill switch with no reason '
  'is a switch nobody can audit six weeks later.';

-- ---------------------------------------------------------------- the occurrence

CREATE TABLE IF NOT EXISTS brain.routine_run (
  id             bigserial PRIMARY KEY,
  routine_id     bigint NOT NULL REFERENCES brain.routine(id),
  scheduled_for  timestamptz NOT NULL,
  fired_at       timestamptz NOT NULL DEFAULT now(),
  fired_by       text NOT NULL DEFAULT '',
  outcome        text NOT NULL CHECK (outcome IN ('fired','skipped','missed')),
  work_item_id   text REFERENCES brain.work_item(id),
  note           text NOT NULL DEFAULT '',

  -- ============================================================== THE WHOLE POINT OF THIS FILE
  --
  -- A double fire is impossible IN THE DATABASE, not unlikely in the caller. The lesson of row
  -- 0377 is that a Python check the database does not back holds only for polite callers: eight
  -- concurrent callers, one proposal, EIGHT work items and SEVEN orphans, on a verb whose own
  -- refusal said "accepting twice would spawn the work twice".
  --
  -- The transition posts the work and then writes THIS ROW, in ONE transaction. Postgres aborts
  -- the whole transaction on a unique violation, so the loser's post is rolled back with it and
  -- there is no work item to orphan. See the header for why this order and not the other one.
  CONSTRAINT routine_run_one_per_slot UNIQUE (routine_id, scheduled_for),

  -- An outcome and its evidence agree, in both directions. `fired` with no work item would be a
  -- routine claiming it posted something that does not exist, which is the fabricated-pointer
  -- shape this row was filed against; `skipped` WITH a work item would be a post the audit says
  -- did not happen.
  CONSTRAINT routine_run_fired_has_work
    CHECK (outcome <> 'fired' OR work_item_id IS NOT NULL),
  CONSTRAINT routine_run_unfired_has_no_work
    CHECK (outcome = 'fired' OR work_item_id IS NULL),
  CONSTRAINT routine_run_unfired_says_why
    CHECK (outcome = 'fired' OR btrim(note) <> '')
);

CREATE INDEX IF NOT EXISTS routine_run_posted_idx
  ON brain.routine_run (routine_id, fired_at DESC) WHERE work_item_id IS NOT NULL;

COMMENT ON TABLE brain.routine_run IS
  'One row per SCHEDULED SLOT, not per attempt. routine_run_one_per_slot is what makes a double '
  'fire impossible: the index entry is taken at INSERT, so a second caller for the same slot '
  'blocks and then gets 23505 rather than passing a check nothing was holding. A slot that was '
  'deliberately not posted is still a row here, with an outcome and a reason.';
COMMENT ON COLUMN brain.routine_run.outcome IS
  'fired: a work item exists and work_item_id names it. skipped: the previous occurrence was '
  'still open and skip_if_open is set. missed: the slot was older than misfire_grace_minutes '
  'when the tick found it, which is the machine having been off. All three consume the slot.';

-- ---------------------------------------------------------------- what is armed, and what is due
--
-- TWO VIEWS AND NOT ONE, because "disabled" and "already fired this slot" are different facts and
-- an operator debugging a silent routine needs to know which one it is. `routine_armed` answers
-- "would this routine ever fire"; `routine_due` answers "is it eligible right now".

CREATE OR REPLACE VIEW brain.routine_armed AS
  SELECT r.*,
         brain.routine_slot(r.anchor_at, r.period_minutes, now()) AS current_slot
    FROM brain.routine r
   WHERE r.disabled_at IS NULL;

COMMENT ON VIEW brain.routine_armed IS
  'Every routine the kill switch has NOT been thrown on, with the slot it is currently in. Gate '
  'one of two: `routine fire` refuses a disabled routine by name as well, so a caller naming the '
  'routine directly cannot go around this view.';

CREATE OR REPLACE VIEW brain.routine_due AS
  SELECT a.*,
         (now() - a.current_slot)
           > make_interval(mins => coalesce(a.misfire_grace_minutes, a.period_minutes))
             AS past_grace
    FROM brain.routine_armed a
   -- A routine cannot fire before its anchor: below anchor_at the slot function returns a slot
   -- EARLIER than the anchor, and this is where that is refused rather than fired.
   WHERE a.current_slot >= a.anchor_at
     AND NOT EXISTS (SELECT 1 FROM brain.routine_run rr
                      WHERE rr.routine_id = a.id AND rr.scheduled_for = a.current_slot);

COMMENT ON VIEW brain.routine_due IS
  'Armed routines whose current slot has no routine_run row yet. `past_grace` true means the tick '
  'arrived too late and the slot is recorded as `missed` rather than fired, which is the machine '
  'having been off. A READ: firing is a separate call, so a console can show what is about to '
  'happen and a dry run costs nothing. Same shape as queue/human_queue/checkpoints.py::due.';

-- ---------------------------------------------------------------- grants
--
-- No DELETE to anyone but the owner, the rule migrations 25 and 29 state: the row is the evidence
-- that a slot was decided, and evidence a caller can silently empty is not evidence. UPDATE on
-- `routine` is the kill switch; there is deliberately NO UPDATE on `routine_run`, because an
-- occurrence is written once and an outcome that can be edited afterwards is not an audit.

GRANT SELECT, INSERT, UPDATE ON brain.routine      TO brain_runtime;
GRANT SELECT, INSERT         ON brain.routine_run  TO brain_runtime;
GRANT USAGE, SELECT ON brain.routine_id_seq        TO brain_runtime;
GRANT USAGE, SELECT ON brain.routine_run_id_seq    TO brain_runtime;
GRANT SELECT ON brain.routine_armed, brain.routine_due TO brain_runtime;
GRANT SELECT ON brain.routine, brain.routine_run       TO brain_subscriber;
GRANT SELECT ON brain.routine_armed, brain.routine_due TO brain_subscriber;

-- Conditional for the reason migrations 23, 25 and 29 are: `brain_operator` exists only on a host
-- where store/bin/provision-operator.sh has run.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'brain_operator') THEN
    EXECUTE 'GRANT SELECT, INSERT, UPDATE ON brain.routine TO brain_operator';
    EXECUTE 'GRANT SELECT, INSERT ON brain.routine_run TO brain_operator';
    EXECUTE 'GRANT USAGE, SELECT ON brain.routine_id_seq TO brain_operator';
    EXECUTE 'GRANT USAGE, SELECT ON brain.routine_run_id_seq TO brain_operator';
    EXECUTE 'GRANT SELECT ON brain.routine_armed, brain.routine_due TO brain_operator';
  END IF;
END $$;

INSERT INTO brain.schema_migration (version, name)
VALUES (34, '0034_routine') ON CONFLICT (version) DO NOTHING;

COMMIT;
