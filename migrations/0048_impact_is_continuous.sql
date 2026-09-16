-- migration 48: `impact` replaces `stakes` as the value half of the human queue's spine, and
-- the whole point of it is that it is CONTINUOUS.
--
-- Row 0434, his ask 4. Written 2026-08-31.
--
-- WHY HIS ASK APPEARED TO FAIL, AND IT WAS NEVER HIS MISTAKE. He asked for urgency times
-- importance. A lane built it, measured that the product collapsed his Shape tier from 19
-- distinct scores to 3, and concluded the product was the wrong shape. The product was fine.
-- The cause is that `stakes` is CATEGORICAL and NARROW: the write gate accepts five words
-- (`none`, `low`, `medium`, `high`, `critical`) and `brain.signal_level` then folds them onto
-- THREE (`none` reads low, `critical` reads high), so two of the five words are not
-- distinguishable after the fold at all. A product of two three-valued scales has at most nine
-- cells and fewer distinct values than that. No arrangement of a coarse scale produces a fine
-- ordering.
--
-- HIS OWN INSTINCT IS THE FIX, in his words on 2026-08-30: *"maybe we should refactor to align
-- with whatever you think is best... include importance stakes or maybe better to say impact
-- which could be money based like rev impact"*. Money is continuous. A signal that can carry
-- money can carry resolution, and the collapse disappears without touching the product.
--
-- ------------------------------------------------------------------ what this migration does
--
--   1. `brain.work_item.impact`, one nullable text column. Accepts a BAND or a NUMBER.
--   2. `brain.impact_magnitude(text)`, the continuous fold. This is the new function and it is
--      the one that carries the design.
--   3. `brain.signal_ok` and `brain.signal_level` widened for the `impact` field, so the write
--      gate and the read fold stay one expression apart the way migration 6 required.
--   4. `brain.work_item_signals` gains `impact` and `impact_magnitude`, APPENDED, which is what
--      CREATE OR REPLACE VIEW permits and what keeps every existing caller working untouched.
--
-- ------------------------------------------------------------------ NOTHING IS BACKFILLED
--
-- NOT ONE ROW IS EDITED BY THIS FILE and that is deliberate. The read falls back:
-- `COALESCE(w.impact, w.stakes)`. A row that has never been given an impact keeps scoring
-- exactly as its stakes scored, because its stakes IS what is read. A row with no stakes either
-- keeps the conservative default that `stakes` has always had, which is `high`. So the day this
-- lands, no row moves for want of an edit, and a row moves only when somebody types a number
-- onto it on purpose.
--
-- This is why `stakes` is not dropped, not renamed and not deprecated in the database. It is the
-- fallback, it is what the FLEET's claim ordering still reads (see the scope note below), and a
-- column that three other surfaces read is not something one lane retires on a Sunday.
--
-- ------------------------------------------------------------------ THE MONEY SCALE, ANCHORED
--
-- A bare number IS MONEY, in whatever single currency the operator keeps his numbers in. It is
-- NOT a one-to-five rating, and that ambiguity is the one trap in this design, so the write gate
-- refuses the values where the two readings differ rather than silently picking one. See
-- `signal_ok` below: `impact=3` is REFUSED, out loud, naming both readings.
--
-- The fold is one decade of money per band step, anchored on three named points:
--
--       impact=1000     -> magnitude 1.0    the same as the band `medium`
--       impact=10000    -> magnitude 2.0    the same as the band `high`
--       impact=100000   -> magnitude 3.0    the same as the band `critical`
--
-- so `magnitude = log10(money / 100)`, clamped into [0.25, 4.0]. Logarithmic and not linear
-- because the alternative was measured to be unusable: on a linear scale one row carrying a
-- 500k number sets the top of the range and every other row on his board rounds to the same
-- indistinguishable sliver near zero. That is the collapse this migration exists to end, arriving
-- through the other door. A log scale says a 100k row outranks a 10k row by exactly as much as
-- the 10k row outranks a 1k row, which is the comparison he actually makes.
--
-- THE CLAMP AT THE BOTTOM IS 0.25 AND NOT 0. A zero magnitude annihilates the product: an item
-- would score zero on the spine however urgent it is, and urgency would stop being able to raise
-- anything. Nothing in this system is worth exactly nothing while it is still on the board, so
-- the floor is a small positive number and `none` sits on it.
--
-- ------------------------------------------------------------------ SCOPE, STATED PLAINLY
--
-- THE FLEET'S CLAIM ORDERING IS NOT TOUCHED BY THIS FILE. `brain.work_item`'s claim path orders
-- by `_score_sql()` in engine/swarm_engine/transitions.py, which reads `stakes`, and it still
-- does. This migration serves the HUMAN queue, which is what row 0434 is about. The two agree on
-- every row that carries stakes and no impact, which is every row that exists today, and they
-- diverge only on a row somebody has typed money onto. That divergence is real, it is small
-- today, and it is written down here rather than discovered later: closing it means changing what
-- the fleet claims first, which is a separate decision and not this lane's to take.
--
-- APPLY AS: the bootstrap superuser (`postgres`), against `brain`, via
-- `store/bin/apply-migration.sh`. NOT as `brain_owner`: measured 2026-08-31, all 40 tables
-- in the `brain` schema are owned by `postgres`, and applying as `brain_owner` dies mid-file
-- on `must be owner of table work_item`. Every apply doc in this repo said otherwise and was
-- wrong. Rehearsed 47 -> 52 on `brain_baseline`, a store built from HEAD at ledger 47.

\set ON_ERROR_STOP on

-- The prefix in the filename is a per-lane counter and it lies. The recorded version below is the
-- key. Two lanes reaching for one number is the collision that matters, because every file here
-- writes ON CONFLICT (version) DO NOTHING: the loser applies its DDL and skips its ledger row, so
-- it re-applies forever and the ledger never catches up. Same guard migrations 24 and 25 carry.
DO $$
DECLARE taken text;
BEGIN
  SELECT name INTO taken FROM brain.schema_migration WHERE version = 48;
  IF taken IS NOT NULL AND taken <> '0048_impact_is_continuous' THEN
    RAISE EXCEPTION 'ledger version 48 is already held by %, so this file would apply its DDL '
                    'and never record itself', taken
      USING HINT = 'Renumber this file to the next free version and re-run.';
  END IF;
END $$;

BEGIN;

-- ------------------------------------------------------------------ the column

ALTER TABLE brain.work_item
  ADD COLUMN IF NOT EXISTS impact text;

COMMENT ON COLUMN brain.work_item.impact IS
  'What this work is worth, as a band (none, low, medium, high, critical) or as MONEY (a bare '
  'number, read as money and never as a 1-to-5 rating). NULL means never assessed, and the read '
  'path falls back to `stakes` so a row nobody has touched keeps the ordering it already had.';

-- ------------------------------------------------------------------ the continuous fold
--
-- One decade of money per band step. The three anchors in the header are asserted at the foot of
-- this file, watched producing their numbers, rather than asserted in prose here.

CREATE OR REPLACE FUNCTION brain.impact_magnitude(raw text) RETURNS numeric
  LANGUAGE sql IMMUTABLE AS $$
  SELECT COALESCE(
    -- The bands. `none` is the floor and not zero, for the annihilation reason in the header.
    CASE lower(btrim(COALESCE(raw, '')))
      WHEN 'none'     THEN 0.25
      WHEN 'low'      THEN 0.5
      WHEN 'medium'   THEN 1.0
      WHEN 'high'     THEN 2.0
      WHEN 'critical' THEN 3.0
      ELSE NULL END,
    -- Money. `brain.signal_numeric` is migration 6's literal gate and is reused rather than
    -- reimplemented, so that what this folds and what `signal_ok` accepts cannot drift.
    -- `signal_numeric` returns double precision; log(numeric, numeric) is the two-argument
    -- form, so the cast is explicit rather than left to a resolution that has no candidate.
    CASE WHEN brain.signal_numeric(raw) IS NOT NULL THEN
      greatest(0.25::numeric, least(4.0::numeric,
        CASE WHEN brain.signal_numeric(raw) < 100 THEN 0.25::numeric
             ELSE log(10::numeric, (brain.signal_numeric(raw) / 100.0)::numeric) END))
      ELSE NULL END,
    -- Unset or unreadable means conservative, and conservative here is what `stakes` has always
    -- defaulted to: `high`. An unassessed row must not sink below an assessed one for saying
    -- nothing.
    2.0::numeric);
$$;

COMMENT ON FUNCTION brain.impact_magnitude(text) IS
  'The continuous value fold: a band or an amount of money onto a magnitude in [0.25, 4.0]. '
  'Anchored so 1000 reads the same as the band medium, 10000 as high and 100000 as critical. '
  'Never returns 0, because a 0 would annihilate the urgency times impact product. Unset or '
  'unreadable returns 2.0, the conservative value stakes has always taken.';

GRANT EXECUTE ON FUNCTION brain.impact_magnitude(text) TO brain_runtime, brain_subscriber;

-- ------------------------------------------------------------------ the write gate, widened
--
-- STRICT ON WRITE, and the one thing it is strict about that no other signal is: a bare number
-- between 0 and 100 is REFUSED. `impact=3` is ambiguous between three dollars and three out of
-- five, the two readings order the row very differently, and only one of them is what the writer
-- meant. The writer is present to be told; the reader never is. `impact=0` is accepted and means
-- none.

CREATE OR REPLACE FUNCTION brain.signal_ok(field text, raw text) RETURNS boolean
  LANGUAGE sql IMMUTABLE AS $$
  SELECT raw IS NULL OR btrim(raw) = ''
      OR lower(btrim(raw)) IN ('low','medium','high')
      OR (field = 'stakes'         AND lower(btrim(raw)) IN ('none','critical'))
      OR (field = 'reversibility'  AND lower(btrim(raw)) IN ('reversible','costly','irreversible'))
      OR (field = 'urgency'        AND lower(btrim(raw)) IN ('none','soon','deadline','decaying'))
      OR (field = 'effort'         AND lower(btrim(raw)) IN ('small','large'))
      OR (field IN ('dependency_unblocking','confidence')
          AND brain.signal_numeric(raw) IS NOT NULL)
      -- Migration 48. The bands, plus money, and the ambiguous band between them is refused.
      OR (field = 'impact' AND lower(btrim(raw)) IN ('none','critical'))
      OR (field = 'impact' AND brain.signal_numeric(raw) IS NOT NULL
          AND (brain.signal_numeric(raw) = 0 OR brain.signal_numeric(raw) >= 100))
$$;

COMMENT ON FUNCTION brain.signal_ok(text, text) IS
  'Strict on write. NULL and '''' mean never assessed and are permitted. Since migration 6 a '
  'decimal literal is permitted for dependency_unblocking and confidence. Since migration 48 '
  '`impact` takes a band or an amount of money, and REFUSES a bare number in (0, 100) because '
  'that reads as both money and a 1-to-5 rating and the two order the row differently.';

-- ------------------------------------------------------------------ the read fold, widened
--
-- `impact` still answers `signal_level` with a BAND, because every existing surface that prints a
-- signal prints a level and none of them should have to learn a new shape to keep working. The
-- continuous number is a separate function and a separate column on the view.

CREATE OR REPLACE FUNCTION brain.signal_level(field text, raw text) RETURNS text
  LANGUAGE sql IMMUTABLE AS $$
  SELECT COALESCE(
    CASE lower(btrim(COALESCE(raw, '')))
      WHEN 'low' THEN 'low' WHEN 'medium' THEN 'medium' WHEN 'high' THEN 'high'
      ELSE NULL END,
    CASE field || ':' || lower(btrim(COALESCE(raw, '')))
      WHEN 'stakes:none'             THEN 'low'
      WHEN 'stakes:critical'         THEN 'high'
      WHEN 'impact:none'             THEN 'low'
      WHEN 'impact:critical'         THEN 'high'
      WHEN 'reversibility:reversible' THEN 'high'
      WHEN 'reversibility:costly'    THEN 'medium'
      WHEN 'reversibility:irreversible' THEN 'low'
      WHEN 'urgency:none'            THEN 'low'
      WHEN 'urgency:soon'            THEN 'medium'
      WHEN 'urgency:deadline'        THEN 'high'
      WHEN 'urgency:decaying'        THEN 'high'
      WHEN 'effort:small'            THEN 'low'
      WHEN 'effort:large'            THEN 'high'
      ELSE NULL END,
    CASE
      WHEN field = 'dependency_unblocking' AND brain.signal_numeric(raw) IS NOT NULL THEN
        CASE WHEN trunc(brain.signal_numeric(raw)) <= 0 THEN 'low'
             WHEN trunc(brain.signal_numeric(raw)) <= 2 THEN 'medium'
             ELSE 'high' END
      WHEN field = 'confidence' AND brain.signal_numeric(raw) IS NOT NULL THEN
        CASE WHEN brain.signal_numeric(raw) < 0.4 THEN 'low'
             WHEN brain.signal_numeric(raw) < 0.8 THEN 'medium'
             ELSE 'high' END
      -- Migration 48. Money read as a BAND, on the same anchors the magnitude uses, so a surface
      -- that only knows how to print a level prints one that agrees with the number.
      WHEN field = 'impact' AND brain.signal_numeric(raw) IS NOT NULL THEN
        CASE WHEN brain.impact_magnitude(raw) < 1.0 THEN 'low'
             WHEN brain.impact_magnitude(raw) < 2.0 THEN 'medium'
             ELSE 'high' END
      ELSE NULL END,
    CASE field
      WHEN 'stakes' THEN 'high'
      WHEN 'impact' THEN 'high'
      WHEN 'reversibility' THEN 'low'
      WHEN 'effort' THEN 'high'
      ELSE 'low' END)
$$;

COMMENT ON FUNCTION brain.signal_level(text, text) IS
  'Tolerant on read. Folds the canon vocabulary onto low/medium/high and returns the conservative '
  'value for anything unset or unreadable. Since migration 48 it also folds `impact`, including '
  'a money amount, onto a band on the same anchors brain.impact_magnitude uses. Never raises, '
  'never returns medium for an unknown. Agrees with swarm_engine.signals.signal_level by test.';

-- ------------------------------------------------------------------ the check on the column
--
-- Added AFTER signal_ok is widened, or the constraint would be validated against the old function
-- and refuse every value this migration exists to accept.

ALTER TABLE brain.work_item
  DROP CONSTRAINT IF EXISTS work_item_impact_check;
ALTER TABLE brain.work_item
  ADD CONSTRAINT work_item_impact_check CHECK (brain.signal_ok('impact', impact));

-- ------------------------------------------------------------------ the view, appended to
--
-- Column names, types and order are unchanged through `lineage_cycle`; `impact` and
-- `impact_magnitude` are APPENDED, which is what CREATE OR REPLACE VIEW permits and what keeps
-- `brain.auto_accept_candidate` and every other caller working untouched.
--
-- BOTH COLUMNS READ `COALESCE(w.impact, w.stakes)`. That single expression is the whole
-- no-backfill design: an untouched row answers with its stakes, through the impact fold, and the
-- two folds agree on every band both vocabularies share.

CREATE OR REPLACE VIEW brain.work_item_signals AS
  SELECT w.id,
         l.has_external OR l.is_cycle OR l.is_truncated AS external,
         l.has_canon    OR l.is_cycle OR l.is_truncated AS canon_touching,
         brain.signal_level('stakes', w.stakes)                               AS stakes,
         brain.signal_level('reversibility', w.reversibility)                 AS reversibility,
         brain.signal_level('urgency', w.urgency)                             AS urgency,
         brain.signal_level('dependency_unblocking', w.dependency_unblocking) AS dependency_unblocking,
         brain.signal_level('effort', w.effort)                               AS effort,
         brain.signal_level('confidence', w.confidence)                       AS confidence,
         brain.signal_level('charter_alignment', w.charter_alignment)         AS charter_alignment,
         l.is_cycle OR l.is_truncated                                         AS lineage_cycle,
         brain.signal_level('impact', COALESCE(w.impact, w.stakes))           AS impact,
         brain.impact_magnitude(COALESCE(w.impact, w.stakes))                 AS impact_magnitude
    FROM brain.work_item w
    CROSS JOIN LATERAL brain.work_item_lineage(w.id) l;

COMMENT ON VIEW brain.work_item_signals IS
  'The resolved signals for every work item. The two hard flags are ORed up the whole parent '
  'chain on every read. Since migration 12 the walk is bounded. Since migration 48 it also '
  'carries `impact` as a band and `impact_magnitude` as a continuous number in [0.25, 4.0], both '
  'read from COALESCE(impact, stakes) so a row that was never given an impact keeps scoring '
  'exactly as its stakes scored.';

-- ------------------------------------------------------------------ the proof, watched running
--
-- Every claim the header makes, asserted here, INCLUDING THE REFUSALS WATCHED HAPPENING. A
-- constraint asserted only by describing it is the blindness this repo has paid for seven times.

DO $$
DECLARE
  n int := 0;
  got numeric;
  lvl text;
BEGIN
  -- the three money anchors
  SELECT brain.impact_magnitude('1000') INTO got;
  IF got <> 1.0 THEN RAISE EXCEPTION 'anchor 1000 folded to % and not 1.0', got; END IF;
  n := n + 1;
  SELECT brain.impact_magnitude('10000') INTO got;
  IF got <> 2.0 THEN RAISE EXCEPTION 'anchor 10000 folded to % and not 2.0', got; END IF;
  n := n + 1;
  SELECT brain.impact_magnitude('100000') INTO got;
  IF got <> 3.0 THEN RAISE EXCEPTION 'anchor 100000 folded to % and not 3.0', got; END IF;
  n := n + 1;

  -- the money anchors agree with the bands they are anchored on
  IF brain.impact_magnitude('1000') <> brain.impact_magnitude('medium') THEN
    RAISE EXCEPTION '1000 and medium disagree'; END IF;
  n := n + 1;
  IF brain.impact_magnitude('10000') <> brain.impact_magnitude('high') THEN
    RAISE EXCEPTION '10000 and high disagree'; END IF;
  n := n + 1;
  IF brain.impact_magnitude('100000') <> brain.impact_magnitude('critical') THEN
    RAISE EXCEPTION '100000 and critical disagree'; END IF;
  n := n + 1;

  -- the clamps, at both ends
  IF brain.impact_magnitude('100000000') <> 4.0 THEN
    RAISE EXCEPTION 'the top clamp is not 4.0'; END IF;
  n := n + 1;
  IF brain.impact_magnitude('0') <> 0.25 THEN
    RAISE EXCEPTION 'zero money did not floor at 0.25'; END IF;
  n := n + 1;

  -- NEVER ZERO. This is the one that stops urgency from being annihilated.
  IF brain.impact_magnitude('none') <= 0 OR brain.impact_magnitude('0') <= 0
     OR brain.impact_magnitude('') <= 0 OR brain.impact_magnitude(NULL) <= 0 THEN
    RAISE EXCEPTION 'a magnitude reached zero, which annihilates the product'; END IF;
  n := n + 1;

  -- unset is conservative, and it is the same conservative stakes has always been
  IF brain.impact_magnitude(NULL) <> brain.impact_magnitude('high') THEN
    RAISE EXCEPTION 'unset impact is not conservative'; END IF;
  n := n + 1;

  -- the fallback: a row with stakes and no impact reads its stakes
  IF brain.impact_magnitude(COALESCE(NULL, 'low')) <> brain.impact_magnitude('low') THEN
    RAISE EXCEPTION 'the stakes fallback does not fold as stakes'; END IF;
  n := n + 1;

  -- the level fold agrees with the magnitude fold on money
  SELECT brain.signal_level('impact', '100000') INTO lvl;
  IF lvl <> 'high' THEN RAISE EXCEPTION '100000 read as level % and not high', lvl; END IF;
  n := n + 1;
  SELECT brain.signal_level('impact', '1000') INTO lvl;
  IF lvl <> 'medium' THEN RAISE EXCEPTION '1000 read as level % and not medium', lvl; END IF;
  n := n + 1;

  -- THE REFUSAL, WATCHED HAPPENING. `impact=3` is the ambiguous case and must not be storable.
  IF brain.signal_ok('impact', '3') THEN
    RAISE EXCEPTION 'impact=3 was accepted, and it is the one value this gate exists to refuse';
  END IF;
  n := n + 1;
  IF brain.signal_ok('impact', '99.9') THEN
    RAISE EXCEPTION 'impact=99.9 was accepted and is inside the refused band'; END IF;
  n := n + 1;
  IF NOT brain.signal_ok('impact', '100') THEN
    RAISE EXCEPTION 'impact=100 was refused and is the first accepted amount'; END IF;
  n := n + 1;
  IF NOT brain.signal_ok('impact', '0') THEN
    RAISE EXCEPTION 'impact=0 was refused and means none'; END IF;
  n := n + 1;

  -- the widening did not loosen any OTHER field. stakes=5 is still refused, as migration 6 says.
  IF brain.signal_ok('stakes', '5') THEN
    RAISE EXCEPTION 'stakes=5 became acceptable, so this migration loosened a gate it does not own';
  END IF;
  n := n + 1;
  IF brain.signal_ok('confidence', 'nan') OR brain.signal_ok('dependency_unblocking', 'inf') THEN
    RAISE EXCEPTION 'nan or inf became acceptable'; END IF;
  n := n + 1;

  -- the view carries both new columns and nothing lost its place
  PERFORM 1 FROM information_schema.columns
    WHERE table_schema = 'brain' AND table_name = 'work_item_signals'
      AND column_name IN ('impact', 'impact_magnitude') HAVING count(*) = 2;
  IF NOT FOUND THEN RAISE EXCEPTION 'the view is missing impact or impact_magnitude'; END IF;
  n := n + 1;

  RAISE NOTICE 'migration 48: % of 20 checks passed, including four refusals watched happening', n;
  IF n <> 20 THEN RAISE EXCEPTION 'migration 48: expected 20 checks, ran %', n; END IF;
END $$;

-- THE COLUMN CHECK, WATCHED REFUSING, against the real table rather than against the function.
-- A CHECK constraint that was never seen refusing a row is a constraint nobody has evidence for.
--
-- AND THE PROBE PUTS THE ID SEQUENCE BACK, which the first version of this file did not and which
-- is worth the six extra lines. `brain.work_item.id` defaults to `nextval('brain.item_id_seq')`,
-- and a sequence does not roll back: the failed INSERT still burns a number, so a probe that
-- proves a constraint would ALSO punch a permanent hole in his task numbering, one per store this
-- file is applied to. This repo already carries a row about a hole in the migration ledger
-- (`0408`) and does not need a second kind of hole created by the thing checking the first.
-- Measured on scratch `brain_impact`: without the restore, the next posted task was 0002 and no
-- 0001 existed.
-- `is_called` IS RESTORED AS WELL AS `last_value`, AND THE FIRST VERSION OF THIS PROBE GOT THAT
-- WRONG. A fresh sequence sits at last_value 1 with is_called FALSE, meaning "1 has not been
-- handed out yet". Restoring only the number, with `setval(seq, 1, true)`, says "1 HAS been handed
-- out", and the first task on a brand new store then comes back 0002 with no 0001 anywhere.
-- Measured exactly that way on scratch `brain_impact2` before this line existed: the first post
-- returned `0002`. It would have been invisible on his live store, where the sequence is long past
-- 1 and is_called is already true, and it would have been wrong on every demo store and every
-- scratch database built from these files afterwards.
DO $$
DECLARE ok boolean := false; before bigint; was_called boolean;
BEGIN
  SELECT last_value, is_called INTO before, was_called FROM brain.item_id_seq;
  BEGIN
    INSERT INTO brain.work_item (title, lane, state, priority, posted_by, impact)
      VALUES ('migration 48 probe, never committed', 'engine', 'inbox', 3, 'migration', '3');
    RAISE EXCEPTION 'the work_item_impact_check accepted impact=3';
  EXCEPTION WHEN check_violation THEN
    ok := true;
  END;
  IF NOT ok THEN RAISE EXCEPTION 'the refusal did not arrive as a check_violation'; END IF;
  PERFORM setval('brain.item_id_seq', before, was_called);
  IF (SELECT last_value FROM brain.item_id_seq) <> before
     OR (SELECT is_called FROM brain.item_id_seq) <> was_called THEN
    RAISE EXCEPTION 'the probe did not put brain.item_id_seq back'; END IF;
  RAISE NOTICE 'migration 48: work_item_impact_check watched refusing impact=3 on the real table, '
               'and item_id_seq restored to (last_value %, is_called %)', before, was_called;
END $$;

INSERT INTO brain.schema_migration (version, name) VALUES (48, '0048_impact_is_continuous')
  ON CONFLICT (version) DO NOTHING;

COMMIT;
