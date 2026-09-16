# What this lane RAISED rather than made

Six items. Each one is a change to something this lane does not own, so each is written down
with its evidence and left for the owner. Building any of them here would have been faster and
would have put an operator-gated edit, a rule divergence and an unclosed security hole into a
diff nobody reviewed.

---

## 1. `human_minutes_est` on the queue-item shape. OPERATOR-GATED. NOT MADE.

**Where it belongs:** `entities/rules/operator-human-queue-contract.md`, "The item shape".

**Why it is needed:** the system has no field for human cost. `effort` in
`entities/rules/signal-vocabulary.md` is the *agent's* token cost, and D8's sweep has now made
seven of the nine signals real `work_item` columns, which makes the confusion easier rather than
harder: `effort` looks like a cost field and is the wrong one.

**Why it is gated:** that file is an `_system`-adjacent rule and D8 has already swept it once in
this program (supersession note dated 2026-08-16, substrate remapped to the Infinity OS runtime,
discipline unchanged). A second edit in the same day by a different lane, to add a field the
rule's author has not seen, is the shape that produces two versions of a contract.

**What was built instead:** `queue/human_queue/tiers.py` computes the tier from five inputs that
already exist -- `item_class`, `template_id` null-or-not, `prepared_context_link`,
`recommended_option`, and the reversibility floor. `brain.queue_item` deliberately has no
`human_minutes_est` column, and the migration says so in a `COMMENT ON TABLE`.

**If it is added**, it should be *computed and calibrated*, never self-declared by the producing
agent: the `not fast` demote already writes the correction into `brain.queue_calibration`, and
that table is the training input for an estimate the system makes for itself.

---

## 2. The tie-break diverges from `entities/rules/priority-model.md`. RECORDED, NOT RESOLVED.

`priority-model.md` says *"ties break deterministically by age (oldest first) for reproducible
ordering."* The D6b brief says *"deterministic tie-break by id."* Both are deterministic; they
are not the same order.

**This lane implements the brief (id), and the reason is not deference:** age is already a term
in the sum (`w_age_per_day`, the anti-starvation term). Breaking ties by age as well would count
the same fact twice, once continuously and once discretely, and the second count would be
invisible in the decomposition the operator reads.

**Who owns it:** whoever next edits `priority-model.md`. Either the rule gains a sentence saying
the human queue breaks ties by id because age is already a term, or this lane is wrong and
should change. It is one line either way, and it is a rule in git, so it is not this lane's line.

---

## 3. An agent passing the string `operator` is not distinguishable from the operator. CLOSED at tasks 0290 and 0313.

**What this item said while it was open, kept verbatim because the reasoning was wrong in an
instructive way:** the acceptance gate refuses an empty decider, a decider that is a name in
`brain.agent`, and a process with `SWARM_PARENT_TASK` set; none of the three closes
impersonation, "because v1 has no authentication and no verb design substitutes for one".

That last clause was the error. v1 DID have authentication and this item did not know it:
migration 20 had already made human identity a database LOGIN through `brain.human_role` and
`brain.current_human()`, for `brain.work_item.actor_type`. The answer was one table over the
whole time this item read as unclosable, and item 6 below spotted it (`what would close it:
migration 20's brain_operator login, the same answer item 3 needs`) without item 3 being
revisited.

**What it cost.** The V9 acceptance run (task 0222) measured the consequence on real drafted
options from a `brain_runtime` connection with `SWARM_PARENT_TASK` cleared: `by='Andrew'`
dispatched, `by='zzz-not-a-person'` dispatched and was booked `actor_type='human'`. V2 invariant
1 -- "a drafted option cannot dispatch without a human choosing it" -- did not hold, and the
`brain.agent` test named above is why: it refuses the names that ARE agents, so every name that
is not one passes, and the agent picks the name. A denylist read as a gate.

**Closed by** `queue/schema/0015_recommendation_human_login.sql` (schema version 32) and
`queue/human_queue/transitions.py::recommend_accept`, task 0290. An acceptance is now refused
unless `brain.current_human()` names the connection, and `decided_by` is the database's answer
rather than the caller's `by=`. Proved by `engine/tests/test_recommendation_human_login.py`,
which tries an unregistered name, an invented name and the literal string `operator` through the
verb AND straight at the table as `brain_runtime`.

**And closed on the other half, by task 0313:**
`queue/schema/0016_recommendation_rejection_login.sql` (schema version 33) and
`queue/human_queue/transitions.py::recommend_reject`. What 0290 wrote here was "still open next
door: `recommend reject` takes its decider from `by=` with no login gate at all", and that
sentence was true when it was written and false by the time it was committed: 0313 landed in the
same commit (89454b6). A rejection still dispatches nothing, so it was never invariant 1. What it
moves is THE FALSIFIER. `brain.queue_acted_on` counts `state = 'rejected'` grouped by
`template_id` and reads neither `decided_by` nor `actor_type`, so an agent able to write that
state prunes a playbook the operator never rejected, and the reader never looks at the column a
cheaper fix would have made honest. Both transitions now take `decided_by` from
`brain.current_human()`, and `queue reject --by` is REQUIRED rather than defaulted to the literal
string `operator`, which is what the forgery rode in on: a bare `queue reject rN --reason x` from
any process used to record the operator as the rejecter.
`engine/tests/test_recommendation_human_login.py` proves both halves in one file: fourteen
tests, seven on the acceptance side, six on the rejection side and one summary assertion over
both, each half tried through the verb AND straight at the table as `brain_runtime`.

**What the two migrations do NOT close, which both their file headers state and this item did
not:** the host residual. On a `local-attended` host every credential file sits in one 0700
directory under one UID, so an OS process that can read the operator's secret can connect as the
operator. That is `store/SECRETS.md`'s residual for every role, it is file permissions rather
than the database, and both gates are exactly as strong as `actor_type = 'human'` on
`brain.work_item` is, and no stronger. What they close absolutely is the case that actually
exists: a process holding the `brain_runtime` credential, which is the login every agent surface
is configured with, has no route to an accepted OR a rejected recommendation under any decider
name.

**Where these two migrations actually are, because it changes what a reader may assume.** They
are IN THIS TREE AND APPLIED TO LIVE. That is measured, not assumed: on 2026-08-19 at 04:01Z,
over a `brain_runtime` connection to database `brain` on port 5432, `brain.schema_migration`
holds 33 as its maximum, over 33 rows, and both target versions are present by name (32 =
`0015_recommendation_human_login`, 33 = `0016_recommendation_rejection_login`). The operator
applied both by hand from a plain shell at approximately 03:2xZ, answering the question task 0323
put to him; two agents have since re-read the live ledger from separate connections with separate
scripts and got the same two numbers. **This paragraph said the opposite until 2026-08-19 and was
right when it was written**: it landed at 03:50Z in commit `8857ecb`, about twenty minutes after
the world moved under it, carrying a state note that was true when its brief was written. Item 3
is closed either way, because the refusal a caller meets today is the PYTHON verb's: it calls
`brain.current_human()`, and live has had that function since migration 20. What applying 32 and
33 adds, and live now has, is the same refusal AT THE TABLE, where a raw-SQL or superuser writer
cannot route around the verb. Do not check whether they are applied by asking whether the trigger
exists: trigger and function `recommendation_human_decider` have been on live since
`0007_queue.sql` and migration 32 only replaces the body, so the name is present either way and
the probe reads as a false pass. The discriminating probes are two, and both were run against
live, 2 of 2, 2 passed and 0 failed: the body, `prosrc LIKE '%current_human%'` on
`recommendation_human_decider`, which matched over 1 function row of 4115 chars, and the presence
of `recommendation_rejection_decider`, which migration 33 creates new and which is the one name
whose existence IS discriminating, found 1 of 1. Script:
`outputs/2026-08-19-T1-0337-raised-live-state/probe_live.py`, SELECT only.

---

## 4. `swarm ask` refuses correctly and renders the refusal as a traceback. POSTED.

`swarm_engine/cli.py:cmd_ask` catches `VerbError` only, so a refusal raised by a database
trigger reaches the operator as a Python traceback with the real message at the bottom. The
refusal itself is correct and the exit code is non-zero; only the rendering is wrong.

That file is D4's tree. Posted as adjacent work with `--parent 0105` rather than edited here,
and asserted in `queue/tests/test_ask_default_refusal.py` so the day someone fixes it, the test
says so out loud instead of passing quietly.

---

## 5. The deadline band diverges from `priority-model.md`. REMOVED HERE, RAISED FOR THE RULE.

`priority-model.md` carries a deadline jump above the score, and this lane implemented it as
`urgency == 'high'` because that is the only input there is. **`brain.work_item` has no due-date
column**, checked against `information_schema` on the live store on 2026-08-17: not `due_at`,
not `deadline`, nothing. So the band was a second reading of `urgency`, which is already worth
`w_urgency * 2.0` in the sum.

**Why the double count was not merely untidy.** `rank()` sorts by `(band, -score, id)`. A band
is absolute, so the second reading did not add to urgency's weight, it replaced every other term
with it. Measured on the live store at 12:19Z on 2026-08-17, before the fix:

```
   1. 0067  9.004      band 1, urgency high
   ...
   5. 0058  5.011      band 1, urgency high
   6. 0070  7.739      band 2, urgency medium, bumped +2 by the operator
```

The operator bumped 0070, was told it was worth +2, watched the score go 5.751 -> 7.739, and
watched it stay at #6 under a 5.011. **The bump is his only lever over ranking and the band had
taken it away**, while the bump log went on recording that he had used it. That is worse than
having no bump: the weight-tuning dataset says the lever was pulled and the operator learns the
lever is decorative.

**This lane removed the band** rather than raising and waiting, because the alternative was to
leave a known-broken operator control in place, and because the fix is a deletion of a term
whose only input is already summed. `queue/human_queue/rank.py:jump_band`, tests
`test_a_bump_lifts_the_item_above_the_neighbours_it_outscores` and
`test_hard_rules_sit_on_top_of_the_score`. Band 0, the live idle agent, is untouched and is the
one fact the sum does not carry.

**Who owns the rule:** whoever next edits `priority-model.md`. Either it gains a sentence saying
the deadline jump needs a deadline field before it can be a band, or this system gains the field
and the band comes back reading THAT. It must not come back reading `urgency`.

---

## 6. An agent passing `--who operator` books its minutes as the operator's. OPEN. (task 0279)

`brain.time_entry` is the operator's stopwatch and the number it produces -- how many hours of
his life this backlog costs -- is the one the whole program is judged by. The verb refuses a
`who` that is a name in `brain.agent`, so the fleet cannot book its own minutes under its own
name. **It does not refuse an agent that passes the string `operator`**, and `who` still defaults
to that string (`queue/human_queue/time_ledger.py`, `time_start` and `time_stop`).

**The reason this item used to give for that is now known to be wrong, and the item is still
open anyway.** It read "for exactly the reason item 3 above gives about `recommend accept`: v1
has no authentication and no verb design substitutes for one". Item 3 above retracts that clause
as its own error: v1 DID have authentication, and tasks 0290 and 0313 have since spent it twice,
on `recommend accept` and on `recommend reject`. So this item is no longer UNCLOSABLE-in-v1, it
is CLOSABLE AND NOT CLOSED, which is a different item with the same state. What has not happened
is the decision below, not the mechanism.

**Why this one was NOT given the `SWARM_PARENT_TASK` process check that `recommend accept`
carries.** That check is right there because acceptance *executes work*; a wrong acceptance
spawns a task. A wrong time entry produces a wrong number, and this ledger already has two
instruments aimed at exactly that -- coverage, which says how much of the work the measurement
actually watched, and the correction path, which supersedes a bad entry without deleting it. The
process check would also have made the feature untestable end to end by the fleet that builds it,
which is how a gate acquires an environment-variable-shaped hole anyway.

**Measured, not argued:** the acceptance run for task 0279 wrote its entries to live under
`who = 'acceptance-0279'`, and every figure in `queue depth` and `queue ceilings` defaults to
`who = 'operator'`, so those rows are visible in the ledger and in none of his numbers. That is
the mitigation available without identity: **make `who` the key every figure is computed per**,
so a wrong name is a wrong bucket rather than a corrupted mean.

**What would close it:** migration 20's `brain_operator` login, the same answer item 3 needs.
`store.transitions._login_for` already redirects a `runtime` verb to the operator's connection
when it declares `actor_type = 'human'`; a stopwatch verb that declared the same would be refused
by `_connect` in any process that does not hold his credential. That is a small change resting on
a decision about who may write his time, which is his.

That sentence is left in the words it was written in, because item 3 above quotes it, but "the
same answer item 3 needs" has since been overtaken: item 3 does not need it any more, it has it,
in `queue/schema/0015_recommendation_human_login.sql` and
`queue/schema/0016_recommendation_rejection_login.sql`. Those two are the worked example for this
one, including the price: on a host with no operator credential the gated verb is gone, because
`_connect` raises before any SQL is sent. `recommend accept` and `recommend reject` pay that
knowingly. A stopwatch that paid it would stop the fleet from booking its own acceptance-run
minutes, which is how task 0279's run measured itself, so the decision is not only about
forgery.
