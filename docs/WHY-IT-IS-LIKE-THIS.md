# Why it is like this

For the person who has forgotten, which in three months is everyone including whoever built it.

Every entry below is a decision that looks arbitrary until you know the incident behind it. The
answers exist in migration headers, task ids and lane reports; this file is the one place they are
collected. Where a number appears, it was measured on the date given, and the file that measured it
is named so you can measure it again rather than trust this one.

---

## 1. `done` is the agent's report. Acceptance is a separate human act.

`done` means an agent said it finished. It has never meant a human agreed. `reopen` is the
rejection verb, and `accept work` is the acceptance verb, and they are different acts by different
parties.

The product turns on this distinction, and it had never been exercised.
**Measured 2026-08-27 on live `brain`: of 122 completed items, 4 were ever accepted, and all four
were probes of the acceptance mechanism itself on 2026-08-16.** The operator declared task
bankruptcy that day rather than review the pile. The note is on every cancelled row's thread,
append-only, in his own words:

> BANKRUPTED 2026-08-27, NOT ACCEPTED. 118 of 122 completed items were never reviewed by a human;
> the only 4 acceptances in this store's history were probes of the acceptance mechanism itself on
> 2026-08-16. The fleet that produced these has been stopped 7+ days. This is a decision about the
> QUEUE and not a judgement on the work: the agent's own report survives on this thread above, and
> the artifacts, runs and receipts are untouched.

A second note covers the open half: 149 open rows, all held for the operator, none claimable by any
agent, 95 posted by agents that were already stopped and had not moved in over seven days.

**Why keep the distinction after that.** Deleting it does not create review time, it deletes the
record that review did not happen. The bankruptcy is legible precisely because acceptance is a
separate column: `state = 'done'` and `accepted_at IS NULL` is a countable population, and 118 was
countable. Collapse the two and the same 118 rows read as finished work.

**What was narrowed instead, and it ships disabled.** Acceptance is automatic for an item that is
non-`external`, non-`canon_touching`, reversible, and whose definition-of-done checks passed. That
narrows *who* accepts, never *whether* acceptance happened: an auto-accepted row records that it
was auto-accepted, with the signal values that made it eligible, so the class can be audited and
reversed wholesale. It is gated behind one week of measurement in which the operator reviews
everything as today and the runtime records how often he would have disagreed
(`engine/swarm_engine/accept.py::disagreement_report`). Shipping it enabled before that measurement
converts unreliable self-reported completion into fabricated completion at fleet scale.

Full record: `outputs/2026-08-27-commander/BANKRUPTCY-EXPORT-2026-08-27.md`, and a `pg_dump` at
`brain-20260827T121725Z.dump`.

---

## 2. The acceptance gate is a database login, not a string.

`brain.current_human()` is one line of SQL:

```sql
SELECT human FROM brain.human_role WHERE role_name = session_user
```

`session_user` is fixed at authentication and unreachable from any SQL a client sends. That is the
whole property.

**Why it had to change.** Migration 22 guarded the *shape* of an acceptance and left the identity
test as "is this name in `brain.agent`", which **fails open**: every name that is not an agent
passed. Measured on 2026-08-27 from `brain_runtime`, the login every agent surface in this fleet
connects as:

```text
DOOR 1  brain.work_item.accepted_by      6 of 6 forged names ACCEPTED
          'zzz-not-a-person'  ACCEPTED   <- the string the V9 adversarial run used
          'operator'          ACCEPTED   <- an agent recording the operator's own decision
DOOR 2  brain.recommendation.decided_by  0 of 6 accepted, refused since migration 32
        the two doors disagreed on 6 of 6 names
```

The string `zzz-not-a-person` is not hypothetical. The 2026-08-16 adversarial run used it to
dispatch real work, booked `actor_type='human'`. The six-name probe is a registered regression test
now: `engine/tests/test_multi_user_subject.py`.

Migration `0036_work_item_acceptance_is_a_login.sql` closes door 1 by composing
`brain.current_human()` rather than building a second notion of who is human. Re-verified by hand
on 2026-08-27 against `brain_demo`: a raw `UPDATE` setting `accepted_by = 'zzz-not-a-person'` on a
**Postgres SUPERUSER** connection is refused, naming the login.

**The one carve-out, and it claims no human.** `brain.auto_acceptor()` returns the literal
`auto-accept`, which the auto-accept rule writes when it accepts an item by itself inside the
`done` transaction, on a fleet host with no operator credential. The sentinel has exactly one
producer so the exemption cannot widen into "any string starting with auto".

**What this does NOT close, stated because a guard that oversells itself is worse than none.**
Every credential on this host is a 0600 file in one 0700 directory under one UID, so an OS process
running as the operator can connect as the operator. The database no longer takes a client's word
about who it is; file permissions are what separate the operator from a process running as the same
user. That is a secret-backend question, not a trigger question. See `store/SECRETS.md`.

**The ceiling is stated so it is not discovered.** One Postgres login per human, good to **twelve**.
A thirteenth reopens the decision. It is never a quiet migration. Role management is user management
here, and past roughly a dozen that becomes the job.

---

## 3. Two forgery doors, and the predicate that finally worked.

Both were found and closed on 2026-08-27. The second had no console in its path at all.

**The two clicks.** In the Judge tier, press `Send back` on an agent's finished work, then press the
control that has silently replaced `Accept work` on the same card. `work_item.result`, the column
every later surface reads as *what the agent reported*, now holds the operator's sentence, and the
row leaves the queue as done, so the rework he asked for two seconds earlier never happens and
nothing says so. Reproduced in a real browser 2 of 2 at commit `6b5fa01`.

**Why the existing guard could not see it.** `web/rooms.py::assert_allowed_on` permitted `done`
where `NOT agent_claimable AND coalesce(claimed_by,'') = ''`. Both of those columns are statements
about a row's **FUTURE**. `agent_claimable` says whether the fleet may take it. `claimed_by` says
whether an agent is holding it at this instant, and `reopen` clears it **by design**, as part of
sending work back. Neither can see the past.

**The predicate that works is "has an agent ALREADY worked this row".** Two identity-free arms: a
`brain.run` row exists, or a `claim` sits on the append-only `brain.thread`. Neither can fire on the
operator's own work, which is what makes this a guard rather than a ban: `run start` is the runner's
verb and no console path reaches it, and `claim` is refused unless `agent_claimable` is true, which
migration 26 forces false on every `actor_type='human'` row.

Four candidate predicates were measured on live and rejected, which is why the header is worth
reading:

| Candidate | Live reading | Why rejected |
|---|---|---|
| `posted_by` is not the operator | 140 of 172 | free text any caller sets. 41 say `admiral` over rows no agent touched |
| the reporter is in `brain.agent` | 4 of 172 | that table holds fleet terminals only. This program's own lane subagents are not in it |
| `actor_type` | 0 of 172 | nullable, and unset is a real state |
| a `brain.run` row, or a thread `claim` | 7 of 172 each, 14 in union with a third arm | identity-free, and cannot fire on the operator's own work |

**Why a trigger and not only the Python guard.** `web/rooms.py` is not on every path.
`swarm done <id> --agent operator` reached the identical forgery with none of it, measured. So
`migrations/0042_done_is_never_filed_over_an_agents_report.sql` refuses the same write for every
caller, including ones nobody has written yet. Re-verified by hand on 2026-08-27 against
`brain_demo`: a raw `UPDATE` on a superuser connection is refused, and the error names the run count
and the claim count it found.

**The third arm is deliberately not in the trigger.** "Reported on by somebody who is not the human
asking" is identity-bearing, and a trigger does not know who is asking. An identity-free version of
it would refuse the operator's own second `done` after his own `reopen`, which is the loop migration
22 exists to protect. So arm 3 lives in `web/rooms.py::agent_work_on`, and
`web/tests/test_done_is_not_a_forgery.py` asserts the two implementations agree on the arms they
share, over the whole table, so they cannot drift.

**What it does not close.** `swarm done <id> --force` by the operator on an ACTIVE row an agent is
holding. That is a different shape: it is signed, it names the displaced holder on the thread, and
the console cannot reach it at all.

**And it changed nothing in history.** Measured against every `done` event this store has ever
written: it would have refused **0 of 127**. The forgery had never been committed. It is refused
before the first time.

---

## 4. A double routine fire is impossible in the schema, not unlikely in the caller.

A routine that fires twice posts the same work twice, forever.

The reflex is `SELECT ... FOR UPDATE`. It does not work here, and the reason is worth carrying:
**`FOR UPDATE` locks a row that exists, and a routine occurrence does not exist until the first fire
creates it.** There is nothing to lock. Only a unique index refuses a second one.

```sql
CREATE UNIQUE INDEX routine_run_one_per_slot
  ON brain.routine_run USING btree (routine_id, scheduled_for);
```

Verified present on 2026-08-27. The occurrence row is written in the same transaction as the post,
so a second caller for one slot gets 23505 and the transaction that would have posted a duplicate
cannot commit. `engine/tests/test_routines.py` runs eight concurrent processes at one slot with the
constraint dropped, then with it in place.

**Why the clock is a systemd timer and not a scheduler process.** The routine OBJECT lives in the
store so it can be listed, disabled and shown to have fired. The CLOCK stays `brain-routine-tick.timer`,
a `oneshot` that runs `swarm routine tick --fire` and exits, so there is no resident process with its
own opinion about what time it is.

**The kill switch is asymmetric on purpose.** `swarm routine disable` works from a process holding no
human credential at all. `swarm routine enable` is refused without one. A kill switch that can be
refused is not a kill switch; arming a standing scheduled dispatch is a different act and needs to be
a human.

---

## 5. The room allowlist is keyed on the URL, never on the payload.

Task 0147, from the 0119 adversarial pass. The allowlist was airtight and the room it keyed on was
not: `room` was read from `request.form`, so a POST from the Study page saying `room=queue` got the
Queue's verb set and ran `reopen` on a live task.

**An allowlist keyed on an attacker-supplied string is an allowlist keyed on nothing.** The room is
now a URL segment and the CSRF token is scoped to the room that issued it.

The same argument rules out `ProxyFix` for the phone: rebuilding `host_url` from
`X-Forwarded-Proto` trusts a header the request carries. `CONSOLE_ORIGIN` comes from the environment
the process was started in and can never come from a request.

---

## 6. Study calls zero verbs, and that is proven rather than described.

`ROOM_VERBS["study"]` is an empty frozenset. `web/tests/test_allowlist.py:test_study_calls_zero_verbs`
enumerates **every verb registered anywhere in the runtime** from `store.registered()`, dispatches
each one with `room=study`, and asserts both that it was refused and that `store.apply` was never
entered. `test_the_write_door_itself_refuses_study` does the same through the real HTTP endpoint,
holding a valid Study session and token, so the refusal is the allowlist's and not the session
check's.

Why: a surface that cannot act cannot bias a decision. Study is the room where you look at what
happened, and looking should not be adjacent to a button.

---

## 7. The queue is a window, not a backlog. Tiers sort attention, not minutes.

Seven items a tier by default. The tier chip carries the **total** and the list says how many are
below the window. A card count rendered as a tier count would report a plan as if it were the debt.
Nothing leaves silently: blocked items, deferred items and everything under the window are counted
and named.

The three tiers sort **cognitive mode**:

```text
Decide   act from prepared context, nothing opened
Judge    open one thing, evaluate, give feedback
Shape    generate: scope, rethink, decide something novel
```

Minutes are the symptom. Two five-minute items are not interchangeable: one answered from a card,
the other needing a repo open, cost the same clock and very different attention.
`human_minutes_est` does not exist and `queue/human_queue/tiers.py` refuses to invent it, because a
field invented to have something to measure produces a measurement that agrees with the invention.
`TIER_CEILING_SECONDS` carries one number, 120 for Decide, because that is the only ceiling anyone
has ever claimed; Judge and Shape are `None`, not a plausible ten and thirty.

**The reversibility floor is not a tie-break.** An irreversible item never sits in Decide whatever
its estimate, because the failure mode of a fast lane is not slowness, it is an irreversible act
taken from a card in eleven seconds. Reversibility is `low` when unset, so an unassessed item cannot
reach Decide by omission either.

---

## 8. Deep work is absent from the markup, not hidden with CSS.

With deep work on, the fast pane, the burning-item line and the run-the-stack strip are **not
rendered**. Only the count survives, in the deep header's own note.

A card that is `display:none` is still read aloud by a screen reader and still shipped down the
wire. The test checks DOM node counts rather than computed styles, for that reason. Deep work is a
cookie the **server** reads, because the fast pane is rendered server side and a client-only deep
work would still send the interrupting HTML.

Re-measured 2026-08-27 on `/queue?tier=judge`, `deep=0` against `deep=1`: fast-pane text 714
characters against 0, `.runbtn` 2 against 0, `nav.rooms` 1 against 0, `.modepill` 1 against 0, and
the surviving count reads `3 waiting`.

---

## 9. Poll and patch. There is no push path and there never will be.

Every three seconds, `GET /api/patch/<room>` returns each region's HTML, rendered by the same
template that rendered the page, and the client swaps what changed while skipping the region holding
the focused input.

No SSE, no websocket, no service worker, no manifest, no Redis, no chat. Checked in the browser
rather than in the source: `window.EventSource` and `window.WebSocket` are replaced with
instrumented wrappers before any page script runs, then the page sits on `/queue` for seven seconds,
more than two poll cycles. Neither is constructed.

No badge, no notification, no inbox. **The 7am moment is opening the page.** A red badge on a
personal surface is an interruption with no verb attached, and item 7 of the must-not-build list is
the argument.

A favicon was added on 2026-08-27 because every cold load took a 404 on `/favicon.ico`. A favicon is
not a PWA: it declares nothing installable, registers no worker and adds no manifest.

---

## 10. The five decisions of 2026-08-27, and what reverting each one costs.

Answered by a delegate under an explicit standing delegation, recorded as delegate-decided, each
written to be reversible by one edit. Full text:
`your-brain/projects/infinity-os-consolidation/decisions/2026-08-27-row-0386-five-decisions.md`.

1. **Multi-user: reversed, narrowly.** Several named humans on one self-hosted instance is in
   scope. Multi-tenant, plugin marketplace, hosted SaaS and public sign-up stay out. Reverting
   costs a down-migration, not a rebuild.
2. **Identity: one Postgres login per human, ceiling twelve.** Settled by decision 1 rather than by
   preference: the case for application users is that database logins do not scale past a few
   people, and decision 1 just ruled that scaling past a few people is out of scope. Rebuilding the
   guarantee in application code puts it back in the layer where it has already failed once.
3. **All eleven console prohibitions kept as written.** The default is keep, the burden is on any
   proposal, and a proposal must name the incident the prohibition was written against. "It would
   feel nicer" is not that.
4. **Queue falsifier: clock restarted 2026-08-27, hard review 2026-09-10.** The two weeks never
   began, because half of what the falsifier judges had produced zero rows. Only the operator's own
   use counts; a delegate working the queue is never counted as dogfooding. The conflict of
   interest is named rather than hidden: the party ruling that the console may be extended is the
   party that wants it extended, and the protection is the date.
5. **The daily `/buddy` ritual: stated end. The points ladder: dormant.** Nothing deleted. A ritual
   that ran four days in August and then went silent for seven is not a ritual with a gap in it.

---

## 11. Why "print the denominator" is a lint and not an aspiration.

A check reports on the comparisons it MADE. It never reports on the comparisons it SHOULD have
made. Anything that shrinks the comparison set to zero, or points it at the wrong column, reads as a
pass.

**Zero compared is a hard failure, never a pass.** `engine/bin/denominator-lint.py` walks every
shell and python script in the repo and refuses a NEW file that prints a counter verdict without a
zero-denominator guard. `policy/denominator-baseline.txt` is the ratchet floor of files that were
already unguarded when the rule landed; it is debt, and it may only shrink.

Five ways the set has actually gone to zero on this program:

* a subprocess ate the loop's stdin, so the restore verifier compared 1 table and printed
  `RESTORE VERIFIED` over 20;
* the tool was not on the PATH, so a check shelling `tailscale status` compared nothing and reported
  a clean negative;
* the probe never reached the thing under test: twelve requests that all got 403 at the CSRF gate
  are twelve measurements of the gate;
* setup did not happen, so a script printed `rule HELD` over an empty world;
* the predicate could not see the object: `git diff --quiet HEAD -- <untracked-path>` prints
  COMMITTED, because an untracked path appears in no diff at all.

Two consequences worth stating on their own. **When the control did not fire, the verdict is
INCONCLUSIVE, not HELD.** And **a wrapper's exit code is not the child's.**

---

## 12. A guard nobody has watched fail is not a guard.

Demonstrate the failure first, then the fix, and quote both runs. Every guard in this repo that is
worth anything has a paired before and after: the paused banner measured at `--dmg` in both themes
and then at `--wait` in both themes; the six-name forgery probe accepting 6 of 6 and then 0 of 6;
the routine race leaving duplicates with the index dropped and none with it in place.

The corollary is that a rollback is a thing that has run, not a paragraph. Migrations 36 and 42 both
carry a stated rollback that was executed and then re-applied.

---

## 13. Why a suite may declare NOT RUN, and why 77.

A suite's verdict is a claim about the code. The moment its verdict also depends on state it does not
control, the red it prints is ambiguous, and a red nobody can read correctly stops being read. That
cost is paid by the one red that was real.

Three verdicts, not two: GREEN, RED, and NOT RUN, the last declared by **exiting 77**, the automake
convention for skipped, unmistakable against this repo's `0 ok / 1 failed / 2 zero denominator`.

A NOT RUN somebody on this host can fix still turns the runner red and names the remedy. One that is
structural to the verification context is printed, counted, and stays green: `test-infra-failure.sh`
reads a blob out of git history and this program mandates testing from a `git archive` export, which
has no `.git`. Both rules are correct and permanently incompatible, and a gate that fires on every
honest run for a reason unrelated to the thing it gates stops being read.

**Before you classify a red as an environment dependency, check the suite reads the environment at
all.** `test_utilization_gauge.py` was filed as one and was not: it wrote its own fixture and stamped
it with a rate-limit window pinned to the literal `2026-08-23T18:00:00Z`. On that date at 18:00Z four
assertions went red. **A fixture with a date in it is live state wearing a fixture's clothes.**

Full rule: `docs/SUITE-INPUT-RULE.md`.

---

## 14. Why the commit rule is "only paths your own task produced".

On 2026-08-18 at 12:01:13Z, commit `45f1a3f` landed 301 files and 51,433 insertions and was pushed.
78 of those paths were the live, mid-task work of 15 other tasks across all 8 running agents,
committed under a message about a VPS clone, so `git log -- <path>` now gives the wrong reason for
every one of them.

The lane that did it named all 301 paths explicitly and proved the staged set equal to its intended
list. It broke rule 1, not rule 2, which is why `docs/COMMIT-HYGIENE.md` leads with rule 1. Building
a path list from `git status` and feeding it to `--pathspec-from-file` names every path and is still
`git add -A`.

---

## 15. Why the ledger is read from the database, never from a directory listing.

The migration ledger is **global across three schema directories** and each file declares its own
version inside itself. Filename numbers are per-directory and do not equal ledger versions:
`queue/schema/0015_recommendation_human_login.sql` is ledger version 32.

This is dangerous rather than untidy. `scratch-db.sh` applies only the ledger versions a database has
not recorded, and every migration writes `ON CONFLICT DO NOTHING`. A new migration declaring a
version that is already present therefore applies **nothing** and reports a clean build. A silent
no-op that reports success is the worst available failure here.

The repo knew this before a brief contradicted it: `queue/schema/0015_...sql:104` reads *"Pick the
next version by reading `brain.schema_migration`, never by listing a directory."*

**And that rule is not sufficient on its own, which took a second incident to learn.** It stops you
colliding with a version that is held. It does not stop you landing in a **hole**, and reading the
ledger is precisely what makes a hole look available. On 2026-08-27 the commander brief assigned
lane E versions 36 to 39; lane E wrote 36, 37 and 38 and left 39, and lanes F and J numbered past it
to 40, 41 and 42. Live `brain` then read 41 rows with a maximum of 42 and one number absent, which
is a ledger a lane can read correctly and still be misled by.

A file at a hole is worse than a file at a collision, because it does not do nothing, it does one
thing in two different places. `migration_list` orders by recorded version and `cmd_migrate` applies
only what is missing, so a file at 39 applies **between 38 and 40** on a fresh `create` and **after
40, 41 and 42** on a `migrate` of a store that already recorded them. One file, two application
orders, and the fresh build is the order every suite here tests against. A suite green on an order
the live store will never see is the failure this repo has already paid for once.

So the rule has a second half: **take `max(version) + 1`, never the lowest free number.** It is in
`docs/CHANGING-IT.md`, and `engine/bin/scratch-db.sh ledger` now prints it as an answer rather than
as advice, with any hole named beside it, in a door that touches no database and is already the
thing a lane is told to read.

Version 39 itself is fenced rather than left open, by
`migrations/0039_a_reserved_number_is_not_a_migration.sql`. The fence is cheaper than a fill and it
is also the only safe thing to put there: the file carries **no DDL**, so it has nothing to order
against 40, 41 and 42, and it is the one file at that hole whose two application orders are provably
identical. Its ledger row's `name` states that it is a reservation, so no row claims a migration that
did not run.

Related, and it changes direction: `docs/SCHEMA-TOLERANCE.md`. There is one tree of migrations and
130 databases on this host carrying a ledger. Your code is one checkout; the store it opens is
whichever of them `BRAIN_PG_DB` names. **The direction of the gap is not stable and must not be
encoded.** For four hours on 2026-08-18 live was at 27 while `migrations/` declared 31; three
minutes after the operator applied 28 through 31, the direction inverted.

---

## 16. Why hosting is local, and what that costs.

The store, the dispatcher, the runner and the console all run on the operator's machine under WSL.
Postgres data lives at `~/.brain-postgres` on native ext4, never under `/mnt/c`.

This deletes step zero rather than deferring it: no VPS provisioning, no cutover, and no per-lane
workdir decision, because workers already run against local working trees.

**It is not a free win and the first version of this paragraph understated it.** Cadences do not fire
and the fleet does not run while the machine sleeps. Freshness review, contradiction review, wager
horizons and standups all stop with the clock. Migration to a VPS later is a cutover, not a mirror:
exactly one authoritative instance per live surface.

---

## 17. Why the product name is absent, and where the runtime sits.

Naming was resolved by altitude, not by a rename. **Infinity OS** is the umbrella program.
**Infinite Brain System** is the product. **The brain** is the knowledge layer. **The runtime** is
this repo, and its shipped name is open. The repo name is functional and cheap to change.

The positioning claim, which is why this is not a Paperclip rebuild: every runtime control plane on
the market is commodity, and what nobody else has is the brain to attribute to. Recommendations are
backed by playbooks in git rather than strings in a database, and the promotion path from runtime
state to canon is native rather than an integration.

The operator's own framing, which is the clearest statement of what it is for: **what double-entry
did for accounting, this does for AI context.** Nothing happens without a second record of what
caused it and what it changed. The breach detector is a join, in exactly the way a trial balance is a
join.

---

## The falsifiers, stated before building so the result can disconfirm

* **The queue falsifier.** If after two weeks of the operator's own dogfooding he is still keeping a
  separate list, the human-management premise is wrong and the console should not be extended.
  Clock restarted 2026-08-27, **hard review 2026-09-10**. It fires unless three things are true: the
  acted-on rate has a real denominator, the daily pass is measured against the ritual it replaces in
  seconds both ways, and the parallel list's 163 open items have moved into the queue or been
  consciously closed.
* **The recommendation falsifier.** If under roughly 30 percent of recommendations are acted on, the
  recommendation layer is a fantasy and should be cut back to events plus paging. Which denominator
  that means, accepted over raised or accepted over decided, is an open question and a lane may not
  answer it. Row `0388`.
* **The consolidation falsifier.** If the ported dispatch could not pass the concurrency test within
  one working day, the storage swap was not the cheap move it appeared to be. It passed.

---

Also here: `README.md` for what this is, `docs/OPERATING.md` for running it,
`docs/CHANGING-IT.md` for editing it, `docs/KNOWN-GAPS.md` for what is missing.
