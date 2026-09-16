"""Routines: a stored call to `post`, on a clock.

The last buildable half of `PLAN.md:255`'s retirement condition. Budgets landed as migration 3;
routines have been "in no lane" for over a year because the same plan leaves them on systemd
timers, which made the condition unsatisfiable as literally written.

THE READING THAT MAKES IT SATISFIABLE WITHOUT REINTERPRETING IT, and it is the whole design:

  the ROUTINE is an object in the store   so it can be listed, disabled, and shown to have fired
  the CLOCK stays a systemd --user timer  exactly where PLAN.md puts it

Nothing in this file is a scheduler process. There is no loop, no daemon, no sleep and no second
notion of what time it is. `brain.routine_slot(anchor_at, period_minutes, now())` is integer
arithmetic the database performs, so two callers at the same instant compute the same slot, which
is what lets ONE unique index be the whole of the idempotency story.

WHY NOT `brain.queue_defer`, since a routine looks like a defer that re-arms. Full reasoning in
`outputs/2026-08-27-C-0376-routines/OBJECT-DECISION.md`. The short form: a defer is keyed to a
source row that ALREADY EXISTS, is a one shot (`woke_at` closes it), and its third occurrence is
REFUSED on purpose, because "three deferrals means mis-scoped, not mis-timed". A routine is keyed
to nothing, is standing, and is unbounded. No row could be described by both tables, so there is
no fact with two homes.

THE IDEMPOTENCY, WHICH IS THE POINT OF THE ROW ABOVE THIS ONE. Row 0377 fixed `recommend accept`
with `SELECT ... FOR UPDATE`. That instrument is wrong here: `FOR UPDATE` locks a row that
exists, and a routine occurrence does not exist until the first fire creates it, so eight callers
all find nothing for slot k and all proceed. The only instrument that refuses a row that is not
there yet is a UNIQUE INDEX, `routine_run_one_per_slot`. Measured, 8 processes on 8 connections:

    index dropped (control arm)   8 fired, 8 work items, 7 orphans
    index in place                1 fired, 1 work item, 7 refused by Postgres, SQLSTATE 23505

ORDER INSIDE `routine fire`: THE POST FIRST, THE OCCURRENCE ROW SECOND. `store.apply` runs the
whole transition in one transaction, so the loser's 23505 rolls back its post too. The claim is
not "a duplicate post never executes", it is "no transaction that posts a duplicate can COMMIT".
Claim-first is unavailable because `routine_run_fired_has_work` needs the work item id and
Postgres CHECK constraints cannot be deferred. Post-first is also better: when `post` itself
refuses, the slot stays unconsumed and the next tick refuses again OUT LOUD, where claim-first
would consume the slot and fail silently until the next period.

ARMING IS A HUMAN ACT AND DISARMING IS NOT (migration 35). A standing scheduled dispatch is a
persistent foothold, so `routine add` and `routine enable` need a human login, exactly as
`recommend accept` does. `routine disable` needs nothing, because a kill switch that can be
refused is not a kill switch. Both directions are refused a second time by the trigger; two
gates, neither trusting the other.
"""

from __future__ import annotations

from datetime import datetime, timezone

import psycopg2

import store
from store import schema

from . import transitions as engine
from .transitions import VerbError

#: The probe `store.schema` uses everywhere else. `brain.routine` is migration 34; a store below
#: it holds no routines at all, so "there are none due" is exactly true there rather than a
#: degradation. The verbs refuse in a sentence instead, per doctrine rule 5: they are surfaces
#: that did not exist before 34 and have nothing honest to fall back to.
ROUTINE_TABLE = ("routine", "id")

#: What `skip_if_open` considers still outstanding. `done` and `cancelled` are finished; a
#: `blocked` routine task is emphatically NOT finished, which is the case the flag exists for.
OPEN_STATES = ("inbox", "active", "blocked")

BELOW_34 = (
    "this store has no brain.routine (migration 34, migrations/0034_routine.sql), so it holds no "
    "routines and nothing here can be scheduled. The operator applies it with: "
    "psql -d <db> -f migrations/0034_routine.sql"
)


def _have_routines(ctx=None) -> bool:
    return schema.has_column(*ROUTINE_TABLE, ctx=ctx)


def _require_routines(ctx=None) -> None:
    if not _have_routines(ctx):
        raise VerbError(BELOW_34, code=2)


def _routine(ctx, name: str) -> dict:
    row = ctx.one("SELECT * FROM brain.routine WHERE name = %s", (str(name),))
    if not row:
        raise VerbError(f"no routine named {name!r}. `swarm routine list` prints every one, "
                        f"disabled included.")
    return row


def _fleet_paused(ctx) -> bool:
    """The global stop, honoured rather than reimplemented.

    The operator already has one kill switch that covers everything (`swarm pause`). A routine
    layer that ignored it would mean `swarm pause` no longer meant what it says, which is worse
    than having no routines: the value of a global stop is that it is believed.
    """
    return (ctx.scalar("SELECT value FROM brain.runtime_flag WHERE key = 'fleet_paused'")
            or "false") == "true"


def _slot(ctx, r: dict, at: datetime | None = None) -> datetime:
    """The largest scheduled slot at or before `at`, asked of the database.

    Asked of the database and never recomputed in Python, so the verb, the view and any surface
    that previews a routine cannot drift apart on rounding. That agreement is what makes the
    unique index sufficient.
    """
    return ctx.scalar("SELECT brain.routine_slot(%s, %s, coalesce(%s, now()))",
                      (r["anchor_at"], r["period_minutes"], at))


# ------------------------------------------------------------------ definition

@store.transition("routine add")
def routine_add(ctx, *, name, title, lane, period_minutes, anchor_at, body="", workdir="",
                parent="", priority=3, max_attempts=2, agent_claimable=False, external=False,
                canon_touching=False, misfire_grace_minutes=None, skip_if_open=True,
                by="", as_operator=True):
    """Create a routine. A HUMAN ACT, gated on a database login, twice.

    `as_operator=True` IS A DEFAULT THAT DOES NOTHING ON ITS OWN, and every caller has to pass it,
    exactly as `recommend accept` documents: `store/transitions.py::_login_for` reads it out of
    the kwargs `store.apply` was CALLED with, not out of this signature. A caller that forgets
    connects as `brain_runtime`, gets NULL from `brain.current_human()`, and is refused below with
    the sentence that names what to pass. Fail closed: a forgotten keyword loses a routine the
    operator can retry, where the alternative loses the gate.

    THE DISPATCH FIELDS ARE `post`'s ARGUMENTS, STORED. A routine is not a new dispatch
    vocabulary; it is a stored call. Which is why `agent_claimable` with no absolute `workdir` is
    refused HERE, by a CHECK constraint on the table, and not at 03:20 by `post` into a journal
    nobody reads. That refusal is the difference between a routine and a fabricated pointer.

    NO PARENT IS LEGAL AND ITS CONSEQUENCE IS STATED. A routine is a recurrence, not a subtask,
    and forcing one to name a parent work item would create a permanent open row whose only
    content is "I am a folder". With no parent nothing inherits by OR, so the routine declares
    its own hard flags; with a parent, `post` ORs the whole chain on top and can only raise them.
    """
    _require_routines(ctx)
    the_human = ctx.scalar("SELECT brain.current_human()")
    if not the_human:
        raise VerbError(
            "refusing to create a routine from a connection the database does not know as a "
            "human. A routine is a standing scheduled dispatch: it posts work every period, "
            "forever, with no human between the timer and the verb, so arming one is at least as "
            "consequential as accepting a recommendation. Call this as the operator "
            "(`as_operator=True`, which opens the operator login); brain.current_human() returned "
            "NULL for this session, which is what it returns for brain_runtime, the login every "
            "agent surface connects as. DISABLING a routine needs none of this.", code=6)

    period = int(period_minutes)
    if period < 1:
        raise VerbError("period_minutes is the number of minutes between occurrences and must be "
                        "at least 1. Daily is 1440, weekly is 10080, twice a day is 720.")
    # THE WORKDIR GATE, ASKED NOW INSTEAD OF AT 03:20, and asked by CALLING `post`'s own
    # implementation rather than restating its rule. `routine_fleet_work_has_an_absolute_workdir`
    # refuses the same row a second time and independently; what this call buys is the sentence.
    # Without it the operator meets SQLSTATE 23514, which `cli.die_db` correctly reports as a
    # possible defect because a bare CHECK violation cannot say whether it was a rule or a bug.
    engine._check_fleet_workdir(bool(agent_claimable), workdir)

    if parent and not ctx.one("SELECT 1 FROM brain.work_item WHERE id = %s", (str(parent),)):
        # The same refusal `post` carries, made now rather than at 03:20: a parent that does not
        # resolve silently drops the inheritance, which is how a hard flag gets laundered.
        raise VerbError(f"no such parent task: {parent}. A routine's parent is what its posted "
                        f"work inherits its hard flags from, so an unresolvable one would launder "
                        f"the gate every night.")

    row = ctx.one(
        """INSERT INTO brain.routine
             (name, title, lane, body, workdir, parent, priority, max_attempts, agent_claimable,
              external, canon_touching, period_minutes, anchor_at, misfire_grace_minutes,
              skip_if_open, created_by)
           VALUES (%(name)s, %(title)s, %(lane)s, %(body)s, %(workdir)s, %(parent)s, %(priority)s,
                   %(max_attempts)s, %(agent_claimable)s, %(external)s, %(canon_touching)s,
                   %(period_minutes)s, %(anchor_at)s, %(grace)s, %(skip_if_open)s, %(by)s)
           RETURNING *""",
        {"name": str(name), "title": title, "lane": lane, "body": body or "",
         "workdir": workdir or "", "parent": str(parent) or None, "priority": int(priority),
         "max_attempts": int(max_attempts), "agent_claimable": bool(agent_claimable),
         "external": bool(external), "canon_touching": bool(canon_touching),
         "period_minutes": period, "anchor_at": anchor_at,
         "grace": None if misfire_grace_minutes is None else int(misfire_grace_minutes),
         "skip_if_open": bool(skip_if_open), "by": by or the_human})
    return {"id": row["id"], "name": row["name"], "period_minutes": row["period_minutes"],
            "anchor_at": row["anchor_at"], "next_slot": _slot(ctx, row),
            "agent_claimable": row["agent_claimable"], "created_by": row["created_by"]}


# ------------------------------------------------------------------ the kill switch

@store.transition("routine disable")
def routine_disable(ctx, *, name, by="operator", reason=""):
    """THE KILL SWITCH. One act, and it asks nobody's permission.

    NOT GATED ON A LOGIN, on purpose, and this is the asymmetry migration 26 already states for
    `agent_claimable`: taking work back from the fleet is the safe direction, and refusing it
    would make the operator ask an agent's permission to stop the agent's routine. A kill switch
    that can be refused is not a kill switch.

    AN UPDATE AND NEVER A DELETE, for the reason `resume` gives about the PAUSE file:
    `brain_runtime` holds DELETE on nothing at all, and a row that records who turned it off and
    why is a better record than a row that is gone.

    THE REASON IS REQUIRED, by this verb and again by `routine_disabled_is_whole`. "Who turned
    this off and why" is the only question anybody asks about a disabled routine six weeks later,
    and a switch nobody can audit is a switch nobody trusts.
    """
    _require_routines(ctx)
    if not str(reason or "").strip():
        raise VerbError("disabling a routine needs a reason. It is the only question anyone asks "
                        "about a dark routine later, and an unlabelled one gets re-enabled by "
                        "somebody who assumes it was an accident.")
    r = _routine(ctx, name)
    if r["disabled_at"] is not None:
        return {"name": r["name"], "disabled": True, "already": True,
                "disabled_at": r["disabled_at"], "disabled_by": r["disabled_by"],
                "disabled_reason": r["disabled_reason"]}
    row = ctx.one(
        "UPDATE brain.routine SET disabled_at = now(), disabled_by = %s, disabled_reason = %s "
        "WHERE name = %s AND disabled_at IS NULL RETURNING disabled_at, disabled_by",
        (by or "operator", str(reason).strip(), str(name)))
    if not row:
        # Somebody else disabled it between the read and the write. That is the outcome this
        # verb wanted, so it is reported and not raised: refusing here would be a kill switch
        # that failed because it was already thrown.
        r = _routine(ctx, name)
        return {"name": r["name"], "disabled": True, "already": True,
                "disabled_at": r["disabled_at"], "disabled_by": r["disabled_by"],
                "disabled_reason": r["disabled_reason"]}
    return {"name": str(name), "disabled": True, "already": False,
            "disabled_at": row["disabled_at"], "disabled_by": row["disabled_by"],
            "disabled_reason": str(reason).strip()}


@store.transition("routine enable")
def routine_enable(ctx, *, name, by="", as_operator=True):
    """Re-arm a disabled routine. The permissive direction, so it is gated like `routine add`."""
    _require_routines(ctx)
    the_human = ctx.scalar("SELECT brain.current_human()")
    if not the_human:
        raise VerbError(
            "refusing to re-arm a routine from a connection the database does not know as a "
            "human. Disabling is free and re-arming is not: a routine an operator switched off "
            "must not be switched back on by the thing it was switched off because of. Call this "
            "as the operator (`as_operator=True`).", code=6)
    r = _routine(ctx, name)
    if r["disabled_at"] is None:
        return {"name": r["name"], "disabled": False, "already": True}
    ctx.execute("UPDATE brain.routine SET disabled_at = NULL, disabled_by = NULL, "
                "disabled_reason = NULL WHERE name = %s", (str(name),))
    return {"name": r["name"], "disabled": False, "already": False,
            "re_armed_by": by or the_human, "was_disabled_for": r["disabled_reason"],
            "next_slot": _slot(ctx, r)}


# ------------------------------------------------------------------ the fire

@store.transition("routine fire")
def routine_fire(ctx, *, name, scheduled_for=None, by="routine", planners=()):
    """Decide ONE slot of ONE routine. The only path from a routine to a work item.

    Every outcome writes a `brain.routine_run` row, including the ones that post nothing, so the
    slot is consumed and the audit shows what was decided rather than leaving a gap a reader has
    to interpret:

      fired    a work item exists and `work_item_id` names it
      skipped  `skip_if_open` and the routine's previous work item is still open
      missed   the slot was older than `misfire_grace_minutes` when the tick arrived

    THE DOUBLE FIRE IS REFUSED BY THE DATABASE, NOT BY THE CHECK BELOW. The check below produces
    the sentence an operator reads instead of a raw SQLSTATE; `routine_run_one_per_slot` is what
    holds when two callers arrive at once, because the check has nothing to hold between its read
    and its write and the index entry is taken at INSERT.

    THE POST COMES FIRST AND THE OCCURRENCE ROW SECOND. One transaction, so the loser's 23505
    rolls back its post as well: no transaction that posts a duplicate can commit. See the module
    docstring for why not the other order.
    """
    _require_routines(ctx)
    r = _routine(ctx, name)

    # GATE 2 OF THE KILL SWITCH. `brain.routine_due` already excludes a disabled routine, so the
    # tick can never bring one here; this is what holds for a caller naming the routine directly.
    if r["disabled_at"] is not None:
        raise VerbError(
            f"routine {r['name']} was disabled at {r['disabled_at']} by {r['disabled_by']}: "
            f"{r['disabled_reason']}. Re-arm it with `swarm routine enable {r['name']}` if that "
            f"reason no longer holds.", code=3)

    # THE GLOBAL STOP, HONOURED. `swarm pause` is the operator's one switch over everything, and a
    # routine layer that ignored it would quietly make that switch mean less than it says.
    if _fleet_paused(ctx):
        raise VerbError(
            "the fleet is paused, so routines dispatch nothing. `swarm resume` lifts it. This is "
            "deliberately not a per-routine question: a global stop that a scheduler ignores is "
            "worse than no global stop, because it is believed.", code=3)

    slot = _slot(ctx, r, scheduled_for)
    if scheduled_for is not None and slot != scheduled_for:
        raise VerbError(
            f"{scheduled_for} is not a slot of routine {r['name']}: with anchor {r['anchor_at']} "
            f"and period {r['period_minutes']} minutes the slot containing it is {slot}. Firing a "
            f"time that is not a slot would put a row in routine_run that no later tick can ever "
            f"match, so the routine would fire twice for that period.")
    if slot < r["anchor_at"]:
        raise VerbError(
            f"routine {r['name']} is anchored at {r['anchor_at']} and has not reached its first "
            f"slot yet. A routine cannot fire before it exists.", code=3)

    # The polite-caller check. Not the guard: see the docstring.
    prior = ctx.one("SELECT * FROM brain.routine_run WHERE routine_id = %s AND scheduled_for = %s",
                    (r["id"], slot))
    if prior:
        raise VerbError(
            f"routine {r['name']} already decided the slot {slot} at {prior['fired_at']}: "
            f"{prior['outcome']}"
            + (f" ({prior['work_item_id']})" if prior["work_item_id"] else "")
            + ". One slot, one decision. The next slot is "
            + f"{slot + _interval(ctx, r['period_minutes'])}.", code=3)

    grace = r["misfire_grace_minutes"] if r["misfire_grace_minutes"] is not None \
        else r["period_minutes"]
    late = ctx.scalar("SELECT (now() - %s) > make_interval(mins => %s)", (slot, int(grace)))
    if late:
        return _record(ctx, r, slot, "missed", by,
                       f"the tick reached this slot more than {grace} minutes late, so it was "
                       f"recorded rather than fired. Backfilling would post work for a period "
                       f"that has already gone.")

    if r["skip_if_open"]:
        still_open = ctx.one(
            """SELECT w.id, w.state
                 FROM brain.routine_run rr JOIN brain.work_item w ON w.id = rr.work_item_id
                WHERE rr.routine_id = %s AND w.state = ANY(%s)
                ORDER BY rr.fired_at DESC LIMIT 1""",
            (r["id"], list(OPEN_STATES)))
        if still_open:
            return _record(ctx, r, slot, "skipped", by,
                           f"{still_open['id']} from a previous occurrence is still "
                           f"{still_open['state']}, and skip_if_open is set. A routine claims "
                           f"there is ONE of these outstanding, not thirty.")

    # THE DISPATCH. `swarm_engine.transitions.post` called as a FUNCTION with this transition's own
    # ctx: the composition the narrow waist allows, and never a copy of its body. So hard flags
    # inherit by OR from `parent`, the claimable rule holds, the brief lands in its write-once
    # column and the thread event is written, all by the one implementation.
    posted = engine.post(
        ctx,
        title=r["title"],
        lane=r["lane"],
        body=(r["body"] or "") + _provenance(r, slot),
        workdir=r["workdir"] or "",
        priority=int(r["priority"]),
        max_attempts=int(r["max_attempts"]),
        parent=r["parent"] or "",
        external="true" if r["external"] else "",
        canon_touching="true" if r["canon_touching"] else "",
        agent_claimable=bool(r["agent_claimable"]),
        posted_by=f"routine:{r['name']}")

    out = _record(ctx, r, slot, "fired", by, "", work_item_id=posted["id"])
    ctx.actor = f"routine:{r['name']}"
    ctx.thread(posted["id"], "note",
               f"posted by routine {r['name']} for the slot {slot} (every "
               f"{r['period_minutes']} minutes from {r['anchor_at']}). Disable it with "
               f"`swarm routine disable {r['name']} --reason ...`, which is one act and needs no "
               f"credential.")
    out["gated"] = posted["gated"]
    out["agent_claimable"] = posted["agent_claimable"]
    return out


def _interval(ctx, minutes: int):
    return ctx.scalar("SELECT make_interval(mins => %s)", (int(minutes),))


def _provenance(r: dict, slot) -> str:
    """The sentence a terminal reads at the top of a routine's work order.

    A routine's brief is identical every period, so without this the reader cannot tell WHICH
    occurrence they are holding, and `swarm show` on two of them is the same text twice.
    """
    return (f"\n\n---\nPosted by routine `{r['name']}` for the slot {slot}. "
            f"Cadence: every {r['period_minutes']} minutes from {r['anchor_at']}. "
            f"This work order is written once, on the routine, and reposted each period; "
            f"amend the routine rather than this row.")


def _record(ctx, r: dict, slot, outcome: str, by: str, note: str, work_item_id=None) -> dict:
    """Write the occurrence. THE UNIQUE INDEX IS THE GUARD AND IT IS HERE.

    `psycopg2.errors.UniqueViolation` is translated into this verb's own refusal rather than left
    to reach the CLI as a raw SQLSTATE, and the translation changes nothing about the guarantee:
    the transaction is already aborted by the time this runs, so `store.apply` rolls back the
    post above it whatever this function says. The sentence is for the human; the index is the
    reason there is only one row.
    """
    try:
        row = ctx.one(
            """INSERT INTO brain.routine_run
                 (routine_id, scheduled_for, fired_by, outcome, work_item_id, note)
               VALUES (%s, %s, %s, %s, %s, %s)
               RETURNING id, scheduled_for, outcome, work_item_id, note""",
            (r["id"], slot, by or "routine", outcome, work_item_id, note))
    except psycopg2.errors.UniqueViolation as exc:
        raise VerbError(
            f"routine {r['name']} slot {slot} was decided by another caller while this one was "
            f"working. Refused by brain.routine_run's unique index on (routine_id, "
            f"scheduled_for), which is what makes a double fire impossible in the database rather "
            f"than unlikely in the caller. Nothing this call wrote is kept: the whole transaction "
            f"rolls back, including the work item it had already posted, so there is no orphan. "
            f"({exc.__class__.__name__})", code=3) from exc
    return {"routine": r["name"], "routine_run": row["id"], "scheduled_for": row["scheduled_for"],
            "outcome": row["outcome"], "work_item_id": row["work_item_id"], "note": row["note"]}


# ------------------------------------------------------------------ reads

def armed() -> list:
    """Every routine the kill switch has NOT been thrown on, with the slot it is in."""
    if not _have_routines():
        schema.warn_once("routines", BELOW_34)
        return []
    with store.read("runtime") as s:
        return s.query("SELECT * FROM brain.routine_armed ORDER BY name")


def listing() -> list:
    """EVERY routine, disabled included, because a dark routine is the thing being looked for.

    THE EMPTY LIST IS NOT SILENT BELOW LEDGER 34. `docs/SCHEMA-TOLERANCE.md`: a fallback that is
    silent is indistinguishable from a store that had nothing to report, and the difference here
    is the difference between "the operator has no routines" and "this store cannot hold one".
    """
    if not _have_routines():
        schema.warn_once("routines", BELOW_34)
        return []
    with store.read("runtime") as s:
        return s.query(
            """SELECT r.*,
                      brain.routine_slot(r.anchor_at, r.period_minutes, now()) AS current_slot,
                      (SELECT count(*) FROM brain.routine_run rr WHERE rr.routine_id = r.id)
                        AS runs,
                      (SELECT max(rr.fired_at) FROM brain.routine_run rr
                        WHERE rr.routine_id = r.id) AS last_fired_at
                 FROM brain.routine r ORDER BY r.disabled_at NULLS FIRST, r.name""")


def due(now: datetime | None = None) -> list:
    """Armed routines whose current slot has no occurrence row yet.

    DELIBERATELY A READ, and firing is a separate call, the shape
    `queue/human_queue/checkpoints.py::due` established: a console can show what is about to
    happen and a dry run costs nothing.

    `now` is accepted for tests and is NOT the tick's normal path, because the view computes the
    slot from the database's clock and a caller passing its own would be the second notion of
    what time it is that this whole design exists not to have.
    """
    if not _have_routines():
        schema.warn_once("routines", BELOW_34)
        return []
    with store.read("runtime") as s:
        if now is None:
            return s.query("SELECT * FROM brain.routine_due ORDER BY name")
        return s.query(
            """SELECT a.*, brain.routine_slot(a.anchor_at, a.period_minutes, %(now)s) AS at_slot,
                      (%(now)s::timestamptz - brain.routine_slot(a.anchor_at, a.period_minutes,
                                                                 %(now)s))
                        > make_interval(mins => coalesce(a.misfire_grace_minutes,
                                                         a.period_minutes)) AS past_grace
                 FROM brain.routine_armed a
                WHERE brain.routine_slot(a.anchor_at, a.period_minutes, %(now)s) >= a.anchor_at
                  AND NOT EXISTS (SELECT 1 FROM brain.routine_run rr
                                   WHERE rr.routine_id = a.id
                                     AND rr.scheduled_for = brain.routine_slot(a.anchor_at,
                                                                              a.period_minutes,
                                                                              %(now)s))
                ORDER BY a.name""", {"now": now})


def runs(name: str, limit: int = 20) -> list:
    if not _have_routines():
        schema.warn_once("routines", BELOW_34)
        return []
    with store.read("runtime") as s:
        return s.query(
            """SELECT rr.* FROM brain.routine_run rr JOIN brain.routine r ON r.id = rr.routine_id
                WHERE r.name = %s ORDER BY rr.scheduled_for DESC LIMIT %s""", (str(name), limit))


def paused() -> bool:
    with store.read("runtime") as s:
        return (s.scalar("SELECT value FROM brain.runtime_flag WHERE key = 'fleet_paused'")
                or "false") == "true"


# ------------------------------------------------------------------ the tick

def tick(dry_run: bool = True, by: str = "routine-tick") -> dict:
    """Decide every due routine once. What the systemd timer calls, and the whole scheduler.

    DEFAULTS TO A DRY RUN, for `checkpoints.fire_due`'s reason, quoted because it is right: "a
    scheduler that fires by default is one typo away from deciding the operator's open questions
    during a test run". The timer passes `--fire` explicitly, which is one visible word in one
    unit file rather than a default nobody can see.

    NOT A LOOP AND NOT A DAEMON. One pass, then the process exits and systemd owns the next one.
    A resident scheduler would be a second thing with an opinion about what time it is, which is
    the failure `PLAN.md` spends most of its rules preventing, and it would also be a process
    that can be alive and not ticking, which is the supervision failure `brain-health` exists
    for and would then have to grow a fourth check.

    A REFUSAL IS A FINDING, NEVER A SILENT SKIP. Every routine that raised is returned with its
    reason, because the most likely refusals are `post` rejecting a workdir and the fleet being
    paused, and both of those are things the operator needs told rather than logged.
    """
    if paused():
        items = due()
        return {"paused": True, "eligible": len(items), "fired": [], "refused": [],
                "dry_run": dry_run,
                "note": "the fleet is paused, so nothing was dispatched. `swarm resume` lifts it."}
    items = due()
    fired, refused = [], []
    for it in items:
        if dry_run:
            fired.append({"routine": it["name"], "scheduled_for": it["current_slot"],
                          "would": "missed" if it["past_grace"] else "fire", "dry_run": True})
            continue
        try:
            fired.append(store.apply("routine fire", name=it["name"], by=by))
        except Exception as e:                                             # noqa: BLE001
            refused.append({"routine": it["name"], "scheduled_for": it["current_slot"],
                            "reason": str(e)})
    return {"paused": False, "eligible": len(items), "fired": fired, "refused": refused,
            "dry_run": dry_run}


def parse_anchor(text: str) -> datetime:
    """`HH:MM` today in UTC, or a full ISO timestamp. Anything else refuses in a sentence.

    `HH:MM` is what an operator types and it means "this time of day", so it anchors at TODAY's
    occurrence of it: with a period of 1440 that is the daily routine he meant, and the first
    slot is today's if it has passed and yesterday's if it has not, which `routine_due` then
    resolves correctly either way.
    """
    t = str(text or "").strip()
    if not t:
        raise VerbError("a routine needs an anchor: --at HH:MM (UTC, today) or a full ISO "
                        "timestamp. There is no default, because a routine that fires at an hour "
                        "nobody chose is a routine nobody trusts.")
    if len(t) == 5 and t[2] == ":":
        try:
            h, m = int(t[:2]), int(t[3:])
        except ValueError:
            raise VerbError(f"{t!r} is not HH:MM.") from None
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise VerbError(f"{t!r} is not a time of day (UTC).")
        now = datetime.now(timezone.utc)
        return now.replace(hour=h, minute=m, second=0, microsecond=0)
    try:
        d = datetime.fromisoformat(t)
    except ValueError:
        raise VerbError(f"{t!r} is neither HH:MM nor an ISO timestamp.") from None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


PERIODS = {"daily": 1440, "weekly": 10080, "hourly": 60, "twice-daily": 720}


def parse_period(text: str) -> int:
    """A word or a number of minutes. MONTHLY IS REFUSED AND SAYS WHY.

    Calendar months are not a fixed number of minutes, so a `period_minutes` that pretended
    otherwise would drift by three days a year and the drift would be invisible until somebody
    counted. A monthly routine is a real request and it is a second migration with a different
    column, not a rounding of this one.
    """
    t = str(text or "").strip().lower()
    if t in ("monthly", "month", "quarterly", "yearly", "annually"):
        raise VerbError(
            f"{t} is not expressible as a fixed period and this refuses rather than approximating "
            f"it. Calendar months are 28 to 31 days, so any minute count drifts by about three "
            f"days a year and nothing would notice. Daily, weekly, hourly, twice-daily, or a "
            f"number of minutes.")
    if t in PERIODS:
        return PERIODS[t]
    try:
        n = int(t)
    except ValueError:
        raise VerbError(f"{text!r} is not a period. One of {', '.join(sorted(PERIODS))}, or a "
                        f"number of minutes.") from None
    if n < 1:
        raise VerbError("a period is at least one minute.")
    return n
