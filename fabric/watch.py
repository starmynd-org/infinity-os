"""The health watcher, and the reason it is a PRODUCER rather than a listener.

A quarantined subscriber, or one whose lag has stopped coming down, is the failure this whole lane
exists to make visible. The obvious place to notice it is inside the listener. That is exactly
where it cannot go:

> The `subscriber` role has **no write path to `event`.** If your listener needs to write an
> event, it is not a listener.

So the thing that notices is a separate process holding the `producer` role. It reads lag through
`runtime` (which has SELECT on the view and nothing on `event`), and it emits through `producer`
(which has INSERT on `event` and no SELECT on anything). Neither role can do the other's half, and
that is the point: the process that reports a stuck listener cannot be the stuck listener.

Run it beside the fleet:

    python3 -m fabric.watch --interval 60

It is also the answer to "listeners bind no port, so the port registry rules do not reach them".
They get no row and no health check by rule 5, and this is what watches them instead.
"""

from __future__ import annotations

import argparse
import json
import time

from . import emit as _emit
from . import lag as _lag

#: Do not re-emit the same condition every cycle. One event per subscriber per condition until it
#: clears; a pager that repeated itself every minute would be muted, and a muted pager is worse
#: than no pager because it looks like one.
_seen: dict = {}


def check_once(*, emit_events: bool = True) -> list:
    """One pass. Returns the conditions found, and emits one event per NEW condition."""
    found = []
    rows = _lag.subscribers()
    # An impossible reading is quarantined before it is paged on, so the event this watcher emits
    # describes a listener that has already been stopped rather than one still skipping events.
    _lag.enforce(rows)
    for row in rows:
        sub = row["subscriber"]
        # ONE ladder, and it lives in `lag._state`. This function used to carry its own copy that
        # tested `lag >= CRITICAL` only, so a cursor poisoned PAST the head -- a negative lag --
        # fell through every branch to `condition = None` and the pager stayed quiet about the
        # worst state this fabric has. A second copy of a safety ladder is a second chance to
        # forget a rung.
        if row["findings"]:
            condition = "impossible"
        elif row["state"] == "quarantined":
            condition = "quarantined"
        elif row["state"] == "critical":
            condition = "lag_critical"
        elif row["state"] == "warn":
            condition = "lag_warn"
        else:
            condition = None

        if condition != _seen.get(sub):
            _seen[sub] = condition
            if condition and emit_events:
                # The watcher is a producer, so this is `event emit` like any other producer's
                # call. It is not external and not canon-touching: reporting that a local listener
                # is stuck touches nothing outside this machine.
                _emit.emit(
                    type="fabric.subscriber.quarantined",
                    external=False, canon_touching=False,
                    subject_type="subscriber", subject_id=sub,
                    payload_summary=_emit.summarise({
                        "subscriber": sub, "condition": condition,
                        "lag": row["lag"], "last_seq": row["last_seq"],
                        "head_seq": row["head_seq"],
                        "high_water_seq": row["high_water_seq"],
                        "findings": row["findings"],
                        "cursor_last_moved": str(row["updated_at"]),
                    }),
                    produced_by=None, actor="fabric-watch")
            if condition:
                found.append({**row, "condition": condition})
    return found


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="fabric.watch",
                                description="watch listener LAG, which is the health signal")
    p.add_argument("--interval", type=float, default=60.0)
    p.add_argument("--once", action="store_true")
    p.add_argument("--no-emit", action="store_true",
                   help="report conditions without emitting events")
    a = p.parse_args(argv)
    while True:
        found = check_once(emit_events=not a.no_emit)
        print(json.dumps({"checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                          "health": _lag.health(), "new_conditions": found},
                         default=str))
        if a.once:
            return 0
        time.sleep(a.interval)


if __name__ == "__main__":
    raise SystemExit(main())
