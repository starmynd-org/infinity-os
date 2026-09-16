#!/usr/bin/env python3
"""PROBE: do the three term-5 identity reads in web/model.py answer, on this store, as this process?

Imports the console's model the way the console does and calls identity_view(),
workspaces_visible() and workspace_access() against the store BRAIN_PG_DB names. Prints what came
back. It asserts only shape: every key present, `reach` a bool, the note a sentence or None.
Whether the human reaches anything is the store's business and is printed, not judged.

    ENGINE_SCRATCH_DB=ios_term5_scratch BRAIN_PG_DB=ios_term5_scratch \\
        python3 migrations/tests/probe_identity_view.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT, ROOT / "engine", ROOT / "queue", ROOT / "voice"):
    sys.path.insert(0, str(p))

DB = os.environ.get("BRAIN_PG_DB", "")
if not DB or "scratch" not in DB or DB in ("brain", "brain_scratch"):
    sys.stderr.write("NOT RUN: BRAIN_PG_DB must name your own scratch database\n")
    sys.exit(77)
os.environ["BRAIN_AFTER_COMMIT_HOOKS"] = "0"

from web import model                                           # noqa: E402

checks = 0
bad = 0


def show(name, value):
    print(f"  {name}: {value}")


iv = model.identity_view()
checks += 1
if set(iv) != {"whoami", "human", "workspaces"}:
    bad += 1
    print(f"  FAIL identity_view keys: {sorted(iv)}")
show("identity_view.human", iv["human"])
show("identity_view.whoami.reachable/agrees", (iv["whoami"].get("reachable"), iv["whoami"].get("agrees")))
show("identity_view.workspaces", iv["workspaces"])

vis = model.workspaces_visible()
checks += 1
if set(vis) != {"human", "workspaces", "count", "declared", "note"} or vis["count"] != len(vis["workspaces"]):
    bad += 1
    print(f"  FAIL workspaces_visible shape: {vis}")
show("workspaces_visible", vis)

acc = model.workspace_access("ios-t5-nowhere")
checks += 1
if set(acc) != {"human", "workspace", "reach", "report", "note"} or not isinstance(acc["reach"], bool):
    bad += 1
    print(f"  FAIL workspace_access shape: {acc}")
if acc["reach"]:
    bad += 1
    print("  FAIL workspace_access says the human reaches a workspace nobody declared")
show("workspace_access('ios-t5-nowhere').reach", acc["reach"])
show("workspace_access('ios-t5-nowhere').report", acc["report"])

print(f"\n{checks - bad} of {checks} shape checks passed on {DB}")
sys.exit(1 if bad else 0)
