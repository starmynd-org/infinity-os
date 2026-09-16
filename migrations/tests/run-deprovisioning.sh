#!/usr/bin/env bash
# Run the two-sided identity suite against a NAMED scratch database and print its exit code from
# inside the same shell, so the code cannot be expanded early by an intermediate shell. Measured
# 2026-09-09: `wsl.exe bash -lc '...; echo $?'` printed 0 over a run that exited 1, because the
# `$?` was expanded before the inner bash saw it. A measurement runs from a file; this is the file.
#
#     ENGINE_SCRATCH_DB=ios_term5_scratch migrations/tests/run-deprovisioning.sh
#
# Refuses a database whose name lacks `scratch`, and refuses brain and brain_scratch by name.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
case "${ENGINE_SCRATCH_DB:-}" in
  "")            printf 'run-deprovisioning: ENGINE_SCRATCH_DB is unset. Name your own scratch store.\n' >&2; exit 2 ;;
  brain)         printf 'run-deprovisioning: refusing brain, the live store.\n' >&2; exit 2 ;;
  brain_scratch) printf 'run-deprovisioning: refusing brain_scratch, the shared one.\n' >&2; exit 2 ;;
  *scratch*)     ;;
  *)             printf 'run-deprovisioning: refusing %s: a scratch store carries "scratch" in its name.\n' "$ENGINE_SCRATCH_DB" >&2; exit 2 ;;
esac
export BRAIN_PG_DB="$ENGINE_SCRATCH_DB"
python3 "$HERE/test_deprovisioning_is_two_sided.py"
RC=$?
printf 'run-deprovisioning: exit %s on %s\n' "$RC" "$ENGINE_SCRATCH_DB"
exit "$RC"
