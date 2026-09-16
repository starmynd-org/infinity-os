"""Fair, oldest-first pagination over a source port.

FAIR MEANS THE OLDEST BACKLOG MOVES. A connector that pages newest-first and stops at a run limit
never reaches the bottom of a large mailbox: every run re-reads the same recent page and the old
mail is never captured, while the run reports success each time. So this pager walks the
authorised window from the cursor FORWARDS, and the acceptance property "oldest backlog
progresses" falls out of the ordering rather than out of a special case.

THE ORDER IS TOTAL. Items are ordered by `(ordering_key, source_id)`, never by `ordering_key`
alone, for the reason spelled out in `cursors.py`: providers emit identical timestamps in bulk and
a partial order either skips or loops.

THE PAGER DOES NOT TRUST THE PROVIDER'S ORDER. It sorts every page itself. Providers reorder under
concurrent edits, and a pager that assumes sorted input turns that into a silently skipped item.

WHAT IT DOES NOT DO. It never fetches beyond the budget, and it never decides an item is
uninteresting -- filtering is the source manifest's job, and dropping is nobody's.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator, Protocol, Sequence


class ProviderError(RuntimeError):
    """Base for a provider-side failure. Coverage becomes UNKNOWN, never zero."""


class RateLimited(ProviderError):
    def __init__(self, retry_after_s: float = 60.0):
        self.retry_after_s = retry_after_s
        super().__init__(f"provider rate limited; retry after {retry_after_s}s")


class AuthExpired(ProviderError):
    """The credential is dead. Retrying costs quota and can get an account locked; stop the run."""


class ProviderUnavailable(ProviderError):
    """Transient provider fault. The window is retried unchanged."""


@dataclass(frozen=True)
class ProviderItem:
    """One item as the provider presents it. Pure data; no bytes are fetched here."""

    source_id: str
    ordering_key: str
    payload: dict[str, Any]
    deleted: bool = False
    attachments: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class Page:
    items: Sequence[ProviderItem]
    has_more: bool


@dataclass(frozen=True)
class PageRequest:
    after_key: str | None
    after_id: str | None
    limit: int


class SourcePort(Protocol):
    """The two calls a pollable source must answer. Fixtures and real connectors share it."""

    def epoch(self) -> str: ...

    def fetch_page(self, request: PageRequest) -> Page: ...


@dataclass
class FairPager:
    port: SourcePort
    page_size: int = 25

    def walk(self, cursor, max_items: int, max_pages: int) -> Iterator[tuple[ProviderItem, int]]:
        """Yield `(item, page_number)` in total order, oldest first, within the budget.

        Stops on budget; a caller that wants to know whether more remains asks `has_more` via the
        last page, which `sweep` records as `backlog_remaining is not None`.
        """
        after_key, after_id = cursor.high_water, cursor.tiebreak
        yielded = 0
        # The requested window. It GROWS, and only grows, when the provider hands back items we
        # have already passed -- the signature of an API that cannot serve "everything after X"
        # and only offers a recent slice. Against such a provider a fixed window stalls after one
        # page and the backlog never drains, while every run still reports success. Widening
        # turns it into an offset pager, which is slower but correct and still bounded by
        # `max_pages`.
        window = self.page_size
        for page_no in range(1, max_pages + 1):
            remaining = max_items - yielded
            if remaining <= 0:
                return
            page = self.port.fetch_page(
                PageRequest(after_key, after_id, max(min(self.page_size, remaining), window))
            )
            # TWO filters, and both are needed. The cursor decides what previous RUNS settled;
            # the in-run frontier decides what THIS run already yielded. Without the second, a
            # provider that returns overlapping pages -- which is normal, and is what happens
            # when an item is edited mid-walk -- yields the same item twice in one run.
            fresh = sorted(
                (
                    i
                    for i in page.items
                    if cursor.is_after(i.ordering_key, i.source_id)
                    and (
                        after_key is None
                        or (i.ordering_key, i.source_id) > (after_key, after_id or "")
                    )
                ),
                key=lambda i: (i.ordering_key, i.source_id),
            )
            if len(fresh) < len(page.items):
                # The provider re-served items we had already passed. Skip past everything this
                # run has yielded, plus one fresh page, on the next request.
                window = yielded + len(fresh) + self.page_size

            if not fresh:
                # No progress. Only worth another request if the provider says more exists AND
                # the window actually widened -- otherwise the next call returns this same page
                # and the loop spins until `max_pages` for nothing.
                if page.has_more and window > len(page.items):
                    continue
                return

            for item in fresh:
                yield item, page_no
                yielded += 1
                after_key, after_id = item.ordering_key, item.source_id
                if yielded >= max_items:
                    return
            if not page.has_more:
                return
