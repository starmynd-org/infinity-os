"""The project board. Bus row 0436, over migration 44's entity and task 0430's claim-path gate.

THIS IS THE ONE OVERRULED PROHIBITION. `web/MUST-NOT-BUILD.md` item 3 -- "Kanban, in any room.
Grouping is read-only trees" -- was overruled by Andrew on 2026-08-28, for a project board only,
against an incident with a bill attached and in his own words:

    "there's certain projects where I kind of just wanted the AI to take a break with it. And then
     I realized later that it worked on it for like eight hours with eight terminals and I ran out
     of API tokens very quickly."

THE CONDITION IS A REQUIREMENT AND NOT A HEDGE, and it is what this module is shaped around:

    A COLUMN IS A PROJECT STATE, AND MOVING BETWEEN COLUMNS CALLS A REGISTERED TRANSITION WHOSE
    ARGUMENT IS THAT STATE. The drop is an affordance on a verb, never a written coordinate.

The prohibition existed to stop an invisible second state machine, and the fear is answered by
construction rather than waived. There is no board table, no board column on any row, and no
position anywhere. A card's column IS `brain.project.state`, read live; a drop IS `project rest`
or `project resume` through `rooms.dispatch` and `store.apply`, the same two verbs the CLI calls;
and the claim path already reads that same column in SQL (`_PROJECT_BUDGET_CTE`, task 0430). Every
surface is looking at one fact. Delete this file and nothing about the system's behaviour changes
except that the operator has to type the verb.

READ-ONLY TREES REMAIN THE RULE IN EVERY OTHER ROOM. The overrule is scoped to this view alone and
nothing here is reusable as a generic board.

WHY THE DRAG IS PERMITTED WHERE `MUST-NOT-BUILD` ITEM 2 FORBIDS DRAG-TO-REORDER. Item 2 is about
ORDERING: no control writes `priority`, nothing is sortable by the operator, and the computed
score stays the machine's. This drag writes no order at all. It writes a state, and it is the same
state change as `swarm project hold <slug> --reason '...'`. Dragging to re-rank anything remains
forbidden, here included: the cards inside a column are ordered by the store's own ORDER BY and
there is no handle to change it.

WHY A REASON IS COLLECTED AT THE DROP RATHER THAN AFTER IT. `project_rest_has_a_reason`
(migration 44) refuses a resting project with no reason, and the trigger requires the matching one
on the way back to `in_progress`. So a drop with no reason cannot succeed, and a board that
discovered that AFTER the card had visually moved would show the operator a state the store does
not hold. The card does not move until the store says it did.

WHAT THIS MODULE DOES NOT DO. It renders no work items as draggable, it has no create form (that
is `swarm project add`, and a project is born rarely enough that a room's worth of form for it
would be a surface before a need), and it has no `rename` -- there is none, for the four
measurements in `engine/swarm_engine/projects.py`'s docstring.
"""

from __future__ import annotations

import store

from . import actions, rooms

# REGISTRATION IS AN IMPORT SIDE EFFECT and this line is not unused. `project rest` and `project
# resume` are declared by @store.transition in `swarm_engine/projects.py`; a console process that
# never imports it would render four columns whose drops raise UnknownTransition, which is the
# disabled affordance this whole feature exists against. `web/app.py` imports the engine's other
# verb modules on the same rule and says so there.
from swarm_engine import projects as _engine_projects                    # noqa: F401

#: The operator's own four words, in his own order, from the 2026-08-28 walkthrough
#: (`outputs/2026-08-27-DEMO/OPERATOR-FEEDBACK.md:678`): "in progress / hold / ice / blocked".
#: The stored value carries an underscore and the label does not; that is migration 44's rule
#: about slugs and the display strings are his.
#:
#: THE STATE COLOUR NEVER RIDES ALONE and each entry carries the word that says it. `hold`, `ice`
#: and `blocked` are all AMBER and not red, and that is a ruling rather than a palette choice:
#: red means damage, and a project the operator deliberately parked is not damage. It is the same
#: argument that recoloured the paused-fleet banner. `blocked` is the arguable one and it is amber
#: too, because on a PROJECT the word means the operator parked it behind something, not that
#: anything broke.
COLUMNS = (
    {"state": "in_progress", "label": "in progress", "tone": "running",
     "verb": "project resume",
     "meaning": "agents may take work here. This is the only column that spends money."},
    {"state": "hold", "label": "hold", "tone": "waiting", "verb": "project rest",
     "meaning": "parked on purpose. The claim path hands out none of its work."},
    {"state": "ice", "label": "ice", "tone": "waiting", "verb": "project rest",
     "meaning": "put down for now. Same throttle as hold; a different thing to say."},
    {"state": "blocked", "label": "blocked", "tone": "waiting", "verb": "project rest",
     "meaning": "waiting on something outside. Same throttle; the reason says what."},
)

#: The console action names the drop posts, mapped to the state they carry. The ACTION is not the
#: verb: `project_hold` and `project_ice` are the same verb with different arguments, which is
#: what "a column is a state" means at this end too.
DROP_ACTIONS = {
    "project_hold": "hold",
    "project_ice": "ice",
    "project_blocked": "blocked",
    "project_resume": "in_progress",
}


class BoardRefusal(actions.ActionRefused):
    """Something the board will not do, said in a sentence rather than reported as a crash.

    A SUBCLASS OF `actions.ActionRefused` AND THAT IS THE WHOLE POINT of the base class here.
    `web/app.py::act` already catches `ActionRefused` and answers 400 with `kind: "refused"` and
    the sentence; a bare Exception falls to the generic arm and answers **500**, so a rule the
    board is enforcing correctly arrives at the operator as a server error. Measured 2026-08-29
    by this lane's own driver: a reasonless move was refused with the right words under HTTP 500,
    and a refusal that reads as a crash teaches people the page is broken. Inheriting fixes it
    with no change to `app.py` at all, which matters because that file is shared with two live
    lanes."""


def wired() -> dict:
    """Does a drop on this store STOP anything, or only record an intention?

    Asked of the store rather than assumed, and reported ON the page. A board that showed four
    columns over a store where the claim path does not read `brain.project.state` would be four
    columns that cannot hold anything, which is `MUST-NOT-BUILD` item 2's own disabled-affordance
    failure arriving through the door item 3's overrule was supposed to close. The two halves are
    named separately because one does not imply the other: the CTE is Python in this repo and the
    function is a row in THIS database's catalogue.
    """
    return _engine_projects.throttle_is_wired()


def present() -> bool:
    """Is there a `brain.project` at all? Below migration 44 there is not, and saying "no
    projects" there would report "this store cannot hold one" as "the operator has none"."""
    with store.read() as s:
        return bool(s.scalar("SELECT to_regclass('brain.project') IS NOT NULL"))


def board() -> dict:
    """The four columns and their cards, read live from `brain.project_open_work`.

    ORDER IS THE STORE'S. Within a column, projects sort by how much open work is under them and
    then by slug, so the column reads as "how much is this costing" rather than as a hand-kept
    ranking. There is no operator-settable order and no handle to make one: `MUST-NOT-BUILD` item
    2 is KEPT and only the state drag is overruled.

    `open_items` is on every card because it is the denominator of the hold. "This project is on
    hold" is a state; "and 14 items are still open under it" is the measurement that says whether
    the hold did anything, and a hold over zero items stopped nothing.
    """
    if not present():
        return {"present": False, "columns": [dict(c, cards=[]) for c in COLUMNS],
                "total": 0, "archived": 0, "has_archive": False,
                "throttle": {"wired": False, "claim_path": False, "schema": False}}
    with store.read() as s:
        rows = [dict(r) for r in s.query("SELECT * FROM brain.project_open_work "
                                         " ORDER BY open_items DESC, slug")]
    # THE ARCHIVE IS AN ABSENCE FROM THE BOARD AND NOT FROM THE RECORD. Migration 47, bus row
    # 0443, decision D-05. A project that finished leaves the four columns so that after a year
    # `in progress` is not every project ever started; the ROW survives with who archived it and
    # why, `swarm project list` still prints it, and the claim path withholds its work whatever
    # any reader does. Archiving is not cancelling, and rows 0399 and 0403 were lost on this
    # operation by tidying a board while their defects were still on his screen.
    #
    # The count is reported rather than swallowed: a board that silently dropped rows would be
    # unable to tell "nothing here" from "seventeen finished projects here".
    has_archive = "archived_at" in (rows[0] if rows else {})
    live = [r for r in rows if not r.get("archived_at")]
    archived = len(rows) - len(live)
    cols = []
    for c in COLUMNS:
        cards = [r for r in live if r["state"] == c["state"]]
        cols.append(dict(c, cards=cards))
    return {"present": True, "columns": cols, "total": len(live), "archived": archived,
            "has_archive": has_archive, "throttle": wired()}


def perform(room: str, action: str, slug: str, reason: str) -> dict:
    """One drop. The whole write path, and it is `rooms.dispatch` and nothing else.

    Reached from `web/app.py::_perform`, which short-circuits to here BEFORE it resolves a queue
    card, for the reason the `steer_*` branch beside it already gives in its own words: those
    verbs act on a run rather than on a row in the operator's queue, so the stale-card guard would
    refuse all of them. A project is the same shape one step further out. It is not a work item at
    all, so `model.find_item` returns None for every slug and every drop would be refused as a
    stale card.

    THE ROOM IS THE ROUTE'S, NOT THE PAYLOAD'S. `guard.guard_write` has already checked a CSRF
    token minted for `board` and bound to this browser before this function is entered, and
    `rooms.assert_room_can_act('board')` has already refused an empty allowlist. Task 0147 is why
    that matters: an allowlist keyed on an attacker-supplied string is an allowlist keyed on
    nothing. This function never reads `request`.
    """
    state = DROP_ACTIONS.get(action)
    if state is None:
        raise BoardRefusal(f"no such board action: {action!r}. One of: "
                           f"{', '.join(sorted(DROP_ACTIONS))}.")
    slug = str(slug or "").strip()
    if not slug:
        raise BoardRefusal("that drop named no project. The card carries the slug, which is "
                           "brain.project's primary key.")
    reason = str(reason or "").strip()
    if not reason:
        # ASKED HERE FOR THE SENTENCE AND ENFORCED BY THE DATABASE ANYWAY. `project_rest_has_a_
        # reason` refuses a resting project with an empty reason and migration 44's trigger
        # refuses a resume with one, so this cannot be the only gate and is not meant to be. What
        # it buys is that the operator reads English at the moment of the drop instead of a
        # constraint name after the card has appeared to move.
        raise BoardRefusal(
            "moving a project between columns needs a reason. Both directions are consequential: "
            "one stops agents spending on it and the other starts them again, and the reason is "
            "the only thing that answers 'why is this here' six weeks later. It is required by "
            "the table (project_rest_has_a_reason) and not by this page.")

    before = one(slug)
    if before is None:
        raise BoardRefusal(f"no project named {slug!r}. The board was read before this drop and "
                           f"the store has moved underneath it; reload rather than acting on a "
                           f"card that is no longer true.")
    if before["state"] == state:
        raise BoardRefusal(
            f"{slug} is already {state.replace('_', ' ')}. Nothing was written. Refusing rather "
            f"than reporting success for a no-op, because a receipt for a change that did not "
            f"happen is how two surfaces come to disagree.")

    if state == "in_progress":
        # THE DIRECTION THAT SPENDS MONEY, and the only one gated on a login. `as_operator=True`
        # selects the operator credential in `store.transitions._login_for`; migration 44's
        # trigger refuses the write a second time if it somehow arrives without one. An agent
        # that can lift its own project's hold is not being held.
        out = rooms.dispatch(room, "project resume", slug=slug, reason=reason,
                             by="operator", as_operator=True)
        note = (f"{out['open_items']} open work item(s) under {slug} are claimable again")
    else:
        # UNGATED, and that is migration 35's asymmetry rather than an oversight: a kill switch
        # that can be refused is not a kill switch, and refusing this direction would make the
        # operator ask an agent's permission to stop the agent.
        out = rooms.dispatch(room, "project rest", slug=slug, state=state, reason=reason,
                             by="operator", as_operator=True)
        note = (f"{out['open_items']} open work item(s) under {slug} are withheld from the "
                f"claim path")
    return {
        "verb": "project resume" if state == "in_progress" else "project rest",
        "slug": slug,
        "was": before["state"],
        "state": out["state"],
        "reason": out.get("state_reason") or reason,
        "open_items": out.get("open_items", 0),
        "note": note,
        "receipt": f"{slug}: {before['state'].replace('_', ' ')} -> "
                   f"{out['state'].replace('_', ' ')}",
    }


def one(slug: str) -> dict | None:
    if not present():
        return None
    with store.read() as s:
        row = s.one("SELECT * FROM brain.project_open_work WHERE slug = %s", (str(slug),))
    return dict(row) if row else None


def items(slug: str, limit: int = 8) -> list:
    """The work under one project, for the card's expansion. Read-only, always.

    A work item is NOT draggable on this board and never becomes so. Moving work between projects
    is `swarm project attach`, it is refused for an agent off a resting project by migration 46,
    and putting it on a drag handle would make the one act that can lift a hold for a single item
    the easiest gesture on the page.
    """
    if not present():
        return []
    with store.read() as s:
        return s.query(
            "SELECT id, title, lane, state, claimed_by FROM brain.work_item WHERE project = %s "
            " ORDER BY (state = 'active') DESC, state, id LIMIT %s", (str(slug), int(limit)))
