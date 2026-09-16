"""Every read the dispatch verbs need.

Reads live apart from writes because `store.read()` hands out a Postgres READ ONLY session:
these functions are exposed broadly precisely because none of them can write. There is no flag
that turns that off, and adding one would be the change that quietly ends the narrow waist.

Nothing here truncates. Truncation is `render.py`'s job, on the way to a terminal, and D00 rule
11 is the reason: store full text, truncate only renderings.
"""

from __future__ import annotations

import store
from store import schema

from .signals import (
    CONSERVATIVE,
    LEVELLED_SIGNALS,
    gate_reasons,
    score_terms,
    signal_level,
    signal_weights,
)

STATES = ("inbox", "active", "done", "blocked", "cancelled")


def _s(role="runtime"):
    return store.read(role)


def task(tid: str):
    with _s() as s:
        return s.one("SELECT * FROM brain.work_item WHERE id = %s", (tid,))


def tasks(state=None, lane=None):
    q = "SELECT * FROM brain.work_item WHERE true"
    p = []
    if state:
        q += " AND state = %s"
        p.append(state)
    if lane:
        q += " AND lane = %s"
        p.append(lane)
    q += " ORDER BY priority ASC, id ASC"
    with _s() as s:
        return s.query(q, p)


def counts():
    with _s() as s:
        rows = s.query("SELECT state, count(*) AS n FROM brain.work_item GROUP BY state")
    out = {st: 0 for st in STATES}
    out.update({r["state"]: r["n"] for r in rows})
    return out


def thread(tid: str):
    with _s() as s:
        return s.query(
            "SELECT seq, ts, from_agent, to_agent, kind, text FROM brain.thread "
            "WHERE work_item_id = %s ORDER BY seq", (tid,))


def artifacts(task_id=None, since_hours=None, agent=None, kinds=()):
    q = ("SELECT seq, work_item_id, ts, first_seen, agent, path, path_host, kind, note, "
         "exists_at_record FROM brain.artifact WHERE true")
    p = []
    if task_id:
        q += " AND work_item_id = %s"
        p.append(task_id)
    if since_hours:
        q += " AND ts > now() - (%s * interval '1 hour')"
        p.append(float(since_hours))
    if agent:
        q += " AND agent = %s"
        p.append(agent)
    if kinds:
        q += " AND kind = ANY(%s)"
        p.append(list(kinds))
    q += " ORDER BY work_item_id, seq"
    with _s() as s:
        return s.query(q, p)


def questions(answered=False, since_hours=12, withdrawn=False):
    """The operator's queue of things only he can decide, and the two ways out of it.

    OPEN EXCLUDES WITHDRAWN (task 0159, migration 31). This list is the thing an interruption is
    spent on, so a question whose task was cancelled has no business in it: on 2026-08-18 the
    operator answered q0140 here, four and a half hours after the task that raised it was
    cancelled, because being in this list is what asks him to decide.

    A withdrawal is not a deletion, so `--withdrawn` shows them, newest first, each with the hand
    that acted.

    BELOW LEDGER 31 THE OPEN LIST IS STILL THE OPEN LIST. `swarm questions` is the operator's
    queue and it served for months before withdrawal existed, so on a store without the column it
    falls back to `answer IS NULL` and keeps printing rather than raising `UndefinedColumn` at
    him -- which is exactly what it did on 2026-08-18, with eleven questions in front of him and
    no way to list them (`docs/SCHEMA-TOLERANCE.md`). `--withdrawn` returns nothing there, and
    nothing is the true answer: a store with nowhere to record a withdrawal holds none.
    """
    withdrawable = schema.question_withdrawal()
    if not withdrawable:
        schema.warn_once("engine.questions", schema.below_31())
    if answered:
        q = ("SELECT * FROM brain.question WHERE answered_at IS NOT NULL "
             "AND answered_at > now() - (%s * interval '1 hour') ORDER BY answered_at DESC")
        p = [float(since_hours)]
    elif withdrawn:
        if not withdrawable:
            return []
        q = ("SELECT * FROM brain.question WHERE withdrawn_at IS NOT NULL "
             "AND withdrawn_at > now() - (%s * interval '1 hour') ORDER BY withdrawn_at DESC")
        p = [float(since_hours)]
    else:
        narrow = " AND withdrawn_at IS NULL" if withdrawable else ""
        q = (f"SELECT * FROM brain.question WHERE answer IS NULL{narrow} "
             "ORDER BY asked_at")
        p = []
    with _s() as s:
        return s.query(q, p)


def question(qid: str):
    with _s() as s:
        return s.one("SELECT * FROM brain.question WHERE id = %s", (qid,))


def unpaged_questions(grace_seconds: float = 120):
    """Open questions that produced no `question.raised` event. Task 0140's absence detector.

    The seam this closes was invisible for a day because both halves reported success: the verb
    printed a question id and the fabric was healthy, and the only thing missing was the join
    between them. Nothing measured the join, so nothing could see it was gone.

    This measures the join and not the wiring, which is the whole point: it asks whether the
    events exist, so it stays true through any future change of transport. `grace_seconds` keeps
    the question the operator is looking at RIGHT NOW out of the finding -- the event is emitted
    after the transaction commits, so for a moment a question legitimately has none.

    `swarm status` and `swarm board` both call this for their open-questions panel, which is why
    a single unguarded predicate here took the whole fleet's situational picture down on
    2026-08-18. Below ledger 31 the withdrawn predicate is dropped and the rest of the detector
    still runs.
    """
    narrow = " AND q.withdrawn_at IS NULL" if schema.question_withdrawal() else ""
    with _s() as s:
        return s.query(
            f"""SELECT q.id, q.asked_by, q.work_item_id, q.asked_at
                 FROM brain.question q
                WHERE q.answer IS NULL{narrow}
                  AND q.asked_at < now() - (%s * interval '1 second')
                  AND NOT EXISTS (SELECT 1 FROM brain.event e
                                   WHERE e.type = 'question.raised'
                                     AND e.subject_id = q.id)
                ORDER BY q.asked_at""",
            [float(grace_seconds)])


def messages(agent: str, unread_only=False):
    with _s() as s:
        read_seq = s.scalar("SELECT inbox_read_seq FROM brain.agent WHERE name = %s", (agent,)) or 0
        q = "SELECT * FROM brain.message WHERE to_agent = %s"
        p = [agent]
        if unread_only:
            q += " AND seq > %s"
            p.append(read_seq)
        q += " ORDER BY seq"
        return s.query(q, p), read_seq


def agents():
    """The fleet, with the provenance of each stop joined on. Task 0273.

    `stop_kind`, `stop_by`, `stop_at` and `stop_record` come from the lifecycle record `stop`
    filed on `brain.message` (see `transitions._lifecycle` for why the record lives there).

    THE JOIN IS KEYED TO THIS STOP AND NOT TO THE LAST ONE, by `m.ts = a.stopped_at`, and the
    equality is exact on purpose. `_lifecycle` writes the message inside the same transaction as
    `stopped_at` and Postgres `now()` is transaction time, so for a stop taken through the verb
    the two timestamps are not merely close, they are the same value.

    Matching "the newest stop record for this agent" instead is wrong in precisely the case this
    task exists to catch, and it was written that way first and caught by
    `test_a_stale_record_does_not_dress_up_a_later_unexplained_stop`: stop an agent through the
    verb, start it, then let it die or be stopped by a hand `UPDATE`, and the stale record dresses
    an unexplained stop in the previous one's actor and reason. That is worse than the bare flag
    it replaces -- a reader acts on a confident wrong answer rather than on a visibly missing one.

    A one-second window was the first attempt and it is not enough: the same test kills the agent
    milliseconds after the signed stop and slips straight through it. Equality also fails in the
    safe direction. If some future change makes `stop` write `clock_timestamp()` instead, this
    join returns nothing and every stop reads "no record of who or why" -- loud, and caught by the
    first assertion in this suite, rather than quietly attributing stops to the wrong actor.

    `stop_kind` is then the tell. A row with `stopped_at` set and `stop_kind` NULL is an agent
    whose column says stopped and which no verb ever explained -- a stop taken before this
    change, a reaper, or a hand edit. `swarm status` prints that case as "STOPPED, no record of
    who or why", and that sentence is the one an admiral needed at 00:56Z on 2026-08-19 and did
    not have.

    Every column read here exists in migration 1, so no store on this host can be too old for
    it (`docs/SCHEMA-TOLERANCE.md`). Consumers that predate this call still work: the four keys
    are additive and nothing was renamed.
    """
    with _s() as s:
        return s.query(
            """SELECT a.name, a.role, a.status, a.work_item_id, a.pid, a.host, a.updated,
                      a.stopped_at, a.tick_fingerprint, a.tick_at, a.inbox_read_seq,
                      EXTRACT(EPOCH FROM (now() - a.updated))::int AS silent,
                      m.kind AS stop_kind, m.from_agent AS stop_by, m.ts AS stop_at,
                      m.text AS stop_record
                 FROM brain.agent a
                 LEFT JOIN LATERAL (
                      SELECT kind, from_agent, ts, text FROM brain.message
                       WHERE to_agent = a.name AND kind = 'stop'
                         AND a.stopped_at IS NOT NULL
                         AND ts = a.stopped_at
                       ORDER BY seq DESC LIMIT 1) m ON true
                ORDER BY a.name""")


def fleet_pause_record():
    """Who paused the fleet, when, and why -- or None if it was never paused on this store.

    The columns have been there since migration 1 (`brain.runtime_flag.set_by`, `set_at`,
    `note`); `pause` has always written `set_by`, `resume` did not until task 0273, and NOTHING
    had ever read any of them. A provenance column no surface displays is the same absence as no
    column at all, which is the finding this task generalises.
    """
    with _s() as s:
        return s.one("SELECT value, set_by, set_at, note FROM brain.runtime_flag "
                     "WHERE key = 'fleet_paused'")


def withheld_by_liveness(seconds: int = 900):
    """Tasks `claim` will not hand out because a live agent is still heartbeating on them.

    The state this names is the 2026-08-18 incident caught one step BEFORE it happens: a task the
    board shows as unclaimed while an agent row says somebody is working it. `transitions.claim`
    refuses those rows, and a refusal nothing can see is how a gate stops being evidence -- so the
    same predicate is readable here, for `claim --explain` and for `doctor`.

    Not restricted to `inbox`. The unowned-but-worked shape the commander traded the double claim
    for during recovery -- kill a runner, its shutdown fires, the task drops to `inbox` with
    `claimed_by` empty while another agent works it -- is the same row seen from the other side,
    and a reader who filtered to one state would see one face of it and call the other clean.
    """
    with _s() as s:
        return s.query(
            """SELECT w.id, w.state, w.lane, w.host, w.claimed_by, w.title,
                      a.name AS live_agent, a.pid,
                      EXTRACT(EPOCH FROM (now() - a.updated))::int AS silent
                 FROM brain.work_item w
                 JOIN brain.agent a ON a.work_item_id = w.id
                WHERE a.status = 'working'
                  AND a.name IS DISTINCT FROM NULLIF(w.claimed_by, '')
                  AND a.updated > now() - make_interval(secs => %s)
                  AND w.state <> 'active'
                ORDER BY w.id""", (seconds,))


def fleet_paused() -> bool:
    # `== 'true'`, not truthiness: `resume` writes 'false' rather than deleting the row, and a
    # bool() on that string is True. See the same note in transitions.claim.
    with _s() as s:
        return str(s.scalar("SELECT value FROM brain.runtime_flag WHERE key = 'fleet_paused'")
                   or "").lower() == "true"


def agent_stopped(name: str) -> bool:
    with _s() as s:
        return s.scalar("SELECT stopped_at FROM brain.agent WHERE name = %s", (name,)) is not None


def objectives(state=None):
    q = "SELECT * FROM brain.objective"
    p = []
    if state:
        q += " WHERE state = %s"
        p.append(state)
    q += " ORDER BY taken_in_at"
    with _s() as s:
        return s.query(q, p)


def objective_seen(source_name, source_signature):
    with _s() as s:
        return s.one("SELECT id FROM brain.objective WHERE source_name = %s "
                     "AND source_signature = %s", (source_name, source_signature)) is not None


def feed(since_minutes=None, agent=None, task_filter=None, kinds=(), limit=200):
    q = "SELECT ts, from_who, to_who, kind, work_item_id, text FROM brain.feed WHERE ts IS NOT NULL"
    p = []
    if since_minutes:
        q += " AND ts > now() - (%s * interval '1 minute')"
        p.append(float(since_minutes))
    if agent:
        q += " AND (from_who = %s OR to_who = %s)"
        p += [agent, agent]
    if task_filter:
        q += " AND work_item_id = %s"
        p.append(task_filter)
    if kinds:
        q += " AND kind = ANY(%s)"
        p.append(list(kinds))
    q += " ORDER BY ts DESC LIMIT %s"
    p.append(int(limit))
    with _s() as s:
        return list(reversed(s.query(q, p)))


def runs(tid: str):
    with _s() as s:
        return s.query("SELECT * FROM brain.run WHERE work_item_id = %s ORDER BY attempt", (tid,))


def signals_of(tid: str, cfg=None):
    """The resolved nine signals for one task, its gate, and its priority score.

    The hard flags come from `brain.work_item_signals`, which ORs them up the whole parent chain
    recursively. The levels are folded here so the explanation can name what was DECLARED
    separately from what it resolved to: a flag a task inherited must be distinguishable from
    one it raised itself, or the trail goes cold one hop up.
    """
    w = signal_weights(cfg)
    with _s() as s:
        t = s.one("SELECT * FROM brain.work_item WHERE id = %s", (tid,))
        if not t:
            return None
        flags = s.one("SELECT external, canon_touching FROM brain.work_item_signals WHERE id = %s",
                      (tid,))
        chain = s.query(
            """WITH RECURSIVE up(id, parent, depth) AS (
                 SELECT id, parent, 0 FROM brain.work_item WHERE id = %s
                 UNION ALL
                 SELECT w.id, w.parent, up.depth + 1 FROM brain.work_item w
                   JOIN up ON w.id = up.parent WHERE up.depth < 64)
               SELECT id, external, canon_touching FROM up
                 JOIN brain.work_item USING (id) WHERE depth > 0 ORDER BY depth""", (tid,))
        age = s.scalar("SELECT EXTRACT(EPOCH FROM (now() - created)) / 86400.0 "
                       "FROM brain.work_item WHERE id = %s", (tid,))

    declared = {f: (t.get(f) or "") for f in LEVELLED_SIGNALS}
    levels = {f: (signal_level(f, declared[f]) or CONSERVATIVE[f]) for f in LEVELLED_SIGNALS}
    ext, canon = bool(flags["external"]), bool(flags["canon_touching"])
    inherited = {
        "external": any(c["external"] for c in chain),
        "canon_touching": any(c["canon_touching"] for c in chain),
    }
    gates = gate_reasons(ext, canon, declared["confidence"], w)
    terms = score_terms(levels, float(age or 0.0), w)
    return {"id": tid, "levels": levels, "declared": declared,
            "external": ext, "canon_touching": canon,
            "inherited_from": [c["id"] for c in chain], "inherited": inherited,
            "gated": bool(gates), "gate_reasons": gates,
            "terms": {k: round(v, 4) for k, v in terms.items()},
            "score": round(sum(terms.values()), 4),
            "priority": t["priority"], "state": t["state"]}


def queue_order(cfg=None, lane=None):
    """The claimable queue in the order `claim` would take it, for `why` and the board.

    This reproduces the claim ORDER BY exactly. It is a read, so it can be wrong about the
    winner by the time anyone acts on it, which is why `claim` does its own ordering inside the
    locking statement rather than calling this.

    IT IS A PREVIEW AND IT IS LOOSER THAN THE CLAIM, which matters to anyone printing it as
    fact. It applies `state = 'inbox'`, `_DEPS_MET` and `_AGENT_CLAIMABLE` -- the same
    predicates `claim` uses, from the same constants, and the last one is not optional here: the
    board and `why` are read by the OPERATOR, and a preview that ranked his own fifteen rows as
    the fleet's next work would be the very confusion migration 26 exists to end -- and it
    applies neither of the two predicates that depend on WHO is claiming: host affinity (`w.host = '' OR w.host = <that agent's label>`) and the lane
    ceiling (task 0122). So a row here can be unclaimable by every agent alive. `host` is
    selected so a caller that wants to be exact can be; `budget_stopped_lanes()` is the other
    half. `why` names both rather than quietly reordering around them, because the alternative
    -- making this exact -- means it can no longer answer "the queue" for the board, only "the
    queue for agent X".
    """
    w = signal_weights(cfg)
    from .transitions import _DEPS_MET, _score_sql
    from .transitions import _AGENT_CLAIMABLE
    q = f"""SELECT w.id, w.title, w.lane, w.priority, w.host, {_score_sql()} AS score
              FROM brain.work_item w
             WHERE w.state = 'inbox' AND {_DEPS_MET}{_AGENT_CLAIMABLE}"""
    p = {k: v for k, v in w.items() if k != "gate_low_confidence"}
    if lane:
        q += " AND w.lane = %(lane)s"
        p["lane"] = lane
    q += " ORDER BY w.priority ASC, score DESC, w.id ASC"
    with _s() as s:
        return s.query(q, p)


def held_and_active(t) -> str:
    """The holder's name if this row says HELD FOR THE OPERATOR while an agent is executing it.

    Empty string otherwise, so it reads as a boolean and prints as a name. One definition, used by
    `swarm show`, `swarm ls`, `swarm doctor` and the mid-run banner on `note`/`artifact`, because
    the whole of task 0119 is four surfaces describing one row and one of them being wrong.
    """
    if not t or t.get("state") != "active" or t.get("agent_claimable"):
        return ""
    return (t.get("claimed_by") or "").strip()


def held_while_active():
    """Every row in that state, for `doctor`. The board says one thing and reality is another.

    Exactly the class the double-claim CRITICAL already covers, and this one was invisible: on
    2026-08-18 the admiral held 0103 sixty seconds after T5 claimed it, `swarm show` rendered
    `NO -- held for the operator`, T5's runner kept executing, and no surface said so.
    """
    with _s() as s:
        return s.query(
            """SELECT w.id, w.lane, w.title, w.claimed_by, w.claimed_at, w.actor_type,
                      w.external, w.canon_touching
                 FROM brain.work_item w
                WHERE w.state = 'active'
                  AND NOT w.agent_claimable
                  AND NULLIF(w.claimed_by, '') IS NOT NULL
                ORDER BY w.id""")


def held_from_the_fleet(state="inbox"):
    """Rows `claim` will not hand out because nobody said the fleet should have them.

    THE SECOND FACE OF `withheld_by_liveness`, and it exists for the same reason: `claim`
    refuses these rows inside its locking statement, and a refusal nothing can see is how a gate
    stops being evidence. `doctor` and `claim --explain` both read this, so the sentence an agent
    is told and the sentence the operator is paged with come from one query.

    `whose` separates the two populations that matter and they are NOT the same finding:

      operator   `actor_type = 'human'`. Working as intended. Nothing to do.
      unclassified   nobody said. Either the operator posted it without `--mine` -- which is how
                     0068 and 0071 landed on live `brain` -- or a fleet surface posted work and
                     forgot `--for-agents`, in which case the task will sit here forever.

    The second is a finding and the first is not, which is why this returns the column rather
    than a count.
    """
    with _s() as s:
        return s.query(
            """SELECT w.id, w.lane, w.state, w.posted_by, w.title, w.created,
                      w.actor_type IS NOT DISTINCT FROM 'human' AS operator_owned
                 FROM brain.work_item w
                WHERE NOT w.agent_claimable
                  AND (%s = '' OR w.state = %s)
                ORDER BY w.id""", (state or "", state or ""))


# ------------------------------------------------------------------ the silent brake
#
# TASK 0177, out of 0122. A fleet idle because its lanes are budget-stopped said nothing, on any
# surface, and silence is the specific failure mode this fleet has been bitten by before
# (2026-08-14: 18 blocked tasks, 11 idle hours, every runner healthy, nothing saying why).
#
# `claim` excludes a budget-stopped lane from its candidate set, which is correct and is silent:
# the agent is handed nothing and cannot tell that from an empty queue. `bin/swarm-run`'s spend
# gate cannot say it either, because that gate is asked about the fleet and the agent and at gate
# time the lane is not decided. So the answer has to be readable from a place all three surfaces
# can ask, and this is that place.
#
# THE SET IS COMPUTED BY THE CLAIM'S OWN SQL, not by a second query that agrees today. Both
# functions below build on `transitions._LANE_BUDGET_CTE`, the same text the candidate query
# filters with, so a change to the definition of "stopped" cannot make the report and the brake
# disagree. That is the whole design constraint here: this task exists because three surfaces
# disagreed, and a fourth opinion would not be an improvement.
#
# WHAT THEY DELIBERATELY DO NOT DO: they do not reconstruct the enforcer's reason string. A lane
# is stopped for one of two reasons (a crossed ceiling with hard stop enabled, or an uncleared
# manual stop), `budget.enforcer.evaluate` composes that sentence with the numbers in it, and
# re-composing it here would be a second reason string free to drift from the first. These name
# the lane and point at `budget check --lane <lane>`, which is the verb whose answer is
# authoritative.


def budget_stopped_lanes():
    """Which lanes may not spend right now: `{'lane', ...}`, or None if the brake is not armed.

    None and `set()` are different answers and the callers say which one they got. A store that
    predates `budget/schema/0003_budget.sql` has no brake at all -- `bin/swarm-run` supports that
    state on purpose and says THE SPEND BRAKE IS NOT ARMED at startup -- and reporting "no lane is
    stopped" there would be asserting a guarantee nothing is enforcing.
    """
    from .transitions import _LANE_BUDGET_CTE, _budget_schema_present
    with _s() as s:
        if not _budget_schema_present(s):
            return None
        return {r["lane"] for r in
                s.query(_LANE_BUDGET_CTE + "SELECT lane FROM budget_stopped_lane ORDER BY lane")}


def dispatch_stalled(cfg=None):
    """The agents that can claim nothing because every lane they claim from is budget-stopped.

    One row per such agent: what it claims from, which of those lanes are stopped, and how much
    otherwise-claimable work is sitting in them. `doctor` turns these into findings; the count is
    what separates "an agent is braked" (a warning) from "work is stranded behind a brake nobody
    can see" (the critical, and the 2026-08-14 shape).

    Live agents, not configured ones. The failure mode is a runner that IS up and heartbeating,
    and an agent in `config.json` that was never started is absent for a reason that has nothing
    to do with spend. An agent with no config entry is skipped rather than guessed at: without its
    lane list there is no claim to make about it.

    `lanes: ["*"]` -- every terminal in the shipped default config -- cannot be answered from the
    config alone, because "every lane it claims from" is then every lane there is. For those the
    question is the one that can actually be measured: is there claimable work, and is every lane
    holding it stopped. An empty board is not a budget finding.
    """
    stopped = budget_stopped_lanes()
    if not stopped:
        # Not armed, or nothing stopped. Either way there is nothing to report, and the healthy
        # fleet pays one catalog lookup plus one anti-join for asking.
        return []
    from .config import agent_config, config, is_planner
    cfg = cfg if cfg is not None else config()
    q = queue_order(cfg)
    out = []
    for a in agents():
        if a["stopped_at"]:
            continue
        acfg = agent_config(a["name"], cfg)
        if not acfg:
            continue
        lanes = acfg.get("lanes", ["*"])
        if is_planner(acfg.get("role", a["role"] or "")) or not lanes:
            # A planner claims nothing, so a lane ceiling cannot be why it is not claiming.
            continue
        any_lane = "*" in lanes
        # Host affinity, applied here because this row is about ONE agent and can be exact where
        # `queue_order` cannot: a task pinned to another machine is not work this agent is
        # missing out on. Doctor reports an unreachable pin separately.
        mine = [r for r in q
                if (any_lane or r["lane"] in lanes)
                and (not r["host"] or r["host"] == (a["host"] or ""))]
        with_work = {r["lane"] for r in mine}
        if any_lane:
            blocked = bool(with_work) and with_work <= stopped
            relevant = sorted(with_work & stopped)
        else:
            blocked = all(l in stopped for l in lanes)
            relevant = sorted(set(lanes) & stopped)
        if not blocked:
            continue
        waiting = [r for r in mine if r["lane"] in stopped]
        out.append({"agent": a["name"], "host": a["host"] or "", "status": a["status"],
                    "any_lane": any_lane, "lanes": list(lanes), "stopped_lanes": relevant,
                    "waiting": len(waiting), "waiting_ids": [r["id"] for r in waiting[:5]]})
    return out


def blocked_waiting():
    with _s() as s:
        return s.query(
            """SELECT w.id, w.title, w.blocked_on, q.text AS question, q.answer,
                      EXTRACT(EPOCH FROM (now() - q.asked_at))::int AS age
                 FROM brain.work_item w
                 LEFT JOIN brain.question q ON q.id = w.blocked_on
                WHERE w.state = 'blocked' ORDER BY w.id""")


def auto_accept_candidates():
    with _s() as s:
        return s.query("SELECT * FROM brain.auto_accept_candidate ORDER BY id")


def brief_window(hours=12):
    with _s() as s:
        return {
            "finished": s.query(
                "SELECT id, title, lane, state, claimed_by, result, finished_at "
                "FROM brain.work_item WHERE finished_at > now() - (%s * interval '1 hour') "
                "ORDER BY finished_at", (float(hours),)),
            "posted": s.query(
                "SELECT id, title, lane, posted_by, created FROM brain.work_item "
                "WHERE created > now() - (%s * interval '1 hour') ORDER BY created",
                (float(hours),)),
            "questions": s.query(
                "SELECT * FROM brain.question WHERE asked_at > now() - (%s * interval '1 hour') "
                "ORDER BY asked_at", (float(hours),)),
            "artifacts": s.query(
                "SELECT work_item_id, path, kind, note, agent, exists_at_record FROM brain.artifact "
                "WHERE ts > now() - (%s * interval '1 hour') ORDER BY work_item_id, seq",
                (float(hours),)),
            "notes": s.query(
                "SELECT work_item_id, from_agent, kind, text, ts FROM brain.thread "
                "WHERE ts > now() - (%s * interval '1 hour') AND kind IN ('note','ask','fail') "
                "ORDER BY ts", (float(hours),)),
        }
