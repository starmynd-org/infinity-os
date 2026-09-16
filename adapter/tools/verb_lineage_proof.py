#!/usr/bin/env python3
"""One `ambiguous` row per lane, written through that lane's OWN verb. Task 0113.

    BRAIN_ROOT=/home/you/.cache/t5-lineage-wt \
      python3 adapter/tools/verb_lineage_proof.py --ref accountability-chart

Task 0103 proved the store CAN hold all four lineage states and wrote three rows into
`brain.receipt` through the one transition it added, `lineage project`. What it could not do
was write them through the verbs the rest of the system actually calls: `post`, `artifact`,
`session register`, `budget note`, `event emit` all took `produced_by` and nothing else, so a
row written by any of them landed with `resolution_status` SQL NULL -- which by that column's
own semantics means "no attempt was made" -- even when an attempt was made and came back
ambiguous. `ambiguous` was expressible in the schema and unreachable from every surface.

This script is the measurement of that claim and of its fix, in one run:

1. Resolve one reference through the real `EntityIndex` and show the status is `ambiguous`
   with its candidates. Nothing here hand-builds the triple: it comes from
   `store_join.lineage_columns(resolution)`, which is the point -- one producer of the three
   columns for four lanes, so the failure case cannot drift between them.
2. CONTROL, the pre-0113 behaviour: one row per lane written the old way, `produced_by` only.
   Read back to show `resolution_status IS NULL` on every one.
3. THE FIX: one row per lane written through the SAME verb with `**lineage_columns(res)`
   splatted in. Read back to show `resolution_status = 'ambiguous'`, `produced_by` NULL, and
   the raw ref preserved.
4. The trap this change had to avoid, demonstrated rather than asserted: ingest's
   `produced_by or "d3-ingest"` default, left alone, would have defaulted the NULL producer of
   an ambiguous resolution to a producer NAME and built a row `session_lineage_coherent`
   refuses. Shown by re-running the coherence rule over the old expression.

Writes to `brain` and to nothing else. Adds no transition and issues no INSERT of its own:
every write below is a `store.apply(verb)` or a lane's own verb function.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve()
_ROOT = next(p for p in _HERE.parents if (p / "store" / "__init__.py").exists())
sys.path[:0] = [str(_ROOT), str(_ROOT / "adapter"), str(_ROOT / "engine"), str(_ROOT / "ingest")]

import store                                                            # noqa: E402
import swarm_engine.transitions                                         # noqa: E402,F401
import budget.transitions                                               # noqa: E402,F401
from brain_adapter import config                                        # noqa: E402
from brain_adapter.index import EntityIndex                             # noqa: E402
from brain_adapter.store_join import lineage_columns                    # noqa: E402
from fabric import emit as _emit                                        # noqa: E402


def rule(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def show(label: str, row: dict | None) -> None:
    if row is None:
        print(f"  {label:<22} <no row>")
        return
    pb, ref, st = row.get("produced_by"), row.get("produced_by_ref"), row.get("resolution_status")
    print(f"  {label:<22} produced_by={pb!r:<12} produced_by_ref={ref!r:<24} "
          f"resolution_status={st!r}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", default="accountability-chart",
                    help="a reference the brain resolves AMBIGUOUSLY (several ids)")
    ap.add_argument("--task", default="0113")
    args = ap.parse_args(argv)

    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())

    # `post` inherits a parent from SWARM_PARENT_TASK, and this process is a swarm terminal, so
    # that variable holds a FILE-BUS task id. The file bus at ~/.swarm and brain.work_item are
    # two different boards that happen to number tasks the same way, and letting 0113 through
    # would make `post` refuse ("no such parent task") on a parent that genuinely exists -- just
    # not here. Cleared explicitly rather than worked around.
    os.environ.pop("SWARM_PARENT_TASK", None)

    # ---------------------------------------------------------------- 1. the resolution
    rule("1. the resolution, from the real index. The triple is BUILT ONCE, here.")
    root = config.brain_root()
    t0 = time.time()
    index = EntityIndex.build(root)
    print(f"  brain root   {root}")
    print(f"  indexed      {time.time() - t0:.2f}s")

    res = index.resolve(args.ref)
    print(f"  ref          {args.ref!r}")
    print(f"  status       {res.status!r}")
    print(f"  candidates   {[c.get('entity_id') for c in (res.candidates or [])]}")
    if res.status != "ambiguous":
        print(f"\n  REFUSING TO CONTINUE: {args.ref!r} resolved {res.status!r}, not 'ambiguous'. "
              f"The whole point of this run is the state that only the two new columns can "
              f"express; proving it with a reference that is not ambiguous would prove nothing. "
              f"Pass --ref with a genuinely ambiguous reference.")
        return 2

    cols = lineage_columns(res)
    print(f"\n  lineage_columns(res) = {cols}")
    assert cols["produced_by"] is None, "an ambiguous resolution must never yield an id"
    assert cols["produced_by_ref"] == args.ref
    assert cols["resolution_status"] == "ambiguous"

    # ---------------------------------------------------------------- 2. the control
    rule("2. CONTROL: the pre-0113 call shape, `produced_by` only. One row per lane.")
    control: dict[str, dict] = {}

    ctl_task = store.apply(
        "post", title=f"T5 0113 CONTROL row (pre-fix shape) {stamp}", lane="adapter",
        posted_by="T5", body="Evidence row for task 0113. Written with produced_by only.",
        produced_by="knowledge-ai-architecture-surface-boundary")
    with store.read("runtime") as s:
        control["engine post"] = s.one(
            "SELECT produced_by, produced_by_ref, resolution_status FROM brain.work_item "
            " WHERE id = %s", (ctl_task["id"],))

    ctl_inc = store.apply(
        "budget note", kind="warn", scope_type="agent", scope_id="T5",
        detail=f"T5 0113 CONTROL row (pre-fix shape) {stamp}",
        produced_by="knowledge-ai-architecture-surface-boundary")
    with store.read("runtime") as s:
        control["budget note"] = s.one(
            "SELECT produced_by, produced_by_ref, resolution_status FROM brain.budget_incident "
            " WHERE id = %s", (ctl_inc["id"],))

    ctl_evt = _emit.emit(type="question.raised", external=False, canon_touching=True,
                         actor="T5", lane="adapter", subject_type="work_item",
                         subject_id=args.task,
                         payload_summary=f"T5 0113 CONTROL row (pre-fix shape) {stamp}",
                         produced_by="knowledge-ai-architecture-surface-boundary")
    with store.read("runtime") as s:
        control["fabric emit"] = s.one(
            "SELECT produced_by, produced_by_ref, resolution_status FROM brain.event "
            " WHERE event_id = %s", (ctl_evt["event_id"],))

    ctl_sid = _register_session(f"t5-0113-control-{stamp}",
                                produced_by="knowledge-ai-architecture-surface-boundary")
    with store.read("runtime") as s:
        control["ingest register"] = s.one(
            "SELECT produced_by, produced_by_ref, resolution_status FROM brain.session "
            " WHERE id = %s", (ctl_sid,))

    for k, v in control.items():
        show(k, v)
    silent = [k for k, v in control.items() if v and v["resolution_status"] is None]
    print(f"\n  {len(silent)}/4 lanes land resolution_status NULL: {silent}")
    print("  NULL here means 'no attempt was made'. An attempt WAS made and it succeeded at "
          "saying \"ambiguous\". That is the gap 0113 closes.")

    # ---------------------------------------------------------------- 3. the fix
    rule("3. THE FIX: the same four verbs, `**lineage_columns(res)` splatted in.")
    fixed: dict[str, dict] = {}

    task = store.apply(
        "post", title=f"T5 0113 lineage evidence row (ambiguous) {stamp}", lane="adapter",
        posted_by="T5",
        body=f"Evidence row for task 0113: ref {args.ref!r} resolved AMBIGUOUS through the "
             f"adapter and the status was carried through the existing `post` verb. "
             f"Not work. Cancel freely.",
        **cols)
    with store.read("runtime") as s:
        fixed["engine post"] = s.one(
            "SELECT id, produced_by, produced_by_ref, resolution_status FROM brain.work_item "
            " WHERE id = %s", (task["id"],))

    store.apply("artifact", id=task["id"], path=str(_HERE), kind="report", agent="T5",
                exists=_HERE.exists(), note="the proof harness itself", **cols)
    with store.read("runtime") as s:
        fixed["engine artifact"] = s.one(
            "SELECT seq AS id, produced_by, produced_by_ref, resolution_status "
            "  FROM brain.artifact WHERE work_item_id = %s ORDER BY seq DESC LIMIT 1",
            (task["id"],))

    inc = store.apply("budget note", kind="warn", scope_type="agent", scope_id="T5",
                      detail=f"T5 0113 lineage evidence row (ambiguous) {stamp}", **cols)
    with store.read("runtime") as s:
        fixed["budget note"] = s.one(
            "SELECT id, produced_by, produced_by_ref, resolution_status FROM brain.budget_incident "
            " WHERE id = %s", (inc["id"],))

    evt = _emit.emit(type="question.raised", external=False, canon_touching=True,
                     actor="T5", lane="adapter", subject_type="work_item", subject_id=args.task,
                     payload_summary=f"T5 0113 lineage evidence row (ambiguous) {stamp}", **cols)
    with store.read("runtime") as s:
        fixed["fabric emit"] = s.one(
            "SELECT event_seq AS id, produced_by, produced_by_ref, resolution_status "
            "  FROM brain.event WHERE event_id = %s", (evt["event_id"],))

    sid = _register_session(f"t5-0113-ambiguous-{stamp}", **cols)
    with store.read("runtime") as s:
        fixed["ingest register"] = s.one(
            "SELECT id, produced_by, produced_by_ref, resolution_status FROM brain.session "
            " WHERE id = %s", (sid,))

    for k, v in fixed.items():
        show(k, v)

    ok = [k for k, v in fixed.items()
          if v and v["resolution_status"] == "ambiguous" and v["produced_by"] is None
          and v["produced_by_ref"] == args.ref]
    print(f"\n  {len(ok)}/{len(fixed)} rows carry ambiguous + NULL producer + the raw ref: {ok}")

    # ---------------------------------------------------------------- 4. the trap
    rule("4. THE TRAP the fix had to avoid, run rather than asserted.")
    # cols["produced_by"] is None, which is what an ambiguous resolution always emits. The old
    # expression is reproduced verbatim, not described.
    old = cols["produced_by"] or "d3-ingest"
    print(f"  ingest's old expression `produced_by or \"d3-ingest\"` on an ambiguous "
          f"resolution yields produced_by = {old!r}")
    print(f"  beside resolution_status = 'ambiguous', which session_lineage_coherent states "
          f"must be NULL.")
    with store.read("runtime") as s:
        verdict = s.scalar(
            "SELECT CASE 'ambiguous' WHEN 'resolved' THEN %s IS NOT NULL "
            "                        WHEN 'ambiguous' THEN %s IS NULL "
            "                        WHEN 'unresolved' THEN %s IS NULL ELSE true END",
            (old, old, old))
    print(f"  the CHECK's own expression, evaluated by Postgres: {verdict}  "
          f"-> the row is REFUSED, so ambiguous would have been unwritable through ingest.")
    print(f"  after the fix the default applies only when resolution_status IS NULL, and the "
          f"row above landed: {fixed['ingest register']}")

    rule("5. the drift view, which must stay empty of anything 0113 touched")
    with store.read("runtime") as s:
        for r in s.query("SELECT * FROM brain.lineage_column_drift"):
            print("  DRIFT:", r)
        print("  (queue_defer is task 0115, the queue lane's table, not 0113's)")

    print(f"\nevidence work_item = {task['id']} (state inbox; it is evidence, not work)")
    return 0 if len(ok) == len(fixed) else 1


def _register_session(key: str, **lineage) -> str:
    """The ingest lane's own verb, on the brain profile. Not a second writer."""
    os.environ["BRAIN_PROFILE"] = "brain"
    from ingest import verbs as ingest_verbs
    from ingest import store as ingest_store
    with ingest_store.transaction() as cur:
        out = ingest_verbs.session_register(
            key, agent_id="T5", kind="main", harness="claude-code",
            workdir=str(_ROOT), stated_goal="task 0113 lineage evidence",
            actor_type="ai", registration_source="backfill", emit_event=False,
            cur=cur, **lineage)
    return out["session_key"]


if __name__ == "__main__":
    raise SystemExit(main())
