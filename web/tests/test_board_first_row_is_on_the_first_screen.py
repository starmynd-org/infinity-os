"""At 375 the board's first row is fully visible without scrolling.

W3-UX-INFINITY, 2026-09-15. This is the REPLACEMENT done-when for audit findings I-02 and I-03 on
the phone, ruled by ALPHA-COMMANDER-2 after the original was measured to be impossible.

THE ORIGINAL TARGET AND WHY IT IS GONE. `docs/ux/audits/INFINITY-STREAMLINE-2026-09-14.md` asked
for "the first row above y 400 at 375". MEASURED on the served board: the shared shell header is
158px, and the first row sits at 540. With the header at ZERO height the first row would still be
at 382, so the whole budget the shell may occupy to reach y400 is 18px. That header carries the
product name, a five-tab nav with a 44px floor, and the room switcher. There is no version of it
under 18px, and no partial cut helps: removing both the subrooms band (46px) and the logo band
(56px) still lands the row at 438.

So y400 was not demanding, it was IMPOSSIBLE, and it was impossible before anyone tried. A
done-when that cannot be met by any correct change to the thing it names is a mis-specified
condition rather than a failing product.

WHAT REPLACES IT, AND WHY THIS ONE IS A REAL BOUNDARY. "The first row is fully visible without
scrolling" is what the audit actually wanted -- its own words were that at 375 the first 812px
"contain the nav, the search, the lede, the chips and the paragraph, and NO ROW". Unlike y400,
something can be on the other side of this line, and something was:

    at e363020, before I-02       first row at 1095, bottom 1150, rows in the first screen: 0
    at this commit                first row at  541, bottom  596, rows in the first screen: 3

Those four numbers are THIS SUITE'S OWN, printed by its `setUpClass` on both trees. An earlier
reading through the desktop browser pane gave 1071/1126 and 540/594 for the same two trees: the
same verdict, a different chromium, a few pixels apart. The suite quotes the instrument that will
re-run rather than the one that happened to measure first, because a docstring citing another
tool's numbers sends the next person hunting a discrepancy that is only two renderers.

A target that could never have failed and a target that could never have passed are the same
defect from opposite ends. This one has failed, in this repository, at a commit that is on origin.

PAINTED, NOT READ OFF THE CASCADE, and the harness is `test_five_tabs_one_row_at_375.py`'s:
chromium loads the board through a Playwright route that answers every request from Flask's test
client. No store, no console process, no network; any request that is not a GET on the harness
origin is aborted and the abort list is asserted empty. The board's rows are the invented fixture
already used by `test_board_opens_as_a_table`, imported rather than copied, because two fixtures
for one surface drift.

NOT RUN rather than skipped quietly: without playwright or chromium this suite says so by name.

WATCHED FAILING. Reverting `web/templates/table.html` and `web/static/console.css` to `e363020`
reddens `test_the_first_row_is_fully_visible_at_375` by name, with the row's bottom at 1150 in an
812 viewport and 0 of 3 rows on the first screen. The control cases stay green across that revert.

Run:  python3 -m unittest web.tests.test_board_first_row_is_on_the_first_screen
"""

import unittest
from unittest import mock
from urllib.parse import urlsplit

from web.tests.test_board_opens_as_a_table import fake_table_view
import web.model as model
from web.app import create_app

try:
    from playwright.sync_api import sync_playwright
except ImportError:                                        # pragma: no cover - NOT RUN below
    sync_playwright = None

ORIGIN = "http://console.test"
WIDTH, HEIGHT = 375, 812

MEASURE = """() => {
  const rows = [...document.querySelectorAll('table.tbl tbody tr')];
  const first = rows[0] ? rows[0].getBoundingClientRect() : null;
  const header = document.querySelector('header');
  const box = a => a.getBoundingClientRect();
  const targets = [
    ...[...document.querySelectorAll('table.tbl thead a.tsort')].map(
      a => ['sort:' + a.textContent.replace(/\\s+/g, ' ').trim(), box(a)]),
    ...[...document.querySelectorAll('table.tbl td.tid a')].map(
      a => ['id:' + a.textContent.trim(), box(a)]),
  ];
  return {
    tapTargets: targets.map(([t, b]) => [t, Math.round(b.width * 10) / 10,
                                            Math.round(b.height * 10) / 10]),
    rows: rows.length,
    firstTop: first ? Math.round(first.top + window.scrollY) : null,
    firstBottom: first ? Math.round(first.bottom + window.scrollY) : null,
    rowsFullyInFirstScreen: rows.filter(
      e => e.getBoundingClientRect().bottom + window.scrollY <= window.innerHeight).length,
    viewport: window.innerHeight,
    headerHeight: header ? Math.round(header.getBoundingClientRect().height) : null,
    scrollWidth: document.documentElement.scrollWidth,
    innerWidth: window.innerWidth,
  };
}"""


class TheFirstRowIsOnTheFirstScreen(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if sync_playwright is None:
            raise unittest.SkipTest("NOT RUN: playwright is not importable in this interpreter")
        cls.patchers = [
            mock.patch.object(model, "table_view", fake_table_view),
            mock.patch.object(model, "queue_view",
                              lambda **kw: {"totals": {"decide": 0}, "tiers": {"decide": []}}),
        ]
        for patcher in cls.patchers:
            patcher.start()
        client = create_app().test_client()
        aborted = []

        def answer(route):
            request = route.request
            if request.method != "GET" or not request.url.startswith(ORIGIN + "/"):
                aborted.append((request.method, request.url))
                return route.abort()
            parts = urlsplit(request.url)
            path = parts.path + ("?" + parts.query if parts.query else "")
            response = client.get(path)
            headers = {k: v for k, v in response.headers.items()
                       if k.lower() != "content-length"}
            return route.fulfill(status=response.status_code, headers=headers,
                                 body=response.get_data())

        with sync_playwright() as pw:
            try:
                browser = pw.chromium.launch()
            except Exception as exc:                       # pragma: no cover - NOT RUN
                raise unittest.SkipTest("NOT RUN: chromium did not launch: %s" % exc)
            ctx = browser.new_context(viewport={"width": WIDTH, "height": HEIGHT})
            ctx.route("**/*", answer)
            page = ctx.new_page()
            page.goto(ORIGIN + "/queue/table", wait_until="load")
            cls.m = page.evaluate(MEASURE)
            ctx.close()
            browser.close()
        cls.aborted = aborted
        print("\n  MEASURED %dx%d  header %spx  first row top %s bottom %s  "
              "rows in first screen %s of %s  scrollWidth %s"
              % (WIDTH, HEIGHT, cls.m["headerHeight"], cls.m["firstTop"], cls.m["firstBottom"],
                 cls.m["rowsFullyInFirstScreen"], cls.m["rows"], cls.m["scrollWidth"]))

    @classmethod
    def tearDownClass(cls):
        for patcher in cls.patchers:
            patcher.stop()

    # ------------------------------------------------------------------------- the done-when

    def test_the_first_row_is_fully_visible_at_375(self):
        """The replacement target. At e363020 this same suite measures the bottom at 1150 in 812."""
        self.assertIsNotNone(self.m["firstBottom"], "the board rendered no rows at all")
        self.assertLessEqual(
            self.m["firstBottom"], self.m["viewport"],
            "the first row's bottom is at %s in a %s viewport, so it needs a scroll"
            % (self.m["firstBottom"], self.m["viewport"]))
        self.assertGreaterEqual(self.m["rowsFullyInFirstScreen"], 1)

    def test_the_tables_own_tap_targets_meet_the_floor_in_both_dimensions(self):
        """The board's sort headers and row id links, added 2026-09-16.

        W3-INFINITY-TEST measured /queue/table at 44 of 64 targets under the floor against a flat
        3-to-8 baseline on every other route, and named these two as what makes it the outlier.
        MEASURED here at 375 before the fix: all twelve were 44px TALL, and six were under 44 WIDE
        -- sort `id` 26.4, `age` 33.3, `lane` 40.1, and all three row id links at 27.8. Same
        one-dimension floor as the nav, same cause: `min-height` declared, `min-width` left `auto`.

        The id links scale with the row count, which is why their route reads 44 and a three-row
        fixture reads 6. The defect is per-link, so a small fixture measures it just as well.

        Both dimensions on every target, because reading one of them is how this survived.
        """
        targets = self.m["tapTargets"]
        self.assertTrue(targets, "no sort links or id links rendered, so this measured nothing")
        under = [(name, w, h) for name, w, h in targets if w < 44 or h < 44]
        self.assertEqual(under, [], "tap targets under the 44px floor: %r" % (under,))

    # ------------------------------------------------- controls: the instrument and the surface

    def test_control_the_tap_target_census_counted_both_kinds(self):
        """A census that silently found only one kind would pass while missing the other.

        Nine sort headers, one per column, and one id link per row.
        """
        names = [t[0] for t in self.m["tapTargets"]]
        self.assertEqual(sum(1 for n in names if n.startswith("sort:")), 9, names)
        self.assertEqual(sum(1 for n in names if n.startswith("id:")), 3, names)

    def test_control_the_fixture_actually_rendered_rows(self):
        """The nonzero beside the zero. A board with no rows satisfies nothing above, and a
        route that silently failed would render none."""
        self.assertEqual(self.m["rows"], 3, "the invented board did not render its three rows")

    def test_control_nothing_was_fetched_from_outside_the_harness(self):
        """No store, no console, no network: any other request would have been aborted."""
        self.assertEqual(self.aborted, [], "requests left the harness: %r" % (self.aborted,))

    def test_control_the_viewport_is_the_phone_one_that_was_asked_for(self):
        """A measurement at the wrong width is a claim about a different screen."""
        self.assertEqual(self.m["innerWidth"], WIDTH)
        self.assertEqual(self.m["viewport"], HEIGHT)

    def test_control_the_page_still_does_not_scroll_sideways(self):
        """The console's standing rule. `.twrap` scrolls in x; the page body never does."""
        self.assertEqual(self.m["scrollWidth"], WIDTH,
                         "the page scrolls sideways: scrollWidth %s at width %s"
                         % (self.m["scrollWidth"], WIDTH))


if __name__ == "__main__":
    unittest.main()
