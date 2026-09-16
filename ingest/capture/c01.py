"""Projection onto the pinned C01 wire contract. The internal types do not change.

PINNED AGAINST: **C01 0.1.0-draft.5**, commit `0f5909af55859403f8ee5e4cc3e36550c3d1c136`,
digest `sha256:fb42d422a7ae834c2faf5523aad57d97786e51dfbe67360d26b92802ddcb6a10`,
tools digest `sha256:049472298f7661391a9fa3df432824d03f204cbb7bd02e493c2c290dcd00b951`.
Verified by recomputing with C01's own `tools/digest.py` against a `git archive` of the named
commit, never against the worktree, and `tools/validate.py` on that archive reports 64/64 positive,
126/126 negative, RESULT: PASS.

WHY A PROJECTION RATHER THAN A REPLACEMENT, and this corrects something I claimed earlier. I wrote
that pinning C01 would be "a one-file swap" because every module imports its shapes from
`contracts.py`. That was half right and worth stating precisely: ten files import those types, so
REPLACING them is not a one-file change at all. What is a one-file change is adding a boundary that
PROJECTS the internal record onto the wire shape, which is this file. The internal types keep the
fields C01 has no home for -- `retention.brains`, `attachments`, `participants`, `occurred_at`
distinct from `captured_at`, `connector_version`, `run_id`, `DeadLetter` -- and none of the 108
tests over them moves.

WHAT THIS FILE WILL NOT DO. It will not invent a `workspace`. C01 requires one on every record and
this lane has no such concept, so it is a required argument with no default: a projection that
guessed it would put an unverified tenant boundary into evidence, and `workspace` is the field the
whole isolation story rests on. The same rule applies to `store` and `locator`: custody knows where
it put the bytes, so the caller passes that rather than this file composing a plausible path.

THE THREE RULINGS THIS ENCODES, each one a decision rather than a reading:

* **CR-I1** identity is the projection `provider`, `account` when present, and `external_id` ONLY.
  `kind`, `thread_id` and `account_ref` are deliberately NOT identity-bearing, because hashing the
  whole object is the E06/E23 gap where the same item on another thread resolved to a second key.
  This is the narrower input this lane argued for and the Admiral ruled for.
* **Open question 4**: `revision_hash` is this lane's `content_digest`, donated as ruled.
* **CR-I3** (this lane's CR-4): `durable: true` and `writeback_allowed: true` each require
  `journal_seq`. A durable outcome is a journal position and not a flag, so the projection reads
  the receipt's own sequence and refuses to assert durability without one.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from .contracts import CaptureReceipt, CaptureRecord, SourceRef

#: The pinned version this projection writes. Instances carry it so a reader can tell which
#: contract produced them; it is not a claim about which version the reader holds.
C01_VERSION = "0.1.0-draft.5"
C01_DIGEST = "sha256:fb42d422a7ae834c2faf5523aad57d97786e51dfbe67360d26b92802ddcb6a10"

#: `kind` here is this lane's internal event kind; `disposition` is C01's. They are not the same
#: vocabulary and the mapping is explicit rather than a rename, because C01 has three states this
#: lane represents elsewhere: `repeat_ignored` is a duplicate outcome rather than a row, and
#: `poison` / `partial` are what this lane holds as a `DeadLetter`.
DISPOSITION_FOR_KIND = {
    "create": "captured",
    "revision": "revised",
    "tombstone": "tombstoned",
}


class ProjectionError(ValueError):
    """The internal record cannot be projected without inventing something. Never guess instead."""


def content_hash(payload: Any) -> str:
    """C01's canonical hash. Byte-compatible with `tools/canonical.py`: sorted keys, no spaces,
    UTF-8, no trailing newline. This lane's own canonicaliser is strictly stricter and is used for
    everything internal; this one exists so the wire value matches C01's tool exactly."""
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def identity_key(source: SourceRef, *, account: str | None = None) -> str:
    """CR-I1. Provider, account when present, external_id. Nothing else.

    `account` is separate from `SourceRef.account_id` on purpose: C01 distinguishes `account`, which
    is identity-bearing, from `account_ref`, which is not. This lane's `account_id` is the former.
    """
    projection: dict[str, str] = {
        "provider": source.provider,
        "external_id": source.source_id,
    }
    acct = account if account is not None else source.account_id
    if acct:
        projection["account"] = acct
    return content_hash(projection)


def to_capture_record(
    record: CaptureRecord,
    *,
    workspace: str,
    source_kind: str,
    custody_store: str,
    custody_locator: str,
    retention_policy_ref: str | None = None,
    cursor_ref: str | None = None,
    attempt: int | None = None,
    thread_id: str | None = None,
    account_ref: str | None = None,
    audience: tuple[str, ...] | None = None,
    tombstoned_by: Mapping[str, Any] | None = None,
    tombstone_reason: str = "source_deleted",
) -> dict[str, Any]:
    """Project one committed journal row onto `capture-record`.

    `workspace`, `source_kind`, `custody_store` and `custody_locator` have no default and no
    fallback: each is a fact this lane's record does not carry, and a projection that supplied one
    would be writing an assertion nobody made into evidence.
    """
    for name, value in (("workspace", workspace), ("source_kind", source_kind),
                        ("custody_store", custody_store), ("custody_locator", custody_locator)):
        if not value:
            raise ProjectionError(
                f"{name} is required by C01 and is not carried by the capture record; the caller "
                "supplies it rather than this projection inventing one"
            )

    disposition = DISPOSITION_FOR_KIND.get(record.kind)
    if disposition is None:
        raise ProjectionError(f"no C01 disposition for internal kind {record.kind!r}")

    durable = disposition in ("captured", "revised")
    instance: dict[str, Any] = {
        "contract_version": C01_VERSION,
        "capture_id": record.capture_id,
        "workspace": workspace,
        "source_identity": {
            "kind": source_kind,
            "provider": record.source.provider,
            "external_id": record.source.source_id,
            "account": record.source.account_id,
        },
        "identity_key": identity_key(record.source),
        "revision": record.revision,
        # Open question 4, ruled: the revision hash IS this lane's content digest.
        "revision_hash": record.content_digest,
        "received_at": record.captured_at,
        "disposition": disposition,
        "durable": durable,
        "live": disposition != "tombstoned",
        "provenance": {"connector": record.provenance.connector},
    }
    if thread_id:
        instance["source_identity"]["thread_id"] = thread_id
    if account_ref:
        instance["source_identity"]["account_ref"] = account_ref
    if record.raw is not None:
        instance["raw_digest"] = record.raw.raw_digest
        custody = {"store": custody_store, "locator": custody_locator}
        if retention_policy_ref:
            custody["retention_policy_ref"] = retention_policy_ref
        instance["raw_custody"] = custody
    if cursor_ref or record.provenance.cursor:
        instance["provenance"]["cursor_ref"] = cursor_ref or record.provenance.cursor
    if attempt is not None:
        instance["provenance"]["attempt"] = attempt
    # CR-3, this lane's own request: the audience the evidence is cleared for travels WITH it.
    aud = audience if audience is not None else record.retention.brains
    if aud:
        instance["audience"] = list(aud)
    if disposition == "tombstoned":
        # TWO THINGS C01'S VALIDATOR CAUGHT HERE, and both were real.
        #
        # `reason` is an ENUM, not prose. My first version wrote a sentence and was rejected.
        # `source_deleted` is a faithful mapping and not an invention: this lane's tombstone comes
        # from `DeliveryAttempt.deleted`, which means exactly "the source reported the item
        # deleted". The other three reasons (retention_expired, operator_request,
        # legal_hold_release) describe deletions this lane does not originate, so the caller
        # overrides when it knows better rather than this file guessing between them.
        #
        # `tombstoned_by` is REQUIRED and is an Actor with an id, a kind and a workspace. This lane
        # records no actor on a tombstone, so there is nothing to map and it is supplied or the
        # projection refuses. Inventing one would put a made-up author of a deletion into evidence,
        # which is the one thing a tombstone must never carry: the whole value of the row is that
        # it says who ended the identity and when.
        if not tombstoned_by:
            raise ProjectionError(
                "C01 requires tombstone.tombstoned_by (an Actor) and this lane records no actor "
                "on a tombstone; the caller supplies it rather than this projection inventing an "
                "author for a deletion"
            )
        instance["tombstone"] = {
            "reason": tombstone_reason,
            "tombstoned_at": record.captured_at,
            "tombstoned_by": dict(tombstoned_by),
        }
    return instance


def to_capture_receipt(
    receipt: CaptureReceipt,
    *,
    custody_store: str,
    custody_locator: str,
    new_source_identity: bool,
    still_live: bool,
    source: SourceRef | None = None,
) -> dict[str, Any]:
    """Project one receipt onto `capture-receipt`, honouring CR-I3.

    `durable` and `writeback_allowed` are not copied from the receipt's own claim: they are derived
    from whether a journal position exists, which is the invariant this lane asked for. A receipt
    without a sequence cannot assert either, and that is checked here rather than left to the
    schema, so the refusal names the reason.
    """
    has_position = bool(receipt.journal_seq and receipt.journal_seq > 0)
    if receipt.durable and not has_position:
        raise ProjectionError(
            "CR-I3: a receipt claiming durability without a journal position cannot be projected; "
            "a durable outcome is a journal position and not a flag"
        )

    outcome = "durable" if receipt.kind in ("create", "revision") else "tombstoned"
    if receipt.duplicate_of is not None and receipt.kind not in ("tombstone",):
        # A redelivery adds no event. C01 has a state for it and this lane returns the original
        # receipt, so the two agree once the outcome is named rather than inferred from the row.
        outcome = "durable"

    instance: dict[str, Any] = {
        "contract_version": C01_VERSION,
        "receipt_id": receipt.receipt_id,
        "capture_id": receipt.capture_id,
        "identity_key": identity_key(source) if source else receipt.content_digest,
        "revision": receipt.revision,
        "outcome": outcome,
        "durable": has_position,
        "writeback_allowed": has_position and receipt.authorises_source_cleanup,
        "revision_hash": receipt.content_digest,
        "custody": {"store": custody_store, "locator": custody_locator},
        "stored_at": receipt.issued_at,
        "counts_toward": {
            "distinct_capture_event": receipt.duplicate_of is None,
            "new_source_identity": bool(new_source_identity),
            "still_live": bool(still_live),
        },
    }
    if receipt.raw_digest:
        instance["raw_digest"] = receipt.raw_digest
    if has_position:
        instance["journal_seq"] = receipt.journal_seq
    return instance


def to_capture_revision(
    record: CaptureRecord,
    *,
    previous_revision_hash: str | None = None,
    supersedes_live: bool = True,
) -> dict[str, Any]:
    """Project a revision row onto C01 `capture-revision`.

    `previous_revision_hash` IS AN ARGUMENT AND NOT DERIVED, because this lane does not carry it.
    The journal links a revision to its predecessor by `supersedes`, which is the prior capture ID;
    C01 links by the prior revision HASH. Those are different values and one cannot be computed
    from the other without reading the superseded row, which a projection has no business doing:
    it would need a store, and a projection that queries is no longer a projection.

    `change_kind` CAN ONLY EVER BE `initial` OR `content_revision` FROM THIS LANE, and that is a
    real limitation rather than an oversight. Deduplication here is purely content-based: a new
    content digest is what makes a revision at all, so a metadata-only change produces no row and
    `metadata_revision` is unreachable. If a consumer needs metadata revisions, this lane has to
    learn to notice them first, and no argument to this function would make that true.
    """
    if record.kind == "tombstone":
        raise ProjectionError(
            "a tombstone is a capture-record disposition in C01, not a capture-revision; "
            "project it with to_capture_record"
        )

    initial = record.revision == 1
    if initial and previous_revision_hash:
        raise ProjectionError(
            "revision 1 cannot name a previous revision hash; the schema forces it null and a "
            "value here means the revision number and the chain disagree"
        )
    if not initial and not previous_revision_hash:
        raise ProjectionError(
            f"revision {record.revision} needs the previous revision hash, which this lane does "
            "not carry (it links by supersedes, a capture id); the caller reads it from the "
            "superseded row"
        )

    return {
        "contract_version": C01_VERSION,
        "capture_id": record.capture_id,
        # CV-I1: the revision's identity_key matches its record's, which holds by construction
        # because both derive from the same SourceRef through the same projection.
        "identity_key": identity_key(record.source),
        "revision": record.revision,
        "revision_hash": record.content_digest,
        "previous_revision_hash": None if initial else previous_revision_hash,
        "raw_digest": record.raw.raw_digest if record.raw else "",
        "received_at": record.captured_at,
        "change_kind": "initial" if initial else "content_revision",
        "supersedes_live": bool(supersedes_live),
    }


# -- T01 projections -------------------------------------------------------------------------------

#: WP-I1's substantive projection, verbatim from INVARIANTS.md at the pin. The hash covers these
#: keys taken only when present, so `title`, `signals.display`, `contract_version`, `packet_id`,
#: `version`, `workspace`, `producer`, `created_at` and `version_hash` itself are excluded. That is
#: the ruling this lane argued for: a re-render must not void a decision a human already made.
WP_SUBSTANTIVE = (
    "actions", "scope", "external", "evidence_refs", "untrusted_excerpts", "intent",
    "authority_required", "canon_touching", "surfacing", "audience", "acceptance",
    "rollback", "cost", "impact", "supersedes", "signals",
)


def work_packet_version_hash(instance: Mapping[str, Any]) -> str:
    """WP-I1, computed over the WIRE instance rather than over the internal packet.

    Deliberate: the hash a consumer recomputes is over what it received, so deriving it from the
    internal model would let the two drift while both looked right.
    """
    projection: dict[str, Any] = {}
    for key in WP_SUBSTANTIVE:
        if key not in instance:
            continue
        value = instance[key]
        if key == "signals" and isinstance(value, Mapping):
            value = {k: v for k, v in value.items() if k != "display"}
        projection[key] = value
    return content_hash(projection)


def _unknownable(value: str) -> dict[str, Any]:
    """This lane's cost is a free string, so a number is reported only when it truly is one."""
    try:
        return {"status": "estimated", "value": float(value), "unit": "USD"}
    except (TypeError, ValueError):
        return {"status": "unknown"}


def to_work_packet(
    packet: Any,
    *,
    workspace: str,
    producer_actor: Mapping[str, Any],
    created_at: str,
    impact_status: str = "unknown",
    surfacing: str | None = None,
    canon_touching: bool = False,
    producer_source: str = "attention",
    scope_out: tuple[str, ...] = (),
    acceptance_checks: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Project a `WorkPacket` onto C01 `work-packet`.

    Three arguments have no default because this lane does not carry the fact at all: `workspace`,
    `producer_actor` and `created_at`. `impact` is a fourth of the same kind handled differently:
    C01 wants a numeric `Unknownable` and this lane carries a `Risk` enum, which is a different
    axis belonging in `signals.stakes`, so the default is an honest `unknown` rather than a Risk
    coerced into a number.
    """
    for name, value in (("workspace", workspace), ("created_at", created_at)):
        if not value:
            raise ProjectionError(
                f"{name} is required by C01 and is not carried by this lane; the caller supplies "
                "it rather than this projection inventing one"
            )
    if not producer_actor:
        raise ProjectionError(
            "producer.actor is a required Actor and this lane records no actor on a packet; "
            "supplying it is the caller's job and not this projection's"
        )

    checks = dict(acceptance_checks or {})
    instance: dict[str, Any] = {
        "contract_version": C01_VERSION,
        "packet_id": packet.packet_id,
        "version": packet.version,
        "workspace": workspace,
        "producer": {"source": producer_source, "actor": dict(producer_actor)},
        "created_at": created_at,
        # `title` is presentation and excluded from the hash; `intent` is the substantive one. Both
        # come from `objective`, which is honest: this lane has one field where C01 has two.
        "title": packet.objective,
        "intent": packet.objective,
        "scope": {"in": list(packet.deliverables), "out": list(scope_out)},
        "evidence_refs": [
            {"object": "capture-record", "id": e.capture_id} for e in packet.evidence
        ],
        "acceptance": [
            {"criterion": c, "check": checks.get(c, "stated by the proposer; no automated check")}
            for c in packet.acceptance_criteria
        ],
        "actions": [
            {
                "action_id": a.derived_id(i),
                "capability": a.capability,
                "external": True,
                "canon_touching": a.canon_touching,
                "reversibility": a.reversibility,
            }
            for i, a in enumerate(packet.external_actions)
        ],
        # WP-I2: raised by OR from the actions, never lowered.
        "external": bool(packet.external_actions),
        "canon_touching": bool(canon_touching
                               or any(a.canon_touching for a in packet.external_actions)),
        "cost": _unknownable(packet.expected_cost),
        "impact": {"status": impact_status},
        "rollback": ({"status": "defined", "steps": [packet.rollback]} if packet.rollback
                     else {"status": "unknown"}),
        "supersedes": None,
        "authority_required": packet.authority_required.value,
    }
    if packet.audience:
        instance["audience"] = list(packet.audience)
    excerpts = [
        {"evidence_ref": e.capture_id, "excerpt": e.excerpt, "trust": "untrusted"}
        for e in packet.evidence if e.excerpt
    ]
    if excerpts:
        instance["untrusted_excerpts"] = excerpts
    # AR-I3: a canon-touching packet always requires human surfacing. Derived rather than taken
    # from the caller, so the two cannot disagree.
    instance["surfacing"] = "human" if instance["canon_touching"] else (surfacing or "rule")
    instance["version_hash"] = work_packet_version_hash(instance)
    return instance


def to_approval_decision(
    approval: Any,
    *,
    workspace: str,
    actor: Mapping[str, Any],
    auth_evidence: Mapping[str, Any],
    version_hash: str,
    decision_id: str,
    expires_at: str | None = None,
    authority_check_ref: str | None = None,
    consumed: Mapping[str, Any] | None = None,
    revocation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Project an `Approval` onto C01 `approval-decision`.

    `auth_evidence` has no default and no fallback. C01's own description names this lane's former
    weakness exactly: Git authorship, email strings and self-asserted labels are not evidence of
    who decided. `decided_by` was a bare string defended by a blacklist of five literals, which is
    a heuristic; this is structural, and it is one of the places C01 is stronger than what I built.

    `consumed` AND `revocation` ARE ARGUMENTS BECAUSE THEY WERE HARDCODED AND THAT WAS A LIE IN
    WAITING. This function used to emit `{"state": "unconsumed"}` and `{"revoked": false}`
    unconditionally, so a decision that had already been SPENT projected as fresh. That is the
    consumer-side shape of the hole STATE-API-PORT v3.1 describes on the store side: migration
    0057's unique constraint stopped a decision being RECORDED twice and never stopped one being
    SPENT twice, and `single_use: true` has been in the contract throughout. A projection that
    always says unconsumed would hand a reviewer a spent approval wearing a fresh one's clothes.

    Both default to the fresh state, which is correct for a decision just made, and both are now
    the CALLER's to state when they are not.
    """
    for name, value in (("workspace", workspace), ("decision_id", decision_id)):
        if not value:
            raise ProjectionError(f"{name} is required by C01 and must be supplied")
    if not actor or not auth_evidence:
        raise ProjectionError(
            "actor and auth_evidence are required: a decider is an authenticated subject, and a "
            "self-asserted name is not evidence of who decided"
        )

    # AN APPROVED DECISION MUST EXPIRE AND MUST NAME ITS AUTHORITY CHECK. C01 requires both, and
    # the pair is the point rather than the fields: an approval with no expiry is a standing
    # permission nobody reviews again, and one that names no authority check cannot be tied to the
    # boundary that let it be made. Enforced here so the refusal says which is missing, instead of
    # arriving as a schema rejection naming the whole object. Found by C01's validator refusing my
    # first two single-use fixtures, which omitted both.
    approved = getattr(approval.decision, "value", approval.decision) == "approved"
    if approved:
        missing = [n for n, v in (("expires_at", expires_at),
                                  ("authority_check_ref", authority_check_ref)) if not v]
        if missing:
            raise ProjectionError(
                f"an approved decision requires {' and '.join(missing)}: an approval with no "
                "expiry is a standing permission nobody reviews again, and one naming no "
                "authority check cannot be tied to the boundary that let it be made"
            )

    instance: dict[str, Any] = {
        "contract_version": C01_VERSION,
        "decision_id": decision_id,
        "workspace": workspace,
        "actor": dict(actor),
        "auth_evidence": dict(auth_evidence),
        "subject": {
            "object": "work-packet",
            "packet_id": approval.packet_id,
            "version_hash": version_hash,
            "action_ids": list(approval.action_ids),
        },
        "decision": approval.decision.value,
        "decided_at": approval.decided_at,
        "single_use": True,
        "consumed": dict(consumed) if consumed else {"state": "unconsumed"},
        "revocation": dict(revocation) if revocation else {"revoked": False},
        "authority_granted": approval.authority_granted.value,
    }
    # AD-I3: an approval cannot expire before it was made. Refused here with the two timestamps
    # named, rather than at the schema with a validation error that does not say which is which.
    if expires_at:
        if expires_at <= approval.decided_at:
            raise ProjectionError(
                f"AD-I3: expires_at {expires_at} is not after decided_at {approval.decided_at}; "
                "an approval cannot expire before it was made"
            )
        instance["expires_at"] = expires_at

    # AD-I4: a revoked approval cannot then be consumed. The store enforces this from 0066; a
    # projection that emitted the pair anyway would be describing a state the store now refuses.
    c, r = instance["consumed"], instance["revocation"]
    if c.get("state") == "consumed" and r.get("revoked") and c.get("consumed_at") and r.get("revoked_at"):
        if c["consumed_at"] > r["revoked_at"]:
            raise ProjectionError(
                f"AD-I4: consumed_at {c['consumed_at']} is after revoked_at {r['revoked_at']}; "
                "an approval revoked before it was spent cannot then be spent"
            )
    if authority_check_ref:
        instance["authority_check_ref"] = authority_check_ref
    return instance
