# Your code does not know what schema it will meet

There is one tree of migrations and there are **132 databases** on this host. 130 of them carry
a `brain.schema_migration` ledger, and on 2026-08-19 exactly **11 of those 130 were at the tip**.
The other 119 sat anywhere from version 6 to version 30. Your code is one checkout; the store it
opens is whichever of those 130 `BRAIN_PG_DB` happens to name.

The gap is not a bug somebody will close before your code runs. It is the shape of the fleet.
Applying a migration to the live store is the operator's act (V00) and the permission classifier
refuses agent writes to it, so a lane writes the migration, proves it on a clone, and hands it
over. Between the handover and the operator's hand, the code is live and the element is not.

**The direction of the gap is not stable and you must not encode it.** For four hours on
2026-08-18 the live store was at 27 while `migrations/` declared 31, and both of that night's
outages were code arriving ahead of live. At 22:13:46Z the operator applied 28 through 31 and the
direction inverted inside three minutes: live became the tip and 119 scratch databases became the
laggards. A rule phrased as "live is behind you" was already false the day it was written. The
durable statement is the one below.

> The ledger of the store your code will open is not the tip of `migrations/`, in either
> direction, and the only way to know it is to read it.

```sql
SELECT max(version) FROM brain.schema_migration;
```

## The rule

1. **Code that reads a schema element must tolerate that element being absent, unless every
   store it can open is proven to have it.** The element is any table, view, column, function,
   index or constraint a migration creates. Tolerate means the read returns an honest empty or
   falls back to the pre-migration behaviour and the process keeps serving. It does not mean
   `except Exception: pass`.
2. **Absence has two shapes and a guard against one is not a guard against the other.** SQL that
   names the element raises `UndefinedTable` or `UndefinedColumn` at query time. `row["new_col"]`
   after `SELECT *` raises `KeyError`, because the query SUCCEEDS and the dict simply has no key.
   The second outage of 2026-08-18 was written by a lane that had read the first one, and it
   still shipped, because the first one taught `UndefinedTable` and the second one was a
   `KeyError`. `.get()` is the whole fix for that shape.
3. **Tolerance is proven by running the path against a store at a lower ledger, and quoting what
   it printed.** A green suite on a database at the tip cannot fail this check, so it is not
   evidence and must not be reported as any. Name the database and its `max(version)` in the
   report. There are 119 to choose from; `brain_t4_0405` was at 27 and `brain_t5_0290` at 23 when
   this was written, and `SELECT max(version)` tells you what they are now.
4. **The tolerated path returns a fact, never a guess.** Fall back only where the absent element
   is the *only* place the answer could live, so "nothing" is true rather than degraded. Where
   absence means you cannot know, raise: an invented answer from a lane that could not read the
   table is the confidently-wrong number this whole system exists to prevent.
5. **A new surface may fail loudly; a path that already served may not start failing.** Writing
   through a table that does not exist must fail where the operator can see it, because an attach
   against a store with no table must not look like it worked. The test is one question: does
   this path serve today at the ledger this store is on? If yes, your migration may not take it
   down. If the path did not exist before your migration, nothing regresses when it raises.
6. **A migration that only replaces a view body cannot crash, and is therefore the dangerous
   one.** Measured: migrations 28, 30 and 31 each `CREATE OR REPLACE VIEW brain.queue_open`, and
   all three emit the same 21 columns. No name changes, so no exception is reachable by any
   `try`. The view exists, the query succeeds, and on a store below 31 it silently returns rows
   it should have filtered out. Rule 7's handover note is the only thing that catches this class,
   which is why it is a rule and not a courtesy.
7. **Ship the tolerance in the same commit as the migration, and hand over four facts.** In your
   report and in the apply runbook: which migrations are unapplied against which store, what
   `max(version)` was when you wrote the code, what is degraded until the operator applies, and
   the exact line the operator runs. `web/docs/APPLY-IMAGES.md` is the model.

## What a reviewer checks

Seven questions with yes/no answers, none of which require trusting the author:

- [ ] Does this change read a table, view, column or function that any migration adds? (Derive
      the set by the method below. Do not trust a filename and do not trust `git log`.)
- [ ] Is every read of it reachable from a path that already serves guarded, in BOTH shapes:
      the SQL that names it and the `row[...]` that follows a `SELECT *`?
- [ ] Was the guarded path RUN against a named store at a lower ledger, with its `max(version)`
      and the output quoted?
- [ ] Does the fallback return a true fact, or does it invent one?
- [ ] Does the write path still fail loudly?
- [ ] If the migration replaces a view body, does the report say what reads short until it is
      applied? No exception will tell you.
- [ ] Does the change ENFORCE anything — a constraint, a trigger, a unique index? Then the older
      store does not raise, it PERMITS WHAT THE NEWER FORBIDS, and no `has_column` guard can see
      it. Say in the report what goes unenforced until the migration is applied.
- [ ] Is any enumeration in the report being cited as coverage? It is not one. See below.

### An enumeration is not coverage, and this was measured

`engine/bin/ledger-census.py` lists what this lane's code names that an older store may lack. It
was written as the corrective for point-fixing, after two fixes each revealed the next site. **It
is not that corrective, and the commit that introduced it contained two defects it could not see.**

CAP14 REV-076 counted every DDL statement across migrations 65 to 67:

| what the statement does | statements | the census sees |
|---|---|---|
| **crash class** — the older store raises: `ADD COLUMN`, `RENAME COLUMN`, views, functions | 20 | 9 |
| **enforcement class** — nothing raises; the older store simply permits what the newer forbids: `ADD CONSTRAINT` ×16, `CREATE TRIGGER`, `CREATE INDEX` | 18 | **0** |
| the rest (`DROP CONSTRAINT`, `DROP VIEW`, `DROP TRIGGER`) | 16 | 0 |

**The instance that settles it: migration 66 enforces single use with five `ADD CONSTRAINT`, a
partial `UNIQUE INDEX` and a trigger. Not one is an `ADD COLUMN`.** The census sees only the seven
columns those constraints operate on — so a store carrying the columns without the constraints,
which `ADD COLUMN IF NOT EXISTS` makes an entirely reachable partial state, **reports as covered
while single use goes unenforced.** There is no `has_column` question whose answer is "is this
constraint enforced".

**The general point, and it is bigger than this file:** an enumeration is another hand-written
list and inherits the same failure one remove out. The corrective is DERIVATION — read the fact
out of the migration rather than keeping a second copy of it. Where a list cannot be derived, say
in its header what it does not cover, which is the only honest version of a hand-maintained set.
- [ ] Does the report name the unapplied set, the ledger it was written against, what is
      degraded, and the operator's exact apply line?

## The pattern that worked

`web/images.py:531`, landed in `ba8e5e3` at 18:57:23Z on 2026-08-18, **in the same commit as the
migration it depends on**:

```python
    except psycopg2.errors.UndefinedTable:
        # A STORE BELOW MIGRATION 29, and the console still renders.
        _warn_no_table()
        _INDEX[0], _INDEX[1] = now, {}
        return _INDEX[1]
```

Run against `brain_t4_0405` at ledger 27 on 2026-08-19 it returns `{}` and prints one line on
stderr naming the migration file and the runbook. Four properties make it the pattern rather than
a swallow:

- It catches **one** exception class, not `Exception`.
- It warns **once per process**, naming the migration file and the runbook. A warning that
  repeats on a three-second poll is one an operator filters out.
- Its own comment states why the fallback is a fact and not a guess: `brain.image_attachment` is
  the only place an attachment lives, so if the table is absent then no image was ever attached
  and rendering nothing is true. It contrasts itself with the Queue room, which raises, because
  an order invented without D6b's tables would be a second classifier.
- It says the write path is a different question and fails loudly.

`queue/human_queue/options.py:252` and `:243` are the same pattern for a table and for a pair of
columns, matching on the element name in the exception text; both return `{}` on a ledger-27
store. `engine/swarm_engine/cli.py:571` is the pattern for the `KeyError` shape and costs four
characters: `if q.get("withdrawn_at"):` in front of the bracket accesses that follow.

## How to derive the set

Do not read `migrations/` and subtract. The filename prefix is a per-lane counter and it lies:
`queue/schema/0009` is ledger version 11, `/0010` is 16, `/0011` is 18, `/0012` is 23, `/0013` is
28 and `/0014` is 30. The `INSERT INTO brain.schema_migration` at the bottom of each file is the
only key.

There are **five** schema directories and no single one of them is the list: `migrations/`,
`budget/schema/`, `ingest/schema/`, `queue/schema/`, `voice/schema/`. (`0029_image_attachment.sql`
says "all four" and omits `ingest/`; `voice/schema/` is empty today. Count the directories
yourself.)

```bash
# the ledger of the store this process will actually open
python3 -c "import sys;sys.path.insert(0,'.');from store import session;print(session.health()['schema_version'])"

# every version any file on disk declares, with its name
grep -A2 -rn "INSERT INTO brain.schema_migration" migrations/ */schema/

# every store on this host and where each one is, which is the number rule 1 turns on
python3 - <<'PY'
import sys, os; sys.path.insert(0, '.')
from store import session
with session.read() as s:
    dbs = [r['datname'] for r in s.query(
        "SELECT datname FROM pg_database WHERE datistemplate=false ORDER BY datname")]
for db in dbs:
    os.environ['BRAIN_PG_DB'] = db
    try:
        with session.read() as s2:
            print(db, s2.scalar("SELECT max(version) FROM brain.schema_migration"))
    except Exception as e:
        print(db, "no ledger:", type(e).__name__)
PY
```

Everything declared and not in a given store's `brain.schema_migration` is unapplied **for that
store**. Then read what each unapplied file creates and grep the tree for each element by name.

One gap this method does not cover, stated so you do not mistake it for coverage: the six files
in `ingest/schema/` declare **no** ledger row at all, and one of them
(`0006_absence_parent_age_basis.sql`) says in its own header that it applies to both profiles. A
file that declares nothing is invisible to `brain.schema_migration` and to every check built on
it. If you own that lane, that is yours to close.

| How your code reads it | What absence does | Verdict |
|---|---|---|
| SQL naming the column or table | `UndefinedColumn` / `UndefinedTable` at query time | does not tolerate |
| `row["new_col"]` after `SELECT *` | `KeyError`: the query succeeds, the dict has no key | does not tolerate |
| `row.get("new_col")` after `SELECT *` | `None`, i.e. pre-migration behaviour | tolerates |
| a guarded reader that returns `{}` | the caller's `if` is simply false | tolerates |
| `CREATE OR REPLACE VIEW`, body only | nothing. The answer is silently short or silently long | no exception exists; declare it |

## Why this file exists

Twice on 2026-08-18, about four hours apart, in two different lanes, against a live store at
ledger 27 with 28, 29, 30 and 31 written, correct, and unapplied. Nobody made a mistake in SQL.
Every lane's tests passed. They passed against a schema the running system did not have.

**18:40Z, task 0167.** `web/images.py` landed reading `brain.image_attachment` from
`web/model.py:178`, on every card, with `migrations/0029_image_attachment.sql` unapplied. Every
room of the console returned HTTP 500 in every database:
`psycopg2.errors.UndefinedTable: relation "brain.image_attachment" does not exist`. The operator's
live console was 500ing on `/queue`. Closed at 18:57:23Z by the guard quoted above.

**22:33Z, task 0159, and this one was worse.** `engine/swarm_engine/transitions.py:1125` reads
`q["withdrawn_at"]` unconditionally, from a `SELECT *`, so it is a `KeyError` and not a database
error. The column is added by `migrations/0031_question_withdrawal.sql`, unapplied. `swarm answer`
-- the one verb the operator uses -- was down for him and for every agent, with eleven open
questions in front of him, and `swarm questions` was down beside it, so he could neither answer
them nor list them.

The operator applied 28 through 31 himself between 22:13:46Z and 22:14:25Z on 2026-08-18. That
closed both outages and closed nothing else: the eight intolerant read sites are all still in the
tree, and 119 stores on this host are still below the tip. Rule 3 is why the audit that found
them could report a verdict per site instead of an opinion.

Full audit with the site-by-site verdicts, the stores each was run against and the measurement
method: `outputs/2026-08-18-T1-0211-schema-tolerance/REPORT.md`.
