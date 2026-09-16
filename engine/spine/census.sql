-- L01-SPINE-01 census: what this store can do, read from the ledger.
--
--     ENGINE_SCRATCH_DB=<a name containing scratch> ./engine/bin/scratch-db.sh psql -f - < engine/spine/census.sql
--
-- This is the path that works on this host. `preflight.py` is the programmatic equivalent and needs
-- credentials of its own; when it cannot connect it reports `connected no` and every capability NO,
-- which is correct rather than convenient: cannot-say is not a pass.
--
-- It reads only the catalogue and the migration ledger, and writes nothing at all.
--
-- ---------------------------------------------------------------- TWO CORRECTIONS ARE BAKED IN HERE
--
-- 1. THE NAMES WERE WRONG. The first version asked for `authority`, `lease`, `effect_reservation`
--    and `execution_receipt`, taken from the Admiral's packet prose because the schema files were
--    what was missing. When migrations 57-64 were merged, four of five were wrong.
--
-- 2. FIXING THE NAMES MADE IT WORSE, and Terminal 04 caught it before it reached a receipt. Reading
--    the merged tree I found `brain.receipt`, ticked the box, and reported R02's receipt present.
--    **`brain.receipt` is a different subsystem** -- the brain's general action-receipt table. The
--    execution receipt is the SETTLED `effect_attempt` ROW ITSELF. A census that finds a relation of
--    the right name and the wrong subject produces a plausible figure about something else, which
--    is worse than reporting absent, because absent is recoverable and plausible is not.
--
-- 3. AND THE VERSION NUMBER LIED TOO, on the first run of the fix. T04's advice was to read
--    `max(version)` and branch on it, which is right in a tree without holes and wrong in this one:
--    it carries 1..64 plus my own 68, so `max >= 67` read TRUE for versions never applied and every
--    stage reported ready. The question is MEMBERSHIP, not magnitude.
--
-- So the authority is whether a version is RECORDED in `brain.schema_migration`. The relation list
-- below remains as CORROBORATION only, and `receipt` is deliberately not in it.

\echo '--- the ledger is the authority, and MEMBERSHIP is the question, not magnitude ---'
--
-- `max(version) >= n` LIES IN A TREE WITH HOLES, and this store is one: it carries 1..64 plus my
-- own 68, with 65, 66 and 67 still on Terminal 04's unmerged branch. Read by magnitude, this store
-- reported "67 yes" and every stage ready, because MY migration raised the maximum past versions
-- that were never applied. That is a plausible figure about the wrong subject for the third time in
-- this file's short life, so the test is now whether each version is RECORDED.
SELECT max(version) AS schema_migration_max,
       count(*) AS versions_recorded,
       (SELECT count(*) FROM generate_series(1, max(version)) g
         WHERE NOT EXISTS (SELECT 1 FROM brain.schema_migration m WHERE m.version = g)) AS holes
FROM brain.schema_migration;

\echo '--- what each version provides, and whether this store has it ---'
SELECT c.version,
       CASE WHEN EXISTS (SELECT 1 FROM brain.schema_migration m WHERE m.version = c.version)
            THEN 'yes' ELSE 'NO' END AS present,
       c.provides
FROM (VALUES
    (57, 'R01 authority_grant and authority_revocation, with authority_in_force'),
    (58, 'R02 execution_lease, with execution_lease_live'),
    (59, 'R02 effect_attempt: an effect is reserved before it happens'),
    (61, 'R01 approval, and the roster trigger for the decider'),
    (63, 'R01 authority scoped to a workspace'),
    (64, 'R02 settle proves it held the lease'),
    (66, 'R01 approval_spendable: single-use spend via consumed_state'),
    (67, 'R02 completeness and effect_state; outcome succeeded -> success')
) AS c(version, provides)
ORDER BY c.version;

\echo '--- the spine stages, and what each is waiting on ---'
SELECT s.stage,
       s.needs,
       CASE WHEN EXISTS (SELECT 1 FROM brain.schema_migration m WHERE m.version = s.needs)
            THEN 'ready' ELSE 'WAITING ON ' || s.needs END AS state
FROM (VALUES
    ('3 approval recorded (R01)', 61),
    ('4 lease and reservation (R02)', 59),
    ('5 receipt settled', 64),
    ('5 receipt carries completeness', 67)
) AS s(stage, needs)
ORDER BY s.needs;

\echo '--- corroboration only. NOT a checklist, and `receipt` is deliberately absent ---'
SELECT r.lane,
       r.rel AS relation,
       CASE WHEN EXISTS (
           SELECT 1 FROM information_schema.tables t
            WHERE t.table_schema = 'brain' AND t.table_name = r.rel
       ) THEN 'present' ELSE 'ABSENT' END AS status
FROM (VALUES
    ('R01', 'authority_grant'),
    ('R01', 'authority_revocation'),
    ('R01', 'approval'),
    ('R02', 'execution_lease'),
    ('R02', 'effect_attempt')
) AS r(lane, rel)
ORDER BY r.lane, r.rel;
