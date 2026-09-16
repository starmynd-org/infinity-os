"""Take-control: the operator seizes one live run, and the run carries the mark forever.

v1 refused this by name -- *"do not build a chat with the admiral"* -- and the operator has
overruled it in writing (`00-COMMANDER-HANDOFF.md`, the first of two deliberate overrules). The
overrule is bounded by four conditions and this module is where three of them are enforced. The
fourth is the UI's.

    1. A mode entered deliberately, per terminal.   -> `take` refuses a run nobody is holding,
                                                       and the console's consequence panel is the
                                                       only path to it.
    2. Every message, both directions, on the thread. -> NOT here. That is the existing `msg`
                                                       verb, which already writes `brain.message`
                                                       AND a `brain.thread` row when it carries a
                                                       task. Take-control adds no private message
                                                       path: a second one would be the narrow
                                                       waist broken at exactly the place the
                                                       overrule was granted.
    3. Control ends with the run.                    -> `release`, and `active_steering` reads
                                                       the run row, so a run that ended is not
                                                       steerable whatever the thread says.
    4. A steered run is MARKED, and excluded from    -> `is_steered` / `steered_tasks`, read by
       auto-accept and from the measurement.            `accept.py`. Property 4 is the one the
                                                       brief says is easiest to forget.

WHY THE MARK IS A THREAD ROW AND NOT A COLUMN. `brain.run` has no `steered` column and adding one
is a migration; the Claude Code permission classifier refuses an agent's write to the live
database, so a column would mean the mark could not exist until the operator ran DDL by hand --
and until then take-control would be shippable with property 4 missing, which is precisely the
outcome the brief warns about. `brain.thread` is append-only, it is what `show --full`
reconstructs a run from, and the auto-accept measurement ALREADY uses the same shape: a
`kind='note'` row whose text starts with a fixed tag (`accept.MEASURE_TAG`). This is that shipped
pattern, not a new one.

WHAT STOPS AN AGENT MARKING ITS OWN WORK STEERED. Two things, and the first is the real one.

**The writer asks the database who it is.** `_the_human` calls `brain.current_human()`, which
answers from `session_user` and returns NULL for `brain_runtime` -- the login every agent surface
connects as. So a mark can only be written from a connection the database knows as a human, which
is V00's rule that human identity is a database login and not a flag. The name that lands in
`from_agent` is the database's answer, never the caller's `by=` string.

**The reader ignores a mark from a fleet name anyway.** `steered_tasks` counts a mark only when
its `from_agent` has no row in `brain.agent` -- the same membership test `accept_work` uses to
refuse a fleet agent as an acceptor. That covers the one route the first guard does not: `note`
is a verb the fleet holds, so an agent CAN write the tag text onto its own thread. It has then
written a note. It has not excluded itself from a measurement.

The mark is PERMANENT and `release` does not clear it. Steering is a fact about how the work was
produced, not a mode that can be switched back off, and a record you can silently rewrite is not
a record.
"""

from __future__ import annotations

import store

from .transitions import VerbError, norm_task_id

# ------------------------------------------------------------------ the recorded mark
#
# ONE definition of the wording, used by the writer AND by every reader, for the reason task 0139
# recorded against the measurement it is modelled on: a reader that greps for a string the writer
# no longer emits scores nothing and says so in no way at all.

STEER_TAG = "[steering] "
SEIZED = STEER_TAG + "SEIZED "
RELEASED = STEER_TAG + "RELEASED "
EXCLUDED = STEER_TAG + "EXCLUDED "

#: What the operator is told, on screen, before he can seize anything. The consequence panel
#: renders this exact sentence, and so does the thread row, so the record and the warning cannot
#: drift apart.
CONSEQUENCE = (
    "Every word both of you say lands on the thread. This run will be marked steered: excluded "
    "from auto-accept and from the disagreement measurement. Control ends when the run ends."
)


def _the_human(ctx, claimed: str) -> str:
    """WHO IS WRITING THIS MARK -- asked of the DATABASE, never taken from the caller's string.

    V00: *human identity is a database login, not a flag.* `brain.current_human()` answers from
    `session_user`, which is fixed at authentication and which no SQL a client can send changes;
    `brain_runtime` -- the login every agent surface connects as -- has no mapping row, cannot
    read the table that would give it one, and cannot write one (migration 20).

    So this returns the name the database says the connection IS, and that name is what lands in
    `from_agent`. A caller passing `by="operator"` from an agent process gets a refusal here
    rather than a forged mark, which is the hole an argument-only check would leave: the reader's
    "not a fleet agent" test would have passed on a string any agent could type.

    Reaching this means the call opened the OPERATOR login, which `store.apply` does when the
    caller passes `as_operator=True`. A caller that forgets is refused and told what to pass.

    The residual is the one `store/SECRETS.md` already records for every role on a
    `local-attended` host: one 0700 directory under one UID, so an OS process that can read the
    operator's secret can connect as the operator. That boundary is the system's, not this
    verb's, and this verb is exactly as strong as `actor_type = 'human'` is.
    """
    human = ctx.scalar("SELECT brain.current_human()")
    if not human:
        raise VerbError(
            "refusing to write a steering mark from a connection the database does not know as "
            "a human. Take-control is the OPERATOR seizing a terminal, the mark removes the run "
            "from the auto-accept measurement, and a mark any agent could write would let an "
            "agent remove its own work from the measurement of agents' work. Call this as the "
            "operator (`as_operator=True`, which opens the operator login); `brain.current_human()"
            "` returned NULL for this session.",
            code=6)
    if claimed and claimed != human:
        raise VerbError(
            f"this call says it is {claimed!r} and the database says the connection is {human!r}. "
            f"Refusing rather than recording either one: the mark is evidence about who steered.",
            code=6)
    if ctx.one("SELECT 1 FROM brain.agent WHERE name = %s", (human,)):
        raise VerbError(
            f"refusing to record {human} as the operator of a steering session: that name is a "
            f"fleet agent. Take-control is the operator seizing a terminal, not a terminal "
            f"seizing itself.", code=6)
    return human


@store.transition("steer take")
def take(ctx, *, id, agent, by="operator", attempt=None, as_operator=True):
    """Seize one live run. Deliberate, per terminal, and refused on a run nobody is running.

    `agent` is the terminal being seized and `by` is the human doing it. Both are recorded
    because the thread row is the whole evidence that the run was co-authored.

    Idempotent: seizing a run already under control returns the existing session rather than
    writing a second mark, so a double-click does not put two SEIZED rows on one thread.
    """
    tid = norm_task_id(id)
    by = (by or "").strip()
    agent = (agent or "").strip()
    if not agent:
        raise VerbError("refusing to seize a run with no terminal named.")
    by = _the_human(ctx, by if by != "operator" else "")

    row = ctx.one("SELECT id, state, claimed_by, attempts FROM brain.work_item WHERE id = %s",
                  (tid,))
    if not row:
        raise VerbError(f"no such work item: {tid}")
    held = (row.get("claimed_by") or "").strip()
    if row.get("state") != "active" or held != agent:
        raise VerbError(
            f"refusing to seize {tid}: state={row.get('state')!r} claimed_by="
            f"{held or 'nobody'!r}, and take-control is control of a RUN. There is no run here "
            f"for {agent} to steer, and a steering session over nothing would mark the task "
            f"steered -- excluding it from the measurement -- for a conversation that reached "
            f"no terminal.",
            code=6)

    n = int(attempt if attempt is not None else (row.get("attempts") or 1))
    live = active_steering_in(ctx, tid)
    if live:
        return {"id": tid, "agent": agent, "attempt": n, "by": live["by"], "already": True,
                "seq": live["seq"]}

    ctx.actor = by
    ctx.thread(tid, "note",
               f"{SEIZED}by {by} · {agent} · attempt {n}. {CONSEQUENCE}")
    return {"id": tid, "agent": agent, "attempt": n, "by": by, "already": False}


@store.transition("steer release")
def release(ctx, *, id, by="operator", agent="", attempt=None, as_operator=True):
    """End the steering session. THE MARK STAYS.

    Releasing control does not un-steer the run and never will: the operator's words are already
    on the thread and the work is already co-authored. What this row records is when he stopped,
    which is what a reader of the trail needs and what the closing seam renders.
    """
    tid = norm_task_id(id)
    by = _the_human(ctx, (by or "").strip() if (by or "").strip() != "operator" else "")
    live = active_steering_in(ctx, tid)
    if not live:
        raise VerbError(
            f"{tid} is not under control right now, so there is nothing to release. The steered "
            f"mark, if one was ever written, stays on the thread either way.")
    n = int(attempt if attempt is not None else live.get("attempt") or 1)
    who = (agent or live.get("agent") or "").strip()
    ctx.actor = by
    ctx.thread(tid, "note",
               f"{RELEASED}by {by} · {who} · attempt {n}. The steered mark stays: it is a fact "
               f"about how this run was produced, not a mode that can be switched back off.")
    return {"id": tid, "agent": who, "attempt": n, "by": by}


# ------------------------------------------------------------------ readers
#
# Two flavours of every read: one taking a transaction context (so `accept.py` can ask INSIDE the
# `done` transaction that triggered the measurement, and see marks that transaction can see), one
# opening its own read session (so the console can ask). The predicate itself is written once.

_MARKS = (
    "SELECT seq, ts, from_agent, text FROM brain.thread "
    " WHERE work_item_id = %s AND kind = 'note' AND text LIKE %s "
    "   AND from_agent NOT IN (SELECT name FROM brain.agent) "
    " ORDER BY seq")

_STEERED_IDS = (
    "SELECT DISTINCT work_item_id FROM brain.thread "
    " WHERE kind = 'note' AND text LIKE %s "
    "   AND from_agent NOT IN (SELECT name FROM brain.agent)")


def _parse(text: str) -> dict:
    """`SEIZED by <by> · <agent> · attempt <n>.` -> the three fields, or empty strings.

    Deliberately forgiving: a mark whose tail does not parse is still a mark. The predicate that
    matters -- "was this run steered" -- turns on the prefix, never on this.
    """
    out = {"by": "", "agent": "", "attempt": 0}
    head = text.split(".", 1)[0]
    if " by " in head:
        head = head.split(" by ", 1)[1]
    parts = [p.strip() for p in head.split("·")]
    if parts:
        out["by"] = parts[0]
    if len(parts) > 1:
        out["agent"] = parts[1]
    if len(parts) > 2 and parts[2].lower().startswith("attempt"):
        tail = parts[2].split()[-1]
        out["attempt"] = int(tail) if tail.isdigit() else 0
    return out


def marks_in(ctx, task_id: str) -> list:
    rows = ctx.execute(_MARKS, (task_id, STEER_TAG + "%"))
    return [dict(r, **_parse(r["text"])) for r in rows]


def marks(task_id: str) -> list:
    """Every steering mark on one task, oldest first, agent-written tags excluded."""
    with store.read() as s:
        rows = s.query(_MARKS, (task_id, STEER_TAG + "%"))
    return [dict(r, **_parse(r["text"])) for r in rows]


def _active(rows: list) -> dict | None:
    """The open session, if there is one: the last SEIZED with no RELEASED after it."""
    live = None
    for r in rows:
        if r["text"].startswith(SEIZED):
            live = r
        elif r["text"].startswith(RELEASED):
            live = None
    return live


def active_steering_in(ctx, task_id: str) -> dict | None:
    return _active(marks_in(ctx, task_id))


def active_steering(task_id: str) -> dict | None:
    """Is the operator holding this run right now? Condition 3 is enforced by the CALLER too.

    This answers only the thread's half. `control ends with the run` also means a run whose row
    has ended is not under control however the thread reads, and the console checks the run row
    before it renders the steering band. Both halves, because a session that outlived its run
    would put a pinned `YOU ARE STEERING` header over a dead terminal.
    """
    return _active(marks(task_id))


def is_steered_in(ctx, task_id: str) -> bool:
    return any(r["text"].startswith(SEIZED) for r in marks_in(ctx, task_id))


def is_steered(task_id: str) -> bool:
    """Property 4's predicate. Permanent: a released session is still a steered run."""
    return any(r["text"].startswith(SEIZED) for r in marks(task_id))


def steered_tasks() -> set:
    """Every task carrying a steering mark. What `accept.measurements()` subtracts."""
    with store.read() as s:
        rows = s.query(_STEERED_IDS, (SEIZED + "%",))
    return {r["work_item_id"] for r in rows}
