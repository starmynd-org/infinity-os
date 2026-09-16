#!/usr/bin/env python3
"""THE OPERATOR'S OWN WORK HAS A DOOR, AND `brain_runtime` CANNOT WALK THROUGH IT.

Task 0267; the title says `brain_runtime` rather than `an agent` since task 0295, because the
login is the whole of what these routes measure -- READ THE RESIDUAL BELOW BEFORE QUOTING THIS
SUITE. Two claims, and the second one is the reason the first is not a one-line flag:

    1. The operator can create a `brain.work_item` with `actor_type = 'human'`, it surfaces in
       `brain.queue_open` as `Mark my task done`, and the console's guard permits `done` on it.

    2. A process connecting as `brain_runtime` CANNOT create one. Not through the verb, not
       through the CLI flag, not by UPDATEing an agent's row into the human partition, not by
       writing itself into the mapping table, and not by asking the provisioning function to
       make it human.

Claim 2 is what makes claim 1 safe. An operator `done` on an agent's task fabricates that
agent's report, and an agent that could set the human column could hand its own work over for a
human signature -- the same forgery arriving from the other side -- so the attribute is
established by WHICH DATABASE LOGIN writes the row (migration 20) rather than asserted by a flag.

WHAT THE CONSOLE GUARD CHECKS MOVED AT TASK 0427 and this docstring moved with it, because a
suite that describes a guard by a line number describes it wrongly the first time the guard is
repaired. `web/rooms.py::assert_allowed_on` no longer reads `actor_type` at all: it reads
`brain.work_item` and permits `done` only where the row is NOT `agent_claimable` AND no agent
holds it. It used to read `item['actor_type']`, and `item` is the console CARD, which stamped
that key on itself from the arm of `brain.queue_open` the row arrived on -- so the guard was
that arm restated rather than a check on the row. `actor_type` still matters here and this suite
is still about it: migration 26's trigger coerces every `actor_type = 'human'` row to
`agent_claimable = false`, so claim 1's row is admitted by the new predicate BECAUSE it is human,
by construction in the database rather than by two predicates being maintained in step.

THE RESIDUAL, so nobody reads this suite as claiming more than it proves: on a `local-attended`
host every credential file lives in one `0700` directory under one UID, so an OS process that
can read the operator's secret file can connect as the operator. That is the same residual
`store/SECRETS.md` records for listeners, and it is file permissions, not the database. What is
closed absolutely is the case that exists: a process holding the `brain_runtime` credential --
which is what every agent surface is configured with -- has no route to this row, and route 3
below runs a real `store.apply` in an environment where only the operator's secret is missing.

One thing task 0295 measured that strengthens the paragraph above rather than weakening it: the
migration 26 coercion named four paragraphs up is NOT conditioned on `session_user`, so it holds
against the operator's own login too. Measured as `brain_operator` in raw SQL on 2026-08-19: a
single `UPDATE ... SET agent_claimable = true` on an `actor_type = 'human'` row comes back
`false`. Reaching such a row takes TWO statements -- clear `actor_type`, then raise the flag --
and `actor_type` is not in `set`'s settable list, so that route cannot be typed through the CLI
at all. See `engine/tests/test_actor_gate.py::test_a_human_row_resists_every_login_including_raw_sql`.

Run:  python3 queue/tests/test_human_actor_identity.py
      (against the queue scratch database, never `brain`)
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "queue"))
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT))

os.environ.setdefault("BRAIN_PG_DB", os.environ.get("QUEUE_SCRATCH_DB", "brain_queue_scratch"))
os.environ.pop("SWARM_PARENT_TASK", None)      # the live runner exports this; tests post roots

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _scratch_preflight import reconcile                          # noqa: E402
reconcile(os.environ["BRAIN_PG_DB"])

import psycopg2                                                   # noqa: E402
import store                                                      # noqa: E402
from store import session                                         # noqa: E402
from swarm_engine import transitions as engine                    # noqa: E402,F401

from web import model as web_model, rooms as web_rooms            # noqa: E402

SCRATCH = str(ROOT / "queue/bin/queue-scratch-db.sh")
SECRETS = Path(os.environ.get("BRAIN_SECRET_DIR", str(Path.home() / ".brain-postgres-secrets")))
PASS, FAIL = 0, 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ok    {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")
    return ok


def refused(fn, *a, **kw) -> str:
    """Run something that must be refused. Returns the message, or '' if it was NOT refused."""
    try:
        fn(*a, **kw)
        return ""
    except Exception as e:                                        # noqa: BLE001
        return str(e) or e.__class__.__name__


def as_role(role: str, sql: str, params=None):
    """One statement on a real login of that role. No store wrapper: the gate is the database's,
    so the test has to be able to reach past every guard written in Python."""
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
    subprocess.run([SCRATCH, "psql", "-q", "-c",
                    "TRUNCATE brain.queue_item, brain.queue_defer, brain.queue_bump, "
                    "brain.queue_calibration, brain.queue_default_event, brain.recommendation, "
                    "brain.thread, brain.question, brain.agent, brain.work_item CASCADE; "
                    "SELECT setval('brain.item_id_seq', 1, false);"],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def row(tid: str) -> dict:
    with store.read("runtime") as s:
        return s.one("SELECT * FROM brain.work_item WHERE id = %s", (tid,)) or {}


def provisioned() -> bool:
    """Is there an operator login on this host at all? Reported, never skipped silently."""
    return (SECRETS / "brain-postgres-role-operator").is_file()


# --------------------------------------------------------------------------- the door works

def test_the_operator_can_put_his_own_work_in_his_own_queue():
    """Claim 1, end to end: the verb, the row, the queue arm, and the console's guard."""
    reset()
    r = store.apply("post", title="Renew the business insurance before it lapses",
                    lane="ops", posted_by="operator", actor_type="human",
                    body="the renewal quote is in the folder; it needs a decision, not an agent")
    tid = r["id"]
    w = row(tid)
    check("the row exists and it is his", w.get("actor_type") == "human",
          f"actor_type={w.get('actor_type')!r}")
    check("it wrote through the OPERATOR login, not the runtime one",
          session.dsn("operator")["user"] == "brain_operator")

    with store.read("runtime") as s:
        arm = s.one("SELECT primary_verb FROM brain.queue_open WHERE source_id = %s", (tid,))
    check("it surfaces in the queue as his own task",
          (arm or {}).get("primary_verb") == "Mark my task done", str(arm))

    # THE CARD THE CONSOLE ACTUALLY PASSES, not a dict assembled here. Until task 0427 this
    # site read `{"id": tid, "actor_type": w["actor_type"]}` off the ROW, and that dict is not
    # the one the write door sees: the console passes `web/model.py::_card`, whose `actor_type`
    # the card stamped on itself from the arm the row arrived on. A test that hand-builds the
    # item proves a property the console does not have, and this one was green through the whole
    # period the guard was checking nothing.
    card = web_model.find_item(tid)
    check("the console finds his row and renders it as his own task",
          bool(card) and (card or {}).get("kind") == "human", f"card={card and card.get('kind')!r}")
    check("and the console's guard PERMITS `done` on it",
          refused(web_rooms.assert_allowed_on, "queue", "done", card) == "",
          refused(web_rooms.assert_allowed_on, "queue", "done", card))

    res = store.apply("done", id=tid, summary="renewed, receipt filed", agent="operator",
                      actor="operator")
    check("`done` lands: an already-human row takes ordinary runtime work",
          row(tid)["state"] == "done", f"state={row(tid)['state']} res={res}")
    return tid


def test_the_guard_still_refuses_done_on_an_agent_item():
    """The prohibition this task must not weaken, re-measured rather than assumed.

    WHAT AN "AGENT ITEM" IS HERE CHANGED AT TASK 0427, and the change is why this test was
    rewritten rather than left alone. It used to post an ordinary `--for-agents`-less row and
    assert the console refused `done` on it, on the strength of `actor_type IS NULL`. Two things
    made that assertion wrong rather than merely differently worded:

      1. `queue/schema/0013` put exactly that row on the operator's queue on purpose -- it is
         `agent_claimable = false`, so no agent will ever take it, and a card the console shows
         him under `Mark my task done` whose button it then refuses is a broken surface, not a
         guard. (The old guard permitted it too, for the wrong reason: the card stamped
         `actor_type: human` on itself from the arm.)
      2. The dict this site built was never the dict the console passes.

    So the agent item is now the row the FLEET may take, and the row an agent is HOLDING, and
    both are read off `brain.work_item` by the guard rather than asserted by the caller.
    """
    # `workdir` is required of any agent-claimable row since task 0100: an empty one made
    # the runner cd to the agent profile's own directory. `/tmp` because these fixtures
    # never run anything there, only post; the gate asks for absolute, not for existing.
    r = store.apply("post", title="An agent's task, posted for agents", lane="queue",
                    posted_by="T9", agent_claimable=True, workdir="/tmp")
    w = row(r["id"])
    check("an ordinary agent post is still actor_type NULL, not silently promoted",
          w.get("actor_type") is None, f"actor_type={w.get('actor_type')!r}")
    check("and it is the fleet's, which is what makes it an agent item",
          w.get("agent_claimable") is True, f"agent_claimable={w.get('agent_claimable')!r}")

    # No card exists for it -- a claimable inbox row is on no arm of his queue -- so the dict
    # below is the one a crafted POST would carry, which is the only way this guard can now be
    # reached with a lie in it. It must not buy permission.
    check("the console shows him no card for it at all", web_model.find_item(r["id"]) is None)
    msg = refused(web_rooms.assert_allowed_on, "queue", "done",
                  {"id": r["id"], "actor_type": "human"})
    check("the console REFUSES `done` on it even when the item claims actor_type human",
          msg != "", "NOT REFUSED")
    check("and the refusal still names the forgery it is about",
          "fabricate that agent's report" in msg, msg[:160])
    check("and it names the column it actually read, not the one it was handed",
          "agent_claimable=True" in msg, msg[:160])

    # The residual `queue/schema/0013` named and could not close from inside a view: a row an
    # agent HOLDS whose agent_claimable is then lowered to false. That row lands on arm 2 with a
    # live agent parked on it, and the console rendered it as his own task.
    store.apply("claim", agent="T-0427-holder", lanes=["*"], host="test")
    held = row(r["id"])
    if check("the agent holds it", held.get("state") == "active"
             and (held.get("claimed_by") or "") == "T-0427-holder", str(held.get("state"))):
        as_role("owner", "UPDATE brain.work_item SET agent_claimable = false WHERE id = %s",
                (r["id"],))
        card = web_model.find_item(r["id"])
        check("taken back from the fleet, it now DOES reach his console as his own task",
              bool(card) and card.get("kind") == "human", f"card={card and card.get('kind')!r}")
        msg = refused(web_rooms.assert_allowed_on, "queue", "done", card)
        check("and the guard refuses it anyway, because an agent is holding it", msg != "",
              "NOT REFUSED -- this is the forgery the prohibition is about")
        check("naming the holder whose report a `done` here would fabricate",
              "T-0427-holder" in msg, msg[:200])
    return r["id"]


# --------------------------------------------------------------------------- an agent cannot

def test_route_1_the_runtime_login_cannot_insert_a_human_item():
    """The direct route: raw SQL as the login every agent surface connects as."""
    msg = refused(as_role, "runtime",
                  "INSERT INTO brain.work_item (title, lane, actor_type) "
                  "VALUES ('forged by an agent', 'queue', 'human')")
    check("brain_runtime CANNOT insert an actor_type=human row", msg != "", "NOT REFUSED")
    check("and the database says why, naming the login", "brain_runtime" in msg, msg[:140])
    check("it is an insufficient_privilege, not a check violation",
          "not a human login" in msg, msg[:140])


def test_route_2_the_runtime_login_cannot_promote_an_agent_row():
    """The sideways route: post an ordinary item, then launder it into the human partition."""
    r = store.apply("post", title="Agent work to be laundered into the human queue", lane="queue")
    msg = refused(as_role, "runtime",
                  "UPDATE brain.work_item SET actor_type = 'human' WHERE id = %s", (r["id"],))
    check("brain_runtime CANNOT update an existing row into actor_type=human", msg != "",
          "NOT REFUSED")
    check("the row is unchanged after the attempt", row(r["id"])["actor_type"] is None,
          str(row(r["id"])["actor_type"]))


def test_route_3_a_process_without_the_credential_is_refused_by_the_verb():
    """The realistic route: an agent runs `store.apply('post', actor_type='human')`.

    The environment is a copy of the secret backend with EVERY secret present except the
    operator's, which is exactly what an agent host looks like: it holds the runtime credential
    it needs to work and does not hold the one that says it is a human.
    """
    tmp = Path(tempfile.mkdtemp(prefix="t3-0267-agent-secrets-"))
    try:
        for f in SECRETS.iterdir():
            if f.is_file() and f.name != "brain-postgres-role-operator":
                shutil.copyfile(f, tmp / f.name)
        env = dict(os.environ, BRAIN_SECRET_DIR=str(tmp))
        code = (
            "import sys, os;"
            f"sys.path[:0]=[{str(ROOT)!r}, {str(ROOT / 'engine')!r}];"
            "import store;"
            "from swarm_engine import transitions as E;"
            "r = store.apply('post', title='an agent forging the operator', lane='queue',"
            "                actor_type='human');"
            "print('NOT REFUSED', r)"
        )
        p = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True,
                           cwd=str(ROOT))
        out = (p.stdout + p.stderr)
        check("a process with no operator credential is REFUSED the verb", p.returncode != 0,
              out[-200:])
        check("it fails closed as StoreConfigError, not by posting an agent item instead",
              "StoreConfigError" in out, out[-200:])
        check("and it says the fallback to brain_runtime is the forgery, not the fix",
              "arriving from the other side" in out, out[-300:])
        with store.read("runtime") as s:
            n = s.scalar("SELECT count(*) FROM brain.work_item "
                         "WHERE title = 'an agent forging the operator'")
        check("no row of any kind was created by the attempt", int(n or 0) == 0, f"{n} rows")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_route_4_the_runtime_login_cannot_make_itself_human():
    """The root route: if an agent could write the mapping, every gate above is decoration."""
    msg = refused(as_role, "runtime", "SELECT * FROM brain.human_role")
    check("brain_runtime cannot even READ brain.human_role", "permission denied" in msg,
          msg[:120] or "NOT REFUSED")
    msg = refused(as_role, "runtime",
                  "INSERT INTO brain.human_role (role_name, human) VALUES "
                  "('brain_runtime', 'operator')")
    check("brain_runtime cannot write itself into brain.human_role", "permission denied" in msg,
          msg[:120] or "NOT REFUSED")
    msg = refused(as_role, "runtime",
                  "SELECT brain.provision_human_role('brain_runtime', 'operator')")
    check("brain_runtime cannot call the provisioning function either",
          "permission denied" in msg, msg[:120] or "NOT REFUSED")
    msg = refused(as_role, "runtime", "SELECT set_config('role', 'brain_operator', false); "
                                      "INSERT INTO brain.work_item (title, lane, actor_type) "
                                      "VALUES ('via a GUC', 'queue', 'human')")
    check("and no setting a client can send changes the answer", msg != "", "NOT REFUSED")


def test_the_cli_flag_is_a_request_not_a_grant():
    """`swarm post --mine` exists so the operator has a terminal door. It is not a way in."""
    tmp = Path(tempfile.mkdtemp(prefix="t3-0267-agent-cli-"))
    try:
        for f in SECRETS.iterdir():
            if f.is_file() and f.name != "brain-postgres-role-operator":
                shutil.copyfile(f, tmp / f.name)
        env = dict(os.environ, BRAIN_SECRET_DIR=str(tmp))
        p = subprocess.run([str(ROOT / "engine/bin/swarm"), "post", "--lane", "queue",
                            "--title", "an agent using the flag", "--mine"],
                           env=env, capture_output=True, text=True, cwd=str(ROOT))
        out = p.stdout + p.stderr
        check("`swarm post --mine` from a process that is not the operator is refused",
              p.returncode != 0, out[-200:])
        with store.read("runtime") as s:
            n = s.scalar("SELECT count(*) FROM brain.work_item "
                         "WHERE title = 'an agent using the flag'")
        check("and it created nothing", int(n or 0) == 0, f"{n} rows")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_brain_runtime_cannot_produce_an_actor_type_human_item():
    """The summary assertion. It restates nothing: it fails if any route above failed.

    Named for the login since task 0295, because every one of the four routes above is a
    `brain_runtime` route -- route 4 says so in its own name. See THE RESIDUAL in the module
    docstring for what this therefore does not claim.
    """
    check("a process connecting as brain_runtime cannot produce an actor_type=human work item",
          FAIL == 0, f"{FAIL} assertion(s) above failed, so the claim is NOT met")


def main():
    print("test_human_actor_identity.py  --  the operator's own door, and the four ways in that "
          "are shut")
    print(f"  store: {os.environ['BRAIN_PG_DB']} (scratch). The live bus at ~/.swarm is "
          f"untouched.\n")
    if not provisioned():
        print("  NOT RUN: no operator credential on this host. Migration 20 is two steps and the\n"
              "  second one is store/bin/provision-operator.sh --db "
              f"{os.environ['BRAIN_PG_DB']}. Refusing to report\n"
              "  green on a host where the door was never opened.")
        return 1
    for fn in (test_the_operator_can_put_his_own_work_in_his_own_queue,
               test_the_guard_still_refuses_done_on_an_agent_item,
               test_route_1_the_runtime_login_cannot_insert_a_human_item,
               test_route_2_the_runtime_login_cannot_promote_an_agent_row,
               test_route_3_a_process_without_the_credential_is_refused_by_the_verb,
               test_route_4_the_runtime_login_cannot_make_itself_human,
               test_the_cli_flag_is_a_request_not_a_grant):
        print(f"=== {fn.__name__.replace('test_', '')} ===")
        fn()
    print("\n=== the claim ===")
    test_brain_runtime_cannot_produce_an_actor_type_human_item()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
