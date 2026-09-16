#!/usr/bin/env bash
# Every suite the INTAKE DOOR lane owns, in one command, against a scratch database.
#
# WHY A RUNNER AND NOT "just run pytest". Task 0354's finding, one lane over: `engine/tests/`
# held 30 test files and its runner dispatched 25, and the banner it printed for that was
# `ALL SUITES GREEN`. A suite nobody invokes is a rule nobody enforces. `pytest <dir>` collects
# by convention, so this file's job is smaller than the engine's: it fixes the DATABASE, prints
# which one, and refuses the live store before pytest ever imports anything.
#
# THE DATABASE IS NEVER `brain`. There is no verb that deletes an objective, so a run against the
# live store leaves rows on the operator's board that only he can clear. `conftest.py` refuses it
# independently: belt and braces, because a runner is a convenience and the refusal has to
# survive somebody running one file by hand.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LANE="$(dirname "$HERE")"
ROOT="$(dirname "$LANE")"

# --list: WHAT THIS RUNNER DISPATCHES, WITHOUT TOUCHING A DATABASE.
#
# This runner had no `--list` at all: the flag fell through to pytest, which answered exit 4, so the
# registration guard could not see this directory and an unregistered suite here was invisible BY
# CONSTRUCTION. Terminal 26's sealed run found the gap; Terminal 04 measured `check-at-head` at 3 of
# 4 on the merged tree for this one cause. It is the same shape as a check that scans one directory
# only: the shape where nothing announces what is missing.
#
# IT EXITS BEFORE THE DATABASE BLOCK ON PURPOSE. A lister that first picks a database, exports it
# and prints a banner is a lister you cannot run in a guard that has no store, which is most of
# them. Nothing above this line reads or writes one.
#
# BASENAMES, ONE PER LINE, NOTHING ELSE ON STDOUT. This printed FULL PATHS until R-INTAKE-LIST-01,
# and that would have failed the gate it was written to satisfy. The guard reads each line with
# `re.match(r"\s*(test[-_][A-Za-z0-9_.-]+\.(?:py|sh))\b", ln)`, which is ANCHORED, and compares what
# it finds against `p.name` from a directory scan. A path does not match, so it is kept WHOLE by the
# normaliser's `elif ln.strip()` arm and reaches the comparison as a name no directory holds:
#
#   FAIL  intake-service/tests: every one of N test files is dispatched
#         on disk and NOT dispatched: [...basenames...]
#         dispatched and NOT on disk: [...absolute paths...]
#
# N IS DELIBERATELY NOT A NUMBER HERE. It was 6 when this was written against the R line's copy of
# this directory, which held three test files: three basenames on disk plus three paths dispatched
# is a union of six. This branch adds `test_capture_gate.py`, so on THIS tree the same failure
# prints 4 and a union of eight. A comment keyed to a raw count is broken by construction -- the
# same lesson the ratchets learned -- and I only found this one because I diffed the two copies
# before the merge, not because anything failed.
#
# A DOUBLE-DIRECTION MISMATCH, not an empty list. This comment claimed the guard would report
# "0 comparisons made. A verdict over an empty set is not a pass." IT DOES NOT, because L01's
# normaliser KEEPS an unmatched line rather than dropping it -- which is the exact behaviour T04
# preferred it for, working as documented. Terminal 25 built the variant instead of accepting the
# reasoning and found the description wrong (CAP14-B REV-027); the conclusion was right the whole
# time and only the mechanism was invented. I had gone from "a path does not match the regex"
# straight to "the list is empty" without asking what happens to a line that does not match.
#
# The correction matters for the next reader, not for this file's behaviour: somebody debugging a
# path-printing runner would search for "0 comparisons made", never find it, and be staring at a
# message naming both directions instead.
#
# I did not know the format mattered at all when I wrote the path version; I found THAT by reading
# the consumer before porting this arm onto the R line, where the same mistake would have shipped as
# the fix for the very gate it broke. This copy is deliberately kept in step with the line's, so the
# merge of this branch cannot reintroduce what the line already corrected.
#
# THE LIST IS READ FROM DISK, not from an array, because this runner dispatches by convention:
# `pytest "$HERE"` collects `test_*.py`, so the honest list is the same glob rather than a second
# declaration that can drift from what actually runs.
if [ "${1:-}" = "--list" ]; then
  for f in "$HERE"/test_*.py; do
    [ -e "$f" ] || continue
    printf '%s\n' "$(basename "$f")"
  done
  exit 0
fi

# THIS LANE'S OWN DATABASE, never the shared `brain_scratch`: every other suite in the estate
# defaults to that one and `scratch-db.sh create` opens with a DROP.
DB="${ENGINE_SCRATCH_DB:-brain_s1_door}"
if [ "$DB" = "brain" ]; then
  echo "run-all: refusing to run against the live store 'brain'." >&2
  exit 1
fi
# AND THE SHARED SCRATCH STORE, WHICH THE COMMENT ABOVE PROMISED AND THE CODE DID NOT DELIVER.
#
# The default is this lane's own name, so an unset variable was always safe. But `ENGINE_SCRATCH_DB`
# is exported by other tooling, and an inherited `brain_scratch` would have been accepted here
# without a word -- into the store every other lane's suite reads, which is exactly what Terminal 26
# is chasing across the R suites. Found by auditing this runner rather than by anything failing.
#
# A DEFAULT THAT IS SAFE IS NOT A GUARD. The guard is what happens when someone else chose.
if [ "$DB" = "brain_scratch" ]; then
  echo "run-all: refusing the shared 'brain_scratch': every lane's suite reads it, so this run" >&2
  echo "         would leave its rows in other lanes' evidence. Name a private database, or" >&2
  echo "         unset ENGINE_SCRATCH_DB to use this lane's own ${DB:+default} brain_s1_door." >&2
  exit 1
fi
export ENGINE_SCRATCH_DB="$DB"
export BRAIN_PG_DB="$DB"

echo "intake-service suite: database $DB, repo $ROOT"
# NEVER PIPED THROUGH tail OR head: the exit code would become the pipe's, and a red suite would
# report green. Long output goes to a file and the file is read.
python3 -m pytest "$HERE" "$@"
rc=$?
echo "intake-service suite: pytest exit $rc (database $DB)"
exit $rc
