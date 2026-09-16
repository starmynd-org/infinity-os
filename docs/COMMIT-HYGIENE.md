# Commit hygiene in a shared worktree

Eight terminals edit `infinity-os` at once. There is one working tree and one
index between all of them. Everything below follows from that.

## The rule

1. **Commit only paths your own task produced.** This is the rule. The others are how you
   keep it.
2. **Name every path on the command line.** Never `git add -A`, never `git add .`, never
   `git add -u`, never `git commit -a`, never `git add <directory>/`. A directory is not a
   path you produced; it is a path other lanes also write into.
3. **Enumerating the whole tree by pathspec is still `git add -A`.** Building a path list
   from `git status` and feeding it to `--pathspec-from-file` names every path and obeys
   rule 2 while breaking rule 1. Rule 1 is the one that matters.
4. **Check `git status --porcelain` before you stage and after you commit.** Every line
   that is not yours must be unchanged between the two reads. Save both; paste them in your
   report.
5. **Never `checkout`, `stash`, `clean`, or `reset --hard` a path you did not create.**
   Those are the only four verbs in git that destroy work, and in this tree the work they
   destroy is usually somebody else's. `git stash` is not a safe parking spot: it takes the
   whole tree.
6. **Never rewrite a commit that is on `origin`.** No `rebase`, no `commit --amend`, no
   `filter-branch`, no force push. Other clones exist.

## What to do instead of the thing you were about to do

| You want to | Do this instead |
|---|---|
| Discard a change you did not make | Nothing. `swarm note` it and move on. |
| Commit "everything so it is backed up" | Commit your paths. Post the rest as a task with `--parent`. |
| Un-stage what somebody else staged | `git restore --staged <your paths only>`, or leave it and name your paths at commit time. `git reset` with no pathspec clears the whole index and is somebody else's decision. |
| Revert a bad commit that is pushed | `swarm ask` the operator. Recovery is their call, not yours. |
| Get a clean tree for a test | You will not get one. A running fleet never has a clean tree. Test against your paths, not against `git status`. |

If you believe your task genuinely requires one of the forbidden verbs, that is a
`swarm ask` with a `--default`, not an improvisation.

## Why this file exists

On 2026-08-18 at 12:01:13Z, commit `45f1a3f` ("0108: the runtime the VPS has to clone")
landed 301 files and 51,433 insertions and was pushed to `origin/master`. 78 of those paths
were the live, mid-task work of 15 other tasks across all 8 running agents, committed under
a message about a VPS clone, so `git log -- <path>` now gives the wrong reason for every one
of them.

Nothing was lost, and the lane that made it named all 301 paths explicitly and proved the
staged set equal to its intended list — it broke rule 1, not rule 2, which is why this file
leads with rule 1. What it did cost is real: `HEAD` on the shared remote still carries a
version of `engine/swarm_engine/transitions.py` that its own author identified as defective
five minutes after the push, because the fix arrived after the sweep and has nowhere to
land that is not another sweep.

Full measurement: `outputs/2026-08-18-commit-hygiene/CONTAMINATION.md`.
