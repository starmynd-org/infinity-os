-- ledger 49: `brain.queue_open` carries the continuous impact, on all four arms.
--
-- Row 0434, his ask 4, the second half. Migration 48 put `impact` and `impact_magnitude` on
-- `brain.work_item_signals`; this file is what makes them reach the human queue, because
-- `queue_open` is what `human_queue.reads.queue()` selects and it names its columns one by one.
--
-- THE PREFIX IN THE FILENAME IS A PER-LANE COUNTER AND IT LIES. This is `queue/schema/0017` and
-- it records ledger version 49. `queue/schema/0016` records 33. Reading the prefix as the version
-- is the mistake `engine/bin/scratch-db.sh` documents at length and migration 24 was renumbered
-- for.
--
-- APPENDED, NEVER REORDERED. CREATE OR REPLACE VIEW permits adding columns at the end and permits
-- nothing else, so the two new columns go last on every arm and every existing caller of this view
-- is untouched by construction rather than by inspection.
--
-- ARMS 3 AND 4 COALESCE AND ARMS 1 AND 2 DO NOT, and the asymmetry is the existing shape of this
-- view rather than a new decision. Arms 1 and 2 INNER JOIN `work_item_signals`, so a row that
-- reaches them has signals. Arms 3 and 4 LEFT JOIN it: a question with no work item, or a
-- recommendation about a session, resolves to NULL there, which is exactly why those two arms
-- already coalesce `external` and `canon_touching`. A NULL magnitude would be worse than a NULL
-- flag: it propagates through the urgency times impact product and takes the whole score to NULL,
-- which sorts unpredictably rather than loudly. So the coalesce routes through
-- `brain.impact_magnitude(NULL)` rather than through a typed literal, because that function is
-- the one definition of what conservative means and a second copy of `2.0` here would be a second
-- definition free to drift from it.
--
-- APPLY AS: the bootstrap superuser (`postgres`), against `brain`, via
-- `store/bin/apply-migration.sh`. NOT as `brain_owner`: measured 2026-08-31, all 40 tables
-- in the `brain` schema are owned by `postgres`, and applying as `brain_owner` dies mid-file
-- on `must be owner of table work_item`. Every apply doc in this repo said otherwise and was
-- wrong. Rehearsed 47 -> 52 on `brain_baseline`, a store built from HEAD at ledger 47.

\set ON_ERROR_STOP on

DO $$
DECLARE taken text;
BEGIN
  SELECT name INTO taken FROM brain.schema_migration WHERE version = 49;
  IF taken IS NOT NULL AND taken <> '0017_queue_open_carries_impact' THEN
    RAISE EXCEPTION 'ledger version 49 is already held by %', taken
      USING HINT = 'Renumber this file to the next free version and re-run.';
  END IF;
END $$;

-- The dependency, named rather than assumed. Applying this file against a store that has not had
-- migration 48 produces a view referencing a column that does not exist, and the error would name
-- `impact_magnitude` without saying which file was supposed to create it.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                  WHERE table_schema = 'brain' AND table_name = 'work_item_signals'
                    AND column_name = 'impact_magnitude') THEN
    RAISE EXCEPTION 'brain.work_item_signals has no impact_magnitude, so queue_open cannot carry it'
      USING HINT = 'Apply migrations/0048_impact_is_continuous.sql first. It is ledger 48.';
  END IF;
  -- ARM 3 CARRIES A PREDICATE THAT LIVES IN migrations/0031, NOT IN THIS DIRECTORY. Named here so
  -- a store without it fails on a sentence rather than on `column q.withdrawn_at does not exist`.
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                  WHERE table_schema = 'brain' AND table_name = 'question'
                    AND column_name = 'withdrawn_at') THEN
    RAISE EXCEPTION 'brain.question has no withdrawn_at, which arm 3 of this view requires'
      USING HINT = 'Apply migrations/0031_question_withdrawal.sql first. It is ledger 31.';
  END IF;
END $$;

BEGIN;

CREATE OR REPLACE VIEW brain.queue_open AS
  -- 1. finished agent work awaiting a human acceptance.
  SELECT 'work_item'::text AS source_type, w.id AS source_id,
         w.title, w.lane AS source_lane, w.claimed_by AS producer,
         'review'::text AS default_item_class, 'Accept work'::text AS primary_verb,
         w.priority, w.finished_at AS surfaced_at, w.created,
         s.external, s.canon_touching, s.reversibility, s.urgency, s.stakes,
         s.charter_alignment, s.dependency_unblocking, s.confidence,
         w.depends_on, w.id AS work_item_id, w.session_id,
         s.impact, s.impact_magnitude
    FROM brain.work_item w
    JOIN brain.work_item_signals s ON s.id = w.id
   WHERE w.state = 'done' AND w.accepted_at IS NULL
  UNION ALL
  -- 2. THE OPERATOR'S OWN QUEUE, which is everything the fleet may not take (queue schema 0013).
  SELECT 'work_item', w.id, w.title, w.lane, w.posted_by,
         'blocker', 'Mark my task done',
         w.priority, w.created, w.created,
         s.external, s.canon_touching, s.reversibility, s.urgency, s.stakes,
         s.charter_alignment, s.dependency_unblocking, s.confidence,
         w.depends_on, w.id, w.session_id,
         s.impact, s.impact_magnitude
    FROM brain.work_item w
    JOIN brain.work_item_signals s ON s.id = w.id
   WHERE NOT w.agent_claimable AND w.state IN ('inbox', 'active')
  UNION ALL
  -- 3. open questions. These are where a stated default lives, so this arm is what the
  --    null-branch gate protects. Withdrawn is not open (migration 31, task 0159).
  --
  --    `AND q.withdrawn_at IS NULL` IS CARRIED FORWARD FROM migrations/0031, AND THE FIRST VERSION
  --    OF THIS FILE DROPPED IT. Migration 31 predicted this in writing, addressed to exactly this
  --    lane: "this view now has a predicate that lives in migrations/, not in your directory.
  --    Re-issuing 0014's body from a later queue migration would silently take it back out, and
  --    the symptom is a cancelled task's question reappearing on the operator's card."
  --
  --    That is what happened. This file was built from 0014's body, the line was not in it, and
  --    `test_cancel_withdraws_questions.py` went red on `after: no card` within the hour. It is
  --    not a cosmetic reappearance: `brain.queue_pending_default` reads the same predicate, so a
  --    checkpoint could FIRE the default of a question belonging to a task that no longer exists,
  --    which is an automatic act taken on behalf of nothing.
  SELECT 'question', q.id, q.text, coalesce(w.lane, ''), q.asked_by,
         'approval', 'Accept default',
         coalesce(w.priority, 1), q.asked_at, q.asked_at,
         coalesce(s.external, false), coalesce(s.canon_touching, false),
         s.reversibility, s.urgency, s.stakes, s.charter_alignment,
         s.dependency_unblocking, s.confidence,
         coalesce(w.depends_on, ''), q.work_item_id, '',
         s.impact, coalesce(s.impact_magnitude, brain.impact_magnitude(NULL))
    FROM brain.question q
    LEFT JOIN brain.work_item w ON w.id = q.work_item_id
    LEFT JOIN brain.work_item_signals s ON s.id = q.work_item_id
   WHERE q.answer IS NULL
     AND q.withdrawn_at IS NULL
  UNION ALL
  -- 4. open recommendations that require a human.
  SELECT 'recommendation', r.id::text, r.text, '', coalesce(r.produced_by, ''),
         'approval', 'Approve',
         2, r.created_at, r.created_at,
         coalesce(s.external, false), coalesce(s.canon_touching, false),
         s.reversibility, s.urgency, s.stakes, s.charter_alignment,
         s.dependency_unblocking, s.confidence,
         coalesce(w.depends_on, ''),
         CASE WHEN r.subject_type = 'work_item' THEN r.subject_id END,
         coalesce(r.cites_session_id, ''),
         s.impact, coalesce(s.impact_magnitude, brain.impact_magnitude(NULL))
    FROM brain.recommendation r
    LEFT JOIN brain.work_item w
           ON r.subject_type = 'work_item' AND w.id = r.subject_id
    LEFT JOIN brain.work_item_signals s
           ON r.subject_type = 'work_item' AND s.id = r.subject_id
   WHERE r.state = 'open'
     AND NOT EXISTS (SELECT 1 FROM brain.queue_item_option o
                      WHERE o.recommendation_id = r.id);

COMMENT ON VIEW brain.queue_open IS
  'The human queue: one view over four arms of three existing tables. There is no queue table '
  'and there must not be one. Arm 2, the operator''s own work, is NOT w.agent_claimable: the '
  'exact complement of the predicate engine claim applies (queue schema 0013, task 0421). Arm 4 '
  'excludes a recommendation claimed by brain.queue_item_option. Since ledger 49 every arm also '
  'carries impact and impact_magnitude, the continuous value half of the ranking spine, coalesced '
  'to the conservative magnitude on the two arms whose signals join is outer.';

-- ------------------------------------------------------------------ the proof, watched running

DO $$
DECLARE n int := 0; nulls int; probe_q text;
BEGIN
  PERFORM 1 FROM information_schema.columns
    WHERE table_schema = 'brain' AND table_name = 'queue_open'
      AND column_name IN ('impact', 'impact_magnitude') HAVING count(*) = 2;
  IF NOT FOUND THEN RAISE EXCEPTION 'queue_open is missing impact or impact_magnitude'; END IF;
  n := n + 1;

  -- THE PREDICATE THIS FILE ONCE DROPPED, PROVEN PRESENT BY WATCHING IT WORK.
  --
  -- Not `is the line in the source`, which is what a reviewer does and what missed it the first
  -- time. A withdrawn question is INSERTED here and the view is required not to return it. Any
  -- future re-issue of this body that forgets `AND q.withdrawn_at IS NULL` fails at apply time
  -- rather than silently putting a cancelled task's question back on his card.
  -- `brain.question.id` is CHECKed against '^q[0-9]{4,}$', so the probe id has to look like a
  -- real question id. A nine-digit one cannot collide with anything the sequence will ever mint,
  -- and it is deleted below either way. The sequence is NOT touched: minting a real id for a probe
  -- is the hole in his task numbering that migration 48's own probe had to be fixed for.
  probe_q := 'q999999949';
  -- `question_withdrawn_signed_ck` requires a signature on any withdrawal: a withdrawn question
  -- with nobody's name on it is exactly the unattributed act migration 31 refuses. The probe
  -- signs itself rather than working around the constraint.
  INSERT INTO brain.question (id, asked_by, text, asked_at, withdrawn_at, withdrawn_by)
    VALUES (probe_q, 'migration', 'a withdrawn question, never committed', now(), now(),
            'ledger-49-probe');
  IF EXISTS (SELECT 1 FROM brain.queue_open
              WHERE source_type = 'question' AND source_id = probe_q) THEN
    RAISE EXCEPTION 'a WITHDRAWN question appears in brain.queue_open. Arm 3 has lost '
                    '`AND q.withdrawn_at IS NULL`, which migrations/0031 owns and which a re-issue '
                    'of queue/schema/0014''s body silently removes. See migration 31''s own note '
                    'to the next lane to touch this directory.';
  END IF;
  n := n + 1;

  -- AND THE POSITIVE CONTROL, because "the view returned nothing" is also what a broken view
  -- prints. The same question, not withdrawn, MUST appear.
  UPDATE brain.question SET withdrawn_at = NULL WHERE id = probe_q;
  IF NOT EXISTS (SELECT 1 FROM brain.queue_open
                  WHERE source_type = 'question' AND source_id = probe_q) THEN
    RAISE EXCEPTION 'an OPEN question does not appear in brain.queue_open, so the check above '
                    'proved nothing: arm 3 is returning no questions at all.';
  END IF;
  n := n + 1;
  DELETE FROM brain.question WHERE id = probe_q;

  -- THE ONE THAT MATTERS: no arm may produce a NULL magnitude, because a NULL propagates through
  -- the product and takes the score with it. Asserted over the whole live view rather than over
  -- an arm somebody remembered to check.
  SELECT count(*) INTO nulls FROM brain.queue_open WHERE impact_magnitude IS NULL;
  IF nulls <> 0 THEN
    RAISE EXCEPTION '% queue_open row(s) carry a NULL impact_magnitude', nulls; END IF;
  n := n + 1;

  -- and it is inside the range the fold promises
  SELECT count(*) INTO nulls FROM brain.queue_open
    WHERE impact_magnitude < 0.25 OR impact_magnitude > 4.0;
  IF nulls <> 0 THEN
    RAISE EXCEPTION '% queue_open row(s) fall outside [0.25, 4.0]', nulls; END IF;
  n := n + 1;

  RAISE NOTICE 'ledger 49: % of 5 checks passed over % queue_open row(s), including a withdrawn '
               'question watched NOT appearing and an open one watched appearing',
               n, (SELECT count(*) FROM brain.queue_open);
  IF n <> 5 THEN RAISE EXCEPTION 'ledger 49: expected 5 checks, ran %', n; END IF;
END $$;

INSERT INTO brain.schema_migration (version, name)
  VALUES (49, '0017_queue_open_carries_impact')
  ON CONFLICT (version) DO NOTHING;

COMMIT;
