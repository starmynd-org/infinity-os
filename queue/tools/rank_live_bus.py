#!/usr/bin/env python3
"""Rank the operator's REAL task graph, read only, without writing anything anywhere.

Why this exists rather than a seeded demo: `brain.work_item` is empty (the store is new and no
lane has loaded dispatch into it), so a decomposition computed from invented rows would prove
that the arithmetic runs and nothing about whether it says anything true. The live file bus at
`$SWARM_HOME/tasks` holds 84 real tasks with real signals and real `depends_on` edges, and it is
the same state contract this runtime ports.

IT WRITES NOTHING. It opens the bus files for reading, builds the DAG in memory, and prints. The
live bus is another system's state and this lane does not touch it.

    python3 queue/tools/rank_live_bus.py [--top 12]
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "queue"))
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT))            # `store`, imported by the package's verb registration

from human_queue import rank, tiers                        # noqa: E402
from swarm_engine.signals import signal_level              # noqa: E402

BUS = Path(os.environ.get("SWARM_HOME", str(Path.home() / ".swarm"))) / "tasks"
FIELD = re.compile(r'^([a-z_]+):[ \t]*(.*)$', re.M)


def read_tasks() -> list:
    out = []
    for f in sorted(glob.glob(str(BUS / "**" / "*.md"), recursive=True)):
        text = Path(f).read_text(encoding="utf-8", errors="ignore")
        head = text.split("---", 2)[1] if text.startswith("---") else text
        row = {k: v.strip() for k, v in FIELD.findall(head)}
        if row.get("id"):
            row["_file"] = f
            out.append(row)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=12)
    args = ap.parse_args()

    raw = read_tasks()
    if not raw:
        print(f"no tasks under {BUS}", file=sys.stderr)
        return 1
    rows = [{"id": r["id"], "state": r.get("state", ""),
             "priority": int(r.get("priority") or 3), "depends_on": r.get("depends_on", "")}
            for r in raw]
    dag = rank.Dag(rows)
    now = datetime.now(timezone.utc)

    live = [r for r in raw if r.get("state") in ("inbox", "active", "blocked")]
    print(f"LIVE BUS SNAPSHOT  {BUS}   {len(raw)} tasks, "
          f"{sum(1 for r in rows if r['depends_on'])} with a depends_on edge, "
          f"{len(live)} not terminal.   READ ONLY.")

    scored = []
    for r in raw:
        created = r.get("created", "")
        try:
            surfaced = datetime.fromisoformat(created.replace("Z", "+00:00"))
        except ValueError:
            surfaced = now
        item = {"urgency": signal_level("urgency", r.get("urgency")),
                "stakes": signal_level("stakes", r.get("stakes")),
                "charter_alignment": signal_level("charter_alignment", r.get("charter_alignment")),
                "surfaced_at": surfaced}
        u = dag.unblock_weight(r["id"])
        s = rank.score(item, u, 0.0, now=now)
        band, band_reason = rank.jump_band({"urgency": item["urgency"]}, now=now)
        t = tiers.tier_of(
            {"item_class": "review" if r.get("state") == "done" else "blocker"},
            signal_level("reversibility", r.get("reversibility")))
        scored.append({"id": r["id"], "state": r.get("state", ""), "u": u, **s,
                       "band": band, "band_reason": band_reason, "tier": t["tier"],
                       "tier_reason": t["reason"], "title": r.get("title", "")[:52],
                       "declared": r.get("dependency_unblocking", ""),
                       "blocked_by": dag.unresolved_blockers(r["id"])})

    scored.sort(key=lambda r: (r["band"], -r["score"], r["id"]))
    print(f"\ntop {args.top} by band then score (the whole bus, terminal states included):")
    print(f"  {'id':<6} {'state':<10} {'tier':<7} {'score':>7} {'U':>6}  declared  title")
    for r in scored[:args.top]:
        print(f"  {r['id']:<6} {r['state']:<10} {r['tier']:<7} {r['score']:>7} "
              f"{r['u']:>6.3f}  {r['declared'] or '-':<8}  {r['title']}")

    unblocking = sorted([r for r in scored if r["u"] > 0], key=lambda r: -r["u"])
    print(f"\n{len(unblocking)} task(s) carry a non-zero unblock weight. The decomposition for "
          f"the largest:")
    for r in unblocking[:3]:
        print(f"\n  {r['id']}  {r['title']}")
        print(f"    score {r['score']} = "
              + ", ".join(f"{k} {v:+g}" for k, v in r["terms"].items() if v))
        print(f"    U = {r['u']:.3f}, from:")
        for d in dag.explain(r["id"]):
            print(f"      {d['id']}: band value {d['band_value']} / {d['blockers']} unresolved "
                  f"blocker(s) + 0.5 * {d['downstream_u']} downstream = {d['contribution']:+g}")
        print(f"    the producer declared dependency_unblocking={r['declared'] or 'unset'}")

    if dag.cycles:
        print(f"\nDEPENDENCY CYCLES FOUND IN THE REAL BUS: {len(dag.cycles)}")
        for c in dag.cycles:
            print("  " + " -> ".join(c))
    else:
        print("\nno dependency cycles in the real bus.")

    declared_high = [r for r in scored if signal_level("dependency_unblocking", r["declared"])
                     == "high"]
    misses = [r for r in declared_high if r["u"] < 0.5]
    print(f"\ncalibration: {len(declared_high)} task(s) declare dependency_unblocking=high; "
          f"{len(misses)} of them release nothing the DAG can see.")
    for r in misses[:6]:
        print(f"  {r['id']} declared high, U = {r['u']:.2f}  {r['title']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
