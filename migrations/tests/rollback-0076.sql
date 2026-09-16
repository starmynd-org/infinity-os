-- 2026-09-09-IOS-term-5: rollback76, preserving rows but LOSING repo-set bindings.
-- Run only on an explicitly named scratch store. It restores both known weaknesses.
-- Reapply leaves surviving receipts unbound; historical snapshots cannot be recovered.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL search_path = pg_catalog, pg_temp;
DO $$
DECLARE head integer; occupied text; bound bigint;
BEGIN
  SELECT max(version) INTO head FROM brain.schema_migration;
  SELECT name INTO occupied FROM brain.schema_migration WHERE version=76;
  IF head IS DISTINCT FROM 76 OR occupied IS DISTINCT FROM '0076_repo_receipts_bind_the_set_and_hosts_agree' THEN
    RAISE EXCEPTION 'rollback76 requires exact latest migration76, got head % and name %', head,occupied
      USING ERRCODE='object_not_in_prerequisite_state';
  END IF;
  LOCK TABLE brain.workspace,brain.workspace_repo,brain.repo_access_receipt IN ACCESS EXCLUSIVE MODE;
  SELECT count(*) INTO bound FROM brain.repo_access_receipt WHERE repo_set IS NOT NULL;
  RAISE NOTICE 'rollback76: permanently losing % recorded repo-set snapshot(s); receipt rows otherwise retained',bound;
END $$;
DROP TRIGGER repo_access_receipt_names_repo_set ON brain.repo_access_receipt;
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

DROP FUNCTION brain.repo_access_receipt_names_repo_set();
DROP FUNCTION brain.repo_access_receipt_covers_current_set(bigint);
DROP FUNCTION brain.workspace_repo_set(text);
ALTER TABLE brain.workspace_repo DROP CONSTRAINT workspace_repo_matches_parent_host;
ALTER TABLE brain.workspace DROP CONSTRAINT workspace_repo_host_key;
ALTER TABLE brain.repo_access_receipt DROP COLUMN repo_set;
DELETE FROM brain.schema_migration WHERE version=76;
COMMIT;
