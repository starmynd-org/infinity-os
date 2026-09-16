#!/usr/bin/env bash
# Every suite for `systemd/`. None needs systemd, a user manager or a database: the suites stub
# `systemctl`, `loginctl` and `ps` and install into a temp directory.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RC=0
INVOKED=0

SUITES=(
  "test_install_n8n_opt_in.sh (install.sh installs and enables the n8n units only with INSTALL_N8N=1)"
)

if [ "${1:-}" = "--list" ]; then
  # ONE BASENAME PER LINE: the contract `engine/tests/test_suite_registration.py` reads.
  for t in "${SUITES[@]}"; do printf '%s\n' "${t%% *}"; done
  exit 0
fi

for entry in "${SUITES[@]}"; do
  suite="${entry%% *}"
  [ -f "$HERE/$suite" ] || { printf '\nMISSING: %s is dispatched and not on disk\n' "$suite"; RC=1; continue; }
  printf '\n============================================================\n%s\n============================================================\n' "$entry"
  INVOKED=$((INVOKED + 1))
  bash "$HERE/$suite" || RC=1
done

ON_DISK=$(ls "$HERE"/test*.py "$HERE"/test*.sh 2>/dev/null | wc -l)
if [ "$INVOKED" -eq 0 ] || [ "$ON_DISK" -eq 0 ]; then   # DENOMINATOR
  printf '\nDENOMINATOR: %s test files invoked of %s on disk. A verdict over an empty set is not a pass.\n' \
    "$INVOKED" "$ON_DISK"
  exit 2
fi
printf '\n%s  (%s of %s test files in systemd/tests/ invoked)\n' \
  "$([ $RC -eq 0 ] && echo 'ALL SUITES GREEN' || echo 'SOMETHING FAILED')" "$INVOKED" "$ON_DISK"
exit $RC
