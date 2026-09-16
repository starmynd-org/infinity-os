"""Per-source setup manifests: the authorised capture boundary, as data.

Section 1 of the end-to-end flow says "capture everything" means everything inside an EXPLICITLY
AUTHORISED source boundary. This type is that boundary. Nothing in the sweep decides what is in
scope; it asks the manifest, and the manifest is written by a human during setup.

THREE PROPERTIES WORTH THE STRICTNESS:

* `hygiene_actions` is an allowlist, and `intents_for` returns only what it contains. A connector
  cannot mark an email read because its code says so; it can only do it because the manifest
  says the source permits it.
* `brains` is the authorised context audience and is copied onto every captured record. An event
  from a restricted source cannot later be used as context for a Brain it was never cleared for,
  because the permission travels with the evidence rather than being looked up at read time.
* `read_only` defaults TRUE. A manifest that has not thought about writeback does not get it.

`setup_checklist` exists so that "connect your email" produces an honest list of what the operator
is about to authorise, in their words, before any credential is created. It is also what the
availability matrix in `source_ports` renders.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: `connectors/__init__.py` states the rule: a connector may import `door` and the standard
#: library, and NOTHING else from this repository. That rule is what keeps a connector deletable
#: and runnable on a user's own machine, so this library obeys it too -- including not importing
#: `capture` for its intent type. `HygieneRequest` below is the local, stdlib-only equivalent,
#: and `sweep` translates it at the boundary.
STDLIB_ONLY = True


@dataclass(frozen=True)
class HygieneRequest:
    """An authorised source-side action, named without depending on the core's outbox type."""

    action: str
    target: str


@dataclass(frozen=True)
class SourceManifest:
    source_ref: str
    provider: str
    account_id: str
    #: What the connector is allowed to read. Empty means the whole authorised account, which is
    #: a decision the operator makes explicitly during setup.
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    #: Source-hygiene actions this source permits. Anything not listed cannot be staged.
    hygiene_actions: tuple[str, ...] = ()
    read_only: bool = True
    consent: str = "authorized-source-manifest"
    sensitivity: str = "normal"
    retain_days: int | None = None
    brains: tuple[str, ...] = ()
    fetch_attachments: bool = True
    fetch_history: bool = False
    notes: tuple[str, ...] = ()

    def permits_hygiene(self, action: str) -> bool:
        return not self.read_only and action in self.hygiene_actions

    def intents_for(self, source_id: str, requested: tuple[str, ...]) -> tuple[HygieneRequest, ...]:
        """Filter requested hygiene down to what the manifest actually authorises."""
        return tuple(
            HygieneRequest(action=a, target=source_id)
            for a in requested
            if self.permits_hygiene(a)
        )

    def in_scope(self, container: str | None) -> bool:
        """Folder/channel scoping. Exclusion wins over inclusion, always."""
        if container is None:
            return not self.include
        if container in self.exclude:
            return False
        return not self.include or container in self.include

    def setup_checklist(self) -> list[dict[str, Any]]:
        """What the operator is agreeing to, in plain sentences, before any credential exists."""
        return [
            {"item": "Provider", "value": self.provider},
            {"item": "Account", "value": self.account_id},
            {
                "item": "What is read",
                "value": ", ".join(self.include) if self.include
                else "the whole authorised account",
            },
            {
                "item": "What is never read",
                "value": ", ".join(self.exclude) if self.exclude else "nothing excluded yet",
            },
            {
                "item": "Attachments and files",
                "value": "retrieved and held in encrypted custody"
                if self.fetch_attachments
                else "not retrieved",
            },
            {
                "item": "History",
                "value": "historical items may be retrieved"
                if self.fetch_history
                else "new items only from connection time",
            },
            {
                "item": "Changes at the source",
                "value": "none; read-only"
                if self.read_only
                else ", ".join(self.hygiene_actions) or "none configured",
            },
            {
                "item": "Which Brains may see this as context",
                "value": ", ".join(self.brains) if self.brains else "none",
            },
            {
                "item": "Retention",
                "value": f"{self.retain_days} days"
                if self.retain_days
                else "kept until the operator deletes it",
            },
            {"item": "Consent basis", "value": self.consent},
        ]
