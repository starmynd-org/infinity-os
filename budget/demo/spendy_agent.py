#!/usr/bin/env python3
"""A stand-in engine that spends money and does work, so the stop has something real to stop.

It prints a cost marker per unit of work in the same shape the runner reads `total_cost_usd` off
the engine's stream-json, and it APPENDS TO A WORK FILE each time. The work file is the point:
after the guard kills it, the file says how far it got, which is how "the run was stopped" is
demonstrated rather than asserted. A process that merely printed would leave nothing behind to
check.

    --units N        how many units of work it intends to do
    --cost C         dollars per unit
    --stubborn       ignore SIGTERM, to demonstrate the SIGKILL escalation
    --limit-line     also print a subscription-limit refusal, to poison the log on purpose
"""

from __future__ import annotations

import argparse
import signal
import sys
import time


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--units", type=int, default=20)
    ap.add_argument("--cost", type=float, default=0.85)
    ap.add_argument("--work-file", default="")
    ap.add_argument("--stubborn", action="store_true")
    ap.add_argument("--limit-line", action="store_true")
    ap.add_argument("--delay", type=float, default=0.15)
    a = ap.parse_args()

    if a.stubborn:
        # An engine that spawned a tool and is mid-write does not always die on the first ask.
        signal.signal(signal.SIGTERM, lambda *_: print("caught SIGTERM, ignoring", flush=True))

    if a.limit_line:
        # The exact wording from the 2026-08-14 incident, planted in a log whose run is then
        # killed for budget. If a classifier reads text before it reads its own record, this is
        # the line that turns a breach into a reopen and a reopen into a spend loop.
        print("[text] You've hit your session limit · resets 7:10pm (Europe/Bucharest)",
              flush=True)

    for i in range(1, a.units + 1):
        print(f"[work] unit {i} of {a.units}", flush=True)
        if a.work_file:
            with open(a.work_file, "a") as fh:
                fh.write(f"unit {i}\n")
        print(f"[cost] {a.cost}", flush=True)
        time.sleep(a.delay)

    print(f"[done] all {a.units} units", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
