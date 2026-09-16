"""A row on /queue/table follows its own id link, and the board still has no form and no new control.

A4 INFINITY-STREAMLINE, ALPHA-SPRINT-1, STREAMLINE item 8 in the form CLOSEDOWN-COMMANDER approved
(2026-09-14): the whole row is a press on the link it already carries. Andrew's
D-INFINITY-UX-RULINGS-1 (2026-09-14) then amended web/MUST-NOT-BUILD.md item 2 condition 3 for a
GET search and a details panel; this file's condition-3 class was MOVED DELIBERATELY in that same
commit, and the search and panel are pinned in test_board_search_and_panel.py.
This file reads the template source, because rendering the board needs a store; the painted
half (a press on a row's title cell arrives at /task/<id>) is the seat's scratch-store stills.
"""

import os
import re
import unittest

WEB_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TABLE = os.path.join(WEB_DIR, "templates", "table.html")


def source():
    with open(TABLE, encoding="utf-8") as handle:
        return handle.read()


def without_comments(text):
    return re.sub(r"\{#.*?#\}", "", text, flags=re.S)


class TheRowFollowsItsLink(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.raw = source()
        cls.live = without_comments(cls.raw)

    def test_the_id_link_is_stretched_over_its_row(self):
        self.assertIn(".tbl tbody tr{position:relative}", self.live)
        self.assertIn('.tbl td.tid a::after{content:"";position:absolute;inset:0}', self.live)

    def test_the_row_still_carries_exactly_one_anchor_the_id_link(self):
        row = re.search(r"\{% for r in rows %\}(.*?)\{% else %\}", self.live, re.S)
        self.assertIsNotNone(row, "the row template is gone")
        anchors = re.findall(r"<a\b[^>]*>", row.group(1))
        self.assertEqual(anchors, ['<a href="{{ r.href }}">'])


class ItemTwosConditionThreeAsAmended(unittest.TestCase):
    """Condition 3 as amended 2026-09-14 (D-INFINITY-UX-RULINGS-1): one GET search form and the
    details panel's script are allowed; no POST form, no inline handler, nothing draggable here.
    Moved deliberately, in the amendment's own commit, from "no form, no input, no script at all"."""

    def test_the_only_form_is_the_get_search(self):
        forms = re.findall(r"<form\b[^>]*>", without_comments(source()))
        self.assertEqual(forms, ['<form class="tsearch" method="get" action="/queue/table" role="search">'])

    def test_no_post_no_inline_handler_nothing_draggable(self):
        live = without_comments(source()).lower()
        for forbidden in ('method="post"', "onclick", "draggable", "<select", "<textarea"):
            self.assertNotIn(forbidden, live, forbidden)

    def test_the_only_script_is_the_details_panel(self):
        scripts = re.findall(r"<script\b[^>]*>", without_comments(source()))
        self.assertEqual(scripts, ["<script src=\"{{ url_for('static', filename='board-panel.js') }}\">"])


if __name__ == "__main__":
    unittest.main()
