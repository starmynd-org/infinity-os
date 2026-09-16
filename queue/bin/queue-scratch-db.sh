#!/usr/bin/env bash
# A scratch database for the queue lane. It is now a THIN NAME over `engine/bin/scratch-db.sh`,
# which applies every lane's schema in ledger order, `queue/schema/` included.
#
# It used to append its own `queue/schema/` loop after calling that script, because that script
# globbed `migrations/` alone. Two loops over one ledger is one loop too many and it drifted
# exactly as you would expect:
#
#   - the queue loop applied its files in FILENAME order after all of `migrations/`, so
#     `0009_null_branch_act_scan.sql` (which records version 11) landed after 15 rather than
#     between 10 and 12, and `0003_budget.sql` was never applied at all;
#   - `migrate` re-ran every queue file every time instead of only the unrecorded ones, and had
#     no ledger check to notice;
#   - and there was no door that built the database if it was absent, so all four suites in
#     `queue/tests/` died in reset() on a TRUNCATE against a database that did not exist.
#
# THE HISTORY THE OLD LOOP CARRIED, kept because it is the reason to distrust a second loop: this
# named `0007_queue.sql` literally in two places, which was correct on the day it was written and
# silently wrong the moment 0008 landed. Measured at 13:38Z on 2026-08-16, a scratch built by this
# script had `brain.queue_defer` WITHOUT `produced_by_ref` and `resolution_status`, which is
# exactly the drift task 0115 had just closed on the live store. That is the same defect migration
# 6 fixed in `engine/bin/scratch-db.sh`, reappearing one directory down because the fix was applied
# to the script and not to the habit. Deleting the loop is what breaks the habit.
#
# `brain.schema_migration` printed below is what a reader should check.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DB="${QUEUE_SCRATCH_DB:-brain_queue_scratch}"
CONTAINER="${BRAIN_PG_CONTAINER:-brain-postgres}"
SECRETS="${BRAIN_SECRET_DIR:-$HOME/.brain-postgres-secrets}"

case "$DB" in
  brain) echo "queue-scratch-db: refusing to operate on the live store 'brain'." >&2; exit 1 ;;
esac

su_psql() {
  docker exec -i -e PGPASSWORD="$(cat "$SECRETS/brain-postgres-bootstrap-superuser")" \
    "$CONTAINER" psql -v ON_ERROR_STOP=1 -h 127.0.0.1 -U postgres "$@"
}

builder() { ENGINE_SCRATCH_DB="$DB" "$REPO/engine/bin/scratch-db.sh" "$@"; }

report() {
  printf 'queue scratch %s ready: schema_migration %s, %s queue objects, lineage drift %s\n' "$DB" \
    "$(su_psql -tAd "$DB" -c 'SELECT max(version) FROM brain.schema_migration')" \
    "$(su_psql -tAd "$DB" -c "SELECT count(*) FROM information_schema.tables
                               WHERE table_schema='brain' AND table_name LIKE 'queue%'")" \
    "$(su_psql -tAd "$DB" -c 'SELECT count(*) FROM brain.lineage_column_drift')"
}

case "${1:-ensure}" in
  create)  builder create;  report ;;
  migrate) builder migrate; report ;;
  # The door a suite should call: build it if it is not there, reconcile it if it is. `create`
  # DROPs, so a suite that called it unconditionally would kill a sibling lane's run.
  ensure)  builder ensure;  report ;;
  ledger)  builder ledger ;;
  psql)    shift; su_psql -d "$DB" "$@" ;;
  drop)    su_psql -d postgres -c "DROP DATABASE IF EXISTS $DB WITH (FORCE)" >/dev/null
           echo "dropped $DB" ;;
  *) echo "usage: queue-scratch-db.sh {create|migrate|ensure|ledger|psql|drop}" >&2; exit 1 ;;
esac
