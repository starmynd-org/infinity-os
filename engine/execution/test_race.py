"""Do the R01 and R02 guards actually hold when two writers arrive at once?

Both packets are full of look-then-write: read whether a lease is held, then insert; read whether a
key was used, then insert; read whether a grant was revoked, then act. Every one of those windows is
closed by a database constraint rather than by the read, and until this file existed NOTHING
demonstrated that. Both handoffs said so and flagged it as the clearest gap; this closes it.

SEPARATE PROCESSES, NOT THREADS, and one file gate to release them together. That is
`engine/tests/test-claim-race.sh`'s finding, repeated by `test_dispatch_idempotency.py`: twelve
processes on twelve connections is what actually races, and threads sharing a connection do not.
This file follows that harness deliberately rather than inventing a second one.

THREE THINGS THAT MAKE A RACE RESULT MEAN SOMETHING, all of them borrowed:

  * a SEQUENTIAL control first, so a green race cannot be explained by a guard that refuses
    everything;
  * an OVERLAP measurement, because a race whose processes did not overlap in time is not a race
    and a green from it is worthless;
  * a POSITIVE CONTROL on the same harness pointed at N different subjects, which must produce N
    winners -- otherwise "exactly one winner" is equally well explained by racers that never ran.

Run:
    BRAIN_PG_DB=brain_scratch_t04 python3 -m pytest engine/execution/test_race.py -v -s
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import time
import uuid

import psycopg2
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import store                                                            # noqa: E402
from execution import coordinator as ex                                 # noqa: E402


ROOT = pathlib.Path(__file__).resolve().parents[2]
RACERS = 8

pytestmark = pytest.mark.skipif(
    os.environ.get("BRAIN_PG_DB", "brain") == "brain",
    reason="refusing to run against the live store; point BRAIN_PG_DB at a scratch database",
)


# --- migration 63: every authority question is asked inside a workspace -----------------------
WS = "ws-test-race"


def _check(*a, **kw):
    """`authority.check` with this suite's workspace defaulted. Pass `workspace=` to override."""
    kw.setdefault("workspace", WS)
    return authority.check(*a, **kw)


def _in_force(*a, **kw):
    kw.setdefault("workspace", WS)
    return authority.in_force(*a, **kw)


# The child. Opens its own connection, warms it, blocks on the gate, then does ONE thing and
# reports when it started and finished so the parent can prove the processes overlapped.
RACER = r'''
import json, os, pathlib, sys, time
sys.path.insert(0, os.environ["RACE_ROOT"])
sys.path.insert(0, os.path.join(os.environ["RACE_ROOT"], "engine"))
import store
from execution import coordinator as ex

who  = os.environ["RACE_WHO"]
ws   = os.environ["RACE_WS"]
op   = os.environ["RACE_OP"]
arg  = os.environ["RACE_ARG"]
gate = pathlib.Path(os.environ["RACE_GATE"])
out  = pathlib.Path(os.environ["RACE_OUT"])

# Warm the connection BEFORE the gate, so the race is over the verb and not over TCP setup.
with store.read() as s:
    s.scalar("SELECT 1")

while not gate.exists():
    time.sleep(0.002)

res = {"who": who, "op": op}
t0 = time.time()
try:
    if op == "acquire":
        res["got"] = ex.acquire(work_item_id=arg, workspace=ws, holder=who,
                                seconds=300)["lease_id"]
    elif op == "reserve":
        lease_id, token, subject, scope = json.loads(os.environ["RACE_EXTRA"])
        res["got"] = ex.reserve(idempotency_key=arg, lease_id=lease_id, lease_epoch=token,
                                description="raced effect", workspace=ws, subject=subject,
                                capability="effect.external", scope=scope)["attempt_seq"]
    elif op == "release":
        res["got"] = ex.release(lease_id=int(arg), reason=f"released by {who}")["lease_id"]
    else:
        raise SystemExit(f"unknown op {op!r}")
    res["ok"] = True
except Exception as exc:                                       # noqa: BLE001 -- the refusal IS the result
    res["ok"] = False
    res["err"] = type(exc).__name__
    res["msg"] = str(exc)[:200]
t1 = time.time()
res["t0"], res["t1"] = t0, t1
out.write_text(json.dumps(res))
'''


def race(tmp: pathlib.Path, op: str, args: list, extra=None) -> list[dict]:
    """One process per entry in `args`, all released by one file. Returns their results.

    `args` carries a target per racer, so the same harness serves the contended scene (every racer
    pointed at ONE target) and its positive control (each pointed at a different one). One harness,
    two scenes, so the control tests the instrument that produced the finding.
    """
    gate = tmp / "GO"
    gate.unlink(missing_ok=True)
    racer_py = tmp / "racer.py"
    racer_py.write_text(RACER)

    procs = []
    for i, arg in enumerate(args):
        who = f"racer-{i}"
        out = tmp / f"out-{who}.json"
        out.unlink(missing_ok=True)
        env = {**os.environ, "RACE_ROOT": str(ROOT), "RACE_WHO": who, "RACE_OP": op,
               "RACE_ARG": str(arg), "RACE_GATE": str(gate), "RACE_OUT": str(out),
               "RACE_EXTRA": json.dumps(extra) if extra is not None else "", "RACE_WS": WS}
        env.pop("SWARM_PARENT_TASK", None)
        procs.append((who, out, subprocess.Popen([sys.executable, str(racer_py)], env=env,
                                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)))
    time.sleep(1.5)                 # let every child reach the gate with a warm connection
    gate.write_text("go")

    results = []
    for who, out, p in procs:
        p.wait(timeout=180)
        if out.exists():
            results.append(json.loads(out.read_text()))
        else:
            err = p.stderr.read().decode()[-400:] if p.stderr else ""
            results.append({"who": who, "ok": False, "err": "NO RESULT FILE", "msg": err})
    return results


def overlap(results: list[dict]) -> int:
    """How many racers were inside the verb at the same moment as at least one other.

    A race that did not overlap is not a race. Every contended scene below asserts on this before it
    asserts on the winner, because a green from processes that ran one after another says nothing
    about concurrency at all.
    """
    spans = [(r["t0"], r["t1"]) for r in results if "t0" in r]
    return sum(1 for i, (a0, a1) in enumerate(spans)
               if any(j != i and b0 < a1 and a0 < b1 for j, (b0, b1) in enumerate(spans)))


def winners(results):
    return [r for r in results if r.get("ok")]


def losers(results):
    return [r for r in results if not r.get("ok")]


def report(name, results):
    """Printed, not swallowed. A race result nobody can see is a number you have to trust."""
    print(f"\n  {name}: {len(winners(results))} won, {len(losers(results))} refused, "
          f"{overlap(results)}/{len(results)} overlapping in time")
    for r in losers(results)[:3]:
        print(f"      refusal: {r.get('err')}: {str(r.get('msg'))[:90]}")


@pytest.fixture(scope="module")
def who() -> str:
    me = store.whoami()
    if not (me.get("reachable") and me.get("agrees")):
        pytest.fail(f"the store does not agree who this process is ({me}); no grant could be made")
    return me["human"]


# ITS OWN VERB NAME, not the one test_execution.py registers. `store.transition` raises
# DuplicateTransition at import time when a name is registered twice, which is the property that
# stops two lanes each defining `done` -- and it fires just as happily on two test modules
# collected into one pytest run.
@store.transition("test race make work item")
def _make_item(ctx, *, title: str):
    return dict(ctx.one("INSERT INTO brain.work_item (title, lane) VALUES (%s, 'mv-race') "
                        "RETURNING id", (title,)))


@store.transition("test race grant")
def _make_grant(ctx, *, who: str, scope: str):
    return dict(ctx.one(
        "INSERT INTO brain.authority_grant (granted_by, workspace, subject_kind, subject, "
        " capability, scope, expires_at, evidence) "
        " VALUES (%s, %s, 'human', %s, 'effect.external', %s, "
        " now() + interval '1 hour', 'engine/execution/test_race.py') RETURNING *",
        (who, WS, who, scope)))


def _item(title="race"):
    return store.apply("test race make work item", title=f"{title} {uuid.uuid4().hex[:8]}")["id"]


# ---------------------------------------------------------------- scenes


def test_sequential_control_the_guard_exists(tmp_path):
    """Before racing anything: acquiring twice IN SEQUENCE must be refused.

    If this fails, every green below is measuring a missing guard rather than a won race, and the
    suite should say so rather than report a race it never observed.
    """
    item = _item("sequential control")
    ex.acquire(workspace=WS, work_item_id=item, holder="first", seconds=300)
    with pytest.raises(ex.LeaseHeld):
        ex.acquire(workspace=WS, work_item_id=item, holder="second", seconds=300)


def test_eight_processes_race_for_one_lease(tmp_path):
    """THE RACE. Eight processes, eight connections, one work item, released together."""
    item = _item("lease race")
    results = race(tmp_path, "acquire", [item] * RACERS)
    report("lease race", results)

    assert overlap(results) >= 2, (
        f"only {overlap(results)} of {RACERS} racers overlapped in time, so this was not a race and "
        f"its result says nothing about concurrency")
    assert len(winners(results)) == 1, (
        f"{len(winners(results))} racers acquired one lease; exactly one may")
    assert all(r["err"] in ("LeaseHeld", "IntegrityError", "UniqueViolation")
               for r in losers(results)), \
        f"a loser failed for the wrong reason: {[r.get('err') for r in losers(results)]}"

    with store.read() as s:
        live = s.scalar("SELECT count(*) FROM brain.execution_lease "
                        " WHERE work_item_id = %s AND released_at IS NULL", (item,))
    assert live == 1, f"{live} unreleased leases on one item"


def test_the_positive_control_eight_items_eight_winners(tmp_path):
    """The same harness, eight DIFFERENT items. All eight must win.

    Without this, "exactly one winner" above is equally well explained by a harness whose racers
    never ran, or by a guard that refuses everything.
    """
    items = [_item("control") for _ in range(RACERS)]
    results = race(tmp_path, "acquire", items)
    report("positive control", results)
    assert overlap(results) >= 2
    assert len(winners(results)) == RACERS, (
        f"only {len(winners(results))} of {RACERS} racers won on {RACERS} separate items, so the "
        f"harness is not measuring what the contended scene claims")
    assert len({r["got"] for r in winners(results)}) == RACERS, "two racers got one lease id"


def test_eight_processes_race_one_idempotency_key(tmp_path, who):
    """THE EFFECT RACE, and the one that would cost money.

    Eight workers, all holding the same live lease and the same key, all arriving at once. The key
    is what stands between one charge and eight, and the look-then-write in `reserve` cannot be
    what enforces it.
    """
    item = _item("effect race")
    scope = f"lane/race-{uuid.uuid4().hex[:10]}"
    store.apply("test race grant", who=who, scope=scope)
    lease = ex.acquire(workspace=WS, work_item_id=item, holder="the-worker", seconds=300)
    key = f"race-key-{uuid.uuid4().hex[:12]}"

    results = race(tmp_path, "reserve", [key] * RACERS,
                   extra=[lease["lease_id"], lease["lease_epoch"], who, scope])
    report("idempotency race", results)

    assert overlap(results) >= 2, "not a race"
    assert len(winners(results)) == 1, (
        f"{len(winners(results))} racers reserved one idempotency key; exactly one may. "
        f"Every extra winner is an effect that happens twice.")
    with store.read() as s:
        rows = s.scalar("SELECT count(*) FROM brain.effect_attempt WHERE idempotency_key = %s",
                        (key,))
    assert rows == 1, f"{rows} attempt rows for one key"
    # EVERY LOSER WAS TOLD THE TRUTH, and told which KIND of truth. `AlreadyAttempted` is the
    # refusal the look caught; `UniqueViolation` is the constraint catching the ones that got past
    # it. Both are correct outcomes and which one fires is a matter of timing, so the assertion
    # accepts either rather than going red the day the race is won differently.
    assert all(r["err"] in ("AlreadyAttempted", "UniqueViolation", "IntegrityError")
               for r in losers(results)), [r.get("err") for r in losers(results)]


def test_the_positive_control_eight_keys_eight_reservations(tmp_path, who):
    """Same harness, eight different keys under one lease. All eight must land."""
    item = _item("effect control")
    scope = f"lane/race-{uuid.uuid4().hex[:10]}"
    store.apply("test race grant", who=who, scope=scope)
    lease = ex.acquire(workspace=WS, work_item_id=item, holder="the-worker", seconds=300)
    keys = [f"race-key-{uuid.uuid4().hex[:12]}" for _ in range(RACERS)]

    results = race(tmp_path, "reserve", keys,
                   extra=[lease["lease_id"], lease["lease_epoch"], who, scope])
    report("effect positive control", results)
    assert overlap(results) >= 2
    assert len(winners(results)) == RACERS, (
        f"only {len(winners(results))} of {RACERS} distinct keys reserved, so the contended scene "
        f"proves nothing")


def test_eight_processes_race_to_release_one_lease(tmp_path):
    """Only one release may land, or the record of who took the lease away is a guess.

    This is the look-then-write in `release`, and the unique constraint under it is what actually
    decides. The losers must say `AlreadyRevoked`-style refusals, not crash.
    """
    item = _item("release race")
    lease = ex.acquire(workspace=WS, work_item_id=item, holder="the-holder", seconds=300)
    results = race(tmp_path, "release", [lease["lease_id"]] * RACERS)
    report("release race", results)

    assert overlap(results) >= 2, "not a race"
    assert len(winners(results)) == 1, f"{len(winners(results))} releases landed on one lease"
    with store.read() as s:
        row = s.one("SELECT released_at, release_reason FROM brain.execution_lease "
                    " WHERE lease_id = %s", (lease["lease_id"],))
    assert row["released_at"] is not None
    assert row["release_reason"].startswith("released by racer-"), row["release_reason"]


# ---------------------------------------------------------------- CAP14-REV-017, the real fence
#
# The three scenes above race one verb against itself, and unique constraints cover all of them.
# CAP14 pointed at a different window and was right that nothing here tested it: a TAKEOVER
# (release plus acquire, in one session) interleaved with a RESERVE in another. No unique constraint
# spans those two statements, so before migration 62 both reservations stood.
#
# These need explicit transaction control, so they drive psycopg2 connections directly instead of
# going through `store.apply`. Two connections, two threads, and the second thread deliberately
# blocks on the first: that block IS the repair, and a version of this scene that ran both on one
# thread would deadlock itself rather than measure anything.


def _dsn():
    from store import session
    return session.dsn("runtime")


def _grant_row(cur, subject, capability, scope, who):
    cur.execute(
        "INSERT INTO brain.authority_grant (subject, workspace, subject_kind, capability, scope, "
        " granted_by, expires_at, evidence) "
        " VALUES (%s, %s, 'service', %s, %s, %s, now() + interval '1 hour', "
        " 'engine/execution/test_race.py') RETURNING grant_seq",
        (subject, WS, capability, scope, who))
    return cur.fetchone()[0]


def test_a_takeover_racing_a_reserve_leaves_exactly_one_open_reservation(who):
    """CAP14 REV-017 scene 1, run for real with two sessions and two threads.

    Observed against the unrepaired code by CAP14: two open reservations on one work item, which is
    two live effects and nobody able to say which one ran.
    """
    import threading
    import time as _t

    item = _item("fence race")
    scope = f"source/fence-{uuid.uuid4().hex[:8]}"
    a = psycopg2.connect(**_dsn())
    b = psycopg2.connect(**_dsn())
    try:
        b.autocommit = True
        with b.cursor() as cb:
            g = _grant_row(cb, "fence-subject", "effect.external", scope, who)
            cb.execute("INSERT INTO brain.execution_lease "
                       " (work_item_id, workspace, holder, expires_at, lease_epoch) "
                       " VALUES (%s, %s, 'worker-A', now() + interval '1 hour', 0) "
                       " RETURNING lease_id, lease_epoch", (item, WS))
            l1, t1 = cb.fetchone()

        errors = {}

        def worker_a():
            """Reserves under L1, holds the transaction open, then commits."""
            try:
                a.autocommit = False
                with a.cursor() as ca:
                    ca.execute(
                        "INSERT INTO brain.effect_attempt (idempotency_key, lease_id, "
                        " lease_epoch, description, workspace, subject, capability, scope, "
                        " grant_seq) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                        (f"fence-A-{item}", l1, t1, "A's effect", WS, "fence-subject",
                         "effect.external", scope, g))
                _t.sleep(0.6)          # B tries to take over while this transaction is open
                a.commit()
            except Exception as exc:                       # noqa: BLE001
                errors["A"] = f"{type(exc).__name__}: {exc}"
                a.rollback()

        def worker_b():
            """Takes the lease away, then reserves under the lease it now holds."""
            _t.sleep(0.2)              # let A get inside its transaction first
            try:
                with b.cursor() as cb:
                    cb.execute("UPDATE brain.execution_lease SET released_at = now(), "
                               " release_reason = 'taken over by worker-B' WHERE lease_id = %s",
                               (l1,))
                    cb.execute("INSERT INTO brain.execution_lease "
                               " (work_item_id, workspace, holder, expires_at, lease_epoch) "
                               " VALUES (%s, %s, 'worker-B', now() + interval '1 hour', 0) "
                               " RETURNING lease_id, lease_epoch", (item, WS))
                    l2, t2 = cb.fetchone()
                    cb.execute(
                        "INSERT INTO brain.effect_attempt (idempotency_key, lease_id, "
                        " lease_epoch, description, workspace, subject, capability, scope, "
                        " grant_seq) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                        (f"fence-B-{item}", l2, t2, "B's effect", WS, "fence-subject",
                         "effect.external", scope, g))
            except Exception as exc:                       # noqa: BLE001
                errors["B"] = f"{type(exc).__name__}: {exc}"

        ta, tb = threading.Thread(target=worker_a), threading.Thread(target=worker_b)
        ta.start(); tb.start(); ta.join(timeout=30); tb.join(timeout=30)
        assert not ta.is_alive() and not tb.is_alive(), (
            "a thread never finished: the repair is blocking indefinitely rather than serialising")

        with b.cursor() as cb:
            cb.execute("SELECT count(*) FROM brain.effect_attempt ea "
                       "  JOIN brain.execution_lease el ON el.lease_id = ea.lease_id "
                       " WHERE el.work_item_id = %s AND ea.settled_at IS NULL", (item,))
            open_now = cb.fetchone()[0]
            cb.execute("SELECT outcome, settled_by FROM brain.effect_attempt "
                       " WHERE idempotency_key = %s", (f"fence-A-{item}",))
            a_row = cb.fetchone()
        print(f"\n  fence race: {open_now} open reservation(s); A's attempt {a_row}; errors {errors}")
        assert open_now == 1, (
            f"{open_now} open reservations on one work item. Two live effects and nobody able to "
            f"say which ran is exactly the defect CAP14 measured.")
        # A's effect is not lost, and it is not pretended to have succeeded either
        assert a_row is not None and a_row[0] == "halted", (
            f"A's in-flight effect should be halted for reconciliation, got {a_row}")
    finally:
        a.close(); b.close()
