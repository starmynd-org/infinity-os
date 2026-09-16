"""F-CAPTURE: the golden capture fixture, built WITHOUT importing the implementation under test.

The shared fixture contract fixes the shape: 72 original synthetic identities (50 email, 12 Slack,
10 meeting), 10 exact repeated deliveries, five new content revisions and four distinct
tombstones -- 91 attempts, 81 distinct capture events, 72 source identities, 68 still live.

WHY THIS FILE RE-IMPLEMENTS THE ID RULES. `COMMON-FIXTURES-AND-EVIDENCE.md` requires that
"expected IDs, revisions, hashes and dispositions are explicit in a golden manifest, not computed
by the implementation being tested". So `_source_key` and `_capture_id` below are a SECOND,
independent implementation of the identity rules, written from the contract's prose rather than
imported from `contracts.py`. A test that asserts the two agree is a real cross-check; a test that
imports the implementation to compute its own expectations proves nothing.

If someone changes the hashing rule in `contracts.py` and the suite stays green, this file was
imported from there by mistake. It imports nothing from the package.

EVERY IDENTITY IS SYNTHETIC. Addresses are `@example.invalid`, a reserved TLD that cannot resolve.
No production mount, no real person, no inherited credential.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

FIXTURE_ID = "F-CAPTURE/1"

# -- the second implementation of the identity rules ----------------------------------------------


def _source_key(provider: str, account_id: str, source_id: str) -> str:
    joined = "\x1f".join((provider, account_id, source_id))
    return "sk_" + hashlib.sha256(joined.encode("utf-8")).hexdigest()[:32]


def _canon(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _canon(value[k]) for k in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_canon(v) for v in value]
    return value


def _digest(payload: Any) -> str:
    blob = json.dumps(
        _canon(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(blob).hexdigest()


def _content_digest(payload: dict[str, Any]) -> str:
    """Mirrors the door's rule: body digest plus attachment descriptors, no attachments here."""
    return _digest({"body": _digest(payload), "attachments": []})


def _tombstone_digest(source_key: str) -> str:
    return _digest({"source_key": source_key, "tombstone": True})


def _capture_id(source_key: str, content_digest: str) -> str:
    joined = "\x1f".join((source_key, content_digest))
    return "cap_" + hashlib.sha256(joined.encode("utf-8")).hexdigest()[:40]


# -- the population -------------------------------------------------------------------------------

#: Identities that receive a byte-identical second delivery. Must add ZERO events.
EXACT_REPEATS = ("e000", "e001", "e002", "e003", "e004", "e005", "e006", "e007", "s000", "m000")
#: Identities whose content changes. Each must add exactly one `revision` event.
REVISED = ("e010", "e011", "s002", "m002", "m003")
#: Identities the source deletes. Each must add exactly one `tombstone` event and stop being live.
TOMBSTONED = ("e020", "e021", "s005", "m005")


@dataclass(frozen=True)
class FixtureAttempt:
    """One delivery attempt, as a connector would present it. Pure data."""

    ordinal: int
    provider: str
    account_id: str
    source_id: str
    label: str
    payload: dict[str, Any]
    occurred_at: str
    participants: tuple[str, ...]
    deleted: bool = False
    expect: str = "create"  # create | duplicate | revision | tombstone

    @property
    def source_key(self) -> str:
        return _source_key(self.provider, self.account_id, self.source_id)

    @property
    def content_digest(self) -> str:
        if self.deleted:
            return _tombstone_digest(self.source_key)
        return _content_digest(self.payload)

    @property
    def capture_id(self) -> str:
        return _capture_id(self.source_key, self.content_digest)

    @property
    def raw_blob(self) -> bytes:
        return json.dumps(self.payload, sort_keys=True).encode("utf-8")


def _email_payload(n: int, body_version: int = 1) -> dict[str, Any]:
    return {
        "subject": "Weekly sync" if n % 7 == 0 else f"Invoice {2000 + n}",
        "from": f"person{n:03d}@example.invalid",
        "to": ["andrew@example.invalid"],
        "body": f"Message body for email {n:03d}, version {body_version}.",
        "message_id": f"<msg-{n:03d}@example.invalid>",
    }


def _slack_payload(n: int, body_version: int = 1) -> dict[str, Any]:
    return {
        "channel": f"C{n:04d}",
        "user": f"U{n:04d}",
        "text": f"Slack message {n:03d}, version {body_version}.",
        "thread_ts": f"17000000{n:02d}.0001",
    }


def _meeting_payload(n: int, body_version: int = 1) -> dict[str, Any]:
    return {
        "title": "Weekly" if n % 3 == 0 else f"Client call {n:03d}",
        "duration_s": 1800 + n,
        "participants": [f"person{n:03d}@example.invalid", "andrew@example.invalid"],
        "transcript": f"Transcript segment for meeting {n:03d}, version {body_version}.",
    }


def _spec() -> list[tuple[str, str, str, str, Any]]:
    """(label, provider, account_id, source_id, payload_builder). 50 + 12 + 10 = 72."""
    out: list[tuple[str, str, str, str, Any]] = []
    for n in range(50):
        out.append((f"e{n:03d}", "email", "acct-mail-1", f"uid-{n:05d}", _email_payload))
    for n in range(12):
        out.append((f"s{n:03d}", "slack", "acct-slack-1", f"C{n:04d}.17000000{n:02d}", _slack_payload))
    for n in range(10):
        out.append((f"m{n:03d}", "tldv", "acct-tldv-1", f"meeting-{n:04d}", _meeting_payload))
    return out


def build_attempts() -> list[FixtureAttempt]:
    """The 91 attempts, in delivery order. Deterministic; no randomness anywhere."""
    spec = _spec()
    attempts: list[FixtureAttempt] = []
    ordinal = 0

    def add(label, provider, account, source_id, payload, deleted, expect, when):
        nonlocal ordinal
        ordinal += 1
        n = int(label[1:])
        attempts.append(
            FixtureAttempt(
                ordinal=ordinal,
                provider=provider,
                account_id=account,
                source_id=source_id,
                label=label,
                payload=payload,
                occurred_at=when,
                participants=(f"person{n:03d}@example.invalid", "andrew@example.invalid"),
                deleted=deleted,
                expect=expect,
            )
        )

    # 1. 72 originals.
    for i, (label, provider, account, source_id, builder) in enumerate(spec):
        n = int(label[1:])
        add(label, provider, account, source_id, builder(n, 1), False, "create",
            f"2026-09-01T{8 + i % 12:02d}:{i % 60:02d}:00Z")

    by_label = {s[0]: s for s in spec}

    # 2. 10 byte-identical redeliveries. Same payload object contents -> same digest -> no event.
    for label in EXACT_REPEATS:
        _, provider, account, source_id, builder = by_label[label]
        n = int(label[1:])
        add(label, provider, account, source_id, builder(n, 1), False, "duplicate",
            f"2026-09-02T09:{n % 60:02d}:00Z")

    # 3. 5 content revisions. Edited body AND, for e010, an edited title -- the case that used to
    #    collapse into a duplicate when identity was derived from the title.
    for label in REVISED:
        _, provider, account, source_id, builder = by_label[label]
        n = int(label[1:])
        payload = builder(n, 2)
        if label == "e010":
            payload = {**payload, "subject": "Weekly sync"}
        add(label, provider, account, source_id, payload, False, "revision",
            f"2026-09-03T10:{n % 60:02d}:00Z")

    # 4. 4 tombstones.
    for label in TOMBSTONED:
        _, provider, account, source_id, builder = by_label[label]
        n = int(label[1:])
        add(label, provider, account, source_id, builder(n, 1), True, "tombstone",
            f"2026-09-04T11:{n % 60:02d}:00Z")

    return attempts


def build_golden_manifest() -> dict[str, Any]:
    """The expected result, stated independently of any implementation."""
    attempts = build_attempts()
    spec = _spec()

    identities = {}
    for label, provider, account, source_id, builder in spec:
        n = int(label[1:])
        sk = _source_key(provider, account, source_id)
        final_revision = 2 if label in REVISED else 1
        live = label not in TOMBSTONED
        identities[label] = {
            "source_key": sk,
            "provider": provider,
            "first_capture_id": _capture_id(sk, _content_digest(builder(n, 1))),
            "final_revision": final_revision,
            "live": live,
            "tombstone_capture_id": _capture_id(sk, _tombstone_digest(sk)) if not live else None,
        }

    return {
        "fixture": FIXTURE_ID,
        "totals": {
            "delivery_attempts": len(attempts),
            "distinct_capture_events": 81,
            "source_identities": 72,
            "live_identities": 68,
            "by_provider": {"email": 50, "slack": 12, "tldv": 10},
            "by_kind": {"create": 72, "revision": 5, "tombstone": 4},
            "duplicate_attempts": 10,
        },
        "exact_repeats": list(EXACT_REPEATS),
        "revised": list(REVISED),
        "tombstoned": list(TOMBSTONED),
        "identities": identities,
    }


def write_golden_manifest(path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(build_golden_manifest(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return path


def load_golden_manifest(path: str | Path | None = None) -> dict[str, Any]:
    path = Path(path or Path(__file__).with_name("f_capture_golden.json"))
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":  # pragma: no cover
    p = write_golden_manifest(Path(__file__).with_name("f_capture_golden.json"))
    m = json.loads(p.read_text(encoding="utf-8"))
    print(f"wrote {p}")
    print(json.dumps(m["totals"], indent=2))
