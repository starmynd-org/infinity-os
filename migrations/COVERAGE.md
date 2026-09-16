# Verb-to-table coverage checklist

Built **before** migration 1, from the live system, on 2026-08-16. Method, so it can be
re-run rather than trusted:

```bash
internal/swarm-admiral/bin/swarm --help          # the verb list
internal/swarm-admiral/bin/swarm <verb> --help   # x40, what each verb reads and writes
ls -1 ~/.swarm/                                  # every directory is state that needs a home
grep -n 'home() /' internal/swarm-admiral/bin/swarm   # every path the CLI actually writes
```

The last line is the one that matters. `--help` tells you a verb exists; only the write sites
tell you what it puts on disk. Six of the homes below are invisible from `--help` alone.

`swarm --help` lists exactly **40** verbs, counted: init, post, claim, done, block, fail, ask,
reopen, objectives, accept, intake, config, state, tick, doctor, set, answer, reanswer, questions,
msg, inbox, note, heartbeat, reap, ls, show, artifact, artifacts, signals, why, feed, status,
board, brief, paused, stop, start, pause, resume, cancel.

## The 40 dispatch verbs

R = reads, W = writes.

| # | Verb | State it touches today | Table(s) |
|---|---|---|---|
| 1 | `init` | creates `seq/ objectives/{inbox,accepted} threads/ artifacts/ messages/ operator/{open,answered} agents/ runs/ logs/` | none at runtime; this verb **becomes the migration** |
| 2 | `post` | W `tasks/inbox/<id>-*.md` (29 fields), W `seq/<n>`, W `threads/<id>.jsonl` (post event, plus the GATE note when a hard flag is set) | `work_item`, `item_id_seq`, `thread` |
| 3 | `claim` | R `PAUSE`, R `agents/<a>.STOP`, R every task, W rename `tasks/inbox → tasks/active`, W `claimed_by` `claimed_at` | `work_item` (`SELECT … FOR UPDATE SKIP LOCKED`), `agent`, `runtime_flag`, `thread` |
| 4 | `done` | W `result` `finished_at` `state`, W thread `done` event | `work_item`, `thread` |
| 5 | `block` | W `result` `finished_at` `state`, W thread `block` event | `work_item`, `thread` |
| 6 | `fail` | W `attempts`, requeue **or** block-and-raise-a-question, W thread | `work_item`, `thread`, `question` |
| 7 | `ask` | W `operator/open/q<id>.json`, W task → blocked with `blocked_on=<qid>`, W thread `ask`, fires `notify.sh` | `question`, `work_item`, `thread` |
| 8 | `reopen` | W done → inbox, W thread `reopen` | `work_item`, `thread` |
| 9 | `objectives` | R `objectives/inbox/*` | `objective` |
| 10 | `accept` | W rename `objectives/inbox → objectives/accepted` | `objective` |
| 11 | `intake` | W copies into `objectives/inbox`, W `objectives/.intake-seen.json` | `objective`, and **`objective.source_name` + `objective.source_signature`** |
| 12 | `config` | R `config.json` | **no table.** See "config" below |
| 13 | `state` | R one task | `work_item` |
| 14 | `tick` | R `objectives/inbox`, R tasks, W `agents/<a>.tick` (`fingerprint`, `at`) | `objective`, `work_item`, and **`agent.tick_fingerprint` + `agent.tick_at`** |
| 15 | `doctor` | R `agents/*.json`, R tasks, R `operator/open` | `agent`, `work_item`, `question` |
| 16 | `set` | W one frontmatter field | `work_item` |
| 17 | `answer` | W `operator/open → operator/answered` + `answer` `answered_at`, W `messages/<planner>.jsonl`, W thread, W requeue a blocked task | `question`, `message`, `thread`, `work_item` |
| 18 | `reanswer` | W `operator/answered` amend + `amended_from`, W messages to every planner, W thread | `question`, `message`, `thread` |
| 19 | `questions` | R `operator/open` and `operator/answered` | `question` |
| 20 | `msg` | W `messages/<to>.jsonl`, W thread `msg` when `--task` | `message`, `thread` |
| 21 | `inbox` | R `messages/<a>.jsonl`, W `messages/<a>.read` | `message`, and **`agent.inbox_read_seq`** |
| 22 | `note` | W `threads/<id>.jsonl` | `thread` |
| 23 | `heartbeat` | W `agents/<a>.json` (`name role status task pid host updated`) | `agent` |
| 24 | `reap` | R agents, R active tasks, W requeue | `agent`, `work_item`, `thread` |
| 25 | `ls` | R tasks | `work_item` |
| 26 | `show` | R task, thread, artifacts; `--full` R `runs/<id>-attempt<N>.stream.jsonl` | `work_item`, `thread`, `artifact`, **`run`** |
| 27 | `artifact` | W `artifacts/<id>.jsonl`, W `work_item.artifacts` summary, W thread on a missing path | `artifact`, `work_item`, `thread` |
| 28 | `artifacts` | R `artifacts/*.jsonl` | `artifact` |
| 29 | `signals` | R task's nine signals **and the whole parent chain** | `work_item` (recursive over `parent`) |
| 30 | `why` | R task, R queue score | `work_item` |
| 31 | `feed` | R `threads/*` ∪ `messages/*` ∪ `operator/{open,answered}` | `thread`, `message`, `question` — a **view**, `brain.feed` |
| 32 | `status` | R task counts, R agents, R `PAUSE`, R open question count | `work_item`, `agent`, **`runtime_flag`**, `question` |
| 33 | `board` | R everything | every read table |
| 34 | `brief` | R tasks, threads, artifacts, questions since N hours | `work_item`, `thread`, `artifact`, `question` |
| 35 | `paused` | R `PAUSE`, R `agents/<a>.STOP` | **`runtime_flag`**, **`agent.stopped`** |
| 36 | `stop` | W `agents/<a>.STOP` | **`agent.stopped`** |
| 37 | `start` | W removes `agents/<a>.STOP` | **`agent.stopped`** |
| 38 | `pause` | W `PAUSE` | **`runtime_flag`** |
| 39 | `resume` | W removes `PAUSE` | **`runtime_flag`** |
| 40 | `cancel` | W task → cancelled + reason, W thread | `work_item`, `thread` |

## The subsystem verbs D00 adds

| Verb | Owning lane | Table(s) |
|---|---|---|
| `session register` / `session end` / `session link-parent` | D3 | `session` |
| `transcript index` / `transcript verify` | D3 | `transcript` |
| `event emit` / `event ack` | D5 | `event`, `subscriber_cursor` |
| `disposition record` | D5 | `disposition`, `observation` |
| `recommend` / `recommend accept` / `recommend reject` | D6 | `recommendation` |
| `budget set` / `budget status` / `budget stop` | D6a | `runtime_flag` (see below) |
| `receipt book` / `touch add` / `entity resolve` | D2 | `receipt`, `touch` |

## What the enumeration found that the frozen sixteen do not name

This is the part the checklist exists for. Each row is a directory or file in `~/.swarm` with a
verb writing to it and no table in `D00`'s frozen sixteen.

| Found | Verbs stranded without it | Resolution in migration 1 |
|---|---|---|
| `PAUSE` — one **fleet-wide** switch | `pause`, `resume`, `paused`, `claim`, `tick`, `status` | **New table `runtime_flag`.** It is fleet state, not per-agent, so it cannot be a column on `agent`. Six verbs read or write it. |
| `agents/<a>.STOP` — per-agent kill switch | `stop`, `start`, `paused`, `claim`, `tick`, `status`, `board` | column `agent.stopped_at` |
| `agents/<a>.tick` — planner wake fingerprint | `tick` | columns `agent.tick_fingerprint`, `agent.tick_at` |
| `messages/<a>.read` — mailbox read pointer | `inbox` | column `agent.inbox_read_seq` |
| `objectives/.intake-seen.json` — intake dedup | `intake` | columns `objective.source_name`, `objective.source_signature` |
| `seq/<n>` — the id allocator, shared by tasks **and** questions | `post`, `ask`, `fail` | **one** sequence `brain.item_id_seq`, drawn by `work_item` and `question` alike |

`runtime_flag` is a **seventeenth table beyond the frozen sixteen**, and it is additive rather
than contradictory: all sixteen frozen tables exist unchanged. Two more are required by documents
`D00` itself points at rather than discovered here — `touch` (`D00`, "Lineage: a touch table, not
just a column") and `event_rollup` (the operator-accepted fabric design's owned-runtime-state
list, and the reason `D00`'s own retention line has a 400-day tier at all). **Nineteen tables.**

`budget set/status/stop` (D6a) has no table in the frozen sixteen either. It lands on
`runtime_flag`, which is the right shape for it: a small set of named fleet-level switches with a
value, a setter and a timestamp. D6a should confirm that fits before building against it.

### config, deliberately not a table

`swarm config` only reads. `config.json` is the operator's file, resolves `{**defaults, **agent}`,
and **passes unknown keys through** — the tolerance that let `config_dirs` ship with no CLI change.
Putting it in a table would either freeze that key set or reimplement JSON in columns. It stays a
file. The keys that must survive the port, read off the live `~/.swarm/config.json`:
`permission_mode`, `config_dirs`, `add_dirs`, `lanes`, `plans`, `model`, `engine_args`,
`allowed_tools`, `task_timeout`, `interval`, `effort`, fleet name.

Measured correction for `D00`: **the `signals` weight block is not in `~/.swarm/config.json`.**
`w_urgency w_dependency_unblocking w_charter_alignment w_stakes w_effort w_age_per_day
gate_low_confidence` exist only as `DEFAULT_SIGNAL_WEIGHTS` at `bin/swarm:572` and as a
`DEFAULT_CONFIG` block at `bin/swarm:136`, and the live file overrides none of them. Porting
"the weight block from config" would port a block that is not there. It is a code default today
and the port must carry it as one, overridable by the same key if the operator ever writes it.

### Two more homes that are files today and stay files

- `~/.swarm/logs/` — runner and fleet logs. Runtime plane, no table.
- `~/.swarm/operator/notifications.log` — `notify.sh` output. Runtime plane, no table.
- `~/.swarm/runs/<id>-attempt<N>.stream.jsonl` — the run transcripts. These **do** get a table
  (`run`), holding the pointer and the metadata; the stream itself stays a file, same
  pointer-and-hash posture as `transcript`. `show --full`'s forensic recovery reads the file
  through the pointer.

## Text is never cut

Two write paths destroy text today and neither can be un-cut, because the full string is never on
disk in either place:

- `bin/swarm:_finish()` — `task.meta["result"] = text.replace("\n"," ")[:2000]`, a hard cut with
  no marker, so it reads as a finished sentence.
- `bin/swarm:append_line()` — a thread line over 4000 bytes keeps `text[:3000]` plus
  `[truncated by swarm]`.

Migration 1 ports `docs/0037-result-spill.patch`'s semantics, not this behaviour: `work_item.result`
and `thread.text` are `text` with no limit, and rendering is where anything gets shortened. A
`text` column has no reason to cut anything.
