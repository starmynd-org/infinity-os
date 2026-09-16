-- 0019: a git_ref names a commit, and a commit is only meaningful inside a repository.
--
-- LANDED 2026-08-17 (task 0256, attempt 1). This file was drafted on 2026-08-16 with a `.DRAFT`
-- suffix and deliberately NOT applied, because applying a column with no writer is schema drift
-- wearing the clothes of a fix, and the writer could not be touched that night: task 0242 (the
-- priority-0 D9 acceptance re-run) was mid-flight and exercises `lineage project`. 0242 finished,
-- so the two halves land together as the draft required:
--
--   this file                                          the columns
--   adapter/brain_adapter/store_join.py                 computes the identity from git
--   adapter/brain_adapter/store_projection.py           writes it on receipt and touch
--   store/bin/brain-receipt-reconcile.py                reads it and reports the attribution axis
--   store/bin/brain-drop-test.sh                        gates on the real corpus, not a fixture
--
-- The `.DRAFT` rename is the only edit to the SQL below; it applied clean on a scratch copy of the
-- live schema on 2026-08-16 and applied clean on live on 2026-08-17 with the same bytes.
--
-- ## The defect, measured on the live `brain` database at 2026-08-16T22:50Z
--
--   brain.receipt   26 rows, 26 non-null git_ref, 26 distinct
--   brain.touch     25 rows, git_ref NOT NULL since migration 5
--
--   store/bin/brain-receipt-reconcile.py -> 51 rows, 26 distinct git_refs
--       DURABLE   0   (ancestor of main or origin/main)
--       FRAGILE   6   (object exists, reachable only from scratch/t5-lineage-join,
--                      scratch/d2-adapter-receipt-proof, scratch/d9-acceptance, scratch/d9-rerun)
--       LOST     20   (no such object in the brain at all)
--
-- Four of the 20 LOST shas were still readable in throwaway fixture repositories when this was
-- written -- /tmp/0149-cli-zf9k78n8 held 23de1e01 ("cli probe") and /tmp/0149-proof-6a81fb59-ifs6ld7b
-- held 74e79385, b3fc01ad and b47d3b18 ("0149 proof fixture, case A/B/C"). That is the cause caught
-- in the act rather than inferred: those commits were never in the brain. `receipt book --repo X`
-- accepts any git repository, `lineage project --repo X <sha>` records the sha it finds there, and
-- neither `brain.receipt` nor `brain.touch` has a column naming X. So a sha from a temp directory
-- and a sha from canon are the same row shape, and the store cannot tell you which it is holding.
--
-- ## Why this is not the same case as `produced_by`, which is deliberately allowed to dangle
--
-- The precedent in this repo (D-CROSSTALK, "Reason 3: an FK inverts the invariant") is that
-- lineage reconciliation is "a reported metric, never a constraint. Dangling ids are visible and
-- counted, not rejected." That is right for `produced_by`, because it points at a MUTABLE brain
-- node that a curator may legitimately retire, and a receipt must stay readable afterwards.
--
-- `git_ref` is the opposite case. It points at an IMMUTABLE commit that is supposed to BE the
-- durable evidence -- "git holds what is true, the store holds what is happening; losing the store
-- must cost zero knowledge." A dangling `produced_by` degrades attribution. A dangling `git_ref`
-- means the knowledge the receipt claims to point at does not exist anywhere. So this migration
-- follows the precedent on the column (record it, make it countable) and the landing task decides
-- separately whether the WRITER refuses -- which is the operator question 0256 raised.
--
-- ## Why nullable, and why nothing is backfilled
--
-- The 26 existing rows were booked before any repo was recorded, so their repository is genuinely
-- unknown. NULL says exactly that. Backfilling them with 'the brain' would be inventing a fact --
-- and the reconciliation above proves it would be a FALSE one for at least four of them, which are
-- demonstrably from /tmp. This repo's own rule from the 2026-07-16 drift repair applies: never
-- backfill a provenance field to make a report look clean.
--
-- No row is deleted here either. The 20 LOST rows are evidence of the defect and the corpus the
-- reconciler measures against; destroying them would erase the only record that this happened.

BEGIN;

-- Renumber guard, migration 5's pattern. `brain.schema_migration` version numbers are NOT file
-- numbers here -- version 11 is '0009_null_branch_act_scan', 16 is '0010_queue_item_lineage_coherent',
-- 18 is '0011_null_branch_residue_closure' -- because several lanes applied migrations in parallel
-- on 2026-08-16. Read the ledger, never `ls migrations/`.
DO $$
DECLARE taken text;
BEGIN
  SELECT name INTO taken FROM brain.schema_migration WHERE version = 19;
  IF taken IS NOT NULL AND taken <> '0019_git_ref_repo_identity' THEN
    RAISE EXCEPTION 'version 19 is already taken by %. Another lane landed a migration while this '
                    'file was being written. Renumber from brain.schema_migration, not from ls.',
                    taken;
  END IF;
END $$;

-- The repository a `git_ref` belongs to.
--
-- Identity is the REPOSITORY'S ROOT COMMIT, not its remote URL and not its path on disk. A remote
-- URL is absent for a local-only repo and changes when an org is renamed (this operation renamed
-- one this month). A filesystem path is meaningless the moment the repo moves, and every one of
-- the 20 LOST shas came from a path that no longer exists. A root commit is immutable, survives
-- rename, move and re-clone, and is derivable with `git rev-list --max-parents=0 HEAD | tail -1`
-- from any working copy -- so two lanes computing it independently get the same string by
-- construction rather than by spelling a name the same way.
--
-- `git_repo_ref` carries the human-legible name beside it (a remote URL, or the path as given)
-- because a bare root-commit sha is unreadable in an incident, and NULL when there is nothing
-- honest to put there. It is a label, never the key.
ALTER TABLE brain.receipt ADD COLUMN IF NOT EXISTS git_repo     text;
ALTER TABLE brain.receipt ADD COLUMN IF NOT EXISTS git_repo_ref text;
ALTER TABLE brain.touch   ADD COLUMN IF NOT EXISTS git_repo     text;
ALTER TABLE brain.touch   ADD COLUMN IF NOT EXISTS git_repo_ref text;

COMMENT ON COLUMN brain.receipt.git_repo IS
  'The root-commit sha of the repository git_ref lives in. NULL means the row predates repo '
  'identity and its repository is genuinely unknown -- it is NOT a claim that the row is the '
  'brain''s. Measured 2026-08-16: 20 of 26 git_refs resolved in no repository on this machine and '
  'four of those were still readable in /tmp fixture repos, so NULL here really does mean unknown.';

COMMENT ON COLUMN brain.receipt.git_repo_ref IS
  'A human-legible name for the same repository (remote URL, or the path as given). A label for '
  'incident reading. git_repo is the key; this may be stale or NULL and nothing joins on it.';

COMMENT ON COLUMN brain.touch.git_repo IS
  'The root-commit sha of the repository git_ref lives in. See brain.receipt.git_repo.';

COMMENT ON COLUMN brain.touch.git_repo_ref IS
  'A human-legible name for the same repository. See brain.receipt.git_repo_ref.';

-- Reconciliation as a reported metric, per the precedent above: visible and counted, not rejected.
-- Deliberately NOT a constraint. The check that scores DURABLE/FRAGILE/LOST needs a git repository
-- and Postgres has none, so the classification lives in store/bin/brain-receipt-reconcile.py and
-- this view is only the store's half: which rows can even be attributed to a repository.
CREATE OR REPLACE VIEW brain.git_ref_attribution AS
  SELECT 'receipt' AS source, id, git_ref, git_repo, git_repo_ref FROM brain.receipt
   WHERE git_ref IS NOT NULL
  UNION ALL
  SELECT 'touch'   AS source, id, git_ref, git_repo, git_repo_ref FROM brain.touch;

COMMENT ON VIEW brain.git_ref_attribution IS
  'Every git_ref in the store beside the repository it was recorded from. A NULL git_repo is an '
  'unattributable pointer: nothing can say where that commit was supposed to be, so nothing can '
  'say whether losing the store would cost knowledge. Count it, do not reject it. The '
  'resolvability half needs git and lives in store/bin/brain-receipt-reconcile.py.';

GRANT SELECT ON brain.git_ref_attribution TO brain_runtime;

INSERT INTO brain.schema_migration (version, name) VALUES (19, '0019_git_ref_repo_identity')
  ON CONFLICT (version) DO NOTHING;

COMMIT;
