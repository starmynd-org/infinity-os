#!/usr/bin/env bash
# The spend brake, wired. Static assertions plus five live runs of the real runner.
#
# What it protects, in one line: A BUDGET STOP MUST BE BLOCKED, NEVER REOPENED.
#
# The two halts look identical from a distance -- a run that ended early, an engine that exited
# non-zero, a task still sitting `active` -- and they are opposite conditions:
#
#   A SUBSCRIPTION REFUSAL is a window that reopens on its own. `reopen`, unspent, attempts reset
#   to 0, because the agent was never allowed to start. Charging it to the task is the 2026-08-14
#   incident: 18 blocked tasks, 18 spurious operator questions, 11 idle hours, every runner healthy.
#
#   A BUDGET STOP is the operator's money. `block`. Not `fail`, which charges an attempt to a lane
#   that did nothing wrong. And emphatically NOT `reopen`, because reopen resets attempts to 0, so
#   the next free terminal claims the task and spends again with NO COUNTER LEFT TO STOP THE LOOP.
#
# The three-way discrimination is visible in two columns of `work_item`, which is why the live
# half asserts on the store rather than on log text:
#
#     unreported failure   ->  state inbox    attempts 1   (one attempt spent, requeued)
#     subscription refusal ->  state inbox    attempts 0   (unspent, the ladder reset)
#     budget stop          ->  state blocked  attempts 0   (parked for a human, and UNSPENT)
#
# THE THIRD ROW'S SECOND COLUMN CHANGED ON 2026-08-29 (task 0461) and the reason it changed is
# the reason it is safe to change. It read `attempts 1` because the CLAIM charges the attempt and
# `block` never gave it back, so nine rows sat blocked at attempt 1 of 2 for a ceiling on one
# afternoon and the operator requeued all nine by hand. `block --unspent` now refunds it.
#
# WHAT DID NOT CHANGE IS THE COLUMN THAT MATTERS. The money retry loop is prevented by `state
# blocked`, NOT by the counter: a blocked row cannot be claimed until a human raises the ceiling,
# so a refund cannot become a retry. Row two and row three are now distinguished by STATE ALONE,
# which is why the discriminator below tests both columns together and never `attempts` alone.
#
# The live half runs `bin/swarm-run` itself with a stub engine on PATH. A static test alone would
# not do: D6a's brake was fully tested and completely unwired, which is the exact failure this
# file exists to make impossible to repeat.
#
# Scene 3 is the load-bearing one for RECONCILIATION. Its stub writes the first-party incident
# exactly the way `budget.enforcer.RunGuard._stop` writes it -- BEFORE the signal, carrying the
# session id -- and then ALSO emits a rate-limit line into the log before dying without a result
# event. That log is poisoned on purpose. A classifier that reads text before it reads its own
# record calls that run a refusal and reopens it.
#
# Scene 4 is the load-bearing one for ENFORCEMENT, and it is the half scene 3 cannot reach. Scene
# 3's stub SIMULATES the guard: it files the incident itself, so it proves the runner reconciles a
# stop correctly while proving nothing about whether anything in production ever produces one.
# That was literally true until this scene existed -- `budget halt`'s stop branch was correct,
# tested, and had no producer. Scene 4's stub touches the brake only to move the meter, the way a
# sibling terminal does; the kill comes from the real `budget guard` the runner backgrounds.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"            # engine/
REPO="$(dirname "$ROOT")"            # the runtime repo
RUNNER="$ROOT/bin/swarm-run"
SWARM="$ROOT/bin/swarm"
BUDGET="$REPO/budget/bin/budget"
SCHEMA="$REPO/budget/schema/0003_budget.sql"

# ITS OWN DATABASE, not the shared `brain_scratch`. Measured while writing this file:
# `test-claim-race.sh` calls `scratch-db.sh truncate` between its races, and it was running in
# another lane's terminal at the same time as this one. Tasks vanished mid-scene, and a task that
# was never claimed looks exactly like a task correctly left alone -- so two assertions passed
# green while testing nothing at all. A suite that asserts on stored state cannot share a store
# with a suite that truncates it.
#
# Applying migration 3 makes this sharper, not softer: `budget_charge` and `budget_incident` both
# carry an FK to `work_item`, so `TRUNCATE brain.work_item CASCADE` now wipes the meter too.
#
# AND A PER-RUN NAME, not a fixed one. The default was `brain_budget_wire` until task 0321, which
# isolated this suite from every OTHER suite and from nothing else -- least of all from a second
# copy of ITSELF. `engine/tests/run-all.sh` exports no override, so every lane's run-all reached
# for that one name, and the store is built by `scratch-db.sh create`, whose first statement is
# `DROP DATABASE IF EXISTS ... WITH (FORCE)`. The later copy dropped the earlier copy's store out
# from under it and severed its connections mid-scene.
#
# Reproduced 2026-08-17 (task 0301's thread, then 0321): two copies against one name, 65s apart.
# Copy A 58 passed / 4 failed, copy B 62 passed / 0 failed. The victim is loud rather than subtle
# -- assertions come back EMPTY ('wanted [11.0000], got []') over psycopg2 UndefinedTable and
# 'database ... does not exist' -- but loud in a way that reads like a code failure to anyone who
# does not know a sibling was running.
#
# $$ is this shell's pid, so two copies cannot collide however they are launched. Pin BUDGET_WIRE_DB
# to name the store yourself; a store you named is yours, and the teardown never touches it.
if [ -n "${BUDGET_WIRE_DB:-}" ]; then
  DB="$BUDGET_WIRE_DB"; DB_PINNED=yes
else
  DB="brain_budget_wire_$$"; DB_PINNED=no
fi
case "$DB" in
  brain)         echo "refusing to run against the live store database 'brain'." >&2; exit 1 ;;
  brain_scratch) echo "refusing to run against the shared scratch database: another lane's suite truncates it." >&2; exit 1 ;;
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
has()  { if grep -q "$2" "$RUNNER"; then ok "$1"; else bad "$1" "not found: $2"; fi; }

echo "test-budget-wiring.sh"
echo

if bash -n "$RUNNER" 2>/dev/null; then ok "swarm-run parses"; else bad "swarm-run parses"; fi

# ================================================================ static wiring
# A correct brake wired to nothing is what this task inherited. These assertions are cheap and
# they fail loudly if any of the four calls is deleted or moved.
echo
echo "  the four calls are present"

has "1. the dispatch gate calls budget check"  'BUDGET_ARGS=(check --agent "\$AGENT"'
has "2. a live run is guarded"                 'guard --pid "\$ENGINE_PID"'
has "3. the run is metered from the run json"  'charge "\$CS_USD" --ref "\$CHARGE_REF" --source run_json'

# ONE CHARGE PER SESSION, and the reader that makes that possible. `$RUN_JSON` and `$STREAM` are
# keyed on (task, attempt) while `$LOG` is keyed on (agent, task, attempt), so two terminals on
# one attempt share the first two. The renderer is last-wins over the result event, so a charge
# read off `$RUN_JSON` alone meters exactly one of them: on the 2026-08-16 corpus that hid
# $15.4671 across `0092-attempt1` and `0102-attempt1`. Task 0244. The reader is asserted here
# because a charge loop pointed at the old single-value read would stay green on the line above.
has "  and it reads the stream session by session" 'budget/run_cost.py" --stream "\$STREAM"'
has "  and the ref carries the SESSION id"         'CHARGE_REF="\${CS_SID:-\$TASK_ID}'
# C5-COMP-1. The guard meters a live run in $0.25 steps; the end charge is the same money. Without
# --truth-up the ledger counts both, and every line above stays green through it.
has "  and it trues up against the guard's steps" 'by swarm-run --truth-up'
has "  and a second session is called out loudly"  'COLLISION: \$TASK_ID attempt'
has "4. reconciliation asks budget halt"       'halt --session "\${SID:-}"'

# THE RUN ROW ID, CAPTURED AND THEN USED, which is what arms the brake on an engine that writes
# no stream-json. Task 0167. `budget halt` matches first-party evidence on run_id OR session_id
# and nothing else, and the session id is read out of the claude stream's `init` event -- inside a
# block that is skipped outright when CFG_ENGINE is codex. So on codex `--session` is ALWAYS
# empty and the run row id is the only key there will ever be.
#
# The capture is asserted separately from every use, because the failure that reached this file
# was precisely a half-wiring: `$RUN_ROW_ID` was read in three places and assigned in none, so
# every `${RUN_ROW_ID:+...}` expanded to nothing, the codex guard's `[ -n ... ]` was never true,
# and the whole path looked wired while being dead. An assertion on the uses alone stays green
# through exactly that.
has "the run row id is CAPTURED from run-start"  'RUN_ROW_ID="\$("\$SWARM" run-start'
has "  and the codex branch is guarded on it"    'guard --pid "\$ENGINE_PID" --run "\$RUN_ROW_ID"'
has "  and halt is asked with it too"            'halt --session "\${SID:-}" \${RUN_ROW_ID:+--run'
has "halt is pointed at THIS runner's parser"  'export SWARM_RUN_PATH='
has "a budget stop takes block"                'block "\$TASK_ID" --agent "\$AGENT"'
has "the brake reports whether it is armed"    'THE SPEND BRAKE IS NOT ARMED'
has "a budget stop does not trip the backoff"  'FAILURES=0   # a ceiling is not an engine fault'
has "the guard is torn down with the run"      'kill "\$GUARD_PID"'
has "the shutdown trap kills the guard too"    'GUARD_PID" \] && { kill "\$GUARD_PID"'

echo
echo "  ordering (a correct call in the wrong place fixes nothing)"

python3 - "$RUNNER" <<'ORDER'
import pathlib, sys

src = pathlib.Path(sys.argv[1]).read_text().splitlines()


def at(needle, label):
    for i, line in enumerate(src):
        if needle in line:
            return i
    print(f"  FAIL  anchor missing: {label}")
    print(f"        {needle}")
    sys.exit(1)


gate    = at('BUDGET_ARGS=(check --agent', "the dispatch gate")
claim   = at('CLAIM_OUT="$("$SWARM" claim', "the claim")
engine  = at('run_engine "$PROMPT_FILE"', "the engine call")
charge  = at('charge "$CS_USD"', "the charge")
state   = at('TASK_STATE="$("$SWARM" state', "the store read")
halt    = at('halt --session "${SID:-}"', "the halt call")
limit   = at('LIMIT_UNTIL="$(limit_reset_epoch', "the refusal check")
failed  = at('"$SWARM" fail "$TASK_ID" --reason "$REASON"', "the fail path")
active  = at('TASK_STATE" = "active"', "the active branch")

rc = 0


def rule(label, cond, detail):
    global rc
    if cond:
        print(f"  ok    {label}")
    else:
        print(f"  FAIL  {label}")
        print(f"        {detail}")
        rc = 1


# BEFORE the claim, not after. A claim you then release is a claim another agent raced for, and
# `release` is conditional on still holding it, so the loser of that race gets nothing.
rule("the gate runs BEFORE the claim, not after it", gate < claim,
     f"gate@{gate} claim@{claim}")
# Charge, then decide. The charge must land whatever the outcome turns out to be; a decision that
# rolled back its own evidence would under-count the very spend it is judging.
rule("the charge runs after the engine and before reconciliation", engine < charge < state,
     f"engine@{engine} charge@{charge} state@{state}")
# The store is the authority on outcome; the log only ever explained it.
rule("halt sits inside the active branch, after the store is asked", active < halt,
     f"active@{active} halt@{halt}")
# THE LOAD-BEARING ORDER OF THE WHOLE FILE. First-party evidence beats somebody else's text. A
# long run killed for budget can carry a rate-limit line from an earlier turn it retried past;
# read the text first and that run is misfiled as a wall, reopened, re-dispatched, and spends
# again with the attempt ladder reset to zero.
rule("halt is asked BEFORE the log is scraped for a refusal", halt < limit,
     f"halt@{halt} limit@{limit}")
rule("both halt checks precede the fail path", halt < failed and limit < failed,
     f"halt@{halt} limit@{limit} fail@{failed}")

sys.exit(rc)
ORDER
if [ $? -eq 0 ]; then PASS=$((PASS + 5)); else FAIL=$((FAIL + 1)); fi

# THE FIFO INVARIANT, pinned rather than described. `run_engine` puts a named pipe between the
# engine and the stream formatter for two reasons that are both load-bearing: `$!` is then the
# ENGINE and not the formatter, so the shutdown trap reaches a real engine rather than a watcher;
# and `$?` is a real engine exit code, so the 124 timeout branch and the failure branch stay
# alive. Wiring a guard is the obvious way to break both, because the natural shape --
# `RunGuard.run()` starting the engine -- makes $! the guard. This block is the assertion that
# the guard was wired the other way round: beside the run, adopting a pid, never owning it.
python3 - "$RUNNER" <<'FIFO'
import pathlib, re, sys

src = pathlib.Path(sys.argv[1]).read_text().splitlines()
rc = 0


def rule(label, cond, detail=""):
    global rc
    print(f"  {'ok   ' if cond else 'FAIL '} {label}")
    if not cond:
        print(f"        {detail}")
        rc = 1


# ANCHORED PAST THE CODEX BRANCH. `run_engine` has two engine paths and both end in
# `ENGINE_PID=$!`, so a naive first-match finds the codex one and every ordering rule below reads
# backwards. Everything here is measured from the `claude -p` line forward, which is the branch
# the guard is wired into.
engine_cmd = next((i for i, l in enumerate(src)
                   if l.strip().startswith('timeout "$CFG_TASK_TIMEOUT" claude -p')), None)


def after(needle, start):
    if start is None:
        return None
    return next((i for i, l in enumerate(src) if i > start and l.strip() == needle), None)


capture = after("ENGINE_PID=$!", engine_cmd)
# ANCHORED PAST THE CODEX BRANCH TOO, and this one was not until task 0167. The block above went
# to the trouble of measuring from the `claude -p` line forward precisely because there are two
# engine paths -- and then found the guard by an UNANCHORED first match over the whole file. That
# was harmless only while the codex branch had no guard in it. The moment 0167 wired one, the
# first match became the CODEX guard at ~line 517 while `capture` and `waiting` were still the
# claude branch's at ~592 and ~627, and `capture < guard < waiting` went red against a runner
# whose claude branch had not changed at all. A false red is the cheap direction; the same
# unanchored search would have gone green on a codex-only guard while the claude branch lost its
# own, which is the expensive one. Measured from the same `claude -p` line as everything else.
guard   = next((i for i, l in enumerate(src)
                if i > (engine_cmd if engine_cmd is not None else -1)
                and 'guard --pid "$ENGINE_PID"' in l), None)
waiting = after('wait "$ENGINE_PID"', capture)
rcline  = after("ENGINE_RC=$?", waiting - 1 if waiting else None)

rule("the engine is still backgrounded and its pid captured directly",
     engine_cmd is not None and capture is not None and capture > engine_cmd,
     f"engine@{engine_cmd} capture@{capture}")
# The guard AFTER the capture. Before it and `$!` would be the guard, which is the whole defect.
rule("the guard starts AFTER $! is captured, so $! is still the ENGINE",
     None not in (capture, guard, waiting) and capture < guard < waiting,
     f"capture@{capture} guard@{guard} wait@{waiting}")
# ENGINE_RC must come off `wait "$ENGINE_PID"` with nothing in between, or it is somebody else's
# exit code and the 124 timeout branch reads a number that means nothing.
rule("ENGINE_RC is read immediately after waiting on the ENGINE",
     None not in (waiting, rcline) and rcline == waiting + 1,
     f"wait@{waiting} ENGINE_RC@{rcline}")
# A pipeline here would set $! to the formatter and $? to the formatter's rc. Both properties die
# silently: nothing errors, the trap just stops reaching the engine.
rule("the engine's stdout goes to the fifo, never through a pipeline",
     any('> "$fifo"' in l for l in src) and not any(re.search(r"claude -p.*\|", l) for l in src),
     "no `> \"$fifo\"` redirection found, or the engine was put in a pipeline")
sys.exit(rc)
FIFO
if [ $? -eq 0 ]; then PASS=$((PASS + 4)); else FAIL=$((FAIL + 1)); fi

# The disposition rule, read straight out of the branch rather than trusted. Everything between
# the halt call and the refusal branch is the budget stop's own code, and `reopen` and `fail`
# must not appear in it.
python3 - "$RUNNER" <<'DISPOSITION'
import pathlib, sys

src = pathlib.Path(sys.argv[1]).read_text().splitlines()
start = next(i for i, l in enumerate(src) if 'halt --session "${SID:-}"' in l)
end   = next(i for i, l in enumerate(src) if 'LIMIT_UNTIL="$(limit_reset_epoch' in l)
body  = "\n".join(src[start:end])

rc = 0
if '"$SWARM" block "$TASK_ID"' in body:
    print("  ok    the budget stop branch calls block")
else:
    print("  FAIL  the budget stop branch calls block")
    rc = 1
for verb in ("reopen", "fail"):
    if f'"$SWARM" {verb} "$TASK_ID"' in body:
        print(f"  FAIL  the budget stop branch must never call {verb}")
        print(f"        `{verb}` is the refusal/failure path. reopen resets attempts to 0, so the")
        print(f"        task is re-claimed and spends again with no counter left to stop it.")
        rc = 1
    else:
        print(f"  ok    the budget stop branch never calls {verb}")
sys.exit(rc)
DISPOSITION
if [ $? -eq 0 ]; then PASS=$((PASS + 3)); else FAIL=$((FAIL + 1)); fi

# ================================================================ live
echo
echo "  live: the real runner, a stub engine, and the store"

# Built fresh from D1's migrations, so every scene starts from a known board and this suite can
# assert on counts rather than on deltas it hopes nobody else moved.
#
# What survives the run, now that the name is per-run (see the DB comment at the top): a RED run's
# store is left behind, because reading the rows is how you find out what went wrong -- that is
# exactly what was wanted at 14:00 on 2026-08-17 and it is why task 0321 did not simply drop on
# every exit. A GREEN run's store is dropped, because there is nothing in it to read and one
# abandoned database per run is how you get the ninety that were in this Postgres when 0321
# counted them. A store you named yourself with BUDGET_WIRE_DB is never touched either way.
drop_private_store() {
  [ "$DB_PINNED" = no ] || return 0
  if [ "$FAIL" -ne 0 ]; then
    printf '\n  post-mortem store kept (this run was red): BRAIN_PG_DB=%s\n' "$DB"
    return 0
  fi
  ENGINE_SCRATCH_DB="$DB" "$ROOT/bin/scratch-db.sh" drop >/dev/null 2>&1
}

# ARMED BEFORE `create` RUNS, and not on the line after it. `create` DROPs, CREATEs, and only THEN
# applies every migration (`engine/bin/scratch-db.sh:293-296`, under that script's `set -euo
# pipefail`), so "create failed" routinely means "the database is on disk", half migrated or fully
# migrated depending on which file died. On the old ordering the `exit 1` below fired with no trap
# armed at all, and that database was left behind for good.
#
# MEASURED 2026-08-18, task 0346, against this file before the move: one throwaway migration forced
# to fail after every real one had applied (SCRATCH_SCHEMA_DIRS pointed at a directory outside the
# repo, so no lane's schema was touched) left `brain_budget_wire_4128867` in pg_database carrying
# 32 brain tables and schema_migration 23. The run printed `FAIL  a private store was built at
# brain_budget_wire_4128867` and stopped -- which names the database and then tells the reader it
# was not built, so nobody goes and drops it. The census that afternoon read 101 `brain%`
# databases, against the 94 task 0321 counted the day before.
#
# `scratch-db.sh drop` is `DROP DATABASE IF EXISTS ... WITH (FORCE)` (:439), so arming this before
# the store exists costs nothing on the paths where `create` dies before it creates anything. It
# also covers the two checks below, which exit, and a Ctrl-C mid-scene, which this suite is long
# enough to make a normal way for it to end.
trap drop_private_store EXIT
# 2>&1 into a variable and NOT into /dev/null. The discarded stderr is what made a lost CREATE
# race -- 'source database "template1" is being accessed by other users' -- print as "Is
# brain-postgres up?", sending the reader to a container that was healthy. scratch-db.sh now
# retries that race; if it still fails, the sentence Postgres actually said belongs on screen.
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

SECRETS="${BRAIN_SECRET_DIR:-$HOME/.brain-postgres-secrets}"
su_apply() {
  docker exec -i -e PGPASSWORD="$(cat "$SECRETS/brain-postgres-bootstrap-superuser")" \
    "${BRAIN_PG_CONTAINER:-brain-postgres}" \
    psql -v ON_ERROR_STOP=1 -h 127.0.0.1 -U postgres -d "$DB" -q -f - < "$1" >/dev/null 2>&1
}
su_query() {
  docker exec -i -e PGPASSWORD="$(cat "$SECRETS/brain-postgres-bootstrap-superuser")" \
    "${BRAIN_PG_CONTAINER:-brain-postgres}" \
    psql -tA -v ON_ERROR_STOP=1 -h 127.0.0.1 -U postgres -d "$DB" -c "$1" 2>/dev/null | tr -d '[:space:]'
}

# ---------------------------------------------------------------- WHAT THE BUILDER LEFT
#
# ASKED BEFORE THIS FILE REPAIRS ANY OF IT, and that ordering is the entire value of these two
# assertions. Task 0164.
#
# The history they encode: `bin/scratch-db.sh` used to glob `migrations/` alone and apply in
# FILENAME order, so `budget/schema/0003_budget.sql` was applied HERE, by this file, after the
# builder had finished -- which put it after `migrations/0005_lineage_resolution.sql`. Migration 5
# adds `produced_by_ref` and `resolution_status` beside every `produced_by` THAT EXISTS WHEN IT
# RUNS, by iterating the catalog, so it swept a database on which `budget_policy`, `budget_charge`
# and `budget_incident` did not exist yet and covered nothing -- while `brain.schema_migration`
# reported version 5 applied. THE LEDGER SAID THE MIGRATION RAN AND THE COLUMNS WERE NOT THERE,
# and this suite went 8 red the moment `budget/transitions.py` started naming all three columns.
#
# Task 0212 fixed that at the builder: `scratch-db.sh` now reads all three lanes' schema
# directories and orders them by the version each file's own INSERT records, so 3 lands before 5
# and the pair is on the budget tables before anything asks for it. `scratch-db.sh ledger` prints
# that order without connecting to anything. Migration 15 then made the invariant hold by
# CONSTRUCTION -- an event trigger completes the pair inside the CREATE TABLE -- so a fourth
# instance of this cannot be written by a lane that has never heard of the rule.
#
# So these check the BUILDER'S output, not this file's. The repair below is a backstop and it
# runs after, deliberately: a repair that ran first would make a builder regression invisible,
# which is the same shape as the ledger lie above -- a guard that passes because the thing it
# guards was quietly fixed underneath it. `check` rather than a hard exit, so a regression here
# reads as one named failure and the rest of the suite still runs.
BUILT_TABLES="$(su_query "
  SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
   WHERE n.nspname = 'brain' AND c.relkind = 'r'
     AND c.relname IN ('budget_policy','budget_charge','budget_incident')")"
check "the builder applied budget/schema/0003 (it is in scratch-db.sh's ledger)" \
      "${BUILT_TABLES:-0}" "3"

# All three tables, and the CONSTRAINTS as well as the columns. The old assertion counted three
# columns on `budget_charge` alone and would have stayed green through a regression that reached
# only `budget_incident`, which is where scene 3's incident is written.
BUILT_LINEAGE="$(su_query "
  SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
   WHERE n.nspname = 'brain' AND c.relkind = 'r'
     AND c.relname IN ('budget_policy','budget_charge','budget_incident')
     AND (SELECT count(*) FROM pg_attribute a
           WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
             AND a.attname IN ('produced_by','produced_by_ref','resolution_status')) = 3
     AND (SELECT count(*) FROM pg_constraint k
           WHERE k.conrelid = c.oid AND k.contype = 'c'
             AND k.conname IN (c.relname || '_resolution_status_enum',
                               c.relname || '_lineage_coherent')) = 2")"
check "the builder left all three budget tables with the full lineage record" \
      "${BUILT_LINEAGE:-0}" "3"

# ---------------------------------------------------------------- the repair, kept as a backstop
#
# Both applications are no-ops against a store the builder ordered correctly, measured: the two
# checks above pass on a database built by `scratch-db.sh create` and nothing else. They are kept
# because this suite must be able to run against a store somebody built another way, and because
# scenes 1 and 3 fail unreadably without the columns -- `UndefinedColumn` inside a rolled-back
# transaction reads as "the brake filed no incident", which is the failure THIS FILE EXISTS TO
# CATCH firing for a reason that has nothing to do with the brake. Both files are idempotent:
# 0003 guards its own version and uses IF NOT EXISTS / duplicate_object, and 0005 is ADD COLUMN
# IF NOT EXISTS inside a DO loop.
docker exec -i -e PGPASSWORD="$(cat "$SECRETS/brain-postgres-bootstrap-superuser")" \
  "${BRAIN_PG_CONTAINER:-brain-postgres}" \
  psql -v ON_ERROR_STOP=1 -h 127.0.0.1 -U postgres -d "$DB" -q -f - < "$SCHEMA" >/dev/null 2>&1
if "$BUDGET" status >/dev/null 2>&1; then
  ok "migration 3 is applied to $DB"
else
  bad "migration 3 is applied to $DB" "budget status still cannot read its tables"
  echo; echo "$PASS passed, $FAIL failed"; exit 1
fi

# MIGRATION 13, ASKED EXPLICITLY, because without it scenes 3 and 4 go red for a reason neither
# assertion can explain. `budget/transitions.py` writes the task-thread line for a stop as kind
# `budget`, which migration 1's CHECK does not allow -- so against an older store every stop
# carrying a `work_item_id` raises CheckViolation and rolls the WHOLE transaction back: no
# incident, nothing for `budget halt` to find, and a live budget stop reconciled as an ordinary
# failure. `scratch-db.sh` applies every schema file every lane has written, so this passes here;
# it is the LIVE store that has to be checked before believing the brake is armed in production.
THREAD_KIND_OK="$(BW_REPO="$REPO" python3 - <<'Q' 2>/dev/null
import os, sys
sys.path.insert(0, os.environ["BW_REPO"])
import store
with store.read("runtime") as s:
    r = s.one("SELECT count(*) AS n FROM pg_constraint "
              "WHERE conrelid = 'brain.thread'::regclass AND contype = 'c' "
              "  AND pg_get_constraintdef(oid) LIKE %s", ("%'budget'%",))
print(r["n"])
Q
)"
if [ "${THREAD_KIND_OK:-0}" != "0" ]; then
  ok "migration 13 is applied to $DB (brain.thread.kind accepts 'budget')"
else
  bad "migration 13 is applied to $DB (brain.thread.kind accepts 'budget')" \
      "every budget stop carrying a work_item_id will roll back and file NOTHING. Apply migrations/0013_thread_budget_kind.sql."
fi

# MIGRATION 5, RE-APPLIED, the second half of the backstop described above: 0003 recreates
# nothing on a correctly built store, but on a store where the budget tables arrived after
# migration 5 it is the file that makes them exist, and the lineage pair still would not be on
# them. Re-running 5 is a no-op by its own construction and by measurement (`store/
# test_lineage_by_construction.sh` re-applies it against a database carrying migration 15's event
# trigger and it stays a no-op), so this is a second application and not a repair.
#
# Found by scene 1 going red with `column "produced_by_ref" of relation "budget_charge" does not
# exist` the moment `budget/transitions.py` started writing the triple: the meter recorded
# NOTHING and the charge assertion read 0.0000.
su_apply "$REPO/migrations/0005_lineage_resolution.sql"
LINEAGE_OK="$(BW_REPO="$REPO" python3 - <<'Q' 2>/dev/null
import os, sys
sys.path.insert(0, os.environ["BW_REPO"])
import store
with store.read("runtime") as s:
    r = s.one("SELECT count(*) AS n FROM information_schema.columns "
              "WHERE table_schema = 'brain' AND table_name = 'budget_charge' "
              "  AND column_name IN ('produced_by','produced_by_ref','resolution_status')")
print(r["n"])
Q
)"
check "the budget tables carry migration 5's lineage triple" "${LINEAGE_OK:-0}" "3"

TMP="$(mktemp -d)"
AGENT="bw$$"
LANE="bw$$"
cleanup_live() {
  "$BUDGET" unset agent "$AGENT" --period total --reason "test teardown" >/dev/null 2>&1
  rm -rf "$TMP"
  # This trap REPLACES the one installed beside `scratch-db.sh create`, so it has to carry the
  # store teardown forward. A `trap ... EXIT` is not additive, and forgetting that here would
  # leak a database on every run from this line down -- silently, since nothing would say so.
  drop_private_store
}
trap cleanup_live EXIT

mkdir -p "$TMP/bin" "$TMP/acct1" "$TMP/acct2" "$TMP/work" "$TMP/state" "$TMP/out"

# TWO account directories on purpose. With one, the refusal scene's `rotate_or_wait` has nothing
# to rotate to and sleeps out the window -- up to the six hour clamp -- which would hang the
# suite. With two it rotates and returns immediately, which is also the branch a real fleet runs.
cat > "$TMP/config.json" <<CONF
{
  "fleet": "swarm",
  "agents": [
    {"name": "$AGENT", "role": "terminal", "lanes": ["$LANE"], "engine": "claude",
     "model": "stub", "permission_mode": "auto", "interval": 1, "task_timeout": 120,
     "config_dirs": ["$TMP/acct1", "$TMP/acct2"], "workdir": "$TMP/work"}
  ]
}
CONF

# The stub engine. It speaks stream-json on stdout, which is the only contract the runner has
# with it, and it exits the way each scene needs.
cat > "$TMP/bin/claude" <<'STUB'
#!/usr/bin/env python3
import json, os, sys, time, uuid

mode = os.environ.get("STUB_MODE", "normal")
sid = "stub-" + uuid.uuid4().hex[:12]

try:
    sys.stdin.read()          # drain the prompt; the runner feeds it on stdin
except Exception:
    pass


def emit(ev):
    sys.stdout.write(json.dumps(ev) + "\n")
    sys.stdout.flush()


emit({"type": "system", "subtype": "init", "session_id": sid,
      "model": "stub", "cwd": os.getcwd()})

# The exact wording from the night of 2026-08-14, byte for byte.
LIMIT = "You've hit your session limit · resets 7:10pm (Europe/Bucharest)"

if mode == "ratelimit":
    emit({"type": "assistant", "message": {"content": [{"type": "text", "text": LIMIT}]}})
    sys.exit(1)                                  # refused, no result event, nothing spent

if mode == "budget":
    # THE POISONED LOG. A long run killed for spend really can carry this line from an earlier
    # turn it retried past. It is here so that a classifier reading text before its own record
    # gets the wrong answer and this scene goes red.
    emit({"type": "assistant", "message": {"content": [{"type": "text", "text": LIMIT}]}})

    # What budget.enforcer.RunGuard._stop does: RECORD FIRST, THEN SIGNAL, carrying the session
    # id, because a killed run writes no result event and session_id is the only key `halt` has.
    sys.path.insert(0, os.environ["STUB_REPO"])
    import budget                                 # noqa: F401  (registers the verbs)
    import store
    store.apply("budget stop", actor="test-guard", kind="hard_stop",
                scope_type="agent", scope_id=os.environ["STUB_AGENT"],
                spend_usd="9.99", limit_usd="1.0000", percent_used="999.00",
                action_taken="run_stopped", detected_by="charge",
                agent=os.environ["STUB_AGENT"], lane=os.environ["STUB_LANE"],
                work_item_id=os.environ["STUB_TASK"], session_id=sid,
                reason="test: the ceiling was crossed mid-run and the group was SIGTERMed",
                by="test-guard")
    sys.exit(143)                                 # killed. No result event, by definition.

if mode == "guarded":
    # THE LIVE GUARD SCENE. Nothing in here simulates the brake: this stub only makes itself a
    # long run whose ceiling is crossed by somebody else while it is still going, which is the
    # fleet case exactly. A SIBLING terminal finishing and charging is what moves the shared
    # meter; this run is allowed when it starts and is over the ceiling ten seconds later.
    #
    # Then it sleeps far past its own task_timeout and does NOT report. If the real
    # `budget guard` does not reach it, this scene takes the full timeout and the runner files
    # the run as an ordinary unreported failure -- state inbox, attempts 1 -- which is what the
    # assertions below distinguish from `blocked`.
    sys.path.insert(0, os.environ["STUB_REPO"])
    import budget                                 # noqa: F401  (registers the verbs)
    import store
    store.apply("budget charge", actor="sibling-terminal", usd="5.00",
                source_ref="sibling-" + sid, source="manual",
                agent=os.environ["STUB_AGENT"],
                note="another terminal finished and charged while this run was live")
    time.sleep(600)
    sys.exit(0)                                   # never reached: something must stop this

if mode == "collision":
    # TWO ENGINE SESSIONS IN ONE STREAM. This is one process rather than two, and it does not
    # need to be two: what the runner sees either way is a single stream file carrying two
    # session ids, because `$RUN_JSON` and `$STREAM` are keyed on (task, attempt) while `$LOG`
    # is keyed on (agent, task, attempt). On 2026-08-16 two terminals really did land on one
    # attempt (`0092-attempt1`, T3's 4aee2969 at $11.4242 and T1's 7912efa2 at $0.9658) and the
    # renderer's last-wins read metered exactly one of them.
    #
    # The SECOND session is also given TWO result events, rising, because `total_cost_usd` is
    # CUMULATIVE within a session. That is the trap in the other direction: a reader that sums
    # every result event charges 3.00 + 7.00 here instead of 7.00, which on the real corpus
    # overstates one 19-result-event run by 10.8x. Only "last within a session, summed across
    # sessions" gets 11.00, and only 11.00 passes this scene.
    other = "stub-other-" + uuid.uuid4().hex[:8]
    emit({"type": "result", "subtype": "success", "session_id": other, "num_turns": 9,
          "total_cost_usd": 4.0000, "is_error": False, "result": "the session that got lost"})
    emit({"type": "result", "subtype": "success", "session_id": sid, "num_turns": 3,
          "total_cost_usd": 3.0000, "is_error": False, "result": "not final, a running total"})
    emit({"type": "result", "subtype": "success", "session_id": sid, "num_turns": 5,
          "total_cost_usd": 7.0000, "is_error": False, "result": "the one the run json keeps"})
    sys.exit(0)

# normal: a run that cost real money and never reported. The ordinary unreported failure.
emit({"type": "result", "subtype": "success", "session_id": sid, "num_turns": 3,
      "total_cost_usd": 0.4200, "is_error": False,
      "result": "did the work, forgot to call done"})
sys.exit(0)
STUB
chmod +x "$TMP/bin/claude"

export ENGINE_HOME="$TMP/state"
export ENGINE_CONFIG="$TMP/config.json"
export PATH="$TMP/bin:$PATH"
export SWARM_ONCE=1
export STUB_REPO="$REPO"
export STUB_AGENT="$AGENT"
export STUB_LANE="$LANE"

meta() {
  "$SWARM" show "$1" --json 2>/dev/null \
    | python3 -c "import json,sys; print(json.load(sys.stdin)['task']['$2'])" 2>/dev/null
}

charged_for() {
  python3 - "$1" <<'Q'
import os, sys
sys.path.insert(0, os.environ["STUB_REPO"])
import store
with store.read("runtime") as s:
    r = s.one("SELECT coalesce(sum(usd),0) AS t FROM brain.budget_charge "
              "WHERE work_item_id = %s", (sys.argv[1],))
print(f"{float(r['t']):.4f}")
Q
}

# How MANY charge rows, not how much. The sum alone cannot tell one correct charge from two
# wrong ones that happen to add up, and the per-session idempotency ref is the thing under test.
charge_rows_for() {
  python3 - "$1" <<'Q'
import os, sys
sys.path.insert(0, os.environ["STUB_REPO"])
import store
with store.read("runtime") as s:
    r = s.one("SELECT count(*) AS n FROM brain.budget_charge WHERE work_item_id = %s",
              (sys.argv[1],))
print(int(r["n"]))
Q
}

incidents_of_kind() {
  python3 - "$1" "$2" <<'Q'
import os, sys
sys.path.insert(0, os.environ["STUB_REPO"])
import store
with store.read("runtime") as s:
    r = s.one("SELECT count(*) AS n FROM brain.budget_incident "
              "WHERE kind = %s AND agent = %s", (sys.argv[1], sys.argv[2]))
print(r["n"])
Q
}

# Hard stops filed against one task BY THE WATCHER. `detected_by` names the enforcement point:
# `sweep` is the guard standing beside a live run, `charge` is a guard metering its own child,
# `preflight` is the dispatch gate. Counting on it is what makes scene 4 assert on the thing this
# task wired rather than on any stop from anywhere.
stops_by_sweep() {
  python3 - "$1" <<'Q'
import os, sys
sys.path.insert(0, os.environ["STUB_REPO"])
import store
with store.read("runtime") as s:
    r = s.one("SELECT count(*) AS n FROM brain.budget_incident "
              "WHERE kind = 'hard_stop' AND detected_by = 'sweep' AND action_taken = 'run_stopped'"
              "  AND work_item_id = %s", (sys.argv[1],))
print(r["n"])
Q
}

# Does this task's thread carry a line matching a pattern? 1 or 0, never the count, because the
# caller wants a yes/no and a count that drifts to 2 would read as a fail on a passing run.
#
# The COLUMN says a refund happened; the THREAD says it happened for a stated reason, signed. An
# `attempts` that silently went from 1 to 0 is indistinguishable at the row from a `reopen`, and
# telling those two apart afterwards is the whole reason task 0461's refund writes a trail.
thread_has() {
  python3 - "$1" "$2" <<'Q'
import os, sys
sys.path.insert(0, os.environ["STUB_REPO"])
import store
with store.read("runtime") as s:
    r = s.one("SELECT count(*) AS n FROM brain.thread "
              "WHERE work_item_id = %s AND text LIKE %s",
              (sys.argv[1], f"%{sys.argv[2]}%"))
print(1 if r["n"] else 0)
Q
}

# ---------------------------------------------------------------- PRECONDITIONS. Task 0322.
#
# Scenes 4, 4b, 5 and 5b arm their own board with `budget set` / `budget charge` / `budget unset`
# and an injected constraint, and every one of those calls sends both streams to /dev/null and
# drops its exit code. That is fine while they land and it is a day of somebody's life when they
# do not, because of WHERE the resulting red lands. If scene 5's arming is swallowed the agent is
# NOT over any ceiling, the gate is asked, it correctly ALLOWS, and the scene prints four failures
# whose plain reading is "the spend gate no-opped and charged $0.42 it should have refused" --
# which is verbatim the title task 0301 was filed under, three lines below a command that printed
# its actual reason into /dev/null. Scene 4b is worse: its arming failure makes it go GREEN.
#
# So each of those scenes now states its premise first, and a premise that did not hold says
# PRECONDITION. That word moves the investigation from "the brake is broken" (production) to "the
# scene could not be armed" (this file), which are opposite places to look.
#
# WHY THESE READ THE STORE AND NOT THE ARMING EXIT CODES. `budget charge` returns EXIT_STOPPED (3)
# whenever the charge it just made puts the scope over -- `budget/cli.py:180` -- which is exactly
# what scene 5's arming charge exists to do. `if ! "$BUDGET" charge ...` would go red on the
# HEALTHY path. And because a precondition is a READ of the same view the gate decides from
# (`budget check --dry-run` records nothing, so it cannot even disturb scene 5's incident count),
# it can never mask a real gate failure. It can only tell you the board was never set up.
precondition() {   # <label> <got> <want>
  if [ "$2" = "$3" ]; then ok "$1"; else
    bad "$1" "PRECONDITION: wanted [$3], got [$2]. The arming above did not land, so nothing this scene asserts is evidence about the brake."
  fi
}

# The enforcer's own verdict for this agent, dry-run, which is the same read `swarm-run`'s dispatch
# gate makes one moment later. `stop` is exit 3 (`budget/cli.py:EXIT_STOPPED`).
gate_says() { "$BUDGET" check --agent "$AGENT" --dry-run >/dev/null 2>&1; [ $? -eq 3 ] && echo stop || echo allow; }

# One row per ACTIVE policy on this agent for one period (`budget_state` is `WHERE state='active'`),
# reduced to the two booleans the enforcer decides from. `none` when no such policy exists at all,
# which is what a swallowed `budget set` -- or a `budget unset` that DID land -- leaves behind.
policy_says() {   # <period> -> "<over_limit>/<stopping>", or "none"
  su_query "SELECT coalesce(
              (SELECT bool_or(over_limit)::text || '/' || bool_or(stopping)::text
                 FROM brain.budget_state
                WHERE scope_type = 'agent' AND scope_id = '$AGENT' AND period = '$1'), 'none')"
}

# run_scene <mode> <label> -> sets SCENE_TASK
#
# Each scene retires the one before it. Scenes 1 and 2 both leave their task in `inbox`, and the
# claim orders by `priority, score, id ASC`, so without this the next scene re-claims the PREVIOUS
# scene's requeued task and asserts against the wrong row. Observed, not theorised: the refusal
# scene ran against the unreported-failure scene's task.
PREV_TASK=""
run_scene() {
  local mode="$1" label="$2"
  [ -n "$PREV_TASK" ] && "$SWARM" cancel "$PREV_TASK" --reason "scene complete" >/dev/null 2>&1
  # The post's stderr is KEPT. It used to go to /dev/null, and when `post` failed every scene
  # then read `state []`, `attempts []`, `charged 0.0000` -- which is a wall of red assertions
  # about the brake for a reason that has nothing to do with the brake. One line saying why beats
  # twenty saying what.
  SCENE_TASK="$("$SWARM" post --lane "$LANE" --title "$label" --for-agents --workdir "$TMP/work" 2>"$TMP/post-$mode.err" | awk '{print $1}')"
  if [ -z "$SCENE_TASK" ]; then
    bad "$mode: the scene's task was posted" \
        "post printed nothing. stderr: $(head -c 300 "$TMP/post-$mode.err")"
    return 1
  fi
  PREV_TASK="$SCENE_TASK"
  export STUB_TASK="$SCENE_TASK"
  STUB_MODE="$mode" timeout 180 "$RUNNER" "$AGENT" > "$TMP/out/$mode.log" 2>&1
  # THE ANTI-VACUUM ASSERTION, and it is here because the vacuum actually happened. A task that
  # was never claimed reads `state inbox, attempts 0, nothing charged`, which is exactly what a
  # correctly handled refusal reads. Two scenes passed green against a store another lane's suite
  # had truncated out from under them. Prove the run happened before believing what it left.
  if grep -q "claimed $SCENE_TASK " "$TMP/out/$mode.log"; then
    ok "$mode: the runner claimed $SCENE_TASK and ran it"
  else
    bad "$mode: the runner claimed $SCENE_TASK and ran it" \
        "no claim line in the runner output; every assertion below would pass vacuously"
  fi
}

# --------------------------------------------------------------- scene 1: an ordinary failure
# The control. Without it, a halt branch that blocked everything would pass every other scene.
run_scene normal "budget wiring: an unreported run that cost money"
T1="$SCENE_TASK"
check "1. unreported run: state"                 "$(meta "$T1" state)"    "inbox"
check "1. unreported run: attempts SPENT"        "$(meta "$T1" attempts)" "1"
# And call 2 landed: `brain.run` has no cost column, so this row is the only record of the spend.
check "1. the run json's total_cost_usd was charged" "$(charged_for "$T1")" "0.4200"

# --------------------------------------------------------------- scene 2: a subscription wall
run_scene ratelimit "budget wiring: a subscription refusal"
T2="$SCENE_TASK"
check "2. refusal: state"                        "$(meta "$T2" state)"    "inbox"
check "2. refusal: attempts RESET, task unspent" "$(meta "$T2" attempts)" "0"
check "2. refusal: nothing was charged"          "$(charged_for "$T2")"   "0.0000"

# --------------------------------------------------------------- scene 3: the budget stop
# The one this file exists for. Same shape as scene 2 from outside -- non-zero exit, task still
# active, a rate-limit line in the log -- and it must come out the other way.
BEFORE_STOPS="$(incidents_of_kind hard_stop "$AGENT")"
run_scene budget "budget wiring: a run stopped for spend"
T3="$SCENE_TASK"
check "3. budget stop: the guard filed its incident" \
      "$(incidents_of_kind hard_stop "$AGENT")" "$((BEFORE_STOPS + 1))"
check "3. budget stop: state is BLOCKED, not requeued"     "$(meta "$T3" state)"    "blocked"
# 0 since task 0461: the claim charged this attempt before the run did anything, and a run
# stopped for spend was never judged. `block --unspent` gives it back. The row is still PARKED,
# which is the part that stops the loop, and the discriminator below is what proves it.
check "3. budget stop: attempts REFUNDED (the run was defunded, not judged)" \
      "$(meta "$T3" attempts)" "0"
check "3. budget stop: the refund is on the thread, not just in the column" \
      "$(thread_has "$T3" 'attempt REFUNDED')" "1"

# BOTH COLUMNS, AND THE `AND` IS THE WHOLE ASSERTION. `attempts 0` alone is now the CORRECT
# outcome of a budget stop, so testing it alone would fire on every healthy run. What must never
# happen is `attempts 0` TOGETHER WITH `state inbox`: that is the row back in the queue with no
# ladder left, which is `reopen`, which is the money retry loop.
if [ "$(meta "$T3" state)" = "inbox" ] && [ "$(meta "$T3" attempts)" = "0" ]; then
  bad "3. THE FAILURE THIS FILE EXISTS TO CATCH" \
      "the budget stop was reopened. The task is back in the queue AND its ladder is reset, so the next free terminal claims it and spends again with no counter left to stop the loop. A refund is only safe while the row stays blocked."
fi

# --------------------------------------------------------------- scene 4: the LIVE guard
# Scene 3 proves the reconciliation half against a stub that writes the incident itself. This one
# removes the stub: nothing in `guarded` mode touches the brake. It makes itself a long run and
# has a SIBLING terminal charge past the ceiling while it is still going, which is the fleet case
# the guard exists for -- the meter is shared, so what takes a live run over is usually not that
# run. The only thing that can stop it is the real `budget guard` the runner backgrounds.
#
# The failure mode is loud rather than silent. If the guard never fires, the stub sleeps out its
# 120s task_timeout, the runner files an ordinary unreported failure, and the state assertion
# below reads `inbox`/attempts 1 instead of `blocked`/attempts 1. The elapsed assertion separates
# "the guard stopped it" from "the timeout stopped it" even if some later change made both leave
# the same row.
#
# A `day` ceiling, not `total`, and unset immediately afterwards: scene 5 sets its own `total`
# policy on the same agent and asserts on a blocked_dispatch count, and a second live ceiling
# would refuse that dispatch for the wrong reason.
"$BUDGET" set agent "$AGENT" --limit 3.00 --period day --by test >/dev/null 2>&1
# The ceiling the sibling terminal is about to cross. Swallow this `set` and the stub charges
# $5.00 against no ceiling at all, `budget guard` has nothing to trip on, the run sleeps out its
# 120s timeout, and the four assertions below report `inbox`/attempts 1, no sweep incident and no
# [budget-guard] line -- which is exactly how "the live guard never fired" looks. The ceiling must
# also be UNBREACHED right now, or the dispatch gate refuses the run before it ever starts and the
# same four reds appear for the opposite reason.
precondition "4. PRECONDITION: a day ceiling exists on $AGENT and is not yet breached" \
             "$(policy_says day)" "false/false"
GUARD_STARTED="$(date +%s)"
run_scene guarded "budget wiring: a live run whose ceiling is crossed under it"
GUARD_ELAPSED=$(( $(date +%s) - GUARD_STARTED ))
TG="$SCENE_TASK"
"$BUDGET" unset agent "$AGENT" --period day --reason "scene 4 complete" >/dev/null 2>&1

# `detected_by` is the column that says WHICH enforcement point fired. `sweep` is the watcher
# re-reading the meter beside a live run; `charge` is the in-process guard metering its own
# child; `preflight` is the dispatch gate. Asserting on it is how this scene proves the stop came
# from the thing this task wired, and not from a path that was already green.
check "4. live guard: a sweep-detected hard_stop was filed against the task" \
      "$(stops_by_sweep "$TG")" "1"
check "4. live guard: state is BLOCKED, so reconciliation matched that incident" \
      "$(meta "$TG" state)" "blocked"
check "4. live guard: attempts REFUNDED (the run was defunded, not judged)" \
      "$(meta "$TG" attempts)" "0"
if [ "$(meta "$TG" state)" = "inbox" ]; then
  bad "4. live guard: the row must stay PARKED" \
      "state is inbox with attempts 0: the live guard's stop was reopened, which is the retry loop."
fi
if [ "$GUARD_ELAPSED" -lt 90 ]; then
  ok "4. live guard: killed in ${GUARD_ELAPSED}s, well inside the 120s task_timeout"
else
  bad "4. live guard: killed in ${GUARD_ELAPSED}s, well inside the 120s task_timeout" \
      "the run went the distance: that is the timeout stopping it, not the guard"
fi
if grep -q "budget-guard" "$TMP/out/guarded.log" 2>/dev/null \
   || grep -q "budget-guard" "$TMP/state/logs/$AGENT/${TG}-attempt1.log" 2>/dev/null; then
  ok "4. live guard: the guard said so in the run's own log"
else
  bad "4. live guard: the guard said so in the run's own log" "no [budget-guard] line anywhere"
fi

# ------------------------------------------------- scene 4b: two sessions, one (task, attempt)
# Task 0244. Scene 1 proves a charge lands; it cannot prove the charge is complete, because one
# session is all it ever produces. This scene is the one that fails against the code scene 1
# passes: a run json read (`json.load(...)['total_cost_usd']`) charges 7.00 and silently drops
# 4.00, and a sum-every-result-event read charges 14.00. Only 11.00 is right.
#
# It runs BEFORE scene 5 arms its total-period ceiling, for the reason scene 4 gives at its own
# `unset`: a live policy on this agent would refuse the dispatch and every assertion here would
# pass vacuously against a run that never happened.
#
# THAT SENTENCE IS THE PRECONDITION, and until now it was only a sentence. Scene 4's `unset` also
# goes to /dev/null, and the meter it leaves behind is well past $3.00 because the guarded stub
# charged $5.00 to trip the guard. So a swallowed `unset` leaves a breached day ceiling standing,
# the gate refuses this dispatch, and 4b reads `charged 0.0000`, `0 charge rows` and no COLLISION
# line -- the exact signature of the per-session charge loop being broken, which is the bug this
# scene exists to catch. `run_scene`'s claim assertion is the backstop and it is not enough on its
# own: it says the run did not happen, not that this file is why.
precondition "4b. PRECONDITION: nothing is braking $AGENT, so this dispatch can happen" \
             "$(gate_says)" "allow"
run_scene collision "budget wiring: two engine sessions sharing one attempt"
TC="$SCENE_TASK"
check "4b. collision: both sessions were charged, neither summed" "$(charged_for "$TC")" "11.0000"
check "4b. collision: one charge row PER SESSION, not per run"    "$(charge_rows_for "$TC")" "2"
if grep -q "COLLISION: $TC attempt" "$TMP/out/collision.log"; then
  ok "4b. collision: the runner said two terminals ran this attempt"
else
  bad "4b. collision: the runner said two terminals ran this attempt" \
      "no COLLISION line: the money was recovered but the duplicated WORK went unreported"
fi

# --------------------------------------------------------------- scene 5: the dispatch gate
# Nothing is spent, nothing is claimed, no attempt is charged: the cheapest stop there is.
"$SWARM" cancel "$PREV_TASK" --reason "scene complete" >/dev/null 2>&1
"$BUDGET" set agent "$AGENT" --limit 0.01 --period total --by test >/dev/null 2>&1
# Breached AFTER the ceiling is set, not before. A `total` window measures from the policy's
# `effective_from`, deliberately, so that raising a ceiling does not reset the meter -- which
# means scene 1's charge is invisible to a policy created after it. An earlier draft of this
# scene relied on that charge and the gate correctly allowed the dispatch.
"$BUDGET" charge 1.00 --ref "gate-probe-$AGENT" --source manual --agent "$AGENT" \
  --note "put the agent over its ceiling so the gate has something to refuse" >/dev/null 2>&1
# THE ONE THIS TASK WAS FILED FOR. Both armings above are silent in both streams and neither exit
# code is looked at, and the gate is a further three lines down. Miss either and the agent is over
# nothing, the gate is asked, it ALLOWS -- correctly -- and the four checks below print a task that
# WAS claimed, an attempt that WAS charged, $0.42 spent, and no blocked_dispatch row. Read cold,
# that is "the spend gate no-opped and spent past a hard ceiling", which is a production alarm.
# It is a dry-run read of the same view the gate reads one moment later, so it cannot mask a gate
# failure, and `--dry-run` records nothing, so it cannot disturb BEFORE_BLOCKED either.
precondition "5. PRECONDITION: $AGENT really is over a hard ceiling before the gate is asked" \
             "$(gate_says)" "stop"
BEFORE_BLOCKED="$(incidents_of_kind blocked_dispatch "$AGENT")"
T4="$("$SWARM" post --lane "$LANE" --title "budget wiring: must never be dispatched" --for-agents --workdir "$TMP/work" 2>/dev/null | awk '{print $1}')"
export STUB_TASK="$T4"
STUB_MODE=normal timeout 120 "$RUNNER" "$AGENT" > "$TMP/out/gate.log" 2>&1

check "5. gate: the task was never claimed"      "$(meta "$T4" state)"    "inbox"
check "5. gate: no attempt was charged"          "$(meta "$T4" attempts)" "0"
check "5. gate: nothing was spent on it"         "$(charged_for "$T4")"   "0.0000"
check "5. gate: a typed blocked_dispatch was recorded" \
      "$(incidents_of_kind blocked_dispatch "$AGENT")" "$((BEFORE_BLOCKED + 1))"
if grep -q "spend gate: NOT dispatching" "$TMP/out/gate.log"; then
  ok "5. gate: the runner said why, in the operator's words"
else
  bad "5. gate: the runner said why, in the operator's words" "no 'spend gate' line in the runner output"
fi

# ------------------------------------------ scene 5b: the refusal outlives its own audit row
# Task 0301, and it is the scene that fails against the code scene 5 passes.
#
# Scene 5 proves the gate refuses when the store lets it write. It cannot see the failure that was
# actually observed under load: the gate DECIDED to refuse, the `blocked_dispatch` INSERT raised,
# `budget/cli.py` turned that exception into exit 2, and `swarm-run` -- which stopped only on
# exit 3 -- claimed, dispatched and spent $0.42 against this $0.01 ceiling. Scene 5 stays green
# through all of it, because on the healthy path the write succeeds.
#
# So the failure is INJECTED rather than waited for, and injected by this scene rather than by
# timing: one NOT VALID check constraint makes exactly the gate's own row unwritable and nothing
# else. Two things are asserted and they are not the same thing --
#   the MONEY is protected: not claimed, no attempt, nothing spent, and the runner says why;
#   the COUNT is not: no incident row can exist, and the operator is told so in words.
# A "fix" that quietly allowed the dispatch to protect the count would pass the second assertion
# and fail the first, which is the trade this scene exists to pin down.
# T4 IS CANCELLED FIRST, and skipping that made this scene pass against the very code it exists to
# fail: `claim` hands out the OLDEST task in the lane, so a fail-open runner spent its $0.42 on
# scene 5's leftover T4 and T5 sat untouched in inbox looking exactly like a task the gate had
# held. Scene 5 cancels its predecessor for the same reason and says so.
"$SWARM" cancel "$T4" --reason "scene complete: 5b needs an empty lane to be readable" >/dev/null 2>&1
su_query "ALTER TABLE brain.budget_incident
            ADD CONSTRAINT bw_0301_unwritable CHECK (kind::text <> 'blocked_dispatch') NOT VALID" \
  >/dev/null
# `su_query` sends psql's stderr to /dev/null, so a failed ALTER is indistinguishable from a
# successful one at this line. Without the constraint actually on the table, 5b is not the
# unwritable-store scene at all -- it is a second, weaker copy of scene 5: the gate writes its row
# normally, "the row really could not be written" reads one too many, and "the uncountable refusal
# is legible in the run log" goes red saying the operator was not told about a thing that never
# happened. Two reds about the audit trail for a store that was never made unwritable.
precondition "5b. PRECONDITION: the blocked_dispatch row really was made unwritable" \
             "$(su_query "SELECT count(*) FROM pg_constraint
                           WHERE conname = 'bw_0301_unwritable'
                             AND conrelid = 'brain.budget_incident'::regclass")" "1"
UNREC_BEFORE="$(incidents_of_kind blocked_dispatch "$AGENT")"
T5="$("$SWARM" post --lane "$LANE" --for-agents --workdir "$TMP/work" \
      --title "budget wiring: refused by a gate that cannot write" 2>/dev/null | awk '{print $1}')"
export STUB_TASK="$T5"
STUB_MODE=normal timeout 120 "$RUNNER" "$AGENT" > "$TMP/out/gate-unwritable.log" 2>&1
# Dropped immediately, and unconditionally: every later reader of this database -- including the
# failure dump below -- has to see an ordinary store.
su_query "ALTER TABLE brain.budget_incident DROP CONSTRAINT bw_0301_unwritable" >/dev/null

check "5b. unwritable: the task was never claimed"   "$(meta "$T5" state)"    "inbox"
check "5b. unwritable: no attempt was charged"       "$(meta "$T5" attempts)" "0"
check "5b. unwritable: NOTHING WAS SPENT"            "$(charged_for "$T5")"   "0.0000"
check "5b. unwritable: and the row really could not be written" \
      "$(incidents_of_kind blocked_dispatch "$AGENT")" "$UNREC_BEFORE"
if grep -q "spend gate: NOT dispatching" "$TMP/out/gate-unwritable.log"; then
  ok "5b. unwritable: the runner still refused, in the operator's words"
else
  bad "5b. unwritable: the runner still refused, in the operator's words" \
      "the gate could not write its row and the runner DISPATCHED: money spent past a hard ceiling"
fi
if grep -q "was NOT recorded" "$TMP/out/gate-unwritable.log"; then
  ok "5b. unwritable: the uncountable refusal is legible in the run log"
else
  bad "5b. unwritable: the uncountable refusal is legible in the run log" \
      "the incident count is one short and nothing in the log says why"
fi

if [ "$FAIL" -ne 0 ]; then
  echo
  echo "  runner output is in $TMP/out (kept only until this shell exits):"
  for f in "$TMP/out"/*.log; do
    echo "  --- $f"
    sed -n '1,60p' "$f" | sed 's/^/      /'
  done
fi

echo
echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ] || exit 1
