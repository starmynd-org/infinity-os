"""The seam onto R01's state API.

R01 owns `R/migrations/**`, `R/queue/schema/**` and `R/ingest/schema/**`, and has not published a
state API at the time this was written. So capture does not import a store: it takes one that
satisfies `JournalStore`, and `sqlite_store.SqliteJournalStore` is the reference implementation
that lets the failure model be tested on a disposable file today.

WHAT THE PORT GUARANTEES, and what a Postgres implementation must therefore also guarantee:

1. `append` is atomic over the journal row AND its outbox intents. Either both are visible after
   a crash or neither is. Half of that pair is the bug where a mailbox gets marked read for an
   event that was never recorded.
2. `append` returns a monotonically increasing `journal_seq` starting at 1. Replay is defined as
   "every row with seq > frontier", so a non-monotonic sequence breaks recovery.
3. `append` is idempotent on `capture_id`. A retry after an ambiguous commit returns the ORIGINAL
   row and its original seq; it never writes a second row and never renumbers.
4. Nothing is ever updated or deleted. Revisions and tombstones are new rows. `head` is derived
   by reading the rows, not by mutating a pointer, so the journal stays append-only and the
   history stays auditable.

THREE THINGS FOR WHOEVER WRITES THE POSTGRES IMPLEMENTATION, each paid for by Terminal 04 rather
than by me, and each the kind that works on the store in front of you:

* **Ask before you write, and keep the constraint as the backstop.** Inside a transition a failed
  statement aborts the whole transaction, so an `except UniqueViolation` handler cannot then query
  for the original row: it dies on `InFailedSqlTransaction`. Guarantee 3 above therefore has to be
  a read followed by a write, with the UNIQUE constraint catching only the genuine race. The
  SQLite reference implementation is already shaped this way.
* **Never interleave two database sessions on one thread in a test scene.** Separate connections
  and a lock timeout. T04 hung a scene exactly this way, and a hang is worse than a failure
  because it has no message.
* **`scratch-db.sh` maps the operator login AFTER every migration runs**, so on a fresh `create`
  the human roster is empty. A self-check assuming a human exists passes on `migrate` and fails on
  `create`: it works on the store in front of you and breaks on the one that ships. Build a
  fixture row when the roster is empty.

PINNED AGAINST: **STATE-API-PORT v4**, digest
`sha256:87fe2bd228d2e917e61b43d0a2b3c685bc435d039ae7aab50ad7e3f70bedc9c8`, covering the ledger-67
shape. Verified by recomputing it with the split recipe, not taken from the message that announced
it. The port moves on a VERSION BUMP and not on a message.

**THE PIN DOES NOT MOVE FOR I02-RECONCILE-01, AND THE TWO VERBS BELOW ARE AN UNRATIFIED LOCAL
EXTENSION.** `reconcile_dead_letter` and `unreconciled_dead_letters` were added to `JournalStore`
for CAP14-REV-041. `JournalStore` is this lane's own seam, so adding to it is mine to do, but it
WIDENS what this lane needs from R01 and that half is not mine: whether the Postgres state API
grows an equivalent verb is T04's to answer, and it has not been asked as of this commit. Stated
plainly rather than folded into the pin: **the SQLite reference implementation can regain
readiness after a reconciliation and a Postgres-backed store cannot until T04 provides the verb.**
No schema number is invented here for the same reason. If T04 declines, this lane's answer is that
reconciliation lives outside the state API entirely, and that is a design change rather than a
silent gap.

**v4 IS A MAJOR BUMP AND IT DOES NOT TOUCH THIS LANE.** It changes `ex.settle`: two new required
keyword arguments (`completeness`, `effect_state`) and the outcome `succeeded` renamed `success`.
This lane makes exactly ONE call against the state API, `authority.check`, and no call against the
execution port at all, which was verified by search rather than assumed before recording the pin.
So v4 is recorded and nothing moves.

ONE THING THE DOCUMENT DOES NOT YET CARRY: its `covers:` line reads `PLACEHOLDER_COMMIT`. The
version and the digest are real and verifiable, and the digest is what a consumer checks; the
commit reference is not resolvable yet. Recorded here as a known limitation rather than
silently pinning to a name that does not exist, because the program rule is to verify against a
NAMED COMMIT and for v4 there is not one yet. If a later reader needs commit-level provenance for
v4, it must come from T04 and not from this line.

**This block said v1 while `attention/rules/authority_port.py` said v2**, because the v2 adaptation
updated the consumer and left the canonical pin behind. One pin in two files is one pin too many:
this is now the only place a version is recorded, and the adapter points here rather than repeating
it. A record that can disagree with itself is worse than a record kept in one place, and this one
did disagree for two commits.

WHAT v2 CHANGED (ledger 63): `workspace` became a COLUMN on the authority rows and a REQUIRED,
KEYWORD-ONLY argument to `check`, `in_force`, `grant`, `revoke`, `decide`, `acquire` and `reserve`
-- not a component of the scope string. So the scope this lane builds did not change shape:
`source/<source_key>` stays exactly that and sits inside a workspace rather than beside one. That
is the answer to "can the two disagree": if the workspace were in the scope string, two lanes would
each construct that string and the constructions could drift; as a separate dimension there is one
place it is compared and no string for anyone to build differently. The value passed is the
workspace the capture record carries, unchanged -- not derived, not reconstructed, not normalised
-- so a disagreement is findable as a join rather than buried inside a concatenation.

WHAT v3 CHANGED (ledger 65), and why the pin moved anyway: migration 0065 renamed `fencing_token`
to the contract's `lease_epoch` across the EXECUTION port, and this lane touches no leases. T04
offered the choice of staying at v2 knowingly, on the grounds that a stale pin somebody CHOSE is
not the same object as a stale pin somebody MISSED. That distinction is right. The pin moved for a
narrower reason: verifying took one recomputation plus reading v3's own signature to confirm
`authority.check(subject, capability, scope, *, workspace, proposal=None, ctx=None)` is
byte-identical to the call this lane makes. Having done that, recording v2 would have been
recording less than I knew. A deliberate lag is defensible when the check is expensive; it was not.

THE SCHEMA IS `ingest`, NOT `brain`, and therefore takes no ledger number and declares no version.
Nothing outside this lane joins these rows to `brain.*`; cross-plane references are id strings and
never foreign keys, which is what keeps the door replaceable and lets it run with no database login
at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Iterator, Mapping, Protocol


@dataclass(frozen=True)
class JournalRow:
    """One committed row, as the store hands it back."""

    journal_seq: int
    capture_id: str
    source_key: str
    kind: str
    revision: int
    content_digest: str
    raw_digest: str | None
    supersedes: str | None
    committed_at: str
    record: Mapping[str, Any]


@dataclass(frozen=True)
class HeadState:
    """The current state of one source identity, derived from its rows."""

    source_key: str
    revision: int
    content_digest: str
    capture_id: str
    live: bool
    journal_seq: int


@dataclass(frozen=True)
class OutboxIntent:
    """A source-hygiene action the manifest permits, staged with its journal row.

    It is an INTENT, not an action. Nothing here reaches a provider; `outbox.py` decides when an
    intent becomes claimable and this packet never executes one.
    """

    action: str
    target: str
    params: Mapping[str, Any] | None = None


class JournalStore(Protocol):
    """Everything capture needs from persistent state. Deliberately small."""

    def append(
        self, row: Mapping[str, Any], intents: Iterable[OutboxIntent] = ()
    ) -> tuple[int, bool]:
        """Commit one journal row plus its outbox intents atomically.

        Returns `(journal_seq, created)`. `created is False` means this `capture_id` was already
        present and the existing seq is being returned -- the idempotent path.
        """
        ...

    def get(self, capture_id: str) -> JournalRow | None: ...

    def head(self, source_key: str) -> HeadState | None: ...

    def rows_since(self, frontier: int = 0, limit: int | None = None) -> Iterator[JournalRow]: ...

    def live_source_keys(self) -> set[str]: ...

    def count_events(self) -> int: ...

    def max_seq(self) -> int: ...

    def dead_letter(self, entry: Mapping[str, Any]) -> str: ...

    def dead_letters(self) -> Iterator[Mapping[str, Any]]: ...

    def reconcile_dead_letter(self, entry: Mapping[str, Any]) -> bool:
        """Record that a human has dealt with one dead letter. Returns False if already recorded.

        I02-RECONCILE-01 (CAP14-REV-041). Before this verb existed the store could `dead_letter`
        and `dead_letters` and nothing could resolve one, so a correct gate became a wall: a
        single permanently refused payload, which `retry.py` treats as a NORMAL event because a
        payload the contract refuses is permanent for those bytes, ended unattended operation for
        that source for ever.

        A RECONCILIATION IS AN APPEND, NOT AN UPDATE. The dead letter itself is never edited; a
        separate row records who resolved it, when, and how. The journal's append-only property is
        the reason an operator can trust what it says about yesterday, and buying readiness back by
        rewriting history would be a poor trade.

        The store enforces existence and idempotency. It does NOT decide whether a resolution is
        legitimate: that is `capture.reconcile`, which is where the rule that a named actor must
        resolve it lives, because a store that could invent a resolver is a store that can clear
        its own losses.
        """
        ...

    def unreconciled_dead_letters(self, source_key: str | None = None) -> int:
        """Dead letters nothing has resolved. `None` counts every source."""
        ...

    def claimable_intents(self) -> Iterator[Mapping[str, Any]]: ...

    def mark_intent_done(self, intent_id: int, result: str) -> None: ...
