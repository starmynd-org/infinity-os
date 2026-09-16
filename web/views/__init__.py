"""R04: the view layer for attention, the queue and the explorer.

`web/model.py` reads the store. This package turns what it read into what a screen may say, and
the whole reason it is a separate layer with its own tests is that three of those rules are
easy to break by accident in a template:

  1. **An unmeasured figure is not a figure.** `Impact.unknown()` has no value field to fill in,
     so a template cannot render a placeholder integer where a measurement was expected. The
     absence carries its own reason and the reason is shown.
  2. **Freshness has three states, not two.** Fresh, stale (read at a known time, older than the
     source's interval) and never-read are different facts. Collapsing stale into absent hides a
     real value; collapsing absent into stale invents a read that did not happen.
  3. **Sorting is not prioritising.** `QueueView.sorted_by()` keeps `rank` on every row and sets
     `reordered`, so the surface states that it is showing a different order rather than
     implying the system will now work in it.

Nothing here reads a store, and nothing here writes. Binding to real rows waits on R01; the
shapes below are what that binding must produce.
"""

from .honesty import (ABSENT, COMPLETE, FRESH, PARTIAL, STALE, UNCERTAIN, Completeness,
                      Freshness, Impact, display_zone, set_display_zone)
from .queue import QueueItem, QueueView, Provenance, ProvenanceStep, TIERS
from .signals import Signals

__all__ = [
    "ABSENT", "COMPLETE", "FRESH", "PARTIAL", "STALE", "UNCERTAIN", "Completeness",
    "Freshness", "Impact", "Provenance", "ProvenanceStep", "QueueItem", "QueueView", "TIERS",
    "Signals", "display_zone", "set_display_zone",
]
