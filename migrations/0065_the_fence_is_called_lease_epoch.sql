-- migration 65: the fence is called `lease_epoch`, the contract's name, and it says who issued it.
--
-- Packet R02, contract alignment. Written 2026-09-07 by Terminal 04 against pinned contract
-- C01-PIN-01 v0.1.0-draft.5 (commit 0f5909a, digest sha256:fb42d422a7ae834c...ddcb6a10).
-- Ledger version 65.
--
-- ------------------------------------------------------------------ WHY RENAME AT ALL
--
-- `Receipt.execution.lease_epoch` was in the contract before the Lease object existed, so when
-- Terminal 04 filed the change request that created `lease.schema.json` it had to choose between
-- asking the contract to adopt the runtime's `fencing_token` or adopting the contract's name. The
-- request said: "The contract's name is better and I will rename to `lease_epoch` rather than ask
-- the contract to move." This is that promise, kept. Two vocabularies for one number is how a
-- receipt and the row it describes stop being joinable by anything but a human reading both.
--
-- ------------------------------------------------------------------ LS-I3 AND LS-I4
--
-- The pinned object states four invariants. Two were already true here and are now stated in the
-- schema rather than only in a trigger:
--
--   LS-I3  the epoch is server-issued and monotonic, and `epoch_issued_by` is the constant
--          `server`. A record claiming any other issuer is rejected. Migration 58 already
--          overwrote a caller-supplied token, so the behaviour is unchanged; what is new is that
--          the ROW now says who issued it, which is what a consumer reading the row can check.
--
--   LS-I4  `expires_at` is strictly after `acquired_at` (already enforced since 58), and
--          `released_at` is never before `acquired_at` (new here). A lease released before it was
--          acquired is not a lease anybody held.
--
-- ------------------------------------------------------------------ HOW THE FUNCTIONS ARE CARRIED
--
-- The four trigger functions below are NOT retyped from migrations 58 to 64. They were dumped from
-- the live store with `pg_get_functiondef` and had one identifier substituted. Retyping them by
-- hand would have re-introduced whichever earlier version I happened to copy, and this file lands
-- after four migrations that each replaced one of them: 62 rewrote the reserve path, 63 added the
-- workspace binding, 64 rewrote the settle check. A rename that quietly reverted the workspace
-- check would be the worst possible outcome of a cosmetic change.

BEGIN;

-- THE SEQUENCE MOVES WITH THE COLUMN. The substitution that carried the four functions forward
-- rewrote their `nextval('brain.fencing_token_seq')` to `lease_epoch_seq`, and the build caught the
-- dangling reference on the next `create`. Renaming the sequence is the right resolution rather
-- than reverting that one line: leaving a `fencing_token_seq` behind a `lease_epoch` column is the
-- two-vocabularies problem this migration exists to end, one object further down. Grants follow the
-- object rather than the name, so the USAGE grants from 58 and 62 are unaffected.
ALTER SEQUENCE brain.fencing_token_seq RENAME TO lease_epoch_seq;

ALTER TABLE brain.execution_lease RENAME COLUMN fencing_token TO lease_epoch;
ALTER TABLE brain.effect_attempt  RENAME COLUMN fencing_token TO lease_epoch;

ALTER TABLE brain.execution_lease
  ADD COLUMN IF NOT EXISTS epoch_issued_by text NOT NULL DEFAULT 'server';

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'execution_lease_epoch_is_server_issued_ck') THEN
    ALTER TABLE brain.execution_lease ADD CONSTRAINT execution_lease_epoch_is_server_issued_ck
      CHECK (epoch_issued_by = 'server');
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'execution_lease_released_after_acquired_ck') THEN
    ALTER TABLE brain.execution_lease ADD CONSTRAINT execution_lease_released_after_acquired_ck
      CHECK (released_at IS NULL OR released_at >= acquired_at);
  END IF;
END $$;

-- The view is recreated so its OWN output column carries the new name. Postgres rewrites a view's
-- internal reference on rename, by attribute number, but the column it publishes keeps the name it
-- was created with, and a consumer selecting `fencing_token` from this view would keep working
-- while the table underneath had moved on. That is exactly the two-vocabularies problem again, one
-- layer up.
DROP VIEW IF EXISTS brain.execution_lease_live;
CREATE VIEW brain.execution_lease_live AS
SELECT l.*
  FROM brain.execution_lease l
 WHERE l.released_at IS NULL
   AND l.expires_at > now()
   AND l.lease_epoch = (SELECT max(l2.lease_epoch) FROM brain.execution_lease l2
                         WHERE l2.work_item_id = l.work_item_id
                           AND l2.workspace IS NOT DISTINCT FROM l.workspace);

GRANT SELECT ON brain.execution_lease_live TO brain_runtime, brain_subscriber;

-- AND `effect_unresolved` TOO, which I missed on the first pass and my own check 1 caught.
--
-- Worth leaving in the file rather than fixing silently: the header above explains that a view
-- publishes the column name it was CREATED with, and that a consumer selecting the old name would
-- keep working while the table moved on. I recreated `execution_lease_live` for exactly that reason
-- and then forgot this one, which is built as `SELECT a.*` over `effect_attempt`. The assertion
-- that the old name exists NOWHERE in the schema is what found it. A check that only looked at the
-- two tables would have passed and shipped a view still publishing `fencing_token`.
DROP VIEW IF EXISTS brain.effect_unresolved;
CREATE VIEW brain.effect_unresolved AS
SELECT a.*,
       CASE WHEN a.settled_at IS NULL THEN 'reserved-never-settled'
            WHEN a.outcome = 'halted' THEN 'halted-by-takeover'
            ELSE 'ambiguous' END AS why
  FROM brain.effect_attempt a
 WHERE a.settled_at IS NULL
    OR (a.outcome IN ('ambiguous', 'halted')
        AND NOT EXISTS (SELECT 1 FROM brain.effect_reconciliation r
                         WHERE r.attempt_seq = a.attempt_seq
                           AND r.finding <> 'still-unknown'));

GRANT SELECT ON brain.effect_unresolved TO brain_runtime, brain_subscriber;

-- ------------------------------------------------------------ the four functions, carried forward

CREATE OR REPLACE FUNCTION brain.effect_attempt_may_reserve()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
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

  SELECT lease_epoch INTO live_token FROM brain.execution_lease_live
   WHERE lease_id = NEW.lease_id;
  IF live_token IS NULL THEN
    RAISE EXCEPTION
      'effect refused: lease % is not live. It was released, has expired, or has been superseded '
      'by a newer lease on work item %.', NEW.lease_id, coalesce(item, '?')
      USING ERRCODE = 'restrict_violation';
  END IF;
  IF NEW.lease_epoch IS DISTINCT FROM live_token THEN
    RAISE EXCEPTION
      'effect refused: the attempt presents fencing token % and lease % currently holds %. '
      'A stale token is a worker acting on a world that has moved.',
      NEW.lease_epoch, NEW.lease_id, live_token USING ERRCODE = 'restrict_violation';
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
$function$
;
CREATE OR REPLACE FUNCTION brain.effect_attempt_settles_once()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
DECLARE
  lease_holder text;
  lease_digest text;
  newest       bigint;
  presented    text;
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'brain.effect_attempt is the record that an effect was attempted: DELETE refused'
      USING ERRCODE = 'restrict_violation';
  END IF;
  IF OLD.settled_at IS NOT NULL THEN
    RAISE EXCEPTION
      'attempt % was settled at % as %. Later information is a reconciliation, not a rewrite: '
      'insert into brain.effect_reconciliation.', OLD.attempt_seq, OLD.settled_at, OLD.outcome
      USING ERRCODE = 'restrict_violation';
  END IF;
  IF NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key
     OR NEW.lease_id      IS DISTINCT FROM OLD.lease_id
     OR NEW.lease_epoch IS DISTINCT FROM OLD.lease_epoch
     OR NEW.description   IS DISTINCT FROM OLD.description
     OR NEW.reserved_at   IS DISTINCT FROM OLD.reserved_at
     OR NEW.subject       IS DISTINCT FROM OLD.subject
     OR NEW.capability    IS DISTINCT FROM OLD.capability
     OR NEW.scope         IS DISTINCT FROM OLD.scope
     OR NEW.grant_seq     IS DISTINCT FROM OLD.grant_seq THEN
    RAISE EXCEPTION 'brain.effect_attempt: the only legal update is to settle'
      USING ERRCODE = 'restrict_violation';
  END IF;
  IF NEW.settled_at IS NULL THEN
    RAISE EXCEPTION 'brain.effect_attempt: an update that settles nothing changes nothing'
      USING ERRCODE = 'restrict_violation';
  END IF;
  IF btrim(coalesce(NEW.settled_by, '')) = '' THEN
    RAISE EXCEPTION
      'settle refused: attempt % names no settler. An outcome nobody signed is an outcome nobody '
      'can be asked about.', OLD.attempt_seq USING ERRCODE = 'restrict_violation';
  END IF;

  -- The takeover halt in migration 62 is the store settling on its own behalf, and it holds no
  -- secret because it IS the server. It is named explicitly rather than left as a hole.
  IF NEW.settled_by = 'lease-release' THEN
    RETURN NEW;
  END IF;

  SELECT l.holder, l.settle_secret_sha256 INTO lease_holder, lease_digest
    FROM brain.execution_lease l WHERE l.lease_id = OLD.lease_id;

  SELECT max(l2.lease_epoch) INTO newest
    FROM brain.execution_lease l2
    JOIN brain.execution_lease l1 ON l1.work_item_id = l2.work_item_id
   WHERE l1.lease_id = OLD.lease_id;
  IF newest IS DISTINCT FROM OLD.lease_epoch THEN
    RAISE EXCEPTION
      'settle refused: attempt % was reserved at epoch % and the work item is now at %. A '
      'superseded holder does not get to write the outcome; the takeover already recorded that '
      'this effect''s fate is unknown.', OLD.attempt_seq, OLD.lease_epoch, newest
      USING ERRCODE = 'restrict_violation';
  END IF;

  IF lease_digest IS NULL THEN
    -- Pre-64 lease. Weaker rule, applied knowingly and named in the message.
    IF NEW.settled_by IS DISTINCT FROM lease_holder THEN
      RAISE EXCEPTION
        'settle refused: lease % predates migration 64 and carries no settle secret, so the weaker '
        'name check applies; it is held by % and % is trying to settle. Re-acquire the lease to '
        'get a secret.', OLD.lease_id, coalesce(lease_holder, '?'), NEW.settled_by
        USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
  END IF;

  presented := current_setting('brain.settle_secret', true);
  IF presented IS NULL OR presented = '' THEN
    RAISE EXCEPTION
      'settle refused: attempt % is under lease %, which carries a settle secret, and none was '
      'presented. Set brain.settle_secret for this transaction to the value acquire returned. '
      'Knowing the holder''s name is not evidence that you held the lease.',
      OLD.attempt_seq, OLD.lease_id USING ERRCODE = 'restrict_violation';
  END IF;
  IF encode(sha256(presented::bytea), 'hex') IS DISTINCT FROM lease_digest THEN
    RAISE EXCEPTION
      'settle refused: the secret presented for lease % does not match the one issued when it was '
      'acquired. This is the impersonation path migration 64 exists to close.', OLD.lease_id
      USING ERRCODE = 'restrict_violation';
  END IF;
  RETURN NEW;
END;
$function$
;
CREATE OR REPLACE FUNCTION brain.execution_lease_issue_token()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
DECLARE
  secret text;
BEGIN
  NEW.lease_epoch := nextval('brain.lease_epoch_seq');
  secret := gen_random_uuid()::text || gen_random_uuid()::text;
  NEW.settle_secret_sha256 := encode(sha256(secret::bytea), 'hex');
  PERFORM set_config('brain.lease_secret', secret, true);   -- true = transaction-local
  RETURN NEW;
END;
$function$
;
CREATE OR REPLACE FUNCTION brain.execution_lease_release_only()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'brain.execution_lease is history: DELETE refused. Release the lease instead.'
      USING ERRCODE = 'restrict_violation';
  END IF;
  IF OLD.released_at IS NOT NULL THEN
    RAISE EXCEPTION
      'lease % was already released at % (%). The first release is the one that happened.',
      OLD.lease_id, OLD.released_at, OLD.release_reason USING ERRCODE = 'restrict_violation';
  END IF;
  IF NEW.lease_id     IS DISTINCT FROM OLD.lease_id
     OR NEW.work_item_id  IS DISTINCT FROM OLD.work_item_id
     OR NEW.holder        IS DISTINCT FROM OLD.holder
     OR NEW.acquired_at   IS DISTINCT FROM OLD.acquired_at
     OR NEW.expires_at    IS DISTINCT FROM OLD.expires_at
     OR NEW.lease_epoch IS DISTINCT FROM OLD.lease_epoch THEN
    RAISE EXCEPTION
      'brain.execution_lease: the only legal update is to release. Extending, re-pointing or '
      're-holding a lease is a NEW lease, so that the record still says who could act when.'
      USING ERRCODE = 'restrict_violation';
  END IF;
  IF NEW.released_at IS NULL THEN
    RAISE EXCEPTION 'brain.execution_lease: an update that releases nothing changes nothing'
      USING ERRCODE = 'restrict_violation';
  END IF;
  RETURN NEW;
END;
$function$
;


-- ------------------------------------------------------------------ EVERY REFUSAL WATCHED HAPPENING
--
-- Seven checks: FOUR refusals watched refusing and THREE positive controls. Both counts asserted.
DO $$
DECLARE
  n int := 0; refusals int := 0; controls int := 0;
  who text; made_human boolean := false;
  ws text := 'ws-mv65-selfcheck';
  item text; l1 bigint; e1 bigint; g_ok bigint; g_here bigint;
  seq_last bigint; seq_called boolean;
BEGIN
  SELECT last_value, is_called INTO seq_last, seq_called FROM brain.item_id_seq;
  SELECT human INTO who FROM brain.human_role ORDER BY human LIMIT 1;
  IF who IS NULL THEN
    who := '_mv65_fixture_human';
    INSERT INTO brain.human_role (role_name, human, granted_at, granted_by)
      VALUES ('brain_runtime', who, now(), 'migration 65 fixture');
    made_human := true;
  END IF;
  INSERT INTO brain.work_item (title, lane) VALUES ('migration 65 self-check', 'mv')
    RETURNING id INTO item;

  -- 1 control: the column is called lease_epoch now, on both tables, and the old name is gone.
  IF EXISTS (SELECT 1 FROM information_schema.columns
              WHERE table_schema = 'brain' AND column_name = 'fencing_token')
     OR NOT EXISTS (SELECT 1 FROM information_schema.columns
                     WHERE table_schema = 'brain' AND table_name = 'execution_lease'
                       AND column_name = 'lease_epoch')
     OR NOT EXISTS (SELECT 1 FROM information_schema.columns
                     WHERE table_schema = 'brain' AND table_name = 'effect_attempt'
                       AND column_name = 'lease_epoch') THEN
    RAISE EXCEPTION 'migration 65: the rename did not land on both tables';
  END IF;
  n := n + 1; controls := controls + 1;

  -- 2 control: a lease acquires, and the epoch is still server-issued and still not the caller's.
  INSERT INTO brain.execution_lease (work_item_id, workspace, holder, expires_at, lease_epoch)
    VALUES (item, ws, who, now() + interval '5 minutes', 0)
    RETURNING lease_id, lease_epoch INTO l1, e1;
  IF e1 IS NULL OR e1 = 0 THEN
    RAISE EXCEPTION 'migration 65: the caller-supplied epoch survived the rename';
  END IF;
  IF (SELECT epoch_issued_by FROM brain.execution_lease WHERE lease_id = l1) <> 'server' THEN
    RAISE EXCEPTION 'migration 65: LS-I3, the row does not say the server issued the epoch';
  END IF;
  n := n + 1; controls := controls + 1;

  -- 3 refusal: LS-I3. A row claiming any other issuer is rejected.
  BEGIN
    INSERT INTO brain.execution_lease (work_item_id, workspace, holder, expires_at, lease_epoch,
                                       epoch_issued_by)
      VALUES (item, 'ws-mv65-other', who, now() + interval '5 minutes', 0, 'the-client');
    RAISE EXCEPTION 'migration 65: a lease claiming a non-server issuer was NOT refused';
  EXCEPTION WHEN check_violation THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 4 refusal: LS-I4. Released before acquired is not a lease anybody held.
  BEGIN
    UPDATE brain.execution_lease
       SET released_at = acquired_at - interval '1 second', release_reason = 'before it began'
     WHERE lease_id = l1;
    RAISE EXCEPTION 'migration 65: a release before acquisition was NOT refused';
  EXCEPTION WHEN check_violation OR restrict_violation THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 5 control: THE CARRIED FUNCTIONS STILL CARRY WHAT 62, 63 AND 64 PUT IN THEM. An effect naming
  --   no licensing authority is still refused, which is migration 62's property surviving a
  --   cosmetic rename that recreated the function it lives in.
  BEGIN
    INSERT INTO brain.effect_attempt (idempotency_key, lease_id, lease_epoch, description)
      VALUES ('mv65-unbound', l1, e1, 'no authority named');
    RAISE EXCEPTION 'migration 65: the reserve path lost migration 62 in the rename';
  EXCEPTION WHEN restrict_violation THEN n := n + 1; controls := controls + 1;
  END;

  -- 6 refusal: and migration 63's workspace binding survived too.
  INSERT INTO brain.authority_grant (granted_by, workspace, subject_kind, subject, capability,
                                     scope, expires_at, evidence)
    VALUES (who, 'ws-mv65-elsewhere', 'human', who, 'effect.external', 'lane/mv65',
            now() + interval '1 hour', 'migration 65') RETURNING grant_seq INTO g_ok;
  BEGIN
    INSERT INTO brain.effect_attempt (idempotency_key, lease_id, lease_epoch, description,
                                      workspace, subject, capability, scope, grant_seq)
      VALUES ('mv65-cross', l1, e1, 'licensed from another workspace',
              ws, who, 'effect.external', 'lane/mv65', g_ok);
    RAISE EXCEPTION 'migration 65: the rename lost migration 63 workspace binding';
  EXCEPTION WHEN restrict_violation THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 7 refusal: and migration 64's settle secret survived, since 64 rewrote the settle trigger and
  --   this file recreated it.
  --
  -- THE SUBJECT IS BUILT FIRST, AND ITS EXISTENCE IS ASSERTED. The first version of this check
  -- updated `WHERE lease_id = l1` after checks 5 and 6 had both been refused, so there was no
  -- attempt row at all: the UPDATE matched zero rows, succeeded trivially, and the check reported
  -- that migration 64 had been lost. A verdict over an empty set, inside a block written to catch
  -- exactly that. It would also have passed vacuously had the settle guard genuinely been broken.
  INSERT INTO brain.authority_grant (granted_by, workspace, subject_kind, subject, capability,
                                     scope, expires_at, evidence)
    VALUES (who, ws, 'human', who, 'effect.external', 'lane/mv65-ok',
            now() + interval '1 hour', 'migration 65') RETURNING grant_seq INTO g_here;
  INSERT INTO brain.effect_attempt (idempotency_key, lease_id, lease_epoch, description,
                                    workspace, subject, capability, scope, grant_seq)
    VALUES ('mv65-settleable', l1, e1, 'a real attempt to try to settle',
            ws, who, 'effect.external', 'lane/mv65-ok', g_here);
  IF (SELECT count(*) FROM brain.effect_attempt WHERE idempotency_key = 'mv65-settleable') <> 1 THEN
    RAISE EXCEPTION 'migration 65: check 7 has no subject, so its verdict would be over nothing';
  END IF;
  BEGIN
    UPDATE brain.effect_attempt SET settled_at = now(), outcome = 'succeeded', result_ref = 'r',
           settled_by = who WHERE idempotency_key = 'mv65-settleable';
    RAISE EXCEPTION 'migration 65: the rename lost migration 64 settle authority';
  EXCEPTION WHEN restrict_violation OR check_violation THEN n := n + 1; refusals := refusals + 1;
  END;

  ALTER TABLE brain.effect_attempt DISABLE TRIGGER effect_attempt_settles_once;
  ALTER TABLE brain.execution_lease DISABLE TRIGGER execution_lease_release_only;
  ALTER TABLE brain.execution_lease DISABLE TRIGGER execution_lease_halts_its_effects;
  ALTER TABLE brain.authority_grant DISABLE TRIGGER authority_grant_append_only;
  DELETE FROM brain.effect_attempt WHERE idempotency_key LIKE 'mv65-%';
  DELETE FROM brain.execution_lease WHERE work_item_id = item;
  DELETE FROM brain.authority_grant WHERE grant_seq IN (g_ok, g_here);
  ALTER TABLE brain.authority_grant ENABLE TRIGGER authority_grant_append_only;
  ALTER TABLE brain.execution_lease ENABLE TRIGGER execution_lease_halts_its_effects;
  ALTER TABLE brain.execution_lease ENABLE TRIGGER execution_lease_release_only;
  ALTER TABLE brain.effect_attempt ENABLE TRIGGER effect_attempt_settles_once;
  DELETE FROM brain.work_item WHERE id = item;
  PERFORM setval('brain.item_id_seq', seq_last, seq_called);
  IF made_human THEN
    DELETE FROM brain.human_role WHERE human = who AND granted_by = 'migration 65 fixture';
  END IF;

  IF n <> 7 OR refusals <> 4 OR controls <> 3 THEN
    RAISE EXCEPTION 'migration 65: expected 7 checks as 4 refusals and 3 controls, ran % as % and %',
      n, refusals, controls;
  END IF;
  RAISE NOTICE
    'migration 65: % of 7 checks passed, % refusals watched refusing and % positive controls. '
    'The fence is lease_epoch, server-issued, and 62/63/64 survived the rename.',
    n, refusals, controls;
END $$;

INSERT INTO brain.schema_migration (version, name)
  VALUES (65, '0065_the_fence_is_called_lease_epoch')
  ON CONFLICT (version) DO NOTHING;

COMMIT;
