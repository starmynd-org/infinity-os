#!/usr/bin/env python3
"""A project is a row, and its state is a control. Bus row 0429 (Sprint P1), migration 44.

Row `0421` item 6 is the operator's, from the 2026-08-28 demo walkthrough. He asked for a board
with `in progress / hold / ice / blocked`, and he said what a hold has to DO:

    "there's certain projects where I kind of just wanted the AI to take a break with it. And then
     I realized later that it worked on it for like eight hours with eight terminals and I ran out
     of API tokens very quickly."

Before migration 44 there was no row to hold that state. A project was a text prefix inside the
nullable `brain.work_item.canonical_task`, and scene 1 re-measures the thing that made the prefix
unusable: it is EMPTY, on every store checked, so a gate built over it excludes nothing while
printing success.

WHAT THIS FILE ASSERTS, AND WHAT IT DELIBERATELY DOES NOT.

Migration 44 is the ENTITY step of the build order `web/MUST-NOT-BUILD.md` item 3 sets: entity,
then verb, then board. So the claim under test here is NOT "a hold throttles agents" -- nothing in
migration 44 throttles anything, and asserting that it does would be the vacuous pass this repo
keeps finding. The claim is narrower and checkable: the four states exist on a row, the join to
work_item exists and cannot contradict `canonical_task`, and the DIRECTION that spends money is
the one that needs a human.

    scenes 2-6    the shape: born in progress, stamped rather than trusted, a reason on rest
    scenes 7-13   migration 35's asymmetry: rest is open to everybody, `in_progress` is not
    scenes 14-18  the join, its foreign key, and the constraint that stops the two project
                  answers disagreeing
    scene 19      the view, including the project with no work at all
    scene 20      the escape hatch migration 44 left open, and MIGRATION 46 CLOSED. This scene
                  did its job: it asserted the hatch as currently-true so that the day somebody
                  closed it, the suite would say the documentation was stale. That happened on
                  2026-08-29 (bus row 0444), so the scene is INVERTED and not narrowed, and it
                  now watches the refusal over the same raw credential. Below ledger 46 it
                  reports the hatch open rather than going red, because both worlds are real.
    scene 22      the ARCHIVE STAMP, migration 47, bus row 0443, decision D-05: a project that
                  finished leaves the board without a fifth state being added to his four words.
                  Migration 35's asymmetry one fact further out -- archiving needs nothing because
                  it stops spending, un-archiving needs a human because it starts it again -- plus
                  the assertion that archiving is NOT cancelling and the row survives on the view.
                  Below ledger 47 it reports rather than failing.
    scene 21      reported, not asserted: whether anything reads brain.project.state yet

Every refusal below is watched over the RAW credentials rather than through a verb, so whatever
refuses is the TABLE. There is no verb yet; that is task 0430.

Run: python3 engine/tests/test_project_entity.py    (a scratch database, never `brain`)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT))

os.environ.setdefault("BRAIN_PG_DB", os.environ.get("ENGINE_SCRATCH_DB", "brain_scratch"))

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _scratch_preflight import reconcile              # noqa: E402
reconcile(os.environ["BRAIN_PG_DB"])

import psycopg2                                       # noqa: E402
import store.session as session                       # noqa: E402

TRANSITIONS = ROOT / "engine/swarm_engine/transitions.py"

PASS, FAIL, INCONC = 0, 0, 0


def ok(msg):
    global PASS
    PASS += 1
    print(f"  ok    {msg}")


def bad(msg, detail=""):
    global FAIL
    FAIL += 1
    print(f"  FAIL  {msg}")
    if detail:
        print(f"        {detail}")


def inconclusive(msg, why):
    """Nothing was observed, and that is neither a pass nor a failure. Task 0382's mechanism."""
    global INCONC
    INCONC += 1
    print(f"  INCONCLUSIVE  {msg}")
    print(f"                {why}")


def eq(msg, got, want):
    ok(msg) if got == want else bad(msg, f"wanted [{want}], got [{got}]")


def truth(msg, cond, detail=""):
    ok(msg) if cond else bad(msg, detail)


# ------------------------------------------------------------------ the raw connections
#
# Neither goes through the narrow waist. `store.read()` is READ ONLY and there is no verb for any
# of this yet, so both of these are psycopg2 over the credentials the runtime and the operator
# actually hold. Whatever refuses below is the TABLE.

def _raw(role, sql, params=None, fetch=False):
    conn = psycopg2.connect(**session.dsn(role))
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            got = cur.fetchall() if fetch else cur.rowcount
        conn.commit()
        return got
    finally:
        conn.close()


def as_runtime(sql, params=None, fetch=False):
    """One statement as `brain_runtime`: the credential every agent process already holds."""
    return _raw("runtime", sql, params, fetch)


def as_operator(sql, params=None, fetch=False):
    """One statement as the operator's own login, which brain.current_human() DOES recognise."""
    return _raw("operator", sql, params, fetch)


def as_owner(sql, params=None, fetch=False):
    """The migration credential. Used only to build fixtures and to read, never to prove a rule."""
    return _raw("owner", sql, params, fetch)


def refused(msg, fn, naming=""):
    try:
        fn()
        bad(msg, "NOT refused. The statement went through.")
    except psycopg2.errors.RaiseException as exc:
        text = str(exc)
        if naming and naming not in text:
            bad(msg, f"refused, but the message does not name {naming!r}: {text.splitlines()[0]}")
        else:
            ok(msg)
    except Exception as exc:                                          # noqa: BLE001
        bad(msg, f"refused by the WRONG thing ({type(exc).__name__}): {exc}")


def refused_by_grant(msg, fn):
    """The FIRST gate: the GRANT. A privilege refusal arrives as InsufficientPrivilege and never
    reaches the trigger, so it must be told apart from the trigger's own refusal or the suite
    would report a rule as held by the thing that never ran."""
    try:
        fn()
        bad(msg, "NOT refused. The statement went through.")
    except psycopg2.errors.InsufficientPrivilege:
        ok(msg)
    except Exception as exc:                                          # noqa: BLE001
        bad(msg, f"refused by the WRONG thing ({type(exc).__name__}): {exc}")


def refused_by_constraint(msg, fn, naming=""):
    """A CHECK or a foreign key, which arrive as IntegrityError rather than RaiseException."""
    try:
        fn()
        bad(msg, "NOT refused. The statement went through.")
    except psycopg2.errors.IntegrityError as exc:
        text = str(exc)
        if naming and naming not in text:
            bad(msg, f"refused, but the message does not name {naming!r}: {text.splitlines()[0]}")
        else:
            ok(msg)
    except Exception as exc:                                          # noqa: BLE001
        bad(msg, f"refused by the WRONG thing ({type(exc).__name__}): {exc}")


# ------------------------------------------------------------------ fixtures

def reset():
    """Only this file's own rows. `scratch-db.sh truncate` would take a sibling suite's world.

    The project DELETE goes through the OPERATOR and not the owner, and that is the rule under
    test doing its job rather than a workaround: migration 44 refuses to delete a RESTING project
    from any connection brain.current_human() does not recognise, and brain_owner is not one --
    the same way migration 35 refuses even the owner the right to arm a routine. A teardown that
    could bypass its own subject would be a teardown proving the subject is not installed.
    """
    # SCOPED TO THIS FILE'S OWN IDS, and the `OR project IS NOT NULL` arm that used to be here is
    # GONE. It read "every work item in the store that names any project", which is not this
    # file's own rows and is exactly what the sentence above says a reset must not take. Measured
    # 2026-08-29 by lane B2, on `brain_b2`: running this suite deleted five work items belonging
    # to a live board fixture in another lane's world and the next driver run read `0 open` on a
    # project that had five. A teardown wider than its own fixture is a destructive verb wearing a
    # test's clothes. Every row this file creates comes from `work_item()` with a `p0429` id.
    as_owner("DELETE FROM brain.work_item WHERE id LIKE 'p0429%'")
    # THE ARCHIVE IS CLEARED FIRST, and through the OPERATOR for the same reason the DELETE is:
    # migration 47 refuses to un-archive from any connection brain.current_human() does not
    # recognise, so a teardown that could bypass its own subject would be a teardown proving the
    # subject is not installed. Guarded on the column so this file still runs below ledger 47.
    if _has_archive():
        as_operator("UPDATE brain.project SET archived_at = NULL, archived_by = NULL, "
                    "archived_reason = NULL WHERE slug LIKE 'p0429-%' AND archived_at IS NOT NULL")
    as_operator("DELETE FROM brain.project WHERE slug LIKE 'p0429-%'")


def _has_archive():
    """Does this store carry migration 47? Asked once per call rather than cached, because a suite
    that remembered the answer would be wrong on the run after the operator applied it."""
    return bool(one("SELECT EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid = "
                    "to_regclass('brain.project') AND attname = 'archived_at' "
                    "AND NOT attisdropped)")[0])


def project(slug, title="a project", state=None, reason=None):
    """Create one, as the OWNER, so the fixture never doubles as the thing under test."""
    if state is None:
        as_owner("INSERT INTO brain.project (slug, title) VALUES (%s, %s)", (slug, title))
    else:
        as_owner("INSERT INTO brain.project (slug, title, state, state_reason) "
                 "VALUES (%s, %s, %s, %s)", (slug, title, state, reason or ""))
    return slug


def one(sql, params=None):
    rows = as_owner(sql, params, fetch=True)
    return rows[0] if rows else None


def work_item(item_id, proj=None, canonical=None, state="inbox"):
    as_owner("INSERT INTO brain.work_item (id, title, lane, state, project, canonical_task) "
             "VALUES (%s, 'an item', 't', %s, %s, %s)",
             (item_id, state, proj, canonical))
    return item_id


# ================================================================== scene 1

def test_the_prefix_it_replaces_is_empty():
    """Re-measured here rather than forwarded from the brief. The denominator that made the string
    prefix unusable as an entity: a project set derived from it is size 0.

    THIS SCENE REFUSES TO PASS OVER AN EMPTY TABLE, and that is not pedantry -- it is the exact
    failure it exists to describe. A scratch database that was just truncated has 0 work items, so
    "0 of 0 rows carry a prefix" is a verdict about nothing, and printing `ok` for it would be the
    same vacuous green the prefix itself would have produced. When the store is empty the count is
    reported as INCONCLUSIVE with its denominator visible.
    """
    total, with_prefix, with_hash = one(
        "SELECT count(*), count(canonical_task), "
        "       count(*) FILTER (WHERE canonical_task LIKE '%#%') FROM brain.work_item")
    print(f"        measured on {os.environ['BRAIN_PG_DB']}: {total} work items, "
          f"{with_prefix} with a canonical_task, {with_hash} containing '#'")
    if total == 0:                                                    # DENOMINATOR
        inconclusive("the canonical_task prefix carries no project",
                     f"brain.work_item holds 0 rows on {os.environ['BRAIN_PG_DB']}, so 0 rows "
                     f"were compared. The measurement that matters was taken on the LIVE store "
                     f"on 2026-08-28 and is recorded in migration 44's header: 0 of 345 on "
                     f"brain, 0 of 57 on brain_demo, 0 of 4 on brain_scratch. A scratch database "
                     f"cannot restate it and must not pretend to.")
        return
    truth(f"the canonical_task prefix carries no project on any of {total} rows",
          with_hash == 0,
          f"{with_hash} rows DO carry a prefix. That changes the migration's 'backfills nothing' "
          f"claim on this store and is worth reporting rather than passing over.")


# ================================================================== scenes 2-6, the shape

def test_a_project_is_born_in_progress():
    reset()
    project("p0429-alpha", "Alpha")
    state, reason = one("SELECT state, state_reason FROM brain.project WHERE slug = 'p0429-alpha'")
    eq("a new project's state is in_progress", state, "in_progress")
    eq("and it needs no reason to be in progress", reason, "")


def test_the_four_states_are_his_four_and_nothing_else():
    reset()
    accepted = []
    for s in ("in_progress", "hold", "ice", "blocked"):
        slug = f"p0429-s-{s.replace('_', '')}"
        try:
            project(slug, "S", state=s, reason="because" if s != "in_progress" else None)
            accepted.append(s)
        except Exception as exc:                                      # noqa: BLE001
            bad(f"the state {s!r} was refused", str(exc).splitlines()[0])
    eq("all four of the operator's states are accepted, 4 compared", len(accepted), 4)
    refused_by_constraint("a fifth state is refused by the CHECK",
                          lambda: project("p0429-s-arch", "S", state="archived", reason="x"),
                          naming="project_state_check")
    reset()


def test_a_resting_project_must_carry_a_reason():
    reset()
    refused_by_constraint("a project born on hold with no reason is refused",
                          lambda: as_owner("INSERT INTO brain.project (slug, title, state) "
                                           "VALUES ('p0429-noreason', 'N', 'hold')"),
                          naming="project_rest_has_a_reason")
    project("p0429-alpha", "Alpha")
    refused_by_constraint("and a hold applied with no reason is refused too",
                          lambda: as_runtime("UPDATE brain.project SET state = 'hold' "
                                             "WHERE slug = 'p0429-alpha'"),
                          naming="project_rest_has_a_reason")


def test_the_stamp_is_the_databases_answer_and_not_the_callers():
    """Migration 36's measured lesson, applied one table over: six forged acceptor names including
    zzz-not-a-person were written from brain_runtime before `accepted_by` stopped being a string
    the caller picks."""
    reset()
    as_runtime("INSERT INTO brain.project (slug, title, state_changed_by, created_by) "
               "VALUES ('p0429-forge', 'F', 'zzz-not-a-person', 'zzz-not-a-person')")
    by, created = one("SELECT state_changed_by, created_by FROM brain.project "
                      "WHERE slug = 'p0429-forge'")
    truth("a forged state_changed_by is overwritten by the connection's own name",
          by != "zzz-not-a-person" and by, f"the table kept {by!r}")
    truth("and so is a forged created_by", created != "zzz-not-a-person" and created,
          f"the table kept {created!r}")
    print(f"        brain_runtime was stamped as {by!r}")


def test_the_operators_login_stamps_the_human_and_not_the_role():
    reset()
    as_operator("INSERT INTO brain.project (slug, title) VALUES ('p0429-op', 'O')")
    by = one("SELECT state_changed_by FROM brain.project WHERE slug = 'p0429-op'")[0]
    human = one("SELECT human FROM brain.human_role WHERE role_name = 'brain_operator'")
    want = human[0] if human else None
    if want is None:
        inconclusive("the operator's stamp is brain.current_human()",
                     "brain.human_role has no row for brain_operator on this store, so there is "
                     "no human for current_human() to return and nothing was compared.")
    else:
        eq("the operator's stamp is brain.current_human(), not the role name", by, want)


# ================================================================== scenes 7-13, the asymmetry

def test_an_agent_may_bring_a_project_to_rest():
    """Migration 35's rule: a kill switch that can be refused is not a kill switch."""
    reset()
    project("p0429-alpha", "Alpha")
    n = as_runtime("UPDATE brain.project SET state = 'hold', state_reason = 'taking a break' "
                   "WHERE slug = 'p0429-alpha'")
    eq("brain_runtime CAN put a project on hold, 1 row", n, 1)
    state = one("SELECT state FROM brain.project WHERE slug = 'p0429-alpha'")[0]
    eq("and the store agrees it is held", state, "hold")


def test_an_agent_may_move_between_resting_states():
    reset()
    project("p0429-alpha", "Alpha", state="hold", reason="resting")
    n = as_runtime("UPDATE brain.project SET state = 'ice', state_reason = 'deeper rest' "
                   "WHERE slug = 'p0429-alpha'")
    eq("hold -> ice is open to brain_runtime, 1 row", n, 1)


def test_an_agent_may_not_put_a_project_back_in_progress():
    """THE ROW WITH THE BILL BEHIND IT. An agent that can lift its own hold is not being held."""
    reset()
    project("p0429-alpha", "Alpha", state="hold", reason="taking a break")
    refused("brain_runtime CANNOT return a held project to in_progress",
            lambda: as_runtime("UPDATE brain.project SET state = 'in_progress', "
                               "state_reason = 'back to work' WHERE slug = 'p0429-alpha'"),
            naming="is not a human login")
    refused("and neither can brain_owner, the migration credential",
            lambda: as_owner("UPDATE brain.project SET state = 'in_progress', "
                             "state_reason = 'back to work' WHERE slug = 'p0429-alpha'"),
            naming="is not a human login")
    state = one("SELECT state FROM brain.project WHERE slug = 'p0429-alpha'")[0]
    eq("and the project is still held afterwards", state, "hold")


def test_a_human_may_put_a_project_back_in_progress():
    reset()
    project("p0429-alpha", "Alpha", state="hold", reason="taking a break")
    n = as_operator("UPDATE brain.project SET state = 'in_progress', "
                    "state_reason = 'the bill is paid' WHERE slug = 'p0429-alpha'")
    eq("the operator's login CAN return it to in_progress, 1 row", n, 1)
    state, reason = one("SELECT state, state_reason FROM brain.project "
                        "WHERE slug = 'p0429-alpha'")
    eq("the state moved", state, "in_progress")
    eq("and the reason for the resume is on the row", reason, "the bill is paid")


def test_a_resume_with_no_reason_is_refused_even_from_a_human():
    reset()
    project("p0429-alpha", "Alpha", state="hold", reason="taking a break")
    refused("a resume carrying no reason is refused",
            lambda: as_operator("UPDATE brain.project SET state = 'in_progress', "
                                "state_reason = '' WHERE slug = 'p0429-alpha'"),
            naming="with no reason")


def test_an_agent_may_not_delete_a_resting_project():
    """Delete-and-recreate is a resume with no record of the resume."""
    reset()
    project("p0429-alpha", "Alpha", state="hold", reason="taking a break")
    # TWO GATES, AND NEITHER TRUSTS THE OTHER. brain_runtime is stopped by the GRANT and never
    # reaches the trigger; brain_owner holds DELETE and is stopped by the trigger. Asserting only
    # the first would report the trigger as held by a statement that never ran it.
    refused_by_grant("brain_runtime holds no DELETE on brain.project at all",
                     lambda: as_runtime("DELETE FROM brain.project WHERE slug = 'p0429-alpha'"))
    refused("and brain_owner, which DOES hold DELETE, is refused by the trigger",
            lambda: as_owner("DELETE FROM brain.project WHERE slug = 'p0429-alpha'"),
            naming="is not a human login")
    n = one("SELECT count(*) FROM brain.project WHERE slug = 'p0429-alpha'")[0]
    eq("and the project is still there", n, 1)


def test_a_title_edit_does_not_move_the_state_stamp():
    """'When did this go on hold' has to stay answerable after somebody fixes a typo."""
    reset()
    project("p0429-alpha", "Alpha", state="hold", reason="taking a break")
    before = one("SELECT state_changed_at FROM brain.project WHERE slug = 'p0429-alpha'")[0]
    as_runtime("UPDATE brain.project SET title = 'Alpha, renamed' WHERE slug = 'p0429-alpha'")
    after = one("SELECT state_changed_at FROM brain.project WHERE slug = 'p0429-alpha'")[0]
    eq("a title edit leaves state_changed_at alone", after, before)


# ================================================================== scenes 14-18, the join

def test_a_work_item_can_name_its_project():
    reset()
    project("p0429-alpha", "Alpha")
    work_item("p0429i1", proj="p0429-alpha")
    got = one("SELECT project FROM brain.work_item WHERE id = 'p0429i1'")[0]
    eq("brain.work_item.project holds the slug", got, "p0429-alpha")


def test_the_project_column_is_nullable_and_stays_that_way():
    """345 of 345 live rows carry no project. A NOT NULL here would have needed a fabricated
    default, which is a project invented out of nothing on every task the fleet posts."""
    reset()
    work_item("p0429i2")
    got = one("SELECT project FROM brain.work_item WHERE id = 'p0429i2'")[0]
    eq("a work item with no project is legal and reads NULL", got, None)


def test_a_work_item_cannot_name_a_project_that_does_not_exist():
    reset()
    refused_by_constraint("the foreign key refuses an invented slug",
                          lambda: work_item("p0429i3", proj="p0429-does-not-exist"),
                          naming="project")


def test_the_two_project_answers_cannot_disagree():
    reset()
    project("p0429-alpha", "Alpha")
    project("p0429-beta", "Beta")
    work_item("p0429i4", proj="p0429-alpha", canonical="p0429-alpha#0088")
    ok("project and a canonical_task prefix that AGREE are accepted")
    refused_by_constraint("project and a canonical_task prefix that DISAGREE are refused",
                          lambda: work_item("p0429i5", proj="p0429-alpha",
                                            canonical="p0429-beta#0088"),
                          naming="work_item_project_matches_canonical")
    work_item("p0429i6", proj="p0429-alpha", canonical=None)
    work_item("p0429i7", proj=None, canonical="p0429-beta#0088")
    ok("either side alone is still legal: the constraint stops disagreement, not absence")


def test_a_project_with_work_on_it_cannot_be_deleted():
    reset()
    project("p0429-alpha", "Alpha")
    work_item("p0429i8", proj="p0429-alpha")
    refused_by_constraint("the operator cannot delete a project that still holds work",
                          lambda: as_operator("DELETE FROM brain.project "
                                              "WHERE slug = 'p0429-alpha'"),
                          naming="work_item")


# ================================================================== scene 19, the view

def test_the_view_counts_and_does_not_drop_an_empty_project():
    reset()
    project("p0429-alpha", "Alpha", state="hold", reason="taking a break")
    project("p0429-empty", "Empty")
    work_item("p0429i9", proj="p0429-alpha", state="active")
    work_item("p0429i10", proj="p0429-alpha", state="inbox")
    work_item("p0429i11", proj="p0429-alpha", state="done")
    row = one("SELECT state, open_items, active_items, all_items FROM brain.project_open_work "
              "WHERE slug = 'p0429-alpha'")
    eq("the held project's state is on the view", row[0], "hold")
    eq("open_items counts inbox + active + blocked, not done", row[1], 2)
    eq("active_items counts only active", row[2], 1)
    eq("all_items counts everything filed against it", row[3], 3)
    empty = one("SELECT open_items, all_items FROM brain.project_open_work "
                "WHERE slug = 'p0429-empty'")
    truth("a project with no work still appears, with zeroes", empty == (0, 0),
          f"got {empty}. A board that drops empty projects hides the ones just created.")


# ================================================================== scene 20, the open hatch

def test_the_escape_hatch_is_open_and_this_says_so():
    """THE ALARM FIRED AND THIS IS THE ANSWER TO IT, not a narrowing of it.

    This scene used to assert the hatch as CURRENTLY TRUE: brain.work_item.project is writable by
    brain_runtime because `post` is an agent verb, so an agent could move a work item off a held
    project in one UPDATE and out from under the throttle. It was written that way on purpose, so
    that the day somebody closed it the suite would go red and say migration 44's header was
    stale. That day is 2026-08-29 and the closer is `migrations/0046_work_does_not_leave_a_
    resting_project.sql`, bus row 0444.

    So the scene is INVERTED rather than deleted or loosened. Row 0444's own brief says "DO NOT
    close this by narrowing scene 20. That scene is the alarm." A narrowing would be an assertion
    that stopped looking; this one still looks, at the same column, over the same raw credential,
    and now watches the refusal instead of the hole.

    BOTH WORLDS ARE REAL, so the ledger decides which is asserted. Migration 46 is not applied to
    every store, and on one below it the hatch IS open; reporting that as a failure would make
    this suite red for a reason that is not a defect. Below 46 it re-measures the hole and says
    which migration would close it.

    Every statement here is over the RAW credentials rather than through a verb, which is this
    file's rule: whatever refuses has to be the DATABASE, because `web/rooms.py` is not on every
    path and `swarm done <id> --agent operator` reached an identical forgery with none of it.
    """
    reset()
    project("p0429-alpha", "Alpha", state="hold", reason="taking a break")
    project("p0429-beta", "Beta")                     # in progress, holding nothing
    work_item("p0429i12", proj="p0429-alpha")
    work_item("p0429i13", proj="p0429-beta")
    work_item("p0429i14")                             # in no project at all

    if ledger() < 46:
        n = as_runtime("UPDATE brain.work_item SET project = NULL WHERE id = 'p0429i12'")
        truth("BELOW LEDGER 46: an agent CAN still move a work item off a held project",
              n == 1,
              "It was refused on a store that has no migration 46. Something else closed this "
              "hatch and nothing in the tree says what.")
        inconclusive("whether the one-UPDATE exit is closed on this store",
                     f"this database is at ledger {ledger()} and the closer is migration 46 "
                     f"(migrations/0046_work_does_not_leave_a_resting_project.sql, bus row 0444). "
                     f"A hold on this store can still be stepped around in one statement.")
        return

    # THE REFUSAL, over the credential every agent process actually holds.
    refused("an agent CANNOT move a work item off a HELD project (migration 46)",
            lambda: as_runtime("UPDATE brain.work_item SET project = NULL WHERE id = 'p0429i12'"),
            naming="p0429-alpha")
    eq("and the row still names the held project afterwards",
       one("SELECT project FROM brain.work_item WHERE id = 'p0429i12'")[0], "p0429-alpha")

    # Repointing to ANOTHER project is the same escape wearing a different value.
    refused("nor can it repoint that row at a project that is in progress",
            lambda: as_runtime("UPDATE brain.work_item SET project = 'p0429-beta' "
                               "WHERE id = 'p0429i12'"),
            naming="p0429-alpha")

    # THE PERMITTED DIRECTIONS, or this is a wall and not a gate. Filing work INTO a hold can only
    # add to it, and moving work off a project that is holding nothing escapes nothing.
    truth("an agent CAN still file work INTO the held project, which cannot escape a hold",
          as_runtime("UPDATE brain.work_item SET project = 'p0429-alpha' "
                     "WHERE id = 'p0429i14'") == 1,
          "The permitted direction was refused, so `post --project` and `swarm project attach` "
          "are both broken by this trigger.")
    truth("and it CAN move work off a project that is in progress",
          as_runtime("UPDATE brain.work_item SET project = NULL "
                     "WHERE id = 'p0429i13'") == 1,
          "Nothing is being escaped there: an in-progress project holds nothing.")

    # IT IS HIS BOARD. Migration 44's asymmetry one level down: stopping is free and starting
    # needs the operator, and moving an item out from under a hold IS starting it.
    truth("the OPERATOR's own login can move work off the held project",
          as_operator("UPDATE brain.work_item SET project = NULL "
                      "WHERE id = 'p0429i12'") == 1,
          "The operator was refused on his own board. The guard reads brain.current_human() and "
          "should exempt it.")


# ================================================================== scene 22, the archive

def test_the_archive_takes_a_project_off_the_board():
    """MIGRATION 47, bus row 0443, decision D-05. The operator's four words gain no fifth.

    He named `in progress / hold / ice / blocked` and none of them is terminal, so a project
    entered on the board stayed on it forever. He was asked (q0460) and the answer was an ARCHIVED
    STAMP rather than a fifth word, because naming his own vocabulary is his.

    THE ASYMMETRY IS MIGRATION 35'S, ONE FACT FURTHER OUT, and it is what this scene watches:
    archiving WITHHOLDS work from the claim path, so it needs nothing; un-archiving puts the work
    back in front of the claim path, so it needs `brain.current_human()`. Every statement is over
    the RAW credentials, so whatever refuses is the TABLE.

    The claim-path half is NOT tested here and cannot be: this file watches the entity and the
    gate lives in `engine/swarm_engine/transitions.py`. It is watched failing and passing in
    `outputs/2026-08-29-commander/lane-B2-throttle-proof.py` phase D.
    """
    reset()
    if ledger() < 47:
        inconclusive("the archive stamp on this store",
                     f"this database is at ledger {ledger()} and the stamp is migration 47 "
                     f"(migrations/0047_a_project_that_finished_leaves_the_board.sql, bus row "
                     f"0443). A project that finishes has nowhere to go here, so after a year "
                     f"'in progress' is every project ever started.")
        return
    project("p0429-fin", "Finished")

    truth("an agent CAN archive a project: it stops spending, so it needs nothing",
          as_runtime("UPDATE brain.project SET archived_at = now(), archived_by = 'forged', "
                     "archived_reason = 'the work finished' WHERE slug = 'p0429-fin'") == 1,
          "Archiving was refused. It is the safe direction and a switch that stops spending must "
          "never need permission.")
    eq("and archived_by is the DATABASE's answer, not the string the caller sent",
       one("SELECT archived_by FROM brain.project WHERE slug = 'p0429-fin'")[0], "brain_runtime")
    eq("the four states are untouched, so a project archived while in progress still says so",
       one("SELECT state FROM brain.project WHERE slug = 'p0429-fin'")[0], "in_progress")

    refused_by_constraint(
        "an archive with no reason is refused by project_archived_is_whole",
        lambda: as_owner("UPDATE brain.project SET archived_reason = '' "
                         "WHERE slug = 'p0429-fin'"),
        naming="project_archived_is_whole")

    refused("an agent CANNOT un-archive it: that puts the work back in the claim path",
            lambda: as_runtime("UPDATE brain.project SET archived_at = NULL, archived_by = NULL, "
                               "archived_reason = NULL WHERE slug = 'p0429-fin'"),
            naming="p0429-fin")
    truth("and it is still archived afterwards",
          one("SELECT archived_at FROM brain.project WHERE slug = 'p0429-fin'")[0] is not None,
          "The refusal did not hold.")
    truth("the OPERATOR's login CAN un-archive it",
          as_operator("UPDATE brain.project SET archived_at = NULL, archived_by = NULL, "
                      "archived_reason = NULL WHERE slug = 'p0429-fin'") == 1,
          "The operator was refused on his own board.")

    refused("a project cannot be BORN archived: one that never started did not finish",
            lambda: as_owner(
                "INSERT INTO brain.project (slug, title, archived_at, archived_by, "
                "archived_reason) VALUES ('p0429-born', 'born archived', now(), 'x', 'y')"),
            naming="p0429-born")

    # ARCHIVING IS NOT CANCELLING, and this is the assertion that keeps it from drifting into one.
    # Rows 0399 and 0403 were lost on this operation by tidying a board while their defects were
    # still on the operator's screen the next morning.
    work_item("p0429i20", proj="p0429-fin")
    as_runtime("UPDATE brain.project SET archived_at = now(), archived_by = 'x', "
               "archived_reason = 'done' WHERE slug = 'p0429-fin'")
    eq("the archived project is STILL on brain.project_open_work, with its work counted",
       one("SELECT all_items FROM brain.project_open_work WHERE slug = 'p0429-fin'")[0], 1)
    eq("and the view carries the stamp so a reader can tell",
       one("SELECT archived_reason FROM brain.project_open_work "
           "WHERE slug = 'p0429-fin'")[0], "done")


# ================================================================== scene 21, reported not passed

def test_whether_anything_reads_the_state_yet():
    """Migration 44 is the entity step and throttles nothing. Reported rather than asserted,
    because task 0430 is landing the gate that makes this false, and a suite that goes red the
    moment the next task succeeds is a suite people delete."""
    src = TRANSITIONS.read_text(encoding="utf-8")
    hits = [n for n, line in enumerate(src.splitlines(), 1)
            if "brain.project" in line or "work_item_project" in line]
    if hits:
        inconclusive("the claim path now mentions a project",
                     f"{TRANSITIONS.relative_to(ROOT)} names it at line(s) "
                     f"{', '.join(str(h) for h in hits)}. Migration 44 promised nothing here; "
                     f"task 0430 is the row that does. Read that gate's own suite for whether a "
                     f"hold actually throttles.")
    else:
        inconclusive("nothing reads brain.project.state yet, which is the build order",
                     f"{TRANSITIONS.relative_to(ROOT)} does not name brain.project. A hold "
                     f"recorded today throttles NOTHING. That is web/MUST-NOT-BUILD.md item 3's "
                     f"order -- entity, then verb, then board -- and task 0430 is the verb.")


def ledger():
    return one("SELECT max(version) FROM brain.schema_migration")[0]


def the_human():
    """What the database calls the operator's own connection. NULL means this suite cannot run."""
    try:
        return as_operator("SELECT brain.current_human()", fetch=True)[0][0]
    except Exception as exc:                                          # noqa: BLE001
        return f"!{type(exc).__name__}: {exc}"


def main():
    print(__doc__.splitlines()[0])
    print(f"database {os.environ['BRAIN_PG_DB']}, ledger {ledger()}")

    # NOT RUN, NOT RED-AS-A-CODE-DEFECT. Every asymmetry scene below needs a connection
    # brain.current_human() recognises, and on a host where store/bin/provision-operator.sh has
    # never run there is no such login. The condition is DETECTED and named rather than guessed
    # at, and the remedy is on this host, so this still turns the runner red rather than passing
    # quietly over an empty world.
    human = the_human()
    if not human or str(human).startswith("!"):
        print(f"\nNOT RUN: brain.current_human() answered {human!r} on database "
              f"{os.environ['BRAIN_PG_DB']}. Migration 44's asymmetry is defined against that "
              f"function, so without a human login there is no permissive direction to test and "
              f"nothing here would be measured. Provision with store/bin/provision-operator.sh, "
              f"which `scratch-db.sh create` also does. 0 comparisons made.")
        return 1
    print(f"  the database calls the operator's connection: {human!r}")

    print("\n-- the prefix this entity replaces")
    test_the_prefix_it_replaces_is_empty()

    print("\n-- the shape of a project row")
    test_a_project_is_born_in_progress()
    test_the_four_states_are_his_four_and_nothing_else()
    test_a_resting_project_must_carry_a_reason()
    test_the_stamp_is_the_databases_answer_and_not_the_callers()
    test_the_operators_login_stamps_the_human_and_not_the_role()

    print("\n-- migration 35's asymmetry: stopping is free, starting is a human's")
    test_an_agent_may_bring_a_project_to_rest()
    test_an_agent_may_move_between_resting_states()
    test_an_agent_may_not_put_a_project_back_in_progress()
    test_a_human_may_put_a_project_back_in_progress()
    test_a_resume_with_no_reason_is_refused_even_from_a_human()
    test_an_agent_may_not_delete_a_resting_project()
    test_a_title_edit_does_not_move_the_state_stamp()

    print("\n-- the join to work_item")
    test_a_work_item_can_name_its_project()
    test_the_project_column_is_nullable_and_stays_that_way()
    test_a_work_item_cannot_name_a_project_that_does_not_exist()
    test_the_two_project_answers_cannot_disagree()
    test_a_project_with_work_on_it_cannot_be_deleted()

    print("\n-- the view")
    test_the_view_counts_and_does_not_drop_an_empty_project()

    print("\n-- what migration 44 does NOT do, stated where it can be checked")
    test_the_escape_hatch_is_open_and_this_says_so()
    print("\n-- the archive stamp, migration 47, which takes a finished project off the board")
    test_the_archive_takes_a_project_off_the_board()
    test_whether_anything_reads_the_state_yet()

    reset()

    # THE DENOMINATOR, with the INCONCLUSIVE count beside it rather than folded into either
    # column. A verdict over an empty set is not a pass.
    if PASS + FAIL == 0:                                              # DENOMINATOR
        print("\n0 comparisons made. A verdict over an empty set is not a pass.")
        return 2
    print(f"\n{PASS} passed, {FAIL} failed, {INCONC} inconclusive")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
