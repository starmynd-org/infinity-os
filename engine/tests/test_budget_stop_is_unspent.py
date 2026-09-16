#!/usr/bin/env python3
"""A run stopped for SPEND gives its attempt back. Task 0461.

THE DEFECT, measured on live `brain` 2026-08-29 at 11:45Z rather than reasoned about.

Nine rows sat in `state = blocked` at `attempts 1 / max_attempts 2` simultaneously. Eight of the
nine carried a `hard_stop` row in `brain.budget_incident` naming the row itself -- 0408 0430 0431
0437 0440 0442 0443 0444, incidents 229 through 245, every one `detected_by = sweep`,
`action_taken = run_stopped`, `prescribed_disposition = block`. The ninth, 0407, was a genuine
operator-question block and is the control that proves the other eight were not just "everything
blocked that hour".

WHERE THE ATTEMPT IS ACTUALLY CHARGED, which is the thing that makes this an omission rather than
an act. `claim` does `attempts = attempts + 1` in the statement that hands the row out, BEFORE the
run has done anything. `block` runs through `_finish`, which touches `result`, `finished_at`,
`state`, the thread and the run row -- and never `attempts`. So nothing charges a budget stop an
attempt. The claim charges it, and `block` leaves it charged. `max_attempts` defaults to 2, so two
ceiling crossings exhaust a perfectly healthy row's entire allowance having never once genuinely
tried it, and only an operator noticing can give it back. On 2026-08-29 an operator did exactly
that, by hand, to all nine rows within the hour.

THE SYSTEM ALREADY COMPUTED THE RIGHT ANSWER AND THREW IT AWAY. `budget/halt.py:84`:

    CHARGES_ATTEMPT = {BUDGET_STOP: False, RATE_LIMIT: False, TASK_FAILURE: True, COMPLETED: False}

`Halt.line()` formats that into the reason string the runner stores on the row. 0431's `result`
column on live `brain` read, verbatim:

    ... -> block (self-clearing=False, charges attempt=False, incident 237)

while `brain.work_item.attempts` for 0431 read 1. Grepping every consumer of `charges_attempt`
across the tree found ten occurrences: the declaration, the dataclass field, the formatter, a JSON
key in `budget/cli.py`, and two tests asserting the RATE_LIMIT value. Nothing acted on it.
`test_the_declared_disposition_is_now_enforced` is the assertion that keeps it that way.

WHY THE REFUND IS SAFE HERE AND WOULD NOT BE SAFE FROM `reopen`, which is the whole design and the
one thing a later edit must not collapse. `reopen` resets attempts to 0 AND returns the row to
`inbox`: the next free terminal claims it and spends again, with no counter left to stop the loop.
That is the money retry loop, and `test-budget-wiring.sh` exists to catch it. The refund here is
safe because THE ROW STAYS BLOCKED. A blocked row cannot be claimed until a human raises the
ceiling or types `budget resume`, so refunds can never outnumber claims. `test_the_row_is_still
_parked_and_that_is_what_makes_the_refund_safe` is that argument as an assertion.

AND IT IS GATED ON FIRST-PARTY EVIDENCE, not on the caller's word, because `--unspent` on trust is
an opt-out from the retry limit that any agent could type on its own task.

Run: ENGINE_SCRATCH_DB=<db> BRAIN_PG_DB=<db> python3 engine/tests/test_budget_stop_is_unspent.py
"""

from __future__ import annotations

import io
import os
import re
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

import store                                                      # noqa: E402
import store.reads as store_reads                                 # noqa: E402
import budget.transitions                            # noqa: E402,F401  (registers the verbs)
from swarm_engine import cli, reads                               # noqa: E402
import swarm_engine.transitions                      # noqa: E402,F401  (registers the verbs)

SCRATCH = str(ROOT / "engine/bin/scratch-db.sh")
LANE = "engine"

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
    ok(f"{msg}  [{got}]") if got == want else bad(msg, f"wanted [{want}], got [{got}]")


def truth(msg, cond, detail=""):
    ok(msg) if cond else bad(msg, detail)


def reset():
    subprocess.run([SCRATCH, "truncate"], check=True, capture_output=True)


def a_claimed_task(agent="T-spend", title="a task that will be defunded mid-run"):
    """post + claim, so `attempts` is 1 the way a real dispatch leaves it. Returns the id."""
    tid = store.apply("post", lane=LANE, posted_by="operator", agent_claimable=True,
                      workdir="/tmp", title=title)["id"]
    got = store.apply("claim", agent=agent, lanes=[LANE])
    assert got and got["id"] == tid, f"setup: claim got {got and got['id']}"
    return tid


def a_hard_stop_against(tid, agent="T-spend", spend=55.942381, limit=50.0):
    """The incident the guard files BEFORE it signals, with 0461's own measured numbers."""
    return store.apply(
        "budget stop", scope_type="fleet", kind="hard_stop", detected_by="sweep",
        action_taken="fleet_stopped", by="budget-guard", work_item_id=tid, agent=agent,
        lane=LANE, spend_usd=spend, limit_usd=limit, percent_used=111.88,
        reason=f"budget hard_stop on fleet: spend {spend} against ceiling {limit}")


def meta(tid):
    t = reads.task(tid)
    return t["state"], t["attempts"], t["max_attempts"]


def thread_text(tid):
    return "\n".join((r["text"] or "") for r in store_reads.thread(tid))


# --------------------------------------------------------------------------- the fix itself

def test_a_budget_stop_parks_the_row_and_gives_the_attempt_back():
    """block --unspent: state blocked, attempts back to 0, and the reason still says why."""
    reset()
    tid = a_claimed_task()
    eq("the claim charged the attempt, which is where the charge comes from", meta(tid)[1], 1)
    inc = a_hard_stop_against(tid)

    reason = ("budget stop, not a task failure and not a subscription wall: this run was stopped "
              "for spend. budget_stop: budget hard_stop on fleet: spend 55.942381 against "
              "ceiling 50.0000 -> block (self-clearing=False, charges attempt=False, "
              f"incident {inc['id']})")
    r = store.apply("block", id=tid, reason=reason, agent="T-spend", unspent=True)

    state, attempts, cap = meta(tid)
    eq("state is BLOCKED, not requeued", state, "blocked")
    eq("the attempt was refunded", attempts, 0)
    eq("max_attempts is untouched: the fix is not a bigger ladder", cap, 2)
    truth("the verb reports the refund and names the incident it acted on",
          r.get("refunded") is True and r.get("incident") == inc["id"], f"got {r}")
    truth("the refund is on the thread with both numbers, so it is auditable",
          "attempt REFUNDED 1 -> 0" in thread_text(tid), thread_text(tid)[-400:])

    # THE ONE THING TO PRESERVE, named in the brief: an operator reading a blocked row must still
    # be told which of the three stops happened. The refund must not eat the reason.
    stored = reads.task(tid)["result"]
    truth("the block reason survives the refund, uncut and still three-way",
          "not a task failure" in stored and "not a subscription wall" in stored
          and "stopped for spend" in stored, stored[:200])


def test_the_row_is_still_parked_and_that_is_what_makes_the_refund_safe():
    """The loop guard is the BLOCKED STATE, not the counter. A refunded row is not claimable."""
    reset()
    tid = a_claimed_task()
    a_hard_stop_against(tid)
    store.apply("block", id=tid, reason="budget stop: stopped for spend", agent="T-spend",
                unspent=True)
    eq("precondition: the ladder is back to 0, so only the state can be stopping a claim",
       meta(tid)[1], 0)

    got = store.apply("claim", agent="T-next", lanes=[LANE])
    truth("a refunded budget stop is NOT handed to the next free terminal",
          got is None, f"claim returned {got and got['id']} -- the money retry loop is open")
    eq("and the row is still where the human left it", meta(tid)[0], "blocked")


def test_two_crossings_no_longer_exhaust_a_healthy_row():
    """The 0461 scenario end to end: cross, requeue, cross again, and still have the ladder."""
    reset()
    tid = a_claimed_task()
    for crossing in (1, 2):
        a_hard_stop_against(tid)
        store.apply("block", id=tid, reason=f"budget stop {crossing}: stopped for spend",
                    agent="T-spend", unspent=True)
        eq(f"after ceiling crossing {crossing}: attempts", meta(tid)[1], 0)
        # What the operator does with `budget resume` in hand: put the work back.
        store.apply("set", id=tid, key="state", value="inbox", agent="operator")
        store.apply("claim", agent="T-spend", lanes=[LANE])

    state, attempts, cap = meta(tid)
    eq("after TWO crossings the row is on its FIRST attempt, not out of tries", attempts, 1)
    truth("so a genuine failure still has a retry left, which is the point",
          attempts < cap, f"attempts {attempts} of {cap}")


# --------------------------------------------------------------------------- the evidence gate

def test_the_refund_is_refused_without_a_first_party_incident():
    """`--unspent` on trust is an opt-out from the retry limit. No incident, no refund."""
    reset()
    tid = a_claimed_task(title="a task with no budget stop anywhere near it")
    r = store.apply("block", id=tid, reason="I would simply like my attempt back",
                    agent="T-spend", unspent=True)

    state, attempts, _ = meta(tid)
    eq("the block still happened: a stopped run must never be left active", state, "blocked")
    eq("but the attempt stays charged", attempts, 1)
    truth("and the verb says so rather than reporting a refund it did not make",
          r.get("refunded") is False, f"got {r}")
    truth("the refusal is on the thread, naming what was missing",
          "REFUND REFUSED" in thread_text(tid), thread_text(tid)[-300:])


def test_an_incident_from_before_this_claim_does_not_pay_for_this_one():
    """The gate is time-bounded, or one old crossing refunds every future attempt on the row."""
    reset()
    tid = a_claimed_task()
    a_hard_stop_against(tid)
    store.apply("block", id=tid, reason="budget stop: stopped for spend", agent="T-spend",
                unspent=True)
    eq("precondition: crossing one was refunded", meta(tid)[1], 0)

    # Requeued and genuinely tried again, with no new crossing. The old incident is still there.
    store.apply("set", id=tid, key="state", value="inbox", agent="operator")
    store.apply("claim", agent="T-spend", lanes=[LANE])
    eq("precondition: the new dispatch charged its own attempt", meta(tid)[1], 1)

    r = store.apply("block", id=tid, reason="asking again on a stale incident", agent="T-spend",
                    unspent=True)
    truth("the stale incident does not pay for the new attempt", r.get("refunded") is False,
          f"got {r} -- one crossing would refund the row forever")
    eq("so the attempt stays charged", meta(tid)[1], 1)


def test_the_counter_cannot_go_negative():
    """GREATEST(attempts - 1, 0). A refund on a row at 0 is a no-op, not a debt."""
    reset()
    tid = store.apply("post", lane=LANE, posted_by="operator", agent_claimable=True,
                      workdir="/tmp", title="never claimed, so never charged")["id"]
    eq("precondition: an unclaimed row has spent nothing", meta(tid)[1], 0)
    a_hard_stop_against(tid)
    store.apply("block", id=tid, reason="budget stop on an unclaimed row", agent="operator",
                unspent=True)
    eq("attempts stays at 0 rather than going below it", meta(tid)[1], 0)


# --------------------------------------------------------------- nothing else changed

def test_an_ordinary_block_is_untouched():
    """The control. Without it, a `block` that refunded everything would pass every test above."""
    reset()
    tid = a_claimed_task(title="blocked on an operator question, not on money")
    a_hard_stop_against(tid)          # evidence present, and STILL no refund without the flag
    r = store.apply("block", id=tid, reason="BLOCKED ON OPERATOR QUESTION q0457", agent="T-spend")

    eq("an ordinary block still charges the attempt", meta(tid)[1], 1)
    truth("and reports no refund at all", "refunded" not in r, f"got {r}")
    truth("and writes no refund line to the thread",
          "REFUNDED" not in thread_text(tid), thread_text(tid)[-300:])


def test_fail_still_climbs_the_ladder():
    """`fail` is the ladder and must stay the ladder. The refund is not a way around it."""
    reset()
    tid = a_claimed_task(title="a task that genuinely does not work")
    r1 = store.apply("fail", id=tid, reason="attempt 1 genuinely failed", agent="T-spend")
    eq("fail 1 requeues", r1["state"], "inbox")
    store.apply("claim", agent="T-spend", lanes=[LANE])
    r2 = store.apply("fail", id=tid, reason="attempt 2 genuinely failed", agent="T-spend")
    eq("fail 2 blocks: a genuinely failing task still surfaces after 2", r2["state"], "blocked")
    truth("and it raised the operator question", bool(r2.get("question")), f"got {r2}")


def test_the_declared_disposition_is_now_enforced():
    """`budget/halt.py` said `charges attempt=False` and nothing acted on it. Now something does.

    Static, and deliberately so: the value is a fact about the module, and a test that reads it
    through a live run would pass just as well against a module that had dropped it.
    """
    from budget import halt
    eq("halt still declares a budget stop does not charge an attempt",
       halt.CHARGES_ATTEMPT[halt.BUDGET_STOP], False)
    eq("...and still prescribes `block`, not `reopen`, for the task itself",
       halt.DISPOSITION[halt.BUDGET_STOP], "block")

    # ON THE COMMAND LINE, NOT ON THE BRANCH TEXT, and the difference was measured rather than
    # anticipated. The first version of this assertion searched the whole `HALT_RC -eq 3` slice
    # for the string `--unspent`, and when the flag was deleted from the invocation as a mutation
    # check the assertion STILL PASSED -- because the twelve lines of comment above the call say
    # `--unspent` five times. A test that a comment can satisfy is a test of the comment.
    runner = (ROOT / "engine/bin/swarm-run").read_text()
    stop_branch = runner.split('if [ "$HALT_RC" -eq 3 ]; then', 1)[1].split("continue", 1)[0]
    code = [ln for ln in stop_branch.splitlines() if not ln.lstrip().startswith("#")]
    invocation = [ln for ln in code if '" block ' in ln]
    eq("the budget branch invokes `block` exactly once", len(invocation), 1)
    truth("the runner's budget branch passes --unspent, so the declaration is acted on",
          any("--unspent" in ln for ln in invocation), "\n".join(code)[:400])
    truth("...and it is `block` it passes it to, not `reopen` or `fail`",
          not any(re.search(r'" (reopen|fail) ', ln) for ln in code), "\n".join(code)[:400])

    tsrc = (ROOT / "engine/swarm_engine/transitions.py").read_text()
    truth("the refund is bounded at zero in SQL, not in Python",
          "GREATEST(attempts - 1, 0)" in tsrc, "")
    truth("and `reopen` still resets to 0 AND requeues, which is why it is a different verb",
          "attempts = 0, blocked_on = ''" in tsrc, "")


def test_the_cli_surfaces_both_outcomes():
    """A refund nobody can see is a refund nobody can audit. Both branches print."""
    reset()
    tid = a_claimed_task()
    a_hard_stop_against(tid)

    class A:
        pass
    args = A()
    args.id, args.reason, args.agent = tid, "budget stop: stopped for spend", "T-spend"
    args.unspent, args.force = True, False
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        cli.cmd_block(args)
    truth("a refund prints the before and after and the incident",
          "refunded" in out.getvalue() and "0/2" in out.getvalue(), repr(out.getvalue()))

    reset()
    tid2 = a_claimed_task()
    args2 = A()
    args2.id, args2.reason, args2.agent = tid2, "no incident here", "T-spend"
    args2.unspent, args2.force = True, False
    out2, err2 = io.StringIO(), io.StringIO()
    with redirect_stdout(out2), redirect_stderr(err2):
        cli.cmd_block(args2)
    truth("a REFUSED refund goes to stderr rather than passing silently",
          "NOT refunded" in err2.getvalue(), repr(err2.getvalue() or out2.getvalue()))
    truth("...and the block itself still reports success",
          "blocked" in out2.getvalue(), repr(out2.getvalue()))


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print("test_budget_stop_is_unspent.py  --  a run stopped for spend was never truly tried")
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
    if PASS + FAIL == 0:                                          # DENOMINATOR
        print("0 comparisons made. A verdict over an empty set is not a pass.")
        return 2
    print(f"{PASS} passed, {FAIL} failed  ({PASS + FAIL} comparisons)")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
