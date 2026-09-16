"""Attention's Freshness and Impact chips show a headline on wide rows and lose no words.

A4 INFINITY-STREAMLINE, ALPHA-SPRINT-1, STREAMLINE item 3, lighter form (ruled in scope by
CLOSEDOWN-COMMANDER, 2026-09-14). Every chip repeated a full sentence on every row, which made
seven rows about 1,400 px tall at 1440 and tall narrow columns with the panel open (STILLS). The
chip now renders its headline and its reason in two spans. At 1100 px and wider the reason is not
drawn in the table, but it stays in the markup, the chip's tooltip and the row's panel. Below that
width the chip reads byte for byte as before.

The contract that makes this safe is `headline() + " (reason)" == render()` for every state, so
the short form can never say something the long form does not.
"""

import os
import re
import unittest
from datetime import datetime, timedelta, timezone

from web.tests.test_routines_and_attention_render import StubAttentionPort, an_app, app_queue_fixture
from web.views import Freshness, Impact

WEB_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INBOX = os.path.join(WEB_DIR, "templates", "attention", "inbox.html")
NOW = datetime(2026, 9, 6, 15, 0, tzinfo=timezone.utc)

CHIP_RE = re.compile(r'<td data-label="(Freshness|Impact)"><a class="att-chip[^"]*"[^>]*title="([^"]*)">(.*?)</a></td>', re.S)
TAG_RE = re.compile(r"<[^>]+>")


def joined(thing):
    reason = thing.reason()
    return thing.headline() + (" (%s)" % reason if reason else "")


class HeadlinePlusReasonIsTheSentence(unittest.TestCase):

    def test_every_impact_kind(self):
        cases = [Impact.measured("12 min", "median over 9 samples"), Impact.estimated("2 h", "prior renewals"),
                 Impact.unknown("no value is recorded"), Impact.none_claimed()]
        for impact in cases:
            self.assertEqual(joined(impact), impact.render(), impact.kind)
        self.assertEqual(Impact.none_claimed().reason(), "")

    def test_every_freshness_state(self):
        cases = [Freshness.read_at(NOW - timedelta(minutes=5), NOW, timedelta(minutes=30)),
                 Freshness.read_at(NOW - timedelta(hours=31), NOW, timedelta(minutes=15)),
                 Freshness.never_read("not indexed for your account")]
        for freshness in cases:
            self.assertEqual(joined(freshness), freshness.render(), freshness.state)


class TheRowChipKeepsEveryWord(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        client = an_app(attention_port=StubAttentionPort(app_queue_fixture())).test_client()
        cls.body = client.get("/attention/").get_data(as_text=True)
        cls.chips = CHIP_RE.findall(cls.body)
        print("\n  DENOMINATORS  freshness+impact chips %d" % len(cls.chips))

    def test_each_chip_is_headline_and_reason_spans_titled_with_the_sentence(self):
        self.assertEqual(len(self.chips), 240 * 2, "the fixture chips did not render")
        for label, title, inner in self.chips:
            self.assertIn('<span class="att-chip-head">', inner, label)
            text = TAG_RE.sub("", inner)
            self.assertEqual(text.replace("&#39;", "'"), title.replace("&#39;", "'"), (label, inner[:120]))

    def test_the_full_sentences_are_still_on_the_page(self):
        for words in ("not measured", "no value is recorded on this account", "12 min", "9 samples",
                      "stale, as of", "every 15 min"):
            self.assertIn(words, self.body)

    def test_only_wide_rows_hide_the_reason(self):
        source = open(INBOX, encoding="utf-8").read()
        rule = ".att-table .att-chip .att-chip-why{display:none}"
        self.assertEqual(source.count(rule), 1)
        wide = re.search(r"@media\(min-width:1100px\)\{(.*?)\n  \}", source, re.S)
        self.assertIsNotNone(wide, "the wide block is gone")
        self.assertIn(rule, wide.group(1))


if __name__ == "__main__":
    unittest.main()
