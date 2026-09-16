"""Subscribers. One of them in v1: `operator-paging`.

Every subscriber here is declared in `departments/SUBSCRIBERS.md` before it runs. A subscriber
that exists in code but not in the declaration is exactly the unenforceable convention EF-6 was
accepted to prevent, so `fabric.listener.Listener` reads the declaration from git at start and
refuses to run without one.
"""
