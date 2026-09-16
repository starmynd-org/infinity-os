#!/usr/bin/env python3
"""THE NAMED TEST.

    **There must be no code path by which a recommendation becomes executed work without a
    human decider.**

One of the program's five success criteria, and it is verified by test rather than by review
because a criterion nobody can re-run is a criterion that decays silently. D9 will try to break
it deliberately; every assertion below is a route D9 would take, written down so the next
attempt starts from the list rather than from scratch.

The test name to quote is `test_no_code_path_executes_a_recommendation_without_a_human`, and it
is the last one in the file: it re-derives the claim from the seven routes rather than restating
it, so a route that stops being covered fails the summary assertion too.

Run:  python3 queue/tests/test_no_self_execution.py
      (against the queue scratch database, never `brain`)
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "queue"))
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT))

os.environ.setdefault("BRAIN_PG_DB", os.environ.get("QUEUE_SCRATCH_DB", "brain_queue_scratch"))
os.environ.pop("SWARM_PARENT_TASK", None)      # the live runner exports this; tests post roots

# Build or reconcile the scratch database BEFORE this suite asserts anything. Task 0212. Running a
# suite by itself is what a brief asks for, and by itself this one used to die in reset() on a
# TRUNCATE against a database nobody had built.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _scratch_preflight import reconcile                 # noqa: E402
reconcile(os.environ["BRAIN_PG_DB"])

import psycopg2                                          # noqa: E402
import store                                             # noqa: E402
from human_queue import transitions as queue_transitions  # noqa: E402,F401
from human_queue.transitions import QueueError            # noqa: E402
from swarm_engine import accept as engine_accept          # noqa: E402,F401  (registers `accept work`)
from swarm_engine import transitions as engine            # noqa: E402,F401

SCRATCH = str(ROOT / "queue/bin/queue-scratch-db.sh")
PASS, FAIL, ROUTES = 0, 0, {}


def route(key):
    def deco(fn):
        fn._route = key
        return fn
    return deco


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ok    {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")
    return ok


def refuses(fn, *a, **kw) -> str:
    """Run something that must be refused. Returns the message, or '' if it was NOT refused."""
    try:
        fn(*a, **kw)
        return ""
    except Exception as e:                                          # noqa: BLE001
        return str(e) or e.__class__.__name__


def su(sql, params=None):
    """A SUPERUSER connection, deliberately. The gates must hold against the strongest caller.

    A gate that only refuses the application role is a gate that a migration, a repair script or
    an operator at a psql prompt walks straight through, and every one of those is a code path.
    """
    pw = (Path.home() / ".brain-postgres-secrets" / "brain-postgres-bootstrap-superuser").read_text().strip()
    db = os.environ["BRAIN_PG_DB"]
    conn = psycopg2.connect(host="127.0.0.1", port=5432, dbname=db, user="postgres", password=pw)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            conn.commit()
    finally:
        conn.close()


def reset():
    subprocess.run([SCRATCH, "psql", "-q", "-c",
                    "TRUNCATE brain.queue_item, brain.queue_defer, brain.queue_bump, "
                    "brain.queue_calibration, brain.queue_default_event, brain.recommendation, "
                    "brain.thread, brain.question, brain.agent, brain.work_item CASCADE; "
                    "SELECT setval('brain.item_id_seq', 1, false);"],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def work_items() -> int:
    with store.read("runtime") as s:
        return int(s.scalar("SELECT count(*) FROM brain.work_item") or 0)


def rec(rid) -> dict:
    with store.read("runtime") as s:
        return s.one("SELECT * FROM brain.recommendation WHERE id = %s", (rid,))


# --------------------------------------------------------------------------- route 1

@route("raising is not executing")
def test_recommend_creates_a_proposal_and_no_work():
    reset()
    before = work_items()
    r = store.apply("recommend", text="Restate the Acme margin story from the source data",
                    rationale="the cost data does not exist for 2025",
                    template_id="playbook-restate-a-metric",
                    proposed_action="rebuild the 2025 margin table from the source export")
    row = rec(r["id"])
    check("a raised recommendation is open", row["state"] == "open", row["state"])
    check("a raised recommendation spawned nothing",
          row["spawned_work_item"] is None and work_items() == before,
          f"work_item count {before} -> {work_items()}")
    check("requires_human is non-null and defaults true", row["requires_human"] is True)
    return r["id"]


# --------------------------------------------------------------------------- route 2
#
# TASK 0156 REWROTE THIS ROUTE. It measured two proxies and both had expired, so the suite printed
# "the criterion is NOT met" on a night when, as far as this evidence goes, it was met:
#
#   FAIL  the whole registry holds exactly three verbs with `accept` in the name
#         found ['accept', 'accept work', 'auto accept flag', 'recommend accept']
#   FAIL  only one module in the whole repo writes brain.recommendation
#         writers: ['outputs/2026-08-16-D9-adversarial/a4_rec.py', 'queue/human_queue/transitions.py']
#
# `auto accept flag` is task 0139's writer for the auto-accept runtime flag; `a4_rec.py` is D9's
# reproduction script, which exists to attack these gates from a superuser prompt and which nothing
# imports. Neither one is a second acceptance path. This is the cheap direction of a bad
# measurement -- a false alarm, not a false calm -- and it still costs, because a suite that cries
# wolf on other lanes' work stops being read, and a criterion nobody reads is not verified.
#
# The repair is not to relax the assertion. Both checks were counts standing in for a property, so
# ask the property: exactly ONE statement anywhere a surface can reach sets a recommendation to
# accepted, and it is inside `recommend accept`, whose human gate routes 3 to 7 then prove. Every
# other statement here is derived from that, including what the verb names are allowed to be.

#: Trees the writer scan does not read, because nothing a surface can reach imports them:
#: `outputs/` (generated artifacts and one-off reproduction scripts), the test files themselves
#: (entrypoints, never imports), scratch trees, and non-source directories. `_unimportable()`
#: PROVES that claim on every run instead of assuming it.
#:
#: By exclusion and not by an allowlist of code directories, deliberately. The obvious repair to
#: the failure above is to name the dirs that can violate the property and scan only those, and
#: that is a proxy with the same expiry date as the verb count it replaces. Measured 2026-08-16:
#: an allowlist of `adapter engine fabric ingest mcp queue web` omits `budget/transitions.py`
#: (seven registered verbs) and `store/` (the narrow waist every verb in the system goes through).
#: An allowlist goes SILENT when a package is added; an exclusion list goes NOISY. On this
#: criterion, take the noise.
UNREACHABLE_TREES = ("outputs", "tests", ".git", "__pycache__", ".pytest_cache")

#: The one module allowed to write `brain.recommendation`. Route 2 exists to keep it the one.
RECOMMENDATION_WRITER = "queue/human_queue/transitions.py"

WRITES_RECOMMENDATION = re.compile(r"UPDATE\s+brain\.recommendation", re.I)
DECLARES_VERB = re.compile(r"@store\.transition\(\s*[\"']([^\"']+)[\"']")


def _excluded(rel: Path) -> bool:
    """True for a path no surface can import: an unreachable tree, a scratch tree, a test file."""
    if any(p in UNREACHABLE_TREES or p.startswith("_scratch") for p in rel.parts):
        return True
    return rel.name.startswith(("test_", "test-")) or rel.stem.endswith("_test")


def _sources() -> list:
    """(repo-relative path, text) for every `.py` a surface could reach."""
    return [(rel, py.read_text(encoding="utf-8", errors="ignore"))
            for py, rel in ((p, p.relative_to(ROOT)) for p in sorted(ROOT.rglob("*.py")))
            if not _excluded(rel)]


def _unimportable(sources) -> list:
    """Scanned files that import something the scan excluded. Must be empty.

    This is what makes the exclusion above earned rather than asserted. An excluded module that
    reachable code imports IS reachable, and skipping it would be the false calm this route exists
    to prevent -- strictly worse than the false alarm that prompted the rewrite.
    """
    reachable_stems = {rel.stem for rel, _ in sources}
    names = set(UNREACHABLE_TREES) | {
        p.relative_to(ROOT).stem for p in ROOT.rglob("*.py")
        if _excluded(p.relative_to(ROOT)) and p.relative_to(ROOT).stem not in reachable_stems}
    return [f"{rel} imports {m.group(1)}"
            for rel, text in sources
            for m in re.finditer(r"^\s*(?:from|import)\s+([\w.]+)", text, re.M)
            if set(m.group(1).split(".")) & names]


def _defining_file(entry: dict) -> str:
    """`registered()` records `abspath:line`. Repo-relative, or the raw record if outside ROOT."""
    path = entry["defined_in"].rsplit(":", 1)[0]
    try:
        return str(Path(path).resolve().relative_to(ROOT))
    except ValueError:
        return entry["defined_in"]


@route("exactly one acceptance path exists")
def test_only_one_transition_can_accept():
    sources = _sources()
    stowaways = _unimportable(sources)
    check("the writer scan reads every file a surface can reach and nothing else",
          not stowaways, f"reachable code imports an excluded module: {stowaways[:3]}")

    writers = sorted(str(rel) for rel, text in sources if WRITES_RECOMMENDATION.search(text))
    # Say what was dropped. A scan that silently narrows reads as "covered everything".
    skipped = sorted(str(p.relative_to(ROOT)) for p in ROOT.rglob("*.py")
                     if _excluded(p.relative_to(ROOT))
                     and WRITES_RECOMMENDATION.search(p.read_text(encoding="utf-8",
                                                                  errors="ignore")))
    print(f"        (writes brain.recommendation but is not a code path, so not scanned: "
          f"{skipped or 'nothing'})")
    check("only one module a surface can reach writes brain.recommendation",
          writers == [RECOMMENDATION_WRITER], f"writers: {writers}")

    # The claim the two counts were standing in for, asked directly. Because `writers` above is
    # complete over the reachable tree, a call chain of any length still ends at THIS statement,
    # inside THIS verb -- so transitive reach is covered without tracing it.
    src = (ROOT / RECOMMENDATION_WRITER).read_text(encoding="utf-8")
    accepted = [m.start() for m in re.finditer(r"state\s*=\s*'accepted'", src)]
    check("only one statement in that module sets state = 'accepted'",
          len(accepted) == 1, f"{len(accepted)} statements")
    start = src.index('@store.transition("recommend accept")')
    end = min((m.start() for m in re.finditer(r"@store\.transition\(", src) if m.start() > start),
              default=len(src))
    check("and that one statement is inside the `recommend accept` verb",
          bool(accepted) and all(start < off < end for off in accepted),
          f"offsets {accepted}, `recommend accept` spans {start}..{end}")

    # Now the verbs. Read the decorators out of the source rather than `store.registered()`, which
    # holds only what THIS process imported: `budget/transitions.py` registers seven verbs no test
    # here imports, and a scan that cannot see them is a scan that reports on its own import list.
    declared = {m.group(1): str(rel) for rel, text in sources for m in DECLARES_VERB.finditer(text)}
    loaded = store.registered()
    declared.update({v: _defining_file(e) for v, e in loaded.items()})   # non-literal verb names
    blind = sorted({str(rel) for rel, text in sources
                    if text.count("@store.transition(") > len(DECLARES_VERB.findall(text))})
    print(f"        (verb names this scan cannot read, being expressions rather than literals, "
          f"in: {blind or 'nothing'})")

    accepting = sorted(v for v in declared if "accept" in v)
    print(f"        (verbs with `accept` in the name: {accepting})")
    # NOT a count. `accept` is objectives, `accept work` is a finished work_item (D4's, with its
    # own human gate), `auto accept flag` writes a runtime_flag. None of the three is defined in a
    # module that writes brain.recommendation at all, and by `writers` above no such write exists
    # anywhere else, so none of them can reach an acceptance however it is called. That is the
    # property; the old assertion measured how many of them there happened to be.
    can_reach = sorted(v for v in accepting if declared[v] in writers)
    check("of those, only `recommend accept` is defined in a module that writes "
          "brain.recommendation at all",
          can_reach == ["recommend accept"], f"can reach a write: {can_reach or 'none'}")


# --------------------------------------------------------------------------- route 3

@route("an agent cannot be the decider")
def test_an_agent_may_not_accept():
    reset()
    store.apply("heartbeat", agent="T7", status="working")
    r = store.apply("recommend", text="ship the thing", proposed_action="ship the thing")
    msg = refuses(store.apply, "recommend accept", id=r["id"], by="T7")
    check("the verb refuses a registered agent as decider",
          "registered agent" in msg, msg[:120] or "NOT REFUSED")
    check("the recommendation is still open after the refusal", rec(r["id"])["state"] == "open")

    # And again at the database, with the application role removed from the picture entirely.
    msg2 = refuses(su, "UPDATE brain.recommendation SET state='accepted', decided_at=now(), "
                       "decided_by='T7', actor_type='human' WHERE id=%s", (r["id"],))
    check("the trigger refuses an agent decider even for the superuser",
          "registered agent" in msg2, msg2[:120] or "NOT REFUSED")


# --------------------------------------------------------------------------- route 4

@route("no decider, no acceptance")
def test_acceptance_needs_a_named_decider():
    reset()
    r = store.apply("recommend", text="ship the thing")
    check("the verb refuses an empty decider",
          "needs a decider" in refuses(store.apply, "recommend accept", id=r["id"], by=""))
    msg = refuses(su, "UPDATE brain.recommendation SET state='accepted', decided_at=now(), "
                      "decided_by='', actor_type='human' WHERE id=%s", (r["id"],))
    check("the trigger refuses an empty decider", "no decider recorded" in msg,
          msg[:120] or "NOT REFUSED")
    msg = refuses(su, "UPDATE brain.recommendation SET state='accepted', decided_at=now(), "
                      "decided_by='operator', actor_type='ai' WHERE id=%s", (r["id"],))
    check("the trigger refuses an acceptance booked as ai", "must be human" in msg,
          msg[:120] or "NOT REFUSED")


# --------------------------------------------------------------------------- route 5

@route("state cannot be forged")
def test_accepted_state_cannot_be_forged():
    reset()
    msg = refuses(su, "INSERT INTO brain.recommendation (text, requires_human, state) "
                      "VALUES ('born accepted', true, 'accepted')")
    check("a recommendation cannot be INSERTed accepted", "may not be born accepted" in msg,
          msg[:120] or "NOT REFUSED")

    posted = store.apply("post", title="a real task", lane="queue")
    msg = refuses(su, "INSERT INTO brain.recommendation (text, requires_human, spawned_work_item) "
                      "VALUES ('born linked', true, %s)", (posted["id"],))
    check("a recommendation cannot be INSERTed already linked to work",
          "may not be born linked" in msg, msg[:120] or "NOT REFUSED")

    r = store.apply("recommend", text="ship the thing")
    msg = refuses(su, "UPDATE brain.recommendation SET spawned_work_item=%s WHERE id=%s",
                  (posted["id"], r["id"]))
    check("an OPEN recommendation cannot be linked to executed work",
          "cannot be linked to executed work" in msg, msg[:120] or "NOT REFUSED")


# --------------------------------------------------------------------------- route 6

@route("requires_human=false opens nothing")
def test_requires_human_false_is_not_a_second_path():
    reset()
    store.apply("heartbeat", agent="T7", status="working")
    r = store.apply("recommend", text="auto-run the nightly restate", requires_human=False)
    check("the declaration is stored as made", rec(r["id"])["requires_human"] is False)
    msg = refuses(store.apply, "recommend accept", id=r["id"], by="T7")
    check("an agent still cannot accept it", "registered agent" in msg,
          msg[:120] or "NOT REFUSED")
    msg = refuses(su, "UPDATE brain.recommendation SET state='accepted', decided_at=now(), "
                      "decided_by='T7', actor_type='human' WHERE id=%s", (r["id"],))
    check("the database still refuses it", "registered agent" in msg,
          msg[:120] or "NOT REFUSED")
    from human_queue import reads
    finding = [f for f in reads.doctor()["findings"]
               if f["kind"] == "requires-human-false-declared"]
    check("the declaration is reported as a finding rather than honoured", len(finding) == 1)


# --------------------------------------------------------------------------- route 7

@route("an agent process cannot accept")
def test_a_fleet_terminal_cannot_accept():
    reset()
    r = store.apply("recommend", text="ship the thing")
    os.environ["SWARM_PARENT_TASK"] = "0042"
    try:
        # `as_operator=True` opens the OPERATOR login, so this route now proves the stronger
        # thing: even a process holding the operator credential is refused while
        # SWARM_PARENT_TASK is set. The environment test runs before the login test.
        msg = refuses(store.apply, "recommend accept", id=r["id"], by="operator",
                      as_operator=True)
    finally:
        os.environ.pop("SWARM_PARENT_TASK", None)
    check("a process running under the fleet runner is refused even as 'operator'",
          "fleet terminal" in msg, msg[:120] or "NOT REFUSED")
    check("and it is still open afterwards", rec(r["id"])["state"] == "open")


# --------------------------------------------------------------------------- the happy path

@route("the one path that does work")
def test_a_human_acceptance_spawns_the_work_and_links_it():
    reset()
    subject = store.apply("post", title="Acme margin restate", lane="data",
                          external="true")           # the subject is EXTERNAL
    r = store.apply("recommend", text="restate the 2025 margin table",
                    proposed_action="rebuild the 2025 margin table from the source export",
                    subject_type="work_item", subject_id=subject["id"],
                    template_id="playbook-restate-a-metric")
    before = work_items()
    # THE OPERATOR LOGIN, since task 0290. `store/transitions.py::_login_for` reads this out
    # of the kwargs `apply` was called with, so it has to be passed and not defaulted, and
    # without it `brain.current_human()` is NULL and migration 32 refuses the acceptance.
    out = store.apply("recommend accept", id=r["id"], by="operator", lane="data",
                      as_operator=True)
    row = rec(r["id"])
    check("a human acceptance is recorded",
          row["state"] == "accepted" and row["decided_by"] == "operator"
          and str(row["actor_type"]) == "human", f"{row['state']} {row['decided_by']}")
    check("it spawned exactly one work item",
          work_items() == before + 1 and row["spawned_work_item"] == out["spawned_work_item"])
    with store.read("runtime") as s:
        spawned = s.one("SELECT * FROM brain.work_item_signals WHERE id = %s",
                        (out["spawned_work_item"],))
    check("the spawned work INHERITS the subject's hard flag by OR",
          bool(spawned["external"]),
          "an accepted recommendation about an external subject produced an unflagged task")
    msg = refuses(store.apply, "recommend accept", id=r["id"], by="operator",
                  as_operator=True)
    check("accepting twice is refused", "already accepted" in msg, msg[:120] or "NOT REFUSED")


@route("the read path cannot write")
def test_the_read_path_cannot_accept():
    reset()
    r = store.apply("recommend", text="ship the thing")
    kind, msg = "", ""
    try:
        with store.read("runtime") as s:
            s.query("UPDATE brain.recommendation SET state = 'accepted' WHERE id = %s",
                    (r["id"],))
    except Exception as e:                                          # noqa: BLE001
        kind, msg = e.__class__.__name__, str(e)
    check("store.read() refuses the write, as a ReadOnlyViolation from Postgres itself",
          kind == "ReadOnlyViolation", f"{kind or 'NOT REFUSED'}: {msg[:90]}")


def test_no_code_path_executes_a_recommendation_without_a_human():
    """The summary assertion. It restates nothing: it fails if any route above failed.

    Named for the criterion so the report can quote a test name a reader can run.
    """
    check("no code path executes a recommendation without a human decider",
          FAIL == 0, f"{FAIL} route(s) above failed, so the criterion is NOT met")


def main():
    print("test_no_self_execution.py  --  the program success criterion")
    print(f"  store: {os.environ['BRAIN_PG_DB']} (scratch). The live bus at ~/.swarm is untouched.\n")
    for fn in (test_recommend_creates_a_proposal_and_no_work,
               test_only_one_transition_can_accept,
               test_an_agent_may_not_accept,
               test_acceptance_needs_a_named_decider,
               test_accepted_state_cannot_be_forged,
               test_requires_human_false_is_not_a_second_path,
               test_a_fleet_terminal_cannot_accept,
               test_a_human_acceptance_spawns_the_work_and_links_it,
               test_the_read_path_cannot_accept):
        print(f"=== {getattr(fn, '_route', fn.__name__)} ===")
        fn()
    print("\n=== the criterion ===")
    test_no_code_path_executes_a_recommendation_without_a_human()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
