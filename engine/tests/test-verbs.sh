#!/usr/bin/env bash
# Verb parity: all 40, exercised against a scratch database, not read off a --help listing.
#
# The list is D1's COVERAGE.md, which counted `swarm --help` on the live system: init, post,
# claim, done, block, fail, ask, reopen, objectives, accept, intake, config, state, tick, doctor,
# set, answer, reanswer, questions, msg, inbox, note, heartbeat, reap, ls, show, artifact,
# artifacts, signals, why, feed, status, board, brief, paused, stop, start, pause, resume, cancel.
#
# Every one is CALLED here and its effect asserted. A verb that parses and does nothing would
# pass a --help diff and fail this.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SWARM="$ROOT/engine/bin/swarm"
SCRATCH="$ROOT/engine/bin/scratch-db.sh"
export BRAIN_PG_DB="${ENGINE_SCRATCH_DB:-brain_scratch}"
unset SWARM_PARENT_TASK

# Reconcile this database to `migrations/` before the first assertion. Task 0153. `run-all.sh`
# does this once for the whole run (task 0148); a suite run BY ITSELF did not, and running one
# suite by itself is what a brief asks for. A red suite about a schema three migrations behind is
# not a finding, it is a full verification cycle spent learning it was never about the code.
#
# `migrate` and not `create`: create does DROP DATABASE ... WITH (FORCE), and dropping this
# database under a sibling lane mid-run is a worse failure than the one being fixed. migrate is
# additive and a no-op on a current database, so calling it from every suite is safe.
if ! "$SCRATCH" migrate; then
  printf 'test-verbs.sh: could not bring %s up to migrations/. Not running: every result below\n' \
    "$BRAIN_PG_DB"
  printf 'would be about the schema, not about the verbs.\n'
  exit 1
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
export ENGINE_CONFIG="$TMP/config.json"
export ENGINE_HOME="$TMP/home"
cat > "$ENGINE_CONFIG" <<'JSON'
{"fleet":"test",
 "defaults":{"permission_mode":"auto"},
 "agents":[{"name":"T1","role":"terminal","lanes":["*"]},
           {"name":"T2","role":"terminal","lanes":["*"]},
           {"name":"admiral","role":"admiral","lanes":[],"plans":["*"]}]}
JSON

PASS=0; FAIL=0; SEEN=(); RAN=()
ok()  { PASS=$((PASS + 1)); printf '  ok    %s\n' "$1"; }
bad() { FAIL=$((FAIL + 1)); printf '  FAIL  %s\n' "$1"; [ -n "${2:-}" ] && printf '        %s\n' "$2"; }
# `verb` declares which of the 40 a check is ABOUT. It is intent, not evidence: it is shell, and
# it records the same name whether the CLI worked or died. Coverage is asserted on RAN, below.
verb() { SEEN+=("$1"); }

# RAN is the evidence: the subcommand `run` actually passed to the CLI, recorded only when the
# CLI did not traceback. Task 0243: a duplicate `--json` on the run-start subparser raised
# argparse.ArgumentError at parser-BUILD time, killing all 40 verbs -- and this suite still
# printed "all 40 verbs were exercised against the store", because it was counting `verb`
# markers. A coverage line that stays green on a CLI that cannot build its parser is worse than
# no coverage line, so it now counts invocations that survived.
run() {
  local v="$1"
  OUT="$("$SWARM" "$@" 2>"$TMP/err")"; RC=$?; ERR="$(cat "$TMP/err")"
  crashed || RAN+=("$v")
}

# One gate in front of every assertion. A traceback exits 1, so `want_rc 1` cannot tell a verb
# refusing on purpose from the CLI falling over -- three checks passed that way under 0243. No
# expected outcome of any verb here includes a Python traceback, so it fails the check outright.
crashed() { case "$ERR" in *Traceback*) return 0 ;; *) return 1 ;; esac; }

want_rc() {
  if crashed; then bad "$2" "the CLI tracebacked (rc=$RC): $(printf '%.300s' "$ERR")"
  elif [ "$RC" = "$1" ]; then ok "$2"
  else bad "$2" "rc=$RC want=$1; $ERR"; fi
}
want_out() {
  # An empty needle makes `case $OUT in *""*)` match anything, so a check whose expected value
  # came from an earlier command that produced nothing passes silently. That is how
  # "questions lists it open" stayed green against a dead CLI.
  if [ -z "$1" ]; then bad "$2" "the expected substring was EMPTY -- the value it came from was never set"
  elif crashed; then bad "$2" "the CLI tracebacked (rc=$RC): $(printf '%.300s' "$ERR")"
  else case "$OUT" in *"$1"*) ok "$2" ;; *) bad "$2" "output was: $(printf '%.200s' "$OUT")" ;; esac; fi
}
no_traceback() { if crashed; then bad "$1 does not traceback" "$ERR"; else ok "$1 does not traceback"; fi; }

"$SCRATCH" truncate >/dev/null 2>&1
echo "test-verbs.sh  --  all 40 verbs against $BRAIN_PG_DB"
echo

# Preflight, before any verb. build_parser() constructs EVERY subparser on EVERY invocation, so a
# duplicate option string anywhere is one exception that takes down all 40 -- `swarm --help`
# included. Asserting it here turns that from 68 confusing red lines into one that names the
# cause. The scan is per-subparser and reports every offender, so the answer to "did any other
# verb carry the same duplicate?" is printed rather than inferred.
echo "  preflight"
PRE="$(PYTHONPATH="$ROOT/engine:$ROOT" python3 -c "
import argparse, collections
from swarm_engine import accept, transitions   # register every verb
from swarm_engine.cli import build_parser
p = build_parser()
sub = [a for a in p._actions if isinstance(a, argparse._SubParsersAction)][0]
bad = []
for name, sp in sorted(sub.choices.items()):
    c = collections.Counter(o for a in sp._actions for o in a.option_strings)
    d = sorted(k for k, v in c.items() if v > 1)
    if d:
        bad.append(name + ':' + ','.join(d))
print('SUBCOMMANDS=%d' % len(sub.choices))
print('DUPES=%s' % (' '.join(bad) if bad else 'none'))
" 2>&1)"
case "$PRE" in
  *"DUPES=none"*) ok "the parser builds and no subparser registers an option string twice" ;;
  *) bad "the parser builds and no subparser registers an option string twice" \
         "$(printf '%.500s' "$PRE")"
     printf '\n  not running the verb checks: every result below would be about the parser.\n'
     printf '\n%s passed, %s failed\n' "$PASS" "$FAIL"; exit 1 ;;
esac
echo

echo "  lifecycle"
verb init;   run init;                                    want_rc 0 "init applies against a live store"
verb post;   run post --lane build --title "first task" --for-agents --workdir /tmp;  want_rc 0 "post creates a task"; want_out "0001" "post prints the id"
verb post;   run post --lane build --title "second" --priority 1 --stakes critical --urgency deadline \
                 --effort small --confidence 0.9x --workdir /tmp; want_rc 1 "post refuses a bad signal value"
             run post --lane build --title "second" --priority 1 --stakes critical --urgency deadline \
                 --effort small --workdir /tmp --for-agents; want_rc 0 "post accepts the canon vocabulary"
verb claim;  run claim --agent T1 --shell;                want_rc 0 "claim takes the highest-priority task"
             want_out "TASK_ID='0002'" "claim honours the priority band before the score"
verb state;  run state 0002;                              want_out "active" "state prints one word"
verb note;   run note 0002 "a working note" --from T1;    want_rc 0 "note appends to the thread"
verb artifact; run artifact 0002 "$TMP/config.json" --kind created --from T1 --note "the config";
                                                          want_rc 0 "artifact records a real path"
verb artifact; run artifact 0002 /nope/missing.txt --kind created --from T1
             want_out "MISSING" "artifact records a missing path AND says so"
verb artifacts; run artifacts --task 0002;                want_rc 0 "artifacts lists them"
verb done;   run done 0002 --summary "finished the second task" --agent T1
             want_rc 0 "done reports the task finished"; want_out "not accepted" "done says it is not acceptance"
verb ls;     run ls --state done;                         want_out "0002" "ls filters by state"
verb show;   run show 0002;                               want_out "finished the second task" "show renders the task"
             no_traceback "show"
verb show;   run show 0002 --json;                        want_rc 0 "show --json"
verb signals; run signals 0002;                           want_out "stakes" "signals prints the nine"
verb why;    run why 0001;                                want_out "queue" "why explains queue position"
verb reopen; run reopen 0002 --reason "not good enough" --from operator
             want_out "attempts reset to 0" "reopen returns it unspent"
verb cancel; run cancel 0002 --reason "no longer needed"; want_rc 0 "cancel closes a task"

echo
echo "  failure paths"
verb claim;  run claim --agent T1;                        want_rc 0 "claim the remaining task"
verb fail;   run fail 0001 --reason "first attempt failed" --agent T1
             want_out "requeued" "fail requeues while attempts remain"
             run claim --agent T1 >/dev/null
verb fail;   run fail 0001 --reason "second attempt failed" --agent T1
             want_out "blocked after" "fail blocks when attempts are exhausted"
             want_out "raised q" "and raises an operator question"
verb block;  run post --lane build --title "to be blocked" --for-agents --workdir /tmp; TID="$OUT"
             run block "$TID" --reason "waiting on a human"; want_rc 0 "block parks a task"

echo
echo "  questions and mail"
verb ask;    run ask "which option do you want?" --from T1 --task 0001 --default "take option A"
             want_out "q" "ask raises a question and returns its id"
QID="$OUT"
verb questions; run questions;                            want_out "$QID" "questions lists it open"
verb answer; run answer "$QID" "take option B";           want_rc 0 "answer records the answer"
verb reanswer; run reanswer "$QID" "actually take option C"; want_rc 0 "reanswer amends it"
verb questions; run questions --answered --since 1;       want_out "option C" "questions --answered shows the amendment"
verb msg;    run msg "look at 0001 when you can" --from T1 --to T2 --task 0001
             want_rc 0 "msg reaches another agent"
verb inbox;  run inbox --agent T2;                        want_out "look at 0001" "inbox reads the mailbox"
verb inbox;  run inbox --agent T2 --mark-read;            want_rc 0 "inbox --mark-read moves the pointer"

echo
echo "  objectives"
mkdir -p "$TMP/drop"; printf 'Ship the thing.\n' > "$TMP/drop/ship-the-thing.md"
verb intake; run intake --source "$TMP/drop";             want_out "took in 1" "intake takes a new objective"
verb intake; run intake --source "$TMP/drop";             want_rc 2 "intake dedups by size:mtime on a second pass"
verb objectives; run objectives;                          want_out "ship-the-thing" "objectives lists the inbox"
verb accept; run accept ship-the-thing;                   want_rc 0 "accept moves it to accepted"
verb objectives; run objectives;                          want_rc 2 "and the inbox is then empty"

echo
echo "  agents and the fleet"
verb heartbeat; run heartbeat --agent T1 --status working --task 0001; want_rc 0 "heartbeat records liveness"
verb status;  run status;                                 want_out "T1" "status shows the fleet"
verb board;   run board;                                  want_out "next up" "board renders"; no_traceback "board"
verb brief;   run brief --since 24;                       want_out "artifacts" "brief summarises the window"; no_traceback "brief"
verb feed;    run feed --limit 20;                        want_rc 0 "feed prints events in time order"; no_traceback "feed"
verb doctor;  run doctor;                                 no_traceback "doctor"
verb paused;  run paused --agent T1;                      want_rc 2 "paused exits 2 when work may proceed"
verb pause;   run pause;                                  want_rc 0 "pause stops the fleet"
verb paused;  run paused --agent T1;                      want_rc 0 "paused exits 0 when the fleet is paused"
verb claim;   run claim --agent T2;                       want_rc 2 "and claim returns nothing while paused"
verb resume;  run resume;                                 want_rc 0 "resume restarts the fleet"
# `--reason` is REQUIRED on stop since task 0273: a stop nobody signed is indistinguishable on
# the board from an agent that died, and on 2026-08-19 an admiral read one as the other.
verb stop;    run stop --agent T2 --reason "the verb suite";
                                                          want_rc 0 "stop halts one agent"
              want_out "by " "stop says who stopped it"
verb stop;    run stop --agent T2;                        want_rc 2 "stop with no reason is refused"
verb paused;  run paused --agent T2;                      want_out "T2 stopped" "paused reports the per-agent stop"
verb claim;   run claim --agent T2;                       want_rc 2 "a stopped agent claims nothing"
verb start;   run start --agent T2;                       want_rc 0 "start releases it"
# reap needs an ACTIVE task whose holder has gone quiet, so make one rather than hoping the
# board still has a leftover from an earlier check.
              run post --lane build --title "held by a silent agent" --for-agents --workdir /tmp; RTID="$OUT"
              run claim --agent T1 >/dev/null
verb reap;    run reap --stale-seconds 0;                 want_rc 0 "reap shows without --yes"
              want_out "nothing was changed" "reap does not act without --yes"
              run state "$RTID";                          want_out "active" "and it really did not act"
verb reap;    run reap --stale-seconds 0 --yes;           want_rc 0 "reap --yes requeues"
              run state "$RTID";                          want_out "inbox" "the reaped task is back in the queue"
verb set;     run set 0001 priority 7;                    want_rc 0 "set changes one field"
verb set;     run set 0001 nonsense 7;                    want_rc 1 "set refuses a field that is not settable"
verb tick;    run tick --agent admiral;                   want_rc 0 "tick says a planner has something to do"
verb tick;    run tick --agent admiral --commit;          want_rc 0 "tick --commit records the pass"
verb tick;    run tick --agent admiral;                   want_rc 2 "and an unchanged board does not wake it again"
verb config;  run config --agent T1;                      want_out "permission_mode" "config resolves an agent"
verb config;  run config --agent T1 --shell;              want_out "CFG_PERMISSION_MODE='auto'" "config --shell exports"

echo
echo "  coverage"
EXPECTED="init post claim done block fail ask reopen objectives accept intake config state tick \
doctor set answer reanswer questions msg inbox note heartbeat reap ls show artifact artifacts \
signals why feed status board brief paused stop start pause resume cancel"
# Counted from RAN, not SEEN: the subcommand really handed to the CLI on a call that did not
# traceback. SEEN is checked too, one line lower, but only to catch a check drifting off the verb
# it claims to cover -- it can never be the thing that says the 40 work.
MISSING=""
for v in $EXPECTED; do
  case " ${RAN[*]} " in *" $v "*) : ;; *) MISSING="$MISSING $v" ;; esac
done
DRIFTED=""
for v in $EXPECTED; do
  case " ${SEEN[*]} " in *" $v "*) : ;; *) DRIFTED="$DRIFTED $v" ;; esac
done
N_EXPECTED="$(printf '%s\n' $EXPECTED | wc -l)"
if [ "$N_EXPECTED" = "40" ]; then ok "the expected list is 40 verbs, matching D1's COVERAGE.md"
else bad "the expected list is 40 verbs" "it is $N_EXPECTED"; fi
if [ -z "$MISSING" ]; then
  ok "all 40 verbs ran against the store without tracebacking ($(printf '%s\n' "${RAN[@]}" | sort -u | wc -l) distinct subcommands invoked)"
else bad "all 40 verbs ran against the store without tracebacking" "never reached the store:$MISSING"; fi
if [ -z "$DRIFTED" ]; then ok "every one of the 40 is also declared by a verb marker"
else bad "every one of the 40 is also declared by a verb marker" "undeclared:$DRIFTED"; fi

# And the CLI must expose exactly these plus the declared additions in engine/VERB-PARITY.md,
# so a verb cannot be added without this test noticing. Task 0310: it noticed. 0280 shipped
# `utilization` in cli.py and declared it in neither place, and this is the check that said so.
# The count lives in the verdict line below and in VERB-PARITY.md's heading; the two move together.
ACTUAL="$(PYTHONPATH="$ROOT/engine:$ROOT" python3 -c "
from swarm_engine import accept, transitions   # register every verb
from swarm_engine.cli import build_parser
import argparse
p = build_parser()
for a in p._actions:
    if isinstance(a, argparse._SubParsersAction):
        print(chr(10).join(sorted(a.choices)))
" | sort)"
EXTRA="$(comm -13 <(printf '%s\n' $EXPECTED | sort) <(printf '%s\n' "$ACTUAL"))"
# `unaccept-work` is the twelfth, added by bus row 0413: acceptance had no inverse and
# web/MUST-NOT-BUILD.md item 10 requires one wherever a real one exists.
#
# `helper` is the THIRTEENTH, from Andrew's own answer on 2026-08-28 to what would make him run
# his real day through this: a helper terminal launched from a task, defaulting to Claude Code or
# Codex. Unlike every earlier addition, its row in engine/VERB-PARITY.md landed in the SAME change
# as the verb and this line, so this check never went red for it. That is the outcome the rule is
# for; the three times it did go red are written up in that file.
#
# `project` is the FOURTEENTH, bus row 0442, and it is the door onto migration 44's entity: before
# it the only way a project row came into existence was a hand-typed INSERT at a psql prompt,
# which is the second-writer shape this architecture spends most of its rules preventing. Its
# row in VERB-PARITY.md landed in the SAME change as the registration and this line, which is the
# second time that has happened and the outcome the rule is for.
WANT_EXTRA="$(printf 'release\nrun-end\nrun-start\nanswer-requeue\nauto-accept\naccept-work\nunaccept-work\nwithdraw\nutilization\nroutine\nwhoami\nadmin\nhelper\nproject\nobservation\ndisposition\n' | sort)"
if [ "$EXTRA" = "$WANT_EXTRA" ]; then
  ok "the only verbs beyond the 40 are the 16 declared in VERB-PARITY.md"
else
  bad "the only verbs beyond the 40 are the 16 declared in VERB-PARITY.md" \
      "found: $(printf '%s' "$EXTRA" | tr '\n' ' ')"
fi

# ...and the list it is compared against must be the one the DOC declares. Task 0317, the other
# half of 0310. WANT_EXTRA is a literal, the verdict line above says "declared in VERB-PARITY.md",
# and until now nothing compared the two: an author who updated only this test went green with the
# doc silently wrong, and the doc is what anyone reads to find out what the CLI surface is. 0280
# got caught only because it updated NEITHER.
#
# The literal STAYS. Deriving WANT_EXTRA from the table would make one source of truth and make
# every verb check depend on parsing prose, where a parse matching zero rows reads as a pass. The
# parsing is confined to this assertion instead, where a zero parse is a hard exit and the size of
# the comparison set is printed on the verdict line rather than implied by it.
DECLARED="$(python3 - "$ROOT/engine/VERB-PARITY.md" <<'PY'
import re, sys

doc = open(sys.argv[1], encoding="utf-8").read()
# The heading carries the count as a word ("Eight verbs BEYOND the 40"), so match the SHAPE and
# not the number: a ninth verb rewrites that word, and a heading regex that then matched nothing
# would empty this check instead of failing it.
m = re.search(r"^##\s+\S+\s+verbs BEYOND the 40.*?(?=^##\s|\Z)", doc, re.M | re.S)
if not m:
    sys.exit(0)          # prints nothing -> denominator 0 below -> the suite exits 2, not green
names = set()
for line in m.group(0).splitlines():
    if not line.startswith("|"):
        continue
    cells = line.split("|")
    if len(cells) < 2:
        continue
    # First column only. The other columns cite verbs they are not declaring (`accept`, `answer`),
    # and counting those would make the doc agree with anything.
    for tok in re.findall(r"`([^`]+)`", cells[1]):
        names.add(tok.split()[0])
print("\n".join(sorted(names)))
PY
)"
N_WANT="$(printf '%s\n' $WANT_EXTRA | grep -c .)"
N_DOC="$(printf '%s\n' $DECLARED | grep -c .)"
N_BOTH="$(comm -12 <(printf '%s\n' $WANT_EXTRA | sort) <(printf '%s\n' $DECLARED | sort) | grep -c .)"
ONLY_TEST="$(comm -23 <(printf '%s\n' $WANT_EXTRA | sort) <(printf '%s\n' $DECLARED | sort) | tr '\n' ' ')"
ONLY_DOC="$(comm -13 <(printf '%s\n' $WANT_EXTRA | sort) <(printf '%s\n' $DECLARED | sort) | tr '\n' ' ')"
# The comparison set, asserted BEFORE any verdict about what it holds. An emptied WANT_EXTRA, or a
# renamed heading the regex above no longer finds, compares nothing -- and "no disagreements found"
# over nothing is the exact shape task 0292 exists to refuse.
if [ "$N_WANT" -eq 0 ] || [ "$N_DOC" -eq 0 ]; then   # DENOMINATOR
  # Through `bad`, not a bare printf: a hand-rolled FAIL line leaves the counter below reading
  # `77 passed, 0 failed` on a run that refused itself, and a reader grepping for that string
  # gets a green out of the very check that exists to refuse one.
  bad "declared extras: the test names $N_WANT and VERB-PARITY.md names $N_DOC" \
      "0 comparisons made. A verdict over an empty set is not a pass."
  printf '\n%s passed, %s failed\n' "$PASS" "$FAIL"
  exit 2
fi
if [ -z "$ONLY_TEST" ] && [ -z "$ONLY_DOC" ]; then
  ok "WANT_EXTRA and VERB-PARITY.md's additions table declare the same verbs ($N_WANT in the test, $N_DOC in the doc, $N_BOTH matched)"
else
  bad "WANT_EXTRA and VERB-PARITY.md's additions table declare the same verbs ($N_WANT in the test, $N_DOC in the doc, $N_BOTH matched)" \
      "only in the test:${ONLY_TEST:- none}   only in the doc:${ONLY_DOC:- none}"
fi

echo
# THIS SUITE'S OWN DENOMINATOR. Task 0292: `0 passed, 0 failed` exits 0 and run-all.sh prints ALL
# SUITES GREEN over it, so a run whose assertions never executed reads exactly like a clean one.
[ $((PASS + FAIL)) -gt 0 ] || { echo "DENOMINATOR: 0 assertions ran. A verdict over an empty set is not a pass."; exit 2; }   # DENOMINATOR
echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ] || exit 1
