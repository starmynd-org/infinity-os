#!/usr/bin/env bash
# Block until Brain Postgres is actually SERVING, then exit 0. The dependants' start gate.
#
# `Requires=brain-store.service` gets the store unit activated first. It does not get the store
# READY first: `brain-store.service` is Type=oneshot and its ExecStart is the wrapper's
# `ensure-running`, which does wait for pg_isready -- but a `docker start` on an already-existing
# container returns the moment the container is up, and the ordering guarantee systemd gives is
# "the prior unit reached active", not "the server behind it accepts queries". On a cold WSL start
# Postgres also runs crash recovery, and recovery is exactly the window in which a dependant that
# trusted the ordering edge alone gets ECONNREFUSED and dies.
#
# So the dependants gate on the registry's declared health check for the store, which is
# `pg_isready`. There is no host-side pg client on this box (measured 2026-08-17: `which pg_isready`
# finds nothing), which is why the check runs inside the container -- exactly as the registry row
# for `5432` already says it does.
#
# This is a WAIT, not a test. It is the ExecStartPre of brain-console.service and
# brain-paging.service, and it is the thing that makes "the console requires Postgres" a fact the
# machine enforces rather than a sentence in a registry cell.
#
# Usage: brain-store-ready.sh [timeout_seconds]   (default 90)
set -euo pipefail

NAME="${BRAIN_PG_CONTAINER:-brain-postgres}"
TIMEOUT="${1:-90}"
deadline=$(( SECONDS + TIMEOUT ))

while :; do
  if docker exec "$NAME" pg_isready -h 127.0.0.1 -p 5432 >/dev/null 2>&1; then
    echo "brain-store-ready: pg_isready passed after ${SECONDS}s"
    exit 0
  fi
  if (( SECONDS >= deadline )); then
    echo "brain-store-ready: ${NAME} did not become ready within ${TIMEOUT}s." >&2
    echo "brain-store-ready: NOT starting the dependant. A console that boots against a down" >&2
    echo "brain-store-ready: store serves HTTP 500 from /api/health, which is worse than absent." >&2
    exit 1
  fi
  sleep 2
done
