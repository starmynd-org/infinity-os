"""The shell nav is SIX tabs with a row of rooms, every room is still one click from its tab,
the intake badge rides the first tab, and the Chats and Terminal holds stay.

A4 INFINITY-STREAMLINE, ALPHA-SPRINT-1, Andrew's D-INFINITY-UX-RULINGS-1 ruling 2 (2026-09-14,
relayed by CLOSEDOWN-COMMANDER). web/MUST-NOT-BUILD.md item 11 carries the dated amendment. The
painted half (390 and 1440, the badge on screen, the browser censuses in test_the_intake_surface,
test_the_keyboard_and_the_phone and test_the_order_and_the_filters) is proved on the seat's
disposable scratch store.

AMENDED BY W5-S7, 2026-09-16, for D-NAV-SIX (Andrew, 2026-09-16): the nav becomes six tabs, adding
Data, with Sources and Goals and KPIs as its rooms. The file keeps its old name so its history
stays readable. Three things are new here and each is its own case: the Data tab opens its own row
on both of its pages; the Inbox tab's badge carries its word in a `.gw` span, which the phone
stylesheet hides visually, so the tab's text (and its accessible name) still says "waiting"; and
Study renders no progress bar, which item 4's goal-progress exception must not widen into.
"""

import os
import re
import unittest
from unittest import mock

# The Data and Study cases drive `create_app()`, which asks the store who it is at boot. Name a
# database that cannot exist BEFORE anything imports the store, and refuse the live one outright.
os.environ["BRAIN_PG_DB"] = os.environ.get("W5_NAV_DB", "brain_w5_nav_no_such_db")
if os.environ["BRAIN_PG_DB"] == "brain":
    raise SystemExit("test_five_tab_nav: refusing the live store; this suite needs no rows")

from web.tests.test_routines_and_attention_render import an_app  # noqa: E402

WEB_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MUST_NOT_BUILD = os.path.join(WEB_DIR, "MUST-NOT-BUILD.md")

TABS = [("Inbox", "/attention/"), ("Work", "/queue"), ("Projects", "/board"), ("Data", "/data"),
        ("Agents", "/fleet"), ("More", "/brief")]
ROOMS = {"/attention/", "/intake", "/queue", "/queue/table", "/board", "/data", "/data/goals", "/fleet",
         "/live", "/sessions", "/brief", "/scope", "/study"}

HEADER_RE = re.compile(r"<header>(.*?)</header>", re.S)
ROOMS_NAV_RE = re.compile(r'<nav class="rooms" role="tablist">(.*?)</nav>', re.S)
TAB_RE = re.compile(r'<a class="room" role="tab" href="([^"]+)" data-nav-group="[a-z]+"\s+'
                    r'aria-selected="(true|false)">([A-Za-z]+)(.*?)</a>', re.S)
SUB_RE = re.compile(r'<nav class="subrooms" aria-label="([^"]+)">(.*?)</nav>', re.S)
SUBLINK_RE = re.compile(r'<a class="subroom" href="([^"]+)"( aria-current="page")?>([A-Za-z ]+)(.*?)</a>', re.S)
TAG_RE = re.compile(r"<[^>]+>")


def header_of(body):
    found = HEADER_RE.search(body)
    assert found, "no header rendered, so nothing below is a measurement"
    return found.group(1)


def text_of(markup):
    """What a tab's text content reads once the tags are gone: the source of its accessible name."""
    return re.sub(r"\s+", " ", TAG_RE.sub("", markup)).strip()


class SixTabs(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = an_app()
        cls.header = header_of(cls.app.test_client().get("/attention/").get_data(as_text=True))
        cls.tabs = TAB_RE.findall(ROOMS_NAV_RE.search(cls.header).group(1))
        print("\n  DENOMINATORS  top tabs %d" % len(cls.tabs))

    def test_the_six_tabs_in_the_ruled_order(self):
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
        self.assertIn('<span class="gate">3<span class="gw"> waiting</span></span>', tabs[0][3])
        self.assertEqual(sum('class="gate"' in extra for _, _, _, extra in tabs), 1, "the badge is on one tab only")
        sub = SUBLINK_RE.findall(SUB_RE.search(header).group(2))
        self.assertIn('<span class="gate">3 waiting</span>', sub[1][3])

    def test_the_inbox_tab_still_says_waiting_in_its_text(self):
        """The phone stylesheet hides `.gw` visually to keep six tabs on one line. The word must
        stay in the tab's text, which is what its accessible name is computed from, and must not
        be dropped from the markup. The painted half, the accessible name read by chromium, is in
        test_five_tabs_one_row_at_375.py."""
        app = an_app()
        app.jinja_env.globals["intake_waiting"] = lambda: 12
        header = header_of(app.test_client().get("/attention/").get_data(as_text=True))
        tabs = TAB_RE.findall(ROOMS_NAV_RE.search(header).group(1))
        self.assertEqual(text_of(tabs[0][2] + tabs[0][3]), "Inbox 12 waiting")

    def test_an_unreadable_count_keeps_its_word_too(self):
        app = an_app()
        app.jinja_env.globals["intake_waiting"] = lambda: None
        header = header_of(app.test_client().get("/attention/").get_data(as_text=True))
        tabs = TAB_RE.findall(ROOMS_NAV_RE.search(header).group(1))
        self.assertIn('<span class="gate">?<span class="gw"> waiting</span></span>', tabs[0][3])
        self.assertEqual(text_of(tabs[0][2] + tabs[0][3]), "Inbox ? waiting")

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
        self.assertEqual(header.count('class="room"'), 6)


def fake_queue_view(**kw):
    return {"totals": {"decide": 0}, "tiers": {"decide": []}}


SOURCES = {"present": True, "note": None, "sources": [
    {"id": 1, "name": "Shop orders", "kind": "live", "sub": "Order lines", "where": "warehouse.shop_orders",
     "last": "09:14", "rows": 1087, "status": "fresh"}]}
GOALS = {"present": True, "note": None, "kpis": [], "goals": [
    {"id": 1, "name": "Lift blended margin to 82 per cent", "sub": "", "target": "82.0%", "now": "80.5%", "pct": 98,
     "measured_from": [], "reported_in": [], "why": "", "done_when": "", "owner": ""}]}
STUDY = {"sessions": 3, "sessions_today": 1, "transcripts": {"n": 2, "verified": 1, "bytes": 1048576},
         "event_total": 40, "runs": 5, "artifacts": 2, "recs": {"n": 1}, "acted_rate": None, "agent_hours": 1.5,
         "health": {"schema_version": 85, "head_seq": 10}, "events": [{"type": "task.created", "n": 3}],
         "days": [{"day": "09-15", "n": 4}, {"day": "09-16", "n": 2}], "day_max": 4, "lag": []}
CROSSTALK = {"feed": [], "item": None, "agents": []}


class ConsolePages(unittest.TestCase):
    """`create_app()` with its store reads replaced by invented rows, as in
    test_data_section_render.py. The real console routes, the real base.html."""

    @classmethod
    def setUpClass(cls):
        import web.model as model
        from web.app import create_app
        cls.patchers = [mock.patch.object(model, "data_sources_view", lambda: SOURCES),
                        mock.patch.object(model, "goals_and_kpis_view", lambda: GOALS),
                        mock.patch.object(model, "queue_view", fake_queue_view),
                        mock.patch.object(model, "study", lambda: STUDY),
                        mock.patch.object(model, "crosstalk", lambda: CROSSTALK)]
        for patcher in cls.patchers:
            patcher.start()
        cls.client = create_app().test_client()

    @classmethod
    def tearDownClass(cls):
        for patcher in cls.patchers:
            patcher.stop()

    def page(self, path):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:400])
        return response.get_data(as_text=True)


class TheDataTabOpensItsRooms(ConsolePages):

    def rooms_on(self, path):
        header = header_of(self.page(path))
        tabs = TAB_RE.findall(ROOMS_NAV_RE.search(header).group(1))
        rows = SUB_RE.findall(header)
        return tabs, rows

    def test_on_sources_the_data_tab_is_selected_and_sources_is_current(self):
        tabs, rows = self.rooms_on("/data")
        self.assertEqual([label for _, sel, label, _ in tabs if sel == "true"], ["Data"])
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0][0], "Data rooms")
        links = SUBLINK_RE.findall(rows[0][1])
        self.assertEqual([(h, t.strip()) for h, _, t, _ in links], [("/data", "Sources"), ("/data/goals", "Goals and KPIs")])
        self.assertEqual([t.strip() for _, cur, t, _ in links if cur], ["Sources"])

    def test_on_goals_the_data_tab_is_selected_and_goals_is_current(self):
        tabs, rows = self.rooms_on("/data/goals")
        self.assertEqual([label for _, sel, label, _ in tabs if sel == "true"], ["Data"])
        links = SUBLINK_RE.findall(rows[0][1])
        self.assertEqual([t.strip() for _, cur, t, _ in links if cur], ["Goals and KPIs"])


class StudyRendersNoProgressBar(ConsolePages):
    """MUST-NOT-BUILD item 4 as amended 2026-09-16: goal progress in the data section is the one
    new exception, and Study is named as still forbidden. The same instrument reads both pages, so
    the zero on Study stands beside a nonzero on Goals, and Study's own count bars are required to
    be present so the zero is not a page that rendered nothing."""

    BAR_MARKS = ('class="dsbar"', 'class="dsbarfill"', "<progress", 'role="progressbar"', "percent of target")

    def main_of(self, path):
        body = self.page(path)
        return body.split("</header>", 1)[1]

    def test_goals_carries_the_goal_bar_so_the_instrument_can_see_one(self):
        main = self.main_of("/data/goals")
        self.assertEqual([mark for mark in self.BAR_MARKS if mark in main],
                         ['class="dsbar"', 'class="dsbarfill"', "percent of target"])

    def test_study_renders_its_count_bars_and_no_progress_bar(self):
        main = self.main_of("/study")
        self.assertEqual(main.count('<div class="b" title='), len(STUDY["days"]), "Study's count bars did not render")
        self.assertEqual([mark for mark in self.BAR_MARKS if mark in main], [])
        self.assertNotIn("data.css", main)


class TheRulingIsWrittenDown(unittest.TestCase):

    def test_item_eleven_carries_the_dated_amendment_and_the_holds(self):
        text = open(MUST_NOT_BUILD, encoding="utf-8").read()
        self.assertIn("AMENDED 2026-09-14 BY THE OPERATOR (D-INFINITY-UX-RULINGS-1", text)
        self.assertIn("THE NAV IS FIVE TABS", text)
        self.assertIn("THE HOLDS STAY", text)

    def test_item_eleven_carries_the_six_tab_amendment(self):
        text = open(MUST_NOT_BUILD, encoding="utf-8").read()
        self.assertIn("AMENDED 2026-09-16 BY THE OPERATOR (D-NAV-SIX", text)
        self.assertIn("THE NAV IS SIX TABS", text)


if __name__ == "__main__":
    unittest.main()
