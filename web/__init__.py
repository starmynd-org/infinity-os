"""The console package. This file exists to put the sibling lanes' packages on the path.

It lives here rather than in `app.py` because `app.py` imports `web.model` at its own top, and
`web/model.py` imports D6b's `human_queue` at ITS top: a bootstrap that ran inside `app.py` would
run after both of those and the import would fail. A package's `__init__` runs before any module
inside it, whoever imports it and in whatever order, which is the property this needs.

**The spelling is load-bearing rather than stylistic.** `engine/bin/swarm` puts `engine/` on the
path and imports `swarm_engine`; D6b's `queue/bin/queue` does the same with `queue/` and
`human_queue`. Spelling either one `engine.swarm_engine` or `queue.human_queue` instead creates a
SECOND module object, and importing both raises `DuplicateTransition: the transition 'post' is
already registered` -- measured, not feared. `queue` is also stdlib, so `queue.human_queue` is a
name collision as well as a duplicate registration.
"""

from __future__ import annotations

import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_REPO, os.path.join(_REPO, "engine"), os.path.join(_REPO, "queue")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
