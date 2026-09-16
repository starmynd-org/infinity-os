"""Connector coverage: make "did we get everything?" an answerable question.

WHY THIS IS A LIBRARY AND NOT AN EDIT TO THE EXISTING CONNECTORS. `email_imap.py`, `slack.py` and
`tldv.py` are working connectors and two of them carry another lane's uncommitted work at the
baseline this was written against. So the pagination, cursor, outcome and retry logic each of them
would otherwise reinvent lives here, gets its own tests against deterministic fake providers, and
is adopted by the existing connectors through a reviewed patch rather than an edit made behind
their author's back.

WHAT COVERAGE MEANS HERE, precisely: for a bounded, authorised source window, every eligible item
is eventually either durably captured or explicitly dead-lettered, the oldest backlog makes
progress on every run, and a run that found nothing is distinguishable from a run that failed.
That last one is the difference between "quiet inbox" and "broken connector", and a system that
cannot tell them apart reports health it does not have.

WHAT IS OUT OF SCOPE, per I02's non-goals: no provider reads, no mailbox labels, no installed
timers, and no claim of unlimited history. Every provider in `fixtures/` is a local fake.
"""

from .cursors import Cursor, CursorError, EpochReset
from .outcomes import RunOutcome, RunStatus
from .pagination import FairPager, PageRequest
from .retry import RetryPolicy, RunBudget
from .manifests import SourceManifest

__all__ = [
    "Cursor",
    "CursorError",
    "EpochReset",
    "FairPager",
    "PageRequest",
    "RetryPolicy",
    "RunBudget",
    "RunOutcome",
    "RunStatus",
    "SourceManifest",
]
