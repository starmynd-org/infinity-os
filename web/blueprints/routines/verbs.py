"""The verbs this surface will need, declared as a proposal rather than implemented as writes.

R03. `web/rooms.py` is the console's allowlist and `store.apply` is its one implementation, so
this file deliberately contains **no state change at all**. Two facts from reading `rooms.py`
before writing anything:

  1. A verb is a transition registered in the store (`store.registered()`), owned by the lane that
     built it. `dispatch` refuses a verb the store has never registered, and its own comment says
     why: *"a second implementation here is exactly the drift the narrow waist exists to prevent."*
     So this lane does not write a transition for `pause`; R01 and R02 do.
  2. `ROOM_VERBS` decides which room may call which verb. Adding a `routines` key to that dict is
     an edit to L01's file, and it is a security decision: the allowlist is the thing that stops a
     POST from one page borrowing another room's verbs, which is a hole this console has already
     had once and closed.

What this file is, then: the exact proposal, as data, so the allowlist entry can be reviewed and
tested before it exists, and so the surface and the reviewer disagree in a test rather than in
production. `PROPOSED_ROOM_VERBS` is what CAP07 asks `ROOM_VERBS["routines"]` to become, and
`EFFECT_GUARDS` is what each verb must additionally check on the row itself.

Until an entry exists in the real table, `rooms.assert_room_can_act("routines")` refuses the room
outright, which is the correct behaviour and the reason the surface ships read-only first.
"""

from __future__ import annotations

from typing import Dict, FrozenSet

# What the five controls on the routine surface would call. Verb names are proposals: the lane
# that registers the transition owns the final spelling, and this file is where the mismatch
# becomes visible instead of becoming a 500 on a button.
PROPOSED_ROOM_VERBS: Dict[str, FrozenSet[str]] = {
    "routines": frozenset({
        "routine activate",    # turn a drafted routine on
        "routine pause",       # stop future runs; an in-flight run finishes
        "routine resume",      # the inverse of pause, and it is a real inverse
        "routine run now",     # one immediate run, refused while paused
        "routine commit",      # apply a proposal, producing the next version
    }),
}

# The per-row conditions, beyond the allowlist. `rooms.assert_allowed_on` is where these belong,
# and its own lesson is that a guard must read the ROW rather than the rendered card: a guard that
# trusts the rendering layer is one template edit away from being false.
EFFECT_GUARDS: Dict[str, str] = {
    "routine activate": (
        "refuse unless the standing grant on THIS version has an accepted approval record. A "
        "routine that reads a mailbox must not become live because a page was rendered."
    ),
    "routine pause": (
        "always permitted for the owner. Pausing removes an effect and can never need an "
        "approval: requiring consent to take permission away teaches users to click through "
        "consent screens."
    ),
    "routine resume": (
        "refuse if the standing grant accepted at pause time is no longer the current version's. "
        "Resuming is not a neutral act if the routine changed while it was off."
    ),
    "routine run now": (
        "refuse while paused, and refuse when the host is unreachable, with the two reasons "
        "reported separately. `Routine.may_run_now` already returns exactly that pair."
    ),
    "routine commit": (
        "refuse when the proposal's base version is not the current version (`ConcurrentEdit`), "
        "and refuse when the proposal widens the grant and no approval accompanies it."
    ),
}

# Verbs whose effect can leave the user's workspace. None of the five do: the surface changes a
# routine, and the routine's own outbound effects are gated by its grant at run time, not here.
# The set is declared empty ON PURPOSE and asserted in the tests, so that a later verb that does
# leave has to change this line and face the reviewer that guards it.
LEAVES_WORKSPACE: FrozenSet[str] = frozenset()


def proposed_entry() -> str:
    """The literal line for `ROOM_VERBS`, so the patch is copy-paste and not transcription."""
    verbs = ", ".join(repr(v) for v in sorted(PROPOSED_ROOM_VERBS["routines"]))
    return '    "routines": frozenset({%s}),' % verbs
