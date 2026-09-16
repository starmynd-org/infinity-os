"""The seams the spine crosses, and the evidence row every stage leaves behind.

L01-SPINE-01. The G3 evidence nobody owned: one fixture run in which a capture enters at the intake
door and comes out the far end as a settled effect with a receipt, crossing every lane boundary on
the way.

WHY THIS FILE IS PROTOCOLS AND NOT CODE. Three of the six stages belong to lanes whose
implementations are not in this checkout (see `preflight.py`, which measures that rather than
asserting it). Writing them as ports means the spine is composed against the shape each lane
publishes, and swapping a real implementation in later is a constructor argument rather than a
rewrite. It also keeps the honest distinction visible at every call site: a stage backed by a
stand-in says so in its own evidence row.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

#: What backs a stage. `real` means this checkout's own product code ran. `stand-in` means a
#: reference implementation in `standins.py` ran because the owning lane's code is not here. There
#: is deliberately no third value: a stage is one or the other, and a run that cannot say which is
#: not evidence.
REAL = "real"
STANDIN = "stand-in"


@dataclass(frozen=True)
class StageEvidence:
    """One printable row. The spine's whole output is a list of these.

    `detail` carries the identifiers a reader needs to follow the trail into the stores by hand:
    capture ids, packet ids, action ids, receipt ids. It is not a summary of what happened, it is
    the set of keys that let someone check.
    """

    stage: str
    backing: str
    ok: bool
    summary: str
    detail: Mapping[str, Any] = field(default_factory=dict)

    def row(self) -> str:
        keys = " ".join(f"{k}={v}" for k, v in self.detail.items())
        return (
            f"{'PASS' if self.ok else 'FAIL'}  {self.stage:<28} {self.backing:<9} "
            f"{self.summary}"
            + (f"  [{keys}]" if keys else "")
        )


class ApprovalRecorder(Protocol):
    """R01's half: a human decision, recorded, naming the actions it authorises.

    `record` takes the packet's version hash and the action ids, because an approval that names
    neither cannot be checked against the packet it is later used to justify. AD-I2 binds an
    approval to specific action ids in both directions, and this lane's `ApprovalLedger` already
    enforces it.
    """

    def record_approval(self, entry: Mapping[str, Any]) -> str: ...

    def approval_for(self, version_hash: str) -> Mapping[str, Any] | None: ...


class ExecutionPort(Protocol):
    """R02's half: take the lease, reserve the effect against an approval, settle with a receipt.

    THE SINGLE-USE PROPERTY LIVES HERE AND NOWHERE ELSE. `ApprovalLedger.check` answers "does this
    decision authorise this packet version and these actions", which is a question about the
    packet. "Has this approval already been spent" is a question about the world, and only the
    thing that reserves effects can answer it. Putting it in the ledger would make an approval
    look spent to a reader that had merely checked it.
    """

    def acquire_lease(self, action_id: str, *, holder: str) -> str: ...

    def reserve(self, entry: Mapping[str, Any]) -> str: ...

    def settle(self, reservation_id: str, *, outcome: str, completeness: str) -> Mapping[str, Any]: ...


class PromotionSink(Protocol):
    """B01's half: the far end. A settled result arrives where promotion decisions are made."""

    def receive(self, result: Mapping[str, Any]) -> str: ...


class SpineRefused(RuntimeError):
    """A stage refused. Every refusal in this spine is named and carries its reason."""
