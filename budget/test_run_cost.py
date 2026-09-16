"""The meter cannot lose a session, and it cannot invent one.

    python3 -m budget.test_run_cost

Hermetic: builds its own stream files in a temp dir, touches no store and no network. The two
shapes it asserts are the two the 2026-08-16 corpus actually contains, and they pull in opposite
directions, which is the whole reason this file exists:

    0092-attempt1   TWO session ids in one file        -> both must be charged
    0012-attempt1   ONE session id, 19 result events   -> only the last must be charged

A reader that sums every result event passes the first and overstates the second by 10.8x. A
reader that keeps only the last result event passes the second and silently drops $11.4242 of the
first. Only "last within a session, summed across sessions" passes both.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

from budget.run_cost import session_costs

FAILED = []


def check(name, got, want):
    if got == want:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}\n         got  {got!r}\n         want {want!r}")


def result_ev(sid, usd, turns=1):
    return json.dumps({"type": "result", "subtype": "success", "session_id": sid,
                       "total_cost_usd": usd, "num_turns": turns})


def noise(sid):
    return json.dumps({"type": "stream_event", "session_id": sid, "event": {"type": "ping"}})


def write(tmp, name, lines):
    p = os.path.join(tmp, name)
    with open(p, "w") as fh:
        fh.write("".join(l + "\n" for l in lines))
    return p


def test_two_interleaved_sessions_are_both_charged(tmp):
    """The 0092-attempt1 shape: two engines, one file, sub-second interleave."""
    a, b = "4aee2969-aaaa", "7912efa2-bbbb"
    p = write(tmp, "two.stream.jsonl", [
        noise(a), noise(b), noise(a), noise(b),
        result_ev(a, 11.424178999999999, 113),
        noise(b),
        result_ev(b, 0.9658025, 12),
    ])
    check("two sessions in one file are both returned", session_costs(p),
          [(a, 11.424178999999999), (b, 0.9658025)])


def test_cumulative_result_events_are_not_summed(tmp):
    """The 0012-attempt1 shape: one session, 19 result events, a RUNNING TOTAL not increments."""
    sid = "0012aaaa-cccc"
    rising = [5.7808, 9.1, 14.62, 21.004, 27.2951]
    p = write(tmp, "cumulative.stream.jsonl", [result_ev(sid, u) for u in rising])
    check("one session's last result wins, its earlier ones are not added",
          session_costs(p), [(sid, 27.2951)])
    check("and the total is the last value, not the sum",
          round(sum(u for _, u in session_costs(p)), 4), 27.2951)


def test_both_shapes_at_once(tmp):
    """The case that breaks either single rule: two sessions, one of them cumulative."""
    a, b = "sess-a", "sess-b"
    p = write(tmp, "both.stream.jsonl", [
        result_ev(a, 1.0), result_ev(b, 2.0), result_ev(a, 3.0), result_ev(b, 5.0),
        result_ev(a, 4.5),
    ])
    check("last-within-session across two interleaved cumulative sessions",
          session_costs(p), [(a, 4.5), (b, 5.0)])


def test_a_torn_line_does_not_stop_the_read(tmp):
    """A shared file is where torn lines come from, so one must not cost the run its charge."""
    sid = "torn-sess"
    p = write(tmp, "torn.stream.jsonl", [
        noise(sid), '{"type": "result", "session_id": "torn-sess", "total_cost',
        result_ev(sid, 2.5), "", "   ",
    ])
    check("a truncated json line is skipped, the good result still lands",
          session_costs(p), [(sid, 2.5)])


def test_run_json_carries_a_run_with_no_stream(tmp):
    """Codex writes no stream-json at all. Its run json is the only evidence there is."""
    rj = os.path.join(tmp, "codex.json")
    with open(rj, "w") as fh:
        json.dump({"type": "result", "session_id": "", "total_cost_usd": 3.25}, fh)
    check("a run json with no session id is charged when the stream produced nothing",
          session_costs("", rj), [("", 3.25)])


def test_run_json_never_overrides_the_stream(tmp):
    """The stream is where a LATER result for the same session would appear. It wins."""
    sid = "shared-sess"
    p = write(tmp, "later.stream.jsonl", [result_ev(sid, 1.0), result_ev(sid, 9.0)])
    rj = os.path.join(tmp, "later.json")
    with open(rj, "w") as fh:
        json.dump({"type": "result", "session_id": sid, "total_cost_usd": 1.0}, fh)
    check("a stale run json does not undercut the stream's last result",
          session_costs(p, rj), [(sid, 9.0)])


def test_run_json_adds_the_session_the_stream_lost(tmp):
    """The residual case: the stream was truncated after a session's result event landed."""
    survivor, lost = "survivor-sess", "lost-sess"
    p = write(tmp, "lost.stream.jsonl", [result_ev(survivor, 2.0)])
    rj = os.path.join(tmp, "lost.json")
    with open(rj, "w") as fh:
        json.dump({"type": "result", "session_id": lost, "total_cost_usd": 7.0}, fh)
    check("a session present only in the run json is still charged",
          session_costs(p, rj), [(lost, 7.0), (survivor, 2.0)])


def test_a_zero_cost_run_is_reported_not_dropped(tmp):
    """`swarm-run`'s comment: a run that cost nothing is evidence, not an absence."""
    sid = "zero-sess"
    p = write(tmp, "zero.stream.jsonl", [result_ev(sid, 0)])
    check("a $0 session is returned so it can be charged", session_costs(p), [(sid, 0.0)])


def test_a_missing_file_is_empty_not_an_error(tmp):
    check("a stream that does not exist reads as no sessions",
          session_costs(os.path.join(tmp, "nope.jsonl")), [])


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    with tempfile.TemporaryDirectory() as tmp:
        for t in tests:
            print(t.__name__)
            t(tmp)
    print()
    if FAILED:
        print(f"FAILED {len(FAILED)} of {len(tests)}: {', '.join(FAILED)}")
        return 1
    print(f"all {len(tests)} tests pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
