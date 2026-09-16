"""The console at $BASE must not be the one attached to the live store. Task 0373.

WHY THIS FILE EXISTS RATHER THAN A LINE IN A RUNBOOK. `test_browser.py` and the suites that
import its harness assert against a UI served by a SEPARATE, LONG-LIVED PROCESS at `BASE`,
default `http://127.0.0.1:3103`. Every one of those suites already has a `BRAIN_PG_DB` guard, and
every one of those guards protects the database the TEST connects to. None of them can reach the
console's own `BRAIN_PG_DB`, which is a different process's environment.

MEASURED ON THIS HOST, 2026-08-23 AND AGAIN 2026-08-27, unchanged:

    ss -ltnp              ->  127.0.0.1:3103   python3   pid 11458
    /proc/11458/environ   ->  BRAIN_PG_DB=brain

So `test_browser.py`'s own documented run line, `BRAIN_PG_DB=brain_console python3 -m
web.tests.test_browser`, would today: pass the live-store guard (brain_console is not brain),
TRUNCATE brain_console, then drive real clicks into a console attached to `brain`, resolving
items and advancing the stack in the operator's live queue, and finally assert about what it saw
having reseeded a database nobody is looking at. A guard that is right about the thing it checks
and blind to the thing that matters.

THE CHECK IS ON THE SERVING PROCESS, NOT ON THE PORT. A port number is a convention and this one
has already been wrong once. `who_serves()` resolves the listener to a pid and reads
`/proc/<pid>/environ`, which is what the 2026-08-23 run did by hand.

AND IT REFUSES RATHER THAN WARNS. `_scratch_preflight.py` is the model: a suite that CAN be aimed
at the live bus by default eventually will be.

WHAT IT CANNOT DO, stated rather than discovered. `/proc` is Linux. On a host without it, or for
a console on another machine, the environment cannot be read from here and this module says
UNKNOWN and refuses anyway -- the safe direction, and the same choice `swarm_engine/liveness.py`
makes about a pid it cannot check. There is no mode in which an unverifiable console is accepted.
"""
from __future__ import annotations

import os
import re
import subprocess
import urllib.parse

LIVE_DB = "brain"
SHARED_SCRATCH = "brain_scratch"


def _port_of(base: str) -> int | None:
    try:
        u = urllib.parse.urlparse(base)
        return u.port or (443 if u.scheme == "https" else 80)
    except ValueError:
        return None


def _pid_listening_on(port: int) -> int | None:
    """The pid holding the listening socket, via `ss`. None when it cannot be established."""
    try:
        r = subprocess.run(["ss", "-ltnp"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    for line in r.stdout.splitlines():
        if f":{port} " not in line and not re.search(rf":{port}\b", line):
            continue
        m = re.search(r"pid=(\d+)", line)
        if m:
            return int(m.group(1))
    return None


def db_of_pid(pid: int) -> str | None:
    """`BRAIN_PG_DB` out of a running process's own environment, or None."""
    try:
        with open(f"/proc/{pid}/environ", "rb") as fh:
            raw = fh.read()
    except OSError:
        return None
    for item in raw.split(b"\0"):
        if item.startswith(b"BRAIN_PG_DB="):
            return item.split(b"=", 1)[1].decode("utf-8", "replace")
    # `store/schema.py` defaults BRAIN_PG_DB to `brain`, so a console started without the variable
    # IS on the live store. An absent variable is the dangerous answer, not the safe one.
    return LIVE_DB


def who_serves(base: str) -> dict:
    """Everything knowable about the console at `base`, with no verdict attached."""
    port = _port_of(base)
    pid = _pid_listening_on(port) if port else None
    return {"base": base, "port": port, "pid": pid,
            "db": db_of_pid(pid) if pid else None}


def refuse_unless_scratch(base: str, *, what: str = "this suite") -> dict:
    """Return the facts, or exit with a refusal that names them. The only entry point.

    Never returns on a console that is on `brain`, on the shared `brain_scratch`, or on one whose
    database cannot be established from here.
    """
    import sys
    w = who_serves(base)
    if w["pid"] is None:
        sys.exit(
            f"{what} refuses to run against {base}: nothing is listening there, or the listening "
            f"process could not be resolved with `ss -ltnp`. This suite drives WRITES through a "
            f"browser, so it will not aim at a console it cannot identify. Start one on a scratch "
            f"database: web/tests/run-all.sh does it for you.")
    if w["db"] in (LIVE_DB, None):
        sys.exit(
            f"{what} REFUSES to run against {base}.\n"
            f"  the console there is pid {w['pid']}, and its own BRAIN_PG_DB reads "
            f"{w['db'] or '(unreadable)'!r}.\n"
            f"  That is the LIVE STORE. This suite clicks real verbs: it would resolve items and\n"
            f"  advance the stack in the operator's queue. Setting BRAIN_PG_DB on THIS process\n"
            f"  does not change the database that console is attached to -- it is a different\n"
            f"  process, and its environment is fixed at exec.\n"
            f"  Start a second console on a scratch database and point BASE at it:\n"
            f"      web/tests/run-all.sh          (does exactly this, then tears it down)")
    if w["db"] == SHARED_SCRATCH:
        sys.exit(
            f"{what} refuses to run against {base}: the console there (pid {w['pid']}) is on "
            f"{SHARED_SCRATCH}, which every lane's suite reads and which this suite would "
            f"TRUNCATE and reseed. Name your own database.")
    return w
