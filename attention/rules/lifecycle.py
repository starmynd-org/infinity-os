"""The rule lifecycle: proposed -> shadow -> active -> retired, with a human at every promotion.

The point of the ladder is that a rule EARNS automation by being measured, and the measurement
happens in shadow where being wrong costs nothing. `P/intake/rules/graduation-path.md` is the
doctrine; this is the machine.

    proposed  a repetition was observed. Nothing runs. It is a suggestion to a human.
    shadow    the rule is evaluated on live items and its answers are RECORDED, not applied.
    active    an exact approved version auto-handles matching items, with a receipt each time.
    retired   it no longer handles anything. Its history and its receipts survive.

THE PROMOTION RULE, and it is the whole authority gate: only `activate` puts a rule into
production, it requires a named human decider, and it binds to an exact `version_hash`. A revised
rule is a NEW version in `proposed`, so an edit can never inherit the previous version's approval.
An agent cannot appear in `decided_by` — the same refusal T01 makes for packet approvals.

RETIREMENT IS NOT DELETION. `retire` restores judgment routing and keeps every receipt, because
the acceptance line is "retire restores judgment routing without losing evidence".
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterator

from .definitions import RuleDefinition


class RuleState(str, Enum):
    PROPOSED = "proposed"
    SHADOW = "shadow"
    ACTIVE = "active"
    RETIRED = "retired"

    @property
    def auto_handles(self) -> bool:
        return self is RuleState.ACTIVE


class LifecycleError(RuntimeError):
    """An illegal transition, or one without a human behind it."""


_ALLOWED: dict[RuleState, tuple[RuleState, ...]] = {
    RuleState.PROPOSED: (RuleState.SHADOW, RuleState.RETIRED),
    RuleState.SHADOW: (RuleState.ACTIVE, RuleState.RETIRED),
    # An active rule can be paused back to shadow, which is the softest recovery available.
    RuleState.ACTIVE: (RuleState.SHADOW, RuleState.RETIRED),
    RuleState.RETIRED: (),
}

_NON_HUMAN = {"agent", "system", "auto", "claude", "assistant", "rule", "scheduler", ""}


def utcnow() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class Transition:
    rule_id: str
    version: int
    version_hash: str
    from_state: RuleState
    to_state: RuleState
    decided_by: str
    decided_at: str
    reason: str = ""
    evidence: tuple[str, ...] = ()


@dataclass
class RuleRecord:
    definition: RuleDefinition
    state: RuleState
    #: The exact hash a human approved for ACTIVE. None unless it has been activated.
    approved_hash: str | None = None
    approved_by: str | None = None

    @property
    def auto_handles(self) -> bool:
        """The single predicate the triage engine consults. Both halves are required."""
        return self.state.auto_handles and self.approved_hash == self.definition.version_hash


class RuleRegistry:
    """The lifecycle, plus an append-only transition log."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, int], RuleRecord] = {}
        self._log: list[Transition] = []

    # -- entry ---------------------------------------------------------------------------------

    def propose(self, definition: RuleDefinition, *, reason: str = "",
                evidence: tuple[str, ...] = ()) -> RuleRecord:
        """A rule enters the world proposed. Nothing runs; a human is being asked."""
        if definition.key in self._records:
            raise LifecycleError(
                f"{definition.rule_id} v{definition.version} already exists; revise it to make a "
                "new version instead"
            )
        record = RuleRecord(definition=definition, state=RuleState.PROPOSED)
        self._records[definition.key] = record
        self._log.append(
            Transition(definition.rule_id, definition.version, definition.version_hash,
                       RuleState.PROPOSED, RuleState.PROPOSED, "observation", utcnow(),
                       reason or "repetition observed", evidence)
        )
        return record

    # -- transitions ----------------------------------------------------------------------------

    def _move(self, rule_id: str, version: int, to: RuleState, decided_by: str,
              reason: str) -> RuleRecord:
        record = self._records.get((rule_id, version))
        if record is None:
            raise LifecycleError(f"no rule {rule_id} v{version}")
        if to not in _ALLOWED[record.state]:
            raise LifecycleError(
                f"{rule_id} v{version} cannot go {record.state.value} -> {to.value}"
            )
        if decided_by.strip().lower() in _NON_HUMAN:
            raise LifecycleError(
                f"{decided_by!r} cannot promote a rule: an agent cannot approve its own automation"
            )
        previous = record.state
        record.state = to
        if to is RuleState.ACTIVE:
            record.approved_hash = record.definition.version_hash
            record.approved_by = decided_by
        if to in (RuleState.SHADOW, RuleState.RETIRED):
            # Leaving ACTIVE drops the approval. Re-activating is a fresh human decision.
            record.approved_hash = None
            record.approved_by = None
        self._log.append(
            Transition(rule_id, version, record.definition.version_hash, previous, to,
                       decided_by, utcnow(), reason)
        )
        return record

    def shadow(self, rule_id: str, version: int, decided_by: str, reason: str = "") -> RuleRecord:
        return self._move(rule_id, version, RuleState.SHADOW, decided_by, reason)

    def activate(self, rule_id: str, version: int, decided_by: str, reason: str = "") -> RuleRecord:
        return self._move(rule_id, version, RuleState.ACTIVE, decided_by, reason)

    def retire(self, rule_id: str, version: int, decided_by: str, reason: str = "") -> RuleRecord:
        return self._move(rule_id, version, RuleState.RETIRED, decided_by, reason)

    def revise(self, rule_id: str, version: int, decided_by: str, **changes: Any) -> RuleRecord:
        """Edit an active rule. The old version stops auto-handling; the new one starts proposed."""
        record = self._records.get((rule_id, version))
        if record is None:
            raise LifecycleError(f"no rule {rule_id} v{version}")
        revised = record.definition.revise(**changes)
        if record.state is RuleState.ACTIVE:
            self.retire(rule_id, version, decided_by, reason=f"superseded by v{revised.version}")
        return self.propose(revised, reason=f"revision of v{version}")

    # -- reads -----------------------------------------------------------------------------------

    def get(self, rule_id: str, version: int) -> RuleRecord | None:
        return self._records.get((rule_id, version))

    def active(self) -> tuple[RuleRecord, ...]:
        return tuple(r for r in self._records.values() if r.auto_handles)

    def shadowing(self) -> tuple[RuleRecord, ...]:
        return tuple(r for r in self._records.values() if r.state is RuleState.SHADOW)

    def all_records(self) -> tuple[RuleRecord, ...]:
        return tuple(self._records.values())

    def transitions(self, rule_id: str | None = None) -> tuple[Transition, ...]:
        return tuple(t for t in self._log if rule_id is None or t.rule_id == rule_id)
