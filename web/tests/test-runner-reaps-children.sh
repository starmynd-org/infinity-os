#!/usr/bin/env bash
# R-WEB-CHILDREN-01: does the runner reap a child a suite left behind?
#
# THE PROPERTY, and it is not "the runner cleans up". It is: A SUITE THAT DIES BEFORE ITS OWN
# TEARDOWN LEAVES NO PROCESS RUNNING ONCE THE RUNNER HAS EXITED. That is the case the web runner
# was missing -- `test_runfeed_browser.py` and `test_browser.py` both kill what they start, so
# nothing leaks on a clean pass, and neither is reaped by anyone when the suite dies first.
#
# WHY THIS TESTS THE REAPER AND NOT A COPY OF IT. Reproducing `run_suite`'s logic here would prove
# that my reimplementation works. This runs the REAL function out of the REAL runner, by sourcing
# the runner with a sentinel that makes it define its functions and stop before it builds a
# database or starts a console.
#
# WHY A CHILD PROCESS AND NOT A PORT. A leaked listener is the SYMPTOM the port census sees; the
# defect is an unreaped child. Testing the child tests the property, needs no port, and cannot
# collide with anything else on this host -- and this suite must never go looking for a process by
# port, which is the whole reason the fix is by process group.
#
#   ./web/tests/test-runner-reaps-children.sh
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); printf '  ok    %s\n' "$1"; }
bad() { FAIL=$((FAIL+1)); printf '  FAIL  %s\n' "$1"; [ -n "${2:-}" ] && printf '        %s\n' "$2"; }

echo "test-runner-reaps-children.sh  --  a suite that dies leaves nothing behind (R-WEB-CHILDREN-01)"
echo

if ! command -v setsid >/dev/null 2>&1; then
  echo "  NOT RUN: no setsid on this host, so the runner cannot put a suite in its own process"
  echo "  group and there is no reaping to test. That is the same condition run-all.sh reports."
  exit 77
fi

# The real function, lifted from the real file. `sed` rather than `source`, because sourcing
# run-all.sh runs its argument parsing, its port census and its database build.
# Overridable so the scene can be pointed at a PLANTED runner and watched failing. A check nobody
# has seen fail is a check nobody has tested, and that applies to this one too.
RUNNER="${WEB_RUNNER:-$HERE/run-all.sh}"
[ -f "$RUNNER" ] || { echo "  FAIL  run-all.sh is not at $RUNNER"; exit 1; }
FN="$(sed -n '/^run_suite() {/,/^}/p' "$RUNNER")"
if [ -z "$FN" ]; then
  bad "run_suite() is defined in run-all.sh" "not found: the runner does not own its suites' children"
  echo; echo "$PASS passed, $FAIL failed"; exit 1
fi
ok "run_suite() is defined in run-all.sh"

# AND IT IS THE FUNCTION THIS SCENE MEANS. T08 priority 3: the sed lift has no assertion that what
# it lifted is what it meant to lift, so a wrong match tests something else entirely and still
# prints green -- which is how T08 got "IDENTICAL, 123 chars" about the wrong function one file
# over. SUITE_PGIDS appears in run_suite and nowhere else.
if printf %s "$FN" | grep -q SUITE_PGIDS; then
  ok "and what was lifted IS run_suite (it carries SUITE_PGIDS)"
else
  bad "and what was lifted IS run_suite" "the sed matched something else; every check below would be about that"
  echo; echo "$PASS passed, $FAIL failed"; exit 1
fi

SUITE_PGIDS=""; ORPHANS=0; ORPHAN_NAMES=""; HAVE_SETSID=yes
eval "$FN"

# A SUITE THAT LEAKS AND THEN DIES. It starts a long-lived child, writes the child's pid where the
# scene can read it, and exits NON-ZERO without cleaning up -- which is what a suite does when it
# fails an assertion before its teardown.
MARK="$(mktemp)"
run_suite "leaky-fixture" bash -c 'sleep 300 & echo $! > "$1"; exit 3' _ "$MARK"
RC=$?
CHILD="$(cat "$MARK" 2>/dev/null)"
rm -f "$MARK"

# 1. THE EXIT CODE SURVIVES THE WRAPPER. A reaper that swallowed the suite's status would turn
#    every red suite green, which is a far worse defect than the leak it was added to fix.
[ "$RC" = "3" ] && ok "the suite's own exit code survives run_suite (3)" \
                || bad "the suite's own exit code survives run_suite" "wanted 3, got $RC"

# 2. THE PLANT TOOK. Without this the next check passes over a child that never existed.
if [ -n "$CHILD" ]; then
  ok "the fixture really started a child (pid $CHILD)"
else
  bad "the fixture really started a child" "no pid was recorded, so check 3 would prove nothing"
fi

# 3. THE PROPERTY.
sleep 0.5
if [ -z "$CHILD" ]; then
  bad "the orphaned child is gone after run_suite returned" "no denominator: see check 2"
elif kill -0 "$CHILD" 2>/dev/null; then
  bad "the orphaned child is gone after run_suite returned" \
      "pid $CHILD is still alive: the runner does not reap what its suites leave"
  kill -9 "$CHILD" 2>/dev/null
else
  ok "the orphaned child is gone after run_suite returned"
fi

# 4. AND IT WAS REPORTED, not silently tidied. A runner that cleans up without saying so leaves the
#    leaking suite unfixed forever, because nobody ever learns it leaks.
[ "$ORPHANS" -ge 1 ] && ok "the orphan was counted and named ($ORPHAN_NAMES )" \
                     || bad "the orphan was counted and named" "ORPHANS=$ORPHANS: reaped in silence"

# 5. NEGATIVE CONTROL. A suite that leaves nothing behind must NOT be reported as an orphan, or
#    the counter above means nothing.
ORPHANS=0; ORPHAN_NAMES=""
run_suite "clean-fixture" bash -c 'exit 0'
[ "$ORPHANS" -eq 0 ] && ok "a suite that leaves nothing is not reported as an orphan" \
                     || bad "a suite that leaves nothing is not reported as an orphan" "ORPHANS=$ORPHANS"

# ---------------------------------------------------------------- R-WEB-MONITOR-SCENE-01
#
# THE MONITOR CONDITION, TESTED RATHER THAN DECLARED. Terminal 08 (REV-063) and Terminal 25
# (REV-024) both measured it: with job control ON, an unguarded `run_suite` returns 0 for EVERY
# failing suite and does not wait at all -- so a red suite reads green, a 77 NOT RUN silently
# becomes a pass, and the next suite starts against a store the previous one is still using.
#
# The cause is util-linux `setsid`: it FORKS when the caller is already a process group leader, and
# a backgrounded job is a leader exactly when job control is on. Plain `setsid` does not wait for
# that fork, so `wait` collects setsid's own 0.
#
# `set +m` in run_suite holds it. The commit that added it could only STATE that, because both this
# scene and the runner are scripts and scripts default monitor-off -- the property was true by
# ambient shell state rather than by construction, which is what T08 named as the actual gap. This
# scene turns the ambient condition on DELIBERATELY, which is the only way to test it from here.
echo
echo "  R-WEB-MONITOR-SCENE-01: under job control"
MON_MARK="$(mktemp)"
set -m
MON_START=$SECONDS
run_suite "monitor-fixture" bash -c 'sleep 2; exit 3'
MON_RC=$?
MON_ELAPSED=$((SECONDS - MON_START))
set +m
rm -f "$MON_MARK"

[ "$MON_RC" = "3" ] && ok "under set -m the suite's exit status survives (3)" \
                    || bad "under set -m the suite's exit status survives" "wanted 3, got $MON_RC"
# AND IT ACTUALLY WAITED. The status alone is not enough: the failure T08 measured returned 0 AT
# 0.00s, so a scene checking only the code would miss the half where the next suite starts early.
[ "$MON_ELAPSED" -ge 1 ] && ok "and it waited for the suite (${MON_ELAPSED}s elapsed)" \
                         || bad "and it waited for the suite" \
                                "elapsed ${MON_ELAPSED}s: it returned without waiting, so the next suite would start against the same store"

# NEGATIVE CONTROL: the same function with `set +m` REMOVED must fail both assertions, or the two
# above are not about the guard. The plant is verified before it is used, because a plant that did
# not take reports the real code and reads as evidence.
# THE CODE LINE, NOT ANY LINE MENTIONING IT. The first version of this plant removed the code and
# then verified with a bare `grep 'set +m'`, which matched the COMMENT above it explaining why
# `set +m` is there -- so a plant that HAD taken reported as not taken. The verification was not
# about what it claimed, which is the failure this whole file is written against, appearing inside
# the check written to prevent it. Anchored to a line that is only the statement.
MON_FN="$(printf '%s\n' "$FN" | grep -vE '^[[:space:]]*set \+m[[:space:]]*$')"
if printf '%s\n' "$MON_FN" | grep -qE '^[[:space:]]*set \+m[[:space:]]*$'; then
  bad "the monitor plant took" "set +m is still present, so the control below proves nothing"
else
  ok "the monitor plant took (set +m removed from the copy)"
  eval "${MON_FN/run_suite()/run_suite_nomonitor()}"
  set -m
  MON_START=$SECONDS
  run_suite_nomonitor "monitor-control" bash -c 'sleep 2; exit 3'
  MON_RC2=$?
  MON_ELAPSED2=$((SECONDS - MON_START))
  set +m
  if [ "$MON_RC2" != "3" ] || [ "$MON_ELAPSED2" -lt 1 ]; then
    ok "without set +m it breaks as measured (rc=$MON_RC2, ${MON_ELAPSED2}s), so the scene discriminates"
  else
    bad "without set +m it breaks as measured" \
        "rc=$MON_RC2 elapsed=${MON_ELAPSED2}s: the guard cannot be what makes the checks above pass"
  fi
fi

echo
echo "$PASS passed, $FAIL failed  ($((PASS + FAIL)) comparisons made)"
# The count was already printed and that is NOT the same as refusing an empty one: every scene here
# is inside an `if`, so a file whose premises all went unmet would print "0 passed, 0 failed" and
# exit 0. `engine/bin/denominator-lint.py` flagged this file, and it was right to.
[ $((PASS + FAIL)) -gt 0 ] || { echo "DENOMINATOR: 0 comparisons made, which is not a pass"; exit 2; }
[ "$FAIL" -eq 0 ]
