"""When a routine runs, answered from the zone's real rules and never from a guess.

R03. One rule governs this module: **a schedule answer is computed from tzdata, or it is not
given.** The surface shows the user a next run and a clock-change explanation; both come from
here, so a plausible-looking wrong answer here is a user who missed a morning.

THE TWO DAYS A YEAR THIS FILE EXISTS FOR. A wall-clock time is not a fact about an instant. In
Europe/Bucharest the clocks jump 03:00 -> 04:00 on the last Sunday in March, so 03:30 does not
happen that day; they fall back 04:00 -> 03:00 on the last Sunday in October, so 03:30 happens
twice. `datetime(2027, 3, 28, 3, 30, tzinfo=ZoneInfo("Europe/Bucharest"))` does NOT raise for the
first case: it silently carries a +02:00 offset that maps back to 04:30 local. Measured, and the
reason `resolve()` round-trips every construction rather than trusting one.

The policy for each case, and both are choices this module makes explicitly rather than inherits:

  * **gap**  -> the routine runs ONCE, at the instant the requested wall time WOULD have been.
    03:30 on the pre-jump offset is 01:30 UTC, and the moved clock calls that 04:30 EEST, so the
    run happens at 04:30 and the screen says why. Not skipped, because a brief that silently
    vanishes once a year is a bug the user cannot see; not fired twice, because the acceptance
    evidence for R03 is that no duplicate fires; and NOT moved to the transition instant, which
    was this module's first answer and was wrong. 04:00 is the moment the clock exists again, but
    it is thirty minutes EARLIER in absolute time than the user asked for, so a routine promising
    "every weekday at 8:30" would quietly run closer to its previous run once a year, in the
    direction of starting before its inputs are ready. (Terminal 08, CAP14-REV-010.)
  * **repeat** -> the routine runs ONCE, on the FIRST pass (`fold=0`). The second pass is a
    scheduling no-op, and `Occurrence.kind` says so, so a receipt can explain why an hour that
    happened twice produced one run.

Nothing here reads a store, a clock source or a host. `next_occurrence` takes the current instant
as an argument so tests state their own "now" rather than depending on the day they run.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as _date, datetime, time, timedelta, timezone
from typing import FrozenSet, Iterator, Optional
from zoneinfo import ZoneInfo

UTC = timezone.utc

# 0 = Monday, matching datetime.weekday(). The surface's word for {0,1,2,3,4}.
WEEKDAYS: FrozenSet[int] = frozenset({0, 1, 2, 3, 4})
EVERY_DAY: FrozenSet[int] = frozenset(range(7))


@dataclass(frozen=True)
class Schedule:
    """A recurring wall-clock time in one named zone. Immutable: an edit makes a new one."""

    hour: int
    minute: int
    days: FrozenSet[int]
    tz: str

    def __post_init__(self) -> None:
        if not 0 <= self.hour <= 23 or not 0 <= self.minute <= 59:
            raise ValueError("hour must be 0-23 and minute 0-59, got %02d:%02d" % (self.hour, self.minute))
        if not self.days:
            raise ValueError("a schedule with no days never runs; say so explicitly instead")
        if not self.days <= EVERY_DAY:
            raise ValueError("days are 0 (Monday) through 6 (Sunday)")
        ZoneInfo(self.tz)  # raises now, at construction, rather than at the first run

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.tz)

    @property
    def wall(self) -> str:
        return "%02d:%02d" % (self.hour, self.minute)


@dataclass(frozen=True)
class Occurrence:
    """One resolved run. `at` is the instant; `local` is what the user's clock will read."""

    at: datetime           # aware, UTC
    local: datetime        # aware, in the schedule's zone
    kind: str              # "ok" | "gap" | "repeat"
    requested: str         # the wall time the user asked for, e.g. "03:30"

    @property
    def surprising(self) -> bool:
        """True when the user's clock will not read what they typed. The surface must say why."""
        return self.kind != "ok"

    def explain(self) -> str:
        if self.kind == "gap":
            return (
                "%s does not exist on that date: the clocks jump forward. This runs once, at %s, "
                "the same moment %s would have been, because the whole clock moved forward with "
                "it. It is not skipped and it is not run twice."
                % (self.requested, self.local.strftime("%H:%M %Z"), self.requested)
            )
        if self.kind == "repeat":
            return (
                "%s happens twice on that date because the clocks go back. This runs once, on the "
                "first pass at %s; the second pass is recorded as already done."
                % (self.requested, self.local.strftime("%H:%M %Z"))
            )
        return "Runs at %s, exactly as written." % self.local.strftime("%H:%M %Z")


def _offset(zone: ZoneInfo, moment: datetime) -> timedelta:
    return moment.astimezone(zone).utcoffset() or timedelta(0)


def resolve(day: _date, schedule: Schedule) -> Occurrence:
    """Resolve the schedule's wall time on one calendar day, whatever the zone does that day.

    The day is not checked against `schedule.days`: this answers "what would happen on this date",
    which is exactly what the clock-change explanation on the surface needs.
    """
    zone = schedule.zone
    wanted = time(schedule.hour, schedule.minute)
    naive_local = datetime.combine(day, wanted)

    first = naive_local.replace(tzinfo=zone, fold=0)
    second = naive_local.replace(tzinfo=zone, fold=1)

    # A gap does not raise; it round-trips to a different wall clock. That is the only reliable
    # detection, and it is why this is not `try: ... except`.
    round_tripped = first.astimezone(UTC).astimezone(zone)
    if round_tripped.time() != wanted or round_tripped.date() != day:
        # The wall time does not exist. `first.astimezone(UTC)` is the instant it WOULD have been,
        # read on the offset in force before the jump; the moved clock calls that instant something
        # later. Run there, and let `explain()` say why the clock reads what it reads.
        at = first.astimezone(UTC)
        return Occurrence(at=at, local=at.astimezone(zone), kind="gap", requested=schedule.wall)

    if first.utcoffset() != second.utcoffset():
        at = first.astimezone(UTC)
        return Occurrence(at=at, local=at.astimezone(zone), kind="repeat", requested=schedule.wall)

    at = first.astimezone(UTC)
    return Occurrence(at=at, local=at.astimezone(zone), kind="ok", requested=schedule.wall)


def occurrences(after: datetime, schedule: Schedule, limit: int = 10) -> Iterator[Occurrence]:
    """The next `limit` runs strictly after `after`. `after` must be timezone aware."""
    if after.tzinfo is None:
        raise ValueError("`after` must be timezone aware; a naive instant has no meaning here")
    zone = schedule.zone
    day = after.astimezone(zone).date()
    found = 0
    for step in range(0, 400):
        candidate = day + timedelta(days=step)
        if candidate.weekday() not in schedule.days:
            continue
        occurrence = resolve(candidate, schedule)
        if occurrence.at <= after:
            continue
        yield occurrence
        found += 1
        if found >= limit:
            return


def next_occurrence(after: datetime, schedule: Schedule) -> Optional[Occurrence]:
    for occurrence in occurrences(after, schedule, limit=1):
        return occurrence
    return None  # pragma: no cover - unreachable for any non-empty day set inside 400 days


def missed_since(last_checked: datetime, now: datetime, schedule: Schedule) -> list:
    """Runs that were due between two instants and did not happen.

    Returned, never re-fired. A routine with an effect the user approved once is not a routine
    whose four skipped mornings may all fire at 14:00 because a host came back. The caller decides
    whether to run the most recent one, and the surface says a run was missed either way.
    """
    if last_checked.tzinfo is None or now.tzinfo is None:
        raise ValueError("both instants must be timezone aware")
    due = []
    for occurrence in occurrences(last_checked, schedule, limit=400):
        if occurrence.at > now:
            break
        due.append(occurrence)
    return due
