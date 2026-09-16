-- 2026-09-09-IOS-term-5. Scratch-only rollback, no row data loss.
\set ON_ERROR_STOP on
BEGIN;
DO $$ BEGIN
  IF current_database() IN ('brain','brain_scratch') OR strpos(current_database(),'scratch') = 0 THEN
    RAISE EXCEPTION 'rollback 75 is approved for a named private scratch store only';
  END IF;
END $$;
DROP INDEX IF EXISTS brain.work_item_done_unaccepted_idx;
DELETE FROM brain.schema_migration WHERE version=75;
COMMIT;
