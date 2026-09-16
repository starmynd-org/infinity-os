"""Attention's rows open a read-only right-hand panel, and an empty default queue links nowhere.

A4 INFINITY-STREAMLINE, ALPHA-SPRINT-1, under D-ALPHA-UX-1 (2026-09-14): rows plus a side panel,
fewest steps, no store writes. Rendered from stub read ports on a real Flask app, so it needs no
console, no store and no browser. The browser half (what the panel paints at 1440 and that it is
absent at 375) is the seat's stills, not this file.

Each case names the change it pins, so reverting that change turns the case red by name.
"""

import os
import re
import unittest

from web.tests.test_routines_and_attention_render import StubAttentionPort, an_app

WEB_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INBOX = os.path.join(WEB_DIR, "templates", "attention", "inbox.html")
INSPECT = os.path.join(WEB_DIR, "templates", "attention", "inspect.html")
PANEL_JS = os.path.join(WEB_DIR, "static", "attention-panel.js")

ROW_RE = re.compile(r'<tr[^>]*data-queue-row="(?P<id>[^"]+)"(?P<rest>.*?)</tr>', re.S)
PANEL_RE = re.compile(r'<aside\b[^>]*\bdata-att-panel\b[^>]*>', re.S)
INSPECT_LINK_RE = re.compile(r'<a class="att-link" href="(?P<href>[^"]+)" data-att-panel-src="(?P<src>[^"]+)">Inspect</a>')


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


class TheInboxCarriesOneClosedPanel(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.body = an_app().test_client().get("/attention/").get_data(as_text=True)
        cls.rows = ROW_RE.findall(cls.body)
        print("\n  DENOMINATORS  rows rendered %d  panels %d" % (len(cls.rows), len(PANEL_RE.findall(cls.body))))

    def test_exactly_one_panel_renders_closed_and_outside_the_queue_section(self):
        panels = PANEL_RE.findall(self.body)
        self.assertEqual(len(panels), 1, panels)
        self.assertIn(" hidden", panels[0])
        self.assertGreater(self.body.index(panels[0]), self.body.index("</section>"),
                           "the panel sits inside the queue section, where its counts are read")

    def test_the_panel_script_is_loaded_once(self):
        self.assertEqual(self.body.count("/static/attention-panel.js"), 1)

    def test_every_rows_inspect_link_names_its_own_inspector_as_the_panel_source(self):
        self.assertEqual(len(self.rows), 4, "the fixture rows did not render, so this proves nothing")
        for item_id, rest in self.rows:
            links = INSPECT_LINK_RE.findall(rest)
            self.assertEqual(len(links), 1, (item_id, rest[:200]))
            href, src = links[0]
            self.assertEqual(href, src, item_id)
            self.assertIn("/attention/%s?" % item_id, src)


class ThePanelOnlyReads(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.js = read(PANEL_JS)
        cls.inbox = read(INBOX)

    def test_the_script_makes_one_request_and_it_is_a_read(self):
        # The positive control: the same scan finds the write door in the inbox's own script.
        self.assertIn("method: 'POST'", self.inbox, "the scanner's control is gone")
        self.assertEqual(self.js.count("fetch("), 1)
        for forbidden in ("POST", "method", "FormData", "/act"):
            self.assertNotIn(forbidden, self.js, forbidden)

    def test_the_script_never_parses_markup_into_the_page_as_a_string(self):
        for forbidden in (".innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"):
            self.assertNotIn(forbidden, self.js, forbidden)
        self.assertIn("DOMParser", self.js)
        self.assertIn("textContent", self.js)

    def test_the_script_and_the_stylesheet_agree_on_one_breakpoint(self):
        js = re.findall(r"min-width: (\d+)px", self.js)
        css = re.findall(r"@media\(min-width:(\d+)px\)\{[^@]*\.att-panel\{display:block", self.inbox)
        self.assertEqual(len(js), 1, js)
        self.assertEqual(len(css), 1, css)
        self.assertEqual(js, css)
        self.assertIn(".att-panel{display:none}", self.inbox)


class ThePanelWearsTheInspectorsStyles(unittest.TestCase):
    """The inspector's styles live in its own page head, which the panel does not import."""

    def test_every_class_the_inspector_styles_has_a_panel_rule(self):
        style = re.search(r"<style>(.*?)</style>", read(INSPECT), re.S).group(1)
        classes = sorted(set(re.findall(r"\.(att-[a-z-]+)\{", style)))
        print("\n  DENOMINATORS  inspector classes %d" % len(classes))
        self.assertGreaterEqual(len(classes), 5)
        inbox = read(INBOX)
        missing = [c for c in classes if ".att-panel .%s{" % c not in inbox]
        self.assertEqual(missing, [])


class AnEmptyDefaultQueueDoesNotLinkToItself(unittest.TestCase):

    def test_the_default_queue_when_empty_offers_no_link_back_to_itself(self):
        body = an_app(attention_port=StubAttentionPort(items=[])).test_client().get("/attention/").get_data(as_text=True)
        self.assertIn("Nothing needs you", body)
        self.assertNotIn(">Open Queue</a>", body)

    def test_an_empty_filter_still_offers_the_way_back_to_the_queue(self):
        body = an_app(attention_port=StubAttentionPort(items=[])).test_client().get(
            "/attention/?filter=review").get_data(as_text=True)
        self.assertIn(">Open Queue</a>", body)


if __name__ == "__main__":
    unittest.main()
