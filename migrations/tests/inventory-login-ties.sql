-- Which brain tables have a trigger whose function body asks brain.current_human()? That is the
-- only kind of attribution this store calls the DATABASE'S ANSWER (migrations 20, 32, 36, 37,
-- 40, 68). A *_by column with no such trigger is a caller string, however it is named.
-- Reads only. Run with
--     ENGINE_SCRATCH_DB=<your scratch> engine/bin/scratch-db.sh psql -tA -f - \
--         < migrations/tests/inventory-login-ties.sql
SELECT 'LOGIN-TIED TRIGGERS ' || count(*) || ' over ' || count(DISTINCT c.relname) || ' tables'
  FROM pg_trigger t
  JOIN pg_class c ON c.oid = t.tgrelid
  JOIN pg_namespace n ON n.oid = c.relnamespace
  JOIN pg_proc p ON p.oid = t.tgfoid
 WHERE n.nspname = 'brain' AND NOT t.tgisinternal
   AND p.prosrc ILIKE '%current_human()%';
SELECT c.relname || ' | ' || string_agg(t.tgname, ', ' ORDER BY t.tgname)
  FROM pg_trigger t
  JOIN pg_class c ON c.oid = t.tgrelid
  JOIN pg_namespace n ON n.oid = c.relnamespace
  JOIN pg_proc p ON p.oid = t.tgfoid
 WHERE n.nspname = 'brain' AND NOT t.tgisinternal
   AND p.prosrc ILIKE '%current_human()%'
 GROUP BY c.relname ORDER BY c.relname;
SELECT 'ALL NON-INTERNAL TRIGGERS ' || count(*) || ' over ' || count(DISTINCT c.relname) || ' tables'
  FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
  JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = 'brain' AND NOT t.tgisinternal;
