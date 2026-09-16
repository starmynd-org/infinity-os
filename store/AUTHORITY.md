# What the authority boundary is, and what it is not

Migration 57 and `store/authority.py`. Packet R01.

## The claim

An act that needs authority asks `store.authority.check(subject, capability, scope)` immediately
before the effect. A `Denied` means the effect does not happen. What is in force is computed at
read time from two append-only tables and the clock, so there is no cached decision to go stale
and nothing has to sweep.

## Four layers, named separately, because they fail separately

1. **A CHECK constraint.** A blank scope, an unknown `subject_kind`, a decision that is neither
   approve nor reject, a perpetual grant with no stated reason: refused by the row's own shape.
   Survives every code path, including `psql`.
2. **A foreign key.** An unknown capability is refused against `brain.capability`, so a typo is a
   loud refusal rather than a grant that silently denies everything forever.
3. **A trigger.** UPDATE and DELETE on the three record tables raise. An actor the store has never
   heard of cannot issue or receive a grant. `brain.authority_actor_is_known` is SECURITY DEFINER
   with a pinned `search_path`, because it reads the human roster that `brain_runtime` deliberately
   cannot see.
4. **A privilege.** `brain_runtime` holds SELECT and INSERT and holds no UPDATE and no DELETE. A
   privilege the role never held cannot be exercised by forgetting a trigger.

Layers 3 and 4 both refuse a planted UPDATE. The test asserts the outcome and not which one spoke
first: privilege refuses before the trigger is reached on the runtime path, and the migration's own
DO block watched the trigger refuse as superuser, where privilege is not in the way.

## What this does NOT defend against, stated plainly

- **A superuser.** Whoever can `DROP TRIGGER` or `GRANT UPDATE` can undo layers 3 and 4. This repo's
  connections are role-scoped so that most processes cannot, and that is the actual protection.
- **A service principal's identity.** `subject_kind = 'service'` is accepted without verification,
  because this store holds no service-principal roster today. The trigger says so in a comment
  rather than passing it through as though it had been checked. Closing this needs a roster, and
  that is a packet nobody has written.
- **Same-process confusion.** A process holding a legitimate runtime connection can issue any grant
  the constraints accept, in the name of any human the store knows. The record of who issued it is
  honest and permanent; the issuing is not itself gated by a second factor.
- **Anything about time.** `expires_at` is compared against the database's `now()`. A store whose
  clock is wrong has grants whose expiry is wrong, and nothing here detects that.

## The rule that makes the record worth having

A grant is never edited and never deleted. It is ended by inserting a revocation that names it. So
the question "was this subject authorised at the moment they acted" stays answerable after the
authority is withdrawn, which is the only time anybody asks it.

## Calling it correctly

    from store import authority

    authority.check(agent, "effect.external", f"lane/{lane}")   # raises Denied, or returns the grant
    ...cause the effect...

Late, not early. A check at claim time and an effect ten minutes later is a stale approval, and the
interval between them is exactly where a revocation lands. `check()` is one query against a view.

For an act that also needs a human decision, pass the exact proposal version:

    authority.check(agent, "effect.spend", scope, proposal=(proposal_id, "v3"))

An approval of `v2` will not satisfy it. That is the whole point: `receipt.approval_ref` was a
free-text string naming no version, so a replayed approval of a superseded proposal was
indistinguishable from a fresh one.

## On a store that predates migration 57

`tables_present()` is false, `in_force()` is empty and `check()` raises `Denied` naming the missing
migration. An absent authority table is not "no restrictions": it is a store that cannot say whether
an act was authorised, and the safe reading of cannot-say is no. Tested against a real store built
at version 56.
