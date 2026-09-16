-- migration 68: a captured message that was lost gets a durable row, and only a named human closes it.
--
-- CAP14-REV-041, routed by the Admiral; ledger number 68 assigned by Terminal 04, next free 69.
-- Written by Terminal 03 (CAP04) on 2026-09-07.
--
-- ---------------------------------------------------------------- WHY A TABLE AND NOT A PORT MINOR
--
-- Terminal 04's reasoning, checked here rather than accepted. THE RUNTIME HAS NO DEAD LETTER
-- ANYWHERE ON THE POSTGRES SIDE, so a port minor would have been a view over an empty set that
-- could never become non-empty. Measured on a scratch database built from this checkout at ledger
-- 56: no relation named `%dead%`, none named `%capture%` beyond `voice_capture`, and no `ingest`
-- schema at all. The three schemas the ledger builds are `brain` and `public`, so these tables go
-- in `brain` with everything else rather than opening a second home for four columns.
--
-- The tempting reuse was `brain.effect_reconciliation`: the same three-part shape with the WRONG
-- SUBJECT. It reconciles an outbound effect and its `attempt_seq` is NOT NULL against
-- `effect_attempt`, so reusing it would manufacture attempts for messages that were never
-- attempted. ONE HONESTY NOTE: that table is at a ledger version this checkout does not carry
-- (max here is 56), so its shape is reported from Terminal 04 and is the one claim in this file
-- that was NOT verified against a database by its author.
--
-- ---------------------------------------------------------------- THE THREE PROPERTIES THIS EARNS
--
-- Terminal 04 named them and reviews against them. Each is enforced twice where it can be, because
-- a property held only by convention is a property held until somebody is in a hurry.
--
-- 1. APPEND-ONLY, BY TRIGGER AND BY GRANT. No UPDATE and no DELETE, refused by a trigger that
--    every role hits including the owner, and not granted to the roles that write. A dead letter
--    is a statement about what happened; editing one is editing the past.
--
-- 2. "STILL UNKNOWN" IS A FINDING, NOT CLOSURE. `still-unknown` is a real resolution a human can
--    record after looking, and it does NOT close the loss: the unreconciled view still counts it.
--    READINESS CANNOT BE REGAINED BY FORGETTING, and it cannot be regained by writing down that
--    you looked either. This is the resolution the vocabulary was missing: without it, a human who
--    genuinely does not know is pushed toward `accepted-loss`, which does close it.
--
-- 3. THE VIEW PRINTS A DENOMINATOR. `capture_dead_letter_status` reports journalled, reconciled,
--    unreconciled and open-findings per source, so ZERO UNRECONCILED BECAUSE NOTHING WAS EVER
--    JOURNALLED is distinguishable from zero because everything was closed. A gate that reads a
--    bare zero cannot tell "nothing was lost" from "nothing was ever looked at", and those two
--    have opposite meanings for unattended readiness.
--
-- ---------------------------------------------------------------- WHAT THIS FILE DOES NOT DO
--
-- It does not apply itself anywhere but a scratch database, and it was proved on one. It does not
-- move STATE-API-PORT v4. It does not add a reconcile verb to the sweep's `DeadLetterLog`: the
-- interface an unattended runner holds must not carry the power to clear its own losses, which
-- Terminal 08 ruled on after checking the separation was structural rather than documentary.

BEGIN;

-- ---------------------------------------------------------------- the loss

CREATE TABLE IF NOT EXISTS brain.capture_dead_letter (
    dead_letter_id text PRIMARY KEY,
    -- The source this was lost from. Nullable because a payload can be refused before anything
    -- has established which source it belonged to, and a row that cannot say is better than no row.
    source_key     text,
    source_ref     text,
    reason         text NOT NULL,
    detail         text NOT NULL,
    received_at    timestamptz NOT NULL,
    raw_digest     text,
    attempts       integer NOT NULL DEFAULT 1 CHECK (attempts >= 1),
    journalled_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT capture_dead_letter_reason_not_blank CHECK (btrim(reason) <> ''),
    CONSTRAINT capture_dead_letter_detail_not_blank CHECK (btrim(detail) <> '')
);

CREATE INDEX IF NOT EXISTS capture_dead_letter_source
    ON brain.capture_dead_letter (source_key, received_at);

COMMENT ON TABLE brain.capture_dead_letter IS
    'One row per eligible item that left the retry ledger without being captured. Append-only.';

-- ---------------------------------------------------------------- the way back

-- THE FOUR RESOLUTIONS, AND THE ONE THAT DOES NOT CLOSE ANYTHING.
--
--   recaptured     the item arrived by another route; the loss no longer stands
--   accepted-loss  a human decided it will never arrive. The honest ending, and it requires a note
--   not-an-item    there was never an item here: a malformed probe, a provider echo
--   still-unknown  a human looked and does not know yet. RECORDED, NOT CLOSED
--
-- There is deliberately no "ignore": an item being ignored is an unreconciled loss and must keep
-- reading as one. `still-unknown` is what an honest reader reaches for instead.
-- MANY FINDINGS PER LOSS, APPEND-ONLY, NEWEST DOES NOT ERASE OLDEST. The first version of this
-- table made `dead_letter_id` the primary key, which welded the door shut: a human who recorded
-- `still-unknown` could never record `recaptured` when the item actually arrived, because the
-- second row hit the primary key and the append-only trigger correctly refused the UPDATE. An
-- honest answer permanently blocked readiness, which is the exact pressure toward `accepted-loss`
-- this file exists to remove. Terminal 04 drove that sequence and found it; the shape here is its
-- own `effect_reconciliation`, which had the cardinality right all along.
CREATE TABLE IF NOT EXISTS brain.capture_dead_letter_reconciliation (
    reconciliation_seq bigserial PRIMARY KEY,
    dead_letter_id text NOT NULL
        REFERENCES brain.capture_dead_letter (dead_letter_id),
    resolution     text NOT NULL
        CHECK (resolution IN ('recaptured', 'accepted-loss', 'not-an-item', 'still-unknown')),
    resolved_by    text NOT NULL,
    resolved_at    timestamptz NOT NULL,
    note           text NOT NULL DEFAULT '',
    recorded_at    timestamptz NOT NULL DEFAULT now(),
    -- Accepting a loss is the only place a human decides an item will never arrive. The other
    -- three describe themselves; this one has to say why, in the row, where it survives.
    CONSTRAINT capture_dead_letter_accepted_loss_needs_a_note
        CHECK (resolution <> 'accepted-loss' OR btrim(note) <> ''),
    -- The resolver is checked against the store's own roster by a trigger below, NOT by a list of
    -- names here. The first version was a denylist of seven literal strings, and Terminal 04 drove
    -- the obvious hole: `agent` refused, `t04-agent` accepted, closing a real loss on a store whose
    -- roster it was not on. A denylist answers "is this one of the names I thought of", and the
    -- question is "is this a human this store knows".
    CONSTRAINT capture_dead_letter_resolved_by_not_blank CHECK (btrim(resolved_by) <> '')
);

CREATE INDEX IF NOT EXISTS capture_dead_letter_reconciliation_letter
    ON brain.capture_dead_letter_reconciliation (dead_letter_id, reconciliation_seq DESC);

COMMENT ON TABLE brain.capture_dead_letter_reconciliation IS
    'One row per loss a named human has dealt with. still-unknown is a finding, not closure.';

-- ---------------------------------------------------------------- append-only, by trigger

CREATE OR REPLACE FUNCTION brain.capture_dead_letter_is_append_only()
RETURNS trigger LANGUAGE plpgsql AS $fn$
BEGIN
    RAISE EXCEPTION
        'brain.% is append-only: % refused. A dead letter records what happened, and a '
        'reconciliation is a new row beside it, never an edit to it',
        TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'restrict_violation';
END $fn$;

DROP TRIGGER IF EXISTS capture_dead_letter_no_update ON brain.capture_dead_letter;
CREATE TRIGGER capture_dead_letter_no_update
    BEFORE UPDATE OR DELETE ON brain.capture_dead_letter
    FOR EACH ROW EXECUTE FUNCTION brain.capture_dead_letter_is_append_only();

DROP TRIGGER IF EXISTS capture_dead_letter_reconciliation_no_update
    ON brain.capture_dead_letter_reconciliation;
CREATE TRIGGER capture_dead_letter_reconciliation_no_update
    BEFORE UPDATE OR DELETE ON brain.capture_dead_letter_reconciliation
    FOR EACH ROW EXECUTE FUNCTION brain.capture_dead_letter_is_append_only();

-- ---------------------------------------------------------------- the resolver is a known human

-- SECURITY DEFINER WITH A PINNED search_path, which is migration 61's shape and not decoration:
-- `brain_runtime` cannot read `brain.human_role` on its own, so a plain trigger raises "permission
-- denied for table human_role" the first time a runtime process records a reconciliation.
CREATE OR REPLACE FUNCTION brain.capture_dead_letter_resolver_is_known()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, brain AS $fn$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM brain.human_role WHERE human = NEW.resolved_by) THEN
        RAISE EXCEPTION
            'brain.capture_dead_letter_reconciliation refused: resolved_by % names no human this '
            'store knows. A loss closed by nobody is not closed, and an agent cannot close its own '
            'hole.', NEW.resolved_by
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END $fn$;

DROP TRIGGER IF EXISTS capture_dead_letter_reconciliation_resolver
    ON brain.capture_dead_letter_reconciliation;
CREATE TRIGGER capture_dead_letter_reconciliation_resolver
    BEFORE INSERT ON brain.capture_dead_letter_reconciliation
    FOR EACH ROW EXECUTE FUNCTION brain.capture_dead_letter_resolver_is_known();

-- ---------------------------------------------------------------- the denominator

-- THE LATEST FINDING PER LOSS DECIDES, and every earlier one is still readable underneath. A
-- `still-unknown` followed by a `recaptured` reads as closed; a `recaptured` followed by a later
-- `still-unknown` reads as open again, which is the honest direction for new information to move.
CREATE OR REPLACE VIEW brain.capture_dead_letter_status AS
WITH latest AS (
    SELECT DISTINCT ON (dead_letter_id)
           dead_letter_id, resolution, resolved_by, resolved_at
      FROM brain.capture_dead_letter_reconciliation
     ORDER BY dead_letter_id, reconciliation_seq DESC
)
SELECT d.source_key,
       count(*)                                                   AS journalled,
       count(l.dead_letter_id) FILTER (
           WHERE l.resolution <> 'still-unknown')                 AS closed,
       count(l.dead_letter_id) FILTER (
           WHERE l.resolution = 'still-unknown')                  AS open_findings,
       count(*) - count(l.dead_letter_id) FILTER (
           WHERE l.resolution <> 'still-unknown')                 AS unreconciled,
       max(d.received_at)                                         AS newest_loss
  FROM brain.capture_dead_letter d
  LEFT JOIN latest l USING (dead_letter_id)
 GROUP BY d.source_key;

COMMENT ON VIEW brain.capture_dead_letter_status IS
    'Per source: how many were journalled, closed, still-unknown and unreconciled. A caller that '
    'reads unreconciled without journalled cannot tell "nothing was lost" from "nothing was '
    'ever looked at".';

-- ---------------------------------------------------------------- append-only, by grant

-- The second half of property 1. The trigger stops an UPDATE that is attempted; the grant stops it
-- being attempted at all, and the two fail differently, which is useful when you are reading logs.
GRANT SELECT, INSERT ON brain.capture_dead_letter                TO brain_runtime;
GRANT SELECT, INSERT ON brain.capture_dead_letter_reconciliation TO brain_runtime;
GRANT SELECT ON brain.capture_dead_letter                        TO brain_subscriber;
GRANT SELECT ON brain.capture_dead_letter_reconciliation         TO brain_subscriber;
GRANT SELECT ON brain.capture_dead_letter_status                 TO brain_runtime, brain_subscriber;

-- THE SEQUENCE, WITHOUT WHICH THE GRANT ABOVE IS A GRANT TO DO NOTHING. Terminal 04 drove it:
-- `brain_runtime` held INSERT on the table and still could not insert a row, because
-- `reconciliation_seq bigserial` needs USAGE on its sequence -- "permission denied for sequence
-- capture_dead_letter_reconciliation_reconciliation_seq_seq". The sweep could journal losses and
-- NOTHING COULD EVER RECORD ONE DEALT WITH, which is the wall this file exists to remove, rebuilt
-- one layer down.
--
-- IT WAS INVISIBLE TO MY OWN TEN CHECKS BECAUSE THEY RUN AS THE MIGRATION OWNER, a superuser, and
-- a superuser bypasses grants. Every refusal I watched was watched from the one role that can
-- never be refused. That is the same defect class this lane has been naming all day, in the place
-- I would have said was safest: a check that cannot fail because it is asked in a context the real
-- caller never occupies. The check below therefore does `SET ROLE brain_runtime` and drives the
-- real path as the role the runtime actually writes as.
GRANT USAGE ON SEQUENCE brain.capture_dead_letter_reconciliation_reconciliation_seq_seq
    TO brain_runtime;

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'brain_operator') THEN
    EXECUTE 'GRANT SELECT, INSERT ON brain.capture_dead_letter TO brain_operator';
    EXECUTE 'GRANT SELECT, INSERT ON brain.capture_dead_letter_reconciliation TO brain_operator';
    EXECUTE 'GRANT SELECT ON brain.capture_dead_letter_status TO brain_operator';
    EXECUTE 'GRANT USAGE ON SEQUENCE '
            'brain.capture_dead_letter_reconciliation_reconciliation_seq_seq TO brain_operator';
  END IF;
END $$;

-- ---------------------------------------------------------------- the checks, with the refusals watched

DO $$
DECLARE
    n int := 0;
    v_journalled bigint;
    v_unreconciled bigint;
    v_open bigint;
    v_rows bigint;
    v_human text;
    v_human_was_built boolean := false;
BEGIN
    -- THE ROSTER IS EMPTY DURING A FRESH BUILD, and this file walked straight into a trap its own
    -- author had written down. `state_port.py` records it as one of Terminal 04's three:
    -- "scratch-db.sh maps the operator login AFTER every migration runs, so on a fresh create the
    -- human roster is empty. A self-check assuming a human exists passes on `migrate` and fails on
    -- `create`: it works on the store in front of you and breaks on the one that ships. Build a
    -- fixture row when the roster is empty."
    --
    -- Which is exactly what happened: the checks below passed against a database that already had
    -- the operator mapped, and failed on the fresh build. The remedy is the one already recorded.
    SELECT human INTO v_human FROM brain.human_role ORDER BY human LIMIT 1;
    IF v_human IS NULL THEN
        INSERT INTO brain.human_role (role_name, human, granted_by)
             VALUES ('brain_operator', 'migration68-probe', 'migration');
        v_human := 'migration68-probe';
        v_human_was_built := true;
    END IF;

    -- An empty store is the state a fresh build is in, so the denominator is stated before any
    -- verdict below is read. A check over an empty set is not a pass.
    SELECT count(*) INTO v_rows FROM brain.capture_dead_letter;
    IF v_rows <> 0 THEN
        RAISE EXCEPTION 'migration 68 expected an empty dead-letter table, found % row(s)', v_rows;
    END IF;
    n := n + 1;

    INSERT INTO brain.capture_dead_letter
        (dead_letter_id, source_key, source_ref, reason, detail, received_at)
    VALUES ('dl_migration68_probe', 'sk_probe', 'email/probe', 'payload-refused',
            'a probe row, never committed', now());
    n := n + 1;

    -- THE TRIGGER, WATCHED REFUSING, IN BOTH DIRECTIONS. A trigger nobody has seen fire is a
    -- trigger nobody has tested.
    BEGIN
        UPDATE brain.capture_dead_letter SET detail = 'edited'
         WHERE dead_letter_id = 'dl_migration68_probe';
        RAISE EXCEPTION 'the append-only trigger allowed an UPDATE';
    EXCEPTION WHEN restrict_violation THEN
        n := n + 1;
    END;

    BEGIN
        DELETE FROM brain.capture_dead_letter WHERE dead_letter_id = 'dl_migration68_probe';
        RAISE EXCEPTION 'the append-only trigger allowed a DELETE';
    EXCEPTION WHEN restrict_violation THEN
        n := n + 1;
    END;

    -- PROPERTY 2, DRIVEN. still-unknown is recorded and the loss stays open.
    INSERT INTO brain.capture_dead_letter_reconciliation
        (dead_letter_id, resolution, resolved_by, resolved_at, note)
    VALUES ('dl_migration68_probe', 'still-unknown', v_human, now(),
            'looked, cannot tell yet');
    SELECT journalled, unreconciled, open_findings
      INTO v_journalled, v_unreconciled, v_open
      FROM brain.capture_dead_letter_status WHERE source_key = 'sk_probe';
    IF v_unreconciled <> 1 OR v_open <> 1 OR v_journalled <> 1 THEN
        RAISE EXCEPTION 'still-unknown closed a loss: journalled=% unreconciled=% open=%',
            v_journalled, v_unreconciled, v_open;
    END IF;
    n := n + 1;

    -- THE DOOR OPENS AGAIN, which is the check that used to test the defect and approve it.
    --
    -- The first version of this file asserted that a SECOND resolution was refused, watched the
    -- primary key refuse it, and recorded PASS. That was the defect: an item recorded
    -- `still-unknown` and then genuinely recaptured could never be closed, so the honest answer
    -- blocked readiness for ever. The property is not "one resolution per loss"; it is "a
    -- resolution cannot be EDITED", and the append-only trigger already gives that. A later,
    -- APPENDED finding must be accepted.
    INSERT INTO brain.capture_dead_letter_reconciliation
        (dead_letter_id, resolution, resolved_by, resolved_at, note)
    VALUES ('dl_migration68_probe', 'recaptured', v_human, now(), 'arrived by the other route');
    SELECT unreconciled, open_findings INTO v_unreconciled, v_open
      FROM brain.capture_dead_letter_status WHERE source_key = 'sk_probe';
    IF v_unreconciled <> 0 OR v_open <> 0 THEN
        RAISE EXCEPTION 'a still-unknown loss that was later recaptured did not close: '
                        'unreconciled=% open=%', v_unreconciled, v_open;
    END IF;
    n := n + 1;

    -- AND THE EDIT IS STILL REFUSED, which is the property that check was reaching for.
    BEGIN
        UPDATE brain.capture_dead_letter_reconciliation SET resolution = 'accepted-loss'
         WHERE dead_letter_id = 'dl_migration68_probe';
        RAISE EXCEPTION 'a resolution was edited';
    EXCEPTION WHEN restrict_violation THEN
        n := n + 1;
    END;

    -- ACCEPTING A LOSS WITHOUT SAYING WHY, WATCHED REFUSING.
    INSERT INTO brain.capture_dead_letter
        (dead_letter_id, source_key, reason, detail, received_at)
    VALUES ('dl_migration68_probe2', 'sk_probe', 'retry-exhausted', 'five attempts', now());
    BEGIN
        INSERT INTO brain.capture_dead_letter_reconciliation
            (dead_letter_id, resolution, resolved_by, resolved_at)
        VALUES ('dl_migration68_probe2', 'accepted-loss', v_human, now());
        RAISE EXCEPTION 'accepted-loss was recorded with no note';
    EXCEPTION WHEN check_violation THEN
        n := n + 1;
    END;

    -- A RESOLVER THIS STORE DOES NOT KNOW, WATCHED REFUSING, AND THE NAME IS THE ONE THAT BEAT
    -- THE DENYLIST. `system` was refused by the old seven-string list; `t04-agent` was accepted by
    -- it and closed a real loss. The roster trigger asks the question the list could not.
    BEGIN
        INSERT INTO brain.capture_dead_letter_reconciliation
            (dead_letter_id, resolution, resolved_by, resolved_at)
        VALUES ('dl_migration68_probe2', 'not-an-item', 't04-agent', now());
        RAISE EXCEPTION 'a resolver this store does not know was accepted';
    EXCEPTION WHEN restrict_violation THEN
        n := n + 1;
    END;

    -- THE POSITIVE CONTROL. If the refusals above pass because EVERY insert is refused, the
    -- constraints protect nothing and prove less. A real closure must work.
    INSERT INTO brain.capture_dead_letter_reconciliation
        (dead_letter_id, resolution, resolved_by, resolved_at, note)
    VALUES ('dl_migration68_probe2', 'accepted-loss', v_human, now(),
            'the provider cannot re-serve this message');
    -- Both probe losses are now closed: the first by a LATER appended `recaptured` over its
    -- `still-unknown`, the second by this `accepted-loss`. Two different routes to closed, and the
    -- denominator agrees with both.
    SELECT unreconciled, closed INTO v_unreconciled, v_journalled
      FROM brain.capture_dead_letter_status WHERE source_key = 'sk_probe';
    IF v_unreconciled <> 0 OR v_journalled <> 2 THEN
        RAISE EXCEPTION 'closing both losses reported unreconciled=% closed=%',
            v_unreconciled, v_journalled;
    END IF;
    n := n + 1;

    -- PROPERTY 3. A source that never journalled anything has NO ROW in the view, which is a
    -- different answer from a row reading zero, and a caller can tell them apart.
    IF EXISTS (SELECT 1 FROM brain.capture_dead_letter_status WHERE source_key = 'sk_never') THEN
        RAISE EXCEPTION 'a source that journalled nothing appeared in the status view';
    END IF;
    n := n + 1;

    -- ------------------------------------------------------------ AS THE ROLE THAT ACTUALLY WRITES
    --
    -- Everything above ran as the migration owner, and a superuser bypasses grants, so every
    -- refusal so far was watched from the one role that can never be refused. Terminal 04 found
    -- what that hid: `brain_runtime` could not insert a reconciliation at all. This block drives
    -- the real path as the real role.
    SET LOCAL ROLE brain_runtime;

    INSERT INTO brain.capture_dead_letter
        (dead_letter_id, source_key, reason, detail, received_at)
    VALUES ('dl_migration68_runtime', 'sk_runtime', 'retry-exhausted', 'five attempts', now());
    INSERT INTO brain.capture_dead_letter_reconciliation
        (dead_letter_id, resolution, resolved_by, resolved_at, note)
    VALUES ('dl_migration68_runtime', 'still-unknown', v_human, now(), 'looked, cannot tell yet');
    n := n + 1;

    -- PROPERTY 2 UNDER THE RUNTIME ROLE. The roster trigger is SECURITY DEFINER with a pinned
    -- search_path precisely so it can read `brain.human_role` as a role that cannot: without that
    -- it dies on "permission denied for table human_role" rather than refusing the resolver, and
    -- an error is not a refusal. Terminal 04 could not reach this check because the sequence
    -- refusal fired first, so the property was unproven until this run.
    BEGIN
        INSERT INTO brain.capture_dead_letter_reconciliation
            (dead_letter_id, resolution, resolved_by, resolved_at)
        VALUES ('dl_migration68_runtime', 'not-an-item', 'claude-opus-5', now());
        RAISE EXCEPTION 'brain_runtime recorded a resolver this store does not know';
    EXCEPTION WHEN restrict_violation THEN
        n := n + 1;
    END;

    -- APPEND-ONLY UNDER THE RUNTIME ROLE, AND IT IS THE **GRANT** THAT REFUSES, NOT THE TRIGGER.
    --
    -- This check first asserted `restrict_violation` and failed with "permission denied for table
    -- capture_dead_letter", which is `insufficient_privilege`. The two halves of property 1 refuse
    -- DIFFERENT ROLES AND IN A DIFFERENT ORDER, and that is the point of having both: the owner
    -- cannot be grant-restricted so the trigger stops it, and the runtime never reaches the trigger
    -- because it holds no UPDATE at all. Asserting one error code for both roles was me treating a
    -- two-layer defence as one layer.
    BEGIN
        UPDATE brain.capture_dead_letter SET detail = 'edited'
         WHERE dead_letter_id = 'dl_migration68_runtime';
        RAISE EXCEPTION 'brain_runtime edited a dead letter';
    EXCEPTION WHEN insufficient_privilege THEN
        n := n + 1;
    END;

    -- The runtime can READ the denominator it is gated on. A gate whose input the gated role
    -- cannot select is a gate that always reads empty.
    SELECT unreconciled INTO v_unreconciled
      FROM brain.capture_dead_letter_status WHERE source_key = 'sk_runtime';
    IF v_unreconciled <> 1 THEN
        RAISE EXCEPTION 'brain_runtime read unreconciled=% for its own open loss', v_unreconciled;
    END IF;
    n := n + 1;

    RESET ROLE;

    -- THE NOTICE REPORTS AFTER THE LAST CHECK, NOT BEFORE IT. It sat above the runtime block for
    -- one build and printed "11 of 15" on a run where all fifteen passed: a progress number
    -- presented as a result. Small, and the same shape as everything else caught here, so it moved
    -- rather than being explained.
    RAISE NOTICE 'migration 68: % of 15 checks passed. The append-only trigger was watched '
                 'refusing an UPDATE and a DELETE on the loss and an EDIT of a resolution; '
                 'accepted-loss-without-a-note and a resolver this store does not know were both '
                 'watched refusing; and a positive control closed a real loss. still-unknown left '
                 'its loss OPEN, and a LATER APPENDED recaptured then closed it, which is the '
                 'pair that proves the door opens again. THE LAST FOUR RAN AS brain_runtime, the '
                 'role the runtime actually writes as and the one every earlier check was not '
                 'asking, because a superuser bypasses grants; there the GRANT refuses the edit '
                 'before the trigger is reached. Probe rows are TRUNCATED below; tables ship EMPTY.',
                 n;
    IF n <> 15 THEN RAISE EXCEPTION 'migration 68: expected 15 checks, ran %', n; END IF;

    -- The fixture human leaves with the fixture rows. A roster this migration grew by one would
    -- be a human nobody hired, sitting in the store as though somebody had.
    IF v_human_was_built THEN
        DELETE FROM brain.human_role WHERE human = 'migration68-probe';
    END IF;
END $$;

-- THE PROBE ROWS MUST NOT SHIP. They cannot be deleted, by this file's own design, so they are
-- removed the only way an append-only table allows: the whole probe runs inside a savepoint-free
-- transaction whose rows are discarded by truncating before commit. TRUNCATE is a DDL-level
-- operation and does not fire the row triggers, which is exactly why it is available here and
-- exactly why the grant half of property 1 matters: `brain_runtime` has INSERT and SELECT only,
-- so no writer role can reach this.
TRUNCATE brain.capture_dead_letter_reconciliation, brain.capture_dead_letter;

INSERT INTO brain.schema_migration (version, name)
  VALUES (68, '0068_a_lost_message_can_be_closed_by_a_named_human')
  ON CONFLICT (version) DO NOTHING;

COMMIT;
