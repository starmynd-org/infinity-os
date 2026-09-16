#!/usr/bin/env bash
# The operator's own login. The half of migration 20 that cannot be committed.
#
# Migration 20 makes `brain.work_item.actor_type = 'human'` a property of WHICH LOGIN wrote the
# row, keyed through `brain.human_role`. That mapping is useless until the operator has a role of
# his own, and a role needs a password, and a password may never appear in a committed file. So
# the migration carries the grant (in `brain.provision_human_role`) and this script carries the
# two things it must not: `CREATE ROLE ... PASSWORD`, and writing the value into the secret
# backend `store/session.py` resolves from.
#
# Run once per database, as part of applying migration 20:
#
#     store/bin/provision-operator.sh --db brain
#
# Idempotent. Re-running re-applies the grant and the mapping and LEAVES THE PASSWORD ALONE,
# because rotating it under a running console is a silent outage. Pass --rotate to mint a new one.
#
# THE ROLE IS NOT A SECOND APP ROLE. It is granted `brain_runtime` by membership, so it can do
# exactly what the app can do, plus the one thing the app must never do: establish that a work
# item is the operator's own. Anything the runtime cannot do, this cannot do either.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONTAINER="${BRAIN_PG_CONTAINER:-brain-postgres}"
SECRETS="${BRAIN_SECRET_DIR:-$HOME/.brain-postgres-secrets}"
DB=""
HUMAN="operator"
ROLE="brain_operator"
REF="brain-postgres-role-operator"
ROTATE=no

die() { printf 'provision-operator: %s\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --db)     DB="${2:-}"; shift 2 ;;
    --human)  HUMAN="${2:-}"; shift 2 ;;
    --rotate) ROTATE=yes; shift ;;
    *) die "usage: provision-operator.sh --db <database> [--human <slug>] [--rotate]" ;;
  esac
done
[ -n "$DB" ] || die "--db is required. There is no default: provisioning the wrong database is a
grant nobody meant to make."

secret() {
  local f="$SECRETS/$1"
  [ -r "$f" ] || die "secret reference '$1' did not resolve from $SECRETS. Failing closed."
  cat "$f"
}

su_psql() {
  docker exec -i -e PGPASSWORD="$(secret brain-postgres-bootstrap-superuser)" \
    "$CONTAINER" psql -v ON_ERROR_STOP=1 -h 127.0.0.1 -U postgres "$@"
}

su_psql -tAd "$DB" -c "SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                        WHERE n.nspname='brain' AND p.proname='provision_human_role'" \
  | grep -q 1 || die "database '$DB' has not had migrations/0020_human_actor_identity.sql applied.
Apply the migration first: this script provisions against the grant that migration defines."

mkdir -p "$SECRETS"; chmod 700 "$SECRETS"

if [ -s "$SECRETS/$REF" ] && [ "$ROTATE" = no ]; then
  PW="$(cat "$SECRETS/$REF")"
  ACTION="kept the existing password"
else
  PW="$(python3 -c 'import secrets; print(secrets.token_urlsafe(36))')"
  ACTION="minted a new password"
fi

# INHERIT, unlike the subscriber roles, and it is load-bearing: the operator's privileges arrive
# through membership in brain_runtime, and a NOINHERIT member holds none of them until it issues
# SET ROLE -- which would also be the moment its writes stopped looking like its own.
su_psql -d "$DB" -q -v role="$ROLE" -v pw="$PW" -v human="$HUMAN" <<'SQL'
\set ON_ERROR_STOP on
BEGIN;
-- \gexec rather than a DO block: psql does not interpolate :'role' inside a dollar-quoted body.
SELECT format('CREATE ROLE %I LOGIN', :'role')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'role')
\gexec
ALTER ROLE :"role" WITH LOGIN PASSWORD :'pw' NOSUPERUSER NOCREATEDB NOCREATEROLE INHERIT;
SELECT brain.provision_human_role(:'role', :'human');
COMMIT;
SQL

umask 077
printf '%s' "$PW" > "$SECRETS/$REF"
chmod 600 "$SECRETS/$REF"

printf 'provisioned %s as %s on %s (%s, secret ref %s)\n' "$HUMAN" "$ROLE" "$DB" "$ACTION" "$REF"
su_psql -d "$DB" -c "SELECT role_name, human, granted_by FROM brain.human_role ORDER BY human"

# What is STILL not provisioned on this host, named. A provisioning run that reports only its own
# success is how a host ends up with five of the seven credentials it needs and a green transcript:
# the V7 cutover brief said "five secret references", and provisioning exactly those five yields a
# host where the paging listener cannot connect at all (task 0169; proof in
# outputs/2026-08-18-D3-0169-secret-count/PROOF.txt).
#
# It does NOT change this script's exit code, and that is deliberate rather than timid: a host with
# no operator credential is a CORRECT state -- "on that host nobody is the operator" -- and failing
# here would turn that doctrine into a broken build. The gate that exits non-zero is
# secret-preflight.py itself; run it directly when you want one.
printf '\n'
"$REPO/store/bin/secret-preflight.py" --db "$DB" || true
