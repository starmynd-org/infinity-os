#!/usr/bin/env python3
"""One test per contract rule, named for the rule it defends.

D00 freezes eleven rules. Nine are numbered in the original list and two more were added with
the narrow waist and the text posture. Every one gets a test here, and the test asserts the rule
rather than the implementation: a rewrite that keeps the rule should keep these green.

EVERY FINISHER CALL HERE NAMES ITS AGENT (task 0144). Eight of these fixtures used to claim as
`A1` or `T1` and then call `done` or `fail` with no `agent=`, which the store read as an
anonymous caller acting on a task somebody holds -- the 2026-08-16 incident's exact shape, and
`_hold` in `swarm_engine.transitions` refuses it now. Nothing about the eleven rules changed and
no assertion here was relaxed: the fixtures were saying less than the production callers they
stand in for. `bin/swarm-run` has always passed `--agent`, and the MCP tools have always refused
an unattributed call outright, so naming the holder makes these fixtures MORE faithful, not less
strict. `test_claimer_predicate.py` is where the refusal itself is pinned.

Run: python3 engine/tests/test_contract.py     (against the scratch database, never `brain`)
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
os.environ.pop("SWARM_PARENT_TASK", None)          # the live runner exports this; tests post roots

import store                                        # noqa: E402
from swarm_engine import accept, reads, transitions  # noqa: E402,F401
from swarm_engine.signals import lane_match, signal_level  # noqa: E402

SWARM = str(ROOT / "engine/bin/swarm")
SCRATCH = str(ROOT / "engine/bin/scratch-db.sh")

PASS, FAIL = 0, 0
_CURRENT = ""


def rule(n):
    def deco(fn):
        fn._rule = n
        return fn
    return deco


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
    if got == want:
        ok(msg)
    else:
        bad(msg, f"wanted [{want}], got [{got}]")


def truth(msg, cond, detail=""):
    ok(msg) if cond else bad(msg, detail)


def reset():
    subprocess.run([SCRATCH, "truncate"], check=True, capture_output=True)


def sh(*args, **kw):
    return subprocess.run([SWARM, *args], capture_output=True, text=True,
                          env={**os.environ}, **kw)


def post(**kw):
    kw.setdefault("lane", "t")
    kw.setdefault("title", "a task")
    kw.setdefault("agent_claimable", True)   # migration 26: these are fleet rows
    kw.setdefault("workdir", "/tmp")         # task 0100: a fleet row names its tree
    return store.apply("post", **kw)["id"]


# ------------------------------------------------------------------ rule 1

@rule(1)
def test_state_lives_in_the_store_not_in_an_agents_head():
    """A second process, sharing nothing with the first, sees the same state."""
    reset()
    tid = post(title="state is external")
    store.apply("claim", agent="A1", lanes=["*"])
    r = sh("state", tid)
    eq("rule 1: a separate process reads the claimed state", r.stdout.strip(), "active")
    r = sh("show", tid, "--json")
    truth("rule 1: and the owner, with no shared memory between them", '"claimed_by": "A1"' in r.stdout,
          r.stdout[:200])


# ------------------------------------------------------------------ rule 2

@rule(2)
def test_claiming_is_atomic_and_the_first_claimer_wins():
    """The full property is the gate (`test-claim-race.sh`). This is its unit-level twin."""
    reset()
    tid = post(title="only one winner")
    first = store.apply("claim", agent="A1", lanes=["*"])
    second = store.apply("claim", agent="A2", lanes=["*"])
    eq("rule 2: the first claimer gets the task", first["id"], tid)
    eq("rule 2: the second claimer gets nothing, not a phantom", second, None)
    eq("rule 2: the store records exactly one owner", reads.task(tid)["claimed_by"], "A1")
    eq("rule 2: and exactly one attempt was spent", reads.task(tid)["attempts"], 1)


# ------------------------------------------------------------------ rule 3

@rule(3)
def test_done_is_not_acceptance_and_reopen_is_the_rejection_verb():
    reset()
    tid = post(title="reported, not accepted", signals={"reversibility": "reversible"})
    store.apply("claim", agent="A1", lanes=["*"])
    store.apply("done", id=tid, summary="I say it is finished", agent="A1")
    t = reads.task(tid)
    eq("rule 3: done sets state", t["state"], "done")
    eq("rule 3: done does NOT set accepted_at", t["accepted_at"], None)
    eq("rule 3: nor accepted_by", t["accepted_by"], None)

    store.apply("reopen", id=tid, reason="not what I asked for", agent="operator")
    t = reads.task(tid)
    eq("rule 3: reopen is the rejection verb, back to inbox", t["state"], "inbox")
    eq("rule 3: and it returns the task unspent", t["attempts"], 0)

    # The narrowing exists, is measured, and is OFF.
    eq("rule 3: auto-accept ships DISABLED", accept.enabled(), False)
    store.apply("claim", agent="A1", lanes=["*"])
    store.apply("done", id=tid, summary="again", agent="A1")
    v = accept.would_accept(tid)
    truth("rule 3: the rule still evaluates while disabled (something to measure)",
          v["eligible"] is True, str(v))
    eq("rule 3: and having evaluated, it still did not accept", reads.task(tid)["accepted_at"], None)
    notes = [e["text"] for e in reads.thread(tid) if "auto-accept measurement" in e["text"]]
    truth("rule 3: the measurement is recorded on the thread", bool(notes), str(notes))


# ------------------------------------------------------------------ rule 4

@rule(4)
def test_no_agent_self_approves_canon():
    reset()
    tid = post(title="touches canon", canon_touching="true",
               signals={"reversibility": "reversible"})
    store.apply("claim", agent="A1", lanes=["*"])
    store.apply("done", id=tid, summary="canon edited", agent="A1")
    v = accept.would_accept(tid)
    eq("rule 4: a canon-touching item is never auto-accept eligible", v["eligible"], False)
    truth("rule 4: and it says why", "canon_touching" in v["reasons"], str(v["reasons"]))

    # The view filters it. The transition refuses it as well, because a gate that exists in one
    # place is a gate one bug away from being absent.
    try:
        store.apply("accept work", id=tid, by="some-agent", auto=True, as_operator=True)
        bad("rule 4: the transition refuses an automatic accept on canon")
    except RuntimeError as e:
        truth("rule 4: the transition refuses an automatic accept on canon",
              "not eligible" in str(e), str(e))
    eq("rule 4: so it is still unaccepted", reads.task(tid)["accepted_at"], None)

    # A human may accept it. Narrowing WHO accepts never narrows WHETHER it was recorded.
    store.apply("accept work", id=tid, by="operator", auto=False, as_operator=True)
    truth("rule 4: a human decision IS recorded", reads.task(tid)["accepted_at"] is not None)
    eq("rule 4: and names the decider", reads.task(tid)["accepted_by"], "operator")


# ------------------------------------------------------------------ rule 5

@rule(5)
def test_a_planner_never_claims():
    """Proven by test, not by code reading, and at both of the two gates."""
    reset()
    post(title="work a planner must not take")

    # Gate 1: the transition. An empty lane list matches nothing.
    got = store.apply("claim", agent="admiral", lanes=[])
    eq("rule 5: a planner's empty lane list claims nothing", got, None)
    eq("rule 5: the task is still in inbox", reads.counts()["inbox"], 1)
    eq("rule 5: lane_match('anything', []) is False", lane_match("anything", []), False)

    # Gate 2: the CLI resolves a planner's lanes to [] whatever config or --lane says. An
    # admiral that could claim would quietly execute the work it had just decomposed.
    cfg = Path(os.environ["ENGINE_CONFIG"])
    cfg.write_text('{"fleet":"test","agents":['
                   '{"name":"admiral","role":"admiral","lanes":["*"],"plans":["*"]},'
                   '{"name":"T1","role":"terminal","lanes":["*"]}]}', encoding="utf-8")
    r = sh("claim", "--agent", "admiral", "--lane", "t")
    eq("rule 5: the CLI refuses a planner even with lanes:['*'] AND --lane t", r.returncode, 2)
    eq("rule 5: nothing was claimed", reads.counts()["inbox"], 1)
    r = sh("claim", "--agent", "T1")
    eq("rule 5: a terminal on the same board claims fine", r.returncode, 0)
    eq("rule 5: so the queue was claimable all along", reads.counts()["active"], 1)


# ------------------------------------------------------------------ rule 6

@rule(6)
def test_a_subscription_refusal_is_not_a_task_failure():
    """The rule that cost 18 tasks and 11 idle hours on 2026-08-14.

    The runner's half is `test-ratelimit.sh`. This is the bus's half: the verb a refusal calls
    must return the task UNSPENT, and the verb a real failure calls must spend an attempt.
    """
    reset()
    tid = post(title="refused before it started", max_attempts=2)
    store.apply("claim", agent="T1", lanes=["*"])
    eq("rule 6: the claim spent attempt 1", reads.task(tid)["attempts"], 1)

    store.apply("reopen", id=tid, reason="subscription rate limit, not a task failure",
                agent="T1")
    t = reads.task(tid)
    eq("rule 6: a refusal requeues the task", t["state"], "inbox")
    eq("rule 6: UNSPENT -- attempts reset to 0", t["attempts"], 0)
    eq("rule 6: and the claim is cleared", t["claimed_by"], "")

    # The contrast, which is what makes the rule mean something.
    store.apply("claim", agent="T1", lanes=["*"])
    store.apply("fail", id=tid, reason="a genuine failure", agent="T1")
    eq("rule 6: a REAL failure spends the attempt", reads.task(tid)["attempts"], 1)
    store.apply("claim", agent="T1", lanes=["*"])
    r = store.apply("fail", id=tid, reason="failed again", agent="T1")
    eq("rule 6: and a second one blocks the task", reads.task(tid)["state"], "blocked")
    truth("rule 6: raising an operator question", r["question"].startswith("q"), str(r))


# ------------------------------------------------------------------ rule 7

@rule(7)
def test_workdir_is_the_boundary_the_worker_may_not_leave():
    reset()
    tid = post(title="scoped", workdir="/tmp/some/where")
    got = store.apply("claim", agent="T1", lanes=["*"])
    eq("rule 7: the claim hands the worker its workdir", got["workdir"], "/tmp/some/where")
    r = sh("claim", "--agent", "T2", "--shell")
    # (nothing left to claim; the assertion below is on the first claim's shell export)
    reset()
    post(title="scoped", workdir="/tmp/some/where")
    r = sh("claim", "--agent", "T1", "--shell")
    truth("rule 7: and exports it to the runner verbatim",
          "TASK_WORKDIR='/tmp/some/where'" in r.stdout, r.stdout)
    # The runner refuses rather than relocating. Asserted against the runner in test-runner.sh;
    # here we assert the bus carries the value that lets it refuse.
    runner = (ROOT / "engine/bin/swarm-run").read_text(encoding="utf-8")
    truth("rule 7: the runner fails a missing workdir rather than relocating",
          "does not exist on this host, refusing" in runner)


# ------------------------------------------------------------------ rule 8

@rule(8)
def test_hard_flags_inherit_by_or_up_the_whole_chain():
    """EF-7. The gate holds exactly one hop without this, and two compliant hops launder a flag."""
    reset()
    gp = post(title="grandparent", external="true")
    p = post(title="parent", parent=gp)
    c = post(title="child", parent=p)
    eq("rule 8: the child inherits external from its GRANDparent (2 hops)",
       reads.signals_of(c)["external"], True)
    eq("rule 8: and names where it came from", reads.signals_of(c)["inherited_from"], [p, gp])

    # Retroactive: raising a flag on an ancestor raises it on every descendant, which is why
    # inheritance is resolved on read rather than stamped at post time.
    reset()
    gp = post(title="grandparent")
    p = post(title="parent", parent=gp)
    c = post(title="child", parent=p)
    eq("rule 8: clean chain reads false", reads.signals_of(c)["canon_touching"], False)
    store.apply("set", id=gp, key="canon_touching", value="true")
    eq("rule 8: raising it on the grandparent raises it on the child, retroactively",
       reads.signals_of(c)["canon_touching"], True)

    # A child may raise, never lower.
    reset()
    p = post(title="flagged parent", external="true")
    c = post(title="child that tries to clear it", parent=p, external="false")
    eq("rule 8: a child cannot LOWER an inherited flag", reads.signals_of(c)["external"], True)
    notes = [e["text"] for e in reads.thread(c) if "can only raise it" in e["text"]]
    truth("rule 8: and the refusal is written to the thread, not swallowed", bool(notes))
    parent_notes = [e["text"] for e in reads.thread(p) if "can only raise it" in e["text"]]
    truth("rule 8: on the parent's thread too, so the trail does not go cold", bool(parent_notes))

    # A parent that does not resolve would drop the inheritance silently.
    try:
        post(title="orphan", parent="9999")
        bad("rule 8: an unresolvable parent is refused")
    except Exception as e:                                        # noqa: BLE001
        truth("rule 8: an unresolvable parent is refused rather than silently unparented",
              "no such parent" in str(e), str(e))


# ------------------------------------------------------------------ rule 9

@rule(9)
def test_a_hard_flag_may_wake_a_listener_to_prepare_never_to_act():
    reset()
    tid = post(title="sends an email", external="true")
    sig = reads.signals_of(tid)
    truth("rule 9: an external task is gated", sig["gated"])
    eq("rule 9: and says which flag", sig["gate_reasons"], ["external"])
    gates = [e["text"] for e in reads.thread(tid) if e["text"].startswith("GATE")]
    truth("rule 9: the gate is on the thread at post time, so the trail says so even if "
          "nobody was watching the feed", bool(gates), str(gates))

    got = store.apply("claim", agent="T1", lanes=["*"])
    eq("rule 9: the gate does not STOP the work being claimed", got["id"], tid)
    eq("rule 9: it guarantees the claim surfaces", got["gated"], True)
    claim_gates = [e["text"] for e in reads.thread(tid)
                   if "claimed work that surfaces" in e["text"]]
    truth("rule 9: and the claim itself is marked on the thread", bool(claim_gates))

    # The acting half: a gated item cannot be auto-accepted, so nothing sends, spends or
    # publishes on an automatic path.
    store.apply("done", id=tid, summary="drafted, not sent", agent="T1")
    eq("rule 9: a gated item is never eligible for the automatic path",
       accept.would_accept(tid)["eligible"], False)


# ------------------------------------------------------------------ rule 10

@rule(10)
def test_one_verb_one_transaction():
    """A killed `done` leaves NO partial state. Proven by killing one mid-transaction.

    `done` is three writes: the row, the thread event and the run row. Under a file rename that
    was one atomic operation; three writes are not, so this is the rule that has to be
    demonstrated rather than argued.
    """
    reset()
    tid = post(title="killed mid-done")
    store.apply("claim", agent="T1", lanes=["*"])

    # A child process that starts `done`, gets far enough to have written the row update and the
    # thread event inside its transaction, and is then killed before the commit.
    child = ROOT / "engine/tests/_kill_mid_done.py"
    proc = subprocess.Popen([sys.executable, str(child), tid], env={**os.environ},
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    out, err = "", ""
    try:
        out, err = proc.communicate(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
    truth("rule 10: the child was killed mid-transaction",
          proc.returncode not in (0,), f"rc={proc.returncode} out={out} err={err[:200]}")

    t = reads.task(tid)
    eq("rule 10: the row change did NOT land", t["state"], "active")
    eq("rule 10: the result did NOT land", t["result"], "")
    eq("rule 10: finished_at did NOT land", t["finished_at"], None)
    dones = [e for e in reads.thread(tid) if e["kind"] == "done"]
    eq("rule 10: and no orphan thread event was left behind", len(dones), 0)

    # Then the same verb, uninterrupted, lands all three together.
    store.apply("done", id=tid, summary="this one commits", agent="T1")
    t = reads.task(tid)
    eq("rule 10: a completed done lands the row", t["state"], "done")
    eq("rule 10: and the thread event, in the same transaction",
       len([e for e in reads.thread(tid) if e["kind"] == "done"]), 1)

    # The registry is the audit surface: two lanes cannot each define `done`.
    try:
        @store.transition("done")
        def _second_done(ctx):
            pass
        bad("rule 10: a second registration of `done` is refused at import time")
    except store.DuplicateTransition as e:
        truth("rule 10: a second registration of `done` is refused at import time",
              "already registered" in str(e))


# ------------------------------------------------------------------ rule 11

@rule(11)
def test_store_full_text_truncate_only_renderings():
    """The 2000-char result cut and the 3000-char thread cut do not port."""
    reset()
    tid = post(title="a long report")
    store.apply("claim", agent="T1", lanes=["*"])
    big = ("A" * 1500) + " MIDDLE-MARKER " + ("B" * 1500) + " THE-VERY-END"
    eq("rule 11: the test text is over 3000 chars", len(big) > 3000, True)
    store.apply("done", id=tid, summary=big, agent="T1")

    stored = reads.task(tid)["result"]
    eq("rule 11: the result is stored byte for byte", stored, big)
    eq("rule 11: including the tail the old cut destroyed", stored.endswith("THE-VERY-END"), True)
    th = [e["text"] for e in reads.thread(tid) if e["kind"] == "done"][0]
    eq("rule 11: the thread event is stored whole too", th, big)

    # The rendering shortens, and SAYS it shortened. The original cut at 2000 with no marker, so
    # the text read as a finished sentence and, as a work order, read as a complete one.
    r = sh("show", tid)
    truth("rule 11: the default view shortens", len(r.stdout) < len(big) + 2000, str(len(r.stdout)))
    truth("rule 11: and says so, loudly", "This view is SHORTENED" in r.stdout, r.stdout[-400:])
    truth("rule 11: naming the command that prints it whole", "--full" in r.stdout)

    r = sh("show", tid, "--full")
    truth("rule 11: --full prints every byte", "THE-VERY-END" in r.stdout and "MIDDLE-MARKER" in r.stdout)
    r = sh("show", tid, "--json")
    truth("rule 11: and --json never truncates", big in r.stdout)


# ------------------------------------------------------------------ the folding parity check

def test_signal_parity_word_vocabulary():
    """signals.py and brain.signal_level() must agree on the word vocabulary.

    They are two implementations of one fold: Python explains the score, SQL orders the queue
    inside the locking statement. Drift means a task is explained one way and dispatched
    another, which surfaces as an unreproducible ordering bug.
    """
    from swarm_engine.signals import CONSERVATIVE, LEVELLED_SIGNALS, SIGNAL_ALIASES
    cases = []
    for f in LEVELLED_SIGNALS:
        for v in ("low", "medium", "high", "", "  ", "garbage", "LOW", "High"):
            cases.append((f, v))
        for v in SIGNAL_ALIASES[f]:
            cases.append((f, v))
    mismatch = []
    with store.read("runtime") as s:
        for f, v in cases:
            sql = s.scalar("SELECT brain.signal_level(%s, %s)", (f, v))
            py = signal_level(f, v) or CONSERVATIVE[f]
            if sql != py:
                mismatch.append(f"{f}={v!r}: sql {sql!r} vs python {py!r}")
    eq(f"parity: python and SQL fold all {len(cases)} word values identically", mismatch, [])


def test_signal_parity_numeric_vocabulary():
    """THE NUMERIC VOCABULARY, closed by migration 6. This was D4's blast-radius lock on the gap.

    Until migration 6, `brain.signal_ok('dependency_unblocking','5')` was false and the INSERT was
    refused outright, while `bin/swarm` accepts the count and folds it `high`. The old form of this
    test asserted the gap (6 of 6 refused, 4 of 6 folded differently) so it could not be papered
    over. Migration 6 widened `signal_ok` and added both branches to `signal_level`, so the same
    six cases are asserted here in their fixed form: accepted, and folded exactly as the source
    folds them. If the store ever narrows again this goes red for the same reason it used to.

    The six values and the six expected levels are D4's measurement, re-derived by EXECUTING
    `bin/swarm:596 signal_level()` on each one rather than by reading it.
    """
    numeric = [("dependency_unblocking", "0", "low"), ("dependency_unblocking", "2", "medium"),
               ("dependency_unblocking", "9", "high"), ("confidence", "0.1", "low"),
               ("confidence", "0.5", "medium"), ("confidence", "0.95", "high")]
    with store.read("runtime") as s:
        refused = [f"{f}={v}" for f, v, _ in numeric
                   if not s.scalar("SELECT brain.signal_ok(%s, %s)", (f, v))]
        wrong_sql = [f"{f}={v}: sql {s.scalar('SELECT brain.signal_level(%s, %s)', (f, v))!r} "
                     f"vs bin/swarm {want!r}" for f, v, want in numeric
                     if s.scalar("SELECT brain.signal_level(%s, %s)", (f, v)) != want]
        wrong_py = [f"{f}={v}" for f, v, want in numeric if signal_level(f, v) != want]
        # Widening is not weakening. A garbage value, and the two non-finite strings the source
        # mishandles, are all still refused on write and still read conservative.
        still_refused = [f"{f}={v}" for f, v in
                         (("dependency_unblocking", "garbage"), ("confidence", "garbage"),
                          ("confidence", "nan"), ("dependency_unblocking", "inf"),
                          ("stakes", "5"), ("urgency", "0.9"))
                         if not s.scalar("SELECT brain.signal_ok(%s, %s)", (f, v))]
    eq("numeric: the store accepts all six of D4's measured cases", refused, [])
    eq("numeric: and SQL folds all six the way bin/swarm folds them", wrong_sql, [])
    eq("numeric: and signals.py folds all six the same way", wrong_py, [])
    eq("numeric: garbage, nan, inf and a numeric on a word-only field are still refused",
       len(still_refused), 6)
    # And it must now go all the way through the CLI, which is the thing the gap prevented.
    reset()
    r = sh("post", "--lane", "t", "--title", "numeric signal", "--dependency-unblocking", "5")
    truth("numeric: post --dependency-unblocking 5 succeeds end to end",
          r.returncode == 0, (r.stderr or r.stdout)[:300])
    tid = (r.stdout or "").strip()          # `post` prints the id and nothing else
    with store.read("runtime") as s:
        raw = s.scalar("SELECT dependency_unblocking FROM brain.work_item WHERE id = %s", (tid,))
        lvl = s.scalar("SELECT dependency_unblocking FROM brain.work_item_signals WHERE id = %s",
                       (tid,))
    # THE COUNT IS STORED, NOT THE LEVEL. D4 refused to fold `5` to `high` at the CLI precisely so
    # this would stay checkable: a store that kept the level would have lost the caller's number.
    eq("numeric: the raw count is stored as written, not folded behind the caller's back", raw, "5")
    eq("numeric: and the signals view folds it to high for the ranking", lvl, "high")


def test_signal_parity_numeric_is_the_same_accepted_set_on_both_sides():
    """The write gate and the fold must accept the same set, or a post dies on a raw CHECK.

    This is the shape of the original defect generalised: migration 1 folded one set of values and
    CHECKed a different one. `validate_signal` (Python, at post time) and `brain.signal_ok` (SQL,
    at insert time) are the two gates; `signal_level` and `brain.signal_level` are the two folds.
    All four are swept over the same value list here, including the four strings Python's bare
    `float()` accepts and Postgres does not.
    """
    from swarm_engine.signals import CONSERVATIVE, LEVELLED_SIGNALS, SIGNAL_ALIASES
    from swarm_engine.signals import validate_signal
    vals = ["low", "medium", "high", "LOW", "High", "", "  ", "garbage",
            "0", "1", "2", "3", "9", "-1", "-0.5", "0.0", "2.7", "2.999", "5", "1e3", "1e-3",
            "+5", "5.", ".5", "0.1", "0.2", "0.4", "0.5", "0.79", "0.8", "0.9", "0.95", "1.0",
            "1.5", "-0.3", "nan", "NaN", "inf", "-inf", "Infinity", "1_0", "0x10", "5x", "1,5"]
    for a in SIGNAL_ALIASES.values():
        vals += list(a)
    vals = list(dict.fromkeys(vals))
    bad_fold, bad_accept = [], []
    with store.read("runtime") as s:
        for f in LEVELLED_SIGNALS:
            for v in vals:
                sql_lv = s.scalar("SELECT brain.signal_level(%s, %s)", (f, v))
                py_lv = signal_level(f, v) or CONSERVATIVE[f]
                if sql_lv != py_lv:
                    bad_fold.append(f"{f}={v!r}: sql {sql_lv!r} vs python {py_lv!r}")
                sql_ok = bool(s.scalar("SELECT brain.signal_ok(%s, %s)", (f, v)))
                try:
                    validate_signal(f, v)
                    py_ok = True
                except ValueError:
                    py_ok = False
                if sql_ok != py_ok:
                    bad_accept.append(f"{f}={v!r}: sql_ok {sql_ok} vs python_ok {py_ok}")
    n = len(LEVELLED_SIGNALS) * len(vals)
    eq(f"parity: python and SQL ACCEPT the same set across all {n} field/value pairs",
       bad_accept, [])
    eq(f"parity: python and SQL FOLD all {n} field/value pairs identically", bad_fold, [])


def test_permission_mode_is_carried_through_config():
    """It is load-bearing and it appeared in no planning document.

    `auto` runs the permission classifier, so a terminal can run the test that proves its own
    work. Under `acceptEdits` it can write the file and not verify it, which produces confident
    unverified work, which is the whole reason a self-report is trustworthy under one and not
    the other. So: carried from config, never hard-coded, never left to the engine default.
    """
    from swarm_engine.config import CARRIED_KEYS, agent_config, resolved
    cfg = Path(os.environ["ENGINE_CONFIG"])
    cfg.write_text(
        '{"fleet":"test",'
        ' "defaults":{"add_dirs":["/a","/b"],"permission_mode":"auto","task_timeout":3600},'
        ' "agents":[{"name":"T1","role":"terminal","lanes":["*"],'
        '            "config_dirs":["~/.claude","~/.claude-acct2"],"a_key_nobody_declared":"kept"},'
        '           {"name":"T9","role":"terminal","lanes":["*"],"permission_mode":"acceptEdits"}]}',
        encoding="utf-8")
    a = agent_config("T1")
    eq("config: permission_mode reaches an agent that did not state it (from defaults)",
       a["permission_mode"], "auto")
    eq("config: {**defaults, **agent} -- the agent's own value wins",
       agent_config("T9")["permission_mode"], "acceptEdits")
    eq("config: fleet defaults merge in", a["add_dirs"], ["/a", "/b"])
    eq("config: config_dirs survives", a["config_dirs"], ["~/.claude", "~/.claude-acct2"])
    eq("config: an UNKNOWN key passes through -- the tolerance that let config_dirs ship "
       "with no CLI change", a["a_key_nobody_declared"], "kept")
    truth("config: every key D00 requires is named in CARRIED_KEYS",
          all(k in CARRIED_KEYS for k in
              ("permission_mode", "config_dirs", "add_dirs", "lanes", "plans", "model",
               "engine_args", "allowed_tools", "task_timeout", "interval", "effort")))
    r = sh("config", "--agent", "T1", "--shell")
    truth("config: and the CLI exports it to the runner",
          "CFG_PERMISSION_MODE='auto'" in r.stdout, r.stdout)
    # The runner must USE the config value rather than hard-coding one.
    runner = (ROOT / "engine/bin/swarm-run").read_text(encoding="utf-8")
    truth("config: the runner reads permission_mode from config",
          "CFG_PERMISSION_MODE" in runner)
    truth("config: and hard-codes no mode of its own",
          "--permission-mode acceptEdits" not in runner and
          "--permission-mode auto\"" not in runner)


def test_the_live_bus_was_not_touched():
    """The constraint, asserted rather than promised."""
    truth("isolation: this engine's config path is not the live bus",
          ".swarm" not in str(Path(os.environ.get("ENGINE_CONFIG", "~/.brain-runtime/config.json"))))
    # A mention in prose is fine and there are several, all of them explaining why this lane
    # does NOT go there. What must not exist is code that RESOLVES the path: an expanduser, a
    # Path(), or a shell parameter expansion that would produce a handle to the live bus.
    import ast
    resolvers = []
    for f in sorted((ROOT / "engine").rglob("*.py")):
        if f.name == "test_contract.py":
            continue                                  # this file quotes the pattern it hunts
        tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"), str(f))
        for node in ast.walk(tree):
            # Any string LITERAL in executable code that names the live bus. A docstring is an
            # ast.Constant too, but it is never an argument, so this finds the dangerous ones.
            if isinstance(node, ast.Call):
                for a in ast.walk(node):
                    if isinstance(a, ast.Constant) and isinstance(a.value, str) \
                       and (".swarm" in a.value and "swarm-admiral" not in a.value):
                        resolvers.append(f"{f.name}:{node.lineno} {a.value!r}")
    eq("isolation: no engine python resolves a ~/.swarm path in executable code", resolvers, [])
    shell = ""
    # FILES ONLY. `glob("*")` yields directories too, and engine/bin acquires one the moment any
    # python imports a module from it: `engine/tests/test_denominator_lint.py` importlib-execs
    # `engine/bin/denominator-lint.py`, which makes CPython write `engine/bin/__pycache__/`. It is
    # gitignored and entirely legitimate; what was not legitimate was `read_text` on it raising
    # IsADirectoryError, which aborted this whole scene. Task 0354 registered that suite in
    # run-all.sh and this went from `94 passed, 0 failed` to `91 passed, 1 failed` -- and the
    # three assertions BELOW this loop were the three that vanished, so the number the suite
    # printed shrank silently rather than the missing checks being named.
    scripts = [f for f in sorted((ROOT / "engine/bin").glob("*")) if f.is_file()]
    for f in scripts:
        shell += f"\n{f.name}: " + f.read_text(encoding="utf-8", errors="replace")
    shell_refs = [l for l in shell.splitlines()
                  if ("SWARM_HOME" in l or "/.swarm" in l) and not l.lstrip().startswith("#")]
    eq(f"isolation: and no engine shell script reads SWARM_HOME or /.swarm "
       f"({len(scripts)} files in engine/bin read)", shell_refs, [])
    truth("isolation: the scratch script refuses the live store database",
          "refusing to operate on the live store database" in
          (ROOT / "engine/bin/scratch-db.sh").read_text(encoding="utf-8"))
    eq("isolation: tests run against a scratch database", os.environ["BRAIN_PG_DB"] != "brain", True)


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    # Rules first, in rule order, then the rest.
    tests.sort(key=lambda f: (getattr(f, "_rule", 99), f.__name__))
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        os.environ["ENGINE_CONFIG"] = str(Path(td) / "config.json")
        Path(os.environ["ENGINE_CONFIG"]).write_text(
            '{"fleet":"test","agents":[{"name":"T1","role":"terminal","lanes":["*"]},'
            '{"name":"T2","role":"terminal","lanes":["*"]},'
            '{"name":"A1","role":"terminal","lanes":["*"]},'
            '{"name":"A2","role":"terminal","lanes":["*"]},'
            '{"name":"admiral","role":"admiral","lanes":[],"plans":["*"]}]}', encoding="utf-8")
        print("test_contract.py  --  one test per frozen contract rule")
        print(f"  store: {os.environ['BRAIN_PG_DB']} (scratch)\n")
        for t in tests:
            n = getattr(t, "_rule", None)
            print(f"{'rule ' + str(n) if n else t.__name__.replace('test_', '')}: "
                  f"{t.__doc__.splitlines()[0] if t.__doc__ else ''}")
            try:
                t()
            except Exception as e:                                # noqa: BLE001
                import traceback
                bad(f"{t.__name__} raised", traceback.format_exc().splitlines()[-1])
            print()
    print(f"{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
