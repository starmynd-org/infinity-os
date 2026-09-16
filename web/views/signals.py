"""The seven signals, and the ranked queue's explanation read from them instead of from prose.

R04, closing the third disagreement this lane recorded against C01. The queue explained its order
with one constant sentence covering every item, while the contract carries seven typed signals per
work packet. A constant explanation is not an explanation: it says how the ranker is meant to
think, not why THIS item is where it is, and it stays plausible after the ranker changes.

TWO RULES, AND THE SECOND ONE IS THE POINT.

1. **An undeclared signal is not a signal.** `_system` doctrine says a missing signal defaults
   conservative: unknown reversibility is treated as costly-or-irreversible, unknown stakes as
   high. That is right for ORDERING and wrong for EXPLAINING. If the ranker treated a silence as
   "high stakes" and the screen then told a person "this is first because it is high stakes", the
   screen would be reporting the system's own guess back as the producer's assessment. So
   `conservative_stakes()` and `conservative_reversibility()` exist for the ranker, `declared`
   says what was actually stated, and `explain()` renders only what was declared, then names the
   silence separately.

2. **An unknown value is refused, never coerced.** The vocabularies are closed. A packet arriving
   with `stakes: "urgent"` is a producer bug, and mapping it to something nearby would hide it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

STAKES = ("low", "medium", "high", "critical")
REVERSIBILITY = ("reversible", "costly", "irreversible")
URGENCY = ("none", "soon", "deadline", "decaying")
EFFORT = ("small", "medium", "large")
ALIGNMENT = ("low", "medium", "high")


@dataclass(frozen=True)
class Signals:
    """The seven weighted signals. The two hard flags live at packet level, not here."""

    stakes: Optional[str] = None
    reversibility: Optional[str] = None
    urgency: Optional[str] = None
    dependency_unblocking: Optional[int] = None
    effort: Optional[str] = None
    confidence: Optional[float] = None
    charter_alignment: Optional[str] = None
    display: Optional[Tuple[str, str]] = None

    def __post_init__(self) -> None:
        _one_of("stakes", self.stakes, STAKES)
        _one_of("reversibility", self.reversibility, REVERSIBILITY)
        _one_of("urgency", self.urgency, URGENCY)
        _one_of("effort", self.effort, EFFORT)
        _one_of("charter_alignment", self.charter_alignment, ALIGNMENT)
        if self.dependency_unblocking is not None and self.dependency_unblocking < 0:
            raise ValueError("dependency_unblocking counts items and cannot be negative")
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence is 0.0 to 1.0, got %r" % (self.confidence,))
        if self.display is not None and (len(self.display) != 2 or not all(isinstance(value, str) for value in self.display)):
            raise ValueError("display is a (label, note) string pair and never ranking evidence")

    @classmethod
    def from_contract(cls, signals: dict) -> "Signals":
        known = {f: signals.get(f) for f in (
            "stakes", "reversibility", "urgency", "dependency_unblocking", "effort",
            "confidence", "charter_alignment")}
        display_present = "display" in signals
        display = signals.get("display")
        if display_present:
            if not isinstance(display, dict) or set(display) - {"label", "note"}:
                raise ValueError("display permits only optional label and note strings; it is inspector-only")
            label, note = display.get("label", ""), display.get("note", "")
            if not isinstance(label, str) or not isinstance(note, str):
                raise ValueError("display label and note are strings when supplied")
            if label or note:
                known["display"] = (label or "Producer display", note)
        unexpected = set(signals) - set(known) - {"display"}
        if unexpected:
            raise ValueError("signals this surface does not know: %s" % ", ".join(sorted(unexpected)))
        return cls(**known)

    # ---- what was actually said, and what was not ------------------------------------------

    @property
    def declared(self) -> Tuple[str, ...]:
        return tuple(name for name, value in self._pairs() if value is not None)

    @property
    def undeclared(self) -> Tuple[str, ...]:
        return tuple(name for name, value in self._pairs() if value is None)

    def _pairs(self):
        return (
            ("stakes", self.stakes),
            ("reversibility", self.reversibility),
            ("urgency", self.urgency),
            ("dependency_unblocking", self.dependency_unblocking),
            ("effort", self.effort),
            ("confidence", self.confidence),
            ("charter_alignment", self.charter_alignment),
        )

    # ---- what the ranker may assume, which is not what the screen may say --------------------

    def conservative_stakes(self) -> str:
        """For ordering. Silence is treated as high, per the signal-vocabulary rule."""
        return self.stakes or "high"

    def conservative_reversibility(self) -> str:
        """For ordering. Silence is treated as costly."""
        return self.reversibility or "costly"

    # ---- the sentence a person reads --------------------------------------------------------

    def explain(self) -> str:
        """WHAT WAS DECLARED, from declared signals only. NOT why this item sits where it does.

        Returns an empty string when nothing was declared, so a caller can fall back to the
        queue-level explanation rather than print a sentence made entirely of defaults.

        RELEASE 22, and it finishes this module's own argument rather than reversing it. R04's
        docstring above says a constant sentence "says how the ranker is meant to think, not why
        THIS item is where it is" -- and fixing that, this function overshot into the opposite
        claim. It opened "Ranked here because ...", which asserts these declared facts PRODUCED
        this position. They did not: the position is assigned by enumerating the queue-wide
        tier-flattened ranking, so the same sentence appears at #1 and at #5 and reads as a causal
        trace at both. `term-4` traced the three separate producers of that row's strings and
        `term-14` bound the source; `term-13` released the repair as ATT-DECLARED-SIGNAL-
        EXPLANATION-01.

        So the sentence now LABELS ITS EVIDENCE instead of claiming a cause. The clauses are
        unchanged -- every declared value renders exactly the words it rendered before -- and only
        the lead-in moved, because the defect was never in what was reported but in what the
        report claimed about it.

        The silence rule is untouched and is the model this follows: `silence_note()` already ends
        "which is the safe assumption and not something anyone claimed."
        """
        clauses = []
        if self.urgency == "deadline":
            clauses.append("it has a dated commitment")
        elif self.urgency == "decaying":
            clauses.append("its value is decaying")
        elif self.urgency == "soon":
            clauses.append("it is wanted soon")
        elif self.urgency == "none":
            clauses.append("nothing is waiting on the clock")

        if self.dependency_unblocking:
            clauses.append("finishing it unblocks %d other %s"
                           % (self.dependency_unblocking,
                              "item" if self.dependency_unblocking == 1 else "items"))

        if self.stakes in ("high", "critical"):
            clauses.append("the stakes are %s" % self.stakes)
        elif self.stakes == "low":
            clauses.append("the stakes are low")

        if self.reversibility == "irreversible":
            clauses.append("it cannot be undone")
        elif self.reversibility == "costly":
            clauses.append("undoing it would be costly")

        if self.confidence is not None and self.confidence < 0.5:
            clauses.append("the producer is not confident in it (%.0f%%)" % (self.confidence * 100))

        if not clauses:
            return ""
        return "Declared for ranking: " + _join(clauses) + "."

    def silence_note(self) -> str:
        """What nobody said. Rendered separately so a default is never read as an assessment."""
        if not self.undeclared:
            return ""
        return ("Not stated by the producer: %s. The ranking treats an unstated stake as high and "
                "unstated reversibility as costly, which is the safe assumption and not something "
                "anyone claimed." % _join(list(self.undeclared)))


def _one_of(name, value, allowed):
    if value is not None and value not in allowed:
        raise ValueError("%s is one of %s, got %r; a closed vocabulary is not coerced"
                         % (name, ", ".join(allowed), value))


def _join(parts):
    parts = list(parts)
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + " and " + parts[-1]
