# How Infinity OS works

**Git holds what is TRUE. Postgres holds what is HAPPENING. Neither owns the other's job.**

Infinity OS is the Postgres half: one self-hosted
service that stores sessions, events, dispositions, recommendations, work items, runs and
receipts; dispatches work to stateless one-shot agent processes; ranks what needs a human; and
shows that queue in a console. It holds no knowledge, and it never approves its own work.

## Start here, by who you are

| If you are | Read |
|---|---|
| new to this and want to know what it is | this file, then `INSTALL.md` |
| running it today | `docs/OPERATING.md` |
| trying to remember why something is the way it is | `docs/WHY-IT-IS-LIKE-THIS.md` |
| about to change it | `docs/CHANGING-IT.md` |
| about to trust a claim in here | `docs/KNOWN-GAPS.md` |

## The problem it solves

A brain that holds what is true cannot, by itself, notice that something happened, decide what to
do about it, do it, and find out whether it worked. This closes that loop without becoming a
second place where truth lives:

```text
sense    -> event           something happened
orient   -> disposition     what class, whose department, which flags
decide   -> recommendation  what to do. A proposal, never self-executing
act      -> work item       one queue, dispatched to a stateless worker
measure  -> run + receipt   what it cost, what it changed, what caused it
learn    -> promotion       a rule, playbook or canon revision, human-accepted
```

Every row on that path carries `produced_by`, a stable brain entity id, so the chain from a
finished piece of work back to the git object that caused it can be walked without a human reading
prose at any hop.

## The four planes

| Plane | Owns | Store | Authority |
|---|---|---|---|
| Truth | canon, doctrine, entity definitions, the planning ladder, receipts | git | authoritative |
| Runtime | sessions, events, dispositions, recommendations, work, runs, queue | Postgres | authoritative for workflow position, never for meaning |
| Render | the console | none | owns nothing durable |
| Agent-runtime | stateless one-shot worker processes | none | replaceable harness |

**The invariant that keeps this honest:** losing this entire store must cost live queue position
and history, and **zero knowledge**. Canon, decisions and receipts recover from git alone. That is
checked by dropping a scratch database and recovering the real receipt corpus from git
(`store/bin/brain-drop-test.sh`), never by asserting it.

## The one property everything else follows from

**One transition function per state change, exposed as a verb, called by every surface.** No
surface reimplements a transition and no integration earns a private path. The CLI, the console,
the MCP server and a cron job all call `store.apply("done", ...)` and reach the same code.

A shared module is a library, not a single writer, so this is enforced structurally rather than by
convention:

* `store.read()` yields a session whose transaction Postgres has put in `SET TRANSACTION READ
  ONLY`. Reads can be exposed as widely as any lane wants, because a caller who issues an INSERT
  through a read session is refused by the database. There is no flag that turns it off, and the
  suite asserts the absence of one.
* `store` exports no `execute`, `insert`, `update`, `cursor`, `connect` or `commit`. A writable
  connection exists only inside `apply()`.
* `@transition("done")` registers a name once. Registering it twice raises at import time.
* Each transition declares the database role it runs as, and the grants do not trust the
  registration: a transition registered under `runtime` cannot insert an event however it is
  written, because `brain_runtime` holds no INSERT on `event`.

The argument in full, with the proof it runs, is `store/narrow_waist.md`.

## What sits on top of it

| Surface | Entry point | Notes |
|---|---|---|
| CLI | `engine/bin/swarm` | 51 subcommands today. Two of them, `admin` and `routine`, are verb groups |
| Console | `web/bin/console`, `http://127.0.0.1:3103` | one shell, five rooms, one write door. Loopback only |
| MCP | `mcp/bin/mcp-server` | nine tools, stdio, no port. Agents participate natively |
| Supervision | `systemd/` | `--user` units for the store, the console, the paging listener, the health sweep |
| Session hooks | `ingest/` | a session registers itself at start; nothing depends on an agent remembering |

Measured against the live console on 2026-08-27: 68 registered transitions, 25 of them reachable
from a console room and 43 reachable from no room. `GET /api/audit` prints that table on demand.

## What lives where

| Path | Holds |
|---|---|
| `migrations/` `store/` | the schema ledger, the five Postgres roles, the store module and the narrow waist |
| `engine/` `roles/` | the verbs, dispatch, the claim protocol, the runner, supervision, the role briefs agents are handed |
| `queue/` `budget/` | the human queue: tiers, ranking, defer, recommendations, the stopwatch, and the spend brake |
| `adapter/` | entity resolution, brief rendering, receipt booking. The component that makes the runtime brain-aware |
| `ingest/` | session registration and the transcript index. A pointer and a hash, never the blob |
| `fabric/` `subscribers/` | the event outbox and subscriber dispatch. Producers never know their consumers |
| `web/` `mcp/` | the console and the MCP server |
| `voice/` | record, transcribe, land it as a normal prompt |
| `systemd/` | supervision for the live services, plus the routine clock |
| `docs/` | the rules that are not negotiable, and why |

No lane reads another lane's working tree.

## What is deliberately not built

`web/MUST-NOT-BUILD.md` holds eleven prohibitions, each stated, then what was actually built, then
how that was checked. All eleven were reviewed on 2026-08-27 and kept with **zero amendments**.
The four most likely to creep back: progress bars outside the run-the-stack pips, a freeform notes
field on a queue item, unread counts and red badges, and anything that interrupts deep work.

Read that file before proposing a console feature. The answer to "why can't I just" is usually in
it, and it is usually an incident somebody already had.

