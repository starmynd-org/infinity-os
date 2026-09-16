# Agent estate import contract

Version `ESTATE/1.0`.

This contract describes a filesystem import. It does not authenticate to Claude, Codex,
OpenAI, or any other provider. The OAuth acquisition step is `BLOCKED-ON-OPERATOR` and is
not implemented here.

## 1. Import invariant

An import is complete only when every member declared by the export has exactly one
recorded outcome:

- `imported`: the member has enough portable meaning to build a disabled draft.
- `review_required`: the portable core is retained, but a named loss or authority gap
  prevents activation.
- `dropped`: the member is an implementation detail that has no entity-level target. The
  outcome names what was dropped and why.
- `refused`: the member is malformed, unsafe, ambiguous, or cannot be distinguished from
  missing input.

The importer never reports only the members it kept. Its report prints the export
denominator first, then all four outcome counts. The sum of the four counts must equal the
denominator. A missing or duplicate outcome refuses the whole report.

Nothing imported is activated. No provider is contacted. No secret value is copied.

## 2. Export envelope

An export carries these required fields before any member is parsed:

| field | requirement |
|---|---|
| `contract_version` | exactly `ESTATE/1.0` |
| `producer` | a supported producer and format version |
| `source_root_id` | a stable, non-secret identifier for the exported estate |
| `generated_at` | a UTC timestamp with `Z` suffix |
| `complete` | boolean, never inferred from the number of files |
| `members` | the complete list of relative paths or catalog identities |
| `member_count` | integer equal to the length of `members` |
| `source_revision` | immutable git SHA, export digest, or provider export id |
| `digests` | SHA-256 for every file in the export |
| `missing` | a list of expected material that the producer could not export, empty when complete |
| `control` | `{name, fired, evidence}` for a producer-specific positive control; `fired` must be true |

Supported producers in this version are:

- `brain-directory/1.0`: canonical Markdown entities and paired deterministic workflows.
- `claude-code-directory/1.0`: an already exported local directory, never a live account.
- `codex-directory/1.0`: an already exported local directory, never a live account.
- `architecture-catalog/1.0`: the frozen 45-type runtime catalog plus the brain graph
  catalog used for Andrew's estate measurement.

An unknown producer is refused. A partial export may be inventoried, but every absent
member is named and every retained member becomes `review_required`. `complete: true` with
a missing declared file is refused as `EXPORT-INCOMPLETE`.

Paths are relative, normalized, and confined to the export root. Absolute paths, parent
traversal, and symlinks escaping the root are refused. UTF-8 is required. A file that
changes between its first and second digest is refused as `SOURCE-MOVED-DURING-IMPORT`.

## 3. Common portable record

Every imported or review-required member becomes one `PortableEntity` with:

| field | requirement |
|---|---|
| `entity_id` | stable source id, or a deterministic id derived from producer plus relative path |
| `entity_type` | one of the eleven types in section 4 |
| `name` | non-empty human-facing name |
| `summary` | non-empty statement of what the entity is for |
| `body` | the source definition, retained as untrusted text |
| `source_ref` | producer, source revision, relative path or catalog identity, and SHA-256 |
| `activation` | always `disabled` or `review_required` on import |
| `authority_required` | `none`, `internal`, `external`, `financial`, or `unknown` |
| `dependencies` | stable references when declared, otherwise empty |
| `extensions` | recognized source metadata that has no portable first-class field |
| `losses` | zero or more explicit loss records |

A loss record carries `code`, `field`, `disposition`, `why`, and `source_value_present`.
The importer may omit a raw source value when retaining it would expose a credential or
unbounded content, but it must still say that a value was present.

Unknown top-level fields are preserved in `extensions` unless their names are credential
bearing. Unknown executable semantics become a loss and force `review_required`. Fields
matching `password`, `secret`, `token`, `api_key`, `apikey`, or `credential`, at any
depth, are refused when they carry a value. A stable secret reference id is portable; a
secret value is not.

The exact credential-cleared frontmatter block is also retained as
`extensions.raw_frontmatter`. This is the lossless fallback for nested source metadata the
dependency-free scalar parser does not structure. Retention as raw untrusted metadata does
not turn unknown executable semantics into supported behavior.

Inline collections are validated before that raw fallback is retained. JSON-shaped inline
collections and the finite simple YAML-flow form `{identifier: scalar, ...}` are supported.
More complex flow syntax the dependency-free parser cannot validate is refused as
`MALFORMED-FRONTMATTER`; it is not passed through as though credential clearance succeeded.

The supported syntax boundary is prospective and finite:

| exact frontmatter input shape | classification |
|---|---|
| `settings: {"api_key":"SYNTHETIC-NOT-A-SECRET"}` | `CREDENTIAL-IN-EXPORT` |
| `settings: {"api_key":"SYNTHETIC-NOT-A-SECRET","api_key":null}` | `MALFORMED-FRONTMATTER`; duplicate key refused before dict construction |
| `settings: {"nested":{"api_key":"SYNTHETIC-NOT-A-SECRET","api_key":null}}` | same duplicate-key refusal at nested object depth |
| `settings: {api_key: SYNTHETIC-NOT-A-SECRET}` | `CREDENTIAL-IN-EXPORT` |
| `settings: {region: SYNTHETIC-REGION}` | supported simple flow map; exact raw text retained |
| `"api_key": SYNTHETIC-NOT-A-SECRET` on an indented line | `CREDENTIAL-IN-EXPORT` |
| `region: SYNTHETIC-REGION` on an indented line | exact raw text retained |
| `credential_ref: vault-item-one` on an indented line | stable reference text retained |
| `settings: {nested: {region: SYNTHETIC-REGION}}` | `MALFORMED-FRONTMATTER` |
| `settings: {region:` | `MALFORMED-FRONTMATTER` |
| `settings: {count: <4,301 decimal digits>}` | `MALFORMED-FRONTMATTER` |
| `type: ["Command"]` | parsed JSON list, then `UNKNOWN-ENTITY-TYPE` |

Quoted and unquoted identifier keys are recognized by the credential scan. JSON objects and
lists are structurally parsed, with duplicate object keys rejected at every object depth
before ordinary dictionary construction. The simple YAML-flow subset permits one flat object
whose keys are identifiers and whose values are scalar; nested collections and
unparseable/over-limit values are outside that subset and refuse before raw retention.
Indented block metadata is retained exactly in the normalized LF interior after credential
values are excluded. Keys ending `_ref` or `_refs` are stable-reference data and remain
retained, not resolved.

A credential reference is portable only when it is explicitly named with `_ref`/`_refs`,
or when an n8n `credential`/`credentials` object contains only a non-empty stable `id` and
optional string `name` per reference. Scalar credential values and reference objects with
extra value-bearing fields are refused.

## 4. Per-entity mapping

The eleven types come from the brain doctrine card. They are not the runtime catalog's
45 component types.

### Command

- Required: stable name, description or stated intent, invocation body, source reference.
- Optional: arguments, examples, invoked agent, skill or workflow, declared tools.
- Dropped: duplicate `.claude/commands` or `.codex/commands` adapter copies when a
  canonical `entities/commands` source exists.
- Lost and said: provider-specific slash registration, autocomplete metadata, and
  host-only aliases do not activate here. They force review when they affect invocation.
- Refused: empty body, no identifiable intent, credential value, or conflicting canonical
  and adapter bodies.

### Agent

- Required: stable name, bounded job, non-empty behavior, source reference.
- Optional: triggers, ordered steps, tools, model hint, constraints, output contract.
- Dropped: adapter copies that are byte-identical to a canonical agent.
- Lost and said: provider model ids, tool names, permission modes, and delegation syntax
  are not assumed to exist here. Unknown tools force review.
- Refused: personality-only definition with no job, empty behavior, duplicate id with
  different content, or credential value.

### Skill

- Required: stable name, trigger or use condition, non-empty procedure, source reference.
- Optional: anti-patterns, quality checks, required outputs, supporting files.
- Dropped: adapter copies that are byte-identical to a canonical skill.
- Lost and said: provider discovery rules, automatic invocation weight, and unsupported
  bundled executables do not travel automatically.
- Refused: no trigger and no procedure, missing referenced required file, path escape, or
  credential value.

### Rule

- Required: stable name, governed scope, normative statement, source reference.
- Optional: enforcement hook references, examples, exceptions, rationale.
- Dropped: runtime adapter copies when a canonical rule exists.
- Lost and said: a behavioral rule does not become mechanical enforcement merely because
  it was imported. Missing enforcement is named and forces review.
- Refused: no governed scope, only temporary status prose, contradictory duplicate, or
  credential value.

### Workflow

- Required for agentic Markdown: stable name, trigger, at least one ordered step, output
  or completion condition.
- Required for deterministic JSON: valid JSON object, a non-empty `nodes` array, a
  `connections` object, and its companion Markdown record when the producer declares the
  pair.
- Optional: inputs, approvals, agents, skills, tools, evaluator, stop condition.
- Dropped: provider UI coordinates and editor-only presentation fields.
- Lost and said: provider node types, credential bindings, webhook urls, schedules, and
  activation state never activate here. Unsupported nodes force review.
- Refused: unparseable JSON, zero-node workflow, missing declared pair, hidden credential
  value, or an approval implied only by origin.

### Tool

- Required: stable name, bounded capability, invocation or interface description, auth
  boundary.
- Optional: operations, implementation pointer, owning department, stable secret refs.
- Dropped: connection status, cached schemas, local installation state.
- Lost and said: the imported record proves that a tool was described, not that it is
  installed, reachable, authenticated, or authorized. Every tool starts disabled.
- Refused: raw credentials, an executable without a described boundary, or a connection
  claimed live without export evidence.

### Knowledge

- Required: stable id, namespace, atomic claim or procedure, non-empty body.
- Optional: aliases, lifecycle, summary, confidence, retrieval class, export class,
  edges, lineage.
- Dropped: generated indexes and adapter caches when their source nodes are present.
- Lost and said: unresolved wikilinks and unknown lifecycle values force review. Canon
  status is never accepted as self-approval in the receiving workspace.
- Refused: empty node, invalid encoding, duplicate stable id with different content, or
  a claimed canon node with no approval provenance.

### Data

- Required: stable id and pointer to the external system of record.
- Optional: refresh description, analytical view, metric references.
- Dropped: copied live values and raw snapshots.
- Lost and said: freshness, row count, and current availability are not inferred from a
  pointer. The imported pointer starts disabled until its tool and authority are resolved.
- Refused: a raw secret, an inline live dataset presented as a pointer, or no destination.

### Memory

- Required: stable id, reviewed lesson, source context, and what should change next time.
- Optional: lineage to a run, intake receipt, or correction.
- Dropped: raw transcripts and unreviewed logs.
- Lost and said: provider conversation state, embedding ids, and retrieval ranking do not
  travel. A raw chat transcript is not promoted to Memory by import.
- Refused: no lesson, only a log, or a credential value.

### Output

- Required: stable id, artifact reference, and lineage to the agent, workflow, or project
  that produced it.
- Optional: media type, digest, size, retrieval class.
- Dropped: unbounded binary bytes and machine-local preview caches.
- Lost and said: an artifact path may be unreachable on the receiving machine. The record
  imports as review-required until the digest and storage pointer resolve.
- Refused: missing lineage, a file claimed present whose digest cannot be read, or a
  credential-bearing location.

### Project

- Required: stable id, objective, scope, and success criteria.
- Optional: tasks, owner references, dependencies, output links, execution modes.
- Dropped: live queue state, transient assignees, run state, and provider-local board
  coordinates.
- Lost and said: tasks become disabled build proposals, never live queue rows. A project
  status is recorded as source history, not adopted as current truth.
- Refused: no objective, no scope boundary, or a project that consists only of runtime
  state.

## 5. Runtime catalog mapping

The `architecture-catalog/1.0` producer enumerates implementation components as well as
portable entities. Its mapping is explicit:

| runtime component | portable entity |
|---|---|
| `brain_command`, `cli_command` | Command |
| `agent` | Agent |
| `brain_skill` | Skill |
| `brain_rule`, `attention_rule` | Rule |
| `brain_workflow`, `workflow`, `routine` | Workflow |
| `mcp_tool` | Tool |
| `doctrine_doc`, `knowledge_graph` | Knowledge |
| `memory` | Memory |

All other runtime component types get a `dropped` outcome with loss code
`RUNTIME-IMPLEMENTATION-DETAIL`. They are not silently excluded and are not promoted into
the eleven by name similarity. `hook` and `event` are also dropped in `ESTATE/1.0`: a hook
is host enforcement and an event is runtime history, neither a portable durable entity by
itself.

The absence of Data, Output, or Project members from this catalog is a measured property of
that producer, not evidence that Andrew's brain contains none. A direct brain-directory
scan covers those types separately and publishes a separate denominator.

## 6. Refusal codes

Every refusal includes `code`, `member`, `why`, and `instead`.

| code | condition |
|---|---|
| `UNKNOWN-PRODUCER` | producer or format version is unsupported |
| `EXPORT-INCOMPLETE` | manifest claims complete but a declared member is absent |
| `PARTIAL-EXPORT` | producer admits missing material; inventory may run but activation is blocked |
| `COUNT-MISMATCH` | declared member count differs from the list or outcomes |
| `PATH-ESCAPE` | absolute path, parent traversal, or escaping symlink |
| `NOT-UTF8` | a text member is not valid UTF-8 |
| `DIGEST-MISMATCH` | bytes do not match the manifest |
| `SOURCE-MOVED-DURING-IMPORT` | a member changes during the read |
| `MALFORMED-FRONTMATTER` | opening YAML fence has no valid closing fence or scalar shape |
| `MALFORMED-JSON` | JSON cannot be parsed |
| `EMPTY-WORKFLOW` | deterministic workflow has zero nodes |
| `MISSING-PAIR` | a declared paired workflow lacks JSON or Markdown |
| `UNKNOWN-ENTITY-TYPE` | a direct export names no supported entity type |
| `MISSING-REQUIRED-FIELD` | per-entity required meaning is absent |
| `DUPLICATE-ID-CONFLICT` | one stable id has different substantive content |
| `CREDENTIAL-IN-EXPORT` | a credential-bearing key contains a value |
| `UNDECLARED-LOSS` | a source field would be omitted without a loss record |

A malformed member is never converted into an empty entity. A zero-member export is
refused unless the manifest both declares zero and carries a non-empty `empty_reason`; its
report still prints denominator zero and the control that proved the parser ran.

## 7. Mapping to Attention `ITEM/1.0`

Import results do not automatically become rows. A row is emitted only when a named human
has a decision to make:

- `kind: review` when source semantics, tools, authority, or losses need inspection.
- `kind: approval` when the entity is fully parsed and can be enabled or kept disabled
  from the card.
- producer source is `brain` for Andrew's brain export and `external` for a third-party
  filesystem export; actor kind is `worker`.
- `authority_required` always travels. `unknown` is admitted but blocks approval.
- options are real acts, such as `enable`, `keep disabled`, or `review first`. Each option
  declares whether an inverse exists and why.

`We imported X` is an announcement and gets no row. `We imported X; enable it, keep it
disabled, or review it first` is decidable. This is the surviving item 7 condition from
`web/MUST-NOT-BUILD.md` at `0180a51`: every row must be decidable.

The mapper requires its producer actor to be provisioned in both GitHub and Postgres.
Provisioning in only one plane is refused as `ACTOR-UNKNOWN`; a caller cannot infer the
missing half from the half it can see. Workspace identity is the same two-plane identity.

Imported tasks never become queue rows merely because a source project listed them. Queue
state belongs in Postgres and import is a proposal, not a launch.

## 8. What this version does not build

- OAuth, account discovery, token refresh, or provider export requests.
- Activation, installation, execution, deployment, or writes to a brain.
- Schema or ingest-door changes. Those belong to other seats.
- Silent best-effort fallback for unknown producers or malformed files.

`2026-09-09-IOS-term-9`
