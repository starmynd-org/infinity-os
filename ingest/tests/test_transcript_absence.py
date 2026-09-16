#!/usr/bin/env python3
"""Task 0324: a transcript file that is gone must be recorded, classified, and not called a
failed verification.

Run it:  python3 ingest/tests/test_transcript_absence.py

Task 0323 made a dead pointer countable -- `filesystem.indexed_not_on_disk` said 61 on the live
store on 2026-08-17. This is about what that 61 MEANS. Measured the same day: all 61 belonged
to sessions started 2026-07-15..07-17 and to no later day, and the oldest file left on disk was
dated 2026-07-19. A hard date cut, 30 days back. That is the harness's retention sweep, i.e.
those files reaching their designed end of life -- so a report that files them next to a hash
mismatch is telling the operator to go and look at 61 non-events, and a report that hides them
is telling them nothing. Neither survives contact with a sweep that runs again tomorrow.

What this pins, on BOTH profiles:

    the row is never deleted        the file goes; the `transcript` row, its pointer, its
                                    sha256 and its byte count all stay. They are the only
                                    surviving record that the transcript existed
    old  -> aged-out                a session past the retention horizon whose file is gone
                                    classifies as the file's end of life
    new  -> unexplained             a session INSIDE the horizon whose file is gone is the
                                    incident shape, and is the only thing here anybody acts on
    keyless -> its parent's age     a pointer with no session of its own is aged against the
                                    session whose DIRECTORY holds it, and says so in
                                    `age_basis` (task 0359). A workflow journal is keyless by
                                    design, so without this it re-reported `unknown-age` every
                                    pass forever and held the daily unit red forever
    no timestamp -> unknown-age     a pointer that cannot be placed on either side of the
                                    horizon is its own answer, never defaulted to `aged-out`.
                                    Witnessed by a journal whose containing session was never
                                    registered: nothing to age against, on either route
    missing is not a failure        on the brain profile a gone file gets verified_ok NULL, not
                                    false, so coverage separates it from `mismatch`, which
                                    still gets false. THIS is the claim D1's boolean could not
                                    carry and D3 solved without touching D1's schema
    a mismatch is still a failure   the corruption signal did not get quieter to make room.
                                    Its witness here is a file with a byte REWRITTEN in place,
                                    not one that grew: task 0330 measured that growth is not
                                    corruption and gave it its own verdict, so an appended file
                                    would no longer witness this claim. See
                                    test_transcript_append.py
    twice is still one row          re-verifying bumps times_observed and does NOT move
                                    first_observed_absent_at, so the loss keeps its date
    a file that comes back closes   returned_at is set and the pointer leaves `open`
    the exit code follows meaning   `transcript verify` exits 0 for aged-out and 1 for
                                    unexplained. A verb that went red every day for a sweep
                                    running on schedule is a verb whose exit code stops
                                    being read

It builds its own databases and drops them, and each profile gets its own temporary projects
root. It never writes to `brain` or `d3_scratch`: those hold the live registry, which is
evidence in open questions. The brain-shaped database is a schema-only pg_dump copy of the live
one, so the test measures D1's schema as applied rather than an imitation of it.
"""

from __future__ import annotations

import datetime as dt
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
from ingest.queries import _absence_counts, coverage_report, filesystem_report  # noqa: E402

SCRATCH_TEST_DB = os.environ.get("BRAIN_ABSENCE_TEST_DB_SCRATCH", "d3_absence_test_scratch")
BRAIN_TEST_DB = os.environ.get("BRAIN_ABSENCE_TEST_DB_BRAIN", "d3_absence_test_brain")
LIVE_BRAIN_DB = "brain"

RETENTION_DAYS = 30

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
    """Schema-only clone of the live store, the same way test_coverage_filesystem.py does it.

    D3's own tables live in the `ingest` schema on this profile too, and the live `brain`
    database already has them, so the dump carries `transcript_absence` along with D1's
    migration 1. If it did not, `apply_scratch_schema` below would put it there.
    """
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
    apply_scratch_schema(dbname)   # idempotent; guarantees D3's own `ingest` tables are there


def fixture_transcript(root: str, session_key: str, when: dt.datetime) -> str:
    """A minimal but real main-session transcript, stamped at `when`.

    The timestamps are the whole point: `transcript_index` reads them into the session's
    `started_at`, which is what the classification is later computed against. Nothing about the
    age is injected into the store by hand.
    """
    project_dir = os.path.join(root, "-mnt-c-Users-you-repos-internal-infinity-os")
    os.makedirs(project_dir, exist_ok=True)
    path = os.path.join(project_dir, f"{session_key}.jsonl")
    stamp = when.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    records = [
        {"type": "user", "sessionId": session_key, "cwd": "/mnt/c/Users/you/repos",
         "version": "2.1.233", "timestamp": stamp,
         "message": {"role": "user", "content": "task 0324 absence fixture"}},
        {"type": "assistant", "sessionId": session_key, "cwd": "/mnt/c/Users/you/repos",
         "timestamp": stamp,
         "message": {"role": "assistant", "content": [{"type": "text", "text": "ok"}]}},
    ]
    with open(path, "w") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")
    return os.path.abspath(path)


def fixture_workflow_journal(root: str, session_key: str, wf_id: str) -> str:
    """A workflow journal: indexed as a KEYLESS pointer on purpose (`transcript_index`).

    It is here because the live store has exactly this shape among its dead pointers -- one of
    the 61 on 2026-08-17 was a journal with a NULL session key. `session_key` here is not the
    journal's key (it has none, and never acquires one); it is the SESSION DIRECTORY the journal
    is written under, which is what task 0359 ages it against.

    Called twice, and the pair is the whole point:

      under a session that IS registered    the parent's timestamp dates it, so a journal swept
                                            with its session classifies with its session
      under one that is NOT                 nothing to age against by either route, so it stays
                                            `unknown-age` and still exits the verb 1

    Before 0359 the first case was also `unknown-age`, which is defensible per-row and fatal
    per-day: it could never clear, so the scheduled sweep was permanently `failed` and its exit
    code was on its way to meaning nothing.
    """
    d = os.path.join(root, "-mnt-c-Users-you-repos-internal-infinity-os",
                     session_key, "subagents", "workflows", wf_id)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "journal.jsonl")
    with open(path, "w") as fh:
        fh.write(json.dumps({"type": "workflow", "event": "agent_result", "agent": 1}) + "\n")
    return os.path.abspath(path)


def absence_row(cur, pointer: str) -> dict | None:
    cur.execute("SELECT * FROM transcript_absence WHERE pointer = %s", (pointer,))
    r = cur.fetchone()
    return dict(r) if r else None


def transcript_row(cur, pointer: str) -> dict | None:
    col = "pointer" if profiles.is_brain() else "path"
    cur.execute(f"SELECT * FROM transcript WHERE {col} = %s", (pointer,))
    r = cur.fetchone()
    return dict(r) if r else None


def run_profile(profile: str, dbname: str, root: str) -> None:
    print(f"\n=== profile {profile} (database {dbname}) ===")
    os.environ["BRAIN_PROFILE"] = profile
    os.environ["BRAIN_DB"] = dbname
    os.environ["BRAIN_TRANSCRIPT_RETENTION_DAYS"] = str(RETENTION_DAYS)
    check(f"{profile}: profile in force", profiles.profile(), profile)

    now = dt.datetime.now(dt.timezone.utc)
    old_file = fixture_transcript(root, "0324-old-and-swept", now - dt.timedelta(days=90))
    new_file = fixture_transcript(root, "0324-new-and-gone", now - dt.timedelta(days=1))
    kept_file = fixture_transcript(root, "0324-still-here", now - dt.timedelta(days=2))
    changed_file = fixture_transcript(root, "0324-changed-under-us", now - dt.timedelta(days=3))
    journal = fixture_workflow_journal(root, "0324-old-and-swept", "wf_0324abcdef")
    # Same shape, under a session directory nothing ever registered. This is the one that keeps
    # `unknown-age` reachable after 0359: the path names a session, the store has no such row,
    # and the honest answer is still that the loss cannot be dated.
    undatable = fixture_workflow_journal(root, "0359-no-session-row", "wf_0359abcdef")

    with store.transaction() as cur:
        for p in (old_file, new_file, kept_file, changed_file, journal, undatable):
            verbs.transcript_index(p, projects_root=root, register_missing_session=True, cur=cur)

    # The three deletions and the one rewrite. Nothing here touches the store: this is the disk
    # changing under a store that has already recorded what was there, which is the situation
    # the whole feature exists for.
    for p in (old_file, new_file, journal, undatable):
        os.remove(p)
    # A byte REWRITTEN in place, same length, so this stays a `mismatch` under task 0330's
    # prefix test and keeps witnessing the claim this file names: the corruption signal did not
    # get quieter to make room for `missing`. It used to be an append, which 0330 measured is
    # not corruption at all -- growth has its own verdict and its own test now
    # (test_transcript_append.py). Nothing else in this file moves: same five pointers, same
    # counts, a stronger witness.
    with open(changed_file, "r+b") as fh:
        fh.seek(len(json.dumps({"type": "user"})))
        was = fh.read(1)
        fh.seek(-1, os.SEEK_CUR)
        fh.write(b"X" if was != b"X" else b"Y")

    res = verbs.transcript_verify(limit=50)
    print("  verify: " + json.dumps({k: v for k, v in res.items() if k != "details"}, default=str))

    # --- the verdicts themselves ---
    check(f"{profile}: six pointers checked", res["checked"], 6)
    check(f"{profile}: four files gone", res["missing"], 4)
    check(f"{profile}: the rewritten file is a mismatch", res["mismatch"], 1)
    check(f"{profile}: and nothing here is an append", res["appended"], 0)
    check(f"{profile}: the untouched file matches", res["match"], 1)

    # --- THE CLAIM: the count is split by what it means ---
    check(f"{profile}: aged-out counted", res["missing_by_classification"]["aged-out"], 2)
    check(f"{profile}: unexplained counted", res["missing_by_classification"]["unexplained"], 1)
    check(f"{profile}: unknown-age counted", res["missing_by_classification"]["unknown-age"], 1)

    with store.read() as cur:
        old_abs = absence_row(cur, old_file)
        new_abs = absence_row(cur, new_file)
        jnl_abs = absence_row(cur, journal)
        und_abs = absence_row(cur, undatable)
        kept_abs = absence_row(cur, kept_file)
        changed_abs = absence_row(cur, changed_file)
        old_tx = transcript_row(cur, old_file)

    check(f"{profile}: the 90-day-old file aged out", old_abs["classification"], "aged-out")
    check(f"{profile}: and says what it was aged against", old_abs["age_basis"],
          "session-last-activity-at" if profile == "scratch" else "session-started-at")
    check(f"{profile}: and pins the horizon it used", old_abs["retention_days"], RETENTION_DAYS)
    check(f"{profile}: the 1-day-old file is unexplained", new_abs["classification"],
          "unexplained")
    # Task 0359. The journal never acquires a key, so this is the parent route or nothing.
    check(f"{profile}: the keyless journal ages with the session that owns its directory",
          jnl_abs["classification"], "aged-out")
    check(f"{profile}: and says the timestamp was the PARENT's, not its own",
          jnl_abs["age_basis"],
          "parent-session-last-activity-at" if profile == "scratch"
          else "parent-session-started-at")
    check(f"{profile}: and it is the same timestamp its session was aged by",
          jnl_abs["aged_from"], old_abs["aged_from"])
    check(f"{profile}: while its session_key stays NULL -- it did not acquire one",
          jnl_abs["session_key"], None)
    check(f"{profile}: a journal whose containing session has no row is still unknown-age",
          und_abs["classification"], "unknown-age")
    check(f"{profile}: and still says it had nothing to age against",
          und_abs["age_basis"], "none")
    check(f"{profile}: a file that is present books no absence", kept_abs, None)
    check(f"{profile}: a file that merely changed books no absence", changed_abs, None)

    # --- the evidence survives the file ---
    check(f"{profile}: the transcript row is NOT deleted", old_tx is not None, True)
    check(f"{profile}: the absence carries the hash the file had",
          old_abs["sha256"], old_tx["sha256"])
    check(f"{profile}: and the byte count", old_abs["bytes"], old_tx["bytes"])
    check(f"{profile}: and the session it belonged to", old_abs["session_key"],
          "0324-old-and-swept")

    # --- missing is not a failed verification, mismatch still is ---
    if profile == "brain":
        check("brain: a gone file gets verified_ok NULL, not false", old_tx["verified_ok"], None)
        check("brain: and is still marked as CHECKED", old_tx["verified_at"] is not None, True)
        with store.read() as cur:
            changed_tx = transcript_row(cur, changed_file)
        check("brain: a mismatch is still false", changed_tx["verified_ok"], False)
    else:
        check("scratch: verify_result records the verdict verbatim",
              transcript_row_verdict(old_file), "missing")

    # --- coverage separates the two, and the two disk-vs-store numbers agree ---
    with store.read() as cur:
        cov = coverage_report(cur, projects_root=root)
        counts = _absence_counts(cur)
    print("  pointers_absent: " + json.dumps(counts, default=str))
    check(f"{profile}: coverage reports pointers_absent", "pointers_absent" in cov, True)
    check(f"{profile}: four open absences", counts["open"], 4)
    check(f"{profile}: split by classification", counts["open_by_classification"],
          {"aged-out": 2, "unexplained": 1, "unknown-age": 1})
    check(f"{profile}: the disk agrees on how many are gone",
          cov["filesystem"]["indexed_not_on_disk"]["n"], 4)
    check(f"{profile}: and all four have been walked up to",
          cov["filesystem"]["indexed_not_on_disk"]["recorded_absent"], 4)
    check(f"{profile}: nothing left unrecorded",
          cov["filesystem"]["indexed_not_on_disk"]["not_yet_recorded"], 0)
    if profile == "brain":
        vs = cov["verify_state"]
        check("brain: the gone files are their own coverage line",
              vs.get("checked; file gone (see pointers_absent)"), 4)
        check("brain: the mismatch is NOT in with them",
              vs.get("not-ok (mismatch|unreadable)"), 1)

    # --- twice is still one row, and the date of death does not move ---
    verbs.transcript_verify(limit=50)
    with store.read() as cur:
        again = absence_row(cur, old_file)
        counts2 = _absence_counts(cur)
    check(f"{profile}: a second pass does not duplicate the row", counts2["open"], 4)
    check(f"{profile}: it counts the observation", again["times_observed"], 2)
    check(f"{profile}: and never moves the first observation",
          again["first_observed_absent_at"], old_abs["first_observed_absent_at"])

    # --- a file that comes back closes its absence ---
    restored = fixture_transcript(root, "0324-new-and-gone", now - dt.timedelta(days=1))
    check(f"{profile}: the restore landed where the pointer says", restored, new_file)
    res3 = verbs.transcript_verify(limit=50)
    with store.read() as cur:
        back = absence_row(cur, new_file)
        counts3 = _absence_counts(cur)
    check(f"{profile}: verify reports the close", res3["absences_closed"], 1)
    check(f"{profile}: returned_at is set", back["returned_at"] is not None, True)
    check(f"{profile}: and it leaves the open count", counts3["open"], 3)
    check(f"{profile}: while the record that it was ever gone survives", counts3["ever"], 4)
    check(f"{profile}: no unexplained loss is open any more",
          counts3["open_by_classification"]["unexplained"], 0)

    # --- the exit code follows the meaning, not the raw count ---
    env = dict(os.environ, BRAIN_PROFILE=profile, BRAIN_DB=dbname,
               BRAIN_TRANSCRIPT_RETENTION_DAYS=str(RETENTION_DAYS))
    aged_only = subprocess.run([os.path.join(LANE, "bin", "ingest"), "transcript", "verify",
                                "--path", old_file], capture_output=True, text=True, env=env)
    check(f"{profile}: an aged-out sweep exits 0", aged_only.returncode, 0)
    aged_journal = subprocess.run([os.path.join(LANE, "bin", "ingest"), "transcript", "verify",
                                   "--path", journal], capture_output=True, text=True, env=env)
    check(f"{profile}: a journal aged by its parent exits 0 too", aged_journal.returncode, 0)
    unknown = subprocess.run([os.path.join(LANE, "bin", "ingest"), "transcript", "verify",
                              "--path", undatable], capture_output=True, text=True, env=env)
    check(f"{profile}: a loss nobody can date still exits 1", unknown.returncode, 1)


def transcript_row_verdict(pointer: str) -> str | None:
    with store.read() as cur:
        cur.execute("SELECT verify_result FROM transcript WHERE path = %s", (pointer,))
        r = cur.fetchone()
    return r["verify_result"] if r else None


def main() -> int:
    for db in (SCRATCH_TEST_DB, BRAIN_TEST_DB):
        admin_sql(f'DROP DATABASE IF EXISTS "{db}"')
        admin_sql(f'CREATE DATABASE "{db}"')
    apply_scratch_schema(SCRATCH_TEST_DB)
    copy_brain_schema(BRAIN_TEST_DB)

    try:
        with tempfile.TemporaryDirectory(prefix="0324-scratch-") as root:
            run_profile("scratch", SCRATCH_TEST_DB, root)
        with tempfile.TemporaryDirectory(prefix="0324-brain-") as root:
            run_profile("brain", BRAIN_TEST_DB, root)
    finally:
        os.environ.pop("BRAIN_DB", None)
        os.environ.pop("BRAIN_PROFILE", None)
        os.environ.pop("BRAIN_TRANSCRIPT_RETENTION_DAYS", None)
        for db in (SCRATCH_TEST_DB, BRAIN_TEST_DB):
            admin_sql(f'DROP DATABASE IF EXISTS "{db}"')

    print(f"\n{CHECKS - len(FAILURES)}/{CHECKS} checks passed")
    for f in FAILURES:
        print(f"  FAIL {f}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
