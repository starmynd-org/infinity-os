#!/usr/bin/env python3
"""A live heartbeat is not a live agent, and the surfaces now say which they know. Task 0371.

THE POSITIVE CONTROL THIS FILE IS BUILT AROUND, in the row's own words: "Kill an agent's process,
leave its heartbeat row intact, and require the surface to stop calling it live. A liveness check
that has never been shown failing against a dead agent has not been shown to work."

So scene 2 forks a real child, waits for it to exit, and hands `classify` a row that says
`working` with a FRESH heartbeat and that pid. Before task 0371 every surface in the engine would
have called that agent live, because every surface read the heartbeat and nothing read the pid.
`reads.agents()` had been selecting `a.pid` since migration 1 and no caller used it.

WHAT IS ASSERTED, AND WHY EACH ONE IS ITS OWN SCENE

  scene 1  the four reachable verdicts, from rows, hermetically
  scene 2  THE CONTROL: fresh heartbeat + dead pid must NOT read live
  scene 3  UNKNOWN is not DEAD. A row from another host, and a row with no pid, are both
           unanswerable from here, and the precedent (task 0280's gauge) is to say so rather
           than to round toward either answer
  scene 4  the STOPPED contradiction: `stopped_at` set while the heartbeat still says
           `working on <task>` is the exact row the operator was looking at when this was filed
  scene 5  ONE WINDOW. `claim`, `claim --explain`, `doctor` and `reap` all mean 900 seconds, and
           this module must import that constant rather than spell a fifth copy of it
  scene 6  the real CLI, end to end: `swarm status --json` carries the verdict, and `swarm status`
           prints a denominator saying how many verdicts a process check stands behind

NEEDS NO DATABASE except scene 6, which reads whatever store BRAIN_PG_DB names and writes
nothing. Scenes 1 to 5 are rows in, verdicts out.

Run: python3 engine/tests/test_liveness_vs_heartbeat.py
"""
from __future__ import annotations

import inspect
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENGINE = HERE.parent
ROOT = ENGINE.parent
for _p in (str(ROOT), str(ENGINE)):          # `store` is at the repo root, beside `engine/`
    if _p not in sys.path:
        sys.path.insert(0, _p)

from swarm_engine import liveness, transitions          # noqa: E402

PASS = FAIL = 0
FAILURES: list[str] = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok    {name}")
    else:
        FAIL += 1
        FAILURES.append(name)
        print(f"  FAIL  {name}")
        if detail:
            for line in str(detail).strip().splitlines()[:8]:
                print(f"        {line}")


def row(**kw):
    base = {"name": "X", "role": "terminal", "status": "working", "work_item_id": None,
            "pid": None, "host": "", "silent": 5, "stopped_at": None}
    base.update(kw)
    return base


def a_dead_pid() -> int:
    """A pid that certainly is not running: fork a child, reap it, return its number.

    Not a large made-up number. A number nobody ever used proves nothing about a check that
    is supposed to notice a process GOING AWAY, and on a busy host a made-up number can be live.
    """
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    for _ in range(50):                       # the pid must actually be reaped before we ask
        if liveness._pid_is_running(p.pid) is False:
            return p.pid
        time.sleep(0.02)
    return p.pid


def main():
    here = liveness.this_host()
    print(__doc__.splitlines()[0])
    print(f"\n  host {here!r}   window {liveness.CLAIM_LIVENESS_SECONDS}s   "
          f"this process is pid {os.getpid()}")

    dead = a_dead_pid()
    print(f"\nscene 1: the reachable verdicts")
    check("a fresh heartbeat with a RUNNING pid on this host is LIVE",
          liveness.classify(row(pid=os.getpid(), host=here, silent=5))["state"] == "LIVE")
    check("a pid still running with a heartbeat past the window is STALE, not LIVE and not DEAD",
          liveness.classify(row(pid=os.getpid(), host=here, silent=99999))["state"] == "STALE")
    check("and STALE says the process is there and has stopped reporting",
          "stopped reporting" in
          liveness.classify(row(pid=os.getpid(), host=here, silent=99999))["evidence"])
    v = liveness.classify(row(pid=dead, host=here, silent=99999))
    check("a gone pid with an old heartbeat is DEAD", v["state"] == "DEAD", v)
    check("and DEAD names the pid and the host it was checked on",
          str(dead) in v["evidence"] and here in v["evidence"], v["evidence"])

    print(f"\nscene 2: THE CONTROL -- a fresh heartbeat over a dead process")
    v = liveness.classify(row(pid=dead, host=here, silent=5, status="working",
                              work_item_id="0052"))
    check("a FRESH heartbeat does not make a dead process live", v["state"] != "LIVE", v)
    check("it reads DEAD, which is the only verdict a process check produces about absence",
          v["state"] == "DEAD", v)
    check("and it rests on a process check, not on arithmetic about the heartbeat",
          v["checkable"] is True, v)
    print(f"        pid {dead} was a real child of this test, waited on and reaped; "
          f"the row says status=working silent=5s")

    print(f"\nscene 3: UNKNOWN is not DEAD")
    v = liveness.classify(row(pid=dead, host="some-other-machine", silent=99999))
    check("a row from ANOTHER host is UNKNOWN even though the pid is not on this one",
          v["state"] == "UNKNOWN", v)
    check("and it says why it could not answer, naming both hosts",
          "some-other-machine" in v["evidence"], v["evidence"])
    check("and it says in words that this is not DEAD",
          "this is not DEAD" in v["evidence"], v["evidence"])
    check("and it does not claim to have checked anything", v["checkable"] is False, v)
    v = liveness.classify(row(pid=None, host=here, silent=99999))
    check("a row with NO pid is UNKNOWN, not DEAD", v["state"] == "UNKNOWN", v)
    check("and names the absent pid as the reason", "no pid recorded" in v["evidence"],
          v["evidence"])
    check("a row with no pid but a FRESH heartbeat is LIVE on the heartbeat, and says that is "
          "all it read",
          liveness.classify(row(pid=None, silent=5))["state"] == "LIVE"
          and "no process check possible" in liveness.classify(row(pid=None, silent=5))["evidence"])

    print(f"\nscene 4: the STOPPED contradiction, which is the row this was filed on")
    v = liveness.classify(row(pid=dead, host=here, silent=895044, status="working",
                              work_item_id="0052", stopped_at="2026-08-18 11:07:04+00:00"))
    check("a stopped agent is reported as STOPPED, not as DEAD -- a stop is a decision",
          v["state"] == "STOPPED", v)
    check("the contradiction is flagged", v.get("contradiction") is True, v)
    check("and it is spelled out: stopped_at set WHILE the heartbeat still says working on a task",
          "still says working on 0052" in v["evidence"], v["evidence"])
    check("and the process check still ran under it, so 'the stop did not take' is separable "
          "from 'the process is gone'", v["checkable"] is True, v)
    still = liveness.classify(row(pid=os.getpid(), host=here, silent=5, status="working",
                                  work_item_id="0052",
                                  stopped_at="2026-08-18 11:07:04+00:00"))
    check("and a stopped agent whose process is STILL RUNNING says the stop did not reach it",
          "the stop did not reach the process" in still["evidence"], still["evidence"])
    # `note` takes a ROW, not a verdict. Passing it a verdict dict the first time round made
    # this assertion read DEAD, which is worth leaving a line about: the two dicts share enough
    # keys (`pid`, `silent`) that the mistake produces a plausible answer instead of a crash.
    quiet_row = row(pid=dead, host=here, silent=99999, status="paused",
                    stopped_at="2026-08-18 11:07:04+00:00")
    check("a stopped agent with nothing contradictory prints nothing extra",
          liveness.note(quiet_row) == "", liveness.note(quiet_row))
    check("and it is still classified STOPPED rather than DEAD",
          liveness.classify(quiet_row)["state"] == "STOPPED", liveness.classify(quiet_row))

    print(f"\nscene 5: one window, imported and not respelled")
    check("liveness uses transitions.CLAIM_LIVENESS_SECONDS itself",
          liveness.CLAIM_LIVENESS_SECONDS is transitions.CLAIM_LIVENESS_SECONDS)
    # THE MODULE DOCSTRING IS EXCLUDED, AND SAYING SO IS THE POINT. It quotes the number three
    # times while EXPLAINING that there must be only one definition of it, and a scan that counted
    # prose would be red for the documentation being right. What is scanned is the code: every
    # line outside the docstring.
    src = inspect.getsource(liveness)
    code = "\n".join(src.splitlines()[len(liveness.__doc__.splitlines()) + 2:])
    literals = re.findall(r"(?<![\w.])900(?![\w.])", code)
    check(f"and spells no fresh copy of the number in its code (found {len(literals)} bare '900' "
          f"literal(s) over {len(code.splitlines())} code lines, docstring excluded)",
          not literals, literals)
    check("and the scan can see the code at all (positive control: the constant's name is there)",
          "CLAIM_LIVENESS_SECONDS" in code)

    print(f"\nscene 6: the real CLI carries it")
    # THIS SCENE SUPPLIES ITS OWN AGENT, AND MY FIRST VERSION DID NOT. Task 0382, and it is this
    # lane's own rule (docs/SUITE-INPUT-RULE.md) landing on this lane's own suite.
    #
    # It used to read whatever agents the store happened to hold, so from the runner -- which
    # truncates the scratch database before the suites -- it read `0 of 0` and went red. The red
    # said nothing about the CLI: it said the fixture was empty, which is a zero denominator
    # wearing a failure's clothes, and it is exactly the shape of `0379`. One `heartbeat` makes
    # the scene deterministic wherever it runs, and the assertion below still requires a verdict
    # on every row rather than accepting an empty roster.
    swarm = ENGINE / "bin" / "swarm"
    seeded = False
    try:
        import store                                                    # noqa: PLC0415
        store.apply("heartbeat", agent="L-liveness", status="working", role="terminal",
                    host=here or "test-host")
        seeded = True
        print(f"        seeded 1 agent row (L-liveness) so this scene does not depend on what "
              f"the store happened to hold")
    except Exception as exc:                                            # noqa: BLE001
        print(f"        could not seed an agent row: {type(exc).__name__}: {exc}")
    p = subprocess.run([sys.executable, str(swarm), "status", "--json"],
                       capture_output=True, text=True)
    if p.returncode != 0:
        check("`swarm status --json` ran", False, p.stderr[:400])
    else:
        j = json.loads(p.stdout)
        check("`swarm status --json` carries the window it used",
              j.get("liveness_window_seconds") == liveness.CLAIM_LIVENESS_SECONDS, j.get("liveness_window_seconds"))
        check("and the host the process check was made on",
              j.get("liveness_host") == here, j.get("liveness_host"))
        ags = j.get("agents") or []
        with_v = [a for a in ags if a.get("liveness")]
        if not ags and not seeded:
            # NOT RUN, not FAIL: the fixture could not be written, so there is nothing to carry a
            # verdict and this scene has no opinion about the CLI.
            print(f"  NOT RUN  every agent row carries a verdict")
            print(f"           condition: 0 agent rows in {os.environ.get('BRAIN_PG_DB')!r} and "
                  f"this process could not write one.")
            print(f"           remedy:    point BRAIN_PG_DB at a scratch database this process "
                  f"can write to.")
        else:
            check(f"and every agent row carries a verdict ({len(with_v)} of {len(ags)})",
                  len(ags) > 0 and len(with_v) == len(ags), f"{len(with_v)} of {len(ags)}")
        states = {a["liveness"]["state"] for a in with_v}
        check("and every verdict is one of the five words, never a sixth",
              states <= {"LIVE", "DEAD", "STALE", "UNKNOWN", "STOPPED"}, states)
    p = subprocess.run([sys.executable, str(swarm), "status"], capture_output=True, text=True)
    m = re.search(r"liveness: (\d+) of (\d+) agent\(s\) process-checked on (\S+), window (\d+)s",
                  p.stdout)
    check("`swarm status` prints the liveness denominator on its own line", m is not None,
          p.stdout[-400:])
    if m:
        print(f"        {m.group(0)}")
        check("and the denominator is not zero-over-zero silently reported as a fleet",
              int(m.group(2)) > 0 or "nothing was compared" in p.stdout, p.stdout[-200:])

    print()
    if PASS + FAIL == 0:                                  # DENOMINATOR
        print("0 comparisons made. A verdict over an empty set is not a pass.")
        return 2
    print(f"{PASS} passed, {FAIL} failed")
    if FAILURES:
        print("FAILED: " + ", ".join(FAILURES))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
