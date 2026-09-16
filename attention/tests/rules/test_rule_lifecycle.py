"""T02 acceptance: auditable shadow-to-approved triage.

The acceptance line: only approved exact rule versions auto-handle; all handled items have
receipts; retire restores judgment routing without losing evidence.

The required cases are each their own test: repeated decisions, ambiguous match, false positive,
operator override, source deletion, active-rule revision, and recoverable misrouting. The two
authority properties Terminal 08 reviews are at the bottom.
"""

from __future__ import annotations

import pytest

from attention.rules.definitions import (
    FixtureRuleSource,
    RuleDefinition,
    RuleDefinitionError,
    seam_digest,
)
from attention.rules.evaluation import (
    evaluate,
    observe_repetition,
    plan_replay,
    propose_from_repetition,
)
from attention.rules.lifecycle import (
    LifecycleError,
    RuleRecord,
    RuleRegistry,
    RuleState,
)
from attention.rules.triage import AuthorityGrant, Disposition, TriageEngine

HUMAN = "andrew"


def newsletter_rule(version: int = 1, **overrides) -> RuleDefinition:
    base = dict(
        rule_id="file-newsletters",
        version=version,
        scope="email",
        description="Newsletters go to reading, not to the inbox review.",
        conditions={"category": "newsletter"},
        proposed_action="file:reading",
        authority_required="internal",
        source_scope=("sk_mail",),
    )
    base.update(overrides)
    return RuleDefinition(**base)


@pytest.fixture
def setup():
    source = FixtureRuleSource()
    registry = RuleRegistry()
    engine = TriageEngine(registry, source,
                          AuthorityGrant(max_authority="internal", sources=("sk_mail",)))
    return source, registry, engine


def item(item_id: str, **fields):
    return {"item_id": item_id, "source_key": "sk_mail", **fields}


def activate(source, registry, definition):
    source.add(definition)
    registry.propose(definition)
    registry.shadow(definition.rule_id, definition.version, HUMAN)
    return registry.activate(definition.rule_id, definition.version, HUMAN)


# -- the ladder ------------------------------------------------------------------------------------


def test_a_proposed_rule_handles_nothing(setup):
    source, registry, engine = setup
    d = newsletter_rule()
    source.add(d)
    record = registry.propose(d)

    assert record.state is RuleState.PROPOSED
    assert record.auto_handles is False
    receipt = engine.triage(item("i1", category="newsletter"))
    assert receipt.disposition is Disposition.TO_HUMAN
    # The reason names the state rather than reading as plain residue: "a proposed rule matched and
    # was not allowed to act" and "nothing matched at all" are different facts for an operator.
    assert "rule_not_active" in receipt.reason
    assert "proposed" in receipt.reason


def test_a_shadow_rule_records_its_answer_and_applies_nothing(setup):
    source, registry, engine = setup
    d = newsletter_rule()
    source.add(d)
    registry.propose(d)
    registry.shadow(d.rule_id, d.version, HUMAN)

    receipt = engine.triage(item("i1", category="newsletter"))
    assert receipt.disposition is Disposition.SHADOW_ONLY
    assert receipt.action == "file:reading"
    assert receipt.disposition.applied is False
    assert engine.auto_handled() == ()


def test_only_an_approved_active_version_auto_handles(setup):
    source, registry, engine = setup
    activate(source, registry, newsletter_rule())

    receipt = engine.triage(item("i1", category="newsletter"))
    assert receipt.disposition is Disposition.AUTO_HANDLED
    assert receipt.rule_version_hash == newsletter_rule().version_hash
    assert receipt.ruleset_digest == source.digest()


def test_an_active_state_alone_does_not_auto_handle_without_a_matching_approved_hash():
    """`auto_handles` requires BOTH halves, and the second half needs its own test.

    Through the registry the two are kept in step, which masks whether `auto_handles` checks the
    hash at all. A record reconstructed from storage -- state ACTIVE, definition edited underneath
    it -- is the case the second half exists for, so it is built by hand here.
    """
    from attention.rules.lifecycle import RuleRecord

    definition = newsletter_rule()
    honest = RuleRecord(definition=definition, state=RuleState.ACTIVE,
                        approved_hash=definition.version_hash, approved_by=HUMAN)
    assert honest.auto_handles is True

    stale = RuleRecord(definition=definition, state=RuleState.ACTIVE,
                       approved_hash="rule_someoldhash", approved_by=HUMAN)
    assert stale.auto_handles is False, "an approval for a different version is not an approval"

    unapproved = RuleRecord(definition=definition, state=RuleState.ACTIVE, approved_hash=None)
    assert unapproved.auto_handles is False


@pytest.mark.parametrize("promoter", ["agent", "system", "auto", "Claude", ""])
def test_an_agent_cannot_promote_a_rule(setup, promoter):
    """LAYER ONE of the no-self-approval rule: who may put a rule into production.

    One case per name rather than one loop over five. The loop stopped at its first failure, so a
    run told you that some name was unguarded and never which; `_NON_HUMAN` could lose four of its
    eight entries and the report would look identical to losing one.

    THE SECOND LAYER IS `test_triage_blocks_an_agent_authored_agent_approved_rule` BELOW, and until
    it existed this test was the whole evidence for a two-layer property. Measured on this branch
    before the split: deleting layer two alone turned NOTHING red across all 105 tests, while
    deleting layer one alone and deleting BOTH layers produced the same red set of exactly one --
    this test. A suite that answers the same way to one defect and to two is not distinguishing
    them; it is reporting the one it can see and covering for the one it cannot.
    """
    source, registry, _ = setup
    d = newsletter_rule()
    source.add(d)
    registry.propose(d)
    registry.shadow(d.rule_id, d.version, HUMAN)
    with pytest.raises(LifecycleError, match="cannot promote"):
        registry.activate(d.rule_id, d.version, promoter)


# -- layer two: provenance that the lifecycle never saw ----------------------------------------


class _ProvenancedRule(RuleDefinition):
    """A definition carrying `authored_by`, which the product type deliberately does not have.

    `triage` reads provenance as `getattr(record.definition, "authored_by", None)` and no field on
    `RuleDefinition` supplies it, so through the ordinary registry path `self_approved` is always
    handed `None` and always answers False. Provenance reaches it only on a record built from
    outside -- `cap14_adapter._CapRule` is the one such builder in the tree, and it subclasses for
    the reason its own docstring gives: B01's rule frontmatter has no provenance field, so widening
    the product type would claim a fact B01 cannot supply. This subclass exists so the lane's own
    suite can drive that path without importing the reviewer's adapter.
    """

    def __init__(self, *, authored_by="human", **kw):
        object.__setattr__(self, "authored_by", authored_by)
        super().__init__(**kw)


def _seat_active(registry, definition, *, approved_by):
    """Seat an already-ACTIVE record directly, the way one restored from storage arrives.

    Deliberately not `activate`: layer one refuses a non-human promoter, so a record holding an
    agent's approval cannot be built through the registry at all. That is exactly why layer two
    exists, and it is why this helper reaches `_records` the way the adapter does.
    """
    record = RuleRecord(definition=definition, state=RuleState.ACTIVE,
                        approved_hash=definition.version_hash, approved_by=approved_by)
    registry._records[definition.key] = record
    return record


def _provenanced_newsletter_rule(*, authored_by, authority_required):
    return _ProvenancedRule(
        authored_by=authored_by,
        rule_id="file-newsletters", version=1, scope="email",
        description="Newsletters go to reading, not to the inbox review.",
        conditions={"category": "newsletter"}, proposed_action="file:reading",
        authority_required=authority_required, source_scope=("sk_mail",),
    )


def test_triage_blocks_an_agent_authored_agent_approved_rule(setup):
    """LAYER TWO, and the assertion is on the REASON because the disposition cannot carry it.

    CAP14 replaced the body of `permissions.self_approved` with `return False` and all 105
    attention tests stayed green. Re-measured here before this case was written: 21 of the 105
    reach `self_approved` -- so the instrument was pointed at the subject -- and not one of them
    ever passes it an agent author, which is why the rule could be deleted in silence.

    THE GRANT IS NARROWED ON PURPOSE SO THAT THE REASON HAS TO DO THE WORK. This rule declares
    `external` authority against an `internal` grant, so `grant.permits` refuses it too, one line
    after the gate under test. Delete `self_approved` and the disposition is STILL `BLOCKED`: a
    case asserting only the block passes straight over the deleted rule, green for the wrong
    cause. Only the reason separates the guard under test from a different guard answering a
    question it was never asked.
    """
    source, registry, engine = setup
    definition = _provenanced_newsletter_rule(authored_by="agent", authority_required="external")
    source.add(definition)
    _seat_active(registry, definition, approved_by="agent")

    receipt = engine.triage(item("i1", category="newsletter"))

    assert receipt.disposition is Disposition.BLOCKED
    assert receipt.reason.startswith("self_approval:"), (
        "blocked, but by the authority gate and not by the self-approval gate, which is the "
        f"shape this case exists to tell apart: {receipt.reason!r}"
    )
    assert receipt.rule_id == "file-newsletters"


def test_an_agent_authored_rule_with_a_human_approver_is_not_self_approved(setup):
    """The control, without which the case above passes on a rule that could never have run.

    Same provenance path, same seating, one field different. If this went to `BLOCKED` too, the
    case above would be measuring the fact that a hand-seated record is refused and not the fact
    that an agent's own approval is.
    """
    source, registry, engine = setup
    definition = _provenanced_newsletter_rule(authored_by="agent", authority_required="internal")
    source.add(definition)
    _seat_active(registry, definition, approved_by="human")

    receipt = engine.triage(item("i2", category="newsletter"))

    assert receipt.disposition is Disposition.AUTO_HANDLED
    assert "self_approval" not in receipt.reason


def test_a_rule_cannot_jump_from_proposed_to_active(setup):
    source, registry, _ = setup
    d = newsletter_rule()
    source.add(d)
    registry.propose(d)
    with pytest.raises(LifecycleError, match="proposed -> active"):
        registry.activate(d.rule_id, d.version, HUMAN)


def test_every_transition_is_logged_with_its_decider(setup):
    source, registry, _ = setup
    activate(source, registry, newsletter_rule())
    log = registry.transitions("file-newsletters")
    assert [t.to_state for t in log] == [RuleState.PROPOSED, RuleState.SHADOW, RuleState.ACTIVE]
    assert log[-1].decided_by == HUMAN
    assert log[-1].version_hash == newsletter_rule().version_hash


# -- repeated decisions -> a proposal ------------------------------------------------------------------


def test_repeated_decisions_produce_a_proposal_and_never_an_active_rule(setup):
    source, registry, _ = setup
    items = [item(f"n{i}", category="newsletter") for i in range(4)] + [
        item("x1", category="invoice")
    ]
    decisions = {f"n{i}": "file:reading" for i in range(4)} | {"x1": "task:pay"}

    observations = observe_repetition(items, decisions, on_fields=["category"], min_count=3)
    assert len(observations) == 1
    assert observations[0].count == 4
    assert observations[0].action == "file:reading"

    record = propose_from_repetition(
        registry, source, observations[0], rule_id="auto-1", scope="email",
        source_scope=("sk_mail",),
    )
    assert record.state is RuleState.PROPOSED
    assert record.auto_handles is False
    assert registry.active() == ()
    assert record.definition.evidence_refs == ("n0", "n1", "n2", "n3")


def test_a_single_decision_does_not_propose_anything(setup):
    _, _, _ = setup
    items = [item("n0", category="newsletter")]
    assert observe_repetition(items, {"n0": "file:reading"}, on_fields=["category"]) == ()


# -- ambiguous match ---------------------------------------------------------------------------------


def test_two_active_rules_proposing_different_actions_go_to_a_human(setup):
    source, registry, engine = setup
    activate(source, registry, newsletter_rule())
    activate(source, registry, RuleDefinition(
        rule_id="urgent-from-chair", version=1, scope="email",
        description="Anything from the chair is urgent.",
        conditions={"category": "newsletter"}, proposed_action="task:urgent",
        authority_required="internal", source_scope=("sk_mail",),
    ))

    receipt = engine.triage(item("i1", category="newsletter"))

    assert receipt.disposition is Disposition.TO_HUMAN
    assert "ambiguous" in receipt.reason
    assert set(receipt.candidates) == {"file-newsletters", "urgent-from-chair"}
    assert engine.auto_handled() == ()


def test_two_rules_agreeing_on_the_action_are_not_ambiguous(setup):
    source, registry, engine = setup
    activate(source, registry, newsletter_rule())
    activate(source, registry, newsletter_rule(rule_id="also-file", version=1))

    receipt = engine.triage(item("i1", category="newsletter"))
    assert receipt.disposition is Disposition.AUTO_HANDLED


# -- receipts ------------------------------------------------------------------------------------------


def test_every_item_gets_a_receipt_whatever_happens(setup):
    source, registry, engine = setup
    activate(source, registry, newsletter_rule())

    engine.triage(item("a", category="newsletter"))
    engine.triage(item("b", category="invoice"))
    engine.triage(item("c", category="newsletter"))

    assert len(engine.receipts) == 3
    assert {r.item_id for r in engine.receipts} == {"a", "b", "c"}
    assert all(r.decided_at and r.reason for r in engine.receipts)
    assert all(r.ruleset_digest == source.digest() for r in engine.receipts)


# -- false positive and operator override -----------------------------------------------------------------


def test_an_operator_override_is_audited_and_does_not_change_the_rule(setup):
    source, registry, engine = setup
    record = activate(source, registry, newsletter_rule())
    engine.triage(item("i1", category="newsletter"))

    override = engine.override("i1", by=HUMAN, action="task:reply", note="this one mattered")

    assert override.overridden_by == HUMAN
    assert override.override_action == "task:reply"
    assert override.rule_version_hash == record.definition.version_hash
    assert len(engine.overrides()) == 1
    # The rule is untouched: correction is a signal, not a permission change.
    assert record.state is RuleState.ACTIVE
    assert record.definition.proposed_action == "file:reading"
    assert record.auto_handles is True


def test_an_override_needs_a_named_human(setup):
    source, registry, engine = setup
    activate(source, registry, newsletter_rule())
    engine.triage(item("i1", category="newsletter"))
    for name in ("", "agent", "system"):
        with pytest.raises(ValueError, match="named human"):
            engine.override("i1", by=name, action="task:reply")


def test_false_positives_show_up_in_the_evaluation_with_a_denominator(setup):
    source, registry, engine = setup
    d = newsletter_rule()
    source.add(d)
    registry.propose(d)
    registry.shadow(d.rule_id, d.version, HUMAN)

    for i in range(5):
        engine.triage(item(f"n{i}", category="newsletter"))
    engine.override("n3", by=HUMAN, action="task:reply")

    decisions = {f"n{i}": "file:reading" for i in range(5)} | {"n3": "task:reply"}
    result = evaluate(engine.receipts, decisions, "file-newsletters", 1)

    assert result.observed == 6, "five shadow evaluations plus the override receipt"
    assert result.false_positives >= 1
    assert result.eligible_denominator == result.observed
    assert result.precision is not None and result.precision < 1.0
    assert result.promotable() is False


def test_a_rule_with_no_observations_has_no_rate_and_is_not_promotable():
    result = evaluate([], {}, "file-newsletters", 1)
    assert result.observed == 0
    assert result.precision is None, "a rate with no denominator is not a rate"
    assert result.promotable() is False


def test_a_clean_shadow_run_is_promotable_but_still_needs_a_human(setup):
    source, registry, engine = setup
    d = newsletter_rule()
    source.add(d)
    registry.propose(d)
    registry.shadow(d.rule_id, d.version, HUMAN)
    for i in range(6):
        engine.triage(item(f"n{i}", category="newsletter"))

    result = evaluate(engine.receipts, {f"n{i}": "file:reading" for i in range(6)},
                      "file-newsletters", 1)
    assert result.promotable() is True
    # Promotable is advisory. Nothing auto-handled, because nobody activated it.
    assert registry.active() == ()
    assert engine.auto_handled() == ()


# -- active-rule revision ----------------------------------------------------------------------------------


def test_revising_an_active_rule_stops_the_old_version_auto_handling(setup):
    source, registry, engine = setup
    activate(source, registry, newsletter_rule())
    engine.triage(item("i1", category="newsletter"))
    assert len(engine.auto_handled()) == 1

    new_record = registry.revise("file-newsletters", 1, HUMAN,
                                 conditions={"category": "newsletter", "sender": "known"})
    source.add(new_record.definition)

    assert registry.get("file-newsletters", 1).state is RuleState.RETIRED
    assert new_record.state is RuleState.PROPOSED
    assert registry.active() == ()

    receipt = engine.triage(item("i2", category="newsletter", sender="known"))
    assert receipt.disposition is Disposition.TO_HUMAN, "nothing auto-handles after a revision"
    # The evidence of what the retired version did is still there.
    assert len(engine.handled_by("file-newsletters", 1)) == 1


def test_a_revised_rule_has_a_different_hash_and_needs_a_new_approval(setup):
    source, registry, engine = setup
    activate(source, registry, newsletter_rule())
    original_hash = registry.get("file-newsletters", 1).definition.version_hash

    new_record = registry.revise("file-newsletters", 1, HUMAN, proposed_action="file:later")
    source.add(new_record.definition)

    assert new_record.definition.version_hash != original_hash
    assert new_record.approved_hash is None
    registry.shadow("file-newsletters", 2, HUMAN)
    registry.activate("file-newsletters", 2, HUMAN)
    assert registry.get("file-newsletters", 2).auto_handles is True
    assert registry.get("file-newsletters", 1).auto_handles is False


def test_a_definition_version_cannot_be_mutated_in_place(setup):
    source, _, _ = setup
    source.add(newsletter_rule())
    with pytest.raises(RuleDefinitionError, match="already exists"):
        source.add(newsletter_rule())


def test_pausing_an_active_rule_back_to_shadow_drops_its_approval(setup):
    source, registry, engine = setup
    record = activate(source, registry, newsletter_rule())
    registry.shadow("file-newsletters", 1, HUMAN, reason="looked wrong this morning")

    assert record.approved_hash is None
    assert record.auto_handles is False
    assert engine.triage(item("i9", category="newsletter")).disposition is Disposition.SHADOW_ONLY


# -- retirement, misrouting and replay ---------------------------------------------------------------------


def test_retiring_a_rule_restores_judgment_routing_without_losing_evidence(setup):
    source, registry, engine = setup
    activate(source, registry, newsletter_rule())
    for i in range(3):
        engine.triage(item(f"n{i}", category="newsletter"))
    assert len(engine.auto_handled()) == 3

    registry.retire("file-newsletters", 1, HUMAN, reason="misrouting the board digest")

    assert registry.active() == ()
    after = engine.triage(item("n9", category="newsletter"))
    assert after.disposition is Disposition.TO_HUMAN
    # Evidence survives retirement.
    assert len(engine.handled_by("file-newsletters", 1)) == 3
    assert registry.get("file-newsletters", 1).state is RuleState.RETIRED
    assert len(registry.transitions("file-newsletters")) == 4


def test_a_replay_routes_every_affected_item_back_to_review_exactly_once(setup):
    source, registry, engine = setup
    activate(source, registry, newsletter_rule())
    for i in range(4):
        engine.triage(item(f"n{i}", category="newsletter"))
    engine.override("n2", by=HUMAN, action="task:reply")
    registry.retire("file-newsletters", 1, HUMAN, reason="recoverable misrouting")

    plan = plan_replay(engine.receipts, "file-newsletters", 1)

    assert len(plan.entries) == 4, "four items, one entry each despite the extra override receipt"
    assert len({e.item_id for e in plan.entries}) == 4
    assert plan.summary()["all_routed_to_human"] is True
    assert plan.summary()["items"] == 4


def test_replay_never_re_runs_an_action_that_already_left_the_system(setup):
    """Duplicate-effect protection: an irreversible action is shown, not repeated."""
    source, registry, engine = setup
    engine.grant = AuthorityGrant(max_authority="external", sources=("sk_mail",))
    activate(source, registry, newsletter_rule(
        rule_id="auto-ack", proposed_action="send", authority_required="external"
    ))
    for i in range(2):
        engine.triage(item(f"s{i}", category="newsletter"))

    plan = plan_replay(engine.receipts, "auto-ack", 1)

    assert len(plan.entries) == 2
    assert all(e.effect_already_applied for e in plan.entries)
    assert all(not e.safe_to_reapply for e in plan.entries)
    assert all(e.route == "human-review-with-warning" for e in plan.entries)
    assert plan.summary()["unsafe_to_reapply"] == 2
    assert "do not re-run" in plan.entries[0].note


def test_a_reversible_action_is_safe_to_decide_again(setup):
    source, registry, engine = setup
    activate(source, registry, newsletter_rule())
    engine.triage(item("n0", category="newsletter"))
    plan = plan_replay(engine.receipts, "file-newsletters", 1)
    assert plan.entries[0].safe_to_reapply is True
    assert plan.needs_warning == ()


def test_a_replay_for_a_rule_that_handled_nothing_is_empty_not_an_error(setup):
    source, registry, engine = setup
    activate(source, registry, newsletter_rule())
    plan = plan_replay(engine.receipts, "file-newsletters", 1)
    assert plan.entries == ()
    assert plan.summary()["items"] == 0


# -- source deletion -------------------------------------------------------------------------------------------


def test_a_deleted_source_item_is_flagged_through_its_receipt(setup):
    """A tombstoned source item that a rule already handled must be findable and re-reviewable."""
    source, registry, engine = setup
    activate(source, registry, newsletter_rule())
    engine.triage(item("n0", category="newsletter"))
    engine.triage(item("n1", category="newsletter"))

    tombstoned = {"n1"}
    affected = [r for r in engine.auto_handled() if r.item_id in tombstoned]

    assert len(affected) == 1
    assert affected[0].rule_id == "file-newsletters"
    # The receipt survives the source deletion; that is what makes the item recoverable.
    plan = plan_replay(engine.receipts, "file-newsletters", 1)
    assert "n1" in {e.item_id for e in plan.entries}


# -- the two authority properties Terminal 08 reviews ----------------------------------------------------------------


def test_a_rule_cannot_grant_itself_authority_it_was_not_given(setup):
    """Property 1: rules cannot broaden permission."""
    source, registry, _ = setup
    engine = TriageEngine(registry, source,
                          AuthorityGrant(max_authority="internal", sources=("sk_mail",)))
    activate(source, registry, newsletter_rule(
        rule_id="auto-pay", proposed_action="pay", authority_required="financial"
    ))

    receipt = engine.triage(item("i1", category="newsletter"))

    assert receipt.disposition is Disposition.BLOCKED
    assert receipt.disposition.applied is False
    assert "financial" in receipt.reason and "internal" in receipt.reason
    assert engine.auto_handled() == ()


def test_a_rule_cannot_act_on_a_source_outside_the_grant(setup):
    """Property 2: rules cannot broaden source permission either."""
    source, registry, _ = setup
    engine = TriageEngine(registry, source,
                          AuthorityGrant(max_authority="internal", sources=("sk_mail",)))
    activate(source, registry, newsletter_rule(source_scope=("sk_mail", "sk_private")))

    outside = {"item_id": "i1", "source_key": "sk_private", "category": "newsletter"}
    receipt = engine.triage(outside)

    assert receipt.disposition is Disposition.BLOCKED
    assert "granted sources" in receipt.reason


def test_narrowing_the_grant_after_activation_takes_effect_immediately(setup):
    """The grant is checked per item, so a narrowed grant is not waiting on a re-approval."""
    source, registry, _ = setup
    wide = TriageEngine(registry, source, AuthorityGrant(max_authority="external",
                                                         sources=("sk_mail",)))
    activate(source, registry, newsletter_rule(
        rule_id="auto-send", proposed_action="send", authority_required="external"
    ))
    assert wide.triage(item("i1", category="newsletter")).disposition is Disposition.AUTO_HANDLED

    narrowed = TriageEngine(registry, source,
                            AuthorityGrant(max_authority="internal", sources=("sk_mail",)))
    assert narrowed.triage(item("i2", category="newsletter")).disposition is Disposition.BLOCKED


def test_a_rule_cannot_cite_brains_outside_the_grant(setup):
    source, registry, _ = setup
    engine = TriageEngine(registry, source, AuthorityGrant(
        max_authority="internal", sources=("sk_mail",), brains=("company",)))
    activate(source, registry, newsletter_rule(brains_audience=("company", "personal")))

    receipt = engine.triage(item("i1", category="newsletter"))
    assert receipt.disposition is Disposition.BLOCKED
    assert "personal" in receipt.reason


def test_a_rule_with_no_conditions_cannot_exist():
    """It would match everything, which is the widest possible permission grab."""
    with pytest.raises(RuleDefinitionError, match="match everything"):
        RuleDefinition(rule_id="r", version=1, scope="email", description="",
                       conditions={}, proposed_action="file:reading")


def test_the_seam_digest_is_stable_and_pinnable():
    assert seam_digest() == seam_digest()
    assert seam_digest().startswith("seam_")


# -- the R01 authority seam ----------------------------------------------------------------------


def test_the_store_backed_authority_asks_r01_with_the_exact_rule_version(setup):
    """T02 must not become a second authority boundary once R01's exists.

    R01 landed `store/authority.check(subject, capability, scope, proposal=(id, version))`, which
    already enforces "an approval of v1 does not authorise v2". This asserts the adapter asks that
    question with the right arguments, so swapping the real `check` in is a composition change and
    not a rewrite of the gate.
    """
    from attention.rules.authority_port import CAPABILITY_FOR, StoreBackedAuthority

    class Denied(Exception):
        pass

    asked = []

    def fake_check(subject, capability, scope, *, proposal=None, workspace=None, ctx=None):
        asked.append((subject, capability, scope, proposal))
        return {"grant_seq": 1}

    source, registry, _ = setup
    engine = TriageEngine(registry, source,
                          StoreBackedAuthority("agent-triage", fake_check, Denied, ledger_version=64,
                                            workspace="ws:alpha"))
    activate(source, registry, newsletter_rule())

    receipt = engine.triage(item("i1", category="newsletter"))

    assert receipt.disposition is Disposition.AUTO_HANDLED
    subject, capability, scope, proposal = asked[0]
    assert subject == "agent-triage"
    assert capability == CAPABILITY_FOR["internal"] == "effect.internal"
    # 62, not 61: the self-approval repair lands at 0061 and `effect.internal` at 0062.
    assert scope == "source/sk_mail"
    assert proposal == ("file-newsletters", "1"), "the exact rule version, not just its id"


def test_a_denial_from_r01_blocks_and_never_falls_back_to_the_local_grant(setup):
    """An adapter that answers from its own model when the real boundary refuses is worse than none."""
    from attention.rules.authority_port import StoreBackedAuthority

    class Denied(Exception):
        pass

    def refusing_check(subject, capability, scope, *, proposal=None, workspace=None, ctx=None):
        raise Denied(f"{subject!r} holds no grant in force for {capability!r} on {scope!r}.")

    source, registry, _ = setup
    engine = TriageEngine(registry, source,
                          StoreBackedAuthority("agent-triage", refusing_check, Denied, ledger_version=64,
                                               workspace="ws:alpha"))
    activate(source, registry, newsletter_rule())

    receipt = engine.triage(item("i1", category="newsletter"))

    assert receipt.disposition is Disposition.BLOCKED
    assert "authority boundary refused" in receipt.reason
    assert engine.auto_handled() == ()


def test_an_authority_boundary_that_errors_is_read_as_no(setup):
    """R01's own rule: a store that cannot say whether an act was authorised means no."""
    from attention.rules.authority_port import StoreBackedAuthority

    class Denied(Exception):
        pass

    def broken_check(subject, capability, scope, *, proposal=None, workspace=None, ctx=None):
        raise RuntimeError("the bus dropped the connection")

    source, registry, _ = setup
    engine = TriageEngine(registry, source,
                          StoreBackedAuthority("agent-triage", broken_check, Denied, ledger_version=64,
                                               workspace="ws:alpha"))
    activate(source, registry, newsletter_rule())

    receipt = engine.triage(item("i1", category="newsletter"))
    assert receipt.disposition is Disposition.BLOCKED
    assert "cannot-say is read as no" in receipt.reason


def test_an_unmappable_authority_level_is_refused_rather_than_guessed(setup):
    from attention.rules.authority_port import StoreBackedAuthority

    class Denied(Exception):
        pass

    source, registry, _ = setup
    engine = TriageEngine(registry, source,
                          StoreBackedAuthority("agent-triage", lambda *a, **k: {}, Denied, ledger_version=64,
                                               workspace="ws:alpha"))
    activate(source, registry, newsletter_rule(authority_required="unknown"))

    receipt = engine.triage(item("i1", category="newsletter"))
    assert receipt.disposition is Disposition.BLOCKED
    assert "maps to no" in receipt.reason


def test_store_backed_authority_refuses_a_store_below_the_repair_migration():
    """T08-REV-018 gap 1, and the Admiral's recorded condition. Fails CLOSED.

    R01 below ledger 0061 accepts an approval row inserted by any actor naming itself decider, so
    a rule could be approved by the agent that proposed it. Delegating there would report a pass
    on exactly the rule NEG-RULE-04 forbids: green and wrong, which is worse than red. So the
    constructor refuses to build, rather than building and hoping the caller checks.
    """
    from attention.rules.authority_port import (
        MINIMUM_LEDGER_VERSION,
        AuthorityStoreTooOld,
        StoreBackedAuthority,
    )

    class Denied(Exception):
        pass

    def never_called(*a, **k):
        raise AssertionError("the guard must refuse before any store call is made")

    for planted in (0, 56, 57, 60, 61, 62, MINIMUM_LEDGER_VERSION - 1):
        with pytest.raises(AuthorityStoreTooOld, match="R01-REPAIR-01|self-approval"):
            StoreBackedAuthority("agent-triage", never_called, Denied, ledger_version=planted)

    # An unknown version is not "probably fine": cannot-say is read as no, as R01 itself rules.
    with pytest.raises(AuthorityStoreTooOld, match="unknown ledger version"):
        StoreBackedAuthority("agent-triage", never_called, Denied)
    with pytest.raises(AuthorityStoreTooOld, match="not a number"):
        StoreBackedAuthority("agent-triage", never_called, Denied, ledger_version="recent")

    # At and above the repair it builds, and the version may be read lazily from the store.
    ok = StoreBackedAuthority("agent-triage", lambda *a, **k: {}, Denied,
                              ledger_version=MINIMUM_LEDGER_VERSION, workspace="ws:alpha")
    assert ok.subject == "agent-triage"
    assert StoreBackedAuthority("agent-triage", lambda *a, **k: {}, Denied,
                                ledger_version=lambda: 99, workspace="ws:alpha") is not None


def test_the_default_authority_is_local_and_never_the_store(setup):
    """The store-backed path is opt-in. A TriageEngine built the ordinary way never reaches R01."""
    source, registry, engine = setup
    assert isinstance(engine.grant, AuthorityGrant)
    assert TriageEngine(registry, source).grant.__class__ is AuthorityGrant


def test_an_effectful_rule_cannot_declare_less_than_external_authority():
    """T08-REV-018 gap 3. The declaration is an input to the gate, so under-declaring walks past it."""
    for action in ("send", "publish", "pay", "delete", "archive-at-source"):
        for level in ("none", "internal"):
            with pytest.raises(RuleDefinitionError, match="leaves the system"):
                newsletter_rule(rule_id=f"bad-{action}", proposed_action=action,
                                authority_required=level)
        # Declared honestly, it builds.
        assert newsletter_rule(rule_id=f"ok-{action}", proposed_action=action,
                               authority_required="external").proposed_action == action


def test_a_canon_touching_rule_always_surfaces_and_no_grant_lifts_it(setup):
    """CAP14-REV-022: the contract's mechanism is canonical and there is no granted canon field.

    T08-REV-018 gap 2 asked for canon-touching as an axis independent of the authority level, and
    my first implementation made it a thing a run could HOLD, with a matching fixture asserting
    `canon_touching_granted` on the approval decision. That field does not exist. The contract's
    mechanism is `canon_touching` plus `surfacing: human`, flag never lowered (AR-I3), so the
    correct behaviour is the one an item carrying the flag already gets: it surfaces, always, and
    no grant can lift it.
    """
    source, registry, engine = setup
    activate(source, registry, newsletter_rule(rule_id="edit-canon", canon_touching=True))

    receipt = engine.triage(item("i1", category="newsletter"))
    assert receipt.disposition is Disposition.TO_HUMAN
    assert "hard_flag_always_surfaces" in receipt.reason
    assert engine.auto_handled() == ()

    # There is no grant that changes this. The widest grant available still surfaces it.
    widest = TriageEngine(registry, source, AuthorityGrant(
        max_authority="financial", sources=("sk_mail",), brains=("company",)))
    assert widest.triage(item("i2", category="newsletter")).disposition is Disposition.TO_HUMAN


def test_the_authority_grant_has_no_canon_axis_to_grant():
    """The absence is the point: a grantable canon flag is what the ruling removed."""
    assert not hasattr(AuthorityGrant(), "canon_touching")


def test_canon_touching_is_part_of_the_rule_version_hash():
    """It is authority-bearing, so flipping it must invalidate an approval rather than ride along."""
    plain = newsletter_rule(rule_id="r-canon")
    canon = newsletter_rule(rule_id="r-canon", canon_touching=True)
    assert plain.version_hash != canon.version_hash


def test_a_no_authority_rule_asks_the_store_nothing():
    """T04's ruling: a capability meaning "no capability required" must not exist as a row.

    Such a row grants everything to everyone the moment somebody grants it by mistake, and it
    would read as authorisation in the permanent record. So a rule needing no authority takes a
    branch here and never reaches `check`.
    """
    from attention.rules.authority_port import CAPABILITY_FOR, StoreBackedAuthority

    class Denied(Exception):
        pass

    def never_called(*a, **k):
        raise AssertionError("a none-authority rule must not reach the store")

    assert "none" not in CAPABILITY_FOR
    auth = StoreBackedAuthority("agent-triage", never_called, Denied, ledger_version=99,
                                    workspace="ws:alpha")
    rule = newsletter_rule(rule_id="r-none", authority_required="none")
    allowed, reason = auth.permits(rule, {"item_id": "i1", "source_key": "sk_mail"})
    assert allowed is True and reason == ""


def test_a_capability_missing_from_the_store_refuses_before_the_call():
    """The per-capability floor, and an honest note that its only entry is now unreachable.

    `effect.internal` lands in migration 0062 and carries a floor of 62. With STATE-API-PORT v2 the
    CONSTRUCTOR floor rose to 63, so no store this class will build against can be below 62: that
    entry can no longer fire. It stays because the mechanism is right and the next capability added
    late will need it, but a floor nothing can reach is dead code, and saying so here is better
    than leaving a test that looks like it proves something it cannot.

    So the mechanism is exercised where it CAN fire: a capability whose floor is above the
    constructor floor.
    """
    from attention.rules import authority_port as ap
    from attention.rules.authority_port import (
        CAPABILITY_FOR,
        LEDGER_FLOOR_FOR_CAPABILITY,
        MINIMUM_LEDGER_VERSION,
        StoreBackedAuthority,
    )

    class Denied(Exception):
        pass

    def never_called(*a, **k):
        raise AssertionError("the floor must refuse before the store is called")

    # The subsumed entry, stated rather than asserted away.
    assert LEDGER_FLOOR_FOR_CAPABILITY["effect.internal"] == 62
    assert MINIMUM_LEDGER_VERSION == 63
    assert LEDGER_FLOOR_FOR_CAPABILITY["effect.internal"] < MINIMUM_LEDGER_VERSION

    # The mechanism still works for a capability that arrives after the constructor floor.
    original = dict(LEDGER_FLOOR_FOR_CAPABILITY)
    try:
        ap.LEDGER_FLOOR_FOR_CAPABILITY["effect.external"] = 99
        at_63 = StoreBackedAuthority("agent-triage", never_called, Denied, ledger_version=63,
                                     workspace="ws:alpha")
        rule = newsletter_rule(rule_id="r-ext", proposed_action="send",
                               authority_required="external")
        allowed, reason = at_63.permits(rule, {"item_id": "i1", "source_key": "sk_mail"})
        assert allowed is False
        assert "effect.external" in reason and "99" in reason
    finally:
        ap.LEDGER_FLOOR_FOR_CAPABILITY.clear()
        ap.LEDGER_FLOOR_FOR_CAPABILITY.update(original)

    # And with no floor above the constructor floor, the call goes through normally.
    calls = []
    ok = StoreBackedAuthority("agent-triage", lambda *a, **k: calls.append(k) or {}, Denied,
                              ledger_version=64, workspace="ws:alpha")
    allowed, _ = ok.permits(newsletter_rule(rule_id="r-int"),
                            {"item_id": "i1", "source_key": "sk_mail"})
    assert allowed is True and calls
    assert calls[0]["workspace"] == "ws:alpha", "v2 passes workspace as a keyword"


def test_the_scope_is_one_flat_exact_string_and_never_a_prefix():
    """R01 matches scopes by exact string equality: no hierarchy, no prefix, no wildcard."""
    from attention.rules.authority_port import scope_for

    assert scope_for({"source_key": "sk_mail"}) == "source/sk_mail"
    assert scope_for({"source": "sk_mail"}) == "source/sk_mail"
    assert scope_for({}) == "source/"
    # Two different sources are two different scopes; nothing here builds a coverable path.
    assert scope_for({"source_key": "gmail"}) != scope_for({"source_key": "gmail/inbox"})


@pytest.mark.parametrize(
    "kind,fragment",
    [
        ("policy", "ask for authority"),
        ("unavailable", "apply it"),
        ("wiring", "change the code"),
        ("repeat", "reconcile rather than re-run"),
        ("something-new", "unrecognised refusal kind"),
        (None, "authority boundary refused"),
    ],
)
def test_every_refusal_kind_still_blocks_and_says_what_to_change(kind, fragment):
    """T04's classification (4c41cf2) adopted, with the constraint that makes it safe.

    The kind enriches the REASON and never reaches the DECISION. A classification that could turn
    into an allow would be a worse bug than the ambiguity it set out to cure, so the parametrisation
    deliberately includes a kind this table has never heard of and the no-kind case: all six block.
    """
    from attention.rules.authority_port import StoreBackedAuthority

    class Denied(Exception):
        def __init__(self, msg, kind=None):
            super().__init__(msg)
            if kind is not None:
                self.kind = kind

    def refusing(*a, **k):
        raise Denied("the boundary refused", kind)

    auth = StoreBackedAuthority("agent-triage", refusing, Denied, ledger_version=64,
                                   workspace="ws:alpha")
    allowed, reason = auth.permits(newsletter_rule(rule_id="r-kind"),
                                   {"item_id": "i1", "source_key": "sk_mail"})

    assert allowed is False, "no refusal kind may become an allow"
    assert fragment in reason


def test_a_wiring_refusal_is_reported_as_wiring_and_not_as_policy(setup):
    """The distinction is the point: a misspelled capability must not read as a missing grant."""
    from attention.rules.authority_port import StoreBackedAuthority

    class Denied(Exception):
        def __init__(self, msg, kind):
            super().__init__(msg)
            self.kind = kind

    def misconfigured(*a, **k):
        raise Denied("capability 'effect.internl' does not exist", "wiring")

    source, registry, _ = setup
    engine = TriageEngine(registry, source,
                          StoreBackedAuthority("agent-triage", misconfigured, Denied,
                                               ledger_version=64, workspace="ws:alpha"))
    activate(source, registry, newsletter_rule())

    receipt = engine.triage(item("i1", category="newsletter"))
    assert receipt.disposition is Disposition.BLOCKED
    assert "[wiring]" in receipt.reason
    assert "change the code" in receipt.reason
    assert "ask for authority" not in receipt.reason
