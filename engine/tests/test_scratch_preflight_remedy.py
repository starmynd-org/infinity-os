#!/usr/bin/env python3
"""The scratch preflight's refusal must never advise the command that empties the live bus. 0353.

WHAT THE DEFECT WAS. `_scratch_preflight.reconcile()` refuses when `BRAIN_PG_DB` and
`ENGINE_SCRATCH_DB` name different databases, which is correct: reconciling one store and asserting
against another is the skew the preflight exists to stop. But its remedy line was formatted
`% (db, target, target, db, db)`, so both exports came back filled with `db` -- the caller's
`BRAIN_PG_DB`, which in any fleet terminal is `brain`, the live store. The very next sentence of its
own refusal says `reset()` TRUNCATEs whatever `ENGINE_SCRATCH_DB` names. So an agent that pasted the
suggested command, which is the single most likely thing an agent does with a remedy line, emptied
`work_item`, `thread` and `artifact` on the live bus. The guard was right and its advice was the
hazard. Found on task 0328 by T1, who did not run it.

WHY THE FIX IS STRUCTURAL AND NOT A BETTER STRING. In the branch that prints the remedy,
`db != target` holds by construction. So `db` is BY DEFINITION not the scratch database, and
`target` is the name the mismatch itself just called wrong. NEITHER INPUT IS SAFE TO ECHO, and no
amount of knowing which name is "live" is needed to see that. The remedy now derives a name instead:
per-lane `brain_<agent>_<task>` inside a fleet terminal, `brain_scratch` outside one.

WHAT THIS SUITE ASSERTS, and the reason it is a suite rather than a comment: the failure mode is a
STRING, and strings get reworded by people who did not read this docstring. Six scenes, each with
its denominator printed.

  1. the refusal still refuses            -- exit 1, so the fix did not disarm the guard
  2. no export in the remedy names the live store  -- the defect itself, over EVERY export line found
  3. the remedy names a derived scratch database and says how to build it
  4. inside a fleet terminal the suggestion is per-lane, not the shared scratch
  5. outside one it is `brain_scratch`
  6. POSITIVE CONTROL: the scan in scene 2 catches a planted bad remedy

SCENE 6 IS THE ONE THAT MAKES 1-5 WORTH READING. A scan for "does this text contain a dangerous
export" passes trivially against text it failed to parse, which is this program's most-measured
failure: a verifier that passed because it compared NOTHING. So the same scanner is pointed at a
string that IS the old defect, and is required to catch it.

NEEDS NO DATABASE. Every scene exits inside the mismatch branch, which returns before `reconcile`
runs `scratch-db.sh` or opens a connection. That is deliberate: a suite about a TRUNCATE hazard
should not need the store it is protecting.

Run: python3 engine/tests/test_scratch_preflight_remedy.py
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

_TESTS = Path(__file__).resolve().parent
_REPO = _TESTS.parents[1]
_LIVE = "brain"

PASS = 0
FAIL = 0


def check(label, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print("  ok   %s" % label)
    else:
        FAIL += 1
        print("  FAIL %s%s" % (label, ("  -- " + detail) if detail else ""))


def refusal(env_extra, db=_LIVE, scratch="brain_scratch"):
    """Run reconcile(db) in a child and return (returncode, stderr).

    A child process because the refusal is a `sys.exit(1)`, and because the suggestion reads the
    environment -- setting it here would leak into every later scene.
    """
    env = dict(os.environ)
    env.pop("SWARM_AGENT", None)
    env.pop("SWARM_PARENT_TASK", None)
    env["ENGINE_SCRATCH_DB"] = scratch
    env.update(env_extra)
    code = (
        "import sys; sys.path.insert(0, %r);"
        "import _scratch_preflight as p; p.reconcile(%r)" % (str(_TESTS), db)
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       cwd=str(_REPO), env=env)
    return r.returncode, r.stderr


#: Every `NAME=value` on an export line in the remedy. The scanner scene 2 uses and scene 6 proves.
_EXPORT = re.compile(r"\b(ENGINE_SCRATCH_DB|BRAIN_PG_DB)=(\S+)")

#: The same, widened to any lane's scratch variable, for the census in scene 7. Kept separate from
#: `_EXPORT` so that scene 6's positive control still proves the exact scanner scene 2 relies on:
#: widening the one under control would have quietly changed what the control controls.
_ANY_EXPORT = re.compile(r"\b([A-Z][A-Z0-9_]*(?:SCRATCH_DB|PG_DB))=(\S+)")


def dangerous_exports(text):
    """Exports in `text` that point a suite at the live store. Returns (findings, examined)."""
    found = _EXPORT.findall(text)
    return [f for f in found if f[1] == _LIVE], found


def _census(repo_root):
    """Every copy of `_scratch_preflight.py` under `repo_root`, and the copies excluded as scratch
    clones. THE EXCLUSION IS ANCHORED BELOW THE REPO ROOT: only a `_scratch-*` segment INSIDE the
    repo marks a scratch clone. A `_scratch-*` segment ABOVE the root is where the repo happens to
    live (the Multiverse worktrees sit under `_scratch-infinity/worktrees/`), and reading it as an
    exclusion swallowed the entire tree: every copy skipped, zero examined, and only the denominator
    check below kept that from reporting a clean pass over nothing. Found 2026-09-07 by Terminal 04.
    """
    root = Path(repo_root)
    skipped, copies = [], []
    for p in sorted(root.rglob("_scratch_preflight.py")):
        parts = p.relative_to(root).parts          # BELOW the root, never the ancestors
        if ".git" in parts or "outputs" in parts:
            continue
        if any(seg.startswith("_scratch-") for seg in parts):
            skipped.append(str(p.relative_to(root)))
            continue
        copies.append(p)
    return copies, skipped


def main():
    print("test_scratch_preflight_remedy.py -- task 0353")

    print("\nscene 1: the guard still refuses a mismatch")
    rc, err = refusal({})
    check("exit code is 1", rc == 1, "got %r" % rc)
    check("it says something", len(err.strip()) > 0, "empty stderr")

    print("\nscene 2: no export in the remedy names the live store %r" % _LIVE)
    bad, examined = dangerous_exports(err)
    # BOTH SIDES NONEMPTY BEFORE ANY VERDICT. A remedy whose exports this scanner could not find is
    # a pass over an empty set, which is the thing scene 6 exists to make impossible to ship.
    check("the scanner found export lines to judge (denominator)", len(examined) >= 2,
          "examined %d, expected at least 2" % len(examined))
    check("none of the %d exports names %r" % (len(examined), _LIVE), not bad, repr(bad))

    print("\nscene 3: the remedy names a scratch database and says how to build it")
    check("it names scratch-db.sh create", "scratch-db.sh create" in err)
    check("it warns against exporting the live name into both",
          "DO NOT resolve this by exporting" in err)
    suggested = {v for _, v in examined}
    check("every suggested database is a scratch name (denominator %d)" % len(suggested),
          bool(suggested) and all(s.startswith("brain_") for s in suggested), repr(suggested))

    print("\nscene 4: inside a fleet terminal the suggestion is per-lane")
    rc, err4 = refusal({"SWARM_AGENT": "T3", "SWARM_PARENT_TASK": "0353"})
    bad4, examined4 = dangerous_exports(err4)
    check("still refuses", rc == 1, "got %r" % rc)
    check("the scanner found exports (denominator)", len(examined4) >= 2, str(len(examined4)))
    check("suggests brain_t3_0353", all(v == "brain_t3_0353" for _, v in examined4),
          repr(sorted({v for _, v in examined4})))
    check("none names %r" % _LIVE, not bad4, repr(bad4))

    print("\nscene 5: outside a fleet terminal it is the shared scratch")
    check("suggests brain_scratch", all(v == "brain_scratch" for _, v in examined),
          repr(sorted(suggested)))

    print("\nscene 6: POSITIVE CONTROL -- the scanner catches the ORIGINAL defect")
    original = (
        "preflight: BRAIN_PG_DB is brain but scratch-db.sh would act on brain_scratch. Not running\n"
        "this suite: ... Export both:\n"
        "    ENGINE_SCRATCH_DB=brain BRAIN_PG_DB=brain <suite>\n"
    )
    bad6, examined6 = dangerous_exports(original)
    check("it examined the planted exports (denominator)", len(examined6) == 2,
          "examined %d, expected 2" % len(examined6))
    check("it caught both of them", len(bad6) == 2, repr(bad6))

    print("\nscene 7: THE CENSUS -- every copy of this preflight in the tree, not just ours")
    # WHY THIS SCENE EXISTS, AND IT IS A CORRECTION TO TASK 0353'S OWN SWEEP.
    #
    # 0353 closed this defect and reported the denominator that proved the fix was complete:
    # "19 files carry an import of _scratch_preflight; 1 site formats the remedy. One site, so one
    # fix covers all 18." Every number in that sentence was true. It was also the wrong
    # population: it counted IMPORTERS OF ONE FILE, and the defect's population is COPIES OF THE
    # FILE. `queue/tests/_scratch_preflight.py` is a separate copy with its own `reconcile`, and it
    # was still printing `% (db, target, target, db, db)` -- both exports filled with the caller's
    # BRAIN_PG_DB, which in a fleet terminal is the live store -- for a full day after 0353 was
    # reported closed. Found 2026-08-23 by running a queue suite with mismatched names and reading
    # the remedy it printed.
    #
    # So this scene stops asking "is OUR copy fixed" and asks "is EVERY copy fixed", by finding
    # them rather than by naming them. A new lane that copies this pattern is covered on the day it
    # lands, which is the only way a duplicated-file defect stays closed.
    # WHAT IS EXCLUDED AND WHY, because a census that quietly drops rows is the defect this whole
    # file is about. Three exclusions, each for a stated reason rather than to make this green:
    #   .git/        not source.
    #   outputs/     report directories and the four embedded repo exports under
    #                2026-08-19-T2-0336; `denominator-lint.py` prunes the same copies for the same
    #                reason, and linting a frozen export tells you about 08-19, not about now.
    #   _scratch-*/  per-agent per-task scratch CLONES of the whole repo, gitignored at
    #                `.gitignore:21`. `_scratch-t3-0100/mutant/` is a 97-file copy and it DOES
    #                still carry the original defect -- verified by this scan before excluding it.
    #                It is excluded because nothing ships or runs it: it is untracked
    #                (`git ls-files` does not know the path) and `git check-ignore` names the rule.
    #                A defect in a frozen mutant fixture is not a hazard to the live bus.
    # An UNTRACKED copy outside `_scratch-*/` is deliberately NOT excluded: a new lane's copy, added
    # and not yet committed, is exactly the case this scene exists to catch before it lands.
    copies, skipped = _census(_REPO)
    check("the census found more than one copy (denominator)", len(copies) > 1,
          "found %d: %s" % (len(copies), [str(p.relative_to(_REPO)) for p in copies]))
    print("       examined: %s" % ", ".join(str(p.relative_to(_REPO)) for p in copies))
    print("       excluded %d gitignored scratch clone(s): %s"
          % (len(skipped), ", ".join(skipped) if skipped else "none"))

    # SCENE 8: THE EXCLUSION MUST NOT SWALLOW A REPO THAT LIVES UNDER A `_scratch-*` FOLDER.
    # A synthetic repo at <tmp>/_scratch-above/repo/ holding two copies, plus one copy inside its
    # own `_scratch-inner/` clone. The census must count the two and skip only the inner one. On the
    # absolute-path filter this file carried until 2026-09-07 it counts zero, which is what every
    # Multiverse worktree looked like to it.
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        fake = Path(tmp) / "_scratch-above" / "repo"
        for rel in ("engine/tests/_scratch_preflight.py", "queue/tests/_scratch_preflight.py",
                    "_scratch-inner/engine/tests/_scratch_preflight.py"):
            (fake / rel).parent.mkdir(parents=True, exist_ok=True)
            (fake / rel).write_text("# synthetic copy for the census scene\n")
        f_copies, f_skipped = _census(fake)
        check("a repo under a _scratch-* ancestor still counts its copies (denominator 2)",
              len(f_copies) == 2, "counted %d, skipped %s" % (len(f_copies), f_skipped))
        check("only the _scratch-* clone INSIDE the repo is excluded",
              f_skipped == ["_scratch-inner/engine/tests/_scratch_preflight.py"], repr(f_skipped))
    for c in copies:
        rel = str(c.relative_to(_REPO))
        env = dict(os.environ)
        env.pop("SWARM_AGENT", None)
        env.pop("SWARM_PARENT_TASK", None)
        # Both names set to a non-live scratch, so whichever this copy reads, the mismatch that
        # triggers the remedy is `db` (the live store) against a scratch target.
        env["ENGINE_SCRATCH_DB"] = "brain_scratch"
        env["QUEUE_SCRATCH_DB"] = "brain_queue_scratch"
        code = ("import sys; sys.path.insert(0, %r);"
                "import _scratch_preflight as p; p.reconcile(%r)" % (str(c.parent), _LIVE))
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                           cwd=str(_REPO), env=env)
        bad, examined = _ANY_EXPORT.findall(r.stderr), _ANY_EXPORT.findall(r.stderr)
        naming_live = [e for e in examined if e[1] == _LIVE]
        check("%s refuses the mismatch" % rel, r.returncode != 0,
              "exit %d" % r.returncode)
        check("%s printed an export line to examine (denominator)" % rel, len(examined) > 0,
              "0 exports in its remedy, so the next check compares nothing")
        check("%s names no live store in its remedy" % rel, not naming_live, repr(naming_live))

    # THIS SUITE'S OWN DENOMINATOR. Task 0292, and it is pointed at this file for a specific
    # reason: every scene above learns what it knows from `refusal()`, which shells out to a
    # subprocess. A python that will not start, an import error inside the child, or a rename of
    # the helper leaves every `check` uncalled -- and `0 passed, 0 failed` exits 0, which run-all.sh
    # prints as ALL SUITES GREEN. The suite that proves a destructive remedy was fixed is the last
    # file that should be able to report a pass without comparing anything.
    if PASS + FAIL == 0:                                    # DENOMINATOR
        print("\nDENOMINATOR: 0 comparisons made. A verdict over an empty set is not a pass.")
        return 2
    print("\n%s: %d passed, %d failed (over 8 scenes)"
          % ("PASS" if FAIL == 0 else "FAIL", PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
