# Supervision, and the two process-lifecycle invariants a transaction cannot hold

`bin/swarm-fleet` was 641 lines in swarm-admiral and appeared in **no planning document for this
program**. Three coverage audits found the same hole: nobody owned process supervision. It is
ported at `engine/bin/swarm-fleet` and owned here.

## What tmux is doing

One session, one window per agent, one runner loop per window. `while :; do swarm-run T1; sleep
5; done`, so a runner that exits comes back. That part is deliberately boring.

**The session is named `swarm-runtime`, not `swarm`.** `swarm` is the session the live
swarm-admiral fleet occupies on this host, and `swarm-fleet down` kills a session by name. This
was caught by running `status` and watching it reconcile against T1..T6 windows belonging to the
other system: with the original name, a test run of `down` would have torn down the running
fleet, including the terminal running the test. The two supervisors must never share a session
name while both exist.

## The four reconciliation rules, each earned

**`up` refuses to start a name that is already heartbeating.** Two runners under one name both
heartbeat as that name, so `reap` sees a live agent and never requeues the dead one's task. It is
not recoverable by hand afterwards, which is why the guard refuses rather than warning.

**`down` writes an `offline` heartbeat for every window it kills.** tmux tears panes down with
SIGHUP. The runner traps HUP, but a runner between commands never gets to write its final
heartbeat, and `status` then reports ghosts forever -- and, worse, `up`'s guard above then refuses
to restart the fleet it just stopped.

**`down` sweeps orphan engines**, matched on `--add-dir $STATE_DIR`. A headless `claude -p`
outlives its runner and keeps billing against a store nobody is watching; four were found alive
against a deleted bus during the original build. Matching on `$STATE_DIR` rather than a shared
path is what makes the sweep scoped to this fleet's engines and unable to touch anything else.

**`status` reconciles three sources and names the disagreement.** Config, tmux and heartbeats.
A name in config with a live heartbeat and no window is a different problem from a window with no
heartbeat, and the table says which:

```
  AGENT        WINDOW  STATUS     SILENT   FINDING
  T1           no      working    12       MISMATCH: heartbeating with no window here
  T2           yes     -          -        MISMATCH: window is up but not heartbeating
  T3           yes     working    904      DIED: window up, silent 904s, still holding 0031
  T9           yes     -          -        window for an agent not in config
```

**`restart` deals with the held task immediately** rather than waiting out `reap`'s stale window.

## The spend brake, and the one rule in it

Wired 2026-08-16. `budget/**` is D6a's; three calls in `bin/swarm-run` are what make it act:

| where | call | on a refusal |
|---|---|---|
| before the claim | `budget check --agent --lane` | exit 3: do not dispatch. Nothing claimed, nothing spent, no attempt charged |
| after the run | `budget charge $total_cost_usd --ref $SID:$ATTEMPT --source run_json` | `brain.run` has no cost column, so this is the only record of what a run cost |
| in reconciliation | `budget halt --session --log --state` | exit 3 budget stop, exit 4 refusal, 0 neither |

**A budget stop takes `block`.** Not `fail`, which charges an attempt to a lane that did nothing
wrong. And emphatically not `reopen`, which clears the claim AND resets `attempts` to 0 -- so the
next free terminal claims the task and spends again, with no counter left to stop the loop. A
subscription refusal keeps taking `reopen`. The two compose rather than compete: the refusal
decides what happens to **this task**, the budget decides whether a **new dispatch** may start.

`budget halt` is asked **before** the log is scraped for a refusal, because a long run killed for
spend can carry a rate-limit line from an earlier turn it retried past. The classifier checks our
own record -- an incident written before the signal was sent -- before it reads anybody's text.

Two consequences worth knowing before touching this:

- **The session id is read from the stream when there is no result event.** A killed run never
  writes one, and a killed run is exactly the one whose session id `halt` needs to find its
  first-party evidence. `run-start` records no run id the runner can read back.
- **The gate does not know the lane yet, so the lane ceiling is not enforced there.** The claim is
  what decides the lane. `--lane` is passed to `budget check` only for a single-lane agent; fleet
  and agent ceilings always apply. **CLOSED as of task 0122:** the lane ceiling lives in `claim`
  itself, which is the only thing that knows the lane it is about to hand out. It excludes
  budget-stopped lanes from the candidate set in the same statement as the `FOR UPDATE SKIP
  LOCKED`, so a stopped lane is never claimed rather than claimed and released -- nothing is
  released, no attempt is charged. It reads `budget_state.stopping` **and** an open `manual_stop`
  in `budget_open_stop`, which is the same pair `budget.enforcer.evaluate` reads, because a manual
  stop has no policy and no spend and is invisible to the meter. It is lane scope only, and it is
  guarded on `to_regclass` so a store that predates migration 3 still claims.
  `tests/test-lane-budget-gate.sh` is the suite: 37 assertions, and its scene 0 is a control that
  proves the refused lane would otherwise have won the board.

  **The consequence to know before debugging an idle fleet:** a multi-lane runner whose lanes are
  all budget-stopped goes quiet with no "spend gate" line, because that line comes from a gate that
  was never asked about those lanes. It heartbeats `idle`, not `budget_stopped`. `budget status`
  is where the answer lives; making the runner say it is open work.

`tests/test-budget-wiring.sh` runs the real runner against a stub engine and asserts the
three-way discrimination in the store: unreported failure `inbox`/1, refusal `inbox`/0, budget
stop `blocked`/1.

### The fourth outcome: infrastructure

Task 0400, 2026-08-18. The operator lost internet at about 01:00Z; every in-flight engine call
returned `Request timed out`; the runner filed each as `unreported: engine exited rc=1 without
reporting` and spent an attempt on it. **Twenty-one tasks spent their entire attempt budget in
under an hour and not one was judged on its merits**, and the 3-strike backoff made it worse by
waking every 300s to claim a fresh task — 0366, 0371, 0374, 0373 and 0381 died as network probes.

So the discrimination is four-way now:

    unreported failure    ->  inbox    attempts 1   (one attempt spent, requeued)
    subscription refusal  ->  inbox    attempts 0   (unspent, and the account ring rotates)
    infrastructure        ->  inbox    attempts 0   (unspent, and the runner stops claiming)
    budget stop           ->  blocked  attempts 1   (parked for a human, NOT requeued)

**The discriminator is `terminal_reason == "api_error"`** on the engine's own terminal result
event, read from `$RUN_JSON` and falling back to the last result event in `$STREAM`. Measured
twice on the live bus on 2026-08-18, at 07:40Z and again after the second outage window: 291 run
jsons then, 297 after, and the shape held both times. At the re-check: 189 `completed`, 88
`api_error`, 10 `aborted_tools`, 10 `aborted_streaming`. All 88 are infrastructure and none is a
task failure (45 `Request timed out`, 41 session-limit walls, one 500, one lost connection). The
obvious heuristic — `num_turns == 1 and total_cost_usd == 0` — misses 9 of those 88, and they are
the expensive ones (`0358-attempt1`: 52 turns, $4.16, timed out mid-run).

Bounds, stated because they will otherwise be rediscovered: a run killed with **no result event at
all** is not classifiable and still spends an attempt, deliberately, because `reopen` resets
attempts to 0 and a pattern that matched a real failure would requeue it forever. A **4xx other
than 429** is the caller's fault, not the network's, and keeps spending. A **429** is a
subscription wall and its branch is asked first, so the account ring still rotates.

The second half is `infra_park`: three consecutive infrastructure failures and the runner stops
claiming entirely and probes `SWARM_NET_PROBE_URL` (default `https://api.anthropic.com/`, any HTTP
answer counts) until the network returns. A probe costs nothing; a task costs an attempt.

`tests/test-infra-failure.sh` is the proof — 62 assertions, including a before/after that runs the
same stub timeout against the runner as it stood at `b1b8054`, the last commit to touch it before
this fix (`inbox`/1), and against the fixed one (`inbox`/0), plus a live six-task park simulation
against an unroutable probe. The SHA is pinned rather than read as HEAD, so the "before" half keeps
measuring the defect after this change is committed.

**Still open, and not fixed here:** nothing pages. The fleet was dead for six hours and was found
only because a human looked; it can now sit *parked* for six hours just as quietly. The detection
gap is V7's loopback relay and this change does not touch it.

## The two invariants that are NOT transaction boundaries

This is the part the incident of 2026-08-16 taught, and it is why "one verb, one transaction" is
necessary and not sufficient.

**1. `ask` blocks the TASK and leaves the PROCESS alive.** T3 asked a question and kept running.
The answer requeued the task. T1 legitimately claimed it. Two agents then executed the same brief
against the same repo, both heartbeating `working` on `0092` in the same second. Claiming was
atomic throughout and was never wrong.

No transaction can fix this, because the thing that must stop is a process the database cannot
see. So the port splits the act:

- `answer` records the answer and tells the planners, always.
- It **refuses to requeue** while the asking agent is still heartbeating `working` on that task,
  and says so loudly. The answer still lands; only the requeue is withheld.
- `answer-requeue` is a separate verb, called once a human or the runner knows the engine is gone.

`swarm doctor` reports the state itself as a **CRITICAL**: a task whose `claimed_by` is one agent
while a different agent heartbeats `working` on it. The store cannot hold two claimers, so that
second heartbeat is the only tell there is.

**2. A release must be conditional on still being the claimer.** Killing T3's window fired the
runner's HUP trap, which released `0092` back to inbox **even though the store recorded T1 as the
claimer**. The trap acted on what the runner thought it held rather than what the store says it
holds, so a dying agent unclaimed a live one's task.

`release` carries `WHERE claimed_by = $me`. It lives in the transition rather than the runner
because every surface that releases needs it, not only the one that had the bug. `swarm-fleet
restart` uses it too, and reports rather than forcing when the store disagrees.

**And it was applied to `release` and to nothing else, which the D9 adversarial pass measured
(task 0144).** `fail`, `reopen`, `block`, `done` and `cancel` carried no such condition, and the
runner's **reconciliation** block calls three of them after every unreported run, guarded only by
"the task state is `active`" -- which is not "I am still the one holding it". A task reaped out
from under a slow run and re-claimed by another terminal is still `active`. Measured: T3 requeued
T1's live `0019` via `fail`, reopened T1's `0020` via `reopen` with its attempts going 1 -> 0, and
`mcp.tools.finish_work(task=<T1's task>, agent='T3')` did the same on a shipped surface.

The condition is now one function, `_hold` in `swarm_engine/transitions.py`, and every verb that
ends or moves a held task goes through it. Two consequences for anyone operating this:

- **Name yourself.** `swarm done|block|fail <id>` on a task an agent is holding needs `--agent`
  (`--from` on `reopen`). An anonymous report on held work is filed against the HOLDER whoever
  typed it, so it refuses rather than forges. A task nobody holds is not gated at all.
- **The override is `--force`, and it is signed and logged.** It needs `--agent` and writes an
  `OVERRIDE` note naming both agents to the task's thread. Use it when you can see both agents
  and you mean it.

`reap` is untouched and must stay so: a genuinely dead agent's task still requeues, which is the
whole point of `reap`. `doctor`'s two-agents-on-one-task CRITICAL is untouched too. Prevention
got better; detection and recovery did not get weaker.

## If you do not run tmux

Supported, and here is the accepted risk written down rather than implied.

Run `engine/bin/swarm-run T1` by hand, or from systemd, or under nohup. Everything works: the
runner heartbeats, claims, reconciles and traps signals on its own.

**What you lose is the one-runner-per-name guard.** Nothing stops you starting `T1` twice, and
two `T1`s heartbeating at once makes `reap` believe a dead agent's task is still being worked, so
that task never requeues. There is no automatic recovery.

The workaround, if you run runners by hand:

```bash
swarm status --json | python3 -c 'import json,sys
for a in json.load(sys.stdin)["agents"]:
    if (a["silent"] or 1e9) < 180: print("LIVE:", a["name"])'
```

Check that before starting a name, every time. `swarm-fleet up` is that check plus tmux, which is
the whole reason to prefer it.
