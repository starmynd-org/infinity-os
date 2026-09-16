# The MCP server

Nine tools, each a thin wrapper over a verb. stdio, no port.

```bash
./mcp/bin/mcp-server            # speaks JSON-RPC on stdin/stdout
```

Register it with Claude Code:

```json
{"mcpServers": {"runtime": {
  "command": "/abs/path/to/infinity-os/mcp/bin/mcp-server",
  "env": {"SWARM_AGENT": "T7"}}}}
```

## The nine

| Tool | Verb it wraps | Owner |
|---|---|---|
| `read_queue` | read only, `store.reads.queue` | — |
| `read_recommendations` | read only | — |
| `emit_event` | `event emit` | D5 |
| `claim_work` | `claim` | D4 |
| `report_progress` | `heartbeat`, plus `note` when there is one | D4 |
| `finish_work` | `done` / `block` / `fail`, chosen by `outcome` | D4 |
| `raise_question` | `ask` | D4 |
| `book_receipt` | `receipt book` | **D2, and not a store transition** |
| `resolve_entity` | `entity resolve` | D2 |

**If a tool contains logic that is not in a verb, the waist is broken** and the console and the
MCP server drift into two subtly different systems. Every handler validates argument names, calls
`store.apply(verb, ...)` or a read, and returns what came back.

That is checked, not claimed: `test_every_write_tool_calls_its_declared_verb_and_no_other`
monkeypatches `store.apply`, calls each write tool, and asserts it entered exactly one transition
and that it was the declared one.

## What is deliberately absent

- **No generic run-a-command tool.** A `run_verb(name, kwargs)` would technically be a thin
  wrapper and would hand an agent the whole surface from one call site nobody reviews.
- **Nothing touching config, `permission_mode`, or `bypassPermissions`.** `permission_mode` is
  load-bearing: `acceptEdits` produces confident unverified work, and `auto` is what makes an
  agent's self-report trustworthy. An agent that could change it could change the thing its own
  trustworthiness rests on. Refused by name in `tools.FORBIDDEN`, at the door, on every call.
- **No `accept work`.** Acceptance is the human's act. An MCP tool for it is self-approval one
  call removed.
- **No `answer` and no `reopen`.** Those are the operator's verbs. An agent answering its own
  question is the actor forgery the whole prohibition is about.

## One seam, named

`book_receipt` wraps D2's `receipt book`, which is **not** a registered store transition: booking
a receipt is a git commit through the promotion door, not a Postgres row. Rather than write a
second implementation, the tool refuses and says which lane owns it and that the adapter-to-store
join is task 0103.

## The bug a real session found, and the test that now catches it

Transition registration is an import side effect. This module did not import the lanes, so the
**server process** had an empty registry: every read tool worked and every write tool failed with
`no transition named 'ask'. Registered: (none registered)`. Every unit test passed, because a test
module that imports the lanes in order to enumerate them has already registered them — the test
process is never the server process.

`test_the_server_process_can_write` runs the server as a subprocess and makes it write. Any lane
shipping a second entry point has this hole.

```bash
BRAIN_PG_DB=brain_console python3 -m mcp.tests.test_tools     # 12
```
