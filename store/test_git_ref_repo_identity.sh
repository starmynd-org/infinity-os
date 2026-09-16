#!/usr/bin/env bash
# Migration 19 and its writer, proven together on a scratch store. Task 0256.
#
# THE DEFECT THIS PROVES REPAIRED, measured on the live `brain` database 2026-08-16T22:50Z and
# re-measured 2026-08-17T10:22Z:
#
#   26 distinct git_refs in brain.receipt + brain.touch
#   DURABLE  0     FRAGILE 6 (scratch branches only)     LOST 20
#
# `brain-adapter receipt book --repo X` accepts any git repository and `lineage project --repo X
# <sha>` records the sha it finds there. Neither `brain.receipt` nor `brain.touch` had a column
# naming X, so a receipt booked into `/tmp/0149-cli-zf9k78n8` was indistinguishable in the store
# from one booked into the brain -- and four of the 20 LOST shas were still readable in exactly
# those `/tmp` fixture repos when the defect was found. The store could not tell "the brain lost a
# commit" from "that commit was never the brain's", and those two need opposite responses.
#
# WHY THIS FILE EXISTS AND THE PYTHON TESTS ARE NOT ENOUGH. `adapter/tests/test_store_join.py`
# proves the seam half with no database: the identity is computed, it differs per repository, it
# is stable across branches, and it reaches `store.apply`. What it CANNOT prove is that the value
# survives the INSERT into a real table with real constraints, or that the loader still works on a
# store that has not applied migration 19. Both of those are the half that broke, so both are here.
#
# Builds its own database in VERSION order across all four schema directories, the same way
# `store/test_lineage_by_construction.sh` does, and drops nothing else.
#
#   ./store/test_git_ref_repo_identity.sh
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(dirname "$HERE")"
CONTAINER="${BRAIN_PG_CONTAINER:-brain-postgres}"
SECRETS="${BRAIN_SECRET_DIR:-$HOME/.brain-postgres-secrets}"
DB="${GIT_REF_REPO_DB:-brain_git_ref_repo}"

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

# The version a file records, read out of its own INSERT rather than off its name. Newlines
# flattened first: six of these files put `VALUES (n, '...')` on the line after the INSERT, and a
# line-oriented grep silently returns nothing for all six. That trap is documented at length in
# store/test_lineage_by_construction.sh and is repeated here rather than shared, because a helper
# sourced across suites is a dependency between fixtures that has to be kept working.
recorded_version() {
  tr '\n' ' ' < "$1" \
    | grep -oE "INSERT INTO brain\.schema_migration \(version, name\)[[:space:]]*VALUES \([0-9]+," \
    | grep -oE "\([0-9]+," | tr -d '(,'
}

TMP="$(mktemp -d)"
cleanup() {
  rm -rf "$TMP"
  sup -d postgres -q -c "DROP DATABASE IF EXISTS $DB WITH (FORCE)" >/dev/null 2>&1
}
trap cleanup EXIT

echo "test_git_ref_repo_identity.sh"
echo

# ---------------------------------------------------------------- two throwaway repositories
#
# Shaped like the ones that produced the 20 LOST shas: a `/tmp` directory, `git init`, a receipt
# committed on a scratch branch. `mine` stands in for the brain and `theirs` for the fixture repo
# a lane books into and deletes an hour later. The whole claim is that the store can tell them
# apart AFTERWARDS, so they have to be genuinely different repositories, not two branches.
mkrepo() {
  local root="$1" branch="$2"
  mkdir -p "$root/departments/d/receipts"
  git -C "$root" init -q -b "$branch"
  git -C "$root" config user.email t@t
  git -C "$root" config user.name t
  # Content keyed to the repo name. Two repos whose first commit is byte-identical and lands in
  # the same second get the SAME root commit sha, so a constant here silently made the two fixture
  # repositories into one repository -- which is a real property of this identity, documented in
  # store_join.repo_identity and pinned by test_two_repositories_born_in_the_same_second_collide.
  printf 'fixture %s\n' "$(basename "$root")" > "$root/README.md"
  git -C "$root" add README.md
  git -C "$root" commit -qm base
}

# A receipt the adapter's own parser accepts: an `id:` in frontmatter and a lineage table. Written
# by hand rather than through `receipt book`, so this suite tests the STORE half without also
# depending on the entity index over the real brain (which needs a 4,000-node cache to be warm).
mkreceipt() {
  local root="$1" slug="$2" rid="$3"
  cat > "$root/departments/d/receipts/$slug.md" <<EOF
---
id: "$rid"
---

# Receipt: $slug

Booked at WAGER-14a moment \`result-produced\`.

| field | value |
| --- | --- |
| \`produced_by\` | \`null\` |
| \`produced_by_ref\` | \`$slug\` |
| \`resolution_status\` | \`unresolved\` |
EOF
  git -C "$root" add "departments/d/receipts/$slug.md"
  git -C "$root" commit -qm "receipt $slug"
  git -C "$root" rev-parse HEAD
}

mkrepo "$TMP/mine"   scratch/mine
mkrepo "$TMP/theirs" scratch/theirs
SHA_MINE="$(mkreceipt "$TMP/mine"   2026-08-17-mine   receipt-mine)"
SHA_THEIRS="$(mkreceipt "$TMP/theirs" 2026-08-17-theirs receipt-theirs)"
ROOT_MINE="$(git -C "$TMP/mine" rev-list --max-parents=0 HEAD)"
ROOT_THEIRS="$(git -C "$TMP/theirs" rev-list --max-parents=0 HEAD)"

# ---------------------------------------------------------------- the fixtures DECLARE THEMSELVES
#
# R-STORE-FIXTURE-POLICY-01, 2026-09-07. THIS SUITE WAS RED FOR THIRTEEN CHECKS AND ALL THIRTEEN HAD
# ONE CAUSE: `project()` refused these fixture repositories, stdout was empty, the JSON parse died,
# and every downstream check compared against a value that was never produced.
#
# The two refusals, in order, read out of the temp file this suite deletes on exit:
#
#   UNDECLARED_REPO   .../theirs is not a repository policy/durable-refs.json declares
#   REF_NOT_DURABLE   ... is not reachable from any durable ref (none declared exist here)
#
# `adapter/brain_adapter/store_projection.py` grew both guards under task 0297/0349, weeks after
# this suite was written, and the suite asserts the behaviour they removed. The guards are RIGHT:
# 20 of the 26 provisional refs task 0297 measured came from repositories nobody kept.
#
# WHAT THIS FIX IS NOT. It does not pass `allow_undeclared_repo`, which reaches around the first
# guard and is then stopped by the second anyway -- I measured that: the failure moves and the count
# stays 12 passed, 13 failed. It does not add a test-only escape to the durability gate, which is
# the move that gate's own comment warns against ("hiding it behind a silent default is how the door
# stopped being watched"). The PRODUCT CODE IS UNTOUCHED and the gate is fully in force below.
#
# WHAT IT IS. The policy is DATA, keyed by root-commit identity, and `BRAIN_DURABLE_POLICY` names
# the file. So the fixtures declare THEMSELVES, in a policy of their own that lives and dies with
# $TMP, and then satisfy the real gate honestly instead of being waved past it. The shipped
# `policy/durable-refs.json` is never read, never written, and never has a /tmp repository added to
# it -- which is the accident the packet asks not to cause.
POLICY="$TMP/durable-refs.json"
cat > "$POLICY" <<POLICYJSON
{
  "version": 1,
  "declared_by": "store/test_git_ref_repo_identity.sh -- fixture policy, lives in \$TMP only",
  "default": { "canon_refs": [], "ledger_refs": [], "book_refs": [], "tombstones": null },
  "repos": {
    "$ROOT_MINE":   { "name": "fixture mine",   "ledger_refs": ["scratch/mine"] },
    "$ROOT_THEIRS": { "name": "fixture theirs", "ledger_refs": ["scratch/theirs"] }
  }
}
POLICYJSON
export BRAIN_DURABLE_POLICY="$POLICY"

# AND THE DEFAULT IS DELIBERATELY EMPTY ABOVE. An undeclared repository gets no durable refs at all
# under this policy, so the gate still refuses one -- proved as its own check further down rather
# than asserted here. A fixture policy that made everything durable would be the silent default
# wearing a different hat.

check "the two fixture repositories have different identities" \
  "$([ "$ROOT_MINE" != "$ROOT_THEIRS" ] && echo different || echo same)" "different"

# ---------------------------------------------------------------- a store one migration short
#
# Built through 18 first, on purpose. `lineage project` MUST keep working on a store that has not
# applied 19 -- the live `brain` database was exactly this on 2026-08-17 -- because trading a
# working loader for an attributed one is a worse store than the one this task set out to fix.
#
# THE CEILING IS READ OUT OF THE MIGRATION UNDER TEST, never written down here. This block first
# pinned `1..19` as a literal and went red the moment 20, 21, 22 and 23 landed (task 0306,
# 2026-08-17): the schema was correct and the FIXTURE was stale, which is the most expensive kind
# of red because it teaches the next reader that this suite's failures are noise. Bumping the
# literal to 23 would have bought four migrations of quiet and then done it again. Deriving it
# also survives a renumber of 0019 itself, which the guard below proves this repo takes seriously.
#
# Anything ABOVE the ceiling is a later lane's work and belongs in that lane's suite -- the same
# rule and the same reason as store/test_lineage_by_construction.sh, which stops at 15. That the
# whole tree still builds is checked by the two scratch-db scripts, not here.
MIGRATION="$REPO/migrations/0019_git_ref_repo_identity.sql"
CEILING="$(recorded_version "$MIGRATION")"
if [ -z "$CEILING" ]; then
  bad "the migration under test records a version" "no brain.schema_migration INSERT in $MIGRATION"
  echo; echo "$PASS passed, $FAIL failed"; exit 1
fi

ORDERED=""
for f in "$REPO"/migrations/[0-9][0-9][0-9][0-9]_*.sql \
         "$REPO"/budget/schema/[0-9][0-9][0-9][0-9]_*.sql \
         "$REPO"/queue/schema/[0-9][0-9][0-9][0-9]_*.sql; do
  [ -r "$f" ] || continue
  v="$(recorded_version "$f")"
  [ -n "$v" ] || continue                  # no ledger row: not a versioned migration
  [ "$v" -le "$CEILING" ] || continue      # a later lane's migration: not this suite's to build
  ORDERED="$ORDERED$v	$f"$'\n'
done
ORDERED="$(printf '%s' "$ORDERED" | sort -n -k1,1)"

# A gap or a duplicate here means a lane took a version another file already holds, or wrote a
# migration that records none. Either one makes every assertion below unreadable, so it is caught
# in the fixture rather than surfacing as a mystery failure three checks later.
check "the fixture found versions 1..$CEILING, once each, across all four schema directories" \
  "$(printf '%s\n' "$ORDERED" | cut -f1 | tr '\n' ',')" \
  "$(seq 1 "$CEILING" | tr '\n' ',')"

sup -d postgres -q -c "DROP DATABASE IF EXISTS $DB WITH (FORCE)" >/dev/null 2>&1
sup -d postgres -q -c "CREATE DATABASE $DB" >/dev/null 2>&1

while IFS=$'\t' read -r v f; do
  [ -n "${f:-}" ] || continue
  [ "$v" -ge "$CEILING" ] && continue     # the ceiling is applied by the assertions below, not here
  if ! apply "$f" >/dev/null 2>&1; then
    bad "the store builds in version order through $((CEILING - 1))" \
        "version $v ($(basename "$f")) failed. Is $CONTAINER up?"
    echo; echo "$PASS passed, $FAIL failed"; exit 1
  fi
done <<<"$ORDERED"

check "before 19: brain.receipt has no git_repo column" \
  "$(q "SELECT count(*) FROM information_schema.columns WHERE table_schema='brain' AND table_name='receipt' AND column_name='git_repo'")" "0"

project() {
  local repo="$1" sha="$2"
  BRAIN_PG_DB="$DB" PYTHONPATH="$REPO/adapter:$REPO" python3 - "$repo" "$sha" <<'PY' 2>"$TMP/project.err"
import json, sys
from brain_adapter.store_projection import project
res = project(sys.argv[1], sys.argv[2], actor="T6-test")
print(json.dumps({"attributed": res["attributed"], "git_repo": res["git_repo"],
                  "already": res["already_projected"]}))
PY
}

OUT="$(project "$TMP/theirs" "$SHA_THEIRS")"; RC=$?
check "on a pre-19 store the projection still succeeds" "$RC" "0"
check "and it reports itself UNATTRIBUTED rather than claiming a repository" \
  "$(printf '%s' "$OUT" | python3 -c 'import json,sys; print(json.load(sys.stdin)["attributed"])')" "False"
check "the receipt row landed anyway: the loader was not traded for the column" \
  "$(q "SELECT count(*) FROM brain.receipt WHERE git_ref = '$SHA_THEIRS'")" "1"
# Skipped is never silent. A courtesy that vanishes without a word is how the next lane inherits
# this same hour, so the skip has to be findable in the output and not only in the return value.
check "and it said so on stderr, naming the migration that fixes it" \
  "$(grep -c '0019_git_ref_repo_identity.sql' "$TMP/project.err")" "1"

# ---------------------------------------------------------------- migration 19
if apply "$MIGRATION" >/dev/null 2>&1; then
  ok "migration 19 applies"
else
  bad "migration 19 applies" "see migrations/0019_git_ref_repo_identity.sql"
  echo; echo "$PASS passed, $FAIL failed"; exit 1
fi

check "the ledger records version 19" \
  "$(q "SELECT name FROM brain.schema_migration WHERE version = 19")" "0019_git_ref_repo_identity"
# BASE TABLE only. brain.git_ref_attribution is a VIEW over both tables and carries the same two
# column names, so an unfiltered information_schema query reports six columns and reads as drift.
# The same trap bit brain-receipt-reconcile.py's table discovery on 2026-08-16.
check "both tables gained both columns, all nullable" \
  "$(q "SELECT string_agg(c.table_name||'.'||c.column_name||':'||c.is_nullable, ',' ORDER BY c.table_name, c.column_name) FROM information_schema.columns c JOIN information_schema.tables t ON t.table_schema=c.table_schema AND t.table_name=c.table_name WHERE c.table_schema='brain' AND c.column_name LIKE 'git_repo%' AND t.table_type='BASE TABLE'")" \
  "receipt.git_repo:YES,receipt.git_repo_ref:YES,touch.git_repo:YES,touch.git_repo_ref:YES"
check "brain.git_ref_attribution exists" \
  "$(q "SELECT count(*) FROM information_schema.views WHERE table_schema='brain' AND table_name='git_ref_attribution'")" "1"

# Re-applying a migration is something this repo does routinely (engine/tests/test-budget-wiring.sh
# among others), so it has to be a no-op rather than an error.
if apply "$MIGRATION" >/dev/null 2>&1; then
  ok "re-applying migration 19 is idempotent"
else
  bad "re-applying migration 19 is idempotent" "the second apply failed"
fi

# The renumber guard, proven by forcing the collision rather than by reading the DO block.
sup -d "$DB" -q -c "DELETE FROM brain.schema_migration WHERE version = 19" >/dev/null 2>&1
sup -d "$DB" -q -c "INSERT INTO brain.schema_migration (version, name) VALUES (19, '0019_someone_else')" >/dev/null 2>&1
if apply "$MIGRATION" >"$TMP/guard.out" 2>&1; then
  bad "the renumber guard refuses a version another lane took" "it applied over 0019_someone_else"
else
  check "the renumber guard names the file that took the version" \
    "$(grep -c 'already taken by 0019_someone_else' "$TMP/guard.out")" "1"
fi
sup -d "$DB" -q -c "UPDATE brain.schema_migration SET name='0019_git_ref_repo_identity' WHERE version=19" >/dev/null 2>&1

# ---------------------------------------------------------------- the writer, on a store that can hold it
#
# A NEW commit, because the pre-19 projection above already claimed $SHA_THEIRS and this module has
# no UPDATE: re-projecting an unattributed row does NOT backfill it. That is deliberate -- the 26
# rows in the live store were booked when no repository was recorded, so their repository is
# genuinely unknown, and writing "the brain" into them would invent a fact the reconciliation
# proves is FALSE for at least four of them. Asserted below rather than assumed.
SHA_THEIRS2="$(mkreceipt "$TMP/theirs" 2026-08-17-theirs-2 receipt-theirs-2)"
OUT="$(project "$TMP/theirs" "$SHA_THEIRS2")"
check "after 19 the projection reports itself attributed" \
  "$(printf '%s' "$OUT" | python3 -c 'import json,sys; print(json.load(sys.stdin)["attributed"])')" "True"
check "brain.receipt records the repository the commit actually came from" \
  "$(q "SELECT git_repo FROM brain.receipt WHERE git_ref = '$SHA_THEIRS2'")" "$ROOT_THEIRS"
check "and NOT the other repository, which is the whole point" \
  "$(q "SELECT count(*) FROM brain.receipt WHERE git_ref = '$SHA_THEIRS2' AND git_repo = '$ROOT_MINE'")" "0"
check "git_repo_ref carries the readable label beside the key" \
  "$(q "SELECT git_repo_ref FROM brain.receipt WHERE git_ref = '$SHA_THEIRS2'")" "$TMP/theirs"

SHA_MINE2="$(mkreceipt "$TMP/mine" 2026-08-17-mine-2 receipt-mine-2)"
project "$TMP/mine" "$SHA_MINE2" >/dev/null
check "a commit from the OTHER repository records the OTHER identity" \
  "$(q "SELECT git_repo FROM brain.receipt WHERE git_ref = '$SHA_MINE2'")" "$ROOT_MINE"
check "so the two are now distinguishable in the store, which they were not before" \
  "$(q "SELECT count(DISTINCT git_repo) FROM brain.receipt WHERE git_ref IN ('$SHA_MINE2','$SHA_THEIRS2')")" "2"

check "re-projecting the pre-19 row does NOT backfill its repository" \
  "$(q "SELECT count(*) FROM brain.receipt WHERE git_ref = '$SHA_THEIRS' AND git_repo IS NULL")" "1"
project "$TMP/theirs" "$SHA_THEIRS" >/dev/null
check "still NULL after a re-run: unknown stays unknown rather than becoming a guess" \
  "$(q "SELECT count(*) FROM brain.receipt WHERE git_ref = '$SHA_THEIRS' AND git_repo IS NULL")" "1"

check "the attribution view sees every git_ref in both tables" \
  "$(q "SELECT count(*) FROM brain.git_ref_attribution")" \
  "$(q "SELECT (SELECT count(*) FROM brain.receipt WHERE git_ref IS NOT NULL) + (SELECT count(*) FROM brain.touch)")"

# ---------------------------------------------------------------- the reconciler reads it back
#
# The column is only worth having if the check that scores durability can USE it. Pointed at
# `theirs`, the commit booked in `mine` must score attributed-elsewhere and LOST -- which is the
# finding the live store could not produce for any of its 20 LOST shas.
# --durable-ref names the fixture's own branch. Without it the reconciler finds no `main`, refuses
# to score anything, and exits 3 -- which is CORRECT of it ("I could not look" is not "it is fine")
# and is why this passes a ref rather than letting a corpus-wide red stand in for a missing branch.
RECON="$(BRAIN_PG_DB="$DB" python3 "$HERE/bin/brain-receipt-reconcile.py" --repo "$TMP/theirs" --durable-ref scratch/theirs --json 2>"$TMP/recon.err")"
if [ -z "$RECON" ]; then
  bad "the reconciler reads the attribution column" "$(head -3 "$TMP/recon.err")"
else
  check "it computes the same identity the writer wrote" \
    "$(printf '%s' "$RECON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["repo_identity"])')" \
    "$ROOT_THEIRS"
  check "a commit from the other repository is LOST and attributed ELSEWHERE, not merely lost" \
    "$(printf '%s' "$RECON" | python3 -c "import json,sys; print('$SHA_MINE2' in json.load(sys.stdin)['lost_by_attribution']['attributed-elsewhere'])")" \
    "True"
  # Three shas, three different answers, which is the finding the live store cannot produce for a
  # single one of its 26 rows. Counted rather than spot-checked: `$SHA_THEIRS` is DURABLE here (it
  # IS a commit in this repository) so it appears in no `lost_*` list at all, and asserting its
  # absence from one would have passed for the wrong reason.
  check "attribution splits three ways: here, elsewhere, and cannot-say" \
    "$(printf '%s' "$RECON" | python3 -c 'import json,sys; d=json.load(sys.stdin)["distinct_by_attribution"]; print(d["attributed-here"], d["attributed-elsewhere"], d["unattributed"])')" \
    "1 1 1"
fi

# ---------------------------------------------------------------- THE GATE STILL BITES
#
# THE CHECK THE FIXTURE-POLICY COMMENT PROMISED. Declaring the fixtures buys nothing if the
# declaration quietly made everything durable, and a policy file is exactly the place that could
# happen without anyone noticing: the suite would go green and the gate would be gone.
#
# So: a THIRD repository, built the same way, deliberately NOT declared. Under the same fixture
# policy that just let the other two through, the projection must still refuse it. If this check
# ever passes for the wrong reason, the two above stop meaning anything.
mkrepo "$TMP/undeclared" scratch/undeclared
SHA_UNDECLARED="$(mkreceipt "$TMP/undeclared" 2026-08-17-undeclared receipt-undeclared)"
# The helper redirects its own stderr to $TMP/project.err, so a second redirect here captures an
# EMPTY file and the reason is lost. Found by this very check refusing to accept "it was refused"
# without the reason, which is the whole point of asking for one.
UNDECLARED_ERR="$TMP/project.err"
if project "$TMP/undeclared" "$SHA_UNDECLARED" >/dev/null; then
  bad "an UNDECLARED repository is still refused under the fixture policy" \
      "it was accepted, so the fixture policy has disabled the durability gate rather than satisfied it"
else
  case "$(cat "$UNDECLARED_ERR")" in
    *UNDECLARED_REPO*|*REF_NOT_DURABLE*)
      ok "an UNDECLARED repository is still refused under the fixture policy" ;;
    *)
      bad "an UNDECLARED repository is still refused under the fixture policy" \
          "it was refused, but not by the durability gate: $(tail -1 "$UNDECLARED_ERR")" ;;
  esac
fi

# AND THE PRODUCTION POLICY WAS NEVER CONSULTED. The whole fix rests on BRAIN_DURABLE_POLICY
# pointing at $TMP, so prove the pointer rather than trust it: the shipped file must not be the
# source any of this read.
check "the policy in force is the fixture's, not the shipped one" \
  "$(case "$BRAIN_DURABLE_POLICY" in "$TMP"/*) echo fixture ;; *) echo SHIPPED ;; esac)" \
  "fixture"

echo
echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
