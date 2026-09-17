"""The Sources pane and the Goals-and-KPIs pane render store-free, a stale/dead source renders
visibly differently from a fresh one, and a goal's progress bar appears only when its target/now
both parse as numbers.

W4-V002-DATA, Commander B, v0.02. Same shape as `test_board_search_and_panel.py`: the route is
driven through `create_app()` with `model.data_sources_view`/`model.goals_and_kpis_view` replaced
by invented rows, so no store is read. `create_app()` still asks the store who it is at boot, and
the store's default database is the LIVE `brain`, so this file names a database that cannot exist
BEFORE the app is imported and refuses `brain` outright. Migration 0085 is proven separately, on a
disposable scratch database, in `outputs/2026-09-16-w4-v002-data/SCRATCH-PROOF.md` -- never here.
"""

import os
import re
import unittest
from unittest import mock

os.environ["BRAIN_PG_DB"] = os.environ.get("W4_DATA_DB", "brain_w4_data_no_such_db")
if os.environ["BRAIN_PG_DB"] == "brain":
    raise SystemExit("test_data_section_render: refusing the live store; this suite needs no rows")

import web.model as model  # noqa: E402  (the database name must be set first)
from web.app import create_app  # noqa: E402

DOT_RE = re.compile(r'<span class="dsdot ds-([a-z]+)"')
BARFILL_RE = re.compile(r'<div class="dsbarfill" style="width: (\d+)%"></div>')


def fake_queue_view(**kw):
    return {"totals": {"decide": 0}, "tiers": {"decide": []}}


SOURCES_PRESENT = {
    "present": True,
    "note": None,
    "sources": [
        {"id": 1, "name": "Shop orders", "kind": "live", "sub": "Order lines",
         "where": "warehouse.shop_orders", "last": "09:14", "rows": 1087, "status": "fresh"},
        {"id": 2, "name": "Ad spend export", "kind": "sheet", "sub": "Impressions omit zero-click rows",
         "where": "ads-export-0912.csv", "last": "12 Sep", "rows": 9204, "status": "stale"},
        {"id": 3, "name": "Legacy inventory sheet", "kind": "sheet", "sub": "Superseded by brain.clients",
         "where": "inventory-old.xlsx", "last": "never", "rows": "—", "status": "never"},
    ],
}

SOURCES_ABSENT = {"present": False, "note": "this store has no brain.data_source_health "
                                            "(migration 0085 is not applied here)", "sources": []}

GOALS_PRESENT = {
    "present": True,
    "note": None,
    "goals": [
        {"id": 1, "name": "Lift blended margin to 82 per cent", "sub": "Written 04 July",
         "target": "82.0%", "now": "80.5%", "pct": model._progress_pct("82.0%", "80.5%"),
         "measured_from": ["Shop weekly summary"], "reported_in": ["Weekly executive"],
         "why": "Margin is the lever that does not need more orders.", "done_when": "",
         "owner": "andrew"},
        {"id": 2, "name": "Every client has a named owner", "sub": "41 clients, 6 unassigned",
         "target": "named", "now": "in progress", "pct": model._progress_pct("named", "in progress"),
         "measured_from": [], "reported_in": [], "why": "", "done_when": "", "owner": ""},
    ],
    "kpis": [
        {"id": 1, "name": "Sales, week", "sub": "Complete ISO weeks only", "target": "22,000",
         "now": "20,376", "src": "Shop weekly summary", "src_status": "fresh"},
        {"id": 2, "name": "Paid social sales", "sub": "Two campaigns paused", "target": "3,200",
         "now": "2,114", "src": "Channel performance", "src_status": "stale"},
    ],
}

GOALS_ABSENT = {"present": False, "note": "this store has no brain.goal (migration 0085 is not "
                                          "applied here)", "goals": [], "kpis": []}


class TheSourcesPaneRendersStoreFree(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.patchers = [
            mock.patch.object(model, "data_sources_view", lambda: SOURCES_PRESENT),
            mock.patch.object(model, "queue_view", fake_queue_view),
        ]
        for patcher in cls.patchers:
            patcher.start()
        cls.client = create_app().test_client()

    @classmethod
    def tearDownClass(cls):
        for patcher in cls.patchers:
            patcher.stop()

    def page(self):
        response = self.client.get("/data")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:400])
        return response.get_data(as_text=True)

    def test_every_source_renders(self):
        body = self.page()
        for name in ("Shop orders", "Ad spend export", "Legacy inventory sheet"):
            self.assertIn(name, body)

    def test_a_stale_source_renders_a_different_status_dot_from_a_fresh_one(self):
        """THE CASE THE REGISTRY CONTRACT'S ITEM (e) IS FOR: a fresh, a stale and a never-touched
        source must not render identically. Asserts the actual rendered dot classes in row order,
        not merely that three rows exist -- a template that dropped the status entirely would
        still pass a weaker assertion. See this file's own docstring for the mutation proof this
        test is the target of."""
        body = self.page()
        self.assertEqual(DOT_RE.findall(body), ["fresh", "stale", "never"])

    def test_the_waiting_banner_appears_when_the_store_cannot_answer(self):
        with mock.patch.object(model, "data_sources_view", lambda: SOURCES_ABSENT):
            response = self.client.get("/data")
        body = response.get_data(as_text=True)
        self.assertIn("waiting", body)
        self.assertIn("migration 0085 is not applied here", body)

    def test_an_empty_but_present_store_says_so_rather_than_showing_an_empty_table(self):
        empty = {"present": True, "note": None, "sources": []}
        with mock.patch.object(model, "data_sources_view", lambda: empty):
            response = self.client.get("/data")
        body = response.get_data(as_text=True)
        self.assertIn("No source is registered yet", body)
        self.assertNotIn("<table", body)


class TheGoalsAndKPIsPaneRendersStoreFree(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.patchers = [
            mock.patch.object(model, "goals_and_kpis_view", lambda: GOALS_PRESENT),
            mock.patch.object(model, "queue_view", fake_queue_view),
        ]
        for patcher in cls.patchers:
            patcher.start()
        cls.client = create_app().test_client()

    @classmethod
    def tearDownClass(cls):
        for patcher in cls.patchers:
            patcher.stop()

    def page(self):
        response = self.client.get("/data/goals")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:400])
        return response.get_data(as_text=True)

    def test_every_goal_and_kpi_renders(self):
        body = self.page()
        for name in ("Lift blended margin to 82 per cent", "Every client has a named owner",
                     "Sales, week", "Paid social sales"):
            self.assertIn(name, body)

    def test_a_parseable_goal_draws_a_bar_at_the_computed_width_and_an_unparseable_one_draws_none(self):
        """MUST-NOT-BUILD item 4's amendment (2026-09-16) permits exactly this and nothing wider:
        a bar only where target/now both parse, at the percentage computed from them, never a
        guessed or default width."""
        body = self.page()
        fills = BARFILL_RE.findall(body)
        self.assertEqual(fills, [str(model._progress_pct("82.0%", "80.5%"))])
        self.assertIn("no bar: the target or the current value is not a number", body)

    def test_a_kpi_names_its_source_and_the_sources_status_dot(self):
        body = self.page()
        self.assertEqual(DOT_RE.findall(body), ["fresh", "stale"])
        self.assertIn("Shop weekly summary", body)
        self.assertIn("Channel performance", body)

    def test_the_waiting_banner_appears_when_the_store_cannot_answer(self):
        with mock.patch.object(model, "goals_and_kpis_view", lambda: GOALS_ABSENT):
            response = self.client.get("/data/goals")
        body = response.get_data(as_text=True)
        self.assertIn("waiting", body)
        self.assertIn("migration 0085 is not applied here", body)


class TheProgressComputationItself(unittest.TestCase):
    """`_progress_pct` unit-level, separate from the render tests above so a defect in the
    arithmetic and a defect in the template cannot hide behind each other."""

    def test_ordinary_percentages_and_counts(self):
        self.assertEqual(model._progress_pct("82.0%", "80.5%"), 98)
        self.assertEqual(model._progress_pct("1,400", "1,087"), 78)
        self.assertEqual(model._progress_pct("260,000", "218,400"), 84)

    def test_unparseable_input_is_none_not_zero(self):
        self.assertIsNone(model._progress_pct("named", "in progress"))
        self.assertIsNone(model._progress_pct(None, "5"))
        self.assertIsNone(model._progress_pct("0", "5"))  # zero target: no division, no guess

    def test_over_target_clamps_at_100(self):
        self.assertEqual(model._progress_pct("100", "140"), 100)


if __name__ == "__main__":
    unittest.main()
