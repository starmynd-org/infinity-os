#!/usr/bin/env bash
# The one control script for the store, per _system/port-registry-rules.md hard rule 5.
# Supports ensure-running, stop, status, health. Nothing else starts this service.
#
# The registry row at tools/port-registry.md names this path. If you move this file, the row is
# wrong and the row is the thing that wins on any conflict, so edit the row in the same commit.
#
# No password appears here. Values resolve from the secret backend at the point of use; a value
# that does not resolve fails closed rather than falling back to a default.
set -euo pipefail

NAME="brain-postgres"
IMAGE="postgres:16.10-alpine"
PORT="${BRAIN_PG_PORT:-5432}"
DATA="${BRAIN_PG_DATA:-$HOME/.brain-postgres/data}"
BACKUPS="${BRAIN_PG_BACKUPS:-$HOME/.brain-postgres/backups}"
SECRETS="${BRAIN_SECRET_DIR:-$HOME/.brain-postgres-secrets}"
DB="${BRAIN_PG_DB:-brain}"

die() { printf 'brain-postgres: %s\n' "$*" >&2; exit 1; }

secret() {
  local ref="$1" f="$SECRETS/$1"
  [ -r "$f" ] || die "secret reference '$ref' did not resolve from $SECRETS. Failing closed."
  local v; v="$(cat "$f")"
  [ -n "$v" ] || die "secret reference '$ref' resolved to an empty value. Failing closed."
  printf '%s' "$v"
}

# Never under /mnt/c. drvfs does not guarantee atomic rename, which is the same constraint that
# governs $SWARM_HOME, and a database is the last thing that should learn about it the hard way.
case "$DATA" in
  /mnt/c/*|/mnt/[a-z]/*) die "data directory $DATA is on drvfs. Postgres data must be on native ext4." ;;
esac

running() { [ "$(docker inspect -f '{{.State.Running}}' "$NAME" 2>/dev/null || echo false)" = "true" ]; }
exists()  { docker inspect "$NAME" >/dev/null 2>&1; }

cmd_ensure_running() {
  if running; then echo "already running"; cmd_health; return 0; fi
  if exists; then docker start "$NAME" >/dev/null; else
    mkdir -p "$DATA" "$BACKUPS"
    docker run -d --name "$NAME" \
      --restart unless-stopped \
      -p "127.0.0.1:$PORT:5432" \
      -v "$DATA":/var/lib/postgresql/data \
      -e POSTGRES_USER=postgres \
      -e POSTGRES_DB="$DB" \
      -e POSTGRES_PASSWORD="$(secret brain-postgres-bootstrap-superuser)" \
      -e POSTGRES_HOST_AUTH_METHOD=scram-sha-256 \
      -e POSTGRES_INITDB_ARGS="--data-checksums --auth=scram-sha-256" \
      "$IMAGE" >/dev/null
  fi
  for _ in $(seq 1 30); do
    if docker exec "$NAME" pg_isready -h 127.0.0.1 -p 5432 >/dev/null 2>&1; then
      cmd_health; return 0
    fi
    sleep 1
  done
  die "started but never became ready. docker logs $NAME"
}

cmd_stop()   { exists && docker stop "$NAME" >/dev/null && echo "stopped" || echo "not running"; }
cmd_status() {
  running && docker ps --filter "name=$NAME" --format '{{.Names}} {{.Status}} {{.Ports}}' \
          || { echo "down"; return 1; }
}

# `pg_isready` proves the server is up and nothing about the listeners, which bind no port and
# fall outside the port registry rules entirely. Listener health is lag, and it is reported
# separately below so a green server is never mistaken for a working fabric.
cmd_health() {
  running || { echo "down"; return 1; }
  docker exec "$NAME" pg_isready -h 127.0.0.1 -p 5432 || return 1
  PGPASSWORD="$(secret brain-postgres-role-runtime)" docker exec -i \
    -e PGPASSWORD="$(secret brain-postgres-role-runtime)" "$NAME" \
    psql -h 127.0.0.1 -U brain_runtime -d "$DB" -tAc \
    "SELECT 'schema=' || COALESCE(max(version)::text,'none') FROM brain.schema_migration" 2>/dev/null \
    || echo "schema=unreadable (migrations not applied, or the runtime secret is wrong)"
  local lag
  lag=$(PGPASSWORD="$(secret brain-postgres-role-runtime)" docker exec -i \
        -e PGPASSWORD="$(secret brain-postgres-role-runtime)" "$NAME" \
        psql -h 127.0.0.1 -U brain_runtime -d "$DB" -tAc \
        "SELECT COALESCE(string_agg(subscriber||' lag='||lag,', '),'no subscribers registered') FROM brain.subscriber_lag" 2>/dev/null || echo "unreadable")
  echo "listeners: $lag"
}

case "${1:-}" in
  ensure-running) cmd_ensure_running ;;
  stop)           cmd_stop ;;
  status)         cmd_status ;;
  health)         cmd_health ;;
  *) die "usage: $0 {ensure-running|stop|status|health}" ;;
esac
