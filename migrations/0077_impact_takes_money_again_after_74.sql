-- migration 77: IMPACT TAKES MONEY AGAIN. Migration 74 dropped it from the write gate.
--
-- LEDGER VERSION 77. max(version) + 1 after 76, read at 928e1db across the three ledger
-- directories (migrations, budget/schema, queue/schema) and matching W-05's
-- `engine/bin/scratch-db.sh ledger` output, "NEXT VERSION IS 77": 73 versions in 1..76, no
-- duplicates. The holes at 54, 55 and 60 are NOT filled. A hole is history, and filling one would
-- reorder nothing the live store saw.
--
-- Written by ROUTINES-admiral (INFINITY-REBOOT-1) at 2026-09-13T19:43Z for IDEBT-INT-9 (M7),
-- ruled into this lane by the restart coordinator. Found while fixing IDEBT-INT-8/7 at 928e1db:
-- W-05's queue run refused `impact` "20000" and "1000" on a scratch store at ledger 76, where
-- migration 48 says both are legal.
--
-- ================================================================= what this closes, read from source
--
-- brain.signal_ok has been redefined four times before this file, in ledger order (census by clause,
-- recorded with this commit):
--
--     0001  base: blank; low/medium/high for every field; stakes none/critical; reversibility's
--           three words; urgency's four words; effort small/large
--     0006  keeps every 0001 clause; ADDS numeric dependency_unblocking and confidence
--     0048  keeps every 0006 clause; ADDS impact none/critical and impact money (0, or >= 100)
--     0074  NARROWS the generic low/medium/high clause to exclude urgency, as it intended, and
--           DROPS BOTH 0048 impact clauses, which it did not intend. Its header says "everything
--           else in the body is byte-for-byte migration 6's", and migration 48 had redefined the
--           function after 6.
--
-- So on a store at 74, 75 or 76, `impact` admits only NULL, '' and low/medium/high. Every amount of
-- money, and the bands `critical` and `none`, is refused at the function and at
-- work_item_impact_check (migration 48), which calls it. The writers that post those values are live
-- code, not fixtures:
--
--     engine/swarm_engine/transitions.py   a post that declares impact
--     `swarm set <id> impact <amount>`
--     queue/human_queue/transitions.py     `queue type --impact`
--     engine/swarm_engine/cli.py           the prompt that offers "an amount of money like 25000"
--
-- engine/swarm_engine/signals.py still accepts money on the Python side, so the two gates disagree
-- and the store wins. 0074's self-check exercised effort, stakes, charter_alignment and confidence and
-- never impact, which is how it passed. migrations/tests/rollback-0074.sql restored 6's body as well,
-- so rolling 74 back did not bring 48's clauses back either; that file is corrected in this commit.
--
-- ================================================================= what this does, and only this
--
-- CREATE OR REPLACE with 0074's committed body plus 0048's two impact clauses, verbatim. No other
-- clause moves: urgency keeps 74's exclusion, and the generic, stakes, reversibility, effort and
-- numeric clauses are 74's text unchanged. The CHECK constraints call the function by name, so
-- replacing the body is the whole fix, for the reason 0074 itself gives.
--
-- ================================================================= rows the store already holds
--
-- Replacing a CHECK's function re-validates nothing. Between 74 and 77 no row could be WRITTEN with
-- money, critical or none. A row that already held one (written at 48 to 73) stayed, and each such
-- row refused its next UPDATE of any column, because the CHECK is re-evaluated on update. From 77
-- those UPDATEs land again. The self-check below COUNTS rows with impact outside low/medium/high and
-- prints the count; nothing is coerced.
--
-- ================================================================= a hazard this file cannot close
--
-- Re-running 0074's FILE on a store at 77 puts the impact-less body back while ledger row 77 stays.
-- migrations/tests/prove_73_74_and_part2.py does exactly that (rollback-0074, then 0074) on the
-- scratch store it is pointed at. After such a run, apply this file again: its collision guard
-- admits its own name, so a re-run is legal.
--
-- ================================================================= stores below 74, and live
--
-- THIS FILE REFUSES ON A STORE WITHOUT LEDGER 74, and records nothing when it does. That is
-- deliberate:
--
--   - Its body carries 74's urgency exclusion. Applied alone to a store below 74 it would bring in
--     74's narrowing without 74's checks or 74's count of held urgency-high rows, and each such
--     row would refuse its next UPDATE.
--   - A body that branched on the ledger (48's body below 74, 74's plus impact from 74 on) would
--     be worse. It would record ledger row 77 on a store below 74. When 74's file was applied
--     later, in recorded-version order, it would drop impact again with 77 already recorded: the
--     defect, with its fix marked as done.
--   - Refusing leaves 77 pending, so the builder's normal order (every pending version,
--     ascending: 74 then 77) lands both correctly.
--
-- Live brain, read-only, REPORTED by the restart coordinator at 2026-09-13T19:47:35Z:
--
--   - Ledger versions up to 69, plus 78 (0018_objectives_reach_attention). 70 to 76 not applied.
--   - So 74 is not on live, and live's signal_ok behaves as 48's: impact 25000, 100, critical and
--     none admitted, 3 refused, urgency high admitted. Live is not hit by the impact defect today.
--   - Applying 74 to live without 77 in the same run opens the defect on live until 77 lands.
--     Apply them together, 74 then 77.
--   - Version 78 is held on live by a file this tree does not carry. The next migration after
--     this one is 79, not max(version) + 1 read from the tree.
--
-- ================================================================= rollback, scratch only
--
-- migrations/tests/rollback-0077.sql restores 0074's body verbatim and deletes ledger row 77. That
-- brings the defect back, which is what undoing this file means. No row is lost.

\set ON_ERROR_STOP on

BEGIN;

DO $$
DECLARE occupied text; has74 boolean; has_check boolean;
BEGIN
  SELECT name INTO occupied FROM brain.schema_migration WHERE version = 77;
  IF occupied IS NOT NULL AND occupied <> '0077_impact_takes_money_again_after_74' THEN
    RAISE EXCEPTION 'migration 77: ledger version 77 is already held by %, so this file would '
                    'apply its DDL and never record itself', occupied
      USING ERRCODE = 'duplicate_object',
            HINT = 'Renumber this file to the next free version and re-run.';
  END IF;
  -- The body below is 74's plus 48's clauses. On a store without either, it would be the wrong body.
  SELECT EXISTS (SELECT 1 FROM brain.schema_migration WHERE version = 74) INTO has74;
  SELECT EXISTS (SELECT 1 FROM pg_constraint
                  WHERE conrelid = 'brain.work_item'::regclass
                    AND conname = 'work_item_impact_check' AND contype = 'c') INTO has_check;
  IF NOT has74 OR NOT has_check THEN
    RAISE EXCEPTION 'migration 77 needs ledger 74 and work_item_impact_check (48): 74 applied %, '
                    'impact check present %', has74, has_check
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
END $$;

CREATE OR REPLACE FUNCTION brain.signal_ok(field text, raw text) RETURNS boolean
  LANGUAGE sql IMMUTABLE AS $$
  SELECT raw IS NULL OR btrim(raw) = ''
      -- Migration 74: the generic three-level clause no longer covers urgency, whose vocabulary
      -- is the four words below and nothing else.
      OR (field <> 'urgency' AND lower(btrim(raw)) IN ('low','medium','high'))
      OR (field = 'stakes'         AND lower(btrim(raw)) IN ('none','critical'))
      OR (field = 'reversibility'  AND lower(btrim(raw)) IN ('reversible','costly','irreversible'))
      OR (field = 'urgency'        AND lower(btrim(raw)) IN ('none','soon','deadline','decaying'))
      OR (field = 'effort'         AND lower(btrim(raw)) IN ('small','large'))
      OR (field IN ('dependency_unblocking','confidence')
          AND brain.signal_numeric(raw) IS NOT NULL)
      -- Migration 48, restored by migration 77 after 74 dropped it. The bands, plus money, and the
      -- ambiguous band between them is refused.
      OR (field = 'impact' AND lower(btrim(raw)) IN ('none','critical'))
      OR (field = 'impact' AND brain.signal_numeric(raw) IS NOT NULL
          AND (brain.signal_numeric(raw) = 0 OR brain.signal_numeric(raw) >= 100))
$$;

COMMENT ON FUNCTION brain.signal_ok(text, text) IS
  'Strict on write. NULL and '''' mean never assessed and are permitted. Since migration 6 a '
  'decimal literal is permitted for dependency_unblocking and confidence. Since migration 48 '
  '`impact` takes a band or an amount of money, and REFUSES a bare number in (0, 100) because '
  'that reads as both money and a 1-to-5 rating. Since migration 74 urgency admits exactly none, '
  'soon, deadline and decaying. Migration 77 restored the two impact clauses 74 dropped.';

-- ---------------------------------------------------------------- watched refusing, and counted

DO $$
DECLARE
  n int := 0; refusals int := 0; controls int := 0;
  held bigint; seq_last bigint; seq_called boolean; item text;
BEGIN
  -- AT THE FUNCTION: the five the ruling names.
  -- 1 control: 100 is the first accepted amount, migration 48's own boundary.
  IF NOT brain.signal_ok('impact', '100') THEN
    RAISE EXCEPTION 'migration 77: impact=100 was refused, so money is still dropped';
  END IF;
  n := n + 1; controls := controls + 1;
  -- 2 refusal: 3 is ambiguous between money and a rating, and must stay refused.
  IF brain.signal_ok('impact', '3') THEN
    RAISE EXCEPTION 'migration 77: impact=3 was accepted, so the restore loosened 48''s refusal';
  END IF;
  n := n + 1; refusals := refusals + 1;
  -- 3 control: the band critical.
  IF NOT brain.signal_ok('impact', 'critical') THEN
    RAISE EXCEPTION 'migration 77: impact=critical was refused';
  END IF;
  n := n + 1; controls := controls + 1;
  -- 4 control: the band none.
  IF NOT brain.signal_ok('impact', 'none') THEN
    RAISE EXCEPTION 'migration 77: impact=none was refused';
  END IF;
  n := n + 1; controls := controls + 1;
  -- 5 refusal: urgency high stays refused. 74 is kept, not undone.
  IF brain.signal_ok('urgency', 'high') THEN
    RAISE EXCEPTION 'migration 77: urgency=high was accepted, so 74''s exclusion was lost';
  END IF;
  n := n + 1; refusals := refusals + 1;

  -- AT THE FUNCTION: the neighbours a wrong splice would move.
  -- 6 refusal: the top of the refused band.
  IF brain.signal_ok('impact', '99.9') THEN
    RAISE EXCEPTION 'migration 77: impact=99.9 was accepted and is inside the refused band';
  END IF;
  n := n + 1; refusals := refusals + 1;
  -- 7 control: 0 means none, 20000 is a value the queue suite posts, and a band still lands.
  IF NOT (brain.signal_ok('impact', '0') AND brain.signal_ok('impact', '20000')
          AND brain.signal_ok('impact', 'high')) THEN
    RAISE EXCEPTION 'migration 77: impact 0, 20000 or high was refused';
  END IF;
  n := n + 1; controls := controls + 1;
  -- 8 control: urgency's four words still land.
  IF NOT (brain.signal_ok('urgency', 'none') AND brain.signal_ok('urgency', 'soon')
          AND brain.signal_ok('urgency', 'deadline') AND brain.signal_ok('urgency', 'decaying')) THEN
    RAISE EXCEPTION 'migration 77: a legitimate urgency word was refused';
  END IF;
  n := n + 1; controls := controls + 1;
  -- 9 refusal: money belongs to impact alone.
  IF brain.signal_ok('stakes', '20000') OR brain.signal_ok('stakes', '5')
     OR brain.signal_ok('urgency', '20000') OR brain.signal_ok('urgency', 'critical') THEN
    RAISE EXCEPTION 'migration 77: money or critical became acceptable on a field that is not impact';
  END IF;
  n := n + 1; refusals := refusals + 1;
  -- 10 control: the other fields are untouched.
  IF NOT (brain.signal_ok('effort', 'medium') AND brain.signal_ok('stakes', 'high')
          AND brain.signal_ok('charter_alignment', 'low') AND brain.signal_ok('confidence', '0.5')) THEN
    RAISE EXCEPTION 'migration 77: another field''s vocabulary moved';
  END IF;
  n := n + 1; controls := controls + 1;

  -- AT THE CHECK, against the real table, with the id sequence put back afterwards.
  SELECT last_value, is_called INTO seq_last, seq_called FROM brain.item_id_seq;
  -- 11 refusal: an impact 3 row.
  BEGIN
    INSERT INTO brain.work_item (title, lane, impact) VALUES ('migration 77 plant', 'mv', '3')
      RETURNING id INTO item;
    RAISE EXCEPTION 'migration 77: a work item with impact 3 LANDED as %', item;
  EXCEPTION WHEN check_violation THEN n := n + 1; refusals := refusals + 1;
  END;
  -- 12 refusal: an urgency high row.
  BEGIN
    INSERT INTO brain.work_item (title, lane, urgency) VALUES ('migration 77 plant', 'mv', 'high')
      RETURNING id INTO item;
    RAISE EXCEPTION 'migration 77: a work item with urgency high LANDED as %', item;
  EXCEPTION WHEN check_violation THEN n := n + 1; refusals := refusals + 1;
  END;
  -- 13 control: an impact 100 row lands, and is removed.
  INSERT INTO brain.work_item (title, lane, impact) VALUES ('migration 77 control', 'mv', '100')
    RETURNING id INTO item;
  DELETE FROM brain.work_item WHERE id = item;
  n := n + 1; controls := controls + 1;
  -- 14 control: an impact critical, urgency deadline row lands, and is removed.
  INSERT INTO brain.work_item (title, lane, impact, urgency)
    VALUES ('migration 77 control', 'mv', 'critical', 'deadline')
    RETURNING id INTO item;
  DELETE FROM brain.work_item WHERE id = item;
  n := n + 1; controls := controls + 1;
  PERFORM setval('brain.item_id_seq', seq_last, seq_called);

  SELECT count(*) INTO held FROM brain.work_item
   WHERE impact IS NOT NULL AND lower(btrim(impact)) NOT IN ('', 'low', 'medium', 'high');
  IF n <> 14 OR refusals <> 6 OR controls <> 8 THEN
    RAISE EXCEPTION 'migration 77: expected 14 checks as 6 refusals and 8 controls, ran % as % and %',
      n, refusals, controls;
  END IF;
  RAISE NOTICE 'migration 77: % of 14 checks passed, % refusals watched refusing and % positive '
               'controls. impact takes money, critical and none again at the function and at the '
               'CHECK; impact 3 and urgency high stay refused. THIS STORE HOLDS % work_item row(s) '
               'with impact outside (low, medium, high); on a store that sat at 74 to 76 each would '
               'have refused its next UPDATE, and from 77 they update again. Counted, not coerced.',
               n, refusals, controls, held;
END $$;

INSERT INTO brain.schema_migration (version, name)
  VALUES (77, '0077_impact_takes_money_again_after_74')
  ON CONFLICT (version) DO NOTHING;

COMMIT;
