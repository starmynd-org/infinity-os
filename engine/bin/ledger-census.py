#!/usr/bin/env python3
"""What this lane's code names that a store behind the tree might not have.

WHAT THIS ENUMERATES, AND IT IS LESS THAN ITS NAME SUGGESTS: relations, and `ALTER TABLE ... ADD
COLUMN`. Nothing else. Read the next three paragraphs before citing any number it prints.

CAP14 REV-076 measured every DDL statement across migrations 65 to 67:

    16  ALTER TABLE ... ADD CONSTRAINT
    12  ALTER TABLE ... DROP CONSTRAINT
     9  ALTER TABLE ... ADD COLUMN          <- this tool's ENTIRE column coverage
     6  CREATE OR REPLACE FUNCTION
     4  CREATE VIEW / CREATE OR REPLACE VIEW      3 DROP VIEW
     1  CREATE TRIGGER    1 DROP TRIGGER    1 CREATE INDEX
     1  ALTER TABLE ... RENAME COLUMN       <- the one that bit, and a rename is not an add
    54 statements. This tool sees 9.

SORT THEM BY WHAT THEY DO TO A STORE THAT IS BEHIND AND THE REAL GAP APPEARS. Twenty are CRASH
class -- the code raises, which is loud and gets found. Eighteen are ENFORCEMENT class: nothing
raises, and the older store simply PERMITS WHAT THE NEWER FORBIDS. This tool covers ZERO of those.

AND THE INSTANCE THAT SETTLES IT. Migration 66 -- the migration whose absence created the
single-use hole this lane spent a night on -- enforces single use with five ADD CONSTRAINT, a
partial UNIQUE INDEX and a trigger. NOT ONE IS AN ADD COLUMN. This tool sees only the seven columns
those constraints operate on, so a store with the columns and without the constraints, which
`ADD COLUMN IF NOT EXISTS` makes an entirely reachable partial state, REPORTS AS COVERED WHILE
SINGLE USE GOES UNENFORCED. `has_column` cannot close that: there is no `has_column` question whose
answer is "is this constraint enforced".

SO THIS IS A WHERE-TO-LOOK INSTRUMENT AND MUST NEVER BE CITED AS COVERAGE. It was written as the
corrective for point-fixing and it is not one: an enumeration is another hand-written list and
inherits the same failure one remove out, which is exactly what happened -- the commit that
introduced it contained two defects it could not see. The corrective is DERIVATION, and the two
scenes in `engine/execution/test_execution.py` that read the ledger out of the migration files
rather than keeping a second copy are what that looks like when it is available.

Its counts also RISE after a fix, because it counts REFERENCES and a guard mentions the column it
guards. A rising number here is not a regression and a falling one is not progress.

    ./engine/bin/ledger-census.py [floor]        default floor 64
"""
from __future__ import annotations

import collections
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
CODE = ["store/authority.py", "engine/execution/coordinator.py"]
MIG_DIRS = ["migrations", "queue/schema", "budget/schema"]
# Transaction-local settings, not relations. Separated because they would otherwise read as two
# objects no migration creates and send a reader hunting for nothing -- which is exactly what
# happened to Terminal 03's independent enumeration before it encoded the distinction.
GUCS = {"lease_secret", "settle_secret"}


def migration_files():
    out = []
    for d in MIG_DIRS:
        p = REPO / d
        if p.is_dir():
            out += sorted(p.glob("*.sql"))
    return out


def version_of(path) -> int:
    m = re.match(r"(\d+)", path.name)
    return int(m.group(1)) if m else -1


def relations(floor: int) -> int:
    refs = collections.defaultdict(list)
    for rel in CODE:
        for n, line in enumerate((REPO / rel).read_text(encoding="utf-8").splitlines(), 1):
            for m in re.finditer(r"brain\.([a-z_]+)", line):
                refs[m.group(1)].append((rel, n))

    creators = {}
    for f in migration_files():
        ver = version_of(f)
        txt = f.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"CREATE\s+(?:OR\s+REPLACE\s+)?(TABLE|VIEW|MATERIALIZED VIEW)\s+"
                             r"(?:IF\s+NOT\s+EXISTS\s+)?brain\.([a-z_]+)", txt, re.I):
            obj = m.group(2)
            if obj not in creators or ver < creators[obj][0]:
                creators[obj] = (ver, f.name, m.group(1).upper())

    print(f"{'object':32} {'kind':6} {'created by':10} {'sites':5}  verdict")
    print("-" * 96)
    above, unknown, sites = [], [], 0
    for obj in sorted(refs):
        sites += len(refs[obj])
        if obj in GUCS:
            print(f"{obj:32} {'GUC':6} {'-':10} {len(refs[obj]):5}  "
                  f"transaction-local setting, not a relation")
            continue
        if obj in creators:
            ver, _, kind = creators[obj]
            note = f"ABOVE {floor} -- needs tolerance" if ver > floor else f"at or below {floor}"
            if ver > floor:
                above.append(obj)
            print(f"{obj:32} {kind[:6]:6} {ver:04d}       {len(refs[obj]):5}  {note}")
        else:
            unknown.append(obj)
            print(f"{obj:32} {'?':6} {'?':10} {len(refs[obj]):5}  "
                  f"NOT CREATED BY ANY MIGRATION IN THE TREE")

    print()
    print(f"RELATIONS: {len(refs)} distinct objects across {len(CODE)} files, {sites} sites "
          f"(prose included -- this scans every line, comments too).")
    print(f"  introduced above ledger {floor}: {len(above)}"
          + (" -> " + ", ".join(above) if above else ""))
    print(f"  created by no migration:      {len(unknown)}"
          + (" -> " + ", ".join(unknown) if unknown else ""))
    return len(refs)


def columns(floor: int) -> int:
    added = {}
    for f in migration_files():
        ver = version_of(f)
        if ver <= floor:
            continue
        txt = f.read_text(encoding="utf-8", errors="replace")
        for a in re.finditer(r"ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?brain\.([a-z_]+)\s+"
                             r"ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?([a-z_]+)", txt, re.I):
            table, col = a.group(1), a.group(2)
            if (col, table) not in added or ver < added[(col, table)][0]:
                added[(col, table)] = (ver, f.name)

    # BOUND TO THE TABLE THE MIGRATION ALTERED. A bare name match cannot tell `expires_at` on
    # brain.approval (66) from `expires_at` on brain.execution_lease (58), and the first version of
    # this reported nine spurious pairs that way. An over-reporting census is not the safe error:
    # it is the one that gets three real findings ignored alongside six false ones.
    real, rejected = [], []
    for rel in CODE:
        lines = (REPO / rel).read_text(encoding="utf-8").splitlines()
        near = None
        for n, line in enumerate(lines, 1):
            t = re.findall(r"brain\.([a-z_]+)", line)
            if t:
                near = t[-1]
            if re.match(r"^\s*#", line):
                continue
            for (col, table), (ver, _) in added.items():
                if re.search(rf"\b{col}\b", line):
                    (real if near == table else rejected).append((rel, n, col, table, ver, near))

    print()
    print(f"COLUMNS added above ledger {floor} -- AND ONLY `ALTER TABLE ... ADD COLUMN`, see the "
          f"module docstring")
    print("-" * 96)
    for rel, n, col, table, ver, _ in real:
        print(f"  {rel}:{n}  {col} on brain.{table} (from {ver:04d})")
    print()
    print(f"  (column, table) pairs added above {floor}: {len(added)}")
    print(f"  written by this lane: {len(real)} site(s), "
          f"{len(set((r[2], r[3]) for r in real))} distinct pair(s)")
    print(f"  name collisions rejected by the table binding: {len(rejected)} site(s)")
    print()
    print("  A SITE HERE IS A REFERENCE, NOT AN UNGUARDED ONE. Whether each is guarded is a "
          "question for a run:")
    print("  engine/bin/prove-tolerance.sh drives the spine at every ledger in the tree.")
    return len(added)


def main() -> int:
    floor = int(sys.argv[1]) if len(sys.argv) > 1 else 64
    n_rel = relations(floor)
    n_col = columns(floor)
    print()
    if n_rel + n_col == 0:                                                   # DENOMINATOR
        print("0 objects and 0 columns compared. A verdict over an empty set is not a pass.")
        return 2
    print(f"Scanned {len(migration_files())} schema file(s) and {len(CODE)} source file(s).")
    print("THIS IS A WHERE-TO-LOOK RESULT. It sees relations and ADD COLUMN, which is 9 of the 54 "
          "DDL statements\nacross 65 to 67, and NONE of the 18 that enforce rather than raise. "
          "Do not cite it as coverage.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
