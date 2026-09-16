"""Source cursors that survive the things providers actually do.

A cursor is three parts, and dropping any one of them is a known way to lose mail:

* `epoch` -- the provider's own validity token. IMAP calls it UIDVALIDITY; Slack has no equivalent
  and gets a constant; tl;dv reissues IDs on re-processing. When the epoch changes, EVERY id the
  cursor holds is meaningless, and continuing from the old high-water mark silently skips the
  entire mailbox. So an epoch change forces a reset, and the reset is REPORTED, never quiet.
* `high_water` -- the ordering key of the newest item known captured.
* `tiebreak` -- the source id at that same ordering key.

WHY THE TIEBREAK EXISTS. Two emails can share a timestamp to the second, and providers do this
constantly for bulk sends. With `occurred_at` alone, `> high_water` skips the second one forever
and `>= high_water` re-fetches the first one forever. The pair `(occurred_at, source_id)` is a
total order, so neither happens. This is the single most common coverage bug in a polling
connector and it is not detectable without a fixture that plants identical timestamps.

A CURSOR IS NEVER ADVANCED PAST AN ITEM THAT WAS NOT CAPTURED OR DEAD-LETTERED. `advance` takes
the dispositions, not the page, so an item that failed transiently holds the frontier and is
retried on the next run instead of being skipped forever.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Any, Iterable


class CursorError(RuntimeError):
    """The stored cursor cannot be trusted. Reset and report; never guess a position."""


@dataclass(frozen=True)
class EpochReset:
    """Recorded when the provider invalidated its own ids."""

    old_epoch: str
    new_epoch: str
    reason: str


@dataclass(frozen=True)
class Cursor:
    source_ref: str
    epoch: str
    high_water: str | None = None
    tiebreak: str | None = None
    #: Items seen at exactly `high_water` that are already done. Bounded by construction: it is
    #: cleared the moment the high-water mark moves, so it holds one timestamp's worth at most.
    settled_at_high_water: tuple[str, ...] = ()

    # -- serialisation ---------------------------------------------------------------------

    def dumps(self) -> str:
        return json.dumps(
            {
                "v": 1,
                "source_ref": self.source_ref,
                "epoch": self.epoch,
                "high_water": self.high_water,
                "tiebreak": self.tiebreak,
                "settled": list(self.settled_at_high_water),
            },
            sort_keys=True,
        )

    @classmethod
    def loads(cls, blob: str, *, source_ref: str) -> "Cursor":
        """Parse a stored cursor, refusing anything it cannot fully understand.

        A semantically corrupt cursor is not repaired in place. Repairing it means inventing a
        position, and an invented position is indistinguishable from a skipped backlog.
        """
        try:
            data = json.loads(blob)
        except (TypeError, ValueError) as exc:
            raise CursorError(f"cursor for {source_ref} is not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise CursorError(f"cursor for {source_ref} is not an object")
        if data.get("v") != 1:
            raise CursorError(f"cursor for {source_ref} has unknown version {data.get('v')!r}")
        if data.get("source_ref") != source_ref:
            raise CursorError(
                f"cursor belongs to {data.get('source_ref')!r}, not {source_ref!r}; "
                "refusing to use another source's position"
            )
        epoch = data.get("epoch")
        if not isinstance(epoch, str) or not epoch:
            raise CursorError(f"cursor for {source_ref} has no epoch")
        hw, tb = data.get("high_water"), data.get("tiebreak")
        if (hw is None) != (tb is None):
            raise CursorError(
                f"cursor for {source_ref} has half a position "
                f"(high_water={hw!r}, tiebreak={tb!r}); it cannot order the next page"
            )
        settled = data.get("settled") or []
        if not isinstance(settled, list) or not all(isinstance(s, str) for s in settled):
            raise CursorError(f"cursor for {source_ref} has a malformed settled list")
        return cls(source_ref, epoch, hw, tb, tuple(settled))

    @classmethod
    def initial(cls, source_ref: str, epoch: str) -> "Cursor":
        return cls(source_ref=source_ref, epoch=epoch)

    # -- behaviour -------------------------------------------------------------------------

    def check_epoch(self, provider_epoch: str) -> tuple["Cursor", EpochReset | None]:
        """Return the cursor to use, plus a reset record if the provider invalidated its ids."""
        if provider_epoch == self.epoch:
            return self, None
        return (
            Cursor.initial(self.source_ref, provider_epoch),
            EpochReset(
                old_epoch=self.epoch,
                new_epoch=provider_epoch,
                reason="provider epoch changed; every stored id is meaningless and the window "
                "is rescanned from the beginning",
            ),
        )

    def is_after(self, ordering_key: str, source_id: str) -> bool:
        """Is this item strictly newer than the cursor, in the total order?"""
        if self.high_water is None:
            return True
        if ordering_key != self.high_water:
            return ordering_key > self.high_water
        if source_id in self.settled_at_high_water:
            return False
        return source_id > (self.tiebreak or "")

    def advance(self, settled: Iterable[tuple[str, str]]) -> "Cursor":
        """Move to the newest SETTLED item. `settled` is (ordering_key, source_id) pairs.

        Only captured-or-dead-lettered items may be passed here. An item still owed a retry is
        not settled and must not move the frontier.
        """
        pairs = sorted(settled)
        if not pairs:
            return self
        top_key, top_id = pairs[-1]
        if self.high_water is not None and (top_key, top_id) < (self.high_water, self.tiebreak or ""):
            return self
        same = tuple(sorted({sid for key, sid in pairs if key == top_key}))
        if top_key == self.high_water:
            same = tuple(sorted(set(same) | set(self.settled_at_high_water)))
        return replace(self, high_water=top_key, tiebreak=top_id, settled_at_high_water=same)

    def describe(self) -> dict[str, Any]:
        return {
            "source_ref": self.source_ref,
            "epoch": self.epoch,
            "position": None if self.high_water is None else f"{self.high_water}/{self.tiebreak}",
            "settled_at_position": len(self.settled_at_high_water),
        }
