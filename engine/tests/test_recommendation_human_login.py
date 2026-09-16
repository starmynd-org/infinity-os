#!/usr/bin/env python3
"""BOTH HALVES OF THE DECISION ARE A LOGIN: a recommendation is accepted, and rejected, by a human.

Task 0290 (accept, migration 32) and task 0313 (reject, migration 33), children of the V9
acceptance run (0222), which found V2 invariant 1 -- a drafted option cannot dispatch without a
human choosing it -- did not hold.

THE SECOND HALF IS NOT INVARIANT 1 AND IS HERE ANYWAY. A rejection dispatches nothing, which is
the reason 0290 named it and left it. What it moves is `brain.queue_acted_on`, PLAN.md's
falsifier for this whole layer, whose rejection arm is `count(*) FILTER (WHERE state =
'rejected')` grouped by `template_id` -- it reads NEITHER `decided_by` NOR `actor_type`. So an
agent able to write that state prunes a playbook the operator never rejected, and the cheaper fix
considered for 0313 (leave the verb open, book `actor_type = 'ai'` honestly) would have made the
row honest while leaving the falsifier movable, because the reader never looks at the column that
was made honest. `test_the_acted_on_rate_cannot_be_moved_by_an_agent` measures that reader
directly rather than asserting about it.

WHAT WAS WRONG, and it is worth stating precisely because the old gate LOOKED like an identity
check. `brain.recommendation_human_decider()` and `queue/human_queue/transitions.py::
recommend_accept` between them made three tests: the decider string is non-empty, the decider is
NOT a name in `brain.agent`, and `SWARM_PARENT_TASK` is unset in the process environment. None of
the three asks who is connected, and the second FAILS OPEN BY CONSTRUCTION -- it refuses the names
that ARE agents, so every name that is not one passes, and the agent picks the name. 0222 measured
exactly that from a `brain_runtime` connection: `by='Andrew'` dispatched, `by='zzz-not-a-person'`
dispatched and was booked `actor_type='human'`, and `by='T2'` dispatched too, right up until a
heartbeat put T2 in `brain.agent`.

WHAT IS BEING PROVEN HERE is the shape `engine/tests/test_steering_exclusion.py::
test_the_runtime_login_cannot_write_a_mark_at_all` already proves for the steering mark, applied
to the second place a human's identity is recorded: IDENTITY COMES FROM THE DATABASE, NOT FROM
`by=`. `brain.current_human()` answers from `session_user`, which is fixed at authentication;
`brain_runtime` -- the login every agent surface connects as -- has no row in `brain.human_role`,
cannot read that table and cannot write one.

THE TEST THAT MAKES THE OTHERS WORTH READING is `test_the_operator_login_still_dispatches`. A
suite that only showed refusals would pass against a verb that refuses everybody, which is not a
fixed gate, it is a broken console. So every refusal below is paired with the same acceptance
landing from the operator's own login, on the same recommendation shape, in the same run.

THE RESIDUAL, so this suite is not read as claiming more than it proves: on a `local-attended`
host every credential file lives in one 0700 directory under one UID, so an OS process that can
read the operator's secret can connect as the operator. That is `store/SECRETS.md`'s residual for
every role and migration 20's for `work_item`; it is file permissions, not the database. What is
closed absolutely is the case that exists -- a process holding the `brain_runtime` credential,
which is what every agent surface is configured with, cannot accept a recommendation under any
decider name.

Run:  python3 engine/tests/test_recommendation_human_login.py     (scratch database, never `brain`)
"""

from __future__ import annotations

import os
import sys
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT / "queue"))
sys.path.insert(0, str(ROOT))

os.environ.setdefault("BRAIN_PG_DB", os.environ.get("ENGINE_SCRATCH_DB", "brain_scratch"))

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _scratch_preflight import reconcile                     # noqa: E402
reconcile(os.environ["BRAIN_PG_DB"])
# The runner exports this into every terminal, and `recommend accept` refuses a process that
# carries it. Popping it is what makes the refusals below about the LOGIN rather than about the
# environment variable -- which is the whole point: the environment test is bypassable by any
# process that clears its own environment, and 0222 cleared it.
os.environ.pop("SWARM_PARENT_TASK", None)

import psycopg2                                              # noqa: E402
import store                                                 # noqa: E402
from store import session                                    # noqa: E402
from swarm_engine import transitions as _engine              # noqa: E402,F401  (registers verbs)
import human_queue                                           # noqa: E402,F401  (registers verbs)

SCRATCH = str(ROOT / "engine/bin/scratch-db.sh")
SECRETS = Path(os.environ.get("BRAIN_SECRET_DIR", str(Path.home() / ".brain-postgres-secrets")))

#: The three names the definition of done names, plus the one the old gate DID catch. The first
#: three are the ones that used to dispatch: an unregistered fleet name, an invented name, and the
#: literal string the operator himself uses.
NAMES = [
    ("T2-unregistered", "a fleet name with no brain.agent row -- 0222's first dispatch"),
    ("zzz-not-a-person", "an invented name that is nobody -- 0222's third dispatch"),
    ("operator", "the literal string the operator's own console passes"),
    ("T-registered", "a name the old gate DID catch, kept so the defence in depth is measured"),
]

PASS, FAIL = 0, 0


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


def truth(msg, cond, detail=""):
    ok(msg) if cond else bad(msg, detail)


def eq(msg, got, want):
    ok(msg) if got == want else bad(msg, f"wanted [{want}], got [{got}]")


def refused(fn, *a, **kw) -> str:
    """Run something that must be refused. Returns the message, or '' if it was NOT refused."""
    try:
        fn(*a, **kw)
        return ""
    except Exception as e:                                   # noqa: BLE001
        return str(e) or e.__class__.__name__


def as_role(role: str, sql: str, params=None):
    """One statement on a real login of that role.

    No `store` wrapper on purpose: the gate under test is the DATABASE's, so the test has to be
    able to reach past every guard written in Python. This is the connection an agent process
    already holds in order to call `store.apply` at all.
    """
    conn = psycopg2.connect(**session.dsn(role))
    try:
        conn.set_session(readonly=False, autocommit=False)
        with conn.cursor() as cur:
            cur.execute(sql, params)
            out = cur.fetchall() if cur.description else []
        conn.commit()
        return out
    finally:
        conn.close()


def reset():
    subprocess.run([SCRATCH, "truncate"], check=True, capture_output=True)


def rec(rid) -> dict:
    with store.read("runtime") as s:
        return s.one("SELECT * FROM brain.recommendation WHERE id = %s::bigint", (str(rid),)) or {}


def work_items() -> int:
    with store.read("runtime") as s:
        return int(s.scalar("SELECT count(*) FROM brain.work_item") or 0)


def the_human() -> str:
    """The name the database gives the OPERATOR's connection.

    Asked on the operator login and not on the runtime one, which is not a detail: from the
    runtime login `brain.current_human()` returns NULL by design, and a helper that asked there
    would report "nobody is the operator" on a correctly provisioned host and skip the suite.
    """
    try:
        rows = as_role("operator", "SELECT brain.current_human()")
    except Exception:                                        # noqa: BLE001 -- no credential here
        return ""
    return (rows[0][0] if rows and rows[0] else "") or ""


def a_drafted_option(text="restate the 2025 margin table") -> dict:
    """A real recommendation about a real subject, which is what 0222 dispatched."""
    subject = store.apply("post", title="Acme margin restate", lane="data",
                          workdir="/tmp", agent_claimable=True)
    return store.apply("recommend", text=text, proposed_action=f"execute: {text}",
                       subject_type="work_item", subject_id=subject["id"],
                       template_id="playbook-restate-a-metric")


def provisioned() -> bool:
    """Is there an operator login on this host at all? Reported, never skipped silently."""
    return (SECRETS / "brain-postgres-role-operator").is_file()


# ------------------------------------------------------------------ through the verb

def test_the_runtime_login_cannot_accept_under_any_name():
    """The call an agent process makes. Four names, one refusal each, nothing spawned.

    This is 0222's measurement re-run: same verb, same names, same absent SWARM_PARENT_TASK.
    """
    reset()
    store.apply("heartbeat", agent="T-registered", status="working")   # so name 4 is a real agent
    for name, why in NAMES:
        r = a_drafted_option()
        before = work_items()
        msg = refused(store.apply, "recommend accept", id=r["id"], by=name, lane="data")
        truth(f"by={name!r} is refused ({why})", msg != "", "NOT REFUSED -- it dispatched")
        eq(f"and {name!r} spawned no work item", work_items(), before)
        eq(f"and the recommendation is still open after by={name!r}", rec(r["id"])["state"],
           "open")
    # The three that used to dispatch are refused BY THE LOGIN, and the sentence has to say so:
    # a refusal for the old reason would mean the fails-open test is still the gate.
    r = a_drafted_option()
    msg = refused(store.apply, "recommend accept", id=r["id"], by="zzz-not-a-person", lane="data")
    truth("and the refusal is about the connection, not about the name",
          "does not know as a human" in msg, msg[:200])


def test_a_name_absent_from_brain_agent_is_no_longer_a_way_through():
    """The defect stated as its own test: the old gate's PASS condition is now a refusal.

    `zzz-not-a-person` satisfies every test the old gate made -- non-empty, not in `brain.agent`,
    and this process has no SWARM_PARENT_TASK. It dispatched on 2026-08-19 and it must not now.
    """
    reset()
    r = a_drafted_option()
    with store.read("runtime") as s:
        n = int(s.scalar("SELECT count(*) FROM brain.agent WHERE name = %s",
                         ("zzz-not-a-person",)) or 0)
    eq("the name really is absent from brain.agent, which is what the old gate tested", n, 0)
    eq("and SWARM_PARENT_TASK really is unset, which is the other test it made",
       os.environ.get("SWARM_PARENT_TASK", ""), "")
    msg = refused(store.apply, "recommend accept", id=r["id"], by="zzz-not-a-person", lane="data")
    truth("it is refused anyway", msg != "", "NOT REFUSED -- the gate still fails open")
    eq("and nothing was spawned", rec(r["id"])["spawned_work_item"], None)


# ------------------------------------------------------------------ straight at the table

def test_the_runtime_login_cannot_accept_straight_at_the_table():
    """Around the verb entirely. THIS is the trigger, and the verb's refusals prove nothing here.

    A gate that exists only in Python is a gate one wrong branch away from being absent, which is
    why `0007_queue.sql` put one in the database in the first place. Migration 32 is what makes
    that one an identity check.
    """
    reset()
    # The fourth name has to actually BE an agent here or the label lies: `reset()` truncates
    # `brain.agent`, so without this the "defence in depth" case is just a fourth unregistered
    # name and the old `brain.agent` test is never exercised at the table at all.
    store.apply("heartbeat", agent="T-registered", status="working")
    for name, why in NAMES:
        r = a_drafted_option()
        msg = refused(as_role, "runtime",
                      "UPDATE brain.recommendation SET state='accepted', decided_at=now(), "
                      "decided_by=%s, actor_type='human' WHERE id=%s::bigint",
                      (name, str(r["id"])))
        truth(f"brain_runtime CANNOT write decided_by={name!r} at the table ({why})",
              msg != "", "NOT REFUSED -- rowcount 1, which is 0222's measurement unchanged")
        eq(f"and the row is untouched after the {name!r} attempt", rec(r["id"])["state"], "open")
    r = a_drafted_option()
    msg = refused(as_role, "runtime",
                  "UPDATE brain.recommendation SET state='accepted', decided_at=now(), "
                  "decided_by='zzz-not-a-person', actor_type='human' WHERE id=%s::bigint",
                  (str(r["id"]),))
    truth("and the database says why, naming the login it refused",
          "not a human login" in msg and "brain_runtime" in msg, msg[:220])


def test_the_contrast_the_blocker_was_named_for_is_gone():
    """0222's finding was a CONTRAST measured in one process on one connection:

        UPDATE brain.work_item      SET actor_type='human' ...  -> REFUSED (migration 20)
        UPDATE brain.recommendation SET decided_by=..., ...     -> rowcount 1

    Both halves are re-measured here. The point is not that the second is refused; it is that it
    is refused for the SAME REASON, so the two tables now answer the same question the same way.
    """
    reset()
    r = a_drafted_option()
    w = refused(as_role, "runtime",
                "INSERT INTO brain.work_item (title, lane, actor_type) "
                "VALUES ('forged by an agent', 'queue', 'human')")
    q = refused(as_role, "runtime",
                "UPDATE brain.recommendation SET state='accepted', decided_at=now(), "
                "decided_by='Andrew', actor_type='human' WHERE id=%s::bigint", (str(r["id"]),))
    truth("brain.work_item still refuses actor_type=human from brain_runtime", w != "",
          "NOT REFUSED -- migration 20 has regressed")
    truth("brain.recommendation now refuses it too", q != "", "NOT REFUSED")
    truth("and both refusals are the same sentence about the same thing",
          "not a human login" in w and "not a human login" in q, f"work_item: {w[:90]} || "
                                                                f"recommendation: {q[:90]}")


# ------------------------------------------------------------------ the console still works

def test_the_operator_login_still_dispatches():
    """The half that makes the refusals meaningful. Same shape, from the operator's own login."""
    reset()
    r = a_drafted_option()
    before = work_items()
    out = store.apply("recommend accept", id=r["id"], by=the_human(), lane="data",
                      as_operator=True)
    row = rec(r["id"])
    eq("the acceptance lands", row["state"], "accepted")
    eq("it spawned exactly one work item", work_items(), before + 1)
    truth("and the recommendation is linked to it",
          bool(row["spawned_work_item"]) and row["spawned_work_item"] == out["spawned_work_item"],
          str(dict(row)))
    eq("booked as a human act", str(row["actor_type"]), "human")
    eq("and the decider recorded is the name the DATABASE gives this connection",
       row["decided_by"], the_human())


def test_the_recorded_decider_is_the_databases_answer_not_the_callers():
    """From the operator's login, claiming to be somebody else is refused rather than stored.

    Same rule `steering.py::_the_human` already holds for the steering mark. `decided_by` is the
    record of who chose this work and it is the input to
    `engine/swarm_engine/accept.py:disagreement_report`; a name the operator typed over his own
    is a false record, which is a quieter failure than a forged one and no more usable.
    """
    reset()
    r = a_drafted_option()
    msg = refused(store.apply, "recommend accept", id=r["id"], by="zzz-not-a-person",
                  lane="data", as_operator=True)
    truth("a decider that is not this connection is refused even from the operator login",
          "the database says the connection is" in msg, msg[:200] or "NOT REFUSED")
    eq("and the recommendation is still open", rec(r["id"])["state"], "open")

    # And at the table, with the application role out of the picture: the operator's OWN login
    # cannot write a decider that is not itself either.
    msg = refused(as_role, "operator",
                  "UPDATE brain.recommendation SET state='accepted', decided_at=now(), "
                  "decided_by='zzz-not-a-person', actor_type='human' WHERE id=%s::bigint",
                  (str(r["id"]),))
    truth("the TABLE refuses it too, naming both names",
          "zzz-not-a-person" in msg and the_human() in msg, msg[:220] or "NOT REFUSED")


def test_the_console_passes_the_keyword_that_opens_that_login():
    """The regression this suite cannot otherwise see, and it is one deleted keyword wide.

    `store/transitions.py::_login_for` reads `as_operator` out of the kwargs `store.apply` was
    CALLED with, not out of the transition's signature default. So a console that stops passing it
    does not get a weaker acceptance, it gets NO acceptance -- and the failure appears at the
    operator's keyboard, on the one act this whole gate exists to let him perform. Asserted on the
    source for the same reason `web/tests/test_option_dispatch.py` asserts route (f) there.
    """
    src = (ROOT / "web/actions.py").read_text()
    for fname in ("dispatch_option", "recommend"):
        start = src.index(f"def {fname}(")
        nxt = src.find("\ndef ", start + 1)
        block = src[start: nxt if nxt > 0 else len(src)]
        truth(f"web/actions.py::{fname} opens the operator login for the acceptance",
              "as_operator" in block, "the console will be refused by its own database")
    cli = (ROOT / "queue/human_queue/cli.py").read_text()
    start = cli.index("def cmd_accept(")
    nxt = cli.find("\ndef ", start + 1)
    truth("and so does `queue recommend accept` on the CLI",
          "as_operator=True" in cli[start: nxt if nxt > 0 else len(cli)],
          "the operator's terminal door would be refused")


# ------------------------------------------------------------------ the other half: rejection

def a_rejectable_option(template="playbook-dead-one") -> dict:
    """A recommendation carrying a template_id, because the template is what a rejection prunes."""
    return store.apply("recommend", text="rewrite the charter", template_id=template,
                       proposed_action="execute: rewrite the charter")


def acted_on_row(template: str) -> dict:
    """What `brain.queue_acted_on` -- the falsifier -- says about one template, right now."""
    with store.read("runtime") as s:
        return dict(s.one("SELECT * FROM brain.queue_acted_on WHERE template_id = %s",
                          (template,)) or {})


def test_the_runtime_login_cannot_reject_under_any_name():
    """The call an agent process makes, through the verb. Four names, one refusal each.

    Same four names as the acceptance half, and the third of them -- the literal string
    `operator` -- is not a hypothetical here: it was `--by`'s DEFAULT on `queue reject` until this
    task, so a bare `queue reject rN --reason x` from any process recorded the operator as the
    rejecter.
    """
    reset()
    store.apply("heartbeat", agent="T-registered", status="working")   # so name 4 is a real agent
    for name, why in NAMES:
        r = a_rejectable_option()
        msg = refused(store.apply, "recommend reject", id=r["id"], by=name, reason="not a problem")
        truth(f"reject by={name!r} is refused ({why})", msg != "", "NOT REFUSED -- it rejected")
        eq(f"and the recommendation is still open after by={name!r}", rec(r["id"])["state"],
           "open")
    r = a_rejectable_option()
    msg = refused(store.apply, "recommend reject", id=r["id"], by="zzz-not-a-person",
                  reason="not a problem")
    truth("and the refusal is about the connection, not about the name",
          "does not know as a human" in msg, msg[:200])


def test_the_runtime_login_cannot_reject_straight_at_the_table():
    """Around the verb entirely. THIS is trigger `recommendation_rejection_decider`.

    The verb's refusals prove nothing about the database, and until migration 33 the database had
    nothing to say about the `rejected` state at all: `0015_recommendation_human_login.sql` does
    not contain the string.
    """
    reset()
    store.apply("heartbeat", agent="T-registered", status="working")
    for name, why in NAMES:
        r = a_rejectable_option()
        msg = refused(as_role, "runtime",
                      "UPDATE brain.recommendation SET state='rejected', decided_at=now(), "
                      "decided_by=%s, actor_type='human' WHERE id=%s::bigint",
                      (name, str(r["id"])))
        truth(f"brain_runtime CANNOT write a rejection by {name!r} at the table ({why})",
              msg != "", "NOT REFUSED -- rowcount 1")
        eq(f"and the row is untouched after the {name!r} attempt", rec(r["id"])["state"], "open")
    # The actor_type dodge: leaving the column NULL is what the verb did before this task, so a
    # gate that only fired on actor_type='human' would be walked straight around by omitting it.
    r = a_rejectable_option()
    msg = refused(as_role, "runtime",
                  "UPDATE brain.recommendation SET state='rejected', decided_at=now(), "
                  "decided_by='operator' WHERE id=%s::bigint", (str(r["id"]),))
    truth("and writing NO actor_type at all -- the pre-0313 shape -- is refused too",
          msg != "", "NOT REFUSED -- the gate is one omitted column wide")
    # An INSERT born rejected reaches `brain.queue_acted_on` by the same route an UPDATE does,
    # because that view GROUPs BY template_id and counts raised alongside rejected.
    msg = refused(as_role, "runtime",
                  "INSERT INTO brain.recommendation (text, requires_human, state, decided_at, "
                  "decided_by, actor_type, template_id) VALUES ('forged', true, 'rejected', "
                  "now(), 'operator', 'human', 'playbook-dead-one')")
    truth("and a recommendation cannot be BORN rejected from the runtime login either",
          msg != "", "NOT REFUSED -- the INSERT arm is open")


def test_the_acted_on_rate_cannot_be_moved_by_an_agent():
    """THE MEASUREMENT, not the row. The reason horn 2 was not enough, measured on the reader.

    `brain.queue_acted_on` counts `state = 'rejected'` and groups by `template_id`, reading
    neither `decided_by` nor `actor_type`. That is why "record actor_type honestly and leave the
    verb open" was rejected as the fix: it makes the row honest and leaves this number movable.
    Everything else in this file asserts about rows. This asserts about the falsifier.
    """
    reset()
    r = a_rejectable_option()
    before = acted_on_row("playbook-dead-one")
    eq("the template starts at 0 rejected", int(before.get("rejected") or 0), 0)
    eq("with the one raised recommendation counted", int(before.get("raised") or 0), 1)
    refused(store.apply, "recommend reject", id=r["id"], by="operator", reason="dead playbook")
    refused(as_role, "runtime",
            "UPDATE brain.recommendation SET state='rejected', decided_at=now(), "
            "decided_by='operator', actor_type='human' WHERE id=%s::bigint", (str(r["id"]),))
    after = acted_on_row("playbook-dead-one")
    eq("and after an agent tried both routes it is still 0", int(after.get("rejected") or 0), 0)
    eq("so the falsifier this layer is judged on did not move", after.get("decided_rate"),
       before.get("decided_rate"))


def test_the_operator_login_still_rejects():
    """The half that makes the refusals meaningful. A gate that refuses everybody is a broken verb.

    Paired with `test_the_operator_login_still_dispatches` above: same shape, other verb, same run.
    """
    reset()
    r = a_rejectable_option()
    out = store.apply("recommend reject", id=r["id"], by=the_human(), reason="dead playbook",
                      as_operator=True)
    row = rec(r["id"])
    eq("the rejection lands", row["state"], "rejected")
    eq("the verb reports the same decider it wrote", out["decided_by"], row["decided_by"])
    eq("the decider recorded is the name the DATABASE gives this connection", row["decided_by"],
       the_human())
    eq("booked as a human act, where the pre-0313 verb left the column NULL",
       str(row["actor_type"]), "human")
    truth("and decided_at is recorded", row["decided_at"] is not None, str(dict(row)))
    eq("and the falsifier moved, which is what a real rejection is for",
       int(acted_on_row("playbook-dead-one").get("rejected") or 0), 1)


def test_the_recorded_rejecter_is_the_databases_answer_not_the_callers():
    """From the operator's own login, rejecting under somebody else's name is refused."""
    reset()
    r = a_rejectable_option()
    msg = refused(store.apply, "recommend reject", id=r["id"], by="zzz-not-a-person",
                  reason="dead playbook", as_operator=True)
    truth("a rejecter that is not this connection is refused even from the operator login",
          "the database says the connection is" in msg, msg[:200] or "NOT REFUSED")
    eq("and the recommendation is still open", rec(r["id"])["state"], "open")
    msg = refused(as_role, "operator",
                  "UPDATE brain.recommendation SET state='rejected', decided_at=now(), "
                  "decided_by='zzz-not-a-person', actor_type='human' WHERE id=%s::bigint",
                  (str(r["id"]),))
    truth("the TABLE refuses it too, naming both names",
          "zzz-not-a-person" in msg and the_human() in msg, msg[:220] or "NOT REFUSED")


def test_the_reject_call_sites_pass_the_keyword_that_opens_that_login():
    """One deleted keyword wide, on the reject half now as well as the accept half.

    `store/transitions.py::_login_for` reads `as_operator` out of the kwargs `store.apply` was
    CALLED with, not out of the transition's signature default, so a call site that stops passing
    it does not get a weaker rejection -- it gets none, at the operator's keyboard.
    """
    src = (ROOT / "web/actions.py").read_text()
    start = src.index("def recommend(")
    nxt = src.find("\ndef ", start + 1)
    block = src[start: nxt if nxt > 0 else len(src)]
    truth("web/actions.py::recommend opens the operator login on BOTH halves",
          "as_operator=True)" in block and "if accept else {}" not in block,
          "it is still accept-only, so the console's Reject button is refused by its own database")
    cli = (ROOT / "queue/human_queue/cli.py").read_text()
    start = cli.index("def cmd_reject(")
    nxt = cli.find("\ndef ", start + 1)
    truth("and so does `queue recommend reject` on the CLI",
          "as_operator=True" in cli[start: nxt if nxt > 0 else len(cli)],
          "the operator's terminal door would be refused")
    truth("and --by no longer defaults to the literal string 'operator', which WAS the forgery",
          'add_argument("--by", default="operator")' not in
          cli[cli.index('add("reject", cmd_reject'): cli.index('add("classify", cmd_classify')],
          "a bare `queue reject` still records the operator as the rejecter")


# ------------------------------------------------------------------ the claim

def test_a_drafted_option_cannot_dispatch_without_a_human():
    """The summary assertion. It restates nothing: it fails if any check above failed."""
    truth("V2 invariant 1 holds: a drafted option cannot dispatch without a human choosing it",
          FAIL == 0, f"{FAIL} assertion(s) above failed, so the invariant is NOT met")
    truth("and its other half holds: a recommendation is REJECTED by a human too, so the "
          "acted-on rate is not a number the measured can move",
          FAIL == 0, f"{FAIL} assertion(s) above failed")


def main():
    print("test_recommendation_human_login.py  --  V2 invariant 1, and the login that now holds it")
    print(f"  store: {os.environ['BRAIN_PG_DB']} (scratch). The live store is untouched.\n")
    if not provisioned():
        print("  NOT RUN: no operator credential on this host. Migration 20 is two steps and the\n"
              "  second one is store/bin/provision-operator.sh --db "
              f"{os.environ['BRAIN_PG_DB']}. Every check below\n"
              "  would be about an absent credential rather than about the gate, and the half\n"
              "  that proves the console still works cannot run at all. Refusing to report green.")
        return 1
    if not the_human():
        print("  NOT RUN: the operator secret exists but brain.human_role maps no login on this\n"
              f"  database. Provision it:  store/bin/provision-operator.sh --db "
              f"{os.environ['BRAIN_PG_DB']}")
        return 1
    for fn in (test_the_runtime_login_cannot_accept_under_any_name,
               test_a_name_absent_from_brain_agent_is_no_longer_a_way_through,
               test_the_runtime_login_cannot_accept_straight_at_the_table,
               test_the_contrast_the_blocker_was_named_for_is_gone,
               test_the_operator_login_still_dispatches,
               test_the_recorded_decider_is_the_databases_answer_not_the_callers,
               test_the_console_passes_the_keyword_that_opens_that_login,
               test_the_runtime_login_cannot_reject_under_any_name,
               test_the_runtime_login_cannot_reject_straight_at_the_table,
               test_the_acted_on_rate_cannot_be_moved_by_an_agent,
               test_the_operator_login_still_rejects,
               test_the_recorded_rejecter_is_the_databases_answer_not_the_callers,
               test_the_reject_call_sites_pass_the_keyword_that_opens_that_login):
        print(f"\n{fn.__name__}")
        print(f"  {(fn.__doc__ or '').strip().splitlines()[0]}")
        fn()
    print("\n=== the claim ===")
    test_a_drafted_option_cannot_dispatch_without_a_human()
    print(f"\n{PASS} passed, {FAIL} failed")
    if PASS + FAIL == 0:                                     # DENOMINATOR
        print("0 comparisons made. A verdict over an empty set is not a pass. Every check above "
              "runs through `reset()` and a scratch connection; if none ran, this suite measured "
              "an absent database rather than a gate.")
        return 2
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
