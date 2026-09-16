#!/usr/bin/env bash
# Because-of-you's arithmetic, against a database nothing else is writing to.
#
# ITS OWN DATABASE, not the shared `brain_scratch`, for the same reason test-budget-wiring.sh
# builds one: a suite that asserts on stored state cannot share a store with suites that write
# to it. Measured while writing this file, not assumed. `_because_of_you()` reads THE OPERATOR'S
# LAST 8 ACTIONS in 36 hours, and at 18:11 another lane's suite posted items 0019 through 0030
# into brain_scratch as `operator` inside four seconds. That buried this file's probe below the
# `LIMIT 8` and all three tests reported "returned no row" -- a red that was entirely about the
# neighbour and not at all about the arithmetic under test.
#
# `create` and not `migrate`: this database belongs to this file, so dropping it is safe, and a
# fresh one makes the LIMIT-8 window deterministic rather than merely likely to be clear.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(dirname "$(dirname "$HERE")")"

DB="${BECAUSE_DB:-brain_because}"
case "$DB" in
  brain)         echo "refusing to run against the live store database 'brain'." >&2; exit 1 ;;
  brain_scratch) echo "refusing to run against the shared scratch database: sibling lanes post as the operator into it and this suite reads the operator's last 8 actions." >&2; exit 1 ;;
esac
export BRAIN_PG_DB="$DB"
export ENGINE_SCRATCH_DB="$DB"
# The runner exports this and `post` inherits it, which would make every task here a child of the
# live task that launched the suite -- and that parent does not exist in a fresh database, so
# every seed dies on "no such parent task". This suite posts roots.
unset SWARM_PARENT_TASK

if ! "$REPO/engine/bin/scratch-db.sh" create; then
  echo "test-because-of-you.sh: could not build $DB. Not running: every result would be about" >&2
  echo "the schema rather than about the arithmetic." >&2
  exit 1
fi

cd "$REPO" || exit 1
python3 -m web.tests.test_because_of_you
