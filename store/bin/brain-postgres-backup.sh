#!/usr/bin/env bash
# Backup, which nothing currently owns.
#
# Paperclip backs its own database up hourly with 30 days of retention. Losing this store must
# cost zero KNOWLEDGE -- canon, decisions and receipts all recover from git -- but it does cost
# queue history, threads, and every operator answer, and nothing else in this program replaces
# that.
#
#   backup            one compressed pg_dump into ~/.brain-postgres/backups, then prune
#   restore <file>    restore into a NAMED SCRATCH database, never over the live one
#   verify            take a backup, restore it to scratch, compare row counts, drop scratch
#   list              what is on disk
#
# Retention: 30 days, matching the story it is replacing. Stated here because a backup job with
# an unstated retention is a disk-full incident with a schedule.
#
# `restore` deliberately refuses to write to the live database. A restore that can silently
# overwrite production is the wrong tool to reach for at the moment you most want it.
set -euo pipefail

NAME="brain-postgres"
DB="${BRAIN_PG_DB:-brain}"
BACKUPS="${BRAIN_PG_BACKUPS:-$HOME/.brain-postgres/backups}"
SECRETS="${BRAIN_SECRET_DIR:-$HOME/.brain-postgres-secrets}"
RETENTION_DAYS="${BRAIN_PG_BACKUP_RETENTION_DAYS:-30}"

die() { printf 'brain-postgres-backup: %s\n' "$*" >&2; exit 1; }

secret() {
  local f="$SECRETS/$1"
  [ -r "$f" ] || die "secret reference '$1' did not resolve. Failing closed."
  cat "$f"
}

SU="$(secret brain-postgres-bootstrap-superuser)"
psu() { docker exec -i -e PGPASSWORD="$SU" "$NAME" "$@"; }

cmd_backup() {
  mkdir -p "$BACKUPS"
  local stamp out
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  out="$BACKUPS/${DB}-${stamp}.dump"
  # -Fc so a restore can be selective and so the dump carries its own compression. The dump is
  # written to stdout and landed by the HOST, because the container's data directory is not
  # readable by the operator's user and a backup nobody can read is not a backup.
  psu pg_dump -U postgres -d "$DB" -Fc > "$out.partial"
  mv "$out.partial" "$out"          # atomic rename on ext4, so a torn dump is never named
  chmod 600 "$out"
  printf 'wrote %s (%s bytes)\n' "$out" "$(stat -c%s "$out")"
  local pruned=0
  while IFS= read -r old; do rm -f "$old"; pruned=$((pruned+1)); done < <(
    find "$BACKUPS" -maxdepth 1 -name "${DB}-*.dump" -type f -mtime "+${RETENTION_DAYS}" 2>/dev/null)
  printf 'retention %sd: %s pruned, %s kept\n' "$RETENTION_DAYS" "$pruned" \
    "$(find "$BACKUPS" -maxdepth 1 -name "${DB}-*.dump" -type f | wc -l)"
}

cmd_list() { ls -lh "$BACKUPS"/*.dump 2>/dev/null || echo "no backups"; }

cmd_restore() {
  local file="${1:-}" scratch="${2:-${DB}_restore_scratch}"
  [ -r "$file" ] || die "usage: $0 restore <dump-file> [scratch-db-name]"
  [ "$scratch" != "$DB" ] || die "refusing to restore over the live database $DB. Name a scratch one."
  psu psql -U postgres -d postgres -c "DROP DATABASE IF EXISTS \"$scratch\"" >/dev/null
  psu psql -U postgres -d postgres -c "CREATE DATABASE \"$scratch\"" >/dev/null
  psu pg_restore -U postgres -d "$scratch" --no-owner --no-privileges < "$file" >/dev/null
  printf 'restored %s into scratch database %s\n' "$file" "$scratch"
}

# A restore nobody proved is an assertion. This takes a real backup, restores it to scratch,
# compares every table's row count against the live database, and reports any difference.
cmd_verify() {
  local scratch="${DB}_verify_scratch"
  cmd_backup
  local newest; newest="$(ls -t "$BACKUPS"/${DB}-*.dump | head -1)"
  cmd_restore "$newest" "$scratch"
  echo "--- row counts, live vs restored ---"
  # The table list is read into an array FIRST. Feeding it to a `while read` loop whose body
  # calls `docker exec -i` lets the inner command eat the rest of the list off stdin: the loop
  # then checks one table, reports every table matched, and the whole verification passes having
  # verified almost nothing. That is a false pass, and a false pass here is worse than no backup.
  local tables=() tbl a b diff=0 checked=0
  mapfile -t tables < <(psu psql -U postgres -d "$DB" -tAc \
      "SELECT table_name FROM information_schema.tables WHERE table_schema='brain' AND table_type='BASE TABLE' ORDER BY 1")
  local expected=${#tables[@]}
  [ "$expected" -gt 0 ] || die "found no tables to compare. Refusing to report a pass."
  for tbl in "${tables[@]}"; do
    [ -n "$tbl" ] || continue
    a=$(psu psql -U postgres -d "$DB"      -tAc "SELECT count(*) FROM brain.\"$tbl\"" </dev/null)
    b=$(psu psql -U postgres -d "$scratch" -tAc "SELECT count(*) FROM brain.\"$tbl\"" </dev/null)
    checked=$((checked+1))
    if [ "$a" = "$b" ]; then printf '  ok    %-20s %s rows\n' "$tbl" "$a"
    else printf '  DIFF  %-20s live=%s restored=%s\n' "$tbl" "$a" "$b"; diff=$((diff+1)); fi
  done
  psu psql -U postgres -d postgres -c "DROP DATABASE IF EXISTS \"$scratch\"" >/dev/null
  # Guard the count as well as the comparison, so a loop that silently stopped early can never
  # be read as a clean run.
  [ "$checked" -eq "$expected" ] || die "compared $checked of $expected tables. Not a pass."
  if [ "$diff" -eq 0 ]; then
    echo "RESTORE VERIFIED: $checked of $expected tables compared, every one matched"
  else
    echo "RESTORE FAILED: $diff of $checked table(s) differed"; exit 1; fi
}

case "${1:-}" in
  backup)  cmd_backup ;;
  list)    cmd_list ;;
  restore) shift; cmd_restore "$@" ;;
  verify)  cmd_verify ;;
  *) die "usage: $0 {backup|list|restore <file> [scratch-db]|verify}" ;;
esac
