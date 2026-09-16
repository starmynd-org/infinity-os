"""The "Awaiting acceptance" tab's key says what its rows are, and old links still work.

A4 INFINITY-STREAMLINE, ALPHA-SPRINT-1, STREAMLINE item 9 (ruled by CLOSEDOWN-COMMANDER,
2026-09-14). The tab read "Awaiting acceptance" and its URL key was `done`: finished work that is
NOT accepted yet, named by the one word that suggests it is over. The key is now `awaiting`, and
`?filter=done` still resolves through `web.views.navigation.FILTER_ALIASES`, so no bookmark or
return link written before the rename breaks.

Stub read ports on a real Flask app: no console, no store, no browser.
"""

import os
import re
import unittest

from web.tests.test_routines_and_attention_render import an_app
from web.views import navigation

WEB_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TABS_RE = re.compile(r'<nav class="att-tabs" aria-label="Attention filters">(.*?)</nav>', re.S)
TAB_RE = re.compile(r'<a href="([^"]+)"\s*(aria-current="page")?\s*>([^<]+)</a>')


def tabs(body):
    block = TABS_RE.search(body)
    assert block, "the Attention filter tabs did not render"
    return [(href, bool(current), label.strip()) for href, current, label in TAB_RE.findall(block.group(1))]


class TheAwaitingTabIsKeyedByItsOwnWord(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.client = an_app().test_client()
        cls.tabs = tabs(cls.client.get("/attention/").get_data(as_text=True))
        print("\n  DENOMINATORS  tabs rendered %d" % len(cls.tabs))

    def test_the_awaiting_acceptance_tab_links_filter_awaiting(self):
        self.assertEqual(len(self.tabs), 6, self.tabs)
        hrefs = {label: href for href, _, label in self.tabs}
        self.assertEqual(hrefs["Awaiting acceptance"], "/attention/?filter=awaiting")

    def test_no_tab_links_the_old_done_key(self):
        self.assertEqual([href for href, _, _ in self.tabs if "filter=done" in href], [])

    def test_an_old_done_link_still_opens_the_awaiting_tab(self):
        response = self.client.get("/attention/?filter=done")
        self.assertEqual(response.status_code, 200)
        current = [label for _, is_current, label in tabs(response.get_data(as_text=True)) if is_current]
        self.assertEqual(current, ["Awaiting acceptance"])

    def test_an_inspector_reached_from_an_old_link_returns_to_the_new_key(self):
        body = self.client.get("/attention/atn_8801?filter=done").get_data(as_text=True)
        self.assertIn("/attention/?filter=awaiting#attention-atn_8801", body)
        self.assertNotIn("filter=done", body)


class TheAliasHasOneHome(unittest.TestCase):

    def test_navigation_maps_done_to_awaiting_and_nothing_else(self):
        self.assertEqual(navigation.FILTER_ALIASES, {"done": "awaiting"})
        self.assertEqual(navigation.context({"filter": "done"})["filter"], "awaiting")
        self.assertEqual(navigation.context({"filter": "nonsense"})["filter"], "queue")
        self.assertIn("awaiting", navigation.FILTERS)
        self.assertNotIn("done", navigation.FILTERS)

    def test_the_store_backed_port_canonicalises_before_its_allowlist(self):
        with open(os.path.join(WEB_DIR, "model.py"), encoding="utf-8") as handle:
            source = handle.read()
        start = source.index("def queue(self, *, offset=0, limit=None, filter_key=")
        body = source[start:start + 2500]
        self.assertIn("filter_key = canonical_filter(filter_key)", body)
        self.assertLess(body.index("canonical_filter(filter_key)"), body.index('raise ValueError("unknown Attention filter")'))


if __name__ == "__main__":
    unittest.main()
