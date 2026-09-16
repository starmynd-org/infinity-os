#!/usr/bin/env bash
# Build a store at each interesting ledger and drive the execution spine against every one.
#
# The four are not arbitrary. Each is the last version at which one thing this lane depends on does
# not yet exist, so a fix that only works above it fails HERE and nowhere else:
#
#   64  the fence is still `fencing_token`, on BOTH execution_lease and effect_attempt
#   65  the rename has happened; an approval still cannot be consumed
#   66  single use exists; `outcome` is still one column doing two jobs
#   67  head. Both settle axes exist, and `succeeded` is spelled `success`
#
# EACH STORE IS BUILT FRESH AND KEPT, so a failure can be inspected rather than only reported. They
# are named `brain_tolerance_l<N>` and are ordinary scratch databases: drop them with
# `ENGINE_SCRATCH_DB=brain_tolerance_l64 engine/bin/scratch-db.sh drop`.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REBUILD="${TOLERANCE_REBUILD:-yes}"

# THE LEDGERS ARE DERIVED, NOT TYPED. R-TOLERANCE-LEDGERS-LITERAL-01, CAP14 REV-076.
#
# This read `LEDGERS=(64 65 66 67)`. Head was 67 the day it was written, so migration 68 would not
# have been proven unless somebody remembered to edit this line -- AND THE FAILURE WOULD HAVE BEEN
# SILENT: four ledgers green, exit 0, nothing said about the one it never heard of. A tool that
# reports a clean run over a set it chose by hand is the empty-set problem with a nonzero count.
#
# CAP14's general point, and it is the one worth carrying: A HAND-WRITTEN LIST STANDING WHERE A
# DERIVED ONE BELONGS. It filed three of them together -- this, the reaper's family regex, and my
# own census's verb list -- which is why "enumerate it" is NOT the corrective for point-fixing that
# I had been claiming. An enumeration is another hand-written list and inherits the same failure
# one remove out, which is exactly what happened inside the commit that introduced the census.
#
# The floor stays 64: below it `brain.execution_lease` has no epoch column under either name and
# this port has nothing to offer that store, which `_epoch_column` says in its refusal. The ceiling
# is whatever the tree now holds. Both are printed, because a derived list that silently derives
# nothing is worse than the literal it replaced.
FLOOR="${TOLERANCE_FLOOR:-64}"
mapfile -t LEDGERS < <(
  ENGINE_SCRATCH_DB=unused "$REPO/engine/bin/scratch-db.sh" ledger 2>/dev/null \
    | awk -v floor="$FLOOR" '$1+0 >= floor && $2 ~ /\/.*\.sql$/ {print $1}' \
    | sort -n | uniq)

printf 'ledgers derived from the tree: %s (floor %s, %s version(s))\n' \
  "${LEDGERS[*]:-none}" "$FLOOR" "${#LEDGERS[@]}"
if [ "${#LEDGERS[@]}" -eq 0 ]; then    # DENOMINATOR
  printf 'NOT A PASS: 0 ledgers derived, so nothing would be driven and the summary would be empty.\n' >&2
  exit 2
fi

fail=0
ran=0
declare -a SUMMARY=()

for v in "${LEDGERS[@]}"; do
  # `scratch` IS IN THE NAME BECAUSE DECISION 20 REQUIRES IT, and the omission had a cost beyond
  # tidiness: CAP14 declined to RUN this tool at all, because driving it would have created four
  # stores outside its own disposable-DB protocol. A naming convention nobody can see you following
  # is one an independent party has to treat as a violation. `reap-scratch-dbs.sh` now knows this
  # family too, so the stores are collected rather than left for whoever notices.
  db="brain_scratch_tolerance_l$v"
  printf '\n========== ledger %s (%s)\n' "$v" "$db"
  if [ "$REBUILD" = yes ]; then
    bash "$REPO/engine/bin/ledger-store.sh" "$v" "$db" >/dev/null 2>&1 \
      || { printf '  BUILD FAILED. Re-run ledger-store.sh %s %s to see why.\n' "$v" "$db" >&2
           SUMMARY+=("ledger $v: BUILD FAILED"); fail=1; continue; }
  fi

  # NOT PIPED THROUGH tail OR head. `docs` trap four: the exit code becomes the pipe's, and a run
  # that printed SOMETHING FAILED has been read as green in this repo exactly that way.
  set +e
  BRAIN_PG_DB="$db" python3 "$REPO/engine/bin/prove-tolerance.py"
  rc=$?
  set -e
  ran=$((ran + 1))
  if [ "$rc" -eq 0 ]; then SUMMARY+=("ledger $v: passed"); else SUMMARY+=("ledger $v: FAILED (rc $rc)"); fail=1; fi
done

printf '\n========== summary\n'
for line in "${SUMMARY[@]}"; do printf '  %s\n' "$line"; done

# THE DENOMINATOR. Without it, a loop whose builds all failed prints a summary of nothing and
# returns whatever the last command felt like, and "no ledger reported a failure" reads as a pass.
printf '\n%s of %s ledger(s) were driven.\n' "$ran" "${#LEDGERS[@]}"
if [ "$ran" -eq 0 ]; then
  printf 'NOT A PASS: no store was driven, so nothing was measured.\n' >&2
  exit 2
fi
exit "$fail"
