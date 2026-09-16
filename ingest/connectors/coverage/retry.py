"""Run budgets and per-item retry, with the two failures that must NOT be retried in a loop.

`RunBudget` bounds one run: items, pages and attempts. A connector without a budget is a
connector that runs until the provider stops it, which is how a polling job earns a rate-limit
ban on a mailbox the operator needs.

`RetryPolicy` bounds one ITEM. An item that fails transiently is deferred (the cursor does not
pass it) and retried on later runs; after `max_attempts` it is DEAD-LETTERED, which is a settled
disposition. That is the mechanism behind the acceptance line "every eligible fixture item
eventually captured or explicitly dead-lettered": there is no third state where an item is
quietly retried for ever while the run reports success.

THE TWO NON-RETRYABLE FAILURES.

* `AuthExpired` ends the run immediately. Retrying a dead credential burns quota and, on several
  providers, locks the account. The cursor is preserved untouched.
* A payload the capture contract refuses is permanent for those bytes. It dead-letters on the
  first attempt rather than consuming `max_attempts` runs to reach the same answer.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RunBudget:
    """What one run is allowed to consume. Deliberately small defaults."""

    max_items: int = 200
    max_pages: int = 20
    #: A backlog larger than this is not a failure; the run ends `partial` and the next run
    #: continues from the advanced cursor. Progress, not completeness, is the per-run promise.
    stop_on_first_deferred: bool = False

    def __post_init__(self) -> None:
        if self.max_items < 1 or self.max_pages < 1:
            raise ValueError("a run budget of zero items or pages can never make progress")


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    base_backoff_s: float = 2.0
    max_backoff_s: float = 300.0

    def backoff_for(self, attempt: int) -> float:
        """Exponential, capped. Returned for the caller to record; nothing here sleeps.

        A connector that sleeps inside a sweep holds a slot and a database connection for the
        duration. The backoff is data: the scheduler decides when the next run happens.
        """
        if attempt < 1:
            raise ValueError("attempt numbering starts at 1")
        return min(self.base_backoff_s * (2 ** (attempt - 1)), self.max_backoff_s)

    def exhausted(self, attempts: int) -> bool:
        return attempts >= self.max_attempts


@dataclass
class AttemptLedger:
    """Per-item attempt counts, carried across runs by the connector's own state.

    Kept separate from the cursor on purpose: the cursor is a position and must stay small and
    cheap to write, while this grows with the number of items currently failing and shrinks back
    to nothing when they settle.
    """

    attempts: dict[str, int]

    def __init__(self, attempts: dict[str, int] | None = None):
        self.attempts = dict(attempts or {})

    def record_failure(self, source_id: str) -> int:
        self.attempts[source_id] = self.attempts.get(source_id, 0) + 1
        return self.attempts[source_id]

    def clear(self, source_id: str) -> None:
        self.attempts.pop(source_id, None)

    def count(self, source_id: str) -> int:
        return self.attempts.get(source_id, 0)

    def pending(self) -> dict[str, int]:
        return dict(self.attempts)
