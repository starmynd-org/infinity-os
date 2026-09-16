#!/usr/bin/env python3
"""A parent cycle is an outage, and `set` may not lower a hard flag. One test per path that broke.

Task 0146 (FIX 3), from the D9 adversarial pass
(`outputs/2026-08-16-D9-adversarial/FINDINGS.md`, items 3 and 3b). Every attack below was measured
succeeding against the ported engine on 2026-08-16; each test is the same attack with the verdict
inverted, plus the negative cases that stop the guards being walls around nothing.

THE OUTAGE. `brain.work_item_signals` ORs the two hard flags up the whole parent chain, and
migration 1 wrote that walk with no cycle guard. One cyclic edge therefore did not poison one row,
it poisoned the view: an UNRELATED row timed out, `claim` hung inside libpq while holding its
`FOR UPDATE` lock, and the fleet stopped claiming. Three defences, tested separately because they
fail separately:

  * the verb refuses the edge (`_refuse_cycle`), which is the fix;
  * the database refuses it again (`work_item_parent_acyclic`), for routes no verb owns;
  * the read is BOUNDED and fails closed (`lineage_cycle`), for the route neither can see -- a
    restore runs with triggers disabled -- so a cycle that arrives anyway degrades to a gated row
    instead of an outage.

THE LAUNDER. EF-7: hard flags inherit by OR and may be raised, never lowered. `post` refused that
and `set` did not, so `set external false` followed by `set parent <clean>` walked out in two
individually-compliant hops and every descendant was born clean. Lowering is refused now, and a
re-parent freezes what the task resolves today into its own row, so moving can only ever re-OR.

Run: python3 engine/tests/test_parent_cycle_guard.py   (against the scratch database, never `brain`)
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT))

os.environ.setdefault("BRAIN_PG_DB", os.environ.get("ENGINE_SCRATCH_DB", "brain_scratch"))

# Reconcile this database to `migrations/` before anything below asserts anything. Task 0153.
# `run-all.sh` does this once for the whole run (task 0148); a suite run BY ITSELF did not, and a
# brief that asks a lane to prove one behaviour asks for exactly that. 11 passed / 9 failed against
# a schema three migrations behind is not a red suite, it is a red suite about nothing.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _scratch_preflight import reconcile              # noqa: E402
reconcile(os.environ["BRAIN_PG_DB"])
os.environ.pop("SWARM_PARENT_TASK", None)
os.environ.pop("SWARM_AGENT", None)

import store                                                     # noqa: E402
from swarm_engine import transitions                             # noqa: E402
from swarm_engine.transitions import VerbError                    # noqa: E402

PASS, FAIL = 0, 0
# Unique per run: this suite is re-run against a scratch database it does not truncate, and a
# leftover inbox row in the same lane would be the one `claim` hands back.
LANE = f"pcg{os.getpid()}"
DEEP_LANE = f"pcgdeep{os.getpid()}"


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


def refuses(msg, fn, *, wanted):
    """The call must RAISE, not return a quiet no-op. Returns the exception text."""
    try:
        r = fn()
    except VerbError as e:
        ok(msg) if wanted in str(e) else bad(msg, f"refused, but not for the right reason: {e}")
        return str(e)
    bad(msg, f"NOT refused: the verb returned {r!r}")
    return ""


def post(**kw):
    kw.setdefault("lane", LANE)
    kw.setdefault("posted_by", "T1")
    kw.setdefault("title", "cycle guard fixture")
    kw.setdefault("agent_claimable", True)   # migration 26: these are fleet rows
    kw.setdefault("workdir", "/tmp")         # task 0100: a fleet row names its tree
    return store.apply("post", **kw)["id"]


def flags(tid):
    with store.read() as s:
        return s.one("SELECT external, canon_touching, lineage_cycle "
                     "FROM brain.work_item_signals WHERE id = %s", (tid,))


def own(tid):
    with store.read() as s:
        return s.one("SELECT external, canon_touching, parent FROM brain.work_item WHERE id = %s",
                     (tid,))


def poison(child, parent):
    """Write a cycle the way a restore does: triggers disabled. The verb and the trigger both
    refuse this edge, which is the point -- this is the route NEITHER of them can see."""
    sql = (f"SET session_replication_role = replica; "
           f"UPDATE brain.work_item SET parent = '{parent}' WHERE id = '{child}';")
    _su_psql(sql)


def _su_psql(sql):
    secret = Path.home() / ".brain-postgres-secrets/brain-postgres-bootstrap-superuser"
    return subprocess.run(
        ["docker", "exec", "-i", "-e", f"PGPASSWORD={secret.read_text().strip()}",
         os.environ.get("BRAIN_PG_CONTAINER", "brain-postgres"),
         "psql", "-v", "ON_ERROR_STOP=1", "-h", "127.0.0.1", "-U", "postgres",
         "-d", os.environ["BRAIN_PG_DB"], "-tAc", sql],
        capture_output=True, text=True)


# ================================================================ the cycle, at write time

def test_set_parent_refuses_the_cycle_and_names_it():
    """`set parent` closing a loop is refused, and the refusal says which loop."""
    a = post(title="cycle A", external="true")
    b = post(title="cycle B", parent=a)
    msg = refuses("`set parent` refuses the edge that closes a loop",
                  lambda: store.apply("set", id=a, key="parent", value=b, agent="T1"),
                  wanted="closes the parent cycle")
    truth("and it names the cycle, so the operator does not have to find it",
          f"{a} -> {b} -> {a}" in msg, msg)
    eq("the row is untouched", own(a)["parent"], None)
    refuses("a task may not be its own parent either",
            lambda: store.apply("set", id=a, key="parent", value=a, agent="T1"),
            wanted="its own parent")


def test_the_database_refuses_it_a_second_time():
    """The trigger catches the routes no verb owns: a hand-written UPDATE."""
    a = post(title="trigger A")
    b = post(title="trigger B", parent=a)
    r = _su_psql(f"UPDATE brain.work_item SET parent = '{b}' WHERE id = '{a}'")
    truth("a raw UPDATE closing the loop is refused by the trigger",
          "refusing to make" in r.stderr and "parent cycle" in r.stderr, r.stderr.strip()[:200])
    truth("and the trigger names the cycle too", f"{a} -> {b} -> {a}" in r.stderr,
          r.stderr.strip()[:200])
    eq("the row is untouched", own(a)["parent"], None)


def test_a_nonexistent_parent_is_a_refusal_not_a_foreign_key_violation():
    """It used to reach the table and come back as a bare Postgres error, which reads as a crash."""
    a = post(title="fk")
    refuses("`set parent 9999` is refused in words",
            lambda: store.apply("set", id=a, key="parent", value="9999", agent="T1"),
            wanted="no such parent task")


# ================================================================ the cycle, at read time

def test_a_cycle_behind_the_guard_degrades_instead_of_hanging():
    """The restore case: triggers disabled, so neither the verb nor the trigger ever saw it."""
    x = post(title="poison X")
    y = post(title="poison Y", parent=x)
    bystander = post(title="bystander")
    poison(x, y)
    eq("the cycle is really on disk", own(x)["parent"], y)

    t0 = time.time()
    got = flags(x)
    elapsed = time.time() - t0
    truth(f"the view RETURNS for a row in the cycle ({elapsed:.1f}s, it used to never return)",
          elapsed < 5, f"took {elapsed:.1f}s")
    eq("and it fails closed: external", got["external"], True)
    eq("and it fails closed: canon_touching", got["canon_touching"], True)
    eq("and it says why, rather than looking like an ordinary gate", got["lineage_cycle"], True)

    t0 = time.time()
    clean = flags(bystander)
    truth(f"an UNRELATED row is unaffected ({time.time()-t0:.1f}s) -- the outage was that it was "
          f"not", (clean["external"], clean["lineage_cycle"]) == (False, False), str(clean))

    tid = post(title="claimable", lane=LANE + "c")
    t0 = time.time()
    claimed = store.apply("claim", agent="T-pcg", lanes=[LANE + "c"])
    truth(f"`claim` still works while the poisoned row sits in the table ({time.time()-t0:.1f}s)",
          claimed is not None and claimed["id"] == tid,
          "the fleet stopped claiming for 6m57s on 2026-08-16 because this hung")
    store.apply("release", id=tid, agent="T-pcg")

    refuses("`post --parent` under the poisoned parent refuses in words rather than hanging",
            lambda: store.apply("post", title="child of poison", lane=LANE, posted_by="T1",
                                workdir="/tmp",
                                parent=x),
            wanted="does not resolve")
    _su_psql(f"SET session_replication_role = replica; "
             f"UPDATE brain.work_item SET parent = NULL WHERE id = '{x}'")
    eq("and the repair puts the bystander back to normal", flags(x)["lineage_cycle"], False)


def test_the_two_bounded_walks_agree():
    """`work_item_lineage` and `parent_cycle_path` stop for the same two reasons.

    They are two functions because they answer two questions, so the stop conditions are the
    duplication. This is the test the migration's header promises.
    """
    with store.read() as s:
        cap = s.scalar("SELECT brain.lineage_depth_cap()")
    truth("the depth cap is a real number", isinstance(cap, int) and cap > 1, str(cap))

    # A chain longer than the cap, built in SQL rather than through `post`: past the cap a chain
    # reads as unresolved, and `post --parent` refuses to build on one, which is the fail-closed
    # behaviour tested below. Depth alone is what is being tested here.
    _su_psql(f"""
      DO $$
      DECLARE prev text := NULL; i int;
      BEGIN
        FOR i IN 1..{cap + 3} LOOP
          INSERT INTO brain.work_item (title, lane, state, posted_by, parent)
          VALUES ('deep ' || i, '{DEEP_LANE}', 'inbox', 'T1', prev) RETURNING id INTO prev;
        END LOOP;
      END $$;""")
    with store.read() as s:
        deep = s.query("SELECT id FROM brain.work_item WHERE lane = %s ORDER BY id",
                       (DEEP_LANE,))
    top, prev = deep[0]["id"], deep[-1]["id"]
    with store.read() as s:
        lin = s.one("SELECT * FROM brain.work_item_lineage(%s)", (prev,))
    eq("a chain past the cap is reported truncated, not silently shortened",
       bool(lin["is_truncated"]), True)
    eq("and the view fails closed on it rather than dropping the ancestors it did not read",
       flags(prev)["lineage_cycle"], True)
    eq("a chain within the cap is neither truncated nor cyclic", flags(top)["lineage_cycle"],
       False)

    a = post(title="agree A")
    b = post(title="agree B", parent=a)
    poison(a, b)
    with store.read() as s:
        lin = s.one("SELECT * FROM brain.work_item_lineage(%s)", (a,))
        path = s.scalar("SELECT brain.parent_cycle_path(%s, %s)", (a, b))
    eq("on a real cycle, the walk says cycle and not truncated",
       (bool(lin["is_cycle"]), bool(lin["is_truncated"])), (True, False))
    eq("and the edge test names the same loop", path, [a, b, a])
    _su_psql(f"SET session_replication_role = replica; "
             f"UPDATE brain.work_item SET parent = NULL WHERE id = '{a}'")


# ================================================================ EF-7 through `set`

def test_set_may_not_lower_a_hard_flag():
    """The measured launder: `set external false` used to be accepted with no refusal."""
    p = post(title="external parent", external="true")
    c = post(title="child", parent=p)
    eq("the child resolves external", flags(c)["external"], True)
    refuses("`set external false` on the child is refused",
            lambda: store.apply("set", id=c, key="external", value="false", agent="T1"),
            wanted="refusing to clear external")

    p2 = post(title="clean parent")
    c2 = post(title="born clean", parent=p2)
    store.apply("set", id=p2, key="external", value="true", agent="T1")   # the retroactive raise
    eq("a flag raised on the parent later reaches the child by OR", flags(c2)["external"], True)
    eq("without touching the child's own column", own(c2)["external"], False)
    msg = refuses("and lowering the INHERITED flag is refused too",
                  lambda: store.apply("set", id=c2, key="external", value="false", agent="T1"),
                  wanted="refusing to clear external")
    truth("the refusal says where the flag comes from", f"inherited by OR from {p2}" in msg, msg)

    refuses("`set canon_touching false` is refused on the same rule",
            lambda: store.apply("set", id=p, key="canon_touching", value="true", agent="T1")
            and store.apply("set", id=p, key="canon_touching", value="false", agent="T1"),
            wanted="refusing to clear canon_touching")


def test_raising_a_hard_flag_is_still_allowed():
    """The guard is a direction, not a lock. `set` that RAISES stays an ordinary edit."""
    a = post(title="raise me")
    eq("starts clean", flags(a)["external"], False)
    store.apply("set", id=a, key="external", value="true", agent="T1")
    eq("`set external true` is accepted", flags(a)["external"], True)
    store.apply("set", id=a, key="canon_touching", value="true", agent="T1")
    eq("`set canon_touching true` is accepted", flags(a)["canon_touching"], True)
    b = post(title="already false")
    store.apply("set", id=b, key="external", value="false", agent="T1")
    eq("and writing false to a flag that is already false is not an error", flags(b)["external"],
       False)


def test_reparenting_re_ors_rather_than_resets():
    """The second hop of the launder, closed independently of the first."""
    p = post(title="ext parent", external="true")
    c = post(title="born clean", parent=post(title="clean parent"))
    store.apply("set", id=own(c)["parent"], key="external", value="true", agent="T1")
    eq("the child resolves external and does not carry it",
       (flags(c)["external"], own(c)["external"]), (True, False))

    g = post(title="grandchild", parent=c)
    clean1, clean2 = post(title="clean 1"), post(title="clean 2")

    store.apply("set", id=c, key="parent", value=clean1, agent="T1")
    eq("hop 1: the flag is frozen into the moving task's own column", own(c)["external"], True)
    eq("hop 1: so it still resolves external under a clean parent", flags(c)["external"], True)
    eq("hop 1: and the descendant keeps it", flags(g)["external"], True)

    store.apply("set", id=g, key="parent", value=clean2, agent="T1")
    eq("hop 2: still external, two individually-compliant hops later", flags(g)["external"], True)
    eq("hop 2: which is what D1 tested for `post` and `set` used to defeat",
       store.apply("post", title="great-grandchild", lane=LANE, posted_by="T1",
                   workdir="/tmp",
                   parent=g)["external"], True)

    with store.read() as s:
        notes = s.query("SELECT text FROM brain.thread WHERE work_item_id = %s AND kind = 'note' "
                        "ORDER BY seq", (c,))
    truth("and the freeze is on the thread, not silent",
          any("re-parent re-ORs" in n["text"] for n in notes), str(notes))

    unflagged = post(title="nothing to freeze")
    store.apply("set", id=unflagged, key="parent", value=clean1, agent="T1")
    eq("a re-parent with no flag to carry writes no flag", own(unflagged)["external"], False)
    store.apply("set", id=c, key="parent", value="", agent="T1")
    eq("clearing the parent cannot launder either: the flag is already carried",
       flags(c)["external"], True)


# ================================================================ who did it

def test_set_records_the_caller_and_refuses_to_guess():
    """The thread on 0044 recorded `operator [set] external = False` for an act T2 performed."""
    a = post(title="attribution")
    store.apply("set", id=a, key="priority", value="1", agent="T9")
    with store.read() as s:
        last = s.one("SELECT from_agent FROM brain.thread WHERE work_item_id = %s "
                     "ORDER BY seq DESC LIMIT 1", (a,))
    eq("an explicit agent is what the thread records", last["from_agent"], "T9")

    os.environ["SWARM_AGENT"] = "T7"
    try:
        store.apply("set", id=a, key="priority", value="2")
        with store.read() as s:
            last = s.one("SELECT from_agent FROM brain.thread WHERE work_item_id = %s "
                         "ORDER BY seq DESC LIMIT 1", (a,))
        eq("SWARM_AGENT is used when the caller passes none", last["from_agent"], "T7")
    finally:
        os.environ.pop("SWARM_AGENT", None)

    store.apply("set", id=a, key="priority", value="3")
    with store.read() as s:
        last = s.one("SELECT from_agent FROM brain.thread WHERE work_item_id = %s "
                     "ORDER BY seq DESC LIMIT 1", (a,))
    eq("outside a fleet terminal, an unnamed `set` is still the operator's",
       last["from_agent"], "operator")

    os.environ["SWARM_PARENT_TASK"] = "0146"
    try:
        refuses("but INSIDE a fleet terminal, an unnamed `set` is refused rather than filed "
                "against the operator",
                lambda: store.apply("set", id=a, key="priority", value="4"),
                wanted="needs an agent here")
    finally:
        os.environ.pop("SWARM_PARENT_TASK", None)


def test_the_timeout_is_set_on_every_role():
    """There was none anywhere: `SHOW statement_timeout` was 0 for all four roles."""
    from store.session import STATEMENT_TIMEOUT
    for role in ("runtime", "producer", "subscriber", "owner"):
        with store.read(role) as s:
            got = s.scalar("SHOW statement_timeout")
        want = STATEMENT_TIMEOUT[role].replace("60s", "1min")
        eq(f"brain_{role} is bounded at {want}", got, want)
    t0 = time.time()
    try:
        with store.read("runtime") as s:
            s.query("SELECT pg_sleep(30)")
        bad("a 30s statement on the runtime role was NOT cancelled")
    except Exception as e:                                        # noqa: BLE001
        truth(f"and it bites: a 30s statement is cancelled after {time.time()-t0:.0f}s",
              "statement timeout" in str(e), str(e).splitlines()[0])


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print("test_parent_cycle_guard.py  --  a cycle is bounded, and `set` may not lower a flag")
    print(f"  store: {os.environ['BRAIN_PG_DB']} (scratch)\n")
    for t in tests:
        print(f"{t.__name__.replace('test_', '')}: "
              f"{t.__doc__.splitlines()[0] if t.__doc__ else ''}")
        try:
            t()
        except Exception:                                         # noqa: BLE001
            import traceback
            bad(f"{t.__name__} raised", traceback.format_exc().strip().splitlines()[-1])
        print()
    print(f"{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
