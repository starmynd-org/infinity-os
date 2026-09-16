"""The only write path.

`bin/swarm` works because it is the only writer. The runner, the board, the operator typing a
verb and an agent calling a verb all go through identical code, which is why many surfaces are
safe at once: none of them can invent a state transition.

Postgres erodes that by default, because a shared module is a library. This file is the
replacement guarantee, and it is three rules:

  1. **A write connection exists only inside `apply()`.** Nothing else in the package opens one
     and nothing hands one out. `store.read()` yields a Postgres READ ONLY session.

  2. **One transition function per state change, registered under one name.** Registering a name
     twice raises `DuplicateTransition` at import time. Two lanes cannot each define `done`, and
     the second one to try finds out immediately rather than in production.

  3. **One verb, one transaction.** A ported `done` is a row update plus a thread event plus an
     artifact record. A file rename was all-or-nothing per task; three writes are not. `apply()`
     wraps the whole transition in a single transaction, so a verb either lands completely or
     leaves no trace. Subsystem transitions get the same treatment as dispatch ones: this is not
     a dispatch-only property.

What a lane may do: define its own transitions with `@transition("session register")` and call
any transition through `apply`. What no lane may do: write a row outside one.

Subprocess versus in-process is a performance decision, not an architectural one. A CLI, an MCP
tool, an n8n webhook and the console all call `apply` with the same verb name and get the same
code. No integration earns a private path.
"""

from __future__ import annotations

import contextlib
import importlib
import inspect
import os
import sys

import psycopg2
import psycopg2.extras

from .session import _connect, human_slug

_REGISTRY: dict[str, "_Transition"] = {}

#: Side effects that run AFTER a verb's transaction commits, keyed by verb name. See
#: `after_commit` below for why this exists and what it is not allowed to be.
_AFTER_COMMIT: dict[str, list] = {}

#: Modules imported once, on the first `apply()`, for their `after_commit` registrations alone.
#: NAMED HERE rather than imported by each surface, and task 0140 is the whole reason.
#:
#: The seam it closes: `swarm ask` wrote a `question` row and paged nobody, while D5's question
#: producer read the FILE bus and could not see that row. Each lane's half was correct against a
#: different bus and the join had no owner. The obvious repair -- have every surface import the
#: producer -- rebuilds the same defect one surface later, because the fourth surface (an n8n
#: webhook, the console, whatever comes next) will not remember. Every surface already goes
#: through `apply()`. So the join lives here, once, and a surface cannot forget it because a
#: surface never mentions it.
_HOOK_MODULES = ("fabric.producers.questions", "fabric.producers.voice",
                 "fabric.producers.fleet")

#: `fabric.producers.voice` joined the list for task 0377 and the argument is the same one, one
#: incident later. A voice capture that fails is the operator's daily intake failing, and on
#: 2026-08-17 it failed silently: the monologue saved as 0 bytes, he believed it had saved, and
#: nobody found out until a human opened the file. `voice health` exists and it is a command
#: somebody has to RUN -- which is precisely what nobody did, because there was no reason to
#: suspect anything. Wiring the emit into the voice CLI instead would have paged the CLI's
#: failures and silently not a cron's, not the console's and not a phone client's; every surface
#: goes through `apply`, so the join belongs here, where a surface never has to remember it.

_HOOKS_LOADED = False


class DuplicateTransition(RuntimeError):
    """Two definitions of one state change. This is the failure the module exists to catch."""


class UnknownTransition(KeyError):
    """A verb nobody registered. Better than a raw SQL fallback that nobody reviewed."""


class _Transition:
    __slots__ = ("name", "fn", "role", "defined_in")

    def __init__(self, name, fn, role, defined_in):
        self.name, self.fn, self.role, self.defined_in = name, fn, role, defined_in


def transition(name: str, *, role: str = "runtime"):
    """Register one state change under one verb name.

    `role` is the database role the transition runs as, and it is the second, independent gate.
    A transition registered under `runtime` cannot insert an event however it is written, because
    `brain_runtime` holds no INSERT on `event`; an event emitter must declare `role="producer"`
    and thereby give up SELECT. The grant and the registration have to agree, and neither one
    trusts the other.
    """

    def decorate(fn):
        if name in _REGISTRY:
            prior = _REGISTRY[name]
            raise DuplicateTransition(
                f"the transition {name!r} is already registered by {prior.defined_in}. "
                f"{_where(fn)} is trying to define it a second time. One transition function per "
                f"state change: call apply({name!r}, ...) instead of reimplementing it."
            )
        _REGISTRY[name] = _Transition(name, fn, role, _where(fn))
        return fn

    return decorate


def _where(fn) -> str:
    try:
        src = inspect.getsourcefile(fn) or "?"
        line = inspect.getsourcelines(fn)[1]
        return f"{src}:{line}"
    except (OSError, TypeError):
        return getattr(fn, "__module__", "?")


def registered() -> dict:
    """Every verb, who defined it, and which role it runs as. This is the audit surface."""
    return {
        name: {"role": t.role, "defined_in": t.defined_in}
        for name, t in sorted(_REGISTRY.items())
    }


def after_commit(verb: str, fn) -> None:
    """Register a side effect that runs AFTER `verb`'s transaction has committed and closed.

    THIS IS NOT A SECOND WRITE PATH and it must never become one. A hook runs outside the
    transaction, on its own connection, under its own role, and it cannot make the verb fail.
    What that buys is the one thing a transaction cannot: `event emit` runs as `brain_producer`
    and `ask` runs as `brain_runtime`, and `brain_runtime` holds no INSERT on `brain.event`
    (`migrations/0002_roles.sql`). So "emit inside the ask transaction" is not a design
    preference anyone can choose here -- it is a grant change that deletes the role split, and
    the role split is what makes the producer unable to read the bus it writes to.

    Three properties, each of which is a rule and not an implementation detail:

    1. **After commit, never before.** The hook sees a state change that has landed. A hook that
       ran inside the transaction could observe a row that then rolled back and page a human
       about a question that does not exist.
    2. **A hook cannot fail the verb.** Anything raised is caught and printed on stderr. A pager
       that is down must not turn a landed `ask` into an error the caller retries, because the
       retry raises the question a second time. The verb's return value is untouched.
    3. **A hook may call `apply()`.** It goes through the same waist as everything else; hooks
       registered for the hook's own verb still fire, so a hook that emits the verb it listens
       for would recurse and is the caller's problem to not write.

    `fn(verb, result, kwargs)` -- the verb name, whatever the transition returned, and the
    keyword arguments it was called with, so a hook never has to re-derive the call.
    """
    _AFTER_COMMIT.setdefault(verb, []).append(fn)


def _load_hooks() -> None:
    """Import `_HOOK_MODULES` once, for their registrations. Lazy, so there is no import cycle.

    `fabric` imports `store`; importing `fabric` from the top of this module would be circular.
    Importing it at the first `apply()` is not, and it is also the point at which the
    registration first matters.
    """
    global _HOOKS_LOADED
    if _HOOKS_LOADED:
        return
    # Set BEFORE importing: a module that raises on import must not be retried on every verb for
    # the life of the process. It reports once, loudly, and then this process runs without it.
    _HOOKS_LOADED = True
    if os.environ.get("BRAIN_AFTER_COMMIT_HOOKS", "1") == "0":
        # Test-only. `swarm doctor` reports questions that reached nobody, so a process that
        # turned paging off does not stay quiet about the consequence.
        return
    for name in _HOOK_MODULES:
        try:
            importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001 -- a broken hook must not break every verb
            print(f"store: after-commit hook module {name!r} did not import "
                  f"({exc.__class__.__name__}: {exc}). Every verb still works; whatever that "
                  f"module wires does NOT. If it is the question producer, questions raised in "
                  f"this process page nobody.", file=sys.stderr)


def _run_after_commit(verb: str, result, kwargs: dict) -> None:
    for fn in _AFTER_COMMIT.get(verb, ()):
        try:
            fn(verb, result, kwargs)
        except Exception as exc:  # noqa: BLE001 -- see rule 2 in `after_commit`
            print(f"store: after-commit hook {getattr(fn, '__qualname__', fn)} failed on "
                  f"{verb!r} ({exc.__class__.__name__}: {exc}). THE VERB LANDED; its side "
                  f"effect did not.", file=sys.stderr)


def _login_for(t: "_Transition", kwargs: dict) -> str:
    """Which database login this call opens. Almost always the transition's own role.

    THE ONE EXCEPTION IS `actor_type='human'`, and it is not a convenience either. Migration 20
    makes the human/agent partition a property of the login that wrote the row: a work item can
    only BECOME `actor_type = 'human'` in a session whose `session_user` has a row in
    `brain.human_role`, and `brain_runtime` -- the login every agent surface connects as -- has
    none and cannot make one. So a verb that declares it is writing the operator's own work has
    to open the operator's own connection, and this is where that is decided, once, for every
    surface rather than in each of them.

    A process that is not the operator does not get a worse row here, it gets no row: `_connect`
    raises `StoreConfigError` because the credential does not resolve, and if it somehow did
    connect as the runtime anyway the trigger refuses the INSERT. Two layers, and the
    database's is the load-bearing one -- this function is only the part that stops the
    operator's own console from being refused by it.

    Deliberately narrow. Only a `runtime` verb is redirected: `event emit` runs as the producer
    and carries `actor_type` on rows in `brain.event`, where no such partition exists and where
    a redirect would break the INSERT-only grant that makes the producer role mean anything.
    """
    if t.role == "runtime" and str(kwargs.get("actor_type") or "") == "human":
        return "operator"
    # THE SECOND ROUTE TO THE SAME DECISION, added with migration 26. `swarm set <id>
    # agent_claimable true` hands one of the operator's rows to the fleet, and migration 26's
    # trigger refuses that write to any session `brain.current_human()` does not recognise. The
    # verb has no `actor_type` to redirect on -- it writes one named column and must not touch
    # the row's actor -- so it declares the intent instead. Declaring it is not what makes it
    # true: an process without the operator credential gets `StoreConfigError` from `_connect`
    # and, if it somehow connected as the runtime anyway, the trigger refuses the UPDATE. This
    # function is again only the part that stops the OPERATOR being refused by his own database.
    if t.role == "runtime" and kwargs.get("as_operator"):
        return "operator"
    return t.role


# ------------------------------------------------------------------ which human, lane E, row 0384
#
# `_login_for` answers WHICH CLASS OF LOGIN: the runtime's, the producer's, or a human's. It
# returns `"operator"` for the human class and it always will, because `store/session.py:ROLES` is
# a list of privilege classes and a named human is not a sixth one (migration 20 grants a human
# role `brain_runtime` by membership, so its class IS the operator's). What lane F left open is
# the other half: WHICH human, once there is more than one.
#
# The answer is `session.human_slug()`, and it is a stated resolution order rather than an
# environment read inlined here. The reasoning is in that function and in
# `outputs/2026-08-27-E-0384-multi-user/IDENTITY-POLICY.md` part 1. The two sentences worth
# carrying at the call site:
#
#   NAMING IS NOT CLAIMING.  The slug selects a CREDENTIAL. A slug whose credential does not
#   resolve raises StoreConfigError from `dsn()` and does NOT fall back to `brain_operator`.
#
#   NOT NAMING IS NOT ANONYMITY.  A process that names nobody resolves `operator`, which is a
#   credential it either holds or does not. There is no path here that writes a human-attributed
#   row without a human login behind it, and every gate downstream still asks
#   `brain.current_human()`, which reads `session_user` and cannot be told what to say.


def apply(verb: str, *args, actor: str = "", **kwargs):
    """Run one registered transition in one transaction. The only write path in this package.

    Every mutation is the same call whether a human, an agent, a hook or a webhook made it, which
    is what makes the whole thing auditable and what stops the console and the MCP server from
    drifting into two subtly different systems.

    That property is also what makes this the only correct home for an after-commit side effect
    that has to reach every surface. See `after_commit`.
    """
    _load_hooks()
    try:
        t = _REGISTRY[verb]
    except KeyError:
        known = ", ".join(sorted(_REGISTRY)) or "(none registered)"
        raise UnknownTransition(
            f"no transition named {verb!r}. Registered: {known}. If this is a new state change, "
            f"define it once with @transition and let every surface call it."
        ) from None

    # A transition registered `role="subscriber"` runs as THAT subscriber's login, not as the
    # shared `brain_subscriber` one. Since migration 10 the shared login maps to no subscriber and
    # can write no cursor row at all, so this is not a convenience: it is how `event ack` reaches
    # its own row. The name comes from the verb's own keyword, and the database then decides
    # whether the caller holds that name -- the caller does not get to assert it.
    subscriber = kwargs.get("subscriber") if t.role == "subscriber" else None

    # WHICH human, when there is more than one. See the block above `apply`'s definition. One
    # resolver, one order, called from one place, so a surface cannot answer this differently
    # from the surface next to it.
    login = _login_for(t, kwargs)
    # POPPED, not read. `as_human` is a directive to THIS function about which credential to
    # open, and it is not an argument to the verb: no transition should have to declare it, and
    # one that did would be inviting itself to make a second decision about identity beside this
    # one. `as_operator` is deliberately NOT popped, because it stays in the repo's existing
    # convention of being declared in a transition's own signature.
    human = human_slug(kwargs.pop("as_human", None)) if login == "operator" else None
    conn = _connect(login, subscriber, human)
    try:
        conn.set_session(readonly=False, autocommit=False)
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            ctx = _WriteContext(cur, actor=actor, verb=verb)
            try:
                result = t.fn(ctx, *args, **kwargs)
            except Exception:
                conn.rollback()
                raise
            conn.commit()
    finally:
        conn.close()

    # Outside the `finally`, so the transaction's connection is already returned before a hook
    # opens its own. A hook runs only on a commit that happened: every path that raises above
    # leaves this line unreached, which is the "after commit, never before" rule expressed as
    # control flow rather than as a comment.
    _run_after_commit(verb, result, kwargs)
    return result


class _WriteContext:
    """What a transition body is given. Scoped to one transaction and thrown away after it.

    It has `execute`, which is the point: inside a registered transition, writing SQL is the
    job. It does not expose the connection, so a transition cannot commit early, open a second
    transaction, or keep the handle after `apply` returns.
    """

    __slots__ = ("_cur", "actor", "verb")

    def __init__(self, cur, actor: str, verb: str):
        self._cur, self.actor, self.verb = cur, actor, verb

    def execute(self, sql: str, params=None) -> list:
        self._cur.execute(sql, params)
        if self._cur.description is None:
            return []
        return [dict(r) for r in self._cur.fetchall()]

    def one(self, sql: str, params=None):
        rows = self.execute(sql, params)
        return rows[0] if rows else None

    def scalar(self, sql: str, params=None):
        row = self.one(sql, params)
        return None if row is None else next(iter(row.values()))

    def thread(self, work_item_id: str, kind: str, text: str, to_agent: str = "") -> None:
        """A thread event, in the same transaction as the row change that caused it.

        This is the method that makes `one verb, one transaction` real rather than aspirational:
        a `done` that updated the row and failed to write the thread event used to be impossible
        under a file rename and becomes possible the moment there are three writes.
        """
        self.execute(
            "INSERT INTO brain.thread (work_item_id, from_agent, to_agent, kind, text) "
            "VALUES (%s, %s, %s, %s, %s)",
            (work_item_id, self.actor, to_agent, kind, text),
        )


@contextlib.contextmanager
def _scratch_registry():
    """Save and restore the registry. For tests only; not exported."""
    saved = dict(_REGISTRY)
    try:
        yield
    finally:
        _REGISTRY.clear()
        _REGISTRY.update(saved)
