"""The five transition functions D3 owns. Every surface calls these; none reimplements them.

    session register       a session exists and is open
    session end            a session is closed
    session link-parent    a session's parent is asserted
    transcript index       a transcript pointer and hash are recorded
    transcript verify      a recorded hash is re-measured against the file

D00's narrow waist: the Claude Code hook, the backfill CLI, a future Codex hook, the MCP
server and the console all enter through these functions. A hook does not INSERT. Each verb
is one transaction, so `register` cannot leave a session row without its event.

Reads live in `queries.py`, not here.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
from typing import Any

from . import config, events, profiles, store, transcript as tx
from .goal import adjudicate


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _norm_ts(value: str | None) -> str | None:
    return value or None


# ---------------------------------------------------------------------------
# session register
# ---------------------------------------------------------------------------
def session_register(
    session_key: str,
    *,
    session_id: str | None = None,
    agent_id: str | None = None,
    kind: str = "main",
    harness: str = "claude-code",
    harness_version: str | None = None,
    entrypoint: str | None = None,
    model: str | None = None,
    model_source: str | None = None,
    permission_mode: str | None = None,
    git_branch: str | None = None,
    is_sidechain: bool | None = None,
    project_root_dir: str | None = None,
    workdir: str | None = None,
    workdir_source: str | None = None,
    started_at: str | None = None,
    last_activity_at: str | None = None,
    state: str = "open",
    time_source: str | None = None,
    parent_session_key: str | None = None,
    parent_source: str | None = None,
    workflow_id: str | None = None,
    turns_user: int | None = None,
    turns_assistant: int | None = None,
    turns_source: str | None = None,
    tokens: dict[str, int | str | None] | None = None,
    cost_usd: float | None = None,
    cost_source: str | None = None,
    stated_goal: str | None = None,
    stated_goal_source: str | None = None,
    stated_goal_tier: str | None = None,
    produced_by: str | None = None,
    produced_by_ref: str | None = None,
    resolution_status: str | None = None,
    actor_type: str | None = None,
    registration_source: str = "hook",
    emit_event: bool = True,
    cur=None,
) -> dict[str, Any]:
    """Idempotent. Called twice for the same session_key it updates, never duplicates.

    Idempotence is required, not a nicety: a SessionStart hook fires again on `--resume` and
    on `--continue`, and a re-run backfill must not double-count.

    TWO WRITERS, AND THE PRECEDENCE BETWEEN THEM (task 0136). This verb is reached by a hook
    that WATCHED the session happen and by a backfill that RECONSTRUCTED it from a transcript
    afterwards. Both are legitimate; they disagree; and which of them ran most recently is an
    accident of scheduling, so write order must not decide the answer. The rule:

        a first-hand observation is never replaced by a reconstruction,
        a reconstruction may only fill a hole the observer left,
        and every rule here is monotone, so the outcome is the same in either order.

    Concretely: `registration_source` ratchets hook > manual > backfill; `state` ratchets
    ended > open > unknown; the hook's cwd, model, actor_type and time provenance outrank
    backfill's decoded equivalents; the goal triple is first-write-wins among hook writes but
    a hook goal displaces a reconstructed one; and `ended_at` is not writable from here at all.

    What this rule does NOT protect against, because nothing at this layer can: `DROP SCHEMA
    ingest CASCADE`. That, not this verb, is what erased the 2026-08-16T12:08Z hook proof row
    that 0136 was filed over. See the "two writers" note in ingest/docs/HOOK-INSTALL.md.

    THE LINEAGE TRIPLE. `produced_by`, `produced_by_ref` and `resolution_status` arrive
    together from `brain_adapter.store_join.lineage_columns(resolution)` and this verb never
    assembles them. Only the brain profile carries the last two: the scratch profile's own
    `session` table has no such columns, which is one more entry for the enumerated list in
    profiles.py rather than a reason to widen that schema from this lane.
    """
    tok = tokens or {}

    def _run(c):
        if profiles.is_brain():
            # D1's migration 1. Same transition, different table shape; see profiles.py for
            # the enumerated list of facts this schema cannot carry.
            c.execute("SELECT id FROM session WHERE id = %s", (session_key,))
            existing = c.fetchone()
            c.execute(profiles.BRAIN_SESSION_UPSERT, {
                "id": session_key, "harness": harness or "",
                "agent": agent_id or "", "role": kind or "",
                "host": config.host_id(), "workdir": workdir or "",
                "workdir_host": config.host_id() if workdir else "",
                "stated_goal": stated_goal, "parent_session_id": parent_session_key,
                "started_at": _norm_ts(started_at),
                # NEVER last_activity_at. See profiles.py: writing a transcript's last
                # timestamp here would claim an end nobody observed.
                "ended_at": None,
                "actor_type": actor_type,
                # `or "d3-ingest"` IS A TRAP ONCE A STATUS CAN RIDE ALONG, and it was the one
                # defect in this file rather than an omission. 'ambiguous' and 'unresolved'
                # both mean produced_by IS NULL; defaulting a NULL producer to the string
                # "d3-ingest" beside either of them builds a row that
                # `session_lineage_coherent` refuses, so the verb would have made the
                # ambiguous case UNWRITABLE while looking like it supported it. The default is
                # the fourth state's default: it applies only when no resolution was attempted.
                #
                # MIGRATION 17 (task 0142) MOVED THE DEFAULT TO ITS OWN COLUMN and changed
                # nothing else about when it applies. `produced_by` now passes straight
                # through, so it holds an entity id or NULL and never a component name, and
                # the producer name lands in `produced_by_producer` under exactly the same
                # condition as before: no resolution was attempted AND no id was supplied.
                # The discriminator is still `resolution_status`, and it still stays NULL --
                # writing 'unresolved' here would assert an attempt that never happened, which
                # is the fix 0142 proposed and the one thing that must not be done.
                # `<table>_produced_by_is_not_a_producer` refuses the old shape at the store
                # now, so a regression here fails loudly rather than silently reappearing.
                "produced_by": produced_by,
                "produced_by_producer": events.PRODUCER
                                        if resolution_status is None and not produced_by
                                        else None,
                "produced_by_ref": produced_by_ref,
                "resolution_status": resolution_status,
            })
            row = c.fetchone()
            result = {"session_key": row["id"], "created": existing is None,
                      "state": None, "event": None, "profile": "brain"}
            if emit_event and existing is None:
                result["event"] = events.emit(
                    c, events.SESSION_STARTED, started_at or _now(), session_key,
                    {"kind": kind, "harness": harness, "model": model, "workdir": workdir,
                     "workdir_host_id": config.host_id(),
                     "parent_session_key": parent_session_key,
                     "registration_source": registration_source},
                    # `row["actor_type"]`, not the `actor_type` argument. See the RETURNING
                    # clause in profiles.py: the event states what the row holds (task 0300).
                    actor_type=row["actor_type"])
            return result

        c.execute("SELECT session_key, state FROM session WHERE session_key = %s", (session_key,))
        existing = c.fetchone()
        c.execute(
            """
            INSERT INTO session (
                session_key, session_id, agent_id, kind, harness, harness_version, entrypoint,
                model, model_source, permission_mode, git_branch, is_sidechain,
                project_root_dir, workdir, workdir_source, workdir_host_id, workdir_path_class,
                started_at, last_activity_at, time_source, parent_session_key, parent_source,
                workflow_id, turns_user, turns_assistant, turns_source,
                tokens_input, tokens_output, tokens_cache_read, tokens_cache_creation,
                tokens_messages, tokens_usage_records, tokens_source,
                cost_usd, cost_source,
                stated_goal, stated_goal_source, stated_goal_tier,
                produced_by, actor_type, registration_source, state)
            VALUES (%s,%s,%s,%s,%s,%s,%s, %s,%s,%s,%s,%s, %s,%s,%s,%s,%s,
                    %s,%s,%s,%s,%s, %s,%s,%s,%s, %s,%s,%s,%s, %s,%s,%s, %s,%s, %s,%s,%s, %s,%s,%s,%s)
            ON CONFLICT (session_key) DO UPDATE SET
                session_id      = COALESCE(EXCLUDED.session_id, session.session_id),
                agent_id        = COALESCE(EXCLUDED.agent_id, session.agent_id),
                kind            = EXCLUDED.kind,
                -- FIRST-HAND BEATS RECONSTRUCTION, for every column below that carries this
                -- same CASE. See the "two writers" section of this docstring: the hook watched
                -- the session happen, backfill inferred it from a transcript afterwards, and
                -- which of them ran most recently is an accident of scheduling. So a stored
                -- hook fact is never replaced by a backfill fact; backfill may only fill a hole
                -- in it. The shape is deliberately identical everywhere so the rule is
                -- greppable and so no column quietly opts out.
                harness_version = CASE WHEN session.registration_source = 'hook'
                                        AND EXCLUDED.registration_source <> 'hook'
                                       THEN COALESCE(session.harness_version, EXCLUDED.harness_version)
                                       ELSE COALESCE(EXCLUDED.harness_version, session.harness_version) END,
                entrypoint      = CASE WHEN session.registration_source = 'hook'
                                        AND EXCLUDED.registration_source <> 'hook'
                                       THEN COALESCE(session.entrypoint, EXCLUDED.entrypoint)
                                       ELSE COALESCE(EXCLUDED.entrypoint, session.entrypoint) END,
                model           = CASE WHEN session.registration_source = 'hook'
                                        AND EXCLUDED.registration_source <> 'hook'
                                       THEN COALESCE(session.model, EXCLUDED.model)
                                       ELSE COALESCE(EXCLUDED.model, session.model) END,
                model_source    = CASE WHEN session.registration_source = 'hook'
                                        AND EXCLUDED.registration_source <> 'hook'
                                       THEN COALESCE(session.model_source, EXCLUDED.model_source)
                                       ELSE COALESCE(EXCLUDED.model_source, session.model_source) END,
                permission_mode = CASE WHEN session.registration_source = 'hook'
                                        AND EXCLUDED.registration_source <> 'hook'
                                       THEN COALESCE(session.permission_mode, EXCLUDED.permission_mode)
                                       ELSE COALESCE(EXCLUDED.permission_mode, session.permission_mode) END,
                git_branch      = CASE WHEN session.registration_source = 'hook'
                                        AND EXCLUDED.registration_source <> 'hook'
                                       THEN COALESCE(session.git_branch, EXCLUDED.git_branch)
                                       ELSE COALESCE(EXCLUDED.git_branch, session.git_branch) END,
                is_sidechain    = COALESCE(EXCLUDED.is_sidechain, session.is_sidechain),
                project_root_dir= COALESCE(EXCLUDED.project_root_dir, session.project_root_dir),
                -- workdir is the sharpest case. The hook has the process's real cwd; backfill
                -- decodes it from a directory name and the schema says that decode is LOSSY.
                -- Letting the reconstruction land on top would silently degrade a known-exact
                -- path, and workdir_source would still read 'hook-arg' unless it moved too.
                workdir         = CASE WHEN session.registration_source = 'hook'
                                        AND EXCLUDED.registration_source <> 'hook'
                                       THEN COALESCE(session.workdir, EXCLUDED.workdir)
                                       ELSE COALESCE(EXCLUDED.workdir, session.workdir) END,
                workdir_source  = CASE WHEN session.registration_source = 'hook'
                                        AND EXCLUDED.registration_source <> 'hook'
                                       THEN COALESCE(session.workdir_source, EXCLUDED.workdir_source)
                                       ELSE COALESCE(EXCLUDED.workdir_source, session.workdir_source) END,
                workdir_host_id = CASE WHEN session.registration_source = 'hook'
                                        AND EXCLUDED.registration_source <> 'hook'
                                       THEN COALESCE(session.workdir_host_id, EXCLUDED.workdir_host_id)
                                       ELSE COALESCE(EXCLUDED.workdir_host_id, session.workdir_host_id) END,
                workdir_path_class = CASE WHEN session.registration_source = 'hook'
                                        AND EXCLUDED.registration_source <> 'hook'
                                       THEN COALESCE(session.workdir_path_class, EXCLUDED.workdir_path_class)
                                       ELSE COALESCE(EXCLUDED.workdir_path_class, session.workdir_path_class) END,
                started_at      = LEAST(COALESCE(EXCLUDED.started_at, session.started_at),
                                        COALESCE(session.started_at, EXCLUDED.started_at)),
                last_activity_at = GREATEST(COALESCE(EXCLUDED.last_activity_at, session.last_activity_at),
                                            COALESCE(session.last_activity_at, EXCLUDED.last_activity_at)),
                -- The strongest observation any writer made wins, and it is a ratchet:
                -- ended > open > unknown. 'ended' was already absolute here. 'open' now is
                -- too, because a hook that watched a session start knows more than a backfill
                -- that found a transcript and, correctly, claims to have witnessed nothing.
                -- Without this, a backfill run mid-session restated every live session as
                -- 'unknown'. Monotone, so the outcome does not depend on write order.
                state           = CASE WHEN 'ended' IN (session.state, EXCLUDED.state) THEN 'ended'
                                       WHEN 'open'  IN (session.state, EXCLUDED.state) THEN 'open'
                                       ELSE 'unknown' END,
                time_source     = CASE WHEN session.registration_source = 'hook'
                                        AND EXCLUDED.registration_source <> 'hook'
                                       THEN COALESCE(session.time_source, EXCLUDED.time_source)
                                       ELSE COALESCE(EXCLUDED.time_source, session.time_source) END,
                parent_session_key = CASE WHEN session.registration_source = 'hook'
                                        AND EXCLUDED.registration_source <> 'hook'
                                       THEN COALESCE(session.parent_session_key, EXCLUDED.parent_session_key)
                                       ELSE COALESCE(EXCLUDED.parent_session_key, session.parent_session_key) END,
                parent_source   = CASE WHEN session.registration_source = 'hook'
                                        AND EXCLUDED.registration_source <> 'hook'
                                       THEN COALESCE(session.parent_source, EXCLUDED.parent_source)
                                       ELSE COALESCE(EXCLUDED.parent_source, session.parent_source) END,
                workflow_id     = COALESCE(EXCLUDED.workflow_id, session.workflow_id),
                turns_user      = COALESCE(EXCLUDED.turns_user, session.turns_user),
                turns_assistant = COALESCE(EXCLUDED.turns_assistant, session.turns_assistant),
                turns_source    = COALESCE(EXCLUDED.turns_source, session.turns_source),
                tokens_input    = COALESCE(EXCLUDED.tokens_input, session.tokens_input),
                tokens_output   = COALESCE(EXCLUDED.tokens_output, session.tokens_output),
                tokens_cache_read = COALESCE(EXCLUDED.tokens_cache_read, session.tokens_cache_read),
                tokens_cache_creation = COALESCE(EXCLUDED.tokens_cache_creation, session.tokens_cache_creation),
                -- The denominator travels with the sums it describes. A row that kept an old
                -- per-record total while taking a new deduped message count would state a ratio
                -- that never happened, so these COALESCE exactly like the four above and by the
                -- same rule: a fact already stored is not replaced, a hole in it is filled.
                tokens_messages = COALESCE(EXCLUDED.tokens_messages, session.tokens_messages),
                tokens_usage_records = COALESCE(EXCLUDED.tokens_usage_records, session.tokens_usage_records),
                tokens_source   = COALESCE(EXCLUDED.tokens_source, session.tokens_source),
                cost_usd        = COALESCE(EXCLUDED.cost_usd, session.cost_usd),
                cost_source     = COALESCE(EXCLUDED.cost_source, session.cost_source),
                -- THE GOAL TRIPLE, and it needs its own rule because the two above pull
                -- opposite ways. Among hook writes it is FIRST-write-wins: the hook calls this
                -- verb on every UserPromptSubmit, and stated_goal is what was asked at the
                -- start, so a later prompt is a new instruction, not a correction. Overwriting
                -- would quietly turn the field into "the last thing said". But a plain
                -- first-write-wins also let backfill's heuristic first line pin the field
                -- before the hook ever saw the real prompt, and then the verbatim goal was
                -- discarded for being second. So: a hook goal replaces a reconstructed one at
                -- any time, and never replaces another hook goal.
                --
                -- The discriminator is stated_goal_source, not registration_source, on purpose.
                -- This triple's provenance is its own column; reusing the row-level one would
                -- misjudge the row where a hook SessionStart (goal NULL) has already flipped
                -- registration_source to 'hook' over a backfilled goal.
                --
                -- All three branch on the same predicates so the triple can never decohere
                -- into one writer's text beside another writer's source label.
                stated_goal     = CASE
                    WHEN session.stated_goal IS NULL              THEN EXCLUDED.stated_goal
                    WHEN session.stated_goal_source LIKE 'hook%%'  THEN session.stated_goal
                    WHEN EXCLUDED.stated_goal_source LIKE 'hook%%'
                        THEN COALESCE(EXCLUDED.stated_goal, session.stated_goal)
                    ELSE session.stated_goal END,
                stated_goal_source = CASE
                    WHEN session.stated_goal IS NULL              THEN EXCLUDED.stated_goal_source
                    WHEN session.stated_goal_source LIKE 'hook%%'  THEN session.stated_goal_source
                    WHEN EXCLUDED.stated_goal_source LIKE 'hook%%'
                        THEN CASE WHEN EXCLUDED.stated_goal IS NOT NULL
                                  THEN EXCLUDED.stated_goal_source ELSE session.stated_goal_source END
                    ELSE session.stated_goal_source END,
                stated_goal_tier   = CASE
                    WHEN session.stated_goal IS NULL              THEN EXCLUDED.stated_goal_tier
                    WHEN session.stated_goal_source LIKE 'hook%%'  THEN session.stated_goal_tier
                    WHEN EXCLUDED.stated_goal_source LIKE 'hook%%'
                        THEN CASE WHEN EXCLUDED.stated_goal IS NOT NULL
                                  THEN EXCLUDED.stated_goal_tier ELSE session.stated_goal_tier END
                    ELSE session.stated_goal_tier END,
                produced_by     = COALESCE(EXCLUDED.produced_by, session.produced_by),
                -- The hook says 'hybrid' (a human and a model together) and backfill says 'ai'
                -- because a transcript cannot see the human. Same rule as the rest.
                actor_type      = CASE WHEN session.registration_source = 'hook'
                                        AND EXCLUDED.registration_source <> 'hook'
                                       THEN COALESCE(session.actor_type, EXCLUDED.actor_type)
                                       ELSE COALESCE(EXCLUDED.actor_type, session.actor_type) END,
                -- A RATCHET, hook > manual > backfill, and the reason 0136 exists. This column
                -- was previously absent from this SET list, which is first-write-wins by
                -- omission: a session backfilled before the hook was installed kept
                -- 'backfill' forever, so `WHERE registration_source='hook'` undercounted live
                -- registration by exactly the sessions backfill happened to reach first. Now
                -- it records that a first-hand registration happened at all, whenever it did.
                registration_source = CASE
                    WHEN 'hook'   IN (session.registration_source, EXCLUDED.registration_source)
                        THEN 'hook'
                    WHEN 'manual' IN (session.registration_source, EXCLUDED.registration_source)
                        THEN 'manual'
                    ELSE 'backfill' END,
                -- ended_at, end_reason and registered_at are deliberately NOT in this list.
                -- `session register` is not an end-observer and must never be able to move an
                -- end, in either direction; `session end` owns ended_at and only ever
                -- COALESCEs onto it. Stated here because 0136 was filed on the belief that
                -- this verb cleared ended_at, which absence alone did not make obvious.
                -- ingest.session_observed_facts_monotonic() enforces the same thing against
                -- writers that are not this verb.
                updated_at      = now()
            -- `actor_type` is RETURNED so the envelope carries the merged value rather than the
            -- argument (task 0300). The CASE above can choose `session.actor_type` over
            -- EXCLUDED's, so the two are not always equal, and the event must state the row.
            RETURNING session_key, state, actor_type::text AS actor_type, (xmax = 0) AS inserted
            """,
            (session_key, session_id, agent_id, kind, harness, harness_version, entrypoint,
             model, model_source, permission_mode, git_branch, is_sidechain,
             project_root_dir, workdir, workdir_source, config.host_id() if workdir else None,
             config.path_class(workdir) if workdir else None,
             _norm_ts(started_at), _norm_ts(last_activity_at), time_source,
             parent_session_key, parent_source, workflow_id,
             turns_user, turns_assistant, turns_source,
             tok.get("input"), tok.get("output"), tok.get("cache_read"), tok.get("cache_creation"),
             tok.get("messages"), tok.get("usage_records"), tok.get("source"),
             cost_usd, cost_source,
             stated_goal, stated_goal_source, stated_goal_tier,
             produced_by, actor_type, registration_source, state),
        )
        row = c.fetchone()
        result = {"session_key": row["session_key"], "created": existing is None,
                  "state": row["state"], "event": None}
        if emit_event and existing is None:
            result["event"] = events.emit(
                c, events.SESSION_STARTED, started_at or _now(), session_key,
                {"kind": kind, "harness": harness, "harness_version": harness_version,
                 "model": model, "workdir": workdir, "workdir_host_id": config.host_id(),
                 "parent_session_key": parent_session_key, "workflow_id": workflow_id,
                 "entrypoint": entrypoint, "permission_mode": permission_mode,
                 "git_branch": git_branch, "stated_goal": stated_goal,
                 "stated_goal_tier": stated_goal_tier,
                 "registration_source": registration_source},
                # The merged value off the RETURNING clause above, not the argument (task 0300).
                actor_type=row["actor_type"],
            )
        return result

    if cur is not None:
        return _run(cur)
    with store.transaction() as c:
        return _run(c)


# ---------------------------------------------------------------------------
# session end
# ---------------------------------------------------------------------------
def session_end(
    session_key: str,
    *,
    ended_at: str | None = None,
    end_reason: str | None = None,
    turns_user: int | None = None,
    turns_assistant: int | None = None,
    tokens: dict[str, int | str | None] | None = None,
    emit_event: bool = True,
    cur=None,
) -> dict[str, Any]:
    """Close a session. Ending an unknown session is an error, not a silent insert.

    A `session end` that auto-created its subject would let a missed registration look like a
    complete record, which is the exact "registry with unknown gaps" failure this lane exists
    to avoid. The caller must register first.
    """
    tok = tokens or {}
    when = ended_at or _now()

    def _run(c):
        if profiles.is_brain():
            c.execute("SELECT id, ended_at FROM session WHERE id = %s", (session_key,))
            r = c.fetchone()
            if r is None:
                raise KeyError(f"session end: no session registered with key {session_key!r}")
            already = r["ended_at"] is not None
            c.execute(profiles.BRAIN_SESSION_END, {"id": session_key, "ended_at": when})
            out = dict(c.fetchone())
            out.update({"already_ended": already, "event": None, "profile": "brain"})
            if emit_event and not already:
                out["event"] = events.emit(
                    c, events.SESSION_ENDED, when, session_key,
                    {"end_reason": end_reason, "turns_user": turns_user,
                     "turns_assistant": turns_assistant, "tokens": tok or None},
                    # Off BRAIN_SESSION_END's RETURNING, so `session.ended` and `session.started`
                    # agree with the row and with each other by construction (task 0300). This
                    # verb never writes the column, so what it reads is what register established.
                    actor_type=out["actor_type"])
            return out

        c.execute("SELECT session_key, state FROM session WHERE session_key = %s", (session_key,))
        row = c.fetchone()
        if row is None:
            raise KeyError(f"session end: no session registered with key {session_key!r}")
        already = row["state"] == "ended"
        c.execute(
            """UPDATE session SET
                 ended_at   = COALESCE(%s, ended_at),
                 end_reason = COALESCE(%s, end_reason),
                 state      = 'ended',
                 turns_user = COALESCE(%s, turns_user),
                 turns_assistant = COALESCE(%s, turns_assistant),
                 tokens_input = COALESCE(%s, tokens_input),
                 tokens_output = COALESCE(%s, tokens_output),
                 tokens_cache_read = COALESCE(%s, tokens_cache_read),
                 tokens_cache_creation = COALESCE(%s, tokens_cache_creation),
                 tokens_messages = COALESCE(%s, tokens_messages),
                 tokens_usage_records = COALESCE(%s, tokens_usage_records),
                 tokens_source = COALESCE(%s, tokens_source),
                 updated_at = now()
               WHERE session_key = %s
               -- `actor_type` for the `session.ended` envelope (task 0300). Not in the SET list
               -- above and never written here, so this reads what `session register` established.
               RETURNING session_key, ended_at, end_reason, actor_type""",
            (when, end_reason, turns_user, turns_assistant,
             tok.get("input"), tok.get("output"), tok.get("cache_read"), tok.get("cache_creation"),
             tok.get("messages"), tok.get("usage_records"), tok.get("source"),
             session_key),
        )
        out = dict(c.fetchone())
        out["already_ended"] = already
        out["event"] = None
        if emit_event and not already:
            out["event"] = events.emit(
                c, events.SESSION_ENDED, when, session_key,
                {"end_reason": end_reason, "turns_user": turns_user,
                 "turns_assistant": turns_assistant, "tokens": tok or None},
                actor_type=out["actor_type"],          # the row's, see the RETURNING above
            )
        return out

    if cur is not None:
        return _run(cur)
    with store.transaction() as c:
        return _run(c)


# ---------------------------------------------------------------------------
# session link-parent
# ---------------------------------------------------------------------------
def session_link_parent(
    session_key: str, parent_session_key: str, *, source: str = "hook-arg", cur=None
) -> dict[str, Any]:
    """Assert a parent. Refuses a cycle and refuses a self-link.

    `source` records how the parentage was known. Never pass a value here that came from
    timing correlation: D3's brief forbids inferring a parent from when two sessions ran.
    """
    if session_key == parent_session_key:
        raise ValueError("session link-parent: a session cannot be its own parent")

    # D1's migration 1 names these columns id / parent_session_id and has no parent_source.
    key_col, parent_col = ("id", "parent_session_id") if profiles.is_brain() \
        else ("session_key", "parent_session_key")

    def _run(c):
        c.execute(f"SELECT 1 FROM session WHERE {key_col} = %s", (session_key,))
        if c.fetchone() is None:
            raise KeyError(f"session link-parent: no session registered with key {session_key!r}")
        c.execute(f"SELECT 1 FROM session WHERE {key_col} = %s", (parent_session_key,))
        if c.fetchone() is None:
            # D1's schema has an FK here, so an unknown parent would fail anyway; on the
            # scratch profile it would silently store a dangling pointer. Refuse in both.
            raise KeyError(
                f"session link-parent: no session registered for parent {parent_session_key!r}")
        # Walk up from the proposed parent: if this session is already an ancestor of it,
        # linking would close a cycle.
        c.execute(
            f"""WITH RECURSIVE up AS (
                 SELECT {key_col} AS k, {parent_col} AS p FROM session WHERE {key_col} = %s
                 UNION ALL
                 SELECT s.{key_col}, s.{parent_col} FROM session s JOIN up ON s.{key_col} = up.p)
               SELECT 1 FROM up WHERE k = %s""",
            (parent_session_key, session_key),
        )
        if c.fetchone() is not None:
            raise ValueError(
                f"session link-parent: {parent_session_key!r} already descends from {session_key!r}")
        if profiles.is_brain():
            c.execute(
                """UPDATE session SET parent_session_id = %s WHERE id = %s
                   RETURNING id AS session_key, parent_session_id AS parent_session_key""",
                (parent_session_key, session_key))
            out = dict(c.fetchone())
            # no parent_source column in migration 1; reported back, not silently lost
            out["parent_source"] = f"{source} (NOT STORED: no parent_source column on this profile)"
            return out
        c.execute(
            """UPDATE session SET parent_session_key = %s, parent_source = %s, updated_at = now()
               WHERE session_key = %s RETURNING session_key, parent_session_key, parent_source""",
            (parent_session_key, source, session_key),
        )
        return dict(c.fetchone())

    if cur is not None:
        return _run(cur)
    with store.transaction() as c:
        return _run(c)


# ---------------------------------------------------------------------------
# transcript index
# ---------------------------------------------------------------------------
ORPHAN_REASON_NO_SESSION = "session-not-registered"

_ORPHAN_RECORD = """
INSERT INTO transcript_orphan (session_key, pointer, pointer_host, reason)
VALUES (%(session_key)s, %(pointer)s, %(pointer_host)s, %(reason)s)
ON CONFLICT (pointer_host, pointer) DO UPDATE SET
    session_key  = EXCLUDED.session_key,
    reason       = EXCLUDED.reason,
    last_seen_at = now(),
    times_seen   = transcript_orphan.times_seen + 1,
    resolved_at  = NULL
RETURNING id AS orphan_id, times_seen, first_seen_at
"""

_ORPHAN_RESOLVE = """
UPDATE transcript_orphan SET resolved_at = now()
WHERE pointer_host = %(pointer_host)s AND pointer = %(pointer)s AND resolved_at IS NULL
RETURNING id AS orphan_id
"""


def _note_orphan(c, row: dict[str, Any], *, key: str | None, pointer: str,
                 reason: str | None) -> None:
    """Count a transcript that could not be keyed, or clear one that now can be.

    Inside the caller's transaction on purpose: the index row and the record of what it could
    not carry commit together, so there is no window in which the gap exists and the count of
    gaps does not.

    It does NOT catch its own failures, and that is deliberate rather than an oversight. A
    counter that swallows its own errors counts a subset and reports it as a total, which is
    the failure this whole verb is being fixed for. It is also not theoretical: while 0228 was
    being deployed, `transcript_orphan` existed in the working tree but not yet in the
    database, and one live SessionEnd at 2026-08-16T18:49:40Z rolled back with UndefinedTable
    and indexed nothing. Apply `schema/0002_transcript_orphan.sql` to every database the verbs
    write to; `ingest/bin/ingest init-schema` does it for D3's own.
    """
    args = {"session_key": key, "pointer": pointer, "pointer_host": config.host_id(),
            "reason": reason}
    if reason is None:
        c.execute(_ORPHAN_RESOLVE, args)
        cleared = c.fetchone()
        row["session_key_unresolved"] = False
        if cleared is not None:
            row["orphan_resolved"] = dict(cleared)["orphan_id"]
        return
    c.execute(_ORPHAN_RECORD, args)
    orphan = dict(c.fetchone())
    row["session_key_unresolved"] = True
    row["unresolved_reason"] = reason
    row["unresolved_key"] = key
    row["orphan_id"] = orphan["orphan_id"]
    row["orphan_times_seen"] = orphan["times_seen"]


def transcript_index(
    path: str, *, projects_root: str | None = None, session_key: str | None = None,
    register_missing_session: bool = False, registration_source: str = "backfill", cur=None,
) -> dict[str, Any]:
    """Record a pointer and a hash for one transcript file. Never the blob.

    The file is read to hash it and to count its records. Nothing from it is stored except
    facts about it and the pointer to it.

    A TRANSCRIPT WHOSE SESSION WAS NEVER REGISTERED (task 0228). Both profiles put a foreign
    key on the session column, so indexing a transcript for an unregistered session used to
    raise `ForeignKeyViolation` out of this verb and into the hook, which swallows every
    failure by design. The transcript was then neither indexed nor counted. Three behaviours
    were available and this is why the third was chosen:

      register the session first  REJECTED. `session_end` already refuses to auto-create its
                                  subject, for the stated reason that an invented start time
                                  makes a missed registration look like a complete record.
                                  Fabricating the same row one function over would defeat that
                                  rule rather than honour it, and this path has even less to
                                  go on: no start was witnessed here at all.
      skip the transcript         REJECTED. The pointer, the byte count and the hash are true
                                  facts about a file that exists, measured at the one moment
                                  the file is guaranteed complete. Discarding them because a
                                  different row is missing loses evidence to punish an absence.
      index it with a NULL key    CHOSEN. Both schemas make the session column nullable. The
      and count the gap           facts survive, the FK is respected, and the key that could
                                  not be honoured is written to `ingest.transcript_orphan`, so
                                  the gap is a countable row and not a swallowed traceback.
                                  A later backfill that reconstructs the session relinks the
                                  pointer (the upsert COALESCEs a non-NULL key in) and clears
                                  the orphan, so the open count falls as gaps are closed.

    The nulling is per-write, never a delete: the upsert keeps an existing non-NULL key, so an
    orphaned re-index can never unlink a transcript that was already linked.
    """
    projects_root = projects_root or os.path.expanduser("~/.claude/projects")
    facts = tx.scan(path, projects_root)
    key = session_key or facts.session_key
    # D1's migration 1 names it `id`; the scratch schema names it `session_key`.
    key_col = "id" if profiles.is_brain() else "session_key"

    def _run(c):
        created_session = False
        if key and register_missing_session:
            c.execute(f"SELECT 1 FROM session WHERE {key_col} = %s", (key,))
            if c.fetchone() is None:
                goal, tier, gsource = adjudicate(facts)
                session_register(
                    key, session_id=facts.session_id, agent_id=facts.agent_id,
                    kind=facts.kind, harness=facts.harness,
                    harness_version=facts.harness_version, entrypoint=facts.entrypoint,
                    model=facts.model, model_source=facts.model_source,
                    permission_mode=facts.permission_mode, git_branch=facts.git_branch,
                    is_sidechain=facts.is_sidechain,
                    project_root_dir=facts.project_root_dir, workdir=facts.workdir,
                    workdir_source=facts.workdir_source,
                    started_at=facts.started_at,
                    last_activity_at=facts.ended_at, state="unknown",
                    time_source=facts.time_source,
                    parent_session_key=facts.parent_session_id,
                    parent_source=facts.parent_source, workflow_id=facts.workflow_id,
                    turns_user=facts.turns_user, turns_assistant=facts.turns_assistant,
                    turns_source=facts.turns_source,
                    tokens={"input": facts.tokens_input, "output": facts.tokens_output,
                            "cache_read": facts.tokens_cache_read,
                            "cache_creation": facts.tokens_cache_creation,
                            "messages": facts.tokens_messages,
                            "usage_records": facts.tokens_usage_records,
                            "source": facts.tokens_source},
                    cost_usd=facts.cost_usd, cost_source=facts.cost_source,
                    stated_goal=goal, stated_goal_source=gsource, stated_goal_tier=tier,
                    actor_type="ai", registration_source=registration_source,
                    emit_event=False, cur=c,
                )
                created_session = True

        # A workflow journal is keyless on purpose and is not a gap; only a key that was
        # claimed and cannot be honoured is one.
        claims_key = bool(key) and facts.kind != tx.KIND_WORKFLOW_JOURNAL
        write_key = key if claims_key else None
        unresolved = None
        if claims_key and not created_session:
            c.execute(f"SELECT 1 FROM session WHERE {key_col} = %s", (key,))
            if c.fetchone() is None:
                write_key, unresolved = None, ORPHAN_REASON_NO_SESSION

        if profiles.is_brain():
            c.execute(profiles.BRAIN_TRANSCRIPT_UPSERT, {
                "session_id": write_key,
                "pointer": facts.path, "pointer_host": config.host_id(),
                "sha256": facts.sha256, "bytes": facts.bytes,
                # Migration 17 (task 0142): the producer name goes to produced_by_producer, not
                # to produced_by. A transcript row never resolves anything, so all three of the
                # lineage triple stay NULL and only the component name is written. This is the
                # unconditional stamp -- unlike the session site above there is no id to pass
                # through -- and it is what put 1,138 of the 2,269 moved rows here.
                "harness": facts.harness or "", "produced_by_producer": events.PRODUCER,
            })
            row = dict(c.fetchone())
            row.update({"session_key": key, "kind": facts.kind,
                        "created_session": created_session, "profile": "brain"})
            _note_orphan(c, row, key=key, pointer=facts.path, reason=unresolved)
            return row

        c.execute(
            """INSERT INTO transcript
                 (session_key, kind, host_id, path, path_class, windows_path,
                  bytes, sha256, line_count, bad_json_lines, first_ts, last_ts)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (host_id, path) DO UPDATE SET
                 session_key = COALESCE(EXCLUDED.session_key, transcript.session_key),
                 kind = EXCLUDED.kind, bytes = EXCLUDED.bytes, sha256 = EXCLUDED.sha256,
                 line_count = EXCLUDED.line_count, bad_json_lines = EXCLUDED.bad_json_lines,
                 first_ts = EXCLUDED.first_ts, last_ts = EXCLUDED.last_ts,
                 indexed_at = now(), verified_at = NULL, verify_result = NULL,
                 verify_sha256 = NULL
               RETURNING transcript_id, sha256, bytes""",
            (write_key, facts.kind,
             config.host_id(), facts.path, config.path_class(facts.path),
             config.windows_path(facts.path), facts.bytes, facts.sha256,
             facts.line_count, facts.bad_json_lines, facts.started_at, facts.ended_at),
        )
        row = dict(c.fetchone())
        row["session_key"] = key
        row["kind"] = facts.kind
        row["created_session"] = created_session
        row["profile"] = "scratch"
        _note_orphan(c, row, key=key, pointer=facts.path, reason=unresolved)
        return row

    if cur is not None:
        return _run(cur)
    with store.transaction() as c:
        return _run(c)


# ---------------------------------------------------------------------------
# transcript verify
# ---------------------------------------------------------------------------
#: Where an absence is recorded. Upsert rather than insert: verify is expected to run over the
#: same gone pointer again and again, and 61 rows that become 61,000 identical rows would make
#: the count useless. `first_observed_absent_at` is never touched on conflict -- it is the
#: closest thing the store has to a date of death and a later pass must not overwrite it.
_ABSENCE_RECORD = """
INSERT INTO transcript_absence (pointer, pointer_host, session_key, sha256, bytes,
                                classification, retention_days, age_basis, aged_from)
VALUES (%(pointer)s, %(pointer_host)s, %(session_key)s, %(sha256)s, %(bytes)s,
        %(classification)s, %(retention_days)s, %(age_basis)s, %(aged_from)s)
ON CONFLICT (pointer_host, pointer) DO UPDATE SET
    session_key             = EXCLUDED.session_key,
    classification          = EXCLUDED.classification,
    retention_days          = EXCLUDED.retention_days,
    age_basis               = EXCLUDED.age_basis,
    aged_from               = EXCLUDED.aged_from,
    last_observed_absent_at = now(),
    times_observed          = transcript_absence.times_observed + 1,
    returned_at             = NULL
RETURNING id AS absence_id, times_observed, first_observed_absent_at
"""

#: A verify that FINDS the file closes the absence. Restores happen, and so do mounts that were
#: down when the last pass ran. Without this the loss count could only grow.
_ABSENCE_RETURNED = """
UPDATE transcript_absence SET returned_at = now()
WHERE pointer_host = %(pointer_host)s AND pointer = %(pointer)s AND returned_at IS NULL
RETURNING id AS absence_id
"""

#: The three states of a pointer whose file is gone. See schema/0004_transcript_absence.sql for
#: what the operator does with each; the short version is that only `unexplained` is an event.
ABSENCE_AGED_OUT = "aged-out"
ABSENCE_UNEXPLAINED = "unexplained"
ABSENCE_UNKNOWN_AGE = "unknown-age"


def _classify_absence(c, session_key: str | None, pointer: str | None = None) -> dict[str, Any]:
    """Was this file old enough that the harness deleting it is its designed end of life?

    The whole point of `transcript verify` growing a `missing` verdict was to make a number
    the operator can act on, and a raw count of gone pointers is not one: it reads identically
    whether a retention sweep aged 61 files out on schedule or somebody removed a transcript
    of a session that ended an hour ago. This is the function that tells those apart.

    It is deliberately conservative in one direction. No timestamp means `unknown-age`, never
    `aged-out`. Defaulting an unknown to "routine" is how a real deletion gets filed as
    housekeeping and never looked at again.

    A KEYLESS POINTER IS AGED AGAINST THE SESSION WHOSE DIRECTORY HOLDS IT (task 0359). The
    conservative rule above has one failure mode of its own, and it had arrived: a pointer that
    can never acquire a session re-reports `unknown-age` on every pass, forever, and holds the
    daily unit `failed` forever. Task 0324's own comment names what that does -- "a scheduled
    verify that went red every day for a sweep running on schedule is a verb whose exit code
    stops being read". Workflow journals are indexed with a NULL key on purpose (see
    `transcript_index`: a journal is not a session and claims no key), so they are exactly that
    shape, and `coverage` reports `workflow-journal` as a first-class kind: every journal that
    ages out would be another permanent red.

    The age was never actually missing -- it was one path component away. Measured on the live
    store 2026-08-17/18, the one gone journal is one of EIGHT pointers that left the same
    directory in the same sweep, and its seven siblings (the parent's own transcript and six
    `agent-*.jsonl` in the same `wf_435c4513-de5/`) all classify `aged-out` from timestamps
    between 2026-07-17T18:45:48Z and 19:12:34Z. The harness's retention sweep takes a session's
    tree, not a file, so aging the journal against the session that owns its directory files it
    with the cohort it actually died with rather than inventing a date for it.

    The basis is written as `parent-session-*` and never as `session-*`: the row must not claim
    the timestamp was the pointer's own. And the derivation stays honest about what it cannot
    do -- a path that names no session, or names one with no row and no timestamp, is still
    undatable and still `unknown-age`. That is the case the fixture in
    `tests/test_transcript_absence.py` still witnesses, and it still exits 1.

    Two things this deliberately does NOT do:

      age against the sibling agents   REJECTED, measured. The `agent-*.jsonl` beside a journal
          were written by the same workflow run, so `max(started_at)` over their sessions is
          tighter in principle. Across all 7 indexed journals it moves the age by 0 or 1 day
          (worst case `wf_61751954-003`: 27d by siblings, 28d by parent) and changes no verdict
          at the 30-day horizon, while costing a pointer-prefix join with profile-specific
          column names and aging the journal by a different rule from every other pointer in
          the same directory.
      drop `unknown-age` from the sum  REJECTED, and it is the thing this task exists to not
          do. Task 0335 left the red visible rather than filtering it; `unexplained` shares
          that exit path, so suppressing the pair would blind the sweep to a real early
          deletion. The fix is to give the pointer an age, not to stop reading its absence.

    The limit, stated rather than hidden: `started_at` is a LOWER bound on when the journal was
    last written, so a session resumed after a gap longer than the horizon would age its journal
    out while the file was young. That is the exposure `session-started-at` already carries for
    every other pointer -- all 60 `aged-out` rows on this host use it -- so this is the existing
    rule applied to one more pointer, not a new class of error.
    """
    horizon = config.transcript_retention_days()
    out: dict[str, Any] = {"retention_days": horizon, "age_basis": "none", "aged_from": None,
                           "classification": ABSENCE_UNKNOWN_AGE}
    key, via = session_key, ""
    if not key and pointer:
        key, via = tx.containing_session_id(pointer), "parent-"
    if not key:
        return out
    c.execute(profiles.BRAIN_SESSION_AGE_BASIS if profiles.is_brain()
              else profiles.SCRATCH_SESSION_AGE_BASIS, {"key": key})
    row = c.fetchone()
    if row is None:
        return out
    row = dict(row)
    for column, basis in (("ended_at", "session-ended-at"),
                          ("last_activity_at", "session-last-activity-at"),
                          ("started_at", "session-started-at")):
        if row.get(column) is not None:
            out["age_basis"], out["aged_from"] = via + basis, row[column]
            break
    if out["aged_from"] is None:
        return out
    age = _dt.datetime.now(_dt.timezone.utc) - out["aged_from"]
    out["classification"] = ABSENCE_AGED_OUT if age.days >= horizon else ABSENCE_UNEXPLAINED
    return out


def _note_absence(c, detail: dict[str, Any], row: dict[str, Any], *, gone: bool) -> None:
    """Record that a pointer's file is gone, or that it came back.

    Inside verify's own transaction, for the same reason `_note_orphan` is inside the index's:
    the row that says the file was checked and the row that says what was found commit
    together, so there is no window in which one is true and the other is not.
    """
    args = {"pointer": row["path"], "pointer_host": config.host_id()}
    if not gone:
        c.execute(_ABSENCE_RETURNED, args)
        closed = c.fetchone()
        if closed is not None:
            detail["absence_returned"] = dict(closed)["absence_id"]
        return
    args.update(_classify_absence(c, row.get("session_key"), pointer=row["path"]))
    args.update({"session_key": row.get("session_key"), "sha256": row["sha256"],
                 "bytes": row["bytes"]})
    c.execute(_ABSENCE_RECORD, args)
    absence = dict(c.fetchone())
    detail.update({
        "absence_id": absence["absence_id"],
        "classification": args["classification"],
        "retention_days": args["retention_days"],
        "age_basis": args["age_basis"],
        "aged_from": args["aged_from"],
        "first_observed_absent_at": absence["first_observed_absent_at"],
        "times_observed": absence["times_observed"],
    })


def transcript_verify(*, transcript_id: int | None = None, path: str | None = None,
                      limit: int | None = None, cur=None) -> dict[str, Any]:
    """Re-hash indexed transcripts and record whether the stored hash still holds.

    A mismatch is recorded, never repaired. A transcript that grew is still reported and the
    index is still not quietly restated -- but it is reported as `appended`, not as `mismatch`.

    APPEND IS NOT CORRUPTION, AND THE FILE ITSELF SAYS WHICH ONE IT IS (task 0330). A sweep can
    hash a transcript whose session is still writing to it; the hash is stale the moment it is
    taken, and it was recorded as if it were authoritative. Measured on this host 2026-08-17:
    ten indexed rows re-hashed differently, and all ten were pure growth. So `mismatch` -- the
    one verdict that means somebody rewrote recorded history -- was about to fire ten times for
    a log doing the only thing a log does.

    The test is evidence, not timing: re-hash the FIRST `stored_bytes` bytes of the file on
    disk. If that prefix reproduces the stored sha256, then every byte this lane ever recorded
    is still there unchanged and the file only got longer. That is `appended`. If the prefix
    differs, or the file did not grow, recorded bytes moved and it is `mismatch` as before.
    Both hashes come out of one read: the prefix is a `.copy()` of the running digest taken at
    the byte boundary, so the cost of telling them apart is nothing.

    Two timing-based alternatives were considered and rejected, both measured:

      skip files whose session has no `ended_at`   On the brain profile `ended_at` is NULL for
          every backfilled session by design -- 1,129 of 1,129, see profiles.py -- so this rule
          skips essentially the whole corpus. It does not slow the hazard down, it turns the
          backfill off.
      record that the hash was taken against an open file   A flag written at index time is a
          guess about the future (a session can be resumed and grow again long after it closed),
          and it says nothing about the rows already recorded. The prefix test needs nothing
          stored in advance and answers retroactively, which is what the ten rows needed.

    `appended` does not exit 1 (see `cmd_transcript_verify`) and does not book an absence: the
    file is present and its recorded history verified. What it does not do is claim `match` --
    the stored hash no longer covers the file, and `transcript index` is what makes it cover the
    file again.

    A MISSING FILE IS NOT A FAILED VERIFICATION (task 0324). The harness deletes transcripts on
    a retention horizon, so a pointer whose file is gone is usually that file reaching its
    designed end of life, not an incident. Three things follow, and none of them is deleting
    the row -- the pointer, the hash and the byte count are the only surviving record that the
    transcript ever existed:

      1. `verified_ok` is set to NULL rather than false on the brain profile, so the gone file
         does not sit in the same coverage bucket as a hash mismatch. See profiles.py.
      2. An `ingest.transcript_absence` row records the loss durably, with the evidence copied
         onto it, so the fact survives even a later re-index.
      3. That row is CLASSIFIED against the retention horizon, because `aged-out` is a line in
         a report and `unexplained` -- gone while still inside the horizon -- is the thing
         somebody has to go and look at.

    `unreadable` keeps its `false`: a file that is on disk and will not open is a real failure.
    """
    import hashlib

    def _run(c):
        if transcript_id is not None and profiles.is_brain():
            c.execute(profiles.BRAIN_TRANSCRIPT_SELECT_BY_ID, {"id": transcript_id})
        elif transcript_id is not None:
            c.execute("SELECT transcript_id, path, sha256, bytes, session_key FROM transcript "
                      "WHERE transcript_id = %s", (transcript_id,))
        elif path is not None and profiles.is_brain():
            c.execute(profiles.BRAIN_TRANSCRIPT_SELECT_BY_POINTER,
                      {"host": config.host_id(), "pointer": os.path.abspath(path)})
        elif path is not None:
            c.execute("SELECT transcript_id, path, sha256, bytes, session_key FROM transcript "
                      "WHERE host_id = %s AND path = %s",
                      (config.host_id(), os.path.abspath(path)))
        elif profiles.is_brain():
            c.execute(profiles.BRAIN_TRANSCRIPT_SELECT_FOR_VERIFY,
                      {"host": config.host_id(), "limit": limit or 10})
        else:
            c.execute(
                """SELECT transcript_id, path, sha256, bytes, session_key FROM transcript
                   WHERE host_id = %s ORDER BY verified_at NULLS FIRST, transcript_id LIMIT %s""",
                (config.host_id(), limit or 10),
            )
        rows = c.fetchall()
        summary = {"checked": 0, "match": 0, "appended": 0, "mismatch": 0, "missing": 0,
                   "unreadable": 0,
                   # The split of `missing` that says whether anyone has to act. Present on
                   # every run, zeroes included: a key that only appears when it is non-zero
                   # is a key a caller learns to stop checking for.
                   "missing_by_classification": {ABSENCE_AGED_OUT: 0, ABSENCE_UNEXPLAINED: 0,
                                                 ABSENCE_UNKNOWN_AGE: 0},
                   "absences_closed": 0,
                   "details": []}
        for r in rows:
            summary["checked"] += 1
            actual = None
            prefix_sha = None
            stored_bytes = r["bytes"]
            try:
                h = hashlib.sha256()
                nbytes = 0
                with open(r["path"], "rb") as fh:
                    for block in iter(lambda: fh.read(1 << 20), b""):
                        # The prefix digest is a copy of the running one taken exactly at the
                        # recorded byte count, so one read answers both "is this the same file"
                        # and "is this that file with more on the end". Only the first crossing
                        # of the boundary takes the copy; `prefix_sha is None` is the guard.
                        if prefix_sha is None and stored_bytes is not None \
                                and nbytes + len(block) >= stored_bytes:
                            cut = stored_bytes - nbytes
                            h.update(block[:cut])
                            prefix_sha = h.hexdigest()
                            h.update(block[cut:])
                        else:
                            h.update(block)
                        nbytes += len(block)
                actual = h.hexdigest()
                if actual == r["sha256"]:
                    verdict = "match"
                elif (stored_bytes is not None and nbytes > stored_bytes
                        and prefix_sha == r["sha256"]):
                    # Every recorded byte is still there, unchanged, and the file got longer.
                    verdict = "appended"
                else:
                    verdict = "mismatch"
            except FileNotFoundError:
                verdict = "missing"
                nbytes = None
            except OSError:
                verdict = "unreadable"
                nbytes = None
            summary[verdict] += 1
            detail = {"transcript_id": r["transcript_id"], "path": r["path"], "verdict": verdict,
                      "stored_sha256": r["sha256"], "actual_sha256": actual,
                      "stored_bytes": r["bytes"], "actual_bytes": nbytes}
            if verdict == "appended":
                # The size of the growth, so the operator reads how far behind the stored hash
                # is without subtracting two numbers, and re-indexing has something to justify.
                detail["appended_bytes"] = nbytes - stored_bytes
            summary["details"].append(detail)

            # `unreadable` books no absence and closes none: the file is there and refusing to
            # open, which is neither a loss nor a return. Calling it either would be a claim
            # this pass has no evidence for.
            if verdict == "missing":
                _note_absence(c, detail, r, gone=True)
                summary["missing_by_classification"][detail["classification"]] += 1
            elif verdict in ("match", "appended", "mismatch"):
                _note_absence(c, detail, r, gone=False)
                if "absence_returned" in detail:
                    summary["absences_closed"] += 1

            if profiles.is_brain():
                # NULL for `missing` since task 0324, so a file that reached its designed end of
                # life is not filed next to a hash mismatch. NULL for `appended` since task 0330,
                # for the same reason read the other way: the row WAS checked, and the answer is
                # neither a pass (the stored hash does not cover the file on disk) nor a fail
                # (nothing recorded was altered). `false` stays what it has always been -- the
                # corruption signal -- and `unreadable` keeps it, because a file that is present
                # and will not open IS a failed verification. The two NULL causes stay apart in
                # the report by the thing that differs on disk: `missing` books an open
                # `ingest.transcript_absence` row and `appended` never does. See
                # profiles.BRAIN_TRANSCRIPT_MARK_VERIFIED and queries.brain_coverage_report.
                c.execute(profiles.BRAIN_TRANSCRIPT_MARK_VERIFIED,
                          {"ok": None if verdict in ("missing", "appended")
                                 else verdict == "match",
                           "id": r["transcript_id"]})
            else:
                # The scratch profile's `verify_result` is text and holds the verdict verbatim,
                # so `appended` needs nothing collapsed. `verify_sha256` carries the hash the
                # file has NOW for both verdicts that found one and disagreed -- for a mismatch
                # it is the evidence, for an append it is what a re-index would store.
                c.execute(
                    """UPDATE transcript SET verified_at = now(), verify_result = %s, verify_sha256 = %s
                       WHERE transcript_id = %s""",
                    (verdict, actual if verdict in ("mismatch", "appended") else None,
                     r["transcript_id"]),
                )
        return summary

    if cur is not None:
        return _run(cur)
    with store.transaction() as c:
        return _run(c)
