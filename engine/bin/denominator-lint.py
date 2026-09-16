#!/usr/bin/env python3
"""The denominator rule, enforced by a machine instead of by a careful reader.

Task 0292, posted by V9 acceptance (task 0222) as the third of four blockers. The rule is one
sentence and it has been written down since V00: **assert the count of things compared, not just
the result.** V9 counted ELEVEN instruments across NINE lanes that shipped a pass because the set
they compared had gone to zero or pointed at the wrong column, and every one of those authors
already knew the rule. A rule that everyone knows and that nothing checks is not a rule, it is a
preference. This file is the check.

WHAT THE DEFECT LOOKS LIKE, in this repo's own harness, measured 2026-08-19 and not inherited:
326 shell and python scripts, 76 of them print a counter verdict (`N passed, M failed`) on 112
lines, and ZERO of the 76 refuse a zero counter. The exit rule is identical everywhere --
`return 1 if FAIL else 0` in 33 python suites, `[ "$FAIL" -eq 0 ]` in 8 shell suites -- so a suite
whose test list came back EMPTY prints

    0 passed, 0 failed

and exits 0. Green. `engine/tests/run-all.sh` prints ALL SUITES GREEN over it, and a reader
grepping for `passed, 0 failed` gets a green from a suite that compared nothing at all. That is
the same shape as the restore verifier reporting RESTORE VERIFIED over 1 table of 20, as the
twelve CSRF probes that all 403'd at the gate and were scored as a pass, and as
`git diff --quiet HEAD -- <untracked>` printing COMMITTED because an untracked path is in no diff.

WHAT THIS LINT DOES, and precisely what it does not:

  * GATED TIER -- counter verdicts. A line that EMITS (`print`/`printf`/`echo`) a string carrying
    both a pass-stem and a fail-stem. This is the repo's universal convention, it is where the
    verdict is actually decided, and it is unambiguous. A NEW file in this tier that carries no
    denominator guard fails the lint.
  * CENSUS TIER -- banner verdicts (VERIFIED, HELD, GREEN, CLEAN, MATCH...). COUNTED AND PRINTED,
    NEVER GATED. Said out loud rather than left implied, because a lint that silently narrows its
    own scope is the defect wearing a lint's clothes. The census number is the size of the surface
    this lint does not yet cover, and it is printed on every run.

  * RATCHET, not a big bang. `policy/denominator-baseline.txt` lists every gated file that was
    already unguarded when the rule landed. Those are debt, not violations. The floor may only
    SHRINK: a baselined file that has since been guarded, or deleted, fails the lint with an
    instruction to remove its line, so the number cannot quietly stay stale.

    AND THE RATCHET PRINTS ITS OWN DENOMINATOR (task 0355), because the rule bit this lint. A
    baselined path that the walk did not find means DELETE THIS LINE in one kind of tree and
    means NOTHING AT ALL in another, and the lint was reading the second as the first. A
    `git archive` export holds tracked files and nothing else, so every untracked baselined path
    is absent from it by construction: on 2026-08-19 an export-based run reported two live,
    on-disk, unguarded verifiers as `no longer exists. Delete its line.`, and that instruction was
    folded into a landing task. Deleting them would have shrunk the floor by two without guarding
    anything, turning tolerated debt into two new violations at the next full-tree run. So the
    lint now asks whether the tree it walked is itself the top of a git work tree, and a missing
    path in a tree that cannot hold untracked files is INCONCLUSIVE, printed with an explicit
    do-NOT-delete, never STALE. An inconclusive entry is not a violation and does not fail the
    run; it is a comparison that did not execute, so it is subtracted from the ratchet's
    denominator on the verdict line -- `72 resolved in this tree, 2 UNRESOLVED -- PARTIAL ratchet
    audit` -- and a run that resolved NONE of a non-empty floor exits 2 BROKEN rather than clean.

WHAT COUNTS AS A GUARD. The marker is a source token, deliberately, so that this lint is a grep
and not an inference -- a static analyser guessing at intent would be one more check that reports
on the comparisons it made. A file is guarded when it carries a line containing the word
DENOMINATOR *and* a comparison of a count against zero, with a nonzero exit within the next 8
lines. Four lines, which is what V9 said each of its own instruments cost:

    if PASS + FAIL == 0:                                    # DENOMINATOR
        print("0 comparisons made. A verdict over an empty set is not a pass.")
        return 2

    [ $((PASS + FAIL)) -gt 0 ] || { echo "DENOMINATOR: 0 compared"; exit 2; }   # DENOMINATOR

EMBEDDED COPIES OF THIS REPO ARE LINTED ONCE, AT THEIR OWN PATHS. Task 0341. A lane that needs
a clean tree writes a full `git archive` export of HEAD into its own `outputs/` directory, which
duplicates every script in the repo at a second path. The ratchet floor is a path list, so the
same debt at the copied path is not on the floor and reads as dozens of NEW violations: on
2026-08-19 that was 73 of 73 violation lines, all under one lane's export, and 0 anywhere else.
A subdirectory is treated as such a copy when it carries ALL of REPO_MARKERS below -- a
structural test, not a match on the word `head-export`, so a lane that names its export anything
else is covered too. The ROOT is never tested against the rule, so the detector cannot prune the
tree it was pointed at. Every copy it skips is NAMED on stdout with the number of scripts it
holds, because a skip nobody prints is how a lint's own denominator quietly goes to zero. If the
markers ever stop matching this repo the detector goes inert and says so, and the failure that
surfaces is the false RED above, never a false green.

THIS FILE OBEYS ITS OWN RULE. Its verdict line carries a pass-stem and a fail-stem, so it is in
its own gated tier, and it appears in its own output as guarded. It also refuses to report at all
on an empty scan: zero files walked, or zero gated verdicts found, exits 2 rather than printing a
clean bill of health over nothing. A lint that passes because it found no files to lint would be
the eleventh instance becoming the twelfth.

Run:
    python3 engine/bin/denominator-lint.py                 # gate: exit 0 clean, 1 violation, 2 broken
    python3 engine/bin/denominator-lint.py --list          # every gated file and its state
    python3 engine/bin/denominator-lint.py --write-baseline # regenerate the floor (only widens with a reason)
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[2]
BASELINE_REL = Path("policy") / "denominator-baseline.txt"

# Directories that hold no checks, or hold copies of them. `_scratch-*` is per-agent working
# space, `outputs/` is NOT skipped: three of V9's eleven instances were one-off verifiers written
# into outputs/, which is exactly where a new check gets written without a harness around it.
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", ".mypy_cache", ".pytest_cache"}
SKIP_PREFIX = ("_scratch-",)
EXTENSIONS = (".py", ".sh", ".bash")

# What makes a subdirectory a COPY of this repo rather than a part of it. All three must be
# present. Tracked files, deliberately: an untracked path (`policy/denominator-baseline.txt`, say)
# is absent from a `git archive` export and would make the test miss the exact thing it is for.
REPO_MARKERS = ("engine/swarm_engine/cli.py", "engine/bin/swarm", "roles/terminal.md")

EMIT = re.compile(r"\b(?:print|printf|echo|sys\.stdout\.write)\b")
PASS_STEM = re.compile(r"pass(?:ed|es)?\b", re.I)
FAIL_STEM = re.compile(r"fail(?:ed|s|ure)?\b", re.I)

# The census tier. Reported, never gated. Each of these is a verdict a reader would act on, and
# none of them carries a count in the shapes seen in this tree.
BANNERS = re.compile(
    r"\b(?:VERIFIED|ALL SUITES GREEN|RESTORE VERIFIED|rule HELD|NO DRIFT|CLEAN|IN SYNC|MATCHES)\b"
)

MARKER = re.compile(r"\bDENOMINATOR\b")
ZERO_CMP = re.compile(r"(?:==\s*0|!=\s*0|-eq\s+0|-gt\s+0|-le\s+0|<\s*1|>\s*0|\bnot\s+\w+\s+and\s+not\s+\w+)")
NONZERO_EXIT = re.compile(r"(?:sys\.exit\(\s*[1-9]|return\s+[1-9]|\bexit\s+[1-9]|\bexit\s+\$)")
GUARD_WINDOW = 8


def tree_sees_untracked(root):
    """Can a file that is UNTRACKED by git appear in this tree at all? (bool, why).

    Task 0355. The ratchet's `no longer exists` rule reads a baselined path that is missing from
    the walk as DELETED, and tells you to delete its line. That inference is only sound in a tree
    where the file COULD have been present. A `git archive` export contains tracked files and
    nothing else, so every untracked baselined path is missing from it by construction -- and on
    2026-08-19 exactly that happened: an export-based run reported two live, on-disk, unguarded
    files as STALE, and the instruction to delete their lines was folded into a landing task. Had
    it landed, two pieces of tolerated debt would have become two NEW violations, and the floor
    would have shrunk without a single file being guarded. The floor may only shrink when a file
    gets GUARDED, never when a tree merely cannot see it.

    The test is whether `root` is ITSELF the top level of a git work tree. Being merely INSIDE one
    is not enough and is the case that matters: the export that caused this was written into this
    repo's own `outputs/`, so `--is-inside-work-tree` says true there while the export's copy of
    the baselined path is still absent. Anything else -- an export, an unpacked tarball, a
    hermetic fixture, git not on the PATH -- cannot answer the question, and the honest verdict
    over a comparison you could not make is INCONCLUSIVE, not a deletion instruction.
    """
    try:
        r = subprocess.run(["git", "-C", str(root), "rev-parse", "--show-toplevel"],
                           capture_output=True, text=True, timeout=15)
    except FileNotFoundError:
        return False, "git is not on the PATH, so this tree's tracked/untracked split is unreadable"
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"git could not be run here ({type(e).__name__})"
    if r.returncode != 0:
        return False, "not a git work tree, so it holds no untracked files by construction"
    try:
        top = Path(r.stdout.strip()).resolve()
    except (OSError, ValueError):
        return False, "git named a top level this process cannot resolve"
    if top != Path(root).resolve():
        return False, (f"a git work tree rooted at {top}, not at the walked root -- so this is a "
                       f"copy or an export inside one, not the tree the floor was written against")
    return True, f"the top level of the git work tree at {top}"


def is_repo_copy(d):
    """True when this directory carries every marker that identifies this repo's own root."""
    return all((d / m).exists() for m in REPO_MARKERS)


def count_scripts(d):
    """How many scripts a pruned subtree held. Printed, so the skip is never a silent narrowing."""
    n = 0
    for dirpath, dirnames, filenames in os.walk(d):
        dirnames[:] = [
            x for x in dirnames
            if x not in SKIP_DIRS and not x.startswith(SKIP_PREFIX)
        ]
        n += sum(1 for fn in filenames if fn.endswith(EXTENSIONS))
    return n


def walk_scripts(root, copies=None):
    """Every shell or python file under the repo, minus the skips. Yields (abs_path, rel_path).

    Embedded copies of this repo are pruned and appended to `copies` as (rel_path, script_count).
    Only SUBdirectories are tested, so `root` itself can never be pruned however it is named.
    """
    for dirpath, dirnames, filenames in os.walk(root):
        keep = []
        for d in dirnames:
            if d in SKIP_DIRS or d.startswith(SKIP_PREFIX):
                continue
            sub = Path(dirpath) / d
            if is_repo_copy(sub):
                if copies is not None:
                    copies.append((sub.relative_to(root).as_posix(), count_scripts(sub)))
                continue
            keep.append(d)
        dirnames[:] = keep
        for fn in sorted(filenames):
            if fn.endswith(EXTENSIONS):
                p = Path(dirpath) / fn
                yield p, p.relative_to(root).as_posix()


def classify(lines):
    """(gated_lines, census_lines, guarded) for one file's source lines."""
    gated, census = [], []
    for i, line in enumerate(lines, 1):
        if EMIT.search(line):
            if PASS_STEM.search(line) and FAIL_STEM.search(line):
                gated.append(i)
            elif BANNERS.search(line):
                census.append(i)
    guarded = False
    for i, line in enumerate(lines):
        if MARKER.search(line) and ZERO_CMP.search(line):
            window = "\n".join(lines[i:i + GUARD_WINDOW + 1])
            if NONZERO_EXIT.search(window):
                guarded = True
                break
    return gated, census, guarded


def read_baseline(baseline):
    """The ratchet floor. Missing file is fatal: a run with no floor compares against nothing."""
    if not baseline.exists():
        return None
    out = []
    for raw in baseline.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if s and not s.startswith("#"):
            out.append(s)
    return out


def scan(root):
    scanned = 0
    gated_files, census_files = {}, {}
    guarded = set()
    copies = []
    for path, rel in walk_scripts(root, copies):
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        scanned += 1
        g, c, is_guarded = classify(lines)
        if g:
            gated_files[rel] = g
            if is_guarded:
                guarded.add(rel)
        if c:
            census_files[rel] = c
    return scanned, gated_files, census_files, guarded, sorted(copies)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--list", action="store_true", help="print every gated file and its state")
    ap.add_argument("--write-baseline", action="store_true",
                    help="regenerate policy/denominator-baseline.txt from the current tree")
    ap.add_argument("--root", default=None,
                    help="tree to walk (default: this repo). Used by the lint's own suite so it "
                         "can exercise the violation, stale and BROKEN paths hermetically.")
    ap.add_argument("--baseline", default=None,
                    help="ratchet floor to read (default: <root>/policy/denominator-baseline.txt)")
    args = ap.parse_args()

    root = Path(args.root).resolve() if args.root else DEFAULT_ROOT
    baseline_path = Path(args.baseline).resolve() if args.baseline else root / BASELINE_REL

    scanned, gated_files, census_files, guarded, copies = scan(root)
    unguarded = sorted(set(gated_files) - guarded)

    # WHAT THE WALK DID NOT WALK, said out loud on every run and every exit path. A skip that is
    # not printed is indistinguishable from a tree that never had those files, which is the
    # zero-denominator defect pointed at the lint itself. Task 0341.
    live = sum(1 for m in REPO_MARKERS if (root / m).exists())
    if copies:
        copies_line = (
            f"denominator-lint: embedded repo copies pruned: {len(copies)}, holding "
            f"{sum(n for _, n in copies)} scripts already linted at their own paths -- "
            + "; ".join(f"{rel} ({n} scripts)" for rel, n in copies)
        )
    elif live == len(REPO_MARKERS):
        copies_line = (
            f"denominator-lint: embedded repo copies pruned: 0 (detector live: {live} of "
            f"{len(REPO_MARKERS)} root markers present, so a copy here would be recognised)"
        )
    else:
        copies_line = (
            f"denominator-lint: embedded repo copies pruned: 0 -- detector INERT on this tree: "
            f"{live} of {len(REPO_MARKERS)} root markers present, so no subdirectory can be "
            f"recognised as a copy of it. Everything found was walked; a copy, if one existed, "
            f"would be counted twice and read as a false RED, never as a false green."
        )

    if args.write_baseline:
        body = [
            "# The denominator ratchet floor. Task 0292.",
            "#",
            "# Every path below prints a counter verdict (`N passed, M failed`) and does NOT refuse",
            "# a zero counter, so it reports green over an empty comparison set. These are the files",
            "# that were already this way when the rule landed on 2026-08-19. They are DEBT, and the",
            "# lint does not fail on them.",
            "#",
            "# THIS LIST MAY ONLY SHRINK. When you add the four-line guard to a file, delete its line",
            "# here in the same change; `engine/bin/denominator-lint.py` fails until you do, which is",
            "# what keeps the number honest rather than decorative. A file added here is a decision",
            "# somebody has to defend in a task, not a formatting fix.",
            "#",
            "# Regenerate with: python3 engine/bin/denominator-lint.py --write-baseline",
            "",
        ]
        body += unguarded
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text("\n".join(body) + "\n", encoding="utf-8")
        print(f"denominator-lint: wrote {baseline_path} with {len(unguarded)} "
              f"baselined files, out of {len(gated_files)} gated verdict files in {scanned} scripts")
        print(copies_line)
        return 0

    baseline = read_baseline(baseline_path)

    # THIS LINT'S OWN DENOMINATORS. Checked before anything is reported, because every finding
    # below is a statement about a set, and a report over an empty set is the defect this file
    # exists to catch. Exit 2 -- BROKEN, not merely violated -- so a caller can tell the two apart.
    broken = []
    if scanned == 0:                                                          # DENOMINATOR
        broken.append(f"walked {root} and found 0 scripts. Nothing was compared.")
    if not gated_files:                                                       # DENOMINATOR
        broken.append("found 0 counter verdicts in the whole tree. Either the convention moved "
                      "or the walk is pointed at the wrong directory; either way this run "
                      "compared nothing and is not a pass.")
    if baseline is None:                                                      # DENOMINATOR
        broken.append(f"{baseline_path} does not exist. With no ratchet floor this run has nothing to "
                      "compare the violations against.")
    if len(broken) > 0:                                                       # DENOMINATOR
        for b in broken:
            print(f"BROKEN    denominator-lint {b}")
        print(f"\ndenominator-lint: 0 files passed, 0 files failed "
              f"(of {len(gated_files)} gated verdict files in {scanned} scripts) -- BROKEN")
        print(copies_line)
        return 2

    baseline_set = set(baseline)
    new_violations = [f for f in unguarded if f not in baseline_set]
    stale_fixed = sorted(f for f in baseline_set if f in guarded)

    # THE RATCHET'S OWN DENOMINATOR. Task 0355. A baselined path the walk did not find is either
    # DELETED (delete its line, the floor shrinks) or INVISIBLE TO THIS TREE (say so, change
    # nothing). Those are opposite instructions and the difference is not in the path, it is in
    # the tree. So ask the tree once, up front, and let the answer pick the message.
    sees_untracked, tree_why = tree_sees_untracked(root)
    missing = sorted(f for f in baseline_set
                     if f not in gated_files and not (root / f).exists())
    stale_gone = missing if sees_untracked else []
    unresolved = [] if sees_untracked else missing
    resolved = len(baseline_set) - len(unresolved)

    if args.list:
        for f in sorted(gated_files):
            state = ("GUARDED " if f in guarded
                     else "baselined" if f in baseline_set else "NEW")
            print(f"{state:10} {f}  (verdict lines: "
                  f"{', '.join(str(n) for n in gated_files[f])})")
        print()

    for f in new_violations:
        print(f"VIOLATION {f}:{gated_files[f][0]} prints a counter verdict with no zero-denominator "
              f"guard. Add the four-line DENOMINATOR guard, or add the path to "
              f"{BASELINE_REL.as_posix()} with a reason.")
    for f in stale_fixed:
        print(f"STALE     {f} is guarded now. Delete its line from "
              f"{BASELINE_REL.as_posix()}; the floor may only shrink.")
    for f in stale_gone:
        print(f"STALE     {f} is in {BASELINE_REL.as_posix()} and no longer exists. "
              f"Delete its line.")
    for f in unresolved:
        print(f"INCONCLUSIVE {f} is in {BASELINE_REL.as_posix()} and was not found in this tree, "
              f"but this tree cannot tell a deleted file from an untracked one: {tree_why}. "
              f"Do NOT delete its line on this run's evidence. Re-run the lint from the top of a "
              f"git work tree to get a verdict on it.")

    # The floor's resolution, printed on the verdict line itself so the number cannot be quoted
    # without it. Task 0355: an UNRESOLVED entry is not a pass and not a violation, it is a
    # comparison that did not execute, and a run that made none of them is BROKEN rather than
    # clean -- the ratchet half of this lint compared nothing at all.
    if baseline_set and resolved == 0:                                        # DENOMINATOR
        print(f"BROKEN    denominator-lint resolved 0 of {len(baseline_set)} baselined paths in "
              f"{root}: {tree_why}. The ratchet compared nothing, so it is not a pass.")
        print(f"\ndenominator-lint: 0 files passed, 0 files failed "
              f"(of {len(gated_files)} gated verdict files in {scanned} scripts) -- BROKEN")
        print(copies_line)
        return 2

    npass = len(gated_files) - len(new_violations)
    nfail = len(new_violations) + len(stale_fixed) + len(stale_gone)
    ratchet = (f"{len(baseline_set)} baselined debt, all {resolved} resolved in this tree"
               if not unresolved else
               f"{len(baseline_set)} baselined debt: {resolved} resolved in this tree, "
               f"{len(unresolved)} UNRESOLVED -- PARTIAL ratchet audit")
    print(f"\ndenominator-lint: {npass} files passed, {nfail} files failed "
          f"(of {len(gated_files)} gated verdict files in {scanned} scripts; "
          f"{len(guarded)} guarded, {ratchet})")
    print(copies_line)
    print(f"denominator-lint: census tier, counted and NOT gated: {len(census_files)} files carry "
          f"a banner verdict (VERIFIED / ALL SUITES GREEN / rule HELD / ...) with no counter. "
          f"That is the surface this lint does not cover yet.")
    return 1 if nfail else 0


if __name__ == "__main__":
    sys.exit(main())
