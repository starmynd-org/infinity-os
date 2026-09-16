# The narrow waist, and how this module enforces it

> One transition function per state change, exposed as a verb, called by every surface.
> No surface reimplements a transition.

## Why a rule was not enough

`bin/swarm` is safe because it is the **only writer**. The runner, the board, the operator typing
a verb and an agent calling a verb all reach the same code, which is why `SPEC.md` says "where
this file and `bin/swarm` disagree, `bin/swarm` wins" and why it refuses an API daemon. Many
surfaces are safe at once precisely because none of them can invent a state transition.

Postgres erodes that by default. A shared `store/` module is a **library**, and four lanes
importing a library can each write their own transitions. Every one of them will look reasonable
in review: a small helper, a well-named function, one INSERT. The drift is invisible per-commit
and total in aggregate.

So it is enforced structurally, in three places that do not rely on anyone reading this file.

## 1. The read path physically cannot write

`store.read()` yields a session whose transaction Postgres has put in **`SET TRANSACTION READ
ONLY`**. Reads can therefore be exposed as broadly as any lane wants, because exposing them costs
nothing: a caller who issues an INSERT through a read session is refused by the database.

```
>>> with store.read() as s:
...     s.query("INSERT INTO brain.work_item (title, lane) VALUES ('x','store')")
ReadOnlyViolation: a write was attempted through store.read(). State changes go through
store.apply(verb). Postgres refused this, which is the intended design.
```

The session object is not a psycopg2 cursor and does not hold one reachably: `__slots__` is
`("_cur",)` and there is no `.connection`, `.copy_expert`, or `.callproc`. Handing a caller a real
cursor hands them a route back to a write.

There is **no flag** on `read()` that turns the read-only property off. Adding one would be the
change that quietly ends the narrow waist, which is why the test suite asserts the absence of one.

## 2. A write connection exists only inside `apply()`

Nothing else in the package opens a writable connection and nothing hands one out. `store` exports
no `execute`, `insert`, `update`, `delete`, `cursor`, `connect` or `commit`:

```
>>> [n for n in dir(store) if n in ("execute","insert","update","cursor","connect","commit")]
[]
```

The practical consequence is greppable. This should return nothing:

```bash
grep -rn "INSERT INTO\|UPDATE .* SET" engine/ adapter/ ingest/ fabric/ queue/ web/ mcp/
```

and if it ever returns something, that code cannot have run, because no connection in the process
would accept it.

## 3. One name, one transition, one transaction

`@transition("done")` registers a state change. Registering the same name twice raises
`DuplicateTransition` **at import time**, so two lanes cannot each define `done` and the second
one finds out immediately rather than in production.

`apply()` wraps the whole transition in a single transaction. This is not tidiness. A ported
`done` is a row update **plus** a thread event **plus** an artifact record. A file rename was
all-or-nothing per task; three writes are not, and a `done` that updated the row and lost the
thread event is a partial state the file bus could never produce. Verified:

```
ok    test_transition_rolls_back_entirely_on_failure
ok    test_transition_commits_all_writes_together
```

## The second gate: the grants do not trust the registration

Each transition declares the database role it runs as. `@transition("event emit",
role="producer")` connects as `brain_producer`, which holds INSERT on `event` and **no SELECT**.
A transition registered under `runtime` cannot insert an event however it is written, because
`brain_runtime` holds no INSERT on `event` at all:

```
ok    test_runtime_role_cannot_insert_an_event
```

A gate in listener code is bypassable by a bug or a wrong branch. A gate as a database grant is
not bypassable by application logic. The module gate and the grant gate have to agree, and
neither one trusts the other.

## What this does not do, stated plainly

It does not stop a lane from importing `psycopg2` and opening its own connection with a resolved
secret. Nothing in a single process can. What it does is remove every **convenient** path: there
is no helper to reach for, no cursor lying around, and a lane that wants to bypass the waist has
to write its own connection code with its own secret resolution, which is a reviewable act rather
than an accident. The `runtime` role's grants then bound the damage — no DELETE anywhere, no DDL,
no INSERT on `event`.

## Subprocess versus in-process

A performance decision, not an architectural one. The engine is a library with a CLI over it, and
bulk reads and indexing may call `store.reads` directly. A CLI, an MCP tool, an n8n webhook and
the console all call `apply` with the same verb name and get the same code. **No integration
earns a private path.**

## Running the proof

```
$ python3 -m store.test_narrow_waist
ok    test_duplicate_registration_is_refused
ok    test_read_refuses_insert
ok    test_read_refuses_update
ok    test_read_session_exposes_no_connection
ok    test_runtime_role_cannot_insert_an_event
ok    test_secret_failure_is_closed
ok    test_store_exports_no_raw_write_helper
ok    test_subscriber_cannot_write_event_through_the_module
ok    test_transition_commits_all_writes_together
ok    test_transition_rolls_back_entirely_on_failure
ok    test_unknown_verb_is_refused

11 passed, 0 failed
```

Every assertion is about what the module and the database **refuse**. A suite that only checked
the happy path would pass just as well against a library that lets four lanes each write their own
transitions, which is the failure this exists to prevent.
