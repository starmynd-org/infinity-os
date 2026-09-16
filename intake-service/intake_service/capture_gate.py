"""Durable capture in front of the door's acknowledgement, and OFF until someone turns it on.

WHAT THIS CLOSES. `app.py` answers 201 once `store.apply` has landed an objective row. That row is
a work item, not evidence: the original provider payload is not kept anywhere, so a body that was
truncated, mis-parsed or later edited cannot be checked against what actually arrived. I01's
invariant -- *never acknowledge until a durable capture receipt exists* -- needs a step before the
row, and this is that step.

WHY IT DEFAULTS TO OFF, and why that is not timidity. This door is running on the operator's host
against a live store. A change that alters what a 201 means, on a service a connector already
trusts, is an operational change and belongs to a separate authorised activation -- not to a code
packet. So `create_app` takes `capture=None` and behaves EXACTLY as it did before; the gate only
exists when a composition root passes one in. `NullCaptureGate` makes "capture is deliberately not
configured here" a thing you can write down rather than an omission you have to notice.

WHY IT IMPORTS NOTHING FROM `ingest`. `connectors/__init__.py`'s rule is that a connector imports
the door's contract and the standard library. The same discipline is what keeps THIS service
deployable on its own: the gate takes a journal-shaped object, and `bootstrap.py` is the only
place that knows `ingest.capture` exists. A wiring mistake there cannot break the door's own
import.

THE FAILURE SEMANTICS ARE THE POINT. If capture cannot complete, the door returns 503 with
`retry: true` and writes NOTHING -- no objective row, no acknowledgement. The connector still holds
the item, which is exactly what `door.py` says a 503 means and what the existing store-down branch
already does. There is no path where the gate fails and the item is taken in anyway; that path is
the bug the gate was added to prevent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol


class CaptureUnavailable(RuntimeError):
    """Capture could not complete. Nothing may be acknowledged; the connector keeps the item."""


class CaptureRefused(ValueError):
    """These bytes can never be captured. Permanent for this payload; retrying cannot help."""


@dataclass(frozen=True)
class GateResult:
    """What the door learns from the gate. Deliberately small."""

    capture_id: str
    receipt_id: str
    journal_seq: int
    content_digest: str
    duplicate: bool

    def as_answer_fields(self) -> dict[str, Any]:
        """Additive keys for the door's JSON answer. The contract's own keys are untouched."""
        return {
            "capture_id": self.capture_id,
            "capture_receipt": self.receipt_id,
            "capture_seq": self.journal_seq,
        }


class CaptureGate(Protocol):
    """The one call the door makes, before it writes anything."""

    def capture(self, payload: Any, raw_body: Mapping[str, Any]) -> GateResult | None:
        """Return a result once evidence is durable, or raise. `None` means capture is disabled."""
        ...


class NullCaptureGate:
    """Capture is deliberately not configured. Written down rather than left implicit."""

    enabled = False

    def capture(self, payload: Any, raw_body: Mapping[str, Any]) -> None:
        return None


class DurableCaptureGate:
    """Captures through an injected journal before the door acknowledges anything.

    `journal` is anything with I01's `capture(DeliveryAttempt) -> CaptureOutcome` shape, and the
    three constructor arguments are the types to build that attempt with. They are passed in
    rather than imported so this module stays free of `ingest`.
    """

    enabled = True

    def __init__(self, journal, *, delivery_attempt_cls, source_ref_cls, provenance,
                 connector_version: str = "intake-door/1"):
        self.journal = journal
        self._attempt = delivery_attempt_cls
        self._source = source_ref_cls
        self._provenance = provenance
        self.connector_version = connector_version

    def capture(self, payload: Any, raw_body: Mapping[str, Any]) -> GateResult:
        import json

        # IDENTITY COMES FROM THE CONNECTOR'S OWN KEY, never from the title. `source_signature` is
        # the key the contract already asks a connector to send and already dedups on, so the
        # capture journal and the door agree about what "the same item" means instead of holding
        # two different opinions about one delivery.
        source_ref = self._source(
            payload.origin or "intake",
            payload.source or "unknown-account",
            payload.signature(),
        )
        try:
            body = json.dumps(raw_body, ensure_ascii=False, sort_keys=True).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise CaptureRefused(f"the request body is not serialisable evidence: {exc}") from exc

        attempt = self._attempt(
            source=source_ref,
            payload=dict(raw_body),
            occurred_at=payload.timestamp or "",
            provenance=self._provenance,
            raw_blob=body,
            raw_media_type="application/json",
            participants=(payload.author,) if payload.author else (),
        )

        try:
            outcome = self.journal.capture(attempt)
        except Exception as exc:  # noqa: BLE001 - every failure here is "do not acknowledge"
            raise CaptureUnavailable(
                f"durable capture did not complete, so nothing was taken in: "
                f"{exc.__class__.__name__}: {exc}"
            ) from exc

        if outcome.dead_letter is not None:
            raise CaptureRefused(
                f"this payload cannot be captured ({outcome.dead_letter.reason}): "
                f"{outcome.dead_letter.detail}"
            )
        if outcome.receipt is None or not outcome.receipt.authorises_source_cleanup:
            raise CaptureUnavailable(
                "capture returned no durable receipt; refusing to acknowledge"
            )

        r = outcome.receipt
        return GateResult(
            capture_id=r.capture_id,
            receipt_id=r.receipt_id,
            journal_seq=r.journal_seq,
            content_digest=r.content_digest,
            duplicate=outcome.duplicate,
        )
