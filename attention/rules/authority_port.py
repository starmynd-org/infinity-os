"""The seam onto R01's authority boundary, so T02 does not become a second one.

R01 landed `store/authority.py` (packet R01, migration 0057) after this packet's triage gate was
written. Its shape is:

    authority.check(subject, capability, scope, proposal=(proposal_id, proposal_version))
        -> the grant that authorised the act, or raises Denied

and it already enforces the property T01 built independently: *an approval of v1 does not authorise
v2*. Two authority models in one estate is how a gate gets bypassed by asking the other one, so the
correct end state is that `triage.py` asks R01 rather than deciding for itself.

IT CANNOT DO THAT TODAY, and the reason is written here rather than left implicit: `authority.check`
is a Postgres query, this packet has no store, and R01's own doc says a store that predates
migration 57 must read `Denied` rather than "no restrictions". So this module is the adapter, and
`AuthorityGrant` in `triage.py` stays the local, tested, in-memory implementation of the same
question until a composition root passes a real one.

THE MAPPING, stated once so the migration is mechanical:

    THE PIN LIVES IN ONE PLACE: `ingest/capture/state_port.py` records the STATE-API-PORT version
    and digest this lane is built against. It is not repeated here, because it was repeated here
    once and the two copies disagreed for two commits.

    T02 concept                 R01 concept
    ---------------------       -------------------------------------------
    rule.authority_required     capability      (see CAPABILITY_FOR; "none" asks nothing)
    item source_key             scope           ("source/<source_key>", EXACT string)
    rule (rule_id, version)     proposal        (proposal_id, proposal_version)
    Disposition.BLOCKED         Denied

TWO THINGS ABOUT R01 THAT ARE EASY TO GET WRONG, both from T04 on 2026-09-06.

**Scope matching is exact string equality. There is no hierarchy, no prefix, no wildcard.** A grant
on `source/gmail` does not cover `source/gmail/inbox`, and a grant on `source/` covers nothing. So
`scope_for` produces one flat string per source and never a path to be matched loosely; if a
hierarchy is ever wanted, it gets designed in R01 rather than faked with string prefixes on both
sides.

**From migration 0061, an approval must name the SUBJECT being checked.** Before it, `check`
matched a proposal id and version while ignoring the subject, so any actor holding any grant could
mint an approval naming itself and pass its own check. The rule-to-proposal mapping above is
unchanged; what changed is the meaning of a pass: somebody must have approved THAT ACTOR for that
rule version, not merely approved the rule version. Approvals written before 0061 name nobody and
now authorise nobody, deliberately un-grandfathered.

THE DIRECTION OF THE MAPPING MATTERS. A `Denied` from R01 must produce `BLOCKED`, never a fallback
to the local grant: an adapter that answers from its own model when the real boundary refuses is
worse than no adapter, because it looks integrated and is not. `StoreBackedAuthority.permits`
therefore has no except-and-allow arm, and an error that is not `Denied` is also a refusal.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol

from .definitions import RuleDefinition

#: T02's authority levels mapped onto the capability names `brain.capability` ACTUALLY holds.
#: Terminal 04 published the vocabulary on 2026-09-06: `work.claim`, `work.execute`, `work.accept`,
#: `effect.external`, `effect.spend`, `authority.grant`, `authority.revoke`, `approval.decide`,
#: `model.route`. My first mapping invented `effect.none` and `effect.internal`; neither existed,
#: so both would have hit the capability foreign key, and because this module reads any error as a
#: refusal they would have surfaced as a mysterious deny rather than as the wiring bug they were.
#:
#: A GENERAL HAZARD OF FAIL-CLOSED DESIGN, written here because this is where the next person
#: meets it. This module reads any exception as a refusal, which is the correct safety posture and
#: has a cost: a WIRING bug and a genuine denial are indistinguishable at the call site. An
#: invented capability name does not announce itself as a typo; it arrives as a foreign-key error,
#: becomes a refusal, and presents as "the authority boundary said no". That is why the mapping is
#: checked against the published vocabulary rather than assumed, why an unmappable level refuses
#: with its own distinct sentence, and why `LEDGER_FLOOR_FOR_CAPABILITY` refuses BEFORE the call
#: instead of letting the store reject it. Fail-closed systems hide their own misconfiguration;
#: the mitigation is to make every refusal say which kind it is.
#:
#: `none` IS DELIBERATELY ABSENT AND MUST STAY ABSENT. T04's reasoning, which is better than my
#: original: a capability meaning "no capability required" is a row that grants everything to
#: everyone the moment somebody grants it by mistake, and it would read as authorisation in the
#: permanent record. A rule that needs no authority asks no authority question, so `permits`
#: returns early and never calls `check`. That is a branch here, not a row there.
CAPABILITY_FOR = {
    "internal": "effect.internal",
    "external": "effect.external",
    "financial": "effect.spend",
}

#: A capability that does not exist yet in every store. `effect.internal` is added by migration
#: 0062 (T04), so delegating an `internal` rule to a store at 61 would hit the foreign key. The
#: floor is per capability rather than global, because the self-approval repair (0061) and the
#: vocabulary addition (0062) are different facts and collapsing them would either block the
#: capabilities that already work or admit one that does not exist.
LEDGER_FLOOR_FOR_CAPABILITY = {
    "effect.internal": 62,
}

#: R01's refusal kinds (T04, `4c41cf2`), and what a reader should DO about each. Naming the remedy
#: rather than only the kind is the whole point: the hazard this module documents is that a wiring
#: bug and a policy denial are indistinguishable at the call site, and a label that does not say
#: what to change has not fixed that.
#:
#: EVERY KIND STILL BLOCKS. The kind enriches the reason and never reaches the decision: `permits`
#: returns False on all of them, including kinds this table has never heard of. A classification
#: that could turn into an allow would be a worse bug than the ambiguity it set out to cure.
REMEDY_FOR_KIND = {
    "policy": "a rule said no; ask for authority",
    "unavailable": "no policy was consulted; the store predates the migration, so apply it",
    "wiring": "the caller is wrong, not the policy; change the code",
    "repeat": "this idempotency key was already used; reconcile rather than re-run",
}


class AuthorityPort(Protocol):
    """The one question triage asks. Both implementations answer it the same way."""

    def permits(self, rule: RuleDefinition, item: Mapping[str, Any]) -> tuple[bool, str]:
        """Return `(allowed, reason)`. `reason` is empty only when allowed."""
        ...


def scope_for(item: Mapping[str, Any]) -> str:
    source = item.get("source_key") or item.get("source") or ""
    return f"source/{source}"


#: The floor, and it has moved once for a reason worth keeping.
#:
#: It was 61: the migration that repairs R01's self-approval hole. Below that, R01 accepted an
#: approval row inserted by any actor naming itself decider, so a rule could be approved by the
#: agent that proposed it (T08 fixture NEG-RULE-04). Delegating before that landed would satisfy
#: this packet's acceptance line while violating the program's no-self-approval property: green
#: and wrong, the worst shape a gate can take.
#:
#: It is now **63**, on T04's correction with STATE-API-PORT v2. `workspace` became required at
#: 0063, and a pre-0063 row carries a NULL workspace which matches nothing: it authorises nobody
#: rather than everybody, which is the safe direction, but it means a store between 61 and 62
#: ACCEPTS the workspace argument while its rows predate the boundary, so every check denies for a
#: reason the error text cannot explain. A floor of 61 would have produced exactly the mysterious
#: deny this module was written to prevent, one layer further in.
MINIMUM_LEDGER_VERSION = 63


class AuthorityStoreTooOld(RuntimeError):
    """The store cannot yet be trusted with this delegation. Fails CLOSED, never open."""


@dataclass(frozen=True)
class StoreBackedAuthority:
    """Asks R01's `authority.check` instead of deciding locally.

    OFF BY DEFAULT AND GUARDED AT CONSTRUCTION. This class is never the default authority for a
    `TriageEngine`; a composition root has to pass it deliberately. On top of that it refuses to
    build at all against a store below `MINIMUM_LEDGER_VERSION`, mirroring R01's own pattern:
    `authority.check` raises `Denied` naming the missing migration rather than reading an absent
    table as "no restrictions", because the safe reading of cannot-say is no. The same reasoning
    applies one level up: a store that has not yet closed its self-approval hole cannot be asked to
    answer whether an agent approved its own rule.

    Switching this on is a recorded step after R01-REPAIR-01 lands and Terminal 08 re-reviews it,
    never a silent default.

    `check` is injected rather than imported so this module stays free of `store` and therefore of
    psycopg2: `attention/` must import cleanly on a machine with no database, which is the same
    discipline `state_port.py` follows for the capture journal.

    `subject` is the acting agent, and `denied_exc` is R01's `Denied` class, passed in for the same
    reason. Any other exception is treated as a refusal, because a boundary that errored did not
    say yes.
    """

    subject: str
    check: Callable[..., Any]
    denied_exc: type[BaseException]
    #: The workspace every call is made in. Required since the ledger-63 shape, and taken as a value
    #: rather than composed, so it can be the one the capture record carries and nothing else.
    workspace: str = ""
    #: The store's schema-ledger version, or a callable returning it. There is no default: a
    #: caller that cannot say which store it is talking to cannot be allowed to delegate to it.
    ledger_version: Any = None

    def _ledger(self) -> int:
        version = self.ledger_version() if callable(self.ledger_version) else self.ledger_version
        return int(version)

    def __post_init__(self) -> None:
        version = self.ledger_version() if callable(self.ledger_version) else self.ledger_version
        if version is None:
            raise AuthorityStoreTooOld(
                "refusing to delegate authority to a store of unknown ledger version; "
                f"R01-REPAIR-01 (migration {MINIMUM_LEDGER_VERSION}) must be present and "
                "cannot-say is read as no"
            )
        try:
            version = int(version)
        except (TypeError, ValueError) as exc:
            raise AuthorityStoreTooOld(
                f"ledger version {self.ledger_version!r} is not a number; refusing to delegate"
            ) from exc
        if version < MINIMUM_LEDGER_VERSION:
            raise AuthorityStoreTooOld(
                f"store is at schema ledger {version}; R01's self-approval hole is repaired at "
                f"{MINIMUM_LEDGER_VERSION} (R01-REPAIR-01). Until that migration has landed and "
                "been re-reviewed, an approval row can be inserted by the same actor that "
                "proposed the rule, so delegating here would report a pass on a rule the "
                "program's no-self-approval property forbids. Use the local AuthorityGrant."
            )

    def permits(self, rule: RuleDefinition, item: Mapping[str, Any]) -> tuple[bool, str]:
        # A rule requiring no authority asks no authority question. Calling `check` here would
        # need a capability meaning "none", and that row must not exist: see CAPABILITY_FOR.
        if rule.authority_required == "none":
            return True, ""

        capability = CAPABILITY_FOR.get(rule.authority_required)
        if capability is None:
            return False, (
                f"rule asks for authority level {rule.authority_required!r}, which maps to no "
                "capability; an unmappable level is refused rather than guessed"
            )
        floor = LEDGER_FLOOR_FOR_CAPABILITY.get(capability)
        if floor is not None and self._ledger() < floor:
            return False, (
                f"capability {capability!r} does not exist in a store below schema ledger {floor}; "
                "refusing rather than sending a check that would fail on the capability foreign "
                "key and read as an authorisation decision"
            )
        # `workspace` is keyword-only from v2. Passing it positionally, or omitting it, raises
        # TypeError at the seam rather than authorising anything, which is the behaviour T04 chose
        # and the one this module wants: a caller that has not decided which workspace it is acting
        # in has not decided whether it may act.
        workspace = item.get("workspace") or self.workspace
        if not workspace:
            return False, (
                "no workspace for this item and none configured; since the ledger-63 shape a check "
                "without a workspace is not a narrower check, it is an undecided one"
            )
        try:
            self.check(
                self.subject,
                capability,
                scope_for(item),
                proposal=(rule.rule_id, str(rule.version)),
                workspace=workspace,
            )
        except self.denied_exc as exc:
            # R01 classifies its refusals. Report the kind and its remedy so a wiring bug reads as
            # a wiring bug here too, which is the other half of the hazard documented above. An
            # unrecognised kind is reported verbatim and still blocks.
            kind = getattr(exc, "kind", None)
            if kind:
                remedy = REMEDY_FOR_KIND.get(kind, "unrecognised refusal kind; treat as a denial")
                return False, f"authority boundary refused [{kind}]: {exc} ({remedy})"
            return False, f"authority boundary refused: {exc}"
        except Exception as exc:  # noqa: BLE001 - a boundary that errored did not say yes
            return False, (
                f"authority boundary could not answer ({exc.__class__.__name__}: {exc}); "
                "cannot-say is read as no"
            )
        return True, ""
