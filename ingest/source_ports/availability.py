"""The availability matrix: what each source family can actually do, stated honestly.

THE RULE THIS FILE EXISTS TO ENFORCE, from I03's acceptance line: *no unavailable connector looks
connected*. A schema that mentions WhatsApp is not WhatsApp support. A port that could work if
someone had an API tier nobody has bought is not a working port. So availability is an explicit
enumerated value with a REQUIRED reason for every state that is not fully available, and
`render_matrix` prints the reason next to the family rather than a green tick.

WHY `FIXTURE_TESTED` IS ITS OWN STATE AND NOT "AVAILABLE". Everything in this packet is tested
against local fixtures with no provider ever contacted. That earns "the adapter works against a
tested fixture", and it does not earn "your Zoom account is connected". Collapsing the two is the
exact overclaim the review flagged, so the type system refuses to collapse them: `is_connected`
is False for every state except `LIVE_AUTHORISED`, which nothing in this packet can set.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Availability(str, Enum):
    #: An adapter exists and is tested against a local fixture. No provider was contacted.
    FIXTURE_TESTED = "fixture-tested"
    #: The operator can bring data in by hand (file, export, paste). Tested, no provider needed.
    MANUAL_ONLY = "manual-only"
    #: The adapter is written but needs a provider approval, paid tier or app review first.
    NEEDS_PROVIDER_APPROVAL = "needs-provider-approval"
    #: The provider's terms or API do not permit this capture method at all.
    UNSUPPORTED = "unsupported"
    #: Live capture is authorised and running. NOTHING in this packet may set this.
    LIVE_AUTHORISED = "live-authorised"

    @property
    def is_connected(self) -> bool:
        return self is Availability.LIVE_AUTHORISED

    @property
    def needs_reason(self) -> bool:
        return self in (
            Availability.NEEDS_PROVIDER_APPROVAL,
            Availability.UNSUPPORTED,
            Availability.MANUAL_ONLY,
        )


@dataclass(frozen=True)
class SourceMethod:
    """One concrete way of getting evidence out of one family."""

    family: str
    method: str
    availability: Availability
    reason: str = ""
    consent: str = ""
    retention_note: str = ""
    captures: tuple[str, ...] = ()
    #: What the operator must do before this method can work at all.
    setup_steps: tuple[str, ...] = ()
    #: Source-side actions this method could perform, if separately authorised. Empty = read-only.
    hygiene: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.availability.needs_reason and not self.reason.strip():
            raise ValueError(
                f"{self.family}/{self.method} is {self.availability.value} and must say why; "
                "an unexplained limitation reads as a bug rather than a boundary"
            )
        if self.availability is Availability.LIVE_AUTHORISED:
            raise ValueError(
                "live-authorised cannot be declared here: activation is a separate operator "
                "action with its own authorisation, not a value in a matrix"
            )

    @property
    def usable_now(self) -> bool:
        return self.availability in (Availability.FIXTURE_TESTED, Availability.MANUAL_ONLY)


#: The declared matrix. Every family named in I03 appears, including the ones that do not work.
MATRIX: tuple[SourceMethod, ...] = (
    SourceMethod(
        family="zoom",
        method="cloud-recording-export",
        availability=Availability.FIXTURE_TESTED,
        consent="All meeting participants must be informed that the meeting is recorded and "
        "that the recording and transcript leave Zoom.",
        retention_note="Recording and transcript are held in encrypted custody; retention "
        "follows the source manifest, not Zoom's own retention.",
        captures=("recording reference", "transcript", "participants", "chat", "timing"),
        setup_steps=(
            "Create a Zoom Server-to-Server OAuth app in the account that owns the recordings.",
            "Grant recording:read:admin and user:read:admin. Nothing that can delete.",
            "Confirm every recurring meeting in scope has recording consent from participants.",
        ),
        hygiene=("mark_imported",),
    ),
    SourceMethod(
        family="zoom",
        method="live-meeting-bot",
        availability=Availability.UNSUPPORTED,
        reason="A joining bot changes the meeting for everyone present and needs per-meeting "
        "consent this system cannot obtain on its own. Not built.",
    ),
    SourceMethod(
        family="voicenotes",
        method="file-drop-import",
        availability=Availability.MANUAL_ONLY,
        reason="There is no provider API in scope; the operator drops audio or an export file "
        "into a watched folder, or uploads it.",
        consent="The operator's own recordings, or recordings they are entitled to hold.",
        retention_note="Audio and transcript are held in encrypted custody.",
        captures=("audio", "transcript when present", "recorded-at", "device"),
        setup_steps=(
            "Choose the import folder or use the upload form.",
            "Decide whether transcription happens locally or is left to the source app.",
        ),
    ),
    SourceMethod(
        family="youtube",
        method="public-video-metadata-and-captions",
        availability=Availability.FIXTURE_TESTED,
        consent="Public content only. Captures the operator's own saved or watched items.",
        retention_note="Metadata and captions are stored; media is referenced, not copied.",
        captures=("url", "title", "description", "published-at", "captions when published"),
        setup_steps=(
            "Provide a YouTube Data API key, or import a Takeout/watch-history export.",
        ),
    ),
    SourceMethod(
        family="youtube",
        method="private-or-unlisted-video",
        availability=Availability.NEEDS_PROVIDER_APPROVAL,
        reason="Requires an OAuth grant from the channel owner. Without it the item is "
        "unavailable, and an unavailable item is recorded as unavailable, not skipped.",
    ),
    SourceMethod(
        family="x",
        method="api-timeline-read",
        availability=Availability.NEEDS_PROVIDER_APPROVAL,
        reason="Timeline reads need a paid API tier tied to a developer account. Until that "
        "account and tier exist, this method cannot run and must not be shown as connected.",
    ),
    SourceMethod(
        family="x",
        method="bookmark-export-import",
        availability=Availability.MANUAL_ONLY,
        reason="The operator's own data export is the only route that needs no paid tier.",
        consent="The operator's own account data.",
        captures=("url", "author", "text", "saved-at"),
        setup_steps=("Request your X data archive.", "Import the bookmarks file."),
    ),
    SourceMethod(
        family="x",
        method="scrape-public-timeline",
        availability=Availability.UNSUPPORTED,
        reason="Prohibited by the platform's terms. Not built, and not going to be.",
    ),
    SourceMethod(
        family="whatsapp",
        method="any",
        availability=Availability.UNSUPPORTED,
        reason="There is no authorised programmatic read of personal WhatsApp conversations. "
        "A message schema that can represent a WhatsApp message is not WhatsApp support, and "
        "this row exists so nobody reads one as the other.",
    ),
    SourceMethod(
        family="manual",
        method="paste-or-upload",
        availability=Availability.MANUAL_ONLY,
        reason="Deliberately always available: it is the fallback for every family above and "
        "for sources that do not exist yet.",
        consent="Whatever the operator states at import time; it is recorded with the evidence.",
        captures=("text", "files", "a stated origin"),
        setup_steps=("None. It is the front door that always works.",),
    ),
)


def for_family(family: str) -> tuple[SourceMethod, ...]:
    return tuple(m for m in MATRIX if m.family == family)


def families() -> tuple[str, ...]:
    seen: list[str] = []
    for m in MATRIX:
        if m.family not in seen:
            seen.append(m.family)
    return tuple(seen)


def usable_methods() -> tuple[SourceMethod, ...]:
    return tuple(m for m in MATRIX if m.usable_now)


def render_matrix() -> str:
    """The operator-facing table. Reasons are in the table, not in a footnote."""
    rows = ["| Family | Method | Status | Why / what it needs |", "|---|---|---|---|"]
    for m in MATRIX:
        why = m.reason or ", ".join(m.setup_steps[:1]) or "-"
        rows.append(f"| {m.family} | {m.method} | {m.availability.value} | {why} |")
    return "\n".join(rows)


def setup_guide(family: str) -> dict[str, Any]:
    """Per-source setup and consent guide, assembled from the matrix rather than hand-written."""
    methods = for_family(family)
    if not methods:
        raise KeyError(f"no source family {family!r} in the availability matrix")
    return {
        "family": family,
        "usable_now": [m.method for m in methods if m.usable_now],
        "blocked": [
            {"method": m.method, "status": m.availability.value, "reason": m.reason}
            for m in methods
            if not m.usable_now
        ],
        "consent": [m.consent for m in methods if m.consent],
        "retention": [m.retention_note for m in methods if m.retention_note],
        "steps": [s for m in methods if m.usable_now for s in m.setup_steps],
        "source_side_actions": sorted({h for m in methods for h in m.hygiene}) or ["none"],
    }
