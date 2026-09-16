# Verb parity: the 40, checked against D1's coverage checklist

Checked by running them, not by diffing a `--help` listing. `engine/tests/test-verbs.sh` calls
every verb below against a scratch database and asserts its effect; a verb that parsed and did
nothing would pass a help diff and fail that suite. **78 assertions, 0 failures**,
re-measured 2026-08-19 by task 0317, which added the seventy-eighth. The 73 this line carried
before task 0273 was not re-run when the suite grew, and neither was the 77.

The list is D1's `migrations/COVERAGE.md`, which counted `swarm --help` on the live system and
got 40. I re-counted it from the same source and agree.

| # | Verb | Ported | Backed by | Verified by |
|---|---|---|---|---|
| 1 | `init` | yes | applies the schema; makes `runs/`, `logs/` | "init applies against a live store" |
| 2 | `post` | yes | `work_item`, `item_id_seq`, `thread` | id printed, canon vocabulary accepted, bad value refused |
| 3 | `claim` | yes | `work_item` (`FOR UPDATE SKIP LOCKED`), `agent`, `runtime_flag`, `thread`, and read-only `budget_state` / `budget_open_stop` | the gate, priority-band ordering, the lane ceiling, the live-holder gate, and the **actor gate** (`agent_claimable`, migration 26, task 0414) |
| 4 | `done` | yes | `work_item`, `thread`, `run` | "done says it is not acceptance" |
| 5 | `block` | yes | `work_item`, `thread` | "block parks a task" |
| 6 | `fail` | yes | `work_item`, `thread`, `question` | requeues, then blocks and raises a question |
| 7 | `ask` | yes | `question`, `work_item`, `thread` | returns a `q` id, blocks the task |
| 8 | `reopen` | yes | `work_item`, `thread` | "attempts reset to 0" |
| 9 | `objectives` | yes | `objective` | lists inbox, exits 2 when empty |
| 10 | `accept` | yes | `objective` | moves inbox -> accepted |
| 11 | `intake` | yes | `objective` + `source_name`/`source_signature` | dedups on a second pass |
| 12 | `config` | yes | **no table, by design** | resolves `{**defaults, **agent}`, `--shell` exports; `--fingerprint` hashes the file (0273) |
| 13 | `state` | yes | `work_item` | prints one word |
| 14 | `tick` | yes | `agent.tick_fingerprint` + `tick_at` | wakes on change, not on a timer |
| 15 | `doctor` | yes | `agent`, `work_item`, `question` | no traceback; exit 1 on a critical |
| 16 | `set` | yes | `work_item` | sets, and refuses a non-settable field |
| 17 | `answer` | yes | `question`, `message`, `thread`, `work_item` | records, tells planners, requeues |
| 18 | `reanswer` | yes | `question`, `message`, `thread` | amends, keeps `amended_from` |
| 19 | `questions` | yes | `question` | open and `--answered` |
| 20 | `msg` | yes | `message`, `thread` | reaches another agent |
| 21 | `inbox` | yes | `message` + `agent.inbox_read_seq` | reads, `--mark-read` moves the pointer |
| 22 | `note` | yes | `thread` | appends, full text |
| 23 | `heartbeat` | yes | `agent` | records liveness |
| 24 | `reap` | yes | `agent`, `work_item`, `thread` | shows without `--yes`, requeues with it |
| 25 | `ls` | yes | `work_item` | filters by state and lane |
| 26 | `show` | yes | `work_item`, `thread`, `artifact`, `run` | renders; `--full`; `--json` |
| 27 | `artifact` | yes | `artifact`, `work_item`, `thread` | records a real path AND a missing one |
| 28 | `artifacts` | yes | `artifact` | grouped by task, re-stats at read time |
| 29 | `signals` | yes | `work_item` recursive over `parent` | prints the nine |
| 30 | `why` | yes | `work_item` | explains queue position |
| 31 | `feed` | yes | view `brain.feed` | time order, `--follow` |
| 32 | `status` | yes | `work_item`, `agent`, `runtime_flag`, `question` | shows the fleet |
| 33 | `board` | yes | every read table | renders, no traceback |
| 34 | `brief` | yes | `work_item`, `thread`, `artifact`, `question` | summarises the window |
| 35 | `paused` | yes | `runtime_flag`, `agent.stopped_at` | exit 0 stop / 2 carry on |
| 36 | `stop` | yes | `agent.stopped_at`, `message` (kind `stop`) | one agent, not the fleet; **`--reason` is REQUIRED** and the record reaches the feed and `status` (0273) |
| 37 | `start` | yes | `agent.stopped_at`, `message` (kind `start`) | releases it, or exits 2/1 saying it did not (0253); signed and in the feed (0273) |
| 38 | `pause` | yes | `runtime_flag` (`set_by`, `set_at`, `note`) | claim returns nothing while paused; `status` now names who paused it and why (0273) |
| 39 | `resume` | yes | `runtime_flag` (`set_by`, `set_at`, `note`) | clears it, signed by whoever ran it rather than by the default (0273) |
| 40 | `cancel` | yes | `work_item`, `thread` | closes a task |

All 40. **The 14 the brief named as the drop risk** -- `init`, `objectives`, `accept`, `intake`,
`config`, `state`, `tick`, `set`, `paused`, `stop`, `start`, `pause`, `resume`, `cancel` -- are
all present and all exercised.

## Sixteen verbs BEYOND the 40, declared rather than slipped in

The brief says do not expand the verb surface. These sixteen are additions and each is named here
so the expansion is visible. `test-verbs.sh` asserts that the CLI exposes **exactly** these
sixteen beyond the 40, so a seventeenth cannot appear without a test failing. That assertion has
now caught it four times. The first: task 0280 shipped `utilization` in `cli.py` and declared it in neither this table
nor the test's `WANT_EXTRA` list, the suite went red at ef6d016, and task 0310 declared it here.

**The second, 2026-08-27, and it is the reason three rows were added at once.** Lanes C, E and F ran
concurrently under one commander and shipped `routine`, `whoami` and `admin`. Every one of the three
suites went green in its own lane, because a lane tests its own paths and this check reads the whole
CLI. It failed only in the commander's integration gate, where all six lanes were exported together
for the first time: `found: accept-work admin answer-requeue auto-accept release routine run-end
run-start utilization whoami withdraw`, eleven against a declared eight. **No lane could have caught
this and none of them was at fault.** The toll below was therefore paid by the commander rather than
by each verb's author, which is a weaker version of this rule and is recorded as such rather than
smoothed over: the author knows what the alternative was, and a commander is reconstructing it.

**The fourth, 2026-09-01, and it was the author's own miss.** S2 shipped `observation` and
`disposition` in `d5a3fa0` and declared them in neither this table nor `WANT_EXTRA`, so the suite
read `found: ... disposition ... observation ...`, sixteen against a declared fourteen, and the
pre-push hook refused for the whole wave. This one had no concurrency excuse and none is offered:
a single lane added two verbs to a file it owned exclusively and did not walk the two lists that
exist to notice. The toll was paid by the author, which is the rule working as intended.

| Verb | Why it exists | Whose instruction |
|---|---|---|
| `release <id> --agent A` | Put a task back **only if `claimed_by = A`**, and say so in the return value rather than raising. The runner's shutdown trap needs a verb it can call unconditionally and read the answer from. | Commander's 2026-08-16 incident note, second finding: "A release must be conditional on still being the claimer." |
| `answer-requeue <id>` | Requeue a blocked task after a human has confirmed its engine is stopped. Split out of `answer` because the requeue is the dangerous half and needs an assertion about a PROCESS that no transaction can make. | Same note, first finding: "the requeue-on-answer path must terminate or fence the original engine." |
| `run-start` / `run-end` | Open and close the `run` row. D1 created the `run` table for `show --full`'s pointer; without these two verbs nothing ever writes it and the table is dead. | D1 slot 1 (`run` exists), D4 brief (`show --full` is mine). |
| `auto-accept` | Read the week's disagreement report; with `--on`/`--off`, write `runtime_flag.auto_accept_enabled`. Both halves were unreachable: **nothing anywhere called `disagreement_report()`**, so the measurement D00 rule 3 gates the switch behind could not be read, and the flag had **no writer at all**, so flipping a fleet-wide behaviour change meant raw SQL outside the waist. | Task 0139, out of D9's acceptance audit (0118). |
| `withdraw <qid> --from A [--reason ...]` | Retire an operator question WITHOUT answering it, signed by whoever does it. `answer` and `reanswer` take no actor and always write the identity `operator`, so the fleet's only way to clear a question it no longer needed was to forge an operator decision -- the exact act task 0147 was posted to stop. It left them standing instead, and on 2026-08-18 the operator spent a real decision on q0140: a question raised by a test, about a release branch that does not exist, on task 0139, which had been cancelled four and a half hours earlier. `cancel` now cascades through this verb. | Task 0159, out of 0147. |
| `accept-work <id> [--by]` | Accept finished WORK. `accept` is the OBJECTIVES verb and always was, so the one act D00 rule 3 makes load-bearing -- acceptance is a human act, separate from the agent's `done` -- had exactly ONE door, and that door was a Flask **development** server on loopback. `swarm accept 0027` answered a real, finished, unaccepted task with "no objective in inbox named: 0027". | Task 0141, out of D9's acceptance audit (0118). |
| `utilization` | Print where the Anthropic **seven-day rate-limit meter** stands now: the value, the reading's timestamp, how many readings and run streams it rests on, and when the window resets. Read only, by design -- a gauge, not a governor. Before it, no surface in the engine could read the meter at all, so the standing commitment to stop the fleet at a ceiling was unenforceable in the plainest sense: nobody could tell whether it had been crossed. Where the ceiling sits is the operator's, asked as q0276 -- unanswered when 0280 built this, **answered 2026-08-19 02:17:18Z, seven minutes after ef6d016 landed**, at 0.85 and STOP all three terminals. Nothing in the engine enforces that yet; this verb is still only the gauge. Reads the run streams only, so it answers with Postgres down. NOT the `budget` CLI, which meters dollars behind a different binary. | Task 0280. |
| `routine <name>` | Create, arm, fire, list and **disable** a standing schedule. The alternative was the one this plan spent a year not choosing: `queue default fire` is a single hard-coded routine with no table behind it, and the two systemd timers supervise processes and dispatch no work, so "the runtime carries routines" was unsatisfiable as literally written and the incumbent board could not retire. The verb exists rather than a cron loop because a cron loop that posts tasks is fifty lines and is exactly the second source of truth this architecture spends most of its rules preventing. `routine disable` deliberately needs no credential: a kill switch that can be refused is not a kill switch. | Task 0376, lane C, under the 2026-08-27 commander program. |
| `whoami` | Print which human the database says this connection is, by asking `brain.current_human()` rather than by reporting what the caller requested. The alternative was the state that made it necessary: once several named humans exist, `$BRAIN_HUMAN` and `as_human` are **requests** that select a credential, and nothing anywhere turned a request into the answer. A surface that cannot ask "who am I actually" ends up trusting its own input, which is the shape of every forgery in this repo's history. | Task 0384, lane E, under the 2026-08-27 commander program. |
| `unaccept-work <id> --reason R` | Withdraw an acceptance without sending the work back. `reopen` moves the row and clears the agent's hold; this withdraws **only the decision**, leaving `state` and `result` untouched so the agent's report survives intact. It exists because acceptance was the one decision of record in the product with no inverse, which `MUST-NOT-BUILD` item 10 forbids wherever a real inverse is possible: the operator pressed Accept work, nothing said so, and there was no way back. Migration 43 is unavoidable rather than convenient, because migration 22's guard refuses the erasure outright; the new exemption needs BOTH a thread row of kind `unaccept` strictly after the acceptance and signed by `current_human()`, AND a non-NULL `current_human()`, and it was **watched failing first**: with either arm stripped, `brain_runtime` forged an unaccept and erased a human's decision. | Bus row `0413`, from Andrew's own demo walkthrough 2026-08-28: "nothing happened when I accepted work ... Maybe you have an option to like unaccept or like send back or undo the acceptance would be good." Declared under `MUST-NOT-BUILD` item 10, which he approved overruling and which was KEPT because his ask lands inside it. |
| `helper <id>` | Open an interactive engine session on ONE task, with that row's own record already loaded and in the directory the row names. It is deliberately **not** `claim`: `claim` takes the row, spends an attempt and puts an agent on the hook for a report, and a human opening a terminal about a row is none of those things, so this verb takes nothing, spends nothing and reports nothing. The alternative was measured on 2026-08-28 and is why this is not a convenience: the eleven-step demo never exercised the terminal at all, and the only route from a task to a session was to read the brief on one surface, open a terminal on another, and paste. It also carries the one fact the whole feature rests on. A Claude Code helper is launched with a `--session-id` minted BEFORE the launch, so the note it leaves on the thread and the row the session hooks write name the same conversation and the record is findable from the task; a Codex helper is told, on screen and before it starts, that its capture is NOT equivalent, because the point of the feature is that the operator can trust the record. | Andrew's own answer to "what is missing that would make you run your real day through this", 2026-08-28: *"the ability to launch a helper terminal from a task, defaulting to Claude Code or Codex"*, under *"We didn't get to test to make sure that all terminal conversations are being logged properly. That was a key part of the product that's not being tracked."* |
| `admin <group>` | Config get/set/unset, agent roster, role and secret provisioning, history and a capability manifest, all through `store.apply`. The alternative was measured before it was built and is the reason this row is not a convenience: **0 of 8 fleet config keys in force were writable from the CLI**, so changing a signal weight or the agent roster meant editing files and knowing which ones, and provisioning was three scripts with three argument conventions, one of which destroyed a shared store once. The verb group exists rather than a config-file editor because an admin CLI that writes config behind the store's back is the second writer this whole architecture exists to prevent. `CREATE ROLE ... PASSWORD` is the one thing that genuinely could not come through the waist, and it is stated in the open rather than solved by putting a superuser connection inside `store/`. | Task 0385, lane F, under the 2026-08-27 commander program. |
| `project <slug>` | The door onto `brain.project` (migration 44). Create a project, bring it to rest in the operator's own four words (`hold` / `ice` / `blocked`), return it to in progress, retitle it, file work into it, remove it. It exists because the entity landed with **no writer at all**: no subcommand, no console form, `web/bin/seed-demo.py` creates none, and the only way a project row came into existence was a hand-written INSERT at a psql prompt, which is the second-writer shape this architecture spends most of its rules preventing. It is not a label either: since task 0430 the claim path excludes every task whose project is not `in_progress`, in SQL, in the same statement that locks the row, and `project list` reports whether that gate is present on THIS store rather than assuming it. The asymmetry is migration 35's: bringing a project to rest needs no credential, because a kill switch that can be refused is not a kill switch, and returning it to `in_progress` needs the operator login because that is the statement that puts agents back on the work. **There is no `rename` and that is measured rather than omitted**: a plain slug rename is refused by the foreign key for any project that has work, the copy-and-repoint route commits and destroys 'when did this go on hold', and `ON UPDATE CASCADE` is refused by `work_item_project_matches_canonical` on exactly the rows that name a project twice. A subcommand that always refused would be `MUST-NOT-BUILD` item 2's disabled affordance, so there is none; `retitle` is offered instead and leaves the state stamp alone. |
| `observation open` and `observation close` | Open and close a standing observation against an event: EF-2's second record, the Orient half of OODA. It exists because the verb had **no door a primary user could reach**. `observation open` and `observation close` were registered transitions callable only by importing `fabric.emit` and calling `store.apply` from a Python prompt, and the store shows exactly that: `brain.observation` held **5 rows, all `kind='demo'`, all the identical string, all written 2026-08-16 by the lane doing the import**, and nothing since could have written another. `fabric/cli.py` did carry an `observe` subcommand, so the honest claim is not "no CLI" but "no reachable one": fabric is named in **0** of `AGENTS.md`, `CLAUDE.md`, `docs/OPERATING.md` and `roles/terminal.md`, has no bin entry, and is invoked by no automation in the repo. `observation close` had neither a CLI nor a helper. Named `observation open` rather than `observe` so the CLI's vocabulary and `store.transitions.registered()` do not drift, which `admin manifest` prints side by side. | The operator's ruling on the Orient and Decide doors, 2026-09-01: *the UI really just needs the verbs a UI might use, but the CLI is what agents, who are the primary users, will use.* Hence a CLI and **not** a console room, leaving the console's one-write-door property untouched. |
| `disposition record` | Record a verdict on an event, optionally answering a standing observation: the Decide half, and the same no-door finding. EF-3 is preserved rather than tightened -- `--event-seq` is required and `--observation-id` is optional, because a disposition always answers an occurrence and does not always answer a standing observation. The door adds one refusal the schema cannot make: an observation opened against a different event than the disposition names is rejected, since both foreign keys would be satisfied and the result is a kink in the lineage chain that reads as a real link to anything walking it. **It is not an acceptance and does not pretend to be**: `decided_by` here is free text with no trigger behind it, unlike `brain.recommendation.decided_by`, which migration 32 binds to `brain.current_human()`. This verb dispatches nothing and spawns nothing; the only path from a proposal to executed work is still `queue accept`. | Same ruling. The measurement behind it: the lineage spine had never been walked on anything but the five-row 2026-08-16 fixture, which `f-lineage-chain` recorded as CONTRADICTED. | Bus row `0442`, child of `0429`, under `MUST-NOT-BUILD` item 3's overrule by Andrew on 2026-08-28, against his own incident: *"there's certain projects where I kind of just wanted the AI to take a break with it. And then I realized later that it worked on it for like eight hours with eight terminals and I ran out of API tokens very quickly."* |

**The fourteenth, `project`, paid the toll in its own change too.** The verb's module and its
`cmd_project` body were written on 2026-08-29 by the agent on row `0442` and were **never
registered on the parser**, so for the whole of that day `swarm project` answered `invalid choice`
and the door did not exist as far as any operator could tell. A subcommand nobody can reach is the
same nothing as a subcommand nobody wrote, and this check could not catch it either: an
unregistered verb is invisible to a test that reads the parser. Lane B2 registered it, and this
row, the `WANT_EXTRA` literal and the registration landed together.

**The thirteenth, `helper`, paid the toll in its own change and the check never went red.** It is
the first addition to do that: 0280 updated neither place, the 2026-08-27 three updated neither
and were caught by an integration gate, and `unaccept-work` was caught in its own lane and handed
over. `helper` was written with this file open, because the brief that commissioned it quoted the
paragraph below at its author. That is the cheapest this rule has ever been enforced, and it is
worth recording that what made it cheap was somebody reading the catch note before writing the
verb rather than after.

**The third catch, 2026-08-28, and it is the mechanism working BETTER than the second time.** A
subagent working bus row `0413` under a commander session built `unaccept-work`, ran this check,
watched it go red, and **stopped rather than editing this file**, because the doc was outside the
scope it had been given. It reported the failure verbatim, named the twelfth verb, and drafted the
row. So unlike 2026-08-27, where three lanes each went green and only the integration gate caught
the drift, here the author knew before anyone else did and said so. The toll was still paid by the
commander, but this time it was **handed over deliberately rather than discovered**, which is the
difference between a check that catches a mistake and a check that prevents one.

`withdraw` is the same shape one more time: the alternative was a queue entry only the operator
could clear, and the way he cleared it was to answer it. It is deliberately NOT on `mcp/tools.py`
or the console yet -- one door, the CLI, which is the surface the fleet's terminals use and the
one whose refusals this task tested. A second door is a decision for whoever needs it, not
something to add unmeasured.

`release` and `answer-requeue` are **the incident fix**, not conveniences. The alternative was
leaving the defect in. `auto-accept` is the same shape: the alternative was a gate nobody could
read and a switch nobody could turn. `accept-work` is the same shape again, one layer up: the
gate existed, was reachable, and could be reached from one process on one port.

### Should the 40 absorb the extras instead? No. (task 0310)

0310 was posted asking this, because the list has now been extended eight times by exception and
an exception list that only grows stops being an exception list. The answer is that **the 40 is a
historical measurement, not a budget**: it is D1's count of `swarm --help` on the system being
ported from, and its only job is to say what was carried across. Absorb `utilization` into it and
the number stops meaning that. It would become 41, then 42, and the parity claim this whole file
exists to make -- "all 40 of D1's verbs are ported and each one was RUN" -- would no longer be
checkable against anything, because the baseline would move every time somebody added a verb.

The extras list is the right shape and the growth is the point: each row costs its author a
paragraph naming who asked for the verb and what the alternative was, and the test refuses to go
green until the row exists. That is a toll, deliberately. What it is NOT is a cap. Nobody has ever
argued eight is too many; if that argument arrives it is an argument about the CLI's surface, and
it should be made against these eight rows, which is exactly what having them written down buys.

The declaration still lives in TWO places -- this table and `WANT_EXTRA` in
`engine/tests/test-verbs.sh` -- and until task 0317 nothing checked that they agree. The test's
failure message said "declared in VERB-PARITY.md" while reading a hardcoded list that could
disagree with this file and still pass. 0280 got caught because it updated NEITHER; an author who
updated only the test would not have been. **That is now the seventy-eighth assertion**: the suite
parses the first column of the table above and asserts, in both directions, that it names exactly
the verbs `WANT_EXTRA` names, printing the size of the comparison on the verdict line (`8 in the
test, 8 in the doc, 8 matched`).

`WANT_EXTRA` stayed a literal rather than being derived from this file, which was the other option
and the tempting one. Deriving it makes one source of truth and makes every verb check downstream
of a prose parse, where a parse matching zero rows reads as a pass -- so the parsing is confined to
the assertion, and a parse that finds no section exits **2** saying `0 comparisons made` instead of
going green over an empty set (task 0292's rule; the file carries the guard and left that lint's
baseline in the same change). Verified by breaking it both ways rather than by reading it: delete
the `utilization` row and the suite prints `77 passed, 1 failed` naming `utilization` as only in
the test; rename this section's heading and it exits 2 having compared nothing.

### Why `accept-work` is a second command and not a smarter `accept` (task 0141)

Because the label has to name the verb that will run -- the rule the console follows in
`web/MUST-NOT-BUILD.md` item 1, and the right rule here too. `accept` dispatches the objectives
transition and `accept-work` dispatches `accept work`; those are two state machines, and a
command that picks between them by the SHAPE of its argument picks wrong the day an objective is
named `0027`. What the wrong door does now is point at the right one: `swarm accept <task-id>`
still refuses, and the refusal prints the command that works, because D9 hit the old message and
read it as a lost task.

**The rule-4 gate is deliberately in two places, and they are not the same check.** The
transition refuses any acceptor registered in `brain.agent` -- `claim` writes that row, so the
test asks a real question about the acceptor and holds for every surface built later. The
CLI **door** additionally refuses to run at all inside a fleet terminal, read off `SWARM_AGENT`
and `SWARM_PARENT_TASK`, the same pair `transitions._caller` reads. That half is at the door on
purpose: a console started FROM a terminal shell inherits `SWARM_PARENT_TASK` and would be
refused forever by an env check inside the transition, and D9 started the console exactly that
way. An agent that unsets both variables AND passes `--by operator` defeats both, and nothing in
one process can stop it; what the two gates remove is every convenient path, which is the same
claim `store/narrow_waist.md` makes for the waist itself.

### The claimer predicate is no longer `release`'s alone (task 0144)

The row above used to read *"`fail` cannot be made conditional without changing what `fail`
means"*. That was the reasoning on the day `release` was written and it was wrong: the D9
adversarial pass measured `fail`, `reopen`, `block`, `done` and `cancel` all ending work a
DIFFERENT agent held, from the runner's reconciliation block and from the shipped MCP
`finish_work` tool. Making a verb conditional on the caller still holding the task does not
change what the verb means; it stops the verb meaning something nobody asked for.

So the condition now lives in **one** function, `_hold` in `swarm_engine/transitions.py`, and
every verb that ends or moves a held task goes through it. `release` keeps its own predicate and
its own shape on purpose: it answers `{'released': False, 'owner': 'T1'}` where the finishers
raise, because the runner's TERM trap calls it blind on shutdown and needs an answer rather than
an exception. `reap` also keeps its own, against the agent it just found dead, because gating it
on the caller would break the recovery path the fix must not weaken.

**A deliberate override exists and is signed:** `--force` on the finishers acts anyway, requires
the caller to name itself, and writes an `OVERRIDE` note to the task's thread naming both agents.
`mcp/tools.py` exposes no `force`, because an agent that can grant itself the override has no
predicate. `engine/tests/test_claimer_predicate.py` is the suite; its mutation check is
`outputs/2026-08-16-D9-fix1/mutation_check.py`.

### `claim` grew a second gate, and it reads two tables it cannot write (task 0122)

The file bus's `claim` decided one thing: which task. This one also decides whether that task's
**lane** may spend. It is the same statement -- the exclusion sits beside the `FOR UPDATE SKIP
LOCKED` -- because the alternative is claim-then-release, and a claim you then release is a claim
another agent raced for while `release` is conditional on still holding it, so the loser of that
race gets nothing while the queue looks like it moved.

Why here and not in the runner, which is where the rest of the spend brake lives: `bin/swarm-run`
asks `budget check` **before** the claim, correctly, and at that moment it does not know the lane,
because the claim is what decides it. Task 0110 passed `--lane` only for an agent with exactly one
configured lane. Every terminal in the shipped default config is `lanes: ["*"]`, so for the whole
default fleet a lane ceiling was ungated at dispatch and braked one run later, through the charge.

Three properties, each of which is a scene in `engine/tests/test-lane-budget-gate.sh` (37
assertions):

- **It reads the same pair the enforcer reads.** `budget_state.stopping` for spend against a
  ceiling, and an open `manual_stop` in `budget_open_stop` for the operator's own act, which has
  no policy and no spend and so appears in neither the meter nor the spend view. It ignores
  `hard_stop` rows in that view exactly as `budget.enforcer.evaluate` does, because a spend-derived
  stop re-derives from `stopping` and honouring it twice keeps a lane down after the ceiling was
  raised. A soft ceiling (`--no-hard-stop`) does not exclude a lane, because `stopping` already
  folds `hard_stop_enabled` in. The suite's last scene walks every lane and asserts the claim gate
  and `budget check --lane X` give the same answer.
- **Lane scope only.** Fleet, agent and work_item ceilings stay the runner's, which knows all three
  before it claims and prints a reason the operator can read. A second brake here would refuse with
  "no claimable work", which is a worse sentence for the same event.
- **It is guarded on `to_regclass`.** `budget/schema/0003_budget.sql` is not in `migrations/`, and
  `bin/swarm-run` supports a store without it on purpose. Naming a missing relation in the
  candidate query would abort the transaction and stop the **entire fleet** claiming, which is a
  worse outage than the lag it replaced. Same guard, same reason, as
  `migrations/0013_thread_budget_kind.sql`.

**The cost, stated rather than buried:** a multi-lane runner whose every lane is stopped now goes
quiet with no reason line, because the runner's gate was never asked about those lanes. It
heartbeats `idle`. That is the one thing this change made worse and it is open work.

### `claim` grew a THIRD gate, and this one is about who is alive (task 0407)

On 2026-08-18 `swarm answer` requeued task 0379 while T5 still held it, T4 claimed it four
minutes later, and at `07:56Z` both agents heartbeat `status=working task=0379` with live windows
and live runners. Five minutes of two agents on one brief. V00 records the same invariant as
having been caught on 2026-08-16 **"only by luck"**, and the luck ran out.

The requeue-on-answer half was already closed here before the incident -- `answer` refuses to
requeue behind an asker that is still heartbeating, `release` is conditional on `claimed_by`, and
the finishers go through `_hold`. All three were re-measured under this task and all three hold.
**The incident still reproduces in this engine**, and `engine/tests/test_double_claim.py` is that
reproduction, because closing producers one at a time leaves the next producer open:

> `_hold` calls a task held only when `state = 'active'`. `ask` moves a held task to `blocked`
> and leaves `claimed_by` set, so from that moment `reopen`, `fail` and `set state` all walk
> straight through the predicate on a task a live agent is executing.

That is not an exotic path. `reopen` on a blocked task is the commander's **ordinary** recovery
move, and tasks 0400 and 0407 were both reopened exactly that way at `08:13Z` and `08:31Z` on the
morning of the incident. Measured before the fix: `reopen` -> `inbox`, `claimed_by` cleared,
`T4 claim` -> handed the task, two agent rows on it.

**So the gate went on the consumer.** `claim` is the single transition every producer of that
state must pass through to do harm -- a task sitting in `inbox` under a live agent is untidy, a
task HANDED OUT from under one is the incident. `_NOT_HELD_BY_A_LIVE_AGENT` is a `NOT EXISTS`
anti-join interpolated into the candidate `SELECT`, so:

- **atomicity is untouched.** The candidate is chosen, tested for a live owner and locked in the
  same `FOR UPDATE SKIP LOCKED` statement. Doing it as a read before the select would open an
  interval in which the answer could go stale, and that interval is where the incident lived.
- **the measured path did not regress.** `test-claim-contention.py 20 12`, three warm runs with
  the gate: spread 96.2 / 166.5 / 88.3 ms, contention **12/12** every run, 12 claims, 12 distinct
  ids, none lost, none duplicated. The same box, same minute, same database, with the anti-join
  removed: 88.8 / 88.6 / 169.2 ms, contention 12/12. Indistinguishable.
- **the self-exclusion is load-bearing.** `a.name <> %(agent)s`: a runner that died and came back
  under its own name has a stale row of its own pointing at the task, and without it that agent
  would be fenced out of its own work by its own ghost -- a wedge the gate itself would have made.

**THE CORPSE RULE, and why it cannot wedge a task.** `CLAIM_LIVENESS_SECONDS` is 900, which is
deliberately the SAME number as `reap --stale-seconds`, and the equality is the whole argument:
the set of tasks this gate withholds and the set of agents `reap` will pronounce dead are exact
complements at every instant, so no task is ever both unclaimable here and untouchable by the
recovery verb. An agent silent longer than that is a corpse -- `claim` steps over it and buries
the row, because `reap` only walks tasks in `active` and would never have cleared a row left on a
task that passed through `inbox`, so `doctor` would have cried "two agents on one task" forever.
Against `bin/swarm-run`'s 60s heartbeat, a background `while sleep` loop that does not stop while
the engine is thinking, 900s is 15 consecutive missed beats: a dead process, not a long turn.

**The gate is never silent**, which is the difference between a fix and a fix nobody can audit.
`reads.withheld_by_liveness()` reads the same predicate; `claim --explain` names the task, the
holder and how long ago it heartbeat; and `doctor` reports it CRITICAL. Doctor needed the addition:
its existing two-agents check walks only `state = 'active'`, so it saw "two agents on one task"
and was blind to the other face of the same defect -- **nobody owns work that is being done**,
which is what killing T5's runner produced during the recovery when its shutdown requeue set 0379
to `inbox` with `claimed_by` empty while T4 was working it.

**It interacts with task 0400's fix, and the interaction is good.** 0400 made the runner call
`reopen --from $AGENT` instead of `fail` when the engine dies on infrastructure, so an outage
stops spending attempts. That reopen fires while the runner is alive and its agent row still reads
`working` on that task -- `hb idle` sits at `swarm-run:1083`, INSIDE the empty-claim branch, so it
runs after the next claim rather than before it. So this gate decides who picks a reopened task
up, and the answer is the runner that already has the workdir warm: the self-exclusion lets it
re-claim its own task, and every other runner is held off until its row moves on.
`test_the_0400_infra_reopen_lands_where_it_should` pins that, including `attempts` still being 1.
The residual: a runner that reopens its own task and then DIES leaves the task fenced until the
900s corpse rule frees it. Bounded, and the alternative -- handing it out immediately -- is the
incident.

**What this does NOT do**, stated rather than found later:

- It is not a lock and not authentication, for the same reasons `_hold` is neither.
- It does not close the producers. `reopen` on a blocked-and-still-claimed task is still permitted
  and still clears the claim. The harm is fenced; the untidiness is not, and it is now visible in
  `doctor` rather than invisible everywhere.
- `answer`'s refusal still leaves a task **blocked with no automatic way back**: nothing in this
  tree calls `answer-requeue`, so a question answered while its asker is alive waits for a human.
  Bounded and visible, not fixed. Posted as its own task.

### `reassign`: the verb shape, PROPOSED and deliberately not built (task 0407)

The brief's fourth defect is that when a commander has to repair ownership, the only tool is
`swarm claim --agent T4`, which spends an attempt (0379 went 1/3 to 2/3 that morning) and only
worked because the task happened to be top of the queue by priority. Repairing ownership should
not mean "hope the right task is at the head of the inbox."

It is written here rather than shipped because the brief says **do not build a second door into
claim state**, and that is the correct instruction: `claim` is the one transition point, and this
gate only holds because everything must pass through it.

    swarm reassign <id> --to <agent> --from <caller> [--force]

- **Names the task.** No queue order, no priority, no head-of-inbox luck.
- **Spends no attempt.** It moves an existing claim; it does not start one. `attempts` untouched
  in both directions, which is the whole complaint.
- **Refuses a live target.** Same predicate as the claim gate: if `--to` is already heartbeating
  something else, that is a second double claim being created by the repair for the first.
- **Refuses a live source without `--force`.** Moving work out from under a heartbeating agent is
  permitted -- the commander does need it -- but never silently. `--force` writes an `OVERRIDE`
  note naming both agents, exactly as the finishers do.
- **Goes through `claim`'s own transition body, not around it.** Whatever gate `claim` grows next
  applies here for free. That is the difference between one door with two handles and two doors.
- **`release` stays.** It is the runner's blind shutdown call and answers `{released, owner}`
  rather than raising; `reassign` is the commander's deliberate act and raises. Two shapes because
  they have two callers, one of which is a dying process.

## The one thing that did NOT port cleanly: CLOSED by migration 6 (task 0111, T2)

**`post --dependency-unblocking 5` and `post --confidence 0.9` now work end to end.**

`bin/swarm` accepts both: `dependency_unblocking` as a count of items unblocked, `confidence` as
0.0 to 1.0, folded by `signal_level()`. Migration 1 carried the *word* aliases and not those two
numeric branches, so until 2026-08-16T13:00Z:

```
file bus:     signal_level('dependency_unblocking','5')  -> 'high'   validate accepts '5'
migration 1:  brain.signal_ok('dependency_unblocking','5') -> false  the INSERT is REFUSED
              brain.signal_level('dependency_unblocking','5') -> 'low'  (the conservative default)
```

Measured on all six numeric cases at the time: **6 of 6 refused by the CHECK, 4 of 6 also fold
differently.** (The other 2 agreed by coincidence -- the conservative default happened to equal
what the count and the threshold would give.)

**`migrations/0006_signal_numeric_vocabulary.sql` fixed the store, not the CLI**, which is the
resolution D4 argued for. The CLI's temporary in-words refusal is **deleted rather than replaced
by a coercion**: `post --dependency-unblocking 5` stores the string `5` and folds it to `high` only
on read, so the caller's count survives in the column and `swarm signals` prints
`dependency_unblocking  high  5`. `brain.signal_numeric()` is now the single accepted-set
definition, shared character for character with `NUMERIC_LITERAL` in `swarm_engine/signals.py`, so
the write gate and the fold cannot drift apart again the way migration 1's did.

Two edges of `bin/swarm` are deliberately NOT ported, both found by executing it:
`signal_level('confidence','nan')` returns `high` there (a garbage string at the TOP of the range),
and `signal_level('dependency_unblocking','inf')` raises an uncaught `OverflowError`. Both are
refused on write and read conservative here.

`test_signal_parity_numeric_vocabulary` was D4's blast-radius lock on the gap and is now the
assertion of the fix; `test_signal_parity_numeric_is_the_same_accepted_set_on_both_sides` sweeps
378 field/value pairs and asserts the two gates and the two folds all agree.

## Host affinity: PORTED INERT, and here is what that means

`post --host`, `claim --host`, `heartbeat --host` and doctor's pinned-host finding all exist and
all work. v1 is single-host, so:

- Every task this engine posts has `host = ''`, which `host_match()` reads as "runs anywhere".
- A task pinned by hand to a host label no agent reports gets a **doctor warning**, not silence.
- `agent.host` comes from config or the caller, never from `uname()`. The CLI relays.

**Ported inert rather than dropped**, and the reason is the column: dropping the flags would
still leave `work_item.host` and `agent.host` in D1's schema, and a column with no verb behind it
is how a field silently stops meaning anything. Inert costs one `WHERE` clause that is always
true on this host. Dropping it costs a migration and a re-port the day a second machine exists.

## Question paging: DECIDED, one path, and it is WIRED as of task 0140

The file bus exec'd `~/.swarm/notify.sh` on every `ask`. **Not ported, and still not ported.**
This engine writes the `question` row and prints one loud line on stderr for the terminal that
ran the verb; it starts no second notification path. The transport is D5's `event emit`.

**The gap this section used to declare is closed.** It read "until it is wired, `swarm questions`
and `swarm brief` are the only surfaces", and that stayed true for a day longer than anyone
noticed, because each lane had built its half against a different bus: `_page()` printed and
returned, and D5's producer read `$SWARM_HOME/operator/open/<qid>.json` and could not see a row
in `brain.question`. D9's acceptance run measured it (`max(event_seq)=54` before `q0028` and 54
after, with the paging subscriber listening) and dispatched it as **task 0140**.

What closed it, and the two things worth carrying forward:

**The call site is `store.transitions.after_commit`, not `cmd_ask`.** Wiring the producer into
this CLI would have paged the CLI's questions and silently not the MCP server's, not the
console's, and not the one `fail` raises for itself when a task runs out of attempts -- which is
the same seam one layer up, and the 02:00 case is precisely `fail`'s. Every surface already goes
through `store.apply`, so the join lives there once and a surface cannot forget it because a
surface never mentions it.

**"Emit inside the `ask` transaction" was never available.** It is the tidier-sounding of the two
directions the seam report offered, and the grants refuse it: `ask` runs as `brain_runtime`,
`brain_runtime` holds no INSERT on `brain.event` (`migrations/0002_roles.sql:69`), and that
asymmetry is what makes a producer unable to read the bus it writes to. Taking it would have
meant editing D1's migration to delete the role split. So the hook runs after the commit, on its
own connection, under `brain_producer`, and it cannot fail the verb.

Proven end to end rather than asserted: `engine/tests/test_question_paging_join.py` (28
assertions), plus one live chain -- CLI `ask`, MCP `raise_question`, and a `fail` to exhaustion
all produced events, and `python3 -m subscribers.operator_paging --once --dry-run` paged 3 of
them and correctly withheld the flagged one's contents as a tombstone. `swarm doctor` now reports
an open question with no `question.raised` event as **CRITICAL**, so the next time this join
breaks it is a finding rather than a silence.

## `.claude/commands/swarm.md`

**Left pointing at swarm-admiral, deliberately, and this is the note that says so.**

That command file is the operator's plain-language console and its translation table maps what he
says to `swarm-admiral/bin/swarm`. swarm-admiral is still the running system -- it is running this
build -- so repointing it at an engine with an empty scratch store would break the console he uses
every day in exchange for nothing. It gets repointed in the cutover, with the store, not here.
The risk of leaving it is that it drifts; the risk of moving it now is that the operator's live
console stops working tonight. Recorded so the cutover has one more item rather than one fewer.

## The work order and the report stop sharing a column (task 0138, migration 14)

`brain.work_item` had exactly **one** free-text column, `result`, and two different facts took
turns in it:

```
post                            result := the posted body        the work order.  What to do.
done / fail / block / cancel    result := the report             What happened.
```

The second write did not amend the first, it **erased** it, and nothing else held a copy: the
thread's `post` event stores the task TITLE, never the body. Since `bin/swarm-run` builds a
terminal's entire prompt from `swarm show "$TASK_ID"`, the consequence was that **`reopen`
dispatched attempt 2 against attempt 1's summary** -- the task was re-worked from the text it had
been rejected FOR, with the instruction it was rejected for missing no longer in the database.
`reopen` is the rejection verb the whole human-acceptance design rests on (D00 rule 3).

Measured on a scratch database built from migrations 1-13, on 2026-08-16:

```
post -f -  <<< 'THE POSTED BRIEF. If this sentence is missing after a done+reopen, ...'   -- 0003
claim --agent T1;  done 0003 --summary 'a summary that is not the brief' --agent T1
show 0003                                          -- result: a summary that is not the brief
SELECT count(*) FROM brain.thread WHERE text LIKE '%THE POSTED BRIEF%';           -- 0
```

Two things about the report worth keeping, because both were found by re-measuring rather than by
reading it:

* **`done` alone is the destroyer.** The `reopen` is not part of the loss, only of the
  consequence: it makes the loss *dispatchable*.
* **`fail` is a second door, and the wider one.** Its requeue branch writes the reason into
  `result` and sets `state = 'inbox'` in the same statement, so the same destruction happens with
  no operator in the loop at all -- and the runner reconciles **every** unreported run to `fail`.
  The original report named only `done` + `reopen`.

It was also D00 rule 11 ("store full text, truncate only renderings") broken by a second
mechanism. `_finish` already refuses the file bus's 2000-byte cut and `render.py` exists so that
only renderings shorten; storing the brief and then replacing it is not a truncation but is just
as unrecoverable.

**The split.** `migrations/0014_work_item_brief.sql` adds `brain.work_item.brief`:

| column | means | written by | overwritten |
|---|---|---|---|
| `brief` | the work order. What to do. | `post`, once | **never.** Write-once at the table |
| `result` | the latest report. What happened. | every finisher | every finisher |

Write-once is enforced by the `work_item_brief_write_once` trigger, not by a test -- same posture
as migration 12's parent-cycle guard, and for the same reason: `done` overwriting `result` was
never an intended act either, so the guarantee cannot live in the politeness of the callers. The
trigger permits `'' -> text` so a brief destroyed before migration 14 stays repairable, and
refuses every replacement or blanking of a non-empty one.

Three things a reader will otherwise trip over:

1. **`post` writes the body into `brief` ONLY, and a fresh task's `result` is empty.** It did
   briefly write both. Migration 14 landed that way on purpose, because surfaces this lane does
   not own read the brief out of `result` on an unfinished task (`web/model.py`'s `review()`,
   `web/templates/detail_task.html`, `swarm show`), and dropping the copy that day would have
   blanked D7's brief panels. Task 0169 moved those surfaces and then dropped `%(body)s` from the
   INSERT's `result` column, so the duplication is over: two facts, two addresses, one writer
   each. A verb that writes the body back into `result` is reintroducing the defect, not
   restoring a convenience. Rows posted DURING the transition still carry the duplicate, which is
   why `swarm show` keeps a `result != brief` guard alongside its emptiness check.
2. **The `brief` write is a second statement, not a column in `post`'s INSERT.** Same transaction,
   so atomicity is unchanged. The INSERT keeps its column list and its placeholder list in step by
   hand, and three terminals were editing this file concurrently on 2026-08-16: a partial overwrite
   landed with `brief` added to the column list and the placeholder edit lost, which is
   `INSERT has more target columns than expressions` and **every post in the fleet failing**. One
   empty brief is a better worst case than a store that cannot accept work.
3. **The backfill recovers only what provably survives**, and `claimed_at IS NULL AND attempts = 0`
   is NOT the test -- `reopen` resets `claimed_by`, `claimed_at`, `finished_at` and `attempts`, so
   a rejected row is indistinguishable from a fresh one on every one of them. The append-only
   `brain.thread` is the evidence instead. Rows that went through a finisher are left empty and
   named in a `NOTICE`; a work order invented from a report would be fabrication.

`engine/tests/test_brief_survives_finishers.py` (34 assertions) pins each finisher separately, the
`reopen` and `fail` round trips against the **subprocess** output of `swarm show` rather than
against the store, the trigger, whole-text storage over the 2000-char render limit, and that a
body-less post gains no invented brief.

**Not applied to `brain`.** The live store is at `schema_migration` 9; 10, 11, 12 and 13 belong to
tasks that were still open the same night and are unapplied there too. `post` now requires the
column, so **whoever applies 10-13 must apply 14 in the same pass** or the first `post` against
that store dies on `column "brief" does not exist`. No column-existence check was added to the
code to soften that: a loud failure at deploy time beats a silently empty work order.
