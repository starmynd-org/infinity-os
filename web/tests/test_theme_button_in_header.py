"""The theme control sits in the header row and floats over nothing.

A4 INFINITY-STREAMLINE, ALPHA-SPRINT-1, STREAMLINE item 10 (ruled by CLOSEDOWN-COMMANDER,
2026-09-14). The button was `position:fixed` at the viewport foot and covered a card at 375
(STILLS\\attention-inbox__375__dark.png). It now renders inside `<header>`'s `.hrow` as an
ordinary flex item, keeps its id so `console.js` still binds it, and at narrow widths is ordered
onto the first header line beside the mode pill.

The markup half renders through a stub Flask app; the CSS half reads `console.css`. The painted
half (nothing overlaps at 375 and 1440) is the seat's stills, not this file.
"""

import os
import re
import unittest

from web.tests.test_routines_and_attention_render import an_app

WEB_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONSOLE_CSS = os.path.join(WEB_DIR, "static", "console.css")
CONSOLE_JS = os.path.join(WEB_DIR, "static", "console.js")

HEADER_RE = re.compile(r"<header>(.*?)</header>", re.S)
NARROW_RE = re.compile(r"@media\(max-width:899px\)\{(.*?)\n\}", re.S)


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


class TheThemeButtonIsInTheHeader(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.body = an_app().test_client().get("/attention/").get_data(as_text=True)
        header = HEADER_RE.search(cls.body)
        assert header, "the page rendered no <header>, so nothing below is a measurement"
        cls.header = header.group(1)
        cls.after_header = cls.body[header.end():]

    def test_exactly_one_theme_button_renders(self):
        self.assertEqual(self.body.count('id="tb"'), 1)

    def test_it_is_inside_the_header_row(self):
        self.assertIn('class="themebtn" id="tb"', self.header)
        self.assertNotIn('id="tb"', self.after_header)

    def test_console_js_still_binds_it_by_id(self):
        self.assertIn("document.getElementById('tb')", read(CONSOLE_JS))


class TheThemeButtonFloatsOverNothing(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.css = read(CONSOLE_CSS)

    def test_the_rule_is_not_fixed_to_the_viewport(self):
        rule = re.search(r"\.themebtn\{([^}]*)\}", self.css)
        self.assertIsNotNone(rule, "console.css carries no .themebtn rule")
        self.assertNotIn("position:fixed", rule.group(1))
        self.assertIn("flex:0 0 auto", rule.group(1))

    def test_at_narrow_widths_it_joins_the_first_header_line(self):
        # console.css has SEVERAL 899px blocks; the header's is the one that orders the mode pill.
        blocks = [m.group(1) for m in NARROW_RE.finditer(self.css)]
        header_blocks = [b for b in blocks if ".hrow .modepill{order:1;margin-left:auto}" in b]
        print("\n  DENOMINATORS  899px blocks %d, of which order the header %d" % (len(blocks), len(header_blocks)))
        self.assertEqual(len(header_blocks), 1, "the 899px header block that orders the mode pill is gone")
        self.assertIn(".hrow .themebtn{order:1}", header_blocks[0])


if __name__ == "__main__":
    unittest.main()
