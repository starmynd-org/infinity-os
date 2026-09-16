-- migration 59: an EFFECT IS RESERVED BEFORE IT HAPPENS, under one idempotency key and one lease,
-- and it is settled exactly once with an outcome that may be `ambiguous`.
--
-- Packet R02, sprint `2026-09-06-multiverse-build-and-launch`. Written 2026-09-06 by Terminal 04.
-- Ledger version 59, reserved by the R01 census. Depends on migration 58 (the lease and its token).
--
-- ------------------------------------------------------------------ THE THREE THINGS THIS CLOSES
--
-- 1. RETRY AFTER AN UNCERTAIN RESULT. A worker sends a request, the connection dies, and it does
--    not know whether the effect happened. Retrying may do it twice; not retrying may do it never.
--    The reservation is written BEFORE the effect, under a key the caller derives from the work, so
--    the second attempt collides with the first and finds out what the first one knew.
--
-- 2. AN EFFECT WRITTEN BY SOMEBODY WHO LOST THE LEASE. Migration 58 gives every lease a token from
--    one sequence. Here the token is checked at reserve time against the newest lease for the item,
--    so a holder whose lease was released or taken over cannot reserve, and therefore cannot claim
--    the effect was authorised. That is the fence, and it is enforced by a trigger rather than by
--    the caller remembering to look.
--
-- 3. A PARTIAL OUTCOME REPORTED AS COMPLETE. `outcome` has three values and `ambiguous` is one of
--    them, first class, requiring a `detail` that says what was actually observed. Folding ambiguity
--    into `failed` is how a retry gets authorised for something that already happened, and folding
--    it into `succeeded` is how a thing that never happened gets marked done.
--
-- ------------------------------------------------------------------ SETTLED ONCE, NEVER REWRITTEN
--
-- An attempt goes unsettled -> settled and stops. Nothing rewrites an outcome, because the outcome
-- is the evidence. When later information arrives -- somebody checked the provider and found the
-- charge -- that is a RECONCILIATION, an append-only row of its own naming who looked and what they
-- found. The attempt still says `ambiguous`, which is what was true at the time, and the
-- reconciliation says what was learned since. Rewriting the attempt would destroy the only record
-- that anyone was ever unsure.

BEGIN;

CREATE TABLE IF NOT EXISTS brain.effect_attempt (
  attempt_seq       bigserial   PRIMARY KEY,
  idempotency_key   text        NOT NULL UNIQUE,
  lease_id          bigint      NOT NULL REFERENCES brain.execution_lease(lease_id),
  fencing_token     bigint      NOT NULL,
  description       text        NOT NULL,
  -- TEXT, NOT `brain.budget_scope`. The enum lives in `budget/schema`, and a store built
  -- without that directory -- which `SCRATCH_SCHEMA_DIRS` exists to allow, so
  -- `test-lane-budget-gate.sh` scene 7 can prove `claim` degrades -- would not have the
  -- type, and this table would fail to create there. The trigger compares `scope_type::text`
  -- so the coupling is one cast in one place rather than a hard dependency in a column.
  budget_scope_type text,
  budget_scope_id   text,
  reserved_at       timestamptz NOT NULL DEFAULT now(),
  settled_at        timestamptz,
  outcome           text,
  result_ref        text,
  detail            text,
  CONSTRAINT effect_attempt_not_blank_ck
    CHECK (btrim(idempotency_key) <> '' AND btrim(description) <> ''),
  CONSTRAINT effect_attempt_outcome_ck
    CHECK (outcome IS NULL OR outcome IN ('succeeded', 'failed', 'ambiguous')),
  CONSTRAINT effect_attempt_settled_together_ck
    CHECK ((settled_at IS NULL) = (outcome IS NULL)),
  -- A SUCCESS NAMES WHAT IT DID. Without this, `succeeded` with no reference is a claim nobody can
  -- check, which is the shape a partial outcome takes when it is reported as complete.
  CONSTRAINT effect_attempt_success_names_its_effect_ck
    CHECK (outcome IS DISTINCT FROM 'succeeded' OR btrim(coalesce(result_ref, '')) <> ''),
  -- AN AMBIGUITY SAYS WHAT IT SAW, so the human reconciling it has something to go on.
  CONSTRAINT effect_attempt_ambiguity_says_what_it_saw_ck
    CHECK (outcome IS DISTINCT FROM 'ambiguous' OR btrim(coalesce(detail, '')) <> ''),
  CONSTRAINT effect_attempt_budget_scope_is_whole_ck
    CHECK ((budget_scope_type IS NULL) = (budget_scope_id IS NULL))
);

CREATE INDEX IF NOT EXISTS effect_attempt_unsettled
  ON brain.effect_attempt (reserved_at) WHERE settled_at IS NULL;

CREATE TABLE IF NOT EXISTS brain.effect_reconciliation (
  reconciliation_seq bigserial   PRIMARY KEY,
  attempt_seq        bigint      NOT NULL REFERENCES brain.effect_attempt(attempt_seq),
  found_at           timestamptz NOT NULL DEFAULT now(),
  found_by           text        NOT NULL,
  finding            text        NOT NULL,
  evidence           text        NOT NULL,
  CONSTRAINT effect_reconciliation_finding_ck
    CHECK (finding IN ('effect-happened', 'effect-did-not-happen', 'still-unknown')),
  CONSTRAINT effect_reconciliation_not_blank_ck
    CHECK (btrim(found_by) <> '' AND btrim(evidence) <> '')
);

-- THE FENCE, AND THE BUDGET STOP, BOTH CHECKED AT RESERVE TIME.
--
-- The budget half degrades rather than raising when the budget tables are absent. That is not
-- politeness: `engine/tests/test-lane-budget-gate.sh` scene 7 deliberately builds a store WITHOUT
-- budget/schema to prove that claim degrades instead of raising UndefinedTable and taking the fleet
-- down, and `SCRATCH_SCHEMA_DIRS` exists so that scene can ask for such a store. A reserve that
-- raised there would break a scene that is testing a real property.
CREATE OR REPLACE FUNCTION brain.effect_attempt_may_reserve() RETURNS trigger
LANGUAGE plpgsql AS $fn$
DECLARE
  live_token bigint;
  item       text;
BEGIN
  SELECT work_item_id INTO item FROM brain.execution_lease WHERE lease_id = NEW.lease_id;

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

DROP TRIGGER IF EXISTS effect_attempt_may_reserve ON brain.effect_attempt;
CREATE TRIGGER effect_attempt_may_reserve BEFORE INSERT ON brain.effect_attempt
  FOR EACH ROW EXECUTE FUNCTION brain.effect_attempt_may_reserve();

-- SETTLED ONCE. The only legal update moves an unsettled attempt to a settled one; nothing else
-- about the row may move, and a settled attempt is finished.
CREATE OR REPLACE FUNCTION brain.effect_attempt_settles_once() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'brain.effect_attempt is the record that an effect was attempted: DELETE refused'
      USING ERRCODE = 'restrict_violation';
  END IF;
  IF OLD.settled_at IS NOT NULL THEN
    RAISE EXCEPTION
      'attempt % was settled at % as %. Later information is a reconciliation, not a rewrite: '
      'insert into brain.effect_reconciliation. Rewriting the outcome destroys the only record '
      'that anyone was unsure.', OLD.attempt_seq, OLD.settled_at, OLD.outcome
      USING ERRCODE = 'restrict_violation';
  END IF;
  IF NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key
     OR NEW.lease_id      IS DISTINCT FROM OLD.lease_id
     OR NEW.fencing_token IS DISTINCT FROM OLD.fencing_token
     OR NEW.description   IS DISTINCT FROM OLD.description
     OR NEW.reserved_at   IS DISTINCT FROM OLD.reserved_at THEN
    RAISE EXCEPTION 'brain.effect_attempt: the only legal update is to settle'
      USING ERRCODE = 'restrict_violation';
  END IF;
  IF NEW.settled_at IS NULL THEN
    RAISE EXCEPTION 'brain.effect_attempt: an update that settles nothing changes nothing'
      USING ERRCODE = 'restrict_violation';
  END IF;
  RETURN NEW;
END;
$fn$;

DROP TRIGGER IF EXISTS effect_attempt_settles_once ON brain.effect_attempt;
CREATE TRIGGER effect_attempt_settles_once BEFORE UPDATE OR DELETE ON brain.effect_attempt
  FOR EACH ROW EXECUTE FUNCTION brain.effect_attempt_settles_once();

CREATE OR REPLACE FUNCTION brain.effect_reconciliation_is_append_only() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
  RAISE EXCEPTION 'brain.effect_reconciliation is append-only: % refused', TG_OP
    USING ERRCODE = 'restrict_violation';
END;
$fn$;

DROP TRIGGER IF EXISTS effect_reconciliation_append_only ON brain.effect_reconciliation;
CREATE TRIGGER effect_reconciliation_append_only
  BEFORE UPDATE OR DELETE ON brain.effect_reconciliation
  FOR EACH ROW EXECUTE FUNCTION brain.effect_reconciliation_is_append_only();

-- WHAT IS UNRESOLVED RIGHT NOW: reserved and never settled, or settled as ambiguous with no
-- reconciliation that reached a conclusion. This is the queue a human works, and it is a view so
-- that nothing has to remember to enqueue anything.
CREATE OR REPLACE VIEW brain.effect_unresolved AS
SELECT a.*,
       CASE WHEN a.settled_at IS NULL THEN 'reserved-never-settled' ELSE 'ambiguous' END AS why
  FROM brain.effect_attempt a
 WHERE a.settled_at IS NULL
    OR (a.outcome = 'ambiguous'
        AND NOT EXISTS (SELECT 1 FROM brain.effect_reconciliation r
                         WHERE r.attempt_seq = a.attempt_seq
                           AND r.finding <> 'still-unknown'));

GRANT SELECT, INSERT, UPDATE ON brain.effect_attempt TO brain_runtime;
GRANT SELECT, INSERT ON brain.effect_reconciliation TO brain_runtime;
GRANT SELECT ON brain.effect_unresolved TO brain_runtime, brain_subscriber;
GRANT USAGE ON SEQUENCE brain.effect_attempt_attempt_seq_seq,
                        brain.effect_reconciliation_reconciliation_seq_seq TO brain_runtime;

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'brain_operator') THEN
    EXECUTE 'GRANT SELECT, INSERT, UPDATE ON brain.effect_attempt TO brain_operator';
    EXECUTE 'GRANT SELECT, INSERT ON brain.effect_reconciliation TO brain_operator';
    EXECUTE 'GRANT SELECT ON brain.effect_unresolved TO brain_operator';
    EXECUTE 'GRANT USAGE ON SEQUENCE brain.effect_attempt_attempt_seq_seq, '
            'brain.effect_reconciliation_reconciliation_seq_seq TO brain_operator';
  END IF;
END $$;

-- ------------------------------------------------------------------ EVERY REFUSAL WATCHED HAPPENING
DO $$
DECLARE
  n int := 0;
  item text; l1 bigint; t1 bigint; l2 bigint; t2 bigint;
  a1 bigint; a2 bigint;
  seq_last bigint; seq_called boolean;
BEGIN
  SELECT last_value, is_called INTO seq_last, seq_called FROM brain.item_id_seq;
  INSERT INTO brain.work_item (title, lane) VALUES ('migration 59 self-check', 'mv')
    RETURNING id INTO item;
  INSERT INTO brain.execution_lease (work_item_id, holder, expires_at, fencing_token)
    VALUES (item, 'worker-a', now() + interval '5 minutes', 0)
    RETURNING lease_id, fencing_token INTO l1, t1;

  -- 1 a reservation under a live lease with the right token lands.
  INSERT INTO brain.effect_attempt (idempotency_key, lease_id, fencing_token, description)
    VALUES ('mv59-key-1', l1, t1, 'send the thing') RETURNING attempt_seq INTO a1;
  n := n + 1;

  -- 2 it is unresolved, because it has not been settled.
  IF NOT EXISTS (SELECT 1 FROM brain.effect_unresolved
                  WHERE attempt_seq = a1 AND why = 'reserved-never-settled') THEN
    RAISE EXCEPTION 'migration 59: a reserved attempt was not unresolved';
  END IF;
  n := n + 1;

  -- 3 THE SAME KEY TWICE IS ONE EFFECT. This is the retry after an uncertain result.
  BEGIN
    INSERT INTO brain.effect_attempt (idempotency_key, lease_id, fencing_token, description)
      VALUES ('mv59-key-1', l1, t1, 'send the thing again');
    RAISE EXCEPTION 'migration 59: a repeated idempotency key was NOT refused';
  EXCEPTION WHEN unique_violation THEN n := n + 1;
  END;

  -- 4 a stale token cannot reserve.
  BEGIN
    INSERT INTO brain.effect_attempt (idempotency_key, lease_id, fencing_token, description)
      VALUES ('mv59-key-stale', l1, t1 - 1, 'act on a world that moved');
    RAISE EXCEPTION 'migration 59: a stale fencing token was NOT refused';
  EXCEPTION WHEN restrict_violation THEN n := n + 1;
  END;

  -- 5 SUCCESS MUST NAME WHAT IT DID.
  BEGIN
    UPDATE brain.effect_attempt SET settled_at = now(), outcome = 'succeeded' WHERE attempt_seq = a1;
    RAISE EXCEPTION 'migration 59: a success with no result reference was NOT refused';
  EXCEPTION WHEN check_violation THEN n := n + 1;
  END;

  -- 6 AMBIGUITY MUST SAY WHAT IT SAW.
  BEGIN
    UPDATE brain.effect_attempt SET settled_at = now(), outcome = 'ambiguous' WHERE attempt_seq = a1;
    RAISE EXCEPTION 'migration 59: an ambiguous outcome with no detail was NOT refused';
  EXCEPTION WHEN check_violation THEN n := n + 1;
  END;

  -- 7 an ambiguous settle with detail lands, and stays unresolved.
  UPDATE brain.effect_attempt
     SET settled_at = now(), outcome = 'ambiguous',
         detail = 'the connection dropped after the request and before any response'
   WHERE attempt_seq = a1;
  IF NOT EXISTS (SELECT 1 FROM brain.effect_unresolved
                  WHERE attempt_seq = a1 AND why = 'ambiguous') THEN
    RAISE EXCEPTION 'migration 59: an ambiguous attempt did not stay unresolved';
  END IF;
  n := n + 1;

  -- 8 SETTLING TWICE IS REFUSED. An ambiguous outcome is not upgraded to a success by a later
  --   opinion; that is what the reconciliation table is for.
  BEGIN
    UPDATE brain.effect_attempt SET settled_at = now(), outcome = 'succeeded', result_ref = 'guess'
     WHERE attempt_seq = a1;
    RAISE EXCEPTION 'migration 59: a settled attempt was re-settled';
  EXCEPTION WHEN restrict_violation THEN n := n + 1;
  END;

  -- 9 a reconciliation resolves it without rewriting it. The attempt still says ambiguous.
  INSERT INTO brain.effect_reconciliation (attempt_seq, found_by, finding, evidence)
    VALUES (a1, 'migration 59 self-check', 'effect-happened', 'provider ledger shows one send');
  IF EXISTS (SELECT 1 FROM brain.effect_unresolved WHERE attempt_seq = a1) THEN
    RAISE EXCEPTION 'migration 59: a reconciled attempt was still unresolved';
  END IF;
  IF (SELECT outcome FROM brain.effect_attempt WHERE attempt_seq = a1) <> 'ambiguous' THEN
    RAISE EXCEPTION 'migration 59: the reconciliation rewrote the attempt';
  END IF;
  n := n + 1;

  -- 10 a reconciliation cannot be edited.
  BEGIN
    UPDATE brain.effect_reconciliation SET finding = 'effect-did-not-happen' WHERE attempt_seq = a1;
    RAISE EXCEPTION 'migration 59: a reconciliation was editable';
  EXCEPTION WHEN restrict_violation THEN n := n + 1;
  END;

  -- 11 NO NEW EFFECT AFTER THE LEASE IS TAKEN AWAY, even with a key nobody has used.
  UPDATE brain.execution_lease SET released_at = now(), release_reason = 'migration 59 self-check'
   WHERE lease_id = l1;
  BEGIN
    INSERT INTO brain.effect_attempt (idempotency_key, lease_id, fencing_token, description)
      VALUES ('mv59-key-after-release', l1, t1, 'act after losing the lease');
    RAISE EXCEPTION 'migration 59: an effect after release was NOT refused';
  EXCEPTION WHEN restrict_violation THEN n := n + 1;
  END;

  -- 12 the new holder can reserve, so check 11 is about the fence and not about the item.
  INSERT INTO brain.execution_lease (work_item_id, holder, expires_at, fencing_token)
    VALUES (item, 'worker-b', now() + interval '5 minutes', 0)
    RETURNING lease_id, fencing_token INTO l2, t2;
  INSERT INTO brain.effect_attempt (idempotency_key, lease_id, fencing_token, description)
    VALUES ('mv59-key-2', l2, t2, 'the new holder acts') RETURNING attempt_seq INTO a2;
  n := n + 1;

  -- 13 least privilege: the runtime settles and reconciles, and deletes neither.
  IF has_table_privilege('brain_runtime', 'brain.effect_attempt', 'DELETE')
     OR has_table_privilege('brain_runtime', 'brain.effect_reconciliation', 'UPDATE')
     OR NOT has_table_privilege('brain_runtime', 'brain.effect_attempt', 'UPDATE') THEN
    RAISE EXCEPTION 'migration 59: runtime privileges on the effect tables are wrong';
  END IF;
  n := n + 1;

  -- Fixture cleanup, triggers stood down for exactly these statements and restored immediately.
  ALTER TABLE brain.effect_reconciliation DISABLE TRIGGER effect_reconciliation_append_only;
  ALTER TABLE brain.effect_attempt DISABLE TRIGGER effect_attempt_settles_once;
  ALTER TABLE brain.execution_lease DISABLE TRIGGER execution_lease_release_only;
  DELETE FROM brain.effect_reconciliation WHERE attempt_seq IN (a1, a2);
  DELETE FROM brain.effect_attempt WHERE attempt_seq IN (a1, a2);
  DELETE FROM brain.execution_lease WHERE work_item_id = item;
  ALTER TABLE brain.execution_lease ENABLE TRIGGER execution_lease_release_only;
  ALTER TABLE brain.effect_attempt ENABLE TRIGGER effect_attempt_settles_once;
  ALTER TABLE brain.effect_reconciliation ENABLE TRIGGER effect_reconciliation_append_only;
  DELETE FROM brain.work_item WHERE id = item;
  PERFORM setval('brain.item_id_seq', seq_last, seq_called);

  IF EXISTS (SELECT 1 FROM brain.effect_attempt) OR EXISTS (SELECT 1 FROM brain.execution_lease) THEN
    RAISE EXCEPTION 'migration 59: rows left behind by the self-check';
  END IF;

  IF n <> 13 THEN
    RAISE EXCEPTION 'migration 59: expected 13 checks, ran %', n;
  END IF;

  RAISE NOTICE
    'migration 59: % of 13 checks passed, seven of them refusals watched refusing, over % attempt '
    'row(s) and % reconciliation(s) left behind. item_id_seq restored to (last_value %, %).',
    n, (SELECT count(*) FROM brain.effect_attempt),
    (SELECT count(*) FROM brain.effect_reconciliation), seq_last, seq_called;
END $$;

INSERT INTO brain.schema_migration (version, name)
  VALUES (59, '0059_an_effect_is_reserved_before_it_happens')
  ON CONFLICT (version) DO NOTHING;

COMMIT;
