"""Attention's standing note is one press away instead of a dashed box on every visit.

A4 INFINITY-STREAMLINE, ALPHA-SPRINT-1, STREAMLINE item 10 (ruled by CLOSEDOWN-COMMANDER,
2026-09-14). "Inspect is read only; action buttons change your store. Sorting changes this view,
never the working order." sat in a dashed `.ro` box above the table on every load. Both sentences
now live, word for word, in a closed "How this page works" disclosure in the title row. The
inspector keeps its own read-only line, which two committed suites assert, and the ranked-order
note that says a sort never changes the working order still renders beside the table.

Stub read ports on a real Flask app: no console, no store, no browser.
"""

import re
import unittest

from web.tests.test_routines_and_attention_render import an_app

SENTENCE_1 = "Inspect is read only; action buttons change your store."
SENTENCE_2 = "Sorting changes this view, never the working order."
HELP_RE = re.compile(r"<details class=\"att-help\"([^>]*)>(.*?)</details>", re.S)


class TheNoteIsOnePressAway(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        client = an_app().test_client()
        cls.inbox = client.get("/attention/").get_data(as_text=True)
        cls.sorted = client.get("/attention/?sort=title").get_data(as_text=True)
        cls.inspector = client.get("/attention/atn_8801").get_data(as_text=True)

    def test_the_dashed_box_is_gone_from_the_queue(self):
        self.assertNotIn('<div class="ro">', self.inbox)

    def test_one_closed_disclosure_carries_both_sentences_verbatim(self):
        found = HELP_RE.findall(self.inbox)
        self.assertEqual(len(found), 1, found)
        attrs, inner = found[0]
        self.assertNotIn("open", attrs)
        self.assertIn("How this page works", inner)
        text = " ".join(inner.split())
        self.assertIn(SENTENCE_1, text)
        self.assertIn(SENTENCE_2, text)

    def test_the_disclosure_sits_in_the_title_row(self):
        bar = re.search(r'<div class="att-bar">(.*?)</div>', self.inbox, re.S)
        self.assertIsNotNone(bar)
        self.assertIn("<h1>Attention</h1>", bar.group(1))
        self.assertIn('class="att-help"', bar.group(1))

    def test_the_working_order_is_still_stated_beside_the_table(self):
        self.assertIn("Ranked order", self.inbox)
        self.assertIn("not what Infinity does first", self.sorted)

    def test_the_inspector_keeps_its_own_read_only_line(self):
        self.assertIn('inspector &middot; read only.', self.inspector)


if __name__ == "__main__":
    unittest.main()
