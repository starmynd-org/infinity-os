"""No internal word reaches a screen: the E11 census, kept in the repo so it cannot come back quietly.

W5-B7 (v0.02, E11). The release audit's plain-language rows (IX-R07-01 to -11, gate case IX-R16-05)
count a fixed word list in the text a browser renders on every product route. At f46a46a that count
was 52 hits on 9 of 18 routes: table names (`brain.agent`), column names (`work_item.result`),
migration numbers, "heartbeat", "verbs", "Runtime store evidence", "not measured". The words were
replaced with plain ones in the same change as this file. The browser census on a seeded console
is the release measurement; this suite is the store-free half that runs on every push:

  1. every template's VISIBLE text (comments, Jinja tags, script, style and markup stripped) carries
     none of the words, with a planted word appended to every template as the control;
  2. the Python strings that are printed straight into the Attention page carry none of them;
  3. the Attention page and both Data pages, rendered with stub data, carry none of them in their
     visible text, with a planted word injected into the same render as the control.

One exemption, named rather than hidden: the exact terminal command in the Send back hint
(`macros.html`), which sits in a panel that is hidden until Send back is pressed. A command a person
copies is not prose, and it cannot be spelled in plain words and still run; row 0414's
`test_the_card_says_where_the_work_went.py` requires it to be readable in that panel.
"""

import os
import pathlib
import re
import unittest
from unittest import mock

os.environ["BRAIN_PG_DB"] = os.environ.get("B7_WORDS_DB", "brain_b7_words_no_such_db")
if os.environ["BRAIN_PG_DB"] == "brain":
    raise SystemExit("test_no_internal_words_on_screen: refusing the live store; this suite needs no rows")

# The release audit's R07 Infinity list, word for word, plus "heartbeat" from the E11 brief.
WORDS = ("brain.", "work_item", "queue_open", "primary_verb", "runtime_flag", "agent_claimable",
         "migration", "store evidence", "verb", "Declared for ranking", "permitted metadata",
         "no data: never read", "not measured", "Definition of done", "heartbeat")
PLANT = "the brain.agent table is written through heartbeat"

ROOT = pathlib.Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates"
EXEMPT_COMMAND = "<code>swarm set {{ item.id }} agent_claimable false --as-operator</code>"


def visible(html):
    """Visible text of a template or a rendered page, plus the PROSE inside Jinja tags.

    A sentence can reach the screen from inside an expression, `{{ x or '(none: ...)' }}`, and
    stripping every tag hid exactly that on the task page at f46a46a ("posted before migration 14").
    So a quoted literal inside `{{ }}` or `{% %}` that contains a space is kept as text; a literal
    without a space is an identifier (`'work_item'`) and is not prose.
    """
    s = re.sub(r"<!--.*?-->", " ", html, flags=re.S)
    s = re.sub(r"\{#.*?#\}", " ", s, flags=re.S)
    prose = []
    for block in re.findall(r"\{\{.*?\}\}|\{%.*?%\}", s, flags=re.S):
        for a, b in re.findall(r"'([^']*)'|\"([^\"]*)\"", block):
            lit = a or b
            if " " in lit.strip():
                prose.append(lit)
    s = re.sub(r"\{%.*?%\}", " ", s, flags=re.S)
    s = re.sub(r"\{\{.*?\}\}", " ", s, flags=re.S)
    s = re.sub(r"<script.*?</script>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<style.*?</style>", " ", s, flags=re.S | re.I)
    return re.sub(r"<[^>]+>", " ", s) + " " + " ".join(prose)


def hits(text):
    low = text.lower()
    return {w: low.count(w.lower()) for w in WORDS if w.lower() in low}


class TemplatesCarryNoInternalWords(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.files = sorted(TEMPLATES.rglob("*.html"))
        print("\n  DENOMINATOR  templates %d, words %d" % (len(cls.files), len(WORDS)))

    def test_the_census_sees_every_template(self):
        self.assertGreaterEqual(len(self.files), 30, "the template tree was not found")

    def test_control_a_planted_word_is_caught_in_every_template(self):
        caught = [p.name for p in self.files
                  if hits(visible(p.read_text(encoding="utf-8") + "\n<p>" + PLANT + "</p>\n"))]
        self.assertEqual(len(caught), len(self.files))

    def test_control_prose_inside_an_expression_is_read_and_an_identifier_is_not(self):
        self.assertTrue(hits(visible("<p>{{ x or '(none: before migration 14)' }}</p>")))
        self.assertEqual(hits(visible("{% if kind == 'work_item' %}<p>ok</p>{% endif %}")), {})

    def test_the_exemption_is_exactly_one_command(self):
        text = (TEMPLATES / "macros.html").read_text(encoding="utf-8")
        self.assertEqual(text.count(EXEMPT_COMMAND), 1)

    def test_no_template_renders_an_internal_word(self):
        found = {}
        for p in self.files:
            text = p.read_text(encoding="utf-8").replace(EXEMPT_COMMAND, " ")
            h = hits(visible(text))
            if h:
                found[p.relative_to(TEMPLATES).as_posix()] = h
        self.assertEqual(found, {})


class PrintedStringsCarryNoInternalWords(unittest.TestCase):

    def test_impact_freshness_signals_permission_and_origin_labels(self):
        from web.model import _StoreAttentionPort
        from web.views.honesty import Freshness, Impact
        from web.views.signals import Signals
        from web import attention_options
        strings = [
            Impact.unknown("no figure is recorded").render(), Impact.unknown("x").headline(),
            Impact.estimated("12 min", "9 samples").render(), Impact.estimated("12 min", "x").headline(),
            Freshness.never_read("not indexed").render(), Freshness.never_read("x").headline(),
            Signals(stakes="high", reversibility="irreversible", confidence=0.2).explain(),
            _StoreAttentionPort.evidence_label,
            attention_options.DONE_DISCLOSURE, attention_options.DONE_INVERSE_NOTE,
        ]
        for option in attention_options.for_row(
                {"source_type": "recommendation", "source_id": 7},
                {"recommendation_state": "open", "recommendation_text": "t"}, operator="b7"):
            strings.append(option.inverse_note or "")
        self.assertGreaterEqual(len(strings), 12, "a recommendation offered no options to read")
        self.assertEqual({s: hits(s) for s in strings if hits(s)}, {})
        self.assertTrue(hits(PLANT), "control: the planted sentence is not caught by the same census")


class RenderedPagesCarryNoInternalWords(unittest.TestCase):

    def census(self, body):
        return hits(visible(body))

    def test_attention_page_with_its_fixture(self):
        from web.tests.test_routines_and_attention_render import (StubAttentionPort, an_app,
                                                                  app_queue_fixture)
        body = an_app(attention_port=StubAttentionPort(app_queue_fixture())).test_client() \
            .get("/attention/").get_data(as_text=True)
        self.assertGreater(body.count("data-queue-row="), 0, "the fixture rendered no rows")
        self.assertEqual(self.census(body), {})
        self.assertTrue(self.census(body.replace("</body>", "<p>" + PLANT + "</p></body>")),
                        "control: a word planted into the same render was not caught")

    def test_both_data_pages_when_empty(self):
        import web.model as model
        from web.app import create_app
        from web.tests.test_data_section_render import fake_queue_view
        empty_sources = {"present": True, "note": None, "sources": []}
        empty_goals = {"present": True, "note": None, "goals": [], "kpis": []}
        with mock.patch.object(model, "data_sources_view", return_value=empty_sources), \
                mock.patch.object(model, "goals_and_kpis_view", return_value=empty_goals), \
                mock.patch.object(model, "queue_view", side_effect=fake_queue_view):
            client = create_app().test_client()
            for path in ("/data", "/data/goals"):
                body = client.get(path).get_data(as_text=True)
                self.assertIn('class="dsempty"', body, "%s did not render its empty state" % path)
                self.assertEqual(self.census(body), {}, path)
                self.assertTrue(self.census(body + "<p>" + PLANT + "</p>"), path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
