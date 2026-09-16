"""Permission arithmetic, written so the independent reviewer can see the shape.

CAP14-REV-011 states the T02 acceptance criterion as an equation and says it will check the code,
not only the results:

    effective_permissions(item, rule) = route_permissions(item) INTERSECT granted_permissions(rule)

    "If the implementation computes a union anywhere, or consults the rule's grant set without
    intersecting, the property is false regardless of test results."

So the intersection is one named function with nothing else in it. A rule narrows what its route
already allowed; it can never add. `effective_permissions` has no argument that could widen the
result and no branch that returns the rule's grants alone.

THE OTHER TWO GATES LIVE HERE FOR THE SAME REASON: they are the ones a reviewer needs to find.

* `hard_flags_force_review` implements this repository's own `signal-vocabulary` and
  `surfacing-policy` rules: `external` and `canon_touching` are gates, not weights, and anything
  carrying either always surfaces whatever the rule says. Inheritance is by OR, never by
  reassessment, so a derived item cannot launder a flag by being reassessed.
* `self_approved` refuses a rule an agent both wrote and approved. The lifecycle already refuses an
  agent as the promoter; this catches the same thing on a record that arrived from elsewhere
  carrying its own provenance.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping


def effective_permissions(
    route_permissions: Iterable[str], granted_permissions: Iterable[str]
) -> frozenset[str]:
    """The intersection, and nothing else. A rule narrows; it never adds."""
    return frozenset(route_permissions) & frozenset(granted_permissions)


def permits_required(
    route_permissions: Iterable[str],
    granted_permissions: Iterable[str],
    required_permissions: Iterable[str],
) -> tuple[bool, frozenset[str]]:
    """Can the act proceed? Returns `(allowed, the permissions it lacked)`.

    Note what is compared: the REQUIRED set against the EFFECTIVE set. Comparing it against the
    rule's grants alone is the defect `DefectRuleBroadens` plants, and it is why the effective set
    is computed first and used for the test rather than assembled inline.
    """
    effective = effective_permissions(route_permissions, granted_permissions)
    required = frozenset(required_permissions)
    return required <= effective, required - effective


def hard_flags(item: Mapping[str, Any], parent: Mapping[str, Any] | None = None) -> dict[str, bool]:
    """`external` and `canon_touching`, inherited from a parent by OR and never lowered."""
    own_external = bool(item.get("external", False))
    own_canon = bool(item.get("canon_touching", item.get("canon-touching", False)))
    if parent is not None:
        own_external = own_external or bool(parent.get("external", False))
        own_canon = own_canon or bool(
            parent.get("canon_touching", parent.get("canon-touching", False))
        )
    return {"external": own_external, "canon_touching": own_canon}


def hard_flags_force_review(
    item: Mapping[str, Any], parent: Mapping[str, Any] | None = None
) -> bool:
    """A gate, not a weight. True means surface, whatever any rule says."""
    flags = hard_flags(item, parent)
    return flags["external"] or flags["canon_touching"]


def self_approved(authored_by: str | None, approved_by: str | None) -> bool:
    """An agent-authored rule needs a human approver. Anything else is self-approval."""
    authored = (authored_by or "").strip().lower()
    approved = (approved_by or "").strip().lower()
    if authored != "agent":
        return False
    return approved != "human"
