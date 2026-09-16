#!/usr/bin/env bash
# The rate-limit gate. Engine-free, deterministic, no network, about a second.
#
# Ported from internal/swarm-admiral/tests/test-ratelimit.sh. Same cases, same wiring assertions,
# re-pointed at engine/bin/swarm-run and extended for the two incident fixes this port carries.
#
# What it protects, in one line: A SUBSCRIPTION REFUSAL MUST NEVER BE CHARGED TO A TASK.
#
# The incident. 2026-08-14, six terminals hit a five-hour session ceiling within minutes of each
# other. Each refusal returned rc=1 with the task still active, so reconciliation called `swarm
# fail`, and two of those blocks a task and raises an operator question. One billing ceiling
# produced 18 blocked tasks and 18 spurious questions, all 23 queued tasks deadlocked behind their
# dependencies, 10 doctor CRITICALs, and the fleet sat idle for ELEVEN HOURS with all six runners
# healthy and nothing wrong with any of the work. 41 attempt logs carried the same string.
#
# Two halves are tested. The parser, against real logs from that night plus the wordings we have
# not seen yet. And the WIRING, statically, because the parser being right is worth nothing if the
# runner still calls `fail`.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNNER="$ROOT/bin/swarm-run"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

PASS=0
FAIL=0

ok()   { PASS=$((PASS + 1)); printf '  ok    %s\n' "$1"; }
bad()  { FAIL=$((FAIL + 1)); printf '  FAIL  %s\n' "$1"; [ -n "${2:-}" ] && printf '        %s\n' "$2"; }
check(){ if [ "$2" = "$3" ]; then ok "$1"; else bad "$1" "wanted [$3], got [$2]"; fi; }

echo "test-ratelimit.sh"
echo

if bash -n "$RUNNER" 2>/dev/null; then ok "swarm-run parses"; else bad "swarm-run parses"; fi

# ---------------------------------------------------------------- the parser
# LIFTED out of the runner rather than copied, so this suite cannot drift away from the
# implementation it is testing. A copy would keep passing after the runner changed.
python3 - "$RUNNER" "$TMP/limit_parser.py" <<'EXTRACT'
import pathlib, sys
src = pathlib.Path(sys.argv[1]).read_text()
try:
    body = src.split("LIMIT_PARSER=$(cat <<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
except IndexError:
    sys.exit("could not find LIMIT_PARSER in the runner")
pathlib.Path(sys.argv[2]).write_text(body)
EXTRACT
if [ -s "$TMP/limit_parser.py" ]; then ok "LIMIT_PARSER extracted from the runner"
else bad "LIMIT_PARSER extracted from the runner"; echo; echo "$PASS passed, $((FAIL + 1)) failed"; exit 1; fi

P="$TMP/limit_parser.py"

parses() {
  local label="$1" want_rc="$2" text="$3" f rc
  f="$TMP/case.log"
  printf '%s\n' "$text" > "$f"
  python3 "$P" "$f" >"$TMP/out" 2>/dev/null
  rc=$?
  check "$label" "$rc" "$want_rc"
}

echo
echo "  detection"

# The exact string from the night, byte for byte.
parses "the 2026-08-14 wording" 0 \
  "[text] You've hit your session limit · resets 7:10pm (Europe/Bucharest)"
parses "usage limit wording" 0 "Claude usage limit reached. Your limit will reset at 7pm"
parses "weekly limit wording" 0 "You've hit your weekly limit"
parses "24h clock, no am/pm" 0 "usage limit reached, resets 19:10"

# THE OTHER SIDE MATTERS MORE THAN IT LOOKS. Detection errs wide on purpose -- a false positive
# costs one requeue, a false negative cost eighteen tasks -- but `reopen` resets the attempt
# counter every time, so a pattern loose enough to match an ordinary success would requeue a
# genuinely failed task FOREVER, with no ladder left to stop it. These are the cases that bound it.
parses "ordinary success is not a limit" 1 \
  "[out] Bash: done. 0007 done, summary written. session=abc turns=14 error=False"
parses "a real failure is not a limit" 1 \
  "engine exited rc=1 without reporting, traceback: KeyError('lane')"
parses "the word limit alone is not a limit" 1 \
  "[text] I set a LIMIT 50 on the query and the reset button is unrelated"
parses "empty log is not a limit" 1 ""
parses "a task whose SUMMARY discusses rate limits is not a limit" 1 \
  "[out] swarm done 0042 --summary \"documented the reset behaviour and the limit ladder\""

python3 "$P" "$TMP/definitely-not-here.log" >/dev/null 2>&1
check "a missing file exits 1" "$?" "1"

echo
echo "  the reset clock"

epoch_for() {
  printf '%s\n' "$2" > "$TMP/case.log"
  python3 "$P" "$TMP/case.log" 2>/dev/null
}

E1="$(epoch_for x "You've hit your session limit · resets 7:10pm (Europe/Bucharest)")"
if [ -n "$E1" ] && [ "$E1" -gt "$(date +%s)" ]; then
  ok "a parsed reset is always in the future"
else
  bad "a parsed reset is always in the future" "got [$E1]"
fi

# 7:10pm in Bucharest and 7:10pm in Los Angeles are ten hours apart. If the zone were ignored
# these two would be equal, and the runner would wake ten hours early every time.
E2="$(epoch_for x "You've hit your session limit · resets 7:10pm (America/Los_Angeles)")"
if [ -n "$E2" ] && [ "$E2" -ne "$E1" ]; then
  ok "the named timezone changes the answer"
else
  bad "the named timezone changes the answer" "Bucharest [$E1] vs Los Angeles [$E2]"
fi

# A refusal we cannot time is still a refusal. Returning "no limit" here would hand the task
# straight back to the fail path, which is the whole defect.
E3="$(epoch_for x "You've hit your session limit")"
if [ -n "$E3" ] && [ "$E3" -gt "$(date +%s)" ]; then
  ok "a limit with no printed time still yields a wait"
else
  bad "a limit with no printed time still yields a wait" "got [$E3]"
fi

E4="$(epoch_for x "usage limit reached, resets 99:99")"
NOW="$(date +%s)"
if [ -n "$E4" ] && [ "$E4" -gt "$NOW" ] && [ "$E4" -lt "$((NOW + 7200))" ]; then
  ok "an impossible clock falls back to a short wait"
else
  bad "an impossible clock falls back to a short wait" "got [$E4]"
fi

echo
echo "  the wiring (static: a correct parser wired to the old path fixes nothing)"

has() {
  if grep -q "$2" "$RUNNER"; then ok "$1"; else bad "$1" "not found: $2"; fi
}

# THE LOAD-BEARING ASSERTION OF THE WHOLE FILE. On the active branch a limit must reach `reopen`,
# which resets attempts to 0, and never `fail`, which spends one.
has "a limit calls reopen"                      'reopen "\$TASK_ID" --from "\$AGENT"'
has "the limit branch requeues unspent"         'attempt \$TASK_ATTEMPTS not charged'
has "a wall does not trip the 3-strike backoff" 'FAILURES=0    # a wall is not a fault'
has "the planner does not commit a refused tick" 'tick NOT committed'
has "rotation reaches the engine"               'local active_dir='
has "config_dirs is read"                       'CFG_CONFIG_DIRS'
has "the wait is clamped"                       '21600'
has "a missing account dir is skipped, not fatal" 'skipping account dir'
has "permission_mode comes from config"         'permission-mode "\$CFG_PERMISSION_MODE"'

# THE INCIDENT FIX, 2026-08-16. The shutdown trap must release CONDITIONALLY. An unconditional
# release is how a dying agent unclaims a task another agent legitimately holds.
has "the shutdown trap releases, it does not fail" 'release "\$CURRENT_TASK" --agent "\$AGENT"'
if grep -q 'fail "\$CURRENT_TASK"' "$RUNNER"; then
  bad "the shutdown trap no longer calls fail on the held task" "still present"
else
  ok "the shutdown trap no longer calls fail on the held task"
fi

# The check must sit INSIDE the active branch. Above it, a limit string in the log of a task the
# agent went on to finish would reopen completed work and throw it away.
python3 - "$RUNNER" <<'ORDER'
import pathlib, sys
src = pathlib.Path(sys.argv[1]).read_text().splitlines()
try:
    active = next(i for i, l in enumerate(src) if 'TASK_STATE" = "active"' in l)
    limit  = next(i for i, l in enumerate(src) if 'LIMIT_UNTIL="$(limit_reset_epoch' in l)
    failed = next(i for i, l in enumerate(src) if '"$SWARM" fail "$TASK_ID" --reason "$REASON"' in l)
except StopIteration:
    print("  FAIL  the limit check sits inside the active branch")
    print("        could not locate one of the three anchors")
    sys.exit(1)
if active < limit < failed:
    print("  ok    the limit check sits inside the active branch, before the fail path")
else:
    print("  FAIL  the limit check sits inside the active branch, before the fail path")
    print(f"        active@{active} limit@{limit} fail@{failed}")
    sys.exit(1)
ORDER
if [ $? -eq 0 ]; then PASS=$((PASS + 1)); else FAIL=$((FAIL + 1)); fi

# And the store is asked BEFORE the log is read. The store is the authority on outcome; the log
# only explains it. Reading the log first would throw away work the agent went on to finish.
python3 - "$RUNNER" <<'ORDER2'
import pathlib, sys
src = pathlib.Path(sys.argv[1]).read_text().splitlines()
state = next((i for i, l in enumerate(src) if 'TASK_STATE="$("$SWARM" state' in l), None)
limit = next((i for i, l in enumerate(src) if 'LIMIT_UNTIL="$(limit_reset_epoch' in l), None)
if state is not None and limit is not None and state < limit:
    print("  ok    the store is asked before the log is read")
    sys.exit(0)
print("  FAIL  the store is asked before the log is read")
print(f"        state@{state} limit@{limit}")
sys.exit(1)
ORDER2
if [ $? -eq 0 ]; then PASS=$((PASS + 1)); else FAIL=$((FAIL + 1)); fi

echo
echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ] || exit 1
