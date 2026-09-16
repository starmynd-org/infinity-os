"""At 375px the five nav tabs sit on ONE header line, with the tap floor and the tab type unchanged.

A4 INFINITY-STREAMLINE, ALPHA-SPRINT-1, asked by CLOSEDOWN-COMMANDER after slot 5 (2026-09-14):
at 375 the five-tab nav wrapped, "More" alone on a second line and room links on a third, a header
of about 200px on the phone Andrew uses. MEASURED in that slot's still board__375__light.png.

PAINTED, NOT READ OFF THE CASCADE. Chromium loads /attention/ from the no-store harness
(`an_app`) through a route that answers every request from Flask's test client: no store, no
console process, no network. Any request that is not a GET on the harness origin is aborted. The
Inbox tab carries a two-digit badge ("12 waiting"), the widest tab this nav renders, so the line is
measured at its worst.
"""

import os
import re
import unittest
from urllib.parse import urlsplit

from web.tests.test_routines_and_attention_render import an_app


def read_console_css():
    """The stylesheet as it ships, for the assertions whose rendered half cannot discriminate."""
    web_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(web_dir, "static", "console.css"), encoding="utf-8") as handle:
        return handle.read()

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - reported as NOT RUN below
    sync_playwright = None

ORIGIN = "http://console.test"
WIDTHS = (375, 390)
TAP = 44
TAB_REM = 0.72  # `.room{font-size:.72rem}` as it stood at 7f37bd6: the type may not shrink to fit.

MEASURE = """() => {
  const tabs = [...document.querySelectorAll('nav.rooms a.room')];
  const subs = [...document.querySelectorAll('nav.subrooms a')];
  const box = a => a.getBoundingClientRect();
  return {
    labels: tabs.map(a => a.textContent.replace(/\\s+/g, ' ').trim()),
    tops: tabs.map(a => a.offsetTop),
    heights: tabs.map(a => box(a).height),
    widths: tabs.map(a => box(a).width),
    sub_labels: subs.map(a => a.textContent.replace(/\\s+/g, ' ').trim()),
    sub_heights: subs.map(a => box(a).height),
    sub_widths: subs.map(a => box(a).width),
    fonts: tabs.map(a => parseFloat(getComputedStyle(a).fontSize)),
    root_font: parseFloat(getComputedStyle(document.documentElement).fontSize),
    scroll_width: document.documentElement.scrollWidth,
  };
}"""


class FiveTabsOneRow(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if sync_playwright is None:
            raise unittest.SkipTest("NOT RUN: playwright is not importable in this interpreter")
        app = an_app()
        app.jinja_env.globals["intake_waiting"] = lambda: 12
        client = app.test_client()
        aborted = []

        def answer(route):
            request = route.request
            if request.method != "GET" or not request.url.startswith(ORIGIN + "/"):
                aborted.append((request.method, request.url))
                return route.abort()
            parts = urlsplit(request.url)
            path = parts.path + ("?" + parts.query if parts.query else "")
            response = client.get(path)
            headers = {k: v for k, v in response.headers.items() if k.lower() != "content-length"}
            return route.fulfill(status=response.status_code, headers=headers, body=response.get_data())

        cls.seen = {}
        with sync_playwright() as pw:
            try:
                browser = pw.chromium.launch()
            except Exception as exc:  # pragma: no cover - reported as NOT RUN
                raise unittest.SkipTest("NOT RUN: chromium did not launch: %s" % exc)
            for width in WIDTHS:
                ctx = browser.new_context(viewport={"width": width, "height": 812})
                ctx.route("**/*", answer)
                page = ctx.new_page()
                page.goto(ORIGIN + "/attention/", wait_until="load")
                cls.seen[width] = page.evaluate(MEASURE)
                ctx.close()
            browser.close()
        cls.aborted = aborted
        for width in WIDTHS:
            m = cls.seen[width]
            print("\n  MEASURED %dpx  tabs %d  tops %s  heights %s  font %s/%s  scrollWidth %d  labels %s"
                  % (width, len(m["tops"]), m["tops"], [round(h) for h in m["heights"]],
                     sorted(set(m["fonts"])), m["root_font"], m["scroll_width"], m["labels"]))

    def test_the_widest_tab_is_what_was_measured(self):
        for width in WIDTHS:
            labels = self.seen[width]["labels"]
            self.assertEqual(len(labels), 5, (width, labels))
            self.assertEqual(labels[0], "Inbox 12 waiting", width)

    def test_at_375_all_five_tabs_share_one_offsettop(self):
        tops = self.seen[375]["tops"]
        self.assertEqual(len(tops), 5, tops)
        self.assertEqual(len(set(tops)), 1, "at 375 the tabs sit on %d lines: %s" % (len(set(tops)), tops))

    def test_at_390_all_five_tabs_share_one_offsettop(self):
        tops = self.seen[390]["tops"]
        self.assertEqual(len(tops), 5, tops)
        self.assertEqual(len(set(tops)), 1, "at 390 the tabs sit on %d lines: %s" % (len(set(tops)), tops))

    def test_every_tab_keeps_the_44px_tap_floor(self):
        for width in WIDTHS:
            for label, height in zip(self.seen[width]["labels"], self.seen[width]["heights"]):
                self.assertGreaterEqual(round(height), TAP, (width, label, height))

    def test_every_tab_keeps_the_floor_IN_WIDTH_TOO(self):
        """The half this suite was not reading, and it was failing the whole time.

        W3-UX-INFINITY, 2026-09-15, on W3-INFINITY-TEST's IX-R11-07. At 375 `Work` and `More`
        measured 39.8px wide against 44 tall. A fingertip is round, so a target that clears the
        floor in one dimension and misses in the other is a miss; 68 of 72 driveable matrix keys
        failed on it, and this suite reported clean on every run because it read `heights` only.

        The cause was the ITEM and not the layout, measured rather than assumed: every tab's width
        was EXACTLY its text plus its padding (Work 27.0 + 12.8 = 39.8), `min-width` was `auto` on
        every one, and at 375 the nav had 31px of spare room against its items. A nav compressing
        its children has none. `console.css`'s 520px tap block now floors `min-width` alongside
        `min-height`, and carries that arithmetic in its own comment.
        """
        for width in WIDTHS:
            for label, w in zip(self.seen[width]["labels"], self.seen[width]["widths"]):
                self.assertGreaterEqual(round(w), TAP, (width, label, w))

    def test_the_subrooms_keep_the_floor_in_both_dimensions(self):
        """The sibling nav, which passed by label length rather than by rule.

        On the board's own route `Queue` (47.9) and `The board` (73.4) clear 44, so a reading
        taken there could not have failed. On `/sessions` the subroom `Live` measured UNDER the
        floor: the same latent defect actually firing, one short label away on any route.

        Both navs are floored together. A floor that applies to one of two sibling navs is the
        kind of asymmetry that gets tidied away later by someone who cannot find the reason.

        AND THE RENDERED HALF OF THIS CASE CANNOT FAIL ON THIS HARNESS, WHICH IS WHY THE RULE IS
        ASSERTED BESIDE IT. Measured: reverting `console.css` reddens the TABS case by name and
        leaves this one green, because the subrooms this stub renders are `Queue` and `The board`
        at 47.9 and 73.4 and they clear 44 without any floor. A reading that passes whether or not
        the thing it tests exists is not a measurement, so the declaration is read from the
        stylesheet too: remove it and this reddens even where the labels happen to be long.
        """
        for width in WIDTHS:
            m = self.seen[width]
            self.assertTrue(m["sub_labels"], "no subroom links rendered, so this measured nothing")
            for label, h, w in zip(m["sub_labels"], m["sub_heights"], m["sub_widths"]):
                self.assertGreaterEqual(round(h), TAP, ("height", width, label, h))
                self.assertGreaterEqual(round(w), TAP, ("width", width, label, w))
        css = read_console_css()
        # ANCHOR ON THE RULE, NOT ON ITS BLOCK. The first version of this searched
        # `@media(max-width:520px){(.*?)\n\}` and matched the WRONG one: that query appears five
        # times in this stylesheet, and a non-greedy capture from the first hit ran on for 23,682
        # characters and missed the declaration entirely. A pattern that occurs many times is not
        # an address. `.room,.subroom{` occurs once, so it is.
        rule = re.search(r"\.room,\.subroom\{([^}]*)\}", css)
        self.assertIsNotNone(rule, "the nav floor rule `.room,.subroom{...}` is gone")
        self.assertIn("min-width:44px", rule.group(1),
                      "the subrooms' width floor is not declared, so it holds only while the "
                      "labels stay long")
        # And it must stay PHONE-ONLY: the nearest @media opening before it is the 520px one.
        before = css[:rule.start()]
        last_media = before.rfind("@media")
        self.assertNotEqual(last_media, -1, "the nav floor rule sits outside any media query")
        self.assertTrue(css[last_media:].startswith("@media(max-width:520px)"),
                        "the nav floor rule moved out of the 520px block, so it now applies to "
                        "pointers as well: %r" % css[last_media:last_media + 40])

    def test_the_tab_type_did_not_shrink_to_fit(self):
        for width in WIDTHS:
            m = self.seen[width]
            for label, font in zip(m["labels"], m["fonts"]):
                self.assertGreaterEqual(font + 0.01, TAB_REM * m["root_font"], (width, label, font))

    def test_the_page_does_not_scroll_sideways(self):
        for width in WIDTHS:
            self.assertLessEqual(self.seen[width]["scroll_width"], width, width)


if __name__ == "__main__":
    unittest.main()
