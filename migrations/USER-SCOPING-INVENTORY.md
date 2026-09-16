# User-scoping inventory: every operational table, and whether it carries a human identity

**Historical snapshot: ledger 70.** The current ledger-74 census is published in
`outputs/2026-09-09-IOS-term-5/INVENTORY-LEDGER74.md`, backed by the full read-only
`inventory-ledger74.json`: 55 tables, 12 LOGIN, 16 STAMP, 3 NAME, 1 SERVICE, 23 NEVER;
10 tables have a workspace column. STAMP distinguishes last-human attribution from
authentication of the existing verb-specific strings. The original 53-row census below
is retained as measured history, not a claim about the current schema.

Written 2026-09-09 by `2026-09-09-IOS-term-5`, for the ruling recorded in
`ARCHITECTURE-2026-09-09-tenancy-and-the-git-boundary.md`: **user-scoped schema from day one,
exactly one user in practice.** "Every operational row that will ever be per-user gets a user
identity now." This file is the list that ruling asks for, with its denominator, so that "most"
never appears in a sentence about it.

## Method, so it can be re-run rather than trusted

Two read-only queries on a scratch store built from the tree, both files in `migrations/tests/`:

```
ENGINE_SCRATCH_DB=ios_term5_scratch engine/bin/scratch-db.sh psql -tA -f - < migrations/tests/inventory-columns.sql
ENGINE_SCRATCH_DB=ios_term5_scratch engine/bin/scratch-db.sh psql -tA -f - < migrations/tests/inventory-login-ties.sql
```

The first lists every base table in schema `brain` with its columns. The second lists every table
that has a trigger whose function body calls `brain.current_human()`, which is the only kind of
attribution this store calls **the database's answer** (migrations 20, 32, 36, 37, 40, 41). A
column named `*_by` with no such trigger is a caller string, however honest its name.

Measured on `ios_term5_scratch` at ledger 70 on 2026-09-09: **53 base tables** (49 at ledger 68
plus 4 from migrations 69 and 70); **15 login-tied triggers over 9 tables**; 47 non-internal
triggers over 25 tables in all.

## The four marks

| mark | meaning |
|---|---|
| **LOGIN** | a human identity on the row, and a trigger makes it equal `brain.current_human()`. Already user-scoped in the sense the ruling means. |
| **NAME** | a human identity on the row, checked against the roster or a grant, but NOT against the login. A caller can pass a colleague's name. Needs the migration-36 move. |
| **NEEDS** | rows a human writes or acts on, with no identity column, or one that is a bare string. |
| **NEVER** | rows no human writes: agents, the fabric, listeners, catalogues, machine bookkeeping. A workspace may still belong on them; that is the second column. |

The `workspace` column answers the other half of the same ruling (workspace is the tenancy unit,
migration 63's column). It is inventoried here and NOT migrated by this seat yet; see "what this
file does not do".

## The 53, one line each

| table | user mark | user column(s) today | workspace today | note |
|---|---|---|---|---|
| admin_change | LOGIN | changed_by | none | migration 40 |
| agent | NEVER | none | none | an agent is fleet state; `delegation_level` is the ladder, not a person |
| approval | NAME | decided_by (61, 63) | yes (63) | migration 71 on this branch makes it LOGIN; task IDN-APPROVAL-DECIDER-FORGE-01 |
| artifact | NEVER | agent, actor_type | none | an agent's record of a path |
| authority_grant | NAME | granted_by (actor-is-known) | yes (63) | a grant by a human is checked by name only |
| authority_revocation | NAME | revoked_by | yes (63) | same shape as the grant |
| budget_charge | NEVER | agent | none | spend by an agent; a workspace budget is a later question |
| budget_incident | NEEDS | detected_by, cleared_by (strings) | none | clearing an incident is a human act |
| budget_policy | NEEDS | set_by, retired_by (strings) | none | a ceiling is a human's decision |
| capability | NEVER | none | none | catalogue |
| capture_dead_letter | NEVER | none | none | machine journal |
| capture_dead_letter_reconciliation | NAME | resolved_by (68, roster-checked) | none | 68 checks the name is a human; not the login |
| config_setting | LOGIN | set_by | none | migration 40 |
| disposition | NEEDS | decided_by (string) | none | D5 verdicts; a human's or an agent's, unrecorded which login |
| effect_attempt | NEVER | subject, settled_by (agent/service) | yes (63) | an agent's act under a lease |
| effect_reconciliation | NEEDS | found_by (string) | none | a finding is made by somebody |
| event | NEVER | actor_type | none | the fabric; per-workspace later, never per-user |
| event_rollup | NEVER | none | none | aggregate |
| execution_lease | NEVER | holder | yes (63) | an agent's lease |
| human_repo_host_identity | LOGIN | human, linked_by, unlinked_by | via host | migration 70 |
| human_role | LOGIN | the roster itself | none | migration 20 |
| image_attachment | NEEDS | attached_by, detached_by, dismissed_by (strings), actor_type | none | three human acts, three strings |
| message | NEVER | from_agent, to_agent | none | agent mail |
| objective | NEEDS | author (string), accepted_at with no accepted_by | none | acceptance is a human act with no human on it |
| observation | NEVER | actor_type | none | the spine's machine half |
| producer_name | NEVER | declared_by | none | registry |
| project | LOGIN | created_by, state_changed_by, archived_by | none | migrations 44, 46, 47 |
| question | NEEDS | asked_by (agent); answer with no answered_by | none | the operator's answer carries no human |
| queue_bump | NEEDS | created_by (string, default operator) | none | a bump is a human's |
| queue_calibration | NEEDS | created_by (string) | none | |
| queue_default_event | NEVER | producer | none | a default firing by the machine |
| queue_defer | NEEDS | created_by, woke_by (strings) | none | a defer is a human's |
| queue_item | NEEDS | disposition, decision with no *_by | none | the attention inbox row: the one that matters most |
| queue_item_option | NEVER | drafted_by (a model) | none | an option is a proposal |
| receipt | NEEDS | booked_by (string), actor_type | none | a receipt of an action names its actor by string |
| recommendation | LOGIN | decided_by | none | migrations 32, 33 |
| repo_access_receipt | NAME | human (roster-checked), checked_by (session_user) | yes (70) | a machine reading about a human; the writer login is the residual 70 states |
| routine | LOGIN | created_by, disabled_by | none | migration 35 |
| routine_run | NEVER | fired_by (the scheduler) | none | |
| run | NEVER | agent | none | an agent's run |
| runtime_flag | NEEDS | set_by (string) | none | pause and resume are human acts |
| schema_migration | NEVER | none | none | the ledger |
| session | NEVER | agent | none | an agent's harness session; a human's web session is a different table, not yet built |
| subscriber_cursor | NEVER | subscriber | none | listener |
| subscriber_role | NEVER | the listener roster | none | |
| thread | NEEDS | from_agent (the operator's notes say 'operator' by string) | none | |
| time_entry | NEEDS | who (string, default operator) | none | the stopwatch is per-person by nature |
| touch | NEVER | none | none | lineage |
| transcript | NEVER | none | none | pointer and hash |
| voice_capture | NEEDS | actor_type, nothing else | none | a voice note is a human's |
| work_item | LOGIN | actor_type (20), accepted_by (36), assigned_human (37) | none | posted_by is still a string |
| workspace | LOGIN | declared_by | is the unit | migration 69 |
| workspace_repo | LOGIN | added_by | yes | migration 69 |

## The counts, and they add up to the denominator

| mark | tables | of |
|---|---|---|
| LOGIN | 10 | 53 |
| NAME | 5 | 53 |
| NEEDS | 16 | 53 |
| NEVER | 22 | 53 |

10 + 5 + 16 + 22 = 53.

Workspace: **6 of 53** carry the column today (approval, authority_grant, authority_revocation,
effect_attempt, execution_lease, repo_access_receipt) plus the unit itself and its repo set.

## What follows from it, in this seat's queue

1. **NAME to LOGIN, 5 tables.** approval is migration 71 on this branch (coupled to its writer's
   role, see 71's header). authority_grant, authority_revocation,
   capture_dead_letter_reconciliation and repo_access_receipt are the same move each, and each
   has the same coupling: the writer must open the human's login before the trigger lands.
2. **NEEDS, 16 tables.** One migration adding `last_human` and `last_human_at` to each, stamped
   by one shared trigger from `brain.current_human()` on INSERT and UPDATE when the connection is
   a human, left alone when it is an agent, and refused when a caller passes a name that is not
   this connection (IDENTITY-POLICY rule A3). Additive: every existing writer passes nothing and
   is unchanged; no writer needs to change for the row to carry who touched it. Per-verb columns
   (answered_by, disposed_by, accepted_by) can follow where a surface needs the verb, not the
   person.
3. **Workspace on the NEVER rows that are per-workspace by nature** (event, run, session,
   observation, budget_*): migration 63's shape, NOT VALID, NULL matching nothing. Not started;
   it is the tenancy half and a larger change to the fabric, and it is stated here rather than
   quietly begun.

## What this file does not do

It does not decide. Every mark restates a ruling already made: the user column decision is
Andrew's (2026-09-09), the login-not-string rule is migration 36's and IDENTITY-POLICY rule A3's,
and the workspace column is migration 63's. Where a mark is a judgement about whether a human
ever writes a row, the note says why, and a reader who disagrees changes the mark, not the rule.
