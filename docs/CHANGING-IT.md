# Changing it

For an agent or an engineer about to edit this repo. Read this before the first edit, not after the
first red.

`docs/WHY-IT-IS-LIKE-THIS.md` is the companion: it says why each rule below exists. This file says
what the rules are and how to work inside them.

## The six rules no lane may trade away

1. **The acceptance gate is a login, not a string.** Extend it, never weaken it.
2. **Print the denominator** on the same line as every verdict. Zero compared is a hard failure,
   never a pass. `engine/bin/denominator-lint.py` enforces it on new files.
3. **A guard nobody has watched fail is not a guard.** Demonstrate the failure first, then the fix,
   and quote both runs.
4. **Never widen a tolerance to turn a red green.** A WCAG failure is fixed by changing the colour,
   never the bar. Lowering `beats >= 3` to `beats >= 1` so a stalled run passes deletes the check.
5. **Colour never rides alone.** Every coloured state also carries a word.
6. **Secrets are references, never values.** No verb, log or config file prints one.

And one that governs prose: **no em dashes and no en dashes** anywhere you write, per
`your-brain/.claude/rules/voice-and-style.md`. Commas, colons, or
restructure. Write "Unknown as of <date>" rather than TBD.

## Adding or changing a state transition

Every state change is one function, registered once, called by every surface. There is no second
path and adding one is the change that quietly ends the property.

```python
import store

@store.transition("done")                       # role="runtime" is the default
def done(ctx, *, id, summary, agent="", force=False):
    ...
```

* Registering a name twice raises `DuplicateTransition` **at import time**. Two lanes cannot each
  define `done`.
* `apply()` wraps the whole transition in **one transaction**. A ported `done` is a row update plus
  a thread event plus an artifact record; a `done` that updated the row and lost the thread event is
  a partial state the old file bus could never produce.
* The `role=` you declare is a database role with its own grants, and the grants do not trust the
  registration. A transition registered under `runtime` cannot insert an event however it is
  written, because `brain_runtime` holds no INSERT on `event`.
* Reads go through `store.read()`, whose transaction Postgres has put in `SET TRANSACTION READ
  ONLY`. There is no flag that turns that off, and the suite asserts the absence of one. Do not add
  one.

This should return nothing outside a registered transition, and if it ever returns something, that
code cannot have run:

```bash
/usr/bin/grep -rn "INSERT INTO\|UPDATE .* SET" engine/ adapter/ ingest/ fabric/ queue/ web/ mcp/
```

Read `store/narrow_waist.md` before you argue with any of the above. Run its proof with
`python3 -m store.test_narrow_waist`.

## Adding a console control

Two guards, not one, and they answer different questions.

* **The allowlist** answers *may this room run this verb at all*. `web/rooms.py:ROOM_VERBS`, a
  frozen set per room, checked before `store.apply` is reached. The room comes from the **URL
  segment**, never from the payload, and the CSRF token is scoped to the room that issued it.
* **The per-item guard** answers *may this room run this verb on THIS row*.
  `rooms.assert_allowed_on`, which reads `brain.work_item` itself and not the rendered card. A guard
  that trusts the rendering layer for the fact it turns on is one template edit away from being
  false.

A card must never offer a verb the door will refuse. Render it disabled with the reason beside it:
an affordance that cannot work is worse than one that is absent.

If the behaviour is safety-bearing, **also put it in the database**. `web/rooms.py` is not on every
path; the CLI reaches the same rows with none of it. Migration 42 is the worked example.

Before adding anything to a room, read `web/MUST-NOT-BUILD.md`. All eleven prohibitions were
reviewed on 2026-08-27 and kept with zero amendments. To propose an amendment you must **name the
incident the prohibition was written against** and show how your change addresses it. "It would feel
nicer" is not that.

## Writing a migration

**Pick the next version by reading `brain.schema_migration`, never by listing a directory. Then
take `max(version) + 1`, never the lowest free number.**

Those are two rules and the second one is not decoration. The first stops you colliding with a
version already held. The second stops you landing in a **hole**, and reading the ledger is exactly
what surfaces a hole as available, so the first rule obeyed perfectly walks you into the second's
failure.

The ledger is global across `migrations/`, `queue/schema/` and `ingest/schema/`, and each file
declares its own version inside itself. Filename numbers are per-directory and do not equal ledger
versions. A migration that declares a version already in the ledger applies **nothing** and reports a
clean build, because `scratch-db.sh` applies only unrecorded versions and every file writes
`ON CONFLICT DO NOTHING`.

A migration that declares a version in a **hole** is worse, because it applies twice, in two
different places. `migration_list` orders files by recorded version and `cmd_migrate` applies only
what a database has not recorded, so one file at a hole has two positions in the sequence:

    a FRESH build   (`create`)     applies it BETWEEN the hole's neighbours
    an EXISTING store (`migrate`)  applies it AFTER every higher version already recorded

If the file is additive and independent the two orders agree and nothing happens. If it touches
anything those higher versions created, the fresh build and the live store diverge, and the fresh
build is the order every suite in this repo tests against. A suite green on an order the live store
will never see is a failure this repo has already paid for.

Ask the tree, which needs no database and prints the holes and the next version together:

```bash
engine/bin/scratch-db.sh ledger | tail -3
#     45 versions read, 1..45, no holes.  NEXT VERSION IS 46 -- take max(version) + 1.
```

And ask the store you are actually going to apply to, because the tree and the store are routinely
at different versions in either direction:

```bash
ENGINE_SCRATCH_DB=my_scratch engine/bin/scratch-db.sh psql -c \
  "SELECT max(version), count(*) FROM brain.schema_migration"
```

Live `brain` was at ledger **42** on 2026-08-27 with 41 rows, and was still there when re-read on
2026-08-29: version 39 was a hole lane E left when it was assigned 36 to 39 and used three of the
four. Migrations 42 and 43 both name that hole in their headers and decline to fill it. It is now
**fenced** rather than open, by `migrations/0039_a_reserved_number_is_not_a_migration.sql`, which
carries no DDL at all: a file with nothing to order is the one file at a hole for which both
application orders are provably identical. Its ledger row's `name` says in words that it is a
reservation, so nothing there claims work that did not happen.

A migration in this repo carries, in its own header:

* what it closes, **measured**, with the numbers and the date;
* why it is a new object rather than an edit to an applied one, because re-applying an applied
  migration is not on the table and a `CREATE OR REPLACE` silently falsifies the earlier file's
  description of live;
* what it does **not** close;
* a **rollback that has been executed** and then re-applied, not a paragraph.

Applying a migration to live `brain` is the operator's act at the keyboard. An agent that finds one
claimable raises a question instead. And do not trust an "applied clean on live" sentence in a
header: at least one of them was never true. Re-read `brain.schema_migration`.

`docs/SCHEMA-TOLERANCE.md` covers the other half: your code is one checkout and the store it opens is
whichever database `BRAIN_PG_DB` names. Counted 2026-08-19, this host held 132 databases, 130 with a
ledger, and 11 of those 130 at the tip. The direction of the gap is not stable and must not be
encoded: for four hours on 2026-08-18 live was behind the tree, and three minutes after the operator
applied four migrations it was ahead of 119 scratch stores.

## Testing

### Test from a `git archive` export, never the working tree

A shared tree carries every other lane's uncommitted edits. A suite that passes on an untracked file
passes on something that does not exist in the commit, and this has gone both ways here: at one
commit `test_contract.py` was green on the export and red in the tree, while `denominator-lint` was
green in the tree and red on the export. Neither runner prints which tree produced its verdict.

```bash
mkdir -p /tmp/head-export && git archive HEAD | tar -x -C /tmp/head-export
cd /tmp/head-export
/usr/bin/grep -rl '^#!' --include='*.sh' --include='swarm*' . | xargs chmod +x
```

**Exports lose the exec bit.** Every script here is mode `100644` in git, a `/mnt/c` artifact.
Verified again 2026-08-27. `chmod +x` after extracting, then prove the change was mode-only by
comparing content against the commit.

Export to `/tmp`, not into `outputs/`. There are already several embedded copies of this repo under
`outputs/` and the lint has to prune them by name.

### Use your own scratch database

Never `brain`, and never the shared `brain_scratch`, which every lane's suite reads by default.

```bash
ENGINE_SCRATCH_DB=brain_mine engine/bin/scratch-db.sh ensure
ENGINE_SCRATCH_DB=brain_mine engine/bin/scratch-db.sh drop     # when you are done
```

`scratch-db.sh` with no subcommand prints usage and touches no database, on purpose. **A lane's
scratch database is not private to that lane**: the builder globs the shared tree, so it also picks
up other lanes' uncommitted migrations. Read the ledger inside it before you trust a build.

### Print the denominator, and the three verdicts

```text
GREEN     the comparisons ran and passed.        Print the denominator.
RED       the comparisons ran and failed.        This is a claim about the CODE.
NOT RUN   the comparisons could not run.         Exit 77. Name the missing input and the remedy.
```

A suite whose input is live state must supply its own fixture or declare NOT RUN. It may never
report the environment as a code defect. A suite that can declare NOT RUN still carries at least one
hermetic scene that runs everywhere, or NOT RUN is deletion with a nicer name. Full rule:
`docs/SUITE-INPUT-RULE.md`.

Run the lint before you report:

```bash
python3 engine/bin/denominator-lint.py     # 0 clean, 1 violation, 2 the lint itself is broken
```

### The runners

```bash
ENGINE_SCRATCH_DB=brain_mine engine/tests/run-all.sh
ENGINE_SCRATCH_DB=brain_mine queue/tests/run-all.sh
BRAIN_PG_DB=brain_mine       web/tests/run-all.sh
```

`run-all.sh` has one owner. Do not edit a runner to register your suite; say what you want
registered and let the registration pass do it. Two lanes editing one runner is a merge conflict on
the one file that decides what runs.

`web/tests/run-all.sh` starts a **second** console on its own database and its own port, and verifies
the target by reading the serving process rather than by trusting the port. If you run a browser
suite by hand, do the same: `BRAIN_PG_DB` set on the test process does not reach a console that was
exec'd earlier with its own environment, so read `/proc/<pid>/environ` and check what the browser is
actually writing into. On this host `3103` has served the **live** store since 2026-08-23.

## Traps that have already cost this programme time

* **`python3`, not `bash`.** Costs a cycle every time.
* **The exec bit is not in git.** `chmod +x` after any export.
* **`grep` may be a shell function.** Run `type grep`. It answered `grep is a function` in a harness
  terminal on 2026-08-27, execing `ugrep` with `--ignore-files --hidden -I --exclude-dir=.git`,
  which silently skips gitignored paths and once produced a 470-file blind spot. It has flipped twice
  in four days with nothing in this tree changing, so **the answer written here is the thing not to
  trust**: run the check yourself, and push any NEGATIVE back through `/usr/bin/grep`.
* **`command grep` does not work under `xargs`, `find -exec`, `timeout` or `sudo`.** `command` is a
  shell builtin and those exec binaries, so the grep never runs and matches nothing, complaining only
  on stderr. With a tidy `2>/dev/null`, a total failure to run is indistinguishable from a clean
  negative. Use `/usr/bin/grep`.
* **Prove a search's coverage before reporting a negative.** Count the population, count what the
  search read, and print the gap on the same line as the verdict. Say which root you searched: both
  false negatives in this fleet's record came from a searcher reporting a narrow root as the whole
  repo.
* **A round number is a cap until proven otherwise.** A plain API read returned exactly 500; the real
  count was 752.
* **A zero may be a field-name error, not a finding.** Check the instrument can see anything before
  reporting that it saw nothing.
* **A wrapper's exit code is not the child's**, and a count read through a pipe is not the child's
  either.
* **`swarm artifacts` exits 2 on an empty ledger.** That is this repo's zero-denominator convention,
  not an error.

## Committing

The full rule is `docs/COMMIT-HYGIENE.md`. The short version:

1. **Commit only paths your own task produced.** This is the rule; the rest is how you keep it.
2. **Name every path on the command line.** Never `git add -A`, `git add .`, `git add -u`,
   `git commit -a`, or `git add <directory>/`.
3. Enumerating the whole tree by pathspec is still `git add -A`.
4. Read `git status --porcelain` before you stage and after you commit. Every line that is not yours
   must be unchanged between the two reads.
5. Never `checkout`, `stash`, `clean` or `reset --hard` a path you did not create. Those are the only
   four verbs in git that destroy work, and here the work they destroy is usually somebody else's.
6. Never rewrite a commit that is on `origin`.

There are dozens of untracked `outputs/` directories in this tree at any time. Each one is another
task's evidence. Commit yours, leave theirs alone.

## The safety-bearing files, if you only read a few

| File | Holds |
|---|---|
| `store/narrow_waist.md` | the one architectural property, and how it is enforced structurally |
| `store/session.py` | the read-only session, the five roles, `dsn()` failing closed |
| `store/transitions.py` | registration, one transaction per verb |
| `web/rooms.py` | the room allowlist and the per-row guard |
| `web/guard.py` | origin and CSRF, keyed on the route rather than the payload |
| `migrations/0036_*.sql` | the acceptance gate as a login |
| `migrations/0042_*.sql` | `done` is never filed over an agent's report |
| `web/MUST-NOT-BUILD.md` | the eleven prohibitions, each with its check |
| `roles/terminal.md` | what an agent is told before it touches anything |

---

Also here: `README.md` for what this is, `docs/WHY-IT-IS-LIKE-THIS.md` for the incident behind each
rule, `docs/OPERATING.md` for running it, `docs/KNOWN-GAPS.md` for what is missing.
