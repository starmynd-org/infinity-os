#!/usr/bin/env bash
# PLANT A VERSION COLLISION AND WATCH THE EXISTING GUARD REFUSE IT, both branches, exit codes read
# from inside this shell. term-5 brief section 5 item 4: "a guard nobody has watched fail" is not
# a guard; and the tenancy ruling: migrations/** "must REFUSE, not resolve. The guard for that
# already exists: find it and watch it fire." It is engine/bin/scratch-db.sh::migration_list,
# reached by both `ledger` (no database) and `migrate` (before any file is applied).
#
#     migrations/tests/plant-ledger-collision.sh
#
# Touches no database: `ledger` reads the tree only. Plants a copy of the newest migration under a
# 9999_ name, which therefore declares the same ledger version, runs the guard, removes the plant,
# runs the guard again. Never `git add`s anything; the plant is untracked and gone by the end.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
NEWEST="$(ls "$ROOT"/migrations/[0-9][0-9][0-9][0-9]_*.sql | sort | tail -1)"
PLANT="$ROOT/migrations/9999_planted_collision.sql"
[ -n "$NEWEST" ] || { printf 'no migration found to copy; 0 plants made, which is not a result\n'; exit 2; }
[ ! -e "$PLANT" ] || { printf 'a plant is already present at %s; refusing to reason over it\n' "$PLANT"; exit 2; }

printf 'baseline, no plant:\n'
"$ROOT/engine/bin/scratch-db.sh" ledger 2>&1 | grep -E 'NEXT VERSION|claim the same' | sed 's/^/    /'
BASE_RC=${PIPESTATUS[0]}
printf '    exit %s\n' "$BASE_RC"

cp "$NEWEST" "$PLANT"
printf 'planted: %s copied to %s (same declared version)\n' "$(basename "$NEWEST")" "$(basename "$PLANT")"
"$ROOT/engine/bin/scratch-db.sh" ledger 2>&1 | grep -E 'NEXT VERSION|claim the same|9999_planted' | sed 's/^/    /'
PLANT_RC=${PIPESTATUS[0]}
printf '    exit %s\n' "$PLANT_RC"
rm -f "$PLANT"
printf 'plant removed: %s\n' "$([ -e "$PLANT" ] && echo STILL THERE || echo gone)"

"$ROOT/engine/bin/scratch-db.sh" ledger 2>&1 | grep -E 'NEXT VERSION|claim the same' | sed 's/^/    /'
AFTER_RC=${PIPESTATUS[0]}
printf '    exit %s\n' "$AFTER_RC"

if [ "$BASE_RC" -eq 0 ] && [ "$PLANT_RC" -ne 0 ] && [ "$AFTER_RC" -eq 0 ]; then
  printf '\n3 of 3 branches as expected: green without the plant (%s), REFUSED with it (%s), green after removal (%s). The guard fires on the collision and on nothing else.\n' "$BASE_RC" "$PLANT_RC" "$AFTER_RC"
  exit 0
fi
printf '\nNOT as expected: baseline %s, with plant %s, after removal %s. A guard that is red on both branches or green on both is not a guard.\n' "$BASE_RC" "$PLANT_RC" "$AFTER_RC"
exit 1
