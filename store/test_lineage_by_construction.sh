#!/usr/bin/env bash
# Migration 15: the lineage pair arrives WITH the table, not after somebody remembers to sweep.
#
# Task 0137. This exists because the thing it checks failed three times in five hours across
# three lanes while a view sat there correctly reporting each failure after the fact:
#
#   queue_defer      migration 7,  24 minutes after the sweep     -> point-fixed by migration 8
#   the budget three migration 3 applied after the sweep, in      -> point-fixed in that suite
#                    engine/tests/test-budget-wiring.sh
#   subscriber_role  migration 10, after BOTH point fixes landed  -> nothing had changed
#
# A sweep is a snapshot and cannot hold an invariant about tables that do not exist yet. The
# lesson this file encodes: PROVE THE MECHANISM ON A TABLE THE MECHANISM HAS NEVER SEEN. Every
# assertion below that matters is made against a table created AFTER migration 15, by a file
# migration 15 has no knowledge of, without editing `migrations/0005_lineage_resolution.sql`.
#
# Builds its own database in VERSION order across all four schema directories (3 is budget's,
# 7/8/11 are queue's), which is what the live store actually has, and drops nothing else.
#
#   ./store/test_lineage_by_construction.sh
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(dirname "$HERE")"
CONTAINER="${BRAIN_PG_CONTAINER:-brain-postgres}"
SECRETS="${BRAIN_SECRET_DIR:-$HOME/.brain-postgres-secrets}"
DB="${LINEAGE_CTOR_DB:-brain_lineage_ctor}"

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

# Apply one migration file. 0002 names the database in two REVOKE/GRANT lines and takes the four
# role passwords, exactly as engine/bin/scratch-db.sh does it.
apply() {
  local f="$1"
  case "$(basename "$f")" in
    0002_*)
      sed "s/ON DATABASE brain FROM/ON DATABASE $DB FROM/; s/ON DATABASE brain TO/ON DATABASE $DB TO/" "$f" \
        | sup -d "$DB" -q \
            -v owner_pw="$(sec brain-postgres-role-owner)" \
            -v producer_pw="$(sec brain-postgres-role-producer)" \
            -v subscriber_pw="$(sec brain-postgres-role-subscriber)" \
            -v runtime_pw="$(sec brain-postgres-role-runtime)" -f - ;;
    *) sup -d "$DB" -q -f - < "$f" ;;
  esac
}

echo "test_lineage_by_construction.sh"
echo

# ---------------------------------------------------------------- a store in VERSION order
#
# DISCOVERED, NOT LISTED, and that is not tidiness. A hardcoded file list is the exact defect
# `engine/bin/scratch-db.sh` and `queue/bin/queue-scratch-db.sh` have each already been fixed for:
# both named their files literally, both were silently one or three migrations behind the moment a
# lane added the next one, and both still printed "ready". Writing a third list here would
# reintroduce it for the third time.
#
# ORDERED BY THE VERSION EACH FILE'S OWN INSERT RECORDS, never by filename and never by `ls`:
# four of this repo's migration files live in other lanes' schema directories, and
# `queue/schema/0009_null_branch_act_scan.sql` records version 11 while its FILENAME says 9.
#
# STOPS AT 15 ON PURPOSE. 15 is the migration under test, so the fixture is the store as it stands
# when 15 applies. Anything numbered above it is a later lane's work and belongs in that lane's
# suite -- `queue/schema/0010_queue_item_lineage_coherent.sql` (version 16, task 0143) is the first
# such file and is deliberately outside this fixture, because two assertions below record the state
# of `queue_item` BEFORE it lands. That the whole tree still builds is checked by the two
# scratch-db scripts, not here.
CEILING=15

sup -d postgres -c "DROP DATABASE IF EXISTS $DB WITH (FORCE)" >/dev/null 2>&1
sup -d postgres -c "CREATE DATABASE $DB" >/dev/null 2>&1

# The version a file records, read out of its own INSERT rather than off its name.
#
# NEWLINES FLATTENED FIRST, and that is not a flourish. Six of these files put `VALUES (n, '...')`
# on the line AFTER the INSERT, so a line-oriented grep matches none of them -- it silently
# returned nothing for 0007, 0008, 0012, 0013, 0014 and queue's 0009, the fixture skipped all six,
# and the queue tables were simply absent while the build reported success. Measured, because that
# is exactly the shape of the bug this whole task is about: a check that finds nothing and a
# reader who concludes there was nothing to find.
recorded_version() {
  tr '\n' ' ' < "$1" \
    | grep -oE "INSERT INTO brain\.schema_migration \(version, name\)[[:space:]]*VALUES \([0-9]+," \
    | grep -oE "\([0-9]+," | tr -d '(,'
}

ORDERED=""
for f in "$REPO"/migrations/[0-9][0-9][0-9][0-9]_*.sql \
         "$REPO"/budget/schema/[0-9][0-9][0-9][0-9]_*.sql \
         "$REPO"/queue/schema/[0-9][0-9][0-9][0-9]_*.sql; do
  [ -r "$f" ] || continue
  v="$(recorded_version "$f")"
  [ -n "$v" ] || continue                 # no ledger row: not a versioned migration
  [ "$v" -le "$CEILING" ] || continue
  ORDERED="$ORDERED$v	$f"$'\n'
done
ORDERED="$(printf '%s' "$ORDERED" | sort -n -k1,1)"

# A gap or a duplicate here means a lane took a version another file already holds, or wrote a
# migration that records none. Either one makes every assertion below unreadable, so it is caught
# in the fixture rather than surfacing as a mystery failure three checks later.
check "the fixture found versions 1..$CEILING, once each, across all four schema directories" \
  "$(printf '%s\n' "$ORDERED" | cut -f1 | tr '\n' ',')" \
  "$(seq 1 "$CEILING" | tr '\n' ',')"

while IFS=$'\t' read -r v f; do
  [ -n "${f:-}" ] || continue
  [ "$v" -ge "$CEILING" ] && continue     # 15 is applied by the assertions below, not here
  if ! apply "$f" >/dev/null 2>&1; then
    bad "the store builds in version order through $((CEILING - 1))" \
        "version $v ($(basename "$f")) failed. Is brain-postgres up?"
    echo; echo "$PASS passed, $FAIL failed"; exit 1
  fi
done <<<"$ORDERED"

# ---------------------------------------------------------------- the drift migration 15 inherits
#
# Asserted BEFORE applying 15, because a fix that cannot be shown to have had something to fix is
# a fix nobody can check. This is instance 3 reproduced from the files: migration 10 created
# brain.subscriber_role with produced_by and neither companion, after both earlier point fixes.
check "before 15: subscriber_role is the drift migration 10 left behind" \
  "$(q "SELECT string_agg(table_name, ',' ORDER BY table_name) FROM brain.lineage_column_drift")" \
  "subscriber_role"

if apply "$REPO/migrations/0015_lineage_columns_by_construction.sql" >/dev/null 2>&1; then
  ok "migration 15 applies"
else
  bad "migration 15 applies" "see the output of applying migrations/0015_lineage_columns_by_construction.sql"
  echo; echo "$PASS passed, $FAIL failed"; exit 1
fi

check "the ledger records version 15" \
  "$(q "SELECT name FROM brain.schema_migration WHERE version = 15")" \
  "0015_lineage_columns_by_construction"

check "15 backfilled what the sweep missed: lineage_column_drift is empty" \
  "$(q "SELECT count(*) FROM brain.lineage_column_drift")" "0"

# The fuller assertion. `queue_item` is EXPECTED here and is not this task's to repair: migration 7
# gave it all three columns and no named CHECKs, and its `resolution_status NOT NULL DEFAULT
# 'resolved'` has to be dropped before the coherence CHECK can be added without refusing the queue
# lane's own inserts. That is task 0143, queue lane. Written as "nothing EXCEPT queue_item" so this
# assertion stays true both before and after 0143 lands.
echo "  --- brain.lineage_drift, right after migration 15 and before any replay:"
sup -d "$DB" -c "SELECT * FROM brain.lineage_drift" | sed 's/^/      /'
check "lineage_drift holds nothing except queue_item, which task 0143 owns" \
  "$(q "SELECT count(*) FROM brain.lineage_drift WHERE table_name <> 'queue_item'")" "0"

# Recorded because it is a live hazard and not an aside. `queue_item` gained `produced_by` in
# migration 7, i.e. AFTER migration 5 swept, so migration 5 has never iterated it. Re-applying
# migration 5 -- which several files in this repo do routinely, `engine/tests/test-budget-wiring.sh`
# among them -- makes its loop reach `queue_item` for the first time and DROP/ADD both named
# CHECKs onto it, `queue_item_lineage_coherent` included. Combined with
# `resolution_status NOT NULL DEFAULT 'resolved'`, that constraint refuses every INSERT that omits
# a producer. So a replay of an old migration is enough to change the queue lane's write path with
# no migration anywhere naming the change. Measured, in this order, on this database.
check "before a replay, queue_item has neither named CHECK" \
  "$(q "SELECT count(*) FROM pg_constraint WHERE conrelid='brain.queue_item'::regclass AND conname IN ('queue_item_resolution_status_enum','queue_item_lineage_coherent')")" "0"

check "the event trigger is installed and enabled" \
  "$(q "SELECT evtenabled FROM pg_event_trigger WHERE evtname = 'brain_lineage_columns'")" "O"

# ---------------------------------------------------------------- THE DEFINITION OF DONE
#
# A table created by a migration written AFTER 15, in a file 15 has never seen, with nobody
# editing 0005. This is a heredoc rather than a committed migration on purpose: a real migration
# 16 would be a schema change to the live store that this task has no mandate to make, and the
# property under test is precisely that the mechanism does not care where the DDL came from.
sup -d "$DB" -q >/dev/null 2>&1 <<'M16'
BEGIN;
SET search_path TO brain, public;
CREATE TABLE brain.t0137_later (
  id           bigserial PRIMARY KEY,
  note         text NOT NULL DEFAULT '',
  produced_by  text
);
COMMIT;
M16

check "a table created after 15 is born with produced_by_ref" \
  "$(q "SELECT count(*) FROM information_schema.columns WHERE table_schema='brain' AND table_name='t0137_later' AND column_name='produced_by_ref'")" "1"
check "a table created after 15 is born with resolution_status" \
  "$(q "SELECT count(*) FROM information_schema.columns WHERE table_schema='brain' AND table_name='t0137_later' AND column_name='resolution_status'")" "1"
check "a table created after 15 is born with the vocabulary CHECK" \
  "$(q "SELECT count(*) FROM pg_constraint WHERE conrelid='brain.t0137_later'::regclass AND conname='t0137_later_resolution_status_enum'")" "1"
check "a table created after 15 is born with the lie-detector" \
  "$(q "SELECT count(*) FROM pg_constraint WHERE conrelid='brain.t0137_later'::regclass AND conname='t0137_later_lineage_coherent'")" "1"
check "and it never appears in either drift view" \
  "$(q "SELECT count(*) FROM brain.lineage_column_drift WHERE table_name='t0137_later'")" "0"

# Nobody edited 0005 to get any of the above. Checked against git rather than asserted in prose:
# the whole failure mode this file guards is a rule that lives in a comment somebody has to read.
if git -C "$REPO" diff --quiet HEAD -- migrations/0005_lineage_resolution.sql 2>/dev/null; then
  ok "migrations/0005_lineage_resolution.sql is byte-identical to git HEAD"
else
  bad "migrations/0005_lineage_resolution.sql is byte-identical to git HEAD" \
      "0005 was edited. The whole point is that a later table needs no change there."
fi

# ---------------------------------------------------------------- the columns are not decoration
#
# Present-and-inert would pass every assertion above. The lie-detector is checked by making it
# REFUSE, as the SUPERUSER, which is the one thing application validation cannot do.
FABRICATED="$(sup -tA -d "$DB" -c \
  "INSERT INTO brain.t0137_later (produced_by, produced_by_ref, resolution_status)
   VALUES ('invented-node-id', 'some-ref', 'unresolved')" 2>&1)"
if grep -q "t0137_later_lineage_coherent" <<<"$FABRICATED"; then
  ok "the new table refuses a FABRICATED id (unresolved + a producer) for the superuser"
else
  bad "the new table refuses a FABRICATED id (unresolved + a producer) for the superuser" \
      "the insert was not refused by t0137_later_lineage_coherent: $FABRICATED"
fi

BADWORD="$(sup -tA -d "$DB" -c \
  "INSERT INTO brain.t0137_later (resolution_status) VALUES ('probably')" 2>&1)"
if grep -q "t0137_later_resolution_status_enum" <<<"$BADWORD"; then
  ok "the new table refuses a status outside the closed vocabulary"
else
  bad "the new table refuses a status outside the closed vocabulary" "$BADWORD"
fi

# THE FOURTH STATE SURVIVES. NULL means no resolution was ever attempted and is not a synonym for
# 'unresolved'. A column added as NOT NULL DEFAULT 'resolved' would pass every structural check
# above and delete this, which is exactly the shape queue_item is in.
sup -d "$DB" -q -c \
  "INSERT INTO brain.t0137_later (produced_by) VALUES ('d3-ingest')" >/dev/null 2>&1
check "the fourth state survives: a producer NAME with no resolution attempt is still writable" \
  "$(q "SELECT count(*) FROM brain.t0137_later WHERE produced_by='d3-ingest' AND resolution_status IS NULL")" "1"

# ---------------------------------------------------------------- the other door
#
# A table that gains produced_by by ALTER rather than by CREATE. Same invariant, different DDL.
sup -d "$DB" -q -c "CREATE TABLE brain.t0137_altered (id int PRIMARY KEY)" >/dev/null 2>&1
check "a table with no produced_by gets nothing" \
  "$(q "SELECT count(*) FROM information_schema.columns WHERE table_schema='brain' AND table_name='t0137_altered' AND column_name IN ('produced_by_ref','resolution_status')")" "0"

sup -d "$DB" -q -c "ALTER TABLE brain.t0137_altered ADD COLUMN produced_by text" >/dev/null 2>&1
check "ALTER TABLE ADD COLUMN produced_by brings both companions and both CHECKs" \
  "$(q "SELECT (SELECT count(*) FROM information_schema.columns WHERE table_schema='brain' AND table_name='t0137_altered' AND column_name IN ('produced_by_ref','resolution_status'))
      + (SELECT count(*) FROM pg_constraint WHERE conrelid='brain.t0137_altered'::regclass AND conname IN ('t0137_altered_resolution_status_enum','t0137_altered_lineage_coherent'))")" "4"

# ---------------------------------------------------------------- scoped, not global
sup -d "$DB" -q -c "CREATE SCHEMA IF NOT EXISTS t0137_elsewhere" >/dev/null 2>&1
sup -d "$DB" -q -c "CREATE TABLE t0137_elsewhere.other (id int, produced_by text)" >/dev/null 2>&1
check "a produced_by outside schema brain is left alone" \
  "$(q "SELECT count(*) FROM information_schema.columns WHERE table_schema='t0137_elsewhere' AND table_name='other' AND column_name IN ('produced_by_ref','resolution_status')")" "0"

# ---------------------------------------------------------------- the primitive refuses honestly
NOPRODUCER="$(sup -tA -d "$DB" -c "SELECT brain.add_lineage_columns('brain.t0137_altered_pkey'::regclass)" 2>&1)"
if grep -qi "relkind" <<<"$NOPRODUCER"; then
  ok "add_lineage_columns refuses a non-table"
else
  bad "add_lineage_columns refuses a non-table" "$NOPRODUCER"
fi

sup -d "$DB" -q -c "CREATE TABLE brain.t0137_bare (id int)" >/dev/null 2>&1
NOCOL="$(sup -tA -d "$DB" -c "SELECT brain.add_lineage_columns('brain.t0137_bare'::regclass)" 2>&1)"
if grep -q "has no produced_by column" <<<"$NOCOL"; then
  ok "add_lineage_columns refuses a table with no produced_by rather than inventing one"
else
  bad "add_lineage_columns refuses a table with no produced_by rather than inventing one" "$NOCOL"
fi

# ---------------------------------------------------------------- replay, which is where a
#                                                                  careless trigger would break
#
# Migration 5 and migration 8 both DROP CONSTRAINT IF EXISTS and then ADD CONSTRAINT, on tables
# the trigger is watching. A trigger that re-added the constraint on any ALTER would re-add it in
# the window between those two statements and the ADD would die with "already exists". This is
# the assertion that the "only when a column is missing" condition in the trigger is doing its
# job; it is the reason that condition is written the way it is.
if apply "$REPO/migrations/0005_lineage_resolution.sql" >/dev/null 2>&1; then
  ok "migration 5 still replays cleanly with the trigger installed"
else
  bad "migration 5 still replays cleanly with the trigger installed" \
      "the trigger is racing migration 5's DROP CONSTRAINT / ADD CONSTRAINT cycle"
fi
if apply "$REPO/queue/schema/0008_queue_defer_lineage.sql" >/dev/null 2>&1; then
  ok "migration 8 still replays cleanly with the trigger installed"
else
  bad "migration 8 still replays cleanly with the trigger installed" "same race, queue lane's file"
fi
if apply "$REPO/migrations/0015_lineage_columns_by_construction.sql" >/dev/null 2>&1; then
  ok "migration 15 is itself re-runnable"
else
  bad "migration 15 is itself re-runnable" "a second application should be a no-op"
fi

check "after every replay, lineage_column_drift is still empty" \
  "$(q "SELECT count(*) FROM brain.lineage_column_drift")" "0"
check "after every replay, the four probe tables still carry their full record" \
  "$(q "SELECT count(*) FROM brain.lineage_drift WHERE table_name LIKE 't0137%'")" "0"

# The other half of the hazard flagged above, now demonstrated rather than argued: the replay of
# migration 5 two blocks up reached `queue_item` for the first time and armed the lie-detector on
# it. Nothing in this file asked for that and no migration records it. Task 0143 (queue lane) owns
# the decision; this assertion exists so that whoever changes migration 5's loop, or stops replaying
# it, learns what else that replay is currently doing.
check "replaying migration 5 retro-fits both named CHECKs onto queue_item (0143's decision, made by a replay)" \
  "$(q "SELECT count(*) FROM pg_constraint WHERE conrelid='brain.queue_item'::regclass AND conname IN ('queue_item_resolution_status_enum','queue_item_lineage_coherent')")" "2"

# What that costs, exercised rather than argued. Branches on the CAUSE and not on the outcome, so
# this stays a real assertion after task 0143 drops the NOT NULL DEFAULT rather than passing either
# way. `queue_item` is the ONLY table where the two interact: everywhere else resolution_status is
# nullable with no default, so an INSERT that names no producer leaves it NULL and the coherence
# CHECK's ELSE-true branch lets the row through.
DEFAULTED="$(q "SELECT count(*) FROM information_schema.columns
                 WHERE table_schema='brain' AND table_name='queue_item'
                   AND column_name='resolution_status' AND is_nullable='NO'
                   AND column_default = '''resolved''::text'")"
NO_PRODUCER="$(sup -tA -d "$DB" -c \
  "INSERT INTO brain.queue_item (source_type, source_id) VALUES ('work_item','0001')" 2>&1)"
if [ "$DEFAULTED" = "1" ]; then
  if grep -q "queue_item_lineage_coherent" <<<"$NO_PRODUCER"; then
    ok "with the NOT NULL DEFAULT still in place, that replay REFUSES a queue_item INSERT naming no producer"
  else
    bad "with the NOT NULL DEFAULT still in place, that replay REFUSES a queue_item INSERT naming no producer" \
        "expected queue_item_lineage_coherent to fire; got: $NO_PRODUCER"
  fi
else
  if grep -q "ERROR" <<<"$NO_PRODUCER"; then
    bad "0143 dropped the NOT NULL DEFAULT, so a queue_item INSERT naming no producer lands" "$NO_PRODUCER"
  else
    ok "0143 dropped the NOT NULL DEFAULT, so a queue_item INSERT naming no producer lands"
  fi
fi

echo
echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
