#!/usr/bin/env python3
"""ROW 0439, HIS ASK 8: the delegation ladder, and the two gates none of its levels move.

THE LOAD-BEARING TEST IS `test_no_level_lowers_a_hard_flag`. Everything else here is arithmetic
about seven names; that one is the property his own brief demanded before anybody built anything:

    "a delegation level cannot lower those gates, and where the ladder appears to say otherwise it
    is the ladder that has to bend."

So it is not asserted for level 7 and assumed for the rest. It is walked across ALL SEVEN LEVELS
times ALL FOUR flag combinations, and the denominator is printed, because "no level lowers a flag"
is also what this check prints if it iterates an empty ladder.

FOUR CLAIMS.

  1. THE NAMES ARE HIS. tell, sell, consult, agree, advise, inquire, delegate, in that order. A
     plan written later proposed seven different ones; his words govern, and this asserts the
     exact sequence so a rename has to be deliberate.

  2. NO LEVEL LOWERS A HARD FLAG, at every level and every combination.

  3. LEVEL 6 IS PASSIVE AND LEVEL 7 IS SILENT, and they are different. `inquire` lands somewhere
     he browses; `delegate` lands nowhere. Conflating them is the failure his own description
     rules out: level 6 is *"not a review queue"* and must not become one.

  4. AN UNREADABLE LEVEL IS REFUSED, not folded. A signal folds conservative because unassessed
     work still has to be ranked; a delegation level that cannot be read means somebody wrote a
     rule that does not say what they think it says, and the safe answer is to refuse it where
     they can still fix it.

Needs no database, no console and no browser.

Run: python3 engine/tests/test_the_ladder.py
"""

from __future__ import annotations

import os
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from swarm_engine import delegation as d                              # noqa: E402
from swarm_engine.signals import HARD_FLAGS                           # noqa: E402

PASS, FAIL = 0, 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ok    {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def refuses(fn, *a, **kw) -> str:
    try:
        fn(*a, **kw)
        return ""
    except Exception as e:                                            # noqa: BLE001
        return str(e)


# ------------------------------------------------------------------ 1. his names, his order

def test_the_names_are_his_and_in_his_order():
    """From row 0439, 2026-08-28: tell, sell, consult, agree, then AI decides with the human
    advising, then INQUIRE, then DELEGATE."""
    want = ("tell", "sell", "consult", "agree", "advise", "inquire", "delegate")
    check("the ladder is his seven words, in his order", d.LEVELS == want, str(d.LEVELS))
    check("and it is exactly seven", len(d.LADDER) == 7 == d.MAX_LEVEL, str(len(d.LADDER)))
    check("a name resolves to its number", d.level_of("inquire") == 6, str(d.level_of("inquire")))
    check("and a number to its name", d.name_of(7) == "delegate", d.name_of(7))
    check("every level has a description in his terms, not a placeholder",
          all(len(x[1]) > 30 for x in d.LADDER), str([x[0] for x in d.LADDER if len(x[1]) <= 30]))


# ------------------------------------------------------------------ 2. THE ONE THAT MATTERS

def test_no_level_lowers_a_hard_flag():
    """WALKED, NOT ASSUMED. Seven levels times four flag combinations, and the denominator printed.

    His brief on 0439 named this before the build existed, and it is the sentence the whole ladder
    has to bend around: an act that is external or canon-touching surfaces at EVERY level,
    including 7."""
    combos = [(True, False), (False, True), (True, True)]
    seen, escaped = 0, []
    for level in range(d.MIN_LEVEL, d.MAX_LEVEL + 1):
        for ext, canon in combos:
            seen += 1
            got = d.surfacing(level, external=ext, canon_touching=canon)
            if got["when"] != "now":
                escaped.append(f"level {level} ({d.name_of(level)}) ext={ext} canon={canon} "
                               f"-> {got['when']}")
    check(f"no flagged act escapes, over {seen} level and flag combinations", not escaped,
          "; ".join(escaped))
    check(f"  and the denominator is real: {seen} = {d.MAX_LEVEL} levels x {len(combos)} combos",
          seen == d.MAX_LEVEL * len(combos), str(seen))

    # THE POSITIVE CONTROL. "Nothing escaped" is also what this prints if `surfacing` returned
    # "now" for everything, which would make the ladder meaningless rather than safe.
    unflagged = [d.surfacing(l)["when"] for l in range(d.MIN_LEVEL, d.MAX_LEVEL + 1)]
    check("  positive control: WITHOUT a flag the levels differ, so the check above is not "
          "passing because everything surfaces",
          len(set(unflagged)) >= 3, str(unflagged))

    # AND THE REASON IS ALWAYS NAMED. A row that surfaced has to be traceable to which gate did it.
    top = d.surfacing(d.MAX_LEVEL, external=True)
    check("the refusal at the TOP of the ladder names the gate rather than the level",
          top["gate"] == ["external"] and "every level" in top["why"], str(top))
    check("and the two gates are the vocabulary's own, not a second list",
          set(d.ALWAYS_SURFACES) == set(HARD_FLAGS), f"{d.ALWAYS_SURFACES} vs {HARD_FLAGS}")


# ------------------------------------------------------------------ 3. passive is not silent

def test_level_six_is_passive_and_level_seven_is_silent():
    """His own description rules out the confusion: level 6 is decide-now, ask-whenever, and it is
    NOT a review queue. Level 7 surfaces nothing at all. If those two ever return the same answer,
    the ladder has six rungs and one of them is mislabelled."""
    six, seven = d.surfacing(6), d.surfacing(7)
    check("level 6 lands somewhere he can browse", six["when"] == "later", str(six))
    check("  and names where", bool(six["lands_in"]), str(six["lands_in"]))
    check("level 7 lands nowhere", seven["when"] == "audit" and seven["lands_in"] is None,
          str(seven))
    check("the two are different answers", six["when"] != seven["when"])
    # LEVEL 6 LANDS IN THE DECISIONS QUEUE, ruled 2026-09-01
    # (`decisions/d-inquire-lands-in-decisions.md`), superseding the 2026-08-31 answer of "intake"
    # which was given before the decisions queue existed. It is still PASSIVE: a listed row he
    # browses, with no notification, no push and no sound, which is the standing posture item 7 of
    # `web/MUST-NOT-BUILD.md` retains after his 2026-08-28 overrule of the intake badge.
    check("level 6 lands in the decisions queue, per the 2026-09-01 ruling",
          six["lands_in"] == "decisions", str(six["lands_in"]))
    check("  and the constant is the queue's own name in brain.operator_queue, not a second "
          "vocabulary a surface would have to translate",
          d.INQUIRE_LANDS_IN == "decisions", d.INQUIRE_LANDS_IN)
    check("and levels 1 to 4 tell him NOW, because the decision has not moved",
          all(d.surfacing(l)["when"] == "now" for l in (1, 2, 3, 4)),
          str([d.surfacing(l)["when"] for l in (1, 2, 3, 4)]))
    check("while 5 acts and tells him at once",
          d.surfacing(5)["when"] == "now" and d.may_act_alone(5), str(d.surfacing(5)))
    check("  and 4 does not act alone at all", not d.may_act_alone(4))


# ------------------------------------------------------------------ 4. refused, not folded

def test_an_unreadable_level_is_refused():
    for bad in ("", "  ", None, "0", "8", "99", "supervise", "garbage", "-1"):
        msg = refuses(d.level_of, bad)
        check(f"{bad!r} is refused", bool(msg), "it was ACCEPTED")
        if msg:
            check(f"  and the refusal for {bad!r} lists the seven",
                  "tell" in msg and "delegate" in msg, msg[:80])
    check("the default for a relationship nobody has set is the CONSERVATIVE end, not the middle",
          d.DEFAULT_LEVEL == 1, str(d.DEFAULT_LEVEL))


def test_the_table_is_one_source_and_not_a_copy():
    t = d.table()
    check(f"the table has one row per level ({len(t)} of {d.MAX_LEVEL})", len(t) == d.MAX_LEVEL)
    check("and its names come from the ladder rather than being retyped",
          [r["name"] for r in t] == list(d.LEVELS), str([r["name"] for r in t]))
    check("and every row says when he finds out",
          all(r["he_finds_out"] in ("now", "later", "audit") for r in t), str(t))


# ------------------------------------------------------------------ 6. the resolution rule

def test_the_resolution_rule_and_its_edge_cases():
    """THE ONE THING MOST LIKELY TO GO WRONG, walked rather than asserted.

    Specified in `outputs/2026-09-01-sprints/S4/RESOLUTION-RULE.md` BEFORE either field was added,
    which is the order that matters: a resolution rule invented while wiring the column is a rule
    shaped by whichever call site happened to be written first."""
    # SPECIFICITY WINS.
    check("an item override beats the agent default",
          d.resolve_level(2, 6)["level"] == 2 and d.resolve_level(2, 6)["source"] == "item")
    check("  in the other direction too, so it is precedence and not a min()",
          d.resolve_level(7, 1)["level"] == 7, str(d.resolve_level(7, 1)))
    check("an item with no override takes the acting agent's default",
          d.resolve_level(None, 6)["level"] == 6 and d.resolve_level(None, 6)["source"] == "agent")

    # UNSET IS NOT UNREADABLE, and the conservative end is 1 rather than the middle.
    ungraded = d.resolve_level(None, None)
    check("nobody having said falls to level 1, the conservative end, not the middle",
          ungraded["level"] == 1 and ungraded["source"] == "default", str(ungraded))
    check("  and a DELIBERATE level 1 is distinguishable from it by `source`",
          d.resolve_level(1, None)["source"] == "item", str(d.resolve_level(1, None)))
    check("  an empty string is unset too, not a parse error, because a NULL column reads that way",
          d.resolve_level("", "")["source"] == "default", str(d.resolve_level("", "")))

    # AN UNREADABLE LEVEL IS STILL REFUSED. The policy has an answer for unset; the parser still
    # refuses garbage, and collapsing the two would fold somebody's bug onto a level.
    for bad in ("8", "0", "supervise", "-1"):
        check(f"  but {bad!r} is still REFUSED rather than folded to a level",
              bool(refuses(d.resolve_level, bad, None)), repr(bad))

    # THE HARD GATES, OVER THE RESOLVED LEVEL. A level-7 agent carrying a flag is at level 1 FOR
    # THAT ACT, whichever side the 7 came from.
    seen, escaped = 0, []
    for override in (None, 7):
        for default in (None, 7):
            for ext, canon in ((True, False), (False, True), (True, True)):
                seen += 1
                got = d.resolve(override, default, external=ext, canon_touching=canon)
                if got["when"] != "now":
                    escaped.append(f"override={override} default={default} ext={ext} "
                                   f"canon={canon} -> {got['when']}")
    check(f"no RESOLVED level escapes a hard flag, over {seen} combinations", not escaped,
          "; ".join(escaped))
    check(f"  and the denominator is real: {seen} = 2 overrides x 2 defaults x 3 flag combos",
          seen == 12, str(seen))

    # THE POSITIVE CONTROL. Without a flag those same combinations must NOT all read `now`, or
    # the check above is passing because everything surfaces.
    unflagged = {d.resolve(o, dd)["when"] for o in (None, 7) for dd in (None, 7)}
    check("  positive control: WITHOUT a flag the same combinations DIFFER, so the check above is "
          "not passing because everything surfaces",
          len(unflagged) >= 2, str(sorted(unflagged)))

    # THE GATE NAMES ITSELF at the top of the ladder, over a RESOLVED level rather than a passed one.
    top = d.resolve(7, None, external=True)
    check("the refusal at level 7 names the gate rather than the level",
          top["gate"] == ["external"] and top["level"] == 7, str(top))

    # THE RESOLVER CANNOT SEE THE FLAGS, and that is the structural half of the constraint: a
    # function that is not given them cannot be argued into lowering one.
    params = set(inspect.signature(d.resolve_level).parameters)
    check("resolve_level is NOT GIVEN the hard flags, which is why it cannot lower one",
          not (params & {"external", "canon_touching"}), str(sorted(params)))

    # HIS OWN WORK IS NOT GOVERNED. This is the half migration 56 cannot prove, because the store
    # refuses to let a migration forge `actor_type='human'` at all.
    mine = d.resolve(None, 7, external=True, actor_type="human")
    check("the ladder does not govern the operator's own work",
          mine["governed"] is False and mine["when"] is None, str(mine))
    check("  while an ordinary row IS governed, so `governed` is not simply false for everything",
          d.resolve(None, 7)["governed"] is True)


def main() -> int:
    print(__doc__.splitlines()[0])
    for fn in (test_the_names_are_his_and_in_his_order,
               test_no_level_lowers_a_hard_flag,
               test_level_six_is_passive_and_level_seven_is_silent,
               test_an_unreadable_level_is_refused,
               test_the_table_is_one_source_and_not_a_copy,
               test_the_resolution_rule_and_its_edge_cases):
        print(f"\n{fn.__name__}")
        fn()
    # DENOMINATOR. A run where every scene returned early would otherwise print `0 passed, 0
    # failed` and exit 0, which is the green this repo's doctrine refuses. Task 0292's rule.
    if PASS + FAIL == 0:                                                # DENOMINATOR
        print("\n0 assertions made. A verdict over an empty set is not a pass.")
        return 2
    print(f"\n{PASS} passed, {FAIL} failed  (over 6 scenes)")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
