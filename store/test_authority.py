"""R01 authority boundary, exercised through the API a caller actually has.

The migration's own DO block already watched eleven refusals refuse at the SQL level. This suite
asks the different question: does the PYTHON path -- `store.apply` and `store.authority.check` --
carry those properties to a caller, and does it fail closed on a store that predates migration 57.

Run:
    ENGINE_SCRATCH_DB=brain_scratch_t04 BRAIN_PG_DB=brain_scratch_t04 python3 -m pytest \
        store/test_authority.py -v

It needs a store built by `engine/bin/scratch-db.sh` with migration 57 applied, and it needs
BRAIN_PG_DB pointed at it. It refuses to run against `brain`.
"""

from __future__ import annotations

import datetime as dt
import os
import uuid

import psycopg2
import pytest

import store
from store import authority


pytestmark = pytest.mark.skipif(
    os.environ.get("BRAIN_PG_DB", "brain") == "brain",
    reason="refusing to run against the live store; point BRAIN_PG_DB at a scratch database",
)


# --- migration 63: every authority question is asked inside a workspace -----------------------
WS = "ws-test-authority"


def _check(*a, **kw):
    """`authority.check` with this suite's workspace defaulted. Pass `workspace=` to override."""
    kw.setdefault("workspace", WS)
    return authority.check(*a, **kw)


def _in_force(*a, **kw):
    kw.setdefault("workspace", WS)
    return authority.in_force(*a, **kw)


@pytest.fixture(scope="module")
def who() -> str:
    """A human this store actually knows. Not invented: a forged one is a separate scene below."""
    # ASKED OF THE STORE, not read out of brain.human_role: `brain_runtime` holds no SELECT on
    # the human roster, by design, and a test that needed that privilege would be testing a
    # different store than the one the runtime actually runs against.
    me = store.whoami()
    human = me["human"] if me.get("reachable") and me.get("agrees") else None
    if human is None:
        pytest.fail(f"the store does not agree about who this process is ({me}), so no grant "
                    f"could be issued by anyone. That is an unbuilt fixture, not a passing test.")
    return human


@pytest.fixture
def scope() -> str:
    """A scope unique to this test, so scenes cannot see each other's grants."""
    return f"lane/test-{uuid.uuid4().hex[:12]}"


def _in(**delta):
    """A real timestamp, not a SQL string. A string here would be a text literal in a
    timestamptz column and the failure would look like a schema problem."""
    return dt.datetime.now(dt.timezone.utc) + dt.timedelta(**delta)


def _grant(who, scope, capability="work.execute", **kw):
    return store.apply("authority grant", workspace=WS, granted_by=who, subject=who, subject_kind="human",
                       capability=capability, scope=scope,
                       expires_at=kw.pop("expires_at", _in(hours=1)),
                       evidence="store/test_authority.py", **kw)


def test_this_store_carries_migration_57(who):
    assert authority.tables_present(), (
        "the four authority tables are not all present. Every scene below would then be a verdict "
        "over a store that cannot answer, which is not a pass.")


def test_a_grant_is_in_force_and_check_returns_it(who, scope):
    g = _grant(who, scope)
    assert g["grant_seq"] > 0
    held = _in_force(who, capability="work.execute", scope=scope)
    assert len(held) == 1, f"expected exactly one grant in force for {scope}, got {len(held)}"
    assert _check(who, "work.execute", scope)["grant_seq"] == g["grant_seq"]


def test_a_grant_does_not_leak_into_a_neighbouring_scope(who, scope):
    _grant(who, scope)
    with pytest.raises(authority.Denied) as e:
        _check(who, "work.execute", scope + "-next-door")
    # the message carries the denominator, so a reader is not sent hunting for a bug in the check
    assert "holds no grant in force" in str(e.value)
    assert "grant(s) in force in that workspace" in str(e.value)


def test_a_grant_does_not_leak_into_another_capability(who, scope):
    _grant(who, scope, capability="work.execute")
    with pytest.raises(authority.Denied):
        _check(who, "effect.spend", scope)


def test_an_expired_grant_is_not_in_force(who, scope):
    g = _grant(who, scope, expires_at=_in(seconds=-1))
    assert _in_force(who, scope=scope) == []
    with pytest.raises(authority.Denied):
        _check(who, "work.execute", scope)
    # and the record of it survives: expiry is not deletion
    with store.read() as s:
        assert s.one("SELECT 1 FROM brain.authority_grant WHERE grant_seq = %s",
                     (g["grant_seq"],)) is not None


def test_revoke_between_the_check_and_the_effect(who, scope):
    """The interval R02 has to survive: authorised a moment ago, revoked, then asked again."""
    g = _grant(who, scope)
    assert _check(who, "work.execute", scope)["grant_seq"] == g["grant_seq"]
    store.apply("authority revoke", workspace=WS, grant_seq=g["grant_seq"], revoked_by=who, reason="test")
    with pytest.raises(authority.Denied):
        _check(who, "work.execute", scope)


def test_revoking_twice_is_refused_and_the_first_stands(who, scope):
    g = _grant(who, scope)
    store.apply("authority revoke", workspace=WS, grant_seq=g["grant_seq"], revoked_by=who, reason="first")
    with pytest.raises(authority.AlreadyRevoked) as e:
        store.apply("authority revoke", workspace=WS, grant_seq=g["grant_seq"], revoked_by=who, reason="second")
    assert "already revoked" in str(e.value)
    with store.read() as s:
        assert s.scalar("SELECT count(*) FROM brain.authority_revocation WHERE grant_seq = %s",
                        (g["grant_seq"],)) == 1


def test_a_replayed_approval_is_not_a_second_decision(who, scope, agent):
    g = _grant(who, scope, capability="approval.decide")
    proposal = f"proposal-{uuid.uuid4().hex[:8]}"
    store.apply("approval decide", workspace=WS, proposal_id=proposal, proposal_version="v1",
                decided_by=who, subject=agent, grant_seq=g["grant_seq"])
    with pytest.raises(authority.AlreadyDecided) as e:
        store.apply("approval decide", workspace=WS, proposal_id=proposal, proposal_version="v1",
                    decided_by=who, subject=agent, grant_seq=g["grant_seq"])
    assert "already decided" in str(e.value)
    with store.read() as s:
        assert s.scalar("SELECT count(*) FROM brain.approval WHERE proposal_id = %s",
                        (proposal,)) == 1


def test_approving_v1_does_not_authorise_v2(who, scope, agent):
    """The hole `receipt.approval_ref` left open: an approval that named no version."""
    decider = _grant(who, scope, capability="approval.decide")
    store.apply("test authority grant to", subject=agent, subject_kind="agent", scope=scope,
                capability="work.execute", granted_by=who)
    proposal = f"proposal-{uuid.uuid4().hex[:8]}"
    store.apply("approval decide", workspace=WS, proposal_id=proposal, proposal_version="v1",
                decided_by=who, subject=agent, grant_seq=decider["grant_seq"])
    assert _check(agent, "work.execute", scope, proposal=(proposal, "v1"))
    with pytest.raises(authority.Denied) as e:
        _check(agent, "work.execute", scope, proposal=(proposal, "v2"))
    # message changed at migration 66: the check now asks for a SPENDABLE approval
    assert "no SPENDABLE approval" in str(e.value)


def test_a_rejection_is_not_an_approval(who, scope, agent):
    decider = _grant(who, scope, capability="approval.decide")
    store.apply("test authority grant to", subject=agent, subject_kind="agent", scope=scope,
                capability="work.execute", granted_by=who)
    proposal = f"proposal-{uuid.uuid4().hex[:8]}"
    store.apply("approval decide", workspace=WS, proposal_id=proposal, proposal_version="v1",
                decided_by=who, subject=agent, grant_seq=decider["grant_seq"], decision="reject")
    with pytest.raises(authority.Denied):
        _check(agent, "work.execute", scope, proposal=(proposal, "v1"))


def test_a_forged_actor_cannot_issue_a_grant(who, scope):
    with pytest.raises(psycopg2.errors.RestrictViolation) as e:
        store.apply("authority grant", workspace=WS, granted_by="_nobody_has_ever_heard_of_me",
                    subject=who, subject_kind="human", capability="work.execute", scope=scope,
                    expires_at=_in(hours=1), evidence="test")
    assert "forged-actor" in str(e.value)


def test_an_unknown_subject_cannot_be_given_a_grant(who, scope):
    with pytest.raises(psycopg2.errors.RestrictViolation):
        store.apply("authority grant", workspace=WS, granted_by=who, subject="_no_such_agent",
                    subject_kind="agent", capability="work.execute", scope=scope,
                    expires_at=_in(hours=1), evidence="test")


def test_an_unknown_capability_is_refused_rather_than_silently_useless(who, scope):
    with pytest.raises(psycopg2.errors.ForeignKeyViolation):
        store.apply("authority grant", workspace=WS, granted_by=who, subject=who, subject_kind="human",
                    capability="work.exceute", scope=scope,
                    expires_at=_in(hours=1), evidence="test")


def test_a_perpetual_grant_must_say_why(who, scope):
    with pytest.raises(psycopg2.errors.CheckViolation):
        store.apply("authority grant", workspace=WS, granted_by=who, subject=who, subject_kind="human",
                    capability="work.execute", scope=scope, expires_at=None, evidence="test")
    g = store.apply("authority grant", workspace=WS, granted_by=who, subject=who, subject_kind="human",
                    capability="work.execute", scope=scope, expires_at=None,
                    perpetual_reason="this human owns the store", evidence="test")
    assert _check(who, "work.execute", scope)["grant_seq"] == g["grant_seq"]


# THE PLANTED BYPASS. A transition that tries to edit the record directly, registered here so the
# attempt goes down the same write path a real lane would use. It must not succeed.
@store.transition("test authority planted bypass")
def _planted_bypass(ctx, *, grant_seq: int):
    return ctx.execute("UPDATE brain.authority_grant SET scope = 'widened' WHERE grant_seq = %s",
                       (grant_seq,))


@store.transition("test authority planted delete")
def _planted_delete(ctx, *, grant_seq: int):
    return ctx.execute("DELETE FROM brain.authority_grant WHERE grant_seq = %s", (grant_seq,))


@pytest.mark.parametrize("verb", ["test authority planted bypass", "test authority planted delete"])
def test_a_planted_bypass_is_refused_and_the_record_is_unchanged(who, scope, verb):
    """Watched failing, which is the only way this check means anything.

    TWO layers refuse this and the test asserts the outcome rather than which layer spoke first.
    The runtime role holds no UPDATE and no DELETE, so privilege refuses before the trigger is
    reached; the migration's own DO block watched the trigger refuse the same two verbs as
    superuser, where privilege is not in the way. Asserting on one specific SQLSTATE here would
    make the test fail the day the other layer wins the race, which would be a correct system and
    a red suite.
    """
    g = _grant(who, scope)
    with pytest.raises(psycopg2.Error) as e:
        store.apply(verb, grant_seq=g["grant_seq"])
    assert isinstance(e.value, (psycopg2.errors.InsufficientPrivilege,
                                psycopg2.errors.RestrictViolation)), \
        f"refused, but with {type(e.value).__name__}, which is not a refusal of the right kind"
    with store.read() as s:
        row = s.one("SELECT scope FROM brain.authority_grant WHERE grant_seq = %s",
                    (g["grant_seq"],))
    assert row is not None, "the row was deleted"
    assert row["scope"] == scope, "the row was edited"


def test_a_store_without_migration_57_fails_closed(monkeypatch):
    """docs/SCHEMA-TOLERANCE.md rule 5: this code runs on a store that has none of these tables.

    It runs, and it DENIES. An absent authority table is not "no restrictions"; it is a store that
    cannot say whether an act was authorised, and the safe reading of cannot-say is no.
    """
    pre57 = os.environ.get("BRAIN_PG_DB_PRE57", "brain_scratch_t04_pre57")
    monkeypatch.setenv("BRAIN_PG_DB", pre57)
    try:
        with store.read() as s:
            version = s.scalar("SELECT max(version) FROM brain.schema_migration")
    except psycopg2.Error as exc:
        pytest.skip(f"no pre-57 store at {pre57!r} to read ({exc.__class__.__name__}); this scene "
                    f"did NOT run and must not be counted as a pass")
    assert version is not None and version < 57, (
        f"{pre57!r} is at schema version {version}, so it is not a pre-57 store and this scene "
        f"would prove nothing")
    assert authority.tables_present() is False
    assert _in_force("anyone") == []
    with pytest.raises(authority.Denied) as e:
        _check("anyone", "work.execute", "anywhere")
    assert "predates migration 57" in str(e.value)


# ---------------------------------------------------------------- CAP14-REV-017, the approval path
#
# Every scene below was written to FAIL against the code at f059037 and was watched failing before
# migration 61 existed. They are the repair's evidence, not decoration.
#
# THE SUBJECT IS AN AGENT AND THE DECIDER IS A HUMAN, which is the realistic shape and also the only
# one the runtime can build: `brain_runtime` may INSERT into `brain.agent` and may not even SELECT
# `brain.human_role`, because the roster of humans is not the runtime's business. A fixture that
# needed to write the human roster would be asking for a privilege the product should never grant.


@store.transition("test authority make agent")
def _make_agent(ctx, *, name: str):
    return dict(ctx.one(
        "INSERT INTO brain.agent (name, role, status, host, updated) "
        " VALUES (%s, 'worker', 'idle', 'test', now()) "
        " ON CONFLICT (name) DO UPDATE SET updated = now() RETURNING name", (name,)))


@store.transition("test authority grant to")
def _grant_to(ctx, *, subject: str, subject_kind: str, scope: str, capability: str,
              granted_by: str):
    return dict(ctx.one(
        "INSERT INTO brain.authority_grant (granted_by, workspace, subject_kind, subject, "
        " capability, scope, expires_at, evidence) "
        " VALUES (%s, %s, %s, %s, %s, %s, now() + interval %s, %s) RETURNING *",
        (granted_by, WS, subject_kind, subject, capability, scope, "1 hour",
         "store/test_authority.py")))


@pytest.fixture
def agent() -> str:
    name = f"mv61-agent-{uuid.uuid4().hex[:8]}"
    store.apply("test authority make agent", name=name)
    return name


def test_a_subject_cannot_approve_its_own_proposal(who, scope):
    """The one that matters. An actor that can authorise itself is not gated by anything.

    Watched failing at f059037: the approval row was written and `check` accepted it, on a grant for
    `effect.external` that has nothing to do with approving.
    """
    g = _grant(who, scope, capability="approval.decide")
    proposal = f"self-{uuid.uuid4().hex[:8]}"
    with pytest.raises(psycopg2.errors.RestrictViolation) as e:
        store.apply("approval decide", workspace=WS, proposal_id=proposal, proposal_version="v1",
                    decided_by=who, subject=who, grant_seq=g["grant_seq"])
    assert "own" in str(e.value).lower() or "itself" in str(e.value).lower(), str(e.value)


def test_an_approver_must_hold_approval_decide(who, scope, agent):
    """The grant an approval names must be the approver's, and must be the right capability.

    Watched failing at f059037: `grant_seq` was a foreign key to ANY grant, so any actor holding any
    grant at all could mint an approval.
    """
    wrong = _grant(who, scope, capability="effect.external")
    proposal = f"cap-{uuid.uuid4().hex[:8]}"
    with pytest.raises(psycopg2.errors.RestrictViolation) as e:
        store.apply("approval decide", workspace=WS, proposal_id=proposal, proposal_version="v1",
                    decided_by=who, subject=agent, grant_seq=wrong["grant_seq"])
    assert "approval.decide" in str(e.value), str(e.value)


def test_an_approval_for_one_subject_does_not_authorise_another(who, scope, agent):
    """Watched failing at f059037: `check` matched on (proposal, version) and ignored the subject."""
    decider = _grant(who, scope, capability="approval.decide")
    _grant(who, scope, capability="effect.external")
    proposal = f"sub-{uuid.uuid4().hex[:8]}"
    store.apply("approval decide", workspace=WS, proposal_id=proposal, proposal_version="v1",
                decided_by=who, subject=agent, grant_seq=decider["grant_seq"])
    with pytest.raises(authority.Denied) as e:
        _check(who, "effect.external", scope, proposal=(proposal, "v1"))
    assert "another subject" in str(e.value) or "no approval" in str(e.value), str(e.value)


def test_a_proper_approval_still_works(who, scope, agent):
    """The positive control, without which the three refusals above are equally well explained by an
    approval path that now refuses everything."""
    decider = _grant(who, scope, capability="approval.decide")
    store.apply("test authority grant to", subject=agent, subject_kind="agent", scope=scope,
                capability="effect.external", granted_by=who)
    proposal = f"ok-{uuid.uuid4().hex[:8]}"
    store.apply("approval decide", workspace=WS, proposal_id=proposal, proposal_version="v1",
                decided_by=who, subject=agent, grant_seq=decider["grant_seq"])
    assert _check(agent, "effect.external", scope, proposal=(proposal, "v1"))


# ---------------------------------------------------------------- a refusal must say which kind
#
# A fail-closed system hides its own misconfiguration. Every scene below REFUSES -- that is not in
# question and is asserted first -- and each refuses for a different reason, which the caller can
# tell apart without reading the message. Terminal 03 supplied the motivating case: its rule engine
# treats any exception as a refusal, so an invented capability name would have surfaced as a
# mysterious policy deny rather than as the wiring bug it was.


def test_a_planted_wiring_error_is_reported_as_wiring_and_not_as_denial(who, scope):
    """The planted defect: a capability that does not exist. THE point of this whole family."""
    _grant(who, scope, capability="effect.external")
    with pytest.raises(authority.Denied) as e:          # it refuses, first and always
        _check(who, "effect.exteRnal", scope)  # one letter wrong
    assert isinstance(e.value, authority.Misconfigured), (
        f"a misspelled capability came back as {type(e.value).__name__}. A wiring error that looks "
        f"like a policy denial sends somebody to argue with the rules instead of fixing the typo.")
    assert e.value.kind == "wiring"
    assert not isinstance(e.value, authority.PolicyDenied)
    assert "wiring error, not a" in str(e.value) and "Known:" in str(e.value)


def test_a_real_denial_is_reported_as_policy(who, scope):
    """The control. Without it, "wiring" could be what this code says about everything."""
    with pytest.raises(authority.PolicyDenied) as e:
        _check(who, "effect.external", scope + "-never-granted")
    assert e.value.kind == "policy"
    assert not isinstance(e.value, authority.Misconfigured)
    assert isinstance(e.value, authority.Denied)


def test_a_store_that_cannot_answer_says_so_rather_than_denying(monkeypatch):
    """Third kind: no policy is involved at all, and the remedy is a migration."""
    pre = os.environ.get("BRAIN_PG_DB_PRE57", "brain_scratch_t04_pre57")
    monkeypatch.setenv("BRAIN_PG_DB", pre)
    try:
        with store.read() as s:
            version = s.scalar("SELECT max(version) FROM brain.schema_migration")
    except psycopg2.Error as exc:
        pytest.skip(f"no pre-57 store at {pre!r} ({exc.__class__.__name__}); this scene did NOT run")
    assert version is not None and version < 57
    with pytest.raises(authority.StoreCannotAnswer) as e:
        _check("anyone", "effect.external", "anywhere")
    assert e.value.kind == "unavailable"
    assert not isinstance(e.value, authority.PolicyDenied)
    # and it is still a refusal, which is the property that must not have been traded away
    assert isinstance(e.value, authority.Denied)


def test_all_three_kinds_are_distinct_and_all_three_refuse():
    """The whole rule in one assertion: distinguishable, and none of them is an allow."""
    kinds = {authority.PolicyDenied.kind, authority.StoreCannotAnswer.kind,
             authority.Misconfigured.kind}
    assert len(kinds) == 3, f"two kinds share a label: {kinds}"
    for cls in (authority.PolicyDenied, authority.StoreCannotAnswer, authority.Misconfigured):
        assert issubclass(cls, authority.Denied), f"{cls.__name__} is not a refusal"


# ---------------------------------------------------------------- migration 63, at the port
#
# The migration proves the boundary at the storage layer. These prove it where consumers meet it,
# which is the only place a consumer can be wrong about it.


def test_a_grant_in_one_workspace_does_not_satisfy_a_check_in_another(who, scope):
    """Identical subject, identical capability, identical scope string. Different workspace.

    This is the whole of migration 63 in one scene, and the reason `workspace` is a column and an
    argument rather than a piece of the scope string: the string a consumer builds is unchanged and
    cannot drift, and the dimension that separates two customers is compared in exactly one place.
    """
    other_ws = "ws-next-door"
    store.apply("authority grant", workspace=other_ws, granted_by=who, subject=who,
                subject_kind="human", capability="effect.external", scope=scope,
                expires_at=_in(hours=1), evidence="store/test_authority.py")

    # in its own workspace it is in force
    assert authority.check(who, "effect.external", scope, workspace=other_ws)["scope"] == scope
    # and next door it does not exist
    with pytest.raises(authority.PolicyDenied) as e:
        authority.check(who, "effect.external", scope, workspace=WS)
    # the message must name the neighbour, or this is the most confusing denial in the system:
    # everything the reader is looking at appears correct
    assert "in ANOTHER" in str(e.value) and other_ws not in str(e.value).split("holds")[0]
    assert "1 grant(s) matching this exact capability and scope in ANOTHER workspace" in str(e.value)


def test_in_force_is_scoped_too(who, scope):
    other_ws = "ws-next-door-2"
    store.apply("authority grant", workspace=other_ws, granted_by=who, subject=who,
                subject_kind="human", capability="effect.external", scope=scope,
                expires_at=_in(hours=1), evidence="store/test_authority.py")
    assert len(authority.in_force(who, workspace=other_ws, scope=scope)) == 1
    assert authority.in_force(who, workspace=WS, scope=scope) == []


def test_a_workspaceless_grant_can_no_longer_be_created(who, scope):
    """The pre-63 shape is not creatable any more, and that is the NOT VALID constraint working.

    I wrote this scene intending to PLANT a NULL-workspace row and prove `check` ignores it. Two
    things stopped that and both are the system being right. The constraint refuses the insert,
    because NOT VALID tolerates rows that were already there and enforces the rule on every new one.
    And standing the constraint down needs ALTER TABLE, which `brain_runtime` does not hold and
    should not.

    So the honest scene is this one. The NULL-matching behaviour itself is proven where it can be:
    migration 63's own check 8 watches a workspace-less grant refused at the storage boundary, and
    `check` filters with `workspace = %s`, which SQL never makes true against NULL. What is asserted
    here is the property a consumer can actually reach.
    """
    with pytest.raises(psycopg2.errors.CheckViolation) as e:
        store.apply("test authority grant with no workspace", who=who, scope=scope)
    assert "names_its_workspace" in str(e.value)


@store.transition("test authority grant with no workspace")
def _grant_no_ws(ctx, *, who: str, scope: str):
    """Attempts the pre-63 shape. The constraint refuses it, which is what the scene asserts."""
    return dict(ctx.one(
        "INSERT INTO brain.authority_grant (granted_by, subject_kind, subject, capability, scope, "
        " expires_at, evidence) VALUES (%s, 'human', %s, 'effect.external', %s, "
        " now() + interval '1 hour', 'pre-63 shape') RETURNING *", (who, who, scope)))
