"""R04 view tests: what a screen is allowed to claim.

These are pure and need no console, no browser and no database, so they run in the plain runner
and cannot be skipped for want of a store. The packet's acceptance evidence is asserted directly:
unknown economic benefit stays unknown, ranked and sortable surfaces are labelled correctly, and
every visible status carries its evidence and its time.
"""

import unittest
from datetime import datetime, timedelta, timezone

from web.views import ABSENT, FRESH, STALE, Freshness, Impact, Provenance, ProvenanceStep, QueueItem, QueueView

UTC = timezone.utc
NOW = datetime(2026, 9, 6, 18, 0, tzinfo=UTC)


def a_provenance(**over):
    base = dict(
        source_label="Your work email",
        zone="company",
        reference="msg/8f21c",
        steps=(ProvenanceStep(NOW - timedelta(minutes=26), "captured from the work inbox", "Intake"),),
    )
    base.update(over)
    return Provenance(**base)


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
        provenance=a_provenance(),
    )
    base.update(over)
    return QueueItem(**base)


class UnknownStaysUnknown(unittest.TestCase):

    def test_an_unknown_impact_has_no_value_to_render(self):
        impact = Impact.unknown("the store holds no euro figure for this account")
        self.assertIsNone(impact.value)
        self.assertFalse(impact.is_known)
        self.assertIn("unmeasured", impact.render())

    def test_an_unknown_impact_must_say_why(self):
        with self.assertRaises(ValueError):
            Impact.unknown("")

    def test_a_measured_impact_must_name_what_measured_it(self):
        with self.assertRaises(ValueError):
            Impact.measured("12 min", "")

    def test_a_measured_impact_renders_its_provenance_beside_the_number(self):
        impact = Impact.measured("12 min", "median over 9 samples of this reply kind")
        self.assertIn("12 min", impact.render())
        self.assertIn("9 samples", impact.render())

    def test_no_effect_claimed_is_not_the_same_as_unknown(self):
        self.assertNotEqual(Impact.none_claimed().kind, Impact.unknown("x").kind)
        self.assertIn("no effect claimed", Impact.none_claimed().render())

    def test_an_unreadable_item_cannot_carry_a_measured_impact(self):
        """A figure about content nobody read could only have come from reading it."""
        with self.assertRaises(ValueError):
            an_item(readable=False, impact=Impact.measured("400 EUR", "from the quote body"))


class FreshnessHasThreeStates(unittest.TestCase):

    def test_a_recent_read_is_fresh(self):
        f = Freshness.read_at(NOW - timedelta(minutes=5), NOW, timedelta(minutes=15))
        self.assertEqual(f.state, FRESH)
        self.assertIn("as of", f.render())

    def test_an_old_read_is_stale_and_still_has_its_value(self):
        f = Freshness.read_at(NOW - timedelta(hours=31), NOW, timedelta(minutes=15))
        self.assertEqual(f.state, STALE)
        self.assertTrue(f.has_value)
        self.assertIn("31 h ago", f.render())

    def test_never_read_is_not_stale(self):
        f = Freshness.never_read("not indexed for your account")
        self.assertEqual(f.state, ABSENT)
        self.assertFalse(f.has_value)
        self.assertIn("not read yet", f.render())

    def test_a_stale_read_states_the_interval_it_missed(self):
        f = Freshness.read_at(NOW - timedelta(hours=31), NOW, timedelta(minutes=15))
        self.assertIn("every 15 min", f.why)

    def test_naive_instants_are_refused(self):
        with self.assertRaises(ValueError):
            Freshness.read_at(datetime(2026, 9, 6, 18, 0), NOW, timedelta(minutes=15))


class SortingIsNotPrioritising(unittest.TestCase):

    def setUp(self):
        self.items = [
            an_item(item_id="a", title="Zebra", rank=1, tier=1),
            an_item(item_id="b", title="Apple", rank=2, tier=2),
            an_item(item_id="c", title="Mango", rank=3, tier=3),
        ]
        self.view = QueueView.ranked(self.items, checked_at=NOW)

    def test_the_ranked_view_is_in_rank_order_and_says_so(self):
        self.assertEqual([i.item_id for i in self.view.items], ["a", "b", "c"])
        self.assertFalse(self.view.reordered)
        self.assertIn("Ranked order", self.view.order_note)

    def test_sorting_changes_the_display_and_flags_itself(self):
        sorted_view = self.view.sorted_by("title")
        self.assertEqual([i.item_id for i in sorted_view.items], ["b", "c", "a"])
        self.assertTrue(sorted_view.reordered)
        self.assertIn("not what Infinity does first", sorted_view.order_note)

    def test_sorting_does_not_change_the_working_order(self):
        self.assertEqual(self.view.sorted_by("title").working_order, ("a", "b", "c"))

    def test_every_row_keeps_its_rank_after_a_sort(self):
        ranks = {i.item_id: i.rank for i in self.view.sorted_by("title", descending=True).items}
        self.assertEqual(ranks, {"a": 1, "b": 2, "c": 3})

    def test_an_unknown_sort_column_is_refused_rather_than_ignored(self):
        with self.assertRaises(ValueError):
            self.view.sorted_by("impact")


class EmptyAndLarge(unittest.TestCase):

    def test_an_empty_queue_is_distinguished_from_a_failed_read(self):
        view = QueueView.ranked([], checked_at=NOW)
        self.assertTrue(view.empty)
        self.assertIn("not a failed read", view.empty_note)
        self.assertIn("Last checked", view.empty_note)

    def test_an_empty_queue_with_no_check_time_says_that_instead_of_inventing_one(self):
        view = QueueView.ranked([])
        self.assertIn("not recorded", view.empty_note)

    def test_a_large_queue_keeps_its_ranked_order(self):
        many = [an_item(item_id="i%d" % n, rank=n + 1, tier=(n % 4) + 1) for n in range(240)]
        view = QueueView.ranked(many)
        self.assertEqual(len(view.items), 240)
        self.assertEqual(view.items[0].item_id, "i0")
        self.assertEqual(view.working_order[:3], ("i0", "i1", "i2"))


class RestrictedItems(unittest.TestCase):

    def setUp(self):
        self.restricted = an_item(
            item_id="atn_8805",
            title="Item in a workspace you cannot read",
            kind="restricted",
            tier=2,
            rank=4,
            readable=False,
            why="ranked from metadata only; its content was never indexed for you",
            impact=Impact.unknown("content not readable by you, so no impact can be stated"),
            freshness=Freshness.never_read("not indexed for your account"),
            provenance=a_provenance(
                source_label="Client workspace (restricted)",
                zone="restricted",
                reference=None,
                steps=(ProvenanceStep(NOW - timedelta(hours=2), "existence recorded; content not read",
                                      "authorization boundary"),),
            ),
        )

    def test_the_inspector_returns_a_refusal_and_no_content_field(self):
        shown = self.restricted.inspect()
        self.assertFalse(shown["readable"])
        self.assertIsNone(shown["content"])
        self.assertIn("not allowed to read", shown["message"])

    def test_the_refusal_carries_no_reference_a_caller_could_fetch(self):
        self.assertNotIn("reference", self.restricted.inspect())

    def test_a_readable_item_shows_its_evidence_and_its_time(self):
        shown = an_item().inspect()
        self.assertTrue(shown["readable"])
        self.assertIn("as of", shown["freshness"])
        self.assertTrue(shown["steps"])

    def test_provenance_with_no_steps_is_refused(self):
        with self.assertRaises(ValueError):
            Provenance(source_label="x", zone="company", reference=None, steps=())


if __name__ == "__main__":
    unittest.main()
