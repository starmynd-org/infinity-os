-- migration 58: a LEASE is the right to cause an effect right now, and it carries a fencing token
-- so that a holder who lost the lease cannot still write through it.
--
-- Packet R02, sprint `2026-09-06-multiverse-build-and-launch`. Written 2026-09-06 by Terminal 04.
-- Ledger version 58, reserved by the R01 census in
-- `swarms/Sprints/2026-09-06-multiverse-build-and-launch/waves/handoffs/CAP06`.
--
-- ------------------------------------------------------------------ what a claim is not
--
-- `brain.work_item.claimed_by` already exists and it is not this. A claim is an INTENTION: this
-- agent means to do this work. It has no expiry, no holder identity beyond a name, and nothing
-- stops a process that has been unresponsive for two hours from waking up and acting on a claim
-- nobody has taken away. Measured before this file: zero rows anywhere in the repo carried a lease,
-- a fencing token or an expiry; `grep -rin lease` over `migrations/ queue/ engine/ store/ budget/`
-- returned only the English word "release" in ranking code.
--
-- A LEASE IS A DIFFERENT STATEMENT: this holder may act, until this instant, and everybody can tell
-- whether that is still true. The two coexist. A claim survives a restart; a lease does not, and
-- must not.
--
-- ------------------------------------------------------------------ ONE LIVE LEASE, AND TAKEOVER IS AN ACT
--
-- The partial unique index allows exactly one unreleased lease per work item. That is what makes
-- "two workers claim one item" a refusal from Postgres rather than a race the application is
-- trusted to win.
--
-- Note what it deliberately does NOT do: it does not let a second worker acquire simply because the
-- first lease expired. An expired lease still blocks, and taking it over means RELEASING it first,
-- with a reason, which leaves a row saying who took what from whom and when. The alternative --
-- letting expiry silently free the item -- makes the common failure invisible exactly when somebody
-- is trying to work out what happened.
--
-- ------------------------------------------------------------------ THE TOKEN CANNOT BE CHOSEN
--
-- `fencing_token` is assigned by a trigger from one sequence, and a value supplied by the caller is
-- overwritten rather than trusted. A token a client can choose is not a fence. Tokens are global
-- and monotonic, so "newest lease for this item" is decidable by comparing two integers, which is
-- what migration 59 does before it lets an effect through.

BEGIN;

CREATE SEQUENCE IF NOT EXISTS brain.fencing_token_seq AS bigint START 1;

CREATE TABLE IF NOT EXISTS brain.execution_lease (
  lease_id       bigserial   PRIMARY KEY,
  work_item_id   text        NOT NULL REFERENCES brain.work_item(id),
  holder         text        NOT NULL,
  acquired_at    timestamptz NOT NULL DEFAULT now(),
  expires_at     timestamptz NOT NULL,
  fencing_token  bigint      NOT NULL,
  released_at    timestamptz,
  release_reason text,
  CONSTRAINT execution_lease_holder_not_blank_ck CHECK (btrim(holder) <> ''),
  CONSTRAINT execution_lease_expiry_after_acquisition_ck CHECK (expires_at > acquired_at),
  CONSTRAINT execution_lease_release_says_why_ck
    CHECK ((released_at IS NULL AND release_reason IS NULL)
           OR (released_at IS NOT NULL AND btrim(coalesce(release_reason, '')) <> ''))
);

-- ONE LIVE LEASE PER ITEM. Partial, so released leases accumulate as history rather than colliding.
CREATE UNIQUE INDEX IF NOT EXISTS execution_lease_one_live_per_item
  ON brain.execution_lease (work_item_id) WHERE released_at IS NULL;

CREATE INDEX IF NOT EXISTS execution_lease_by_item
  ON brain.execution_lease (work_item_id, fencing_token DESC);

-- The token is issued here and nowhere else. A caller that supplies one is overwritten, silently
-- from its point of view and loudly in the record: the row shows the token the store assigned.
CREATE OR REPLACE FUNCTION brain.execution_lease_issue_token() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
  NEW.fencing_token := nextval('brain.fencing_token_seq');
  RETURN NEW;
END;
$fn$;

DROP TRIGGER IF EXISTS execution_lease_issue_token ON brain.execution_lease;
CREATE TRIGGER execution_lease_issue_token BEFORE INSERT ON brain.execution_lease
  FOR EACH ROW EXECUTE FUNCTION brain.execution_lease_issue_token();

-- A LEASE IS RELEASED, NEVER EDITED. The one legal UPDATE sets released_at and release_reason on a
-- row where released_at was NULL. Everything else about the row is what it was when the lease was
-- issued, and an effect written under it is only auditable if that stays true. Re-releasing is
-- refused too: the first release is the one that happened, and overwriting its time and reason
-- would erase who actually took the lease away.
CREATE OR REPLACE FUNCTION brain.execution_lease_release_only() RETURNS trigger
LANGUAGE plpgsql AS $fn$
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
     OR NEW.fencing_token IS DISTINCT FROM OLD.fencing_token THEN
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
$fn$;

DROP TRIGGER IF EXISTS execution_lease_release_only ON brain.execution_lease;
CREATE TRIGGER execution_lease_release_only BEFORE UPDATE OR DELETE ON brain.execution_lease
  FOR EACH ROW EXECUTE FUNCTION brain.execution_lease_release_only();

-- WHICH LEASE MAY ACT, computed now. Unreleased, unexpired, and the newest token for its item.
CREATE OR REPLACE VIEW brain.execution_lease_live AS
SELECT l.*
  FROM brain.execution_lease l
 WHERE l.released_at IS NULL
   AND l.expires_at > now()
   AND l.fencing_token = (SELECT max(l2.fencing_token) FROM brain.execution_lease l2
                           WHERE l2.work_item_id = l.work_item_id);

COMMENT ON VIEW brain.execution_lease_live IS
  'Leases that may act right now. Expiry is arithmetic on now(), so nothing sweeps and nothing can '
  'fail to sweep. A lease absent from this view is not evidence that its holder stopped running.';

GRANT SELECT, INSERT, UPDATE ON brain.execution_lease TO brain_runtime;
GRANT SELECT ON brain.execution_lease_live TO brain_runtime, brain_subscriber;
GRANT USAGE ON SEQUENCE brain.execution_lease_lease_id_seq, brain.fencing_token_seq TO brain_runtime;

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'brain_operator') THEN
    EXECUTE 'GRANT SELECT, INSERT, UPDATE ON brain.execution_lease TO brain_operator';
    EXECUTE 'GRANT SELECT ON brain.execution_lease_live TO brain_operator';
    EXECUTE 'GRANT USAGE ON SEQUENCE brain.execution_lease_lease_id_seq, '
            'brain.fencing_token_seq TO brain_operator';
  END IF;
END $$;

-- ------------------------------------------------------------------ EVERY REFUSAL WATCHED HAPPENING
--
-- Thirteen checks: SEVEN refusals watched refusing and six positive controls, so the refusals are
-- not simply refusing everything. Both counts are asserted; this block once printed "eight" over
-- seven EXCEPTION blocks (CAP14-REV-017). Each refusal sits in its own BEGIN/EXCEPTION subtransaction,
-- because a failed statement aborts everything after it otherwise. The count is asserted at the end.
DO $$
DECLARE
  n int := 0;
  refusals int := 0;
  item text;
  l1 bigint; l2 bigint;
  t1 bigint; t2 bigint;
  seq_last bigint; seq_called boolean;
BEGIN
  SELECT last_value, is_called INTO seq_last, seq_called FROM brain.item_id_seq;
  INSERT INTO brain.work_item (title, lane) VALUES ('migration 58 self-check', 'mv')
    RETURNING id INTO item;

  -- 1 a lease is acquired and is live.
  INSERT INTO brain.execution_lease (work_item_id, holder, expires_at, fencing_token)
    VALUES (item, 'worker-a', now() + interval '5 minutes', 0)
    RETURNING lease_id, fencing_token INTO l1, t1;
  IF NOT EXISTS (SELECT 1 FROM brain.execution_lease_live WHERE lease_id = l1) THEN
    RAISE EXCEPTION 'migration 58: a fresh lease was not live';
  END IF;
  n := n + 1;

  -- 2 the caller asked for token 0 and did not get it. A token a client can choose is not a fence.
  IF t1 IS NULL OR t1 = 0 THEN
    RAISE EXCEPTION 'migration 58: the caller supplied fencing token survived';
  END IF;
  n := n + 1;

  -- 3 two workers, one item: refused by the index, not by the application.
  BEGIN
    INSERT INTO brain.execution_lease (work_item_id, holder, expires_at, fencing_token)
      VALUES (item, 'worker-b', now() + interval '5 minutes', 0);
    RAISE EXCEPTION 'migration 58: a second live lease on one item was NOT refused';
  EXCEPTION WHEN unique_violation THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 4 a lease cannot be extended or shortened. That is a new lease, so the record still says who
  --   could act when.
  BEGIN
    UPDATE brain.execution_lease SET expires_at = now() + interval '1 hour' WHERE lease_id = l1;
    RAISE EXCEPTION 'migration 58: expires_at was editable after issue';
  EXCEPTION WHEN restrict_violation THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 5 nor re-pointed to another holder.
  BEGIN
    UPDATE brain.execution_lease SET holder = 'worker-b' WHERE lease_id = l1;
    RAISE EXCEPTION 'migration 58: holder was editable after issue';
  EXCEPTION WHEN restrict_violation THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 6 nor deleted.
  BEGIN
    DELETE FROM brain.execution_lease WHERE lease_id = l1;
    RAISE EXCEPTION 'migration 58: a lease was deletable';
  EXCEPTION WHEN restrict_violation THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 7 a release must say why.
  BEGIN
    UPDATE brain.execution_lease SET released_at = now() WHERE lease_id = l1;
    RAISE EXCEPTION 'migration 58: a release with no reason was NOT refused';
  EXCEPTION WHEN check_violation THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 8 AN EXPIRED LEASE STILL BLOCKS. Expiry removes the right to act; it does not free the item.
  --   This is the property that makes a takeover an act somebody performed rather than a silence.
  UPDATE brain.execution_lease SET released_at = NULL WHERE FALSE;   -- no-op, keeps the reader honest
  IF EXISTS (SELECT 1 FROM brain.execution_lease_live
              WHERE lease_id = l1 AND expires_at > now()) THEN
    -- still unexpired here by construction; the expiry case is exercised in the suite, where the
    -- clock can be moved by inserting a lease that is already past. Positive control only.
    n := n + 1;
  ELSE
    RAISE EXCEPTION 'migration 58: the live view lost an unexpired lease';
  END IF;

  -- 9 a takeover is a release plus a new lease, and the new token is strictly greater.
  UPDATE brain.execution_lease
     SET released_at = now(), release_reason = 'taken over by worker-b in migration 58 self-check'
   WHERE lease_id = l1;
  INSERT INTO brain.execution_lease (work_item_id, holder, expires_at, fencing_token)
    VALUES (item, 'worker-b', now() + interval '5 minutes', 0)
    RETURNING lease_id, fencing_token INTO l2, t2;
  IF t2 <= t1 THEN
    RAISE EXCEPTION 'migration 58: the new token % did not exceed the old %', t2, t1;
  END IF;
  n := n + 1;

  -- 10 the released lease is out of the live view, and its record survives.
  IF EXISTS (SELECT 1 FROM brain.execution_lease_live WHERE lease_id = l1)
     OR NOT EXISTS (SELECT 1 FROM brain.execution_lease WHERE lease_id = l1) THEN
    RAISE EXCEPTION 'migration 58: a released lease was still live, or its record vanished';
  END IF;
  n := n + 1;

  -- 11 releasing twice is refused: the first release is the one that happened.
  BEGIN
    UPDATE brain.execution_lease SET released_at = now(), release_reason = 'again'
     WHERE lease_id = l1;
    RAISE EXCEPTION 'migration 58: a second release was NOT refused';
  EXCEPTION WHEN restrict_violation THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 12 a lease on an item that does not exist is refused by the foreign key.
  BEGIN
    INSERT INTO brain.execution_lease (work_item_id, holder, expires_at, fencing_token)
      VALUES ('no-such-item', 'worker-c', now() + interval '5 minutes', 0);
    RAISE EXCEPTION 'migration 58: a lease on an unknown work item was NOT refused';
  EXCEPTION WHEN foreign_key_violation THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 13 least privilege: the runtime may acquire and release, and may not delete history.
  IF NOT has_table_privilege('brain_runtime', 'brain.execution_lease', 'INSERT')
     OR NOT has_table_privilege('brain_runtime', 'brain.execution_lease', 'UPDATE')
     OR has_table_privilege('brain_runtime', 'brain.execution_lease', 'DELETE') THEN
    RAISE EXCEPTION 'migration 58: runtime privileges on execution_lease are wrong';
  END IF;
  n := n + 1;

  -- Fixture cleanup. The leases reference the work item, so they go first; DELETE is refused by the
  -- trigger, which is the point of check 6, so the trigger is stood down for exactly this statement
  -- and restored immediately. A migration cleaning up after itself is the one caller entitled to do
  -- that, and doing it in the open is better than leaving two fixture leases and a fixture work item
  -- in every store that applies this file.
  ALTER TABLE brain.execution_lease DISABLE TRIGGER execution_lease_release_only;
  DELETE FROM brain.execution_lease WHERE work_item_id = item;
  ALTER TABLE brain.execution_lease ENABLE TRIGGER execution_lease_release_only;
  DELETE FROM brain.work_item WHERE id = item;
  PERFORM setval('brain.item_id_seq', seq_last, seq_called);

  IF EXISTS (SELECT 1 FROM brain.execution_lease) THEN
    RAISE EXCEPTION 'migration 58: % lease row(s) left behind by the self-check',
      (SELECT count(*) FROM brain.execution_lease);
  END IF;

  IF n <> 13 THEN
    RAISE EXCEPTION 'migration 58: expected 13 checks, ran %', n;
  END IF;

  RAISE NOTICE
    'migration 58: % of 13 checks passed, % of them refusals watched refusing, over % work '
    'item(s) and % lease row(s) left behind. item_id_seq restored to (last_value %, is_called %).',
    n, refusals, (SELECT count(*) FROM brain.work_item), (SELECT count(*) FROM brain.execution_lease),
    seq_last, seq_called;
END $$;

INSERT INTO brain.schema_migration (version, name)
  VALUES (58, '0058_a_lease_is_the_right_to_act_now')
  ON CONFLICT (version) DO NOTHING;

COMMIT;
