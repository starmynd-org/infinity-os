"""The Sessions room's reader. Proof that terminal conversations are logged, with denominators.

WHY THIS EXISTS. Asked on 2026-08-28 what one thing would make him run his real day through this,
the operator answered the terminal, a wrapper on it, and "proof that all terminal conversations are
being logged properly. That was a key part of the product that's not being tracked." Measured the
same day: it IS tracked, 1679 sessions and 1634 transcripts, and the console said so in ONE LINE on
Study (`study.html:18`, fed by `model.py:862`), which counts rows in `brain.transcript`.

**Every number on that line is a count of rows that exist**, so the 92 files on disk the index had
never seen could not appear on it at any value. The screen could not show the gap in the thing it
was reporting. That is bus row `0424`, and this module is its fix.

WHAT THIS MODULE IS. **A reader, and only a reader.** It calls no verb, writes no row and touches
no file. `ROOM_VERBS["sessions"]` is an empty frozenset and `web/tests/test_allowlist.py` proves
every registered verb in the runtime is refused from this room before `store.apply` is entered,
which is the same enforcement Study has and the reason MUST-NOT-BUILD item 6 permits this room at
all.

IT SHELLS OUT ON PURPOSE. The numbers come from `ingest/bin/ingest coverage`, as a subprocess,
rather than from SQL written here. The ingest CLI's own docstring states the rule this follows:
"subprocess vs in-process is a performance decision, one implementation is not." A second copy of
the disk-walk logic in `web/` would be a second answer to the question this page exists to answer
honestly, which is the defect, not the fix. Measured 470ms for the whole report including the walk,
which is why a page load can afford it.

THREE TRAPS, and all three shape the code below.

1. **`on_disk_not_indexed` HAS A FLOOR AND THE FLOOR IS NOT A GAP.** `ingest/ingest/queries.py`
   states it outright: the hook indexes a transcript at `SessionEnd`, so every session open RIGHT
   NOW is a file on disk with no `transcript` row and is counted here. A surface that renders 92
   as "92 missing" replaces a screen that under-reported with a screen that over-reports, and the
   second is worse because it looks like diligence. The floor is rendered as prose beside the
   number, every time, and the number is never painted as damage.

2. **A refused report is not a zero report.** When the projects root is not a directory on this
   host, `queries.filesystem` withholds the counts and names the reason rather than reporting
   every pointer as dead. This module propagates that refusal to the screen verbatim. Rendering
   `0` where the answer is `unknown` is the exact class of lie the room was built against.

3. **A VERIFIED COUNT WITH NO DATE IS A LIE IN THE TIME DIMENSION, and this module shipped with
   that defect on 2026-08-28 before catching it.** The first version rendered `verified 1292 of
   1634` and called the remainder "waiting", on a stated premise that the daily sweep was healthy.
   The premise came from `systemctl --user show`, which answered `Result=success,
   ExecMainStatus=0`. **That answer was empty, not true**: the WSL user manager had restarted that
   day, and a restarted manager holds no record of a unit that never ran this boot, so it reports
   success for a unit it has never seen. `journalctl` held the truth. The sweep had crashed on
   every run and `max(verified_at)` was TEN DAYS old. So the room built to stop a numerator being
   read without its denominator rendered one itself, in a dimension nobody was looking at. The
   sweep's own freshness is now read from the store and rendered beside the count, and a count
   older than two dailies is DAMAGE rather than waiting.

4. **The subprocess is timed out and the timeout is a rendered state.** Row `0417` measured
   `bin/try-to-book-the-receipt` blocking for minutes on 9p IO with no output, and the lesson is
   that an operator-facing surface must never inherit an unbounded wait. `~/.claude/projects` is
   on the WSL filesystem rather than `/mnt/c` so the walk is fast today, but a page that hangs
   because a mount went slow is a worse failure than a page that says the report timed out.
"""

from __future__ import annotations

import json
import os
import subprocess
import datetime
from pathlib import Path
from typing import Any

import store

#: Ten seconds. The measured cost is 470ms, so this is roughly twenty times headroom and still far
#: below the point where a person decides the page is broken. See trap 3.
TIMEOUT_S = 10

_CLI = Path(__file__).resolve().parent.parent / "ingest" / "bin" / "ingest"


def _run_coverage() -> tuple[dict[str, Any] | None, str | None]:
    """Return (report, error). Exactly one is None. Never raises."""
    if not _CLI.exists():
        return None, (f"{_CLI} does not exist, so coverage cannot be read from here. "
                      f"No number is shown rather than a number that means nothing.")
    try:
        p = subprocess.run([str(_CLI), "coverage"], capture_output=True, text=True,
                           timeout=TIMEOUT_S, env=os.environ.copy())
    except subprocess.TimeoutExpired:
        return None, (f"`ingest coverage` did not return within {TIMEOUT_S}s. The disk walk over "
                      f"the projects root is the slow half; a stalled mount is the usual cause. "
                      f"Counts withheld rather than shown stale.")
    except OSError as e:
        return None, f"could not run `ingest coverage`: {e}"
    if p.returncode != 0:
        tail = (p.stderr or p.stdout or "").strip().splitlines()
        return None, ("`ingest coverage` exited %d: %s" %
                      (p.returncode, tail[-1] if tail else "no output"))
    try:
        return json.loads(p.stdout), None
    except json.JSONDecodeError as e:
        return None, f"`ingest coverage` did not return JSON: {e}"


def _n(d: Any, *path, default=0):
    """Walk a nested dict, returning `default` for any missing hop.

    Deliberately tolerant on read. The two ingest profiles do not carry the same facts
    (`ingest/ingest/profiles.py` enumerates the difference), so a key absent under one profile is
    a normal condition and not an error. Tolerant on read, strict on write, which is the posture
    the signal vocabulary already uses.
    """
    cur = d
    for k in path:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur if cur is not None else default


#: Two dailies. The sweep runs once a day, so one missed run is a machine that was asleep and two
#: is a mechanism that is not working. Chosen rather than measured, and stated so it can be argued.
STALE_AFTER_H = 48


def _sweep() -> dict[str, Any]:
    """When did the whole-corpus re-hash last actually land? Read from the store, not from systemd.

    THE INSTRUMENT MATTERS AND IT IS THE POINT OF THIS FUNCTION. `systemctl --user is-failed`
    and `systemctl --user show` report on units the CURRENT user manager has run. After the
    manager restarts they answer `inactive` and `Result=success` for a unit that has never run and
    for one that crashed before the restart, identically. `list-timers` keeps showing a LAST time
    because that is read from a persistent stamp file on disk, which makes the pair look
    corroborating when only one of them holds data. The store cannot lie in that direction: a
    `verified_at` is written only by a sweep that got far enough to write it.
    """
    try:
        with store.read() as s:
            last = s.scalar("SELECT max(verified_at) FROM brain.transcript")
    except Exception as e:                                              # noqa: BLE001
        return {"known": False, "why": f"could not read verified_at: {e}"}
    if last is None:
        return {"known": True, "last": None, "hours": None, "stale": True,
                "state": "dmg", "word": "never run"}
    now = datetime.datetime.now(datetime.timezone.utc)
    hours = (now - last).total_seconds() / 3600.0
    stale = hours > STALE_AFTER_H
    return {"known": True, "last": last, "hours": round(hours, 1), "stale": stale,
            "state": "dmg" if stale else "rest",
            "word": "not running" if stale else "current"}


#: How many sessions the room OFFERS to open. Twelve, and the number is a rendering decision
#: rather than a fact about the corpus, which is why the denominator is printed beside it every
#: time. This is not a page one of a hundred and forty seven: there is no second page and the
#: room does not pretend there is. It is a door, sized so that the newest day of work is behind
#: it, standing next to a count that until now had no door at all.
RECENT_N = 12


def _console_db() -> str:
    """The database THIS console is attached to, spelled the way `store.session.dsn` spells it."""
    return os.environ.get("BRAIN_PG_DB", "brain")


def _ingest_db() -> str:
    """The database `ingest coverage` connects to, spelled the way `ingest/ingest/config.py` does.

    THE TWO ARE DIFFERENT VARIABLES AND NOBODY SETS THE SECOND. Measured 2026-08-29 on a console
    serving a seeded scratch store: this room printed `sessions 1764` while the store behind the
    console held 14 session rows, because `_run_coverage` passes `os.environ.copy()` to a CLI that
    reads `BRAIN_DB` and defaults it to `brain`, the LIVE store, while the console reads
    `BRAIN_PG_DB`. On the operator's own console the two resolve to the same name and nothing is
    visible; on every scratch, demo and preview console they do not, and this room then reports
    another database's corpus as its own.

    It is REPORTED here and not repaired here. Making the subprocess follow the console would
    change what the room shows on every non-live console, which is a decision about what this
    proof surface is for, and lane D2 does not take it while the operator is asleep. It is written
    up at `outputs/2026-08-29-commander/lane-D2-decisions.md` D-D2-4 with the one-line patch.
    """
    return os.environ.get("BRAIN_DB", "brain")


def _recent(limit: int = RECENT_N) -> dict[str, Any]:
    """The newest sessions, so that the count on this page has something to open.

    WHY A READER ON A ROOM THAT SHELLS OUT FOR ITS NUMBERS. The coverage report is the disk walk
    and it is deliberately a report of TOTALS: `ingest coverage` returns counts, not identities,
    and adding identities to it would change a proof instrument to serve a rendering. This is the
    other half and it is a different question. The numbers above say how much is logged; these
    twelve rows say `here is one, open it`, which is the half the operator asked for when he asked
    for proof that his terminal conversations are being logged.

    THE MEASUREMENT THAT MADE THIS A DEFECT, 2026-08-29: the room reported `sessions 1763` and
    `transcripts 1748` and its whole set of distinct hrefs was the nine room tabs, `/`, `?deep=1`
    and two static files. `/session/<sid>` is a real route that renders the row, its transcripts
    and its subagents, and **0 of 1763 sessions were reachable by any click**. A count is proof
    that a number exists. Opening one is the proof.

    IT FAILS CLOSED AND IT SAYS SO. Trap 2 of this module is that a refused report is not a zero
    report, and it applies to this read as much as to the walk: on any error the list is empty and
    the reason is carried to the screen rather than rendering as `no sessions`.
    """
    try:
        with store.read() as s:
            rows = s.query(
                "SELECT id, role, agent, harness, workdir, started_at, ended_at "
                "  FROM brain.session ORDER BY started_at DESC NULLS LAST LIMIT %s", (limit,))
            total = s.scalar("SELECT count(*) FROM brain.session")
    except Exception as e:                                              # noqa: BLE001
        return {"rows": [], "total": None, "db": _console_db(), "ingest_db": _ingest_db(),
                "why": f"the session index could not be read: {e}"}
    return {"rows": rows, "total": total, "db": _console_db(), "ingest_db": _ingest_db(),
            "why": None}


def feed() -> dict[str, Any]:
    """The whole room, in one dict. Shapes numbers; decides nothing."""
    rep, err = _run_coverage()
    if err:
        return {"refused": err, "rows": [], "fs": {}, "report": None}

    fs = _n(rep, "filesystem", default={})

    # A refusal from inside the report itself. Trap 2: propagate it, do not flatten it to zeros.
    if fs.get("refused"):
        return {"refused": fs["refused"], "rows": [], "fs": fs, "report": rep}

    on_disk = _n(fs, "files_on_disk")
    indexed_n = _n(fs, "indexed", "n")
    missing_n = _n(fs, "on_disk_not_indexed", "n")

    verify = _n(rep, "verify_state", default={})
    ok = verify.get("ok", 0)
    not_yet = verify.get("not-yet-verified", 0)
    gone = verify.get("checked; file gone (see pointers_absent)", 0)
    pointers = _n(rep, "transcripts_total", "files")

    sweep = _sweep()
    # `indexed_not_on_disk` is the DISK's answer; `pointers_absent` is the CLASSIFIER's. They are
    # not the same fact and the gap between them is the sweep's backlog, which is why it went
    # unseen: the first version of this room rendered only the classified half.
    dead_total = _n(fs, "indexed_not_on_disk", "n")
    unclassified = _n(fs, "indexed_not_on_disk", "not_yet_recorded")

    absent_by = _n(rep, "pointers_absent", "open_by_classification", default={})
    unexplained = absent_by.get("unexplained", 0)
    aged_out = absent_by.get("aged-out", 0)
    unknown_age = absent_by.get("unknown-age", 0)

    orphans_open = _n(rep, "transcript_orphans", "open")
    outbox_failed = _n(rep, "event_outbox", "failed")
    outbox_emitted = _n(rep, "event_outbox", "emitted")

    # ---------------------------------------------------------------- the state of each number
    #
    # RED IS DAMAGE AND NOTHING ELSE, shell wide (MUST-NOT-BUILD item 7). Three things here are
    # damage: a pointer whose file vanished with no explanation, a transcript whose indexing
    # transaction half-landed, and an event that failed to emit. Everything else is waiting or
    # resting, and the two that look alarming are deliberately NOT red:
    #
    #   `on_disk_not_indexed`   has a floor of live sessions (trap 1). Amber, waiting.
    #   `not-yet-verified`      the timer is healthy and walking a backlog. Amber, waiting.
    #   `aged-out`              absences the classifier EXPLAINED. Grey, resting. An explained
    #                           absence is the system working, and painting it red would spend
    #                           the damage colour on the classifier's own success.
    #
    # STATE NEVER RIDES COLOUR ALONE. Every row carries `word`, rendered beside the number.
    def state(n, bad="broken", good="clean", warn=None):
        if n:
            return ("dmg", bad) if warn is None else (warn[0], warn[1])
        return ("rest", good)

    rows = [
        {"k": "indexed", "n": indexed_n, "of": on_disk,
         "pct": _n(fs, "indexed", "pct", default=None),
         "state": "rest" if on_disk and indexed_n == on_disk else "wait",
         "word": "complete" if on_disk and indexed_n == on_disk else "incomplete",
         "note": "Denominator is the DISK WALK, not the store, so a rolled-back index cannot "
                 "hide in it."},
        {"k": "on disk, not indexed", "n": missing_n, "of": on_disk,
         "state": "wait" if missing_n else "rest",
         "word": "waiting" if missing_n else "none",
         "note": "HAS A FLOOR, AND THE FLOOR IS NOT A GAP. The hook indexes at SessionEnd, so "
                 "every session open right now is counted here. Expect roughly one per open "
                 "terminal. Not damage, and not a to-do list."},
        {"k": "verified", "n": ok, "of": pointers,
         "pct": round(100.0 * ok / pointers, 1) if pointers else None,
         "state": "dmg" if sweep.get("stale") else ("rest" if pointers and ok == pointers
                                                    else "wait"),
         "word": "STALE" if sweep.get("stale") else
                 ("complete" if pointers and ok == pointers else "partial"),
         "note": ("AS OF %s, which is %s hours ago. The sweep is not running, so this number is "
                  "a historical fact and not a current one." % (sweep.get("last"),
                                                                sweep.get("hours"))
                  ) if sweep.get("stale") else
                 "Whole-corpus re-hash, daily. This is the number that means the bytes on disk "
                 "are still the bytes that were indexed."},
        {"k": "never verified", "n": not_yet, "of": pointers,
         "state": ("dmg" if sweep.get("stale") else "wait") if not_yet else "rest",
         "word": ("not draining" if sweep.get("stale") else "waiting") if not_yet else "none",
         "note": "Indexed, never re-hashed. A backlog is only a backlog if something is draining "
                 "it. WHEN THE SWEEP IS DOWN THIS IS NOT WAITING, IT IS STOPPED, and calling it "
                 "waiting is how it sat unseen."},
        {"k": "absent, explained", "n": aged_out + unknown_age, "of": pointers,
         "state": "rest", "word": "explained",
         "note": "Files the disk no longer has and the classifier accounted for. An explained "
                 "absence is the system working."},
        {"k": "absent, UNCLASSIFIED", "n": unclassified, "of": dead_total,
         "state": "dmg" if unclassified else "rest",
         "word": "unknown" if unclassified else "none",
         "note": "The disk says these pointers' files are gone and the classifier has never "
                 "walked up to them, so nobody knows whether any of it was an early deletion. "
                 "NOT the same fact as `absent, explained`: that one is the classifier's verdict, "
                 "this one is the absence of a verdict. This row did not exist in the first "
                 "version of this page and its absence is what let the sweep failure hide."},
        {"k": "absent, UNEXPLAINED", "n": unexplained, "of": pointers,
         "state": "dmg" if unexplained else "rest",
         "word": "broken" if unexplained else "none",
         "note": "A pointer whose file vanished with no reason found. This is the one number "
                 "here that is damage."},
        {"k": "orphaned transcripts", "n": orphans_open, "of": pointers,
         "state": "dmg" if orphans_open else "rest",
         "word": "broken" if orphans_open else "none",
         "note": "The indexing transaction survived and its session key did not. Distinct from "
                 "`on disk, not indexed`, where nothing survived."},
        {"k": "events failed to emit", "n": outbox_failed, "of": outbox_failed + outbox_emitted,
         "state": "dmg" if outbox_failed else "rest",
         "word": "broken" if outbox_failed else "none",
         "note": "Diagnose before requeueing. A requeue that succeeds without explaining the "
                 "original failure converts a known defect into an unknown one."},
    ]

    return {
        "refused": None,
        "sweep": sweep,
        "report": rep,
        "fs": fs,
        "rows": rows,
        "root": fs.get("projects_root"),
        "host": fs.get("host_id"),
        "sessions_total": _n(rep, "sessions_total"),
        "by_role": _n(rep, "sessions_by_role", default={}),
        "by_month": _n(rep, "sessions_by_month", default={}),
        "by_harness": _n(rep, "by_harness", default={}),
        "mib": _n(rep, "transcripts_total", "mib"),
        "pointers": pointers,
        "sample": _n(fs, "on_disk_not_indexed", "sample", default=[]),
        "sample_of": _n(fs, "on_disk_not_indexed", "sample_of"),
        "ended_observed": _n(rep, "coverage", "ended_at_observed", default={}),
        "linked": _n(rep, "transcripts_linked_to_a_session", default={}),
        # The door. See `_recent`: the counts above are the walk's, these rows are the store's,
        # and they are the only thing on this page a person can open.
        "recent": _recent(),
        "recent_n": RECENT_N,
    }
