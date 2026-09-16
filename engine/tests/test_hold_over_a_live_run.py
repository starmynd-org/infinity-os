#!/usr/bin/env python3
"""Holding a row does not recall an in-flight claim. The 2026-08-18 0103 sequence, then fenced.

THE INCIDENT, from the thread of 0103, timestamped.

    11:20  T5       [post]  Globex weekly_summary ... publishes a fabricated decline
    11:30  T5       [claim] attempt 1
    11:30  admiral  [set]   agent_claimable = False
    11:31  admiral  [note]  HELD BY ADMIRAL PENDING THE OPERATOR'S ANSWER TO q0112

The admiral held it sixty seconds AFTER it was claimed, deliberately, because the task changes a
view on a client's executive dashboard and the operator's standing default is measure-and-report.
`swarm show 0103` then rendered `claimable by an agent: NO -- held for the operator` while the row
was `state active`, `claimed_by T5`, and a runner was executing it. Nothing bad happened because
T5 staged everything as `*.PROPOSED.sql` and touched no live view: a good agent, not a guarantee.

WHAT THE GATE IS AND IS NOT. `_AGENT_CLAIMABLE` is a CLAIM-TIME predicate inside the
`FOR UPDATE SKIP LOCKED` statement (task 0414), which is correct and is why it is atomic. Nothing
here touches it and `test_the_0414_refusal_is_unchanged` re-proves it. The gap was that no verb
meant "stop working this", and lowering the flag was being used as if one did. `claim` only ever
reads rows in `inbox`, so on an ACTIVE row the flag has NO effect today at all -- its whole effect
is on the row's future, after a fail or a release requeues it.

THE SEMANTICS CHOSEN, of the three the brief named: REFUSE THE `set`, through `transitions._hold`,
the same claimer predicate every finisher already loads its task through, with `--force` open for
the caller who really does mean "hold the future and let this run finish".

  * Not ACCEPT-AND-SIGNAL. That leaves the write silent by default: the admiral still gets a bare
    `ok`, still believes the row is held, the runner still runs. It is today's state with a
    message attached, and `test_the_signal_is_opportunistic_and_says_so` measures why the message
    on its own is not enough.
  * Not ACCEPT-AND-RELEASE. That makes a metadata key a finisher, throwing away in-flight work
    with no `--force` and no thread entry, standing beside `release` and `cancel` which the
    2026-08-16 fix deliberately gated behind the holder predicate. A second, weaker path to
    "take the task off its holder" is the defect this family exists to close.
  * REFUSE costs the caller nothing they had. The row is `active`, so it was never claimable; the
    refusal makes them name which of three different things they meant, and each has a verb.

Run: ENGINE_SCRATCH_DB=<db> BRAIN_PG_DB=<db> python3 engine/tests/test_hold_over_a_live_run.py
"""

from __future__ import annotations

import inspect
import io
import os
import re
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

import store                                                     # noqa: E402
from swarm_engine.transitions import VerbError            # noqa: E402
from swarm_engine import cli, reads, transitions                  # noqa: E402

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


def state_of(tid):
    t = reads.task(tid)
    return (t["state"], t["claimed_by"], t["agent_claimable"])


def the_incident(lane="dash", holder="T5"):
    """post, claim, and stop one instant before the admiral's `set`. Returns the task id."""
    reset()
    tid = store.apply("post", lane=lane, posted_by=holder, agent_claimable=True,
                      workdir="/tmp", external=True,
                      title="Globex weekly_summary: the view publishes a fabricated decline",
                      )["id"]
    got = store.apply("claim", agent=holder, lanes=[lane])
    assert got and got["id"] == tid, f"setup: claim got {got and got['id']}"
    store.apply("heartbeat", agent=holder, status="working", task=tid, pid=4242)
    return tid


def show(tid):
    buf = io.StringIO()

    class A:
        id = tid
        json = False
        full = True
    with redirect_stdout(buf):
        cli.cmd_show(A())
    return buf.getvalue()


def doctor():
    buf = io.StringIO()

    class A:
        stale_seconds = 900
        stuck_seconds = 7200
        question_seconds = 43200
        blocked_threshold = 5
        json = False
    with redirect_stdout(buf):
        rc = cli.cmd_doctor(A())
    return rc, buf.getvalue()


# ---------------------------------------------------------------- the reproduction

def test_the_bare_hold_over_a_live_run_is_refused():
    """post, claim, lower the flag. The exact 0103 sequence, and the write no longer lands."""
    tid = the_incident()
    eq("setup: the row is the incident's -- active, held by T5, agent-claimable",
       state_of(tid), ("active", "T5", True))

    try:
        store.apply("set", id=tid, key="agent_claimable", value="false", agent="admiral")
    except VerbError as e:
        ok("the admiral's bare `set agent_claimable false` on an ACTIVE row is REFUSED")
        eq("...with the claimer predicate's own exit code, not a new one", e.code, 6)
        truth("...and it names the holder", "T5" in str(e), str(e)[:200])
        truth("...and says a hold is not a way to stop a running agent",
              "NOT A WAY TO STOP A RUNNING AGENT" in str(e), str(e)[:400])
        truth("...and names `cancel --force` as what actually stops the run",
              "swarm cancel" in str(e), str(e)[:400])
        truth("...and names --force as the way to hold the row's future",
              "--force" in str(e), str(e)[:400])
    else:
        bad("the admiral's bare `set agent_claimable false` on an ACTIVE row is REFUSED",
            "it was accepted, which is the 0103 defect verbatim")

    eq("the row is untouched by the refusal: still claimable, still T5's, still active",
       state_of(tid), ("active", "T5", True))
    eq("...and no `set` event was written to the thread",
       [e["kind"] for e in reads.thread(tid) if e["kind"] == "set"], [])


def test_the_refusal_does_not_reach_a_row_nobody_is_running():
    """The narrowness of the change. Every state but `active` is exactly as it was."""
    reset()
    tid = store.apply("post", lane="dash", title="not running", posted_by="commander",
                      agent_claimable=True, workdir="/tmp")["id"]
    r = store.apply("set", id=tid, key="agent_claimable", value="false", agent="admiral")
    eq("lowering the flag on an INBOX row is ordinary and still works", r["agent_claimable"],
       False)
    eq("...and the row is held from the fleet, as the operator asked",
       reads.task(tid)["agent_claimable"], False)

    got = store.apply("claim", agent="T9", lanes=["dash"])
    truth("...so `claim` will not hand it out", got is None,
          f"the hold did not take: T9 got {got and got['id']}")

    # An `active` row with the claim already cleared is not held by anybody, which is `_hold`'s
    # own definition and not a second one written here.
    tid2 = the_incident(lane="dash2", holder="T7")
    store.apply("release", id=tid2, agent="T7", reason="stepping back")
    r2 = store.apply("set", id=tid2, key="agent_claimable", value="false", agent="admiral")
    eq("a released row takes the hold with no force at all", r2["agent_claimable"], False)


def test_raising_the_flag_is_still_ungated():
    """The safe direction stays open. Taking work BACK is what the gate is for, not giving it."""
    tid = the_incident(lane="raise")
    store.apply("release", id=tid, agent="T5", reason="done looking")
    store.apply("set", id=tid, key="agent_claimable", value="false", agent="admiral")
    r = store.apply("set", id=tid, key="agent_claimable", value="true", agent="operator",
                    as_operator=True)
    eq("the operator can raise it again", r["agent_claimable"], True)
    eq("...and no force was needed", state_of(tid)[2], True)


def test_the_forced_hold_lands_and_says_what_it_did_not_do():
    """--force is open, because holding the row's FUTURE is the admiral's real intent."""
    tid = the_incident()
    r = store.apply("set", id=tid, key="agent_claimable", value="false", agent="admiral",
                    force=True)
    eq("the signed hold lands", r["agent_claimable"], False)
    eq("...and the verb reports WHO it was held over, rather than a bare ok",
       r["held_while_active"], "T5")
    eq("...and it did NOT release the claim or stop the task",
       state_of(tid), ("active", "T5", False))

    trail = "\n".join(e["text"] for e in reads.thread(tid))
    truth("`_hold`'s own OVERRIDE line names who forced it, unchanged by this task",
          "OVERRIDE" in trail and "admiral" in trail, trail[-400:])
    truth("...and a second line says what the override did NOT do",
          "did" in trail and "NOT recall the claim" in trail, trail[-400:])
    truth("...and points at the verb that would actually stop the run",
          f"swarm cancel {tid}" in trail, trail[-400:])

    # The person who TYPED it is told too, not only the thread. The admiral read
    # `agent_claimable = False` on 0103, believed the row was held, and it was not.
    buf, err = io.StringIO(), io.StringIO()

    class A:
        id = tid
        key = "agent_claimable"
        value = "true"
        agent = "admiral"
        force = True
        as_operator = False
    # re-run the same forced lower to capture the CLI's own output
    A.value = "false"
    with redirect_stdout(buf), redirect_stderr(err):
        cli.cmd_set(A())
    said = buf.getvalue() + err.getvalue()
    truth("`swarm set --force` tells the caller it did NOT stop the run",
          "did NOT stop the run" in said, said)
    truth("...and that doctor will report it until one of the two is made true",
          "doctor" in said and "CRITICAL" in said, said)
    truth("...without hiding the stored value it did write",
          "agent_claimable = False" in said, said)

    msgs, _ = reads.messages("T5")
    truth("the holder is messaged", len(msgs) == 2, f"{len(msgs)} messages")
    truth("...and the message tells it not to apply anything irreversible",
          msgs and "irreversible" in msgs[0]["text"], msgs and msgs[0]["text"][:200])
    truth("...and is filed against the task, so the thread carries it too",
          msgs and msgs[0]["work_item_id"] == tid, msgs and msgs[0]["work_item_id"])


def test_the_holder_lowering_its_own_flag_needs_no_force_and_is_not_called_an_override():
    """`_hold` lets the holder through, and the record must not call that an override.

    The same defect this task is about, one size down: a sentence that is NEARLY true, on a row
    somebody reads as a guarantee. T5 lowering the flag on its own live task passes the predicate
    with no `--force`, and a thread line saying `T5 forced` would describe an act nobody performed.
    Every surface still reports the resulting held-and-active row, because the board is still
    saying two things at once whoever wrote it.
    """
    tid = the_incident()
    r = store.apply("set", id=tid, key="agent_claimable", value="false", agent="T5")
    eq("the holder needs no --force to hold its own live row", r["agent_claimable"], False)
    eq("...and the verb still reports the state it produced", r["held_while_active"], "T5")

    trail = "\n".join(e["text"] for e in reads.thread(tid))
    truth("no OVERRIDE line, because nothing was overridden", "OVERRIDE" not in trail, trail)
    truth("...and the record does not say `forced`", "T5 forced" not in trail, trail[-300:])
    truth("...it says what happened", "lowered the flag on its own live task" in trail,
          trail[-300:])

    rc, out = doctor()
    truth("doctor still reports the row: the board says two things whoever wrote them",
          any(tid in l and "HELD FOR THE OPERATOR" in l for l in out.splitlines()), out)
    truth("...and it is still CRITICAL", rc != 0, f"exit {rc}")


def test_show_never_claims_held_for_the_operator_about_a_live_run():
    """The line that was read as a guarantee and was a description of a column."""
    tid = the_incident()
    store.apply("set", id=tid, key="agent_claimable", value="false", agent="admiral", force=True)
    out = show(tid)

    line = [l for l in out.splitlines() if "claimable by an agent" in l]
    truth("`show` prints the claimability line exactly once", len(line) == 1, str(line))
    got = line[0] if line else ""
    truth("...and it no longer reads as a bare `NO -- held for the operator`",
          got.strip() != "claimable by an agent: NO -- held for the operator", got)
    truth("...it says the row IS being executed, and by whom",
          "T5 IS EXECUTING IT NOW" in got, got)
    truth("...and the block says the hold reaches future claims only",
          "FUTURE claims only" in out, out)
    truth("...and tells the agent reading it -- this function IS the agent prompt -- to stage "
          "and report", "apply nothing irreversible" in out, out)
    truth("...and names the verb that stops the run", f"swarm cancel {tid}" in out, out)

    # The other side of the predicate, or this is a banner and not a test.
    reset()
    quiet = store.apply("post", lane="dash", title="an ordinary held row", posted_by="operator",
                        actor_type="human")["id"]
    q = show(quiet)
    truth("an INBOX held row still reads plainly `NO -- held for the operator`",
          "claimable by an agent: NO -- held for the operator" in q, q)
    truth("...with no claim about anybody executing it", "IS EXECUTING IT NOW" not in q, q)


def test_ls_marks_the_row_the_operator_scans():
    """`[OPERATOR]` was scoped to inbox/blocked, so `active` was the one state it said nothing in."""
    tid = the_incident()
    store.apply("set", id=tid, key="agent_claimable", value="false", agent="admiral", force=True)
    buf = io.StringIO()

    class A:
        state = None
        lane = None
        json = False
    with redirect_stdout(buf):
        cli.cmd_ls(A())
    out = buf.getvalue()
    row = [l for l in out.splitlines() if l.startswith(tid)]
    truth("the row is on `swarm ls`", len(row) == 1, out)
    truth("...marked [HELD+RUNNING]", row and "[HELD+RUNNING]" in row[0], row)
    truth("...and still carries its [GATE], because it is client-visible work",
          row and "[GATE]" in row[0], row)


def test_doctor_reports_held_and_active_as_a_critical():
    """Same class as the double-claim CRITICAL: the board says one thing, reality another."""
    tid = the_incident()
    rc, before = doctor()
    truth("doctor is quiet about the row while it is legitimately claimable",
          "HELD FOR THE OPERATOR" not in before, before)

    store.apply("set", id=tid, key="agent_claimable", value="false", agent="admiral", force=True)
    rc, out = doctor()
    line = [l for l in out.splitlines() if tid in l and "ACTIVE" in l]
    truth("doctor reports it", len(line) >= 1, out)
    got = line[0] if line else ""
    truth("...as a CRITICAL, so `doctor` exits non-zero", rc != 0, f"exit {rc}\n{out}")
    truth("...naming the agent executing it", "T5" in got, got)
    truth("...and saying the flag does not recall an in-flight claim",
          "does NOT recall an in-flight claim" in got, got)
    truth("...and naming the incident it came from", "0103" in got, got)
    truth("...and both repairs, so the reader can make the board true either way",
          "swarm cancel" in got and "agent_claimable true" in got, got)
    truth("...and flagging that this one is client-visible work", "GATE" in got, got)


def test_the_signal_is_opportunistic_and_says_so():
    """What a terminal mid-run ACTUALLY notices, measured rather than hoped."""
    run = (ROOT / "engine/bin/swarm-run").read_text()
    inbox_reads = [l.strip() for l in run.splitlines() if "swarm" in l.lower() and "inbox" in l]
    truth("`bin/swarm-run` reads an inbox in exactly one place", len(inbox_reads) == 1,
          str(inbox_reads))
    # And that place is the PLANNER prompt, not a terminal's loop. Measured by what surrounds it.
    idx = run.index(inbox_reads[0]) if inbox_reads else 0
    truth("...and it is the planner wake, so no terminal polls its inbox mid-run",
          "planning pass" in run[max(0, idx - 3000):idx], run[max(0, idx - 400):idx][-200:])

    # So the channel that does reach the model is the stdout of the verbs it calls itself.
    tid = the_incident()
    store.apply("set", id=tid, key="agent_claimable", value="false", agent="admiral", force=True)
    buf = io.StringIO()

    class A:
        id = tid
        text = "staged the change, applied nothing"
        agent = "T5"
    with redirect_stdout(buf):
        cli.cmd_note(A())
    out = buf.getvalue()
    truth("`swarm note` -- which a working agent calls repeatedly -- banners the hold",
          "IS NOW HELD FOR THE OPERATOR" in out, out)
    truth("...and says plainly that nothing recalled the claim",
          "Nothing recalled your claim" in out, out)
    truth("...and asks for a disclosure if something was already applied",
          "ALREADY applied" in out, out)
    truth("...without swallowing the verb's own result", "noted" in out, out)

    # It is a banner, not a gate: the note still landed. A signal that failed the verb would be a
    # worse defect than the one being fixed.
    truth("the note itself was written",
          any("staged the change" in e["text"] for e in reads.thread(tid)),
          str([e["text"][:40] for e in reads.thread(tid)]))


def test_the_0414_refusal_is_unchanged():
    """The claim-time gate re-proven: still one WHERE fragment, still inside the locking statement.

    The constraint the brief names first. Whatever this task did, `claim` must still refuse the
    operator's rows atomically, and the refusal must not have moved to a surface or a post-claim
    release.
    """
    src = inspect.getsource(transitions.claim)
    truth("the gate is still a WHERE fragment defined once",
          len(re.findall(r"^_AGENT_CLAIMABLE = ", inspect.getsource(transitions), re.M)) == 1)
    truth("...still interpolated into the candidate statement",
          "{_AGENT_CLAIMABLE}" in src, src[:0])
    stmt = src[src.index("SELECT w.id"):src.index("LIMIT 1")]
    truth("...inside the statement that takes the row lock",
          "FOR UPDATE SKIP LOCKED" in stmt and "{_AGENT_CLAIMABLE}" in stmt, stmt)
    truth("...and nothing in `set` releases or re-claims a row",
          "claimed_by = ''" not in inspect.getsource(transitions.set_field),
          inspect.getsource(transitions.set_field)[:0])

    # And behaviourally, at the scale 0414 measured it: 15 of the operator's rows, a `lanes: ["*"]`
    # claimer, 0 of them handed out.
    reset()
    mine, fleet = [], []
    for i in range(15):
        mine.append(store.apply("post", lane=f"l{i % 4}", title=f"the operator's own {i}",
                                posted_by="operator", actor_type="human")["id"])
    for i in range(9):
        fleet.append(store.apply("post", lane=f"l{i % 4}", title=f"fleet work {i}",
                                 posted_by="commander", agent_claimable=True,
                                 workdir="/tmp")["id"])
    handed = []
    while True:
        got = store.apply("claim", agent="T1", lanes=["*"])
        if not got:
            break
        handed.append(got["id"])
        store.apply("release", id=got["id"], agent="T1", reason="drain")
        store.apply("set", id=got["id"], key="state", value="done", agent="T1")
    leaked = [t for t in handed if t in mine]
    eq("15 of 15 operator rows REFUSED to a `lanes: [*]` claimer",
       f"{len([t for t in mine if t not in handed])} of {len(mine)}", "15 of 15")
    eq("...and none of them leaked", leaked, [])
    eq("...while 9 of 9 fleet rows were still handed out",
       sorted(t for t in handed if t in fleet), sorted(fleet))
    eq("...and nothing else came out", sorted(handed), sorted(fleet))


def test_the_predicate_is_the_one_the_finishers_use():
    """Structural. Not a second copy of the claimer predicate written next to a better message."""
    src = inspect.getsource(transitions.set_field)
    truth("`set` reaches the hold gate for `agent_claimable` as well as for `state`",
          src.count("_hold(ctx, id, who") == 2, src)
    truth("...and it is the same function, with a verb-specific repair rather than a second rule",
          "advice=_LOWERING_A_HELD_ROW" in src, src)
    truth("...only for LOWERING, so the safe direction is not gated",
          "not truthy(value)" in src, src)
    truth("`_hold` still refuses on its one definition of held",
          "task.get(\"state\") != \"active\" or not holder or caller == holder"
          in inspect.getsource(transitions._hold), inspect.getsource(transitions._hold)[:0])

    # One definition of held-and-active across every surface, or this task's whole finding --
    # four surfaces describing one row and one of them wrong -- comes back in a new shape.
    csrc = inspect.getsource(cli)
    truth("every surface asks `reads.held_and_active`, none re-derives it",
          csrc.count("reads.held_and_active(") == 3
          and "state'] == 'active'" not in csrc, csrc[:0])
    truth("...and `doctor` reads the set from one query",
          "reads.held_while_active()" in csrc, csrc[:0])


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print("test_hold_over_a_live_run.py  --  a hold that did not stop a run is not a hold")
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
