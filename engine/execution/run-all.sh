#!/usr/bin/env bash
# Every suite the execution lane owns. Packet R02.
#
# WHY THIS IS NOT IN `engine/tests/`. That directory has a runner and a registration check, and the
# check is currently green at 45 of 45. Dropping an unregistered file in would turn it red, and
# registering it there means editing `engine/tests/run-all.sh`, which is a shared file this lane
# does not own. So the execution lane carries its own runner, the way `store/` now does, and the
# Admiral registers both. `engine/tests/run-all.sh` is the shape this mirrors.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$(dirname "$HERE")")"
RC=0
INVOKED=0
NOTRUN=0
NOTRUN_NAMES=""

SUITES=(
  "test_execution.py (leases, fencing, cancellation, idempotency and honest outcomes, packet R02)"
  "test_race.py     (the same guards under eight processes arriving at once, with overlap measured)"
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

for entry in "${SUITES[@]}"; do
  suite="${entry%% *}"
  [ -f "$HERE/$suite" ] || { printf '\nMISSING: %s is dispatched and not on disk\n' "$suite"; RC=1; continue; }
  printf '\n============================================================\n%s\n============================================================\n' "$entry"
  INVOKED=$((INVOKED + 1))
  ( cd "$ROOT" && python3 -m pytest "engine/execution/$suite" -q )
  status=$?
  if [ "$status" -eq 77 ]; then
    NOTRUN=$((NOTRUN + 1)); NOTRUN_NAMES="$NOTRUN_NAMES $suite"
  elif [ "$status" -ne 0 ]; then
    RC=1
  fi
done

ON_DISK=$(ls "$HERE"/test*.py "$HERE"/test*.sh 2>/dev/null | wc -l)
if [ "$INVOKED" -eq 0 ] || [ "$ON_DISK" -eq 0 ]; then   # DENOMINATOR
  printf '\nDENOMINATOR: %s test files invoked of %s on disk. 0 comparisons made. A verdict over an empty set is not a pass.\n' \
    "$INVOKED" "$ON_DISK"
  exit 2
fi
printf '\n%s  (%s of %s test files in engine/execution/ invoked, %s NOT RUN)\n' \
  "$([ $RC -eq 0 ] && echo 'ALL SUITES GREEN' || echo 'SOMETHING FAILED')" \
  "$INVOKED" "$ON_DISK" "$NOTRUN"
exit $RC
