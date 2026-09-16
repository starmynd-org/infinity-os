#!/usr/bin/env bash
# Every suite the deploy lane owns. Packet R06.
#
# WRITTEN WITH THE SUITE RATHER THAN AFTER IT. This lane already learned, one directory over, that a
# committed suite no runner names is instance 5 of the pattern `tools/check-at-head.sh` exists to
# close, so `store/run-all.sh` had to be written retroactively and immediately found a red suite
# nobody was running. Writing this one first was cheaper.
#
# `prove-the-suite-notices.sh` is deliberately NOT dispatched here. It builds a store, breaks it and
# drops it, to demonstrate that the behaviour assertions catch a restore that kept every row and
# lost every rule. A demonstration that mutates a store is something you run on purpose, not on
# every green run. It is named here so a reader knows it exists rather than finding it by listing
# the directory.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$(dirname "$HERE")")"
RC=0
INVOKED=0
NOTRUN=0

SUITES=(
  "test_restore_proves_behaviour.py (a restore is proven by behaviour surviving, not row counts)"
)

if [ "${1:-}" = "--list" ]; then
  # ONE BASENAME PER LINE. `engine/tests/run-all.sh` is the reference and does exactly this.
  # This printed the whole entry, description and all, so `test_suite_registration.py`
  # reported the SAME file twice in opposite directions -- once as on-disk-not-dispatched
  # and once as dispatched-not-on-disk. Two halves of one mismatch, reading like two
  # defects. Found by Terminal 25; the check now normalises as well, but a producer that
  # keeps its own contract is worth more than a consumer that repairs it.
  for t in "${SUITES[@]}"; do printf '%s\n' "${t%% *}"; done
  exit 0
fi

# R-DBNAME-01, the same guard `store/run-all.sh` and `engine/execution/run-all.sh` carry.
#
# This line was `export BRAIN_PG_DB="${ENGINE_SCRATCH_DB:-brain_scratch}"`, at the top of the file.
# With the variable unset it did not fall back to a lane-private name: it pointed the suite at
# `brain_scratch`, THE STORE EVERY LANE'S SUITE READS -- chosen by this file rather than by whoever
# ran it. Measured on the integration line by Terminal 26 while auditing which files SET a database
# name rather than read one.
#
# AND IT IS PLACED BELOW THE `--list` ARM, WHICH IS THE WHOLE OF ITS CORRECTNESS. `--list` is a
# machine contract that `engine/tests/test_suite_registration.py` calls on every discovered runner
# with NO environment: it must cost no database and no variable. Putting this above the exit makes
# `--list` refuse with nothing set, and the runner then reports as "does not answer --list" -- a
# name-safety fix turning itself into a registration failure. That is not hypothetical: it is what
# happened when the same guard went into the other two runners, and it was caught there.
: "${ENGINE_SCRATCH_DB:=}"
case "$ENGINE_SCRATCH_DB" in
  "")            printf '%s: ENGINE_SCRATCH_DB is unset. Name your own store; there is no default,\n' "$0" >&2
                 printf '  because the default was `brain_scratch`, which every lane reads.\n' >&2
                 exit 2 ;;
  brain)         printf '%s: refusing `brain`. That is the live store.\n' "$0" >&2; exit 2 ;;
  brain_scratch) printf '%s: refusing `brain_scratch`. It is shared: another lane is reading it.\n' "$0" >&2
                 exit 2 ;;
  *scratch*)     ;;
  *)             printf '%s: refusing %s: a scratch store must have "scratch" in its name.\n' "$0" "$ENGINE_SCRATCH_DB" >&2
                 exit 2 ;;
esac
export BRAIN_PG_DB="$ENGINE_SCRATCH_DB"

for entry in "${SUITES[@]}"; do
  suite="${entry%% *}"
  [ -f "$HERE/$suite" ] || { printf '\nMISSING: %s is dispatched and not on disk\n' "$suite"; RC=1; continue; }
  printf '\n============================================================\n%s\n============================================================\n' "$entry"
  INVOKED=$((INVOKED + 1))
  ( cd "$ROOT" && python3 -m pytest "deploy/tests/$suite" -q )
  status=$?
  if [ "$status" -eq 77 ]; then NOTRUN=$((NOTRUN + 1)); elif [ "$status" -ne 0 ]; then RC=1; fi
done

# COUNTED, not declared, and it deliberately does not count the demonstration script: `test*.py`
# and `test*.sh` are the dispatched shapes, and `prove-the-suite-notices.sh` is neither by design.
ON_DISK=$(ls "$HERE"/test*.py "$HERE"/test*.sh 2>/dev/null | wc -l)
if [ "$INVOKED" -eq 0 ] || [ "$ON_DISK" -eq 0 ]; then   # DENOMINATOR
  printf '\nDENOMINATOR: %s test files invoked of %s on disk. 0 comparisons made. A verdict over an empty set is not a pass.\n' \
    "$INVOKED" "$ON_DISK"
  exit 2
fi
printf '\n%s  (%s of %s test files in deploy/tests/ invoked, %s NOT RUN)\n' \
  "$([ $RC -eq 0 ] && echo 'ALL SUITES GREEN' || echo 'SOMETHING FAILED')" \
  "$INVOKED" "$ON_DISK" "$NOTRUN"
exit $RC
