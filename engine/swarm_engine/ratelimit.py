r"""Read the fleet's CURRENT seven-day rate-limit utilization off the run streams.

A GAUGE, NOT A GOVERNOR. Nothing in this module stops, pauses, throttles or rotates
anything. Where the ceiling sits is question q0276 and it is the operator's, unanswered as
this was written; a ceiling nobody can read is a ceiling nobody will hold, so this exists to
make the number readable and nothing more.

WHY THIS EXISTS AT ALL. `~/.claude` and `~/.claude-acct2` are ONE Anthropic account (same
`accountUuid`, measured on task 0261), so there is one meter and every agent walls together.
When it reaches 1.0 the operator loses his own Claude access until the window resets, which
makes the meter an operator-outage risk, not a build-throughput one. Before this, no surface
in the engine could read it: `grep -rn 'rate_limit\|utilization'` over `engine/bin/` and
`engine/swarm_engine/` returned nothing. Note that `budget` is a DIFFERENT meter -- it counts
dollars off the terminal `result` event's `total_cost_usd`. It knows nothing about this one.

THE METHOD IS TASK 0261'S, by agent T5, reimplemented here rather than re-derived. Its report
is `outputs/2026-08-19-T5-0261-rate-limit-burn/BURN.md` and the two traps it names are the two
a naive reader gets wrong:

* TIME. `rate_limit_event` lines carry NO timestamp of their own -- the whole line is
  `{type, rate_limit_info, uuid, session_id}`. So each event is BRACKETED between the nearest
  timestamped line before it and the nearest after it (`assistant` and `user` lines carry an
  ISO-8601 `timestamp`), and the midpoint is the point estimate. Both bounds are kept so the
  bracket is auditable. File mtime is NOT used: on a long-running attempt mtime is when the
  stream was last written, not when the event arrived.

* WINDOW. Readings carry `resetsAt`. A stream directory holds readings from PREVIOUS windows
  too -- 0261 found three at 0.97 and 0.98 from the window before -- and folding those into the
  current series manufactures a false spike. The current window is the greatest `resetsAt`
  present; everything older is counted separately and excluded, never silently dropped.

WHAT THIS ADDS TO 0261'S METHOD, and why. 0261 was a burn study and read what it read. A gauge
that a ceiling depends on has two more obligations:

* It may never understate. Utilization does not fall inside one window, so if any reading in
  the window is HIGHER than the latest one by time, the higher value is the floor and both are
  reported. Printing only the latest would under-report the meter, which is the one direction
  a ceiling cannot tolerate.

* It may never print a stale or invented number in place of an honest UNKNOWN. Every path that
  cannot produce a reading returns UNKNOWN with the reason attached. There is no default, no
  last-known value and no zero.

It does not compute a burn rate or predict a wall. That is 0261's arithmetic over a series;
this answers "where is the meter right now", in one read.

NO STORE. This reads files only. It does not import or connect to Postgres, so it still answers
when the store is down -- which is exactly when someone is most likely to be asking.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

# `swarm-run:82,87` derives the stream directory the same way, and it is the writer.
DEFAULT_ENGINE_HOME = "~/.brain-runtime"

# Only lines carrying one of these substrings are worth a `json.loads`. The streams run to
# ~156 MB across ~118 files on this fleet and only ~9% of lines carry a timestamp, so parsing
# every line is the difference between a sub-second read and one nobody will run twice.
_TS_HINT = '"timestamp"'
_RL_HINT = '"rate_limit_event"'

# Beyond this the printed value is reported as a FLOOR rather than as the current reading: the
# fleet has burned since and nothing wrote it down. Chosen because a busy terminal emits a
# reading every few minutes, so a quarter hour of silence means the fleet is idle or wedged.
DEFAULT_MAX_AGE_MINUTES = 15


def runs_dir(explicit=None) -> Path:
    if explicit:
        return Path(explicit).expanduser()
    home = os.environ.get("ENGINE_HOME") or DEFAULT_ENGINE_HOME
    return Path(home).expanduser() / "runs"


def _iso(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def _fmt(t):
    return t.isoformat(timespec="seconds").replace("+00:00", "Z") if t else ""


def scan(directory: Path):
    """Every seven-day reading in `directory`, bracketed in time. Reads, parses nothing else.

    Returns (readings, stats). A reading is a dict with prev_ts/next_ts/mid_ts (mid_ts is None
    when the event has no timestamped line on either side of it and therefore cannot be placed
    in time at all -- those are counted, not guessed at).
    """
    readings = []
    stats = {"files_seen": 0, "files_unreadable": 0, "torn_lines": 0}
    for f in sorted(directory.glob("*.stream.jsonl")):
        stats["files_seen"] += 1
        try:
            fh = f.open(errors="replace")
        except OSError:
            stats["files_unreadable"] += 1
            continue
        prev_ts, pending, sid = None, [], None
        with fh:
            for line in fh:
                has_ts = _TS_HINT in line
                if not has_ts and _RL_HINT not in line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    # A stream being written right now ends mid-line. Normal, not an error.
                    stats["torn_lines"] += 1
                    continue
                sid = o.get("session_id") or sid
                ts = _iso(o.get("timestamp")) if o.get("timestamp") else None
                if ts:
                    for ev in pending:                      # close the bracket
                        ev["next_ts"] = ts
                    pending = []
                    prev_ts = ts
                    continue
                if o.get("type") != "rate_limit_event":
                    continue
                info = o.get("rate_limit_info") or {}
                if info.get("rateLimitType") != "seven_day":
                    continue
                ev = {
                    "prev_ts": prev_ts,
                    "next_ts": None,
                    "utilization": info.get("utilization"),
                    "status": info.get("status"),
                    "overage": info.get("isUsingOverage"),
                    "resets_at": info.get("resetsAt"),
                    "session_id": o.get("session_id") or sid,
                    "file": str(f),
                    "stream": f.name.replace(".stream.jsonl", ""),
                }
                pending.append(ev)
                readings.append(ev)

    for r in readings:
        a, b = r["prev_ts"], r["next_ts"]
        r["mid_ts"] = a + (b - a) / 2 if a and b else (a or b)
    return readings, stats


def read(directory=None, max_age_minutes=DEFAULT_MAX_AGE_MINUTES, now=None):
    """The whole answer, as a dict. `ok` False means UNKNOWN and `reason` says why.

    UNKNOWN is a real answer here and is never substituted for. There is no last-known value
    to fall back to and 0.0 is a lie, so every failure path lands on `ok: False` with prose.
    """
    d = runs_dir(directory)
    now = now or datetime.now(timezone.utc)
    res = {"ok": False, "reason": None, "runs_dir": str(d), "read_at": _fmt(now)}

    if not d.exists():
        res["reason"] = f"{d} does not exist, so no run stream has ever been read from it"
        return res
    if not d.is_dir():
        res["reason"] = f"{d} exists but is not a directory"
        return res

    readings, stats = scan(d)
    res.update(stats)

    if stats["files_seen"] == 0:
        res["reason"] = f"no *.stream.jsonl files under {d}"
        return res
    if stats["files_unreadable"] == stats["files_seen"]:
        res["reason"] = (f"all {stats['files_seen']} stream files under {d} refused to open "
                         f"(permissions?)")
        return res
    if not readings:
        res["reason"] = (f"read {stats['files_seen']} stream files under {d} and none carried a "
                         f"seven_day rate_limit_event. The engine only emits one once the API "
                         f"has reported a limit on that session")
        return res

    # The current window is the furthest-out reset present. Anything older is a dead window:
    # 0261 found three readings at 0.97/0.98 from the window before this one, and folding those
    # into the current series would read as a spike that never happened.
    resets = {r["resets_at"] for r in readings if r["resets_at"]}
    if not resets:
        res["reason"] = (f"{len(readings)} seven-day readings carried no resetsAt, so which "
                         f"window they belong to cannot be established")
        return res
    current_reset = max(resets)
    reset_dt = datetime.fromtimestamp(current_reset, timezone.utc)

    window = [r for r in readings if r["resets_at"] == current_reset]
    res["readings_previous_windows"] = len(readings) - len(window)
    res["unplaced_readings"] = sum(1 for r in window if not r["mid_ts"])
    placed = [r for r in window if r["mid_ts"]]

    if not placed:
        res["reason"] = (f"all {len(window)} readings in the current window sit between no "
                         f"timestamped lines at all, so none can be placed in time")
        return res

    # The window this data describes has already turned over. The last number belongs to a
    # window that no longer exists and reporting it as current would be exactly the stale
    # number this command exists not to print.
    if now > reset_dt:
        res["reason"] = (f"the newest window in the streams reset at {_fmt(reset_dt)}, which is "
                         f"in the past. Every reading on disk belongs to an expired window; the "
                         f"meter has since reset and nothing has written a reading in the new one")
        res["window_resets_at"] = _fmt(reset_dt)
        return res

    placed.sort(key=lambda r: r["mid_ts"])
    latest = placed[-1]
    age = now - latest["mid_ts"]

    # Utilization does not fall inside a window. If some earlier-timestamped reading is higher
    # than the latest one, the higher value is the truth about where the meter stands, and
    # printing the lower one would understate it -- the one direction a ceiling cannot tolerate.
    high = max(placed, key=lambda r: (r["utilization"] if r["utilization"] is not None else -1))
    res.update({
        "ok": True,
        "utilization": latest["utilization"],
        "observed_at": _fmt(latest["mid_ts"]),
        "observed_bracket": [_fmt(latest["prev_ts"]), _fmt(latest["next_ts"])],
        "age_seconds": int(age.total_seconds()),
        "stale": age > timedelta(minutes=max_age_minutes),
        "max_age_minutes": max_age_minutes,
        "status": latest["status"],
        "is_overage": latest["overage"],
        "readings": len(placed),
        "streams": len({r["file"] for r in placed}),
        "window_resets_at": _fmt(reset_dt),
        "hours_to_reset": round((reset_dt - now).total_seconds() / 3600.0, 2),
        "source_stream": latest["stream"],
        "source_file": latest["file"],
        "high_water": high["utilization"],
        "high_water_at": _fmt(high["mid_ts"]),
    })
    res["understated"] = (res["high_water"] is not None
                          and res["utilization"] is not None
                          and res["high_water"] > res["utilization"])
    return res


BAR = "=" * 78


def render(r) -> str:
    """The human form. Short enough that an admiral pass reads it without scrolling."""
    if not r["ok"]:
        return "\n".join([
            BAR,
            "seven-day rate limit: UNKNOWN",
            BAR,
            f"reason: {r['reason']}",
            "",
            "This is NOT zero and NOT the last number you saw. Nothing was read, so there",
            "is no number here. Do not enforce a ceiling against this output.",
            f"looked in: {r['runs_dir']}",
        ])

    lines = [
        f"seven-day rate limit   {r['utilization']:.2f}  of 1.00   ({r['status']})",
        f"  observed at          {r['observed_at']}  ({r['age_seconds'] // 60}m "
        f"{r['age_seconds'] % 60}s ago)",
        f"  rests on             {r['readings']} readings in this window, from "
        f"{r['streams']} streams",
        f"  window resets        {r['window_resets_at']}  (in {r['hours_to_reset']:.1f} h)",
        f"  newest reading from  {r['source_stream']}",
    ]
    if r.get("readings_previous_windows"):
        lines.append(f"  excluded             {r['readings_previous_windows']} readings from "
                     f"earlier windows")
    if r.get("unplaced_readings"):
        lines.append(f"  excluded             {r['unplaced_readings']} readings that could not "
                     f"be placed in time")
    if r.get("is_overage"):
        lines.append("  OVERAGE              this reading reports the account is on overage")
    if r["understated"]:
        lines += ["", BAR,
                  f"FLOOR, NOT THE LATEST READING. A reading of {r['high_water']:.2f} at "
                  f"{r['high_water_at']} is higher",
                  "than the newest one. The meter does not fall inside a window, so treat "
                  f"{r['high_water']:.2f} as",
                  "where it actually stands and find out why the two disagree.", BAR]
    if r["stale"]:
        lines += ["", BAR,
                  f"STALE: this reading is {r['age_seconds'] // 60} minutes old, past the "
                  f"{r['max_age_minutes']}-minute mark.",
                  "The meter only rises inside a window, so the number above is a FLOOR, not the",
                  "current value. The fleet has burned since and nothing wrote it down.", BAR]
    return "\n".join(lines)
