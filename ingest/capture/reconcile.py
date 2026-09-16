"""Reconciling a dead letter: the way back from a loss, and the one act this lane cannot do itself.

I02-RECONCILE-01 (CAP14-REV-041). CAP14 drove the gap rather than arguing it: one permanently
refused payload, then three completely clean runs, and `unattended_ready` stayed `[False, False,
False]`. The `DeadLetterLog` protocol had `record` and `unreconciled` and nothing else, and
`SqliteJournalStore` had `dead_letter()` and `dead_letters()` and nothing that resolved one.
Nothing COULD reconcile.

That matters more than it first sounds, because a permanently refused payload is a NORMAL event by
this lane's own `retry.py`: a payload the capture contract refuses is permanent for those bytes and
dead-letters on the first attempt. So one malformed message ended unattended operation for that
source for ever. The readiness gate CAP14 asked for in REV-038 was right, and a correct gate with
no way back is a wall.

THE RULE THIS MODULE EXISTS TO HOLD: **a reconciliation names a resolver, and this lane is never
the resolver.** Nothing in `connectors/` or `capture/` calls `reconcile`, and no sweep, retry path
or readiness check reaches it. A system that could clear its own losses would report unattended
readiness by deciding it was fine, which is worse than the wall it replaced. The same reasoning the
repo already applies to acceptance applies here: `done` means an agent reported completion, and
acceptance is a human act.

WHAT IT IS NOT. It is not a repair: reconciling does not recapture the item, and `accepted-loss`
is one of the resolutions because the honest outcome is sometimes that the item is gone. It is not
an edit either. The dead letter row is never touched, and a reconciliation is an append BESIDE it.

MANY FINDINGS PER LOSS, AND THE NEWEST DECIDES. Terminal 04 drove the defect the first shape had:
one reconciliation row per loss meant a human who recorded `still-unknown` could never record
`recaptured` when the item actually arrived, so the honest answer blocked readiness for ever. That
is the exact pressure toward `accepted-loss` this module exists to remove, rebuilt by accident.
Every earlier finding stays readable underneath.
"""

from __future__ import annotations

from typing import Any, Protocol

#: The four honest answers about a dead letter, and only three of them close it. There is
#: deliberately no "ignore": an item being ignored is an unreconciled loss and should keep reading
#: as one, which is what `still-unknown` says out loud.
RESOLUTIONS: tuple[str, ...] = (
    #: The item was captured by another route and the loss no longer stands.
    "recaptured",
    #: The item is gone and a human has decided to accept that. The honest ending, not a failure
    #: to find a better one.
    "accepted-loss",
    #: There was never an item here: a malformed probe, a test artefact, a provider echo.
    "not-an-item",
    #: A human looked and STILL DOES NOT KNOW. Recorded, and it CLOSES NOTHING.
    #:
    #: CAP14 ruled this in and named the condition: a vocabulary offering only closing answers
    #: forces an honest reader who does not know into one of them, and the cheapest is
    #: `accepted-loss`. A vocabulary with no word for the true state manufactures a false one, and
    #: it manufactures the one that shuts the hole. The condition is that this must leave the item
    #: counted as unreconciled: if it cleared the count it would be a silent close wearing an
    #: honest label, which is strictly worse than `accepted-loss`, because `accepted-loss` at
    #: least says what it did.
    "still-unknown",
)

#: The resolutions that actually close a loss. `still-unknown` is deliberately not among them.
CLOSING_RESOLUTIONS: tuple[str, ...] = ("recaptured", "accepted-loss", "not-an-item")


class ReconcilableLog(Protocol):
    """The two verbs a reconciliation needs. Narrower than `JournalStore` on purpose."""

    def reconcile_dead_letter(self, entry: dict[str, Any]) -> bool: ...

    def unreconciled_dead_letters(self, source_key: str | None = None) -> int: ...


def reconcile(
    store: ReconcilableLog,
    dead_letter_id: str,
    *,
    resolution: str,
    resolved_by: str,
    resolved_at: str,
    note: str = "",
) -> bool:
    """Record what a named human found about one dead letter. Appends; never edits.

    Every refusal below is named, and each one exists because the unnamed version of it would let
    readiness be regained by an act that decided nothing.
    """
    if resolution not in RESOLUTIONS:
        raise ValueError(
            "unknown resolution " + repr(resolution) + "; one of " + ", ".join(RESOLUTIONS)
        )
    if not resolved_by or not resolved_by.strip():
        raise ValueError(
            "a reconciliation must name who resolved it; an unattributed resolution is a loss "
            "cleared by nobody"
        )
    if not resolved_at or not resolved_at.strip():
        raise ValueError("a reconciliation must say when it happened")
    if resolution == "still-unknown" and not note.strip():
        raise ValueError(
            "recording that you looked and do not know must say what you looked at; otherwise it "
            "is indistinguishable from not having looked"
        )
    if resolution == "accepted-loss" and not note.strip():
        raise ValueError(
            "accepting a loss must say why; the other two resolutions describe themselves and "
            "this one is the only place a human decides an item will never arrive"
        )
    return store.reconcile_dead_letter(
        {
            "dead_letter_id": dead_letter_id,
            "resolution": resolution,
            "resolved_by": resolved_by.strip(),
            "resolved_at": resolved_at,
            "note": note,
        }
    )
