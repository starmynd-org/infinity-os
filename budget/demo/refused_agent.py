#!/usr/bin/env python3
"""A stand-in engine that is REFUSED, reproducing the 2026-08-14 log byte for byte.

Nothing here is invented. The lines are the ones in `swarm-admiral/docs/rate-limits.md`, and the
run json carries the same keys the runner reads: one turn, zero cost, is_error true. That is what
a refusal looks like on disk, and it is what my classifier has to tell apart from a run that was
killed for spending too much.

    --run-json PATH    write the engine result json the runner reconciles from
"""

from __future__ import annotations

import argparse
import json
import sys

REFUSAL = "You've hit your session limit · resets 7:10pm (Europe/Bucharest)"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-json", default="")
    ap.add_argument("--session", default="8669b724")
    a = ap.parse_args()

    print(f"[init] session={a.session} model=claude-opus-5 "
          f"cwd=/mnt/c/Users/you/repos/infinity-os", flush=True)
    print(f"[text] {REFUSAL}", flush=True)
    print("", flush=True)
    print("=== engine result ===", flush=True)
    print(REFUSAL, flush=True)
    print(f"session={a.session} turns=1 cost_usd=0 error=True", flush=True)

    if a.run_json:
        with open(a.run_json, "w") as fh:
            json.dump({"session_id": a.session, "num_turns": 1, "total_cost_usd": 0,
                       "is_error": True, "result": REFUSAL}, fh)
    return 1        # non-zero, task still active: indistinguishable from a failure by exit code


if __name__ == "__main__":
    sys.exit(main())
