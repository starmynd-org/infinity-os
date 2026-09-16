"""Reads, exposed broadly.

Every function here opens a Postgres READ ONLY session, so this module can grow as large as any
lane needs without ever becoming a second write path. Add to it freely. If you find yourself
wanting to add a function that changes something, that is a transition and it belongs in an
engine lane behind `@transition`.

Bulk reads and indexing may call these directly rather than shelling out to a CLI. Subprocess
versus in-process is a performance decision, not an architectural one.
"""

from __future__ import annotations

from . import schema
from .session import read

CLAIMABLE_ORDER = """
    ORDER BY w.priority DESC,
             ( 1.0 * CASE s.urgency               WHEN 'high' THEN 2 WHEN 'medium' THEN 1 ELSE 0 END
             + 1.0 * CASE s.dependency_unblocking WHEN 'high' THEN 2 WHEN 'medium' THEN 1 ELSE 0 END
             + 1.0 * CASE s.charter_alignment     WHEN 'high' THEN 2 WHEN 'medium' THEN 1 ELSE 0 END
             + 1.0 * CASE s.stakes                WHEN 'high' THEN 2 WHEN 'medium' THEN 1 ELSE 0 END
             - 1.0 * CASE s.effort                WHEN 'high' THEN 2 WHEN 'medium' THEN 1 ELSE 0 END
             + 0.25 * EXTRACT(epoch FROM now() - w.created) / 86400.0
             ) DESC,
             w.created ASC
"""


def work_item(item_id: str) -> dict | None:
    with read() as s:
        return s.one("SELECT * FROM brain.work_item WHERE id = %s", (item_id,))


def signals(item_id: str) -> dict | None:
    """The nine signals with hard flags resolved by OR up the WHOLE parent chain.

    Resolved on read rather than stamped at post time, on purpose: raising a flag on a parent
    raises it on every descendant, retroactively. Stamping holds for exactly one hop, and a
    two-hop chain in which each hop looks compliant on its own is how a gate gets laundered.
    """
    with read() as s:
        return s.one("SELECT * FROM brain.work_item_signals WHERE id = %s", (item_id,))


def queue(lane: str | None = None, limit: int = 50) -> list:
    """What `claim` would take next, in order, without taking it.

    Read-only by construction, so `why` and the console can call it as often as they like. The
    actual claim is a transition and uses `FOR UPDATE SKIP LOCKED`; this is the preview.
    """
    sql = (
        "SELECT w.id, w.title, w.lane, w.priority, w.created, "
        "       s.external, s.canon_touching, s.urgency, s.stakes, s.effort, s.confidence "
        "  FROM brain.work_item w JOIN brain.work_item_signals s ON s.id = w.id "
        " WHERE w.state = 'inbox' " + ("AND w.lane = %(lane)s " if lane else "")
        + CLAIMABLE_ORDER + " LIMIT %(limit)s"
    )
    with read() as s:
        return s.query(sql, {"lane": lane, "limit": limit})


def thread(item_id: str) -> list:
    with read() as s:
        return s.query(
            "SELECT seq, ts, from_agent, to_agent, kind, text FROM brain.thread "
            "WHERE work_item_id = %s ORDER BY seq",
            (item_id,),
        )


def feed(since_minutes: int = 60, limit: int = 200) -> list:
    with read() as s:
        return s.query(
            "SELECT * FROM brain.feed WHERE ts > now() - make_interval(mins => %s) "
            "ORDER BY ts DESC LIMIT %s",
            (since_minutes, limit),
        )


def artifacts(item_id: str | None = None) -> list:
    """Recorded artifacts. `exists_at_record` is what was true when the claim was made.

    A record whose path is gone now is a finding, not a formatting problem. Re-statting is the
    caller's job because this session cannot reach the filesystem for it.
    """
    sql = "SELECT * FROM brain.artifact"
    params: tuple = ()
    if item_id:
        sql += " WHERE work_item_id = %s"
        params = (item_id,)
    with read() as s:
        return s.query(sql + " ORDER BY seq", params)


def open_questions() -> list:
    """Open means neither answered NOR withdrawn (migration 31, task 0159).

    A withdrawn question is one nobody has to decide -- normally because the task that raised it
    was cancelled -- and every surface that reads this one is a surface that asks a human to
    decide something.

    BELOW LEDGER 31 IT FALLS BACK TO `answer IS NULL` AND KEEPS SERVING. This read is the
    console's open-questions panel and it served long before withdrawal existed; taking it down
    over a column a store has not received yet is the 2026-08-18 image outage again with a
    different element (`docs/SCHEMA-TOLERANCE.md`, rules 1 and 5). The fallback is a fact and not
    a guess: a store with nowhere to record a withdrawal holds no withdrawn questions.
    """
    withdrawable = schema.question_withdrawal()
    if not withdrawable:
        schema.warn_once("open_questions", schema.below_31())
    narrow = " AND withdrawn_at IS NULL" if withdrawable else ""
    with read() as s:
        return s.query(
            "SELECT id, asked_by, work_item_id, asked_at, text, default_if_unanswered "
            f"FROM brain.question WHERE answer IS NULL{narrow} ORDER BY asked_at"
        )


def agents() -> list:
    with read() as s:
        return s.query(
            "SELECT name, role, status, work_item_id, host, updated, "
            "       stopped_at IS NOT NULL AS stopped, "
            "       EXTRACT(epoch FROM now() - updated)::bigint AS age_seconds "
            "FROM brain.agent ORDER BY name"
        )


def fleet_paused() -> bool:
    with read() as s:
        return bool(s.scalar(
            "SELECT count(*) > 0 FROM brain.runtime_flag "
            "WHERE key = 'fleet_paused' AND value = 'true'"
        ))


def subscriber_lag(subscriber: str | None = None) -> list:
    """Listener health, which is lag and not liveness.

    `pg_isready` proves the server is up and says nothing about the listeners. They bind no port,
    so they are outside the port registry rules entirely and this is the only signal that tells a
    healthy idle listener from a dead one.
    """
    sql = "SELECT * FROM brain.subscriber_lag"
    params: tuple = ()
    if subscriber:
        sql += " WHERE subscriber = %s"
        params = (subscriber,)
    with read() as s:
        return s.query(sql + " ORDER BY subscriber", params)


def auto_accept_candidates() -> list:
    """Who WOULD be auto-accepted. Nothing acts on this: the rule ships DISABLED.

    It exists so the one week of measurement has something to measure. Without
    `work_item.reversibility` this query returns nothing, the measurement reports a perfect
    0 percent disagreement rate, and the test evaluated nothing at all.
    """
    with read() as s:
        return s.query("SELECT * FROM brain.auto_accept_candidate ORDER BY finished_at")


def missing_lineage(subject_type: str = "work_item") -> list:
    """Subjects with a `produced_by` and no SCOREABLE `touch` rows.

    `produced_by` is a scalar and the wager ledger scores a set. A subject with a producer and no
    touches is a lineage claim that cannot be scored.

    `t.entity_id IS NOT NULL` is not a tidy-up, it is the correction task 0114 owed this
    function. Before migration 9 an edge whose component did not resolve could not be projected
    at all, so a subject whose only edge was unresolved had NO rows here and this query flagged
    it correctly. Migration 9 lands those edges, which would have silently flipped this subject
    from "flagged" to "fine" -- while nothing about it changed: an edge with no component id
    still names no component, and still cannot be scored. Existing is not the bar; being
    scoreable is. The unresolved edges themselves stay visible in `brain.touch_unresolved`.
    """
    with read() as s:
        return s.query(
            "SELECT w.id, w.produced_by FROM brain.work_item w "
            " WHERE w.produced_by IS NOT NULL "
            "   AND NOT EXISTS (SELECT 1 FROM brain.touch t "
            "                    WHERE t.subject_type = %s AND t.subject_id = w.id "
            "                      AND t.entity_id IS NOT NULL) "
            " ORDER BY w.created",
            (subject_type,),
        )


def unresolved_touch_edges(limit: int = 200) -> list:
    """Component-touch edges git holds whose component the brain could not name.

    The gap, named. A null `entity_id` is a debt rather than a resolution, and the reason
    migration 9 could widen the column without the store quietly becoming lossy is that the
    debt is readable from here instead of being invisible in a table of edges that look fine.

    NEVER clear a row by UPDATEing an id into it. git holds the edge (WAGER-7) and this table is
    a copy; an id that exists only here is reconcilable against nothing and makes the store
    authoritative for lineage. Name the component, book a follow-on receipt whose edge resolves
    (append-only, WAGER-13a), and project that commit.
    """
    with read() as s:
        return s.query(
            "SELECT id, subject_type, subject_id, entity_ref, entity_resolution_status, "
            "       component_type, orient_role, role, git_ref, touched_at "
            "  FROM brain.touch_unresolved ORDER BY touched_at DESC, id DESC LIMIT %s",
            (limit,),
        )
