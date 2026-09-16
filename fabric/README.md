# The event fabric

> *"A row in an events table is the record; a notification carrying only the event id is the
> doorbell; the listener reads the row and acts."*

The problem it solves, in the buildout project's own words: today a producer that wants to trigger
an n8n flow must know that flow's webhook URL, and a failure at 02:00 waits for the 07:00 rollup if
it surfaces at all.

**Producers never know their consumers.** The moment a producer holds a webhook URL, the fabric has
failed at its only job.

## Run it

```bash
python3 -m fabric.demonstrate                   # every claim below, checked against the live store
python3 -m fabric.cli verbs                     # every transition and the role it runs as
python3 -m fabric.cli health                    # liveness AND lag, because they differ
python3 -m fabric.cli types                     # the registered event types
python3 -m subscribers.operator_paging --once   # drain, page, and retract
python3 -m fabric.watch --interval 60           # the lag watcher. A producer, NOT a listener.
python3 subscribers/tests/test_paging_retraction.py   # a page that stops being true is taken back
fabric/bin/event-emit --json -                  # event emit, taking a producer's whole envelope
```

`fabric/bin/event-emit` is the second surface onto `event emit` and it exists because the first
one is the wrong shape for a producer that holds an envelope rather than flags. It is what
`BRAIN_EVENT_EMIT` points at, and until task 0287 wrote it there was nothing on this box for
that variable to name: 133 session envelopes were parked in `ingest.event_outbox` behind it and
`session.ended` had never once reached the bus. It maps `ingest/docs/EVENT-CONTRACT.md`'s seven
keys onto `producers/sessions.py` and holds no SQL of its own.

`engine/tests/run-all.sh` runs the engine lane's suites and not this one, so the paging suite is
named here rather than left to be discovered. It needs the scratch store provisioned for this
subscriber, which is not the default and fails with `no CONNECT privilege` until it is done:

```bash
store/bin/provision-subscriber.sh --db brain_scratch --subscriber operator-paging
```

## The EF decisions, and the code that implements each one

Every row cites a decision from
`swarms/Sprints/2026-08-04-event-fabric-substrate/waves/BLOCKERS-AND-DECISIONS.md`, all seven
Tier-1 items accepted by the operator on 2026-08-05, none rejected and none amended.

| ID | What was accepted | Where it lives now | How it is enforced |
|---|---|---|---|
| **EF-1** | One record per inbound item on the runtime plane; 14/90/400 day retention; 4096-byte ceiling; 5,000/day | `types.py` (`EventType.retention_class`, `DAILY_EVENT_BUDGET`), `emit.py:_event_emit`, `emit.py:_check_budget` | The type sets the retention class, not the producer. The ceiling **REJECTS** at 4097 bytes (`PayloadTooLarge`) and never truncates. The daily budget is a governor, not a gate, and `_check_budget`'s docstring says why: the producer has no SELECT so it cannot count its own writes |
| **EF-2** | The two-record path (event + observation) is the normal path | `emit.py:_observation_open`, CLI `observe` | A separate verb, because `event` is `producer`-only and `observation` is `runtime`-only. One transaction cannot span both roles; the docstring records that as a consequence of EF-11 rather than a shortcut |
| **EF-3** | `event_id` REQUIRED on `disposition`, `observation_id` NULLABLE | `emit.py:_disposition_record` | The signature raises before it reaches D1's `NOT NULL`, so the error names the rule instead of the constraint. Demonstrated both ways |
| **EF-4** | A fourth receipt-booking moment: consequential-event-emitted | `types.py` (`books_receipt`), `emit.py:emit` returns `receipt_due`, `lag.py:receipts_due` | The emitter is TOLD a receipt is now due, and the gap is queryable. Booking is `receipt book`, which is D2's verb: this lane reports the obligation and does not reimplement someone else's transition |
| **EF-5** | `caused_by_event_id` and `approval_ref` on `receipt` | D1's schema; `lag.py:receipts_due` is the other half of the join | The layer-3 breach detector is a join on those two fields. This is the query that finds consequential events with no receipt pointing at them |
| **EF-6** | A subscriber declares before it runs, in `departments/SUBSCRIBERS.md` | `listener.py:load_declaration`, `departments/SUBSCRIBERS.md` | The listener READS git at start: no entry, no start. `Status` not `live`, no start. `effect` without `gated`, no start. The commit is stamped on the cursor. Declare, never discover |
| **EF-7** | Hard flags inherit by OR; a derived item never lowers either flag | `emit.py:inherit`, `producers/questions.py:_task_flags` | A function, not a convention, because two compliant hops defeat a one-hop gate. Verified against the live bus: task `0099` reads `external=True` and a question raised from it is flagged. An unknown task reads `(True, True)`, conservative, because the two mistakes are not symmetrical |
| **EF-23** | `declaration_commit` and `quarantined_at` on `subscriber_cursor` | `emit.py:_event_ack`, `emit.py:_subscriber_quarantine` | `event ack` carries `WHERE quarantined_at IS NULL`, so a quarantine is a mechanism and not a label |
| **EF-15** | The event type registry needs a home; Tier 3, undecided | `types.py` | The runtime registry, deliberately small, with the undecided part named in its docstring. When EF-15 lands, this table loads from that file and no caller changes |

## The four properties that are structural, not intentional

**1. A producer cannot read the bus.** `brain_producer` holds INSERT on `event` and nothing else,
so `INSERT ... RETURNING` fails. `event emit` supplies its own `event_id` uuid and hands that back.
The caller learns the uuid it chose; it never learns the sequence number and has no business
knowing one.

**2. A subscriber cannot write the bus, and this is re-proved at every listener start.** Not at
grant time. From inside the running process, on a connection it opens with its own credentials,
deliberately read-WRITE so the refusal comes from the GRANT (`42501`) and not from a session flag
the process set on itself. A `25006 read_only_sql_transaction` is treated as an inconclusive test
and also refuses the start.

```
event=gate_verified  subscriber=operator-paging
  INSERT=42501 permission denied for table event
  UPDATE=42501 permission denied for table event
  DELETE=42501 permission denied for table event
```

**3. A gated subscriber never holds a flagged payload.** The blanking is a `CASE` in the `SELECT`
(`listener.py:read_batch`), so the bytes do not cross the process boundary. Blanking after the
fetch would make the gate a promise about what the code does with data it already has.

**4. A listener can stop itself and cannot start itself.** `subscriber quarantine` runs as
`subscriber`; `subscriber release` runs as `runtime`. The RLS policy `cursor_admin` is
`owner`/`runtime` only, so there is no path from inside a listener to clear its own quarantine
however it is written.

**5. A listener cannot say who it is.** *Added by migration 10 on 2026-08-16, and it is a
correction rather than an addition.* The policy on `subscriber_cursor` keyed on
`current_setting('brain.subscriber')` — a GUC the client sets — and every listener logged in as the
one `brain_subscriber` role. Measured: one listener called `set_config` and moved another's cursor
from 10 to 4242, which makes that listener skip 4,232 events while running perfectly. The policy
now keys on `session_user` through `brain.subscriber_role`, a table no listener may read or write,
and each listener holds its own login (`store/bin/provision-subscriber.sh`). `event ack` asks
`brain.current_subscriber()` who the connection is and refuses if that is not the subscriber it was
called for. **If a subscriber can name itself, it has no identity.**

## The question producer's call site, and why it is not in a CLI (task 0140)

This producer was written against the FILE bus while the engine was written against Postgres, so
for one day `swarm ask` wrote a `brain.question` row, printed one line on stderr, and emitted
nothing. Measured by D9 with this subscriber listening live: `max(event_seq)=54` before `q0028`
and 54 after. Every `question.raised` event that existed at that point had come from this
module's own file-bus demo. Neither lane was wrong; the join between them had no owner.

Two things closed it, and only the first is the obvious one:

1. **`producers/questions.py` reads `brain.question` first**, falling back to
   `$SWARM_HOME/operator/{open,answered}/<qid>.json`. The file-bus reads are KEPT: swarm-admiral
   is still the running fleet and its `notify.sh` still calls `fabric.cli notify-hook`, so a
   producer that could only read Postgres would fix tonight's fleet by breaking the one that is
   currently answering the operator. The flags are read from the question's OWN bus and never
   the other one, because file-bus task `0140` and store `work_item 0140` are different items and
   a cross-bus fallback would answer a safety question with a stranger's flags.
2. **The call site is `store.transitions.after_commit("ask", ...)`**, not a line in the CLI. A
   call in `cmd_ask` would page the CLI's questions and silently not the MCP server's, not the
   console's, and not the one `fail` raises for itself when a task runs out of attempts -- which
   is the 02:00 case this producer exists for. Every surface goes through `store.apply`, so the
   join lives there once. `after_commit` also documents why the alternative -- emitting inside
   the `ask` transaction -- is not available: `ask` runs as `brain_runtime`, and property 1 above
   is exactly the grant that forbids it.

`question.answered` is wired the same way, so a pager can retract rather than re-page.

**And one consumer does (task 0162).** Until then that sentence was true of the producer and false
of everything downstream: `subscribers/operator_paging/policy.py` had `question.answered` on its
never-page list, so the one event that can cancel a page was the one event the pager threw away.
Measured on the scratch store -- one `ask`, one `answer` -- `handled=2 paged=1 never_page=1`, and
the never-paged one was the answer. `operator-paging` now cancels the page it sent for that
question and stays silent when it has no page outstanding, which is what keeps a retraction from
becoming the machine volume the never-page list exists to keep off the channel.

## Health is lag, not liveness

`pg_isready` proves the server is up and proves nothing about whether anything is listening. A
listener that is running, connected and stuck reports healthy under every process-level check, and
listeners bind no port, so the port registry's rules do not reach them at all.

```sql
max(event_seq) - subscriber_cursor.last_seq
```

**One correction to that formula, found by running it.** It counts events the subscriber never
declared an interest in, so any subscriber consuming a subset of types shows permanent non-zero lag
with nothing wrong. `drain()` advances the cursor to the head it has EXAMINED once nothing
consumable is left. Safe because `read_batch` reads `head_seq` before the rows, so head can only be
behind what was examined, and `event ack` takes `GREATEST` so a cursor never moves backwards.

**A second correction, and it is the more important one: an impossible value is a finding, not a
reading.** `lag.py` tested `lag >= WARN` and nothing else, so a cursor poisoned past the head of the
bus produced `lag = -4241` and the module reported `state=ok`, verdict *keeping up*. That is worse
than the poisoning it was reporting on, because a lost event is a lost event and this told the
operator nothing was wrong. Three conditions are now checked first, are CRITICAL whatever the
number says, and quarantine the subscriber (`fabric.lag.enforce`, verb
`subscriber quarantine detected`, which runs as `runtime` so no listener can aim it at a rival):

| condition | column | why it cannot be true |
|---|---|---|
| negative lag | `lag < 0` | a subscriber cannot be ahead of the bus it reads |
| cursor past the head | `ahead_of_head` | it has acknowledged events that do not exist |
| cursor moved backwards | `moved_backwards` | `event ack` takes `GREATEST`; a lower `last_seq` than `high_water_seq` was written around the verb |

The third is the sharp one: a cursor walked from 3 back to 1 reads `lag = 2`, positive and well
under `LAG_WARN`, and is still CRITICAL. `worst_lag` is computed over readings that can be true,
because `max()` across a `-4241` makes the worst number the smallest one.

## Quarantine

- **Caused by:** three consecutive handler failures on the same `event_seq` (a poison event), or an
  explicit `fabric quarantine`. A page that no transport delivered raises, so a pager that cannot
  page stops and is seen to have stopped rather than acking into a channel nobody receives.
- **Does:** the cursor stops. Even a correct `event ack` is refused. The listener refuses to start.
  Lag climbs and keeps climbing, which is the one signal an operator cannot mistake for idleness.
- **Cleared by:** `python3 -m fabric.cli release --subscriber X --by <human>` and nothing else.

## The doorbell

Migration 1 has no trigger and no `pg_notify` (checked, not assumed), and migrations are D1's. So
`event emit` rings `pg_notify('brain_event', <uuid>)` in the same transaction as the INSERT, which
is better than a trigger for one reason anyway: it cannot fire for a row this verb did not write.

**A listener does not rely on it.** Catch-up is by `event_seq > cursor`, so a dropped notification
costs latency and never costs an event. A notification carries no delivery guarantee and building
as though it did is how a fabric silently loses its first event.

## What this lane deliberately did not build

- **A second backlog.** Events say what happened. The single work queue is D4's `work_item`, and a
  recommendation is a proposal rather than a queue entry.
- **The retention sweep.** `brain.sweep_events()` is `owner`'s, revoked from PUBLIC, and this lane
  has no business executing it.
- **`pipeline-failure-observer`.** Its evidence falsifier passed and its producer falsifier did
  not. See `FALSIFIER.md`.
- **A second write path to anything.** `store` exports no `execute`, `insert`, `connect` or
  `commit`, and nothing here reaches around that.
