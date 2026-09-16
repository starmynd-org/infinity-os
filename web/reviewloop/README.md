# Review Loop

This package prepares proposed changes to authored behaviour as `ITEM/1.0` Attention rows. It is a
local-only Git seam. It does not write Postgres, merge, push, publish, deploy, or call a remote.

The contract is `PROPOSAL-CONTRACT.md`. The core API is in `core.py`.

Run the hermetic suite from Git Bash:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=C:/Users/you/repos/_scratch-infinity/worktrees/R-ios10 python -m unittest discover -s C:/Users/you/repos/_scratch-infinity/worktrees/R-ios10/web/reviewloop/tests -t C:/Users/you/repos/_scratch-infinity/worktrees/R-ios10 -v
```

Run the smallest real repository pass:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=C:/Users/you/repos/_scratch-infinity/worktrees/R-ios10 python C:/Users/you/repos/_scratch-infinity/worktrees/R-ios10/web/reviewloop/run_smallest_real_target.py
```

That pass checks the already documented `web/README.md` room-table defect. The novelty sweep is
expected to find the existing answer in `docs/KNOWN-GAPS.md`, suppress the duplicate proposal, and
stop. Evidence is written to `web/reviewloop/evidence/SMALLEST-REAL-PASS.json`.

After committing, verify the exact artifact from a Git archive:

```bash
PYTHONDONTWRITEBYTECODE=1 python C:/Users/you/repos/_scratch-infinity/worktrees/R-ios10/web/reviewloop/verify_committed.py --sha <commit> --output C:/Users/you/repos/_scratch-infinity/worktrees/R-ios10/web/reviewloop/evidence/VERIFY-COMMITTED.json
```

Those are this seat's literal paths. From a merged checkout, replace them with that checkout's
literal absolute path; do not rely on `cd`, shell variables, or command substitution.

The package can prepare a local proposal branch only after an `AcceptedDecision` attributed to a
human. The returned result always states that push and pull request publication are missing and
belong to a human.
