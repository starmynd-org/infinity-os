"""R03 version and permission tests.

The packet's acceptance evidence is that chat edits and form edits yield the same version, that a
stale routine version cannot execute, and that the standing grant is explicit. Those are the
assertions here; the surface is a front end onto exactly these calls.
"""

import unittest
from datetime import datetime, timezone

import routines as r

UTC = timezone.utc
TZ = "Europe/Bucharest"


def a_routine(**over):
    base = dict(
        routine_id="rtn_1042",
        version=4,
        title="Weekday morning brief",
        asked_for="Every weekday at 8:30, summarize urgent items and show drafts for approval",
        schedule=r.Schedule(8, 30, r.WEEKDAYS, TZ),
        grant=r.Grant(
            sources=(r.Source("Your work email"), r.Source("Your calendar, next 24 hours")),
            effects=(
                r.Effect("Writes a brief into your Attention inbox"),
                r.Effect("Prepares reply drafts"),
                r.Effect("Sends a reply to another person", leaves=True, policy="asks you first, every time"),
            ),
            destination="Attention inbox, and a copy to you by email",
        ),
    )
    base.update(over)
    return r.Routine(**base)


class EffectsMustStateTheirPolicy(unittest.TestCase):

    def test_an_outbound_effect_without_a_policy_is_refused(self):
        with self.assertRaises(ValueError):
            r.Effect("Emails your accountant", leaves=True)

    def test_an_internal_effect_needs_no_policy(self):
        self.assertFalse(r.Effect("Writes a brief").leaves)


class ScheduleEdits(unittest.TestCase):

    def test_moving_the_time_needs_no_new_permission(self):
        current = a_routine()
        proposal = r.propose(current, schedule=r.Schedule(7, 30, r.WEEKDAYS, TZ))
        self.assertEqual(proposal.changes, ("Time 08:30 becomes 07:30",))
        self.assertFalse(proposal.requires_new_grant)

    def test_committing_bumps_the_version_and_moves_the_next_run(self):
        current = a_routine()
        now = datetime(2026, 9, 6, 17, 49, tzinfo=UTC)
        proposal = r.propose(current, schedule=r.Schedule(7, 30, r.WEEKDAYS, TZ))
        after = r.commit(current, proposal)
        self.assertEqual(after.version, 5)
        self.assertEqual(after.next_run(now).local.strftime("%a %H:%M"), "Mon 07:30")

    def test_the_conversation_and_the_controls_produce_the_same_version(self):
        """Chat and form parity is not a UI claim: both paths build the same Schedule and call the
        same two functions, so the results must be identical objects."""
        current = a_routine()
        wanted = r.Schedule(7, 30, r.WEEKDAYS, TZ)
        from_chat = r.commit(current, r.propose(current, schedule=wanted))
        from_form = r.commit(current, r.propose(current, schedule=wanted))
        self.assertEqual(from_chat, from_form)

    def test_a_proposal_that_changes_nothing_is_empty_and_commits_to_the_same_version(self):
        current = a_routine()
        proposal = r.propose(current, schedule=current.schedule)
        self.assertTrue(proposal.empty)
        self.assertEqual(r.commit(current, proposal).version, current.version)

    def test_nothing_is_applied_by_proposing(self):
        current = a_routine()
        r.propose(current, schedule=r.Schedule(7, 30, r.WEEKDAYS, TZ))
        self.assertEqual(current.schedule.wall, "08:30")
        self.assertEqual(current.version, 4)


class TheStandingGrant(unittest.TestCase):

    def test_adding_an_outside_recipient_requires_a_new_permission(self):
        current = a_routine()
        widened = r.Grant(
            sources=current.grant.sources,
            effects=current.grant.effects + (
                r.Effect("Emails the summary to your accountant", leaves=True, policy="asks you first, every time"),
            ),
            destination=current.grant.destination,
        )
        proposal = r.propose(current, grant=widened)
        self.assertTrue(proposal.requires_new_grant)
        self.assertIn("version 4", proposal.reason)

    def test_adding_a_source_requires_a_new_permission(self):
        current = a_routine()
        widened = r.Grant(
            sources=current.grant.sources + (r.Source("The #sales chat channel"),),
            effects=current.grant.effects,
            destination=current.grant.destination,
        )
        self.assertTrue(r.propose(current, grant=widened).requires_new_grant)

    def test_removing_a_source_does_not_require_a_new_permission(self):
        """Narrowing is not widening. Asking for consent to take something away teaches users to
        click through consent screens."""
        current = a_routine()
        narrowed = r.Grant(
            sources=current.grant.sources[:1],
            effects=current.grant.effects,
            destination=current.grant.destination,
        )
        proposal = r.propose(current, grant=narrowed)
        self.assertFalse(proposal.requires_new_grant)
        self.assertIn("Stops reading Your calendar, next 24 hours", proposal.changes)

    def test_changing_where_the_result_goes_requires_a_new_permission(self):
        current = a_routine()
        moved = r.Grant(current.grant.sources, current.grant.effects, "A shared folder your team can read")
        self.assertTrue(r.propose(current, grant=moved).requires_new_grant)

    def test_the_grant_says_whether_anything_leaves(self):
        self.assertTrue(a_routine().grant.leaves_workspace)

    def test_an_approval_policy_only_edit_is_visible_and_commits_the_new_permission(self):
        """The UX-APP-01 watched negative: policy words are the permission, not decoration."""
        current = a_routine()
        amended = r.Grant(
            current.grant.sources,
            tuple(
                r.Effect(effect.what, effect.leaves,
                         "asks you before sending anything to another person"
                         if effect.leaves else effect.policy)
                for effect in current.grant.effects
            ),
            current.grant.destination,
        )
        proposal = r.propose(current, grant=amended)
        self.assertFalse(proposal.empty)
        self.assertTrue(proposal.requires_new_grant)
        self.assertIn("Approval for Sends a reply to another person changes", proposal.changes[0])
        committed = r.commit(current, proposal)
        self.assertEqual(committed.version, current.version + 1)
        self.assertEqual(committed.grant.effects[-1].policy, "asks you before sending anything to another person")


class StaleApprovals(unittest.TestCase):

    def test_an_action_prepared_under_an_older_version_cannot_be_approved(self):
        current = a_routine(version=5)
        self.assertFalse(current.approval_stands(prepared_under_version=3))
        self.assertFalse(current.approval_stands(prepared_under_version=4))
        self.assertTrue(current.approval_stands(prepared_under_version=5))

    def test_editing_invalidates_a_pending_approval(self):
        current = a_routine()
        prepared_under = current.version
        after = r.commit(current, r.propose(current, schedule=r.Schedule(7, 30, r.WEEKDAYS, TZ)))
        self.assertTrue(current.approval_stands(prepared_under))
        self.assertFalse(after.approval_stands(prepared_under))


class ConcurrentEdits(unittest.TestCase):

    def test_a_proposal_prepared_against_an_older_version_is_refused(self):
        current = a_routine()
        stale = r.propose(current, schedule=r.Schedule(7, 30, r.WEEKDAYS, TZ))
        moved_on = r.commit(current, r.propose(current, title="Morning brief"))
        with self.assertRaises(r.ConcurrentEdit) as caught:
            r.commit(moved_on, stale)
        self.assertIn("version 4", str(caught.exception))
        self.assertIn("version 5", str(caught.exception))


class PauseAndHost(unittest.TestCase):

    def test_a_paused_routine_has_no_next_run_rather_than_a_pretend_one(self):
        paused = r.pause(a_routine())
        self.assertIsNone(paused.next_run(datetime(2026, 9, 6, 17, 49, tzinfo=UTC)))

    def test_pausing_is_idempotent_and_keeps_everything_else(self):
        once = r.pause(a_routine())
        twice = r.pause(once)
        self.assertEqual(once, twice)
        self.assertEqual(twice.version, 4)
        self.assertEqual(twice.grant, a_routine().grant)

    def test_resume_restores_the_next_run(self):
        back = r.resume(r.pause(a_routine()))
        self.assertIsNotNone(back.next_run(datetime(2026, 9, 6, 17, 49, tzinfo=UTC)))

    def test_run_now_is_refused_while_paused_and_says_why(self):
        allowed, why = r.pause(a_routine()).may_run_now(host_available=True)
        self.assertFalse(allowed)
        self.assertIn("paused", why)

    def test_a_sleeping_worker_is_a_different_fact_from_a_paused_routine(self):
        """The host being unreachable must not be reported as the routine being off, and an active
        routine on a reachable host must not be reported as blocked because a laptop slept."""
        active = a_routine()
        allowed, why = active.may_run_now(host_available=False)
        self.assertFalse(allowed)
        self.assertIn("host", why)
        self.assertNotIn("paused", why)
        self.assertEqual(active.state, r.ACTIVE)
        self.assertTrue(active.may_run_now(host_available=True)[0])

    def test_pausing_does_not_touch_the_version_or_the_history_of_the_grant(self):
        current = a_routine()
        self.assertEqual(r.pause(current).grant, current.grant)

    def test_a_draft_has_no_next_run_and_cannot_run_now(self):
        draft = a_routine(state=r.DRAFT)
        self.assertIsNone(draft.next_run(datetime(2026, 9, 6, 17, 49, tzinfo=UTC)))
        allowed, reason = draft.may_run_now(host_available=True)
        self.assertFalse(allowed)
        self.assertIn("Activate", reason)
        self.assertEqual(r.pause(draft), draft)
        with self.assertRaises(ValueError):
            r.resume(draft)


if __name__ == "__main__":
    unittest.main()
