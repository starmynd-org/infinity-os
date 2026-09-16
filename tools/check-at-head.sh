#!/usr/bin/env bash
# Run the checks that matter against WHAT A COMMIT ACTUALLY CONTAINS, not against the working tree.
#
# Phase 4 of `outputs/2026-08-29-commander/05-PROJECT-PLAN.md`. Written 2026-08-31.
#
# ==============================================================================================
# THE PATTERN THIS CLOSES, AND IT HAS SEVEN INSTANCES
# ==============================================================================================
#
# Seven times in this repo a check existed, was green, and had NEVER RUN AGAINST THE ARTEFACT THAT
# SHIPS:
#
#   1. HEAD was unimportable for nineteen hours while every suite passed on the working tree.
#   2. A suite printed `12 passed` while writing nine rows into production.
#   3. Five files sat past their own ratchet.
#   4. Nothing executed that ratchet.
#   5. Two committed suites were dispatched by nothing.
#   6. A contrast check visited 4 of 21 route-and-theme combinations.
#   7. A clean export could not run its own tests, because 298 scripts lost their exec bit.
#
# Every one of them is the same shape: THE THING CHECKED AND THE THING SHIPPED WERE DIFFERENT
# OBJECTS. A working tree has your uncommitted fixes in it, your untracked files, and your local
# exec bits. A commit has none of that.
#
# ==============================================================================================
# TWO RULES DECIDE WHETHER THIS WORKS, AND BOTH ARE EASY TO LOSE
# ==============================================================================================
#
# 1. IT RUNS AGAINST `git archive HEAD`, NEVER THE WORKING TREE. If somebody "simplifies" this by
#    running the checks in place, it recreates the exact blindness it exists to close, and it will
#    still be green every day until the day it matters.
#
#    `git archive HEAD` and not `git stash`, not a copy of the directory, not `git status`: the
#    archive is BYTE FOR BYTE what the commit contains, including the mode bits, and it cannot be
#    contaminated by anything in the working directory.
#
#    IT IS RUN FROM THE REPO ROOT. `git archive HEAD` from a subdirectory archives only that
#    subtree, which produces a convincing false `ModuleNotFoundError` that reads like a real
#    import failure. This script cds to the root itself rather than trusting where it was called.
#
# 2. EVERY CHECK IS WATCHED FAILING FIRST. `--self-test` plants each defect into a throwaway export
#    and requires the corresponding check to catch it. A guard nobody has seen fail is a guard
#    nobody has evidence for, and five of the seven instances above were guards that were green.
#
# ==============================================================================================
# WHERE THIS LIVES IS ROW `0472` AND IT IS THE OPERATOR'S DECISION, NOT THIS FILE'S
# ==============================================================================================
#
# This script is the INSTRUMENT. What invokes it is a separate question and it is open:
#
#   * there is no CI on this repo;
#   * the fleet runner is the wrong place, because it runs when the fleet runs and the fleet is
#     paused;
#   * a git hook is bypassable with `--no-verify`, and this repo's pre-commit hook has printed
#     `ALLOW (INCONCLUSIVE) ... 0 artifact(s)` on all 44 of its last commits.
#
# So it is not wired into anything by this file. Wiring it is the operator's call and it is put to
# him in `outputs/2026-08-31-phase-1/02-ASSUMPTIONS.md`.
#
# Usage:
#   tools/check-at-head.sh              run every check against HEAD
#   tools/check-at-head.sh <commit>     run them against that commit instead
#   tools/check-at-head.sh --self-test  plant each defect and require each check to catch it
#   tools/check-at-head.sh --list       name the checks and exit, touching nothing
#
# THE COMMIT ARGUMENT EXISTS FOR THE PRE-PUSH HOOK AND FOR NOTHING ELSE. A push sends a specific
# sha, which is not always HEAD: pushing a branch you are not standing on, or a range, sends
# something else entirely, and a guard that checked HEAD in that case would be checking a commit
# nobody was sending. `tools/hooks/pre-push` passes each sha it is about to send.
#
# Exit: 0 all checks passed. 1 a check failed. 2 this script could not run a check at all, which
# is not the same answer and must not be read as one.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || { printf 'check-at-head: cannot reach the repo root\n' >&2; exit 2; }

PASS=0; FAIL=0; RAN=0; UNEVAL=0

# THREE OUTCOMES, NOT TWO. R-t04-WSL-GIT-01, and it is the reason this file was changed.
#
# A CHECK THAT COULD NOT RUN IS NOT A CHECK THAT FAILED, and reporting them the same way produced a
# confident wrong verdict on healthy code. Measured 2026-09-07 in a cut worktree: under Windows Git
# Bash the export succeeds but `python3` is a Python without `psycopg2`, so `imports` and
# `suites_registered` both returned 1 and this script printed "2 passed, 2 failed" at a commit whose
# code was fine. The control that proved it: the same two checks fail identically at 8e853e5, a
# commit independently measured 4/4 by the Admiral from the operator checkout.
#
# That is worse than a check that does not run at all. A silent check gets ignored; a confidently
# wrong one gets ACTED ON, and the action is to "fix" two non-defects in a healthy tree while the
# guard congratulates you. This script is installed as pre-push by the operator's ruling on row
# 0472, so it is the last thing between a mistake and a shared branch.
#
# A check now returns 77 for UNEVALUATED, the same spelling `run-all.sh` uses for NOT RUN, and the
# banner and the exit code both keep it separate from a pass and from a failure.
EXIT_UNEVALUATED=77

ok()     { PASS=$((PASS+1)); printf '  ok    %s\n' "$1"; }
bad()    { FAIL=$((FAIL+1)); printf '  FAIL  %s\n' "$1"; [ -n "${2:-}" ] && printf '        %s\n' "$2"; }
uneval() { UNEVAL=$((UNEVAL+1)); printf '  ????  %s  UNEVALUATED, not a pass and not a failure\n' "$1"
           [ -n "${2:-}" ] && printf '        %s\n' "$2"; }

CHECKS="imports suites_registered exec_bits ledger_unique"
REF="HEAD"

if [ "${1:-}" = "--list" ]; then
  for c in $CHECKS; do printf '%s\n' "$c"; done
  exit 0
fi

# ------------------------------------------------------------------ the export
#
# ONE export, reused by every check, because building four costs four archives of a repo this size
# and buys nothing: no check writes into the tree.

export_head() {
  local dest="$1"
  if ! git rev-parse --git-dir >/dev/null 2>&1; then
    # THE OTHER HALF OF R-t04-WSL-GIT-01. In a cut worktree `.git` is a FILE holding an absolute
    # path, and when that path is a Windows one this git cannot resolve it. Under WSL the archive
    # then produces nothing and every check would report over an empty tree. Say which condition
    # this is, because "could not export" reads like a broken repository and it is not one.
    if [ -f "$ROOT/.git" ]; then
      printf 'check-at-head: this git cannot read the worktree at %s.\n' "$ROOT" >&2
      printf '  .git is a file naming: %s\n' "$(sed -n 's/^gitdir: //p' "$ROOT/.git" 2>/dev/null)" >&2
      printf '  A git that cannot resolve that path exports nothing. This is an ENVIRONMENT\n' >&2
      printf '  condition, not a defect in the commit. Assembled recipe: export with the git that\n' >&2
      printf '  CAN read this worktree, run the checks where the dependencies are.\n' >&2
    fi
    return 1
  fi
  git archive "$REF" | tar -x -C "$dest" || return 1
  # THE SANITY CHECK ON THE EXPORT ITSELF. An empty or partial archive would make every check
  # below pass over nothing, which is the empty-set verdict this repo refuses to call a pass.
  local n; n="$(find "$dest" -name '*.py' | wc -l)"
  [ "$n" -ge 100 ] || { printf 'check-at-head: the export holds %s python files, which is too few '\
'to be this repo. Refusing to report a verdict over it.\n' "$n" >&2; return 1; }
  return 0
}

# ------------------------------------------------------------------ the checks
#
# Each takes the export directory as its only argument and returns 0 or 1. Each is a FUNCTION so
# `--self-test` can point it at a planted tree, which is the property that makes it possible to
# watch one fail.

# CAN PYTHON HERE EVEN ANSWER THE QUESTION? Shared by the two checks that run Python, because a
# missing interpreter or a missing dependency says nothing whatever about the commit under test.
# Both failure modes were live on this machine on 2026-09-07 and both produced FAIL.
python_can_answer() {
  command -v python3 >/dev/null 2>&1 || { printf 'no python3 on PATH'; return 1; }
  python3 -c "import psycopg2" >/dev/null 2>&1 \
    || { printf 'python3 (%s) cannot import psycopg2, so it cannot load this repo; the interpreter with the dependencies is elsewhere (on this machine, under WSL)' "$(command -v python3)"; return 1; }
  return 0
}

check_imports() {
  local t="$1" why
  why="$(python_can_answer)" || { printf '%s\n' "$why" >&2; return $EXIT_UNEVALUATED; }
  local out
  out="$( cd "$t" && python3 -c "import sys; sys.path[:0]=['.','engine','queue','voice']; import web.app" 2>&1 )" \
    && return 0
  # A STORE THAT IS ABSENT IS NOT A BROKEN IMPORT. T26 measured this file 4/4 unsealed and 2/4
  # sealed at ONE commit, because importing the app reaches the store. A guard whose answer depends
  # on whether a database happens to be up is not answering about the commit.
  case "$out" in
    *OperationalError*|*"could not connect"*|*"connection refused"*|*"No such file or directory"*"socket"*)
      printf 'importing the app reached the store and no database answered, so this says nothing about the commit: %s\n' \
        "$(printf '%s' "$out" | tail -1)" >&2
      return $EXIT_UNEVALUATED ;;
  esac
  printf '%s\n' "$out" >&2
  return 1
}

check_suites_registered() {
  local t="$1" why
  why="$(python_can_answer)" || { printf '%s\n' "$why" >&2; return $EXIT_UNEVALUATED; }
  ( cd "$t" && python3 engine/tests/test_suite_registration.py ) >/dev/null 2>&1
}

check_exec_bits() {
  # THE RECORDED MODE, NOT THE EXTRACTED FILE. Rewritten 2026-09-07 for R-GUARD-EXECBIT-01.
  #
  # This read `[ -x "$t/$f" ]` on the exported tree, and on a filesystem that cannot represent an
  # exec bit -- NTFS through Git Bash, which is half of this program's hosts -- `-x` is true for
  # every file. So the check reported `ok` while `tools/check-at-head.sh` and `tools/hooks/pre-push`
  # were both mode 100644 in the tree and git was silently ignoring the hook on any host that DOES
  # honour the bit. A green from a test the environment cannot answer is the worst of the three
  # outcomes this file now distinguishes, and it was coming from this check.
  #
  # `git ls-tree` reads the mode RECORDED IN THE COMMIT, which is the thing that actually ships and
  # the thing the pushing host honours. It is filesystem-independent by construction, and it is
  # still a fact about $REF rather than about the working tree, which is this file's whole rule.
  # `$2`/`$3` exist ONLY so --self-test can point this at a planted commit in a throwaway repo.
  # Without them the defect this check now looks for -- a mode recorded in a COMMIT -- cannot be
  # planted at all, and the scene below could only ever report UNEVALUATED. A check that cannot be
  # watched failing is the thing this file exists to prevent, and rewriting the mechanism without
  # rewriting its scene had quietly created one.
  local ref="${2:-$REF}" repo="${3:-.}"
  local bad_files="" mode rest path n=0
  while IFS=$'\t' read -r rest path; do
    mode="${rest%% *}"
    case "$path" in
      # THE ROOT ARMS ARE NOT REDUNDANT. L01-EXECBIT-ROOT-BLIND-01, CAP14 REV-072.
      #
      # `*/run-all.sh` REQUIRES A SLASH, so a runner at the repo root was invisible to this check
      # while every nested one was covered -- and the denominator would not have caught it either,
      # because `n` was nonzero from the nested matches. Zero incidence today (no root-level runner
      # exists at this commit), which is why it is a blind spot rather than a defect: the check
      # would have reported `ok` over the one file it could not see, in a tree where everything
      # else it could see was fine. `*-db.sh` already matches at the root because `*` may match
      # nothing; `*/run-all.sh` cannot, and that asymmetry is the whole finding.
      run-all.sh|*/run-all.sh|*/scratch-db.sh|*-db.sh|tools/check-at-head.sh|tools/hooks/*) ;;
      *) continue ;;
    esac
    n=$((n + 1))
    [ "$mode" = "100755" ] || bad_files="$bad_files $path($mode)"
  done < <(git -C "$repo" ls-tree -r "$ref")
  # DENOMINATOR. A pattern that matches nothing reports no bad modes, which is the empty-set pass
  # this repo refuses. These names have existed for months; zero means the listing broke.
  if [ "$n" -eq 0 ]; then
    printf 'no runner, guard or hook matched in %s: 0 modes compared, which is not a pass\n' "$ref"
    return 1
  fi
  [ -z "$bad_files" ] || {
    printf 'recorded NOT executable in %s (git honours this, not the working copy):%s\n' \
      "$ref" "$bad_files"
    return 1
  }
  return 0
}

check_ledger_unique() {
  # TWO FILES DECLARING ONE LEDGER VERSION is the collision every migration in this repo carries a
  # guard against, and the guard is per-file: the loser applies its DDL, skips its ledger row via
  # ON CONFLICT DO NOTHING, and re-applies forever. Nothing looked ACROSS the directories at once,
  # which is where the collision actually lives.
  #
  # THE DIRECTORY LIST IS NOT THIS FILE'S TO INVENT. `engine/bin/scratch-db.sh` is the builder, and
  # its `SCRATCH_SCHEMA_DIRS` default is the one statement of which lanes number into
  # `brain.schema_migration`. The first version of this check hard-coded three directories and
  # missed `budget/schema` (versions 3, 21 and 45 were invisible; CAP06 T04-R-SHARED-02, confirmed
  # by CAP14-REV-001 on 2026-09-06): a fourth lane had appeared and the two lists drifted. So the
  # list below is READ FROM THE BUILDER in the export under test and compared to what this check
  # expects; if the two disagree the check is red, because a guard scanning fewer lanes than the
  # builder applies is exactly the blindness that let 45 be claimable twice.
  #
  # `ingest/schema` is deliberately NOT a ledger directory (it creates schema `ingest` and records
  # no `schema_migration` row, the builder says so). That rule is ENFORCED here as an inverse
  # assertion rather than left as an absence: an `ingest/schema` file that starts declaring a
  # ledger version turns this check red instead of being silently unscanned.
  local t="$1"
  ( cd "$t" && python3 - <<'PYCHK'
import pathlib, re, sys
EXPECTED = ("migrations", "budget/schema", "queue/schema")
NON_LEDGER = ("ingest/schema",)

# 1. The builder's own lane set, read from the export, must equal what this check scans.
builder = pathlib.Path("engine/bin/scratch-db.sh")
if not builder.is_file():
    print("engine/bin/scratch-db.sh is missing from the export; the lane set cannot be checked")
    sys.exit(1)
# COMMENT LINES ARE SKIPPED, AND THE FIRST MATCH ON A REAL LINE WINS. The builder documents its
# own default in four comments; a comment that closes its brace, placed above the assignment,
# would otherwise be read instead of the assignment and the drift detector would agree with
# itself (CAP14-REV-013, reproduced 2026-09-06). The match is also confined to one line so an
# unclosed brace cannot run the capture across a newline.
m = None
for line in builder.read_text(encoding="utf-8", errors="replace").splitlines():
    if line.lstrip().startswith("#"):
        continue
    m = re.search(r'SCRATCH_SCHEMA_DIRS:-([^}"]+)\}', line)
    if m:
        break
if not m:
    print("engine/bin/scratch-db.sh no longer states a SCRATCH_SCHEMA_DIRS default on a code line; the lane set cannot be checked")
    sys.exit(1)
declared = tuple(m.group(1).split())
# Order is not part of the contract; membership is. Compared as sets so a reordered builder
# default is not reported as a phantom drift.
if set(declared) != set(EXPECTED):
    print("ledger lane set drift: the builder applies %s but this guard scans %s"
          % (" ".join(declared), " ".join(EXPECTED)))
    sys.exit(1)

def versions_in(d):
    out = []
    p = pathlib.Path(d)
    if not p.is_dir():
        return out
    for f in sorted(p.glob("*.sql")):
        m = re.search(r"VALUES\s*\((\d+),", f.read_text(encoding="utf-8", errors="replace"))
        if m:
            out.append((int(m.group(1)), str(f)))
    return out

# 2. No two files across the ledger directories declare one version.
seen, dupes = {}, []
for d in EXPECTED:
    for v, f in versions_in(d):
        if v in seen:
            dupes.append(f"{v}: {seen[v]} and {f}")
        else:
            seen[v] = f
if not seen:                                                    # DENOMINATOR
    print("no migration declared a ledger version; refusing to call that agreement")
    sys.exit(1)
if dupes:
    print("ledger version declared twice: " + "; ".join(dupes))
    sys.exit(1)

# 3. The non-ledger directories declare nothing, and that is asserted, not assumed.
stray = [f"{v}: {f}" for d in NON_LEDGER for v, f in versions_in(d)]
if stray:
    print("a non-ledger directory declares a schema_migration version: " + "; ".join(stray))
    sys.exit(1)
print("ledger: %d versions across %s; %s declare none" % (len(seen), " ".join(EXPECTED), " ".join(NON_LEDGER)))
sys.exit(0)
PYCHK
  )
}

run_all_checks() {
  local t="$1"
  RAN=0
  for c in $CHECKS; do
    RAN=$((RAN+1))
    local out
    out="$("check_$c" "$t" 2>&1)"; local rc=$?
    case $rc in
      0)                  ok "$c" ;;
      $EXIT_UNEVALUATED)  uneval "$c" "$out" ;;
      *)                  bad "$c" "$out" ;;
    esac
  done
}

# ------------------------------------------------------------------ --self-test
#
# PLANT THE DEFECT, REQUIRE THE CATCH. Each scene breaks the export in exactly the way its check
# exists to notice, and this run is RED if a check stays green under its own defect. That is the
# difference between a guard and a guard nobody has watched.
#
# `expect_catch` EXISTS BECAUSE THIS FILE BRIEFLY GOT IT WRONG, on 2026-09-07, within minutes of
# the change that introduced the third outcome. The scenes read `if check_x; then bad; else ok`,
# which treats ANY non-zero return as "it caught the defect". Once a check could return 77 for
# UNEVALUATED, a check that could not run at all reported as a successful catch:
#
#     ok    imports catches a broken import
#     python3 ... cannot import psycopg2
#
# The scene was green because nothing ran. That is the exact hollow-evidence failure this whole
# guard exists to prevent, introduced into the guard's own test by the fix for a different instance
# of it. A catch has to be an ACTUAL FAILURE, so the scenes now name the code they require.
expect_catch() {
  local name="$1" fn="$2" tree="$3" out rc
  out="$("$fn" "$tree" 2>&1)"; rc=$?
  case $rc in
    0)                  bad "$name" "it stayed green under its own planted defect" ;;
    $EXIT_UNEVALUATED)  uneval "$name" "the scene did NOT run, so it proves nothing: $out" ;;
    *)                  ok "$name" ;;
  esac
}

self_test() {
  local t; t="$(mktemp -d)"
  trap 'rm -rf "$t"' RETURN
  export_head "$t" || { printf 'check-at-head: could not export HEAD\n' >&2; return 2; }

  printf '\nBASELINE: every check on an untouched export must PASS, or the scenes below prove nothing\n'
  run_all_checks "$t"
  local base_fail=$FAIL
  if [ "$base_fail" -ne 0 ]; then
    printf '\nself-test: the baseline is already red, so a planted defect proves nothing about the\n'
    printf 'check that was already failing. Fix HEAD first.\n'
    return 1
  fi

  printf '\nPLANTED DEFECTS: each check must now go RED under its own defect\n'

  # 1. imports: break the module the console entry point needs.
  local s; s="$(mktemp -d)"; export_head "$s" >/dev/null 2>&1
  printf '\nimport an module that is not there\n' > "$s/web/model.py"
  printf 'import a_module_that_does_not_exist\n' >> "$s/web/model.py"
  expect_catch "imports catches a broken import" check_imports "$s"
  rm -rf "$s"

  # 2. registration: plant a suite the runner does not name.
  #
  # PLANTED IN `engine/tests/` AND NOT IN `web/tests/`, and the first version of this scene got
  # that wrong. It planted the file under `web/tests/` and the check STAYED GREEN, which read like
  # a broken guard and was actually the guard telling the truth: at that commit
  # `test_suite_registration.py` scanned `engine/tests/` only, so a suite nothing dispatched was
  # silent by construction one directory over. That is instance 5 of the pattern in this file's
  # header, and it is the reason the scene is planted where the check's contract actually is.
  #
  # The scene below then plants in the OTHER two directories and REPORTS rather than asserts,
  # because whether they are covered depends on which commit is being checked, and a self-test
  # that went red on an older commit for having less coverage would be measuring the past.
  s="$(mktemp -d)"; export_head "$s" >/dev/null 2>&1
  printf '#!/usr/bin/env python3\n' > "$s/engine/tests/test_planted_and_undeclared.py"
  expect_catch "suites_registered catches an undispatched suite" check_suites_registered "$s"
  rm -rf "$s"

  # 2b. THE SAME DEFECT IN THE OTHER TWO DIRECTORIES, reported with its answer either way. This is
  # the line that says out loud how wide the guard is at the commit under test.
  local covered="" uncovered=""
  local d
  for d in web/tests queue/tests; do
    s="$(mktemp -d)"; export_head "$s" >/dev/null 2>&1
    printf '#!/usr/bin/env python3\n' > "$s/$d/test_planted_and_undeclared.py"
    if check_suites_registered "$s"; then uncovered="$uncovered $d"; else covered="$covered $d"; fi
    rm -rf "$s"
  done
  printf '  note  registration coverage at this commit: engine/tests plus[%s ]; NOT covered:[%s ]\n' \
    "$covered" "$uncovered"

  # 3. exec bits: RECORD a runner at 100644, the way a checkout with core.filemode=false does.
  #
  # THIS SCENE USED TO chmod -x A FILE IN THE EXPORT, which stopped meaning anything the moment
  # check_exec_bits started reading git's recorded mode instead of the filesystem's. It then
  # reported UNEVALUATED with a true-sounding but wrong reason ("chmod did not take"), when the
  # real reason was that it was planting a defect the check no longer looks for. Rewriting a
  # mechanism without rewriting its scene leaves a check nobody can watch fail, which is the exact
  # thing this file exists to prevent, so the plant now matches the mechanism.
  local g; g="$(mktemp -d)"
  (
    cd "$g" || exit 1
    git init -q
    git config user.email t@t; git config user.name t
    mkdir -p tools
    printf '#!/usr/bin/env bash\ntrue\n' > run-all.sh
    printf '#!/usr/bin/env bash\ntrue\n' > tools/check-at-head.sh
    git add run-all.sh tools/check-at-head.sh
    git update-index --chmod=+x run-all.sh                 # this one is correct
    git update-index --chmod=-x tools/check-at-head.sh     # and THIS is the planted defect
    git commit -qm planted
  ) >/dev/null 2>&1
  if check_exec_bits "" HEAD "$g" >/dev/null 2>&1; then
    bad "exec_bits catches a mode recorded 100644" "it stayed green over a planted 100644"
  else
    ok "exec_bits catches a mode recorded 100644"
  fi
  # AND THE POSITIVE CONTROL, because a check that fails on everything also "catches" the defect.
  ( cd "$g" && git update-index --chmod=+x tools/check-at-head.sh && git commit -qm fixed ) >/dev/null 2>&1
  if check_exec_bits "" HEAD "$g" >/dev/null 2>&1; then
    ok "exec_bits passes once the mode is recorded 100755"
  else
    bad "exec_bits passes once the mode is recorded 100755" \
        "it stayed red after the defect was removed, so its verdict is not about the mode"
  fi
  rm -rf "$g"

  # 3b. THE MECHANISM ITSELF: does git actually ignore a hook without the exec bit?
  #
  # R-GUARD-EXECBIT-01. Everything above reasons about the recorded mode. This scene proves the
  # CONSEQUENCE hermetically, against a throwaway bare repository, so the rule rests on a
  # measurement rather than on what git's documentation is understood to say.
  #
  # A pre-push hook that always exits 1 is installed twice: once without the bit and once with it.
  # If git honours the bit, the first push LANDS and the second is REFUSED. Exits are taken
  # unpiped, because a pipe would hand back the pipeline's status and this whole scene is about a
  # push that succeeds when it should not.
  local h; h="$(mktemp -d)"
  (
    cd "$h" || exit 1
    git init -q --bare origin.git
    git init -q work && cd work
    git config user.email t@t; git config user.name t
    git config --local core.hooksPath .githooks
    mkdir -p .githooks
    printf '#!/usr/bin/env sh\nexit 1\n' > .githooks/pre-push
    chmod -x .githooks/pre-push 2>/dev/null
    echo hello > a.txt; git add a.txt; git commit -qm one
    git remote add origin ../origin.git
    git push -q origin HEAD:refs/heads/main
    printf 'NOBIT_EXIT=%s\n' "$?"
    chmod +x .githooks/pre-push 2>/dev/null
    echo again >> a.txt; git commit -qam two
    git push -q origin HEAD:refs/heads/main
    printf 'BIT_EXIT=%s\n' "$?"
    printf 'BIT_TOOK=%s\n' "$([ -x .githooks/pre-push ] && echo yes || echo no)"
  ) > "$h/out" 2>/dev/null
  local nobit bit took
  nobit="$(sed -n 's/^NOBIT_EXIT=//p' "$h/out")"
  bit="$(sed -n 's/^BIT_EXIT=//p' "$h/out")"
  took="$(sed -n 's/^BIT_TOOK=//p' "$h/out")"
  if [ "$took" != "yes" ]; then
    # THE PLANT DID NOT TAKE. Same filesystem condition as scene 3, and reporting a verdict here
    # would be a verdict about NTFS rather than about git.
    uneval "a hook without the exec bit is ignored by git" \
      "chmod +x did not take on this filesystem, so the two halves are indistinguishable"
  elif [ "$nobit" = "0" ] && [ "$bit" != "0" ]; then
    ok "a hook without the exec bit is ignored by git (push landed $nobit, then refused $bit)"
  elif [ "$nobit" != "0" ] && [ "$bit" != "0" ]; then
    uneval "a hook without the exec bit is ignored by git" \
      "both pushes were refused ($nobit, $bit): this git honours the hook either way, so the \
defect this scene describes cannot occur on this host and is not disproved either"
  else
    bad "a hook without the exec bit is ignored by git" \
      "unexpected pair: without the bit exit $nobit, with it exit $bit"
  fi
  rm -rf "$h"

  # 4. ledger: two files declaring one version, across two directories, which is where it happens.
  #
  # BOTH HALVES OF THE COLLISION ARE PLANTED, on a version number no commit will ever contain. The
  # first version of this scene planted ONE file claiming 48 and expected the repo to supply the
  # other half, which made the scene depend on which migrations happened to exist at the commit
  # under test: it stayed green against a commit with no 48 in it, and read as a broken guard when
  # it was a broken scene. A planted defect has to BE a defect at every commit, or the check it is
  # testing gets blamed for the calendar.
  s="$(mktemp -d)"; export_head "$s" >/dev/null 2>&1
  printf 'INSERT INTO brain.schema_migration (version, name) VALUES (9001, %s);\n' "'a'" \
    > "$s/migrations/9001_planted_a.sql"
  printf 'INSERT INTO brain.schema_migration (version, name) VALUES (9001, %s);\n' "'b'" \
    > "$s/queue/schema/9001_planted_b.sql"
  if check_ledger_unique "$s" >/dev/null 2>&1; then \
    bad "ledger_unique catches two files claiming one version" "it stayed green"; \
    else ok "ledger_unique catches two files claiming one version"; fi
  rm -rf "$s"

  # 4b. THE LANE THE FIRST VERSION OF THIS CHECK COULD NOT SEE: one half in `budget/schema`, the
  # other in `migrations`. Both halves planted, on a number no commit contains, for the same
  # reason as scene 4.
  s="$(mktemp -d)"; export_head "$s" >/dev/null 2>&1
  mkdir -p "$s/budget/schema"
  printf 'INSERT INTO brain.schema_migration (version, name) VALUES (9003, %s);\n' "'a'" \
    > "$s/migrations/9003_planted_a.sql"
  printf 'INSERT INTO brain.schema_migration (version, name) VALUES (9003, %s);\n' "'b'" \
    > "$s/budget/schema/9003_planted_b.sql"
  if check_ledger_unique "$s" >/dev/null 2>&1; then \
    bad "ledger_unique sees budget/schema" "a version claimed by migrations and budget/schema stayed green"; \
    else ok "ledger_unique sees budget/schema"; fi
  rm -rf "$s"

  # 4c. A NON-LEDGER DIRECTORY STARTS DECLARING VERSIONS. The rule is the builder's; the check
  # must enforce it rather than merely not scan the directory.
  s="$(mktemp -d)"; export_head "$s" >/dev/null 2>&1
  mkdir -p "$s/ingest/schema"
  printf 'INSERT INTO brain.schema_migration (version, name) VALUES (9004, %s);\n' "'c'" \
    > "$s/ingest/schema/9004_planted_c.sql"
  if check_ledger_unique "$s" >/dev/null 2>&1; then \
    bad "ledger_unique refuses a ledger row in ingest/schema" "it stayed green"; \
    else ok "ledger_unique refuses a ledger row in ingest/schema"; fi
  rm -rf "$s"

  # 4d. THE TWO LISTS DRIFT: the builder gains a lane this guard does not scan. This is the class
  # of defect behind T04-R-SHARED-02, and it must be red on the day it recurs.
  s="$(mktemp -d)"; export_head "$s" >/dev/null 2>&1
  sed -i 's|SCRATCH_SCHEMA_DIRS:-migrations budget/schema queue/schema}|SCRATCH_SCHEMA_DIRS:-migrations budget/schema queue/schema planted/schema}|' \
    "$s/engine/bin/scratch-db.sh"
  if check_ledger_unique "$s" >/dev/null 2>&1; then \
    bad "ledger_unique catches lane-set drift against scratch-db.sh" "it stayed green"; \
    else ok "ledger_unique catches lane-set drift against scratch-db.sh"; fi
  rm -rf "$s"

  # MERGE, 2026-09-07: both scenes below are the R line's (CAP14-REV-013) and the banner under
  # them is mine. They are independent -- two new planted defects for `ledger_unique`, and a
  # banner that counts a third outcome -- so both are kept whole. Neither lane's intent is
  # traded for the other's.
  # 4e. THE COMMENT DECOY (CAP14-REV-013): a brace-closed comment stating the old default sits
  # above a drifted real assignment. A guard that reads the comment agrees with itself and stays
  # green; this scene requires red.
  s="$(mktemp -d)"; export_head "$s" >/dev/null 2>&1
  sed -i 's|^read -r -a SCHEMA_DIRS <<<"${SCRATCH_SCHEMA_DIRS:-migrations budget/schema queue/schema}"|# default is ${SCRATCH_SCHEMA_DIRS:-migrations budget/schema queue/schema}\nread -r -a SCHEMA_DIRS <<<"${SCRATCH_SCHEMA_DIRS:-migrations budget/schema queue/schema planted/schema}"|' \
    "$s/engine/bin/scratch-db.sh"
  if check_ledger_unique "$s" >/dev/null 2>&1; then \
    bad "ledger_unique ignores a comment decoy above the builder's lane set" "it read the comment and stayed green"; \
    else ok "ledger_unique ignores a comment decoy above the builder's lane set"; fi
  rm -rf "$s"

  # 4f. REORDERED, NOT DRIFTED: the builder lists the same three lanes in another order. Green
  # is required, because order is not part of the contract and a phantom red sends the next
  # person hunting.
  s="$(mktemp -d)"; export_head "$s" >/dev/null 2>&1
  sed -i 's|SCRATCH_SCHEMA_DIRS:-migrations budget/schema queue/schema}|SCRATCH_SCHEMA_DIRS:-budget/schema migrations queue/schema}|' \
    "$s/engine/bin/scratch-db.sh"
  if check_ledger_unique "$s" >/dev/null 2>&1; then \
    ok "ledger_unique accepts a reordered but identical lane set"; \
    else bad "ledger_unique accepts a reordered but identical lane set" "it went red on order alone"; fi
  rm -rf "$s"

  printf '\n%s passed, %s failed, %s unevaluated  (%s checks, each watched failing under its own planted defect)\n' \
    "$PASS" "$FAIL" "$UNEVAL" "$RAN"
  if [ "$UNEVAL" -gt 0 ]; then
    printf "a scene that could not RUN has not been watched failing; this run does not clear it.
" >&2
  fi

  [ "$FAIL" -eq 0 ] || return 1
  [ "$UNEVAL" -eq 0 ] || return 2
  return 0
}

# ------------------------------------------------------------------ main

if [ "${1:-}" = "--self-test" ]; then
  self_test; exit $?
fi

# A COMMIT NAMED ON THE COMMAND LINE, resolved and refused BEFORE anything is exported, so a typo
# says so rather than producing an empty tree and a verdict over nothing.
if [ -n "${1:-}" ]; then
  REF="$(git rev-parse --verify --quiet "$1^{commit}" 2>/dev/null)"
  if [ -z "$REF" ]; then
    printf 'check-at-head: %s is not a commit. Nothing was checked, which is not a pass.\n' "$1" >&2
    exit 2
  fi
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
export_head "$TMP" || { printf 'check-at-head: could not export HEAD\n' >&2; exit 2; }

# The NAME the caller used, not the resolved sha, unless the caller passed a sha itself. The
# hook passes a full 40-character sha and printing both spellings of one commit reads as two.
_named="$(git rev-parse --short "$REF")"
[ "${1:-}" = "HEAD" ] || [ -z "${1:-}" ] && _named="HEAD ($_named)"
case "${1:-}" in "$_named"|"") ;; *) [ "${#1}" -lt 12 ] && _named="$1 ($(git rev-parse --short "$REF"))" ;; esac
printf 'checking %s, exported to a temporary tree\n' "$_named"
run_all_checks "$TMP"

if [ "$RAN" -eq 0 ]; then                                       # DENOMINATOR
  printf '\n0 checks ran. That is not a pass.\n'
  exit 2
fi
printf '\n%s passed, %s failed, %s unevaluated  (over %s checks, against %s and not the working tree)\n' \
  "$PASS" "$FAIL" "$UNEVAL" "$RAN" "$(git rev-parse --short "$REF")"
if [ "$UNEVAL" -gt 0 ]; then
  printf 'UNEVALUATED means this environment could not answer, NOT that the commit is clean.\n' >&2
  printf 'Do not read this run as a pass, and do not "fix" what it could not measure.\n' >&2
fi
[ "$FAIL" -eq 0 ] || exit 1
# EXIT 2, THE CODE THIS FILE ALREADY RESERVES for "could not run a check at all". An unevaluated
# guard must not hand back the same 0 a fully evaluated one does: that is the whole finding.
[ "$UNEVAL" -eq 0 ] || exit 2
exit 0
