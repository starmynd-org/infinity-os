"""Connection handling. Thin on purpose: this is a library, not a second writer.

D00's narrow waist says a shared store module is not a single writer, so the rule is
structural instead: every state change lives in exactly one function in verbs.py, and
nothing outside verbs.py issues INSERT or UPDATE against `session` or `transcript`.
Read paths may query freely.
"""

from __future__ import annotations

import contextlib
import sys
from typing import Iterator

import psycopg2
import psycopg2.extras

from . import config, profiles


@contextlib.contextmanager
def connect(database: str | None = None, autocommit: bool = False) -> Iterator[psycopg2.extensions.connection]:
    conn = psycopg2.connect(config.dsn(database))
    conn.autocommit = autocommit
    try:
        with conn.cursor() as cur:
            # On the brain profile, `session` and `transcript` resolve to D1's schema while
            # `event_outbox` resolves to D3's own. The outbox is a producer-side buffer and
            # must not live in D1's namespace, but it does have to share the transaction:
            # a session and its event commit together or not at all.
            path = f"{config.schema()}, ingest, public" if profiles.is_brain() \
                else f"{config.schema()}, public"
            cur.execute(f"SET search_path TO {path}")
        yield conn
    finally:
        conn.close()


@contextlib.contextmanager
def transaction(database: str | None = None, *,
                drain_after_commit: bool = True) -> Iterator[psycopg2.extras.RealDictCursor]:
    """One verb, one transaction (D00 rule 10), and then the outbox drain.

    A verb that writes a session row, a transcript row and an event must not be able to
    leave two of the three behind.

    `drain_after_commit=False` is for the one caller that IS the drain. Without it
    `ingest events drain --limit 2` drains two envelopes, commits, and then drains ten more
    on the way out, which makes `--limit` a number that does not mean what it says.
    """
    with connect(database) as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
        # Reached only on a commit that happened -- every path above that raises re-raises.
        # "After commit, never before" as control flow rather than as a comment, which is the
        # same shape `store/transitions.py::after_commit` uses one lane over.
        if drain_after_commit:
            _drain_after_commit(conn)


#: What the last after-commit drain in this process did. Read by the hook so its log line can
#: say whether the envelope it parked actually reached the bus: `session register` builds its
#: return value INSIDE the transaction, so its `event` field can only ever say "pending", and a
#: log line that says pending about an event emitted 0.5 s later is how a working path gets
#: reported as broken. None means no drain has run yet in this process.
_LAST_DRAIN: dict | None = None


def last_drain() -> dict | None:
    return _LAST_DRAIN


def _drain_after_commit(conn) -> None:
    """Emit whatever this transaction (or an earlier one) parked. Task 0287.

    This is where `session.started` and `session.ended` actually reach `brain.event`, and it
    is here rather than inside the verb because `brain.event.session_id` is a foreign key to
    the session row the verb has only just committed. See `events.emit`.

    Two rules it inherits from the after-commit hook one lane over, both load-bearing:

    1. **It cannot fail the verb.** The session is registered; a store that will not take the
       event must not turn that into an exception the hook logs as a failed registration. The
       envelope stays parked and the next transaction retries it.
    2. **It is bounded.** `AFTER_COMMIT_DRAIN_LIMIT` attempts, because this runs on the
       operator's own SessionStart path.
    """
    global _LAST_DRAIN
    from . import events
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            counts = events.drain(cur, limit=events.AFTER_COMMIT_DRAIN_LIMIT)
        conn.commit()
        _LAST_DRAIN = dict(counts)
        if counts["still_pending"]:
            print(f"ingest: {counts['still_pending']} envelope(s) did not emit and stay "
                  f"parked in event_outbox; read emit_detail there.", file=sys.stderr)
        # Louder than `still_pending`, and deliberately so (task 0302). A pending envelope is
        # being retried and needs nobody; a `failed` one has been retired from the queue and
        # will not move again until someone fixes its cause and runs `ingest events requeue`.
        # Printing the two at the same volume is how a terminal state becomes invisible.
        if counts.get("failed"):
            print(f"ingest: {counts['failed']} envelope(s) were refused "
                  f"{events.MAX_EMIT_ATTEMPTS} times and are now emit_status='failed'. They "
                  f"are RETIRED, NOT DISCARDED -- payload and last refusal are on the row. "
                  f"Read them with `ingest events list --status failed`, fix the cause, then "
                  f"`ingest events requeue`.", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001 -- see rule 1
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001 -- a dead connection has nothing to roll back
            pass
        # Same key set as a successful drain, `failed` included. A reader of `last_drain()`
        # that has to check whether a key exists before reading it will eventually forget to.
        _LAST_DRAIN = {"attempted": 0, "emitted": 0, "still_pending": 0, "failed": 0,
                       "error": f"{exc.__class__.__name__}: {exc}"}
        print(f"ingest: the after-commit event drain failed "
              f"({exc.__class__.__name__}: {exc}). THE VERB LANDED; its events are still "
              f"parked in event_outbox and the next drain retries them.", file=sys.stderr)


@contextlib.contextmanager
def read(database: str | None = None) -> Iterator[psycopg2.extras.RealDictCursor]:
    with connect(database) as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            yield cur
        finally:
            cur.close()
