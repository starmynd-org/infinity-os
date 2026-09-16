#!/usr/bin/env python3
"""DISPATCHING THE SAME PROPOSAL TWICE AT ONCE: does `recommend accept` spawn one task or two?

`queue/human_queue/transitions.py::recommend_accept` is the only path from a proposal to executed
work, and its sixth refusal exists for exactly this: "a recommendation that is not open --
accepting twice would spawn the work twice." That refusal is a SELECT followed by an UPDATE:

    rec = ctx.one("SELECT * FROM brain.recommendation WHERE id = %s::bigint", ...)
    if rec["state"] != "open":  raise QueueError(...)
    ...
    ctx.execute("UPDATE brain.recommendation SET state = 'accepted', ... WHERE id = %s::bigint")

No `FOR UPDATE` on the read, and no `WHERE state = 'open'` on the write. Read-then-write with
nothing holding the row in between is the shape that is correct alone and wrong in company.

THE DATABASE DOES NOT CLOSE IT EITHER, AND SAYS SO IN ITS OWN WORDS.
`queue/schema/0015_recommendation_human_login.sql` gates acceptance a second time in a trigger, and
its header states the residual under the heading "WHAT THIS DOES **NOT** CLOSE":

    THE STATE COLUMN IS ONE STATEMENT DEEP ... nothing here guards
    `brain.recommendation.state` transitions in general, only the transition INTO `accepted`.

The trigger body is guarded by `IF NEW.state = 'accepted' AND OLD.state IS DISTINCT FROM
'accepted'`, so a second UPDATE arriving after the first has committed sees OLD.state = 'accepted',
skips the whole block, and raises nothing.

So both gates are about WHO decides, and neither is about HOW MANY TIMES. This suite measures the
consequence instead of arguing about it.

WHY IT IS WORTH MEASURING AT ALL, given the operator clicks one button. Two reasons, neither
hypothetical: the console's option rail dispatches over HTTP, and a double-submit, a retried
request, or two open tabs are the ordinary ways one intention becomes two calls. And
`projects/infinity-os-consolidation/PLAN.md` makes native routines a condition of Paperclip's
retirement -- routines dispatch on a schedule, without a human between the timer and the verb, and
Paperclip closes this natively with `routine_run_dispatch_fingerprint`. A scheduler on top of a
non-idempotent dispatch is how one nightly routine becomes two nightly tasks.

WHAT IS MEASURED
  scene 1  the SEQUENTIAL control: accept, then accept again, one after the other. The guard must
           refuse. If this fails the suite is measuring a missing guard rather than a race, and
           says so rather than reporting a race it did not observe.
  scene 2  THE RACE: N separate PROCESSES on N separate connections, blocked on a start file so
           they collide, all accepting the SAME recommendation. Threads would not do; this follows
           `engine/tests/test-claim-race.sh`, whose own comment says twelve processes on twelve
           connections is what actually races.
  scene 3  THE POSITIVE CONTROL, and it is the scene that makes scene 2 worth reading: the same
           harness, the same N processes, pointed at N DIFFERENT recommendations, must produce N
           spawned tasks. Without it, "scene 2 spawned one task" is equally well explained by a
           counter that cannot count past one, or by racers that never ran.
  scene 4  ACCEPT versus REJECT on one recommendation: the two deciding verbs contend with each
           other, and the loser must not overwrite the winner's decision.
  scene 5  ONE ANSWER WINS: `answer` has the same shape, and the caller racing it is a CLOCK --
           `queue default fire` runs unattended at 07:00 and 19:00 and composes `answer`, and its
           own refusal promises a default "fires into silence, not over an answer".

Scene 2 counts spawned work by reading the work items themselves -- every one carries "Executing
accepted recommendation r<id>." in its `brief`, written by the verb -- and NOT by reading
`brain.recommendation.spawned_work_item`, which holds a single id and would report one no matter
how many tasks exist. Counting the column would hide precisely the defect being looked for.

Run:  ENGINE_SCRATCH_DB=<scratch> BRAIN_PG_DB=<scratch> \
      python3 engine/tests/test_dispatch_idempotency.py        (a scratch database, never `brain`)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT / "queue"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

os.environ.setdefault("BRAIN_PG_DB", os.environ.get("ENGINE_SCRATCH_DB", "brain_scratch"))

from _scratch_preflight import reconcile                                        # noqa: E402

DB = os.environ["BRAIN_PG_DB"]
if DB == "brain":
    sys.exit("refusing to run against the live store. Set BRAIN_PG_DB to a scratch database.")
reconcile(DB)

# The runner exports this into every terminal, and `recommend accept` refuses a process that
# carries it: a fleet terminal working a task is not the operator. Popping it is what makes this
# suite about the RACE rather than about an environment variable.
os.environ.pop("SWARM_PARENT_TASK", None)

import psycopg2                                                                 # noqa: E402
import store                                                                    # noqa: E402
from store import session                                                       # noqa: E402
from swarm_engine import transitions as _engine        # noqa: E402,F401  (registers verbs)
import human_queue                                     # noqa: E402,F401  (registers verbs)

SECRETS = Path(os.environ.get("BRAIN_SECRET_DIR", Path.home() / ".brain-postgres-secrets"))
SCRATCH = str(ROOT / "engine/bin/scratch-db.sh")
RACERS = 8

PASS: list[str] = []
FAIL: list[str] = []


def ok(what: str, detail: str = "") -> None:
    PASS.append(what)
    print(f"  ok    {what}" + (f"\n        {detail}" if detail else ""))


def bad(what: str, detail: str = "") -> None:
    FAIL.append(what)
    print(f"  FAIL  {what}" + (f"\n        {detail}" if detail else ""))


def eq(what: str, got, want) -> None:
    (ok if got == want else bad)(what, f"got {got!r}, want {want!r}")


def provisioned() -> bool:
    """Is there an operator login on this host? Reported, never skipped silently."""
    return (SECRETS / "brain-postgres-role-operator").is_file()


def the_human() -> str:
    """The name the database gives the OPERATOR's connection, asked on that login."""
    try:
        conn = psycopg2.connect(**session.dsn("operator"))
    except Exception:                                        # noqa: BLE001 -- no credential here
        return ""
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT brain.current_human()")
            row = cur.fetchone()
        return (row[0] if row else "") or ""
    finally:
        conn.close()


def reset() -> None:
    subprocess.run([SCRATCH, "truncate"], check=True, capture_output=True,
                   env={**os.environ, "ENGINE_SCRATCH_DB": DB})


def a_recommendation(text: str) -> dict:
    """A real recommendation about a real subject, the shape task 0222 dispatched."""
    subject = store.apply("post", title=f"subject for {text}", lane="data",
                          workdir="/tmp", agent_claimable=True)
    return store.apply("recommend", text=text, proposed_action=f"execute: {text}",
                       subject_type="work_item", subject_id=subject["id"],
                       template_id="playbook-restate-a-metric")


def spawned_for(rid) -> list[str]:
    """Every work item this recommendation actually produced, read off the ITEMS.

    Not off `recommendation.spawned_work_item`, which is one column and would answer "one" however
    many exist. The verb writes "Executing accepted recommendation r<id>." into each `brief`, so
    the briefs are the honest denominator.
    """
    with store.read("runtime") as s:
        rows = s.query(
            "SELECT id FROM brain.work_item WHERE brief LIKE %s ORDER BY id",
            (f"%Executing accepted recommendation r{rid}.%",))
    return [r["id"] for r in rows]


def rec(rid) -> dict:
    with store.read("runtime") as s:
        return s.one("SELECT * FROM brain.recommendation WHERE id = %s::bigint",
                     (str(rid),)) or {}


# ------------------------------------------------------------------ the racer, as a child process

RACER = r'''
import json, os, sys, time
ROOT = os.environ["RACE_ROOT"]
sys.path.insert(0, ROOT + "/engine")
sys.path.insert(0, ROOT + "/queue")
sys.path.insert(0, ROOT)
os.environ.pop("SWARM_PARENT_TASK", None)
import store
from store import session
from swarm_engine import transitions as _e
import human_queue
import psycopg2

rid   = os.environ["RACE_REC"]
verb  = os.environ.get("RACE_VERB", "recommend accept")
who   = os.environ["RACE_WHO"]
gate  = os.environ["RACE_GATE"]
out   = os.environ["RACE_OUT"]

# WARM THE CONNECTION BEFORE THE BARRIER. `store.apply` opens its own connection inside the call,
# so without this the race would be a race between TCP handshakes and password lookups, and the
# first racer to finish authenticating would win every time -- which looks like a serialised
# system and would report a false green.
try:
    c = psycopg2.connect(**session.dsn("operator")); c.close()
except Exception as e:
    json.dump({"who": who, "error": f"no operator connection: {e}"}, open(out, "w")); sys.exit(0)

while not os.path.exists(gate):
    time.sleep(0.002)

t0 = time.monotonic()
res, err = None, ""
try:
    if verb == "answer":
        txt = f"decided by {who}: ship it after the audit"
        store.apply("answer", qid=rid, text=txt)
        res = txt
    elif verb == "recommend reject":
        r = store.apply("recommend reject", id=rid, by=os.environ["RACE_HUMAN"],
                        reason="not now, the margin table is being restated elsewhere",
                        as_operator=True)
        res = "rejected"
    else:
        r = store.apply("recommend accept", id=rid, by=os.environ["RACE_HUMAN"],
                        lane="data", as_operator=True, execute=True)
        res = r.get("spawned_work_item")
except Exception as e:
    err = str(e) or e.__class__.__name__
t1 = time.monotonic()
json.dump({"who": who, "spawned": res, "error": err, "t0": t0, "t1": t1}, open(out, "w"))
'''


def race(pairs, human: str, tmp: Path) -> list[dict]:
    """Fire one process per (racer-name, recommendation-id), all released by one file.

    `pairs` carries a recommendation id per racer so the same harness serves scene 2 (all the same
    id) and scene 3 (all different ones). One harness, two scenes, so the control tests the
    instrument that produced the finding rather than a second instrument.
    """
    gate = tmp / "GO"
    if gate.exists():
        gate.unlink()
    racer_py = tmp / "racer.py"
    racer_py.write_text(RACER)

    procs = []
    for entry in pairs:
        who, rid = entry[0], entry[1]
        verb = entry[2] if len(entry) > 2 else "recommend accept"
        out = tmp / f"out-{who}.json"
        if out.exists():
            out.unlink()
        env = {**os.environ, "RACE_ROOT": str(ROOT), "RACE_REC": str(rid), "RACE_WHO": who,
               "RACE_GATE": str(gate), "RACE_OUT": str(out), "RACE_HUMAN": human,
               "RACE_VERB": verb}
        env.pop("SWARM_PARENT_TASK", None)
        procs.append((who, out, subprocess.Popen([sys.executable, str(racer_py)], env=env,
                                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)))
    time.sleep(1.5)                     # let every child reach the barrier with a warm connection
    gate.write_text("go")
    for _who, _out, p in procs:
        p.wait(timeout=180)

    results = []
    for who, out, p in procs:
        if out.exists():
            results.append(json.loads(out.read_text()))
        else:
            results.append({"who": who, "error": "no result file: "
                                                 + (p.stderr.read().decode()[-400:] if p.stderr else "")})
    return results


def overlap(results: list[dict]) -> int:
    """How many racers were inside the verb at the same moment as at least one other.

    A race that did not overlap is not a race, and a green from it means nothing. This is the same
    check `test-claim-race.sh` prints as "claims overlapping in time 12/12".
    """
    spans = [(r["t0"], r["t1"]) for r in results if "t0" in r]
    n = 0
    for i, (a0, a1) in enumerate(spans):
        if any(j != i and b0 < a1 and a0 < b1 for j, (b0, b1) in enumerate(spans)):
            n += 1
    return n


# ------------------------------------------------------------------ scenes

def scene_1_sequential_control(human: str) -> None:
    print("\nscene 1: the SEQUENTIAL control -- accepting twice in a row must be refused")
    reset()
    r = a_recommendation("restate the 2025 margin table")
    first = store.apply("recommend accept", id=r["id"], by=human, lane="data",
                        as_operator=True, execute=True)
    ok("the first acceptance dispatched", f"spawned {first.get('spawned_work_item')}")
    try:
        store.apply("recommend accept", id=r["id"], by=human, lane="data",
                    as_operator=True, execute=True)
        bad("the second acceptance is refused", "it was ACCEPTED a second time, sequentially")
    except Exception as e:                                   # noqa: BLE001
        msg = str(e)
        ok("the second acceptance is refused", msg[:160])
        (ok if "already" in msg else bad)(
            "and the refusal says the recommendation is already decided", msg[:160])
    eq("exactly one work item exists for it", len(spawned_for(r["id"])), 1)


def scene_2_the_race(human: str, tmp: Path) -> dict:
    print(f"\nscene 2: THE RACE -- {RACERS} processes accepting the SAME recommendation at once")
    reset()
    r = a_recommendation("restate the 2025 margin table")
    rid = r["id"]
    results = race([(f"r{i}", rid) for i in range(RACERS)], human, tmp)

    ran = [x for x in results if "t0" in x]
    over = overlap(results)
    wins = [x for x in ran if not x["error"]]
    refused = [x for x in ran if x["error"]]
    items = spawned_for(rid)

    print(f"  racers launched {RACERS}   reached the verb {len(ran)}   overlapping in time {over}")
    print(f"  returned success {len(wins)}   refused {len(refused)}")
    print(f"  work items carrying 'accepted recommendation r{rid}': {len(items)}  {items}")
    print(f"  recommendation.spawned_work_item records: {rec(rid).get('spawned_work_item')!r}")

    if not ran:
        bad("the racers reached the verb", "0 of them did; this scene measured nothing")
        return {"ran": 0}
    if over < 2:
        bad("the racers actually overlapped in time",
            f"{over} of {len(ran)} overlapped, so nothing was raced and a green here is vacuous")
    else:
        ok("the racers actually overlapped in time", f"{over} of {len(ran)}")

    eq("exactly ONE work item was spawned", len(items), 1)
    eq("exactly ONE racer was allowed to dispatch", len(wins), 1)

    recorded = rec(rid).get("spawned_work_item")
    orphans = [i for i in items if i != recorded]
    if orphans:
        bad("no orphaned task exists",
            f"{len(orphans)} task(s) {orphans} were spawned by this recommendation and are NOT "
            f"what it records ({recorded!r}). Real work, running, with nothing pointing at it.")
    else:
        ok("no orphaned task exists", f"the one recorded is {recorded!r}")
    return {"ran": len(ran), "over": over, "wins": len(wins), "items": len(items),
            "orphans": len(orphans)}


def scene_3_positive_control(human: str, tmp: Path) -> None:
    print(f"\nscene 3: POSITIVE CONTROL -- the same harness on {RACERS} DIFFERENT recommendations")
    print("         must produce 8 tasks, or scene 2's '1' means the instrument, not the system")
    reset()
    recs = [a_recommendation(f"restate table {i}") for i in range(RACERS)]
    results = race([(f"c{i}", recs[i]["id"]) for i in range(RACERS)], human, tmp)

    ran = [x for x in results if "t0" in x]
    over = overlap(results)
    total = sum(len(spawned_for(r["id"])) for r in recs)
    wins = len([x for x in ran if not x["error"]])
    print(f"  racers reached the verb {len(ran)}   overlapping in time {over}")
    print(f"  work items spawned across all {RACERS} recommendations: {total}")

    if not ran:
        bad("the control racers reached the verb", "0 did; the control proves nothing")
        return
    eq(f"the counter can see {RACERS} spawned tasks", total, RACERS)
    eq(f"all {RACERS} independent dispatches succeeded", wins, RACERS)
    (ok if over >= 2 else bad)("the control also genuinely overlapped", f"{over} of {len(ran)}")


def scene_4_accept_versus_reject(human: str, tmp: Path) -> None:
    """The two deciding verbs contend with EACH OTHER, not only with themselves.

    A doubled rejection is cheap -- a rejection dispatches nothing. A rejection INTERLEAVED with an
    acceptance is not: unlocked, both read `state = 'open'`, both pass, and the last writer owns
    `state`, `decided_at` and `decided_by`. An acceptance that really did spawn a task can end up
    recorded as `rejected`, with the task still running, and `brain.queue_acted_on` -- which counts
    by STATE and groups by TEMPLATE -- counting it as a pruned playbook. The falsifier for the whole
    queue layer would be reading a rejection off work that is executing.

    So the invariant is not "one accept wins". It is: ONE decision wins, and the spawned-task count
    agrees with which one it was. Both outcomes are legitimate; disagreement between them is not.
    """
    print(f"\nscene 4: ACCEPT versus REJECT -- {RACERS} processes, half each, one recommendation")
    reset()
    r = a_recommendation("restate the 2025 margin table")
    rid = r["id"]
    half = RACERS // 2
    pairs = ([(f"a{i}", rid, "recommend accept") for i in range(half)]
             + [(f"j{i}", rid, "recommend reject") for i in range(RACERS - half)])
    results = race(pairs, human, tmp)

    ran = [x for x in results if "t0" in x]
    over = overlap(results)
    wins = [x for x in ran if not x["error"]]
    items = spawned_for(rid)
    final = rec(rid)

    print(f"  racers reached the verb {len(ran)}   overlapping in time {over}")
    print(f"  decisions that succeeded {len(wins)}: {[w['spawned'] for w in wins]}")
    print(f"  final state {final.get('state')!r}   decided_by {final.get('decided_by')!r}   "
          f"spawned_work_item {final.get('spawned_work_item')!r}")
    print(f"  work items carrying 'accepted recommendation r{rid}': {len(items)}  {items}")

    if not ran:
        bad("the accept/reject racers reached the verb", "0 did; this scene measured nothing")
        return
    (ok if over >= 2 else bad)("the two verbs genuinely overlapped", f"{over} of {len(ran)}")
    eq("exactly ONE decision was recorded", len(wins), 1)

    state = final.get("state")
    if state == "accepted":
        eq("an accepted recommendation has exactly one task", len(items), 1)
    elif state == "rejected":
        eq("a REJECTED recommendation spawned no task at all", len(items), 0)
    else:
        bad("the recommendation reached a decided state", f"it is {state!r}")


def scene_5_one_answer_wins(human: str, tmp: Path) -> None:
    """`answer` is the same shape, and the caller it races is a CLOCK rather than a person.

    `queue default fire` runs unattended at 07:00 and 19:00 and composes `answer`, and its own
    refusal says a default "fires into silence, not over an answer". That promise is what the
    whole silence-window doctrine rests on, and unlocked it held only for callers arriving one at
    a time: the operator answering AT a checkpoint and the checkpoint firing both read
    `answer IS NULL`, both pass, and the last writer wins. A machine-written default landing on
    top of a human decision, recorded as though silence shipped it, is the exact outcome the
    doctrine forbids.

    Racing `answer` against itself with DISTINGUISHABLE texts is the sharp version of the test:
    it is not enough that one write lands, the stored answer has to be the winner's own words.
    Proving `answer` atomic covers the scheduled path too, because the default composes it.
    """
    print(f"\nscene 5: ONE ANSWER WINS -- {RACERS} processes answering one question at once")
    reset()
    subject = store.apply("post", title="a task that needs a decision", lane="data",
                          workdir="/tmp", agent_claimable=True)
    q = store.apply("ask", question="ship the restate now or after the audit?",
                    agent="T-asker", task=subject["id"],
                    default="wait for the audit before shipping anything")
    qid = q["id"]
    results = race([(f"ans{i}", qid, "answer") for i in range(RACERS)], human, tmp)

    ran = [x for x in results if "t0" in x]
    over = overlap(results)
    wins = [x for x in ran if not x["error"]]
    with store.read("runtime") as s:
        stored = (s.one("SELECT answer FROM brain.question WHERE id = %s", (qid,)) or {}).get("answer")

    print(f"  racers reached the verb {len(ran)}   overlapping in time {over}")
    print(f"  answers accepted {len(wins)}: {[w['spawned'] for w in wins]}")
    print(f"  the stored answer is {stored!r}")

    if not ran:
        bad("the answer racers reached the verb", "0 did; this scene measured nothing")
        return
    (ok if over >= 2 else bad)("the answers genuinely overlapped", f"{over} of {len(ran)}")
    eq("exactly ONE answer was recorded", len(wins), 1)
    if wins:
        (ok if stored == wins[0]["spawned"] else bad)(
            "the stored answer is the winner's own words, not a loser's",
            f"stored {stored!r}, winner wrote {wins[0]['spawned']!r}")


def main() -> int:
    print(f"test_dispatch_idempotency.py -- one proposal, many callers, how many tasks?")
    print(f"  store: {DB} (scratch)")

    if not provisioned():
        print("\nNOT RUN: there is no operator credential on this host "
              f"({SECRETS}/brain-postgres-role-operator is absent), and `recommend accept` is "
              "refused by migration 32 from any other login. This suite cannot measure anything "
              "here. Provision with store/bin/provision-operator.sh.")
        return 1
    human = the_human()
    if not human:
        print("\nNOT RUN: the operator credential exists but brain.current_human() returned NULL "
              f"on database {DB}. The per-database half of migration 20 is not applied here; "
              f"`scratch-db.sh create` maps it. Nothing was measured.")
        return 1
    print(f"  the database calls this connection: {human!r}")

    tmp = Path(os.environ.get("TMPDIR", "/tmp")) / f"dispatch-race-{os.getpid()}"
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        scene_1_sequential_control(human)
        scene_2_the_race(human, tmp)
        scene_3_positive_control(human, tmp)
        scene_4_accept_versus_reject(human, tmp)
        scene_5_one_answer_wins(human, tmp)
    finally:
        for f in tmp.glob("*"):
            f.unlink()
        tmp.rmdir()

    total = len(PASS) + len(FAIL)
    if total == 0:                                                          # DENOMINATOR
        print("\nDENOMINATOR: 0 comparisons made. A verdict over an empty set is not a pass.")
        return 2
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed (over 5 scenes)")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
