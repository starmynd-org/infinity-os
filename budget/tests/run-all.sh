#!/usr/bin/env bash
# Every suite the budget lane owns in this directory. Hermetic: no database, no network, no
# credential. test_runner_truth_up.py proves the runner's end-of-run charge composes with the
# guard's mid-run steps instead of doubling them (finding C5-COMP-1).
#
# WHY THIS FILE EXISTS AT ALL. Registration discovers a suite root by the presence of a runner in
# it, and a test file in a directory with no runner is dispatched by nothing and seen by nothing:
# the planted-file control that proved it went unreported at the integration tip this was written
# on. So the runner is the registration. Its `--list` is the contract the registration suite reads.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONDONTWRITEBYTECODE=1

# THE DECLARED LIST, IN AN ARRAY, so `--list` prints exactly what the loop dispatches.
BUDGET_SUITES=(
  "test_runner_truth_up.py  (the end-of-run charge trues up against the guard's steps, C5-COMP-1)"
)

# `--list` prints one basename per line and exits before anything runs.
if [ "${1:-}" = "--list" ]; then
  for t in "${BUDGET_SUITES[@]}"; do printf '%s\n' "${t%% *}"; done
  exit 0
fi

RC=0
INVOKED=0
NOTRUN=0
NOTRUN_NAMES=""
for t in "${BUDGET_SUITES[@]}"; do
  f="${t%% *}"
  printf '\n============================================================\n%s\n============================================================\n' "$t"
  INVOKED=$((INVOKED + 1))
  python3 -B "$HERE/$f"
  case "$?" in
    0)  ;;
    77) NOTRUN=$((NOTRUN + 1)); NOTRUN_NAMES="$NOTRUN_NAMES $f"
        printf '  ^^ NOT RUN (INHERENT): the suite said which input it lacked. Counted, not red.\n' ;;
    *)  RC=1 ;;
  esac
done

ON_DISK=$(ls "$HERE"/test_*.py 2>/dev/null | wc -l)
DECLARED=${#BUDGET_SUITES[@]}
if [ "$INVOKED" -eq 0 ]; then                                   # DENOMINATOR
  printf '\nDENOMINATOR: 0 suites dispatched of %s test_*.py on disk. 0 comparisons made.\n' "$ON_DISK"
  printf 'A verdict over an empty set is not a pass.\n'
  exit 2
fi
if [ $RC -eq 0 ]; then
  printf '\nALL BUDGET SUITES GREEN  (%s of %s declared invoked; %s declared of %s test_*.py on disk; %s NOT RUN)\n' \
    "$INVOKED" "$DECLARED" "$DECLARED" "$ON_DISK" "$NOTRUN"
else
  printf '\nSOMETHING FAILED  (%s of %s declared invoked; %s declared of %s test_*.py on disk; %s NOT RUN)\n' \
    "$INVOKED" "$DECLARED" "$DECLARED" "$ON_DISK" "$NOTRUN"
fi
if [ "$DECLARED" -ne "$ON_DISK" ]; then
  printf 'REGISTRATION GAP: %s declared against %s test_*.py file(s) in this directory.\n' \
    "$DECLARED" "$ON_DISK"
  RC=1
fi
if [ "$NOTRUN" -gt 0 ]; then
  printf 'NOT RUN:%s\n' "$NOTRUN_NAMES"
fi
exit $RC
