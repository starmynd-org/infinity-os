#!/usr/bin/env python3
"""Task 0441: `scan()` sums `message.usage` once per MESSAGE, never once per RECORD.

Run it:  python3 ingest/tests/test_token_denominator.py

THE DEFECT THIS PINS. The harness writes one assistant message to a transcript as SEVERAL
records, and every one of them repeats the COMPLETED `message.usage` block -- not a partial and
not a delta, the same finished numbers. `scan()` accumulated with four `+=` inside the per-record
loop, so each message's tokens were counted once per record. Re-measured 2026-08-29 over the whole
corpus root `~/.claude/projects`, population 1496 files, scanned 1496, gap 0:

    assistant records carrying usage   174,060
    distinct message.id                 83,779       record/message ratio 2.0776
    message.id present                 174,060 of 174,060   the key is total; no fallback fired
    priced via budget/price_card.py     $27,749.37 naive  vs  $12,508.47 deduped  =  2.2184x

Nothing prices those tokens today -- `cost_usd` is NULL by design -- which is exactly why this
needed a test rather than a fix alone. A wrong number that no surface reads is a wrong number
waiting for a surface to read it.

WHY THE FIXTURE ASSERTS ITS OWN REPETITION FIRST. A transcript in which every message appears
once would satisfy every equality below while proving nothing, because dedup and naive summation
agree on it. So scene 1 asserts `usage_records > messages` on the fixture BEFORE any verdict
about the token totals, and scene 2 computes the naive sum the old code would have produced and
requires it to DIFFER. Those two are the negative control: they fail if the fixture stops
exercising the defect, which is the way this suite would otherwise rot into a tautology.

HERMETIC. Scenes 1 to 5 read fixtures this file writes into a temporary directory and touch no
database, no corpus and no network, so they run everywhere. Scene 6 needs postgres to prove the
three new columns survive a round trip through `session register`; when postgres is unreachable it
prints NOT RUN, names the condition it DETECTED, and contributes nothing to the denominator --
it is never counted as a pass.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
LANE = os.path.dirname(HERE)
sys.path.insert(0, LANE)

from ingest import transcript as tx  # noqa: E402

FAILURES: list[str] = []
CHECKS = 0
NOT_RUN: list[str] = []

USAGE_A = {"input_tokens": 10, "output_tokens": 100,
           "cache_read_input_tokens": 1000, "cache_creation_input_tokens": 50}
USAGE_B = {"input_tokens": 5, "output_tokens": 7,
           "cache_read_input_tokens": 0, "cache_creation_input_tokens": 3}
USAGE_C = {"input_tokens": 1, "output_tokens": 1,
           "cache_read_input_tokens": 2, "cache_creation_input_tokens": 4}
USAGE_D = {"input_tokens": 2, "output_tokens": 2,
           "cache_read_input_tokens": 8, "cache_creation_input_tokens": 16}

COLS = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")


def check(label: str, got, want) -> None:
    global CHECKS
    CHECKS += 1
    if got == want:
        print(f"  ok   {label} = {got!r}")
    else:
        FAILURES.append(f"{label}: got {got!r}, want {want!r}")
        print(f"  FAIL {label}: got {got!r}, want {want!r}")


def rec(**kw) -> str:
    return json.dumps(kw)


def assistant(msg_id, usage, request_id=None, model="claude-opus-5"):
    """One RECORD of an assistant message. Several of these share a `message.id` on purpose."""
    msg = {"role": "assistant", "model": model, "content": [{"type": "text", "text": "x"}]}
    if msg_id is not None:
        msg["id"] = msg_id
    if usage is not None:
        msg["usage"] = usage
    out = {"type": "assistant", "sessionId": "11111111-2222-3333-4444-555555555555",
           "timestamp": "2026-08-29T10:00:00.000Z", "cwd": "/tmp", "message": msg}
    if request_id is not None:
        out["requestId"] = request_id
    return json.dumps(out)


def write_fixture(root: str) -> str:
    """A transcript that repeats each message's completed usage across several records.

    The shape is taken from the real corpus: three records for the first message, two for the
    second. The last two records are the two fallback rungs -- a record with no `message.id` but
    a `requestId`, written twice, and a record with NEITHER key, written once.
    """
    sid = "11111111-2222-3333-4444-555555555555"
    d = os.path.join(root, "-tmp-somewhere")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{sid}.jsonl")
    lines = [
        rec(type="user", sessionId=sid, cwd="/tmp",
            timestamp="2026-08-29T09:59:00.000Z",
            message={"role": "user", "content": "do the thing"}),
        assistant("msg_A", USAGE_A),
        assistant("msg_A", USAGE_A),          # same message, same completed usage
        assistant("msg_A", USAGE_A),          # and again
        assistant("msg_B", USAGE_B),
        assistant("msg_B", USAGE_B),
        assistant(None, USAGE_C, request_id="req_C"),   # id missing, requestId is the fallback
        assistant(None, USAGE_C, request_id="req_C"),
        assistant(None, USAGE_D),             # neither key: counted ONCE, never dropped
        assistant("msg_E", None),             # an assistant record carrying no usage at all
    ]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return path


def naive_totals(path: str) -> dict:
    """What the OLD code produced: one `+=` per record, no dedup. The negative control."""
    tot = dict.fromkeys(COLS, 0)
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r.get("type") != "assistant":
                continue
            u = (r.get("message") or {}).get("usage")
            if isinstance(u, dict):
                for c in COLS:
                    tot[c] += int(u.get(c) or 0)
    return tot


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="t4-0441-") as root:
        path = write_fixture(root)
        f = tx.scan(path, root)

        print("=== scene 1: the fixture exercises the defect (negative control) ===")
        # Asserted BEFORE any verdict about totals. If these two were equal, every equality in
        # scene 3 would hold for a fixture with no repetition in it, and the suite would be
        # measuring nothing.
        check("usage records counted", f.tokens_usage_records, 8)
        check("distinct messages", f.tokens_messages, 4)
        check("records exceed messages, so dedup has work to do",
              f.tokens_usage_records > f.tokens_messages, True)

        print("\n=== scene 2: the old per-record sum and the new one DIFFER ===")
        naive = naive_totals(path)
        check("naive input (old behaviour)", naive["input_tokens"], 44)
        check("naive output (old behaviour)", naive["output_tokens"], 318)
        check("dedup input differs from naive", f.tokens_input != naive["input_tokens"], True)
        check("dedup output differs from naive", f.tokens_output != naive["output_tokens"], True)

        print("\n=== scene 3: each message's usage is counted exactly once ===")
        # A once, B once, req_C once, the keyless record once.
        check("tokens_input", f.tokens_input, 10 + 5 + 1 + 2)
        check("tokens_output", f.tokens_output, 100 + 7 + 1 + 2)
        check("tokens_cache_read", f.tokens_cache_read, 1000 + 0 + 2 + 8)
        check("tokens_cache_creation", f.tokens_cache_creation, 50 + 3 + 4 + 16)

        print("\n=== scene 4: the denominator is stored beside the sums ===")
        check("tokens_source names the method", f.tokens_source, "message-usage-deduped")
        check("as_dict carries the denominator", "tokens_messages" in f.as_dict(), True)
        check("as_dict carries the record count", "tokens_usage_records" in f.as_dict(), True)
        check("no price is invented here", f.cost_usd, None)
        check("and no cost source either", f.cost_source, None)
        # turns_assistant counts RECORDS, and must keep doing so: it is a different question from
        # how many messages carried usage, and conflating them would hide the ratio again.
        check("turns_assistant still counts records", f.turns_assistant, 9)

        print("\n=== scene 5: a transcript with no repetition is unchanged, not 'fixed' ===")
        d2 = os.path.join(root, "-tmp-clean")
        os.makedirs(d2, exist_ok=True)
        p2 = os.path.join(d2, "22222222-2222-3333-4444-555555555555.jsonl")
        with open(p2, "w", encoding="utf-8") as fh:
            fh.write(assistant("solo", USAGE_A) + "\n")
        g = tx.scan(p2, root)
        check("one record, one message", (g.tokens_usage_records, g.tokens_messages), (1, 1))
        check("dedup is a no-op there", g.tokens_input, 10)
        check("and it still says so", g.tokens_source, "message-usage-deduped")

        print("\n=== scene 6: the three columns survive a round trip through the store ===")
        db_ok, why = _try_store(f)
        if db_ok is None:
            NOT_RUN.append(why)
            print(f"  NOT RUN  {why}")

    total = CHECKS
    # Scenes 1-5 are hermetic and always contribute, so a zero here means the suite did not
    # execute, not that everything held.
    if total == 0:                                              # DENOMINATOR
        print("0 comparisons made. A verdict over an empty set is not a pass.")
        return 2

    print(f"\n{total - len(FAILURES)}/{total} checks passed, {len(FAILURES)} failed")
    for n in NOT_RUN:
        print(f"NOT RUN: {n}")
    if FAILURES:
        print("FAILED:")
        for x in FAILURES:
            print(f"  - {x}")
        return 1
    print("PASS")
    return 0


def _try_store(f):
    """Round-trip the facts through `session register` on a scratch-profile database.

    Returns (True, "") when it ran, (None, reason) when it could not. The reason names the
    condition DETECTED -- the exception postgres raised -- not a guess at why.
    """
    import glob
    dbname = os.environ.get("BRAIN_TOKDEN_TEST_DB", "d3_tokden_test_scratch")
    try:
        import psycopg2
        from ingest import config
    except Exception as exc:                                  # pragma: no cover
        return None, f"psycopg2/config import failed: {exc.__class__.__name__}: {exc}"
    try:
        conn = psycopg2.connect(config.dsn("postgres"))
    except Exception as exc:
        return None, (f"postgres unreachable at {config.PG_HOST}:{config.PG_PORT}: "
                      f"{exc.__class__.__name__}: {str(exc).strip()}")
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
        cur.execute(f'CREATE DATABASE "{dbname}"')
    conn.close()

    os.environ["BRAIN_PROFILE"] = "scratch"
    os.environ["BRAIN_DB"] = dbname
    try:
        c2 = psycopg2.connect(config.dsn(dbname))
        c2.autocommit = True
        for p in sorted(glob.glob(os.path.join(LANE, "schema", "*.sql"))):
            with c2.cursor() as cur:
                cur.execute(open(p, encoding="utf-8").read())
        c2.close()

        import psycopg2.extras
        from ingest import store, verbs
        with store.connect(dbname) as conn2:
            with conn2.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                verbs.session_register(
                    session_key="0441-round-trip", kind=tx.KIND_MAIN, harness="claude-code",
                    workdir="/tmp", registration_source="backfill", actor_type="ai",
                    tokens={"input": f.tokens_input, "output": f.tokens_output,
                            "cache_read": f.tokens_cache_read,
                            "cache_creation": f.tokens_cache_creation,
                            "messages": f.tokens_messages,
                            "usage_records": f.tokens_usage_records,
                            "source": f.tokens_source},
                    emit_event=False, cur=cur)
                cur.execute("SELECT tokens_input, tokens_messages, tokens_usage_records, "
                            "tokens_source, cost_usd FROM session WHERE session_key = %s",
                            ("0441-round-trip",))
                row = cur.fetchone()
            conn2.commit()
        check("stored tokens_input", row["tokens_input"], f.tokens_input)
        check("stored tokens_messages", row["tokens_messages"], f.tokens_messages)
        check("stored tokens_usage_records", row["tokens_usage_records"], f.tokens_usage_records)
        check("stored tokens_source", row["tokens_source"], "message-usage-deduped")
        check("cost_usd is still NULL in the store", row["cost_usd"], None)
        return True, ""
    finally:
        os.environ.pop("BRAIN_PROFILE", None)
        os.environ.pop("BRAIN_DB", None)
        try:
            conn = psycopg2.connect(config.dsn("postgres"))
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
