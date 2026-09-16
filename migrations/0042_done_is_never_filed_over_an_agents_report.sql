-- migration 42: a `done` report is never filed over an agent's, by any caller
--
-- LEDGER VERSION 42, read from brain.schema_migration and not from this directory. Live `brain`
-- is at 33 and the tree carries 34..38 and 40..41 unapplied; 39 is a hole lane E left and it is
-- not mine to fill. Verified free on live and on brain_lane_j on 2026-08-27 before this was
-- written, per queue/schema/0015's own rule: pick the next version by READING the ledger.
--
-- Bus row `0399`, wave 3 lane J. Additive and re-runnable: two functions, one trigger, two
-- COMMENTs, one index. It drops nothing, edits no applied file, changes no column type, and
-- BACKFILLS NOTHING -- there is nothing to infer here, only writes to refuse from now on.
--
-- NOT APPLIED TO LIVE when this line was written. Migration 19 in this directory carries an
-- "applied clean on live" sentence that was never true, so treat any such claim as a thing to
-- re-read from `brain.schema_migration` before relying on it.
--
-- ================================================================= WHAT IS OPEN
--
-- `MUST-NOT-BUILD.md` item 1, in the prohibition's own words: *"An operator `done` on an agent's
-- work item fabricates the agent's report."* `web/rooms.py::assert_allowed_on` is the guard that
-- exists to prevent it and it permitted `done` where
--
--     NOT agent_claimable   AND   coalesce(claimed_by, '') = ''
--
-- Both of those are statements about a row's FUTURE. `agent_claimable` says whether the fleet may
-- TAKE it; `claimed_by` says whether an agent is holding it AT THIS INSTANT. Neither can see that
-- an agent has already worked it and already filed a report, and `reopen` empties the second one
-- BY DESIGN as part of sending work back.
--
-- So: press `Send back` on an agent's finished work item in the Judge tier. The server runs
-- `reopen`. The card stays on screen and its primary control has silently changed from
-- `Accept work` to `Mark my task done`. Press that, type a summary, and `work_item.result` --
-- the column every later surface reads as WHAT THE AGENT REPORTED -- now holds the operator's
-- sentence, and the row leaves the queue as done, so the rework he asked for two seconds earlier
-- never happens and nothing says so.
--
-- Reproduced 2 of 2 in a real browser at commit 6b5fa01 against brain_lane_j, on the repo's own
-- seeded rows 0001 and 0002. Quoted in outputs/2026-08-27-J-0399/.
--
-- ================================================================= WHY A TRIGGER AND NOT ONLY
-- ================================================================= THE PYTHON GUARD
--
-- Because `web/rooms.py` is not on every path, and the CLI reaches the same forgery with none of
-- it. MEASURED on brain_lane_j, 2026-08-27, ledger 41, through registered verbs only:
--
--     store.apply("set",    id='0002', key='agent_claimable', value='false', agent='operator')
--     store.apply("reopen", id='0002', reason='re-walk it', agent='operator')
--     store.apply("done",   id='0002', summary='CLI probe: the operator files T6 report for it.',
--                           agent='operator')
--       -> {'id': '0002', 'state': 'done', 'artifacts': 1, ...}
--       -> work_item.result = "CLI probe: the operator files T6 report for it."
--
-- `_hold` did not refuse it: it gates `active` rows only, and `reopen` had just moved the row to
-- `inbox`. That is task 0377's lesson with a different subject -- a check the database does not
-- back holds only for polite callers -- so the floor goes here, where every caller stands on it.
--
-- ================================================================= THE PREDICATE, AND THE THREE
-- ================================================================= COLUMNS IT IS NOT
--
-- The question is DID AN AGENT EVER WORK THIS ROW. Measured read-only on live `brain` 2026-08-27
-- (310 work items, 172 of them satisfying the old two-term predicate):
--
--     7 of 172   a brain.run row exists                        <- arm 1
--     7 of 172   a `claim` on the append-only brain.thread     <- arm 2, the same seven
--    11 of 172   a done/fail/block on the thread from a name that is not `operator`
--    14 of 172   the union of the three
--   140 of 172   posted_by is not the operator         REJECTED: free text any caller sets, and
--                                                      41 of them say `admiral` over rows no
--                                                      agent ever touched. Migration 26 rejected
--                                                      this column for the same reason.
--     4 of 172   the reporter is in brain.agent        REJECTED: that table holds fleet TERMINALS
--                                                      only. The seven rows this program's own
--                                                      lane subagents worked on 2026-08-27
--                                                      (0371-0375, 0379, 0382, reported by
--                                                      `lane-B`) are not in it.
--     0 of 172   actor_type                            REJECTED: nullable, and migration 26
--                                                      rejected it on measurement: "unset is a
--                                                      real state".
--
-- THIS TRIGGER CARRIES ARMS 1 AND 2 ONLY, and the omission of arm 3 is a decision rather than an
-- oversight. Arm 3 is the identity-bearing one: "reported on by somebody who is not the human
-- asking". A trigger does not know who is asking. An identity-FREE version of it -- "a `done`
-- already exists on the thread" -- would refuse the operator's own second `done` after his own
-- `reopen`, which is the accept -> reopen -> done -> accept loop migration 22 exists in writing
-- to protect, and 1 of the 172 live rows already carries a `done` from `operator`. So arm 3 lives
-- in `web/rooms.py::agent_work_on`, where the acting human is known, and the two identity-free
-- arms live here where every caller passes. `web/tests/test_done_is_not_a_forgery.py` asserts the
-- two implementations agree on the arms they share, over the whole table, so they cannot drift.
--
-- Arms 1 and 2 CANNOT fire on the operator's own work, and that is what makes this a guard rather
-- than a ban:
--   * `brain.run` is written by `run start`, which is the runner's verb. No console path reaches
--     it and the operator has no runner.
--   * `claim` is refused unless `agent_claimable` is true, and migration 26's first trigger
--     coerces that to false on every `actor_type = 'human'` row. The operator's own work can
--     never carry a claim.
-- Arm 2 is not redundant with arm 1: on live, 122 rows carry a claim on the thread and 121 carry
-- a run.
--
-- ================================================================= WHEN IT FIRES, AND THE THREE
-- ================================================================= WRITES IT MUST NOT TOUCH
--
--   1. THE AGENT'S OWN REPORT. `_finish` reaches this UPDATE only through `_hold`, which admits
--      only the HOLDER of an `active` row, so every legitimate agent `done` arrives with
--      OLD.state = 'active'. That is the exemption, and it is read off the row rather than off
--      the caller.
--   2. AN ACCEPTANCE. `accept work` writes `accepted_by` and `accepted_at` on a row that is
--      already `done` and touches neither `state` nor `result`. Refusing it would be the worst
--      available failure here: the operator unable to accept an agent's work BECAUSE an agent did
--      it. So this fires only on a write that actually FILES A REPORT -- one that moves `state`
--      or rewrites `result`.
--   3. EVERY OTHER VERB. `fail`, `block`, `reopen` and `cancel` all write a state that is not
--      `done` and return on the first line. `set priority`, `set lane`, the image verbs and the
--      queue verbs touch no column this looks at.
--
-- WHAT IT WOULD HAVE REFUSED IN THE WHOLE OF LIVE HISTORY: nothing. Measured on `brain.thread`,
-- which is append-only, by reading the kind immediately before each of the 127 `done` events:
--
--     rows with a `reopen` and a later `done` and no `claim` between them  ..... 0
--     rows with a `block`  and a later `done` and no `claim` between them  ..... 0 of 8
--
-- The forgery has never been committed on this store. This refuses it before the first time.
--
-- ================================================================= WHAT IT DOES NOT CLOSE
--
-- `swarm done <id> --force` by the operator on an ACTIVE row an agent is holding. That arrives
-- with OLD.state = 'active' and is exempt here by the rule above. It is a different shape: it is
-- signed, it names the displaced holder on the thread, and the console cannot reach it at all
-- (`assert_allowed_on` refuses a non-empty `claimed_by`). Stated rather than hidden.
--
-- ================================================================= ROLLBACK, STATED
--
--     BEGIN;
--       DROP TRIGGER IF EXISTS work_item_done_is_not_a_forgery ON brain.work_item;
--       DROP FUNCTION IF EXISTS brain.work_item_done_is_not_a_forgery();
--       DROP FUNCTION IF EXISTS brain.work_item_agent_has_worked(text);
--       DELETE FROM brain.schema_migration WHERE version = 42;
--     COMMIT;
--
-- Executed on brain_lane_j on 2026-08-27 and re-applied afterwards, so the rollback is a thing
-- that has run and not a paragraph.

\set ON_ERROR_STOP on

BEGIN;

-- ---------------------------------------------------------------- the predicate, as a function

-- A FUNCTION AND NOT AN INLINE EXISTS, so there is ONE place the database's half of this
-- predicate is written and a reader can call it to ask about a row without reproducing it.
CREATE OR REPLACE FUNCTION brain.work_item_agent_has_worked(item_id text) RETURNS boolean
  LANGUAGE sql STABLE AS $$
  SELECT EXISTS (SELECT 1 FROM brain.run r WHERE r.work_item_id = item_id)
      OR EXISTS (SELECT 1 FROM brain.thread t
                  WHERE t.work_item_id = item_id AND t.kind = 'claim');
$$;

COMMENT ON FUNCTION brain.work_item_agent_has_worked(text) IS
  'Has an agent already worked this row? Two identity-free arms: a brain.run row (written by '
  '`run start`, which is the runner''s verb and which no console path reaches) and a `claim` on '
  'the append-only brain.thread (`claim` is refused unless agent_claimable is true, and '
  'migration 26''s first trigger forces that false on every actor_type=''human'' row). Neither '
  'can fire on the operator''s own work, which is what makes the guard above it a guard rather '
  'than a ban. NOT agent_claimable and NOT claimed_by: both of those are statements about a '
  'row''s future, and `reopen` clears the second by design, which is bus row 0399. The third, '
  'identity-bearing arm -- reported on by somebody who is not the human asking -- is in '
  'web/rooms.py::agent_work_on, because a trigger does not know who is asking and an '
  'identity-free version of it would refuse the operator''s own second `done` after his own '
  '`reopen`.';

-- The two arms are two lookups per refusable write. `run` already has UNIQUE (work_item_id,
-- attempt) and `thread` already has thread_item_idx (work_item_id, seq); this one covers arm 2's
-- kind filter so the claim probe does not read every thread row on a long-lived item.
CREATE INDEX IF NOT EXISTS thread_claim_idx
  ON brain.thread (work_item_id) WHERE kind = 'claim';

-- ---------------------------------------------------------------- the trigger

CREATE OR REPLACE FUNCTION brain.work_item_done_is_not_a_forgery() RETURNS trigger
  LANGUAGE plpgsql AS $$
BEGIN
  -- Not a `done` at all. `fail`, `block`, `reopen` and `cancel` all leave here.
  IF NEW.state IS DISTINCT FROM 'done' THEN
    RETURN NEW;
  END IF;

  -- The holder's own report. `_finish` reaches this UPDATE only through `_hold`, which admits
  -- only the agent holding an `active` row, so this is exactly the legitimate agent `done`.
  IF OLD.state IS NOT DISTINCT FROM 'active' THEN
    RETURN NEW;
  END IF;

  -- Not a report: an acceptance, a reprioritisation, an image pointer. `accept work` writes
  -- accepted_by and accepted_at on an already-done row and must never be refused here.
  IF NEW.state IS NOT DISTINCT FROM OLD.state
     AND NEW.result IS NOT DISTINCT FROM OLD.result THEN
    RETURN NEW;
  END IF;

  IF NOT brain.work_item_agent_has_worked(NEW.id) THEN
    RETURN NEW;
  END IF;

  RAISE EXCEPTION
    'refusing to file a `done` report on % over an agent''s work: the row was worked by an agent '
    '(brain.run rows: %, claims on the thread: %) and no agent is holding it now, so this write '
    'would replace that agent''s report in work_item.result with somebody else''s words and take '
    'the row off the queue as finished',
    NEW.id,
    (SELECT count(*) FROM brain.run r WHERE r.work_item_id = NEW.id),
    (SELECT count(*) FROM brain.thread t WHERE t.work_item_id = NEW.id AND t.kind = 'claim')
    USING HINT =
      'MUST-NOT-BUILD item 1: an operator `done` on an agent''s work item fabricates the agent''s '
      'report. If the work is finished and good, the verb is `accept work` (swarm accept-work '
      '<id> --by <you>), which is a separate act by design (D00 rule 3). If it is not, `reopen` '
      'sends it back -- and note that reopen CLEARS claimed_by, which is why the row''s own '
      'columns can look like the operator''s own work one statement after a send-back. That is '
      'bus row 0399. If nothing will pick the row up because it was taken back from the fleet, '
      '`swarm set <id> agent_claimable true --as-operator` hands it back. An agent reporting its '
      'OWN work is never refused here: it arrives holding an active row.';
END $$;

COMMENT ON FUNCTION brain.work_item_done_is_not_a_forgery() IS
  'Bus row 0399. Refuses a write that files a `done` report on a row an agent has worked while '
  'no agent is holding it. Exempts, by construction rather than by name: the agent''s own report '
  '(OLD.state = ''active'', which only the holder can reach through _hold), an acceptance (state '
  'and result both unchanged), and every row no agent ever worked, which is the operator''s own '
  'queue and is 158 of the 172 exposed live rows. Measured against the whole of live history on '
  '2026-08-27: it would have refused 0 of the 127 `done` events ever written to this store.';

DROP TRIGGER IF EXISTS work_item_done_is_not_a_forgery ON brain.work_item;
CREATE TRIGGER work_item_done_is_not_a_forgery
  BEFORE UPDATE ON brain.work_item
  FOR EACH ROW
  EXECUTE FUNCTION brain.work_item_done_is_not_a_forgery();

INSERT INTO brain.schema_migration (version, name)
     VALUES (42, '0042_done_is_never_filed_over_an_agents_report')
  ON CONFLICT (version) DO NOTHING;

COMMIT;
