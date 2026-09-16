#!/usr/bin/env python3
"""`swarm utilization` reports the meter, or reports UNKNOWN. It never reports a third thing.

Task 0280. The fleet's standing commitment is to stop before the seven-day rate limit reaches a
ceiling, because both Claude config dirs are ONE account (measured on 0261) and at 1.0 the
OPERATOR loses his own Claude access until the window resets. That commitment was unenforceable
for the plainest possible reason: no surface in the engine could read the meter.

A gauge a ceiling depends on has exactly one way to be dangerous, and it is not being absent.
It is answering with a number that is not the meter. So every test below is about a way this
could produce a confident wrong number:

  test_reads_the_meter                  the ordinary read, values and counts and window
  test_bracket_is_the_midpoint          the event has no timestamp of its own; it is bracketed
  test_previous_window_is_excluded      a dead window's 0.98 is not this window's spike
  test_five_hour_is_not_seven_day       a different meter on the same line shape
  test_unplaced_reading_is_not_guessed  an event with no timestamped line either side
  test_high_water_beats_the_latest      the meter does not fall; do not print the lower one
  test_stale_is_declared                an old reading is a FLOOR and says so
  test_expired_window_is_unknown        the window turned over: the last number is dead
  test_unknown_when_*                   the four ways there is nothing to read
  test_unknown_never_carries_a_number   the structural one: UNKNOWN prints no value at all
  test_exit_codes                       0 with a reading, 2 without, through the real CLI

Engine-free, store-free, network-free, deterministic. Fixtures are written to a tmpdir.

Run: python3 engine/tests/test_utilization_gauge.py
"""
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENGINE = HERE.parent
sys.path.insert(0, str(ENGINE))

from swarm_engine import ratelimit  # noqa: E402

RESET_CURRENT = 1787508000          # 2026-08-23T18:00:00Z, the window 0261 measured
RESET_PREVIOUS = 1787162400         # 2026-08-19T18:00:00Z, the window before it
NOW = datetime(2026, 8, 19, 2, 0, 0, tzinfo=timezone.utc)


def reset_ahead_of(when, days=7):
    """A window reset that is still in the future relative to `when`. Task 0379.

    THE TWO CLOCKS THIS FILE RUNS ON, AND WHY THEY MAY NOT BE MIXED.

    Every scene above `test_exit_codes` freezes time: it passes `now=NOW` into `ratelimit.read`,
    so `RESET_CURRENT` -- a literal four days after `NOW` -- is a live window forever, and those
    scenes are deterministic at any real date. They are correct and they stay pinned.

    `test_exit_codes` cannot freeze time, because it goes through the real CLI, which has no
    `--now`. It timestamps its fixture with `datetime.now()`. Stamping THAT fixture with
    `RESET_CURRENT` was the defect: on 2026-08-23 at 18:00:00Z the real clock crossed the
    literal, and from that instant the fixture described a fresh reading inside a window that had
    already reset. `ratelimit` answered UNKNOWN, correctly, and four assertions went red with no
    hint of the cause -- measured 2026-08-24T00:38Z, and the same suite was 47 of 47 green at
    2026-08-23T15:50Z with no code change in between.

    This is NOT the environment dependency it was filed as. The scene writes its own fixture into
    a tempdir and reads it back with `--runs <that dir>`; it never touches the live runs
    directory or the live meter. It was a fixture with an expiry date, which is live state
    wearing a fixture's clothes. See `docs/SUITE-INPUT-RULE.md`.

    Deriving the reset from the same clock that stamps the lines is not a widened tolerance: the
    assertions below are unchanged and still demand a real reading, a real value on stdout and
    the four JSON fields. What changes is that the fixture stops describing an impossible state.
    """
    return int((when + timedelta(days=days)).timestamp())

FAILURES = []
CHECKS = 0


def check(name, cond, detail=""):
    global CHECKS
    CHECKS += 1
    print(f"  {'ok  ' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def ts(dt):
    return dt.isoformat().replace("+00:00", "Z")


def line_ts(dt, kind="assistant"):
    return json.dumps({"type": kind, "timestamp": ts(dt), "session_id": "s1"})


def line_rl(u, reset=RESET_CURRENT, kind="seven_day", status="allowed_warning"):
    return json.dumps({
        "type": "rate_limit_event",
        "rate_limit_info": {"rateLimitType": kind, "utilization": u,
                            "status": status, "isUsingOverage": False, "resetsAt": reset},
        "uuid": "u1", "session_id": "s1"})


def stream(d, name, lines):
    p = Path(d) / f"{name}.stream.jsonl"
    p.write_text("\n".join(lines) + "\n")
    return p


def ordinary(d):
    """Two streams, a rising staircase, one reading from a dead window mixed in."""
    base = NOW - timedelta(minutes=40)
    stream(d, "0100-attempt1", [
        line_ts(base), line_rl(0.60), line_ts(base + timedelta(seconds=60)),
        line_rl(0.61), line_ts(base + timedelta(seconds=120)),
        # a reading from the PREVIOUS window, exactly as 0261 found three of on the live set
        line_rl(0.98, reset=RESET_PREVIOUS), line_ts(base + timedelta(seconds=180)),
    ])
    stream(d, "0101-attempt1", [
        line_ts(base + timedelta(minutes=20)), line_rl(0.62),
        line_ts(base + timedelta(minutes=20, seconds=100)),
        # a FIVE-hour reading: same line shape, different meter, must not be counted
        line_rl(0.05, kind="five_hour"), line_ts(base + timedelta(minutes=21)),
        line_rl(0.63), line_ts(base + timedelta(minutes=38)),
    ])


def main():
    print(__doc__.splitlines()[0])

    with tempfile.TemporaryDirectory() as d:
        ordinary(d)
        r = ratelimit.read(directory=d, now=NOW)

        print("\ntest_reads_the_meter")
        check("ok", r["ok"] is True, r.get("reason"))
        check("utilization is the newest reading", r["utilization"] == 0.63, r.get("utilization"))
        check("reset is the CURRENT window", r["window_resets_at"] == "2026-08-23T18:00:00Z",
              r.get("window_resets_at"))
        check("hours_to_reset is forward", 111 < r["hours_to_reset"] < 113, r["hours_to_reset"])
        check("streams counted", r["streams"] == 2, r.get("streams"))
        check("readings counted", r["readings"] == 4, r.get("readings"))
        check("status carried through", r["status"] == "allowed_warning")

        print("\ntest_bracket_is_the_midpoint")
        # The 0.63 event sits between +20:100s and +38:00 in stream 0101. Midpoint, not either end.
        base = NOW - timedelta(minutes=40)
        lo, hi = base + timedelta(minutes=21), base + timedelta(minutes=38)
        check("observed_at is the midpoint of its bracket",
              r["observed_at"] == ts(lo + (hi - lo) / 2), r["observed_at"])
        check("both bracket ends are reported", r["observed_bracket"] == [ts(lo), ts(hi)],
              r["observed_bracket"])

        print("\ntest_previous_window_is_excluded")
        check("the dead window's reading is excluded", r["readings_previous_windows"] == 1,
              r.get("readings_previous_windows"))
        check("0.98 from the dead window is NOT the answer", r["utilization"] != 0.98)
        check("and it is not the high-water mark either", r["high_water"] == 0.63,
              r.get("high_water"))

        print("\ntest_five_hour_is_not_seven_day")
        # Six rate_limit_event lines were written. Five are seven_day (four current, one dead
        # window); the sixth is a five_hour reading of 0.05 wearing the same line shape.
        check("all five seven-day readings are accounted for",
              r["readings"] + r["readings_previous_windows"] == 5,
              f'{r["readings"]} + {r["readings_previous_windows"]}')
        scanned, _ = ratelimit.scan(Path(d))
        check("the five_hour value is nowhere in the scan",
              0.05 not in [x["utilization"] for x in scanned],
              str([x["utilization"] for x in scanned]))

    print("\ntest_unplaced_reading_is_not_guessed")
    with tempfile.TemporaryDirectory() as d:
        ordinary(d)
        # An event with no timestamped line before OR after it, at the top of its own stream.
        stream(d, "0102-attempt1", [line_rl(0.99)])
        r = ratelimit.read(directory=d, now=NOW)
        check("it is counted as unplaced", r["unplaced_readings"] == 1, r.get("unplaced_readings"))
        check("it does not become the answer", r["utilization"] == 0.63, r["utilization"])
        check("and it does not raise the high-water mark", r["high_water"] == 0.63,
              r.get("high_water"))

    print("\ntest_high_water_beats_the_latest")
    with tempfile.TemporaryDirectory() as d:
        base = NOW - timedelta(minutes=30)
        stream(d, "0100-attempt1", [
            line_ts(base), line_rl(0.80), line_ts(base + timedelta(seconds=60))])
        stream(d, "0101-attempt1", [                      # LATER in time, LOWER value
            line_ts(base + timedelta(minutes=10)), line_rl(0.75),
            line_ts(base + timedelta(minutes=10, seconds=60))])
        r = ratelimit.read(directory=d, now=NOW)
        check("understated is flagged", r["understated"] is True)
        check("high_water is the higher one", r["high_water"] == 0.80, r.get("high_water"))
        text = ratelimit.render(r)
        check("the render says FLOOR out loud", "FLOOR" in text)
        check("and prints the higher number", "0.80" in text)

    print("\ntest_stale_is_declared")
    with tempfile.TemporaryDirectory() as d:
        base = NOW - timedelta(hours=3)
        stream(d, "0100-attempt1", [
            line_ts(base), line_rl(0.55), line_ts(base + timedelta(seconds=60))])
        r = ratelimit.read(directory=d, now=NOW, max_age_minutes=15)
        check("still a real reading", r["ok"] is True)
        check("stale is set", r["stale"] is True)
        check("age is reported in seconds", r["age_seconds"] > 3 * 3600 - 120)
        check("the render says STALE out loud", "STALE" in ratelimit.render(r))
        fresh = ratelimit.read(directory=d, now=NOW, max_age_minutes=600)
        check("and the threshold is what decides it", fresh["stale"] is False)

    print("\ntest_expired_window_is_unknown")
    with tempfile.TemporaryDirectory() as d:
        base = NOW - timedelta(days=2)
        stream(d, "0100-attempt1", [
            line_ts(base), line_rl(0.94, reset=RESET_PREVIOUS),
            line_ts(base + timedelta(seconds=60))])
        # NOW is past RESET_PREVIOUS: the window turned over and nothing has read the new one.
        after = datetime.fromtimestamp(RESET_PREVIOUS, timezone.utc) + timedelta(hours=1)
        r = ratelimit.read(directory=d, now=after)
        check("UNKNOWN, not the dead window's number", r["ok"] is False)
        check("the reason names the reset", "reset" in (r["reason"] or ""))
        check("0.94 appears nowhere in the output", "0.94" not in ratelimit.render(r))

    print("\ntest_unknown_when_there_is_nothing_to_read")
    with tempfile.TemporaryDirectory() as d:
        r = ratelimit.read(directory=str(Path(d) / "nope"), now=NOW)
        check("missing directory", r["ok"] is False and "does not exist" in r["reason"])

        r = ratelimit.read(directory=d, now=NOW)
        check("empty directory", r["ok"] is False and "no *.stream.jsonl" in r["reason"])

        f = Path(d) / "afile"
        f.write_text("x")
        r = ratelimit.read(directory=str(f), now=NOW)
        check("not a directory", r["ok"] is False and "not a directory" in r["reason"])

        stream(d, "0100-attempt1", [line_ts(NOW), line_ts(NOW + timedelta(seconds=1))])
        r = ratelimit.read(directory=d, now=NOW)
        check("streams but no seven-day reading",
              r["ok"] is False and "seven_day" in r["reason"], r.get("reason"))

    print("\ntest_unknown_when_the_streams_cannot_be_opened")
    if os.geteuid() == 0:
        print("  skip  running as root: chmod 000 does not deny root")
    else:
        with tempfile.TemporaryDirectory() as d:
            p = stream(d, "0100-attempt1", [line_ts(NOW), line_rl(0.61)])
            os.chmod(p, 0o000)
            try:
                r = ratelimit.read(directory=d, now=NOW)
                check("UNKNOWN, not a number from the files it could open", r["ok"] is False)
                check("the reason names the refusal", "refused to open" in (r["reason"] or ""),
                      r.get("reason"))
            finally:
                os.chmod(p, 0o644)

    print("\ntest_unknown_never_carries_a_number")
    # The structural one. Whatever the reason, an UNKNOWN render must not contain anything a
    # reader could mistake for a utilization -- no last-known value, no 0.0, no default.
    with tempfile.TemporaryDirectory() as d:
        for label, r in (
                ("missing", ratelimit.read(directory=str(Path(d) / "nope"), now=NOW)),
                ("empty", ratelimit.read(directory=d, now=NOW))):
            text = ratelimit.render(r)
            check(f"{label}: says UNKNOWN", "UNKNOWN" in text)
            check(f"{label}: prints no 0.x value",
                  not any(f"0.{n}" in text for n in range(10)), text)
            check(f"{label}: dict carries no utilization key", "utilization" not in r)

    print("\ntest_exit_codes")
    swarm = ENGINE / "bin" / "swarm"
    with tempfile.TemporaryDirectory() as d:
        # The one scene on the REAL clock, so its window comes off the real clock too. Task 0379.
        base = datetime.now(timezone.utc) - timedelta(minutes=1)
        live_reset = reset_ahead_of(base)
        stream(d, "0100-attempt1", [
            line_ts(base), line_rl(0.61, reset=live_reset),
            line_ts(base + timedelta(seconds=30))])
        print(f"      fixture: 1 stream, 1 seven-day reading at 0.61, window resets "
              f"{datetime.fromtimestamp(live_reset, timezone.utc).isoformat().replace('+00:00', 'Z')} "
              f"(derived from this run's clock, not a literal)")
        # THE PRECONDITION CONTROL, and it is the whole lesson of 0379. Without it, a fixture
        # whose window has expired produces four assertion failures that read like gauge
        # defects, and nobody looks at the fixture. With it, the ONE thing that is actually
        # wrong says so first, by name.
        check("the fixture's own window is still open, so a reading is possible at all",
              live_reset > datetime.now(timezone.utc).timestamp(),
              f"the fixture describes a window that reset at "
              f"{datetime.fromtimestamp(live_reset, timezone.utc)}, which is in the past. The "
              f"gauge is right to say UNKNOWN and the four checks below are about the fixture, "
              f"not about the gauge.")
        p = subprocess.run([sys.executable, str(swarm), "utilization", "--runs", d],
                           capture_output=True, text=True)
        check("a reading exits 0", p.returncode == 0, f"rc={p.returncode} {p.stderr[:200]}")
        check("and prints the value on stdout", "0.61" in p.stdout, p.stdout[:200])

        p = subprocess.run([sys.executable, str(swarm), "utilization", "--runs", d, "--json"],
                           capture_output=True, text=True)
        check("--json parses", (j := json.loads(p.stdout))["ok"] is True)
        check("--json carries the four required fields",
              all(k in j for k in ("utilization", "observed_at", "readings", "streams",
                                   "window_resets_at")))

    with tempfile.TemporaryDirectory() as d:
        p = subprocess.run([sys.executable, str(swarm), "utilization", "--runs", d],
                           capture_output=True, text=True)
        check("UNKNOWN exits 2", p.returncode == 2, f"rc={p.returncode}")
        check("and UNKNOWN goes to stderr, so a pipe reading stdout gets nothing",
              "UNKNOWN" in p.stderr and p.stdout.strip() == "",
              f"out={p.stdout[:80]!r}")

    print()
    # THE DENOMINATOR. This file used to print `all green` with no count at all, so a scene that
    # stopped executing -- an early return, a fixture that stopped building -- would have read
    # exactly like a clean run. Task 0382.
    if CHECKS == 0:                                       # DENOMINATOR
        print("0 comparisons made. A verdict over an empty set is not a pass.")
        return 2
    print(f"{CHECKS - len(FAILURES)} passed, {len(FAILURES)} failed")
    if FAILURES:
        print(f"FAILED {len(FAILURES)}: " + ", ".join(FAILURES))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
