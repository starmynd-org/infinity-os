"""The listener: what a subscriber is, and the four things it must prove before it consumes.

A listener is the safety-bearing half of this fabric, so it does not simply connect and read. It
proves four preconditions at start and refuses to run if any of them fails. Every one of them is
a failure that would otherwise be silent.

1. **It is declared.** `_system/subscriber-registry-rules.md`: "Declare, never discover. A listener
   that consumes a type prefix it has not declared is out of contract, whatever its code does,"
   and "on disagreement between the declaration and the runtime, git wins and the service fails
   closed at start." So the declaration is read from git, the consumed prefixes come from it
   rather than from code, and the commit it was read at is stamped on the cursor
   (`declaration_commit`, EF-23).

2. **It cannot write to `event`.** Verified by trying, on a live connection opened by this
   process with this listener's own credentials, at every start. D1 verified the grant at grant
   time; a grant nobody re-tests is a grant that survives exactly until someone runs a convenient
   `GRANT` to fix an unrelated problem. If the INSERT succeeds, the listener does not start.

3. **The DATABASE knows who it is.** `subscriber_cursor` is under FORCED row-level security and,
   since migration 10, the policy matches `session_user` through `brain.subscriber_role`. This
   used to match `current_setting('brain.subscriber')`, a setting the client sends: one listener
   set it to another's name and moved that listener's cursor from 10 to 4242 (measured
   2026-08-16). A listener now connects with its own credential and ASKS the database which
   subscriber that credential is; if the answer is not the name it is running under, it does not
   start. A role with no mapping is nobody and reads nothing.

4. **It is not quarantined.** A quarantined listener that quietly resumes is worse than one that
   stays down, because the condition that quarantined it is still there.

**Health is lag, not liveness.** `pg_isready` proves the server is up and proves nothing about
whether anything is listening, and a listener that is running, connected and stuck reports healthy
under every process-level check. Listeners bind no port, so the port registry rules do not reach
them at all. The signal is `max(event_seq) - subscriber_cursor.last_seq`, and it is built here
with the first listener rather than after it.
"""

from __future__ import annotations

import os
import select
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import psycopg2
import psycopg2.errors

import store
from store import session as _store_session

from . import emit as _emit

#: Where the declaration of record lives. The registry entry is a git artifact; the cursor is a
#: runtime one; the join key is the subscriber slug. One string, two planes, no translation table.
#:
#: THE DEFAULT USED TO BE ONE LAPTOP'S PATH, `/mnt/c/Users/you/repos/internal/
#: your-brain`, and that made brain-paging refuse at start on every install that is
#: not that laptop (W5-S6, clean install, 2026-09-16), which in turn failed brain-health on every
#: sweep. It is now resolved, in order: `SUBSCRIBERS_DECLARATION`; `BRAIN_ROOT`; else the brain
#: checked out BESIDE this install, `<install>/../your-brain`, the same sibling
#: convention `systemd/brain-n8n.service` already ships. On the operator's laptop that is the
#: identical path, so nothing moves there. On an install with no brain beside it the file does not
#: exist, the subscriber is not declared on that host, and `declared_here()` says so by name.
INSTALL_ROOT = Path(__file__).resolve().parents[1]
SIBLING_BRAIN = "your-brain"


def declaration_path(env=None, install_root: Path = None) -> Path:
    """The SUBSCRIBERS.md this host would read, whether or not it exists. Pure, so a test can
    hand it an environment and an install location."""
    env = os.environ if env is None else env
    if env.get("SUBSCRIBERS_DECLARATION"):
        return Path(env["SUBSCRIBERS_DECLARATION"])
    root = Path(env["BRAIN_ROOT"]) if env.get("BRAIN_ROOT") else \
        (install_root or INSTALL_ROOT).parent / SIBLING_BRAIN
    return root / "departments" / "SUBSCRIBERS.md"


BRAIN_ROOT = declaration_path().parent.parent
DECLARATION_PATH = declaration_path()


def declared_here(path: Path = None) -> tuple:
    """(True, "") when this host carries a subscriber declaration file at all, else (False, why).

    ABSENCE OF THE FILE IS THE ONLY THING THIS FORGIVES. A file that exists but lacks the entry, or
    carries a broken one, is still `StartupRefused` at start, loudly, because that is a declaration
    somebody wrote wrong. No file means this host was never given a brain to declare subscribers
    in -- a customer install -- and there "not declared here" is the true state, not a fault.
    """
    path = path or DECLARATION_PATH
    if path.exists():
        return True, ""
    return False, (f"no subscriber declaration on this host (looked for {path}; set BRAIN_ROOT or "
                   f"SUBSCRIBERS_DECLARATION to name one). Declare, never discover: an undeclared "
                   f"listener does not run, and on an install without a brain that is the correct "
                   f"state rather than a failure.")

#: A poison event is one the handler fails on repeatedly. This many consecutive failures on the
#: SAME event_seq quarantines the subscriber.
QUARANTINE_AFTER_CONSECUTIVE_FAILURES = 3


class StartupRefused(RuntimeError):
    """A precondition failed. The listener is not running, and that is visible.

    A service running on stale authority is worse than a service that is down, because a down
    service is visible. That is R5's posture and it is this class's whole reason to exist.
    """


class GateBreach(RuntimeError):
    """The subscriber role could write to `event`. Nothing starts until a human looks at this."""


@dataclass
class Declaration:
    """One entry from `departments/SUBSCRIBERS.md`, parsed rather than assumed."""
    subscriber: str
    owning_department: str
    action_class: str          # prepare | effect
    flagged_posture: str       # prepare-only | gated
    consumes: tuple            # event type prefixes, segment-boundary matched
    phase: int
    status: str                # planned | live | quarantined | retired
    commit: str = ""

    @property
    def gated(self) -> bool:
        """`gated` means this subscriber NEVER sees a flagged event's contents.

        Mandatory for `Action class: effect`. It gets a tombstone instead, so the cursor still
        advances past the row and the gating is countable rather than invisible.
        """
        return self.flagged_posture == "gated"


def _git_head(root: Path) -> str:
    try:
        out = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=15)
        return out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def load_declaration(subscriber: str, path: Path = None) -> Declaration:
    """Read the entry from git. Fails closed on anything short of a complete, live entry."""
    path = path or DECLARATION_PATH
    if not path.exists():
        raise StartupRefused(
            f"no subscriber declaration at {path}. EF-6 was accepted precisely so a subscription "
            f"is a declared, checkable fact rather than an inference from code. A listener with "
            f"no entry is an undeclared subscription and is unavailable by default."
        )
    text = path.read_text(encoding="utf-8")

    block = None
    for chunk in text.split("\n## ")[1:]:
        if chunk.splitlines()[0].strip() == subscriber:
            block = chunk
            break
    if block is None:
        raise StartupRefused(
            f"{subscriber!r} has no entry in {path}. Declare, never discover: a listener that "
            f"consumes a type prefix it has not declared is out of contract, whatever its code "
            f"does."
        )

    def field_of(name: str) -> str:
        for line in block.splitlines():
            s = line.strip().lstrip("-").strip()
            if s.lower().startswith(name.lower() + ":"):
                return s.split(":", 1)[1].strip().strip("`")
        raise StartupRefused(
            f"{subscriber}: the declaration is missing the required field {name!r}. The entry "
            f"shape is fixed so a check can parse the registry mechanically without "
            f"per-subscriber adapters; a partial entry is not a declaration."
        )

    consumes = tuple(p.strip().strip("`") for p in field_of("Consumes").split(",") if p.strip())
    if not consumes:
        raise StartupRefused(f"{subscriber}: Consumes is empty. A subscriber that consumes "
                             f"nothing is a process, not a subscription.")
    if any("*" in p for p in consumes):
        raise StartupRefused(
            f"{subscriber}: Consumes carries a wildcard ({consumes}). Explicit values only: no "
            f"wildcards inside a segment, no suffix or infix wildcards, no predicate."
        )

    d = Declaration(
        subscriber=subscriber,
        owning_department=field_of("Owning department"),
        action_class=field_of("Action class").lower(),
        flagged_posture=field_of("Flagged-event posture").lower(),
        consumes=consumes,
        phase=int(field_of("Phase")),
        status=field_of("Status").lower(),
        commit=_git_head(path.parent.parent),
    )

    if d.action_class not in ("prepare", "effect"):
        raise StartupRefused(f"{subscriber}: Action class must be prepare or effect, "
                             f"got {d.action_class!r}. Never both.")
    if d.action_class == "effect" and not d.gated:
        raise StartupRefused(
            f"{subscriber}: Action class 'effect' makes 'Flagged-event posture: gated' MANDATORY, "
            f"and this entry says {d.flagged_posture!r}. An effect-class subscriber that sees "
            f"flagged contents is gated only by its own good behaviour, which is not a gate."
        )
    if d.status != "live":
        raise StartupRefused(
            f"{subscriber}: Status is {d.status!r}, not 'live'. A declaration is the authority a "
            f"listener runs under and it says this one is not running."
        )
    return d


def verify_cannot_write_event(subscriber: str) -> dict:
    """Prove the safety property from INSIDE this process, with this listener's own credentials.

    Deliberately opens a read-WRITE connection to do it. A read-only session would raise
    `read_only_sql_transaction` (25006) and that proves nothing about the grant: it proves a flag
    this process set on itself. The only result that counts is `insufficient_privilege` (42501),
    which comes from the grant and cannot be turned off by application logic.

    Returns the evidence. Raises `GateBreach` if any write lands.
    """
    evidence = {}
    conn = psycopg2.connect(**_store_session.dsn("subscriber", subscriber))
    try:
        conn.set_session(readonly=False, autocommit=False)
        attempts = {
            "INSERT": ("INSERT INTO brain.event (type, external, canon_touching) "
                       "VALUES ('question.raised', false, false)", ()),
            "UPDATE": ("UPDATE brain.event SET payload_summary = 'tampered'", ()),
            "DELETE": ("DELETE FROM brain.event", ()),
        }
        for name, (sql, params) in attempts.items():
            with conn.cursor() as cur:
                try:
                    cur.execute(sql, params)
                except psycopg2.errors.InsufficientPrivilege as exc:
                    evidence[name] = f"{exc.pgcode} {str(exc).strip()}"
                    conn.rollback()
                except psycopg2.errors.ReadOnlySqlTransaction as exc:
                    conn.rollback()
                    raise GateBreach(
                        f"{name} on brain.event was refused by the session's read-only flag "
                        f"({exc.pgcode}), not by the grant. That is an inconclusive test and this "
                        f"listener will not run on an inconclusive safety proof."
                    ) from None
                else:
                    conn.rollback()
                    raise GateBreach(
                        f"{name} on brain.event SUCCEEDED as brain_{'subscriber'}. The subscriber "
                        f"role has a write path to the bus. Nothing consumes until a human has "
                        f"looked at this: it is the one grant that does not collapse under "
                        f"simplification and it has collapsed."
                    )
    finally:
        conn.close()
    return evidence


@dataclass
class Batch:
    rows: list = field(default_factory=list)
    head_seq: int = 0
    gated_out: int = 0


class Listener:
    """The base every subscriber inherits. Subclasses implement `handle(row)` and nothing else.

    The doorbell wakes it; the cursor tells it what it missed. Those are different jobs and
    conflating them is how a fabric loses its first event: a notification carries no delivery
    guarantee, so the catch-up query is the correctness mechanism and the notification is only
    the latency mechanism.
    """

    #: Wake up at least this often even with no doorbell, so a dropped NOTIFY costs latency and
    #: never costs an event.
    poll_seconds = 15.0
    batch_size = 200

    def __init__(self, subscriber: str, declaration_path: Path = None, log=None):
        self.subscriber = subscriber
        self.declaration = load_declaration(subscriber, declaration_path)
        self.log = log or (lambda **kw: print(_fmt(kw), file=sys.stderr, flush=True))
        self._conn = None
        self._fail_seq = None
        self._fail_count = 0
        self.gated_total = 0

    # ---------------------------------------------------------------- lifecycle

    def start(self) -> dict:
        """Run every precondition, then connect. Returns the startup evidence."""
        d = self.declaration
        gate = verify_cannot_write_event(self.subscriber)
        self.log(event="gate_verified", subscriber=self.subscriber, **gate)

        cur = self.cursor_row()
        if cur and cur.get("quarantined"):
            raise StartupRefused(
                f"{self.subscriber} is quarantined (since {cur.get('updated_at')}). Release is a "
                f"human's act through the runtime role: `fabric subscriber release "
                f"--subscriber {self.subscriber} --by <name>`. A listener that cleared its own "
                f"quarantine would retry the poisoned event forever and the operator would see a "
                f"sawtooth instead of a condition."
            )

        self._conn = psycopg2.connect(**_store_session.dsn("subscriber", self.subscriber))
        # autocommit so NOTIFY is delivered (notifications do not arrive inside an open
        # transaction), read-only so this long-lived connection keeps store.read()'s guarantee:
        # the only write this process makes is `event ack`, on its own connection, through apply.
        self._conn.set_session(readonly=True, autocommit=True)
        with self._conn.cursor() as c:
            # Precondition 3, and it is now a QUESTION rather than a declaration. Nothing this
            # process can send changes the answer: `session_user` was fixed when the credential
            # authenticated, and the mapping table is readable by no role this process holds.
            c.execute("SELECT brain.current_subscriber(), session_user")
            who, role = c.fetchone()
            if who != self.subscriber:
                self._conn.close()
                self._conn = None
                raise StartupRefused(
                    f"this process is running as {self.subscriber!r} and the database says the "
                    f"credential it connected with is {who!r} (role {role}). A listener does not "
                    f"get to settle that disagreement: it is not starting. If {self.subscriber!r} "
                    f"is a real subscriber, provision it -- "
                    f"store/bin/provision-subscriber.sh --subscriber {self.subscriber}"
                )
            c.execute(f"LISTEN {_emit.CHANNEL}")
        self.log(event="started", subscriber=self.subscriber, action_class=d.action_class,
                 flagged_posture=d.flagged_posture, consumes=",".join(d.consumes),
                 declaration_commit=d.commit or "(none)", session_user=role)
        return gate

    def stop(self):
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ---------------------------------------------------------------- reading

    def cursor_row(self) -> dict | None:
        with store.read("subscriber", subscriber=self.subscriber) as s:
            return s.one(
                "SELECT subscriber, last_seq, head_seq, lag, quarantined, updated_at "
                "  FROM brain.subscriber_lag WHERE subscriber = %s", (self.subscriber,))

    def last_seq(self) -> int:
        row = self.cursor_row()
        return int(row["last_seq"]) if row else 0

    def read_batch(self) -> Batch:
        """Everything past the cursor that this subscriber declared, flagged rows GATED IN SQL.

        The blanking is a `CASE` in the SELECT, not a filter in Python, on purpose: for a `gated`
        subscriber the flagged payload never crosses the process boundary at all. Blanking after
        the fetch would mean the bytes were in the listener's address space and the gate would be
        a promise about what the code does with them.
        """
        d = self.declaration
        prefixes = list(d.consumes)
        # Segment-boundary prefix match, pushed into SQL so the cursor advances over rows this
        # subscriber does not consume rather than stalling behind them.
        where_type = "(" + " OR ".join(
            ["(e.type = %s OR e.type LIKE %s)"] * len(prefixes)) + ")"
        params: list = []
        for p in prefixes:
            params.extend([p, (p if p.endswith(".") else p + ".") + "%"])

        flagged = "(e.external OR e.canon_touching)"
        if d.gated:
            select_list = f"""
                e.event_seq, e.event_id, e.type, e.occurred_at, e.external, e.canon_touching,
                CASE WHEN {flagged} THEN '' ELSE e.department     END AS department,
                CASE WHEN {flagged} THEN '' ELSE e.lane           END AS lane,
                CASE WHEN {flagged} THEN '' ELSE e.subject_type   END AS subject_type,
                CASE WHEN {flagged} THEN '' ELSE e.subject_id     END AS subject_id,
                CASE WHEN {flagged} THEN '' ELSE e.payload_summary END AS payload_summary,
                CASE WHEN {flagged} THEN NULL ELSE e.payload_ref  END AS payload_ref,
                CASE WHEN {flagged} THEN NULL ELSE e.work_item_id END AS work_item_id,
                {flagged} AS tombstone"""
        else:
            select_list = f"""
                e.event_seq, e.event_id, e.type, e.occurred_at, e.external, e.canon_touching,
                e.department, e.lane, e.subject_type, e.subject_id, e.payload_summary,
                e.payload_ref, e.work_item_id, {flagged} AS tombstone"""

        with store.read("subscriber", subscriber=self.subscriber) as s:
            head = s.scalar("SELECT COALESCE(max(event_seq), 0) FROM brain.event") or 0
            rows = s.query(
                f"SELECT {select_list} FROM brain.event e "
                f" WHERE e.event_seq > %s AND {where_type} "
                f" ORDER BY e.event_seq LIMIT %s",
                [self.last_seq()] + params + [self.batch_size],
            )
        gated_out = sum(1 for r in rows if r.get("tombstone") and self.declaration.gated)
        return Batch(rows=rows, head_seq=head, gated_out=gated_out)

    # ---------------------------------------------------------------- the loop

    def handle(self, row: dict) -> None:
        raise NotImplementedError

    def drain(self) -> int:
        """Handle everything past the cursor. Acks per row, so a crash re-delivers one row.

        At-least-once, never at-most-once: a duplicate page is an annoyance and a dropped page is
        the failure this fabric exists to prevent.
        """
        n = 0
        while True:
            batch = self.read_batch()
            if not batch.rows:
                # Nothing left that this subscriber consumes, so the cursor advances to the head
                # it has examined. Without this, `max(event_seq) - last_seq` counts events the
                # subscriber never declared an interest in, and any subscriber consuming a subset
                # of types shows permanent non-zero lag with nothing wrong. Measured: a drain that
                # ended on six unconsumed `session.*` rows left lag at 6 forever.
                #
                # It is safe because `read_batch` reads `head_seq` BEFORE the rows, so head can
                # only ever be behind what was examined, never ahead of it, and `event ack` takes
                # GREATEST so a cursor never moves backwards.
                if batch.head_seq > self.last_seq():
                    _emit.ack(self.subscriber, batch.head_seq, self.declaration.commit)
                return n
            truncated = len(batch.rows) >= self.batch_size
            for row in batch.rows:
                seq = int(row["event_seq"])
                try:
                    self.handle(row)
                except Exception as exc:               # noqa: BLE001 - the poison-event path
                    self._on_failure(seq, exc)
                    return n
                self._fail_seq, self._fail_count = None, 0
                _emit.ack(self.subscriber, seq, self.declaration.commit)
                n += 1
                if row.get("tombstone") and self.declaration.gated:
                    self.gated_total += 1
            if not truncated and batch.head_seq > self.last_seq():
                _emit.ack(self.subscriber, batch.head_seq, self.declaration.commit)

    def _on_failure(self, seq: int, exc: Exception) -> None:
        """Quarantine is defined here, and it is defined by repetition on ONE event.

        A handler that fails once has hit a blip. A handler that fails three times on the same
        `event_seq` has hit an event it cannot process, and retrying it forever means every event
        behind it is stuck too. Quarantine stops the cursor deliberately so lag climbs and keeps
        climbing, which is the one signal an operator cannot mistake for idleness.
        """
        if self._fail_seq != seq:
            self._fail_seq, self._fail_count = seq, 0
        self._fail_count += 1
        self.log(event="handler_failed", subscriber=self.subscriber, event_seq=seq,
                 attempt=self._fail_count, error=f"{type(exc).__name__}: {exc}")
        if self._fail_count >= QUARANTINE_AFTER_CONSECUTIVE_FAILURES:
            reason = (f"{self._fail_count} consecutive failures on event_seq {seq}: "
                      f"{type(exc).__name__}: {exc}")
            _emit.quarantine(self.subscriber, reason)
            self.log(event="quarantined", subscriber=self.subscriber, event_seq=seq,
                     reason=reason)
            raise StartupRefused(
                f"{self.subscriber} quarantined itself on event_seq {seq}. {reason}"
            ) from exc

    def run(self, until=None) -> None:
        """LISTEN, drain, sleep, repeat. `until` is a predicate, for tests and demonstrations."""
        self.start()
        try:
            self.drain()
            while until is None or not until():
                if select.select([self._conn], [], [], self.poll_seconds) == ([], [], []):
                    self.drain()          # the poll leg: a dropped doorbell costs latency only
                    continue
                self._conn.poll()
                rung = len(self._conn.notifies)
                self._conn.notifies.clear()
                if rung:
                    self.log(event="doorbell", subscriber=self.subscriber, notifications=rung)
                self.drain()
        finally:
            self.stop()


def _fmt(kw: dict) -> str:
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return stamp + "  " + "  ".join(f"{k}={v}" for k, v in kw.items())
