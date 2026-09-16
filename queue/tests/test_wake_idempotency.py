#!/usr/bin/env python3
"""One wake, one thread note, one woke_by, however many wakers arrive at once. Task 0382.

THE RESIDUAL TASK 0377 LEFT OPEN, AND ITS OWN INSTRUCTION FOR CLOSING IT.

0377 fixed three decision verbs and named `queue wake` as carrying the same check-then-act shape:
a good predicate on the SELECT (`AND woke_at IS NULL`) and none at all on the UPDATE
(`WHERE id = %s`). It refused to sweep it in with them, in its own words: "Do not batch-fix them.
Each needs its own answer to 'is concurrency reachable here, and what is correct on contention' --
for these three it was 'wait, re-read, refuse', and that will not be right everywhere."

THE MEASUREMENT, TAKEN BEFORE THE FIX. Lane C ran the harness on 2026-08-27 against the pre-fix
verb, 8 processes on 8 connections, barrier-released at one open defer. Three consecutive runs,
identical:

    returned success 7   refused 1                    (over 8 racers)
    thread notes written saying the defer woke: 7     (over 8 racers)
    woke_by on the row: a DIFFERENT loser each run    (waker-7, waker-4, waker-1)

Seven callers all passed `WHERE woke_at IS NULL` and all wrote. Nothing was spawned, so this is
not a double dispatch -- but in a system whose record IS the thread, ONE event appearing SEVEN
times with seven actors is a corrupted record, and the operator cannot tell it from an item that
genuinely woke seven times.

AND THE CORRECT BEHAVIOUR ON CONTENTION IS NOT WHAT IT WAS FOR THE THREE. A second waker is not a
rival decision; it is the same fact arriving twice, and the loser is usually a CLOCK. So the write
is serialised and the loser is refused, exactly as there -- but `no open defer 12`, which was one
message for two different states, had to be split. See `queue_wake`'s docstring for the argument.

WHAT IS ASSERTED

  scene 1  SEQUENTIAL control: waking twice in a row is refused, so a race result is
           attributable to concurrency rather than to a missing guard at all
  scene 2  the refusal for an ALREADY-WOKEN defer names who woke it and when, and says it is not
           a failure. A routine has to be able to tell that from scene 3's message.
  scene 3  and a defer that does not exist gets a DIFFERENT refusal, because a routine that gets
           this one has a bug in what it is watching
  scene 4  THE RACE: 8 processes, one open defer, exactly one wake and exactly one thread note
  scene 5  POSITIVE CONTROL: the same harness on 8 DIFFERENT defers must wake all 8. Without it,
           "scene 4 woke one" is equally well explained by a harness that never raced

Builds nothing on the live store: `_scratch_preflight` reconciles a database you name.

Run: QUEUE_SCRATCH_DB=brain_q_wake BRAIN_PG_DB=brain_q_wake python3 queue/tests/test_wake_idempotency.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for _p in (str(ROOT), str(ROOT / "engine"), str(ROOT / "queue")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

sys.path.insert(0, str(HERE))
from _scratch_preflight import reconcile                                # noqa: E402

reconcile()

import store                                                            # noqa: E402
from human_queue import transitions as qt                               # noqa: E402,F401

PASS = FAIL = 0
FAILURES: list[str] = []

RACERS = 8


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok    {name}")
    else:
        FAIL += 1
        FAILURES.append(name)
        print(f"  FAIL  {name}")
        for line in str(detail).strip().splitlines()[:8]:
            print(f"        {line}")


RACER = r'''
import json, os, sys, time
sys.path.insert(0, os.environ["RACE_ROOT"])
sys.path.insert(0, os.path.join(os.environ["RACE_ROOT"], "engine"))
sys.path.insert(0, os.path.join(os.environ["RACE_ROOT"], "queue"))
import psycopg2
import store
from store import session
from human_queue import transitions as qt   # noqa: F401  registers the verb

did = os.environ["RACE_DEFER"]
who = os.environ["RACE_WHO"]
gate = os.environ["RACE_GATE"]
out = os.environ["RACE_OUT"]

# WARM THE CONNECTION BEFORE THE BARRIER, for the reason test_dispatch_idempotency gives: without
# it the race is between TCP handshakes and the first to authenticate wins every time, which looks
# like a serialised system and reports a false green.
try:
    c = psycopg2.connect(**session.dsn("runtime")); c.close()
except Exception as e:
    json.dump({"who": who, "error": f"no connection: {e}"}, open(out, "w")); sys.exit(0)

while not os.path.exists(gate):
    time.sleep(0.002)

t0 = time.monotonic()
res, err = None, ""
try:
    r = store.apply("queue wake", defer_id=int(did), by=who, note="raced")
    res = r.get("id")
except Exception as e:
    err = str(e) or e.__class__.__name__
t1 = time.monotonic()
json.dump({"who": who, "woke": res, "error": err, "t0": t0, "t1": t1}, open(out, "w"))
'''


def race(defers, tmp: Path) -> list[dict]:
    gate = tmp / "GO"
    if gate.exists():
        gate.unlink()
    racer_py = tmp / "racer.py"
    racer_py.write_text(RACER)
    procs = []
    for who, did in defers:
        out = tmp / f"out-{who}.json"
        if out.exists():
            out.unlink()
        env = {**os.environ, "RACE_ROOT": str(ROOT), "RACE_DEFER": str(did), "RACE_WHO": who,
               "RACE_GATE": str(gate), "RACE_OUT": str(out)}
        env.pop("SWARM_PARENT_TASK", None)
        procs.append((who, out, subprocess.Popen([sys.executable, str(racer_py)], env=env,
                                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)))
    time.sleep(1.5)
    gate.write_text("go")
    for _w, _o, p in procs:
        p.wait(timeout=180)
    results = []
    for who, out, p in procs:
        if out.exists():
            results.append(json.loads(out.read_text()))
        else:
            results.append({"who": who, "error": "no result file: "
                            + (p.stderr.read().decode()[-300:] if p.stderr else "")})
    return results


def overlap(results) -> int:
    """How many racers were inside the verb at the same instant as at least one other.

    A race that did not overlap is not a race, and a green from it means nothing.
    """
    spans = [(r["t0"], r["t1"]) for r in results if "t0" in r and "t1" in r]
    n = 0
    for i, (a0, a1) in enumerate(spans):
        if any(j != i and b0 < a1 and a0 < b1 for j, (b0, b1) in enumerate(spans)):
            n += 1
    return n


def make_defer(label: str) -> int:
    tid = store.apply("post", lane="queue", title=f"wake fixture {label}",
                      agent_claimable=True, workdir=str(ROOT),
                      signals={"reversibility": "reversible"})["id"]
    r = store.apply("queue defer", source_type="work_item", source_id=tid, kind="until-time",
                    label=label, wake_at="2026-01-01T00:00:00Z", by="fixture",
                    reason="a fixture defer")
    return int(r["id"]), tid


def notes_saying_woke(tid: str, did: int) -> int:
    with store.read() as s:
        rows = s.query("SELECT text FROM brain.thread WHERE work_item_id = %s", (tid,))
    return sum(1 for r in rows if f"queue defer {did} woke" in (r["text"] or ""))


def main() -> int:
    print(__doc__.splitlines()[0])
    print(f"\n  database {os.environ.get('BRAIN_PG_DB')!r}   {RACERS} racers")

    print("\nscene 1: SEQUENTIAL control -- waking twice in a row is refused")
    did, tid = make_defer("sequential")
    first = store.apply("queue wake", defer_id=did, by="first")
    check(f"the first wake succeeds (defer {did})", first["id"] == did, first)
    err = ""
    try:
        store.apply("queue wake", defer_id=did, by="second")
    except Exception as e:                                              # noqa: BLE001
        err = str(e)
    check("the second is refused", bool(err), "it was NOT refused, so any race result below "
                                              "would be about a missing guard, not concurrency")
    check(f"and exactly one thread note says it woke "
          f"({notes_saying_woke(tid, did)} of 1 expected)",
          notes_saying_woke(tid, did) == 1, notes_saying_woke(tid, did))

    print("\nscene 2: the ALREADY-WOKEN refusal names who and when, and says it is not a failure")
    check("it says 'already woken'", "already woken" in err, err)
    check("it names the waker", "first" in err, err)
    check("it says in words that this is not a failure", "not a failure" in err, err)
    check("and it no longer says the old 'no open defer', which meant two different things",
          "no open defer" not in err, err)

    print("\nscene 3: and a defer that does not exist gets a DIFFERENT refusal")
    err2 = ""
    try:
        store.apply("queue wake", defer_id=99999999, by="stray")
    except Exception as e:                                              # noqa: BLE001
        err2 = str(e)
    check("it says 'no such defer'", "no such defer" in err2, err2)
    check("and it is NOT the already-woken message, so a routine can tell them apart",
          "already woken" not in err2, err2)

    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)

        print(f"\nscene 4: THE RACE -- {RACERS} processes, ONE open defer")
        did, tid = make_defer("raced")
        results = race([(f"waker-{i}", did) for i in range(1, RACERS + 1)], tmp)
        ok_n = sum(1 for r in results if r.get("woke"))
        refused = sum(1 for r in results if r.get("error"))
        ov = overlap(results)
        broken = [r for r in results if r.get("error", "").startswith("no ")
                  and "no such defer" not in r.get("error", "")
                  and "already woken" not in r.get("error", "")]
        check(f"the racers actually overlapped ({ov} of {len(results)} were inside the verb at "
              f"the same instant as another)", ov >= 2,
              "they did not overlap, so this scene proves nothing about concurrency")
        check(f"no racer failed for a reason other than losing ({len(broken)} did)",
              not broken, broken)
        check(f"exactly ONE racer woke it ({ok_n} succeeded, {refused} refused, of "
              f"{len(results)})", ok_n == 1, [r.get("who") for r in results if r.get("woke")])
        n = notes_saying_woke(tid, did)
        check(f"and exactly ONE thread note says it woke ({n} written over {len(results)} "
              f"racers; lane C measured SEVEN here before the fix)", n == 1, n)
        with store.read() as s:
            row = s.one("SELECT woke_by, woke_at FROM brain.queue_defer WHERE id = %s", (did,))
        winner = [r["who"] for r in results if r.get("woke")]
        check(f"and woke_by is the racer that actually won, not a loser "
              f"(woke_by={row['woke_by']!r}, winner={winner})",
              winner and row["woke_by"] == winner[0], (row["woke_by"], winner))

        print(f"\nscene 5: POSITIVE CONTROL -- the same harness on {RACERS} DIFFERENT defers")
        pairs, tids = [], []
        for i in range(1, RACERS + 1):
            d2, t2 = make_defer(f"control-{i}")
            pairs.append((f"ctl-{i}", d2))
            tids.append((d2, t2))
        results = race(pairs, tmp)
        ok_n = sum(1 for r in results if r.get("woke"))
        ov = overlap(results)
        check(f"all {RACERS} wake ({ok_n} succeeded, {ov} overlapped) -- so scene 4's ONE is a "
              f"refusal and not a harness that never ran", ok_n == RACERS,
              [r.get("error") for r in results if not r.get("woke")])
        total_notes = sum(notes_saying_woke(t, d) for d, t in tids)
        check(f"and {RACERS} thread notes were written, one each ({total_notes})",
              total_notes == RACERS, total_notes)

    print()
    if PASS + FAIL == 0:                                                # DENOMINATOR
        print("0 comparisons made. A verdict over an empty set is not a pass.")
        return 2
    print(f"{PASS} passed, {FAIL} failed  (over {RACERS} racers per race scene)")
    if FAILURES:
        print("FAILED: " + ", ".join(FAILURES))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
