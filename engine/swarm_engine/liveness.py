"""A live heartbeat is not a live agent. Task 0371.

WHAT WAS MEASURED BEFORE ANYTHING WAS CHANGED, because the row that asked for this said in its
own words that nothing in it had been measured at filing time.

`swarm status` and `swarm board` printed three things about each agent: the `status` word that
`heartbeat` last wrote, the task id that heartbeat last named, and `silent`, the seconds since
`brain.agent.updated`. **No surface performed a process check of any kind.** `reads.agents()`
already SELECTs `a.pid` and `a.host`, and nothing anywhere read either of them.

The live board at 2026-08-27T07:1xZ, unedited:

    D9R        terminal   working    on 0052  silent 248h37  STOPPED, no record of who or why

`working on 0052`, from a row whose pid is 4097838, and `/proc/4097838` does not exist. So the
one column that could have answered was in hand and unread, and the reader was left to do the
arithmetic on `silent 248h37` and decide for themselves.

THE WINDOWS, AND WHETHER THEY AGREE. `transitions.CLAIM_LIVENESS_SECONDS` is 900 and is what
`claim` refuses on, what `claim --explain` quotes, and what `doctor` reads through
`reads.withheld_by_liveness`. `reap` defaults `stale_seconds=900`. One more literal exists at
`transitions.py:1201`, spelled `interval '15 minutes'`, on the requeue-behind-a-live-claimer
refusal. Same number, three spellings, one of them not sharing the constant. They agree today and
nothing makes them agree tomorrow; this module imports the constant rather than adding a fourth.

WHAT A REAL LIVENESS TEST IS ON THIS PROGRAM, which is the question the row actually asked.

A pid on THIS host is checkable and nothing else is. An agent on another host cannot be checked
from here, and pretending otherwise is worse than saying UNKNOWN -- the precedent is task 0280's
utilization gauge, which reads the meter or says UNKNOWN and never a third thing. So:

    LIVE      a process check on this host found the pid, or the heartbeat is inside the window
              and there is nothing to contradict it. The evidence is named either way.
    DEAD      the heartbeat says working and the pid is GONE from this host. This is the only
              verdict that asserts absence, and it is only ever reached with a process check.
    STALE     the heartbeat has aged past the window and the pid IS still on this host. The
              process exists and has stopped reporting, which is a different problem from a
              crash and is not the same call to make.
    UNKNOWN   there is no way to answer from here: no pid recorded, or a row from another host.
              Never guessed, never rounded to LIVE or DEAD.
    STOPPED   `stopped_at` is set. A stop is a decision, not a liveness reading, and it is
              reported as itself.

WHAT THIS DOES NOT DO. It does not change `reap`, on the row's own instruction: `reap` acting on
a bad liveness answer is the consequence and the answer is the defect, and mixing the two makes
both unreviewable. It does not change `claim` either. It is a READ. Every write path behaves
exactly as it did.

ONE HONEST LIMIT, STATED HERE RATHER THAN DISCOVERED LATER. A pid on this host is checked by
existence alone, not by identity: pids are reused, so a recycled pid belonging to some unrelated
process reads as LIVE. That is the safe direction (it never manufactures a DEAD), and closing it
needs a start-time or a cmdline fingerprint on `brain.agent`, which is a migration and is not
this row's. `pid_recycling_unchecked` is reported on every verdict that rests on a pid, so no
reader has to remember this paragraph.
"""

from __future__ import annotations

import os

from .transitions import CLAIM_LIVENESS_SECONDS

#: What `os.uname().nodename` answers on the host this process is running on. An agent row whose
#: `host` is neither empty nor this string describes a machine this process cannot inspect.
def this_host() -> str:
    try:
        return os.uname().nodename
    except AttributeError:                                   # non-POSIX; there is no /proc either
        return ""


def _pid_is_running(pid: int) -> bool | None:
    """True, False, or None when this host cannot answer. Never an exception, never a guess."""
    if not pid or pid <= 0:
        return None
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # The process exists and belongs to another user. Existence is the question being asked.
        return True
    except (OSError, ValueError):
        return None


def _here_can_check(host: str, here: str) -> bool:
    """Is the row's host this host? An empty `host` means the row never said, so we may look."""
    return bool(here) and (not host or host == here)


def classify(a, now_window: int = CLAIM_LIVENESS_SECONDS) -> dict:
    """One agent row in, one verdict out, with the evidence that produced it.

    `a` is a `reads.agents()` row, or anything with the same keys. `.get()` throughout, per
    `docs/SCHEMA-TOLERANCE.md` rule 2: a caller replaying an older `--json` payload degrades to
    UNKNOWN rather than raising.
    """
    silent = a.get("silent")
    pid = a.get("pid")
    host = (a.get("host") or "").strip()
    here = this_host()
    fresh = silent is not None and silent < now_window

    # A STOP IS A DECISION, NOT A LIVENESS READING, so it is reported as itself. But the process
    # check still runs, because "stopped and its process is gone" and "stopped and its process is
    # still there" are different facts and only one of them means the stop took.
    #
    # AND THE CONTRADICTION IS NAMED. This is the case the row was filed on. D9R's row carries
    # `stopped_at`, `status = 'working'` and `work_item_id = '0052'` all at once, so the board
    # printed the words `working on 0052` and `STOPPED` on the SAME LINE and reconciled neither.
    # A reader scanning for who is working reads the first half.
    if a.get("stopped_at"):
        run = _pid_is_running(int(pid)) if (pid and _here_can_check(host, here)) else None
        bits = ["stopped_at is set"]
        if a.get("status") == "working" or a.get("work_item_id"):
            bits.append(f"but the heartbeat still says {a.get('status') or '?'}"
                        + (f" on {a['work_item_id']}" if a.get("work_item_id") else "")
                        + f", {silent}s ago")
        bits.append(f"pid {pid} is gone from {here}" if run is False else
                    f"pid {pid} is STILL RUNNING on {here}, so the stop did not reach the process"
                    if run is True else "no process check was possible from here")
        return {"state": "STOPPED", "silent": silent, "evidence": "; ".join(bits),
                "checkable": run is not None, "pid": pid,
                "pid_recycling_unchecked": run is True,
                "contradiction": bool(a.get("status") == "working" or a.get("work_item_id"))}

    # CAN THIS HOST ANSWER AT ALL? Answered before any verdict, so an UNKNOWN is a statement
    # about reach and not a fallback for everything that did not match a branch.
    if not pid:
        checkable, why = False, "no pid recorded on the agent row"
    elif host and here and host != here:
        checkable, why = False, f"the row names host {host!r} and this is {here!r}"
    elif not here:
        checkable, why = False, "this process cannot name its own host"
    else:
        checkable, why = True, ""

    if not checkable:
        # The heartbeat is the only evidence there is, and it is reported AS the heartbeat.
        return {"state": "LIVE" if fresh else "UNKNOWN", "silent": silent,
                "evidence": (f"heartbeat {silent}s old, inside the {now_window}s window; "
                             f"no process check possible ({why})") if fresh else
                            (f"heartbeat {silent}s old, past the {now_window}s window, and no "
                             f"process check is possible from here ({why}). Nothing was read, so "
                             f"there is no answer here -- this is not DEAD"),
                "checkable": False, "pid": pid, "pid_recycling_unchecked": False}

    running = _pid_is_running(int(pid))
    if running is None:
        return {"state": "UNKNOWN", "silent": silent,
                "evidence": f"pid {pid} could not be checked on this host",
                "checkable": False, "pid": pid, "pid_recycling_unchecked": False}
    if not running:
        # THE ONE VERDICT THAT ASSERTS ABSENCE, and the only one a process check produces.
        return {"state": "DEAD", "silent": silent,
                "evidence": f"pid {pid} is gone from {here}, heartbeat {silent}s old",
                "checkable": True, "pid": pid, "pid_recycling_unchecked": False}
    return {"state": "LIVE" if fresh else "STALE", "silent": silent,
            "evidence": (f"pid {pid} is running on {here}, heartbeat {silent}s old"
                         + ("" if fresh else f", past the {now_window}s window: the process is "
                                             f"there and has stopped reporting")),
            "checkable": True, "pid": pid, "pid_recycling_unchecked": True}


def note(a, now_window: int = CLAIM_LIVENESS_SECONDS) -> str:
    """The one-line form every surface prints, verdict and evidence on the same line."""
    v = classify(a, now_window)
    if v["state"] == "STOPPED":
        # `cli.stopped_note` already prints STOPPED with its provenance, so repeating the word
        # adds nothing. The CONTRADICTION is the part no other surface says, and it is the whole
        # reason this row exists, so it prints and the quiet case stays quiet.
        return f"  [{v['evidence']}]" if v.get("contradiction") else ""
    return f"  {v['state']} ({v['evidence']})"
