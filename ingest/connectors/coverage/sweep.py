"""One bounded run over one source. The place all of I02's properties become simultaneous.

THE SINK IS A CALLABLE, NOT AN IMPORT. `sweep` pushes each item into a `CaptureSink` it is
handed. In production that sink is `door.push` over HTTP; in the tests it is a `CaptureJournal`
wrapped by an adapter. Either way the connector layer stays free of the core, which is the rule
`connectors/__init__.py` sets and the property that lets a user run a connector on their laptop.

THE ORDER OF OPERATIONS INSIDE A RUN:

    1. Ask the provider for its epoch. A change resets the cursor and the run reports `reset`.
    2. Walk the window oldest-first inside the budget.
    3. Push each item. Classify the answer: captured, duplicate, dead-lettered or deferred.
    4. Advance the cursor over SETTLED items only.
    5. Stage authorised source hygiene -- and only for items that were actually captured.
    6. Report a status that says what is known, including what is NOT known.

Step 4 is where coverage is won or lost. The cursor advances over the settled prefix, so a
deferred item holds the frontier and comes back next run; it does not get stepped over because
the items after it happened to succeed.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from .cursors import Cursor, CursorError
from .manifests import HygieneRequest, SourceManifest
from .outcomes import ItemDisposition, RunOutcome, RunStatus
from .pagination import (
    AuthExpired,
    FairPager,
    ProviderError,
    ProviderItem,
    ProviderUnavailable,
    RateLimited,
    SourcePort,
)
from .retry import AttemptLedger, RetryPolicy, RunBudget


class PermanentPayloadError(RuntimeError):
    """The sink refused these bytes permanently. Dead-letter now; retrying cannot change it."""


class TransientSinkError(RuntimeError):
    """The sink could not accept the item right now. Defer and hold the frontier."""


class DeadLetterLog(Protocol):
    """The durable side of "explicitly dead-lettered", which I02's acceptance line requires.

    CAP14-REV-038 found that this lane recorded a dead letter as an `ItemDisposition` in the run
    outcome and nowhere else. That object lives as long as the process, so an item that left the
    retry ledger uncaptured left NO record: a later quiet run reported clean counters over a hole
    it could not see. "Explicitly dead-lettered" has to mean a row somebody can read tomorrow, not
    a field in an object that has already been garbage collected.

    Injected rather than imported, like the capture sink, so `connectors/` keeps importing only the
    door's contract and the standard library.

    THIS INTERFACE HAS TWO VERBS AND CANNOT CLEAR A LOSS. That is the design, not an omission.
    Reconciliation lives in `capture.reconcile`, needs a named human resolver, and on the Postgres
    side is the pair of tables migration 68 creates. It is named here because a reader arriving at
    this protocol should be told the verb exists elsewhere and why it is not here, rather than
    concluding the way back was never built: CAP14-REV-041 found the gap by reading exactly these
    two verbs. What the runner holds must not include the power to declare its own hole closed.
    """

    def record(self, entry: Mapping[str, Any]) -> str:
        """Persist one dead letter. Returns its id."""
        ...

    def unreconciled(self, source_ref: str) -> int:
        """How many dead letters for this source nothing has reconciled yet.

        THE WAY BACK IS NOT HERE, AND THAT IS DELIBERATE (I02-RECONCILE-01, CAP14-REV-041).
        Reconciliation lives in `capture.reconcile`, which requires a named human resolver and one
        of three stated resolutions. Nothing in `connectors/` calls it, and the sweep has no verb
        that could: a system able to clear its own losses would report unattended readiness by
        deciding it was fine, which is worse than the wall REV-041 found. This protocol carries
        only what a run needs, which is the ability to ask.
        """
        ...


class CaptureSink(Protocol):
    """What a connector needs from the door. Two answers and two exceptions, nothing else."""

    def __call__(
        self,
        item: ProviderItem,
        manifest: SourceManifest,
        hygiene: tuple[HygieneRequest, ...],
    ) -> str:
        """Return "captured" or "duplicate"; raise Permanent/Transient for the other two."""
        ...


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


@dataclass
class SweepResult:
    outcome: RunOutcome
    cursor: Cursor
    ledger: AttemptLedger


def sweep(
    *,
    port: SourcePort,
    manifest: SourceManifest,
    cursor: Cursor,
    sink: CaptureSink,
    budget: RunBudget | None = None,
    policy: RetryPolicy | None = None,
    ledger: AttemptLedger | None = None,
    dead_letter_log: DeadLetterLog | None = None,
    hygiene: tuple[str, ...] = (),
    page_size: int = 25,
    clock: Callable[[], str] = _now,
) -> SweepResult:
    budget = budget or RunBudget()
    policy = policy or RetryPolicy()
    ledger = ledger or AttemptLedger()
    started = clock()
    before = cursor.describe()
    notes: list[str] = []

    # 1. Epoch. A provider that reissued its ids has invalidated every position we hold.
    try:
        provider_epoch = port.epoch()
    except AuthExpired as exc:
        return _failed(manifest, cursor, before, started, clock, "auth-expired", str(exc), ledger)
    except ProviderError as exc:
        return _failed(manifest, cursor, before, started, clock, "provider", str(exc), ledger)

    cursor, reset = cursor.check_epoch(provider_epoch)
    if reset is not None:
        notes.append(reset.reason)
        # The ledger is per-item and its ids are from the old epoch. Keeping it would apply a
        # stale attempt count to a different item that happens to reuse the id.
        ledger = AttemptLedger()

    # 2-5. Walk and push.
    pager = FairPager(port, page_size=page_size)
    items: list[ItemDisposition] = []
    durable_ok = True
    pages = 0
    error_kind: str | None = None
    error_detail = ""
    retry_after: float | None = None
    hit_budget = False

    try:
        for item, page_no in pager.walk(cursor, budget.max_items, budget.max_pages):
            pages = max(pages, page_no)
            if not manifest.in_scope(item.payload.get("container")):
                # Out of the authorised boundary. It is settled -- we are not going to capture it
                # and we must not stall the frontier on it -- but it is recorded, not silent.
                items.append(
                    ItemDisposition(item.source_id, item.ordering_key, "dead-lettered",
                                    "outside the authorised source manifest")
                )
                durable_ok &= _record_dead_letter(
                    dead_letter_log, manifest, item, "outside-source-manifest",
                    "outside the authorised source manifest", clock())
                continue

            allowed = manifest.intents_for(item.source_id, hygiene)
            try:
                result = sink(item, manifest, allowed)
                ledger.clear(item.source_id)
                items.append(ItemDisposition(item.source_id, item.ordering_key, result))
            except PermanentPayloadError as exc:
                ledger.clear(item.source_id)
                items.append(
                    ItemDisposition(item.source_id, item.ordering_key, "dead-lettered", str(exc))
                )
                durable_ok &= _record_dead_letter(
                    dead_letter_log, manifest, item, "permanent-payload-refusal",
                    str(exc), clock())
            except TransientSinkError as exc:
                attempts = ledger.record_failure(item.source_id)
                if policy.exhausted(attempts):
                    ledger.clear(item.source_id)
                    items.append(
                        ItemDisposition(
                            item.source_id, item.ordering_key, "dead-lettered",
                            f"gave up after {attempts} attempts: {exc}",
                        )
                    )
                    # THE ONE CAP14-REV-038 FOUND. The item leaves the retry ledger here and is
                    # never tried again; if this row is not written, nothing outside this process
                    # ever knows it existed.
                    durable_ok &= _record_dead_letter(
                        dead_letter_log, manifest, item, "retry-exhausted",
                        f"gave up after {attempts} attempts: {exc}", clock())
                else:
                    items.append(
                        ItemDisposition(
                            item.source_id, item.ordering_key, "deferred",
                            f"attempt {attempts}, retry in {policy.backoff_for(attempts)}s: {exc}",
                        )
                    )
                    if budget.stop_on_first_deferred:
                        break
            if len(items) >= budget.max_items:
                hit_budget = True
    except RateLimited as exc:
        error_kind, error_detail, retry_after = "rate-limited", str(exc), exc.retry_after_s
    except AuthExpired as exc:
        error_kind, error_detail = "auth-expired", str(exc)
    except ProviderUnavailable as exc:
        error_kind, error_detail = "provider-unavailable", str(exc)
    except CursorError as exc:
        error_kind, error_detail = "corrupt-cursor", str(exc)

    # 4. Advance over the settled PREFIX only. A deferred item holds everything behind it.
    settled_prefix: list[tuple[str, str]] = []
    for disp in sorted(items, key=lambda d: (d.ordering_key, d.source_id)):
        if not disp.settled:
            break
        settled_prefix.append((disp.ordering_key, disp.source_id))
    cursor = cursor.advance(settled_prefix)

    # 6. A status that says what is known.
    deferred = sum(1 for i in items if not i.settled)
    if error_kind is not None:
        status = RunStatus.FAILED
    elif reset is not None:
        status = RunStatus.RESET
    elif hit_budget or deferred:
        status = RunStatus.PARTIAL
    elif not items:
        status = RunStatus.QUIET
    else:
        status = RunStatus.COMPLETE

    if deferred and error_kind is None:
        notes.append(f"{deferred} item(s) deferred; the cursor is held behind the oldest of them")

    return SweepResult(
        outcome=RunOutcome(
            source_ref=manifest.source_ref,
            status=status,
            started_at=started,
            finished_at=clock(),
            cursor_before=before,
            cursor_after=cursor.describe(),
            items=tuple(items),
            pages_fetched=pages,
            epoch_reset=reset,
            error_kind=error_kind,
            error_detail=error_detail,
            retry_after_s=retry_after,
            backlog_remaining=None if status in (RunStatus.COMPLETE, RunStatus.QUIET) else -1,
            notes=tuple(notes),
            durable_accounting=durable_ok,
            unreconciled_dead_letters=(
                dead_letter_log.unreconciled(manifest.source_ref) if dead_letter_log else None
            ),
        ),
        cursor=cursor,
        ledger=ledger,
    )


def _failed(manifest, cursor, before, started, clock, kind, detail, ledger) -> SweepResult:
    """A run that could not even establish the epoch. The cursor is returned untouched."""
    return SweepResult(
        outcome=RunOutcome(
            source_ref=manifest.source_ref,
            status=RunStatus.FAILED,
            started_at=started,
            finished_at=clock(),
            cursor_before=before,
            cursor_after=cursor.describe(),
            error_kind=kind,
            error_detail=detail,
            backlog_remaining=-1,
            notes=("coverage is unknown for this run; it is not zero",),
            durable_accounting=True,
            unreconciled_dead_letters=None,
        ),
        cursor=cursor,
        ledger=ledger,
    )


def _record_dead_letter(log, manifest, item, reason: str, detail: str, when: str) -> bool:
    """Write one durable dead letter. Returns False when there is no log to write to.

    The return value is the honest half: with no log configured this lane cannot claim its
    accounting is durable, and `RunOutcome.unattended_ready` reads that rather than assuming.
    Silently succeeding here is exactly the shape CAP14-REV-038 found.
    """
    if log is None:
        return False
    log.record({
        "dead_letter_id": f"dl_{manifest.source_ref}:{item.source_id}:{reason}",
        "source_key": item.source_id,
        "source_ref": manifest.source_ref,
        "reason": reason,
        "detail": detail[:2000],
        "received_at": when,
        "ordering_key": item.ordering_key,
    })
    return True
