#!/usr/bin/env bash
# A scratch database for the engine lane, built from D1's migrations and nothing else.
#
# Why this exists rather than testing against `brain`: D4's brief says build against a scratch
# database, and the concurrency gate posts 20 rows and races 12 claimers at them. Doing that in
# the store other lanes are reading would leave their board full of `race task 7`. It also proves
# something worth proving on its own: migration 1 applies cleanly to an empty database, so the
# schema is reproducible rather than a thing that happened once by hand.
#
# The live file bus at ~/.swarm is not touched by anything in this repo. This script talks to
# Postgres only.
set -euo pipefail

# THE NAME, AND WHETHER ANYONE CHOSE IT. Task 0248, 2026-08-19.
#
# `brain_scratch` is the store every lane's suite reads by default, so a destructive verb that
# lands on it because nobody said otherwise has the whole fleet as its blast radius. DB_WAS_NAMED
# is what the two DROP sites below consult, and it is the difference between "drop the database I
# told you to drop" and "drop the one you guessed". An empty ENGINE_SCRATCH_DB counts as unset,
# the same way `:-` already reads it, because a caller that exported an empty variable did not
# name a database either.
DEFAULT_DB=brain_scratch
DB="${ENGINE_SCRATCH_DB:-$DEFAULT_DB}"
if [ -n "${ENGINE_SCRATCH_DB:-}" ]; then DB_WAS_NAMED=yes; else DB_WAS_NAMED=no; fi
CONTAINER="${BRAIN_PG_CONTAINER:-brain-postgres}"
SECRETS="${BRAIN_SECRET_DIR:-$HOME/.brain-postgres-secrets}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

die() { printf 'scratch-db: %s\n' "$*" >&2; exit 1; }

# EVERY DOOR, IN ONE PLACE, AND REACHABLE WITHOUT TOUCHING A DATABASE. This function exists
# because reading this script's argument list used to be the one input that destroyed a store:
# the dispatch defaulted a missing subcommand to `create`, only an UNRECOGNISED word reached the
# usage arm, and `create` opens with DROP DATABASE. On 2026-08-18 at 22:19:03Z an agent ran
# `./engine/bin/scratch-db.sh 2>&1 | head -25` to find out what the verbs were and dropped the
# shared brain_scratch at ledger 30, rebuilding it to 7 before SIGPIPE from `head` killed it under
# `set -euo pipefail`. Tasks 0227 (forensics and repair) and 0248 (this fix).
usage() {
  cat <<USAGE
usage: scratch-db.sh <subcommand>

  create     DROP \$ENGINE_SCRATCH_DB and rebuild it from every lane's schema.  DESTRUCTIVE
  migrate    apply only the ledger versions this database has not recorded.     additive
  ensure     migrate it if it exists, create it if it does not.  The door a suite should call
  ledger     print the (version, file) list this script would apply.  Touches no database
  truncate   empty brain's tables, keeping the schema.  What a test reset calls
  drop       DROP \$ENGINE_SCRATCH_DB.                                          DESTRUCTIVE
  psql       run psql against it; every remaining argument is passed straight through
  help       this text.  So are -h, --help, and no arguments at all

  ENGINE_SCRATCH_DB names the database.  Unset it means $DEFAULT_DB, which every lane's suite
  reads, so create and drop REFUSE an EXISTING $DEFAULT_DB unless you name it yourself:

      ENGINE_SCRATCH_DB=$DEFAULT_DB scratch-db.sh create

  There is no prompt to answer: the opt-in is an environment variable, which a non-interactive
  runner sets exactly as easily as a human types it.
USAGE
}

secret() {
  local f="$SECRETS/$1"
  [ -r "$f" ] || die "secret reference '$1' did not resolve from $SECRETS. Failing closed."
  local v; v="$(cat "$f")"
  [ -n "$v" ] || die "secret reference '$1' resolved empty. Failing closed."
  printf '%s' "$v"
}

# Guard, because a typo here would drop the store every other lane is building on.
case "$DB" in
  brain) die "refusing to operate on the live store database 'brain'. This script is scratch only." ;;
esac

su_psql() {
  docker exec -i -e PGPASSWORD="$(secret brain-postgres-bootstrap-superuser)" \
    "$CONTAINER" psql -v ON_ERROR_STOP=1 -h 127.0.0.1 -U postgres "$@"
}

# THE RACE THE THREE FUNCTIONS BELOW EXIST TO CLOSE. Task 0344, measured 2026-08-18, and it is
# the half of 0321 that fix did not reach.
#
# Two lanes running `create` at the same moment, each building its OWN database under its own
# per-run name, still collide. `migrations/0002_roles.sql:38-41` is four bare
#   ALTER ROLE brain_owner WITH PASSWORD :'owner_pw' NOSUPERUSER NOCREATEDB NOCREATEROLE;
# and those write `pg_authid`, a SHARED catalog: one tuple per role for the whole cluster, not one
# per database. At that statement the two lanes are not building two independent databases, they
# are rewriting the SAME four rows, and Postgres refuses the loser outright:
#   psql:<stdin>:38: ERROR:  tuple concurrently updated
# Measured here on a bare two-way race, first attempt: one build `ready: 32 tables,
# schema_migration 23`, the other dead at line 38 with rc=3. 0321's retry loop lives in cmd_create
# and wraps CREATE DATABASE ONLY, above the migration loop, so it never covered this.
# Every suite that calls `create` inherits the hole, including both budget suites under run-all.sh.
#
# WHY A HOST FILE LOCK AND NOT pg_advisory_lock, which is the obvious answer and is WRONG.
# MEASURED on this container (PostgreSQL 16.10): advisory locks are scoped to the CURRENT DATABASE,
# not to the cluster. Session A held pg_advisory_lock on a key inside one scratch database; session
# B asked for THE SAME KEY from `postgres` with lock_timeout=3s and was granted it immediately, and
# `pg_locks` showed the row carrying that database's oid rather than 0. Two racing lanes are in two
# different databases by construction, so a Postgres advisory lock between them serialises nothing:
# it reads correct, costs a round trip, and still loses one build in two. Holding one from a shared
# database would need a side session kept alive across the whole apply, and a side session that is
# ever orphaned leaks the lock and deadlocks every later create, which is worse than the bug.
#
# Every racer is a process on THIS host talking to one container, so a host file lock is the mutex
# that actually covers them, and the kernel drops it when the fd closes, including on SIGKILL.
# There is nothing to leak and no stuck-holder state to reap. A racer on a DIFFERENT host is not
# covered by it, and that is what the retry in apply_one is for.
CLUSTER_WRITE_LOCK="${SCRATCH_CLUSTER_LOCK:-${TMPDIR:-/tmp}/brain-scratch-db-cluster-write.lock}"

# Does this file write a catalog SHARED by the whole cluster, rather than one private to $DB? Read
# out of the file's own text, so a cluster-wide statement a later lane writes is covered without
# anyone remembering to extend a list here.
#
# DELIBERATELY OVER-INCLUSIVE. It also matches `ALTER ROLE ... IN DATABASE ...` (migrations 0010,
# 0012 and 0020), which writes `pg_db_role_setting` keyed by (database, role) and so is private to
# $DB and never contended, and it matches the phrase in a comment. Over-matching costs those three
# files a mutex they do not need, for a few milliseconds each; under-matching reopens this race
# silently. Only one of those two mistakes announces itself.
cluster_wide() {
  grep -qiE '(CREATE|ALTER|DROP)[[:space:]]+(ROLE|USER|GROUP|TABLESPACE|DATABASE)' "$1"
}

# Apply one migration file to $DB. Shared by create (which applies all of them into an empty
# database) and migrate (which applies only the ones this database has not recorded).
apply_raw() {
  local f="$1"
  case "$(basename "$f")" in
    0002_*)
      # 0002 names the database in two REVOKE/GRANT lines. Everything else in it is
      # cluster-wide (roles) or schema-scoped, so the substitution is exactly those two lines
      # and is done here rather than by editing D1's file, which this lane does not own.
      sed "s/ON DATABASE brain FROM/ON DATABASE $DB FROM/; s/ON DATABASE brain TO/ON DATABASE $DB TO/" \
        "$f" \
        | su_psql -d "$DB" -q \
            -v owner_pw="$(secret brain-postgres-role-owner)" \
            -v producer_pw="$(secret brain-postgres-role-producer)" \
            -v subscriber_pw="$(secret brain-postgres-role-subscriber)" \
            -v runtime_pw="$(secret brain-postgres-role-runtime)" \
            -f -
      ;;
    *) su_psql -d "$DB" -q -f - < "$f" ;;
  esac
}

# The same apply, serialised against every other lane on this host when the file touches a shared
# catalog. fd 9 is used nowhere else in this script, and the subshell holds it for exactly one
# file's apply rather than for the whole build: 19 of the 23 schema files in the ledger today are
# private to $DB and there is no reason for two lanes to take turns over those.
#
# The `: >>` probe is creatability, not existence, and it does not truncate: on a host where the
# lock path cannot be opened we apply unlocked rather than refusing to build, because the retry in
# apply_one still makes that correct, only slower.
apply_once() {
  local f="$1"
  if cluster_wide "$f" && : >>"$CLUSTER_WRITE_LOCK" 2>/dev/null; then
    ( flock 9; apply_raw "$f" ) 9>>"$CLUSTER_WRITE_LOCK"
  else
    apply_raw "$f"
  fi
}

# The sentences Postgres says ONLY when two writers met. Narrow on purpose: none of these is what a
# migration that is simply wrong says, so retrying them cannot turn a broken migration into a slow
# green, which is the failure mode a blanket retry would introduce.
CONTENTION_RE='tuple concurrently (updated|deleted)|deadlock detected|could not serialize access'

# Apply, and retry the contention that the host lock cannot cover: a lane on another host, or a
# cluster-wide statement written in a form `cluster_wide` does not recognise.
#
# RETRYING IS SAFE BECAUSE EVERY SCHEMA FILE IN THIS TREE IS ONE TRANSACTION. Checked 2026-08-18
# across all of migrations/, budget/schema/ and queue/schema/: zero files lack a top-level BEGIN,
# so a file that loses the race applied NOTHING and the retry starts from where the first attempt
# started. A lane that lands a non-transactional migration invalidates this and has to say so here.
#
# The backoff is JITTERED, unlike the flat 2s cmd_create uses for the template1 race, and that is
# not decoration: three lanes lose together, and three flat sleeps put all three back on the same
# catalog tuple at the same instant, so a lockstep retry re-collides for as many rounds as it has
# attempts. stderr is captured to a file rather than merged into stdout, so psql's NOTICEs still
# arrive on stderr and a migration's own query output still arrives on stdout, as before.
apply_one() {
  local f="$1" try=1 rc=0 errf nap said
  errf="$(mktemp)"
  while :; do
    rc=0
    apply_once "$f" 2>"$errf" || rc=$?

    if [ "$rc" -eq 0 ]; then
      if [ -s "$errf" ]; then cat "$errf" >&2; fi
      rm -f "$errf"
      return 0
    fi

    # Not contention: this is the caller's failure to report, with psql's own text, exactly as it
    # read before this wrapper existed.
    if ! grep -qiE "$CONTENTION_RE" "$errf"; then
      cat "$errf" >&2
      rm -f "$errf"
      return "$rc"
    fi

    if [ "$try" -ge 5 ]; then
      said="$(cat "$errf")"; rm -f "$errf"
      die "$(basename "$f") lost a shared-catalog race $try times against another concurrent build
and did not converge. That is contention between lanes, NOT a migration that does not apply, so
re-run this suite alone before believing anything else it printed. Postgres said:
$said"
    fi

    printf 'scratch-db: %s hit concurrent-write contention, retrying (attempt %s of 5)\n' \
      "$(basename "$f")" "$try" >&2
    nap=$(( try * 5 + (RANDOM % 10) ))
    try=$((try + 1))
    sleep "$(( nap / 10 )).$(( nap % 10 ))"
  done
}

# Every lane's schema directory. There is ONE ledger, `brain.schema_migration`, and four lanes
# number into it from three directories: D1 writes `migrations/`, D6a writes `budget/schema/`,
# D6b writes `queue/schema/`. A builder that globbed `migrations/` alone did not build a partial
# database, it built a database that LOOKED complete -- `schema_migration` said 15, the maximum
# any file in `migrations/` records -- while missing every budget and queue object. Measured
# 2026-08-16 on brain_scratch, task 0212: zero `queue_*` tables, zero `budget_*` tables, and three
# suites reading red or short because of it. `test_claimer_predicate.py` raised
# `UndefinedFunction: brain.default_is_null_branch` (queue/schema/0009, version 11) for one hard
# failure; `test-surface-doors.py` skipped two assertions naming `brain.budget_incident`
# (budget/schema/0003, version 3); all four `queue/tests/*` could not run at all.
#
# `ingest/schema/` is deliberately NOT here. It creates schema `ingest`, not `brain`, records no
# schema_migration row, and its own suite builds and drops its own database. It is not in this
# ledger and must not be given a version.
#
# SCRATCH_SCHEMA_DIRS overrides the list, space separated, for the one caller that needs LESS than
# everything: `test-lane-budget-gate.sh` scene 7 needs a store where the budget tables genuinely do
# not exist, to prove `claim` degrades instead of raising UndefinedTable and taking the fleet down.
# It used to get that store by accident, because this script could not build the budget schema at
# all. Now it has to ask, which is the point -- the scene's premise is stated where a reader can
# see it rather than resting on a defect in the builder.
read -r -a SCHEMA_DIRS <<<"${SCRATCH_SCHEMA_DIRS:-migrations budget/schema queue/schema}"

# WHETHER THE CALLER NARROWED THE LIST, which `cmd_ledger` needs and nothing else does. The same
# provenance distinction DB_WAS_NAMED draws above: a subset is missing whole lanes' versions by
# construction, so EVERY number those lanes hold reads as a hole. Reporting those as holes would
# be a false alarm on a correct run, and a gap report that cries wolf on `SCRATCH_SCHEMA_DIRS=
# migrations` is one nobody reads on the day there is a real one.
if [ -n "${SCRATCH_SCHEMA_DIRS:-}" ]; then DIRS_WERE_NAMED=yes; else DIRS_WERE_NAMED=no; fi

# The version a file claims, read out of its own INSERT rather than off its filename.
#
# The filename prefix is a per-lane counter and it LIES: `queue/schema/0009_null_branch_act_scan
# .sql` records version 11 and `queue/schema/0010_queue_item_lineage_coherent.sql` records 16,
# because two lanes reached for `0009` and `0010` on the same afternoon and the queue lane took
# the next free ledger number without renaming its file. Ordering by prefix would apply 16 before
# 12 and would apply budget's 3 after migrations' 15, which is the wrong way round: 0003_budget
# creates `budget_charge` and `budget_incident`, and migration 5 is what puts the lineage triple
# on them. Recorded version is the only key that reproduces the order the live store actually saw.
#
# `tr` first because the INSERT and its VALUES sit on one line in some files and two in others.
recorded_version() {
  local f="$1" v
  v="$(tr '\n' ' ' < "$f" \
       | grep -oE 'INSERT INTO brain\.schema_migration \(version, *name\) *VALUES *\( *[0-9]+' \
       | grep -oE '[0-9]+ *$' | tr -d ' ' | sort -un)"
  if [ -z "$v" ]; then
    # IS THE WHOLE DIRECTORY A LEDGER DIRECTORY, OR IS THIS ONE FILE MISSING A ROW? Those are
    # different mistakes and they were reported the same way, which cost Terminal 26 a sealed
    # release run on 2026-09-07: it passed SCRATCH_SCHEMA_DIRS with four directories including
    # `ingest/schema`, and got a message naming ONE file's missing row -- which reads like a
    # fixable defect in that file. It is not. NONE of ingest/schema's files records a ledger row,
    # by design: `tools/check-at-head.sh` has a scene that REFUSES a ledger row appearing there.
    # So that directory can never be named here, and the old message pointed at the first file
    # instead of at the argument.
    local d; d="$(dirname "$f")"
    local total ledgered
    total="$(ls "$d"/*.sql 2>/dev/null | wc -l | tr -d '[:space:]')"
    ledgered="$(grep -l 'schema_migration' "$d"/*.sql 2>/dev/null | wc -l | tr -d '[:space:]')"
    if [ "${ledgered:-0}" -eq 0 ] && [ "${total:-0}" -gt 0 ]; then
      die "$d IS NOT A LEDGER DIRECTORY and cannot be named in SCRATCH_SCHEMA_DIRS: none of its
    $total .sql files records a brain.schema_migration row. This is not a defect in
    $(basename "$f") -- it is a category error in the argument. The ledger directories are
    the SCRATCH_SCHEMA_DIRS default: migrations budget/schema queue/schema."
    fi
    die "$(basename "$f") records no brain.schema_migration row, though $ledgered of $total files
    in $d do. Refusing to apply it: it would run again on every pass and the ledger would never
    catch up."
  fi
  [ "$(wc -l <<<"$v")" -eq 1 ] || die "$(basename "$f") records more than one version
    ($(tr '\n' ' ' <<<"$v")). One file, one ledger row."
  printf '%s' "$v"
}

# `version<TAB>path` for every schema file in every lane directory, ordered by recorded version,
# refusing when two files claim the same number. THAT is the collision worth catching -- a
# duplicate prefix across two directories is cosmetic, a duplicate ledger number means one lane's
# migration silently never applies, because every file writes `ON CONFLICT (version) DO NOTHING`.
#
# The result lands in the global MIGRATIONS via load_migrations, and callers read that variable
# rather than calling this in `< <(...)`. That is not a style preference. Process substitution and
# command substitution both fork, so a `die` in here would have exited the SUBSHELL and left the
# caller running -- measured while writing this: a planted duplicate version printed its refusal
# to stderr and the ledger still listed every other file and returned 0. A guard that reports and
# does not stop is the failure it exists to prevent, wearing the guard's clothes.
migration_list() {
  local d f v rows="" dupes
  for d in "${SCHEMA_DIRS[@]}"; do
    for f in "$REPO/$d"/[0-9][0-9][0-9][0-9]_*.sql; do
      [ -e "$f" ] || continue
      v="$(recorded_version "$f")" || return 1
      rows+="$v	$f"$'\n'
    done
  done
  rows="$(sort -n <<<"$rows" | grep -v '^$')"
  dupes="$(cut -f1 <<<"$rows" | uniq -d)"
  [ -z "$dupes" ] && { printf '%s\n' "$rows"; return 0; }
  die "two schema files claim the same ledger version(s): $(tr '\n' ' ' <<<"$dupes")
$(grep -E "^($(tr '\n' '|' <<<"$dupes" | sed 's/|$//'))	" <<<"$rows")
Every file writes ON CONFLICT (version) DO NOTHING, so the loser would apply nothing and still
report success. Renumber one of them before anything runs."
}

# Read the ledger into the main shell ONCE, so a refusal inside it stops this script. Every caller
# below iterates MIGRATIONS; none of them re-globs the tree.
MIGRATIONS=""
load_migrations() {
  MIGRATIONS="$(migration_list)" || exit 1
}

# THE SECOND HALF OF THE 0248 INCIDENT, AND THE HALF A USAGE LINE DOES NOT CLOSE.
#
# Printing usage on a bare invocation stops the exact command that destroyed brain_scratch. It
# does not stop the next one. `scratch-db.sh drop` and `scratch-db.sh create` are both explicit
# verbs with an IMPLICIT target, and a lane that forgets the `ENGINE_SCRATCH_DB=` prefix -- which
# is precisely what happened on 0208, where every SIBLING command in that run carried it -- still
# lands a FORCE drop on the store every suite reads.
#
# So the rule is about the TARGET, not the verb: a destructive verb refuses a database whose name
# came from the default rather than from a caller, AND ONLY WHEN THAT DATABASE EXISTS. The second
# clause is what keeps the safe paths exactly as cheap as they were. `ensure` on a missing
# brain_scratch still delegates to `create` and still builds it, because there is nothing there to
# destroy; the refusal fires only where a populated store would have gone.
#
# NOT A PROMPT, deliberately. Every caller of this script is a suite or a runner, and a
# confirmation no runner can answer is a new outage rather than a fix. The opt-in is
# `ENGINE_SCRATCH_DB=brain_scratch`, which a script sets as easily as a human types it, and which
# a reader of the failing command can see is about the shared store.
#
# NOT another name-based refusal either. The file already refuses `brain` by name at :30-32 and
# that guard did not fire here, because from this script's point of view a bare `create` against
# its own default is an ordinary correctly-formed build. Provenance is the thing the name cannot
# tell you, and it also survives a later change of DEFAULT_DB without anyone remembering to.
require_named_target() {
  local verb="$1"
  [ "$DB_WAS_NAMED" = yes ] && return 0
  su_psql -tAd postgres -c "SELECT 1 FROM pg_database WHERE datname = '$DB'" | grep -q 1 || return 0
  die "refusing to $verb $DB. That name came from the DEFAULT, not from you: ENGINE_SCRATCH_DB is
unset, so this would have dropped the shared scratch store every lane's suite reads, and the
database is there right now. On 2026-08-18 that is exactly what happened, from a command that was
only trying to read the usage line. If you MEAN the shared store, say so:
    ENGINE_SCRATCH_DB=$DB $(basename "${BASH_SOURCE[0]}") $verb
and if you meant your own, name it the same way. Nothing was changed."
}

cmd_create() {
  require_named_target create
  su_psql -d postgres -c "DROP DATABASE IF EXISTS $DB WITH (FORCE)" >/dev/null

  # CREATE DATABASE copies template1, and Postgres refuses outright while any OTHER session is
  # connected to template1 -- which is precisely what a sibling lane's `create` is doing for the
  # second its own copy runs. So two lanes starting their suites together is not an error, it is
  # contention, and the answer is to wait rather than to fail.
  #
  # Measured 2026-08-17 (tasks 0301/0321): two lanes' run-all.sh a moment apart, and psql came back
  # `source database "template1" is being accessed by other users`. The caller had run this with
  # `>/dev/null 2>&1`, so that line went nowhere and the suite printed "Is brain-postgres up?" --
  # naming an outage when the container was healthy the whole time. Retry the race; keep the real
  # sentence for the failure that is not one.
  local try=1 err=""
  while :; do
    if err="$(su_psql -d postgres -c "CREATE DATABASE $DB" 2>&1)"; then break; fi
    if [ "$try" -ge 5 ]; then
      die "CREATE DATABASE $DB failed after $try attempts. Postgres said:
$err"
    fi
    try=$((try + 1))
    sleep 2
  done

  # A HALF-BUILT STORE MUST NOT LOOK LIKE A FINISHED ONE. The third failure task 0248 names.
  #
  # A `create` that dies between here and the last migration leaves a database that reads exactly
  # like a real one: `brain.schema_migration` reports a plausible maximum and every suite pointed
  # at it produces a green or a red that is about the schema rather than the code. On 2026-08-18
  # that database sat at ledger 7 against a tree at 31 for 85 minutes and NOTHING said so, because
  # the killer was SIGPIPE from a `head -25` and SIGPIPE writes no Postgres ERROR.
  #
  # This is a row rather than a trap, and that is the point: a trap only runs if the shell lives
  # long enough to run it, and the interruption this exists for is the one where it did not.
  # A marker WRITTEN FIRST and DELETED LAST is true under SIGKILL, SIGPIPE, a pulled container and
  # a lost host, none of which run any cleanup at all.
  #
  # In `public` and not `brain`: schema `brain` does not exist until migration 1 applies, and a
  # create killed before that is still a half-built store. Nothing counts public tables --
  # cmd_create's own count below and queue-scratch-db.sh's report both filter table_schema='brain'
  # -- and on the success path it is gone before anyone can read it anyway.
  su_psql -d "$DB" -q -c "CREATE TABLE public.scratch_build_in_progress
      (started_at timestamptz NOT NULL DEFAULT now(), builder text NOT NULL);
    INSERT INTO public.scratch_build_in_progress (builder) VALUES ('pid $$ on $(uname -n)')" \
    >/dev/null

  # EVERY migration EVERY lane has written, in ledger order, not a hardcoded list. It used to name
  # 0001 and 0002 literally, which was correct on the day it was written and silently wrong the
  # moment migration 4 landed: a freshly built scratch database was three migrations behind the
  # live store while still printing "ready", so a suite could pass here and fail against `brain`.
  # Migration 6 (the numeric signal vocabulary) is the one that made this visible, because the
  # engine's own parity test asserts behaviour that only exists from 6 onward.
  #
  # It was then globbing `migrations/` alone, which is the SAME defect one directory wider, and it
  # survived four months of fixes because a partial database still reports a plausible maximum
  # version. See the SCHEMA_DIRS comment for what that cost, measured.
  local v f
  while IFS=$'\t' read -r v f; do
    apply_one "$f"
  done <<<"$MIGRATIONS"

  # Last, so that every path that did not reach it leaves the marker behind.
  su_psql -d "$DB" -q -c "DROP TABLE public.scratch_build_in_progress" >/dev/null

  printf 'scratch database %s ready: %s tables, schema_migration %s\n' "$DB" \
    "$(su_psql -tAd "$DB" -c "SELECT count(*) FROM information_schema.tables WHERE table_schema='brain' AND table_type='BASE TABLE'")" \
    "$(su_psql -tAd "$DB" -c "SELECT max(version) FROM brain.schema_migration")"
}

# create if it does not exist, migrate if it does. The door a suite should call, because a suite
# cannot know which it needs: `create` DROPs, so calling it unconditionally would kill a sibling
# lane's run, and `migrate` refuses a database that was never built, so calling THAT
# unconditionally fails the first time anyone uses a new scratch name. Both wrong answers were
# live on 2026-08-16 -- `queue/tests/*` assumed the database already existed and died in reset()
# on a TRUNCATE against nothing.
cmd_ensure() {
  if su_psql -tAd postgres -c "SELECT 1 FROM pg_database WHERE datname = '$DB'" | grep -q 1; then
    cmd_migrate
  else
    printf 'scratch database %s does not exist yet; building it\n' "$DB"
    cmd_create
  fi
}

# Bring an EXISTING scratch database up to the working tree, applying only what it has not
# recorded. `create` drops the database, which is wrong to do from a test harness while other
# lanes are working in the same tree against the same scratch; this is the additive door.
#
# Task 0148. The engine suite was read as 40 failures against one cause: `post` died on a column
# a migration in the tree had added and this database had not received. It was `produced_by_ref`
# (migration 9) at 13:48 and `brief` (migration 14) by 20:00 the same day, so the recurrence is
# the defect, not either column. A scratch database that is not reconciled to `migrations/` before
# the suite runs makes the suite's green/red unreadable, and a one-shot `psql -f` only postpones
# that to the next migration a lane writes.
#
# Only-what-is-missing rather than re-running everything: the files declare themselves
# re-runnable, but "declares" is not "measured", and replaying an applied migration is a risk this
# does not need to take. brain.schema_migration is the ledger, and the version a file carries is
# read out of the file's own INSERT (see recorded_version) rather than off its name, because two
# of them are named for a number they do not record.
cmd_migrate() {
  # TWO FACTS, ONE ROUND TRIP. `truncate` runs between every test in several suites and `migrate`
  # runs at the top of every one of them, so the half-built check is folded into the existence
  # query that was already here rather than costing a second `docker exec` per call.
  local built interrupted
  read -r built interrupted <<<"$(su_psql -tAF' ' -d "$DB" -c "SELECT
      (SELECT count(*) FROM information_schema.tables
        WHERE table_schema='brain' AND table_name='schema_migration'),
      (SELECT count(*) FROM information_schema.tables
        WHERE table_schema='public' AND table_name='scratch_build_in_progress')")"

  # The marker first: a create killed before migration 1 has the marker and no ledger, and
  # "it was never built" would be the less accurate of the two true sentences.
  #
  # REFUSING RATHER THAN QUIETLY FINISHING IT. `migrate` could apply the missing versions and heal
  # this, and that is what makes refusing the right answer: this marker cannot tell a build that
  # DIED from a build that is RUNNING RIGHT NOW in another terminal, and applying migrations into
  # a database another process is mid-way through building is a worse outage than a red. A lane
  # that reads this line knows in one sentence what a plausible ledger number would have hidden.
  if [ "$interrupted" = 1 ]; then
    die "$DB was left HALF-BUILT by an interrupted 'create', and is not safe to assert against:
$(su_psql -tAd "$DB" -c "SELECT '    started ' || started_at || ', ' || builder
                           FROM public.scratch_build_in_progress LIMIT 1")
Its brain.schema_migration reports $(su_psql -tAd "$DB" -c "SELECT coalesce(max(version)::text,'nothing')
  FROM brain.schema_migration" 2>/dev/null || echo 'nothing'), which is a number, not a finished store. Either that build is still
running in another terminal, in which case wait for it and re-run this, or it is dead and the
store has to be rebuilt:
    ENGINE_SCRATCH_DB=$DB $(basename "${BASH_SOURCE[0]}") create"
  fi

  [ "$built" = 1 ] || die "$DB has no brain.schema_migration: it was never built. Run 'create' first."

  local applied pending=() f v
  applied="$(su_psql -tAd "$DB" -c "SELECT version FROM brain.schema_migration ORDER BY version")"

  # migration_list has already refused a file that records nothing and a pair that record the same
  # number, so what arrives here is (version, path) in ledger order and the only question left is
  # which of them this database has not seen.
  while IFS=$'\t' read -r v f; do
    grep -qxF "$v" <<<"$applied" || pending+=("$f")
  done <<<"$MIGRATIONS"

  if [ ${#pending[@]} -eq 0 ]; then
    printf 'scratch database %s is current: schema_migration %s, nothing pending\n' "$DB" \
      "$(su_psql -tAd "$DB" -c "SELECT max(version) FROM brain.schema_migration")"
    return 0
  fi

  for f in "${pending[@]}"; do
    printf 'applying %s to %s\n' "$(basename "$f")" "$DB"
    apply_one "$f" || die "$(basename "$f") failed against $DB. The tree holds a migration that
      does not apply; that is the lane that wrote it to fix, not this harness to skip."
  done

  printf 'scratch database %s migrated: %s applied, schema_migration %s\n' "$DB" \
    "${#pending[@]}" \
    "$(su_psql -tAd "$DB" -c "SELECT max(version) FROM brain.schema_migration")"
}

# The SECOND HALF of migration 20, which this builder never did. Task 0312.
#
# Migration 20 makes `brain.work_item.actor_type = 'human'` a property of WHICH LOGIN wrote the
# row, keyed through `brain.human_role`, and its own header states that applying it is TWO steps:
# the file, then `store/bin/provision-operator.sh`. This script only ever did the first, so every
# scratch database it produced sat at ledger 20+ with `brain.human_role` EMPTY and the trigger
# `work_item_human_actor_is_a_login()` correctly refused every `actor_type='human'` write against
# it. Measured 2026-08-17 on a freshly built brain_t3_0312: `bash queue/tests/run-all.sh` printed
# SOMETHING FAILED with three identical InsufficientPrivilege tracebacks
# (test_queue_mechanics.py:234, test_defer_and_demote_joins.py:135,
# test_human_actor_identity.py:124) while the three suites that need no human actor passed 174
# assertions between them. That is a red owned by the harness, and a harness-owned red on a fresh
# database is the one that gets read as "the neighbour broke something" and ignored.
#
# HERE AND NOT IN queue/bin/queue-scratch-db.sh, which is deliberately a thin name over this file
# and whose header is an argument against exactly that kind of per-lane afterthought. The queue
# suites are the ones that need this today; the ledger they build from is shared, so the missing
# step is shared too.
#
# IT DOES NOT CALL provision-operator.sh, AND THAT IS THE POINT. That script does
# `ALTER ROLE ... PASSWORD`, which is CLUSTER-wide, and MINTS a new value when the secret file is
# absent. A test harness that called it would rotate a live console's credential out from under it,
# and on a host where nobody provisioned the operator it would CREATE the operator -- undoing the
# doctrine store/session.py states outright ("a host that has not run it resolves no secret and
# fails closed, which is correct: on that host nobody is the operator"). So this does the
# per-database half only: the mapping and the grants, for a role that already exists, only when
# this host already holds the credential. No CREATE ROLE, no password, nothing cluster-wide that
# was not already true.
#
# A host with no operator secret gets a stated line and suites that say NOT RUN, never a green.
ensure_operator_login() {
  local ref="brain-postgres-role-operator"

  # Below migration 20 (or a SCRATCH_SCHEMA_DIRS subset that excludes it): nothing to provision,
  # and saying so would be noise about a database that is correct as it stands.
  su_psql -tAd "$DB" -c "SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                          WHERE n.nspname='brain' AND p.proname='provision_human_role'" \
    | grep -q 1 || return 0

  if [ ! -s "$SECRETS/$ref" ]; then
    printf 'scratch database %s: NO OPERATOR CREDENTIAL on this host (%s/%s), so brain.human_role
  is empty and any suite that writes actor_type=human will say NOT RUN. That is correct rather
  than broken: on this host nobody is the operator. To become one:
      store/bin/provision-operator.sh --db %s\n' "$DB" "$SECRETS" "$ref" "$DB" >&2
    return 0
  fi

  # The secret without the role happens on a rebuilt cluster: the file survived in $HOME and
  # CREATE ROLE did not. Minting here is refused for the reason above, so say which half is gone.
  su_psql -tAd postgres -c "SELECT 1 FROM pg_roles WHERE rolname = 'brain_operator'" \
    | grep -q 1 || {
    printf 'scratch database %s: the operator SECRET is present but the LOGIN ROLE brain_operator
  does not exist in this cluster. Not minting one from a test harness. Run:
      store/bin/provision-operator.sh --db %s\n' "$DB" "$DB" >&2
    return 0
  }

  # Idempotent by construction: the function is ON CONFLICT (role_name) DO UPDATE and its two
  # GRANTs are no-ops when already held. `operator` is the same human slug provision-operator.sh
  # defaults to and the same one the live store carries, so a scratch database and `brain` agree.
  su_psql -d "$DB" -q -c "SELECT brain.provision_human_role('brain_operator', 'operator')" >/dev/null
  printf 'scratch database %s: operator login mapped, brain.human_role %s row(s)\n' "$DB" \
    "$(su_psql -tAd "$DB" -c 'SELECT count(*) FROM brain.human_role')"
}

cmd_truncate() {
  # Between tests. TRUNCATE needs owner; the runtime role holds DELETE on nothing by design.
  #
  # `brain.project` (migration 44, task 0429) is on this list because `brain.work_item.project`
  # references it: a reset that emptied work_item and left the projects standing would leak one
  # suite's fixture into the next one's board, and `brain.project_open_work` would report projects
  # nobody in the current test created. It is also DELETE-guarded by a trigger for anything but a
  # human login, and TRUNCATE fires no row triggers -- which is correct here for the same reason
  # migration 22 exempts a restore: a reset is not a resume, it is the absence of a world.
  #
  # A store one version behind does not have the table. `to_regclass` answers NULL rather than
  # raising there, so the reset still empties everything else instead of failing whole.
  su_psql -d "$DB" -q -c "
    TRUNCATE brain.thread, brain.message, brain.artifact, brain.question, brain.run,
             brain.agent, brain.objective, brain.runtime_flag, brain.work_item CASCADE;
    SELECT setval('brain.item_id_seq', 1, false);" >/dev/null
  su_psql -d "$DB" -q -c "
    DO \$\$ BEGIN
      IF to_regclass('brain.project') IS NOT NULL THEN
        EXECUTE 'TRUNCATE brain.project CASCADE';
      END IF;
    END \$\$;" >/dev/null
}

# THE SECOND DROP SITE, AND IT TAKES THE SAME GUARD. Someone typing `drop` means the drop -- but
# they mean it about A DATABASE, and the one this lands on when nobody names one is the shared
# store. An explicit verb with an implicit target is the same composition that cost brain_scratch
# on 2026-08-18, one word further along. Every in-tree caller of this door already passes
# ENGINE_SCRATCH_DB (test-codex-guard.sh:141, test-budget-wiring.sh:330, test-infra-failure.sh:222,
# test-lane-budget-gate.sh:98 and :100, test_cancel_withdraws_questions.py), so the guard costs
# them nothing and covers the invocation nobody wrote down.
cmd_drop() {
  require_named_target drop
  su_psql -d postgres -c "DROP DATABASE IF EXISTS $DB WITH (FORCE)" >/dev/null; echo "dropped $DB"
}
cmd_psql() { su_psql -d "$DB" "$@"; }

# The ledger this script would apply, printed and not applied. `ledger` is how a reader answers
# "did two lanes collide on a number" without connecting to anything, which is the question that
# came up five separate times on 2026-08-16.
cmd_ledger() {
  local v f n=0 first="" last="" prev="" g holes=""
  while IFS=$'\t' read -r v f; do
    [ -n "$v" ] || continue
    printf '%3s  %s\n' "$v" "${f#$REPO/}"
    n=$((n + 1))
    [ -n "$first" ] || first="$v"
    if [ -n "$prev" ]; then
      g=$((prev + 1))
      while [ "$g" -lt "$v" ]; do holes+="$g "; g=$((g + 1)); done
    fi
    prev="$v"; last="$v"
  done <<<"$MIGRATIONS"

  # DENOMINATOR. This function prints a VERDICT about the tree's numbering, and a verdict over an
  # empty set is not a pass: with no versions read, "no holes" and "next version is 1" are both
  # things this would say about a tree it never looked at. Every other door here fails loudly on
  # an empty tree by trying to build nothing; the reporting door has to say so itself.
  if [ "$n" -eq 0 ]; then
    printf '\n    0 versions read from: %s\n' "${SCHEMA_DIRS[*]}" >&2
    printf '    A verdict over an empty set is not a pass. There is nothing here to number\n' >&2
    printf '    against, so this prints no next version rather than guessing 1.\n' >&2
    exit 2
  fi

  # THE SECOND HALF OF THE RULE, AND THE HALF READING THE LEDGER DOES NOT GIVE YOU. Task 0408.
  #
  # queue/schema/0015 carries "Pick the next version by reading brain.schema_migration, never by
  # listing a directory," which is right and is not sufficient. Reading the ledger is EXACTLY what
  # surfaces a hole as available. On 2026-08-27 lane E was assigned 36 to 39, wrote 36, 37 and 38,
  # and left 39; lanes F and J then took 40, 41 and 42. A lane obeying the rule perfectly reads
  # the ledger, sees 39 unheld, and takes it -- and a file at 39 applies BETWEEN 38 and 40 on a
  # fresh `create` and AFTER 40, 41 and 42 on a `migrate` of a store that already recorded them,
  # because migration_list orders by recorded version and cmd_migrate applies only what is
  # missing. Two application orders for one file, and the fresh build is the order every suite in
  # this repo tests against.
  #
  # HERE rather than in a lint, because `ledger` is already the thing a lane is told to read
  # before it names a file, it touches no database, and a hole is legal: it is the FILE that fills
  # one that is dangerous, so this reports rather than refuses.
  printf '\n    %s versions read, %s..%s' "$n" "$first" "$last"
  if [ -n "$holes" ]; then
    printf ', HOLES AT %s' "${holes% }"
  else
    printf ', no holes'
  fi
  printf '.  NEXT VERSION IS %s -- take max(version) + 1.\n' "$((last + 1))"

  if [ -n "$holes" ] && [ "$DIRS_WERE_NAMED" = yes ]; then
    printf '    SCRATCH_SCHEMA_DIRS narrowed this to `%s`, so the holes above are the other\n' \
      "${SCHEMA_DIRS[*]}"
    printf '    lanes numbers and are expected. This is not the whole ledger: do not pick a\n'
    printf '    version off it, and do not pick %s off it either.\n' "$((last + 1))"
  elif [ -n "$holes" ]; then
    printf '    NEVER TAKE A HOLE, even though the ledger says it is free. A file at a hole\n'
    printf '    applies between its neighbours on a fresh `create` and after every higher\n'
    printf '    version an existing store already recorded on a `migrate`. Two orders for one\n'
    printf '    file, and the fresh build is the one every suite tests against.\n'
  fi
}

# ASKING WHAT THIS SCRIPT TAKES HAPPENS BEFORE THIS SCRIPT DOES ANYTHING. Above load_migrations,
# which globs three schema directories and can refuse the whole run over a ledger collision, and
# far above anything that opens a connection: a reader who typed no subcommand gets the text and
# nothing else runs. Exit 2 rather than 0 for the empty case because no subcommand IS an error --
# `scratch-db.sh` alone used to mean `create` and a caller that still believes that must not read
# a success -- while an explicit `help` is a request that was satisfied and exits 0.
case "${1:-}" in
  -h|--help|help) usage; exit 0 ;;
  '')
    usage >&2
    printf '\nscratch-db: no subcommand, so nothing was done. This used to mean `create`, which
DROPS %s. See task 0248.\n' "$DB" >&2
    exit 2
    ;;
esac

# Read and validate the ledger before dispatch, not inside a command that may be a no-op. `psql`,
# `truncate` and `drop` do not consult it, but they are cheap and a tree that holds a colliding
# pair is worth refusing at every door rather than at the two that happen to read it.
load_migrations

# The three doors that BUILD or RECONCILE schema also reconcile the operator mapping, and the four
# that do not touch schema do not. Called from the dispatch rather than from inside cmd_create and
# cmd_migrate, because cmd_ensure delegates to both and a call in each would run it twice on the
# cold path -- harmless, since it is idempotent, and still two lines of report for one fact.
case "$1" in
  create)   cmd_create;  ensure_operator_login ;;
  migrate)  cmd_migrate; ensure_operator_login ;;
  ensure)   cmd_ensure;  ensure_operator_login ;;
  ledger)   cmd_ledger ;;
  truncate) cmd_truncate ;;
  drop)     cmd_drop ;;
  psql)     shift; cmd_psql "$@" ;;
  *)
    printf 'scratch-db: no such subcommand: %s\n\n' "$1" >&2
    usage >&2
    exit 2
    ;;
esac
