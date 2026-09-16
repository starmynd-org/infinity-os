#!/usr/bin/env python3
"""Three verbs that had no door the operator could reach. Task 0141, out of D9's audit (0118).

The waist rule is "one transition function per state change, exposed as a verb, called by every
surface" -- NOT "every verb on every surface". Thirty transitions being CLI-only is the rule
working. These three were different, because they are hops on the operator's own path:

  `accept work`    CONSOLE ONLY. The one act D00 rule 3 makes load-bearing -- acceptance is a
                   human act, separate from the agent's `done` -- had exactly one door, and that
                   door was a Flask DEVELOPMENT server on loopback. `swarm accept` is the
                   OBJECTIVES verb, so the obvious command answered a task id with "no objective
                   in inbox named: 0027", which reads like a lost task.
  `lineage project` NO SURFACE AT ALL. Not the CLI, not the console, not MCP. It is the only
                   writer of `brain.touch.git_ref` and the only path from a booked receipt into
                   the store, and reaching it meant importing `brain_adapter.store_projection` in
                   a Python REPL -- which is what D9 had to do to finish its acceptance run.
  `budget outcome` NO SURFACE. `enforcer.py` called it in-process and nothing else could, so an
                   incident whose guard was itself killed stayed an intention with no way to
                   close it out.

Every check below drives the SHIPPED BINARY as a subprocess. A test that called `store.apply`
would pass just as well against the code before this task, because the transitions were all
fine: the defect was that no door reached them. The two that assert a REFUSAL call the
transition directly on purpose, and say why where they do.

Run: python3 engine/tests/test-surface-doors.py    (a scratch database, never `brain`)

The two budget checks SKIP against the shared `brain_scratch`, loudly, because `scratch-db.sh`
applies `migrations/` and the budget tables live in `budget/schema/`. To run all 38, build a
scratch database that has both -- and note the ORDER, which is the whole trap:

    export ENGINE_SCRATCH_DB=brain_t6_0141 BRAIN_PG_DB=brain_t6_0141
    engine/bin/scratch-db.sh create
    engine/bin/scratch-db.sh psql -q -f - < budget/schema/0003_budget.sql
    engine/bin/scratch-db.sh psql -q -f - < migrations/0005_lineage_resolution.sql   # AGAIN

Migration 5's lineage sweep is driven off `information_schema`, so it adds `produced_by_ref` and
`resolution_status` to the tables that exist WHEN IT RUNS. Apply the budget schema afterwards and
`budget_incident` never gets them, and `budget note` dies on a column that is in the code and not
in the database. The sweep is re-runnable by design, so running it a second time is the fix
rather than a hand-written ALTER. That is task 0137's finding met from the other side.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT / "adapter"))
sys.path.insert(0, str(ROOT))

os.environ.setdefault("BRAIN_PG_DB", os.environ.get("ENGINE_SCRATCH_DB", "brain_scratch"))

# Reconcile this database to `migrations/` before anything below asserts anything. Task 0153.
# `run-all.sh` does this once for the whole run (task 0148); a suite run BY ITSELF did not, and a
# brief that asks a lane to prove one behaviour asks for exactly that. 11 passed / 9 failed against
# a schema three migrations behind is not a red suite, it is a red suite about nothing.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _scratch_preflight import reconcile              # noqa: E402
reconcile(os.environ["BRAIN_PG_DB"])
# The live runner exports both of these into every terminal, and `accept-work` refuses to run
# where they are set. Popped here so the suite tests the operator's shell; the refusal itself is
# tested by putting one of them back, explicitly, in the check that asserts it.
os.environ.pop("SWARM_PARENT_TASK", None)
os.environ.pop("SWARM_AGENT", None)

import store                                             # noqa: E402
from swarm_engine import accept, transitions             # noqa: E402,F401  (registers the verbs)
from budget import transitions as budget_transitions     # noqa: E402,F401  (same)

SCRATCH = str(ROOT / "engine/bin/scratch-db.sh")
SWARM = str(ROOT / "engine/bin/swarm")
ADAPTER = str(ROOT / "adapter/bin/brain-adapter")
BUDGET = str(ROOT / "budget/bin/budget")

PASS, FAIL, SKIP = 0, 0, 0


def ok(msg):
    global PASS
    PASS += 1
    print(f"  ok    {msg}")


def skip(msg, why):
    """Loud, counted, and never folded into the pass total.

    A check that cannot run has to look different from a check that ran. The budget tables are
    the case here: they live in `budget/schema/`, `scratch-db.sh` applies `migrations/` only, and
    a shared `brain_scratch` therefore has no `brain.budget_incident` at all -- so these two
    would fail for a reason that is about the database and not about the door, which is the exact
    illegibility task 0148 put a preflight in `run-all.sh` to end.
    """
    global SKIP
    SKIP += 1
    print(f"  SKIP  {msg}\n        {why}")


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


def run(binary, *args, env=None):
    return subprocess.run([binary, *args], capture_output=True, text=True,
                          env={**os.environ, **(env or {})})


def reset():
    subprocess.run([SCRATCH, "truncate"], check=True, capture_output=True)


def finished_task(title, **signals):
    """A task an agent has claimed, run and reported done on. Not accepted: that is the point."""
    tid = store.apply("post", lane="t", title=title, signals=signals or None,
                      agent_claimable=True, workdir="/tmp")["id"]
    store.apply("claim", agent="T9", lanes=["t"], role="terminal")
    store.apply("done", id=tid, summary="reported finished by the agent", agent="T9")
    return tid


def row(tid):
    with store.read("runtime") as s:
        return s.one("SELECT state, accepted_at, accepted_by FROM brain.work_item WHERE id = %s",
                     (tid,))


# ============================================================ 1. accept work, the operator's door

def test_the_cli_can_accept_finished_work():
    """The headline. Before task 0141 there was no argv that reached this transition."""
    reset()
    tid = finished_task("an item the operator accepts from his own shell")
    r = run(SWARM, "accept-work", tid, "--by", "operator")
    eq("accept-work exits 0", r.returncode, 0)
    got = row(tid)
    truth("accepted_at is written", got["accepted_at"] is not None, f"row: {got}")
    eq("accepted_by records the human", got["accepted_by"], "operator")
    eq("acceptance does NOT change state: `done` is the agent's report, acceptance is the "
       "separate act (D00 rule 3)", got["state"], "done")
    with store.read("runtime") as s:
        kinds = [t["kind"] for t in s.query(
            "SELECT kind FROM brain.thread WHERE work_item_id = %s ORDER BY seq", (tid,))]
    truth("the acceptance lands on the thread, so the week's measurement can score it",
          "accept" in kinds, f"thread kinds: {kinds}")


def test_the_wrong_door_now_names_the_right_one():
    """D9 ran `swarm accept 0027` and was told there was no OBJECTIVE by that name.

    True, and the wrong answer to the question being asked. The objectives verb keeps the plain
    name; the refusal has to hand the operator the command that works, or the next person reads
    it as a lost task exactly as D9 did.
    """
    reset()
    tid = finished_task("an item the operator tries to accept through the objectives verb")
    r = run(SWARM, "accept", tid)
    truth("`swarm accept <task-id>` still refuses (it is the objectives verb)", r.returncode != 0)
    truth("and the refusal names `accept-work` and the id", f"accept-work {tid}" in r.stderr,
          f"stderr: {r.stderr.strip()}")
    truth("nothing was accepted by the refusal", row(tid)["accepted_at"] is None)


def test_an_agent_terminal_cannot_accept_from_the_cli():
    """D00 rule 4: the decider on every acceptance is a human.

    The door reads the same two variables `transitions._caller` reads. In a terminal they mean
    the hands on the keyboard belong to an agent, and this is the surface where that inference
    is sound -- which is also why the check is at the door and not in the transition, where a
    console started FROM a terminal shell would inherit the variable and be refused forever.
    """
    reset()
    tid = finished_task("an item an agent tries to accept for itself")
    for var in ("SWARM_PARENT_TASK", "SWARM_AGENT"):
        r = run(SWARM, "accept-work", tid, env={var: "T9"})
        eq(f"refused with {var} set, exit 6", r.returncode, 6)
        truth(f"the refusal with {var} names the rule",
              "human" in r.stderr.lower(), f"stderr: {r.stderr.strip()}")
    truth("nothing was accepted by either attempt", row(tid)["accepted_at"] is None)


def test_the_verb_itself_refuses_an_agent_acceptor():
    """Called through `store.apply`, NOT the CLI, and that is the point of this check.

    An env gate lives on one surface. This one is in the transition, so an agent that unsets the
    variables, or reaches the verb from a surface built next month, is still refused by the name
    it works under -- `claim` registers that name in `brain.agent`, so the membership test is a
    real question about the acceptor rather than a blocklist.
    """
    reset()
    tid = finished_task("an item accepted by the agent that did it")
    try:
        store.apply("accept work", id=tid, by="T9", as_operator=True)
        bad("the verb refuses a fleet agent as acceptor", "it accepted")
    except Exception as e:                                            # noqa: BLE001
        truth("the verb refuses a fleet agent as acceptor (D00 rule 4)",
              "fleet agent" in str(e), f"raised: {e}")
    truth("accepted_at is still null after the refusal", row(tid)["accepted_at"] is None)

    try:
        store.apply("accept work", id=tid, by="   ", as_operator=True)
        bad("the verb refuses an unattributed acceptance", "it accepted")
    except Exception as e:                                            # noqa: BLE001
        truth("the verb refuses an acceptance attributed to nobody", "no acceptor" in str(e),
              f"raised: {e}")
    truth("accepted_at is still null after the second refusal", row(tid)["accepted_at"] is None)


def test_the_console_actor_is_still_accepted():
    """The regression guard on the one door that already worked.

    A new guard on a verb whose only caller is the console is one bad predicate away from
    breaking the console. `web/app.py:59` accepts as `operator`, who claims nothing and is
    therefore not in `brain.agent`.
    """
    reset()
    tid = finished_task("an item accepted from the console")
    store.apply("accept work", id=tid, by="operator", as_operator=True)
    eq("the console's actor still accepts", row(tid)["accepted_by"], "operator")


def test_the_verb_is_registered_once_and_the_cli_calls_it():
    reg = store.registered()
    truth("`accept work` is a registered transition", "accept work" in reg)
    src = (ROOT / "engine/swarm_engine/cli.py").read_text(encoding="utf-8")
    truth("the CLI reaches it through store.apply and writes no SQL of its own",
          'store.apply("accept work"' in src and "UPDATE brain.work_item" not in src)


# ============================================================ 2. lineage project, the git -> store hop

# A fresh subject per run, and it is not decoration. `brain.touch`'s uniqueness constraint is on
# the EDGE -- subject, far end, component type, role -- with NULLS NOT DISTINCT, and deliberately
# NOT on `git_ref`: the first commit to state an edge is the one that states it, and a later
# receipt repeating it is not a correction. `scratch-db.sh truncate` does not clear `brain.touch`
# or `brain.receipt`, so a fixed subject id makes the SECOND run of this suite project a receipt
# whose edge collides with the first run's, insert nothing, and fail an assertion about today's
# commit. That is what happened, and it is worth stating rather than quietly fixing: a suite that
# passes once and fails identically ever after is the worst kind of green.
NONCE = f"t6-0141-{os.getpid()}"


def _receipt_markdown() -> str:
    """Rendered by D2's own `Receipt`, never hand-written markdown.

    A hand-rolled fixture tests this suite's idea of the format. Rendering through the type the
    booking path uses means the file under test is the file `receipt book` commits.
    """
    from brain_adapter.promotion import Lineage
    from brain_adapter.receipt import Receipt, Touch
    return Receipt(
        department="t6-sandbox", date="2026-08-16", slug="0141-surface-door",
        title="a receipt booked for the surface-door test", moment="result-produced",
        summary="proves the projection door, and nothing else",
        lineage=Lineage(produced_by="knowledge-ai-architecture-surface-boundary",
                        produced_by_ref="surface-boundary", resolution_status="resolved",
                        actor_type="ai"),
        touches=[Touch(entity_id="knowledge-ai-architecture-surface-boundary",
                       component_type="knowledge", orient_role="tradition",
                       entity_ref="surface-boundary", resolution_status="resolved")],
        subject_type="work_item", subject_id=NONCE,
    ).render()


def _git(repo, *args):
    p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return p.stdout.strip()


def _fixture_commit(tmp: Path) -> tuple[Path, str]:
    """A real commit holding a real receipt, in a throwaway repo. Never anyone's brain."""
    repo = tmp / "brainlike"
    (repo / "departments" / "t6-sandbox" / "receipts").mkdir(parents=True)
    # `receipts/durable`, not `scratch/t6-0141`, since task 0349: `lineage project` refuses a
    # git_ref that is not reachable from a durable ref, and the default entry in
    # `policy/durable-refs.json` names this branch for a repository the policy does not know.
    # The fixture is still a throwaway repo -- what changed is that a commit on a branch nobody
    # keeps can no longer become a store row asserting it exists.
    _git(repo, "init", "-q", "-b", "receipts/durable")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    target = repo / "departments/t6-sandbox/receipts/2026-08-16-surface-door.md"
    target.write_text(_receipt_markdown(), encoding="utf-8")
    _git(repo, "add", str(target.relative_to(repo)))
    _git(repo, "commit", "-qm", "receipt")
    return repo, _git(repo, "rev-parse", "HEAD")


def test_the_adapter_cli_projects_a_committed_receipt():
    reset()
    with tempfile.TemporaryDirectory() as td:
        repo, sha = _fixture_commit(Path(td))
        r = run(ADAPTER, "lineage", "project", sha, "--repo", str(repo), "--allow-undeclared-repo")
        eq("lineage project exits 0", r.returncode, 0)
        out = json.loads(r.stdout or "{}")
        truth("it reports the projection", out.get("projected") is True, r.stdout + r.stderr)
        with store.read("runtime") as s:
            rec = s.one("SELECT id, git_ref, booked_by FROM brain.receipt WHERE git_ref = %s",
                        (sha,))
            edges = s.query("SELECT entity_id, git_ref, role FROM brain.touch WHERE git_ref = %s",
                            (sha,))
        truth("brain.receipt carries the commit", rec is not None, "no receipt row")
        eq("one touch edge landed", len(edges), 1)
        eq("the edge names the commit it came from (brain.touch.git_ref is NOT NULL and this "
           "verb is its only writer)", edges[0]["git_ref"] if edges else None, sha)

        again = run(ADAPTER, "lineage", "project", sha, "--repo", str(repo), "--allow-undeclared-repo")
        eq("re-projecting exits 0", again.returncode, 0)
        out2 = json.loads(again.stdout or "{}")
        eq("re-projecting inserts nothing: a reconciliation pass can run as often as anyone "
           "likes", out2.get("touches_inserted"), 0)
        truth("and says so rather than reporting a fresh projection",
              out2.get("already_projected") is True, again.stdout)


def test_a_moving_ref_is_resolved_and_reported_as_the_commit_it_resolved_to():
    """Corrected by measurement, and the correction is worth keeping.

    This check was written expecting `HEAD` to be refused, because the transition refuses a
    git_ref that is not a full sha. It is not refused: `read_promotion` resolves the ref against
    git BEFORE the transition sees it (`store_join._full_sha`), which is right -- git is what
    resolves refs, and the row still names an immutable commit. What that DOES mean is that the
    door must report the resolved sha, and the first version of it echoed the argument, so
    `lineage project HEAD` printed `git_ref: HEAD` beside a row that said otherwise. The one
    field a reader would reconcile against git was the one field that could not be.
    """
    with tempfile.TemporaryDirectory() as td:
        repo, sha = _fixture_commit(Path(td))
        r = run(ADAPTER, "lineage", "project", "HEAD", "--repo", str(repo), "--allow-undeclared-repo")
        eq("a resolvable ref projects", r.returncode, 0)
        eq("and the door reports the COMMIT, not the ref it was handed",
           json.loads(r.stdout or "{}").get("git_ref"), sha)


def test_the_door_passes_the_transitions_refusal_through():
    """A ref git cannot resolve is a typed refusal (5), not a success and not a store failure."""
    with tempfile.TemporaryDirectory() as td:
        repo, _ = _fixture_commit(Path(td))
        r = run(ADAPTER, "lineage", "project", "no-such-ref-t6-0141", "--repo", str(repo),
                "--allow-undeclared-repo")
        eq("an unresolvable ref exits 5, the adapter's `promotion refused`", r.returncode, 5)
        truth("the refusal is typed and reaches the operator",
              "GIT_FAILED" in (r.stdout + r.stderr) or "NOT_A_COMMIT" in (r.stdout + r.stderr),
              f"stdout: {r.stdout} stderr: {r.stderr}")


def test_the_adapters_other_subcommands_still_need_no_database():
    """The invariant `lineage project` could have quietly ended, measured as an import graph.

    `store_projection` imports psycopg2. If that import moved to module scope, `entity resolve`
    would need Postgres running, and "losing the store costs zero knowledge" would stop being
    true of the code that reads the knowledge.
    """
    probe = ("import sys; sys.path.insert(0, %r); import brain_adapter.cli; "
             "print(sorted(m for m in sys.modules if m in ('store', 'psycopg2')))"
             % str(ROOT / "adapter"))
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)
    eq("importing the adapter CLI pulls in no database driver", out.stdout.strip(), "[]")


# ============================================================ 3. budget outcome

def _budget_schema_ready() -> str:
    """"" when the budget tables are here in the shape the code expects, else why they are not.

    The second half of this is a real trap and it is measured rather than assumed: applying
    `budget/schema/0003_budget.sql` AFTER `migrations/` leaves `budget_incident` without the
    lineage pair, because migration 5's sweep is driven off `information_schema` and had already
    run when the table did not exist. Re-running `migrations/0005_lineage_resolution.sql` fixes
    it -- the sweep is re-runnable by design -- and that is task 0137 hit from the other side.
    """
    with store.read("runtime") as s:
        cols = {r["column_name"] for r in s.query(
            "SELECT column_name FROM information_schema.columns "
            " WHERE table_schema = 'brain' AND table_name = 'budget_incident'")}
    if not cols:
        return ("brain.budget_incident does not exist in this database. It is created by "
                "budget/schema/0003_budget.sql, which scratch-db.sh does not apply: it applies "
                "migrations/ only. Apply it, then re-apply migrations/0005_lineage_resolution.sql.")
    missing = {"produced_by_ref", "resolution_status"} - cols
    if missing:
        return (f"brain.budget_incident is missing {sorted(missing)}, so `budget note` cannot "
                f"write a row. The budget schema landed after migration 5's lineage sweep had "
                f"already read the catalog. Re-apply migrations/0005_lineage_resolution.sql.")
    return ""


def test_the_budget_cli_can_close_out_an_incident():
    """An incident is written BEFORE the signal is sent, so until this verb runs it states an
    intention. The guard called it in-process and nothing else could."""
    why = _budget_schema_ready()
    if why:
        skip("budget outcome closes out an incident", why)
        skip("a missing incident is refused", why)
        return
    reset()
    inc = store.apply("budget note", actor="test", kind="warn", scope_type="agent",
                      scope_id="T9", spend_usd="1.00", limit_usd="2.00", percent_used=50,
                      detail="the meter crossed the warn line")
    r = run(BUDGET, "outcome", str(inc["id"]), "--signal", "TERM", "--detail",
            "engine 1234 exited 143", "--pid", "1234", "--json")
    eq("budget outcome exits 0", r.returncode, 0)
    with store.read("runtime") as s:
        got = s.one("SELECT signal_sent, engine_pid, detail FROM brain.budget_incident "
                    "WHERE id = %s", (inc["id"],))
    eq("the signal actually sent is recorded", got["signal_sent"], "TERM")
    eq("the pid it was observed on is recorded", got["engine_pid"], 1234)
    truth("the detail is APPENDED, never over the incident's own account",
          "warn line" in got["detail"] and "exited 143" in got["detail"], got["detail"])


def test_closing_out_an_incident_that_does_not_exist_is_an_error():
    if _budget_schema_ready():
        return                                   # already reported by the check above, once
    r = run(BUDGET, "outcome", "99999999", "--signal", "TERM")
    truth("a missing incident is refused rather than silently doing nothing",
          r.returncode != 0 and "no incident" in (r.stdout + r.stderr),
          f"rc={r.returncode} {r.stdout} {r.stderr}")


def main():
    print(__doc__.splitlines()[0])
    print("\n-- accept work: the operator's own door (was: console only)")
    test_the_cli_can_accept_finished_work()
    test_the_wrong_door_now_names_the_right_one()
    test_an_agent_terminal_cannot_accept_from_the_cli()
    test_the_verb_itself_refuses_an_agent_acceptor()
    test_the_console_actor_is_still_accepted()
    test_the_verb_is_registered_once_and_the_cli_calls_it()
    print("\n-- lineage project: git -> store (was: no surface at all)")
    test_the_adapter_cli_projects_a_committed_receipt()
    test_a_moving_ref_is_resolved_and_reported_as_the_commit_it_resolved_to()
    test_the_door_passes_the_transitions_refusal_through()
    test_the_adapters_other_subcommands_still_need_no_database()
    print("\n-- budget outcome: what the incident actually did (was: in-process only)")
    test_the_budget_cli_can_close_out_an_incident()
    test_closing_out_an_incident_that_does_not_exist_is_an_error()
    print(f"\n{PASS} passed, {FAIL} failed" + (f", {SKIP} SKIPPED (read them)" if SKIP else ""))
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
