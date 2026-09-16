#!/usr/bin/env bash
# Every suite this lane owns, in the order a reader should read them.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$(dirname "$HERE")")"
# THE STORE NAME IS CHOSEN AND GUARDED FURTHER DOWN, not here. This line was
# `export BRAIN_PG_DB="${ENGINE_SCRATCH_DB:-brain_scratch}"`; R-DBNAME-ENGINE-TESTS-01 is why it
# moved rather than being patched in place, and the guard below says what the defect was.
unset SWARM_PARENT_TASK
RC=0
INVOKED=0            # suites this run actually dispatched. The banner's denominator, task 0354.
NOTRUN=0             # suites that declared NOT RUN by exiting 77. Task 0372.
NOTRUN_NAMES=""

# EXIT 77 IS NOT RUN, AND IT DOES NOT TURN THIS RUNNER RED. Task 0372, rule in
# `docs/SUITE-INPUT-RULE.md`.
#
# A suite whose input is live state must declare NOT RUN with the reason rather than report the
# environment as a code defect. 77 is the automake convention for "skipped" and is unmistakable
# against this repo's 0 ok / 1 failed / 2 zero-denominator.
#
# WHY IT STAYS GREEN, WHICH IS THE PART SOMEBODY WILL WANT TO ARGUE WITH. `test-infra-failure.sh`
# reads a blob out of git history, and this program mandates that a suite result is only trusted
# from a `git archive` export, which has no `.git`. Those two rules are both correct and
# permanently incompatible, so that suite could never pass under the method, at any sha, ever --
# and this banner therefore read SOMETHING FAILED on every honest export verification for weeks,
# over a tree in which nothing had failed. A gate that always fires for a reason unrelated to the
# thing it gates stops being read, and the day it is right nobody looks.
#
# The counterpart is `queue/tests/run-all.sh`, whose six credential-gated suites DO turn that
# runner red, because a missing operator credential is something a human can go and fix on this
# host. Remediable NOT RUN is loud; inherent NOT RUN is counted. The suite itself decides which it
# is, because only the suite knows which input it lacked -- and 77 is reserved for the inherent
# case. A suite that means "somebody fix this host" exits 1 with the remedy, as it always did.
#
# This does NOT let a suite vanish: NOT RUN is printed by the suite, named on this banner, and
# counted next to INVOKED. And a suite that can declare NOT RUN still carries scenes that run
# everywhere -- `test-infra-failure.sh` stands down 4 of its 62 assertions and runs the other 58.

# THE DISPATCH LIST. Declared here, above everything that costs a database, so that `--list`
# below can print it without reconciling, reaping or running anything -- and so the list a reader
# sees and the list the loop iterates cannot be two different lists.
#
# test-budget-wiring.sh and test-lane-budget-gate.sh build their OWN databases and ignore
# BRAIN_PG_DB below, on purpose: both assert on stored state and test-claim-race.sh truncates the
# shared scratch between races. They do not share one either -- test-lane-budget-gate.sh refuses to
# run against test-budget-wiring.sh's database by name.
#
# Each of those names is now PER RUN, and the loop below is why (task 0321). This script exports no
# override, so before the fix every lane's run-all reached for the same two defaults, and both
# suites build with `scratch-db.sh create`, which opens `DROP DATABASE IF EXISTS ... WITH (FORCE)`.
# Two overlapping run-alls meant one dropped the other's store mid-run. Nothing needs setting here:
# the suites default to a pid-suffixed name and drop it again when they finish green.
SUITES=(
  "test-claim-race.sh  (THE PROGRAM GATE)"
  "test_contract.py    (the eleven rules)"
  "test-ratelimit.sh   (the refusal rule)"
  "test-verbs.sh       (all 40 verbs)"
  "test_claimer_predicate.py (a verb acts only on a task the caller holds, task 0144)"
  "test_double_claim.py (claim refuses a task a live agent still holds, task 0407)"
  "test_actor_gate.py (the operator's own work is not handed out by claim, task 0414)"
  "test_hold_over_a_live_run.py (holding a row does not recall an in-flight claim, task 0119)"
  "test-auto-accept-measurement.py (the week's measurement, task 0139)"
  "test_question_paging_join.py (a question raised anywhere becomes an event, task 0140)"
  "test_brief_survives_finishers.py (the posted work order survives done/fail/block/cancel, task 0138)"
  "test_steering_exclusion.py (a steered run is excluded from auto-accept and the measurement, V4)"
  "test_stop_leaves_a_trail.py (a deliberate stop is not a silent failure, task 0273)"
  "test_cancel_withdraws_questions.py (a cancelled task leaves no open question, task 0159)"
  "test_parent_cycle_guard.py (a parent cycle is bounded, 'set' cannot lower a flag, 0146)"
  "test_workdir_gate.py (a claimable task names its tree, at post and at set, task 0100)"
  "test_start_is_not_silent.py (start releases a stop or says it did not, task 0253)"
  "test_utilization_gauge.py (the rate-limit gauge reads the meter or says UNKNOWN, 0280)"
  "test-surface-doors.py (accept work, lineage project and budget outcome have doors, 0141)"
  "test_orient_and_decide_doors.py (the same defect one hop along the spine: observation open, observation close and disposition record had no door either, so Orient and Decide were unreachable and brain.observation held five demo rows. Drives the shipped binary, because a test calling store.apply would have passed before the doors existed, S2 2026-09-01)"
  "test-acceptance-guard.py (the COLUMN behind the accept-work door, ledger 22, 0260)"
  "test_unaccept.py (acceptance has an inverse and the inverse is a record, ledger 43, rows 0413 and 0419)"
  "test_project_entity.py (a project is a ROW with a state, not a text prefix: the four states, the join to work_item, and ledger 35's asymmetry -- coming to rest is open to every login, returning to in_progress is a human's, task 0429 / ledger 44)"
  "test_helper_verb.py (a helper terminal opens on a task and does NOT claim it: the session id it mints is the one on the thread, and the codex capture gap is stated before the session starts, 2026-08-28)"
  "test_recommendation_human_login.py (a drafted option cannot dispatch without a human, 0290)"
  "test_budget_stop_is_unspent.py (a run stopped for SPEND gives its attempt back and stays PARKED: the claim charges the attempt, block left it charged, and nine rows sat blocked at attempt 1 of 2 for a ceiling on 2026-08-29. Evidence-gated on brain.budget_incident, task 0461)"
  "test_denominator_lint.py (the denominator lint actually refuses, on trees it builds, task 0292)"
  "test_suite_registration.py (every test file in this directory is in the list above, task 0354)"
  "test_web_suite_registration.py (every test file in web/tests/ is in ITS runner's list, and that runner exists at all, task 0373)"
  # ROW 0439, HIS ASK 8, registered in the same change that built it. It needs no database:
  # the ladder is seven names and one rule, and the rule is walked over all 21 level and
  # flag combinations with the denominator printed.
  "test_the_ladder.py (the delegation ladder in HIS words, and the two hard flags no level lowers, row 0439)"
  "test_liveness_vs_heartbeat.py (a live heartbeat is not a live agent, and every surface says which it knows, task 0371)"
  "test_routines.py (a routine cannot fire one slot twice: the guard dropped and raced, then restored and raced, task 0376 -- registered on lane C's request via crosstalk)"
  "test_admin_verbs.py (the admin verb group's five refusals, each watched failing then passing, task 0385 -- registered on lane F's request via crosstalk)"
  "test_multi_user_subject.py (several named humans on ONE instance: every refusal watched failing first, task 0384 -- registered by lane B because the registration check went red; lane E filed no crosstalk request, which is the mechanism working)"
  "test_scratch_preflight_remedy.py (the scratch preflight never names the live store in its remedy, task 0353)"
  "test_paging_on_an_install_without_a_brain.py (brain-paging is skipped by name and brain-health stays green on an install with no brain beside it; the declaration is never one laptop's path, W5-S7 2026-09-16)"
  "test_urgency_words_match_the_store.py (swarm post --help and the write-path validator offer exactly the urgency words migration 74 lets the store accept, W5-B2 E36, W5-S7 2026-09-16)"
  "test_dispatch_idempotency.py (one proposal dispatches ONE task however many callers arrive at once, task 0377 dispatch idempotency)"
  "test-budget-wiring.sh (the spend brake: block, never reopen)"
  "test-infra-failure.sh (a dead network is not a failed task, task 0400)"
  "test-codex-guard.sh (the same brake on the CODEX branch, keyed on run_id, task 0167)"
  "test-lane-budget-gate.sh (a lane ceiling gates a multi-lane agent's claim, task 0122)"
  "test-project-budget-gate.sh (a HELD PROJECT is never handed out and nothing else is: the grain the four scopes did not have, task 0430)"
  "test-scratch-reaper.sh (what the scratch reaper will NOT drop, task 0332)"
  "test-scratch-db-guard.sh (asking scratch-db.sh what it takes must not destroy a store, 0248)"
)

# Invoked AFTER the loop, with arguments and a banner of its own, but named here so that it is
# counted and listed with the rest. A suite the runner runs and the list does not name would make
# `--list` a second, quieter dispatch list, which is the defect one level down from task 0354's.
PROBE="test-claim-contention.py"

# `--list` prints exactly what this script dispatches, one basename per line, and exits without
# touching a database. Task 0354. It exists so that the registration check does not have to parse
# this file's prose: `engine/tests/test_suite_registration.py` compares this output against the
# `test*` files on disk, and it can only ever go stale by the array above going stale, which is the
# thing being checked. Before it, five suites (test_denominator_lint.py among them, the only
# evidence the denominator lint refuses anything) sat in engine/tests/ un-run for weeks, and the
# banner at the bottom said ALL SUITES GREEN over 25 of 30 without saying so.
if [ "${1:-}" = "--list" ]; then
  for t in "${SUITES[@]}"; do printf '%s\n' "${t%% *}"; done
  printf '%s\n' "$PROBE"
  exit 0
fi

# R-DBNAME-ENGINE-TESTS-01: NAME YOUR OWN STORE, AND REFUSE A DEFAULT THAT IS SOMEBODY ELSE'S.
#
# Line 6 was `export BRAIN_PG_DB="${ENGINE_SCRATCH_DB:-brain_scratch}"`. With the variable unset it
# did not fall back to a lane-private name: it pointed all 36 suites below at `brain_scratch`, THE
# STORE EVERY LANE'S SUITE READS -- not the live store, so not the worst case, but the shared one,
# and chosen by this file rather than by whoever ran it. This is the most-run runner in the repo
# and it was the last copy of the defect: the identical line is quoted as REMOVED in `store/`,
# `engine/execution/` and `deploy/tests/`, and it was still here. Reported by Terminal 04 against
# `1f35c38` and unowned in writing until now.
#
# The block is `engine/execution/run-all.sh`'s, which is where the rule was first written, so that
# the two cannot drift into two different refusals. Refused and not defaulted, because a default is
# exactly how the shared name got here.
#
# ITS POSITION BETWEEN TWO EXITS IS THE WHOLE OF THE FIX'S CORRECTNESS, and neither half of that
# generalises to "put guards low" -- each exit had to be asked what it MEANS.
#
#   BELOW the `--list` exit above, because `--list` is a MACHINE CONTRACT.
#   `test_suite_registration.py` calls it on every discovered runner with no environment at all, so
#   it must cost no database and no variable either. Terminal 04 put this same guard above that
#   exit once, one file over: `--list` began refusing with nothing set, which would have made every
#   runner report as "does not answer --list" and turned a name-safety fix into a registration
#   failure.
#
#   ABOVE the `scratch-db.sh migrate` exit below, which is the opposite reason and is specific to
#   THIS file. That step is the first thing here that touches a database, and it MIGRATES what it
#   is given. A guard placed after it would refuse `brain_scratch` only once this run had already
#   applied migrations to it -- the refusal would print while the damage it names was done. The
#   execution runner has no such step, so its guard sits immediately before the suite loop and the
#   distinction never arose there.
: "${ENGINE_SCRATCH_DB:=}"
case "$ENGINE_SCRATCH_DB" in
  "")            printf '%s: ENGINE_SCRATCH_DB is unset. Name your own store; there is no default,\n' "$0" >&2
                 printf '  because the default was `brain_scratch`, which every lane reads.\n' >&2
                 printf '  e.g. ENGINE_SCRATCH_DB=brain_scratch_$USER_$$ %s\n' "$0" >&2
                 exit 2 ;;
  brain)         printf '%s: refusing `brain`. That is the live store.\n' "$0" >&2; exit 2 ;;
  brain_scratch) printf '%s: refusing `brain_scratch`. It is shared: another lane is reading it,\n' "$0" >&2
                 printf '  and this runner migrates and truncates what it is given.\n' >&2
                 exit 2 ;;
  *scratch*)     ;;
  *)             printf '%s: refusing %s: a scratch store must have "scratch" in its name, so that\n' "$0" "$ENGINE_SCRATCH_DB" >&2
                 printf '  anything reading a database list can tell it from a real one.\n' >&2
                 exit 2 ;;
esac
export BRAIN_PG_DB="$ENGINE_SCRATCH_DB"

# Reconcile the scratch database to `migrations/` BEFORE any suite runs. Task 0148.
#
# Without this the suite's green/red is unreadable rather than merely wrong: on 2026-08-16 it read
# 33 passed / 40 failed at 13:48 because brain_scratch was missing `produced_by_ref`, and 32/41 at
# 20:10 because it was missing `brief` instead. Both times ONE cause, both times `post` dying on a
# column a live lane had added to the tree and not to this database, and both times the other 39
# failures were the same absent task 0001 asserted on forty ways. Several lanes share this tree,
# so the tree gains migrations while the suite is running; the drift is structural, and applying
# whichever column is missing today only moves the same morning to the next migration.
#
# `migrate` and not `create`: create drops the database, and dropping it under a sibling lane
# mid-run is a worse failure than the one being fixed. migrate applies only unrecorded versions.
# Not conditional and not `|| true`: a migration in the tree that will not apply is a real finding
# and stops the run here, where it is legible, rather than as forty assertion failures below.
if ! "$ROOT/engine/bin/scratch-db.sh" migrate; then
  printf '\nrun-all: could not bring %s up to migrations/. Not running the suites: every result\n' \
    "$BRAIN_PG_DB"
  printf 'below would be about the schema, not about the code.\n'
  exit 1
fi

# Reap yesterday's generated scratch stores before anything builds today's. Task 0332.
#
# The suites below leave databases behind BY DESIGN: a red run of test-budget-wiring.sh or
# test-lane-budget-gate.sh keeps its per-run store so the rows can be read afterwards (task 0321),
# and nothing had ever dropped one. brain-postgres was holding 93 `brain_%` databases at 19:36 on
# 2026-08-17, each a full 32-table schema, and the count only went up.
#
# NON-FATAL, and the ordering says why: a sweeper that stopped the suites would trade a real
# result for a housekeeping failure. `|| true` and one printed line, never `exit`.
#
# It takes only names a script generated (`brain_budget_wire_<pid>` and the two lane-ceiling
# forms), only when nothing is connected, only over 24h old, and never WITH (FORCE) -- so a
# sibling lane's run in flight, a store somebody pinned, and every one of the ~90 hand-named
# survivors are all out of its reach. `test-scratch-reaper.sh` below is that sentence asserted.
"$ROOT/engine/bin/reap-scratch-dbs.sh" reap || \
  printf 'run-all: the scratch reaper failed. Housekeeping only; running the suites anyway.\n'

for t in "${SUITES[@]}"; do
  f="${t%% *}"
  printf '\n============================================================\n%s\n============================================================\n' "$t"
  INVOKED=$((INVOKED + 1))
  case "$f" in
    *.py) python3 "$HERE/$f" ;;
    *)    "$HERE/$f" ;;
  esac
  case "$?" in
    0)  ;;
    77) NOTRUN=$((NOTRUN + 1)); NOTRUN_NAMES="$NOTRUN_NAMES $f"
        printf '  ^^ NOT RUN (INHERENT): this suite could not run here and said which input it lacked.\n'
        printf '     Counted, not red. docs/SUITE-INPUT-RULE.md\n' ;;
    *)  RC=1 ;;
  esac
done
printf '\n============================================================\ncontention probe (does the gate actually race?)\n============================================================\n'
INVOKED=$((INVOKED + 1))
python3 "$HERE/$PROBE" 20 12 || RC=1

# THE BANNER'S DENOMINATOR. Task 0354, and the reason it exists is this banner itself: `ALL SUITES
# GREEN` was printed over 25 of the 30 `test*` files in this directory, and nothing in the line
# said 25, or 30, or that the two differed. That is the untallied verdict task 0292's rule refuses,
# printed by the one script every lane quotes.
#
# ON_DISK is counted, not declared: a file dropped into engine/tests/ and never registered moves
# this number and not INVOKED, so the gap shows up on the next run rather than a month later.
# WHICH files are missing is test_suite_registration.py's job (it is in the list above and turns
# the run red); this line's job is to make the size of the comparison impossible not to read.
ON_DISK=$(ls "$HERE"/test*.py "$HERE"/test*.sh 2>/dev/null | wc -l)
# A glob that matched nothing, or a loop that dispatched nothing, would print `0 of 0 invoked` next
# to ALL SUITES GREEN -- a green from a run that ran no suites at all.
if [ "$INVOKED" -eq 0 ] || [ "$ON_DISK" -eq 0 ]; then   # DENOMINATOR
  printf '\nDENOMINATOR: %s test files invoked of %s on disk. 0 comparisons made. A verdict over an empty set is not a pass.\n' \
    "$INVOKED" "$ON_DISK"
  exit 2
fi
printf '\n%s  (%s of %s test files in engine/tests/ invoked, %s NOT RUN)\n' \
  "$([ $RC -eq 0 ] && echo 'ALL SUITES GREEN' || echo 'SOMETHING FAILED')" \
  "$INVOKED" "$ON_DISK" "$NOTRUN"
if [ "$NOTRUN" -gt 0 ]; then
  printf 'NOT RUN, with the input each one lacked printed above:%s\n' "$NOTRUN_NAMES"
fi
exit $RC
