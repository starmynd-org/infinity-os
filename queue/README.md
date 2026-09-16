# `queue/` -- recommendations, the human queue, and its unblock-weighted ranking

The human half, which is the actual product:

> *"here's what you should be doing. Here's what's at the top of the list ... it's not first in
> first out or last in first out. It's based on all the priorities and what's being blocked the
> most."*

```
queue/schema/0007_queue.sql   5 tables, 4 views, 4 columns on recommendation, 2 gate triggers
queue/schema/0012_*.sql       ledger 23: brain.time_entry, the operator's stopwatch, append-only
queue/human_queue/            the package (named `human_queue`, see below)
queue/bin/queue               the CLI, thin over store.apply
queue/bin/queue-scratch-db.sh a scratch database, built on engine's, which refuses `brain`
queue/tests/                  six suites, 332 assertions (measured 2026-08-17, tasks 0281 + 0279)
queue/tools/rank_live_bus.py  ranks the operator's real bus, read only, writes nothing
queue/RAISED.md               what this lane raised rather than made
```

## The importable package is `human_queue`, not `queue`

`queue` is a Python standard library module. Measured, not feared: with the repo root on the
path, `from queue.human_queue import transitions` raises `ModuleNotFoundError: 'queue' is not a
package`, because a directory without `__init__.py` is only a namespace portion and the stdlib's
regular module wins the scan. The lane owns `queue/**` as a directory; the package inside it is
named for what it is, which is the shape `engine/swarm_engine/` already uses.

## The one thing this lane is judged on

> **There must be no code path by which a recommendation becomes executed work without a human
> decider.**

`recommend accept` is the only path from a proposal to executed work. There is no
`recommend execute`, no `--auto`, no batch accept, and no flag that turns the decider off.

It is enforced twice, independently:

1. **The verb** refuses an empty decider, a decider that is a registered agent, a recommendation
   that is not open, and a process running under the fleet runner.
2. **The trigger** `recommendation_human_decider` refuses `state = 'accepted'` without a
   non-agent decider, a `decided_at`, and `actor_type = 'human'`; refuses a row born accepted or
   born linked to work; and refuses to link executed work to anything not accepted. **It refuses
   the superuser at a psql prompt**, which is the difference between a gate and a convention.

`queue/tests/test_no_self_execution.py`, test name
`test_no_code_path_executes_a_recommendation_without_a_human`. 27 assertions, seven named
routes, and the summary assertion fails if any route does.

`requires_human` is stored as the producer declared it and **changes nothing**: acceptance needs
a human whatever the column says, and `queue doctor` counts every row that claimed otherwise. A
column that quietly enabled an unattended path would be the second acceptance path this lane
exists not to have.

## The other gate: a default may never carry a hard flag's action

If the originating task is `external` or `canon_touching` (ORed up the whole parent chain), the
stated default must be the null branch. Enforced at `ask` time by the trigger
`question_default_null_branch`, so it holds for the CLI, the console, an MCP wrapper and a human
typing INSERT.

**Consequence: silence can only ever ship the reversible branch, bounded and not by construction.
The bound is measured and it is stated here: `no action; all of it` is still accepted.** That
consequence is what makes a bad week degrade safely rather than dangerously, and it is why a calm
pending-defaults surface is honest rather than cosmetic. `brain.queue_default_event.null_branch`
records the verdict at fire time, so the claim is auditable afterwards rather than inferred from
the trigger still being installed.

The "bounded" is load-bearing. The unqualified version of that sentence read clean on 2026-08-16
while an external act had shipped, and the structural closure that replaced the guesswork was
proposed as making the property true *by construction* and then broken by twenty strings built
only from words it allows. This layer states what it has measured, never what it wishes were
true, and the surviving accept is tracked as its own open task rather than folded in.

Two classifiers, both in the database, both called by Python and never reimplemented, so no
parity test is needed to hold copies together:

* `brain.default_is_null_branch(text)` governs an UNFLAGGED task. It refuses any text naming an
  act verb, anywhere, however negated (migration 0009). An unflagged default may still act.
* `brain.default_is_null_branch_gated(text)` governs an `external` or `canon_touching` task. It
  adds two conditions (migration 0011): no RESIDUE, meaning every word is one this system
  recognises as belonging to a null branch, and no BARE IMPERATIVE, meaning no clause opens with
  a verb that is not a null-branch verb. The first closes acts whose verb nobody listed
  (`hold; envoyer la facture a Mick`) without naming a single synonym, because what carries the
  act is the object and the recipient, and both are residue. The second closes `no action; do it`,
  which names no object for the first to see.

## The ranking, and the four changes to `priority-model.md`

```
score = w_u·urgency + w_s·stakes + w_c·charter + w_unblock·U(i) + aging + bump(t)
```

1. **The effort term is gone.** It is the agent's token cost; subtracting it from a human ranking
   is a category error. Human cost is handled by tiering.
2. **U(i) is marginal and transitive** over the `depends_on` DAG:
   `U(i) = Σ_j (1/b(j))·(v(j) + γ·U(j))`, γ = 0.5, memoized, cycle-safe. `b(j)` divides because
   an item waiting on three things is only marginally unblocked by resolving one. Cycles are
   survived and recorded as findings rather than assumed away.
3. **The bump is a decaying additive term** (~24h half life), never a pin. Every bump carries a
   required reason, because every bump is a labelled disagreement between the operator and the
   model and that log is the weight-tuning dataset.
4. **The deadline band is gone** (task 0281, `queue/RAISED.md` item 5). This system has no
   deadline: there is no due-date column, so the band was read off `urgency == high`, which is
   already a term in the sum. A band is absolute, so that second reading of one signal outranked
   every other term at once, **including the bump**.

The declared `dependency_unblocking` signal is kept and reported beside the computed U, never
substituted for it: overwriting the declaration would destroy the evidence that a producer
mis-declared.

One hard rule on top, as a sort band: **an item with a live agent idle on it jumps first** (the
only condition that costs fleet wall-clock by the minute), then score, then id. The test for
what may sit in a band is whether the sum already carries it; `agent_idle_since` is in no term,
and urgency was. Blocked items are filtered and counted, never silently dropped; roots are
repaired above their dependents after sorting; each tier always renders its single oldest item,
marked.

**One ranking, and it is `rank.rank`.** `queue list` and `queue why` both reach it through
`reads.queue`, so `why` cannot report a rank the list does not render. `brain.queue_open` has no
ORDER BY and the console recomputes nothing. Asserted by bending the function and watching both
surfaces bend: `test_why_and_list_are_one_ranking_path_not_two`.

## Tiers sort cognitive mode, not minutes

`Decide` (act from prepared context) · `Judge` (open one thing, evaluate) · `Shape` (generate).
The real variable is whether the item evicts a loaded context.

**An irreversible item never sits in Decide whatever its estimate**, and reversibility is `low`
when unset, so an unassessed item cannot reach the fast lane by omission. `not fast` demotes in
one tap and writes a calibration miss against the producer in the same transaction, because
fast-lane trust is the whole asset and an absorbed bad estimate gets made again.

## Defer

Five kinds, each checked by the verb and again by a CHECK constraint, so **an untyped defer
cannot be stored**. `until-time`, `until-event`, `until-question`, `accept-default` (a decision,
not a snooze), `decline` (needs a reason).

**Defer-with-question is the one no ordinary task manager can have:** it posts an agent task to
produce what is missing and wakes when it lands. Deferral that makes the item cheaper rather
than older.

**The third defer refuses to be a defer.** Three deferrals means mis-scoped, not mis-timed.
Deferring past a silence window is refused until the caller acknowledges what silence will ship.

## The operator's stopwatch, and the two numbers it turns from assertions into facts

Ledger version 23, `queue/schema/0012_operator_time_entry.sql`. Applied to live 2026-08-17.

This system measures **agent** cost to four significant figures. Until this table it measured the
**operator's** time not at all, while the whole program exists to buy back his review hours.

```
queue time start <id>     one running timer per person, refused if one is already going
queue time stop           the ONE update this ledger permits on a row, and only once
queue time status         what is running, what was measured, what was thrown away
queue time correct <id>   supersede a stopped entry with a new one. It is never edited
queue depth               now prints hours of his time beside items per day, with coverage
queue ceilings            is `Decide` really a two-minute lane? measured
```

**`time status` is not a transition and that is not an omission.** It changes no state.
`store.registered()` is the audit surface for what *can* change state, and a read in that list is
a name a reviewer has to check and then discover is inert. `correct` **is** a fourth verb, because
the `corrects` column would otherwise be unwritable: the narrow waist admits no raw INSERT, so a
correction path that is not a registered transition is a correction path that does not exist.

**Append-only, for migration 22's reason plus one.** A record you can silently rewrite is not a
record — and this one is a *measurement input*, so the moment a stopped interval can be edited,
every figure computed from it has a provenance nobody can reconstruct. A correction is a new row
naming the one it supersedes; both survive, and `correction_reason` is required by a CHECK.
Proven, not asserted: `brain_runtime` — the credential every agent process holds — was run at
migration 22's own four attack shapes against live and refused at every one.

### Three policies, each decided rather than defaulted

1. **A forgotten timer is `abandoned`, never a fourteen-hour measurement.** Past four hours the
   trigger *forces* `ended_how = 'abandoned'` and the entry leaves every mean. **Forced, not
   refused**, because a refusal would strand the running row forever — nothing else can close
   one. **Not clamped to the cap**, because a clamp is indistinguishable in the mean from a real
   four-hour session: excluding it loses a data point, clamping it manufactures one. The stated
   cost of the bound is that a genuinely long session has to be several entries.
2. **Two timers at once are refused**, by the verb in words and by a partial unique index against
   a psql prompt. The doctrine is that context switching *is* the cost; the arithmetic is that
   overlapping intervals let measured minutes exceed the wall-clock they are measured from, which
   makes a clearance rate wrong rather than untidy.
3. **A timer is never required.** An untimed item is normal and a system that nagged for one would
   collect compliance entries rather than measurements. So the figures report **coverage** —
   *"measured over N of M"* — and a tier with no measured entry reports `null` and its depth, never
   another tier's mean wearing a per-tier label. **There is no fallback anywhere in
   `depth_and_clearance`.**

`reads.operator_minutes(hours=..., who=...)` is the third caller of that coverage rule and the
only one with no CLI wrapper: it is what the console's Brief divides fleet hours by (task 0290).
It exists rather than the Brief calling `time_status` for two reasons. It takes **hours**, because
that screen states everything over its own 12-hour window and a ratio whose halves cover different
spans is not a ratio; and it reports coverage **without** reading the queue, because
`depth_and_clearance` reads every open item and `web/app.py:_queue_ctx` keeps it off the console's
three-second poll — which the Brief is on. `_cleared`, `_ledger_counts` and `_coverage` are shared
by both, so *cleared* and *measured over N of M* have one definition each.

`queue depth` now prints **two currencies and never mixes them**: `eta_days` from item throughput
(real, but it has no minutes in it and it averages over days the queue was never opened) and
`stopwatch.eta_hours` from measured operator minutes. `tier_at_start` is snapshotted because
`queue demote` moves the tier of exactly the items whose duration matters most — read live, a
Decide item's nine-minute overrun would be counted against Judge, and the Decide ceiling would
measure clean forever.

`tiers.TIER_CEILING_SECONDS` holds `decide: 120` and `None` for the other two, because **nobody
has claimed a number for Judge or Shape** and filling them in would manufacture a claim in order
to have something to measure.

### For a future activity-monitor lane — what this one learned

OS-level tracking was explicitly out of scope here and is a larger, privacy-sensitive piece of
work. Five things it should not have to rediscover:

1. **The ledger shape already fits.** `brain.time_entry` has no column that assumes a human typed
   the start. An activity monitor writes `who`, `source_type`/`source_id`, an interval and a
   `note`; what it must not do is write its own INSERT. Give it a verb, and give it a *distinct
   `who`* (or a `source` column added by a new migration) so a monitored interval is never
   silently averaged together with a hand-timed one — the two have completely different error
   distributions and coverage must be reportable per source.
2. **The cap is the wrong instrument for automatic capture and must be revisited, not inherited.**
   Four hours exists because a *manual* timer running that long is almost certainly a closed
   laptop. An activity monitor knows the laptop closed, so it should end the interval on that
   evidence rather than relabel it after the fact. Inheriting the cap unexamined would throw away
   real long sessions the monitor can actually vouch for.
3. **One-timer-per-person will collide immediately.** The partial unique index is per `who`, and a
   monitor observing two applications at once has two intervals. Either it reduces to one focused
   interval before writing (probably right — the queue's premise is that one thing is being
   worked) or it needs a different `who`, and the second choice quietly re-admits the overlapping
   minutes this ledger refuses.
4. **Attribution is the hard part, not capture.** Nothing in an OS event says which queue item a
   window belonged to. The manual timer's whole value is that the operator answered that question.
   A monitor that guesses will produce high coverage and low truth, which is strictly worse than
   this table's low coverage and high truth — and *the coverage field is what makes that
   comparison possible*, so keep reporting it.
5. **Consent is a state change, not a config flag.** Whatever it records is about a person. If it
   ships, the on/off should be a verb with a thread event, so "when did this start watching" is
   answerable from the append-only trail rather than from a file's mtime.

## Composition, so there is still one implementation of everything

A transition may call another lane's transition FUNCTION with its own `ctx` -- one
implementation, one transaction. It may never re-implement one. `recommend accept` creates its
work item through `swarm_engine.transitions.post`, so hard flags inherit by OR exactly as they
would for a hand-posted task; `queue default fire` answers through
`swarm_engine.transitions.answer`. Copying either body would have produced a second writer.

## Running it

```bash
./queue/bin/queue-scratch-db.sh create          # 0001, 0002, then 0007
python3 queue/tests/test_no_self_execution.py   # the program success criterion
python3 queue/tests/test_ask_default_refusal.py
python3 queue/tests/test_queue_mechanics.py
./queue/bin/queue list ; ./queue/bin/queue why 0088 ; ./queue/bin/queue doctor
```

**Pick the next migration number by reading `brain.schema_migration`, never by listing a
directory, and re-read it immediately before applying.** This file was written as `0005` against
a ledger whose maximum was 4, and two more lanes landed 5 and 6 before it reached the store. The
guard at the top of the migration caught it and refused before `BEGIN`, so nothing partial
landed.
