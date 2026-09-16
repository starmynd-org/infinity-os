#!/usr/bin/env bash
# The LANE ceiling, gated inside `claim` rather than one run later. Task 0122, out of 0110.
#
# What it protects, in one line: A LANE THAT MAY NOT SPEND IS NEVER HANDED OUT.
#
# The dispatch gate in `bin/swarm-run` has to run BEFORE the claim -- a claim you then release is
# a claim another agent raced for, and `release` is conditional on still holding it, so the loser
# of that race gets nothing while the queue looks like it moved. But the lane is exactly what the
# claim decides, so at gate time the runner does not know it. Task 0110 passed `--lane` only when
# the agent had exactly one configured lane. Every terminal in the shipped default config is
# `lanes: ["*"]` (`swarm_engine/config.py:DEFAULT_CONFIG`), so for the whole default fleet a lane
# ceiling was not gated at dispatch at all. It still braked, one run later, when `budget charge`
# pushed the meter over and the NEXT pre-claim check refused. A lag of one full run per agent.
#
# The fix is in `claim`, which is the only thing that knows the lane it is about to hand out.
#
# THE THREE WAYS TO GET THIS HALF RIGHT, each of which is a scene below:
#
#   1. Read only `brain.budget_state` and a MANUAL stop is invisible. `budget stop lane X` needs
#      no policy and has no spend, so it appears in neither the meter nor the spend view. That is
#      the mistake `budget/reads.py:open_stops` exists to document, and scene 3 is it.
#   2. Exclude any lane the budget knows about, and a SOFT ceiling (`--no-hard-stop`) stops a lane
#      that `budget.enforcer.evaluate` would have allowed. A ceiling crossed with hard stop off is
#      a measurement, not a brake. Scene 4.
#   3. Exclude the lane and forget it must come back. A raised ceiling and `budget resume` both
#      have to reopen the lane, or the brake is a one-way door. Scene 5.
#
# Scene 0 is the control and it is not optional. Without it every assertion below is vacuous: a
# lane A task that was never claimable for some unrelated reason reads exactly like a lane A task
# correctly refused. Scene 0 proves the board hands out lane A FIRST when nothing is braked.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"            # engine/
REPO="$(dirname "$ROOT")"            # the runtime repo
SWARM="$ROOT/bin/swarm"
BUDGET="$REPO/budget/bin/budget"
SCHEMA="$REPO/budget/schema/0003_budget.sql"

# Its own database, for the reason `test-budget-wiring.sh` gives at length: a suite that asserts
# on stored state cannot share a store with a suite that truncates it, and `test-claim-race.sh`
# truncates `brain_scratch` between races.
#
# AND A PER-RUN NAME (task 0321), for the reason test-budget-wiring.sh states at length: the fixed
# default isolated this suite from every other suite and not at all from a second copy of itself,
# and `scratch-db.sh create` opens with DROP ... WITH (FORCE).
#
# This suite fails DIFFERENTLY from that one, and worse. Its lanes are fixed names (lca..lce) and
# so are its charge refs (`--ref lane-gate-lcd`), and `budget charge` is idempotent on
# (source, source_ref). So a sibling run does not crash this one, it QUIETLY CHANGES ITS ANSWER:
# the sibling's identical charge dedupes yours away, and a charge whose charged_at predates your
# policy's effective_from falls outside your `total` window, so the spend reads 0 against a
# ceiling that is really breached. That is very likely what produced the lone failure in the
# 14:37 run-all on 2026-08-17 --
#   FAIL  4. the spend view says lane lcd is OVER its ceiling   wanted [True], got [False]
# -- with the very next check on the same row passing, so the policy was there and the $2.00
# charge was not. Standalone on a private name the same suite is 39 passed / 0 failed, twice.
# A per-run store fixes it at the root: dedupe is per-database, so nobody else holds your refs.
if [ -n "${LANE_GATE_DB:-}" ]; then
  DB="$LANE_GATE_DB"; DB_PINNED=yes
else
  DB="brain_lane_ceiling_$$"; DB_PINNED=no
fi
case "$DB" in
  brain)                echo "refusing to run against the live store database 'brain'." >&2; exit 1 ;;
  brain_scratch)        echo "refusing to run against the shared scratch database: another lane's suite truncates it." >&2; exit 1 ;;
  brain_budget_wire|brain_budget_wire_*)
                        echo "refusing to share test-budget-wiring.sh's database." >&2; exit 1 ;;
esac
export BRAIN_PG_DB="$DB"
export ENGINE_SCRATCH_DB="$DB"
# The runner exports this and `post` inherits it, which would make every task here a child of the
# live task that launched the suite. Clear it: this test posts roots.
unset SWARM_PARENT_TASK

PASS=0
FAIL=0
ok()   { PASS=$((PASS + 1)); printf '  ok    %s\n' "$1"; }
bad()  { FAIL=$((FAIL + 1)); printf '  FAIL  %s\n' "$1"; [ -n "${2:-}" ] && printf '        %s\n' "$2"; }
check(){ if [ "$2" = "$3" ]; then ok "$1"; else bad "$1" "wanted [$3], got [$2]"; fi; }

echo "test-lane-budget-gate.sh"
echo

# ================================================================ the store
#
# A RED run's stores are left behind to be read; a GREEN run's are dropped, because there is
# nothing in them to read and one abandoned database per run is how this Postgres came to hold
# ninety of them. Stores you named with LANE_GATE_DB / LANE_GATE_NOBUDGET_DB are never touched.
# NODB is only set once scene 7 is reached, hence the ${NODB:-} guard: this runs on every exit,
# including the early ones below and a Ctrl-C before scene 7 exists.
drop_private_stores() {
  [ "$DB_PINNED" = no ] || return 0
  if [ "$FAIL" -ne 0 ]; then
    printf '\n  post-mortem store kept (this run was red): BRAIN_PG_DB=%s\n' "$DB"
    return 0
  fi
  ENGINE_SCRATCH_DB="$DB" "$ROOT/bin/scratch-db.sh" drop >/dev/null 2>&1
  [ -n "${NODB:-}" ] && [ "${NODB_PINNED:-no}" = no ] &&
    ENGINE_SCRATCH_DB="$NODB" "$ROOT/bin/scratch-db.sh" drop >/dev/null 2>&1
  return 0
}

# ARMED BEFORE `create` RUNS, and not on the line after it. `create` DROPs, CREATEs, and only THEN
# applies every migration (`engine/bin/scratch-db.sh:293-296`, under that script's `set -euo
# pipefail`), so "create failed" routinely means "the database is on disk", half migrated or fully
# migrated depending on which file died. On the old ordering the `exit 1` below fired with no trap
# armed at all, and that database was left behind for good.
#
# MEASURED 2026-08-18, task 0346, against this file before the move: one throwaway migration forced
# to fail after every real one had applied (SCRATCH_SCHEMA_DIRS pointed at a directory outside the
# repo, so no lane's schema was touched) left `brain_lane_ceiling_4139610` in pg_database. The run
# printed `FAIL  a private store was built at brain_lane_ceiling_4139610` and stopped, which names
# the database and then tells the reader it was not built, so nobody goes and drops it. The census
# that afternoon read 101 `brain%` databases, against the 94 task 0321 counted the day before.
#
# `scratch-db.sh drop` is `DROP DATABASE IF EXISTS ... WITH (FORCE)` (:439), so arming this before
# the store exists costs nothing on the paths where `create` dies before it creates anything, and
# `drop_private_stores` already guards `${NODB:-}` for exactly this reason: it runs on exits that
# happen long before scene 7 names a second store.
trap drop_private_stores EXIT
# 2>&1 into a variable, not into /dev/null. Discarding stderr is what let a lost CREATE race --
# 'source database "template1" is being accessed by other users', which two lanes starting their
# run-alls together produce -- print here as "Is brain-postgres up?", pointing the reader at a
# container that was healthy. scratch-db.sh now retries that race; if it still fails, say what
# Postgres actually said.
if ! CREATE_OUT="$("$ROOT/bin/scratch-db.sh" create 2>&1)"; then
  bad "a private store was built at $DB" \
      "scratch-db.sh create failed: $(printf '%s' "$CREATE_OUT" | tail -3 | tr '\n' ' ')"
  echo; echo "$PASS passed, $FAIL failed"; exit 1
fi

su_psql() {
  docker exec -i -e PGPASSWORD="$(cat "${BRAIN_SECRET_DIR:-$HOME/.brain-postgres-secrets}/brain-postgres-bootstrap-superuser")" \
    "${BRAIN_PG_CONTAINER:-brain-postgres}" \
    psql -v ON_ERROR_STOP=1 -h 127.0.0.1 -U postgres -d "$1" -q "${@:2}"
}

su_query() { su_psql "$DB" -tAc "$1" 2>/dev/null | tr -d '[:space:]'; }

# ---------------------------------------------------------------- WHAT THE BUILDER LEFT
#
# ASKED BEFORE ANYTHING BELOW REPAIRS IT, and that ordering is the whole value of these two
# assertions. Task 0173.
#
# The history they encode. `scratch-db.sh` used to glob `migrations/` alone in FILENAME order, so
# `budget/schema/0003_budget.sql` was applied HERE, by this file, after the builder had finished.
# That put migration 3 AFTER `migrations/0005_lineage_resolution.sql`, and 5 adds
# `produced_by_ref` and `resolution_status` beside every `produced_by` THAT EXISTS WHEN IT RUNS,
# by iterating the catalog. So it swept a database on which `budget_policy`, `budget_charge` and
# `budget_incident` did not exist yet and covered nothing, while `brain.schema_migration` reported
# version 5 applied. `budget/transitions.py` names all three columns in its INSERTs, so
# `budget set` and `budget charge` died with UndefinedColumn while `budget status`, which only
# reads, stayed green: every scene below would have passed having braked nothing. That is why this
# file re-applied migration 5 by hand, and said in a comment that the real fix was filed elsewhere.
#
# It is no longer filed. Task 0212 fixed it at the builder, which is option (a) of the three that
# 0173 put to the lane: `scratch-db.sh` reads `migrations/`, `budget/schema/` and `queue/schema/`
# and orders them by the version each file's own INSERT records, so 3 lands before 5.
# `scratch-db.sh ledger` prints that order without connecting to anything. Migration 15 then made
# the invariant hold by CONSTRUCTION, with an event trigger that completes the pair inside the
# CREATE TABLE, so a lane that has never heard of the rule cannot write a fourth instance of this.
#
# Measured 2026-08-17, task 0173, on a database built by `scratch-db.sh create` and nothing else:
# all three budget tables carry the triple, `brain.lineage_column_drift` returns 0 rows, and
# `budget set` and `budget charge` both succeed. So these two check the BUILDER'S output, not the
# repair this file used to perform.
#
# WHAT THEY DO NOT CATCH, said plainly so nobody reads more into a green than is there. The two
# mechanisms are independent and either one alone is enough, so an assertion on the columns being
# PRESENT cannot tell you which one supplied them. Measured the same day on `brain_..._oldway`,
# built the pre-0212 way (`SCRATCH_SCHEMA_DIRS=migrations`, then 0003 applied by hand afterwards):
# psql printed `NOTICE: brain_lineage_columns: added produced_by_ref and resolution_status to
# brain.budget_policy` for all three tables, the drift view stayed empty, and `budget set`
# succeeded. Migration 15's trigger repaired the bad ordering inside the CREATE TABLE. If a future
# task needs to fence the ORDER rather than the outcome, the thing to assert on is
# `scratch-db.sh ledger`, which prints it without connecting to anything.
check "the builder left the lineage triple on all three budget tables" \
      "$(su_query "
        SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'brain' AND c.relkind = 'r'
           AND c.relname IN ('budget_policy','budget_charge','budget_incident')
           AND (SELECT count(*) FROM pg_attribute a
                 WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
                   AND a.attname IN ('produced_by','produced_by_ref','resolution_status')) = 3")" \
      "3"
check "and migration 5's drift view is empty on the store it built" \
      "$(su_query "SELECT count(*) FROM brain.lineage_column_drift")" "0"

# ---------------------------------------------------------------- the backstop, and why it stays
#
# Both applications are no-ops against a store the builder ordered correctly, which is exactly
# what the two checks above just measured rather than asserted. They stay because this suite must
# be able to run against a store somebody built another way, and because scenes 1 through 6 fail
# unreadably without the columns: UndefinedColumn inside a rolled-back transaction reads as "the
# brake filed no incident", which is the failure THIS FILE EXISTS TO CATCH firing for a reason
# that has nothing to do with the brake. Both files are idempotent by their own construction:
# 0003 is CREATE TABLE IF NOT EXISTS throughout and guards its own ledger version, and 0005 is
# ADD COLUMN IF NOT EXISTS inside a DO loop.
#
# They run AFTER the assertions, deliberately. A repair that ran first would make a builder
# regression invisible, which is the same shape as the ledger lie above: a guard that passes
# because the thing it guards was quietly fixed underneath it.
su_psql "$DB" -f - < "$SCHEMA" >/dev/null 2>&1
su_psql "$DB" -f - < "$REPO/migrations/0005_lineage_resolution.sql" >/dev/null 2>&1

if "$BUDGET" status >/dev/null 2>&1; then
  ok "a private store was built at $DB with migration 3 applied"
else
  bad "a private store was built at $DB with migration 3 applied" "budget status cannot read its tables"
  echo; echo "$PASS passed, $FAIL failed"; exit 1
fi
if "$BUDGET" set lane preflight-probe --limit 1 --period total --by test >/dev/null 2>&1 \
   && "$BUDGET" unset lane preflight-probe --period total --reason probe >/dev/null 2>&1; then
  ok "the budget verbs can write to it (the lineage triple resolved)"
else
  bad "the budget verbs can write to it (the lineage triple resolved)" \
      "budget set failed. Read 'scratch-db.sh ledger': migration 3 must land before migration 5."
  echo; echo "$PASS passed, $FAIL failed"; exit 1
fi

TMP="$(mktemp -d)"
# `trap ... EXIT` REPLACES, it does not add: this has to carry forward the store teardown
# installed beside `scratch-db.sh create`, or every run from this line down leaks a database
# and nothing says so.
trap 'rm -rf "$TMP"; drop_private_stores' EXIT

# THE AGENT IS TWO-LANE AND ITS LANES COME FROM CONFIG, not from `claim --lane`. That is the whole
# case: the runner calls `swarm claim --agent T1` with no lane at all, and the lane list is read
# out of config by `cmd_claim`. Passing `--lane a,b` on the command line would test a path the
# fleet never takes.
LA="lca"; LB="lcb"; LC="lcc"; LD="lcd"; LE="lce"
cat > "$TMP/config.json" <<CONF
{
  "fleet": "swarm",
  "agents": [
    {"name": "LC1", "role": "terminal", "lanes": ["$LA", "$LB"], "engine": "claude",
     "model": "stub", "permission_mode": "auto", "interval": 1, "workdir": "$TMP"},
    {"name": "LC2", "role": "terminal", "lanes": ["$LC", "$LD", "$LE"], "engine": "claude",
     "model": "stub", "permission_mode": "auto", "interval": 1, "workdir": "$TMP"}
  ]
}
CONF
export ENGINE_CONFIG="$TMP/config.json"

# `--for-agents` since migration 26: `claim` hands out only rows somebody said the fleet may
# have, and every row this suite posts is fleet work. See engine/tests/test_actor_gate.py.
post()  { "$SWARM" post --lane "$1" --title "$2" --priority "${3:-3}" --for-agents --workdir "$TMP" 2>/dev/null | awk '{print $1}'; }
meta()  { "$SWARM" show "$1" --json 2>/dev/null \
            | python3 -c "import json,sys; print(json.load(sys.stdin)['task']['$2'])" 2>/dev/null; }
# The lane of whatever `claim` handed out, or the empty string when it handed out nothing.
claim_lane() {
  "$SWARM" claim --agent "$1" --json 2>/dev/null \
    | python3 -c "import json,sys
try: print(json.load(sys.stdin)['lane'])
except Exception: print('')" 2>/dev/null
}
# The enforcer's own answer for a lane, so the two gates are COMPARED rather than each assumed.
gate_says() { "$BUDGET" check --lane "$1" --dry-run >/dev/null 2>&1; [ $? -eq 3 ] && echo stop || echo allow; }

# ---------------------------------------------------------------- PRECONDITIONS. Task 0322.
#
# Every scene below arms itself with `budget set` / `budget charge` / `budget stop`, and every one
# of those calls sends both its streams to /dev/null and drops its exit code. That is fine while
# they land and catastrophic when they do not, because of WHERE the resulting red appears: an
# unarmed lane is not braked, `claim` correctly hands it out, and the scene reports "the claim
# handed out a stopped lane" -- a production alarm about the brake, three lines below the command
# that actually failed and swallowed its own reason. Task 0301 was filed under exactly that
# sentence and cost a day before the cause turned out to be upstream of the brake entirely.
#
# So each scene now states its premise before asserting on it, and a premise that did not hold
# says PRECONDITION. That word is the whole point: it moves the failure from "the brake is broken"
# to "the scene could not be armed", which are opposite investigations.
#
# WHY THESE READ THE STORE AND NOT THE ARMING EXIT CODES. `budget charge` returns EXIT_STOPPED (3)
# whenever the charge it just made puts the scope over -- `budget/cli.py:180` -- which is precisely
# what every arming charge here is for. `if ! "$BUDGET" charge ...` would therefore go red on the
# HEALTHY path. A read of the state the gate itself reads has no such ambiguity, and because it is
# a read it cannot mask a real gate failure: it can only tell you the board was never set up.
precondition() {   # <label> <got> <want>
  if [ "$2" = "$3" ]; then ok "$1"; else
    bad "$1" "PRECONDITION: wanted [$3], got [$2]. The arming above did not land, so nothing this scene asserts is evidence about the brake."
  fi
}

# One row per ACTIVE policy (`budget_state` is `WHERE p.state = 'active'`), with the two booleans
# the enforcer decides from. Empty string when no policy covers the lane at all, which is the
# state a swallowed `budget set` leaves behind.
policy_says() {   # <lane> -> "<over_limit>/<stopping>/<hard_stop_enabled>", or "none"
  sql_one "SELECT coalesce(
             (SELECT bool_or(over_limit)::text || '/' || bool_or(stopping)::text || '/'
                  || bool_or(hard_stop_enabled)::text
                FROM brain.budget_state WHERE scope_type = 'lane' AND scope_id = %s), 'none') AS s" "$1"
}

# Open manual stops on a lane. A `budget stop` writes no policy and no charge, so `policy_says`
# cannot see it and this is the only read that can.
manual_stops_on() { sql_one "SELECT count(*) AS n FROM brain.budget_open_stop
                              WHERE scope_type = 'lane' AND scope_id = %s AND kind = 'manual_stop'" "$1"; }

sql_one() {
  python3 - "$@" <<'Q'
import os, sys
sys.path.insert(0, os.environ["LANE_GATE_REPO"])
import store
with store.read("runtime") as s:
    print(list(s.one(sys.argv[1], tuple(sys.argv[2:]) or None).values())[0])
Q
}
export LANE_GATE_REPO="$REPO"

# ================================================================ scene 0: the control
# Lane A sorts FIRST: priority 1 against lane B's priority 3, and `claim` orders by priority ASC
# before anything else. Every later assertion reads "claim did not hand out lane A", which is only
# evidence of a brake if lane A would otherwise have won. This is that proof.
echo
echo "  scene 0: the control -- with nothing braked, lane $LA wins the board"

A0="$(post "$LA" "control: lane A, priority 1" 1)"
B0="$(post "$LB" "control: lane B, priority 3" 3)"
check "0. the board is two tasks in two lanes"  "$([ -n "$A0" ] && [ -n "$B0" ] && echo yes)" "yes"
check "0. an unbraked claim hands out lane $LA" "$(claim_lane LC1)" "$LA"
"$SWARM" cancel "$A0" --agent LC1 --reason "control complete" >/dev/null 2>&1

# ================================================================ scene 1: THE DEFINITION OF DONE
echo
echo "  scene 1: a ceiling down on lane $LA, an agent whose lanes are [$LA, $LB]"

A1="$(post "$LA" "must never be claimed while lane $LA is stopped" 1)"
"$BUDGET" set lane "$LA" --limit 0.01 --period total --by test >/dev/null 2>&1
# Breached AFTER the ceiling is set, not before: a `total` window measures from the policy's
# `effective_from` on purpose, so raising a ceiling does not reset the meter -- which also means a
# charge made before the policy existed is invisible to it.
"$BUDGET" charge 1.00 --ref "lane-gate-$LA" --source manual --lane "$LA" \
  --note "put lane $LA over its ceiling" >/dev/null 2>&1
# The premise, before the three assertions that are only meaningful under it. If either arming
# call above was swallowed, lane $LA is an ordinary open lane: the enforcer allows it, the claim
# hands it out, and the two reds below say the brake no-opped.
precondition "1. PRECONDITION: lane $LA is over a HARD ceiling" "$(policy_says "$LA")" "true/true/true"

check "1. the enforcer stops lane $LA"     "$(gate_says "$LA")" "stop"
check "1. the enforcer allows lane $LB"    "$(gate_says "$LB")" "allow"
check "1. the claim hands out lane $LB"    "$(claim_lane LC1)"  "$LB"

# The other half of the definition of done, and the reason the fix is in `claim` and not in a
# claim-then-release dance: NOTHING WAS RELEASED AND NO ATTEMPT WAS CHARGED.
check "1. the lane $LA task is untouched: state"      "$(meta "$A1" state)"      "inbox"
check "1. the lane $LA task is untouched: attempts"   "$(meta "$A1" attempts)"   "0"
check "1. the lane $LA task is untouched: claimed_by" "$(meta "$A1" claimed_by)" ""
check "1. no claim event was ever written for it" \
      "$(sql_one "SELECT count(*) AS n FROM brain.thread WHERE work_item_id = %s AND kind = 'claim'" "$A1")" "0"
check "1. nothing was spent against it" \
      "$(sql_one "SELECT count(*) AS n FROM brain.budget_charge WHERE work_item_id = %s" "$A1")" "0"

echo
echo "  scene 2: it is not a one-shot -- lane $LA stays out for as long as the ceiling is down"
for i in 1 2 3; do post "$LB" "drain filler $i" 3 >/dev/null; done
DRAINED=""
for i in 1 2 3 4; do DRAINED="$DRAINED$(claim_lane LC1) "; done
case "$DRAINED" in
  *"$LA"*) bad "2. four consecutive claims never touched lane $LA" "got: $DRAINED" ;;
  *)       ok  "2. four consecutive claims never touched lane $LA" ;;
esac
check "2. and the lane $LA task is STILL inbox/0" \
      "$(meta "$A1" state)/$(meta "$A1" attempts)" "inbox/0"

# ================================================================ scene 3: the manual stop
# A `budget stop` needs no policy and has no spend, so it is in NEITHER the meter nor
# `brain.budget_state`. A gate that reads only the spend view lets this straight through, which is
# why `budget/reads.py` queries `covering_policies` and `open_stops` together.
echo
echo "  scene 3: a MANUAL stop on lane $LC -- no policy, no spend, invisible to the spend view"

C3="$(post "$LC" "lane $LC, stopped by hand" 1)"
"$BUDGET" stop lane "$LC" --reason "operator says no" --by test >/dev/null 2>&1
# A swallowed `budget stop` and a gate that ignores manual stops are INDISTINGUISHABLE from the
# assertions below -- both leave lane $LC claimable. This separates them.
precondition "3. PRECONDITION: the manual stop on lane $LC really is open" "$(manual_stops_on "$LC")" "1"
check "3. the spend view has NO row for lane $LC (so reading it alone would miss this)" \
      "$(sql_one "SELECT count(*) AS n FROM brain.budget_state WHERE scope_type = 'lane' AND scope_id = %s" "$LC")" "0"
check "3. the enforcer stops lane $LC anyway" "$(gate_says "$LC")" "stop"
D3="$(post "$LD" "lane $LD, the one that should come out" 3)"
check "3. the claim skips lane $LC and takes lane $LD" "$(claim_lane LC2)" "$LD"
check "3. the lane $LC task is untouched" "$(meta "$C3" state)/$(meta "$C3" attempts)" "inbox/0"

# ================================================================ scene 4: the soft ceiling
# `hard_stop_enabled = false` is a measurement, not a brake: `budget_state.stopping` already folds
# it in and `evaluate()` returns warn, not stop. Excluding the lane here would be a SECOND opinion
# about what a ceiling means, and two opinions is how a brake fires at a number the dashboard does
# not show.
echo
echo "  scene 4: a lane with a SOFT ceiling, and a lane with no ceiling at all, both stay open"

D4="$(post "$LD" "lane $LD, over a soft ceiling" 1)"
"$BUDGET" set lane "$LD" --limit 0.01 --period total --no-hard-stop --by test >/dev/null 2>&1
"$BUDGET" charge 2.00 --ref "lane-gate-$LD" --source manual --lane "$LD" \
  --note "way over a ceiling that does not brake" >/dev/null 2>&1
# THE WORST VACUUM IN THIS FILE, which is why it is named here and not left to the checks below.
# This scene's conclusion is that lane $LD stays OPEN -- and a lane with no ceiling on it at all is
# also open. So if either arming call was swallowed, "4. the enforcer allows lane $LD" and "4. so
# the claim hands lane $LD out" both go GREEN while proving nothing whatsoever about soft ceilings.
# The two `check`s immediately below would go red, but on a `bool_or` over an empty view they read
# "wanted True, got None", which names neither the cause nor the consequence.
precondition "4. PRECONDITION: lane $LD is over a ceiling whose hard stop is OFF" \
             "$(policy_says "$LD")" "true/false/false"
check "4. the spend view says lane $LD is OVER its ceiling" \
      "$(sql_one "SELECT bool_or(over_limit) AS b FROM brain.budget_state WHERE scope_type='lane' AND scope_id=%s" "$LD")" "True"
check "4. and says it is NOT stopping (hard stop is off)" \
      "$(sql_one "SELECT bool_or(stopping) AS b FROM brain.budget_state WHERE scope_type='lane' AND scope_id=%s" "$LD")" "False"
check "4. the enforcer allows lane $LD"                "$(gate_says "$LD")" "allow"
check "4. so the claim hands lane $LD out"             "$(claim_lane LC2)"  "$LD"
E4="$(post "$LE" "lane $LE, no ceiling was ever set" 1)"
check "4. a lane with no ceiling is not excluded"      "$(claim_lane LC2)"  "$LE"

# ---------------------------------------------------------------- 4b: kind discrimination
# `brain.budget_open_stop` carries BOTH `manual_stop` and `hard_stop`, and `evaluate()` blocks on
# the first and not the second. That asymmetry is deliberate: a hard stop is spend-derived, so it
# re-derives from `budget_state.stopping` for as long as it is true, and honouring the incident as
# well would keep a lane down after the spend that caused it left the window. Nothing else in this
# file can catch a gate that drops the `kind` filter, because `budget set` acknowledges open hard
# stops for the scope it touches -- so raising a ceiling clears the incident and the two readings
# converge. This scene files a hard stop against a lane with NO policy at all, which is the state
# they cannot converge in.
echo
echo "  scene 4b: an open hard_stop with no ceiling behind it must NOT stop the lane"
E4B="$(post "$LE" "lane $LE, an open hard_stop and no policy" 1)"
python3 - "$LE" <<'HARD'
import os, sys
sys.path.insert(0, os.environ["LANE_GATE_REPO"])
import budget            # noqa: F401  registers the verbs
import store
store.apply("budget stop", actor="test", kind="hard_stop", scope_type="lane",
            scope_id=sys.argv[1], spend_usd="9.99", limit_usd="1.0000",
            percent_used="999.00", action_taken="run_stopped", detected_by="charge",
            lane=sys.argv[1], reason="a spend-derived stop whose spend is no longer in window",
            by="test")
HARD
check "4b. the incident really is open on that lane" \
      "$(sql_one "SELECT count(*) AS n FROM brain.budget_open_stop WHERE scope_type='lane' AND scope_id=%s AND kind='hard_stop'" "$LE")" "1"
check "4b. the enforcer allows the lane anyway"  "$(gate_says "$LE")" "allow"
check "4b. so the claim hands it out"            "$(claim_lane LC2)"  "$LE"

# ================================================================ scene 5: it opens again
# A brake that cannot be released is a wall. Both routes out have to work: `budget resume` for a
# manual stop, and raising the ceiling for a spend-derived one.
echo
echo "  scene 5: both routes out of a stopped lane reopen it"

"$BUDGET" resume lane "$LC" --reason "operator cleared it" --by test >/dev/null 2>&1
check "5. budget resume reopens lane $LC"            "$(claim_lane LC2)"  "$LC"
"$BUDGET" set lane "$LA" --limit 100 --period total --by test >/dev/null 2>&1
check "5. the enforcer allows lane $LA again"        "$(gate_says "$LA")" "allow"
check "5. raising the ceiling reopens lane $LA"      "$(claim_lane LC1)"  "$LA"
check "5. and the task it had refused is the one it hands out" \
      "$(meta "$A1" state)/$(meta "$A1" attempts)" "active/1"

# ================================================================ scene 6: the parity sweep
# THE INVARIANT, stated once and checked against the enforcer rather than against this file's own
# idea of it. For every lane: `budget check --lane X` says stop, or the claim will hand X out.
# Two gates that disagree is the failure this scene exists to catch, and it is the one that would
# survive every scene above if the disagreement were in a case none of them covers.
echo
echo "  scene 6: the claim gate and budget check agree, lane by lane"

"$BUDGET" stop lane "$LB" --reason "sweep: put a second lane down" --by test >/dev/null 2>&1
# The sweep compares two gates lane by lane, and the comparison only bites in the STOP direction:
# with every lane open it degenerates into five copies of "allow, and the claim handed it out",
# which passes green against a claim gate that has no budget predicate in it at all. This stop is
# what puts a lane in the other branch, so a swallowed one silently deletes the scene.
precondition "6. PRECONDITION: at least one lane in the sweep really is stopped" \
             "$(manual_stops_on "$LB")" "1"
for L in "$LA" "$LB" "$LC" "$LD" "$LE"; do
  T="$(post "$L" "parity sweep probe for $L" 1)"
  WHO=LC1; case "$L" in "$LC"|"$LD"|"$LE") WHO=LC2 ;; esac
  GOT="$(claim_lane "$WHO")"
  SAYS="$(gate_says "$L")"
  if [ "$SAYS" = "stop" ]; then
    check "6. $L: enforcer says stop, so the claim must not hand it out" "$GOT" ""
  else
    check "6. $L: enforcer says allow, so the claim hands it out"        "$GOT" "$L"
  fi
  "$SWARM" cancel "$T" --force --agent sweep --reason "sweep complete" >/dev/null 2>&1
done
"$BUDGET" resume lane "$LB" --reason "sweep complete" --by test >/dev/null 2>&1

# ================================================================ scene 7: an un-migrated store
# `bin/swarm-run` supports a store that predates migration 3 on purpose: it says THE SPEND BRAKE
# IS NOT ARMED and dispatches anyway. Naming `brain.budget_state` unconditionally in the candidate
# query would raise UndefinedTable there, abort the transaction, and stop the ENTIRE FLEET
# claiming. A spend brake that takes the fleet down when it is not installed is worse than the lag
# it replaced, so the predicate is guarded on `to_regclass` -- the same guard
# `migrations/0013_thread_budget_kind.sql` uses for the same reason.
echo
echo "  scene 7: a store built from migrations/ alone, where the budget tables do not exist"

#
# SCRATCH_SCHEMA_DIRS=migrations is this scene's PREMISE, stated rather than inherited. It used to
# be inherited: `scratch-db.sh create` globbed `migrations/` alone and could not build the budget
# schema at ALL, so "a store where the budget tables do not exist" was the only store it knew how
# to make. Task 0212 fixed that -- the builder now applies every lane's schema in ledger order,
# because three suites were reading red or short against a scratch that had no queue or budget
# objects in it. This scene still needs the budget-less store, so it now asks for one. Without
# this variable the two assertions below would go red, and they would be right to: the store would
# have `brain.budget_state` in it and the scene would be proving nothing.
#
# Per-run, for the same reason the main store is (task 0321). This one is easy to overlook because
# it is built two hundred lines below the other and its name never appears at the top of the file,
# but it is `scratch-db.sh create` against a fixed default like any other, so a sibling run dropped
# it WITH (FORCE) mid-scene exactly the same way.
if [ -n "${LANE_GATE_NOBUDGET_DB:-}" ]; then
  NODB="$LANE_GATE_NOBUDGET_DB"; NODB_PINNED=yes
else
  NODB="brain_lane_ceiling_nobudget_$$"; NODB_PINNED=no
fi
if ENGINE_SCRATCH_DB="$NODB" SCRATCH_SCHEMA_DIRS="migrations" \
     "$ROOT/bin/scratch-db.sh" create >/dev/null 2>&1; then
  NB_STATUS="$(BRAIN_PG_DB="$NODB" "$BUDGET" status 2>&1 | head -1)"
  case "$NB_STATUS" in
    *"budget_state"*|*UndefinedTable*) ok "7. the budget tables really are absent there" ;;
    *) bad "7. the budget tables really are absent there" "budget status said: $NB_STATUS" ;;
  esac
  NB_TASK="$(BRAIN_PG_DB="$NODB" "$SWARM" post --lane "$LA" --title "no budget schema here" --for-agents --workdir "$TMP" 2>/dev/null | awk '{print $1}')"
  NB_OUT="$(BRAIN_PG_DB="$NODB" "$SWARM" claim --agent LC1 --json 2>&1)"
  case "$NB_OUT" in
    *"\"id\": \"$NB_TASK\""*) ok "7. claim still works, and did not raise UndefinedTable" ;;
    *) bad "7. claim still works, and did not raise UndefinedTable" "claim said: $NB_OUT" ;;
  esac
else
  bad "7. a second store was built at $NODB" "scratch-db.sh create failed"
fi

# ================================================================ NO SCENE 8, AND WHY NOT
#
# There was one. It posted 40 filler tasks, ran `EXPLAIN (ANALYZE)` on the claim statement, and
# asserted `loops=1` on the `Bitmap Heap Scan on budget_charge` -- a fence on `AS MATERIALIZED` in
# `_LANE_BUDGET_CTE`, whose whole job is to compute the stopped-lane set once per claim instead of
# once per candidate row.
#
# It was deleted after being measured three ways, because it asserted on the PLANNER and not on
# this code. The correlated-subquery draft of the predicate really was observed at `loops=60` on a
# 60-row inbox, which is what motivated the CTE. It was then re-measured on a second store, with a
# stopped lane and a 70-row inbox, and every form -- correlated, plain CTE, materialized CTE --
# came back `loops=1`. So the assertion passes or fails on table statistics, and a suite that goes
# red because Postgres changed its mind about a join order teaches the next reader to ignore it.
#
# The keyword stays, as the comment beside it says: cheap insurance that removes the planner's
# discretion. It is simply not something this file can honestly police.

echo
echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ] || exit 1
