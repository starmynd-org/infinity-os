#!/usr/bin/env bash
# THE PROJECT HOLD. Task 0430 (Sprint P2), out of web/MUST-NOT-BUILD.md item 3.
#
# What it protects, in one line: A PROJECT THAT IS HELD IS NEVER HANDED OUT, AND NOTHING ELSE IS.
#
# The incident, and it is the operator's own, quoted in the overrule of MUST-NOT-BUILD item 3:
#
#     "there's certain projects where I kind of just wanted the AI to take a break with it. And
#      then I realized later that it worked on it for like eight hours with eight terminals and I
#      ran out of API tokens very quickly."
#
# The latched hold already existed at four grains -- fleet, agent, lane, work_item -- and
# `test-lane-budget-gate.sh` already proves the enforcement mechanism at the lane grain. THE GAP
# WAS THE GRAIN, NOT THE MECHANISM: lanes are kinds of work, so three projects running in one lane
# shared a single switch and a project spanning two lanes had none. This suite is about the grain,
# so its two load-bearing scenes are 4 and 5 and neither one is about braking at all:
#
#   scene 4  two projects in the SAME lane, one held -- the other must stay claimable.
#            If this passes with a LANE stop substituted for the project stop, the feature is a
#            lane switch wearing a project's name and this whole task built nothing.
#   scene 5  one project across TWO lanes, one stop -- both halves must go.
#            The other direction of the same claim.
#
# Everything else here is the standard shape borrowed from test-lane-budget-gate.sh: a control
# that proves the board would have handed the row out, a latch that does not clear itself, a
# resume that reopens the door, and a store one migration behind on which the claim must still
# work rather than raise.
#
# WHAT THIS SUITE DOES NOT PROVE, said here rather than left to be inferred from a green banner.
# It proves the MECHANISM over rows it writes itself. It proves nothing about live data, because
# there is none: `canonical_task` was measured empty on 2026-08-28 -- 0 of 345 rows on `brain`,
# and task 0429 re-measured 0 of 406 across three databases. Every project in every scene below is
# one this file created. The gate is armed and the world is empty; task 0429 fills the second one.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"            # engine/
REPO="$(dirname "$ROOT")"            # the runtime repo
SWARM="$ROOT/bin/swarm"
BUDGET="$REPO/budget/bin/budget"

# A per-run private store, for the reasons test-lane-budget-gate.sh gives at length: a suite that
# asserts on stored state cannot share a store with a suite that truncates it, and a FIXED private
# name isolates a suite from every other suite and not at all from a second copy of itself, while
# `scratch-db.sh create` opens with DROP ... WITH (FORCE).
if [ -n "${PROJECT_GATE_DB:-}" ]; then
  DB="$PROJECT_GATE_DB"; DB_PINNED=yes
else
  DB="brain_project_hold_$$"; DB_PINNED=no
fi
case "$DB" in
  brain)         echo "refusing to run against the live store database 'brain'." >&2; exit 1 ;;
  brain_scratch) echo "refusing to run against the shared scratch database: another lane's suite truncates it." >&2; exit 1 ;;
esac
export BRAIN_PG_DB="$DB"
export ENGINE_SCRATCH_DB="$DB"
# The runner exports this and `post` inherits it, which would make every task here a child of the
# live task that launched the suite. Clear it: this suite posts roots.
unset SWARM_PARENT_TASK

PASS=0
FAIL=0
ok()   { PASS=$((PASS + 1)); printf '  ok    %s\n' "$1"; }
bad()  { FAIL=$((FAIL + 1)); printf '  FAIL  %s\n' "$1"; [ -n "${2:-}" ] && printf '        %s\n' "$2"; }
check(){ if [ "$2" = "$3" ]; then ok "$1"; else bad "$1" "wanted [$3], got [$2]"; fi; }
# A premise that did not hold is not a brake that failed. The word moves the reader from "the hold
# is broken" to "the scene could not be armed", which are opposite investigations. Task 0322's
# lesson, borrowed whole.
precondition() {
  if [ "$2" = "$3" ]; then ok "$1"; else
    bad "$1" "PRECONDITION: wanted [$3], got [$2]. The arming above did not land, so nothing this scene asserts is evidence about the hold."
  fi
}

echo "test-project-budget-gate.sh"
echo

drop_private_stores() {
  [ "$DB_PINNED" = no ] || return 0
  if [ "$FAIL" -ne 0 ]; then
    printf '\n  post-mortem store kept (this run was red): BRAIN_PG_DB=%s\n' "$DB"
    return 0
  fi
  ENGINE_SCRATCH_DB="$DB" "$ROOT/bin/scratch-db.sh" drop >/dev/null 2>&1
  [ -n "${OLDDB:-}" ] && [ "${OLDDB_PINNED:-no}" = no ] &&
    ENGINE_SCRATCH_DB="$OLDDB" "$ROOT/bin/scratch-db.sh" drop >/dev/null 2>&1
  return 0
}
# Armed BEFORE `create` runs, not after: `create` DROPs, CREATEs and only THEN applies every
# migration, so "create failed" routinely means the database is on disk, half migrated. Task 0346.
trap drop_private_stores EXIT

if ! CREATE_OUT="$("$ROOT/bin/scratch-db.sh" create 2>&1)"; then
  bad "a private store was built at $DB" \
      "scratch-db.sh create failed: $(printf '%s' "$CREATE_OUT" | tail -3 | tr '\n' ' ')"
  echo; echo "$PASS passed, $FAIL failed"; exit 1
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"; drop_private_stores' EXIT

export PROJECT_GATE_REPO="$REPO"
sql_one() {
  python3 - "$@" <<'Q'
import os, sys
sys.path.insert(0, os.environ["PROJECT_GATE_REPO"])
import store
with store.read("runtime") as s:
    print(list(s.one(sys.argv[1], tuple(sys.argv[2:]) or None).values())[0])
Q
}

# ================================================================ the store is the right store
#
# ASKED BEFORE ANY SCENE, because every assertion below is about migration 45 and a store built
# without it would fail them all for one reason wearing nine costumes. The claim is DESIGNED to
# work on such a store (scene 8 proves that separately), so an unarmed store here does not go red
# loudly -- it goes green quietly, having held nothing. That is the shape this check exists
# against.
check "the builder applied migration 45" \
      "$(sql_one "SELECT count(*) AS n FROM brain.schema_migration WHERE version = 45")" "1"
check "and 'project' really is a budget scope on it" \
      "$(sql_one "SELECT count(*) AS n FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid
                   JOIN pg_namespace n ON n.oid = t.typnamespace
                  WHERE n.nspname = 'brain' AND t.typname = 'budget_scope'
                    AND e.enumlabel = 'project'")" "1"
if ! "$BUDGET" status >/dev/null 2>&1; then
  bad "the budget verbs can read this store" "budget status cannot read its tables"
  echo; echo "$PASS passed, $FAIL failed"; exit 1
fi
ok "the budget verbs can read this store"

# The agent claims from both lanes, and its lanes come from CONFIG rather than from `claim --lane`,
# because that is what the runner does: `swarm claim --agent PJ1` with no lane at all.
LA="pga"; LB="pgb"
cat > "$TMP/config.json" <<CONF
{
  "fleet": "swarm",
  "agents": [
    {"name": "PJ1", "role": "terminal", "lanes": ["$LA", "$LB"], "engine": "claude",
     "model": "stub", "permission_mode": "auto", "interval": 1, "workdir": "$TMP"}
  ]
}
CONF
export ENGINE_CONFIG="$TMP/config.json"

# A project ROW. Migration 44 ships the table and its trigger and no verb yet -- the write surface
# is a separate bus row (see this file's foot) -- so the fixture goes through a registered
# transition rather than a raw connection, which is the same idiom budget/test_budget.py uses for
# the rows its own verbs refuse to write. `store.read()` is READ ONLY at the database, so there is
# no other way in even from a test.
mkproject() {   # <slug> <title>
  PJ_SLUG="$1" PJ_TITLE="$2" python3 - <<'Q' >/dev/null 2>&1
import os, sys
sys.path.insert(0, os.environ["PROJECT_GATE_REPO"])
import store
@store.transition("test.create-a-project")
def _mk(ctx, *, slug, title):
    ctx.execute("INSERT INTO brain.project (slug, title) VALUES (%s, %s) "
                "ON CONFLICT (slug) DO NOTHING", (slug, title))
store.apply("test.create-a-project", actor="test",
            slug=os.environ["PJ_SLUG"], title=os.environ["PJ_TITLE"])
Q
}

# Move a project to one of migration 44's three resting states, or back. `state_reason` is NOT
# optional on a resting project (`project_rest_has_a_reason`), which is 44's rule and not this
# file's, so the fixture always supplies one.
rest_project() {   # <slug> <state> <reason>
  PJ_SLUG="$1" PJ_STATE="$2" PJ_WHY="$3" python3 - <<'Q' 2>&1 | tail -1
import os, sys
sys.path.insert(0, os.environ["PROJECT_GATE_REPO"])
import store
@store.transition("test.move-a-project")
def _mv(ctx, *, slug, state, why):
    ctx.execute("UPDATE brain.project SET state = %s, state_reason = %s WHERE slug = %s",
                (state, why, slug))
try:
    store.apply("test.move-a-project", actor="test", slug=os.environ["PJ_SLUG"],
                state=os.environ["PJ_STATE"], why=os.environ["PJ_WHY"])
except Exception as exc:
    print(f"refused: {str(exc).splitlines()[0][:90]}")
else:
    print("moved")
Q
}

# `--for-agents` since migration 26: `claim` hands out only rows somebody said the fleet may have.
# `--project` is the foreign key into brain.project that the hold actually reads; `--canonical-task`
# is a different fact and is deliberately NOT what any gate here consults.
post() {   # <lane> <project or ""> <title> [priority]
  if [ -n "$2" ]; then
    "$SWARM" post --lane "$1" --project "$2" --title "$3" --priority "${4:-3}" \
      --for-agents --workdir "$TMP" 2>/dev/null | awk '{print $1}'
  else
    "$SWARM" post --lane "$1" --title "$3" --priority "${4:-3}" \
      --for-agents --workdir "$TMP" 2>/dev/null | awk '{print $1}'
  fi
}
meta() { "$SWARM" show "$1" --json 2>/dev/null \
           | python3 -c "import json,sys; print(json.load(sys.stdin)['task']['$2'])" 2>/dev/null; }
# The ID of whatever `claim` handed out, or the empty string when it handed out nothing.
claim_id() {
  "$SWARM" claim --agent PJ1 --json 2>/dev/null \
    | python3 -c "import json,sys
try: print(json.load(sys.stdin)['id'])
except Exception: print('')" 2>/dev/null
}
held() { sql_one "SELECT count(*) AS n FROM brain.budget_open_stop
                   WHERE scope_type::text = 'project' AND scope_id = %s
                     AND kind = 'manual_stop'" "$1"; }
# What the DATABASE thinks a row's project is, through the one function every reader goes through.
row_project() { sql_one "SELECT coalesce(brain.work_item_project(w), '<null>') AS p
                           FROM brain.work_item w WHERE w.id = %s" "$1"; }

PA="apollo"; PB="borealis"; PC="cassini"; PD="dryad"
mkproject "$PA" "Apollo"; mkproject "$PB" "Borealis"
mkproject "$PC" "Cassini"; mkproject "$PD" "Dryad"
check "four project rows exist to hold anything at all" \
      "$(sql_one "SELECT count(*) AS n FROM brain.project")" "4"

# ================================================================ scene 0: the control
#
# NOT OPTIONAL, and it is the assertion that makes every later "claim did not hand it out" mean
# anything. A task that was unclaimable for some unrelated reason -- a dependency, the actor gate,
# a lane nobody claims -- reads EXACTLY like a task correctly withheld by the hold. This proves
# the board hands this row out first when nothing is held.
echo "  scene 0: the control -- with nothing held, the $PA task wins the board"

A0="$(post "$LA" "$PA" "control: project $PA, priority 1" 1)"
B0="$(post "$LB" "" "control: no project at all, priority 3" 3)"
check "0. the board is two tasks"                    "$([ -n "$A0" ] && [ -n "$B0" ] && echo yes)" "yes"
check "0. the $PA row's project resolves to $PA"     "$(row_project "$A0")" "$PA"
check "0. an unheld claim hands out the $PA task"    "$(claim_id)" "$A0"
"$SWARM" cancel "$A0" --agent PJ1 --reason "control complete" >/dev/null 2>&1

# ================================================================ scene 1: the definition of done
echo
echo "  scene 1: a hold on project $PA -- the task is never handed out, and nothing is spent"

A1="$(post "$LA" "$PA" "must never be claimed while $PA is held" 1)"
"$BUDGET" stop project "$PA" --reason "operator wants this one to rest" --by test >/dev/null 2>&1
precondition "1. PRECONDITION: the hold on $PA is open" "$(held "$PA")" "1"
check "1. the spend view has NO row for project $PA (a hold needs no ceiling and has no spend)" \
      "$(sql_one "SELECT count(*) AS n FROM brain.budget_state WHERE scope_type::text = 'project'")" "0"
check "1. the claim does not hand out the $PA task"  "$([ "$(claim_id)" = "$A1" ] && echo handed || echo withheld)" "withheld"

# The other half of the definition of done, and the reason the gate is IN the claim rather than a
# claim-then-release dance: nothing was released and no attempt was charged.
check "1. the $PA task is untouched: state"          "$(meta "$A1" state)"      "inbox"
check "1. the $PA task is untouched: attempts"       "$(meta "$A1" attempts)"   "0"
check "1. the $PA task is untouched: claimed_by"     "$(meta "$A1" claimed_by)" ""
check "1. no claim event was ever written for it" \
      "$(sql_one "SELECT count(*) AS n FROM brain.thread WHERE work_item_id = %s AND kind = 'claim'" "$A1")" "0"
check "1. nothing was spent against it" \
      "$(sql_one "SELECT count(*) AS n FROM brain.budget_charge WHERE work_item_id = %s" "$A1")" "0"

# ================================================================ scene 2: not a one-shot
echo
echo "  scene 2: the hold latches -- it is not one refusal, it is a door that stays shut"

for i in 1 2 3; do post "$LB" "" "drain filler $i" 3 >/dev/null; done
DRAINED=""
for i in 1 2 3 4; do DRAINED="$DRAINED$(claim_id) "; done
case "$DRAINED" in
  *"$A1"*) bad "2. four consecutive claims never touched the $PA task" "got: $DRAINED" ;;
  *)       ok  "2. four consecutive claims never touched the $PA task" ;;
esac
check "2. and it is STILL inbox/0" "$(meta "$A1" state)/$(meta "$A1" attempts)" "inbox/0"

# ================================================================ scene 3: THE GRAIN, half one
#
# THE SCENE THIS TASK EXISTS FOR. Project $PB is in lane $LA -- THE SAME LANE as held project $PA.
# If the implementation were a lane switch under a project's name, this row would be withheld too
# and this check is the only thing in the file that would notice.
echo
echo "  scene 3: THE GRAIN -- project $PB in the SAME lane $LA as held $PA stays claimable"

B3="$(post "$LA" "$PB" "same lane as the held project, different project" 1)"
precondition "3. PRECONDITION: both rows really are in lane $LA" \
      "$(meta "$A1" lane)/$(meta "$B3" lane)" "$LA/$LA"
precondition "3. PRECONDITION: and they really are in different projects" \
      "$(row_project "$A1")/$(row_project "$B3")" "$PA/$PB"
check "3. the claim hands out the $PB task"          "$(claim_id)" "$B3"
check "3. and the $PA task in that same lane is still withheld" \
      "$(meta "$A1" state)/$(meta "$A1" attempts)" "inbox/0"

# ================================================================ scene 4: THE GRAIN, half two
#
# The other direction. Project $PC has one row in lane $LA and one in lane $LB. ONE stop takes
# both. Under the old four scopes this project had no switch at all: stopping either lane would
# have taken unrelated work with it and stopping neither would have taken half the project.
echo
echo "  scene 4: THE GRAIN -- one hold on project $PC spanning lanes $LA and $LB takes BOTH"

C4A="$(post "$LA" "$PC" "project $PC, lane $LA" 1)"
C4B="$(post "$LB" "$PC" "project $PC, lane $LB" 1)"
precondition "4. PRECONDITION: the two $PC rows really are in different lanes" \
      "$(meta "$C4A" lane)/$(meta "$C4B" lane)" "$LA/$LB"
# The control for this scene specifically: before the hold, one of them comes out.
FIRST="$(claim_id)"
case "$FIRST" in
  "$C4A"|"$C4B") ok "4. CONTROL: before the hold, a $PC row is handed out" ;;
  *)             bad "4. CONTROL: before the hold, a $PC row is handed out" "claim gave: ${FIRST:-nothing}" ;;
esac
"$SWARM" cancel "$FIRST" --agent PJ1 --reason "control complete" >/dev/null 2>&1
REMAINING="$([ "$FIRST" = "$C4A" ] && echo "$C4B" || echo "$C4A")"
"$BUDGET" stop project "$PC" --reason "one switch for the whole project" --by test >/dev/null 2>&1
precondition "4. PRECONDITION: the hold on $PC is open" "$(held "$PC")" "1"
check "4. the remaining $PC row, in the OTHER lane, is now withheld too" \
      "$([ "$(claim_id)" = "$REMAINING" ] && echo handed || echo withheld)" "withheld"
check "4. and it is untouched" "$(meta "$REMAINING" state)/$(meta "$REMAINING" attempts)" "inbox/0"
check "4. neither lane was stopped to achieve it" \
      "$(sql_one "SELECT count(*) AS n FROM brain.budget_open_stop WHERE scope_type = 'lane'")" "0"

# ================================================================ scene 5: rows in no project
#
# `brain.work_item_project` returns NULL for a row with no canonical_task, which on 2026-08-28 was
# EVERY row in every database. A gate that read NULL as a match would stop the entire fleet the
# moment any project was held, and it would do it silently.
echo
echo "  scene 5: a row in NO project is untouched by any hold"

N5="$(post "$LB" "" "no project at all" 1)"
check "5. its project is NULL"                        "$(row_project "$N5")" "<null>"
check "5. two projects are held right now"            "$(sql_one "SELECT count(*) AS n FROM brain.budget_open_stop WHERE scope_type::text = 'project' AND kind = 'manual_stop'")" "2"
check "5. and the claim hands it out anyway"          "$(claim_id)" "$N5"

# ================================================================ scene 6: the door reopens
#
# A brake that is a one-way door is not a brake, it is a deletion. `budget resume` is the ONLY way
# out of a manual stop, and it has to actually work.
echo
echo "  scene 6: budget resume reopens it -- and it is the only thing that does"

# Resume is SCOPED, and this is the check that says so. `budget resume` UPDATEs every open
# manual_stop matching (scope_type, scope_id), so a resume that matched on scope_type alone would
# lift every project at once -- which nothing else in this file would notice, because scene 6's
# remaining assertions would all still pass.
"$BUDGET" resume project "$PC" --reason "wrong project on purpose" --by test >/dev/null 2>&1
check "6. resuming $PC does not lift the hold on $PA"  "$(held "$PA")" "1"
"$BUDGET" stop project "$PC" --reason "put it back" --by test >/dev/null 2>&1
"$BUDGET" resume project "$PA" --reason "break over" --by test >/dev/null 2>&1
precondition "6. PRECONDITION: the hold on $PA is closed" "$(held "$PA")" "0"
check "6. the $PA task is claimable again"            "$(claim_id)" "$A1"
check "6. and the resumed incident is recorded, not deleted" \
      "$(sql_one "SELECT count(*) AS n FROM brain.budget_incident
                   WHERE scope_type::text = 'project' AND scope_id = %s
                     AND kind = 'manual_stop' AND cleared_at IS NOT NULL" "$PA")" "1"
check "6. the $PC hold is untouched by resuming $PA"  "$(held "$PC")" "1"

# ================================================================ scene 7: no project CEILING
#
# brain.budget_charge carries agent, lane and work_item dimensions and NO project, so a project
# ceiling would meter 0.00 against the operator's limit in every window forever: a brake that
# cannot fire, displayed as a brake that is not firing. Refused in three places, and all three are
# checked here because each catches a different caller.
echo
echo "  scene 7: a project CEILING is refused three ways -- it would meter 0.00 forever"

# EXIT_USAGE (2), from argparse: `project` is not in `set`'s `choices`. Captured into a variable
# on its own line rather than read as `$?` inside the `check` call, so that adding anything at all
# between the two lines cannot silently change which command is being reported on.
"$BUDGET" set project "$PA" --limit 5 --period total --by test >/dev/null 2>&1
SET_RC=$?
check "7. the CLI refuses it (EXIT_USAGE, from argparse choices)" "$SET_RC" "2"
check "7. and no policy row exists for any project" \
      "$(sql_one "SELECT count(*) AS n FROM brain.budget_policy WHERE scope_type::text = 'project'")" "0"
check "7. the TRANSACTION refuses it, so every other caller of the verb gets the sentence too" \
      "$(python3 - <<'Q'
import os, sys
sys.path.insert(0, os.environ["PROJECT_GATE_REPO"])
import store, budget.transitions  # noqa: F401  (registers the verbs)
from budget.transitions import BudgetError
try:
    store.apply("budget set", actor="test", scope_type="project", scope_id="apollo",
                limit_usd="5.00", period="total", set_by="test")
except BudgetError as exc:
    print("refused" if "HOLD scope" in str(exc) else f"wrong-message: {exc}")
except Exception as exc:
    print(f"wrong-exception: {exc.__class__.__name__}")
else:
    print("ACCEPTED")
Q
)" "refused"
check "7. and the DATABASE refuses it, so no code path can store one however it is written" \
      "$(python3 - <<'Q'
import os, sys
sys.path.insert(0, os.environ["PROJECT_GATE_REPO"])
import store
@store.transition("test.write-a-project-ceiling-behind-the-verbs-back")
def _sneak(ctx):
    ctx.execute("INSERT INTO brain.budget_policy (scope_type, scope_id, period, limit_usd) "
                "VALUES ('project','apollo','total',5.00)")
try:
    store.apply("test.write-a-project-ceiling-behind-the-verbs-back", actor="test")
except Exception as exc:
    print("refused" if "budget_policy_no_project_ceiling" in str(exc)
          else f"wrong-error: {str(exc)[:80]}")
else:
    print("ACCEPTED")
Q
)" "refused"

# ================================================================ scene 8: one migration behind
#
# `claim` is what every agent calls to get work. Naming `brain.work_item_project` on a store that
# does not have it raises UndefinedFunction INSIDE that statement, which is not a degraded hold,
# it is the whole fleet unable to take work because a budget migration is one version behind.
# `_project_scope_present` is the probe; this scene is the proof that it is asked.
echo
echo "  scene 8: on a store WITHOUT migration 45, the claim still works rather than raising"

if [ -n "${PROJECT_GATE_OLD_DB:-}" ]; then
  OLDDB="$PROJECT_GATE_OLD_DB"; OLDDB_PINNED=yes
else
  OLDDB="brain_project_hold_old_$$"; OLDDB_PINNED=no
fi
if ENGINE_SCRATCH_DB="$OLDDB" SCRATCH_SCHEMA_DIRS="migrations" \
     "$ROOT/bin/scratch-db.sh" create >/dev/null 2>&1; then
  # A SUBSHELL, not a `VAR=x sql_one ...` prefix. `sql_one` is a shell function, and in bash
  # outside POSIX mode a variable assignment preceding a function call PERSISTS in the shell after
  # the call returns -- which would silently re-point every later read at the wrong database.
  OLD_HAS="$(export BRAIN_PG_DB="$OLDDB"; sql_one "SELECT count(*) AS n FROM pg_proc p
               JOIN pg_namespace n ON n.oid = p.pronamespace
              WHERE n.nspname = 'brain' AND p.proname = 'work_item_project'")"
  precondition "8. PRECONDITION: brain.work_item_project really is absent there" "$OLD_HAS" "0"
  OLD_TASK="$(BRAIN_PG_DB="$OLDDB" "$SWARM" post --lane "$LA" --title "no project scope here" \
                --canonical-task "$PA#9" --for-agents --workdir "$TMP" 2>/dev/null | awk '{print $1}')"
  OLD_OUT="$(BRAIN_PG_DB="$OLDDB" "$SWARM" claim --agent PJ1 --json 2>&1)"
  case "$OLD_OUT" in
    *"\"id\": \"$OLD_TASK\""*) ok "8. claim hands out the task and raises no UndefinedFunction" ;;
    *) bad "8. claim hands out the task and raises no UndefinedFunction" "claim said: $(printf '%s' "$OLD_OUT" | tr '\n' ' ' | cut -c1-160)" ;;
  esac
else
  bad "8. a second store was built at $OLDDB" "scratch-db.sh create failed"
fi

# ================================================================ scene 9: the board's arm
#
# THE OTHER HALF OF THE STOPPED-PROJECT SET, and the half migration 44 left for this row to
# decide. Every scene above holds a project with `budget stop`, the operator's latch. This one
# holds it by moving `brain.project.state` off `in_progress` and files NO budget incident at all,
# because that is what a board column moving will do.
#
# 44's comment on that column said "NOTHING READS THIS COLUMN YET. Setting a project to `hold`
# today records an intention and throttles no agent." This scene is that sentence stopping being
# true, and it is worth an assertion of its own that no incident was written: if the throttle
# secretly worked by filing a budget stop under the covers, one decision would have two records
# that drift the first time either is written without the other.
echo
echo "  scene 9: project state alone throttles -- a board column, no budget incident"

# TWO ROWS IN THE SAME PROJECT, not one row claimed and put back. `reopen` would work and would
# leave `attempts` at 1, which quietly weakens the "nothing was spent" assertion below into
# "nothing MORE was spent". A second untouched row keeps that reading clean.
D9C="$(post "$LA" "$PD" "the control: claimed while $PD is in_progress" 1)"
check "9. CONTROL: while $PD is in_progress the claim hands out a $PD row" "$(claim_id)" "$D9C"
D9="$(post "$LA" "$PD" "held by the BOARD, not by budget stop" 1)"
precondition "9. PRECONDITION: the move to hold was accepted" "$(rest_project "$PD" hold "operator wants it to rest")" "moved"
check "9. no budget incident exists for $PD -- this is the board's arm, not the latch" \
      "$(sql_one "SELECT count(*) AS n FROM brain.budget_incident WHERE scope_id = %s" "$PD")" "0"
check "9. and the claim withholds it anyway" \
      "$([ "$(claim_id)" = "$D9" ] && echo handed || echo withheld)" "withheld"
check "9. untouched: no attempt charged" "$(meta "$D9" state)/$(meta "$D9" attempts)" "inbox/0"

# ================================================================ scene 10: which states rest
#
# All three of the operator's resting words throttle, and `in_progress` does not. The predicate is
# `state <> 'in_progress'`, taken from migration 44's own `project_resting_idx ... WHERE state <>
# 'in_progress'` and its comment "the throttle reads the resting set".
#
# EACH STATE IS MEASURED, not inferred from `hold` working. A predicate written as
# `state = 'hold'` passes scene 9 and fails only here, and it is the likeliest wrong version of
# this feature -- `hold` is the word in the operator's quote.
echo
echo "  scene 10: ice and blocked rest too; in_progress does not"

for ST in ice blocked; do
  MOVED="$(rest_project "$PD" "$ST" "testing $ST")"
  precondition "10. PRECONDITION: $PD moved to $ST" "$MOVED" "moved"
  check "10. state '$ST' withholds the claim" \
        "$([ "$(claim_id)" = "$D9" ] && echo handed || echo withheld)" "withheld"
done

# THE CONTROL FOR SCENES 9 AND 10 TOGETHER, and without it all four of those verdicts are
# compatible with a $PD task that simply stopped being claimable for an unrelated reason -- the
# reopen above, the actor gate, a lane nobody claims. Returning the project to `in_progress` and
# watching the SAME row come out is the only thing that separates "withheld by the throttle" from
# "was never coming out anyway".
#
# It is also the only place this suite can test that direction at all. Migration 44's trigger
# gates the return to `in_progress` on `brain.current_human()`, deliberately -- stopping is
# available to everybody, starting again is a human login's to do -- so a fleet connection can
# only ever move a project TO rest. On a store with no human logins this fixture connects as
# brain_runtime and the move is refused, which is 44 working, not this suite failing. Hence the
# two-branch read below rather than a bare `check`: the control either runs and is asserted, or it
# declares itself NOT RUN and names the condition it DETECTED.
BACK="$(rest_project "$PD" in_progress "back to work")"
if [ "$BACK" = "moved" ]; then
  check "10. CONTROL: back in_progress, the SAME row is handed out again" "$(claim_id)" "$D9"
else
  echo "  NOT RUN  10. CONTROL: back in_progress, the SAME row is handed out again"
  echo "           detected: the return to in_progress was refused by migration 44's"
  echo "           project_state_is_a_control trigger, which requires brain.current_human()."
  echo "           This store has no human login. That is 44 working as designed and it is not"
  echo "           evidence about the throttle either way -- so it is NOT RUN, not a pass."
  echo "           Remedy, if you want this control: run with a human-login DSN. Migration 44's"
  echo "           own suite covers the trigger; what is uncovered here is only the round trip."
fi

# ================================================================ the banner
#
# THE DENOMINATOR. A verdict over an empty set is not a pass, and this file is one of the shapes
# that can produce one: every scene above depends on `scratch-db.sh create`, on `swarm post`, and
# on a config file. If any of those stops working, the scenes stop RUNNING rather than start
# failing, and "0 passed, 0 failed" exits 0 on a fleet whose hold is not enforced at all.
echo
if [ $((PASS + FAIL)) -eq 0 ]; then                             # DENOMINATOR
  echo "DENOMINATOR: 0 comparisons made. A verdict over an empty set is not a pass."
  exit 2
fi
echo "$PASS passed, $FAIL failed  ($((PASS + FAIL)) comparisons made)"
[ "$FAIL" -eq 0 ] || exit 1
