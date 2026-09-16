"""Put the spine and the three lanes it composes on the path.

Derived from `__file__` rather than written as an absolute path: this suite runs from WSL against
a Windows-created worktree, and a hardcoded drive letter is invisible from one of the two.
"""

from __future__ import annotations

import pathlib
import sys

SPINE = pathlib.Path(__file__).resolve().parents[1]
ROOT = SPINE.parents[1]
for p in (SPINE, ROOT / "ingest", ROOT / "attention", ROOT / "intake-service"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
