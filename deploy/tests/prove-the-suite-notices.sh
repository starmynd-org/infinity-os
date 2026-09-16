#!/usr/bin/env bash
# Would the restore suite notice a restore that kept every ROW and lost every RULE?
#
# That is the failure the suite claims to catch, and a suite that claims it without demonstrating it
# is the thing this repository keeps finding. So: take a real restored store, drop the triggers and
# constraints while leaving every row in place, and ask it the same questions. The counts still
# match. The behaviour does not.
#
#   ENGINE_SCRATCH_DB=brain_scratch_t04 ./deploy/tests/prove-the-suite-notices.sh
set -uo pipefail
SRC="${ENGINE_SCRATCH_DB:-}"
case "$SRC" in ""|brain) echo "name a scratch database in ENGINE_SCRATCH_DB" >&2; exit 2 ;; esac
case "$SRC" in *scratch*) ;; *) echo "refusing $SRC" >&2; exit 2 ;; esac
TGT="${SRC}_broken"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PGP="$(cat "$HOME/.brain-postgres-secrets/brain-postgres-bootstrap-superuser")"
SU() { docker exec -i -e PGPASSWORD="$PGP" brain-postgres psql -Atq -h 127.0.0.1 -U postgres -d "$1" -c "$2"; }

DUMP="$(mktemp -u)/d.dump"; mkdir -p "$(dirname "$DUMP")"
bash "$ROOT/deploy/backup.sh" "$SRC" "$DUMP" || exit 1
bash "$ROOT/deploy/restore.sh" "$DUMP" "$TGT" || exit 1

echo
echo "=== breaking it the way a bad restore breaks: rows kept, rules dropped ==="
SU "$TGT" "DROP TRIGGER IF EXISTS approval_decider_is_entitled ON brain.approval;
           ALTER TABLE brain.approval DROP CONSTRAINT IF EXISTS approval_is_not_self_ck;
           ALTER TABLE brain.approval DROP CONSTRAINT IF EXISTS approval_is_decided_once_uq;
           DROP TRIGGER IF EXISTS effect_attempt_settles_once ON brain.effect_attempt;
           ALTER SEQUENCE brain.lease_epoch_seq RESTART WITH 1;" >/dev/null

WHO="$(SU "$TGT" "SELECT human FROM brain.human_role ORDER BY human LIMIT 1")"
G="$(SU "$TGT" "SELECT grant_seq FROM brain.authority_grant WHERE capability='approval.decide' ORDER BY grant_seq DESC LIMIT 1")"

echo
echo "--- ROW COUNTS, the cheap check, on the broken store ---"
for t in authority_grant approval effect_attempt execution_lease; do
  printf '  %-22s source=%-5s restored=%s\n' "$t" "$(SU "$SRC" "SELECT count(*) FROM brain.$t")" \
         "$(SU "$TGT" "SELECT count(*) FROM brain.$t")"
done
echo "  counts match, and the store below is wide open."

echo
echo "--- BEHAVIOUR, the real check ---"
OUT="$(SU "$TGT" "INSERT INTO brain.approval (proposal_id, proposal_version, decided_by, subject,
      grant_seq, decision) VALUES ('broken-restore', 'v1', '$WHO', '$WHO', $G, 'approve')" 2>&1)"
if [ -z "$OUT" ] || ! printf '%s' "$OUT" | grep -qi 'own proposal'; then
  echo "  SELF-APPROVAL ACCEPTED on the restored store. This is the defect the suite must catch."
else
  echo "  self-approval still refused: the break did not take, and this demonstration proves nothing"
fi
SEQ="$(SU "$TGT" "SELECT last_value FROM brain.lease_epoch_seq")"
echo "  lease_epoch_seq restarted at $SEQ, so the next lease reuses an epoch already issued."
echo
echo "That is what 'row counts match' is worth on its own."
SU "$TGT" "SELECT 1" >/dev/null && docker exec -i -e PGPASSWORD="$PGP" brain-postgres \
  psql -q -h 127.0.0.1 -U postgres -d postgres -c "DROP DATABASE IF EXISTS \"$TGT\" WITH (FORCE)" >/dev/null
rm -rf "$(dirname "$DUMP")"
echo "broken store dropped."
