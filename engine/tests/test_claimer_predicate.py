#!/usr/bin/env python3
"""A verb may only act on a task the caller still holds. One test per path that broke.

The 2026-08-16T12:00Z incident, closed on every door rather than on the one D4 found. The
adversarial pass (`outputs/2026-08-16-D9-adversarial/FINDINGS.md`) measured all of these
succeeding against the ported engine; each test below is the same attack with the verdict
inverted, plus the negative cases that stop the guard being a wall around nothing.

The two halves:

  THE CLAIMER PREDICATE. `fail`, `reopen`, `block`, `done`, `cancel` and `set state` may not act
  on a task whose `claimed_by` is a different, live agent. `_hold` is the single implementation;
  `test_no_finisher_forgets_the_claimer_predicate` reads the source and fails if a verb is added
  that moves a task without going through it.

  THE REQUEUE DEFAULT. `answer` signed `requeue=True`, so its guard existed only for callers who
  passed the keyword by hand. It signs `requeue=False` now, and `queue default fire` -- the one
  caller in the tree that asked for the dangerous value by name, with no human in the loop -- no
  longer asks for it.

What is NOT gated, deliberately, so the omissions are stated rather than found later:

  `ask` parks a held task at `blocked` without an outcome and WITHOUT MAKING IT CLAIMABLE, so it
  cannot produce the double claim this predicate exists to prevent. `claim` takes an unheld row.
  `release` and `reap` carry their own `claimed_by =` predicate against the agent they are
  clearing, which is the holder by construction -- `reap` is the recovery path for a genuinely
  dead agent and weakening it would be the opposite of this fix, so
  `test_reap_still_requeues_a_genuinely_dead_agents_task` pins it.

Run: python3 engine/tests/test_claimer_predicate.py   (against the scratch database, never `brain`)
"""

from __future__ import annotations

import inspect
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT / "queue"))
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

import store                                                    # noqa: E402
from swarm_engine import reads, transitions                     # noqa: E402
from swarm_engine.transitions import VerbError                   # noqa: E402

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


def refuses(msg, fn, *, wanted="may only act on a task the caller still holds"):
    """The call must RAISE, not return a quiet no-op. Returns the exception text."""
    try:
        r = fn()
    except VerbError as e:
        if wanted in str(e):
            ok(msg)
        else:
            bad(msg, f"refused, but not for the right reason: {e}")
        return str(e)
    bad(msg, f"NOT refused: the verb returned {r!r}")
    return ""


def held_task(holder="T1", *, lane="cp", title="live work", pid=4242, **post_kw):
    """A task in the state the incident happened in: active, claimed, agent heartbeating."""
    post_kw.setdefault("agent_claimable", True)   # migration 26: these are fleet rows
    post_kw.setdefault("workdir", "/tmp")         # task 0100: a fleet row names its tree
    tid = store.apply("post", lane=lane, title=title, posted_by="commander", **post_kw)["id"]
    got = store.apply("claim", agent=holder, lanes=[lane])
    assert got and got["id"] == tid, f"setup: claim got {got and got['id']}, wanted {tid}"
    store.apply("heartbeat", agent=holder, status="working", task=tid, pid=pid)
    return tid


def state_of(tid):
    t = reads.task(tid)
    return (t["state"], t["claimed_by"], t["attempts"])


def _call_args(src: str, open_paren: int) -> str:
    """The text of one call's argument list, matched with balanced parentheses.

    `[^)]*` is wrong for any call carrying a nested call, and every interesting one here does.
    """
    depth, i = 0, open_paren
    while i < len(src):
        if src[i] == "(":
            depth += 1
        elif src[i] == ")":
            depth -= 1
            if depth == 0:
                return src[open_paren:i + 1]
        i += 1
    return src[open_paren:]


# ================================================================ the requeue default

def test_answer_library_shape_does_not_requeue_behind_a_live_engine():
    """`store.apply('answer', qid, text)` with NO requeue kwarg. FINDINGS scenario 8b."""
    reset()
    tid = held_task("T3")
    q = store.apply("ask", question="which branch?", agent="T3", task=tid,
                    default="hold; nothing is sent")
    store.apply("heartbeat", agent="T3", status="working", task=tid, pid=1111)

    r = store.apply("answer", qid=q["id"], text="branch B")      # <-- the whole test: no kwarg
    eq("answer: the library shape did NOT requeue", r["requeued"], False)
    eq("answer: and it names the live engine it refused behind", r["requeue_refused"], "T3")
    eq("answer: the answer still landed", reads.question(q["id"])["answer"], "branch B")
    eq("answer: the task stayed blocked", state_of(tid)[0], "blocked")

    second = store.apply("claim", agent="T1", lanes=["cp"])
    eq("answer: so a second agent cannot claim it", second, None)
    eq("answer: requeue=False is the SIGNATURE default, not just the CLI's",
       inspect.signature(transitions.answer).parameters["requeue"].default, False)


def test_queue_default_fire_does_not_requeue_behind_a_live_engine():
    """Silence at a checkpoint, no human in the loop. FINDINGS scenario 8c, the headline."""
    reset()
    tid = held_task("T3")
    q = store.apply("ask", question="which branch?", agent="T3", task=tid,
                    default="hold; nothing is sent")
    store.apply("heartbeat", agent="T3", status="working", task=tid, pid=1111)

    r = store.apply("queue default fire", qid=q["id"], checkpoint="19:00", by="default")
    eq("default fire: silence did NOT requeue behind the live engine", r["requeued"], False)
    eq("default fire: and it says whose engine", r["requeue_refused"], "T3")
    eq("default fire: the default still FIRED -- silence decided", r["default_text"],
       "hold; nothing is sent")
    eq("default fire: the answer landed", reads.question(q["id"])["answer"],
       "hold; nothing is sent")
    eq("default fire: the task stayed blocked, attempts NOT reset", state_of(tid),
       ("blocked", "T3", 1))
    eq("default fire: so a second agent cannot claim it",
       store.apply("claim", agent="T1", lanes=["cp"]), None)

    with store.read() as s:
        n = s.scalar("SELECT count(*) FROM brain.queue_default_event WHERE question_id = %s",
                     (q["id"],))
    eq("default fire: the accountability row is still written", n, 1)
    notes = [e["text"] for e in reads.thread(tid) if "DEFAULTED at 19:00" in e["text"]]
    truth("default fire: and the thread says it was not requeued",
          any("NOT requeued" in t for t in notes), str(notes))
    # Read the call site, not the file: the comment above it quotes the old value on purpose.
    # The argument list has to be matched with balanced parens, not `[^)]*` -- `tuple(planners)`
    # sits between `engine.answer(` and the keyword this test is about, so a lazy scan to the
    # first `)` truncates the call one argument BEFORE the argument under test and reports the
    # right answer for the wrong reason.
    from human_queue import transitions as hq
    call = [_call_args(src, m.end() - 1) for src in [inspect.getsource(hq.queue_default_fire)]
            for m in re.finditer(r"engine\.answer\(", src)]
    eq("default fire: it calls the ONE answer implementation, exactly once", len(call), 1)
    truth("default fire: and asks it for requeue=False",
          bool(call) and "requeue=False" in call[0], str(call))


# ================================================================ the claimer predicate

def test_fail_refuses_a_caller_that_is_not_the_holder():
    """The runner's reconciliation path. FINDINGS: T3 requeued T1's live 0019."""
    reset()
    tid = held_task("T1")
    before = state_of(tid)
    refuses("fail: T3 may not fail T1's live task",
            lambda: store.apply("fail", id=tid, reason="engine exited rc=1", agent="T3"))
    eq("fail: and the task is untouched", state_of(tid), before)


def test_reopen_refuses_a_caller_that_is_not_the_holder():
    """FINDINGS: T3 reopened T1's live 0020 and its attempts went 1 -> 0."""
    reset()
    tid = held_task("T1")
    msg = refuses("reopen: T3 may not reopen T1's live task",
                  lambda: store.apply("reopen", id=tid, reason="subscription rate limit",
                                      agent="T3"))
    eq("reopen: the claim, the state AND the attempt ladder survive", state_of(tid),
       ("active", "T1", 1))
    # `swarm reopen` spells the flag `--from`; the other gated verbs spell it `--agent`. An error
    # message that tells a runner to pass a flag the verb does not have is a wrong message.
    truth("reopen: and the refusal names `--from`, which is the flag `swarm reopen` takes",
          "--from T1" in msg and "--agent" not in msg, msg)
    for verb, kw in (("fail", {"reason": "r"}), ("done", {"summary": "s"})):
        other = refuses(f"reopen: (control) `{verb}` still names --agent",
                        lambda v=verb, k=kw: store.apply(v, id=tid, agent="T3", **k))
        truth(f"reopen: `{verb}`'s refusal names --agent, not --from",
              "--agent T1" in other and "--from" not in other, other)


def test_block_refuses_a_caller_that_is_not_the_holder():
    """The runner's budget-stop path. FINDINGS: T3 blocked T1's live 0021."""
    reset()
    tid = held_task("T1")
    refuses("block: T3 may not block T1's live task",
            lambda: store.apply("block", id=tid, reason="budget stop", agent="T3"))
    eq("block: T1's run is still running", state_of(tid), ("active", "T1", 1))
    eq("block: and no result was written over it", reads.task(tid)["result"], "")


def test_done_refuses_a_caller_that_is_not_the_holder():
    """FINDINGS: T3 closed T1's 0022 and the thread carried T3's forged report."""
    reset()
    tid = held_task("T1")
    refuses("done: T3 may not report T1's live task finished",
            lambda: store.apply("done", id=tid, summary="T3 says T1's task is finished",
                                agent="T3"))
    eq("done: the task is still T1's, still running", state_of(tid), ("active", "T1", 1))
    truth("done: and no forged report reached the thread",
          not [e for e in reads.thread(tid) if e["kind"] == "done"],
          str(reads.thread(tid)))


def test_cancel_refuses_a_caller_that_is_not_the_holder():
    """FINDINGS: T3 cancelled T1's live 0023, ending work T1 was doing."""
    reset()
    tid = held_task("T1")
    refuses("cancel: T3 may not cancel T1's live task",
            lambda: store.apply("cancel", id=tid, reason="cancelled", agent="T3"))
    eq("cancel: T1's work survives", state_of(tid), ("active", "T1", 1))


def test_set_state_refuses_a_caller_that_is_not_the_holder():
    """The sixth door: `set state inbox` makes a live task claimable with no verb in the trail."""
    reset()
    tid = held_task("T1")
    refuses("set: T3 may not `set state` on T1's live task",
            lambda: store.apply("set", id=tid, key="state", value="inbox", agent="T3"))
    eq("set: the task is untouched", state_of(tid), ("active", "T1", 1))
    store.apply("set", id=tid, key="priority", value="1", agent="T3")
    eq("set: but ordinary metadata on a running task is NOT gated", reads.task(tid)["priority"], 1)


def test_an_anonymous_caller_is_refused_on_a_held_task():
    """`swarm done ID --summary ...` with no --agent. The CLI defaults `--agent` to ''.

    If `''` passed the predicate, the predicate would be opt-in: omit one flag and it is gone.
    The refusal has its OWN message, because "you did not say who you are" and "you are not the
    holder" need different repairs.
    """
    reset()
    tid = held_task("T1", lane="cpa")
    msg = refuses("anonymous: an unnamed caller may not `done` T1's live task",
                  lambda: store.apply("done", id=tid, summary="no agent given"),
                  wanted="was called by nobody: no --agent was given")
    truth("anonymous: and the message names the flag AND the holder to pass",
          "--agent T1" in msg, msg)
    eq("anonymous: the task is untouched", state_of(tid), ("active", "T1", 1))
    truth("anonymous: and no report was filed against T1",
          not [e for e in reads.thread(tid) if e["kind"] == "done"], str(reads.thread(tid)))
    for verb, kw in (("fail", {"reason": "r"}), ("block", {"reason": "r"}),
                     ("reopen", {"reason": "r"})):
        # `was given` and not `no --agent was given`: `reopen` names `--from`, and pinning the
        # flag per verb is `test_reopen_refuses_a_caller_that_is_not_the_holder`'s job.
        refuses(f"anonymous: nor `{verb}` it", lambda v=verb, k=kw: store.apply(v, id=tid, **k),
                wanted="was called by nobody: no --")
    # `cancel` and `set` sign `agent="operator"` in their own signatures, so they are never
    # anonymous -- they arrive as a NAMED non-holder and take the mismatch refusal instead.
    # Both doors are shut; this pins which one, so a later change to either default is visible.
    refuses("anonymous: `cancel` defaults to `operator`, so it takes the MISMATCH refusal",
            lambda: store.apply("cancel", id=tid, reason="r"))
    eq("anonymous: still T1's, after all five", state_of(tid), ("active", "T1", 1))

    # The negative: anonymous is fine on a task nobody holds. This is the operator at a
    # terminal closing an unclaimed item, which must not need a name it does not have.
    free = store.apply("post", lane="cpa", title="nobody holds this", posted_by="commander",
                       agent_claimable=True, workdir="/tmp")["id"]
    store.apply("block", id=free, reason="waiting on a human")
    eq("anonymous: but an UNHELD task is not gated", state_of(free)[0], "blocked")


def test_mcp_finish_work_refuses_a_caller_that_is_not_the_holder():
    """The shipped surface. FINDINGS a8f_mcp.py: finish_work returned {'requeued': True}."""
    reset()
    import mcp.tools as tools
    tid = held_task("T1")
    refuses("mcp: finish_work(agent='T3') may not fail T1's live task",
            lambda: tools.finish_work(task=tid, outcome="failed",
                                      summary="T3 says T1's task failed", agent="T3"))
    eq("mcp: the task is untouched", state_of(tid), ("active", "T1", 1))
    refuses("mcp: nor report it done",
            lambda: tools.finish_work(task=tid, outcome="done", summary="T3 says so", agent="T3"))
    refuses("mcp: nor block it",
            lambda: tools.finish_work(task=tid, outcome="blocked", summary="T3 says so",
                                      agent="T3"))
    eq("mcp: still T1's, after all three", state_of(tid), ("active", "T1", 1))
    truth("mcp: and finish_work exposes no force parameter to an agent",
          "force" not in inspect.signature(tools.finish_work).parameters,
          str(inspect.signature(tools.finish_work)))


# ================================================================ the negative cases
#
# A guard that refuses everything is not a guard, it is an outage. These are the calls that must
# still work, and they are the reason the predicate tests `state = 'active'` and a non-empty
# `claimed_by` rather than just comparing two names.

def test_the_holder_itself_is_never_refused():
    """Every guarded verb, called by the agent that actually holds the task."""
    reset()
    for verb, kw in (("fail", {"reason": "a genuine failure"}),
                     ("done", {"summary": "finished it"}),
                     ("block", {"reason": "waiting on a human"}),
                     ("cancel", {"reason": "no longer needed"}),
                     ("reopen", {"reason": "sending my own task back"})):
        tid = held_task("T1", lane=f"cph{verb}")
        try:
            store.apply(verb, id=tid, agent="T1", **kw)
            ok(f"holder: T1 may {verb} the task T1 holds")
        except VerbError as e:
            bad(f"holder: T1 may {verb} the task T1 holds", str(e))
    tid = held_task("T1", lane="cphset")
    try:
        store.apply("set", id=tid, key="state", value="inbox", agent="T1")
        ok("holder: T1 may `set state` on the task T1 holds")
    except VerbError as e:
        bad("holder: T1 may `set state` on the task T1 holds", str(e))


def test_a_task_nobody_holds_is_not_gated():
    """Finished, unclaimed and released work stays the operator's to move."""
    reset()
    tid = held_task("T1", lane="cpu")
    store.apply("done", id=tid, summary="T1 finished it", agent="T1")
    store.apply("reopen", id=tid, reason="not what I asked for", agent="operator")
    eq("unheld: the operator may reopen FINISHED work, unspent", state_of(tid), ("inbox", "", 0))

    fresh = store.apply("post", lane="cpu", title="never claimed", posted_by="commander",
                        agent_claimable=True, workdir="/tmp")["id"]
    store.apply("block", id=fresh, reason="waiting on a human", agent="operator")
    eq("unheld: and may block a task nobody ever claimed", state_of(fresh)[0], "blocked")

    tid2 = held_task("T1", lane="cpu2")
    store.apply("release", id=tid2, agent="T1", reason="runner stopped")
    store.apply("cancel", id=tid2, reason="no longer needed", agent="operator")
    eq("unheld: and may cancel a task its holder released", state_of(tid2)[0], "cancelled")


def test_force_is_the_signed_logged_override():
    """The operator override: it works, it must be signed, and it lands on the thread."""
    reset()
    tid = held_task("T1")
    refuses("force: an UNSIGNED override is refused (it would be filed against T1)",
            lambda: store.apply("cancel", id=tid, reason="stop it", agent="", force=True),
            wanted="needs --agent")

    store.apply("cancel", id=tid, reason="the operator is stopping this run", agent="operator",
                force=True)
    eq("force: a signed override acts", state_of(tid)[0], "cancelled")
    notes = [e for e in reads.thread(tid)
             if "OVERRIDE" in (e["text"] or "") and e["kind"] == "note"]
    truth("force: and it is on the thread, naming both agents",
          bool(notes) and "operator" in notes[0]["text"] and "T1" in notes[0]["text"],
          str([n["text"] for n in notes]))
    eq("force: signed by the overrider, not by the agent it displaced",
       notes[0]["from_agent"] if notes else None, "operator")


def test_reap_still_requeues_a_genuinely_dead_agents_task():
    """THE RULE THIS FIX MAY NOT BREAK. A dead agent's task must still come back."""
    reset()
    tid = held_task("T1", lane="cpr")

    fresh = store.apply("reap", stale_seconds=900)
    eq("reap: a heartbeating agent's task is NOT reaped", fresh["found"], [])

    shown = store.apply("reap", stale_seconds=0)
    eq("reap: a silent agent's task is found", [r["id"] for r in shown["found"]], [tid])
    eq("reap: and showing it does not move it", state_of(tid), ("active", "T1", 1))

    acted = store.apply("reap", stale_seconds=0, act=True)
    eq("reap: --yes requeues it", acted["requeued"], [tid])
    eq("reap: the claim is cleared and the task is claimable again", state_of(tid)[:2],
       ("inbox", ""))
    got = store.apply("claim", agent="T2", lanes=["cpr"])
    eq("reap: a live agent picks up the dead one's work", got["id"], tid)
    with store.read() as s:
        dead = s.one("SELECT status, work_item_id FROM brain.agent WHERE name = 'T1'")
    eq("reap: and the dead agent is marked dead", dead["status"], "dead")
    truth("reap: reap carries its own claimer predicate, it does not borrow _hold",
          "_hold(" not in inspect.getsource(transitions.reap)
          and "claimed_by = %s" in inspect.getsource(transitions.reap),
          "reap must stay independent of the finishers' guard")


# ================================================================ the structural test

# Every transition that moves a work item's state, and why it is or is not gated. A verb added
# later that moves state lands in neither column and fails this test, which is the point: the
# next lane cannot forget the predicate by not knowing about it.
EXEMPT = {
    "claim": "takes an inbox row nobody holds; FOR UPDATE SKIP LOCKED is its guard",
    "release": "carries its own `claimed_by = %s`, which IS this predicate, verb-local",
    "reap": "carries its own `claimed_by = %s` against the agent it just found dead. Gating it "
            "on the caller would break the recovery path this fix must not weaken",
    "ask": "parks a held task at `blocked` with no outcome and does NOT make it claimable, so "
           "it cannot produce the double claim the predicate exists to prevent",
    "answer": "the requeue half is guarded by the live-heartbeat check and requeue=False, which "
              "is the other half of this same fix",
}


def _reaches_hold(fn: str, bodies: dict, seen=()) -> bool:
    """Does this function reach `_hold`, directly or through a helper in the same module?

    TRANSITIVE ON PURPOSE, AND NOT BY BLESSING A NAME. `done`, `block` and `cancel` are one line
    each: they delegate to `_finish`, which calls `_hold` as its first statement. A check that
    only looked for a literal `_hold(` in the verb's own body would report those three ungated
    while the behavioural tests above prove they refuse -- and the way to make such a check pass
    is to hardcode `_finish` as an approved callee, which stops testing anything the day `_finish`
    drops the call. So this walks the call graph and re-derives it: `_finish` counts because its
    SOURCE contains `_hold(`, and it stops counting the moment that stops being true.
    """
    body = bodies.get(fn, "")
    if "_hold(" in body:
        return True
    for callee in set(re.findall(r"\b(_\w+)\(", body)) - set(seen) - {fn}:
        if callee in bodies and _reaches_hold(callee, bodies, (*seen, fn)):
            return True
    return False


def test_no_finisher_forgets_the_claimer_predicate():
    """Read the source. Any verb that moves a work item must be gated or listed as exempt."""
    src = Path(inspect.getsourcefile(transitions)).read_text(encoding="utf-8")
    bodies = {}
    for name, obj in vars(transitions).items():
        if not callable(obj) or getattr(obj, "__module__", "") != transitions.__name__:
            continue
        try:
            bodies[obj.__name__] = inspect.getsource(obj)
        except (OSError, TypeError):
            continue

    verbs = dict(re.findall(r'@store\.transition\("([^"]+)"\)\s*\ndef\s+(\w+)', src))
    truth("structure: the transition registry was parsed out of the source", len(verbs) > 20,
          f"found {len(verbs)} verbs")

    movers, ungated = [], []
    for verb, fn in verbs.items():
        body = bodies.get(fn, "")
        # A verb "moves" a task if it writes work_item.state, directly or through _finish.
        writes = re.search(r"UPDATE brain\.work_item[\s\S]{0,400}?\bstate\s*=", body) \
            or "_finish(" in body or "SET {key}" in body
        if not writes:
            continue
        movers.append(verb)
        if verb not in EXEMPT and not _reaches_hold(fn, bodies):
            ungated.append(verb)

    truth("structure: the movers were found", len(movers) >= 8, str(sorted(movers)))
    eq("structure: every verb that moves a task is gated by _hold or listed exempt WITH A REASON",
       sorted(ungated), [])
    for verb in ("done", "block", "fail", "reopen", "cancel", "set"):
        truth(f"structure: `{verb}` reaches the one predicate",
              _reaches_hold(verbs.get(verb, ""), bodies),
              f"{verb} does not reach _hold, directly or through a helper")
    truth("structure: and the transitive walk is not blessing a name -- `_finish` counts only "
          "because its own source calls the predicate",
          "_hold(" in bodies.get("_finish", ""),
          "_finish stopped calling _hold, so done/block/cancel are now ungated")
    eq("structure: there is exactly ONE implementation of the predicate",
       len(re.findall(r"^def _hold\(", src, re.M)), 1)
    truth("structure: and it refuses rather than returning a no-op",
          "raise VerbError" in inspect.getsource(transitions._hold),
          "a silent no-op tells the caller it succeeded")


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print("test_claimer_predicate.py  --  a verb may only act on a task the caller still holds")
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
