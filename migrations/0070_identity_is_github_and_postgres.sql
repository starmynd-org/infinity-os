-- migration 70: IDENTITY IS GITHUB AND POSTGRES, both required, so a human removed from either is
-- removed.
--
-- LEDGER VERSION 70. max(version) + 1 after migration 69, read from `engine/bin/scratch-db.sh
-- ledger` and from brain.schema_migration on scratch ios_term5_scratch on 2026-09-09. Not a hole.
--
-- Written by 2026-09-09-IOS-term-5. Depends on migration 69 (brain.workspace), which it references.
--
-- ================================================================= the ruling this implements
--
-- Andrew, 2026-09-09, decision B in ARCHITECTURE-2026-09-09-tenancy-and-the-git-boundary.md,
-- closed and not re-openable. His words:
--
--     "they both are required to then get access. You have to add them to GitHub, and you also
--      have to add them to Postgres, and their Postgres has to be signed in to GitHub. I'm kind of
--      fine with that because it makes it even more secure. A little bit of a headache, but here
--      we need to focus on security."
--
-- And the failure mode he named for it, which is the test this file was written to pass:
--
--     "a user removed from GitHub but not from Postgres is the failure mode to guard. That is the
--      test to plant."
--
-- The test is migrations/tests/test_deprovisioning_is_two_sided.py. It was written FIRST and
-- watched failing on ios_term5_scratch at ledger 68: "3 passed, 9 failed (12 scenes)", scene 4
-- reading THE FAILURE MODE IS LIVE, because with brain.human_role as the whole of identity an
-- approval by a human with no GitHub identity at all landed. This file is what makes it pass.
--
-- ================================================================= the shape, and why a receipt
--
-- THE POSTGRES HALF already exists and is untouched: brain.human_role (migration 20) and
-- brain.current_human(), which reads session_user. No function here replaces or wraps it; lane C
-- composes it and asked to be told if it changed (IDENTITY-POLICY part 1). It did not.
--
-- THE GITHUB HALF is two tables:
--
--   brain.human_repo_host_identity   WHICH repo-host account a human IS. A link, written by a
--                                    human login (the human at sign-in, or the operator
--                                    provisioning), checked against the roster, unlinkable and
--                                    never deleted. Identity, not permission.
--
--   brain.repo_access_receipt        WHAT THE REPO HOST SAID when asked whether that account
--                                    reaches every repo in a workspace's declared set, WITH THE
--                                    DATE IT WAS ASKED and the denominator it was asked over.
--
-- THE AND is brain.human_reaches(human, workspace): the Postgres half holds, the workspace is
-- declared, a live link exists for its host, and the NEWEST receipt for that link and that
-- workspace says the account reaches repos_checked of repos_checked, over the set as it is now,
-- and is younger than brain.repo_access_receipt_ttl(). Anything else is false. No receipt is
-- false. A stale receipt is false. A receipt over a smaller set than the one now declared is false.
--
-- WHY THIS IS NOT "MAINTAINING VISIBILITY IN A TABLE", which Andrew's ruling A forbids. A permission
-- matrix is authored by an administrator and is the source of truth. A receipt is written by the
-- machine after asking the repo host, carries the moment it was asked, and DECAYS. The source of
-- truth is GitHub, exactly as ruled; the receipt exists because Postgres cannot call GitHub from
-- inside a trigger, and the alternative, checking in application code on every request, is the
-- layer IDENTITY-POLICY part 3 reason 2 rejects: a guard in application code protects only the
-- callers that go through it. This is the fleet's own instrument rule applied to identity: a
-- repeated claim is a reading with a date on it, and it decays silently while still reading as
-- current, so the date is on the row and the gate reads it.
--
-- DE-PROVISIONING IS TWO-SIDED, as provisioning is. Remove a human from GitHub and the next
-- receipt says so and access ends; if no check ever runs again the ttl ends it. Remove a human
-- from Postgres and brain.human_role no longer maps them, migration 63's own check refuses, and a
-- receipt for a name the roster does not know is refused at insert. Both halves are watched below.
--
-- ================================================================= what this does NOT close
--
-- 1. decided_by IS STILL A NAME THE CALLER PASSES, NOT THE LOGIN. Measured on ios_term5_scratch at
--    ledger 68 on 2026-09-09 by migrations/tests/probe_approval_decider_login.py: from
--    brain_runtime, the login every agent surface holds, `INSERT INTO brain.approval ...
--    decided_by='lane-e-two'` over a real grant LANDED as approval_seq 20. Migrations 61 and 63
--    check that the name is a human and holds the grant; neither compares it to session_user, and
--    `store/authority.py::_decide` is registered with the default runtime role. This gate composes
--    the same name and is EXACTLY AS STRONG AS 61 AND 63, no stronger: an agent that can forge a
--    decider today can forge one who reaches. Closing it is migration 36's move on this column, a
--    second trigger requiring decided_by = brain.current_human(), AND a change to the role
--    `approval decide` runs as and to the console path that calls it. Those files belong to other
--    seats in the 2026-09-09 fleet, so it is filed as a FINDING on the bus rather than done here.
--
-- 2. A RECEIPT IS AS TRUSTWORTHY AS THE LOGIN THAT WROTE IT, and today that is brain_runtime. The
--    adapter that asks GitHub runs in the web process, which holds the runtime credential, which
--    every agent also holds. Andrew's ruling on unattended access is the remedy: "a service
--    credential scoped per workspace." That is a login role per workspace, minted by a provisioner
--    the way migration 20's humans are, and INSERT on this table moved from brain_runtime to it.
--    It is the next migration in this seat's queue, and until it lands this header says so.
--
-- 3. AN UNDECLARED WORKSPACE IS UNGOVERNED BY THIS GATE. The approval trigger returns early where
--    NEW.workspace has no row in brain.workspace, so every row written before migration 69 and
--    every suite that names an arbitrary workspace string behaves exactly as at 68. The gate begins
--    at declaration, which is the provisioning act, the same way migration 63's constraints begin
--    at 63. Watched as check 8 below and as scene 11 of the test, and printed rather than implied.
--
-- 4. THE DATABASE CANNOT ASK GITHUB. Something must: the repo-host adapter, which asks and then
--    inserts a receipt. Until an adapter runs, a store with this migration has NO receipts and
--    therefore NOBODY reaches any declared workspace, which is the fail-closed direction and the
--    one this store always chooses.
--
-- 5. THE TTL IS THIS SEAT'S NUMBER, not a ruling. 24 hours bounds how long a human removed from
--    GitHub keeps access if no check runs; the adapter's routine is what makes it shorter. It is a
--    function, brain.repo_access_receipt_ttl(), so replacing it is one migration a reviewer can
--    see, the pattern brain.human_login_ceiling() set. Flagged to the operator in the handoff.
--
-- ================================================================= rollback, executed
--
-- Roll THIS back before migration 69: brain.repo_access_receipt references brain.workspace.
--
--     BEGIN;
--       DROP TRIGGER IF EXISTS approval_decider_reaches_the_workspace ON brain.approval;
--       DROP FUNCTION IF EXISTS brain.approval_decider_reaches_the_workspace();
--       DROP FUNCTION IF EXISTS brain.current_workspace_access(text);
--       DROP FUNCTION IF EXISTS brain.workspaces_visible_to(text);
--       DROP FUNCTION IF EXISTS brain.human_reaches(text, text);
--       DROP FUNCTION IF EXISTS brain.human_reach_report(text, text);
--       DROP TRIGGER IF EXISTS repo_access_receipt_append_only ON brain.repo_access_receipt;
--       DROP TRIGGER IF EXISTS repo_access_receipt_is_a_reading ON brain.repo_access_receipt;
--       DROP FUNCTION IF EXISTS brain.repo_access_receipt_append_only();
--       DROP FUNCTION IF EXISTS brain.repo_access_receipt_is_a_reading();
--       DROP TABLE IF EXISTS brain.repo_access_receipt;
--       DROP TRIGGER IF EXISTS human_repo_host_identity_is_a_human_act ON brain.human_repo_host_identity;
--       DROP FUNCTION IF EXISTS brain.human_repo_host_identity_is_a_human_act();
--       DROP TABLE IF EXISTS brain.human_repo_host_identity;
--       DROP FUNCTION IF EXISTS brain.repo_access_receipt_ttl();
--       DELETE FROM brain.schema_migration WHERE version = 70;
--     COMMIT;
--
-- TWO-WAY, WITH A STATED LOSS: every link and every receipt. Receipts are re-derivable by asking
-- the repo host again; links are re-derivable by linking again, which is a human act. Neither is a
-- store restore. After the rollback the store is at migration 69's behaviour exactly, which is
-- measurable rather than asserted: the planted test reads red again at scene 4. Executed and
-- re-applied on ios_term5_scratch on 2026-09-09; the run is quoted in the seat's handoff.

\set ON_ERROR_STOP on

BEGIN;

-- ---------------------------------------------------------------- the ttl, as a function

CREATE OR REPLACE FUNCTION brain.repo_access_receipt_ttl() RETURNS interval
  LANGUAGE sql IMMUTABLE AS $$ SELECT interval '24 hours' $$;

GRANT EXECUTE ON FUNCTION brain.repo_access_receipt_ttl() TO PUBLIC;

COMMENT ON FUNCTION brain.repo_access_receipt_ttl() IS
  'How long a repo-host receipt counts as current. A receipt older than this grants nothing, '
  'whatever it says. 24 hours is this seat''s number and not a ruling; it is the upper bound on '
  'how long a human removed at the host keeps access when no check runs, and the adapter''s '
  'routine is what makes the real figure smaller. Replace it in a migration, never in place.';

-- ---------------------------------------------------------------- the link: which account a human is

CREATE TABLE IF NOT EXISTS brain.human_repo_host_identity (
  identity_seq bigserial    PRIMARY KEY,
  human        text         NOT NULL,
  repo_host    text         NOT NULL DEFAULT 'github',
  host_login   text         NOT NULL,
  host_user_id text,
  linked_at    timestamptz  NOT NULL DEFAULT now(),
  linked_by    text         NOT NULL,
  unlinked_at  timestamptz,
  unlinked_by  text,
  -- NOT GitHub's login shape. The first draft of this line was GitHub's 39-character rule, which a
  -- customer's in-house host with email-shaped logins could never satisfy; the Admiral's relay of
  -- 20260909T124100Z asked for exactly this to be found. A login here is any non-empty string
  -- without whitespace, and the host decides what it means.
  CONSTRAINT human_repo_host_identity_login_ck
    CHECK (btrim(host_login) <> '' AND host_login !~ '\s' AND length(host_login) <= 254),
  CONSTRAINT human_repo_host_identity_unlink_is_whole_ck
    CHECK ((unlinked_at IS NULL AND unlinked_by IS NULL)
        OR (unlinked_at IS NOT NULL AND btrim(COALESCE(unlinked_by, '')) <> ''))
);

-- One LIVE link per human per host, and one live human per host account. History rows (unlinked)
-- stay, so a re-link is a new row and the old one still says who it was and when it ended.
CREATE UNIQUE INDEX IF NOT EXISTS human_repo_host_identity_one_live_link_per_human
  ON brain.human_repo_host_identity (human, repo_host) WHERE unlinked_at IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS human_repo_host_identity_one_live_human_per_login
  ON brain.human_repo_host_identity (repo_host, host_login) WHERE unlinked_at IS NULL;

COMMENT ON TABLE brain.human_repo_host_identity IS
  'The GitHub half of identity: which repo-host account a human in brain.human_role IS. A link '
  'and not a permission: it says nothing about what the account reaches, which is a question '
  'for the host and is recorded, dated, in brain.repo_access_receipt. Written only by a human '
  'login, as that human, for a name the roster knows. Unlinked, never deleted.';

COMMENT ON COLUMN brain.human_repo_host_identity.host_user_id IS
  'The host''s immutable id for the account, when the adapter learned it. Logins can be renamed '
  'at the host; the id cannot. Nullable because a link made by hand at provisioning may not know '
  'it yet, and the first receipt is where the adapter fills it in for the next link.';

CREATE OR REPLACE FUNCTION brain.human_repo_host_identity_is_a_human_act() RETURNS trigger
  LANGUAGE plpgsql AS $$
DECLARE who text;
BEGIN
  who := brain.current_human();
  IF who IS NULL THEN
    RAISE EXCEPTION 'refusing to % the repo-host identity of %: this connection is %, which is '
                    'not a human login', lower(TG_OP), NEW.human, session_user
      USING ERRCODE = 'insufficient_privilege',
            HINT = 'Linking a human to a host account is the act that makes the GitHub half of '
                   'identity true, so it is a human''s act: the human at sign-in or the operator '
                   'provisioning. An agent holding brain_runtime has no route here.';
  END IF;
  IF TG_OP = 'INSERT' THEN
    IF NEW.linked_by IS DISTINCT FROM who THEN
      RAISE EXCEPTION 'refusing to record % as the linker of %: this connection is %',
                      NEW.linked_by, NEW.human, who USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.unlinked_at IS NOT NULL THEN
      RAISE EXCEPTION 'refusing a link that is born unlinked' USING ERRCODE = 'check_violation';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM brain.human_roster() r WHERE r.human = NEW.human) THEN
      RAISE EXCEPTION 'refusing to link %: this database does not know that human', NEW.human
        USING ERRCODE = 'foreign_key_violation',
              HINT = 'Provisioning is two-sided and Postgres comes first here: `swarm admin human '
                     'provision <slug>`, then link the account.';
    END IF;
    RETURN NEW;
  END IF;
  -- UPDATE: the ONLY change allowed is the unlink, once, by a human, as that human.
  IF OLD.unlinked_at IS NOT NULL THEN
    RAISE EXCEPTION 'refusing to edit an unlinked identity row: it is history now'
      USING ERRCODE = 'restrict_violation';
  END IF;
  IF NEW.human IS DISTINCT FROM OLD.human OR NEW.repo_host IS DISTINCT FROM OLD.repo_host
     OR NEW.host_login IS DISTINCT FROM OLD.host_login OR NEW.linked_at IS DISTINCT FROM OLD.linked_at
     OR NEW.linked_by IS DISTINCT FROM OLD.linked_by
     OR (NEW.host_user_id IS DISTINCT FROM OLD.host_user_id AND OLD.host_user_id IS NOT NULL) THEN
    RAISE EXCEPTION 'refusing to rewrite the identity of %: a link is unlinked and re-made, never '
                    'edited', OLD.human USING ERRCODE = 'restrict_violation';
  END IF;
  IF NEW.unlinked_at IS NOT NULL AND NEW.unlinked_by IS DISTINCT FROM who THEN
    RAISE EXCEPTION 'refusing to record % as the unlinker of %: this connection is %',
                    NEW.unlinked_by, OLD.human, who USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS human_repo_host_identity_is_a_human_act ON brain.human_repo_host_identity;
CREATE TRIGGER human_repo_host_identity_is_a_human_act
  BEFORE INSERT OR UPDATE ON brain.human_repo_host_identity
  FOR EACH ROW EXECUTE FUNCTION brain.human_repo_host_identity_is_a_human_act();

-- ---------------------------------------------------------------- the receipt: what the host said, and when

CREATE TABLE IF NOT EXISTS brain.repo_access_receipt (
  receipt_seq   bigserial   PRIMARY KEY,
  human         text        NOT NULL,
  repo_host     text        NOT NULL DEFAULT 'github',
  host_login    text        NOT NULL,
  workspace     text        NOT NULL REFERENCES brain.workspace (workspace),
  checked_at    timestamptz NOT NULL DEFAULT now(),
  reaches       boolean     NOT NULL,
  repos_checked integer     NOT NULL,
  repos_reached integer     NOT NULL,
  evidence      jsonb       NOT NULL DEFAULT '{}'::jsonb,
  checked_by    text        NOT NULL DEFAULT session_user::text,
  CONSTRAINT repo_access_receipt_counts_ck
    CHECK (repos_checked >= 0 AND repos_reached >= 0 AND repos_reached <= repos_checked),
  -- The verdict IS the count. A receipt cannot say "reaches" over zero repos, or over a set it
  -- only partly reached: nothing to reach is not everything, and most is not all.
  CONSTRAINT repo_access_receipt_reaches_is_the_count_ck
    CHECK (reaches = (repos_checked > 0 AND repos_reached = repos_checked))
);

CREATE INDEX IF NOT EXISTS repo_access_receipt_newest_first
  ON brain.repo_access_receipt (human, workspace, receipt_seq DESC);

COMMENT ON TABLE brain.repo_access_receipt IS
  'What the repo host said when asked whether a human''s linked account reaches every repo in a '
  'workspace''s declared set, with the moment it was asked and the denominator it was asked '
  'over. A reading with a date on it, not a permission: the newest one decides, only while it '
  'is younger than brain.repo_access_receipt_ttl(), and only if it was asked over the set as it '
  'is now. Append-only. Written by the repo-host adapter after it asks; never by hand.';

COMMENT ON COLUMN brain.repo_access_receipt.checked_by IS
  'Which login recorded the reading. brain_runtime today, which is as trustworthy as that login '
  'is; the ruled remedy is a service credential scoped per workspace (this file''s header, '
  'residual 2).';

CREATE OR REPLACE FUNCTION brain.repo_access_receipt_is_a_reading() RETURNS trigger
  LANGUAGE plpgsql AS $$
DECLARE set_size bigint;
BEGIN
  IF NEW.checked_at > now() THEN
    RAISE EXCEPTION 'refusing a receipt dated in the future (%): a reading cannot be taken later '
                    'than now, and a future date is the one way to stretch one', NEW.checked_at
      USING ERRCODE = 'check_violation';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM brain.human_roster() r WHERE r.human = NEW.human) THEN
    RAISE EXCEPTION 'refusing a receipt for %: this database does not know that human', NEW.human
      USING ERRCODE = 'foreign_key_violation',
            HINT = 'Identity is both halves. A host account that reaches every repo still reaches '
                   'nothing here until Postgres maps a login to that human.';
  END IF;
  SELECT count(*) INTO set_size FROM brain.workspace_repo r WHERE r.workspace = NEW.workspace;
  IF NEW.repos_checked <> set_size THEN
    RAISE EXCEPTION 'refusing a receipt over % repos for workspace %, whose declared set holds %: '
                    'the reading was taken over a different set', NEW.repos_checked, NEW.workspace,
                    set_size
      USING ERRCODE = 'check_violation',
            HINT = 'The adapter reads brain.workspace_repo and asks about every row. A receipt '
                   'over fewer or more repos than that is about some other workspace.';
  END IF;
  RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS repo_access_receipt_is_a_reading ON brain.repo_access_receipt;
CREATE TRIGGER repo_access_receipt_is_a_reading
  BEFORE INSERT ON brain.repo_access_receipt
  FOR EACH ROW EXECUTE FUNCTION brain.repo_access_receipt_is_a_reading();

CREATE OR REPLACE FUNCTION brain.repo_access_receipt_append_only() RETURNS trigger
  LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'refusing to % receipt %: a reading is appended, never edited. Ask the host '
                  'again and append what it says now.', lower(TG_OP), OLD.receipt_seq
    USING ERRCODE = 'restrict_violation';
END $$;

DROP TRIGGER IF EXISTS repo_access_receipt_append_only ON brain.repo_access_receipt;
CREATE TRIGGER repo_access_receipt_append_only
  BEFORE UPDATE OR DELETE ON brain.repo_access_receipt
  FOR EACH ROW EXECUTE FUNCTION brain.repo_access_receipt_append_only();

-- ---------------------------------------------------------------- the AND

CREATE OR REPLACE FUNCTION brain.human_reach_report(p_human text, p_workspace text)
  RETURNS TABLE (postgres_half boolean, declared boolean, linked boolean, host_login text,
                 receipt_seq bigint, checked_at timestamptz, reaches boolean,
                 repos_reached integer, repos_checked integer, set_size bigint,
                 fresh boolean, reach boolean)
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = brain, pg_temp AS $$
  WITH pg AS (
    SELECT EXISTS (SELECT 1 FROM brain.human_role hr WHERE hr.human = p_human) AS postgres_half
  ), ws AS (
    SELECT w.repo_host,
           (SELECT count(*) FROM brain.workspace_repo r WHERE r.workspace = p_workspace) AS set_size
      FROM brain.workspace w WHERE w.workspace = p_workspace
  ), link AS (
    SELECT i.host_login
      FROM brain.human_repo_host_identity i JOIN ws ON ws.repo_host = i.repo_host
     WHERE i.human = p_human AND i.unlinked_at IS NULL
  ), rc AS (
    SELECT r.receipt_seq, r.checked_at, r.reaches, r.repos_reached, r.repos_checked
      FROM brain.repo_access_receipt r
      JOIN link ON link.host_login = r.host_login
      JOIN ws ON ws.repo_host = r.repo_host
     WHERE r.human = p_human AND r.workspace = p_workspace
     ORDER BY r.receipt_seq DESC LIMIT 1
  )
  SELECT pg.postgres_half,
         EXISTS (SELECT 1 FROM ws)                        AS declared,
         EXISTS (SELECT 1 FROM link)                      AS linked,
         (SELECT l.host_login FROM link l)                AS host_login,
         rc.receipt_seq, rc.checked_at, rc.reaches, rc.repos_reached, rc.repos_checked,
         (SELECT w.set_size FROM ws w)                    AS set_size,
         rc.checked_at > now() - brain.repo_access_receipt_ttl() AS fresh,
         (pg.postgres_half
          AND EXISTS (SELECT 1 FROM ws)
          AND EXISTS (SELECT 1 FROM link)
          AND COALESCE(rc.reaches
                       AND rc.repos_checked = (SELECT w.set_size FROM ws w)
                       AND rc.checked_at > now() - brain.repo_access_receipt_ttl(), false)) AS reach
    FROM pg LEFT JOIN rc ON true
$$;

ALTER FUNCTION brain.human_reach_report(text, text) OWNER TO brain_owner;
REVOKE EXECUTE ON FUNCTION brain.human_reach_report(text, text) FROM PUBLIC;
GRANT  EXECUTE ON FUNCTION brain.human_reach_report(text, text) TO brain_runtime, brain_subscriber, brain_owner;

COMMENT ON FUNCTION brain.human_reach_report(text, text) IS
  'Every term of the AND, separately, so a refusal can say WHICH half failed. SECURITY DEFINER '
  'because the Postgres half is brain.human_role, which the runtime cannot read; it answers a '
  'yes/no per term and returns nothing about anybody else. `reach` is the conjunction and is the '
  'only column a gate should decide on.';

CREATE OR REPLACE FUNCTION brain.human_reaches(p_human text, p_workspace text) RETURNS boolean
  LANGUAGE sql STABLE AS $$
  SELECT COALESCE((SELECT r.reach FROM brain.human_reach_report(p_human, p_workspace) r), false)
$$;

GRANT EXECUTE ON FUNCTION brain.human_reaches(text, text) TO brain_runtime, brain_subscriber, brain_owner;

COMMENT ON FUNCTION brain.human_reaches(text, text) IS
  'Identity is GitHub AND Postgres: true only when brain.human_role maps the human, the workspace '
  'is declared, a live host link exists, and the newest receipt for that link says the account '
  'reaches all of the set as it is now, younger than brain.repo_access_receipt_ttl(). Derived '
  'from what the host said; nothing here is maintained by hand.';

CREATE OR REPLACE FUNCTION brain.workspaces_visible_to(p_human text) RETURNS SETOF text
  LANGUAGE sql STABLE AS $$
  SELECT w.workspace FROM brain.workspace w
   WHERE brain.human_reaches(p_human, w.workspace)
   ORDER BY w.workspace
$$;

GRANT EXECUTE ON FUNCTION brain.workspaces_visible_to(text) TO brain_runtime, brain_subscriber, brain_owner;

COMMENT ON FUNCTION brain.workspaces_visible_to(text) IS
  'Per-user visibility, DERIVED from repo access and never maintained: the declared workspaces '
  'whose repo set the human''s linked account reaches, per the newest fresh receipt. Andrew''s '
  'ruling A. With one workspace and one human it returns one row or none, and the schema does '
  'not know which case it is in.';

CREATE OR REPLACE FUNCTION brain.current_workspace_access(p_workspace text) RETURNS boolean
  LANGUAGE sql STABLE AS $$
  SELECT brain.human_reaches(brain.current_human(), p_workspace)
$$;

GRANT EXECUTE ON FUNCTION brain.current_workspace_access(text) TO PUBLIC;

COMMENT ON FUNCTION brain.current_workspace_access(text) IS
  'The AND, for THIS connection: brain.human_reaches(brain.current_human(), workspace). Public '
  'because the answer is about the caller, as brain.current_human() is.';

-- ---------------------------------------------------------------- the gate on approvals

CREATE OR REPLACE FUNCTION brain.approval_decider_reaches_the_workspace() RETURNS trigger
  LANGUAGE plpgsql AS $$
DECLARE
  rep  record;
  half text;
BEGIN
  -- Residual 3, stated in the header: an undeclared workspace is ungoverned by this gate.
  IF NOT EXISTS (SELECT 1 FROM brain.workspace w WHERE w.workspace = NEW.workspace) THEN
    RETURN NEW;
  END IF;
  SELECT * INTO rep FROM brain.human_reach_report(NEW.decided_by, NEW.workspace);
  IF rep.reach THEN
    RETURN NEW;
  END IF;
  IF NOT rep.postgres_half THEN
    half := format('the Postgres half does not hold: brain.human_role maps no login to %s',
                   NEW.decided_by);
  ELSIF NOT rep.linked THEN
    half := format('the Postgres half holds (brain.human_role maps %s) and the GitHub half does '
                   'not: %s has no live repo-host identity linked', NEW.decided_by, NEW.decided_by);
  ELSIF rep.receipt_seq IS NULL THEN
    half := format('the Postgres half holds (brain.human_role maps %s) and the GitHub half does '
                   'not: no receipt of a repo-host check exists for %s (%s) in %s',
                   NEW.decided_by, NEW.decided_by, rep.host_login, NEW.workspace);
  ELSIF NOT rep.reaches THEN
    half := format('the Postgres half holds (brain.human_role maps %s) and the GitHub half does '
                   'not: the newest receipt (seq %s, checked %s) says %s reaches %s of %s repos '
                   'in %s', NEW.decided_by, rep.receipt_seq, rep.checked_at, rep.host_login,
                   rep.repos_reached, rep.repos_checked, NEW.workspace);
  ELSIF rep.repos_checked <> rep.set_size THEN
    half := format('the Postgres half holds (brain.human_role maps %s) and the GitHub half is '
                   'unmeasured: the newest receipt (seq %s) was over %s repos and %s now holds %s',
                   NEW.decided_by, rep.receipt_seq, rep.repos_checked, NEW.workspace, rep.set_size);
  ELSE
    half := format('the Postgres half holds (brain.human_role maps %s) and the GitHub half has '
                   'decayed: the newest receipt (seq %s) is from %s, older than the ttl of %s',
                   NEW.decided_by, rep.receipt_seq, rep.checked_at, brain.repo_access_receipt_ttl());
  END IF;
  RAISE EXCEPTION 'refusing the approval of % by % in workspace %: identity is both, and %',
                  NEW.proposal_id, NEW.decided_by, NEW.workspace, half
    USING ERRCODE = 'insufficient_privilege',
          HINT = 'Access is GitHub AND Postgres (Andrew, 2026-09-09). Have the repo host asked '
                 'again; the adapter appends what it says. If the host says this account no '
                 'longer reaches the set, the human has been removed there and Postgres must '
                 'follow: `swarm admin human revoke <slug>`. A human removed from GitHub but not '
                 'from Postgres is the failure mode this gate exists to refuse.';
END $$;

-- Sorts after approval_decider_is_entitled (i before r), so migration 61 and 63's roster, grant
-- and workspace checks refuse a malformed approval before this one is asked. Migration 36's
-- reason for a SECOND trigger beside an applied one applies here word for word: 63's function is
-- applied and its file describes live; a CREATE OR REPLACE on it would silently make that false.
DROP TRIGGER IF EXISTS approval_decider_reaches_the_workspace ON brain.approval;
CREATE TRIGGER approval_decider_reaches_the_workspace
  BEFORE INSERT ON brain.approval
  FOR EACH ROW EXECUTE FUNCTION brain.approval_decider_reaches_the_workspace();

-- ---------------------------------------------------------------- grants
--
-- READ broad. The link is written by human logins through their brain_runtime membership, gated
-- by trigger (migration 37's shape). The receipt is INSERT-only, to brain_runtime, which is the
-- residual 2 the header states; UPDATE and DELETE go to nobody below the owner, so for the
-- runtime the grant refuses before the append-only trigger is reached, the two layers migration
-- 68 also keeps.
GRANT SELECT ON brain.human_repo_host_identity, brain.repo_access_receipt
  TO brain_runtime, brain_subscriber;
GRANT INSERT, UPDATE ON brain.human_repo_host_identity TO brain_runtime;
GRANT INSERT ON brain.repo_access_receipt TO brain_runtime;
GRANT USAGE ON SEQUENCE brain.human_repo_host_identity_identity_seq_seq,
                        brain.repo_access_receipt_receipt_seq_seq TO brain_runtime;

-- ---------------------------------------------------------------- every refusal watched happening
--
-- As the applying superuser, mapped to a fixture human for this transaction the way migration 63
-- did, and unmapped at the end. Grants are not exercised here (a superuser bypasses them); the
-- planted test exercises them from the real logins.
DO $$
DECLARE
  n int := 0; refusals int := 0; controls int := 0;
  fx text := '_mv70_fixture_human'; ag text := '_mv70_agent';
  ws text := 'ws-mv70'; ws_un text := 'ws-mv70-undeclared';
  g bigint; g_un bigint; visible int;
BEGIN
  INSERT INTO brain.human_role (role_name, human, granted_by)
    VALUES (session_user, fx, 'migration 70 fixture');
  INSERT INTO brain.agent (name, role, status, host, updated)
    VALUES (ag, 'worker', 'idle', 'migration-70', now())
    ON CONFLICT (name) DO UPDATE SET updated = now();
  INSERT INTO brain.workspace (workspace, declared_by) VALUES (ws, fx);
  INSERT INTO brain.workspace_repo (workspace, repo_full_name, added_by)
    VALUES (ws, 'starmynd-org/example', fx);
  INSERT INTO brain.human_repo_host_identity (human, host_login, linked_by)
    VALUES (fx, 'mv70-login', fx);
  INSERT INTO brain.authority_grant (granted_by, workspace, subject_kind, subject, capability,
                                     scope, expires_at, evidence)
    VALUES (fx, ws, 'human', fx, 'approval.decide', 'lane/mv70', now() + interval '1 hour',
            'migration 70') RETURNING grant_seq INTO g;

  -- 1 refusal: no receipt at all. THE PLANT.
  BEGIN
    INSERT INTO brain.approval (proposal_id, proposal_version, workspace, decided_by, subject,
                                grant_seq, decision)
      VALUES ('mv70-no-receipt', 'v1', ws, fx, ag, g, 'approve');
    RAISE EXCEPTION 'migration 70: an approval with no repo-host receipt was NOT refused';
  EXCEPTION WHEN insufficient_privilege THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 2 control: a fresh receipt that reaches, and the same approval lands.
  INSERT INTO brain.repo_access_receipt (human, host_login, workspace, reaches, repos_checked,
                                         repos_reached)
    VALUES (fx, 'mv70-login', ws, true, 1, 1);
  INSERT INTO brain.approval (proposal_id, proposal_version, workspace, decided_by, subject,
                              grant_seq, decision)
    VALUES ('mv70-ok', 'v1', ws, fx, ag, g, 'approve');
  n := n + 1; controls := controls + 1;

  -- 3 refusal: REMOVED FROM GITHUB, STILL IN POSTGRES. The test Andrew named.
  INSERT INTO brain.repo_access_receipt (human, host_login, workspace, reaches, repos_checked,
                                         repos_reached)
    VALUES (fx, 'mv70-login', ws, false, 1, 0);
  BEGIN
    INSERT INTO brain.approval (proposal_id, proposal_version, workspace, decided_by, subject,
                                grant_seq, decision)
      VALUES ('mv70-removed', 'v1', ws, fx, ag, g, 'approve');
    RAISE EXCEPTION 'migration 70: a human removed at the host but mapped in Postgres was NOT refused';
  EXCEPTION WHEN insufficient_privilege THEN n := n + 1; refusals := refusals + 1;
  END;
  IF NOT EXISTS (SELECT 1 FROM brain.human_role WHERE human = fx) THEN
    RAISE EXCEPTION 'migration 70: the refusal above touched brain.human_role, so it was not the GitHub half';
  END IF;

  -- 4 refusal: a receipt that reaches but has decayed past the ttl.
  INSERT INTO brain.repo_access_receipt (human, host_login, workspace, checked_at, reaches,
                                         repos_checked, repos_reached)
    VALUES (fx, 'mv70-login', ws, now() - brain.repo_access_receipt_ttl() - interval '1 minute',
            true, 1, 1);
  BEGIN
    INSERT INTO brain.approval (proposal_id, proposal_version, workspace, decided_by, subject,
                                grant_seq, decision)
      VALUES ('mv70-stale', 'v1', ws, fx, ag, g, 'approve');
    RAISE EXCEPTION 'migration 70: a receipt older than the ttl still granted access';
  EXCEPTION WHEN insufficient_privilege THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 5 refusal: a receipt dated in the future.
  BEGIN
    INSERT INTO brain.repo_access_receipt (human, host_login, workspace, checked_at, reaches,
                                           repos_checked, repos_reached)
      VALUES (fx, 'mv70-login', ws, now() + interval '1 day', true, 1, 1);
    RAISE EXCEPTION 'migration 70: a future-dated receipt was accepted';
  EXCEPTION WHEN check_violation THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 6 refusal: a receipt over a different denominator than the declared set.
  BEGIN
    INSERT INTO brain.repo_access_receipt (human, host_login, workspace, reaches, repos_checked,
                                           repos_reached)
      VALUES (fx, 'mv70-login', ws, true, 2, 2);
    RAISE EXCEPTION 'migration 70: a receipt over 2 repos was accepted for a set of 1';
  EXCEPTION WHEN check_violation THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 7 refusal: THE MIRROR. A receipt for a name the roster does not know.
  BEGIN
    INSERT INTO brain.repo_access_receipt (human, host_login, workspace, reaches, repos_checked,
                                           repos_reached)
      VALUES ('_mv70_ghost', 'ghost-login', ws, true, 1, 1);
    RAISE EXCEPTION 'migration 70: a receipt for a human Postgres does not know was accepted';
  EXCEPTION WHEN foreign_key_violation THEN n := n + 1; refusals := refusals + 1;
  END;
  IF brain.human_reaches('_mv70_ghost', ws) THEN
    RAISE EXCEPTION 'migration 70: brain.human_reaches is true for a human Postgres does not know';
  END IF;

  -- 8 control: RESIDUAL 3. An undeclared workspace is not gated; the approval lands.
  INSERT INTO brain.authority_grant (granted_by, workspace, subject_kind, subject, capability,
                                     scope, expires_at, evidence)
    VALUES (fx, ws_un, 'human', fx, 'approval.decide', 'lane/mv70', now() + interval '1 hour',
            'migration 70') RETURNING grant_seq INTO g_un;
  INSERT INTO brain.approval (proposal_id, proposal_version, workspace, decided_by, subject,
                              grant_seq, decision)
    VALUES ('mv70-undeclared', 'v1', ws_un, fx, ag, g_un, 'approve');
  n := n + 1; controls := controls + 1;

  -- 9 refusal: a receipt cannot be edited, and 10 refusal: cannot be deleted.
  BEGIN
    UPDATE brain.repo_access_receipt SET reaches = true, repos_reached = 1 WHERE human = fx;
    RAISE EXCEPTION 'migration 70: a receipt was edited after the fact';
  EXCEPTION WHEN restrict_violation THEN n := n + 1; refusals := refusals + 1;
  END;
  BEGIN
    DELETE FROM brain.repo_access_receipt WHERE human = fx;
    RAISE EXCEPTION 'migration 70: a receipt was deleted';
  EXCEPTION WHEN restrict_violation THEN n := n + 1; refusals := refusals + 1;
  END;

  -- 11 control: visibility follows the newest receipt and nothing else.
  SELECT count(*) INTO visible FROM brain.workspaces_visible_to(fx);
  IF visible <> 0 THEN
    RAISE EXCEPTION 'migration 70: % workspaces visible while the newest fresh receipt is stale', visible;
  END IF;
  INSERT INTO brain.repo_access_receipt (human, host_login, workspace, reaches, repos_checked,
                                         repos_reached)
    VALUES (fx, 'mv70-login', ws, true, 1, 1);
  SELECT count(*) INTO visible FROM brain.workspaces_visible_to(fx);
  IF visible <> 1 THEN
    RAISE EXCEPTION 'migration 70: % of 1 workspaces visible after a fresh reaches=true receipt', visible;
  END IF;
  n := n + 1; controls := controls + 1;

  -- 12 refusal: adding a repo to the set invalidates every receipt until the host is asked again.
  INSERT INTO brain.workspace_repo (workspace, repo_full_name, added_by)
    VALUES (ws, 'starmynd-org/second', fx);
  BEGIN
    INSERT INTO brain.approval (proposal_id, proposal_version, workspace, decided_by, subject,
                                grant_seq, decision)
      VALUES ('mv70-grown-set', 'v1', ws, fx, ag, g, 'approve');
    RAISE EXCEPTION 'migration 70: a receipt over the old set still granted access to the grown set';
  EXCEPTION WHEN insufficient_privilege THEN n := n + 1; refusals := refusals + 1;
  END;

  -- cleanup: probe rows out, append-only triggers off for the deletes and on again after.
  ALTER TABLE brain.approval DISABLE TRIGGER approval_append_only;
  ALTER TABLE brain.authority_grant DISABLE TRIGGER authority_grant_append_only;
  ALTER TABLE brain.repo_access_receipt DISABLE TRIGGER repo_access_receipt_append_only;
  DELETE FROM brain.approval WHERE proposal_id LIKE 'mv70-%';
  DELETE FROM brain.authority_grant WHERE grant_seq IN (g, g_un);
  DELETE FROM brain.repo_access_receipt WHERE human = fx;
  ALTER TABLE brain.repo_access_receipt ENABLE TRIGGER repo_access_receipt_append_only;
  ALTER TABLE brain.authority_grant ENABLE TRIGGER authority_grant_append_only;
  ALTER TABLE brain.approval ENABLE TRIGGER approval_append_only;
  DELETE FROM brain.human_repo_host_identity WHERE human = fx;
  DELETE FROM brain.workspace_repo WHERE workspace = ws;
  DELETE FROM brain.workspace WHERE workspace = ws;
  DELETE FROM brain.agent WHERE name = ag;
  DELETE FROM brain.human_role WHERE human = fx AND granted_by = 'migration 70 fixture';

  IF n <> 12 OR refusals <> 9 OR controls <> 3 THEN
    RAISE EXCEPTION 'migration 70: expected 12 checks as 9 refusals and 3 controls, ran % as % and %',
      n, refusals, controls;
  END IF;
  RAISE NOTICE 'migration 70: % of 12 checks passed, % refusals watched refusing and % positive '
               'controls. A human removed at the repo host but still mapped in Postgres was '
               'refused with brain.human_role untouched; a stale, a future-dated, a wrong-set and '
               'a ghost receipt were each refused; the undeclared-workspace residual was watched '
               'landing. Fixture rows removed; tables ship EMPTY.', n, refusals, controls;
END $$;

INSERT INTO brain.schema_migration (version, name)
  VALUES (70, '0070_identity_is_github_and_postgres')
  ON CONFLICT (version) DO NOTHING;

COMMIT;
