"""What the store IN FRONT OF YOU has, as opposed to what `migrations/` declares.

`docs/SCHEMA-TOLERANCE.md` is the rule this module exists to make cheap:

> The ledger of the store your code will open is not the tip of `migrations/`, in either
> direction, and the only way to know it is to read it.

Measured on 2026-08-19: 132 databases on this host, 130 with a `brain.schema_migration` ledger,
11 at the tip and 119 below it, spread from version 6 to 30. One checkout meets all of them.

WHY A PROBE AND NOT A `try`. The doctrine's worked examples (`web/images.py:531`,
`queue/human_queue/options.py:252`) catch `UndefinedTable`/`UndefinedColumn` and return a fact,
and that is the right shape for a standalone read. It does not work inside a transition. Postgres
aborts the whole transaction on the failed statement, so a `cancel` that discovers the missing
column by hitting it cannot then go on to cancel the task: every later statement raises
`InFailedSqlTransaction`. `cancel` is a path that already served (doctrine rule 5) and it may not
start failing because a cascade added later cannot run, so it has to ASK BEFORE it writes. Asking
is one catalog query, and the same question read the same way everywhere is easier to review than
five hand-written `except` clauses.

The `KeyError` shape is NOT this module's job and must not be routed through it. `row["new_col"]`
after `SELECT *` costs four characters to fix (`row.get("new_col")`) and needs no round trip: the
row you already hold is the probe. Doctrine rule 2 is that a guard against one shape is not a
guard against the other, and using this module where `.get()` belongs would be a round trip that
buys nothing.
"""

from __future__ import annotations

import os
import sys
import time

from .session import read

#: How long one process trusts a probe result. A migration is applied by a hand, not by a loop, so
#: the window in which a long-lived process (the console, a subscriber) holds a stale answer is
#: bounded by this rather than by its uptime. `web/images.py` caches its own absence check the same
#: way and for the same reason: a probe on every render of a three-second poll is a query per
#: render, and a permanent cache means the operator applies a migration and the console keeps
#: behaving as though he had not until someone restarts it.
TTL_SECONDS = 60.0

#: (database, schema, table, column) -> (checked_at, present). Keyed by database because a test
#: process legitimately points `BRAIN_PG_DB` at two stores at different ledgers in one run, and a
#: cache that forgot which one it measured would answer for the wrong store.
_CACHE: dict[tuple[str, str, str, str], tuple[float, bool]] = {}

#: Warnings already printed, so a poll loop says it once rather than once per render.
_WARNED: set[str] = set()

_PROBE = """
SELECT count(*) > 0
  FROM information_schema.columns
 WHERE table_schema = %s AND table_name = %s AND column_name = %s
"""

# to_regclass returns NULL for an absent name rather than raising, so a relation probe needs no
# exception handling and cannot be mistaken for a permissions error.
_PROBE_REL = "SELECT to_regclass(%s)"


def _db() -> str:
    return os.environ.get("BRAIN_PG_DB", "brain")


def has_column(table: str, column: str, *, schema: str = "brain", ctx=None) -> bool:
    """Does this store have `schema.table.column`? One catalog query, cached for `TTL_SECONDS`.

    `ctx` is a transition's write context. Pass it and the probe runs on the transaction's OWN
    connection and is NOT cached: inside a transition the answer decides whether a write happens,
    and a write may not be decided by something another process measured a minute ago on another
    connection.
    """
    if ctx is not None:
        return bool(ctx.scalar(_PROBE, (schema, table, column)))
    key = (_db(), schema, table, column)
    hit = _CACHE.get(key)
    now = time.monotonic()
    if hit and now - hit[0] < TTL_SECONDS:
        return hit[1]
    with read() as s:
        present = bool(s.scalar(_PROBE, (schema, table, column)))
    _CACHE[key] = (now, present)
    return present


def has_relation(relation: str, *, schema: str = "brain", ctx=None) -> bool:
    """Does this store have `schema.relation` AT ALL -- table, view or materialized view?

    `has_column` answers "has this table grown a column"; this answers "does this object exist".
    They are different questions and the second had no helper, which is part of why the second
    rule-5 failure of the night was a missing VIEW rather than a missing column: the column half
    had a helper and a habit, and the relation half had neither.

    `to_regclass` returns NULL rather than raising for an absent name, so this needs no exception
    handling and cannot be confused with a permissions error.

    Same caching contract as `has_column`, and the same reason: pass `ctx` inside a transition and
    the probe runs on that transaction's own connection and is NOT cached, because a write may not
    be decided by something another process measured a minute ago on another connection.
    """
    if ctx is not None:
        return ctx.scalar(_PROBE_REL, (f"{schema}.{relation}",)) is not None
    key = (_db(), schema, relation, "*relation*")
    hit = _CACHE.get(key)
    now = time.monotonic()
    if hit and now - hit[0] < TTL_SECONDS:
        return hit[1]
    with read() as s:
        present = s.scalar(_PROBE_REL, (f"{schema}.{relation}",)) is not None
    _CACHE[key] = (now, present)
    return present


def warn_once(key: str, message: str) -> None:
    """One line on stderr, once per process. The four properties `docs/SCHEMA-TOLERANCE.md` names.

    A fallback that is silent is indistinguishable from a store that had nothing to report, and
    the difference is exactly what the operator needs in order to know a migration is waiting on
    his hand. A fallback that warns on every poll is one he filters out, which is the same thing.
    """
    if key in _WARNED:
        return
    _WARNED.add(key)
    print(f"schema: {message}", file=sys.stderr)


#: The one element this module is asked about today, named once so five call sites cannot drift
#: apart on the spelling. `migrations/0031_question_withdrawal.sql`, ledger version 31, task 0159.
QUESTION_WITHDRAWAL = ("question", "withdrawn_at")


def question_withdrawal(ctx=None) -> bool:
    """Can this store record that a question was WITHDRAWN rather than answered?

    False means the store is below ledger 31. Every caller's fallback is then the pre-31
    behaviour, and that is a fact rather than a degradation: with nowhere to write a withdrawal,
    no question in that store has ever been withdrawn, so "open means unanswered" is exactly true
    there. The one caller that must not fall back is `withdraw` itself, which is a surface that
    did not exist before 31 and refuses in a sentence (doctrine rule 5).
    """
    return has_column(*QUESTION_WITHDRAWAL, ctx=ctx)


def below_31() -> str:
    """What every fallback site says: one sentence, the migration file, and the apply line.

    A function and not a constant, because the database name belongs in the sentence and a
    constant would freeze whichever name was set at import. Test processes set `BRAIN_PG_DB` after
    importing, and a message that named the wrong store would be worse than one that named none.
    """
    return (
        f"this store ({_db()}) is below migration 31 "
        f"(migrations/0031_question_withdrawal.sql): brain.question has no withdrawn_at, so no "
        f"question in it can have been withdrawn and 'open' here means 'unanswered'. "
        f"`swarm withdraw` and `swarm questions --withdrawn` need it. The operator applies it "
        f"with: psql -d {_db()} -f migrations/0031_question_withdrawal.sql"
    )
