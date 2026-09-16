-- The rollback migration 0072 states in its header, as a runnable file. Scratch only:
--     ENGINE_SCRATCH_DB=<your scratch> engine/bin/scratch-db.sh psql -v ON_ERROR_STOP=1 -f - \
--         < migrations/tests/rollback-0072.sql
-- LOSS, STATED: every last_human / last_human_at stamp on the sixteen tables, which cannot be
-- re-derived from anything else. Two-way in DDL, one-way in data. Prints how many stamps it is
-- about to lose BEFORE it loses them, so a reader sees the number rather than the word.
\set ON_ERROR_STOP on
DO $$
DECLARE t text; c bigint; total bigint := 0; tables int := 0;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'budget_incident', 'budget_policy', 'disposition', 'effect_reconciliation',
    'image_attachment', 'objective', 'question', 'queue_bump', 'queue_calibration',
    'queue_defer', 'queue_item', 'receipt', 'runtime_flag', 'thread', 'time_entry',
    'voice_capture'] LOOP
    IF to_regclass('brain.' || t) IS NULL THEN CONTINUE; END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                    WHERE table_schema = 'brain' AND table_name = t AND column_name = 'last_human') THEN
      CONTINUE;
    END IF;
    EXECUTE format('SELECT count(*) FROM brain.%I WHERE last_human IS NOT NULL', t) INTO c;
    total := total + c; tables := tables + 1;
  END LOOP;
  RAISE NOTICE 'rollback 72: about to drop % stamps across % tables. They cannot be re-derived.', total, tables;
END $$;
BEGIN;
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'budget_incident', 'budget_policy', 'disposition', 'effect_reconciliation',
    'image_attachment', 'objective', 'question', 'queue_bump', 'queue_calibration',
    'queue_defer', 'queue_item', 'receipt', 'runtime_flag', 'thread', 'time_entry',
    'voice_capture'] LOOP
    IF to_regclass('brain.' || t) IS NULL THEN CONTINUE; END IF;
    EXECUTE format('DROP TRIGGER IF EXISTS last_human_is_the_login ON brain.%I', t);
    EXECUTE format('ALTER TABLE brain.%I DROP COLUMN IF EXISTS last_human_at', t);
    EXECUTE format('ALTER TABLE brain.%I DROP COLUMN IF EXISTS last_human', t);
  END LOOP;
END $$;
DROP FUNCTION IF EXISTS brain.last_human_is_the_login();
DELETE FROM brain.schema_migration WHERE version = 72;
COMMIT;
SELECT 'rolled back 72; ledger now ' || max(version) AS verdict FROM brain.schema_migration;
