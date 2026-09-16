"""The operator's stopwatch: three state changes, registered once each on D1's narrow waist.

    queue time start · queue time stop · queue time correct

`queue time status` IS NOT HERE AND THAT IS NOT AN OMISSION. It changes no state, so there is no
transition for it to be. `store.transition` registers STATE CHANGES; registering a read would open
a write connection to answer a question and would put a name in `store.registered()` -- the audit
surface for "what can change state in this system" -- that changes nothing. `reads.time_status()`
is the read, and `queue time status` is the CLI over it, exactly as `queue list` is the CLI over
`reads.queue()`.

WHY THIS FILE EXISTS AT ALL. The runtime measures agent cost to four significant figures and
measured the operator's time not at all, while the entire program exists to buy back his review
hours. Two numbers were assertions until rows land here: the ETA that `queue depth` divides depth
by, and `Decide`'s two-minute ceiling, which nobody had ever put a stopwatch on.

THE THREE POLICY DECISIONS, one line of reasoning each. Each is enforced twice -- once here in
words, once in the database (`queue/schema/0012_operator_time_entry.sql`, ledger version 23) --
because a gate that exists in one place is a gate one bug away from being absent.

1. A FORGOTTEN TIMER IS `abandoned`, NOT A FOURTEEN-HOUR MEASUREMENT. Past four hours the stop is
   forced to `abandoned` and the entry leaves every mean: an unbounded entry is the likeliest way
   this feature produces a confidently wrong number, and clamping it to the cap would invent a
   measurement rather than lose one.

2. TWO TIMERS AT ONCE ARE REFUSED. The queue's premise is that context switching is the cost, and
   beyond the doctrine overlapping intervals let measured minutes exceed elapsed wall-clock, which
   makes a clearance rate arithmetically wrong rather than merely untidy.

3. A TIMER IS NEVER REQUIRED. An untimed item is normal; a system that nagged for one would
   collect compliance entries rather than measurements. So every figure derived from this ledger
   reports its own coverage -- "measured over N of M" -- instead of averaging what it has.

WHAT THIS DELIBERATELY DOES NOT GUARD, stated because a gate that oversells itself is worse than
no gate: an agent process passing `--who operator` is not distinguishable from the operator by
anything in v1. That is the same open hole `transitions._looks_like_an_agent_process` documents
for `recommend accept`, and it is in `queue/RAISED.md`. What IS closed is the cheap half: a name
in `brain.agent` is refused outright, so agent minutes cannot be booked as human ones under an
agent's own name.
"""

from __future__ import annotations

import store

from . import tiers
from .transitions import QueueError, _overlay, _pending_default, _source, _work_item_of

#: The end states. `stopped` is a measured interval and the only kind any mean counts;
#: `abandoned` is a forgotten timer, in the ledger and out of every mean.
ENDED_HOW = ("stopped", "abandoned")


def _cap_seconds(ctx) -> float:
    """The cap, read OUT OF THE DATABASE rather than held as a Python constant.

    `brain.time_entry_cap()` is what the trigger enforces. A second copy here would be a number
    two files have to agree about forever, and this lane already documents that exact drift for
    the POSTPONING tuple in `queue/README.md`. Reading it costs one cheap call per verb.
    """
    return float(ctx.scalar("SELECT EXTRACT(EPOCH FROM brain.time_entry_cap())"))


def _refuse_agent(ctx, who: str) -> str:
    who = str(who or "").strip()
    if not who:
        raise QueueError("a time entry needs a name: whose minutes are these?")
    if ctx.one("SELECT 1 FROM brain.agent WHERE name = %s", (who,)):
        raise QueueError(
            f"{who} is a registered agent, and this ledger measures HUMAN time. Agent cost is "
            f"already measured to four significant figures by `budget/` against the billed "
            f"total; booking it here as human minutes would corrupt the one number this table "
            f"exists to produce.")
    return who


def _tier_at(ctx, source_type: str, source_id: str, row: dict) -> str | None:
    """The tier the operator is looking at, or None when the item is not in a live tier.

    ONE COMPOSITION, THREE CALLERS. `reads.queue()`, `queue demote` and this verb all reach the
    tier through `tiers.tier_inputs` over the same two rows, which is task 0158's rule: a demote
    that computed the tier from `brain.queue_item` alone disagreed with the tier the console
    rendered, and wrote a calibration miss naming a tier the operator had never seen.

    None rather than a guess when the item is not in `brain.queue_open`: he may legitimately time
    something he has just accepted or answered, and inventing `shape` for it would put minutes
    into a tier's mean that the item was never in. Such an entry counts toward the overall figure
    and toward no tier's, which is stated wherever the number is printed.
    """
    qo = ctx.one("SELECT * FROM brain.queue_open WHERE source_type = %s AND source_id = %s",
                 (source_type, source_id))
    if not qo:
        return None
    ov = _overlay(ctx, source_type, source_id)
    pending = _pending_default(ctx, source_type, source_id, row)
    inputs = tiers.tier_inputs(dict(qo), dict(ov), pending["default_text"] if pending else None)
    from swarm_engine.signals import signal_level
    return tiers.tier_of(inputs, signal_level("reversibility", qo.get("reversibility")))["tier"]


@store.transition("queue time start")
def time_start(ctx, *, source_type, source_id, who="operator", note=""):
    """Start the stopwatch on one queue item. Refuses a second one.

    The refusal is the interesting half and it names what is already running, with how long it has
    been going, because "you already have a timer" with no subject is a message that gets read as
    a bug. If that running entry is already past the cap the message says so: it is going to be
    recorded as abandoned whatever happens next, and the operator should know that before he
    decides whether to stop it or correct it.
    """
    who = _refuse_agent(ctx, who)
    row = _source(ctx, source_type, str(source_id))

    running = ctx.one(
        """SELECT id, source_type, source_id, started_at, elapsed_seconds, over_cap
             FROM brain.time_entry_annotated WHERE who = %s AND running""", (who,))
    if running:
        mins = float(running["elapsed_seconds"]) / 60.0
        raise QueueError(
            f"{who} already has a timer running: {running['source_type']} "
            f"{running['source_id']}, {mins:.1f} minutes so far"
            + (" and ALREADY PAST THE CAP, so it will be recorded as abandoned"
               if running["over_cap"] else "")
            + ". Two timers at once are refused: you work one thing at a time by design, and "
              "overlapping intervals would let the measured minutes exceed the wall-clock they "
              "are measured from. Stop that one first.", code=6)

    tier = _tier_at(ctx, source_type, str(source_id), row)
    entry = ctx.one(
        """INSERT INTO brain.time_entry (source_type, source_id, who, tier_at_start, note)
           VALUES (%s, %s, %s, %s, %s)
           RETURNING id, started_at, tier_at_start""",
        (source_type, str(source_id), who, tier, note or ""))

    wid = _work_item_of(source_type, str(source_id), row)
    if wid:
        ctx.actor = who
        ctx.thread(wid, "note",
                   f"time entry {entry['id']} started ({tier or 'no live tier'})"
                   + (f": {note}" if note else ""))
    return {"id": entry["id"], "source_type": source_type, "source_id": str(source_id),
            "who": who, "started_at": entry["started_at"], "tier_at_start": entry["tier_at_start"],
            "cap_seconds": _cap_seconds(ctx)}


@store.transition("queue time stop")
def time_stop(ctx, *, who="operator", abandon=False, note=""):
    """Stop the running timer. The ONE update this ledger permits on a row, and only once.

    `abandon=True` says out loud what the cap says silently: this was not time spent on the item.
    Both land the same `abandoned` label and both leave the entry out of every mean, because the
    difference between "I forgot" and "I was over four hours" is not a difference the measurement
    can act on -- in neither case is there evidence of how long the work took.

    The cap is applied by the TRIGGER, not here, and this function does not pre-empt it. What
    this function does is report what happened afterwards, in the return value, so the CLI can
    say it in words. A Python-side clamp would be a second implementation of the rule and the two
    would drift; worse, it would be the implementation that a psql prompt bypasses.
    """
    who = _refuse_agent(ctx, who)
    running = ctx.one(
        "SELECT * FROM brain.time_entry_annotated WHERE who = %s AND running", (who,))
    if not running:
        raise QueueError(
            f"{who} has no timer running, so there is nothing to stop. `queue time status` shows "
            f"what has been measured today.", code=6)

    ctx.execute(
        "UPDATE brain.time_entry SET stopped_at = now(), ended_how = %s, "
        "note = CASE WHEN %s = '' THEN note ELSE %s END WHERE id = %s",
        ("abandoned" if abandon else "stopped", note or "", note or "", running["id"]))
    # Re-read: the trigger may have overridden `ended_how`, and reporting what we ASKED FOR
    # rather than what LANDED is how a surface comes to print `stopped` over a row the database
    # recorded as abandoned.
    final = ctx.one("SELECT * FROM brain.time_entry WHERE id = %s", (running["id"],))
    capped = (not abandon) and final["ended_how"] == "abandoned"

    src = _source(ctx, running["source_type"], running["source_id"])
    wid = _work_item_of(running["source_type"], running["source_id"], src)
    if wid:
        ctx.actor = who
        ctx.thread(wid, "note",
                   f"time entry {final['id']} {final['ended_how']} after "
                   f"{float(final['seconds']) / 60.0:.1f} minutes"
                   + (" (past the cap: a forgotten timer, left out of every mean)" if capped
                      else "")
                   + (f": {note}" if note else ""))
    return {"id": final["id"], "source_type": final["source_type"],
            "source_id": final["source_id"], "who": who,
            "started_at": final["started_at"], "stopped_at": final["stopped_at"],
            "seconds": float(final["seconds"]), "minutes": float(final["seconds"]) / 60.0,
            "ended_how": final["ended_how"], "tier_at_start": final["tier_at_start"],
            "capped": capped, "cap_seconds": _cap_seconds(ctx),
            "measured": final["ended_how"] == "stopped"}


@store.transition("queue time correct")
def time_correct(ctx, *, entry_id, started_at, stopped_at, reason, who="", note=""):
    """Correct a stopped entry by SUPERSEDING it, never by editing it.

    This verb exists because the `corrects` column would otherwise be a column no surface can
    write: the narrow waist admits no raw INSERT, so a correction path that is not a registered
    transition is a correction path that does not exist. The brief named three verbs; this is the
    fourth and it is the one that makes "append-only" a usable rule rather than a wall. Without
    it the honest response to a mistyped entry is to leave it wrong forever.

    Both rows survive and the ledger reads as a history: the original says what was recorded, this
    one says what was true and `correction_reason` says how they differ. `time_entry_effective`
    drops the superseded original from every mean; an auditor still sees both.

    The database refuses a correction that moves the item (`source_type`/`source_id` must match),
    because that would silently move measured minutes between two tiers, and refuses a second
    correction of the same row, because "which one supersedes it" must not have two answers.
    """
    if not str(reason or "").strip():
        raise QueueError(
            "a correction needs a reason. This is the one row shape whose whole job is to explain "
            "a disagreement with an earlier record, and an unlabelled one leaves a reader with two "
            "intervals and no way to tell which is the truth.")
    target = ctx.one("SELECT * FROM brain.time_entry WHERE id = %s", (entry_id,))
    if not target:
        raise QueueError(f"no time entry {entry_id}")
    if target["stopped_at"] is None:
        raise QueueError(f"time entry {entry_id} is still running, so it is not wrong yet. "
                         f"`queue time stop` closes it; a correction supersedes a closed one.")
    who = _refuse_agent(ctx, who or target["who"])
    if stopped_at < started_at:
        raise QueueError("a corrected interval that ends before it starts is not a correction")

    row = ctx.one(
        """INSERT INTO brain.time_entry
             (source_type, source_id, who, started_at, stopped_at, ended_how, tier_at_start,
              note, corrects, correction_reason)
           VALUES (%s, %s, %s, %s, %s, 'stopped', %s, %s, %s, %s)
           RETURNING id, seconds, ended_how""",
        (target["source_type"], target["source_id"], who, started_at, stopped_at,
         target["tier_at_start"], note or "", entry_id, reason))

    src = _source(ctx, target["source_type"], target["source_id"])
    wid = _work_item_of(target["source_type"], target["source_id"], src)
    if wid:
        ctx.actor = who
        ctx.thread(wid, "note",
                   f"time entry {entry_id} corrected by {row['id']}: "
                   f"{float(target['seconds']) / 60.0:.1f} -> "
                   f"{float(row['seconds']) / 60.0:.1f} minutes. {reason}")
    return {"id": row["id"], "corrects": entry_id, "ended_how": row["ended_how"],
            "seconds": float(row["seconds"]), "minutes": float(row["seconds"]) / 60.0,
            "was_seconds": float(target["seconds"]), "reason": reason,
            # The trigger caps a correction exactly as it caps a stop: a correction claiming six
            # hours is no more a stopwatch reading than a forgotten timer is.
            "capped": row["ended_how"] == "abandoned"}


__all__ = ["ENDED_HOW", "time_start", "time_stop", "time_correct"]
