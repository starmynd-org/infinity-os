#!/usr/bin/env python3
"""ROW 0438, HIS ASK 7's QUALITY CONDITION: the badge counts what is genuinely waiting on HIM.

`test_the_intake_surface.py` proves the badge exists, is amber, and follows the store down when a
row is accepted. It cannot prove the thing this file is about, because it lands its own rows and
they are all the same kind. What it could never catch is the state his live store was actually in:

    2026-08-31, measured on `brain`:
        id 2  intake-heartbeat-2026-08-29   inbox     the runtime's own daily heartbeat
        id 3  how-intake-works-now          inbox     actually his
        id 4  intake-heartbeat-2026-08-30   inbox     the runtime's own daily heartbeat
        badge reads 3. The answer is 1.

He granted the overrule of MUST-NOT-BUILD item 7 on the condition that the badge counts only what
is waiting on him, and two thirds of it was the machine telling itself it was alive.

FOUR CLAIMS, AND THE THIRD IS THE ONE WORTH HAVING.

  1. AN ORIGIN IS DECLARED, NEVER INFERRED. A lane refused to hardcode `source_name LIKE
     'intake-heartbeat-%'` into the render layer and was right to. This asserts the fact travels
     from the writer, through the door, onto the row.

  2. THE BADGE COUNTS HUMAN ORIGIN ONLY, over a table larger than the count, landed and accepted
     in the shape `test_the_intake_surface` uses: a badge that counted every row would pass every
     markup assertion ever written and fail this one.

  3. UNDECLARED READS AS HUMAN, AND THAT IS THE SAFE DIRECTION. This is the check that has to
     exist, because it is the one that fails loudly if somebody later "tidies" the default to
     machine to make the badge smaller. An undeclared machine row costs him a glance; an
     undeclared human row would never surface at all, which is the failure the badge exists to
     prevent. The asymmetry is the design and it is asserted rather than described.

  4. THE HEARTBEAT STILL LANDS AND IS STILL LISTED. It is excluded from the COUNT and not from the
     ROOM. Its presence is how he sees the push path is alive, and its absence on a given day is
     the signal that it died, so a fix that stopped it landing would have broken the thing it was
     meant to sharpen.

Run:  python3 -m web.tests.test_intake_origin

Needs a scratch store and nothing else: no console, no browser. It TRUNCATEs `brain.objective`
and builds its own fixture, so it must never point at the live store.
"""

from __future__ import annotations

import os as _os
import sys as _sys

_R = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
for _p in (_R, _os.path.join(_R, "engine"), _os.path.join(_R, "queue")):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

import subprocess  # noqa: E402
import sys  # noqa: E402

import store  # noqa: E402
import swarm_engine  # noqa: E402,F401  (import registers the verbs)
from swarm_engine import cli  # noqa: E402
from web import model  # noqa: E402

PASS, FAIL = 0, 0
LIVE = ("brain", "brain_scratch")


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ok    {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def guard():
    """THE STORE THIS SUITE MAY TRUNCATE. There is no verb that deletes an objective, which is
    exactly why the refusal is here rather than in a comment."""
    db = _os.environ.get("BRAIN_PG_DB", "brain")
    if db in LIVE:
        print(f"REFUSING to run against {db!r}: this suite TRUNCATEs brain.objective and there is "
              f"no verb that undoes it. Export BRAIN_PG_DB to a scratch database.")
        sys.exit(2)
    return db


def has_origin() -> bool:
    with store.read() as s:
        return bool(s.scalar(
            "SELECT count(*) > 0 FROM information_schema.columns "
            " WHERE table_schema = 'brain' AND table_name = 'objective' "
            "   AND column_name = 'origin'"))


def reset():
    subprocess.run([_os.path.join(_R, "engine/bin/scratch-db.sh"), "psql", "-q", "-c",
                    "TRUNCATE brain.objective CASCADE"],
                   env={**_os.environ, "ENGINE_SCRATCH_DB": _os.environ["BRAIN_PG_DB"]},
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def land(name, origin=None, body="x"):
    return store.apply("intake", name=name, body=body, source_name=f"{name}.md",
                       source_signature=f"{abs(hash(name)) % 100000}:1", bytes_=len(body),
                       origin=origin)


# ------------------------------------------------------------------ 1. declared, never inferred

def test_the_origin_is_declared_at_the_door():
    reset()
    r = land("a machine row", origin="machine")
    check("the door records a declared machine origin", r.get("origin") == "machine", str(r))
    r = land("a human row", origin="human")
    check("and a declared human origin", r.get("origin") == "human", str(r))
    r = land("a row that said nothing")
    check("an undeclared row records NOTHING rather than a guess", r.get("origin") is None, str(r))
    # A third word is a typo, and a typo that stored would read as human forever with nobody told.
    try:
        land("a row that said something else", origin="robot")
        check("an unrecognised origin is refused", False, "it was accepted")
    except Exception as e:                                          # noqa: BLE001
        check("an unrecognised origin is refused", True)
        check("  and the refusal names the two words that are allowed",
              "human" in str(e) and "machine" in str(e), str(e)[:90])


def test_the_file_declares_itself_and_the_sweep_reads_it():
    """THE PART A FLAG ALONE COULD NOT DO. `var/intake` is ONE folder swept by ONE timer and it
    holds both kinds: on 2026-08-31 it carried two n8n heartbeats and one file a human put there.
    So the declaration has to be able to ride the file."""
    machine = "---\norigin: machine\n---\n\n# Intake heartbeat\n"
    check("a leading front matter block declares machine",
          cli._declared_origin(machine) == "machine")
    check("a file with no front matter declares nothing",
          cli._declared_origin("# just a heading\n") is None)
    check("a front matter block with no origin key declares nothing",
          cli._declared_origin("---\ntitle: x\n---\n") is None)
    # THE ONES THAT MATTER: nothing a person can type into a plain markdown file may make a row
    # QUIET by accident. Every unreadable shape has to fall through to undeclared, which is human.
    for weird in ("origin: machine\n",                       # not in a block
                  "# h\n\n---\norigin: machine\n---\n",      # block is not first
                  "---\n",                                   # unterminated
                  "",                                        # empty
                  "----\norigin: machine\n----\n"):          # not a front matter fence
        check(f"nothing quiet comes from {weird[:18]!r}", cli._declared_origin(weird) is None,
              str(cli._declared_origin(weird)))


# ------------------------------------------------------------------ 2 and 3. the count

def test_the_badge_counts_human_origin_over_a_larger_table():
    """LAND N, ACCEPT SOME, ASSERT THE BADGE OVER A LARGER TABLE. The shape
    `test_the_intake_surface` uses, with the one variable it could not vary."""
    if not has_origin():
        check("PRECONDITION: this store has brain.objective.origin (ledger 50)", False,
              "apply migrations/0050_intake_declares_its_origin.sql")
        return
    reset()
    check("the zero state first, so what follows is a change and not a coincidence",
          model.intake_waiting_count() == 0, str(model.intake_waiting_count()))

    # His live shape, reproduced: two heartbeats and one of his own.
    land("intake-heartbeat-2026-08-29", origin="machine")
    land("how-intake-works-now")
    land("intake-heartbeat-2026-08-30", origin="machine")
    check("THE BADGE READS 1 WHERE IT READ 3", model.intake_waiting_count() == 1,
          f"badge reads {model.intake_waiting_count()}")
    d = model.intake()
    check("  over a table of 3 inbox rows, so the count is narrower than the table",
          d["inbox_n"] == 3 and d["waiting_n"] == 1, str({k: d[k] for k in ("inbox_n", "waiting_n")}))
    check("  and the room says how many it did not count",
          d["machine_n"] == 2, str(d["machine_n"]))

    # Now widen the table further and accept one, the way the existing suite does.
    land("a second thing of his")
    land("a third thing of his")
    check("the badge follows the store up to 3", model.intake_waiting_count() == 3,
          str(model.intake_waiting_count()))
    store.apply("accept", name="a second thing of his")
    check("and back down to 2 when one is accepted", model.intake_waiting_count() == 2,
          str(model.intake_waiting_count()))
    with store.read() as s:
        total = int(s.scalar("SELECT count(*) FROM brain.objective"))
    check(f"while the table holds {total}, which is the denominator the badge is NOT",
          model.intake_waiting_count() != total, f"badge {model.intake_waiting_count()} of {total}")


def test_undeclared_reads_as_human_and_that_direction_is_the_safe_one():
    """THE CHECK THAT EXISTS TO FAIL IF SOMEBODY LATER TIDIES THE DEFAULT.

    Making the default `machine` would shrink the badge and look like an improvement. It would
    also mean any producer that has not been taught to declare itself can drop something on his
    board that he is never shown. This asserts the asymmetry on purpose so that change cannot be
    made quietly."""
    if not has_origin():
        check("PRECONDITION: this store has brain.objective.origin (ledger 50)", False,
              "apply migrations/0050_intake_declares_its_origin.sql")
        return
    reset()
    land("a producer that never declared itself")
    check("an undeclared row IS counted, because undeclared reads as human",
          model.intake_waiting_count() == 1, str(model.intake_waiting_count()))
    with store.read() as s:
        check("  and the store itself folds NULL to human, in one place both surfaces read",
              s.scalar("SELECT brain.objective_origin(NULL)") == "human")
        check("  as it does for an unreadable value",
              s.scalar("SELECT brain.objective_origin('nonsense')") == "human")
        check("  while only an explicit machine is quiet",
              s.scalar("SELECT brain.objective_origin('machine')") == "machine")


# ------------------------------------------------------------------ 4. the heartbeat survives

def test_the_heartbeat_still_lands_and_is_still_listed():
    """ITS ABSENCE IS THE SIGNAL, SO ITS PRESENCE HAS TO BE REAL. A fix that stopped the heartbeat
    landing, or hid it from the room, would have broken the thing it was sharpening."""
    if not has_origin():
        check("PRECONDITION: this store has brain.objective.origin (ledger 50)", False,
              "apply migrations/0050_intake_declares_its_origin.sql")
        return
    reset()
    r = land("intake-heartbeat-2026-08-31", origin="machine")
    check("the heartbeat still LANDS", r["taken"] is True, str(r))
    with store.read() as s:
        check("  as a real row in brain.objective, in state inbox",
              s.scalar("SELECT state FROM brain.objective WHERE name = %s",
                       ("intake-heartbeat-2026-08-31",)) == "inbox")
    d = model.intake()
    names = [w["name"] for w in d["waiting"]]
    check("  and it is still LISTED in the room, not hidden",
          "intake-heartbeat-2026-08-31" in names, str(names))
    check("  labelled machine, so the room says why the badge skipped it",
          [w["origin"] for w in d["waiting"]] == ["machine"], str(d["waiting"]))
    check("  and the badge does not count it", model.intake_waiting_count() == 0,
          str(model.intake_waiting_count()))


def main() -> int:
    db = guard()
    print(f"  store: {db} (scratch)\n")
    for fn in (test_the_origin_is_declared_at_the_door,
               test_the_file_declares_itself_and_the_sweep_reads_it,
               test_the_badge_counts_human_origin_over_a_larger_table,
               test_undeclared_reads_as_human_and_that_direction_is_the_safe_one,
               test_the_heartbeat_still_lands_and_is_still_listed):
        print(f"=== {fn.__name__} ===")
        fn()
    # DENOMINATOR. Three of the five scenes return early without asserting when the store is
    # below ledger 50, so a run against an unmigrated store could otherwise print a green over
    # nothing. Task 0292's rule, and the reason this suite states its precondition as a FAILED
    # check rather than as a skip.
    if PASS + FAIL == 0:                                                # DENOMINATOR
        print("\n0 assertions made. A verdict over an empty set is not a pass. Check the store "
              "is at ledger 50 and rerun.")
        return 2
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
