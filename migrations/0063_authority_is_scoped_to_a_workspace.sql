-- migration 63: AUTHORITY IS SCOPED TO A WORKSPACE, so a grant in one cannot license an act in
-- another.
--
-- Packet R01, the G2 condition on R01/R02. Written 2026-09-06 by Terminal 04 against pinned contract
-- C01-PIN-01, v0.1.0-draft.5, commit 0f5909a, digest
-- sha256:fb42d422a7ae834c2faf5523aad57d97786e51dfbe67360d26b92802ddcb6a10.
-- Ledger version 63, reserved for this since the census.
--
-- ------------------------------------------------------------------ WHY THIS IS A GATE
--
-- Every object in the pinned contract requires `workspace`: ApprovalDecision, CaptureRecord, Lease,
-- Receipt, WorkPacket and the accounting record, all six. The runtime had it on none of them. So
-- until this migration, nothing in the store stopped a grant issued in one workspace from
-- authorising an effect in another: `authority.check` matched subject, capability and scope, and
-- two workspaces that happen to use the same lane name shared an authority boundary without either
-- of them saying so.
--
-- The operator's own canon requires two isolated workspaces (F-IDENTITY), so single-tenant is not a
-- release state. This is why the Admiral holds it as a G2 condition rather than a nice-to-have.
--
-- ------------------------------------------------------------------ A COLUMN, NEVER PART OF THE SCOPE
--
-- The tempting shape is to fold the workspace into the scope string: `ws-7/lane/email` instead of
-- `lane/email`. It is wrong, and Terminal 03 put the reason better than I did while adapting its
-- attention rules to this port:
--
--   "If the workspace were part of the scope string, two lanes would each construct that string and
--   the constructions could drift; as a separate dimension there is one place it is compared and no
--   string for anyone to build differently."
--
-- So `workspace` is a column and an argument, and every existing scope string keeps its shape. A
-- consumer's `scope_for(item)` does not change at all: `source/gmail` stays `source/gmail` and sits
-- INSIDE a workspace rather than beside one. A disagreement about which workspace an act belongs to
-- is then findable as a join, rather than buried inside a concatenation nobody can decompose.
--
-- ------------------------------------------------------------------ NULL IS NOT A WILDCARD
--
-- Rows written before this migration have no workspace and cannot be given one: inventing a value
-- would be asserting a boundary nobody drew. They are left NULL, the constraints are NOT VALID so
-- Postgres enforces them on every new and updated row while tolerating the old, and
-- `authority.check` treats a NULL workspace as matching NOTHING. A pre-63 grant therefore authorises
-- nothing at all rather than everything, which is the only safe reading of "this row predates the
-- boundary".

BEGIN;

ALTER TABLE brain.authority_grant      ADD COLUMN IF NOT EXISTS workspace text;
ALTER TABLE brain.authority_revocation ADD COLUMN IF NOT EXISTS workspace text;
ALTER TABLE brain.approval             ADD COLUMN IF NOT EXISTS workspace text;
ALTER TABLE brain.execution_lease      ADD COLUMN IF NOT EXISTS workspace text;
ALTER TABLE brain.effect_attempt       ADD COLUMN IF NOT EXISTS workspace text;

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['authority_grant', 'approval', 'execution_lease', 'effect_attempt'] LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = t || '_names_its_workspace_ck') THEN
      EXECUTE format(
        'ALTER TABLE brain.%I ADD CONSTRAINT %I CHECK (workspace IS NOT NULL '
        'AND btrim(workspace) <> %L) NOT VALID', t, t || '_names_its_workspace_ck', '');
    END IF;
  END LOOP;
END $$;

-- LS-I1, now stated in full. The old index allowed one unreleased lease per work item across the
-- whole store; the contract's invariant is one per item PER WORKSPACE. Two workspaces working the
-- same packet id are two different pieces of work and must not block each other.
DROP INDEX IF EXISTS brain.execution_lease_one_live_per_item;
CREATE UNIQUE INDEX IF NOT EXISTS execution_lease_one_live_per_item_per_workspace
  ON brain.execution_lease (workspace, work_item_id) WHERE released_at IS NULL;

-- THE VIEW CARRIES IT, so a consumer that filters `authority_in_force` gets the dimension without
-- having to know it came from a join. DROP and CREATE rather than REPLACE: `g.*` is wider now.
DROP VIEW IF EXISTS brain.authority_in_force;
CREATE VIEW brain.authority_in_force AS
SELECT g.grant_seq, g.granted_at, g.granted_by, g.workspace, g.subject_kind, g.subject,
       g.capability, g.scope, g.packet_version, g.expires_at, g.evidence
  FROM brain.authority_grant g
  LEFT JOIN brain.authority_revocation r ON r.grant_seq = g.grant_seq
 WHERE r.grant_seq IS NULL
   AND (g.expires_at IS NULL OR g.expires_at > now());

GRANT SELECT ON brain.authority_in_force TO brain_runtime, brain_subscriber;

-- A GRANT AND ITS REVOCATION BELONG TO THE SAME WORKSPACE, and a revocation that could name a
-- different one would be a way to end somebody else's grant from outside their boundary.
CREATE OR REPLACE FUNCTION brain.authority_revocation_shares_the_workspace() RETURNS trigger
LANGUAGE plpgsql AS $fn$
DECLARE g_ws text;
BEGIN
  SELECT workspace INTO g_ws FROM brain.authority_grant WHERE grant_seq = NEW.grant_seq;
  IF NEW.workspace IS DISTINCT FROM g_ws THEN
    RAISE EXCEPTION
      'revocation refused: grant % belongs to workspace % and the revocation claims %. Ending a '
      'grant from outside its workspace is reaching across the boundary this migration draws.',
      NEW.grant_seq, coalesce(g_ws, 'NULL'), coalesce(NEW.workspace, 'NULL')
      USING ERRCODE = 'restrict_violation';
  END IF;
  RETURN NEW;
END;
$fn$;

DROP TRIGGER IF EXISTS authority_revocation_shares_the_workspace ON brain.authority_revocation;
CREATE TRIGGER authority_revocation_shares_the_workspace BEFORE INSERT ON brain.authority_revocation
  FOR EACH ROW EXECUTE FUNCTION brain.authority_revocation_shares_the_workspace();

-- THE APPROVAL AND ITS GRANT, likewise. Migration 61 already requires the grant to belong to the
-- decider and to carry approval.decide; this adds that both sit in the workspace the approval names.
CREATE OR REPLACE FUNCTION brain.approval_decider_is_entitled() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, brain AS $fn$
DECLARE
  g record;
BEGIN
  IF NEW.subject IS NOT DISTINCT FROM NEW.decided_by THEN
    RAISE EXCEPTION
      'approval refused: % cannot approve its own proposal. An actor that can authorise itself '
      'is not gated by anything, and no agent self-approval is a program property.', NEW.decided_by
      USING ERRCODE = 'restrict_violation';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM brain.human_role WHERE human = NEW.decided_by) THEN
    RAISE EXCEPTION
      'approval refused: decided_by % names no human this store knows. A decision made by nobody '
      'is not a decision.', NEW.decided_by USING ERRCODE = 'restrict_violation';
  END IF;
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
  -- NEW IN 63: the decision and the authority behind it are in the same workspace.
  IF g.workspace IS DISTINCT FROM NEW.workspace THEN
    RAISE EXCEPTION
      'approval refused: the decision claims workspace % and grant % belongs to %. An approval '
      'cannot borrow authority from another workspace.',
      coalesce(NEW.workspace, 'NULL'), NEW.grant_seq, coalesce(g.workspace, 'NULL')
      USING ERRCODE = 'restrict_violation';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM brain.authority_in_force WHERE grant_seq = NEW.grant_seq) THEN
    RAISE EXCEPTION
      'approval refused: grant % is not in force. It was revoked or it expired, and a decision '
      'signed with a withdrawn authority is not signed.', NEW.grant_seq
      USING ERRCODE = 'restrict_violation';
  END IF;
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

-- THE EFFECT AND ITS GRANT. Migration 62 checks the binding matches the grant; this adds the
-- workspace to that binding, which is the reserve path's half of the boundary.
CREATE OR REPLACE FUNCTION brain.effect_attempt_may_reserve() RETURNS trigger
LANGUAGE plpgsql AS $fn$
DECLARE
  live_token bigint;
  item       text;
  g          record;
BEGIN
  SET LOCAL lock_timeout = '5s';
  SELECT work_item_id INTO item FROM brain.execution_lease WHERE lease_id = NEW.lease_id FOR UPDATE;
  IF item IS NULL THEN
    RAISE EXCEPTION 'effect refused: no lease %', NEW.lease_id USING ERRCODE = 'restrict_violation';
  END IF;
  PERFORM 1 FROM brain.execution_lease WHERE work_item_id = item FOR UPDATE;

  SELECT fencing_token INTO live_token FROM brain.execution_lease_live
   WHERE lease_id = NEW.lease_id;
  IF live_token IS NULL THEN
    RAISE EXCEPTION
      'effect refused: lease % is not live. It was released, has expired, or has been superseded '
      'by a newer lease on work item %.', NEW.lease_id, coalesce(item, '?')
      USING ERRCODE = 'restrict_violation';
  END IF;
  IF NEW.fencing_token IS DISTINCT FROM live_token THEN
    RAISE EXCEPTION
      'effect refused: the attempt presents fencing token % and lease % currently holds %. '
      'A stale token is a worker acting on a world that has moved.',
      NEW.fencing_token, NEW.lease_id, live_token USING ERRCODE = 'restrict_violation';
  END IF;

  IF NEW.grant_seq IS NULL OR NEW.subject IS NULL OR NEW.capability IS NULL OR NEW.scope IS NULL THEN
    RAISE EXCEPTION
      'effect refused: the attempt names no licensing authority. An effect that does not record '
      'which grant permitted it cannot be audited afterwards.'
      USING ERRCODE = 'restrict_violation';
  END IF;
  SELECT * INTO g FROM brain.authority_in_force WHERE grant_seq = NEW.grant_seq;
  IF g.grant_seq IS NULL THEN
    RAISE EXCEPTION
      'effect refused: grant % is not in force. It was revoked or it expired, possibly a moment '
      'ago and after the caller checked it.', NEW.grant_seq USING ERRCODE = 'restrict_violation';
  END IF;
  -- NEW IN 63: the workspace is part of the binding, so a grant cannot license across the boundary.
  IF g.workspace IS DISTINCT FROM NEW.workspace THEN
    RAISE EXCEPTION
      'effect refused: the attempt claims workspace % and grant % belongs to %. A grant in one '
      'workspace does not license an act in another, which is the whole of migration 63.',
      coalesce(NEW.workspace, 'NULL'), NEW.grant_seq, coalesce(g.workspace, 'NULL')
      USING ERRCODE = 'restrict_violation';
  END IF;
  IF g.subject IS DISTINCT FROM NEW.subject
     OR g.capability IS DISTINCT FROM NEW.capability
     OR g.scope IS DISTINCT FROM NEW.scope THEN
    RAISE EXCEPTION
      'effect refused: grant % licenses % to % on %, and the attempt claims % / % / %. The '
      'binding is checked against the grant, not copied from the caller.',
      NEW.grant_seq, g.subject, g.capability, g.scope, NEW.subject, NEW.capability, NEW.scope
      USING ERRCODE = 'restrict_violation';
  END IF;

  IF NEW.budget_scope_id IS NOT NULL AND to_regclass('brain.budget_open_stop') IS NOT NULL THEN
    IF EXISTS (SELECT 1 FROM brain.budget_open_stop
                WHERE scope_id = NEW.budget_scope_id
                  AND scope_type::text = NEW.budget_scope_type) THEN
      RAISE EXCEPTION
        'effect refused: an open budget stop covers scope %/%.',
        NEW.budget_scope_type, NEW.budget_scope_id USING ERRCODE = 'restrict_violation';
    END IF;
  END IF;
  RETURN NEW;
END;
$fn$;

-- A LEASE AND ITS EFFECTS SHARE A WORKSPACE TOO, enforced where the lease is read rather than left
-- to the caller: the attempt's workspace must match the lease it is reserved under.
CREATE OR REPLACE FUNCTION brain.effect_attempt_shares_the_lease_workspace() RETURNS trigger
LANGUAGE plpgsql AS $fn$
DECLARE l_ws text;
BEGIN
  SELECT workspace INTO l_ws FROM brain.execution_lease WHERE lease_id = NEW.lease_id;
  IF l_ws IS DISTINCT FROM NEW.workspace THEN
    RAISE EXCEPTION
      'effect refused: lease % belongs to workspace % and the attempt claims %. One act cannot '
      'straddle two workspaces.', NEW.lease_id, coalesce(l_ws, 'NULL'),
      coalesce(NEW.workspace, 'NULL') USING ERRCODE = 'restrict_violation';
  END IF;
  RETURN NEW;
END;
$fn$;

DROP TRIGGER IF EXISTS effect_attempt_shares_the_lease_workspace ON brain.effect_attempt;
CREATE TRIGGER effect_attempt_shares_the_lease_workspace BEFORE INSERT ON brain.effect_attempt
  FOR EACH ROW EXECUTE FUNCTION brain.effect_attempt_shares_the_lease_workspace();

-- ------------------------------------------------------------------ EVERY REFUSAL WATCHED HAPPENING
--
-- Nine checks: SIX refusals watched refusing and THREE positive controls. Both counts asserted.
DO $$
DECLARE
  n int := 0; refusals int := 0; controls int := 0;
  who text; made_human boolean := false; ag text := '_mv63_agent';
  ws_a text := 'ws-mv63-alpha'; ws_b text := 'ws-mv63-beta';
  item text; l_a bigint; t_a bigint; g_a bigint; g_b bigint; g_dec bigint;
  seq_last bigint; seq_called boolean;
BEGIN
  SELECT last_value, is_called INTO seq_last, seq_called FROM brain.item_id_seq;
  SELECT human INTO who FROM brain.human_role ORDER BY human LIMIT 1;
  IF who IS NULL THEN
    who := '_mv63_fixture_human';
    INSERT INTO brain.human_role (role_name, human, granted_at, granted_by)
      VALUES ('brain_runtime', who, now(), 'migration 63 fixture');
    made_human := true;
  END IF;
  INSERT INTO brain.agent (name, role, status, host, updated)
    VALUES (ag, 'worker', 'idle', 'migration-63', now())
    ON CONFLICT (name) DO UPDATE SET updated = now();
  INSERT INTO brain.work_item (title, lane) VALUES ('migration 63 self-check', 'mv')
    RETURNING id INTO item;

  -- 1 control: THE SAME SCOPE STRING IN TWO WORKSPACES. Identical string, different authority.
  INSERT INTO brain.authority_grant (granted_by, workspace, subject_kind, subject, capability,
                                     scope, expires_at, evidence)
    VALUES (who, ws_a, 'human', who, 'effect.external', 'lane/shared-name',
            now() + interval '1 hour', 'migration 63') RETURNING grant_seq INTO g_a;
  INSERT INTO brain.authority_grant (granted_by, workspace, subject_kind, subject, capability,
                                     scope, expires_at, evidence)
    VALUES (who, ws_b, 'human', who, 'effect.external', 'lane/shared-name',
            now() + interval '1 hour', 'migration 63') RETURNING grant_seq INTO g_b;
  n := n + 1; controls := controls + 1;

  -- 2 control: the in-force view reports the workspace each belongs to.
  IF (SELECT workspace FROM brain.authority_in_force WHERE grant_seq = g_a) IS DISTINCT FROM ws_a
     OR (SELECT workspace FROM brain.authority_in_force WHERE grant_seq = g_b) IS DISTINCT FROM ws_b
  THEN
    RAISE EXCEPTION 'migration 63: the in-force view lost the workspace';
  END IF;
  n := n + 1; controls := controls + 1;

  -- 3 refusal: a revocation cannot reach across the boundary.
  BEGIN
    INSERT INTO brain.authority_revocation (grant_seq, workspace, revoked_by, reason)
      VALUES (g_a, ws_b, who, 'reaching across the boundary');
    RAISE EXCEPTION 'migration 63: a cross-workspace revocation was NOT refused';
  EXCEPTION WHEN restrict_violation THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 4 refusal: an approval cannot borrow authority from another workspace.
  INSERT INTO brain.authority_grant (granted_by, workspace, subject_kind, subject, capability,
                                     scope, expires_at, evidence)
    VALUES (who, ws_a, 'human', who, 'approval.decide', 'lane/shared-name',
            now() + interval '1 hour', 'migration 63') RETURNING grant_seq INTO g_dec;
  BEGIN
    INSERT INTO brain.approval (proposal_id, proposal_version, workspace, decided_by, subject,
                                grant_seq, decision)
      VALUES ('mv63-cross', 'v1', ws_b, who, ag, g_dec, 'approve');
    RAISE EXCEPTION 'migration 63: a cross-workspace approval was NOT refused';
  EXCEPTION WHEN restrict_violation THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 5 control: the same approval inside its own workspace lands.
  INSERT INTO brain.approval (proposal_id, proposal_version, workspace, decided_by, subject,
                              grant_seq, decision)
    VALUES ('mv63-ok', 'v1', ws_a, who, ag, g_dec, 'approve');
  n := n + 1; controls := controls + 1;

  -- 6 refusal: THE DEFECT THIS MIGRATION CLOSES. Subject, capability and scope match exactly, and
  --   the grant belongs to the other workspace.
  INSERT INTO brain.execution_lease (work_item_id, workspace, holder, expires_at, fencing_token)
    VALUES (item, ws_a, who, now() + interval '5 minutes', 0)
    RETURNING lease_id, fencing_token INTO l_a, t_a;
  BEGIN
    INSERT INTO brain.effect_attempt (idempotency_key, lease_id, fencing_token, description,
                                      workspace, subject, capability, scope, grant_seq)
      VALUES ('mv63-cross', l_a, t_a, 'licensed from the wrong workspace',
              ws_a, who, 'effect.external', 'lane/shared-name', g_b);
    RAISE EXCEPTION 'migration 63: an effect licensed across workspaces was NOT refused';
  EXCEPTION WHEN restrict_violation THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 7 refusal: nor may the attempt straddle the lease's workspace.
  BEGIN
    INSERT INTO brain.effect_attempt (idempotency_key, lease_id, fencing_token, description,
                                      workspace, subject, capability, scope, grant_seq)
      VALUES ('mv63-straddle', l_a, t_a, 'a lease in one workspace, an effect in another',
              ws_b, who, 'effect.external', 'lane/shared-name', g_b);
    RAISE EXCEPTION 'migration 63: an effect straddling two workspaces was NOT refused';
  EXCEPTION WHEN restrict_violation THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 8 refusal: a row naming no workspace at all.
  BEGIN
    INSERT INTO brain.authority_grant (granted_by, subject_kind, subject, capability, scope,
                                       expires_at, evidence)
      VALUES (who, 'human', who, 'effect.external', 'lane/nowhere',
              now() + interval '1 hour', 'migration 63');
    RAISE EXCEPTION 'migration 63: a grant naming no workspace was NOT refused';
  EXCEPTION WHEN check_violation THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 9 refusal: LS-I1 in full. One unreleased lease per item PER WORKSPACE.
  BEGIN
    INSERT INTO brain.execution_lease (work_item_id, workspace, holder, expires_at, fencing_token)
      VALUES (item, ws_a, 'somebody-else', now() + interval '5 minutes', 0);
    RAISE EXCEPTION 'migration 63: a second live lease in one workspace was NOT refused';
  EXCEPTION WHEN unique_violation THEN n := n + 1; refusals := refusals + 1;
  END;
  -- and the other half of LS-I1, which is why the index is on (workspace, work_item_id): the OTHER
  -- workspace holds its own lease on the same item without being blocked by this one.
  INSERT INTO brain.execution_lease (work_item_id, workspace, holder, expires_at, fencing_token)
    VALUES (item, ws_b, who, now() + interval '5 minutes', 0);

  ALTER TABLE brain.effect_attempt DISABLE TRIGGER effect_attempt_settles_once;
  ALTER TABLE brain.execution_lease DISABLE TRIGGER execution_lease_release_only;
  ALTER TABLE brain.execution_lease DISABLE TRIGGER execution_lease_halts_its_effects;
  ALTER TABLE brain.approval DISABLE TRIGGER approval_append_only;
  ALTER TABLE brain.authority_grant DISABLE TRIGGER authority_grant_append_only;
  DELETE FROM brain.effect_attempt WHERE idempotency_key LIKE 'mv63-%';
  DELETE FROM brain.execution_lease WHERE work_item_id = item;
  DELETE FROM brain.approval WHERE proposal_id LIKE 'mv63-%';
  DELETE FROM brain.authority_grant WHERE grant_seq IN (g_a, g_b, g_dec);
  ALTER TABLE brain.authority_grant ENABLE TRIGGER authority_grant_append_only;
  ALTER TABLE brain.approval ENABLE TRIGGER approval_append_only;
  ALTER TABLE brain.execution_lease ENABLE TRIGGER execution_lease_halts_its_effects;
  ALTER TABLE brain.execution_lease ENABLE TRIGGER execution_lease_release_only;
  ALTER TABLE brain.effect_attempt ENABLE TRIGGER effect_attempt_settles_once;
  DELETE FROM brain.work_item WHERE id = item;
  DELETE FROM brain.agent WHERE name = ag;
  PERFORM setval('brain.item_id_seq', seq_last, seq_called);
  IF made_human THEN
    DELETE FROM brain.human_role WHERE human = who AND granted_by = 'migration 63 fixture';
  END IF;

  IF n <> 9 OR refusals <> 6 OR controls <> 3 THEN
    RAISE EXCEPTION 'migration 63: expected 9 checks as 6 refusals and 3 controls, ran % as % and %',
      n, refusals, controls;
  END IF;
  RAISE NOTICE
    'migration 63: % of 9 checks passed, % refusals watched refusing and % positive controls. '
    'Two workspaces held the same scope string and did not share authority.',
    n, refusals, controls;
END $$;

INSERT INTO brain.schema_migration (version, name)
  VALUES (63, '0063_authority_is_scoped_to_a_workspace')
  ON CONFLICT (version) DO NOTHING;

COMMIT;
