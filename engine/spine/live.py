"""Stages 3 to 5 against the REAL R01 and R02, now that migrations 57-64 are merged.

The Admiral's ruling of 2026-09-07 resolved L01-SPINE-01's dependency by merging the R integration
head into this branch. `standins.py` was written because those tables did not exist here; this
module replaces it with calls to the product code that owns them, `store.authority` and
`engine.execution.coordinator`, so stages 3 to 5 stop being `stand-in` and become `real`.

WHAT THE STAND-INS GOT WRONG, MEASURED RATHER THAN GUESSED AGAIN. Terminal 08 warned that the
stand-ins encoded MY reading of R01 and R02, and that a green run would then be green about the
wrong thing. It was right, and the merge lets me say exactly how wrong:

  * MY LEASE was `action_id` plus `holder`. THE REAL ONE is `brain.execution_lease` with a
    `fencing_token`, an `expires_at` and a `settle_secret_sha256`. I had no fencing token and no
    expiry at all, so my "one dispatcher holds the action" scene could not have detected a stale
    holder acting after its lease lapsed, which is the case leases exist for.
  * MY SINGLE-USE SPEND was `UNIQUE(version_hash)` on a reservation. THE REAL ONE is an
    `idempotency_key` the CALLER derives from the work, and a repeat raises `AlreadyAttempted`
    carrying the prior attempt rather than a bare refusal, so a retrying caller learns what the
    first attempt knew. Mine refused; the real one informs.
  * MY OUTCOME VOCABULARY was `success`, meaning succeeded. The real one at ledger 64 was
    `succeeded`, `failed` or `ambiguous`, and ambiguous is not a polite failed: it means the
    effect may have happened and the process cannot tell. My stand-in had no way to say that,
    which is the single most important thing an execution port can say. Migration 67 then widened
    it to eight and renamed `succeeded` back to `success`, so the vocabulary is discovered here
    rather than written down.
  * MY RECEIPT carried `outcome` and `completeness` side by side, and at ledger 64 that was wrong:
    `effect_attempt` had no completeness column, so it travelled in `detail` and the receipt said
    so rather than smoothing it over. MIGRATION 67 MADE THE ORIGINAL SHAPE RIGHT, splitting one
    question into three -- outcome, completeness, effect_state. The guess was correct about the
    pair and wrong about when.

Every one of those was a plausible reading. That is the point: plausible and wrong is exactly what
a stand-in produces, and only the real tables could settle it.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Mapping

from ports import SpineRefused

_ROOT = Path(__file__).resolve().parents[2]


class LiveUnavailable(RuntimeError):
    """The real runtime could not be reached or its tables are absent. Never a silent fallback."""


#: The lease's monotonic guard, under both of its names. Migration 65 renames `fencing_token` to
#: `lease_epoch` and adds `epoch_issued_by`; at ledger 64 only the first exists. Ordered newest
#: first so a store carrying both answers with the current name.
#:
#: THE OLDER ARM IS STILL UNREACHABLE ON A REAL STORE, AND THE REASON HAS CHANGED TWICE.
#:
#: It was unreachable because `coordinator.py` wrote `lease_epoch` unconditionally, so a pre-65
#: store failed inside `acquire` before this chooser ran. R-LEASE-TOLERANCE-01 fixed that, and
#: `acquire` now RETURNS THE EPOCH UNDER THE CURRENT NAME whatever the store calls it -- so a
#: ledger-64 lease arrives here already keyed `lease_epoch` and the `fencing_token` arm still never
#: fires. Dead for a new reason, which is worth more than the fact of being dead: a disclosure that
#: names a cause goes stale when the cause is repaired, and this one nearly did.
#:
#: It stays because it costs one comparison and a store that pre-dates that normalisation would
#: need it. But a green suite says NOTHING about it: the tests pass because stubs reach an arm
#: reality cannot, which is dead code inside a repair FOR the class of untested-but-green code.
EPOCH_NAMES: tuple[str, ...] = ("lease_epoch", "fencing_token")


#: The migration that splits settle into outcome, completeness and effect_state, and the one that
#: gives `noop` a spelling. Membership is what is asked, never magnitude.
SETTLE_SPLIT_MIGRATION = 67

#: What a successful settle is called, oldest last. Migration 67 renamed `succeeded` to `success`
#: and widened the set to eight; before it, `succeeded` was the only spelling.
SUCCESS_NAMES: tuple[str, ...] = ("success", "succeeded")


def _settle_kwargs(coordinator, outcome: str, completeness: str, ledger_versions) -> dict:
    """Answer `settle` in whichever shape it asks, on either side of migration 67.

    At 67 and above it takes three questions -- outcome, completeness, effect_state. Below 67 it
    takes one, and this fixture has nothing true to say in that vocabulary, so it refuses instead
    of answering. The decision is made on the LEDGER, because the callee now has a single signature
    that tolerates internally and a signature probe answers "post-67" everywhere.

    `effect_state` is `not_applied` and that is not a placeholder. THIS FIXTURE SENDS NOTHING: the
    external action is an email that is never delivered, so claiming `applied` would be the one
    lie in the run. RC-I2 refuses `success` over an effect that is not applied, so the honest pair
    here is `noop` with `not_applied` -- the effect was authorised, reserved and settled, and
    deliberately never caused.
    """
    # DETECTED BY LEDGER, NOT BY SIGNATURE, AND A RUN IS WHAT TAUGHT ME THAT.
    #
    # This asked `inspect.signature(coordinator._settle)` for a `completeness` parameter. That
    # worked while the callee had two shapes. T04's tolerance gave it ONE signature that decides
    # by ledger internally, so the probe now answers "post-67" on every store, and against a real
    # ledger-64 store this sent `noop` -- which that store has no spelling for. The detection was
    # not wrong when written; it was made wrong by a fix elsewhere, and no test of mine could have
    # noticed because both stub signatures still differ.
    #
    # AND MY PRE-67 ARM WAS WRONG TOO, in a way only that store could show: it sent `succeeded`,
    # while the pre-67 vocabulary is ambiguous, cancelled, failed, halted, SUCCESS. Both halves of
    # my backward compatibility were wrong, in the arm I had already disclosed as unreachable.
    if SETTLE_SPLIT_MIGRATION in set(ledger_versions):
        return {
            "outcome": "noop",
            "completeness": completeness,
            "effect_state": "not_applied",
        }

    # BELOW 67 THIS FIXTURE CANNOT BE SETTLED TRUTHFULLY, so it is not settled at all.
    #
    # It authorises, leases and reserves an effect it DELIBERATELY NEVER PERFORMS. Post-67 that is
    # `noop` with `not_applied`. The pre-67 vocabulary has no word for it: `success` is false,
    # `failed` is false and would take the attempt off the human's list, and `cancelled` or
    # `halted` would each claim something that did not happen. T04's own port refuses to
    # mistranslate here rather than picking the nearest word, and a fixture that picked one would
    # be writing a false row to keep a green count.
    raise LiveUnavailable(
        "this store is below migration " + str(SETTLE_SPLIT_MIGRATION) + ", whose outcome "
        "vocabulary has no spelling for an effect that was authorised and deliberately never "
        "performed. The spine settles exactly that, so it stops here rather than claiming an "
        "outcome it did not have. Run it against a store carrying " + str(SETTLE_SPLIT_MIGRATION)
        + ", or read this as the honest limit of the fixture rather than a defect in the store"
    )


def _epoch_kwarg(coordinator, lease) -> dict:
    """Read the lease's epoch by whichever name it carries and pass it by whichever name is taken.

    THIS PROBE IS NOT THE TRAP `_settle_kwargs` HAD, and the difference is worth stating because
    T04's grep -- "any caller reading those signatures has the same trap waiting" -- lands here.

    `_settle_kwargs` read a signature to DECIDE WHICH WORLD IT WAS IN, and broke the moment the
    callee became uniformly tolerant and answered the same everywhere. This reads a signature to
    CHOOSE A NAME TO PASS, which stays correct however many shapes the callee has: passing the
    keyword the callee declares is right by construction, and if `reserve` is renamed again this
    follows without an edit.

    MEASURED, THOUGH, THE PROBE NOW HAS ONE POSSIBLE ANSWER. At T04's head `_reserve` declares
    `lease_epoch` and nothing else, so the loop below cannot return `fencing_token` -- and
    `acquire` normalises the lease row to the current name too, so the value side cannot see the
    old one either. The tolerance is correct, unexercised, and would matter only against an older
    `acquire`. That is the third distinct reason this arm has been dead, and each repair elsewhere
    changed the reason without changing the fact.

    The value is read from the lease row the store actually returned; the keyword is chosen from
    `reserve`'s own signature, so a caller cannot pass a name the callee stopped accepting.
    """
    import inspect

    present = [n for n in EPOCH_NAMES if n in lease]
    if not present:
        raise LiveUnavailable(
            "the lease carries neither " + " nor ".join(EPOCH_NAMES) + "; R02's monotonic guard "
            "has been renamed again and this caller has not been told: " + repr(sorted(lease))
        )
    value = lease[present[0]]

    try:
        accepted = set(inspect.signature(coordinator.reserve).parameters)
    except (TypeError, ValueError):  # pragma: no cover - a C-level or wrapped callable
        accepted = set()
    if "kwargs" in accepted or not accepted:
        # `reserve(**kwargs)` forwards to `_reserve`, so ask the function that declares the names.
        accepted = set(inspect.signature(coordinator._reserve).parameters)

    for name in EPOCH_NAMES:
        if name in accepted:
            return {name: value}
    raise LiveUnavailable(
        "R02's `reserve` accepts neither " + " nor ".join(EPOCH_NAMES) + "; it takes "
        + repr(sorted(accepted))
    )


def bind(database: str):
    """Point the store at a named disposable database and import the real modules.

    The environment is set before `store` is imported. THAT ORDER IS NOT REQUIRED, and the reason I
    first gave for it was false.

    I wrote here that `store.session` reads the name at import time. I never measured it; I reasoned
    it and stated it as a fact, and it travelled from this comment into a fleet-wide warning to
    Terminals 26 and 04. Terminal 04 measured the opposite and asked where mine came from, which is
    the question that had no answer. Driven since, both the plain `store.read()` path and this
    module's own import graph: `dsn()` reads `BRAIN_PG_DB` per connection, so a value set AFTER the
    import is honoured, and a guard placed after the import guards the value that will actually be
    used.

    Setting it early is still what this function does, because the refusal above must happen before
    anything can connect at all. That is a real reason and it is a different one.

    WHAT IS TRUE AND DOES NOT DEPEND ON IMPORT ORDER: this function MUTATES THE PROCESS
    ENVIRONMENT. Anything else in this process that later reads `BRAIN_PG_DB` or
    `ENGINE_SCRATCH_DB` sees a value the spine chose, not one a human did. That hazard was the half
    worth reporting, and tying it to a mechanism that turned out not to exist is how a true finding
    gets discarded when somebody checks the false half.
    """
    # ONE REFUSAL RULE, NOT A SECOND COPY OF IT. This used to test `"scratch" not in database or
    # database == "brain"` -- its own weaker version of `preflight.guard_database_name`, which
    # additionally refuses an unnamed database and the SHARED `brain_scratch` that every lane's
    # suite reads. So the WRITING path was more permissive than the read-only census beside it, and
    # `--db brain_scratch` would have driven real transitions into the store other terminals are
    # reading. Backwards, and found by auditing my own runners for Terminal 26 rather than by
    # anything failing.
    #
    # Calling the same function is the repair. Two copies of a safety rule diverge in the direction
    # of whoever edited one of them.
    from preflight import UnsafeDatabase, guard_database_name  # noqa: PLC0415

    try:
        database = guard_database_name(database)
    except UnsafeDatabase as exc:
        raise LiveUnavailable("refusing " + repr(database) + ": " + str(exc)) from exc
    os.environ["BRAIN_PG_DB"] = database
    os.environ["ENGINE_SCRATCH_DB"] = database
    for p in (str(_ROOT), str(_ROOT / "engine")):
        if p not in sys.path:
            sys.path.insert(0, p)
    try:
        from store import authority  # noqa: PLC0415
        from execution import coordinator  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - a broken checkout, not a runtime state
        raise LiveUnavailable("the real runtime is not importable: " + str(exc)) from exc
    return authority, coordinator


class LiveRuntime:
    """R01 and R02 through their own product code. B01 has no table and stays a stand-in.

    Every method below is one call into a module this lane does not own, which is the whole value:
    a green run now means the real transitions accepted the sequence, not that my SQL agreed with
    my other SQL.
    """

    is_standin = False

    def __init__(self, database: str, *, workspace: str = "spine", promotion_sink=None):
        self.authority, self.coordinator = bind(database)
        self.workspace = workspace
        self.database = database
        self._leases: dict[str, dict] = {}
        self._work_item: str | None = None
        self._grant_seq: int | None = None
        self._promotion = promotion_sink

        # THE VANTAGE POINT IS PART OF THE CLAIM (CAP14, generalising T04's superuser finding).
        # A refusal watched from a privileged role proves the layers that ignore privilege and
        # nothing about the layer that does not, so a run reporting refusals as evidence has to say
        # WHO WAS LOOKING. Measured here rather than assumed: this path connects as `brain_runtime`
        # and not a superuser, so its refusals are seen from the role the real caller occupies. The
        # run prints it, and a test refuses a superuser vantage, because the day this connects as
        # `owner` every negative scene in the spine quietly stops meaning what it says.
        from store.session import read  # noqa: PLC0415

        with read() as ctx:
            seen = ctx.one(
                "SELECT current_user AS role, "
                "(SELECT rolsuper FROM pg_roles WHERE rolname = current_user) AS superuser"
            )
        self.vantage = str(seen["role"])
        self.vantage_is_superuser = bool(seen["superuser"])

        # THE LEDGER THE FIGURE WAS MEASURED AGAINST, so it cannot be quoted without it.
        #
        # SPINE-G3-RENAME-01: the live figure was produced on a ledger-64 store, and on a store
        # carrying 65-67 the same code does not produce a different number -- it does not run,
        # because `fencing_token` no longer exists. So a figure without its ledger describes a
        # store configuration rather than a system, and CAP14 certified one and I produced one.
        # Recording the version beside the number is what stops the next quotation being stale by
        # construction. Membership again, not magnitude: holes are reported, not smoothed over.
        with read() as ctx:
            versions = {int(r["version"]) for r in ctx.query(
                "SELECT version FROM brain.schema_migration")}
        self.ledger_max = max(versions) if versions else None
        self.ledger_recorded = len(versions)
        #: MEMBERSHIP, not magnitude. A scene that needs migration 66 must ask whether 66 is
        #: RECORDED, because this tree has carried a higher maximum over an unapplied range more
        #: than once tonight and `max >= 66` reads true there.
        self.ledger_versions = frozenset(versions)
        self.ledger_holes = (
            sorted(set(range(1, self.ledger_max + 1)) - versions) if self.ledger_max else []
        )

        if not self.authority.tables_present() or not self.coordinator.tables_present():
            raise LiveUnavailable(
                "R01 or R02 tables are absent from " + repr(database) + ". The census says which; "
                "this refuses rather than falling back to a stand-in, because a run that quietly "
                "downgraded would report the composition and imply the components"
            )

    # -- R01 -------------------------------------------------------------------------------------

    def record_approval(self, entry: Mapping[str, Any]) -> str:
        """A grant, then a human's decision about one exact proposal version.

        TWO CALLS AND NOT ONE, which my stand-in collapsed. `approval decide` takes a `grant_seq`,
        so a decision cannot exist without an authority that licensed it; the stand-in stored a
        decision floating free of any grant, and nothing could have caught that.
        """
        action_id = entry["action_ids"][0]
        import store  # noqa: PLC0415

        # THE DISPATCHER HAS TO EXIST BEFORE IT CAN BE APPROVED FOR ANYTHING. R01 refused a
        # `service` subject at the approval gate: "subject spine-dispatcher-1 is neither an agent
        # nor a human this store knows. An approval for nobody authorises nothing and should not
        # be recorded as though it did."
        #
        # I had chosen `service` precisely because 0057 does not verify service identities, and
        # the store refused that at the next gate anyway. Registering the dispatcher through the
        # engine's own `heartbeat` transition is what a real deployment does; writing the row
        # directly would bypass the only write path this repo has.
        import swarm_engine.transitions  # noqa: F401,PLC0415  (registers every verb)

        store.apply("heartbeat", agent=entry["subject"], status="idle", role="dispatcher",
                    host="spine-fixture")

        # TWO GRANTS, AND THE SECOND ONE IS THE CORRECTION R01 TAUGHT ME.
        #
        # My stand-in stored one approval and nothing else. The real R01 refused that model twice,
        # in its own words: "operator cannot approve its own proposal. An actor that can authorise
        # itself is not gated by anything", and then "grant 22 belongs to spine-dispatcher-1 and
        # the decision is signed operator. An approval names the authority the APPROVER holds, not
        # one it found."
        #
        # So the approver holds `approval.decide` and the dispatcher holds `effect.external`, and
        # the approval cites the approver's grant. That is a materially better model than the one
        # I guessed, and no stand-in of mine would have found it.
        approver_grant = store.apply(
            "authority grant",
            granted_by=entry["decided_by"],
            workspace=self.workspace,
            subject=entry["decided_by"],
            subject_kind="human",
            capability="approval.decide",
            scope=entry["scope"],
            evidence="L01-SPINE-01 fixture run, synthetic",
            expires_at=entry.get("expires_at"),
        )
        grant = store.apply(
            "authority grant",
            granted_by=entry["decided_by"],
            workspace=self.workspace,
            subject=entry["subject"],
            capability=entry["capability"],
            scope=entry["scope"],
            subject_kind=entry.get("subject_kind", "agent"),
            evidence="L01-SPINE-01 fixture run, synthetic",
            packet_version=entry["version_hash"],
            expires_at=entry.get("expires_at"),
        )
        self._grant_seq = grant["grant_seq"]
        store.apply(
            "approval decide",
            proposal_id=entry["packet_id"],
            proposal_version=entry["version_hash"],
            workspace=self.workspace,
            decided_by=entry["decided_by"],
            subject=entry["subject"],
            grant_seq=approver_grant["grant_seq"],
            decision="approve",
        )
        return str(action_id)

    def approval_for(self, version_hash: str) -> Mapping[str, Any] | None:
        # `authority.py` publishes reads (`in_force`, `check`) and registers its WRITES as
        # transitions; there is no public reader for one approval row. Rather than invent an API on
        # someone else's module, read the table the way a reviewer would.
        from store.session import read  # noqa: PLC0415

        with read() as ctx:
            return ctx.one(
                "SELECT * FROM brain.approval WHERE proposal_version = %s", (version_hash,)
            )

    # -- R02 -------------------------------------------------------------------------------------

    def acquire_lease(self, action_id: str, *, holder: str) -> str:
        """Take the lease. THE LEASE IS ON A WORK ITEM, not on an action id.

        My stand-in keyed a lease on `action_id` and nothing else. `brain.execution_lease` has a
        foreign key to `brain.work_item`, so a lease on an id no work item carries is refused
        outright. That is the third shape my reading got wrong, and it is the one that matters
        most for the packet's own claim: "one fixture run on ONE DISPATCHER" means a dispatcher
        holding a real work item, not a string.
        """
        import store  # noqa: PLC0415
        import swarm_engine.transitions  # noqa: F401,PLC0415

        item = store.apply(
            "post",
            title="L01-SPINE-01 fixture: email the revised timeline (never sent)",
            lane="spine", posted_by="operator", priority=3,
            body="Synthetic. Created by the spine fixture so the lease has a real subject.",
        )
        work_item_id = item.get("id") or item.get("work_item_id") or item.get("task_id")
        self._work_item = work_item_id
        try:
            lease = self.coordinator.acquire(
                work_item_id=work_item_id, workspace=self.workspace, holder=holder
            )
        except self.coordinator.LeaseHeld as exc:
            raise SpineRefused(str(exc)) from exc
        self._leases[action_id] = lease
        return str(lease["lease_id"])

    def reserve(self, entry: Mapping[str, Any]) -> str:
        """Say what is about to happen, under a key derived from the work.

        THE AUTHORITY CHECK HAPPENS INSIDE THIS CALL, in the same transaction, immediately before
        the row is written. That is R02's design and it is stronger than my stand-in's: mine
        checked an approval it had stored itself, so a grant revoked a second earlier would not
        have stopped anything.
        """
        lease = self._leases[entry["action_id"]]
        try:
            attempt = self.coordinator.reserve(
                idempotency_key=entry["reservation_id"],
                lease_id=lease["lease_id"],
                # THE FIELD MIGRATION 65 RENAMES, READ AND PASSED BY WHICHEVER NAME THE STORE USES.
                #
                # CAP14 raised SPINE-G3-RENAME-01 as an evidence problem and said it was not a code
                # defect, because this branch never modified `coordinator.py` so the merge takes
                # T04's `lease_epoch` version cleanly. That is right about `coordinator.py` and
                # wrong about THIS FILE: the caller is mine, and it named `fencing_token` twice --
                # once reading the lease dict and once as the keyword. On a store carrying 65 the
                # read is a KeyError and the keyword is a TypeError, so the merge would have taken
                # a correct callee and left a broken caller.
                #
                # `docs/SCHEMA-TOLERANCE.md` rule 5 already governs this: the code must run
                # correctly on a store that has none of the migrations. So the name is discovered
                # rather than assumed, in both directions, and this works at ledger 64 and at 65+
                # without a follow-up edit at merge time.
                **_epoch_kwarg(self.coordinator, lease),
                description=entry.get("description", "L01-SPINE-01 fixture effect"),
                workspace=self.workspace,
                subject=entry["subject"],
                capability=entry["capability"],
                scope=entry["scope"],
                proposal=(entry["packet_id"], entry["version_hash"]),
            )
        except self.coordinator.AlreadyAttempted as exc:
            raise SpineRefused(str(exc)) from exc
        except self.authority.Denied as exc:
            raise SpineRefused(str(exc)) from exc
        return str(attempt["attempt_seq"])

    def settle(self, reservation_id: str, *, outcome: str, completeness: str) -> Mapping[str, Any]:
        """Say what happened, exactly once, proving the lease was held.

        `settle_secret` is the proof migration 64 added: `settled_by` is only a label, because a
        name sitting in a table any process can read is a guard you defeat by copying it. My
        stand-in had no such concept.

        COMPLETENESS IS R02's QUESTION SINCE MIGRATION 67, and it has a column. This docstring said
        the opposite for one commit after 67 landed, while the comment ten lines below already said
        it had stopped: SPINE-DOC-01's class, one method over, and outside the guard I wrote for it
        because that guard covers the module docstring only. CAP14 found it; T06 named the general
        shape an hour earlier -- a description adjacent to code is a second implementation that
        nothing typechecks.

        What each field means, read from `_settle_kwargs` rather than restated here so the two
        cannot drift again: `outcome` is what happened, `completeness` is how sure the settler is,
        `effect_state` is what became of the effect.
        """
        action_id = next(iter(self._leases))
        lease = self._leases[action_id]
        # MIGRATION 67 SPLIT ONE QUESTION INTO THREE, and this is the stage that was labelled
        # WAITING ON 67 until the merge. `outcome` is what happened, `completeness` is how sure the
        # settler is, `effect_state` is what became of the effect. All three are required even for
        # a plain success, because RC-I2 refuses `success` over an effect that is not `applied` and
        # that refusal is only legible if the caller had to write the claim down.
        #
        # SO COMPLETENESS STOPS TRAVELLING IN `detail`. Until 67 there was no column for it and the
        # spine carried attention's answer in a free-text field, which the receipt said plainly was
        # a difference rather than a fit. Now it goes where it belongs, and the row means what it
        # says rather than what a reader has to reconstruct.
        #
        # The outcome vocabulary moved too: `succeeded` became `success`, and the set widened to
        # eight. Discovered rather than assumed, the same way the lease field is, so this works on
        # either side of 67 without an edit.
        row = self.coordinator.settle(
            attempt_seq=int(reservation_id),
            settled_by="spine-dispatcher-1",
            settle_secret=lease.get("settle_secret"),
            result_ref="spine://fixture/" + str(reservation_id),
            detail="L01-SPINE-01 fixture effect, never sent",
            **_settle_kwargs(self.coordinator, outcome, completeness, self.ledger_versions),
        )
        return {
            "receipt_id": "attempt_" + str(reservation_id),
            "reservation_id": reservation_id,
            # READ BACK FROM THE ROW R02 WROTE, not echoed from what was asked for. A receipt that
            # repeated the caller's own arguments would agree with itself whatever the store did.
            "outcome": row.get("outcome"),
            "completeness": row.get("completeness", completeness),
            "effect_state": row.get("effect_state"),
            "settled_at": str(row.get("settled_at", "")),
        }

    # -- B01, which still has no table -------------------------------------------------------------

    def receive(self, result: Mapping[str, Any]) -> str:
        if self._promotion is None:
            raise LiveUnavailable("no promotion sink supplied")
        return self._promotion.receive(result)

    def count(self, table: str) -> int:
        if self._promotion is not None and table.startswith("standin_"):
            return self._promotion.count(table)
        from store.session import read  # noqa: PLC0415

        with read() as ctx:
            return int(ctx.one("SELECT count(*) AS c FROM brain." + table)["c"])

    def close(self) -> None:
        pass
