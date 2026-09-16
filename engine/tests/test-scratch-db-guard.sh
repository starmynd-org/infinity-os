#!/usr/bin/env bash
# The three ways `engine/bin/scratch-db.sh` could destroy a store nobody asked it to touch,
# asserted by RUNNING the bad shapes rather than by reading the source. Task 0248.
#
# WHAT THIS IS ABOUT, because a guard whose incident is not written down is a guard the next
# refactor deletes. On 2026-08-18 at 22:19:03Z an agent working task 0208 ran, verbatim:
#
#     ...; echo "=== usage ==="; ./engine/bin/scratch-db.sh 2>&1 | head -25
#
# It was reading for the usage line. Every SIBLING command in that same run carried
# `ENGINE_SCRATCH_DB=brain_console`; this one carried none, because it was never meant to touch a
# database at all. Two defaults composed: the dispatch read `case "${1:-create}"`, so no
# subcommand meant `create`, and only an UNRECOGNISED word reached the usage arm; and the name
# read `${ENGINE_SCRATCH_DB:-brain_scratch}`, so no override meant the store every lane's suite
# reads. `cmd_create` opens with DROP DATABASE ... WITH (FORCE). The shared scratch went from
# ledger 30 to ledger 7 and stayed there for 85 minutes with three terminals live, and the
# Postgres log held NO ERROR for the window because what stopped the rebuild was SIGPIPE from
# `head -25` under `set -euo pipefail`. Tasks 0227 (forensics and repair) and 0248 (the fix).
#
# WHY IT SWAPS DEFAULT_DB IN A COPY RATHER THAN RUNNING THE REAL SCRIPT BARE. The defect is
# ABOUT the default, so a test that exercises it has to let the default fire -- and the real
# default is `brain_scratch`, which is the one database this suite must never risk. So scenes 1
# to 4 run a byte-identical copy with `DEFAULT_DB=` rewritten to a name this suite owns. That is
# the whole rewrite; one `sed` on one assignment, asserted below before anything is run, so the
# copy cannot silently drift into testing something else.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$(dirname "$HERE")")"          # the repo
REAL="$ROOT/engine/bin/scratch-db.sh"
CONTAINER="${BRAIN_PG_CONTAINER:-brain-postgres}"
SECRETS="${BRAIN_SECRET_DIR:-$HOME/.brain-postgres-secrets}"

# Inherited from a parent shell, either of these would make every scene below name a database
# this suite did not choose -- which is the defect, not the test for it.
unset ENGINE_SCRATCH_DB BRAIN_PG_DB SCRATCH_SCHEMA_DIRS

DB="brain_t_0248_$$"                 # populated, and the copy's default points AT it
COLD="brain_t_0248_cold_$$"          # deliberately never built

PASS=0
FAIL=0
ok()   { PASS=$((PASS + 1)); printf '  ok    %s\n' "$1"; }
bad()  { FAIL=$((FAIL + 1)); printf '  FAIL  %s\n' "$1"; [ -n "${2:-}" ] && printf '        %s\n' "$2"; }
check(){ if [ "$2" = "$3" ]; then ok "$1"; else bad "$1" "wanted [$3], got [$2]"; fi; }

echo "test-scratch-db-guard.sh  --  asking this script what it takes must not destroy a store"
echo

su_psql() {
  docker exec -i -e PGPASSWORD="$(cat "$SECRETS/brain-postgres-bootstrap-superuser")" \
    "$CONTAINER" psql -tA -v ON_ERROR_STOP=1 -h 127.0.0.1 -U postgres "$@" </dev/null
}

# THE FINGERPRINT, and pg_database.oid is the load-bearing field. A ledger number and a table
# count both survive a drop-and-rebuild that got far enough, which is exactly the state the
# incident left and exactly what made it invisible. The oid does not: a rebuilt database is a
# NEW row in pg_database. Any scene whose oid moved saw a DROP, whatever else it read.
fingerprint() {
  su_psql -d postgres -c "SELECT coalesce((SELECT oid::text FROM pg_database WHERE datname='$1'), 'absent')" \
    | tr -d '[:space:]'
  printf ' '
  su_psql -d "$1" -c "SELECT coalesce(max(version)::text,'none') || '/' ||
     (SELECT count(*) FROM information_schema.tables
       WHERE table_schema='brain' AND table_type='BASE TABLE')
     FROM brain.schema_migration" 2>/dev/null | tr -d '[:space:]' || printf 'unreadable'
}

TMP="$(mktemp -d)"
cleanup() {
  local d
  for d in "$DB" "$COLD"; do
    su_psql -d postgres -c "DROP DATABASE IF EXISTS $d WITH (FORCE)" >/dev/null 2>&1
  done
  rm -rf "$TMP"
}
trap cleanup EXIT

# The copy, and the proof that it is a copy. REPO is derived from BASH_SOURCE/../.., so the
# schema directories have to be reachable from the copy's own root; symlinks, because this suite
# is asserting on the dispatch and the guards, not on the migrations.
mkdir -p "$TMP/engine/bin" "$TMP/budget" "$TMP/queue"
sed "s/^DEFAULT_DB=brain_scratch\$/DEFAULT_DB=$DB/" "$REAL" > "$TMP/engine/bin/scratch-db.sh"
chmod +x "$TMP/engine/bin/scratch-db.sh"
ln -s "$ROOT/migrations"     "$TMP/migrations"
ln -s "$ROOT/budget/schema"  "$TMP/budget/schema"
ln -s "$ROOT/queue/schema"   "$TMP/queue/schema"
COPY="$TMP/engine/bin/scratch-db.sh"

echo "0. the copy differs from the shipped script in exactly one line, and it is the default name"
check "1. DEFAULT_DB now names this suite's own store" \
      "$(grep -c "^DEFAULT_DB=$DB\$" "$COPY")" "1"
check "2. and nothing else changed" \
      "$(diff "$REAL" "$COPY" | grep -c '^[<>]')" "2"
check "3. the shipped script still defaults to the shared store, which is what makes this matter" \
      "$(grep -c '^DEFAULT_DB=brain_scratch$' "$REAL")" "1"
echo

echo "1. a bare invocation prints usage and touches no database"
if ! ENGINE_SCRATCH_DB="$DB" "$REAL" create >/dev/null 2>&1; then
  bad "the store this suite asserts against was built" "scratch-db.sh create failed"
  echo; echo "$PASS passed, $((FAIL)) failed"; exit 1
fi
BEFORE="$(fingerprint "$DB")"
OUT="$("$COPY" 2>&1)"; RC=$?
AFTER="$(fingerprint "$DB")"
check "1. it exits 2, not 0: no subcommand is an error, and a caller that still believes bare
        means create must not read a success" "$RC" "2"
case "$OUT" in
  *"usage: scratch-db.sh <subcommand>"*) ok "2. it printed the usage line" ;;
  *) bad "2. it printed the usage line" "said: $(printf '%s' "$OUT" | head -1)" ;;
esac
check "3. the store is UNCHANGED -- same pg_database.oid, so it was never dropped and rebuilt" \
      "$AFTER" "$BEFORE"
echo

echo "2. and so does the exact command that caused the incident"
BEFORE="$(fingerprint "$DB")"
OUT="$("$COPY" 2>&1 | head -25)"
AFTER="$(fingerprint "$DB")"
case "$OUT" in
  *"usage: scratch-db.sh <subcommand>"*) ok "1. \`scratch-db.sh 2>&1 | head -25\` prints usage" ;;
  *) bad "1. \`scratch-db.sh 2>&1 | head -25\` prints usage" "said: $(printf '%s' "$OUT" | head -1)" ;;
esac
check "2. the whole text fits inside head -25, so the reader gets all of it" \
      "$(printf '%s\n' "$OUT" | grep -c 'DESTRUCTIVE')" "2"
check "3. the store is UNCHANGED" "$AFTER" "$BEFORE"
echo

echo "3. a destructive verb refuses a name that came from the default rather than from a caller"
for verb in create drop; do
  BEFORE="$(fingerprint "$DB")"
  OUT="$("$COPY" "$verb" 2>&1)"; RC=$?
  AFTER="$(fingerprint "$DB")"
  check "1. \`$verb\` with ENGINE_SCRATCH_DB unset exits nonzero" "$([ "$RC" -ne 0 ] && echo yes || echo no)" "yes"
  case "$OUT" in
    *"refusing to $verb"*"came from the DEFAULT"*) ok "2. and says the name was not chosen" ;;
    *) bad "2. and says the name was not chosen" "said: $(printf '%s' "$OUT" | head -1)" ;;
  esac
  case "$OUT" in
    *"ENGINE_SCRATCH_DB=$DB"*) ok "3. and names the opt-in that gets it done, which a runner sets
        as easily as a human types it -- there is no prompt to answer" ;;
    *) bad "3. and names the opt-in" "said: $(printf '%s' "$OUT" | head -3 | tr '\n' ' ')" ;;
  esac
  check "4. the store is UNCHANGED" "$AFTER" "$BEFORE"
done
# The other half of the same rule: naming it is enough, and nothing else is asked of you.
ENGINE_SCRATCH_DB="$DB" "$COPY" create >/dev/null 2>&1
check "5. and naming it explicitly still rebuilds: the guard is an opt-in, not a wall" \
      "$([ "$(fingerprint "$DB")" != "$BEFORE" ] && echo rebuilt || echo untouched)" "rebuilt"
echo

echo "4. the safe doors are exactly as cheap as they were: a MISSING default is still built"
check "1. $COLD does not exist yet" \
      "$(su_psql -d postgres -c "SELECT count(*) FROM pg_database WHERE datname='$COLD'" | tr -d '[:space:]')" "0"
sed -i "s/^DEFAULT_DB=$DB\$/DEFAULT_DB=$COLD/" "$COPY"
OUT="$("$COPY" ensure 2>&1)"; RC=$?
check "2. \`ensure\` with no ENGINE_SCRATCH_DB builds it -- the refusal fires only where a
        populated store would have been destroyed, and there was nothing there" "$RC" "0"
case "$OUT" in
  *"ready:"*) ok "3. and it reported a finished store" ;;
  *) bad "3. and it reported a finished store" "said: $(printf '%s' "$OUT" | tail -1)" ;;
esac
OUT="$("$COPY" ensure 2>&1)"
case "$OUT" in
  *"is current"*|*"migrated"*) ok "4. and the warm second call reconciles instead of refusing" ;;
  *) bad "4. and the warm second call reconciles" "said: $(printf '%s' "$OUT" | tail -1)" ;;
esac
sed -i "s/^DEFAULT_DB=$COLD\$/DEFAULT_DB=$DB/" "$COPY"
echo

echo "5. a build killed mid-migration does not leave a store that looks finished"
# The real script here, with the name given explicitly, because this scene is about what an
# INTERRUPTED create leaves behind and not about which database it chose.
ENGINE_SCRATCH_DB="$DB" "$REAL" create 2>&1 | head -25 >/dev/null
RC=${PIPESTATUS[0]}
check "1. SIGPIPE from \`head\` kills it under set -euo pipefail, as it did on 2026-08-18" \
      "$RC" "141"
LEDGER="$(su_psql -d "$DB" -c 'SELECT max(version) FROM brain.schema_migration' | tr -d '[:space:]')"
check "2. and what it left reports a PLAUSIBLE ledger number, which is why this was invisible" \
      "$([ -n "$LEDGER" ] && [ "$LEDGER" -lt 30 ] 2>/dev/null && echo "short but plausible" || echo "$LEDGER")" \
      "short but plausible"
check "3. but the store now SAYS it is half-built, in a row written first and dropped last, which
        is true under SIGKILL and a pulled container as a trap would not be" \
      "$(su_psql -d "$DB" -c "SELECT count(*) FROM information_schema.tables
          WHERE table_schema='public' AND table_name='scratch_build_in_progress'" | tr -d '[:space:]')" "1"
OUT="$(ENGINE_SCRATCH_DB="$DB" "$REAL" migrate 2>&1)"; RC=$?
check "4. \`migrate\` refuses it rather than asserting against it" "$RC" "1"
case "$OUT" in
  *"HALF-BUILT"*) ok "5. and says so in those words" ;;
  *) bad "5. and says so in those words" "said: $(printf '%s' "$OUT" | head -1)" ;;
esac
OUT="$(ENGINE_SCRATCH_DB="$DB" "$REAL" ensure 2>&1)"
case "$OUT" in
  *"HALF-BUILT"*) ok "6. and so does \`ensure\`, which is the door every suite's preflight calls" ;;
  *) bad "6. and so does \`ensure\`" "said: $(printf '%s' "$OUT" | head -1)" ;;
esac
ENGINE_SCRATCH_DB="$DB" "$REAL" create >/dev/null 2>&1
check "7. a COMPLETED build leaves none of it behind: a good store looks exactly as it did" \
      "$(su_psql -d "$DB" -c "SELECT count(*) FROM information_schema.tables
          WHERE table_schema='public'" | tr -d '[:space:]')" "0"
echo

# ---------------------------------------------------------------------------------------------
# 6. THE LEDGER DOOR, AND IT TOUCHES NO DATABASE. Task 0408.
#
# `ledger` is the door a lane is told to read before it names a migration file, and until 0408 it
# printed the (version, file) list and nothing else -- so it could not answer the question the
# lane actually has, which is "what number do I take". `migration_list` already refuses two files
# claiming the SAME version. It does not notice a GAP, and a gap is the case where both
# application orders are legal and DIFFERENT: a file at a hole applies between the hole's
# neighbours on a fresh `create` and after every higher version an existing store already recorded
# on a `migrate`. On 2026-08-27 lane E was assigned 36 to 39, wrote 36, 37 and 38, and left 39;
# live `brain` read 41 rows, max 42, missing 39 on that date and again on 2026-08-29.
#
# 6.2 IS THE POSITIVE CONTROL AND IT IS THE POINT OF THIS SCENE. An assertion that the real tree
# has no holes passes just as happily when the hole detector is broken as when the tree is clean,
# so it is not evidence on its own. Removing the fence from a COPY of the tree and watching the
# report name 39 is what separates those two worlds.
echo "6. \`ledger\` reports the hole it finds, and the next version, without opening a connection"

LTMP="$(mktemp -d)"
mkdir -p "$LTMP/engine/bin" "$LTMP/budget" "$LTMP/queue"
cp "$REAL" "$LTMP/engine/bin/scratch-db.sh"
cp -r "$ROOT/migrations"    "$LTMP/migrations"
ln -s "$ROOT/budget/schema" "$LTMP/budget/schema"
ln -s "$ROOT/queue/schema"  "$LTMP/queue/schema"
LCOPY="$LTMP/engine/bin/scratch-db.sh"
mkdir -p "$LTMP/nothing_here"

# max + 1 computed HERE, from the files, so this scene does not go stale on the day 46 lands and
# does not silently agree with the script by reading the script's own answer back.
LMAX="$(for d in migrations budget/schema queue/schema; do
          for f in "$ROOT/$d"/[0-9][0-9][0-9][0-9]_*.sql; do
            [ -e "$f" ] || continue
            tr '\n' ' ' < "$f" \
              | grep -oE 'INSERT INTO brain\.schema_migration \(version, *name\) *VALUES *\( *[0-9]+' \
              | grep -oE '[0-9]+ *$' | tr -d ' '
          done
        done | sort -n | tail -1)"
LNEXT=$((LMAX + 1))

OUT="$("$REAL" ledger 2>&1)"; RC=$?
check "1. it exits 0 against the real tree" "$RC" "0"
case "$OUT" in
  *"NEXT VERSION IS $LNEXT -- take max(version) + 1"*)
    ok "2. and answers the lane's actual question with max(version) + 1 = $LNEXT, rather than
        leaving them to pick the lowest free number off the list" ;;
  *) bad "2. it names the next version as max + 1 = $LNEXT" "said: $(printf '%s' "$OUT" | tail -2 | tr '\n' ' ')" ;;
esac
case "$OUT" in
  *"no holes"*) ok "3. the real tree has NO HOLES, which is the invariant migration 39's fence
        exists to hold -- a hole here means a file that fills it has two application orders" ;;
  *) bad "3. the real tree has no holes" "said: $(printf '%s' "$OUT" | grep -F 'versions read')" ;;
esac
case "$OUT" in
  *"versions read"*) ok "4. and it prints the DENOMINATOR: how many versions the verdict compared" ;;
  *) bad "4. it prints the count of versions compared" "no 'versions read' line" ;;
esac

# The control. Byte-for-byte the same tree with one file moved aside.
mv "$LTMP/migrations/0039_a_reserved_number_is_not_a_migration.sql" "$LTMP/39.held"
OUT="$("$LCOPY" ledger 2>&1)"
case "$OUT" in
  *"HOLES AT 39"*) ok "5. POSITIVE CONTROL: take the fence away and it says HOLES AT 39. Without
        this, check 3 would read the same on a tree with a hole and on a broken detector" ;;
  *) bad "5. POSITIVE CONTROL: with 0039 removed it must say HOLES AT 39" \
         "said: $(printf '%s' "$OUT" | grep -F 'versions read')" ;;
esac
case "$OUT" in
  *"NEVER TAKE A HOLE"*) ok "6. and tells the reader not to take it even though the ledger says
        it is free, which is the half of the rule reading the ledger cannot give you" ;;
  *) bad "6. and says NEVER TAKE A HOLE" "said: $(printf '%s' "$OUT" | tail -4 | tr '\n' ' ')" ;;
esac
case "$OUT" in
  *"NEXT VERSION IS $LNEXT"*) ok "7. and STILL says $LNEXT, not 39: the hole is reported, never
        offered" ;;
  *) bad "7. and still says NEXT VERSION IS $LNEXT" "said: $(printf '%s' "$OUT" | grep -F 'NEXT VERSION')" ;;
esac
mv "$LTMP/39.held" "$LTMP/migrations/0039_a_reserved_number_is_not_a_migration.sql"

# A caller-narrowed list is missing whole lanes' versions BY CONSTRUCTION, and a report that cries
# wolf on every correct `SCRATCH_SCHEMA_DIRS=migrations` run is one nobody reads on the day there
# is a real hole. test-lane-budget-gate.sh:515 and test-project-budget-gate.sh:419 run exactly this.
OUT="$(SCRATCH_SCHEMA_DIRS=migrations "$REAL" ledger 2>&1)"
case "$OUT" in
  *"SCRATCH_SCHEMA_DIRS narrowed this"*) ok "8. a caller-narrowed list says the holes are the
        other lanes' numbers, instead of raising a false alarm on a correct run" ;;
  *) bad "8. a narrowed list says so" "said: $(printf '%s' "$OUT" | tail -3 | tr '\n' ' ')" ;;
esac
case "$OUT" in
  *"NEVER TAKE A HOLE"*) bad "9. and does NOT print the real-hole warning over expected gaps" \
        "the wolf was cried" ;;
  *) ok "9. and does NOT print the real-hole warning over gaps it created itself" ;;
esac
case "$OUT" in
  *"This is not the whole ledger"*) ok "10. and says the list is not the whole ledger, so nobody
        picks a version off a subset" ;;
  *) bad "10. and says the list is not the whole ledger" "said: $(printf '%s' "$OUT" | tail -2 | tr '\n' ' ')" ;;
esac

# DENOMINATOR, asserted on the door rather than only obeyed inside it. `ledger` prints a verdict
# about the tree's numbering; over zero files "no holes" and "next version is 1" are both things it
# would be saying about a tree it never read.
OUT="$(SCRATCH_SCHEMA_DIRS=nothing_here "$LCOPY" ledger 2>&1)"; RC=$?
check "11. a tree with NO schema files exits 2, never 0: a verdict over an empty set is not a pass" \
      "$RC" "2"
case "$OUT" in
  *"0 versions read"*) ok "12. and says how many it compared, which is what makes the refusal
        readable rather than mysterious" ;;
  *) bad "12. and says 0 versions read" "said: $(printf '%s' "$OUT" | head -2 | tr '\n' ' ')" ;;
esac
case "$OUT" in
  *"NEXT VERSION IS"*) bad "13. and offers NO next version, rather than guessing 1" "it guessed" ;;
  *) ok "13. and offers no next version rather than guessing 1 over a tree it never read" ;;
esac
rm -rf "$LTMP"
echo

# DENOMINATOR. This suite's own verdict, and it used to be able to print `0 passed, 0 failed` and
# exit 0: `[ "$FAIL" -eq 0 ]` is true over an empty world, so a run that died before scene 1 built
# anything -- or a future edit that guards every scene behind a condition none of them meet --
# would report a clean suite over nothing. Task 0408 closed this and removed this file's line from
# policy/denominator-baseline.txt in the same change.
echo "$PASS passed, $FAIL failed"
[ $((PASS + FAIL)) -gt 0 ] || { echo "DENOMINATOR: 0 compared"; exit 2; }   # DENOMINATOR
[ "$FAIL" -eq 0 ]
