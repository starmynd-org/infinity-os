"""I02-RECONCILE-01: what a reconciliation must say before it is allowed to clear a loss.

Every refusal here exists because the unnamed version of it would let unattended readiness be
regained by an act that decided nothing. The operational scene, one refused payload and three clean
runs that stay unready until a human closes it, lives in
`connectors/coverage/tests/test_coverage_and_recovery.py`; this file is about the contract.
"""

from __future__ import annotations

import pytest

from capture.reconcile import RESOLUTIONS, reconcile
from capture.sqlite_store import SqliteJournalStore


@pytest.fixture
def store(tmp_path):
    s = SqliteJournalStore(tmp_path / "journal.sqlite3")
    yield s
    s.close()


def a_dead_letter(store, dead_letter_id="dl_1"):
    store.dead_letter({
        "dead_letter_id": dead_letter_id,
        "source_key": "sk-1",
        "reason": "payload-refused",
        "detail": "the door refused this payload as unrepresentable",
        "received_at": "2026-09-07T08:00:00Z",
    })
    return dead_letter_id


def test_a_resolution_outside_the_three_is_refused(store):
    """There is deliberately no "ignore": an item being ignored is an unreconciled loss and must
    keep reading as one."""
    dl = a_dead_letter(store)
    with pytest.raises(ValueError, match="unknown resolution"):
        reconcile(store, dl, resolution="ignore", resolved_by="andrew",
                  resolved_at="2026-09-07T09:00:00Z")
    assert store.unreconciled_dead_letters() == 1


@pytest.mark.parametrize("resolved_by", ["", "   "])
def test_an_unattributed_resolution_is_refused(store, resolved_by):
    """A loss cleared by nobody is not cleared. This is the same rule the repo already applies to
    acceptance: it is a human act and the human is named."""
    dl = a_dead_letter(store)
    with pytest.raises(ValueError, match="must name who resolved it"):
        reconcile(store, dl, resolution="recaptured", resolved_by=resolved_by,
                  resolved_at="2026-09-07T09:00:00Z")
    assert store.unreconciled_dead_letters() == 1


def test_a_resolution_without_a_time_is_refused(store):
    dl = a_dead_letter(store)
    with pytest.raises(ValueError, match="when it happened"):
        reconcile(store, dl, resolution="recaptured", resolved_by="andrew", resolved_at="")
    assert store.unreconciled_dead_letters() == 1


def test_accepting_a_loss_must_say_why(store):
    """The other two resolutions describe themselves. This one is a human deciding an item will
    never arrive, and a decision like that should not be recordable in silence."""
    dl = a_dead_letter(store)
    with pytest.raises(ValueError, match="must say why"):
        reconcile(store, dl, resolution="accepted-loss", resolved_by="andrew",
                  resolved_at="2026-09-07T09:00:00Z")

    assert reconcile(store, dl, resolution="accepted-loss", resolved_by="andrew",
                     resolved_at="2026-09-07T09:00:00Z",
                     note="the provider cannot re-serve this message") is True
    assert store.unreconciled_dead_letters() == 0


def test_reconciling_a_loss_that_was_never_recorded_is_refused(store):
    """The one thing a reconciliation must never be able to say: that a loss was handled when no
    loss was ever recorded."""
    with pytest.raises(KeyError, match="no dead letter"):
        reconcile(store, "dl_does_not_exist", resolution="not-an-item", resolved_by="andrew",
                  resolved_at="2026-09-07T09:00:00Z")


def test_the_count_is_per_source_when_asked_and_global_when_not(store):
    a_dead_letter(store, "dl_1")
    store.dead_letter({
        "dead_letter_id": "dl_2", "source_key": "sk-2", "reason": "retry-exhausted",
        "detail": "five attempts", "received_at": "2026-09-07T08:05:00Z",
    })

    assert store.unreconciled_dead_letters() == 2
    assert store.unreconciled_dead_letters("sk-1") == 1

    reconcile(store, "dl_1", resolution="recaptured", resolved_by="andrew",
              resolved_at="2026-09-07T09:00:00Z")
    assert store.unreconciled_dead_letters("sk-1") == 0
    assert store.unreconciled_dead_letters("sk-2") == 1
    assert store.unreconciled_dead_letters() == 1


def test_the_vocabulary_is_four_words_and_only_three_of_them_close():
    """A contract test, so adding a fifth is a deliberate change rather than a value someone
    passed once. The split matters more than the count: `still-unknown` is a real answer that
    closes nothing, and a reader who has looked and does not know has a word for it."""
    from capture.reconcile import CLOSING_RESOLUTIONS

    assert RESOLUTIONS == ("recaptured", "accepted-loss", "not-an-item", "still-unknown")
    assert CLOSING_RESOLUTIONS == ("recaptured", "accepted-loss", "not-an-item")
    assert "still-unknown" not in CLOSING_RESOLUTIONS


def test_still_unknown_records_the_finding_and_closes_nothing(store):
    """CAP14's condition on ruling it in: if it cleared the count it would be a silent close
    wearing an honest label, which is strictly worse than accepted-loss."""
    dl = a_dead_letter(store)
    reconcile(store, dl, resolution="still-unknown", resolved_by="andrew",
              resolved_at="2026-09-07T09:00:00Z", note="looked at the provider, cannot tell yet")

    assert store.unreconciled_dead_letters() == 1
    assert any(r["resolution"] == "still-unknown"
               for r in store.dead_letter_reconciliations(dl))


def test_a_still_unknown_loss_can_be_closed_later_when_the_item_arrives(store):
    """Terminal 04's defect sequence, which the first shape made impossible: the door has to open
    again, or the honest answer becomes a permanent block and everyone learns to say
    accepted-loss instead."""
    dl = a_dead_letter(store)
    reconcile(store, dl, resolution="still-unknown", resolved_by="andrew",
              resolved_at="2026-09-07T09:00:00Z", note="cannot tell yet")
    assert store.unreconciled_dead_letters() == 1

    reconcile(store, dl, resolution="recaptured", resolved_by="andrew",
              resolved_at="2026-09-07T11:00:00Z", note="arrived by the other route")
    assert store.unreconciled_dead_letters() == 0

    history = [r["resolution"] for r in store.dead_letter_reconciliations(dl)]
    assert history == ["still-unknown", "recaptured"], "the earlier finding must stay readable"


def test_recording_that_you_looked_must_say_what_you_looked_at(store):
    dl = a_dead_letter(store)
    with pytest.raises(ValueError, match="what you looked at"):
        reconcile(store, dl, resolution="still-unknown", resolved_by="andrew",
                  resolved_at="2026-09-07T09:00:00Z")
