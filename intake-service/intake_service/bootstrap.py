"""The composition root for durable capture. The ONLY place that knows `ingest.capture` exists.

`capture_gate.py` deliberately imports nothing from `ingest`: it takes a journal-shaped object so
the service stays deployable on its own and a wiring mistake cannot break the door's import. This
file is the other half of that arrangement, and it is the only file in `intake-service/` that
reaches into the capture package.

OFF UNLESS THREE THINGS ARE TRUE, and each of them is a deliberate act:

    INTAKE_CAPTURE=on              the operator turned it on
    INTAKE_CAPTURE_DIR=<path>      custody and journal have somewhere to live
    INTAKE_CAPTURE_KEY=<64 hex>    a 32-byte key exists

Any one missing returns `None`, which `create_app(capture=None)` treats as "behave exactly as
before". `build_gate` never invents a key and never falls back to plaintext custody: a missing key
is a refusal, because a service that quietly stores a mailbox unencrypted because a variable was
unset is worse than one that will not start.

WHY IT REFUSES RATHER THAN WARNS. `host.require_token()` in this same package establishes the
pattern and the reason: a door that boots without its credential looks healthy, accepts everything,
and the first evidence is a stranger's row on the operator's board. The same argument applies to
custody, so `INTAKE_CAPTURE=on` with a broken configuration raises at construction instead of
starting a service that captures nothing while reporting 201s.

NOTHING HERE ACTIVATES ANYTHING. Turning capture on for a running door changes what a 201 means to
a connector that already trusts it, which is an operational decision with its own authorisation.
This file makes that decision expressible and testable; it does not make it.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from .capture_gate import CaptureGate, DurableCaptureGate, NullCaptureGate

#: Where the capture package lives, relative to this service. Added to `sys.path` only when
#: capture is actually being built, so an unconfigured door never touches it.
_INGEST = Path(__file__).resolve().parents[2] / "ingest"

ENV_ENABLED = "INTAKE_CAPTURE"
ENV_DIR = "INTAKE_CAPTURE_DIR"
ENV_KEY = "INTAKE_CAPTURE_KEY"


class CaptureConfigError(RuntimeError):
    """Capture was asked for and cannot be built. Refuse to boot; do not degrade silently."""


def enabled(env: dict[str, str] | None = None) -> bool:
    env = os.environ if env is None else env
    return (env.get(ENV_ENABLED) or "").strip().lower() in ("on", "1", "true", "yes")


def build_gate(env: dict[str, str] | None = None, *, run_id: str = "door") -> CaptureGate | None:
    """Return a configured gate, `None` when capture is off, or raise when it is misconfigured."""
    env = os.environ if env is None else env
    if not enabled(env):
        return None

    root = (env.get(ENV_DIR) or "").strip()
    if not root:
        raise CaptureConfigError(
            f"{ENV_ENABLED} is on but {ENV_DIR} is unset: durable capture needs somewhere durable"
        )
    key_hex = (env.get(ENV_KEY) or "").strip()
    if not key_hex:
        raise CaptureConfigError(
            f"{ENV_ENABLED} is on but {ENV_KEY} is unset. Custody holds private mail, transcripts "
            "and attachments; this will not fall back to plaintext"
        )
    try:
        key = bytes.fromhex(key_hex)
    except ValueError as exc:
        raise CaptureConfigError(f"{ENV_KEY} is not hex: {exc}") from exc
    if len(key) != 32:
        raise CaptureConfigError(
            f"{ENV_KEY} decodes to {len(key)} bytes; AES-256-GCM needs exactly 32"
        )

    if str(_INGEST) not in sys.path:
        sys.path.insert(0, str(_INGEST))
    try:
        from capture.contracts import Provenance, SourceRef
        from capture.journal import CaptureJournal, DeliveryAttempt
        from capture.raw_custody import AesGcmCipher, RawStore
        from capture.sqlite_store import SqliteJournalStore
    except ImportError as exc:  # pragma: no cover - a broken checkout, not a config error
        raise CaptureConfigError(f"the capture package is not importable: {exc}") from exc

    base = Path(root)
    store = SqliteJournalStore(base / "journal.sqlite3")
    custody = RawStore(base / "custody", AesGcmCipher(key))
    journal = CaptureJournal(store, custody)

    return DurableCaptureGate(
        journal,
        delivery_attempt_cls=DeliveryAttempt,
        source_ref_cls=SourceRef,
        provenance=Provenance(connector="intake-door", connector_version="1", run_id=run_id),
    )


def describe(env: dict[str, str] | None = None) -> dict[str, Any]:
    """What an operator needs to see before turning this on, without printing the key."""
    env = os.environ if env is None else env
    key = (env.get(ENV_KEY) or "").strip()
    return {
        "capture_enabled": enabled(env),
        "capture_dir": (env.get(ENV_DIR) or "") or None,
        "key_present": bool(key),
        "key_length_bytes": (len(key) // 2) if key else 0,
        "backing_store": "sqlite (disposable reference); the Postgres JournalStore is pending R01",
        "note": "off means this door behaves exactly as it did before capture existed",
    }


__all__ = [
    "CaptureConfigError",
    "NullCaptureGate",
    "build_gate",
    "describe",
    "enabled",
]
