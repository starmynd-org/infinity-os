-- migration 64: A SETTLER PROVES IT HELD THE LEASE, with a secret the server issued and nobody can
-- read back out of the table.
--
-- Packet R02, repair R02-REPAIR-01, final item. Written 2026-09-06 by Terminal 04.
-- Ledger version 64, released by Terminal 03 (its capture journal is `ingest.*` and takes no
-- number) and re-taken by R01. 0063 stays reserved for the contract/workspace slice.
--
-- ------------------------------------------------------------------ WHAT WAS STILL WRONG
--
-- Migration 62 made a settle require `settled_by`, and required it to equal the lease holder's
-- name. That closed the stranger case CAP14 found: a session that never reserved the attempt and
-- names itself something else is refused. It did NOT close impersonation, and I said so in the
-- handoff rather than leaving it to be discovered: the holder's name is a plain string sitting in
-- a table any runtime process can read, so anything that can read `brain.execution_lease` can
-- settle any attempt by claiming to be the holder. A guard you defeat by copying a value out of
-- the row it guards is a speed bump.
--
-- ------------------------------------------------------------------ WHAT A SECRET BUYS, EXACTLY
--
-- Acquiring a lease now mints a random secret. The TABLE STORES ONLY ITS SHA-256, so reading every
-- lease row tells you nothing you can settle with. The plaintext is handed to the acquiring
-- transaction once, through a transaction-local setting that no other session can read, and the
-- worker keeps it for as long as it holds the lease. Settling presents it and the digest is
-- compared.
--
-- So impersonation stops being "know the holder's name" and becomes "possess a value that was
-- shown once to the process that acquired the lease". That is a real boundary and it is still a
-- BEARER TOKEN: a worker that leaks its secret, logs it, or hands it to a child process has handed
-- over the ability to settle. This migration does not fix that and does not pretend to. The
-- alternative -- binding to `pg_backend_pid()` -- is unforgeable from another connection and
-- breaks the honest case, because a worker that reconnects can no longer settle its own effect,
-- and pids are reused. A secret survives a reconnect. That trade is the reason for this shape.
--
-- Built entirely on Postgres 16 built-ins: `gen_random_uuid()`, `sha256()`, `set_config(..., true)`.
-- No pgcrypto, nothing to install on the customer's host.

BEGIN;

ALTER TABLE brain.execution_lease ADD COLUMN IF NOT EXISTS settle_secret_sha256 text;

-- THE SECRET IS MINTED BY THE SERVER, never supplied by the caller, for the same reason the fencing
-- token is: a secret the client chooses is a secret the client can reuse. The plaintext leaves
-- through a transaction-local GUC, which is visible to the acquiring transaction and to nothing
-- else -- not to another session, and not after this transaction ends.
CREATE OR REPLACE FUNCTION brain.execution_lease_issue_token() RETURNS trigger
LANGUAGE plpgsql AS $fn$
DECLARE
  secret text;
BEGIN
  NEW.fencing_token := nextval('brain.fencing_token_seq');
  secret := gen_random_uuid()::text || gen_random_uuid()::text;
  NEW.settle_secret_sha256 := encode(sha256(secret::bytea), 'hex');
  PERFORM set_config('brain.lease_secret', secret, true);   -- true = transaction-local
  RETURN NEW;
END;
$fn$;

-- THE SETTLE CHECK. The secret is presented the same way it was issued: a transaction-local
-- setting, so it never travels in a column, an index or a log line of the statement.
--
-- BACKWARD BEHAVIOUR IS STATED RATHER THAN ASSUMED. A lease created before this migration has no
-- digest. Such a lease falls back to migration 62's name check, which is weaker, and the refusal
-- message says which rule it applied. Silently accepting a name for a lease that HAS a digest would
-- be the whole repair undone by a fallback, so that case is refused.
CREATE OR REPLACE FUNCTION brain.effect_attempt_settles_once() RETURNS trigger
LANGUAGE plpgsql AS $fn$
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
     OR NEW.fencing_token IS DISTINCT FROM OLD.fencing_token
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

  SELECT max(l2.fencing_token) INTO newest
    FROM brain.execution_lease l2
    JOIN brain.execution_lease l1 ON l1.work_item_id = l2.work_item_id
   WHERE l1.lease_id = OLD.lease_id;
  IF newest IS DISTINCT FROM OLD.fencing_token THEN
    RAISE EXCEPTION
      'settle refused: attempt % was reserved at epoch % and the work item is now at %. A '
      'superseded holder does not get to write the outcome; the takeover already recorded that '
      'this effect''s fate is unknown.', OLD.attempt_seq, OLD.fencing_token, newest
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
$fn$;

-- The digest column is readable, which is the point of storing a digest rather than the secret.
GRANT SELECT, INSERT, UPDATE ON brain.execution_lease TO brain_runtime;

-- ------------------------------------------------------------------ EVERY REFUSAL WATCHED HAPPENING
--
-- Eight checks: FIVE refusals watched refusing and THREE positive controls. Both counts asserted.
DO $$
DECLARE
  n int := 0; refusals int := 0; controls int := 0;
  who text; made_human boolean := false;
  item text; l1 bigint; t1 bigint; g_ok bigint; a1 bigint;
  ws text := 'ws-mv64-selfcheck';   -- 63 runs BEFORE this file and requires one
  secret text; digest_now text;
  seq_last bigint; seq_called boolean;
BEGIN
  SELECT last_value, is_called INTO seq_last, seq_called FROM brain.item_id_seq;
  SELECT human INTO who FROM brain.human_role ORDER BY human LIMIT 1;
  IF who IS NULL THEN
    who := '_mv64_fixture_human';
    INSERT INTO brain.human_role (role_name, human, granted_at, granted_by)
      VALUES ('brain_runtime', who, now(), 'migration 64 fixture');
    made_human := true;
  END IF;

  INSERT INTO brain.work_item (title, lane) VALUES ('migration 64 self-check', 'mv')
    RETURNING id INTO item;
  -- WORKSPACE, BECAUSE MIGRATION 63 RUNS BEFORE THIS ONE. Found on a fresh `create`, not on a
  -- `migrate`: 63 was applied to a store that already had 64, so this insert had no constraint to
  -- violate and the file looked correct. On a clean build the order is 61, 62, 63, 64 and the
  -- constraint is already there. That is the "passes on the store you are looking at, breaks on the
  -- one that ships" shape this repo keeps meeting, and it is why every migration is proven on a
  -- store dropped and rebuilt rather than migrated forward.
  INSERT INTO brain.execution_lease (work_item_id, workspace, holder, expires_at, fencing_token)
    VALUES (item, ws, who, now() + interval '5 minutes', 0)
    RETURNING lease_id, fencing_token INTO l1, t1;
  secret := current_setting('brain.lease_secret', true);

  -- 1 control: the secret was issued and handed to this transaction.
  IF secret IS NULL OR length(secret) < 32 THEN
    RAISE EXCEPTION 'migration 64: acquiring a lease issued no settle secret';
  END IF;
  n := n + 1; controls := controls + 1;

  -- 2 control: THE TABLE HOLDS ONLY THE DIGEST, which is what the whole design rests on.
  SELECT settle_secret_sha256 INTO digest_now FROM brain.execution_lease WHERE lease_id = l1;
  IF digest_now IS NULL OR digest_now = secret
     OR digest_now IS DISTINCT FROM encode(sha256(secret::bytea), 'hex') THEN
    RAISE EXCEPTION 'migration 64: the stored value is not the digest of the issued secret';
  END IF;
  n := n + 1; controls := controls + 1;

  INSERT INTO brain.authority_grant (granted_by, workspace, subject_kind, subject, capability,
                                     scope, expires_at, evidence)
    VALUES (who, ws, 'human', who, 'effect.external', 'lane/mv64', now() + interval '1 hour',
            'migration 64 self-check') RETURNING grant_seq INTO g_ok;
  INSERT INTO brain.effect_attempt (idempotency_key, lease_id, fencing_token, description,
                                    workspace, subject, capability, scope, grant_seq)
    VALUES ('mv64-a', l1, t1, 'an effect to settle', ws, who, 'effect.external', 'lane/mv64', g_ok)
    RETURNING attempt_seq INTO a1;

  -- 3 refusal: THE NAME IS NO LONGER ENOUGH. Migration 62 would have allowed this.
  PERFORM set_config('brain.settle_secret', '', true);
  BEGIN
    UPDATE brain.effect_attempt SET settled_at = now(), outcome = 'succeeded',
           result_ref = 'r', settled_by = who WHERE attempt_seq = a1;
    RAISE EXCEPTION 'migration 64: settling by name alone was NOT refused';
  EXCEPTION WHEN restrict_violation THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 4 refusal: a wrong secret.
  PERFORM set_config('brain.settle_secret', gen_random_uuid()::text, true);
  BEGIN
    UPDATE brain.effect_attempt SET settled_at = now(), outcome = 'succeeded',
           result_ref = 'r', settled_by = who WHERE attempt_seq = a1;
    RAISE EXCEPTION 'migration 64: settling with a wrong secret was NOT refused';
  EXCEPTION WHEN restrict_violation THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 5 refusal: THE DIGEST IS NOT THE SECRET. Anyone may read the digest; presenting it must fail,
  --   or storing a digest instead of the secret bought nothing at all.
  PERFORM set_config('brain.settle_secret', digest_now, true);
  BEGIN
    UPDATE brain.effect_attempt SET settled_at = now(), outcome = 'succeeded',
           result_ref = 'r', settled_by = who WHERE attempt_seq = a1;
    RAISE EXCEPTION 'migration 64: presenting the stored digest was NOT refused';
  EXCEPTION WHEN restrict_violation THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 6 control: the right secret settles, and the name it records is whatever the settler said.
  --   The secret is the proof; the name is now only a label on the record.
  PERFORM set_config('brain.settle_secret', secret, true);
  UPDATE brain.effect_attempt SET settled_at = now(), outcome = 'succeeded',
         result_ref = 'provider-1', settled_by = 'the-worker-process' WHERE attempt_seq = a1;
  IF (SELECT settled_by FROM brain.effect_attempt WHERE attempt_seq = a1)
     IS DISTINCT FROM 'the-worker-process' THEN
    RAISE EXCEPTION 'migration 64: the settler name was not recorded';
  END IF;
  n := n + 1; controls := controls + 1;

  -- 7 refusal: settling twice is still refused, secret or no secret.
  BEGIN
    UPDATE brain.effect_attempt SET settled_at = now(), outcome = 'failed', settled_by = who
     WHERE attempt_seq = a1;
    RAISE EXCEPTION 'migration 64: a settled attempt was re-settled';
  EXCEPTION WHEN restrict_violation THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 8 refusal, of a different shape: the issued secret must not have been disturbed by any of the
  --   settings above. A GUC that other code can overwrite is not a channel you can trust.
  IF current_setting('brain.lease_secret', true) IS DISTINCT FROM secret THEN
    RAISE EXCEPTION 'migration 64: the issued lease secret changed mid-transaction';
  END IF;
  n := n + 1; refusals := refusals + 1;

  ALTER TABLE brain.effect_attempt DISABLE TRIGGER effect_attempt_settles_once;
  ALTER TABLE brain.execution_lease DISABLE TRIGGER execution_lease_release_only;
  ALTER TABLE brain.execution_lease DISABLE TRIGGER execution_lease_halts_its_effects;
  ALTER TABLE brain.authority_grant DISABLE TRIGGER authority_grant_append_only;
  DELETE FROM brain.effect_attempt WHERE idempotency_key LIKE 'mv64-%';
  DELETE FROM brain.execution_lease WHERE work_item_id = item;
  DELETE FROM brain.authority_grant WHERE grant_seq = g_ok;
  ALTER TABLE brain.authority_grant ENABLE TRIGGER authority_grant_append_only;
  ALTER TABLE brain.execution_lease ENABLE TRIGGER execution_lease_halts_its_effects;
  ALTER TABLE brain.execution_lease ENABLE TRIGGER execution_lease_release_only;
  ALTER TABLE brain.effect_attempt ENABLE TRIGGER effect_attempt_settles_once;
  DELETE FROM brain.work_item WHERE id = item;
  PERFORM setval('brain.item_id_seq', seq_last, seq_called);
  IF made_human THEN
    DELETE FROM brain.human_role WHERE human = who AND granted_by = 'migration 64 fixture';
  END IF;

  IF n <> 8 OR refusals <> 5 OR controls <> 3 THEN
    RAISE EXCEPTION 'migration 64: expected 8 checks as 5 refusals and 3 controls, ran % as % and %',
      n, refusals, controls;
  END IF;
  RAISE NOTICE
    'migration 64: % of 8 checks passed, % refusals watched refusing and % positive controls. '
    'The lease table stores digests only.', n, refusals, controls;
END $$;

INSERT INTO brain.schema_migration (version, name)
  VALUES (64, '0064_a_settler_proves_it_held_the_lease')
  ON CONFLICT (version) DO NOTHING;

COMMIT;
