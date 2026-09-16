#!/usr/bin/env python3
"""A deliberate stop and an agent that died are not the same thing on the board any more.

THE INCIDENT, 2026-08-19, from the post of task 0273:

    00:55Z  The commander stops T4 and T5 on a rate-limit decision. Exit 0. The bus feed carries
            no stop event for either agent. No actor, no time, no reason.
    00:56Z  An admiral pass sees two terminals stopped with no trail of any kind, cannot tell a
            deliberate stop from a silent failure, and records: "something else stopped two
            terminals tonight with no trail, and that is worse".
    00:57Z  That pass restarts both. Correctly, on the information it had.
    01:0xZ  The admiral reads the commander's answer, finds them running, stops them again.

The restarting pass was not wrong. Losing two of five terminals silently is exactly what an
admiral exists to catch, and it had no way to see the stop. `swarm stop` wrote one column and
emitted nothing, so the two states were byte-identical: a STOPPED flag and no explanation.

WHAT THIS FILE ASSERTS. `test_a_deliberate_stop_is_distinguishable_from_an_agent_that_died` is
the named test the task's definition of done asks for, and it is written as the comparison rather
than as a property of one row: it stops one agent through the verb, kills another by writing
`stopped_at` directly the way a reaper or a hand `UPDATE` would, and requires that what `swarm
status` prints about them differs and that the difference names the actor. A test that only
asserted "the reason appears" would pass against a build that printed the same reason for both.

WHAT IT DOES NOT ASSERT. `stopped_at` means exactly what it meant, and the runner reads it
exactly as before -- `test_the_lifecycle_itself_is_unchanged` is that sentence measured rather
than promised. This is the verb learning to leave a record, not the lifecycle changing.

Run: ENGINE_SCRATCH_DB=<db> BRAIN_PG_DB=<db> python3 engine/tests/test_stop_leaves_a_trail.py
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT))

os.environ.setdefault("BRAIN_PG_DB", os.environ.get("ENGINE_SCRATCH_DB", "brain_scratch"))

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _scratch_preflight import reconcile              # noqa: E402
reconcile(os.environ["BRAIN_PG_DB"])
os.environ.pop("SWARM_PARENT_TASK", None)

# Set BEFORE `store` is imported, exactly as test_question_paging_join.py does and for the same
# reason: the fabric event is half of what this file measures, and a stray
# BRAIN_AFTER_COMMIT_HOOKS=0 in the shell would report the trail as complete while measuring only
# the half that cannot be switched off.
os.environ["BRAIN_AFTER_COMMIT_HOOKS"] = "1"

import store                                                      # noqa: E402
from store import transitions as _st                              # noqa: E402
from swarm_engine import cli, reads                               # noqa: E402
from swarm_engine.transitions import VerbError                    # noqa: E402

OK, ERR, EMPTY = 0, 1, 2

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
    subprocess.run([str(ROOT / "engine/bin/scratch-db.sh"), "truncate"],
                   check=True, capture_output=True)


def register(name, status="idle"):
    """A real agent exists because it heartbeated. Nothing else registers one."""
    store.apply("heartbeat", agent=name, status=status)


def run(fn, **kw):
    """Run one CLI verb with an argparse-shaped namespace. Returns (exit, stdout, stderr)."""
    ns = type("A", (), kw)
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = fn(ns)
    return rc, out.getvalue().strip(), err.getvalue().strip()


def status_line(name):
    """The line `swarm status` prints for one agent. What a human or an admiral actually reads."""
    _, out, _ = run(cli.cmd_status, json=False)
    for line in out.splitlines():
        if line.strip().split(" ")[0] == name:
            return line.strip()
    return ""


def stopped_part(name):
    """Just the STOPPED annotation, with the agent's own name and clock cut off it.

    Comparing whole lines would have been a VACUOUS assertion and nearly shipped as one: two
    agents' status lines differ in the name and the silent duration no matter what the STOPPED
    flag says, so `line_a != line_b` passes against the exact build this task exists to fix.
    What has to differ is the part that explains the flag, so that is what this returns.
    """
    line = status_line(name)
    return line[line.index("STOPPED"):] if "STOPPED" in line else ""


def kill_silently(name):
    """An agent that DIED: `stopped_at` set with nothing filed anywhere.

    This is what the incident's admiral was actually looking at, and it is written through the
    store rather than through the verb on purpose -- a reaper, a hand `UPDATE`, or any stop taken
    before task 0273 all leave exactly this row and nothing else.
    """
    r = subprocess.run(
        [str(ROOT / "engine/bin/scratch-db.sh"), "psql", "-q", "-c",
         f"UPDATE brain.agent SET stopped_at = now() WHERE name = '{name}'"],
        capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"could not kill {name} by hand: {r.stderr.strip()}")


def feed_text(agent):
    return "\n".join(f"{e['from_who']} -> {e['to_who']}: {e['text']}"
                     for e in reads.feed(since_minutes=60, agent=agent))


def events(kind_prefix="fleet."):
    with store.read("runtime") as s:
        return s.query("SELECT type, subject_id, payload_summary, payload_ref, "
                       "octet_length(payload_summary) AS bytes FROM brain.event "
                       "WHERE type LIKE %s ORDER BY event_seq", (kind_prefix + "%",))


# ------------------------------------------------------------------ the named test

def test_a_deliberate_stop_is_distinguishable_from_an_agent_that_died():
    """THE 00:57Z QUESTION: was this stopped on purpose, or did it just go?"""
    reset()
    register("ZMEANT")
    register("ZDIED")

    run(cli.cmd_stop, agent="ZMEANT", reason="rate limit: holding until the window resets",
        by="commander", json=False)
    kill_silently("ZDIED")

    meant, died = stopped_part("ZMEANT"), stopped_part("ZDIED")
    truth("both agents are flagged STOPPED, which is the state the admiral saw",
          meant.startswith("STOPPED") and died.startswith("STOPPED"),
          f"[{status_line('ZMEANT')}] [{status_line('ZDIED')}]")
    truth("THE FIX: what status says about the flag is not the same for the two",
          meant != died,
          f"a deliberate stop and a dead agent still read identically: both say [{meant}]")
    truth("the deliberate one names who took the decision",
          "commander" in meant, meant)
    truth("...and why", "rate limit" in meant, meant)
    truth("the one nobody signed says SO, rather than looking like a decision",
          "no record" in died.lower(), died)
    truth("and it does not borrow the other agent's reason",
          "rate limit" not in died and "commander" not in died, died)


def test_a_stale_record_does_not_dress_up_a_later_unexplained_stop():
    """the near-miss: an agent explained once must not look explained forever"""
    reset()
    register("ZSTALE")
    run(cli.cmd_stop, agent="ZSTALE", reason="the first stop, which WAS signed",
        by="commander", json=False)
    run(cli.cmd_start, agent="ZSTALE", reason="released", by="commander", json=False)
    truth("setup: it ran again, and there is a signed stop in its history",
          not reads.agent_stopped("ZSTALE"))

    # ...and now it dies, or somebody sets the column by hand. The signed record above is still
    # the newest `stop` this agent has, and a join that took "the newest one" would hand this
    # stop the previous one's actor and reason.
    kill_silently("ZSTALE")
    line = stopped_part("ZSTALE")
    truth("the new stop is reported as unexplained, not as the old one",
          "no record" in line.lower(), line)
    truth("it does not borrow the earlier stop's actor",
          "commander" not in line, line)
    truth("...or its reason", "the first stop" not in line, line)


def test_a_stop_with_no_reason_is_refused_rather_than_defaulted():
    """an unsigned stop is the bug; a stop signed 'unspecified' is the bug with a label"""
    reset()
    register("ZR")
    raised = None
    try:
        store.apply("stop", agent="ZR", by="commander", reason="")
    except VerbError as e:
        raised = str(e)
    truth("the transition refuses an empty reason", raised is not None,
          "a reasonless stop was accepted")
    truth("...and the refusal says why the reason is load-bearing",
          bool(raised) and "died" in raised, raised or "")
    truth("NOTHING was written: the agent is not stopped", not reads.agent_stopped("ZR"))
    truth("...and no record was filed either", "ZR" not in feed_text("ZR"))

    # The same refusal one layer up, where the operator meets it.
    p = cli.build_parser() if hasattr(cli, "build_parser") else None
    if p is not None:
        code = None
        try:
            p.parse_args(["stop", "--agent", "ZR"])
        except SystemExit as e:
            code = e.code
        truth("and the parser refuses `stop --agent X` with no --reason at all", code == 2,
              f"argparse exited {code}")


def test_the_stop_is_in_the_feed_with_actor_time_and_reason():
    """the feed is where the admiral looked and found nothing"""
    reset()
    register("ZF")
    before = feed_text("ZF")
    truth("setup: the feed says nothing about ZF yet", before == "", before)

    run(cli.cmd_stop, agent="ZF", reason="drawing past the rate-limit budget",
        by="commander", json=False)
    after = reads.feed(since_minutes=60, agent="ZF")
    truth("exactly one record, not none and not two", len(after) == 1, str(after))
    rec = after[0] if after else {}
    eq("the actor is the from side", rec.get("from_who"), "commander")
    eq("the agent is the to side", rec.get("to_who"), "ZF")
    truth("the time is on the record", rec.get("ts") is not None)
    truth("the reason is on the record, whole",
          "drawing past the rate-limit budget" in (rec.get("text") or ""), str(rec))
    truth("and it says it was a STOP rather than any other kind of traffic",
          (rec.get("text") or "").startswith("STOP ZF"), str(rec))


def test_start_is_visible_the_same_way_and_actually_releases():
    """the other half of the pair, including the half task 0253 fixed"""
    reset()
    register("ZS")
    run(cli.cmd_stop, agent="ZS", reason="stopped so it can be started", by="commander",
        json=False)
    rc, out, _ = run(cli.cmd_start, agent="ZS", reason="rate-limit window reset", by="admiral",
                     json=False)
    eq("exit 0", rc, OK)
    truth("the line still begins the way `start` has always begun",
          out.startswith("ZS started"), out)
    truth("...and now says who released it", "admiral" in out, out)
    truth("the release is real, not a print", not reads.agent_stopped("ZS"))
    f = feed_text("ZS")
    truth("the start is in the feed with its actor and reason",
          "START ZS by admiral" in f and "window reset" in f, f)


def test_a_start_that_released_nothing_files_no_record():
    """a trail that logs things that did not happen is not a trail"""
    reset()
    register("ZN")
    rc, out, _ = run(cli.cmd_start, agent="ZN", reason="nothing to do", by="admiral", json=False)
    eq("exit 2 -- there was nothing to release", rc, EMPTY)
    truth("and the feed is empty, because nothing happened",
          feed_text("ZN") == "", feed_text("ZN"))
    truth("no fleet.agent.started event either",
          not [e for e in events() if e["subject_id"] == "ZN"], str(events()))


def test_a_reason_past_the_4096_byte_ceiling_neither_truncates_nor_refuses_the_stop():
    """the ceiling REJECTS rather than truncates, so an unbounded reason had to be checked"""
    reset()
    register("ZBIG")
    long_reason = "rate limit. " + ("why this matters, at length. " * 400)
    truth(f"setup: the reason is {len(long_reason)} bytes, past the 4096 ceiling",
          len(long_reason.encode("utf-8")) > 4096)

    rc, out, _ = run(cli.cmd_stop, agent="ZBIG", reason=long_reason, by="commander", json=False)
    eq("the stop still lands", rc, OK)
    truth("the agent is stopped", reads.agent_stopped("ZBIG"))

    with store.read("runtime") as s:
        text = s.scalar("SELECT text FROM brain.message WHERE to_agent = 'ZBIG' "
                        "AND kind = 'stop' ORDER BY seq DESC LIMIT 1")
    truth("THE RECORD IS NOT CUT: the whole reason is in the store",
          long_reason.rstrip() in (text or ""),
          f"stored {len(text or '')} bytes of a {len(long_reason)}-byte reason")

    ev = [e for e in events() if e["subject_id"] == "ZBIG"]
    if ev:
        truth("the event's payload_summary is inside the 4096-byte ceiling",
              ev[0]["bytes"] <= 4096, f"{ev[0]['bytes']} bytes")
        truth("...and says in the payload that it summarised rather than cutting silently",
              "reason_truncated_in_summary" in (ev[0]["payload_summary"] or ""),
              ev[0]["payload_summary"][:200])
        truth("...and points at the untruncated record",
              (ev[0]["payload_ref"] or "").startswith("brain.message:"), str(ev[0]))
    else:
        bad("a fleet.agent.stopped event was emitted for the long reason",
            "no event at all -- the after-commit hook did not fire")


def test_the_stop_event_carries_actor_timestamp_and_reason():
    """the fabric's copy, for subscribers that never read the feed"""
    reset()
    register("ZE")
    run(cli.cmd_stop, agent="ZE", reason="a signed stop", by="commander", json=False)
    ev = [e for e in events("fleet.agent.stopped") if e["subject_id"] == "ZE"]
    truth("one fleet.agent.stopped event", len(ev) == 1, str(events()))
    if ev:
        truth("the actor is in it", '"by":"commander"' in ev[0]["payload_summary"],
              ev[0]["payload_summary"])
        truth("the reason is in it", "a signed stop" in ev[0]["payload_summary"],
              ev[0]["payload_summary"])
        truth("the subject is the agent, so a subscriber can filter on it",
              ev[0]["subject_id"] == "ZE", str(ev[0]))


def test_pause_and_resume_are_signed_too():
    """the same family: runtime_flag has carried set_by since migration 1 and nobody read it"""
    reset()
    run(cli.cmd_pause, reason="rate-limit window, whole fleet", by="commander", json=False)
    _, out, _ = run(cli.cmd_status, json=False)
    truth("status says who paused the fleet", "PAUSED by commander" in out,
          out.splitlines()[0] if out else "")
    truth("...and why", "rate-limit window" in out, out.splitlines()[0] if out else "")

    run(cli.cmd_resume, reason="window reset", by="admiral", json=False)
    rec = reads.fleet_pause_record()
    truth("the fleet is no longer paused", not reads.fleet_paused())
    eq("and the resume is signed by whoever ran it, not by the default",
       (rec or {}).get("set_by"), "admiral")


def test_the_lifecycle_itself_is_unchanged():
    """the constraint: `stopped_at` means what it meant and the runner reads it as before"""
    reset()
    register("ZC")
    run(cli.cmd_stop, agent="ZC", reason="the constraint check", by="commander", json=False)
    truth("stop still sets the column the runner reads", reads.agent_stopped("ZC"))

    tid = store.apply("post", lane="engine", posted_by="test", agent_claimable=True,
                      workdir="/tmp", title="a task a stopped agent must not take")["id"]
    truth("a stopped agent still claims nothing",
          store.apply("claim", agent="ZC", lanes=["engine"]) is None)
    run(cli.cmd_start, agent="ZC", reason="release", by="commander", json=False)
    got = store.apply("claim", agent="ZC", lanes=["engine"])
    truth("and once started it claims again -- end to end, through the real verbs",
          got is not None and got["id"] == tid, str(got))
    store.apply("cancel", id=tid, agent="ZC", reason="test teardown", force=True)


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    # The named test first, whatever it sorts as: it is the one the definition of done names.
    named = "test_a_deliberate_stop_is_distinguishable_from_an_agent_that_died"
    tests.sort(key=lambda t: (t.__name__ != named, t.__name__))
    print("test_stop_leaves_a_trail.py  --  a stop nobody signed reads as a crash, and on "
          "2026-08-19 it was read as one")
    print(f"  store: {os.environ['BRAIN_PG_DB']} (scratch)\n")
    for t in tests:
        print(f"{t.__name__.replace('test_', '')}: "
              f"{t.__doc__.splitlines()[0] if t.__doc__ else ''}")
        try:
            t()
        except Exception:                                        # noqa: BLE001
            import traceback
            bad(f"{t.__name__} raised", traceback.format_exc().splitlines()[-1])
        print()
    print(f"{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
