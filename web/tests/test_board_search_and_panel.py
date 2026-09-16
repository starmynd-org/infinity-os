"""/queue/table's GET search narrows the rendered board, and its details panel lifts only the task
page's existing Accept work and Send back forms.

A4 INFINITY-STREAMLINE, ALPHA-SPRINT-1, Andrew's D-INFINITY-UX-RULINGS-1 ruling 1 (2026-09-14,
relayed by CLOSEDOWN-COMMANDER), amending web/MUST-NOT-BUILD.md item 2 condition 3 in the same
commit.

The route is driven through `create_app()` with `model.table_view` replaced by invented rows, so no
store is read. `create_app()` still asks the store who it is at boot, and the store's default
database is the LIVE `brain`, so this file names a database that cannot exist BEFORE the app is
imported and refuses `brain` outright. The panel's posts are proved only on a disposable scratch
store in the seat's stills slot, never here and never on the live store.
"""

import os
import re
import unittest
from unittest import mock

os.environ["BRAIN_PG_DB"] = os.environ.get("A4_BOARD_DB", "brain_a4_board_no_such_db")
if os.environ["BRAIN_PG_DB"] == "brain":
    raise SystemExit("test_board_search_and_panel: refusing the live store; this suite needs no rows")

import web.model as model  # noqa: E402  (the database name must be set first)
from web.app import create_app  # noqa: E402

WEB_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PANEL_JS = os.path.join(WEB_DIR, "static", "board-panel.js")
TABLE = os.path.join(WEB_DIR, "templates", "table.html")
MUST_NOT_BUILD = os.path.join(WEB_DIR, "MUST-NOT-BUILD.md")

ROWS = [
    ("0101", "Renew the Northwind contract", "review"),
    ("0102", "Draft the pricing FAQ", "work"),
    ("0103", "Call Aiko about invoice 3107", "work"),
]
ROW_RE = re.compile(r'<td class="tid"><a href="/task/(\d+)">')


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def fake_table_view(queue=None, sort=None, direction="desc", limit=500):
    rows = []
    for rid, title, qname in ROWS:
        rows.append({"id": rid, "source_type": "work_item", "title": title, "queue": [qname],
                     "since": None, "age_seconds": 86400.0, "lane": "ops", "state_word": "judge",
                     "impact": None, "impact_text": None, "impact_magnitude": None, "unblocks": None,
                     "prio": 2, "href": "/task/%s" % rid, "enriched": True, "impact_is_money": False})
    shown = [r for r in rows if queue is None or queue in r["queue"]]
    counts = {q: {"n": sum(1 for r in rows if q in r["queue"]), "available": True, "why": None}
              for q in model.QUEUES}
    return {"rows": shown, "counts": counts, "across": None, "decisions_available": True,
            "queue": queue, "sort": None, "dir": "desc", "total": len(rows), "shown": len(shown),
            "enriched": len(shown), "sortable": sorted(model.SORTABLE)}


class TheSearchNarrowsTheBoard(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # Two reads replaced, both with invented data: the board itself, and the shell's mode pill,
        # whose `dispatch_href()` asks `model.queue_view()["totals"]["decide"]` on every page. The
        # intake badge needs nothing: its count fails soft to "? waiting" with no store.
        cls.patchers = [
            mock.patch.object(model, "table_view", fake_table_view),
            mock.patch.object(model, "queue_view",
                              lambda **kw: {"totals": {"decide": 0}, "tiers": {"decide": []}}),
        ]
        for patcher in cls.patchers:
            patcher.start()
        cls.client = create_app().test_client()

    @classmethod
    def tearDownClass(cls):
        for patcher in cls.patchers:
            patcher.stop()

    def page(self, query=""):
        response = self.client.get("/queue/table" + query)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:400])
        return response.get_data(as_text=True)

    def test_without_a_search_every_row_renders(self):
        self.assertEqual(ROW_RE.findall(self.page()), ["0101", "0102", "0103"])

    def test_a_title_word_narrows_the_rows_and_says_so(self):
        body = self.page("?q=northwind")
        self.assertEqual(ROW_RE.findall(body), ["0101"])
        self.assertIn("showing <b>1</b> of <b>3</b>", body)
        self.assertIn("matching <b>&ldquo;northwind&rdquo;</b>", body)
        self.assertIn('name="q" value="northwind"', body)

    def test_an_id_matches_too(self):
        self.assertEqual(ROW_RE.findall(self.page("?q=0103")), ["0103"])

    def test_the_search_keeps_the_queue_tab(self):
        body = self.page("?q=draft&queue=work")
        self.assertEqual(ROW_RE.findall(body), ["0102"])
        self.assertIn('<input type="hidden" name="queue" value="work">', body)

    def test_no_match_says_which_search_found_nothing(self):
        body = self.page("?q=zzzz")
        self.assertEqual(ROW_RE.findall(body), [])
        self.assertIn("Nothing on the board matches <b>&ldquo;zzzz&rdquo;</b>", body)

    def test_the_panel_is_closed_and_its_script_loads_once(self):
        body = self.page()
        panels = re.findall(r"<aside\b[^>]*data-board-panel[^>]*>", body)
        self.assertEqual(len(panels), 1, panels)
        self.assertIn(" hidden", panels[0])
        self.assertEqual(body.count("/static/board-panel.js"), 1)


class ThePanelScriptLiftsOnlyTheTwoForms(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.js = read(PANEL_JS)

    def test_one_request_and_it_is_a_read(self):
        self.assertEqual(self.js.count("fetch("), 1)
        for forbidden in ("POST", "method:", "FormData", ".innerHTML", "insertAdjacentHTML", "outerHTML"):
            self.assertNotIn(forbidden, self.js, forbidden)

    def test_it_lifts_the_task_section_and_rewires_through_the_console(self):
        self.assertIn("section[data-task-page]", self.js)
        self.assertIn("new Event('console:rewire')", self.js)
        self.assertIn("DOMParser", self.js)

    def test_exactly_accept_work_and_send_back_are_kept(self):
        allowed = re.search(r"const ALLOWED = \[([^\]]*)\];", self.js)
        self.assertIsNotNone(allowed)
        self.assertEqual(re.findall(r"'([a-z_]+)'", allowed.group(1)), ["accept_work", "send_back"])
        self.assertIn("form.replaceWith(note)", self.js)

    def test_only_task_rows_open_the_panel_and_other_links_navigate(self):
        # Measured in slot 3b: the first linked row on the seeded board was a question, whose page
        # has no task record, and the panel swallowed a working link. Task hrefs only.
        self.assertIn(r"const TASK_HREF = /^\/task\/[^/?#]+/;", self.js)
        guard = self.js.index("if (!TASK_HREF.test(link.getAttribute('href'))) return;")
        self.assertLess(guard, self.js.index("e.preventDefault();\n    show("),
                        "the task-only guard must run before the press is taken")

    def test_the_script_and_the_stylesheet_agree_on_one_breakpoint(self):
        js = re.findall(r"min-width: (\d+)px", self.js)
        css = re.findall(r"@media\(min-width:(\d+)px\)\{[^@]*\.bp-panel\{display:block", read(TABLE))
        self.assertEqual(len(js), 1)
        self.assertEqual(js, css)


class ItemTwoIsAmendedInWriting(unittest.TestCase):

    def test_condition_three_carries_the_dated_amendment(self):
        text = read(MUST_NOT_BUILD)
        self.assertIn("CONDITION 3 NOW ALLOWS A SEARCH AND A DETAILS PANEL", text)
        self.assertIn("AMENDED 2026-09-14 BY THE OPERATOR (D-INFINITY-UX-RULINGS-1", text)
        self.assertIn("no other form is\n> lifted into the panel", text)


if __name__ == "__main__":
    unittest.main()
