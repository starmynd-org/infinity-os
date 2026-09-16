#!/usr/bin/env bash
# The infrastructure gate. The real runner, a stub engine, a private store, and a before/after.
#
# What it protects, in one line: A DEAD NETWORK MUST NEVER BE CHARGED TO A TASK'S ATTEMPT BUDGET.
#
# The incident, 2026-08-18, task 0400. The operator lost internet at about 01:00Z. Every in-flight
# engine call returned `Request timed out`, the runner filed each as `unreported: engine exited
# rc=1 without reporting`, and `fail` spent an attempt on each. TWENTY-ONE TASKS spent their
# entire attempt budget in under an hour and not one of them was judged on its merits; the fleet
# then sat dead for six hours and was found only because a human looked. The 3-strike backoff made
# it worse rather than better -- it slept 300s and woke to claim a FRESH task, so 0366, 0371,
# 0374, 0373 and 0381 were spent as network probes at roughly three minutes each.
#
# Three things are proved here, and the third is the one that stops a fix being worse than the
# defect:
#
#   1. an infrastructure timeout does NOT consume an attempt   (scenes 1, 2)
#   2. consecutive infrastructure failures STOP the claiming    (scene 6)
#   3. a genuine task failure DOES still consume an attempt     (scenes 3, 4)
#
# Scene 0 is the before/after. It runs the SAME stub against the runner as it stood at the last
# commit before this fix (a pinned SHA, not HEAD -- see the scene) and asserts the OLD behaviour,
# so the numbers in this file are a measured delta rather than a claim that the new path looks
# right. Without it "attempts 0" proves only that something set it to 0, which a claim that never
# happened would also do.
#
# The discriminator under test is `terminal_reason == "api_error"`, read off the engine's own
# terminal result event. Not the `num_turns == 1 and total_cost_usd == 0` heuristic the incident
# writeup proposed: scene 2 is that heuristic's blind spot, taken byte for byte from the shape of
# `0379-attempt1` (27 turns, $1.91, timed out). Measured over the live bus twice on 2026-08-18 --
# 291 run jsons at 07:40Z, 297 after the second outage window -- the heuristic misses 8 of 84 and
# then 9 of 88 api_error runs, and the missed ones are always the expensive ones. Note that
# `0379-attempt1` itself is GONE from disk now: a rerun after `reopen` reset attempts to 0 landed
# on attempt 1 again and clobbered the log, which is why scene 2 encodes its shape here instead of
# reading it.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"            # engine/
REPO="$(dirname "$ROOT")"            # the runtime repo
RUNNER="$ROOT/bin/swarm-run"
SWARM="$ROOT/bin/swarm"

TMP="$(mktemp -d)"

# ITS OWN DATABASE, per-run, for the reason test-budget-wiring.sh spells out at length: a suite
# that asserts on stored state cannot share a store with a suite that truncates it, and two copies
# of THIS suite against one fixed name would drop each other's store mid-scene (`scratch-db.sh
# create` opens with DROP DATABASE ... WITH (FORCE)).
if [ -n "${INFRA_TEST_DB:-}" ]; then
  DB="$INFRA_TEST_DB"; DB_PINNED=yes
else
  DB="brain_infra_fail_$$"; DB_PINNED=no
fi
case "$DB" in
  brain)         echo "refusing to run against the live store database 'brain'." >&2; exit 1 ;;
  brain_scratch) echo "refusing to run against the shared scratch database: another lane truncates it." >&2; exit 1 ;;
esac
export BRAIN_PG_DB="$DB"
export ENGINE_SCRATCH_DB="$DB"
# The runner exports this and `post` inherits it, which would make every task here a child of the
# live task that launched the suite. This file posts roots.
unset SWARM_PARENT_TASK

PASS=0
FAIL=0
SKIP=0
ok()   { PASS=$((PASS + 1)); printf '  ok    %s\n' "$1"; }
bad()  { FAIL=$((FAIL + 1)); printf '  FAIL  %s\n' "$1"; [ -n "${2:-}" ] && printf '        %s\n' "$2"; }
# NOT RUN, not FAIL. Task 0372, and the rule it closes is `docs/SUITE-INPUT-RULE.md`: a suite
# whose input is live state must declare NOT RUN with the reason rather than report the
# environment as a code defect. $2 is the CONDITION THAT WAS DETECTED, never a guess at a cause,
# and $3 is the remedy or the word `none`.
skip() { SKIP=$((SKIP + 1)); printf '  NOT RUN  %s\n' "$1"
         [ -n "${2:-}" ] && printf '           condition: %s\n' "$2"
         [ -n "${3:-}" ] && printf '           remedy:    %s\n' "$3"; }
check(){ if [ "$2" = "$3" ]; then ok "$1"; else bad "$1" "wanted [$3], got [$2]"; fi; }
has()  { if grep -q "$2" "$RUNNER"; then ok "$1"; else bad "$1" "not found: $2"; fi; }

echo "test-infra-failure.sh"
echo

if bash -n "$RUNNER" 2>/dev/null; then ok "swarm-run parses"; else bad "swarm-run parses"; fi

# ================================================================ the parser, in isolation
# LIFTED from the runner rather than copied, so this suite cannot drift away from the
# implementation it is testing. A copy would keep passing after the runner changed.
echo
echo "  the discriminator"

python3 - "$RUNNER" "$TMP/infra_parser.py" <<'EXTRACT'
import pathlib, sys
src = pathlib.Path(sys.argv[1]).read_text()
try:
    body = src.split("INFRA_PARSER=$(cat <<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
except IndexError:
    sys.exit("could not find INFRA_PARSER in the runner")
pathlib.Path(sys.argv[2]).write_text(body)
EXTRACT
if [ -s "$TMP/infra_parser.py" ]; then ok "INFRA_PARSER extracted from the runner"
else bad "INFRA_PARSER extracted from the runner"; echo; echo "$PASS passed, $((FAIL + 1)) failed"; exit 1; fi

P="$TMP/infra_parser.py"

# classifies <label> <want-rc> <run-json-body>
classifies() {
  local label="$1" want="$2" body="$3" rc
  printf '%s\n' "$body" > "$TMP/case.json"
  : > "$TMP/case.stream.jsonl"
  python3 "$P" "$TMP/case.json" "$TMP/case.stream.jsonl" >"$TMP/case.out" 2>/dev/null
  rc=$?
  check "$label" "$rc" "$want"
}

# The exact result event from the night, byte for byte off ~/.swarm/runs/0379-attempt2.json.
classifies "the 2026-08-18 timeout is infrastructure" 0 \
  '{"type":"result","is_error":true,"num_turns":1,"total_cost_usd":0,"terminal_reason":"api_error","api_error_status":null,"result":"Request timed out"}'
# THE ONE THE PROPOSED HEURISTIC MISSES. 0379-attempt1: 27 turns, $1.91 of real work, same outage.
classifies "an EXPENSIVE timeout is infrastructure too" 0 \
  '{"type":"result","is_error":true,"num_turns":27,"total_cost_usd":1.9117234999999997,"terminal_reason":"api_error","api_error_status":null,"result":"Request timed out"}'
classifies "a 500 is infrastructure" 0 \
  '{"type":"result","is_error":true,"num_turns":4,"total_cost_usd":0.2,"terminal_reason":"api_error","api_error_status":500,"result":"API Error: 500 Internal server error."}'
classifies "a dropped connection is infrastructure" 0 \
  '{"type":"result","is_error":true,"num_turns":9,"total_cost_usd":0.8,"terminal_reason":"api_error","api_error_status":null,"result":"API Error: Connection lost mid-response."}'

# THE OTHER SIDE, and it matters more than it looks. `reopen` resets attempts to 0 every time
# (transitions.py:702-707), so a classifier loose enough to match a real failure requeues it
# FOREVER with no ladder left to stop it. These are the cases that bound it.
classifies "an ordinary completed run is NOT infrastructure" 1 \
  '{"type":"result","is_error":false,"num_turns":14,"total_cost_usd":0.42,"terminal_reason":"completed","result":"did the work"}'
classifies "a completed run that ERRORED is NOT infrastructure" 1 \
  '{"type":"result","is_error":true,"num_turns":14,"total_cost_usd":0.42,"terminal_reason":"completed","result":"the agent gave up"}'
classifies "a killed run is NOT infrastructure" 1 \
  '{"type":"result","is_error":true,"num_turns":75,"total_cost_usd":4.9,"terminal_reason":"aborted_streaming","result":null}'
# A 4xx is the caller's fault. Without this bound a task whose prompt the API always refuses is
# requeued unspent forever, which is the same shredder pointed the other way.
classifies "a 400 is the caller's fault, NOT infrastructure" 1 \
  '{"type":"result","is_error":true,"num_turns":1,"total_cost_usd":0,"terminal_reason":"api_error","api_error_status":400,"result":"API Error: 400 prompt is too long"}'
classifies "a 403 is the caller's fault, NOT infrastructure" 1 \
  '{"type":"result","is_error":true,"num_turns":1,"total_cost_usd":0,"terminal_reason":"api_error","api_error_status":403,"result":"API Error: 403 forbidden"}'
# 429 IS a wall, and the wall branch runs first. Reaching here means its wording was unreadable,
# and waiting is the right answer for it either way.
classifies "a 429 whose wording we could not read is infrastructure" 0 \
  '{"type":"result","is_error":true,"num_turns":1,"total_cost_usd":0,"terminal_reason":"api_error","api_error_status":429,"result":"quota"}'
classifies "no result event is NOT classifiable" 1 '{"type":"system","subtype":"init"}'
classifies "garbage is NOT classifiable" 1 'not json at all'

# THE FALLBACK THAT `0379-attempt1` MAKES NECESSARY. That run has a 16KB log carrying a result and
# NO run json on disk at all, because `$RUN_JSON` and `$STREAM` are keyed on (task, attempt) and a
# second terminal on the same attempt shares them. Read only the run json and the most expensive
# run of the whole incident classifies as unknown and spends an attempt.
rm -f "$TMP/gone.json"
cat > "$TMP/fallback.stream.jsonl" <<'S'
{"type":"system","subtype":"init","session_id":"s1"}
{"type":"result","session_id":"s1","num_turns":27,"total_cost_usd":1.91,"terminal_reason":"api_error","api_error_status":null,"result":"Request timed out"}
S
python3 "$P" "$TMP/gone.json" "$TMP/fallback.stream.jsonl" >/dev/null 2>&1
check "a missing run json falls back to the stream" "$?" "0"
python3 "$P" "$TMP/gone.json" "$TMP/also-gone.jsonl" >/dev/null 2>&1
check "both files missing exits 1" "$?" "1"

echo
echo "  the wiring (a correct discriminator wired to the old path fixes nothing)"

has "an infra failure calls reopen"        'reason "infrastructure failure, not a task failure'
has "  and says the attempt was not charged" 'attempt \$TASK_ATTEMPTS not charged'
has "  and records the outcome as infra"   'outcome infra_failure'
has "the infra streak is counted SEPARATELY from FAILURES" 'INFRA_FAILURES=\$((INFRA_FAILURES + 1))'
has "an infra streak parks instead of claiming" 'NOT claiming another task'
has "the park probes the network"          'net_reachable'
has "a genuine failure still calls fail"   'fail "\$TASK_ID" --reason "\$REASON"'
has "a genuine failure still spends"       'FAILURES=\$((FAILURES + 1))'

# ORDERING. The subscription wall is `api_error` too (status 429, all 41 in the live corpus), so
# the infra branch asking first would swallow every wall and the account ring would never rotate.
# And asking after `fail` would be asking after the attempt was already spent.
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


active = at('TASK_STATE" = "active"', "the active branch")
state  = at('TASK_STATE="$("$SWARM" state', "the store read")
limit  = at('LIMIT_UNTIL="$(limit_reset_epoch', "the refusal check")
infra  = at('INFRA_REASON="$(infra_failure_reason', "the infrastructure check")
failed = at('"$SWARM" fail "$TASK_ID" --reason "$REASON"', "the fail path")

rc = 0


def rule(label, cond, detail):
    global rc
    print(f"  {'ok   ' if cond else 'FAIL '} {label}")
    if not cond:
        print(f"        {detail}")
        rc = 1


rule("the infra check sits inside the active branch", active < infra,
     f"active@{active} infra@{infra}")
rule("the store is asked before the run is classified", state < infra,
     f"state@{state} infra@{infra}")
rule("the subscription wall is asked FIRST (a wall is api_error too)", limit < infra,
     f"limit@{limit} infra@{infra}")
rule("the infra check runs BEFORE the fail path", infra < failed,
     f"infra@{infra} fail@{failed}")
sys.exit(rc)
ORDER
if [ $? -eq 0 ]; then PASS=$((PASS + 4)); else FAIL=$((FAIL + 1)); fi

# ================================================================ live: the real runner
echo
echo "  live: the real runner, a stub engine, and the store"

drop_private_store() {
  [ "$DB_PINNED" = no ] || return 0
  if [ "$FAIL" -ne 0 ]; then
    printf '\n  post-mortem store kept (this run was red): BRAIN_PG_DB=%s\n' "$DB"
    return 0
  fi
  ENGINE_SCRATCH_DB="$DB" "$ROOT/bin/scratch-db.sh" drop >/dev/null 2>&1
}
# ARMED BEFORE `create`, because `create` DROPs and CREATEs and only then migrates, so "create
# failed" routinely means "the database is on disk, half migrated".
trap drop_private_store EXIT

if ! CREATE_OUT="$("$ROOT/bin/scratch-db.sh" create 2>&1)"; then
  bad "a private store was built at $DB" \
      "scratch-db.sh create failed: $(printf '%s' "$CREATE_OUT" | tail -3 | tr '\n' ' ')"
  echo; echo "$PASS passed, $FAIL failed"; exit 1
fi
ok "a private store was built at $DB"

AGENT="if$$"
LANE="if$$"
cleanup_live() { rm -rf "$TMP"; drop_private_store; }
trap cleanup_live EXIT           # REPLACES the trap above, so it carries the teardown forward

mkdir -p "$TMP/bin" "$TMP/acct1" "$TMP/acct2" "$TMP/work" "$TMP/state" "$TMP/out"

# Two account directories, so the wall scene's `rotate_or_wait` rotates and returns instead of
# sleeping out the window and hanging this suite.
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

# The stub engine. Stream-json on stdout is the only contract the runner has with it.
cat > "$TMP/bin/claude" <<'STUB'
#!/usr/bin/env python3
import json, os, sys, uuid

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

LIMIT = "You've hit your session limit · resets 7:10pm (Europe/Bucharest)"

# The result event from ~/.swarm/runs/0379-attempt2.json, field for field. `duration_api_ms` is 0
# and `subtype` is "success" in the real one too -- the engine calls a timed-out run a successful
# result event carrying an error, which is precisely why `is_error` alone cannot discriminate.
TIMEOUT = {"type": "result", "subtype": "success", "session_id": sid, "is_error": True,
           "num_turns": 1, "total_cost_usd": 0, "stop_reason": "stop_sequence",
           "terminal_reason": "api_error", "api_error_status": None,
           "duration_api_ms": 0, "result": "Request timed out"}

if mode == "infra":
    emit({"type": "assistant", "message": {"content": [{"type": "text", "text": "Request timed out"}]}})
    emit(TIMEOUT)
    sys.exit(1)               # rc=1 with the task still active: the incident's exact shape

if mode == "infra_expensive":
    # 0379-attempt1: 27 turns and $1.91 of real work, killed mid-run by the same outage. The
    # `num_turns == 1 and cost == 0` heuristic charges this one an attempt. The typed field does not.
    ev = dict(TIMEOUT, num_turns=27, total_cost_usd=1.9117234999999997)
    emit(ev)
    sys.exit(1)

if mode == "client_error":
    # NOT infrastructure. The API refused this request on its own merits and will refuse it again,
    # so it must keep spending attempts or it requeues forever.
    emit(dict(TIMEOUT, api_error_status=400,
              result="API Error: 400 prompt is too long: 250000 tokens > 200000 maximum"))
    sys.exit(1)

if mode == "ratelimit":
    # A wall, and a wall is `api_error` too. The refusal branch must win: it is the one that
    # rotates the account ring. Both branches reopen, so the store cannot tell them apart -- the
    # assertion is on which line the runner printed.
    emit({"type": "assistant", "message": {"content": [{"type": "text", "text": LIMIT}]}})
    emit(dict(TIMEOUT, api_error_status=429, result=LIMIT))
    sys.exit(1)

# normal: a run that did real work and never reported. The ordinary unreported failure, and the
# control for the whole file: without it, a branch that reopened everything would pass every
# other scene in here.
emit({"type": "result", "subtype": "success", "session_id": sid, "num_turns": 3,
      "total_cost_usd": 0.4200, "is_error": False, "terminal_reason": "completed",
      "result": "did the work, forgot to call done"})
sys.exit(0)
STUB
chmod +x "$TMP/bin/claude"

export ENGINE_HOME="$TMP/state"
export ENGINE_CONFIG="$TMP/config.json"
export PATH="$TMP/bin:$PATH"
export SWARM_ONCE=1

meta() {
  "$SWARM" show "$1" --json 2>/dev/null \
    | python3 -c "import json,sys; print(json.load(sys.stdin)['task']['$2'])" 2>/dev/null
}

PREV_TASK=""
# run_scene <mode> <label> [runner]
run_scene() {
  local mode="$1" label="$2" runner="${3:-$RUNNER}"
  # Each scene retires the one before it. Every scene here leaves its task in `inbox`, and the
  # claim orders by (priority, score, id ASC), so without this the next scene re-claims the
  # PREVIOUS scene's requeued task and asserts against the wrong row.
  [ -n "$PREV_TASK" ] && "$SWARM" cancel "$PREV_TASK" --reason "scene complete" >/dev/null 2>&1
  SCENE_TASK="$("$SWARM" post --lane "$LANE" --title "$label" --for-agents --workdir "$TMP/work" 2>"$TMP/post-$mode.err" | awk '{print $1}')"
  if [ -z "$SCENE_TASK" ]; then
    bad "$mode: the scene's task was posted" \
        "post printed nothing. stderr: $(head -c 300 "$TMP/post-$mode.err")"
    return 1
  fi
  PREV_TASK="$SCENE_TASK"
  STUB_MODE="$mode" timeout 180 "$runner" "$AGENT" > "$TMP/out/$mode.log" 2>&1
  # THE ANTI-VACUUM ASSERTION. A task that was never claimed reads `inbox, attempts 0`, which is
  # exactly what a correctly-unspent infrastructure failure reads. Prove the run happened first.
  if grep -q "claimed $SCENE_TASK " "$TMP/out/$mode.log"; then
    ok "$mode: the runner claimed $SCENE_TASK and ran it"
  else
    bad "$mode: the runner claimed $SCENE_TASK and ran it" \
        "no claim line in the runner output; every assertion below would pass vacuously"
  fi
}

# --------------------------------------------------------- scene 0: BEFORE, the runner as it was
# The delta this whole file reports. Without it, "attempts 0" proves only that something set it
# to 0. The old runner is checked out into its own tree with the repo's real `roles/`, `budget/`
# and `bin/swarm` symlinked beside it, because it resolves all three from its own path.
#
# PINNED TO A SHA, NOT TO `HEAD`. It was `HEAD` while the fix was being written and that is a trap
# with a fuse on it: the moment the fix is committed, HEAD carries the FIXED runner and this scene
# asserts the defect against a runner that no longer has it, going red for the one reason that
# means everything is fine. b1b8054 is the last commit that touched `engine/bin/swarm-run` before
# task 0400 ("D4: the claimer predicate, the paging join, the parent cycle guard, and six new
# suites"), and it is the runner whose shape produced the 2026-08-18 incident.
BEFORE_SHA="${INFRA_TEST_BEFORE_SHA:-b1b8054b9395a145d5acd48333fc9b0f50abb794}"
echo
echo "  scene 0: BEFORE -- the same timeout against the runner at ${BEFORE_SHA:0:7}"
mkdir -p "$TMP/before/engine/bin"
if git -C "$REPO" show "$BEFORE_SHA:engine/bin/swarm-run" > "$TMP/before/engine/bin/swarm-run" 2>/dev/null \
   && [ -s "$TMP/before/engine/bin/swarm-run" ]; then
  chmod +x "$TMP/before/engine/bin/swarm-run"
  ln -sfn "$REPO/roles"  "$TMP/before/roles"
  ln -sfn "$REPO/budget" "$TMP/before/budget"
  ln -sfn "$ROOT/bin/swarm" "$TMP/before/engine/bin/swarm"
  run_scene infra "infra: BEFORE, the runner at $BEFORE_SHA" "$TMP/before/engine/bin/swarm-run"
  T0="$SCENE_TASK"
  check "0. BEFORE: the timeout was filed as an unreported failure" \
        "$(grep -c 'unreported: engine exited rc=1' "$TMP/out/infra.log")" "1"
  check "0. BEFORE: state"                    "$(meta "$T0" state)"    "inbox"
  check "0. BEFORE: attempts SPENT -- the defect" "$(meta "$T0" attempts)" "1"
else
  # WHY THIS IS NOT A FAIL. Task 0372.
  #
  # This program mandates that a suite result is only trusted from a `git archive` export (task
  # 0336, restated by 0359: "the shared working tree carries every lane's uncommitted edits and
  # is not what anyone will clone"). An export has no `.git`. So this scene, which reads a blob
  # out of history, can NEVER run under the method the program requires, at any sha, ever -- and
  # for weeks the whole engine runner therefore exited 1 and printed SOMETHING FAILED on every
  # honest export verification, over a tree in which nothing had failed. A gate that always
  # fires for a reason unrelated to the thing it gates stops being read.
  #
  # THE OLD MESSAGE GUESSED, AND GUESSED WRONG. It said "Is this a shallow clone?" of a tree that
  # was neither shallow nor a clone: it is not a git work tree at all. Someone would have gone
  # looking for a clone. The branches below name the condition that was DETECTED.
  #
  # The other 58 assertions in this file are unaffected and still run. Scene 0 alone stands down,
  # and the banner says 58 of 62 rather than pretending 58 is the whole suite.
  if ! git -C "$REPO" rev-parse --git-dir >/dev/null 2>&1; then
    skip "0. the before/after against the runner at ${BEFORE_SHA:0:7} (4 assertions)" \
         "$REPO is not a git work tree, so there is no history to read a blob out of. This is what a \`git archive\` export is, and the export IS this program's mandated verification method." \
         "none, and none is wanted. See docs/SUITE-INPUT-RULE.md: this is an INHERENT NOT RUN and does not turn the runner red."
  elif ! git -C "$REPO" cat-file -e "$BEFORE_SHA:engine/bin/swarm-run" 2>/dev/null; then
    skip "0. the before/after against the runner at ${BEFORE_SHA:0:7} (4 assertions)" \
         "$REPO IS a git work tree, and $BEFORE_SHA:engine/bin/swarm-run is not reachable in it. A shallow clone or a different repo would both look like this." \
         "git -C $REPO fetch --unshallow, or set INFRA_TEST_BEFORE_SHA to a sha this repo has."
  else
    bad "0. the runner at $BEFORE_SHA was checked out for the before/after" \
        "the object IS reachable and the checkout still produced nothing at $TMP/before/engine/bin/swarm-run. That is a real failure, not an absent history."
  fi
fi

# --------------------------------------------------------- scene 1: AFTER, the same timeout
echo
echo "  scene 1: AFTER -- the same timeout against the fixed runner"
run_scene infra "infra: a network timeout"
T1="$SCENE_TASK"
check "1. AFTER: the timeout was recognised as infrastructure" \
      "$(grep -c 'engine failed on INFRASTRUCTURE' "$TMP/out/infra.log")" "1"
check "1. AFTER: it was NOT filed as an unreported failure" \
      "$(grep -c 'unreported: engine exited' "$TMP/out/infra.log")" "0"
check "1. AFTER: state"                       "$(meta "$T1" state)"    "inbox"
check "1. AFTER: attempts NOT SPENT"          "$(meta "$T1" attempts)" "0"

# --------------------------------------------------------- scene 2: the expensive timeout
# 0379-attempt1's shape. The heuristic the writeup proposed charges this one; the typed field
# does not, and this run is the reason the typed field was chosen.
echo
echo "  scene 2: the timeout that did \$1.91 of real work first"
run_scene infra_expensive "infra: an expensive timeout"
T2="$SCENE_TASK"
check "2. expensive timeout: recognised as infrastructure" \
      "$(grep -c 'engine failed on INFRASTRUCTURE' "$TMP/out/infra_expensive.log")" "1"
check "2. expensive timeout: attempts NOT SPENT" "$(meta "$T2" attempts)" "0"

# --------------------------------------------------------- scene 3: the control
# THE DIRECTION A BAD FIX BREAKS. A classifier that stopped counting real failures would pass
# every scene above and be worse than the defect it replaced.
echo
echo "  scene 3: the control -- a genuine failure must still spend an attempt"
run_scene normal "infra: an ordinary unreported failure"
T3="$SCENE_TASK"
check "3. genuine failure: filed as unreported" \
      "$(grep -c 'unreported: engine finished but never called' "$TMP/out/normal.log")" "1"
check "3. genuine failure: NOT called infrastructure" \
      "$(grep -c 'engine failed on INFRASTRUCTURE' "$TMP/out/normal.log")" "0"
check "3. genuine failure: state"             "$(meta "$T3" state)"    "inbox"
check "3. genuine failure: attempts SPENT"    "$(meta "$T3" attempts)" "1"

# --------------------------------------------------------- scene 4: the 4xx bound
echo
echo "  scene 4: a request the API refuses on its merits must keep spending"
run_scene client_error "infra: a 400 the API will refuse every time"
T4="$SCENE_TASK"
check "4. a 400: NOT called infrastructure" \
      "$(grep -c 'engine failed on INFRASTRUCTURE' "$TMP/out/client_error.log")" "0"
check "4. a 400: attempts SPENT"              "$(meta "$T4" attempts)" "1"

# --------------------------------------------------------- scene 5: the wall keeps its branch
echo
echo "  scene 5: a subscription wall is api_error too, and keeps its own branch"
run_scene ratelimit "infra: a subscription refusal"
T5="$SCENE_TASK"
check "5. wall: taken by the refusal branch" \
      "$(grep -c 'engine refused on a subscription limit' "$TMP/out/ratelimit.log")" "1"
check "5. wall: NOT swallowed by the infra branch" \
      "$(grep -c 'engine failed on INFRASTRUCTURE' "$TMP/out/ratelimit.log")" "0"
check "5. wall: the account ring still rotated" \
      "$(grep -c 'rate limited, rotating to account' "$TMP/out/ratelimit.log")" "1"
check "5. wall: attempts NOT SPENT"           "$(meta "$T5" attempts)" "0"

# --------------------------------------------------------- scene 6: the park
# THE SECOND DEFECT, and the one that turned an outage into a shredder. `sleep 300; claim another
# task` is the right reflex for a transient engine fault and the wrong one for a dead dependency.
# The runner is let loose here WITHOUT SWARM_ONCE, against six queued tasks and a probe pointed at
# an unroutable address, and the assertion is that it stops claiming rather than spending them.
#
# 10.255.255.1 is RFC1918 space with nothing on it: the connect hangs until the timeout rather
# than being refused, which is what a dead router looks like from inside a process. A DNS name
# that does not resolve would fail INSTANTLY and would not exercise the same path.
echo
echo "  scene 6: three infrastructure failures in a row stop the claiming"
[ -n "$PREV_TASK" ] && "$SWARM" cancel "$PREV_TASK" --reason "scene complete" >/dev/null 2>&1
PARK_TASKS=()
for i in 1 2 3 4 5 6; do
  PARK_TASKS+=("$("$SWARM" post --lane "$LANE" --title "infra park: queued task $i" --for-agents --workdir "$TMP/work" 2>/dev/null | awk '{print $1}')")
done
if [ -z "${PARK_TASKS[0]:-}" ]; then
  bad "6. six tasks were queued for the park scene" "post printed nothing"
else
  ok "6. six tasks were queued for the park scene"
  SWARM_ONCE=0 STUB_MODE=infra \
    SWARM_NET_PROBE_URL="https://10.255.255.1/" SWARM_NET_PROBE_TIMEOUT=2 \
    timeout 90 "$RUNNER" "$AGENT" > "$TMP/out/park.log" 2>&1
  PARK_RC=$?
  # timeout kills it at 90s, so 124 is the EXPECTED exit: a parked runner does not return.
  check "6. the runner had to be killed -- it never came back to claim" "$PARK_RC" "124"
  check "6. it parked instead of claiming again" \
        "$(grep -c 'NOT claiming another task' "$TMP/out/park.log")" "1"
  check "6. it did NOT fall through to the old 300s-then-claim backoff" \
        "$(grep -c '3 consecutive engine failures, sleeping 300s' "$TMP/out/park.log")" "0"
  check "6. it probed the network while parked" \
        "$(grep -c 'still parked' "$TMP/out/park.log")" "1"
  # THE NUMBER THAT MATTERS. Exactly three engine runs, then nothing. The incident's runner did
  # three, slept, and did five more.
  check "6. exactly three engine runs before it stopped" \
        "$(grep -c 'engine failed on INFRASTRUCTURE' "$TMP/out/park.log")" "3"
  # And the queue is intact. In the incident this is where 0366, 0371, 0374, 0373 and 0381 died.
  PARK_SPENT=0
  PARK_TOUCHED=0
  for t in "${PARK_TASKS[@]}"; do
    [ -n "$t" ] || continue
    [ "$(meta "$t" attempts)" = "0" ] || PARK_SPENT=$((PARK_SPENT + 1))
    grep -q "claimed $t " "$TMP/out/park.log" && PARK_TOUCHED=$((PARK_TOUCHED + 1))
  done
  check "6. not one queued task spent an attempt" "$PARK_SPENT" "0"
  # The claim orders by id, so the reopened task is re-claimed ahead of the untouched ones: all
  # three runs land on the SAME task and the other five are never seen. That is the whole point.
  check "6. only ONE task was ever claimed; five were never touched" "$PARK_TOUCHED" "1"
fi

echo
# THE DENOMINATOR, WITH THE NOT-RUN COUNT ON THE SAME LINE. A suite that quietly ran 58 of its
# 62 assertions and printed only "58 passed" would be claiming coverage it did not have.
echo "$PASS passed, $FAIL failed, $SKIP scene(s) NOT RUN  (of $((PASS + FAIL + SKIP * 4)) assertions declared)"
if [ $((PASS + FAIL)) -eq 0 ]; then   # DENOMINATOR
  echo "DENOMINATOR: 0 comparisons made. A verdict over an empty set is not a pass."
  exit 2
fi
[ "$FAIL" -eq 0 ] || exit 1
# 77 is NOT RUN, the automake convention, and `engine/tests/run-all.sh` reads it. Everything that
# could run passed; at least one scene could not run and said which input it lacked. See
# docs/SUITE-INPUT-RULE.md.
[ "$SKIP" -eq 0 ] || exit 77
