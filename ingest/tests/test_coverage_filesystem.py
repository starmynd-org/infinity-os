#!/usr/bin/env python3
"""Task 0323: a transcript whose indexing transaction rolled back must be countable.

Run it:  python3 ingest/tests/test_coverage_filesystem.py

Every other count in `ingest/ingest/queries.py` is a SELECT against `session`, `transcript`,
`transcript_orphan` or `event_outbox`, so its denominator is "rows we managed to write". The
six sessions task 0318 fixed rolled their whole transaction back on
`transcript_session_key_fkey`, which left no transcript row, no session row AND no orphan row,
so four of them were absent from the numerator and the denominator of every ratio the report
printed. `transcript_orphans {open: 2}` read like a total; the real gap was 76.

What this pins, on BOTH profiles:

    the disk is the denominator      `files_on_disk` comes from `tx.walk`, and `indexed`
                                     is reported out of it, not out of `transcript`
    the rolled-back file is counted  a real ForeignKeyViolation rolls back an index; the file
                                     is then in `on_disk_not_indexed` while
                                     `transcript_orphans.open` is still 0. This is THE claim:
                                     the two numbers are not the same fact, and widening
                                     `transcript_orphan` could never have found this case
    the dead pointer is counted      a row whose file has been deleted lands in
                                     `indexed_not_on_disk`, the other direction nothing counted
    another host is not a gap        a pointer stored against a different host_id is reported
                                     separately and stays out of both differences
    a wrong root refuses             an absent projects root withholds the counts and says why,
                                     rather than reporting every pointer in the store as dead

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
import psycopg2.errors  # noqa: E402

from ingest import config, profiles, store, verbs  # noqa: E402
from ingest.queries import _orphan_counts, filesystem_report  # noqa: E402

SCRATCH_TEST_DB = os.environ.get("BRAIN_FSCOV_TEST_DB_SCRATCH", "d3_fscov_test_scratch")
BRAIN_TEST_DB = os.environ.get("BRAIN_FSCOV_TEST_DB_BRAIN", "d3_fscov_test_brain")
LIVE_BRAIN_DB = "brain"

OTHER_HOST = "wsl:some-other-machine:deadbeefcafe"
UNREGISTERED = "0323-a-session-nobody-registered"

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
    """Schema-only clone of the live store, the same way test_transcript_orphan.py does it."""
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


def fixture_transcript(root: str, session_key: str) -> str:
    """A minimal but real main-session transcript. The scanner reads this; nothing is faked."""
    project_dir = os.path.join(root, "-mnt-c-Users-you-repos-internal-infinity-os")
    os.makedirs(project_dir, exist_ok=True)
    path = os.path.join(project_dir, f"{session_key}.jsonl")
    records = [
        {"type": "user", "sessionId": session_key, "cwd": "/mnt/c/Users/you/repos",
         "version": "2.1.233", "timestamp": "2026-08-17T14:00:00.000Z",
         "message": {"role": "user", "content": "task 0323 filesystem-denominator fixture"}},
        {"type": "assistant", "sessionId": session_key, "cwd": "/mnt/c/Users/you/repos",
         "timestamp": "2026-08-17T14:00:05.000Z",
         "message": {"role": "assistant", "content": [{"type": "text", "text": "ok"}]}},
    ]
    with open(path, "w") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")
    return os.path.abspath(path)


def trip_the_fk(cur, pointer: str) -> None:
    """The insert the hook used to do: a transcript row naming a session nobody registered.

    This is not a stand-in for the 2026-08-16 failure, it is that failure: the same constraint,
    tripped the same way, inside a transaction that has already indexed something legitimate.
    """
    if profiles.is_brain():
        cur.execute("INSERT INTO transcript (session_id, pointer, pointer_host, sha256, bytes) "
                    "VALUES (%s,%s,%s,%s,%s)",
                    (UNREGISTERED, pointer + ".fkprobe", config.host_id(), "0" * 64, 1))
    else:
        cur.execute("INSERT INTO transcript (session_key, kind, host_id, path, path_class, "
                    "bytes, sha256, line_count) VALUES (%s,'main',%s,%s,'wsl-ext4',1,%s,1)",
                    (UNREGISTERED, config.host_id(), pointer + ".fkprobe", "0" * 64))


def insert_other_host_pointer(cur, pointer: str) -> None:
    """A pointer that is absolute on a machine this one cannot see. Test-only raw insert:
    no verb can write another host's pointer, because `config.host_id()` is this host."""
    if profiles.is_brain():
        cur.execute("INSERT INTO transcript (session_id, pointer, pointer_host, sha256, bytes) "
                    "VALUES (NULL,%s,%s,%s,%s)", (pointer, OTHER_HOST, "1" * 64, 2))
    else:
        cur.execute("INSERT INTO transcript (session_key, kind, host_id, path, path_class, "
                    "bytes, sha256, line_count) VALUES (NULL,'main',%s,%s,'wsl-ext4',2,%s,1)",
                    (OTHER_HOST, pointer, "1" * 64))


def run_profile(profile: str, dbname: str, root: str) -> None:
    print(f"\n=== profile {profile} (database {dbname}) ===")
    os.environ["BRAIN_PROFILE"] = profile
    os.environ["BRAIN_DB"] = dbname
    check(f"{profile}: profile in force", profiles.profile(), profile)

    indexed_file = fixture_transcript(root, "0323-indexed")
    rolled_back_file = fixture_transcript(root, "0323-rolled-back")
    deleted_file = fixture_transcript(root, "0323-then-deleted")

    # 1. one file indexed for real, and one indexed then removed from disk under us.
    #    `register_missing_session=True` is load-bearing: the verb's default is False, and
    #    without it these two files index as keyless pointers and book themselves an orphan row
    #    each, which would make the orphan count below prove nothing.
    with store.transaction() as cur:
        verbs.transcript_index(indexed_file, projects_root=root,
                               register_missing_session=True, cur=cur)
        verbs.transcript_index(deleted_file, projects_root=root,
                               register_missing_session=True, cur=cur)
    os.remove(deleted_file)

    # 2. the group-B shape: a legitimate index in a transaction that then dies on the FK.
    #    Nothing survives -- not the transcript row, not the orphan row.
    tripped = None
    try:
        with store.transaction() as cur:
            verbs.transcript_index(rolled_back_file, projects_root=root,
                                   session_key=UNREGISTERED,
                                   register_missing_session=False, cur=cur)
            trip_the_fk(cur, rolled_back_file)
    except psycopg2.errors.ForeignKeyViolation:
        tripped = "ForeignKeyViolation"
    check(f"{profile}: the index transaction died on the real constraint", tripped,
          "ForeignKeyViolation")

    # 3. a pointer belonging to another host
    with store.transaction() as cur:
        insert_other_host_pointer(cur, "/srv/brain/projects/-srv-x/0323-elsewhere.jsonl")

    with store.read() as cur:
        orphans = _orphan_counts(cur)
        fs = filesystem_report(cur, projects_root=root)

    print("  " + json.dumps({k: v for k, v in fs.items() if k != "projects_root"}, default=str))

    # --- the denominator is the disk ---
    check(f"{profile}: root was found", fs["root_exists"], True)
    check(f"{profile}: files_on_disk counts the walk", fs["files_on_disk"], 2)
    check(f"{profile}: by kind", fs["files_on_disk_by_kind"], {"main": 2})
    check(f"{profile}: indexed is reported out of the disk count", fs["indexed"]["of"],
          fs["files_on_disk"])
    check(f"{profile}: indexed", fs["indexed"]["n"], 1)

    # --- THE CLAIM: the rolled-back file is counted, and the orphan table cannot see it ---
    check(f"{profile}: transcript_orphans.open says nothing about it", orphans["open"], 0)
    check(f"{profile}: transcript_orphans.ever says nothing about it either", orphans["ever"], 0)
    check(f"{profile}: on_disk_not_indexed counts it", fs["on_disk_not_indexed"]["n"], 1)
    check(f"{profile}: and names it", fs["on_disk_not_indexed"]["sample"], [rolled_back_file])
    check(f"{profile}: by kind", fs["on_disk_not_indexed"]["by_kind"], {"main": 1})

    # --- the other direction, task 0324's shape ---
    check(f"{profile}: indexed_not_on_disk counts the deleted file",
          fs["indexed_not_on_disk"]["n"], 1)
    check(f"{profile}: and names it", fs["indexed_not_on_disk"]["sample"], [deleted_file])

    # --- another host is not a gap ---
    check(f"{profile}: other-host pointers counted on their own",
          fs["pointers_on_other_hosts"], 1)
    check(f"{profile}: this host's pointers are the ones compared",
          fs["pointers_indexed_here"], 2)
    check(f"{profile}: an unreachable host's pointer is not called dead",
          fs["indexed_not_on_disk"]["of"], 2)
    # No row may be in the table and in neither bucket, which is the shape of the bug this
    # whole section exists to end.
    check(f"{profile}: every pointer row is in exactly one of the two buckets",
          fs["pointers_indexed_here"] + fs["pointers_on_other_hosts"], fs["pointers_total"])

    # --- a root that is not there refuses instead of guessing ---
    with store.read() as cur:
        gone = filesystem_report(cur, projects_root=os.path.join(root, "no-such-root"))
    check(f"{profile}: absent root reported as absent", gone["root_exists"], False)
    check(f"{profile}: absent root refuses", "refused" in gone, True)
    check(f"{profile}: absent root reports no gap counts",
          [k for k in ("files_on_disk", "on_disk_not_indexed", "indexed_not_on_disk")
           if k in gone], [])
    check(f"{profile}: absent root still says what the store holds",
          gone["pointers_indexed_here"], 2)

    # --- the sample cap is honest about what it hides ---
    import ingest.queries as q
    saved_limit = q.SAMPLE_LIMIT
    q.SAMPLE_LIMIT = 0
    try:
        with store.read() as cur:
            capped = filesystem_report(cur, projects_root=root)
    finally:
        q.SAMPLE_LIMIT = saved_limit
    check(f"{profile}: a capped sample still reports the full count",
          [capped["on_disk_not_indexed"]["sample"], capped["on_disk_not_indexed"]["sample_of"]],
          [[], 1])


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
        # A root per profile: the disk IS the denominator here, so a shared root would let the
        # first profile's files count against the second profile's store.
        with tempfile.TemporaryDirectory(prefix="t3-0323-scratch-") as root_s:
            run_profile("scratch", SCRATCH_TEST_DB, root_s)
        with tempfile.TemporaryDirectory(prefix="t3-0323-brain-") as root_b:
            run_profile("brain", BRAIN_TEST_DB, root_b)
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
