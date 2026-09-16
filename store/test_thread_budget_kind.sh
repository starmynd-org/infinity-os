#!/usr/bin/env bash
# Migration 13: a spend stop against a live task must land, and must read as a spend stop.
#
# Task 0120. This exists because the thing it checks was dead in production for a day while every
# suite above it was green: `budget/transitions.py` filed its task-thread line as kind `budget`,
# migration 1's `brain.thread.kind` CHECK had no such word, and so EVERY `budget stop` carrying a
# `work_item_id` raised CheckViolation and rolled the whole transaction back -- no incident, no
# stop, nothing for `budget halt` to find. The tests that covered `budget stop` all passed,
# because none of them passed a `work_item_id` that exists, so none of them reached the branch.
# The lesson this file encodes: EXERCISE THE BRANCH WITH REAL FOREIGN KEYS.
#
# WHY NOT engine/tests/test-budget-wiring.sh, which 0120's brief names. HISTORY, AND IT IS CLOSED:
# that suite was red before this task touched anything, for an unrelated reason. `scratch-db.sh`
# applied `migrations/*` in FILENAME order and the suite applied `budget/schema/0003_budget.sql`
# afterwards, so migration 5 -- whose job is to add `produced_by_ref` and `resolution_status`
# beside every `produced_by` -- swept BEFORE the budget tables existed and covered nothing, while
# `brain.schema_migration` still reported 5 applied. Every INSERT in `budget/transitions.py` then
# failed with UndefinedColumn. Measured 2026-08-16T16:45Z: 28 passed, 8 failed, IDENTICALLY with
# and without migration 13 in the tree.
#
# DO NOT READ THE PARAGRAPH ABOVE AS CURRENT. Commit e9c0ba7 (task 0212) rewrote
# `engine/bin/scratch-db.sh` to walk `migrations/` + `budget/schema/` + `queue/schema/` ordered by
# the version each file's own INSERT records, so 3 lands before 5 by construction; migration 15
# then made the invariant hold inside CREATE TABLE. Re-measured on task 0168, 2026-08-16T19:03Z,
# on a database built by `scratch-db.sh create` and nothing else: ledger prints 3 before 5,
# `applied_at` confirms it ran that way, all three budget tables carry the full triple, and
# test-budget-wiring.sh read 52 passed / 0 failed. The 28/8 above no longer reproduces. It is
# left on the record because the FAILURE SHAPE is the lesson -- a ledger that says the migration
# ran while the columns are absent -- not because the suite is still red.
#
# THAT 52/0 IS A TIMESTAMP, NOT A STANDING FACT, and re-measuring it is the point of this note.
# Same task, 2026-08-16T22:25Z, `BUDGET_WIRE_DB=brain_t3_0168_bw`: 47 passed, 2 failed, exit 1.
# NOT the ordering defect, and the five assertions that would catch it are all green -- including
# the two task 0164 added that read the BUILDER's output before the suite repairs anything. Both
# reds are static greps against `engine/bin/swarm-run`, which another lane was editing DURING the
# run (mtime 22:23:06Z, mid-run): the `budget halt` call gained `${RUN_ROW_ID:+--run ...}` so the
# asserted one-line literal no longer matches, and a second, earlier guard block now precedes the
# `$!` capture that the ordering check compares against by FIRST match. The arithmetic reconciles
# and is worth knowing before reading any count from that suite: it scores each python block as a
# unit (`PASS+5` or `FAIL+1`), so one red inside the ordering block turns 52/0 into 47/2 while 52
# assertion LINES still print. 47 + 2 is not 52 there, and that is the scoring, not a lost test.
#
# This file builds its store in VERSION order (1,2,3,4,5,6,9,13), which is what live `brain`
# actually had at 13, so it tests migration 13 rather than that ordering defect. That list is
# hardcoded ON PURPOSE, to pin the store at 13; it is not a general builder and must not grow into
# one. Anything that wants every lane's schema calls `engine/bin/scratch-db.sh`.
#
#   ./store/test_thread_budget_kind.sh          # builds its own database and drops nothing else
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(dirname "$HERE")"
CONTAINER="${BRAIN_PG_CONTAINER:-brain-postgres}"
SECRETS="${BRAIN_SECRET_DIR:-$HOME/.brain-postgres-secrets}"
DB="${THREAD_KIND_DB:-brain_t5_0120}"

case "$DB" in
  brain)         echo "refusing to run against the live store database 'brain'." >&2; exit 1 ;;
  brain_scratch) echo "refusing to run against the shared scratch database: another lane truncates it." >&2; exit 1 ;;
esac

PASS=0; FAIL=0
ok()   { PASS=$((PASS+1)); printf '  ok    %s\n' "$1"; }
bad()  { FAIL=$((FAIL+1)); printf '  FAIL  %s\n' "$1"; [ -n "${2:-}" ] && printf '        %s\n' "$2"; }
check(){ if [ "$2" = "$3" ]; then ok "$1"; else bad "$1" "wanted [$3], got [$2]"; fi; }

sec() { cat "$SECRETS/$1"; }
sup() { docker exec -i -e PGPASSWORD="$(sec brain-postgres-bootstrap-superuser)" \
          "$CONTAINER" psql -v ON_ERROR_STOP=1 -h 127.0.0.1 -U postgres "$@"; }
q()   { sup -tA -d "$DB" -c "$1"; }

echo "test_thread_budget_kind.sh"
echo

# ---------------------------------------------------------------- a store in VERSION order
sup -d postgres -c "DROP DATABASE IF EXISTS $DB WITH (FORCE)" >/dev/null 2>&1
sup -d postgres -c "CREATE DATABASE $DB" >/dev/null 2>&1 || { echo "cannot create $DB. Is $CONTAINER up?" >&2; exit 1; }
sup -q -d "$DB" -f - < "$REPO/migrations/0001_initial.sql" >/dev/null 2>&1
# 0002 names the database in two REVOKE/GRANT lines; substituted here rather than by editing it.
sed "s/ON DATABASE brain FROM/ON DATABASE $DB FROM/; s/ON DATABASE brain TO/ON DATABASE $DB TO/" \
    "$REPO/migrations/0002_roles.sql" \
  | sup -q -d "$DB" -v owner_pw="$(sec brain-postgres-role-owner)" \
        -v producer_pw="$(sec brain-postgres-role-producer)" \
        -v subscriber_pw="$(sec brain-postgres-role-subscriber)" \
        -v runtime_pw="$(sec brain-postgres-role-runtime)" -f - >/dev/null 2>&1
for f in "$REPO/budget/schema/0003_budget.sql" \
         "$REPO/migrations/0004_touch_wager_2c.sql" \
         "$REPO/migrations/0005_lineage_resolution.sql" \
         "$REPO/migrations/0006_signal_numeric_vocabulary.sql" \
         "$REPO/migrations/0009_touch_unresolved_component.sql" \
         "$REPO/migrations/0013_thread_budget_kind.sql"; do
  sup -q -d "$DB" -f - < "$f" >/dev/null 2>&1 || bad "applying $(basename "$f")"
done
check "the store is built through migration 13" "$(q "SELECT max(version) FROM brain.schema_migration")" "13"

# ---------------------------------------------------------------- the vocabulary itself
echo
echo "  the vocabulary"
DEF="$(q "SELECT pg_get_constraintdef(c.oid) FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid JOIN pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname='brain' AND t.relname='thread' AND c.conname='thread_kind_check'")"
case "$DEF" in *"'budget'"*) ok "thread_kind_check knows the word 'budget'" ;;
               *) bad "thread_kind_check knows the word 'budget'" "definition: $DEF" ;; esac
LOST=""
for w in post claim note msg ask answer done block fail reopen cancel artifact heartbeat reap set accept event; do
  case "$DEF" in *"'$w'"*) ;; *) LOST="$LOST $w" ;; esac
done
check "migration 1's seventeen all survived" "${LOST:-none}" "none"
q "INSERT INTO brain.work_item (id,title,lane,state,claimed_by) VALUES ('9100','0120 verification: a real task to stop','store','active','T5')" >/dev/null
q "INSERT INTO brain.thread (work_item_id,kind,text) VALUES ('9100','budgetary','a near miss')" >/dev/null 2>&1
check "a word that is NOT in the list is still refused" \
      "$(q "SELECT count(*) FROM brain.thread WHERE kind='budgetary'")" "0"

# ---------------------------------------------------------------- the real call, on a real task
#
# `RunGuard._stop`'s store.apply, argument for argument, through the narrow waist. Not a hand
# INSERT: a hand INSERT would not have caught the original defect either.
run_stop() {
  ( cd "$REPO" && BRAIN_PG_DB="$DB" python3 - "$1" <<'PY'
import sys
import store
import budget.transitions           # noqa: F401  registers the verbs
try:
    row = store.apply("budget stop", actor="T5", kind="hard_stop",
                      scope_type="work_item", scope_id="9100",
                      action_taken="run_stopped", detected_by="sweep",
                      agent="T5", lane="store", work_item_id="9100",
                      session_id="t5-0120-session", spend_usd="1.25",
                      reason=sys.argv[1], by="T5")
    print(f"OK {row['prescribed_disposition']}")
except Exception as exc:                                     # noqa: BLE001
    print(f"RAISED {type(exc).__name__}")
PY
  ) 2>/dev/null | tail -1
}
wipe() { q "DELETE FROM brain.budget_incident WHERE work_item_id='9100'; DELETE FROM brain.thread WHERE work_item_id='9100'" >/dev/null; }

echo
echo "  scene 1: the stop lands, and it reads as a spend stop"
wipe
R="$(run_stop '0120 verification: ceiling crossed under a live run')"
check "the call returned" "$R" "OK block"
check "the incident row was filed" "$(q "SELECT count(*) FROM brain.budget_incident WHERE work_item_id='9100'")" "1"
check "exactly one thread event" "$(q "SELECT count(*) FROM brain.thread WHERE work_item_id='9100'")" "1"
check "it reads as a BUDGET event, not a note" "$(q "SELECT kind FROM brain.thread WHERE work_item_id='9100'")" "budget"
check "the text still names the incident kind" \
      "$(q "SELECT left(text,22) FROM brain.thread WHERE work_item_id='9100'")" "budget hard_stop on wo"
check "both rows share one transaction timestamp" \
      "$(q "SELECT count(*) FROM brain.thread t JOIN brain.budget_incident i ON i.work_item_id=t.work_item_id AND i.occurred_at=t.ts WHERE t.work_item_id='9100'")" "1"

# ---------------------------------------------------------------- the mutation
#
# Put migration 1's seventeen words back and make the SAME call. If this scene stays green, this
# file is testing nothing: the constraint is what carries the fix.
echo
echo "  scene 2: MUTATION -- migration 1's vocabulary back, the same call must file NOTHING"
wipe
q "ALTER TABLE brain.thread DROP CONSTRAINT IF EXISTS thread_kind_check;
   ALTER TABLE brain.thread ADD CONSTRAINT thread_kind_check CHECK (kind IN
     ('post','claim','note','msg','ask','answer','done','block','fail','reopen','cancel',
      'artifact','heartbeat','reap','set','accept','event'));" >/dev/null
R="$(run_stop '0120 verification: this must roll back')"
check "the call raised instead of returning" "$R" "RAISED CheckViolation"
check "NO incident survived: the WHOLE transaction rolled back" \
      "$(q "SELECT count(*) FROM brain.budget_incident WHERE work_item_id='9100'")" "0"
check "and no thread event either" "$(q "SELECT count(*) FROM brain.thread WHERE work_item_id='9100'")" "0"

echo
echo "  scene 3: the word restored -- it lands again"
q "ALTER TABLE brain.thread DROP CONSTRAINT IF EXISTS thread_kind_check;
   ALTER TABLE brain.thread ADD CONSTRAINT thread_kind_check CHECK (kind IN
     ('post','claim','note','msg','ask','answer','done','block','fail','reopen','cancel',
      'artifact','heartbeat','reap','set','accept','event','budget'));" >/dev/null
wipe
R="$(run_stop '0120 verification: and again once the store has the word')"
check "the call returned" "$R" "OK block"
check "it reads as a BUDGET event" "$(q "SELECT kind FROM brain.thread WHERE work_item_id='9100'")" "budget"

# ---------------------------------------------------------------- the backfill predicate
#
# Migration 13 also corrects the rows filed as `note` while the word was missing. The predicate is
# a JOIN on the shared transaction timestamp, not a prose match, and these three decoys are why.
echo
echo "  scene 4: the backfill moves the real pair and nothing that merely looks like it"
q "INSERT INTO brain.work_item (id,title,lane) VALUES ('9101','0120 decoys','store') ON CONFLICT DO NOTHING" >/dev/null
wipe
q "UPDATE brain.thread SET kind='note' WHERE work_item_id='9100'" >/dev/null   # as it was filed pre-13
q "BEGIN;
   INSERT INTO brain.budget_incident (kind,scope_type,scope_id,action_taken,detected_by,prescribed_disposition,work_item_id,detail)
     VALUES ('hard_stop','work_item','9100','run_stopped','sweep','block','9100','A: the genuine pair');
   INSERT INTO brain.thread (work_item_id,kind,text) VALUES ('9100','note','budget hard_stop on work_item:9100: A');
   COMMIT;" >/dev/null
q "INSERT INTO brain.thread (work_item_id,kind,text) VALUES ('9101','note','budget hard_stop on work_item:9101: B hand-typed, no incident behind it')" >/dev/null
q "INSERT INTO brain.budget_incident (kind,scope_type,scope_id,action_taken,detected_by,prescribed_disposition,work_item_id,detail)
     VALUES ('manual_stop','work_item','9101','dispatch_refused','manual','block','9101','C: incident and line in DIFFERENT transactions')" >/dev/null
q "INSERT INTO brain.thread (work_item_id,kind,text) VALUES ('9101','note','budget manual_stop on work_item:9101: C')" >/dev/null
q "INSERT INTO brain.thread (work_item_id,kind,text) VALUES ('9101','note','D: an ordinary note that mentions budget hard_stop on work_item')" >/dev/null
sup -q -d "$DB" -f - < "$REPO/migrations/0013_thread_budget_kind.sql" >/dev/null 2>&1
check "the genuine pair moved to kind budget" \
      "$(q "SELECT count(*) FROM brain.thread WHERE work_item_id='9100' AND kind='budget' AND text LIKE '%: A'")" "1"
check "B (prose-alike, no incident) stayed a note" \
      "$(q "SELECT count(*) FROM brain.thread WHERE text LIKE '%B hand-typed%' AND kind='note'")" "1"
check "C (real incident, separate transaction) stayed a note" \
      "$(q "SELECT count(*) FROM brain.thread WHERE text LIKE '%9101: C' AND kind='note'")" "1"
check "D (an ordinary note) stayed a note" \
      "$(q "SELECT count(*) FROM brain.thread WHERE text LIKE 'D: an ordinary%' AND kind='note'")" "1"
check "re-running the migration moves nothing further (idempotent)" \
      "$(sup -d "$DB" -f - < "$REPO/migrations/0013_thread_budget_kind.sql" 2>&1 | grep -c 'reclassified note -> budget' )" "1"
check "  ...and that second run reclassified 0" \
      "$(sup -d "$DB" -f - < "$REPO/migrations/0013_thread_budget_kind.sql" 2>&1 | grep -o '13: [0-9]* thread row' | grep -o '[0-9]*' | tail -1)" "0"

echo
echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ] || exit 1
