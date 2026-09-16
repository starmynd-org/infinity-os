-- queue schema 0014, ledger version 30: two to four DRAFTED OPTIONS per queue item, each one a
-- proposal that only a human can execute.
--
-- Task 0165 (V3, the action layer). Additive and re-runnable: one CREATE TABLE, one
-- CREATE OR REPLACE VIEW, three COMMENTs, one ledger row. It drops nothing, edits no applied
-- file, adds no column to an existing table and changes no column type.
--
-- ================================================================= WHAT THIS IS FOR
--
-- `brain.queue_item` carries a SINGULAR `recommended_option` and its `counterargument`. The
-- operator asked for two to four choices per item -- "spin up an AI to do it", "spin up an AI to
-- help", "a fast model or a deeper one with a bigger prompt", "scope it out as a full swarm
-- sprint" -- drafted by an AI rather than written by hand, and choosing one has to DISPATCH.
--
-- ================================================================= AN OPTION IS A RECOMMENDATION
--
-- The one thing this file refuses to build is a second path from a proposal to executed work.
-- `recommend accept` is where `requires_human` is enforced, and
-- `queue/tests/test_no_self_execution.py` is the named test over seven routes into it. A drafted
-- option that dispatched through anything else would be an eighth route that test does not cover,
-- and the option layer would have quietly become the thing the criterion exists to forbid.
--
-- So a drafted option IS a `brain.recommendation` row and this table is the FACETS beside it:
--
--     recommendation.text              the drafted plan            (C section 1.2 `plan`)
--     recommendation.rationale         the counterargument         (C section 1.2, REQUIRED)
--     recommendation.proposed_action   the title of the task it would post
--     recommendation.template_id       per option, as today
--     recommendation.spawned_work_item the dispatched task -- written ONLY by `recommend accept`
--     recommendation.decided_by/_at    who chose it and when, actor_type forced to 'human'
--
-- and this table adds only what a recommendation has nowhere to put: which of the four kinds the
-- option is, who does it, how much thinking, what size, what it is estimated to cost and take,
-- which lane it posts into, and whether it is the drafted recommendation of the set.
--
-- THERE IS NO `verb` COLUMN AND ITS ABSENCE IS DELIBERATE. C section 1.2 lists one. A verb name
-- in a producer-written column is a verb the console would have to trust a drafting agent to
-- name, and the console's whole shape is that a room's verbs come from a frozen allowlist in
-- `web/rooms.py`. `kind` is the stored fact; `web/model.py::OPTION_VERB` maps the four kinds onto
-- the console's labels in one place. A producer can propose a KIND. It cannot name a verb.
--
-- ================================================================= WHY THE VIEW CHANGES
--
-- `brain.queue_open`'s fourth arm surfaces EVERY open recommendation as its own queue card. Four
-- drafted options would therefore arrive as four extra cards in the operator's queue on top of
-- the item they belong to -- the queue flooding itself with the component built to keep it small.
-- An option is rendered ON its item's card, so arm 4 now excludes any recommendation this table
-- claims. That is a fact about the queue and not a rendering trick: `queue ls` and the console
-- read the same view, and hiding the rows in the console alone is exactly the two-surfaces-
-- disagree defect this store is built against.
--
-- DEPENDS ON queue schema 0013 (ledger version 28) and REFUSES without it, in words. Arms 1-3
-- below are reproduced from 0013 byte for byte -- CREATE OR REPLACE VIEW has no partial form --
-- and applying this file over 0007's older arm 2 would silently REVERT task 0421's widening,
-- putting the operator's two unclassified rows (0068, 0071) back on no surface at all. A
-- migration that quietly undoes another one is worse than one that refuses to run.

\set ON_ERROR_STOP on

BEGIN;

-- ---------------------------------------------------------------- refuse before 28, in words

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM brain.schema_migration WHERE version = 28) THEN
    RAISE EXCEPTION 'queue schema 0013 (ledger version 28) is not applied, so this file cannot '
                    'reproduce brain.queue_open without reverting it'
      USING HINT = 'Apply queue/schema/0013_queue_open_shows_what_the_fleet_will_not_take.sql '
                   'first. This file reproduces that view whole, because CREATE OR REPLACE VIEW '
                   'has no partial form; running it over 0007''s older arm 2 would restore '
                   'w.actor_type = ''human'' and hide the operator''s unclassified rows again. '
                   'Refusing here rather than doing that silently.';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.tables
                  WHERE table_schema = 'brain' AND table_name = 'recommendation') THEN
    RAISE EXCEPTION 'brain.recommendation does not exist, and a drafted option IS a '
                    'recommendation: there is nowhere for the plan, the counterargument or the '
                    'human decision to live'
      USING HINT = 'Apply migrations/0001_initial.sql and queue/schema/0007_queue.sql first.';
  END IF;
END $$;

-- ---------------------------------------------------------------- the options

CREATE TABLE IF NOT EXISTS brain.queue_item_option (
  id                bigserial PRIMARY KEY,
  -- The queue's own address for a row, the same triple `queue_item`, `queue_defer` and
  -- `queue_bump` use. Deliberately the same three words: an option set belongs to a queue ITEM,
  -- and a fourth spelling of "which row" is a fourth place the console can address the wrong one.
  source_type       text    NOT NULL CHECK (source_type IN ('work_item','question','recommendation')),
  source_id         text    NOT NULL,
  -- 1..4. The operator asked for "sometimes 2, sometimes 4"; the ceiling is in the column because
  -- five options is not a choice, it is a form. The FLOOR of two is not expressible per row and
  -- is enforced by `queue draft options`, which writes a whole set in one transaction.
  n                 int     NOT NULL CHECK (n >= 1 AND n <= 4),
  -- The proposal itself. NOT NULL: an option with no recommendation behind it would be an option
  -- with no path to execution, which is a button that cannot work.
  recommendation_id bigint  NOT NULL REFERENCES brain.recommendation(id),
  kind              text    NOT NULL CHECK (kind IN ('agent-does','agent-helps','agent-deep','sprint')),
  label             text    NOT NULL CHECK (btrim(label) <> ''),
  who               text    NOT NULL DEFAULT '',
  thinking          text    NOT NULL DEFAULT '',
  size              text    NOT NULL DEFAULT '',
  -- WHICH LANE THE DISPATCHED TASK IS POSTED INTO, and this is the whole of "fast model versus
  -- deep model". `model`, `effort` and `engine` are per-AGENT config already
  -- (`~/.brain-runtime/config.json`); the lane is what decides which agent profile may claim the
  -- row (`swarm claim` filters on it, `swarm-run` reads CFG_MODEL and CFG_EFFORT from the
  -- profile). So an option does not carry a model name -- it carries the lane, and the model and
  -- effort are READ BACK from the config that will actually run it. A model name stored here
  -- would be a second copy of a config value, free to drift from the one the runner obeys, and
  -- the card would state a model no run ever used.
  lane              text    NOT NULL DEFAULT '',
  -- Estimates, and they are the DRAFTING agent's. Nullable on purpose: C section 1.6 renders a
  -- missing cost as a finding chip rather than blocking, and a NOT NULL here would teach drafting
  -- agents to write a plausible zero. `~` is added by the renderer, never stored.
  cost_est          numeric CHECK (cost_est IS NULL OR cost_est >= 0),
  time_est          text    NOT NULL DEFAULT '',
  -- Exactly one option per set may be the drafted recommendation (partial unique index below).
  -- It buys no privilege on the card: same row height, same one tap. The uniform cost is what
  -- stops the other options being decoration.
  recommended       boolean NOT NULL DEFAULT false,
  drafted_by        text    NOT NULL DEFAULT '',
  drafted_at        timestamptz NOT NULL DEFAULT now(),
  -- CALIBRATION. The estimate above is a claim; these two are what happened. They are the same
  -- shape as `brain.queue_calibration`'s declared/observed pair and exist for the same reason: a
  -- producer whose estimates drift gets caught by measurement rather than by impression. Written
  -- after the dispatched run finishes; NULL means not measured yet and never means zero.
  cost_actual       numeric CHECK (cost_actual IS NULL OR cost_actual >= 0),
  minutes_actual    numeric CHECK (minutes_actual IS NULL OR minutes_actual >= 0),
  -- One row per recommendation: an option is a proposal and a proposal belongs to one option.
  -- This is also what makes the arm-4 exclusion below a lookup rather than a scan.
  UNIQUE (recommendation_id),
  UNIQUE (source_type, source_id, n)
);

-- ---------------------------------------------------------------- a draft that FAILED
--
-- C section 1.6b makes "option drafting failed" a normative card state: an amber block, word
-- first, with the legacy single-option card beneath it as the fallback. Amber and not red -- the
-- queue item is intact and resolvable, and nothing recorded as present is gone.
--
-- WITHOUT SOMEWHERE TO WRITE IT, THAT STATE COULD NOT BE REACHED, and a card state nothing can
-- produce is decoration in a template. So a drafting agent whose attempt died records it here,
-- through the same verb, and the card renders the sentence the agent wrote rather than a generic
-- one. Absence of a draft (nobody tried) and a failed draft (somebody tried and could not) are
-- different facts and the operator is owed the difference: `options_drafted_at` is what tells
-- them apart, and a NULL there means nobody has tried, not that the drafter found nothing.
ALTER TABLE brain.queue_item
  ADD COLUMN IF NOT EXISTS options_drafted_at timestamptz,
  ADD COLUMN IF NOT EXISTS options_error      text;

COMMENT ON COLUMN brain.queue_item.options_error IS
  'One line from a drafting attempt that failed, written by `queue draft options --failed`. The '
  'card renders it as an amber finding with the legacy single-option card beneath. NULL with a '
  'NULL options_drafted_at means nobody has tried, which is a different fact.';

CREATE UNIQUE INDEX IF NOT EXISTS queue_item_option_one_recommendation
  ON brain.queue_item_option (source_type, source_id) WHERE recommended;

CREATE INDEX IF NOT EXISTS queue_item_option_source_idx
  ON brain.queue_item_option (source_type, source_id, n);

COMMENT ON TABLE brain.queue_item_option IS
  'Two to four drafted options per queue item. Each option IS a brain.recommendation -- the plan '
  'is its text, the counterargument its rationale, the dispatched task its spawned_work_item -- '
  'so choosing one runs `recommend accept`, the single place requires_human is enforced, and no '
  'second path from a proposal to executed work exists. This table carries only the facets a '
  'recommendation has nowhere to put. There is no verb column: a producer proposes a KIND and '
  'the console maps kinds to its own verbs.';

COMMENT ON COLUMN brain.queue_item_option.lane IS
  'The lane the dispatched task is posted into, and the whole of the fast-versus-deep choice. '
  'model/effort/engine are per-agent config; the lane decides which agent profile may claim the '
  'row. Storing a model name here would be a second copy of a config value, free to drift from '
  'the one swarm-run obeys.';

COMMENT ON COLUMN brain.queue_item_option.recommended IS
  'The drafted recommendation of the set, at most one. It carries a neutral outlined word on the '
  'card and no other privilege: same row height, same single tap. A pre-selected option is a '
  'default ridden rather than a choice made.';

-- ---------------------------------------------------------------- the view, arm 4 narrowed
--
-- Arms 1, 2 and 3 are queue schema 0013's, reproduced byte for byte. The ONLY change in this
-- file is arm 4's WHERE clause. Column names, types and order are unchanged, which is what
-- CREATE OR REPLACE VIEW requires and what keeps every reader working across the swap.

CREATE OR REPLACE VIEW brain.queue_open AS
  -- 1. finished agent work awaiting a human acceptance. `done` means the agent reported it
  --    finished; acceptance is a separate act (D00 contract rule 3).
  SELECT 'work_item'::text AS source_type, w.id AS source_id,
         w.title, w.lane AS source_lane, w.claimed_by AS producer,
         'review'::text AS default_item_class, 'Accept work'::text AS primary_verb,
         w.priority, w.finished_at AS surfaced_at, w.created,
         s.external, s.canon_touching, s.reversibility, s.urgency, s.stakes,
         s.charter_alignment, s.dependency_unblocking, s.confidence,
         w.depends_on, w.id AS work_item_id, w.session_id
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
         w.depends_on, w.id, w.session_id
    FROM brain.work_item w
    JOIN brain.work_item_signals s ON s.id = w.id
   WHERE NOT w.agent_claimable AND w.state IN ('inbox', 'active')
  UNION ALL
  -- 3. open questions. These are where a stated default lives, so this arm is what the
  --    null-branch gate protects.
  SELECT 'question', q.id, q.text, coalesce(w.lane, ''), q.asked_by,
         'approval', 'Accept default',
         coalesce(w.priority, 1), q.asked_at, q.asked_at,
         coalesce(s.external, false), coalesce(s.canon_touching, false),
         s.reversibility, s.urgency, s.stakes, s.charter_alignment,
         s.dependency_unblocking, s.confidence,
         coalesce(w.depends_on, ''), q.work_item_id, ''
    FROM brain.question q
    LEFT JOIN brain.work_item w ON w.id = q.work_item_id
    LEFT JOIN brain.work_item_signals s ON s.id = q.work_item_id
   WHERE q.answer IS NULL
  UNION ALL
  -- 4. open recommendations that require a human. Requiring one is the norm and not the
  --    exception: `recommend accept` refuses an agent decider whatever this column says.
  --
  --    EXCEPT A DRAFTED OPTION, which is a recommendation that already has a card: its item's.
  --    Without this term a four-option item arrives as five queue cards, four of them proposals
  --    the operator has already been shown in one place, and the component built to make a
  --    choice small would be the thing making the queue big. The option is not hidden -- it is
  --    rendered on `brain.queue_item`'s card by `web/model.py` and it is still accepted through
  --    `recommend accept` like any other recommendation.
  SELECT 'recommendation', r.id::text, r.text, '', coalesce(r.produced_by, ''),
         'approval', 'Approve',
         2, r.created_at, r.created_at,
         coalesce(s.external, false), coalesce(s.canon_touching, false),
         s.reversibility, s.urgency, s.stakes, s.charter_alignment,
         s.dependency_unblocking, s.confidence,
         coalesce(w.depends_on, ''),
         CASE WHEN r.subject_type = 'work_item' THEN r.subject_id END,
         coalesce(r.cites_session_id, '')
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
  'and there must not be one -- a second list is the parallel queue the whole design refuses. '
  'Arm 2, the operator''s own work, is NOT w.agent_claimable: the exact complement of the '
  'predicate engine claim applies (queue schema 0013, task 0421). Arm 4 excludes a recommendation '
  'claimed by brain.queue_item_option: a drafted option is rendered on its item''s card, so '
  'surfacing it again as a card of its own would flood the queue with the component built to '
  'keep a choice small (queue schema 0014, task 0165).';

-- ---------------------------------------------------------------- grants
--
-- A new table inherits none. Migration 2 grants by explicit table list and sets ALTER DEFAULT
-- PRIVILEGES for the owner only, so a table created and not granted is a table every verb is
-- refused on -- measured here first, as `permission denied for table queue_item_option` out of
-- `queue draft options`. Same posture as 0007: DELETE to nobody. An option is a decision the
-- operator was offered, and a row nothing may delete is the only kind of record that is one.
GRANT SELECT, INSERT, UPDATE ON brain.queue_item_option TO brain_runtime;
GRANT USAGE, SELECT ON brain.queue_item_option_id_seq TO brain_runtime;
-- The operator's own login acts through `brain_runtime` by membership (migration 20), so it
-- needs no grant of its own here and deliberately gets none.

INSERT INTO brain.schema_migration (version, name)
     VALUES (30, '0014_queue_item_options')
  ON CONFLICT (version) DO NOTHING;

COMMIT;
