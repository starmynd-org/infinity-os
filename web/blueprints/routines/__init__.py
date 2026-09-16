"""The routines surface: read, and preview a change. It does NOT write.

R03. `web/app.py`'s opening comment states the one write door: every state change on this console
goes through `POST /<room>/act`, which calls `rooms.dispatch`, which calls `store.apply`, and the
reason it is one route rather than five is that the fifth one written in a hurry is the one that
forgets to check. A routines blueprint with its own `POST /routines/<id>/pause` would be that
fifth route, so this blueprint has no write route at all.

What lives here instead:

  * read views over a `RoutineReadPort` the caller supplies, so this file has no store dependency
    and can be rendered in a test with a stub;
  * `POST /routines/<id>/preview`, which calls `routines.propose()` and renders the result. It
    changes nothing: proposing is a pure function, and showing a user what would happen before
    they agree is the whole point of the R03 flow.

The verbs (activate, pause, resume, run now, commit an edit) are registered with the console's
existing dispatcher rather than served here. `verbs.py` beside this file holds them in the shape
`rooms.dispatch` consumes; wiring that table entry and registering this blueprint are two lines in
files L01 owns, and they go to the Admiral as a narrow patch with a fixture rather than being
edited here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, List, Optional, Protocol

from flask import Blueprint, abort, render_template, request

from routines import ACTIVE, DRAFT, PAUSED, Routine, Schedule, propose
from web.views import display_zone

UTC = timezone.utc


@dataclass(frozen=True)
class HistoryEntry:
    """One line of what happened to a routine. Supplied by the store, never computed here."""

    at: datetime
    version: int
    kind: str          # created | edited | ran | missed | paused | resumed | ran-now
    text: str
    by: str = ""
    duration: str = ""


@dataclass(frozen=True)
class HostStatus:
    """Where a routine runs, and whether that place is reachable.

    Separate from the routine's own state on purpose: `Routine.may_run_now` takes availability as
    an argument precisely so a surface cannot report a sleeping laptop as a stopped server.
    """

    name: str
    kind: str
    reachable: bool
    detail: str = ""

    @property
    def sentence(self) -> str:
        if self.reachable:
            return "%s (%s) is accepting work. This does not depend on your laptop being awake." % (self.name, self.kind)
        return "%s (%s) is not reachable right now. %s" % (self.name, self.kind, self.detail)


def _capability_note(port):
    """The sentence a port prints when it cannot see its data at all.

    Optional on the Protocol on purpose: a stub in a test supplies rows and has nothing to say,
    while the store-backed port in `web/model.py` has plenty to say until R01 lands. A port that
    cannot answer is treated as having nothing to report, never as an outage.
    """
    getter = getattr(port, "capability_note", None)
    return getter() if callable(getter) else None


class RoutineReadPort(Protocol):
    """What R01 must supply. Reads only: this port has no write method to call by accident.

    `capability_note()` is optional: return a sentence when the store cannot hold routines at all,
    so the surface says so instead of rendering an empty list that reads as "you have none".
    """

    def list_routines(self) -> List[Routine]: ...

    def get_routine(self, routine_id: str) -> Optional[Routine]: ...

    def history(self, routine_id: str) -> List[HistoryEntry]: ...

    def host_for(self, routine_id: str) -> HostStatus: ...


def build(port: RoutineReadPort, now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> Blueprint:
    """Make the blueprint over a read port. `now` is injected so tests state their own instant."""

    bp = Blueprint("routines", __name__, url_prefix="/routines")

    @bp.get("/")
    def index():
        moment = now()
        rows = []
        for routine in port.list_routines():
            history = port.history(routine.routine_id)
            host = port.host_for(routine.routine_id)
            rows.append({
                "routine": routine,
                "next": routine.next_run(moment),
                "host": host,
                "history": history,
                "last": history[0] if history else None,
                "updated": history[0].at if history else None,
                "needs_attention": routine.state != ACTIVE or not host.reachable
                                   or any(entry.kind == "missed" for entry in history),
            })
        tab = request.args.get("tab", "all")
        predicates = {
            "all": lambda row: True,
            "running": lambda row: row["routine"].state == ACTIVE,
            "drafts": lambda row: row["routine"].state == DRAFT,
            "paused": lambda row: row["routine"].state == PAUSED,
            "attention": lambda row: row["needs_attention"],
            "history": lambda row: bool(row["history"]),
        }
        if tab not in predicates:
            abort(400)
        rows = [row for row in rows if predicates[tab](row)]
        return render_template("routines/index.html", rows=rows, tab=tab, room="routines", now=moment,
                               display_zone=display_zone, note=_capability_note(port))

    @bp.get("/<routine_id>")
    def detail(routine_id):
        routine = port.get_routine(routine_id)
        if routine is None:
            abort(404)
        moment = now()
        return_tab = request.args.get("tab", "all")
        if return_tab not in ("all", "running", "drafts", "paused", "attention", "history"):
            return_tab = "all"
        return render_template(
            "routines/detail.html",
            routine=routine,
            next_run=routine.next_run(moment),
            host=port.host_for(routine_id),
            history=port.history(routine_id),
            display_zone=display_zone,
            room="routines",
            now=moment,
            return_tab=return_tab,
        )

    @bp.post("/<routine_id>/preview")
    def preview(routine_id):
        """Render what a change would do. Writes nothing; there is nothing here to undo."""
        routine = port.get_routine(routine_id)
        if routine is None:
            abort(404)
        wall = (request.form.get("time") or "").strip()
        days_key = (request.form.get("days") or "").strip()
        schedule = _schedule_from_form(routine, wall, days_key)
        proposal = propose(routine, schedule=schedule)
        moment = now()
        return_tab = request.form.get("tab", "all")
        if return_tab not in ("all", "running", "drafts", "paused", "attention", "history"):
            return_tab = "all"
        return render_template(
            "routines/_proposal.html",
            routine=routine,
            proposal=proposal,
            preview_next=proposal.preview_next_run(moment, routine),
            room="routines",
            return_tab=return_tab,
        )

    return bp


DAY_SETS = {
    "weekdays": frozenset({0, 1, 2, 3, 4}),
    "daily": frozenset(range(7)),
    "weekends": frozenset({5, 6}),
}


def _schedule_from_form(routine: Routine, wall: str, days_key: str) -> Optional[Schedule]:
    """Build a Schedule from form input, or None when the form asked for no change.

    Invalid input returns None rather than a guess: a routine that reads a mailbox is not the
    place to be generous with a half-parsed time.
    """
    hour, minute = routine.schedule.hour, routine.schedule.minute
    if wall:
        try:
            parts = wall.split(":")
            hour, minute = int(parts[0]), int(parts[1])
        except (ValueError, IndexError):
            return None
    days = DAY_SETS.get(days_key, routine.schedule.days)
    try:
        candidate = Schedule(hour, minute, days, routine.schedule.tz)
    except ValueError:
        return None
    return None if candidate == routine.schedule else candidate
