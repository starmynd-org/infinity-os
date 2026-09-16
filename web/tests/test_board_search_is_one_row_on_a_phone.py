"""At a phone width the board's search is one row, and the queue chips keep the 44px tap floor.

W3-UX-INFINITY, 2026-09-15, audit finding I-03 (density), step 1. At 375 the search form's 14rem
flex basis is wider than what is left beside its label and its button, so the row wrapped to THREE
lines and the search alone cost 94px above the first row. An 8rem basis at the board's existing
720px breakpoint puts label, field and button back on one line.

MEASURED in chromium, two trees served side by side, `e363020` against this change:

    search form height     94px -> 44px
    table header at        1017 -> 487
    first row at           1071 -> 541

THE LABEL STAYS VISIBLE, and that is the part worth pinning rather than the pixels. Hiding it and
leaning on the placeholder would have bought another ~95px; a placeholder is not a label. This
suite asserts the label is still rendered and still bound to the input, so the cheap version of
this fix cannot be introduced later without reddening a case by name.

WHAT THIS SUITE DOES NOT CLAIM. It does not claim the audit's 375 done-when is met. It is not:
the first row is at 541 against a target of 400, and the arithmetic says no change inside this
finding can reach it, because the shared shell header alone is 159px. That is a separate,
single-owner change and it is not in here.

AND THE CHIP FLOOR IS PINNED HERE BECAUSE NOTHING ELSE PINS IT. Checked rather than assumed:
`test_queue_board_door_tap_floor.py` selects `.qdoor a.qwindow` and `.chipnote a.qwindow`, which is
the door FROM the ranked window TO the board, and `test_five_tabs_one_row_at_375.py` selects
`nav.rooms a.room`, which is the shell nav. Neither touches `nav.tqs a.tq`. A pin on a different
element stays green while this one breaks, so the board's own chips get their own.

A RELATED CHANGE WAS CONSIDERED AND REFUSED, recorded so nobody re-proposes it. Putting the six
chips on one scrolling row would buy another ~50px, and `console.css` already argues against it in
writing at the 720px block: the tabs wrap instead of scrolling sideways because "a second
horizontal scrollbar above the table's own would be two different swipe gestures stacked on one
screen", and `.twrap` is "the only element on this surface allowed to scroll in x". So this suite
also asserts the chips still WRAP and the body still does not scroll sideways.

WATCHED FAILING. Reverting `web/templates/table.html` to `e96bbd6` reddens
`test_the_search_is_one_row_at_a_phone_width` by name. The control cases stay green across that
revert.

Run:  python3 -m unittest web.tests.test_board_search_is_one_row_on_a_phone
"""

import os
import re
import unittest

WEB_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TABLE = os.path.join(WEB_DIR, "templates", "table.html")
CONSOLE_CSS = os.path.join(WEB_DIR, "static", "console.css")


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


class TheSearchIsOneRowOnAPhone(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.table = read(TABLE)
        cls.css = read(CONSOLE_CSS)

    # ---------------------------------------------------------------------------- the finding

    def test_the_search_is_one_row_at_a_phone_width(self):
        """The narrow override exists, is at the board's own breakpoint, and shrinks the basis.

        The pixel proof is a browser measurement recorded in the seat report; what a store-free
        suite can hold is that the rule is present and says what it must. Keyed to the basis
        rather than to a rendered height, because a height is a number this file cannot see.
        """
        narrow = re.search(
            r"@media\(max-width:720px\)\{\s*\.tsearch input\[name=\"q\"\]\{([^}]*)\}",
            self.table)
        self.assertIsNotNone(narrow, "the board's narrow search override is gone")
        self.assertIn("flex:1 1 8rem", narrow.group(1))
        wide = re.search(r"\.tsearch input\[name=\"q\"\]\{flex:1 1 (\d+)rem", self.table)
        self.assertIsNotNone(wide)
        self.assertGreater(int(wide.group(1)), 8,
                           "the wide basis is no longer wider than the narrow one, so the "
                           "override is doing nothing")

    # ------------------------------------------------- controls: what must NOT have been traded

    def test_control_the_search_label_is_still_visible_and_still_bound(self):
        """The ~95px this fix deliberately did not take.

        A placeholder disappears the moment someone types and is not reliably announced. If a
        later change hides the label to buy the space, this reddens.
        """
        self.assertIn('<label for="tq">Search the board</label>', self.table)
        self.assertIn('id="tq" name="q"', self.table)
        for hidden in ("display:none", "visibility:hidden", "clip:rect(0"):
            self.assertNotIn(".tsearch label{%s" % hidden, self.table.replace(" ", ""))

    def test_control_the_queue_chips_keep_the_44px_tap_floor_in_BOTH_dimensions(self):
        """Nothing else pins this, and the first version of it read only ONE dimension.

        W3-INFINITY-TEST found the SHARED shell nav 44px tall and 39.8px wide: a near miss that a
        height-only check reports clean on every route. This assertion had exactly that shape.

        It is pinned at the RULE and not at a rendered width on purpose. Measured in chromium at
        375, the narrowest chip ("work 2") is 58.8px wide, so an assertion over this fixture's
        data would pass whether or not the floor existed and could not have failed. The rule is
        what makes it true for a queue name nobody has added yet.
        """
        self.assertRegex(self.css, r"\.tq\{[^}]*min-height:var\(--tap\)")
        self.assertRegex(self.css, r"\.tq\{[^}]*min-width:var\(--tap\)")
        self.assertRegex(self.css, r"--tap:\s*44px")

        # THE NARROW BLOCK IS FOUND BY ITS CONTENT, NOT BY ITS QUERY, and the first version of
        # this was vacuous because of that. `@media(max-width:720px){` occurs THREE times in
        # console.css; `re.search` took the first, which is the queue grid's block at :1908 and
        # has never contained a `.tq` rule at all. So the four assertions below were asking
        # whether a block that never mentions `.tq` shrinks `.tq`, and could not have failed.
        # The board's block is at :2314. Filter every block by the signature, require exactly
        # one, and the address is then the content rather than the query.
        blocks = [m.group(1) for m in
                  re.finditer(r"@media\(max-width:720px\)\{(.*?)\n\}", self.css, re.S)]
        boards = [b for b in blocks if ".tq{" in b]
        self.assertEqual(len(boards), 1,
                         "expected exactly one 720px block styling .tq, found %d of %d blocks"
                         % (len(boards), len(blocks)))
        for prop in ("min-height", "height:", "min-width", "width:"):
            self.assertNotRegex(boards[0], r"\.tq\{[^}]*" + re.escape(prop),
                                "the narrow block shrinks .tq's %s, breaching the floor" % prop)

    def test_control_the_chips_still_wrap_and_never_scroll_sideways(self):
        """The change this finding considered and refused, kept refused.

        `console.css`'s own 720px block argues it in writing: two horizontal swipe gestures
        stacked on one screen, and `.twrap` is the only element on this surface allowed to
        scroll in x.
        """
        self.assertRegex(self.css, r"\.tqs\{[^}]*flex-wrap:wrap")
        tqs = re.search(r"\.tqs\{([^}]*)\}", self.css)
        self.assertIsNotNone(tqs)
        self.assertNotIn("overflow", tqs.group(1))
        self.assertNotIn("nowrap", tqs.group(1))

    def test_control_the_search_is_still_the_one_get_form(self):
        """MUST-NOT-BUILD item 2 condition 3 as amended. A CSS change must not widen the surface."""
        self.assertEqual(self.table.count("<form"), 1)
        self.assertNotIn('method="post"', self.table.lower())
        self.assertIn('<form class="tsearch" method="get" action="/queue/table" role="search">',
                      self.table)


if __name__ == "__main__":
    unittest.main()
