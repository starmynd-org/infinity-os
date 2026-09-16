#!/usr/bin/env bash
# Drop the scratch databases the test suites GENERATE, and nothing else. Task 0332.
#
# WHY THIS EXISTS. brain-postgres held 93 `brain_%` databases when this was written (counted, not
# estimated), each carrying a full 32-table schema at migration 23. Nothing in the repo has ever
# dropped one. Task 0321 then made the two budget suites build PER-RUN stores (`brain_budget_wire_$$`
# and friends): a GREEN run drops its own store, but a RED run deliberately KEEPS it, because
# reading the rows is how you find out what went wrong. That trade is right and the answer to it is
# a reaper, not fewer post-mortems -- but it means the count now grows by one database per failing
# suite run instead of staying flat.
#
# WHAT IT WILL NEVER TOUCH, which is the whole point of the file. Only names a SCRIPT generated:
#
#     ^brain_((budget_wire|lane_ceiling|lane_ceiling_nobudget)_[0-9]+|scratch_tolerance_l[0-9]+)$
#
# THE FOURTH FAMILY WAS ADDED 2026-09-07 AND IT IS NOT A WIDENING. `prove-tolerance.sh` builds one
# store per ledger, `brain_scratch_tolerance_l64` and up, and this regex matched none of them, so
# four leaked on every run of a tool whose whole purpose is to be run repeatedly. The added
# alternative is anchored on `l` plus digits, so it can only match names a script generated; the
# paragraph below still holds unchanged and no legacy or hand-named store becomes reachable. The
# warning at the end of it is about widening to names a HUMAN chose, and this is not that.
#
# The suffix is `$$`, a pid, so it is all digits. Every one of the ~90 hand-named survivors carries
# a human suffix -- brain_budget_wire_t4_0137, brain_lane_ceiling_t6_0301, brain_bw0301n,
# brain_t3_0153e, brain_t4_0200_control -- and fails that anchor on the non-digit. Verified against
# all 93 names present on 2026-08-17: four matched, all four generated. The three PRE-0321 fixed
# names (brain_budget_wire, brain_lane_ceiling, brain_lane_ceiling_nobudget) do not match either,
# and that is deliberate: legacy stores are an operator's judgement call, not a reaper's, because
# some of them are somebody's open post-mortem. THIS SCRIPT IS NOT THE PLACE TO WIDEN THAT REGEX
# WITHOUT ONE.
#
# THREE GATES, and each one is enough on its own to spare a store:
#
#   1. the name was generated (the regex above), checked in SQL and then AGAIN in bash before any
#      DROP is composed, so a stray psql NOTICE cannot become a database name;
#   2. nothing is connected to it (pg_stat_activity), AND the DROP is issued WITHOUT (FORCE), so
#      the check-then-drop window closes itself -- a run that connects in that gap makes Postgres
#      refuse the drop rather than lose its connections mid-scene. `scratch-db.sh drop` uses FORCE
#      because a caller naming one store means it; a sweeper aimed at a list must not;
#   3. it is older than SCRATCH_REAP_MIN_AGE_HOURS (default 24). A store built ten minutes ago is
#      somebody's live run whatever pg_stat_activity happened to say in the instant we looked.
#
# Age comes off the filesystem rather than a new table: PG_VERSION is written once when the
# database is created and never rewritten, so its mtime is the store's BIRTH time. Measured here
# rather than assumed -- a fresh database read back its create second, and it did not move after a
# table was created in it and rows written. `missing_ok := true` makes a database in a non-default
# tablespace (where 'base/<oid>' is the wrong path) return NULL, and NULL loses the comparison, so
# an unreadable age spares the store instead of reaping it.
#
# SEPARATE FILE AND NOT A `scratch-db.sh reap` DOOR. scratch-db.sh is per-database and it calls
# load_migrations at dispatch, which `die`s when two schema files claim one ledger version. A
# reaper that refused to run because the working tree had a migration collision would stop reaping
# exactly when the fleet is churning hardest. Nothing here reads the tree.
set -uo pipefail

CONTAINER="${BRAIN_PG_CONTAINER:-brain-postgres}"
SECRETS="${BRAIN_SECRET_DIR:-$HOME/.brain-postgres-secrets}"
MIN_AGE_HOURS="${SCRATCH_REAP_MIN_AGE_HOURS:-24}"

# ONE definition, used by SQL and by bash. Keep them identical: the second check exists to catch a
# mangled line, not to hold a different opinion about what a generated name is.
# `scratch_tolerance` IS THE FOURTH FAMILY. R-TOLERANCE-STORE-NAMES-01, CAP14 REV-076.
#
# `engine/bin/prove-tolerance.sh` builds one store per ledger on every run and this regex matched
# none of them, so a script whose header says it drops the scratch databases the suites generate
# "and nothing else" was leaving four behind each time. CAP14 also declined to RUN the tool over
# the original names, which lacked `scratch` and therefore fell outside decision 20's disposable-DB
# protocol -- so a hand-written family list did not merely leak stores, it stopped an independent
# party from driving the thing the stores exist to prove.
#
# THAT IS THE SAME DEFECT AS THE TWO IT WAS FILED WITH, and CAP14's point is the general one: a
# hand-written list standing where a derived one belongs. This one cannot be derived -- there is no
# authority to read a family from, because the families ARE the naming convention -- so it stays a
# list, and the honest mitigation is that adding a family to it is now part of adding a builder.
# THE NEW FAMILY IS ITS OWN ALTERNATIVE, NOT A NAME ADDED TO THE OLD LIST. The first version of
# this edit appended `|scratch_tolerance_l[0-9]+` inside the existing group and relaxed the shared
# `_[0-9]+` suffix to `_?[0-9]*` to accommodate it -- which would have made `brain_budget_wire`
# with no digits at all match, WIDENING a drop rule while claiming to add a family. Caught by
# reading the alternation rather than the diff.
GENERATED_RE='^brain_((budget_wire|lane_ceiling|lane_ceiling_nobudget)_[0-9]+|scratch_tolerance_l[0-9]+)$'

die() { printf 'reap-scratch-dbs: %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'USAGE'
usage: reap-scratch-dbs.sh [list|reap] [--min-age HOURS] [name ...]

  list   (default)  print the generated scratch stores that WOULD be dropped, drop nothing
  reap              drop them

  --min-age HOURS   spare stores younger than this. Decimals allowed. Default 24
                    (or $SCRATCH_REAP_MIN_AGE_HOURS).
  name ...          NARROW the candidates to these databases. Every gate still applies, so
                    naming a store is never a way to reap one the rules protect.
USAGE
}

MODE=list
NAMES=()
while [ $# -gt 0 ]; do
  case "$1" in
    list|reap)   MODE="$1" ;;
    --min-age)   shift; [ $# -gt 0 ] || die "--min-age needs a value"; MIN_AGE_HOURS="$1" ;;
    -h|--help)   usage; exit 0 ;;
    -*)          usage >&2; die "unknown option: $1" ;;
    *)           NAMES+=("$1") ;;
  esac
  shift
done

# Validated HERE and not by Postgres, because this value is interpolated into the SQL below. A
# rejected value must die with a sentence about the flag; letting it through would report it as
# "could not read pg_database", which sends the reader to the container.
[[ "$MIN_AGE_HOURS" =~ ^[0-9]+(\.[0-9]+)?$ ]] \
  || die "--min-age must be a non-negative number of hours, got '$MIN_AGE_HOURS'"

secret() {
  local f="$SECRETS/$1"
  [ -r "$f" ] || die "secret reference '$1' did not resolve from $SECRETS. Failing closed."
  local v; v="$(cat "$f")"
  [ -n "$v" ] || die "secret reference '$1' resolved empty. Failing closed."
  printf '%s' "$v"
}

# `</dev/null` IS LOAD BEARING, and it cost this file a wrong answer before it was there.
# `docker exec -i` reads stdin. Called from inside a `while read ... done <<<"$rows"` loop, the
# first iteration's docker exec swallows every remaining row, and the loop ends one line in. The
# symptom is not an error: the script reported `dropped 0` while a store that passed every gate sat
# untouched, which reads like a filter bug and is an input bug. The loop below is also written over
# an ARRAY rather than a stream so nothing in it touches stdin, but the redirect stays: the next
# caller to add a `while read` around this function should not have to know.
su_psql() {
  docker exec -i -e PGPASSWORD="$(secret brain-postgres-bootstrap-superuser)" \
    "$CONTAINER" psql -v ON_ERROR_STOP=1 -h 127.0.0.1 -U postgres "$@" </dev/null
}

# A store somebody PINNED is theirs, and the pin is the statement that it is. The budget suites
# treat a pinned name as opt-out of their own teardown (test-budget-wiring.sh:325), and run-all.sh
# calls this script in the same shell those variables live in, so honouring them here keeps one
# meaning for one word. It matters for the narrow case the age gate does not cover: a pinned name
# that is all digits, idle, and older than the window -- an operator's open post-mortem, which is
# the one store in the cluster it would be worst to take.
pinned() {
  local n="$1" p
  for p in "${BUDGET_WIRE_DB:-}" "${LANE_GATE_DB:-}" "${LANE_GATE_NOBUDGET_DB:-}" \
           "${BRAIN_PG_DB:-}" "${ENGINE_SCRATCH_DB:-}"; do
    [ -n "$p" ] && [ "$p" = "$n" ] && return 0
  done
  return 1
}

# `-F$'\t'` and not the default pipe: a database name cannot contain a tab here (the regex above
# allows letters, digits and underscore only), so the split cannot be ambiguous.
CANDIDATES="$(su_psql -tA -F$'\t' -c "
  SELECT d.datname,
         to_char((pg_stat_file('base/' || d.oid || '/PG_VERSION', true)).modification,
                 'YYYY-MM-DD HH24:MI'),
         round(extract(epoch FROM
               now() - (pg_stat_file('base/' || d.oid || '/PG_VERSION', true)).modification)
               / 3600.0, 1),
         pg_size_pretty(pg_database_size(d.datname))
  FROM pg_database d
  WHERE d.datname ~ '$GENERATED_RE'
    AND NOT EXISTS (SELECT 1 FROM pg_stat_activity a WHERE a.datname = d.datname)
    AND (pg_stat_file('base/' || d.oid || '/PG_VERSION', true)).modification
        < now() - (interval '1 hour' * $MIN_AGE_HOURS)
  ORDER BY 2" 2>&1)" || die "could not read pg_database from container '$CONTAINER':
$CANDIDATES"

# The same population without the age and connection gates, so the report can say what it SPARED.
# A sweeper that prints only what it took reads as 'there was nothing else here'.
TOTAL_GENERATED="$(su_psql -tAc \
  "SELECT count(*) FROM pg_database WHERE datname ~ '$GENERATED_RE'" 2>/dev/null | tr -d '[:space:]')"

# Read the whole result set BEFORE the loop that drops. See the note on su_psql: the loop body
# talks to the container, and a loop that is still reading from a stream while its body does that
# is one redirect away from processing a single row and calling it a clean sweep.
ROWS=()
while IFS= read -r line; do
  [ -n "$line" ] && ROWS+=("$line")
done <<<"$CANDIDATES"

REAPED=0; SPARED=0; REFUSED=0
for ((i = 0; i < ${#ROWS[@]}; i++)); do
  IFS=$'\t' read -r name born age size <<<"${ROWS[i]}"
  [ -n "$name" ] || continue

  # Gate 1, second reading. The SQL already applied this regex; bash applies it again to the text
  # that actually arrived, because everything below composes a DROP out of this string.
  if ! [[ "$name" =~ $GENERATED_RE ]]; then
    printf '  REFUSING %s: not a generated scratch name. This is a bug in this script, not a store to drop.\n' \
      "$name" >&2
    REFUSED=$((REFUSED + 1))
    continue
  fi
  if [ ${#NAMES[@]} -gt 0 ]; then
    printf '%s\n' "${NAMES[@]}" | grep -qxF "$name" || continue
  fi
  if pinned "$name"; then
    printf '  spared   %-42s pinned in this shell\n' "$name"
    SPARED=$((SPARED + 1))
    continue
  fi

  if [ "$MODE" = list ]; then
    printf '  would drop %-40s born %s  %sh  %s\n' "$name" "$born" "$age" "$size"
    REAPED=$((REAPED + 1))
    continue
  fi

  # No FORCE. If a run connected between the SELECT above and this line, Postgres refuses and says
  # so, which is the correct outcome: the store is in use after all.
  if err="$(su_psql -d postgres -c "DROP DATABASE $name" 2>&1)"; then
    printf '  dropped  %-42s born %s  %sh  %s\n' "$name" "$born" "$age" "$size"
    REAPED=$((REAPED + 1))
  else
    err="$(printf '%s' "$err" | grep -v '^$' | tail -1)"
    # Every lane's run-all.sh calls this, so two reapers reading one list is the normal case, not
    # an incident. `DROP DATABASE` and not `IF EXISTS` because a store that vanished between the
    # SELECT and here is worth SAYING, and saying who probably took it is worth more than silence.
    case "$err" in
      *"does not exist"*)
        printf '  gone     %-42s already dropped (a sibling reaper got there first)\n' "$name"
        REAPED=$((REAPED + 1)) ;;
      *)
        printf '  kept     %-42s DROP refused: %s\n' "$name" "$err"
        SPARED=$((SPARED + 1)) ;;
    esac
  fi
done

printf 'reap-scratch-dbs: %s %s, %s spared, of %s generated scratch store(s); min age %sh\n' \
  "$([ "$MODE" = list ] && echo 'would drop' || echo 'dropped')" \
  "$REAPED" "$SPARED" "${TOTAL_GENERATED:-?}" "$MIN_AGE_HOURS"

[ "$REFUSED" -eq 0 ] || exit 1
exit 0
