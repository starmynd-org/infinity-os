-- migration 4: `touch` gains component_type and orient_role, per WAGER-2c
--
-- WHY 4 AND NOT 3. The brief for this change said "0003, it does not collide with 0001_initial
-- and 0002_roles", and `ls migrations/` agrees, because only those two files live here. The
-- live ledger disagrees: `brain.schema_migration` already holds version 3, '0003_budget',
-- applied 2026-08-16T12:10:54Z, and that lane's file is at `budget/schema/0003_budget.sql`
-- rather than in this directory. Version 3 is taken. This is version 4.
--
-- Read the live table, never this directory, when picking the next number. Every migration here
-- ends with `INSERT ... ON CONFLICT (version) DO NOTHING`, so a second file claiming a taken
-- version applies its DDL and then SILENTLY skips its ledger row: the store ends up one
-- migration ahead of what `schema_migration` reports, and nothing raises.
--
-- WHAT THIS IMPLEMENTS. `D00` specifies `touch` as `(subject_type, subject_id, entity_id,
-- role)`. The brain's locked rule `_system/wager-ledger-rules.md` line 78, WAGER-2c, specifies
-- six fields and TWO distinct role columns:
--
--   Rule WAGER-2c, `component_touch` (git edge): `touch_id`, `disposition_id` or `wager_id`,
--   `component_type`, `component_id`, `orient_role` (tradition, previous-experience,
--   analysis-synthesis, substrate), `role` (load-bearing, incidental).
--
-- `D00`'s 4-tuple keeps `role` and drops `orient_role`. That drop is not cosmetic. `D00`'s own
-- justification for the table is that "the wager ledger scores which components shaped an
-- action", and `orient_role` -- which part of the orientation the component came from -- is the
-- axis that scoring runs on. Without it the table is a plain many-to-many that looks complete
-- and cannot answer the question it was added for. D2 found this (D-CROSSTALK slot 7,
-- 2026-08-16T12:10Z); the commander ruled at 12:15Z that D2 is right and dispatched this as
-- task 0101.
--
-- ===================================================================================
-- DIRECTION OF TRUTH: GIT IS THE SOURCE. THIS TABLE IS A PROJECTION.
-- ===================================================================================
--
-- WAGER-7: "git holds the contracts, the per-item receipts, the immutable pre-registered
-- wagers, THE COMPONENT TOUCH-EDGES, and the promoted verdict diagnoses."
--
-- So component touch-edges are a git artifact by the brain's own rule. D2's `touch add` writes
-- them into git inside the receipt, as a fenced machine-readable block, one file per promotion.
-- `brain.touch` is LOADED FROM THAT. It is not the source, it must never become the source, and
-- nothing may be added to it that only exists here.
--
-- This is load-bearing for the invariant the whole program rests on: LOSING THE WHOLE STORE
-- COSTS QUEUE POSITION AND HISTORY AND ZERO KNOWLEDGE. If this table were authoritative for
-- lineage, dropping it would lose lineage and that invariant would be false rather than merely
-- untested. D1 proved the invariant by dropping a scratch database; keeping it true as the
-- schema grows is what this comment is for.
--
-- Reconcile against git, do not trust the row. Same posture as WAGER-14b.
--
-- ===================================================================================
-- (subject_type, subject_id) GENERALIZES WAGER-2c. IT DOES NOT REPLACE IT.
-- ===================================================================================
--
-- WAGER-2c's subject is `disposition_id` OR `wager_id`: a two-target union. The pair
-- `(subject_type, subject_id)` carried since migration 1 is a GENERALIZATION of that union, not
-- a different key and not a rename. `subject_type='disposition'` is WAGER-2c's `disposition_id`
-- arm; `subject_type='wager'` is its `wager_id` arm. Recorded here on purpose: if the two names
-- are never tied together in writing, a later reader cannot tell which is authoritative, and
-- WAGER-2c is authoritative. A third `subject_type` is a change to the rule, not to this table.
--
-- Likewise `entity_id` here is WAGER-2c's `component_id`, and stays `text` rather than a foreign
-- key for the reason migration 1 already records at its foot.
--
-- ===================================================================================
--
-- Additive and idempotent enough to re-run. `brain.touch` was verified EMPTY (0 rows) before
-- this was applied, so there is no backfill: the guard below is for a later re-run against a
-- populated store, where it fails closed with a countable message instead of a bare constraint
-- violation.
--
-- Style follows migration 1: strict on write, tolerant on read, CHECK constraints rather than
-- application validation, and an unset value means the conservative end and never the zero.

\set ON_ERROR_STOP on

BEGIN;

SET search_path TO brain, public;

-- ---------------------------------------------------------------- fail closed on bad rows
--
-- The two CHECKs below would refuse a populated table anyway. This turns that into a message
-- that says how many rows and which column, which is the difference between a five-minute fix
-- and a bisect.

DO $$
DECLARE bad_role bigint;
BEGIN
  SELECT count(*) INTO bad_role FROM brain.touch
   WHERE role IS NULL OR role NOT IN ('load-bearing', 'incidental');
  IF bad_role > 0 THEN
    RAISE EXCEPTION
      '0004_touch_wager_2c: % existing brain.touch row(s) carry a role outside WAGER-2c '
      '(load-bearing / incidental). Migration 1 defaulted role to the empty string. Reconcile '
      'those rows from git -- git holds the touch-edges, this table is a projection -- then '
      're-run.', bad_role;
  END IF;
END $$;

-- ---------------------------------------------------------------- the two new WAGER-2c fields

-- `component_type` is WAGER-2c's `component_type`. TEXT, DELIBERATELY NOT AN ENUM HERE. WAGER-2c
-- names the field and does not enumerate its values; the ten-value set the adapter validates
-- against (agent, skill, rule, playbook, workflow, command, knowledge, tool, namespace,
-- department) is the adapter's own, and a CHECK here would freeze a vocabulary the locked rule
-- never froze and would refuse a component type the brain grows later. The two columns WAGER-2c
-- DOES enumerate are constrained below. Absence of a CHECK here is a decision, not an oversight.
ALTER TABLE brain.touch ADD COLUMN IF NOT EXISTS component_type text;

-- `orient_role` is the field `D00` dropped and the one scoring runs on.
ALTER TABLE brain.touch ADD COLUMN IF NOT EXISTS orient_role text;

-- ---------------------------------------------------------------- the two enums, strict on write
--
-- Exact values, not lower(btrim(...)). Migration 1's `signal_ok` folds case because the
-- operator's canon writes signals in a second, richer vocabulary that has to parse without a
-- translation step. There is no second vocabulary here: the git side (`Touch.validate()` in
-- adapter/brain_adapter/receipt.py) tests exact set membership, and a projection that accepted
-- 'Load-Bearing' where its source refuses it would let the two drift apart in casing.
--
-- NULL is permitted on `orient_role` and means NEVER STATED, which is the same latitude
-- migration 1 gives `actor_type` and the signal columns. The four values have no conservative
-- member -- no ordering runs through tradition / previous-experience / analysis-synthesis /
-- substrate -- so an unset edge reads as unset rather than being assigned a value it never had.
-- Tolerant on read is exactly that: a read of an unset orient_role returns NULL and never
-- raises. A GARBAGE value is still refused at insert, which is the half that matters.

ALTER TABLE brain.touch DROP CONSTRAINT IF EXISTS touch_orient_role_enum;
ALTER TABLE brain.touch ADD CONSTRAINT touch_orient_role_enum CHECK (
  orient_role IS NULL
  OR orient_role IN ('tradition', 'previous-experience', 'analysis-synthesis', 'substrate')
);

-- `role` is an enum in the brain, not free text. Migration 1 gave it `NOT NULL DEFAULT ''`,
-- which the CHECK now refuses, so the default moves with it.
--
-- UNSET MEANS 'load-bearing', WHICH IS THE CONSERVATIVE END AND NOT THE ZERO. `incidental` is
-- the zero: an edge defaulted to incidental drops out of the ledger's scoring silently, which is
-- the same capability loss this migration exists to undo. An edge defaulted to load-bearing is
-- scored and can be corrected. The git side agrees and got there first --
-- `Touch.role` defaults to "load-bearing" in adapter/brain_adapter/receipt.py -- so source and
-- projection carry the same default rather than two that happen to look alike.
ALTER TABLE brain.touch ALTER COLUMN role SET DEFAULT 'load-bearing';

ALTER TABLE brain.touch DROP CONSTRAINT IF EXISTS touch_role_enum;
ALTER TABLE brain.touch ADD CONSTRAINT touch_role_enum CHECK (
  role IN ('load-bearing', 'incidental')
);

-- ---------------------------------------------------------------- the edge's identity
--
-- Migration 1's UNIQUE was (subject_type, subject_id, entity_id, role). Leaving it there while
-- adding `orient_role` would make two git edges that differ ONLY in orient_role collide, and a
-- loader written with ON CONFLICT DO NOTHING would then drop one WITHOUT ERROR -- quietly losing
-- the orient_role distinction, which is precisely the capability this migration restores. So the
-- edge's identity in the projection is widened to match the edge's identity in git.
--
-- This can only ever accept MORE rows than the old constraint, never fewer. Nothing in the repo
-- writes to brain.touch yet (store/reads.py only SELECTs from it), so no ON CONFLICT target
-- breaks. `touch_subject_idx` already covers (subject_type, subject_id), so dropping the old
-- constraint's index costs no lookup.
--
-- NULLS NOT DISTINCT is required and is not decoration. Postgres treats NULLs in a unique key as
-- distinct by default, so with the plain form two IDENTICAL `D00`-shaped rows -- both with
-- component_type and orient_role unset -- would stop conflicting, and adding these columns would
-- have SILENTLY REMOVED dedup for exactly the rows shaped like the old 4-tuple. Postgres 16.10
-- is the live server and this is a 15+ feature.

ALTER TABLE brain.touch DROP CONSTRAINT IF EXISTS touch_subject_type_subject_id_entity_id_role_key;
ALTER TABLE brain.touch DROP CONSTRAINT IF EXISTS touch_edge_key;
ALTER TABLE brain.touch ADD CONSTRAINT touch_edge_key
  UNIQUE NULLS NOT DISTINCT (subject_type, subject_id, entity_id, component_type, orient_role, role);

-- ---------------------------------------------------------------- the same notes, in the store
--
-- Duplicated from the header on purpose. A reader who reaches this table through `\d+ brain.touch`
-- has not opened this file, and the direction of truth is the one thing they must not get wrong.

COMMENT ON TABLE brain.touch IS
  'Component touch-edges, WAGER-2c shape. A PROJECTION, NOT THE SOURCE: WAGER-7 puts the '
  'component touch-edges in git, written by the adapter''s `touch add` inside the receipt, and '
  'this table is loaded from there. Reconcile against git; never let a touch-edge exist only '
  'here. If this table were authoritative for lineage, dropping the store would lose lineage '
  'and the "losing the store costs zero knowledge" invariant would be false.';

COMMENT ON COLUMN brain.touch.subject_type IS
  'With subject_id, GENERALIZES WAGER-2c''s `disposition_id` OR `wager_id` union rather than '
  'replacing it: subject_type=''disposition'' is the first arm, ''wager'' the second. A third '
  'value is a change to WAGER-2c, not to this table.';

COMMENT ON COLUMN brain.touch.entity_id IS
  'WAGER-2c''s `component_id`. Text, not a foreign key, for the reason migration 1 records.';

COMMENT ON COLUMN brain.touch.component_type IS
  'WAGER-2c''s `component_type`. Text on purpose: WAGER-2c names the field without enumerating '
  'it, so a CHECK here would freeze a vocabulary the locked rule left open.';

COMMENT ON COLUMN brain.touch.orient_role IS
  'Which part of the orientation the component came from: tradition, previous-experience, '
  'analysis-synthesis, substrate. The field D00''s 4-tuple dropped, and the axis the wager '
  'ledger scores on. NULL means never stated -- the four values have no conservative member, so '
  'an unset edge is not assigned one.';

COMMENT ON COLUMN brain.touch.role IS
  'WAGER-2c enum: load-bearing or incidental. Unset defaults to load-bearing, the conservative '
  'end -- incidental is the zero and would drop the edge out of scoring silently.';

COMMENT ON CONSTRAINT touch_edge_key ON brain.touch IS
  'The edge''s identity, matched to git''s. NULLS NOT DISTINCT because Postgres would otherwise '
  'stop deduplicating rows that leave component_type and orient_role unset.';

-- No new grants. Column privileges follow the table grant migration 2 already made:
-- SELECT, INSERT, UPDATE on brain.touch to brain_runtime, DELETE to nobody.

INSERT INTO brain.schema_migration (version, name) VALUES (4, '0004_touch_wager_2c')
  ON CONFLICT (version) DO NOTHING;

COMMIT;
