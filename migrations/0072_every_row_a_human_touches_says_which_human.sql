-- migration 72: EVERY ROW A HUMAN TOUCHES SAYS WHICH HUMAN, as the database names the connection.
--
-- LEDGER VERSION 72. max(version) + 1 after 71, read from `engine/bin/scratch-db.sh ledger` on
-- 2026-09-09. Not a hole.
--
-- Written by 2026-09-09-IOS-term-5, executing item 2 of migrations/USER-SCOPING-INVENTORY.md.
--
-- ================================================================= the ruling this implements
--
-- Andrew, 2026-09-09, ARCHITECTURE-2026-09-09-tenancy-and-the-git-boundary.md, closed:
--
--     "User-scoped schema from day one, exactly one user in practice. Every operational row that
--      will ever be per-user gets a user identity now, while there are 53 migrations and little
--      data. Retrofitting a user column across a live event fabric later is one of the most
--      expensive changes a system of this shape can be asked to make. Cheap now, brutal later,
--      and the cost is not symmetric."
--
-- ================================================================= what exists already, measured
--
-- migrations/USER-SCOPING-INVENTORY.md, from two query files on ios_term5_scratch at ledger 70:
-- 53 base tables; 10 carry a human identity the database answers (a trigger calling
-- brain.current_human()); 5 carry a name checked against the roster but not the login; 16 are
-- rows a human writes or acts on with no identity column or a bare string; 22 no human writes.
-- This file is the 16. The 5 are migration 36's move each, with a writer coupling, and 71 is
-- the first of them. The 22 are left alone, on purpose.
--
-- ================================================================= the shape, and why one column
--
-- Each of the 16 tables gains two nullable columns:
--
--     last_human     text          the human who last wrote this row, as brain.current_human()
--                                  named the connection, or NULL: no human ever has
--     last_human_at  timestamptz   when
--
-- and one shared trigger, brain.last_human_is_the_login(), BEFORE INSERT OR UPDATE:
--
--   a HUMAN login writes      the columns are SET to the database's answer, whatever the caller
--                             passed. Not naming is not anonymity (IDENTITY-POLICY rule A3).
--   a human names a COLLEAGUE the write is refused. A name a caller passes is CHECKED, never
--                             recorded (rule A3 again, and 36's "Equal", and 71's).
--   an AGENT login writes     the columns are left exactly as they were: NULL on a new row, the
--                             last human's name on an old one. An agent that tries to SET or
--                             CLEAR them is refused. An agent is not a human and cannot say one
--                             was here, which is the forgery migration 20 exists to refuse.
--
-- ONE COLUMN PAIR AND ONE FUNCTION, NOT SIXTEEN VERB-SHAPED COLUMNS, because the ruling is about
-- the IDENTITY on the row and not the verb. answered_by, disposed_by, accepted_by name a verb and
-- each needs its writer to know it; last_human needs no writer to change at all. Every existing
-- writer passes nothing, gets stamped or left alone by the trigger, and is unchanged. Per-verb
-- columns can follow where a surface needs the verb rather than the person, and a reader who
-- has this column can already answer "who did this" for every one of the 16.
--
-- IT IS NOT A PERMISSION and grants nothing: a bare record of which human, the same tier as
-- IDENTITY-POLICY part 3 tier 1 (attribution), never tier 3 (a matrix). It is not workspace
-- either; the tenancy column is inventory item 3 and is not begun here.
--
-- ================================================================= a migrations-only store
--
-- Six of the sixteen live in queue/schema or budget/schema (queue_bump, queue_calibration,
-- queue_defer, queue_item, budget_incident, budget_policy; time_entry is queue's too). A store
-- built with SCRATCH_SCHEMA_DIRS=migrations does not have them, and migration 37 already meets
-- that situation and degrades with a stated NOTICE rather than dying. Same here: a table that is
-- absent is skipped by name, the count of present tables is printed, and the ledger row lands
-- either way, because a later `migrate` with the full lane set re-running this file is exactly
-- what re-running an idempotent ALTER ... IF NOT EXISTS is for.
--
-- ================================================================= what this does NOT close
--
-- 1. THE HOST RESIDUAL, as 20, 36, 63 and 71 state: file permissions separate the operator from a
--    process under the same UID. Unchanged.
-- 2. THE 5 NAME TABLES keep their name-checked columns; this file does not touch them. Each
--    becomes LOGIN by its own migration when its writer opens the human's login (71's coupling).
-- 3. HISTORY. `last_human` is the LAST human, not every human. The thread and the receipts remain
--    the trail; this column is the answer to "whose row is this now", which is what a per-user
--    surface filters on. A full per-row history is an audit table and is not ruled.
--
-- ================================================================= rollback, executed, WITH LOSS
--
-- migrations/tests/rollback-0072.sql drops the trigger from each table, the function, and the two
-- columns. THE DROP LOSES THE STAMPS AND THEY CANNOT BE RE-DERIVED: who last touched a queue item
-- is not recorded anywhere else. So this migration is two-way in DDL and ONE-WAY IN DATA. On the
-- day it lands there are zero stamps to lose; a week later there are not. That is stated here, in
-- words, per the term-5 brief section 5 item 3, rather than left to be discovered in a recovery.
-- Executed and re-applied on ios_term5_scratch on 2026-09-09 with zero stamps on the store.

\set ON_ERROR_STOP on

BEGIN;

-- ---------------------------------------------------------------- the one function

CREATE OR REPLACE FUNCTION brain.last_human_is_the_login() RETURNS trigger
  LANGUAGE plpgsql AS $$
DECLARE who text;
BEGIN
  who := brain.current_human();
  IF TG_OP = 'INSERT' THEN
    IF who IS NULL THEN
      IF NEW.last_human IS NOT NULL OR NEW.last_human_at IS NOT NULL THEN
        RAISE EXCEPTION 'refusing to record % as the human behind a new % row: this connection '
                        'is %, which is not a human login',
                        COALESCE(NEW.last_human, '<a time with no name>'), TG_TABLE_NAME, session_user
          USING ERRCODE = 'insufficient_privilege',
                HINT = 'last_human is the database''s answer about the connection that wrote '
                       'the row. An agent cannot say a human was here.';
      END IF;
      RETURN NEW;
    END IF;
    IF NEW.last_human IS NOT NULL AND NEW.last_human <> who THEN
      RAISE EXCEPTION 'refusing to record % as the human behind a new % row: this connection is %',
                      NEW.last_human, TG_TABLE_NAME, who
        USING ERRCODE = 'insufficient_privilege',
              HINT = 'A name a caller passes is checked, never recorded (IDENTITY-POLICY rule '
                     'A3). Pass nothing; the database fills it in.';
    END IF;
    NEW.last_human := who;
    NEW.last_human_at := now();
    RETURN NEW;
  END IF;
  -- UPDATE
  IF who IS NULL THEN
    IF NEW.last_human IS DISTINCT FROM OLD.last_human
       OR NEW.last_human_at IS DISTINCT FROM OLD.last_human_at THEN
      RAISE EXCEPTION 'refusing to change the human behind % row from % to %: this connection is '
                      '%, which is not a human login', TG_TABLE_NAME,
                      COALESCE(OLD.last_human, '<nobody>'), COALESCE(NEW.last_human, '<nobody>'),
                      session_user
        USING ERRCODE = 'insufficient_privilege',
              HINT = 'An agent''s write leaves last_human exactly as it was. Setting it or '
                     'clearing it is a human''s act, recorded from the human''s own login.';
    END IF;
    RETURN NEW;
  END IF;
  IF NEW.last_human IS DISTINCT FROM OLD.last_human
     AND NEW.last_human IS NOT NULL AND NEW.last_human <> who THEN
    RAISE EXCEPTION 'refusing to record % as the human behind a % row: this connection is %',
                    NEW.last_human, TG_TABLE_NAME, who
      USING ERRCODE = 'insufficient_privilege';
  END IF;
  NEW.last_human := who;
  NEW.last_human_at := now();
  RETURN NEW;
END $$;

COMMENT ON FUNCTION brain.last_human_is_the_login() IS
  'One trigger function for every operational table a human writes: stamps last_human and '
  'last_human_at from brain.current_human() when the connection is a human, refuses a caller '
  'that names somebody else, and leaves both untouched when the connection is an agent. '
  'Composes the one notion of who is human (migration 20''s COMMENT) rather than a second.';

-- ---------------------------------------------------------------- the sixteen, guarded by name

DO $$
DECLARE
  t text;
  present int := 0;
  absent text := '';
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'budget_incident', 'budget_policy', 'disposition', 'effect_reconciliation',
    'image_attachment', 'objective', 'question', 'queue_bump', 'queue_calibration',
    'queue_defer', 'queue_item', 'receipt', 'runtime_flag', 'thread', 'time_entry',
    'voice_capture'] LOOP
    IF to_regclass('brain.' || t) IS NULL THEN
      absent := absent || ' ' || t;
      CONTINUE;
    END IF;
    EXECUTE format('ALTER TABLE brain.%I ADD COLUMN IF NOT EXISTS last_human text', t);
    EXECUTE format('ALTER TABLE brain.%I ADD COLUMN IF NOT EXISTS last_human_at timestamptz', t);
    EXECUTE format('COMMENT ON COLUMN brain.%I.last_human IS %L', t,
      'The human who last wrote this row, as the DATABASE names the connection '
      '(brain.current_human()), or NULL when no human ever has. Stamped by trigger; a caller''s '
      'name is checked, never recorded; an agent''s write leaves it alone. Migration 72.');
    EXECUTE format('DROP TRIGGER IF EXISTS last_human_is_the_login ON brain.%I', t);
    EXECUTE format('CREATE TRIGGER last_human_is_the_login BEFORE INSERT OR UPDATE ON brain.%I '
                   'FOR EACH ROW EXECUTE FUNCTION brain.last_human_is_the_login()', t);
    present := present + 1;
  END LOOP;
  IF present = 0 THEN
    RAISE EXCEPTION 'migration 72: 0 of 16 tables present; this is not a store this file knows';
  END IF;
  RAISE NOTICE 'migration 72: last_human on % of 16 tables%', present,
    CASE WHEN absent = '' THEN '' ELSE '; absent on this store (a migrations-only build), skipped by name:' || absent END;
END $$;

-- ---------------------------------------------------------------- every refusal watched happening
--
-- On brain.runtime_flag, which every build has. As the applying superuser, first NOT mapped (an
-- agent's shape), then mapped to a fixture human, then unmapped again. Fixture rows removed.
DO $$
DECLARE
  n int := 0; refusals int := 0; controls int := 0;
  fx text := '_mv72_fixture_human'; k text := 'mv72-flag';
  got text; got_at timestamptz;
BEGIN
  -- 1 refusal: a non-human connection naming a human on a new row.
  BEGIN
    INSERT INTO brain.runtime_flag (key, value, last_human) VALUES (k, 'x', 'operator');
    RAISE EXCEPTION 'migration 72: an agent connection recorded a human on a new row';
  EXCEPTION WHEN insufficient_privilege THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 2 control: a non-human connection writes, and the row says no human has touched it.
  INSERT INTO brain.runtime_flag (key, value) VALUES (k, 'x');
  SELECT last_human, last_human_at INTO got, got_at FROM brain.runtime_flag WHERE key = k;
  IF got IS NOT NULL OR got_at IS NOT NULL THEN
    RAISE EXCEPTION 'migration 72: an agent write stamped a human (%)', got;
  END IF;
  n := n + 1; controls := controls + 1;

  INSERT INTO brain.human_role (role_name, human, granted_by)
    VALUES (session_user, fx, 'migration 72 fixture');

  -- 3 control: a human updates the row and is stamped, having passed nothing.
  UPDATE brain.runtime_flag SET value = 'y' WHERE key = k;
  SELECT last_human, last_human_at INTO got, got_at FROM brain.runtime_flag WHERE key = k;
  IF got IS DISTINCT FROM fx OR got_at IS NULL THEN
    RAISE EXCEPTION 'migration 72: a human''s update was not stamped (got %)', got;
  END IF;
  n := n + 1; controls := controls + 1;

  -- 4 refusal: a human naming a colleague.
  BEGIN
    UPDATE brain.runtime_flag SET value = 'z', last_human = 'somebody-else' WHERE key = k;
    RAISE EXCEPTION 'migration 72: a colleague''s name was recorded from another login';
  EXCEPTION WHEN insufficient_privilege THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 5 control: a human inserting a new row is stamped.
  INSERT INTO brain.runtime_flag (key, value) VALUES (k || '-2', 'x');
  SELECT last_human INTO got FROM brain.runtime_flag WHERE key = k || '-2';
  IF got IS DISTINCT FROM fx THEN
    RAISE EXCEPTION 'migration 72: a human''s insert was not stamped (got %)', got;
  END IF;
  n := n + 1; controls := controls + 1;

  DELETE FROM brain.human_role WHERE human = fx AND granted_by = 'migration 72 fixture';

  -- 6 refusal: an agent clearing the stamp.
  BEGIN
    UPDATE brain.runtime_flag SET last_human = NULL, last_human_at = NULL WHERE key = k;
    RAISE EXCEPTION 'migration 72: an agent connection cleared a human''s stamp';
  EXCEPTION WHEN insufficient_privilege THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 7 control: an agent's ordinary update leaves the stamp exactly as it was.
  UPDATE brain.runtime_flag SET value = 'w' WHERE key = k;
  SELECT last_human INTO got FROM brain.runtime_flag WHERE key = k;
  IF got IS DISTINCT FROM fx THEN
    RAISE EXCEPTION 'migration 72: an agent''s update changed the stamp (now %)', got;
  END IF;
  n := n + 1; controls := controls + 1;

  DELETE FROM brain.runtime_flag WHERE key IN (k, k || '-2');

  IF n <> 7 OR refusals <> 3 OR controls <> 4 THEN
    RAISE EXCEPTION 'migration 72: expected 7 checks as 3 refusals and 4 controls, ran % as % and %',
      n, refusals, controls;
  END IF;
  RAISE NOTICE 'migration 72: % of 7 checks passed, % refusals watched refusing and % positive '
               'controls, on brain.runtime_flag. An agent naming a human, a human naming a '
               'colleague, and an agent clearing a stamp were refused; a human''s insert and '
               'update were stamped from the login; an agent''s writes left the stamp alone. '
               'Fixture rows removed.', n, refusals, controls;
END $$;

INSERT INTO brain.schema_migration (version, name)
  VALUES (72, '0072_every_row_a_human_touches_says_which_human')
  ON CONFLICT (version) DO NOTHING;

COMMIT;
