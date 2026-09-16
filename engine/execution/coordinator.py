"""Leases, fencing and idempotent effects. Packet R02.

Every write here goes through `store.apply`, because that is the only write path in this codebase
and a lane that opened its own connection would be the end of that property rather than an
exception to it.

WHY A LEASE AND NOT A CLAIM. `brain.work_item.claimed_by` is an intention: this agent means to do
this work. It has no expiry and no holder identity beyond a name, so a process that has been
unresponsive for two hours can wake up and act on a claim nobody took away. A lease says something
narrower and more useful: this holder may act, until this instant, and anybody can tell whether
that is still true.

WHY THE EPOCH. Expiry alone does not stop a slow worker: it checks the clock, sees time remaining,
stalls for a minute, and writes. Meanwhile the item was taken over. The lease epoch is issued by one
sequence in the database, is copied into the reservation, and is compared against the newest lease
for that item at reserve time, so the stalled worker's write is refused rather than merged. The
epoch is assigned by a trigger and a value the caller supplies is overwritten: an epoch a client can
choose is not a fence.

IT WAS CALLED `fencing_token` UNTIL MIGRATION 65. The contract carried `lease_epoch` on
`Receipt.execution` before the Lease object existed, so when this lane filed the change request that
created that object it chose to adopt the contract's name rather than ask the contract to move. Two
vocabularies for one number is how a receipt and the row it describes stop being joinable by
anything except a human reading both.

WHERE THE AUTHORITY CHECK GOES. In `reserve`, immediately before the row is written, and nowhere
earlier. A check at claim time and an effect ten minutes later is a stale approval, and the gap
between them is exactly where a revocation lands.
"""

from __future__ import annotations

import datetime as dt

import store
from store import schema
from store import authority


class LeaseHeld(RuntimeError):
    """Somebody else holds an unreleased lease on this item. Includes expired ones, deliberately."""


class LeaseNotLive(RuntimeError):
    """The lease is released, expired, or superseded. Whatever it was for, it may not do it now."""

    kind = "policy"


class LeaseUnavailable(LeaseNotLive):
    """The store cannot say anything about leases, because it predates migrations 58 and 59.

    Refuses, like everything else here, and says WHY it refuses: the remedy is applying a migration,
    not releasing a lease or asking for authority.
    """

    kind = "unavailable"


class AmbiguousOutcome(RuntimeError):
    """Raised when a caller tries to treat an unresolved attempt as if it had been resolved."""


class AlreadyAttempted(authority.Denied):
    """This idempotency key was already used. The effect is not repeated.

    Subclasses `Denied` so a caller that catches the refusal family still refuses, and carries its
    own kind because the remedy is different from every other kind: read the prior attempt and
    reconcile it. Asking for more authority would not help and re-executing is the thing this
    exists to prevent.
    """

    kind = "repeat"


_TABLES = ("execution_lease", "effect_attempt", "effect_reconciliation")

_ABSENT = ("this store has no execution tables: it predates migrations 58 and 59. Refusing, "
           "because a store that cannot record that an effect was attempted must not be used to "
           "attempt one.")


def tables_present(ctx=None) -> bool:
    sql = ("SELECT count(*) FROM information_schema.tables "
           "WHERE table_schema = 'brain' AND table_name = ANY(%s)")
    if ctx is not None:
        return ctx.one(sql, (list(_TABLES),))["count"] == len(_TABLES)
    with store.read() as s:
        return s.scalar(sql, (list(_TABLES),)) == len(_TABLES)


def live_lease(work_item_id: str):
    """The lease that may act on this item right now, or None. Computed, never cached."""
    with store.read() as s:
        if not tables_present(s):
            return None
        row = s.one("SELECT * FROM brain.execution_lease_live WHERE work_item_id = %s",
                    (work_item_id,))
        return dict(row) if row else None


def unresolved() -> list:
    """Attempts a human still has to look at: never settled, or settled ambiguous and unreconciled.

    This is a view, so nothing has to remember to enqueue anything, and an attempt cannot fall out
    of the queue because a process died before it got round to filing it.
    """
    with store.read() as s:
        if not tables_present(s):
            return []
        return s.query("SELECT * FROM brain.effect_unresolved ORDER BY attempt_seq")


@store.transition("execution lease acquire")
def _acquire(ctx, *, work_item_id: str, workspace: str, holder: str, seconds: int = 300) -> dict:
    """Take the right to act on one item. Refused if anybody already holds an unreleased lease.

    An EXPIRED lease still blocks, and that is the design rather than an oversight. Letting expiry
    silently free the item makes the most common failure -- a worker that stopped without saying so
    -- invisible at exactly the moment somebody is trying to work out what happened. Taking over
    means calling `release` first, with a reason, which leaves a row saying who took what and when.
    """
    if not tables_present(ctx):
        raise LeaseUnavailable(_ABSENT)
    # PER WORKSPACE, since migration 63. LS-I1 is "at most one unreleased lease per packet per
    # workspace", so two workspaces working the same item are two pieces of work and must not block
    # each other. Querying without the workspace here would report a neighbour's lease as this
    # workspace's, which is a refusal the caller cannot act on and cannot see the cause of.
    held = ctx.one(
        "SELECT lease_id, holder, expires_at FROM brain.execution_lease "
        " WHERE workspace = %s AND work_item_id = %s AND released_at IS NULL",
        (workspace, work_item_id))
    if held is not None:
        expired = held["expires_at"] <= dt.datetime.now(dt.timezone.utc)
        raise LeaseHeld(
            f"lease {held['lease_id']} on {work_item_id!r} in workspace {workspace!r} is held by "
            f"{held['holder']!r} until "
            f"{held['expires_at']}{' -- EXPIRED, and still blocking until it is released' if expired else ''}. "
            f"Release it with a reason to take it over.")
    # `lease_epoch` is passed as 0 and the trigger overwrites it. Passing anything here is
    # theatre; the column is NOT NULL and the trigger is the only thing that decides.
    epoch = _epoch_column(ctx)
    row = ctx.one(
        "INSERT INTO brain.execution_lease "
        f" (work_item_id, workspace, holder, expires_at, {epoch}) "
        " VALUES (%s, %s, %s, now() + make_interval(secs => %s), 0) RETURNING *",
        (work_item_id, workspace, holder, seconds))
    lease = dict(row)
    # THE SETTLE SECRET, HANDED OVER ONCE. Migration 64 mints it in the trigger and puts the
    # plaintext in a transaction-local setting; the table keeps only its SHA-256. Read it here,
    # return it to the caller, and KEEP IT: it is the only evidence that this process is the one
    # that held the lease, and it cannot be recovered from the row afterwards.
    lease["settle_secret"] = ctx.scalar("SELECT current_setting('brain.lease_secret', true)")
    # AND UNDER THE CURRENT NAME WHATEVER THE STORE CALLS IT, so the tolerance reaches the CALLER.
    #
    # The resolver made `acquire` serve a pre-65 store, and the row it returns carries the store's
    # own column name -- so at ledger 64 the key is `fencing_token`, and `reserve` takes an argument
    # called `lease_epoch`. Every caller therefore had to resolve the name a second time to hand
    # step one's output to step two, and none does: this repo's own execution suite writes
    # `lease["lease_epoch"]` throughout. Found by driving the spine against a real ledger-64 store,
    # where `acquire` succeeded and the next line raised KeyError. A tolerance that stops at the
    # port boundary is a tolerance the spine cannot use.
    lease["lease_epoch"] = lease[epoch]
    return lease


@store.transition("execution lease release")
def _release(ctx, *, lease_id: int, reason: str) -> dict:
    """End a lease. The reason is required by a CHECK, not by this function's politeness."""
    if not tables_present(ctx):
        raise LeaseUnavailable(_ABSENT)
    prior = ctx.one("SELECT released_at, release_reason FROM brain.execution_lease "
                    " WHERE lease_id = %s", (lease_id,))
    if prior is None:
        raise LeaseNotLive(f"no lease {lease_id}")
    if prior["released_at"] is not None:
        # Ask before you write: inside a transition a failed statement aborts everything after it,
        # so the message has to be assembled before the statement that would fail.
        raise LeaseNotLive(
            f"lease {lease_id} was already released at {prior['released_at']} "
            f"({prior['release_reason']!r}). The first release is the one that happened.")
    row = ctx.one(
        "UPDATE brain.execution_lease SET released_at = now(), release_reason = %s "
        " WHERE lease_id = %s RETURNING *", (reason, lease_id))
    return dict(row)


@store.transition("effect reserve")
def _reserve(ctx, *, idempotency_key: str, lease_id: int, lease_epoch: int, description: str,
             workspace: str, subject: str, capability: str, scope: str,
             proposal: tuple | None = None, budget_scope_type: str | None = None,
             budget_scope_id: str | None = None) -> dict:
    """Say what you are about to do, before you do it, under a key you can present again.

    THE AUTHORITY CHECK IS HERE AND IT IS LATE ON PURPOSE. `authority.check` runs inside this
    transaction, immediately before the row is written, so a grant revoked a second ago stops the
    effect that was about to happen. R01 owns the answer; this owns the moment it is asked.

    THE KEY IS THE CALLER'S JOB. Derive it from the work, not from the clock or a random number: a
    key that differs between the first attempt and the retry buys nothing at all. The unique
    constraint is what makes a repeat a refusal, and this function turns that refusal into the
    prior attempt, so a retrying caller learns what the first attempt knew instead of guessing.
    """
    if not tables_present(ctx):
        raise LeaseUnavailable(_ABSENT)
    prior = ctx.one("SELECT * FROM brain.effect_attempt WHERE idempotency_key = %s",
                    (idempotency_key,))
    if prior is not None:
        # A REPEAT, not a denial and not a wiring error. It gets its own kind for the same reason
        # the others do: the caller's next move is to reconcile, not to ask for authority.
        raise AlreadyAttempted(
            f"idempotency key {idempotency_key!r} was already reserved at {prior['reserved_at']} "
            f"as attempt {prior['attempt_seq']}, outcome "
            f"{prior['outcome'] or 'NOT YET SETTLED'}. This is the same effect, not a new one. "
            f"Read the attempt and reconcile it; do not re-execute it.")

    # Authority, asked at the last possible moment and in this transaction. Raises Denied, and
    # nothing is written.
    #
    # WHAT PASSING ctx DOES AND DOES NOT BUY, corrected after CAP14-REV-017. It puts the check and
    # the write in ONE TRANSACTION, so a rollback covers both and there is no separate-session
    # window. It does NOT make them one snapshot: under READ COMMITTED -- which is what this repo
    # runs at, since nothing sets an isolation level -- each statement takes its own snapshot, so a
    # revocation committing between these two statements is invisible to the check. The earlier
    # version of this comment claimed otherwise and was wrong.
    #
    # The property is real anyway, because migration 62 re-checks the grant inside the INSERT's own
    # trigger, which is the statement that writes the row and therefore the only place that can see
    # that revocation. This check stays because it produces the good error message and refuses
    # before any work is done; the trigger is what makes the refusal true under concurrency.
    grant = authority.check(subject, capability, scope, workspace=workspace, proposal=proposal,
                            ctx=ctx)

    row = ctx.one(
        "INSERT INTO brain.effect_attempt "
        # THE SAME RESOLVER, ON THIS TABLE. Migration 65 renamed the column here too.
        f" (idempotency_key, lease_id, {_epoch_column(ctx, 'effect_attempt')}, description, "
        "  workspace, subject, capability, "
        "  scope, grant_seq, budget_scope_type, budget_scope_id) "
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *",
        (idempotency_key, lease_id, lease_epoch, description, workspace, subject, capability,
         scope, grant["grant_seq"], budget_scope_type, budget_scope_id))

    # SPEND THE APPROVAL, IN THIS TRANSACTION, BY CONDITIONAL UPDATE. Migration 66: an approval is
    # single use. Until it existed, `check` found a recorded decision and left it there, so the
    # same approval satisfied every effect that named it -- one human approving one payment
    # authorised every payment naming that proposal version.
    #
    # `WHERE consumed_state = 'unconsumed'` plus a row count, never a read followed by a write. Two
    # workers reaching one approval at the same instant both see `unconsumed` if you look first;
    # exactly one gets ROW_COUNT 1 from this. This lane was caught believing a look-then-write was
    # atomic once already (CAP14-REV-017, the fence), and did not need telling twice.
    #
    # If it matches nothing the approval was spent, revoked or expired between `check` and here, and
    # raising rolls back the attempt row above with it. That is the point of doing both in one
    # transaction: there is no state where the effect is reserved and the approval was not spent.
    # THE CONSUMPTION COLUMNS ALSO ARRIVE WITH 66, AND THE TOLERANCE FIX ONE FILE OVER DID NOT
    # REACH THIS FAR. R-LEDGER-CENSUS-02.
    #
    # `check` was taught to fall back to `brain.approval` on a pre-66 store, which made the check
    # PASS there -- and the very next statement in the same transaction is this UPDATE, against
    # three columns that store does not have. So the fix moved the failure by one statement instead
    # of removing it: exactly the point-fix pattern the census exists to stop me repeating, done by
    # me, inside the commit that introduced the census. The relation census could not catch it
    # because `brain.approval` is not a missing relation; only its columns are missing.
    #
    # PRE-66 BEHAVIOUR IS REPRODUCED RATHER THAN FAKED: below 66 an approval was never consumed,
    # so below 66 this does not consume. That is schema-tolerance rule 5 -- the path served at 64
    # and must keep serving -- and it is a REAL weakening, which is why it is announced on the row
    # and why `check` returns `single_use_enforced`. A silent non-consumption would re-open the
    # hole 66 closed and leave no way to find out it had.
    if proposal is not None:
        single_use = schema.has_column("approval", "consumed_state", ctx=ctx)
        if single_use:
            ctx.execute(
                "UPDATE brain.approval SET consumed_state = 'consumed', consumed_at = now(), "
                "       consumed_by = %s "
                " WHERE approval_seq = %s AND consumed_state = 'unconsumed'",
                (f"attempt:{row['attempt_seq']}", grant["approval_seq"]))
            if ctx.one("SELECT consumed_state, consumed_by FROM brain.approval "
                       " WHERE approval_seq = %s",
                       (grant["approval_seq"],))["consumed_by"] != f"attempt:{row['attempt_seq']}":
                raise authority.PolicyDenied(
                    f"approval {grant['approval_seq']} was spent by somebody else between the "
                    f"check and this write. It is single use, so this effect does not happen and "
                    f"the reservation above is rolled back with this refusal.")
        else:
            schema.warn_once(
                "approval-not-single-use",
                f"this store is below migration 66, so approval {grant['approval_seq']} was NOT "
                f"consumed by attempt {row['attempt_seq']} and can license further effects. That "
                f"is the pre-66 behaviour, reproduced deliberately; apply 66 to end it.")
        out = dict(row)
        out["single_use_enforced"] = single_use
        return out
    return dict(row)


# THE EPOCH COLUMN'S NAME, READ FROM THE STORE RATHER THAN ASSUMED. R-LEASE-TOLERANCE-01.
#
# Migration 65 renamed `fencing_token` to `lease_epoch`. This file then wrote the new name
# UNCONDITIONALLY and called `has_column` ZERO TIMES, so on any store below ledger 65 `acquire`
# raised `UndefinedColumn` and the FIRST STEP OF THE EXECUTION SPINE could not run at all. Measured
# by Terminal 08 in REV-064 and reproduced here: ledger 64 exits 1, ledger 67 and 68 pass.
#
# THAT IS SCHEMA-TOLERANCE RULE 5 BY ITS OWN TEST -- does this path serve today at the ledger this
# store is on? `execution lease acquire` DID serve at 64, and stopped. And it matters more than a
# tolerance question usually does because of THIS LANE'S OWN CARVE-OUT: a lane does not apply
# migrations to the live store, it hands over the apply order. SO THE LIVE STORE IS BEHIND BY
# CONSTRUCTION until the operator applies 65 to 67, and the merged runtime would meet it there.
#
# The repo's pattern was already established in swarm_engine/projects.py, routines.py and
# transitions.py, and Terminal 03 applied it in `live.py` one file over after a rename finding of
# its own. The caller tolerated and the callee -- this file, which actually writes the column -- did
# not.
#
# Resolved ONCE PER CALL against the connection in hand, not cached at import: a process can outlive
# a migration, and a name cached at import is the same class of defect as the rename it is here to
# absorb.
EPOCH_NAMES = ("lease_epoch", "fencing_token")


def _epoch_column(ctx, relation: str = "execution_lease") -> str:
    """Whichever of the two names this store actually has, ON THIS RELATION. Prefers the current.

    THE RELATION IS AN ARGUMENT BECAUSE MIGRATION 65 RENAMED THE COLUMN ON TWO TABLES:

        ALTER TABLE brain.execution_lease RENAME COLUMN fencing_token TO lease_epoch;
        ALTER TABLE brain.effect_attempt  RENAME COLUMN fencing_token TO lease_epoch;

    This resolver was written against the first and hardcoded it, so `reserve` -- STEP TWO of the
    spine -- still wrote `lease_epoch` into `brain.effect_attempt` unconditionally and raised
    UndefinedColumn at ledger 64. The fix for R-LEASE-TOLERANCE-01 made step one serve a store it
    then handed to a step two that could not. Neither Terminal 08 nor I saw it by reading, because
    we were both reasoning about the LEASE table; driving the spine against a real ledger-64
    database found it on the next line.

    R-LEDGER-CENSUS-02 did not catch it either, and that is the more useful half. That census
    enumerates `ALTER TABLE ... ADD COLUMN` above ledger 64, and A RENAME IS NOT AN ADD -- so the
    method was narrower than the claim for the second time in one night, in the same way. The
    census is a good tool for saying where to look and a bad one for saying nothing is left.

    The default keeps the existing scene's `_epoch_column(s)` call meaning what it meant.
    """
    for name in EPOCH_NAMES:
        if schema.has_column(relation, name, ctx=ctx):
            return name
    # NEITHER. Say which store and both names, rather than let the INSERT fail on whichever was
    # guessed last: a store with no epoch column at all is below migration 58 and this port has
    # nothing to offer it.
    raise LeaseUnavailable(
        "brain.execution_lease has neither 'lease_epoch' nor 'fencing_token'. That store is below "
        "migration 58 and the execution port cannot take a lease on it at all.")


OUTCOMES = ("success", "denied", "noop", "ambiguous", "partial", "failed", "halted", "cancelled")
COMPLETENESS = ("complete", "partial", "uncertain")
EFFECT_STATES = ("applied", "not_applied", "unknown")

# WHAT EACH OUTCOME WAS CALLED BEFORE MIGRATION 67, for stores that have not had it applied yet.
# The vocabulary at 62 to 66 was five values, and three of the eight above have no spelling in it.
PRE_67_OUTCOMES = {"success": "succeeded", "failed": "failed", "ambiguous": "ambiguous",
                   "halted": "halted", "cancelled": "cancelled"}
# The only two that reached `effect_unresolved` at 62 to 66, so the only two that can carry doubt
# there without the doubt disappearing from human review. Migration 67's own view rewrite says so.
PRE_67_UNRESOLVED = ("ambiguous", "halted")


@store.transition("effect settle")
def _settle(ctx, *, attempt_seq: int, outcome: str, completeness: str, effect_state: str,
            settled_by: str, settle_secret: str | None = None, result_ref: str | None = None,
            detail: str | None = None) -> dict:
    """Say what actually happened, exactly once, answering TWO questions rather than one.

    `outcome` is what happened: one of `success`, `denied`, `noop`, `ambiguous`, `partial`,
    `failed`, `halted`, `cancelled`. `completeness` is how sure you are: `complete`, `partial` or
    `uncertain`. `effect_state` is what became of the effect itself: `applied`, `not_applied` or
    `unknown`.

    ALL THREE ARE REQUIRED, INCLUDING FOR A PLAIN SUCCESS, where two of them are forced. That looks
    like ceremony and is not. Migration 67's RC-I2 refuses `success` over an effect that is not
    `applied`, and the refusal is only legible if the caller had to write the claim down: a worker
    that types `outcome="success", effect_state="unknown"` is told exactly what it got wrong,
    where a signature that inferred `applied` from `success` would have manufactured the caller's
    most consequential assertion on its behalf and never asked.

    Before migration 67 this took one `outcome` doing both jobs, and `ambiguous` was the only way to
    spell doubt. That made "it failed and I am not sure" UNWRITEABLE: you could say it failed, or
    you could say you were unsure, and both rows were false. A worker whose payment call timed out
    after the charge left had no true row to write. It can now write
    `outcome="failed", completeness="uncertain", effect_state="unknown"`, and that row goes to
    `effect_unresolved` for a human.

    A success must name what it did and any uncertainty must say what it saw; both are CHECK
    constraints, so a caller that forgets is refused rather than believed.

    THE OLD SPELLING `succeeded` IS GONE, renamed to the contract's `success`. Two spellings of one
    value is how a vocabulary rots, so it is refused here with a sentence rather than left to
    surface as a CheckViolation from the driver.

    THIS SIGNATURE NO LONGER TELLS YOU WHAT LEDGER THE STORE IS ON, and it used to. Terminal 03's
    caller detected the pre-67 world by asking `inspect.signature` whether `completeness` was a
    parameter, which was correct while this function had two shapes -- and the tolerance below gave
    it ONE shape that decides internally, so that probe now answers "post-67" everywhere and sent a
    `noop` to a ledger-64 store that has no spelling for it. **A caller that reads the callee's
    SHAPE breaks the moment the callee becomes uniformly tolerant**, which is the good outcome here
    and a trap for anything that was reading the shape. No test on either side could catch it: two
    stub signatures still differ from each other, so the stubs pass while reality does not.

    ASK THE STORE, NOT THIS FUNCTION: `schema.has_column("effect_attempt", "completeness")` is what
    this function itself uses, and `reserve` and `authority.check` were made tolerant the same way,
    so any caller inspecting THEIR signatures has the same trap waiting.

    There is no second settle. Later information is a reconciliation.

    `settle_secret` IS THE PROOF, and `settled_by` is now only the label on the record. Migration
    64 closed the impersonation path migration 62 left open: the holder's name is a string sitting
    in a table any runtime process can read, so a guard you defeat by copying a value out of the row
    it guards is a speed bump. The secret is minted by the server when the lease is acquired, and
    only its digest is stored. Pass the value `acquire` returned.

    It remains a BEARER TOKEN. A worker that logs its secret or hands it to a child process has
    handed over the ability to settle, and this does not fix that.

    Historical note on `settled_by`, which migration 62 introduced: CAP14-REV-017 found that this took
    an attempt number and nothing else, so any caller could close another worker's genuinely
    unresolved effect as a success with an invented reference -- removing it from
    `effect_unresolved` and therefore from human review, which is a partial or unknown outcome
    reported as complete. Migration 62 requires the settler to be the holder of the lease the
    attempt was reserved under, and refuses a holder whose lease has since been superseded.

    An EXPIRED lease can still settle, deliberately. A worker waiting on a slow provider must be
    able to report honestly what happened; refusing that would turn every slow effect into a
    permanent unknown. Superseded is a different thing from expired, and by then the takeover has
    already recorded that this effect's fate is unknown.
    """
    if not tables_present(ctx):
        raise LeaseUnavailable(_ABSENT)
    if outcome == "succeeded":
        raise AmbiguousOutcome(
            "outcome 'succeeded' was renamed to 'success' in migration 67, to match the contract. "
            "The store accepts one spelling on purpose.")
    for name, value, allowed in (("outcome", outcome, OUTCOMES),
                                 ("completeness", completeness, COMPLETENESS),
                                 ("effect_state", effect_state, EFFECT_STATES)):
        if value not in allowed:
            raise AmbiguousOutcome(f"{name}={value!r} is not one of {', '.join(allowed)}")
    prior = ctx.one("SELECT settled_at, outcome FROM brain.effect_attempt WHERE attempt_seq = %s",
                    (attempt_seq,))
    if prior is None:
        raise AmbiguousOutcome(f"no attempt {attempt_seq}")
    if prior["settled_at"] is not None:
        raise AmbiguousOutcome(
            f"attempt {attempt_seq} was settled at {prior['settled_at']} as {prior['outcome']!r}. "
            f"That is what was true then. File a reconciliation instead of rewriting it.")
    # PRESENTED THE WAY IT WAS ISSUED: a transaction-local setting, so it never travels in a column,
    # an index, or the text of the statement that would end up in a log.
    if settle_secret is not None:
        ctx.execute("SELECT set_config('brain.settle_secret', %s, true)", (settle_secret,))
    # AND THE SAME QUESTION ABOUT MY OWN MIGRATION, WHICH I DID NOT ASK. R-LEDGER-CENSUS-02.
    #
    # `completeness` and `effect_state` arrive with 67. I wrote 67, wrote this to depend on it
    # unconditionally, and one screen up argued at length that the live store IS BEHIND BY
    # CONSTRUCTION because this lane hands over an apply order rather than applying it. That
    # argument bought tolerance for somebody else's rename in 65 and I never turned it on myself:
    # `effect settle` served at 64 and would have raised UndefinedColumn on every store below 67.
    #
    # Below 67 there is ONE column doing both jobs, with five values. The translation is faithful
    # or it is refused -- never lossy and quiet, because the whole reason 67 exists is that the old
    # vocabulary could not spell "it failed and I am not sure", and a `failed` row written to a
    # pre-67 store does not reach `effect_unresolved` there. Writing it anyway would drop the exact
    # row a human most needs to see, in the name of tolerance.
    if schema.has_column("effect_attempt", "completeness", ctx=ctx):
        row = ctx.one(
            "UPDATE brain.effect_attempt "
            "   SET settled_at = now(), outcome = %s, completeness = %s, effect_state = %s, "
            "       result_ref = %s, detail = %s, settled_by = %s "
            " WHERE attempt_seq = %s RETURNING *",
            (outcome, completeness, effect_state, result_ref, detail, settled_by, attempt_seq))
        return dict(row)
    if outcome not in PRE_67_OUTCOMES:
        # BOTH HALVES, BECAUSE ONE LIST READS AS EITHER. Terminal 03 read this message, saw
        # `success` in it, passed `succeeded`, and was refused again.
        #
        # It printed `sorted(PRE_67_OUTCOMES)` -- the KEYS, which are what a caller MAY PASS -- and
        # a reader takes a list headed "this store's vocabulary" to be what the STORE ACCEPTS,
        # which is the values. The two differ in exactly one entry and that entry is the rename
        # this whole migration is about, so the one word the message was most needed for is the one
        # it got wrong. Naming the keys alone tells a caller to pass what it just passed.
        raise AmbiguousOutcome(
            f"this store is below migration 67. PASS one of: "
            f"{', '.join(sorted(PRE_67_OUTCOMES))} -- these are the current contract's spellings "
            f"and this port translates them. The store itself records "
            f"{', '.join(sorted(set(PRE_67_OUTCOMES.values())))}, so {outcome!r} has no spelling "
            f"there and there is no true row to write. Apply 67, or settle with an outcome the "
            f"store can hold. Do NOT pass the store's spelling: {outcome!r} is refused whether it "
            f"is a post-67 name this store lacks or a pre-67 name this port has renamed.")
    carries_doubt = completeness != "complete" or effect_state == "unknown"
    if carries_doubt and outcome not in PRE_67_UNRESOLVED:
        raise AmbiguousOutcome(
            f"this store is below migration 67, where a single `outcome` column carries both what "
            f"happened and how sure you are. completeness={completeness!r} with "
            f"effect_state={effect_state!r} is doubt, and below 67 only "
            f"{' and '.join(PRE_67_UNRESOLVED)} reach `effect_unresolved`. Writing {outcome!r} "
            f"here would record the doubt nowhere and take the attempt off the human's list, which "
            f"is the defect migration 67 was written to fix. Apply 67, or settle as 'ambiguous'.")
    schema.warn_once(
        "settle-one-axis",
        f"this store is below migration 67, so `completeness` and `effect_state` are not recorded "
        f"and {outcome!r} is written as {PRE_67_OUTCOMES[outcome]!r}. Apply 67 to keep both axes.")
    row = ctx.one(
        "UPDATE brain.effect_attempt "
        "   SET settled_at = now(), outcome = %s, result_ref = %s, detail = %s, settled_by = %s "
        " WHERE attempt_seq = %s RETURNING *",
        (PRE_67_OUTCOMES[outcome], result_ref, detail, settled_by, attempt_seq))
    return dict(row)


@store.transition("effect reconcile")
def _reconcile(ctx, *, attempt_seq: int, found_by: str, finding: str, evidence: str) -> dict:
    """Record what somebody found out later. Append-only, and it does not touch the attempt.

    `finding` is `effect-happened`, `effect-did-not-happen` or `still-unknown`. The third is a real
    answer and is why the unresolved view does not treat any reconciliation as closure: somebody
    looking and failing to find out is information, and it must not read as resolution.
    """
    if not tables_present(ctx):
        raise LeaseUnavailable(_ABSENT)
    row = ctx.one(
        "INSERT INTO brain.effect_reconciliation (attempt_seq, found_by, finding, evidence) "
        " VALUES (%s, %s, %s, %s) RETURNING *", (attempt_seq, found_by, finding, evidence))
    return dict(row)


# The names lanes call. Thin, because every one of them is one `store.apply`: the transition IS the
# implementation, and a wrapper that did anything else would be a second place for the rules to live.
def acquire(*, work_item_id: str, workspace: str, holder: str, seconds: int = 300) -> dict:
    return store.apply("execution lease acquire", work_item_id=work_item_id, workspace=workspace,
                       holder=holder, seconds=seconds)


def release(*, lease_id: int, reason: str) -> dict:
    return store.apply("execution lease release", lease_id=lease_id, reason=reason)


def reserve(**kwargs) -> dict:
    """See `_reserve`. Keyword-only, and the keywords are that function's signature."""
    return store.apply("effect reserve", **kwargs)


def settle(*, attempt_seq: int, outcome: str, completeness: str, effect_state: str,
           settled_by: str, settle_secret: str | None = None,
           result_ref: str | None = None, detail: str | None = None) -> dict:
    return store.apply("effect settle", attempt_seq=attempt_seq, outcome=outcome,
                       completeness=completeness, effect_state=effect_state,
                       settled_by=settled_by, settle_secret=settle_secret,
                       result_ref=result_ref, detail=detail)


def reconcile(*, attempt_seq: int, found_by: str, finding: str, evidence: str) -> dict:
    return store.apply("effect reconcile", attempt_seq=attempt_seq, found_by=found_by,
                       finding=finding, evidence=evidence)
