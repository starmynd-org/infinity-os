"""The brain adapter: entity resolution, brief rendering, receipt booking.

Three verbs, not three functions: `entity resolve`, `receipt book`, `touch add`. Every
surface that needs lineage or a promotion calls those. The adapter does not commit to git
from more than one place -- `promotion.promote()` is the single transition and the single
audit point.

The runtime never owns knowledge. Canon is read freely; git is written only through a
visible, typed promotion event.
"""

__all__ = ["brief", "config", "frontmatter", "index", "promotion", "receipt", "tokens"]
