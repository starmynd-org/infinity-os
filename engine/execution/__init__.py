"""R02: the execution coordinator. Leases, fencing, idempotent effects and honest outcomes.

The whole package is three verbs and two questions:

    acquire(work_item_id, holder, seconds)      take the right to act, and get a fencing token
    reserve(key, lease, capability, scope)      say what you are about to do, before you do it
    settle(attempt, outcome, ...)               say what actually happened, exactly once

    live_lease(work_item_id)                    who may act on this item right now
    unresolved()                                what a human still has to look at

`reserve` is where authority and execution meet: it calls `store.authority.check` immediately
before it writes, so a grant revoked one second ago stops the effect that was about to happen.
Migrations 58 and 59 carry the storage half; this module is the caller-facing half, and neither
one is trusted to be the only guard.
"""

from .coordinator import (                                        # noqa: F401
    AlreadyAttempted,
    AmbiguousOutcome,
    LeaseHeld,
    LeaseNotLive,
    LeaseUnavailable,
    acquire,
    live_lease,
    reconcile,
    release,
    reserve,
    settle,
    unresolved,
)
