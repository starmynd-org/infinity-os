-- migration 12: a parent cycle stops being a fleet-wide outage
--
-- Task 0146 (FIX 3), child of 0119. Additive and re-runnable: it redefines one view, adds three
-- functions, one trigger and one per-role setting, drops no data and edits no applied file.
-- `0001_initial.sql` stays exactly as it is; an applied migration is history, and history is
-- amended by a later migration.
--
-- ================================================================= what was broken
--
-- `brain.work_item_signals` resolves the two hard flags by ORing them up the WHOLE parent chain,
-- which is the property EF-7 rests on. Migration 1 wrote that walk as a recursive CTE with NO
-- cycle guard and NO depth cap, and the walk runs over the whole table for every row. So one
-- cyclic edge did not poison one row. It poisoned the view.
--
-- Measured on 2026-08-16 (task 0119 item 3b, and re-measured for this task on `brain_fix0146`):
--
--     store.apply('set', id='0011', key='parent', value='0012')   -- 0012.parent was already 0011
--     -- ACCEPTED, no refusal
--     SET statement_timeout='8s';
--     SELECT external, canon_touching FROM brain.work_item_signals WHERE id='0010';
--     ERROR:  canceling statement due to statement timeout      -- 0010 is unrelated to the cycle
--
-- and the consequence, because `claim` reads the view AFTER taking its row:
--
--     claim did NOT return within 45s; its backend was still `active` 44.9s after the client
--     was killed. `post --parent` did not return within 30s either.
--
-- `claim` blocks inside libpq while holding its `FOR UPDATE` row lock, so a Python SIGALRM does
-- not interrupt it and the whole fleet stops claiming until a human finds the backends by hand.
-- Three of them survived their killed clients for up to 6m57s on 2026-08-16.
--
-- ================================================================= what this migration changes
--
-- 1. A bounded ancestry walk, `brain.work_item_lineage(task)`. It stops on a revisit and it stops
--    at `brain.lineage_depth_cap()`, and it reports WHICH, because they are different facts: a
--    revisit means every reachable ancestor was still visited, so the OR over them is complete;
--    the cap means ancestors were not read at all, so the answer fails closed.
--
--    `brain.parent_cycle_path(child, new_parent)` walks the same way for the write guard, because
--    it answers a different question (which loop would this edge close) and expressing one in
--    terms of the other needed more array surgery than it saved. The two stop conditions are the
--    duplication, so a test pins them together --
--    `test_parent_cycle_guard.py::the two bounded walks agree` builds a chain past the cap and a
--    chain with a loop and asserts both functions say the same thing about both.
--
-- 2. The view FAILS CLOSED on an unresolvable chain. A row in a cycle now reads
--    `external = true, canon_touching = true, lineage_cycle = true` instead of hanging. That
--    direction is not a detail: a corrupt lineage makes the row maximally gated, so it surfaces
--    to the operator, `auto_accept_candidate` never sees it, and NOTHING is auto-accepted on the
--    strength of an ancestry the database could not read.
--
--    `claim` therefore keeps working while a poisoned row sits in the table, which is the point.
--    Refusing the read instead would have converted a hang into a claim that fails forever on the
--    same row, and the fleet would still stop.
--
-- 3. A BEFORE trigger refuses the cyclic edge at write time and names the loop. This is the
--    second gate, not the first: `set parent` and `post --parent` refuse in the verb, in words,
--    before they ever reach the table (`engine/swarm_engine/transitions.py`, `_refuse_cycle`).
--    The trigger is what catches a route neither verb owns -- a future verb, a hand-written
--    UPDATE, a lane that adds a re-parent tomorrow.
--
--    It does NOT catch a restore: `pg_restore` and `session_replication_role = replica` run with
--    triggers disabled, by design, which is exactly why point 2 exists and why the timeout in
--    point 4 exists. The write guard is the fix; the bounded read and the timeout are what make
--    the next unknown route survivable.
--
-- 4. `statement_timeout` per role, per database. There was none anywhere in the system: measured
--    `SHOW statement_timeout` = `0` for brain_runtime, brain_producer, brain_subscriber and
--    brain_owner on 2026-08-16. Nothing bounded a runaway query, which is the part that turned a
--    bad row into an outage.
--
--    `IN DATABASE current_database()`, never cluster-wide, because a role is a cluster object and
--    a scratch database must not be able to change how the live store behaves.
--
-- WHY 12. Read from `brain.schema_migration`, never from `ls`: this repo's migration files live
-- in three directories (`migrations/`, `queue/schema/`, `budget/schema/`) and the ledger is the
-- only truth. Measured at 2026-08-16T16:5xZ: 1,2,4,5,6,7,8,9 taken, 10 taken by
-- `0010_subscriber_identity` and 11 taken by `queue/schema/0009_null_branch_act_scan`, which its
-- own lane moved from 10 to 11 this afternoon after both files claimed 10. So 12 is the next free
-- version, and the guard below fails loudly rather than silently if a third lane took it first.

\set ON_ERROR_STOP on

DO $$
DECLARE taken text;
BEGIN
  SELECT name INTO taken FROM brain.schema_migration WHERE version = 12;
  IF taken IS NOT NULL AND taken <> '0012_parent_cycle_guard' THEN
    RAISE EXCEPTION 'schema version 12 is already held by %, not 0012_parent_cycle_guard. Pick '
                    'the next version by reading brain.schema_migration, never by listing a '
                    'directory -- this repo''s migration files live in three directories.',
                    taken;
  END IF;
END $$;

BEGIN;

SET search_path TO brain, public;

-- ---------------------------------------------------------------- the cap, in one place

CREATE OR REPLACE FUNCTION brain.lineage_depth_cap() RETURNS integer
  LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$ SELECT 64 $$;

COMMENT ON FUNCTION brain.lineage_depth_cap() IS
  'How far up a parent chain any walk in this schema will read. 64, against an observed maximum '
  'depth of 3 and a designed shape of task -> child -> grandchild: it is a backstop, not a '
  'budget. A chain that reaches it is reported as unresolved and fails closed, never truncated '
  'quietly -- a truncated walk would DROP ancestors, and dropping an ancestor lowers a hard flag, '
  'which is the laundering this schema exists to refuse.';

-- ---------------------------------------------------------------- the one ancestry walk

CREATE OR REPLACE FUNCTION brain.work_item_lineage(task text)
  RETURNS TABLE(has_external boolean, has_canon boolean, is_cycle boolean,
                is_truncated boolean, chain_depth integer)
  LANGUAGE sql STABLE AS $$
  WITH RECURSIVE up(id, parent, external, canon_touching, depth, path, revisit) AS (
    SELECT w.id, w.parent, w.external, w.canon_touching, 1, ARRAY[w.id], false
      FROM brain.work_item w
     WHERE w.id = task
    UNION ALL
    -- One step up. It stops for exactly two reasons, and it records which: `revisit` says this
    -- id was already on the path (a cycle, and every reachable ancestor was still read), and the
    -- depth test says the cap stopped it (ancestors were NOT read).
    SELECT p.id, p.parent, p.external, p.canon_touching, up.depth + 1, up.path || p.id,
           p.id = ANY(up.path)
      FROM up JOIN brain.work_item p ON p.id = up.parent
     WHERE up.parent IS NOT NULL
       AND NOT up.revisit
       AND up.depth < brain.lineage_depth_cap()
  )
  SELECT COALESCE(bool_or(up.external), false),
         COALESCE(bool_or(up.canon_touching), false),
         COALESCE(bool_or(up.revisit), false),
         COALESCE(bool_or(up.depth >= brain.lineage_depth_cap()
                          AND up.parent IS NOT NULL AND NOT up.revisit), false),
         COALESCE(max(up.depth), 0)
    FROM up;
$$;

COMMENT ON FUNCTION brain.work_item_lineage(text) IS
  'One task in, its resolved ancestry out: the OR of both hard flags over every ancestor read, '
  'plus whether the walk hit a cycle or the depth cap. brain.work_item_signals is built on it and '
  'the engine''s re-parent guard calls it for the values it freezes, so the read path and the '
  'write path cannot disagree about what a chain says.';

-- ---------------------------------------------------------------- the view, now bounded

-- Column names, types and order are unchanged through `charter_alignment`; `lineage_cycle` is
-- appended, which is what CREATE OR REPLACE VIEW permits and what keeps `auto_accept_candidate`
-- and every caller working untouched.
CREATE OR REPLACE VIEW brain.work_item_signals AS
  SELECT w.id,
         -- FAIL CLOSED. An ancestry the database could not resolve reads as maximally gated, so
         -- the row surfaces to the operator instead of being quietly treated as clean.
         l.has_external OR l.is_cycle OR l.is_truncated AS external,
         l.has_canon    OR l.is_cycle OR l.is_truncated AS canon_touching,
         brain.signal_level('stakes', w.stakes)                               AS stakes,
         brain.signal_level('reversibility', w.reversibility)                 AS reversibility,
         brain.signal_level('urgency', w.urgency)                             AS urgency,
         brain.signal_level('dependency_unblocking', w.dependency_unblocking) AS dependency_unblocking,
         brain.signal_level('effort', w.effort)                               AS effort,
         brain.signal_level('confidence', w.confidence)                       AS confidence,
         brain.signal_level('charter_alignment', w.charter_alignment)         AS charter_alignment,
         l.is_cycle OR l.is_truncated                                         AS lineage_cycle
    FROM brain.work_item w
    CROSS JOIN LATERAL brain.work_item_lineage(w.id) l;

COMMENT ON VIEW brain.work_item_signals IS
  'The resolved signals for every work item. The two hard flags are ORed up the whole parent '
  'chain on every read, so raising a flag on a parent raises it on every descendant '
  'retroactively (D00 rule 8, EF-7). Since migration 12 the walk is bounded: a cycle or a chain '
  'past brain.lineage_depth_cap() sets lineage_cycle and both flags read true, rather than the '
  'query never returning.';

-- ---------------------------------------------------------------- the write-time guard

CREATE OR REPLACE FUNCTION brain.parent_cycle_path(child text, new_parent text)
  RETURNS text[] LANGUAGE sql STABLE AS $$
  -- The loop that `child.parent = new_parent` would close, as a path, or NULL if it closes none.
  -- Walks UP from new_parent looking for child; bounded by the same cap and the same revisit test
  -- as brain.work_item_lineage, so a chain that is ALREADY cyclic cannot hang this either.
  WITH RECURSIVE up(id, parent, path, revisit) AS (
    SELECT w.id, w.parent, ARRAY[w.id], false
      FROM brain.work_item w
     WHERE w.id = new_parent
    UNION ALL
    SELECT p.id, p.parent, up.path || p.id, p.id = ANY(up.path)
      FROM up JOIN brain.work_item p ON p.id = up.parent
     WHERE up.parent IS NOT NULL
       AND NOT up.revisit
       AND array_length(up.path, 1) < brain.lineage_depth_cap()
  )
  SELECT ARRAY[child] || up.path
    FROM up
   WHERE up.id = child
   ORDER BY array_length(up.path, 1)
   LIMIT 1;
$$;

COMMENT ON FUNCTION brain.parent_cycle_path(text, text) IS
  'NULL when the edge child -> new_parent is safe; the loop it would close, oldest edge last, '
  'when it is not. Naming the loop is the point: a refusal that says "cycle" and not WHICH cycle '
  'leaves the operator to find it by hand, which is what the outage already cost them.';

CREATE OR REPLACE FUNCTION brain.work_item_refuse_parent_cycle() RETURNS trigger
  LANGUAGE plpgsql AS $$
DECLARE loop_path text[];
BEGIN
  IF NEW.parent IS NULL THEN
    RETURN NEW;
  END IF;
  IF NEW.parent = NEW.id THEN
    RAISE EXCEPTION 'refusing to make % its own parent', NEW.id
      USING HINT = 'A task cannot be its own ancestor: the flag walk over its chain would never '
                   'terminate, and it would not terminate for any OTHER row either.';
  END IF;
  loop_path := brain.parent_cycle_path(NEW.id, NEW.parent);
  IF loop_path IS NOT NULL THEN
    RAISE EXCEPTION 'refusing to make % the parent of %: that closes the parent cycle %',
                    NEW.parent, NEW.id, array_to_string(loop_path, ' -> ')
      USING HINT = 'Hard flags are resolved by walking this chain on every read. A loop in it '
                   'made brain.work_item_signals non-terminating for EVERY row on 2026-08-16, '
                   'not just for the rows in the loop, and `claim` hung holding its row lock.';
  END IF;
  RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS work_item_parent_acyclic ON brain.work_item;
CREATE TRIGGER work_item_parent_acyclic
  BEFORE INSERT OR UPDATE OF parent ON brain.work_item
  FOR EACH ROW WHEN (NEW.parent IS NOT NULL)
  EXECUTE FUNCTION brain.work_item_refuse_parent_cycle();

COMMENT ON FUNCTION brain.work_item_refuse_parent_cycle() IS
  'The second gate. The verbs refuse a cyclic re-parent in words before reaching the table; this '
  'catches the routes no verb owns. It cannot catch a restore, which runs with triggers '
  'disabled -- that is what the bounded view and the statement timeout are for.';

-- ---------------------------------------------------------------- a bound on everything else

-- ONE LINE PER ROLE, AND THE NUMBER IS THE ARGUMENT:
--
--   brain_runtime     15s   Every verb is one small transaction; the slowest measured (`claim`
--                           under 12-way contention) is far under a second. 15s is ~50x headroom
--                           and 27x shorter than the 6m57s a single cyclic row bought us.
--   brain_producer    15s   Event emission is a single-row append. Same shape, same bound.
--   brain_subscriber  60s   A listener legitimately scans the whole event table to catch up, so
--                           it gets four times the runtime's budget -- and still a bound, because
--                           a wedged listener is how D00 trap 3's health signal goes quiet.
--   brain_owner      30min  Migrations, backfills and the retention sweep ARE long, and they are
--                           attended. The bound exists only so a runaway DDL cannot sit forever;
--                           an operator who needs more says SET statement_timeout in the session.
--
-- The same four numbers are in `store/session.py` (`STATEMENT_TIMEOUT`), applied to the
-- connection itself, because this ALTER ROLE only covers databases this migration has been
-- applied to and only covers roles that exist today. Belt and braces on purpose: the client-side
-- one protects an unmigrated database, this one protects a psql session that never opens Python.
DO $$
DECLARE
  db text := current_database();
  r record;
BEGIN
  FOR r IN SELECT * FROM (VALUES ('brain_runtime',    '15s'),
                                 ('brain_producer',   '15s'),
                                 ('brain_subscriber', '60s'),
                                 ('brain_owner',      '30min')) AS v(role_name, timeout)
  LOOP
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r.role_name) THEN
      EXECUTE format('ALTER ROLE %I IN DATABASE %I SET statement_timeout = %L',
                     r.role_name, db, r.timeout);
    END IF;
  END LOOP;
  -- Every provisioned subscriber login from migration 10 gets the subscriber's bound. A listener
  -- provisioned LATER gets it from store/session.py, which is why that half is not optional.
  FOR r IN SELECT rolname FROM pg_roles WHERE rolname LIKE 'brain\_sub\_%'
  LOOP
    EXECUTE format('ALTER ROLE %I IN DATABASE %I SET statement_timeout = %L',
                   r.rolname, db, '60s');
  END LOOP;
END $$;

-- ---------------------------------------------------------------- say what is already broken

DO $$
DECLARE poisoned text;
BEGIN
  SELECT string_agg(id, ', ' ORDER BY id) INTO poisoned
    FROM brain.work_item_signals WHERE lineage_cycle;
  IF poisoned IS NOT NULL THEN
    RAISE NOTICE 'parent cycles already in this database: %. They now read external=true, '
                 'canon_touching=true and lineage_cycle=true instead of hanging. Repair each one '
                 'with `swarm set <id> parent <a-real-ancestor>` or by clearing the parent.',
                 poisoned;
  END IF;
END $$;

GRANT EXECUTE ON FUNCTION brain.lineage_depth_cap()               TO brain_runtime, brain_owner;
GRANT EXECUTE ON FUNCTION brain.work_item_lineage(text)           TO brain_runtime, brain_owner;
GRANT EXECUTE ON FUNCTION brain.parent_cycle_path(text, text)     TO brain_runtime, brain_owner;

INSERT INTO brain.schema_migration (version, name)
VALUES (12, '0012_parent_cycle_guard') ON CONFLICT (version) DO NOTHING;

COMMIT;
