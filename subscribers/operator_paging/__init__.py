"""The `operator-paging` subscriber. Effect class, gated posture, phase 1.

Declared in `departments/SUBSCRIBERS.md` before it runs. A subscriber that exists in code but not
in the declaration is exactly the unenforceable convention EF-6 was accepted to prevent.
"""

from .consumer import SUBSCRIBER_ID, OperatorPaging  # noqa: F401

__all__ = ["OperatorPaging", "SUBSCRIBER_ID"]
