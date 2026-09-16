#!/usr/bin/env bash
# Apply ONE migration file to a live store and prove the ledger moved.
#
# Written 2026-08-31, because the procedure that was written down could not work on this host.
#
# ==============================================================================================
# WHY THIS EXISTS: THE DOCUMENTED COMMAND IS WRONG HERE
# ==============================================================================================
#
# `voice/docs/APPLY.md` hands the operator this line, and four migrations are recorded as having
# been applied that way:
#
#     psql -d brain -v ON_ERROR_STOP=1 -f migrations/0024_objective_voice_intake.sql
#
# There is NO `psql` on this host's PATH. Postgres runs in the docker container `brain-postgres`,
# and the migration files live on the WSL filesystem where that container cannot see them, so
# `-f` would fail on the path even if psql existed. Measured 2026-08-31: `which psql` finds
# nothing, `docker ps` shows `brain-postgres` up.
#
# A procedure that cannot run is worse than no procedure, because somebody reads it, believes the
# step is understood, and discovers the gap at the moment they are applying schema to production.
#
# ==============================================================================================
# WHAT IT REFUSES, AND WHY EACH REFUSAL IS THERE
# ==============================================================================================
#
#   * IT APPLIES ONE FILE. A loop over five files that dies on the third leaves a store nobody can
#     describe. One file, one verdict, and the operator decides whether to run the next.
#   * IT READS THE LEDGER BEFORE AND AFTER and refuses to report success unless the version it
#     expected actually arrived. `psql` exiting 0 having applied nothing is the exact failure the
#     apply docs in this repo already warn about: every migration here writes its ledger row
#     `ON CONFLICT (version) DO NOTHING`, so a file whose DDL is already present exits 0 and
#     changes nothing, which reads identically to a successful first application.
#   * IT REFUSES A FILE THAT DECLARES NO LEDGER VERSION, for `scratch-db.sh`'s reason: a migration
#     with no `brain.schema_migration` row is one nothing can tell has been applied.
#   * IT NAMES THE DATABASE ON EVERY LINE. There is more than one store on this host and the
#     difference between `brain` and a scratch is the difference between a rehearsal and an act.
#
# THERE IS NO `--all` AND THERE MUST NOT BE. The point of applying schema by hand is that a human
# reads each verdict before causing the next one.
#
# Usage:
#   store/bin/apply-migration.sh migrations/0048_impact_is_continuous.sql
#   BRAIN_DB=brain_v2 store/bin/apply-migration.sh migrations/0048_impact_is_continuous.sql
#
# Exit: 0 applied and the ledger moved. 1 refused or failed. 2 already applied, nothing to do.

set -uo pipefail

FILE="${1:-}"
DB="${BRAIN_DB:-brain}"
CONTAINER="${BRAIN_PG_CONTAINER:-brain-postgres}"
SECRETS="${BRAIN_SECRET_DIR:-$HOME/.brain-postgres-secrets}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

die() { printf 'apply-migration: %s\n' "$*" >&2; exit 1; }

[ -n "$FILE" ] || die "usage: store/bin/apply-migration.sh <path/to/NNNN_name.sql>
There is no --all. Apply one file, read the verdict, then decide about the next."
[ -f "$ROOT/$FILE" ] || [ -f "$FILE" ] || die "no such file: $FILE"
[ -f "$FILE" ] || FILE="$ROOT/$FILE"

# THE VERSION THE FILE DECLARES, out of its own INSERT. The filename prefix is a per-lane counter
# and lies; `engine/bin/scratch-db.sh` says so at length and migration 24 was renumbered for it.
VERSION="$(grep -oE 'VALUES *\(([0-9]+),' "$FILE" | head -1 | grep -oE '[0-9]+')"
[ -n "$VERSION" ] || die "$FILE records no brain.schema_migration row, so nothing could ever tell
whether it had been applied. Refusing."

# THE ROLE THAT CAN ACTUALLY APPLY DDL HERE IS `postgres`, NOT `brain_owner`, AND EVERY APPLY
# DOC IN THIS REPO SAYS OTHERWISE.
#
# Measured on live `brain` 2026-08-31: all 40 tables in the `brain` schema and the schema itself
# are owned by `postgres`. `brain_owner` is a LOGIN role with grants, not the owner of anything,
# so `ALTER TABLE brain.work_item ...` as `brain_owner` dies on
#
#     ERROR:  must be owner of table work_item
#
# Watched happening: applying migration 48 as `brain_owner` to a store at ledger 47 failed exactly
# there, mid-file. `engine/bin/scratch-db.sh` has always used `-U postgres` for the same reason;
# only the prose disagreed. The header line "APPLY AS: brain owner" that every migration in this
# repo carries, including the five written today, is wrong and has been corrected.
#
# THE CREDENTIAL IS THE BOOTSTRAP SUPERUSER and that is worth being uncomfortable about, which is
# why this script applies one file, reads the ledger on both sides, and has no --all.
super_pw() {
  local f="$SECRETS/brain-postgres-bootstrap-superuser"
  [ -r "$f" ] || die "the bootstrap superuser credential did not resolve from $SECRETS. Failing closed."
  cat "$f"
}

psql_owner() {
  docker exec -i -e PGPASSWORD="$(super_pw)" "$CONTAINER" \
    psql -v ON_ERROR_STOP=1 -h 127.0.0.1 -U postgres -d "$DB" "$@"
}

docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$CONTAINER" \
  || die "the container '$CONTAINER' is not running. \`docker ps\` is where to look."

BEFORE="$(psql_owner -tAc 'SELECT COALESCE(max(version), 0) FROM brain.schema_migration' 2>/dev/null | tr -d '[:space:]')"
[ -n "$BEFORE" ] || die "could not read the ledger on '$DB'. Nothing was applied."

printf '\n  file    %s\n  declares ledger version %s\n  database %s, currently at ledger %s\n\n' \
  "$(basename "$FILE")" "$VERSION" "$DB" "$BEFORE"

# ALREADY THERE IS ITS OWN ANSWER AND IT IS NOT AN ERROR. Exit 2, the way `swarm intake` reports
# nothing-to-do, so a caller can tell "I did it" from "it was already done".
HELD="$(psql_owner -tAc "SELECT name FROM brain.schema_migration WHERE version = $VERSION" 2>/dev/null | tr -d '[:space:]')"
if [ -n "$HELD" ]; then
  printf '  ALREADY APPLIED: ledger %s is held by %s. Nothing to do.\n\n' "$VERSION" "$HELD"
  exit 2
fi

printf '  applying...\n\n'
if ! psql_owner < "$FILE"; then
  AFTER="$(psql_owner -tAc 'SELECT COALESCE(max(version), 0) FROM brain.schema_migration' 2>/dev/null | tr -d '[:space:]')"
  printf '\n  FAILED. The ledger is at %s (it was %s).\n' "${AFTER:-unreadable}" "$BEFORE"
  printf '  Every migration in this repo is one transaction, so a failure rolls its own DDL back.\n'
  exit 1
fi

# THE LEDGER IS THE VERDICT, NOT THE EXIT CODE. Every file here writes its row ON CONFLICT DO
# NOTHING, so a re-run exits 0 having changed nothing.
LANDED="$(psql_owner -tAc "SELECT name FROM brain.schema_migration WHERE version = $VERSION" 2>/dev/null | tr -d '[:space:]')"
AFTER="$(psql_owner -tAc 'SELECT COALESCE(max(version), 0) FROM brain.schema_migration' 2>/dev/null | tr -d '[:space:]')"
if [ -z "$LANDED" ]; then
  printf '\n  REFUSING TO REPORT SUCCESS: psql exited 0 but ledger %s is still absent on %s.\n' \
    "$VERSION" "$DB"
  printf '  That is the shape an apply doc in this repo already warns about. Investigate before\n'
  printf '  applying anything else.\n'
  exit 1
fi
printf '\n  APPLIED. ledger %s is now held by %s. %s moved %s -> %s.\n\n' \
  "$VERSION" "$LANDED" "$DB" "$BEFORE" "$AFTER"
exit 0
