# adapter/ — the brain adapter (lane D2)

Entity resolution, brief rendering, receipt booking. This is the lane that makes the
runtime *the brain with hands* rather than a task tracker with a wiki next to it: if
`produced_by` resolves to a real brain entity and a receipt books back with lineage
intact, the attribution property exists. Nothing else in the program produces it.

The brain read is `your-brain` (override with `$BRAIN_ROOT`). This
lane reads canon freely and writes to git only through one function.

## Three verbs, not three functions

```
bin/brain-adapter entity resolve <ref> [--reverse] [--audit]
bin/brain-adapter receipt book    --department ... --date ... --slug ... --moment ...
bin/brain-adapter touch add       --subject-type ... --subject-id ... --touch ...
bin/brain-adapter brief render    --work-item <json> [--budget N]
bin/brain-adapter lineage project <sha> [--repo ...]      # git -> store. NOT a fourth verb
```

`lineage project` is a fourth SUBCOMMAND and still not a fourth verb: the three above write
git, and this one writes nothing. It reads a commit git already holds and calls the store
transition of the same name, which is the loader migration 4's projection always implied. It is
here because that transition had no surface at all — not this CLI, not the console, not MCP —
so the hop from a booked receipt into the store could only be taken by importing
`brain_adapter.store_projection` in a Python REPL, which is what D9 had to do to finish its
acceptance run (task 0141, out of 0118). It is the one subcommand that needs Postgres, its
import is inside the function so the others still do not, and `exit 6` means the store did not
answer — distinct from `exit 5`, which means the commit was read and refused.

`receipt book` and `touch add` both call `promotion.promote()`. That is the only
`git commit` in `adapter/**` — one transition, one implementation, one audit point, per
the narrow waist in `D00-shared-context.md`. The MCP server's `book_receipt` and
`resolve_entity` tools are thin wrappers over these, never a parallel path.

Exit codes are the contract, because a caller must tell the outcomes apart without
parsing prose:

| Code | Meaning |
|---|---|
| 0 | resolved / booked |
| 2 | usage error |
| 3 | unresolved entity |
| 4 | ambiguous entity (several nodes matched; the caller disambiguates) |
| 5 | promotion refused (stderr names the refusal code) |

## Where a receipt is allowed to land (task 0349)

`receipt book` cannot return success onto a ref nobody keeps. Before 2026-08-17 it could, and
did: 0 of the 26 `git_ref`s in the live store were ancestors of a durable ref, because
`promote()` refused `main` (no agent self-approves canon) and nothing else was durable, so every
receipt was booked onto a scratch branch, a disposable worktree or a `mkdtemp` directory.

| Where you book | What happens |
|---|---|
| `main`, `master`, `trunk` | `PROTECTED_BRANCH`, unchanged. A human lands canon. |
| a scratch branch, a detached HEAD, anything undeclared | `NOT_DURABLE`, refused **before** anything is staged: no commit, no file left behind |
| `receipts/durable` (the declared ledger) | booked, and the result carries `durability_tier` and `durable_ref` |

`policy/durable-refs.json` at the repository root is the one declaration of which refs are
durable, keyed by root commit, read as data by three tools that keep their own classifiers: this
adapter, `store/bin/brain-receipt-reconcile.py` and `store/bin/brain-git-ref-census.py`. There is
no flag that adds a durable ref at the moment somebody wants a green, and a policy naming a
protected branch as bookable is itself refused (`POLICY_REFUSED`).

`lineage project` carries the same gate as `REF_NOT_DURABLE`, because the store row is what
asserts the commit exists and nine of the 26 bad rows were committed by a fixture builder that
never called `promote()` at all.

Tiers, and the report always says which: `mirrored` (ancestor of a remote-tracking ref, survives
losing this machine), `canon` (ancestor of `main`), `ledger` (ancestor of a declared ref here:
survives branch cleanup, a deleted worktree, `/tmp` and gc, and lives in one working copy until a
human merges it).

## What an unresolvable entity produces

Never silent, never fabricated:

```json
{"produced_by": null, "produced_by_ref": "<the raw string as given>",
 "resolution_status": "unresolved", "resolution_candidates": []}
```

`produced_by` is a real NULL and the raw reference is preserved beside it, so the failure
is visible in the row rather than only in a log. `ambiguous` is a distinct status on
purpose: it means the brain holds several nodes by that name, which is a fixable input
error, and collapsing it into `unresolved` would hide that. `promote()` refuses
`LINEAGE_INCOHERENT` if any caller writes `produced_by = NULL` while claiming
`resolution_status = 'resolved'`.

`receipt book` refuses an unresolvable producer outright. `--allow-unresolved` books it
with `produced_by: null` and the raw reference rendered visibly in the receipt body and in
the commit message, never as a plausible-looking id.

## Layout

| Path | Holds |
|---|---|
| `brain_adapter/index.py` | the entity index; `resolve()` and `reverse()` |
| `brain_adapter/frontmatter.py` | the small frontmatter reader (~11.5k files, so not PyYAML per file) |
| `brain_adapter/brief.py` | LOAD-2 brief rendering, budgeted by demotion |
| `brain_adapter/promotion.py` | **the single git writer**, and every refusal |
| `brain_adapter/durability.py` | what makes a ref durable; the gate `promote()` runs twice |
| `brain_adapter/receipt.py` | receipt and touch-edge documents |
| `brain_adapter/tokens.py` | token accounting, exact when a key exists, labelled estimate otherwise |
| `brain_adapter/cli.py` | the verbs |
| `tools/` | the measurement scripts behind every number below |
| `reports/` | their output |
| `tests/` | 39 tests; the refusal tests are the ones that matter |

## Measured, at brain HEAD `c7919b93`

11,537 tracked markdown files, 6,086 carrying an `id:`, 76 namespaces under `knowledge/`.

**Resolution**, three populations because a blended number hides which one `produced_by`
depends on:

| Population | Denominator | Resolved | Rate |
|---|---|---|---|
| A. id round trip (`resolve(id)` and `reverse(id)` agree) | 6,086 id-bearing nodes, 100 top-level namespaces | 6,086 | **100.00%** |
| B. real `[[wikilinks]]` as the corpus writes them | 2,673 distinct targets, 98 namespaces, no sampling | 2,444 | **91.43%** |
| C. bare filename lookups | 6,086 | 4,766 | 78.31% |

Population B is the honest headline. Of its 229 non-hits: 58 ambiguous, 31 are real files
that carry no `id:` at all (`_system/` rule files and `INDEX.md` are validator-exempt by
design), and 140 are genuinely dangling links in the corpus. Excluding non-id-bearing
targets the rate is 92.51%. References arrive as `id` 1,267 / `alias` 708 / `filename`
451 / `path` 18 — 47% are not ids, which is why the alias pass is load-bearing.

Population C is the warning: 165 filename lookups resolve to a **different** node and
1,155 are ambiguous. Do not store filenames.

**Rename stability** (`tools/rename_experiment.py`, in a scratch worktree, restored
clean): 40 nodes from 40 namespaces moved to a new directory and a new filename.
`resolve(id)` survived 40/40, `reverse(id)` tracked the move 40/40, and
`resolve(old-filename)` landed correctly 18/40 — exactly the 18 carrying the old name in
`aliases`, no exceptions. Of the other 22, five resolved to a *different* node.

**Brief rendering**: the representative brief for work item 0093 routes to
`ai-architecture` and renders at **24,929 estimated tokens** (upper bound 28,045; method
`chars/3.6`, stated as an estimate because this host has no Claude tokenizer and no API
key). The whole `ai-architecture` namespace is 133 files and ~264,891 estimated tokens, so
the brief is **9.4% of the namespace, a 10.6x reduction**. At a 12,000-token budget the
same brief renders at 11,210 with 3 nodes demoted to summary and 2 omitted, every one of
them named in the brief's own "Not loaded" section.

## The rules this implements rather than decides

- **No agent self-approves canon.** `promote()` refuses `main`, `master` and `trunk`
  unconditionally, and refuses any path under `*/canon/*` that carries no `approval_ref`.
  A receipt records who decided; it never decides.
- **An invisible substrate write that becomes authoritative meaning is never a
  promotion.** `promotion.PROMOTION_KINDS` is the taxonomy from
  `knowledge/ai-architecture/concepts/surface-boundary.md`; `read-only` and
  `runtime-state-write` are refused as `NOT_A_PROMOTION` because they do not cross the
  plane boundary.
- **The runtime never owns knowledge.** Canon is read freely; git is written only here.
- **Receipts are append-only** (`WAGER-13a`). Booking over an existing receipt is refused;
  `touch add` books a follow-on receipt rather than editing a committed one.
- **A receipt carries `caused_by_event_id` and `approval_ref` even when both are null.**
  Null is a value; a missing key would make "flagged event with no approval" undetectable
  by join, which is the breach the fields exist to expose.
- **Generated files are raised, not patched.** `GENERATED_PREFIXES` refuses them, because
  a generator's `--check` destroys hand edits on the next run.
- **The commit never sweeps a file the promotion did not write.** `promote()` reads back
  `git show --name-only` and refuses `COMMIT_SWEPT_EXTRA_FILES` on any extra path. The
  brain working tree carries other sessions' in-flight work; `git add -A` is never used.

## Reproducing the numbers

```bash
export BRAIN_ROOT=/path/to/a/scratch/worktree     # ext4, not /mnt/c: ~40x faster
python3 tools/resolution_hit_rate.py --json reports/resolution-hit-rate.json
python3 tools/rename_stability.py   --json reports/rename-stability.json
python3 tools/rename_experiment.py  --worktree "$BRAIN_ROOT" --n 40
python3 -m unittest discover -s tests -v
```

`tools/rename_experiment.py` mutates files and restores them; it refuses to run on
`main`/`master` or on a dirty tree, and verifies the restore with `git status`.
