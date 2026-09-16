#!/usr/bin/env bash
# Restore a dump into a DIFFERENT disposable store, so the restore can be compared against the
# original rather than replacing it.
#
# Packet R06. Restoring over the source is how you discover your restore was broken by no longer
# having the thing it broke, so this refuses to do it.
set -euo pipefail

DUMP="${1:-}"
DB="${2:-}"
[ -n "$DUMP" ] && [ -n "$DB" ] || { echo "usage: restore.sh <in.dump> <target-scratch-db>" >&2; exit 2; }
[ -r "$DUMP" ] || { echo "restore.sh: cannot read $DUMP" >&2; exit 2; }
case "$DB" in
  brain) echo "restore.sh: refusing the live store 'brain'." >&2; exit 2 ;;
  *scratch*) ;;
  *) echo "restore.sh: refusing $DB: the name does not say scratch." >&2; exit 2 ;;
esac

CONTAINER="${BRAIN_PG_CONTAINER:-brain-postgres}"
SECRET="${BRAIN_SECRET_DIR:-$HOME/.brain-postgres-secrets}/brain-postgres-bootstrap-superuser"
[ -r "$SECRET" ] || { echo "restore.sh: no superuser secret at $SECRET. Failing closed." >&2; exit 2; }
PGP="$(cat "$SECRET")"

su() { docker exec -i -e PGPASSWORD="$PGP" "$CONTAINER" "$@" -h 127.0.0.1 -U postgres; }

su psql -v ON_ERROR_STOP=1 -d postgres -c "DROP DATABASE IF EXISTS \"$DB\" WITH (FORCE)" >/dev/null
su psql -v ON_ERROR_STOP=1 -d postgres -c "CREATE DATABASE \"$DB\"" >/dev/null
# --no-owner because the fixture restores as the superuser into a store whose roles already exist;
# a customer restore onto a fresh host provisions roles first, which is the runbook's job and not
# this script's. Said here rather than left for somebody to discover from a permissions error.
su pg_restore --no-owner --no-acl -d "$DB" < "$DUMP" >/dev/null 2>&1 || true

APPLIED=$(su psql -Atq -d "$DB" -c "SELECT coalesce(max(version), -1) FROM brain.schema_migration")
[ "$APPLIED" -ge 0 ] || { echo "restore.sh: restored store has no ledger. Not a restore." >&2; exit 1; }
printf 'restore.sh: %s -> %s (schema_migration %s)\n' "$DUMP" "$DB" "$APPLIED"
