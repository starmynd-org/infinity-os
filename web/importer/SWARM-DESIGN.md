# Import-to-swarm design

This is the written design that precedes any swarm-planning code. It consumes only
`ESTATE/1.0` outcomes. It proposes disabled build agents; it does not create agents,
install tools, activate imported material, or contact a provider.

## Entry gate

The planner accepts an import only after the denominator invariant holds and the
producer-specific positive control fired. Refused and dropped members remain in the
evidence ledger but never become build work. `review_required` members stop at a review
agent until every named loss and authority gap has a human disposition.

## Ordered build

1. `estate-integrity-reviewer` checks stable identity, duplicate conflicts, losses,
   dependencies, source references, and the import denominator. It is always first.
2. `estate-authority-reviewer` resolves `authority_required: unknown`, provider-specific
   tools, and any external or financial boundary. It runs before builders when any
   retained member needs it.
3. One type-bounded builder is proposed for each retained portable type, in this order:
   Knowledge, Data, Memory, Tool, Skill, Rule, Command, Workflow, Agent, Project, Output.
   Foundations precede behavior; behavior precedes orchestration; outputs are last.
4. `estate-dependency-reviewer` checks that references resolve to a retained or explicitly
   external identity and reports cycles. It cannot waive missing dependencies.
5. `estate-safety-reviewer` checks secret absence, disabled activation, declared losses,
   tool boundaries, and that imported projects did not become live queue work.
6. `estate-acceptance-reviewer` produces `ITEM/1.0` approval/review proposals for a named
   human. It never emits an announcement-only Attention row.

## Builder defaults

- Every proposal is disabled. Unknown authority blocks approval.
- A builder receives only source references and the portable record for its own type.
- No builder receives raw credentials, OAuth tokens, chat transcripts, or binary payloads.
- Concurrency is one by default. Parallelism is allowed only for dependency-independent
  type groups after the integrity and authority stages pass.
- Builders propose artifacts in a staging workspace. They cannot write the canonical
  brain, provider settings, live queues, source systems, or production services.
- A failed builder produces a refusal or review item with `why` and `instead`; it does not
  silently skip the member or retry against a live source.
- Completion means every retained member has a build disposition and every proposal has
  a validation result. It does not mean activation.

## Human boundary

The final human chooses among real acts such as `enable`, `keep disabled`, or `review
first`. Each option states its own inverse. Enabling remains outside this module. A
summary such as “we imported 42 agents” belongs in evidence, not Attention.

`2026-09-09-IOS-term-9`
