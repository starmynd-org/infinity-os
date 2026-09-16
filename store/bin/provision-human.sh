#!/usr/bin/env bash
# A NAMED human's login. The half of a `swarm admin human provision` that cannot go through the
# store, and the file that says plainly why it cannot.
#
# `store.apply` opens one of the five logins in `store/session.py:ROLES`. Migration 2 makes every
# one of them `NOSUPERUSER NOCREATEDB NOCREATEROLE`, on purpose, and minting a login needs a
# privilege none of them has and none of them should have. Putting a superuser connection inside
# `store/` so that provisioning could "go through the waist" would open a far larger hole than
# the one the admin verb group closes: a route to CREATE ROLE inside the module every surface
# imports. So the verb records the intent and the outcome in `brain.admin_change`, transactional
# and attributed, and THIS runs the two things a committed artifact and a non-superuser login
# must not: `CREATE ROLE ... PASSWORD`, and writing that value into the secret backend.
#
#     store/bin/provision-human.sh --db brain --human jake
#     store/bin/provision-human.sh --db brain --human jake --revoke
#
# THIS SCRIPT DOES NOT OWN `operator`. `store/bin/provision-operator.sh` does, it is named in ten
# error messages and in `store/session.py`, and every operator surface already writes through
# `brain_operator`. Two provisioners for one role is how two provisioners drift, so this one
# refuses that slug and says where to go.
#
# Idempotent. Re-running an already-provisioned human re-applies the grant and the mapping and
# LEAVES THE PASSWORD ALONE, because rotating it under a live session is a silent outage. Pass
# --rotate to mint a new one deliberately.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONTAINER="${BRAIN_PG_CONTAINER:-brain-postgres}"
SECRETS="${BRAIN_SECRET_DIR:-$HOME/.brain-postgres-secrets}"
DB=""
HUMAN=""
ROTATE=no
REVOKE=no

die() { printf 'provision-human: %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<USAGE
usage: provision-human.sh --db <database> --human <slug> [--rotate] [--revoke]

  --db      required. There is no default: provisioning the wrong database is a grant nobody
            meant to make.
  --human   required. Lowercase, digits and hyphens. Becomes the role brain_human_<slug> and the
            secret reference brain-postgres-human-<slug>.
  --rotate  mint a new password for a human who already has one. Not the default: rotating under
            a live session is a silent outage.
  --revoke  the LOCKOUT half: ALTER ROLE ... NOLOGIN and remove the credential file. The MAPPING
            is removed by \`swarm admin human revoke\`, which records who did it. Run the verb
            first; this makes the login unusable afterwards.
USAGE
}

[ $# -gt 0 ] || { usage; exit 2; }

while [ $# -gt 0 ]; do
  case "$1" in
    --db)     DB="${2:-}"; shift 2 ;;
    --human)  HUMAN="${2:-}"; shift 2 ;;
    --rotate) ROTATE=yes; shift ;;
    --revoke) REVOKE=yes; shift ;;
    -h|--help|help) usage; exit 0 ;;
    *) usage >&2; die "unrecognised argument '$1'" ;;
  esac
done
[ -n "$DB" ]    || die "--db is required. There is no default: provisioning the wrong database is
a grant nobody meant to make."
[ -n "$HUMAN" ] || die "--human is required."

case "$HUMAN" in
  operator) die "the 'operator' human is provisioned by store/bin/provision-operator.sh, which
owns the brain_operator role. That role is named in store/session.py and in every operator
surface, and a second provisioner for one role is how two provisioners drift. Run:
    store/bin/provision-operator.sh --db $DB" ;;
esac

# ONE PRODUCER FOR THE ROLE NAME, the same rule provision-subscriber.sh follows. Deriving it
# twice is how a login ends up connecting as a role that was never granted anything.
ROLE="$(cd "$REPO" && python3 -c "
import sys; sys.path.insert(0, '.')
from store.session import human_role_name, human_secret_ref
print(human_role_name(sys.argv[1])); print(human_secret_ref(sys.argv[1]))" "$HUMAN")"
REF="$(printf '%s\n' "$ROLE" | sed -n 2p)"
ROLE="$(printf '%s\n' "$ROLE" | sed -n 1p)"

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

if [ "$REVOKE" = yes ]; then
  # The mapping is NOT removed here. `swarm admin human revoke` removes it inside a transaction
  # that also writes who did it to brain.admin_change, and a second route that skipped the trail
  # would be the audit hole this whole lane exists to close. This closes the login.
  su_psql -d "$DB" -q -v role="$ROLE" <<'SQL'
\set ON_ERROR_STOP on
SELECT format('ALTER ROLE %I NOLOGIN', :'role')
 WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'role')
\gexec
SQL
  if [ -e "$SECRETS/$REF" ]; then rm -f "$SECRETS/$REF"; fi
  printf 'revoked login %s on %s: NOLOGIN set, secret reference %s removed.\n' "$ROLE" "$DB" "$REF"
  printf 'The brain.human_role MAPPING is removed by `swarm admin human revoke %s`, which records
who did it. If you have not run that, this role is still mapped and is now unable to log in.\n' \
    "$HUMAN"
  exit 0
fi

# THE CEILING IS CHECKED BEFORE A ROLE IS MINTED, not only by the trigger that would refuse the
# mapping afterwards. Both are real and neither is redundant: the trigger is the one that cannot
# be walked past, and this one stops the cluster growing a login whose mapping is then refused,
# which would leave a role nobody meant to create and no record of why.
su_psql -tAd "$DB" -c "SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                        WHERE n.nspname='brain' AND p.proname='human_login_ceiling'" \
  | grep -q 1 && {
  HAVE="$(su_psql -tAd "$DB" -c 'SELECT count(*) FROM brain.human_role')"
  CAP="$(su_psql -tAd "$DB" -c 'SELECT brain.human_login_ceiling()')"
  MAPPED="$(su_psql -tAd "$DB" -c "SELECT count(*) FROM brain.human_role WHERE role_name = '$ROLE'")"
  if [ "$MAPPED" = 0 ] && [ "$HAVE" -ge "$CAP" ]; then
    die "refusing to mint a $((HAVE + 1))th human login against a stated ceiling of $CAP ($HAVE
exist on $DB). This is a decision boundary, not a capacity limit: the operator ruled on
2026-08-27 (row 0386, decision 2) that a thirteenth login REOPENS the decision. Raise it by
replacing brain.human_login_ceiling() in a migration, or revoke a human who has left."
  fi
}

mkdir -p "$SECRETS"; chmod 700 "$SECRETS"

if [ -s "$SECRETS/$REF" ] && [ "$ROTATE" = no ]; then
  PW="$(cat "$SECRETS/$REF")"
  ACTION="kept the existing password"
else
  PW="$(python3 -c 'import secrets; print(secrets.token_urlsafe(36))')"
  ACTION="minted a new password"
fi

# INHERIT, for the reason provision-operator.sh gives: the privileges arrive through membership
# in brain_runtime, and a NOINHERIT member holds none of them until it issues SET ROLE, which is
# also the moment its writes stop looking like its own.
su_psql -d "$DB" -q -v role="$ROLE" -v pw="$PW" -v human="$HUMAN" <<'SQL'
\set ON_ERROR_STOP on
BEGIN;
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
