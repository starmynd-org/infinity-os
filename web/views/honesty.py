"""The two components that decide what a screen is allowed to claim.

R04. `web/model.py` already carries this rule for the console's own figures: a number on the
surface is a number the store held, or it is not on the surface. These types carry the same rule
into the attention views, where the pressure is worse, because the design asks for a euro figure
next to every item and the store holds one for almost none of them.

The design of `Impact` is the argument. There is no `Impact(value=None, fallback=0)` and no
`Impact.unknown(display="-")`: an unmeasured impact is a different constructor with no value
field at all, so a template that wants to print something has nothing to print but the reason. A
placeholder here is indistinguishable from a measurement to the person reading it, and the whole
pitch of this console is that the reader can tell.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

FRESH = "fresh"
STALE = "stale"
ABSENT = "absent"

# C01 `Receipt.completeness`, kept as the contract spells it.
COMPLETE = "complete"
PARTIAL = "partial"
UNCERTAIN = "uncertain"

# THE ZONE A STAMP IS PRINTED IN, set once by the app and read by every render below.
#
# Measured 2026-09-06 with the real templates in a browser: the routines surface printed
# "Mon 07 Sep 2026, 08:30 EEST" and the attention surface, one click away, printed
# "Sat 06 Sep 2026, 14:55 UTC" for a read that happened at 17:55 local. Both were true and the
# pair was unreadable. Stored instants stay UTC; only the printing moves.
#
# It is module state rather than an argument on `render()` because a template that must remember
# to pass the zone is a template that will forget on the one screen nobody re-reads.
_DISPLAY_ZONE = timezone.utc


def set_display_zone(name: str) -> None:
    """Set the zone every freshness stamp is printed in. Called once, at app startup."""
    global _DISPLAY_ZONE
    _DISPLAY_ZONE = ZoneInfo(name)


def display_zone():
    return _DISPLAY_ZONE


@dataclass(frozen=True)
class Impact:
    """What an item is worth to the person reading it, or an explicit statement that nobody knows.

    Build one of three ways and never by hand:
        Impact.measured("12 min", "median over 9 samples of this reply kind")
        Impact.unknown("no value is recorded on this account")
        Impact.none_claimed()
    """

    kind: str                      # "measured" | "estimated" | "unknown" | "none"
    value: Optional[str] = None
    why: str = ""

    @classmethod
    def measured(cls, value: str, why: str) -> "Impact":
        if not value:
            raise ValueError("a measured impact needs its value; use Impact.unknown() instead")
        if not why:
            raise ValueError(
                "a measured impact must say what measured it: a figure with no provenance is the "
                "thing this class exists to prevent"
            )
        return cls(kind="measured", value=value, why=why)

    @classmethod
    def unknown(cls, why: str) -> "Impact":
        if not why:
            raise ValueError("an unknown impact must say why it is unknown")
        return cls(kind="unknown", value=None, why=why)

    @classmethod
    def estimated(cls, value: str, why: str) -> "Impact":
        """A figure somebody guessed well. NOT a measurement, and never rendered as one.

        Added when the contract's `Unknownable` turned out to carry three statuses, unknown /
        estimated / measured, while this class had two. Without this branch an estimated cost
        arriving from a work packet would have taken the `measured` path and been printed as a
        fact, which is the exact substitution the class exists to prevent.

        No version or digest is named here on purpose. The pinned contract family is asserted by
        `web/tests/test_contract_reconciliation.py`, which fails when the draft moves; a version
        written into a comment drifts silently, because nothing asserts a comment. The convention
        is Terminal 07's, adopted after an audit of my own edits found this line.
        """
        if not value:
            raise ValueError("an estimated impact needs its value; use Impact.unknown() instead")
        if not why:
            raise ValueError("an estimated impact must say what the estimate is based on")
        return cls(kind="estimated", value=value, why=why)

    @classmethod
    def none_claimed(cls) -> "Impact":
        return cls(kind="none", value=None, why="no business effect is claimed for this item")

    @classmethod
    def from_contract(cls, unknownable: dict) -> "Impact":
        """Build from a C01 `Unknownable`: {status, value?, unit?, basis?}.

        The contract permits a bare `{"status": "unknown"}` with no basis, and this surface does
        not, because "not measured" with no reason attached is a shrug on a screen whose whole
        claim is that you can tell why. The substitute reason says exactly that much and no more.
        """
        status = unknownable.get("status")
        basis = unknownable.get("basis") or ""
        if status == "unknown":
            if "value" in unknownable:
                raise ValueError(
                    "an Unknownable with status 'unknown' must carry no value; the contract "
                    "forbids it and this is the one place a violation would reach a screen")
            return cls.unknown(basis or "the producer recorded no basis for this figure")
        if status in ("measured", "estimated"):
            value = unknownable.get("value")
            unit = unknownable.get("unit")
            if value is None or not unit:
                raise ValueError("a %s Unknownable needs both value and unit" % status)
            rendered = "%s %s" % (_trim_number(value), unit)
            reason = basis or "no basis recorded by the producer"
            return cls.measured(rendered, reason) if status == "measured" else cls.estimated(rendered, reason)
        raise ValueError("unknown Unknownable status %r" % (status,))

    @property
    def is_known(self) -> bool:
        """True only for a measurement. An estimate is deliberately not 'known'."""
        return self.kind == "measured"

    @property
    def is_estimate(self) -> bool:
        return self.kind == "estimated"

    def render(self) -> str:
        """Plain text for a template. Deliberately never returns a bare number."""
        if self.kind == "measured":
            return "%s (%s)" % (self.value, self.why)
        if self.kind == "estimated":
            return "estimated %s, unconfirmed (%s)" % (self.value, self.why)
        if self.kind == "none":
            return "no effect claimed"
        return "unmeasured (%s)" % self.why

    # A4 INFINITY-STREAMLINE (STREAMLINE item 3, 2026-09-14): the row chip shows the headline at
    # wide widths and keeps the reason beside it in the markup, the tooltip and the panel. The pair
    # is pinned to `render()` by `web/tests/test_attention_chip_headlines.py`, so they cannot drift.
    def headline(self) -> str:
        """`render()` without its parenthetical reason. Never a bare number either."""
        if self.kind == "measured":
            return self.value
        if self.kind == "estimated":
            return "estimated %s, unconfirmed" % self.value
        if self.kind == "none":
            return "no effect claimed"
        return "unmeasured"

    def reason(self) -> str:
        """The parenthetical `render()` carries, or "" where it carries none."""
        return "" if self.kind == "none" else self.why


def _trim_number(value):
    """3.0 prints as 3, 12.5 prints as 12.5. A trailing .0 reads as false precision."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


@dataclass(frozen=True)
class Freshness:
    """When this was last read, and whether that is recent enough to act on.

    `stale` is a real value that is old. `absent` is no value at all. A surface that shows them
    the same way tells a user their pipeline is empty when the connector is broken.
    """

    state: str                      # FRESH | STALE | ABSENT
    at: Optional[datetime] = None
    why: str = ""

    @classmethod
    def read_at(cls, at: datetime, now: datetime, interval: timedelta) -> "Freshness":
        if at.tzinfo is None or now.tzinfo is None:
            raise ValueError("freshness needs timezone-aware instants")
        age = now - at
        if age <= interval:
            return cls(state=FRESH, at=at)
        return cls(
            state=STALE,
            at=at,
            why="last read %s ago; this source usually reports every %s"
                % (_duration(age), _duration(interval)),
        )

    @classmethod
    def never_read(cls, why: str) -> "Freshness":
        if not why:
            raise ValueError("an absent read must say why there is no value")
        return cls(state=ABSENT, at=None, why=why)

    @property
    def has_value(self) -> bool:
        return self.state in (FRESH, STALE)

    def render(self) -> str:
        if self.state == ABSENT:
            return "not read yet (%s)" % self.why
        stamp = self.at.astimezone(_DISPLAY_ZONE).strftime("%a %d %b %Y, %H:%M %Z")
        if self.state == STALE:
            return "stale, as of %s (%s)" % (stamp, self.why)
        return "fresh, as of %s" % stamp

    def headline(self) -> str:
        """`render()` without its parenthetical reason (A4, STREAMLINE item 3)."""
        if self.state == ABSENT:
            return "not read yet"
        stamp = self.at.astimezone(_DISPLAY_ZONE).strftime("%a %d %b %Y, %H:%M %Z")
        return ("stale, as of %s" if self.state == STALE else "fresh, as of %s") % stamp

    def reason(self) -> str:
        """The parenthetical `render()` carries, or "" where it carries none."""
        return self.why if self.state in (ABSENT, STALE) else ""


@dataclass(frozen=True)
class Completeness:
    """Whether a finished piece of work actually covered everything it was supposed to.

    C01's `Receipt.completeness` is complete / partial / uncertain, and this surface had no
    component for it, so a routine that finished with a source missing said "finished" and put the
    caveat in prose that a reader skims. The three states are different promises:

      complete  -- every source it needed was read.
      partial   -- it finished, and it names what it could not include. A partial result is still
                   useful; a partial result that does not say what is missing is a trap, which is
                   why `partial()` refuses to be built without the list.
      uncertain -- it finished and cannot tell whether it covered everything. This is worse than
                   partial and must not be rendered as if it were partial: with `partial` you know
                   the shape of the hole, with `uncertain` you do not know there is one.
    """

    state: str                       # COMPLETE | PARTIAL | UNCERTAIN
    missing: tuple = ()
    why: str = ""

    @classmethod
    def complete(cls) -> "Completeness":
        return cls(state=COMPLETE)

    @classmethod
    def partial(cls, missing, why: str = "") -> "Completeness":
        missing = tuple(missing)
        if not missing:
            raise ValueError(
                "a partial result must name what is missing; 'partial' on its own tells a reader "
                "there is a hole without telling them where, which is worse than either state")
        return cls(state=PARTIAL, missing=missing, why=why)

    @classmethod
    def uncertain(cls, why: str) -> "Completeness":
        if not why:
            raise ValueError("an uncertain result must say what it cannot account for")
        return cls(state=UNCERTAIN, why=why)

    @classmethod
    def from_contract(cls, value: str, missing=(), why: str = "") -> "Completeness":
        """Build from a `Receipt.completeness` value. Unknown values are refused, not defaulted."""
        if value == COMPLETE:
            return cls.complete()
        if value == PARTIAL:
            return cls.partial(missing, why)
        if value == UNCERTAIN:
            return cls.uncertain(why or "the producer recorded no reason")
        raise ValueError("unknown completeness %r" % (value,))

    @property
    def whole(self) -> bool:
        return self.state == COMPLETE

    def render(self) -> str:
        if self.state == COMPLETE:
            return "complete"
        if self.state == PARTIAL:
            listed = ", ".join(self.missing)
            tail = " (%s)" % self.why if self.why else ""
            return "finished without %s%s" % (listed, tail)
        return "finished, but it cannot tell whether anything is missing (%s)" % self.why


def _duration(delta: timedelta) -> str:
    seconds = int(abs(delta.total_seconds()))
    if seconds < 90:
        return "%d s" % seconds
    if seconds < 5400:
        return "%d min" % round(seconds / 60)
    if seconds < 172800:
        return "%d h" % round(seconds / 3600)
    return "%d days" % round(seconds / 86400)
