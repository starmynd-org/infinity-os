-- 0016_recommendation_rejection_login.sql -- the OTHER half of the decision: a rejection's
-- decider becomes a database LOGIN too.  Schema version 33.
--
-- Task 0313, child of 0290.  0290 gated the transition into `accepted` and said, in its own file
-- header, exactly what it was leaving open:
--
--     `recommend reject` IS NOT GATED HERE.  A rejection dispatches nothing, so it is not
--     invariant 1, and widening this file to cover it would have changed a verb no measurement
--     was reported against.  It is adjacent work and it is named on task 0290's thread.
--
-- This is that adjacent work.  Additive and re-runnable: it creates ONE new function and ONE new
-- trigger.  No table is altered, no column is added, nothing is dropped, and
-- `brain.recommendation_human_decider` -- migration 32's function -- is not touched.
--
-- READ THE LEDGER BEFORE APPLYING THIS.  `brain.schema_migration` on live held 31 as its maximum
-- at 2026-08-19T03:0xZ, and version 32 is claimed by `queue/schema/0015_recommendation_human_
-- login.sql`, which is IN THIS TREE AND NOT YET APPLIED TO LIVE.  So the ledger's next free
-- number and this tree's next free number are not the same number, and the tree's is the binding
-- one: two files claiming 32 would mean one of them applies nothing and still reports success,
-- because every migration ends ON CONFLICT DO NOTHING.  33 is free in both.  Four lanes write
-- this tree; a number free ten minutes ago is not a number free now, and the guard below refuses
-- rather than applying its DDL against a taken version.
--
-- ----------------------------------------------------------------------------------------
-- WHY A REJECTION NEEDS THE SAME GATE, WHEN IT DISPATCHES NOTHING
-- ----------------------------------------------------------------------------------------
--
-- Because the thing a rejection moves is not work, it is THE FALSIFIER.  `brain.queue_acted_on`
-- (`0007_queue.sql`) is the view PLAN.md points at when it says this layer is cut back to events
-- plus paging under roughly 30 percent acted-on.  Its rejection arm is:
--
--     count(*) FILTER (WHERE state = 'rejected')  AS rejected
--     ... GROUP BY coalesce(template_id, '(novel)')
--
-- Read that filter twice.  It counts BY STATE and groups BY TEMPLATE.  It does not look at
-- `decided_by`, it does not look at `actor_type`, and it has no way to tell a rejection the
-- operator made from one an agent wrote.  `recommend_reject`'s own docstring names the
-- consequence: "a template whose recommendations are consistently rejected is a dead playbook,
-- and that is a prunable finding."  So an agent that can write `state = 'rejected'` can prune a
-- playbook the operator never rejected, and it is a QUIETER forgery than a forged acceptance
-- because nothing is spawned for anybody to notice.
--
-- THIS IS WHY "just record actor_type honestly" WAS CONSIDERED AND REJECTED.  Task 0313's brief
-- offered it as the alternative: keep the verb open to any caller and book `actor_type = 'ai'`
-- when the connection is not a human, so the row at least stops implying a human decided.  That
-- makes the ROW honest and leaves the FALSIFIER movable, because the reader above never reads the
-- column that was made honest.  A measurement that the things being measured can move is not a
-- measurement.  The row is made honest here as well -- `actor_type` is forced to `human`, which
-- is now true by construction -- but that is the smaller half.
--
-- ----------------------------------------------------------------------------------------
-- WHAT THIS COSTS, STATED RATHER THAN DISCOVERED LATER
-- ----------------------------------------------------------------------------------------
--
-- A host with no operator credential loses `queue reject` entirely: `store/transitions.py::
-- _connect` raises `StoreConfigError` before any SQL is sent.  That is a real cost and it is the
-- one 0313's brief flagged as the reason the answer was not obvious -- rejecting is cheap and
-- reversible in a way accepting is not, so gating it might be over-charging for it.
--
-- It is charged anyway, for two reasons that are the repository's own and not this file's.  The
-- codebase ALREADY accepts this exact cost one verb over, in `queue/human_queue/cli.py`'s words:
-- "On a host with no operator credential this fails closed in `_connect`, which is correct:
-- there, nobody is the operator."  And `recommend_reject`'s docstring already promised the
-- symmetry this file is delivering: rejections "are recorded as fully as acceptances."  An agent
-- that believes a recommendation is wrong has the same route it has always had for believing one
-- is right: say so on the thread.  D00 contract rule 4 is about who DECIDES, and rejecting is
-- deciding.
--
-- ----------------------------------------------------------------------------------------
-- WHY A SECOND TRIGGER RATHER THAN FOUR MORE LINES IN MIGRATION 32'S FUNCTION
-- ----------------------------------------------------------------------------------------
--
-- Because `0015_recommendation_human_login.sql` advertises itself as re-runnable and does
-- `CREATE OR REPLACE FUNCTION brain.recommendation_human_decider()`.  Folding the rejection rule
-- into that body would mean any later re-application of 0015 -- by hand, by a rebuild, by a
-- scratch database built from a tree that has 0015 and not this file -- SILENTLY DELETES this
-- gate and leaves a green ledger row saying 33 was applied.  A guard that a re-run of an older
-- file removes is not a guard.  Two functions, two triggers, and neither file can erase the
-- other.  They fire in name order (`recommendation_human_decider` then
-- `recommendation_rejection_decider`); both are BEFORE row triggers that return NEW, and they
-- guard disjoint transitions, so the order between them is not load-bearing.
--
-- ----------------------------------------------------------------------------------------
-- WHAT THIS DOES **NOT** CLOSE
-- ----------------------------------------------------------------------------------------
--
-- THE HOST RESIDUAL, the same one `store/SECRETS.md` records for every role and migration 20
-- states for `work_item`.  On a `local-attended` host every credential file sits in one 0700
-- directory under one UID, so an OS process that can read the operator's secret can connect as
-- the operator.  This trigger is exactly as strong as `actor_type = 'human'` on `brain.work_item`
-- is, and no stronger.  What is closed absolutely is the case that exists: a process holding the
-- `brain_runtime` credential -- what every agent surface is configured with -- has no route to a
-- rejected recommendation under any decider name.
--
-- `expired` IS NOT GATED, and that is deliberate rather than overlooked.  `brain.queue_acted_on`
-- counts it in its own column, no code path in this tree writes it (grepped: the string appears
-- in the CHECK constraint and in the view, and in no Python), and an expiry sweep is a clock
-- acting, not a human.  When something starts writing it, the question of who may is a new one.
--
-- THE STATE COLUMN IS STILL ONE STATEMENT DEEP, the residual migration 22 records and 0015
-- repeats: nothing guards `brain.recommendation.state` transitions in general.  This file guards
-- the transition INTO `rejected`, as 32 guards the transition INTO `accepted`.  Between them the
-- two transitions that move the falsifier are now both a human's.

\set ON_ERROR_STOP on

DO $$
DECLARE taken text;
BEGIN
  SELECT name INTO taken FROM brain.schema_migration WHERE version = 33;
  IF taken IS NOT NULL AND taken <> '0016_recommendation_rejection_login' THEN
    RAISE EXCEPTION 'schema version 33 is already held by %, not '
                    '0016_recommendation_rejection_login. Pick the next version by reading '
                    'brain.schema_migration AND by grepping the tree for the number, never by '
                    'listing a directory: at the time this was written the ledger''s next free '
                    'number was 32 and the tree''s was 33.', taken;
  END IF;
END $$;

BEGIN;

-- This file is meaningless without migration 20: `brain.current_human()` is the whole mechanism.
-- Refusing here beats installing a trigger that calls a function that does not exist, which would
-- turn every rejection into an UndefinedFunction at the moment the operator tried to decide.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                  WHERE n.nspname = 'brain' AND p.proname = 'current_human') THEN
    RAISE EXCEPTION 'brain.current_human() does not exist, so migration 20 has not been applied '
                    'to this database. Apply migrations/0020_human_actor_identity.sql first: this '
                    'file is that migration applied to a third question.';
  END IF;
END $$;

CREATE OR REPLACE FUNCTION brain.recommendation_rejection_decider() RETURNS trigger AS $$
DECLARE
  is_agent boolean;
  the_human text;
BEGIN
  -- THE TRANSITION INTO `rejected`, from either direction. The INSERT arm is not theoretical:
  -- `brain.queue_acted_on` groups by `template_id` and counts raised AND rejected, so a row
  -- INSERTed already-rejected against a template moves that template's rate exactly as far as
  -- an UPDATE does. Ordinary later work on a row that is ALREADY rejected is left alone, which
  -- is migration 20's rule: establishing the attribute is the privileged act, living with it
  -- afterwards is not.
  IF NEW.state IS DISTINCT FROM 'rejected' THEN
    RETURN NEW;
  END IF;
  IF TG_OP = 'UPDATE' AND OLD.state IS NOT DISTINCT FROM 'rejected' THEN
    RETURN NEW;
  END IF;

  -- ORDER IS DELIBERATE AND IT IS THE SAME ORDER MIGRATION 32 USES. The specific refusals come
  -- FIRST so that a caller who is wrong in a specific way is told the specific true reason; the
  -- login check is last because it is the one that answers NULL for every connection that is not
  -- a human, including a superuser one, and putting it first would replace every message below
  -- with "not a human login". Being refused for the most specific true reason is the property;
  -- "refused" alone is not.
  IF NEW.decided_by IS NULL OR btrim(NEW.decided_by) = '' THEN
    RAISE EXCEPTION 'recommendation % cannot be rejected with no decider recorded', NEW.id
      USING HINT = 'A rejection is the falsifier''s other half -- brain.queue_acted_on counts it '
                   'and template pruning reads that count. Record who decided.';
  END IF;
  IF NEW.decided_at IS NULL THEN
    RAISE EXCEPTION 'recommendation % cannot be rejected with no decided_at', NEW.id;
  END IF;
  SELECT EXISTS (SELECT 1 FROM brain.agent WHERE name = NEW.decided_by) INTO is_agent;
  IF is_agent THEN
    RAISE EXCEPTION 'recommendation % cannot be rejected by %, which is a registered agent',
                    NEW.id, NEW.decided_by
      USING HINT = 'D00 contract rule 4 is about who DECIDES, and rejecting is deciding. An '
                   'agent that believes this recommendation is wrong may say so on the thread; '
                   'it may not reject it. Kept as defence in depth and no longer as the gate: '
                   'this test fails open by construction, since every name that is NOT an agent '
                   'passes it and the agent picks the name.';
  END IF;
  IF NEW.actor_type IS DISTINCT FROM 'human'::brain.actor_type THEN
    RAISE EXCEPTION 'recommendation % rejected with actor_type %, which must be human',
                    NEW.id, COALESCE(NEW.actor_type::text, 'null')
      USING HINT = 'Until task 0313 this column was left NULL on every rejection, which reads as '
                   '"unknown" and is indistinguishable from "nobody recorded it". It is forced '
                   'here because the check below now makes it true rather than merely asserted.';
  END IF;

  -- THE GATE. Everything above is a string the caller chose; this is not. `brain.current_human()`
  -- answers from `session_user`, fixed at authentication, and no SQL a client can send changes
  -- it -- `SET ROLE` moves `current_user` and not this.
  the_human := brain.current_human();
  IF the_human IS NULL THEN
    RAISE EXCEPTION 'refusing to reject recommendation %: this connection is %, which is not a '
                    'human login', NEW.id, session_user
      USING HINT = 'A rejection dispatches nothing, which is why migration 32 left it open. It '
                   'moves brain.queue_acted_on instead, and that view counts by STATE and groups '
                   'by TEMPLATE -- it never reads decided_by or actor_type. So an agent that can '
                   'write this state can prune a playbook the operator never rejected, and no '
                   'honestly-recorded actor_type would stop it, because the reader does not read '
                   'that column. The operator''s surfaces connect as a role mapped in '
                   'brain.human_role; provision one with store/bin/provision-operator.sh. A '
                   'process holding the brain_runtime credential has no route to this row and is '
                   'not supposed to.',
          ERRCODE = 'insufficient_privilege';
  END IF;
  IF NEW.decided_by <> the_human THEN
    RAISE EXCEPTION 'recommendation % records % as the decider and this connection is %. '
                    'Refusing rather than storing either one: the decided_by column is the '
                    'record of who rejected this proposal.', NEW.id, NEW.decided_by, the_human
      USING HINT = 'Write brain.current_human() into decided_by, or reject from the login whose '
                   'name you meant. `recommend reject` does the first.',
          ERRCODE = 'insufficient_privilege';
  END IF;

  RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS recommendation_rejection_decider ON brain.recommendation;
CREATE TRIGGER recommendation_rejection_decider
  BEFORE INSERT OR UPDATE ON brain.recommendation
  FOR EACH ROW EXECUTE FUNCTION brain.recommendation_rejection_decider();

COMMENT ON FUNCTION brain.recommendation_rejection_decider() IS
  'The falsifier''s other half. brain.queue_acted_on counts rejections by STATE and groups by '
  'TEMPLATE, reading neither decided_by nor actor_type, so an agent able to write state = '
  '''rejected'' could prune a playbook the operator never rejected. Since migration 33 the '
  'decider on a rejection is a login mapped in brain.human_role, read through '
  'brain.current_human(), exactly as migration 32 made it on an acceptance. Separate from '
  'brain.recommendation_human_decider() on purpose: that function is CREATE OR REPLACEd by a '
  'file that advertises itself as re-runnable, and a guard an older file''s re-run deletes is '
  'not a guard. Proven by engine/tests/test_recommendation_human_login.py.';

COMMENT ON COLUMN brain.recommendation.decided_by IS
  'Who decided, on BOTH halves of the decision. Since migration 32 (accept) and migration 33 '
  '(reject) this is the database''s answer to brain.current_human() and not a string the calling '
  'process chose; the two triggers refuse a decided_by that disagrees with the connection rather '
  'than storing either name. It is the input to engine/swarm_engine/accept.py:disagreement_report '
  'and to brain.queue_acted_on, which is this layer''s falsifier.';

INSERT INTO brain.schema_migration (version, name)
     VALUES (33, '0016_recommendation_rejection_login')
  ON CONFLICT (version) DO NOTHING;

COMMIT;
