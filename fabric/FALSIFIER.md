# The phase-1 pilot's falsifiers, checked before the pilot was built

`lane-2-subscriber-contract.md` section 5.3 names three falsifiers for `pipeline-failure-observer`,
each with a test that runs **before** the build rather than after it. The D5 brief names F3 and
asks for the count either way. All three are reported here, because checking the named one and not
noticing that a different one fired would be the more expensive mistake.

**Result: F3 passed clearly. F1 fires, and it fires for a reason created after the sprint.**

---

## F3 — "the failures are too rare to produce evidence" — **NOT falsified**

> *Test before building:* count qualifying pipeline failures in the last 90 days from existing
> logs. **Fewer than 4 in 90 days falsifies the choice**, because a 30 day phase-1 window would
> then be expected to produce roughly one event and one observation is not evidence.

**Window:** 2026-05-18 to 2026-08-16.
**Source:** the operator's own ETL health reviews in `your-brain`, which are
the existing log of record for these pipelines. Counted at the **episode** grain, which is the
conservative reading: one credential expiry that produced 140 worker failures is counted once.

| # | Date | Episode | Evidence |
|---|---|---|---|
| 1 | 2026-06-25 | Meta token invalidated; **140 failures** across meta + instagram | `sessions/reviews/2026-07-01-orchestrator-etl-health-review.md:74` |
| 2 | 2026-07-01 | **CRITICAL: 11 of 12 monitored client pipelines failing, 0 records landing** | same file, `:68` |
| 3 | 2026-07-12 | 2 transient SSL failures, shopify `workspace_0006` | `sessions/reviews/2026-07-12-t5-etl-health-review.md:10` |
| 4 | 2026-07-13 | isolated SYS_003 SSL failures | `sessions/reviews/2026-07-13-etl-health-review-t5.md:11` |
| 5 | 2026-07-21 | ads pipeline change "broke every report type's grain" | `sessions/reviews/2026-07-21-a1-ads-pipeline-harden-commander.md:28` |
| 6 | 2026-07-24 | drift ads spend ingestion failure requiring a fix | `sessions/reviews/2026-07-24-drift-ads-spend-ingestion-fix-closeout.md` |

**6 episodes against a threshold of fewer than 4.** At run grain the count is 140+ from episode 1
alone. The falsifier does not fire, and it does not fire by a wide margin rather than a whisker.

**The same reviews independently ask for this fabric**, which is worth more than the count:

> *"silent for months because auth/billing failures never dead-letter (alert policies miss them)"*
> — `2026-07-01-orchestrator-etl-health-review.md:78`
>
> *"add an alert for sustained worker-FAILED-without-DLQ so the next silent outage pages someone"*
> — same file, `:85`

That is the pilot's value claim, written down by the operator's own review three weeks before this
lane existed, in a review that had no idea it was making the case.

**Honest limit of this count.** These are the reviews' summaries, not the orchestrator's raw run
table. The authoritative log is the ETL's own run history in GCP, which this lane cannot reach
(see F1, which is the same wall). If that table were queried the count would go up, never down:
every episode above is already a summary of one or more real failed runs.

---

## F1 — "the producer cannot emit, or only from the wrong side of a boundary" — **FIRES**

> *Test before building:* attempt one write from the orchestrator ETL host to the VPS Postgres
> over the tailnet. Binary result.

**The test as written cannot be run, because its target no longer exists.** The sprint scoped the
pilot against a VPS Postgres reachable over the tailnet. `D00-shared-context.md` then froze v1
hosting afterwards:

> *"v1 is **LOCAL**, on this machine, under WSL ... Port `5432`, loopback bind ... **There is no
> VPS in v1.** A lane provisioning a remote agent server has misread this brief."*

The orchestrator ETL runs in GCP. The store is bound to `127.0.0.1:5432` inside WSL on the
operator's laptop. There is no route, and adding one would be provisioning the thing `D00` says
does not exist in v1. **The pilot's producer is on the wrong side of a boundary, exactly as F1
describes, and it got there through a decision taken after the falsifier was written.**

This is not an argument against the pilot. It is a statement about sequencing: `pipeline-failure-
observer` becomes buildable the moment the store is reachable from where its producer runs, and its
own evidence falsifier already passed. It is declared in `departments/SUBSCRIBERS.md` with
`Status: planned` and this precondition recorded as **NOT MET**, so a listener started against it
refuses at `Status` rather than starting and consuming nothing.

---

## F2 — "the failure is already detected sooner by something cheaper" — **not falsified, not fully measured**

> *Test before building:* measure the detection lag on the last 30 days of known pipeline failures
> under the current setup. If the median lag is already inside the target window, the pilot is
> falsified.

Not measured to a median, and saying so is the point. What the existing record does say is the
opposite of falsification: the 2026-07-01 review found failures that had been **silent for months**
because auth and billing failures never dead-letter and the alert policies miss them, and the
2026-07-13 review records `vw_unified_health_summary` broken for a third consecutive cycle. A
detection path that missed a multi-month outage is not a cheaper detector already inside the
window.

Measuring the median properly needs the orchestrator's run history, which is behind the same wall
as F1. Reported as partial rather than claimed as passed.

---

## What this means for the consumer that WAS built

The D5 brief's instruction on a fired falsifier is explicit: *"If it fails, say so and page on
session lifecycle alone rather than building an observer for an event that does not occur."*

F3 did not fail, so the observer is not the thing that was dropped. F1 did, so the observer has no
producer that can reach it, and building the listener half would have produced a subscriber
consuming a type nothing can emit. So the consumer that was built is `operator-paging`, against
the two producers that CAN reach the store from inside this machine: session lifecycle and operator
questions. `pipeline-failure-observer` is declared, `planned`, with its two preconditions marked
MET and NOT MET, and it will start the day the second one changes.
