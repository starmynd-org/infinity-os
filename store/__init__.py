r"""The store.

A shared module is a library, not a single writer. Four lanes importing it can each write
their own state transitions and every one of them will look reasonable in review. That is the
failure this module is built to make impossible rather than to discourage.

The public surface is exactly three things:

    store.read(role=...)          a context manager yielding a READ ONLY session
    store.apply(verb, ...)        the only way to change state
    store.transition(name)        the decorator that registers a verb, used by engine lanes
    store.whoami()                which human this process IS, as the DATABASE names it
    store.human_slug()            which human this process is ASKING to be

The last two are lane E's, added when the store grew from one human to several (row `0386`
decision 2, one Postgres login per human, ceiling twelve). They are reads and neither one can
change what `brain.current_human()` answers: a slug selects which credential to open, and the
database then says who that login is.

There is no `execute`, no `insert`, no `update`, no `cursor`, and no way to reach the raw
connection. `read()` hands out a session whose transaction Postgres itself has put in READ ONLY
mode, so a caller who tries to write through the read path is refused by the database and not by
a convention, a linter, or a code review.

What that buys, concretely: `grep -rn "INSERT INTO\|UPDATE " engine/ adapter/ ingest/ fabric/
queue/ web/` should return nothing outside a registered transition. If it ever returns something,
that code cannot have run, because there is no connection in this process that would accept it.

Read `narrow_waist.md` next to this file for the argument. The rule it implements:

    One transition function per state change, exposed as a verb, called by every surface.
    No surface reimplements a transition.
"""

from .session import (ReadOnlyViolation, StoreConfigError, human_slug, read, health,
                      whoami)
from .transitions import (
    DuplicateTransition,
    UnknownTransition,
    apply,
    registered,
    transition,
)

__all__ = [
    "read",
    "health",
    "whoami",
    "human_slug",
    "apply",
    "transition",
    "registered",
    "ReadOnlyViolation",
    "StoreConfigError",
    "DuplicateTransition",
    "UnknownTransition",
]
