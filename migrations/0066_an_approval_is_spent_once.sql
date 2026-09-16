-- migration 66: an approval is SPENT once, not merely RECORDED once, and it can expire and be
-- withdrawn.
--
-- Packet R01, contract alignment. Written 2026-09-07 by Terminal 04 against pinned contract
-- C01-PIN-01 v0.1.0-draft.5. Ledger version 66.
--
-- ------------------------------------------------------------------ THE HOLE, STATED PLAINLY
--
-- Migration 57 gave `brain.approval` a unique constraint over (proposal_id, proposal_version,
-- decided_by), and I described that at the time as closing the replayed-approval hole. It does not.
-- It stops the same human RECORDING the same decision twice. It does nothing whatever to stop ONE
-- recorded decision being SPENT on two effects, which is the property that actually matters and the
-- one the contract asks for: `single_use` is `{"const": true}` and `consumed` is required.
--
-- The difference is the whole of this migration. Before it:
--
--     approve once  ->  effect A passes its check  ->  effect B passes the SAME check  ->  two effects
--
-- A human approving one payment authorised every payment that named the same proposal version.
--
-- ------------------------------------------------------------------ CONSUMED BY A CONDITIONAL UPDATE
--
-- Consumption is `UPDATE ... WHERE consumed_state = 'unconsumed'` and a row count, NOT a read
-- followed by a write. Two workers reaching the same approval at the same instant both see
-- `unconsumed` if you look first; exactly one of them gets `ROW_COUNT = 1` from a conditional
-- update. This lane has already been caught once this week believing a look-then-write was atomic
-- (CAP14-REV-017, the fence), so the single-use guarantee is written as the one shape that does not
-- need a lock to be correct.
--
-- ------------------------------------------------------------------ AD-I3 AND AD-I4
--
--   AD-I3  `expires_at` is strictly after `decided_at`. An approval cannot expire before it was
--          made. NULL means no expiry was set, which stays legal and is not the same as "expired".
--   AD-I4  a consumed decision's `consumed_at` is never after `revoked_at`. A revoked approval
--          cannot then be consumed; spending an authority after it was withdrawn is the ordering
--          this constraint exists to make unrepresentable rather than merely discouraged.

BEGIN;

ALTER TABLE brain.approval ADD COLUMN IF NOT EXISTS consumed_state text NOT NULL DEFAULT 'unconsumed';
ALTER TABLE brain.approval ADD COLUMN IF NOT EXISTS consumed_at timestamptz;
ALTER TABLE brain.approval ADD COLUMN IF NOT EXISTS consumed_by text;
ALTER TABLE brain.approval ADD COLUMN IF NOT EXISTS expires_at timestamptz;
ALTER TABLE brain.approval ADD COLUMN IF NOT EXISTS revoked_at timestamptz;
ALTER TABLE brain.approval ADD COLUMN IF NOT EXISTS revoked_by text;
ALTER TABLE brain.approval ADD COLUMN IF NOT EXISTS revoke_reason text;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'approval_consumed_state_ck') THEN
    ALTER TABLE brain.approval ADD CONSTRAINT approval_consumed_state_ck
      CHECK (consumed_state IN ('unconsumed', 'consumed'));
  END IF;
  -- A CONSUMED DECISION SAYS WHEN AND BY WHAT. `consumed` with no receipt is a claim that the
  -- approval was spent on something nobody can name.
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'approval_consumed_names_its_receipt_ck') THEN
    ALTER TABLE brain.approval ADD CONSTRAINT approval_consumed_names_its_receipt_ck
      CHECK (consumed_state = 'unconsumed'
             OR (consumed_at IS NOT NULL AND btrim(coalesce(consumed_by, '')) <> ''));
  END IF;
  -- AD-I3
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'approval_expiry_after_decision_ck') THEN
    ALTER TABLE brain.approval ADD CONSTRAINT approval_expiry_after_decision_ck
      CHECK (expires_at IS NULL OR expires_at > decided_at);
  END IF;
  -- AD-I4
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'approval_not_consumed_after_revoked_ck') THEN
    ALTER TABLE brain.approval ADD CONSTRAINT approval_not_consumed_after_revoked_ck
      CHECK (consumed_at IS NULL OR revoked_at IS NULL OR consumed_at <= revoked_at);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'approval_revocation_is_whole_ck') THEN
    ALTER TABLE brain.approval ADD CONSTRAINT approval_revocation_is_whole_ck
      CHECK ((revoked_at IS NULL AND revoked_by IS NULL)
             OR (revoked_at IS NOT NULL AND btrim(coalesce(revoked_by, '')) <> ''));
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS approval_unconsumed
  ON brain.approval (proposal_id, proposal_version, subject) WHERE consumed_state = 'unconsumed';

-- WHAT AN APPROVAL IS GOOD FOR RIGHT NOW: decided approve, not consumed, not revoked, not expired.
-- A view for the same reason every other one here is a view: expiry is arithmetic on now(), so
-- nothing has to sweep and nothing can fail to sweep.
CREATE OR REPLACE VIEW brain.approval_spendable AS
SELECT a.*
  FROM brain.approval a
 WHERE a.decision = 'approve'
   AND a.consumed_state = 'unconsumed'
   AND a.revoked_at IS NULL
   AND (a.expires_at IS NULL OR a.expires_at > now());

GRANT SELECT ON brain.approval_spendable TO brain_runtime, brain_subscriber;

-- CONSUMPTION IS ONE-WAY AND HAPPENS ONCE. The append-only trigger from 57 refuses every UPDATE on
-- this table, so consumption needs a hole in it, and the hole is exactly this shape: unconsumed to
-- consumed, or unrevoked to revoked, and nothing else. Everything that made the decision what it
-- was stays immutable.
CREATE OR REPLACE FUNCTION brain.approval_is_append_only_except_spending() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'brain.approval is append-only: DELETE refused. A decision that was made '
                    'cannot be unmade, only revoked.' USING ERRCODE = 'restrict_violation';
  END IF;
  IF NEW.approval_seq     IS DISTINCT FROM OLD.approval_seq
     OR NEW.proposal_id      IS DISTINCT FROM OLD.proposal_id
     OR NEW.proposal_version IS DISTINCT FROM OLD.proposal_version
     OR NEW.workspace        IS DISTINCT FROM OLD.workspace
     OR NEW.decided_by       IS DISTINCT FROM OLD.decided_by
     OR NEW.subject          IS DISTINCT FROM OLD.subject
     OR NEW.grant_seq        IS DISTINCT FROM OLD.grant_seq
     OR NEW.decision         IS DISTINCT FROM OLD.decision
     OR NEW.decided_at       IS DISTINCT FROM OLD.decided_at
     OR NEW.expires_at       IS DISTINCT FROM OLD.expires_at THEN
    RAISE EXCEPTION
      'brain.approval: the only legal updates are spending it and revoking it. Editing what was '
      'decided, who decided it, or what it was for destroys the record of the decision itself.'
      USING ERRCODE = 'restrict_violation';
  END IF;
  IF OLD.consumed_state = 'consumed' AND NEW.consumed_state = 'consumed'
     AND (NEW.consumed_at IS DISTINCT FROM OLD.consumed_at
          OR NEW.consumed_by IS DISTINCT FROM OLD.consumed_by) THEN
    RAISE EXCEPTION
      'approval % was already spent at % on %. An approval is single use: the contract says so with '
      '`single_use: true`, and spending it twice is the defect this migration exists to close.',
      OLD.approval_seq, OLD.consumed_at, OLD.consumed_by USING ERRCODE = 'restrict_violation';
  END IF;
  IF NEW.consumed_state = 'unconsumed' AND OLD.consumed_state = 'consumed' THEN
    RAISE EXCEPTION 'approval %: spending cannot be undone. Revoke it or record a new decision.',
      OLD.approval_seq USING ERRCODE = 'restrict_violation';
  END IF;
  IF OLD.revoked_at IS NOT NULL AND NEW.revoked_at IS DISTINCT FROM OLD.revoked_at THEN
    RAISE EXCEPTION 'approval % was already revoked at %. The first revocation stands.',
      OLD.approval_seq, OLD.revoked_at USING ERRCODE = 'restrict_violation';
  END IF;
  -- AD-I4 AT THE MOMENT OF SPENDING, not only as a stored-row constraint. A CHECK compares the two
  -- columns of one row; this refuses the ACT of spending an approval that is already revoked, which
  -- is the thing a caller actually attempts.
  IF OLD.consumed_state = 'unconsumed' AND NEW.consumed_state = 'consumed'
     AND OLD.revoked_at IS NOT NULL THEN
    RAISE EXCEPTION
      'approval % was revoked at % and cannot now be spent. Authority withdrawn before it was used '
      'was never used.', OLD.approval_seq, OLD.revoked_at USING ERRCODE = 'restrict_violation';
  END IF;
  RETURN NEW;
END;
$fn$;

DROP TRIGGER IF EXISTS approval_append_only ON brain.approval;
CREATE TRIGGER approval_append_only BEFORE UPDATE OR DELETE ON brain.approval
  FOR EACH ROW EXECUTE FUNCTION brain.approval_is_append_only_except_spending();

GRANT SELECT, INSERT, UPDATE ON brain.approval TO brain_runtime;
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'brain_operator') THEN
    EXECUTE 'GRANT SELECT, INSERT, UPDATE ON brain.approval TO brain_operator';
  END IF;
END $$;

-- ------------------------------------------------------------------ EVERY REFUSAL WATCHED HAPPENING
--
-- Ten checks: SIX refusals watched refusing and FOUR positive controls. Both counts asserted.
DO $$
DECLARE
  n int := 0; refusals int := 0; controls int := 0;
  who text; made_human boolean := false; ag text := '_mv66_agent';
  ws text := 'ws-mv66'; g_dec bigint; a1 bigint; a2 bigint; hit int;
BEGIN
  SELECT human INTO who FROM brain.human_role ORDER BY human LIMIT 1;
  IF who IS NULL THEN
    who := '_mv66_fixture_human';
    INSERT INTO brain.human_role (role_name, human, granted_at, granted_by)
      VALUES ('brain_runtime', who, now(), 'migration 66 fixture');
    made_human := true;
  END IF;
  INSERT INTO brain.agent (name, role, status, host, updated)
    VALUES (ag, 'worker', 'idle', 'migration-66', now())
    ON CONFLICT (name) DO UPDATE SET updated = now();
  INSERT INTO brain.authority_grant (granted_by, workspace, subject_kind, subject, capability,
                                     scope, expires_at, evidence)
    VALUES (who, ws, 'human', who, 'approval.decide', 'lane/mv66', now() + interval '1 hour',
            'migration 66') RETURNING grant_seq INTO g_dec;

  -- 1 control: a decision lands unconsumed and is spendable.
  INSERT INTO brain.approval (proposal_id, proposal_version, workspace, decided_by, subject,
                              grant_seq, decision)
    VALUES ('mv66-a', 'v1', ws, who, ag, g_dec, 'approve') RETURNING approval_seq INTO a1;
  IF (SELECT consumed_state FROM brain.approval WHERE approval_seq = a1) <> 'unconsumed'
     OR NOT EXISTS (SELECT 1 FROM brain.approval_spendable WHERE approval_seq = a1) THEN
    RAISE EXCEPTION 'migration 66: a fresh approval was not spendable';
  END IF;
  n := n + 1; controls := controls + 1;

  -- 2 control: spending it once works, by conditional update, and reports exactly one row.
  UPDATE brain.approval SET consumed_state = 'consumed', consumed_at = now(),
         consumed_by = 'attempt:mv66-first'
   WHERE approval_seq = a1 AND consumed_state = 'unconsumed';
  GET DIAGNOSTICS hit = ROW_COUNT;
  IF hit <> 1 THEN
    RAISE EXCEPTION 'migration 66: spending a fresh approval updated % rows', hit;
  END IF;
  n := n + 1; controls := controls + 1;

  -- 3 control: AND IT IS NO LONGER SPENDABLE. This is the whole migration. Before it, this row
  --   would still have satisfied a second effect's check.
  IF EXISTS (SELECT 1 FROM brain.approval_spendable WHERE approval_seq = a1) THEN
    RAISE EXCEPTION 'migration 66: a spent approval is still spendable';
  END IF;
  n := n + 1; controls := controls + 1;

  -- 4 refusal: THE SECOND SPEND. The conditional update matches nothing, which is the guarantee;
  --   an unconditional one is refused by the trigger, which is the belt.
  UPDATE brain.approval SET consumed_state = 'consumed', consumed_at = now(),
         consumed_by = 'attempt:mv66-second'
   WHERE approval_seq = a1 AND consumed_state = 'unconsumed';
  GET DIAGNOSTICS hit = ROW_COUNT;
  IF hit <> 0 THEN
    RAISE EXCEPTION 'migration 66: a second spend matched % row(s)', hit;
  END IF;
  BEGIN
    UPDATE brain.approval SET consumed_at = now(), consumed_by = 'attempt:mv66-forced'
     WHERE approval_seq = a1;
    RAISE EXCEPTION 'migration 66: re-spending an approval was NOT refused';
  EXCEPTION WHEN restrict_violation THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 5 refusal: spending cannot be undone.
  BEGIN
    UPDATE brain.approval SET consumed_state = 'unconsumed' WHERE approval_seq = a1;
    RAISE EXCEPTION 'migration 66: un-spending an approval was NOT refused';
  EXCEPTION WHEN restrict_violation OR check_violation THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 6 refusal: what was decided stays immutable. 57 said append-only; this file opens one hole in
  --   that, and the hole must not have widened.
  BEGIN
    UPDATE brain.approval SET decision = 'reject' WHERE approval_seq = a1;
    RAISE EXCEPTION 'migration 66: the decision itself was editable';
  EXCEPTION WHEN restrict_violation THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 7 refusal: AD-I3. An approval cannot expire before it was made.
  BEGIN
    INSERT INTO brain.approval (proposal_id, proposal_version, workspace, decided_by, subject,
                                grant_seq, decision, expires_at)
      VALUES ('mv66-past', 'v1', ws, who, ag, g_dec, 'approve', now() - interval '1 hour');
    RAISE EXCEPTION 'migration 66: an approval expiring before its decision was NOT refused';
  EXCEPTION WHEN check_violation THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 8 refusal: AD-I4, at the MOMENT OF SPENDING. A stored-row CHECK compares two columns; this
  --   refuses the act a caller actually attempts, which is spending an authority already withdrawn.
  INSERT INTO brain.approval (proposal_id, proposal_version, workspace, decided_by, subject,
                              grant_seq, decision)
    VALUES ('mv66-b', 'v1', ws, who, ag, g_dec, 'approve') RETURNING approval_seq INTO a2;
  UPDATE brain.approval SET revoked_at = now(), revoked_by = who, revoke_reason = 'withdrawn'
   WHERE approval_seq = a2;
  BEGIN
    UPDATE brain.approval SET consumed_state = 'consumed', consumed_at = now(),
           consumed_by = 'attempt:mv66-after-revoke'
     WHERE approval_seq = a2 AND consumed_state = 'unconsumed';
    RAISE EXCEPTION 'migration 66: spending a revoked approval was NOT refused';
  EXCEPTION WHEN restrict_violation THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 9 refusal: a consumed row with no receipt names nothing.
  BEGIN
    INSERT INTO brain.approval (proposal_id, proposal_version, workspace, decided_by, subject,
                                grant_seq, decision, consumed_state)
      VALUES ('mv66-c', 'v1', ws, who, ag, g_dec, 'approve', 'consumed');
    RAISE EXCEPTION 'migration 66: a consumed approval naming no receipt was NOT refused';
  EXCEPTION WHEN check_violation THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 10 control: a revoked approval is not spendable either, by the view.
  IF EXISTS (SELECT 1 FROM brain.approval_spendable WHERE approval_seq = a2) THEN
    RAISE EXCEPTION 'migration 66: a revoked approval was still spendable';
  END IF;
  n := n + 1; controls := controls + 1;

  ALTER TABLE brain.approval DISABLE TRIGGER approval_append_only;
  ALTER TABLE brain.authority_grant DISABLE TRIGGER authority_grant_append_only;
  DELETE FROM brain.approval WHERE proposal_id LIKE 'mv66-%';
  DELETE FROM brain.authority_grant WHERE grant_seq = g_dec;
  ALTER TABLE brain.authority_grant ENABLE TRIGGER authority_grant_append_only;
  ALTER TABLE brain.approval ENABLE TRIGGER approval_append_only;
  DELETE FROM brain.agent WHERE name = ag;
  IF made_human THEN
    DELETE FROM brain.human_role WHERE human = who AND granted_by = 'migration 66 fixture';
  END IF;

  IF n <> 10 OR refusals <> 6 OR controls <> 4 THEN
    RAISE EXCEPTION 'migration 66: expected 10 checks as 6 refusals and 4 controls, ran % as % and %',
      n, refusals, controls;
  END IF;
  RAISE NOTICE
    'migration 66: % of 10 checks passed, % refusals watched refusing and % positive controls. '
    'An approval is now spent once, not merely recorded once.', n, refusals, controls;
END $$;

INSERT INTO brain.schema_migration (version, name)
  VALUES (66, '0066_an_approval_is_spent_once')
  ON CONFLICT (version) DO NOTHING;

COMMIT;
