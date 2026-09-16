"""D3 is the fabric's first producer. This is the producer side only.

D00: "An event producer calls `event emit`. It does not INSERT." D5 owns `event`, the
`event emit` verb and the subscriber contract. This module therefore does exactly two
things:

  1. builds the payload in the shape D5 consumes, and
  2. hands it to `event emit` if that verb exists, or parks it in `event_outbox` if it
     does not yet.

The outbox is a producer-side buffer, not a second event log. Nothing subscribes to it.
`ingest events drain` replays parked rows through the real verb once D5 lands, which is why
`occurred_at` is carried in the payload rather than defaulted at emit time: a replayed
session-start must keep the time the session actually started.

## Every envelope goes through the outbox, and the verb is called AFTER the commit (task 0287)

This module used to try the verb inline, inside the caller's transaction, and park only on
failure. **That could never have worked for `session.started`, and the reason is a foreign
key.** `brain.event.session_id` references `brain.session(id)`, `event emit` runs in its own
transaction on its own connection as `brain_producer`, and at `session register` time this
lane's session row is still uncommitted and therefore invisible to it. Measured on live
`brain` with two concurrent connections before this was changed:

    ERROR: insert or update on table "event" violates foreign key constraint
           "event_session_id_fkey"
    DETAIL: Key (session_id)=(...) is not present in table "session".

So an inline call had exactly two possible outcomes: fail, or succeed by dropping the
`session_id` link -- and the link is the whole point, because without it no event walks back
to the session that produced it.

The park is still inside the caller's transaction, which keeps the property the docstring
below claims: a rolled-back session parks nothing, so it can never emit a phantom. What moved
is the *emission*, to `store.transaction`'s after-commit drain, which is the same "after
commit, never before" rule `store/transitions.py::after_commit` states for the question
producer and for the same reason.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Any

from . import config

#: The default is D5's own envelope surface, in this repo, resolved by path rather than by
#: PATH lookup. It is a default and not a hardcoding: `BRAIN_EVENT_EMIT` still overrides it.
#:
#: Why a default at all, when the contract said "export BRAIN_EVENT_EMIT": the installed hook
#: entry in `~/.claude/settings.json` is a bare command with no `env` block, so a live session
#: inherits nothing this lane has not put in its own code. That is precisely how the profile
#: default went wrong in task 0228 -- six hours of sessions registered into the database nobody
#: reads -- and the answer taken there is the answer taken here: a wrong default belongs in
#: this lane's code, where it can be fixed, not in the operator's settings file.
DEFAULT_EVENT_EMIT_CMD = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "fabric", "bin", "event-emit")


def emit_command() -> str | None:
    """Resolved per call, never at import.

    A module-level constant is read once, when the first import happens, which makes the
    variable unsettable from a test, from a hook that exports it late, and from anything that
    imports this module before its own configuration runs.
    """
    return os.environ.get("BRAIN_EVENT_EMIT") or DEFAULT_EVENT_EMIT_CMD or None

SESSION_STARTED = "session.started"
SESSION_ENDED = "session.ended"

PRODUCER = "d3-ingest"


def build(event_type: str, occurred_at: str, session_key: str, payload: dict[str, Any],
          *, actor_type: str | None) -> dict[str, Any]:
    """The envelope. Documented for D5 in ingest/docs/EVENT-CONTRACT.md.

    `actor_type` IS AN EIGHTH TOP-LEVEL KEY AND NOT A PAYLOAD FIELD (task 0300), because it maps
    to a column -- `brain.event.actor_type` -- exactly as `produced_by` does. A payload field
    reaches only `payload_summary`, which is free text, so the column would have stayed wrong
    while the summary said otherwise: two answers instead of none.

    IT IS A REQUIRED KEYWORD, DELIBERATELY, AND NOT ONE WITH A DEFAULT. The defect this closes
    (task 0300) was `fabric/producers/sessions.py` asserting the literal `"ai"` on every session
    event while `session register` wrote `'hybrid'` for the same session, because a Claude Code
    session is a human and a model together. 150 events on live `brain` said `ai` about a session
    row that said `hybrid`. A default here would have re-created exactly that: a caller that
    forgets and a value nobody chose, indistinguishable from a value someone did. So the caller
    states it, and `None` means "this producer recorded no actor" -- a state
    `brain.event.actor_type` already holds on 50 rows -- rather than "somebody forgot the kwarg".
    """
    return {
        "event_type": event_type,
        "occurred_at": occurred_at,
        "subject_type": "session",
        "subject_id": session_key,
        "produced_by": PRODUCER,
        "actor_type": actor_type,
        "host_id": config.host_id(),
        "payload": payload,
    }


def _try_verb(envelope: dict[str, Any]) -> tuple[bool, str]:
    cmd = emit_command()
    if not cmd:
        return False, "no BRAIN_EVENT_EMIT configured"
    argv = cmd.split()
    if not (shutil.which(argv[0]) or os.access(argv[0], os.X_OK)):
        return False, f"{argv[0]} is neither on PATH nor an executable file"
    try:
        proc = subprocess.run(
            argv + ["--json", "-"], input=json.dumps(envelope),
            capture_output=True, text=True, timeout=30,
        )
    except Exception as exc:  # the producer must never take the session down with it
        return False, f"event emit raised: {exc}"
    if proc.returncode != 0:
        return False, f"event emit exit {proc.returncode}: {proc.stderr.strip()[:400]}"
    return True, proc.stdout.strip()[:400]


def emit(cur, event_type: str, occurred_at: str, session_key: str, payload: dict[str, Any],
         *, actor_type: str | None) -> str:
    """Park one envelope inside the caller's transaction. Always returns 'pending'.

    Takes the caller's cursor on purpose: a session registration and its parked envelope are
    one transaction, so a committed session can never be missing its event, and a rolled-back
    one can never have parked a phantom.

    THE VERB IS NOT CALLED HERE, and that is a correctness rule rather than a scheduling
    preference. `event emit` writes `brain.event.session_id`, a foreign key to the very row
    this transaction has not committed yet; the module docstring carries the measured refusal.
    The envelope is emitted by the after-commit drain in `store.transaction`, at which point
    the session it names exists.

    `actor_type` IS THE ROW'S, NOT THE CALLER'S INTENT. Both call sites in `verbs.py` read it
    back out of their own statement's RETURNING clause rather than forwarding the argument they
    passed in. The two are the same today; they would stop being the same the moment the upsert's
    merge precedence changes, and one of the two would then be wrong with nothing to catch it.
    Reading the stored value is what makes an event and its session unable to disagree.
    """
    env = build(event_type, occurred_at, session_key, payload, actor_type=actor_type)
    cur.execute(
        """INSERT INTO event_outbox
             (event_type, occurred_at, subject_type, subject_id, produced_by, payload,
              emit_status, emit_detail)
           VALUES (%s, %s, %s, %s, %s, %s::jsonb, 'pending', %s)""",
        (env["event_type"], env["occurred_at"], env["subject_type"], env["subject_id"],
         env["produced_by"], json.dumps(env), "parked, awaiting the after-commit drain"),
    )
    return "pending"


#: How many envelopes one after-commit drain will attempt. A BOUND ON THE SESSION'S HOT PATH,
#: not a throughput setting: each attempt is a subprocess and a round trip, the hook budget is
#: 8 s, and an unbounded drain would put a 133-row backlog on the next operator's SessionStart.
#: The backlog still clears, over that many sessions or in one `ingest events drain`.
#:
#: The number is 4 because an envelope costs a measured 0.65 s (`ingest events drain --limit 3`
#: took 1.95 s wall on 2026-08-17), so a worst case is ~2.6 s against an 8 s budget, and the
#: ordinary case is one envelope and one round trip. Ten would have been ~6.5 s and over.
#:
#: The failure mode this used to leave, and how task 0302 closed it: the drain was FIFO by
#: `seq` alone, so N envelopes that can never emit sat at the head and starved everything
#: behind them, with no attempt counter in `event_outbox` to skip them with. There is one now
#: (`schema/0003_event_outbox_terminal.sql`), the drain orders by it, and `MAX_EMIT_ATTEMPTS`
#: retires a row that keeps being refused. What still makes a backlog visible is unchanged:
#: `ingest coverage` reports the outbox by status, and a `failed` count above zero is the
#: signal to read `emit_detail`, fix the cause, and `ingest events requeue`.
AFTER_COMMIT_DRAIN_LIMIT = int(os.environ.get("BRAIN_EVENT_DRAIN_LIMIT", "4"))

#: How many refusals an envelope absorbs before the drain stops offering it. Task 0302.
#:
#: THE POINT IS THE TERMINAL STATE, NOT THE NUMBER. `emit_status` has carried a `failed` value
#: in its CHECK since the schema was written and nothing ever wrote it, so a permanently
#: undeliverable envelope and one waiting its turn were the same row to every reader. Five is
#: chosen to be several ordinary sessions' worth of retries -- the after-commit drain runs once
#: per verb, so a transient refusal (store restarting, emit surface being redeployed) clears
#: long before it -- while still being reached the same day rather than never.
#:
#: THAT REASONING WAS FALSE UNTIL TASK 0426, and the two `failed` rows on live `brain` are what
#: it cost. "Once per verb" is a statement about ONE session; ten sessions ending in the same
#: second ran ten drains over the same rows, and five of them spent the whole budget on one
#: envelope in 968 ms. The bound now belongs to the retry it names, because `drain` takes
#: `FOR UPDATE SKIP LOCKED` and no two drains hold the same row. Read the measurement there.
#:
#: `failed` IS NOT `forgotten`. The row keeps its whole envelope, its attempt count and the
#: last refusal verbatim; it is out of the FIFO head and nothing else. `requeue` below is the
#: way back, and it exists so that giving up is never the same act as discarding.
MAX_EMIT_ATTEMPTS = int(os.environ.get("BRAIN_EVENT_MAX_ATTEMPTS", "5"))


def drain(cur, limit: int = 1000) -> dict[str, int]:
    """Replay parked envelopes through `event emit`. This is the ONLY path that emits.

    ORDER BY (attempts, seq), NOT seq (task 0302). Fresh envelopes outrank retries and FIFO is
    preserved within an attempt count, so a row that is going to be refused cannot hold the
    head against envelopes that have never been tried. Ordering by `seq` alone is what made
    seven undeliverable rows a starvation risk for everything behind them.

    An envelope refused `MAX_EMIT_ATTEMPTS` times becomes `failed`, which is terminal for the
    drain and for nothing else: the payload, the count and the refusal all stay on the row and
    `requeue` puts it back. The drain never decides a refusal is *permanent* by reading it --
    see the schema file for why a classifier would have been wrong about exactly these rows.
    """
    # `FOR UPDATE SKIP LOCKED` (task 0426). Without it, concurrent drains read each other's
    # rows and attempt the same envelope, which is not a throughput inefficiency but the thing
    # that defeats `MAX_EMIT_ATTEMPTS`. Measured on live `brain` 2026-08-28, on the two rows
    # that are `failed` there:
    #
    #   seq 825 and 826 went from attempts=0 to attempts=5 between 17:19:49.663 and
    #   17:19:50.631 on 2026-08-20, a window of 968 ms. Five drains ran in that second, one per
    #   SessionEnd hook, because ten Claude Code sessions ended together when WSL shut down.
    #   The comment on MAX_EMIT_ATTEMPTS reasons from "the after-commit drain runs once per
    #   verb, so a transient refusal ... clears long before it". That is true of five sessions
    #   in sequence and false of five in the same second, and the refusal here lasted under a
    #   second: `/mnt/c` was returning EIO while the mount went away, and seq 821-824 emitted
    #   through the SAME absolute path in the SAME second.
    #
    #   The same race also writes a wrong row. seq 818, 819 and 820 read `emit_status='emitted'`
    #   while carrying that refusal verbatim in `emit_detail`, and seq 820 has
    #   first_attempted_at LATER than last_attempted_at. Two drains held one row: one succeeded
    #   and set the status, the other was refused and clobbered the detail. A reader of that row
    #   cannot tell whether the envelope reached the bus.
    #
    # SKIP LOCKED rather than plain FOR UPDATE, because this runs on the operator's session
    # path: a second drain must take other work or none, never block behind a subprocess call.
    cur.execute(
        "SELECT seq, payload, attempts FROM event_outbox WHERE emit_status = 'pending' "
        "ORDER BY attempts, seq LIMIT %s FOR UPDATE SKIP LOCKED",
        (limit,),
    )
    rows = cur.fetchall()
    counts = {"attempted": 0, "emitted": 0, "still_pending": 0, "failed": 0}
    for row in rows:
        counts["attempted"] += 1
        ok, detail = _try_verb(row["payload"])
        attempts = (row["attempts"] or 0) + 1
        if ok:
            # `attempts` is not reset on success. "It emitted on the third try" is a fact about
            # a flapping emit surface and zeroing it would hide one.
            cur.execute(
                "UPDATE event_outbox SET emit_status='emitted', emitted_at=now(), "
                "attempts=%s, last_attempted_at=now(), "
                "first_attempted_at=COALESCE(first_attempted_at, now()), emit_detail=%s "
                "WHERE seq=%s",
                (attempts, detail, row["seq"]),
            )
            counts["emitted"] += 1
        elif attempts >= MAX_EMIT_ATTEMPTS:
            # The detail says the count as well as the refusal, because a reader who finds this
            # row later needs to know it was retried and not simply dropped on first contact.
            cur.execute(
                "UPDATE event_outbox SET emit_status='failed', failed_at=now(), "
                "attempts=%s, last_attempted_at=now(), "
                "first_attempted_at=COALESCE(first_attempted_at, now()), emit_detail=%s "
                "WHERE seq=%s",
                (attempts,
                 f"gave up after {attempts} attempts; last refusal: {detail}"[:2000],
                 row["seq"]),
            )
            counts["failed"] += 1
        else:
            cur.execute(
                "UPDATE event_outbox SET attempts=%s, last_attempted_at=now(), "
                "first_attempted_at=COALESCE(first_attempted_at, now()), emit_detail=%s "
                "WHERE seq=%s",
                (attempts, detail, row["seq"]),
            )
            counts["still_pending"] += 1
    return counts


def requeue(cur, *, seqs: list[int] | None = None, all_failed: bool = False,
            reason: str = "requeued by hand") -> dict[str, Any]:
    """Put `failed` envelopes back in the pending queue. Task 0302.

    This is the half of the terminal state that makes it safe to have one. Without a way back,
    marking a row `failed` would be indistinguishable from deciding to forget it, and the drain
    would be quietly discarding real events every time an emit surface had a bad day.

    ONLY `failed` ROWS MOVE. A `pending` row is already queued and does not need this; an
    `emitted` row must never be requeued from here, because "re-emit something that reached the
    bus" is a different act with a different blast radius and it does not get to share a verb
    with "retry something that never did". (`event emit` would in fact dedupe it -- `event_id`
    is a uuid5 over the envelope's identity and the column is UNIQUE -- but relying on a
    downstream collision to undo a local mistake is not a design.)

    `attempts` resets to 0 so the requeued row competes fairly for the drain head again, and
    `requeues` increments so the reset does not erase its own evidence: a row that has been
    round the loop three times is a different problem from one that failed once.
    """
    if not seqs and not all_failed:
        raise ValueError("requeue needs --seq or --all-failed; it will not guess a scope")
    where = "emit_status = 'failed'"
    params: list[Any] = [f"{reason} (was: %s)"]
    if seqs:
        where += " AND seq = ANY(%s)"
    cur.execute(
        f"""UPDATE event_outbox
               SET emit_status = 'pending', failed_at = NULL, attempts = 0,
                   requeues = requeues + 1,
                   emit_detail = format(%s, coalesce(emit_detail, '(no detail)'))
             WHERE {where}
         RETURNING seq, subject_id, event_type, requeues""",
        params + ([seqs] if seqs else []),
    )
    moved = [dict(r) for r in cur.fetchall()]
    # A seq that named a row which was not `failed` is reported, not silently absorbed: the
    # caller asked for something specific and deserves to know it did not happen.
    asked = set(seqs or [])
    return {"requeued": len(moved), "rows": moved,
            "not_failed_or_absent": sorted(asked - {r["seq"] for r in moved})}
