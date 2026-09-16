"""The idempotent capture door, and the only place a `CaptureReceipt` is manufactured.

THE THREE OUTCOMES, and there is no fourth. A delivery attempt either becomes a durable capture
(receipt), becomes an explicit dead letter (never counted as a capture), or fails transiently and
raises so the connector keeps it. "Silently dropped" is not reachable from here, and neither is
"acknowledged but not stored": the receipt is constructed from the sequence number the store
returned after its commit, so it cannot exist before the row does.

THE FOUR CLASSIFICATIONS of an attempt against a known identity:

    unseen (source_key, content_digest)      -> create   (revision 1)
    known source_key, new content_digest     -> revision (revision n+1, supersedes head)
    (source_key, content_digest) already a row-> duplicate (the ORIGINAL receipt, re-issued)
    deletion signal                          -> tombstone (a new row; identity stops being live)

Note what the duplicate rule is keyed on: the full `(source_key, content_digest)` pair, not the
head. A provider that redelivers an OLD revision of a thread must not manufacture a new event
either, and keying on the head alone would do exactly that.

WHY A TOMBSTONE IS A ROW AND NOT A FLAG. Deletion is evidence. Replay of the journal has to
reproduce "this existed and then was deleted", and a mutable `live` column cannot be replayed.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import secrets
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from .contracts import (
    CaptureRecord,
    CaptureReceipt,
    ContractError,
    DeadLetter,
    Provenance,
    RawCustodyRef,
    RetentionPolicy,
    SourceRef,
    digest_of,
    to_utc_iso,
)
from .raw_custody import CustodyError, RawStore
from .state_port import OutboxIntent


class TransientCaptureError(RuntimeError):
    """The attempt may succeed unchanged later. THE CONNECTOR STILL OWNS IT.

    Raised rather than returned, because a connector that treats this as an outcome object is one
    `if` away from acknowledging a source item that was never captured.
    """


@dataclass(frozen=True)
class Attachment:
    """One attachment. `fetch_error` set means the connector could not retrieve the bytes.

    An attachment it could not fetch is not an attachment it may skip: the capture fails whole.
    """

    filename: str
    media_type: str
    blob: bytes | None = None
    fetch_error: str | None = None


@dataclass(frozen=True)
class DeliveryAttempt:
    """What a connector hands the door. One source item, one attempt."""

    source: SourceRef
    payload: Mapping[str, Any]
    occurred_at: str
    provenance: Provenance
    raw_blob: bytes | None = None
    raw_media_type: str = "application/json"
    attachments: Sequence[Attachment] = ()
    participants: Sequence[str] = ()
    retention: RetentionPolicy = field(default_factory=RetentionPolicy)
    deleted: bool = False
    intents: Sequence[OutboxIntent] = ()


@dataclass(frozen=True)
class CaptureOutcome:
    """What the door returns. Exactly one of `receipt` or `dead_letter` is set."""

    receipt: CaptureReceipt | None = None
    dead_letter: DeadLetter | None = None
    duplicate: bool = False

    @property
    def captured(self) -> bool:
        return self.receipt is not None

    @property
    def new_event(self) -> bool:
        """True only when this attempt added a row. The number the acceptance evidence counts."""
        return self.receipt is not None and not self.duplicate


def _utcnow() -> str:
    return to_utc_iso(_dt.datetime.now(_dt.timezone.utc))


class CaptureJournal:
    """Durable revision-aware capture over a `JournalStore` and a `RawStore`."""

    def __init__(
        self,
        store: Any,
        raw: RawStore,
        *,
        clock: Callable[[], str] = _utcnow,
        fault: Callable[[str], None] | None = None,
    ):
        self.store = store
        self.raw = raw
        self.clock = clock
        self._fault = fault or (lambda _point: None)

    # -- the door ------------------------------------------------------------------------------

    def capture(self, attempt: DeliveryAttempt) -> CaptureOutcome:
        now = self.clock()
        source_key = attempt.source.source_key

        # 1. Canonicalise. A payload that cannot be represented is poison: it will fail
        #    identically forever, so it dead-letters instead of consuming retries.
        try:
            content_digest = self._content_digest(attempt)
        except ContractError as exc:
            return self._dead_letter(source_key, "uncanonicalizable-payload", str(exc), now)

        capture_id = CaptureRecord.derive_id(source_key, content_digest)

        # 2. Idempotency BEFORE custody. A redelivery must not rewrite blobs or restage intents.
        existing = self.store.get(capture_id)
        if existing is not None:
            return CaptureOutcome(
                receipt=self._receipt_from_row(existing, now, duplicate_of=capture_id),
                duplicate=True,
            )

        head = self.store.head(source_key)
        if attempt.deleted:
            kind, revision, supersedes = "tombstone", (head.revision if head else 0), (
                head.capture_id if head else None
            )
        elif head is None:
            kind, revision, supersedes = "create", 1, None
        else:
            kind, revision, supersedes = "revision", head.revision + 1, head.capture_id

        # 3. Raw custody FIRST and WHOLE. Any failure here means no row and no receipt; the
        #    residue is orphan bytes, which cost disk and lose nothing.
        try:
            raw_ref, attachment_refs = self._store_raw(attempt)
        except CustodyError as exc:
            # A policy refusal (too big, wrong media type) is permanent for this payload.
            return self._dead_letter(source_key, "custody-refused", str(exc), now)
        except TransientCaptureError:
            raise
        self._fault("after_raw")

        record = CaptureRecord(
            capture_id=capture_id,
            source=attempt.source,
            kind=kind,  # type: ignore[arg-type]
            revision=revision,
            content_digest=content_digest,
            raw=raw_ref,
            attachments=tuple(attachment_refs),
            occurred_at=attempt.occurred_at,
            captured_at=now,
            participants=tuple(attempt.participants),
            retention=attempt.retention,
            provenance=attempt.provenance,
            supersedes=supersedes,
        )

        # 4. Commit the row and its hygiene intents atomically. A tombstone stages no intent:
        #    there is nothing left at the source to mark read.
        intents = () if kind == "tombstone" else tuple(attempt.intents)
        seq, created = self.store.append(
            {
                "capture_id": capture_id,
                "source_key": source_key,
                "kind": kind,
                "revision": revision,
                "content_digest": content_digest,
                "raw_digest": raw_ref.raw_digest if raw_ref else None,
                "supersedes": supersedes,
                "committed_at": now,
                "record": record.to_wire(),
            },
            intents,
        )

        # 5. Only now does a receipt exist -- and it is built from what the store COMMITTED, not
        #    from what this call intended. `created is False` here means the store found the
        #    capture_id already present (a racing writer, or a retry after an ambiguous commit).
        #    In that case the row on disk is authoritative and this call's computed kind/revision
        #    are stale, so re-reading is the only way the receipt cannot lie about the row.
        if not created:
            committed = self.store.get(capture_id)
            if committed is None:  # pragma: no cover - a store that lies about its own writes
                raise TransientCaptureError(
                    f"store reported {capture_id} as existing but cannot read it back"
                )
            return CaptureOutcome(
                receipt=self._receipt_from_row(committed, now, duplicate_of=capture_id),
                duplicate=True,
            )

        return CaptureOutcome(
            receipt=CaptureReceipt(
                receipt_id="rcpt_" + secrets.token_hex(12),
                capture_id=capture_id,
                source_key=source_key,
                journal_seq=seq,
                content_digest=content_digest,
                raw_digest=raw_ref.raw_digest if raw_ref else None,
                kind=kind,  # type: ignore[arg-type]
                revision=revision,
                issued_at=now,
                durable=True,
            ),
            duplicate=False,
        )

    # -- internals -----------------------------------------------------------------------------

    def _content_digest(self, attempt: DeliveryAttempt) -> str:
        """What makes this delivery "the same event" as another.

        A tombstone's digest is derived from the identity rather than the payload, so a provider
        that reports the same deletion twice dedups instead of stacking tombstones.
        """
        if attempt.deleted:
            return digest_of(
                {"tombstone": True, "source_key": attempt.source.source_key}
            )
        body = digest_of(dict(attempt.payload))
        # Attachment identity is part of content identity: the same body with a new attachment is
        # a revision, not a duplicate. Digests only -- no bytes flow into the content hash.
        att = [
            {"filename": a.filename, "media_type": a.media_type, "len": len(a.blob or b"")}
            for a in attempt.attachments
        ]
        return digest_of({"body": body, "attachments": att})

    def _store_raw(
        self, attempt: DeliveryAttempt
    ) -> tuple[RawCustodyRef | None, list[RawCustodyRef]]:
        for a in attempt.attachments:
            if a.fetch_error is not None:
                # Partial evidence is not evidence. Fail the whole attempt and keep it retryable.
                raise TransientCaptureError(
                    f"attachment {a.filename!r} could not be retrieved: {a.fetch_error}"
                )
            if a.blob is None:
                raise TransientCaptureError(f"attachment {a.filename!r} has no bytes and no error")

        raw_ref = None
        if attempt.raw_blob is not None:
            raw_ref = self.raw.put(attempt.raw_blob, attempt.raw_media_type)
        refs = [self.raw.put(a.blob or b"", a.media_type) for a in attempt.attachments]
        return raw_ref, refs

    def _receipt_from_row(self, row: Any, now: str, duplicate_of: str) -> CaptureReceipt:
        return CaptureReceipt(
            receipt_id="rcpt_" + secrets.token_hex(12),
            capture_id=row.capture_id,
            source_key=row.source_key,
            journal_seq=row.journal_seq,
            content_digest=row.content_digest,
            raw_digest=row.raw_digest,
            kind=row.kind,
            revision=row.revision,
            issued_at=now,
            duplicate_of=duplicate_of,
            durable=True,
        )

    def _dead_letter(self, source_key: str, reason: str, detail: str, now: str) -> CaptureOutcome:
        dl_id = "dl_" + hashlib.sha256(
            "\x1f".join((source_key, reason, detail)).encode("utf-8")
        ).hexdigest()[:24]
        entry = DeadLetter(
            dead_letter_id=dl_id,
            source_key=source_key,
            reason=reason,
            detail=detail[:2000],
            received_at=now,
        )
        self.store.dead_letter(
            {
                "dead_letter_id": entry.dead_letter_id,
                "source_key": entry.source_key,
                "reason": entry.reason,
                "detail": entry.detail,
                "received_at": entry.received_at,
                "raw_digest": entry.raw_digest,
                "attempts": entry.attempts,
            }
        )
        return CaptureOutcome(dead_letter=entry)

    # -- coverage reads ------------------------------------------------------------------------

    def is_live(self, source: SourceRef) -> bool:
        head = self.store.head(source.source_key)
        return bool(head and head.live)

    def frontier(self) -> int:
        return self.store.max_seq()
