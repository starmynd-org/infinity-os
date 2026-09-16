# Budget enforcement: the spend brake

> A budget stop is the operator's money and means **STOP**.
> A subscription refusal is a window that reopens on its own and means **WAIT**, with the task
> going back **unspent**.

Those are opposite conditions that look identical from a distance: a run that ended early, an
engine that exited non-zero, a task still sitting `active`. Everything in this directory exists
to keep them apart, because confusing them is expensive in both directions.

| Confusion | What it costs |
|---|---|
| A refusal treated as a breach | A healthy fleet stranded. Measured 2026-08-14: 41 charged attempts, 18 blocked tasks, 18 spurious operator questions, 11 idle hours with every runner healthy |
| A breach treated as a refusal | Real money, in a loop. The refusal path is `reopen`, which resets `attempts` to 0 by design, so the task is claimed by the next free terminal and spends again with no counter left to stop it |

## Run it

```bash
budget/bin/budget status                       # ceilings, spend, open stops, recent incidents
budget/bin/budget set fleet --limit 40         # a ceiling, fleet wide
budget/bin/budget set agent T2 --limit 5       # and per agent
budget/bin/budget check --agent T2 --lane d6a  # the dispatch gate. exit 3 = do not start
budget/bin/budget guard --pid 1234 --agent T2  # stand beside a live run. exit 3 = it stopped it
budget/bin/budget stop fleet --reason "..."    # stop now. Does not clear itself
budget/bin/budget resume fleet                 # the only thing that lifts a manual stop
budget/bin/budget outcome 12 --signal TERM     # what the incident's prediction ACTUALLY did

python3 -m budget.demo.demonstrate             # seven scenes, real processes, live store
python3 -m budget.test_budget                  # 18 tests, ~40s
```

## What is here

| Path | What it is |
|---|---|
| `schema/0003_budget.sql` | `budget_policy`, `budget_charge`, `budget_incident`, two views, the grants |
| `transitions.py` | The seven state changes, registered once each on the narrow waist |
| `enforcer.py` | `preflight()` (refuse a dispatch) and `RunGuard` (kill a live run) |
| `halt.py` | The one classifier: budget stop, subscription refusal, or task failure |
| `reads.py` | `budget status` and the gate, both reading `brain.budget_state` |
| `cli.py`, `bin/budget` | A thin wrapper. Argument parsing and rendering, no logic |
| `demo/`, `test_budget.py` | The demonstration and the regression suite |

## Why three tables and not `runtime_flag`

D1 asked whether budget state fits `runtime_flag`. It does not, and the answer is in
`D-CROSSTALK.md` slot 1 with its measurements. Three things a flat key/value cannot give a
budget:

1. **An append-only stream.** `runtime_flag` is `key text PRIMARY KEY`. The second breach of the
   same scope is a duplicate-key error, so an incident stream would need a timestamp mangled into
   the key: a log line wearing a primary key, not joinable and not queryable by kind.
2. **Types with ranges.** `value` is `text`, and a policy of `{"limit_usd": "twenty dollars",
   "warn_percent": "ninety-ish"}` inserts cleanly. The rest of this schema refuses
   `reversibility='probably'`; a spend brake deserves the same.
3. **An idempotent meter.** `brain.run` has no cost column, so nothing recorded what a run spent.
   `budget_charge` is `UNIQUE (source, source_ref)`, because reconciliation re-reads a run's json
   and a long run is charged while it is still running.

No `runtime_flag` key was added. A cached `budget_stopped` boolean would drift from the ledger
that decides it; the derived question is a view (`brain.budget_state`), not a switch.

## How the distinction is made structural

**One.** The database cannot hold a refusal in the breach table:

```sql
cause text NOT NULL DEFAULT 'budget' CHECK (cause = 'budget')
```

```
$ INSERT INTO brain.budget_incident (kind, scope_type, cause, action_taken)
    VALUES ('hard_stop','fleet','rate_limit','run_stopped');
ERROR:  new row for relation "budget_incident" violates check constraint
        "budget_incident_cause_check"
```

A gate in application code is bypassable by a bug or a wrong branch. This one is not reachable by
any code path, however that code is written: the same reasoning that makes the four roles grants
rather than if-statements.

**Two.** The classifier checks its own record before it reads anybody's text:

```
1. FIRST-PARTY   an incident row carrying this run_id, written BEFORE the signal was sent
2. INFERRED      LIMIT_PARSER, lifted from swarm-admiral/bin/swarm-run, matching the log
3. THE BUS       still `active` means the agent never reported: a task failure
```

The order is the whole point. A long run killed for budget may carry a rate-limit line from an
earlier turn it retried past. Read the text first and that run is misfiled as a refusal, reopened,
re-dispatched, and spends again. Proven in `test_first_party_evidence_beats_a_poisoned_log`,
against a log that contains both, where `LIMIT_PARSER` genuinely matches and the classifier still
returns `budget_stop`.

**Three.** They differ on every field that matters, and a test asserts each one:

| | subscription refusal | budget stop |
|---|---|---|
| detected by | text in the engine log | our own record, written before we signalled |
| disposition | `reopen` | `block` |
| clears itself | yes, at `retry_after_epoch` | **never** |
| charges an attempt | no | no (the lane did nothing wrong) — **enforced since 2026-08-29**, task 0461: the claim charges it and `block --unspent` gives it back |
| costs | `cost_usd=0`, `turns=1` | the accrued spend, metered |
| recorded in `budget_incident` | **cannot be** | typed row |
| the fix | wait, or rotate account | a human raises the ceiling or types `budget resume` |

**Four.** They compose rather than fight. The refusal decides what happens to **this task**; the
budget decides whether a **new dispatch** may start. A refusal during an open budget stop yields
both: the task goes back unspent to wait for the window, and nothing new starts. Account rotation
in `swarm-run` is untouched.

## The verbs

`budget set` · `budget unset` · `budget charge` · `budget stop` · `budget resume` · `budget note`
· `budget outcome`, plus `budget status` and `budget check`, which are reads.

Three of those are the surface the brief names. The others are the mechanism the named three
would otherwise have to reimplement privately: without a metering verb the ceiling has nothing to
compare against; without a non-stopping incident verb a warning has to be filed as a stop; without
an outcome verb a row claims a process died before anyone looked; and a ceiling that can only be
lowered and never removed makes "turn it off" mean "set it to a very large number", which is
indistinguishable in the data from a deliberate ceiling.

Every one is registered exactly once through `@store.transition` and reachable only through
`store.apply`. The console and the MCP server are wrappers over these, like `bin/budget` is.

## Two ordering rules worth keeping

**Record the incident, then send the signal.** A crash between the two leaves a stop that is
recorded but not carried out, and the next preflight refuses anyway because the gate is derived
from the meter. The other order leaves money spent, a process dead, and no record of why, which
the runner reads as an unexplained failure and charges the lane an attempt it did not earn. Same
reasoning as writing the port registry row before the first bind.

**Charge, then decide.** They are two verbs, not one. The charge must land whatever the decision
turns out to be; a decision that rolled back its own evidence would under-count the spend it is
judging.

## What this does not do

- **It does not meter anything by itself.** The runner does. Wired 2026-08-16 in
  `engine/bin/swarm-run`: `budget check` before the claim, `budget guard` beside the live run,
  `budget charge` with the engine's `total_cost_usd` after the run, `budget halt` in
  reconciliation ahead of the refusal check.
  `engine/tests/test-budget-wiring.sh` runs the real runner against a stub engine and proves the
  three-way discrimination in the store: an unreported failure leaves `inbox`/attempts 1, a
  refusal leaves `inbox`/attempts 0, a budget stop leaves `blocked`/attempts 0.
  - That last cell read `attempts 1` until 2026-08-29, contradicting the table above in this very
    file, and the row was right and the code was wrong. The attempt is charged by the **claim**,
    before a run does anything; `block` never touched the column, so it simply left the charge
    standing. Measured on live `brain` that day: nine rows sat `blocked` at attempt 1 of 2 at
    once, eight of them carrying a `hard_stop` incident naming the row, and with
    `max_attempts` defaulting to 2 a second crossing would have exhausted a healthy row's whole
    allowance. The operator requeued all nine by hand. `swarm block --unspent` now refunds it,
    gated on a first-party `budget_incident` so it cannot become an opt-out from the retry limit,
    and the row **stays blocked** — parked is what keeps a refund from becoming a retry loop, and
    it is why `reopen` still cannot be used here. `engine/tests/test_budget_stop_is_unspent.py`
    (task 0461) is the suite.
  - One defect in this directory was found by that wiring and fixed with it: `budget stop`
    wrote its thread line as kind `budget`, which `brain.thread.kind`'s CHECK does not allow, so
    **every stop carrying a `work_item_id` rolled its whole transaction back** and filed no
    incident at all. That is the exact call `RunGuard._stop` makes on a live task. The 19 tests
    below never reached it because none of them passes a `work_item_id` that exists.
- **The dispatch gate does not know the lane yet.** `budget check` runs before the claim, on
  purpose, and the claim is what decides the lane. The runner passes `--lane` only when an agent
  has exactly one configured lane; fleet and agent ceilings always apply. A lane ceiling on a
  multi-lane agent still brakes, one run later, through `budget charge`.
- **It does not know what a run will cost before it runs.** The preflight gate refuses work when
  the meter is already at the ceiling; it cannot refuse the run that will cross it. `RunGuard`
  covers that case, mid-run, which is why both exist.
- **The mid-run guard watches the METER, not this run's own spend, and that is a limit of the
  engine rather than a choice.** Claude Code's stream-json carries `total_cost_usd` on the
  terminal `result` event and nowhere else: measured over 40 real stream files, the only
  USD-bearing keys anywhere are `total_cost_usd` and `modelUsage.*.costUSD`, both on `type=result`.
  Every mid-run event carries token counts and no price. So a run's own spend is not observable
  until it has already been paid, and `RunGuard.watch` stops a live run on what IS observable:
  the shared meter moving under it, which is what a sibling terminal charging or an operator
  typing `budget stop` does. On a six-terminal fleet that is the common case, and it is the
  difference between one over-spend and six. Turning token counts into dollars would need a price
  table keyed by model and date; `ingest/ingest/transcript.py` declines to own one for the same
  reason and nothing else here owns one either.
- **It does not estimate remaining subscription quota.** Nothing exposes it. That is
  `rate-limits.md`'s territory and is deliberately left there.
