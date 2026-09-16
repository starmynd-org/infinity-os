"""R03: routines, the decision layer under the simple front door.

Two modules, both pure:

  * `schedule` answers when a routine runs, from tzdata, including the two days a year a wall
    clock is not a fact about an instant.
  * `versions` holds one authoritative routine, its standing grant, and the rule that an approval
    belongs to the version it was prepared under.

Neither reads a store, opens a socket or holds a clock. Persistence, dispatch and receipts are
R01/R02; the web blueprint under `web/blueprints/routines/` is one front end onto these calls, and
the conversational path is the other. Both land here, which is what makes "the chat and the
controls edit the same thing" true rather than aspirational.
"""

from .schedule import (EVERY_DAY, WEEKDAYS, Occurrence, Schedule, missed_since,
                       next_occurrence, occurrences, resolve)
from .versions import (ACTIVE, DRAFT, PAUSED, ConcurrentEdit, Effect, Grant, Proposal, Routine, Source,
                       commit, pause, propose, resume)

__all__ = [
    "ACTIVE", "DRAFT", "PAUSED", "EVERY_DAY", "WEEKDAYS",
    "ConcurrentEdit", "Effect", "Grant", "Occurrence", "Proposal", "Routine", "Schedule", "Source",
    "commit", "missed_since", "next_occurrence", "occurrences", "pause", "propose", "resolve",
    "resume",
]
