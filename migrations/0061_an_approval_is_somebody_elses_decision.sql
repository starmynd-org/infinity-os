-- migration 61: an APPROVAL IS SOMEBODY ELSE'S DECISION, made by a human entitled to make it, and
-- it names the subject it authorises.
--
-- Packet R01, repair R02-REPAIR-01 lead item. Written 2026-09-06 by Terminal 04 after CAP14-REV-017.
-- Ledger version 61. (The contract/workspace slice moves to 62; both are R01's.)
--
-- ------------------------------------------------------------------ THE DEFECT, MEASURED
--
-- Terminal 08 read the trigger list and observed that `brain.approval` carries only its append-only
-- trigger. It asked for that to be verified rather than asserted. Verified on a disposable store at
-- commit f059037, and it is worse than the reading suggested. Three lines of actual output:
--
--     holds a grant for: effect.external
--     self-approval row written: 18  decided_by: operator
--     authority.check PASSED on a self-minted approval. grant 109
--
-- Three holes, and they compose into one:
--
--   1. NO ACTOR CHECK. `decided_by` was any non-blank string. `authority_grant` has had
--      `authority_grant_actor_is_known` since migration 57; the approval path never got one.
--   2. `grant_seq` WAS A FOREIGN KEY TO ANY GRANT. Nothing required it to belong to the decider or
--      to carry `approval.decide`. The capability vocabulary existed and this path never read it.
--   3. `authority.check` MATCHED ON (proposal_id, proposal_version) AND IGNORED THE SUBJECT, so an
--      approval minted for one actor satisfied the check for any other.
--
-- Together: any actor holding any grant could authorise itself. "No agent self-approval" is a
-- program property and C01 encodes it at schema level, so this was a gate that was not a gate.
--
-- ------------------------------------------------------------------ WHY NOT NULL IS `NOT VALID`
--
-- `subject` is required from here on and cannot be required retroactively: rows written before this
-- migration do not have one, and inventing a value for them would be manufacturing a decision
-- nobody made. `NOT VALID` is exactly the right instrument -- Postgres enforces the constraint on
-- every new and updated row and tolerates the existing ones -- and the reader is left able to tell
-- the two eras apart. `authority.check` treats a NULL subject as authorising NOBODY, so the old
-- rows fail closed rather than being grandfathered into meaning something.

BEGIN;

ALTER TABLE brain.approval ADD COLUMN IF NOT EXISTS subject text;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'approval_subject_required_ck') THEN
    ALTER TABLE brain.approval
      ADD CONSTRAINT approval_subject_required_ck
      CHECK (subject IS NOT NULL AND btrim(subject) <> '') NOT VALID;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'approval_is_not_self_ck') THEN
    ALTER TABLE brain.approval
      ADD CONSTRAINT approval_is_not_self_ck
      CHECK (subject IS DISTINCT FROM decided_by) NOT VALID;
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS approval_by_proposal_and_subject
  ON brain.approval (proposal_id, proposal_version, subject);

-- THE APPROVER MUST BE ENTITLED, and entitlement is not a claim the caller makes about itself.
--
-- SECURITY DEFINER with a pinned search_path, for the same reason migration 57's actor trigger
-- needs it and stated again here rather than left to be inferred: this reads `brain.human_role`,
-- which `brain_runtime` deliberately cannot SELECT, because the roster of humans is not the
-- runtime's business. Without SECURITY DEFINER this check does not fail closed, it fails loud and
-- unrelated, and the obvious repair is to grant the runtime read access to the human roster --
-- which would be a real privacy regression bought to make a check work.
CREATE OR REPLACE FUNCTION brain.approval_decider_is_entitled() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, brain AS $fn$
DECLARE
  g record;
BEGIN
  -- 1. NOT YOURSELF. Checked in the trigger as well as in the CHECK constraint, so the message is
  --    the one a reader needs; the constraint is the belt that holds if this function is dropped.
  IF NEW.subject IS NOT DISTINCT FROM NEW.decided_by THEN
    RAISE EXCEPTION
      'approval refused: % cannot approve its own proposal. An actor that can authorise itself '
      'is not gated by anything, and no agent self-approval is a program property.', NEW.decided_by
      USING ERRCODE = 'restrict_violation';
  END IF;

  -- 2. A HUMAN THIS STORE KNOWS. An approval is a human act; carve-out 4 in the repository's own
  --    orientation says acceptance is a human act and this is the same rule one step earlier.
  IF NOT EXISTS (SELECT 1 FROM brain.human_role WHERE human = NEW.decided_by) THEN
    RAISE EXCEPTION
      'approval refused: decided_by % names no human this store knows. A decision made by nobody '
      'is not a decision.', NEW.decided_by USING ERRCODE = 'restrict_violation';
  END IF;

  -- 3. THE GRANT MUST BE THE DECIDER'S, AND MUST BE THE RIGHT CAPABILITY, AND MUST BE IN FORCE.
  --    Before this, grant_seq was a foreign key to any grant at all.
  SELECT * INTO g FROM brain.authority_grant WHERE grant_seq = NEW.grant_seq;
  IF g.subject IS DISTINCT FROM NEW.decided_by THEN
    RAISE EXCEPTION
      'approval refused: grant % belongs to % and the decision is signed %. An approval names the '
      'authority the APPROVER holds, not one it found.', NEW.grant_seq, g.subject, NEW.decided_by
      USING ERRCODE = 'restrict_violation';
  END IF;
  IF g.capability IS DISTINCT FROM 'approval.decide' THEN
    RAISE EXCEPTION
      'approval refused: grant % carries capability %, not approval.decide. Holding some authority '
      'is not holding this one.', NEW.grant_seq, g.capability USING ERRCODE = 'restrict_violation';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM brain.authority_in_force WHERE grant_seq = NEW.grant_seq) THEN
    RAISE EXCEPTION
      'approval refused: grant % is not in force. It was revoked or it expired, and a decision '
      'signed with a withdrawn authority is not signed.', NEW.grant_seq
      USING ERRCODE = 'restrict_violation';
  END IF;

  -- 4. THE SUBJECT MUST BE SOMEBODY. Same two verifiable kinds as migration 57, same stated limit:
  --    a service principal cannot be verified because this store holds no roster for one.
  IF NEW.subject IS NULL THEN
    RAISE EXCEPTION
      'approval refused: the approval names no subject. An approval that authorises nobody in '
      'particular is the shape that authorises everybody.' USING ERRCODE = 'restrict_violation';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM brain.agent WHERE name = NEW.subject)
     AND NOT EXISTS (SELECT 1 FROM brain.human_role WHERE human = NEW.subject) THEN
    RAISE EXCEPTION
      'approval refused: subject % is neither an agent nor a human this store knows. An approval '
      'for nobody authorises nothing and should not be recorded as though it did.', NEW.subject
      USING ERRCODE = 'restrict_violation';
  END IF;
  RETURN NEW;
END;
$fn$;

DROP TRIGGER IF EXISTS approval_decider_is_entitled ON brain.approval;
CREATE TRIGGER approval_decider_is_entitled BEFORE INSERT ON brain.approval
  FOR EACH ROW EXECUTE FUNCTION brain.approval_decider_is_entitled();

-- ------------------------------------------------------------------ EVERY REFUSAL WATCHED HAPPENING
--
-- Eleven checks: EIGHT refusals watched refusing and THREE positive controls. Both counts are asserted
-- separately at the end, not just the total. CAP14-REV-017 found that migrations 57 and 58 each
-- reported a refusal count one higher than the number of EXCEPTION blocks they contained, because
-- only the total was guarded. An unasserted split is how a layer quietly stops existing.
DO $$
DECLARE
  n int := 0;
  refusals int := 0;
  controls int := 0;
  who text; ag text := '_mv61_selfcheck_agent';
  made_human boolean := false;
  g_decide bigint; g_wrong bigint; a bigint;
BEGIN
  -- ON A FRESH BUILD THE ROSTER IS EMPTY. `scratch-db.sh` maps the operator login AFTER every
  -- migration has run, so a migration that assumed a human exists fails on `create` and passes on
  -- `migrate` -- which is the worst shape of all, because it works on the store you are looking at
  -- and breaks on the one that ships. Migration 57 met this first and solved it the same way.
  SELECT human INTO who FROM brain.human_role ORDER BY human LIMIT 1;
  IF who IS NULL THEN
    who := '_mv61_fixture_human';
    INSERT INTO brain.human_role (role_name, human, granted_at, granted_by)
      VALUES ('brain_runtime', who, now(), 'migration 61 fixture');
    made_human := true;
  END IF;
  INSERT INTO brain.agent (name, role, status, host, updated)
    VALUES (ag, 'worker', 'idle', 'migration-61', now())
    ON CONFLICT (name) DO UPDATE SET updated = now();

  INSERT INTO brain.authority_grant (granted_by, subject_kind, subject, capability, scope,
                                     expires_at, evidence)
    VALUES (who, 'human', who, 'approval.decide', 'lane/mv61', now() + interval '1 hour',
            'migration 61 self-check') RETURNING grant_seq INTO g_decide;
  INSERT INTO brain.authority_grant (granted_by, subject_kind, subject, capability, scope,
                                     expires_at, evidence)
    VALUES (who, 'human', who, 'effect.external', 'lane/mv61', now() + interval '1 hour',
            'migration 61 self-check') RETURNING grant_seq INTO g_wrong;

  -- 1 control: a proper approval lands. Somebody else's decision, by an entitled human.
  INSERT INTO brain.approval (proposal_id, proposal_version, decided_by, subject, grant_seq,
                              decision)
    VALUES ('mv61-proposal', 'v1', who, ag, g_decide, 'approve') RETURNING approval_seq INTO a;
  n := n + 1; controls := controls + 1;

  -- 2 refusal: approving yourself.
  BEGIN
    INSERT INTO brain.approval (proposal_id, proposal_version, decided_by, subject, grant_seq,
                                decision)
      VALUES ('mv61-self', 'v1', who, who, g_decide, 'approve');
    RAISE EXCEPTION 'migration 61: a self-approval was NOT refused';
  EXCEPTION WHEN restrict_violation THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 3 refusal: a grant that is not the approval capability.
  BEGIN
    INSERT INTO brain.approval (proposal_id, proposal_version, decided_by, subject, grant_seq,
                                decision)
      VALUES ('mv61-cap', 'v1', who, ag, g_wrong, 'approve');
    RAISE EXCEPTION 'migration 61: an approval signed with a non-approval grant was NOT refused';
  EXCEPTION WHEN restrict_violation THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 4 refusal: a decider this store has never heard of.
  BEGIN
    INSERT INTO brain.approval (proposal_id, proposal_version, decided_by, subject, grant_seq,
                                decision)
      VALUES ('mv61-ghost', 'v1', '_nobody_at_all', ag, g_decide, 'approve');
    RAISE EXCEPTION 'migration 61: an approval by an unknown decider was NOT refused';
  EXCEPTION WHEN restrict_violation THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 5 refusal: a subject this store has never heard of.
  BEGIN
    INSERT INTO brain.approval (proposal_id, proposal_version, decided_by, subject, grant_seq,
                                decision)
      VALUES ('mv61-nosub', 'v1', who, '_no_such_subject', g_decide, 'approve');
    RAISE EXCEPTION 'migration 61: an approval for an unknown subject was NOT refused';
  EXCEPTION WHEN restrict_violation THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 6 refusal: no subject at all. The TRIGGER speaks first here, because a BEFORE trigger runs
  --   ahead of the table's constraints, so this is restrict_violation and not check_violation.
  BEGIN
    INSERT INTO brain.approval (proposal_id, proposal_version, decided_by, grant_seq, decision)
      VALUES ('mv61-null', 'v1', who, g_decide, 'approve');
    RAISE EXCEPTION 'migration 61: an approval naming no subject was NOT refused';
  EXCEPTION WHEN restrict_violation THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 7 refusal: a revoked grant cannot sign a decision.
  INSERT INTO brain.authority_revocation (grant_seq, revoked_by, reason)
    VALUES (g_decide, who, 'migration 61 self-check');
  BEGIN
    INSERT INTO brain.approval (proposal_id, proposal_version, decided_by, subject, grant_seq,
                                decision)
      VALUES ('mv61-revoked', 'v1', who, ag, g_decide, 'approve');
    RAISE EXCEPTION 'migration 61: an approval signed with a revoked grant was NOT refused';
  EXCEPTION WHEN restrict_violation THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 7b THE SECOND LAYER, PROVEN TO BE ONE. With the trigger stood down, the CHECK constraints
  --   still refuse both a missing subject and a self-approval. Four refusal layers are only defence
  --   in depth if each is asserted; an unasserted layer is how one quietly stops existing, which is
  --   the same finding that produced the split counts in this block.
  ALTER TABLE brain.approval DISABLE TRIGGER approval_decider_is_entitled;
  BEGIN
    INSERT INTO brain.approval (proposal_id, proposal_version, decided_by, grant_seq, decision)
      VALUES ('mv61-null-2', 'v1', who, g_wrong, 'approve');
    ALTER TABLE brain.approval ENABLE TRIGGER approval_decider_is_entitled;
    RAISE EXCEPTION 'migration 61: with the trigger down, a subjectless approval was NOT refused';
  EXCEPTION WHEN check_violation THEN n := n + 1; refusals := refusals + 1;
  END;
  BEGIN
    INSERT INTO brain.approval (proposal_id, proposal_version, decided_by, subject, grant_seq,
                                decision)
      VALUES ('mv61-self-2', 'v1', who, who, g_wrong, 'approve');
    ALTER TABLE brain.approval ENABLE TRIGGER approval_decider_is_entitled;
    RAISE EXCEPTION 'migration 61: with the trigger down, a self-approval was NOT refused';
  EXCEPTION WHEN check_violation THEN n := n + 1; refusals := refusals + 1;
  END;
  ALTER TABLE brain.approval ENABLE TRIGGER approval_decider_is_entitled;

  -- 8 control: the row from check 1 survived, with its subject recorded.
  IF (SELECT subject FROM brain.approval WHERE approval_seq = a) IS DISTINCT FROM ag THEN
    RAISE EXCEPTION 'migration 61: the approval did not record its subject';
  END IF;
  n := n + 1; controls := controls + 1;

  -- 9 control: the trigger is SECURITY DEFINER with a pinned search_path, asserted not assumed.
  IF NOT EXISTS (
       SELECT 1 FROM pg_proc p JOIN pg_namespace ns ON ns.oid = p.pronamespace
        WHERE ns.nspname = 'brain' AND p.proname = 'approval_decider_is_entitled'
          AND p.prosecdef AND p.proconfig @> ARRAY['search_path=pg_catalog, brain']) THEN
    RAISE EXCEPTION 'migration 61: approval_decider_is_entitled is not SECURITY DEFINER with a '
                    'pinned search_path';
  END IF;
  n := n + 1; controls := controls + 1;

  -- Cleanup. Both tables are append-only, so their triggers are stood down for exactly these
  -- statements and restored immediately, in the open.
  ALTER TABLE brain.approval DISABLE TRIGGER approval_append_only;
  ALTER TABLE brain.authority_grant DISABLE TRIGGER authority_grant_append_only;
  ALTER TABLE brain.authority_revocation DISABLE TRIGGER authority_revocation_append_only;
  DELETE FROM brain.approval WHERE proposal_id LIKE 'mv61-%';
  DELETE FROM brain.authority_revocation WHERE grant_seq IN (g_decide, g_wrong);
  DELETE FROM brain.authority_grant WHERE grant_seq IN (g_decide, g_wrong);
  ALTER TABLE brain.authority_revocation ENABLE TRIGGER authority_revocation_append_only;
  ALTER TABLE brain.authority_grant ENABLE TRIGGER authority_grant_append_only;
  ALTER TABLE brain.approval ENABLE TRIGGER approval_append_only;
  DELETE FROM brain.agent WHERE name = ag;
  IF made_human THEN
    DELETE FROM brain.human_role WHERE human = who AND granted_by = 'migration 61 fixture';
  END IF;

  IF n <> 11 OR refusals <> 8 OR controls <> 3 THEN
    RAISE EXCEPTION 'migration 61: expected 11 checks as 8 refusals and 3 controls, ran % as % and %',
      n, refusals, controls;
  END IF;

  RAISE NOTICE
    'migration 61: % of 11 checks passed, % refusals watched refusing and % positive controls. '
    'brain.approval holds % row(s) and brain.authority_grant %.',
    n, refusals, controls, (SELECT count(*) FROM brain.approval),
    (SELECT count(*) FROM brain.authority_grant);
END $$;

INSERT INTO brain.schema_migration (version, name)
  VALUES (61, '0061_an_approval_is_somebody_elses_decision')
  ON CONFLICT (version) DO NOTHING;

COMMIT;
