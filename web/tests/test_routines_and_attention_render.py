"""Render tests for the two new surfaces, against stub read ports and no database.

These prove the templates render what the view layer decided, on a real Flask app with a real
Jinja environment, without a console, a store or a browser. The browser pass over the running
console is a separate check and does not replace this one: a template that renders the right
words from a stub is the precondition for the browser check meaning anything.

`base.html` is L01's file and is extended, never edited. It reads `theme`, `room`, `since` and
`deep_on` from the render context, so the harness supplies them the way `web/app.py` does.
"""

import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from flask import Flask

import routines as r
from web.blueprints.routines import HistoryEntry, HostStatus
from web.blueprints.routines import build as build_routines
from web.views import (Completeness, Freshness, Impact, Provenance, ProvenanceStep, QueueItem,
                       QueueView, Signals, set_display_zone)
from web.views.blueprint import build as build_attention

UTC = timezone.utc
NOW = datetime(2026, 9, 6, 15, 0, tzinfo=UTC)   # Sunday 18:00 in Europe/Bucharest
TZ = "Europe/Bucharest"

GRANT = r.Grant(
    sources=(r.Source("Your work email", "41 messages"), r.Source("Your calendar, next 24 hours")),
    effects=(
        r.Effect("Writes a brief into your Attention inbox"),
        r.Effect("Sends a reply to another person", leaves=True, policy="asks you first, every time"),
    ),
    destination="Attention inbox, and a copy to you by email",
)

ROUTINE = r.Routine(
    routine_id="rtn_1042",
    version=4,
    title="Weekday morning brief",
    asked_for="Every weekday at 8:30, summarize urgent items and show drafts for approval",
    schedule=r.Schedule(8, 30, r.WEEKDAYS, TZ),
    grant=GRANT,
)


class StubRoutinePort:
    def __init__(self, routine=ROUTINE, reachable=True):
        self._routine = routine
        self._host = HostStatus("infinity-01", "VPS profile", reachable,
                                detail="Its last check-in was 14 minutes ago.")

    def list_routines(self):
        return [self._routine]

    def get_routine(self, routine_id):
        return self._routine if routine_id == self._routine.routine_id else None

    def history(self, routine_id):
        return [
            HistoryEntry(NOW - timedelta(days=2), 4, "ran", "5 items, 2 drafts prepared, 0 sent", duration="1 min 12 s"),
            HistoryEntry(NOW - timedelta(days=5), 3, "missed", "a source was unavailable and the brief said so"),
        ]

    def host_for(self, routine_id):
        return self._host


class WorkbenchFixturePort(StubRoutinePort):
    """UX-APP-01's deterministic, fixture-only routine workbench data."""

    def __init__(self):
        super().__init__()
        states = (r.ACTIVE, r.PAUSED, r.DRAFT, r.ACTIVE, r.ACTIVE, r.ACTIVE,
                  r.DRAFT, r.ACTIVE, r.ACTIVE, r.PAUSED, r.ACTIVE, r.DRAFT)
        titles = (
            "Weekday morning brief", "Pause while travelling", "Draft: client renewal check",
            "Missed source recovery", "DST spring-forward check", "DST fall-back check",
            "Draft: approval-required send", "External reply review", "Stale version refusal",
            "Unavailable host recovery", "No result yet", "Draft: source coverage review",
        )
        self._routines = []
        for index, (state, title) in enumerate(zip(states, titles)):
            schedule = (r.Schedule(3, 30, frozenset({6}), TZ)
                        if title in ("DST spring-forward check", "DST fall-back check") else ROUTINE.schedule)
            self._routines.append(replace(ROUTINE, routine_id="rtn_%04d" % (1042 + index), title=title,
                                          state=state, schedule=schedule, version=4 + (index % 3)))

    def list_routines(self):
        return self._routines

    def get_routine(self, routine_id):
        return next((routine for routine in self._routines if routine.routine_id == routine_id), None)

    def history(self, routine_id):
        if routine_id == "rtn_1052":
            return []  # The no-result-yet state is not a failure.
        if routine_id == "rtn_1045":
            return [HistoryEntry(NOW - timedelta(days=1), 4, "missed", "source unavailable; no catch-up ran")]
        if routine_id == "rtn_1051":
            return [HistoryEntry(NOW - timedelta(minutes=15), 4, "blocked", "host is not reachable")]
        if routine_id == "rtn_1050":
            return [HistoryEntry(NOW - timedelta(minutes=9), 4, "refused",
                                 "Approval prepared under v4 was refused after this routine moved to v6; review again.")]
        return super().history(routine_id)

    def host_for(self, routine_id):
        if routine_id == "rtn_1051":
            return HostStatus("infinity-01", "VPS profile", False, "Fixture check is stale.")
        return super().host_for(routine_id)


def an_item(**over):
    base = dict(
        item_id="atn_8801",
        title="Renewal quote for Halden Bros expires Monday",
        kind="decision",
        tier=1,
        rank=1,
        why="a dated commitment with an external deadline inside 72 h",
        impact=Impact.unknown("no value is recorded on this account"),
        freshness=Freshness.read_at(NOW - timedelta(minutes=22), NOW, timedelta(minutes=30)),
        provenance=Provenance("Your work email", "company", "msg/8f21c",
                              (ProvenanceStep(NOW - timedelta(minutes=26), "captured from the work inbox", "Intake"),)),
    )
    base.update(over)
    return QueueItem(**base)


RESTRICTED = an_item(
    item_id="atn_8805",
    title="Item in a workspace you cannot read",
    kind="restricted",
    tier=2,
    rank=4,
    readable=False,
    why="ranked from metadata only; its content was never indexed for you",
    impact=Impact.unknown("content not readable by you, so no impact can be stated"),
    freshness=Freshness.never_read("not indexed for your account"),
    provenance=Provenance("Client workspace (restricted)", "restricted", None,
                          (ProvenanceStep(NOW - timedelta(hours=2), "existence recorded; content not read",
                                          "authorization boundary"),)),
)

MEASURED = an_item(item_id="atn_8802", title="Draft reply to Priya", kind="draft", tier=2, rank=2,
                   why="a prepared draft waiting for you",
                   impact=Impact.measured("12 min", "median over 9 samples of this reply kind"))

STALE = an_item(item_id="atn_8803", title="Pipeline summary could not include #sales", kind="exception",
                tier=1, rank=3, why="a routine finished with a source missing",
                freshness=Freshness.read_at(NOW - timedelta(hours=31), NOW, timedelta(minutes=15)))


class StubAttentionPort:
    evidence_label = 'Fixture evidence'
    def __init__(self, items=None):
        self._items = items if items is not None else [an_item(), MEASURED, STALE, RESTRICTED]

    def queue(self):
        return QueueView.ranked(self._items, checked_at=NOW)

    def item(self, item_id):
        for candidate in self._items:
            if candidate.item_id == item_id:
                return candidate
        return None


def app_queue_fixture():
    """UX-APP-02's 240 deterministic rows, including the named truth states."""
    items = [an_item(), MEASURED, STALE, RESTRICTED]
    items.extend([
        an_item(item_id="atn_estimated", title="Forecasted renewal impact", kind="forecast", tier=2, rank=5,
                impact=Impact.estimated("2 h", "fixture forecast from prior renewals")),
        an_item(item_id="atn_never_read", title="Quiet source needs first read", kind="source", tier=4, rank=6,
                freshness=Freshness.never_read("fixture source has not been indexed")),
        an_item(item_id="atn_failed_source", title="Source check failed", kind="exception", tier=1, rank=7,
                freshness=Freshness.never_read("fixture source check failed; retry is pending")),
        an_item(item_id="atn_display", title="Source coverage status", kind="status", tier=3, rank=8,
                signals=Signals.from_contract({"display": {"label": "Source status", "note": "Fixture source is quiet"}})),
    ])
    items.extend(
        an_item(item_id="atn_%03d" % number, title="Fixture queue item %03d" % number,
                kind="decision", tier=(number % 4) + 1, rank=number + 1)
        for number in range(8, 240)
    )
    return items


def an_app(routine_port=None, attention_port=None, now=NOW):
    app = Flask(__name__, template_folder="../templates", static_folder="../static")
    app.register_blueprint(build_routines(routine_port or StubRoutinePort(), now=lambda: now))
    app.register_blueprint(build_attention(attention_port or StubAttentionPort()))

    # base.html's own inputs. It is L01's file and is extended, never edited, so the harness
    # supplies exactly what it reads: four flags and the six callables its chrome calls. Stubs,
    # not the console's implementations: this test is about the two new templates, and pulling in
    # the real chrome would make it a test of the console instead.
    app.jinja_env.globals.update(
        intake_waiting=lambda: 0,
        deep_count=lambda: 0,
        dispatch_count=lambda: 0,
        dispatch_extra=lambda: {},
        dispatch_href=lambda: "/queue",
        mode_href=lambda **kw: "/queue",
    )

    @app.context_processor
    def _base_context():
        return {"theme": "dark", "room": "routines", "since": 0, "deep_on": False,
                "deep_since": None, "patch_only": False, "dispatch_on": False}

    return app


class RoutinesRender(unittest.TestCase):

    def setUp(self):
        self.client = an_app().test_client()

    def test_the_index_shows_the_next_run_in_the_users_zone(self):
        body = self.client.get("/routines/").get_data(as_text=True)
        self.assertEqual(self.client.get("/routines/").status_code, 200)
        self.assertIn("Mon 07 Sep 2026, 08:30 EEST", body)

    def test_the_index_says_whether_anything_leaves(self):
        body = self.client.get("/routines/").get_data(as_text=True)
        self.assertIn("asks you first", body)

    def test_the_primary_surface_is_a_compact_routine_row_table_not_a_card_showcase(self):
        body = an_app(routine_port=WorkbenchFixturePort()).test_client().get("/routines/").get_data(as_text=True)
        self.assertIn('data-routine-workbench="true"', body)
        self.assertIn('<table class="rtn-table">', body)
        self.assertEqual(body.count("data-routine-row="), 12)
        for expected in ("DST spring-forward check", "DST fall-back check", "Stale version refusal",
                         "approval required", "no result yet"):
            self.assertIn(expected, body)

    def test_workbench_keeps_row_labels_and_refused_actions_in_its_narrow_static_layout(self):
        body = an_app(routine_port=WorkbenchFixturePort()).test_client().get("/routines/").get_data(as_text=True)
        self.assertIn("@media(max-width:760px)", body)
        self.assertIn('data-label="State"', body)
        self.assertIn('tabindex="-1"', body)
        self.assertIn('button disabled', body)
        self.assertIn("console write door is not wired", body)

    def test_workbench_filters_keep_drafts_distinct_from_paused_routines(self):
        client = an_app(routine_port=WorkbenchFixturePort()).test_client()
        drafts = client.get("/routines/?tab=drafts").get_data(as_text=True)
        paused = client.get("/routines/?tab=paused").get_data(as_text=True)
        self.assertIn("Draft: client renewal check", drafts)
        self.assertNotIn("Pause while travelling", drafts)
        self.assertIn("Pause while travelling", paused)
        self.assertNotIn("Draft: client renewal check", paused)

    def test_an_inspector_keeps_the_fixture_label_and_return_to_the_selected_collection(self):
        client = an_app(routine_port=WorkbenchFixturePort()).test_client()
        index = client.get("/routines/?tab=drafts").get_data(as_text=True)
        self.assertEqual(index.count("/routines/rtn_1044?tab=drafts"), 2,
                         "title and Inspect action must preserve the selected tab")
        body = client.get("/routines/rtn_1044?tab=drafts").get_data(as_text=True)
        self.assertIn("Fixture routine inspector", body)
        self.assertIn("Return to drafts routines", body)
        self.assertIn("/routines/?tab=drafts#routine-rtn_1044", body)

    def test_outbound_policy_summary_agrees_between_row_and_inspector(self):
        no_ask = replace(ROUTINE, grant=replace(GRANT, effects=(r.Effect("Sends a reply", leaves=True,
                                                                    policy="sends without asking"),)))
        port = StubRoutinePort(no_ask)
        client = an_app(routine_port=port).test_client()
        row = client.get("/routines/").get_data(as_text=True)
        detail = client.get("/routines/rtn_1042").get_data(as_text=True)
        self.assertIn("external: standing permission", row)
        self.assertIn("external: standing permission", detail)
        self.assertNotIn("approval required (sends without asking)", row)

    def test_policy_summary_handles_approval_mixed_and_unknown_wording_without_contradiction(self):
        cases = (
            ((r.Effect("Sends a reply", leaves=True, policy="asks you first, every time"),),
             "external: approval required"),
            ((r.Effect("Sends a reply", leaves=True, policy="sends without asking"),
              r.Effect("Posts a summary", leaves=True, policy="asks you first, every time")),
             "external: mixed approval policies"),
            ((r.Effect("Sends a reply", leaves=True, policy="policy supplied by producer"),),
             "external: policy wording needs review"),
            ((r.Effect("Sends a reply", leaves=True, policy="must not send without asking"),),
             "external: policy wording needs review"),
            ((r.Effect("Sends a reply", leaves=True, policy="no approval has been granted"),),
             "external: policy wording needs review"),
        )
        for effects, expected in cases:
            with self.subTest(expected=expected):
                routine = replace(ROUTINE, grant=replace(GRANT, effects=effects))
                client = an_app(routine_port=StubRoutinePort(routine)).test_client()
                self.assertIn(expected, client.get("/routines/").get_data(as_text=True))
                self.assertIn(expected, client.get("/routines/rtn_1042").get_data(as_text=True))

    def test_transition_and_stale_refusal_fixtures_describe_real_rendered_states(self):
        port = WorkbenchFixturePort()
        spring = an_app(routine_port=port, now=datetime(2026, 3, 28, 12, 0, tzinfo=UTC)).test_client()
        fall = an_app(routine_port=port, now=datetime(2026, 10, 24, 12, 0, tzinfo=UTC)).test_client()
        self.assertIn("does not exist on that date", spring.get("/routines/rtn_1046").get_data(as_text=True))
        self.assertIn("happens twice on that date", fall.get("/routines/rtn_1047").get_data(as_text=True))
        all_rows = an_app(routine_port=port).test_client().get("/routines/").get_data(as_text=True)
        self.assertIn("Approval prepared under v4 was refused", all_rows)

    def test_a_paused_routine_shows_no_next_run(self):
        client = an_app(routine_port=StubRoutinePort(r.pause(ROUTINE))).test_client()
        body = client.get("/routines/").get_data(as_text=True)
        self.assertIn("no next run", body)
        self.assertNotIn("08:30 EEST", body)

    def test_an_unreachable_host_is_not_reported_as_a_stopped_routine(self):
        client = an_app(routine_port=StubRoutinePort(reachable=False)).test_client()
        body = client.get("/routines/").get_data(as_text=True)
        self.assertIn("not reachable", body)
        self.assertIn("active", body)

    def test_the_detail_page_states_the_current_schedule_before_the_original_sentence(self):
        body = self.client.get("/routines/rtn_1042").get_data(as_text=True)
        self.assertLess(body.index("08:30</b>"), body.index("First asked for"))

    def test_the_detail_page_shows_the_standing_grant_in_full(self):
        body = self.client.get("/routines/rtn_1042").get_data(as_text=True)
        for expected in ("Your work email", "Sends a reply to another person",
                         "leaves your workspace", "Attention inbox, and a copy to you by email"):
            self.assertIn(expected, body)

    def test_history_renders_both_a_run_and_a_miss(self):
        body = self.client.get("/routines/rtn_1042").get_data(as_text=True)
        self.assertIn("ran:", body)
        self.assertIn("missed:", body)

    def test_an_unknown_routine_is_404_not_an_empty_page(self):
        self.assertEqual(self.client.get("/routines/nope").status_code, 404)

    def test_the_surface_offers_no_write_route(self):
        """The console has one write door. A pause button here would be a second one."""
        app = an_app()
        posts = [rule.rule for rule in app.url_map.iter_rules() if "POST" in rule.methods]
        self.assertEqual(posts, ["/routines/<routine_id>/preview"])


class ProposalRender(unittest.TestCase):

    def setUp(self):
        self.client = an_app().test_client()

    def test_a_time_change_previews_without_a_new_permission(self):
        body = self.client.post("/routines/rtn_1042/preview", data={"time": "07:30"}).get_data(as_text=True)
        self.assertIn("Time 08:30 becomes 07:30", body)
        self.assertIn("No new permission needed", body)
        self.assertIn("Mon 07 Sep 2026, 07:30 EEST", body)

    def test_the_preview_changes_nothing(self):
        self.client.post("/routines/rtn_1042/preview", data={"time": "07:30"})
        body = self.client.get("/routines/rtn_1042").get_data(as_text=True)
        self.assertIn("version 4", body)
        self.assertIn("08:30", body)

    def test_an_unparseable_time_previews_no_change_rather_than_guessing(self):
        body = self.client.post("/routines/rtn_1042/preview", data={"time": "half eight"}).get_data(as_text=True)
        self.assertIn("Nothing is different yet", body)


class AttentionRender(unittest.TestCase):

    def setUp(self):
        self.client = an_app().test_client()

    def test_the_ranked_view_says_it_is_the_working_order(self):
        body = self.client.get("/attention/").get_data(as_text=True)
        self.assertIn("Ranked order", body)

    def test_the_primary_surface_is_a_240_row_attention_table_with_evidence_bound_chips(self):
        body = an_app(attention_port=StubAttentionPort(app_queue_fixture())).test_client().get("/attention/").get_data(as_text=True)
        self.assertIn('data-attention-queue="true"', body)
        self.assertIn('<table class="att-table">', body)
        self.assertEqual(body.count("data-queue-row="), 240)
        self.assertGreaterEqual(body.count("data-evidence="), 240 * 4)
        for expected in ("Forecasted renewal impact", "Quiet source needs first read", "Source check failed",
                         "you may see only limited details"):
            self.assertIn(expected, body)
        self.assertNotIn("msg/8f21c", body)

    def test_attention_rows_keep_labels_and_inspector_targets_in_its_narrow_static_layout(self):
        body = an_app(attention_port=StubAttentionPort(app_queue_fixture())).test_client().get("/attention/").get_data(as_text=True)
        self.assertIn("@media(max-width:760px)", body)
        self.assertIn('data-label="Permission"', body)
        self.assertIn('tabindex="-1"', body)
        self.assertIn("#evidence-permission", body)

    def test_status_chips_link_to_claim_specific_inspector_evidence(self):
        body = self.client.get("/attention/").get_data(as_text=True)
        for anchor in ("#evidence-rank", "#evidence-freshness", "#evidence-impact", "#evidence-permission"):
            self.assertIn(anchor, body)
        self.assertIn('data-evidence="2026-09-06T14:38:00+00:00"', body)
        inspector = self.client.get("/attention/atn_8801?filter=stale&sort=title&dir=desc").get_data(as_text=True)
        self.assertIn('data-evidence-origin>Fixture evidence</span>', inspector)
        self.assertIn('inspector &middot; read only.', inspector)
        self.assertIn("Return to queue", inspector)
        self.assertIn("/attention/?filter=stale&amp;sort=title&amp;dir=desc#attention-atn_8801", inspector)
        self.assertIn('id="evidence-freshness"', inspector)
        self.assertIn("Freshness evidence", inspector)

    def test_a_partial_result_is_visible_in_the_queue_not_only_its_inspector(self):
        partial = an_item(item_id="atn_partial", title="Completed fixture", why="finished work", tier=3, rank=5,
                          completeness=Completeness.partial(["T22 missing-source sentinel"], "source unavailable"))
        client = an_app(attention_port=StubAttentionPort([partial])).test_client()
        body = client.get("/attention/").get_data(as_text=True)
        self.assertIn("finished without T22 missing-source sentinel", body)
        self.assertIn("source unavailable", body)
        self.assertIn("#evidence-completeness", body)
        inspector = client.get("/attention/atn_partial").get_data(as_text=True)
        self.assertIn('id="evidence-completeness"', inspector)
        self.assertIn("Completeness evidence", inspector)

    def test_filters_are_read_only_views_and_do_not_mutate_rank(self):
        items = app_queue_fixture()
        before = {item.item_id: item.rank for item in items}
        client = an_app(attention_port=StubAttentionPort(items)).test_client()
        body = client.get("/attention/?filter=restricted&sort=title").get_data(as_text=True)
        self.assertIn("you may see only limited details", body)
        self.assertNotIn("Renewal quote for Halden Bros", body)
        self.assertEqual(before, {item.item_id: item.rank for item in items})

    def test_unmeasured_impact_renders_as_unmeasured(self):
        body = self.client.get("/attention/").get_data(as_text=True)
        self.assertIn("unmeasured (no value is recorded on this account)", body)
        self.assertIn("no value is recorded on this account", body)

    def test_a_measured_impact_carries_its_provenance(self):
        body = self.client.get("/attention/").get_data(as_text=True)
        self.assertIn("12 min", body)
        self.assertIn("9 samples", body)

    def test_a_stale_read_is_labelled_stale_and_keeps_its_time(self):
        body = self.client.get("/attention/").get_data(as_text=True)
        self.assertIn("stale, as of", body)
        self.assertIn("every 15 min", body)

    def test_sorting_states_that_the_working_order_is_unchanged(self):
        body = self.client.get("/attention/?sort=title").get_data(as_text=True)
        self.assertIn("not what Infinity does first", body)

    def test_the_rank_column_survives_a_sort(self):
        body = self.client.get("/attention/?sort=title").get_data(as_text=True)
        self.assertIn("Rank / status", body)
        for rank in ("1", "2", "3", "4"):
            self.assertIn("#%s " % rank, body)

    def test_an_unknown_sort_column_is_refused(self):
        self.assertEqual(self.client.get("/attention/?sort=impact").status_code, 400)

    def test_an_empty_queue_is_not_a_failed_read(self):
        client = an_app(attention_port=StubAttentionPort(items=[])).test_client()
        body = client.get("/attention/").get_data(as_text=True)
        self.assertIn("Nothing needs you", body)
        self.assertIn("not a failed read", body)

    def test_the_inspector_refuses_a_restricted_item_and_shows_no_content(self):
        body = self.client.get("/attention/atn_8805").get_data(as_text=True)
        self.assertIn("not allowed to read", body)
        self.assertIn("not read", body)
        self.assertNotIn("msg/8f21c", body)

    def test_the_inspector_shows_the_chain_for_a_readable_item(self):
        body = self.client.get("/attention/atn_8801").get_data(as_text=True)
        self.assertIn("captured from the work inbox", body)
        self.assertIn("Intake", body)

    def test_the_attention_surface_offers_no_write_route(self):
        app = an_app()
        posts = [rule.rule for rule in app.url_map.iter_rules()
                 if "POST" in rule.methods and rule.rule.startswith("/attention")]
        self.assertEqual(posts, [])


class AStatedAbsenceIsNotAnEmptyList(unittest.TestCase):
    """The difference between "you have none" and "this console cannot see them".

    Both render as an empty list unless something stops them, and an empty list is the more
    reassuring of the two, which is exactly why it is the dangerous default. `web/model.py`'s
    ports return a sentence while the store has nowhere to keep this data.
    """

    class BlindRoutinePort(StubRoutinePort):
        def capability_note(self):
            return "This console cannot see routines yet: the store it reads has nowhere to keep them."

        def list_routines(self):
            return []

    class BlindAttentionPort(StubAttentionPort):
        def capability_note(self):
            return "This console cannot see your attention queue yet."

        def queue(self):
            return QueueView.ranked([])

    def test_the_routines_surface_says_it_cannot_see_rather_than_that_there_are_none(self):
        client = an_app(routine_port=self.BlindRoutinePort()).test_client()
        body = client.get("/routines/").get_data(as_text=True)
        self.assertIn("cannot see routines yet", body)
        self.assertNotIn("No routines yet", body)

    def test_the_attention_surface_says_it_cannot_see_rather_than_nothing_needs_you(self):
        client = an_app(attention_port=self.BlindAttentionPort()).test_client()
        body = client.get("/attention/").get_data(as_text=True)
        self.assertIn("cannot see your attention queue", body)
        self.assertNotIn("Nothing needs you", body)

    def test_a_port_with_rows_and_no_note_renders_normally(self):
        """The note is optional, and a stub that has nothing to say must not trigger the branch."""
        body = an_app().test_client().get("/routines/").get_data(as_text=True)
        self.assertNotIn("cannot see routines", body)
        self.assertIn("Mon 07 Sep 2026, 08:30 EEST", body)

    def test_a_genuinely_empty_queue_still_says_it_is_not_a_failed_read(self):
        """The absence branch must not swallow the empty-queue case, which is a different sentence."""
        client = an_app(attention_port=StubAttentionPort(items=[])).test_client()
        body = client.get("/attention/").get_data(as_text=True)
        self.assertIn("Nothing needs you", body)
        self.assertIn("not a failed read", body)




class OneConsoleOneClock(unittest.TestCase):
    """Every stored instant prints in the reader's zone.

    Found in a browser, not in review: the routines surface printed EEST and the attention
    surface, one click away, printed UTC for the same afternoon. Both were true, and the pair was
    unreadable.
    """

    def setUp(self):
        set_display_zone(TZ)
        self.addCleanup(set_display_zone, "UTC")
        self.client = an_app().test_client()

    def test_freshness_stamps_are_in_the_readers_zone(self):
        body = self.client.get("/attention/").get_data(as_text=True)
        self.assertIn("EEST", body)
        self.assertNotIn("UTC", body)

    def test_updated_stamp_uses_the_reader_zone_even_when_the_host_is_utc(self):
        import os
        import time
        if not hasattr(time, "tzset"):
            self.skipTest("host timezone cannot be changed in-process on this platform")
        original = os.environ.get("TZ")
        try:
            os.environ["TZ"] = "UTC"
            time.tzset()
            body = self.client.get("/attention/").get_data(as_text=True)
            self.assertIn("06 Sep, 17:34 EEST", body)
            self.assertNotIn("06 Sep, 14:34 UTC", body)
        finally:
            if original is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = original
            time.tzset()

    def test_the_provenance_chain_is_in_the_readers_zone(self):
        body = self.client.get("/attention/atn_8801").get_data(as_text=True)
        self.assertIn("EEST", body)
        self.assertNotIn("UTC", body)

    def test_the_routine_history_is_in_the_readers_zone(self):
        body = self.client.get("/routines/rtn_1042").get_data(as_text=True)
        self.assertIn("EEST", body)
        self.assertNotIn("UTC", body)

    def test_the_next_run_and_the_history_agree_on_the_zone(self):
        body = self.client.get("/routines/rtn_1042").get_data(as_text=True)
        self.assertEqual(body.count("UTC"), 0)


if __name__ == "__main__":
    unittest.main()
