# Operating it

For the person running this day to day. Every path here is absolute. Every command was run on this
host on 2026-08-27 unless it is marked otherwise.

Run Python files with `python3`, never `bash`. `bash engine/bin/swarm --help` produces forty lines
of shell syntax errors that look like a broken repo and are nothing of the kind.

```bash
export REPO=/mnt/c/Users/you/repos/infinity-os
cd $REPO
```

## Where everything is

| Thing | Where | Check it |
|---|---|---|
| the store | Postgres 5432 in docker container `brain-postgres`, data at `~/.brain-postgres` | `docker exec brain-postgres pg_isready` |
| live database | `brain` | `BRAIN_PG_DB=brain python3 engine/bin/swarm status` |
| the console | `http://127.0.0.1:3103`, loopback only | `curl -s http://127.0.0.1:3103/api/health` |
| secrets | one 0600 file per reference in `~/.brain-postgres-secrets`, 0700 | `python3 store/bin/secret-preflight.py --db brain` |
| supervision | `systemd --user`, no sudo | `systemctl --user list-units 'brain-*'` |
| backups | `~/.brain-postgres/backups`, 30 days | `store/bin/brain-postgres-backup.sh list` |

There is no sudo path here on purpose: sudo on this host needs a password no agent may enter, so
supervision is `systemd --user` and nothing else.

## Starting it

Normally you start nothing. The units come up with the session:

```bash
systemctl --user list-units 'brain-*'
systemctl --user restart brain-console.service      # after a code change to web/
```

By hand, for a store that is not `brain`:

```bash
BRAIN_PG_DB=brain_demo $REPO/web/bin/console         # prints the bind it chose, then serves
```

**If WSL has just restarted**, wait about 200 seconds and run this **before anything else starts a
unit by hand**, because a hand start erases the evidence about what the boot did:

```bash
cd $REPO && ./systemd/reboot-test verify
```

### The units, and what each one is for

| Unit | Job |
|---|---|
| `brain-store.service` | Postgres on 5432 |
| `brain-console.service` | the console on 3103 |
| `brain-paging.service` | the operator-paging listener. Binds no port; its health is subscriber lag |
| `brain-health.timer` | every 60s: `pg_isready`, `GET /api/health`, listener lag, with `--repair` |
| `brain-transcript-verify.timer` | daily 03:20: re-hash the whole transcript corpus. Reports, never repairs |
| `brain-routine-tick.timer` | every 5 minutes: `swarm routine tick --fire`, the clock behind `brain.routine` |

Three timers, three different jobs, none substituting for another. Health asks whether the runtime
is **up**. Transcript-verify asks whether data it already recorded is **still true**. Routine-tick
asks whether a slot the operator scheduled has been **crossed**.

**Two of those are not in the state the repo describes, measured 2026-08-27:**
`brain-routine-tick.timer` is in `systemd/install.sh`'s unit list and is **not installed on this
host** (`systemctl --user is-enabled` answers `not-found`); re-run `systemd/install.sh` to install
it. And `brain-transcript-verify.service` is in `failed` state. Neither affects the console or the
store.

## The console: one shell, five rooms, one write door

| Room | For | May write |
|---|---|---|
| **Queue** `/queue?tier=decide\|judge\|shape` | the daily pass. Three tiers, seven items each | 17 verbs, from `answer` to `accept work` to the image verbs |
| **Brief** `/brief` | what happened overnight. Run it first | `answer`, `reopen` |
| **Fleet** `/fleet` | what the agents are doing, and the run feed | 8 verbs registered. **Zero rendered controls today**, row `0400` |
| **Scope** `/scope` | turning an intention into a posted work item | `post`, `intake`, `accept` |
| **Study** `/study` | looking at what happened | **nothing, ever.** Proven by test |

`GET /api/audit` prints that table live, plus every registered verb no room can reach.

Two behaviours worth knowing before you use it:

* **The list is a window and the chip is the total.** Seven a tier. The count under the list says
  how many are below it. Nothing is dropped silently.
* **Nothing vanishes without a receipt.** Every resolution leaves a receipt stripe for seven
  seconds. Where a real inverse verb exists the stripe offers it; where none exists it says so.

### The daily pass

1. `/brief`. Red means damage and nothing else: items that spent every attempt. Amber is waiting on
   you.
2. `/queue?tier=decide`, or press **Run the stack** for the full-screen version: one Decide item at
   a time, resolve and advance. `skip for now` cycles to the back and is called skipping, not
   deferring.
3. `/queue?tier=judge`. This is where **Accept work** and **Send back** live. `done` on the card is
   an agent's report; your acceptance is the separate act.
4. `/queue?tier=shape` for recommendations and anything that needs generating rather than judging.
5. **Deep work** in the shell header removes the fast pane, the burning line and the run strip from
   the markup. Only the count survives. Press it again to come back.

The phone surface is the same URLs at 390px. There is no push notification and there never will be:
the 7am moment is opening the page.

## The CLI

```bash
python3 engine/bin/swarm brief          # what happened overnight. Run this first
python3 engine/bin/swarm status         # fleet state, per agent, with liveness
python3 engine/bin/swarm board          # text dashboard
python3 engine/bin/swarm ls --state inbox
python3 engine/bin/swarm show <id>      # one task and its whole append-only thread
python3 engine/bin/swarm why <id>       # one line explaining its queue position
python3 engine/bin/swarm signals <id>   # its nine signals, and whether it is gated
python3 engine/bin/swarm questions      # what is waiting on you
python3 engine/bin/swarm doctor         # absence detection. Exit 1 on a critical finding
python3 engine/bin/swarm whoami         # which human this process IS, per the database
```

Posting work, with the signals that set its rank. You state facts; you do not rank:

```bash
python3 engine/bin/swarm post --lane exec --title "Draft the Q3 investor update" \
  --posted-by operator --workdir $REPO --for-agents \
  --stakes high --urgency deadline --effort medium --reversibility reversible \
  --confidence 0.8 --charter-alignment high --external false --canon-touching false
```

Accepting finished work, which is the act only you can do:

```bash
python3 engine/bin/swarm accept-work <id>
```

**Do not pass `--by`.** The help text says it defaults to `$USER`; measured 2026-08-27, it does not:
with `--by` omitted the row is accepted as the human the database says this connection is,
`operator`, not `you`. Passing a name that is not this connection's human is refused, and the
refusal names both: *"this call says the acceptor is 'zzz-not-a-person' and the database says the
connection is 'operator'."*

The human queue has its own CLI for the decision layer:

```bash
python3 queue/bin/queue list                  # the ranked queue, a window per tier
python3 queue/bin/queue why <id>              # the additive decomposition of one score
python3 queue/bin/queue recommend "..." --rationale "..." --subject-type work_item \
  --subject-id <id> --produced-by <entity> --by <agent>
python3 queue/bin/queue accept <rid>          # the ONLY path from a recommendation to work
python3 queue/bin/queue acted-on              # this layer's own falsifier, with its denominator
python3 queue/bin/queue doctor                # dead wake conditions, cycles, act-shaped defaults
```

## Routines

A routine is a stored call to `post`, on a clock. The object lives in the store so it can be listed,
disabled and shown to have fired; the clock is a systemd timer.

```bash
python3 engine/bin/swarm routine add standup-check --title "Post the standup check" \
  --lane ops --every 1 --at 00:00 --body "..." --workdir $REPO --by operator
python3 engine/bin/swarm routine list
python3 engine/bin/swarm routine due
python3 engine/bin/swarm routine runs standup-check
python3 engine/bin/swarm routine disable standup-check --reason "the standup moved to Slack"
```

`disable` needs no credential and works from anywhere. `enable` is refused without a human login. A
kill switch that can be refused is not a kill switch.

Live `brain` holds **0 routines and 0 routine runs** as of 2026-08-27.

## Config, and who changed it

```bash
python3 engine/bin/swarm config                      # the resolved values in force
python3 engine/bin/swarm admin config get            # per key, per layer, with provenance
python3 engine/bin/swarm admin config set signals.w_urgency 2.5 --note "why"
python3 engine/bin/swarm admin history --limit 20    # who changed what, when, from what to what
```

Every admin write goes through the same door as a `post`: one transaction, attributed to the
database login rather than to an argument, recorded in `brain.admin_change`.

## When something is stuck

| Symptom | What to run | What it means |
|---|---|---|
| the console 500s on `/queue` | check the queue schema is applied | `engine/bin/scratch-db.sh` applies only `migrations/`; `queue/schema/` is separate. The room refuses to invent an order of its own |
| a task is `blocked` and nothing moves | `swarm show <id>` | it is waiting on an answer. Answer it in `/question/<qid>` or at the CLI |
| you answered and it did not wake | `swarm show <id>`, then `swarm stop --agent X`, then `swarm answer-requeue <id> --from operator` | requeuing behind a live engine is how one task got two agents on 2026-08-16. It is refused, not repeated |
| an agent reads `working` and has not moved | `swarm status` | liveness is a heartbeat plus a process check. `UNKNOWN` is not `DEAD`, and the line says which it is |
| `Accept work` has turned into `Mark my task done` | do not press it | you pressed `Send back`, which clears `claimed_by`. The control renders disabled with the reason. Row `0399`, closed |
| nothing will pick a row up | `swarm set <id> agent_claimable true --as-operator` | it was taken back from the fleet |
| a stopped agent is holding work | `swarm reap --yes` | requeues work owned by a dead agent |
| the whole fleet should stop | `swarm pause` | `swarm resume` restarts it. Per-agent: `swarm stop --agent X` |
| the listener looks fine and is stuck | `subscribers/bin/paging-health` | a listener that is running, connected and stuck reports healthy under every process-level check. The signal is lag |
| a health question about all three | `systemd/brain-health` | run it by hand any time; the timer runs it every 60s with `--repair` |

## Backups, and the one thing they do not cover

```bash
store/bin/brain-postgres-backup.sh backup      # one compressed pg_dump, then prune to 30 days
store/bin/brain-postgres-backup.sh verify      # back up, restore to scratch, compare row counts, drop
store/bin/brain-postgres-backup.sh list
store/bin/brain-postgres-backup.sh restore <file>   # into a NAMED SCRATCH database, never over live
```

Losing this store costs **zero knowledge**: canon, decisions and receipts recover from git alone,
and `store/bin/brain-drop-test.sh` checks that by destroying a scratch copy and recovering the real
receipt corpus rather than by asserting it. What a backup buys back is what git never held: queue
history, threads, and every answer you typed.

## Things not to do

* Never point a suite, a script or a scratch-database verb at `brain`. `scratch-db.sh` refuses the
  name outright.
* Never run `scratch-db.sh` with no subcommand to find out what the verbs are. It prints usage and
  touches nothing now, because on 2026-08-18 a missing subcommand defaulted to `create`, which opens
  with `DROP DATABASE`, and destroyed the shared scratch store at ledger 30.
* Never reseed or truncate a store another process is serving. Read the listener's pid and
  `/proc/<pid>/environ` first, which is what the demo driver does before it will drive anything.
* Never `git add -A` in this tree. See `docs/COMMIT-HYGIENE.md`.

---

Also here: `README.md` for what this is, `docs/WHY-IT-IS-LIKE-THIS.md` for why it behaves this way,
`docs/CHANGING-IT.md` for editing it, `docs/KNOWN-GAPS.md` for what is missing.
