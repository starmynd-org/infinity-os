"""Who may do what, answered from the store and not from the caller's opinion of itself.

Packet R01. The tables are migration 57; this module is the only thing that writes them, and it
writes them through `store.apply` like every other state change in this package.

WHAT THIS MODULE IS FOR, IN ONE SENTENCE: an executor about to cause an effect asks `check()`
immediately before the effect, and a `Denied` means no effect happens.

THREE PROPERTIES, AND EACH ONE IS A DEFECT THIS REPO HAS ALREADY MET SOMEWHERE ELSE.

1. IT ASKS AT THE MOMENT OF THE EFFECT, NOT AT DISPATCH. A grant checked when an item was claimed
   and relied on when the effect fires is a stale approval, and the interval between them is
   exactly where a revocation lands. `check()` is cheap and is meant to be called late.

2. IT ANSWERS FROM `brain.authority_in_force`, WHICH IS A VIEW. Expiry is arithmetic on `now()`,
   so nothing has to sweep and nothing can fail to sweep. There is no cached decision here and
   deliberately no TTL: `store/schema.py` caches a SCHEMA probe, which changes when a human
   applies a migration, and an authority answer is not that kind of fact.

3. IT WORKS ON A STORE THAT HAS NEVER SEEN MIGRATION 57. `docs/SCHEMA-TOLERANCE.md` rule 5: the
   code must run correctly on a store with none of these migrations. On such a store there are no
   grants, so `in_force()` is empty and `check()` DENIES, with a message naming the missing
   migration. Failing closed is the only safe reading of "this store cannot tell me". The
   alternative -- treating an absent table as "no restrictions" -- is the shape that turns a
   half-applied store into an open door.

WHAT THIS MODULE DOES NOT CLAIM. It is a check at the storage boundary, enforced by constraints
and triggers that a superuser can drop. It does not defend against a process that holds superuser
credentials, and this repo's connections are role-scoped precisely so that most of them cannot.
`store/AUTHORITY.md` states the boundary in full; nothing here should be read as more than that.
"""

from __future__ import annotations

import contextlib

import psycopg2

from . import schema
from .session import read
from .transitions import transition


class Denied(PermissionError):
    """The act does not happen. The BASE of three kinds, and callers should catch this one.

    A FAIL-CLOSED SYSTEM HIDES ITS OWN MISCONFIGURATION, which is why this is a family and not one
    class. Every kind below refuses, so nothing gets less safe by distinguishing them; but a wiring
    bug that arrives looking like a policy decision costs somebody an afternoon deciding why the
    rules say no, when the answer is that a capability name was misspelled. Terminal 03 met exactly
    that: its rule engine reads any exception as a refusal, so an invented capability name would
    have surfaced as a mysterious deny rather than as the wiring error it was.

    `kind` is the machine-readable version, for a caller that wants to branch without importing
    three classes.
    """

    kind = "denied"


class PolicyDenied(Denied):
    """A rule said no. The system is working and the answer is no.

    No grant in force covers this subject, capability and scope; or the exact proposal version was
    not approved. This is the only one of the three that means "ask for authority".
    """

    kind = "policy"


class StoreCannotAnswer(Denied):
    """The store cannot say whether this was authorised, so the answer is no.

    Not a decision: an absence of one. A store that predates the authority migrations has no grants
    to consult, and reading that as "no restrictions" is how a half-applied store becomes an open
    door. Refusing is right and calling it a policy denial is not, because there is no policy here
    to go and change.
    """

    kind = "unavailable"


class Misconfigured(Denied):
    """The CALLER is wrong, not the policy. A capability that does not exist, or a malformed ask.

    Still refuses, because a caller asking an incoherent question must not get an effect. But the
    remedy is a code change, not a grant, and saying so at the call site is the whole point of
    separating this from `PolicyDenied`.
    """

    kind = "wiring"


class AlreadyRevoked(RuntimeError):
    """This grant was already ended. The first revocation stands; this call changed nothing."""


class AlreadyDecided(RuntimeError):
    """This approver already decided this exact proposal version. A replay is not a new decision."""


#: The one place the table set is named, so a store missing them is detected identically
#: everywhere rather than by five hand-written `except UndefinedTable` clauses.
_TABLES = ("authority_grant", "authority_revocation", "approval", "capability")

_ABSENT = (
    "this store has no authority tables: it predates migration 57 "
    "(0057_authority_is_a_grant_and_a_revocation). Denying, because a store that cannot say "
    "whether an act was authorised has not said yes."
)


@contextlib.contextmanager
def _ask(ctx):
    """Answer through the caller's transaction when there is one, and open a read session when not.

    Both objects expose `one`, `scalar` and `query`, which is the whole surface this module needs,
    so the body of a read is written once rather than twice.
    """
    if ctx is not None:
        yield ctx
    else:
        with read() as s:
            yield s


def tables_present(ctx=None) -> bool:
    """Does this store carry migration 57 at all.

    One catalog query over all four tables rather than four probes, and it asks the catalog rather
    than trying a SELECT: inside a transition a failed statement aborts the whole transaction, so
    a check that discovers absence by hitting it cannot then go on to do anything. That is the
    same argument `store/schema.py` opens with, and this follows it.
    """
    sql = ("SELECT count(*) FROM information_schema.tables "
           "WHERE table_schema = 'brain' AND table_name = ANY(%s)")
    if ctx is not None:
        return ctx.one(sql, (list(_TABLES),))["count"] == len(_TABLES)
    with read() as s:
        return s.scalar(sql, (list(_TABLES),)) == len(_TABLES)


def in_force(subject: str, *, workspace: str, capability: str | None = None,
             scope: str | None = None, ctx=None) -> list:
    """Every grant in force for this subject in this workspace, narrowest filter the caller asked.

    Empty is a real answer and the common one. A caller must not read empty as "could not tell":
    `tables_present()` is the question about the store, and this is the question about the subject.
    """
    with _ask(ctx) as s:
        if not tables_present(s):
            return []
        sql = ["SELECT * FROM brain.authority_in_force WHERE workspace = %s AND subject = %s"]
        args: list = [workspace, subject]
        if capability is not None:
            sql.append("AND capability = %s")
            args.append(capability)
        if scope is not None:
            sql.append("AND scope = %s")
            args.append(scope)
        return s.query(" ".join(sql) + " ORDER BY grant_seq", tuple(args))


def check(subject: str, capability: str, scope: str, *, workspace: str,
          proposal: tuple | None = None, ctx=None) -> dict:
    """Raise `Denied` unless a grant in force covers this act. Return the grant that did.

    Call this IMMEDIATELY BEFORE the effect. `proposal`, when given, is
    `(proposal_id, proposal_version)` and adds the second half of the question: not only may this
    subject act, but was THIS EXACT VERSION approved. An approval of `v1` does not authorise `v2`,
    which is the property `receipt.approval_ref` could not express and the reason a replayed
    approval used to be indistinguishable from a fresh one.

    `workspace` IS REQUIRED AND KEYWORD-ONLY, added by migration 63. Keyword-only on purpose: it
    sits beside three other short strings, and an argument that can be silently transposed with
    `scope` is the wrong shape for the one dimension that separates two customers. A grant in one
    workspace does not satisfy a check in another. A stored row whose workspace is NULL matches
    NOTHING, because SQL equality is never true against NULL: rows written before migration 63
    authorise nobody rather than everybody, which is the only safe reading of a row that predates
    the boundary.

    PASS `ctx` FROM INSIDE A TRANSITION. Without it this opens its own read session, which is a
    second transaction, and a revocation landing between the check and the write would not be seen
    by either. With the calling transition's own context the check and the effect record are one
    transaction and one snapshot, which is the difference between asking and knowing. R02's
    `effect reserve` passes it for exactly that reason.

    `single_use_enforced` IS ON THE RESULT WHEN, AND ONLY WHEN, `proposal` WAS GIVEN. True means
    the approval was read through `brain.approval_spendable` and is now spent-once; False means the
    store is below migration 66, where that view and the columns behind it do not exist and one
    recorded decision satisfies every effect naming it.

    ABSENCE OF THE KEY MEANS NO APPROVAL WAS CHECKED. It does NOT mean enforced, and a caller that
    reads it as enforced has inverted the safe default -- which is why it is absent rather than
    True on the no-proposal path: a missing key raises a KeyError at the caller, and a True would
    have been believed. Test it as `result.get("single_use_enforced") is False` when you mean
    "checked and not enforced", never as a truthiness test.

    NOTHING IN THIS REPO BRANCHES ON IT TODAY. `effect reserve` returns the same field from its own
    probe, and `engine/bin/prove-tolerance.py` asserts both agree with the store at four ledgers;
    no production caller reads either. It is here for the second caller of `check`, because there is
    currently exactly one (`engine/execution/coordinator.py`) and it fails loudly a few lines later
    on a pre-66 store -- so the silent-acceptance path this field exists to disclose is unreachable
    TODAY and would be reachable the moment somebody calls `check` without going on to reserve.
    """
    with _ask(ctx) as s:
        if not tables_present(s):
            raise StoreCannotAnswer(_ABSENT)
        # ASKED FIRST, so a misspelled capability cannot arrive as "no grant covers this". The
        # vocabulary is closed precisely so this is answerable; not answering it would waste the
        # closure.
        if s.one("SELECT 1 FROM brain.capability WHERE capability = %s", (capability,)) is None:
            known = ", ".join(r["capability"] for r in
                              s.query("SELECT capability FROM brain.capability ORDER BY 1"))
            raise Misconfigured(
                f"{capability!r} is not a capability this store knows, so no grant could ever "
                f"carry it and no policy decision is involved. This is a wiring error, not a "
                f"denial. Known: {known}.")
        grant = s.one(
            "SELECT * FROM brain.authority_in_force "
            " WHERE workspace = %s AND subject = %s AND capability = %s AND scope = %s "
            " ORDER BY grant_seq LIMIT 1", (workspace, subject, capability, scope))
        if grant is None:
            # The denominator, in the message. "Denied" with no context sends a reader looking for
            # a bug in the check; "denied, and this subject holds two other grants" sends them to
            # the scope they actually asked for.
            #
            # `elsewhere` is the workspace-shaped version of that, and it is the message somebody
            # will need at 2am: a grant that matches this capability and scope EXACTLY but lives in
            # another workspace is the single most confusing way for this check to say no, because
            # everything the reader is looking at appears correct.
            held = s.scalar(
                "SELECT count(*) FROM brain.authority_in_force "
                " WHERE workspace = %s AND subject = %s", (workspace, subject))
            elsewhere = s.scalar(
                "SELECT count(*) FROM brain.authority_in_force "
                " WHERE subject = %s AND capability = %s AND scope = %s "
                "   AND workspace IS DISTINCT FROM %s", (subject, capability, scope, workspace))
            raise PolicyDenied(
                f"{subject!r} holds no grant in force for {capability!r} on {scope!r} in workspace "
                f"{workspace!r}. It holds {held} grant(s) in force in that workspace, and "
                f"{elsewhere} grant(s) matching this exact capability and scope in ANOTHER "
                f"workspace. A grant next door is not a grant here.")
        if proposal is not None:
            proposal_id, proposal_version = proposal
            # THE SUBJECT IS PART OF THE MATCH, and it was not before migration 61. CAP14-REV-017
            # found this looked up an approval by (proposal, version) alone, so an approval minted
            # for one actor satisfied the check for any other. `subject IS NOT NULL` is not
            # redundant: rows written before 61 have none, and an approval naming nobody authorises
            # nobody rather than everybody.
            # SPENDABLE, not merely decided. Migration 66: an approval is single use, so this
            # reads the view that excludes the consumed, the revoked and the expired. Before 66
            # this matched on `brain.approval` and one recorded decision satisfied every effect
            # that named it -- a human approving one payment authorised every payment naming the
            # same proposal version. The unique constraint from 57 stopped a decision being
            # RECORDED twice and never stopped one being SPENT twice.
            # THE VIEW ONLY EXISTS FROM MIGRATION 66. R-LEDGER-CENSUS-01.
            #
            # A store below 66 has `brain.approval` and no `approval_spendable`, and this read
            # raised UndefinedTable there -- the SECOND site of the same rule-5 failure T08 found in
            # the lease path, surfaced only because the first fix let execution get this far. The
            # census that followed says this is the last RELATION: of 14 objects this lane names
            # across 37 sites at aa1813d, exactly one is introduced above ledger 64, and it is this.
            #
            # THAT SCOPE WAS NARROWER THAN IT READ, AND THIS COMMENT SAID "THE LAST ONE". A column
            # is an object too, and a table that exists while lacking a column added by 66 is not a
            # missing relation, so the relation census could not see it BY CONSTRUCTION.
            # R-LEDGER-CENSUS-02 ran the same method over columns and found seven (column, table)
            # pairs above 64 that this lane writes -- including the ones five lines below.
            #
            # ON A PRE-66 STORE SINGLE USE IS NOT MERELY UNCHECKED, IT DOES NOT EXIST: the columns
            # that record consumption arrive with the view. So the fallback reads `brain.approval`,
            # which is precisely the pre-66 behaviour -- one recorded decision satisfies every
            # effect naming it. That is a real weakening and it is the store's, not this code's,
            # and rule 5 requires the path to keep serving at the ledger the store is on.
            #
            # IT IS NOT SILENT. `single_use_enforced` comes back on the result so a caller can tell
            # the two worlds apart, because a caller that cannot is exactly how the pre-66 hole gets
            # re-inherited by something written after it was closed.
            spendable = schema.has_relation("approval_spendable", ctx=s)
            source = "brain.approval_spendable" if spendable else "brain.approval"
            decided = s.one(
                f"SELECT approval_seq, decision FROM {source} "
                " WHERE proposal_id = %s AND proposal_version = %s "
                "   AND subject IS NOT NULL AND subject = %s AND workspace = %s LIMIT 1",
                (proposal_id, proposal_version, subject, workspace))
            if decided is None:
                other = s.scalar(
                    "SELECT count(*) FROM brain.approval WHERE proposal_id = %s", (proposal_id,))
                elsewhere = s.scalar(
                    "SELECT count(*) FROM brain.approval "
                    " WHERE proposal_id = %s AND proposal_version = %s "
                    "   AND (subject IS NULL OR subject <> %s)",
                    (proposal_id, proposal_version, subject))
                # THE DIAGNOSTIC NEEDS THE SAME TOLERANCE THE READ ABOVE GOT, and did not have it.
                # `consumed_state`, `revoked_at` and `expires_at` ON brain.approval ALL ARRIVE WITH
                # 66. Guarding only the successful read left the DENIAL path raising UndefinedColumn
                # on a pre-66 store -- the failing path, which is the one that runs when something
                # is already wrong, and the one whose message a human reads at 2am.
                if spendable:
                    spent = s.scalar(
                        "SELECT count(*) FROM brain.approval "
                        " WHERE proposal_id = %s AND proposal_version = %s AND subject = %s "
                        "   AND (consumed_state = 'consumed' OR revoked_at IS NOT NULL "
                        "        OR (expires_at IS NOT NULL AND expires_at <= now()))",
                        (proposal_id, proposal_version, subject))
                    tail = (f"and {spent} for this subject are already spent, revoked or expired. "
                            f"An approval is single use, so one that was already spent is not an "
                            f"approval you still hold.")
                else:
                    # Not "0 are spent". THE STORE CANNOT ANSWER THE QUESTION, and a zero would be
                    # read as an answer.
                    tail = ("and this store is below migration 66, so it has no record of an "
                            "approval being spent, revoked or expired at all. That count is "
                            "unavailable rather than zero, and on this store an approval is NOT "
                            "single use.")
                raise PolicyDenied(
                    f"no SPENDABLE approval for {subject!r} on {proposal_id!r} at version "
                    f"{proposal_version!r}. {other} decision(s) exist for that proposal in total; "
                    f"{elsewhere} at this version name another subject or none; {tail}")
        out = dict(grant)
        if proposal is not None:
            # The caller needs this to SPEND it. `reserve` consumes it in the same transaction.
            out["approval_seq"] = decided["approval_seq"]
            # THE COMMENT ABOVE PROMISED THIS KEY AND THE CODE DID NOT SET IT. Written as a claim
            # about behaviour that did not exist, in the paragraph explaining why the weakening is
            # safe -- so the sentence that made the fallback defensible was the false one. Found
            # because Terminal 03 said this was the bullet it would look hardest at, which is a
            # better reason than any I supplied myself.
            out["single_use_enforced"] = spendable
        return out


@transition("authority grant")
def _grant(ctx, *, granted_by: str, workspace: str, subject: str, capability: str, scope: str,
           subject_kind: str = "agent", expires_at=None, perpetual_reason: str | None = None,
           packet_version: str | None = None, evidence: str) -> dict:
    """Issue a grant. Append-only: there is no `authority amend`, by construction.

    Every refusal below comes from the database, not from this function. That is deliberate: a
    check written here would protect callers who go through here, and the point of putting it in
    a constraint and a trigger is that it also protects the ones who do not.
    """
    if not tables_present(ctx):
        raise StoreCannotAnswer(_ABSENT)
    row = ctx.one(
        "INSERT INTO brain.authority_grant "
        " (granted_by, workspace, subject_kind, subject, capability, scope, packet_version, "
        "  expires_at, perpetual_reason, evidence) "
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *",
        (granted_by, workspace, subject_kind, subject, capability, scope, packet_version,
         expires_at, perpetual_reason, evidence))
    return dict(row)


@transition("authority revoke")
def _revoke(ctx, *, grant_seq: int, workspace: str, revoked_by: str, reason: str) -> dict:
    """End a grant by recording that it ended. The grant row is not touched and cannot be.

    A second revocation is a caller bug, not a no-op, so it is surfaced. The unique constraint is
    what detects it; this only translates the violation into a sentence a reader can act on.

    `workspace` must be the grant's own, and migration 63's trigger is what enforces that rather
    than this function: ending a grant from outside its workspace would be reaching across the
    boundary that migration exists to draw.
    """
    if not tables_present(ctx):
        raise StoreCannotAnswer(_ABSENT)
    # ASK BEFORE YOU WRITE, and the reason is the one `store/schema.py` opens with: inside a
    # transition, a failed statement aborts the whole transaction, so the handler cannot then run
    # a query to say what the conflict was. Measured here on 2026-09-06 -- the first version of
    # this function caught UniqueViolation and died in the handler on InFailedSqlTransaction.
    prior = ctx.one("SELECT revoked_at, revoked_by FROM brain.authority_revocation "
                    " WHERE grant_seq = %s", (grant_seq,))
    if prior is not None:
        raise AlreadyRevoked(
            f"grant {grant_seq} was already revoked at {prior['revoked_at']} by "
            f"{prior['revoked_by']!r}. The first revocation stands and this call changed nothing.")
    try:
        row = ctx.one(
            "INSERT INTO brain.authority_revocation (grant_seq, workspace, revoked_by, reason) "
            " VALUES (%s, %s, %s, %s) RETURNING *", (grant_seq, workspace, revoked_by, reason))
    except psycopg2.errors.UniqueViolation:
        # The look-then-insert above is not atomic against a concurrent revoker. The constraint
        # is, which is why it exists; this reports the race without a second query, because the
        # transaction is aborted by the time we are here.
        raise AlreadyRevoked(
            f"grant {grant_seq} was revoked concurrently. One revocation stands; this one did "
            f"not land, and no query can be run on this aborted transaction to name the winner."
        ) from None
    return dict(row)


@transition("approval decide", role="operator")
def _decide(ctx, *, proposal_id: str, proposal_version: str, workspace: str, decided_by: str,
            subject: str, grant_seq: int, decision: str = "approve") -> dict:
    """Record one decision, by an entitled human, about one exact proposal version, FOR one subject.

    ROLE="operator" SINCE 2026-09-09, task IDN-APPROVAL-DECIDER-FORGE-01 part 2, by
    2026-09-09-IOS-term-5 under ruling R3. Measured before the change, two seats, two scratch
    stores: from brain_runtime, the login every agent surface holds, an approval naming a real
    human as decided_by LANDED, 1 of 1, because migrations 61 and 63 check the NAME and never the
    login. Migration 71 makes decided_by equal brain.current_human(), and this registration is the
    other half: the verb now opens a HUMAN's login, the operator's by default or the one
    `as_human=<slug>` names, exactly as `accept work` has since migration 36. A process that holds
    no human credential gets StoreConfigError from `store.session.dsn` and never reaches the
    store, which is the intended outcome on an agent's host: an agent may not decide. The two
    halves ship together or neither ships; `store/test_authority.py` is 5 failed of 28 with only
    the trigger and 28 of 28 with both.

    `subject` is who the approval authorises, and it is required. Before migration 61 there was no
    such field, and the consequence was measured rather than theorised: any actor holding any grant
    could insert an approval naming itself and satisfy its own check. The store now refuses that,
    and refuses a decider who is unknown, who holds no `approval.decide` grant, or whose grant has
    been revoked. None of those checks live in this function, on purpose: a check written here would
    protect callers who come through here, and the point of putting it in a trigger is that it also
    protects the ones who do not.

    Original note, still true:

    THE VERSION IS PART OF THE KEY. Approving `v1` says nothing about the `v2` that replaced it,
    and a caller that re-sends the same approval gets `AlreadyDecided` rather than a second row.
    That is deduplicated retry, which the phone surface needs and which R02 relies on: an approval
    delivered twice by a flaky network must not read as two approvals.
    """
    if not tables_present(ctx):
        raise StoreCannotAnswer(_ABSENT)
    prior = ctx.one(
        "SELECT decided_at, decision FROM brain.approval "
        " WHERE proposal_id = %s AND proposal_version = %s AND decided_by = %s",
        (proposal_id, proposal_version, decided_by))
    if prior is not None:
        raise AlreadyDecided(
            f"{decided_by!r} already decided {proposal_id!r} version {proposal_version!r} at "
            f"{prior['decided_at']} ({prior['decision']}). A replay is not a new decision.")
    try:
        row = ctx.one(
            "INSERT INTO brain.approval "
            " (proposal_id, proposal_version, workspace, decided_by, subject, grant_seq, "
            "  decision) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *",
            (proposal_id, proposal_version, workspace, decided_by, subject, grant_seq, decision))
    except psycopg2.errors.UniqueViolation:
        raise AlreadyDecided(
            f"{decided_by!r} decided {proposal_id!r} version {proposal_version!r} concurrently. "
            f"One decision stands and this call added nothing."
        ) from None
    return dict(row)
