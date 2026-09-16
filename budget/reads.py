"""What `budget status` and the dispatch gate read.

Everything here goes through `store.read()`, which Postgres has put in READ ONLY mode, so this
file cannot become a second write path however it grows.

One rule governs the shape of these queries: **the gate and the dashboard read the same view.**
`brain.budget_state` computes the window once, in SQL, and both the number the operator sees and
the boolean the brake acts on come out of it. Two definitions of "the window" is how a brake
fires at a different number than the status output shows, which is the kind of disagreement that
is only ever discovered while it is costing money.
"""

from __future__ import annotations

import store

# The policies that cover one unit of work. Ordered widest first so the reported reason for a
# refusal is the widest brake that is down, which is the one the operator wants to hear about.
_COVERING = """
SELECT * FROM brain.budget_state
 WHERE (scope_type = 'fleet')
    OR (scope_type = 'agent'     AND scope_id = %(agent)s)
    OR (scope_type = 'lane'      AND scope_id = %(lane)s)
    OR (scope_type = 'work_item' AND scope_id = %(work_item)s)
 ORDER BY array_position(ARRAY['fleet','agent','lane','work_item']::text[],
                         scope_type::text), scope_id
"""

_OPEN_STOPS = """
SELECT * FROM brain.budget_open_stop
 WHERE (scope_type = 'fleet')
    OR (scope_type = 'agent'     AND scope_id = %(agent)s)
    OR (scope_type = 'lane'      AND scope_id = %(lane)s)
    OR (scope_type = 'work_item' AND scope_id = %(work_item)s){project_arm}
 ORDER BY occurred_at
"""

# THE PROJECT ARM, and it is deliberately not a new parameter.
#
# A project hold (task 0430) is enforced primarily in `claim`, because that is the only place that
# knows the project of the row it is about to hand out -- `bin/swarm-run` asks `budget check`
# BEFORE the claim (engine/SUPERVISION.md:55), and before the claim there is no task, so there is
# no project. Adding a `--project` argument here would be a branch no caller could ever fill.
#
# But the OTHER half of the operator's incident is a run ALREADY GOING: "it worked on it for like
# eight hours with eight terminals". `budget.enforcer.RunGuard` meters a live run and calls
# `evaluate(agent, lane, work_item_id)` on every tick, and it already has the work_item_id. So the
# project is derivable from arguments this function is ALREADY given, and holding a project stops
# its running agents at the next meter tick as well as stopping the next claim. No caller changes.
#
# `scope_type::text`, not the enum literal, for the reason migration 45 states at length: on a
# store one version behind, `scope_type = 'project'` does not return empty, it RAISES `invalid
# input value for enum brain.budget_scope`. This function is on the dispatch gate's path and a
# raise here refuses every dispatch on the fleet. The cast cannot raise. It also costs the partial
# index on (scope_type, scope_id), which is worth saying out loud and is not worth caring about:
# `budget_open_stop` is a view over uncleared incidents and a healthy fleet has none.
_PROJECT_ARM = """
    OR (scope_type::text = 'project' AND scope_id = (
          SELECT brain.work_item_project(w) FROM brain.work_item w WHERE w.id = %(work_item)s))"""

# Migration 45 applied to THIS database? Asked, never assumed, and the shape is
# `engine.swarm_engine.transitions._budget_schema_present`'s: naming a function that does not
# exist raises `UndefinedFunction`, which on this path is the dispatch gate refusing every run for
# a reason that has nothing to do with money. pg_proc rather than `to_regprocedure`, because a
# signature naming a composite type that does not exist can itself raise.
#
# Asked per call rather than cached, for that function's reason: a cached "absent" would leave the
# project hold unenforced for the life of the process after the migration landed, which is the
# failure mode that costs money.
_HAS_PROJECT_SCOPE = """
SELECT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                WHERE n.nspname = 'brain' AND p.proname = 'work_item_project')
"""


def covering_policies(agent: str = "", lane: str = "", work_item_id: str = "") -> list[dict]:
    """Every active policy whose scope covers this unit of work, with its current spend."""
    with store.read("runtime") as s:
        return s.query(_COVERING, {"agent": agent, "lane": lane,
                                   "work_item": work_item_id or ""})


def open_stops(agent: str = "", lane: str = "", work_item_id: str = "") -> list[dict]:
    """Uncleared manual and hard stops covering this unit of work.

    A manual stop needs no policy and no spend, so it does not appear in `budget_state`. Reading
    only the spend view is the mistake that lets `budget stop --scope fleet` be silently ignored
    by a scope that happens to have no ceiling set.

    Covers the PROJECT of `work_item_id` as well as the four dimensions named in the signature.
    See `_PROJECT_ARM` for why that is derived here rather than passed in.
    """
    with store.read("runtime") as s:
        arm = _PROJECT_ARM if s.scalar(_HAS_PROJECT_SCOPE) else ""
        return s.query(_OPEN_STOPS.format(project_arm=arm),
                       {"agent": agent, "lane": lane, "work_item": work_item_id or ""})


def status(scope_type: str | None = None, scope_id: str | None = None) -> list[dict]:
    """Every active policy and its window spend. The whole of `budget status`."""
    sql = "SELECT * FROM brain.budget_state"
    params: dict = {}
    where = []
    if scope_type:
        where.append("scope_type = %(scope_type)s")
        params["scope_type"] = scope_type
    if scope_id is not None and scope_type:
        where.append("scope_id = %(scope_id)s")
        params["scope_id"] = scope_id
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += (" ORDER BY array_position(ARRAY['fleet','agent','lane','work_item']::text[],"
            " scope_type::text), scope_id, period")
    with store.read("runtime") as s:
        return s.query(sql, params)


def incidents(limit: int = 50, kind: str | None = None, open_only: bool = False) -> list[dict]:
    """The typed breach records, newest first."""
    sql = ("SELECT id, occurred_at, kind, cause, scope_type, scope_id, spend_usd, limit_usd, "
           "       percent_used, action_taken, detected_by, prescribed_disposition, agent, lane, "
           "       work_item_id, run_id, engine_pid, signal_sent, detail, cleared_at, cleared_by "
           "  FROM brain.budget_incident")
    where, params = [], {"limit": limit}
    if kind:
        where.append("kind = %(kind)s::brain.budget_incident_kind")
        params["kind"] = kind
    if open_only:
        where.append("cleared_at IS NULL")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY occurred_at DESC, id DESC LIMIT %(limit)s"
    with store.read("runtime") as s:
        return s.query(sql, params)


def incident(incident_id: int) -> dict | None:
    with store.read("runtime") as s:
        return s.one("SELECT * FROM brain.budget_incident WHERE id = %s", (incident_id,))


def spend(agent: str = "", lane: str = "", since_hours: int = 24) -> dict:
    """Raw meter reading, independent of any policy. Answers "what did we actually spend"."""
    with store.read("runtime") as s:
        return s.one(
            "SELECT COALESCE(sum(usd), 0)::numeric(12,6) AS usd, count(*) AS charges, "
            "       min(charged_at) AS first_at, max(charged_at) AS last_at "
            "  FROM brain.budget_charge "
            " WHERE charged_at > now() - (%(hours)s || ' hours')::interval "
            "   AND (%(agent)s = '' OR agent = %(agent)s) "
            "   AND (%(lane)s  = '' OR lane  = %(lane)s)",
            {"hours": since_hours, "agent": agent, "lane": lane},
        )


def charges_for_run(run_id: int) -> list[dict]:
    with store.read("runtime") as s:
        return s.query(
            "SELECT id, usd, source, source_ref, charged_at, note FROM brain.budget_charge "
            " WHERE run_id = %s ORDER BY id", (run_id,))
