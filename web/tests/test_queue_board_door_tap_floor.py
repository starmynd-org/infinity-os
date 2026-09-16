"""The Queue's door to the whole board meets the 44px tap floor on a phone.

A4 INFINITY-STREAMLINE, ALPHA-SPRINT-1, within the ruled phone scope (2026-09-14).
test_phone_surface measured `A.qwindow 19px 'the whole board, sortable'` under the floor at 390
(dark, Decide, deep work off) on a disposable scratch store. The floor rule in queue.html selected
`.chipnote a.qwindow`, and this door sits in `p.qdoor`, so it never applied. This file reads the
template source; the painted half is test_phone_surface itself, in a stills slot.
"""

import os
import re
import unittest

WEB_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUEUE = os.path.join(WEB_DIR, "templates", "queue.html")


def read():
    with open(QUEUE, encoding="utf-8") as handle:
        return handle.read()


class TheBoardDoorMeetsTheTapFloor(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.source = read()

    def test_the_door_is_still_a_qwindow_link_inside_qdoor(self):
        self.assertRegex(self.source, r'<p class="qdoor"><a class="qwindow" href="/queue/table">')

    def test_a_rule_gives_the_qdoor_link_the_tap_floor(self):
        rules = re.findall(r"([^{}]*)\{([^}]*)\}", self.source)
        floored = [sel for sel, body in rules
                   if ".qdoor a.qwindow" in sel and "min-height:var(--tap,44px)" in body]
        self.assertEqual(len(floored), 1, floored)

    def test_the_chipnote_floor_is_kept(self):
        rules = re.findall(r"([^{}]*)\{([^}]*)\}", self.source)
        self.assertTrue(any(".chipnote a.qwindow" in sel and "min-height:var(--tap,44px)" in body
                            for sel, body in rules))


if __name__ == "__main__":
    unittest.main()
