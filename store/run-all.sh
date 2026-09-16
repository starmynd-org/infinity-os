#!/usr/bin/env bash
# Every suite the store lane owns, in the order a reader should read them.
#
# WHY THIS FILE EXISTS AT ALL. Until 2026-09-06 `store/` had no runner. Its suites were committed,
# green when somebody ran them by hand, and DISPATCHED BY NOTHING -- instance 5 of the pattern
# `tools/check-at-head.sh` was built to close ("two committed suites were dispatched by nothing").
# `test_narrow_waist.py` had been in that position for weeks: it is the suite that proves no code in
# this repository writes outside a registered transition, and no runner named it.
#
# Found while adding `test_authority.py` for packet R01, filed with the Admiral as T04-R-SHARED-03
# rather than fixed silently, and written here on that decision. `engine/tests/run-all.sh` is the
# shape this mirrors, deliberately, down to the denominator on the banner.
#
# The mirror was INCOMPLETE until 2026-09-07 and the gap cost Terminal 08 a wasted review pass:
# it carried the dispatch shape and not the migrate precondition. See R-t04-STORE-RUNNER-01
# below the dispatch list for what was missed and why.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
RC=0
INVOKED=0            # suites this run actually dispatched. The banner's denominator.
NOTRUN=0             # suites that declared NOT RUN by exiting 77.
NOTRUN_NAMES=""

# EXIT 77 IS NOT RUN, AND IT DOES NOT TURN THIS RUNNER RED, on the rule in `docs/SUITE-INPUT-RULE.md`
# and for the reason `engine/tests/run-all.sh` sets out at length: a suite whose input is live state
# declares NOT RUN with the reason rather than reporting the environment as a code defect.

# THE DISPATCH LIST, declared above anything that costs a database so `--list` can print it without
# building one.
SUITES=(
  "test_narrow_waist.py (there is ONE write path and nothing reaches around it)"
  "test_authority.py    (grants, revocations and approvals: the storage boundary, packet R01)"
  "test_git_ref_repo_identity.sh (a git ref names which repository it is a ref IN)"
  "test_lineage_by_construction.sh (lineage columns exist because a function added them)"
  "test_thread_budget_kind.sh (a thread event carries the budget kind it was raised under)"
)

if [ "${1:-}" = "--list" ]; then
  # ONE BASENAME PER LINE, which is the contract `test_suite_registration.py` compares against
  # and which this file did not keep: it printed the whole entry, description and all, so every
  # name read as dispatched-but-not-on-disk AND every file read as on-disk-but-not-dispatched.
  # Nothing compared the two until R-SUITEREG-01 registered this runner, so a runner written to
  # close the dispatched-by-nothing pattern was itself the thing nobody was checking.
  # `engine/tests/run-all.sh` is the reference and does exactly this.
  for t in "${SUITES[@]}"; do printf '%s\n' "${t%% *}"; done
  exit 0
fi

# PLACED AFTER THE `--list` EARLY EXIT, and that position is the whole of the fix's
# correctness. `--list` is a MACHINE CONTRACT that `engine/tests/test_suite_registration.py`
# calls on every discovered runner with no environment at all: it must cost no database and
# now no variable either. I put this guard above the exit first, and `--list` began refusing
# with nothing set -- which would have made every one of these runners report as "does not
# answer --list", turning a name-safety fix into a registration failure. Same lesson as
# R-t04-STORE-RUNNER-01's migrate step, one file over, learned twice.
# R-DBNAME-01: NAME YOUR OWN STORE, AND REFUSE A DEFAULT THAT IS SOMEBODY ELSE'S.
#
# This line was `export BRAIN_PG_DB="${ENGINE_SCRATCH_DB:-brain_scratch}"`. With the variable
# unset it did not fall back to a lane-private name: it pointed every suite below at
# `brain_scratch`, THE STORE EVERY LANE'S SUITE READS. Not the live store, so not the worst case,
# but the shared one -- and chosen by this file rather than by whoever ran it. Measured on the
# integration line by Terminal 26 while auditing which files SET a database name rather than read
# one; `deploy/tests/run-all.sh` carries the identical line and is T12's.
#
# The rule, from decision 20 and from `scratch-db.sh`'s own guard, applied here: a run names its
# own store, and a name that is `brain`, is the shared `brain_scratch`, or does not contain
# "scratch" is refused rather than used. Refused and not defaulted, because a default is exactly
# how the shared name got here.
: "${ENGINE_SCRATCH_DB:=}"
case "$ENGINE_SCRATCH_DB" in
  "")            printf '%s: ENGINE_SCRATCH_DB is unset. Name your own store; there is no default,\n' "$0" >&2
                 printf '  because the default was `brain_scratch`, which every lane reads.\n' >&2
                 printf '  e.g. ENGINE_SCRATCH_DB=brain_scratch_$USER_$$ %s\n' "$0" >&2
                 exit 2 ;;
  brain)         printf '%s: refusing `brain`. That is the live store.\n' "$0" >&2; exit 2 ;;
  brain_scratch) printf '%s: refusing `brain_scratch`. It is shared: another lane is reading it,\n' "$0" >&2
                 printf '  and this runner migrates and truncates what it is given.\n' >&2
                 exit 2 ;;
  *scratch*)     ;;
  *)             printf '%s: refusing %s: a scratch store must have "scratch" in its name, so that\n' "$0" "$ENGINE_SCRATCH_DB" >&2
                 printf '  anything reading a database list can tell it from a real one.\n' >&2
                 exit 2 ;;
esac
export BRAIN_PG_DB="$ENGINE_SCRATCH_DB"

# BRING THE DATABASE UP TO THE TREE BEFORE RUNNING ANYTHING. R-t04-STORE-RUNNER-01, raised by
# Terminal 08 on 2026-09-07: T08 hit 17 failures in `test_authority.py` that all had ONE cause, a
# schema this database did not have, and read as seventeen code defects. `engine/tests/run-all.sh`
# has done this since its own morning of forty-failures-one-cause; this runner mirrored that file
# and did not.
#
# WHY THE MIRROR MISSED IT, since the Admiral asked and the answer is the useful part. I mirrored
# what LOOKED LIKE THE PATTERN -- the declared SUITES array, `--list`, the counted ON_DISK
# denominator, exit 77 as NOT RUN, never piping a runner -- because those are the parts of that file
# that are visibly about dispatch. The migrate step sits above the loop, touches no suite, and reads
# like local setup for the engine lane. So I copied the shape and skipped the precondition, which is
# the ordinary way a mirror goes wrong: you carry across what is legible as structure and leave what
# is legible as housekeeping.
#
# It was never housekeeping, and engine's own comment says so in a sentence that is just as true
# here: SEVERAL LANES SHARE THIS TREE, SO THE TREE GAINS MIGRATIONS WHILE THE SUITE IS RUNNING. The
# drift is structural. `store/test_authority.py` reads five tables that arrived in migrations 57,
# 61, 63, 66 and 67, every one of them written during this sprint, so this runner was MORE exposed
# to that drift than the file it copied, not less.
#
# `migrate` and not `create`, for engine's reason: create drops the database, and dropping it under
# a sibling lane mid-run is a worse failure than the one being fixed. Not conditional and not
# `|| true`: a migration in the tree that will not apply is a real finding, and it stops the run
# here where it is legible rather than as seventeen assertion failures below.
if ! "$ROOT/engine/bin/scratch-db.sh" migrate; then
  printf '\nrun-all: could not bring %s up to migrations/. Not running the suites: every result\n' \
    "$BRAIN_PG_DB"
  printf 'below would be about the schema, not about the code.\n'
  exit 1
fi

for entry in "${SUITES[@]}"; do
  suite="${entry%% *}"
  [ -f "$HERE/$suite" ] || { printf '\nMISSING: %s is dispatched and not on disk\n' "$suite"; RC=1; continue; }
  printf '\n============================================================\n%s\n============================================================\n' "$entry"
  INVOKED=$((INVOKED + 1))
  case "$suite" in
    *.py) ( cd "$ROOT" && python3 -m pytest "store/$suite" -q ) ;;
    *.sh) ( cd "$ROOT" && bash "store/$suite" ) ;;
  esac
  # NEVER PIPED. `engine/tests/run-all.sh` says why and this repo has the scar: a run that printed
  # SOMETHING FAILED was read as green because the exit code became the pipe's.
  status=$?
  if [ "$status" -eq 77 ]; then
    NOTRUN=$((NOTRUN + 1)); NOTRUN_NAMES="$NOTRUN_NAMES $suite"
  elif [ "$status" -ne 0 ]; then
    RC=1
  fi
done

# ON_DISK is COUNTED, not declared, so a file dropped into store/ and never registered moves this
# number and not INVOKED, and the gap shows up on the next run rather than a month later.
ON_DISK=$(ls "$HERE"/test*.py "$HERE"/test*.sh 2>/dev/null | wc -l)
if [ "$INVOKED" -eq 0 ] || [ "$ON_DISK" -eq 0 ]; then   # DENOMINATOR
  printf '\nDENOMINATOR: %s test files invoked of %s on disk. 0 comparisons made. A verdict over an empty set is not a pass.\n' \
    "$INVOKED" "$ON_DISK"
  exit 2
fi
printf '\n%s  (%s of %s test files in store/ invoked, %s NOT RUN)\n' \
  "$([ $RC -eq 0 ] && echo 'ALL SUITES GREEN' || echo 'SOMETHING FAILED')" \
  "$INVOKED" "$ON_DISK" "$NOTRUN"
if [ "$NOTRUN" -gt 0 ]; then
  printf 'NOT RUN, with the input each one lacked printed above:%s\n' "$NOTRUN_NAMES"
fi
exit $RC
