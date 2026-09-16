#!/usr/bin/env python3
"""A HUMAN REMOVED FROM GITHUB BUT NOT FROM POSTGRES MUST BE REFUSED.

Planted 2026-09-09 by 2026-09-09-IOS-term-5, BEFORE the schema that makes it pass, on Andrew's
ruling recorded in ARCHITECTURE-2026-09-09-tenancy-and-the-git-boundary.md, decision B:

    "they both are required to then get access. You have to add them to GitHub, and you also have
     to add them to Postgres, and their Postgres has to be signed in to GitHub."

and the failure mode he named for it: "a user removed from GitHub but not from Postgres is the
failure mode to guard. That is the test to plant."

WHY IT LIVES UNDER migrations/tests/ AND NOT engine/tests/. Seat term-5's declared file set is
`migrations/**` and the identity columns of `web/model.py`, and the 2026-09-09 fleet writes nothing
outside its set. `engine/tests/run-all.sh` has one owner and this seat is not it, so this suite is
NOT dispatched by any runner yet. That is stated here so it cannot be mistaken for coverage:
registering it is a request to the integrator, made on the bus, and until it lands the only thing
that runs this file is the command below.

WHAT IT PROVES, scene by scene. Every refusal is WATCHED FAILING FIRST: on a store at ledger 68 or
below, scene 4 lands an approval it must refuse, and this suite exits 1 saying so in words. The
schema that turns it green is migrations 69 and 70, written after this file and named in it only
so a reader can find them.

  1  THE POSTGRES HALF EXISTS: `lane-e-two` is mapped in brain.human_role. Absent, NOT RUN (77).
  2  A WORKSPACE IS DECLARED, as a repo set, by a human login. The tenancy unit.
  3  THE GITHUB HALF IS LINKED: `lane-e-two` names a repo-host login. A link, not a permission.
  4  THE PLANT. No receipt of any repo-host check exists for (lane-e-two, workspace). An approval
     decided by lane-e-two in that workspace MUST BE REFUSED. Before 69/70 the store cannot tell a
     human removed from GitHub from one who was never there, and the approval LANDS.
  5  POSITIVE CONTROL. A fresh receipt says lane-e-two reaches every repo in the set; the same
     approval lands.
  6  REMOVED FROM GITHUB, STILL IN POSTGRES. The newest receipt says lane-e-two no longer reaches
     the set. The approval is refused, the refusal names both halves, and brain.human_role still
     holds the row: nothing in Postgres was touched, and access is gone anyway.
  7  A RECEIPT DECAYS. A receipt that says reaches=true but is older than
     brain.repo_access_receipt_ttl() grants nothing. A reading with a date on it is not current.
  8  A RECEIPT CANNOT BE FUTURE-DATED, which is the only way to stretch one.
  9  VISIBILITY IS DERIVED, NOT MAINTAINED: brain.workspaces_visible_to('lane-e-two') follows the
     newest receipt and nothing else.
 10  THE MIRROR. A receipt for a name brain.human_roster() does not know is refused, and
     brain.human_reaches() is false for it: removed from Postgres but on GitHub is the other half.
 11  THE RESIDUAL, STATED. An approval in a workspace NOBODY DECLARED is not gated by this
     migration. The gate begins at declaration, exactly as migration 63's constraints begin at 63.
     Printed as a control so the residual is read rather than inferred.
 12  RECEIPTS ARE APPEND-ONLY: UPDATE and DELETE are refused from the login that writes them.
 13  THE RECEIPT WRITER IS THE WORKSPACE'S SERVICE (migration 73). brain_runtime, the login every
     agent holds, may not write one; a throwaway service login this suite mints and drops can.
     Watched red at ledger 72 (runtime landed a receipt) and green at 73.

NEEDS: a scratch database whose name contains `scratch`, the operator credential, and the
`lane-e-two` credential (both already on this host under ~/.brain-postgres-secrets). Run from WSL,
where docker and psycopg2 are:

    ENGINE_SCRATCH_DB=ios_term5_scratch engine/bin/scratch-db.sh ensure
    store/bin/provision-human.sh --db ios_term5_scratch --human lane-e-two
    ENGINE_SCRATCH_DB=ios_term5_scratch BRAIN_PG_DB=ios_term5_scratch \\
        python3 migrations/tests/test_deprovisioning_is_two_sided.py

Exit 0 green with the denominator on the line, 1 red, 77 NOT RUN with the missing input named.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "engine" / "tests"))

DB = os.environ.get("ENGINE_SCRATCH_DB", "")
if not DB or "scratch" not in DB or DB in ("brain", "brain_scratch"):
    sys.stderr.write("NOT RUN: ENGINE_SCRATCH_DB must name your own scratch database, containing "
                     "'scratch', never brain or brain_scratch. e.g. ios_term5_scratch\n")
    sys.exit(77)
os.environ["BRAIN_PG_DB"] = DB
os.environ["BRAIN_AFTER_COMMIT_HOOKS"] = "0"
os.environ.pop("BRAIN_HUMAN", None)

from _scratch_preflight import reconcile                        # noqa: E402
# T5_SKIP_PREFLIGHT=1 exists for ONE purpose: the rollback proof. The preflight reconciles the
# store to the tree, which re-applies 69 and 70 before the first scene runs, so a store that was
# just rolled back would read green and prove nothing about the rollback. Set it only when the
# ledger you want to assert against is deliberately BEHIND the tree, and say so in the report.
if os.environ.get("T5_SKIP_PREFLIGHT") == "1":
    print("  preflight: SKIPPED on request (T5_SKIP_PREFLIGHT=1); asserting against the store as it is")
else:
    reconcile(DB)

import psycopg2                                                 # noqa: E402
import psycopg2.errors                                          # noqa: E402
from store import session                                       # noqa: E402

SCRATCH = str(ROOT / "engine" / "bin" / "scratch-db.sh")
HUMAN = os.environ.get("T5_SECOND_HUMAN", "lane-e-two")
HOST_LOGIN = HUMAN + "-gh"
# Migration 73: a receipt is written only by the workspace's OWN service login. This suite mints
# a throwaway one on the scratch cluster (a login role, cluster-wide, like every role
# provision-human.sh mints), with a password that lives only in this process, and drops it at the
# end. Below ledger 73 the function that maps it does not exist and receipts fall back to
# brain_runtime, which is exactly 70's stated residual 2 and what scene 13 watches close.
SERVICE_ROLE = "brain_ws_ios_t5_ws"
SERVICE_PW: str | None = None
SERVICE_CREATED = False
WS = "ios-t5-ws"
WS_UNDECLARED = "ios-t5-undeclared"
REPO = "starmynd-org/ios-t5-example"
AGENT = "_ios_t5_agent"
PREFIX = "ios-t5-"

PASS = FAIL = 0
LEDGER = 0


def ok(msg):
    global PASS
    PASS += 1
    print(f"  ok    {msg}")


def bad(msg, detail=""):
    global FAIL
    FAIL += 1
    print(f"  FAIL  {msg}")
    if detail:
        for line in str(detail).strip().splitlines():
            print(f"        {line}")


def su(sql: str) -> str:
    """One statement as the bootstrap superuser, for FIXTURE only. Never for the act under test."""
    r = subprocess.run([SCRATCH, "psql", "-tA", "-f", "-"], input=sql,
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or r.stdout.strip())
    return r.stdout.strip()


def as_login(role: str, human: str | None, sql: str, params=None):
    """One statement on a REAL login. The gate is the database's, so the test reaches past every
    guard written in Python and asks the store directly, as that login."""
    conn = psycopg2.connect(**session.dsn(role, human=human))
    try:
        conn.set_session(readonly=False, autocommit=False)
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall() if cur.description else []
        conn.commit()
        return rows
    finally:
        conn.close()


def refused(role, human, sql, params=None) -> tuple[str, str]:
    """(sqlstate, message) if the statement was refused, ('', '') if it LANDED."""
    try:
        as_login(role, human, sql, params)
        return "", ""
    except psycopg2.Error as e:
        return (e.pgcode or ""), (str(e) or e.__class__.__name__)


def as_service(sql, params=None):
    """One statement as the workspace's service login, or as brain_runtime below ledger 73."""
    if SERVICE_PW is None:
        return as_login("runtime", None, sql, params)
    base = session.dsn("runtime")
    conn = psycopg2.connect(host=base["host"], port=base["port"], dbname=base["dbname"],
                            user=SERVICE_ROLE, password=SERVICE_PW,
                            options="-c search_path=brain,public", connect_timeout=5)
    try:
        conn.set_session(readonly=False, autocommit=False)
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall() if cur.description else []
        conn.commit()
        return rows
    finally:
        conn.close()


def refused_as_service(sql, params=None) -> tuple[str, str]:
    try:
        as_service(sql, params)
        return "", ""
    except psycopg2.Error as e:
        return (e.pgcode or ""), (str(e) or e.__class__.__name__)


def drop_service_role():
    """Idempotent: revoke what the role holds in this database and drop it, if it exists."""
    global SERVICE_CREATED, SERVICE_PW
    su(f"""DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{SERVICE_ROLE}') THEN
        IF to_regclass('brain.workspace_service_role') IS NOT NULL THEN
          DELETE FROM brain.workspace_service_role WHERE role_name = '{SERVICE_ROLE}';
        END IF;
        EXECUTE 'DROP OWNED BY {SERVICE_ROLE}';
        EXECUTE 'DROP ROLE {SERVICE_ROLE}';
      END IF;
    END $$""")
    SERVICE_CREATED = False
    SERVICE_PW = None


def provision_service_role() -> bool:
    """Mint the throwaway login and map it as WS's service through migration 73's function.
    Returns False, leaving receipts on brain_runtime, when the function is absent (ledger < 73)."""
    global SERVICE_PW, SERVICE_CREATED
    if not present("function", "provision_workspace_service_role"):
        return False
    import secrets as _secrets
    pw = _secrets.token_urlsafe(24)
    # The ephemeral password goes over stdin, never a process command line or a receipt.
    try:
        su(f"CREATE ROLE {SERVICE_ROLE} LOGIN PASSWORD '{pw}' NOSUPERUSER NOCREATEDB NOCREATEROLE")
    except RuntimeError:
        raise RuntimeError("scratch service role creation failed; secret-bearing SQL withheld") from None
    SERVICE_CREATED = True
    su(f"SELECT brain.provision_workspace_service_role('{SERVICE_ROLE}', '{WS}')")
    SERVICE_PW = pw
    return True


def present(kind: str, name: str) -> bool:
    if kind == "table":
        return su(f"SELECT count(*) FROM information_schema.tables WHERE table_schema='brain' "
                  f"AND table_name='{name}'") == "1"
    return su(f"SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
              f"WHERE n.nspname='brain' AND p.proname='{name}'") != "0"


# ------------------------------------------------------------------ fixture, superuser only

def clean():
    """Remove this suite's own rows from a previous run, and nothing else. Append-only triggers are
    disabled for the delete and re-enabled, the way migration 63's self-check does it."""
    su(f"""
    DO $$
    BEGIN
      ALTER TABLE brain.approval DISABLE TRIGGER approval_append_only;
      ALTER TABLE brain.authority_grant DISABLE TRIGGER authority_grant_append_only;
      DELETE FROM brain.approval WHERE proposal_id LIKE '{PREFIX}%';
      DELETE FROM brain.authority_grant WHERE evidence = 'test_deprovisioning_is_two_sided';
      ALTER TABLE brain.authority_grant ENABLE TRIGGER authority_grant_append_only;
      ALTER TABLE brain.approval ENABLE TRIGGER approval_append_only;
      IF to_regclass('brain.repo_access_receipt') IS NOT NULL THEN
        ALTER TABLE brain.repo_access_receipt DISABLE TRIGGER repo_access_receipt_append_only;
        DELETE FROM brain.repo_access_receipt WHERE workspace LIKE '{PREFIX}%' OR human = '{HUMAN}';
        ALTER TABLE brain.repo_access_receipt ENABLE TRIGGER repo_access_receipt_append_only;
      END IF;
      IF to_regclass('brain.human_repo_host_identity') IS NOT NULL THEN
        DELETE FROM brain.human_repo_host_identity WHERE human = '{HUMAN}';
      END IF;
      IF to_regclass('brain.workspace_service_role') IS NOT NULL THEN
        DELETE FROM brain.workspace_service_role WHERE workspace LIKE '{PREFIX}%';
      END IF;
      IF to_regclass('brain.workspace_service_credential') IS NOT NULL THEN
        DELETE FROM brain.workspace_service_credential WHERE workspace LIKE '{PREFIX}%';
      END IF;
      IF to_regclass('brain.workspace_repo') IS NOT NULL THEN
        DELETE FROM brain.workspace_repo WHERE workspace LIKE '{PREFIX}%';
      END IF;
      IF to_regclass('brain.workspace') IS NOT NULL THEN
        DELETE FROM brain.workspace WHERE workspace LIKE '{PREFIX}%';
      END IF;
      DELETE FROM brain.agent WHERE name = '{AGENT}';
    END $$;
    """)


def fixture_agent_and_grant(workspace: str) -> int:
    """An agent to be the subject, and a grant that lets HUMAN decide approvals in `workspace`.
    The grant is the OPERATOR's act, from the operator's own login; migration 63 checks the rest."""
    su(f"INSERT INTO brain.agent (name, role, status, host, updated) VALUES "
       f"('{AGENT}', 'worker', 'idle', 'ios-t5', now()) ON CONFLICT (name) DO UPDATE SET updated = now()")
    rows = as_login("operator", None,
                    "INSERT INTO brain.authority_grant (granted_by, workspace, subject_kind, subject, "
                    "capability, scope, expires_at, evidence) VALUES ('operator', %s, 'human', %s, "
                    "'approval.decide', 'lane/ios-t5', now() + interval '1 hour', "
                    "'test_deprovisioning_is_two_sided') RETURNING grant_seq",
                    (workspace, HUMAN))
    return rows[0][0]


APPROVAL_SQL = ("INSERT INTO brain.approval (proposal_id, proposal_version, workspace, decided_by, "
                "subject, grant_seq, decision) VALUES (%s, 'v1', %s, %s, %s, %s, 'approve') "
                "RETURNING approval_seq")


def approve(proposal: str, workspace: str, grant_seq: int) -> tuple[str, str]:
    """The act under test, from HUMAN's OWN login."""
    return refused("operator", HUMAN, APPROVAL_SQL, (proposal, workspace, HUMAN, AGENT, grant_seq))


def receipt(reaches: bool, checked_at_sql: str = "now()", human: str = HUMAN, ws: str = WS):
    """The adapter's act: record what the repo host said. Written as the workspace's service login
    from ledger 73 on (migration 73), and as brain_runtime below it (70's residual 2).
    At76, obtain the set before the synthetic reading and submit that exact snapshot."""
    n_reached = 1 if reaches else 0
    if present("function", "workspace_repo_set"):
        from psycopg2.extras import Json
        snapshot = as_service("SELECT brain.workspace_repo_set(%s)", (ws,))[0][0]
        return refused_as_service(
            f"INSERT INTO brain.repo_access_receipt (human, repo_host, host_login, workspace, "
            f"checked_at, reaches, repos_checked, repos_reached, evidence, repo_set) VALUES "
            f"(%s, 'github', %s, %s, {checked_at_sql}, %s, 1, %s, "
            f"'{{\"source\": \"test_deprovisioning_is_two_sided\"}}'::jsonb, %s)",
            (human, HOST_LOGIN, ws, reaches, n_reached, Json(snapshot)))
    return refused_as_service(
                   f"INSERT INTO brain.repo_access_receipt (human, repo_host, host_login, workspace, "
                   f"checked_at, reaches, repos_checked, repos_reached, evidence) VALUES "
                   f"(%s, 'github', %s, %s, {checked_at_sql}, %s, 1, %s, "
                   f"'{{\"source\": \"test_deprovisioning_is_two_sided\"}}'::jsonb)",
                   (human, HOST_LOGIN, ws, reaches, n_reached))


# ------------------------------------------------------------------ the scenes

def main() -> int:
    global LEDGER
    LEDGER = int(su("SELECT max(version) FROM brain.schema_migration"))
    print(f"store {DB} at ledger {LEDGER}; human under test {HUMAN}; workspace {WS}")

    # 1. the Postgres half
    roster = [r[0] for r in as_login("runtime", None, "SELECT human FROM brain.human_roster()")]
    if HUMAN not in roster:
        print(f"  NOT RUN  {HUMAN} is not in brain.human_roster() on {DB} ({len(roster)} humans "
              f"mapped). Provision it:\n"
              f"    store/bin/provision-human.sh --db {DB} --human {HUMAN}")
        return 77
    ok(f"scene 1: the Postgres half exists, {HUMAN} is 1 of {len(roster)} humans in brain.human_roster()")

    clean()

    have = {
        "brain.workspace": present("table", "workspace"),
        "brain.workspace_repo": present("table", "workspace_repo"),
        "brain.human_repo_host_identity": present("table", "human_repo_host_identity"),
        "brain.repo_access_receipt": present("table", "repo_access_receipt"),
        "brain.human_reaches()": present("function", "human_reaches"),
        "brain.workspaces_visible_to()": present("function", "workspaces_visible_to"),
        "brain.repo_access_receipt_ttl()": present("function", "repo_access_receipt_ttl"),
    }
    missing = [k for k, v in have.items() if not v]
    print(f"  the AND: {len(have) - len(missing)} of {len(have)} schema objects present"
          + (f"; MISSING: {', '.join(missing)}" if missing else ""))

    # 2. the tenancy unit, declared by a human login
    code, msg = refused("operator", None,
                        "INSERT INTO brain.workspace (workspace, repo_host, declared_by) "
                        "VALUES (%s, 'github', 'operator')", (WS,))
    if code:
        bad(f"scene 2: declaring workspace {WS} as the operator", msg)
    else:
        code, msg = refused("operator", None,
                            "INSERT INTO brain.workspace_repo (workspace, repo_host, repo_full_name, "
                            "added_by) VALUES (%s, 'github', %s, 'operator')", (WS, REPO))
        if code:
            bad(f"scene 2: adding {REPO} to {WS}'s repo set", msg)
        else:
            ok(f"scene 2: workspace {WS} declared with a repo set of 1 ({REPO})")
    drop_service_role()
    if provision_service_role():
        print(f"  receipts below are written as {SERVICE_ROLE}, {WS}'s service login (migration 73)")
    else:
        print("  receipts below are written as brain_runtime: no migration 73 on this store")

    # 3. the GitHub half, linked
    code, msg = refused("operator", None,
                        "INSERT INTO brain.human_repo_host_identity (human, repo_host, host_login, "
                        "linked_by) VALUES (%s, 'github', %s, 'operator')", (HUMAN, HOST_LOGIN))
    if code:
        bad(f"scene 3: linking {HUMAN} to github login {HOST_LOGIN}", msg)
    else:
        ok(f"scene 3: {HUMAN} is linked to github login {HOST_LOGIN}")

    grant_seq = fixture_agent_and_grant(WS)

    # 4. THE PLANT: no receipt, so no evidence the human reaches the set. Must be refused.
    code, msg = approve(PREFIX + "no-receipt", WS, grant_seq)
    if not code:
        bad("scene 4: THE FAILURE MODE IS LIVE. With no repo-host receipt at all, an approval by "
            f"{HUMAN} in {WS} LANDED. This store cannot tell a human removed from GitHub from one "
            "who was never there: brain.human_role is the whole of identity here.",
            f"ledger {LEDGER}; the AND is missing {len(missing)} of {len(have)} objects")
    elif code == "42501" and "identity is both" in msg:
        ok(f"scene 4: with no receipt, the approval by {HUMAN} in {WS} was refused ({code})")
    else:
        bad("scene 4: refused, but not by the two-sided identity gate", f"{code}: {msg}")

    # 5. positive control: a fresh receipt that reaches, and the same approval lands
    code, msg = receipt(True)
    if code:
        bad("scene 5: recording a reaches=true receipt as brain_runtime", f"{code}: {msg}")
    else:
        code, msg = approve(PREFIX + "reaches", WS, grant_seq)
        if code:
            bad(f"scene 5: with a fresh reaches=true receipt the approval was STILL refused, so the "
                f"gate fires on everything and proves nothing", f"{code}: {msg}")
        else:
            ok(f"scene 5: with a fresh reaches=true receipt the approval by {HUMAN} in {WS} landed")

    # 6. THE TEST ANDREW NAMED: removed from GitHub, still in Postgres
    code, msg = receipt(False)
    if code:
        bad("scene 6: recording a reaches=false receipt", f"{code}: {msg}")
    else:
        still_mapped = su(f"SELECT count(*) FROM brain.human_role WHERE human = '{HUMAN}'") == "1"
        code, msg = approve(PREFIX + "removed-from-github", WS, grant_seq)
        if not code:
            bad(f"scene 6: {HUMAN} was removed from GitHub (newest receipt reaches=false) and is "
                f"still mapped in Postgres, and the approval LANDED")
        elif not still_mapped:
            bad("scene 6: refused, but brain.human_role no longer holds the human, so this was not "
                "the GitHub half refusing")
        elif code == "42501" and "identity is both" in msg and "github" in msg.lower():
            ok(f"scene 6: removed from GitHub, still in Postgres (brain.human_role holds {HUMAN}): "
               f"refused, and the refusal names both halves")
        else:
            bad("scene 6: refused, but not with the words a reader needs", f"{code}: {msg}")

    # 7. a receipt decays
    ttl = su("SELECT brain.repo_access_receipt_ttl()") if have["brain.repo_access_receipt_ttl()"] else "?"
    code, msg = receipt(True, checked_at_sql="now() - brain.repo_access_receipt_ttl() - interval '1 minute'")
    if code:
        bad("scene 7: recording a backdated reaches=true receipt", f"{code}: {msg}")
    else:
        code, msg = approve(PREFIX + "stale", WS, grant_seq)
        if not code:
            bad(f"scene 7: a reaches=true receipt older than the ttl ({ttl}) still granted access")
        else:
            ok(f"scene 7: a reaches=true receipt older than the ttl ({ttl}) grants nothing ({code})")

    # 8. a receipt cannot be future-dated
    code, msg = receipt(True, checked_at_sql="now() + interval '1 day'")
    if code == "23514" and "future" in msg.lower():
        ok(f"scene 8: a future-dated receipt is refused at insert ({code}, and the refusal says why)")
    elif code:
        bad("scene 8: refused, but not by the future-date check; a missing table also refuses",
            f"{code}: {msg}")
    else:
        bad("scene 8: a future-dated receipt was accepted, which is the one way to stretch a reading")

    # 9. visibility is derived from the newest receipt
    if have["brain.workspaces_visible_to()"]:
        seen_after_removal = [r[0] for r in as_login(
            "runtime", None, "SELECT * FROM brain.workspaces_visible_to(%s)", (HUMAN,))]
        receipt(True)
        seen_after_return = [r[0] for r in as_login(
            "runtime", None, "SELECT * FROM brain.workspaces_visible_to(%s)", (HUMAN,))]
        if WS in seen_after_removal:
            bad(f"scene 9: {WS} was visible to {HUMAN} while the newest receipt said reaches=false",
                f"visible: {seen_after_removal}")
        elif WS not in seen_after_return:
            bad(f"scene 9: {WS} was not visible to {HUMAN} after a fresh reaches=true receipt",
                f"visible: {seen_after_return}")
        else:
            ok(f"scene 9: visibility follows the newest receipt: 0 of 1 declared workspaces visible "
               f"after removal, 1 of 1 after return")
    else:
        bad("scene 9: brain.workspaces_visible_to() does not exist")

    # 10. the mirror: on GitHub, not in Postgres
    code, msg = receipt(True, human="ios-t5-ghost")
    reaches = su("SELECT brain.human_reaches('ios-t5-ghost', '" + WS + "')") \
        if have["brain.human_reaches()"] else "?"
    if code == "23503" and reaches == "f":
        ok(f"scene 10: a receipt for a name the roster does not know is refused ({code}), and "
           f"brain.human_reaches('ios-t5-ghost') is false: removed from Postgres is the other half")
    else:
        bad("scene 10: the mirror is open, or refused for a reason other than the roster",
            f"receipt insert: {code or 'LANDED'}; human_reaches: {reaches}")

    # 11. the residual, stated: an undeclared workspace is not gated
    grant_undeclared = fixture_agent_and_grant(WS_UNDECLARED)
    code, msg = approve(PREFIX + "undeclared", WS_UNDECLARED, grant_undeclared)
    if code:
        bad(f"scene 11: an approval in UNDECLARED workspace {WS_UNDECLARED} was refused ({code}); "
            f"the gate is wider than this file states, and every existing suite that names an "
            f"arbitrary workspace string is now red", msg)
    else:
        ok(f"scene 11: RESIDUAL, stated: workspace {WS_UNDECLARED} was never declared in "
           f"brain.workspace, and an approval there is NOT gated. The gate begins at declaration.")

    # 12. receipts are append-only from the login that writes them
    if have["brain.repo_access_receipt"]:
        c1, _ = refused_as_service(
                        "UPDATE brain.repo_access_receipt SET reaches = true WHERE human = %s", (HUMAN,))
        c2, _ = refused_as_service(
                        "DELETE FROM brain.repo_access_receipt WHERE human = %s", (HUMAN,))
        if c1 == "42501" and c2 == "42501":
            writer = SERVICE_ROLE if SERVICE_PW is not None else "brain_runtime"
            ok(f"scene 12: UPDATE ({c1}) and DELETE ({c2}) on a receipt are refused as {writer}")
        else:
            bad("scene 12: a receipt can be edited after the fact", f"update: {c1 or 'LANDED'}; "
                f"delete: {c2 or 'LANDED'}")
    else:
        bad("scene 12: brain.repo_access_receipt does not exist")

    # 13. the receipt writer is the workspace's service, and brain_runtime may not forge one
    code, msg = refused("runtime", None,
                        "INSERT INTO brain.repo_access_receipt (human, repo_host, host_login, workspace, "
                        "reaches, repos_checked, repos_reached) VALUES (%s, 'github', %s, %s, true, 1, 1)",
                        (HUMAN, HOST_LOGIN, WS))
    if code == "42501":
        ok(f"scene 13: a receipt from brain_runtime, the login every agent holds, is refused ({code}); "
           f"only {WS}'s own service login writes them")
    elif code:
        bad("scene 13: refused, but not as a privilege refusal", f"{code}: {msg}")
    else:
        bad("scene 13: brain_runtime wrote a receipt widening a human's reach. Migration 70's "
            "residual 2 is open on this store: unattended access is not yet a service credential "
            "scoped per workspace (migration 73)")

    drop_service_role()
    clean()
    return 0


if __name__ == "__main__":
    try:
        rc = main()
    except psycopg2.Error as exc:
        print(f"  FAIL  the suite itself hit a database error it did not plan for: {exc}")
        rc, FAIL = 1, FAIL + 1
    finally:
        if SERVICE_CREATED:
            drop_service_role()
    if rc == 77:
        sys.exit(77)
    total = PASS + FAIL
    if total == 0:                                                  # DENOMINATOR
        print("\n0 scenes ran. That is not a pass.")
        sys.exit(2)
    print(f"\n{PASS} passed, {FAIL} failed  ({total} scenes, store {DB} at ledger {LEDGER})")
    if FAIL:
        print("RED: the removed-from-GitHub-but-not-from-Postgres failure mode is not refused by "
              "this store, or the gate around it is not the shape this file states.")
    sys.exit(1 if FAIL else 0)
