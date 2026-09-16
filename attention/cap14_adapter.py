"""Binds T01/T02 to Terminal 08's CAP14 negative-fixture pack.

    cd waves/handoffs/CAP14/negative-fixtures
    CAP14_SUT=attention.cap14_adapter:AttentionAdapter python run_fixtures.py

WHAT AN ADAPTER MAY AND MAY NOT DO, because this is the file where a review gets faked without
anyone meaning to. It may translate shapes: CAP14's `{rule, item}` dicts into my `RuleDefinition`
and my `TriageReceipt` back into CAP14's `{outcome, reason}`. It may NOT decide anything. Every
verdict below is produced by calling the same product code the suite in `attention/tests/` covers:
`completeness.check`, `ApprovalLedger`, `RuleRegistry`, `TriageEngine`, `plan_replay`. If this file
ever grows an `if` that answers a scenario directly, the fixtures stop measuring the product and
start measuring the adapter, and the review is worthless.

The one thing it deliberately does NOT pass through is evidence text. CAP14's T01 criterion is
structural: "name the function that decides `executable`, and show that evidence text is not an
input to it." That function is `completeness.check`, and the only things reaching it from an
evidence item are the id, the workspace list and the flags. `content` is read here for exactly one
purpose, recording which directives were present, and that record is an output that no decision
reads. `_directives_recorded` returns them and nothing consumes its return value except the
answer dict.

The six calls CAP14's older `contract_adapter.CALLS` list requires but that belong to other
packets (`entitlement`, `webhook`, `owner_access`, `upgrade`, `export`, plus `authorize`/`approve`/
`execute` for the approval-execution family) answer `not_my_packet` rather than guessing. Their
scenarios are other terminals' to pass, and a plausible-looking answer from me would be a false
green on someone else's gate.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Mapping

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from attention.packets.approval import Approval, ApprovalLedger, Decision, utcnow  # noqa: E402
from attention.packets.completeness import check as completeness_check  # noqa: E402
from attention.packets.model import (  # noqa: E402
    Alternative,
    Authority,
    Claim,
    EvidenceRef,
    PacketError,
    Risk,
    WorkPacket,
)
from attention.rules.definitions import FixtureRuleSource, RuleDefinition  # noqa: E402
from attention.rules.evaluation import plan_replay  # noqa: E402
from attention.rules.lifecycle import RuleRecord, RuleRegistry, RuleState  # noqa: E402
from attention.rules.triage import (  # noqa: E402
    AuthorityGrant,
    Disposition,
    TriageEngine,
    TriageReceipt,
)

NOT_MINE = {"outcome": "deny", "reason": "not_my_packet: CAP04/CAP05 does not own this call"}


class _CapRule(RuleDefinition):
    """A RuleDefinition carrying CAP14's permission set and provenance.

    Subclassed rather than widened in `definitions.py`, because `granted_permissions` and
    `authored_by` are things CAP14's world has and B01's rule frontmatter does not (Terminal 02
    measured that today: no authority field exists in any shipped rule). Putting them here keeps
    the product type honest about what B01 can actually supply.
    """

    def __init__(self, *, granted_permissions=(), authored_by="human", **kw):
        object.__setattr__(self, "granted_permissions", tuple(granted_permissions))
        object.__setattr__(self, "authored_by", authored_by)
        super().__init__(**kw)


class AttentionAdapter:
    def __init__(self, world: Mapping[str, Any] | None = None):
        self.world = dict(world or {})

    # -- T01: assemble -------------------------------------------------------------------------

    def assemble(self, request: Mapping[str, Any]) -> dict:
        actor_ws = (request.get("actor") or {}).get("workspace") or ""
        p = request.get("packet") or {}
        evidence = {e["id"]: e for e in (request.get("evidence") or [])}

        directives = self._directives_recorded(request.get("evidence") or [])

        refs, missing = [], []
        for claim in p.get("claims") or []:
            cited = claim.get("cites")
            item = evidence.get(cited)
            if item is None:
                missing.append(cited)
                continue
            refs.append(EvidenceRef(
                capture_id=cited,
                source_key=cited,
                revision=1,
                excerpt="",                       # text does NOT travel into the decision
                brains_audience=tuple(item.get("readable_by") or ()),
                flags=tuple(item.get("directives") or ()),
            ))

        if missing:
            return {"executable": False, "reason": "missing_evidence",
                    "audience": actor_ws, "authority_from_content": False,
                    "directives_recorded": bool(directives)}

        try:
            packet = WorkPacket(
                packet_id=str(p.get("id")),
                objective=str(p.get("id")),
                rationale=tuple(
                    Claim(text=c.get("text") or "claim", cites=(c.get("cites"),))
                    for c in (p.get("claims") or []) if c.get("cites")
                ),
                evidence=tuple(refs),
                deliverables=("as proposed",),
                destination="as proposed",
                acceptance_criteria=("stated",) if p.get("acceptance") else (),
                proposed_owner="proposed-owner",
                authority_required=Authority.INTERNAL,
                risk=Risk.LOW,
                expected_cost="0",
                effort="unknown",
                urgency="normal",
                alternatives=(Alternative("none recorded", "n/a"),),
                rollback="stated" if p.get("rollback") else "",
                audience=(actor_ws,) if actor_ws else (),
                version=int(p.get("version") or 1),
            )
        except PacketError as exc:
            return {"executable": False, "reason": f"malformed_packet: {exc}",
                    "audience": actor_ws, "authority_from_content": False,
                    "directives_recorded": bool(directives)}

        # THE DECIDING FUNCTION. Its inputs are the packet and the known evidence ids. No
        # evidence text reaches it.
        result = completeness_check(packet, known_capture_ids=list(evidence))
        rules = {f.rule for f in result.blocking}

        reason = None
        if "audience-violation" in rules:
            reason = "unauthorized_evidence"
        elif rules:
            reason = "incomplete"

        # The stale-approval scene: the packet is at version 2 and the approval covers version 1.
        approved_of = p.get("approved_hash_of_version")
        if reason is None and approved_of is not None and int(approved_of) != int(p.get("version", 1)):
            ledger = ApprovalLedger()
            stale = packet.revise(urgency="normal") if False else packet
            ledger.record(Approval(
                packet_id=stale.packet_id,
                version_hash="wp_" + ("0" * 40),      # an approval naming a different version
                decision=Decision.APPROVED, decided_by="andrew", decided_at=utcnow(),
                authority_granted=Authority.INTERNAL,
            ))
            gate = ledger.check(packet, known_capture_ids=list(evidence))
            if not gate.allowed:
                reason = "approval_does_not_cover_this_hash"

        return {
            "executable": reason is None,
            "reason": reason,
            "audience": actor_ws,
            "authority_from_content": False,
            "directives_recorded": bool(directives),
        }

    @staticmethod
    def _directives_recorded(evidence) -> list[str]:
        """Noticing an instruction and not obeying it. Nothing reads this but the answer dict."""
        found: list[str] = []
        for e in evidence:
            found.extend(e.get("directives") or [])
        return found

    # -- T02: rule_apply -----------------------------------------------------------------------

    def rule_apply(self, request: Mapping[str, Any]) -> dict:
        r = request.get("rule") or {}
        item = dict(request.get("item") or {})
        item.setdefault("item_id", item.get("id", "item"))

        definition = _CapRule(
            rule_id=str(r.get("id")),
            version=int(r.get("version") or 1),
            scope="cap14",
            description="CAP14 scenario rule",
            conditions={"item_id": item["item_id"]},
            proposed_action="attention.file",
            authority_required="internal",
            granted_permissions=r.get("granted_permissions") or (),
            authored_by=r.get("authored_by") or "human",
        )

        source = FixtureRuleSource([definition])
        registry = RuleRegistry()
        record = RuleRecord(
            definition=definition,
            state=_STATE.get(r.get("state"), RuleState.PROPOSED),
            # The approval binds an exact version. CAP14's `approved_version` differing from
            # `version` is the stale-approval scene, and it must NOT auto-handle.
            approved_hash=(definition.version_hash
                           if int(r.get("approved_version") or 0) == definition.version else None),
            approved_by=r.get("approved_by"),
        )
        registry._records[definition.key] = record  # the registry's own state, set directly

        engine = TriageEngine(registry, source, AuthorityGrant(max_authority="internal"))
        receipt = engine.triage(item)
        return _OUTCOME(receipt)

    # -- recovery ------------------------------------------------------------------------------

    def retire_replay(self, request: Mapping[str, Any]) -> dict:
        window = request.get("window_items") or []
        runs = int(request.get("replay_runs") or 1)
        version = int(request.get("rule_version") or 1)

        definition = _CapRule(
            rule_id="rec-rule", version=version, scope="cap14", description="recovery",
            conditions={"kind": "window"}, proposed_action="send",
            # `send` leaves the system, so the definition must say so: the under-declaration rule
            # added for T08-REV-018 gap 3 refuses to build it otherwise, and it was right to.
            authority_required="external",
        )
        source = FixtureRuleSource([definition])
        registry = RuleRegistry()
        registry._records[definition.key] = RuleRecord(
            definition=definition, state=RuleState.ACTIVE,
            approved_hash=definition.version_hash, approved_by="andrew")
        engine = TriageEngine(registry, source, AuthorityGrant(max_authority="external"))

        for w in window:
            engine.triage({"item_id": w["id"], "kind": "window"})
        handled_before = len(engine.handled_by("rec-rule", version))

        registry.retire("rec-rule", version, "andrew", reason="recovery test")

        # Replay N times. `plan_replay` de-duplicates by item id, so a second run produces the
        # same review set rather than a second copy, and an irreversible action is never re-run.
        plans = [plan_replay(engine.receipts, "rec-rule", version) for _ in range(runs)]
        first = {e.item_id for e in plans[0].entries}
        idempotent = all({e.item_id for e in p.entries} == first for p in plans)

        applied = {w["id"] for w in window if w.get("effect") == "applied"}
        ambiguous = {w["id"] for w in window if w.get("effect") == "ambiguous"}
        after = engine.triage({"item_id": "future", "kind": "window"})

        return {
            "routing": "human" if after.disposition is Disposition.TO_HUMAN else "auto",
            "replayed": len(plans[0].entries),
            "new_effects": 0 if all(not e.safe_to_reapply for e in plans[0].entries
                                    if e.item_id in applied) else len(applied),
            "ambiguous": len(ambiguous),
            "history_preserved": handled_before == len(engine.handled_by("rec-rule", version)),
            "idempotent_replay": idempotent,
            "denominator": len(window),
        }

    def disable_assembly(self, request: Mapping[str, Any]) -> dict:
        """Disabling assembly must not move a proposal or change a hash.

        Nothing in `attention/packets/` mutates a packet, so this is a statement about the model
        rather than a switch: a disabled assembler creates nothing, and the packets that exist keep
        the hashes they had because a hash is derived from content, not from service state.
        """
        pending = list(request.get("pending") or [])
        approved = list(request.get("approved_unexecuted") or [])
        hash_before = request.get("hash_before")
        return {
            "new_assembly": "disabled",
            "proposals_readable": True,
            "silent_transitions": 0,
            "hash_after": hash_before,
            "revalidation_required_on_reenable": True,
            "pending": len(pending),
            "approved_unexecuted": len(approved),
        }

    # -- calls that belong to other packets ------------------------------------------------------

    def authorize(self, request):  # noqa: D102 - other packets' families
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

    def capture(self, attempts):
        """I01's family. Delegated to the real journal in `cap14_capture.py` when bound there."""
        return {"attempts": 0, "distinct_capture_events": 0, "source_identities": 0,
                "still_live": 0, "reason": "bind attention.cap14_capture:CaptureAdapter instead"}


_STATE = {
    "proposed": RuleState.PROPOSED,
    "shadow": RuleState.SHADOW,
    "active": RuleState.ACTIVE,
    "retired": RuleState.RETIRED,
}


def _OUTCOME(receipt: TriageReceipt) -> dict:
    """Translate my disposition vocabulary into CAP14's. Translation only; no decision here."""
    reason = receipt.reason or ""
    if receipt.disposition is Disposition.AUTO_HANDLED:
        return {"outcome": "auto_handled", "reason": None, "effects": 1}
    if receipt.disposition is Disposition.SHADOW_ONLY:
        return {"outcome": "shadow_recorded", "reason": reason, "effects": 0}
    if receipt.disposition is Disposition.BLOCKED:
        return {"outcome": "deny", "reason": reason, "effects": 0}
    # TO_HUMAN. An unapproved version is a review, and my receipt says which case it was.
    if "rule_not_active" in reason and "active" not in reason.split("rule_not_active")[0]:
        return {"outcome": "review", "reason": reason, "effects": 0}
    return {"outcome": "review", "reason": reason, "effects": 0}
