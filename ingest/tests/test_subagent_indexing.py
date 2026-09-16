#!/usr/bin/env python3
"""Task 0329: a subagent transcript must reach the store without a human running anything.

Run it:  python3 ingest/tests/test_subagent_indexing.py

The hole this closes, measured on the live store 2026-08-17: 698 subagent transcripts on disk
and 689 of them carrying the IDENTICAL `indexed_at` of 2026-08-16T12:20:40Z. One sweep, one
human, one command. `claude-session-hook` indexed exactly one file per session -- the main
transcript named in the SessionEnd payload -- and Claude Code fires no per-subagent event this
lane is wired to, so every subagent file born after that sweep had no row at all and the 9 that
existed were made by hand while closing task 0325.

What this pins, on BOTH profiles:

    the hook indexes the subagents   a real SessionEnd payload through the real hook binary
                                     leaves a row for every subagent file of that session, and
                                     for the workflow journal beside them
    the subagent session is real     each row is keyed `<parent_uuid>:<agent_id>`, the same
                                     convention `tx.scan` derives and the 733 pre-existing
                                     rows use, and the session row exists so the key resolves
                                     rather than booking an orphan
    a second pass writes NOTHING     re-running over already-indexed files leaves their
                                     `verified_at` intact. THIS is the claim: both profiles'
                                     transcript upserts null the verification state ON
                                     CONFLICT, so an insert-only selector is the safety
                                     property and not a speed-up (task 0318)
    and the unconditional pass does  the same corpus through plain `backfill.run` nulls
                                     `verified_at` on every row it walks. The contrast is
                                     measured here rather than asserted in a comment, because
                                     it is the whole reason `--only-missing` exists
    the budget is obeyed             a sweep given no time indexes nothing and reports what it
                                     deferred, instead of overrunning the hook's 8s bound
    one bad file loses only itself   a file that disappears between the walk and the scan is
                                     rolled back to its own SAVEPOINT; the files beside it in
                                     the same transaction still land

It builds its own databases and drops them, and each profile gets its own temporary projects
root. It never writes to `brain` or `d3_scratch`: those hold the live registry, which is
evidence in open questions. The brain-shaped database is a schema-only pg_dump copy of the live
one, so the test measures D1's schema as applied rather than an imitation of it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
LANE = os.path.dirname(HERE)
sys.path.insert(0, LANE)

os.environ.pop("BRAIN_DSN", None)
os.environ.pop("BRAIN_SCHEMA", None)

import psycopg2  # noqa: E402

from ingest import backfill, config, profiles, store  # noqa: E402

SCRATCH_TEST_DB = os.environ.get("BRAIN_SUBAGENT_TEST_DB_SCRATCH", "d3_subagent_test_scratch")
BRAIN_TEST_DB = os.environ.get("BRAIN_SUBAGENT_TEST_DB_BRAIN", "d3_subagent_test_brain")
LIVE_BRAIN_DB = "brain"

HOOK = os.path.join(LANE, "bin", "claude-session-hook")
SESSION = "0329aaaa-0000-4000-8000-00000000beef"
PROJECT_DIR = "-mnt-c-Users-you-repos-internal-infinity-os"
AGENTS = ["a0329000000000001", "a0329000000000002"]
WF_AGENT = "a0329000000000003"
WORKFLOW = "wf_0329test"
LATE_AGENT = "a0329000000000004"

FAILURES: list[str] = []
CHECKS = 0


def check(label: str, got, want) -> None:
    global CHECKS
    CHECKS += 1
    if got == want:
        print(f"  ok   {label} = {got!r}")
    else:
        FAILURES.append(f"{label}: got {got!r}, want {want!r}")
        print(f"  FAIL {label}: got {got!r}, want {want!r}")


def admin_sql(sql: str) -> None:
    conn = psycopg2.connect(config.dsn("postgres"))
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.close()


def apply_scratch_schema(dbname: str) -> None:
    import glob
    conn = psycopg2.connect(config.dsn(dbname))
    conn.autocommit = True
    for path in sorted(glob.glob(os.path.join(LANE, "schema", "*.sql"))):
        with conn.cursor() as cur:
            cur.execute(open(path).read())
    conn.close()


def copy_brain_schema(dbname: str) -> None:
    """Schema-only clone of the live store, the same way test_coverage_filesystem.py does it."""
    pw = config._password_from_container()
    env = f"-e PGPASSWORD={pw}"
    cmd = (f"docker exec -i {env} {config.PG_CONTAINER} "
           f"pg_dump -U {config.PG_USER} -d {LIVE_BRAIN_DB} -Fc --schema-only | "
           f"docker exec -i {env} {config.PG_CONTAINER} "
           f"pg_restore -U {config.PG_USER} -d {dbname} --no-owner --no-privileges")
    subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=180)
    conn = psycopg2.connect(config.dsn(dbname))
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM information_schema.tables "
                    "WHERE table_schema='brain' AND table_type='BASE TABLE'")
        n = cur.fetchone()[0]
    conn.close()
    if n == 0:
        raise SystemExit(f"copy of {LIVE_BRAIN_DB} into {dbname} produced no tables")


# --- fixtures: real files, scanned by the real scanner -----------------------------------

def _write(path: str, records: list[dict]) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")
    return os.path.abspath(path)


def main_transcript(root: str) -> str:
    return _write(os.path.join(root, PROJECT_DIR, f"{SESSION}.jsonl"), [
        {"type": "user", "sessionId": SESSION, "cwd": "/mnt/c/Users/you/repos",
         "version": "2.1.233", "timestamp": "2026-08-17T18:00:00.000Z",
         "message": {"role": "user", "content": "task 0329 subagent-indexing fixture"}},
        {"type": "assistant", "sessionId": SESSION, "cwd": "/mnt/c/Users/you/repos",
         "timestamp": "2026-08-17T18:00:05.000Z",
         "message": {"role": "assistant", "content": [{"type": "text", "text": "ok"}]}},
    ])


def subagent_transcript(root: str, agent_id: str, workflow: str | None = None) -> str:
    """A subagent file with the field shape the corpus actually has: `sessionId` is the
    PARENT's uuid and `agentId` is the subagent's own id, confirmed over 733 files."""
    parts = [root, PROJECT_DIR, SESSION, "subagents"]
    if workflow:
        parts += ["workflows", workflow]
    parts.append(f"agent-{agent_id}.jsonl")
    return _write(os.path.join(*parts), [
        {"type": "user", "sessionId": SESSION, "agentId": agent_id, "isSidechain": True,
         "cwd": "/mnt/c/Users/you/repos", "version": "2.1.233",
         "timestamp": "2026-08-17T18:00:10.000Z",
         "message": {"role": "user", "content": f"subagent {agent_id} brief"}},
        {"type": "assistant", "sessionId": SESSION, "agentId": agent_id,
         "timestamp": "2026-08-17T18:00:20.000Z",
         "message": {"role": "assistant", "model": "claude-opus-5",
                     "content": [{"type": "text", "text": "done"}],
                     "usage": {"input_tokens": 10, "output_tokens": 3}}},
    ])


def workflow_journal(root: str, workflow: str) -> str:
    return _write(os.path.join(root, PROJECT_DIR, SESSION, "subagents", "workflows",
                               workflow, "journal.jsonl"),
                  [{"type": "workflow", "sessionId": SESSION,
                    "timestamp": "2026-08-17T18:00:30.000Z"}])


# --- store reads used by the checks -------------------------------------------------------

def pointer_rows() -> dict[str, dict]:
    ptr, host = ("pointer", "pointer_host") if profiles.is_brain() else ("path", "host_id")
    key = "session_id" if profiles.is_brain() else "session_key"
    with store.read() as cur:
        cur.execute(f"SELECT {ptr} AS p, {key} AS k, verified_at FROM transcript "
                    f"WHERE {host} = %s", (config.host_id(),))
        return {r["p"]: r for r in cur.fetchall()}


def session_ids() -> set[str]:
    col = "id" if profiles.is_brain() else "session_key"
    with store.read() as cur:
        cur.execute(f"SELECT {col} AS k FROM session")
        return {r["k"] for r in cur.fetchall()}


def mark_all_verified() -> int:
    """Stand in for a `transcript verify` pass having run. It is the state the upsert destroys."""
    with store.transaction(drain_after_commit=False) as cur:
        cur.execute("UPDATE transcript SET verified_at = now() RETURNING 1")
        return len(cur.fetchall())


def run_hook(event: str, root: str, log_path: str, dbname: str, profile: str) -> str:
    payload = {"hook_event_name": event, "session_id": SESSION,
               "cwd": "/mnt/c/Users/you/repos",
               "transcript_path": os.path.join(root, PROJECT_DIR, f"{SESSION}.jsonl")}
    if event == "SessionEnd":
        payload["reason"] = "test-0329"
    env = dict(os.environ, BRAIN_PROFILE=profile, BRAIN_DB=dbname,
               CLAUDE_PROJECTS_ROOT=root, BRAIN_HOOK_LOG=log_path)
    res = subprocess.run([sys.executable, HOOK], input=json.dumps(payload),
                         capture_output=True, text=True, env=env, timeout=120)
    if res.returncode != 0:
        raise SystemExit(f"hook exited {res.returncode}: {res.stderr}")
    return open(log_path).read()


def run_profile(profile: str, dbname: str, root: str) -> None:
    print(f"\n=== profile {profile} (database {dbname}) ===")
    os.environ["BRAIN_PROFILE"] = profile
    os.environ["BRAIN_DB"] = dbname
    os.environ["CLAUDE_PROJECTS_ROOT"] = root
    check(f"{profile}: profile in force", profiles.profile(), profile)

    main_file = main_transcript(root)
    subs = [subagent_transcript(root, a) for a in AGENTS]
    subs.append(subagent_transcript(root, WF_AGENT, workflow=WORKFLOW))
    journal = workflow_journal(root, WORKFLOW)

    # --- 1. the hook, end to end, exactly as the harness invokes it ------------------------
    log_path = os.path.join(root, "hook.log")
    run_hook("SessionStart", root, log_path, dbname, profile)
    log = run_hook("SessionEnd", root, log_path, dbname, profile)
    print("  hook log:")
    for line in log.strip().splitlines():
        print(f"    {line}")

    rows = pointer_rows()
    check(f"{profile}: hook indexed the main transcript", main_file in rows, True)
    check(f"{profile}: hook indexed every subagent transcript",
          sorted(p for p in rows if "/subagents/" in p and "journal" not in p), sorted(subs))
    check(f"{profile}: hook indexed the workflow journal too", journal in rows, True)
    check(f"{profile}: the journal stays keyless, as it is not a session",
          rows[journal]["k"], None)
    for path, agent in zip(subs, AGENTS + [WF_AGENT]):
        check(f"{profile}: {os.path.basename(path)} keyed parent:agent",
              rows[path]["k"], f"{SESSION}:{agent}")
    check(f"{profile}: every subagent session row exists, so no key is orphaned",
          {f"{SESSION}:{a}" for a in AGENTS + [WF_AGENT]} <= session_ids(), True)
    check(f"{profile}: the hook said so in its log",
          "subagents on_disk=4 indexed=4 already=0 deferred=0 failed=0" in log, True)

    # --- 2. a second pass must not touch a row that already exists -------------------------
    # 5 rows: the main transcript, three subagents, the workflow journal.
    verified = mark_all_verified()
    check(f"{profile}: a verify pass is standing on every row", verified, 5)

    late = subagent_transcript(root, LATE_AGENT)
    # A file that goes away between the walk and the scan. Not a contrivance: `tx.walk` lists
    # the corpus and the hook scans it a moment later, and the harness's retention sweep runs
    # on its own schedule.
    dangling = os.path.join(root, PROJECT_DIR, SESSION, "subagents", "agent-a0329dead.jsonl")
    os.symlink(os.path.join(root, "no-such-file.jsonl"), dangling)

    res = backfill.index_missing_subagents(SESSION, transcript_path=main_file,
                                           projects_root=root)
    print("  sweep: " + json.dumps({k: v for k, v in res.items() if k != "subagent_dir"},
                                   default=str))
    check(f"{profile}: the sweep saw everything on disk", res["on_disk"], 6)
    check(f"{profile}: it skipped what already had a row", res["already_indexed"], 4)
    check(f"{profile}: it indexed only the new file", res["indexed"], 1)
    check(f"{profile}: the vanished file failed alone", res["failed"], 1)

    rows = pointer_rows()
    check(f"{profile}: the new subagent is in the store", late in rows, True)
    check(f"{profile}: THE CLAIM -- verification state survived the sweep",
          sorted(os.path.basename(p) for p, r in rows.items() if r["verified_at"] is None),
          [f"agent-{LATE_AGENT}.jsonl"])

    # --- 3. the budget is a bound, not a suggestion ----------------------------------------
    os.remove(dangling)
    yet_another = subagent_transcript(root, "a0329000000000005")
    starved = backfill.index_missing_subagents(SESSION, transcript_path=main_file,
                                               projects_root=root, budget_s=-1)
    check(f"{profile}: a sweep with no budget indexes nothing", starved["indexed"], 0)
    check(f"{profile}: and reports what it deferred", starved["deferred"], 1)
    check(f"{profile}: which is still missing afterwards", yet_another in pointer_rows(), False)

    # --- 4. `backfill --only-missing` is the catch-up, and it is safe ----------------------
    caught = backfill.run(root, progress_every=0, only_missing=True)
    check(f"{profile}: the deferred file is picked up",
          caught["counts"].get("transcripts_indexed"), 1)
    check(f"{profile}: and nothing else is touched",
          caught["counts"].get("skipped_already_indexed"), 6)
    rows = pointer_rows()
    check(f"{profile}: still exactly the two new rows unverified",
          len([1 for r in rows.values() if r["verified_at"] is None]), 2)

    # --- 5. the contrast, measured: the unconditional pass wipes the verification state ----
    before = len([1 for r in pointer_rows().values() if r["verified_at"] is not None])
    backfill.run(root, progress_every=0)
    after = len([1 for r in pointer_rows().values() if r["verified_at"] is not None])
    check(f"{profile}: plain backfill had verified rows to lose", before, 5)
    check(f"{profile}: and nulled every one of them (task 0318's hazard, why the flag exists)",
          after, 0)


def main() -> int:
    saved = os.environ.pop("BRAIN_PROFILE", None)
    print("=== the default profile ===")
    check("BRAIN_PROFILE unset means brain", profiles.profile(), "brain")
    if saved:
        os.environ["BRAIN_PROFILE"] = saved

    for db in (SCRATCH_TEST_DB, BRAIN_TEST_DB):
        admin_sql(f'DROP DATABASE IF EXISTS "{db}" WITH (FORCE)')
        admin_sql(f'CREATE DATABASE "{db}"')
    apply_scratch_schema(SCRATCH_TEST_DB)
    copy_brain_schema(BRAIN_TEST_DB)

    try:
        # A root per profile: the files ARE the input here, so a shared root would let the
        # first profile's fixtures count against the second profile's store.
        with tempfile.TemporaryDirectory(prefix="t4-0329-scratch-") as root_s:
            run_profile("scratch", SCRATCH_TEST_DB, root_s)
        with tempfile.TemporaryDirectory(prefix="t4-0329-brain-") as root_b:
            run_profile("brain", BRAIN_TEST_DB, root_b)
    finally:
        for var in ("BRAIN_PROFILE", "BRAIN_DB", "CLAUDE_PROJECTS_ROOT"):
            os.environ.pop(var, None)
        for db in (SCRATCH_TEST_DB, BRAIN_TEST_DB):
            admin_sql(f'DROP DATABASE IF EXISTS "{db}" WITH (FORCE)')
        print(f"\ndropped {SCRATCH_TEST_DB}, {BRAIN_TEST_DB}")

    print(f"\n{CHECKS - len(FAILURES)}/{CHECKS} checks passed")
    if FAILURES:
        print("FAILED:")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
