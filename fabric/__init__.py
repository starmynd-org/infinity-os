"""The event fabric.

    "A row in an events table is the record; a notification carrying only the event id is the
    doorbell; the listener reads the row and acts."

The problem it solves, in the buildout project's own words: today a producer that wants to trigger
an n8n flow must know that flow's webhook URL, and a failure at 02:00 waits for the 07:00 rollup
if it surfaces at all.

**Producers never know their consumers.** The moment a producer holds a webhook URL, the fabric
has failed at its only job. Subscribers declare interest in `departments/SUBSCRIBERS.md` under
`_system/subscriber-registry-rules.md`; producers call `event emit` and are done.

Import this package to register its verbs. `store.transitions` refuses a second definition of any
of them at import time, so there is exactly one `event emit` in the process no matter how many
surfaces call it.
"""

from . import types                     # noqa: F401  the event type registry
from . import emit                      # noqa: F401  registers the verbs
from . import lag                       # noqa: F401  the health signal
from .emit import (                     # noqa: F401
    CHANNEL, ack, dispose, emit as emit_event, inherit, observe, quarantine, release, summarise,
)
from .types import all_types, matches_any, matches_prefix, resolve

__all__ = [
    "CHANNEL", "ack", "all_types", "dispose", "emit", "emit_event", "inherit", "lag",
    "matches_any", "matches_prefix", "observe", "quarantine", "release", "resolve",
    "summarise", "types",
]
