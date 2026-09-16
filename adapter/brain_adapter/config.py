"""Where the brain is and where the adapter keeps its cache.

The brain path is config, never a constant compiled into a call site: the same adapter
serves the individual, department, and company tiers of the topology.
"""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_BRAIN = "/mnt/c/Users/you/repos/your-brain"

ENV_BRAIN = "BRAIN_ROOT"
ENV_CACHE = "BRAIN_ADAPTER_CACHE"


def brain_root(explicit: str | None = None) -> Path:
    root = Path(explicit or os.environ.get(ENV_BRAIN) or DEFAULT_BRAIN).expanduser()
    return root.resolve()


def cache_dir() -> Path:
    return Path(os.environ.get(ENV_CACHE) or (Path.home() / ".cache" / "brain-adapter"))
