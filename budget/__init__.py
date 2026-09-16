"""Budget enforcement: the spend brake.

Three things live here and nothing else does:

    budget.transitions   the five state changes, registered as verbs on the narrow waist
    budget.enforcer      the gate that refuses a dispatch, and the guard that kills a live run
    budget.halt          the classifier that tells a budget stop apart from a rate-limit refusal

`budget status` is a read and is therefore not a transition. State changes are
`budget set`, `budget charge`, `budget stop`, `budget resume`, `budget note` and
`budget outcome`, each registered exactly once and reachable only through `store.apply`.

The property the whole lane exists to hold, stated once:

    A budget stop is the operator's money and means STOP.
    A subscription refusal is a window that reopens on its own and means WAIT,
    with the task going back UNSPENT.

Conflating them is expensive in both directions. A refusal treated as a breach strands a healthy
fleet (2026-08-14: 18 blocked tasks, 11 idle hours). A breach treated as a refusal retries into
real money, because `reopen` resets the attempt counter and the next terminal picks the task
straight back up. See `budget/README.md` for how the distinction is made structural rather than
asserted.
"""

from . import transitions as _transitions  # noqa: F401  (import registers the verbs)

__all__ = ["enforcer", "halt", "reads", "transitions"]
