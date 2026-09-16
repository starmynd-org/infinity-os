-- 0007_queue.sql -- the human queue: the membrane overlay, typed defers, decaying bumps,
-- producer calibration, fired defaults, and the two gates this lane is judged on.
--
-- Additive. It creates six tables and four views, alters `brain.recommendation` by adding four
-- columns, and installs two triggers. It edits no file another lane owns and drops nothing.
--
-- VERSION 7, read from `brain.schema_migration` and not from `ls migrations/`. D1 logged the
-- trap at 12:20Z and THIS FILE HIT IT: it was written as 0005 against a ledger whose maximum was
-- 4, and by the time it reached the live store two more lanes had landed `0005_lineage_resolution`
-- and `0006_signal_numeric_vocabulary`, neither of them in `migrations/`. The guard below caught
-- it and refused, which is the whole reason it is there -- every migration ends with ON CONFLICT
-- DO NOTHING, so a file claiming a taken version would otherwise apply its DDL and silently skip
-- its ledger row, leaving the store one migration ahead of what the ledger reports. Measured:
--
--   ERROR: schema version 5 is already held by 0005_lineage_resolution, not 0005_queue.
--   -> 0 queue tables created. The refusal happens before BEGIN, so nothing partial lands.
--
-- Re-read the ledger immediately before applying this. A number that was free ten minutes ago
-- is not a number that is free now, on a store four lanes are writing to at once.
--
-- ----------------------------------------------------------------------------------------
-- THE TWO GATES, and why each is a trigger rather than a check in application code
-- ----------------------------------------------------------------------------------------
--
-- 1. A DEFAULT MAY NEVER CARRY A HARD FLAG'S ACTION.  `question_default_null_branch` refuses to
--    store an act-shaped `default_if_unanswered` on a question whose task is `external` or
--    `canon_touching` (flags resolved by OR up the whole parent chain, through
--    `brain.work_item_signals`). The consequence is the property the surface rests on:
--    **silence can only ever ship the reversible branch.**
--
-- 2. NO RECOMMENDATION BECOMES EXECUTED WORK WITHOUT A HUMAN DECIDER.
--    `recommendation_human_decider` refuses `state = 'accepted'` unless a decider is recorded,
--    the decider is not a registered agent, and `actor_type` is `human`; and it refuses to link
--    a spawned work item to anything not accepted.
--
--    *** SUPERSEDED IN PART. READ `queue/schema/0015_recommendation_human_login.sql` (schema
--    version 32, task 0290) BEFORE RELYING ON THIS PARAGRAPH. *** The "not a registered agent"
--    test above is a DENYLIST and it fails open: it refuses the names that ARE in `brain.agent`,
--    so every name that is not one passes, and the agent picks the name. The V9 acceptance run
--    measured real drafted options dispatching from a `brain_runtime` connection under
--    `by='Andrew'` and `by='zzz-not-a-person'`. Migration 32 replaces the gate with
--    `brain.current_human()` -- the login, per migration 20 -- and keeps the `brain.agent` test
--    as defence in depth. The function body below is the version that shipped and is left
--    unedited on purpose: this file is applied on live, and the builder applies 32 after it.
--
-- A gate in application code is bypassable by a bug or a wrong branch. A gate as a trigger is
-- not bypassable by application logic at all -- it refuses the superuser at a psql prompt, which
-- is the same posture as migration 2's grants and migration 4's enums. This is deliberately the
-- second, independent gate: the verbs in `queue/human_queue/transitions.py` refuse first, in
-- words, and the database refuses whatever the verbs do.

\set ON_ERROR_STOP on

DO $$
DECLARE taken text;
BEGIN
  SELECT name INTO taken FROM brain.schema_migration WHERE version = 7;
  IF taken IS NOT NULL AND taken <> '0007_queue' THEN
    RAISE EXCEPTION 'schema version 7 is already held by %, not 0007_queue. Pick the next '
                    'version by reading brain.schema_migration, never by listing a directory.',
                    taken;
  END IF;
END $$;

BEGIN;

-- ------------------------------------------------------------------ recommendation, extended
--
-- D1's `recommendation` already carries `rationale`, `state`, `cites_event_id`,
-- `cites_session_id` and `requires_human NOT NULL`. Four fields the D6 contract names are
-- missing, and each is load-bearing rather than bookkeeping:

ALTER TABLE brain.recommendation
  -- Non-null means procedural: the template is a playbook IN GIT, resolved through D2's adapter.
  -- It is never the text of a playbook stored here. Null means novel, which routes to a
  -- first-principles agent instead. It is also the grouping key of the acted-on rate, which is
  -- this layer's stated falsifier, so pruning can target a dead playbook rather than condemn the
  -- lane.
  ADD COLUMN IF NOT EXISTS template_id text,
  -- What would be done. `text` is what the operator reads; `proposed_action` is what acceptance
  -- would execute, and it becomes the title of the spawned work item.
  ADD COLUMN IF NOT EXISTS proposed_action text NOT NULL DEFAULT '',
  -- U(i) as computed when the recommendation was raised. A SNAPSHOT for audit, never the live
  -- value: the live one is recomputed over the DAG on every read, because a stored score goes
  -- stale the moment a dependency resolves.
  ADD COLUMN IF NOT EXISTS unblock_weight numeric,
  -- The work item acceptance created. Written by exactly one transition, and the trigger below
  -- refuses to let it be written on a row a human has not accepted.
  ADD COLUMN IF NOT EXISTS spawned_work_item text REFERENCES brain.work_item(id);

COMMENT ON COLUMN brain.recommendation.template_id IS
  'Null means novel. Non-null resolves to a playbook in git through the brain adapter; never a '
  'playbook body stored in the database. Grouping key of the acted-on rate.';
COMMENT ON COLUMN brain.recommendation.unblock_weight IS
  'Snapshot of U(i) at creation, for audit. The ranking recomputes U live over the depends_on '
  'DAG; a stored score is stale the moment a dependency resolves.';
COMMENT ON COLUMN brain.recommendation.spawned_work_item IS
  'The executed work. Only `recommend accept` writes it, and trigger recommendation_human_decider '
  'refuses it on any row not accepted by a human.';

CREATE UNIQUE INDEX IF NOT EXISTS recommendation_spawned_once
  ON brain.recommendation (spawned_work_item) WHERE spawned_work_item IS NOT NULL;
CREATE INDEX IF NOT EXISTS recommendation_open_idx ON brain.recommendation (state, created_at);
CREATE INDEX IF NOT EXISTS recommendation_template_idx ON brain.recommendation (template_id);

-- ------------------------------------------------------------------ gate 2: the human decider

CREATE OR REPLACE FUNCTION brain.recommendation_human_decider() RETURNS trigger AS $$
DECLARE is_agent boolean;
BEGIN
  IF TG_OP = 'INSERT' THEN
    IF NEW.state = 'accepted' THEN
      RAISE EXCEPTION 'a recommendation may not be born accepted (id would be %)', NEW.id
        USING HINT = 'INSERT it open, then accept it through `recommend accept`, which is the '
                     'one transition that records a human decider.';
    END IF;
    IF NEW.spawned_work_item IS NOT NULL THEN
      RAISE EXCEPTION 'a recommendation may not be born linked to executed work'
        USING HINT = 'The link is written by `recommend accept` and by nothing else.';
    END IF;
    RETURN NEW;
  END IF;

  IF NEW.state = 'accepted' AND OLD.state IS DISTINCT FROM 'accepted' THEN
    IF NEW.decided_by IS NULL OR btrim(NEW.decided_by) = '' THEN
      RAISE EXCEPTION 'recommendation % cannot be accepted with no decider recorded', NEW.id
        USING HINT = 'Acceptance is a human act. Record who decided.';
    END IF;
    IF NEW.decided_at IS NULL THEN
      RAISE EXCEPTION 'recommendation % cannot be accepted with no decided_at', NEW.id;
    END IF;
    SELECT EXISTS (SELECT 1 FROM brain.agent WHERE name = NEW.decided_by) INTO is_agent;
    IF is_agent THEN
      RAISE EXCEPTION 'recommendation % cannot be accepted by %, which is a registered agent',
                      NEW.id, NEW.decided_by
        USING HINT = 'D00 contract rule 4: the decider on every acceptance is a human. An agent '
                     'that believes this recommendation is right may say so on the thread; it '
                     'may not accept it.';
    END IF;
    IF NEW.actor_type IS DISTINCT FROM 'human'::brain.actor_type THEN
      RAISE EXCEPTION 'recommendation % accepted with actor_type %, which must be human',
                      NEW.id, COALESCE(NEW.actor_type::text, 'null')
        USING HINT = 'effectiveness-points slices on actor_type; an acceptance booked as ai '
                     'would both break the gate and corrupt the leverage measurement.';
    END IF;
  END IF;

  IF NEW.spawned_work_item IS NOT NULL AND OLD.spawned_work_item IS DISTINCT FROM NEW.spawned_work_item THEN
    IF NEW.state <> 'accepted' THEN
      RAISE EXCEPTION 'recommendation % is % and cannot be linked to executed work %',
                      NEW.id, NEW.state, NEW.spawned_work_item
        USING HINT = 'Executed work exists only downstream of a human acceptance.';
    END IF;
  END IF;

  RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS recommendation_human_decider ON brain.recommendation;
CREATE TRIGGER recommendation_human_decider
  BEFORE INSERT OR UPDATE ON brain.recommendation
  FOR EACH ROW EXECUTE FUNCTION brain.recommendation_human_decider();

COMMENT ON FUNCTION brain.recommendation_human_decider() IS
  'Program success criterion: there must be no code path by which a recommendation becomes '
  'executed work without a human decider. Proven by queue/tests/test_no_self_execution.py.';

-- ------------------------------------------------------------------ gate 1: the null branch
--
-- ONE implementation of "is this default act-shaped", and it lives here rather than in Python
-- so that no second copy can drift from it. D4 kept `signal_level` in two places and had to
-- write a parity test to hold them together; this lane does not create that debt. The Python
-- side calls this function.
--
-- The test is an ALLOWLIST, not a denylist, and that direction is the safety property: on a
-- flagged task the default must positively state a null branch. A default nobody can classify
-- is refused, because the conservative default in this system is always to show the operator
-- rather than to let silence act.

CREATE OR REPLACE FUNCTION brain.default_is_null_branch(t text) RETURNS boolean AS $$
DECLARE
  s text := lower(coalesce(t, ''));
  -- The null branch: hold, stage only, prepare but do not send.
  null_re text := '(^|[^a-z])(hold|holds|held|wait|waits|pause|paused|stage only|staged only|'
                  || 'stage it only|stage (?:it |the )?[a-z ]{0,20}(?:but|and) (?:do not|don''t|'
                  || 'never)|draft only|prepare[a-z]* (?:it )?[a-z ]{0,20}(?:but|and) (?:do not|'
                  || 'don''t|never)|no action|take no action|nothing (?:is |gets |will be )?'
                  || '(?:sent|sends|ship|ships|shipped|published|publishes|deployed|deploys|'
                  || 'spent|spends|charged|committed|pushed|merged|deleted|changed|happens|'
                  || 'moves)|do not|does not|don''t|never|leave (?:it )?(?:as is|alone|'
                  || 'unchanged|untouched)|stays? (?:blocked|queued|open|as is|local)|remains? '
                  || '(?:blocked|open|unsent)|skip|defer|ask again|escalate|surface|keep (?:it )?'
                  || 'local|keep (?:it )?in draft|no-op|noop)([^a-z]|$)';
  -- Verbs that reach outside or touch canon. Each must be negated to survive.
  act_re text := '(^|[^a-z])(send|sends|sending|email|emails|reply|replies|publish|publishes|'
                 || 'deploy|deploys|ship|ships|push|pushes|commit|commits|merge|merges|pay|pays|'
                 || 'charge|charges|refund|refunds|delete|deletes|drop|drops|cancel|cancels|'
                 || 'buy|buys|order|orders|sign|signs|submit|submits|upload|uploads|post|posts|'
                 || 'message|messages|dm|call|calls|text|texts|notify|notifies|transfer|'
                 || 'transfers|spend|spends|provision|provisions|launch|launches|release|'
                 || 'releases|apply|applies|overwrite|overwrites|rename|renames|archive|'
                 || 'archives|approve|approves|accept|accepts|execute|executes|run|runs)([^a-z]|$)';
  neg_re  text := '(do not|does not|don''t|never|without|no |not |nothing|none |neither |'
                  || 'refuse|rather than|instead of|before |unless |cannot|can''t|won''t|'
                  || 'will not|but )';
  -- An article immediately before the token means it is a noun, not an imperative: "the email",
  -- "the run", "this post". Without this the lexicon refuses a default for naming the object it
  -- is refusing to act on, which is the phrasing an honest null branch naturally uses.
  noun_re text := '(^|[^a-z])(the|a|an|that|this|these|those|each|every|its|his|her|their|our|'
                  || 'one|no|any|another|same|last|next|first|second)( +)$';
  n int;
  i int;
  pos int;
  win text;
  prev_end int := 1;
BEGIN
  IF btrim(s) = '' THEN
    -- No default at all is not this function's business; `ask` requires one separately.
    RETURN true;
  END IF;
  IF s !~ null_re THEN
    RETURN false;
  END IF;
  n := regexp_count(s, act_re);
  i := 1;
  WHILE i <= n LOOP
    -- subexpr 2 is the verb itself. Without it the position lands on the separator the
    -- pattern captures in group 1, the window loses its trailing space, and the article test
    -- below never fires.
    pos := regexp_instr(s, act_re, 1, i, 0, '', 2);
    -- The window runs from the END OF THE PREVIOUS ACT VERB, never further back. Reaching past
    -- it would let one negator govern every verb after it, so "do not send anything, then send
    -- the summary" would read as a null branch. It is not one, and this is the reason the
    -- window is bounded rather than a fixed lookback: measured, that string passed a plain
    -- 34-character lookback.
    win := substr(s, greatest(prev_end, pos - 34),
                  greatest(0, pos - greatest(prev_end, pos - 34)));
    IF win !~ noun_re AND win !~ neg_re THEN
      RETURN false;
    END IF;
    prev_end := pos + 1;
    i := i + 1;
  END LOOP;
  RETURN true;
END $$ LANGUAGE plpgsql IMMUTABLE;

COMMENT ON FUNCTION brain.default_is_null_branch(text) IS
  'True when a stated default is a null branch (hold, stage only, prepare but do not send). '
  'The single implementation: Python calls this, so the two cannot drift.';

CREATE OR REPLACE FUNCTION brain.question_default_null_branch() RETURNS trigger AS $$
DECLARE
  ext boolean := false;
  canon boolean := false;
BEGIN
  IF NEW.work_item_id IS NULL OR coalesce(btrim(NEW.default_if_unanswered), '') = '' THEN
    RETURN NEW;
  END IF;
  SELECT s.external, s.canon_touching INTO ext, canon
    FROM brain.work_item_signals s WHERE s.id = NEW.work_item_id;
  IF NOT coalesce(ext, false) AND NOT coalesce(canon, false) THEN
    RETURN NEW;
  END IF;
  IF brain.default_is_null_branch(NEW.default_if_unanswered) THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION
    'the default on % is act-shaped and % is %: a default may never carry a hard flag''s action',
    NEW.work_item_id, NEW.work_item_id,
    concat_ws(' + ', CASE WHEN ext THEN 'external' END, CASE WHEN canon THEN 'canon_touching' END)
    USING HINT = 'Silence must only ever ship the reversible branch. State the null branch: '
                 'hold, stage only, prepare but do not send. If the act really is the right '
                 'default, the task is asking the operator to approve it, which is the answer, '
                 'not the default.';
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS question_default_null_branch ON brain.question;
CREATE TRIGGER question_default_null_branch
  BEFORE INSERT OR UPDATE OF default_if_unanswered, work_item_id ON brain.question
  FOR EACH ROW EXECUTE FUNCTION brain.question_default_null_branch();

-- ------------------------------------------------------------------ the membrane overlay
--
-- NOT a second queue. The human queue is a VIEW over three sources that already exist: the
-- operator's own `work_item` rows (`actor_type = 'human'`), finished work awaiting acceptance,
-- open `question` rows, and open `recommendation` rows. This table carries only the membrane
-- fields from `entities/rules/operator-human-queue-contract.md` that have no home on those
-- rows, and every source row appears in the queue whether or not it has one of these.
--
-- `human_minutes_est` IS DELIBERATELY ABSENT. It belongs on the queue-item shape in that
-- contract, which is an operator-gated edit. This lane raised it; it did not make it. Until it
-- exists the tier is computed from item_class, template_id null-or-not, prepared_context_link,
-- recommended_option and the reversibility floor. See queue/RAISED.md.

CREATE TABLE IF NOT EXISTS brain.queue_item (
  id                    bigserial PRIMARY KEY,
  source_type           text NOT NULL CHECK (source_type IN ('work_item','question','recommendation')),
  source_id             text NOT NULL,
  item_class            text CHECK (item_class IS NULL OR item_class IN
                          ('review','approval','blocker','assumption','fyi','scope')),
  template_id           text,
  prepared_context_link text,
  recommended_option    text,
  counterargument       text,
  tier_override         text CHECK (tier_override IS NULL OR tier_override IN ('decide','judge','shape')),
  tier_override_reason  text,
  added                 timestamptz NOT NULL DEFAULT now(),
  opened_at             timestamptz,
  disposed_at           timestamptz,
  disposition           text,
  decision              text,
  promotion_event       text,
  eta                   timestamptz,
  produced_by           text,
  produced_by_ref       text,
  resolution_status     text NOT NULL DEFAULT 'resolved'
                          CHECK (resolution_status IN ('resolved','ambiguous','unresolved')),
  UNIQUE (source_type, source_id)
);

COMMENT ON TABLE brain.queue_item IS
  'The membrane overlay on a source row, per entities/rules/operator-human-queue-contract.md. '
  'Not a queue: brain.queue_open is the queue and it is a view. human_minutes_est is absent on '
  'purpose -- adding it is an operator-gated edit to that contract, raised in queue/RAISED.md.';

-- ------------------------------------------------------------------ typed defers
--
-- THERE IS NO UNTYPED DEFER, and the CHECKs below are what make that a property of the store
-- rather than a discipline in a docstring. Every path out carries a wake condition.

CREATE TABLE IF NOT EXISTS brain.queue_defer (
  id                bigserial PRIMARY KEY,
  source_type       text NOT NULL CHECK (source_type IN ('work_item','question','recommendation')),
  source_id         text NOT NULL,
  n                 int  NOT NULL CHECK (n >= 1),
  kind              text NOT NULL CHECK (kind IN
                      ('until-time','until-event','until-question','accept-default','decline')),
  label             text NOT NULL DEFAULT '',
  wake_at           timestamptz,
  wake_event_type   text,
  wake_subject_type text,
  wake_subject_id   text,
  wake_task         text REFERENCES brain.work_item(id),
  reason            text NOT NULL DEFAULT '',
  created_at        timestamptz NOT NULL DEFAULT now(),
  created_by        text NOT NULL DEFAULT 'operator',
  woke_at           timestamptz,
  woke_by           text,
  produced_by       text,
  CONSTRAINT defer_until_time_has_a_time
    CHECK (kind <> 'until-time'     OR wake_at IS NOT NULL),
  CONSTRAINT defer_until_event_has_an_event
    CHECK (kind <> 'until-event'    OR coalesce(btrim(wake_event_type), '') <> ''),
  CONSTRAINT defer_until_question_has_a_task
    CHECK (kind <> 'until-question' OR wake_task IS NOT NULL),
  CONSTRAINT defer_decline_has_a_reason
    CHECK (kind <> 'decline'        OR btrim(reason) <> '')
);
CREATE INDEX IF NOT EXISTS queue_defer_source_idx ON brain.queue_defer (source_type, source_id);
CREATE INDEX IF NOT EXISTS queue_defer_open_idx ON brain.queue_defer (woke_at) WHERE woke_at IS NULL;

COMMENT ON TABLE brain.queue_defer IS
  'One row per deferral. The CHECK constraints are the anti-graveyard: a defer with no wake '
  'condition cannot be stored, so an item can only ever leave the queue toward something.';

-- ------------------------------------------------------------------ bumps: the disagreement log
--
-- A decaying additive term, never a pin and never an absolute override. A pin accumulates into
-- a second manual queue the score no longer governs; an override fights every recompute. The
-- `reason` is NOT NULL and non-empty because every bump is a labelled disagreement between the
-- operator and the model, and that log is the weight-tuning dataset. An unlabelled bump would
-- be a row in the dataset with no label, which is worse than no row.

CREATE TABLE IF NOT EXISTS brain.queue_bump (
  id               bigserial PRIMARY KEY,
  source_type      text NOT NULL CHECK (source_type IN ('work_item','question','recommendation')),
  source_id        text NOT NULL,
  delta            numeric NOT NULL CHECK (delta <> 0),
  half_life_hours  numeric NOT NULL DEFAULT 24 CHECK (half_life_hours > 0),
  reason           text NOT NULL CHECK (btrim(reason) <> ''),
  model_score      numeric,
  model_rank       int,
  model_tier       text,
  item_class       text,
  created_at       timestamptz NOT NULL DEFAULT now(),
  created_by       text NOT NULL DEFAULT 'operator'
);
CREATE INDEX IF NOT EXISTS queue_bump_source_idx ON brain.queue_bump (source_type, source_id);
CREATE INDEX IF NOT EXISTS queue_bump_recent_idx ON brain.queue_bump (created_at DESC);

COMMENT ON TABLE brain.queue_bump IS
  'Every bump is a labelled disagreement between the operator and the model. The contribution '
  'decays with half_life_hours (default 24) rather than pinning, so it composes with '
  'recomputation instead of fighting it. This table is the weight-tuning dataset.';

-- ------------------------------------------------------------------ producer calibration

CREATE TABLE IF NOT EXISTS brain.queue_calibration (
  id          bigserial PRIMARY KEY,
  source_type text NOT NULL,
  source_id   text NOT NULL,
  producer    text NOT NULL DEFAULT '',
  kind        text NOT NULL CHECK (kind IN
                ('not-fast','unblock-overdeclared','default-overridden','wake-condition-dead')),
  declared    text,
  observed    text,
  note        text NOT NULL DEFAULT '',
  created_at  timestamptz NOT NULL DEFAULT now(),
  created_by  text NOT NULL DEFAULT 'operator'
);
CREATE INDEX IF NOT EXISTS queue_calibration_producer_idx
  ON brain.queue_calibration (producer, kind, created_at DESC);

COMMENT ON TABLE brain.queue_calibration IS
  'Fast-lane trust is the whole asset: one ambush a week and the lane stops being used. A '
  '"not fast" demote corrects the producer''s estimate system side and lands in that producer''s '
  'rollup, rather than being absorbed silently by the operator.';

-- ------------------------------------------------------------------ fired defaults

CREATE TABLE IF NOT EXISTS brain.queue_default_event (
  id             bigserial PRIMARY KEY,
  question_id    text NOT NULL UNIQUE REFERENCES brain.question(id),
  work_item_id   text,
  checkpoint     text NOT NULL CHECK (checkpoint IN ('07:00','19:00','manual')),
  fired_at       timestamptz NOT NULL DEFAULT now(),
  default_text   text NOT NULL,
  producer       text NOT NULL DEFAULT '',
  external       boolean NOT NULL DEFAULT false,
  canon_touching boolean NOT NULL DEFAULT false,
  null_branch    boolean NOT NULL,
  window_extended_to timestamptz
);

COMMENT ON TABLE brain.queue_default_event IS
  'One row per default that silence shipped. `null_branch` is recorded at fire time so the '
  'claim "silence only ever shipped the reversible branch" is auditable after the fact rather '
  'than inferred from the trigger still being installed.';

-- ------------------------------------------------------------------ views
--
-- `queue_open` is the queue. One view over three sources, so there is no second list to drift.
-- The ordering is NOT here: ranking is computed in queue/human_queue/rank.py, because the
-- unblock weight is a memoized walk over a DAG and because the decomposition, not the number,
-- is what the operator reads. The view carries the inputs.

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
  -- 2. the operator's own tasks. Confirmed by the operator 2026-08-16: his tasks are work_item
  --    rows with actor_type = 'human', in the same table, which is what makes the human/agent
  --    partition computable rather than conventional.
  SELECT 'work_item', w.id, w.title, w.lane, w.posted_by,
         'blocker', 'Mark my task done',
         w.priority, w.created, w.created,
         s.external, s.canon_touching, s.reversibility, s.urgency, s.stakes,
         s.charter_alignment, s.dependency_unblocking, s.confidence,
         w.depends_on, w.id, w.session_id
    FROM brain.work_item w
    JOIN brain.work_item_signals s ON s.id = w.id
   WHERE w.actor_type = 'human' AND w.state IN ('inbox', 'active')
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
   WHERE r.state = 'open';

COMMENT ON VIEW brain.queue_open IS
  'The human queue: one view over four arms of three existing tables. There is no queue table '
  'and there must not be one -- a second list is the parallel queue the whole design refuses.';

-- Every pending default with the flags that govern it. The operator reads this to see what
-- silence will ship; the lane reads it to prove that what silence can ship is only ever the
-- reversible branch.
CREATE OR REPLACE VIEW brain.queue_pending_default AS
  SELECT q.id AS question_id, q.work_item_id, q.asked_by AS producer, q.asked_at,
         q.default_if_unanswered AS default_text,
         coalesce(s.external, false) AS external,
         coalesce(s.canon_touching, false) AS canon_touching,
         brain.default_is_null_branch(q.default_if_unanswered) AS null_branch,
         (coalesce(s.external, false) OR coalesce(s.canon_touching, false)) AS gated,
         e.fired_at, e.checkpoint
    FROM brain.question q
    LEFT JOIN brain.work_item_signals s ON s.id = q.work_item_id
    LEFT JOIN brain.queue_default_event e ON e.question_id = q.id
   WHERE q.answer IS NULL AND coalesce(btrim(q.default_if_unanswered), '') <> '';

-- The falsifier. Reported even when it is bad, especially when it is bad: under roughly 30
-- percent acted-on, this layer is cut back to events plus paging (PLAN.md).
CREATE OR REPLACE VIEW brain.queue_acted_on AS
  SELECT coalesce(template_id, '(novel)') AS template_id,
         count(*)                                              AS raised,
         count(*) FILTER (WHERE state = 'accepted')            AS accepted,
         count(*) FILTER (WHERE state = 'rejected')            AS rejected,
         count(*) FILTER (WHERE state = 'expired')             AS expired,
         count(*) FILTER (WHERE state = 'open')                AS still_open,
         round(count(*) FILTER (WHERE state = 'accepted')::numeric
               / nullif(count(*), 0), 4)                       AS acted_on_rate,
         round(count(*) FILTER (WHERE state IN ('accepted','rejected'))::numeric
               / nullif(count(*), 0), 4)                       AS decided_rate
    FROM brain.recommendation
   GROUP BY 1;

-- Override-after-default, per producer. It needs no new mechanism: `question.amended_at` is set
-- by `reanswer`, so an amended answer on a question whose default fired IS the override.
CREATE OR REPLACE VIEW brain.queue_default_override AS
  SELECT e.producer,
         count(*)                                                     AS defaults_fired,
         count(*) FILTER (WHERE q.amended_at IS NOT NULL)             AS overridden,
         round(count(*) FILTER (WHERE q.amended_at IS NOT NULL)::numeric
               / nullif(count(*), 0), 4)                              AS override_rate
    FROM brain.queue_default_event e
    JOIN brain.question q ON q.id = e.question_id
   GROUP BY 1;

COMMENT ON VIEW brain.queue_default_override IS
  'If the operator regularly reverses what silence shipped, that producer''s defaults are bad '
  'and this number says so before trust erodes quietly. The defaults equivalent of the '
  'mis-surface governor in entities/rules/surfacing-policy.md.';

-- ------------------------------------------------------------------ grants
--
-- New tables inherit no grants: migration 2 grants by explicit table list and set ALTER DEFAULT
-- PRIVILEGES for owner only. Same posture as migration 2 and 0003_budget: DELETE to nobody.
-- Rows are removed by the retention sweep, which is owner-only, and by nothing else.

GRANT SELECT, INSERT, UPDATE ON
  brain.queue_item, brain.queue_defer, brain.queue_bump,
  brain.queue_calibration, brain.queue_default_event
  TO brain_runtime;
GRANT USAGE, SELECT ON
  brain.queue_item_id_seq, brain.queue_defer_id_seq, brain.queue_bump_id_seq,
  brain.queue_calibration_id_seq, brain.queue_default_event_id_seq
  TO brain_runtime;
GRANT SELECT ON brain.queue_open, brain.queue_pending_default,
                brain.queue_acted_on, brain.queue_default_override
  TO brain_runtime;
GRANT SELECT ON brain.queue_open, brain.queue_pending_default,
                brain.queue_acted_on, brain.queue_default_override
  TO brain_subscriber;
GRANT EXECUTE ON FUNCTION brain.default_is_null_branch(text) TO brain_runtime, brain_subscriber;

INSERT INTO brain.schema_migration (version, name)
VALUES (7, '0007_queue') ON CONFLICT (version) DO NOTHING;

COMMIT;
