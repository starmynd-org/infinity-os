#!/usr/bin/env python3
"""Lane E, row `0384`: several named humans on ONE self-hosted instance.

Every assertion here is about what the store REFUSES, and every refusal is watched FAILING before
it is watched passing, because a guard nobody has watched fail is not a guard.

WHAT IT PROVES, and each of these is a numbered scene below:

  1  TWO HUMANS, ONE TRAIL. `operator` and a second provisioned human each accept their own work
     and the row records the name the DATABASE gives that connection, not the one the caller
     typed.

  2  THE ACCEPTANCE GATE ON `brain.work_item` WAS A STRING AND FAILED OPEN. Scene 2 DROPS
     migration 36's trigger and attacks the column from `brain_runtime` with six names: 6 of 6
     accepted, including `zzz-not-a-person` and `operator`. It restores the trigger and runs the
     same six: 1 of 6, and the one is `brain.auto_acceptor()`, which claims no human.

  3  A HUMAN MAY NOT RECORD A COLLEAGUE'S ACCEPTANCE. Refused by the verb, and refused AGAIN by
     the trigger with the verb's own check bypassed.

  4  THE AUTOMATIC CARVE-OUT IS EXACTLY ONE STRING, FROM ONE PRODUCER. `brain.auto_acceptor()`
     is byte-equal to what `engine/swarm_engine/accept.py` writes, and a near miss is refused.

  5  ONE INSERT INTO `brain.agent` LOCKED THE OPERATOR OUT OF HIS OWN CONSOLE. Scene 5 drops
     migration 38's trigger and demonstrates the denial of service, then restores it.

  6  WHOSE QUEUE IS WHOSE, and it changes nothing today: with zero assignments,
     `brain.queue_open_for('operator')` is `brain.queue_open` row for row, with BOTH counts on
     the line.

  7  WHICH HUMAN A PROCESS IS. The resolver's order, and a slug with no credential failing CLOSED
     rather than becoming the operator.

  8  `store/session.py:ROLES` IS STILL FIVE, and the statement timeout a named human gets through
     Python agrees with the one migration 20 sets in the database.

  9  THE THREE ATTRIBUTION READERS still mean what they meant. Asserted against what each one
     actually reads, which is not what the lane brief assumed for two of the three.

NEEDS a scratch database at ledger 38 or above, an operator credential, and a second provisioned
human. Without any of them it prints NOT RUN with the reason and exits 77, rather than reporting
a green over a store it could not exercise.

    ENGINE_SCRATCH_DB=brain_lane_e engine/bin/scratch-db.sh ensure
    ENGINE_SCRATCH_DB=brain_lane_e BRAIN_PG_DB=brain_lane_e \\
        python3 engine/bin/swarm admin human provision lane-e-two
    ENGINE_SCRATCH_DB=brain_lane_e BRAIN_PG_DB=brain_lane_e \\
        python3 engine/tests/test_multi_user_subject.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT / "queue"))
sys.path.insert(0, str(ROOT))

os.environ.setdefault("BRAIN_PG_DB", os.environ.get("ENGINE_SCRATCH_DB", "brain_scratch"))
os.environ["BRAIN_AFTER_COMMIT_HOOKS"] = "0"
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _scratch_preflight import reconcile                        # noqa: E402
reconcile(os.environ["BRAIN_PG_DB"])
os.environ.pop("SWARM_PARENT_TASK", None)
os.environ.pop("SWARM_AGENT", None)
os.environ.pop("BRAIN_HUMAN", None)

import psycopg2                                                 # noqa: E402
import store                                                    # noqa: E402
from store import session                                       # noqa: E402
from store.session import dsn                                   # noqa: E402
from swarm_engine import accept as accept_mod                   # noqa: E402
from swarm_engine.transitions import VerbError                  # noqa: E402
import human_queue.transitions as _q                            # noqa: F401,E402
from human_queue import reads as qreads                         # noqa: E402
from human_queue.transitions import QueueError                  # noqa: E402

DB = os.environ["BRAIN_PG_DB"]
SECOND = os.environ.get("LANE_E_SECOND_HUMAN", "lane-e-two")
PASS = FAIL = SKIP = 0

#: The six. Every one is a forgery except that `operator` is a real human on this store and
#: `auto-accept` is the rule's declared sentinel. `zzz-not-a-person` is the exact string the
#: 2026-08-16 adversarial run used to dispatch real work booked `actor_type='human'`.
FORGED = ["zzz-not-a-person", "Andrew", "operator", "you", "auto-accept", "T-lane-e"]


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


def skip(msg):
    global SKIP
    SKIP += 1
    print(f"  NOT RUN  {msg}")


def eq(msg, got, want):
    ok(msg) if got == want else bad(msg, f"wanted [{want}], got [{got}]")


def truth(msg, cond, detail=""):
    ok(msg) if cond else bad(msg, detail)


# ------------------------------------------------------------------ raw connections
#
# Raw psycopg2 rather than `store`, on purpose and only inside this suite: the point of most of
# these scenes is what a connection can do OUTSIDE a verb, and asking that through the verb would
# measure the verb instead of the database.


def _conn(role, human=None):
    c = psycopg2.connect(**dsn(role, None, human))
    c.set_session(readonly=False, autocommit=True)
    return c


def _sql(conn, q, params=None):
    with conn.cursor() as cur:
        cur.execute(q, params)
        return cur.fetchall() if cur.description else []


def _try(conn, q, params=None):
    """(True, rows) or (False, the server's first line). Never raises."""
    try:
        return True, _sql(conn, q, params)
    except psycopg2.Error as exc:
        return False, str(exc).splitlines()[0]


def _superuser():
    """A bootstrap superuser connection, or None. Scratch-only, and only for the two acts no
    non-superuser login has: DDL on another role's table and an INSERT into brain.human_role.

    `brain_owner` is NOCREATEROLE and holds SELECT and DELETE on brain.human_role and nothing
    else, which is migration 20's design and is not something to relax for a test. So the one
    assertion that needs an INSERT there says NOT RUN rather than passing for the wrong reason.
    """
    ref = Path(os.environ.get("BRAIN_SECRET_DIR",
                              str(Path.home() / ".brain-postgres-secrets"))) / \
        "brain-postgres-bootstrap-superuser"
    try:
        pw = ref.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not pw:
        return None
    try:
        c = psycopg2.connect(host=os.environ.get("BRAIN_PG_HOST", "127.0.0.1"),
                             port=int(os.environ.get("BRAIN_PG_PORT", "5432")),
                             dbname=DB, user="postgres", password=pw,
                             options="-c search_path=brain,public", connect_timeout=5)
        c.set_session(readonly=False, autocommit=True)
        return c
    except psycopg2.Error:
        return None


def _seed(conn, title, state="done"):
    return _sql(conn, "INSERT INTO brain.work_item (title, lane, state, posted_by, reversibility)"
                      " VALUES (%s, 'lane-e', %s, 'T-lane-e', 'high') RETURNING id",
                (title, state))[0][0]


# ------------------------------------------------------------------ preconditions


def _ledger() -> int:
    with store.read() as s:
        return int(s.scalar("SELECT max(version) FROM brain.schema_migration") or 0)


def _have(human=None) -> bool:
    """Can this host connect AS that human AND does this DATABASE know them as one?

    THE SECOND HALF WAS MISSING AND IT IS THE HALF THIS SUITE NEEDS. Opening the connection only
    proves a login exists, and a Postgres login is CLUSTER-WIDE while `brain.human_role` is
    PER-DATABASE. So on any fresh scratch store -- which is every store this suite runs against --
    the connection succeeded, this returned True, the precondition passed, and scene 1 then failed
    with `wanted [lane-e-two], got [None]`: a MISSING FIXTURE reported as a code defect, on every
    run, in a suite whose entire subject is that two humans are distinguishable.

    That is one of the eight failing signatures in the engine suite (decision 10) and it is the
    same shape as most of the others: the guard tested a PROXY it could reach rather than the
    PROPERTY the scenes need. `brain.current_human()` IS the property -- it is what every scene
    below compares against -- so ask it, and let the preflight say NOT RUN with the provisioning
    command rather than let three scenes fail for a reason that is not about the code.
    """
    want = human or "operator"
    try:
        c = _conn("operator", human)
        try:
            with c.cursor() as cur:
                cur.execute("SELECT brain.current_human()")
                got = (cur.fetchone() or [None])[0]
        finally:
            c.close()
    except Exception:                                           # noqa: BLE001
        return False
    return got == want


def _preconditions() -> str:
    if DB in ("brain", ""):
        return (f"BRAIN_PG_DB={DB!r}. This suite drops triggers and forges acceptances; it does "
                f"not run against the live store. Build one: ENGINE_SCRATCH_DB=brain_lane_e "
                f"engine/bin/scratch-db.sh ensure")
    try:
        v = _ledger()
    except Exception as exc:                                    # noqa: BLE001
        return f"the store at {DB!r} is not reachable ({exc.__class__.__name__}: {exc})"
    if v < 38:
        return (f"{DB} is at ledger {v} and this suite needs 38 or above (lane E's migrations 36, "
                f"37 and 38). Apply them: engine/bin/scratch-db.sh ensure")
    if not _have():
        return (f"this host holds no operator credential, so nothing here can be exercised from a "
                f"human login. store/bin/provision-operator.sh --db {DB}")
    if not _have(SECOND):
        return (f"no credential for the second human {SECOND!r}. Two humans is the whole subject "
                f"of this lane and one human proves nothing about it: "
                f"BRAIN_PG_DB={DB} swarm admin human provision {SECOND}")
    return ""


# ------------------------------------------------------------------ 1. two humans, one trail


def scene_1_two_humans_one_trail(op, two, rt):
    print("\n-- 1. two humans, one trail. Each acceptance records the DATABASE's name")
    eq("the operator login is the human `operator`",
       _sql(op, "SELECT brain.current_human()")[0][0], "operator")
    eq(f"the second login is the human {SECOND!r}",
       _sql(two, "SELECT brain.current_human()")[0][0], SECOND)
    truth("they are two different session_users",
          _sql(op, "SELECT session_user")[0][0] != _sql(two, "SELECT session_user")[0][0])

    a, b = _seed(rt, "lane E scene 1 a"), _seed(rt, "lane E scene 1 b")
    store.apply("accept work", id=a, by="operator", as_operator=True)
    store.apply("accept work", id=b, by=SECOND, as_operator=True, as_human=SECOND)
    got = dict(_sql(rt, "SELECT id, accepted_by FROM brain.work_item WHERE id = ANY(%s)",
                    ([a, b],)))
    eq(f"{a} records operator", got.get(a), "operator")
    eq(f"{b} records {SECOND}", got.get(b), SECOND)
    n = _sql(rt, "SELECT count(DISTINCT accepted_by) FROM brain.work_item "
                 "WHERE accepted_by IS NOT NULL AND accepted_by <> ''")[0][0]
    truth(f"the trail carries {n} distinct acceptor(s) over 2 acceptances in this scene", n >= 2)


# ------------------------------------------------------- 2. the gate, watched failing first


def _attack(rt, tag):
    """Every forged name against brain.work_item.accepted_by, from brain_runtime. Returns the
    names that landed. The denominator is always len(FORGED)."""
    landed = []
    for name in FORGED:
        tid = _seed(rt, f"lane E {tag} {name}")
        good, out = _try(rt, "UPDATE brain.work_item SET accepted_at = now(), accepted_by = %s "
                             "WHERE id = %s RETURNING accepted_by", (name, tid))
        if good and out:
            landed.append(name)
    return landed


def scene_2_the_gate_fails_open_without_it(su, rt):
    print(f"\n-- 2. the gate, watched FAILING first ({len(FORGED)} forged names, "
          f"from brain_runtime)")
    # brain.agent is emptied so migration 22's `is_agent` rule cannot be credited with a refusal
    # that belongs to migration 36. Measuring one guard while another is quietly doing the work
    # is how a guard gets believed for the wrong reason.
    _sql(su, "DELETE FROM brain.agent")

    _sql(su, "DROP TRIGGER IF EXISTS work_item_acceptance_is_a_login ON brain.work_item")
    before = _attack(rt, "scene2-without")
    print(f"        WITHOUT migration 36: {len(before)} of {len(FORGED)} forged names accepted "
          f"{sorted(before)}")
    eq(f"without the trigger the gate fails OPEN on {len(FORGED)} of {len(FORGED)} names",
       len(before), len(FORGED))
    truth("including the exact string the 2026-08-16 adversarial run used",
          "zzz-not-a-person" in before)
    truth("including `operator`, so an agent could record the operator's own decision",
          "operator" in before)

    _sql(su, """CREATE TRIGGER work_item_acceptance_is_a_login
                  BEFORE INSERT OR UPDATE OF accepted_by, accepted_at ON brain.work_item
                  FOR EACH ROW EXECUTE FUNCTION brain.work_item_acceptance_is_a_login()""")
    after = _attack(rt, "scene2-with")
    print(f"        WITH migration 36:    {len(after)} of {len(FORGED)} forged names accepted "
          f"{sorted(after)}")
    eq(f"with the trigger, 1 of {len(FORGED)} lands", len(after), 1)
    eq("and the one that lands is the declared sentinel, which claims no human",
       after, ["auto-accept"])


def scene_3_a_colleague_is_not_you(two, rt, op):
    print("\n-- 3. a human may not record a COLLEAGUE's acceptance")
    tid = _seed(rt, "lane E scene 3")
    try:
        store.apply("accept work", id=tid, by="operator", as_operator=True, as_human=SECOND)
        bad("the verb refuses a colleague's name", "it accepted")
    except (VerbError, psycopg2.Error) as exc:
        truth("the verb refuses a colleague's name", "operator" in str(exc), str(exc)[:120])
    eq("nothing landed", _sql(rt, "SELECT accepted_at FROM brain.work_item WHERE id = %s",
                              (tid,))[0][0], None)

    # THE SAME REFUSAL, WITH THE VERB OUT OF THE WAY. A gate that exists only in Python is a gate
    # one bug away from being absent, which is the argument migration 22 and 0015 both make.
    good, out = _try(two, "UPDATE brain.work_item SET accepted_at = now(), "
                          "accepted_by = 'operator' WHERE id = %s", (tid,))
    truth("and the DATABASE refuses it too, with the Python check bypassed", not good, str(out))
    good2, _ = _try(two, "UPDATE brain.work_item SET accepted_at = now(), accepted_by = %s "
                         "WHERE id = %s", (SECOND, tid))
    truth(f"while {SECOND} accepting under his OWN name lands", good2)


def scene_4_one_sentinel_one_producer(rt):
    print("\n-- 4. the automatic carve-out is exactly one string, from one producer")
    from_db = _sql(rt, "SELECT brain.auto_acceptor()")[0][0]
    src = (ROOT / "engine" / "swarm_engine" / "accept.py").read_text(encoding="utf-8")
    truth("engine/swarm_engine/accept.py writes brain.auto_acceptor() rather than a literal",
          "accepted_by = brain.auto_acceptor()" in src,
          "a second spelling of a sentinel is how a narrow exemption silently widens")
    eq("and the trigger exempts that same one value", from_db, "auto-accept")
    tid = _seed(rt, "lane E scene 4")
    good, _ = _try(rt, "UPDATE brain.work_item SET accepted_at = now(), "
                       "accepted_by = 'auto-accepted' WHERE id = %s", (tid,))
    truth("a NEAR MISS ('auto-accepted') is refused, so the exemption is equality not a prefix",
          not good)


# ------------------------------------------------- 5. one INSERT locked the operator out


def scene_5_the_namespace_denial_of_service(su, op, rt):
    print("\n-- 5. one INSERT into brain.agent locked the operator out, watched FAILING first")
    _sql(su, "DELETE FROM brain.agent WHERE name = 'operator'")
    _sql(su, "DROP TRIGGER IF EXISTS agent_name_is_not_a_human ON brain.agent")

    tid = _seed(rt, "lane E scene 5 before")
    good, _ = _try(op, "UPDATE brain.work_item SET accepted_at = now(), accepted_by = 'operator' "
                       "WHERE id = %s", (tid,))
    truth("before the attack, the operator accepts his own work", good)

    poisoned, _ = _try(rt, "INSERT INTO brain.agent (name) VALUES ('operator') "
                           "ON CONFLICT (name) DO NOTHING")
    truth("WITHOUT migration 38, brain_runtime registers an agent called `operator`", poisoned)
    tid2 = _seed(rt, "lane E scene 5 after")
    good2, why = _try(op, "UPDATE brain.work_item SET accepted_at = now(), "
                          "accepted_by = 'operator' WHERE id = %s", (tid2,))
    truth("and the OPERATOR is then refused on his own login, permanently", not good2, str(why))
    humans = _sql(rt, "SELECT count(*) FROM brain.human_roster()")[0][0]
    cap = _sql(rt, "SELECT brain.human_login_ceiling()")[0][0]
    print(f"        the whole attack: 1 INSERT from brain_runtime, cost 1 statement, "
          f"blast radius {humans} of {humans} human logins on this store and up to {cap} "
          f"under the stated ceiling")

    _sql(su, "DELETE FROM brain.agent WHERE name = 'operator'")
    _sql(su, """CREATE TRIGGER agent_name_is_not_a_human
                  BEFORE INSERT OR UPDATE OF name ON brain.agent
                  FOR EACH ROW EXECUTE FUNCTION brain.agent_name_is_not_a_human()""")
    again, why2 = _try(rt, "INSERT INTO brain.agent (name) VALUES ('operator')")
    truth("WITH migration 38, the same INSERT is refused", not again, str(why2))
    tid3 = _seed(rt, "lane E scene 5 restored")
    good3, _ = _try(op, "UPDATE brain.work_item SET accepted_at = now(), "
                        "accepted_by = 'operator' WHERE id = %s", (tid3,))
    truth("and the operator accepts his own work again", good3)

    # THE OTHER DIRECTION: an accident rather than an attack. It needs an INSERT into
    # brain.human_role, which no non-superuser login has and none should, so it runs over the
    # bootstrap connection or says NOT RUN. Passing this by way of a permission denied would be
    # the guard being credited for a refusal that belongs to migration 20's grants.
    _sql(su, "INSERT INTO brain.agent (name) VALUES ('lane-e-ghost') "
             "ON CONFLICT (name) DO NOTHING")
    blocked, why3 = _try(su, "INSERT INTO brain.human_role (role_name, human) "
                             "VALUES ('brain_human_lane_e_ghost', 'lane-e-ghost')")
    truth("and mapping a human under a name the fleet already uses is refused too, from the "
          "SUPERUSER connection, which is the route a hurry actually takes",
          not blocked, str(why3))
    _sql(su, "DELETE FROM brain.human_role WHERE human = 'lane-e-ghost'")
    _sql(su, "DELETE FROM brain.agent WHERE name = 'lane-e-ghost'")


# ------------------------------------------------------------------ 6. whose queue is whose


def scene_6_whose_queue_is_whose(op, rt, two):
    print("\n-- 6. whose queue is whose, and it changes nothing today")
    _sql(op, "UPDATE brain.work_item SET assigned_human = NULL")
    tid = _seed(rt, "lane E scene 6 open", state="inbox")

    total = _sql(rt, "SELECT count(*) FROM brain.queue_open")[0][0]
    mine = _sql(rt, "SELECT count(*) FROM brain.queue_open_for('operator')")[0][0]
    theirs = _sql(rt, "SELECT count(*) FROM brain.queue_open_for(%s)", (SECOND,))[0][0]
    print(f"        queue_open {total}  ·  for operator {mine}  ·  for {SECOND} {theirs}")
    truth(f"the queue is not empty, so this scene compares something ({total} open items)",
          total > 0, "a zero denominator is a hard failure, never a pass")
    eq(f"with zero assignments, queue_open_for('operator') IS queue_open ({mine} of {total})",
       mine, total)
    eq(f"and {SECOND}'s queue is empty, of {total}", theirs, 0)

    r = store.apply("queue assign", id=tid, to=SECOND, as_operator=True)
    eq("the verb assigns", r["assigned_human"], SECOND)
    mine2 = _sql(rt, "SELECT count(*) FROM brain.queue_open_for('operator')")[0][0]
    theirs2 = _sql(rt, "SELECT count(*) FROM brain.queue_open_for(%s)", (SECOND,))[0][0]
    eq(f"one assignment moves exactly one row off the operator's queue "
       f"({mine} -> {mine2} of {total})", mine2, mine - 1)
    eq(f"and exactly one onto {SECOND}'s ({theirs} -> {theirs2} of {total})", theirs2, theirs + 1)
    eq(f"and brain.queue_open itself is UNCHANGED at {total}: this is a filter, not a boundary",
       _sql(rt, "SELECT count(*) FROM brain.queue_open")[0][0], total)

    rows, tot = qreads.mine(SECOND)
    eq("reads.mine returns both numbers so a caller can print a denominator",
       (len(rows), tot), (theirs2, total))

    # The two refusals, each watched.
    try:
        store.apply("queue assign", id=tid, to="nobody-is-this", as_operator=True)
        bad("assigning to a human nobody is, is refused", "it landed")
    except (QueueError, psycopg2.Error) as exc:
        truth("assigning to a human nobody is, is refused by the verb",
              "does not know" in str(exc), str(exc)[:100])
    good, why = _try(rt, "UPDATE brain.work_item SET assigned_human = 'nobody-is-this' "
                         "WHERE id = %s", (tid,))
    truth("and by the DATABASE, with the verb out of the way", not good, str(why))
    # A REAL CHANGE, not a no-op. The trigger returns early when the value does not move
    # (migration 20's asymmetry: establishing the attribute is the privileged act, living with
    # it is not), and the first version of this assertion re-wrote the SAME slug and read the
    # pass-through as a hole. UNASSIGNING is the change that matters here anyway: it is how an
    # agent would move a row off a human's queue.
    good2, why2 = _try(rt, "UPDATE brain.work_item SET assigned_human = NULL WHERE id = %s",
                       (tid,))
    truth("and an agent login may not UNASSIGN a human's row either", not good2, str(why2))
    eq(f"so the row is still {SECOND}'s after the attempt",
       _sql(rt, "SELECT assigned_human FROM brain.work_item WHERE id = %s", (tid,))[0][0], SECOND)
    good3, _ = _try(rt, "UPDATE brain.work_item SET assigned_human = %s WHERE id = %s",
                    (SECOND, tid))
    truth("while re-writing the SAME value from any login is a no-op and is NOT refused, which "
          "is what keeps every ordinary write on an assigned row working", good3)

    back = store.apply("queue assign", id=tid, to="", as_operator=True)
    eq("unassigning returns the row to the default assignee rather than to nobody",
       back["assigned_human"], None)
    eq(f"so the operator's queue is whole again ({mine} of {total})",
       _sql(rt, "SELECT count(*) FROM brain.queue_open_for('operator')")[0][0], mine)
    eq("and brain.default_assignee() is who it went back to",
       _sql(rt, "SELECT brain.default_assignee()")[0][0], "operator")


# ------------------------------------------------------------------ 7. which human am I


def scene_7_which_human_is_this_process():
    print("\n-- 7. which human a PROCESS is: a request, then the database's answer")
    os.environ.pop("BRAIN_HUMAN", None)
    eq("with nothing set, a process is the operator", session.human_slug(), "operator")
    os.environ["BRAIN_HUMAN"] = SECOND
    eq("$BRAIN_HUMAN is the session's answer", session.human_slug(), SECOND)
    eq("an explicit slug outranks it", session.human_slug("operator"), "operator")
    os.environ.pop("BRAIN_HUMAN", None)

    for junk in ("Andrew", "zzz not a person", "OPERATOR", "a" * 40):
        try:
            session.human_slug(junk)
            bad(f"{junk!r} is refused as a slug", "it was accepted")
        except store.StoreConfigError:
            ok(f"{junk!r} is refused rather than quoted into a role name")

    who = store.whoami()
    truth("whoami reaches the database", who["reachable"], who["reason"])
    eq("and it agrees for the operator", (who["requested"], who["human"]), ("operator", "operator"))
    truth("the login it names is a role, not a slug", str(who["login"]).startswith("brain_"))

    ghost = store.whoami("lane-e-no-credential")
    truth("a human with no credential on this host FAILS CLOSED", not ghost["agrees"])
    eq("and does NOT silently become the operator", ghost["human"], None)
    truth("with a sentence naming the provisioner rather than a permissions error",
          "provision" in ghost["reason"], ghost["reason"][:120])


def scene_8_roles_is_still_five():
    print("\n-- 8. store/session.py:ROLES is a list of privilege CLASSES, not a roster")
    eq("ROLES is five", len(session.ROLES), 5)
    eq("and they are the five classes", sorted(session.ROLES),
       sorted(("owner", "producer", "subscriber", "runtime", "operator")))
    truth(f"a named human is NOT in it: {SECOND!r} arrives from brain.human_roster()",
          SECOND not in session.ROLES)
    eq(f"and resolves the operator CLASS, so its statement timeout is that class's",
       session.STATEMENT_TIMEOUT["operator"], "15s")
    # Migration 20 sets the same bound in the database with ALTER ROLE. Neither is redundant:
    # this one covers Python, that one covers a psql session that never opens Python. They have
    # to agree, and until this assertion nothing checked that they did.
    mig = (ROOT / "migrations" / "0020_human_actor_identity.sql").read_text(encoding="utf-8")
    truth("migration 20 sets the same bound on every human role it mints",
          "statement_timeout = %L" in mig and "'15s'" in mig)
    eq(f"and {SECOND} resolves its own login, not the operator's",
       session.human_role_name(SECOND), "brain_human_" + SECOND.replace("-", "_"))
    eq("while `operator` keeps the name every surface already writes through",
       session.human_role_name("operator"), "brain_operator")


# ------------------------------------------------------------------ 9. the three readers


def scene_9_the_three_attribution_readers(rt):
    print("\n-- 9. the three attribution readers, checked one at a time")

    # READER 1: brain.queue_acted_on. Counts by STATE, groups by TEMPLATE.
    cols = [r[0] for r in _sql(rt, "SELECT column_name FROM information_schema.columns "
                                   "WHERE table_schema='brain' AND table_name='queue_acted_on'")]
    truth("queue_acted_on publishes no decider column at all: it counts by STATE",
          "decided_by" not in cols, f"columns: {cols}")
    src7 = (ROOT / "queue" / "schema" / "0007_queue.sql").read_text(encoding="utf-8")
    view = src7.split("CREATE OR REPLACE VIEW brain.queue_acted_on")[1].split(";")[0]
    truth("and its definition does not mention decided_by, so multi-user cannot move it",
          "decided_by" not in view)

    # READER 2: the auto-accept measurement. Its `decided_by` is brain.thread.from_agent.
    src = (ROOT / "engine" / "swarm_engine" / "accept.py").read_text(encoding="utf-8")
    truth("the auto-accept measurement's decided_by is brain.thread.from_agent, not the column",
          '"decided_by": decision["from_agent"]' in src)
    truth("and disagreement_report scores m['human'], derived from the thread event KIND",
          'm["human"] in ("accepted", "rejected")' in src)
    truth("and `accept work` now sets ctx.actor from the DATABASE before it writes that thread "
          "event, so the measurement's name is the database's too",
          "by = the_human" in src and "ctx.actor = by" in src)

    # READER 3: the points ledger. A JSONL file, sliced by actor_type.
    qsrc = (ROOT / "queue" / "human_queue" / "reads.py").read_text(encoding="utf-8")
    pts = qsrc.split("def points(")[1].split("\ndef ")[0]
    truth("the points ledger reads a JSONL file and slices on actor_type, never on a human name",
          'e.get("actor_type")' in pts and "decided_by" not in pts)
    truth("so it is unaffected: actor_type is human/ai/hybrid and stays three values",
          "by_actor_type" in pts)


# ------------------------------------------------------------------ run


def main():
    global FAIL
    why = _preconditions()
    if why:
        print(f"test_multi_user_subject: NOT RUN. {why}")
        return 77

    print(f"lane E, row 0384: several named humans on one instance. store {DB}, "
          f"ledger {_ledger()}, humans {SECOND!r} and 'operator'")

    su = _superuser()
    if su is None:
        print("test_multi_user_subject: NOT RUN. This suite DROPS two triggers to watch each "
              "guard fail before it passes, and DDL on brain.work_item and brain.agent needs "
              "the bootstrap superuser credential, which this host does not hold. Reporting NOT "
              "RUN rather than skipping the failing halves and printing a green over guards "
              "nobody watched fail.")
        return 77
    op, two, rt = _conn("operator"), _conn("operator", SECOND), _conn("runtime")
    try:
        scene_1_two_humans_one_trail(op, two, rt)
        scene_2_the_gate_fails_open_without_it(su, rt)
        scene_3_a_colleague_is_not_you(two, rt, op)
        scene_4_one_sentinel_one_producer(rt)
        scene_5_the_namespace_denial_of_service(su, op, rt)
        scene_6_whose_queue_is_whose(op, rt, two)
        scene_7_which_human_is_this_process()
        scene_8_roles_is_still_five()
        scene_9_the_three_attribution_readers(rt)
    finally:
        # Whatever happened, the triggers this suite drops are put back. A suite that leaves a
        # gate off is worse than a suite that fails.
        for stmt in (
            "DROP TRIGGER IF EXISTS work_item_acceptance_is_a_login ON brain.work_item",
            """CREATE TRIGGER work_item_acceptance_is_a_login
                 BEFORE INSERT OR UPDATE OF accepted_by, accepted_at ON brain.work_item
                 FOR EACH ROW EXECUTE FUNCTION brain.work_item_acceptance_is_a_login()""",
            "DELETE FROM brain.agent WHERE name = 'operator'",
            "DROP TRIGGER IF EXISTS agent_name_is_not_a_human ON brain.agent",
            """CREATE TRIGGER agent_name_is_not_a_human
                 BEFORE INSERT OR UPDATE OF name ON brain.agent
                 FOR EACH ROW EXECUTE FUNCTION brain.agent_name_is_not_a_human()""",
        ):
            _try(su, stmt)
        restored = _sql(su, "SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal AND tgname = "
                            "ANY(%s)", ([["work_item_acceptance_is_a_login",
                                          "agent_name_is_not_a_human"]][0],))[0][0]
        if restored != 2:
            FAIL += 1
            print(f"  FAIL  the suite left {restored} of 2 dropped triggers restored")
        else:
            print(f"\n  cleanup: 2 of 2 dropped triggers restored")
        for c in (op, two, rt, su):
            c.close()

    total = PASS + FAIL
    if total == 0:                                                          # DENOMINATOR
        print("\nDENOMINATOR: 0 comparisons made. A verdict over an empty set is not a pass.")
        return 2
    print(f"\n{PASS} passed, {FAIL} failed, {SKIP} not run  (of {total} assertions compared, "
          f"over 9 scenes)")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
