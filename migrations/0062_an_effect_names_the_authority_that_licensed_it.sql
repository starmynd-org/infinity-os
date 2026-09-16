-- migration 62: AN EFFECT NAMES THE AUTHORITY THAT LICENSED IT, a reserve locks the lease it is
-- reserving under, a takeover halts what the previous holder left in flight, and a settle is made
-- by somebody entitled to make it.
--
-- Packet R02, repair R02-REPAIR-01. Written 2026-09-06 by Terminal 04 after CAP14-REV-017 and its
-- runnable harness CAP14-REV-017b (`waves/handoffs/CAP14/candidate-probes/race_r02.py`), which
-- demonstrated all three defects FAILING against f059037 on a disposable store.
-- Ledger version 62. (The contract/workspace slice moves to 63.)
--
-- ------------------------------------------------------------------ WHAT WAS WRONG, AND MEASURED
--
-- 1. THE FENCE WAS NOT DECIDABLE. `effect_attempt_may_reserve` read `brain.execution_lease_live`
--    with a plain SELECT and held nothing. Nothing in this repository sets an isolation level
--    (`store/transitions.py` opens every write at the default), so that read happens at READ
--    COMMITTED under a statement snapshot. A takeover committing between the read and the
--    reserving transaction's commit left BOTH reservations standing. CAP14 observed two.
--
-- 2. THE REVOKE WINDOW WAS OPEN. `store/authority.py` checked authority in one statement and the
--    effect row was written in the next. Under READ COMMITTED each statement takes its own
--    snapshot, so a revocation committing between them was invisible to the check and did not stop
--    the write. CAP14 observed one effect row written after the grant was revoked. Passing `ctx`
--    was still right -- one transaction, one rollback -- but the comment claiming it made the two
--    "one snapshot" was wrong, and this migration is why it can now be true.
--
-- 3. A STRANGER COULD SETTLE. `brain.effect_attempt` recorded no subject, capability, scope or
--    grant, and nothing said who was allowed to settle an attempt. Any session could close another
--    worker's genuinely unresolved effect as succeeded with an invented reference, which removes it
--    from `brain.effect_unresolved` and therefore from human review. That is a partial or unknown
--    outcome reported as complete, which R02's own acceptance forbids.
--
-- ------------------------------------------------------------------ THE JOIN, NOT THE NARRATIVE
--
-- The deepest of the three is 3, and it is not really about settling. An effect that does not name
-- the authority that licensed it cannot be audited afterwards: the check happened, and left no
-- trace, so "which grant permitted this?" was answerable only by reading logs and believing them.
-- `.claude/rules/result-and-escalation-contract.md` asks for a join rather than a narrative, and
-- migration 57 already does this properly on the grant side -- `authority_grant_actor_is_known`
-- binds `granted_by` to the roster. The reserve path had no equivalent. Now it does, and the
-- binding is checked against the grant rather than copied from the caller's opinion of itself.

BEGIN;

-- One more capability, requested by Terminal 03's rule engine, which needs a class between
-- "no authority required" and "an effect outside this store".
INSERT INTO brain.capability (capability, description) VALUES
  ('effect.internal', 'cause an effect inside this store that is not merely reading')
ON CONFLICT (capability) DO NOTHING;

ALTER TABLE brain.effect_attempt ADD COLUMN IF NOT EXISTS subject     text;
ALTER TABLE brain.effect_attempt ADD COLUMN IF NOT EXISTS capability  text;
ALTER TABLE brain.effect_attempt ADD COLUMN IF NOT EXISTS scope       text;
ALTER TABLE brain.effect_attempt ADD COLUMN IF NOT EXISTS grant_seq   bigint;
ALTER TABLE brain.effect_attempt ADD COLUMN IF NOT EXISTS settled_by  text;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'effect_attempt_grant_fk') THEN
    ALTER TABLE brain.effect_attempt ADD CONSTRAINT effect_attempt_grant_fk
      FOREIGN KEY (grant_seq) REFERENCES brain.authority_grant(grant_seq);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'effect_attempt_capability_fk') THEN
    ALTER TABLE brain.effect_attempt ADD CONSTRAINT effect_attempt_capability_fk
      FOREIGN KEY (capability) REFERENCES brain.capability(capability);
  END IF;
  -- NOT VALID, for the reason migration 61 gives at length: rows written before this cannot be
  -- given a licensing authority retroactively without inventing one, and Postgres enforces a
  -- NOT VALID constraint on every new and updated row while tolerating the old ones.
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'effect_attempt_names_its_authority_ck') THEN
    ALTER TABLE brain.effect_attempt ADD CONSTRAINT effect_attempt_names_its_authority_ck
      CHECK (subject IS NOT NULL AND btrim(subject) <> ''
             AND capability IS NOT NULL
             AND scope IS NOT NULL AND btrim(scope) <> ''
             AND grant_seq IS NOT NULL) NOT VALID;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'effect_attempt_settler_is_named_ck') THEN
    ALTER TABLE brain.effect_attempt ADD CONSTRAINT effect_attempt_settler_is_named_ck
      CHECK (settled_at IS NULL OR btrim(coalesce(settled_by, '')) <> '') NOT VALID;
  END IF;
  -- The contract's outcome vocabulary is wider than the three this table shipped with. `halted`
  -- is needed by this migration itself: it is what a takeover leaves behind.
  ALTER TABLE brain.effect_attempt DROP CONSTRAINT IF EXISTS effect_attempt_outcome_ck;
  ALTER TABLE brain.effect_attempt ADD CONSTRAINT effect_attempt_outcome_ck
    CHECK (outcome IS NULL OR outcome IN ('succeeded', 'failed', 'ambiguous', 'halted', 'cancelled'));
END $$;

-- THE RESERVE PATH, REWRITTEN. Three changes: it takes a lock before it reads, it re-checks the
-- licensing grant inside the same statement, and it verifies the binding rather than trusting it.
CREATE OR REPLACE FUNCTION brain.effect_attempt_may_reserve() RETURNS trigger
LANGUAGE plpgsql AS $fn$
DECLARE
  live_token bigint;
  item       text;
  g          record;
BEGIN
  -- A BOUNDED WAIT, SET BEFORE ANYTHING IS LOCKED. A reserve that blocks forever because another
  -- session is holding the lease rows in an open transaction is an outage dressed as correctness:
  -- the worker never returns, the effect never happens, and nothing says why. Five seconds is long
  -- enough for an honest overlapping commit and short enough that a stuck holder produces a clean,
  -- retryable `lock_not_available` instead of a hung process. SET LOCAL, so it ends with the
  -- transaction and cannot leak into whatever the connection does next.
  --
  -- Found the way these things are found: CAP14's harness ran one open transaction against another
  -- on a single thread, and the second statement waited for a commit that could not arrive until it
  -- returned. That particular deadlock is the scene's shape rather than the code's, but a runtime
  -- that can wait forever on a lock will eventually do it in production too.
  SET LOCAL lock_timeout = '5s';

  -- THE LOCK, AND IT COMES FIRST. Selecting the item and locking every lease row for it means a
  -- concurrent release must wait for this transaction rather than slipping between the read below
  -- and the commit. Without it the view read is a statement snapshot that stops being true the
  -- instant it is taken, which is defect 1.
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
      'by a newer lease on work item %. An effect reserved under a lease its holder no longer has '
      'is exactly what the fencing token exists to prevent.', NEW.lease_id, coalesce(item, '?')
      USING ERRCODE = 'restrict_violation';
  END IF;
  IF NEW.fencing_token IS DISTINCT FROM live_token THEN
    RAISE EXCEPTION
      'effect refused: the attempt presents fencing token % and lease % currently holds %. '
      'A stale token is a worker acting on a world that has moved.',
      NEW.fencing_token, NEW.lease_id, live_token USING ERRCODE = 'restrict_violation';
  END IF;

  -- THE LICENSING AUTHORITY, RE-CHECKED HERE. Whatever the caller checked a statement ago, this
  -- statement is where the row is written, and this is the only place that can see a revocation
  -- that committed in between.
  IF NEW.grant_seq IS NULL OR NEW.subject IS NULL OR NEW.capability IS NULL OR NEW.scope IS NULL THEN
    RAISE EXCEPTION
      'effect refused: the attempt names no licensing authority. An effect that does not record '
      'which grant permitted it cannot be audited afterwards, and "which authority allowed this" '
      'stops being a join and becomes a story.' USING ERRCODE = 'restrict_violation';
  END IF;
  SELECT * INTO g FROM brain.authority_in_force WHERE grant_seq = NEW.grant_seq;
  IF g.grant_seq IS NULL THEN
    RAISE EXCEPTION
      'effect refused: grant % is not in force. It was revoked or it expired, possibly a moment '
      'ago and after the caller checked it. The check is repeated here because that is the '
      'statement that writes the row.', NEW.grant_seq USING ERRCODE = 'restrict_violation';
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
        'effect refused: an open budget stop covers scope %/%. Reserve before you spend means the '
        'stop is consulted before the effect, not after the charge.',
        NEW.budget_scope_type, NEW.budget_scope_id USING ERRCODE = 'restrict_violation';
    END IF;
  END IF;
  RETURN NEW;
END;
$fn$;

-- A TAKEOVER HALTS WHAT THE PREVIOUS HOLDER LEFT IN FLIGHT.
--
-- Locking alone does not finish defect 1. Serialised, the honest sequence is: A reserves while it
-- legitimately holds the lease, B then takes the lease away. A's effect is now in an unknown state
-- -- it may have happened -- and leaving its reservation looking open beside B's new one is how one
-- work item ends up with two live effects and nobody able to say which ran.
--
-- So releasing a lease SETTLES its unsettled attempts as `halted`, which is exactly what is true:
-- the worker was stopped and nobody knows whether the effect landed. `halted` is not `failed`; it
-- goes into `brain.effect_unresolved` for a human, because a retry decided by this trigger would be
-- the blind re-execution R02's rollback rule forbids.
CREATE OR REPLACE FUNCTION brain.execution_lease_halts_its_effects() RETURNS trigger
LANGUAGE plpgsql AS $fn$
DECLARE
  halted int;
BEGIN
  IF OLD.released_at IS NULL AND NEW.released_at IS NOT NULL THEN
    UPDATE brain.effect_attempt
       SET settled_at = now(),
           outcome    = 'halted',
           settled_by = 'lease-release',
           detail     = coalesce(detail || ' | ', '')
                        || 'halted because lease ' || OLD.lease_id || ' was released: '
                        || coalesce(NEW.release_reason, 'no reason given')
                        || '. Whether the effect happened is UNKNOWN and a human must reconcile it.'
     WHERE lease_id = OLD.lease_id AND settled_at IS NULL;
    GET DIAGNOSTICS halted = ROW_COUNT;
    IF halted > 0 THEN
      RAISE NOTICE 'lease %: % in-flight effect(s) halted and left for reconciliation',
        OLD.lease_id, halted;
    END IF;
  END IF;
  RETURN NEW;
END;
$fn$;

DROP TRIGGER IF EXISTS execution_lease_halts_its_effects ON brain.execution_lease;
CREATE TRIGGER execution_lease_halts_its_effects AFTER UPDATE ON brain.execution_lease
  FOR EACH ROW EXECUTE FUNCTION brain.execution_lease_halts_its_effects();

-- A SETTLE IS MADE BY SOMEBODY ENTITLED TO MAKE IT.
--
-- `settled_by` is required, and it must be the holder of the lease the attempt was reserved under,
-- or the reserved marker the halt above uses. A caller that never reserved the attempt and holds no
-- lease is refused, which is defect 3.
--
-- WHAT THIS DELIBERATELY DOES NOT REQUIRE: that the lease still be live. A worker whose lease
-- expired while it was waiting on a slow provider must still be able to report honestly what
-- happened, and refusing that would convert every slow effect into a permanent unknown. Being
-- SUPERSEDED is different from being expired, and a superseded holder is refused, because by then
-- somebody else owns the work and the halt above has already recorded the truthful outcome.
CREATE OR REPLACE FUNCTION brain.effect_attempt_settles_once() RETURNS trigger
LANGUAGE plpgsql AS $fn$
DECLARE
  lease_holder text;   -- NOT `holder`: a variable named after the column it reads is ambiguous
  newest bigint;
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
  IF NEW.settled_by <> 'lease-release' THEN
    SELECT l.holder INTO lease_holder FROM brain.execution_lease l
      WHERE l.lease_id = OLD.lease_id;
    IF NEW.settled_by IS DISTINCT FROM lease_holder THEN
      RAISE EXCEPTION
        'settle refused: attempt % was reserved under lease % held by %, and % is trying to settle '
        'it. A stranger closing another worker''s unresolved effect removes it from human review '
        'while its real outcome is still unknown.',
        OLD.attempt_seq, OLD.lease_id, coalesce(lease_holder, '?'), NEW.settled_by
        USING ERRCODE = 'restrict_violation';
    END IF;
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
  END IF;
  RETURN NEW;
END;
$fn$;

-- `halted` JOINS `ambiguous` IN THE UNRESOLVED QUEUE. Migration 59's view knew two ways to be
-- unresolved: never settled, or settled ambiguous without a conclusion. `halted` is a third and it
-- is the one this migration creates: the worker was stopped mid-effect and whether the effect
-- landed is unknown. A halted attempt that dropped out of the queue would be a takeover quietly
-- closing the books on an effect nobody checked, which is the blind re-execution risk one step
-- earlier.
-- DROP then CREATE, not CREATE OR REPLACE. `a.*` is wider now that this migration added five
-- columns, so the trailing `why` shifts position and Postgres refuses to rename a view column in
-- place. Recreating it is the honest way to widen a `SELECT *` view.
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
GRANT SELECT ON brain.authority_in_force TO brain_runtime;

-- ------------------------------------------------------------------ EVERY REFUSAL WATCHED HAPPENING
--
-- Ten checks: SEVEN refusals watched refusing and THREE positive controls. Both counts asserted.
-- The concurrency properties themselves cannot be proven from inside one transaction -- they need
-- two sessions -- so they are proven by CAP14's harness and by engine/execution/test_race.py, and
-- this block proves the single-session preconditions those races depend on.
DO $$
DECLARE
  n int := 0; refusals int := 0; controls int := 0;
  who text; made_human boolean := false;
  item text; l1 bigint; t1 bigint; g_ok bigint; g_other bigint; a1 bigint;
  seq_last bigint; seq_called boolean;
BEGIN
  SELECT last_value, is_called INTO seq_last, seq_called FROM brain.item_id_seq;
  SELECT human INTO who FROM brain.human_role ORDER BY human LIMIT 1;
  IF who IS NULL THEN
    who := '_mv62_fixture_human';
    INSERT INTO brain.human_role (role_name, human, granted_at, granted_by)
      VALUES ('brain_runtime', who, now(), 'migration 62 fixture');
    made_human := true;
  END IF;

  INSERT INTO brain.work_item (title, lane) VALUES ('migration 62 self-check', 'mv')
    RETURNING id INTO item;
  INSERT INTO brain.execution_lease (work_item_id, holder, expires_at, fencing_token)
    VALUES (item, who, now() + interval '5 minutes', 0)
    RETURNING lease_id, fencing_token INTO l1, t1;
  INSERT INTO brain.authority_grant (granted_by, subject_kind, subject, capability, scope,
                                     expires_at, evidence)
    VALUES (who, 'human', who, 'effect.external', 'lane/mv62', now() + interval '1 hour',
            'migration 62 self-check') RETURNING grant_seq INTO g_ok;
  INSERT INTO brain.authority_grant (granted_by, subject_kind, subject, capability, scope,
                                     expires_at, evidence)
    VALUES (who, 'human', who, 'effect.spend', 'lane/other', now() + interval '1 hour',
            'migration 62 self-check') RETURNING grant_seq INTO g_other;

  -- 1 control: a bound reservation lands.
  INSERT INTO brain.effect_attempt (idempotency_key, lease_id, fencing_token, description,
                                    subject, capability, scope, grant_seq)
    VALUES ('mv62-ok', l1, t1, 'a licensed effect', who, 'effect.external', 'lane/mv62', g_ok)
    RETURNING attempt_seq INTO a1;
  n := n + 1; controls := controls + 1;

  -- 2 refusal: an effect naming no authority at all.
  BEGIN
    INSERT INTO brain.effect_attempt (idempotency_key, lease_id, fencing_token, description)
      VALUES ('mv62-unbound', l1, t1, 'an unlicensed effect');
    RAISE EXCEPTION 'migration 62: an effect naming no authority was NOT refused';
  EXCEPTION WHEN restrict_violation THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 3 refusal: a binding that does not match the grant it names.
  BEGIN
    INSERT INTO brain.effect_attempt (idempotency_key, lease_id, fencing_token, description,
                                      subject, capability, scope, grant_seq)
      VALUES ('mv62-mismatch', l1, t1, 'claiming more than the grant gives',
              who, 'effect.external', 'lane/mv62', g_other);
    RAISE EXCEPTION 'migration 62: a binding mismatched against its grant was NOT refused';
  EXCEPTION WHEN restrict_violation THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 4 refusal: a revoked grant cannot license an effect, checked in the writing statement.
  INSERT INTO brain.authority_revocation (grant_seq, revoked_by, reason)
    VALUES (g_other, who, 'migration 62 self-check');
  BEGIN
    INSERT INTO brain.effect_attempt (idempotency_key, lease_id, fencing_token, description,
                                      subject, capability, scope, grant_seq)
      VALUES ('mv62-revoked', l1, t1, 'licensed by a withdrawn grant',
              who, 'effect.spend', 'lane/other', g_other);
    RAISE EXCEPTION 'migration 62: an effect under a revoked grant was NOT refused';
  EXCEPTION WHEN restrict_violation THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 5 refusal: a settle that names nobody.
  BEGIN
    UPDATE brain.effect_attempt SET settled_at = now(), outcome = 'succeeded',
           result_ref = 'anonymous' WHERE attempt_seq = a1;
    RAISE EXCEPTION 'migration 62: a settle naming no settler was NOT refused';
  EXCEPTION WHEN restrict_violation THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 6 refusal: a stranger settling somebody else's attempt. This is CAP14 scene 3.
  BEGIN
    UPDATE brain.effect_attempt SET settled_at = now(), outcome = 'succeeded',
           result_ref = 'invented-by-a-stranger', settled_by = '_a_stranger'
     WHERE attempt_seq = a1;
    RAISE EXCEPTION 'migration 62: a stranger settling another holder''s attempt was NOT refused';
  EXCEPTION WHEN restrict_violation THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 7 control: the holder may settle its own attempt.
  UPDATE brain.effect_attempt SET settled_at = now(), outcome = 'succeeded',
         result_ref = 'provider-receipt-1', settled_by = who
   WHERE attempt_seq = a1;
  IF (SELECT outcome FROM brain.effect_attempt WHERE attempt_seq = a1) <> 'succeeded' THEN
    RAISE EXCEPTION 'migration 62: the holder could not settle its own attempt';
  END IF;
  n := n + 1; controls := controls + 1;

  -- 8 control: A TAKEOVER HALTS WHAT IS IN FLIGHT. This is the second half of CAP14 scene 1, and
  --   it is the part a lock alone does not give you.
  INSERT INTO brain.effect_attempt (idempotency_key, lease_id, fencing_token, description,
                                    subject, capability, scope, grant_seq)
    VALUES ('mv62-inflight', l1, t1, 'in flight when the lease was taken away',
            who, 'effect.external', 'lane/mv62', g_ok);
  UPDATE brain.execution_lease
     SET released_at = now(), release_reason = 'taken over in migration 62 self-check'
   WHERE lease_id = l1;
  IF (SELECT outcome FROM brain.effect_attempt WHERE idempotency_key = 'mv62-inflight')
     IS DISTINCT FROM 'halted' THEN
    RAISE EXCEPTION 'migration 62: a takeover did not halt the in-flight effect';
  END IF;
  n := n + 1; controls := controls + 1;

  -- 9 the halted attempt is UNRESOLVED, because whether it happened is unknown and a human has to
  --   look. A takeover that quietly closed the books would be the blind re-execution R02 forbids,
  --   one step earlier.
  IF NOT EXISTS (SELECT 1 FROM brain.effect_unresolved
                  WHERE idempotency_key = 'mv62-inflight') THEN
    RAISE EXCEPTION 'migration 62: a halted effect was not left for reconciliation';
  END IF;
  n := n + 1; refusals := refusals + 1;

  -- 10 refusal: a superseded holder cannot settle. The takeover already recorded the truth.
  BEGIN
    UPDATE brain.effect_attempt SET settled_at = now(), outcome = 'succeeded',
           result_ref = 'too late', settled_by = who
     WHERE idempotency_key = 'mv62-inflight';
    RAISE EXCEPTION 'migration 62: a superseded holder settled an attempt';
  EXCEPTION WHEN restrict_violation THEN n := n + 1; refusals := refusals + 1;
  END;

  -- Cleanup, triggers stood down for exactly these statements and restored immediately.
  ALTER TABLE brain.effect_attempt DISABLE TRIGGER effect_attempt_settles_once;
  ALTER TABLE brain.execution_lease DISABLE TRIGGER execution_lease_release_only;
  ALTER TABLE brain.execution_lease DISABLE TRIGGER execution_lease_halts_its_effects;
  ALTER TABLE brain.authority_grant DISABLE TRIGGER authority_grant_append_only;
  ALTER TABLE brain.authority_revocation DISABLE TRIGGER authority_revocation_append_only;
  DELETE FROM brain.effect_attempt WHERE idempotency_key LIKE 'mv62-%';
  DELETE FROM brain.execution_lease WHERE work_item_id = item;
  DELETE FROM brain.authority_revocation WHERE grant_seq IN (g_ok, g_other);
  DELETE FROM brain.authority_grant WHERE grant_seq IN (g_ok, g_other);
  ALTER TABLE brain.authority_revocation ENABLE TRIGGER authority_revocation_append_only;
  ALTER TABLE brain.authority_grant ENABLE TRIGGER authority_grant_append_only;
  ALTER TABLE brain.execution_lease ENABLE TRIGGER execution_lease_halts_its_effects;
  ALTER TABLE brain.execution_lease ENABLE TRIGGER execution_lease_release_only;
  ALTER TABLE brain.effect_attempt ENABLE TRIGGER effect_attempt_settles_once;
  DELETE FROM brain.work_item WHERE id = item;
  PERFORM setval('brain.item_id_seq', seq_last, seq_called);
  IF made_human THEN
    DELETE FROM brain.human_role WHERE human = who AND granted_by = 'migration 62 fixture';
  END IF;

  IF n <> 10 OR refusals <> 7 OR controls <> 3 THEN
    RAISE EXCEPTION 'migration 62: expected 10 checks as 7 refusals and 3 controls, ran % as % and %',
      n, refusals, controls;
  END IF;

  RAISE NOTICE
    'migration 62: % of 10 checks passed, % refusals watched refusing and % positive controls. '
    'brain.effect_attempt holds % row(s).',
    n, refusals, controls, (SELECT count(*) FROM brain.effect_attempt);
END $$;

INSERT INTO brain.schema_migration (version, name)
  VALUES (62, '0062_an_effect_names_the_authority_that_licensed_it')
  ON CONFLICT (version) DO NOTHING;

COMMIT;
