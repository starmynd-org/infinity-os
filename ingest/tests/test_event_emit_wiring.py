#!/usr/bin/env python3
"""Task 0287: the envelope is parked in the transaction and emitted after it commits.

Run it:  python3 ingest/tests/test_event_emit_wiring.py

Builds its own database (`d3_emit_wiring_test` by default), applies the scratch schema, and
drops it again. It never touches `d3_scratch` or `brain`.

**What this test can and cannot reach, said plainly.** It exercises the producer side: when
the verb is called, with what, and what happens to the outbox row. It does NOT apply D1's
migrations, so it never writes `brain.event` and cannot re-prove the foreign key that forced
this design. That refusal was measured on live and is quoted in `ingest/ingest/events.py`;
what is testable here is the ORDERING that refusal demands, and that is what every check below
is about. `$BRAIN_EVENT_EMIT` is a stub that records its stdin, which is also the only way to
assert the negative -- "the verb was NOT called yet" -- at all.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
LANE = os.path.dirname(HERE)
REPO = os.path.dirname(LANE)
sys.path.insert(0, LANE)

TEST_DB = os.environ.get("BRAIN_EMIT_WIRING_TEST_DB", "d3_emit_wiring_test")
os.environ["BRAIN_DB"] = TEST_DB
os.environ["BRAIN_PROFILE"] = "scratch"
os.environ.pop("BRAIN_DSN", None)

import psycopg2  # noqa: E402
import psycopg2.extras  # noqa: E402

from ingest import config, events, store  # noqa: E402

FAILURES: list[str] = []
CHECKS = 0

TMP = tempfile.mkdtemp(prefix="d3-emit-wiring-")
CALLS = os.path.join(TMP, "calls.jsonl")

#: A stub `event emit`. It records the envelope it was handed, one JSON object per line, so a
#: check can assert both that it ran and that it did not.
STUB = os.path.join(TMP, "stub-emit")
STUB_SRC = f"""#!/usr/bin/env python3
import json, os, sys
argv = sys.argv[1:]
assert argv[0] == "--json" and argv[1] == "-", argv
env = json.loads(sys.stdin.read())
with open({CALLS!r}, "a") as fh:
    fh.write(json.dumps(env) + "\\n")
if os.environ.get("STUB_FAIL") == "1":
    print("stub refused on purpose", file=sys.stderr)
    sys.exit(3)
print(json.dumps({{"event_id": "stub", "type": env["event_type"]}}))
"""


def check(name: str, got, want) -> None:
    global CHECKS
    CHECKS += 1
    if got == want:
        print(f"  ok   {name} = {got!r}")
    else:
        FAILURES.append(f"{name}: got {got!r}, want {want!r}")
        print(f"  FAIL {name}: got {got!r}, want {want!r}")


def calls() -> list:
    if not os.path.exists(CALLS):
        return []
    with open(CALLS) as fh:
        return [json.loads(line) for line in fh if line.strip()]


def reset_calls() -> None:
    if os.path.exists(CALLS):
        os.remove(CALLS)


def outbox() -> list:
    with store.read() as cur:
        cur.execute("SELECT seq, event_type, subject_id, emit_status, emit_detail, attempts, "
                    "requeues, failed_at, first_attempted_at, last_attempted_at "
                    "FROM event_outbox ORDER BY seq")
        return [dict(r) for r in cur.fetchall()]


def row(subject_id: str) -> dict:
    return [r for r in outbox() if r["subject_id"] == subject_id][0]


def park(cur, key: str, when: str = "2026-08-17T00:00:00+00:00",
         actor_type: str | None = "hybrid") -> None:
    events.emit(cur, events.SESSION_STARTED, when, key,
                {"kind": "main", "harness": "claude-code"}, actor_type=actor_type)


def setup() -> None:
    admin = psycopg2.connect(config.dsn("postgres"))
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute("DROP DATABASE IF EXISTS " + TEST_DB)
        cur.execute("CREATE DATABASE " + TEST_DB)
    admin.close()
    conn = psycopg2.connect(config.dsn(TEST_DB))
    conn.autocommit = True
    import glob
    for path in sorted(glob.glob(os.path.join(LANE, "schema", "*.sql"))):
        with open(path) as fh, conn.cursor() as cur:
            cur.execute(fh.read())
    conn.close()

    with open(STUB, "w") as fh:
        fh.write(STUB_SRC)
    os.chmod(STUB, 0o755)
    os.environ["BRAIN_EVENT_EMIT"] = STUB


def teardown() -> None:
    admin = psycopg2.connect(config.dsn("postgres"))
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute("DROP DATABASE IF EXISTS " + TEST_DB)
    admin.close()
    print(f"dropped {TEST_DB}")


def main() -> int:
    setup()

    print("\n1. THE VERB IS NOT CALLED INSIDE THE TRANSACTION")
    print("   `brain.event.session_id` references the session row this transaction has not")
    print("   committed, so an inline call could only fail or drop the link.")
    reset_calls()
    with store.transaction() as cur:
        park(cur, "sess-A")
        check("during the transaction: envelopes handed to the verb", len(calls()), 0)
        rows = [dict(r) for r in
                (cur.execute("SELECT emit_status FROM event_outbox"), cur.fetchall())[1]]
        check("during the transaction: outbox row is parked", rows[0]["emit_status"], "pending")
    check("after commit: envelopes handed to the verb", len(calls()), 1)
    check("after commit: it was the right envelope", calls()[0]["subject_id"], "sess-A")
    check("after commit: occurred_at is the session's, not the drain's",
          calls()[0]["occurred_at"], "2026-08-17T00:00:00+00:00")
    check("after commit: produced_by names the component", calls()[0]["produced_by"], "d3-ingest")
    check("after commit: outbox row is emitted", outbox()[0]["emit_status"], "emitted")

    print("\n2. A ROLLED-BACK SESSION EMITS NOTHING")
    print("   The park is inside the transaction precisely so this is impossible.")
    reset_calls()
    try:
        with store.transaction() as cur:
            park(cur, "sess-rolled-back")
            raise RuntimeError("the verb failed after parking")
    except RuntimeError:
        pass
    check("rolled back: envelopes handed to the verb", len(calls()), 0)
    check("rolled back: outbox rows", [r["subject_id"] for r in outbox()], ["sess-A"])

    print("\n3. THE DRAIN IS BOUNDED, BECAUSE IT RUNS ON THE OPERATOR'S SESSION-START PATH")
    reset_calls()
    was = events.AFTER_COMMIT_DRAIN_LIMIT
    events.AFTER_COMMIT_DRAIN_LIMIT = 2
    try:
        with store.transaction(drain_after_commit=False) as cur:
            for i in range(5):
                park(cur, f"sess-B{i}")
        check("parked with the drain suppressed", len(calls()), 0)
        with store.transaction() as cur:
            pass
        check("one drain emits at most the bound", len(calls()), 2)
        check("and it is FIFO", [c["subject_id"] for c in calls()], ["sess-B0", "sess-B1"])
    finally:
        events.AFTER_COMMIT_DRAIN_LIMIT = was

    print("\n4. A VERB THAT WILL NOT TAKE THE EVENT DOES NOT FAIL THE SESSION")
    reset_calls()
    os.environ["STUB_FAIL"] = "1"
    try:
        with store.transaction() as cur:
            park(cur, "sess-C")            # the session registration itself must still land
    finally:
        os.environ.pop("STUB_FAIL")
    still = [r for r in outbox() if r["subject_id"] == "sess-C"]
    check("the refused envelope stays parked", still[0]["emit_status"], "pending")
    check("and the refusal is recorded where the next reader looks",
          "stub refused on purpose" in (still[0]["emit_detail"] or ""), True)
    reset_calls()
    with store.transaction() as cur:
        pass
    check("a later drain retries it", [r["emit_status"] for r in outbox()
                                       if r["subject_id"] == "sess-C"], ["emitted"])

    print("\n5. REPLAY IS IDEMPOTENT BY CONSTRUCTION, NOT BY LUCK")
    print("   The event id is a uuid5 over (type, subject_type, subject_id, occurred_at) and")
    print("   `brain.event.event_id` is UNIQUE, so a second drain of one envelope collides.")
    # `event-emit` has no .py suffix (it is a command), so the loader is named explicitly
    # rather than inferred from the extension.
    import importlib.util
    from importlib.machinery import SourceFileLoader
    path = os.path.join(REPO, "fabric", "bin", "event-emit")
    spec = importlib.util.spec_from_loader("event_emit_shim", SourceFileLoader(
        "event_emit_shim", path))
    shim = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(shim)
    env = {"event_type": "session.started", "subject_type": "session", "subject_id": "s1",
           "occurred_at": "2026-08-17T00:00:00+00:00"}
    check("same envelope, same id", shim.event_id_for(env), shim.event_id_for(dict(env)))
    check("a different instant is a different event",
          shim.event_id_for({**env, "occurred_at": "2026-08-17T00:00:01+00:00"})
          != shim.event_id_for(env), True)

    print("\n6. THE FLAGS ARE DECIDED, NEVER DEFAULTED")
    try:
        shim.emit_envelope({**env, "event_type": "session.invented"})
        check("an undeclared type is refused", "not refused", "refused")
    except Exception as exc:
        check("an undeclared type is refused", exc.__class__.__name__, "EnvelopeRefused")
    try:
        shim.emit_envelope({k: v for k, v in env.items() if k != "occurred_at"})
        check("an envelope with no occurred_at is refused", "not refused", "refused")
    except Exception as exc:
        check("an envelope with no occurred_at is refused",
              exc.__class__.__name__, "EnvelopeRefused")

    print("\n7. THE EVENT'S actor_type IS THE SESSION ROW'S, NOT A LITERAL (task 0300)")
    print("   `producers/sessions.py` hardcoded 'ai' while `session register` wrote 'hybrid' for")
    print("   the same session. 150 events on live `brain` contradicted the row they point at.")
    reset_calls()
    with store.transaction() as cur:
        park(cur, "sess-hybrid", when="2026-08-17T01:00:00+00:00", actor_type="hybrid")
    check("the envelope carries actor_type at the TOP level, beside produced_by",
          calls()[0].get("actor_type"), "hybrid")
    check("and it is the hook's value, not the producer's guess",
          calls()[0].get("actor_type") == "ai", False)

    print("   Through the REAL verbs, both of them: the value each one puts in the envelope is")
    print("   read off its own RETURNING clause, so neither can drift from the session row.")
    from ingest import verbs  # noqa: PLC0415 -- only this section needs it
    reset_calls()
    verbs.session_register("sess-verbs", session_id="sess-verbs", kind="main",
                           harness="claude-code", actor_type="hybrid",
                           registration_source="hook")
    verbs.session_end("sess-verbs", end_reason="proof")
    by_type = {c["event_type"]: c for c in calls()}
    check("session register's envelope carries the row's actor_type",
          by_type.get("session.started", {}).get("actor_type"), "hybrid")
    check("session end's envelope carries it too, off its own UPDATE ... RETURNING",
          by_type.get("session.ended", {}).get("actor_type"), "hybrid")

    print("   The envelope is what the emitter maps, so map it and read the kwargs back.")
    mapped = {}
    real_started = shim._sessions.emit_session_started
    real_ended = shim._sessions.emit_session_ended
    try:
        shim._sessions.emit_session_started = lambda sid, **kw: mapped.update(kw) or {}
        shim._sessions.emit_session_ended = lambda sid, **kw: mapped.update(kw) or {}
        base = {"event_type": "session.started", "subject_type": "session",
                "subject_id": "s-map", "occurred_at": "2026-08-17T02:00:00+00:00"}

        shim.emit_envelope({**base, "actor_type": "hybrid"})
        check("event emit passes 'hybrid' through unchanged", mapped.get("actor_type"), "hybrid")

        mapped.clear()
        shim.emit_envelope({**base, "event_type": "session.ended", "actor_type": "hybrid"})
        check("session.ended too, so the two events cannot disagree",
              mapped.get("actor_type"), "hybrid")

        # THE REGRESSION GUARD. An envelope with no actor_type must emit NULL, which means
        # "not recorded". If it ever emits 'ai' again, this check is the one that fails.
        mapped.clear()
        shim.emit_envelope(dict(base))
        check("an envelope with NO actor_type emits NULL, never 'ai'",
              mapped.get("actor_type", "MISSING"), None)

        mapped.clear()
        shim.emit_envelope({**base, "actor_type": "human"})
        check("'human' is legal too: this file judges nothing but the domain",
              mapped.get("actor_type"), "human")
    finally:
        shim._sessions.emit_session_started = real_started
        shim._sessions.emit_session_ended = real_ended

    try:
        shim.emit_envelope({**base, "actor_type": "robot"})
        check("a value outside brain.actor_type is refused", "not refused", "refused")
    except Exception as exc:
        check("a value outside brain.actor_type is refused",
              exc.__class__.__name__, "EnvelopeRefused")

    # A default would be indistinguishable from a chosen value, which is the defect itself.
    try:
        with store.transaction() as cur:
            events.emit(cur, events.SESSION_STARTED, "2026-08-17T03:00:00+00:00", "sess-noarg",
                        {"kind": "main"})
        check("events.emit refuses to be called without actor_type", "accepted", "TypeError")
    except TypeError:
        check("events.emit refuses to be called without actor_type", "TypeError", "TypeError")

    print("\n8. AN ENVELOPE THAT CAN NEVER EMIT REACHES A TERMINAL STATE (task 0302)")
    print("   `emit_status` carried a 'failed' value in its CHECK and nothing ever wrote it, so")
    print("   an undeliverable envelope and one waiting its turn were the same row, at the head")
    print("   of a FIFO drain, starving everything behind them.")
    reset_calls()
    was_max = events.MAX_EMIT_ATTEMPTS
    was_limit = events.AFTER_COMMIT_DRAIN_LIMIT
    events.MAX_EMIT_ATTEMPTS = 3
    try:
        with store.transaction(drain_after_commit=False) as cur:
            park(cur, "sess-doomed", when="2026-08-17T04:00:00+00:00")

        os.environ["STUB_FAIL"] = "1"
        try:
            for expected in (1, 2):
                with store.transaction() as cur:
                    pass
                check(f"refusal {expected}: still pending, attempts counted",
                      (row("sess-doomed")["emit_status"], row("sess-doomed")["attempts"]),
                      ("pending", expected))
            with store.transaction() as cur:
                pass
        finally:
            os.environ.pop("STUB_FAIL")
        doomed = row("sess-doomed")
        check("the MAX_EMIT_ATTEMPTS'th refusal is terminal", doomed["emit_status"], "failed")
        check("failed_at is set, so the CHECK constraint holds", doomed["failed_at"] is not None, True)
        check("the detail says it was RETRIED, not dropped on first contact",
              "gave up after 3 attempts" in (doomed["emit_detail"] or ""), True)
        check("and the last refusal survives verbatim inside it",
              "stub refused on purpose" in (doomed["emit_detail"] or ""), True)
        check("first_attempted_at and last_attempted_at are both recorded",
              (doomed["first_attempted_at"] is not None,
               doomed["last_attempted_at"] is not None), (True, True))

        print("\n   NOTHING WAS DISCARDED. The payload is still on the row.")
        with store.read() as cur:
            cur.execute("SELECT payload FROM event_outbox WHERE subject_id = 'sess-doomed'")
            check("the failed row still carries its whole envelope",
                  cur.fetchone()["payload"]["occurred_at"], "2026-08-17T04:00:00+00:00")

        print("\n   AND IT IS OUT OF THE HEAD, which is the starvation half of the fix.")
        reset_calls()
        with store.transaction(drain_after_commit=False) as cur:
            park(cur, "sess-behind", when="2026-08-17T05:00:00+00:00")
        with store.transaction() as cur:
            pass
        check("an envelope queued behind a failed one emits",
              row("sess-behind")["emit_status"], "emitted")

        print("\n   A RETRY NEVER OUTRANKS A FRESH ENVELOPE, even before it terminates.")
        print("   Ordering by seq alone is what made the seven rows a starvation risk.")
        events.MAX_EMIT_ATTEMPTS = 99          # keep the refused row pending, not retired
        events.AFTER_COMMIT_DRAIN_LIMIT = 1    # one slot, so the ordering is the whole answer
        with store.transaction(drain_after_commit=False) as cur:
            park(cur, "sess-head-blocker", when="2026-08-17T06:00:00+00:00")
        os.environ["STUB_FAIL"] = "1"
        try:
            with store.transaction() as cur:
                pass
        finally:
            os.environ.pop("STUB_FAIL")
        check("the blocker is refused once and stays pending",
              (row("sess-head-blocker")["emit_status"], row("sess-head-blocker")["attempts"]),
              ("pending", 1))
        reset_calls()
        with store.transaction(drain_after_commit=False) as cur:
            park(cur, "sess-newcomer", when="2026-08-17T07:00:00+00:00")
        with store.transaction() as cur:
            pass
        check("the one drain slot goes to the newcomer, not the lower seq",
              [c["subject_id"] for c in calls()], ["sess-newcomer"])
        events.AFTER_COMMIT_DRAIN_LIMIT = was_limit

        print("\n   REQUEUE IS THE WAY BACK, which is what makes 'failed' safe to write at all.")
        events.MAX_EMIT_ATTEMPTS = 3
        with store.transaction(drain_after_commit=False) as cur:
            res = events.requeue(cur, seqs=[doomed["seq"]], reason="the cause was fixed")
        check("the failed row moved", res["requeued"], 1)
        back = row("sess-doomed")
        check("it is pending again", back["emit_status"], "pending")
        check("failed_at was cleared with it", back["failed_at"], None)
        check("attempts reset so it competes fairly for the head", back["attempts"], 0)
        check("but the reset did not erase its own evidence", back["requeues"], 1)
        check("and the reason rides beside the refusal it replaces",
              "the cause was fixed" in (back["emit_detail"] or "")
              and "gave up after 3 attempts" in (back["emit_detail"] or ""), True)
        reset_calls()
        with store.transaction() as cur:
            pass
        check("and now it emits", row("sess-doomed")["emit_status"], "emitted")
        check("with the occurred_at it was parked with, not the requeue's clock",
              calls()[0]["occurred_at"], "2026-08-17T04:00:00+00:00")

        print("\n   REQUEUE TOUCHES ONLY FAILED ROWS, AND NAMES WHAT IT DID NOT MOVE.")
        emitted_seq = row("sess-doomed")["seq"]
        with store.transaction(drain_after_commit=False) as cur:
            res = events.requeue(cur, seqs=[emitted_seq])
        check("an emitted row is not re-emitted from here", res["requeued"], 0)
        check("and the caller is told which seq did not move",
              res["not_failed_or_absent"], [emitted_seq])
        check("the emitted row is untouched", row("sess-doomed")["emit_status"], "emitted")
        try:
            with store.transaction(drain_after_commit=False) as cur:
                events.requeue(cur)
            check("requeue with no scope is refused", "accepted", "ValueError")
        except ValueError:
            check("requeue with no scope is refused", "ValueError", "ValueError")
    finally:
        events.MAX_EMIT_ATTEMPTS = was_max
        events.AFTER_COMMIT_DRAIN_LIMIT = was_limit

    print("\n9. TWO CONCURRENT DRAINS NEVER HOLD THE SAME ENVELOPE (task 0426)")
    print("   Ten sessions ending together run ten drains. Before `FOR UPDATE SKIP LOCKED`")
    print("   they read each other's rows: on live `brain`, seq 825 and 826 went from 0 to")
    print("   MAX_EMIT_ATTEMPTS in 968 ms and were retired by a refusal that lasted under a")
    print("   second, and seq 818-820 read `emitted` while carrying a refusal in emit_detail.")
    reset_calls()
    with store.transaction(drain_after_commit=False) as cur:
        park(cur, "sess-race-a", when="2026-08-17T08:00:00+00:00")
        park(cur, "sess-race-b", when="2026-08-17T09:00:00+00:00")

    with store.connect(TEST_DB) as conn_a, store.connect(TEST_DB) as conn_b:
        cur_a = conn_a.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur_b = conn_b.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        # Without SKIP LOCKED the second drain does not fail, it BLOCKS on the first drain's
        # row lock, and a regression test that hangs teaches nobody anything. The timeout is
        # what turns that hang into a named failure.
        cur_b.execute("SET lock_timeout = '3s'")
        a = events.drain(cur_a, limit=1)          # holds its row, deliberately uncommitted
        b = events.drain(cur_b, limit=4)          # must skip it rather than wait for it
        conn_a.commit()
        conn_b.commit()

    check("the first drain took one envelope", a["attempted"], 1)
    check("the second drain took the other and did not wait", b["attempted"], 1)
    check("between them each envelope was attempted exactly once",
          sorted(c["subject_id"] for c in calls()), ["sess-race-a", "sess-race-b"])
    check("and both are emitted, once each",
          [row("sess-race-a")["attempts"], row("sess-race-b")["attempts"]], [1, 1])
    check("no row carries a status one drain wrote and a detail another did",
          [r["emit_status"] for r in outbox()
           if r["subject_id"] in ("sess-race-a", "sess-race-b")], ["emitted", "emitted"])

    teardown()
    print(f"\n{CHECKS - len(FAILURES)}/{CHECKS} checks passed")
    for f in FAILURES:
        print(f"  FAILED: {f}")
    print("PASS" if not FAILURES else "FAIL")
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    sys.exit(main())
