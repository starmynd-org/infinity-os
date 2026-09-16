-- migration 21: the meter can say that it does not add up
--
-- Task 0251 (budget lane), child of 0166. Additive, re-runnable, and one enum value wide. It adds
-- nothing to any table and changes no existing row.
--
-- ================================================================= what this is for
--
-- 0251 taught `RunGuard` to derive a live run's spend from its own token stream and charge it
-- against the ceiling while the run is still going (`budget/price_card.py`, `budget/enforcer.py`).
-- The operator approved that on one condition, in their own words:
--
--     "if the delta is not zero, that is a finding with its own incident row, not a log line"
--
-- The delta is: what the price card said the run cost, minus what the engine's own billing says
-- it cost, at end of run. `budget selfcheck` computes it, and this migration is the word the
-- incident table needs in order to hold the answer.
--
-- ================================================================= why a new kind, not an old one
--
-- `brain.budget_incident_kind` carries six values and every one of them is about a CEILING:
-- warn, hard_stop, soft_breach, manual_stop, blocked_dispatch, resumed. A meter mismatch is not a
-- ceiling event. No limit was crossed, nothing was stopped, and the spend may be entirely within
-- budget -- what is wrong is that two independent measurements of the same money disagree.
--
-- Filing it under `warn` was the alternative and it is the trap this lane keeps refusing. Migration
-- 13 exists because a spend stop was filed as a generic `note` and became unreadable; the enum
-- comments in 0003 say the same thing from the other side ("a typo in a text column becomes a
-- branch that silently never fires"). An operator filtering `budget incidents --kind warn` is
-- asking "what got close to a ceiling", and an answer that mixes in "the price card may be stale"
-- makes both questions harder to ask. One word, one meaning.
--
-- ================================================================= what it does NOT do
--
-- `meter_mismatch` stops nothing. It is filed by `budget note`, which cannot write a stopping
-- action, and the CHECK below holds that structurally rather than by convention: a row of this
-- kind may only claim `none` or `warned`. A card that has drifted is a measurement problem, and
-- killing live runs over a measurement problem would be the cure being worse than the disease.
-- What the row is for is a human deciding whether to refit the card or fix the stream.
--
-- `cause` stays 'budget' and is still CHECKed to it. A meter mismatch is the operator's money
-- being mis-measured; it is not a subscription refusal, and the guarantee that this table cannot
-- physically hold a refusal is untouched.
--
-- ================================================================= applying it
--
-- ALTER TYPE ... ADD VALUE inside a transaction block is allowed from PostgreSQL 12 on (this
-- store is 16.10), with the one restriction that the new value cannot be USED in the same
-- transaction. Nothing here uses it, so the BEGIN/COMMIT shape every other migration in this tree
-- carries is kept rather than special-cased.
--
-- Version 21 is claimed rather than 19 or 20 because files recording those two exist in the tree
-- unapplied (migrations/0019_git_ref_repo_identity.sql, 0020_human_actor_identity.sql). Filenames
-- are not versions here and gaps are normal: read brain.schema_migration, never `ls`.

\set ON_ERROR_STOP on

BEGIN;

SET search_path TO brain, public;

DO $$
DECLARE existing text;
BEGIN
  SELECT name INTO existing FROM brain.schema_migration WHERE version = 21;
  IF existing IS NOT NULL AND existing <> '0021_meter_mismatch_kind' THEN
    RAISE EXCEPTION 'schema version 21 is already applied as %, not 0021_meter_mismatch_kind. '
                    'Renumber this migration rather than applying it over another lane''s.', existing;
  END IF;
END $$;

-- The value itself. IF NOT EXISTS makes the file re-runnable, which is the property the scratch
-- database builder and every rerun of the suite depend on.
ALTER TYPE brain.budget_incident_kind ADD VALUE IF NOT EXISTS 'meter_mismatch';

-- A meter mismatch that claims it stopped something is a record that lies, exactly as migration 3
-- says of a warn that claims a run was stopped. Added as NOT VALID and then validated: the table
-- holds no rows of this kind yet (the value did not exist a moment ago), so validation is a
-- formality, but doing it in two steps keeps the pattern honest if this is ever applied to a
-- database that somehow does.
ALTER TABLE brain.budget_incident
  ADD CONSTRAINT budget_incident_meter_mismatch_never_stops
  CHECK (kind::text <> 'meter_mismatch' OR action_taken IN ('none', 'warned')) NOT VALID;

ALTER TABLE brain.budget_incident VALIDATE CONSTRAINT budget_incident_meter_mismatch_never_stops;

INSERT INTO brain.schema_migration (version, name)
VALUES (21, '0021_meter_mismatch_kind')
ON CONFLICT (version) DO NOTHING;

COMMIT;
