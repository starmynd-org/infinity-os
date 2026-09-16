#!/usr/bin/env python3
"""Task 0136: the hook and backfill disagree, and write order must not decide who wins.

Run it:  python3 ingest/tests/test_writer_precedence.py

Builds its own database (`d3_precedence_test` by default), applies the scratch schema, and
drops it again. It never touches `d3_scratch`: the live registry is evidence in an open
question and a test that mutates it would destroy what someone is still reading.

The property under test is not "the hook wins" but "the answer is the same either way".
Every assertion below is made twice, once per write order, and the expected value is
identical in both. A rule that only holds in the order the author happened to test is the
defect 0136 was filed about, one layer down.
"""

from __future__ import annotations

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LANE = os.path.dirname(HERE)
sys.path.insert(0, LANE)

TEST_DB = os.environ.get("BRAIN_PRECEDENCE_TEST_DB", "d3_precedence_test")
os.environ["BRAIN_DB"] = TEST_DB
# Task 0228 flipped the process default to `brain`. This test applies the SCRATCH schema to a
# database of its own and asserts on columns only that schema has (`state`, `model_source`,
# `stated_goal_tier`), so it names the profile it needs instead of inheriting a default. It
# used to POP this variable, which was the same request spelled as an assumption.
os.environ["BRAIN_PROFILE"] = "scratch"
os.environ.pop("BRAIN_DSN", None)

import psycopg2  # noqa: E402
from ingest import config, store, verbs  # noqa: E402

FAILURES: list[str] = []
CHECKS = 0

# What each writer claims about the same session. Every field here is one the two writers
# genuinely disagree about in production, not an invented conflict.
HOOK = dict(
    workdir="/mnt/c/Users/you/repos/infinity-os",
    workdir_source="hook-arg",
    model="claude-opus-5",
    model_source="hook-payload",
    time_source="hook-observed",
    actor_type="hybrid",
    entrypoint="cli",
    permission_mode="acceptEdits",
    goal="Reply with exactly this and nothing else: d3-hook-live-proof",
    goal_source="hook:first-prompt",
    goal_tier="A:explicit-brief",
)
BACKFILL = dict(
    workdir="/mnt/c/Users/you/repos/infinity-os",
    workdir_source="decoded-dirname",   # the schema says this decode is lossy
    model="claude-sonnet-5",
    model_source="transcript-record",
    time_source="transcript-first-record",
    actor_type="ai",                    # a transcript cannot see the human
    entrypoint="sdk",
    permission_mode="default",
    goal="Reply with exactly this and nothing",
    goal_source="first-user-prompt:first-line",
    goal_tier="B:inferred",
)


def check(name: str, got, want) -> None:
    global CHECKS
    CHECKS += 1
    if got != want:
        FAILURES.append(f"{name}: got {got!r}, want {want!r}")
        print(f"  FAIL {name}: got {got!r}, want {want!r}")
    else:
        print(f"  ok   {name} = {got!r}")


def hook_register(cur, key, *, goal=False, **kw):
    return verbs.session_register(
        key, session_id=key, kind="main", harness="claude-code",
        entrypoint=HOOK["entrypoint"], permission_mode=HOOK["permission_mode"],
        model=HOOK["model"], model_source=HOOK["model_source"],
        workdir=HOOK["workdir"], workdir_source=HOOK["workdir_source"],
        started_at="2026-08-16T12:08:06.365475+00:00", time_source=HOOK["time_source"],
        actor_type=HOOK["actor_type"], registration_source="hook",
        stated_goal=HOOK["goal"] if goal else None,
        stated_goal_source=HOOK["goal_source"] if goal else None,
        stated_goal_tier=HOOK["goal_tier"] if goal else None,
        state="open", emit_event=False, cur=cur, **kw)


def backfill_register(cur, key, **kw):
    return verbs.session_register(
        key, session_id=key, kind="main", harness="claude-code",
        entrypoint=BACKFILL["entrypoint"], permission_mode=BACKFILL["permission_mode"],
        model=BACKFILL["model"], model_source=BACKFILL["model_source"],
        workdir=BACKFILL["workdir"], workdir_source=BACKFILL["workdir_source"],
        started_at="2026-08-16T12:08:06.365475+00:00", time_source=BACKFILL["time_source"],
        actor_type=BACKFILL["actor_type"], registration_source="backfill",
        stated_goal=BACKFILL["goal"], stated_goal_source=BACKFILL["goal_source"],
        stated_goal_tier=BACKFILL["goal_tier"],
        # backfill witnessed nothing, so it registers 'unknown'. This is the value that used
        # to overwrite a live 'open'.
        state="unknown", last_activity_at="2026-08-16T12:08:12+00:00",
        emit_event=False, cur=cur, **kw)


def row(cur, key):
    cur.execute("SELECT * FROM session WHERE session_key = %s", (key,))
    return cur.fetchone()


def assert_first_hand_survives(cur, key, label):
    """The hook's facts, whichever order the two writers ran in."""
    r = row(cur, key)
    check(f"{label}: registration_source", r["registration_source"], "hook")
    check(f"{label}: workdir_source", r["workdir_source"], "hook-arg")
    check(f"{label}: model", r["model"], HOOK["model"])
    check(f"{label}: model_source", r["model_source"], HOOK["model_source"])
    check(f"{label}: time_source", r["time_source"], HOOK["time_source"])
    check(f"{label}: actor_type", r["actor_type"], HOOK["actor_type"])
    check(f"{label}: entrypoint", r["entrypoint"], HOOK["entrypoint"])
    check(f"{label}: permission_mode", r["permission_mode"], HOOK["permission_mode"])
    check(f"{label}: stated_goal", r["stated_goal"], HOOK["goal"])
    check(f"{label}: stated_goal_source", r["stated_goal_source"], HOOK["goal_source"])
    check(f"{label}: stated_goal_tier", r["stated_goal_tier"], HOOK["goal_tier"])


def main() -> int:
    print(f"=== database {TEST_DB} ===")
    admin = psycopg2.connect(config.dsn("postgres"))
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
        cur.execute(f'CREATE DATABASE "{TEST_DB}"')
    admin.close()

    import glob
    sql_paths = sorted(glob.glob(os.path.join(LANE, "schema", "*.sql")))
    conn = psycopg2.connect(config.dsn(TEST_DB))
    conn.autocommit = True
    for sql_path in sql_paths:
        with conn.cursor() as cur:
            cur.execute(open(sql_path).read())
    conn.close()
    print(f"schema applied ({', '.join(os.path.basename(p) for p in sql_paths)})\n")

    try:
        with store.transaction() as cur:
            # ---------------------------------------------------------------
            print("1. hook first, then backfill (the order 0136 assumed)")
            k = "order-hook-then-backfill"
            hook_register(cur, k)
            hook_register(cur, k, goal=True)          # UserPromptSubmit
            verbs.session_end(k, ended_at="2026-08-16T12:08:12.904514+00:00",
                              end_reason="hook", emit_event=False, cur=cur)
            ended_before = row(cur, k)["ended_at"]
            backfill_register(cur, k)
            assert_first_hand_survives(cur, k, "hook-then-backfill")
            r = row(cur, k)
            check("hook-then-backfill: ended_at survives backfill", r["ended_at"], ended_before)
            check("hook-then-backfill: ended_at is not NULL", r["ended_at"] is not None, True)
            check("hook-then-backfill: state", r["state"], "ended")

            # ---------------------------------------------------------------
            print("\n2. backfill first, then hook (the order that actually happens)")
            k2 = "order-backfill-then-hook"
            backfill_register(cur, k2)
            hook_register(cur, k2)
            hook_register(cur, k2, goal=True)
            assert_first_hand_survives(cur, k2, "backfill-then-hook")
            check("backfill-then-hook: state (open beats unknown)", row(cur, k2)["state"], "open")

            # ---------------------------------------------------------------
            print("\n3. a mid-session backfill must not restate a live session as unknown")
            k3 = "live-session-mid-backfill"
            hook_register(cur, k3)
            check("live: state after hook", row(cur, k3)["state"], "open")
            backfill_register(cur, k3)
            check("live: state after a backfill run", row(cur, k3)["state"], "open")

            # ---------------------------------------------------------------
            print("\n4. among hook writes the FIRST goal still wins (no regression)")
            k4 = "two-prompts-one-goal"
            hook_register(cur, k4)
            verbs.session_register(k4, kind="main", registration_source="hook",
                                   stated_goal="the first thing asked",
                                   stated_goal_source="hook:first-prompt",
                                   stated_goal_tier="A:explicit-brief",
                                   state="open", emit_event=False, cur=cur)
            verbs.session_register(k4, kind="main", registration_source="hook",
                                   stated_goal="a later, different instruction",
                                   stated_goal_source="hook:first-prompt",
                                   stated_goal_tier="A:explicit-brief",
                                   state="open", emit_event=False, cur=cur)
            check("two prompts: goal is the first one",
                  row(cur, k4)["stated_goal"], "the first thing asked")

            # ---------------------------------------------------------------
            print("\n5. backfill alone is unchanged (no hook anywhere near it)")
            k5 = "backfill-only"
            backfill_register(cur, k5)
            backfill_register(cur, k5)
            r = row(cur, k5)
            check("backfill-only: registration_source", r["registration_source"], "backfill")
            check("backfill-only: state", r["state"], "unknown")
            check("backfill-only: goal", r["stated_goal"], BACKFILL["goal"])
            check("backfill-only: ended_at stays NULL", r["ended_at"], None)

            # ---------------------------------------------------------------
            print("\n6. the trigger binds a writer that is not verbs.py")
            k6 = "hand-written-update"
            hook_register(cur, k6)
            verbs.session_end(k6, ended_at="2026-08-16T12:08:12.904514+00:00",
                              emit_event=False, cur=cur)
            was = row(cur, k6)["ended_at"]
            cur.execute("UPDATE session SET ended_at = NULL, state = 'open' "
                        "WHERE session_key = %s", (k6,))
            r = row(cur, k6)
            check("raw UPDATE: ended_at restored", r["ended_at"], was)
            check("raw UPDATE: state stays ended", r["state"], "ended")

            # ---------------------------------------------------------------
            print("\n7. session end is still able to record an end it observes")
            k7 = "end-still-works"
            hook_register(cur, k7)
            check("end-still-works: open before", row(cur, k7)["ended_at"], None)
            verbs.session_end(k7, ended_at="2026-08-16T13:00:00+00:00",
                              emit_event=False, cur=cur)
            r = row(cur, k7)
            check("end-still-works: ended_at set", str(r["ended_at"])[:19],
                  "2026-08-16 13:00:00")
            check("end-still-works: state", r["state"], "ended")
    finally:
        admin = psycopg2.connect(config.dsn("postgres"))
        admin.autocommit = True
        with admin.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
        admin.close()
        print(f"\ndropped {TEST_DB}")

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
