-- migration 43: acceptance gets an inverse, and the withdrawal is a record rather than a revert
--
-- LEDGER VERSION 43, read from brain.schema_migration and not from this directory. Live `brain`
-- and `brain_demo` were both at 42 when this was written (2026-08-28, read-only), and 39 is a
-- hole lane E left which is not mine to fill. Verified free on both before this file was named,
-- per queue/schema/0015's own rule: pick the next version by READING the ledger.
--
-- Bus row `0413`. Additive and re-runnable: one widened CHECK, one new function, one function
-- replaced in place, three COMMENTs. It drops nothing, edits no applied file, changes no column
-- type and BACKFILLS NOTHING.
--
-- NOT APPLIED TO LIVE when this line was written. Migration 19 in this directory carries an
-- "applied clean on live" sentence that was never true, so treat any such claim, including this
-- one, as a thing to re-read from `brain.schema_migration` before relying on it.
--
-- ================================================================= WHAT IS OPEN
--
-- The operator drove the product end to end for the first time on 2026-08-28 and said:
--
--   "nothing happened when I accepted work. That would maybe be a moment for confetti and for it
--    to change from accept work to like accepted. Maybe you have an option to like unaccept or
--    like send back or undo the acceptance would be good."
--
-- `web/MUST-NOT-BUILD.md` item 10 promises an inverse verb wherever one exists, and acceptance
-- had none. It was put to him as a collision and he approved overruling item 10; the approval was
-- NOT spent, because item 10 does not forbid undo, it forbids a LIE about undo, and his ask is
-- the honest version of the same thing. The ruling is
-- `your-brain/projects/infinity-os-consolidation/decisions/2026-08-28-row-0421-two-prohibitions-overruled.md`
-- section B, and it says in as many words: deliver it "by building the inverses, starting with an
-- `unaccept` transition".
--
-- ================================================================= THE INVARIANT THIS CHANGES,
-- ================================================================= STATED BEFORE ANYTHING ELSE
--
-- Migration 22 refuses an erasure outright:
--
--   'refusing to erase the acceptance of %: it is recorded as accepted by % at %'
--   HINT: 'A record you can silently remove is not a record. Nothing in the runtime clears this
--          and that is deliberate, not a gap (web/actions.py:accept_work).'
--
-- The word that carries the whole rule is SILENTLY. This migration does not remove the refusal,
-- it gives it one narrow exemption: an erasure is permitted when the withdrawal is ITSELF a
-- record, on the same append-only thread the acceptance landed on, written by the human this
-- CONNECTION is. What migration 22 forbade was a removal that leaves the trail reading as though
-- the acceptance never happened. That is still forbidden here, by construction: the acceptance
-- row and the withdrawal row are both on `brain.thread`, no verb deletes from it, and the
-- withdrawal cannot be written by anything but a human login.
--
-- THE SECOND-ORDER CONSEQUENCE, NAMED RATHER THAN DISCOVERED LATER. Migration 22's rewrite rule
-- says an acceptance is replaced only after a `reopen`. Human A accepts, human B unaccepts,
-- human B accepts: `accepted_by` has moved from A to B with no reopen anywhere. That path is now
-- open and it was not before. It is permitted rather than closed because every step of it is on
-- the thread with a reason attached, which is exactly what migration 22's own HINT asks for
-- ("an acceptance is amended by re-deciding, not by editing"). What is NOT permitted, and is the
-- thing that rule exists against, is the silent edit: a bare UPDATE still hits the rewrite
-- refusal untouched, because it writes no withdrawal.
--
-- ================================================================= WHY THE LOGIN TEST IS HERE
-- ================================================================= AND NOT IN MIGRATION 36
--
-- Migration 36 is the trigger that makes `accepted_by` the DATABASE's answer about the
-- connection rather than a name the caller chose. It cannot carry this: its second line is
--
--   IF NOT is_accepted THEN RETURN NEW; END IF;
--
-- and an erasure is by definition `NOT is_accepted`. Widening that early return would change what
-- migration 36 means on every write it currently ignores. So the withdrawal's login test lives in
-- migration 22's guard, beside the refusal it is an exemption to, where a reader who finds the
-- refusal also finds the one way past it.
--
-- MEASURED, and it is the reason a login test is needed at all rather than assumed: from
-- `brain_runtime`, the credential every agent surface in this fleet holds, `brain.current_human()`
-- returns NULL. So arm 2 below is what makes this exemption unreachable from every agent process,
-- with or without a forged thread row. Arm 1 is what makes the removal non-silent. Neither arm is
-- redundant: arm 1 alone would let any holder of brain_runtime write its own withdrawal and erase,
-- and arm 2 alone would permit a human to erase leaving nothing on the trail, which is the exact
-- sentence migration 22 refuses.
--
-- ================================================================= THE NEW WORD, AND THE READERS
-- ================================================================= THAT MUST LEARN IT
--
-- `unaccept` is the nineteenth value of `brain.thread.kind`, added under migration 13's own
-- instruction ("If you are adding a nineteenth, add it here, and check `web/model.py` in the same
-- commit"). It is a distinct kind and not a `note` with a prefix for the reason migration 13
-- gives: a reader filtering the thread by kind must be able to tell a withdrawn acceptance from
-- somebody's remark, and the guard below reads a KIND rather than matching prose.
--
-- THE ONE READER THIS MIGRATION CANNOT REACH, said out loud because a silent drop is worse than
-- a red: `web/model.py:644` builds the console item screen's trail from a HARD ALLOWLIST of
-- kinds, and a kind that is not in it is DROPPED, not rendered unknown. `web/` is another lane's
-- file. Until `"unaccept"` is added to that tuple the withdrawal is recorded, is returned by
-- `swarm show`, and is in `brain.feed`, and does NOT appear on the console item screen. That is
-- the same defect migration 13 was written to repair for `budget` and it is stated here so the
-- lane that owns that file can close it in one word.
--
-- `swarm show` needs nothing: `engine/swarm_engine/reads.py:thread()` selects every kind.
-- `brain.feed` needs nothing: it passes `kind` through unfiltered. `web/model.py:crosstalk`
-- needs nothing: it falls through `.get(kind, "rep")` and renders.
--
-- ================================================================= WHAT IT DOES NOT CLOSE
--
--   * A restore. Triggers are disabled during one, exactly as migration 22 already says.
--   * A human erasing a COLLEAGUE's acceptance. Permitted, on purpose: it is a real act a second
--     seat may need to take, it is recorded under the withdrawer's own name, and the verb's
--     thread text names both parties. Refusing it would also make an acceptance unwithdrawable
--     the day its acceptor's login is retired.
--   * Filing a `done` report over an agent's work after a withdrawal. That is migration 42's job
--     and it still does it: an unaccept leaves `state` and `result` untouched, so the row is
--     still `done` with the agent's own report in it, and the operator's `done` on it arrives
--     with OLD.state = 'done' and a changed `result`, which migration 42 refuses. Tested in
--     `engine/tests/test_unaccept.py` rather than reasoned about.
--
-- ================================================================= ROLLBACK, STATED
--
--     BEGIN;
--       -- restore migration 22's function exactly as that file defines it, then:
--       DROP FUNCTION IF EXISTS brain.work_item_unacceptance_is_recorded(text, timestamptz);
--       ALTER TABLE brain.thread DROP CONSTRAINT IF EXISTS thread_kind_check;
--       ALTER TABLE brain.thread ADD CONSTRAINT thread_kind_check
--         CHECK (kind IN ('post','claim','note','msg','ask','answer','done','block','fail',
--                         'reopen','cancel','artifact','heartbeat','reap','set','accept','event',
--                         'budget'));
--       DELETE FROM brain.schema_migration WHERE version = 43;
--     COMMIT;
--
-- The narrowing of the CHECK is the half that can fail: it refuses if any `unaccept` row exists,
-- which is correct, since dropping the word while rows carry it would leave the table unable to
-- validate its own contents. Reclassify those rows or keep the word.

\set ON_ERROR_STOP on

DO $$
DECLARE taken text;
BEGIN
  SELECT name INTO taken FROM brain.schema_migration WHERE version = 43;
  IF taken IS NOT NULL AND taken <> '0043_acceptance_has_an_inverse' THEN
    RAISE EXCEPTION 'schema version 43 is already held by %, not 0043_acceptance_has_an_inverse. '
                    'Renumber from brain.schema_migration, never from ls: this repo keeps '
                    'migrations in three directories, the filename prefix is a per-lane counter, '
                    'and on 2026-08-17 versions 19, 20 and 21 were ALL claimed by files applied '
                    'nowhere. Scan all three directories as well as the ledger.',
                    taken;
  END IF;
END $$;

BEGIN;

SET search_path TO brain, public;

-- FAIL FAST RATHER THAN QUEUE. Migration 13's reasoning, unchanged: `ALTER TABLE ... ADD
-- CONSTRAINT` takes ACCESS EXCLUSIVE and a lock request that waits does not wait quietly.
SET LOCAL lock_timeout = '5s';

-- ---------------------------------------------------------------- the vocabulary, one word wider

-- DROP IF EXISTS then ADD, because Postgres 16 has no `ADD CONSTRAINT IF NOT EXISTS` and this
-- pair is re-runnable. The nineteen words are migration 13's eighteen in their original order
-- plus `unaccept` at the end. Nothing is removed.
ALTER TABLE brain.thread DROP CONSTRAINT IF EXISTS thread_kind_check;

ALTER TABLE brain.thread ADD CONSTRAINT thread_kind_check
  CHECK (kind IN ('post','claim','note','msg','ask','answer','done','block',
                  'fail','reopen','cancel','artifact','heartbeat','reap',
                  'set','accept','event','budget','unaccept'));

COMMENT ON COLUMN brain.thread.kind IS
  'The verb that produced the event. Nineteen words since migration 43, which added '
  '''unaccept'': the withdrawal of an acceptance, written only by the `unaccept work` verb and '
  'read by brain.work_item_unacceptance_is_recorded() as the evidence that lets migration 22 '
  'permit the erasure. It is a kind rather than a prefixed note so that a reader filtering by '
  'kind can tell a withdrawn decision from a remark, which is migration 13''s argument for '
  '''budget''. Readers with their own allowlist of kinds (web/model.py:644, the console item '
  'screen trail) must learn any word added here or they will silently DROP it.';

-- ---------------------------------------------------------------- the evidence, as a function

-- A FUNCTION AND NOT AN INLINE EXISTS, for migration 42's reason: one place the database's half
-- of this predicate is written, callable by a reader who wants to ask about a row without
-- reproducing it.
--
-- `t.from_agent = brain.current_human()` is not decoration. Without it the withdrawal on the
-- thread could name anybody while the connection is somebody else, which is the forgery
-- migration 36 closed for `accepted_by` arriving one column over. `unaccept work` sets
-- `ctx.actor` to the database's own answer, so the two agree by construction.
--
-- `accepted IS NULL` is the pre-migration-22 half-acceptance: an `accepted_by` with no timestamp,
-- which that guard now refuses to create but which an older row could carry. There is no instant
-- for the withdrawal to be strictly after, so the ordering term is dropped for that case only and
-- the other two terms still stand. Refusing instead would make such a row unwithdrawable forever.
CREATE OR REPLACE FUNCTION brain.work_item_unacceptance_is_recorded(
    item_id text, accepted timestamptz) RETURNS boolean
  LANGUAGE sql STABLE AS $$
  SELECT EXISTS (SELECT 1 FROM brain.thread t
                  WHERE t.work_item_id = item_id
                    AND t.kind = 'unaccept'
                    AND (accepted IS NULL OR t.ts > accepted)
                    AND t.from_agent = brain.current_human());
$$;

GRANT EXECUTE ON FUNCTION brain.work_item_unacceptance_is_recorded(text, timestamptz) TO PUBLIC;

COMMENT ON FUNCTION brain.work_item_unacceptance_is_recorded(text, timestamptz) IS
  'Bus row 0413. Has THIS connection''s human recorded a withdrawal of the acceptance stamped at '
  '`accepted`, on the append-only thread? The one arm of migration 22''s erasure exemption that '
  'is about evidence; the other arm, that brain.current_human() is not NULL at all, is in the '
  'guard itself. STRICTLY after, so a withdrawal written before the acceptance it claims to '
  'undo does not count. `unaccept work` writes the row inside the same transaction as the '
  'erasure and Postgres sees its own writes, so ordering here is against the OLD acceptance''s '
  'timestamp and never against the withdrawal''s own.';

-- ---------------------------------------------------------------- migration 22's guard, replaced
--
-- CREATE OR REPLACE of a function migration 22 defines. Migration 22's FILE is untouched, which
-- is this repo's rule: an applied migration is history and history is amended by a later one.
-- Everything below is byte-identical to migration 22 except the erasure branch, which is marked.
-- A rebuild applies 22 then 43 in version order, so the replacement always lands last.

CREATE OR REPLACE FUNCTION brain.work_item_acceptance_guard() RETURNS trigger
  LANGUAGE plpgsql AS $$
DECLARE
  was_accepted boolean;
  is_accepted  boolean;
  by_changed   boolean;
  at_changed   boolean;
  reopened     boolean;
  is_agent     boolean;
  withdrawn    boolean;
  the_human    text;
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

  -- ------------------------------------------------------------ erased ONLY ON THE RECORD
  --
  -- MIGRATION 43 IS THIS BRANCH AND NOTHING ELSE IN THIS FUNCTION. Migration 22 raised here
  -- unconditionally. Bus row 0413: the operator asked acceptance for an inverse, and item 10 of
  -- web/MUST-NOT-BUILD.md already required one wherever an inverse exists. The refusal stays;
  -- what is added is the one way past it, and it is two arms that a caller must satisfy both of.
  --
  --   arm 1  the withdrawal is on the append-only thread, strictly after this acceptance, and
  --          signed by the human this connection IS.  A record you can silently remove is not a
  --          record; one whose removal is itself a record still is.
  --   arm 2  the connection is a human login at all.  brain.current_human() is NULL for
  --          brain_runtime, the credential every agent surface holds, so no agent process
  --          reaches this branch however it writes the thread.
  --
  -- Reached only when BOTH halves are cleared. `SET accepted_at = NULL` alone and
  -- `SET accepted_by = ''` alone leave is_accepted TRUE and fall into the rewrite branch below,
  -- which is where they were refused before this migration and where they still are.
  IF was_accepted AND NOT is_accepted THEN
    the_human := brain.current_human();
    withdrawn := the_human IS NOT NULL
                 AND brain.work_item_unacceptance_is_recorded(OLD.id, OLD.accepted_at);
    IF NOT withdrawn THEN
      RAISE EXCEPTION 'refusing to erase the acceptance of %: it is recorded as accepted by % at '
                      '%, and this connection (%) filed no withdrawal for it',
                      OLD.id, COALESCE(NULLIF(OLD.accepted_by, ''), '<null>'), OLD.accepted_at,
                      COALESCE(the_human, session_user::text)
        USING HINT = 'A record you can silently remove is not a record. Since migration 43 an '
                     'acceptance CAN be withdrawn, and the way to do it is the `unaccept work` '
                     'verb, which writes an `unaccept` row on the append-only thread with a '
                     'reason on it and then clears the two columns in the same transaction. Two '
                     'things are required and this write had at least one of them missing: a '
                     'thread row of kind `unaccept` for this item, dated after the acceptance '
                     'and signed by brain.current_human(); and a connection the database knows '
                     'as a human at all, which brain_runtime is not. `reopen` remains the way to '
                     'send the WORK back; unaccept withdraws only the decision. ' || HINT_WHY;
    END IF;
    RETURN NEW;
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
                     'path IS permitted here and puts every step on the thread. Since migration '
                     '43 `unaccept work` is the other permitted route, and it is a withdrawal '
                     'that is itself recorded rather than an edit. ' || HINT_WHY;
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
  'and created a work item born accepted, all four at rowcount=1. Migration 43 replaced the '
  'ERASURE branch only: an acceptance may now be withdrawn, but only by a connection '
  'brain.current_human() recognises and only where that human has filed an `unaccept` row on the '
  'append-only thread after the acceptance, which is what `unaccept work` does in one '
  'transaction. Bus row 0413, and web/MUST-NOT-BUILD.md item 10, which requires an inverse verb '
  'wherever a real one exists. It does NOT distinguish the console''s legitimate acceptance from '
  'a rogue agent writing the same UPDATE, because both arrive on the same login; that residual '
  'needs an operator-only credential and is posted as a child of task 0260. It also cannot cover '
  'a restore, which runs with triggers disabled.';

-- ---------------------------------------------------------------- say it in the schema too

COMMENT ON COLUMN brain.work_item.accepted_at IS
  '`done` means the agent reported it finished. Acceptance is a separate act and `reopen` is the '
  'rejection verb. Narrowing WHO accepts never narrows WHETHER it was recorded. Moves together '
  'with accepted_by -- migration 22 refuses half an acceptance -- and is only ever set on a row '
  'in state `done`. CLEARED BY EXACTLY ONE VERB, `unaccept work` (migration 43, bus row 0413), '
  'which files a withdrawal on the append-only thread in the same transaction; the guard refuses '
  'any other erasure, so a record that is removed is still a record of two decisions rather than '
  'of none. Before migration 43 nothing cleared it at all.';

COMMENT ON COLUMN brain.work_item.accepted_by IS
  'The human who decided, as the DATABASE names this connection (brain.current_human(), from '
  'session_user), or brain.auto_acceptor() when the auto-accept rule acted and no human did. '
  'Migration 22 guarded the SHAPE of an acceptance; migration 36 guards WHOSE it is; migration '
  '43 gives it an inverse, `unaccept work`, which is the only writer that may clear this pair '
  'and which records the withdrawal on the thread before doing it. Not a string the caller '
  'picks: measured 2026-08-27, six forged names including zzz-not-a-person were accepted from '
  'brain_runtime before that changed.';

-- ---------------------------------------------------------------- prove it, in the migration
--
-- Migration 13's rule: a migration that says it widened a vocabulary and did not is exactly the
-- class of thing this store's tests exist to catch, so it catches itself. Reads the constraint
-- back out of the catalog, and reads back that the two functions exist with the shapes this file
-- claims for them.

DO $$
DECLARE
  def     text;
  missing text;
BEGIN
  SELECT pg_get_constraintdef(c.oid) INTO def
    FROM pg_constraint c
    JOIN pg_class     t ON t.oid = c.conrelid
    JOIN pg_namespace n ON n.oid = t.relnamespace
   WHERE n.nspname = 'brain' AND t.relname = 'thread' AND c.conname = 'thread_kind_check';

  IF def IS NULL THEN
    RAISE EXCEPTION 'migration 43: thread_kind_check is not on brain.thread after this migration';
  END IF;

  SELECT string_agg(w, ', ') INTO missing
    FROM unnest(ARRAY['post','claim','note','msg','ask','answer','done','block','fail','reopen',
                      'cancel','artifact','heartbeat','reap','set','accept','event','budget',
                      'unaccept']) AS w
   WHERE def NOT LIKE '%''' || w || '''%';

  IF missing IS NOT NULL THEN
    RAISE EXCEPTION 'migration 43: thread_kind_check lost or never gained: %. Definition is %',
                    missing, def;
  END IF;

  IF to_regprocedure('brain.work_item_unacceptance_is_recorded(text, timestamptz)') IS NULL THEN
    RAISE EXCEPTION 'migration 43: the evidence predicate is not installed';
  END IF;

  -- The replacement actually landed. A CREATE OR REPLACE that silently produced migration 22's
  -- body again would leave every refusal in place and this migration claiming an inverse that
  -- does not exist, which is the shape of every confidently-wrong number in this repo.
  IF position('work_item_unacceptance_is_recorded' in
              pg_get_functiondef('brain.work_item_acceptance_guard()'::regprocedure)) = 0 THEN
    RAISE EXCEPTION 'migration 43: work_item_acceptance_guard() does not call the evidence '
                    'predicate, so the erasure exemption is not installed';
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_trigger tg
                   JOIN pg_class c ON c.oid = tg.tgrelid
                   JOIN pg_namespace n ON n.oid = c.relnamespace
                  WHERE n.nspname = 'brain' AND c.relname = 'work_item'
                    AND NOT tg.tgisinternal
                    AND tg.tgname = 'work_item_acceptance_guard') THEN
    RAISE EXCEPTION 'migration 43: work_item_acceptance_guard is not on brain.work_item. '
                    'Migration 22 creates the trigger; this file only replaces the function it '
                    'runs, so a missing trigger means 22 was never applied here.';
  END IF;
END $$;

INSERT INTO brain.schema_migration (version, name)
     VALUES (43, '0043_acceptance_has_an_inverse')
  ON CONFLICT (version) DO NOTHING;

COMMIT;
