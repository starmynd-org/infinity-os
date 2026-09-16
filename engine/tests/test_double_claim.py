#!/usr/bin/env python3
"""One task, two live agents. The 2026-08-18 incident, reproduced deliberately and then fenced.

THE INCIDENT, from the bus, timestamped. `07:22:34Z` T5 claimed 0379. `07:46:44Z` T5 asked q0403
WHILE STILL HOLDING IT and the task went to `blocked`. `07:49Z` the commander answered, and the
answer requeued 0379 to `inbox` without telling T5 or releasing it. `07:54:43Z` T4 claimed 0379.
`07:56Z` both T4 and T5 heartbeat `status=working task=0379`, both with live tmux windows and live
runners. Five minutes of two agents on one brief, and V00 records the same invariant as having
been caught on 2026-08-16 "only by luck".

WHAT THIS SUITE ADDS TO `test_claimer_predicate.py`, which already guards the producers. That
suite's docstring says `ask` "parks a held task at `blocked` WITHOUT MAKING IT CLAIMABLE, so it
cannot produce the double claim this predicate exists to prevent." That sentence is true about
`ask` and false about what comes next, and `test_reopen_after_ask_is_the_open_side_door` below is
the measurement: `_hold` calls a task held only when `state = 'active'`, so once `ask` has moved a
task to `blocked` it is still `claimed_by = T5` with a live T5 on it and the predicate waves
`reopen`, `fail` and `set state` straight through. `reopen` on a blocked task is not an exotic
attack, it is the commander's ORDINARY recovery move -- tasks 0400 and 0407 were both reopened
that way at 08:13Z and 08:31Z on the morning of the incident.

So the guard here is at the CONSUMER, not on each producer. `transitions.claim` refuses a task
another agent is still heartbeating `working` on, as an anti-join inside the statement that
already takes the row lock. Whatever put the row in `inbox` -- a requeue, a reopen, a forced
fail, a hand UPDATE, a verb nobody has written yet -- it stops at the one transition every one of
them must pass through to do harm.

THE CORPSE RULE, which is the other half and the reason this is not a wedge.
`transitions.CLAIM_LIVENESS_SECONDS` is 900, the SAME number as `reap`'s `--stale-seconds`
default, and `test_a_corpse_does_not_wedge_a_task_forever` pins both the equality and its
consequence. An agent silent longer than that is a corpse: `claim` steps over it and buries the
row. The equality means the set of tasks this gate withholds and the set of agents `reap` will
pronounce dead are exact complements, so no task is ever both unclaimable here and untouchable
there. Against `bin/swarm-run`'s 60s heartbeat that is a 15x margin on a background `while sleep`
loop which does not stop while the engine is thinking, so 15 missed beats is a dead process
rather than a long turn.

Run: python3 engine/tests/test_double_claim.py     (against the scratch database, never `brain`)
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

import store                                                     # noqa: E402
from swarm_engine import reads, transitions                      # noqa: E402

SCRATCH = str(ROOT / "engine/bin/scratch-db.sh")
LIVENESS = transitions.CLAIM_LIVENESS_SECONDS

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


def backdate(agent: str, seconds: int):
    """Age one agent's heartbeat for real, rather than sleeping through the window.

    Through `scratch-db.sh psql`, the idiom `test_brief_survives_finishers.py` already uses, and
    NOT through the waist: no verb sets `updated` backwards and none should. This is the one thing
    a test of a 900s timeout cannot obtain honestly any other way, and the alternative -- shrinking
    the constant for the test -- would assert the plumbing while never exercising the number the
    fleet actually runs on.
    """
    import subprocess
    r = subprocess.run([SCRATCH, "psql", "-tAc",
                        f"UPDATE brain.agent SET updated = now() - make_interval(secs => "
                        f"{int(seconds)}) WHERE name = '{agent}'"],
                       capture_output=True, text=True)
    assert r.returncode == 0, f"backdate failed: {r.stderr}"


def agents_on(tid: str):
    return sorted(a["name"] for a in reads.agents()
                  if a["work_item_id"] == tid and a["status"] == "working")


def state_of(tid):
    t = reads.task(tid)
    return (t["state"], t["claimed_by"])


def asked_and_answered(holder="T5", lane="dc", answer_kw=None):
    """Rebuild the incident's exact opening: claim, heartbeat, ask, operator answers.

    Returns the task id. The task is left wherever `answer` decided to leave it, because which
    of those two states it is IS the first thing under test.
    """
    reset()
    tid = store.apply("post", lane=lane, title="the incident", posted_by="commander",
                      agent_claimable=True, workdir="/tmp")["id"]
    got = store.apply("claim", agent=holder, lanes=[lane])
    assert got and got["id"] == tid, f"setup: claim got {got and got['id']}"
    store.apply("heartbeat", agent=holder, status="working", task=tid, pid=4242)
    q = store.apply("ask", question="which option", agent=holder, task=tid,
                    default="do nothing")
    store.apply("answer", qid=q["id"], text="option B", **(answer_kw or {}))
    return tid


# ---------------------------------------------------------------- the reproduction

def test_reopen_after_ask_is_the_open_side_door():
    """The producer this suite was written for, measured before it is fenced."""
    tid = asked_and_answered()

    # `answer` itself already refuses, and that half was fixed on 2026-08-16. Pinned here so a
    # later change cannot quietly reopen the front door while the back one is being watched.
    eq("answer alone does NOT requeue behind a live asker", state_of(tid), ("blocked", "T5"))

    # And then the ordinary recovery verb walks straight past the predicate, because `_hold`
    # calls a task held only when it is `active` and this one is `blocked`.
    r = store.apply("reopen", id=tid, reason="RESUMED after a transient engine window",
                    agent="commander")
    eq("reopen on a blocked-but-still-claimed task is not refused", r["state"], "inbox")
    eq("...and it clears the claim while T5 is still on the agent row",
       state_of(tid), ("inbox", ""))
    eq("...so the store now shows nobody owning work T5 is doing", agents_on(tid), ["T5"])

    # THE ASSERTION THE WHOLE TASK IS ABOUT. Before the fix this handed 0379 to T4.
    other = store.apply("claim", agent="T4", lanes=["dc"])
    truth("claim REFUSES a task T5 is still heartbeating on",
          other is None,
          f"DOUBLE CLAIM: T4 was handed {other and other['id']} while T5 holds it")
    eq("...and exactly one agent is working it", agents_on(tid), ["T5"])
    eq("...and the task is untouched, not consumed by the refused claim",
       state_of(tid), ("inbox", ""))


def test_answer_requeue_override_cannot_hand_the_task_to_a_second_agent():
    """The signed override still writes to the thread, and still cannot produce two agents."""
    tid = asked_and_answered(answer_kw={"requeue": True})
    eq("answer --requeue over a live asker does requeue, as designed",
       state_of(tid), ("inbox", ""))
    other = store.apply("claim", agent="T4", lanes=["dc"])
    truth("...but the claim gate still refuses to hand it out",
          other is None,
          f"DOUBLE CLAIM through the override: T4 got {other and other['id']}")
    eq("...one agent on the task", agents_on(tid), ["T5"])


def test_shutdown_requeue_cannot_steal_a_task_another_agent_now_owns():
    """Defect 3: killing T5's runner requeued 0379 while T4 was working it."""
    reset()
    tid = store.apply("post", lane="dc", title="handover", posted_by="commander",
                      agent_claimable=True, workdir="/tmp")["id"]
    store.apply("claim", agent="T5", lanes=["dc"])
    store.apply("heartbeat", agent="T5", status="working", task=tid, pid=1)

    # T5 goes silent and is reaped; T4 legitimately picks the task up. This is the real sequence:
    # ownership moved while T5's runner was still alive enough to fire its trap.
    backdate("T5", LIVENESS + 60)
    store.apply("reap", stale_seconds=LIVENESS, act=True)
    got = store.apply("claim", agent="T4", lanes=["dc"])
    eq("T4 legitimately owns it after the reap", (got and got["id"], got and got["claimed_by"]),
       (tid, "T4"))

    # Now T5's TERM trap fires. It acts on what T5 THOUGHT it held.
    r = store.apply("release", id=tid, agent="T5", reason="runner shutdown")
    eq("T5's shutdown release does not fire", r["released"], False)
    eq("...and it names the real owner rather than raising", r["owner"], "T4")
    eq("...so the task is still T4's, active, not an unowned row being worked",
       state_of(tid), ("active", "T4"))

    # The opposite direction, so this is a predicate and not a wall around nothing.
    r2 = store.apply("release", id=tid, agent="T4", reason="runner shutdown")
    eq("the real holder's release DOES fire", r2["released"], True)
    eq("...and the task goes back to the queue", state_of(tid), ("inbox", ""))


def test_a_corpse_does_not_wedge_a_task_forever():
    """A stale heartbeat from a dead agent must not make a task permanently unclaimable."""
    tid = asked_and_answered()
    store.apply("reopen", id=tid, reason="recovery", agent="commander")
    truth("while T5 is live the task is withheld",
          store.apply("claim", agent="T4", lanes=["dc"]) is None)

    # The rule: silent longer than CLAIM_LIVENESS_SECONDS and the row is a corpse, not a holder.
    backdate("T5", LIVENESS - 30)
    truth("still withheld 30s INSIDE the window",
          store.apply("claim", agent="T4", lanes=["dc"]) is None,
          "the window is being read as smaller than it is")
    backdate("T5", LIVENESS + 30)
    got = store.apply("claim", agent="T4", lanes=["dc"])
    eq("30s OUTSIDE the window the task is claimable again", got and got["id"], tid)
    eq("...by T4", got and got["claimed_by"], "T4")

    # And the corpse is buried, or `doctor` reports two agents on one task for ever afterwards.
    eq("the dead agent's row no longer points at the task", agents_on(tid), ["T4"])
    eq("...and it is marked dead rather than left working on nothing",
       [a["status"] for a in reads.agents() if a["name"] == "T5"], ["dead"])


def test_the_window_matches_reaps_so_there_is_no_unreachable_interval():
    """The un-wedge argument, asserted rather than written in a comment."""
    from swarm_engine import cli
    src = inspect.getsource(cli)
    m = re.search(r'"--stale-seconds",\s*type=int,\s*default=(\d+)', src)
    truth("`swarm reap --stale-seconds` has a readable default", m is not None,
          "the flag's default moved; this equality can no longer be checked")
    eq("claim's liveness window equals reap's stale window, so the two are complements",
       LIVENESS, int(m.group(1)) if m else None)

    # And the transition's own default, which is what a library caller gets.
    tsrc = inspect.getsource(transitions)
    m2 = re.search(r"def reap\(ctx, \*, stale_seconds=(\d+)", tsrc)
    truth("reap's transition signature carries the same number", m2 is not None, tsrc[:0])
    eq("...and it is the liveness window", int(m2.group(1)) if m2 else None, LIVENESS)

    # The consequence, measured rather than argued: at the exact boundary, whatever claim
    # withholds is something reap is willing to clear, so nothing is stranded between them.
    tid = asked_and_answered()
    store.apply("reopen", id=tid, reason="recovery", agent="commander")
    backdate("T5", LIVENESS + 5)
    truth("one second past the window, claim hands it out",
          store.apply("claim", agent="T4", lanes=["dc"]) is not None,
          "a task withheld by claim that reap would also decline is a wedge")


def test_an_agent_is_not_fenced_out_by_its_own_ghost():
    """A runner that died and came back under its own name must still claim its own work."""
    tid = asked_and_answered()
    store.apply("reopen", id=tid, reason="recovery", agent="commander")
    got = store.apply("claim", agent="T5", lanes=["dc"])
    eq("T5 can re-claim the task its OWN stale row points at", got and got["id"], tid)
    eq("...and nobody else appears on it", agents_on(tid), ["T5"])


def test_the_0400_infra_reopen_lands_where_it_should():
    """The interaction with task 0400's fix, pinned rather than discovered in production.

    0400 made `bin/swarm-run` call `reopen --from $AGENT` instead of `fail` when the engine died
    on infrastructure, so an outage stops spending attempts. That reopen runs while the runner is
    still alive and its agent row still reads `working` on that very task -- `hb idle` is at
    swarm-run:1083, INSIDE the empty-claim branch, so it fires after the next claim rather than
    before it. Which means this gate now decides who picks a reopened task up, and the answer had
    better be the runner that already has the workdir warm.
    """
    reset()
    tid = store.apply("post", lane="dc", title="killed by a dead network",
                      agent_claimable=True, workdir="/tmp",
                      posted_by="commander")["id"]
    store.apply("claim", agent="T5", lanes=["dc"])
    store.apply("heartbeat", agent="T5", status="working", task=tid, pid=99)
    store.apply("reopen", id=tid, reason="engine failed on INFRASTRUCTURE", agent="T5")
    eq("the infra reopen spends no attempt, as 0400 requires", state_of(tid), ("inbox", ""))
    eq("...and attempts really is 0", reads.task(tid)["attempts"], 0)

    truth("a DIFFERENT runner does not race it away while T5's row still names it",
          store.apply("claim", agent="T4", lanes=["dc"]) is None,
          "0400's reopen would hand the task to whichever runner polled first")
    got = store.apply("claim", agent="T5", lanes=["dc"])
    eq("T5 takes its own task back on its next loop", got and got["id"], tid)
    eq("...on attempt 1, so the outage still cost nothing", got and got["attempts"], 1)


def test_an_ordinary_queue_is_not_slowed_or_withheld():
    """The gate must be invisible when nothing is wrong, or it is a fleet-wide brake."""
    reset()
    ids = [store.apply("post", lane="dc", title=f"plain {i}", posted_by="commander",
                       agent_claimable=True, workdir="/tmp")["id"]
           for i in range(1, 6)]
    store.apply("heartbeat", agent="T9", status="idle", task=None)
    got = [store.apply("claim", agent=f"N{i}", lanes=["dc"]) for i in range(1, 6)]
    eq("five plain tasks, five claims", sorted(g["id"] for g in got if g), sorted(ids))
    eq("an idle agent's row fences nothing",
       len(reads.withheld_by_liveness(LIVENESS)), 0)


def test_the_gate_is_never_silent():
    """A refusal nothing can see is how a gate stops being evidence."""
    tid = asked_and_answered()
    store.apply("reopen", id=tid, reason="recovery", agent="commander")

    held = reads.withheld_by_liveness(LIVENESS)
    eq("the withheld task is readable", [r["id"] for r in held], [tid])
    eq("...naming the live agent", [r["live_agent"] for r in held], ["T5"])

    # doctor must call it critical: nobody owns work that is being done.
    import subprocess
    r = subprocess.run([sys.executable, str(ROOT / "engine/bin/swarm"), "doctor"],
                       capture_output=True, text=True, env={**os.environ})
    out = r.stdout + r.stderr
    truth("doctor names the task", tid in out, out[-600:])
    truth("doctor names the live agent", "T5" in out, out[-600:])
    truth("doctor exits non-zero on it, so cron can page", r.returncode != 0,
          f"rc={r.returncode}")

    # `claim --explain` is what the runner calls when it is handed nothing.
    r2 = subprocess.run([sys.executable, str(ROOT / "engine/bin/swarm"), "claim",
                         "--agent", "T4", "--lane", "dc", "--explain"],
                        capture_output=True, text=True, env={**os.environ})
    out2 = r2.stdout + r2.stderr
    eq("claim --explain still reports an empty claim", r2.returncode, 2)
    truth("...and says WHY, naming the task and the holder",
          tid in out2 and "T5" in out2, out2[-600:])


def test_the_gate_is_inside_the_locking_statement():
    """Atomicity was not weakened to buy the check. Structural, because it cannot be raced here."""
    src = inspect.getsource(transitions.claim)
    truth("the liveness anti-join is interpolated into the candidate SELECT",
          "_NOT_HELD_BY_A_LIVE_AGENT" in src,
          "the gate moved out of the claim statement")
    stmt = src[src.index("SELECT w.id"):src.index("LIMIT 1")]
    truth("...in the same statement that takes the row lock",
          "FOR UPDATE SKIP LOCKED" in stmt
          and "{_NOT_HELD_BY_A_LIVE_AGENT}" in stmt,
          stmt)
    truth("...and the fragment is a WHERE-clause anti-join, not a second query",
          transitions._NOT_HELD_BY_A_LIVE_AGENT.strip().startswith("AND NOT EXISTS")
          and "%(liveness)s" in transitions._NOT_HELD_BY_A_LIVE_AGENT,
          transitions._NOT_HELD_BY_A_LIVE_AGENT)
    truth("...so the claim still costs ONE candidate statement, as it did when it was measured",
          len(re.findall(r"ctx\.one\(", src)) == 2,
          f"{len(re.findall(r'ctx.one', src))} ctx.one calls in claim, expected 2 "
          f"(candidate + update)")
    truth("...and the row lock is still SKIP LOCKED, not a blocking lock",
          "FOR UPDATE SKIP LOCKED" in src and "FOR UPDATE\n" not in src)
    eq("there is exactly one liveness constant",
       len(re.findall(r"^CLAIM_LIVENESS_SECONDS = ", inspect.getsource(transitions), re.M)), 1)


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print("test_double_claim.py  --  one task, two live agents: reproduced, then made impossible")
    print(f"  store: {os.environ['BRAIN_PG_DB']} (scratch)")
    print(f"  liveness window: {LIVENESS}s\n")
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
