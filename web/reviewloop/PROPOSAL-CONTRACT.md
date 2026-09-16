# Review Loop proposal contract

Version `REVIEWLOOP/1.0`. The Attention envelope is `ITEM/1.0`.

## Boundary

Git holds authored behaviour. Postgres holds a proposal, its decision, and its receipts while the
decision is in flight. The review loop reads authored material at an immutable Git SHA and emits a
decidable Attention item. It writes no queue state itself.

An accepted item may prepare one commit on one new local branch. It does not merge, push, open a
remote pull request, deploy, or act on an external system. The surface must say what is missing:
the branch is local, and a human must publish it and open or review the pull request.

## What the loop may propose

The loop may propose a bounded textual diff to an authored file when all of these are true:

1. The pass declared the target before it ran.
2. The source is read at an immutable base SHA.
3. The target locator occurs exactly once.
4. At least one novelty query is searched across every tracked file before the proposal is emitted.
5. The search finds no existing answer outside the target.
6. The proposal states the target, base SHA, source and replacement hashes, unified diff, rationale,
   reviewer class, novelty evidence, and the exact local-only accept action.
7. The Attention item has at least one real option and declares the inverse, or the lack of one, on
   every option.

The diff is evidence attached to a proposal. It is not a direct edit to the integration line.

## What the loop may never propose

The loop refuses:

- queue rows, receipts, events, sessions, transcripts, or run logs in Git;
- a secret value, credential, authority, or entitlement in a payload;
- rank, score, points, position, tier, badge, unread, or count on a proposal row;
- a proposal whose only action is acknowledge, dismiss, or noted;
- a topic already present elsewhere in the repository;
- a direct edit to an existing file under `migrations/**`;
- acceptance attributed to an agent;
- publication, merge, push, deploy, or a remote pull request action;
- last-write-wins when the target changed after the proposal was prepared.

The payload refusal is finite and inspectable. `REVIEWLOOP/1.0` treats
`data/{queue,receipts,events,sessions,transcripts,run-logs}/**` and a JSON object whose
`record_kind` is queue row, receipt, event, session, transcript, or run log as operational state.
It refuses explicit structured value fields named `api_key`, `access_token`, `refresh_token`,
`password`, `secret`, `credential`, `private_key`, `authority`, or `entitlement` in JSON or at the
start of a YAML-like or environment-style line. Stable pointer fields such as `secret_ref` remain
admissible.

Admission inspects the complete candidate file after applying the proposed replacement, not only
the replacement fragment. A partial edit therefore cannot create one of the declared forbidden
structured fields outside the fragment inspected by the gate.

JSON objects with a repeated member name at any nesting depth are unsupported and refuse before
interpretation. This prevents an earlier sensitive or operational member from being discarded by
last-member-wins decoding while its original bytes remain in the proposed file.

This is not universal semantic secret detection. Arbitrary prose and unsupported serializations
still require human review; the loop must not claim that a finite structured scan proves they carry
no sensitive value.

## Reviewer classes

| Target class | Reviewer at proposal time | Conflict behaviour |
|---|---|---|
| governed | One named human is mandatory | Emit a human-surfaced exception row. Never choose a side. |
| authored | A repository maintainer role is enough | Emit an exception row for the relevant maintainer. |
| draft | A workspace reviewer role is enough. No named human is required. | Emit an exception row for a workspace reviewer. |
| migration | Nobody. Existing migrations refuse. | Refuse the collision and route a new append-only migration to the migration owner. |

Governed means `web/MUST-NOT-BUILD.md` and successors, `repo-registry/**`, the secret registry,
`**/decisions/**`, `CLAUDE.md`, and `AGENTS.md`. A governed proposal carries
`canon_touching: true`, `surfacing: human`, and `authority_required: internal`.

The asymmetry is deliberate. A draft proposal still needs a human decision before a commit is
prepared, but it does not need one person's name before it can enter review. A governed proposal
does. Losing one of two concurrent edits to governed material is not recoverable by redoing the
work, because the lost ruling may never be noticed.

## Decidable row

Every proposal is `kind: proposal` and offers exactly these decisions:

1. Prepare the reviewed diff on a new local branch.
2. Reject the change and record why.
3. Send it back for revision without changing repository content.

Every merge conflict is `kind: exception` and offers exactly these decisions:

1. Ask the agent to prepare a reconciliation preserving the proposed intent.
2. Keep the current version.
3. Open both versions for comparison.

There is no read-more option. Supporting evidence is attached to the row, but it is not a fourth
choice that avoids the decision.

## Acceptance and the Git path

Only `accept-change`, attributed to a human, may call the local materializer. A governed proposal
also requires that human to match the name fixed on the proposal. The materializer re-reads the
target ref before writing anything. The proposal producer is not a reviewer even if a caller
labels that same identity human.

- If the target content hash still matches, it creates a new worktree and local branch at the
  current target SHA, writes the one reviewed replacement, stages the named target, and commits.
- If the hash differs, it prepares no branch. It emits the exception row above.
- If the target is an existing migration, it refuses and emits no resolution row.

The Git adapter has no `push`, `merge`, `fetch`, or remote command in its vocabulary.

## Metrics and brake

Every pass publishes, in this order:

1. review-target denominator and tracked-file denominator for each novelty query;
2. reviewed, candidate, proposed, suppressed, and refused counts;
3. proposals per pass;
4. proposals confirmed by a non-author over proposals emitted;
5. rejected proposals over decided proposals as the false-positive rate.

Proposal and decision denominators count unique proposal identities. More than one copy of a
proposal, more than one decision for one proposal, or contradictory decisions for one proposal
refuse measurement instead of inflating the numerator or denominator.

Zero decided proposals is `not-measurable`, never `0 percent`. A full pass with zero emitted
proposals stops immediately. A pass with emitted proposals remains open until non-author decisions
land. A completed pass stops on that component when zero proposals were accepted by a non-author.

`2026-09-09-IOS-term-10`
