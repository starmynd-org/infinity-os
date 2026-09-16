"""R04: the queue's explanation is read from the item's own signals.

The failure this replaces: one constant sentence about how the ranker is meant to think, printed
under every item. It is not an explanation of any particular item, and it stays plausible after
the ranker changes, which is the worst property a status sentence can have.
"""

import unittest
from datetime import datetime, timedelta, timezone

from web.views import Freshness, Impact, Provenance, ProvenanceStep, QueueItem, Signals

UTC = timezone.utc
NOW = datetime(2026, 9, 6, 18, 0, tzinfo=UTC)


def an_item(**over):
    base = dict(
        item_id="atn_8801", title="Renewal quote expires Monday", kind="decision", tier=1, rank=1,
        why="a dated commitment with an external deadline inside 72 h",
        impact=Impact.unknown("no value is recorded on this account"),
        freshness=Freshness.read_at(NOW - timedelta(minutes=22), NOW, timedelta(minutes=30)),
        provenance=Provenance("Your work email", "company", "msg/8f21c", (
            ProvenanceStep(NOW - timedelta(minutes=26), "captured", "Intake"),)),
    )
    base.update(over)
    return QueueItem(**base)


class ClosedVocabulariesAreNotCoerced(unittest.TestCase):

    def test_an_unknown_value_is_refused(self):
        """A packet arriving with stakes 'urgent' is a producer bug. Mapping it to something
        nearby would hide it."""
        with self.assertRaises(ValueError):
            Signals(stakes="urgent")
        with self.assertRaises(ValueError):
            Signals(reversibility="maybe")
        with self.assertRaises(ValueError):
            Signals(urgency="whenever")

    def test_a_signal_this_surface_does_not_know_is_refused(self):
        with self.assertRaises(ValueError):
            Signals.from_contract({"stakes": "high", "novelty": "high"})

    def test_out_of_range_numbers_are_refused(self):
        with self.assertRaises(ValueError):
            Signals(confidence=1.4)
        with self.assertRaises(ValueError):
            Signals(dependency_unblocking=-1)

    def test_every_contract_signal_is_accepted(self):
        s = Signals.from_contract({
            "stakes": "critical", "reversibility": "irreversible", "urgency": "decaying",
            "dependency_unblocking": 4, "effort": "large", "confidence": 0.9,
            "charter_alignment": "high"})
        self.assertEqual(s.undeclared, ())

    def test_display_signal_is_inspector_only_not_a_declared_ranking_signal(self):
        signal = Signals.from_contract({"display": {"label": "Source status", "note": "Fixture source is quiet"}})
        self.assertEqual(signal.declared, ())
        self.assertEqual(signal.explain(), "")
        self.assertEqual(signal.display, ("Source status", "Fixture source is quiet"))

    def test_display_signal_matches_the_contracts_optional_label_and_note_shape(self):
        self.assertEqual(Signals.from_contract({"display": {"label": "Only one field"}}).display,
                         ("Only one field", ""))
        self.assertEqual(Signals.from_contract({"display": {"note": "Only a note"}}).display,
                         ("Producer display", "Only a note"))
        self.assertIsNone(Signals.from_contract({"display": {}}).display)
        with self.assertRaises(ValueError):
            Signals.from_contract({"display": None})
        with self.assertRaises(ValueError):
            Signals.from_contract({"display": {"label": 1}})
        with self.assertRaises(ValueError):
            Signals.from_contract({"display": {"unexpected": "x"}})


class TheExplanationUsesOnlyWhatWasSaid(unittest.TestCase):

    def test_nothing_declared_gives_no_sentence(self):
        """Rather than a sentence assembled entirely out of defaults."""
        self.assertEqual(Signals().explain(), "")
        self.assertEqual(an_item(signals=Signals()).explain_rank(), "")

    def test_an_item_with_no_signals_at_all_explains_nothing(self):
        self.assertEqual(an_item().explain_rank(), "")

    def test_the_sentence_names_the_declared_reasons(self):
        s = Signals(urgency="deadline", dependency_unblocking=3, stakes="high",
                    reversibility="irreversible")
        text = s.explain()
        self.assertIn("dated commitment", text)
        self.assertIn("unblocks 3 other items", text)
        self.assertIn("stakes are high", text)
        self.assertIn("cannot be undone", text)

    def test_one_unblocked_item_is_singular(self):
        self.assertIn("unblocks 1 other item", Signals(dependency_unblocking=1).explain())

    def test_a_defaulted_signal_never_appears_as_an_assessment(self):
        """The rule this class exists for. The ranker may treat silence as high stakes; the screen
        may not report that back as something the producer said."""
        text = Signals(urgency="deadline").explain()
        self.assertIn("dated commitment", text)
        self.assertNotIn("stakes", text)
        self.assertNotIn("undone", text)

    def test_low_confidence_is_surfaced_because_it_changes_how_to_read_the_item(self):
        self.assertIn("not confident", Signals(confidence=0.3).explain())

    def test_high_confidence_is_not_announced(self):
        """Confidence is worth saying when it is low. Saying it when high is noise."""
        self.assertEqual(Signals(confidence=0.95).explain(), "")


class TheSilenceIsReportedSeparately(unittest.TestCase):

    def test_it_lists_what_nobody_stated(self):
        note = Signals(urgency="deadline").silence_note()
        self.assertIn("stakes", note)
        self.assertIn("reversibility", note)
        self.assertIn("not something anyone claimed", note)

    def test_a_fully_declared_packet_has_no_silence_note(self):
        s = Signals(stakes="high", reversibility="costly", urgency="soon",
                    dependency_unblocking=0, effort="small", confidence=0.8,
                    charter_alignment="high")
        self.assertEqual(s.silence_note(), "")

    def test_the_conservative_defaults_are_available_to_the_ranker(self):
        """Ordering may assume the worst; the explanation may not. Both behaviours, one object."""
        s = Signals()
        self.assertEqual(s.conservative_stakes(), "high")
        self.assertEqual(s.conservative_reversibility(), "costly")
        self.assertEqual(s.explain(), "")


class TheInspectorCarriesBoth(unittest.TestCase):

    def test_the_reason_and_the_silence_reach_the_inspector(self):
        item = an_item(signals=Signals(urgency="deadline", stakes="high"))
        shown = item.inspect()
        self.assertIn("dated commitment", shown["rank_reason"])
        self.assertIn("reversibility", shown["unstated_signals"])

    def test_an_item_without_signals_carries_empty_strings_not_none(self):
        shown = an_item().inspect()
        self.assertEqual(shown["rank_reason"], "")
        self.assertEqual(shown["unstated_signals"], "")

    def test_display_signal_reaches_only_the_inspector_payload(self):
        item = an_item(signals=Signals.from_contract({"display": {"label": "Sync", "note": "source quiet"}}))
        shown = item.inspect()
        self.assertEqual(shown["display"], ("Sync", "source quiet"))
        self.assertEqual(shown["rank_reason"], "")


if __name__ == "__main__":
    unittest.main()
