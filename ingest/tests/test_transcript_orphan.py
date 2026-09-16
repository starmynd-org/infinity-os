#!/usr/bin/env python3
"""Task 0228: a transcript whose session was never registered must be countable, not silent.

Run it:  python3 ingest/tests/test_transcript_orphan.py

The installed SessionEnd hook indexed a transcript for a session that had never been
registered, hit the foreign key on `transcript`, and the hook swallowed the traceback as it is
designed to. The transcript was then neither indexed nor counted, and nothing anywhere held a
number that said so. This test pins the replacement behaviour on BOTH profiles:

    the FK is real                 a raw INSERT naming an unregistered session still fails,
                                   so the verb is not being credited for a missing constraint
    the verb does not raise        the transcript is indexed with a NULL session key
    the gap is a row               `ingest.transcript_orphan` holds the key that could not be
                                   honoured, and re-indexing bumps `times_seen` instead of
                                   piling up duplicates
    the gap closes                 registering the session and indexing again links the
                                   transcript and stamps `resolved_at`, so `open` falls

It builds its own databases and drops them. It never writes to `brain` or `d3_scratch`: those
hold the live registry, which is evidence in open questions, and a test that mutates evidence
destroys what someone else is still reading. The brain-shaped database is a schema-only copy
of the live one, taken with pg_dump, so the test measures D1's schema as applied rather than a
hand-written imitation of it that could drift from it without either one being wrong.
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
import psycopg2.errors  # noqa: E402

from ingest import config, profiles, store, verbs  # noqa: E402
from ingest.queries import _orphan_counts  # noqa: E402

SCRATCH_TEST_DB = os.environ.get("BRAIN_ORPHAN_TEST_DB_SCRATCH", "d3_orphan_test_scratch")
BRAIN_TEST_DB = os.environ.get("BRAIN_ORPHAN_TEST_DB_BRAIN", "d3_orphan_test_brain")
LIVE_BRAIN_DB = "brain"

SESSION_KEY = "0228-orphan-test-session"

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
    """Schema-only clone of the live store, the same way store/bin/brain-drop-test.sh does it."""
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
        raise SystemExit(f"copy of {LIVE_BRAIN_DB} into {dbname} produced no tables in schema brain")


def fixture_transcript(root: str, session_key: str) -> str:
    """A minimal but real main-session transcript: the scanner reads this, nothing is faked."""
    project_dir = os.path.join(root, "-mnt-c-Users-you-repos-internal-infinity-os")
    os.makedirs(project_dir, exist_ok=True)
    path = os.path.join(project_dir, f"{session_key}.jsonl")
    records = [
        {"type": "user", "sessionId": session_key, "cwd": "/mnt/c/Users/you/repos",
         "version": "2.1.233", "timestamp": "2026-08-16T18:00:00.000Z",
         "message": {"role": "user", "content": "task 0228 orphan fixture"}},
        {"type": "assistant", "sessionId": session_key, "cwd": "/mnt/c/Users/you/repos",
         "timestamp": "2026-08-16T18:00:05.000Z",
         "message": {"role": "assistant", "content": [{"type": "text", "text": "ok"}]}},
    ]
    with open(path, "w") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")
    return path


def session_row_count(cur, key: str) -> int:
    col = "id" if profiles.is_brain() else "session_key"
    cur.execute(f"SELECT count(*) AS n FROM session WHERE {col} = %s", (key,))
    return cur.fetchone()["n"]


def transcript_key(cur, pointer: str):
    if profiles.is_brain():
        cur.execute("SELECT session_id AS k FROM transcript WHERE pointer = %s", (pointer,))
    else:
        cur.execute("SELECT session_key AS k FROM transcript WHERE path = %s", (pointer,))
    row = cur.fetchone()
    return None if row is None else row["k"]


def raw_insert_with_key(cur, pointer: str, key: str) -> None:
    """What the hook used to do. Kept so the test proves the FK is there to be tripped."""
    if profiles.is_brain():
        cur.execute("INSERT INTO transcript (session_id, pointer, pointer_host, sha256, bytes) "
                    "VALUES (%s,%s,%s,%s,%s)",
                    (key, pointer + ".fkprobe", config.host_id(), "0" * 64, 1))
    else:
        cur.execute("INSERT INTO transcript (session_key, kind, host_id, path, path_class, "
                    "bytes, sha256, line_count) VALUES (%s,'main',%s,%s,'wsl-ext4',1,%s,1)",
                    (key, config.host_id(), pointer + ".fkprobe", "0" * 64))


def run_profile(profile: str, dbname: str, root: str) -> None:
    print(f"\n=== profile {profile} (database {dbname}) ===")
    os.environ["BRAIN_PROFILE"] = profile
    os.environ["BRAIN_DB"] = dbname
    check(f"{profile}: profile in force", profiles.profile(), profile)

    path = fixture_transcript(root, SESSION_KEY)

    # 1. the constraint this fix exists to respect is actually present
    with store.transaction() as cur:
        check(f"{profile}: session is not registered", session_row_count(cur, SESSION_KEY), 0)
    tripped = None
    try:
        with store.transaction() as cur:
            raw_insert_with_key(cur, path, SESSION_KEY)
    except psycopg2.errors.ForeignKeyViolation:
        tripped = "ForeignKeyViolation"
    check(f"{profile}: a raw insert naming an unregistered session still fails", tripped,
          "ForeignKeyViolation")

    # 2. the verb indexes it anyway, keyless, and counts the gap
    with store.transaction() as cur:
        r = verbs.transcript_index(path, projects_root=root, session_key=SESSION_KEY, cur=cur)
    check(f"{profile}: index reports the key unresolved", r["session_key_unresolved"], True)
    check(f"{profile}: reason", r["unresolved_reason"], "session-not-registered")
    check(f"{profile}: the key it could not honour is reported back", r["unresolved_key"],
          SESSION_KEY)
    check(f"{profile}: bytes measured", r["bytes"] > 0, True)
    with store.transaction() as cur:
        check(f"{profile}: transcript row exists with a NULL key", transcript_key(cur, path), None)
        counts = _orphan_counts(cur)
        check(f"{profile}: open orphans", counts["open"], 1)
        check(f"{profile}: counted by reason", counts["open_by_reason"],
              {"session-not-registered": 1})
        cur.execute("SELECT session_key FROM transcript_orphan WHERE resolved_at IS NULL")
        check(f"{profile}: the key is preserved in the count", cur.fetchone()["session_key"],
              SESSION_KEY)
        check(f"{profile}: no session was fabricated to satisfy the FK",
              session_row_count(cur, SESSION_KEY), 0)

    # 3. indexing the same file again counts one gap, not two
    with store.transaction() as cur:
        r = verbs.transcript_index(path, projects_root=root, session_key=SESSION_KEY, cur=cur)
        check(f"{profile}: re-index bumps times_seen", r["orphan_times_seen"], 2)
        check(f"{profile}: still one open orphan", _orphan_counts(cur)["open"], 1)

    # 4. register the session, index again: the pointer links and the gap closes
    with store.transaction() as cur:
        verbs.session_register(SESSION_KEY, session_id=SESSION_KEY, kind="main",
                               workdir="/mnt/c/Users/you/repos", actor_type="ai",
                               registration_source="manual", emit_event=False, cur=cur)
        r = verbs.transcript_index(path, projects_root=root, session_key=SESSION_KEY, cur=cur)
        check(f"{profile}: index no longer unresolved", r["session_key_unresolved"], False)
        check(f"{profile}: transcript now carries the key", transcript_key(cur, path), SESSION_KEY)
        counts = _orphan_counts(cur)
        check(f"{profile}: open orphans after the session lands", counts["open"], 0)
        check(f"{profile}: the closed one is still on the record", counts["ever"], 1)


def main() -> int:
    # The default itself is under test: task 0228 flipped it, and a silent flip back would put
    # every hook-registered session in a database nobody reads without failing anything.
    saved = os.environ.pop("BRAIN_PROFILE", None)
    print("=== the default profile ===")
    check("BRAIN_PROFILE unset means brain", profiles.profile(), "brain")
    check("BRAIN_PROFILE unset means the brain database",
          [t for t in config.dsn().split() if t.startswith("dbname=")], ["dbname=brain"])
    check("BRAIN_PROFILE unset means the brain schema", config.schema(), "brain")
    if saved:
        os.environ["BRAIN_PROFILE"] = saved

    for db in (SCRATCH_TEST_DB, BRAIN_TEST_DB):
        admin_sql(f'DROP DATABASE IF EXISTS "{db}" WITH (FORCE)')
        admin_sql(f'CREATE DATABASE "{db}"')
    apply_scratch_schema(SCRATCH_TEST_DB)
    copy_brain_schema(BRAIN_TEST_DB)

    try:
        with tempfile.TemporaryDirectory(prefix="t6-0228-projects-") as root:
            run_profile("scratch", SCRATCH_TEST_DB, root)
            run_profile("brain", BRAIN_TEST_DB, root)
    finally:
        os.environ.pop("BRAIN_PROFILE", None)
        os.environ.pop("BRAIN_DB", None)
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
