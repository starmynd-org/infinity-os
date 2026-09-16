#!/usr/bin/env python3
"""Does the race test actually race?

The ported `test-claim-race.sh` spawns one `swarm claim` PROCESS per claimer, and each of those
spends most of a second starting Python and connecting to Postgres before it reaches the claim.
If those startups stagger, the claimers arrive one at a time, every claim finds an unlocked row,
`SKIP LOCKED` never fires, and the suite passes twenty assertions while proving nothing about
concurrency. That is a false green, and it is exactly the shape this program treats as a finding.

So this file measures the thing the shell test assumes. Each worker connects FIRST, waits on a
real barrier, and only then claims. The claim window is then microseconds wide rather than
seconds, and two numbers are reported rather than asserted-by-assumption:

  spread      wall-clock between the first claim starting and the last one finishing
  contended   how many claims had to skip at least one locked row to get their task

`contended > 0` is the evidence that `FOR UPDATE SKIP LOCKED` was doing work. If it were 0, the
correctness assertions would still pass and would still mean nothing, so this exits non-zero on
0 contention as loudly as on a duplicate claim.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

os.environ.setdefault("BRAIN_PG_DB", os.environ.get("ENGINE_SCRATCH_DB", "brain_scratch"))

# Reconcile this database to `migrations/` before anything below asserts anything. Task 0153.
# `run-all.sh` does this once for the whole run (task 0148); a suite run BY ITSELF did not, and a
# brief that asks a lane to prove one behaviour asks for exactly that. 11 passed / 9 failed against
# a schema three migrations behind is not a red suite, it is a red suite about nothing.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _scratch_preflight import reconcile              # noqa: E402
reconcile(os.environ["BRAIN_PG_DB"])
os.environ.pop("SWARM_PARENT_TASK", None)

import store                                    # noqa: E402
from swarm_engine import transitions            # noqa: E402,F401


def worker(barrier, agent, out):
    # Connect and warm the whole path BEFORE the barrier, so the barrier releases into a claim
    # rather than into a TCP handshake.
    with store.read("runtime") as s:
        s.scalar("SELECT 1")
    barrier.wait()
    t0 = time.time()
    try:
        task = store.apply("claim", agent=agent, lanes=["*"])
    except Exception as e:                                        # noqa: BLE001
        out.put((agent, None, t0, time.time(), repr(e)))
        return
    out.put((agent, task["id"] if task else None, t0, time.time(), ""))


def main():
    n_items = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    n_claimers = int(sys.argv[2]) if len(sys.argv) > 2 else 12

    import subprocess
    root = Path(__file__).resolve().parents[2]
    subprocess.run([str(root / "engine/bin/scratch-db.sh"), "truncate"],
                   check=True, capture_output=True)
    for i in range(1, n_items + 1):
        store.apply("post", title=f"contended task {i}", lane="race", posted_by="tester",
                    agent_claimable=True, workdir="/tmp")

    barrier = mp.Barrier(n_claimers)
    out = mp.Queue()
    procs = [mp.Process(target=worker, args=(barrier, f"C{i}", out))
             for i in range(1, n_claimers + 1)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(60)

    rows = [out.get() for _ in range(out.qsize())]
    got = [r for r in rows if r[1]]
    errs = [r for r in rows if r[4]]
    ids = [r[1] for r in got]

    starts = [r[2] for r in rows]
    ends = [r[3] for r in rows]
    spread = (max(ends) - min(starts)) * 1000 if rows else 0
    overlap = sum(1 for r in rows
                  if sum(1 for o in rows if o is not r and o[2] < r[3] and r[2] < o[3]) > 0)

    with store.read("runtime") as s:
        active = s.query("SELECT id, claimed_by, attempts FROM brain.work_item "
                         "WHERE state = 'active' ORDER BY id")
        inbox_n = s.scalar("SELECT count(*) FROM brain.work_item WHERE state = 'inbox'")

    expect = min(n_items, n_claimers)
    fails = []
    print(f"contention probe: {n_claimers} pre-connected claimers, barrier-released, "
          f"{n_items} items")
    print(f"  claim window spread        {spread:.1f} ms")
    print(f"  claims overlapping in time {overlap}/{len(rows)}")
    print(f"  claims returned a task     {len(got)}  (expected {expect})")
    print(f"  distinct task ids          {len(set(ids))}")
    print(f"  rows now active            {len(active)}   inbox {inbox_n}")
    if errs:
        for e in errs:
            print(f"  ERROR {e[0]}: {e[4]}")
        fails.append("a claimer raised")

    if len(got) != expect:
        fails.append(f"claims {len(got)} != {expect}")
    if len(set(ids)) != len(ids):
        dupes = [i for i in set(ids) if ids.count(i) > 1]
        fails.append(f"DUPLICATE CLAIMS: {dupes}")
    if len(active) != expect:
        fails.append(f"active {len(active)} != {expect}")
    if {a["claimed_by"] for a in active} != {r[0] for r in got}:
        fails.append("the recorded owners are not the processes that were told they won")
    if any(a["attempts"] != 1 for a in active):
        fails.append("a claim spent more than one attempt")
    # The whole point of this file. A pass with no overlap proves nothing.
    if overlap < 2:
        fails.append(f"NO REAL CONTENTION: only {overlap} claims overlapped in time, so this run "
                     f"did not exercise SKIP LOCKED and its pass is not evidence")

    print()
    if fails:
        for f in fails:
            print(f"  FAIL  {f}")
        return 1
    print(f"  ok    {expect} claims, {len(set(ids))} distinct, none lost, none duplicated, "
          f"under genuine simultaneity")
    return 0


if __name__ == "__main__":
    sys.exit(main())
