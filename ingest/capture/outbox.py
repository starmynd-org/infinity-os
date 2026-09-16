"""Source-hygiene outbox: the gate between "captured" and "allowed to touch the source".

This is where invariant 1 becomes executable. An intent to mark an email read, add an eyes
reaction or flag a recording imported is only ever CLAIMABLE because it was committed in the same
transaction as its journal row. `authorise` re-checks that independently, so even a caller that
fabricates an intent id cannot get past it.

WHAT THIS MODULE DELIBERATELY DOES NOT DO. It does not talk to a provider. Executing an intent is
n8n's job in the target architecture and a later operational phase here; I02's non-goals forbid
provider writes and this packet honours that. `drain` takes an executor callable so the ORDERING
can be tested without any provider existing, and `NullExecutor` is what the tests use.

SOURCE STATE IS NOT WORK STATE. A completed intent means "safely captured", never "a human read
it", "the request was accepted" or "the work is done". Nothing here writes work state, and the
vocabulary is kept separate on purpose.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterator

from .contracts import CaptureReceipt


class OutboxAuthorisationError(RuntimeError):
    """An intent was claimed for a capture that is not durably journalled. Never proceed."""


@dataclass(frozen=True)
class HygieneIntent:
    intent_id: int
    journal_seq: int
    capture_id: str
    action: str
    target: str


class NullExecutor:
    """Records what WOULD be done at the source. Touches nothing."""

    def __init__(self) -> None:
        self.performed: list[HygieneIntent] = []

    def __call__(self, intent: HygieneIntent) -> str:
        self.performed.append(intent)
        return "recorded-not-executed"


class SourceHygieneOutbox:
    def __init__(self, store: Any):
        self.store = store

    def authorise(self, capture_id: str) -> None:
        """The independent check. A row must exist; a receipt object is not sufficient evidence."""
        if self.store.get(capture_id) is None:
            raise OutboxAuthorisationError(
                f"refusing source hygiene for {capture_id}: no committed journal row"
            )

    def authorise_receipt(self, receipt: CaptureReceipt) -> None:
        if not receipt.authorises_source_cleanup:
            raise OutboxAuthorisationError(
                f"receipt {receipt.receipt_id} does not authorise source cleanup"
            )
        self.authorise(receipt.capture_id)

    def claimable(self) -> Iterator[HygieneIntent]:
        for row in self.store.claimable_intents():
            yield HygieneIntent(
                intent_id=row["intent_id"],
                journal_seq=row["journal_seq"],
                capture_id=row["capture_id"],
                action=row["action"],
                target=row["target"],
            )

    def drain(self, executor: Callable[[HygieneIntent], str] | None = None) -> list[HygieneIntent]:
        """Authorise, execute and retire every claimable intent. Returns what was retired."""
        executor = executor or NullExecutor()
        done: list[HygieneIntent] = []
        for intent in list(self.claimable()):
            self.authorise(intent.capture_id)
            result = executor(intent)
            self.store.mark_intent_done(intent.intent_id, result)
            done.append(intent)
        return done
