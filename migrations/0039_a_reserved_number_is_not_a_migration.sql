-- migration 39: the hole is closed by a RESERVATION, and a reservation is not a migration
--
-- LEDGER VERSION 39, read from brain.schema_migration and not from this directory. Re-measured
-- 2026-08-29 through store.read() against live `brain`: 41 rows, min 1, max 42, and the one
-- absent number in that span is 39. Across the working tree on the same date, 44 of 44 files
-- matching [0-9][0-9][0-9][0-9]_*.sql in migrations/, budget/schema/ and queue/schema/ yielded a
-- recorded version, and they spell 1..38 and 40..45. Nothing declared 39 and nothing ever had.
--
-- Bus row `0408`. This file creates NO object. It has no DDL at all: one guard that reads the
-- ledger, and the ledger row itself. That is the entire content and it is the entire point.
--
-- NOT APPLIED TO LIVE when this line was written. Migration 19 in this directory carries an
-- "applied clean on live" sentence that was never true, so treat any such claim, including this
-- one, as a thing to re-read from brain.schema_migration before relying on it.
--
-- ================================================================= WHERE THE HOLE CAME FROM
--
-- `outputs/2026-08-27-commander/L00-COMMANDER-BRIEF.md`, CORRECTION 1, assigned lane E the range
-- "36 to 39" on 2026-08-27. Lane E wrote three of the four:
--
--     36  migrations/0036_work_item_acceptance_is_a_login.sql
--     37  migrations/0037_work_item_assigned_human.sql
--     38  migrations/0038_one_namespace_for_humans_and_agents.sql
--     39  nothing
--
-- Then lanes F and J numbered past it (40, 41, 42), and 43, 44 and 45 landed after them. Both
-- migration 42 and migration 43 name the hole in their own headers and decline to fill it, in
-- the same words: "39 is a hole lane E left and it is not mine to fill." They were right to
-- decline, and the reason is below.
--
-- ================================================================= WHY A FREE HOLE IS A HAZARD
--
-- `engine/bin/scratch-db.sh::migration_list` orders every schema file by its RECORDED version,
-- and `cmd_migrate` applies only the versions a given database has not recorded. Those two facts
-- together mean a file at 39 has two different positions in the sequence:
--
--     a FRESH build   (`create`)    applies 39 BETWEEN 38 and 40
--     an EXISTING store (`migrate`) applies 39 AFTER 40, 41 and 42, because those are recorded
--
-- One file, two application orders, and the fresh build is the order every suite in this repo
-- tests against. If the file at 39 is additive and independent the two orders agree and nothing
-- happens. If it touches anything 40, 41 or 42 created, the suites go green on an order the live
-- store will never see. That failure mode has been paid for here before.
--
-- The rule the repo already carried is `queue/schema/0015_recommendation_human_login.sql`:
-- "Pick the next version by reading brain.schema_migration, never by listing a directory."
-- That rule is right and it is not sufficient, because READING THE LEDGER IS EXACTLY WHAT
-- SURFACES 39 AS AVAILABLE. A lane that obeys it perfectly can still land on the hazard. The
-- missing half is: take max(version) + 1, never the lowest free number.
--
-- ================================================================= WHY THIS FILE IS SAFE TO BE
-- ================================================================= THE ONE FILE AT 39
--
-- Everything above is an argument against putting work at 39. It is not an argument against
-- putting THIS at 39, and the difference is the whole design: a file with no DDL has nothing to
-- order against 40, 41 and 42. Applied before them on a fresh build and after them on a live
-- migrate, it produces byte-identical stores, because in both orders it does the same single
-- thing to the same single table. It is the one file for which the two orders are provably the
-- same, and once it exists there is no second file at 39 for which they could differ.
--
-- ================================================================= WHAT THE ROW CLAIMS, AND
-- ================================================================= WHAT IT MUST NOT
--
-- A schema_migration row is ordinarily a claim that a migration ran. This one is not, and its
-- `name` is where it says so: `0039_a_reserved_number_is_not_a_migration`. A reader running
--
--     SELECT version, name FROM brain.schema_migration ORDER BY version
--
-- gets the sentence in the answer rather than having to come and find this file. Nothing was
-- backfilled and no work is described that did not happen: the row records that 39 was assigned,
-- was not used, and is now permanently spent.
--
-- ================================================================= ROLLBACK, EXECUTED
--
--     DELETE FROM brain.schema_migration WHERE version = 39;
--
-- Run against brain_t3_0408_fresh on 2026-08-29: ledger went 45 rows -> 44 rows, version 39 rows
-- 1 -> 0, max unchanged at 45. Then `scratch-db.sh migrate` re-applied this file ("1 applied"),
-- version 39 rows back to 1 with name `0039_a_reserved_number_is_not_a_migration` and 45 rows
-- again, and a second `migrate` said "is current, nothing pending". There is nothing else to
-- undo, which is the property that made the fence cheaper than a fill. Note what the rollback
-- RESTORES: the hole, and with it the ambiguity. Do not run it to tidy up.
--
-- ================================================================= WHAT THIS DOES NOT CLOSE
--
-- It closes THIS hole. It does not prevent the next one: a lane assigned a range and using part
-- of it leaves a new hole one number higher, and no row here can stop that. The two things that
-- do are the corrected rule (max(version) + 1) now carried in docs/CHANGING-IT.md and
-- docs/WHY-IT-IS-LIKE-THIS.md, and `scratch-db.sh ledger`, which since task 0408 PRINTS any hole
-- it finds and prints the next free number next to it. `ledger` touches no database and is
-- already the thing a lane is told to read before it names a file.

\set ON_ERROR_STOP on

-- The house guard, and here it carries more than usual. If a lane has since put REAL work at 39,
-- this file must not quietly become a no-op over it: the reservation would then be a lie about a
-- number that is genuinely in use, and this file's own INSERT would do nothing under
-- ON CONFLICT (version) DO NOTHING while reporting a clean build.
DO $$
DECLARE taken text;
BEGIN
  SELECT name INTO taken FROM brain.schema_migration WHERE version = 39;
  IF taken IS NOT NULL AND taken <> '0039_a_reserved_number_is_not_a_migration' THEN
    RAISE EXCEPTION 'schema version 39 is already held by %, not the reservation this file '
                    'writes. That is the good outcome of a bad race: someone filled the hole '
                    'with real work. DELETE this file rather than this row, and take '
                    'max(version) + 1 for anything new.', taken;
  END IF;
END $$;

BEGIN;

INSERT INTO brain.schema_migration (version, name)
     VALUES (39, '0039_a_reserved_number_is_not_a_migration')
  ON CONFLICT (version) DO NOTHING;

COMMIT;
