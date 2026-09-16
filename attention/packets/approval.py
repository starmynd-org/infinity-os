"""Approval binds to one immutable packet version, and to nothing else.

Invariant 8 of the end-to-end flow: *approval authorises a specific packet version, not an
unlimited objective.* This module is that sentence, executable.

WHAT AN APPROVAL IS NOT, and each of these is a failure the review found in V2:

* It is not implied by ORIGIN. A packet that came from a trusted agent, a rule, or the operator's
  own inbox is not thereby approved.
* It is not implied by CONFIDENCE. A 0.98 score is not a decision.
* It is not implied by PROSE. An agent writing "approved" in its summary approves nothing.
* It is not implied by TIME. Nothing here ages into approval.

So `Approval` can only be constructed with a human decider and an exact `version_hash`, and
`authorises` re-derives the hash from the packet it is handed. A packet edited after approval
produces a different hash and the approval simply does not match it — there is no path where a
stale approval accepts a changed target, because the match is structural rather than a flag
someone has to remember to clear.

`ApprovalLedger.check` returns a REASON, not a boolean, because "why did this not run" is the
question an operator actually asks.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .completeness import Completeness, check
from .model import Authority, WorkPacket


class Decision(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"
    DEFERRED = "deferred"
    CHANGES_REQUESTED = "changes-requested"


class ApprovalError(RuntimeError):
    """An approval could not be constructed or recorded. Never downgraded to a warning."""


@dataclass(frozen=True)
class Approval:
    """A human decision about one exact packet version."""

    packet_id: str
    version_hash: str
    decision: Decision
    decided_by: str
    decided_at: str
    authority_granted: Authority
    note: str = ""
    #: The exact actions this decision authorises, under C01's AD-I2. EMPTY MEANS THE WHOLE PACKET,
    #: which is what every approval meant before actions had identity; naming a subset is the new
    #: capability and naming nothing is unchanged behaviour.
    action_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.decided_by.strip():
            raise ApprovalError("an approval with no decider is not an approval")
        if self.decided_by.strip().lower() in ("agent", "system", "auto", "claude", "assistant"):
            raise ApprovalError(
                f"{self.decided_by!r} cannot be a decider: an agent cannot approve its own work"
            )
        if not self.version_hash.startswith("wp_"):
            raise ApprovalError("an approval must name a packet version hash")

    def authorises(self, packet: WorkPacket) -> bool:
        """Does this approval cover THIS packet as it stands right now?"""
        if self.decision is not Decision.APPROVED:
            return False
        if packet.packet_id != self.packet_id:
            return False
        if packet.version_hash != self.version_hash:
            return False
        # AD-I2: NO ACTION WIDENING, checked in both directions because each catches a different
        # thing. An id this approval names that the packet does not carry means the approval is
        # stale or forged: it was written against actions that are not these. An action the packet
        # carries that the approval does not name means the approval covers less than the packet
        # would do, which is the widening the invariant is named for. Either way it does not
        # authorise THIS packet, and an approval that named nothing authorises the packet whole,
        # which is what every approval meant before actions had identity.
        if self.action_ids:
            carried = set(packet.action_ids())
            named = set(self.action_ids)
            if named - carried or carried - named:
                return False

        # An approval cannot grant more authority than it was given, even for the right hash.
        return _authority_rank(self.authority_granted) >= _authority_rank(packet.authority_required)


_RANK = {
    Authority.NONE: 0,
    Authority.INTERNAL: 1,
    Authority.EXTERNAL: 2,
    Authority.FINANCIAL: 3,
    Authority.UNKNOWN: 99,
}


def _authority_rank(a: Authority) -> int:
    return _RANK[a]


@dataclass(frozen=True)
class ExecutionCheck:
    allowed: bool
    reason: str
    completeness: Completeness | None = None


class ApprovalLedger:
    """Append-only record of decisions. Approved versions stay readable for ever."""

    def __init__(self) -> None:
        self._by_hash: dict[str, Approval] = {}
        self._history: list[Approval] = []

    def record(self, approval: Approval) -> Approval:
        existing = self._by_hash.get(approval.version_hash)
        if existing is not None and existing.decision is not approval.decision:
            raise ApprovalError(
                f"version {approval.version_hash} already has decision "
                f"{existing.decision.value}; record a decision on a new version instead of "
                "overwriting a human's answer"
            )
        self._by_hash[approval.version_hash] = approval
        self._history.append(approval)
        return approval

    def for_version(self, version_hash: str) -> Approval | None:
        return self._by_hash.get(version_hash)

    def history(self, packet_id: str) -> tuple[Approval, ...]:
        return tuple(a for a in self._history if a.packet_id == packet_id)

    def check(self, packet: WorkPacket, *, known_capture_ids=None) -> ExecutionCheck:
        """The single gate. Completeness first, then an approval that matches THIS version."""
        completeness = check(packet, known_capture_ids=known_capture_ids)
        if not completeness.can_execute:
            return ExecutionCheck(
                False,
                "incomplete: " + ", ".join(f.rule for f in completeness.blocking),
                completeness,
            )
        approval = self._by_hash.get(packet.version_hash)
        if approval is None:
            prior = self.history(packet.packet_id)
            if prior:
                return ExecutionCheck(
                    False,
                    f"the approved version was {prior[-1].version_hash}, but this packet is "
                    f"{packet.version_hash}: it changed after approval and needs a new decision",
                    completeness,
                )
            return ExecutionCheck(False, "no decision has been recorded for this version",
                                  completeness)
        if not approval.authorises(packet):
            if approval.decision is not Decision.APPROVED:
                return ExecutionCheck(False, f"decision was {approval.decision.value}",
                                      completeness)
            if approval.action_ids:
                carried = set(packet.action_ids())
                named = set(approval.action_ids)
                unknown, uncovered = sorted(named - carried), sorted(carried - named)
                if unknown:
                    return ExecutionCheck(
                        False,
                        f"AD-I2: the approval names action(s) this packet does not carry "
                        f"({unknown}); it was written against different actions",
                        completeness,
                    )
                if uncovered:
                    return ExecutionCheck(
                        False,
                        f"AD-I2: the packet carries action(s) the approval does not name "
                        f"({uncovered}); an approval never widens to cover them",
                        completeness,
                    )
            return ExecutionCheck(
                False,
                f"approval granted {approval.authority_granted.value} authority but the packet "
                f"requires {packet.authority_required.value}",
                completeness,
            )
        return ExecutionCheck(True, "approved for this exact version", completeness)


def utcnow() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
