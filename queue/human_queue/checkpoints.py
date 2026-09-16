"""Defaults fire at two fixed checkpoints, not continuously.

07:00 and 19:00. A drip of deadlines is a slot machine: it teaches the operator to watch the
queue because something might be about to happen. Two checkpoints are a rhythm, and a rhythm can
be planned around, which is the difference between a system that respects attention and one that
farms it.

The grace rule matters as much as the times. A question fires at the NEXT checkpoint after the
one that followed it, so every default gets at least one full interval of silence before silence
counts as an answer. Without that, an `ask` at 18:59 would be decided by silence at 19:00, and
the operator would have been given one minute to disagree with something he had not read.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

import store
from store import schema

from .transitions import queue_default_fire  # noqa: F401  (registers the verb)

CHECKPOINTS = ("07:00", "19:00")


def _at(day, hhmm: str) -> datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    return datetime.combine(day, time(h, m), tzinfo=timezone.utc)


def checkpoint_bounds(now: datetime | None = None) -> dict:
    """The checkpoint we are at or just past, and the one before it.

    Everything asked at or before `previous` has had a full interval of silence and is eligible.
    """
    now = now or datetime.now(timezone.utc)
    today = now.date()
    marks = sorted([_at(today - timedelta(days=1), "07:00"), _at(today - timedelta(days=1), "19:00"),
                    _at(today, "07:00"), _at(today, "19:00")])
    past = [m for m in marks if m <= now]
    current = past[-1]
    previous = past[-2]
    return {"now": now, "current": current, "previous": previous,
            "label": f"{current:%H:%M}"}


def due(now: datetime | None = None) -> list:
    """Open questions whose default is eligible to fire at this checkpoint.

    Deliberately a READ. Firing is a separate call, so a console can show what silence is about
    to decide and a dry run costs nothing.

    BELOW LEDGER 31 THE WITHDRAWN PREDICATE IS DROPPED AND THIS STILL ANSWERS. It is the pre-31
    behaviour and it is honest there for the same reason everywhere else is: a store with no
    `withdrawn_at` holds no withdrawn questions, so the predicate could only ever have removed
    zero rows. What that store does NOT have is the protection -- `queue_pending_default` is also
    un-narrowed there, so a default could fire on behalf of a cancelled task, which is doctrine
    rule 6's silent class and is why `store.schema.below_31()` says so on stderr.
    """
    b = checkpoint_bounds(now)
    if schema.question_withdrawal():
        narrow = "AND q.withdrawn_at IS NULL"
    else:
        narrow = ""
        schema.warn_once("checkpoints.due", schema.below_31())
    with store.read("runtime") as s:
        rows = s.query(
            f"""SELECT p.*, q.asked_at, e.window_extended_to
                 FROM brain.queue_pending_default p
                 JOIN brain.question q ON q.id = p.question_id
                 LEFT JOIN brain.queue_default_event e ON e.question_id = p.question_id
                WHERE q.answer IS NULL
                  -- Migration 31 already narrows queue_pending_default; this is the second
                  -- copy, deliberately, because firing a default is an ACT taken on the
                  -- operator's behalf and it must not be one predicate away from happening on
                  -- behalf of a task that was cancelled (task 0159).
                  {narrow}
                  AND q.asked_at <= %s
                  AND (e.fired_at IS NULL)
                  AND (e.window_extended_to IS NULL OR e.window_extended_to <= %s)
                ORDER BY q.asked_at""", (b["previous"], b["now"]))
    return [{**r, "checkpoint": b["label"]} for r in rows]


def fire_due(now: datetime | None = None, dry_run: bool = True, planners=()) -> dict:
    """Fire every eligible default at this checkpoint.

    Defaults to a DRY RUN. A scheduler that fires by default is one typo away from deciding the
    operator's open questions during a test run, and the whole point of the mechanism is that
    what silence decides is predictable.
    """
    b = checkpoint_bounds(now)
    items = due(now)
    fired, refused = [], []
    for it in items:
        if dry_run:
            fired.append({**it, "dry_run": True})
            continue
        try:
            fired.append(store.apply("queue default fire", qid=it["question_id"],
                                     checkpoint=b["label"], planners=tuple(planners)))
        except Exception as e:                                     # noqa: BLE001
            # A refusal is a finding, never a silent skip: it means a flag was raised after the
            # default was written, which is the one case the write-time trigger cannot catch.
            refused.append({"question_id": it["question_id"], "reason": str(e)})
    return {"checkpoint": b["label"], "eligible": len(items), "fired": fired,
            "refused": refused, "dry_run": dry_run}
