-- migration 22: the operator's acceptance stops being a column any agent can write
--
-- Task 0260, child of 0242 (the D9 re-run). Reported by T6, re-measured by T4 before a line of
-- this was written. Additive and re-runnable: one function, one trigger, two COMMENTs. It drops
-- nothing, edits no applied file and changes no column type.
--
-- APPLIED TO LIVE by the operator at the keyboard on 2026-08-17. `brain.schema_migration` on the
-- live `brain` database holds version 22 = `0022_work_item_acceptance_guard`, applied_at
-- 2026-08-17 11:13:27.692454+00, and `pg_trigger` on `brain.work_item` lists three non-internal
-- triggers: this one, `work_item_brief_write_once`, `work_item_parent_acyclic`. He re-ran the
-- four-route attack as `brain_runtime` against live himself rather than trusting the scratch
-- result and all four were refused; the four real acceptances (0027, 0051, 0054, 0055) are
-- intact. That sentence is measured, not asserted: it was re-read from the ledger and from
-- `pg_trigger` after the apply. Migration 19 in this same directory carries an "applied clean on
-- live" line that was NEVER TRUE, so treat every such claim -- including this one -- as a thing
-- to re-read before you rely on it.
--
-- ================================================================= DO NOT SIMPLIFY THIS TO
-- ================================================================= WRITE-ONCE
--
-- FIRST, BECAUSE IT IS THE ONE CHANGE THIS FILE REFUSES, and the operator asked for the refusal
-- to happen at review rather than in production (task 0260, his close note).
--
-- The guard below looks like it is one branch away from being simpler. The REWRITE branch --
-- `IF was_accepted AND is_accepted` -- does not just refuse; it goes and asks `brain.thread`
-- whether a `reopen` landed strictly after the acceptance being replaced, and permits the write
-- if one did. Deleting that subquery and refusing every second write would shorten the function,
-- remove its only read of another table, and read as an obvious tightening. It is not. It is a
-- silent removal of a human decision.
--
-- THE LOOP IT WOULD KILL, by name:  accept -> reopen -> done -> accept again.
--
-- That is D00 rule 3's own sequence and it contains TWO REAL HUMAN DECISIONS, not one decision
-- and one correction. The operator accepts a task; on a second read he decides it is not what he
-- asked for and sends it back with `reopen`; an agent works it again and reports `done`; he reads
-- the new report and decides a second time. Write-once refuses that fourth step. D00 rule 3 is
-- explicit that narrowing WHO may accept must never narrow WHETHER an acceptance was recorded,
-- and write-once narrows exactly that: the second decision still happens, it just stops being
-- written down, and the record this whole migration exists to protect ends up describing the
-- first decision as though the send-back never happened.
--
-- HOW IT WOULD BE DISCOVERED IF IT SHIPPED -- which is the argument for catching it here. Not by
-- a crash and not by a red suite in the lane that made the change. By the operator, at the
-- console, on the one kind of night that matters most to the measurement: a night he sent work
-- back. His second Accept would be refused by his own database, with a message about rewriting an
-- acceptance, for doing the thing the system asked him to do. And the item he could not accept is
-- precisely a send-back-then-accept case, which is the highest-information row in the input to
-- `engine/swarm_engine/accept.py:disagreement_report`. A write-once trigger does not merely
-- annoy him; it deletes the disagreements from the auto-accept measurement, leaving a cleaner
-- number that is wrong -- the exact confidently-wrong shape this program was built to prevent.
--
-- BOTH DIRECTIONS ARE PINNED BY TESTS, so a write-once patch cannot pass green:
--   engine/tests/test-acceptance-guard.py
--     test_accept_reopen_done_accept_again_is_permitted        the loop must WORK -- and the same
--                                                             test then asserts that with no NEW
--                                                             reopen after the second acceptance
--                                                             a rewrite is refused again, so the
--                                                             exception is not a loophole either
--     test_writing_the_same_acceptance_again_is_not_a_failure  an idempotent re-write is a no-op,
--                                                             not an error (migration 14's rule)
-- If you are reading this because one of those two failed, the test is not the thing that is
-- wrong. Do not relax it, do not delete it, and do not narrow its window: that is
-- tolerance-widening, and it is how this defect would come back wearing a green suite.
--
-- IF YOU STILL THINK WRITE-ONCE IS RIGHT, the change you actually want is a different EVIDENCE
-- SOURCE for the re-decision, not a deleted branch. The `brain.thread` reopen event is doing real
-- work here: the thread is append-only and no verb deletes from it, so the operator's send-back
-- leaves a mark a rogue UPDATE has no reason to manufacture. Replace that evidence with something
-- stronger (an operator-only login -- see "what this does NOT close" below) and the branch can
-- become stricter. Remove the evidence and keep the strictness and you have taken a human
-- decision out of the record to make a function shorter.
--
-- One note for whoever tidies this file: this block is deliberately `--` comment text, which psql
-- strips and the database never stores. Verified 2026-08-17 -- `pg_proc.prosrc` for
-- `brain.work_item_acceptance_guard` on live is byte-identical to the `$$ ... $$` body below,
-- 6765 characters, zero differing lines -- and this file has to keep describing live exactly.
-- Moving any of this INSIDE the function body silently ends that, because live is already applied
-- and re-applying is not on the table. New text there needs a new migration.
--
-- ================================================================= what was broken
--
-- D00 rule 3 makes acceptance a HUMAN act, separate from the agent's `done`, and
-- `brain.work_item.accepted_by` is the entire record of it. The verb guards that door properly:
--
--     store.apply("accept work", id=..., by="T2")
--     -> VerbError: refusing to record T2 as the acceptor of 0035: T2 is a fleet agent, and the
--        decider on every acceptance is a human (D00 rule 4).
--
-- The COLUMN behind that door had no guard at all. `information_schema.triggers` on
-- `brain.work_item` listed `work_item_brief_write_once` (migration 14) and
-- `work_item_parent_acyclic` (migration 12) and nothing about acceptance. Meanwhile
-- `brain.recommendation` -- which holds ZERO rows on live and which nothing produces -- has had
-- `recommendation_human_decider` guarding its decider since migration 7. The trigger went on the
-- empty table and not on the one the operator writes every day.
--
-- MEASURED 2026-08-17 on scratch `brain_t4_0260` (built by `engine/bin/scratch-db.sh create`,
-- schema_migration 19, 30 tables), as `brain_runtime` over a direct psycopg2 connection -- the
-- credential every agent process already holds in order to call `store.apply` at all. All four
-- returned `rowcount=1`:
--
--   1  UPDATE brain.work_item SET accepted_by='T2' WHERE id='0001'
--      0001 had just been accepted by the operator through the verb. Afterwards the row says the
--      acceptor was an agent, and `accepted_at` still carries the human's timestamp.
--
--   2  UPDATE brain.work_item SET accepted_by='operator', accepted_at=now() WHERE id='0002'
--      0002 was BLOCKED. Never done, never reviewed, never touched by a human. An acceptance
--      forged onto work nobody finished.
--
--   3  UPDATE brain.work_item SET accepted_by='', accepted_at=NULL WHERE id='0001'
--      The acceptance erased. `web/actions.py:accept_work` states the intended rule in prose --
--      "acceptance is a recorded decision; nothing clears it" -- and nothing enforced it.
--
--   4  INSERT INTO brain.work_item (id,lane,title,state,accepted_by,accepted_at)
--             VALUES ('9260','t','forged','done','operator',now())
--      NOT IN THE REPORT AND FOUND WHILE REPRODUCING IT. A work item BORN accepted, out of
--      nothing: no `post`, no `claim`, no `done`, no thread, no run. This is the worst of the
--      four for the measurement below, because the other three at least leave a real task
--      behind that an auditor could go and read.
--
-- ================================================================= why this table, now
--
-- Two weeks of dogfooding produce exactly one primary artefact: the record of what the operator
-- accepted and what he sent back. `engine/swarm_engine/accept.py:disagreement_report` scores the
-- auto-accept rule against precisely that record, and the whole reason auto-accept SHIPS
-- DISABLED is that the measurement has to come first. A measurement whose input any agent can
-- write is the confidently-wrong number the program exists to prevent.
--
-- ================================================================= the rules, and why each one
--
-- The trigger is BEFORE INSERT and BEFORE UPDATE OF accepted_by, accepted_at. `UPDATE OF` means
-- it costs nothing on the writes that do not name those columns, which is nearly all of them --
-- `claim`, `done`, `fail`, `block`, `reopen`, `set`, `artifact` all pass straight through.
--
--   INSERT   A row may not be born accepted. Closes route 4. `post` never names either column,
--            so no verb is affected.
--
--   ERASE    accepted -> unaccepted is refused, always, including after a reopen. Closes route
--            3, and it is the rule `web/actions.py` already documents as the reason `accept
--            work` offers no undo.
--
--   REWRITE  A recorded acceptance may not be silently replaced. Closes route 1. THIS IS THE RULE
--            A LATER READER WILL TRY TO SIMPLIFY -- see "DO NOT SIMPLIFY THIS TO WRITE-ONCE" at
--            the top of this file before you touch it.
--            ONE EXCEPTION, and it is not a loophole, it is D00 rule 3's own loop: accept ->
--            reopen -> done -> accept again is a real sequence of two real human decisions, and
--            a rule that refused the second one would narrow WHETHER acceptance is recorded,
--            which is exactly the thing D00 says narrowing WHO must never do. So a replacement
--            is permitted only when `brain.thread` -- append-only, deleted by no verb -- carries
--            a `reopen` STRICTLY AFTER the acceptance being replaced. A rogue UPDATE has no such
--            event; the operator's send-back does. Writing the same values again is a no-op and
--            is allowed, so a re-run or an idempotent write is not a failure (migration 14's
--            precedent).
--
--   COHERENT accepted_at and accepted_by move together. Half an acceptance -- a `by` with no
--            `at`, or an `at` with no `by` -- is refused. This is what actually catches route 1
--            in its other form, against a row that was never accepted: `SET accepted_by='T2'`
--            alone leaves `accepted_at` NULL, and a reader would then have to decide for itself
--            which of the two columns to believe.
--
--   FINISHED Only work in state `done` can be accepted. Closes route 2. Acceptance means a human
--            read the agent's report, and an inbox/active/blocked/cancelled row has no report to
--            have read. This is not a new rule invented here: `web/templates/detail_task.html`
--            already renders the Accept button only under `w.state == 'done' and not
--            w.accepted_at`, and `accept.would_accept` already lists `state <> done` as an
--            ineligibility reason. The table now holds what the template held.
--
--   HUMAN    The acceptor may not be a registered agent. The predicate is the verb's own, byte
--            for byte in intent: `EXISTS (SELECT 1 FROM brain.agent WHERE name = ...)`. `claim`
--            and `heartbeat` both write a row there, so every name the fleet has taken work
--            under is in that table, and the operator -- who claims nothing -- is not. Two
--            gates, the verb's and this one, for the reason
--            `queue/human_queue/transitions.py:152` gives about `recommendation`: a gate that
--            exists in one place is a gate one bug away from being absent.
--
-- ================================================================= what this does NOT close
--
-- STATED PLAINLY BECAUSE A GUARD THAT OVERSELLS ITSELF IS WORSE THAN NO GUARD.
--
-- A rogue or buggy agent can still write `accepted_by='operator', accepted_at=now()` onto a
-- genuinely `done`, genuinely unaccepted task. This trigger cannot refuse that, and no trigger
-- can: the console's own legitimate acceptance is byte-for-byte the same UPDATE from the same
-- login, `brain_runtime`, so the database has nothing to tell them apart by. Route 2 is closed
-- only in the shape it was reported -- forging onto work that was never finished.
--
-- And the FINISHED rule is one statement deep, not two. `state` is an ordinary column that
-- `brain_runtime` may write, so an agent that first does `UPDATE ... SET state='done'` and then
-- accepts gets past it. That is deliberate and not an oversight: guarding `state` transitions at
-- the table is a different and much larger piece of work (every verb writes that column), and it
-- would still not close the residual above, because the last statement in the pair is
-- indistinguishable from the console's. What this rule buys is that the reported one-statement
-- forge no longer works and the two-statement version leaves a `done` state on a task with no
-- `done` event on the append-only thread -- a discrepancy an auditor can find. The rule that
-- actually ends this threat model is an operator-only login, below.
--
-- Closing the residual needs the acceptance to be written by a login the fleet does not hold,
-- and that is a real piece of work rather than a line here. It was posted as swarm task 0275,
-- child of 0260 -- and 0275 IS NOW CANCELLED, not done: the operator reset the whole queue at
-- 2026-08-17T10:54Z ("full queue reset ... this backlog served two superseded programs"). So the
-- residual described in this section is OPEN AND UNTRACKED BY ANY LIVE TASK, and this paragraph
-- is the only remaining pointer to it. Do not read the cancellation as a decision that the
-- residual does not matter; it is a decision about the queue. Two traps if you go and read 0275:
-- it calls this file "migration 21", which was its number before the renumber below, and it
-- predates the live apply.
-- Migration 20 (`0020_human_actor_identity`, task 0267, in flight while this was written) builds
-- `brain.human_role` and a `brain_operator` login for the neighbouring question of who may
-- create the operator's OWN work; that is the mechanism the follow-up should use. This migration
-- deliberately does NOT depend on it -- an uncommitted, unapplied migration is not a dependency
-- -- and the two compose without either knowing about the other.
--
-- A restore is also not covered, and cannot be: `pg_restore` and `session_replication_role =
-- replica` run with triggers disabled by design. That is why the guarantee is repeated as a
-- COMMENT on the columns, where a reader of the schema finds it. Migration 14 made the same
-- point about `brief` and it holds here for the same reason.
--
-- ================================================================= WHY 22, AND WHY NOT 21
--
-- Read from `brain.schema_migration`, never from `ls migrations/`. This repo's migration files
-- live in three directories (`migrations/`, `queue/schema/`, `budget/schema/`), the filename
-- prefix is a per-lane counter that lies, and the ledger is the only truth. Tonight the ledger
-- is ALSO not enough on its own, and that is worth writing down because it is not the usual
-- case: FOUR files are stacked above the applied maximum, so the next free number has to be read
-- from the ledger AND from a scan of all three directories.
--
-- Measured 2026-08-17T10:4xZ on the live `brain` database: `schema_migration` held 1..18 with no
-- gaps, so max(version) = 18, and every number above it was claimed by a file applied NOWHERE.
-- RE-MEASURED at 11:2xZ, and it had already moved: live now reads 1..18 PLUS 21, because
-- `budget/schema/0021_meter_mismatch_kind.sql` was applied to live at 10:52:48Z while this file
-- was being tested. That is the collision below, resolved from the other end -- 21 is not merely
-- claimed now, it is TAKEN, on live, by another lane. Taking a claimed number means two files
-- with one ledger row:
--
--   19  migrations/0019_git_ref_repo_identity.sql   task 0256. Its own header says it "applied
--       clean on live on 2026-08-17"; it did NOT. Live has no version-19 row and
--       `information_schema.columns` returns 0 for `brain.receipt.git_repo`. The DDL half of
--       0256 was refused by the harness permission classifier and T6 recorded that on the
--       thread; the header sentence ran ahead of it.
--   20  migrations/0020_human_actor_identity.sql    task 0267, T3, in flight and uncommitted
--       while this file was being written. It is also the migration the follow-up to THIS task
--       builds on -- see "what this does NOT close" above.
--   21  budget/schema/0021_meter_mismatch_kind.sql  the budget lane, and APPLIED TO LIVE at
--       2026-08-17T10:52:48Z. This file was numbered 21 first and TESTED as 21; the budget file
--       appeared during that test run and `scratch-db.sh` stopped the suite with "two schema
--       files claim the same ledger version(s): 21". The renumber to 22 was mine rather than the
--       budget lane's because there was no way to tell from the tree who wrote first -- and the
--       live ledger then settled it, which is the point: had this file kept 21 it would have hit
--       `ON CONFLICT (version) DO NOTHING` against a row another lane had already written, and
--       applied NOTHING while exiting 0.
--
-- That refusal is the builder working, not the builder being fussy: EVERY migration file writes
-- `ON CONFLICT (version) DO NOTHING`, so the loser of a duplicate applies nothing at all and
-- still exits 0. A silent no-op migration is the failure mode the whole ledger exists to catch.
--
-- So 22, and the guard below fails loudly rather than silently if a fifth lane takes it first.
-- NOTE that live already has a real gap and this does not widen it in any way that matters: the
-- ledger there reads 1..18 and 21, so 19 and 20 are missing and 22 will sit above them. The
-- ledger tolerates that (each file applies independently) and this file touches nothing any of
-- them touches -- 19 adds columns to `receipt` and `touch`, 20 adds `human_role` and constrains
-- `actor_type`, 21 is a budget meter kind. Apply them in any order.

\set ON_ERROR_STOP on

DO $$
DECLARE taken text;
BEGIN
  SELECT name INTO taken FROM brain.schema_migration WHERE version = 22;
  IF taken IS NOT NULL AND taken <> '0022_work_item_acceptance_guard' THEN
    RAISE EXCEPTION 'schema version 22 is already held by %, not 0022_work_item_acceptance_guard. '
                    'Renumber from brain.schema_migration, never from ls: this repo keeps '
                    'migrations in three directories, the filename prefix is a per-lane counter, '
                    'and on 2026-08-17 versions 19, 20 and 21 were ALL claimed by files applied '
                    'nowhere. Scan all three directories as well as the ledger.',
                    taken;
  END IF;
END $$;

BEGIN;

SET search_path TO brain, public;

-- ---------------------------------------------------------------- the guard

CREATE OR REPLACE FUNCTION brain.work_item_acceptance_guard() RETURNS trigger
  LANGUAGE plpgsql AS $$
DECLARE
  was_accepted boolean;
  is_accepted  boolean;
  by_changed   boolean;
  at_changed   boolean;
  reopened     boolean;
  is_agent     boolean;
  HINT_WHY constant text :=
    'D00 rule 3: `done` is the agent''s report that it finished, and acceptance is the separate '
    'act that says a human read it. brain.work_item.accepted_by is the whole record of that act '
    'and it is the input to the auto-accept measurement (engine/swarm_engine/accept.py). Write '
    'it through the `accept work` verb -- `swarm accept-work <id> --by <you>`, or the console -- '
    'and reject through `reopen`, which leaves both acts on the append-only thread. If you are a '
    'fleet agent, neither of those is yours to call.';
BEGIN
  -- Absent is one state with two spellings on this table: `accepted_by` is nullable and `post`
  -- leaves it NULL, while a hand-written UPDATE reaches for ''. Both mean not accepted, and
  -- every test below has to read them the same way or route 3 walks straight through the ''
  -- door.
  is_accepted := NEW.accepted_at IS NOT NULL OR COALESCE(NEW.accepted_by, '') <> '';

  -- ------------------------------------------------------------ INSERT: never born accepted
  IF TG_OP = 'INSERT' THEN
    IF is_accepted THEN
      RAISE EXCEPTION 'a work item may not be born accepted (id would be %, accepted_by %)',
                      NEW.id, COALESCE(NULLIF(NEW.accepted_by, ''), '<null>')
        USING HINT = 'Acceptance is the record that a human read a finished report, so it cannot '
                     'precede the work. INSERT the item through `post`, which names neither '
                     'column, and accept it after an agent has reported it done. ' || HINT_WHY;
    END IF;
    RETURN NEW;
  END IF;

  was_accepted := OLD.accepted_at IS NOT NULL OR COALESCE(OLD.accepted_by, '') <> '';
  by_changed   := COALESCE(NEW.accepted_by, '') IS DISTINCT FROM COALESCE(OLD.accepted_by, '');
  at_changed   := NEW.accepted_at IS DISTINCT FROM OLD.accepted_at;

  -- Nothing about the acceptance actually moved. Covers the `SET accepted_by = accepted_by`
  -- shape and any statement that merely names the columns.
  IF NOT by_changed AND NOT at_changed THEN
    RETURN NEW;
  END IF;

  -- ------------------------------------------------------------ never erased
  IF was_accepted AND NOT is_accepted THEN
    RAISE EXCEPTION 'refusing to erase the acceptance of %: it is recorded as accepted by % at %',
                    OLD.id, COALESCE(NULLIF(OLD.accepted_by, ''), '<null>'), OLD.accepted_at
      USING HINT = 'A record you can silently remove is not a record. Nothing in the runtime '
                   'clears this and that is deliberate, not a gap (web/actions.py:accept_work). '
                   'Send the work back with `reopen` instead, which leaves BOTH acts on the '
                   'trail. ' || HINT_WHY;
  END IF;

  -- ------------------------------------------------------------ never silently replaced
  IF was_accepted AND is_accepted THEN
    -- The one legitimate replacement: the operator sent it back after accepting it, an agent
    -- worked it again, and he is deciding a second time. `brain.thread` is append-only and no
    -- verb deletes from it, so the reopen event is evidence a rogue UPDATE cannot manufacture
    -- without also writing a reopen it has no reason to write. STRICTLY after: `reopen` lands in
    -- a later transaction than the acceptance it rejects, so `>` is right and `>=` would let a
    -- same-transaction pair through.
    SELECT EXISTS (SELECT 1 FROM brain.thread t
                    WHERE t.work_item_id = OLD.id
                      AND t.kind = 'reopen'
                      AND t.ts > OLD.accepted_at) INTO reopened;
    IF NOT reopened THEN
      RAISE EXCEPTION 'refusing to rewrite the acceptance of %: it is recorded as accepted by % '
                      'at %, and this UPDATE would make it % at %',
                      OLD.id, COALESCE(NULLIF(OLD.accepted_by, ''), '<null>'), OLD.accepted_at,
                      COALESCE(NULLIF(NEW.accepted_by, ''), '<null>'), NEW.accepted_at
        USING HINT = 'An acceptance is amended by re-deciding, not by editing: `reopen` the item, '
                     'let it be worked and reported again, then accept it a second time. That '
                     'path IS permitted here and puts every step on the thread. ' || HINT_WHY;
    END IF;
  END IF;

  -- Everything past here is a NEW acceptance being recorded, or a re-decision after a reopen.
  -- Both must satisfy the same three rules; there is no cheaper second door.
  IF NOT is_accepted THEN
    RETURN NEW;                       -- unaccepted -> unaccepted, nothing to check
  END IF;

  -- ------------------------------------------------------------ both halves, or neither
  IF NEW.accepted_at IS NULL OR COALESCE(NEW.accepted_by, '') = '' THEN
    RAISE EXCEPTION 'refusing half an acceptance on %: accepted_by=% accepted_at=%',
                    NEW.id, COALESCE(NULLIF(NEW.accepted_by, ''), '<null>'),
                    COALESCE(NEW.accepted_at::text, '<null>')
      USING HINT = 'The two columns are one fact. A `by` with no `at` or an `at` with no `by` '
                   'leaves every reader to pick which half to believe, and the measurement picks '
                   'differently from the console. ' || HINT_WHY;
  END IF;

  -- ------------------------------------------------------------ only finished work
  IF NEW.state <> 'done' THEN
    RAISE EXCEPTION 'refusing to accept %: it is %, not done',
                    NEW.id, NEW.state
      USING HINT = 'Acceptance says a human read the agent''s report. A task that is not `done` '
                   'has filed no report, so there is nothing for the acceptance to be about. '
                   'The console already renders the Accept button only on a done, unaccepted '
                   'item (web/templates/detail_task.html); this is that rule held by the table. '
                   || HINT_WHY;
  END IF;

  -- ------------------------------------------------------------ never an agent
  SELECT EXISTS (SELECT 1 FROM brain.agent a WHERE a.name = NEW.accepted_by) INTO is_agent;
  IF is_agent THEN
    RAISE EXCEPTION 'refusing to record % as the acceptor of %: % is a fleet agent',
                    NEW.accepted_by, NEW.id, NEW.accepted_by
      USING HINT = 'D00 rule 4: the decider on every acceptance is a human, and an agent '
                   'accepting ANY agent''s work is that self-approval one call removed. Every '
                   'name the fleet has taken work under is in brain.agent, written by `claim` '
                   'and `heartbeat`; the operator, who claims nothing, is not. ' || HINT_WHY;
  END IF;

  RETURN NEW;
END $$;

COMMENT ON FUNCTION brain.work_item_acceptance_guard() IS
  'The second gate on the operator''s acceptance. `accept work` is the first and it is a good '
  'one, but it guards the DOOR and this guards the COLUMN: measured 2026-08-17 (task 0260), '
  'plain UPDATEs and an INSERT as brain_runtime -- the credential every agent process holds -- '
  'reattributed a human''s acceptance to an agent, forged one onto a blocked task, erased one, '
  'and created a work item born accepted, all four at rowcount=1. It does NOT distinguish the '
  'console''s legitimate acceptance from a rogue agent writing the same UPDATE, because both '
  'arrive on the same login; that residual needs an operator-only credential and is posted as a '
  'child of task 0260. It also cannot cover a restore, which runs with triggers disabled.';

DROP TRIGGER IF EXISTS work_item_acceptance_guard ON brain.work_item;
CREATE TRIGGER work_item_acceptance_guard
  BEFORE INSERT OR UPDATE OF accepted_by, accepted_at ON brain.work_item
  FOR EACH ROW
  EXECUTE FUNCTION brain.work_item_acceptance_guard();

-- ---------------------------------------------------------------- say it in the schema too

COMMENT ON COLUMN brain.work_item.accepted_by IS
  'WHO decided, and the whole record of D00 rule 3''s human act. Never an agent: `claim` and '
  '`heartbeat` put every name the fleet works under into brain.agent, and the acceptor may not '
  'be one of them. `auto-accept` is the reserved name the measured rule writes when the operator '
  'has turned it on (engine/swarm_engine/accept.py:measure_in_transaction); it ships off. '
  'Written by the `accept work` verb and by nothing else. Guarded by trigger '
  'work_item_acceptance_guard (migration 22): a row is never born accepted, an acceptance is '
  'never erased, and it is replaced only after a `reopen` on the append-only thread. A trigger '
  'cannot cover a restore, which runs with triggers disabled, so treat ANY code path that '
  'UPDATEs this column outside `accept work` as a defect regardless of what the table lets '
  'through.';

COMMENT ON COLUMN brain.work_item.accepted_at IS
  '`done` means the agent reported it finished. Acceptance is a separate act and `reopen` is the '
  'rejection verb. Narrowing WHO accepts never narrows WHETHER it was recorded. Moves together '
  'with accepted_by -- migration 22 refuses half an acceptance -- and is only ever set on a row '
  'in state `done`. Nothing clears it, on purpose: a record that can be silently removed is not '
  'a record (web/actions.py:accept_work).';

INSERT INTO brain.schema_migration (version, name)
VALUES (22, '0022_work_item_acceptance_guard') ON CONFLICT (version) DO NOTHING;

COMMIT;
