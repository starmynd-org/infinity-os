#!/usr/bin/env bash
# Back up one disposable store, so a restore can be PROVEN rather than assumed.
#
# Packet R06. This is a FIXTURE, not the customer's backup job. It exists so that
# `deploy/tests/test_restore_proves_behaviour.py` can take a real dump of a real store and restore
# it into another one, and so the operational runbook has something that has actually run.
#
# THREE REFUSALS, and each one is a mistake this repository has already paid for somewhere:
#   * it refuses the live store by name, the way `engine/bin/scratch-db.sh` does;
#   * it refuses any database whose name does not say scratch, because "I pointed it at the wrong
#     database" is the failure that has no undo;
#   * it refuses to overwrite an existing dump file, because a backup script that silently
#     replaces yesterday's dump is one bad night away from having no backups at all.
set -euo pipefail

DB="${1:-${ENGINE_SCRATCH_DB:-}}"
OUT="${2:-}"
[ -n "$DB" ]  || { echo "usage: backup.sh <scratch-db> <out.dump>" >&2; exit 2; }
[ -n "$OUT" ] || { echo "usage: backup.sh <scratch-db> <out.dump>" >&2; exit 2; }
case "$DB" in
  brain) echo "backup.sh: refusing the live store 'brain'. This is a fixture." >&2; exit 2 ;;
  *scratch*) ;;
  *) echo "backup.sh: refusing $DB: the name does not say scratch." >&2; exit 2 ;;
esac
[ -e "$OUT" ] && { echo "backup.sh: $OUT exists. Refusing to overwrite a dump." >&2; exit 2; }

CONTAINER="${BRAIN_PG_CONTAINER:-brain-postgres}"
SECRET="${BRAIN_SECRET_DIR:-$HOME/.brain-postgres-secrets}/brain-postgres-bootstrap-superuser"
[ -r "$SECRET" ] || { echo "backup.sh: no superuser secret at $SECRET. Failing closed." >&2; exit 2; }

# -Fc so the restore can be selective later; the fixture restores whole, but a customer runbook
# that cannot restore one table is a runbook with one option.
docker exec -i -e PGPASSWORD="$(cat "$SECRET")" "$CONTAINER" \
  pg_dump -h 127.0.0.1 -U postgres -Fc --no-owner --no-acl "$DB" > "$OUT"

BYTES=$(wc -c < "$OUT")
# A DUMP OF NOTHING IS NOT A BACKUP. pg_dump exits 0 on an empty database, and a zero-byte file
# that reports success is the shape of a backup nobody discovers is empty until they need it.
[ "$BYTES" -gt 1024 ] || { echo "backup.sh: $OUT is $BYTES bytes. That is not a backup." >&2; exit 1; }
printf 'backup.sh: %s -> %s (%s bytes)\n' "$DB" "$OUT" "$BYTES"
