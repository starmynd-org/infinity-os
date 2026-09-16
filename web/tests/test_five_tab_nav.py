"""The shell nav is five tabs with a row of rooms, every room is still one click from its tab,
the intake badge rides the first tab, and the Chats and Terminal holds stay.

A4 INFINITY-STREAMLINE, ALPHA-SPRINT-1, Andrew's D-INFINITY-UX-RULINGS-1 ruling 2 (2026-09-14,
relayed by CLOSEDOWN-COMMANDER). web/MUST-NOT-BUILD.md item 11 carries the dated amendment. The
painted half (390 and 1440, the badge on screen, the browser censuses in test_the_intake_surface,
test_the_keyboard_and_the_phone and test_the_order_and_the_filters) is proved on the seat's
disposable scratch store.
"""

import os
import re
import unittest

from web.tests.test_routines_and_attention_render import an_app

WEB_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MUST_NOT_BUILD = os.path.join(WEB_DIR, "MUST-NOT-BUILD.md")

TABS = [("Inbox", "/attention/"), ("Work", "/queue"), ("Projects", "/board"), ("Agents", "/fleet"),
        ("More", "/brief")]
ROOMS = {"/attention/", "/intake", "/queue", "/queue/table", "/board", "/fleet", "/live", "/sessions",
         "/brief", "/scope", "/study"}

HEADER_RE = re.compile(r"<header>(.*?)</header>", re.S)
ROOMS_NAV_RE = re.compile(r'<nav class="rooms" role="tablist">(.*?)</nav>', re.S)
TAB_RE = re.compile(r'<a class="room" role="tab" href="([^"]+)" data-nav-group="[a-z]+"\s+'
                    r'aria-selected="(true|false)">([A-Za-z]+)(.*?)</a>', re.S)
SUB_RE = re.compile(r'<nav class="subrooms" aria-label="([^"]+)">(.*?)</nav>', re.S)
SUBLINK_RE = re.compile(r'<a class="subroom" href="([^"]+)"( aria-current="page")?>([A-Za-z ]+)(.*?)</a>', re.S)


def header_of(body):
    found = HEADER_RE.search(body)
    assert found, "no header rendered, so nothing below is a measurement"
    return found.group(1)


class FiveTabs(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = an_app()
        cls.header = header_of(cls.app.test_client().get("/attention/").get_data(as_text=True))
        cls.tabs = TAB_RE.findall(ROOMS_NAV_RE.search(cls.header).group(1))
        print("\n  DENOMINATORS  top tabs %d" % len(cls.tabs))

    def test_the_five_tabs_in_the_ruled_order(self):
        self.assertEqual([(label, href) for href, _, label, _ in self.tabs], TABS)

    def test_inbox_is_the_selected_tab_on_attention(self):
        self.assertEqual([label for _, sel, label, _ in self.tabs if sel == "true"], ["Inbox"])

    def test_the_inbox_row_lists_attention_then_intake_with_attention_current(self):
        rows = SUB_RE.findall(self.header)
        self.assertEqual(len(rows), 1, rows)
        label, inner = rows[0]
        self.assertEqual(label, "Inbox rooms")
        links = SUBLINK_RE.findall(inner)
        self.assertEqual([(h, t.strip()) for h, _, t, _ in links], [("/attention/", "Attention"), ("/intake", "Intake")])
        self.assertEqual([t.strip() for _, cur, t, _ in links if cur], ["Attention"])

    def test_no_chats_or_terminal_link_the_holds_stay(self):
        for held in ('href="/chats', 'href="/terminal'):
            self.assertNotIn(held, self.header, held)


class TheBadgeRidesTheFirstTab(unittest.TestCase):

    def test_three_waiting_shows_on_inbox_and_on_intake(self):
        app = an_app()
        app.jinja_env.globals["intake_waiting"] = lambda: 3
        header = header_of(app.test_client().get("/attention/").get_data(as_text=True))
        tabs = TAB_RE.findall(ROOMS_NAV_RE.search(header).group(1))
        self.assertEqual(tabs[0][2], "Inbox")
        self.assertIn('<span class="gate">3 waiting</span>', tabs[0][3])
        self.assertEqual(sum('class="gate"' in extra for _, _, _, extra in tabs), 1, "the badge is on one tab only")
        sub = SUBLINK_RE.findall(SUB_RE.search(header).group(2))
        self.assertIn('<span class="gate">3 waiting</span>', sub[1][3])

    def test_nothing_waiting_is_an_absence(self):
        header = header_of(an_app().test_client().get("/attention/").get_data(as_text=True))
        self.assertNotIn('class="gate"', header)


class EveryRoomIsStillLinked(unittest.TestCase):

    def test_every_room_is_a_tab_or_a_group_room_somewhere(self):
        source = open(os.path.join(WEB_DIR, "templates", "base.html"), encoding="utf-8").read()
        groups = re.search(r"\{% set nav_groups = \[(.*?)\] %\}", source, re.S)
        self.assertIsNotNone(groups)
        hrefs = set(re.findall(r"'(/[a-z/]*)'", groups.group(1)))
        self.assertEqual(hrefs, ROOMS)

    def test_the_superseded_strip_is_not_rendered(self):
        header = header_of(an_app().test_client().get("/attention/").get_data(as_text=True))
        self.assertEqual(header.count('class="room"'), 5)


class TheRulingIsWrittenDown(unittest.TestCase):

    def test_item_eleven_carries_the_dated_amendment_and_the_holds(self):
        text = open(MUST_NOT_BUILD, encoding="utf-8").read()
        self.assertIn("AMENDED 2026-09-14 BY THE OPERATOR (D-INFINITY-UX-RULINGS-1", text)
        self.assertIn("THE NAV IS FIVE TABS", text)
        self.assertIn("THE HOLDS STAY", text)


if __name__ == "__main__":
    unittest.main()
