-- migration 56: a delegation level attaches to the AGENT as a default and to the WORK ITEM as an
-- override, and the two hard flags still surface at every level including seven.
--
-- Row `0439`, his ask 8, second half. Written 2026-09-01 by S4 on the sprint map's governance lane.
--
-- ------------------------------------------------------------------ what was actually missing
--
-- The ladder was built on 2026-08-31 and WIRED TO NOTHING. Measured 2026-09-01, not taken on
-- trust: `engine/swarm_engine/delegation.py` is imported by exactly ONE file in the repo, its own
-- test `engine/tests/test_the_ladder.py`. No migration declared a delegation column. No row carried
-- a level. It was a correct subsystem that cost real work and did nothing.
--
--     brain.agent                11 rows, no delegation column
--     brain.work_item           375 rows, no delegation column
--
-- ------------------------------------------------------------------ BOTH FIELDS, NOT EITHER
--
-- His ruling: a level attaches to the agent as a DEFAULT, with a per-work-item OVERRIDE. Both.
-- The two carry different meanings that one field could not:
--
--   * the agent default is a standing statement about a RELATIONSHIP: how much leeway this agent
--     has in general;
--   * the item override is a statement about ONE PIECE OF WORK: this particular thing is different.
--
-- NULL IS NOT A LEVEL AND NEVER FOLDS TO ONE. It means NOBODY HAS SAID, which is a real and common
-- state and must stay distinguishable from a deliberate level 1. That is why both columns are
-- nullable and neither carries a DEFAULT: a column default would make every one of the 386 existing
-- rows claim a level nobody granted.
--
-- ------------------------------------------------------------------ WHICH AGENT'S DEFAULT
--
-- THE ACTING AGENT -- `work_item.claimed_by` -- and never the poster or the assignee. If a level-6
-- agent posts work that a level-2 agent claims and executes, the act is the CLAIMER'S act and the
-- claimer's leeway governs. The alternative lets an agent launder leeway to another simply by
-- posting the work, which is the same one-hop laundering D00 rule 8 and EF-7 already forbid for
-- hard flags. Assignment is an intention; the claim is the fact.
--
-- ------------------------------------------------------------------ RESOLVED ON READ, NEVER STAMPED
--
-- `brain.work_item_delegation` is a VIEW and that is the point. A view computes at the instant it
-- is read, so a level CANNOT be stamped at claim time and reused. This matters in one direction
-- above all: when the operator LOWERS an agent's level because it has just misbehaved, that
-- revocation must bite the item the agent is holding RIGHT NOW. A stamped level would let it finish
-- the current item at the old leeway, which is the exact moment the revocation is most needed.
--
-- The same argument the hard flags already won: they are resolved on read up the whole parent chain
-- rather than stamped at post time, because stamping holds for exactly one hop.
--
-- ------------------------------------------------------------------ A LEVEL DOES NOT INHERIT
--
-- Deliberate asymmetry with the hard flags, which sit two fields away and inherit aggressively. A
-- hard flag inherits because it is a property of THE WORK'S CONSEQUENCES. A delegation level is a
-- property of THE RELATIONSHIP with the acting agent. Inheriting a level down the chain would grant
-- a child agent leeway nobody granted it, invisibly. Inheritance of a level can only ever GRANT
-- leeway the acting agent was not given, so the conservative choice is none.
--
-- Recorded in `outputs/2026-09-01-sprints/S4/OPEN-QUESTIONS.md` as the one decision here most worth
-- his overruling, because it is cheap to reverse in that direction and impossible in the other.
--
-- ------------------------------------------------------------------ WHY NEW OBJECTS, NOT AN EDIT
--
-- `brain.work_item_signals` is NOT recreated by this file, and neither is `queue_open`. Both are
-- read by lanes running in parallel with this one today, and `CREATE OR REPLACE VIEW` permits only
-- APPENDED columns, so a recreation is a cross-lane change for a column those views do not need.
-- Migration 52 set the precedent one file ago: it added `kind` to `work_item` and touched
-- `work_item_signals` not at all, creating `operator_queue` instead. This file does the same.
--
-- Adding a column to `brain.work_item` does not invalidate a dependent view: Postgres stores a view
-- as a rewrite rule with its column list already expanded. Verified on this store 2026-09-01 that
-- none of the six direct dependents uses star-expansion, so none can silently change shape.
--
-- ------------------------------------------------------------------ WHAT THIS DOES NOT CLOSE
--
--   * Nothing is backfilled. All 11 agents and all 375 work items keep a NULL level, which reads as
--     level 1, `tell`. Which agents have earned leeway is HIS answer and not a pattern match.
--   * `charter_alignment` coming out of the score is a separate ruling and is BLOCKED on ownership:
--     it is a scoring term in five independent implementations and S4 owns one. See
--     `outputs/2026-09-01-sprints/S4/BLOCKED-charter-alignment.md`.
--   * This file gives a level somewhere to live and a view that reads it. It does not make any
--     agent ACT on one; the runner consuming `surfaces` is downstream of this.
--
-- ROLLBACK, and it has been executed on the rehearsal store rather than described:
--
--     DROP VIEW IF EXISTS brain.work_item_delegation;
--     DROP FUNCTION IF EXISTS brain.delegation_resolve(smallint, smallint);
--     DROP FUNCTION IF EXISTS brain.delegation_surfacing(smallint, boolean, boolean);
--     ALTER TABLE brain.work_item DROP CONSTRAINT IF EXISTS work_item_delegation_level_check;
--     ALTER TABLE brain.agent     DROP CONSTRAINT IF EXISTS agent_delegation_level_check;
--     ALTER TABLE brain.work_item DROP COLUMN IF EXISTS delegation_level;
--     ALTER TABLE brain.agent     DROP COLUMN IF EXISTS delegation_level;
--     DELETE FROM brain.schema_migration WHERE version = 56;
--
-- APPLY AS: the bootstrap superuser (`postgres`), against `brain`, via
-- `store/bin/apply-migration.sh`. NOT as `brain_owner`: measured 2026-08-31, all 40 tables in the
-- `brain` schema are owned by `postgres`, and applying as `brain_owner` dies mid-file on
-- `must be owner of table work_item`.
--
-- LEDGER 56 WAS ANNOUNCED BEFORE THIS FILE WAS WRITTEN, in
-- `outputs/2026-09-01-sprints/S4/LEDGER-CLAIM.md`, per the sprint map's shared-path rule. The
-- filename prefix is NOT the version and never has been: `queue/schema/0017_queue_open_carries_
-- impact.sql` declares version 49 and is the fourth highest applied row.

\set ON_ERROR_STOP on

DO $$
DECLARE taken text;
BEGIN
  SELECT name INTO taken FROM brain.schema_migration WHERE version = 56;
  IF taken IS NOT NULL AND taken <> '0056_a_level_attaches_to_the_work' THEN
    RAISE EXCEPTION 'ledger version 56 is already held by %', taken
      USING HINT = 'Renumber this file to the next free version and re-run.';
  END IF;
END $$;

BEGIN;

-- ------------------------------------------------------------------ the two columns

ALTER TABLE brain.agent
  ADD COLUMN IF NOT EXISTS delegation_level smallint;

ALTER TABLE brain.agent DROP CONSTRAINT IF EXISTS agent_delegation_level_check;
ALTER TABLE brain.agent ADD CONSTRAINT agent_delegation_level_check
  CHECK (delegation_level IS NULL OR delegation_level BETWEEN 1 AND 7);

COMMENT ON COLUMN brain.agent.delegation_level IS
  'This agent''s STANDING delegation level, 1 to 7 on the ladder in his words: tell, sell, '
  'consult, agree, advise, inquire, delegate. NULL means nobody has granted this agent any '
  'leeway, which reads as level 1 (tell) and is NOT the same fact as a deliberate 1. Lowering '
  'this takes effect on the item the agent is holding right now, because the level is resolved '
  'on read and never stamped at claim.';

ALTER TABLE brain.work_item
  ADD COLUMN IF NOT EXISTS delegation_level smallint;

ALTER TABLE brain.work_item DROP CONSTRAINT IF EXISTS work_item_delegation_level_check;
ALTER TABLE brain.work_item ADD CONSTRAINT work_item_delegation_level_check
  CHECK (delegation_level IS NULL OR delegation_level BETWEEN 1 AND 7);

COMMENT ON COLUMN brain.work_item.delegation_level IS
  'A per-item OVERRIDE of the acting agent''s standing level, 1 to 7. NULL means no override and '
  'the item takes the ACTING agent''s default (work_item.claimed_by), never the poster''s and '
  'never the assignee''s: otherwise an agent launders leeway to another by posting the work. '
  'An override does NOT inherit to child rows.';

-- ------------------------------------------------------------------ the resolution rule, in SQL
--
-- THE SPECIFICATION IS `outputs/2026-09-01-sprints/S4/RESOLUTION-RULE.md`, WRITTEN BEFORE EITHER
-- COLUMN EXISTED. The Python half is `delegation.resolve_level()`, and the two are held equal by
-- `test_the_ladder.py`, not by reading -- the same discipline `test_signal_parity` applies to
-- `brain.signal_level`, and for the same reason: two implementations of one fold drift, and then a
-- row is explained one way and ordered another.

CREATE OR REPLACE FUNCTION brain.delegation_resolve(item_override smallint, agent_default smallint)
RETURNS smallint LANGUAGE sql IMMUTABLE AS $$
  SELECT COALESCE(item_override, agent_default, 1::smallint);
$$;

COMMENT ON FUNCTION brain.delegation_resolve(smallint, smallint) IS
  'Which level applies: the item override, else the ACTING agent''s default, else 1 (tell). '
  'Specificity wins and the fallback is the conservative end, not the middle: an agent nobody has '
  'graded must not act on his behalf because nothing said it could not.';

CREATE OR REPLACE FUNCTION brain.delegation_source(item_override smallint, agent_default smallint)
RETURNS text LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE WHEN item_override IS NOT NULL THEN 'item'
              WHEN agent_default IS NOT NULL THEN 'agent'
              ELSE 'default' END;
$$;

COMMENT ON FUNCTION brain.delegation_source(smallint, smallint) IS
  'WHERE the resolved level came from. Not decoration: a surface that says "this acted without '
  'asking you" and cannot say whether that was the agent''s standing leeway or a per-item '
  'override cannot be argued with, and the point of the ladder is that the relationship is '
  'legible.';

-- THE HARD CONSTRAINT, AND THE REASON IT IS A SEPARATE FUNCTION.
--
-- `delegation_resolve` is NOT GIVEN THE FLAGS and therefore cannot lower one. It answers "how much
-- leeway" and hands the answer here, where the gates are applied on every path. A resolver that
-- took the flags "so it could optimise" would be the entire defect. It does not take them.
--
-- His own words on row 0439: a delegation level cannot lower those gates, and WHERE THE LADDER
-- APPEARS TO SAY OTHERWISE IT IS THE LADDER THAT HAS TO BEND.

CREATE OR REPLACE FUNCTION brain.delegation_surfacing(
  level smallint, external boolean, canon_touching boolean)
RETURNS text LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE
    WHEN COALESCE(external, false) OR COALESCE(canon_touching, false) THEN 'now'
    WHEN level >= 7 THEN 'audit'
    WHEN level  = 6 THEN 'later'
    ELSE 'now'
  END;
$$;

COMMENT ON FUNCTION brain.delegation_surfacing(smallint, boolean, boolean) IS
  'WHEN he finds out: now, later (level 6 inquire, passive), or audit (level 7 delegate, silent). '
  'The two hard flags win at EVERY level including 7, so an agent at level 7 about to send, '
  'deploy, spend, publish or touch canon is at level 1 for that act. The flag branch is FIRST and '
  'unconditional, which is what makes that structural rather than a convention.';

-- ------------------------------------------------------------------ the view a surface reads

CREATE OR REPLACE VIEW brain.work_item_delegation AS
  SELECT w.id,
         w.title,
         w.state,
         w.claimed_by,
         w.actor_type,
         w.delegation_level                             AS item_override,
         a.delegation_level                             AS agent_default,
         brain.delegation_resolve(w.delegation_level, a.delegation_level)  AS level,
         brain.delegation_source(w.delegation_level, a.delegation_level)   AS source,
         w.external,
         w.canon_touching,
         -- THE LADDER DOES NOT GOVERN HIS OWN WORK. `actor_type='human'` is the operator's own
         -- work entering his own queue, and a human's act is not delegated. Answering "level 1"
         -- for him would be a category error a surface would render as "waiting on approval" for
         -- work he is doing himself.
         (COALESCE(w.actor_type, '') <> 'human')        AS governed,
         CASE WHEN COALESCE(w.actor_type, '') = 'human' THEN NULL
              ELSE brain.delegation_surfacing(
                     brain.delegation_resolve(w.delegation_level, a.delegation_level),
                     w.external, w.canon_touching) END  AS surfaces,
         -- WHICH GATE DID IT, so a row that surfaced traces to the reason rather than to a mood.
         NULLIF(concat_ws(',', CASE WHEN w.external THEN 'external' END,
                               CASE WHEN w.canon_touching THEN 'canon_touching' END), '')
                                                        AS gate
    FROM brain.work_item w
    LEFT JOIN brain.agent a ON a.name = NULLIF(btrim(w.claimed_by), '');

COMMENT ON VIEW brain.work_item_delegation IS
  'Every work item with its delegation level RESOLVED ON READ from the item override and the '
  'ACTING agent''s default, plus whether it surfaces and which gate did it. A view rather than a '
  'column on purpose: a view computes at the instant it is read, so a level cannot be stamped at '
  'claim and reused, and lowering an agent''s level bites the item it is holding right now. '
  'A LEFT JOIN so an item claimed by nobody, or by an agent with no row, still appears and reads '
  'as level 1: an unresolvable agent is not a licence.';

GRANT SELECT ON brain.work_item_delegation TO brain_runtime, brain_subscriber;

-- ------------------------------------------------------------------ the proof, watched running

DO $$
DECLARE n int := 0; probe text; got text; lvl smallint; escaped int;
BEGIN
  -- 1. NULL ON BOTH SIDES READS AS 1, AND IS DISTINGUISHABLE FROM A DELIBERATE 1 BY `source`.
  IF brain.delegation_resolve(NULL, NULL) <> 1
     OR brain.delegation_source(NULL, NULL) <> 'default' THEN
    RAISE EXCEPTION 'an ungraded relationship did not fall to level 1 / source default'; END IF;
  IF brain.delegation_source(1::smallint, NULL) <> 'item' THEN
    RAISE EXCEPTION 'a deliberate level 1 is indistinguishable from nobody having said'; END IF;
  n := n + 1;

  -- 2. SPECIFICITY WINS: the item override beats the agent default, in both directions.
  IF brain.delegation_resolve(2::smallint, 6::smallint) <> 2
     OR brain.delegation_resolve(7::smallint, 1::smallint) <> 7
     OR brain.delegation_source(2::smallint, 6::smallint) <> 'item' THEN
    RAISE EXCEPTION 'the item override did not beat the agent default'; END IF;
  n := n + 1;

  -- 3. NO OVERRIDE TAKES THE AGENT DEFAULT.
  IF brain.delegation_resolve(NULL, 6::smallint) <> 6
     OR brain.delegation_source(NULL, 6::smallint) <> 'agent' THEN
    RAISE EXCEPTION 'an item with no override did not take the agent default'; END IF;
  n := n + 1;

  -- 4. THE GATE, WALKED OVER ALL 21 LEVEL-AND-FLAG COMBINATIONS RATHER THAN ASSERTED ONCE.
  --    7 levels x 3 flag combinations. THE DENOMINATOR IS PRINTED, because a verdict over an
  --    empty set is not a pass.
  SELECT count(*) INTO escaped
    FROM generate_series(1, 7) AS g(level)
    CROSS JOIN (VALUES (true, false), (false, true), (true, true)) AS f(ext, canon)
   WHERE brain.delegation_surfacing(g.level::smallint, f.ext, f.canon) <> 'now';
  IF escaped <> 0 THEN
    RAISE EXCEPTION 'a flagged act escaped at % of 21 level-and-flag combinations', escaped;
  END IF;
  n := n + 1;

  -- 5. THE POSITIVE CONTROL. "Nothing escaped" is ALSO what check 4 prints if the function
  --    returned 'now' for everything, which would make the ladder meaningless rather than safe.
  --    WITHOUT a flag the levels must still DIFFER, and by at least three distinct answers.
  SELECT count(DISTINCT brain.delegation_surfacing(g.level::smallint, false, false))
    INTO escaped FROM generate_series(1, 7) AS g(level);
  IF escaped < 3 THEN
    RAISE EXCEPTION 'positive control failed: unflagged levels gave only % distinct answers, so '
                    'check 4 may be passing because everything surfaces', escaped;
  END IF;
  n := n + 1;

  -- 6. THE CHECK CONSTRAINT, WATCHED REFUSING. An eighth level is somebody inventing a rung, and
  --    a rung that stored would grant or withhold authority nobody defined.
  BEGIN
    INSERT INTO brain.work_item (title, lane, state, priority, posted_by, delegation_level)
      VALUES ('migration 56 probe, never committed', 'engine', 'inbox', 3, 'migration', 8);
    RAISE EXCEPTION 'work_item_delegation_level_check accepted level 8';
  EXCEPTION WHEN check_violation THEN
    n := n + 1;
  END;

  -- 7. AND ON THE AGENT SIDE TOO, watched refusing rather than assumed symmetrical.
  --
  --    AN INSERT, NOT AN UPDATE, AND THE FIRST DRAFT OF THIS FILE GOT IT WRONG. It said
  --    `UPDATE brain.agent SET delegation_level = 0 WHERE name = (SELECT name FROM brain.agent
  --    LIMIT 1)`, which on a store with NO AGENTS updates nothing, raises nothing, and falls
  --    through to the failure line -- reporting that the constraint ACCEPTED level 0 when in
  --    truth nothing was ever offered to it. A VERDICT OVER AN EMPTY SET, in the one place this
  --    file is supposed to be proving a refusal.
  --
  --    Caught 2026-09-01 by the rehearsal, not by review: `queue/tests/run-all.sh` brought
  --    `brain_queue_scratch` up through this file and it died here, because that store holds zero
  --    agents. An INSERT supplies its own row and cannot be fooled by an empty table.
  BEGIN
    INSERT INTO brain.agent (name, delegation_level) VALUES ('migration-56-probe', 0);
    RAISE EXCEPTION 'agent_delegation_level_check accepted level 0';
  EXCEPTION WHEN check_violation THEN
    n := n + 1;
  END;

  -- 8. THE GATE WALKED ON A REAL ROW, THROUGH THE VIEW, NOT THROUGH THE FUNCTION.
  --    A level-7 row carrying `external` MUST STILL SURFACE. This is the acceptance gate and it
  --    is watched here on an actual row that goes through the actual join.
  INSERT INTO brain.work_item (title, lane, state, priority, posted_by,
                               delegation_level, external)
    VALUES ('migration 56 gate probe, never committed', 'engine', 'inbox', 3, 'migration', 7, true)
    RETURNING id INTO probe;

  SELECT d.level, d.surfaces INTO lvl, got
    FROM brain.work_item_delegation d
   WHERE d.title = 'migration 56 gate probe, never committed';
  IF lvl IS NULL THEN
    RAISE EXCEPTION 'the planted row did not appear in work_item_delegation at all, so the gate '
                    'below would be a verdict over an empty set';
  END IF;
  IF lvl <> 7 THEN
    RAISE EXCEPTION 'the planted row did not carry level 7, it carried %', lvl; END IF;
  IF got <> 'now' THEN
    RAISE EXCEPTION 'A LEVEL-7 ROW CARRYING external DID NOT SURFACE: it read %', got; END IF;
  n := n + 1;

  -- 9. THE POSITIVE CONTROL FOR CHECK 8, ON A REAL ROW. Without it, check 8 would pass on a view
  --    that said 'now' for every row, which is a gate nobody has seen fail.
  INSERT INTO brain.work_item (title, lane, state, priority, posted_by,
                               delegation_level, external)
    VALUES ('migration 56 control probe, never committed', 'engine', 'inbox', 3, 'migration',
            7, false);
  SELECT surfaces INTO got FROM brain.work_item_delegation
   WHERE title = 'migration 56 control probe, never committed';
  IF got <> 'audit' THEN
    RAISE EXCEPTION 'positive control failed: an UNFLAGGED level-7 row read % rather than audit, '
                    'so check 8 may be passing because every row surfaces', got;
  END IF;
  n := n + 1;

  -- 10. AN ORDINARY ROW IS GOVERNED, and the operator's own work is NOT -- but only half of
  --     that pair can be proven here, and the reason is worth recording because it is a property
  --     of the store rather than a gap in this file.
  --
  --     THIS FILE CANNOT PLANT A `actor_type='human'` ROW. Migration 20's trigger
  --     `brain.work_item_human_actor_is_a_login()` refuses that value to any connection with no
  --     row in `brain.human_role`, and a migration applies as the bootstrap superuser `postgres`,
  --     which is deliberately not a human login. Measured 2026-09-01 by watching this very block
  --     die on it:
  --
  --         ERROR: refusing to mark work item 0004 as actor_type=human: this connection is
  --                postgres, which is not a human login
  --
  --     THAT IS THE STORE WORKING. Human authorship is established by WHICH LOGIN writes the row
  --     and not by a flag anyone may pass, precisely so an agent cannot hand its own work to the
  --     operator to sign off. A migration that could forge it would be the same forgery arriving
  --     through a third door.
  --
  --     So the ungoverned half is proven in `engine/tests/test_the_ladder.py`, which calls
  --     `delegation.resolve(actor_type='human')` directly and needs no row and no login. What is
  --     asserted HERE is the half a migration can honestly assert: an ordinary row IS governed,
  --     so `governed` is not simply false for everything.
  SELECT count(*) INTO escaped FROM brain.work_item_delegation
   WHERE title LIKE 'migration 56 %probe%' AND governed IS NOT TRUE;
  IF escaped <> 0 THEN
    RAISE EXCEPTION 'an ordinary work item read as ungoverned in % rows', escaped; END IF;
  n := n + 1;

  DELETE FROM brain.work_item WHERE posted_by = 'migration' AND title LIKE 'migration 56 %probe%';
  DELETE FROM brain.agent WHERE name = 'migration-56-probe';

  RAISE NOTICE 'migration 56: % of 10 checks passed, including TWO CHECK constraints watched '
               'refusing and the level-7-plus-external gate watched holding on a real row through '
               'the view, with its positive control. Ladder walked over 21 level-and-flag '
               'combinations. % agents and % work items now carry a delegation column; all of them '
               'NULL, which reads as level 1 and is nobody''s grant.',
               n, (SELECT count(*) FROM brain.agent),
               (SELECT count(*) FROM brain.work_item);
  IF n <> 10 THEN RAISE EXCEPTION 'migration 56: expected 10 checks, ran %', n; END IF;
END $$;

INSERT INTO brain.schema_migration (version, name)
  VALUES (56, '0056_a_level_attaches_to_the_work')
  ON CONFLICT (version) DO NOTHING;

COMMIT;
