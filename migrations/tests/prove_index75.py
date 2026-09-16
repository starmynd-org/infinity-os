"""2026-09-09-IOS-term-5: wrong-shape plant, apply, rollback and reapply on scratch."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone

root = Path(__file__).resolve().parents[2]
db = os.environ.get("ENGINE_SCRATCH_DB", "")
assert "scratch" in db and db not in ("brain", "brain_scratch")
parser = argparse.ArgumentParser()
parser.add_argument("--receipt", type=Path, required=True)
parser.add_argument("--start-installed", action="store_true",
                    help="explicitly roll back an already-applied migration 75 before the planted campaign")
args = parser.parse_args()
report = {"seat":"2026-09-09-IOS-term-5","sha":os.environ["T5_SOURCE_SHA"],"store":db,
          "utc":datetime.now(timezone.utc).isoformat(),"invocation":sys.argv,"steps":[]}
migration = root / "migrations/0075_done_unaccepted_has_a_selective_index.sql"
rollback = root / "migrations/tests/rollback-0075.sql"
report["input_sha256"] = {p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in (migration,rollback,Path(__file__))}
def run(label, source, expected=0):
    command = [str(root / "engine/bin/scratch-db.sh"),"psql","-tA","-v","ON_ERROR_STOP=1","-f","-"]
    result = subprocess.run(command,input=source,text=True,capture_output=True)
    report["steps"].append({"label":label,"invocation":command,"sql":source,"exit":result.returncode,
                            "stdout":result.stdout,"stderr":result.stderr})
    assert (result.returncode == 0) == (expected == 0), (label,result.returncode,result.stderr)
    print(label, "exit",result.returncode,flush=True)
    return result
def snapshot(label):
    return run(label,"SELECT count(*), md5(coalesce(jsonb_agg(to_jsonb(w) ORDER BY id)::text,'[]')) FROM brain.work_item w;").stdout.strip()
success = False
mutated = False
try:
    if args.start_installed:
        installed = run("explicit installed precondition", "SELECT to_regclass('brain.work_item_done_unaccepted_idx') IS NOT NULL, EXISTS(SELECT 1 FROM brain.schema_migration WHERE version=75);").stdout.strip()
        assert installed == "t|t", installed
        run("requested initial rollback",rollback.read_text())
    status = run("initial index and ledger absent", "SELECT to_regclass('brain.work_item_done_unaccepted_idx') IS NULL, NOT EXISTS(SELECT 1 FROM brain.schema_migration WHERE version=75);").stdout.strip()
    assert status == "t|t", status
    baseline = snapshot("row baseline")
    mutated = True
    run("plant wrong same-name index", "CREATE INDEX work_item_done_unaccepted_idx ON brain.work_item(state) WHERE state='active';")
    wrong = run("wrong shape refused",migration.read_text(),1)
    assert "same-name index has the wrong shape" in wrong.stderr
    run("remove wrong-shape plant", "DROP INDEX brain.work_item_done_unaccepted_idx;")
    for phase, path, present in (("apply",migration,True),("rollback",rollback,False),("reapply",migration,True)):
        run(phase,path.read_text())
        state = run(phase+" shape and ledger", "SELECT to_regclass('brain.work_item_done_unaccepted_idx') IS NOT NULL, EXISTS(SELECT 1 FROM brain.schema_migration WHERE version=75);").stdout.strip()
        assert state == ("t|t" if present else "f|f"), state
        assert snapshot(phase+" row equality") == baseline
    report["verdict"] = "4 of 4 branches matched: wrong-shape refusal, apply, rollback, reapply; rows unchanged"
    success = True
except Exception as error:
    report["error"] = {"type":type(error).__name__,"message":str(error)}
    raise
finally:
    try:
        if not success:
            report["verdict"] = "FAILED; inspect individual steps; no pre-existing index or version is removed"
            if mutated:
                run("failure cleanup",rollback.read_text())
    finally:
        args.receipt.write_text(json.dumps(report,indent=2)+"\n")
