"""Connections, and the read path.

Two facts hold this file together:

1. A password never appears here, in a config file, or in a committed connection string. Each
   role's value resolves at runtime from a secret reference and the process fails closed when it
   does not resolve. See SECRETS.md.
2. `read()` yields a session that Postgres has put in READ ONLY mode. That is the structural half
   of the narrow waist: the read path is exposed broadly precisely because it is incapable of
   writing, so exposing it costs nothing.
"""

from __future__ import annotations

import contextlib
import os
import re
from pathlib import Path

import psycopg2
import psycopg2.extras

# The product name is not in this file and must not be. It is a config value; this module is
# named for what it does.
#
# `operator` is the fifth and it is NOT a fifth kind of privilege: migration 20 grants it
# `brain_runtime` by membership, so it can do what the app can do and one thing more -- write
# `brain.work_item.actor_type = 'human'`, which the trigger there refuses to any login with no
# row in `brain.human_role`. It exists because the operator's own work has to enter his own queue
# and an agent must not be able to put work there. Provisioned by store/bin/provision-operator.sh,
# and a host that has not run it resolves no secret and fails closed, which is correct: on that
# host nobody is the operator.
ROLES = ("owner", "producer", "subscriber", "runtime", "operator")

# References, not values. The resolver below turns one of these into a value at the point of use.
SECRET_REFS = {role: f"brain-postgres-role-{role}" for role in ROLES}

#: A subscriber slug. Narrow because it becomes a Postgres role name, and because a name that has
#: to be quoted is a name that will one day be quoted wrong.
SUBSCRIBER_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,48}$")

#: A human slug, same argument as the subscriber's three lines up. Shorter, because it also
#: becomes a secret reference id and a column value people read at a glance.
HUMAN_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,32}$")

#: How long a statement of each role's may run before Postgres cancels it. Until 2026-08-16 there
#: was none anywhere in the system: `SHOW statement_timeout` was `0` for all four roles, so one
#: cyclic parent row made `brain.work_item_signals` non-terminating and `claim` hung inside libpq
#: HOLDING ITS ROW LOCK. The whole fleet stopped claiming and three backends outlived their killed
#: clients by up to 6m57s. A bound is what turns the next unknown bug of that shape into an error.
#:
#:   runtime     15s   every verb is one small transaction; the slowest measured is well under a
#:                     second, so this is ~50x headroom and 27x shorter than that outage.
#:   producer    15s   event emission is a single-row append. Same shape, same bound.
#:   subscriber  60s   a listener legitimately scans the event table to catch up, so four times
#:                     the runtime's budget -- and still a bound, because a wedged listener is how
#:                     the health signal goes quiet.
#:   owner      30min  migrations, backfills and the retention sweep ARE long, and attended. The
#:                     bound only stops a runaway sitting forever; an operator who needs more says
#:                     SET statement_timeout in the session.
#:
#: Migration 12 sets the same four numbers with `ALTER ROLE ... IN DATABASE`, which covers a psql
#: session that never opens Python. This copy covers a database that migration has not reached and
#: a subscriber role provisioned after it ran. Neither is redundant.
#:   operator    15s   the same verbs as the runtime, from a console a human is sitting in front
#:                     of. Same bound, and deliberately not a longer one: a hung console is a
#:                     hung fleet member.
STATEMENT_TIMEOUT = {"owner": "30min", "producer": "15s", "subscriber": "60s", "runtime": "15s",
                     "operator": "15s"}


class StoreConfigError(RuntimeError):
    """Raised when a required setting or secret does not resolve. The process fails closed."""


class ReadOnlyViolation(RuntimeError):
    """Raised when a write is attempted through the read path."""


def _secret_backend() -> Path:
    """Where the local backend keeps values.

    v1 is `local-attended`, so the backend is a 0700 directory in the operator's home, outside
    every repo and outside the Postgres data directory -- a data-directory backup must never
    carry the credentials that open it. A later location class swaps this function and nothing
    else: callers only ever see a reference id.
    """
    return Path(os.environ.get("BRAIN_SECRET_DIR", str(Path.home() / ".brain-postgres-secrets")))


def resolve_secret(ref: str) -> str:
    """Reference in, value out. Fails closed, and never logs or returns the value on failure."""
    path = _secret_backend() / ref
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise StoreConfigError(
            f"secret reference {ref!r} did not resolve from the configured backend "
            f"({exc.__class__.__name__}). Refusing to connect."
        ) from None
    if not value:
        raise StoreConfigError(f"secret reference {ref!r} resolved to an empty value.")
    return value


def subscriber_role_name(subscriber: str) -> str:
    """The one producer of a subscriber's Postgres role name.

    Derived rather than looked up, because it has to be known BEFORE a connection exists. Both
    this module and `store/bin/provision-subscriber.sh` call this function, so the role the
    listener connects as and the role the provisioner granted are the same string by construction
    rather than by two people spelling it the same way.
    """
    if not SUBSCRIBER_SLUG.match(subscriber or ""):
        raise StoreConfigError(
            f"{subscriber!r} is not a subscriber slug (lowercase, digits and hyphens). It would "
            f"become a database role name, and this is refused rather than quoted."
        )
    return "brain_sub_" + subscriber.replace("-", "_")


def human_role_name(human: str) -> str:
    """The one producer of a NAMED human's Postgres role name. Migration 20's other pattern.

    `brain.provision_human_role` accepts `brain_operator` or `brain_human_<slug>` and refuses
    everything else, so this function and that regex have to agree. Derived in both places from
    the same rule rather than looked up, for the reason `subscriber_role_name` gives: the role a
    login connects as and the role the provisioner granted must be the same string by
    construction, not because two people spelled it the same way.

    The operator keeps its own name. It predates named humans, `store/bin/provision-operator.sh`
    owns it, every operator surface already writes through it, and renaming it would be a silent
    outage on the one login this system administers itself from.
    """
    h = str(human or "").strip()
    if h == "operator":
        return "brain_operator"
    if not HUMAN_SLUG.match(h):
        raise StoreConfigError(
            f"{human!r} is not a human slug (lowercase, digits and hyphens). It would become a "
            f"database role name and a secret reference id, and this is refused rather than "
            f"quoted."
        )
    return "brain_human_" + h.replace("-", "_")


def human_secret_ref(human: str) -> str:
    """Reference id for a named human's credential. `operator` keeps the reference it has."""
    h = str(human or "").strip()
    return SECRET_REFS["operator"] if h == "operator" else f"brain-postgres-human-{h}"


#: The default human. `operator` is not "the admin": it is the login this system administers
#: itself from and the one every surface already wrote through, so it is what an unconfigured
#: process resolves to and nothing about a single-human host changes.
DEFAULT_HUMAN = "operator"


def human_slug(explicit: str | None = None) -> str:
    """WHICH human a process is asking to be. Lane E's rule A1, and the only producer of it.

    Resolution order, and it is a stated policy rather than an env read scattered in `apply()`:

        1. an explicit `as_human=<slug>`   a tool acting deliberately: a test, the provisioner
        2. $BRAIN_HUMAN                    a named human's shell or service unit
        3. DEFAULT_HUMAN                   the value every existing surface already resolves to

    **WHAT THIS RETURNS IS A REQUEST, NOT A FACT**, and the distinction is the whole of the
    policy. The slug selects WHICH CREDENTIAL `dsn()` opens. A process that does not hold that
    credential fails closed there and does not fall back to `brain_operator`. Asking to be
    somebody is how you find out you are not them. What you ARE is `brain.current_human()`,
    which reads `session_user` and cannot be told what to say; `whoami()` below is the read that
    asks it.

    Per-PROCESS rather than per-call is the grain on purpose. A credential is per process, and a
    per-call answer makes "which human" a property of twenty call sites that then drift, which is
    the narrow waist's own argument one level up. `explicit` stays for the caller that genuinely
    acts under a named credential, and is not what a surface uses.
    """
    for candidate in (explicit, os.environ.get("BRAIN_HUMAN")):
        h = str(candidate or "").strip()
        if not h:
            continue
        if h != DEFAULT_HUMAN and not HUMAN_SLUG.match(h):
            raise StoreConfigError(
                f"{h!r} is not a human slug (lowercase, digits and hyphens, 33 characters or "
                f"fewer). It selects a Postgres login and a secret reference id, so it is "
                f"refused rather than quoted. Set BRAIN_HUMAN to a slug "
                f"`swarm admin human list` shows, or unset it to be {DEFAULT_HUMAN!r}."
            )
        return h
    return DEFAULT_HUMAN


def whoami(explicit: str | None = None) -> dict:
    """Who this process IS, according to the database. Lane E's rule A2.

    `human_slug()` says who the process is asking to be. This opens that connection and asks
    `brain.current_human()`, which reads `session_user`, so the answer is the database's.

    THE GAP THIS EXISTS TO CLOSE, which is invisible until the second human arrives. Set
    `CONSOLE_OPERATOR=zosia` on a host holding no credential for `zosia` and, before this, the
    console started cleanly, rendered her name on every card, and then refused every write she
    made for the rest of the evening, one refusal at a time, with a message about a decider
    mismatch. **A configuration error that presents as a permissions error is the worst failure
    available here**, because the reader's first move is to widen a permission.

    Fails soft into a dict with `reachable: False` rather than raising. A surface calls this to
    DECIDE whether it can write, and a status read that raises makes the status page the first
    thing to go down.
    """
    slug = human_slug(explicit)
    out = {"requested": slug, "login": None, "human": None, "agrees": False,
           "reachable": False, "reason": ""}
    try:
        with read("operator", human=None if slug == DEFAULT_HUMAN else slug) as s:
            row = s.one("SELECT session_user AS login, brain.current_human() AS human")
        out.update(login=row["login"], human=row["human"], reachable=True)
        out["agrees"] = bool(row["human"]) and row["human"] == slug
        if not row["human"]:
            out["reason"] = (
                f"connected as {row['login']}, which brain.current_human() does not name. That "
                f"login is not mapped in brain.human_role, so every human-attributed write from "
                f"this process will be refused by the database.")
        elif not out["agrees"]:
            out["reason"] = (
                f"this process asked to be {slug!r} and the database says it is "
                f"{row['human']!r}. The credential it opened belongs to somebody else.")
    except StoreConfigError as exc:
        out["reason"] = str(exc)
    except Exception as exc:                                            # noqa: BLE001
        out["reason"] = f"{exc.__class__.__name__}: {exc}"
    return out


def dsn(role: str, subscriber: str | None = None, human: str | None = None) -> dict:
    """Connection parameters for one role. The password is bound here and nowhere else.

    `subscriber` is not a hint: it selects the LOGIN ROLE. Since migration 10 a listener's identity
    is `session_user` resolved through `brain.subscriber_role`, so connecting as the shared
    `brain_subscriber` login now maps to no subscriber and reads and writes zero cursor rows. Each
    listener therefore holds its own credential, and there is no way to ask for another's, because
    asking would mean holding another's password.

    An unprovisioned subscriber fails closed here, loudly. It does NOT fall back to the shared
    login: that fallback is the defect this replaced, wearing a deprecation notice.
    """
    if role not in ROLES:
        raise StoreConfigError(f"unknown role {role!r}. One of: {', '.join(ROLES)}")
    if subscriber is not None and role != "subscriber":
        raise StoreConfigError(
            f"a subscriber identity was passed with role {role!r}. Only the subscriber role has "
            f"one; every other role is the runtime and names itself in the verb."
        )
    # `human` SELECTS THE LOGIN, exactly as `subscriber` does one branch down, and for the same
    # reason: since migration 20 a human's identity is `session_user` resolved through
    # `brain.human_role`, so several named humans on one instance means several logins and there
    # is no way to ask for another's, because asking would mean holding another's password.
    # ADDITIVE: `human=None` is every caller that existed before named humans, and it resolves
    # `brain_operator` exactly as it always did.
    if human is not None and role != "operator":
        raise StoreConfigError(
            f"a human identity was passed with role {role!r}. Only the operator role carries one: "
            f"every other role is an agent surface and names itself in the verb."
        )
    if human is not None and str(human).strip() not in ("", "operator"):
        user, ref = human_role_name(human), human_secret_ref(human)
    elif subscriber is None:
        user, ref = f"brain_{role}", SECRET_REFS[role]
    else:
        user, ref = subscriber_role_name(subscriber), f"brain-postgres-subscriber-{subscriber}"
    try:
        password = resolve_secret(ref)
    except StoreConfigError:
        if role == "operator":
            # THE INTENDED OUTCOME ON AN AGENT'S HOST, not an error to route around. A process
            # that does not hold the operator's credential is not the operator, and the fallback
            # a reader will reach for -- "just use brain_runtime" -- is precisely the forgery
            # migration 20 exists to refuse. A NAMED human fails closed the same way and must
            # NOT fall back to brain_operator either: one human writing as another is the
            # self-service identity migration 10 removed, wearing a friendlier name.
            named = human is not None and str(human).strip() not in ("", "operator")
            provisioner = (f"    store/bin/provision-human.sh --db "
                           f"{os.environ.get('BRAIN_PG_DB', 'brain')} --human {human}"
                           if named else
                           f"    store/bin/provision-operator.sh --db "
                           f"{os.environ.get('BRAIN_PG_DB', 'brain')}")
            raise StoreConfigError(
                f"this process holds no credential for {user} (secret reference {ref!r} did not "
                f"resolve), so it cannot write work that belongs to a human. If you ARE that "
                f"human on this host, provision it:\n"
                f"{provisioner}\n"
                f"Refusing to fall back to brain_runtime: that login is an agent, and an agent "
                f"posting itself an actor_type=human item is the forgery web/rooms.py refuses, "
                f"arriving from the other side."
            ) from None
        if subscriber is None:
            raise
        raise StoreConfigError(
            f"subscriber {subscriber!r} has no credential of its own (secret reference {ref!r} "
            f"did not resolve), so it cannot prove who it is. Provision it:\n"
            f"    store/bin/provision-subscriber.sh --db "
            f"{os.environ.get('BRAIN_PG_DB', 'brain')} --subscriber {subscriber}\n"
            f"Refusing to connect as the shared brain_subscriber login instead: that login is "
            f"nobody, and a listener that runs as nobody is the self-service identity migration "
            f"10 removed."
        ) from None
    return {
        "host": os.environ.get("BRAIN_PG_HOST", "127.0.0.1"),
        "port": int(os.environ.get("BRAIN_PG_PORT", "5432")),
        "dbname": os.environ.get("BRAIN_PG_DB", "brain"),
        "user": user,
        "password": password,
        "options": f"-c search_path=brain,public -c statement_timeout={STATEMENT_TIMEOUT[role]}",
        "application_name": os.environ.get("BRAIN_APP_NAME", f"store/{role}"),
        "connect_timeout": 5,
    }


def _connect(role: str, subscriber: str | None = None, human: str | None = None):
    try:
        return psycopg2.connect(**dsn(role, subscriber, human))
    except psycopg2.OperationalError as exc:
        who = (human_role_name(human) if human else
               subscriber_role_name(subscriber) if subscriber else f"brain_{role}")
        raise StoreConfigError(f"could not connect as {who}: {exc}") from None


@contextlib.contextmanager
def read(role: str = "runtime", subscriber: str | None = None, human: str | None = None):
    """A READ ONLY session. Exposed broadly, because it cannot write.

    The read-only property is set by Postgres, not by this module:

        SET TRANSACTION READ ONLY

    A caller that gets clever and issues an INSERT through this session gets
    `read-only sql transaction` from the server. There is no flag on this function that turns
    that off, and adding one would be the change that quietly ends the narrow waist.

    `subscriber` CONNECTS AS that subscriber's own role. It used to `SET brain.subscriber`, which
    was a string the client chose, and one subscriber used it to advance another's cursor
    (measured, 2026-08-16). Identity is now `session_user`, established at authentication and
    unreachable from SQL, and a role with no mapping row reads nothing rather than everything.
    """
    conn = _connect(role, subscriber, human)
    try:
        conn.set_session(readonly=True, autocommit=False)
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            yield _ReadSession(cur)
        conn.rollback()
    finally:
        conn.close()


class _ReadSession:
    """A cursor with a query method and nothing else.

    Deliberately not a psycopg2 cursor subclass and deliberately not returning one: handing a
    caller a real cursor hands them `copy_expert`, `callproc`, and a connection attribute, and
    the point of this class is that there is no route from a read to a write.
    """

    __slots__ = ("_cur",)

    def __init__(self, cur):
        self._cur = cur

    def query(self, sql: str, params=None) -> list:
        """Run one SELECT and return every row as a dict."""
        try:
            self._cur.execute(sql, params)
        except psycopg2.errors.ReadOnlySqlTransaction as exc:
            raise ReadOnlyViolation(
                "a write was attempted through store.read(). State changes go through "
                "store.apply(verb). Postgres refused this, which is the intended design."
            ) from exc
        if self._cur.description is None:
            return []
        return [dict(r) for r in self._cur.fetchall()]

    def one(self, sql: str, params=None):
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def scalar(self, sql: str, params=None):
        row = self.one(sql, params)
        return None if row is None else next(iter(row.values()))


def health() -> dict:
    """Server liveness AND listener lag, because they are different questions.

    `pg_isready` proves the server is up and nothing about the listeners, which bind no port and
    fall outside the port registry rules entirely. Listener health is lag:
    `max(event_seq) - subscriber_cursor.last_seq`.

    `ahead_of_head` and `moved_backwards` ride along because this dict reaches a status line, and
    a status line that shows `lag` without them will render an impossible number in the reassuring
    colour. `fabric.lag` is where the ladder and the quarantine live; this is the honest cheap read.
    """
    with read("runtime") as s:
        return {
            "server": s.scalar("SELECT 'up'"),
            "schema_version": s.scalar("SELECT max(version) FROM brain.schema_migration"),
            "head_seq": s.scalar("SELECT COALESCE(max(event_seq), 0) FROM brain.event"),
            "subscribers": s.query(
                "SELECT subscriber, last_seq, lag, quarantined, ahead_of_head, moved_backwards "
                "  FROM brain.subscriber_lag ORDER BY subscriber"
            ),
        }
