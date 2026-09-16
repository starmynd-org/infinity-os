"""Deterministic fake providers. No network, no credential, no provider account.

Each one models the SPECIFIC misbehaviour its real counterpart is known for, because a fake that
only does the happy path proves nothing about coverage:

* `FakeImapSource` has a UIDVALIDITY epoch it can change under you, and it can emit a block of
  messages sharing one timestamp to the second -- the two things that break a naive IMAP cursor.
* `FakeSlackSource` pages with a limit it does not always honour, returns pages in arbitrary
  order, and can edit a message in place so its content changes without its id changing.
* `FakeMeetingSource` withholds a transcript that arrives later, which is a revision of an item
  already captured rather than a new meeting.

Every one of them can be told to raise a rate limit, an expired credential or a transient fault at
a chosen call number, so the failure paths are exercised at exact positions instead of by luck.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..pagination import (
    AuthExpired,
    Page,
    PageRequest,
    ProviderItem,
    ProviderUnavailable,
    RateLimited,
)


@dataclass
class FaultPlan:
    """Deterministic faults, keyed on which fetch call they hit. 1 is the first fetch."""

    rate_limit_on: set[int] = field(default_factory=set)
    auth_expired_from: int | None = None
    unavailable_on: set[int] = field(default_factory=set)
    auth_expired_on_epoch: bool = False


class _Base:
    def __init__(self, items: list[ProviderItem], epoch_value: str = "e1",
                 faults: FaultPlan | None = None, honour_limit: bool = True,
                 ignore_after: bool = False):
        self._items = list(items)
        self._epoch = epoch_value
        self.faults = faults or FaultPlan()
        self.calls = 0
        self.honour_limit = honour_limit
        #: Some real APIs cannot be asked for "everything after X" -- they serve a recent window
        #: and nothing else, so consecutive pages OVERLAP. A pager that assumes non-overlapping
        #: pages processes those items twice per run, which costs quota and, for a connector that
        #: also stages hygiene, marks the same item read twice.
        self.ignore_after = ignore_after

    def epoch(self) -> str:
        if self.faults.auth_expired_on_epoch:
            raise AuthExpired("the stored credential is no longer valid")
        return self._epoch

    def set_epoch(self, value: str) -> None:
        """The provider reissues its identifiers. Every id we hold becomes meaningless."""
        self._epoch = value

    def add(self, item: ProviderItem) -> None:
        self._items.append(item)

    def replace(self, source_id: str, item: ProviderItem) -> None:
        """Edit in place: same id, new content. A revision, not a new item."""
        self._items = [item if i.source_id == source_id else i for i in self._items]

    def _check_faults(self) -> None:
        self.calls += 1
        if self.faults.auth_expired_from is not None and self.calls >= self.faults.auth_expired_from:
            raise AuthExpired("the stored credential expired mid-run")
        if self.calls in self.faults.rate_limit_on:
            raise RateLimited(retry_after_s=30.0)
        if self.calls in self.faults.unavailable_on:
            raise ProviderUnavailable("provider returned 503")

    def fetch_page(self, request: PageRequest) -> Page:
        self._check_faults()
        after = (request.after_key, request.after_id or "")
        eligible = sorted(
            (i for i in self._items
             if self.ignore_after
             or request.after_key is None
             or (i.ordering_key, i.source_id) > after),
            key=lambda i: (i.ordering_key, i.source_id),
        )
        limit = request.limit if self.honour_limit else request.limit + 3
        window = eligible[:limit]
        return Page(items=self._shuffle(window), has_more=len(eligible) > len(window))

    def _shuffle(self, items: list[ProviderItem]) -> list[ProviderItem]:
        return items


class FakeImapSource(_Base):
    """UIDVALIDITY-style epoch, and identical timestamps in bulk."""


class FakeSlackSource(_Base):
    """Returns pages in a deliberately unsorted order and over-serves its limit."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("honour_limit", False)
        super().__init__(*args, **kwargs)

    def _shuffle(self, items):
        # Reversed, not random: the test must be reproducible, and reversed is enough to break a
        # pager that trusts the provider's order.
        return list(reversed(items))


class FakeMeetingSource(_Base):
    """A transcript that is missing at first and arrives on a later run."""


# -- populations ----------------------------------------------------------------------------


def email_items(count: int, *, identical_timestamp_block: int = 0) -> list[ProviderItem]:
    """`identical_timestamp_block` items all share one second, which is the trap."""
    items: list[ProviderItem] = []
    for n in range(count):
        if n < identical_timestamp_block:
            key = "2026-09-01T08:00:00Z"
        else:
            key = f"2026-09-01T{8 + n // 60:02d}:{n % 60:02d}:00Z"
        items.append(
            ProviderItem(
                source_id=f"uid-{n:05d}",
                ordering_key=key,
                payload={
                    "container": "INBOX",
                    "subject": f"Message {n:03d}",
                    "from": f"person{n:03d}@example.invalid",
                    "body": f"Body of message {n:03d}.",
                },
            )
        )
    return items


def slack_items(count: int) -> list[ProviderItem]:
    return [
        ProviderItem(
            source_id=f"C0001.17000000{n:02d}",
            ordering_key=f"17000000{n:02d}.0001",
            payload={"container": "C0001", "text": f"Slack message {n:03d}", "user": f"U{n:04d}"},
        )
        for n in range(count)
    ]


def meeting_items(count: int) -> list[ProviderItem]:
    return [
        ProviderItem(
            source_id=f"meeting-{n:04d}",
            ordering_key=f"2026-09-01T1{n}:00:00Z",
            payload={
                "container": "recorded",
                "title": "Weekly" if n % 2 == 0 else f"Client call {n}",
                "transcript": None,
            },
        )
        for n in range(count)
    ]
