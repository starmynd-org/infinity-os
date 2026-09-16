#!/usr/bin/env bash
# The drop test.
#
#   "Losing the entire store must cost live queue position and history, and ZERO KNOWLEDGE."
#
# That is the invariant every design question in this program resolves against, and it is the
# kind of claim that is comfortable to assert and unpleasant to check. So this checks it, in the
# three halves it actually has:
#
#   the CONTROL   (stages 2a, 4a)  three DISTINCT canon commits planted by this script and then
#                                  recovered. Proves the recovery MECHANISM works, and nothing
#                                  else. A control, labelled as one.
#   the CORPUS    (stages 2b, 4b)  the receipts ACTUALLY IN THE STORE, copied into the scratch
#                                  database, destroyed with it, and recovered from git alone.
#                                  This is the invariant. The control is only the instrument.
#   the ATTRIBUTION (stage 6)      the same corpus read from the LIVE store, which is where the
#                                  migration-19 `git_repo` axis lives. Stage 4b runs with the
#                                  store gone and therefore cannot see it.
#
# ## Why stage 2b exists, which is the whole point of this file
#
# Until 2026-08-17 stage 2 planted THREE RECEIPTS OF ITS OWN from canon commits and stage 4
# recovered those three. That is self-certifying: it seeds its own evidence and then finds it, so
# it passed every time while the receipts actually in the store pointed at commits that exist
# nowhere. Task 0256 added stage 6, which measures the real corpus and turned the test red.
# Task 0297 moved the corpus into the RECOVERY path as well, so the sentence "recovered from git
# with the store gone" is now spoken about the store's own rows and not about fixtures.
#
# Worse, the old control was not even three commits: CANON_COMMIT and DECISION_COMMIT both fell
# back to RECEIPT_COMMIT when their pathspec matched nothing, and `-- decisions/` matches nothing
# in this repo because decisions live at `knowledge/<ns>/decisions/`. It recovered 5a9675a7 twice
# and called it three. The control below asserts distinctness rather than assuming it.
#
# It never WRITES to the live database. Stage 2b reads it with `pg_dump --data-only`, stage 6
# reads it through brain-receipt-reconcile.py (`store.session.read`). The scratch name is fixed
# and refused if it equals $DB.
#
#   ./brain-drop-test.sh [path-to-a-git-repo-holding-canon]
set -euo pipefail

NAME="brain-postgres"
DB="${BRAIN_PG_DB:-brain}"
SCRATCH="${DB}_droptest_scratch"
SECRETS="${BRAIN_SECRET_DIR:-$HOME/.brain-postgres-secrets}"
CANON_REPO="${1:-/mnt/c/Users/you/repos/your-brain}"

die() { printf 'drop-test: %s\n' "$*" >&2; exit 1; }
[ "$SCRATCH" != "$DB" ] || die "scratch name collides with the live database"
[ -d "$CANON_REPO/.git" ] || die "$CANON_REPO is not a git repo"

SU="$(cat "$SECRETS/brain-postgres-bootstrap-superuser")"
psu() { docker exec -i -e PGPASSWORD="$SU" "$NAME" "$@"; }
S()   { psu psql -v ON_ERROR_STOP=1 -U postgres -d "$SCRATCH" "$@" </dev/null; }

echo "=== 1. build a scratch copy of the store ==="
psu psql -U postgres -d postgres -c "DROP DATABASE IF EXISTS \"$SCRATCH\"" >/dev/null
psu psql -U postgres -d postgres -c "CREATE DATABASE \"$SCRATCH\"" >/dev/null
psu pg_dump -U postgres -d "$DB" -Fc --schema-only \
  | psu pg_restore -U postgres -d "$SCRATCH" --no-owner --no-privileges >/dev/null 2>&1 || true
S -tAc "SELECT 'tables in scratch: '||count(*) FROM information_schema.tables WHERE table_schema='brain' AND table_type='BASE TABLE'"

echo
echo "=== 2a. the CORPUS: the store's OWN receipts and touches, copied in ==="
# The rows themselves, `pg_dump --data-only`, ids and all -- not a re-derivation and not a
# fixture. brain.receipt and brain.touch carry no foreign keys and no user triggers (checked
# against pg_constraint/pg_trigger on 2026-08-17), so the data restores standalone into the
# schema-only scratch built in stage 1. Live is only read.
#
# This runs BEFORE the control is planted, and the sequence is advanced afterwards, because the
# dump carries the live ids: planting first hands the control id=1 and the COPY then dies on
# receipt_pkey. Restoring real rows into an empty table and moving the sequence out of their way
# is the order that keeps the corpus byte-identical to the store's.
psu pg_dump -U postgres -d "$DB" --data-only --table=brain.receipt --table=brain.touch \
  | psu psql -v ON_ERROR_STOP=1 -U postgres -d "$SCRATCH" -q >/dev/null \
  || die "could not copy the store's receipts into the scratch database. The corpus half of this
test cannot be skipped quietly."
# `||` reads the exit of psql, not of pg_dump: a pg_dump that dies mid-pipe hands psql an empty
# input, psql exits 0, and the pipeline reads as success with an empty corpus. The row count below
# is what actually catches that, so it is a guard and not a progress message.
COPIED=$(S -tAc "SELECT count(*) FROM brain.receipt")
[ "${COPIED:-0}" -gt 0 ] || die "copied 0 receipts out of the live store. A corpus of nothing is
not a pass -- check that brain.receipt is populated before believing this number."
echo "  copied $COPIED receipt rows and $(S -tAc 'SELECT count(*) FROM brain.touch') touch rows"

# `--table` dumps carry no setval, so the identity counter in the scratch copy still points at 1
# while the restored rows occupy 1..N.
for t in receipt touch; do
  S -q -c "SELECT setval(pg_get_serial_sequence('brain.$t','id'),
                         COALESCE((SELECT max(id) FROM brain.$t), 0) + 1, false)" >/dev/null
done

echo
echo "=== 2b. the CONTROL: three DISTINCT canon commits, planted by this script ==="
# Three real commits from the canon repo, so the recovery below reads git and not a fixture --
# and three DISTINCT ones, because a control that is secretly one commit repeated proves a third
# of what it claims. `-- decisions/` is deliberately absent: it matches nothing here (decisions
# live at knowledge/<ns>/decisions/) and silently collapsed the old control from 3 to 2.
CONTROL=$(
  { git -C "$CANON_REPO" log -1 --format=%H -- tools/port-registry.md
    git -C "$CANON_REPO" log -1 --format=%H -- 'knowledge/*.md'
    git -C "$CANON_REPO" log -1 --format=%H -- 'knowledge/*/decisions/*.md'
    git -C "$CANON_REPO" log -6 --format=%H main
  } 2>/dev/null | awk 'NF && !seen[$0]++' | head -3
)
CONTROL_N=$(printf '%s\n' "$CONTROL" | grep -c . || true)
[ "${CONTROL_N:-0}" -eq 3 ] || die "could only find ${CONTROL_N:-0} distinct canon commits for the
control. Three is the floor: a control that is secretly one commit repeated is what this replaced."

for c in $CONTROL; do
  S -q -c "INSERT INTO brain.receipt (action, subject_type, subject_id, detail, git_ref, canon_touching)
           VALUES ('droptest-control','tool','droptest-control','planted by brain-drop-test.sh','$c', true)"
done
echo "  planted $CONTROL_N distinct control commits"

echo
echo "=== 2c. the things that SHOULD be lost ==="
S -q -c "INSERT INTO brain.work_item (title,lane,state,canonical_task) VALUES ('live queue position that SHOULD be lost','store','inbox','infinity-os#0091')"
S -q -c "INSERT INTO brain.thread (work_item_id,from_agent,kind,text) SELECT id,'T2','note','history that SHOULD be lost' FROM brain.work_item LIMIT 1"
S -tAc "SELECT 'receipts='||(SELECT count(*) FROM brain.receipt)||' touches='||(SELECT count(*) FROM brain.touch)||' work_items='||(SELECT count(*) FROM brain.work_item)||' thread='||(SELECT count(*) FROM brain.thread)"

# Read the git_refs OUT before the drop, because after it there is nowhere to read them from.
# That asymmetry is the finding, not a flaw in the test: a receipt's git_ref is a pointer INTO
# git, and a pointer that only existed in the store dies with it. What survives is the commit.
#
# The control is excluded from the corpus list by its action, so the two verdicts below cannot
# borrow each other's greens: three commits this script chose BECAUSE they are on main would
# otherwise dilute the corpus with three guaranteed passes.
CORPUS_REFS=$(S -tAc "SELECT DISTINCT git_ref FROM (
                        SELECT git_ref FROM brain.receipt WHERE action <> 'droptest-control'
                        UNION ALL SELECT git_ref FROM brain.touch
                      ) r WHERE git_ref IS NOT NULL ORDER BY 1")
CORPUS_N=$(printf '%s\n' "$CORPUS_REFS" | grep -c . || true)
[ "$CORPUS_N" -gt 0 ] || die "the store held 0 git_refs. A corpus of nothing is not a pass."
printf '%s\n' "$CORPUS_REFS" > /tmp/brain-drop-test-corpus-refs.txt
echo "  carried $CORPUS_N distinct git_refs out of the store, to be scored after the drop"

echo
echo "=== 3. DROP THE STORE ==="
psu psql -U postgres -d postgres -c "DROP DATABASE \"$SCRATCH\"" >/dev/null
psu psql -U postgres -d postgres -tAc \
  "SELECT CASE WHEN count(*)=0 THEN 'scratch database is GONE' ELSE 'STILL THERE' END
     FROM pg_database WHERE datname='$SCRATCH'" </dev/null

echo
echo "=== 4a. the CONTROL recovers from git alone, with the store gone ==="
fail=0
for ref in $CONTROL; do
  if git -C "$CANON_REPO" cat-file -e "$ref^{commit}" 2>/dev/null; then
    subj=$(git -C "$CANON_REPO" log -1 --format=%s "$ref")
    files=$(git -C "$CANON_REPO" show --stat --format= "$ref" | tail -1 | tr -s ' ')
    printf '  RECOVERED %s  "%s" %s\n' "${ref:0:8}" "$subj" "$files"
  else
    printf '  LOST      %s\n' "${ref:0:8}"; fail=$((fail+1))
  fi
done
echo "  -> the recovery mechanism works. That is all this proves."

echo
echo "=== 4b. the CORPUS recovers from git alone, with the store gone ==="
# The store's own $CORPUS_N git_refs, scored by the SAME classifier the gate uses, reading the
# list carried out in 2b rather than a database that no longer exists. `resolves` is not
# `durable`: a commit reachable only from a scratch branch is one `git branch -D` from gone, so a
# two-state recovered/lost loop here would report it as recovered and be wrong in the direction
# that matters.
RECON="$(cd "$(dirname "$0")" && pwd)/brain-receipt-reconcile.py"
[ -x "$RECON" ] || die "$RECON is missing. The corpus half of this test cannot be skipped quietly."
set +e
python3 "$RECON" --repo "$CANON_REPO" --refs-from /tmp/brain-drop-test-corpus-refs.txt \
  | sed -n '/distinct git_refs/,$p' | sed 's/^/  /'
RECOVER_RC=${PIPESTATUS[0]}
set -e
echo "  brain-receipt-reconcile.py --refs-from exited $RECOVER_RC (0 all durable, 1 some LOST,"
echo "  2 some FRAGILE, 3 could not measure)"

# THE HARSHER READING, RUN EVERY TIME AND REPORTED EVERY TIME.
#
# A tombstone is a ref the operation has recorded as unrecoverable, with the loss it assessed,
# on a durable ref. Honouring it is what lets this test reach 0 while six pointers in the store
# still point at nothing. That is a defensible position and it is exactly the shape of thing
# that quietly becomes "we passed": so the run that does NOT honour tombstones is executed on
# every pass and its number is printed beside the other one. Nobody has to remember to ask.
set +e
python3 "$RECON" --repo "$CANON_REPO" --no-tombstones \
  --refs-from /tmp/brain-drop-test-corpus-refs.txt > /tmp/brain-drop-test-strict.txt 2>&1
# Read the CHILD's exit, not a pipeline's last stage. `foo | grep` reports grep's status, and
# that is how a failing child reads as a pass one line further down.
STRICT_RC=$?
set -e
STRICT_OUT="$(grep -E '^RECONCILE' /tmp/brain-drop-test-strict.txt || true)"
echo "  --no-tombstones (recorded-gone scored as LOST) exited $STRICT_RC:"
echo "    ${STRICT_OUT:-<no verdict line>}"

# The knowledge plane itself, counted rather than asserted.
#
# Decisions live at knowledge/<namespace>/decisions/, NOT at a top-level decisions/. Globbing
# 'decisions/*.md' returns 0 and reads as "no decisions survived", which is a confidently wrong
# zero from a test that looked in the wrong place. D00 cites these nodes as
# `decisions/session-transcript-posture.md`; the real path is
# knowledge/ai-architecture/decisions/session-transcript-posture.md.
K=$(git -C "$CANON_REPO" ls-files 'knowledge/*.md' | wc -l)
D=$(git -C "$CANON_REPO" ls-files 'knowledge/*/decisions/*.md' | wc -l)
SY=$(git -C "$CANON_REPO" ls-files '_system/*.md' | wc -l)
[ "$D" -gt 0 ] || die "found 0 decisions. Check the path before believing that number."
printf '  canon in git with the store gone: %s knowledge nodes, %s decisions, %s _system rules\n' "$K" "$D" "$SY"

echo
echo "=== 5. what the drop DID cost, which is the honest half ==="
echo "  live queue position: GONE (1 inbox work_item, unrecoverable from git)"
echo "  thread history:      GONE (1 thread event, unrecoverable from git)"
echo "  operator answers:    would be GONE. Nothing in git holds them."
echo "  -> this is why the backup job exists. Zero knowledge lost is not zero loss."

echo
echo "=== 6. the same corpus read from the LIVE store, for the attribution axis ==="
# Stage 4b already scored these shas with the store gone, which is the invariant's own phrasing.
# This adds the one thing that phrasing cannot reach: migration 19's `git_repo` column, which
# exists only in the store and says WHICH repository each ref was recorded from. attributed-here
# and unattributed are different findings and 4b, holding a bare list of shas, cannot tell them
# apart.
#
# The classification lives in ONE place (brain-receipt-reconcile.py) and is invoked from both
# stages rather than reimplemented in bash, so the two can never drift into disagreeing.
set +e
CORPUS_JSON="$(python3 "$RECON" --repo "$CANON_REPO" --json 2>/tmp/brain-drop-test-recon.err)"
CORPUS_RC=$?
set -e

if [ "$CORPUS_RC" -eq 3 ]; then
  # "I could not look" must never share an outcome with "I looked and it was fine". The reconciler
  # keeps exit 3 separate for exactly this, and honouring it here is what stops a store that is
  # down from reading as a corpus that is clean.
  echo "  COULD NOT MEASURE the corpus:"; sed 's/^/    /' /tmp/brain-drop-test-recon.err
  echo "DROP TEST FAILED: the mechanism passed but the corpus was never measured. That is not a"
  echo "pass -- it is the shape of green this test exists to stop."
  exit 1
fi

python3 - "$CORPUS_JSON" <<'PY'
import json, sys
r = json.loads(sys.argv[1])
st, at = r["distinct_by_state"], r["distinct_by_attribution"]
print(f"  {r['rows']} rows, {r['distinct_refs']} distinct git_refs in the LIVE store")
# TOMBSTONED is printed even when it is 0. Omitting it made the line say 20 of 26 and show
# three zeros, which reads as six refs the summary lost track of rather than six it named.
print(f"    DURABLE {st['DURABLE']}   FRAGILE {st['FRAGILE']}   "
      f"TOMBSTONED {st.get('TOMBSTONED', 0)}   LOST {st['LOST']}")
print(f"    attributed-here {at['attributed-here']}   attributed-elsewhere "
      f"{at['attributed-elsewhere']}   unattributed {at['unattributed']}")
lost = r["lost_by_attribution"]
if lost["attributed-here"]:
    print(f"    {len(lost['attributed-here'])} LOST ref(s) are attributed to THIS repository. The")
    print("    store says the brain held those commits and the brain does not.")
if lost["unattributed"]:
    print(f"    {len(lost['unattributed'])} LOST ref(s) predate migration 19 and cannot say which")
    print("    repository they came from. Unknown, which is not the same as fine.")
PY

echo
if [ "$RECOVER_RC" -eq 3 ]; then
  # Same rule as stage 6's exit 3, applied to the recovery half: could-not-look never shares an
  # outcome with looked-and-fine.
  echo "DROP TEST FAILED: the control recovered but the store's own $CORPUS_N git_refs were never"
  echo "scored (--refs-from exited 3). That is not a pass, it is an unmeasured corpus."
  exit 1
elif [ "$fail" -ne 0 ] || [ "$K" -eq 0 ] || [ "$SY" -eq 0 ]; then
  echo "DROP TEST FAILED: $fail of $CONTROL_N CONTROL commit(s) unrecoverable, or canon counted"
  echo "zero ($K knowledge, $SY _system). The recovery mechanism itself is broken, so nothing this"
  echo "run says about the corpus can be believed."
  exit 1
elif [ "$RECOVER_RC" -eq 0 ] && [ "$CORPUS_RC" -eq 0 ]; then
  echo "DROP TEST PASSED: the control recovers, AND every one of the $CORPUS_N git_refs the store"
  echo "itself held is accounted for with the store gone -- each is either an ancestor of a"
  echo "durable ref or recorded as unrecoverable on one. The counts are in stage 4b; read them"
  echo "rather than this line, because 'accounted for' is not 'recovered':"
  echo "    --no-tombstones exited $STRICT_RC. If that is 1, some of these refs point at nothing"
  echo "    and the pass above rests on the operation having written that down, not on the"
  echo "    commits existing. Both sentences are true and only one of them is comfortable."
else
  # The mechanism passed and the corpus did not, and this is the honest red the whole thing is
  # about. It does NOT become green by narrowing what counts: the way out is to make the corpus
  # true -- retire the receipts whose commits are gone, or land the branches that hold the fragile
  # ones -- not to stop asking.
  echo "DROP TEST FAILED: the control recovers, but the store's OWN receipts do not. Of the"
  echo "$CORPUS_N git_refs carried out of the store before the drop, not all survive it"
  echo "(--refs-from exited $RECOVER_RC, live-store reconcile exited $CORPUS_RC)."
  echo "Run these for the per-sha breakdown and the recoverable/gone split:"
  echo "    store/bin/brain-receipt-reconcile.py --repo $CANON_REPO"
  echo "    store/bin/brain-git-ref-census.py --repo $CANON_REPO"
  exit 1
fi
