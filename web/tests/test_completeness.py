"""R04: a finished result that did not cover everything has to say so.

The state comes from C01's `Receipt.completeness` (complete / partial / uncertain). The reason it
is a typed component rather than a sentence in the item's prose: "the pipeline summary finished"
and "the pipeline summary finished without the sales channel" are different claims, and the second
one is the one a person acts on differently.
"""

import unittest
from datetime import datetime, timedelta, timezone

from web.views import (COMPLETE, PARTIAL, UNCERTAIN, Completeness, Freshness, Impact, Provenance,
                       ProvenanceStep, QueueItem)

UTC = timezone.utc
NOW = datetime(2026, 9, 6, 18, 0, tzinfo=UTC)


def an_item(**over):
    base = dict(
        item_id="atn_8803",
        title="Pipeline summary could not include the sales channel",
        kind="exception", tier=1, rank=3,
        why="a routine finished with a source missing, so its output is incomplete",
        impact=Impact.unknown("the effect of the missing source is not measured"),
        freshness=Freshness.read_at(NOW - timedelta(hours=31), NOW, timedelta(minutes=15)),
        provenance=Provenance("The sales chat channel", "company", "conn/chat", (
            ProvenanceStep(NOW - timedelta(hours=5), "read failed: access expired", "chat connector"),)),
    )
    base.update(over)
    return QueueItem(**base)


class PartialMustNameTheHole(unittest.TestCase):

    def test_partial_without_a_list_is_refused(self):
        """'Partial' alone tells a reader there is a hole without telling them where."""
        with self.assertRaises(ValueError):
            Completeness.partial([])

    def test_partial_names_what_was_missed(self):
        c = Completeness.partial(["the sales chat channel"], "its access had expired")
        self.assertEqual(c.state, PARTIAL)
        self.assertIn("the sales chat channel", c.render())
        self.assertIn("access had expired", c.render())

    def test_partial_reads_as_a_result_and_not_as_a_failure(self):
        """A partial result is still useful. The wording must not imply the run failed."""
        text = Completeness.partial(["one source"]).render()
        self.assertTrue(text.startswith("finished"))
        self.assertNotIn("failed", text)
        self.assertNotIn("error", text)


class UncertainIsWorseThanPartial(unittest.TestCase):

    def test_uncertain_must_say_what_it_cannot_account_for(self):
        with self.assertRaises(ValueError):
            Completeness.uncertain("")

    def test_uncertain_does_not_render_as_partial(self):
        """With partial you know the shape of the hole; with uncertain you do not know there is one."""
        uncertain = Completeness.uncertain("two sources returned no cursor").render()
        partial = Completeness.partial(["a source"]).render()
        self.assertIn("cannot tell", uncertain)
        self.assertNotIn("cannot tell", partial)


class TheContractVocabulary(unittest.TestCase):

    def test_every_contract_value_maps(self):
        self.assertTrue(Completeness.from_contract(COMPLETE).whole)
        self.assertEqual(Completeness.from_contract(PARTIAL, ["x"]).state, PARTIAL)
        self.assertEqual(Completeness.from_contract(UNCERTAIN, why="no cursor").state, UNCERTAIN)

    def test_an_unknown_value_is_refused_rather_than_defaulted(self):
        """Defaulting an unrecognised state to 'complete' would be the worst possible guess."""
        with self.assertRaises(ValueError):
            Completeness.from_contract("finished")

    def test_uncertain_from_the_contract_without_a_reason_still_gets_one(self):
        self.assertIn("no reason", Completeness.from_contract(UNCERTAIN).render())


class TheItemCarriesIt(unittest.TestCase):

    def test_an_item_without_completeness_is_not_incomplete(self):
        """Absent is not a claim either way, and must not render as a caveat."""
        self.assertFalse(an_item().incomplete)
        self.assertIsNone(an_item().inspect()["completeness"])

    def test_a_partial_item_is_flagged_and_inspectable(self):
        item = an_item(completeness=Completeness.partial(["the sales chat channel"],
                                                         "its access had expired"))
        self.assertTrue(item.incomplete)
        self.assertIn("the sales chat channel", item.inspect()["completeness"])

    def test_a_complete_item_is_not_flagged(self):
        self.assertFalse(an_item(completeness=Completeness.complete()).incomplete)


if __name__ == "__main__":
    unittest.main()
