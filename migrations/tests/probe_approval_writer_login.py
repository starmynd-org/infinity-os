"""2026-09-09-IOS-term-5: paired actual-login proof through store.apply, scratch only."""
import os
from pathlib import Path
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
DB = os.environ.get("ENGINE_SCRATCH_DB", "")
if "scratch" not in DB or DB in ("brain", "brain_scratch"):
    raise SystemExit("refusing unnamed, shared or live database")
os.environ["BRAIN_PG_DB"] = DB
os.environ["BRAIN_AFTER_COMMIT_HOOKS"] = "0"

import psycopg2
import store
import store.authority
from store import session
if "--plant-old-writer" in sys.argv:
    from store import transitions
    transitions._REGISTRY["approval decide"].role = "runtime"
    print("PLANTED OLD WRITER: the positive human-login predicate must now fail", flush=True)

def su(sql):
    result = subprocess.run([str(ROOT / "engine/bin/scratch-db.sh"), "psql", "-tA", "-f", "-"],
                            input=sql, capture_output=True, text=True, check=True)
    return result.stdout.strip()

tag = "ios-t5-writer-" + uuid.uuid4().hex
agent = "_" + tag
passed = 0
try:
    su(f"INSERT INTO brain.agent(name) VALUES ('{agent}')")
    for slug in ("operator", "lane-e-two"):
        with psycopg2.connect(**session.dsn("operator", human=slug)) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT current_database(), session_user, brain.current_human()")
                db, login, human = cur.fetchone()
                cur.execute("INSERT INTO brain.authority_grant(granted_by,workspace,subject_kind,"
                            "subject,capability,scope,expires_at,evidence) VALUES "
                            "(%s,%s,'human',%s,'approval.decide',%s,now()+interval '1 hour',%s) "
                            "RETURNING grant_seq", (human,tag,human,tag,tag))
                grant_seq = cur.fetchone()[0]
        assert db == DB and human == slug
        os.environ["BRAIN_HUMAN"] = slug
        row = store.apply("approval decide", workspace=tag, proposal_id=tag + slug,
                          proposal_version="v1", decided_by=human, subject=agent,
                          grant_seq=grant_seq)
        assert row["decided_by"] == human
        passed += 1
        print(f"LOGIN CONTROL {passed}/3: {login}, current_human={human}, decided_by={row['decided_by']}")
    try:
        os.environ["BRAIN_HUMAN"] = "operator"
        store.apply("approval decide", workspace=tag, proposal_id=tag + "forged",
                    proposal_version="v1", decided_by="lane-e-two", subject=agent,
                    grant_seq=grant_seq)
    except psycopg2.errors.InsufficientPrivilege:
        passed += 1
        print("FORGED HUMAN REFUSED 42501: operator cannot record lane-e-two's decision")
    else:
        raise AssertionError("forged human decision landed")
finally:
    su(f"""BEGIN;
    ALTER TABLE brain.approval DISABLE TRIGGER approval_append_only;
    ALTER TABLE brain.authority_grant DISABLE TRIGGER authority_grant_append_only;
    DELETE FROM brain.approval WHERE workspace = '{tag}';
    DELETE FROM brain.authority_grant WHERE workspace = '{tag}';
    ALTER TABLE brain.approval ENABLE TRIGGER approval_append_only;
    ALTER TABLE brain.authority_grant ENABLE TRIGGER authority_grant_append_only;
    DELETE FROM brain.agent WHERE name = '{agent}';
    COMMIT;""")
print(f"{passed} of 3 writer-login predicates passed; store={DB}; fixture removed; 2026-09-09-IOS-term-5")
assert passed == 3
