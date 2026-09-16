#!/usr/bin/env python3
"""The falsifier does not fire from an empty denominator. Task 0382.

THE DEFECT, FOUND BY LANE A ON 2026-08-27 WHILE USING THE LAYER AND REPRODUCED IN ONE COMMAND.

`queue acted-on` reported the recommendation layer's own falsifier as FIRED while nothing had
been decided. Live store, unedited:

    $ python3 queue/bin/queue acted-on
    recommendations raised 3, accepted 0, decided 0
    acted-on rate: 0.0  (falsifier: below 30% this layer is cut back to events plus paging)
      BELOW THE FALSIFIER: cut this layer back to events plus paging
      (novel)                          raised   3  accepted   0  rate 0.0000

Every number is correct and the sentence is false. Three recommendations were sitting OPEN in the
operator's queue awaiting the one act that is his alone, and the layer read that as evidence that
it should be deleted. `reads.acted_on` guarded `raised == 0` and never extended the guard to
`decided == 0`.

WHY IT IS WORSE THAN THE ZERO IT REPLACED. Before any recommendation existed the falsifier could
not fire in either direction and everyone knew it; the 2026-08-16 UX review wrote down "the layer
correctly refuses to render 0 %". Adding real data turned a visible absence into a confident
wrong answer, and a confident wrong answer recommending an irreversible act is the worst state
this program has a rule against.

WHAT IS ASSERTED HERE

  scene 1  raised 0                  -> undefined, unchanged, and no verdict
  scene 2  THE DEFECT: raised > 0, decided 0 -> NOT MEASURABLE, and the words `BELOW THE
           FALSIFIER` must NOT appear. This is the scene that goes red against the old code.
  scene 3  a real decision arrives  -> the falsifier fires again, so scene 2 is a guard and not
           a mute button. Without this, "it stopped saying BELOW THE FALSIFIER" is equally well
           explained by a check that can no longer say anything.
  scene 4  the BAR IS UNTOUCHED at 0.30, read out of the source, because the tempting wrong fix
           to a falsifier that fires is to move it
  scene 5  the CLI exits 2 on a zero denominator, this repo's convention, so a caller reading
           `$?` can tell "not measured" from "measured and fine"

Builds its own database and drops nothing else. Never `brain`, never `brain_scratch`.

Run: QUEUE_SCRATCH_DB=brain_q_acted python3 queue/tests/test_acted_on_denominator.py
"""
from __future__ import annotations

import inspect
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for _p in (str(ROOT), str(ROOT / "engine"), str(ROOT / "queue")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

sys.path.insert(0, str(HERE))
from _scratch_preflight import reconcile                                # noqa: E402

reconcile()

import store                                                            # noqa: E402
from human_queue import reads                                           # noqa: E402

PASS = FAIL = 0
FAILURES: list[str] = []


# `.get()` and not `[...]` on the keys this task ADDED, per docs/SCHEMA-TOLERANCE.md rule 2 and
# for a second reason that matters here: this suite must be runnable against the PRE-FIX reader to
# demonstrate its scenes failing. A KeyError is not a demonstration, it is a crash, and a crash
# proves nothing about which assertion would have caught the defect.


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok    {name}")
    else:
        FAIL += 1
        FAILURES.append(name)
        print(f"  FAIL  {name}")
        for line in str(detail).strip().splitlines()[:8]:
            print(f"        {line}")


def main() -> int:
    db = os.environ.get("BRAIN_PG_DB", "")
    print(__doc__.splitlines()[0])
    print(f"\n  database {db!r}")

    import psycopg2

    # The superuser connection the sibling suites use to plant a fixture state. Same reasoning as
    # `test_no_self_execution.py:_sql`: the states below are the INPUT to the reader under test,
    # not a claim about who may write them.
    _pw = (Path.home() / ".brain-postgres-secrets"
           / "brain-postgres-bootstrap-superuser").read_text().strip()

    def run_sql(statement, params=()):
        """A direct write, used ONLY to build the fixture STATES this reader must classify.

        The object under test is a READ. Driving these states through `recommend accept` would
        need the operator LOGIN (migration 32), which is the credential `queue/tests/run-all.sh`
        already declares NOT RUN when it is absent, and this suite is about a denominator rather
        than about that gate. `test_recommendation_human_login.py` is what proves the gate, and
        nothing here asserts anything about who may decide.
        """
        conn = psycopg2.connect(host="127.0.0.1", port=5432,
                                dbname=os.environ["BRAIN_PG_DB"], user="postgres",
                                password=_pw)
        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(statement, params)
        finally:
            conn.close()

    def as_operator(statement, params=()):
        """A DECISION, written from the operator LOGIN, because nothing else may write one.

        Migration 32's trigger refuses a rejection even from `postgres` -- measured while writing
        this suite: "this connection is postgres, which is not a human login". That gate is
        0290's and 0313's and it is working; this goes through the door rather than around it.
        Raises when the credential is absent, and the caller declares NOT RUN.
        """
        from store import session
        conn = psycopg2.connect(**session.dsn("operator"))
        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(statement, params)
        finally:
            conn.close()

    run_sql("DELETE FROM brain.recommendation")

    print("\nscene 1: nothing recommended -- unchanged, and still undefined")
    a = reads.acted_on()
    check(f"raised {a['raised']}, rate {a['acted_on_rate']!r}",
          a["raised"] == 0 and a["acted_on_rate"] is None, a)
    check("the verdict says undefined rather than zero", "undefined" in a["verdict"], a["verdict"])
    check("and it is not measurable", a.get("measurable") is False, a)
    check("and it does NOT say BELOW THE FALSIFIER",
          "BELOW THE FALSIFIER" not in a["verdict"], a["verdict"])

    print("\nscene 2: THE DEFECT -- 3 raised, 0 decided")
    for i in (1, 2, 3):
        run_sql("INSERT INTO brain.recommendation "
                "  (id, subject_type, subject_id, text, rationale, requires_human, state) "
                "VALUES (%s, 'work_item', %s, %s, 'fixture', true, 'open')",
                (i, f"000{i}", f"an open recommendation {i}"))
    a = reads.acted_on()
    check(f"raised {a['raised']}, decided {a['decided']}, still open {a.get('still_open')}",
          (a["raised"], a["decided"], a.get("still_open")) == (3, 0, 3), a)
    check("it is NOT measurable", a.get("measurable") is False, a)
    check("the verdict says NOT MEASURABLE", a["verdict"].startswith("NOT MEASURABLE"),
          a["verdict"])
    # THE ASSERTION THAT GOES RED AGAINST THE OLD CODE.
    check("and it does NOT say BELOW THE FALSIFIER over 0 decisions",
          "BELOW THE FALSIFIER" not in a["verdict"], a["verdict"])
    check("and it says in words that this is not a 0 percent rate",
          "NOT a 0 percent acted-on rate" in a["verdict"], a["verdict"])

    print("\nscene 3: CONTROL -- a real decision, and the falsifier fires again")
    # A DECISION IS WRITTEN THROUGH THE OPERATOR LOGIN, BECAUSE NOTHING ELSE MAY WRITE ONE.
    # Measured while writing this suite: migration 32's trigger refuses a rejection even from
    # `postgres`, with "this connection is postgres, which is not a human login". That gate is
    # 0290/0313's and it is working exactly as designed; this scene goes through the door rather
    # than around it, and asserts nothing about who may decide.
    have_decision = True
    try:
        as_operator("UPDATE brain.recommendation SET state = 'rejected', decided_at = now(), "
                    "decided_by = brain.current_human(), actor_type = 'human' WHERE id = 1")
    except (psycopg2.Error, OSError, KeyError, FileNotFoundError) as exc:
        # DELIBERATELY NARROW. A bare `except Exception` here swallowed a NameError in this
        # file's own first draft and reported it as "no operator credential", which is the shape
        # of mistake this whole suite is about: an absent measurement dressed as a finding.
        have_decision = False
        # NOT RUN, with the condition and the remedy, per docs/SUITE-INPUT-RULE.md. This is the
        # same operator credential `queue/tests/run-all.sh` already declares six suites NOT RUN
        # over, so the idiom is copied rather than invented.
        print(f"  NOT RUN  scene 3, the control")
        print(f"           condition: no decision can be written from this connection. "
              f"{type(exc).__name__}: {str(exc).splitlines()[0]}")
        print(f"           remedy:    store/bin/provision-operator.sh --db "
              f"{os.environ.get('BRAIN_PG_DB')}")
        print(f"           Scenes 1, 2, 4 and 5 ran. Only the proof that the guard is not a MUTE "
              f"BUTTON stands down, and that is the half worth shouting about.")
    if have_decision:
        a = reads.acted_on()
        check(f"raised {a['raised']}, decided {a['decided']}, accepted {a['accepted']}",
              (a["raised"], a["decided"], a["accepted"]) == (3, 1, 0), a)
        check("it IS measurable now", a.get("measurable") is True, a)
        check("and the falsifier fires, so scene 2 is a guard and not a mute button",
              a["verdict"].startswith("BELOW THE FALSIFIER"), a["verdict"])
        check(f"acted_on_rate is accepted/raised = {a['acted_on_rate']}",
              a["acted_on_rate"] == 0.0, a)
        check(f"and decided_rate is accepted/decided = {a.get('decided_rate')}, printed beside it "
              f"rather than instead of it", a.get("decided_rate") == 0.0, a)

        as_operator("UPDATE brain.recommendation SET state = 'accepted', decided_at = now(), "
                    "decided_by = brain.current_human(), actor_type = 'human' WHERE id IN (2, 3)")
        a = reads.acted_on()
        check(f"and a healthy layer reads above the threshold: {a['accepted']} accepted of "
              f"{a['raised']} raised = {a['acted_on_rate']}",
              a["verdict"] == "above the falsifier threshold", a)
        check(f"with all three decided the two readings agree: {a['acted_on_rate']} over "
              f"raised and {a.get('decided_rate')} over decided, {a.get('still_open')} still open",
              a["acted_on_rate"] == a.get("decided_rate") == 0.6667 and a.get("still_open") == 0, a)

        # AND THE CASE THAT MAKES THE SECOND READING WORTH PRINTING. One more raised and left
        # open, and the two numbers part company: 2 accepted of 4 raised is 0.5000, which is over
        # the bar, while 2 of 3 decided is 0.6667. On a deep queue the gap is the whole verdict --
        # 3 accepted of 5 decided of 100 raised reads 3 percent one way and 60 percent the other,
        # and only one of those is a statement about whether the playbooks are any good. WHICH
        # ONE THE FALSIFIER MEANS IS NOT SETTLED HERE. It is PLAN.md's number and the operator's
        # call, and it is on the bus as its own row. What this scene pins is that both are
        # reported, so nobody has to guess which they are reading.
        run_sql("INSERT INTO brain.recommendation "
                "  (id, subject_type, subject_id, text, rationale, requires_human, state) "
                "VALUES (4, 'work_item', '0004', 'a fourth, left open', 'fixture', true, 'open')")
        a = reads.acted_on()
        check(f"the two readings diverge once something is still open: "
              f"{a['acted_on_rate']} over {a['raised']} raised, {a.get('decided_rate')} over "
              f"{a['decided']} decided, {a.get('still_open')} still open",
              a["acted_on_rate"] == 0.5 and a.get("decided_rate") == 0.6667
              and a.get("still_open") == 1, a)
        check("and the verdict is still computed from the published rate, unchanged",
              a["verdict"] == "above the falsifier threshold", a["verdict"])

    print("\nscene 4: the bar is where PLAN.md put it")
    src = inspect.getsource(reads.acted_on)
    check("the falsifier threshold is still 0.30 in the returned payload",
          a["falsifier_threshold"] == 0.30, a["falsifier_threshold"])
    bars = sorted(set(re.findall(r"< 0\.(\d+)", src)))
    check(f"and the source compares against 0.30 and nothing else (found {bars})",
          bars == ["30"], bars)
    check("positive control: the scan can see the comparison at all", "< 0.30" in src)

    print("\nscene 5: the CLI exits on the zero denominator")
    run_sql("UPDATE brain.recommendation SET state = 'open'")
    env = {**os.environ}
    p = subprocess.run([sys.executable, str(ROOT / "queue" / "bin" / "queue"), "acted-on"],
                       capture_output=True, text=True, env=env, cwd=str(ROOT))
    check(f"`queue acted-on` exits 2 on an unmeasurable rate (rc={p.returncode})",
          p.returncode == 2, p.stdout + p.stderr)
    check("and prints NOT MEASURABLE with what it compared on the same line",
          re.search(r"acted-on rate: NOT MEASURABLE\s+\(compared 0 decision\(s\) out of \d+ raised",
                    p.stdout) is not None, p.stdout)
    check("and does not print the cut-the-layer instruction",
          "BELOW THE FALSIFIER" not in p.stdout, p.stdout)
    p = subprocess.run([sys.executable, str(ROOT / "queue" / "bin" / "queue"), "acted-on",
                        "--json"], capture_output=True, text=True, env=env, cwd=str(ROOT))
    j = json.loads(p.stdout)
    check("--json carries `measurable` so a machine reader can tell the two apart",
          j.get("measurable") is False, j)

    run_sql("DELETE FROM brain.recommendation")

    print()
    if PASS + FAIL == 0:                                                # DENOMINATOR
        print("0 comparisons made. A verdict over an empty set is not a pass.")
        return 2
    print(f"{PASS} passed, {FAIL} failed")
    if FAILURES:
        print("FAILED: " + ", ".join(FAILURES))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
