"""DRAFT capture contract. Replace with the pinned C01 version; do not fork it.

`CONTRACT_DRAFT_VERSION` and `contract_digest()` exist so that a receipt written today can be
told apart from one written against the real C01 schema. A capture journal that cannot say which
contract produced a row is a journal you cannot safely migrate.

CANONICALISATION IS THE LOAD-BEARING PART. Two deliveries of the same email are "the same event"
if and only if their canonical bytes hash the same, so `canonical_bytes` decides deduplication,
revision detection and the content digest all at once. It is deliberately strict: sorted keys, no
insignificant whitespace, UTF-8, and it REFUSES values it cannot round-trip rather than silently
coercing them. A lossy canonicaliser produces two identities for one email, and that is the
duplicate-event bug this packet was written to close.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Literal, Mapping

CONTRACT_DRAFT_VERSION = "capture-draft-0.1.0+cap04"

#: Event kinds a journal row can carry. `create` is a first sighting, `revision` a new content
#: digest for an identity already captured, `tombstone` a source-side deletion. A tombstone IS a
#: capture event: the fact that something was deleted is evidence and must survive replay.
EventKind = Literal["create", "revision", "tombstone"]


class ContractError(ValueError):
    """The payload cannot be represented in this contract. Never retryable unchanged."""


def _canonical(value: Any) -> Any:
    """Reject what JSON would quietly mangle, normalise what it would render ambiguously."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        # NaN/Inf serialise as bare tokens that are not JSON and do not survive a round trip.
        if value != value or value in (float("inf"), float("-inf")):
            raise ContractError("non-finite float is not representable in a capture payload")
        return value
    if isinstance(value, str):
        # A NUL byte survives Python and dies in Postgres text. Measured in this repo already,
        # so it is refused HERE, at the contract, where the refusal is one dead letter rather
        # than a red suite much later.
        if "\x00" in value:
            raise ContractError("NUL byte in capture payload string")
        return value
    if isinstance(value, _dt.datetime):
        return to_utc_iso(value)
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if not isinstance(k, str):
                raise ContractError(f"non-string mapping key {k!r} in capture payload")
            if "\x00" in k:
                raise ContractError("NUL byte in capture payload key")
            out[k] = _canonical(v)
        return {k: out[k] for k in sorted(out)}
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    raise ContractError(f"type {type(value).__name__} is not representable in a capture payload")


def canonical_bytes(payload: Mapping[str, Any]) -> bytes:
    """The exact bytes that get hashed. Deterministic across processes and Python versions."""
    return json.dumps(
        _canonical(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def digest_of(payload: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(payload)).hexdigest()


def digest_of_bytes(blob: bytes) -> str:
    return "sha256:" + hashlib.sha256(blob).hexdigest()


def to_utc_iso(when: _dt.datetime) -> str:
    """Naive datetimes are refused. "Which timezone was that" is not a question a journal answers."""
    if when.tzinfo is None:
        raise ContractError("naive datetime in capture payload; attach a timezone")
    return (
        when.astimezone(_dt.timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


@dataclass(frozen=True)
class SourceRef:
    """The identity triple. `source_id` is the provider's own ID, never a title or subject line.

    Title-based deduplication is the E06/E23 gap: two different meetings called "Weekly" collapse
    into one event and the second is lost. This type has no title field, on purpose.
    """

    provider: str
    account_id: str
    source_id: str

    def __post_init__(self) -> None:
        for name in ("provider", "account_id", "source_id"):
            v = getattr(self, name)
            if not isinstance(v, str) or not v.strip():
                raise ContractError(f"SourceRef.{name} must be a non-empty string")
            if "\x00" in v:
                raise ContractError(f"NUL byte in SourceRef.{name}")

    @property
    def source_key(self) -> str:
        """Stable identity hash. Field-separated so `a|b` and `ab|` cannot collide."""
        joined = "\x1f".join((self.provider, self.account_id, self.source_id))
        return "sk_" + hashlib.sha256(joined.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True)
class Provenance:
    """Where this came from and how to audit that the run was complete."""

    connector: str
    connector_version: str
    run_id: str
    cursor: str | None = None
    fetched_at: str | None = None


@dataclass(frozen=True)
class RetentionPolicy:
    """Consent and retention travel WITH the evidence, not in a config file somewhere else.

    `brains` is the authorised audience: which Infinite Brains may be queried for context on this
    event. An empty tuple means none, and that is a legitimate answer for a restricted source.
    """

    consent: str = "authorized-source-manifest"
    sensitivity: str = "normal"
    retain_days: int | None = None
    brains: tuple[str, ...] = ()


@dataclass(frozen=True)
class RawCustodyRef:
    """A durable immutable reference to the original bytes. Never the bytes themselves."""

    raw_digest: str
    byte_len: int
    media_type: str
    encrypted: bool
    custody: str = "local-cas"


@dataclass(frozen=True)
class CaptureRecord:
    """One committed journal row.

    `capture_id` is derived, not assigned: two processes that see the same delivery derive the
    same ID without talking to each other, which is what makes the door idempotent under retry.
    """

    capture_id: str
    source: SourceRef
    kind: EventKind
    revision: int
    content_digest: str
    raw: RawCustodyRef | None
    attachments: tuple[RawCustodyRef, ...]
    occurred_at: str
    captured_at: str
    participants: tuple[str, ...]
    retention: RetentionPolicy
    provenance: Provenance
    supersedes: str | None = None
    contract_version: str = CONTRACT_DRAFT_VERSION

    @staticmethod
    def derive_id(source_key: str, content_digest: str) -> str:
        joined = "\x1f".join((source_key, content_digest))
        return "cap_" + hashlib.sha256(joined.encode("utf-8")).hexdigest()[:40]

    def to_wire(self) -> dict[str, Any]:
        d = asdict(self)
        d["source"] = {**asdict(self.source), "source_key": self.source.source_key}
        d["participants"] = list(self.participants)
        d["attachments"] = [asdict(a) for a in self.attachments]
        d["retention"] = {**asdict(self.retention), "brains": list(self.retention.brains)}
        return d


@dataclass(frozen=True)
class CaptureReceipt:
    """Proof the event is safe. The ONLY thing that authorises source-side cleanup.

    `durable` is never set optimistically: it is written after the journal's own commit returned,
    so a receipt object that exists at all has already survived the write it is claiming.
    """

    receipt_id: str
    capture_id: str
    source_key: str
    journal_seq: int
    content_digest: str
    raw_digest: str | None
    kind: EventKind
    revision: int
    issued_at: str
    duplicate_of: str | None = None
    durable: bool = True
    contract_version: str = CONTRACT_DRAFT_VERSION

    @property
    def authorises_source_cleanup(self) -> bool:
        return bool(self.durable and self.journal_seq > 0)


@dataclass(frozen=True)
class DeadLetter:
    """A delivery that cannot become an event. Explicitly NOT a capture; never counted as one."""

    dead_letter_id: str
    source_key: str | None
    reason: str
    detail: str
    received_at: str
    raw_digest: str | None = None
    attempts: int = 1


def contract_digest() -> str:
    """Digest of the draft's shape, so a handoff can pin exactly what was built against."""
    shape = {
        "version": CONTRACT_DRAFT_VERSION,
        "types": {
            "SourceRef": ["provider", "account_id", "source_id"],
            "Provenance": ["connector", "connector_version", "run_id", "cursor", "fetched_at"],
            "RetentionPolicy": ["consent", "sensitivity", "retain_days", "brains"],
            "RawCustodyRef": ["raw_digest", "byte_len", "media_type", "encrypted", "custody"],
            "CaptureRecord": [
                "capture_id", "source", "kind", "revision", "content_digest", "raw",
                "attachments", "occurred_at", "captured_at", "participants", "retention",
                "provenance", "supersedes", "contract_version",
            ],
            "CaptureReceipt": [
                "receipt_id", "capture_id", "source_key", "journal_seq", "content_digest",
                "raw_digest", "kind", "revision", "issued_at", "duplicate_of", "durable",
                "contract_version",
            ],
            "DeadLetter": [
                "dead_letter_id", "source_key", "reason", "detail", "received_at", "raw_digest",
                "attempts",
            ],
        },
        "rules": {
            "identity": "sha256(provider|account_id|source_id), never title",
            "capture_id": "sha256(source_key|content_digest)",
            "dedup": "same (source_key, content_digest) is the same event",
            "revision": "new content_digest for a known source_key",
            "receipt": "issued only after journal commit returns",
        },
    }
    return digest_of(shape)
