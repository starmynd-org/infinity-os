"""The delegation ladder: seven levels of leeway, in his words, and the two gates none of them move.

Row `0439`, his ask 8. Built 2026-08-31 on his ruling of the same day.

HIS NAMES, NOT NEW ONES. The ladder is his and he wrote it out in order on 2026-08-28:

    tell, sell, consult, agree, then AI decides with the human advising, then INQUIRE (AI decides
    and it lands in a decisions queue to review later), then DELEGATE (AI decides, audit trail
    only, not worth his attention unless something goes wrong)

A plan written later proposed seven different names (`Ask first`, `Propose options`, `Recommend
one`, ...). Those are a reinvention of a ladder he had already named precisely, and this module
uses HIS words. The one-line descriptions below are his own, quoted where he gave them.

HIS STATED GOAL, and it is the test for whether any of this is worth having:

    "a clear relationship as to who is doing the work and who has the responsibility to make the
    decision here and how much leeway to give the AI."

So each level answers exactly two questions: WHO DECIDES, and WHEN DOES HE FIND OUT. Nothing else
belongs on the ladder, and a level that cannot answer both is not a level.

================================================================================================
THE HARD CONSTRAINT, AND IT IS WHY THE LADDER BENDS RATHER THAN THE GATES
================================================================================================

`external` and `canon_touching` are HARD FLAGS in the signal vocabulary. An item carrying either
ALWAYS surfaces, whatever every other signal says, and a derived item inherits them by OR and may
never lower one (`signals.py` rule 2, D00 rule 8, EF-7).

**No delegation level lowers either flag, including level 7.** His own brief on `0439` says this
before anybody built anything: *"a delegation level cannot lower those gates, and where the ladder
appears to say otherwise it is the ladder that has to bend."*

So `delegate` does not mean "do anything without telling me". It means audit-only WITHIN THE
BOUNDARY, and the boundary is those two flags. An agent at level 7 that is about to send, deploy,
spend, publish or propose a change to canon is at level 1 for that act. `surfacing()` below is the
one implementation of that rule and `test_the_ladder.py` walks all seven levels against it.

================================================================================================
WHERE LEVEL 6 LANDS. ASKED AND ANSWERED, NOT ASSUMED
================================================================================================

`inquire` is the level that needs somewhere to put things, and the destination is now RULED rather
than inferred: **the decisions queue**, recorded in
`knowledge/infinity-os-product/decisions/d-inquire-lands-in-decisions.md` on 2026-09-01.

His earlier answer, on 2026-08-31, named the intake room. That answer was not wrong; the QUESTION
was incomplete. The decisions queue did not exist when it was put (migration 52 built it the same
day), so the options he chose between were "intake" and "a new surface" and the third possibility
was never on the page. His own words describing the level in the first place say *"it lands in a
decisions queue to review later"*, so the ruling restores what he said originally.

CONCRETELY, and this matters because the two destinations are different tables reached by
different verbs, so this was never really a one-line change:

    decisions queue   brain.work_item.kind = 'decision', which surfaces through
                      brain.operator_queue WHERE queue = 'decisions'. Written with the `set` verb.
    intake room       brain.objective with origin = 'machine', excluded from
                      brain.objective_waiting so it never reaches his badge. Written with `intake`.

================================================================================================
`inquire` IS THE VISIBILITY ANSWER, AND THAT IS RECORDED HERE RATHER THAN ASSUMED
================================================================================================

**THIS SECTION EXISTS BECAUSE THE PREVIOUS VERSION OF IT ASSUMED THE ANSWER SILENTLY.** The line
that used to sit here said an `inquire` act stays *"OFF his badge. That is exactly the
passive-notification behaviour he described"* -- true, load-bearing, and recorded nowhere. A
standing product posture was being leaned on by a docstring.

The posture, as the repo states it (`web/MUST-NOT-BUILD.md`, item 7 and the line that survives his
2026-08-28 overrule of the badge): **no notification, no push, no sound, no badge anywhere but
intake.** What he changed on 2026-08-28 was the badge, in his own words, and everything else about
item 7 was explicitly NOT overruled. The 7am moment is still "open the page".

So there is no interrupt rung, no digest rung and no archive-only rung, and **none is to be
built**. The visibility axis the vision asks for is answered by the ladder already having a passive
level: `inquire` acts and lands somewhere he browses, `delegate` acts and lands in an audit trail.
Two passive destinations, no push, which is the whole axis the product is willing to have.

Recorded as `knowledge/infinity-os-product/decisions/d-visibility-is-inquire.md`, 2026-09-01.
Building an interrupt rung would contradict a standing decision, so a later reader who wants one is
reading a decision, not a gap.
"""

from __future__ import annotations

#: The seven, in his order, lowest leeway first. The index IS the level: `LADDER[6]` is `inquire`.
#: A tuple rather than a dict so the ORDER is the data structure: this ladder only means anything
#: as a sequence, and a dict would let somebody add an eighth in the middle without noticing.
LADDER = (
    ("tell",     "He decides and says what to do. The AI executes and nothing is its call."),
    ("sell",     "He decides and explains why. The AI executes, and understanding the reason is "
                 "what lets it do the work well rather than literally."),
    ("consult",  "He asks first, then decides. The AI's input is real and the decision is not."),
    ("agree",    "They decide together. Neither acts on this alone."),
    ("advise",   "The AI decides with him advising. The call has moved; his voice has not."),
    ("inquire",  "The AI decides and acts, and it lands where he can ask about it later. A "
                 "PASSIVE inbox he browses when he wants, never a queue that waits on him."),
    ("delegate", "The AI decides and acts, audit trail only. Not worth his attention unless "
                 "something goes wrong."),
)

LEVELS = tuple(name for name, _ in LADDER)
MIN_LEVEL, MAX_LEVEL = 1, len(LADDER)

#: The level at which the AI may act before he sees it. Below this the act waits on him.
FIRST_ACTING_LEVEL = 5          # `advise`. At 4 and below the decision is his or shared.

#: The level at which acting stops being announced at once and becomes browsable later.
FIRST_PASSIVE_LEVEL = 6         # `inquire`

#: WHERE AN `inquire` ACT LANDS. **The decisions queue**, ruled 2026-09-01
#: (`decisions/d-inquire-lands-in-decisions.md`), superseding the 2026-08-31 answer of "intake"
#: which was given before the decisions queue existed.
#:
#: The string is the queue's own name in `brain.operator_queue`, so a surface can match on it
#: without a second vocabulary. A row gets there by carrying `work_item.kind = 'decision'`.
INQUIRE_LANDS_IN = "decisions"

#: The two flags no level moves. Named here rather than inlined so a reader of this module sees
#: the whole rule without opening `signals.py`.
ALWAYS_SURFACES = ("external", "canon_touching")

#: A level nobody set. THE CONSERVATIVE END, and it is level 1 rather than the middle for the
#: reason `signals.py` opens with: defaulting to the middle turns every unassessed relationship
#: into an average one, and here that would mean an agent nobody has granted leeway to acting on
#: his behalf because nothing said it could not.
DEFAULT_LEVEL = 1


class LadderError(ValueError):
    """A level outside the seven, or a name that is not one of his."""


def level_of(name_or_number) -> int:
    """One level, from his word or from its number. Refuses anything else rather than folding.

    A LEVEL IS NOT A SIGNAL AND DOES NOT GET A CONSERVATIVE FOLD. An unreadable signal means
    "nobody assessed this" and the read continues; an unreadable LEVEL means somebody wrote a
    delegation rule that does not say what they think it says, and the only safe answer is to
    refuse it where they can still fix it.
    """
    v = str(name_or_number if name_or_number is not None else "").strip().lower()
    if not v:
        raise LadderError(
            f"no delegation level given. The seven are, lowest leeway first: "
            f"{', '.join(LEVELS)}. Nothing defaults here.")
    if v.isdigit():
        n = int(v)
        if not MIN_LEVEL <= n <= MAX_LEVEL:
            raise LadderError(
                f"delegation level {n} is outside the ladder, which runs {MIN_LEVEL} to "
                f"{MAX_LEVEL}: {', '.join(LEVELS)}.")
        return n
    if v in LEVELS:
        return LEVELS.index(v) + 1
    raise LadderError(
        f"{name_or_number!r} is not a delegation level. The seven are, lowest leeway first: "
        f"{', '.join(LEVELS)}, or 1 to {MAX_LEVEL}.")


def name_of(level) -> str:
    return LEVELS[level_of(level) - 1]


def describes(level) -> str:
    return LADDER[level_of(level) - 1][1]


def may_act_alone(level) -> bool:
    """Does the AI get to make this call at all, before he has seen it?"""
    return level_of(level) >= FIRST_ACTING_LEVEL


#: THE FALLBACK WHEN NOBODY HAS SAID. Not a level anybody chose, and distinguishable from a
#: deliberate level 1 only by `resolve_level`'s `source` field, which is why that field exists.
UNSET_SOURCES = ("item", "agent", "default")


def resolve_level(item_override=None, agent_default=None) -> dict:
    """WHICH LEVEL APPLIES: the item's override, else the acting agent's default, else level 1.

    THE SPECIFICATION IS `outputs/2026-09-01-sprints/S4/RESOLUTION-RULE.md`, WRITTEN BEFORE EITHER
    FIELD EXISTED. This function implements sections 2, 3 and 3a of it and nothing else; the gates
    are section 6 and they live in `surfacing()` for the structural reason restated below.

    THIS FUNCTION IS NOT GIVEN THE HARD FLAGS AND THAT IS THE WHOLE SAFETY PROPERTY. `external` and
    `canon_touching` are not parameters here, so no resolution rule -- and no later edit to one --
    can lower a gate, because this function cannot see one. It answers exactly one question, "how
    much leeway does the acting agent have here", and hands the answer to `surfacing()`, which
    applies the gates on every path. A resolver that took the flags "so it could optimise" would be
    the entire defect this separation exists to make impossible.

    SPECIFICITY WINS, AND THE FALLBACK IS THE CONSERVATIVE END. `None` is not a level and never
    folds to one: it means NOBODY HAS SAID, which is a real state and must stay distinguishable
    from a deliberate level 1. When nobody has said, the answer is level 1 (`tell`) rather than the
    middle, for the reason this module and `signals.py` both open with -- defaulting to the middle
    would let an agent nobody has granted leeway to act on his behalf BECAUSE NOTHING SAID IT COULD
    NOT.

    WHICH AGENT'S DEFAULT the caller passes is settled in RESOLUTION-RULE.md section 3 and it is
    the ACTING agent -- `work_item.claimed_by` -- never the poster and never the assignee.
    Otherwise a high-level agent launders leeway to a low-level one by posting the work, which is
    the same one-hop laundering that D00 rule 8 and EF-7 already forbid for hard flags. Assignment
    is an intention; the claim is the fact. This function takes the resolved default as an argument
    rather than reading the row itself so that it stays pure and the caller's choice of agent is
    visible at the call site rather than buried here.

    UNSET IS NOT UNREADABLE, and the two get different answers:

      unset       `None`, or an agent with no row, or one nobody has graded. A LEGITIMATE state
                  with a defined conservative answer. Returns level 1, source `default`.
      unreadable  a value that is not one of the seven. A BUG in somebody's delegation rule, and
                  `level_of` refuses it out loud where they can still fix it.

    `level_of` is the parser and refuses to guess; this is the policy and has an answer for unset.
    Collapsing them would either make `level_of` fold garbage onto a level or make this crash on
    the ordinary case of an agent nobody has graded.

    Returns the SOURCE as well as the level, and that is not decoration: a surface that says "this
    acted without asking you" and cannot say whether that was the agent's standing leeway or a
    per-item override cannot be argued with. Every other return in this module carries its `why`
    and this one carries its `source` for the same reason.
    """
    if item_override is not None and str(item_override).strip() != "":
        n = level_of(item_override)
        return {
            "level": n, "level_name": LEVELS[n - 1], "source": "item",
            "why": (f"this work item carries an explicit delegation override of {n} "
                    f"({LEVELS[n - 1]}), which is more specific than any agent default and "
                    f"therefore wins."),
        }
    if agent_default is not None and str(agent_default).strip() != "":
        n = level_of(agent_default)
        return {
            "level": n, "level_name": LEVELS[n - 1], "source": "agent",
            "why": (f"this item carries no override, so it takes the ACTING agent's standing "
                    f"level of {n} ({LEVELS[n - 1]}). Resolved fresh at this act rather than "
                    f"stamped at claim, so lowering the agent's level bites work already in "
                    f"flight."),
        }
    return {
        "level": DEFAULT_LEVEL, "level_name": LEVELS[DEFAULT_LEVEL - 1], "source": "default",
        "why": (f"neither the item nor the acting agent carries a level, so nobody has granted "
                f"any leeway here and this falls to {DEFAULT_LEVEL} "
                f"({LEVELS[DEFAULT_LEVEL - 1]}). An unresolvable agent is not a licence, and the "
                f"fallback is the conservative end rather than the middle."),
    }


def resolve(item_override=None, agent_default=None, *,
            external: bool = False, canon_touching: bool = False,
            actor_type: str | None = None) -> dict:
    """THE WHOLE GOVERNANCE ANSWER FOR ONE ACT: which level applies, and when he finds out.

    The one call a surface or a transition should make. It composes the two halves that are kept
    apart on purpose -- `resolve_level` (which cannot see the flags) and `surfacing` (which applies
    them) -- so that no caller has to remember to apply the gates, and no caller CAN skip them.

    THE LADDER DOES NOT GOVERN THE OPERATOR'S OWN WORK. `actor_type='human'` is his own work
    entering his own queue, and a human's act is not delegated. Asked about him the ladder has no
    meaning, and answering "level 1" would be a category error a surface would then render as
    "waiting on approval" for work he is doing himself. RESOLUTION-RULE.md section 7.
    """
    if str(actor_type or "").strip().lower() == "human":
        return {
            "governed": False, "level": None, "level_name": None, "source": None,
            "when": None, "gate": [], "lands_in": None,
            "why": ("this is the operator's own work (`actor_type='human'`). The ladder governs "
                    "how much leeway the AI has and has no meaning asked about him, so no level "
                    "is resolved and nothing here waits on anybody."),
        }
    r = resolve_level(item_override, agent_default)
    s = surfacing(r["level"], external=external, canon_touching=canon_touching)
    return {
        "governed": True,
        "level": r["level"], "level_name": r["level_name"], "source": r["source"],
        "when": s["when"], "gate": s["gate"], "lands_in": s["lands_in"],
        "why": r["why"] + " " + s["why"],
    }


def surfacing(level, *, external: bool = False, canon_touching: bool = False) -> dict:
    """WHEN HE FINDS OUT, and whether the level had any say in it.

    THE ONE IMPLEMENTATION OF THE HARD CONSTRAINT. Returns a dict rather than a string because the
    interesting part is not the answer, it is WHY: a surface that says "this is waiting on you"
    without saying whether that is the ladder's doing or a hard flag's cannot be argued with, and
    the whole point of the ladder is that the relationship is legible.

    Three outcomes:

      `now`      he is told at once. Either the level is below acting, or a hard flag fired.
      `later`    it acted and landed somewhere passive he browses. Level 6.
      `audit`    it acted and nothing surfaces. Level 7, WITHIN THE BOUNDARY.

    THE FLAGS WIN AT EVERY LEVEL INCLUDING SEVEN, and the returned `gate` names which one, so a
    row that surfaced can always be traced to the reason rather than to a mood.
    """
    n = level_of(level)
    gates = [f for f, on in (("external", external), ("canon_touching", canon_touching)) if on]
    if gates:
        return {
            "when": "now", "level": n, "level_name": LEVELS[n - 1],
            "gate": gates,
            "lands_in": None,
            "why": (f"{' and '.join(gates)} always surfaces, at every level including "
                    f"{MAX_LEVEL} ({LEVELS[-1]}). No delegation level lowers a hard flag, so at "
                    f"level {n} ({LEVELS[n - 1]}) this act still waits on him."),
        }
    if n < FIRST_ACTING_LEVEL:
        return {
            "when": "now", "level": n, "level_name": LEVELS[n - 1], "gate": [],
            "lands_in": None,
            "why": (f"level {n} ({LEVELS[n - 1]}) is below {FIRST_ACTING_LEVEL} "
                    f"({LEVELS[FIRST_ACTING_LEVEL - 1]}), so the decision has not moved to the AI "
                    f"and there is nothing to tell him about after the fact."),
        }
    if n < FIRST_PASSIVE_LEVEL:
        return {
            "when": "now", "level": n, "level_name": LEVELS[n - 1], "gate": [],
            "lands_in": None,
            "why": (f"level {n} ({LEVELS[n - 1]}) acts and tells him at once. His voice has not "
                    f"moved even though the call has."),
        }
    if n == FIRST_PASSIVE_LEVEL:
        return {
            "when": "later", "level": n, "level_name": LEVELS[n - 1], "gate": [],
            "lands_in": INQUIRE_LANDS_IN,
            "why": (f"level {n} ({LEVELS[n - 1]}) acts, and the act lands in the "
                    f"{INQUIRE_LANDS_IN} queue for him to review later. PASSIVE: it is listed and "
                    f"waits to be browsed, and nothing about it notifies, pushes or sounds. That "
                    f"is the visibility answer rather than a rung nobody built."),
        }
    return {
        "when": "audit", "level": n, "level_name": LEVELS[n - 1], "gate": [],
        "lands_in": None,
        "why": (f"level {n} ({LEVELS[n - 1]}) acts and nothing surfaces, WITHIN THE BOUNDARY. The "
                f"boundary is {' and '.join(ALWAYS_SURFACES)}: an act carrying either is level 1 "
                f"for that act, however much leeway the agent has otherwise."),
    }


def table() -> list[dict]:
    """The whole ladder as rows, for a surface that wants to render it. One source, not a copy."""
    return [{"level": i + 1, "name": n, "describes": d,
             "acts_alone": may_act_alone(i + 1),
             "he_finds_out": surfacing(i + 1)["when"]}
            for i, (n, d) in enumerate(LADDER)]
