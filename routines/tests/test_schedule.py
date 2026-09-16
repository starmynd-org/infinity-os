"""R03 schedule tests. Every assertion is against tzdata, not against a remembered rule.

The two daylight-saving cases are the reason this module exists, so they are asserted at the
instant, not at the wall clock: a test that only checks the printed string passes for an
implementation that prints the right words at the wrong moment.
"""

import unittest
from datetime import date, datetime, timedelta, timezone

import routines as r

UTC = timezone.utc
TZ = "Europe/Bucharest"


class ScheduleConstruction(unittest.TestCase):

    def test_a_schedule_with_no_days_is_refused(self):
        with self.assertRaises(ValueError):
            r.Schedule(8, 30, frozenset(), TZ)

    def test_an_unknown_zone_fails_at_construction_not_at_the_first_run(self):
        with self.assertRaises(Exception):
            r.Schedule(8, 30, r.WEEKDAYS, "Europe/Atlantis")

    def test_out_of_range_time_is_refused(self):
        with self.assertRaises(ValueError):
            r.Schedule(24, 0, r.WEEKDAYS, TZ)


class OrdinaryDays(unittest.TestCase):

    def setUp(self):
        self.weekday_0830 = r.Schedule(8, 30, r.WEEKDAYS, TZ)

    def test_next_run_from_a_sunday_evening_is_monday_morning(self):
        now = datetime(2026, 9, 6, 17, 49, tzinfo=UTC)          # Sunday
        nxt = r.next_occurrence(now, self.weekday_0830)
        self.assertEqual(nxt.kind, "ok")
        self.assertEqual(nxt.local.strftime("%a %d %b %H:%M"), "Mon 07 Sep 08:30")
        self.assertEqual(nxt.at, datetime(2026, 9, 7, 5, 30, tzinfo=UTC))  # 08:30 EEST

    def test_a_weekday_schedule_skips_the_weekend(self):
        friday_evening = datetime(2026, 9, 4, 18, 0, tzinfo=UTC)
        nxt = r.next_occurrence(friday_evening, self.weekday_0830)
        self.assertEqual(nxt.local.weekday(), 0)

    def test_the_boundary_is_strict(self):
        """A run exactly at `after` is in the past, not the next one. Otherwise a scheduler that
        wakes at its own fire time fires the same occurrence twice."""
        exact = datetime(2026, 9, 7, 5, 30, tzinfo=UTC)
        nxt = r.next_occurrence(exact, self.weekday_0830)
        self.assertEqual(nxt.local.strftime("%a %d %b"), "Tue 08 Sep")

    def test_a_naive_instant_is_refused(self):
        with self.assertRaises(ValueError):
            r.next_occurrence(datetime(2026, 9, 6, 17, 49), self.weekday_0830)


class TheClocksGoForward(unittest.TestCase):
    """Europe/Bucharest, Sunday 28 March 2027: 03:00 becomes 04:00, so 03:30 does not exist."""

    def setUp(self):
        self.sunday_0330 = r.Schedule(3, 30, frozenset({6}), TZ)

    def test_the_gap_is_detected_rather_than_silently_shifted(self):
        occurrence = r.resolve(date(2027, 3, 28), self.sunday_0330)
        self.assertEqual(occurrence.kind, "gap")
        self.assertTrue(occurrence.surprising)

    def test_it_runs_at_the_instant_the_requested_time_would_have_been(self):
        """Terminal 08, CAP14-REV-010. The earlier answer fired at the transition instant, 04:00,
        which is thirty minutes earlier in absolute time than the user asked for."""
        occurrence = r.resolve(date(2027, 3, 28), self.sunday_0330)
        self.assertEqual(occurrence.at, datetime(2027, 3, 28, 1, 30, tzinfo=UTC))
        self.assertEqual(occurrence.local.strftime("%H:%M"), "04:30")
        self.assertEqual(occurrence.local.utcoffset(), timedelta(hours=3))

    def test_the_gap_does_not_move_the_run_closer_to_the_previous_one(self):
        """The property the instant above protects, stated independently of the number."""
        day_before = r.resolve(date(2027, 3, 27), self.sunday_0330)
        gap_day = r.resolve(date(2027, 3, 28), self.sunday_0330)
        self.assertEqual(gap_day.at - day_before.at, timedelta(hours=24))

    def test_it_is_not_skipped_and_not_doubled(self):
        window_start = datetime(2027, 3, 27, 12, 0, tzinfo=UTC)
        window_end = datetime(2027, 3, 29, 12, 0, tzinfo=UTC)
        due = r.missed_since(window_start, window_end, self.sunday_0330)
        self.assertEqual(len(due), 1)
        self.assertEqual(due[0].kind, "gap")

    def test_the_explanation_names_both_the_asked_for_time_and_the_clock_reading(self):
        occurrence = r.resolve(date(2027, 3, 28), self.sunday_0330)
        text = occurrence.explain()
        self.assertIn("03:30", text)
        self.assertIn("04:30", text)
        self.assertIn("the same moment", text)
        self.assertNotIn("immediately after the jump", text)

    def test_an_ordinary_morning_schedule_is_untouched_by_the_change(self):
        weekday_0830 = r.Schedule(8, 30, r.WEEKDAYS, TZ)
        monday_after = r.resolve(date(2027, 3, 29), weekday_0830)
        self.assertEqual(monday_after.kind, "ok")
        self.assertEqual(monday_after.local.strftime("%H:%M"), "08:30")


class TheClocksGoBack(unittest.TestCase):
    """Sunday 31 October 2027: 04:00 becomes 03:00, so 03:30 happens twice."""

    def setUp(self):
        self.sunday_0330 = r.Schedule(3, 30, frozenset({6}), TZ)

    def test_the_repeat_is_detected(self):
        occurrence = r.resolve(date(2027, 10, 31), self.sunday_0330)
        self.assertEqual(occurrence.kind, "repeat")

    def test_it_runs_on_the_first_pass(self):
        occurrence = r.resolve(date(2027, 10, 31), self.sunday_0330)
        self.assertEqual(occurrence.at, datetime(2027, 10, 31, 0, 30, tzinfo=UTC))
        self.assertEqual(occurrence.local.utcoffset(), timedelta(hours=3))  # still EEST

    def test_the_second_pass_does_not_produce_a_second_run(self):
        window_start = datetime(2027, 10, 30, 12, 0, tzinfo=UTC)
        window_end = datetime(2027, 11, 1, 12, 0, tzinfo=UTC)
        due = r.missed_since(window_start, window_end, self.sunday_0330)
        self.assertEqual(len(due), 1)


class MissedRuns(unittest.TestCase):

    def test_every_due_run_in_the_gap_is_reported(self):
        weekday_0830 = r.Schedule(8, 30, r.WEEKDAYS, TZ)
        last_seen = datetime(2026, 9, 1, 0, 0, tzinfo=UTC)     # Tuesday, before that day's run
        now = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)          # Friday lunchtime
        due = r.missed_since(last_seen, now, weekday_0830)
        self.assertEqual([o.local.strftime("%a") for o in due], ["Tue", "Wed", "Thu", "Fri"])

    def test_nothing_is_due_when_no_time_has_passed(self):
        weekday_0830 = r.Schedule(8, 30, r.WEEKDAYS, TZ)
        moment = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
        self.assertEqual(r.missed_since(moment, moment, weekday_0830), [])

    def test_missed_runs_are_returned_not_executed(self):
        """The contract is the return value: this module cannot fire anything, which is what stops
        four skipped mornings from arriving at once when a host comes back."""
        weekday_0830 = r.Schedule(8, 30, r.WEEKDAYS, TZ)
        due = r.missed_since(datetime(2026, 9, 1, 0, 0, tzinfo=UTC),
                             datetime(2026, 9, 4, 12, 0, tzinfo=UTC), weekday_0830)
        self.assertTrue(all(isinstance(o, r.Occurrence) for o in due))


if __name__ == "__main__":
    unittest.main()
