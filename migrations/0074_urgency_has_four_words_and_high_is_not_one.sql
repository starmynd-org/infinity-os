-- migration 74: URGENCY HAS FOUR WORDS, AND `high` IS NOT ONE OF THEM.
--
-- LEDGER VERSION 74. max(version) + 1 after 73, read from `engine/bin/scratch-db.sh ledger` on
-- 2026-09-09. Not a hole.
--
-- Written by 2026-09-09-IOS-term-5 for task STORE-SIGNAL-VOCAB-01a, released by
-- 2026-09-09-IOS-term-13 at 20260909T125132Z, filed by term-2 (reading source, ITEM-CONTRACT
-- 2026-09-09 section 6 F1 at 2b43d49) and term-11 (planting on ios_term11_scratch at 125038Z).
--
-- ================================================================= what this closes, measured
--
-- term-11, ios_term11_scratch at ledger 71, 4 calls and 1 planted row:
--     brain.signal_ok('urgency', 'high')      true    <- the defect: the view's vocabulary is
--                                                        (none, soon, deadline, decaying)
--     brain.signal_ok('urgency', 'deadline')  true    control
--     brain.signal_ok('effort', 'medium')     true    in both sets, no defect
--     brain.signal_ok('stakes', 'urgent')     false   control, refused by both
--     INSERT INTO brain.work_item ... urgency = 'high'   LANDED, 1 row, removed by id
--
-- migrations/0006_signal_numeric_vocabulary.sql lines 140 to 152: brain.signal_ok admits
-- `low`, `medium` and `high` for EVERY signal column before the per-field sets. For urgency the
-- per-field set is the whole vocabulary and the generic clause is the hole. web/views/signals.py
-- refuses `high` with ValueError in Signals.from_contract, so a row the store accepted 500s the
-- Attention surface the moment the raw column reaches it (the other half, STORE-SIGNAL-VOCAB-01b,
-- is term-4's: render undeclared rather than fail). term-3 already refuses the value at the
-- ingest door, so this bites only rows that reach the store another way, which is every fixture.
--
-- ================================================================= why CREATE OR REPLACE, here
--
-- The seven CHECK constraints on brain.work_item (migration 1) call brain.signal_ok by name. A
-- second function beside it cannot narrow a CHECK that does not call it, and a second constraint
-- would leave the first one admitting the value. So the function body is replaced, and this
-- header says so in words because migration 6's file then no longer describes what its function
-- does on a store at 74: from 74 on, the generic low/medium/high clause excludes urgency and
-- everything else in the body is byte-for-byte migration 6's. 0006 is not edited (append-only;
-- the integrator's gate refuses an edit).
--
-- ================================================================= exactly one field, and why
--
-- The view's vocabulary also differs from the store's for THREE OTHER FIELDS, found by reading
-- web/views/signals.py beside migration 6 while writing this:
--
--     stakes         store admits none, low, medium, high, critical;   view: low, medium, high, critical
--     reversibility  store admits low, medium, high AND the three words; view: reversible, costly, irreversible
--     effort         store admits low, medium, high, small, large;     view: small, medium, large
--
-- Those are NOT tightened here. The task names urgency, with a measurement, a predicate and a
-- confirming seat; the other three have a reading of source and no landed row yet, and the
-- engine's own vocabulary (engine/swarm_engine/signals.py) folds stakes' five words and
-- reversibility's levels deliberately. They are filed as siblings on the bus for term-13 to
-- measure and route, per its own sibling-sweep rule, rather than widened into this file.
--
-- ================================================================= rows the store already holds
--
-- Replacing a CHECK's function does not re-validate existing rows. A row already holding
-- urgency = 'high' stays as it is until the next UPDATE touches it, at which point the CHECK
-- refuses the UPDATE. The self-check below COUNTS such rows and prints the count; it does not
-- coerce them (the task's own text: "report the count and do not coerce them; that is a second
-- decision"). On live `brain` the count is the operator's to read, with:
--
--     SELECT count(*) FROM brain.work_item WHERE lower(btrim(urgency)) IN ('low','medium','high');
--
-- ================================================================= rollback, executed
--
-- migrations/tests/rollback-0074.sql restores migration 6's body verbatim and deletes the ledger
-- row. Two-way, no loss.

\set ON_ERROR_STOP on

BEGIN;

CREATE OR REPLACE FUNCTION brain.signal_ok(field text, raw text) RETURNS boolean
  LANGUAGE sql IMMUTABLE AS $$
  SELECT raw IS NULL OR btrim(raw) = ''
      -- Migration 74: the generic three-level clause no longer covers urgency, whose vocabulary
      -- is the four words below and nothing else. Every other line is migration 6's.
      OR (field <> 'urgency' AND lower(btrim(raw)) IN ('low','medium','high'))
      OR (field = 'stakes'         AND lower(btrim(raw)) IN ('none','critical'))
      OR (field = 'reversibility'  AND lower(btrim(raw)) IN ('reversible','costly','irreversible'))
      OR (field = 'urgency'        AND lower(btrim(raw)) IN ('none','soon','deadline','decaying'))
      OR (field = 'effort'         AND lower(btrim(raw)) IN ('small','large'))
      OR (field IN ('dependency_unblocking','confidence')
          AND brain.signal_numeric(raw) IS NOT NULL)
$$;

COMMENT ON FUNCTION brain.signal_ok(text, text) IS
  'Strict on write. NULL and '''' mean never assessed and are permitted. Since migration 6 a '
  'decimal literal is permitted for dependency_unblocking and confidence and nothing else. '
  'Since migration 74 urgency admits exactly none, soon, deadline and decaying: the generic '
  'low/medium/high clause does not cover it, because the surface that renders it refuses those '
  'words and a row the store accepted was a 500 waiting to happen.';

-- ---------------------------------------------------------------- watched refusing, and counted

DO $$
DECLARE
  n int := 0; refusals int := 0; controls int := 0;
  held bigint; seq_last bigint; seq_called boolean; item text;
BEGIN
  -- 1 refusal: the defect itself, at the function.
  IF brain.signal_ok('urgency', 'high') THEN
    RAISE EXCEPTION 'migration 74: signal_ok still admits urgency high';
  END IF;
  n := n + 1; refusals := refusals + 1;
  -- 2 control: the vocabulary still lands.
  IF NOT (brain.signal_ok('urgency', 'deadline') AND brain.signal_ok('urgency', 'none')
          AND brain.signal_ok('urgency', 'soon') AND brain.signal_ok('urgency', 'decaying')
          AND brain.signal_ok('urgency', NULL) AND brain.signal_ok('urgency', '')) THEN
    RAISE EXCEPTION 'migration 74: a legitimate urgency word was refused';
  END IF;
  n := n + 1; controls := controls + 1;
  -- 3 control: the other fields are untouched.
  IF NOT (brain.signal_ok('effort', 'medium') AND brain.signal_ok('stakes', 'high')
          AND brain.signal_ok('charter_alignment', 'low') AND brain.signal_ok('confidence', '0.5'))
     OR brain.signal_ok('stakes', 'urgent') THEN
    RAISE EXCEPTION 'migration 74: another field''s vocabulary moved';
  END IF;
  n := n + 1; controls := controls + 1;
  -- 4 refusal: the planted row, at the CHECK.
  SELECT last_value, is_called INTO seq_last, seq_called FROM brain.item_id_seq;
  BEGIN
    INSERT INTO brain.work_item (title, lane, urgency) VALUES ('migration 74 plant', 'mv', 'high')
      RETURNING id INTO item;
    RAISE EXCEPTION 'migration 74: a work item with urgency high LANDED as %', item;
  EXCEPTION WHEN check_violation THEN n := n + 1; refusals := refusals + 1;
  END;
  -- 5 control: the same row with a real word lands, and is removed.
  INSERT INTO brain.work_item (title, lane, urgency) VALUES ('migration 74 control', 'mv', 'deadline')
    RETURNING id INTO item;
  DELETE FROM brain.work_item WHERE id = item;
  PERFORM setval('brain.item_id_seq', seq_last, seq_called);
  n := n + 1; controls := controls + 1;

  SELECT count(*) INTO held FROM brain.work_item
   WHERE lower(btrim(urgency)) IN ('low', 'medium', 'high');
  IF n <> 5 OR refusals <> 2 OR controls <> 3 THEN
    RAISE EXCEPTION 'migration 74: expected 5 checks as 2 refusals and 3 controls, ran % as % and %',
      n, refusals, controls;
  END IF;
  RAISE NOTICE 'migration 74: % of 5 checks passed, % refusals watched refusing and % positive '
               'controls. urgency high is refused at the function and at the CHECK; the four '
               'words land. THIS STORE HOLDS % existing work_item row(s) with urgency in '
               '(low, medium, high); they are counted, not coerced, and each will refuse its next '
               'UPDATE until somebody decides what word it meant.', n, refusals, controls, held;
END $$;

INSERT INTO brain.schema_migration (version, name)
  VALUES (74, '0074_urgency_has_four_words_and_high_is_not_one')
  ON CONFLICT (version) DO NOTHING;

COMMIT;
