"""Producer 1: session lifecycle. The producer side of the D3 contract.

**D3 owns `session register`, `session end` and the harness hook. This lane owns `event emit`.**
The two meet here and nowhere else: D3 calls `emit_session_started` / `emit_session_ended` after
its own transition commits, and it never touches `brain.event`.

D3 ran in parallel with this lane and crosstalk slot 4 was empty when this was written, so this
module was the contract offered rather than the integration observed. It is deliberately shaped so
D3 needs no knowledge of the fabric beyond two function names: no event type strings in D3's code,
no retention class, no flag arithmetic, and above all no INSERT.

**It is the integration observed as of 2026-08-17 (task 0287)**, and the last sentence of that
paragraph -- "the fabric side needs no change" -- was wrong in four places, each named in
`emit_session_started` below. D3 does not call these functions directly either: it hands an
envelope to `fabric/bin/event-emit`, which is where the mapping lives, because a hook running
on the operator's session-start path is a subprocess and not an import.

**Why a session event is `machine` class.** Sessions are high volume and 14-day retention is the
right tier; the durable record of a session is D3's `session` row and its transcript pointer, not
this event. The event exists so a subscriber can react at 02:00, not so anything is remembered.

**One ordering constraint, found by running it rather than by reading the DDL.**
`brain.event.session_id` is a foreign key to `brain.session(id)`, so emitting `session.started`
with `session_id` set before D3's row exists fails:

    psycopg2.errors.ForeignKeyViolation: insert or update on table "event" violates foreign key
    constraint "event_session_id_fkey"   DETAIL: Key is not present in table "session".

So **`session register` must commit before `event emit` is called**, which is the natural order
anyway: the event announces something that has happened. The two links are kept separate here so
the ordering is a choice rather than a trap: `subject_type`/`subject_id` always carry the session
id as free text and are always safe, and the `session_id` foreign key is set only when
`link_session` is true. `work_item_id` carries the same constraint against `brain.work_item(id)`.
"""

from __future__ import annotations

from .. import emit as _emit


def emit_session_started(session_id: str, *, harness: str = "", agent: str = "",
                         role: str = "", workdir: str = "", stated_goal: str = "",
                         work_item_id: str = None, external: bool = False,
                         canon_touching: bool = False, parent_flags=(),
                         produced_by: str = None, produced_by_producer: str = None,
                         occurred_at=None, payload: dict = None, actor_type: str = None,
                         event_id: str = None, link_session: bool = True) -> dict:
    """One `session.started`. Flags inherit by OR from the parent session or work item (EF-7).

    `parent_flags` is a sequence of `(external, canon_touching)` pairs. A subagent session of a
    flagged parent is flagged, which is the case the one-hop version of the gate misses.

    Four arguments were added by task 0287, when the D3 envelope was actually wired through.
    Each one exists because leaving it out would have written something false:

    `occurred_at` -- WITHOUT IT A REPLAY LIES. The parked envelopes carry the instant the
    session really started; defaulting to `now()` at emit time would have told every
    subscriber that 133 sessions from yesterday began this afternoon. `EVENT-CONTRACT.md`
    says the same thing from the producer's side and this is the parameter that honours it.

    `payload` -- the envelope's own payload dict, passed through verbatim rather than
    re-derived from the named arguments above. The contract fixes that shape and a second
    construction of it here is a second thing to keep in step.

    `produced_by_producer` -- `d3-ingest` is a component name, and since migration 17 a
    component name in `produced_by` is refused by a CHECK. See `_emit._event_emit`.

    `event_id` -- supplied by the caller when it wants replay to be idempotent. The column is
    UNIQUE, so a deterministic id makes a second drain of the same envelope collide instead of
    writing a duplicate event.

    A fifth was added by task 0300, and it replaced a literal rather than filling a hole:

    `actor_type` -- THIS FUNCTION USED TO HARDCODE `"ai"`. It is not a producer's fact to
    invent. `session register` writes `actor_type='hybrid'` for a hook-registered Claude Code
    session, because a Claude Code session is a human and a model together
    (`ingest/bin/claude-session-hook`), and this module asserted `ai` about the very same
    session. Measured on live `brain` on 2026-08-17: 150 session events said `ai` next to a
    `brain.session` row that said `hybrid`, so one lineage walk crossed a row that contradicted
    the row it walked to. The value now arrives in the envelope, read off the RETURNING clause
    of D3's own statement.

    `None` is the honest default and NOT a return of the old bug. It means "no actor was
    recorded", a state `brain.event.actor_type` already holds (the column is nullable and
    `brain.actor_type` admits NULL by construction, `migrations/0001_initial.sql:97`). What the
    old code did was different in kind: it recorded a specific actor nobody had established.
    Unlike `external` / `canon_touching`, which `emit.FlagOmitted` refuses to default because a
    default there is a PERMISSIVE gate, a null actor gates nothing and claims nothing.
    """
    e, c = _emit.inherit(external, canon_touching, *parent_flags)
    return _emit.emit(
        type="session.started", external=e, canon_touching=c, occurred_at=occurred_at,
        subject_type="session", subject_id=session_id,
        session_id=session_id if link_session else None, work_item_id=work_item_id,
        payload_summary=_emit.summarise(payload if payload is not None else {
            "session": session_id, "harness": harness, "agent": agent, "role": role,
            "workdir": workdir, "goal": stated_goal[:400],
        }),
        actor_type=actor_type, produced_by=produced_by,
        produced_by_producer=produced_by_producer, event_id=event_id,
        actor=agent or "session",
    )


def emit_session_ended(session_id: str, *, agent: str = "", outcome: str = "",
                       work_item_id: str = None, external: bool = False,
                       canon_touching: bool = False, parent_flags=(),
                       produced_by: str = None, produced_by_producer: str = None,
                       occurred_at=None, payload: dict = None, actor_type: str = None,
                       event_id: str = None, link_session: bool = True) -> dict:
    """One `session.ended`. `actor_type` carries the same meaning it does above: the value D3's
    `session end` read back off its own UPDATE, never a literal chosen here (task 0300).
    """
    e, c = _emit.inherit(external, canon_touching, *parent_flags)
    return _emit.emit(
        type="session.ended", external=e, canon_touching=c, occurred_at=occurred_at,
        subject_type="session", subject_id=session_id,
        session_id=session_id if link_session else None, work_item_id=work_item_id,
        payload_summary=_emit.summarise(
            payload if payload is not None
            else {"session": session_id, "outcome": outcome[:400]}),
        actor_type=actor_type, produced_by=produced_by,
        produced_by_producer=produced_by_producer, event_id=event_id,
        actor=agent or "session",
    )
