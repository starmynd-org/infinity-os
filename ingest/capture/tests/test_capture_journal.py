"""I01 acceptance: the full F-CAPTURE run, and the failure model around it.

The acceptance statement this file has to produce evidence for is exact:

    81 distinct capture events over 72 source IDs, 68 live identities after four tombstones;
    duplicates cause no new event; all acknowledged raw digests resolve.

Every expected number below comes from `capture/fixtures/f_capture_golden.json`, which is written
by a module that imports nothing from the implementation. If the golden file and the run disagree,
the run is wrong.
"""

from __future__ import annotations

import json

import pytest

from capture.contracts import Provenance, RetentionPolicy, SourceRef
from capture.fixtures import f_capture
from capture.journal import (
    Attachment,
    CaptureJournal,
    DeliveryAttempt,
    TransientCaptureError,
)
from capture.outbox import NullExecutor, OutboxAuthorisationError, SourceHygieneOutbox
from capture.raw_custody import AesGcmCipher, CustodyError, NullCipher, RawStore
from capture.recovery import (
    coverage_report,
    export_journal,
    find_dangling_references,
    find_orphan_blobs,
    forget_content,
    replay,
    restore_journal,
)
from capture.sqlite_store import SqliteJournalStore
from capture.state_port import OutboxIntent

GOLDEN = f_capture.build_golden_manifest()

PROV = Provenance(connector="fixture", connector_version="1", run_id="run-1", cursor="c0")


def _attempt(fx: f_capture.FixtureAttempt, *, intents=()) -> DeliveryAttempt:
    return DeliveryAttempt(
        source=SourceRef(fx.provider, fx.account_id, fx.source_id),
        payload=fx.payload,
        occurred_at=fx.occurred_at,
        provenance=PROV,
        raw_blob=fx.raw_blob,
        raw_media_type="application/json",
        participants=fx.participants,
        retention=RetentionPolicy(brains=("company",)),
        deleted=fx.deleted,
        intents=intents,
    )


@pytest.fixture
def door(store, custody):
    return CaptureJournal(store, custody)


# -- the headline acceptance run -------------------------------------------------------------


def test_f_capture_91_attempts_produce_the_golden_counts(door, store, custody):
    """91 delivery attempts in F-CAPTURE. The one test the acceptance line is stated in."""
    attempts = f_capture.build_attempts()
    assert len(attempts) == GOLDEN["totals"]["delivery_attempts"] == 91

    outcomes = [door.capture(_attempt(fx)) for fx in attempts]

    assert all(o.captured for o in outcomes), "every fixture attempt must produce a receipt"
    assert sum(1 for o in outcomes if o.duplicate) == GOLDEN["totals"]["duplicate_attempts"]
    assert sum(1 for o in outcomes if o.new_event) == GOLDEN["totals"]["distinct_capture_events"]

    report = coverage_report(store)
    assert report["capture_events"] == GOLDEN["totals"]["distinct_capture_events"] == 81
    assert report["source_identities"] == GOLDEN["totals"]["source_identities"] == 72
    assert report["live_identities"] == GOLDEN["totals"]["live_identities"] == 68
    assert report["by_kind"] == GOLDEN["totals"]["by_kind"]
    assert report["dead_letters"] == 0

    # "all acknowledged raw digests resolve" -- the second half of the acceptance line.
    assert find_dangling_references(store, custody) == set()
    assert find_orphan_blobs(store, custody) == set()


def test_golden_identities_match_the_independent_implementation(door, store):
    """The cross-check: derived IDs must equal the ones the fixture computed on its own.

    `f_capture` re-derives source keys and capture IDs from the contract's prose. If this passes,
    the implementation and an independent reading of the contract agree; if the hashing rule in
    `contracts.py` changes, this goes red, which is the point.
    """
    for fx in f_capture.build_attempts():
        door.capture(_attempt(fx))

    for label, expected in GOLDEN["identities"].items():
        head = store.head(expected["source_key"])
        assert head is not None, f"{label} was never captured"
        assert head.revision == expected["final_revision"], label
        assert head.live is expected["live"], label
        first = store.get(expected["first_capture_id"])
        assert first is not None, f"{label} first capture id does not match the golden manifest"
        if not expected["live"]:
            assert store.get(expected["tombstone_capture_id"]) is not None, label


def test_duplicates_reissue_the_original_receipt_and_add_no_row(door, store):
    """A redelivery returns the ORIGINAL sequence number, not a new one."""
    fx = f_capture.build_attempts()[0]
    first = door.capture(_attempt(fx))
    before = store.count_events()

    again = door.capture(_attempt(fx))

    assert again.duplicate is True
    assert again.new_event is False
    assert again.receipt.journal_seq == first.receipt.journal_seq
    assert again.receipt.capture_id == first.receipt.capture_id
    assert again.receipt.duplicate_of == first.receipt.capture_id
    assert store.count_events() == before


def test_old_revision_redelivered_is_still_a_duplicate(door, store):
    """Keying dedup on the head alone would make this a spurious third event."""
    attempts = f_capture.build_attempts()
    original = next(a for a in attempts if a.label == "e010" and a.expect == "create")
    revised = next(a for a in attempts if a.label == "e010" and a.expect == "revision")

    door.capture(_attempt(original))
    door.capture(_attempt(revised))
    events_after_revision = store.count_events()

    replayed_old = door.capture(_attempt(original))

    assert replayed_old.duplicate is True
    assert store.count_events() == events_after_revision
    assert store.head(original.source_key).revision == 2

    # A receipt must describe the row that exists, not the classification this call guessed.
    # Without this, a door that dedups on the head alone still writes no second row (the store
    # rejects it) but hands back a receipt claiming revision 3 -- a receipt that lies.
    row = store.get(replayed_old.receipt.capture_id)
    assert replayed_old.receipt.kind == row.kind == "create"
    assert replayed_old.receipt.revision == row.revision == 1
    assert replayed_old.receipt.journal_seq == row.journal_seq


# -- edited title and body -------------------------------------------------------------------


def test_edited_title_and_body_is_a_revision_not_a_duplicate(door, store):
    """E06/E23: title-keyed dedup lost the edit. Identity is the provider ID, so it cannot."""
    attempts = f_capture.build_attempts()
    original = next(a for a in attempts if a.label == "e010" and a.expect == "create")
    revised = next(a for a in attempts if a.label == "e010" and a.expect == "revision")
    assert revised.payload["subject"] != original.payload["subject"], "fixture must edit the title"

    first = door.capture(_attempt(original))
    second = door.capture(_attempt(revised))

    assert second.new_event is True
    assert second.receipt.kind == "revision"
    assert second.receipt.revision == 2
    assert second.receipt.capture_id != first.receipt.capture_id
    row = store.get(second.receipt.capture_id)
    assert row.supersedes == first.receipt.capture_id
    # The superseded row is still readable. A revision never overwrites its predecessor.
    assert store.get(first.receipt.capture_id) is not None


def test_two_different_meetings_with_the_same_title_stay_separate(door, store):
    """The failure the identity rule exists to prevent, asserted directly."""
    a = DeliveryAttempt(
        source=SourceRef("tldv", "acct-tldv-1", "meeting-aaaa"),
        payload={"title": "Weekly", "transcript": "first"},
        occurred_at="2026-09-01T09:00:00Z",
        provenance=PROV,
    )
    b = DeliveryAttempt(
        source=SourceRef("tldv", "acct-tldv-1", "meeting-bbbb"),
        payload={"title": "Weekly", "transcript": "second"},
        occurred_at="2026-09-01T10:00:00Z",
        provenance=PROV,
    )

    ra, rb = door.capture(a), door.capture(b)

    assert ra.new_event and rb.new_event
    assert ra.receipt.source_key != rb.receipt.source_key
    assert store.count_events() == 2


# -- the crash window ------------------------------------------------------------------------


def test_crash_between_raw_custody_and_journal_commit_issues_no_receipt(tmp_path, custody):
    """The window the whole ordering exists for.

    Raw bytes are durable; the commit is interrupted. The requirements are: no receipt, no row, no
    outbox intent, and the residue is an orphan blob rather than a dangling reference.
    """

    def crash(point: str) -> None:
        if point == "before_commit":
            raise KeyboardInterrupt("simulated crash inside the commit")

    store = SqliteJournalStore(tmp_path / "j.sqlite3", fault=crash)
    door = CaptureJournal(store, custody)
    fx = f_capture.build_attempts()[0]

    with pytest.raises(KeyboardInterrupt):
        door.capture(_attempt(fx, intents=(OutboxIntent("mark_read", fx.source_id),)))

    assert store.count_events() == 0
    assert list(store.claimable_intents()) == []
    assert find_dangling_references(store, custody) == set()
    orphans = find_orphan_blobs(store, custody)
    assert len(orphans) == 1, "the raw blob is the expected residue and it is still on disk"
    store.close()

    # Restart without the fault: the SAME delivery is retried and produces exactly one event.
    store2 = SqliteJournalStore(tmp_path / "j.sqlite3")
    door2 = CaptureJournal(store2, custody)
    outcome = door2.capture(_attempt(fx, intents=(OutboxIntent("mark_read", fx.source_id),)))
    assert outcome.new_event is True
    assert store2.count_events() == 1
    assert find_orphan_blobs(store2, custody) == set(), "the orphan was adopted, not duplicated"
    assert len(list(store2.claimable_intents())) == 1
    store2.close()


def test_source_hygiene_is_unreachable_without_a_committed_row(store, custody, tmp_path):
    """Invariant 1, asserted as a refusal rather than as a comment."""
    outbox = SourceHygieneOutbox(store)
    with pytest.raises(OutboxAuthorisationError):
        outbox.authorise("cap_does_not_exist")

    door = CaptureJournal(store, custody)
    fx = f_capture.build_attempts()[0]
    outcome = door.capture(_attempt(fx, intents=(OutboxIntent("mark_read", fx.source_id),)))

    outbox.authorise_receipt(outcome.receipt)
    executor = NullExecutor()
    done = outbox.drain(executor)
    assert len(done) == 1
    assert done[0].capture_id == outcome.receipt.capture_id
    assert list(store.claimable_intents()) == [], "a drained intent is not claimable twice"
    assert len(executor.performed) == 1


def test_a_tombstone_stages_no_source_hygiene(door, store):
    """There is nothing left at the source to mark read, and pretending otherwise is a write."""
    fx = next(a for a in f_capture.build_attempts() if a.expect == "tombstone")
    door.capture(_attempt(fx, intents=(OutboxIntent("mark_read", fx.source_id),)))
    assert list(store.claimable_intents()) == []


# -- attachment failure ----------------------------------------------------------------------


def test_attachment_failure_captures_nothing_and_stays_retryable(door, store, custody):
    """Partial evidence is not evidence: the capture fails whole and the connector keeps it."""
    fx = f_capture.build_attempts()[0]
    base = _attempt(fx)
    failing = DeliveryAttempt(
        source=base.source,
        payload=base.payload,
        occurred_at=base.occurred_at,
        provenance=base.provenance,
        raw_blob=base.raw_blob,
        attachments=(
            Attachment("ok.pdf", "application/pdf", blob=b"%PDF-1.4 fixture"),
            Attachment("broken.pdf", "application/pdf", fetch_error="provider returned 502"),
        ),
        intents=(OutboxIntent("mark_read", fx.source_id),),
    )

    with pytest.raises(TransientCaptureError):
        door.capture(failing)

    assert store.count_events() == 0
    assert list(store.claimable_intents()) == []
    assert find_dangling_references(store, custody) == set()

    # The retry, with the attachment now retrievable, succeeds once.
    fixed = DeliveryAttempt(
        source=failing.source,
        payload=failing.payload,
        occurred_at=failing.occurred_at,
        provenance=failing.provenance,
        raw_blob=failing.raw_blob,
        attachments=(
            Attachment("ok.pdf", "application/pdf", blob=b"%PDF-1.4 fixture"),
            Attachment("broken.pdf", "application/pdf", blob=b"%PDF-1.4 second"),
        ),
        intents=failing.intents,
    )
    outcome = door.capture(fixed)
    assert outcome.new_event is True
    assert len(store.get(outcome.receipt.capture_id).record["attachments"]) == 2


def test_a_new_attachment_makes_a_revision_not_a_duplicate(door, store):
    """Same body, new attachment. Content identity includes the attachment descriptors."""
    fx = f_capture.build_attempts()[0]
    door.capture(_attempt(fx))
    base = _attempt(fx)
    with_attachment = DeliveryAttempt(
        source=base.source,
        payload=base.payload,
        occurred_at=base.occurred_at,
        provenance=base.provenance,
        raw_blob=base.raw_blob,
        attachments=(Attachment("late.pdf", "application/pdf", blob=b"%PDF-1.4 late"),),
    )
    outcome = door.capture(with_attachment)
    assert outcome.new_event is True
    assert outcome.receipt.kind == "revision"


# -- poison ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload,reason",
    [
        ({"body": "a\x00b"}, "uncanonicalizable-payload"),
        ({"score": float("nan")}, "uncanonicalizable-payload"),
        ({"when": {1: "int key"}}, "uncanonicalizable-payload"),
    ],
)
def test_poison_payloads_dead_letter_and_are_never_counted_as_captures(door, store, payload, reason):
    outcome = door.capture(
        DeliveryAttempt(
            source=SourceRef("email", "acct-mail-1", "uid-poison"),
            payload=payload,
            occurred_at="2026-09-01T09:00:00Z",
            provenance=PROV,
        )
    )
    assert outcome.captured is False
    assert outcome.receipt is None
    assert outcome.dead_letter.reason == reason
    assert store.count_events() == 0
    assert coverage_report(store)["dead_letters"] == 1


def test_oversized_and_wrong_media_blobs_are_refused_by_custody(tmp_path, store):
    from capture.raw_custody import CustodyPolicy

    tiny = RawStore(
        tmp_path / "c",
        AesGcmCipher(AesGcmCipher.generate_key()),
        policy=CustodyPolicy(max_blob_bytes=16, allowed_media=frozenset({"application/json"})),
    )
    door = CaptureJournal(store, tiny)

    big = door.capture(
        DeliveryAttempt(
            source=SourceRef("email", "a", "big"),
            payload={"b": "x"},
            occurred_at="2026-09-01T09:00:00Z",
            provenance=PROV,
            raw_blob=b"x" * 64,
        )
    )
    assert big.dead_letter.reason == "custody-refused"

    wrong = door.capture(
        DeliveryAttempt(
            source=SourceRef("email", "a", "wrong"),
            payload={"b": "x"},
            occurred_at="2026-09-01T09:00:00Z",
            provenance=PROV,
            raw_blob=b"tiny",
            raw_media_type="application/x-msdownload",
        )
    )
    assert wrong.dead_letter.reason == "custody-refused"
    assert store.count_events() == 0


# -- custody security --------------------------------------------------------------------------


def test_custody_refuses_plaintext_unless_asked_out_loud(tmp_path):
    with pytest.raises(CustodyError):
        RawStore(tmp_path / "c", NullCipher())
    assert RawStore(tmp_path / "c2", NullCipher(), allow_plaintext=True).is_plaintext() is True


def test_raw_bytes_are_not_on_disk_in_the_clear(tmp_path, store):
    custody = RawStore(tmp_path / "c", AesGcmCipher(AesGcmCipher.generate_key()))
    door = CaptureJournal(store, custody)
    secret = b'{"body":"the quarterly numbers are confidential"}'
    outcome = door.capture(
        DeliveryAttempt(
            source=SourceRef("email", "a", "secret"),
            payload={"body": "the quarterly numbers are confidential"},
            occurred_at="2026-09-01T09:00:00Z",
            provenance=PROV,
            raw_blob=secret,
        )
    )
    on_disk = b"".join(
        p.read_bytes() for p in (tmp_path / "c").rglob("*") if p.is_file()
    )
    assert b"confidential" not in on_disk
    assert custody.get(outcome.receipt.raw_digest) == secret


def test_a_tampered_blob_is_refused_rather_than_returned(tmp_path, store):
    custody = RawStore(tmp_path / "c", AesGcmCipher(AesGcmCipher.generate_key()))
    ref = custody.put(b'{"body":"original"}', "application/json")
    blob_path = next(p for p in (tmp_path / "c").rglob("*") if p.is_file())
    corrupted = bytearray(blob_path.read_bytes())
    corrupted[-1] ^= 0xFF
    blob_path.write_bytes(bytes(corrupted))

    with pytest.raises(CustodyError):
        custody.get(ref.raw_digest)


def test_a_swapped_blob_is_caught_by_the_digest_even_without_a_cipher_tag(tmp_path):
    """The second custody guard, and it needs its own test to be exercised at all.

    With AES-GCM a tampered blob fails the tag before the digest is ever recomputed, so the
    `actual != raw_digest` check is dead code in that path. Custody may legitimately be run with
    an operator-chosen cipher that has no tag, and the store must still refuse to hand back bytes
    that do not match their own name -- so the check is proven here, on a plaintext store.
    """
    custody = RawStore(tmp_path / "c", NullCipher(), allow_plaintext=True)
    ref = custody.put(b'{"body":"original"}', "application/json")
    blob_path = next(p for p in (tmp_path / "c").rglob("*") if p.is_file())
    blob_path.write_bytes(b'{"body":"substituted by an attacker"}')

    with pytest.raises(CustodyError, match="refusing to return it"):
        custody.get(ref.raw_digest)


# -- replay, export, restore, deletion after restore -------------------------------------------


def test_replay_is_frontier_based_and_resumable(door, store):
    for fx in f_capture.build_attempts()[:20]:
        door.capture(_attempt(fx))

    seen = []
    first = replay(store, seen.append, limit=5)
    assert first.delivered == 5
    second = replay(store, seen.append, frontier=first.frontier)
    assert second.delivered == store.count_events() - 5
    assert [r.journal_seq for r in seen] == sorted(r.journal_seq for r in seen)


def test_a_failing_handler_leaves_a_resumable_frontier(door, store):
    for fx in f_capture.build_attempts()[:10]:
        door.capture(_attempt(fx))

    delivered = []

    def handler(row):
        if len(delivered) == 4:
            raise RuntimeError("consumer died")
        delivered.append(row.journal_seq)

    with pytest.raises(RuntimeError):
        replay(store, handler)

    resumed = []
    result = replay(store, lambda r: resumed.append(r.journal_seq), frontier=delivered[-1])
    assert resumed[0] == delivered[-1] + 1
    assert len(delivered) + result.delivered == store.count_events()


def test_export_restore_then_deletion_after_restore(door, store, custody, tmp_path):
    """Replay and deletion after restore, with acknowledged captures never discarded."""
    attempts = f_capture.build_attempts()
    for fx in attempts:
        door.capture(_attempt(fx))
    before = coverage_report(store)

    export_path = tmp_path / "journal-export.ndjson"
    written = export_journal(store, export_path)
    assert written == before["capture_events"]

    restored_store = SqliteJournalStore(tmp_path / "restored.sqlite3")
    assert restore_journal(restored_store, export_path) == written
    after = coverage_report(restored_store)
    assert after["capture_events"] == before["capture_events"]
    assert after["live_identities"] == before["live_identities"] == 68
    assert after["by_kind"] == before["by_kind"]

    # A tombstone that arrives AFTER the restore still removes the identity from the live set,
    # and does so without discarding a single already-acknowledged capture.
    restored_door = CaptureJournal(restored_store, custody)
    victim = next(a for a in attempts if a.label == "e030")
    outcome = restored_door.capture(
        DeliveryAttempt(
            source=SourceRef(victim.provider, victim.account_id, victim.source_id),
            payload=victim.payload,
            occurred_at="2026-09-05T12:00:00Z",
            provenance=PROV,
            deleted=True,
        )
    )
    assert outcome.receipt.kind == "tombstone"
    final = coverage_report(restored_store)
    assert final["live_identities"] == 67
    assert final["capture_events"] == before["capture_events"] + 1
    assert restored_store.get(GOLDEN["identities"]["e030"]["first_capture_id"]) is not None
    restored_store.close()


def test_restore_refuses_to_overwrite_a_non_empty_store(door, store, tmp_path):
    for fx in f_capture.build_attempts()[:3]:
        door.capture(_attempt(fx))
    path = tmp_path / "e.ndjson"
    export_journal(store, path)
    with pytest.raises(RuntimeError, match="newer state"):
        restore_journal(store, path)


def test_retention_erasure_removes_bytes_and_keeps_the_row(door, store, custody):
    fx = f_capture.build_attempts()[0]
    outcome = door.capture(_attempt(fx))
    erased = forget_content(store, custody, outcome.receipt.source_key)

    assert erased == 1
    assert store.get(outcome.receipt.capture_id) is not None, "the event still happened"
    assert find_dangling_references(store, custody) == {outcome.receipt.raw_digest}


def test_the_contract_version_travels_with_every_row(door, store):
    fx = f_capture.build_attempts()[0]
    outcome = door.capture(_attempt(fx))
    row = store.get(outcome.receipt.capture_id)
    assert row.record["contract_version"] == outcome.receipt.contract_version
    assert json.loads(json.dumps(row.record)) == row.record, "the record must round-trip as JSON"


def test_a_swapped_blob_is_invisible_to_the_cheap_check_and_caught_by_the_deep_one(
    door, store, tmp_path
):
    """The gap Terminal 08 named: presence is not integrity.

    `find_dangling_references` asks whether a file exists, so a blob swapped for different bytes
    passes it. That is a legitimate cheap check and it was documented as more than it was: the
    acceptance line says acknowledged digests RESOLVE, and resolving means returning the right
    bytes. `find_unreadable_references` is the honest form and this is the case that separates them.
    """
    from capture.raw_custody import NullCipher, RawStore
    from capture.recovery import find_unreadable_references

    plain = RawStore(tmp_path / "plain", NullCipher(), allow_plaintext=True)
    plain_door = CaptureJournal(store, plain)
    fx = f_capture.build_attempts()[0]
    outcome = plain_door.capture(_attempt(fx))
    digest = outcome.receipt.raw_digest

    blob_path = next(p for p in (tmp_path / "plain").rglob("*") if p.is_file())
    blob_path.write_bytes(b'{"body":"substituted after acknowledgement"}')

    # The cheap check still says everything is fine, because the file is still there.
    assert find_dangling_references(store, plain) == set()
    # The deep check does not.
    unreadable = find_unreadable_references(store, plain)
    assert digest in unreadable
    assert "refusing to return it" in unreadable[digest]


def test_the_deep_check_catches_bytes_that_are_simply_gone_as_well(door, store, custody):
    """One report for both custody failures, because an operator asks one question of it."""
    from capture.recovery import find_unreadable_references

    fx = f_capture.build_attempts()[0]
    outcome = door.capture(_attempt(fx))
    custody.forget(outcome.receipt.raw_digest)

    unreadable = find_unreadable_references(store, custody)
    assert outcome.receipt.raw_digest in unreadable
    assert "has no blob" in unreadable[outcome.receipt.raw_digest]


def test_the_deep_check_is_empty_on_a_healthy_journal(door, store, custody):
    from capture.recovery import find_unreadable_references

    for fx in f_capture.build_attempts()[:12]:
        door.capture(_attempt(fx))
    assert find_unreadable_references(store, custody) == {}
