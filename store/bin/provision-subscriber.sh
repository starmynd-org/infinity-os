#!/usr/bin/env bash
# One login role per subscriber. The half of migration 10 that cannot be committed.
#
# Migration 10 moved subscriber identity off a client-settable GUC and onto `session_user`, keyed
# through `brain.subscriber_role`. That mapping is useless until each listener has a role of its
# own, and a role needs a password, and a password may never appear in a committed file. So the
# migration carries the grant list (in `brain.provision_subscriber_role`) and this script carries
# the two things it must not: `CREATE ROLE ... PASSWORD`, and writing the value into the secret
# backend `store/session.py` resolves from.
#
# Run once per subscriber per database, as part of applying migration 10:
#
#     store/bin/provision-subscriber.sh --db brain --subscriber operator-paging
#
# Idempotent. Re-running an already-provisioned subscriber re-applies the grants and the mapping
# and LEAVES THE PASSWORD ALONE, because rotating it under a running listener is a silent outage.
# Pass --rotate to mint a new one deliberately.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONTAINER="${BRAIN_PG_CONTAINER:-brain-postgres}"
SECRETS="${BRAIN_SECRET_DIR:-$HOME/.brain-postgres-secrets}"
DB=""
SUB=""
ROTATE=no

die() { printf 'provision-subscriber: %s\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --db)         DB="${2:-}"; shift 2 ;;
    --subscriber) SUB="${2:-}"; shift 2 ;;
    --rotate)     ROTATE=yes; shift ;;
    *) die "usage: provision-subscriber.sh --db <database> --subscriber <slug> [--rotate]" ;;
  esac
done
[ -n "$DB" ]  || die "--db is required. There is no default: provisioning the wrong database is a
grant nobody meant to make."
[ -n "$SUB" ] || die "--subscriber is required."

# The role name has ONE producer, `store.session.subscriber_role_name`, and it is the same one the
# runtime uses to decide which credential to connect with. Deriving it twice is how a listener
# ends up connecting as a role that was never granted anything.
ROLE="$(cd "$REPO" && python3 -c "
import sys; sys.path.insert(0, '.')
from store.session import subscriber_role_name
print(subscriber_role_name(sys.argv[1]))" "$SUB")"
REF="brain-postgres-subscriber-$SUB"

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
                        WHERE n.nspname='brain' AND p.proname='provision_subscriber_role'" \
  | grep -q 1 || die "database '$DB' has not had migrations/0010_subscriber_identity.sql applied.
Apply the migration first: this script provisions against the grant list that migration defines."

mkdir -p "$SECRETS"; chmod 700 "$SECRETS"

if [ -s "$SECRETS/$REF" ] && [ "$ROTATE" = no ]; then
  PW="$(cat "$SECRETS/$REF")"
  ACTION="kept the existing password"
else
  PW="$(python3 -c 'import secrets; print(secrets.token_urlsafe(36))')"
  ACTION="minted a new password"
fi

# CREATE ROLE is cluster-wide; every GRANT below is not. A role provisioned against a scratch
# database therefore holds NOTHING in `brain`, which is the property that makes it safe to build
# scratch databases in the same cluster as the live store.
su_psql -d "$DB" -q -v role="$ROLE" -v pw="$PW" -v sub="$SUB" <<'SQL'
\set ON_ERROR_STOP on
BEGIN;
-- \gexec rather than a DO block: psql does not interpolate :'role' inside a dollar-quoted body,
-- so a DO block here silently receives the literal text ":'role'" -- measured, it is a syntax
-- error at apply time rather than a wrong role, which is the good version of that mistake.
SELECT format('CREATE ROLE %I LOGIN', :'role')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'role')
\gexec
ALTER ROLE :"role" WITH LOGIN PASSWORD :'pw' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
SELECT brain.provision_subscriber_role(:'role', :'sub');
COMMIT;
SQL

umask 077
printf '%s' "$PW" > "$SECRETS/$REF"
chmod 600 "$SECRETS/$REF"

printf 'provisioned %s as %s on %s (%s, secret ref %s)\n' "$SUB" "$ROLE" "$DB" "$ACTION" "$REF"
su_psql -d "$DB" -c "SELECT role_name, subscriber, granted_by FROM brain.subscriber_role
                      ORDER BY subscriber"

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
