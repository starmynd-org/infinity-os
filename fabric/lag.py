"""The health signal, and it is not the obvious one.

`pg_isready` proves the server is up. It proves nothing about whether anything is listening. A
listener that is running, connected and stuck reports healthy under every process-level check you
would otherwise write, and listeners bind no port, so the port registry's control-script and
health-check rules do not reach them at all. They get no row by rule 5 and they are exactly the
processes whose silent death loses events.

The signal is one subtraction:

    max(event_seq) - subscriber_cursor.last_seq     -- per subscriber

D1 shipped it as the view `brain.subscriber_lag`. This module is what reads it, what decides when
a number is a problem, and what makes the difference between "up" and "keeping up" impossible to
confuse in a status line.

**AN IMPOSSIBLE VALUE IS A FINDING, NOT A READING.** This module used to test `lag >= WARN` and
nothing else, so when one subscriber advanced another's cursor past the head of the bus
(2026-08-16, task 0119 item 6) it read `lag = -4241` and printed `state=ok`, verdict "keeping up".
That is worse than the cursor bug it was reporting on: the cursor bug loses events, and this told
the operator nothing was wrong while it happened. The whole argument for lag being the health
signal is that a stuck listener reports healthy under every process-level check -- a lag metric
that cannot report its own corruption defeats its own purpose and is not worth running.

So there are two ladders here and the order between them matters. The impossible readings are
checked FIRST and they are CRITICAL regardless of the number, and the subscriber is quarantined,
because a cursor nobody can explain must stop rather than keep skipping events quietly.
"""

from __future__ import annotations

import psycopg2

import store

#: Lag thresholds. Deliberately absolute counts rather than a rate: a rate needs history and the
#: first thing this has to survive is being new. Tune with measurement, not with taste.
LAG_WARN = 25
LAG_CRITICAL = 200

#: The columns migration 10 added. Named here so a store that predates it fails with a sentence
#: instead of an UndefinedColumn, and never by falling back to the blind query -- the blind query
#: is the one that reported the poisoning as healthy.
_NEEDS_MIGRATION_10 = (
    "brain.subscriber_lag is missing the corruption columns (high_water_seq, ahead_of_head, "
    "moved_backwards). Apply migrations/0010_subscriber_identity.sql. This module will not fall "
    "back to the query without them: that query is the one that rendered a cursor poisoned 4,241 "
    "events past the head of the bus as state=ok."
)


def findings(row: dict) -> list:
    """Everything about this reading that cannot be true. Empty is the normal case.

    Negative lag and `ahead_of_head` are the same arithmetic seen from two sides -- `lag` IS
    `head_seq - last_seq` -- and both are named because both are how somebody describes it when
    they see it, and a check nobody can find is a check nobody runs.
    """
    out = []
    lag, last, head = row["lag"], row["last_seq"], row["head_seq"]
    high = row["high_water_seq"]
    if lag < 0:
        out.append(f"negative lag ({lag}): a subscriber cannot be ahead of the bus it reads")
    if row["ahead_of_head"]:
        out.append(f"cursor {last} is past the head of the bus ({head}): it has acknowledged "
                   f"{last - head} events that do not exist, and will skip every real one until "
                   f"the bus catches up")
    if row["moved_backwards"]:
        out.append(f"cursor moved backwards: {last} is below the high water mark {high}. "
                   f"`event ack` takes GREATEST, so this was written around the verb")
    return out


def _state(row: dict) -> str:
    """The ladder. Impossible first, and impossible outranks every threshold below it."""
    if row.get("findings"):
        return "critical"
    if row["quarantined"]:
        return "quarantined"
    if row["lag"] >= LAG_CRITICAL:
        return "critical"
    if row["lag"] >= LAG_WARN:
        return "warn"
    return "ok"


def subscribers() -> list:
    """Every declared cursor, its lag, what cannot be true about it, and a verdict on it."""
    try:
        with store.read("runtime") as s:
            rows = s.query(
                "SELECT subscriber, last_seq, head_seq, lag, quarantined, updated_at, "
                "       high_water_seq, quarantine_reason, ahead_of_head, moved_backwards "
                "  FROM brain.subscriber_lag ORDER BY subscriber")
    except psycopg2.errors.UndefinedColumn as exc:                       # pragma: no cover
        raise RuntimeError(_NEEDS_MIGRATION_10) from exc
    for r in rows:
        r["findings"] = findings(r)
        r["state"] = _state(r)
    return rows


def enforce(rows: list = None) -> list:
    """Quarantine every subscriber whose reading is impossible. Returns what it stopped.

    A health check that writes is unusual and it is deliberate here. The listener whose cursor was
    poisoned is running fine and has nothing to report -- that is the entire problem -- so the only
    thing positioned to stop it is the thing that can see the damage. `quarantined_at` already
    exists and already stops `event ack` from advancing, so quarantining makes lag climb from a
    fixed point, which is the one signal an operator cannot mistake for idleness.

    It runs as `runtime` through `subscriber quarantine detected`, not as a listener: no listener
    holds the runtime role, so no listener can reach this to quarantine a rival.
    """
    from . import emit as _emit
    rows = subscribers() if rows is None else rows
    stopped = []
    for r in rows:
        if not r["findings"] or r["quarantined"]:
            continue
        out = _emit.quarantine_detected(r["subscriber"], "; ".join(r["findings"]))
        r["quarantined"] = True
        stopped.append({"subscriber": r["subscriber"], "findings": r["findings"],
                        "quarantined_at": out.get("quarantined_at")})
    return stopped


def health(enforce_findings: bool = True) -> dict:
    """Liveness AND lag, side by side, because they answer different questions.

    A caller that reads only `server` learns that Postgres is accepting connections. A caller that
    reads only `subscribers` learns nothing when the server is down. Reporting one without the
    other is how a dashboard goes green over a fabric that has not delivered an event in a day.

    `worst_lag` is computed over the readings that CAN be true. Taking a max across a poisoned row
    is how a -4,241 became the reassuring end of the range: it made the worst number the smallest
    one. Impossible rows are counted separately and they set the verdict on their own.
    """
    with store.read("runtime") as s:
        head = s.scalar("SELECT COALESCE(max(event_seq), 0) FROM brain.event")
        today = s.scalar(
            "SELECT count(*) FROM brain.event WHERE emitted_at >= date_trunc('day', now())")
    subs = subscribers()
    impossible = [r for r in subs if r["findings"]]
    quarantined = enforce(subs) if (impossible and enforce_findings) else []
    worst = max((r["lag"] for r in subs if not r["findings"]), default=0)
    return {
        "server": "up",
        "head_seq": head,
        "events_today": today,
        "subscribers": subs,
        "worst_lag": worst,
        "impossible": [{"subscriber": r["subscriber"], "last_seq": r["last_seq"],
                        "lag": r["lag"], "findings": r["findings"]} for r in impossible],
        "quarantined_now": quarantined,
        # The line a status page should print. "up" is not an answer to "is anything listening",
        # and neither is a number that cannot be true.
        "verdict": ("no subscribers declared" if not subs
                    else f"CRITICAL: {len(impossible)} impossible reading"
                         f"{'' if len(impossible) == 1 else 's'}, quarantined"
                    if impossible
                    else "quarantined" if any(r["quarantined"] for r in subs)
                    else "critical" if worst >= LAG_CRITICAL
                    else "warn" if worst >= LAG_WARN
                    else "keeping up"),
    }


def receipts_due(limit: int = 50) -> list:
    """EF-4: consequential-event-emitted is a receipt booking moment. This finds the gaps.

    EF-5 gave `receipt` the `caused_by_event_id` column precisely so the layer-3 breach detector
    is a join, and this is the other half of that join: consequential events with no receipt
    pointing at them. Booking one is `receipt book`, which is D2's verb; this lane reports the
    obligation and does not reimplement someone else's transition.
    """
    with store.read("runtime") as s:
        return s.query(
            "SELECT e.event_seq, e.event_id, e.type, e.occurred_at, e.department "
            "  FROM brain.event e "
            " WHERE e.retention_class = 'consequential' "
            "   AND NOT EXISTS (SELECT 1 FROM brain.receipt r "
            "                    WHERE r.caused_by_event_id = e.event_seq) "
            " ORDER BY e.event_seq DESC LIMIT %s", (limit,))
