#!/usr/bin/env python3
"""THE OPERATOR'S STOPWATCH, AND THE FOUR WAYS A LEDGER LIKE THIS PRODUCES A CONFIDENT LIE.

Task 0279. This system measures agent cost to four significant figures and measured the
operator's time not at all, while the whole program exists to buy back his review hours. Two
numbers were assertions until `brain.time_entry` had rows: the ETA `queue depth` divides depth by,
and `Decide`'s two-minute ceiling, which nobody had ever put a stopwatch on.

A measurement table is easy to build and easy to make dishonest, and the four ways are each a
scene below:

  1. AN EDITED RECORD.        A stopped interval that can be rewritten has a provenance nobody can
                              reconstruct. Refused for UPDATE and DELETE at the table, so a psql
                              prompt is refused too, and corrected by SUPERSEDING -- both rows
                              survive and the correction says why.
  2. AN UNBOUNDED ENTRY.      One closed laptop and the mean is wrong by two orders of magnitude.
                              Past four hours the stop is FORCED to `abandoned` and leaves every
                              mean. Not clamped to the cap: a clamp invents a measurement.
  3. OVERLAPPING INTERVALS.   Two timers at once let measured minutes exceed the wall-clock they
                              are measured from. Refused by the verb in words and by a partial
                              unique index at the table.
  4. AN AVERAGE THAT IMPLIES  A timer is never required, so the timed set is a SUBSET. Every
     TOTALITY.                figure reports "measured over N of M" and a tier with no entry
                              reports null, never another tier's number.

Every duration assertion here is computed BY HAND against a known interval rather than by asking
the code whether it agrees with itself.

Run:  python3 queue/tests/test_time_ledger.py
      (against the queue scratch database, never `brain`)
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "queue"))
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT))

os.environ.setdefault("BRAIN_PG_DB", os.environ.get("QUEUE_SCRATCH_DB", "brain_queue_scratch"))
os.environ.pop("SWARM_PARENT_TASK", None)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _scratch_preflight import reconcile                          # noqa: E402
reconcile(os.environ["BRAIN_PG_DB"])

import psycopg2                                                   # noqa: E402
import store                                                      # noqa: E402
from store import session                                         # noqa: E402
from human_queue import reads, tiers                              # noqa: E402
from human_queue import time_ledger                               # noqa: E402,F401
from human_queue import transitions as queue_transitions          # noqa: E402,F401
from swarm_engine import transitions as engine                    # noqa: E402,F401

SCRATCH = str(ROOT / "queue/bin/queue-scratch-db.sh")
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


def as_runtime(sql: str, params=None):
    """One statement on the real `brain_runtime` login -- the credential EVERY agent process
    already holds in order to call `store.apply` at all.

    No store wrapper, on purpose: the append-only rule is the database's, so the test has to be
    able to reach past every guard written in Python. This is migration 22's own attack shape,
    which found four rowcount=1 forgeries on an unguarded column.
    """
    conn = psycopg2.connect(**session.dsn("runtime"))
    try:
        conn.set_session(readonly=False, autocommit=False)
        with conn.cursor() as cur:
            cur.execute(sql, params)
            out = cur.fetchall() if cur.description else []
            n = cur.rowcount
        conn.commit()
        return out, n
    finally:
        conn.close()


def su(sql: str) -> str:
    """Superuser psql, for TEST SETUP ONLY -- never to prove a rule.

    It exists because an entry that has been running for five hours cannot be produced by waiting.
    Inserting one BORN RUNNING with a past `started_at` is a shape the trigger permits by design,
    so this manufactures the clock, not the verdict: the stop that follows goes through the verb.
    """
    r = subprocess.run([SCRATCH, "psql", "-tAq", "-c", sql], capture_output=True, text=True)
    if r.returncode != 0:
        # NOT check=True. `CalledProcessError` stringifies to the argv and drops stderr, so a
        # `refused()` around it would assert on a message that never contains the database's
        # words -- the suite would go green on ANY failure, including the trigger having been
        # dropped and the statement failing for some unrelated reason.
        raise RuntimeError(r.stderr.strip() or f"psql exited {r.returncode}")
    return r.stdout.strip()


def reset():
    subprocess.run([SCRATCH, "psql", "-q", "-c",
                    "TRUNCATE brain.time_entry, brain.queue_item, brain.queue_defer, "
                    "brain.queue_bump, brain.queue_calibration, brain.queue_default_event, "
                    "brain.recommendation, brain.thread, brain.question, brain.agent, "
                    "brain.work_item CASCADE; "
                    "SELECT setval('brain.item_id_seq', 1, false); "
                    "SELECT setval('brain.time_entry_id_seq', 1, false);"],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def entry(eid: int) -> dict:
    with store.read("runtime") as s:
        return s.one("SELECT * FROM brain.time_entry WHERE id = %s", (eid,)) or {}


def a_decide_item(title="Approve the renewal quote") -> str:
    """A work item the tier machinery puts in Decide, so `tier_at_start` has something to snapshot.

    Decide needs a recommended option, a prepared context or template, `reversibility=high`, and
    an item_class that is not one of the never-decide ones. The `queue_open` arm for a finished
    task defaults `item_class` to `review`, which is never Decide, so the overlay has to say
    `approval` explicitly -- exactly what a producing agent does through `queue classify`.
    """
    r = store.apply("post", title=title, lane="queue", posted_by="T5",
                    signals={"reversibility": "high"})
    tid = r["id"]
    store.apply("done", id=tid, agent="T5", summary="done, needs your nod")
    store.apply("queue classify", source_type="work_item", source_id=tid,
                item_class="approval", template_id="playbook-renewal",
                prepared_context_link="outputs/quote.md", recommended_option="approve it")
    return tid


# --------------------------------------------------------------------------- scene 0: it works

def test_start_stop_and_status_end_to_end():
    """The three verbs, against a real store, with a duration checked against the clock."""
    reset()
    tid = a_decide_item()

    st = reads.time_status(who="operator")
    check("status on an empty ledger says no timer, and does not invent a zero",
          st["running"] is None and st["today"]["n"] == 0
          and st["today"]["mean_minutes"] is None, str(st["today"]))

    started = store.apply("queue time start", source_type="work_item", source_id=tid,
                          who="operator", note="reading the quote")
    check("start returns a running entry on the item", started["source_id"] == tid)
    check("THE TIER IS SNAPSHOTTED AT START, not read live later",
          started["tier_at_start"] == "decide", str(started))
    check("the cap comes from the database, not a Python constant",
          started["cap_seconds"] == 4 * 3600, str(started["cap_seconds"]))

    st = reads.time_status(who="operator")
    check("status shows it running, on the right item",
          st["running"] and st["running"]["id"] == started["id"]
          and st["running"]["source_id"] == tid)
    check("a running entry is not over the cap and carries no warning",
          st["running"]["over_cap"] is False and st["running"]["warning"] is None)

    row = entry(started["id"])
    check("a running entry has NO duration, which is the honest answer",
          row["seconds"] is None and row["stopped_at"] is None and row["ended_how"] is None)

    stopped = store.apply("queue time stop", who="operator", note="approved")
    check("stop returns the measured interval", stopped["measured"] is True
          and stopped["ended_how"] == "stopped", str(stopped))
    # HAND-CHECKED against the wall clock rather than against the code's own arithmetic.
    row = entry(started["id"])
    wall = (row["stopped_at"] - row["started_at"]).total_seconds()
    check("`seconds` is the stored interval, and it agrees with the two timestamps",
          abs(float(row["seconds"]) - wall) < 1e-6, f"{row['seconds']} vs {wall}")
    check("and it agrees with what the verb reported",
          abs(stopped["seconds"] - wall) < 1e-6, f"{stopped['seconds']} vs {wall}")
    check("the tier snapshot survives the stop", row["tier_at_start"] == "decide")

    st = reads.time_status(who="operator")
    check("status now shows nothing running and one measured entry",
          st["running"] is None and st["today"]["n"] == 1, str(st["today"]))

    with store.read("runtime") as s:
        thread = s.query("SELECT kind, text FROM brain.thread WHERE work_item_id = %s "
                         "AND text LIKE 'time entry%%' ORDER BY ts", (tid,))
    check("both the start and the stop are on the append-only thread",
          len(thread) == 2 and "started" in thread[0]["text"]
          and "stopped" in thread[1]["text"], str(thread))


# --------------------------------------------------------------------------- scene 1: append-only

def test_a_stopped_entry_is_never_edited():
    """THE DEFINITION-OF-DONE REFUSAL. Four routes, all as `brain_runtime`, all refused.

    Migration 22 found four rowcount=1 forgeries against an unguarded column using exactly this
    login. The same four shapes are run here against `brain.time_entry` BEFORE anyone reports
    that it is append-only.
    """
    reset()
    tid = a_decide_item()
    e = store.apply("queue time start", source_type="work_item", source_id=tid)
    store.apply("queue time stop")
    eid, before = e["id"], entry(e["id"])

    msg = refused(as_runtime,
                  "UPDATE brain.time_entry SET stopped_at = stopped_at + interval '30 minutes' "
                  "WHERE id = %s", (eid,))
    check("route 1: brain_runtime CANNOT stretch a stopped interval", bool(msg))
    check("and the refusal names the row and both its ends",
          "refusing to rewrite stopped time entry" in msg and str(eid) in msg, msg)
    check("it says how to correct it instead of just saying no",
          "corrects" in msg and "append-only" in msg, msg)

    msg = refused(as_runtime,
                  "UPDATE brain.time_entry SET started_at = started_at - interval '1 hour' "
                  "WHERE id = %s", (eid,))
    check("route 2: nor move the start backwards", bool(msg))

    msg = refused(as_runtime,
                  "UPDATE brain.time_entry SET ended_how = 'stopped' WHERE id = %s "
                  "AND ended_how = 'stopped'", (eid,))
    check("route 3: not even a no-op rewrite of a stopped row -- there is no cheap second door",
          bool(msg), msg)

    # ROUTE 4 IS LOCKED TWICE AND THE OUTER LOCK FIRES FIRST, which is worth pinning rather than
    # papering over: `brain_runtime` holds no DELETE grant on this table at all (migration 2's
    # standing posture, DELETE to nobody), so it is refused by privilege before the trigger is
    # ever reached. The trigger is the lock that still holds for a login that DOES have the
    # grant -- the superuser at a psql prompt -- and both are asserted, because a suite that only
    # checked the grant would go green on a schema whose trigger had been dropped.
    msg = refused(as_runtime, "DELETE FROM brain.time_entry WHERE id = %s", (eid,))
    check("route 4: brain_runtime cannot delete -- it holds no DELETE on this table",
          "permission denied" in msg, msg)
    msg = refused(su, f"DELETE FROM brain.time_entry WHERE id = {eid}")
    check("and the SUPERUSER, who does hold it, is refused by the trigger", bool(msg))
    check("the delete refusal says a wrong entry is superseded, not removed",
          "superseded, not removed" in msg, msg)

    after = entry(eid)
    check("after all four attacks the row is byte-for-byte what it was",
          (after["started_at"], after["stopped_at"], after["seconds"], after["ended_how"])
          == (before["started_at"], before["stopped_at"], before["seconds"],
              before["ended_how"]), f"{before} -> {after}")

    msg = refused(as_runtime,
                  "INSERT INTO brain.time_entry (source_type, source_id, who, stopped_at, "
                  "ended_how) VALUES ('work_item', %s, 'operator', now(), 'stopped')", (tid,))
    check("an entry BORN STOPPED is refused: nobody ran a stopwatch on it", bool(msg))
    check("and the refusal explains why an untimed interval is worse than a missing one",
          "indistinguishable in the mean" in msg, msg)


def test_a_correction_supersedes_and_both_rows_survive():
    reset()
    tid = a_decide_item()
    e = store.apply("queue time start", source_type="work_item", source_id=tid)
    store.apply("queue time stop")
    eid = e["id"]

    now = datetime.now(timezone.utc)
    c = store.apply("queue time correct", entry_id=eid,
                    started_at=now - timedelta(minutes=7), stopped_at=now,
                    reason="I started it four minutes after I actually opened the quote")
    check("the correction is a NEW row, not an edit", c["id"] != eid)
    check("it records 7 minutes, hand-computed", abs(c["minutes"] - 7.0) < 0.01, str(c))

    check("the original row is still there, unchanged", entry(eid).get("stopped_at") is not None)
    with store.read("runtime") as s:
        eff = s.query("SELECT id FROM brain.time_entry_effective WHERE source_id = %s", (tid,))
        ann = s.query("SELECT id, superseded FROM brain.time_entry_annotated "
                      "WHERE source_id = %s ORDER BY id", (tid,))
    check("the MEASUREMENT reads only the correction",
          [r["id"] for r in eff] == [c["id"]], str(eff))
    check("an AUDITOR still sees both, with the original marked superseded",
          len(ann) == 2 and ann[0]["superseded"] is True and ann[1]["superseded"] is False,
          str(ann))

    msg = refused(store.apply, "queue time correct", entry_id=eid,
                  started_at=now - timedelta(minutes=3), stopped_at=now, reason="again")
    check("a SECOND correction of the same row is refused: it would have two successors",
          bool(msg), msg)

    msg = refused(store.apply, "queue time correct", entry_id=c["id"],
                  started_at=now - timedelta(minutes=3), stopped_at=now, reason="")
    check("a correction with no reason is refused by the verb", bool(msg))
    check("and the refusal says why an unlabelled correction is useless",
          "explain a disagreement" in msg, msg)

    other = a_decide_item("A different item entirely")
    msg = refused(as_runtime,
                  "INSERT INTO brain.time_entry (source_type, source_id, who, started_at, "
                  "stopped_at, ended_how, corrects, correction_reason) VALUES ('work_item', %s, "
                  "'operator', now() - interval '5 min', now(), 'stopped', %s, 'moving it')",
                  (other, c["id"]))
    check("a correction may not move the item: that would move minutes between two tiers",
          bool(msg) and "same item" in msg, msg)

    running = store.apply("queue time start", source_type="work_item", source_id=other)
    msg = refused(store.apply, "queue time correct", entry_id=running["id"],
                  started_at=now - timedelta(minutes=1), stopped_at=now, reason="early")
    check("correcting a RUNNING entry is refused: it is not wrong yet, it is unfinished",
          bool(msg) and "still running" in msg, msg)
    store.apply("queue time stop")


# --------------------------------------------------------------------------- scene 2: the cap

def test_a_forgotten_timer_is_abandoned_and_leaves_every_mean():
    """POLICY 1. Five hours is a closed laptop, not a measurement.

    The interval is manufactured by inserting an entry BORN RUNNING with a past `started_at` --
    a shape the trigger permits, because that is what a timer started five hours ago looks like.
    The STOP goes through the verb, so what is under test is the real path.
    """
    reset()
    tid = a_decide_item()
    eid = int(su("INSERT INTO brain.time_entry (source_type, source_id, who, started_at, "
                 f"tier_at_start) VALUES ('work_item', '{tid}', 'operator', "
                 "now() - interval '5 hours', 'decide') RETURNING id"))

    st = reads.time_status(who="operator")
    check("status flags a running timer that is already past the cap",
          st["running"]["over_cap"] is True and st["running"]["warning"], str(st["running"]))
    check("and it warns BEFORE the stop, while there is still time to correct it into truth",
          "WILL be recorded as abandoned" in st["running"]["warning"])

    out = store.apply("queue time stop", who="operator")
    check("the stop LANDS -- a refusal would strand the row forever", entry(eid)["stopped_at"])
    check("but it is recorded as abandoned, not stopped", out["ended_how"] == "abandoned")
    check("the verb reports the override rather than the write it attempted",
          out["capped"] is True and out["measured"] is False, str(out))
    row = entry(eid)
    check("THE DURATION IS NOT CLAMPED: the ledger still says five hours",
          abs(float(row["seconds"]) - 5 * 3600) < 60, str(row["seconds"]))

    with store.read("runtime") as s:
        eff = s.query("SELECT id FROM brain.time_entry_effective")
    check("and it is out of the row set every mean is computed over", eff == [], str(eff))

    d = reads.depth_and_clearance(days=7)
    check("the clearance figure counts it as abandoned and not as measured",
          d["stopwatch"]["entries"]["abandoned"] == 1
          and d["stopwatch"]["entries"]["measured"] == 0, str(d["stopwatch"]["entries"]))
    check("five hours did not move a single mean",
          d["stopwatch"]["overall"]["mean_minutes"] is None, str(d["stopwatch"]["overall"]))

    # And the same rule on a correction: a six-hour claim is no more a stopwatch reading.
    now = datetime.now(timezone.utc)
    c = store.apply("queue time correct", entry_id=eid, started_at=now - timedelta(hours=6),
                    stopped_at=now, reason="I think I was on it all afternoon")
    check("a CORRECTION over the cap is capped too, and says so",
          c["capped"] is True and entry(c["id"])["ended_how"] == "abandoned", str(c))

    # A four-hour-minus-a-minute entry is under the cap and IS measured, so the boundary is a
    # boundary rather than a mood.
    reset()
    tid = a_decide_item()
    eid = int(su("INSERT INTO brain.time_entry (source_type, source_id, who, started_at, "
                 f"tier_at_start) VALUES ('work_item', '{tid}', 'operator', "
                 "now() - interval '3 hours 59 minutes', 'decide') RETURNING id"))
    out = store.apply("queue time stop", who="operator")
    check("3h59 is under the cap and is measured",
          out["ended_how"] == "stopped" and out["measured"] is True, str(out))

    reset()
    tid = a_decide_item()
    store.apply("queue time start", source_type="work_item", source_id=tid)
    out = store.apply("queue time stop", abandon=True, note="I walked away from this")
    check("and the operator may declare an abandon himself, with the same consequence",
          out["ended_how"] == "abandoned" and out["measured"] is False and out["capped"] is False,
          str(out))


# --------------------------------------------------------------------------- scene 3: one timer

def test_two_timers_at_once_are_refused_twice():
    """POLICY 2. Refused by the verb in words, and by the table against a psql prompt."""
    reset()
    a, b = a_decide_item("first thing"), a_decide_item("second thing")
    store.apply("queue time start", source_type="work_item", source_id=a)

    msg = refused(store.apply, "queue time start", source_type="work_item", source_id=b)
    check("the verb refuses the second timer", bool(msg))
    check("and NAMES the one already running, with how long it has been going",
          a in msg and "minutes so far" in msg, msg)
    check("it gives the arithmetic reason, not just the doctrine",
          "exceed the wall-clock" in msg, msg)

    msg, _ = "", None
    msg = refused(as_runtime,
                  "INSERT INTO brain.time_entry (source_type, source_id, who) "
                  "VALUES ('work_item', %s, 'operator')", (b,))
    check("and brain_runtime cannot open a second one behind the verb's back", bool(msg))
    check("the table's refusal is the unique index, so a psql prompt hits it too",
          "time_entry_one_running_per_person" in msg, msg)

    with store.read("runtime") as s:
        n = s.scalar("SELECT count(*) FROM brain.time_entry WHERE stopped_at IS NULL")
    check("exactly one timer is running after both attempts", n == 1, str(n))

    # PER PERSON, not globally: a second human is a thing that will happen, and the schema admits
    # them without a migration.
    store.apply("queue time start", source_type="work_item", source_id=b, who="a-collaborator")
    with store.read("runtime") as s:
        n = s.scalar("SELECT count(*) FROM brain.time_entry WHERE stopped_at IS NULL")
    check("the rule is one timer PER PERSON, so a second human is not blocked by the first",
          n == 2, str(n))
    store.apply("queue time stop", who="a-collaborator")
    store.apply("queue time stop", who="operator")


def test_an_agent_may_not_book_its_own_minutes_as_human_time():
    reset()
    tid = a_decide_item()
    # `brain.agent` is written by `claim` and `heartbeat` and by nothing else, so every name the
    # fleet has taken work under is in it and the operator, who claims nothing, is not. Same
    # predicate `recommend accept` and migration 22 use; registering T5 here is what makes the
    # refusal below a test of the gate rather than of an empty table.
    store.apply("heartbeat", agent="T5", status="working")
    msg = refused(store.apply, "queue time start", source_type="work_item", source_id=tid,
                  who="T5")
    check("a registered agent name is refused as the owner of a time entry", bool(msg))
    check("and the refusal says agent cost is already measured elsewhere",
          "budget" in msg and "human" in msg.lower(), msg)
    msg = refused(store.apply, "queue time start", source_type="work_item", source_id=tid,
                  who="  ")
    check("an empty name is refused: whose minutes are these?", bool(msg))
    with store.read("runtime") as s:
        check("neither attempt left a row",
              s.scalar("SELECT count(*) FROM brain.time_entry") == 0)


def test_a_timer_needs_a_real_item():
    reset()
    msg = refused(store.apply, "queue time start", source_type="work_item", source_id="9999")
    check("a timer on an item that does not exist is refused", bool(msg) and "9999" in msg, msg)
    msg = refused(store.apply, "queue time stop")
    check("stopping when nothing is running is refused, and says what to run instead",
          bool(msg) and "time status" in msg, msg)


# --------------------------------------------- scene 4: coverage, and the absence of a fallback

def test_the_clearance_figure_reports_its_own_coverage():
    """POLICY 3, and the whole point of the task.

    Three items cleared, ONE of them timed. The figure must say "1 of 3" rather than reporting a
    mean over one item as though it had watched all three.
    """
    reset()
    a, b, c = (a_decide_item("timed one"), a_decide_item("untimed one"),
               a_decide_item("untimed two"))

    now = datetime.now(timezone.utc)
    eid = int(su("INSERT INTO brain.time_entry (source_type, source_id, who, started_at, "
                 f"tier_at_start) VALUES ('work_item', '{a}', 'operator', "
                 "now() - interval '6 minutes', 'decide') RETURNING id"))
    store.apply("queue time stop", who="operator")
    check("the setup produced one 6-minute measured entry",
          abs(float(entry(eid)["seconds"]) - 360) < 5, str(entry(eid)["seconds"]))

    for tid in (a, b, c):
        store.apply("accept work", id=tid, by="operator", as_operator=True)

    d = reads.depth_and_clearance(days=7)
    sw = d["stopwatch"]
    check("all three are counted as cleared, timer or no timer", d["cleared"] == 3, str(d))
    check("COVERAGE SAYS 1 OF 3 rather than implying it saw everything",
          sw["coverage"]["measured"] == 1 and sw["coverage"]["cleared"] == 3, str(sw["coverage"]))
    check("and the coverage rate is stated as a number too",
          abs(sw["coverage"]["rate"] - 1 / 3) < 0.01, str(sw["coverage"]))
    check("the sentence a human reads names both halves",
          "measured over 1 of 3" in sw["coverage"]["text"], sw["coverage"]["text"])
    check("the mean is over the ONE item that was timed, hand-checked at 6 minutes",
          sw["overall"]["n"] == 1 and abs(sw["overall"]["median_minutes"] - 6.0) < 0.1,
          str(sw["overall"]))
    check("the basis line says what the number is made of",
          "1 timed item" in sw["basis"], sw["basis"])


def test_an_unmeasured_tier_is_null_and_never_another_tiers_number():
    """No fallback anywhere in this function. A gap is reported as a gap."""
    reset()
    timed = a_decide_item("a decide item that gets timed")
    # An item with nothing prepared is Shape, and nothing times it.
    shaped = store.apply("post", title="rethink the whole lane", lane="queue", posted_by="T5")
    store.apply("done", id=shaped["id"], agent="T5", summary="over to you")

    su("INSERT INTO brain.time_entry (source_type, source_id, who, started_at, tier_at_start) "
       f"VALUES ('work_item', '{timed}', 'operator', now() - interval '4 minutes', 'decide')")
    store.apply("queue time stop", who="operator")

    d = reads.depth_and_clearance(days=7)
    sw = d["stopwatch"]
    check("decide has a measured hours ETA", sw["eta_hours"]["decide"] is not None,
          str(sw["eta_hours"]))
    check("shape has NO measurement, so its ETA is null and not a borrowed mean",
          sw["eta_hours"]["shape"] is None, str(sw["eta_hours"]))
    check("judge likewise", sw["eta_hours"]["judge"] is None, str(sw["eta_hours"]))
    unmeasured = {u["tier"] for u in sw["unmeasured_tiers"]}
    check("the unmeasured tiers are NAMED, with the depth they are hiding",
          unmeasured == {"judge", "shape"}
          and all("depth" in u for u in sw["unmeasured_tiers"]), str(sw["unmeasured_tiers"]))
    check("the total is labelled as covering only the tiers that have a measurement",
          "measured_tiers_only" in str(list(sw)), str(list(sw)))
    check("no unmeasured tier's hours quietly equal the measured tier's",
          sw["eta_hours"]["decide"] != sw["eta_hours"]["shape"])

    # Hand-computed: depth(decide) items x 4 minutes / 60.
    depth_decide = d["depth"]["decide"]
    expect = round(depth_decide * sw["per_item"]["decide"]["median_minutes"] / 60.0, 2)
    check("the hours figure IS depth x median minutes, computed by hand here",
          abs(sw["eta_hours"]["decide"] - expect) < 1e-9,
          f"{sw['eta_hours']['decide']} vs {expect}")


def test_an_empty_ledger_says_nothing_rather_than_zero():
    reset()
    a_decide_item()
    d = reads.depth_and_clearance(days=7)
    sw = d["stopwatch"]
    check("with no entries at all, every hours figure is null",
          all(v is None for v in sw["eta_hours"].values())
          and sw["eta_hours_measured_tiers_only"] is None, str(sw["eta_hours"]))
    check("and the basis says the ceilings and the backlog cost are still ASSERTIONS",
          "assertions" in sw["basis"], sw["basis"])
    check("the item-throughput half is untouched and still reports its own basis",
          "item throughput" in d["eta_days_basis"], d["eta_days_basis"])


# --------------------------------------------------------------------------- scene 5: ceilings

def test_the_two_minute_decide_ceiling_stops_being_an_assertion():
    reset()
    check("only Decide has a claimed ceiling; the other two are unclaimed, not invented",
          tiers.TIER_CEILING_SECONDS == {"decide": 120, "judge": None, "shape": None},
          str(tiers.TIER_CEILING_SECONDS))

    c = reads.tier_ceilings(days=30)
    check("with no entries, Decide reports UNMEASURED and not a clean zero-breach rate",
          "UNMEASURED" in c["tiers"]["decide"]["verdict"]
          and c["tiers"]["decide"].get("over_ceiling") is None,
          str(c["tiers"]["decide"]))
    check("and judge/shape say plainly that nobody has claimed a number for them",
          "no ceiling has been claimed" in c["tiers"]["judge"]["verdict"])

    # Three Decide items: two inside the claimed ceiling, one nine-minute ambush. THREE and not
    # two, because with an even n the median is the mean of the middle pair and the two statistics
    # coincide -- a test that "the median resists an outlier" needs an n where they can differ.
    for title, secs in (("a real two-minute item", 90), ("another one", 100), ("an ambush", 540)):
        tid = a_decide_item(title)
        su("INSERT INTO brain.time_entry (source_type, source_id, who, started_at, "
           f"tier_at_start) VALUES ('work_item', '{tid}', 'operator', "
           f"now() - interval '{secs} seconds', 'decide')")
        store.apply("queue time stop", who="operator")

    c = reads.tier_ceilings(days=30)
    d = c["tiers"]["decide"]
    check("three timed Decide items are measured", d["n"] == 3, str(d))
    check("ONE of them breaches the two-minute ceiling, counted", d["over_ceiling"] == 1, str(d))
    check("the breach RATE is stated too, so one ambush in three is legible",
          abs(d["over_ceiling_rate"] - 1 / 3) < 0.01, str(d["over_ceiling_rate"]))
    check("the verdict says what the stopwatch says, not what the lane claims",
          "stopwatch readings" in d["verdict"], d["verdict"])
    # Hand-computed over 90s, 100s, 540s: median = 100s = 1.67min, mean = 243.3s = 4.06min.
    check("median is hand-computed at 1.67 minutes", abs(d["median_minutes"] - 1.67) < 0.1,
          str(d["median_minutes"]))
    check("mean is hand-computed at 4.06 minutes", abs(d["mean_minutes"] - 4.06) < 0.1,
          str(d["mean_minutes"]))
    check("SO THE TWO DISAGREE: the mean calls Decide a four-minute lane, the median a "
          "ninety-second one with a bug in it. Both are reported.",
          d["mean_minutes"] > 2 * d["median_minutes"], str(d))


def test_the_tier_snapshot_survives_a_demote():
    """The reason `tier_at_start` is a column and not a join.

    `queue demote` moves the tier of exactly the items whose duration is most interesting. If the
    measurement read the tier live, a Decide item that took nine minutes and was demoted BECAUSE
    it took nine minutes would be counted against Judge, and the Decide ceiling would measure
    clean forever.
    """
    reset()
    tid = a_decide_item()
    su("INSERT INTO brain.time_entry (source_type, source_id, who, started_at, tier_at_start) "
       f"VALUES ('work_item', '{tid}', 'operator', now() - interval '9 minutes', 'decide')")
    store.apply("queue time stop", who="operator")

    r = store.apply("queue demote", source_type="work_item", source_id=tid, by="operator")
    check("the item is demoted out of Decide", r["was"] == "decide" and r["tier"] == "judge")

    c = reads.tier_ceilings(days=30)
    check("the nine minutes are STILL counted against Decide, where they were spent",
          c["tiers"]["decide"]["n"] == 1 and c["tiers"]["judge"]["n"] == 0,
          str({k: v["n"] for k, v in c["tiers"].items()}))
    check("and they still show as a breach of the two-minute claim",
          c["tiers"]["decide"]["over_ceiling"] == 1, str(c["tiers"]["decide"]))

    with store.read("runtime") as s:
        miss = s.one("SELECT * FROM brain.queue_calibration WHERE source_id = %s", (tid,))
    check("the calibration miss the demote wrote now has a duration standing behind it",
          miss and miss["declared"] == "decide" and miss["observed"] == "judge", str(miss))


def test_several_sittings_on_one_item_are_one_observation():
    """Minutes PER ITEM, not per sitting. Otherwise discipline about pausing looks like speed."""
    reset()
    tid = a_decide_item()
    for secs in (120, 60, 180):
        su("INSERT INTO brain.time_entry (source_type, source_id, who, started_at, "
           f"tier_at_start) VALUES ('work_item', '{tid}', 'operator', "
           f"now() - interval '{secs} seconds', 'decide')")
        store.apply("queue time stop", who="operator")

    c = reads.tier_ceilings(days=30)
    d = c["tiers"]["decide"]
    check("three sittings on one item are ONE observation", d["n"] == 1, str(d))
    # Hand-computed: 120 + 60 + 180 = 360 seconds = 6 minutes.
    check("and the observation is their SUM, hand-computed at 6 minutes",
          abs(d["median_minutes"] - 6.0) < 0.1, str(d["median_minutes"]))
    check("counting sittings would have reported 2 minutes; it does not",
          abs(d["median_minutes"] - 2.0) > 1.0, str(d["median_minutes"]))


# --------------------------------------------------------------------------- the claim

def test_the_claim():
    """The one sentence this suite exists to make checkable."""
    reset()
    tid = a_decide_item()
    su("INSERT INTO brain.time_entry (source_type, source_id, who, started_at, tier_at_start) "
       f"VALUES ('work_item', '{tid}', 'operator', now() - interval '9 hours', 'decide')")
    store.apply("queue time stop", who="operator")
    other = a_decide_item("a second item")
    su("INSERT INTO brain.time_entry (source_type, source_id, who, started_at, tier_at_start) "
       f"VALUES ('work_item', '{other}', 'operator', now() - interval '3 minutes', 'decide')")
    store.apply("queue time stop", who="operator")
    store.apply("accept work", id=other, by="operator", as_operator=True)
    store.apply("accept work", id=tid, by="operator", as_operator=True)

    d = reads.depth_and_clearance(days=7)
    sw = d["stopwatch"]
    ok = (sw["overall"]["n"] == 1                      # the nine-hour entry is not a measurement
          and abs(sw["overall"]["median_minutes"] - 3.0) < 0.1
          and sw["coverage"]["measured"] == 1 and sw["coverage"]["cleared"] == 2
          and sw["entries"]["abandoned"] == 1)
    check("a forgotten timer cannot poison the clearance figure, and the figure says how much of "
          "the work it actually watched",
          ok, str({"overall": sw["overall"], "coverage": sw["coverage"],
                   "entries": sw["entries"]}))


def main():
    print(f"  store: {os.environ['BRAIN_PG_DB']} (scratch). The live bus at ~/.swarm is untouched.")
    for fn in (test_start_stop_and_status_end_to_end,
               test_a_stopped_entry_is_never_edited,
               test_a_correction_supersedes_and_both_rows_survive,
               test_a_forgotten_timer_is_abandoned_and_leaves_every_mean,
               test_two_timers_at_once_are_refused_twice,
               test_an_agent_may_not_book_its_own_minutes_as_human_time,
               test_a_timer_needs_a_real_item,
               test_the_clearance_figure_reports_its_own_coverage,
               test_an_unmeasured_tier_is_null_and_never_another_tiers_number,
               test_an_empty_ledger_says_nothing_rather_than_zero,
               test_the_two_minute_decide_ceiling_stops_being_an_assertion,
               test_the_tier_snapshot_survives_a_demote,
               test_several_sittings_on_one_item_are_one_observation,
               test_the_claim):
        print(f"\n=== {fn.__name__} ===")
        fn()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
