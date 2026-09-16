"""`/sprint/<slug>` does not scroll the page sideways, at zero rows or at many.

W3-UX-INFINITY, 2026-09-16. Reported by W3-INFINITY-TEST over 18 routes at two phone widths:
`/sprint/current` was the one route whose `document.scrollWidth` exceeded the viewport, at 668
against 375.

THE CAUSE IS ONE MISSING BOX. `detail_sprint.html` carried `<table class="tbl">` and nothing else,
and `.tbl` in `console.css` carries an unconditional `min-width:46rem` (736px), narrowed to
`40rem` (640px) inside the 720px block. So the floor that actually overflows a phone is 640px in a
375px viewport, which is what this suite prints beside its verdict and what the reported
`scrollWidth` of 668 is made of.
That floor was written for the board, whose nine columns sit inside `.twrap`; the `.twrap` comment
says so in as many words. The floor and the box that contains it are one design in two selectors,
and this template adopted the class without the box.

IT OVERFLOWED AT ZERO ROWS, which is why no fixture change could have hidden or caused it. The
only row an unresolved ladder renders is "nothing resolves to this ladder yet". A fixed minimum
overflows an empty table exactly as far as a full one, so the route that exposed the defect is the
one with the LEAST to show. A property can be content-dependent in its visibility and
content-independent in its cause.

WHY THIS SUITE RENDERS THE TEMPLATE AND NOT THE ROUTE. `/sprint/<slug>` runs a raw SQL query
through `store.read()`, so the route needs a database. The DEFECT does not: it is a stylesheet
floor against a viewport, and the template plus the real `console.css` is the whole of it. So the
template is rendered inside an app context with invented items and served to chromium by the same
Playwright route-fulfil harness `test_five_tabs_one_row_at_375.py` uses. No store, no console
process, no network.

BOTH ARMS ARE MEASURED, because the empty one is the finding: the template is rendered with zero
items and with three, and the page must not scroll sideways in either.

WATCHED FAILING. Reverting `web/templates/detail_sprint.html` to `01ee182` reddens
`test_the_page_does_not_scroll_sideways_at_zero_rows` and `..._at_three_rows` by name, at
`scrollWidth` 736 in a 375 viewport. The control cases stay green.

Run:  python3 -m unittest web.tests.test_sprint_does_not_scroll_the_page
"""

import os
import unittest
from unittest import mock
from urllib.parse import urlsplit

os.environ["BRAIN_PG_DB"] = os.environ.get("W3_SPRINT_DB", "brain_w3ux_sprint_no_such_db")
if os.environ["BRAIN_PG_DB"] == "brain":
    raise SystemExit("test_sprint_does_not_scroll_the_page: refusing the live store")

from flask import render_template  # noqa: E402
import web.model as model  # noqa: E402
from web.app import create_app  # noqa: E402

try:
    from playwright.sync_api import sync_playwright
except ImportError:                                    # pragma: no cover - reported NOT RUN
    sync_playwright = None

ORIGIN = "http://console.test"
WIDTH, HEIGHT = 375, 812

ROWS = [
    {"id": "0024", "title": "Renew the Northwind contract", "state": "open",
     "canonical_task": "alpha#0024", "accepted_at": None},
    {"id": "0025", "title": "Draft the pricing FAQ", "state": "judge",
     "canonical_task": "alpha#0025", "accepted_at": "2026-09-01"},
    {"id": "0026", "title": "Call Aiko about invoice 3107", "state": "done",
     "canonical_task": "alpha#0026", "accepted_at": None},
]

MEASURE = """() => {
  const t = document.querySelector('table.tbl');
  const par = t ? t.parentElement : null;
  document.documentElement.offsetHeight;
  return {
    scrollWidth: document.documentElement.scrollWidth,
    innerWidth: window.innerWidth,
    hasTable: !!t,
    rows: t ? t.querySelectorAll('tr').length : 0,
    tableMinWidth: t ? getComputedStyle(t).minWidth : null,
    parentClass: par ? par.className : null,
    parentOverflowX: par ? getComputedStyle(par).overflowX : null,
  };
}"""


class SprintDoesNotScrollThePage(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if sync_playwright is None:
            raise unittest.SkipTest("NOT RUN: playwright is not importable in this interpreter")
        # The shared shell counts the Decide tier on every page, which is a store read. Replaced,
        # like every other store-free suite here, so the only thing under test is the stylesheet
        # floor against the viewport.
        cls.patchers = [mock.patch.object(
            model, "queue_view",
            lambda **kw: {"totals": {"decide": 0}, "tiers": {"decide": []}})]
        for patcher in cls.patchers:
            patcher.start()
        app = create_app()
        pages = {}
        with app.test_request_context("/sprint/alpha"):
            pages["/empty"] = render_template("detail_sprint.html", room="queue",
                                              slug="alpha", items=[])
            pages["/three"] = render_template("detail_sprint.html", room="queue",
                                              slug="alpha", items=ROWS)
        client = app.test_client()
        aborted = []

        def answer(route):
            request = route.request
            if request.method != "GET" or not request.url.startswith(ORIGIN + "/"):
                aborted.append((request.method, request.url))
                return route.abort()
            path = urlsplit(request.url).path
            if path in pages:
                return route.fulfill(status=200, headers={"content-type": "text/html"},
                                     body=pages[path])
            # Static files (console.css above all) come from the real app, so the floor under
            # test is the shipped one and not a copy.
            response = client.get(path)
            headers = {k: v for k, v in response.headers.items()
                       if k.lower() != "content-length"}
            return route.fulfill(status=response.status_code, headers=headers,
                                 body=response.get_data())

        cls.seen = {}
        with sync_playwright() as pw:
            try:
                browser = pw.chromium.launch()
            except Exception as exc:                   # pragma: no cover - reported NOT RUN
                raise unittest.SkipTest("NOT RUN: chromium did not launch: %s" % exc)
            ctx = browser.new_context(viewport={"width": WIDTH, "height": HEIGHT})
            ctx.route("**/*", answer)
            page = ctx.new_page()
            for key in ("/empty", "/three"):
                page.goto(ORIGIN + key, wait_until="load")
                cls.seen[key] = page.evaluate(MEASURE)
            ctx.close()
            browser.close()
        cls.aborted = aborted
        for key, m in cls.seen.items():
            print("\n  MEASURED %-7s %dx%d  scrollWidth %s  rows %s  table min-width %s  "
                  "parent %r overflow-x %s"
                  % (key, WIDTH, HEIGHT, m["scrollWidth"], m["rows"], m["tableMinWidth"],
                     m["parentClass"], m["parentOverflowX"]))

    @classmethod
    def tearDownClass(cls):
        for patcher in getattr(cls, "patchers", []):
            patcher.stop()

    # ------------------------------------------------------------------------- the finding

    def test_the_page_does_not_scroll_sideways_at_zero_rows(self):
        """The arm that matters: a fixed floor overflows an empty table too."""
        m = self.seen["/empty"]
        self.assertEqual(m["scrollWidth"], m["innerWidth"],
                         "the page scrolls sideways at ZERO rows: scrollWidth %s in a %s viewport"
                         % (m["scrollWidth"], m["innerWidth"]))

    def test_the_page_does_not_scroll_sideways_at_three_rows(self):
        m = self.seen["/three"]
        self.assertEqual(m["scrollWidth"], m["innerWidth"],
                         "the page scrolls sideways at three rows: scrollWidth %s in a %s viewport"
                         % (m["scrollWidth"], m["innerWidth"]))

    # ---------------------------------------------------- controls: the instrument and the cause

    def test_control_the_floor_is_still_there_to_be_contained(self):
        """If `.tbl` ever loses its min-width the two assertions above pass for a new reason.

        This keeps them honest: the page must be held by the BOX, not by the floor going away.
        Measured 736px (46rem) at the time of writing.
        """
        for key in ("/empty", "/three"):
            floor = self.seen[key]["tableMinWidth"]
            self.assertIsNotNone(floor)
            self.assertGreater(float(floor.rstrip("px")), float(WIDTH),
                               "%s: .tbl's min-width %s no longer exceeds the viewport, so the "
                               "wrapper is not what is holding the page" % (key, floor))

    def test_control_the_table_is_inside_a_scrolling_box(self):
        """Names the mechanism rather than only its effect."""
        for key in ("/empty", "/three"):
            m = self.seen[key]
            self.assertTrue(m["hasTable"], "%s rendered no table at all" % key)
            self.assertIn("twrap", m["parentClass"] or "",
                          "%s: the table's parent is %r" % (key, m["parentClass"]))
            self.assertIn(m["parentOverflowX"], ("auto", "scroll"),
                          "%s: the wrapper does not scroll in x (%s)"
                          % (key, m["parentOverflowX"]))

    def test_control_the_empty_arm_really_is_empty(self):
        """A zero-row page that quietly rendered rows would make the first case say nothing."""
        self.assertEqual(self.seen["/empty"]["rows"], 2,
                         "expected a header row and the 'nothing resolves' row only")
        self.assertEqual(self.seen["/three"]["rows"], 4)

    def test_control_nothing_was_fetched_from_outside_the_harness(self):
        self.assertEqual(self.aborted, [], "requests left the harness: %r" % (self.aborted,))


if __name__ == "__main__":
    unittest.main()
