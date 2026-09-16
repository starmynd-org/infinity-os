"""What a run cost, read so that a shared stream file cannot hide half of it.

    python3 -m budget.run_cost --stream RUN.stream.jsonl [--run-json RUN.json]

Prints one `session_id<TAB>usd` line per DISTINCT engine session found. `swarm-run` charges each
line separately, which is why the session id is on it: `budget charge` is idempotent on
(source, source_ref), so one ref per session is what lets two sessions in one file both land
without either one double-charging on a re-read.

WHY THIS EXISTS. `swarm-run` derives the run json and the stream from (task, attempt) alone:

    RUN_JSON="$STATE/runs/${TASK_ID}-attempt${TASK_ATTEMPTS}.json"

The engine LOG is per-agent (`logs/$AGENT/...`), these two are not. Two terminals on the same
(task, attempt) therefore keep separate logs and share one stream and one run json. That is not
hypothetical: on 2026-08-16, `0092-attempt1` was run by T3 (session 4aee2969, $11.4242) and by T1
(session 7912efa2, $0.9658), and `0102-attempt1` by T6 (5babe034, $4.0429) and T3 (e35f7812,
$5.0359). Both pairs interleave sub-second through one file. The renderer keeps the last result
event it sees and `os.replace`s it over the run json, so exactly one session per file could ever
be charged, and $15.4671 of the $21.4688 those four sessions cost could not reach the meter.
(Nothing was actually lost: the live fleet ran a runner with no budget code at all. The hole was
structural and ahead of us. See `outputs/2026-08-16-T5-0244-two-sessions-one-run-json/`.)

THE TRAP THIS FILE EXISTS TO NOT FALL INTO. `total_cost_usd` is CUMULATIVE WITHIN A SESSION.
`0012-attempt1` carries 19 result events on ONE session id, climbing $5.7808 -> $27.2951: the
engine re-emits a running total, it does not emit increments. So "how many result events are in
the file" is the wrong question and summing them is a 10.8x overstatement of that one run. The
right question is how many distinct SESSION IDS are in the file:

    LAST result event WITHIN a session id, summed ACROSS distinct session ids.

`modelUsage` on a non-final result event is the session-FINAL snapshot and disagrees with its own
event's `total_cost_usd`, so it is not a cross-check on anything and is not read here.

WHAT THIS CANNOT RECOVER. The second engine truncates the stream (`: > "$stream_json"`) before it
starts, which destroys whatever the first engine had already written. Both 2026-08-16 collisions
survived because each engine's result event lands at the END of its own run, after the other's
truncation. Had the first session finished before the second started, its result event would be
gone from the stream and its run json overwritten, and only its per-agent log would still carry
`session=... cost_usd=...`. That residual is why the collision itself needs closing upstream and
not just metering correctly here.
"""

from __future__ import annotations

import argparse
import json
import sys


def _last_result_per_session(path: str) -> dict[str, float]:
    """session_id -> the LAST `total_cost_usd` that session reported. See the trap above."""
    costs: dict[str, float] = {}
    try:
        fh = open(path, errors="replace")
    except OSError:
        return costs
    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue      # a torn line is what a shared file looks like; skip it, keep going
            if not isinstance(ev, dict) or ev.get("type") != "result":
                continue
            sid = ev.get("session_id") or ""
            usd = ev.get("total_cost_usd")
            if not sid or usd is None:
                continue
            try:
                costs[sid] = float(usd)   # LAST wins within a session, by assignment order
            except (TypeError, ValueError):
                continue
    return costs


def _run_json_result(path: str) -> tuple[str, float] | None:
    try:
        with open(path, errors="replace") as fh:
            ev = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(ev, dict):
        return None
    usd = ev.get("total_cost_usd")
    if usd is None:
        return None
    try:
        return (ev.get("session_id") or "", float(usd))
    except (TypeError, ValueError):
        return None


def session_costs(stream: str = "", run_json: str = "") -> list[tuple[str, float]]:
    """Every distinct session in this run, with what it cost, ordered by session id.

    The run json is read too and it is authoritative for its OWN session: codex writes no
    stream at all, and a run whose stream was truncated after its result event landed still has
    its run json. It never overrides a session the stream also reports, because the stream is
    where a LATER result event for that session would appear.
    """
    costs = _last_result_per_session(stream) if stream else {}
    rj = _run_json_result(run_json) if run_json else None
    if rj is not None:
        sid, usd = rj
        # An empty session id (codex, or a malformed result) cannot be keyed against the stream,
        # so it is only trusted when the stream produced nothing at all. Keying it as "" and
        # charging it alongside real sessions would give every such run the same idempotency ref.
        if sid:
            costs.setdefault(sid, usd)
        elif not costs:
            costs[""] = usd
    return sorted(costs.items())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="budget.run_cost", description=__doc__.splitlines()[0])
    ap.add_argument("--stream", default="", help="the run's stream.jsonl")
    ap.add_argument("--run-json", default="", help="the run's result json, read as a fallback")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of TSV")
    a = ap.parse_args(argv)
    rows = session_costs(a.stream, a.run_json)
    if a.json:
        print(json.dumps([{"session_id": s, "usd": u} for s, u in rows]))
    else:
        for sid, usd in rows:
            print(f"{sid}\t{usd!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
