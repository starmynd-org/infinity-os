-- migration 6: the canon's NUMERIC signal vocabulary, which migration 1 refused
--
-- Filed by D4 to D-CROSSTALK slot 7 with the measurement, and fixed here rather than at the CLI.
-- D4's judgement, which this migration preserves rather than overturns: the CLI must NOT fold
-- `5` to `high` behind the caller's back, because storing a level where the caller wrote a count
-- discards the count and turns a real gap into a green suite. So the store is widened to accept
-- what the source accepts, and the CLI's temporary refusal is removed, not replaced by a coercion.
--
-- WHAT WAS WRONG, measured on `brain` and `brain_scratch` before this file was applied:
--
--   signal_ok('dependency_unblocking','5')    false   -> the INSERT was refused by a CHECK
--   signal_level('dependency_unblocking','5')  low    -> bin/swarm folds this 'high'
--
--   All six of D4's measured cases (du = 0, 2, 9; confidence = 0.1, 0.5, 0.95) were refused,
--   6 of 6. Four of the six also folded differently. The other two agreed by coincidence: the
--   conservative default happens to equal what the count and the threshold would have produced.
--
-- WHY IT IS NOT COSMETIC. `dependency_unblocking` is a queue-ordering input: the claim ORDER BY
-- calls brain.signal_level() inside the locking statement, so a signal the store refuses to store
-- is an input the ranking never sees. A count of 9 items unblocked ranked as `low`.
--
-- THE SOURCE OF TRUTH IS THE CODE, NOT THE PROSE. SPEC.md says the live `bin/swarm` wins where it
-- and the canon document disagree. The thresholds below were lifted by EXECUTING
-- `bin/swarm:596 signal_level()` on each value, not by reading it:
--
--   dependency_unblocking, a COUNT:  int(float(v)) then  <=0 low,  <=2 medium,  else high
--   confidence, a FLOAT 0.0-1.0:     float(v)      then  <0.4 low, <0.8 medium, else high
--
-- WIDENING IS NOT WEAKENING, and two of the source's own edges are deliberately NOT ported:
--
--   1. bin/swarm folds `confidence=nan` to 'high'. Measured, not inferred: float('nan') < 0.4 is
--      False and float('nan') < 0.8 is False, so the string `nan` falls through to the top of the
--      range. That contradicts the function's own docstring ("a garbage value must never land in
--      the middle of the range") and it would let a garbage string LIFT a task up the queue.
--   2. bin/swarm raises an uncaught OverflowError on `dependency_unblocking=inf`.
--
--   The regex below admits no `nan`, `inf` or `Infinity`, so the cast that follows it can never
--   see a non-finite value and can never raise. Both are refused on write and read conservative.
--   Filed against bin/swarm separately; this store does not inherit them.
--
-- STRICT ON WRITE, TOLERANT ON READ is unchanged. NULL and '' still mean "never assessed" and
-- still read as the conservative end. Nothing here makes unset mean zero: signal_numeric('')
-- returns NULL, not 0, so an unset `dependency_unblocking` reads `low` because it is unset and
-- not because it was folded through a count of zero.
--
-- Widening only ever accepts MORE rows than migration 1's CHECK, so no existing row can become
-- invalid and no revalidation is required. CREATE OR REPLACE keeps the ACLs granted in
-- migration 2 and keeps brain.work_item_signals working without a rebuild.

BEGIN;

SET search_path TO brain, public;

-- ---------------------------------------------------------------- the numeric reader
--
-- One reader, used by both signal_ok (strict, on write) and signal_level (tolerant, on read), so
-- the accepted set and the folded set cannot drift apart. Returns NULL for anything that is not a
-- finite decimal, which is what both callers mean by "unreadable".
--
-- The regex mirrors Python's float() literal grammar minus the two forms above: optional sign,
-- digits with an optional fraction or a bare fraction, and an optional exponent. `5.`, `.5`, `+5`
-- and `1e3` all parse in both languages. Underscore separators (`1_0`, which Python's float()
-- accepts) do not; that is a Python literal artifact rather than a vocabulary, and refusing it
-- costs a caller nothing.
--
-- The cast lives inside the THEN arm on purpose. Postgres does not evaluate a CASE arm whose
-- condition is false, including during constant folding, so `signal_numeric('garbage')` returns
-- NULL rather than raising `invalid input syntax for type double precision`. Verified against
-- garbage, nan, inf, '5x', '0x10' and '1,5' before this file was written.
--
-- double precision, not numeric, is required for parity: bin/swarm compares IEEE 754 float64
-- values against 0.4 and 0.8, and numeric would compare different values at the boundary.

CREATE OR REPLACE FUNCTION brain.signal_numeric(raw text) RETURNS double precision
  LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE
    WHEN btrim(COALESCE(raw, '')) ~ '^[+-]?([0-9]+(\.[0-9]*)?|\.[0-9]+)([eE][+-]?[0-9]+)?$'
    THEN btrim(raw)::double precision
  END
$$;

COMMENT ON FUNCTION brain.signal_numeric(text) IS
  'The canon numeric signal reader. Returns a finite double for a decimal literal and NULL for '
  'anything else, including nan and inf, which bin/swarm folds to high and crashes on '
  'respectively. NULL means unreadable, never zero.';

-- ---------------------------------------------------------------- tolerant on read

CREATE OR REPLACE FUNCTION brain.signal_level(field text, raw text) RETURNS text
  LANGUAGE sql IMMUTABLE AS $$
  SELECT COALESCE(
    CASE lower(btrim(COALESCE(raw, '')))
      WHEN 'low' THEN 'low' WHEN 'medium' THEN 'medium' WHEN 'high' THEN 'high'
      ELSE NULL END,
    CASE field || ':' || lower(btrim(COALESCE(raw, '')))
      WHEN 'stakes:none'             THEN 'low'
      WHEN 'stakes:critical'         THEN 'high'
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
    -- Migration 6. The canon's two numeric signals, folded with bin/swarm's own thresholds.
    -- `trunc()` is int(float(v)): both truncate toward zero, so -0.5 and 0.5 are both a count
    -- of 0 and both read low. Only these two fields gain a numeric branch; `stakes=5` is still
    -- refused, because the source has no numeric branch for it either.
    CASE
      WHEN field = 'dependency_unblocking' AND brain.signal_numeric(raw) IS NOT NULL THEN
        CASE WHEN trunc(brain.signal_numeric(raw)) <= 0 THEN 'low'
             WHEN trunc(brain.signal_numeric(raw)) <= 2 THEN 'medium'
             ELSE 'high' END
      WHEN field = 'confidence' AND brain.signal_numeric(raw) IS NOT NULL THEN
        CASE WHEN brain.signal_numeric(raw) < 0.4 THEN 'low'
             WHEN brain.signal_numeric(raw) < 0.8 THEN 'medium'
             ELSE 'high' END
      ELSE NULL END,
    -- Unset, or unreadable, means conservative. Stakes and reversibility are set by the
    -- operator's canon outright; the rest take the value that cannot lift a task up the queue
    -- on its own, so an unassessed task never overtakes an assessed one by saying nothing.
    CASE field
      WHEN 'stakes' THEN 'high'
      WHEN 'reversibility' THEN 'low'   -- low reversibility IS costly-or-irreversible here
      WHEN 'effort' THEN 'high'
      ELSE 'low' END)
$$;

COMMENT ON FUNCTION brain.signal_level(text, text) IS
  'Tolerant on read. Folds the canon vocabulary -- the words, and since migration 6 the numeric '
  'forms of dependency_unblocking (a count) and confidence (0.0 to 1.0) -- onto low/medium/high, '
  'and returns the conservative value for anything unset or unreadable. Never raises, never '
  'returns medium for an unknown. Agrees with swarm_engine.signals.signal_level by test.';

-- ---------------------------------------------------------------- strict on write

CREATE OR REPLACE FUNCTION brain.signal_ok(field text, raw text) RETURNS boolean
  LANGUAGE sql IMMUTABLE AS $$
  SELECT raw IS NULL OR btrim(raw) = ''
      OR lower(btrim(raw)) IN ('low','medium','high')
      OR (field = 'stakes'         AND lower(btrim(raw)) IN ('none','critical'))
      OR (field = 'reversibility'  AND lower(btrim(raw)) IN ('reversible','costly','irreversible'))
      OR (field = 'urgency'        AND lower(btrim(raw)) IN ('none','soon','deadline','decaying'))
      OR (field = 'effort'         AND lower(btrim(raw)) IN ('small','large'))
      -- Migration 6. Exactly the set signal_level can now fold, and no wider: if a value is
      -- accepted here it has a defined level, and if it has a defined level it is accepted here.
      -- Keeping these two conditions the same expression is what stops the write gate and the
      -- ordering fold from drifting the way migration 1's did.
      OR (field IN ('dependency_unblocking','confidence')
          AND brain.signal_numeric(raw) IS NOT NULL)
$$;

COMMENT ON FUNCTION brain.signal_ok(text, text) IS
  'Strict on write. NULL and '''' mean never assessed and are permitted. Since migration 6 a '
  'decimal literal is permitted for dependency_unblocking and confidence and nothing else: '
  'stakes=5, confidence=nan and dependency_unblocking=inf are all still refused.';

GRANT EXECUTE ON FUNCTION brain.signal_numeric(text) TO brain_runtime, brain_subscriber;

INSERT INTO brain.schema_migration (version, name) VALUES (6, '0006_signal_numeric_vocabulary')
  ON CONFLICT (version) DO NOTHING;

COMMIT;
