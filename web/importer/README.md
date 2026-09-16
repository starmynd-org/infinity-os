# Agent estate importer

`web/importer` is a dependency-free, refusal-first parser for frozen filesystem exports.
Read `IMPORT-CONTRACT.md` before changing it.

It can validate a manifest-backed `ESTATE/1.0` directory, parse a frozen brain commit,
measure the frozen architecture catalog, map retained outcomes to decidable `ITEM/1.0`
proposals, and produce a disabled swarm plan. It cannot authenticate, activate, install,
execute, deploy, or write a brain.

## Reproduce Andrew's measurement

From Git Bash at the repository root:

```text
python web/importer/measure_andrew_estate.py --catalog-repo C:/Users/you/repos/_scratch-infinity/worktrees/R-c1 --catalog-revision eb2974138b0a77873df7bc9fd0340ad239be27df --brain-repo C:/Users/you/repos/your-brain --brain-revision 9e56eda660c73fd673fb9a2ba2640f5a38831954 --output web/importer/evidence/ANDREW-ESTATE-COVERAGE.json
```

The reader uses committed git blobs, not either working tree. The report prints each
denominator before outcome counts; the catalog and direct brain views overlap and are not
summed into a false unique total.

## Verify

```text
python -m unittest discover -s web/importer/tests -v
```

The refusal plants exercise every stable refusal code in `ESTATE/1.0`. A non-author must
rerun them before clearance.

`2026-09-09-IOS-term-9`
