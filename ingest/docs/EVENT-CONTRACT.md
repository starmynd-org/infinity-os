# D3 → D5: the session lifecycle producer

D3 is the fabric's first producer. **D5 owns `event`, the `event emit` verb and the subscriber
contract; D3 owns only the producer side.** This file is the interface, posted here so D5 does
not have to read D3's working tree.

## The rule this obeys

D00: *"An event producer calls `event emit`. It does not INSERT."* `ingest/ingest/events.py`
therefore builds the envelope and hands it to `event emit`. It has no INSERT path into
`event`, which is what keeps the `producer` role's INSERT-only grant meaningful rather than
decorative.

## Two event types

| `event_type` | Emitted when | Emitted by |
|---|---|---|
| `session.started` | a session is registered for the first time | `session register`, first insert only |
| `session.ended` | a session is closed for the first time | `session end`, first close only |

Both are emitted **inside the verb's transaction**. A committed session can never be missing
its event and a rolled-back one can never have emitted a phantom.

Re-registering an existing session emits nothing. Re-ending an already-ended session emits
nothing. Both verbs are idempotent because `SessionStart` fires again on `--resume`.

## The envelope

```json
{
  "event_type":   "session.started",
  "occurred_at":  "2026-08-16T12:08:06.365475+00:00",
  "subject_type": "session",
  "subject_id":   "5d12a2ef-3eff-493b-afb5-4eefee46d2df",
  "produced_by":  "d3-ingest",
  "actor_type":   "hybrid",
  "host_id":      "wsl:laptop:966ee2153ffa",
  "payload":      { }
}
```

`occurred_at` is when the thing **happened**, not when it was emitted. It is carried in the
envelope precisely so a replayed event keeps the real time. Do not default it at consume time.

`subject_id` is the `session_key`, which is the session uuid for a main session and
`<parent_session_uuid>:<agent_id>` for a subagent. It is not always a uuid.

`host_id` says which machine the paths in the payload are absolute on. v1 is local; a VPS move
later invalidates every path whose `host_id` is not the reader's own.

### `actor_type`, the eighth key (added 2026-08-17, task 0300)

This file used to fix **seven** keys and `actor_type` was not among them, so the consuming side
had nothing to read and `fabric/producers/sessions.py` supplied the literal `"ai"` on every
session event instead. `session register` writes `actor_type='hybrid'` for a hook-registered
Claude Code session, on the stated grounds that *a Claude Code session is a human and a model
together* (`ingest/bin/claude-session-hook`). The two never agreed. Measured on live `brain`:

```
session.started   event=ai  session=hybrid    78 rows
session.ended     event=ai  session=hybrid    72 rows
```

150 events asserting one thing about the session row that asserts another, and both are reachable
from the same `session_id` join, so the walk 0287 built crossed the contradiction on every hop.

**It is a top-level key and not a payload field**, because it maps to a column,
`brain.event.actor_type`, exactly as `produced_by` does. A payload field reaches only
`payload_summary`, which is free text: the column would have stayed wrong while the summary said
otherwise, which is worse than one wrong answer.

**Its value is the `session` row's, read off this lane's own `RETURNING` clause** at the moment
the row is written, never the argument the caller passed in. The two are equal today. They stop
being equal as soon as the upsert's merge precedence prefers the stored value to `EXCLUDED`'s,
and a second independent statement of one fact is what produced the 150 rows in the first place.

**An omitted key means NULL, which means "not recorded".** `brain.actor_type` admits NULL and
`brain.event.actor_type` already held it on 50 rows before this change. It does **not** mean
`ai`: `fabric/bin/event-emit` refuses to default it, and a value outside
`('human','ai','hybrid')` is refused by name rather than left to fail the INSERT.

#### The 150 already-emitted rows are LEFT WRONG, deliberately, and expire 2026-08-31

Said out loud rather than defaulted to, because "we fixed it going forward" reads as "there is
nothing wrong in the table" and there is:

- **No producer path can correct them.** Measured on live, not read off the migration:
  `brain_producer` holds `INSERT` on `brain.event` and nothing else. Correcting them means a
  hand-written `UPDATE` as `brain_owner` against the event log, outside every verb, and there is
  no `event correct` verb because an event log is not a table you go back and revise. That is a
  larger breach of D00 than the wrong value is.
- **They expire on their own, and soon.** All 150 are `machine` class. `brain.sweep_events()`
  deletes `machine` rows with `occurred_at < now() - interval '14 days'`; the newest of the 150
  occurred at `2026-08-17T13:52:46Z`, so the last one is sweepable from **2026-08-31**.
- **The durable record was never wrong.** `brain.session.actor_type` says `hybrid` for every one
  of those sessions. `producers/sessions.py` states why a session event is `machine` class: the
  event exists so a subscriber can react at 02:00, not so anything is remembered.

**Who can still be misled until 2026-08-31, stated plainly.** No subscriber reads
`event.actor_type` and no query joins on it, but two surfaces print the whole row --
`python3 -m fabric.cli show <seq>` (a positional, not `--seq`) and the console's event detail
page (`web/app.py`) -- and neither joins to `session`, so a human reading one of those 150 rows
sees `ai` with nothing beside it to contradict. If that is not acceptable, the fix is an
operator-authorised `brain_owner` UPDATE, not a change to any code path above.

### `session.started` payload

```json
{
  "kind": "main",                    // 'main' | 'subagent'
  "harness": "claude-code",
  "harness_version": "2.1.233",      // nullable
  "model": "claude-opus-5",          // nullable: not in the hook payload, only in the transcript
  "workdir": "/mnt/c/Users/you/repos/infinity-os",
  "workdir_host_id": "wsl:laptop:966ee2153ffa",
  "parent_session_key": null,
  "workflow_id": null,
  "entrypoint": "cli",
  "permission_mode": null,
  "git_branch": null,
  "stated_goal": null,               // null at SessionStart; the first prompt has not arrived
  "stated_goal_tier": null,
  "registration_source": "hook"      // 'hook' | 'backfill' | 'manual'
}
```

### `session.ended` payload

```json
{
  "end_reason": "clear",             // whatever the harness reported, or 'hook'
  "turns_user": null,
  "turns_assistant": null,
  "tokens": null
}
```

**Every field in both payloads is nullable and a null means "not recorded", never zero.**
`model` is genuinely null on a hook-registered `session.started` because the SessionStart hook
payload does not carry it; it is filled in later from the transcript when the session is
indexed. A subscriber that treats a null model as "no model" will be wrong.

## Backfill emits nothing, on purpose

The 1123 backfilled sessions did **not** emit `session.started`. Replaying two months of
starts into a fabric that never saw them would tell every subscriber that 1123 sessions began
the moment the index was built. Backfill is an index, not a history replay. If D5 or D6 wants
historical sessions they read the `session` table, which is the record; the fabric carries
what is happening now.

## The outbox, and the verb it drains through (wired 2026-08-17, task 0287)

Every envelope is parked in `ingest.event_outbox` with `emit_status='pending'` inside the
verb's own transaction, and emitted **after that transaction commits**. **The outbox is a
producer-side buffer, not a second event log — do not subscribe to it.**

`BRAIN_EVENT_EMIT` names the command. It defaults to `fabric/bin/event-emit` in this repo, so
nothing needs exporting; set it only to point somewhere else.

```bash
ingest/bin/ingest events drain          # replays pending rows, fresh envelopes before retries
ingest/bin/ingest events list --status failed        # what was retired, and why, untruncated
ingest/bin/ingest events requeue --seq N --reason "…" # put a retired envelope back
```

`drain` marks replayed rows `emitted` and never replays one twice. It also runs automatically
after every ingest transaction, bounded to `AFTER_COMMIT_DRAIN_LIMIT` envelopes, which is what
makes a live session's two events reach the bus without anyone running a command.

### An envelope that can never emit is retired, not left pending (task 0302)

`emit_status` has always had three values and until 0302 the drain only ever wrote two: a
permanently undeliverable envelope sat `pending` forever, indistinguishable from one waiting
its turn, at the head of a FIFO queue. Two changes close that:

- **Order is `(attempts, seq)`, not `seq`.** Fresh envelopes outrank retries; FIFO is preserved
  within an attempt count. A row that is going to be refused cannot hold the head.
- **`BRAIN_EVENT_MAX_ATTEMPTS` refusals (default 5) write `emit_status='failed'`** with
  `failed_at`, the count, and the last refusal verbatim in `emit_detail`.

The drain never reads a refusal and decides it is *permanent*. It counts. The seven envelopes
that motivated this are why: they were refused by `event_session_id_fkey` because five real
pre-flip sessions have no `brain.session` row, and that refusal stops the moment those sessions
are reconciled. A classifier would have written a judgement into the store as a fact.

**`failed` is terminal for the drain and for nothing else.** No row is deleted and no payload
is dropped. `requeue` returns a retired envelope to `pending` with `attempts` reset and
`requeues` incremented — so the reset does not erase its own evidence — and it emits with the
`occurred_at` it was parked with. Requeue moves `failed` rows only: re-emitting something that
already reached the bus is a different act and does not share a verb with retrying something
that never did.

#### The five sessions were reconciled and the seven emitted (task 0311, 2026-08-17)

The paragraph above is written in the future tense because when 0302 landed the call had not
been made. It has been: the five were written into `brain.session` (1220 rows → 1225) and the
seven originals requeued and drained, landing as `event_seq` 321–327 with `occurred_at` values
of `2026-08-16 18:28:38.124678+00` through `18:48:06.165455+00`. `ingest events list --status
failed` returns `count: 0` on both profiles.

**Reconciling was chosen over documenting the gap because these were first-hand observations,
not a reconstruction.** All five carry `registration_source='hook'` and
`time_source='hook-observed'` in `d3_scratch.ingest.session`, so a hook watched each one start;
moving them is a store-to-store copy of an observation. That is why "Backfill emits nothing, on
purpose" above does not bite here — that rule refuses replaying 1123 starts the fabric never
saw, while these seven envelopes were parked live, at the instant, by the producer.

**The sub-shape run was A2: register with `emit_event=False`, then requeue the originals.** The
alternative — register with `emit_event=True` and let the five new `session.started` envelopes
be the events — loses two real observed ends, because no branch of the register path produces
`session.ended` and two of the five have one (`seq` 4 and 8). The two `session.ended` were
written through `session end`, which owns `ended_at`; the three sessions whose end nobody
observed are still `ended_at IS NULL` and must stay that way.

**Two things about those seven rows that a reader should not have to rediscover.**
`brain.event.actor_type` is NULL on all seven while `brain.session.actor_type` says `hybrid`,
because the envelopes were parked on 2026-08-16 and `actor_type` did not become an envelope key
until 0300 on 2026-08-17. That is the contract's own "not recorded" and it is literally true of
the producer that built them; hand-editing seven parked payloads to insert a value the producer
never wrote is the same second-assertion mistake that put 150 wrong `ai` rows on live. And all
seven are `retention_class='machine'`, so `brain.sweep_events()` removes them once `occurred_at`
passes 14 days — **from 2026-08-30**. The durable record is the `session` row, which does not
expire; the events exist so a subscriber can react, and these are a day late already.

The script that did the write is `ingest/tools/reconcile-preflip-sessions`. It is a one-off, it
pins the five keys and their `started_at` and refuses if the source has moved since, and it is
kept because "which rows were written, from what, and what was deliberately not written" is not
answerable from the resulting table.

### The three answers D5 owed this file, now that both sides exist

1. **The invocation is `<cmd> --json -`, the envelope on stdin, non-zero on failure**, exactly
   as assumed here. Nothing on the box implemented it until 0287: `fabric.cli emit` is
   flag-based. `fabric/bin/event-emit` is the surface that takes the envelope.
2. **`session.started` and `session.ended` are registered**, in `fabric/types.py`, both
   `machine` class (14 days). An unregistered type is refused at emit time.
3. **A refused envelope stays pending and never blocks the session.** The refusal is written
   to `emit_detail` and the next drain retries it. There is no back-pressure, deliberately: a
   store that will not take an event must not turn a landed session into a failed one. After
   `BRAIN_EVENT_MAX_ATTEMPTS` refusals it is retired to `failed` rather than retried forever —
   see the section above; that is still not back-pressure, because the session never sees it.

### Why emission is after the commit and not inside it

`brain.event.session_id` is a foreign key to `brain.session(id)`, `event emit` runs in its own
transaction as `brain_producer`, and during `session register` this lane's session row is
uncommitted and invisible to it. Measured on live `brain`:

```
ERROR:  insert or update on table "event" violates foreign key constraint "event_session_id_fkey"
DETAIL: Key (session_id)=(...) is not present in table "session".
```

So an inline emit had two possible outcomes, fail or drop the `session_id` link, and the link
is the entire reason the column exists. The park stays inside the transaction, which keeps the
property that matters: a rolled-back session parks nothing and can never emit a phantom.

### `produced_by` is not `produced_by`

The envelope's `produced_by` is `d3-ingest`, a component name. Since migration 17 the store
CHECKs that `brain.event.produced_by <> 'd3-ingest'`, because that column means an entity id
and nothing else. `fabric/bin/event-emit` maps the envelope field onto
`produced_by_producer`. A consumer reading lineage should expect `produced_by` NULL and
`resolution_status` NULL on these events: nobody asked, and that is the honest answer.
