"""The verb proposal is data, so it can be checked before it is a security decision.

R03. These assert properties of what CAP07 is ASKING for, against the console's live allowlist.
The two that matter to a reviewer: the routines surface is not quietly asking for another room's
verbs, and nothing in the proposal can be run today.

WHY IT PARSES `web/rooms.py` INSTEAD OF IMPORTING IT. `import web.rooms` pulls in `store`, which
imports `psycopg2`, which this environment does not have, so an import here would make a pure
proposal check depend on a database driver and would be skipped exactly where it matters. Parsing
reads the same file that ships and needs nothing installed. It is not a second copy of the
allowlist: if the parse finds nothing, the test fails rather than passing over an empty set, which
is this repo's own rule that a verdict over an empty set is not a pass.
"""

import ast
import os
import unittest

from web.blueprints.routines.verbs import (EFFECT_GUARDS, LEAVES_WORKSPACE,
                                           PROPOSED_ROOM_VERBS, proposed_entry)

ROOMS_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "rooms.py")


def live_room_verbs():
    """`ROOM_VERBS` as it stands in the shipped file, read without importing the store."""
    with open(ROOMS_PY, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    for node in tree.body:
        targets = getattr(node, "targets", []) or ([node.target] if hasattr(node, "target") else [])
        for target in targets:
            if isinstance(target, ast.Name) and target.id == "ROOM_VERBS":
                out = {}
                for key, value in zip(node.value.keys, node.value.values):
                    verbs = set()
                    for arg in getattr(value, "args", []):
                        for element in getattr(arg, "elts", []):
                            if isinstance(element, ast.Constant) and isinstance(element.value, str):
                                verbs.add(element.value)
                    out[key.value] = frozenset(verbs)
                return out
    raise AssertionError("ROOM_VERBS was not found in %s; this check has no denominator" % ROOMS_PY)


class TheProposalIsNotYetLive(unittest.TestCase):

    def setUp(self):
        self.live = live_room_verbs()
        # A verdict over an empty set is not a pass: prove the parse actually found the table.
        self.assertGreaterEqual(len(self.live), 3, "parsed too few rooms to trust this check")
        self.assertIn("queue", self.live)

    def test_the_routines_room_is_still_refused_by_the_real_allowlist(self):
        """Shipping this file must not make the room actable. It is a proposal, not an entry."""
        self.assertNotIn("routines", self.live)

    # THE ATTENTION ROOM IS LIVE, AND ITS ALLOWLIST IS PINNED HERE, NOT DENIED. Until 2026-09-12
    # this case asserted `attention` absent from ROOM_VERBS, which was true when it was written and
    # false once the room had its door. The eight are ruled in `web/rooms.py`'s own attention entry
    # and its comments: `answer`, `accept work`, `unaccept work` under R18, `reopen` under the
    # receipt-expectations ruling, `done` under R36, `accept` and `recommend accept` under Andrew's
    # D4, and `recommend reject` under Andrew's 12 September 2026 instruction to represent the
    # existing proposal acts (commit df867ca). A stale absence is not a guard; the exact set is,
    # and an unruled ninth verb goes red here by name (TF-01, 2026-09-13).
    RULED_ATTENTION_VERBS = frozenset({
        "answer", "accept work", "unaccept work", "done", "reopen",
        "accept", "recommend accept", "recommend reject"})

    def test_the_attention_room_holds_exactly_its_ruled_verbs_and_no_other(self):
        self.assertIn("attention", self.live)
        self.assertEqual(self.live["attention"], self.RULED_ATTENTION_VERBS)
        # Not redundant with the equality: a widening of BOTH the file and the constant above
        # stays green on the equality and goes red here, so the constant cannot quietly grow.
        for verb in ("dismiss", "post", "note", "queue draft options", "routine pause"):
            self.assertNotIn(verb, self.live["attention"], verb)
            self.assertNotIn(verb, self.RULED_ATTENTION_VERBS, verb)

    def test_the_proposal_does_not_ask_for_the_attention_room(self):
        # Its verbs being live nowhere is `test_the_proposal_does_not_borrow_another_rooms_verbs`
        # above; this is the other direction, with a denominator so an emptied proposal is red.
        self.assertGreaterEqual(len(PROPOSED_ROOM_VERBS["routines"]), 5)
        self.assertNotIn("attention", PROPOSED_ROOM_VERBS)

    def test_the_proposal_does_not_borrow_another_rooms_verbs(self):
        """The hole this console has already had once: a page taking a verb from another room."""
        for room, verbs in self.live.items():
            overlap = PROPOSED_ROOM_VERBS["routines"] & verbs
            self.assertEqual(overlap, frozenset(), "routines asks for %s from %r" % (overlap, room))


class EveryProposedVerbCarriesItsGuard(unittest.TestCase):

    def test_each_verb_has_a_stated_row_level_guard(self):
        for verb in PROPOSED_ROOM_VERBS["routines"]:
            self.assertIn(verb, EFFECT_GUARDS)
            self.assertTrue(EFFECT_GUARDS[verb].strip(), "%s has an empty guard" % verb)

    def test_no_guard_is_stated_for_a_verb_that_is_not_proposed(self):
        self.assertEqual(set(EFFECT_GUARDS), set(PROPOSED_ROOM_VERBS["routines"]))

    def test_none_of_the_five_verbs_leaves_the_workspace(self):
        """Changing a routine does not send anything. If that stops being true, this fails."""
        self.assertEqual(LEAVES_WORKSPACE, frozenset())
        self.assertEqual(LEAVES_WORKSPACE & PROPOSED_ROOM_VERBS["routines"], frozenset())

    def test_pause_is_permitted_without_an_approval_and_resume_is_not(self):
        self.assertIn("can never need an approval", EFFECT_GUARDS["routine pause"])
        self.assertIn("refuse", EFFECT_GUARDS["routine resume"])

    def test_run_now_reports_its_two_refusals_separately(self):
        self.assertIn("separately", EFFECT_GUARDS["routine run now"])


class TheEntryIsCopyPasteable(unittest.TestCase):

    def test_it_renders_a_line_that_parses_as_python(self):
        line = proposed_entry().strip().rstrip(",")
        namespace = {}
        exec("entry = {%s}" % line, namespace)                            # noqa: S102
        self.assertEqual(set(namespace["entry"]["routines"]), set(PROPOSED_ROOM_VERBS["routines"]))


if __name__ == "__main__":
    unittest.main()
