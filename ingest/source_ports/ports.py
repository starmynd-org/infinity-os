"""Portable source ports: one adapter shape for every family, including the ones that cannot run.

A "port" here is the pair (family, method) from the availability matrix plus the code that turns
that method's evidence into `ProviderItem`s the existing coverage pager already understands. That
reuse is the point of the packet: Zoom, a voice note, a YouTube video, an X bookmark and a pasted
note all arrive through the same cursor, pagination, retry and dead-letter machinery that email
and Slack use, so a new family inherits the coverage properties instead of reinventing them.

THE THREE OUTCOMES A PORT CAN REPORT FOR ONE ITEM:

    available    -- evidence retrieved, hand it to the door
    unavailable  -- the item exists but its evidence cannot be had (private, revoked, deleted,
                    transcript not produced). A RECORD IS STILL MADE. Silence here is how a
                    coverage gap becomes invisible.
    refused      -- the method itself is not permitted to run at all

`UnavailableEvidence` is the type that stops the packet's worst failure: an item that quietly does
not arrive and leaves no trace, so the operator believes they have their meetings when one of them
was never captured.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Protocol, Sequence

from connectors.coverage.pagination import Page, PageRequest, ProviderItem

from .availability import Availability, SourceMethod
from .untrusted import ExternalContent


class PortRefused(RuntimeError):
    """This method cannot run. The reason is the matrix's reason; it is not a transient error."""


@dataclass(frozen=True)
class UnavailableEvidence:
    """The item exists; its evidence does not. Recorded, never skipped."""

    source_id: str
    ordering_key: str
    reason: str
    detail: str = ""
    retry_possible: bool = False

    def as_item(self, family: str) -> ProviderItem:
        """An unavailable item is still an item. It captures the ABSENCE, with its reason."""
        return ProviderItem(
            source_id=self.source_id,
            ordering_key=self.ordering_key,
            payload={
                "container": family,
                "evidence": "unavailable",
                "reason": self.reason,
                "detail": self.detail,
                "retry_possible": self.retry_possible,
            },
        )


@dataclass(frozen=True)
class PortItem:
    """Evidence retrieved from a portable source, with its untrusted parts wrapped."""

    source_id: str
    ordering_key: str
    fields: dict[str, Any]
    external: tuple[ExternalContent, ...] = ()
    media_ref: str | None = None

    def as_item(self, family: str) -> ProviderItem:
        payload: dict[str, Any] = {"container": family, "evidence": "present", **self.fields}
        if self.external:
            payload["external_content"] = [e.to_wire() for e in self.external]
            payload["external_flags"] = sorted({f for e in self.external for f in e.flags})
        if self.media_ref:
            payload["media_ref"] = self.media_ref
        return ProviderItem(source_id=self.source_id, ordering_key=self.ordering_key,
                            payload=payload)


class SourcePortAdapter(Protocol):
    """What every family adapter implements. Two of these are all the pager needs."""

    method: SourceMethod

    def epoch(self) -> str: ...

    def fetch_page(self, request: PageRequest) -> Page: ...


class FixturePort:
    """Base adapter over a local fixture set. No network call exists in this class or below it.

    A port whose method is not usable now REFUSES on every call rather than returning an empty
    page. An empty page from an unavailable source is indistinguishable from a quiet source, and
    that is exactly the "looks connected" failure the matrix exists to prevent.
    """

    def __init__(
        self,
        method: SourceMethod,
        items: Sequence[PortItem | UnavailableEvidence] = (),
        epoch_value: str = "e1",
    ):
        self.method = method
        self._items = list(items)
        self._epoch = epoch_value

    # -- availability -------------------------------------------------------------------------

    def _guard(self) -> None:
        if not self.method.usable_now:
            raise PortRefused(
                f"{self.method.family}/{self.method.method} is "
                f"{self.method.availability.value}: {self.method.reason}"
            )

    @property
    def connected(self) -> bool:
        """False for everything this packet can build. Only live activation makes it True."""
        return self.method.availability.is_connected

    # -- the port ------------------------------------------------------------------------------

    def epoch(self) -> str:
        self._guard()
        return self._epoch

    def set_epoch(self, value: str) -> None:
        self._epoch = value

    def revise(self, source_id: str, item: PortItem | UnavailableEvidence) -> None:
        """A later run finds new evidence for an id already seen -- a revision, not a new item."""
        self._items = [item if getattr(i, "source_id", None) == source_id else i
                       for i in self._items]

    def add(self, item: PortItem | UnavailableEvidence) -> None:
        self._items.append(item)

    def fetch_page(self, request: PageRequest) -> Page:
        self._guard()
        after = (request.after_key, request.after_id or "")
        provider_items = [i.as_item(self.method.family) for i in self._items]
        eligible = sorted(
            (i for i in provider_items
             if request.after_key is None or (i.ordering_key, i.source_id) > after),
            key=lambda i: (i.ordering_key, i.source_id),
        )
        window = eligible[: request.limit]
        return Page(items=window, has_more=len(eligible) > len(window))


class ManualImportPort(FixturePort):
    """The always-available fallback: the operator hands over the evidence themselves.

    Provenance is explicitly `manual`, and the origin the operator states is recorded as their
    CLAIM, not as a verified fact. The system cannot verify that a pasted email really came from
    the person named in it, and pretending otherwise would put an unverified assertion into
    evidence.
    """

    def __init__(self, method: SourceMethod, epoch_value: str = "manual"):
        super().__init__(method, (), epoch_value)

    def submit(
        self,
        *,
        source_id: str,
        occurred_at: str,
        text: str,
        stated_origin: str,
        files: Iterable[dict[str, Any]] = (),
    ) -> PortItem:
        item = PortItem(
            source_id=source_id,
            ordering_key=occurred_at,
            fields={
                "import_method": "manual",
                "stated_origin": stated_origin,
                "origin_verified": False,
                "files": list(files),
            },
            external=(ExternalContent.capture(text, origin=f"manual:{stated_origin}", field_name="text"),),
        )
        self.add(item)
        return item
