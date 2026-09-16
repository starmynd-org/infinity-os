"""The verbs: `event emit`, `event ack`, `observation open`, `observation close`,
`disposition record`, `subscriber quarantine`, `subscriber release`.

Every producer calls `event emit`. No producer INSERTs. That is not tidiness: it is what keeps
the `producer` role's INSERT-only grant meaningful rather than decorative, because it makes this
file the single place where the hard-flag check, the retention class, the 4096-byte ceiling and
the daily budget are enforced. A second insert path would be a second, unreviewed copy of all
four.

Three constraints D1 published in crosstalk slot 1 shape this file, and each one is load-bearing:

1. **A producer cannot use `RETURNING`.** `INSERT ... RETURNING event_seq` as `brain_producer`
   fails with `permission denied for table event`, because RETURNING needs SELECT on the returned
   columns and the producer deliberately has none. So `emit` supplies its own `event_id` uuid and
   hands that back. The caller learns the uuid it chose; it never learns the sequence number, and
   it has no business knowing one.

2. **`subscriber_cursor` is under FORCED row-level security, keyed on the CONNECTION.** It used to
   key on `SET brain.subscriber`, and that is a string the client picks: one listener set it to
   another listener's name and advanced that listener's cursor from 10 to 4242 (measured
   2026-08-16). Migration 10 keys the policy on `session_user` through `brain.subscriber_role`,
   so `event ack` no longer identifies itself at all -- it asks the database who it is and refuses
   if the answer is not the subscriber it was called for.

3. **There is no NOTIFY trigger in migration 1.** Checked, not assumed: `grep -n 'TRIGGER|NOTIFY'`
   over `migrations/*.sql` finds one COMMENT string and no trigger. Migrations are D1's and this
   lane does not touch them, so the doorbell is rung from inside this verb with `pg_notify`, in
   the same transaction as the INSERT. That is strictly better than a trigger for one reason
   anyway: it cannot fire for a row this verb did not write.

**The doorbell carries only the event id.** The listener reads the row and acts. It also does not
*rely* on the doorbell: a listener catches up by `event_seq > cursor`, so a dropped notification
costs latency and never costs an event. A notification carries no delivery guarantee and building
as though it did is how a fabric silently loses its first event.
"""

from __future__ import annotations

import json
import uuid as _uuid

import store
from store import transitions as _t

from . import types as _types

#: The one channel. A listener LISTENs here and then reads the rows it has not seen.
CHANNEL = "brain_event"


class FlagOmitted(ValueError):
    """`external` or `canon_touching` was not stated. Refused rather than defaulted.

    D1's columns are `NOT NULL` with no default for exactly this reason, and this check exists on
    top of that so the error names the rule rather than the constraint. An event that forgot to
    say whether it touches the outside world is not an unflagged event; it is an event whose flag
    nobody decided.
    """


class PayloadTooLarge(ValueError):
    """Over 4096 bytes. REJECTED, never truncated (EF-1).

    Porting a destructive cut into a store whose whole pitch is that nothing is lost would be the
    worst kind of faithful. The full payload goes behind `payload_ref`.
    """


class BudgetExceeded(RuntimeError):
    """EF-1's 5,000 events/day. A governor, and its limits are documented at the call site."""


class SubscriberIdentityRefused(PermissionError):
    """This connection is not the subscriber the verb was called for.

    RLS refuses it either way, by matching no row. This exists so the refusal is a raised error
    with a name on it rather than a write that quietly touched nothing, because "updated 0 rows"
    is how a listener concludes it acked when it did not.
    """


def _identity(ctx, subscriber: str) -> str:
    """Ask the DATABASE who this connection is. The caller does not get a say.

    `session_user` is fixed at authentication; `brain.subscriber_role` maps it to exactly one
    subscriber and is readable by nobody but the owner. A role with no mapping answers NULL.
    """
    me = ctx.scalar("SELECT brain.current_subscriber()")
    if me is None:
        raise SubscriberIdentityRefused(
            f"this connection maps to no subscriber, so it cannot act as {subscriber!r}. Either "
            f"it is the shared brain_subscriber login (which is nobody, by design) or its role "
            f"was never provisioned: store/bin/provision-subscriber.sh --subscriber {subscriber}"
        )
    if me != subscriber:
        raise SubscriberIdentityRefused(
            f"this connection is {me!r} and the verb was called for {subscriber!r}. A subscriber "
            f"acts on its own cursor and on no other, and since migration 10 it cannot name "
            f"itself into being someone else."
        )
    return me


def _as_flag(value, name: str) -> bool:
    if value is None:
        raise FlagOmitted(
            f"{name} was not stated. An event carrying external: true or canon_touching: true may "
            f"wake a listener to prepare, never to send, spend, deploy, publish or touch canon, "
            f"and that gate cannot run on a flag nobody set. Pass True or False explicitly."
        )
    if not isinstance(value, bool):
        raise FlagOmitted(f"{name} must be a bool, got {type(value).__name__}: {value!r}")
    return value


def inherit(external: bool, canon_touching: bool, *parents) -> tuple[bool, bool]:
    """EF-7: hard flags inherit by OR, and a derived item may never LOWER either flag.

    Two compliant hops defeat a gate that holds only one hop, which is why this is a function
    rather than a convention. `parents` is a sequence of (external, canon_touching) pairs.
    """
    e = _as_flag(external, "external")
    c = _as_flag(canon_touching, "canon_touching")
    for pe, pc in parents:
        e = e or bool(pe)
        c = c or bool(pc)
    return e, c


def _check_budget(actor: str) -> None:
    """EF-1's 5,000/day, and an honest note about what this check can and cannot be.

    The producer role has no SELECT on `event`, by design, so a producer cannot count its own
    writes. The count therefore happens on a separate `runtime` read connection before the write,
    which means it is a governor and not a gate: two concurrent emitters can each see 4,999 and
    both write. That race is acceptable for a volume budget and would not be acceptable for a
    safety flag, which is why the flags are enforced inside the write transaction and this is not.
    """
    with store.read("runtime") as s:
        n = s.scalar(
            "SELECT count(*) FROM brain.event WHERE emitted_at >= date_trunc('day', now())"
        )
    if n is not None and n >= _types.DAILY_EVENT_BUDGET:
        raise BudgetExceeded(
            f"{n} events already emitted today and EF-1's budget is "
            f"{_types.DAILY_EVENT_BUDGET}/day. Refusing to emit. This is a volume contract, not a "
            f"safety gate: if the traffic is real, the budget is the thing to change, in WAGER-2f, "
            f"with the measurement attached."
        )


@_t.transition("event emit", role="producer")
def _event_emit(ctx, *, type: str, external, canon_touching,
                occurred_at=None, department: str = "", lane: str = "",
                subject_type: str = "", subject_id: str = "",
                payload_summary: str = "", payload_ref=None,
                work_item_id=None, session_id=None,
                actor_type=None, produced_by=None, produced_by_ref=None,
                resolution_status=None, produced_by_producer=None, event_id=None) -> str:
    """Write one event and ring the doorbell, in one transaction. Returns the event_id uuid.

    THE LINEAGE TRIPLE IS THREE COLUMNS, NOT ONE, AND IT IS NOT ASSEMBLED HERE.
    `produced_by` alone can say "resolved to this id" and can say nothing. It cannot say
    "a reference was resolved and came back ambiguous", because that state is a NULL producer
    plus a status, and a NULL producer with no status means "nobody asked". Callers pass the
    triple from `brain_adapter.store_join.lineage_columns(resolution)`:

        emit(type=..., **lineage_columns(res))

    One producer of the triple for every lane, so the failure case cannot drift between them.
    This lane never builds it: it accepts three kwargs and puts them in three columns. Omitting
    all three still means "no resolution was attempted", which is the honest default and what
    every existing caller keeps getting. `brain.event_lineage_coherent` refuses an incoherent
    triple, and it is the only thing here that judges one.

    AND THE FOURTH COLUMN IS `produced_by_producer`, WHICH IS NOT PART OF THE TRIPLE.
    Migration 17 split the old single column in two: `produced_by` holds an entity id and
    nothing else, and the name of the RUNTIME COMPONENT that wrote the row goes here. The
    store enforces it -- `event_produced_by_is_not_a_producer` CHECKs
    `produced_by <> 'd3-ingest'` -- so this column is not decoration: without it the ingest
    lane's envelope, whose `produced_by` is literally the string `d3-ingest`, cannot be
    emitted at all. Migration 17's header predicted that refusal and called it correct
    (`migrations/0017_producer_column.sql`, "WHAT ELSE THIS TOUCHES", item 1); this kwarg is
    the other half it named, added by task 0287 when the ingest envelope was first wired
    through. `resolution_status` stays NULL beside it and still means "nobody asked".
    """
    et = _types.resolve(type)
    e, c = inherit(external, canon_touching)

    size = len(payload_summary.encode("utf-8"))
    if size > 4096:
        raise PayloadTooLarge(
            f"payload_summary is {size} bytes and the ceiling is 4096. It is REJECTED, not "
            f"truncated. Put the full payload behind payload_ref and summarise here."
        )
    if size == 4096 and payload_ref is None:
        # Not an error, but the shape that produces one next time. Say so rather than wait.
        pass

    eid = str(event_id or _uuid.uuid4())

    ctx.execute(
        "INSERT INTO brain.event "
        "  (event_id, type, occurred_at, department, lane, subject_type, subject_id, "
        "   payload_summary, payload_ref, retention_class, external, canon_touching, "
        "   work_item_id, session_id, actor_type, produced_by, produced_by_ref, "
        "   resolution_status, produced_by_producer) "
        "VALUES (%s, %s, COALESCE(%s, now()), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
        "        %s, %s, %s, %s)",
        (eid, et.name, occurred_at, department, lane, subject_type, subject_id,
         payload_summary, payload_ref, et.retention_class, e, c,
         work_item_id, session_id, actor_type, produced_by, produced_by_ref,
         resolution_status, produced_by_producer),
    )

    # The doorbell. Only the event id rides it; the listener reads the row and acts. pg_notify
    # needs no grant, which is the one thing a producer with no SELECT can still do.
    ctx.execute("SELECT pg_notify(%s, %s)", (CHANNEL, eid))
    return eid


def emit(*, type: str, external=None, canon_touching=None, actor: str = "",
         check_budget: bool = True, **kw) -> dict:
    """The producer-facing call. Every producer uses this and none of them INSERTs.

    Returns a dict rather than a bare id because EF-4 made consequential-event-emitted a receipt
    booking moment: the caller has to be told a receipt is now due, and telling it is cheaper
    here than discovering the gap in an audit later.
    """
    et = _types.resolve(type)
    if check_budget:
        _check_budget(actor)
    eid = store.apply("event emit", type=type, external=external,
                      canon_touching=canon_touching, actor=actor, **kw)
    return {
        "event_id": eid,
        "type": et.name,
        "retention_class": et.retention_class,
        # EF-4 plus EF-5: the receipt carries caused_by_event_id and approval_ref, and booking it
        # is `receipt book`, which is D2's verb. This lane reports the obligation; it does not
        # reimplement someone else's transition.
        "receipt_due": et.books_receipt,
    }


@_t.transition("event ack", role="subscriber")
def _event_ack(ctx, *, subscriber: str, last_seq: int, declaration_commit: str = "") -> dict:
    """Advance one subscriber's cursor. The ONLY write a listener ever makes.

    The identity check is first and it is not a formality: this transaction runs on a connection
    opened with THIS subscriber's own credential, and `brain.current_subscriber()` reports what
    the database authenticated rather than what the caller typed. RLS would refuse a mismatch by
    matching no row; the check turns that silent no-op into a named error.
    """
    _identity(ctx, subscriber)

    rows = ctx.execute(
        "INSERT INTO brain.subscriber_cursor (subscriber, last_seq, declaration_commit) "
        "VALUES (%s, %s, %s) "
        "ON CONFLICT (subscriber) DO UPDATE "
        "   SET last_seq = GREATEST(brain.subscriber_cursor.last_seq, EXCLUDED.last_seq), "
        "       updated_at = now(), "
        "       declaration_commit = EXCLUDED.declaration_commit "
        " WHERE brain.subscriber_cursor.quarantined_at IS NULL "
        "RETURNING subscriber, last_seq, quarantined_at",
        (subscriber, last_seq, declaration_commit),
    )
    # GREATEST, not assignment: a cursor never moves backwards. A replay that re-acks an older
    # sequence must not un-see the events between.
    # The WHERE clause is the quarantine mechanism: a quarantined cursor stops advancing, so lag
    # climbs and stays climbing until a human clears it.
    return rows[0] if rows else {"subscriber": subscriber, "advanced": False,
                                 "reason": "quarantined or not own row"}


@_t.transition("subscriber quarantine", role="subscriber")
def _subscriber_quarantine(ctx, *, subscriber: str, reason: str) -> dict:
    """A listener stops itself. It cannot clear itself; that asymmetry is the point.

    Quarantine is deliberately self-service and release deliberately is not. A listener that
    could clear its own quarantine would retry a poisoned event forever and the operator would
    see a lag graph that sawtooths instead of a condition that demands attention.

    Self-service means ITSELF. Quarantining a different listener would be a denial of service one
    subscriber could aim at another, so the same identity check `event ack` runs applies here, and
    the detector's quarantine (below) is a separate verb on a separate role.
    """
    _identity(ctx, subscriber)
    rows = ctx.execute(
        "UPDATE brain.subscriber_cursor "
        "   SET quarantined_at = COALESCE(quarantined_at, now()), quarantine_reason = %s, "
        "       updated_at = now() "
        " WHERE subscriber = %s "
        "RETURNING subscriber, last_seq, quarantined_at, quarantine_reason",
        (reason, subscriber),
    )
    return rows[0] if rows else {"subscriber": subscriber, "quarantined": False}


@_t.transition("subscriber quarantine detected", role="runtime")
def _subscriber_quarantine_detected(ctx, *, subscriber: str, reason: str,
                                    detected_by: str = "fabric.lag") -> dict:
    """Quarantine a subscriber the HEALTH SIGNAL condemned, not one that condemned itself.

    A poisoned cursor is exactly the case the self-service verb above cannot reach: the listener
    whose cursor was moved is running fine and has nothing to report, and the one thing that can
    see the damage is the thing reading the lag. So this verb runs as `runtime` -- no listener
    holds that role, so no listener can quarantine a rival with it -- and it is called by
    `fabric.lag.enforce`.

    It is separate from `subscriber quarantine` rather than a flag on it because the two differ in
    who may call them, and a role difference expressed as a keyword argument is a role difference
    that survives exactly until someone passes the keyword.
    """
    if not reason:
        raise ValueError("a quarantine with no reason is a listener that stops for no stated "
                         "cause, which is the failure this fabric exists to make visible.")
    rows = ctx.execute(
        "UPDATE brain.subscriber_cursor "
        "   SET quarantined_at = COALESCE(quarantined_at, now()), "
        "       quarantine_reason = %s, updated_at = now() "
        " WHERE subscriber = %s "
        "RETURNING subscriber, last_seq, high_water_seq, quarantined_at, quarantine_reason",
        (f"{reason} (detected by {detected_by})", subscriber),
    )
    return rows[0] if rows else {"subscriber": subscriber, "quarantined": False}


@_t.transition("subscriber release", role="runtime")
def _subscriber_release(ctx, *, subscriber: str, released_by: str) -> dict:
    """Un-quarantine. `runtime`, never `subscriber`: this is a human's act, through a human's role.

    The listener holds the `subscriber` role and the RLS policy `cursor_admin` grants only
    `owner` and `runtime` a write outside their own row, so this verb is unreachable from inside
    a listener however the listener is written.
    """
    if not released_by:
        raise ValueError(
            "released_by is required. A quarantine cleared by nobody is a quarantine that will "
            "be cleared again by nobody."
        )
    rows = ctx.execute(
        "UPDATE brain.subscriber_cursor "
        "   SET quarantined_at = NULL, "
        "       quarantine_reason = %s, "
        "       updated_at = now() "
        " WHERE subscriber = %s "
        "RETURNING subscriber, last_seq, quarantined_at",
        (f"released by {released_by}", subscriber),
    )
    return rows[0] if rows else {"subscriber": subscriber, "released": False}


@_t.transition("observation open", role="runtime")
def _observation_open(ctx, *, event_id: int, subject_type: str = "", subject_id: str = "",
                      kind: str = "", text: str = "", actor_type=None, produced_by=None,
                      produced_by_ref=None, resolution_status=None,
                      produced_by_producer=None) -> int:
    """EF-2's second record. Two records are the NORMAL path, with the O-test in the rule.

    This is a separate verb from `event emit` and it cannot be folded into it, because the two
    writes run as two different Postgres roles: `producer` holds INSERT on `event` and nothing
    else, `runtime` holds INSERT on `observation` and only SELECT on `event`. One verb one
    transaction still holds for each; what does not hold is one transaction across both, and that
    is a direct consequence of the three-role split (EF-11) rather than a shortcut here. Logged to
    crosstalk slot 8.
    """
    # The lineage triple, same contract as `event emit` above: built by
    # `brain_adapter.store_join.lineage_columns`, never here.
    #
    # `produced_by_producer` joined the INSERT for S2 (2026-09-01) and it is migration 17's
    # column, not a new idea. A CLI caller is a PRODUCER NAME (`T2`, `operator`), not an entity
    # id, and since 17 `produced_by IS NOT NULL` means "an entity id" UNCONDITIONALLY, because a
    # lineage walker cannot otherwise tell the nine walkable rows from the 2,269 that resolve to
    # nothing. Writing a caller's name into `produced_by` would put this row in the second group
    # and re-open exactly the ambiguity 17 closed. So the name lands here, `produced_by` stays
    # NULL, and `produced_by_ref` and `resolution_status` stay NULL too: that is migration 5's
    # fourth state, "no resolution was ever attempted", which is the truth about a typed verb.
    # `brain_adapter.store_join.producer_stamp()` emits precisely this shape and is what the
    # `swarm observation open` surface calls.
    return ctx.scalar(
        "INSERT INTO brain.observation "
        "  (event_id, subject_type, subject_id, kind, text, actor_type, produced_by, "
        "   produced_by_ref, resolution_status, produced_by_producer) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (event_id, subject_type, subject_id, kind, text, actor_type, produced_by,
         produced_by_ref, resolution_status, produced_by_producer),
    )


@_t.transition("observation close", role="runtime")
def _observation_close(ctx, *, observation_id: int) -> dict:
    return ctx.one(
        "UPDATE brain.observation SET status = 'closed', closed_at = now() "
        " WHERE id = %s RETURNING id, status, closed_at",
        (observation_id,),
    )


@_t.transition("disposition record", role="runtime")
def _disposition_record(ctx, *, event_id: int, verdict: str, rationale: str = "",
                        observation_id=None, decided_by: str = "", wager_ref=None,
                        actor_type=None, produced_by=None, produced_by_ref=None,
                        resolution_status=None, produced_by_producer=None) -> int:
    """EF-3: `event_id` is REQUIRED and `observation_id` is NULLABLE.

    A disposition always answers an occurrence; it does not always answer a standing observation.
    D1 encoded that as `event_id bigint NOT NULL` and `observation_id bigint NULL`, and this
    signature refuses to let a caller reach the constraint by accident.
    """
    if event_id is None:
        raise ValueError(
            "disposition record requires event_id (EF-3). A disposition that answers no "
            "occurrence has nothing to be a disposition of."
        )
    # The lineage triple, same contract as `event emit` above.
    return ctx.scalar(
        "INSERT INTO brain.disposition "
        "  (event_id, observation_id, verdict, rationale, decided_by, wager_ref, actor_type, "
        "   produced_by, produced_by_ref, resolution_status, produced_by_producer) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (event_id, observation_id, verdict, rationale, decided_by, wager_ref, actor_type,
         produced_by, produced_by_ref, resolution_status, produced_by_producer),
    )


def ack(subscriber: str, last_seq: int, declaration_commit: str = "") -> dict:
    return store.apply("event ack", subscriber=subscriber, last_seq=last_seq,
                       declaration_commit=declaration_commit, actor=subscriber)


def quarantine(subscriber: str, reason: str) -> dict:
    return store.apply("subscriber quarantine", subscriber=subscriber, reason=reason,
                       actor=subscriber)


def quarantine_detected(subscriber: str, reason: str, detected_by: str = "fabric.lag") -> dict:
    return store.apply("subscriber quarantine detected", subscriber=subscriber, reason=reason,
                       detected_by=detected_by, actor=detected_by)


def release(subscriber: str, released_by: str) -> dict:
    return store.apply("subscriber release", subscriber=subscriber, released_by=released_by,
                       actor=released_by)


def observe(event_id: int, **kw) -> int:
    return store.apply("observation open", event_id=event_id, **kw)


def dispose(event_id: int, verdict: str, **kw) -> int:
    return store.apply("disposition record", event_id=event_id, verdict=verdict, **kw)


def summarise(payload: dict) -> str:
    """A payload_summary that is a summary, not a truncation.

    JSON, compact, and it RAISES if it will not fit rather than cutting. The caller's job is then
    to put the full thing behind `payload_ref` and summarise properly, which is the behaviour the
    4096-byte reject-don't-truncate rule exists to force.
    """
    s = json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str)
    if len(s.encode("utf-8")) > 4096:
        raise PayloadTooLarge(
            f"summary would be {len(s.encode('utf-8'))} bytes. Nothing here truncates: write a "
            f"real summary and put the full payload behind payload_ref."
        )
    return s
