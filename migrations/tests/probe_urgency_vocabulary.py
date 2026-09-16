"""2026-09-09-IOS-term-5: function and raw-row urgency predicates; transaction rolled back."""
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
DB = os.environ.get("ENGINE_SCRATCH_DB", "")
if "scratch" not in DB or DB in ("brain", "brain_scratch"):
    raise SystemExit("scratch database required")
os.environ["BRAIN_PG_DB"] = DB
import psycopg2
from store import session

passed = 0
conn = psycopg2.connect(**session.dsn("runtime"))
try:
    with conn.cursor() as cur:
        cur.execute("SELECT NOT brain.signal_ok('urgency','high')")
        denied = cur.fetchone()[0]
        passed += int(denied)
        print(f"function refuses urgency high: {denied}")
        cur.execute("SAVEPOINT invalid_urgency")
        try:
            cur.execute("INSERT INTO brain.work_item(title,lane,urgency) VALUES ('ios-t5 urgency plant','mv','high')")
        except psycopg2.errors.CheckViolation:
            passed += 1
            print("raw urgency high row: REFUSED 23514")
        else:
            print("raw urgency high row: LANDED")
        cur.execute("ROLLBACK TO SAVEPOINT invalid_urgency")
        cur.execute("INSERT INTO brain.work_item(title,lane,urgency) VALUES ('ios-t5 urgency control','mv','deadline')")
        passed += 1
        print("raw urgency deadline row: LANDED")
finally:
    conn.rollback()
    conn.close()
print(f"{passed} of 3 urgency predicates passed; store={DB}; transaction rolled back")
raise SystemExit(0 if passed == 3 else 1)
