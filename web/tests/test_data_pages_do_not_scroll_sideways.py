"""`/data` and `/data/goals` do not scroll the page sideways at 320, 375 or 390.

W5-S7, 2026-09-16. MEASURED at 4aef1be in headless chromium: at every phone width the Sources
table made `document.scrollWidth` 503 and the Goals-and-KPIs page 538, so the whole page scrolled
sideways and the Last read and Rows columns sat off the right edge. WCAG 2.2 1.4.10 (reflow) lets
a data table scroll in two dimensions, never the page around it. `data.css` now puts each table in
a `.dsscroll` box that carries the overflow, the same answer as the console's `.twrap`.

THE CONTROL IS IN THE SAME RUN. After each reading the box's overflow is planted back to
`visible`, and the page MUST then scroll sideways. That proves two things the green reading alone
cannot: the rows are wide enough to overflow a phone at all (so the green is not a narrow fixture),
and the instrument sees an overflow when there is one.

Harness: `create_app()` with the three store reads replaced by the invented rows of
test_data_section_render.py, fulfilled through a Playwright route. No store, no console process,
no network.
"""

import os
import unittest
from unittest import mock
from urllib.parse import urlsplit

os.environ["BRAIN_PG_DB"] = os.environ.get("W5_DATA_SCROLL_DB", "brain_w5_data_scroll_no_such_db")
if os.environ["BRAIN_PG_DB"] == "brain":
    raise SystemExit("test_data_pages_do_not_scroll_sideways: refusing the live store")

import web.model as model  # noqa: E402
from web.app import create_app  # noqa: E402
from web.tests import test_data_section_render as fx  # noqa: E402

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - reported as NOT RUN below
    sync_playwright = None

ORIGIN = "http://console.test"
WIDTHS = (320, 375, 390)
PAGES = ("/data", "/data/goals")
PLANT = ".dsscroll{overflow-x:visible!important}"

MEASURE = """() => {
  document.documentElement.offsetHeight;
  const tables = [...document.querySelectorAll('table.dstable')];
  return {
    scroll: document.documentElement.scrollWidth,
    inner: window.innerWidth,
    tables: tables.length,
    boxed: tables.filter(t => t.parentElement.classList.contains('dsscroll')
                         && getComputedStyle(t.parentElement).overflowX === 'auto').length,
    widest: Math.max(0, ...tables.map(t => Math.round(t.getBoundingClientRect().width))),
  };
}"""


class DataPagesDoNotScrollSideways(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if sync_playwright is None:
            raise unittest.SkipTest("NOT RUN: playwright is not importable in this interpreter")
        cls.patchers = [mock.patch.object(model, "data_sources_view", lambda: fx.SOURCES_PRESENT),
                        mock.patch.object(model, "goals_and_kpis_view", lambda: fx.GOALS_PRESENT),
                        mock.patch.object(model, "queue_view", fx.fake_queue_view)]
        for patcher in cls.patchers:
            patcher.start()
        client = create_app().test_client()

        def answer(route):
            request = route.request
            if request.method != "GET" or not request.url.startswith(ORIGIN + "/"):
                return route.abort()
            parts = urlsplit(request.url)
            response = client.get(parts.path + ("?" + parts.query if parts.query else ""))
            headers = {k: v for k, v in response.headers.items() if k.lower() != "content-length"}
            return route.fulfill(status=response.status_code, headers=headers, body=response.get_data())

        cls.seen, cls.planted = {}, {}
        with sync_playwright() as pw:
            try:
                browser = pw.chromium.launch()
            except Exception as exc:  # pragma: no cover - reported as NOT RUN
                raise unittest.SkipTest("NOT RUN: chromium did not launch: %s" % exc)
            for width in WIDTHS:
                ctx = browser.new_context(viewport={"width": width, "height": 812})
                ctx.route("**/*", answer)
                page = ctx.new_page()
                for path in PAGES:
                    page.goto(ORIGIN + path, wait_until="load")
                    cls.seen[(path, width)] = page.evaluate(MEASURE)
                    page.add_style_tag(content=PLANT)
                    cls.planted[(path, width)] = page.evaluate(MEASURE)
                ctx.close()
            browser.close()
        for key in sorted(cls.seen):
            m, p = cls.seen[key], cls.planted[key]
            print("\n  MEASURED %-12s %d  scrollWidth %d  tables %d boxed %d  widest %d  |  planted visible: scrollWidth %d"
                  % (key[0], key[1], m["scroll"], m["tables"], m["boxed"], m["widest"], p["scroll"]))

    @classmethod
    def tearDownClass(cls):
        for patcher in getattr(cls, "patchers", []):
            patcher.stop()

    def test_the_pages_measured_carry_their_tables(self):
        for (path, width), m in self.seen.items():
            self.assertEqual(m["tables"], 1 if path == "/data" else 2, (path, width, m))

    def test_the_page_does_not_scroll_sideways_on_a_phone(self):
        wide = [(path, width, m["scroll"]) for (path, width), m in sorted(self.seen.items()) if m["scroll"] > width]
        self.assertEqual(wide, [], "these pages scroll sideways: %s" % wide)

    def test_every_data_table_sits_in_a_scroll_box(self):
        for (path, width), m in self.seen.items():
            self.assertEqual(m["boxed"], m["tables"], (path, width, m))

    def test_the_control_without_the_box_the_page_would_scroll(self):
        still = [(path, width) for (path, width), p in sorted(self.planted.items()) if p["scroll"] <= width]
        self.assertEqual(still, [], "with the box's overflow planted visible these pages still did not scroll, "
                                    "so the fixture is too narrow to prove anything: %s" % still)


if __name__ == "__main__":
    unittest.main()
