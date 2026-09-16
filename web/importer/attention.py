"""Map import outcomes to decidable ITEM/1.0 proposals, never announcements."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from .errors import ImportRefusal
from .model import Outcome


def _option(
    option_id: str,
    label: str,
    does: str,
    *,
    recommended: bool = False,
) -> dict[str, Any]:
    return {
        "option_id": option_id,
        "label": label,
        "does": does,
        "kind": "human-does",
        "reversibility": "reversible",
        "inverse": {
            "exists": True,
            "verb": "amend",
            "does": "amends this import disposition before any activation",
        },
        "recommended": recommended,
    }


def outcome_to_item(
    outcome: Outcome,
    *,
    workspace: str,
    actor_id: str,
    created_at: str,
    actor_github_provisioned: bool,
    actor_postgres_provisioned: bool,
    external: bool = False,
) -> dict[str, Any] | None:
    """Return a decision proposal only for a retained entity that needs a human act."""

    if outcome.entity is None or outcome.disposition not in {"imported", "review_required"}:
        return None
    if not actor_github_provisioned or not actor_postgres_provisioned:
        missing = []
        if not actor_github_provisioned:
            missing.append("GitHub")
        if not actor_postgres_provisioned:
            missing.append("Postgres")
        raise ImportRefusal(
            "ACTOR-UNKNOWN",
            actor_id,
            f"the producer actor is not provisioned in both identity planes; missing {missing}",
            "provision the same stable actor in GitHub and Postgres before emitting an item",
        )
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{2,127}", workspace):
        raise ImportRefusal(
            "BAD-ID",
            workspace,
            "workspace does not satisfy ITEM/1.0 Id syntax",
            "pass the stable receiving workspace id",
        )
    entity = outcome.entity
    digest = hashlib.sha256(
        f"{entity.source_ref.get('producer')}\0{outcome.member}\0{entity.entity_id}".encode("utf-8")
    ).hexdigest()[:24]
    authority_unknown = entity.authority_required == "unknown"
    needs_review = outcome.disposition == "review_required" or authority_unknown
    if needs_review:
        kind = "review"
        why = (
            f"{entity.name} has named import losses or unresolved authority; decide whether to "
            "review it now or keep the disabled draft unchanged."
        )
        options = [
            _option(
                "review-first",
                "Review first",
                "Opens the source references, losses, dependencies, and authority boundary for review.",
                recommended=True,
            ),
            _option(
                "keep-disabled",
                "Keep disabled",
                "Leaves the imported draft disabled without approving its unresolved semantics.",
            ),
        ]
    else:
        kind = "approval"
        why = f"{entity.name} is a complete disabled draft; decide whether to enable it or keep it disabled."
        options = [
            _option(
                "enable",
                "Enable",
                "Approves this disabled draft for the separate activation path.",
            ),
            _option(
                "keep-disabled",
                "Keep disabled",
                "Leaves the imported draft disabled.",
                recommended=True,
            ),
            _option(
                "review-first",
                "Review first",
                "Opens the source and mapped semantics before deciding on activation.",
            ),
        ]
    source = "external" if external else "brain"
    return {
        "contract_version": "ITEM/1.0",
        "c01_version": "0.1.0-draft.5",
        "item_id": f"estate-{digest}",
        "version": 1,
        "workspace": workspace,
        "producer": {
            "source": source,
            "actor": {"actor_id": actor_id, "kind": "worker", "workspace": workspace},
        },
        "created_at": created_at,
        "title": f"Review imported {entity.entity_type.lower()}: {entity.name}"[:200],
        "why": why[:500],
        "kind": kind,
        "options": options,
        "signals": {"reversibility": "reversible", "confidence": 1.0},
        "impact": {"status": "unknown"},
        "freshness": {
            "read_at": created_at,
            "interval_seconds": 86400,
        },
        "provenance": {
            "source_label": "ESTATE/1.0 filesystem import",
            "zone": "UTC",
            "steps": [
                {
                    "at": created_at,
                    "what": f"parsed {outcome.member} as {outcome.disposition}",
                    "by": actor_id,
                }
            ],
        },
        "readable": True,
        "external": external,
        "canon_touching": False,
        "authority_required": entity.authority_required,
        "completeness": {
            "state": "complete" if outcome.disposition == "imported" else "partial",
            **(
                {"missing": [loss.field for loss in outcome.losses]}
                if outcome.disposition == "review_required"
                else {}
            ),
        },
        "extensions": {"estate_member": outcome.member},
    }
