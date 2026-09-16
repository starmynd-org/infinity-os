"""2026-09-09-IOS-term-5: strict red/green, rollback/reapply proof; scratch stores only.

Run this file with ENGINE_SCRATCH_DB, T5_SOURCE_SHA, and --receipt /absolute/log.txt.
Every child's full output and exit status is retained. A wrong red is a failed proof.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
DB = os.environ.get("ENGINE_SCRATCH_DB", "")
if not DB or "scratch" not in DB or DB in ("brain", "brain_scratch"):
    raise SystemExit("refusing unnamed, shared or live store")
SHA = os.environ["T5_SOURCE_SHA"]
os.environ["BRAIN_PG_DB"] = DB
os.environ["BRAIN_AFTER_COMMIT_HOOKS"] = "0"
os.environ["T5_SKIP_PREFLIGHT"] = "1"
os.environ.pop("BRAIN_HUMAN", None)
parser = argparse.ArgumentParser()
parser.add_argument("--receipt", required=True)
args = parser.parse_args()
receipt = Path(args.receipt)
receipt.parent.mkdir(parents=True, exist_ok=True)
log = receipt.open("x", encoding="utf-8")
def say(value):
    print(value, flush=True)
    log.write(str(value) + "\n")
    log.flush()

say(json.dumps({"seat": "2026-09-09-IOS-term-5", "source_sha": SHA,
                "store": DB, "root": str(ROOT), "invocation": sys.argv,
                "utc": subprocess.check_output(["date", "-u", "+%Y%m%dT%H%M%SZ"], text=True).strip()}))
for name in ("store/authority.py", "migrations/0073_unattended_access_is_a_service_credential_per_workspace.sql",
             "migrations/0074_urgency_has_four_words_and_high_is_not_one.sql",
             "migrations/tests/test_deprovisioning_is_two_sided.py", "migrations/tests/prove_73_74_and_part2.py"):
    say(f"sha256 {name} {hashlib.sha256((ROOT / name).read_bytes()).hexdigest()}")

completed = 0
def run(label, command, expected=0, contains=(), sql=None):
    global completed
    say(json.dumps({"predicate": label, "expected_exit": expected, "invocation": command}))
    result = subprocess.run(command, input=sql, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, timeout=300)
    say(result.stdout)
    say(f"exit={result.returncode}")
    if result.returncode != expected or any(text not in result.stdout for text in contains):
        raise AssertionError(f"unexpected result: {label}")
    completed += 1
    return result.stdout

def python(name, *extra, **kwargs):
    return run(name, [sys.executable, str(ROOT / "migrations/tests" / name), *extra], **kwargs)

def psql(label, sql, **kwargs):
    return run(label, [str(ROOT / "engine/bin/scratch-db.sh"), "psql", "-tA", "-f", "-"], sql=sql, **kwargs)

def migration(name, **kwargs):
    return psql(name, (ROOT / "migrations" / name).read_text(), **kwargs)

def restore():
    for version, name in ((73, "0073_unattended_access_is_a_service_credential_per_workspace.sql"),
                          (74, "0074_urgency_has_four_words_and_high_is_not_one.sql")):
        out = psql(f"is {version} applied", f"SELECT count(*) FROM brain.schema_migration WHERE version={version};")
        if out.strip() == "0":
            migration(name)

try:
    psql("store identity and ledger", "SELECT current_database(),max(version),count(*) FROM brain.schema_migration;", contains=(DB,))
    python("run_authority_pair.py", "--plant-old-writer", expected=1, contains=("5 failed, 23 passed",))
    python("run_authority_pair.py", contains=("28 passed",))
    python("probe_approval_decider_login.py", contains=("REFUSED 42501", "1 of 1 probe inserts attempted"))
    python("probe_approval_writer_login.py", "--plant-old-writer", expected=1,
           contains=("PLANTED OLD WRITER", "which is not a human login"))
    python("probe_approval_writer_login.py", contains=("3 of 3 writer-login predicates passed",))
    migration("tests/rollback-0074.sql", contains=("rolled back 74",))
    python("probe_urgency_vocabulary.py", expected=1, contains=("raw urgency high row: LANDED", "1 of 3 urgency predicates passed"))
    migration("tests/rollback-0073.sql", contains=("rolled back 73",))
    python("test_deprovisioning_is_two_sided.py", expected=1,
           contains=("FAIL  scene 13: brain_runtime wrote a receipt", "12 passed, 1 failed  (13 scenes",))
    migration("0073_unattended_access_is_a_service_credential_per_workspace.sql", contains=("8 of 8 checks passed",))
    python("test_deprovisioning_is_two_sided.py", contains=("13 passed, 0 failed  (13 scenes",))
    migration("0074_urgency_has_four_words_and_high_is_not_one.sql", contains=("5 of 5 checks passed",))
    python("probe_urgency_vocabulary.py", contains=("3 of 3 urgency predicates passed",))
    python("test_deprovisioning_is_two_sided.py", contains=("13 passed, 0 failed  (13 scenes",))
    psql("ephemeral service role removed", "SELECT count(*) FROM pg_roles WHERE rolname='brain_ws_ios_t5_ws';", contains=("0\n",))
    say(f"PASS: {completed} of 16 proof steps matched; 2026-09-09-IOS-term-5; source={SHA}; store={DB}")
    assert completed == 16
finally:
    restore()
    log.close()
