#!/usr/bin/env python3
"""Three more verbs that had no door: `observation open`, `observation close`, `disposition record`.

The direct sequel to `test-surface-doors.py`, and the same defect one layer along the spine. That
suite's finding was three transitions no argv reached; these are three more, and they are worse
placed, because they are two whole stages of OODA. Orient and Decide were reachable only by
importing `fabric.emit` and calling `store.apply` from Python. `brain.observation` held five rows
on the live store, all `kind='demo'`, all the identical string, all written 2026-08-16 by a lane
doing exactly that import, and nothing since could have written another one.

`fabric/cli.py` did carry `observe` and `dispose` subcommands, so "0 CLI" was imprecise about
existence. It was right about reachability, which is the thing that matters: fabric is named in 0
of `AGENTS.md`, `CLAUDE.md`, `docs/OPERATING.md` and `roles/terminal.md`, has no bin entry, and is
invoked by no automation in the repo. `observation close` had no CLI and no helper either way.

THE RULE THIS SUITE INHERITS, and it is the reason every check drives the shipped binary as a
subprocess: a test that called `store.apply` would have passed just as well before the doors
existed, because the transitions were always fine. The defect was that nothing reached them.

Run: python3 engine/tests/test_orient_and_decide_doors.py   (a scratch database, never `brain`)
"""

from __future__ import annotations

import os
import subprocess
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
os.environ.pop("SWARM_AGENT", None)

import store                                          # noqa: E402
from fabric import emit as fabric_emit                # noqa: E402

SWARM = str(ROOT / "engine/bin/swarm")
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


def run(*args):
    return subprocess.run([SWARM, *args], capture_output=True, text=True, env=dict(os.environ))


def an_event(kind="pipeline.run.failed"):
    """A real event to observe, emitted through the producer verb rather than INSERTed.

    The type has to be one `fabric/types.py` already registers. That is not a nuisance to work
    around with a test-only type: producers never know their consumers, so a subscriber can only
    declare interest in a type that exists, and inventing one at emit time would make the
    declaration unfalsifiable. `pipeline.run.failed` is registered, carries no after-commit
    producer, and pages nobody.
    """
    fabric_emit.emit(type=kind, external=False, canon_touching=False, actor="T-test",
                     payload_summary="an occurrence for the orient-and-decide doors",
                     check_budget=False)
    with store.read("runtime") as s:
        return s.scalar("SELECT max(event_seq) FROM brain.event")


def obs(oid):
    with store.read("runtime") as s:
        return s.one("SELECT id, event_id, kind, status, closed_at, produced_by, "
                     "produced_by_producer, actor_type FROM brain.observation WHERE id = %s",
                     (oid,))


# ==================================================== 1. observation open, the headline door

def test_the_cli_can_open_an_observation():
    """Before this, no argv on any documented surface reached `observation open`."""
    seq = an_event()
    r = run("observation", "open", "--event-seq", str(seq), "--text", "a real reading",
            "--kind", "suite", "--from", "T-test", "--actor-type", "ai", "--json")
    eq("observation open exits 0", r.returncode, 0)
    import json
    oid = json.loads(r.stdout)["observation_id"]
    got = obs(oid)
    eq("the row points at the event it observes", got["event_id"], seq)
    eq("status starts open", got["status"], "open")
    eq("the kind is the caller's, not `demo`", got["kind"], "suite")


def test_the_producer_name_lands_in_the_producer_column():
    """Migration 17, and getting this wrong would make every row the door writes un-walkable.

    Since 17, `produced_by IS NOT NULL` means "an entity id" UNCONDITIONALLY, because that is the
    only rule a lineage walker can follow that is right about both the nine rows carrying a real
    id and the 2,269 carrying a producer name. A door that stamped its caller into `produced_by`
    would put every row it writes into the second group and re-open exactly that ambiguity.
    """
    seq = an_event()
    import json
    r = run("observation", "open", "--event-seq", str(seq), "--text", "x", "--from", "T-test",
            "--json")
    got = obs(json.loads(r.stdout)["observation_id"])
    eq("produced_by_producer carries the caller's name", got["produced_by_producer"], "T-test")
    eq("produced_by stays NULL: it means an entity id and the caller is not one",
       got["produced_by"], None)


def test_an_observation_of_nothing_is_refused():
    seq = an_event()
    r = run("observation", "open", "--event-seq", str(seq), "--text", "   ")
    truth("empty text is refused", r.returncode != 0, r.stderr)
    r = run("observation", "open", "--event-seq", "999999999", "--text", "x")
    truth("a dangling event sequence is refused", r.returncode != 0, r.stderr)
    truth("and the refusal names the event rather than a constraint",
          "no event with sequence" in r.stderr, r.stderr)


# ==================================================== 2. observation close, which had NO surface

def test_the_cli_can_close_an_observation():
    """`observation close` had neither a CLI nor a helper. On live, 0 of 5 rows were ever closed."""
    seq = an_event()
    import json
    oid = json.loads(run("observation", "open", "--event-seq", str(seq), "--text", "x",
                         "--json").stdout)["observation_id"]
    r = run("observation", "close", str(oid))
    eq("observation close exits 0", r.returncode, 0)
    got = obs(oid)
    eq("status is closed", got["status"], "closed")
    truth("closed_at is stamped", got["closed_at"] is not None, f"row: {got}")


def test_closing_twice_is_nothing_to_do_and_not_an_error():
    """Exit 2 is this repo's `nothing to do`. A second close changes no row, so it is not a 0."""
    seq = an_event()
    import json
    oid = json.loads(run("observation", "open", "--event-seq", str(seq), "--text", "x",
                         "--json").stdout)["observation_id"]
    run("observation", "close", str(oid))
    r = run("observation", "close", str(oid))
    eq("the second close exits 2, not 0 and not 1", r.returncode, 2)


# ==================================================== 3. disposition record

def test_the_cli_can_record_a_disposition_answering_an_observation():
    seq = an_event()
    import json
    oid = json.loads(run("observation", "open", "--event-seq", str(seq), "--text", "x",
                         "--json").stdout)["observation_id"]
    r = run("disposition", "record", "--event-seq", str(seq), "--observation-id", str(oid),
            "--verdict", "acted", "--by", "T-test", "--from", "T-test", "--json")
    eq("disposition record exits 0", r.returncode, 0)
    did = json.loads(r.stdout)["disposition_id"]
    with store.read("runtime") as s:
        got = s.one("SELECT event_id, observation_id, verdict FROM brain.disposition "
                    "WHERE id = %s", (did,))
    eq("it answers the event", got["event_id"], seq)
    eq("and the standing observation", got["observation_id"], oid)


def test_a_disposition_may_answer_an_event_with_no_observation():
    """EF-3: event_id REQUIRED, observation_id NULLABLE. The door must not tighten the contract."""
    seq = an_event()
    r = run("disposition", "record", "--event-seq", str(seq), "--verdict", "noted", "--json")
    eq("a disposition with no observation is allowed", r.returncode, 0)


def test_a_disposition_citing_two_things_must_agree_with_itself():
    """Both foreign keys would be satisfied, so the schema cannot catch this one. The door does.

    An observation opened against event A, cited by a disposition recorded against event B, puts
    a kink in the chain that reads as a real link to anything walking it.
    """
    seq_a, seq_b = an_event(), an_event()
    import json
    oid = json.loads(run("observation", "open", "--event-seq", str(seq_a), "--text", "x",
                         "--json").stdout)["observation_id"]
    r = run("disposition", "record", "--event-seq", str(seq_b), "--observation-id", str(oid),
            "--verdict", "x")
    truth("a cross-event disposition is refused", r.returncode != 0, r.stderr)
    truth("and the refusal names both events", str(seq_a) in r.stderr and str(seq_b) in r.stderr,
          r.stderr)


# ==================================================== 4. the spine, joined

def test_the_chain_joins_end_to_end_from_rows_the_cli_wrote():
    """The gate. Not `the verbs work`, but `what they wrote is walkable`.

    One real row at each hop, each pointing at the last, joined on the real foreign keys. This is
    the query that returned nothing but a five-row 2026-08-16 fixture before these doors existed.
    """
    seq = an_event()
    import json
    oid = json.loads(run("observation", "open", "--event-seq", str(seq), "--text", "walkable",
                         "--kind", "suite", "--from", "T-test", "--json").stdout)["observation_id"]
    run("disposition", "record", "--event-seq", str(seq), "--observation-id", str(oid),
        "--verdict", "acted", "--from", "T-test")
    with store.read("runtime") as s:
        walk = s.one("""
            SELECT e.event_seq, o.id obs, o.kind, d.id disp, d.verdict
              FROM brain.event e
              JOIN brain.observation o ON o.event_id = e.event_seq
              JOIN brain.disposition d ON d.observation_id = o.id AND d.event_id = e.event_seq
             WHERE o.id = %s""", (oid,))
    truth("event -> observation -> disposition joins on real keys", walk is not None,
          "the join returned nothing, which is the CONTRADICTED verdict this suite exists to move")
    if walk:
        eq("and the observation is not a fixture", walk["kind"], "suite")


def test_the_cli_owns_no_sql_for_these_verbs():
    """The narrow waist. `cli.py` calls `store.apply`; it must not have grown a second writer.

    The reads it does are pre-checks that turn a foreign-key traceback into a sentence, and they
    go through `store.read()`, which Postgres holds READ ONLY.
    """
    src = (ROOT / "engine/swarm_engine/cli.py").read_text(encoding="utf-8")
    start = src.index("def cmd_observation_open")
    end = src.index("def cmd_heartbeat")
    body = src[start:end]
    for banned in ("INSERT INTO", "UPDATE brain", "DELETE FROM"):
        truth(f"no `{banned}` in the three new commands", banned not in body,
              "a write outside a registered transition is the one thing this file may not have")
    truth("every state change goes through store.apply", body.count("store.apply(") == 3,
          f"expected 3 store.apply calls, found {body.count('store.apply(')}")


def main():
    print(__doc__.splitlines()[0])
    print("\n-- observation open (was: reachable only by importing fabric.emit)")
    test_the_cli_can_open_an_observation()
    test_the_producer_name_lands_in_the_producer_column()
    test_an_observation_of_nothing_is_refused()
    print("\n-- observation close (was: no CLI and no helper at all)")
    test_the_cli_can_close_an_observation()
    test_closing_twice_is_nothing_to_do_and_not_an_error()
    print("\n-- disposition record (was: reachable only by importing fabric.emit)")
    test_the_cli_can_record_a_disposition_answering_an_observation()
    test_a_disposition_may_answer_an_event_with_no_observation()
    test_a_disposition_citing_two_things_must_agree_with_itself()
    print("\n-- the spine, walked on rows these doors wrote")
    test_the_chain_joins_end_to_end_from_rows_the_cli_wrote()
    test_the_cli_owns_no_sql_for_these_verbs()
    if PASS + FAIL == 0:                                     # DENOMINATOR
        print("\n0 comparisons made over 10 test functions. A verdict over an empty set is not "
              "a pass: every test raised before its first assertion, or the doors are gone and "
              "nothing reached a check.")
        return 2
    print(f"\n{PASS} passed, {FAIL} failed   "
          f"({PASS + FAIL} assertions over 10 test functions on {os.environ['BRAIN_PG_DB']})")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
