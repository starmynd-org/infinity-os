-- The invented week's dates. ALPHA-SPRINT-1, D-ALPHA-TESTDATA-1, companion to seeds/alpha-example.py.
--
-- The verbs stamp now(), so a freshly seeded store is one minute old and every "Updated" and
-- "Freshness" cell reads the same. This spreads the week over the last seven days, one offset per
-- work item, applied to the item AND its own thread rows so their order stays true.
--
-- SCRATCH ONLY. Run as the disposable container's superuser against the scratch database; it
-- refuses a database named brain and a database without "scratch" in its name.

\set ON_ERROR_STOP on
BEGIN;
DO $$
BEGIN
  IF current_database() = 'brain' OR position('scratch' in current_database()) = 0 THEN
    RAISE EXCEPTION 'alpha-example-dates: refusing database %', current_database();
  END IF;
END $$;

CREATE TEMP TABLE a4_offsets AS
SELECT id, make_interval(hours => (abs(hashtext(id)) % 160) + 2) AS back
  FROM brain.work_item;

UPDATE brain.work_item w SET created = w.created - o.back
  FROM a4_offsets o WHERE o.id = w.id;
UPDATE brain.work_item w SET claimed_at = w.claimed_at - o.back
  FROM a4_offsets o WHERE o.id = w.id AND w.claimed_at IS NOT NULL;
UPDATE brain.work_item w SET finished_at = w.finished_at - o.back
  FROM a4_offsets o WHERE o.id = w.id AND w.finished_at IS NOT NULL;
UPDATE brain.thread t SET ts = t.ts - o.back
  FROM a4_offsets o WHERE o.id = t.work_item_id;
UPDATE brain.objective SET taken_in_at = taken_in_at - make_interval(hours => (id::int * 29) % 150 + 3);
UPDATE brain.recommendation SET created_at = created_at - make_interval(hours => (id::int * 17) % 120 + 1);

SELECT 'dated work_item=' || count(*) || ' oldest=' || min(created) || ' newest=' || max(created)
  FROM brain.work_item;
COMMIT;
