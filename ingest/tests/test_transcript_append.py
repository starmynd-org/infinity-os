#!/usr/bin/env python3
"""Task 0330: a transcript that GREW is not a transcript that was TAMPERED WITH, and the file
itself is the evidence for which one it is.

Run it:  python3 ingest/tests/test_transcript_append.py

What this is about. The 2026-08-16T12:20:40Z backfill sweep hashed transcripts whose sessions
were still writing to them. A hash taken against an open append-only log is stale the moment it
is taken, and `transcript index` recorded it as if it were authoritative. Re-measured on the
live stores 2026-08-18 (read-only, `verified_at` untouched): `d3_scratch` held ELEVEN indexed
rows that re-hashed differently, and all eleven were pure growth -- hashing the first `bytes` of
each file on disk reproduced the stored sha256 exactly, and in all eleven the byte at the cut
landed on a newline. Nine were main sessions and two were subagents; three were still growing
that morning. `brain` held none by then, its two having already been re-indexed.

Before this task all eleven would have come back `mismatch`, and on the brain profile `mismatch`
is `verified_ok = false` -- the same bucket as corruption. The first scheduled verify would have
reported eleven files as "the bytes we recorded are no longer the bytes on disk", which is the
shape of tampering, for a log doing the only thing a log does. Run against those eleven before
they were re-indexed, this verdict returned `appended` eleven times out of eleven, exit 0.

What this pins, on BOTH profiles:

    growth is `appended`            a file whose stored hash still reproduces as a PREFIX of
                                    what is on disk verifies as `appended`, never `mismatch`
    a rewrite is still `mismatch`   a byte changed in place, same length, is corruption and
                                    stays corruption
    GROWTH IS NOT AN EXCUSE         a file that grew AND had a recorded byte rewritten is a
                                    `mismatch`. This is the whole test: the verdict turns on
                                    the prefix holding, not on the file having got longer
    a shrink is `mismatch`          bytes this lane recorded are gone; the prefix test cannot
                                    even be run
    append is not a failure         `appended` does not exit 1 and books no absence. `mismatch`
                                    still exits 1
    the two NULL causes stay apart  on the brain profile `appended` and `missing` both write
                                    verified_ok NULL, and coverage still reports them on
                                    separate lines -- split by the open `transcript_absence`
                                    row that only `missing` books
    scratch keeps the word          `verify_result` holds 'appended' verbatim, and
                                    `verify_sha256` carries what the file hashes to now
    re-indexing is the remedy       `transcript index` on an appended file makes it `match`
                                    again, and that is the only thing that does

It builds its own databases and drops them, and each profile gets its own temporary projects
root. It never writes to `brain` or `d3_scratch`: those hold the live registry, which is
evidence in open questions. The brain-shaped database is a schema-only pg_dump copy of the live
one, so the test measures D1's schema as applied rather than an imitation of it.
"""

from __future__ import annotations

import datetime as dt
import hashlib
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

from ingest import config, profiles, store, verbs  # noqa: E402
from ingest.queries import coverage_report  # noqa: E402

SCRATCH_TEST_DB = os.environ.get("BRAIN_APPEND_TEST_DB_SCRATCH", "d3_append_test_scratch")
BRAIN_TEST_DB = os.environ.get("BRAIN_APPEND_TEST_DB_BRAIN", "d3_append_test_brain")
LIVE_BRAIN_DB = "brain"

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
    """Schema-only clone of the live store, the same way test_transcript_absence.py does it."""
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
    apply_scratch_schema(dbname)


def fixture_transcript(root: str, session_key: str) -> str:
    """A minimal but real main-session transcript. Same shape as the 0324 fixture."""
    project_dir = os.path.join(root, "-mnt-c-Users-you-repos-internal-infinity-os")
    os.makedirs(project_dir, exist_ok=True)
    path = os.path.join(project_dir, f"{session_key}.jsonl")
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    records = [
        {"type": "user", "sessionId": session_key, "cwd": "/mnt/c/Users/you/repos",
         "version": "2.1.233", "timestamp": stamp,
         "message": {"role": "user", "content": "task 0330 append fixture"}},
        {"type": "assistant", "sessionId": session_key, "cwd": "/mnt/c/Users/you/repos",
         "timestamp": stamp,
         "message": {"role": "assistant", "content": [{"type": "text", "text": "ok"}]}},
    ]
    with open(path, "w") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")
    return os.path.abspath(path)


def append_a_turn(path: str) -> None:
    """What a live session does to its own transcript, and the reason this task exists."""
    with open(path, "a") as fh:
        fh.write(json.dumps({"type": "user",
                             "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
                             "message": {"role": "user", "content": "one more turn"}}) + "\n")


def rewrite_a_byte(path: str, offset: int = 20) -> None:
    """What corruption does: a byte inside the recorded region changes, length unchanged."""
    with open(path, "r+b") as fh:
        fh.seek(offset)
        was = fh.read(1)
        fh.seek(offset)
        fh.write(b"X" if was != b"X" else b"Y")


def sha_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def transcript_row(cur, pointer: str) -> dict | None:
    col = "pointer" if profiles.is_brain() else "path"
    cur.execute(f"SELECT * FROM transcript WHERE {col} = %s", (pointer,))
    r = cur.fetchone()
    return dict(r) if r else None


def absence_row(cur, pointer: str) -> dict | None:
    cur.execute("SELECT * FROM transcript_absence WHERE pointer = %s", (pointer,))
    r = cur.fetchone()
    return dict(r) if r else None


def detail_for(res: dict, pointer: str) -> dict:
    return next(d for d in res["details"] if d["path"] == pointer)


def run_profile(profile: str, dbname: str, root: str) -> None:
    print(f"\n=== profile {profile} (database {dbname}) ===")
    os.environ["BRAIN_PROFILE"] = profile
    os.environ["BRAIN_DB"] = dbname
    check(f"{profile}: profile in force", profiles.profile(), profile)

    untouched = fixture_transcript(root, "0330-untouched")
    grown = fixture_transcript(root, "0330-grew-under-us")
    rewritten = fixture_transcript(root, "0330-rewritten-in-place")
    grown_and_rewritten = fixture_transcript(root, "0330-grew-and-was-rewritten")
    shrunk = fixture_transcript(root, "0330-truncated")
    gone = fixture_transcript(root, "0330-deleted")

    with store.transaction() as cur:
        for p in (untouched, grown, rewritten, grown_and_rewritten, shrunk, gone):
            verbs.transcript_index(p, projects_root=root, register_missing_session=True, cur=cur)

    with store.read() as cur:
        stored_bytes = transcript_row(cur, grown)["bytes"]

    # The disk changes under a store that has already recorded what was there. Nothing below
    # touches the store -- that is the whole situation this verdict exists for.
    append_a_turn(grown)
    rewrite_a_byte(rewritten)
    # The adversarial one: it grew AND a recorded byte moved. Growth must not launder it.
    rewrite_a_byte(grown_and_rewritten)
    append_a_turn(grown_and_rewritten)
    with open(shrunk, "r+b") as fh:
        fh.truncate(os.path.getsize(shrunk) // 2)
    os.remove(gone)

    grown_sha_now = sha_of(grown)
    res = verbs.transcript_verify(limit=50)
    print("  verify: " + json.dumps({k: v for k, v in res.items() if k != "details"},
                                    default=str))

    # --- THE CLAIM: the verdict turns on the prefix holding, not on the file getting longer ---
    check(f"{profile}: six pointers checked", res["checked"], 6)
    check(f"{profile}: the untouched file matches", res["match"], 1)
    check(f"{profile}: the grown file is APPENDED", res["appended"], 1)
    check(f"{profile}: growth did not launder the rewrite", res["mismatch"], 3)
    check(f"{profile}: the deleted file is missing", res["missing"], 1)

    check(f"{profile}: the grown file, by name", detail_for(res, grown)["verdict"], "appended")
    check(f"{profile}: the rewritten file, by name",
          detail_for(res, rewritten)["verdict"], "mismatch")
    check(f"{profile}: grew AND rewritten is a mismatch",
          detail_for(res, grown_and_rewritten)["verdict"], "mismatch")
    check(f"{profile}: a shrink is a mismatch", detail_for(res, shrunk)["verdict"], "mismatch")

    # --- the append reports how far behind the stored hash is ---
    d = detail_for(res, grown)
    check(f"{profile}: the append names its size",
          d["appended_bytes"], d["actual_bytes"] - d["stored_bytes"])
    check(f"{profile}: and the stored byte count is what was indexed",
          d["stored_bytes"], stored_bytes)
    check(f"{profile}: and the actual hash is the file's hash now",
          d["actual_sha256"], grown_sha_now)
    check(f"{profile}: a mismatch reports no append size",
          "appended_bytes" in detail_for(res, rewritten), False)

    # --- an append is not a loss: it books no absence ---
    with store.read() as cur:
        check(f"{profile}: an appended file books no absence", absence_row(cur, grown), None)
        check(f"{profile}: the deleted one does", absence_row(cur, gone) is not None, True)

    # --- how each profile records it ---
    with store.read() as cur:
        grown_tx = transcript_row(cur, grown)
        rewritten_tx = transcript_row(cur, rewritten)
        untouched_tx = transcript_row(cur, untouched)
        gone_tx = transcript_row(cur, gone)

    if profile == "brain":
        # D1's boolean cannot hold five verdicts. `appended` takes the NULL that task 0324
        # established -- checked, and neither a pass nor a fail -- and `false` stays what it has
        # always been, the corruption signal.
        check("brain: an append is neither pass nor fail", grown_tx["verified_ok"], None)
        check("brain: and is still marked as CHECKED", grown_tx["verified_at"] is not None, True)
        check("brain: a rewrite is still false", rewritten_tx["verified_ok"], False)
        check("brain: an untouched file is still true", untouched_tx["verified_ok"], True)
        check("brain: a gone file is still NULL", gone_tx["verified_ok"], None)
        # ...and the two NULL causes are still told apart, by the absence row only one books.
        with store.read() as cur:
            vs = coverage_report(cur, projects_root=root)["verify_state"]
        print("  verify_state: " + json.dumps(vs, default=str))
        check("brain: the append is its own coverage line",
              vs.get("checked; file grew since indexing (re-index to re-cover)"), 1)
        check("brain: NOT merged in with the gone file",
              vs.get("checked; file gone (see pointers_absent)"), 1)
        check("brain: and NOT in the failure bucket",
              vs.get("not-ok (mismatch|unreadable)"), 3)
    else:
        # `verify_result` is text here and needs nothing collapsed.
        check("scratch: verify_result records the verdict verbatim",
              grown_tx["verify_result"], "appended")
        check("scratch: and the rewrite keeps its own word",
              rewritten_tx["verify_result"], "mismatch")
        check("scratch: verify_sha256 carries what the file hashes to now",
              grown_tx["verify_sha256"], grown_sha_now)
        check("scratch: a match stores no second hash", untouched_tx["verify_sha256"], None)
        with store.read() as cur:
            vs = coverage_report(cur, projects_root=root)["verify_state"]
        print("  verify_state: " + json.dumps(vs, default=str))
        check("scratch: coverage counts the append under its own name", vs.get("appended"), 1)

    # --- the exit code follows the meaning ---
    env = dict(os.environ, BRAIN_PROFILE=profile, BRAIN_DB=dbname)
    ok = subprocess.run([os.path.join(LANE, "bin", "ingest"), "transcript", "verify",
                         "--path", grown], capture_output=True, text=True, env=env)
    check(f"{profile}: an append does NOT make verify red", ok.returncode, 0)
    red = subprocess.run([os.path.join(LANE, "bin", "ingest"), "transcript", "verify",
                          "--path", rewritten], capture_output=True, text=True, env=env)
    check(f"{profile}: a rewrite still does", red.returncode, 1)

    # --- re-indexing is the remedy, and it is the only one ---
    with store.transaction() as cur:
        verbs.transcript_index(grown, projects_root=root, register_missing_session=True, cur=cur)
    res2 = verbs.transcript_verify(path=grown)
    check(f"{profile}: a re-indexed append verifies clean", res2["match"], 1)
    check(f"{profile}: and is no longer an append", res2["appended"], 0)
    with store.read() as cur:
        again = transcript_row(cur, grown)
    check(f"{profile}: the stored hash now covers the whole file",
          again["sha256"], grown_sha_now)
    if profile == "brain":
        check("brain: and the row reads as a pass", again["verified_ok"], True)


def main() -> int:
    for db in (SCRATCH_TEST_DB, BRAIN_TEST_DB):
        admin_sql(f'DROP DATABASE IF EXISTS "{db}"')
        admin_sql(f'CREATE DATABASE "{db}"')
    apply_scratch_schema(SCRATCH_TEST_DB)
    copy_brain_schema(BRAIN_TEST_DB)

    try:
        with tempfile.TemporaryDirectory(prefix="0330-scratch-") as root:
            run_profile("scratch", SCRATCH_TEST_DB, root)
        with tempfile.TemporaryDirectory(prefix="0330-brain-") as root:
            run_profile("brain", BRAIN_TEST_DB, root)
    finally:
        os.environ.pop("BRAIN_DB", None)
        os.environ.pop("BRAIN_PROFILE", None)
        for db in (SCRATCH_TEST_DB, BRAIN_TEST_DB):
            admin_sql(f'DROP DATABASE IF EXISTS "{db}"')

    print(f"\n{CHECKS - len(FAILURES)}/{CHECKS} checks passed")
    for f in FAILURES:
        print(f"  FAIL {f}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
