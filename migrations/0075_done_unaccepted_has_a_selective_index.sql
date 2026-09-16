-- migration 75: the done-but-unaccepted arm stays selective as accepted history grows.
-- Written by 2026-09-09-IOS-term-5 for term-4's INDEX-REQUESTS.md.
-- Version 75 follows measured ledger 74 on ios_term5_scratch and file ledger through 74.
--
-- Exact predicate: SELECT id,state FROM brain.work_item
--   WHERE state='done' AND accepted_at IS NULL.
-- Source 465ce06, ios_term5_scratch ledger 74, synthetic accepted-history probe:
-- 100 matches of 10002 total rows; three-run median milliseconds before/add/remove
-- 1.678 / 0.800 / 1.809. Shared hit blocks 726 / 301 / 726. The planner chose this
-- partial index only while present, and every returned row/value digest stayed equal.
-- Full plans and invocation: outputs/2026-09-09-IOS-term-5/selective-index-plans.json.
-- This is SQL evidence, not a queue_open or rendered latency claim. It does not
-- address queue_open JIT compilation. The second proposed human-open index is not
-- added: work_item_state_idx already serves that arm and the measured difference
-- did not establish a latency benefit at the larger selective load.
--
-- TWO-WAY, NO ROW DATA LOSS: rollback-0075.sql drops only this index and ledger row.
-- The proof executes rollback and reapply and plants a same-name wrong predicate
-- to ensure IF NOT EXISTS cannot silently accept the wrong index shape.
\set ON_ERROR_STOP on
BEGIN;

CREATE INDEX IF NOT EXISTS work_item_done_unaccepted_idx ON brain.work_item (state)
  WHERE state = 'done' AND accepted_at IS NULL;

DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_index i
    WHERE i.indexrelid = 'brain.work_item_done_unaccepted_idx'::regclass
      AND i.indrelid = 'brain.work_item'::regclass AND i.indisvalid AND i.indisready
      AND NOT i.indisunique AND i.indnkeyatts = 1 AND i.indnatts = 1
      AND pg_get_indexdef(i.indexrelid,1,true) = 'state'
      AND pg_get_expr(i.indpred,i.indrelid) = '((state = ''done''::text) AND (accepted_at IS NULL))'
  ) THEN
    RAISE EXCEPTION 'migration 75: same-name index has the wrong shape';
  END IF;
  RAISE NOTICE 'migration 75: 1 of 1 index shape checks passed';
END $$;
INSERT INTO brain.schema_migration (version, name)
  VALUES (75,'0075_done_unaccepted_has_a_selective_index') ON CONFLICT(version) DO NOTHING;
COMMIT;
