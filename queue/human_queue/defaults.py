"""The rule that carries the most weight in this lane.

    **A default may never carry a hard flag's action. Enforced at `ask` time, not by
    convention.**

If the originating task is `external` or `canon_touching` (flags resolved by OR up the whole
parent chain), the stated default must be the null branch: hold, stage only, prepare but do not
send. Anything else is refused at the point of writing.

CONSEQUENCE, and it is the reason the rule is worth this much machinery: **silence can only ever
ship the reversible branch.** That is what makes a bad week degrade safely rather than
dangerously, and it is why the pending-defaults surface can be honestly calm rather than
cosmetically softened. A calm surface that was hiding a pending irreversible act would be the
worst object in this system.

WHERE THE ENFORCEMENT ACTUALLY LIVES, because it is not this file. The classifiers are
`brain.default_is_null_branch(text)`, defined in `queue/schema/0007_queue.sql` and redefined by
`queue/schema/0009_null_branch_act_scan.sql`, and `brain.default_is_null_branch_gated(text)`,
added by `queue/schema/0011_null_branch_residue_closure.sql`. The refusal is the trigger
`question_default_null_branch` on `brain.question`. This module calls the database rather than
reimplementing either test, so there is exactly ONE implementation of each and no parity test is
needed to hold two copies together -- the debt D4 took on `signal_level` and had to write a test
to service.

WHAT THE CLASSIFIER ASKS, since migration 0009 (task 0145) inverted it. It no longer tries to
prove a sentence is safe; it proves the sentence contains NO ACT. Any act verb anywhere in the
text disqualifies it, regardless of position and regardless of what else the text says, and a
null branch must still be positively stated. The D9 adversarial pass defeated the old shape by
leading with a null-branch phrase and appending the act -- `stage only. do not be shy, email the
client the quote` classified as a null branch and FIRED on an external task -- because the old
shape excused an act verb whenever a negator sat in a 34-character window before it, and English
has unlimited ways to put a negator somewhere it does not govern. There is no window now.

WHAT THE FLAGGED-TASK CLASSIFIER ASKS ON TOP OF THAT, since migration 0011 (task 0154). An act
scan is a denylist over an open vocabulary, and task 0145 measured six plainly-stated acts it
cannot see because nobody listed their verb (`hold; get the cheque to Mick`, `hold; envoyer la
facture a Mick`, `hold. rm -rf the staging bucket`). So on a flagged task the question changes
from *which words are dangerous* to *which words does this system recognise*: strip the
null-branch words and a closed function-word class, and refuse on any RESIDUE, naming it. What
carried the act was never the verb; it was `cheque` and `Mick`, and an object and a recipient are
residue by construction. A second rule catches what residue cannot see -- `no action; do it`
names no object at all -- by refusing a clause that OPENS with a verb that is not a null-branch
verb.

IT IS BOUNDED, NOT TRUE BY CONSTRUCTION, and that distinction is the whole lesson of the D9 pass.
Task 0154 broke the residue check with twenty strings built only from allowed words and seventeen
got through. Nineteen of those twenty are refused now; the twentieth, `no action; all of it`, is
still ACCEPTED and is tracked as its own open task rather than folded in and called closed. A
safety claim that measures cleaner than the safety property is the defect this program exists to
prevent, so this module says what it checked and not what it wishes were true.

The cost, stated plainly because it changed what this module recommends, twice in one night:
a default may not name the act even to forbid it (`prepare it but do not send it` is refused,
0009), and on a flagged task it may not use a LIGHT VERB either -- `take`, `get`, `go`, `hear`,
`happen`, `proceed` are residue, because allowing them re-opens the bare-imperative class. That
is why `take no action` left `SUGGESTIONS` below and `no action` replaced it. Measured against
every `default_if_unanswered` ever written in this operation (14 on the swarm bus, 3 on the live
store), the incremental cost of 0011 is ZERO: 0009 already refuses all 17.

The trigger, not this module, is what makes the rule hold for `swarm ask`, for the console, for
an MCP wrapper, and for a human typing INSERT at a psql prompt. This module exists so a caller
can ask "would this be refused?" before it writes, and so the refusal reads in words rather than
as a raw exception.
"""

from __future__ import annotations

import store

# The direction is an ALLOWLIST: on a flagged task the default must positively state a null
# branch. A denylist would pass anything the lexicon had not thought of, and the failure of a
# denylist here is an irreversible act shipped by silence.
#
# EVERY ONE OF THESE SURVIVES THE STRICTEST GATE THAT CAN BE APPLIED TO IT, and that is a
# testable property of this tuple rather than a nicety: `test_ask_default_refusal.py` asserts that
# each one is accepted BY THE GATED CLASSIFIER and that `brain.act_verbs_in` finds nothing in it.
#
# This tuple has now been narrowed twice, once per migration, and both times for the same reason:
# a system that suggests a string it then refuses is worse than either rule alone, because it
# reads as a bug and teaches the writer to route around the gate.
#
#   task 0145 removed "stage it only, do not send" and "prepare it but do not send it": migration
#   0009 refuses any text naming an act verb, even under a negation.
#
#   task 0154 replaced "take no action" with "no action": migration 0011 makes `take` residue on a
#   flagged task, along with the other light verbs. Measured, not guessed -- `take no action` is
#   the one string in this tuple that option (C) refuses, and it was the single reason this
#   change had to ship in the same commit as the migration.
SUGGESTIONS = (
    "hold until I answer",
    "stage only",
    "no action",
    "no action; ask again at the next checkpoint",
    "the task stays blocked until you answer",
)


class DefaultRefused(ValueError):
    """A stated default that carries a hard flag's action. Refused at write time."""


def is_null_branch(text: str, *, gated: bool = False) -> bool:
    """One classifier per case, and both are the database's. Called, never copied.

    `gated=False` is the rule for an UNFLAGGED task: 0009's act scan and positive allowlist. An
    unflagged task's default is allowed to act, which is what "silence is a usable answer" means
    for reversible work.

    `gated=True` is the rule for an `external` or `canon_touching` task since migration 0011: the
    same act scan, plus no unrecognised word, plus no bare imperative. Callers should not pick
    this flag by taste -- `check_default` derives it from the task's own flags.
    """
    fn = "brain.default_is_null_branch_gated" if gated else "brain.default_is_null_branch"
    with store.read("runtime") as s:
        return bool(s.scalar(f"SELECT {fn}(%s)", (text or "",)))


def residue_in(text: str) -> list[str]:
    """The words a flagged task's default uses that this system does not recognise.

    Evidence, like `act_verbs_in` and for the same reason: a refusal that says "you wrote
    `cheque` and `mick`" can be checked against the sentence, and one that says "act-shaped"
    cannot.
    """
    with store.read("runtime") as s:
        return list(s.scalar("SELECT brain.null_branch_residue(%s)", (text or "",)) or [])


def bare_imperatives_in(text: str) -> list[str]:
    """Clause heads that read as an imperative rather than as a null branch: `do it`, `proceed`.

    Separate from `residue_in` because it catches the opposite failure: residue is a default that
    says too much, and a bare imperative is one that says almost nothing and points at the brief.
    """
    with store.read("runtime") as s:
        return list(s.scalar("SELECT brain.bare_imperative_in(%s)", (text or "",)) or [])


def act_verbs_in(text: str) -> list[str]:
    """The act verbs actually present, from the same database function the classifier uses.

    This is the EVIDENCE, not the verdict. It exists so a refusal can name the word it refused
    on, so the console can underline it, and so `brain.queue_default_event` can record what was
    actually shipped independently of what the classifier believed. The D9 pass found the record
    agreeing with the bug; a list can be checked against the text afterwards and a boolean
    cannot.
    """
    with store.read("runtime") as s:
        return list(s.scalar("SELECT brain.act_verbs_in(%s)", (text or "",)) or [])


def flags_of(task_id: str) -> dict:
    """The two hard flags, ORed up the whole parent chain, from D1's recursive view."""
    with store.read("runtime") as s:
        row = s.one("SELECT external, canon_touching FROM brain.work_item_signals WHERE id = %s",
                    (task_id,))
    return {"external": bool(row["external"]), "canon_touching": bool(row["canon_touching"])} \
        if row else {"external": False, "canon_touching": False}


def check_default(task_id: str | None, default_text: str) -> dict:
    """Would this default be refused? Ask before writing; the database asks again at write time.

    Returns the verdict rather than raising, so a console can grey out a Save button instead of
    catching an exception, and so this can be used to audit defaults that were legal when they
    were written and are not legal now. That second case is real: flags inherit by OR and can be
    RAISED on a parent at any time, so a default written under no flags becomes act-shaped on a
    task that never changed. The write-time trigger cannot see that; `queue doctor` does.
    """
    text = (default_text or "").strip()
    if not task_id or not text:
        return {"ok": True, "gated": False, "null_branch": True, "reason": ""}
    flags = flags_of(task_id)
    gated = flags["external"] or flags["canon_touching"]
    # THE VERDICT IS THE ONE THE WRITE PATH WILL APPLY, not a laxer one that happens to be
    # cheaper. The trigger asks `default_is_null_branch_gated` on a flagged task, so this asks it
    # too; a preflight that says yes where the write path says no is a preflight nobody trusts,
    # and `test_one_classifier_so_the_preflight_cannot_drift` is what holds the two together.
    null_branch = is_null_branch(text, gated=gated)
    acts = act_verbs_in(text)
    residue = residue_in(text) if gated else []
    imperatives = bare_imperatives_in(text) if gated else []
    if not gated or null_branch:
        return {"ok": True, "gated": gated, "null_branch": null_branch, "act_verbs": acts,
                "residue": residue, "bare_imperatives": imperatives, "reason": "", **flags}
    names = " + ".join(n for n, v in (("external", flags["external"]),
                                      ("canon_touching", flags["canon_touching"])) if v)
    # Name the word. A refusal that says "act-shaped" and nothing else gets read as the gate
    # being fussy; a refusal that says "you wrote `email`" gets read as the gate being right.
    # Most substantive evidence first, and this order is deliberate: `hold; get the cheque to
    # Mick` trips the residue check AND the clause-head rule, and naming `cheque, mick` tells the
    # writer why the sentence is dangerous where naming `get` tells them about grammar. Empty
    # residue with an imperative present is exactly the bare-imperative class, which has no other
    # evidence to give, so it comes last and never competes with a better answer.
    if acts:
        why = (f"It names an act: {', '.join(acts)}. Even negated, an act verb is refused, "
               f"because a negation is bypassable and an absence is not.")
    elif residue:
        why = (f"It uses words this gate does not recognise: {', '.join(residue)}. On a flagged "
               f"task the default must be a null branch and nothing else, so an object, a "
               f"recipient, a date or an amount is refused whatever verb carries it.")
    elif imperatives:
        why = (f"It opens a clause with {', '.join(imperatives)}. On a flagged task a clause may "
               f"not open with a verb that is not a null-branch verb, because \"do it\" ships "
               f"whatever the brief already says to do.")
    else:
        why = ("It states no null branch at all, so it cannot be classified, and an "
               "unclassifiable default is refused rather than assumed safe.")
    return {"ok": False, "gated": True, "null_branch": False, "act_verbs": acts,
            "residue": residue, "bare_imperatives": imperatives, **flags,
            "reason": (f"{task_id} is {names}, so its default may not carry an action. "
                       f"Refused: {text!r}. {why} Silence must only ever ship the reversible "
                       f"branch. Try one of: " + "; ".join(SUGGESTIONS) + ". If the act really "
                       f"is the right default, then the task is asking the operator to approve "
                       f"it, which is the answer and not the default.")}


def refuse_if_act_shaped(task_id: str | None, default_text: str) -> None:
    """The raising form, for callers that want the exception."""
    verdict = check_default(task_id, default_text)
    if not verdict["ok"]:
        raise DefaultRefused(verdict["reason"])
