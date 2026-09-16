#!/usr/bin/env python3
"""Drive the execution spine against a real store at whatever ledger it is on.

Run it through `engine/bin/prove-tolerance.sh`, which builds the stores with `ledger-store.sh` and
drives this against each. Or point BRAIN_PG_DB at one store and run this directly.

WHY IT IS A TOOL AND NOT A SUITE. It needs several databases that take a minute each to build, and
this repo already decided that a suite nobody runs is worse than a tool somebody does: the existing
`test_the_epoch_column_is_read_from_the_store_not_assumed` says so in its own docstring and tests
the RESOLVER instead. That was the right call and it is not sufficient, because a resolver test
cannot execute the branch it selects. Testing the resolver proved the epoch name was resolved for
`brain.execution_lease` while `reserve` wrote the other name into `brain.effect_attempt` two
functions away, and it passed the whole time.

WHAT DRIVING IT FOUND, none of which was visible by reading, and two of which were in code written
to fix the first:

    ledger 64, before  KeyError: 'lease_epoch'          acquire returned the store's own name
    ledger 64, before  UndefinedColumn lease_epoch      on brain.effect_attempt: 65 renamed BOTH
    ledger 64, before  single_use_enforced missing      a comment promised a key nothing set
    ledger 66, before  UndefinedColumn completeness     migration 67, written by this lane

Prints a line per check with PASS/FAIL and a denominator at the end. A verdict over an empty set
is not a pass, so a run that reaches no checks exits non-zero saying so.
"""
from __future__ import annotations

import datetime as dt
import os
import pathlib
import sys
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

DB = os.environ.get("BRAIN_PG_DB", "")
if DB in ("", "brain"):
    sys.exit("refusing to run against the live store. Set BRAIN_PG_DB to a scratch database.")

# The repo is this file's grandparent, so the tool runs from anywhere. T04_REPO overrides it, which
# is what let this be written and driven from a scratchpad before it was landed.
REPO = pathlib.Path(os.environ.get("T04_REPO") or pathlib.Path(__file__).resolve().parents[2])
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "engine"))

import store                                                              # noqa: E402
from store import schema                                                  # noqa: E402
from store import authority                                               # noqa: E402
from execution import coordinator as ex                                   # noqa: E402

WS = "ws-t04-tolerance"
PASSED = 0
FAILED = 0
CHECKS = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASSED, FAILED, CHECKS
    CHECKS += 1
    if ok:
        PASSED += 1
        print(f"  PASS  {label}")
    else:
        FAILED += 1
        print(f"  FAIL  {label}")
    if detail:
        print(f"        {detail}")


@store.transition("t04 tolerance make item")
def _make_item(ctx, *, title: str):
    return dict(ctx.one("INSERT INTO brain.work_item (title, lane) VALUES (%s, 'mv-test') "
                        "RETURNING id", (title,)))


@store.transition("t04 tolerance make agent")
def _make_agent(ctx, *, name: str):
    return dict(ctx.one("INSERT INTO brain.agent (name, role, status, host, updated) "
                        " VALUES (%s, 'worker', 'idle', 't04-tol', now()) "
                        " ON CONFLICT (name) DO UPDATE SET updated = now() RETURNING name",
                        (name,)))


@store.transition("t04 tolerance grant to")
def _grant_to(ctx, *, subject: str, scope: str, granted_by: str):
    return dict(ctx.one(
        "INSERT INTO brain.authority_grant (granted_by, workspace, subject_kind, subject, "
        " capability, scope, expires_at, evidence) "
        " VALUES (%s, %s, 'agent', %s, 'effect.external', %s, now() + interval '1 hour', "
        " 't04 tolerance driver') RETURNING *", (granted_by, WS, subject, scope)))


def main() -> int:
    with store.read() as s:
        ledger = s.scalar("SELECT max(version) FROM brain.schema_migration")
        spendable = schema.has_relation("approval_spendable", ctx=s)
        consumed = schema.has_column("approval", "consumed_state", ctx=s)
        two_axes = schema.has_column("effect_attempt", "completeness", ctx=s)

    print(f"store {DB}: ledger {ledger}")
    print(f"  approval_spendable view      : {spendable}")
    print(f"  approval.consumed_state      : {consumed}")
    print(f"  effect_attempt.completeness  : {two_axes}")
    print()

    me = store.whoami()
    if not (me.get("reachable") and me.get("agrees")):
        print(f"the store does not agree who this process is ({me}). Every check below would be a "
              f"verdict over an unbuilt fixture, so none is run.")
        return 2
    who = me["human"]

    agent = f"tol-{uuid.uuid4().hex[:8]}"
    scope = f"lane/t04-tol-{uuid.uuid4().hex[:12]}"
    key = f"t04-tol-{uuid.uuid4().hex[:12]}"
    proposal = f"tol-{uuid.uuid4().hex[:8]}"
    item = store.apply("t04 tolerance make item", title=f"tolerance {uuid.uuid4().hex[:8]}")["id"]
    store.apply("t04 tolerance make agent", name=agent)
    decider = store.apply(
        "authority grant", workspace=WS, granted_by=who, subject=who, subject_kind="human",
        capability="approval.decide", scope=scope,
        expires_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1),
        evidence="t04 tolerance driver")
    store.apply("t04 tolerance grant to", subject=agent, scope=scope, granted_by=who)
    store.apply("approval decide", workspace=WS, proposal_id=proposal, proposal_version="v1",
                decided_by=who, subject=agent, grant_seq=decider["grant_seq"])

    # 1. THE CHECK ANSWERS, AND SAYS WHICH WORLD IT IS IN.
    try:
        got = authority.check(workspace=WS, subject=agent, capability="effect.external",
                              scope=scope, proposal=(proposal, "v1"))
        check("authority.check served this ledger", True)
        check("check reports single_use_enforced",
              "single_use_enforced" in got,
              f"keys carry it: {'single_use_enforced' in got}")
        check(f"single_use_enforced == {consumed} (the store's own answer)",
              got.get("single_use_enforced") == consumed,
              f"got {got.get('single_use_enforced')!r}, columns present {consumed}")
    except Exception as exc:                                        # noqa: BLE001
        check("authority.check served this ledger", False, f"{type(exc).__name__}: {exc}")
        return report()

    # 2. THE SPINE: acquire, then reserve, which is where the consumption UPDATE lives.
    lease = ex.acquire(work_item_id=item, workspace=WS, holder="t04-tol", seconds=300)
    try:
        first = ex.reserve(idempotency_key=key, lease_id=lease["lease_id"],
                           lease_epoch=lease["lease_epoch"], description="the approved effect",
                           workspace=WS, subject=agent, capability="effect.external", scope=scope,
                           proposal=(proposal, "v1"))
        check("reserve served this ledger", True)
        check(f"reserve reports single_use_enforced == {consumed}",
              first.get("single_use_enforced") == consumed,
              f"got {first.get('single_use_enforced')!r}")
    except Exception as exc:                                        # noqa: BLE001
        check("reserve served this ledger", False, f"{type(exc).__name__}: {exc}")
        return report()

    # 3. WHAT SINGLE USE ACTUALLY DID, read from the store rather than from the return value.
    if consumed:
        with store.read() as s:
            row = s.one("SELECT consumed_state, consumed_by FROM brain.approval "
                        " WHERE proposal_id = %s", (proposal,))
        check("above 66 the approval is consumed", row["consumed_state"] == "consumed",
              f"consumed_state={row['consumed_state']!r} by {row['consumed_by']!r}")
        # and a second effect naming it is refused
        try:
            ex.reserve(idempotency_key=key + "-2", lease_id=lease["lease_id"],
                       lease_epoch=lease["lease_epoch"], description="the replay",
                       workspace=WS, subject=agent, capability="effect.external", scope=scope,
                       proposal=(proposal, "v1"))
            check("above 66 a second effect is refused", False, "it was allowed")
        except authority.PolicyDenied as exc:
            check("above 66 a second effect is refused", True, str(exc)[:100])
    else:
        # THE HONEST PRE-66 CLAIM, STATED AND THEN TESTED: single use does not exist there, so a
        # second effect naming the same approval SUCCEEDS. That is the hole 66 closed, reproduced
        # deliberately. A tolerance fix that silently refused instead would be inventing a
        # guarantee the store cannot keep, which is the same lie in the other direction.
        try:
            second = ex.reserve(idempotency_key=key + "-2", lease_id=lease["lease_id"],
                                lease_epoch=lease["lease_epoch"], description="the replay",
                                workspace=WS, subject=agent, capability="effect.external",
                                scope=scope, proposal=(proposal, "v1"))
            check("below 66 a second effect is ALLOWED, and says so",
                  second.get("single_use_enforced") is False,
                  f"attempt {second['attempt_seq']} reserved on a spent approval, "
                  f"single_use_enforced={second.get('single_use_enforced')!r}")
        except Exception as exc:                                    # noqa: BLE001
            check("below 66 a second effect is ALLOWED, and says so", False,
                  f"{type(exc).__name__}: {exc}")

    # 4. SETTLE, WHICH IS WHERE MY OWN MIGRATION 67 LIVES.
    try:
        settled = ex.settle(attempt_seq=first["attempt_seq"], outcome="success",
                            completeness="complete", effect_state="applied",
                            settled_by="t04-tol", settle_secret=lease["settle_secret"],
                            result_ref="ref/t04-tolerance")
        check("settle served this ledger", True)
        want = "success" if two_axes else "succeeded"
        check(f"settle wrote outcome {want!r} for this ledger", settled["outcome"] == want,
              f"got {settled['outcome']!r}")
    except Exception as exc:                                        # noqa: BLE001
        check("settle served this ledger", False, f"{type(exc).__name__}: {exc}")

    # 5. AND THE ROW THAT CANNOT BE TOLD TRUTHFULLY BELOW 67 IS REFUSED RATHER THAN FLATTENED.
    key2 = f"t04-tol-{uuid.uuid4().hex[:12]}"
    proposal2 = f"tol-{uuid.uuid4().hex[:8]}"
    store.apply("approval decide", workspace=WS, proposal_id=proposal2, proposal_version="v1",
                decided_by=who, subject=agent, grant_seq=decider["grant_seq"])
    doubtful = ex.reserve(idempotency_key=key2, lease_id=lease["lease_id"],
                          lease_epoch=lease["lease_epoch"], description="the timed-out effect",
                          workspace=WS, subject=agent, capability="effect.external", scope=scope,
                          proposal=(proposal2, "v1"))
    try:
        # `detail` IS REQUIRED BY MIGRATION 67's `uncertain_needs_detail` CHECK, and the first
        # version of this driver omitted it -- so at head the scene died on a CheckViolation that
        # was the constraint working. Below 67 the Python refusal fires before any SQL, so the
        # omission was invisible on the two stores this was written for. A driver that only ever
        # ran against the stores it was written for is not evidence about the others.
        ex.settle(attempt_seq=doubtful["attempt_seq"], outcome="failed",
                  completeness="uncertain", effect_state="unknown", settled_by="t04-tol",
                  detail="the provider timed out after the request left",
                  settle_secret=lease["settle_secret"])
        check("a doubtful failure below 67 is refused, and above 67 is recorded", two_axes,
              "it was accepted" + ("" if two_axes else " -- the doubt went nowhere"))
    except ex.AmbiguousOutcome as exc:
        check("a doubtful failure below 67 is refused, and above 67 is recorded", not two_axes,
              str(exc)[:160])
    except Exception as exc:                                        # noqa: BLE001
        check("a doubtful failure below 67 is refused, and above 67 is recorded", False,
              f"neither accepted nor refused with a sentence: {type(exc).__name__}: {exc}")

    ex.release(lease_id=lease["lease_id"], reason="tolerance driver")
    return report()


def report() -> int:
    print()
    print(f"DENOMINATOR: {CHECKS} check(s) reached, {PASSED} passed, {FAILED} failed.")
    if CHECKS == 0:                                                          # DENOMINATOR
        print("  0 checks ran. A verdict over an empty set is not a pass.")
        return 2
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
