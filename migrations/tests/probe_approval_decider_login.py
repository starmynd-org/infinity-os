#!/usr/bin/env python3
"""PROBE: is brain.approval.decided_by tied to the LOGIN, or only to a NAME the caller passes?

2026-09-09, 2026-09-09-IOS-term-5, while planting the two-sided identity test. Migration 36 made
brain.work_item.accepted_by the database's answer (accepted_by must equal brain.current_human()).
Migrations 61 and 63 check that approval.decided_by is a name in brain.human_role and that the
grant belongs to that name. Read as written, neither compares decided_by to session_user.

This probe MEASURES that rather than asserting it: from brain_runtime, the login every agent
surface holds, it inserts an approval decided_by=<a real human> over a real grant, and reports
whether the store LANDED it. It cleans up after itself. Scratch only.

    ENGINE_SCRATCH_DB=ios_term5_scratch BRAIN_PG_DB=ios_term5_scratch \\
        python3 migrations/tests/probe_approval_decider_login.py
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
AGENT = "_ios_t5_probe_agent"
WS = "ios-t5-probe-ws"


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


def main():
    su(f"INSERT INTO brain.agent (name, role, status, host, updated) VALUES ('{AGENT}', 'worker', "
       f"'idle', 'probe', now()) ON CONFLICT (name) DO UPDATE SET updated = now()")
    grant = as_login("operator", None,
                     "INSERT INTO brain.authority_grant (granted_by, workspace, subject_kind, subject, "
                     "capability, scope, expires_at, evidence) VALUES ('operator', %s, 'human', %s, "
                     "'approval.decide', 'lane/probe', now() + interval '1 hour', "
                     "'probe_approval_decider_login') RETURNING grant_seq", (WS, HUMAN))[0][0]
    who = as_login("runtime", None, "SELECT session_user, brain.current_human()")[0]
    print(f"probe login: session_user={who[0]} brain.current_human()={who[1]}")
    landed = ""
    try:
        seq = as_login("runtime", None,
                       "INSERT INTO brain.approval (proposal_id, proposal_version, workspace, decided_by, "
                       "subject, grant_seq, decision) VALUES ('ios-t5-probe', 'v1', %s, %s, %s, %s, "
                       "'approve') RETURNING approval_seq", (WS, HUMAN, AGENT, grant))[0][0]
        landed = f"LANDED as approval_seq {seq}"
    except psycopg2.Error as e:
        landed = f"REFUSED {e.pgcode}: {str(e).strip().splitlines()[0]}"
    print(f"from brain_runtime, INSERT brain.approval decided_by={HUMAN!r}: {landed}")
    su("ALTER TABLE brain.approval DISABLE TRIGGER approval_append_only; "
       "ALTER TABLE brain.authority_grant DISABLE TRIGGER authority_grant_append_only; "
       "DELETE FROM brain.approval WHERE proposal_id = 'ios-t5-probe'; "
       "DELETE FROM brain.authority_grant WHERE evidence = 'probe_approval_decider_login'; "
       "ALTER TABLE brain.authority_grant ENABLE TRIGGER authority_grant_append_only; "
       "ALTER TABLE brain.approval ENABLE TRIGGER approval_append_only; "
       f"DELETE FROM brain.agent WHERE name = '{AGENT}'")
    print("1 of 1 probe inserts attempted; rows cleaned up")


if __name__ == "__main__":
    main()
