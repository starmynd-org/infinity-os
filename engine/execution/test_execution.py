"""R02: leases, fencing, cancellation and honest outcomes, through the API a lane actually calls.

The two migrations watched their own refusals refuse at the SQL level. This asks the other
question: does the Python path carry those properties to a caller, and does the authority check
land in the right place -- inside the reserving transaction, late enough that a revocation one
second old still stops the effect.

The six scenes the packet names are all here: two workers claiming one item, lease expiry and
fencing, a revoke landing between the lease and the effect, budget exhaustion, a process that
crashed mid-effect, and an ambiguous external result.

Run:
    BRAIN_PG_DB=brain_scratch_t04 python3 -m pytest engine/execution/test_execution.py -v

Every scene uses ids and keys unique to itself, so nothing here asserts a global count and two
runs against one store cannot interfere. It refuses to run against `brain`.
"""

from __future__ import annotations

import os
import pathlib
import re
import sys
import uuid

import psycopg2
import pytest

# `engine/` is what `engine/bin/swarm` puts on the path, so `execution` is importable the same way
# `swarm_engine` is. Done here rather than in a conftest because this directory has no other test.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import store
from store import schema                                                            # noqa: E402
from store import authority                                             # noqa: E402
from execution import coordinator as ex                                 # noqa: E402


pytestmark = pytest.mark.skipif(
    os.environ.get("BRAIN_PG_DB", "brain") == "brain",
    reason="refusing to run against the live store; point BRAIN_PG_DB at a scratch database",
)


# --- migration 63: every authority question is asked inside a workspace -----------------------
WS = "ws-test-execution"


def _check(*a, **kw):
    """`authority.check` with this suite's workspace defaulted. Pass `workspace=` to override."""
    kw.setdefault("workspace", WS)
    return authority.check(*a, **kw)


def _in_force(*a, **kw):
    kw.setdefault("workspace", WS)
    return authority.in_force(*a, **kw)


@pytest.fixture(scope="module")
def who() -> str:
    me = store.whoami()
    if not (me.get("reachable") and me.get("agrees")):
        pytest.fail(f"the store does not agree about who this process is ({me}); no grant could "
                    f"be issued, so every scene below would be a verdict over an unbuilt fixture")
    return me["human"]


@store.transition("test execution make work item")
def _make_item(ctx, *, title: str):
    return dict(ctx.one("INSERT INTO brain.work_item (title, lane) VALUES (%s, 'mv-test') "
                        "RETURNING id", (title,)))


@store.transition("test execution plant expired lease")
def _plant_expired_lease(ctx, *, work_item_id: str, holder: str):
    """A lease that was already over when it was written.

    The clock cannot be moved and sleeping through a real expiry would make the suite slow and
    flaky, so the expiry is planted directly. `expires_at > acquired_at` is a CHECK, so both are
    backdated together rather than writing a row the schema would refuse.
    """
    return dict(ctx.one(
        "INSERT INTO brain.execution_lease "
        " (work_item_id, workspace, holder, acquired_at, expires_at, lease_epoch) "
        " VALUES (%s, %s, %s, now() - interval '2 hours', now() - interval '1 hour', 0) "
        " RETURNING *", (work_item_id, WS, holder)))


@pytest.fixture
def item(who) -> str:
    return _grant_free_item()


def _grant_free_item() -> str:
    return store.apply("test execution make work item",
                       title=f"R02 scene {uuid.uuid4().hex[:8]}")["id"]


def _grant(who, scope, capability="effect.external", hours=1):
    """A grant in force for this scene only. Scopes are unique per scene, so grants cannot leak."""
    import datetime as dt
    return store.apply("authority grant", workspace=WS, granted_by=who, subject=who, subject_kind="human",
                       capability=capability, scope=scope,
                       expires_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=hours),
                       evidence="engine/execution/test_execution.py")


@pytest.fixture
def scope() -> str:
    return f"lane/r02-{uuid.uuid4().hex[:12]}"


@pytest.fixture
def key() -> str:
    return f"r02-{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------- the six scenes the packet names


def test_two_workers_claim_one_item(item):
    """Only one lease. The refusal comes from the index, not from the application being careful."""
    first = ex.acquire(workspace=WS, work_item_id=item, holder="worker-a", seconds=300)
    assert first["lease_epoch"] > 0
    with pytest.raises(ex.LeaseHeld) as e:
        ex.acquire(workspace=WS, work_item_id=item, holder="worker-b", seconds=300)
    assert "worker-a" in str(e.value)
    live = ex.live_lease(item)
    assert live["lease_id"] == first["lease_id"] and live["holder"] == "worker-a"


def test_an_expired_lease_is_not_live_and_still_blocks(item):
    """Expiry removes the right to act. It does NOT quietly free the item.

    Both halves matter. If expiry freed the item, a worker that stopped without saying so would
    leave no trace, and the most common failure would be the one hardest to see afterwards.
    """
    planted = store.apply("test execution plant expired lease",
                          work_item_id=item, holder="worker-gone")
    assert ex.live_lease(item) is None, "an expired lease was still live"
    with pytest.raises(ex.LeaseHeld) as e:
        ex.acquire(workspace=WS, work_item_id=item, holder="worker-b", seconds=300)
    assert "EXPIRED" in str(e.value) and "release it" in str(e.value).lower()
    # taking over is an act, and it leaves a row saying who did it
    ex.release(lease_id=planted["lease_id"], reason="worker-gone stopped responding")
    took_over = ex.acquire(workspace=WS, work_item_id=item, holder="worker-b", seconds=300)
    assert took_over["lease_epoch"] > planted["lease_epoch"]
    with store.read() as s:
        row = s.one("SELECT release_reason FROM brain.execution_lease WHERE lease_id = %s",
                    (planted["lease_id"],))
    assert row["release_reason"] == "worker-gone stopped responding"


def test_a_stale_lease_epoch_cannot_reserve(who, item, scope, key):
    """The slow worker. It still believes it holds the lease; the store knows better."""
    old = ex.acquire(workspace=WS, work_item_id=item, holder="worker-a", seconds=300)
    _grant(who, scope)
    ex.release(lease_id=old["lease_id"], reason="taken over")
    new = ex.acquire(workspace=WS, work_item_id=item, holder="worker-b", seconds=300)
    assert new["lease_epoch"] > old["lease_epoch"]
    # worker-a wakes up and acts, with a key nobody has used and a lease it no longer holds
    with pytest.raises(psycopg2.errors.RestrictViolation) as e:
        ex.reserve(workspace=WS, idempotency_key=key, lease_id=old["lease_id"],
                   lease_epoch=old["lease_epoch"], description="act on a world that moved",
                   subject=who, capability="effect.external", scope=scope)
    assert "not live" in str(e.value)
    # and the new holder can, so the refusal was about the fence and not about the item
    ok = ex.reserve(workspace=WS, idempotency_key=key, lease_id=new["lease_id"],
                    lease_epoch=new["lease_epoch"], description="the new holder acts",
                    subject=who, capability="effect.external", scope=scope)
    assert ok["attempt_seq"] > 0


def test_a_revoke_between_the_lease_and_the_effect_stops_the_effect(who, item, scope, key):
    """The scene R01 and R02 exist together for.

    The lease is still live and the token is still current, so nothing about execution has changed.
    What changed is the answer to "may this happen", and it changed after the work started.
    """
    lease = ex.acquire(workspace=WS, work_item_id=item, holder="worker-a", seconds=300)
    grant = _grant(who, scope)
    # the effect is authorised at this instant
    assert _check(who, "effect.external", scope)["grant_seq"] == grant["grant_seq"]
    store.apply("authority revoke", workspace=WS, grant_seq=grant["grant_seq"], revoked_by=who,
                reason="the customer withdrew consent")
    with pytest.raises(authority.Denied):
        ex.reserve(workspace=WS, idempotency_key=key, lease_id=lease["lease_id"],
                   lease_epoch=lease["lease_epoch"], description="send the thing",
                   subject=who, capability="effect.external", scope=scope)
    # NOTHING WAS WRITTEN. A refused effect that still left a reservation would be a reservation
    # nobody can settle, and it would sit in the unresolved queue forever looking like a crash.
    with store.read() as s:
        assert s.one("SELECT 1 FROM brain.effect_attempt WHERE idempotency_key = %s",
                     (key,)) is None
    assert ex.live_lease(item)["lease_id"] == lease["lease_id"], "the lease was collateral damage"


def test_an_open_budget_stop_refuses_the_reservation(who, item, scope, key):
    """Budget exhaustion, consulted BEFORE the effect rather than discovered from the charge."""
    lease = ex.acquire(workspace=WS, work_item_id=item, holder="worker-a", seconds=300)
    _grant(who, scope, capability="effect.spend")
    budget_scope_id = f"r02-budget-{uuid.uuid4().hex[:8]}"
    # `lane`, not the first label in the enum. `fleet` is a whole-fleet stop and
    # `budget_incident_check2` requires its scope_id to be empty, which is a different scene: this
    # one is about a stop that covers ONE scope while everything else keeps running.
    scope_type = "lane"
    with store.read() as s:
        known = s.scalar(
            "SELECT count(*) FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
            " WHERE t.typname = 'budget_scope' AND e.enumlabel = %s", (scope_type,))
    if not known:
        pytest.fail(f"brain.budget_scope has no {scope_type!r} label in this store, so the budget "
                    f"hook could not be exercised. That is an unbuilt fixture, not a pass.")

    # with no stop open, the reservation lands
    first = ex.reserve(workspace=WS, idempotency_key=key, lease_id=lease["lease_id"],
                       lease_epoch=lease["lease_epoch"], description="spend a little",
                       subject=who, capability="effect.spend", scope=scope,
                       budget_scope_type=scope_type, budget_scope_id=budget_scope_id)
    assert first["attempt_seq"] > 0

    store.apply("test execution open a budget stop",
                scope_type=scope_type, scope_id=budget_scope_id)
    with pytest.raises(psycopg2.errors.RestrictViolation) as e:
        ex.reserve(workspace=WS, idempotency_key=key + "-2", lease_id=lease["lease_id"],
                   lease_epoch=lease["lease_epoch"], description="spend more",
                   subject=who, capability="effect.spend", scope=scope,
                   budget_scope_type=scope_type, budget_scope_id=budget_scope_id)
    assert "budget stop" in str(e.value)
    # an effect that names no budget scope is unaffected: the stop is about spending, not about
    # everything the worker might do
    unrelated = ex.reserve(workspace=WS, idempotency_key=key + "-3", lease_id=lease["lease_id"],
                           lease_epoch=lease["lease_epoch"], description="write a file",
                           subject=who, capability="effect.spend", scope=scope)
    assert unrelated["attempt_seq"] > 0


@store.transition("test execution open a budget stop")
def _open_a_stop(ctx, *, scope_type: str, scope_id: str):
    return dict(ctx.one(
        "INSERT INTO brain.budget_incident (kind, scope_type, scope_id, cause, detected_by) "
        " VALUES ('manual_stop', %s::brain.budget_scope, %s, 'budget', 'charge') RETURNING id",
        (scope_type, scope_id)))


def test_a_process_that_crashed_mid_effect(who, item, scope, key):
    """The reservation outlives the process, which is the whole point of writing it first.

    Nothing here simulates a crash by mocking. The crash IS the absence of a settle: the row was
    written, the process never came back, and what the store holds afterwards is exactly what it
    would hold in the real case.
    """
    lease = ex.acquire(workspace=WS, work_item_id=item, holder="worker-a", seconds=300)
    _grant(who, scope)
    attempt = ex.reserve(workspace=WS, idempotency_key=key, lease_id=lease["lease_id"],
                         lease_epoch=lease["lease_epoch"], description="send the thing",
                         subject=who, capability="effect.external", scope=scope)
    # ... the process dies here ...
    rows = {r["attempt_seq"]: r for r in ex.unresolved()}
    assert attempt["attempt_seq"] in rows
    assert rows[attempt["attempt_seq"]]["why"] == "reserved-never-settled"

    # THE RETRY. A new process picks the work up, derives the same key from the same work, and is
    # told what the first attempt knew rather than being allowed to do it again.
    new_lease = _retake(item, lease)
    with pytest.raises(ex.AlreadyAttempted) as e:
        ex.reserve(workspace=WS, idempotency_key=key, lease_id=new_lease["lease_id"],
                   lease_epoch=new_lease["lease_epoch"], description="send the thing",
                   subject=who, capability="effect.external", scope=scope)
    # SINCE MIGRATION 62 THE MESSAGE SAYS `halted`, NOT `NOT YET SETTLED`, and that is an
    # improvement rather than a drift. Retaking the lease releases it, and a release now settles the
    # previous holder's in-flight attempts as `halted` -- the worker was stopped and whether its
    # effect landed is unknown. So the retrying process is told something strictly more useful than
    # "nobody has settled this": it is told that the attempt was interrupted and is waiting on a
    # human. The refusal, which is the property under test, is unchanged.
    assert "halted" in str(e.value) and "do not re-execute" in str(e.value)
    with store.read() as s:
        assert s.scalar("SELECT count(*) FROM brain.effect_attempt WHERE idempotency_key = %s",
                        (key,)) == 1, "the retry created a second effect"
    assert attempt["attempt_seq"] in {r["attempt_seq"] for r in ex.unresolved()},         "a halted effect must stay in the queue a human works"


def _retake(item, lease):
    ex.release(lease_id=lease["lease_id"], reason="the holder stopped responding")
    return ex.acquire(workspace=WS, work_item_id=item, holder="worker-b", seconds=300)


def test_an_ambiguous_external_result_never_reports_complete(who, item, scope, key):
    """The connection dropped after the request and before any response.

    Ambiguous is not a polite failed. A failed attempt may be retried; an ambiguous one may not be,
    until somebody has looked, and the store is what enforces the difference.
    """
    lease = ex.acquire(workspace=WS, work_item_id=item, holder="worker-a", seconds=300)
    _grant(who, scope)
    attempt = ex.reserve(workspace=WS, idempotency_key=key, lease_id=lease["lease_id"],
                         lease_epoch=lease["lease_epoch"], description="charge the card",
                         subject=who, capability="effect.external", scope=scope)

    # a success that names nothing is refused
    with pytest.raises(psycopg2.errors.CheckViolation):
        ex.settle(attempt_seq=attempt["attempt_seq"], settled_by="worker-a",
                  settle_secret=lease["settle_secret"], outcome="success",
                  completeness="complete", effect_state="applied")
    # an ambiguity that says nothing is refused
    with pytest.raises(psycopg2.errors.CheckViolation):
        ex.settle(attempt_seq=attempt["attempt_seq"], settled_by="worker-a",
                  settle_secret=lease["settle_secret"], outcome="ambiguous",
                  completeness="uncertain", effect_state="unknown")

    settled = ex.settle(attempt_seq=attempt["attempt_seq"], settled_by="worker-a",
                  settle_secret=lease["settle_secret"], outcome="ambiguous",
                        completeness="uncertain", effect_state="unknown",
                        detail="connection dropped after the request, before any response")
    assert settled["outcome"] == "ambiguous"
    assert settled["completeness"] == "uncertain" and settled["effect_state"] == "unknown"
    assert attempt["attempt_seq"] in {r["attempt_seq"] for r in ex.unresolved()}

    # it cannot be upgraded to a success by a later opinion
    with pytest.raises(ex.AmbiguousOutcome) as e:
        ex.settle(attempt_seq=attempt["attempt_seq"], settled_by="worker-a",
                  settle_secret=lease["settle_secret"], outcome="success",
                  completeness="complete", effect_state="applied", result_ref="guess")
    assert "reconciliation" in str(e.value)

    # somebody looks and cannot tell: that is information, and it is NOT resolution
    ex.reconcile(attempt_seq=attempt["attempt_seq"], found_by="an operator",
                 finding="still-unknown", evidence="the provider console shows nothing either way")
    assert attempt["attempt_seq"] in {r["attempt_seq"] for r in ex.unresolved()}, \
        "still-unknown was treated as closure"

    # somebody looks again and finds out. The attempt still says what was true at the time.
    ex.reconcile(attempt_seq=attempt["attempt_seq"], found_by="an operator",
                 finding="effect-happened", evidence="provider ledger shows exactly one charge")
    assert attempt["attempt_seq"] not in {r["attempt_seq"] for r in ex.unresolved()}
    with store.read() as s:
        assert s.one("SELECT outcome FROM brain.effect_attempt WHERE attempt_seq = %s",
                     (attempt["attempt_seq"],))["outcome"] == "ambiguous"


def test_a_store_without_the_execution_tables_fails_closed(monkeypatch):
    """docs/SCHEMA-TOLERANCE.md rule 5, for this half of the runtime."""
    pre = os.environ.get("BRAIN_PG_DB_PRE57", "brain_scratch_t04_pre57")
    monkeypatch.setenv("BRAIN_PG_DB", pre)
    try:
        with store.read() as s:
            version = s.scalar("SELECT max(version) FROM brain.schema_migration")
    except psycopg2.Error as exc:
        pytest.skip(f"no pre-migration store at {pre!r} ({exc.__class__.__name__}); this scene did "
                    f"NOT run and must not be counted as a pass")
    assert version is not None and version < 58
    assert ex.live_lease("anything") is None
    assert ex.unresolved() == []
    with pytest.raises(ex.LeaseUnavailable) as e:
        ex.acquire(workspace=WS, work_item_id="anything", holder="worker-a")
    assert e.value.kind == "unavailable", "a store that cannot answer must not look like a policy no"
    assert "predates migrations 58 and 59" in str(e.value)


def test_knowing_the_holders_name_is_not_enough_to_settle(who, item, scope, key):
    """Migration 64: the impersonation path migration 62 left open, closed and watched closing.

    Under 62 the guard was `settled_by == the lease holder's name`, and that name is a plain string
    in a table any runtime process can read. A guard you defeat by copying a value out of the row it
    guards is a speed bump. The secret is minted by the server, only its digest is stored, and the
    plaintext is handed to the acquiring transaction once.
    """
    lease = ex.acquire(workspace=WS, work_item_id=item, holder="the-real-worker", seconds=300)
    _grant(who, scope)
    attempt = ex.reserve(workspace=WS, idempotency_key=key, lease_id=lease["lease_id"],
                         lease_epoch=lease["lease_epoch"], description="charge the card",
                         subject=who, capability="effect.external", scope=scope)

    assert lease["settle_secret"], "acquire returned no settle secret"

    # AN IMPERSONATOR CAN READ EVERYTHING THE TABLE HOLDS. That is the premise, not a shortcut:
    # the holder's name and the digest are both readable, and neither is enough.
    with store.read() as s:
        row = s.one("SELECT holder, settle_secret_sha256 FROM brain.execution_lease "
                    " WHERE lease_id = %s", (lease["lease_id"],))
    assert row["holder"] == "the-real-worker"
    assert row["settle_secret_sha256"] and row["settle_secret_sha256"] != lease["settle_secret"], \
        "the plaintext secret is readable from the table, which defeats the whole design"

    # the name alone: refused
    with pytest.raises(psycopg2.errors.RestrictViolation) as e:
        ex.settle(attempt_seq=attempt["attempt_seq"], outcome="success",
                  completeness="complete", effect_state="applied",
                  result_ref="invented", settled_by="the-real-worker")
    assert "none was presented" in str(e.value)

    # the digest, which anyone can read: refused
    with pytest.raises(psycopg2.errors.RestrictViolation):
        ex.settle(attempt_seq=attempt["attempt_seq"], outcome="success",
                  completeness="complete", effect_state="applied", result_ref="invented",
                  settled_by="the-real-worker", settle_secret=row["settle_secret_sha256"])

    # the real secret, from a process that does not even use the holder's name: accepted, because
    # the secret is the proof and the name is only the label on the record
    settled = ex.settle(attempt_seq=attempt["attempt_seq"], outcome="success",
                        completeness="complete", effect_state="applied",
                        result_ref="provider-receipt", settled_by="a-differently-named-process",
                        settle_secret=lease["settle_secret"])
    assert settled["outcome"] == "success"
    assert settled["settled_by"] == "a-differently-named-process"


def test_one_approval_cannot_authorise_two_effects(who, item, scope, key):
    """Migration 66, and the defect it closes, at the port.

    Before 66 this scene passed twice. `check` found a recorded decision, left it there, and the
    same approval satisfied the next effect that named it: one human approving one payment
    authorised every payment naming that proposal version. The unique constraint from migration 57
    stopped a decision being RECORDED twice and never stopped one being SPENT twice, and I described
    it at the time as closing the replay hole. It did not.
    """
    agent = f"spend-{uuid.uuid4().hex[:8]}"
    store.apply("test execution make agent", name=agent)
    decider = _grant(who, scope, capability="approval.decide")
    store.apply("test execution grant to", subject=agent, scope=scope, granted_by=who)
    proposal = f"spend-{uuid.uuid4().hex[:8]}"
    store.apply("approval decide", workspace=WS, proposal_id=proposal,
                proposal_version="v1", decided_by=who, subject=agent,
                grant_seq=decider["grant_seq"])

    lease = ex.acquire(work_item_id=item, workspace=WS, holder="worker-a", seconds=300)

    # the first effect spends it
    first = ex.reserve(idempotency_key=key, lease_id=lease["lease_id"],
                       lease_epoch=lease["lease_epoch"], description="the approved payment",
                       workspace=WS, subject=agent, capability="effect.external", scope=scope,
                       proposal=(proposal, "v1"))
    assert first["attempt_seq"] > 0

    # and the approval is now spent, naming the attempt that spent it
    with store.read() as s:
        row = s.one("SELECT consumed_state, consumed_by FROM brain.approval "
                    " WHERE proposal_id = %s", (proposal,))
    assert row["consumed_state"] == "consumed"
    assert row["consumed_by"] == f"attempt:{first['attempt_seq']}", (
        "a spent approval must name WHAT spent it, or the audit trail stops at 'it was used'")

    # THE SECOND EFFECT IS REFUSED. Different key, same approval.
    with pytest.raises(authority.PolicyDenied) as e:
        ex.reserve(idempotency_key=key + "-again", lease_id=lease["lease_id"],
                   lease_epoch=lease["lease_epoch"], description="the same payment, again",
                   workspace=WS, subject=agent, capability="effect.external", scope=scope,
                   proposal=(proposal, "v1"))
    assert "SPENDABLE" in str(e.value) and "already spent" in str(e.value)

    # and nothing was written for it: a refused effect leaves no reservation behind
    with store.read() as s:
        assert s.one("SELECT 1 FROM brain.effect_attempt WHERE idempotency_key = %s",
                     (key + "-again",)) is None


def test_an_outcome_and_a_completeness_are_two_questions(who, item, scope, key):
    """Migration 67, through the API a caller actually uses.

    The scene that matters is the one the store could not express before: an effect that FAILED and
    about which the worker is UNCERTAIN. Under the old vocabulary the worker had to choose between
    two false rows -- `failed`, claiming a certainty it did not have, or `ambiguous`, withdrawing
    the claim that it failed. This test writes the true row and then proves it reaches a human.

    RC-I2 is the other half: `success` over an effect that is not `applied` is refused. The refusal
    that matters in practice is not success-over-not_applied, which nobody writes on purpose, but
    success over an effect whose state was never established.
    """
    lease = ex.acquire(workspace=WS, work_item_id=item, holder="worker-a", seconds=300)
    _grant(who, scope)
    attempt = ex.reserve(workspace=WS, idempotency_key=key, lease_id=lease["lease_id"],
                         lease_epoch=lease["lease_epoch"], description="charge the card",
                         subject=who, capability="effect.external", scope=scope)
    seq = attempt["attempt_seq"]

    # RC-I2: success over an effect whose state is unknown. This is the whole invariant.
    with pytest.raises(psycopg2.errors.CheckViolation):
        ex.settle(attempt_seq=seq, outcome="success", completeness="complete",
                  effect_state="unknown", result_ref="provider-1", settled_by="worker-a",
                  settle_secret=lease["settle_secret"])

    # RC-I2 again, in the form the contract's own negative fixture states it.
    with pytest.raises(psycopg2.errors.CheckViolation):
        ex.settle(attempt_seq=seq, outcome="success", completeness="complete",
                  effect_state="not_applied", result_ref="provider-1", settled_by="worker-a",
                  settle_secret=lease["settle_secret"])

    # a success that admits it is only partial contradicts itself
    with pytest.raises(psycopg2.errors.CheckViolation):
        ex.settle(attempt_seq=seq, outcome="success", completeness="partial",
                  effect_state="applied", result_ref="provider-1", settled_by="worker-a",
                  settle_secret=lease["settle_secret"])

    # the old spelling gets a sentence rather than a driver-level constraint error
    with pytest.raises(ex.AmbiguousOutcome) as e:
        ex.settle(attempt_seq=seq, outcome="succeeded", completeness="complete",
                  effect_state="applied", result_ref="provider-1", settled_by="worker-a",
                  settle_secret=lease["settle_secret"])
    assert "renamed to 'success'" in str(e.value)

    # AND HERE IS THE ROW THAT COULD NOT BE WRITTEN BEFORE MIGRATION 67.
    settled = ex.settle(attempt_seq=seq, outcome="failed", completeness="uncertain",
                        effect_state="unknown", settled_by="worker-a",
                        settle_secret=lease["settle_secret"],
                        detail="the provider timed out after the charge was submitted")
    assert settled["outcome"] == "failed"
    assert settled["completeness"] == "uncertain"
    assert settled["effect_state"] == "unknown"

    # saying it is only worth something if a human is asked. Under the old view predicate, keyed on
    # `outcome IN ('ambiguous', 'halted')`, a `failed` outcome would not have been in this set.
    assert seq in {r["attempt_seq"] for r in ex.unresolved()}, \
        "an uncertain failure is not in front of a human"


def test_uncertainty_must_say_what_it_saw(who, item, scope, key):
    """Migration 59 required a detail on `ambiguous`. Migration 67 moved that requirement onto the
    uncertainty axis, which is wider: whoever is unsure says what they are unsure about, whatever
    outcome they pair it with. Without this the new expressiveness would be a way to file doubt
    with no content, which is worse than the old vocabulary rather than better.
    """
    lease = ex.acquire(workspace=WS, work_item_id=item, holder="worker-a", seconds=300)
    _grant(who, scope)
    attempt = ex.reserve(workspace=WS, idempotency_key=key, lease_id=lease["lease_id"],
                         lease_epoch=lease["lease_epoch"], description="charge the card",
                         subject=who, capability="effect.external", scope=scope)
    with pytest.raises(psycopg2.errors.CheckViolation):
        ex.settle(attempt_seq=attempt["attempt_seq"], outcome="failed", completeness="uncertain",
                  effect_state="unknown", settled_by="worker-a",
                  settle_secret=lease["settle_secret"])


def test_the_epoch_column_is_read_from_the_store_not_assumed(item):
    """R-LEASE-TOLERANCE-01. Migration 65 renamed the column; this port must still serve a store
    that has not caught up.

    THE DEFECT: `acquire` wrote `lease_epoch` unconditionally and called `has_column` zero times, so
    on any store below ledger 65 it raised UndefinedColumn and THE FIRST STEP OF THE EXECUTION SPINE
    could not run. Found by Terminal 08 in REV-064; measured at ledger 64 exits 1, 67 and 68 pass.

    IT MATTERS MORE THAN A TOLERANCE QUESTION USUALLY DOES because of this lane's own carve-out: a
    lane never applies migrations to the live store, it hands over the apply order. THE LIVE STORE
    IS BEHIND BY CONSTRUCTION until the operator applies them, so the merged runtime meets it there.

    This scene tests the RESOLVER rather than needing two stores, because a suite that needs a
    ledger-64 database to run is a suite nobody runs. The resolver is the whole of the fix: given a
    store, it returns the name that store actually has.

    AND IT CANNOT TELL THE FIX FROM ITS ABSENCE, which CAP14 measured in REV-033: a mutation
    replacing the resolver's body with `return "lease_epoch"` passes every assertion below, because
    this host's store carries only that name and the loop returns on its first iteration. What is
    asserted here is real -- the insert agrees with the resolver on a live store, which no stub can
    show -- but it is not coverage of the branch the fix exists for. The two scenes that ARE are
    `test_the_resolver_is_driven_against_simulated_stores_it_will_never_meet_here` and
    `test_every_table_migration_65_renamed_is_served_by_the_resolver`; both fail under that
    mutation, and this one does not. Read the three together or you will over-credit this one.
    """
    with store.read() as s:
        name = ex._epoch_column(s)
        present = [c for c in ex.EPOCH_NAMES
                   if schema.has_column("execution_lease", c, ctx=s)]
    # DENOMINATOR. If neither name is present the assertion below would be comparing nothing, and a
    # store with no epoch column at all is a different finding.
    assert present, "neither epoch name exists on this store; below migration 58"
    assert name in present, f"resolver returned {name!r} which this store does not have"
    # THE PREFERENCE ORDER, ASSERTED AS A FACT ABOUT THE CONSTANT AND NOT ABOUT THIS STORE.
    # My first version of this compared the resolver to present[0] -- and present is BUILT from
    # EPOCH_NAMES, so on a store carrying only one name it agreed with itself whatever the order
    # was. Watched: inverting EPOCH_NAMES and re-running, IT STILL PASSED. No real store carries
    # both names, so the order can only be checked against intent, which is what this does.
    assert ex.EPOCH_NAMES[0] == "lease_epoch", (
        f"EPOCH_NAMES starts with {ex.EPOCH_NAMES[0]!r}: the CURRENT name must come first, or a "
        f"store that has caught up would still be served the pre-65 column")
    # AND IT IS THE NAME THE INSERT WOULD USE, driven rather than inferred: acquire on this store
    # must succeed and the row must carry that column.
    lease = ex.acquire(work_item_id=item, workspace=WS, holder="tolerance", seconds=60)
    assert name in lease, f"the lease row has no {name!r}, so the resolver and the insert disagree"
    ex.release(lease_id=lease["lease_id"], reason="tolerance scene")


def test_the_resolver_is_driven_against_simulated_stores_it_will_never_meet_here():
    """R-EPOCH-BRANCH-UNREACHED-01, CAP14 REV-033. The scene above never runs the pre-65 branch.

    THE FINDING, AND IT IS EXACT. `test_the_epoch_column_is_read_from_the_store_not_assumed` runs
    against whatever store is present, which on this host is 67 or 68 and carries only
    `lease_epoch`. So `_epoch_column` returns on its FIRST iteration, `fencing_token` is never
    reached, and the preference order is asserted about the CONSTANT, which is intent rather than
    execution. CAP14 drove the proof: a mutation replacing the whole resolver body with
    `return "lease_epoch"` -- deleting the entire fix -- passes every assertion that scene makes.

    My docstring there says a suite needing a ledger-64 database is a suite nobody runs. That is
    true and the conclusion drawn from it was too wide: `has_column` is the ONLY thing the resolver
    consults, so stubbing it simulates any store with no database at all. The premise argued
    against a database and I let it argue against driving the branch.

    Three stores that do not exist on this host, driven in about fifteen lines.
    """
    calls: list = []

    def store_with(*present):
        def stub(table, column, *, ctx=None, **kw):
            calls.append((table, column))
            return column in present
        return stub

    real = schema.has_column
    cases = [
        (("lease_epoch",), "lease_epoch", "post-65: the current name"),
        (("fencing_token",), "fencing_token", "pre-65: the name that store actually has"),
        (("lease_epoch", "fencing_token"), "lease_epoch", "both: the current one wins"),
    ]
    try:
        for present, want, why in cases:
            ex.schema.has_column = store_with(*present)
            got = ex._epoch_column(object(), "execution_lease")
            assert got == want, f"{why}: store has {present}, resolver returned {got!r}"
        # AND BELOW 58 IT REFUSES RATHER THAN GUESSING. A resolver that returned a name here would
        # hand an INSERT a column that does not exist and turn a legible refusal into a driver
        # error two frames away.
        ex.schema.has_column = store_with()
        try:
            ex._epoch_column(object(), "execution_lease")
            raise AssertionError("a store with neither name got a column name back, not a refusal")
        except ex.LeaseUnavailable as exc:
            assert "lease_epoch" in str(exc) and "fencing_token" in str(exc), (
                f"the refusal names neither column: {exc}")
    finally:
        ex.schema.has_column = real

    # THE DENOMINATOR, AND IT IS THE WHOLE POINT OF THIS SCENE. If the stub was never consulted the
    # four assertions above compared a hardcoded return value against itself, which is precisely
    # the mutation CAP14 showed the other scene cannot survive.
    assert calls, "the resolver consulted has_column zero times: it is not reading the store at all"
    assert any(c[1] == "fencing_token" for c in calls), (
        f"the pre-65 branch was never executed; probes were {sorted(set(c[1] for c in calls))}")


def test_every_table_migration_65_renamed_is_served_by_the_resolver():
    """R-LEDGER-CENSUS-02. Migration 65 renamed the fence on TWO tables and the resolver knew one.

    THE DEFECT THE SCENE ABOVE COULD NOT SEE. It tests the resolver, deliberately, because a suite
    that needs a ledger-64 database is a suite nobody runs -- and it therefore passed for as long as
    `reserve` wrote `lease_epoch` into `brain.effect_attempt` unconditionally two functions away.
    At ledger 64 `acquire` succeeded and the next call raised UndefinedColumn. Found by building a
    real ledger-64 store (`engine/bin/ledger-store.sh`) and driving the spine at it.

    THE TABLE LIST IS READ OUT OF THE MIGRATION, not typed here. A list typed here would be a second
    copy of the ledger, free to drift from it silently, which is the same defect one level up.
    """
    sql = pathlib.Path(__file__).resolve().parents[2] / "migrations"
    files = sorted(sql.glob("0065_*.sql"))
    assert files, f"no 0065_*.sql under {sql}; this scene would be a verdict over an empty set"
    renamed = re.findall(r"ALTER\s+TABLE\s+brain\.([a-z_]+)\s+RENAME\s+COLUMN\s+"
                         r"fencing_token\s+TO\s+lease_epoch",
                         files[0].read_text(encoding="utf-8"), re.I)
    # THE DENOMINATOR, AND THE POINT OF THE SCENE. If this is 1 the finding never existed.
    assert len(renamed) >= 2, (
        f"migration 65 renames the fence on {renamed}: this scene exists because it is more than "
        f"one table, so a 1 means the premise has changed and the scene should be re-read")
    # ASSERTED ON WHICH TABLE IS PROBED, NOT ON THE NAME THAT COMES BACK, because at head both
    # tables carry `lease_epoch` and a resolver that ignored its argument entirely would return the
    # right string for the wrong reason. MY FIRST VERSION OF THIS SCENE DID THAT: I planted the old
    # hardcoded `execution_lease` back into the resolver and the scene still passed, on a store
    # where the question it asks cannot come out wrong. Watched, then rewritten.
    probed: list = []
    real = schema.has_column

    def recording(table, column, *, schema_=None, ctx=None, **kw):
        probed.append(table)
        return real(table, column, ctx=ctx, **kw)

    with store.read() as s:
        for table in renamed:
            probed.clear()
            ex.schema.has_column = recording
            try:
                name = ex._epoch_column(s, table)
            finally:
                ex.schema.has_column = real
            assert probed, f"the resolver probed nothing for brain.{table}"
            assert set(probed) == {table}, (
                f"asked for brain.{table}, the resolver probed {sorted(set(probed))}. It is "
                f"ignoring the relation it was given, which is invisible at head because both "
                f"tables carry the same column name and is UndefinedColumn at ledger 64")
            assert schema.has_column(table, name, ctx=s), (
                f"the resolver returned {name!r} for brain.{table}, which does not have it")


def test_the_pre_67_vocabulary_is_the_ledgers_own_and_not_a_second_copy():
    """The names `settle` writes to a store below 67, checked against the constraint that held then.

    Below migration 67 one `outcome` column carries both what happened and how sure you are, with
    five values. `_settle` translates into them, and a hand-typed translation table is exactly the
    kind of thing that stays right until somebody edits one of the two places.
    """
    sql = pathlib.Path(__file__).resolve().parents[2] / "migrations"
    files = sorted(sql.glob("0062_*.sql"))
    assert files, f"no 0062_*.sql under {sql}; nothing to compare the table against"
    m = re.search(r"outcome\s+IN\s*\(([^)]*)\)", files[0].read_text(encoding="utf-8"), re.I)
    assert m, "migration 62 no longer states the outcome vocabulary as `outcome IN (...)`"
    ledger_values = set(re.findall(r"'([a-z_]+)'", m.group(1)))
    assert ledger_values, "0 values parsed. A verdict over an empty set is not a pass."
    assert set(ex.PRE_67_OUTCOMES.values()) == ledger_values, (
        f"PRE_67_OUTCOMES writes {sorted(set(ex.PRE_67_OUTCOMES.values()))} and the store at 62 to "
        f"66 accepts {sorted(ledger_values)}. A value not in that set is a CheckViolation from the "
        f"driver instead of a sentence from this port.")
    # And every key is a value the CURRENT contract actually uses, so the map cannot grow a name
    # the caller can never pass.
    assert set(ex.PRE_67_OUTCOMES) <= set(ex.OUTCOMES), (
        f"PRE_67_OUTCOMES has keys outside OUTCOMES: "
        f"{sorted(set(ex.PRE_67_OUTCOMES) - set(ex.OUTCOMES))}")
    # THE THREE THAT HAVE NO OLD SPELLING ARE THE POINT, and `_settle` refuses them by name below 67
    # rather than flattening them onto a neighbour.
    assert set(ex.OUTCOMES) - set(ex.PRE_67_OUTCOMES) == {"denied", "noop", "partial"}, (
        f"the outcomes with no pre-67 spelling are now "
        f"{sorted(set(ex.OUTCOMES) - set(ex.PRE_67_OUTCOMES))}")


# Its own verb names: `store.transition` raises DuplicateTransition when a name is registered twice,
# and store/test_authority.py registers similarly-shaped fixtures of its own.
@store.transition("test execution make agent")
def _make_agent(ctx, *, name: str):
    return dict(ctx.one("INSERT INTO brain.agent (name, role, status, host, updated) "
                        " VALUES (%s, 'worker', 'idle', 'exec-test', now()) "
                        " ON CONFLICT (name) DO UPDATE SET updated = now() RETURNING name",
                        (name,)))


@store.transition("test execution grant to")
def _grant_to(ctx, *, subject: str, scope: str, granted_by: str):
    return dict(ctx.one(
        "INSERT INTO brain.authority_grant (granted_by, workspace, subject_kind, subject, "
        " capability, scope, expires_at, evidence) "
        " VALUES (%s, %s, 'agent', %s, 'effect.external', %s, now() + interval '1 hour', "
        " 'engine/execution/test_execution.py') RETURNING *", (granted_by, WS, subject, scope)))
