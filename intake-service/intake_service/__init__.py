"""The intake door package. This file exists to put the sibling lanes' packages on the path.

Copied in shape from `web/__init__.py`, and for the same reason it gives there: a package's
`__init__` runs before any module inside it, whoever imports it and in whatever order, which is
the property a path bootstrap needs. `app.py` imports `store` and `swarm_engine` at its own top,
so a bootstrap that lived in `app.py` would run too late.

**THE SPELLING IS LOAD-BEARING RATHER THAN STYLISTIC.** `engine/bin/swarm` puts `engine/` on the
path and imports `swarm_engine`. Spelling it `engine.swarm_engine` here instead would create a
SECOND module object, and importing both raises `DuplicateTransition: the transition 'intake' is
already registered` (measured by the web lane, not feared). So: `engine/` goes on the path and
the package is `swarm_engine`.

`queue/` is deliberately NOT added. This door calls one verb, `intake`, which lives in
`swarm_engine.transitions`; `human_queue` registers verbs this service never calls, and every
import is one more module that can fail at boot on a door whose whole job is to be up.
"""

from __future__ import annotations

import os
import sys

#: `intake-service/` is a directory beside `web/` and `engine/`, not inside them, so the repo root
#: is two levels up from this file.
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

for _p in (_REPO, os.path.join(_REPO, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
