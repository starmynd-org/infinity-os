"""The door onto a project row. Bus row 0442, child of 0429, migration 44.

Migration 44 made a project a ROW instead of a text prefix, and it built the whole rule set: the
slug regex, the operator's four states, the reason a resting project must carry, the stamps the
database writes rather than trusts, and the asymmetry that lets anybody stop a project and only a
human start one again. What it did not build is a WRITER. Measured on 2026-08-29, before this
file existed: no `swarm project` subcommand, no console form, `web/bin/seed-demo.py` creates none,
and the only way a project row came into existence was a hand-written INSERT at a psql prompt.

So this file is a DOOR AND NOT A NEW RULE. Every refusal it raises is a refusal the table already
makes; what the sentence buys is that an operator meets English instead of SQLSTATE 23514, and
`engine/tests/test_project_entity.py` keeps watching the raw credentials so the TABLE stays the
thing that actually holds. Two gates, neither trusting the other, exactly as migration 44 says of
its own trigger and grants.

WHICH DIRECTION NEEDS WHAT, which is migration 35's asymmetry one level up and not this file's
invention:

    project add                 nothing. A project that did not exist a moment ago throttled
                                nothing, so its birth cannot RELEASE anything.
    project hold / ice /        nothing, and a reason. A kill switch that can be refused is not
    project blocked             a kill switch.
    project resume              a HUMAN LOGIN and a reason. This is the direction that spends
                                money: since task 0430 the claim path excludes every project
                                whose state is not `in_progress`, so a resume is the statement
                                that agents may take work on this project again.
    project rm                  a human login, always -- `brain_runtime` holds no DELETE on
                                `brain.project` at all -- and the trigger refuses a RESTING
                                project a second time, because delete-and-recreate is a resume
                                with no record of the resume.
    project archive             nothing, and a reason. Migration 47, bus row 0443, decision D-05.
                                It takes a FINISHED project off the board, and it withholds that
                                project's work from the claim path exactly as a hold does, so it
                                is the direction that stops spending. It is NOT a fifth state:
                                the operator's four words are untouched and a project archived
                                while `blocked` is still blocked.
    project unarchive           a HUMAN LOGIN. It puts the work back in front of the claim path,
                                which is the same direction `resume` is, so it takes the same
                                gate. It restores no state, because the archive never took one.

THERE IS NO `project rename`, AND THAT IS A DECISION WITH FOUR MEASUREMENTS BEHIND IT rather than
an omission. Task 0442's brief handed this row the question. Measured 2026-08-29 on a scratch
database at ledger 45, over the raw owner and operator credentials:

  1. `UPDATE brain.project SET slug = ...` on a project with ZERO work items: accepted, 1 row.
  2. The same UPDATE on a project with ONE work item: refused, `ForeignKeyViolation` on
     `work_item_project_fkey`. Migration 44 leaves the FK at NO ACTION on update deliberately.
     So a plain rename works only on the projects nobody needs to rename.
  3. Copy the row, repoint the work, delete the old, in one transaction: it commits, and it
     destroys the provenance. The copy carried `state_changed_at` across explicitly and the
     INSERT arm of `brain.project_state_is_a_control()` overwrote it with `now()` anyway --
     measured 88ms apart on the new row. "When did this go on hold" becomes unanswerable, which
     is precisely what `test_a_title_edit_does_not_move_the_state_stamp` exists to protect.
  4. `ON UPDATE CASCADE` is not the escape either. Repointing `brain.work_item.project` alone on
     a row that also carries a `canonical_task` is refused by
     `work_item_project_matches_canonical`. A cascade updates `project` and cannot touch
     `canonical_task`, so it would fail on exactly the rows that name a project twice.

A subcommand that always refused would be `web/MUST-NOT-BUILD.md` item 2's disabled affordance, so
there is no `rename` action at all. `project retitle` IS offered: the trigger's own UPDATE arm
returns early when the state does not move, so a title edit leaves the stamp alone. Renaming a
slug stays a considered manual act, and the four measurements above are what somebody doing it by
hand needs to know.

Run: nothing here is a scheduler, a reader loop, or a second opinion about state. Every write is
`store.apply(...)` and this module owns none of the connections.
"""

from __future__ import annotations

import re

import store
from store import schema

from . import transitions as engine
from .transitions import VerbError

#: The probe `store.schema` uses everywhere else. `brain.project` is migration 44; a store below
#: it holds no projects at all, and every verb here refuses in a sentence rather than falling back
#: to something that looks like success. Doctrine rule 5: these are surfaces that did not exist
#: before 44 and have nothing honest to degrade to.
PROJECT_TABLE = ("project", "slug")

#: The operator's own four words from the 2026-08-28 walkthrough, stored with the underscore
#: migration 34's slug rule requires. `in_progress` is not in this tuple because it is the state a
#: project RETURNS to, through a different verb with a different gate.
REST_STATES = ("hold", "ice", "blocked")

#: Duplicated from migration 44's CHECK on purpose, so the caller gets a sentence naming the rule
#: before the database gets a chance to name the constraint. The CHECK is what actually holds.
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")

BELOW_44 = (
    "this store has no brain.project (migration 44, migrations/0044_a_project_is_a_row.sql), so "
    "it holds no projects and there is nothing here to create, hold or resume. The operator "
    "applies it with: psql -d <db> -f migrations/0044_a_project_is_a_row.sql"
)


def _have_projects(ctx=None) -> bool:
    return schema.has_column(*PROJECT_TABLE, ctx=ctx)


def _require_projects(ctx=None) -> None:
    if not _have_projects(ctx):
        raise VerbError(BELOW_44, code=2)


def _project(ctx, slug: str) -> dict:
    row = ctx.one("SELECT * FROM brain.project WHERE slug = %s", (str(slug),))
    if not row:
        raise VerbError(f"no project named {slug!r}. `swarm project list` prints every one, "
                        f"resting included.")
    return row


def _check_slug(slug: str) -> str:
    """Migration 44's regex, asked here for the sentence and again by the CHECK for the guarantee.

    The regex excludes `#` by construction, and that is the load-bearing part rather than tidiness:
    the slug is the string before the `#` in `brain.work_item.canonical_task`, so a slug that could
    contain one would swallow the separator the whole column is built on.
    """
    s = str(slug or "").strip()
    if not s:
        raise VerbError("a project needs a slug. It is the primary key, the string before the "
                        "'#' in a canonical_task, and the thing after /sprint/ in the console "
                        "URL, so there is no default that could be right.")
    if not SLUG_RE.match(s):
        raise VerbError(
            f"{s!r} is not a project slug. Lower case letters, digits and hyphens, starting with "
            f"a letter or digit, 63 characters at most: ^[a-z0-9][a-z0-9-]{{0,62}}$. Same shape "
            f"as a routine name (migration 34), so `swarm project hold {s.lower()}` never needs "
            f"quoting, and it excludes '#' because the slug is the half of a canonical_task in "
            f"front of one.")
    return s


def _check_reason(reason: str, what: str) -> str:
    r = str(reason or "").strip()
    if not r:
        raise VerbError(
            f"{what} needs a reason (--reason). Both directions are consequential and both are "
            f"decisions somebody has to be able to read six weeks later: `project_rest_has_a_"
            f"reason` requires one on the way out of in_progress, and the trigger requires the "
            f"matching one on the way back in, so the row always says why it is where it is.")
    return r


# ------------------------------------------------------------------ birth
#
# UNGATED, and that is migration 44's rule rather than a convenience. A project that did not exist
# a moment ago was throttling nothing, so its birth cannot RELEASE anything; it can only add a
# place for a throttle to live. `brain_runtime` holds INSERT for exactly this reason, which is
# what lets an agent that has been told to work on a project file it as one.


@store.transition("project add")
def project_add(ctx, *, slug, title, state="in_progress", reason="", by=""):
    """Create a project. The verb this whole task exists for.

    `state_changed_by` and `created_by` ARE NOT PARAMETERS AND MUST NOT BECOME ONE.
    `brain.project_state_is_a_control()` overwrites both with the database's own answer about the
    connection, and migration 36's measured lesson is why: six forged acceptor names including
    `zzz-not-a-person` were written from `brain_runtime` before `accepted_by` stopped being a
    string the caller picks. `by` below is a courtesy for the CLI's output line and is never
    written to the row -- the row carries what the database says, and this function reads it back
    rather than reporting what it sent.

    BORN RESTING IS LEGAL and needs a reason, which `project_rest_has_a_reason` requires
    independently. It is the honest shape for "set this up, do not start on it yet".
    """
    _require_projects(ctx)
    slug = _check_slug(slug)
    t = str(title or "").strip()
    if not t:
        raise VerbError("a project needs a title (--title). `brain.project.title` is NOT NULL and "
                        "non-blank, and a board column of bare slugs is a board nobody reads.")
    st = str(state or "in_progress").strip()
    if st != "in_progress" and st not in REST_STATES:
        raise VerbError(f"{st!r} is not a project state. The operator's four words, stored: "
                        f"in_progress, {', '.join(REST_STATES)}. There is no fifth and no "
                        f"terminal one -- he named four, and a `done` or `archived` nobody asked "
                        f"for would be a vocabulary this schema invented.")
    r = _check_reason(reason, f"a project born {st!r}") if st != "in_progress" else ""

    if ctx.one("SELECT 1 FROM brain.project WHERE slug = %s", (slug,)):
        # Read first for the sentence; the PRIMARY KEY refuses it again and is the guarantee. A
        # bare 23505 here would read as a defect rather than as "that name is taken".
        raise VerbError(f"a project named {slug!r} already exists. `swarm project show {slug}` "
                        f"prints it. The slug is the primary key: it is the same string that goes "
                        f"before the '#' in a canonical_task and after /sprint/ in a console URL, "
                        f"so two projects cannot share one.", code=3)

    row = ctx.one(
        "INSERT INTO brain.project (slug, title, state, state_reason) "
        "VALUES (%s, %s, %s, %s) RETURNING *", (slug, t, st, r))
    return {"slug": row["slug"], "title": row["title"], "state": row["state"],
            "state_reason": row["state_reason"], "created_by": row["created_by"],
            "state_changed_by": row["state_changed_by"], "created_at": row["created_at"],
            "asked_for_by": str(by or "").strip()}


# ------------------------------------------------------------------ the kill switch


@store.transition("project rest")
def project_rest(ctx, *, slug, state, reason, by="", as_operator=False):
    """Bring a project to rest: hold, ice or blocked. THE ROW WITH THE BILL BEHIND IT.

    `as_operator` SELECTS A CREDENTIAL FOR THE RECORD AND IS NOT A GATE, and the default is False
    precisely so that it cannot become one. `store.transitions._login_for` reads it out of the
    kwargs `store.apply` was CALLED with; a caller that passes nothing connects as `brain_runtime`
    and this verb still succeeds, because stopping must never need permission. What it buys is
    truthfulness at the other end: measured 2026-08-29 by lane B2, the console pressed this verb
    while holding the operator credential and `brain.project_state_is_a_control` stamped
    `state_changed_by = 'brain_runtime'`, so the operator's own board recorded his hold as the
    fleet's. The whole question anybody asks about a held project is who held it and why, and the
    row was answering the first half wrong. `web/board.py` passes True; an agent passes nothing
    and is refused nothing.

    NOT GATED ON A LOGIN, on purpose. The operator's words on 2026-08-28 are the requirement:
    "there's certain projects where I kind of just wanted the AI to take a break with it. And then
    I realized later that it worked on it for like eight hours with eight terminals and I ran out
    of API tokens very quickly." Refusing this direction would make him ask an agent's permission
    to stop the agent, and a kill switch that can be refused is not a kill switch.

    MOVING BETWEEN REST STATES IS THE SAME ACT and takes the same door: hold -> ice is still a
    project at rest, so it needs nothing either.

    WHAT THIS ACTUALLY DOES depends on a sibling row and this verb does not pretend otherwise. The
    claim path excludes work on a project whose state is not `in_progress` only where task 0430's
    `_PROJECT_BUDGET_CTE` and `budget/schema/0045_project_is_a_hold_scope.sql` are both present.
    `swarm project show` reports whether they are, rather than this verb printing a promise it
    cannot keep.
    """
    _require_projects(ctx)
    st = str(state or "").strip()
    if st not in REST_STATES:
        raise VerbError(f"{st!r} is not a resting state. One of: {', '.join(REST_STATES)}. "
                        f"Returning a project to in_progress is `swarm project resume`, which "
                        f"needs a human login and is the direction that spends money.")
    r = _check_reason(reason, f"moving a project to {st!r}")
    p = _project(ctx, slug)
    if p["state"] == st:
        # Reported, not raised. The caller wanted this project at rest in this state and it is:
        # refusing here would be a kill switch that failed because it was already thrown. The
        # reason is NOT overwritten, because the first one is the record of when it started.
        return {"slug": p["slug"], "state": st, "already": True,
                "state_reason": p["state_reason"], "state_changed_at": p["state_changed_at"],
                "state_changed_by": p["state_changed_by"], "open_items": _open_items(ctx, p["slug"])}
    row = ctx.one("UPDATE brain.project SET state = %s, state_reason = %s WHERE slug = %s "
                  "RETURNING *", (st, r, p["slug"]))
    return {"slug": row["slug"], "state": row["state"], "already": False, "was": p["state"],
            "state_reason": row["state_reason"], "state_changed_at": row["state_changed_at"],
            "state_changed_by": row["state_changed_by"],
            "open_items": _open_items(ctx, row["slug"])}


def _open_items(ctx, slug: str) -> int:
    """How much work is under this project and not finished. THE DENOMINATOR OF THE HOLD.

    "This project is on hold" is a state. "And 14 items are still open under it" is the
    measurement that says whether the hold did anything, and it is the number migration 44 built
    `brain.project_open_work` to carry. Printed on every state change for that reason: a hold over
    zero items is a hold that stopped nothing, and the operator should read that from the verb
    rather than discover it later.
    """
    return int(ctx.scalar(
        "SELECT count(*) FROM brain.work_item WHERE project = %s "
        "AND state IN ('inbox', 'active', 'blocked')", (str(slug),)) or 0)


# ------------------------------------------------------------------ the permissive direction


@store.transition("project resume")
def project_resume(ctx, *, slug, reason, by="", as_operator=True):
    """Return a project to in_progress. A HUMAN ACT, gated on a database login, twice.

    `as_operator=True` IS A DEFAULT THAT DOES NOTHING ON ITS OWN, exactly as `routine add`
    documents: `store/transitions.py::_login_for` reads it out of the kwargs `store.apply` was
    CALLED with, not out of this signature. A caller that forgets connects as `brain_runtime`,
    gets NULL from `brain.current_human()`, and is refused below by a sentence that names what to
    pass -- and refused a second time by `brain.project_state_is_a_control()` if it somehow
    reached the UPDATE. Fail closed.

    THIS IS THE STATEMENT THAT SPENDS MONEY. Since task 0430 the claim path excludes every task
    whose project is not `in_progress`, so this verb is what puts agents back on the work, and it
    is the direction the eight-terminal bill came from. An agent that can lift its own project's
    hold is not being held.
    """
    _require_projects(ctx)
    r = _check_reason(reason, "returning a project to in_progress")
    the_human = ctx.scalar("SELECT brain.current_human()")
    if not the_human:
        raise VerbError(
            "refusing to return a project to in_progress from a connection the database does not "
            "know as a human. Putting a project back in progress is the statement that agents may "
            "spend on it again, and it is the direction the eight-terminal bill came from; "
            "STOPPING it needs no login at all. Call this as the operator (`as_operator=True`, "
            "which opens the operator login). brain.current_human() returned NULL for this "
            "session, which is what it returns for brain_runtime, the login every agent surface "
            "in this fleet connects as. On a host where store/bin/provision-operator.sh has never "
            "run it returns NULL for brain_owner too, and the break-glass is to provision the "
            "credential rather than to add a second notion of who is human.", code=6)
    p = _project(ctx, slug)
    if p["state"] == "in_progress":
        return {"slug": p["slug"], "state": "in_progress", "already": True,
                "state_reason": p["state_reason"], "state_changed_at": p["state_changed_at"],
                "state_changed_by": p["state_changed_by"],
                "open_items": _open_items(ctx, p["slug"])}
    row = ctx.one("UPDATE brain.project SET state = 'in_progress', state_reason = %s "
                  "WHERE slug = %s RETURNING *", (r, p["slug"]))
    return {"slug": row["slug"], "state": row["state"], "already": False, "was": p["state"],
            "state_reason": row["state_reason"], "state_changed_at": row["state_changed_at"],
            "state_changed_by": row["state_changed_by"], "resumed_by": by or the_human,
            "open_items": _open_items(ctx, row["slug"])}


# ------------------------------------------------------------------ leaving the board
#
# MIGRATION 47, bus row 0443, decision D-05. The operator named four states and none of them is
# terminal, so a project entered on the board stayed on it forever and after a year `in progress`
# is every project that was ever started. He was asked (q0460) and the answer was an ARCHIVED
# STAMP rather than a fifth word, because `in progress / hold / ice / blocked` are his own four.
#
# THE STAMP IS NOT MERELY AN ABSENCE FROM THE BOARD. `_PROJECT_BUDGET_CTE` withholds an archived
# project's work from the claim path exactly as it withholds a resting one's; without that half an
# archived project keeps `state = 'in_progress'` and agents keep spending on it, which is the
# eight-terminals incident in a new place. `swarm project list` and `swarm project show` report
# whether this store carries the column at all rather than assuming it.
#
# ARCHIVING IS NOT CANCELLING and nothing here may drift into it. The row survives, the work
# survives, and `swarm project show <slug>` still prints both. Rows 0399 and 0403 were lost on this
# operation by tidying a board while their defects were still on the operator's screen.


ARCHIVE_COLUMN = ("project", "archived_at")

BELOW_47 = (
    "this store has no brain.project.archived_at (migration 47, "
    "migrations/0047_a_project_that_finished_leaves_the_board.sql), so a project has nowhere to "
    "go when it finishes and every project ever started stays on the board. The operator applies "
    "it with: psql -d <db> -f migrations/0047_a_project_that_finished_leaves_the_board.sql"
)


def _have_archive(ctx=None) -> bool:
    return schema.has_column(*ARCHIVE_COLUMN, ctx=ctx)


@store.transition("project archive")
def project_archive(ctx, *, slug, reason, by=""):
    """Take a finished project off the board. UNGATED, on migration 35's asymmetry.

    It needs no login for the same reason `project rest` does not: archiving WITHHOLDS work from
    the claim path, so it is the direction that stops spending, and a switch that stops spending
    must never need permission. `swarm project unarchive` is the other direction and does need a
    human.

    `archived_at` and `archived_by` ARE NOT PARAMETERS AND MUST NOT BECOME ONE. The trigger
    overwrites both with the database's own answer about the connection, on migration 36's
    measured rule: six forged acceptor names including `zzz-not-a-person` were written from
    `brain_runtime` before `accepted_by` stopped being a string the caller picks.
    """
    _require_projects(ctx)
    if not _have_archive(ctx):
        raise VerbError(BELOW_47, code=2)
    r = _check_reason(reason, "archiving a project")
    p = _project(ctx, slug)
    if p.get("archived_at"):
        return {"slug": p["slug"], "already": True, "archived_at": p["archived_at"],
                "archived_by": p["archived_by"], "archived_reason": p["archived_reason"],
                "state": p["state"], "open_items": _open_items(ctx, p["slug"])}
    row = ctx.one("UPDATE brain.project SET archived_at = now(), archived_by = %s, "
                  "archived_reason = %s WHERE slug = %s RETURNING *",
                  (str(by or "").strip() or "unset", r, p["slug"]))
    return {"slug": row["slug"], "already": False, "archived_at": row["archived_at"],
            "archived_by": row["archived_by"], "archived_reason": row["archived_reason"],
            "state": row["state"], "open_items": _open_items(ctx, row["slug"])}


@store.transition("project unarchive")
def project_unarchive(ctx, *, slug, by="", as_operator=True):
    """Put a project back on the board. A HUMAN ACT, gated twice and neither gate trusts the other.

    THIS IS THE STATEMENT THAT SPENDS MONEY, the same shape `project resume` is: un-archiving puts
    this project's work back in front of the claim path. `as_operator=True` opens the operator
    login in `store.transitions._login_for` and the trigger refuses the UPDATE a second time if it
    somehow arrives without one.

    IT DOES NOT RESTORE A STATE. The archive is orthogonal to the operator's four words, so a
    project archived while `blocked` comes back `blocked`, which is the point of the stamp being a
    separate fact rather than a fifth state.
    """
    _require_projects(ctx)
    if not _have_archive(ctx):
        raise VerbError(BELOW_47, code=2)
    the_human = ctx.scalar("SELECT brain.current_human()")
    if not the_human:
        raise VerbError(
            "refusing to put an archived project back on the board from a connection the "
            "database does not know as a human. Un-archiving puts its work back in the claim "
            "path, so it is the direction that spends money; ARCHIVING needs no login at all. "
            "Call this as the operator (`as_operator=True`). brain.current_human() returned NULL "
            "for this session, which is what it returns for brain_runtime, the login every agent "
            "surface in this fleet connects as.", code=6)
    p = _project(ctx, slug)
    if not p.get("archived_at"):
        return {"slug": p["slug"], "already": True, "state": p["state"],
                "open_items": _open_items(ctx, p["slug"])}
    row = ctx.one("UPDATE brain.project SET archived_at = NULL, archived_by = NULL, "
                  "archived_reason = NULL WHERE slug = %s RETURNING *", (p["slug"],))
    return {"slug": row["slug"], "already": False, "state": row["state"],
            "was_archived_at": p["archived_at"], "was_archived_by": p["archived_by"],
            "was_archived_reason": p["archived_reason"], "by": by or the_human,
            "open_items": _open_items(ctx, row["slug"])}


# ------------------------------------------------------------------ the ordinary edit


@store.transition("project retitle")
def project_retitle(ctx, *, slug, title):
    """Change the display title. NOT a rename: the slug is the primary key and does not move.

    Ungated and stamp-preserving, and both are the table's doing rather than this verb's. The
    UPDATE arm of `brain.project_state_is_a_control()` returns early when the state does not move,
    so "when did this go on hold" stays answerable after somebody fixes a typo. See this module's
    docstring for why there is no slug rename and what a manual one has to cope with.
    """
    _require_projects(ctx)
    t = str(title or "").strip()
    if not t:
        raise VerbError("a project needs a title. `brain.project.title` is NOT NULL and "
                        "non-blank; to remove a project entirely use `swarm project rm`.")
    p = _project(ctx, slug)
    row = ctx.one("UPDATE brain.project SET title = %s WHERE slug = %s RETURNING *", (t, p["slug"]))
    return {"slug": row["slug"], "title": row["title"], "was": p["title"],
            "state": row["state"], "state_changed_at": row["state_changed_at"]}


# ------------------------------------------------------------------ removal


@store.transition("project rm")
def project_rm(ctx, *, slug, by="", as_operator=True):
    """Delete a project. A HUMAN ACT for two independent reasons, and neither trusts the other.

    THE GRANT IS THE FIRST GATE and it covers every project, resting or not: migration 44 gives
    `brain_runtime` SELECT, INSERT and UPDATE and NO DELETE at all, so an agent process cannot
    reach the trigger. THE TRIGGER IS THE SECOND and it covers the resting ones: deleting a
    held project and creating it again is a resume with no record of the resume, which is the
    same escape by a different door.

    A PROJECT WITH WORK FILED AGAINST IT IS REFUSED BY THE FOREIGN KEY, not by this verb, because
    NO ACTION is what stops the work being orphaned. The count is read first so the operator gets
    a sentence with a number in it instead of SQLSTATE 23503.
    """
    _require_projects(ctx)
    the_human = ctx.scalar("SELECT brain.current_human()")
    p = _project(ctx, slug)
    if not the_human and p["state"] != "in_progress":
        raise VerbError(
            f"refusing to delete project {p['slug']}: it is {p['state']} and this connection is "
            f"not a human login. Deleting a resting project and creating it again is a resume "
            f"that leaves no record of the resume. Return it to in_progress first with `swarm "
            f"project resume {p['slug']} --reason '...'`, from a login brain.current_human() "
            f"recognises, and delete it after.", code=6)
    n = int(ctx.scalar("SELECT count(*) FROM brain.work_item WHERE project = %s",
                       (p["slug"],)) or 0)
    if n:
        raise VerbError(
            f"refusing to delete project {p['slug']}: {n} work item(s) are filed against it and "
            f"brain.work_item.project is a foreign key with NO ACTION on delete, so the database "
            f"would refuse this too. Unpoint them first. `swarm project show {p['slug']}` lists "
            f"them.", code=3)
    ctx.execute("DELETE FROM brain.project WHERE slug = %s", (p["slug"],))
    return {"slug": p["slug"], "title": p["title"], "was": p["state"],
            "deleted_by": by or the_human or "", "work_items": n}


# ------------------------------------------------------------------ the join


@store.transition("project attach")
def project_attach(ctx, *, id, slug, agent=""):
    """File one existing work item into a project. The other half of the door.

    WITHOUT THIS THERE IS NO PATH FROM AN EXISTING TASK TO A PROJECT, and a project that holds no
    work throttles nothing however carefully its state is set: `_PROJECT_NOT_BUDGET_STOPPED` (task
    0430) anti-joins on `brain.work_item.project`, and every row in this store carries NULL there.
    `swarm post --project` covers work posted after the project exists; this covers the 345 rows
    that already did.

    IT WAS ALSO ONE MOUTH OF THE ESCAPE HATCH MIGRATION 44 LEFT OPEN, and MIGRATION 46 CLOSED IT
    (bus row 0444, 2026-08-29). `brain.work_item.project` is writable by `brain_runtime` because
    `post` is an agent verb, so an agent could move a row OFF a held project and out from under
    the throttle in one UPDATE. `brain.work_item_does_not_leave_a_resting_project` now refuses
    that for any connection `brain.current_human()` does not recognise.

    SO THIS FUNCTION ASKS FIRST AND THE DATABASE STILL HOLDS. The sentence below is the door's
    job: an operator meets English rather than SQLSTATE P0001 out of a trigger he has never heard
    of. The GUARANTEE is the trigger, over every path including psql, and neither gate trusts the
    other -- the same shape migration 44 uses for its own grants. On a store below ledger 46 the
    refusal here still stands and the trigger is simply absent, which is the one case where this
    verb is stricter than the store; it says so rather than pretending the store enforces it.

    MOVING WORK ONTO A PROJECT IS STILL UNGATED, resting or not. Filing work into a hold can only
    add to it, never escape it, which is why this verb needs no login at all in that direction.

    THE COHERENCE CHECK IS ASKED FIRST, FOR THE SENTENCE. `work_item_project_matches_canonical`
    refuses a row whose `canonical_task` prefix names a different project. That arrives as a bare
    23514, which `cli.die_db` correctly reports as a possible defect, because a raw CHECK
    violation cannot say whether it was a rule or a bug. Here it is a rule and it says so.
    """
    _require_projects(ctx)
    p = _project(ctx, slug)
    w = engine._require(ctx, str(id))
    canonical = (w.get("canonical_task") or "").strip()
    if canonical and "#" in canonical:
        prefix = canonical.split("#", 1)[0]
        if prefix != p["slug"]:
            raise VerbError(
                f"{w['id']} carries canonical_task {canonical!r}, whose project prefix is "
                f"{prefix!r}, and filing it under {p['slug']!r} would make the row give two "
                f"different answers to 'which project is this'. "
                f"work_item_project_matches_canonical refuses that. They are not the same fact -- "
                f"canonical_task is a foreign task id on the git planning ladder -- so fix "
                f"whichever one is wrong: `swarm set {w['id']} canonical_task '<slug>#<id>'`, or "
                f"attach it to {prefix!r} instead.", code=3)
    was = (w.get("project") or "") or None
    if was == p["slug"]:
        return {"id": w["id"], "project": p["slug"], "already": True, "was": was,
                "left_resting": None, "state": p["state"]}
    left_resting = None
    if was:
        prior = ctx.one("SELECT slug, state FROM brain.project WHERE slug = %s", (was,))
        if prior and prior["state"] != "in_progress":
            left_resting = {"slug": prior["slug"], "state": prior["state"]}
            if not ctx.scalar("SELECT brain.current_human()"):
                raise VerbError(
                    f"refusing to move {w['id']} off project {prior['slug']}, which is "
                    f"{prior['state']}: this connection is not a human login. Moving an item out "
                    f"from under a hold lifts the hold FOR THAT ITEM, and that is the direction "
                    f"that spends money -- the operator's own words are that a project he thought "
                    f"was resting ran eight terminals wide and the bill was the notification. "
                    f"Return the project to in_progress first with `swarm project resume "
                    f"{prior['slug']} --reason '...'`, from the operator login, and move the work "
                    f"after. Filing work INTO a project needs nothing at all. The database refuses "
                    f"this too, from every path including psql: migration 46, "
                    f"brain.work_item_does_not_leave_a_resting_project.", code=6)
    ctx.execute("UPDATE brain.work_item SET project = %s WHERE id = %s", (p["slug"], w["id"]))
    ctx.actor = str(agent or "").strip() or ctx.actor
    ctx.thread(w["id"], "note",
               f"filed into project {p['slug']} ({p['state']})"
               + (f", moved off {left_resting['slug']} which is {left_resting['state']}"
                  if left_resting else ""))
    return {"id": w["id"], "project": p["slug"], "already": False, "was": was,
            "left_resting": left_resting, "state": p["state"]}


# ------------------------------------------------------------------ reads


def listing() -> list:
    """EVERY project, resting included, because a resting one is what is being looked for.

    Reads `brain.project_open_work`, which LEFT JOINs, so a project with no work still appears
    with zeroes. A board that dropped empty projects would hide exactly the ones somebody just
    created.

    THE EMPTY LIST IS NOT SILENT BELOW LEDGER 44 (`docs/SCHEMA-TOLERANCE.md`): "the operator has
    no projects" and "this store cannot hold one" are different facts, and a caller who sees only
    an empty list must not read the second as the first.
    """
    if not _have_projects():
        schema.warn_once("projects", BELOW_44)
        return []
    # ARCHIVED PROJECTS SORT TO THE FOOT RATHER THAN VANISHING, and that is migration 47's rule
    # rather than this reader's taste: archiving is not cancelling, so the record survives and a
    # listing that dropped the row would be the tidy-up rows 0399 and 0403 were lost to. The BOARD
    # is what leaves them off the four columns; a list is a record and shows everything.
    archived = "archived_at IS NOT NULL," if _have_archive() else ""
    with store.read("runtime") as s:
        return s.query(
            "SELECT * FROM brain.project_open_work "
            f" ORDER BY {archived} (state = 'in_progress') DESC, state, slug")


def one(slug: str) -> dict | None:
    if not _have_projects():
        schema.warn_once("projects", BELOW_44)
        return None
    with store.read("runtime") as s:
        return s.one("SELECT * FROM brain.project_open_work WHERE slug = %s", (str(slug),))


def items(slug: str, limit: int = 50) -> list:
    if not _have_projects():
        return []
    with store.read("runtime") as s:
        return s.query(
            "SELECT id, title, lane, state, claimed_by FROM brain.work_item WHERE project = %s "
            " ORDER BY (state = 'active') DESC, state, id LIMIT %s", (str(slug), int(limit)))


def throttle_is_wired() -> dict:
    """Does a hold on this store actually STOP anything? Asked of the store, never assumed.

    Migration 44 is the entity and it throttles nothing by itself; task 0430's
    `budget/schema/0045_project_is_a_hold_scope.sql` and `_PROJECT_BUDGET_CTE` are what make the
    claim path read `brain.project.state`. Both can be absent, and on a store where they are, a
    verb that printed "held" would be promising something no code performs -- which is the
    disabled affordance `web/MUST-NOT-BUILD.md` item 2 names, one layer down where nobody can see
    it. So `swarm project` says which of the two it found rather than assuming either.

    Two independent probes, and the source read is deliberate: the CTE is Python in this repo, the
    function is a row in the catalogue of THIS database, and one does not imply the other.
    """
    from pathlib import Path
    src = Path(__file__).resolve().parent / "transitions.py"
    in_claim_path = "_PROJECT_BUDGET_CTE" in src.read_text(encoding="utf-8")
    try:
        with store.read("runtime") as s:
            fn = bool(s.scalar("SELECT to_regprocedure('brain.work_item_project"
                               "(brain.work_item)') IS NOT NULL"))
    except Exception:                                                    # noqa: BLE001
        fn = False
    return {"claim_path": in_claim_path, "schema": fn, "wired": in_claim_path and fn}
