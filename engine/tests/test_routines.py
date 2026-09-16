#!/usr/bin/env python3
"""EIGHT TIMERS, ONE SLOT: does a routine post one task or eight?

`engine/swarm_engine/routines.py` is the last buildable half of `PLAN.md:255`'s board-retirement
condition, and the reason row `0377` (dispatch idempotency) had to close before this one could
open is that a scheduler on a non-idempotent dispatch is how one nightly routine becomes two
nightly tasks. Row 0377 measured it on the verb next door: eight concurrent callers, ONE
recommendation, EIGHT work items and SEVEN orphans, on a verb whose own refusal read "accepting
twice would spawn the work twice". It was right about the consequence and unable to prevent it.

So this suite does not assert that a routine has a guard. It watches the guard FAIL first.

WHY THE INSTRUMENT IS DIFFERENT FROM 0377'S, which is the design claim under test here. Row 0377
fixed `recommend accept` with `SELECT ... FOR UPDATE`. That locks a row THAT EXISTS: a
recommendation is there before anyone accepts it, so the losers queue on it, wake, re-read, and
are refused. A ROUTINE OCCURRENCE DOES NOT EXIST until the first fire creates it, so there is
nothing to lock and eight callers all find no row for slot k and all proceed. The only instrument
that refuses a row that is not there yet is a UNIQUE INDEX, and scene 2 is this suite proving that
claim by removing it and measuring what happens.

WHAT IS MEASURED
  scene 1  SEQUENTIAL CONTROL. Fire a slot, then fire it again, one after the other. If this
           fails the suite is measuring a missing guard rather than a race and says so.
  scene 2  THE GUARD, WATCHED FAILING. `routine_run_one_per_slot` is DROPPED, then N processes
           on N connections race one slot. This must produce MORE THAN ONE work item. A green
           here is a failure of the experiment, not a pass: it means the racers never collided
           and scene 3 would be measuring nothing.
  scene 3  THE RACE, with the constraint back. Same N processes, same slot: exactly one work
           item, exactly one routine_run row, and every loser refused by Postgres with 23505.
  scene 4  POSITIVE CONTROL. The same harness pointed at N DIFFERENT routines must produce N
           work items. Without it, "scene 3 produced one" is equally well explained by a counter
           that cannot count past one or by racers that never ran.
  scene 5  NO ORPHANS. After scene 3, the surviving routine_run row points at a work item that
           exists, and no work item exists that no routine_run points at.
  scene 6  THE KILL SWITCH, both gates and the asymmetry. Disabling needs no credential;
           `routine_due` drops the routine; `routine fire` refuses it by name; and a raw
           `brain_runtime` UPDATE re-arming it is refused by migration 35's trigger.
  scene 7  THE FLEET PAUSE, honoured. `swarm pause` is the operator's one switch over
           everything and a scheduler that ignored it would make that switch mean less than it
           says.
  scene 8  THE POINTER THAT MUST NOT BE FABRICATED. A routine that dispatches fleet work with no
           absolute workdir is refused when it is WRITTEN, by the verb and again by a CHECK, and
           not at 03:20 by `post` into a journal nobody reads.

Run:  ENGINE_SCRATCH_DB=<scratch> BRAIN_PG_DB=<scratch> \
      python3 engine/tests/test_routines.py        (a scratch database, never `brain`)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
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

# `routine add` refuses a process that carries this, through `post`'s own agent-claimable rules
# and through the operator login it opens. Popping it is what makes this suite about the RACE.
os.environ.pop("SWARM_PARENT_TASK", None)

import psycopg2                                                                 # noqa: E402
import store                                                                    # noqa: E402
from store import session                                                       # noqa: E402
from swarm_engine import routines                        # noqa: E402,F401  (registers the verbs)
from swarm_engine import transitions as _engine          # noqa: E402,F401

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
    """Is there an operator login on this host? Reported, never skipped silently.

    `routine add` is gated on `brain.current_human()` by design (migration 35), so a host with no
    operator credential cannot arm a routine at all and this suite cannot construct its subject.
    That is an inconclusive run, not a green one.
    """
    return (SECRETS / "brain-postgres-role-operator").is_file()


def reset() -> None:
    subprocess.run([SCRATCH, "truncate"], check=True, capture_output=True,
                   env={**os.environ, "ENGINE_SCRATCH_DB": DB})


def psql(sql: str) -> subprocess.CompletedProcess:
    return subprocess.run([SCRATCH, "psql", "-q", "-c", sql], capture_output=True, text=True,
                          env={**os.environ, "ENGINE_SCRATCH_DB": DB})


def drop_the_index() -> None:
    if not index_present():
        bad("the control arm found routine_run_one_per_slot to drop",
            "it was ALREADY absent from this store, so scene 2 would measure a defect that a "
            "previous run left behind rather than one it created. Rebuild the scratch database.")
        return
    r = psql("ALTER TABLE brain.routine_run DROP CONSTRAINT routine_run_one_per_slot")
    if r.returncode != 0:
        bad("the control arm could drop routine_run_one_per_slot", r.stderr.strip()[:300])


def restore_the_index() -> None:
    """Put it back. THE RESET MUST COME FIRST and that is not a detail.

    The control arm leaves eight rows for one slot in `routine_run`, so ADD CONSTRAINT UNIQUE
    over them fails on the duplicates it was dropped to allow. Measured on the first run of this
    suite: the restore failed, the database was left WITHOUT the constraint, and scene 3 then
    reported eight work items and read as a broken guard. The suite was right and the store was
    the thing that had changed. `ensure_the_index` in `main` is the second belt.
    """
    if index_present():
        return
    reset()
    r = psql("ALTER TABLE brain.routine_run ADD CONSTRAINT routine_run_one_per_slot "
             "UNIQUE (routine_id, scheduled_for)")
    if r.returncode != 0:
        bad("the control arm could put routine_run_one_per_slot back", r.stderr.strip()[:300])


def index_present() -> bool:
    with store.read("runtime") as s:
        return bool(s.scalar("SELECT count(*) FROM pg_constraint "
                             "WHERE conname = 'routine_run_one_per_slot'"))


def a_routine(name: str, **kw) -> dict:
    """One routine, anchored so its current slot is NOW and its grace is wide.

    Anchored in the past by exactly one period, so `routine_slot(anchor, period, now())` is the
    anchor itself and every racer computes the same slot without depending on when the suite runs.
    """
    period = kw.pop("period_minutes", 1440)
    anchor = kw.pop("anchor_at", datetime.now(timezone.utc) - timedelta(minutes=1))
    return store.apply("routine add", name=name, title=f"work from {name}", lane="ops",
                       body=f"the standing work order of {name}", period_minutes=period,
                       anchor_at=anchor, as_operator=True, **kw)


def items_posted_by(name: str) -> list:
    """Every work item this routine actually produced, read off THE ITEMS.

    Not off `routine_run.work_item_id`, which is one column per slot and would answer "one"
    however many items exist. `post` writes `posted_by = routine:<name>` onto the item itself, so
    the items are the honest denominator, which is the same reasoning
    `test_dispatch_idempotency.py` gives for counting briefs rather than `spawned_work_item`.
    """
    with store.read("runtime") as s:
        return [r["id"] for r in s.query(
            "SELECT id FROM brain.work_item WHERE posted_by = %s ORDER BY id",
            (f"routine:{name}",))]


def run_rows(name: str) -> list:
    with store.read("runtime") as s:
        return s.query(
            "SELECT rr.* FROM brain.routine_run rr JOIN brain.routine r ON r.id = rr.routine_id "
            "WHERE r.name = %s ORDER BY rr.id", (name,))


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
from swarm_engine import routines
import psycopg2

name = os.environ["RACE_ROUTINE"]
who  = os.environ["RACE_WHO"]
gate = os.environ["RACE_GATE"]
out  = os.environ["RACE_OUT"]

# WARM THE CONNECTION BEFORE THE BARRIER. `store.apply` opens its own connection inside the call,
# so without this the race would be between TCP handshakes and secret lookups and the first racer
# to finish authenticating would win every time, which looks like a serialised system and would
# report a false green.
try:
    c = psycopg2.connect(**session.dsn("runtime")); c.close()
except Exception as e:
    json.dump({"who": who, "error": "no runtime connection: %s" % e}, open(out, "w")); sys.exit(0)

while not os.path.exists(gate):
    time.sleep(0.002)

res, err, sqlstate = None, "", ""
try:
    res = store.apply("routine fire", name=name, by=who)
except Exception as e:
    err = str(e)
    sqlstate = getattr(getattr(e, "__cause__", None), "pgcode", "") or ""
json.dump({"who": who, "result": res, "error": err, "sqlstate": sqlstate},
          open(out, "w"), default=str)
'''


def race(names, tmp: Path) -> list:
    """N processes on N connections, barrier released. Threads would not do.

    This follows `engine/tests/test-claim-race.sh`, whose own comment says twelve processes on
    twelve connections is what actually races: a GIL-bound thread pool serialises at exactly the
    point being measured.
    """
    gate = tmp / "gate"
    if gate.exists():
        gate.unlink()
    procs, outs = [], []
    for i, name in enumerate(names):
        out = tmp / f"out-{i}.json"
        outs.append(out)
        procs.append(subprocess.Popen(
            [sys.executable, "-c", RACER],
            env={**os.environ, "RACE_ROOT": str(ROOT), "RACE_ROUTINE": name,
                 "RACE_WHO": f"racer-{i}", "RACE_GATE": str(gate), "RACE_OUT": str(out)},
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    time.sleep(1.5)                       # every child is warmed and spinning on the gate
    gate.write_text("go")
    for p in procs:
        p.wait(timeout=120)
    got = []
    for o in outs:
        got.append(json.loads(o.read_text()) if o.exists() else {"error": "no output file"})
    return got


# ------------------------------------------------------------------ scenes

def scene_1_sequential_control() -> None:
    print("\nSCENE 1  the sequential control: fire a slot, then fire it again")
    reset()
    a_routine("nightly")
    first = store.apply("routine fire", name="nightly", by="scene1")
    eq("the first fire posts one work item", bool(first["work_item_id"]), True)
    try:
        store.apply("routine fire", name="nightly", by="scene1")
        bad("the second fire of one slot is refused", "it was ACCEPTED")
    except Exception as e:                                                # noqa: BLE001
        ok("the second fire of one slot is refused", str(e)[:160])
    eq("one work item exists, over 2 sequential calls", len(items_posted_by("nightly")), 1)
    eq("one routine_run row exists, over 2 sequential calls", len(run_rows("nightly")), 1)


def scene_2_the_guard_watched_failing(tmp: Path) -> None:
    print(f"\nSCENE 2  THE CONTROL ARM: the unique index DROPPED, {RACERS} racers on one slot")
    print("         a green here would mean the racers never collided, so it is read as a FAILURE")
    reset()
    a_routine("nightly")
    drop_the_index()
    got = race(["nightly"] * RACERS, tmp)
    landed = [g for g in got if g.get("result")]
    items = items_posted_by("nightly")
    runs = run_rows("nightly")
    print(f"         returned success {len(landed)}   refused {len(got) - len(landed)}")
    print(f"         work items posted by routine:nightly: {len(items)}  {items}")
    outcomes = {}
    for r in runs:
        outcomes[r["outcome"]] = outcomes.get(r["outcome"], 0) + 1
    print(f"         routine_run rows for that ONE slot:   {len(runs)}  {outcomes}")
    print("         (rows can exceed items: a racer that reads the winner's still-open item "
          "records `skipped`)")
    orphans = [i for i in items if i not in {r["work_item_id"] for r in runs}]
    print(f"         ORPHANS (a work item no routine_run points at): {len(orphans)}  {orphans}")
    for g in got:
        if g.get("error"):
            print(f"         a refusal WITH NO CONSTRAINT IN PLACE: {g['error'][:200]}")
    (ok if len(items) > 1 else bad)(
        f"WITHOUT the unique index, {RACERS} racers produce MORE THAN ONE work item",
        f"{len(items)} items and {len(runs)} occurrence rows for one slot, over {RACERS} racers. "
        f"One nightly routine, {len(items)} nightly tasks. This is the defect the index exists to "
        f"prevent, measured rather than argued about.")
    # ORPHANS ARE ZERO HERE AND THAT IS A FINDING, not a pass. Row 0377's seven orphans came from
    # `recommendation.spawned_work_item` holding ONE id, so the last writer won and six real
    # tasks were pointed at by nothing. `routine_run` is a row per fire, so the pointer cannot be
    # overwritten and the same race produces DUPLICATION instead of ORPHANING. Both are wrong;
    # this one is merely easier to see afterwards, which is worth knowing when reading 0377's
    # numbers next to these.
    ok(f"the pointer is a row and not a column, so the control arm orphans {len(orphans)} of "
       f"{len(items)} items",
       "the same race on `recommend accept` orphaned 7 of 8 because spawned_work_item is one "
       "column and the last writer won. Duplication is the failure mode here instead.")
    restore_the_index()
    return {"items": len(items), "runs": len(runs), "landed": len(landed),
            "orphans": len(orphans)}


def scene_3_the_race(tmp: Path) -> dict:
    print(f"\nSCENE 3  THE RACE with routine_run_one_per_slot in place, {RACERS} racers, one slot")
    if not index_present():
        bad("scene 3 has a constraint to measure",
            "routine_run_one_per_slot is ABSENT from this store, so scene 3 would measure the "
            "control arm a second time. Nothing below this line means anything.")
        return {"items": 0, "runs": 0, "landed": 0, "refused": 0, "by_db": 0}
    reset()
    a_routine("nightly")
    got = race(["nightly"] * RACERS, tmp)
    landed = [g for g in got if g.get("result")]
    refused = [g for g in got if g.get("error")]
    items = items_posted_by("nightly")
    runs = run_rows("nightly")
    print(f"         returned success {len(landed)}   refused {len(refused)}")
    print(f"         work items posted by routine:nightly: {len(items)}  {items}")
    print(f"         routine_run rows for that ONE slot:   {len(runs)}")
    seen = set()
    for r in refused:
        head = (r.get("error") or "")[:60]
        if head in seen:
            continue
        seen.add(head)
        print(f"         a loser's refusal: {r['error'][:220]}")
    print(f"         distinct refusal shapes: {len(seen)}, over {len(refused)} refusals")
    eq(f"exactly ONE work item, over {RACERS} concurrent racers", len(items), 1)
    eq(f"exactly ONE routine_run row, over {RACERS} concurrent racers", len(runs), 1)
    eq(f"exactly ONE caller returned success, over {RACERS}", len(landed), 1)
    eq(f"the other {RACERS - 1} were refused, over {RACERS}", len(refused), RACERS - 1)
    # TWO REFUSAL SHAPES ARE CORRECT HERE AND THE SPLIT MOVES BETWEEN RUNS. A racer that starts
    # its transaction AFTER the winner committed is caught by the verb's own read, which is the
    # sentence layer working; a racer that started BEFORE is caught by the index, which is the
    # guard. Measured across runs of this suite: 6 to 7 of 7 by the index and the remainder by
    # the read. Asserting an exact split would be asserting a schedule. What must hold is that
    # EVERY loser is refused, and that the index is demonstrably one of the two doing it, since
    # the read alone is precisely what row 0377 measured failing.
    by_db = [r for r in refused if "unique index" in (r.get("error") or "")]
    by_read = [r for r in refused if "already decided the slot" in (r.get("error") or "")]
    (ok if by_db else bad)(
        f"at least one loser was refused BY THE DATABASE, over {len(refused)} refusals",
        f"{len(by_db)} of {len(refused)} name brain.routine_run's unique index; "
        f"{len(by_read)} arrived after the winner committed and met the verb's read first. "
        f"The read alone is what 0377 measured failing.")
    eq(f"every loser was refused by one of the two, over {len(refused)} refusals",
       len(by_db) + len(by_read), len(refused))
    return {"items": len(items), "runs": len(runs), "landed": len(landed),
            "refused": len(refused), "by_db": len(by_db)}


def scene_4_positive_control(tmp: Path) -> None:
    print(f"\nSCENE 4  the positive control: the same harness on {RACERS} DIFFERENT routines")
    reset()
    names = [f"r{i}" for i in range(RACERS)]
    for n in names:
        a_routine(n)
    got = race(names, tmp)
    landed = [g for g in got if g.get("result")]
    total = sum(len(items_posted_by(n)) for n in names)
    print(f"         returned success {len(landed)}   work items {total}")
    eq(f"{RACERS} different routines produce {RACERS} work items, over {RACERS} racers",
       total, RACERS)
    ok("so the harness CAN produce more than one, which is what makes scene 3 readable",
       f"{len(landed)} of {RACERS} racers landed against distinct routines")


def scene_5_no_orphans(measured: dict) -> None:
    print("\nSCENE 5  no orphans: every posted item is pointed at, and every pointer resolves")
    runs = run_rows("nightly")
    items = set(items_posted_by("nightly"))
    pointed = {r["work_item_id"] for r in runs if r["work_item_id"]}
    eq(f"every posted work item is pointed at by a routine_run, over {len(items)} items",
       sorted(items - pointed), [])
    eq(f"every routine_run pointer resolves to a real work item, over {len(pointed)} pointers",
       sorted(pointed - items), [])
    with store.read("runtime") as s:
        dangling = s.scalar(
            "SELECT count(*) FROM brain.routine_run rr LEFT JOIN brain.work_item w "
            "ON w.id = rr.work_item_id WHERE rr.work_item_id IS NOT NULL AND w.id IS NULL")
    eq("no routine_run points at a work item that does not exist, over the whole table",
       int(dangling), 0)


def scene_6_the_kill_switch() -> None:
    print("\nSCENE 6  the kill switch: one act, two gates, and the asymmetry")
    reset()
    a_routine("nightly")
    eq("the routine is due before anything is thrown, over 1 armed routine",
       [d["name"] for d in routines.due()], ["nightly"])

    # DISABLING NEEDS NO CREDENTIAL. `routine disable` is a `runtime` verb with no as_operator,
    # so this call connects as brain_runtime, the login every agent surface uses.
    res = store.apply("routine disable", name="nightly", by="scene6",
                      reason="proving one act is enough")
    eq("disable lands from a NON-human login, in one act", res["disabled"], True)
    eq("and it is refused a reason it did not give", res["disabled_reason"],
       "proving one act is enough")
    eq("gate 1: brain.routine_due no longer lists it, over 1 routine", len(routines.due()), 0)
    try:
        store.apply("routine fire", name="nightly", by="scene6")
        bad("gate 2: firing a disabled routine by name is refused", "it FIRED")
    except Exception as e:                                                # noqa: BLE001
        ok("gate 2: firing a disabled routine by name is refused", str(e)[:140])

    try:
        store.apply("routine disable", name="nightly", by="scene6", reason="")
        bad("a disable with no reason is refused", "it landed with no reason")
    except Exception as e:                                                # noqa: BLE001
        ok("a disable with no reason is refused", str(e)[:120])

    # RE-ARMING IS THE PRIVILEGED DIRECTION, and the trigger is the gate that holds when the verb
    # is bypassed entirely.
    try:
        store.apply("routine enable", name="nightly")          # no as_operator: brain_runtime
        bad("re-arming from a non-human login is refused by the verb", "it landed")
    except Exception as e:                                                # noqa: BLE001
        ok("re-arming from a non-human login is refused by the verb", str(e)[:120])

    conn = psycopg2.connect(**session.dsn("runtime"))
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE brain.routine SET disabled_at = NULL, disabled_by = NULL, "
                        "disabled_reason = NULL WHERE name = 'nightly'")
        bad("re-arming by raw SQL as brain_runtime is refused by the trigger", "it landed")
    except psycopg2.errors.InsufficientPrivilege as e:
        ok("re-arming by raw SQL as brain_runtime is refused by the trigger (migration 35)",
           str(e).strip().splitlines()[0][:140])
    except Exception as e:                                                # noqa: BLE001
        bad("re-arming by raw SQL as brain_runtime is refused by the trigger",
            f"refused, but not by the trigger: {type(e).__name__}: {e}")
    finally:
        conn.close()

    res = store.apply("routine enable", name="nightly", as_operator=True)
    eq("the operator can re-arm it", res["disabled"], False)
    eq("and it is due again, over 1 routine", len(routines.due()), 1)


def scene_7_the_fleet_pause() -> None:
    print("\nSCENE 7  the fleet pause, honoured rather than reimplemented")
    reset()
    a_routine("nightly")
    store.apply("pause", by="scene7", note="proving routines honour the global stop")
    res = routines.tick(dry_run=False, by="scene7")
    eq("a tick under `swarm pause` dispatches nothing", len(res["fired"]), 0)
    eq("and says so rather than reporting a quiet zero", res["paused"], True)
    eq("no work item was posted, over 1 due routine", len(items_posted_by("nightly")), 0)
    try:
        store.apply("routine fire", name="nightly", by="scene7")
        bad("firing one routine by name under pause is refused too", "it FIRED")
    except Exception as e:                                                # noqa: BLE001
        ok("firing one routine by name under pause is refused too", str(e)[:120])
    store.apply("resume", by="scene7")
    res = routines.tick(dry_run=False, by="scene7")
    eq("after `swarm resume` the same tick dispatches, over 1 due routine", len(res["fired"]), 1)


def scene_8_the_pointer_that_must_not_be_fabricated() -> None:
    print("\nSCENE 8  a routine dispatching fleet work says WHERE, absolutely, at write time")
    reset()
    try:
        a_routine("fleet-sweep", agent_claimable=True)
        bad("a fleet routine with no workdir is refused when it is WRITTEN", "it was armed")
    except Exception as e:                                                # noqa: BLE001
        ok("a fleet routine with no workdir is refused when it is WRITTEN", str(e)[:140])
    try:
        a_routine("fleet-sweep", agent_claimable=True, workdir="outputs")
        bad("a RELATIVE workdir is refused too", "it was armed")
    except Exception as e:                                                # noqa: BLE001
        ok("a RELATIVE workdir is refused too", str(e)[:140])

    # AND THE DATABASE REFUSES IT INDEPENDENTLY, which is the half that holds if the verb's check
    # is ever edited away. Written as raw SQL on the operator login, going around the verb.
    conn = psycopg2.connect(**session.dsn("operator"))
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO brain.routine (name, title, lane, agent_claimable, workdir, "
                "period_minutes, anchor_at) VALUES ('bypass', 't', 'ops', true, '', 1440, now())")
        bad("the CHECK refuses it independently of the verb", "the row landed")
    except psycopg2.errors.CheckViolation as e:
        ok("the CHECK refuses it independently of the verb",
           "routine_fleet_work_has_an_absolute_workdir: "
           + str(e).strip().splitlines()[0][:110])
    except Exception as e:                                                # noqa: BLE001
        bad("the CHECK refuses it independently of the verb",
            f"refused, but not by the CHECK: {type(e).__name__}: {e}")
    finally:
        conn.close()

    r = a_routine("fleet-sweep", agent_claimable=True, workdir=str(ROOT))
    eq("an ABSOLUTE workdir is accepted, over 3 attempts", r["agent_claimable"], True)
    fired = store.apply("routine fire", name="fleet-sweep", by="scene8")
    with store.read("runtime") as s:
        wi = s.one("SELECT workdir, agent_claimable FROM brain.work_item WHERE id = %s",
                   (fired["work_item_id"],))
    eq("and the posted work carries that workdir", wi["workdir"], str(ROOT))
    eq("and is claimable by the fleet", wi["agent_claimable"], True)


def main() -> int:
    print(f"routines: the object, its scheduler and its kill switch. store={DB}")
    if not provisioned():
        print("\nINCONCLUSIVE: no operator login on this host "
              f"({SECRETS}/brain-postgres-role-operator is absent). `routine add` is gated on "
              "brain.current_human() by migration 35, so this suite cannot construct its subject "
              "and has compared NOTHING. Run store/bin/provision-operator.sh. Reported rather "
              "than skipped: a suite that skips silently reports a green over zero comparisons.")
        return 2

    tmp = ROOT / "engine/tests/.routine-race"
    tmp.mkdir(parents=True, exist_ok=True)
    control = {}
    try:
        scene_1_sequential_control()
        control = scene_2_the_guard_watched_failing(tmp)
        raced = scene_3_the_race(tmp)
        scene_5_no_orphans(raced)
        scene_4_positive_control(tmp)
        scene_6_the_kill_switch()
        scene_7_the_fleet_pause()
        scene_8_the_pointer_that_must_not_be_fabricated()
    finally:
        # THE STORE GOES BACK THE WAY IT WAS FOUND, whatever raised. A suite that drops a
        # constraint and dies leaves the next run measuring its own wreckage, which is exactly
        # what happened on this suite's first run.
        if not index_present():
            reset()
            restore_the_index()
        for f in tmp.glob("*"):
            f.unlink()
        tmp.rmdir()

    print("\nTHE MEASUREMENT, both arms, over the same 8 racers on the same one slot:")
    print(f"  index DROPPED   {control.get('items', '?')} work items, "
          f"{control.get('runs', '?')} occurrence rows, {control.get('orphans', '?')} orphans")
    print(f"  index IN PLACE  {raced['items']} work item, {raced['runs']} occurrence row, "
          f"{raced['refused']} refused, {raced['by_db']} of them BY THE DATABASE")

    total = len(PASS) + len(FAIL)
    if total == 0:                                                          # DENOMINATOR
        print("\nDENOMINATOR: 0 comparisons made. A verdict over an empty set is not a pass.")
        return 2
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed (over 8 scenes, {total} assertions)")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
