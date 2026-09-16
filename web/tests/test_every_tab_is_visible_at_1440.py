"""At 1440 on a page with a row of rooms, every nav tab is inside the nav's visible box.

W5-S7, 2026-09-16. MEASURED on trunk 2341335 in headless chromium: on /attention/ at 1440,
`nav.rooms` had a 185px client box over 363px of tabs, so Projects, Agents and More sat behind the
nav's hidden scrollbar and only "Inbox" and "Work" were on screen. `.subrooms` carries
`flex-basis:100%` to take the line under the tabs, but `.hrow` wrapped only below 900px, so above
it the room row shared the tabs' line and squeezed the nav. `test_five_tabs_one_row_at_375.py`
reads 375 and 390 only, which is why it stayed green.

WHY THE READING IS BOXES AND NOT offsetTop. A clipped tab keeps the same offsetTop as the tabs
that show, so a one-row pin passes over exactly this defect. The case reads, per tab, whether its
box lies inside the nav's box, and whether the nav's scrollWidth equals its clientWidth.

THE CONTROL IS IN THE SAME RUN. The same measurement is taken again with the nav planted narrow
(120px, no wrap, scrolling), and that reading MUST report clipped tabs. If it reports none, the
instrument is blind and the green above it means nothing, so the control case fails by name.

Harness: the no-store `an_app` from test_routines_and_attention_render, fulfilled through a
Playwright route like test_five_tabs_one_row_at_375.py. No store, no console process, no network.
"""

import unittest
from urllib.parse import urlsplit

from web.tests.test_routines_and_attention_render import an_app

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - reported as NOT RUN below
    sync_playwright = None

ORIGIN = "http://console.test"
WIDTH = 1440
PLANT = "nav.rooms{max-width:120px!important;flex:0 0 120px!important;flex-wrap:nowrap!important;overflow-x:auto!important}"

MEASURE = """() => {
  const nav = document.querySelector('nav.rooms');
  const nb = nav.getBoundingClientRect();
  const tabs = [...nav.querySelectorAll('a.room')].map(a => {
    const b = a.getBoundingClientRect();
    return {label: a.textContent.replace(/\\s+/g, ' ').trim(),
            inside: b.left >= nb.left - 0.5 && b.right <= nb.right + 0.5 && b.right <= window.innerWidth + 0.5};
  });
  return {client: nav.clientWidth, scroll: nav.scrollWidth, tabs: tabs,
          rooms_row: document.querySelectorAll('nav.subrooms a').length,
          doc_scroll: document.documentElement.scrollWidth};
}"""


class EveryTabIsVisibleAt1440(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if sync_playwright is None:
            raise unittest.SkipTest("NOT RUN: playwright is not importable in this interpreter")
        app = an_app()
        app.jinja_env.globals["intake_waiting"] = lambda: 12
        client = app.test_client()

        def answer(route):
            request = route.request
            if request.method != "GET" or not request.url.startswith(ORIGIN + "/"):
                return route.abort()
            parts = urlsplit(request.url)
            response = client.get(parts.path + ("?" + parts.query if parts.query else ""))
            headers = {k: v for k, v in response.headers.items() if k.lower() != "content-length"}
            return route.fulfill(status=response.status_code, headers=headers, body=response.get_data())

        with sync_playwright() as pw:
            try:
                browser = pw.chromium.launch()
            except Exception as exc:  # pragma: no cover - reported as NOT RUN
                raise unittest.SkipTest("NOT RUN: chromium did not launch: %s" % exc)
            ctx = browser.new_context(viewport={"width": WIDTH, "height": 900})
            ctx.route("**/*", answer)
            page = ctx.new_page()
            page.goto(ORIGIN + "/attention/", wait_until="load")
            cls.seen = page.evaluate(MEASURE)
            page.add_style_tag(content=PLANT)
            cls.planted = page.evaluate(MEASURE)
            ctx.close()
            browser.close()
        for name, m in (("as shipped", cls.seen), ("planted narrow", cls.planted)):
            print("\n  MEASURED %dpx %s  tabs %d  rooms row %d  nav %d/%d  doc %d  clipped %s"
                  % (WIDTH, name, len(m["tabs"]), m["rooms_row"], m["client"], m["scroll"], m["doc_scroll"],
                     [t["label"] for t in m["tabs"] if not t["inside"]]))

    def test_the_page_measured_has_a_row_of_rooms_and_the_full_strip(self):
        self.assertGreaterEqual(len(self.seen["tabs"]), 5, self.seen["tabs"])
        self.assertGreater(self.seen["rooms_row"], 0, "no row of rooms rendered, so the squeeze was not exercised")

    def test_at_1440_every_tab_is_inside_the_nav_box(self):
        clipped = [t["label"] for t in self.seen["tabs"] if not t["inside"]]
        self.assertEqual(clipped, [], "at 1440 these tabs are hidden behind the nav's scroll: %s (nav %d/%d)"
                         % (clipped, self.seen["client"], self.seen["scroll"]))

    def test_at_1440_the_nav_does_not_scroll(self):
        self.assertEqual(self.seen["scroll"], self.seen["client"], self.seen)

    def test_the_control_a_planted_narrow_nav_is_seen_as_clipped(self):
        self.assertTrue([t for t in self.planted["tabs"] if not t["inside"]],
                        "the planted 120px nav read as unclipped, so this instrument is blind: %s" % self.planted)
        self.assertGreater(self.planted["scroll"], self.planted["client"], self.planted)


if __name__ == "__main__":
    unittest.main()
