#!/usr/bin/env bash
# Every suite the queue lane owns. Task 0212: there was no such file, which is part of why these
# four went unrun for a whole day while `engine/tests/run-all.sh` was being read as "the tests".
#
# Each suite reconciles the scratch database itself (queue/tests/_scratch_preflight.py), so this
# is a list and an exit code rather than a harness. The one thing it does add is exporting BOTH
# names from one value: the store reads BRAIN_PG_DB, the builder reads QUEUE_SCRATCH_DB, and a
# caller that exports only the first gets a suite that asserts on one database while TRUNCATEing
# another. The preflight refuses that rather than doing it, so this file makes it impossible to
# hit by accident.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export QUEUE_SCRATCH_DB="${QUEUE_SCRATCH_DB:-brain_queue_scratch}"
export BRAIN_PG_DB="$QUEUE_SCRATCH_DB"
# The runner exports this and `post` inherits it, which would make every task these suites create
# a child of the live task that launched them -- and `post` raises `no such parent task` outright,
# because that id lives on the file bus and not in this store.
unset SWARM_PARENT_TASK

RC=0
INVOKED=0
NOTRUN=0
NOTRUN_NAMES=""
# EXIT 77 IS AN INHERENT NOT RUN AND DOES NOT TURN THIS RUNNER RED. Task 0372, rule in
# `docs/SUITE-INPUT-RULE.md`. The credential-gated NOT RUN above is the REMEDIABLE kind and keeps
# its exit 1: somebody can go and provision the operator login on this host. A suite that exits 77
# is saying the input cannot exist in this verification context at all, which is a different
# sentence and needs a different colour.
# THE DECLARED LIST, IN AN ARRAY, so `--list` can print exactly what the loop dispatches.
#
# IT WAS AN INLINE `for t in ...` UNTIL 2026-08-31 and that is why nothing checked it. The engine
# and web runners both grew `--list` and a registration suite that compares it against the files on
# disk; this directory had neither, so an unregistered suite here was silent BY CONSTRUCTION, which
# is the same shape as `test_suite_registration.py` scanning one directory only. Both halves are
# closed in the same change: this array plus `--list` here, and the registration suite widened to
# walk all three directories.
#
# THE PATHS OF THE OTHER TWO DIRECTORIES ARE DELIBERATELY NOT SPELLED OUT ANYWHERE IN THIS FILE.
# `test_web_suite_registration.py` asserts that neither this runner nor the engine's dispatches the
# web suites, and it asserts it with a string scan over this file. A comment naming that path trips
# it, which it did on the first version of this block. The scan is crude and it is crude on
# purpose; rewording a comment is cheaper than weakening it.
QUEUE_SUITES=(
  "test_queue_mechanics.py         (ranking, bump decay, the hard rules)"
  "test_ask_default_refusal.py     (a default may never carry a hard flag's action)"
  "test_no_self_execution.py       (the queue proposes, it does not act)"
  "test_defer_and_demote_joins.py  (a defer's wake condition and the tier a demote is FROM)"
  "test_human_actor_identity.py    (the operator's own door, and the four ways in that are shut)"
  "test_time_ledger.py             (the operator's stopwatch: append-only, the cap, and coverage)"
  "test_queue_open_is_the_complement.py  (his queue IS what the fleet may not take: no third state)"
  "test_spawned_workdir.py         (where the work this lane spawns runs, when nobody types --workdir)"
  "test_acted_on_denominator.py    (this layer's falsifier does not fire from an empty denominator, task 0382)"
  "test_wake_idempotency.py        (one wake, one thread note, one woke_by, however many wakers arrive at once, task 0382)"
  "test_impact_and_the_weights.py  (row 0434: impact is continuous, the spine is a product, and the weights arrive)"
  "test_his_five_queues.py         (row 0380's neighbour, his ask 5: five queues, and only one needed a column)"
)

# `--list` prints exactly what this script dispatches, one basename per line, and touches nothing.
# The same contract the engine and the web runners both have, so a registration
# check compares this output against the files on disk instead of parsing this file's prose. A
# prose parse that matched nothing would compare zero files and report no disagreements, which is
# a pass over an empty set.
if [ "${1:-}" = "--list" ]; then
  for t in "${QUEUE_SUITES[@]}"; do printf '%s\n' "${t%% *}"; done
  exit 0
fi

# Credential inspection belongs to execution, after the pure --list return. Moving the
# warning to stderr fixed its stdout contract but still inspected credentials and emitted
# provisioning advice while listing. The registration regression uses an absent backend
# and an empty synthetic credential to keep that distinction observable.
#
# Task 0312: normal execution still requires the operator LOGIN for these suites. Missing
# credentials remain a named NOT RUN and exit 1; listing neither needs nor provisions one.
OPERATOR_SECRET="${BRAIN_SECRET_DIR:-$HOME/.brain-postgres-secrets}/brain-postgres-role-operator"
NEEDS_HUMAN_LOGIN=" test_queue_mechanics.py test_defer_and_demote_joins.py test_human_actor_identity.py test_queue_open_is_the_complement.py test_no_self_execution.py test_spawned_workdir.py test_impact_and_the_weights.py test_his_five_queues.py "
HAVE_OPERATOR=yes
if [ ! -s "$OPERATOR_SECRET" ]; then
  HAVE_OPERATOR=no
  {
  printf 'NO OPERATOR CREDENTIAL on this host (%s).\n' "$OPERATOR_SECRET"
  printf 'Six suites below need the operator LOGIN -- four to write actor_type=human, and two to\n'
  printf 'accept a recommendation, whose decider has been that login since migration 32. Migration\n'
  printf '20 is\n'
  printf 'two steps and the second is:  store/bin/provision-operator.sh --db %s\n' "$BRAIN_PG_DB"
  } >&2
fi

for t in "${QUEUE_SUITES[@]}"; do
  f="${t%% *}"
  printf '\n============================================================\n%s\n============================================================\n' "$t"
  if [ "$HAVE_OPERATOR" = no ] && [ "${NEEDS_HUMAN_LOGIN#* $f }" != "$NEEDS_HUMAN_LOGIN" ]; then
    printf '  NOT RUN: this suite needs the operator LOGIN -- to write actor_type=human (migration\n'
    printf '  20) or to accept a recommendation (migration 32). Both establish who a human is from\n'
    printf '  the LOGIN and not from a flag, and there is no operator credential on this host, so\n'
    printf '  there is no login to map. Provision it and rerun:\n'
    printf '      store/bin/provision-operator.sh --db %s\n' "$BRAIN_PG_DB"
    RC=1
    NOTRUN=$((NOTRUN + 1)); NOTRUN_NAMES="$NOTRUN_NAMES $f"
    continue
  fi
  INVOKED=$((INVOKED + 1))
  python3 "$HERE/$f"
  case "$?" in
    0)  ;;
    77) NOTRUN=$((NOTRUN + 1)); NOTRUN_NAMES="$NOTRUN_NAMES $f"
        printf '  ^^ NOT RUN (INHERENT): the suite said which input it lacked. Counted, not red.\n'
        printf '     docs/SUITE-INPUT-RULE.md\n' ;;
    *)  RC=1 ;;
  esac
done

# THE DENOMINATOR. This banner had none: `ALL QUEUE SUITES GREEN` printed over however many suites
# the loop happened to dispatch, with nothing saying how many that was or how many test files sit
# in this directory. That is the shape task 0354 found in the engine runner (25 of 30 reported as
# ALL SUITES GREEN) and it was still here. Task 0382.
ON_DISK=$(ls "$HERE"/test_*.py 2>/dev/null | wc -l)
DECLARED=$(( $(printf '%s' "$NOTRUN_NAMES" | wc -w) + INVOKED ))
if [ "$INVOKED" -eq 0 ] && [ "$NOTRUN" -eq 0 ]; then     # DENOMINATOR
  printf '\nDENOMINATOR: 0 suites dispatched of %s test_*.py on disk. 0 comparisons made.\n' "$ON_DISK"
  printf 'A verdict over an empty set is not a pass.\n'
  exit 2
fi
if [ $RC -eq 0 ]; then
  printf '\nALL QUEUE SUITES GREEN  (%s of %s declared invoked; %s declared of %s test_*.py on disk; %s NOT RUN)\n' \
    "$INVOKED" "$DECLARED" "$DECLARED" "$ON_DISK" "$NOTRUN"
elif [ "$HAVE_OPERATOR" = no ]; then
  printf '\nSOMETHING FAILED (six suites NOT RUN: no operator credential on this host, see above)  (%s of %s declared invoked; %s declared of %s test_*.py on disk; %s NOT RUN)\n' \
    "$INVOKED" "$DECLARED" "$DECLARED" "$ON_DISK" "$NOTRUN"
else
  printf '\nSOMETHING FAILED  (%s of %s declared invoked; %s declared of %s test_*.py on disk; %s NOT RUN)\n' \
    "$INVOKED" "$DECLARED" "$DECLARED" "$ON_DISK" "$NOTRUN"
fi
if [ "$DECLARED" -ne "$ON_DISK" ]; then
  printf 'REGISTRATION GAP: %s test_*.py file(s) in queue/tests/ are not in this runner list.\n' \
    "$((ON_DISK - DECLARED))"
  RC=1
fi
if [ "$NOTRUN" -gt 0 ]; then
  printf 'NOT RUN:%s\n' "$NOTRUN_NAMES"
fi
exit $RC
