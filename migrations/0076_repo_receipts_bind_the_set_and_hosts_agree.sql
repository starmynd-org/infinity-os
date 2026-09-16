-- Migration 76: a receipt names the repo set it checked, and parent/child hosts agree.
-- Signed: 2026-09-09-IOS-term-5. RELEASE15 and RELEASE16, separate predicates.
-- Version occupancy: identity-repair-preflight.json; own ios_term5_scratch max75,
-- 72 unique file versions across migrations/budget/queue, highest75;76 unused.
--
-- Existing69/70/73 remain unchanged. A complete, canonical JSON repo-set snapshot
-- is supplied by the service writer after asking about THAT snapshot. It is not
-- generated from current state at INSERT: that would bless an old asynchronous
-- reading as though the service had checked the replacement set.
-- The adapter is unbuilt. Its contract is: read workspace_repo_set(workspace),
-- ask the host about exactly that set, then submit the same value as repo_set.
-- The INSERT trigger refuses NULL or a set that changed before the reading lands.
-- The reader also compares the stored set, so a later same-count replacement
-- cannot keep an old receipt effective. Exact-set restoration may reuse a still-
-- fresh matching receipt; this is set equality, not monotonic invalidation.
--
-- HISTORICAL DATA IS NOT INVENTED. Existing receipts gain NULL repo_set and stay
-- intact. They no longer establish reach until a fresh bound reading arrives.
-- No historical set is backfilled from today's declarations. Count is reported.
-- New missing-binding receipts refuse explicitly; this is a receipt-writer API
-- transition, not an optional metadata field. The legitimate owned fixture uses it.
--
-- A composite FK refuses a parent host change with dependent repos and covers
-- concurrent referential writes. It never cascades a host rename into repo identity.
-- Empty parents may change host; the old workspace-only FK remains untouched.
-- Existing parent/child mismatches REFUSE with a count, never get relabelled.
--
-- ROLLBACK IS ONE-WAY IN BINDING DATA: rollback-0076.sql drops repo_set and loses
-- all recorded snapshots. Receipt rows otherwise remain. It restores70's reader
-- and reason, so its count-only weakness and parent-host weakness return.
-- Reapply cannot recover lost snapshots; surviving receipts are NULL/unbound and
-- need a fresh reading again. No row, service identity or user matrix is fabricated.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL search_path = pg_catalog, pg_temp;

DO $$
DECLARE occupied text; head integer; mismatches bigint;
BEGIN
  SELECT name INTO occupied FROM brain.schema_migration WHERE version=76;
  SELECT max(version) INTO head FROM brain.schema_migration;
  IF occupied IS NOT NULL AND occupied <> '0076_repo_receipts_bind_the_set_and_hosts_agree' THEN
    RAISE EXCEPTION 'migration76 version collision: occupied by %', occupied
      USING ERRCODE='duplicate_object';
  END IF;
  IF occupied IS NULL AND head IS DISTINCT FROM 75 THEN
    RAISE EXCEPTION 'migration76 requires predecessor75; actual ledger %', head
      USING ERRCODE='object_not_in_prerequisite_state';
  END IF;
  IF occupied IS NULL AND (
       EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid='brain.repo_access_receipt'::regclass
                AND attname='repo_set' AND NOT attisdropped)
       OR to_regprocedure('brain.workspace_repo_set(text)') IS NOT NULL
       OR to_regprocedure('brain.repo_access_receipt_covers_current_set(bigint)') IS NOT NULL
       OR to_regprocedure('brain.repo_access_receipt_names_repo_set()') IS NOT NULL
       OR EXISTS (SELECT 1 FROM pg_constraint WHERE connamespace='brain'::regnamespace
                   AND conname IN ('workspace_repo_host_key','workspace_repo_matches_parent_host'))
       OR EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid='brain.repo_access_receipt'::regclass
                   AND tgname='repo_access_receipt_names_repo_set')) THEN
    RAISE EXCEPTION 'migration76 wrong state: its column, function, trigger or constraint name exists without its ledger row'
      USING ERRCODE='duplicate_object';
  END IF;
  IF occupied IS NOT NULL AND NOT EXISTS (
       SELECT 1 FROM pg_attribute WHERE attrelid='brain.repo_access_receipt'::regclass
        AND attname='repo_set' AND atttypid='jsonb'::regtype AND NOT attnotnull AND NOT attisdropped) THEN
    RAISE EXCEPTION 'migration76 wrong state: recorded migration lacks its nullable jsonb repo_set column'
      USING ERRCODE='object_not_in_prerequisite_state';
  END IF;
  LOCK TABLE brain.workspace, brain.workspace_repo, brain.repo_access_receipt IN ACCESS EXCLUSIVE MODE;
  SELECT count(*) INTO mismatches FROM brain.workspace_repo r JOIN brain.workspace w USING(workspace)
    WHERE r.repo_host IS DISTINCT FROM w.repo_host;
  IF mismatches <> 0 THEN
    RAISE EXCEPTION 'migration76 refuses % existing parent/child host mismatch row(s); no identities were relabelled', mismatches
      USING ERRCODE='check_violation';
  END IF;
END $$;

ALTER TABLE brain.repo_access_receipt ADD COLUMN IF NOT EXISTS repo_set jsonb;
COMMENT ON COLUMN brain.repo_access_receipt.repo_set IS
  'The exact canonical workspace_repo_set snapshot that the service checked. NULL is historical/unbound and grants no reach. New receipts must supply a matching snapshot; rollback76 permanently loses it.';

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='brain.workspace'::regclass
                   AND conname='workspace_repo_host_key') THEN
    ALTER TABLE brain.workspace ADD CONSTRAINT workspace_repo_host_key UNIQUE(workspace,repo_host);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='brain.workspace_repo'::regclass
                   AND conname='workspace_repo_matches_parent_host') THEN
    ALTER TABLE brain.workspace_repo ADD CONSTRAINT workspace_repo_matches_parent_host
      FOREIGN KEY(workspace,repo_host) REFERENCES brain.workspace(workspace,repo_host) ON UPDATE RESTRICT;
  END IF;
END $$;

CREATE OR REPLACE FUNCTION brain.workspace_repo_set(p_workspace text) RETURNS jsonb
  LANGUAGE sql STABLE SET search_path = brain, pg_temp AS $$
  SELECT jsonb_build_object(
    'repo_host', w.repo_host,
    'repos', COALESCE((SELECT jsonb_agg(jsonb_build_object('repo_host',r.repo_host,'repo_full_name',r.repo_full_name)
                                     ORDER BY r.repo_host COLLATE "C",r.repo_full_name COLLATE "C")
                        FROM brain.workspace_repo r WHERE r.workspace=w.workspace), '[]'::jsonb))
    FROM brain.workspace w WHERE w.workspace=p_workspace
$$;
COMMENT ON FUNCTION brain.workspace_repo_set(text) IS
  'Canonical exact repo-set snapshot. Invoker privileges apply; it grants no table access. Read before asking the host and submit the SAME value with the resulting service receipt.';
GRANT EXECUTE ON FUNCTION brain.workspace_repo_set(text) TO PUBLIC;

CREATE OR REPLACE FUNCTION brain.repo_access_receipt_names_repo_set() RETURNS trigger
  LANGUAGE plpgsql SET search_path = brain, pg_temp AS $$
BEGIN
  IF NEW.repo_set IS NULL THEN
    RAISE EXCEPTION 'receipt for workspace % lacks the repo-set snapshot it checked; obtain and submit workspace_repo_set with the service reading', NEW.workspace
      USING ERRCODE='check_violation';
  END IF;
  IF NEW.repo_set IS DISTINCT FROM brain.workspace_repo_set(NEW.workspace) THEN
    RAISE EXCEPTION 'receipt for workspace % names a different declared repo set; the set changed or this reading checked another set', NEW.workspace
      USING ERRCODE='check_violation';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS repo_access_receipt_names_repo_set ON brain.repo_access_receipt;
CREATE TRIGGER repo_access_receipt_names_repo_set
  BEFORE INSERT ON brain.repo_access_receipt
  FOR EACH ROW EXECUTE FUNCTION brain.repo_access_receipt_names_repo_set();

CREATE OR REPLACE FUNCTION brain.repo_access_receipt_covers_current_set(p_receipt_seq bigint) RETURNS boolean
  LANGUAGE sql STABLE SET search_path = brain, pg_temp AS $$
  SELECT COALESCE((SELECT r.repo_set IS NOT NULL AND r.repo_set=brain.workspace_repo_set(r.workspace)
                    FROM brain.repo_access_receipt r WHERE r.receipt_seq=p_receipt_seq),false)
$$;
COMMENT ON FUNCTION brain.repo_access_receipt_covers_current_set(bigint) IS
  'Whether a retained receipt explicitly names the current declared set. Separate from host verdict, TTL and human identity; no permission is stored.';
GRANT EXECUTE ON FUNCTION brain.repo_access_receipt_covers_current_set(bigint) TO PUBLIC;

-- Keep70's SQL return signature and raw verdict/TTL components. Add the exact-set
-- predicate to reach. Existing model/SQL callers keep their return shape.
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
    SELECT r.receipt_seq, r.checked_at, r.reaches, r.repos_reached, r.repos_checked, r.repo_set
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
                       AND rc.repo_set IS NOT NULL
                       AND rc.repo_set = brain.workspace_repo_set(p_workspace)
                       AND rc.checked_at > now() - brain.repo_access_receipt_ttl(), false)) AS reach
    FROM pg LEFT JOIN rc ON true
$$;

-- Preserve every existing refusal branch; name the new unbound/changed-set
-- branch truthfully instead of falling through to the old stale-time explanation.
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
  ELSIF NOT brain.repo_access_receipt_covers_current_set(rep.receipt_seq) THEN
    half := format('the Postgres half holds (brain.human_role maps %s) and the repo-host reading '
                   'does not name the current declared repo set: receipt %s is unbound or the '
                   'set has changed. Obtain a fresh reading for the declared set.',
                   NEW.decided_by, rep.receipt_seq);
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

DO $$
DECLARE missing bigint; legacy bigint;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='brain.workspace'::regclass
      AND conname='workspace_repo_host_key' AND contype='u' AND convalidated
      AND pg_get_constraintdef(oid,true)='UNIQUE (workspace, repo_host)') THEN
    RAISE EXCEPTION 'migration76 wrong state: composite workspace key has the wrong shape'
      USING ERRCODE='object_not_in_prerequisite_state';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='brain.workspace_repo'::regclass
      AND conname='workspace_repo_matches_parent_host' AND contype='f' AND convalidated
      AND NOT condeferrable AND confupdtype='r'
      AND pg_get_constraintdef(oid,true)='FOREIGN KEY (workspace, repo_host) REFERENCES brain.workspace(workspace, repo_host) ON UPDATE RESTRICT') THEN
    RAISE EXCEPTION 'migration76 wrong state: host-agreement foreign key has the wrong shape'
      USING ERRCODE='object_not_in_prerequisite_state';
  END IF;
  SELECT count(*) INTO missing FROM brain.repo_access_receipt WHERE repo_set IS NOT NULL
    AND jsonb_typeof(repo_set) IS DISTINCT FROM 'object';
  IF missing <> 0 THEN
    RAISE EXCEPTION 'migration76 refuses % malformed existing repo-set snapshot(s); no binding was coerced', missing
      USING ERRCODE='check_violation';
  END IF;
  SELECT count(*) INTO legacy FROM brain.repo_access_receipt WHERE repo_set IS NULL;
  RAISE NOTICE 'migration76: composite-key and host-FK shape checks passed; % historical unbound receipt(s) retained and unable to establish reach until a fresh bound reading', legacy;
END $$;

INSERT INTO brain.schema_migration (version, name)
  VALUES (76,'0076_repo_receipts_bind_the_set_and_hosts_agree')
  ON CONFLICT(version) DO NOTHING;
COMMIT;
