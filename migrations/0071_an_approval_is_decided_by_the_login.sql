-- migration 71: AN APPROVAL IS DECIDED BY THE LOGIN, not by a name the caller passes.
--
-- LEDGER VERSION 71. max(version) + 1 after 70, read from `engine/bin/scratch-db.sh ledger` on
-- 2026-09-09 (67 versions read, 1..70, holes at 54 55 60, NEXT VERSION IS 71). Not a hole.
--
-- Written by 2026-09-09-IOS-term-5 for task IDN-APPROVAL-DECIDER-FORGE-01 part 1, released by
-- 2026-09-09-IOS-term-13 at 20260909T123656Z after confirmation by 2026-09-09-IOS-term-11.
--
-- ================================================================= what this closes, measured
--
-- migrations/tests/probe_approval_decider_login.py, from brain_runtime, the login every agent
-- surface in this fleet holds:
--
--   ios_term5_scratch  at ledger 68, 2026-09-09, term-5:   decided_by='lane-e-two' LANDED, approval_seq 20
--   ios_term11_scratch at ledger 68, 2026-09-09, term-11:  decided_by='lane-e-two' LANDED, approval_seq 18
--
-- 1 of 1 forged-decider inserts landed on each of two stores, two seats, same probe digest
-- (sha256 f7598676). Migrations 61 and 63 check that decided_by NAMES a human in brain.human_role
-- and that the grant belongs to that name and is in force. Neither compares the name to
-- session_user. `store/authority.py::_decide` is registered with the default runtime role and
-- takes decided_by as a keyword argument. So brain.approval.decided_by had, until this file, the
-- shape brain.work_item.accepted_by had before migration 36: a string test that fails open.
--
-- This is migration 36's move on this column, and nothing else. The rule, in 36's words: "Not 'a
-- human is connected AND the caller named someone'. Equal."
--
-- ================================================================= why a THIRD trigger, not an edit
--
-- Migration 61's function is applied on every store that has 61, and its file describes live; a
-- CREATE OR REPLACE here would silently make that description false (36's argument, and 70's).
-- So this is a trigger beside it. Firing order inside one BEFORE phase is alphabetical:
--
--   approval_decider_is_entitled            (61/63)  roster, grant, workspace, subject
--   approval_decider_is_the_login           (this)   the name IS this connection
--   approval_decider_reaches_the_workspace  (70)     the connection's human reaches the set
--
-- A malformed approval is refused by 61 before this is asked; a well-formed one that this refuses
-- never reaches 70. Each file holds one rule.
--
-- ================================================================= no carve-out, and why
--
-- Migration 36 exempts one value, brain.auto_acceptor(), because the auto-accept rule accepts an
-- item by itself inside the `done` transaction with no human near it. There is no automatic
-- approver. Migration 61's title is the rule: an approval is SOMEBODY ELSE'S decision, and the
-- somebody is a human (61 refuses a decided_by outside brain.human_role). So there is nothing to
-- exempt, and an exemption here would be the door this file closes, re-opened under a nicer name.
--
-- ================================================================= THE COUPLING, which is part of this file
--
-- THIS MIGRATION MUST NOT LAND ALONE. `approval decide` runs as brain_runtime today, so on a store
-- with this file every call through it is refused, including store/test_authority.py's approval
-- scenes, which open the operator's identity through store.whoami() and then write through the
-- runtime login. That refusal is CORRECT and it is the point: an agent surface may not decide.
-- The other half, part 2 of the task, is the transition registered with the operator role and its
-- callers opening the human's own login, exactly as web/actions.py does with as_operator=True for
-- acceptance. store/authority.py and the console path are in nobody's declared set in the
-- 2026-09-09 fleet; the Admiral holds the routing as AUTHORITY-MODULE-OWNERSHIP. This file is
-- authored, proved on scratch, and reported FIXED-UNCLEARED; it is not merge-requested until
-- part 2 has an owner, per the task's own text.
--
-- ================================================================= what it does NOT close
--
-- The host residual every identity migration here states (20, 36, 63): on a local-attended host
-- every credential is a 0600 file under one UID, so a process that can read the operator's
-- secret can connect as the operator. Unchanged. What is closed is the case that exists: a
-- process holding brain_runtime has no route to a decided approval under any human's name.
--
-- ================================================================= rollback, executed
--
--     BEGIN;
--       DROP TRIGGER IF EXISTS approval_decider_is_the_login ON brain.approval;
--       DROP FUNCTION IF EXISTS brain.approval_decider_is_the_login();
--       DELETE FROM brain.schema_migration WHERE version = 71;
--     COMMIT;
--
-- TWO-WAY, NO LOSS: one function, one trigger, no column, no grant. After it the store is at 70's
-- behaviour, measurable: the probe lands 1 of 1 again. migrations/tests/rollback-0071.sql is the
-- file; executed and re-applied on ios_term5_scratch on 2026-09-09.

\set ON_ERROR_STOP on

BEGIN;

CREATE OR REPLACE FUNCTION brain.approval_decider_is_the_login() RETURNS trigger
  LANGUAGE plpgsql AS $$
DECLARE who text;
BEGIN
  who := brain.current_human();
  IF who IS NULL THEN
    RAISE EXCEPTION 'refusing to record an approval of % decided by %: this connection is %, '
                    'which is not a human login', NEW.proposal_id, NEW.decided_by, session_user
      USING ERRCODE = 'insufficient_privilege',
            HINT = 'An approval is somebody else''s decision (migration 61) and the somebody is a '
                   'human. Until this migration the test on decided_by was "is this name in '
                   'brain.human_role and does it hold the grant", which FAILS OPEN from any '
                   'login: measured 2026-09-09 on two scratch stores, 1 of 1 forged deciders '
                   'landed from brain_runtime. It is now established by WHICH LOGIN writes the '
                   'row, as brain.work_item.accepted_by has been since migration 36. Decide as '
                   'the human who decided: the operator''s surfaces open a login mapped in '
                   'brain.human_role (as_operator=True), and a named human''s open its own.';
  END IF;
  IF COALESCE(NEW.decided_by, '') <> who THEN
    RAISE EXCEPTION 'refusing to record % as the decider of %: this connection is %',
                    COALESCE(NULLIF(NEW.decided_by, ''), '<null>'), NEW.proposal_id, who
      USING ERRCODE = 'insufficient_privilege',
            HINT = 'decided_by is the record of WHO decided, so it is the database''s answer '
                   'about this connection and never a name the caller chose. A human recording '
                   'a colleague''s decision is the same forgery one seat over.';
  END IF;
  RETURN NEW;
END $$;

COMMENT ON FUNCTION brain.approval_decider_is_the_login() IS
  'Migration 36''s rule on brain.approval.decided_by: equal to brain.current_human(), which '
  'reads session_user. Composes the one notion of who is human rather than building a second, '
  'per migration 20''s COMMENT. Beside 61''s and 70''s triggers, never inside them.';

DROP TRIGGER IF EXISTS approval_decider_is_the_login ON brain.approval;
CREATE TRIGGER approval_decider_is_the_login
  BEFORE INSERT ON brain.approval
  FOR EACH ROW EXECUTE FUNCTION brain.approval_decider_is_the_login();

COMMENT ON COLUMN brain.approval.decided_by IS
  'The human who decided, as the DATABASE names this connection (brain.current_human(), from '
  'session_user). Migration 61 guarded WHICH names may decide; migration 71 guards that the name '
  'IS the login. Not a string the caller picks: measured 2026-09-09, 1 of 1 forged deciders '
  'landed from brain_runtime before that changed.';

-- ---------------------------------------------------------------- every refusal watched happening
--
-- Two fixture humans: fx is mapped to THIS session's login for the transaction, fx2 to the
-- brain_runtime role the way migration 63 mapped its fixture. Both are removed at the end.
DO $$
DECLARE
  n int := 0; refusals int := 0; controls int := 0;
  fx text := '_mv71_fixture_human'; fx2 text := '_mv71_other_human'; ag text := '_mv71_agent';
  ws text := 'ws-mv71'; g bigint; g2 bigint;
BEGIN
  INSERT INTO brain.human_role (role_name, human, granted_by)
    VALUES (session_user, fx, 'migration 71 fixture');
  INSERT INTO brain.human_role (role_name, human, granted_by)
    VALUES ('brain_runtime', fx2, 'migration 71 fixture');
  INSERT INTO brain.agent (name, role, status, host, updated)
    VALUES (ag, 'worker', 'idle', 'migration-71', now())
    ON CONFLICT (name) DO UPDATE SET updated = now();
  -- ws-mv71 is deliberately NOT declared in brain.workspace, so migration 70's gate stands aside
  -- and this self-check measures only its own rule.
  INSERT INTO brain.authority_grant (granted_by, workspace, subject_kind, subject, capability,
                                     scope, expires_at, evidence)
    VALUES (fx, ws, 'human', fx, 'approval.decide', 'lane/mv71', now() + interval '1 hour',
            'migration 71') RETURNING grant_seq INTO g;
  INSERT INTO brain.authority_grant (granted_by, workspace, subject_kind, subject, capability,
                                     scope, expires_at, evidence)
    VALUES (fx, ws, 'human', fx2, 'approval.decide', 'lane/mv71', now() + interval '1 hour',
            'migration 71') RETURNING grant_seq INTO g2;

  -- 1 refusal: a human recording a COLLEAGUE's decision. fx2 is a real human with a real grant;
  --   this connection is fx.
  BEGIN
    INSERT INTO brain.approval (proposal_id, proposal_version, workspace, decided_by, subject,
                                grant_seq, decision)
      VALUES ('mv71-colleague', 'v1', ws, fx2, ag, g2, 'approve');
    RAISE EXCEPTION 'migration 71: a colleague''s decision was recorded from another login';
  EXCEPTION WHEN insufficient_privilege THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 2 control: the human decides as themselves.
  INSERT INTO brain.approval (proposal_id, proposal_version, workspace, decided_by, subject,
                              grant_seq, decision)
    VALUES ('mv71-self', 'v1', ws, fx, ag, g, 'approve');
  n := n + 1; controls := controls + 1;

  -- 3 refusal: THE PROBE'S SHAPE. Unmap this session, so it is not a human login; fx2 is still a
  --   known human holding a grant, which is everything 61 and 63 ask for.
  DELETE FROM brain.human_role WHERE human = fx AND granted_by = 'migration 71 fixture';
  BEGIN
    INSERT INTO brain.approval (proposal_id, proposal_version, workspace, decided_by, subject,
                                grant_seq, decision)
      VALUES ('mv71-forged', 'v1', ws, fx2, ag, g2, 'approve');
    RAISE EXCEPTION 'migration 71: a non-human login recorded an approval under a human''s name';
  EXCEPTION WHEN insufficient_privilege THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 4 refusal: an empty decider is not a decider either.
  INSERT INTO brain.human_role (role_name, human, granted_by)
    VALUES (session_user, fx, 'migration 71 fixture');
  BEGIN
    INSERT INTO brain.approval (proposal_id, proposal_version, workspace, decided_by, subject,
                                grant_seq, decision)
      VALUES ('mv71-blank', 'v1', ws, '', ag, g, 'approve');
    RAISE EXCEPTION 'migration 71: a blank decider was accepted';
  EXCEPTION WHEN insufficient_privilege OR check_violation OR restrict_violation THEN
    n := n + 1; refusals := refusals + 1;
  END;

  ALTER TABLE brain.approval DISABLE TRIGGER approval_append_only;
  ALTER TABLE brain.authority_grant DISABLE TRIGGER authority_grant_append_only;
  DELETE FROM brain.approval WHERE proposal_id LIKE 'mv71-%';
  DELETE FROM brain.authority_grant WHERE grant_seq IN (g, g2);
  ALTER TABLE brain.authority_grant ENABLE TRIGGER authority_grant_append_only;
  ALTER TABLE brain.approval ENABLE TRIGGER approval_append_only;
  DELETE FROM brain.agent WHERE name = ag;
  DELETE FROM brain.human_role WHERE human IN (fx, fx2) AND granted_by = 'migration 71 fixture';

  IF n <> 4 OR refusals <> 3 OR controls <> 1 THEN
    RAISE EXCEPTION 'migration 71: expected 4 checks as 3 refusals and 1 control, ran % as % and %',
      n, refusals, controls;
  END IF;
  RAISE NOTICE 'migration 71: % of 4 checks passed, % refusals watched refusing and % positive '
               'control. A colleague''s decision, a non-human login''s decision under a human''s '
               'name, and a blank decider were each refused; a human deciding as themselves '
               'landed. Fixture rows removed; tables ship EMPTY.', n, refusals, controls;
END $$;

INSERT INTO brain.schema_migration (version, name)
  VALUES (71, '0071_an_approval_is_decided_by_the_login')
  ON CONFLICT (version) DO NOTHING;

COMMIT;
