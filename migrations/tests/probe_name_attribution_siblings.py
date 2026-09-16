#!/usr/bin/env python3
"""PROBE: the four sibling tables where a human's name is CHECKED but never tied to the LOGIN.

Row IDN-DECIDER-FORGE-SIBLINGS-01 (term-13, 2026-09-09). migrations/USER-SCOPING-INVENTORY.md marks
four tables NAME: authority_grant.granted_by, authority_revocation.revoked_by,
capture_dead_letter_reconciliation.resolved_by and repo_access_receipt.human. Each is checked
against the roster (or the actor list) and none is compared to session_user. This probe MEASURES
that, per table, from brain_runtime, the login every agent surface holds, naming a real human.
It prints attempted and landed per table and cleans up after itself. Scratch only.

repo_access_receipt is listed because the inventory listed it, and its result is printed with the
distinction migration 70's header draws: its `human` column is the SUBJECT of a reading, not the
actor; the actor is `checked_by`, which defaults to session_user. A landed insert there is 70's
stated residual 2 (the writer's trust), not a name forgery of the other three's shape.

    ENGINE_SCRATCH_DB=ios_term5_scratch BRAIN_PG_DB=ios_term5_scratch \\
        python3 migrations/tests/probe_name_attribution_siblings.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

DB = os.environ.get("ENGINE_SCRATCH_DB", "")
if not DB or "scratch" not in DB or DB in ("brain", "brain_scratch"):
    sys.stderr.write("NOT RUN: ENGINE_SCRATCH_DB must name your own scratch database\n")
    sys.exit(77)
os.environ["BRAIN_PG_DB"] = DB

import psycopg2                                                 # noqa: E402
from store import session                                       # noqa: E402

SCRATCH = str(ROOT / "engine" / "bin" / "scratch-db.sh")
HUMAN = "lane-e-two"
AGENT = "_ios_t5_sib_agent"
WS = "ios-t5-sib-ws"
DL = "dl_ios_t5_sib_probe"


def su(sql):
    r = subprocess.run([SCRATCH, "psql", "-tA", "-c", sql], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip())
    return r.stdout.strip()


def as_login(role, human, sql, params=None):
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


def attempt(table, sql, params):
    try:
        rows = as_login("runtime", None, sql, params)
        ident = rows[0][0] if rows and rows[0] else "?"
        print(f"  {table}: from brain_runtime naming {HUMAN!r}: LANDED (key {ident})")
        return True, ident
    except psycopg2.Error as e:
        first = str(e).strip().splitlines()[0]
        print(f"  {table}: from brain_runtime naming {HUMAN!r}: REFUSED {e.pgcode}: {first}")
        return False, None


def main():
    who = as_login("runtime", None, "SELECT session_user, brain.current_human()")[0]
    print(f"probe login: session_user={who[0]} brain.current_human()={who[1]}")
    roster = [r[0] for r in as_login("runtime", None, "SELECT human FROM brain.human_roster()")]
    if HUMAN not in roster:
        print(f"NOT RUN: {HUMAN} is not on this store's roster ({len(roster)} humans)")
        sys.exit(77)
    su(f"INSERT INTO brain.agent (name, role, status, host, updated) VALUES ('{AGENT}', 'worker', "
       f"'idle', 'probe', now()) ON CONFLICT (name) DO UPDATE SET updated = now()")
    attempted = landed = 0
    results = {}

    # 1. authority_grant.granted_by
    attempted += 1
    ok, g = attempt("authority_grant.granted_by",
                    "INSERT INTO brain.authority_grant (granted_by, workspace, subject_kind, subject, "
                    "capability, scope, expires_at, evidence) VALUES (%s, %s, 'agent', %s, "
                    "'effect.external', 'lane/sib', now() + interval '1 hour', "
                    "'probe_name_attribution_siblings') RETURNING grant_seq", (HUMAN, WS, AGENT))
    landed += ok
    results["authority_grant"] = ok

    # 2. authority_revocation.revoked_by, on that grant (or on one the operator makes if 1 refused)
    if g is None:
        g = as_login("operator", None,
                     "INSERT INTO brain.authority_grant (granted_by, workspace, subject_kind, subject, "
                     "capability, scope, expires_at, evidence) VALUES ('operator', %s, 'agent', %s, "
                     "'effect.external', 'lane/sib', now() + interval '1 hour', "
                     "'probe_name_attribution_siblings') RETURNING grant_seq", (WS, AGENT))[0][0]
    attempted += 1
    ok, _ = attempt("authority_revocation.revoked_by",
                    "INSERT INTO brain.authority_revocation (grant_seq, workspace, revoked_by, reason) "
                    "VALUES (%s, %s, %s, 'probe_name_attribution_siblings') RETURNING revocation_seq",
                    (g, WS, HUMAN))
    landed += ok
    results["authority_revocation"] = ok

    # 3. capture_dead_letter_reconciliation.resolved_by
    as_login("runtime", None,
             "INSERT INTO brain.capture_dead_letter (dead_letter_id, source_key, source_ref, reason, "
             "detail, received_at) VALUES (%s, 'sk_ios_t5', 'probe/sib', 'payload-refused', "
             "'a probe row', now())", (DL,))
    attempted += 1
    ok, _ = attempt("capture_dead_letter_reconciliation.resolved_by",
                    "INSERT INTO brain.capture_dead_letter_reconciliation (dead_letter_id, resolution, "
                    "resolved_by, resolved_at, note) VALUES (%s, 'still-unknown', %s, now(), "
                    "'probe_name_attribution_siblings') RETURNING reconciliation_seq", (DL, HUMAN))
    landed += ok
    results["capture_dead_letter_reconciliation"] = ok

    # 4. repo_access_receipt.human, with the workspace and link the operator declares
    have_70 = su("SELECT count(*) FROM information_schema.tables WHERE table_schema='brain' "
                 "AND table_name='repo_access_receipt'") == "1"
    if have_70:
        as_login("operator", None, "INSERT INTO brain.workspace (workspace, declared_by) VALUES (%s, 'operator')", (WS,))
        as_login("operator", None, "INSERT INTO brain.workspace_repo (workspace, repo_full_name, added_by) "
                                    "VALUES (%s, 'starmynd-org/sib', 'operator')", (WS,))
        as_login("operator", None, "INSERT INTO brain.human_repo_host_identity (human, host_login, linked_by) "
                                    "VALUES (%s, %s, 'operator')", (HUMAN, HUMAN + "-gh"))
        attempted += 1
        ok, _ = attempt("repo_access_receipt.human (the SUBJECT of a reading; actor is checked_by)",
                        "INSERT INTO brain.repo_access_receipt (human, host_login, workspace, reaches, "
                        "repos_checked, repos_reached) VALUES (%s, %s, %s, true, 1, 1) RETURNING receipt_seq",
                        (HUMAN, HUMAN + "-gh", WS))
        landed += ok
        results["repo_access_receipt"] = ok
        print("    (a landed receipt is migration 70's stated residual 2, the writer login's trust, "
              "not a forged actor: checked_by is session_user)")
    else:
        print("  repo_access_receipt: table absent on this store (below ledger 70); 0 of 1 attempted")

    # cleanup, superuser, triggers off for the deletes
    su(f"""DO $$ BEGIN
      ALTER TABLE brain.authority_revocation DISABLE TRIGGER USER;
      ALTER TABLE brain.authority_grant DISABLE TRIGGER USER;
      ALTER TABLE brain.capture_dead_letter_reconciliation DISABLE TRIGGER USER;
      ALTER TABLE brain.capture_dead_letter DISABLE TRIGGER USER;
      DELETE FROM brain.authority_revocation WHERE reason = 'probe_name_attribution_siblings';
      DELETE FROM brain.authority_grant WHERE evidence = 'probe_name_attribution_siblings';
      DELETE FROM brain.capture_dead_letter_reconciliation WHERE dead_letter_id = '{DL}';
      DELETE FROM brain.capture_dead_letter WHERE dead_letter_id = '{DL}';
      ALTER TABLE brain.capture_dead_letter ENABLE TRIGGER USER;
      ALTER TABLE brain.capture_dead_letter_reconciliation ENABLE TRIGGER USER;
      ALTER TABLE brain.authority_grant ENABLE TRIGGER USER;
      ALTER TABLE brain.authority_revocation ENABLE TRIGGER USER;
      IF to_regclass('brain.repo_access_receipt') IS NOT NULL THEN
        ALTER TABLE brain.repo_access_receipt DISABLE TRIGGER USER;
        DELETE FROM brain.repo_access_receipt WHERE workspace = '{WS}';
        ALTER TABLE brain.repo_access_receipt ENABLE TRIGGER USER;
        DELETE FROM brain.human_repo_host_identity WHERE human = '{HUMAN}';
        DELETE FROM brain.workspace_repo WHERE workspace = '{WS}';
        DELETE FROM brain.workspace WHERE workspace = '{WS}';
      END IF;
      DELETE FROM brain.agent WHERE name = '{AGENT}';
    END $$""")
    left = su(f"SELECT (SELECT count(*) FROM brain.authority_grant WHERE evidence = 'probe_name_attribution_siblings') "
              f"+ (SELECT count(*) FROM brain.capture_dead_letter WHERE dead_letter_id = '{DL}')")
    print(f"\n{landed} of {attempted} forged-name inserts LANDED from brain_runtime on {DB}; "
          f"per table: {results}; rows left behind: {left}")


if __name__ == "__main__":
    main()
