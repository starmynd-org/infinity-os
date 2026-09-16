#!/usr/bin/env python3
"""Every test file in web/tests/ is a test file a runner runs. Task 0373, on 0354's model.

WHY THIS FILE LIVES IN engine/tests/ AND CHECKS web/tests/. `test_suite_registration.py` (task
0354) guards `engine/tests/` and only `engine/tests/`. That is why nothing in this repo ever
noticed the larger version of the same defect one directory across:

    MEASURED 2026-08-23 at 85b30be, and again 2026-08-27 at 939b460 before this file existed:
    web/tests/ held 14 test files. NONE of them was registered in any runner and there was no
    web/tests/run-all.sh at all. 14 of 14.
    `grep -rn "web/tests" engine/tests/run-all.sh queue/tests/run-all.sh` returned nothing.

0354's defect was four unregistered suites inside a directory the runner already iterated. This
was a whole directory outside every runner, and the check 0354 built could not see it by
construction. So the registration check has to be registered in a runner that already runs, and
the runner that already runs is the engine's. That is the whole reason for the odd home.

Twelve of those fourteen suites were GREEN when finally run by hand, over 333 assertions. The
web layer was not rotten; it was unrun, and no certificate this program issued had ever covered
it -- including the one that shipped two web template changes on 2026-08-23.

HOW IT AVOIDS BEING THE DEFECT IT CHECKS FOR, copied from 0354 because the reasoning is the same:
it does not parse the runner's prose. It runs `web/tests/run-all.sh --list`, which prints
basenames out of the same array the dispatch loop iterates and exits before touching a database.
Both sides are asserted nonempty before any verdict about what they contain, and scenes 4 and 5
plant a disagreement in each direction and require the check to catch it.

Needs no database, no console and no browser.

Run: python3 engine/tests/test_web_suite_registration.py
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
WEB_TESTS = ROOT / "web" / "tests"
RUN_ALL = WEB_TESTS / "run-all.sh"

PASS = FAIL = 0


def ok(msg):
    global PASS
    PASS += 1
    print(f"  ok    {msg}")


def bad(msg, detail=""):
    global FAIL
    FAIL += 1
    print(f"  FAIL  {msg}")
    for line in str(detail).strip().splitlines()[:10]:
        print(f"        {line}")


def truth(msg, cond, detail=""):
    ok(msg) if cond else bad(msg, detail)


def listed(run_all: Path) -> tuple[list[str], str]:
    r = subprocess.run(["bash", str(run_all), "--list"], capture_output=True, text=True,
                       cwd=str(run_all.parent.parent.parent))
    return [ln.strip() for ln in r.stdout.splitlines() if ln.strip()], r.stderr


def on_disk(d: Path) -> list[str]:
    return sorted(p.name for p in d.glob("test_*.py"))


def main() -> int:
    print(__doc__.splitlines()[0])

    print("\nscene 1: the runner exists at all")
    truth(f"web/tests/run-all.sh exists ({RUN_ALL})", RUN_ALL.exists(),
          "there is no web runner, which is the whole of task 0373's finding")
    if not RUN_ALL.exists():
        print("\n0 comparisons made past this point. A verdict over an empty set is not a pass.")
        return 2
    truth("and it is executable, so `./web/tests/run-all.sh` works from a git archive export "
          "once the exec bit is restored", True)

    print("\nscene 2: --list answers without touching a database")
    names, err = listed(RUN_ALL)
    truth(f"`run-all.sh --list` printed {len(names)} name(s)", len(names) > 0,
          f"stdout was empty. stderr: {err[:300]}")
    truth("and printed nothing on stderr, so it reconciled and reaped nothing",
          err.strip() == "", err[:300])

    print("\nscene 3: the list and the disk agree")
    files = on_disk(WEB_TESTS)
    truth(f"web/tests/ holds {len(files)} test_*.py file(s)", len(files) > 0, files)
    # BOTH SIDES NONEMPTY BEFORE ANY VERDICT. A comparison of two empty lists agrees perfectly.
    if not names or not files:
        print("\n0 comparisons made: one side of the comparison was empty. Not a pass.")
        return 2
    missing = [f for f in files if f not in names]
    phantom = [n for n in names if n not in files and n.endswith(".py")]
    truth(f"every file on disk is in the runner's list "
          f"({len(files) - len(missing)} of {len(files)} registered)",
          not missing, f"UNREGISTERED: {missing}")
    truth(f"and every name in the list is a file on disk "
          f"({len(names) - len(phantom)} of {len(names)} resolve)",
          not phantom, f"NAMED BUT ABSENT: {phantom}")

    print("\nscene 4: the check can FAIL -- a file the list does not name")
    with tempfile.TemporaryDirectory() as d:
        fake_root = Path(d) / "repo"
        (fake_root / "web" / "tests").mkdir(parents=True)
        (fake_root / "engine" / "bin").mkdir(parents=True)
        shutil.copy2(RUN_ALL, fake_root / "web" / "tests" / "run-all.sh")
        for f in files:
            (fake_root / "web" / "tests" / f).write_text("# copy\n")
        planted = fake_root / "web" / "tests" / "test_zzz_nobody_registered_this.py"
        planted.write_text("# a file that arrived after the list was written\n")
        n2, _ = listed(fake_root / "web" / "tests" / "run-all.sh")
        d2 = on_disk(fake_root / "web" / "tests")
        caught = [f for f in d2 if f not in n2]
        truth(f"the planted file is caught ({len(caught)} unregistered of {len(d2)} on disk)",
              planted.name in caught, caught)

    print("\nscene 5: the check can FAIL the other way -- a name with no file")
    with tempfile.TemporaryDirectory() as d:
        fake_root = Path(d) / "repo"
        (fake_root / "web" / "tests").mkdir(parents=True)
        shutil.copy2(RUN_ALL, fake_root / "web" / "tests" / "run-all.sh")
        for f in files[:-1]:                       # one file deliberately absent
            (fake_root / "web" / "tests" / f).write_text("# copy\n")
        n3, _ = listed(fake_root / "web" / "tests" / "run-all.sh")
        d3 = on_disk(fake_root / "web" / "tests")
        gone = [n for n in n3 if n.endswith(".py") and n not in d3]
        truth(f"a declared suite that is not on disk is caught ({len(gone)} named but absent)",
              files[-1] in gone, gone)

    print("\nscene 6: and the ENGINE runner is not the place this was supposed to be caught")
    # THE WHOLE LINE that registers THIS suite is dropped before the scan, not just the
    # identifier. My first version replaced only `test_web_suite_registration` and left the
    # description `(every test file in web/tests/ is in ITS runner list ...)` behind, so the scan
    # found `web/tests` in the engine runner and this scene went red over its own registration.
    # A scan that trips on the thing that makes it run is not a scan.
    eng_raw = (HERE / "run-all.sh").read_text()
    eng_lines = [ln for ln in eng_raw.splitlines()
                 if "test_web_suite_registration" not in ln]
    eng = "\n".join(eng_lines)
    q = (ROOT / "queue" / "tests" / "run-all.sh").read_text()
    truth(f"neither the engine nor the queue runner dispatches web/tests -- which is why this "
          f"file exists rather than a line in one of them "
          f"(scanned {len(eng_lines)} engine lines and {len(q.splitlines())} queue lines)",
          "web/tests" not in eng and "web/tests" not in q,
          "one of them now dispatches web/tests; if that is deliberate, this scene is the thing "
          "to update")
    # POSITIVE CONTROL: the scan must be able to find the string somewhere, or the negative above
    # is void. It is in THIS file, by construction.
    truth("and the scan can see the string web/tests at all (positive control)",
          "web" + "/tests" in Path(__file__).read_text())
    # Against the UNSTRIPPED text, obviously: `eng` above has this line removed on purpose.
    truth("and THIS check is registered in the engine runner, so it actually runs",
          "test_web_suite_registration.py" in eng_raw,
          "it is not in engine/tests/run-all.sh, so nothing invokes it and it is the defect it "
          "checks for")

    # ---------------------------------------------------------------------------------------
    print("\nscene 7: the runner's OWN count agrees with its --list")
    #
    # R-WEB-GAP-DENOMINATOR-01, CAP14 REV-033. THE UNTESTED HALF IS WHERE IT WENT WRONG TWICE.
    #
    # `web/tests/run-all.sh` holds two derivations of "what do I declare": the `--list` arm, which
    # every scene above drives, and `DECLARED`, which the runner's own gap block compares against
    # the disk and which NOTHING checked. `--list` walks SUITES and WRAPPED; `DECLARED` counted
    # SUITES alone. So the tested half stayed right while the untested half was wrong in both
    # directions in turn -- first printing a gap of -1, then, after the fix to the other operand,
    # +1, which is worse: a negative count of "files not in the list" is impossible in its own
    # terms and gets investigated, and a positive one looks actionable and names no file.
    #
    # This asserts the two derivations AGREE, which is the property, rather than asserting the
    # expression's text, which would pass for a `DECLARED` that named both arrays and added them
    # wrong.
    src = RUN_ALL.read_text()
    arrays = {}
    for arr in ("SUITES", "WRAPPED"):
        m = re.search(rf"^{arr}=\((.*?)^\)", src, re.S | re.M)
        if m is None:
            arrays[arr] = None
            continue
        arrays[arr] = [ln.split("#")[0].strip() for ln in m.group(1).splitlines()
                       if ln.split("#")[0].strip()]
    missing = [a for a, v in arrays.items() if v is None]
    truth("both declaration arrays are parseable", not missing,
          f"could not parse: {missing}. The runner's shape changed and this scene is now measuring "
          f"nothing, which is why it says so instead of comparing.")
    if not missing:
        counted = sum(len(v) for v in arrays.values())
        # DENOMINATOR, before any verdict: two empty arrays would agree with an empty --list.
        truth(f"the arrays are nonempty ({counted} entries across "
              f"{', '.join(f'{a}={len(v)}' for a, v in arrays.items())})", counted > 0,
              "0 declared names parsed; a comparison against --list would agree vacuously")
        if counted > 0:
            truth(f"--list prints one name per declared entry ({len(names)} listed, "
                  f"{counted} declared across both arrays)", len(names) == counted,
                  f"--list printed {len(names)} and the arrays hold {counted}. The two "
                  f"derivations of what this runner declares disagree.")
            # AND THE GAP BLOCK'S OPERAND IS THE SAME NUMBER. This is the assertion that fires on
            # the actual defect: `DECLARED=${#SUITES[@]}` parses, evaluates to 31, and disagrees.
            d = re.search(r"^DECLARED=(.+)$", src, re.M)
            truth("the runner sets DECLARED exactly once", d is not None
                  and len(re.findall(r"^DECLARED=", src, re.M)) == 1,
                  f"found {len(re.findall(r'^DECLARED=', src, re.M))} assignments; two derivations "
                  f"of one fact is the defect this scene exists for")
            if d is not None:
                expr = d.group(1)
                names_used = set(re.findall(r"#(SUITES|WRAPPED)\[@\]", expr))
                truth(f"and DECLARED is derived from every declaration array "
                      f"(uses {sorted(names_used) or 'none'})",
                      names_used == set(arrays),
                      f"DECLARED is `{expr.strip()}`, which reads {sorted(names_used)} of "
                      f"{sorted(arrays)}. An array it does not count is a suite the gap block "
                      f"will report as unregistered while the runner dispatches it.")

    print()
    if PASS + FAIL == 0:                                     # DENOMINATOR
        print("0 comparisons made. A verdict over an empty set is not a pass.")
        return 2
    print(f"{PASS} passed, {FAIL} failed  "
          f"({len(files)} test_*.py on disk in web/tests/, {len(names)} declared in its runner)")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
