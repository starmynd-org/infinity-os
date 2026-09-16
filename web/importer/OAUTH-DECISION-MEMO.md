# OAuth estate-access decision memo

Status: `BLOCKED-ON-OPERATOR`.

This seat performed no OAuth flow, account discovery, provider request, token exchange,
export, install, or activation. It accessed zero accounts and zero provider stores. A
search of the frozen local brain commit `9e56eda660c73fd673fb9a2ba2640f5a38831954`
and Infinity commit `56838e54a17e2f5585b9bc257dff043614b379a9` found no provider contract that proves
what Claude, ChatGPT, or Codex exposes for this purpose. Therefore this memo does not
invent endpoints or scopes.

## Operator decision required

Andrew must choose whether Infinity may request provider access at all and, for each
provider, approve a provider-authored capability and scope inventory before implementation.
The inventory must say whether the provider exports each of: commands, agents, skills,
rules, workflows, tools, knowledge, data pointers, memory, output references, and projects.
“All account data” is not an acceptable substitute.

Minimum acceptable authorization:

- read-only identity sufficient to bind the export to the consenting account;
- explicit, separately described read/export scope for each supported entity type;
- no message history, prompts, transcripts, credentials, billing data, or live runtime
  control unless Andrew separately opts in to that named class;
- no write, install, activate, execute, deploy, send, share, or delete capability;
- previewed denominator and missing-material statement before consent;
- short-lived credentials, revocation, audit trail, and a local immutable export id;
- provider response retained only as an `ESTATE/1.0` filesystem export before parsing.

## Product promise

The UI may promise: “Preview what can be exported; authorize read-only access; review
every imported, review-required, dropped, and refused member; nothing is enabled.” It may
not promise complete account portability until the provider proves a complete-export
signal and a stable denominator.

## Partial export

A provider that cannot prove completeness must set `complete: false` and name every known
missing scope. Retained members become `review_required`; activation remains blocked. A
provider that claims complete while omitting a declared member is refused
`EXPORT-INCOMPLETE`. Unknown scope or an unnamed gap is `PARTIAL-EXPORT`, not success.

## Revocation and deletion

Revocation stops future provider reads and refreshes immediately. It does not silently
delete the immutable local evidence already imported. The consent screen must separately
offer deletion of unimported staging data and a reviewable request to remove imported
records, with receipts. Provider revocation is never represented as successful deletion
from Infinity, and Infinity deletion is never represented as provider revocation.

Implementation remains `BLOCKED-ON-OPERATOR` until Andrew approves the provider list,
provider-authored capabilities, exact scopes, retention, revocation, and deletion policy.

## Connect overlap does not change the import boundary

Source record: Connect's retained reply at
`C:/Users/you/repos/internal/starmynd-connect/programme/REPLY-TO-INFINITY-OS-2026-09-09.md`,
relayed to this seat by the Admiral in
`20260909T200412Z-from-2026-09-09-IOS-admiral.md`. The deletion statements below are
reported policy and architecture boundaries, not executed erasure measurements.

Connect's 2026-09-09 reply corrects the earlier assumption that provider brokering was
probably outside its scope. Its shipped application has an OAuth application route and a
Connect “connection” commonly represents a provider connection. That governs what is
stored and who may read it back. This importer's hard bound governs what it may read. The
axes overlap but do not conflict: no agent may run a live authentication flow, read a
credential, or contact a provider before the operator and provider-authored capability
inventory authorize the exact scope.

Who is authoritative for a provider connection shared by Connect and Infinity is an open
design question for Andrew, not an importer default. The unattended service-credential
lifecycle is neither built nor specified in Connect and is not delegated here.

If Andrew later approves a Connect-backed reference, the current Connect design addresses
a secret by the opaque triple `(workspace, connection, field)`. The reference is not a URL,
because URLs are logged; it points to the retained payload rather than a version id, because
rotation and restore change version/revision ids. These verbs are design context, not a
shipped capability claim. A scalar `_ref` accepted by `ESTATE/1.0` must not be assumed to be
a sufficient Connect address.

The shipped Connect connection “cap” is not an enforcing safety predicate: it counts behind
a provider-novelty branch, and a second connection to the same provider takes an exempt
path. An importer denominator, refusal, consent limit, or capacity promise must not rely on
that count. The audit of `web/importer/**` found no existing cap assumption.

Connect describes workspace as stable and access groups as still moving. No mapping exists
between Connect grants/groups/custody and Infinity's GitHub-and-Postgres, repo-derived
tenancy. Multi-OS authorization remains deferred until that mapping has a named owner and
reviewable contract.

## Connect deletion limits

For a single Connect connection, deletion is a policy deletion, not prompt cryptographic
erasure; Connect's stated backup-expiry policy is 30 days. Only a whole workspace has the
per-workspace key boundary needed for cryptographic erasure, and Connect has measured no
timing for that operation. This memo therefore promises no workspace-erasure duration and
no immediate cryptographic erasure of a credential or connection. Infinity's requirement
is that revocation stop its future reads; neither this memo nor the Connect reply claims an
executed measurement of shipped Connect enforcement. Any deletion still needs a separate,
reviewable receipt.

`2026-09-09-IOS-term-9`
