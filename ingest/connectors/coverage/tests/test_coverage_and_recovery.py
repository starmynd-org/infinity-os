"""I02 acceptance: bounded coverage that is observable and recoverable.

The acceptance line: every eligible fixture item is eventually captured or explicitly
dead-lettered; the oldest backlog progresses; quiet and failed runs differ.

The sink here is a real `CaptureJournal` from I01, wrapped by a small adapter -- so these tests
also prove the two packets compose: a redelivered item comes back as `duplicate` from the door's
own idempotency rather than from anything the connector remembers.
"""

from __future__ import annotations

import pytest

from capture.contracts import Provenance, RetentionPolicy, SourceRef
from capture.journal import CaptureJournal, DeliveryAttempt
from capture.reconcile import RESOLUTIONS, reconcile
from capture.state_port import OutboxIntent
from connectors.coverage.cursors import Cursor, CursorError
from connectors.coverage.fixtures.providers import (
    FakeImapSource,
    FakeMeetingSource,
    FakeSlackSource,
    FaultPlan,
    email_items,
    meeting_items,
    slack_items,
)
from connectors.coverage.manifests import SourceManifest
from connectors.coverage.outcomes import RunStatus
from connectors.coverage.pagination import ProviderItem
from connectors.coverage.retry import AttemptLedger, RetryPolicy, RunBudget
from connectors.coverage.sweep import (
    PermanentPayloadError,
    TransientSinkError,
    sweep,
)

PROV = Provenance(connector="fixture-imap", connector_version="1", run_id="run-1")


class InMemoryDeadLetterLog:
    """A durable-enough log for the suite: it outlives a run, which is the property under test.

    The point of CAP14-REV-038 is not that the store must be Postgres, it is that the record must
    outlive the RunOutcome. A dict that survives across sweeps demonstrates exactly that, and the
    real implementation is `SqliteJournalStore.dead_letter` behind the same two calls.
    """

    def __init__(self):
        self.rows: dict[str, dict] = {}
        self.reconciled: set[str] = set()
        self.resolutions: dict[str, dict] = {}

    def record(self, entry):
        self.rows[entry["dead_letter_id"]] = dict(entry)
        return entry["dead_letter_id"]

    def unreconciled(self, source_ref):
        return sum(1 for k, r in self.rows.items()
                   if r.get("source_ref") == source_ref and k not in self.reconciled)

    # I02-RECONCILE-01. The two verbs `capture.reconcile` needs, so the suite can take the way
    # back through the sanctioned path instead of reaching into this double's internals.
    def reconcile_dead_letter(self, entry):
        if entry["dead_letter_id"] not in self.rows:
            raise KeyError("no dead letter " + repr(entry["dead_letter_id"]) + " to reconcile")
        if entry["dead_letter_id"] in self.reconciled:
            return False
        self.reconciled.add(entry["dead_letter_id"])
        self.resolutions[entry["dead_letter_id"]] = dict(entry)
        return True

    def unreconciled_dead_letters(self, source_key=None):
        return sum(1 for k in self.rows if k not in self.reconciled)

MANIFEST = SourceManifest(
    source_ref="email/acct-mail-1",
    provider="email",
    account_id="acct-mail-1",
    include=("INBOX",),
    exclude=("Personal",),
    hygiene_actions=("mark_read",),
    read_only=False,
    brains=("company",),
)


class JournalSink:
    """Adapts the capture door to the connector's `CaptureSink` shape.

    This adapter is the ONLY place the two layers meet, which is the point: in production the
    same adapter speaks HTTP to `intake-service` instead, and the connector does not change.
    """

    def __init__(self, journal: CaptureJournal, fail_ids: dict[str, str] | None = None):
        self.journal = journal
        self.fail_ids = dict(fail_ids or {})
        self.seen: list[str] = []

    def __call__(self, item, manifest, hygiene):
        self.seen.append(item.source_id)
        mode = self.fail_ids.get(item.source_id)
        if mode == "transient":
            raise TransientSinkError("the door returned 503")
        if mode == "permanent":
            raise PermanentPayloadError("the door refused this payload as unrepresentable")
        outcome = self.journal.capture(
            DeliveryAttempt(
                source=SourceRef(manifest.provider, manifest.account_id, item.source_id),
                payload=item.payload,
                occurred_at=item.ordering_key,
                provenance=PROV,
                retention=RetentionPolicy(brains=manifest.brains),
                deleted=item.deleted,
                intents=tuple(OutboxIntent(h.action, h.target) for h in hygiene),
            )
        )
        if outcome.dead_letter is not None:
            raise PermanentPayloadError(outcome.dead_letter.reason)
        return "duplicate" if outcome.duplicate else "captured"


@pytest.fixture
def sink(store, custody):
    return JournalSink(CaptureJournal(store, custody))


def run(port, sink, cursor, **kw):
    return sweep(port=port, manifest=kw.pop("manifest", MANIFEST), cursor=cursor, sink=sink, **kw)


# -- identical timestamps ----------------------------------------------------------------------


def test_identical_timestamps_are_all_captured_exactly_once(sink, store):
    """Twelve messages share one second. A cursor ordered on time alone skips or loops."""
    port = FakeImapSource(email_items(12, identical_timestamp_block=12))
    cursor = Cursor.initial(MANIFEST.source_ref, "e1")

    first = run(port, sink, cursor, budget=RunBudget(max_items=5, max_pages=5), page_size=5)
    assert first.outcome.captured == 5

    second = run(port, sink, first.cursor, budget=RunBudget(max_items=50, max_pages=10))
    assert second.outcome.captured == 7

    third = run(port, sink, second.cursor)
    assert third.outcome.status is RunStatus.QUIET
    assert store.count_events() == 12
    assert len(set(sink.seen)) == 12, "no item was fetched twice across the three runs"


# -- backlog larger than the run limit ----------------------------------------------------------


def test_backlog_larger_than_the_run_limit_drains_oldest_first(sink, store):
    """The acceptance property: the oldest backlog progresses, run after run."""
    port = FakeImapSource(email_items(53))
    cursor = Cursor.initial(MANIFEST.source_ref, "e1")
    budget = RunBudget(max_items=10, max_pages=2)

    positions, runs = [], 0
    while runs < 12:
        result = run(port, sink, cursor, budget=budget, page_size=5)
        runs += 1
        cursor = result.cursor
        positions.append(cursor.high_water)
        if result.outcome.status is RunStatus.QUIET:
            break

    assert store.count_events() == 53
    assert positions == sorted(positions), "the frontier never went backwards"
    assert len(set(positions)) > 1, "the frontier actually moved"
    # The FIRST run must have captured the OLDEST items, not the newest.
    assert sink.seen[0] == "uid-00000"


def test_a_partial_run_does_not_claim_coverage(sink):
    port = FakeImapSource(email_items(30))
    result = run(port, sink, Cursor.initial(MANIFEST.source_ref, "e1"),
                 budget=RunBudget(max_items=10, max_pages=2), page_size=5)
    assert result.outcome.status is RunStatus.PARTIAL
    assert result.outcome.coverage_known is False
    assert result.outcome.unattended_ready is False


# -- UIDVALIDITY reset ---------------------------------------------------------------------------


def test_uidvalidity_reset_rescans_without_duplicating_events(sink, store):
    """The provider reissues its ids. Everything is re-fetched; nothing is captured twice."""
    port = FakeImapSource(email_items(8))
    first = run(port, sink, Cursor.initial(MANIFEST.source_ref, "e1"))
    assert first.outcome.captured == 8
    assert store.count_events() == 8

    port.set_epoch("e2")
    second = run(port, sink, first.cursor)

    assert second.outcome.status is RunStatus.RESET
    assert second.outcome.epoch_reset is not None
    assert second.outcome.cursor_after["epoch"] == "e2"
    assert second.outcome.duplicates == 8, "every item was re-fetched"
    assert second.outcome.captured == 0
    assert store.count_events() == 8, "and not one of them became a second event"


def test_an_epoch_change_is_never_silent(sink):
    port = FakeImapSource(email_items(3))
    first = run(port, sink, Cursor.initial(MANIFEST.source_ref, "e1"))
    port.set_epoch("e2")
    second = run(port, sink, first.cursor)
    assert any("epoch changed" in n for n in second.outcome.notes)
    assert second.outcome.summary()["epoch_reset"] is not None


# -- semantically corrupt cursor state ------------------------------------------------------------


@pytest.mark.parametrize(
    "blob,fragment",
    [
        ("not json at all", "not valid JSON"),
        ('{"v": 99, "source_ref": "email/acct-mail-1", "epoch": "e1"}', "unknown version"),
        ('{"v": 1, "source_ref": "slack/other", "epoch": "e1"}', "another source"),
        ('{"v": 1, "source_ref": "email/acct-mail-1"}', "no epoch"),
        (
            '{"v": 1, "source_ref": "email/acct-mail-1", "epoch": "e1", '
            '"high_water": "2026-09-01T08:00:00Z"}',
            "half a position",
        ),
    ],
)
def test_a_corrupt_cursor_is_refused_rather_than_guessed(blob, fragment):
    with pytest.raises(CursorError, match=fragment):
        Cursor.loads(blob, source_ref=MANIFEST.source_ref)


def test_recovery_from_a_corrupt_cursor_rescans_and_captures_everything(sink, store):
    """The operator-visible recovery: refuse the cursor, start clean, dedup absorbs the rescan."""
    port = FakeImapSource(email_items(6))
    good = run(port, sink, Cursor.initial(MANIFEST.source_ref, "e1")).cursor
    assert store.count_events() == 6

    try:
        Cursor.loads("{bad", source_ref=MANIFEST.source_ref)
    except CursorError:
        recovered = Cursor.initial(MANIFEST.source_ref, port.epoch())

    result = run(port, sink, recovered)
    assert result.outcome.duplicates == 6
    assert store.count_events() == 6
    assert result.cursor.high_water == good.high_water


def test_a_cursor_round_trips_through_storage(sink):
    port = FakeImapSource(email_items(4))
    result = run(port, sink, Cursor.initial(MANIFEST.source_ref, "e1"))
    restored = Cursor.loads(result.cursor.dumps(), source_ref=MANIFEST.source_ref)
    assert restored == result.cursor


# -- rate limit, expired auth, quiet source --------------------------------------------------------


def test_rate_limit_ends_the_run_partially_and_preserves_the_cursor(sink, store):
    port = FakeImapSource(email_items(30), faults=FaultPlan(rate_limit_on={2}))
    result = run(port, sink, Cursor.initial(MANIFEST.source_ref, "e1"), page_size=5)

    assert result.outcome.status is RunStatus.FAILED
    assert result.outcome.error_kind == "rate-limited"
    assert result.outcome.retry_after_s == 30.0
    assert result.outcome.coverage_known is False
    # Work done before the limit is kept, and the cursor moved over it.
    assert result.outcome.captured == 5
    assert result.cursor.high_water is not None

    port.faults = FaultPlan()
    resumed = run(port, sink, result.cursor, budget=RunBudget(max_items=100, max_pages=20))
    assert store.count_events() == 30
    assert resumed.outcome.duplicates == 0, "the resume did not re-fetch what was already settled"


def test_expired_auth_stops_the_run_and_is_not_a_quiet_source(sink):
    port = FakeImapSource(email_items(10), faults=FaultPlan(auth_expired_on_epoch=True))
    result = run(port, sink, Cursor.initial(MANIFEST.source_ref, "e1"))

    assert result.outcome.status is RunStatus.FAILED
    assert result.outcome.error_kind == "auth-expired"
    assert result.outcome.captured == 0
    assert result.outcome.coverage_known is False
    assert result.outcome.cursor_after == result.outcome.cursor_before, "cursor untouched"


def test_a_quiet_source_and_a_failed_source_are_different(sink):
    """E07/E08/E12 in one assertion. Both captured zero; only one of them is healthy."""
    quiet_port = FakeImapSource([])
    quiet = run(quiet_port, sink, Cursor.initial(MANIFEST.source_ref, "e1"))

    broken_port = FakeImapSource(email_items(10), faults=FaultPlan(auth_expired_on_epoch=True))
    broken = run(broken_port, sink, Cursor.initial(MANIFEST.source_ref, "e1"))

    assert quiet.outcome.captured == broken.outcome.captured == 0
    assert quiet.outcome.status is RunStatus.QUIET
    assert broken.outcome.status is RunStatus.FAILED
    assert quiet.outcome.coverage_known is True
    assert broken.outcome.coverage_known is False
    assert broken.outcome.unattended_ready is False
    assert quiet.outcome.summary() != broken.outcome.summary()

    # Since CAP14-REV-038, neither is unattended-ready with NO durable dead-letter log: a run that
    # cannot ask whether anything was lost earlier cannot claim readiness, and cannot-say is read
    # as no here as it is everywhere else in this lane. The quiet-versus-failed distinction this
    # test exists for is carried by `coverage_known` and `status`, which is where it belongs.
    assert quiet.outcome.unattended_ready is False
    assert quiet.outcome.unreconciled_dead_letters is None

    # With a log and nothing unreconciled, the quiet run IS ready. That is the positive control,
    # so the new condition cannot be satisfied by refusing everything.
    log = InMemoryDeadLetterLog()
    ready = run(FakeImapSource([]), sink, Cursor.initial(MANIFEST.source_ref, "e1"),
                dead_letter_log=log)
    assert ready.outcome.status is RunStatus.QUIET
    assert ready.outcome.unreconciled_dead_letters == 0
    assert ready.outcome.unattended_ready is True


# -- retry to dead letter ---------------------------------------------------------------------------


def test_a_repeatedly_failing_item_is_eventually_dead_lettered_not_retried_forever(sink, store):
    """The acceptance line's "or explicitly dead-lettered". There is no third state."""
    port = FakeImapSource(email_items(4))
    sink.fail_ids = {"uid-00001": "transient"}
    ledger = AttemptLedger()
    policy = RetryPolicy(max_attempts=3)
    cursor = Cursor.initial(MANIFEST.source_ref, "e1")

    statuses = []
    for _ in range(4):
        result = sweep(port=port, manifest=MANIFEST, cursor=cursor, sink=sink,
                       policy=policy, ledger=ledger)
        cursor, ledger = result.cursor, result.ledger
        statuses.append(result.outcome.status)
        if result.outcome.status is RunStatus.QUIET:
            break

    assert RunStatus.PARTIAL in statuses
    assert statuses[-1] is RunStatus.QUIET, "the source became quiet once the item settled"
    assert ledger.pending() == {}, "no item is still owed a retry"
    assert store.count_events() == 3, "the three good items captured; the poison one did not"


def test_a_deferred_item_holds_the_frontier_behind_it(sink):
    """The item after a deferred one must not be stepped over."""
    port = FakeImapSource(email_items(4))
    sink.fail_ids = {"uid-00001": "transient"}
    result = run(port, sink, Cursor.initial(MANIFEST.source_ref, "e1"))

    assert result.outcome.deferred == 1
    # uid-00000 is settled, so the frontier sits exactly there and NOT past uid-00002/3.
    assert result.cursor.high_water == "2026-09-01T08:00:00Z"
    assert result.cursor.tiebreak == "uid-00000"


def test_a_permanently_refused_payload_dead_letters_on_the_first_attempt(sink):
    port = FakeImapSource(email_items(3))
    sink.fail_ids = {"uid-00001": "permanent"}
    result = run(port, sink, Cursor.initial(MANIFEST.source_ref, "e1"))

    assert result.outcome.dead_lettered == 1
    assert result.outcome.deferred == 0
    assert result.outcome.status is RunStatus.COMPLETE
    assert result.cursor.tiebreak == "uid-00002", "a dead letter is settled; the frontier passes"


# -- pagination and thread edits ---------------------------------------------------------------------


def test_an_unsorted_over_serving_provider_is_still_paged_correctly(store, custody):
    """Slack returns pages reversed and over-serves its limit. The pager sorts and bounds anyway."""
    sink = JournalSink(CaptureJournal(store, custody))
    manifest = SourceManifest(
        source_ref="slack/acct-slack-1", provider="slack", account_id="acct-slack-1",
        include=("C0001",), brains=("company",),
    )
    port = FakeSlackSource(slack_items(20))
    cursor = Cursor.initial(manifest.source_ref, "e1")

    seen_order = []
    for _ in range(6):
        result = sweep(port=port, manifest=manifest, cursor=cursor, sink=sink,
                       budget=RunBudget(max_items=4, max_pages=1), page_size=4)
        cursor = result.cursor
        seen_order.extend(d.source_id for d in result.outcome.items)
        if result.outcome.status is RunStatus.QUIET:
            break

    assert store.count_events() == 20
    assert seen_order == sorted(seen_order), "items were processed in total order despite the provider"
    assert len(seen_order) == len(set(seen_order)), "nothing was processed twice"


def test_a_provider_that_cannot_page_from_a_position_is_not_processed_twice(sink, store):
    """Some APIs only serve a recent window, so consecutive pages OVERLAP.

    Without an in-run frontier the pager re-yields everything on every page: the door absorbs it
    (dedup), but the connector burns quota and stages the same hygiene action repeatedly, which
    on a real mailbox is a visible double "mark read".
    """
    port = FakeImapSource(email_items(9), ignore_after=True)
    result = run(port, sink, Cursor.initial(MANIFEST.source_ref, "e1"),
                 budget=RunBudget(max_items=20, max_pages=8), page_size=3,
                 hygiene=("mark_read",))

    processed = [d.source_id for d in result.outcome.items]
    assert processed == sorted(processed)
    assert len(processed) == len(set(processed)) == 9, "each item processed exactly once per run"
    assert result.outcome.duplicates == 0
    assert len(list(store.claimable_intents())) == 9, "one hygiene action per item, not per page"


def test_an_edited_message_becomes_a_revision_on_the_next_run(store, custody):
    """A thread edit changes content under a stable id. That is a revision, not a duplicate."""
    sink = JournalSink(CaptureJournal(store, custody))
    port = FakeImapSource(email_items(3))
    first = run(port, sink, Cursor.initial(MANIFEST.source_ref, "e1"))
    assert first.outcome.captured == 3

    edited = ProviderItem(
        source_id="uid-00001",
        ordering_key="2026-09-02T09:00:00Z",  # the edit moves it forward in the window
        payload={"container": "INBOX", "subject": "Message 001 (edited)",
                 "from": "person001@example.invalid", "body": "Rewritten body."},
    )
    port.replace("uid-00001", edited)

    second = run(port, sink, first.cursor)
    assert second.outcome.captured == 1
    assert store.count_events() == 4
    assert store.head(SourceRef("email", "acct-mail-1", "uid-00001").source_key).revision == 2


def test_a_late_transcript_is_a_revision_of_the_same_meeting(store, custody):
    sink = JournalSink(CaptureJournal(store, custody))
    manifest = SourceManifest(source_ref="tldv/acct-tldv-1", provider="tldv",
                             account_id="acct-tldv-1", include=("recorded",), brains=("company",))
    port = FakeMeetingSource(meeting_items(2))
    first = sweep(port=port, manifest=manifest, cursor=Cursor.initial(manifest.source_ref, "e1"),
                  sink=sink)
    assert first.outcome.captured == 2

    port.replace("meeting-0000", ProviderItem(
        source_id="meeting-0000", ordering_key="2026-09-02T10:00:00Z",
        payload={"container": "recorded", "title": "Weekly", "transcript": "It arrived later."},
    ))
    second = sweep(port=port, manifest=manifest, cursor=first.cursor, sink=sink)

    assert second.outcome.captured == 1
    assert store.head(SourceRef("tldv", "acct-tldv-1", "meeting-0000").source_key).revision == 2


# -- the authorised boundary -------------------------------------------------------------------------


def test_items_outside_the_manifest_are_recorded_not_silently_dropped(sink, store):
    port = FakeImapSource(
        email_items(2)
        + [ProviderItem("uid-90000", "2026-09-01T23:00:00Z",
                        {"container": "Personal", "subject": "private"})]
    )
    result = run(port, sink, Cursor.initial(MANIFEST.source_ref, "e1"))

    assert result.outcome.captured == 2
    assert result.outcome.dead_lettered == 1
    excluded = next(i for i in result.outcome.items if i.source_id == "uid-90000")
    assert "outside the authorised source manifest" in excluded.detail
    assert store.count_events() == 2


def test_source_hygiene_is_staged_only_when_the_manifest_permits_it(store, custody):
    journal = CaptureJournal(store, custody)
    sink = JournalSink(journal)
    port = FakeImapSource(email_items(2))
    run(port, sink, Cursor.initial(MANIFEST.source_ref, "e1"), hygiene=("mark_read", "archive"))

    staged = list(store.claimable_intents())
    assert {i["action"] for i in staged} == {"mark_read"}, "archive is not in the manifest"
    assert len(staged) == 2


def test_a_read_only_manifest_stages_no_hygiene_at_all(store, custody):
    journal = CaptureJournal(store, custody)
    sink = JournalSink(journal)
    read_only = SourceManifest(
        source_ref="email/ro", provider="email", account_id="acct-mail-1",
        include=("INBOX",), hygiene_actions=("mark_read",), read_only=True,
    )
    port = FakeImapSource(email_items(2))
    sweep(port=port, manifest=read_only, cursor=Cursor.initial(read_only.source_ref, "e1"),
          sink=sink, hygiene=("mark_read",))

    assert list(store.claimable_intents()) == []


def test_the_setup_checklist_states_what_is_being_authorised():
    checklist = MANIFEST.setup_checklist()
    items = {row["item"]: row["value"] for row in checklist}
    assert items["What is never read"] == "Personal"
    assert items["Changes at the source"] == "mark_read"
    assert items["Which Brains may see this as context"] == "company"
    assert items["History"] == "new items only from connection time"


# -- I02-DURABILITY-01: a lost item must leave a record that outlives the run -----------------------


def test_retry_exhaustion_writes_a_durable_dead_letter_naming_the_lost_item(sink, store):
    """CAP14-REV-038. Fails against 591e95d, where the disposition was in-memory only.

    Four items, one failing transiently until its retries are exhausted. The run that gives up must
    record the in-memory disposition AND a durable row naming the item, its source and the reason.
    """
    log = InMemoryDeadLetterLog()
    port = FakeImapSource(email_items(4))
    sink.fail_ids = {"uid-00001": "transient"}
    ledger, cursor = AttemptLedger(), Cursor.initial(MANIFEST.source_ref, "e1")
    policy = RetryPolicy(max_attempts=3)

    exhausting = None
    for _ in range(4):
        result = sweep(port=port, manifest=MANIFEST, cursor=cursor, sink=sink, policy=policy,
                       ledger=ledger, dead_letter_log=log)
        cursor, ledger = result.cursor, result.ledger
        if any(d.disposition == "dead-lettered" for d in result.outcome.items):
            exhausting = result
            break

    assert exhausting is not None, "the item must eventually leave the retry ledger"
    dead = [d for d in exhausting.outcome.items if d.disposition == "dead-lettered"]
    assert [d.source_id for d in dead] == ["uid-00001"]

    # The durable half, which is the whole finding.
    rows = [r for r in log.rows.values() if r["source_key"] == "uid-00001"]
    assert len(rows) == 1, "the lost item must leave exactly one durable row"
    row = rows[0]
    assert row["source_ref"] == MANIFEST.source_ref
    assert row["reason"] == "retry-exhausted"
    assert "gave up after 3 attempts" in row["detail"]
    assert exhausting.outcome.durable_accounting is True

    # Three of four captured, and the fourth is accounted for rather than absent.
    assert store.count_events() == 3


def test_a_later_quiet_run_cannot_claim_readiness_over_an_unreconciled_loss(sink, store):
    """The cross-run half, and the reason the in-memory disposition was not enough.

    A quiet run's own counters are clean: it captured nothing because there was nothing to capture.
    Its `dead_lettered` count is zero, and CAP14 explicitly warned against reading that as proof no
    item was ever lost. Readiness now consults the DURABLE log, so the hole is still visible three
    runs later.
    """
    log = InMemoryDeadLetterLog()
    port = FakeImapSource(email_items(4))
    sink.fail_ids = {"uid-00001": "transient"}
    ledger, cursor = AttemptLedger(), Cursor.initial(MANIFEST.source_ref, "e1")
    policy = RetryPolicy(max_attempts=3)
    for _ in range(5):
        result = sweep(port=port, manifest=MANIFEST, cursor=cursor, sink=sink, policy=policy,
                       ledger=ledger, dead_letter_log=log)
        cursor, ledger = result.cursor, result.ledger

    quiet = sweep(port=port, manifest=MANIFEST, cursor=cursor, sink=sink, policy=policy,
                  ledger=ledger, dead_letter_log=log)

    assert quiet.outcome.status is RunStatus.QUIET
    assert quiet.outcome.dead_lettered == 0, "this run lost nothing; that is not the question"
    assert quiet.outcome.unreconciled_dead_letters == 1
    assert quiet.outcome.unattended_ready is False, "a clean run over an open hole is not ready"

    # Reconciling the loss is what restores readiness, and it is a human act elsewhere. This used
    # to reach into the double's internals because no verb existed; CAP14-REV-041 named that gap
    # and the sanctioned path is now the one the suite takes.
    reconcile(log, next(iter(log.rows)), resolution="recaptured",
              resolved_by="andrew", resolved_at="2026-09-07T09:00:00Z")
    after = sweep(port=port, manifest=MANIFEST, cursor=cursor, sink=sink, policy=policy,
                  ledger=ledger, dead_letter_log=log)
    assert after.outcome.unreconciled_dead_letters == 0
    assert after.outcome.unattended_ready is True


def test_without_a_durable_log_the_run_refuses_to_claim_durable_accounting(sink):
    """No log configured is not the same as nothing lost, and must not read as it."""
    port = FakeImapSource(email_items(2))
    sink.fail_ids = {"uid-00001": "permanent"}
    result = run(port, sink, Cursor.initial(MANIFEST.source_ref, "e1"))

    assert result.outcome.dead_lettered == 1
    assert result.outcome.durable_accounting is False
    assert result.outcome.unattended_ready is False
    assert result.outcome.summary()["durable_accounting"] is False


def test_a_clean_run_with_a_log_stays_unattended_ready(sink, store):
    """The control: nothing lost, nothing unreconciled, readiness intact. A repair that made
    readiness unreachable would pass every negative above and be useless."""
    log = InMemoryDeadLetterLog()
    port = FakeImapSource(email_items(4))
    result = run(port, sink, Cursor.initial(MANIFEST.source_ref, "e1"), dead_letter_log=log)

    assert result.outcome.status is RunStatus.COMPLETE
    assert result.outcome.dead_lettered == 0
    assert log.rows == {}
    assert result.outcome.durable_accounting is True
    assert result.outcome.unreconciled_dead_letters == 0
    assert result.outcome.unattended_ready is True
    assert store.count_events() == 4


# -- I02-RECONCILE-01: a correct gate must not become a wall ---------------------------------------
#
# CAP14-REV-041. The readiness gate from REV-038 is right and stays. The finding is that the
# contract offered no way back: `DeadLetterLog` had `record` and `unreconciled`, the store had
# `dead_letter` and `dead_letters`, and nothing could resolve one. A permanently refused payload is
# a NORMAL event by this lane's own retry rules, so one malformed message ended unattended
# operation for that source for ever.


class StoreBackedDeadLetterLog:
    """The dead-letter log wired to the REAL journal store, not a dict.

    CAP14 accepted `InMemoryDeadLetterLog` as an honest demonstration that the record outlives a
    `RunOutcome`, while noting it is not evidence that any store persists anything. This adapter
    closes that specific gap for the scenes below: every row here goes through
    `SqliteJournalStore.dead_letter`, survives in a file, and is counted by a SQL join against the
    reconciliation table.
    """

    def __init__(self, store):
        self.store = store

    def record(self, entry):
        return self.store.dead_letter(entry)

    def unreconciled(self, source_ref):
        return self.store.unreconciled_dead_letters()

    def reconcile_dead_letter(self, entry):
        return self.store.reconcile_dead_letter(entry)

    def unreconciled_dead_letters(self, source_key=None):
        return self.store.unreconciled_dead_letters(source_key)


def test_a_permanent_refusal_does_not_end_unattended_operation_for_ever(sink, store):
    """CAP14's operational scene, driven against the real store rather than a dict.

    One permanently refused payload, then three completely clean runs. Before the repair the answer
    was `[False, False, False]` with no third verb anywhere that could change it. The gate is
    unchanged; what is new is that a human can close the loss and readiness comes back.
    """
    log = StoreBackedDeadLetterLog(store)
    sink.fail_ids = {"uid-00001": "permanent"}
    first = run(FakeImapSource(email_items(3)), sink, Cursor.initial(MANIFEST.source_ref, "e1"),
                dead_letter_log=log)
    assert first.outcome.dead_lettered == 1
    assert first.outcome.unattended_ready is False

    sink.fail_ids = {}
    readiness = []
    for _ in range(3):
        clean = run(FakeImapSource([]), sink, Cursor.initial(MANIFEST.source_ref, "e1"),
                    dead_letter_log=log)
        assert clean.outcome.status is RunStatus.QUIET
        assert clean.outcome.dead_lettered == 0
        readiness.append(clean.outcome.unattended_ready)
    assert readiness == [False, False, False], "the gate holds until somebody deals with the loss"

    dead_letter_id = next(iter(store.dead_letters()))["dead_letter_id"]
    assert reconcile(log, dead_letter_id, resolution="accepted-loss", resolved_by="andrew",
                     resolved_at="2026-09-07T09:00:00Z",
                     note="the provider cannot re-serve this message") is True

    after = run(FakeImapSource([]), sink, Cursor.initial(MANIFEST.source_ref, "e1"),
                dead_letter_log=log)
    assert after.outcome.unreconciled_dead_letters == 0
    assert after.outcome.unattended_ready is True, "readiness must be regainable, not one-way"


def test_no_run_ever_reconciles_its_own_loss(sink, store):
    """The rule that makes the way back safe: this lane is never the resolver.

    A system that could clear its own dead letters would report unattended readiness by deciding it
    was fine, which is worse than the wall it replaced. Ten further sweeps, none of which touch the
    count, and only an explicit human act moves it.
    """
    log = StoreBackedDeadLetterLog(store)
    sink.fail_ids = {"uid-00001": "permanent"}
    run(FakeImapSource(email_items(2)), sink, Cursor.initial(MANIFEST.source_ref, "e1"),
        dead_letter_log=log)
    sink.fail_ids = {}

    for _ in range(10):
        result = run(FakeImapSource(email_items(2)), sink,
                     Cursor.initial(MANIFEST.source_ref, "e1"), dead_letter_log=log)
        assert result.outcome.unreconciled_dead_letters == 1
    assert store.unreconciled_dead_letters() == 1


def test_reconciling_appends_and_never_edits_the_dead_letter(sink, store):
    """The dead letter is evidence about yesterday and stays byte-identical.

    Buying readiness back by rewriting the record would trade the one property that makes the log
    worth reading. The resolution lives beside it, not in it.
    """
    log = StoreBackedDeadLetterLog(store)
    sink.fail_ids = {"uid-00001": "permanent"}
    run(FakeImapSource(email_items(2)), sink, Cursor.initial(MANIFEST.source_ref, "e1"),
        dead_letter_log=log)

    before = dict(next(iter(store.dead_letters())))
    reconcile(log, before["dead_letter_id"], resolution="not-an-item", resolved_by="andrew",
              resolved_at="2026-09-07T09:00:00Z")
    after = dict(next(iter(store.dead_letters())))

    assert after == before
    assert store.unreconciled_dead_letters() == 0


def test_a_loss_can_be_reopened_and_closed_again(sink, store):
    """The door opens in both directions, and the trail keeps every answer.

    THIS TEST USED TO ASSERT THE OPPOSITE. It said a second reconciliation was refused and called
    that idempotency. Terminal 04 drove the consequence: a human who recorded `still-unknown`
    could never record `recaptured` when the item actually arrived, so the honest answer blocked
    readiness for ever, which is the exact pressure toward `accepted-loss` this lane built the
    vocabulary to remove. The property is not "one resolution per loss"; it is "a resolution
    cannot be edited".
    """
    log = StoreBackedDeadLetterLog(store)
    sink.fail_ids = {"uid-00001": "permanent"}
    run(FakeImapSource(email_items(2)), sink, Cursor.initial(MANIFEST.source_ref, "e1"),
        dead_letter_log=log)
    dead_letter_id = next(iter(store.dead_letters()))["dead_letter_id"]

    reconcile(log, dead_letter_id, resolution="still-unknown", resolved_by="andrew",
              resolved_at="2026-09-07T09:00:00Z", note="cannot tell yet")
    assert store.unreconciled_dead_letters() == 1, "an honest not-yet-known closes nothing"

    reconcile(log, dead_letter_id, resolution="recaptured", resolved_by="andrew",
              resolved_at="2026-09-07T09:05:00Z", note="arrived by the other route")
    assert store.unreconciled_dead_letters() == 0

    trail = [r["resolution"] for r in store.dead_letter_reconciliations(dead_letter_id)]
    assert trail == ["still-unknown", "recaptured"]
