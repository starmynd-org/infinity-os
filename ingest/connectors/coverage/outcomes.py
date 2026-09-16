"""Structured run outcomes, so "nothing happened" and "nothing worked" are different words.

E07/E08/E12 are all versions of one failure: a connector that reports success because it did not
crash. A run that fetched zero items because the inbox was quiet and a run that fetched zero items
because the token expired look identical in a log line that says "sweep complete".

`RunStatus` therefore has no boolean and no "ok". Every terminal value names WHY:

    complete   -- the authorised window was exhausted and every eligible item is settled
    partial    -- the run ended early on purpose (budget, rate limit) with work still owed
    quiet      -- the window was exhausted and there was genuinely nothing new
    failed     -- the provider or the credential broke; coverage is UNKNOWN, not zero
    reset      -- the provider invalidated its ids; the window is being rescanned

`unattended_ready` is deliberately conservative and deliberately not a health check: it says this
RUN behaved, never that the source is safe to leave running on a host. I02's claim is fixture
coverage and recovery; unattended availability needs the later authorised canary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .cursors import Cursor, EpochReset


class RunStatus(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    QUIET = "quiet"
    FAILED = "failed"
    RESET = "reset"


@dataclass(frozen=True)
class ItemDisposition:
    source_id: str
    ordering_key: str
    disposition: str  # captured | duplicate | dead-lettered | deferred
    detail: str = ""

    @property
    def settled(self) -> bool:
        """Settled means the frontier may pass it. A deferred item is NOT settled."""
        return self.disposition in ("captured", "duplicate", "dead-lettered")


@dataclass(frozen=True)
class RunOutcome:
    source_ref: str
    status: RunStatus
    started_at: str
    finished_at: str
    cursor_before: dict[str, Any]
    cursor_after: dict[str, Any]
    items: tuple[ItemDisposition, ...] = ()
    pages_fetched: int = 0
    epoch_reset: EpochReset | None = None
    error_kind: str | None = None
    error_detail: str = ""
    retry_after_s: float | None = None
    backlog_remaining: int | None = None
    notes: tuple[str, ...] = ()
    #: Whether every dead letter this run produced reached a DURABLE log. False means an item left
    #: the retry ledger uncaptured and only this object remembers it, which lasts until the process
    #: exits. CAP14-REV-038: an in-memory disposition is an account of a loss, not a record of one.
    durable_accounting: bool = True
    #: Dead letters for this source that nothing has reconciled yet, read from the durable log at
    #: the end of the run. `None` means no log was configured, so the question was not asked.
    unreconciled_dead_letters: int | None = None

    @property
    def captured(self) -> int:
        return sum(1 for i in self.items if i.disposition == "captured")

    @property
    def duplicates(self) -> int:
        return sum(1 for i in self.items if i.disposition == "duplicate")

    @property
    def dead_lettered(self) -> int:
        return sum(1 for i in self.items if i.disposition == "dead-lettered")

    @property
    def deferred(self) -> int:
        return sum(1 for i in self.items if i.disposition == "deferred")

    @property
    def settled_pairs(self) -> list[tuple[str, str]]:
        return [(i.ordering_key, i.source_id) for i in self.items if i.settled]

    @property
    def coverage_known(self) -> bool:
        """False means this run cannot say what it missed. A failed run never claims coverage."""
        return self.status in (RunStatus.COMPLETE, RunStatus.QUIET)

    @property
    def unattended_ready(self) -> bool:
        """One run behaving is necessary and, since CAP14-REV-038, not sufficient.

        A quiet run over a source that lost an item three runs ago is still a quiet run: its own
        counters are clean and say nothing about the hole. So readiness now also requires that the
        DURABLE accounting is intact and that nothing is sitting unreconciled. `None` for
        `unreconciled_dead_letters` means no durable log was configured and the question could not
        be asked, which is not an answer of zero: cannot-say is read as no, as it is everywhere
        else in this lane.
        """
        if not self.durable_accounting:
            return False
        if self.unreconciled_dead_letters is None or self.unreconciled_dead_letters > 0:
            return False
        return self.coverage_known and self.deferred == 0 and self.error_kind is None

    def summary(self) -> dict[str, Any]:
        return {
            "source_ref": self.source_ref,
            "status": self.status.value,
            "captured": self.captured,
            "duplicates": self.duplicates,
            "dead_lettered": self.dead_lettered,
            "deferred": self.deferred,
            "pages_fetched": self.pages_fetched,
            "coverage_known": self.coverage_known,
            "durable_accounting": self.durable_accounting,
            "unreconciled_dead_letters": self.unreconciled_dead_letters,
            "unattended_ready": self.unattended_ready,
            "backlog_remaining": self.backlog_remaining,
            "error_kind": self.error_kind,
            "retry_after_s": self.retry_after_s,
            "epoch_reset": None if self.epoch_reset is None else self.epoch_reset.reason,
            "cursor_after": self.cursor_after,
            "notes": list(self.notes),
        }
