"""The event type registry, the retention map, and prefix matching.

EF-15 records that the registry's canonical home is a proposed `_system/event-type-registry.md`
and that the choice is Tier 3 and undecided. So this file is the *runtime* registry: it is what
`event emit` validates against, and it is deliberately small. When EF-15 is decided, this table is
loaded from that file instead of being written here; nothing else in the fabric changes, because
every caller already goes through `resolve()`.

Three things live here and nowhere else:

1. **Which types exist.** `event emit` refuses an unregistered type. A fabric where any producer
   can invent a type is a fabric where no subscriber can declare what it consumes, and EF-6's whole
   argument is that a subscription must be a declared fact rather than an inference.

2. **Retention class per type.** D00 freezes 14/90/400 by class and the sweep is `owner`'s. A
   producer does not get to choose how long its event lives; the type does. A producer that could
   pick `consequential` for a heartbeat would defeat the retention contract by accident.

3. **Prefix matching, on segment boundaries.** `_system/subscriber-registry-rules.md` is explicit:
   "Explicit values only. No wildcards inside a segment, no suffix or infix wildcards, no
   predicate. Prefixes break on segment boundaries." `question.` matches `question.raised`;
   `quest` matches nothing at all.
"""

from __future__ import annotations

from dataclasses import dataclass

# Retention classes, as D1 constrained the column: machine 14d, standard 90d, consequential 90d,
# rollups 400d. The sweep that enforces them is brain.sweep_events(), executable by `owner` only.
RETENTION_CLASSES = ("machine", "standard", "consequential")

# EF-1's volume line carries 5,000 events/day alongside the 14/90/400 days and the 4096 bytes.
DAILY_EVENT_BUDGET = 5000


@dataclass(frozen=True)
class EventType:
    name: str
    retention_class: str
    description: str
    # EF-4 added a fourth receipt-booking moment, consequential-event-emitted. A type that books
    # is a type whose emission is itself a consequential action in the wager ledger's sense.
    books_receipt: bool = False

    def __post_init__(self):
        if self.retention_class not in RETENTION_CLASSES:
            raise ValueError(
                f"{self.name}: retention_class {self.retention_class!r} is not one of "
                f"{RETENTION_CLASSES}. D1's CHECK constraint would reject it at insert time; "
                f"this catches it at import time instead."
            )


# The registry. Scope is deliberately two producers, per the D5 brief: session lifecycle and
# operator questions. `pipeline.run.failed` is registered because the accepted phase-1 pilot
# consumes it and its falsifier passed (see fabric/FALSIFIER.md); the observer that consumes it
# is not wired in v1 and the reason is recorded there.
_TYPES: dict[str, EventType] = {}


def register(t: EventType) -> EventType:
    if t.name in _TYPES:
        raise ValueError(
            f"event type {t.name!r} is already registered. One type, one definition: a second "
            f"definition is how two producers end up emitting the same name with two retention "
            f"classes and no subscriber can tell them apart."
        )
    _TYPES[t.name] = t
    return t


# --- producer 1: session lifecycle (D3 provides the hook; it calls `event emit`) ---------------
register(EventType("session.started", "machine",
                   "A harness session registered. Machine volume, 14 days."))
register(EventType("session.ended", "machine",
                   "A harness session ended. Machine volume, 14 days."))

# --- producer 2: operator questions -------------------------------------------------------------
# Added 2026-08-16 after audit: with only session lifecycle wired, a question raised at 02:00 sits
# until the 07:00 brief, and doctor's question-age warning is 12 hours, so nothing surfaces it.
register(EventType("question.raised", "standard",
                   "An agent raised a question only the operator can answer. This is the type "
                   "that has to reach a human without waiting for the brief."))
register(EventType("question.answered", "standard",
                   "The operator answered. Emitted so a pager can retract rather than re-page, "
                   "and consumed for exactly that by operator-paging since task 0162."))
# The other way a question stops needing a human, added 2026-08-18 by task 0159. Until then
# `question.answered` was the ONLY event that could take a page back, so a question whose task
# was cancelled left its page standing all night and its row in `swarm questions` forever -- and
# the operator eventually answered one of them, which is what the retraction exists to prevent.
register(EventType("question.withdrawn", "standard",
                   "A question was retired WITHOUT being answered, signed by whoever did it -- "
                   "normally because its task was cancelled. Retracts the page the same way an "
                   "answer does, and never means a decision was made."))

# --- the accepted phase-1 pilot's type ----------------------------------------------------------
register(EventType("pipeline.run.failed", "consequential",
                   "A deterministic-lane pipeline run failed. The accepted phase-1 pilot's type.",
                   books_receipt=True))

# --- producer 3: voice capture (task 0377, lane V2) -----------------------------------------
# A capture failure is `consequential` and not `standard`, and the argument is the incident: on
# 2026-08-17 the operator's daily monologue saved as 0 bytes, he believed it had saved, and the
# intake for his whole planning process was gone until a human checked by hand. A type whose
# whole job is to prevent that cannot be on a 90-day retention class -- the thing a later reader
# needs is the record that a day's capture failed, months after the day.
#
# `books_receipt` is FALSE. A receipt is a wager-ledger obligation and this lane does not own one;
# claiming a booking moment it cannot close would put an obligation in the ledger with no verb
# behind it, which is worse than not emitting at all.
register(EventType("voice.capture.failed", "consequential",
                   "A voice capture failed at a named stage, or landed with no words. The "
                   "operator records daily and this is the intake path for the whole planning "
                   "process; a failure that reaches nobody is the 2026-08-17 incident."))

# --- producer 4: fleet lifecycle (task 0273, lane engine) ------------------------------------
# The gap these close, in the words of the task that found it: fleet lifecycle was the ONLY part
# of this system with no audit trail, inside a program whose stated character is that a record
# you can silently rewrite is not a record. `stop` wrote one column and emitted nothing, so at
# 00:55Z on 2026-08-19 a commander's deliberate stop of T4 and T5 and two terminals dying
# silently were the same two facts on the board. An admiral pass read it as the second, restarted
# both at 00:57Z, and reversed a decision three minutes after it was taken.
#
# `standard`, not `consequential`, and the distinction is not the retention number -- D1's sweep
# gives both 90 days. `consequential` is the class of an event the wager ledger books against,
# and this lane owns no receipt obligation; claiming the class without the verb behind it is the
# mistake `voice.capture.failed` declined to make for the same reason. `books_receipt` is False
# on all four.
#
# THE 4096-BYTE CEILING, checked rather than assumed, because it REFUSES rather than truncates.
# A stop reason is operator prose and has no bound. `fabric.producers.fleet` summarises into a
# bounded dict and points `payload_ref` at `brain.message:<seq>`, where the untruncated record
# is. Nothing here cuts a reason: the load-bearing copy is the message row, and the event is the
# redundant one.
register(EventType("fleet.agent.stopped", "standard",
                   "One agent was deliberately stopped, signed and with a reason. The event that "
                   "makes a stop distinguishable from a silent failure."))
register(EventType("fleet.agent.started", "standard",
                   "One stopped agent was released. Emitted only when a stop was actually "
                   "lifted, so a no-op `start` never appears as one."))
register(EventType("fleet.paused", "standard",
                   "The whole fleet was paused, signed."))
register(EventType("fleet.resumed", "standard",
                   "The fleet pause was cleared, signed."))

# --- the fabric's own health --------------------------------------------------------------------
# A quarantined subscriber is the failure this whole lane exists to make visible, so it is an
# event rather than a log line: it pages, and the page is the point.
register(EventType("fabric.subscriber.quarantined", "consequential",
                   "A subscriber quarantined itself. Its cursor has STOPPED advancing and events "
                   "are accumulating behind it.", books_receipt=True))


class UnregisteredType(KeyError):
    """A producer invented a type. Refused, loudly, at emit time."""


def resolve(name: str) -> EventType:
    try:
        return _TYPES[name]
    except KeyError:
        known = ", ".join(sorted(_TYPES))
        raise UnregisteredType(
            f"event type {name!r} is not registered. Registered: {known}. "
            f"Producers never know their consumers, so a subscriber can only declare interest in "
            f"a type that exists; inventing one at emit time makes the declaration unfalsifiable."
        ) from None


def all_types() -> dict[str, EventType]:
    return dict(sorted(_TYPES.items()))


def matches_prefix(event_type: str, prefix: str) -> bool:
    """Segment-boundary prefix match, per `_system/subscriber-registry-rules.md`.

    A declared prefix either ends at a segment boundary (`question.`) or names the whole type
    (`question.raised`). It never matches a partial segment, so `quest` matches nothing and
    `question` does not silently swallow a future `questionnaire.*` namespace.
    """
    if event_type == prefix:
        return True
    if prefix.endswith("."):
        return event_type.startswith(prefix)
    return event_type.startswith(prefix + ".")


def matches_any(event_type: str, prefixes) -> bool:
    return any(matches_prefix(event_type, p) for p in prefixes)
