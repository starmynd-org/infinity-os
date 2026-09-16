#!/usr/bin/env python3
"""The operator's own work is not handed out by `claim`. One test per way that could fail open.

Task 0414, the last thing blocking the V1 cutover. `brain.work_item` is the fleet's dispatch
queue AND the operator's day. Measured on live `brain` on 2026-08-18, before migration 26: 24
rows in `inbox`, 15 of them posted by `operator` -- 0058..0073 less 0064 -- including
0061, a credential rotation, 0066, a contract to send to a client, and 0069, a QC result
to send to a client. Every terminal is
configured `"lanes": ["*"]` and `claim` had no actor predicate, so the first poll after the
cutover would have handed one of those out.

THE FIXTURE BELOW HAS THE SHAPE OF THE REAL BOARD. `LIVE_INBOX` is the 24 rows as they
stood, ids and lanes and posters and actor_type included, with identifying titles replaced by
invented ones -- and the two rows that carry
`actor_type = NULL` rather than `'human'` are in it, because those two are the whole reason this
guard is a column of its own instead of a read of `actor_type`. A test built from a tidy fixture
would pass on a guard that fails open on exactly the rows that matter.

What each test pins:

  test_no_operator_row_is_ever_handed_out    drains the board with a real `lanes: ["*"]` claimer
                                             until it is empty and asserts WHICH ids came out
  test_a_refusal_costs_nothing               attempts, state, thread and `run` are untouched
  test_a_new_row_is_safe_without_a_flag      the default, which is the part nobody can forget
  test_brain_runtime_cannot_promote_the_row  migration 26's trigger, over `brain_runtime` -- and
                                             over that login ONLY. See below.
  test_a_human_row_cannot_be_promoted        the two notions of human cannot drift apart
  test_a_child_cannot_launder_the_subtree    `--for-agents` under a held parent is refused
  test_a_store_without_the_gate_refuses      an unmigrated store hands out NOTHING
  test_the_predicate_is_in_the_claim_stmt    structural: not a post-claim release, not a surface

WHAT NO TEST HERE PINS, AND WHY THE NAME ABOVE SAYS `brain_runtime` RATHER THAN `an agent`
(task 0295, 2026-08-19). Migration 26's trigger is over the LOGIN. It refuses `brain_runtime`,
which is what every agent surface connects as, and it refuses nothing about the process behind
that connection. On a `local-attended` host the fleet's terminals and the operator's own console
run as the same Unix user, so a terminal can read `~/.brain-postgres-secrets/` and open the
operator's login itself -- `swarm set <id> agent_claimable true --as-operator`, or six lines of
psycopg2 with no flag at all. Both were measured from a live fleet terminal on 2026-08-19
(0295's thread; scratch stores `brain_t2_0222_path` and `brain_t3_0295`). So the sentence this
file supports is "an agent CONNECTING AS `brain_runtime` cannot promote the row", not "an agent
cannot". The residual is filesystem permissions and it is stated in `store/SECRETS.md`; what
closes it is an operator decision, not a test.

Run: python3 engine/tests/test_actor_gate.py   (against the scratch database, never `brain`)
"""

from __future__ import annotations

import inspect
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT))

os.environ.setdefault("BRAIN_PG_DB", os.environ.get("ENGINE_SCRATCH_DB", "brain_scratch"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _scratch_preflight import reconcile              # noqa: E402
reconcile(os.environ["BRAIN_PG_DB"])
os.environ.pop("SWARM_PARENT_TASK", None)

import store                                                    # noqa: E402
from swarm_engine import reads, transitions                     # noqa: E402
from swarm_engine.transitions import VerbError                  # noqa: E402

SCRATCH = str(ROOT / "engine/bin/scratch-db.sh")
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


def eq(msg, got, want):
    ok(msg) if got == want else bad(msg, f"wanted [{want}], got [{got}]")


def truth(msg, cond, detail=""):
    ok(msg) if cond else bad(msg, detail)


def reset():
    import subprocess
    subprocess.run([SCRATCH, "truncate"], check=True, capture_output=True)


# ------------------------------------------------------------------ the live board, verbatim
#
# (title, lane, posted_by, actor_type, whose). `whose` is the ANSWER, read off the live store
# by hand on 2026-08-18 and asserted below -- it is not computed from the other columns, because
# computing it is precisely the thing the guard must not do.
OPERATOR = "operator"
FLEET = "fleet"

LIVE_INBOX = [
    ("Renew a personal document",                          "personal",  "operator", "human", OPERATOR),
    ("Tell the client their data issue is resolved",     "client",    "operator", "human", OPERATOR),
    ("Gate B: the twelve-row login pass on the app",      "client",    "operator", "human", OPERATOR),
    ("Rotate the two exposed registry tokens",           "security",  "operator", "human", OPERATOR),
    ("Decide the marketplace double count",               "client",    "operator", "human", OPERATOR),
    ("Own or hand off the T25 view files",                "client",    "operator", "human", OPERATOR),
    ("Record the VSL video",                              "marketing", "operator", "human", OPERATOR),
    ("Send the contract to the client",                  "client",    "operator", "human", OPERATOR),
    ("Run the human side of the QC reports",              "qc",        "operator", "human", OPERATOR),
    # 0068. NULL, not 'human'. Created 0.7s after the row above it, by the same hand, on
    # 2026-08-17. This is the row a guard reading `actor_type` hands to an agent.
    ("Double-check the exported QC data",                 "qc",        "operator", None,    OPERATOR),
    ("Send the double-checked QC result to the client",  "client",    "operator", "human", OPERATOR),
    ("Record the audio walkthrough",                      "marketing", "operator", "human", OPERATOR),
    # 0071. The second one.
    ("Unify the multiple presentations",                  "marketing", "operator", None,    OPERATOR),
    ("Build out the unified presentation",                "marketing", "operator", "human", OPERATOR),
    ("Set the email provider key on the host",           "marketing", "operator", "human", OPERATOR),
    ("adapter backlog A",                                 "adapter",   "T5",       None,    FLEET),
    ("adapter backlog B",                                 "adapter",   "T5",       None,    FLEET),
    ("adapter backlog C",                                 "adapter",   "T5",       None,    FLEET),
    ("adapter backlog D",                                 "adapter",   "T5",       None,    FLEET),
    ("adapter backlog E",                                 "adapter",   "T5",       None,    FLEET),
    ("queue backlog",                                     "queue",     "commander", None,   FLEET),
    ("adapter lineage A",                                 "adapter",   "T1",       "ai",    FLEET),
    ("adapter lineage B",                                 "adapter",   "T1",       "ai",    FLEET),
    ("adapter lineage C",                                 "adapter",   "T1",       "ai",    FLEET),
]


def load_board():
    """Post the live board into the scratch store the way each row was actually made.

    The operator's rows go in through the OPERATOR'S OWN LOGIN when they carry `actor_type =
    'human'`, because that is how migration 20 makes them, and through the ordinary runtime login
    when they do not -- which is exactly how 0068 and 0071 exist. The fleet's go in with
    `--for-agents`. Nothing here sets `agent_claimable` on an operator row: the default is what
    is under test.
    """
    ids = {OPERATOR: [], FLEET: []}
    for title, lane, poster, actor, whose in LIVE_INBOX:
        kw = {"actor_type": actor} if actor else {}
        if whose == FLEET:
            kw["agent_claimable"] = True
            kw["workdir"] = "/tmp"        # task 0100: a fleet row names its tree
        r = store.apply("post", title=title, lane=lane, posted_by=poster, **kw)
        ids[whose].append(r["id"])
    return ids


def drain(agent="T1", lanes=("*",), limit=200):
    """Claim until the queue is empty, the way a `lanes: ["*"]` terminal polls after the cutover."""
    got = []
    for _ in range(limit):
        t = store.apply("claim", agent=agent, lanes=list(lanes))
        if not t:
            return got
        got.append(t["id"])
        # Finish it, so the next claim is not simply the same row under a live-agent gate.
        store.apply("done", id=t["id"], summary="drained by the test", agent=agent)
    raise AssertionError("drain did not terminate")


# ------------------------------------------------------------------ the tests

def test_no_operator_row_is_ever_handed_out():
    """The whole finding: 15 operator rows, a `lanes: ["*"]` claimer, and 0 of them come out."""
    reset()
    ids = load_board()
    eq("setup: the board is the live one", len(ids[OPERATOR]) + len(ids[FLEET]), 24)
    eq("setup: 15 of the 24 are the operator's", len(ids[OPERATOR]), 15)

    handed = drain()
    leaked = sorted(set(handed) & set(ids[OPERATOR]))
    eq("15 of 15 operator rows REFUSED to a `lanes: [*]` claimer",
       len(set(ids[OPERATOR]) - set(handed)), 15)
    truth("and none of them leaked", not leaked, f"handed out: {leaked}")
    eq("9 of 9 fleet rows still claimable", len(set(ids[FLEET]) & set(handed)), 9)
    eq("nothing else came out", sorted(handed), sorted(ids[FLEET]))

    # The two rows that make this a column and not a read of actor_type.
    null_actor = [tid for tid in ids[OPERATOR]
                  if reads.task(tid)["actor_type"] is None]
    eq("the two actor_type=NULL operator rows are in the fixture", len(null_actor), 2)
    truth("and BOTH were refused, which a guard reading actor_type would not have done",
          not (set(null_actor) & set(handed)),
          f"leaked: {sorted(set(null_actor) & set(handed))}")


def test_a_refusal_costs_nothing():
    """A refusal is not a failure: no attempt, no state change, no thread event, no run row."""
    reset()
    tid = store.apply("post", title="the operator's own", lane="personal",
                      posted_by="operator")["id"]
    before = reads.task(tid)
    thread_before = len(reads.thread(tid))

    for _ in range(5):
        store.apply("claim", agent="T1", lanes=["*"])

    after = reads.task(tid)
    eq("attempts unchanged after 5 claim polls", after["attempts"], before["attempts"])
    eq("attempts is still 0, so max_attempts is not being burned", after["attempts"], 0)
    eq("state is still inbox", after["state"], "inbox")
    truth("it was never marked failed or blocked", after["state"] not in ("blocked", "cancelled"),
          f"state is {after['state']}")
    eq("claimed_by is still empty", after["claimed_by"], "")
    eq("no event was written to the thread", len(reads.thread(tid)), thread_before)
    with store.read() as s:
        runs = s.query("SELECT * FROM brain.run WHERE work_item_id = %s", (tid,))
    eq("no attempt row in brain.run", len(runs), 0)

    # And it is still the HUMAN's to act on, which is the third half of "a refusal is a
    # refusal". `brain.queue_open` is the operator's own door and its second arm -- the one whose
    # `primary_verb` is `Mark my task done` -- is what carries a row to him.
    his = store.apply("post", title="his own, marked", lane="client", posted_by="operator",
                      actor_type="human")["id"]
    store.apply("claim", agent="T1", lanes=["*"])
    with store.read() as s:
        seen = s.query("SELECT source_id FROM brain.queue_open WHERE source_id = %s", (his,))
    truth("a refused row is still on the operator's own surface (brain.queue_open)", bool(seen),
          "the guard took it away from the fleet AND from him, which is not a guard")
    eq("and it is still his to claim: untouched by the poll", reads.task(his)["attempts"], 0)

    # BOTH HALVES OF THE PARTITION, PINNED TOGETHER HERE.
    #
    # Until queue schema 0013 this block asserted a RESIDUAL instead: `queue_open`'s second arm
    # read `w.actor_type = 'human'`, that column has a meaningful NULL ("unset is a real state",
    # migration 1), and so the two live rows carrying NULL -- 0068 and 0071 -- were held from the
    # fleet by this guard and were STILL on no surface he reads. That was written here as a
    # tripwire: widening `queue_open` was the queue lane's call, not this one.
    #
    # THE QUEUE LANE MADE IT. `queue/schema/0013_queue_open_shows_what_the_fleet_will_not_take.sql`
    # (ledger version 28, task 0421) rewrote that arm to `NOT w.agent_claimable`, the exact
    # complement of the predicate `claim` applies. The residual is closed, so the claim inverts
    # to the positive one: `tid` -- posted with no actor_type at all, and polled for six times
    # above without ever being handed out -- is held from the fleet AND reaches his console, and
    # it arrives through arm 2, whose `primary_verb` is `Mark my task done`, a verb he can act on
    # rather than some other arm's.
    #
    # One conjunction on purpose. The tripwire's worth was never the residual, it was that the
    # two halves of the partition are asserted in one place: held-from-the-fleet without
    # on-his-queue is the hole 0421 closed, and on-his-queue without held-from-the-fleet would be
    # this guard failing open. The queue side pins the same pair from its end, in
    # `queue/tests/test_queue_open_is_the_complement.py::test_the_fleet_still_cannot_take_it`.
    with store.read() as s:
        his_own = s.query("SELECT primary_verb FROM brain.queue_open "
                          "WHERE source_type = 'work_item' AND source_id = %s", (tid,))
    verbs = [r["primary_verb"] for r in his_own]
    held = reads.task(tid)["agent_claimable"] is False
    truth("an unclassified operator row is held from the fleet AND is on his console "
          "with `Mark my task done`",
          held and verbs == ["Mark my task done"],
          f"agent_claimable={reads.task(tid)['agent_claimable']!r} (want False), "
          f"queue_open arms={verbs!r} (want ['Mark my task done']); "
          "an empty arm list means queue/schema/0013 is not applied to this store")


def test_a_new_row_is_safe_without_a_flag():
    """Nobody has to remember anything. Post one, try to claim it, read the refusal."""
    reset()
    tid = store.apply("post", title="posted just now, nobody flagged anything",
                      lane="brand-new-lane-invented-today", posted_by="operator")["id"]
    eq("the default is held", reads.task(tid)["agent_claimable"], False)
    got = drain()
    truth("a brand new row in a brand new lane is refused with no flag set",
          tid not in got, "it was handed out")

    held = [r for r in reads.held_from_the_fleet() if r["id"] == tid]
    eq("and it is legible: held_from_the_fleet names it", len(held), 1)
    truth("and marks it unclassified rather than pretending it is deliberate",
          held and held[0]["operator_owned"] is False)


def test_brain_runtime_cannot_promote_the_row():
    """`brain_runtime` raising false -> true is refused by the database, not by Python.

    The NAME of this test is `brain_runtime`, not `an agent`, because `brain_runtime` is the
    whole of what it measures. A process that holds the operator's credential is not refused
    here, and on this host every agent process can read it. See the module docstring.
    """
    reset()
    tid = store.apply("post", title="the operator's own", lane="security",
                      posted_by="operator")["id"]
    try:
        store.apply("set", id=tid, key="agent_claimable", value="true", agent="T1")
    except Exception as e:                                       # noqa: BLE001
        msg = str(e)
        truth("an agent's raise is refused", "not a human login" in msg, msg[:160])
        truth("and the refusal says which login it was",
              "brain_runtime" in msg, msg[:160])
    else:
        bad("an agent's raise is refused", "it succeeded")
    eq("the row is still held", reads.task(tid)["agent_claimable"], False)

    # The safe direction stays open to everybody.
    fleet = store.apply("post", title="fleet work", lane="engine", posted_by="commander",
                        agent_claimable=True, workdir="/tmp")["id"]
    store.apply("set", id=fleet, key="agent_claimable", value="false", agent="T1")
    eq("but LOWERING is open to any caller: taking work back is the safe direction",
       reads.task(fleet)["agent_claimable"], False)


def test_a_human_row_cannot_be_promoted():
    """One notion of human. `actor_type='human'` and agent-claimable cannot coexist."""
    reset()
    tid = store.apply("post", title="his own, marked", lane="client", posted_by="operator",
                      actor_type="human")["id"]
    eq("a human-actor row is held", reads.task(tid)["agent_claimable"], False)
    r = store.apply("set", id=tid, key="agent_claimable", value="true", agent="operator",
                    as_operator=True)
    eq("even the operator's own raise is coerced back", reads.task(tid)["agent_claimable"], False)
    truth("and the verb REPORTS the coercion rather than echoing the request",
          r.get("coerced") is True and r["agent_claimable"] is False,
          f"returned {r!r}")
    truth("the thread records what was stored, not what was asked",
          any("coerced" in (e["text"] or "") for e in reads.thread(tid)),
          "a reader of the thread would think the raise landed")


def test_a_child_cannot_launder_the_subtree():
    """`--for-agents` under a held parent is refused, so a hold cannot be escaped one hop at a time."""
    reset()
    parent = store.apply("post", title="the operator's own", lane="client",
                         posted_by="operator")["id"]
    try:
        store.apply("post", title="child that lets itself out", lane="engine",
                    posted_by="T1", parent=parent, agent_claimable=True, workdir="/tmp")
    except VerbError as e:
        truth("a claimable child under a held parent is refused",
              "not agent-claimable" in str(e), str(e)[:160])
    else:
        bad("a claimable child under a held parent is refused", "it was posted")

    child = store.apply("post", title="ordinary child", lane="engine", posted_by="T1",
                        parent=parent)["id"]
    eq("and an ordinary child inherits the hold", reads.task(child)["agent_claimable"], False)

    fleet = store.apply("post", title="fleet root", lane="engine", posted_by="commander",
                        agent_claimable=True, workdir="/tmp")["id"]
    kid = store.apply("post", title="adjacent work, handed off", lane="engine", posted_by="T1",
                      parent=fleet, workdir="/tmp")["id"]   # task 0100: claimable, so it says where
    eq("while a child of FLEET work inherits claimable, with no flag passed",
       reads.task(kid)["agent_claimable"], True)


def test_a_store_without_the_gate_refuses():
    """An unmigrated store hands out NOTHING. Fail closed, and say which migration."""
    reset()
    store.apply("post", title="fleet work", lane="engine", posted_by="commander",
                agent_claimable=True, workdir="/tmp")
    # RENAME, NOT DROP. `brain.work_item_claimability` and the partial index both depend on the
    # column, so a DROP needs CASCADE and would leave the scratch store missing objects migration
    # 26 will not re-create (its version is already recorded). A rename is invisible to a
    # dependent view -- Postgres rewrites the stored definition and rewrites it back -- and it is
    # exactly what `_actor_gate_present` looks for, because that function asks
    # `information_schema.columns` for the NAME.
    # As the SUPERUSER, because `brain_owner` does not own `work_item` on a scratch store built
    # by `scratch-db.sh` (postgres does) and `ALTER TABLE` needs the owner. Same secret file that
    # script reads. If it is not on this host, SAY SO instead of passing: an unrun test that
    # prints nothing is how a fail-open path ships.
    import psycopg2
    secret = Path(os.environ.get("BRAIN_SECRET_DIR", str(Path.home() / ".brain-postgres-secrets")))
    secret = secret / "brain-postgres-bootstrap-superuser"
    if not secret.is_file():
        bad("a store with no actor gate refuses every claim",
            f"NOT RUN: {secret} is not readable, so this suite cannot rename the column. The "
            f"fail-closed path is UNVERIFIED on this host, not verified good.")
        return
    dsn = dict(store.session.dsn("owner"), user="postgres",
               password=secret.read_text(encoding="utf-8").strip())
    con = psycopg2.connect(**dsn)
    con.autocommit = True
    with con.cursor() as cur:
        cur.execute("ALTER TABLE brain.work_item "
                    "RENAME COLUMN agent_claimable TO agent_claimable_hidden_by_a_test")
    try:
        try:
            store.apply("claim", agent="T1", lanes=["*"])
        except VerbError as e:
            truth("a store with no actor gate refuses every claim",
                  "no brain.work_item.agent_claimable column" in str(e), str(e)[:200])
            truth("and names the migration to apply",
                  "0026_work_item_agent_claimable" in str(e), str(e)[:200])
        else:
            bad("a store with no actor gate refuses every claim",
                "it dispatched anyway, which is the fail-OPEN direction")
    finally:
        with con.cursor() as cur:
            cur.execute("ALTER TABLE brain.work_item "
                        "RENAME COLUMN agent_claimable_hidden_by_a_test TO agent_claimable")
        con.close()
    got = store.apply("claim", agent="T1", lanes=["*"])
    truth("and it dispatches again the moment the column is back",
          bool(got), "the gate stayed shut after the column returned")


def test_the_predicate_is_in_the_claim_statement():
    """Structural. Not a post-claim release, not a surface, not a config narrowing."""
    src = inspect.getsource(transitions)
    claim = inspect.getsource(transitions.claim)
    truth("the gate is a WHERE fragment, defined once",
          len(re.findall(r"^_AGENT_CLAIMABLE = ", src, re.M)) == 1,
          "there is not exactly one definition of the predicate")
    truth("and it names the column, not a lane list or a poster name",
          "w.agent_claimable" in transitions._AGENT_CLAIMABLE
          and "lane" not in transitions._AGENT_CLAIMABLE
          and "posted_by" not in transitions._AGENT_CLAIMABLE,
          repr(transitions._AGENT_CLAIMABLE))

    # The one statement that also holds the row lock. Both must be in the same SQL string, or the
    # gate is being applied to a row somebody else may already have taken.
    m = re.search(r"row = ctx\.one\(\s*f?\"\"\"(.*?)\"\"\"", claim, re.S)
    truth("the candidate query exists and is one statement", bool(m))
    if m:
        q = m.group(1)
        truth("the gate is INSIDE the FOR UPDATE SKIP LOCKED statement",
              "_AGENT_CLAIMABLE" in q and "FOR UPDATE SKIP LOCKED" in q,
              "the predicate and the row lock are not in the same statement")
    truth("claim never releases a row it should not have taken",
          "release" not in claim.split("def claim")[-1].split("return task")[0].lower()
          or "_AGENT_CLAIMABLE" in claim,
          "a post-claim release burns an attempt; that is a claim with an apology")

    # And the fleet config is NOT the guard. Every terminal is `lanes: ["*"]`; if narrowing lanes
    # were load-bearing this assertion would be the thing that noticed.
    truth("the gate does not consult the agent's lane list",
          "lanes" not in transitions._AGENT_CLAIMABLE)


def test_the_cli_deters_as_operator_from_a_fleet_terminal():
    """Option D of q0305, and it DRIVES THE SHIPPED BINARY because the gate is at the door.

    A test that called `store.apply` would pass identically against the code before this, since
    the deterrent is deliberately NOT in the transition -- the console passes `as_operator=True`
    as a Python kwarg and can be started from a shell holding `SWARM_PARENT_TASK`, so an env
    check in the verb would refuse the operator's own unattended surface.

    THE LAST TWO CHECKS ARE THE POINT OF THE WHOLE THING. They assert that the refusal SAYS it
    is not a boundary and names the two ways past it. A refusal string that reads as a control
    is the failure this test exists to catch, because the next reader stops looking.
    """
    import subprocess
    reset()
    tid = store.apply("post", title="held, and unclassified", lane="engine",
                      posted_by="operator")["id"]
    swarm = str(ROOT / "engine/bin/swarm")

    for var in ("SWARM_PARENT_TASK", "SWARM_AGENT"):
        r = subprocess.run([swarm, "set", tid, "agent_claimable", "true",
                            "--agent", "T9", "--as-operator"],
                           capture_output=True, text=True, env={**os.environ, var: "T9"})
        eq(f"refused with {var} set, exit 6", r.returncode, 6)
        truth(f"the refusal with {var} names the flag and the terminal",
              "--as-operator" in r.stderr and "fleet terminal" in r.stderr,
              f"stderr: {r.stderr.strip()[:200]}")
    eq("and the row is still held after both attempts",
       reads.task(tid)["agent_claimable"], False)

    r = subprocess.run([swarm, "set", tid, "agent_claimable", "true", "--as-operator"],
                       capture_output=True, text=True,
                       env={k: v for k, v in os.environ.items()
                            if k not in ("SWARM_AGENT", "SWARM_PARENT_TASK")})
    truth("a shell that is NOT a fleet terminal is not refused by this door",
          "fleet terminal" not in r.stderr, f"stderr: {r.stderr.strip()[:200]}")

    r = subprocess.run([swarm, "set", tid, "agent_claimable", "true",
                        "--agent", "T9", "--as-operator"],
                       capture_output=True, text=True, env={**os.environ, "SWARM_AGENT": "T9"})
    truth("THE REFUSAL SAYS IT IS A DETERRENT AND NOT A BOUNDARY, in the message itself",
          "NOT A BOUNDARY" in r.stderr, f"stderr: {r.stderr.strip()[:200]}")
    truth("and names both ways past it: unsetting the vars, and the DSN that skips the CLI",
          "env -u" in r.stderr and "psycopg2" in r.stderr,
          f"stderr: {r.stderr.strip()[:200]}")


def test_a_human_row_resists_every_login_including_raw_sql():
    """The coercion is the DATABASE'S, and it is what actually protects the named rows.

    Task 0295 re-measurement. `test_a_human_row_cannot_be_promoted` pins this through the verb;
    this pins it one layer down, as `brain_operator` with no transition in the call path, because
    the whole finding on 0295 is that a fleet process can open that login. Trigger 1
    (`work_item_human_is_never_agent_claimable`) COERCES rather than refuses and it is not
    conditioned on `session_user`, so no login is exempt -- which is why the operator's 14
    `actor_type='human'` rows were never one `--as-operator` away, and the 79 unclassified held
    rows on live `brain` (read 2026-08-19) were.

    THE SECOND HALF IS THE HOLE THAT IS ACTUALLY THERE. Clearing `actor_type` is refused by
    nothing, so the human rows are reachable in two raw UPDATEs. `actor_type` is not in `set`'s
    settable list, so that route cannot be typed through the CLI at all -- it needs SQL, which is
    a more deliberate act than a flag, and it is the residual q0305's option B is about.
    """
    reset()
    human = store.apply("post", title="his own, marked human", lane="client",
                        posted_by="operator", actor_type="human")["id"]
    unclassified = store.apply("post", title="held because nobody classified it", lane="engine",
                               posted_by="operator")["id"]

    import psycopg2
    from store import session
    conn = psycopg2.connect(**session.dsn("operator"))
    try:
        cur = conn.cursor()
        cur.execute("SELECT session_user")
        eq("this connection really is the operator's login", cur.fetchone()[0], "brain_operator")

        cur.execute("UPDATE brain.work_item SET agent_claimable = true, workdir = '/tmp' "
                    "WHERE id = %s RETURNING agent_claimable", (human,))
        eq("a raw UPDATE as brain_operator is COERCED on a human-actor row",
           cur.fetchone()[0], False)

        cur.execute("UPDATE brain.work_item SET agent_claimable = true, workdir = '/tmp' "
                    "WHERE id = %s RETURNING agent_claimable", (unclassified,))
        eq("but the same UPDATE lands on an unclassified held row",
           cur.fetchone()[0], True)

        cur.execute("UPDATE brain.work_item SET actor_type = NULL WHERE id = %s "
                    "RETURNING actor_type", (human,))
        eq("nothing refuses CLEARING actor_type, which is the residual", cur.fetchone()[0], None)
        cur.execute("UPDATE brain.work_item SET agent_claimable = true WHERE id = %s "
                    "RETURNING agent_claimable", (human,))
        eq("so the human row is reachable in TWO raw UPDATEs, not one", cur.fetchone()[0], True)
        conn.rollback()
    finally:
        conn.close()

    try:
        store.apply("set", id=human, key="actor_type", value="agent", agent="operator",
                    as_operator=True)
        bad("`set actor_type` is refused", "it was accepted")
    except Exception as e:                                       # noqa: BLE001
        truth("`set actor_type` is refused: it is not in the settable set",
              "not settable" in str(e), f"raised: {e}")


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print("test_actor_gate.py  --  the operator's own work is not handed out by `claim`")
    print(f"  store: {os.environ['BRAIN_PG_DB']} (scratch)\n")
    for t in tests:
        print(f"{t.__name__.replace('test_', '')}: "
              f"{t.__doc__.splitlines()[0] if t.__doc__ else ''}")
        try:
            t()
        except Exception:                                        # noqa: BLE001
            import traceback
            tb = traceback.format_exc()
            bad(f"{t.__name__} raised", tb.strip().replace("\n", "\n        "))
        print()
    print(f"{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
