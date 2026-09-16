#!/usr/bin/env python3
"""The denominator lint fires. Asserted, because a lint nobody has ever seen refuse is a claim.

Task 0292. `engine/bin/denominator-lint.py` is the enforcement point for V00's sentence *"assert
the count of things compared, not just the result."* This suite is the reason anyone should
believe it enforces anything: it builds throwaway trees, runs the real lint against them with
`--root`/`--baseline`, and reads the exit code and the printed line for every outcome the lint
claims to have -- clean, VIOLATION, STALE (fixed), STALE (deleted), and the three BROKEN paths.

WHY IT IS BUILT THIS WAY. A suite that only ran the lint against this repo would assert exactly
one thing: that the repo is green today. Green is the answer a lint gives when it is working and
also the answer it gives when it is walking an empty directory, which is the whole defect. So
every refusal path is exercised on a tree this file constructs, where the right answer is known
before the lint runs.

Needs no database and no scratch store: the lint reads files and prints, nothing else.

Run: python3 engine/tests/test_denominator_lint.py
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LINT = ROOT / "engine" / "bin" / "denominator-lint.py"

# The marker set is read out of the lint, never restated here. A copy of the list would keep
# passing after the real one changed, which is a check comparing against its own memory.
_spec = importlib.util.spec_from_file_location("denominator_lint", LINT)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
REPO_MARKERS = _mod.REPO_MARKERS

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
        for line in str(detail).strip().splitlines()[:8]:
            print(f"        {line}")


def truth(msg, cond, detail=""):
    ok(msg) if cond else bad(msg, detail)


def lint(root=None, baseline=None, *extra):
    argv = [sys.executable, str(LINT)]
    if root is not None:
        argv += ["--root", str(root)]
    if baseline is not None:
        argv += ["--baseline", str(baseline)]
    argv += list(extra)
    r = subprocess.run(argv, capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


# The two file bodies every case below is built from. UNGUARDED is the shape 76 files in this
# repo have today; GUARDED is the same file plus the four lines the rule asks for.
UNGUARDED = '''#!/usr/bin/env python3
PASS, FAIL = 0, 0
def main():
    print(f"{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0
'''

GUARDED = '''#!/usr/bin/env python3
PASS, FAIL = 0, 0
def main():
    if PASS + FAIL == 0:                                  # DENOMINATOR
        print("0 comparisons made. A verdict over an empty set is not a pass.")
        return 2
    print(f"{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0
'''

# The marker as a bare comment, with nothing compared to zero. Must NOT count.
FAKE_COMMENT = '''#!/usr/bin/env python3
# DENOMINATOR: I thought about it.
PASS, FAIL = 0, 0
def main():
    print(f"{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0
'''

# The marker and the zero comparison, but the branch does not exit nonzero. Must NOT count:
# noticing the empty set and carrying on is the defect, not the fix.
FAKE_NOEXIT = '''#!/usr/bin/env python3
PASS, FAIL = 0, 0
def main():
    if PASS + FAIL == 0:                                  # DENOMINATOR
        print("nothing compared, carrying on anyway")
    print(f"{PASS} passed, {FAIL} failed")
    return 0
'''


# The two forms roles/terminal.md prints as THE guard, in shell. Documented snippets that the
# lint does not actually accept would be worse than no snippet, so both are asserted below rather
# than trusted.
SH_GUARDED = """#!/usr/bin/env bash
PASS=0; FAIL=0
[ $((PASS + FAIL)) -gt 0 ] || { echo "DENOMINATOR: 0 compared"; exit 2; }   # DENOMINATOR
echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
"""

SH_UNGUARDED = """#!/usr/bin/env bash
PASS=0; FAIL=0
echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
"""


def tree(**files):
    """Build a throwaway tree; returns its path. Caller removes it."""
    d = Path(tempfile.mkdtemp(prefix="denom-lint-"))
    for rel, body in files.items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return d


def git_tree(**files):
    """A throwaway tree that is a REAL git work tree, so the lint can see tracked vs untracked.

    Task 0355. `tree()` builds a plain directory, and the lint now (correctly) refuses to call a
    missing baselined path DELETED in a tree it cannot ask that question of. Every case that
    needs a real STALE verdict needs a real work tree, so it gets one.
    """
    d = tree(**files)
    subprocess.run(["git", "init", "-q", str(d)], capture_output=True, text=True, check=True)
    return d


def baseline_at(d, *paths):
    p = d / "baseline.txt"
    p.write_text("# floor\n" + "\n".join(paths) + "\n", encoding="utf-8")
    return p


def test_clean_tree_is_green():
    """A tree whose only counter verdict is guarded passes, and says how many it compared."""
    d = tree(**{"suite.py": GUARDED})
    try:
        rc, out = lint(d, baseline_at(d))
        truth("a guarded counter verdict is a pass", rc == 0, out)
        truth("and the verdict line carries the denominator it compared",
              "1 gated verdict files in" in out, out)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_a_new_unguarded_verdict_is_refused():
    """The case the rule exists for: a NEW check that prints a green over an empty set."""
    d = tree(**{"suite.py": GUARDED, "newcheck.py": UNGUARDED})
    try:
        rc, out = lint(d, baseline_at(d))
        truth("an unguarded counter verdict outside the baseline FAILS the lint", rc == 1, out)
        truth("and the refusal names the file and the line", "VIOLATION newcheck.py:4" in out, out)
        truth("and the refusal says what to do about it", "DENOMINATOR guard" in out, out)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_the_baseline_is_a_floor_not_an_exemption_generator():
    """A file already on the floor is debt, not a violation. That is what makes it adoptable."""
    d = tree(**{"suite.py": GUARDED, "old.py": UNGUARDED})
    try:
        rc, out = lint(d, baseline_at(d, "old.py"))
        truth("a baselined unguarded file does not fail the lint", rc == 0, out)
        truth("and the debt is printed rather than hidden", "1 baselined debt" in out, out)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_the_floor_may_only_shrink():
    """Fix a baselined file and the lint makes you delete its line. Otherwise the number lies."""
    d = tree(**{"suite.py": GUARDED, "old.py": GUARDED})
    try:
        rc, out = lint(d, baseline_at(d, "old.py"))
        truth("a baselined file that is now guarded FAILS until its line is deleted",
              rc == 1, out)
        truth("and the message says the floor may only shrink",
              "STALE     old.py is guarded now" in out and "only shrink" in out, out)
    finally:
        shutil.rmtree(d, ignore_errors=True)

    d = git_tree(**{"suite.py": GUARDED, "other.py": UNGUARDED})
    try:
        rc, out = lint(d, baseline_at(d, "other.py", "deleted.py"))
        truth("a baselined file that no longer exists FAILS too, in a tree that could have "
              "held it", rc == 1, out)
        truth("and says so by name", "STALE     deleted.py" in out, out)
        truth("and the entries it COULD resolve are counted, not just the one that failed",
              "2 baselined debt" in out and "resolved in this tree" in out, out)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_an_export_cannot_call_an_untracked_baselined_file_deleted():
    """Task 0355, the incident itself. A `git archive` export holds tracked files and NOTHING
    else, so an untracked baselined path is missing from it by construction. Reading that as
    DELETED told a landing task to shrink the floor by two while both files sat on disk unguarded.
    """
    d = git_tree(**{"suite.py": GUARDED, "tracked_debt.py": UNGUARDED,
                    "outputs/untracked_debt.py": UNGUARDED})
    ex = Path(tempfile.mkdtemp(prefix="denom-lint-export-"))
    try:
        run = lambda *a: subprocess.run(["git", "-C", str(d), *a], capture_output=True,
                                        text=True, check=True)
        run("add", "suite.py", "tracked_debt.py")
        run("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")
        # outputs/untracked_debt.py is deliberately never added: it is the shape this fleet
        # writes almost everything in, and the shape an export cannot carry.
        tar = ex / "export.tar"
        with open(tar, "wb") as fh:
            subprocess.run(["git", "-C", str(d), "archive", "HEAD"], stdout=fh, check=True)
        subprocess.run(["tar", "-xf", str(tar), "-C", str(ex)], check=True)
        tar.unlink()

        bl = baseline_at(d, "tracked_debt.py", "outputs/untracked_debt.py")
        truth("precondition: the untracked file is really on disk in the work tree",
              (d / "outputs" / "untracked_debt.py").exists(), str(d))
        truth("precondition: and is really absent from the export",
              not (ex / "outputs" / "untracked_debt.py").exists(), str(ex))

        rc, out = lint(ex, bl)
        truth("an export does NOT tell you to delete the line of a file it merely cannot see",
              "Delete its line." not in out, out)
        truth("it calls that entry INCONCLUSIVE by name",
              "INCONCLUSIVE outputs/untracked_debt.py" in out, out)
        truth("and says do NOT delete it", "Do NOT delete its line" in out, out)
        truth("and subtracts it from the ratchet's denominator on the verdict line",
              "1 resolved in this tree, 1 UNRESOLVED" in out and "PARTIAL ratchet audit" in out,
              out)

        rc2, out2 = lint(d, bl)
        truth("while the SAME baseline over the real work tree resolves both and is clean",
              rc2 == 0, out2)
        truth("and says so with the count it resolved",
              "all 2 resolved in this tree" in out2, out2)
    finally:
        shutil.rmtree(d, ignore_errors=True)
        shutil.rmtree(ex, ignore_errors=True)


def test_a_copy_inside_a_work_tree_is_still_not_the_work_tree():
    """The detector this fix must NOT use. 0336's export was written into this repo's own
    `outputs/`, so `git rev-parse --is-inside-work-tree` answers TRUE inside it and a lint keyed
    on that would have kept the bug. The sound question is whether the walked root IS the top.
    """
    outer = git_tree(**{"suite.py": GUARDED})
    try:
        inner = outer / "copy"
        inner.mkdir()
        (inner / "suite.py").write_text(GUARDED, encoding="utf-8")
        inside = subprocess.run(["git", "-C", str(inner), "rev-parse", "--is-inside-work-tree"],
                                capture_output=True, text=True)
        truth("precondition: the naive test says the copy IS inside a work tree",
              inside.stdout.strip() == "true", inside.stdout + inside.stderr)
        sees, why = _mod.tree_sees_untracked(inner)
        truth("but the lint refuses it, because it is not the top of that work tree",
              sees is False, why)
        truth("and names which tree it actually found", "not at the walked root" in why, why)
        sees_outer, why_outer = _mod.tree_sees_untracked(outer)
        truth("while the real top of the work tree IS accepted", sees_outer is True, why_outer)
    finally:
        shutil.rmtree(outer, ignore_errors=True)


def test_the_marker_cannot_be_faked():
    """The guard is a marker AND a zero comparison AND a nonzero exit. Two of three is not a guard."""
    d = tree(**{"suite.py": GUARDED, "comment.py": FAKE_COMMENT})
    try:
        rc, out = lint(d, baseline_at(d))
        truth("the word DENOMINATOR in a comment does not make a file guarded",
              rc == 1 and "VIOLATION comment.py" in out, out)
    finally:
        shutil.rmtree(d, ignore_errors=True)

    d = tree(**{"suite.py": GUARDED, "noexit.py": FAKE_NOEXIT})
    try:
        rc, out = lint(d, baseline_at(d))
        truth("noticing the empty set and continuing anyway does not make a file guarded",
              rc == 1 and "VIOLATION noexit.py" in out, out)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_the_lint_refuses_to_report_over_nothing():
    """The lint applies the rule to itself. Three ways its own set goes to zero, three BROKENs."""
    d = Path(tempfile.mkdtemp(prefix="denom-lint-empty-"))
    try:
        rc, out = lint(d, baseline_at(d))
        truth("an empty tree is BROKEN (exit 2), not a pass", rc == 2, out)
        truth("and it says it compared nothing", "found 0 scripts" in out, out)
    finally:
        shutil.rmtree(d, ignore_errors=True)

    d = tree(**{"nothing.py": "print('hello')\n"})
    try:
        rc, out = lint(d, baseline_at(d))
        truth("scripts but zero counter verdicts is BROKEN (exit 2), not a pass", rc == 2, out)
        truth("and it says the walk may be pointed at the wrong directory",
              "compared nothing and is not a pass" in out, out)
    finally:
        shutil.rmtree(d, ignore_errors=True)

    d = tree(**{"suite.py": GUARDED})
    try:
        rc, out = lint(d, d / "no-such-baseline.txt")
        truth("a missing ratchet floor is BROKEN (exit 2), not a pass", rc == 2, out)
        truth("and it says there was nothing to compare the violations against",
              "nothing to" in out and "compare the violations against" in out, out)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_the_guard_form_the_doctrine_prints_is_the_form_the_lint_accepts():
    """roles/terminal.md prints a shell guard. If the lint rejected it the doctrine would lie."""
    d = tree(**{"guarded.sh": SH_GUARDED, "unguarded.sh": SH_UNGUARDED})
    try:
        rc, out = lint(d, baseline_at(d), "--list")
        truth("the shell guard printed in roles/terminal.md is accepted",
              "GUARDED    guarded.sh" in out, out)
        truth("and the same file without it is refused",
              rc == 1 and "VIOLATION unguarded.sh" in out, out)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_the_lint_obeys_its_own_rule():
    """It prints a counter verdict, so it is in its own gated tier, so it must be guarded."""
    rc, out = lint(None, None, "--list")
    lines = [l for l in out.splitlines() if l.strip().endswith(")") and "denominator-lint.py" in l]
    truth("the lint appears in its own gated tier", len(lines) == 1, out[:400])
    truth("and its own state is GUARDED, not baselined",
          bool(lines) and lines[0].startswith("GUARDED"), lines[:1])


def repo_copy(d, rel, markers=REPO_MARKERS, **files):
    """Plant a directory that looks like a copy of this repo, plus whatever files it should hold."""
    for m in markers:
        f = d / rel / m
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("# marker\n", encoding="utf-8")
    for name, body in files.items():
        f = d / rel / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body, encoding="utf-8")


def test_an_embedded_copy_of_this_repo_is_linted_once():
    """Task 0341. A lane's `git archive` export of HEAD is the same debt at a second path."""
    d = tree(**{"suite.py": GUARDED})
    try:
        repo_copy(d, "outputs/run/head-export", **{"web/tests/old.py": UNGUARDED})
        rc, out = lint(d, baseline_at(d))
        truth("baselined debt duplicated inside an embedded copy is not a NEW violation",
              rc == 0 and "VIOLATION" not in out, out)
        truth("and the pruned copy is NAMED, not silently dropped",
              "outputs/run/head-export" in out and "embedded repo copies pruned: 1" in out, out)
        truth("and the skip carries its own denominator: how many scripts it held",
              "scripts already linted at their own paths" in out, out)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_the_copy_detector_can_never_prune_the_tree_it_was_pointed_at():
    """Only SUBdirectories are tested. A root carrying the markers is still the thing under lint."""
    d = tree(**{"suite.py": GUARDED, "newcheck.py": UNGUARDED})
    try:
        repo_copy(d, ".")
        rc, out = lint(d, baseline_at(d))
        truth("the root is linted even when it carries every marker",
              rc == 1 and "VIOLATION newcheck.py" in out, out)
        truth("and it is not reported as a pruned copy of itself",
              "embedded repo copies pruned: 0" in out, out)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_a_partial_marker_set_is_not_a_copy():
    """The rule is ALL markers. Otherwise any directory named `engine` prunes real checks."""
    d = tree(**{"suite.py": GUARDED})
    try:
        repo_copy(d, "outputs/run/notacopy", markers=REPO_MARKERS[:-1],
                  **{"newcheck.py": UNGUARDED})
        rc, out = lint(d, baseline_at(d))
        truth("a directory holding only some of the markers is still walked",
              rc == 1 and "VIOLATION outputs/run/notacopy/newcheck.py" in out, out)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_the_copy_detector_is_live_on_this_repo_and_not_merely_present():
    """A detector whose markers no longer match this tree is a control that cannot fire."""
    missing = [m for m in REPO_MARKERS if not (ROOT / m).exists()]
    truth(f"all {len(REPO_MARKERS)} copy markers exist at this repo's root, so the detector "
          f"can fire here", not missing, f"missing: {missing}")
    rc, out = lint()
    truth("and the real run says which way the detector went, live or inert",
          "embedded repo copies pruned:" in out and "INERT" not in out, out)


def test_this_repo_is_green_today():
    """The shipped state. Named as one assertion among many, never as the whole suite."""
    rc, out = lint()
    truth("denominator-lint exits 0 on this repo as shipped", rc == 0, out)
    truth("and it reports a nonzero number of gated verdict files, so the walk found the tree",
          " gated verdict files in " in out and " in 0 scripts" not in out, out)
    truth("and it prints the census tier it does NOT gate, rather than implying full coverage",
          "census tier, counted and NOT gated" in out, out)


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print("test_denominator_lint.py  --  the denominator lint refuses, and refuses for the "
          "right reasons (task 0292)")
    print(f"  lint under test: {LINT}")
    print(f"  cases: {len(tests)}\n")
    for t in tests:
        print(f"{t.__name__.replace('test_', '')}: "
              f"{t.__doc__.splitlines()[0] if t.__doc__ else ''}")
        try:
            t()
        except Exception:                                         # noqa: BLE001
            import traceback
            bad(f"{t.__name__} raised", traceback.format_exc().strip().splitlines()[-1])
        print()

    # This suite's own denominator. Task 0292: `tests` is discovered by prefix, so a rename or a
    # bad edit empties it, every counter stays 0, and the line below would read `0 passed, 0
    # failed` and exit 0 -- the exact shape this file exists to refuse. Exit 2, not 1, so a
    # caller can tell "the checks failed" from "there were no checks".
    if PASS + FAIL == 0 or len(tests) == 0:                       # DENOMINATOR
        print("0 comparisons made over 0 discovered cases. A verdict over an empty set is not a "
              "pass. Something renamed or emptied the test_* functions in this file.")
        return 2
    print(f"{PASS} passed, {FAIL} failed  (over {len(tests)} cases)")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
