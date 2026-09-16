#!/usr/bin/env bash
# THE PROGRAM GATE.
#
# Ported from internal/swarm-admiral/tests/test-bus.sh, `t_claim_race` (line 325), assertion for
# assertion. Nothing in its intent is amended: the same ten checks run, in the same order,
# against the same claim semantics. What changed underneath is the mechanism -- `os.rename` on
# tasks/inbox -> tasks/active became `SELECT ... FOR UPDATE SKIP LOCKED` -- and that is exactly
# what this test exists to interrogate.
#
# The property, from swarm-admiral's own comment on that function:
#
#   "THE CLAIM RACE. The load bearing property of the whole system: a task is handed to exactly
#    one agent, no duplicates, none lost, total conserved."
#
# The gate D4 was given names 12 claimers against 20 items. The suite the test came from uses 30
# items and 40 claimers. Both run here, because the ported test's own parameters are the ones it
# was verified against and dropping to a smaller shape would be a quieter test than the one being
# replaced. The 12x20 case is reported first because it is the stated gate.
#
# Claimers are separate PROCESSES, not threads, blocked on a start file so they collide instead
# of queueing behind each other's interpreter startup. That is the file-bus test's own device and
# it matters more here, not less: twelve processes on twelve connections is what actually
# exercises SKIP LOCKED.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SWARM="$ROOT/engine/bin/swarm"
SCRATCH="$ROOT/engine/bin/scratch-db.sh"
export BRAIN_PG_DB="${ENGINE_SCRATCH_DB:-brain_scratch}"
# The runner exports this and `post` inherits from it by design, which would make every task in
# this test a child of the live task that launched it. Clear it: the test posts roots.
unset SWARM_PARENT_TASK

# Reconcile this database to `migrations/` before the first race. Task 0153. `run-all.sh` does this
# once for the whole run (task 0148); a suite run BY ITSELF did not. This one matters more than
# most: it is THE PROGRAM GATE, and a gate that goes red because `post` died on a column this
# database never received says nothing at all about whether a task is handed to exactly one agent.
#
# `migrate` and not `create`: create does DROP DATABASE ... WITH (FORCE), and dropping this
# database under a sibling lane mid-run is a worse failure than the one being fixed.
if ! "$SCRATCH" migrate; then
  printf 'test-claim-race.sh: could not bring %s up to migrations/. Not running: the gate below\n' \
    "$BRAIN_PG_DB"
  printf 'would be about the schema, not about the claim.\n'
  exit 1
fi

TMPROOT="$(mktemp -d)"
trap 'rm -rf "$TMPROOT"' EXIT

PASS=0; FAIL=0
ok()  { PASS=$((PASS + 1)); printf '  ok    %s\n' "$1"; }
bad() { FAIL=$((FAIL + 1)); printf '  FAIL  %s\n' "$1"; [ -n "${2:-}" ] && printf '        %s\n' "$2"; }
assert_eq() { if [ "$2" = "$3" ]; then ok "$1"; else bad "$1" "wanted [$3], got [$2]"; fi; }

count_state() { "$SWARM" ls --state "$1" --json | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))'; }
meta() { "$SWARM" show "$1" --json | python3 -c "import json,sys; print(json.load(sys.stdin)['task']['$2'])"; }

race() {
  local N="$1" C="$2" i
  printf '\n=== %s claimers against %s items ===\n' "$C" "$N"
  "$SCRATCH" truncate >/dev/null 2>&1

  for ((i = 1; i <= N; i++)); do
    "$SWARM" post --lane race --title "race task $i" --for-agents --workdir /tmp >/dev/null
  done
  assert_eq "race setup posted $N tasks" "$(count_state inbox)" "$N"

  local od="$TMPROOT/race-out-$N-$C"
  rm -rf "$od"; mkdir -p "$od"

  for ((i = 1; i <= C; i++)); do
    (
      until [ -f "$od/GO" ]; do sleep 0.01; done
      out="$("$SWARM" claim --agent "R$i" --shell 2>/dev/null)"; rc=$?
      if [ "$rc" -eq 0 ]; then
        eval "$out"
        printf 'R%s %s\n' "$i" "${TASK_ID:-NONE}" > "$od/$i"
      else
        printf 'R%s EMPTY%s\n' "$i" "$rc" > "$od/$i"
      fi
    ) &
  done
  sleep 1.0
  : > "$od/GO"
  wait

  local claims ids total uniq
  claims="$(cat "$od"/[0-9]* 2>/dev/null | grep -v ' EMPTY' || true)"
  ids="$(printf '%s\n' "$claims" | awk 'NF{print $2}' | sort)"
  total="$(printf '%s\n' "$ids" | grep -c . || true)"
  uniq="$(printf '%s\n' "$ids" | sort -u | grep -c . || true)"

  # The original runs 40 claimers at 30 items, so every item goes. The stated gate runs 12 at
  # 20, where twelve items go and eight stay queued. The property is identical either way and
  # the counts are derived rather than hard-coded, so neither shape weakens the other.
  local expect_claimed=$(( N < C ? N : C ))
  local expect_inbox=$((  N > C ? N - C : 0 ))
  local expect_losers=$(( C > N ? C - N : 0 ))

  assert_eq "every claimable task was claimed" "$total" "$expect_claimed"
  assert_eq "no task was claimed twice"        "$uniq"  "$expect_claimed"
  assert_eq "inbox holds exactly the unclaimed remainder" "$(count_state inbox)" "$expect_inbox"
  assert_eq "active holds exactly the claimed set"        "$(count_state active)" "$expect_claimed"

  # Conservation: nothing leaked into another state and nothing vanished.
  local tot=$(( $(count_state inbox) + $(count_state active) + $(count_state done) \
              + $(count_state blocked) + $(count_state cancelled) ))
  assert_eq "total tasks conserved" "$tot" "$N"

  # Nothing was skipped as well as nothing doubled. Every task here carries identical signals,
  # so the queue order reduces to id order and the winners must be the FIRST `expect_claimed`
  # ids. That is a stronger check than "some set of the right size": it would catch a claimer
  # that took a task out of order, which SKIP LOCKED is entitled to do only when a row is
  # genuinely locked -- and under contention a skipped row must still be taken by whoever
  # locked it, never left behind.
  local want; want="$(for ((i = 1; i <= expect_claimed; i++)); do printf '%04d\n' "$i"; done)"
  assert_eq "the claimed set is the front of the queue, in order" "$ids" "$want"

  # And the winner recorded in the store is the process that was told it won. If two writers had
  # both believed they owned a task, this is where it would show.
  local agent tid owner bad_owner=0 bad_attempts=0
  while read -r agent tid; do
    [ -z "$agent" ] && continue
    owner="$(meta "$tid" claimed_by)"
    [ "$owner" = "$agent" ] || { bad_owner=$((bad_owner + 1))
      printf '        task %s says owner [%s] but [%s] was told it won\n' "$tid" "$owner" "$agent"; }
    [ "$(meta "$tid" attempts)" = "1" ] || bad_attempts=$((bad_attempts + 1))
  done <<< "$claims"
  assert_eq "every winner matches the recorded owner" "$bad_owner" "0"
  assert_eq "every claim spent exactly one attempt" "$bad_attempts" "0"

  # The losers were told the queue was empty, not handed a phantom task.
  local empties; empties="$(cat "$od"/[0-9]* 2>/dev/null | grep -c ' EMPTY2' || true)"
  assert_eq "losers got exit 2" "$empties" "$expect_losers"
}

echo "test-claim-race.sh  --  the program gate"
echo "  store: $BRAIN_PG_DB (scratch). The live bus at ~/.swarm is not touched by this file."

# The stated gate.
race 20 12
# The parameters the ported test was verified against under the file bus.
race 30 40

echo
echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ] || exit 1
