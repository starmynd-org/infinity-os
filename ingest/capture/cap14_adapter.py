"""Binds I01's capture journal to Terminal 08's CAP14 fixture pack.

    cd waves/handoffs/CAP14/negative-fixtures
    CAP14_SUT=capture.cap14_adapter:CaptureAdapter python3 run_fixtures.py

WHY THIS IS STRONGER EVIDENCE THAN MY OWN F-CAPTURE SUITE, and it is worth being precise about
the difference. `capture/fixtures/f_capture.py` derives its golden manifest from an independent
re-reading of the contract's prose, which rules out the tautology of expectations computed by the
implementation. But it is still mine: I wrote both the attempt stream and the expectations.

CAP14's pack is not. `capture-attempts.json` is Terminal 08's 91-attempt stream and
`capture-golden.json` is its hand-written expectation, sourced from
`COMMON-FIXTURES-AND-EVIDENCE.md` and carrying its own note that "these expected values are the
authority; no system under test may compute them". Passing it means the journal agrees with a
second party's reading of the same contract, on a stream it has never seen.

THE ADAPTER DECIDES NOTHING. It translates their attempt vocabulary into `DeliveryAttempt` and
reads the four counts back out of `recovery.coverage_report`, which derives them from committed
rows rather than accumulating them. Their `content_hash` becomes the payload, so two attempts
sharing a hash are byte-identical deliveries and must dedup; a changed hash under a known identity
is a revision. That is the whole mapping, and it is the mapping the counts test.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping

_HERE = Path(__file__).resolve().parent
_INGEST = _HERE.parent
if str(_INGEST) not in sys.path:
    sys.path.insert(0, str(_INGEST))

from capture.contracts import Provenance, RetentionPolicy, SourceRef  # noqa: E402
from capture.journal import CaptureJournal, DeliveryAttempt  # noqa: E402
from capture.raw_custody import AesGcmCipher, RawStore  # noqa: E402
from capture.recovery import (  # noqa: E402
    coverage_report,
    find_dangling_references,
    find_unreadable_references,
)
from capture.contracts import CaptureReceipt  # noqa: E402
from capture.outbox import OutboxAuthorisationError, SourceHygieneOutbox  # noqa: E402
from capture.sqlite_store import SqliteJournalStore  # noqa: E402

#: CAP14's `kind` values map onto providers. The account is constant: the fixture is one operator's
#: sources, and identity is the provider's own id, never the kind.
PROVIDER_FOR = {"email": "email", "slack": "slack", "meeting": "tldv"}

NOT_MINE = {"outcome": "deny", "reason": "not_my_packet: CAP04 owns capture only"}


class CaptureAdapter:
    def __init__(self, world: Mapping[str, Any] | None = None):
        self.world = dict(world or {})

    def capture(self, request: Any) -> dict:
        """Replay CAP14's attempt stream through the real journal and report the four counts."""
        attempts = self._attempts(request)
        workdir = Path(tempfile.mkdtemp(prefix="cap14-capture-"))
        try:
            store = SqliteJournalStore(workdir / "journal.sqlite3")
            custody = RawStore(workdir / "custody", AesGcmCipher(AesGcmCipher.generate_key()))
            self._replay(attempts, CaptureJournal(store, custody))

            report = coverage_report(store)
            dangling = find_dangling_references(store, custody)
            unreadable = find_unreadable_references(store, custody)
            answer = {
                "attempts": len(attempts),
                "distinct_capture_events": report["capture_events"],
                "source_identities": report["source_identities"],
                "still_live": report["live_identities"],
                # The second half of I01's acceptance line: every acknowledged raw digest resolves.
                # TWO numbers, because they are two different failures and the cheap one cannot see
                # the second. `dangling` asks whether the file exists; `unreadable` reads every blob
                # back through custody, which re-verifies the digest and the cipher tag, so it also
                # catches bytes swapped for other bytes. A run can score 91/81/72/68 with perfect
                # counts and a swapped blob, which is the scene T08 offered to write.
                "dangling_raw_digests": len(dangling),
                "unreadable_raw_digests": len(unreadable),
            }
            store.close()
            return answer
        finally:
            shutil.rmtree(workdir, ignore_errors=True)


    @staticmethod
    def _replay(attempts, journal) -> None:
        """One replay path, shared by `capture` and `custody_audit`, so they cannot diverge."""
        provenance = Provenance(connector="cap14", connector_version="1", run_id="cap14")
        for a in attempts:

                kind = a.get("kind") or "email"
                source = SourceRef(
                    PROVIDER_FOR.get(kind, kind), "cap14-account", str(a["identity"])
                )
                deleted = a.get("op") == "tombstone"
                # The content hash IS the content for dedup purposes: identical hash means an
                # identical delivery and must add no event; a new hash under a known identity is a
                # revision. A tombstone carries no content and derives its digest from the identity.
                payload = {"content_hash": a.get("content_hash"), "kind": kind}
                journal.capture(DeliveryAttempt(
                    source=source,
                    payload=payload,
                    occurred_at=f"2026-09-01T00:00:{int(a.get('attempt', 0)) % 60:02d}Z",
                    provenance=provenance,
                    raw_blob=json.dumps(payload, sort_keys=True).encode("utf-8"),
                    raw_media_type="application/json",
                    retention=RetentionPolicy(brains=("company",)),
                    deleted=deleted,
                ))

    def custody_audit(self, request: Mapping[str, Any]) -> dict:
        """CAP14's POS-CUSTODY-01 / NEG-CUSTODY-01: presence is not resolution.

        Runs the stream, optionally corrupts one blob, and reports both custody numbers. The
        corruption is a REPLACEMENT with different valid bytes: the file stays present and
        non-empty, so `dangling_raw_digests` is unmoved and only the read-back number sees it.
        That is the whole point of the scene, and it is the shape my own defect had.

        `unreadable` is a SUPERSET of `dangling`: it reads every referenced blob back, so bytes
        that are gone fail it too. The two are not disjoint and the fixture does not assume they
        are.
        """
        attempts = self._attempts(request)
        workdir = Path(tempfile.mkdtemp(prefix="cap14-custody-"))
        try:
            store = SqliteJournalStore(workdir / "journal.sqlite3")
            custody = RawStore(workdir / "custody", AesGcmCipher(AesGcmCipher.generate_key()))
            self._replay(attempts, CaptureJournal(store, custody))

            corrupt = request.get("corrupt")
            if corrupt:
                # One operation or a list of them. THE BLOB LIST IS RESOLVED ONCE, before any
                # operation runs, so `which` indexes the ORIGINAL ordering. Recomputing it
                # between operations would make a delete shift every later index, and the
                # combined scene would silently corrupt a different blob than it named.
                operations = corrupt if isinstance(corrupt, list) else [corrupt]
                blobs = sorted(
                    p for p in (workdir / "custody").rglob("*")
                    if p.is_file() and not p.name.startswith(".")
                )
                for op in operations:
                    mode = op.get("mode")
                    which = int(op.get("which") or 1)
                    if which > len(blobs):
                        raise ValueError(f"asked to corrupt blob {which} of {len(blobs)}")
                    target = blobs[which - 1]
                    if mode == "replace_bytes":
                        # Different valid bytes. Present, non-empty and wrong: under AES-GCM this
                        # fails the tag, on a plaintext store it fails the digest, and both are
                        # the same refusal as far as this number is concerned.
                        target.write_bytes(b"substituted-after-acknowledgement-" + b"" * 64)
                    elif mode == "delete":
                        target.unlink()
                    else:
                        raise ValueError(f"unknown corruption mode {mode!r}")

            answer = {
                "dangling_raw_digests": len(find_dangling_references(store, custody)),
                "unreadable_raw_digests": len(find_unreadable_references(store, custody)),
            }
            store.close()
            return answer
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    def authorise_hygiene(self, request: Mapping[str, Any]) -> dict:
        """CAP14's POS-ACK-01 / NEG-ACK-03 / NEG-ACK-04, over the real outbox.

        A thin shim: `SourceHygieneOutbox.authorise` does the deciding, and it decides by asking
        the STORE for a committed row. That is the whole property. NEG-ACK-04 is the one worth
        having, and it is Terminal 08's rather than mine: a caller presents a receipt claiming
        durability for a capture that was never committed, and the receipt must not be enough.
        `authorise_receipt` checks the receipt's own claim first and then re-checks the row
        anyway, so a fabricated receipt fails on the row and not on its self-description.

        The fixture's ids are logical. Capture ids are DERIVED from content and cannot be assigned,
        so each `committed_rows` entry is captured for real and its derived id recorded; an id that
        is not in that set is passed through unresolved, which is exactly a fabricated intent.
        """
        workdir = Path(tempfile.mkdtemp(prefix="cap14-hygiene-"))
        try:
            store = SqliteJournalStore(workdir / "journal.sqlite3")
            custody = RawStore(workdir / "custody", AesGcmCipher(AesGcmCipher.generate_key()))
            journal = CaptureJournal(store, custody)
            outbox = SourceHygieneOutbox(store)
            provenance = Provenance(connector="cap14", connector_version="1", run_id="ack")

            real_for: dict[str, str] = {}
            for logical in request.get("committed_rows") or []:
                outcome = journal.capture(DeliveryAttempt(
                    source=SourceRef("email", "cap14-account", str(logical)),
                    payload={"logical": str(logical)},
                    occurred_at="2026-09-01T00:00:00Z",
                    provenance=provenance,
                    raw_blob=json.dumps({"logical": str(logical)}, sort_keys=True).encode("utf-8"),
                    raw_media_type="application/json",
                ))
                real_for[str(logical)] = outcome.receipt.capture_id

            asked = str(request.get("capture_id"))
            resolved = real_for.get(asked, asked)

            try:
                if request.get("receipt_presented"):
                    # A receipt that asserts durability for a capture that may not exist. The
                    # outbox must refuse on the missing row, not on the receipt's own claim.
                    outbox.authorise_receipt(CaptureReceipt(
                        receipt_id="rcpt_presented", capture_id=resolved, source_key="sk_x",
                        journal_seq=1, content_digest="sha256:" + "0" * 64, raw_digest=None,
                        kind="create", revision=1, issued_at="2026-09-01T00:00:00Z", durable=True,
                    ))
                else:
                    outbox.authorise(resolved)
            except OutboxAuthorisationError as exc:
                return {"authorised": False, "reason": str(exc)}
            return {"authorised": True, "reason": None}
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    @staticmethod
    def _attempts(request: Any) -> list[dict]:
        """Accept the fixture's `{attempts_file}` indirection, or a raw list."""
        if isinstance(request, list):
            return request
        if isinstance(request, Mapping):
            if "attempts" in request:
                return list(request["attempts"])
            name = request.get("attempts_file")
            if name:
                base = _find_fixture_dir()
                doc = json.loads((base / name).read_text(encoding="utf-8"))
                return list(doc["attempts"] if isinstance(doc, Mapping) else doc)
        raise ValueError(f"cannot read an attempt stream from {type(request).__name__}")

    # -- calls belonging to other packets ---------------------------------------------------------

    def authorize(self, request):
        return dict(NOT_MINE)

    def approve(self, request):
        return dict(NOT_MINE)

    def execute(self, request):
        return {"outcome": "deny", "reason": NOT_MINE["reason"]}

    def entitlement(self, request):
        return {"paid_access": False, "free_os": False, "customer_data": "preserved",
                "reason": NOT_MINE["reason"]}

    def webhook(self, request):
        return {"outcome": "rejected", "reason": NOT_MINE["reason"]}

    def owner_access(self, request):
        return dict(NOT_MINE)

    def upgrade(self, request):
        return {"version": "unknown", "preserved_all": False, "reason": NOT_MINE["reason"]}

    def export(self, request):
        return dict(NOT_MINE)


def _find_fixture_dir() -> Path:
    """The runner executes from its own directory, so the fixtures are beside it."""
    for candidate in (Path.cwd() / "fixtures", Path.cwd()):
        if (candidate / "capture-attempts.json").exists():
            return candidate
    raise FileNotFoundError("capture-attempts.json is not beside the runner")
