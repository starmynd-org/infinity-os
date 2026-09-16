-- migration 36: the acceptance on brain.work_item stops being a string and becomes a login
--
-- LEDGER VERSION 36. Picked by reading brain.schema_migration on live `brain` (33 rows, max 33)
-- and on scratch `brain_lane_e` (37 rows, max 41, with lanes C and F's unmerged work in it), and
-- never by listing this directory, per the guard in
-- queue/schema/0015_recommendation_human_login.sql:104.
--
-- ================================================================= what this closes, measured
--
-- Migration 22 wrote the answer to this in its own header and then filed it as somebody else's
-- job: "The rule that actually ends this threat model is an operator-only login ... posted as
-- swarm task 0275 ... and 0275 IS NOW CANCELLED, not done ... this paragraph is the only
-- remaining pointer to it."  This file is that pointer being followed.
--
-- MEASURED on scratch `brain_lane_e` at ledger 41 on 2026-08-27, from `brain_runtime`, the login
-- every agent surface in this fleet connects as, before a line of this was written:
--
--   DOOR 1  brain.work_item.accepted_by       6 of 6 forged names ACCEPTED
--             'zzz-not-a-person'  ACCEPTED    <- the exact string the V9 adversarial run used
--             'Andrew'            ACCEPTED
--             'operator'          ACCEPTED    <- an agent recording the operator's own decision
--             'you'          ACCEPTED    <- $USER on this host, which is nobody
--             'auto-accept'       ACCEPTED
--             'T2'                refused only because T2 was in brain.agent
--
--   DOOR 2  brain.recommendation.decided_by   0 of 6 forged names accepted
--             every one refused by migration 32's trigger, naming the login
--
--   the two doors disagree on 6 of 6 names
--
-- Both columns record the same act, "a human read this and decided". One asks the database and
-- one asks the caller. This file makes the second ask the database, which is the shape
-- queue/schema/0015_recommendation_human_login.sql already established and is the reason that
-- file is the model here rather than a new idea.
--
-- ================================================================= why a SECOND trigger
--
-- Migration 22's own footer forbids editing its function: "this file has to keep describing live
-- exactly. Moving any of this INSIDE the function body silently ends that, because live is
-- already applied and re-applying is not on the table. New text there needs a new migration."
-- It also records that `pg_proc.prosrc` for brain.work_item_acceptance_guard on live is
-- byte-identical to that file's body, 6765 characters. A CREATE OR REPLACE here would make that
-- sentence false and no reader would find out.
--
-- So this adds a SECOND trigger beside it and touches migration 22's function not at all, which
-- is the same move migration 26 made when it added work_item_agent_claimable_raise_is_a_human.
-- Two triggers on one table, each holding one rule, is also the property both files already
-- argue for: a gate that exists in one place is a gate one bug away from being absent.
--
-- Trigger firing order inside one BEFORE phase is alphabetical by trigger name, so
-- `work_item_acceptance_guard` runs before `work_item_acceptance_is_a_login`. That is the order
-- a reader wants: the coherent/finished/rewrite rules refuse a malformed acceptance first, and
-- this file only ever sees one that is already well formed.
--
-- ================================================================= the rule, and the one carve-out
--
--   HUMAN      accepted_by must be exactly brain.current_human(), which reads session_user.
--              Not "a human is connected AND the caller named someone". Equal. A human who could
--              record a colleague's acceptance is the forgery this closes, one seat over, and
--              under row 0386 decision 2 there are now up to twelve seats.
--
--   AUTOMATIC  ONE value is exempt, and it is exempt because it claims no human: the literal the
--              auto-accept rule writes when it accepts an item by itself
--              (engine/swarm_engine/accept.py, `measure_in_transaction`). That write happens
--              INSIDE the `done` transaction, as brain_runtime, on a fleet host with no operator
--              credential and no human anywhere near it. Requiring a human login there would not
--              make it safer, it would delete the automatic path, and the automatic path is what
--              engine/swarm_engine/accept.py::disagreement_report exists to measure.
--
--              The exemption is NARROW AND HONEST. It does not let an agent claim a human read
--              anything: the row says `auto-accept`, every reader can see that it does, and
--              `disagreement_report` already keeps rule-acted rows OUT of the agreement rate
--              rather than scoring the rule's own act as a human agreeing with it.
--
--              THE STRING HAS ONE PRODUCER, brain.auto_acceptor(), and both the trigger and
--              engine/swarm_engine/accept.py call it. That is deliberate: two copies of a
--              sentinel is how the exemption silently widens to "any string starting with auto",
--              and it is the argument store/session.py::subscriber_role_name already makes about
--              a role name.
--
-- ================================================================= what it does NOT close
--
-- The residual migration 22 states is NARROWED HERE, NOT REMOVED, and the difference is worth
-- being exact about.
--
-- Migration 22: "A rogue or buggy agent can still write accepted_by='operator', accepted_at=now()
-- onto a genuinely done, genuinely unaccepted task ... the console's own legitimate acceptance is
-- byte-for-byte the same UPDATE from the same login, brain_runtime, so the database has nothing
-- to tell them apart by."
--
-- After this file the two are NOT the same UPDATE from the same login. The console's acceptance
-- comes from brain_operator (web/actions.py now passes as_operator=True, this lane) and the
-- agent's comes from brain_runtime, and the database tells them apart by session_user, which no
-- SQL a client sends can change.
--
-- WHAT REMAINS, stated plainly because a guard that oversells itself is worse than no guard:
-- store/SECRETS.md's local-attended residual. Every credential on this host is a 0600 file in one
-- 0700 directory under one UID, so an OS process that can read the operator's secret file can
-- connect as the operator. The database no longer takes a client's word about who it is; file
-- permissions are what separate the operator from a process running as the same user. That is
-- unchanged by this file and is a secret-backend question, not a trigger question.
--
-- ================================================================= rollback, stated and executed
--
--     BEGIN;
--       DROP TRIGGER IF EXISTS work_item_acceptance_is_a_login ON brain.work_item;
--       DROP FUNCTION IF EXISTS brain.work_item_acceptance_is_a_login();
--       DROP FUNCTION IF EXISTS brain.auto_acceptor();
--       DELETE FROM brain.schema_migration WHERE version = 36;
--     COMMIT;
--
-- Additive: one function, one sentinel function, one trigger. No column added, none dropped, no
-- grant withdrawn, migration 22's function untouched. After the rollback the store is exactly at
-- migration 22's behaviour, which is measurable rather than asserted: the six-name probe accepts
-- 6 of 6 again. Run, and re-applied, on brain_lane_e. See PROOF.md section 4.

\set ON_ERROR_STOP on

BEGIN;

-- ---------------------------------------------------------------- the one sentinel

CREATE OR REPLACE FUNCTION brain.auto_acceptor() RETURNS text
  LANGUAGE sql IMMUTABLE AS $$ SELECT 'auto-accept'::text $$;

GRANT EXECUTE ON FUNCTION brain.auto_acceptor() TO PUBLIC;

COMMENT ON FUNCTION brain.auto_acceptor() IS
  'The name brain.work_item.accepted_by carries when the AUTO-ACCEPT RULE accepted an item and '
  'no human did. One producer, called by the trigger that exempts it and by '
  'engine/swarm_engine/accept.py which writes it, so the exemption and the writer cannot drift '
  'into two spellings. It is not a human and no reader should treat it as one: '
  'engine/swarm_engine/accept.py::disagreement_report keeps rule-acted rows out of the '
  'agreement rate for exactly that reason.';

-- ---------------------------------------------------------------- the gate

CREATE OR REPLACE FUNCTION brain.work_item_acceptance_is_a_login() RETURNS trigger
  LANGUAGE plpgsql AS $$
DECLARE
  is_accepted boolean;
  moved       boolean;
  who         text;
BEGIN
  -- Absent is one state with two spellings on this table, exactly as migration 22 computes it.
  is_accepted := NEW.accepted_at IS NOT NULL OR COALESCE(NEW.accepted_by, '') <> '';
  IF NOT is_accepted THEN
    RETURN NEW;
  END IF;

  -- Nothing about the acceptance actually moved. An idempotent re-write is a no-op and not a
  -- failure (migration 14's rule, which migration 22 also honours). Without this branch a
  -- `SET accepted_by = accepted_by` from any agent surface would start failing, and the row it
  -- would fail on is one that was accepted legitimately.
  IF TG_OP = 'UPDATE'
     AND COALESCE(NEW.accepted_by, '') IS NOT DISTINCT FROM COALESCE(OLD.accepted_by, '')
     AND NEW.accepted_at IS NOT DISTINCT FROM OLD.accepted_at THEN
    RETURN NEW;
  END IF;

  -- The one carve-out. The rule accepting an item by itself claims no human and says so in the
  -- column. See the header.
  IF NEW.accepted_by = brain.auto_acceptor() THEN
    RETURN NEW;
  END IF;

  who := brain.current_human();

  IF who IS NULL THEN
    RAISE EXCEPTION 'refusing to record an acceptance of % from %: this connection is not a '
                    'human login', COALESCE(NEW.id, '(new)'), session_user
      USING ERRCODE = 'insufficient_privilege',
            HINT = 'Acceptance is the act that turns an agent''s self-report into recorded '
                   'completion, and until this migration the only identity test on it was "is '
                   'this name in brain.agent", which FAILS OPEN: every name that is not an agent '
                   'passed. Measured 2026-08-27 from brain_runtime: 6 of 6 forged names were '
                   'accepted, including zzz-not-a-person and operator. It is now established by '
                   'WHICH LOGIN writes the row, which is what migration 32 already does for '
                   'brain.recommendation.decided_by. The operator''s surfaces pass '
                   'as_operator=True, which opens a login mapped in brain.human_role; provision '
                   'one with store/bin/provision-operator.sh or, for a named human, '
                   '`swarm admin human provision <slug>`.';
  END IF;

  IF COALESCE(NEW.accepted_by, '') <> who THEN
    RAISE EXCEPTION 'refusing to record % as the acceptor of %: this connection is %',
                    COALESCE(NULLIF(NEW.accepted_by, ''), '<null>'),
                    COALESCE(NEW.id, '(new)'), who
      USING ERRCODE = 'insufficient_privilege',
            HINT = 'accepted_by is the record of WHO decided, so it is the database''s answer '
                   'about this connection and never a name the caller chose. Under row 0386 '
                   'decision 2 there are up to twelve human logins on this instance, so a human '
                   'recording a COLLEAGUE''s acceptance is the same forgery one seat over. Pass '
                   'the name this connection actually is, or connect as the human who decided.';
  END IF;

  RETURN NEW;
END $$;

COMMENT ON FUNCTION brain.work_item_acceptance_is_a_login() IS
  'Success criterion 5, held at the second of the two doors that record an acceptance. '
  'brain.recommendation.decided_by has asked the database since migration 32; '
  'brain.work_item.accepted_by asked the CALLER until this one. Composes '
  'brain.current_human() rather than building a second notion of who is human, which is the '
  'rule migration 20''s own COMMENT states: two notions drift and the weaker one becomes the '
  'real policy.';

-- Named to sort AFTER work_item_acceptance_guard, so migration 22's coherence, finished and
-- rewrite rules refuse a malformed acceptance before this one is asked. Same `UPDATE OF` column
-- list as migration 22, for the reason migration 22 gives: it costs nothing on the writes that
-- do not name those columns, which is nearly all of them.
DROP TRIGGER IF EXISTS work_item_acceptance_is_a_login ON brain.work_item;
CREATE TRIGGER work_item_acceptance_is_a_login
  BEFORE INSERT OR UPDATE OF accepted_by, accepted_at ON brain.work_item
  FOR EACH ROW
  EXECUTE FUNCTION brain.work_item_acceptance_is_a_login();

COMMENT ON COLUMN brain.work_item.accepted_by IS
  'The human who decided, as the DATABASE names this connection (brain.current_human(), from '
  'session_user), or brain.auto_acceptor() when the auto-accept rule acted and no human did. '
  'Migration 22 guarded the SHAPE of an acceptance; migration 36 guards WHOSE it is. Not a '
  'string the caller picks: measured 2026-08-27, six forged names including zzz-not-a-person '
  'were accepted from brain_runtime before that changed.';

INSERT INTO brain.schema_migration (version, name)
     VALUES (36, '0036_work_item_acceptance_is_a_login')
  ON CONFLICT (version) DO NOTHING;

COMMIT;
