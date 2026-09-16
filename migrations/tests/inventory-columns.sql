-- Every base table in schema brain with its columns, one line per table, for the user-scoping
-- inventory (term-5 brief section 5 item 1). Reads only. Run with
--     ENGINE_SCRATCH_DB=<your scratch> engine/bin/scratch-db.sh psql -tA -f - \
--         < migrations/tests/inventory-columns.sql
-- The first line is the denominator; the rest is one table per line.
SELECT 'TABLES ' || count(*) || ' in schema brain at ledger ' ||
       (SELECT max(version) FROM brain.schema_migration)
  FROM information_schema.tables
 WHERE table_schema = 'brain' AND table_type = 'BASE TABLE';
SELECT t.table_name || ' | ' ||
       string_agg(c.column_name || ':' || c.data_type, ', ' ORDER BY c.ordinal_position)
  FROM information_schema.tables t
  JOIN information_schema.columns c
    ON c.table_schema = t.table_schema AND c.table_name = t.table_name
 WHERE t.table_schema = 'brain' AND t.table_type = 'BASE TABLE'
 GROUP BY t.table_name
 ORDER BY t.table_name;
