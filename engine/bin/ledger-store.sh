#!/usr/bin/env bash
# A scratch store STOPPED AT A GIVEN LEDGER VERSION, for testing schema tolerance against a store
# that is genuinely behind rather than one that is pretended to be.
#
# WHY THIS EXISTS. `docs/SCHEMA-TOLERANCE.md` rule 5 says a path that already served may not start
# failing, and this lane's own carve-out says a lane never applies migrations to the live store --
# it hands over the apply order. SO THE LIVE STORE IS BEHIND BY CONSTRUCTION and the merged runtime
# meets it there. Every tolerance claim in this repo was therefore a claim about a store nobody had
# built. Three defects were found within an hour of building one:
#
#   * `reserve` wrote `lease_epoch` into brain.effect_attempt unconditionally. Migration 65 renamed
#     the column on TWO tables and the resolver only knew about the lease. Ledger 64: UndefinedColumn.
#   * `reserve` consumed the approval unconditionally. Those three columns arrive with 66, so the
#     tolerance added to `check` one file over made the check pass and handed a working check to a
#     write that could not run.
#   * `settle` wrote `completeness` and `effect_state` unconditionally. They arrive with 67, which
#     this lane wrote itself, so every store below 67 could not settle at all.
#
# None was visible by reading, and two were in code written to fix the first. `check-at-head.sh`
# exists because a check that never ran against the artefact is not a check; this exists because a
# tolerance claim that never ran against an old store is not a measurement.
#
# It delegates to scratch-db.sh for every database operation, so the guards there -- refusing the
# live store by name, refusing an unnamed shared scratch, the build-in-progress marker -- apply
# unchanged rather than being reimplemented next to them.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCRATCH="$REPO/engine/bin/scratch-db.sh"

die() { printf 'ledger-store: %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'USAGE'
usage: ledger-store.sh <version> <dbname>

  Build $dbname from every schema file whose RECORDED ledger version is <= <version>.

      ledger-store.sh 64 brain_t04_l64      a store four migrations behind
      ledger-store.sh 66 brain_t04_l66      single use exists, the two settle axes do not
      ledger-store.sh 999 brain_t04_lhead   everything, which is what scratch-db.sh create does

  DESTRUCTIVE: it DROPs $dbname first, exactly as scratch-db.sh create does. The name is a required
  argument and there is no default, because the one bug this family has actually had was a verb
  that defaulted its target and dropped the store the whole fleet was building on (task 0248).
USAGE
}

case "${1:-}" in
  ""|-h|--help|help) usage; exit 0 ;;
esac

VERSION="${1:-}"
DB="${2:-}"
case "$VERSION" in
  ''|*[!0-9]*) usage; die "the first argument must be a ledger version, got '${VERSION}'." ;;
esac
[ -n "$DB" ] || { usage; die "name the database. There is no default."; }
case "$DB" in
  brain) die "refusing to operate on the live store database 'brain'." ;;
  brain_scratch) die "refusing 'brain_scratch': it is the store every lane's suite reads by default.
    Name a database of your own." ;;
esac

# THE LEDGER, READ FROM THE HARNESS RATHER THAN FROM FILENAMES. A file's recorded version is the
# INSERT inside it, not its numeric prefix -- two files in different lane directories carry
# prefixes that do not match their versions, and ordering by prefix applies them in the wrong
# order. `scratch-db.sh ledger` already resolves that and refuses duplicates; asking it is the only
# way to get the same order the live store actually saw.
TMP="$REPO/.ledger-store-tmp"
rm -rf "$TMP"
mkdir -p "$TMP/migrations" "$TMP/budget" "$TMP/queue"
trap 'rm -rf "$TMP"' EXIT

LEDGER="$(ENGINE_SCRATCH_DB="$DB" "$SCRATCH" ledger 2>/dev/null || true)"
[ -n "$LEDGER" ] || die "scratch-db.sh ledger printed nothing. Nothing was selected, so nothing is
  built: a store assembled from an empty file list would report a plausible maximum version and be
  wrong about every table."

selected=0
while read -r v f; do
  case "$v" in ''|*[!0-9]*) continue ;; esac
  # AND THE SECOND FIELD MUST BE A SCHEMA FILE. `ledger` ends with a summary line that OPENS WITH A
  # NUMBER -- "67  versions read, 1..67, HOLES AT ..." -- so a numeric-first-field test alone reads
  # the word "versions" as a path and dies. Measured: the first run of this script did exactly that.
  case "$f" in */*.sql) ;; *) continue ;; esac
  [ "$v" -le "$VERSION" ] || continue
  case "$f" in
    migrations/*)  cp "$REPO/$f" "$TMP/migrations/" ;;
    budget/*)      cp "$REPO/$f" "$TMP/budget/" ;;
    queue/*)       cp "$REPO/$f" "$TMP/queue/" ;;
    *)             die "unrecognised schema directory in '$f'." ;;
  esac
  selected=$((selected + 1))
done <<<"$LEDGER"

# THE DENOMINATOR, PRINTED. A verdict over an empty set is not a pass, and neither is a build: a
# store built from zero files still answers `SELECT 1` and would read as a working ledger-N store
# to anything that only asked whether it was reachable.
total="$(printf '%s\n' "$LEDGER" | grep -cE '^[[:space:]]*[0-9]+[[:space:]]+[^[:space:]]+/[^[:space:]]+\.sql$' || true)"
printf 'ledger-store: %s of %s schema files record version <= %s\n' "$selected" "$total" "$VERSION"
[ "$selected" -gt 0 ] || die "0 files selected at version $VERSION. Refusing to build a store out of
  nothing and call it a ledger-$VERSION database."

SCRATCH_SCHEMA_DIRS=".ledger-store-tmp/migrations .ledger-store-tmp/budget .ledger-store-tmp/queue" \
  ENGINE_SCRATCH_DB="$DB" "$SCRATCH" create

# WHAT IT ACTUALLY REACHED, not what was asked for. A requested version with no file recording it
# builds a store one or more versions lower, and reporting the request would make every later
# measurement wrong by a name.
reached="$(ENGINE_SCRATCH_DB="$DB" "$SCRATCH" psql -tAc "SELECT max(version) FROM brain.schema_migration")"
printf 'ledger-store: %s is at ledger %s (requested <= %s)\n' "$DB" "$reached" "$VERSION"
