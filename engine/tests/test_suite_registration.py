#!/usr/bin/env python3
"""Every test file in engine/tests/ is a test file the runner runs. Task 0354, child of 0352.

`engine/tests/run-all.sh` is the script every lane quotes when it says the harness is green. On
2026-08-19 it dispatched **25 of the 30 `test*` files sitting next to it**, and the banner it
printed for that was `ALL SUITES GREEN`. Nothing in the line said 25, or 30, or that the two
differed, and nothing in the five orphans said they were orphans. They were not stubs or
scaffolding -- run standalone that day, all five were green:

    test_denominator_lint.py            34 passed, 0 failed (over 13 cases)
    test_brief_survives_finishers.py    34 passed, 0 failed
    test_steering_exclusion.py          41 passed, 0 failed
    test-scratch-db-guard.sh            29 passed, 0 failed
    test-codex-guard.sh                 14 passed, 0 failed

The most pointed one is the first: `test_denominator_lint.py` is the ONLY evidence anyone has
that the denominator lint refuses anything, and for three days nothing invoked it. A rule with an
enforcement point nobody runs is the shape task 0292 was written against, one level up.

WHY THIS IS A SUITE AND NOT A COMMENT IN run-all.sh. The failure mode is a file ARRIVING, which is
a thing that happens after any comment is written and read. 0352 registered its own new suite and
counted the list while it was in there -- that count is the only reason this task exists. The next
author will not count. So the count runs on every run-all, red on disagreement.

HOW IT AVOIDS BEING THE DEFECT IT CHECKS FOR. It does not parse run-all.sh's prose. It runs
`run-all.sh --list`, which prints the basenames out of the same `SUITES` array the loop iterates
and exits before reconciling or reaping anything -- so the list under test and the list dispatched
cannot drift apart, and this check costs no database. A prose parse that matched nothing would
have compared zero files and reported no disagreements, which is a pass over an empty set.

Both sides of the comparison are asserted nonempty BEFORE any verdict about what they contain,
and the ability of the check to fail at all is proved by scenes 5 and 6, which plant a file the
list does not name and delete a name the disk still has, and require the check to catch each.

Needs no database and no scratch store: it lists files and runs one script with `--list`.

Run: python3 engine/tests/test_suite_registration.py
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
RUN_ALL = HERE / "run-all.sh"

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
    if got == want:
        ok(msg)
    else:
        bad(msg, f"wanted [{want}], got [{got}]")


# ---------------------------------------------------------------------------
# The three primitives. Each takes its subject as an argument so that the scenes
# below can point them at a planted tree instead of the real one -- a check whose
# inputs cannot be varied is a check nobody has seen fail.
# ---------------------------------------------------------------------------

def normalise_listing(stdout: str):
    """`--list` output, one basename per entry. PURE, so a scene can hand it a fabricated line.

    Pure for the same reason `compare` is: a normaliser that can only be exercised by running a
    real runner can only be tested where a real runner disagrees with its directory, which is
    exactly the state nobody has on hand. Terminal 25 asked for a positive control and there was
    nowhere to put one until this came out of the subprocess.
    """
    out = []
    for ln in stdout.splitlines():
        m = re.match(r"\s*(test[-_][A-Za-z0-9_.-]+\.(?:py|sh))\b", ln)
        if m:
            out.append(m.group(1))
        elif ln.strip():
            out.append(ln.strip())
    return out


def listed_from(runall: Path):
    """What run-all.sh says it dispatches. Its own array, not a parse of this file's idea of it."""
    r = subprocess.run(["bash", str(runall), "--list"], capture_output=True, text=True,
                       timeout=60, cwd=str(ROOT))
    if r.returncode != 0:
        return None, r
    # ONE BASENAME PER LINE IS THE CONTRACT, AND THIS TOLERATES A RUNNER THAT ADDS A DESCRIPTION.
    # Terminal 25 found deploy/tests reported TWICE in opposite directions -- the same file as
    # "on disk and NOT dispatched" and as "dispatched and NOT on disk" -- because that runner emits
    # `name.py (description)` and this read the whole line. Two halves of one mismatch, which reads
    # like two defects.
    #
    # I fixed my own two runners to emit basenames and should have fixed this as well: changing the
    # producers left every other runner one description away from the same false pair, and there
    # are seven of them.
    #
    # MERGE, 2026-09-07: L01 wrote a normaliser for the same defect on the R line and ITS VERSION IS
    # THE BETTER ONE, so this is theirs and the reasoning above is mine. Mine took the first
    # whitespace token unconditionally; theirs only rewrites a line that LOOKS like a test basename
    # and keeps any other line WHOLE. That difference matters for exactly the case Terminal 26 hit:
    # a runner printing a banner into `--list` shows up here as the actual prose, which is
    # diagnosable, instead of as a truncated fragment like "NO", which is not.
    return normalise_listing(r.stdout), r


def on_disk(dirpath: Path):
    """The test files a reader would say live in that directory. Same shape the brief measured:
    `test*.py` and `test*.sh`. Leading-underscore helpers (_scratch_preflight.py, _kill_mid_done.py)
    are imported by suites, never dispatched, and do not match."""
    return sorted(p.name for p in dirpath.iterdir()
                  if p.is_file() and p.name.startswith("test")
                  and p.suffix in (".py", ".sh"))


def compare(listed, disk):
    """(on disk but not dispatched, dispatched but not on disk). Pure, so scene 7 can hand it
    an empty side and watch what this file does with it."""
    return sorted(set(disk) - set(listed)), sorted(set(listed) - set(disk))


# ---------------------------------------------------------------------------


def test_the_list_is_cheap_and_says_something():
    """`--list` has to exit before the expensive, database-touching part of run-all.sh, or nobody
    will call it and this suite becomes a second full run of the harness."""
    listed, r = listed_from(RUN_ALL)
    if listed is None:
        bad("run-all.sh --list exits 0", f"exit {r.returncode}: {r.stderr[:300]}")
        return
    ok("run-all.sh --list exits 0")
    # AGAINST RAW STDOUT, NOT THE NORMALISED LIST. R-SUITEREG-BASENAME-CHECK-01, Terminal 25.
    #
    # `listed_from` normalises, so reading `listed` here made this assertion UNABLE TO FAIL: a
    # runner that stopped printing only basenames was repaired on the way in and then asserted to
    # have printed only basenames. T25 planted exactly that -- broke this repository's own reference
    # runner so it emitted `test-claim-race.sh  (THE PROGRAM GATE)` -- and this line still said ok.
    #
    # The normaliser must keep tolerating runners this lane does not own. The CONTRACT assertion on
    # a runner it does own has to see the raw line, or the tolerance erases the contract.
    raw = [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]
    eq(f"and prints only basenames ({len(raw)} lines)",
       [n for n in raw if not re.fullmatch(r"test[-_][A-Za-z0-9_.-]+\.(py|sh)", n)], [])
    truth = "run-all:" not in r.stdout and "migrat" not in r.stdout.lower()
    if truth:
        ok("and reconciles nothing on the way: no migrate or reaper chatter in its output")
    else:
        bad("and reconciles nothing on the way: no migrate or reaper chatter in its output",
            f"stdout carried runner chatter: {r.stdout[:300]}")


# THE ROOTS ARE DISCOVERED, NOT LISTED. R-SUITEREG-01, 2026-09-07.
#
# THIS FILE SCANNED `engine/tests/` ONLY, WHICH MADE THE SAME DEFECT SILENT ONE DIRECTORY OVER.
# Measured 2026-08-29 by diffing `web/tests/run-all.sh`'s declared names against a clean
# `git archive HEAD` export: 22 declared, 24 present, 2 undeclared. One of the two was
# `test_terminal_carries_no_write.py`, which IS the proof behind the one condition attached to
# MUST-NOT-BUILD item 11's overrule. A condition whose proof nothing runs is a condition nobody is
# checking, and this suite could not have caught it because of one hardcoded directory.
#
# `queue/tests/run-all.sh` had no `--list` at all until the 2026-08-31 change, so it came third and
# not first: the contract had to exist before it could be compared against.
#
# THE LIST WAS THEN HARDCODED AND STOPPED THERE, TWICE. It was widened to three on 2026-08-31, and
# four more runners arrived. Terminal 12 reported the gap; measured 2026-09-07 by asking every
# `run-all.sh` in the tree for `--list`:
#
#     engine/tests 45, web/tests 31, queue/tests 17     covered
#     deploy/tests 1, engine/execution 2, store 5,      NOT COVERED
#     intake-service/tests 2
#
# Ten suites in four runners, and TWO OF THE FOUR WERE MINE. `store/run-all.sh` was written this
# sprint specifically to close the "committed suites dispatched by nothing" pattern, and was then
# never registered with the check that proves a runner dispatches what is on disk: the hole closed
# one level down and left open one level up.
#
# So the roots are DISCOVERED. A root is declared by holding a `run-all.sh`. Extending a list by
# hand is what produced this gap twice, and the failure mode is a runner ARRIVING -- exactly what a
# hardcoded list cannot see.
#
# MERGE, 2026-09-07: the R line widened the hardcoded list to five, adding `engine/execution` and
# `store` -- the same two gaps, found independently and closed the other way. DISCOVERY SUBSUMES
# THAT: it finds those two and also `deploy/tests` and `intake-service/tests`, which the widened
# list still missed. Their intent is kept and their mechanism is not, because the mechanism is the
# thing that had already failed twice.
SKIP_DIR_NAMES = {".git", "outputs", "node_modules", "_migration-checkpoints"}


def discover_roots():
    """Every directory in the tree holding a `run-all.sh`, as (reldir, relrunner).

    Pruned by DIRECTORY NAME BELOW THE ROOT, never by a path segment anywhere in the absolute path.
    That distinction is not pedantry: on 2026-09-07 a check that pruned any path containing a
    `_scratch-` segment silently excluded EVERY file in these worktrees, because they live under
    `_scratch-infinity`. It examined nothing and said so only because it guarded its denominator.
    """
    found = []
    for runner in sorted(ROOT.rglob("run-all.sh")):
        rel = runner.relative_to(ROOT)
        if any(part in SKIP_DIR_NAMES for part in rel.parts):
            continue
        found.append((str(rel.parent).replace("\\", "/"), str(rel).replace("\\", "/")))
    return found


def test_every_runner_lists_and_every_file_is_dispatched():
    """THE RULE, over every runner the repository declares rather than a list somebody maintained.

    Each side of each comparison is asserted nonempty BEFORE any verdict about what it contains,
    for the reason the single-directory version already gives: `no unregistered files` is also what
    a scan of the wrong path prints.
    """
    roots = discover_roots()
    # A DISCOVERY THAT FINDS NOTHING, OR ALMOST NOTHING, IS NOT A CLEAN REPOSITORY.
    # One root is the shape a broken walk takes: it finds the directory it started in and stops.
    if len(roots) < 2:                                                  # DENOMINATOR
        bad("the walk found the repository's runners",
            f"found {len(roots)}: {[r for r, _ in roots] or 'none'}. A walk that finds one root or "
            f"none has not surveyed a repository, and every verdict below would be over that.")
        return
    ok(f"discovered {len(roots)} suite roots: {' '.join(r for r, _ in roots)}")

    total = 0
    unlistable = []
    for reldir, relrunall in roots:
        d, runall = ROOT / reldir, ROOT / relrunall
        if not d.is_dir() or not runall.is_file():
            bad(f"{reldir} and its runner both exist", f"dir={d.is_dir()} runner={runall.is_file()}")
            continue
        listed, r = listed_from(runall)
        if listed is None:
            # A RUNNER THAT CANNOT BE ENUMERATED CANNOT BE VERIFIED, and that is a finding about the
            # runner rather than a crash of this check. Named, counted, and reported once at the end
            # so four of them do not read as four unrelated failures.
            unlistable.append(f"{relrunall} (exit {r.returncode})")
            continue
        disk = on_disk(d)
        if not listed or not disk:                                      # DENOMINATOR
            bad(f"{reldir}: the list names {len(listed or [])} and the disk holds {len(disk)}",
                "0 comparisons made. A verdict over an empty set is not a pass.")
            continue
        missing, unknown = compare(listed, disk)
        n = len(set(listed) | set(disk))
        total += n
        if missing or unknown:
            bad(f"{reldir}: every one of {n} test files is dispatched",
                f"on disk and NOT dispatched: {missing or 'none'}; "
                f"dispatched and NOT on disk: {unknown or 'none'}")
        else:
            ok(f"{reldir}: all {n} test files are dispatched, and every name dispatched exists")
    if unlistable:
        bad(f"every discovered runner answers --list ({len(unlistable)} do not)",
            "; ".join(unlistable) + ". A runner that cannot list its suites cannot be checked "
            "against its directory, so those suites are dispatched by something nobody verifies. "
            "Add a `--list` arm that prints one basename per line and exits BEFORE anything that "
            "costs a database.")
    if total == 0:                                                      # DENOMINATOR
        bad("the scan compared something", f"0 files compared across {len(roots)} roots")
    else:
        ok(f"the scan covered {total} files across {len(roots)} roots")


def test_every_test_file_in_this_directory_is_dispatched():
    """THE RULE. The verdict line carries both sides of the comparison, because `no unregistered
    files` is also what this prints when the directory scan or the list comes back empty."""
    listed, _ = listed_from(RUN_ALL)
    disk = on_disk(HERE)
    if not listed or not disk:                                          # DENOMINATOR
        bad(f"the dispatch list names {len(listed or [])} files and engine/tests/ holds {len(disk)}",
            "0 comparisons made. A verdict over an empty set is not a pass.")
        return
    missing, unknown = compare(listed, disk)
    n = len(set(listed) | set(disk))
    if not missing:
        ok(f"every test file in engine/tests/ is in run-all.sh's dispatch list "
           f"({len(disk)} on disk, {len(listed)} dispatched, {n} compared)")
    else:
        bad(f"every test file in engine/tests/ is in run-all.sh's dispatch list "
            f"({len(disk)} on disk, {len(listed)} dispatched, {n} compared)",
            "green files nobody runs: " + " ".join(missing))
    if not unknown:
        ok(f"and every dispatched name is a file that exists ({len(listed)} checked)")
    else:
        bad(f"and every dispatched name is a file that exists ({len(listed)} checked)",
            "dispatched but absent: " + " ".join(unknown))


def test_a_description_carrying_line_matches_its_file():
    """R-SUITEREG-DEPLOY-01's positive control, asked for by Terminal 25.

    THE DEFECT IT GUARDS: `deploy/tests` reported the SAME file twice in opposite directions --
    "on disk and NOT dispatched" AND "dispatched and NOT on disk" -- because its runner emits
    `name.py (description)` and the check compared whole lines. Two halves of one mismatch, reading
    like two defects, in a check whose whole job is to compare those two sides.
    """
    listed = normalise_listing(
        "test_restore_proves_behaviour.py (a restore is proven by behaviour surviving, not row counts)\n"
        "test_narrow_waist.py (there is ONE write path and nothing reaches around it)\n"
        "test-because-of-you.sh   (a shell suite, spaced differently)\n")
    disk = ["test_restore_proves_behaviour.py", "test_narrow_waist.py", "test-because-of-you.sh"]
    missing, unknown = compare(listed, disk)
    if not listed:                                                  # DENOMINATOR
        bad("the description fixture normalised to something", "0 lines parsed, so the comparison "
            "below would be over an empty set")
    elif missing or unknown:
        bad("a description-carrying line matches its file",
            f"on disk and NOT dispatched: {missing}; dispatched and NOT on disk: {unknown}")
    else:
        ok(f"all {len(disk)} description-carrying lines match their files")

    # NEGATIVE CONTROL, and it is the half that keeps the normaliser honest. A line that is NOT a
    # test basename must survive WHOLE, so a runner printing a banner into `--list` is reported as
    # the prose it is. Truncating it to a first token would make the check pass over pollution and
    # call it a suite name.
    polluted = normalise_listing("NO OPERATOR CREDENTIAL on this host (/x/y).\ntest_narrow_waist.py\n")
    if polluted == ["NO OPERATOR CREDENTIAL on this host (/x/y).", "test_narrow_waist.py"]:
        ok("a line that is not a basename survives whole, so pollution is visible")
    else:
        bad("a line that is not a basename survives whole", f"got {polluted}")


def test_no_name_is_dispatched_twice():
    """A duplicate would inflate the banner's own denominator, which is the number this task added
    to make the gap visible in the first place."""
    listed, _ = listed_from(RUN_ALL)
    if not listed:                                                      # DENOMINATOR
        bad("the dispatch list has no duplicates",
            "0 comparisons made. A verdict over an empty set is not a pass.")
        return
    dupes = sorted({n for n in listed if listed.count(n) > 1})
    eq(f"the dispatch list has no duplicates ({len(listed)} entries, "
       f"{len(set(listed))} distinct)", dupes, [])


def test_the_banner_prints_its_own_denominator():
    """run-all.sh's last line. Read out of the source, because the only way to read it out of a RUN
    is to run the whole harness, and a check that expensive is a check that gets commented out."""
    src = RUN_ALL.read_text(encoding="utf-8")
    have = re.search(r"test files in engine/tests/ invoked", src)
    if have:
        ok("run-all.sh's banner line carries `N of M test files in engine/tests/ invoked`")
    else:
        bad("run-all.sh's banner line carries `N of M test files in engine/tests/ invoked`",
            "ALL SUITES GREEN with no tally is the untallied verdict task 0292 refuses")
    guard = re.search(r"#\s*DENOMINATOR", src)
    if guard:
        ok("and run-all.sh refuses to print that banner over 0 invoked / 0 on disk")
    else:
        bad("and run-all.sh refuses to print that banner over 0 invoked / 0 on disk",
            "no DENOMINATOR marker in run-all.sh")


def test_a_planted_file_is_caught():
    """THE PROOF THAT IT CAN FAIL, half one: a new suite arrives and nobody registers it. Built on
    a copy of the directory listing rather than by writing into engine/tests/, because a test that
    leaves a file behind in the tree it audits is a test that changes its own answer."""
    listed, _ = listed_from(RUN_ALL)
    tmp = Path(tempfile.mkdtemp(prefix="t0354_plant_"))
    try:
        for n in on_disk(HERE):
            (tmp / n).write_text("# stand-in\n", encoding="utf-8")
        (tmp / "test_zz_nobody_registered_this.py").write_text("# stand-in\n", encoding="utf-8")
        (tmp / "_scratch_preflight.py").write_text("# helper, must not count\n", encoding="utf-8")
        disk = on_disk(tmp)
        missing, unknown = compare(listed, disk)
        eq("a test file the list does not name is reported, by name",
           missing, ["test_zz_nobody_registered_this.py"])
        eq("and the leading-underscore helpers are not mistaken for suites",
           [n for n in disk if n.startswith("_")], [])
        eq("and nothing else in the directory is dragged in with it", unknown, [])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_a_deleted_entry_is_caught():
    """THE PROOF THAT IT CAN FAIL, half two: the array itself goes stale. The entry removed is
    test_denominator_lint.py, which is the file this whole task was found through."""
    tmp = Path(tempfile.mkdtemp(prefix="t0354_del_"))
    try:
        cut = tmp / "run-all.sh"
        src = RUN_ALL.read_text(encoding="utf-8")
        line = [ln for ln in src.splitlines() if '"test_denominator_lint.py' in ln]
        if len(line) != 1:
            bad("the scene can remove exactly one dispatch entry",
                f"matched {len(line)} lines, not 1 -- this scene is asserting over the wrong thing")
            return
        ok("the scene can remove exactly one dispatch entry")
        cut.write_text(src.replace(line[0] + "\n", ""), encoding="utf-8")
        listed, r = listed_from(cut)
        if listed is None:
            bad("the cut copy still lists", f"exit {r.returncode}: {r.stderr[:300]}")
            return
        missing, _ = compare(listed, on_disk(HERE))
        eq("a suite dropped out of the array is reported, by name",
           missing, ["test_denominator_lint.py"])
        eq("and the cut copy is otherwise the same list",
           len(listed), len(listed_from(RUN_ALL)[0]) - 1)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_an_empty_side_is_refused_not_passed():
    """The zero case, stated as an assertion rather than trusted to the guards above. `compare([],
    disk)` reports EVERY file missing and `compare(listed, [])` reports every name unknown -- so a
    caller that read the words `no disagreements` off an empty run would be reading them off a
    branch this file never takes."""
    disk = on_disk(HERE)
    missing, unknown = compare([], disk)
    eq(f"an empty dispatch list reports all {len(disk)} files unregistered, not zero",
       len(missing), len(disk))
    eq("and claims nothing about names it never saw", unknown, [])
    missing2, unknown2 = compare(["test_contract.py"], [])
    eq("an empty directory reports the dispatched name as absent, not as agreement",
       unknown2, ["test_contract.py"])
    eq("and reports nothing missing, because nothing was on disk to miss", missing2, [])


def test_every_scene_in_this_file_is_dispatched():
    """The rule this file applies to runners, applied to this file's own TESTS list.

    Found by adding a scene and watching the banner still say eight: a scene defined here and left
    out of TESTS is dispatched by nothing, and nothing said so. That is the defect this suite exists
    to catch, one floor down, in the suite that catches it.
    """
    defined = sorted(k for k, v in list(globals().items())
                     if k.startswith("test_") and callable(v))
    declared = sorted(f.__name__ for f in TESTS)
    if not defined or not declared:                                     # DENOMINATOR
        bad("this file's own scenes were compared",
            f"defined={len(defined)} declared={len(declared)}: a verdict over an empty set is not "
            f"a pass")
        return
    missing = [n for n in defined if n not in declared]
    unknown = [n for n in declared if n not in defined]
    if missing or unknown:
        bad(f"every one of {len(defined)} scenes in this file is dispatched",
            f"defined and NOT in TESTS: {missing or 'none'}; "
            f"in TESTS and NOT defined: {unknown or 'none'}")
    else:
        ok(f"all {len(defined)} scenes in this file are dispatched by TESTS")


TESTS = [test_the_list_is_cheap_and_says_something,
         test_every_test_file_in_this_directory_is_dispatched,
         test_no_name_is_dispatched_twice,
         test_the_banner_prints_its_own_denominator,
         test_a_planted_file_is_caught,
         test_a_deleted_entry_is_caught,
         test_an_empty_side_is_refused_not_passed,
         test_every_runner_lists_and_every_file_is_dispatched,
         test_a_description_carrying_line_matches_its_file,
         test_every_scene_in_this_file_is_dispatched]
# THIS LIST IS HAND-MAINTAINED AND IS NOW CHECKED AGAINST DISCOVERY, which is the same shape this
# file applies to every runner one level up: a DECLARED set compared against what is actually
# there, with both sides asserted nonempty first.
#
# The hazard was real and was found the ordinary way -- by adding a scene on 2026-09-07 and watching
# the banner still say eight. A scene defined above and not added here is dispatched by nothing,
# silently, which is precisely the runner-versus-directory disagreement every scene here measures.
#
# THE LIST IS KEPT RATHER THAN REPLACED BY DISCOVERY, and that is deliberate. Order is not
# incidental: `test_every_runner_lists_and_every_file_is_dispatched` runs last because it is the
# expensive one that shells out to seven runners, and definition order would move it. Discovery as a
# REPLACEMENT would have silently reordered the suite; discovery as a CHECK keeps the order a human
# chose and still refuses the omission. The same argument as the runners themselves: the declared
# list is the contract, and something independent has to notice when it stops matching.


def main():
    print("test_suite_registration.py  --  a file in engine/tests/, web/tests/ or "
          "queue/tests/ is a file its runner runs (task 0354, widened 2026-08-31)")
    for t in TESTS:
        print(f"\n{t.__name__}")
        try:
            t()
        except Exception as e:                                          # noqa: BLE001
            bad(f"{t.__name__} raised", f"{type(e).__name__}: {e}")
    # This suite's own. Every scene above can return early on a refusal, and a build where all
    # seven did would otherwise print `0 passed, 0 failed` and exit 0 -- the exact green this file
    # exists to refuse, printed by the file that refuses it. Task 0292.
    if PASS + FAIL == 0:                                                # DENOMINATOR
        print("\n0 comparisons made. A verdict over an empty set is not a pass.")
        return 2
    print(f"\n{PASS} passed, {FAIL} failed  (over {len(TESTS)} scenes)")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
