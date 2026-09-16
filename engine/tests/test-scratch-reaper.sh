#!/usr/bin/env bash
# The reaper's blast radius, asserted. Task 0332.
#
# `engine/bin/reap-scratch-dbs.sh` is the only thing in this repo that drops a database it was not
# told to drop by name, so the only interesting question about it is what it will NOT take. Every
# scene below is a store that survives, except the two that prove it can take anything at all.
#
# THE STORES THIS SUITE BUILDS ARE BARE -- `CREATE DATABASE` and no migrations. That is faithful,
# not a shortcut: the reaper reads pg_database, pg_stat_activity and one file's mtime, and never
# opens a schema. It also keeps this suite off the migration path entirely, so a tree with a
# ledger collision (which `scratch-db.sh` refuses at every door) does not turn this red for a
# reason that has nothing to do with the reaper.
#
# THE STORE NAMES ARE PER RUN, and they are eight digits starting 98 or 99 for two reasons at once.
# Per-run because run-all.sh runs this suite in EVERY lane, and a fixed name here would have been
# the defect task 0321 had just spent a task removing from the two budget suites -- one lane's copy
# dropping another lane's store mid-scene, read as a reaper bug. Eight digits above Linux's 4194304
# pid ceiling because the suffix still has to be ALL DIGITS to sit inside the reaper's own reach
# (that is what the suite is here to exercise), and an all-digit name is exactly what a real
# `test-budget-wiring.sh` builds out of `$$`. 99000042 cannot be a pid; 9942 could.
#
# AND EVERY DESTRUCTIVE CALL NAMES ITS OWN STORES. `reap-scratch-dbs.sh reap name ...` NARROWS the
# candidate set and cannot widen it, so this suite can exercise the drop path at `--min-age 0`
# without reaching the four real generated stores that were sitting on this cluster when it was
# written -- somebody's red run kept those, and a test that swept them up while proving the sweeper
# is safe would be the joke telling itself.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"            # engine/
REAPER="$ROOT/bin/reap-scratch-dbs.sh"
CONTAINER="${BRAIN_PG_CONTAINER:-brain-postgres}"
SECRETS="${BRAIN_SECRET_DIR:-$HOME/.brain-postgres-secrets}"

# The reaper honours a pinned name from the environment. This suite asserts that in scene 6 with a
# variable it sets itself, so anything inherited from a parent shell would make scene 5's drop
# silently not happen. Clear them.
unset BUDGET_WIRE_DB LANE_GATE_DB LANE_GATE_NOBUDGET_DB BRAIN_PG_DB ENGINE_SCRATCH_DB

# Two numeric bases so this suite can hold four generated-shaped stores at once, both per run.
# The pid is zero-padded to the width of pid_max BEFORE the prefix, which is what makes the mapping
# from pid to name one-to-one: a modulo would have folded four live pids onto one suffix, and the
# whole reason these names are per-run is that two copies must not collide. `99` + seven digits is
# nine digits, so the result clears the 4194304 pid ceiling with the low pids too -- `9` + a bare
# `$$` of 42 would be 942, which is a perfectly ordinary pid and therefore a store a real
# test-budget-wiring.sh run could be holding right now.
SUF1="99$(printf '%07d' "$$")"
SUF2="98$(printf '%07d' "$$")"

PASS=0
FAIL=0
ok()   { PASS=$((PASS + 1)); printf '  ok    %s\n' "$1"; }
bad()  { FAIL=$((FAIL + 1)); printf '  FAIL  %s\n' "$1"; [ -n "${2:-}" ] && printf '        %s\n' "$2"; }
check(){ if [ "$2" = "$3" ]; then ok "$1"; else bad "$1" "wanted [$3], got [$2]"; fi; }

echo "test-scratch-reaper.sh"
# R-REAPER-01. AN AGE FLOOR IN THE ENVIRONMENT CHANGES WHAT AN ASSERTION MEANS, so say so rather
# than let a scene quietly measure somebody else's number. Every scene below now states its own
# `--min-age`, so this is a note and not a refusal -- but a reader who set the variable deserves to
# know it is not reaching these scenes, and a future scene that forgets to state its floor will be
# read against this line.
#
# Terminal 26 set it to 1000000 on a SHARED container, for a good reason: exercising the reaper's
# real default would have eaten other terminals' post-mortem stores, which are kept ON PURPOSE when
# a run goes red. Five reaper signatures then went red for a reason that was not about the reaper.
if [ -n "${SCRATCH_REAP_MIN_AGE_HOURS:-}" ]; then
  printf '  NOTE: SCRATCH_REAP_MIN_AGE_HOURS=%s is set in this environment and DOES NOT reach the\n' \
    "$SCRATCH_REAP_MIN_AGE_HOURS"
  printf '        scenes below: each states its own --min-age, so what they assert does not move\n'
  printf '        when somebody raises the floor to protect a shared container.\n'
fi
echo

su_psql() {
  docker exec -i -e PGPASSWORD="$(cat "$SECRETS/brain-postgres-bootstrap-superuser")" \
    "$CONTAINER" psql -tA -v ON_ERROR_STOP=1 -h 127.0.0.1 -U postgres "$@" </dev/null
}
exists() { su_psql -c "SELECT count(*) FROM pg_database WHERE datname='$1'" 2>/dev/null | tr -d '[:space:]'; }

# Every store this file makes, dropped whatever happens -- including the Ctrl-C that a suite which
# builds databases has to survive. WITH (FORCE) here and not in the reaper: this list is names the
# suite chose, which is the case where forcing is right.
MADE=()
cleanup() {
  local d
  for d in ${MADE[@]+"${MADE[@]}"}; do
    su_psql -d postgres -c "DROP DATABASE IF EXISTS $d WITH (FORCE)" >/dev/null 2>&1
  done
}
trap cleanup EXIT

# born: build a store and, when given an age, backdate the file the reaper reads the age off.
# PG_VERSION is written once at CREATE and never rewritten, so its mtime is the store's birth time
# -- measured, not assumed: a fresh database read back its create second and did not move after
# 100k rows and two checkpoints.
born() {
  local db="$1" when="${2:-}"
  su_psql -d postgres -c "DROP DATABASE IF EXISTS $db WITH (FORCE)" >/dev/null 2>&1
  su_psql -d postgres -c "CREATE DATABASE $db" >/dev/null 2>&1 || { bad "could not build $db"; return 1; }
  MADE+=("$db")
  [ -n "$when" ] || return 0
  local oid; oid="$(su_psql -c "SELECT oid FROM pg_database WHERE datname='$db'")"
  docker exec "$CONTAINER" sh -c "touch -d '$when' /var/lib/postgresql/data/base/$oid/PG_VERSION"
}

if bash -n "$REAPER" 2>/dev/null; then ok "the reaper parses"; else bad "the reaper parses"; fi
if [ -x "$REAPER" ]; then ok "the reaper is executable"; else bad "the reaper is executable"; fi

# ================================================================ static: the statement it issues
#
# The whole check-then-drop window rests on this one absence. `DROP DATABASE x` fails when a
# session is connected; `DROP DATABASE x WITH (FORCE)` severs that session and succeeds. A sweeper
# working from a list read seconds ago must take the first, so that a run which connected in the
# gap loses a drop instead of losing its connections mid-scene. scratch-db.sh keeps FORCE on
# purpose -- a caller naming one store means it -- and that is exactly the difference.
echo
echo "  the statement it issues"
if grep -q 'DROP DATABASE \$name"' "$REAPER"; then ok "1. it drops by name"; else bad "1. it drops by name"; fi
if grep -q 'DROP DATABASE.*FORCE' "$REAPER"; then
  bad "2. and NEVER WITH (FORCE)" "found a forced drop in the reaper; that reopens the race it closes"
else
  ok "2. and NEVER WITH (FORCE)"
fi

# ================================================================ the one value that reaches SQL
#
# --min-age is interpolated into the candidate query (Postgres has no bind parameter for the
# interval arithmetic that reads well here), so it is the only caller-supplied string in this
# script that ever reaches the database. It is validated against ^[0-9]+(.[0-9]+)?$ BEFORE the
# connection is opened, which is also why the refusal names the flag rather than the container.
echo
echo "  the only argument that reaches SQL is validated first"
for badv in '.' '-5' 'abc' '1.2.3' '24; DROP DATABASE brain'; do
  out="$("$REAPER" list --min-age "$badv" 2>&1)"; rc=$?
  if [ "$rc" -ne 0 ] && grep -q 'must be a non-negative number of hours' <<<"$out"; then
    ok "refused --min-age '$badv'"
  else
    bad "refused --min-age '$badv'" "rc=$rc, said: $(head -1 <<<"$out")"
  fi
done
check "and the live store is still there" "$(exists brain)" "1"

# ================================================================ scene 1: the hand-named survive
#
# The ~90 leftovers this reaper was written next to are hand-named -- brain_bw0301n,
# brain_t4_0200_control, brain_budget_wire_t4_0137 -- and several of them are somebody's open
# post-mortem. Retiring those is an operator's judgement call. The anchor that keeps the reaper out
# of it is that a generated suffix is ALL DIGITS, so this scene asks for the worst case: a store
# whose name starts with a generated prefix, three days old, idle, and NAMED ON THE COMMAND LINE at
# --min-age 0. Every gate the reaper has, opened as far as it opens.
echo
echo "  1. a hand-named store is out of reach"
HAND="brain_budget_wire_t2_0332hand_$$"
born "$HAND" '2026-01-02 03:04:05' || true
OUT="$("$REAPER" reap --min-age 0 "$HAND" 2>&1)"
check "1. a hand-named store is still there after an explicit reap" "$(exists "$HAND")" "1"
if grep -q "$HAND" <<<"$OUT"; then bad "2. and was not even named in the report" "$OUT"; else ok "2. and was not even named in the report"; fi

# The same for the three PRE-0321 fixed names, which is where a careless widening of the regex
# would land first: they are the stores the suites used before the per-run fix, and one of them
# was still on this cluster the day this was written.
echo
echo "  2. the pre-0321 fixed names are out of reach"
for legacy in brain_budget_wire brain_lane_ceiling brain_lane_ceiling_nobudget; do
  if [[ "$legacy" =~ ^brain_(budget_wire|lane_ceiling|lane_ceiling_nobudget)_[0-9]+$ ]]; then
    bad "$legacy is outside the reaper's regex"
  else
    ok "$legacy is outside the reaper's regex"
  fi
done

# ================================================================ scene 3: the age gate
echo
echo "  3. age"
YOUNG="brain_budget_wire_$SUF1"
OLD="brain_budget_wire_$SUF2"
born "$YOUNG" || true
born "$OLD" '2026-01-02 03:04:05' || true
# `--min-age 24` STATED, not inherited. This scene means "a store old enough is listed and a
# fresh one is not", and it used to take the floor from the environment, so
# SCRATCH_REAP_MIN_AGE_HOURS silently changed what it asserts. 24 reproduces the reaper's own
# default, and $OLD is backdated to January while $YOUNG is born seconds ago, so any floor
# between them discriminates. R-REAPER-01.
OUT="$("$REAPER" list --min-age 24 "$YOUNG" "$OLD" 2>&1)"
if grep -q "would drop $OLD" <<<"$OUT"; then ok "1. a store older than 24h is a candidate"; else bad "1. a store older than 24h is a candidate" "$OUT"; fi
if grep -q "$YOUNG" <<<"$OUT"; then bad "2. a store born minutes ago is not" "$OUT"; else ok "2. a store born minutes ago is not"; fi
check "3. list mode dropped neither of them" "$(exists "$OLD")" "1"

# ================================================================ scene 4: a live connection
#
# The gate that matters while the fleet is up. Postgres is asked who is connected, and the answer
# is taken as the last word however old the store looks.
echo
echo "  4. a live connection"
HELD="brain_lane_ceiling_$SUF1"
born "$HELD" '2026-01-02 03:04:05' || true
docker exec -i -e PGPASSWORD="$(cat "$SECRETS/brain-postgres-bootstrap-superuser")" \
  "$CONTAINER" psql -tA -h 127.0.0.1 -U postgres -d "$HELD" -c "SELECT pg_sleep(30)" >/dev/null 2>&1 &
HOLDER=$!
for _ in 1 2 3 4 5 6 7 8 9 10; do
  [ "$(su_psql -c "SELECT count(*) FROM pg_stat_activity WHERE datname='$HELD'")" = "1" ] && break
  sleep 1
done
check "1. a session is holding it" "$(su_psql -c "SELECT count(*) FROM pg_stat_activity WHERE datname='$HELD'")" "1"
OUT="$("$REAPER" reap --min-age 0 "$HELD" 2>&1)"
check "2. a three-day-old store with one session is NOT dropped" "$(exists "$HELD")" "1"
if kill -0 "$HOLDER" 2>/dev/null; then ok "3. and the session was not severed"; else bad "3. and the session was not severed"; fi
# Terminate the BACKEND, not just the client: killing `docker exec` leaves the server process
# running until its query returns, and a store with a ghost backend is not droppable in cleanup.
su_psql -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='$HELD'" >/dev/null 2>&1
kill "$HOLDER" 2>/dev/null
wait "$HOLDER" 2>/dev/null

# ================================================================ scene 5: it can actually drop
#
# Two scenes of "it refused" prove nothing without this one.
echo
echo "  5. and it does drop what it should"
# `--min-age 24` STATED, for the reason scene 4 gives. This is the only DESTRUCTIVE call in
# this suite that took its floor from the environment, and it is the one Terminal 26 watched
# go red under SCRATCH_REAP_MIN_AGE_HOURS=1000000: nothing was old enough, nothing was
# dropped, and the scene reported a reaper defect. The reaper was right and the scene was
# measuring a floor somebody else had set.
OUT="$("$REAPER" reap --min-age 24 "$OLD" 2>&1)"
check "1. the old generated store is gone" "$(exists "$OLD")" "0"
if grep -q "dropped  $OLD" <<<"$OUT"; then ok "2. and said so"; else bad "2. and said so" "$OUT"; fi
check "3. the young one beside it is untouched" "$(exists "$YOUNG")" "1"

# ================================================================ scene 6: a pinned store is mine
#
# A name in BUDGET_WIRE_DB / LANE_GATE_DB is how the budget suites are told a store belongs to the
# person who named it, and their own teardown already honours it. This closes the narrow case the
# age gate does not: an operator's open post-mortem, pinned, idle, and older than the window.
echo
echo "  6. a pinned store is spared"
PIN="brain_lane_ceiling_nobudget_$SUF1"
born "$PIN" '2026-01-02 03:04:05' || true
OUT="$(LANE_GATE_DB="$PIN" "$REAPER" reap --min-age 0 "$PIN" 2>&1)"
check "1. still there" "$(exists "$PIN")" "1"
if grep -q "spared   $PIN" <<<"$OUT"; then ok "2. and the report says why"; else bad "2. and the report says why" "$OUT"; fi

# ================================================================ scene 7: the cluster-wide sweep
#
# LIST ONLY, and deliberately with no name filter and no age floor: this is the assertion that on
# THIS cluster, right now, with every gate open, the reaper's reach contains nothing but generated
# names. It is the scene that would have caught a widened regex on a machine holding ninety
# hand-named stores. It drops nothing, so it is safe to run in a live fleet.
echo
echo "  7. with every gate open, the reach is still only generated names"
STRAY=0
while IFS= read -r line; do
  n="$(sed -n 's/^ *would drop \([^ ]*\).*/\1/p' <<<"$line")"
  [ -n "$n" ] || continue
  [[ "$n" =~ ^brain_(budget_wire|lane_ceiling|lane_ceiling_nobudget)_[0-9]+$ ]] || {
    printf '        in reach and NOT generated: %s\n' "$n"; STRAY=$((STRAY + 1)); }
done < <("$REAPER" list --min-age 0 2>&1)
check "1. no store outside the regex is ever in reach" "$STRAY" "0"

echo
echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
