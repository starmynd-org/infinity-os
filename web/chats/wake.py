"""The wake mechanism: block until work arrives, without a poll and without a port.

ANDREW'S OBJECTION, AND WHAT IT IS ACTUALLY ABOUT

> *"constantly polling the bus ... deterministic that doesn't waste tokens ... or a webhook, or
> always listening"*

**The cost he is naming is TOKENS, and tokens are spent per AGENT TURN, not per filesystem stat.**
An agent that wakes every thirty seconds to find an empty queue has spent a turn's context on
nothing, ninety times an hour. **So the fix is not a faster poll; it is that the waiting happens
inside ONE call the agent is already blocked on.** Any implementation with that shape solves the
cost problem, including a polling one.

**Which is exactly why this file is careful about what it CLAIMS.** The Admiral's `145932Z`
broadcast, about the fleet's own timer: *"`bus-wait.sh` is a polling loop that a seat blocks ON,
not an event listener. Do not describe it as event-driven in a receipt."* **So `wait_for_message`
returns the mechanism it actually used, every time, and callers print it.** A receipt that says
"event-driven" when the process was sleeping in a loop is a lie whether or not it changes the
latency, and this operation has paid for exactly that class of sentence.

WHAT IS ACTUALLY IMPLEMENTED, MEASURED PER PLATFORM RATHER THAN ASSUMED

- **Windows: a real kernel wait.** `FindFirstChangeNotificationW` on the mailbox directory with
  `FILE_NOTIFY_CHANGE_FILE_NAME`, then `WaitForSingleObject`. `protocol.place` finishes with
  `os.replace`, which is a rename into the directory, which is precisely what that filter fires on.
  The process is not scheduled at all between messages. `mechanism` reads `win32-change-notify`.
- **Everything else: an adaptive poll**, and `mechanism` reads `poll`. **This is a NAMED GAP, not a
  silent fallback:** an `inotify` path is not written, this box is Windows, and writing a Linux
  path I cannot exercise here would be a green neighbour rather than a check.

THE RACE, AND WHY THE ORDER IN `wait_for_message` IS THE ORDER IT IS

Arm the watch, THEN scan, THEN block. A message that lands between an initial scan and an
arming would be invisible until the next one -- the classic lost-wakeup, and on a queue it presents
as an agent that hangs with work sitting in front of it. Arming first makes that window empty:
anything that arrives after the arm sets the handle, whether or not the scan saw it.

WHAT THIS DOES NOT DO, AND IT IS NOT A PROMISE, IT IS A PROPERTY

**It opens no socket and binds no port.** Its argument is a directory. *"Reachable from off this
host" is not a property a directory watch can have*, which is a stronger statement than a
prohibition against exposing one, because there is nothing here to expose. `MUST-NOT-BUILD.md` item
11's list -- tunnel, reverse proxy, Funnel, public exposure, cache layer, service worker, PWA --
is satisfied by construction rather than by discipline.
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass

from . import protocol

#: `FILE_NOTIFY_CHANGE_FILE_NAME`. Creation, deletion and renaming of a file in the directory.
#: A rename INTO the directory is what `os.replace` performs, so this is the exact filter.
_FILE_NAME = 0x00000001

_WAIT_OBJECT_0 = 0x00000000
_WAIT_TIMEOUT = 0x00000102
_INVALID_HANDLE = -1

#: Poll interval for the fallback. Deliberately short, because on the fallback the LATENCY is the
#: whole cost and it is paid by a sleeping process, not by an agent's context window.
_POLL_SECONDS = 0.25


@dataclass
class Woken:
    """What the wait returned. **`mechanism` is a field, not a docstring claim.**

    `reason` is one of `message`, `timeout`, `stuck`. `stuck` means files are present and none is
    deliverable -- the loud-empty case, which a naive timer reports as an empty queue and which is
    the instrument failure this operation keeps paying for.
    """

    mechanism: str
    reason: str
    scan: protocol.Scan
    waited_seconds: float

    @property
    def messages(self) -> list[protocol.Message]:
        return self.scan.delivered

    def receipt(self) -> str:
        """One line, honest about which mechanism ran. This is what a caller prints."""
        note = self.scan.loud_empty
        head = (f"waited {self.waited_seconds:.1f}s on {self.mechanism}: {self.reason}, "
                f"{len(self.scan.delivered)} message(s) deliverable")
        return f"{head}. {note}" if note else head


def mechanism_available() -> str:
    """Which mechanism THIS process would use, without waiting. Report it beside any latency."""
    if sys.platform == "win32" and _kernel32() is not None:
        return "win32-change-notify"
    return "poll"


def _kernel32():
    """`kernel32` with the three calls bound, or `None`. Bound lazily so import stays portable."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return None
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.FindFirstChangeNotificationW.argtypes = [wintypes.LPCWSTR, wintypes.BOOL,
                                                     wintypes.DWORD]
        k32.FindFirstChangeNotificationW.restype = wintypes.HANDLE
        k32.FindCloseChangeNotification.argtypes = [wintypes.HANDLE]
        k32.FindCloseChangeNotification.restype = wintypes.BOOL
        k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        k32.WaitForSingleObject.restype = wintypes.DWORD
    except Exception:
        return None
    return k32


def wait_for_message(directory: str, *, timeout_seconds: float = 600.0) -> Woken:
    """Block until a complete message is in `directory`, or the timeout expires.

    **mutatesState: NO.** It reads; it moves nothing; draining is `protocol.mark_done`, which the
    caller does after acting. A wait that consumed what it found would be a fixture, and a caller
    that crashed between waking and acting would have lost the message.

    Returns a `Woken` whose `mechanism` field says which implementation actually ran. It never
    claims to be event-driven when it polled.
    """
    os.makedirs(directory, exist_ok=True)
    started = time.monotonic()
    k32 = _kernel32()

    def _elapsed() -> float:
        return time.monotonic() - started

    def _result(mech: str, scan: protocol.Scan) -> Woken | None:
        if scan.delivered:
            return Woken(mech, "message", scan, _elapsed())
        return None

    if k32 is None:
        while True:
            found = protocol.scan(directory)
            done = _result("poll", found)
            if done:
                return done
            if _elapsed() >= timeout_seconds:
                return Woken("poll", "stuck" if found.loud_empty else "timeout", found, _elapsed())
            time.sleep(min(_POLL_SECONDS, max(0.0, timeout_seconds - _elapsed())))

    handle = k32.FindFirstChangeNotificationW(directory, False, _FILE_NAME)
    if handle in (0, _INVALID_HANDLE, None) or handle == (2 ** 64 - 1) or handle == (2 ** 32 - 1):
        # Refused by name rather than degrading silently into a poll that calls itself an event
        # wait. A caller that gets `poll` back knows it, because the field says so.
        found = protocol.scan(directory)
        return Woken("poll", "message" if found.delivered else "timeout", found, _elapsed())
    try:
        while True:
            # ARM (done, above and by FindNextChangeNotification below) BEFORE SCAN. See the
            # module docstring: the other order loses a message that lands in between.
            found = protocol.scan(directory)
            done = _result("win32-change-notify", found)
            if done:
                return done
            remaining = timeout_seconds - _elapsed()
            if remaining <= 0:
                return Woken("win32-change-notify",
                             "stuck" if found.loud_empty else "timeout", found, _elapsed())
            status = k32.WaitForSingleObject(handle, int(min(remaining, 4_000_000) * 1000))
            if status == _WAIT_TIMEOUT:
                found = protocol.scan(directory)
                if found.delivered:
                    return Woken("win32-change-notify", "message", found, _elapsed())
                return Woken("win32-change-notify",
                             "stuck" if found.loud_empty else "timeout", found, _elapsed())
            if status != _WAIT_OBJECT_0:
                found = protocol.scan(directory)
                return Woken("win32-change-notify",
                             "message" if found.delivered else "timeout", found, _elapsed())
            if not k32.FindNextChangeNotification(handle):
                found = protocol.scan(directory)
                return Woken("win32-change-notify",
                             "message" if found.delivered else "timeout", found, _elapsed())
    finally:
        k32.FindCloseChangeNotification(handle)
