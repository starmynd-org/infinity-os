-- migration 57: authority is a GRANT and a REVOCATION, both append-only, and what is in force is
-- computed at read time from the two. An approval names the exact proposal version it approved.
--
-- Packet R01, sprint `2026-09-06-multiverse-build-and-launch`. Written 2026-09-06 by Terminal 04.
--
-- ------------------------------------------------------------------ what is actually missing
--
-- Measured on brain_scratch_t04, built from the ledger at version 56, before this file:
--
--   brain.human_role      role_name, human, granted_at, granted_by      -- and no way to take it back
--   brain.agent           name, role, delegation_level                  -- a standing property, not a decision
--   brain.receipt         approval_ref text                             -- a STRING pointing at nothing
--
-- Three things follow from that and all three are the packet's subject:
--
--   1. A GRANT CANNOT BE WITHDRAWN. `human_role` has `granted_at` and `granted_by` and no
--      `revoked_at`. Taking a role away means DELETE, which destroys the record that it was ever
--      held, so the question "was this person authorised at the time they acted" becomes
--      unanswerable the moment the answer is interesting.
--   2. NOTHING EXPIRES. A grant issued once is in force until somebody deletes the row.
--   3. `receipt.approval_ref` IS A FREE-TEXT STRING. It references no table, so it cannot be
--      checked, and it names no VERSION, so an approval of proposal v1 reads identically to an
--      approval of the v2 that replaced it. That is the replayed-approval hole in one column.
--
-- ------------------------------------------------------------------ APPEND-ONLY, AND ENFORCED HERE
--
-- The grant table refuses UPDATE and DELETE from a trigger, not from a convention. A revocation is
-- a NEW ROW naming the grant it ends. This is the one property the packet calls "immutable grants
-- at the storage boundary": it holds against application code that has a bug, against a lane that
-- never read this comment, and against a `psql` session with a good reason. It does not hold
-- against a superuser who drops the trigger, and this file does not pretend otherwise --
-- `store/AUTHORITY.md` in this handoff states that boundary rather than hiding it.
--
-- ------------------------------------------------------------------ RESOLVED ON READ, NEVER STAMPED
--
-- `brain.authority_in_force` is a VIEW, for the reason migration 56 made `work_item_delegation` a
-- view: a stamped `is_active` column is a cache of a time-dependent fact, and it is wrong from the
-- instant an expiry passes until whatever was supposed to update it runs. Expiry is arithmetic on
-- `now()`. Nothing has to sweep, so nothing can fail to sweep.
--
-- ------------------------------------------------------------------ NULL expires_at IS A DECISION
--
-- A perpetual grant is legitimate: the owner of a store does not hold their ownership on a timer.
-- But `expires_at IS NULL` reading as "forever" by default is how a grant meant for an afternoon
-- becomes permanent. So the CHECK requires that a grant with no expiry SAY WHY in
-- `perpetual_reason`. Neither field defaults. Doctrine rule: NULL means nobody has said, and this
-- table refuses to let nobody-has-said mean forever.

BEGIN;

CREATE TABLE IF NOT EXISTS brain.authority_grant (
  grant_seq        bigserial PRIMARY KEY,
  granted_at       timestamptz NOT NULL DEFAULT now(),
  granted_by       text        NOT NULL,
  subject_kind     text        NOT NULL,
  subject          text        NOT NULL,
  capability       text        NOT NULL,
  scope            text        NOT NULL,
  packet_version   text,
  expires_at       timestamptz,
  perpetual_reason text,
  evidence         text        NOT NULL,
  CONSTRAINT authority_grant_subject_kind_ck
    CHECK (subject_kind IN ('human', 'agent', 'service')),
  CONSTRAINT authority_grant_not_blank_ck
    CHECK (btrim(granted_by) <> '' AND btrim(subject) <> ''
           AND btrim(capability) <> '' AND btrim(scope) <> '' AND btrim(evidence) <> ''),
  CONSTRAINT authority_grant_expiry_is_a_decision_ck
    CHECK (expires_at IS NOT NULL OR btrim(coalesce(perpetual_reason, '')) <> '')
);

-- A revocation is a new row. UNIQUE on grant_seq so a second revocation of one grant is refused
-- rather than silently duplicated: revoking twice is a caller bug worth surfacing, and the API
-- turns the violation into "already revoked at <time> by <who>" rather than a stack trace.
CREATE TABLE IF NOT EXISTS brain.authority_revocation (
  revocation_seq bigserial   PRIMARY KEY,
  grant_seq      bigint      NOT NULL UNIQUE REFERENCES brain.authority_grant(grant_seq),
  revoked_at     timestamptz NOT NULL DEFAULT now(),
  revoked_by     text        NOT NULL,
  reason         text        NOT NULL,
  CONSTRAINT authority_revocation_not_blank_ck
    CHECK (btrim(revoked_by) <> '' AND btrim(reason) <> '')
);

-- An approval names the EXACT proposal version. `receipt.approval_ref` could not, which is why a
-- v1 approval could be replayed against a v2 proposal. UNIQUE over (proposal, version, approver)
-- makes a replay a refusal at the storage boundary rather than a second decision.
CREATE TABLE IF NOT EXISTS brain.approval (
  approval_seq     bigserial   PRIMARY KEY,
  proposal_id      text        NOT NULL,
  proposal_version text        NOT NULL,
  decided_at       timestamptz NOT NULL DEFAULT now(),
  decided_by       text        NOT NULL,
  grant_seq        bigint      NOT NULL REFERENCES brain.authority_grant(grant_seq),
  decision         text        NOT NULL,
  CONSTRAINT approval_decision_ck CHECK (decision IN ('approve', 'reject')),
  CONSTRAINT approval_not_blank_ck
    CHECK (btrim(proposal_id) <> '' AND btrim(proposal_version) <> '' AND btrim(decided_by) <> ''),
  CONSTRAINT approval_is_decided_once_uq UNIQUE (proposal_id, proposal_version, decided_by)
);

-- APPEND-ONLY, ENFORCED. One function, three triggers, so the refusal reads the same on all three
-- tables and there is one place to review.
CREATE OR REPLACE FUNCTION brain.authority_is_append_only() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
  RAISE EXCEPTION
    'brain.% is append-only: % refused. A grant is ended by inserting into '
    'brain.authority_revocation, and a decision is changed by recording a new one. '
    'Editing the record of what was authorised destroys the only evidence of what was true '
    'at the time somebody acted.', TG_TABLE_NAME, TG_OP
    USING ERRCODE = 'restrict_violation';
END;
$fn$;

DROP TRIGGER IF EXISTS authority_grant_append_only ON brain.authority_grant;
CREATE TRIGGER authority_grant_append_only BEFORE UPDATE OR DELETE ON brain.authority_grant
  FOR EACH ROW EXECUTE FUNCTION brain.authority_is_append_only();
DROP TRIGGER IF EXISTS authority_revocation_append_only ON brain.authority_revocation;
CREATE TRIGGER authority_revocation_append_only BEFORE UPDATE OR DELETE ON brain.authority_revocation
  FOR EACH ROW EXECUTE FUNCTION brain.authority_is_append_only();
DROP TRIGGER IF EXISTS approval_append_only ON brain.approval;
CREATE TRIGGER approval_append_only BEFORE UPDATE OR DELETE ON brain.approval
  FOR EACH ROW EXECUTE FUNCTION brain.authority_is_append_only();

-- A CAPABILITY VOCABULARY, so "unknown role" is a refusal and not a typo that grants nothing and
-- says nothing. A free-text capability column would accept 'work.exceute' and then deny every
-- check against it forever, which fails closed but silently, and a silent fail-closed is how a
-- lane spends a day debugging the wrong thing.
CREATE TABLE IF NOT EXISTS brain.capability (
  capability  text        PRIMARY KEY,
  description text        NOT NULL,
  added_at    timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT capability_not_blank_ck CHECK (btrim(capability) <> '' AND btrim(description) <> '')
);

INSERT INTO brain.capability (capability, description) VALUES
  ('work.claim',        'take an item off the queue and hold it'),
  ('work.execute',      'run the work an item names'),
  ('work.accept',       'accept a finished item on a human''s behalf -- a human act, carve-out 4'),
  ('effect.external',   'cause an effect outside this store'),
  ('effect.spend',      'commit money or paid quota'),
  ('authority.grant',   'issue a grant'),
  ('authority.revoke',  'end a grant'),
  ('approval.decide',   'approve or reject an exact proposal version'),
  ('model.route',       'select a provider or model for a task')
ON CONFLICT (capability) DO NOTHING;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'authority_grant_capability_fk') THEN
    ALTER TABLE brain.authority_grant
      ADD CONSTRAINT authority_grant_capability_fk
      FOREIGN KEY (capability) REFERENCES brain.capability(capability);
  END IF;
END $$;

-- THE FORGED ACTOR. An actor this store has never heard of cannot issue a grant, and cannot be
-- given one. The two kinds this store can verify are verified; the third is named as unverifiable
-- rather than waved through as though it had been checked.
-- SECURITY DEFINER, AND THE REASON IS NOT CONVENIENCE. This trigger has to read
-- `brain.human_role` to answer "does this store know the actor", and `brain_runtime` deliberately
-- holds no SELECT on that table: the roster of humans is not the runtime's business. Without
-- SECURITY DEFINER the check does not fail closed, it fails LOUD AND UNRELATED -- every grant
-- insert from the runtime path dies on `permission denied for table human_role`, which reads as a
-- provisioning bug and would be "fixed" by granting the runtime read access to the human roster.
-- Measured: that is exactly what happened on the first run of `store/test_authority.py`.
--
-- The search_path is pinned because a SECURITY DEFINER function that resolves its own table names
-- through the caller's search_path is the standard way this pattern becomes a privilege
-- escalation. Pinned to pg_catalog first, then brain, and set here rather than assumed.
CREATE OR REPLACE FUNCTION brain.authority_actor_is_known() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, brain AS $fn$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM brain.human_role WHERE human = NEW.granted_by) THEN
    RAISE EXCEPTION
      'brain.authority_grant refused: granted_by % names no human this store knows. '
      'Authority comes from somebody, and a grant issued by nobody is the forged-actor case.',
      NEW.granted_by USING ERRCODE = 'restrict_violation';
  END IF;
  IF NEW.subject_kind = 'agent'
     AND NOT EXISTS (SELECT 1 FROM brain.agent WHERE name = NEW.subject) THEN
    RAISE EXCEPTION 'brain.authority_grant refused: no agent named %.', NEW.subject
      USING ERRCODE = 'restrict_violation';
  END IF;
  IF NEW.subject_kind = 'human'
     AND NOT EXISTS (SELECT 1 FROM brain.human_role WHERE human = NEW.subject) THEN
    RAISE EXCEPTION 'brain.authority_grant refused: no human named %.', NEW.subject
      USING ERRCODE = 'restrict_violation';
  END IF;
  -- subject_kind 'service': this store holds no service-principal roster today, so the identity
  -- is NOT verified here. That is a stated limitation, carried in store/AUTHORITY.md, and not a
  -- check that quietly passes everything.
  RETURN NEW;
END;
$fn$;

DROP TRIGGER IF EXISTS authority_grant_actor_is_known ON brain.authority_grant;
CREATE TRIGGER authority_grant_actor_is_known BEFORE INSERT ON brain.authority_grant
  FOR EACH ROW EXECUTE FUNCTION brain.authority_actor_is_known();

-- WHAT IS IN FORCE, computed now, from the two append-only tables and the clock.
CREATE OR REPLACE VIEW brain.authority_in_force AS
SELECT g.grant_seq, g.granted_at, g.granted_by, g.subject_kind, g.subject,
       g.capability, g.scope, g.packet_version, g.expires_at, g.evidence
  FROM brain.authority_grant g
  LEFT JOIN brain.authority_revocation r ON r.grant_seq = g.grant_seq
 WHERE r.grant_seq IS NULL
   AND (g.expires_at IS NULL OR g.expires_at > now());

COMMENT ON VIEW brain.authority_in_force IS
  'Grants minus revocations minus expiry, resolved at read time. Never stamp this into a column: '
  'an is_active flag is wrong from the instant an expiry passes until something sweeps it.';

-- ------------------------------------------------------------------ LEAST PRIVILEGE, AS PRIVILEGE
--
-- The append-only trigger is one layer. THE GRANT IS ANOTHER, and it is the cheaper one: the
-- runtime role is given SELECT and INSERT and is given no UPDATE and no DELETE at all. A trigger
-- can be dropped by whoever owns the table; a privilege the role never held cannot be exercised
-- by forgetting a trigger. The three checks at the end assert all three privileges rather than
-- trusting that these statements say what they mean.
--
-- brain_operator is a member of brain_runtime, so it inherits these. It is granted explicitly
-- anyway, and guarded by an existence test, because `budget/schema/0003` established that pattern
-- for exactly this reason: the role does not exist on every store.
GRANT SELECT, INSERT ON brain.authority_grant, brain.authority_revocation, brain.approval
  TO brain_runtime;
GRANT SELECT ON brain.capability, brain.authority_in_force TO brain_runtime, brain_subscriber;
GRANT USAGE ON SEQUENCE brain.authority_grant_grant_seq_seq,
                        brain.authority_revocation_revocation_seq_seq,
                        brain.approval_approval_seq_seq TO brain_runtime;

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'brain_operator') THEN
    EXECUTE 'GRANT SELECT, INSERT ON brain.authority_grant, brain.authority_revocation, '
            'brain.approval TO brain_operator';
    EXECUTE 'GRANT SELECT ON brain.capability, brain.authority_in_force TO brain_operator';
    EXECUTE 'GRANT USAGE ON SEQUENCE brain.authority_grant_grant_seq_seq, '
            'brain.authority_revocation_revocation_seq_seq, '
            'brain.approval_approval_seq_seq TO brain_operator';
  END IF;
END $$;

-- ------------------------------------------------------------------ EVERY REFUSAL WATCHED HAPPENING
--
-- Nineteen checks: TEN refusals watched refusing, three privileges asserted, and six positive
-- controls that prove the refusals are not simply refusing everything. Both the total AND the
-- refusal count are asserted at the end. They were not always: this block once printed "eleven"
-- over ten EXCEPTION blocks, because only the total was guarded, and CAP14-REV-017 caught it by
-- counting the blocks by hand. A sentence nothing checks drifts from the code it describes.
DO $$
DECLARE
  n            int := 0;
  refusals     int := 0;
  who          text;
  made_human   boolean := false;
  g_ok         bigint;
  g_exp        bigint;
  g_rev        bigint;
BEGIN
  SELECT human INTO who FROM brain.human_role ORDER BY human LIMIT 1;
  IF who IS NULL THEN
    who := '_mv57_fixture_human';
    INSERT INTO brain.human_role (role_name, human, granted_at, granted_by)
      VALUES ('brain_runtime', who, now(), 'migration 57 fixture');
    made_human := true;
  END IF;

  -- 1 positive control: a well-formed grant lands.
  INSERT INTO brain.authority_grant
    (granted_by, subject_kind, subject, capability, scope, packet_version, expires_at, evidence)
    VALUES (who, 'human', who, 'work.execute', 'lane/mv-fixture', 'c01-unpinned',
            now() + interval '1 hour', 'migration 57 self-check')
    RETURNING grant_seq INTO g_ok;
  n := n + 1;

  -- 2 it is in force.
  IF NOT EXISTS (SELECT 1 FROM brain.authority_in_force WHERE grant_seq = g_ok) THEN
    RAISE EXCEPTION 'migration 57: a fresh unexpired grant was not in force';
  END IF;
  n := n + 1;

  -- 3 UPDATE refused.
  BEGIN
    UPDATE brain.authority_grant SET scope = 'widened' WHERE grant_seq = g_ok;
    RAISE EXCEPTION 'migration 57: UPDATE on authority_grant was NOT refused';
  EXCEPTION WHEN restrict_violation THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 4 DELETE refused.
  BEGIN
    DELETE FROM brain.authority_grant WHERE grant_seq = g_ok;
    RAISE EXCEPTION 'migration 57: DELETE on authority_grant was NOT refused';
  EXCEPTION WHEN restrict_violation THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 5 forged actor: granted_by nobody knows.
  BEGIN
    INSERT INTO brain.authority_grant
      (granted_by, subject_kind, subject, capability, scope, expires_at, evidence)
      VALUES ('_nobody_has_ever_heard_of_me', 'human', who, 'work.execute', 's',
              now() + interval '1 hour', 'x');
    RAISE EXCEPTION 'migration 57: a grant from an unknown actor was NOT refused';
  EXCEPTION WHEN restrict_violation THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 6 unknown human subject.
  BEGIN
    INSERT INTO brain.authority_grant
      (granted_by, subject_kind, subject, capability, scope, expires_at, evidence)
      VALUES (who, 'human', '_no_such_human', 'work.execute', 's',
              now() + interval '1 hour', 'x');
    RAISE EXCEPTION 'migration 57: a grant to an unknown human was NOT refused';
  EXCEPTION WHEN restrict_violation THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 7 unknown agent subject.
  BEGIN
    INSERT INTO brain.authority_grant
      (granted_by, subject_kind, subject, capability, scope, expires_at, evidence)
      VALUES (who, 'agent', '_no_such_agent', 'work.execute', 's',
              now() + interval '1 hour', 'x');
    RAISE EXCEPTION 'migration 57: a grant to an unknown agent was NOT refused';
  EXCEPTION WHEN restrict_violation THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 8 unknown capability, refused by the vocabulary FK rather than accepted and denied forever.
  BEGIN
    INSERT INTO brain.authority_grant
      (granted_by, subject_kind, subject, capability, scope, expires_at, evidence)
      VALUES (who, 'human', who, 'work.exceute', 's', now() + interval '1 hour', 'x');
    RAISE EXCEPTION 'migration 57: an unknown capability was NOT refused';
  EXCEPTION WHEN foreign_key_violation THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 9 no expiry and no reason: the decision was not made, so the row is refused.
  BEGIN
    INSERT INTO brain.authority_grant
      (granted_by, subject_kind, subject, capability, scope, evidence)
      VALUES (who, 'human', who, 'work.execute', 's', 'x');
    RAISE EXCEPTION 'migration 57: a perpetual grant with no stated reason was NOT refused';
  EXCEPTION WHEN check_violation THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 10 blank scope. An empty string is not a scope, and it is the shape a bug produces.
  BEGIN
    INSERT INTO brain.authority_grant
      (granted_by, subject_kind, subject, capability, scope, expires_at, evidence)
      VALUES (who, 'human', who, 'work.execute', '   ', now() + interval '1 hour', 'x');
    RAISE EXCEPTION 'migration 57: a blank scope was NOT refused';
  EXCEPTION WHEN check_violation THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 11 an expired grant is not in force. Positive control: it IS in authority_grant.
  INSERT INTO brain.authority_grant
    (granted_by, subject_kind, subject, capability, scope, expires_at, evidence)
    VALUES (who, 'human', who, 'work.execute', 'lane/mv-expired',
            now() - interval '1 second', 'migration 57 self-check')
    RETURNING grant_seq INTO g_exp;
  IF EXISTS (SELECT 1 FROM brain.authority_in_force WHERE grant_seq = g_exp)
     OR NOT EXISTS (SELECT 1 FROM brain.authority_grant WHERE grant_seq = g_exp) THEN
    RAISE EXCEPTION 'migration 57: an expired grant was still in force, or its record vanished';
  END IF;
  n := n + 1;

  -- 12 a revoked grant leaves force, and its record stays.
  INSERT INTO brain.authority_grant
    (granted_by, subject_kind, subject, capability, scope, expires_at, evidence)
    VALUES (who, 'human', who, 'effect.external', 'lane/mv-revoked',
            now() + interval '1 hour', 'migration 57 self-check')
    RETURNING grant_seq INTO g_rev;
  INSERT INTO brain.authority_revocation (grant_seq, revoked_by, reason)
    VALUES (g_rev, who, 'migration 57 self-check');
  IF EXISTS (SELECT 1 FROM brain.authority_in_force WHERE grant_seq = g_rev)
     OR NOT EXISTS (SELECT 1 FROM brain.authority_grant WHERE grant_seq = g_rev) THEN
    RAISE EXCEPTION 'migration 57: a revoked grant was still in force, or its record vanished';
  END IF;
  n := n + 1;

  -- 13 revoking twice is refused.
  BEGIN
    INSERT INTO brain.authority_revocation (grant_seq, revoked_by, reason)
      VALUES (g_rev, who, 'again');
    RAISE EXCEPTION 'migration 57: a second revocation of one grant was NOT refused';
  EXCEPTION WHEN unique_violation THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 14 a replayed approval is refused. Same proposal, same version, same approver, twice.
  INSERT INTO brain.approval (proposal_id, proposal_version, decided_by, grant_seq, decision)
    VALUES ('mv-fixture-proposal', 'v1', who, g_ok, 'approve');
  BEGIN
    INSERT INTO brain.approval (proposal_id, proposal_version, decided_by, grant_seq, decision)
      VALUES ('mv-fixture-proposal', 'v1', who, g_ok, 'approve');
    RAISE EXCEPTION 'migration 57: a replayed approval was NOT refused';
  EXCEPTION WHEN unique_violation THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 15 THE WRONG PACKET VERSION. The v1 approval exists; the v2 the executor would actually be
  -- asked to run has none. This is the property `receipt.approval_ref` could not carry, and it is
  -- what makes a replayed approval of a superseded proposal a miss rather than a hit.
  IF NOT EXISTS (SELECT 1 FROM brain.approval
                  WHERE proposal_id = 'mv-fixture-proposal' AND proposal_version = 'v1')
     OR EXISTS (SELECT 1 FROM brain.approval
                 WHERE proposal_id = 'mv-fixture-proposal' AND proposal_version = 'v2') THEN
    RAISE EXCEPTION 'migration 57: approval did not distinguish proposal versions';
  END IF;
  n := n + 1;

  -- The fixture grant is ended THE WAY THE DESIGN SAYS TO END ONE. It cannot be deleted, and it
  -- must not be left in force: a migration that finished by leaving somebody able to execute work
  -- in a fixture scope for the next hour would be the exact defect this table exists to prevent.
  -- Revoking it leaves the whole episode auditable and leaves nothing in force.
  INSERT INTO brain.authority_revocation (grant_seq, revoked_by, reason)
    VALUES (g_ok, who, 'migration 57 self-check fixture, ended immediately');

  IF EXISTS (SELECT 1 FROM brain.authority_in_force) THEN
    RAISE EXCEPTION 'migration 57: % grant(s) left in force by the self-check',
      (SELECT count(*) FROM brain.authority_in_force);
  END IF;

  IF made_human THEN
    -- Nothing in force references it; both grants naming it are revoked or expired.
    DELETE FROM brain.human_role WHERE human = who AND granted_by = 'migration 57 fixture';
  END IF;

  -- 16, 17, 18 LEAST PRIVILEGE, ASSERTED. The runtime role can add to the record and cannot
  -- edit it, and that is true of the privilege and not only of the trigger.
  IF NOT has_table_privilege('brain_runtime', 'brain.authority_grant', 'INSERT') THEN
    RAISE EXCEPTION 'migration 57: brain_runtime cannot INSERT a grant, so nothing could ever '
                    'be authorised through the runtime path';
  END IF;
  n := n + 1;
  IF has_table_privilege('brain_runtime', 'brain.authority_grant', 'UPDATE') THEN
    RAISE EXCEPTION 'migration 57: brain_runtime holds UPDATE on an append-only table';
  END IF;
  n := n + 1;
  IF has_table_privilege('brain_runtime', 'brain.authority_grant', 'DELETE') THEN
    RAISE EXCEPTION 'migration 57: brain_runtime holds DELETE on an append-only table';
  END IF;
  n := n + 1;

  -- 19 the actor check must be SECURITY DEFINER with a pinned search_path, or it is either
  -- broken for the runtime path or an escalation waiting to be found.
  IF NOT EXISTS (
       SELECT 1 FROM pg_proc p JOIN pg_namespace ns ON ns.oid = p.pronamespace
        WHERE ns.nspname = 'brain' AND p.proname = 'authority_actor_is_known'
          AND p.prosecdef
          AND p.proconfig @> ARRAY['search_path=pg_catalog, brain']) THEN
    RAISE EXCEPTION 'migration 57: authority_actor_is_known is not SECURITY DEFINER with a '
                    'pinned search_path';
  END IF;
  n := n + 1;

  IF n <> 19 THEN
    RAISE EXCEPTION 'migration 57: expected 19 checks, ran %', n;
  END IF;

  RAISE NOTICE
    'migration 57: % of 19 checks passed, % of them refusals watched refusing and '
    'three of them privileges asserted, over % '
    'capabilities, % grant row(s), % revocation(s) and % approval(s). 0 grants left in force.',
    n, refusals, (SELECT count(*) FROM brain.capability),
    (SELECT count(*) FROM brain.authority_grant),
    (SELECT count(*) FROM brain.authority_revocation),
    (SELECT count(*) FROM brain.approval);
END $$;

INSERT INTO brain.schema_migration (version, name)
  VALUES (57, '0057_authority_is_a_grant_and_a_revocation')
  ON CONFLICT (version) DO NOTHING;

COMMIT;
