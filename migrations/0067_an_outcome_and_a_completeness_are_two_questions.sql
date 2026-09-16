-- migration 67: an outcome and a completeness are TWO DIFFERENT QUESTIONS, and this table has been
-- answering them with one column.
--
-- Packet R01/R02, contract alignment. Written 2026-09-07 by Terminal 04 against pinned contract
-- C01-PIN-01 v0.1.0-draft.5. Ledger version 67. The last of the two draft.5 alignments; 66 was the
-- other.
--
-- ------------------------------------------------------------------ THE CONFLATION, STATED PLAINLY
--
-- `brain.effect_attempt.outcome` shipped in migration 59 with three values -- succeeded, failed,
-- ambiguous -- and migration 62 widened it to five by adding halted and cancelled. Every one of
-- those versions makes the same mistake, which I named in my own change request to C01 on
-- 2026-09-06 and did not fix until now:
--
--     `ambiguous` is doing the work of both WHAT HAPPENED and HOW SURE AM I.
--
-- Those are independent. A thing can have failed and be certain. A thing can have failed and be
-- uncertain. Today the second one CANNOT BE WRITTEN DOWN: to record the uncertainty you must give
-- up saying it failed, and to record the failure you must claim a certainty you do not have. Both
-- available rows are false, and a worker that has just watched a payment API time out after the
-- charge left has to pick one of them.
--
-- The contract already had this right. `Receipt` separates `outcome` (success, denied, noop,
-- ambiguous, partial, failed, halted, cancelled) from `completeness` (complete, partial, uncertain)
-- and carries per-effect state in `effects[].state` (applied, not_applied, unknown). C01's own
-- summary of what CAP06 asked for says it in one line: outcome and completeness as separate fields
-- SO THAT PARTIAL AND AMBIGUOUS CANNOT REPORT COMPLETE.
--
-- This migration adopts that shape. I am not defending mine.
--
-- ------------------------------------------------------------------ WHAT RC-I2 IS, AND WHY IT BITES
--
-- RC-I2 is the contract's receipt invariant: `outcome success but an effect is not in state
-- applied` is a rejection. In C01's own verification run it is fixture `receipt.neg.05`, refused in
-- both strict and tolerate-newer-minor modes.
--
-- It is worth being clear about why an invariant that reads like bookkeeping is the important one
-- here. A receipt is what everything downstream believes. If a caller can write `success` over an
-- effect whose state is `unknown`, then the single most consequential fact in the system -- did the
-- thing happen -- is settable by the party with the strongest incentive to say yes, and the row
-- leaves `effect_unresolved`, which is to say it leaves human review, at the moment it most needs
-- to be in it. That is the same failure the settle path already refuses in migration 59's rule
-- against reporting an unresolved effect as succeeded with an invented reference. RC-I2 generalises
-- it from one bad move to the whole class.
--
-- In this store one attempt is one effect, so `effects[].state` becomes a column, `effect_state`,
-- and RC-I2 becomes a CHECK: outcome `success` requires effect_state `applied`.
--
-- ------------------------------------------------------------------ THE RENAME IS REAL, NOT ADDITIVE
--
-- `succeeded` becomes `success`, the contract's spelling. This is deliberately a rename and not an
-- alias: two spellings of one value is how a vocabulary rots, and a store that accepts both teaches
-- every consumer that neither is authoritative. Existing rows are updated, and this migration
-- asserts at the end that the string `succeeded` appears nowhere in any brain function body, view
-- definition or constraint expression.
--
-- That assertion is here because migration 65 taught it. 65 renamed `fencing_token` to
-- `lease_epoch` and I checked the two tables and called it done; a view built as `SELECT a.*` was
-- still publishing the old name, and a sequence still carried it. Checking the obvious places is
-- how a rename half-lands. Check everywhere the string can hide.
--
-- ------------------------------------------------------------------ THE BACKFILL, AND WHAT IT ASSERTS
--
-- Old rows carry no completeness. Reading one into the new vocabulary is a judgement about what the
-- old vocabulary MEANT, so it is written here rather than left in a commit message:
--
--   succeeded -> success   / complete  / applied
--       Old `succeeded` was constrained to carry a non-empty `result_ref`, and a result reference
--       is a reference to an applied effect. The row asserted application.
--   failed    -> failed    / complete  / not_applied
--       Old `failed` is the one that deserves suspicion, because the honest reading of a legacy
--       failure could be `uncertain`. It is read as complete because the old vocabulary had a
--       SEPARATE bucket for not knowing, and it was `ambiguous`. A writer who chose `failed` when
--       `ambiguous` was available was asserting knowledge. Reading it as uncertain now would invent
--       doubt those writers did not express and would flood `effect_unresolved` with rows nobody
--       ever flagged.
--   ambiguous -> ambiguous / uncertain / unknown
--   halted    -> halted    / uncertain / unknown
--       Migration 62's own comment on the halt says it: whether the effect happened is UNKNOWN.
--   cancelled -> cancelled / complete  / not_applied
--       Cancelled is refused before execution, so nothing was applied.
--
-- The backfill prints its denominator. A migration that reports success over zero rows has proved
-- nothing, and this repo has produced that answer often enough to make printing the count a rule.

BEGIN;

ALTER TABLE brain.effect_attempt ADD COLUMN IF NOT EXISTS completeness text;
ALTER TABLE brain.effect_attempt ADD COLUMN IF NOT EXISTS effect_state  text;

-- ------------------------------------------------------------------ TWO ROWS IN THE OLD VOCABULARY
--
-- WRITTEN AFTER THE FIRST GREEN RUN, BECAUSE THE FIRST GREEN RUN WAS HOLLOW. On the migrate path
-- this store had 12 settled rows and check 6 compared 55 against 57. On a FRESH create it had none,
-- so check 6 read "old predicate showed 0 rows, new view shows 2, lost 0" -- a superset assertion
-- over an empty set, which is the failure this repo names most often and which I have spent two
-- days pointing out in other lanes. `lost = 0` is trivially true when there was nothing to lose.
--
-- So the migration plants what it needs to measure. These two rows are written in the OLD
-- vocabulary, BEFORE the rename and the backfill, with completeness NULL, because seeding them in
-- the new vocabulary would prove nothing either: it would skip the backfill entirely and test only
-- that a row I just wrote correctly is still visible. The property under test is that a row written
-- the old way, converted by the backfill, is still in front of a human afterwards.
DO $seed$
DECLARE who text; item text; l1 bigint; t1 bigint; g bigint;
BEGIN
  SELECT human INTO who FROM brain.human_role ORDER BY human LIMIT 1;
  IF who IS NULL THEN
    who := '_mv67_seed_human';
    INSERT INTO brain.human_role (role_name, human, granted_at, granted_by)
      VALUES ('brain_runtime', who, now(), 'migration 67 seed');
  END IF;
  INSERT INTO brain.agent (name, role, status, host, updated)
    VALUES ('_mv67_seed_agent', 'worker', 'idle', 'migration-67', now())
    ON CONFLICT (name) DO UPDATE SET updated = now();
  INSERT INTO brain.work_item (title, lane) VALUES ('migration 67 seed', 'mv') RETURNING id INTO item;
  INSERT INTO brain.authority_grant (granted_by, workspace, subject_kind, subject, capability,
                                     scope, expires_at, evidence)
    VALUES (who, 'ws-mv67seed', 'human', who, 'effect.external', 'lane/mv67seed',
            now() + interval '1 hour', 'migration 67 seed') RETURNING grant_seq INTO g;
  INSERT INTO brain.execution_lease (work_item_id, workspace, holder, expires_at, lease_epoch)
    VALUES (item, 'ws-mv67seed', who, now() + interval '1 hour', 0)
    RETURNING lease_id, lease_epoch INTO l1, t1;
  PERFORM set_config('brain.settle_secret', current_setting('brain.lease_secret', true), true);

  INSERT INTO brain.effect_attempt (idempotency_key, lease_id, lease_epoch, description, workspace,
                                    subject, capability, scope, grant_seq)
    VALUES ('mv67seed-ambiguous', l1, t1, 'an old ambiguous effect', 'ws-mv67seed', who,
            'effect.external', 'lane/mv67seed', g);
  INSERT INTO brain.effect_attempt (idempotency_key, lease_id, lease_epoch, description, workspace,
                                    subject, capability, scope, grant_seq)
    VALUES ('mv67seed-halted', l1, t1, 'an old halted effect', 'ws-mv67seed', who,
            'effect.external', 'lane/mv67seed', g);

  UPDATE brain.effect_attempt SET settled_at = now(), outcome = 'ambiguous', settled_by = who,
         detail = 'the provider never answered'
   WHERE idempotency_key = 'mv67seed-ambiguous';
  -- `halted` is written directly rather than by releasing the lease, because the halt trigger is
  -- rewritten later in this same file and would write the NEW columns. The row has to arrive in the
  -- old shape or it is not testing the backfill.
  ALTER TABLE brain.effect_attempt DISABLE TRIGGER effect_attempt_settles_once;
  UPDATE brain.effect_attempt SET settled_at = now(), outcome = 'halted',
         settled_by = 'lease-release', detail = 'the worker was stopped mid-flight'
   WHERE idempotency_key = 'mv67seed-halted';
  ALTER TABLE brain.effect_attempt ENABLE TRIGGER effect_attempt_settles_once;
END $seed$;

-- ------------------------------------------------------------------ the rename, then the backfill

DO $mig$
DECLARE renamed int; filled int; settled int;
BEGIN
  ALTER TABLE brain.effect_attempt DROP CONSTRAINT IF EXISTS effect_attempt_outcome_ck;
  ALTER TABLE brain.effect_attempt DISABLE TRIGGER effect_attempt_settles_once;

  UPDATE brain.effect_attempt SET outcome = 'success' WHERE outcome = 'succeeded';
  GET DIAGNOSTICS renamed = ROW_COUNT;

  UPDATE brain.effect_attempt
     SET completeness = CASE outcome WHEN 'success'   THEN 'complete'
                                     WHEN 'failed'    THEN 'complete'
                                     WHEN 'cancelled' THEN 'complete'
                                     ELSE 'uncertain' END,
         effect_state = CASE outcome WHEN 'success'   THEN 'applied'
                                     WHEN 'failed'    THEN 'not_applied'
                                     WHEN 'cancelled' THEN 'not_applied'
                                     ELSE 'unknown' END
   WHERE settled_at IS NOT NULL AND completeness IS NULL;
  GET DIAGNOSTICS filled = ROW_COUNT;

  ALTER TABLE brain.effect_attempt ENABLE TRIGGER effect_attempt_settles_once;

  SELECT count(*) INTO settled FROM brain.effect_attempt WHERE settled_at IS NOT NULL;
  RAISE NOTICE 'migration 67: % settled rows on this store; % renamed to success, % given a completeness and an effect_state',
    settled, renamed, filled;

  -- The constraint added below is validated, not NOT VALID, so the backfill must be total. Say so
  -- here rather than discover it as a constraint error with no explanation attached.
  IF EXISTS (SELECT 1 FROM brain.effect_attempt
              WHERE settled_at IS NOT NULL AND (completeness IS NULL OR effect_state IS NULL)) THEN
    RAISE EXCEPTION 'migration 67: the backfill left settled rows with no completeness';
  END IF;
END $mig$;

-- ------------------------------------------------------------------ the vocabulary and the bindings
--
-- EVERY `ADD CONSTRAINT` IS PRECEDED BY ITS OWN `DROP ... IF EXISTS`, added after this file was
-- already green. Two of the constraints below had that guard from the start, because they replace
-- differently-named predecessors from migration 59, and the other six did not. That is a file that
-- half-guards, which is worse than one that does not guard at all: it reads as if the question had
-- been considered.
--
-- Found by running R-t04-STORE-RUNNER-01's own proof. Rolling a store back behind the tree -- drop
-- the two columns, delete the ledger row -- and letting the runner reconcile it produced:
--
--     ERROR:  constraint "effect_attempt_success_needs_ref_ck" for relation "effect_attempt"
--             already exists
--
-- because dropping the COLUMNS cascades to the constraints that mention them and leaves the ones
-- that do not. A ledger version applies once, so no supported path reaches that state and this was
-- not a live defect. It is still a footgun for anyone restoring a store mid-ledger, which
-- `deploy/tests/test_restore_proves_behaviour.py` exists to do, and the guard costs one line each.

ALTER TABLE brain.effect_attempt DROP CONSTRAINT IF EXISTS effect_attempt_outcome_ck;
ALTER TABLE brain.effect_attempt ADD CONSTRAINT effect_attempt_outcome_ck
  CHECK (outcome IS NULL OR outcome IN ('success', 'denied', 'noop', 'ambiguous',
                                        'partial', 'failed', 'halted', 'cancelled'));

ALTER TABLE brain.effect_attempt DROP CONSTRAINT IF EXISTS effect_attempt_completeness_ck;
ALTER TABLE brain.effect_attempt ADD CONSTRAINT effect_attempt_completeness_ck
  CHECK (completeness IS NULL OR completeness IN ('complete', 'partial', 'uncertain'));

ALTER TABLE brain.effect_attempt DROP CONSTRAINT IF EXISTS effect_attempt_effect_state_ck;
ALTER TABLE brain.effect_attempt ADD CONSTRAINT effect_attempt_effect_state_ck
  CHECK (effect_state IS NULL OR effect_state IN ('applied', 'not_applied', 'unknown'));

-- A settled attempt states all three or none. Saying what happened while declining to say how sure
-- you are is the exact evasion this migration exists to remove.
ALTER TABLE brain.effect_attempt DROP CONSTRAINT IF EXISTS effect_attempt_settled_states_all_ck;
ALTER TABLE brain.effect_attempt ADD CONSTRAINT effect_attempt_settled_states_all_ck
  CHECK ((settled_at IS NULL) = (completeness IS NULL)
     AND (settled_at IS NULL) = (effect_state  IS NULL));

-- RC-I2 ITSELF.
ALTER TABLE brain.effect_attempt DROP CONSTRAINT IF EXISTS effect_attempt_rc_i2_ck;
ALTER TABLE brain.effect_attempt ADD CONSTRAINT effect_attempt_rc_i2_ck
  CHECK (outcome IS DISTINCT FROM 'success' OR effect_state = 'applied');

-- The three outcome-to-completeness bindings the contract states as if/then. `success` is complete
-- by definition; `partial` and `ambiguous` are the two that C01's negative fixtures 02 and 03 catch
-- reporting themselves complete.
ALTER TABLE brain.effect_attempt DROP CONSTRAINT IF EXISTS effect_attempt_outcome_completeness_ck;
ALTER TABLE brain.effect_attempt ADD CONSTRAINT effect_attempt_outcome_completeness_ck
  CHECK ((outcome IS DISTINCT FROM 'success'   OR completeness = 'complete')
     AND (outcome IS DISTINCT FROM 'partial'   OR completeness = 'partial')
     AND (outcome IS DISTINCT FROM 'ambiguous' OR completeness = 'uncertain'));

-- A report cannot call itself complete while the effect's state is unknown. This is the general
-- form of the sentence C01 wrote for CAP06: partial and ambiguous cannot report complete.
ALTER TABLE brain.effect_attempt DROP CONSTRAINT IF EXISTS effect_attempt_complete_knows_state_ck;
ALTER TABLE brain.effect_attempt ADD CONSTRAINT effect_attempt_complete_knows_state_ck
  CHECK (completeness IS DISTINCT FROM 'complete' OR effect_state <> 'unknown');

-- Migration 59 required a detail on `ambiguous`. Uncertainty is now its own axis and is wider than
-- that one outcome, so the requirement moves with it: whoever is unsure says what they are unsure
-- about. The old constraint is dropped because its subject no longer exists in that form.
ALTER TABLE brain.effect_attempt DROP CONSTRAINT IF EXISTS effect_attempt_ambiguity_says_what_it_saw_ck;
ALTER TABLE brain.effect_attempt DROP CONSTRAINT IF EXISTS effect_attempt_uncertain_needs_detail_ck;
ALTER TABLE brain.effect_attempt ADD CONSTRAINT effect_attempt_uncertain_needs_detail_ck
  CHECK (completeness IS DISTINCT FROM 'uncertain' OR btrim(coalesce(detail, '')) <> '');

-- Migration 59's success-needs-a-reference rule, carried over to the new spelling.
ALTER TABLE brain.effect_attempt DROP CONSTRAINT IF EXISTS effect_attempt_success_names_its_effect_ck;
ALTER TABLE brain.effect_attempt DROP CONSTRAINT IF EXISTS effect_attempt_success_needs_ref_ck;
ALTER TABLE brain.effect_attempt ADD CONSTRAINT effect_attempt_success_needs_ref_ck
  CHECK (outcome IS DISTINCT FROM 'success' OR btrim(coalesce(result_ref, '')) <> '');

-- ------------------------------------------------------------------ the halt, now saying both things
--
-- Re-declared in full rather than patched, because the body is short and a regex over
-- pg_get_functiondef is how migration 65 nearly shipped a half-rename. The halt already said in
-- prose that the effect's fate is unknown; now it says it in the columns, which is the only form a
-- query can read.
CREATE OR REPLACE FUNCTION brain.execution_lease_halts_its_effects() RETURNS trigger
LANGUAGE plpgsql AS $fn$
DECLARE
  halted int;
BEGIN
  IF OLD.released_at IS NULL AND NEW.released_at IS NOT NULL THEN
    UPDATE brain.effect_attempt
       SET settled_at   = now(),
           outcome      = 'halted',
           completeness = 'uncertain',
           effect_state = 'unknown',
           settled_by   = 'lease-release',
           detail       = coalesce(detail || ' | ', '')
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

-- ------------------------------------------------------------------ what a human still has to look at
--
-- The view keyed on `outcome IN ('ambiguous', 'halted')`, which was the only way to spell doubt.
-- Now that doubt has its own axis the view reads that axis instead. Every row the old predicate
-- caught is still caught, because both old outcomes backfill to uncertain-and-unknown, and one kind
-- of row is caught that could not previously be written at all: a failure somebody is unsure about.
-- The superset relation is not asserted here in prose. It is recomputed as check 6 below.
CREATE TEMP TABLE mv67_unresolved_before ON COMMIT DROP AS
SELECT attempt_seq FROM brain.effect_attempt a
 WHERE a.settled_at IS NULL
    OR (a.outcome IN ('ambiguous', 'halted')
        AND NOT EXISTS (SELECT 1 FROM brain.effect_reconciliation r
                         WHERE r.attempt_seq = a.attempt_seq
                           AND r.finding <> 'still-unknown'));

DROP VIEW IF EXISTS brain.effect_unresolved;
CREATE VIEW brain.effect_unresolved AS
SELECT a.*,
       CASE WHEN a.settled_at IS NULL       THEN 'reserved-never-settled'
            WHEN a.outcome = 'halted'       THEN 'halted-by-takeover'
            WHEN a.effect_state = 'unknown' THEN 'effect state unknown'
            ELSE 'completeness uncertain' END AS why
  FROM brain.effect_attempt a
 WHERE a.settled_at IS NULL
    OR ((a.completeness = 'uncertain' OR a.effect_state = 'unknown')
        AND NOT EXISTS (SELECT 1 FROM brain.effect_reconciliation r
                         WHERE r.attempt_seq = a.attempt_seq
                           AND r.finding <> 'still-unknown'));

GRANT SELECT ON brain.effect_unresolved TO brain_runtime, brain_subscriber;
DO $grant$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'brain_operator') THEN
    EXECUTE 'GRANT SELECT ON brain.effect_unresolved TO brain_operator';
  END IF;
END $grant$;

-- ------------------------------------------------------------------ EVERY REFUSAL WATCHED HAPPENING
--
-- Fifteen checks: NINE refusals watched refusing and SIX positive controls. Both counts asserted.
--
-- Check 2 is the scene that proves "failed and uncertain" is expressible. Checks 7 and 8 are RC-I2
-- refused when violated. Check 6 recomputes the view's superset relation rather than claiming it.
DO $checks$
DECLARE
  n int := 0; refusals int := 0; controls int := 0;
  who text; made_human boolean := false; ag text := '_mv67_agent';
  ws text := 'ws-mv67'; g_ok bigint; item text; l1 bigint; t1 bigint; secret text;
  a1 bigint; a2 bigint; a3 bigint; a4 bigint;
  before_n int; after_n int; lost int; stray text;
BEGIN
  SELECT human INTO who FROM brain.human_role ORDER BY human LIMIT 1;
  IF who IS NULL THEN
    who := '_mv67_fixture_human';
    INSERT INTO brain.human_role (role_name, human, granted_at, granted_by)
      VALUES ('brain_runtime', who, now(), 'migration 67 fixture');
    made_human := true;
  END IF;
  INSERT INTO brain.agent (name, role, status, host, updated)
    VALUES (ag, 'worker', 'idle', 'migration-67', now())
    ON CONFLICT (name) DO UPDATE SET updated = now();
  INSERT INTO brain.authority_grant (granted_by, workspace, subject_kind, subject, capability,
                                     scope, expires_at, evidence)
    VALUES (who, ws, 'human', who, 'effect.external', 'lane/mv67', now() + interval '1 hour',
            'migration 67 self-check') RETURNING grant_seq INTO g_ok;
  INSERT INTO brain.work_item (title, lane) VALUES ('migration 67 self-check', 'mv')
    RETURNING id INTO item;
  INSERT INTO brain.execution_lease (work_item_id, workspace, holder, expires_at, lease_epoch)
    VALUES (item, ws, who, now() + interval '1 hour', 0)
    RETURNING lease_id, lease_epoch INTO l1, t1;
  secret := current_setting('brain.lease_secret', true);
  PERFORM set_config('brain.settle_secret', secret, true);

  INSERT INTO brain.effect_attempt (idempotency_key, lease_id, lease_epoch, description,
                                    workspace, subject, capability, scope, grant_seq)
    VALUES ('mv67-a', l1, t1, 'an ordinary effect', ws, who, 'effect.external', 'lane/mv67', g_ok)
    RETURNING attempt_seq INTO a1;
  INSERT INTO brain.effect_attempt (idempotency_key, lease_id, lease_epoch, description,
                                    workspace, subject, capability, scope, grant_seq)
    VALUES ('mv67-b', l1, t1, 'an effect that fails uncertainly', ws, who, 'effect.external',
            'lane/mv67', g_ok) RETURNING attempt_seq INTO a2;
  INSERT INTO brain.effect_attempt (idempotency_key, lease_id, lease_epoch, description,
                                    workspace, subject, capability, scope, grant_seq)
    VALUES ('mv67-c', l1, t1, 'an effect that half lands', ws, who, 'effect.external',
            'lane/mv67', g_ok) RETURNING attempt_seq INTO a3;
  INSERT INTO brain.effect_attempt (idempotency_key, lease_id, lease_epoch, description,
                                    workspace, subject, capability, scope, grant_seq)
    VALUES ('mv67-d', l1, t1, 'an effect used for refusals', ws, who, 'effect.external',
            'lane/mv67', g_ok) RETURNING attempt_seq INTO a4;

  -- 1 control: the ordinary path. Success is spelled the contract's way and carries all three.
  UPDATE brain.effect_attempt
     SET settled_at = now(), outcome = 'success', completeness = 'complete',
         effect_state = 'applied', result_ref = 'provider-1', settled_by = who
   WHERE attempt_seq = a1;
  IF (SELECT outcome || '/' || completeness || '/' || effect_state
        FROM brain.effect_attempt WHERE attempt_seq = a1) <> 'success/complete/applied' THEN
    RAISE EXCEPTION 'migration 67: an ordinary success did not record all three fields';
  END IF;
  n := n + 1; controls := controls + 1;

  -- 2 control: THE SCENE THIS MIGRATION EXISTS FOR. Failed AND uncertain, together, in one row.
  --   Before this migration these two facts could not both be written down: choosing `failed`
  --   claimed a certainty the writer did not have, and choosing `ambiguous` withdrew the claim that
  --   it failed. A worker whose payment call timed out after the charge left had no true row.
  UPDATE brain.effect_attempt
     SET settled_at = now(), outcome = 'failed', completeness = 'uncertain',
         effect_state = 'unknown', settled_by = who,
         detail = 'provider timed out after the charge was submitted; the charge may have landed'
   WHERE attempt_seq = a2;
  IF (SELECT outcome || '/' || completeness || '/' || effect_state
        FROM brain.effect_attempt WHERE attempt_seq = a2) <> 'failed/uncertain/unknown' THEN
    RAISE EXCEPTION 'migration 67: failed-and-uncertain was not recorded as written';
  END IF;
  n := n + 1; controls := controls + 1;

  -- 3 control: and being able to SAY it is only worth something if it reaches a human. The row from
  --   check 2 is in effect_unresolved. Under the old view predicate, keyed on
  --   `outcome IN ('ambiguous','halted')`, an outcome of `failed` would not have been.
  IF NOT EXISTS (SELECT 1 FROM brain.effect_unresolved WHERE attempt_seq = a2) THEN
    RAISE EXCEPTION 'migration 67: an uncertain failure is not in effect_unresolved';
  END IF;
  n := n + 1; controls := controls + 1;

  -- 4 control: a partial outcome, which the old vocabulary had no word for at all. It applied
  --   something, so its effect_state is `applied` and its completeness is `partial`; those are
  --   different questions and this row answers both.
  UPDATE brain.effect_attempt
     SET settled_at = now(), outcome = 'partial', completeness = 'partial',
         effect_state = 'applied', result_ref = 'provider-2', settled_by = who,
         detail = 'three of five recipients were sent'
   WHERE attempt_seq = a3;
  IF (SELECT outcome || '/' || completeness || '/' || effect_state
        FROM brain.effect_attempt WHERE attempt_seq = a3) <> 'partial/partial/applied' THEN
    RAISE EXCEPTION 'migration 67: a partial outcome did not record as written';
  END IF;
  n := n + 1; controls := controls + 1;

  -- 5 control: the backfill reached every settled row that existed before this migration ran.
  --   Stated with its denominator: a zero-row pass here would mean nothing.
  SELECT count(*) INTO before_n FROM brain.effect_attempt
   WHERE settled_at IS NOT NULL AND idempotency_key NOT LIKE 'mv67-%';
  IF before_n < 2 THEN
    RAISE EXCEPTION 'migration 67: only % pre-existing settled rows, so the backfill check proves '
      'nothing. The seed block should guarantee at least the two it plants.', before_n;
  END IF;
  IF EXISTS (SELECT 1 FROM brain.effect_attempt
              WHERE settled_at IS NOT NULL AND (completeness IS NULL OR effect_state IS NULL)) THEN
    RAISE EXCEPTION 'migration 67: a settled row carries no completeness';
  END IF;
  -- The two seeded rows specifically, read back in the new vocabulary. A count is not enough: the
  -- backfill could have filled every row with the same wrong pair and still satisfied a count.
  IF (SELECT outcome || '/' || completeness || '/' || effect_state FROM brain.effect_attempt
       WHERE idempotency_key = 'mv67seed-ambiguous') <> 'ambiguous/uncertain/unknown'
     OR (SELECT outcome || '/' || completeness || '/' || effect_state FROM brain.effect_attempt
          WHERE idempotency_key = 'mv67seed-halted') <> 'halted/uncertain/unknown' THEN
    RAISE EXCEPTION 'migration 67: the seeded old-vocabulary rows did not backfill as documented';
  END IF;
  RAISE NOTICE 'migration 67 check 5: % settled rows predating this migration, all carrying a completeness and an effect_state',
    before_n;
  n := n + 1; controls := controls + 1;

  -- 6 control: THE VIEW LOST NOBODY. Recomputed, not asserted in prose. The old predicate's set was
  --   captured into a temp table before the view was replaced; every attempt_seq in it must still
  --   be visible through the new view. A rewrite of a human-review queue that quietly drops rows is
  --   the worst possible outcome of a cosmetic change, and the same sentence is in migration 65 for
  --   the same reason.
  SELECT count(*) INTO before_n FROM mv67_unresolved_before;
  IF before_n < 2 THEN
    RAISE EXCEPTION 'migration 67: the old predicate matched only % rows, so a superset assertion '
      'over it proves nothing. This check refuses to pass over an empty set.', before_n;
  END IF;
  SELECT count(*) INTO lost FROM mv67_unresolved_before b
   WHERE NOT EXISTS (SELECT 1 FROM brain.effect_unresolved u WHERE u.attempt_seq = b.attempt_seq);
  SELECT count(*) INTO after_n FROM brain.effect_unresolved;
  IF lost <> 0 THEN
    RAISE EXCEPTION 'migration 67: the new effect_unresolved lost % of % rows the old one showed',
      lost, before_n;
  END IF;
  RAISE NOTICE 'migration 67 check 6: old predicate showed % rows, new view shows %, lost 0',
    before_n, after_n;
  n := n + 1; controls := controls + 1;

  -- 7 refusal: RC-I2. Success over an effect that did not apply.
  BEGIN
    UPDATE brain.effect_attempt
       SET settled_at = now(), outcome = 'success', completeness = 'complete',
           effect_state = 'not_applied', result_ref = 'r', settled_by = who
     WHERE attempt_seq = a4;
    RAISE EXCEPTION 'migration 67: RC-I2 did not refuse success over a not_applied effect';
  EXCEPTION WHEN check_violation THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 8 refusal: RC-I2 again, and this is the shape that matters in practice. Nobody writes
  --   success-over-not_applied on purpose; what a hurried worker writes is success over an effect
  --   whose state it never established.
  BEGIN
    UPDATE brain.effect_attempt
       SET settled_at = now(), outcome = 'success', completeness = 'complete',
           effect_state = 'unknown', result_ref = 'r', settled_by = who
     WHERE attempt_seq = a4;
    RAISE EXCEPTION 'migration 67: RC-I2 did not refuse success over an unknown effect';
  EXCEPTION WHEN check_violation THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 9 refusal: a success that admits it is partial. Contradictory on its face; refused.
  BEGIN
    UPDATE brain.effect_attempt
       SET settled_at = now(), outcome = 'success', completeness = 'partial',
           effect_state = 'applied', result_ref = 'r', settled_by = who
     WHERE attempt_seq = a4;
    RAISE EXCEPTION 'migration 67: success with completeness partial was NOT refused';
  EXCEPTION WHEN check_violation THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 10 refusal: AMBIGUOUS REPORTING COMPLETE. C01 negative fixture receipt.neg.03.
  BEGIN
    UPDATE brain.effect_attempt
       SET settled_at = now(), outcome = 'ambiguous', completeness = 'complete',
           effect_state = 'applied', settled_by = who, detail = 'unsure'
     WHERE attempt_seq = a4;
    RAISE EXCEPTION 'migration 67: an ambiguous outcome reporting complete was NOT refused';
  EXCEPTION WHEN check_violation THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 11 refusal: PARTIAL REPORTING COMPLETE. C01 negative fixture receipt.neg.02.
  BEGIN
    UPDATE brain.effect_attempt
       SET settled_at = now(), outcome = 'partial', completeness = 'complete',
           effect_state = 'applied', result_ref = 'r', settled_by = who
     WHERE attempt_seq = a4;
    RAISE EXCEPTION 'migration 67: a partial outcome reporting complete was NOT refused';
  EXCEPTION WHEN check_violation THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 12 refusal: complete over an unknown effect state, whatever the outcome. The general form.
  BEGIN
    UPDATE brain.effect_attempt
       SET settled_at = now(), outcome = 'failed', completeness = 'complete',
           effect_state = 'unknown', settled_by = who
     WHERE attempt_seq = a4;
    RAISE EXCEPTION 'migration 67: completeness complete over an unknown effect was NOT refused';
  EXCEPTION WHEN check_violation THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 13 refusal: uncertainty with nothing said about it. Migration 59 required this of `ambiguous`;
  --   the requirement now follows the axis rather than the one outcome.
  BEGIN
    UPDATE brain.effect_attempt
       SET settled_at = now(), outcome = 'failed', completeness = 'uncertain',
           effect_state = 'unknown', settled_by = who, detail = NULL
     WHERE attempt_seq = a4;
    RAISE EXCEPTION 'migration 67: an uncertain settle with no detail was NOT refused';
  EXCEPTION WHEN check_violation THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 14 refusal: settling while declining to say how sure you are.
  BEGIN
    UPDATE brain.effect_attempt
       SET settled_at = now(), outcome = 'failed', settled_by = who
     WHERE attempt_seq = a4;
    RAISE EXCEPTION 'migration 67: a settle with no completeness was NOT refused';
  EXCEPTION WHEN check_violation THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 15 refusal: THE OLD SPELLING IS GONE. If `succeeded` still writes, the rename is a suggestion
  --   and two vocabularies are live at once.
  BEGIN
    UPDATE brain.effect_attempt
       SET settled_at = now(), outcome = 'succeeded', completeness = 'complete',
           effect_state = 'applied', result_ref = 'r', settled_by = who
     WHERE attempt_seq = a4;
    RAISE EXCEPTION 'migration 67: the old value succeeded was still accepted';
  EXCEPTION WHEN check_violation THEN n := n + 1; refusals := refusals + 1;
  END;

  -- AND THE OLD NAME EXISTS NOWHERE ELSE. Not a numbered check: it is the migration-65 lesson
  -- applied, and it belongs with the rename rather than beside it. Function bodies, view
  -- definitions and constraint expressions are all places a stale literal survives a value-level
  -- rename and keeps working until it silently does not.
  -- `prokind = 'f'` because pg_get_functiondef refuses aggregates, and brain carries some.
  -- Without it this scan raises instead of reporting, which is a check that cannot fail
  -- honestly: it dies before it can say yes or no.
  SELECT string_agg(src, ', ') INTO stray FROM (
    SELECT p.proname AS src FROM pg_proc p JOIN pg_namespace ns ON ns.oid = p.pronamespace
      WHERE ns.nspname = 'brain' AND p.prokind = 'f'
        AND pg_get_functiondef(p.oid) LIKE '%succeeded%'
    UNION ALL
    SELECT c.relname FROM pg_class c JOIN pg_namespace ns ON ns.oid = c.relnamespace
      WHERE ns.nspname = 'brain' AND c.relkind = 'v'
        AND pg_get_viewdef(c.oid) LIKE '%succeeded%'
    UNION ALL
    SELECT con.conname FROM pg_constraint con JOIN pg_namespace ns ON ns.oid = con.connamespace
      WHERE ns.nspname = 'brain' AND pg_get_constraintdef(con.oid) LIKE '%succeeded%'
  ) s;
  IF stray IS NOT NULL THEN
    RAISE EXCEPTION 'migration 67: the old value succeeded still appears in: %', stray;
  END IF;
  IF EXISTS (SELECT 1 FROM brain.effect_attempt WHERE outcome = 'succeeded') THEN
    RAISE EXCEPTION 'migration 67: rows still carry outcome succeeded';
  END IF;

  -- fixture teardown
  ALTER TABLE brain.effect_attempt DISABLE TRIGGER effect_attempt_settles_once;
  ALTER TABLE brain.execution_lease DISABLE TRIGGER execution_lease_halts_its_effects;
  ALTER TABLE brain.execution_lease DISABLE TRIGGER execution_lease_release_only;
  ALTER TABLE brain.authority_grant DISABLE TRIGGER authority_grant_append_only;
  DELETE FROM brain.effect_attempt WHERE idempotency_key LIKE 'mv67-%'
                                      OR idempotency_key LIKE 'mv67seed-%';
  DELETE FROM brain.execution_lease WHERE workspace = 'ws-mv67seed';
  DELETE FROM brain.work_item WHERE title = 'migration 67 seed';
  DELETE FROM brain.authority_grant WHERE workspace = 'ws-mv67seed';
  DELETE FROM brain.execution_lease WHERE work_item_id = item;
  DELETE FROM brain.work_item WHERE id = item;
  DELETE FROM brain.authority_grant WHERE grant_seq = g_ok;
  ALTER TABLE brain.authority_grant ENABLE TRIGGER authority_grant_append_only;
  ALTER TABLE brain.execution_lease ENABLE TRIGGER execution_lease_release_only;
  ALTER TABLE brain.execution_lease ENABLE TRIGGER execution_lease_halts_its_effects;
  ALTER TABLE brain.effect_attempt ENABLE TRIGGER effect_attempt_settles_once;
  DELETE FROM brain.agent WHERE name IN (ag, '_mv67_seed_agent');
  IF made_human THEN
    DELETE FROM brain.human_role WHERE human = who AND granted_by = 'migration 67 fixture';
  END IF;
  IF EXISTS (SELECT 1 FROM brain.human_role WHERE granted_by = 'migration 67 seed') THEN
    DELETE FROM brain.human_role WHERE granted_by = 'migration 67 seed';
  END IF;

  IF n <> 15 OR refusals <> 9 OR controls <> 6 THEN
    RAISE EXCEPTION 'migration 67: expected 15 checks as 9 refusals and 6 controls, ran % as % and %',
      n, refusals, controls;
  END IF;
  RAISE NOTICE 'migration 67: % of 15 checks passed, % refusals watched refusing and % positive controls. An outcome and a completeness are now two questions, and RC-I2 is enforced.',
    n, refusals, controls;
END $checks$;

INSERT INTO brain.schema_migration (version, name)
  VALUES (67, '0067_an_outcome_and_a_completeness_are_two_questions')
  ON CONFLICT (version) DO NOTHING;

COMMIT;
