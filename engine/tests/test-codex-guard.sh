#!/usr/bin/env bash
# The spend brake on the CODEX engine, wired and live. Task 0167.
#
# WHY THIS IS A SEPARATE FILE FROM test-budget-wiring.sh, which already runs five live scenes:
# every one of those scenes runs `engine: claude`. The codex branch of `run_engine` is a second
# engine path with its own launch, its own guard, and until 0167 no guard at all -- and it was
# left unguarded DELIBERATELY, for a reason that was correct at the time:
#
#   `budget halt` matches first-party evidence on run_id OR session_id and nothing else. The
#   runner learned the session id by reading the engine's own stream-json `init` event. Codex
#   writes no stream-json. So a guard on that branch would have filed a stop nobody could match
#   to its run, reconciliation would have read it as an ordinary task failure, and the lane would
#   have been charged an attempt it did not earn. A STOP THAT CANNOT BE MATCHED IS WORSE THAN NO
#   STOP -- it converts "the operator ran out of money" into "this agent is failing", which is
#   the 2026-08-14 shape: a real condition, misfiled, and retried until it burns the ladder.
#
# 0167 gave the stop a key that exists at launch: `swarm run-start` prints the `brain.run` row id
# and the runner hands it to `budget guard --run` and `budget halt --run`. This file is the proof
# that the key actually arrives, because the ways it can fail to are quiet ones. The defect this
# file was written against was exactly that: `$RUN_ROW_ID` was READ in three places and ASSIGNED
# in none, so every `${RUN_ROW_ID:+--run ...}` expanded to nothing, the codex guard's `[ -n ... ]`
# test was never true, and the branch looked fully wired while running exactly as unguarded as
# before. Nothing errored. A static grep for the flag passed.
#
# So the assertion that matters here is not "the guard fired". It is:
#
#     THE INCIDENT CARRIES A run_id AND AN EMPTY session_id,
#
# which is the shape only the codex path can produce, and which is what makes the difference
# between `blocked` and `inbox`/attempts-1 on the row afterwards.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"            # engine/
REPO="$(dirname "$ROOT")"            # the runtime repo
RUNNER="$ROOT/bin/swarm-run"
SWARM="$ROOT/bin/swarm"
BUDGET="$REPO/budget/bin/budget"

# ITS OWN DATABASE, for the reason test-budget-wiring.sh:50-56 records in blood: a suite that
# asserts on stored state cannot share a store with a suite that truncates it. Measured again
# while writing this file -- `brain_scratch` was dropped and rebuilt by another lane mid-probe.
#
# AND A PER-RUN NAME (task 0331, the same repair task 0321 made to the other two budget suites).
# The default was the fixed `brain_codex_guard`, which isolated this suite from every OTHER suite
# and from nothing else -- least of all from a second copy of ITSELF. The store is built by
# `scratch-db.sh create`, whose first statement is `DROP DATABASE IF EXISTS ... WITH (FORCE)`
# (`engine/bin/scratch-db.sh:144`), so the later copy drops the earlier copy's store out from under
# it and severs its connections mid-scene. The victim is loud rather than subtle -- assertions come
# back EMPTY over psycopg2 UndefinedTable and 'database ... does not exist' -- but loud in a way
# that reads like a code failure to anyone who does not know a sibling was running.
#
# HOW THE EXPOSURE HERE DIFFERS FROM 0321'S, stated so nobody re-derives it: this file is NOT in
# `engine/tests/run-all.sh`'s suite list (checked 2026-08-17: run-all names test-budget-wiring.sh
# and test-lane-budget-gate.sh and not this one), so it is not being launched concurrently by every
# lane's run-all the way those two were. The collision here needs two operators, or two lanes,
# hand-running this file at once. That makes it lower severity and not a different bug, and it is
# the reason this was left out of 0321's scope rather than folded into it.
#
# $$ is this shell's pid, so two copies cannot collide however they are launched. Pin CODEX_GUARD_DB
# to name the store yourself; a store you named is yours, and the teardown never touches it.
if [ -n "${CODEX_GUARD_DB:-}" ]; then
  DB="$CODEX_GUARD_DB"; DB_PINNED=yes
else
  DB="brain_codex_guard_$$"; DB_PINNED=no
fi
# The refusal list, widened with the name. While the default was fixed, `brain_scratch` and the two
# sibling budget stores were unreachable from here; a caller-supplied pin can name any of them, and
# every one of them is a store this file would DROP WITH (FORCE) on the way in.
case "$DB" in
  brain|brain_prod)     echo "refusing to run against the live store database '$DB'." >&2; exit 2 ;;
  brain_scratch)        echo "refusing to run against the shared scratch database: another lane's suite truncates it." >&2; exit 2 ;;
  brain_budget_wire|brain_budget_wire_*)
                        echo "refusing to share test-budget-wiring.sh's database." >&2; exit 2 ;;
  brain_lane_ceiling|brain_lane_ceiling_*)
                        echo "refusing to share test-lane-budget-gate.sh's database." >&2; exit 2 ;;
esac
export ENGINE_SCRATCH_DB="$DB"
export BRAIN_PG_DB="$DB"
# The runner exports this for the agent it launches; inherited here it makes every `post` below
# try to parent onto a task id that does not exist in this scratch store.
unset SWARM_PARENT_TASK

PASS=0
FAIL=0
ok()   { PASS=$((PASS + 1)); printf '  ok    %s\n' "$1"; }
bad()  { FAIL=$((FAIL + 1)); printf '  FAIL  %s\n' "$1"; [ -n "${2:-}" ] && printf '        %s\n' "$2"; }
check(){ if [ "$2" = "$3" ]; then ok "$1"; else bad "$1" "wanted [$3], got [$2]"; fi; }

echo "test-codex-guard.sh"
echo

# ================================================================ static
echo "  static: the key is captured, then used"

# THE CAPTURE, ASSERTED APART FROM THE USES. See the header: the half-wired state passed a grep
# for `--run` and was completely dead. These three must all hold, and the first is the one that
# was missing.
if grep -q 'RUN_ROW_ID="\$("\$SWARM" run-start' "$RUNNER"; then
  ok "run-start's printed id is captured into \$RUN_ROW_ID"
else
  bad "run-start's printed id is captured into \$RUN_ROW_ID" \
      "without this every \${RUN_ROW_ID:+--run ...} expands to nothing and the branch is dead"
fi
if grep -q 'guard --pid "\$ENGINE_PID" --run "\$RUN_ROW_ID"' "$RUNNER"; then
  ok "the codex branch passes it to budget guard"
else
  bad "the codex branch passes it to budget guard"
fi
if grep -q 'halt --session "\${SID:-}" \${RUN_ROW_ID:+--run' "$RUNNER"; then
  ok "reconciliation passes it to budget halt"
else
  bad "reconciliation passes it to budget halt" \
      "SID is read inside an 'if CFG_ENGINE != codex' block, so on codex --session is ALWAYS empty"
fi

# `run-start` must PRINT the id and not merely return it. A verb whose id is only in its --json
# payload cannot be read by `RUN_ROW_ID="$(...)"`.
if grep -q 'print(r\["run_id"\])' "$ROOT/swarm_engine/cli.py"; then
  ok "swarm run-start prints the bare run row id on stdout"
else
  bad "swarm run-start prints the bare run row id on stdout"
fi

echo

# ================================================================ live
echo "  live: the real runner, a stub codex, and the store"

# What survives the run, now that the name is per-run (see the DB comment at the top): a RED run's
# store is left behind, because reading the rows is how you find out what went wrong -- which is
# exactly why task 0321 did not simply drop on every exit. A GREEN run's store is dropped, because
# there is nothing in it to read and one abandoned database per run is how ninety of them ended up
# in this Postgres. A store you named yourself with CODEX_GUARD_DB is never touched either way.
drop_private_store() {
  [ "$DB_PINNED" = no ] || return 0
  if [ "$FAIL" -ne 0 ]; then
    printf '\n  post-mortem store kept (this run was red): BRAIN_PG_DB=%s\n' "$DB"
    return 0
  fi
  ENGINE_SCRATCH_DB="$DB" "$ROOT/bin/scratch-db.sh" drop >/dev/null 2>&1
  return 0
}

# ARMED BEFORE `create` RUNS, and not on the line after it. `create` DROPs, CREATEs, and only THEN
# applies every migration (`engine/bin/scratch-db.sh:144-183`), so "create failed" routinely means
# "the database is on disk and half migrated". Arming the trap on the success side leaves that one
# orphaned and unnamed, which is the disease this task is about rather than a second one.
#
# MEASURED 2026-08-17 while verifying this very fix: two copies of this file launched together, and
# the second died mid-`create` on `psql:<stdin>:38: ERROR: tuple concurrently updated` -- migration
# 0002's cluster-wide `ALTER ROLE brain_owner WITH PASSWORD`, which two concurrent creates write to
# the same pg_authid tuple. It exited leaving `brain_codex_guard_2562920` behind with nothing on
# screen to say so. From here that same run prints the name it kept.
#
# `scratch-db.sh drop` is `DROP DATABASE IF EXISTS` (:323), so arming this before the store exists
# costs nothing on the paths where `create` dies before creating anything.
trap drop_private_store EXIT
# 2>&1 into a variable and NOT into /dev/null. The discarded stderr is what made a lost CREATE
# race -- 'source database "template1" is being accessed by other users', which is what two lanes
# starting together produce -- print as "Is brain-postgres up?", sending the reader to a container
# that was healthy. scratch-db.sh now retries that race; if it still fails, the sentence Postgres
# actually said belongs on screen. It does NOT retry the role race above, so this still fires.
if ! CREATE_OUT="$("$ROOT/bin/scratch-db.sh" create 2>&1)"; then
  bad "a private store was built at $DB" \
      "scratch-db.sh create failed: $(printf '%s' "$CREATE_OUT" | tail -3 | tr '\n' ' ')"
  echo; echo "$PASS passed, $FAIL failed"; exit 1
fi
if "$SWARM" status >/dev/null 2>&1; then
  ok "a private store was built at $DB"
else
  bad "a private store was built at $DB" "swarm status failed against BRAIN_PG_DB=$DB"
  echo; echo "$PASS passed, $FAIL failed"; exit 1
fi

TMP="$(mktemp -d)"
AGENT="cg$$"
LANE="cg$$"
cleanup_live() {
  "$BUDGET" unset agent "$AGENT" --period day --reason "test teardown" >/dev/null 2>&1
  rm -rf "$TMP"
  # This trap REPLACES the one installed beside `scratch-db.sh create`, so it has to carry the store
  # teardown forward. A `trap ... EXIT` is not additive, and forgetting that here would leak a
  # database on every run from this line down -- silently, since nothing would say so.
  drop_private_store
}
trap cleanup_live EXIT

mkdir -p "$TMP/bin" "$TMP/acct1" "$TMP/acct2" "$TMP/work" "$TMP/state" "$TMP/out"

cat > "$TMP/config.json" <<CONF
{
  "fleet": "swarm",
  "agents": [
    {"name": "$AGENT", "role": "terminal", "lanes": ["$LANE"], "engine": "codex",
     "model": "stub", "permission_mode": "auto", "interval": 1, "task_timeout": 120,
     "config_dirs": ["$TMP/acct1", "$TMP/acct2"], "workdir": "$TMP/work"}
  ]
}
CONF

# The stub codex. It writes PLAIN TEXT, never stream-json, which is the whole point: there is no
# `init` event, so no session id will ever exist for this run and the run row id is the only key.
#
# Like scene 4 of test-budget-wiring.sh it does not touch the brake to stop itself. A SIBLING
# terminal charges past the ceiling while this run is live -- the fleet case, where what takes a
# run over is usually not that run -- and then it sleeps far past its own task_timeout without
# reporting. If the guard never reaches it, this takes the full 120s and the runner files an
# ordinary unreported failure: `inbox`, attempts 1. That is what the assertions below separate
# `blocked` from.
cat > "$TMP/bin/codex" <<'STUB'
#!/usr/bin/env python3
import os, sys, time

try:
    sys.stdin.read()          # drain the prompt; the runner feeds it on stdin
except Exception:
    pass

# Plain text on stdout, the way codex actually talks. No session id anywhere in it.
print("codex stub: starting work")
sys.stdout.flush()

sys.path.insert(0, os.environ["STUB_REPO"])
import budget                                     # noqa: F401  (registers the verbs)
import store
store.apply("budget charge", actor="sibling-terminal", usd="5.00",
            source_ref="sibling-codex-" + str(os.getpid()), source="manual",
            agent=os.environ["STUB_AGENT"],
            note="another terminal finished and charged while this codex run was live")

print("codex stub: the meter moved under me; sleeping past my own timeout")
sys.stdout.flush()
time.sleep(600)
sys.exit(0)                                       # never reached: something must stop this
STUB
chmod +x "$TMP/bin/codex"

export ENGINE_HOME="$TMP/state"
export ENGINE_CONFIG="$TMP/config.json"
export PATH="$TMP/bin:$PATH"
export SWARM_ONCE=1
export STUB_REPO="$REPO"
export STUB_AGENT="$AGENT"

meta() {
  "$SWARM" show "$1" --json 2>/dev/null \
    | python3 -c "import json,sys; print(json.load(sys.stdin)['task']['$2'])" 2>/dev/null
}

# THE ASSERTION THIS FILE EXISTS FOR. Not "an incident was filed" but "the incident carries the
# key that only the run row can supply, and no session id at all".
codex_stop_shape() {
  python3 - "$1" <<'Q'
import os, sys
sys.path.insert(0, os.environ["STUB_REPO"])
import store
with store.read("runtime") as s:
    r = s.one("SELECT count(*) AS n FROM brain.budget_incident "
              "WHERE kind = 'hard_stop' AND detected_by = 'sweep' "
              "  AND action_taken = 'run_stopped' AND work_item_id = %s "
              "  AND run_id IS NOT NULL AND coalesce(session_id,'') = ''", (sys.argv[1],))
print(r["n"])
Q
}

# And that the run id it carries is THIS task's run row, not any row.
codex_stop_matches_run() {
  python3 - "$1" <<'Q'
import os, sys
sys.path.insert(0, os.environ["STUB_REPO"])
import store
with store.read("runtime") as s:
    r = s.one("SELECT count(*) AS n FROM brain.budget_incident i "
              "  JOIN brain.run rn ON rn.id = i.run_id "
              " WHERE i.work_item_id = %s AND rn.work_item_id = %s "
              "   AND i.kind = 'hard_stop' AND i.detected_by = 'sweep'", (sys.argv[1], sys.argv[1]))
print(r["n"])
Q
}

# ---------------------------------------------------------------- PRECONDITION. Task 0326.
#
# The one scene below arms its own board with `budget set`, and that call sends both streams to
# /dev/null and drops its exit code. That is fine while it lands and it is a day of somebody's
# life when it does not, because of WHERE the resulting red appears. Swallow the arming and there
# is no ceiling for the sibling's $5.00 charge to cross, `budget guard` has nothing to trip on,
# the run sleeps out its 180s `timeout`, and the four checks below report no stop incident,
# state != blocked, and the elapsed assertion red -- whose plain reading is "the codex guard never
# fired", a production alarm, three lines below the command that actually failed with its reason
# in /dev/null. Task 0301 was filed under exactly that shape and cost a day before the cause
# turned out to be upstream of the brake entirely. This is the same repair 0322 made to the other
# two budget suites; this file was untracked then and so was not in its scope.
#
# WHY THIS READS THE STORE AND NOT THE ARMING EXIT CODE. `budget charge` returns EXIT_STOPPED (3)
# whenever the charge it just made puts the scope over -- `budget/cli.py:180` -- so an `if !` on
# an arming call goes red on the HEALTHY path. A read of the state the enforcer itself decides
# from has no such ambiguity, and because it is a read it cannot mask a real guard failure: it can
# only tell you the board was never set up.
sql_one() {
  python3 - "$@" <<'Q'
import os, sys
sys.path.insert(0, os.environ["STUB_REPO"])
import store
with store.read("runtime") as s:
    print(list(s.one(sys.argv[1], tuple(sys.argv[2:]) or None).values())[0])
Q
}

# One row per ACTIVE policy on this agent for one period (`budget_state` is `WHERE state='active'`),
# reduced to the two booleans the enforcer decides from. `none` when no such policy exists at all,
# which is what a swallowed `budget set` leaves behind. Not-yet-breached is asserted too: a stale
# policy already over its ceiling would let the guard fire before the sibling ever charged, which
# is a different scene from the one this file claims to run.
policy_says() {   # <period> -> "<over_limit>/<stopping>", or "none"
  sql_one "SELECT coalesce(
             (SELECT bool_or(over_limit)::text || '/' || bool_or(stopping)::text
                FROM brain.budget_state
               WHERE scope_type = 'agent' AND scope_id = %s AND period = %s), 'none') AS s" \
          "$AGENT" "$1"
}

precondition() {   # <label> <got> <want>
  if [ "$2" = "$3" ]; then ok "$1"; else
    bad "$1" "PRECONDITION: wanted [$3], got [$2]. The arming above did not land, so nothing this scene asserts is evidence about the guard."
  fi
}

TASK="$("$SWARM" post --lane "$LANE" --title "codex guard: a live run whose ceiling is crossed under it" \
        --for-agents --workdir "$TMP/work" 2>"$TMP/post.err" | awk '{print $1}')"
if [ -z "$TASK" ]; then
  bad "the scene's task was posted" "post printed nothing. stderr: $(head -c 300 "$TMP/post.err")"
  echo; echo "$PASS passed, $FAIL failed"; exit 1
fi
ok "the scene's task was posted as $TASK"

"$BUDGET" set agent "$AGENT" --limit 3.00 --period day --by test >/dev/null 2>&1
precondition "PRECONDITION: a day ceiling exists on $AGENT and is not yet breached" \
             "$(policy_says day)" "false/false"
STARTED="$(date +%s)"
timeout 180 "$RUNNER" "$AGENT" > "$TMP/out/codex.log" 2>&1
ELAPSED=$(( $(date +%s) - STARTED ))
"$BUDGET" unset agent "$AGENT" --period day --reason "scene complete" >/dev/null 2>&1

# ANTI-VACUUM. A task never claimed reads inbox/0/nothing-charged, which is also what several
# correct outcomes read. Prove the run happened before believing anything it left behind.
if grep -q "claimed $TASK " "$TMP/out/codex.log"; then
  ok "the runner claimed $TASK and ran it on the codex branch"
else
  bad "the runner claimed $TASK and ran it on the codex branch" \
      "no claim line; every assertion below would pass vacuously. log: $(tail -c 400 "$TMP/out/codex.log")"
fi

# The branch really was the codex one. If this says otherwise the whole file is testing claude.
if grep -q "codex run NOT guarded" "$TMP/out/codex.log"; then
  bad "the codex branch armed its guard" \
      "the runner said it had no run row id, so it ran the branch UNGUARDED. \`swarm run-start\` printed nothing."
else
  ok "the codex branch armed its guard (no 'NOT guarded' line in the run log)"
fi

check "the guard filed a stop keyed on run_id with NO session id" "$(codex_stop_shape "$TASK")" "1"
check "  and that run_id is this task's own run row"              "$(codex_stop_matches_run "$TASK")" "1"
check "the task is BLOCKED, so halt matched the stop on --run"    "$(meta "$TASK" state)"    "blocked"
check "attempts NOT reset (a budget stop is not a wall)"          "$(meta "$TASK" attempts)" "1"

# Separates "the guard stopped it" from "the 120s task_timeout stopped it" even if a later change
# made both leave the same row.
if [ "$ELAPSED" -lt 100 ]; then
  ok "killed in ${ELAPSED}s, well inside the 120s task_timeout"
else
  bad "killed in ${ELAPSED}s, well inside the 120s task_timeout" \
      "that is the timeout, not the guard. The guard did not reach this run."
fi

if [ "$(meta "$TASK" state)" = "inbox" ]; then
  bad "THE FAILURE THIS FILE EXISTS TO CATCH" \
      "a codex run stopped for money was reconciled as an ordinary task failure. The stop carried no key halt could match, so the lane was charged an attempt it did not earn."
fi

echo
echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ] || exit 1
