"""The board at `/queue/table` opens as a table, not as an article.

W3-UX-INFINITY, 2026-09-15. Audit finding I-02 in
`the product factory repository` `docs/ux/audits/INFINITY-STREAMLINE-2026-09-14.md`, the finding that
failed the R16 anti-pattern gate on its own: between the search and the first row the board printed
a 3-line lede, a 5-line coverage paragraph and a boxed 4-line callout, and at 375 the first 812px
held no row at all.

WHAT THIS SUITE PINS, AND WHAT IT DELIBERATELY DOES NOT. It pins the STRUCTURE: that every one of
those sentences still exists word for word, that all of them are behind ONE closed "How this page
works" disclosure, that the surface keeps a short count, and that the board grew no form, no script
and no POST while that happened. It does NOT pin the PIXELS. "search, chips and the table header
within the first 300px at 1440, and the first row above y400 at 375" is the audit's done-when and it
needs a browser over a seeded store, which is the walker harness's job and not this file's. Anyone
reading a green run here has NOT been told the pixel condition is met.

WATCHED FAILING. Reverting `web/templates/table.html` to e363020 reddens this suite by name:
`test_the_three_blocks_are_not_between_the_search_and_the_table`,
`test_one_closed_disclosure_carries_the_prose`, `test_the_count_is_one_short_line`,
`test_the_two_status_lines_share_one_row` and
`test_the_lede_is_inside_the_disclosure_not_above_the_table`. Its four control tests, which assert
what the change must NOT have moved, stay green across the revert -- so a red run here is this
change's red and not the harness falling over.

The route is driven through `create_app()` with `model.table_view` replaced by invented rows, so no
store is read. `create_app()` still asks the store who it is at boot, and the store's default
database is the LIVE `brain`, so this file names a database that cannot exist BEFORE the app is
imported and refuses `brain` outright. Same shape as `test_board_search_and_panel.py`.

Run:  python3 -m unittest web.tests.test_board_opens_as_a_table
"""

import html
import os
import re
import unittest
from unittest import mock

os.environ["BRAIN_PG_DB"] = os.environ.get("W3_BOARD_DB", "brain_w3ux_board_no_such_db")
if os.environ["BRAIN_PG_DB"] == "brain":
    raise SystemExit("test_board_opens_as_a_table: refusing the live store; this suite needs no rows")

import web.model as model  # noqa: E402  (the database name must be set first)
from web.app import create_app  # noqa: E402

WEB_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TABLE = os.path.join(WEB_DIR, "templates", "table.html")

# The three blocks the audit measured, each by a phrase that appears nowhere else on the page.
LEDE = "This is the inventory and not the plan"
COVERAGE = "carry no ranked card"
CALLOUT = "Nothing in the runtime writes"

HELP_RE = re.compile(r"<details class=\"tt-help\"([^>]*)>(.*?)</details>", re.S)
TABLE_OPEN_RE = re.compile(r"<table class=\"tbl\"")
ROW_RE = re.compile(r'<td class="tid">.*?>(\d+)</a>')


ROWS = [
    ("0101", "Renew the Northwind contract", "review", True),
    ("0102", "Draft the pricing FAQ", "work", True),
    # THE UNRANKED ROW IS THE POINT OF THIS FIXTURE. With `enriched` False on one of three rows the
    # coverage arm renders, which is the arm the audit measured as five lines of paragraph.
    ("0103", "Call Aiko about invoice 3107", "work", False),
]


def fake_table_view(queue=None, sort=None, direction="desc", limit=500):
    """`test_board_search_and_panel.py`'s fixture with one row left unranked.

    The route -- not the model -- applies the search and recomputes `shown` and `enriched`, so this
    takes the model's real signature and never sees `q`. `across` is 4 over 3 rows so the overlap
    sentence takes its "more than" arm, and `decisions` is a readable 0 so the callout takes its
    `by construction` arm. Those are the three blocks I-02 measured, all rendered at once.
    """
    rows = []
    for rid, title, qname, enriched in ROWS:
        rows.append({"id": rid, "source_type": "work_item", "title": title, "queue": [qname],
                     "since": None, "age_seconds": 86400.0, "lane": "ops", "state_word": "judge",
                     "impact": None, "impact_text": None, "impact_magnitude": None,
                     "unblocks": None, "prio": 2, "href": "/task/%s" % rid, "enriched": enriched,
                     "impact_is_money": False})
    shown = [r for r in rows if queue is None or queue in r["queue"]]
    counts = {q: {"n": sum(1 for r in rows if q in r["queue"]), "available": True, "why": None}
              for q in model.QUEUES}
    return {"rows": shown, "counts": counts, "across": 4, "decisions_available": True,
            "queue": queue, "sort": None, "dir": "desc", "total": len(rows), "shown": len(shown),
            "enriched": sum(1 for r in shown if r["enriched"]), "sortable": sorted(model.SORTABLE)}


class TheBoardOpensAsATable(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.patchers = [
            mock.patch.object(model, "table_view", fake_table_view),
            mock.patch.object(model, "queue_view",
                              lambda **kw: {"totals": {"decide": 0}, "tiers": {"decide": []}}),
        ]
        for patcher in cls.patchers:
            patcher.start()
        client = create_app().test_client()
        response = client.get("/queue/table")
        assert response.status_code == 200, response.get_data(as_text=True)[:400]
        cls.body = response.get_data(as_text=True)
        cls.narrowed = client.get("/queue/table?q=northwind").get_data(as_text=True)
        cls.source = open(TABLE, encoding="utf-8").read()

    @classmethod
    def tearDownClass(cls):
        for patcher in cls.patchers:
            patcher.stop()

    # ------------------------------------------------------------------ the finding, four ways

    def test_the_three_blocks_are_not_between_the_search_and_the_table(self):
        """I-02's measurement, made into an assertion: nothing explanatory sits above the table.

        The instrument is position, not presence. Each phrase must still be SOMEWHERE on the page
        -- the control below asserts exactly that -- and it must not be in the span that runs from
        the end of the search form to the start of the table.
        """
        start = self.body.index("</form>")
        end = TABLE_OPEN_RE.search(self.body).start()
        between = self.body[start:end]
        for phrase in (LEDE, COVERAGE, CALLOUT):
            self.assertNotIn(phrase, between,
                             "%r still sits between the search and the table" % phrase)

    def test_one_closed_disclosure_carries_the_prose(self):
        found = HELP_RE.findall(self.body)
        self.assertEqual(len(found), 1, "expected exactly one board disclosure, found %d" % len(found))
        attrs, inner = found[0]
        self.assertNotIn("open", attrs, "the disclosure ships open")
        self.assertIn("How this page works", inner)
        for phrase in (LEDE, COVERAGE, CALLOUT):
            self.assertIn(phrase, inner, "%r is not in the disclosure" % phrase)

    def test_the_lede_is_inside_the_disclosure_not_above_the_table(self):
        """The lede's link to the ranked window survives the move, inside the disclosure."""
        _, inner = HELP_RE.findall(self.body)[0]
        self.assertIn('<a href="/queue">the ranked window</a>', inner)

    def test_the_two_status_lines_share_one_row(self):
        """The count and the decisions state are one row of status, not two stacked paragraphs.

        MEASURED in chromium at 1440 on 2026-09-15: stacked, they cost 61px and put the table
        header at 322; on one row the header is at 290, inside the audit's 300px condition. This
        asserts the STRUCTURE that produced that number -- the number itself is in the seat report,
        because a store-free render has no layout.
        """
        row = re.search(r'<div class="tstatus">(.*?)</div>', self.body, re.S)
        self.assertIsNotNone(row, "the status row is gone")
        self.assertEqual(row.group(1).count('<p class="tden">'), 2,
                         "the count and the decisions state are not both in the status row")
        self.assertEqual(self.body.count('<p class="tden">'), 2,
                         "a .tden line escaped the status row")

    def test_the_count_is_one_short_line(self):
        """`8 open` unnarrowed, and the gap's SIZE without the gap's paragraph."""
        line = re.search(r'<p class="tden">(.*?)</p>', self.body, re.S).group(1)
        text = " ".join(html.unescape(re.sub(r"<[^>]+>", "", line)).split())
        self.assertEqual(text, "3 open · 1 not ranked", text)
        self.assertLess(len(text), 60, "the count line is a paragraph again: %r" % text)

    # -------------------------------------------------------------- controls: what must NOT move

    def test_control_every_moved_sentence_is_still_on_the_page(self):
        """The control that makes the first test a MOVE and not a DELETION.

        If this file only asserted absence above the table, deleting all three blocks outright
        would pass it. This is the nonzero beside that zero.
        """
        for phrase in (LEDE, COVERAGE, CALLOUT):
            self.assertIn(phrase, self.body, "%r was deleted rather than moved" % phrase)

    def test_control_the_denominator_survives_when_the_board_is_narrowed(self):
        """A short count must not become a bare count. `test_board_search_and_panel.py:92`
        pins the same string from the other side; this asserts it from inside the finding."""
        self.assertIn("showing <b>1</b> of <b>3</b>", self.narrowed)
        self.assertIn("matching <b>&ldquo;northwind&rdquo;</b>", self.narrowed)

    def test_control_zero_decisions_is_still_distinguished_from_unreadable(self):
        """What `test_the_board_and_the_five_queues.py` check 5 reads out of `innerText`.

        A closed `<details>` renders no text into `innerText`, so this phrase has to be on the
        surface. It is also the assertion that would have caught this change breaking that suite
        silently, on a host with no store, which is where this change was written.
        """
        start = self.body.index("</nav>")
        end = TABLE_OPEN_RE.search(self.body).start()
        surface = self.body[start:end]
        self.assertIn("by construction", surface)
        self.assertNotIn("cannot be read", self.body,
                         "the page reported BOTH a zero count and an unreadable column")

    def test_control_the_board_grew_no_form_no_post_and_no_second_script(self):
        """MUST-NOT-BUILD item 2 condition 3 as amended: one GET search form, one panel script.

        A `<details>` is neither. This is the control that proves the fix did not widen the
        surface to buy the reading improvement.
        """
        self.assertEqual(self.source.count("<form"), 1, "a second form landed on the board")
        self.assertNotIn('method="post"', self.source.lower())
        self.assertEqual(self.source.count("<script"), 1, "a second script landed on the board")
        self.assertNotIn("onclick", self.source.lower())


if __name__ == "__main__":
    unittest.main()
