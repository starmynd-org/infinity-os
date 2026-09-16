# The fleet config has no history, and the fingerprint is the way to give it one

**Task 0273, lane engine, 2026-08-19.** A proposal, plus the one read-only piece of it that was
trivial enough to build. Everything under "What to build" is proposed and **not implemented**.

## The gap, measured

`~/.brain-runtime/config.json` decides what model each terminal runs, which lanes it may claim
from, and which agents exist at all. On 2026-08-18 it changed **three times in one day** -- model,
lanes, agent set -- with no record of who, when or why. A lane opened an investigation to find
out and the answer came back "the commander" (task 0215, question q0217).

Three facts, checked in this repo on 2026-08-19 rather than assumed:

1. **Nothing in this codebase writes that file.** `engine/swarm_engine/config.py` reads it and
   has no write path; the only config write anywhere in the engine is
   `fleet_config.set_agent_override`, which writes a **per-agent override** into
   `brain.runtime_flag` and is already fully provenanced -- `set_by` comes from
   `brain.current_human()` and the transition refuses a connection the database does not know as
   a human. So every change to the FILE is a human editing it by hand.
2. **`~/.brain-runtime` is not a git repository.** `git rev-parse --show-toplevel` there returns
   `fatal: not a git repository`. There is no history to read.
3. **The runner never records it.** `engine/bin/swarm-fleet` and `engine/bin/swarm-run` contain
   no reference to `config.json`, no hash and no fingerprint of any kind. A run leaves no
   evidence of what configuration produced it.

## The decision: fingerprint, not version control

The brief named both options and preferred the fingerprint. Agreed, and the argument is sharper
than "it is easier":

**Version control answers a question nobody is asking at the moment they ask it.** Putting the
file under git gives a commit per *committed* change. The failure mode here is an edit made in
place at 22:40 by somebody moving fast during an incident, which is exactly the edit that does
not get committed. A git history that is silent about the change that mattered is worse than no
history, because its silence reads as "nothing changed".

**A fingerprint recorded at every runner start cannot be skipped, because nobody has to
remember it.** It is written by the machine that is about to consume the file, at the moment it
consumes it. It survives an in-place edit, an editor that rewrites the file wholesale, and a
`cp` from another host. It does not tell you *what* changed -- and that is a real limitation,
stated rather than hidden: it tells you that the fleet at 23:10 was not running the configuration
the fleet at 21:00 was running, and it stamps both moments. That is precisely the fact task 0215
had to open an investigation to establish.

The two are not exclusive and the fingerprint is the one that is load-bearing. If the file is
later put under version control, the fingerprint is what catches the uncommitted edit.

## Built, because it was trivial and additive

    swarm config --fingerprint

`engine/swarm_engine/config.py:fingerprint()` and the flag in `cmd_config`. A **read**: it hashes
the file's bytes and prints the sha256, the byte count, the mtime and the path. It writes nothing
and cannot break a caller that does not pass the flag. Exit 2 and a named line when the file is
absent, because a fleet running on `DEFAULT_CONFIG` is a fact worth exiting differently for.

    $ swarm config --fingerprint
    176eb07e128c2a546d02477e2b9183fbb0f70350da216ee6bc14dfaa24817472  5765 bytes  \
mtime 2026-08-19T00:12:11.625246+00:00  /home/you/.brain-runtime/config.json

Bytes, not the parsed dict. A reordered key or an edited comment is still an edit somebody made,
and the question a reader is asking is whether it is the same file -- not whether it happens to
parse the same.

## What to build. PROPOSED, NOT BUILT.

1. **`swarm-fleet up` records the fingerprint before it starts a single window**, into
   `brain.runtime_flag` under `config_fingerprint`: `value` the sha256, `set_by` the host,
   `note` the byte count and mtime. Those four columns have existed since migration 1, so this
   needs **no migration** and serves on every store on this host whatever its ledger says.
2. **A `fleet.config.fingerprinted` event** carrying the previous fingerprint and the new one,
   emitted only **when they differ**. `runtime_flag` holds the latest and an ON CONFLICT
   overwrites, so the flag alone gives state and not a trail; the event is what makes the trail.
   Emitting only on change keeps the volume at "a few a day" instead of one per runner start,
   which matters against `fabric/types.py:DAILY_EVENT_BUDGET` (5000/day).
3. **`swarm status` prints the fingerprint's first 8 characters and its age**, next to where it
   now prints who paused the fleet. An admiral comparing two moments should not have to run a
   second command to find out whether the fleet was reconfigured between them.
4. **`swarm doctor` warns when the running fingerprint differs from the one recorded at start.**
   That is the case where the file was edited underneath a live fleet: the terminals are running
   the old config and the next one to start will not be. Nothing detects that today.

## Why none of it is in this task's commit

The task's definition of done says "your proposal for config provenance, not implemented unless
it is trivial and additive". Item 1 is a write on the runner's startup path and items 2 to 4
depend on it. The read above is additive by construction; a write into `swarm-fleet up` is a
change to how the fleet boots, and it belongs to a task somebody reviews as one.

---

## What got built, 2026-08-27, task 0385 (lane F, the admin verb group)

**The other half of this proposal, and not the four items above.** This file argued for a
fingerprint because nothing recorded WHO changed the file. Lane F went at the same gap from the
other end: rather than fingerprinting a file only a hand writes, it gave config a **writer**, so
the change that matters is recorded at the moment it is made.

    resolution order, after 0385:   code default  <  config file  <  brain.config_setting

`migrations/0040_admin_config_provenance.sql` adds `brain.config_setting` (the current store
override per `(scope, key)`) and `brain.admin_change` (append-only, one row per admin verb that
changed something). `swarm admin config set|unset`, `swarm admin agent add|remove` and
`swarm admin human plan|confirm|revoke` are the writers, all through `store.apply`.

Three properties this file's fingerprint could not have:

- **who**, from the LOGIN. Migration 40's triggers overwrite `set_by` and `changed_by` from
  `brain.current_human()` rather than checking an argument, so a caller cannot spell its own
  attribution. Measured: a raw `INSERT ... set_by = 'i-said-so'` as `brain_operator` lands the row
  reading `operator`.
- **what changed to what**, from `brain.admin_change.old_value` and `new_value`. The three edits
  in one day that this file opened with would each be one row.
- **and the file is not the second writer.** Nothing here writes `config.json`. The file keeps its
  tolerance for unknown keys, which `engine/swarm_engine/config.py` argues for at length, and the
  store layer sits on top.

**Items 1 to 4 above are STILL NOT BUILT.** They are about the file, and they remain the right
answer for the file: an operator editing `~/.brain-runtime/config.json` by hand still leaves no
record, and `swarm config --fingerprint` is still the only thing that notices. What 0385 changes is
that there is now a route which does not require editing the file at all, and every use of it is
recorded. `swarm config --layers` prints, per key, the file value, the store value and which is in
force.
