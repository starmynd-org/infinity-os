"""One authoritative routine, versioned, with the permission boundary attached to the version.

R03. The surface lets a user change a routine two ways, by talking and by controls, and both must
land on the same object. That is not a UI concern: it is this module, and the UI is two front ends
onto `propose()` and `commit()`.

THE RULE THAT MAKES THE APPROVAL MEAN SOMETHING. A standing grant belongs to a version, not to a
routine. When what a routine reads or where its output goes changes, the old grant does not stretch
to cover the new behaviour: a new version is created and a pending action prepared under the old
one stops being approvable (`approval_stands`). Without that, "ask me before anything leaves" is a
promise about a routine the user last read three edits ago.

Nothing here writes. Persistence and dispatch are R01/R02; this is the decision layer they call, so
it can be tested without a database and cannot accidentally acquire an effect.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import List, Optional, Tuple

from .schedule import Occurrence, Schedule, next_occurrence

ACTIVE = "active"
PAUSED = "paused"
DRAFT = "draft"

# Effect policies are display text today, so a positive permission summary may recognise only the
# canonical values emitted by this fixture contract. Natural-language negation is not authority.
_STANDING_EFFECT_POLICIES = frozenset({"sends without asking"})
_ASK_FIRST_EFFECT_POLICIES = frozenset({"asks you first, every time"})


@dataclass(frozen=True)
class Source:
    """Something the routine reads, named as the user would say it, not as a scope string."""

    name: str
    detail: str = ""


@dataclass(frozen=True)
class Effect:
    """Something the routine does. `leaves` is the whole safety question in one boolean."""

    what: str
    leaves: bool = False
    policy: str = ""

    def __post_init__(self) -> None:
        if self.leaves and not self.policy:
            raise ValueError(
                "an effect that leaves the workspace must state its approval policy; "
                "an unstated policy renders as a blank on the consent panel"
            )


@dataclass(frozen=True)
class Grant:
    """What the user agreed this routine may do. Compared structurally, never by identity."""

    sources: Tuple[Source, ...]
    effects: Tuple[Effect, ...]
    destination: str

    @property
    def leaves_workspace(self) -> bool:
        return any(effect.leaves for effect in self.effects)

    @property
    def approval_summary(self) -> str:
        """A truthful compact summary of the outward-effect policies.

        The workbench cannot call every outbound effect "approval required" and then print a
        standing/no-ask policy beside it. Unknown wording stays unknown rather than being guessed.
        """
        policies = [effect.policy.strip().lower() for effect in self.effects if effect.leaves]
        if not policies:
            return "internal only"
        standing = [policy for policy in policies if policy in _STANDING_EFFECT_POLICIES]
        approval = [policy for policy in policies if policy in _ASK_FIRST_EFFECT_POLICIES]
        if len(standing) == len(policies):
            return "external: standing permission"
        if len(approval) == len(policies):
            return "external: approval required"
        if standing and approval:
            return "external: mixed approval policies"
        return "external: policy wording needs review"

    def widens_to(self, other: "Grant") -> bool:
        """True when `other` permits something this grant does not.

        Narrowing is not widening: removing a source or an outbound effect needs no new approval,
        and treating it as if it did would train users to click through consent screens.
        """
        if {s.name for s in other.sources} - {s.name for s in self.sources}:
            return True
        if {(e.what, e.leaves) for e in other.effects} - {(e.what, e.leaves) for e in self.effects}:
            return True
        if other.destination != self.destination:
            return True
        return False


@dataclass(frozen=True)
class Routine:
    """The current version of one routine. Every field change makes a new instance."""

    routine_id: str
    version: int
    title: str
    asked_for: str
    schedule: Schedule
    grant: Grant
    state: str = ACTIVE
    approval_policy: str = "ask-before-anything-leaves"

    def __post_init__(self) -> None:
        if self.version < 1:
            raise ValueError("versions start at 1")
        if self.state not in (ACTIVE, PAUSED, DRAFT):
            raise ValueError("state is %r, %r or %r, got %r" % (ACTIVE, PAUSED, DRAFT, self.state))

    def next_run(self, after: datetime) -> Optional[Occurrence]:
        """None when paused. A paused routine has no next run, and inventing one is a lie."""
        if self.state != ACTIVE:
            return None
        return next_occurrence(after, self.schedule)

    def approval_stands(self, prepared_under_version: int) -> bool:
        """Whether an action prepared under an older version may still be approved."""
        return prepared_under_version == self.version

    def may_run_now(self, host_available: bool) -> Tuple[bool, str]:
        """Two independent reasons a run cannot start, reported separately on purpose.

        A paused routine and an unavailable host are different facts. Collapsing them into one
        'cannot run' is how a surface ends up telling a user their laptop being asleep stopped
        the server.
        """
        if self.state == DRAFT:
            return False, "This routine is a draft. Activate its accepted standing grant first."
        if self.state != ACTIVE:
            return False, "This routine is paused. Resume it first; nothing was lost."
        if not host_available:
            return False, "The host that runs this is not reachable right now."
        return True, ""


@dataclass(frozen=True)
class Proposal:
    """A change that has not happened. The surface renders this before it renders a save button."""

    base_version: int
    changes: Tuple[str, ...]
    requires_new_grant: bool
    reason: str = ""
    schedule: Optional[Schedule] = None
    grant: Optional[Grant] = None
    title: Optional[str] = None
    _touched: Tuple[str, ...] = field(default=(), repr=False)

    @property
    def empty(self) -> bool:
        return not self.changes

    def preview_next_run(self, after: datetime, current: Routine) -> Optional[Occurrence]:
        schedule = self.schedule or current.schedule
        if current.state != ACTIVE:
            return None
        return next_occurrence(after, schedule)


def propose(current: Routine, *, schedule: Optional[Schedule] = None,
            grant: Optional[Grant] = None, title: Optional[str] = None) -> Proposal:
    """Describe what would change, in the user's words, without changing anything."""
    changes: List[str] = []
    touched: List[str] = []

    if schedule is not None and schedule != current.schedule:
        touched.append("schedule")
        if (schedule.hour, schedule.minute) != (current.schedule.hour, current.schedule.minute):
            changes.append("Time %s becomes %s" % (current.schedule.wall, schedule.wall))
        if schedule.days != current.schedule.days:
            changes.append("Days change from %s to %s" % (_days_phrase(current.schedule.days), _days_phrase(schedule.days)))
        if schedule.tz != current.schedule.tz:
            changes.append("Time zone %s becomes %s" % (current.schedule.tz, schedule.tz))

    requires_new_grant = False
    reason = ""
    if grant is not None and grant != current.grant:
        touched.append("grant")
        added_sources = [s.name for s in grant.sources if s.name not in {x.name for x in current.grant.sources}]
        removed_sources = [s.name for s in current.grant.sources if s.name not in {x.name for x in grant.sources}]
        added_effects = [e for e in grant.effects if (e.what, e.leaves) not in {(x.what, x.leaves) for x in current.grant.effects}]
        removed_effects = [e.what for e in current.grant.effects if (e.what, e.leaves) not in {(x.what, x.leaves) for x in grant.effects}]
        for name in added_sources:
            changes.append("Also reads %s" % name)
        for name in removed_sources:
            changes.append("Stops reading %s" % name)
        for effect in added_effects:
            changes.append("Also does: %s" % effect.what)
        for what in removed_effects:
            changes.append("Stops doing: %s" % what)
        old_policies = {(e.what, e.leaves): e.policy for e in current.grant.effects}
        new_policies = {(e.what, e.leaves): e.policy for e in grant.effects}
        for effect_key in sorted(set(old_policies) & set(new_policies)):
            old_policy, new_policy = old_policies[effect_key], new_policies[effect_key]
            if old_policy != new_policy:
                changes.append(
                    "Approval for %s changes from %s to %s"
                    % (effect_key[0], old_policy or "no separate approval", new_policy or "no separate approval")
                )
        if grant.destination != current.grant.destination:
            changes.append("Result goes to %s instead of %s" % (grant.destination, current.grant.destination))
        # A policy is the permission, not decorative copy. A policy-only edit used to produce an
        # empty proposal and `commit()` then kept the old permission. Any such change is now both
        # visible and reviewable, even where the new words appear narrower.
        policy_changed = any(old_policies[key] != new_policies[key] for key in set(old_policies) & set(new_policies))
        requires_new_grant = current.grant.widens_to(grant) or policy_changed
        if requires_new_grant:
            reason = (
                "This asks for something the permission you accepted for version %d does not "
                "cover. Saving replaces that permission, and anything version %d had already "
                "prepared stops being approvable."
                % (current.version, current.version)
            )

    if title is not None and title != current.title:
        touched.append("title")
        changes.append("Name becomes %r" % title)

    return Proposal(
        base_version=current.version,
        changes=tuple(changes),
        requires_new_grant=requires_new_grant,
        reason=reason,
        schedule=schedule,
        grant=grant,
        title=title,
        _touched=tuple(touched),
    )


class ConcurrentEdit(RuntimeError):
    """Raised when a proposal is committed against a version that is no longer current."""


def commit(current: Routine, proposal: Proposal) -> Routine:
    """Apply a proposal, producing the next version. Refuses a stale base."""
    if proposal.base_version != current.version:
        raise ConcurrentEdit(
            "this change was prepared against version %d and the routine is now version %d; "
            "review it again before saving" % (proposal.base_version, current.version)
        )
    if proposal.empty:
        return current
    return replace(
        current,
        version=current.version + 1,
        schedule=proposal.schedule or current.schedule,
        grant=proposal.grant or current.grant,
        title=proposal.title or current.title,
    )


def pause(current: Routine) -> Routine:
    """Pausing does not delete, and it does not stop a run already in flight.

    Stopping in-flight work is a different verb with a different consequence, and a control that
    silently did both would make 'pause' unsafe to press during a run.
    """
    if current.state in (PAUSED, DRAFT):
        return current
    return replace(current, state=PAUSED)


def resume(current: Routine) -> Routine:
    if current.state == DRAFT:
        raise ValueError("a draft must be activated through its accepted standing grant, not resumed")
    if current.state == ACTIVE:
        return current
    return replace(current, state=ACTIVE)


def _days_phrase(days) -> str:
    names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    ordered = sorted(days)
    if ordered == [0, 1, 2, 3, 4]:
        return "weekdays"
    if ordered == list(range(7)):
        return "every day"
    if ordered == [5, 6]:
        return "weekends"
    return ", ".join(names[d] for d in ordered)
