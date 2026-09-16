"""Attention finding regressions with connections prohibited; no private fixtures."""
from dataclasses import replace
import unittest
from unittest.mock import patch
from web.tests.check_attention_ux import Scene
from web.views.navigation import context
from web.tests import test_attention_native_options as native


class ReviewEvidence(unittest.TestCase):
    def test_every_completed_inspector_has_report_link(self):
        scene = Scene()
        client = scene.app.test_client()
        for item in scene.items:
            response = client.get('/attention/' + item.item_id + '?offset=7&limit=7')
            self.assertEqual(response.status_code, 200)
            self.assertTrue(b'data-completion-report' in response.data)
            self.assertTrue(b'from_attention=1#completion-report' in response.data)

    def test_restricted_item_cannot_link_to_report(self):
        scene = Scene()
        scene.items[0] = replace(scene.items[0], readable=False, title='Restricted item')
        self.assertFalse(b'data-completion-report' in scene.app.test_client().get(
            '/attention/' + scene.items[0].item_id).data)

    def test_missing_link_plant_is_detected(self):
        with patch('web.views.navigation.report_url', return_value=None):
            with self.assertRaises(AssertionError):
                self.test_every_completed_inspector_has_report_link()

    def test_return_context_cannot_supply_a_destination(self):
        self.assertEqual(context(dict(filter='invalid', sort='bad', offset='-1',
                                      limit='9999999999', next='https://invalid.example')), {'filter': 'queue'})


class TaskRecordLink(unittest.TestCase):
    """P3-02: every readable work-item inspector links to the task's own record with its context."""

    def _scene_with_human(self):
        scene = Scene()
        fixture = native.Admission()
        fixture.setUp()
        try:
            original = native.model.attention_port().queue(limit=0)
        finally:
            fixture.stack.close()
        human = next(i for i in original.items if i.kind == 'human')
        self.assertTrue(human.native_options, 'fixture human row offers no Mark done; no denominator')
        scene.items.append(replace(human, item_id='work_item:0001', rank=9))
        recommendation = next(i for i in original.items if i.kind == 'recommendation')
        scene.items.append(replace(recommendation, rank=10))
        return scene

    def test_readable_work_items_of_every_kind_link_to_their_record_with_the_queue_context(self):
        from web.views.navigation import task_url
        scene = self._scene_with_human()
        client = scene.app.test_client()
        for item in scene.items:
            response = client.get('/attention/' + item.item_id + '?filter=queue&sort=title&dir=desc&offset=7&limit=7')
            self.assertEqual(response.status_code, 200, item.item_id)
            body = response.get_data(as_text=True)
            self.assertIn('inspector &middot; read only.', body, 'the control must reach a rendered inspector')
            if item.item_id.startswith('work_item:'):
                sid = item.item_id.partition(':')[2]
                expected = '/task/' + sid + '?filter=queue&amp;sort=title&amp;dir=desc&amp;offset=7&amp;limit=7&amp;from_attention=1'
                self.assertIn('data-task-record href="' + expected + '"', body, item.item_id)
                self.assertIn("Read this task's brief, report and history", body)
                self.assertEqual(task_url(item.item_id, dict(filter='queue', sort='title', dir='desc', offset='7', limit='7')),
                                 expected.replace('&amp;', '&'))
                self.assertIn('#attention-' + item.item_id, body)
            else:
                self.assertIn('Argument against', body, 'the recommendation inspector rendered its own branch')
                self.assertNotIn('data-task-record', body, item.item_id)
        self.assertIsNone(task_url('recommendation:1', {}))
        self.assertIsNone(task_url('work_item', {}))
        self.assertIsNone(task_url('work_item:', {}))
        self.assertEqual(task_url('work_item:0001', dict(filter='invalid', offset='-1', next='https://x.invalid')),
                         '/task/0001?filter=queue&from_attention=1')

    def test_a_restricted_work_item_links_nowhere(self):
        scene = self._scene_with_human()
        scene.items[0] = replace(scene.items[0], readable=False, title='Restricted item')
        body = scene.app.test_client().get('/attention/' + scene.items[0].item_id).get_data(as_text=True)
        self.assertIn('You are not allowed to read this one', body, 'the restricted inspector rendered')
        self.assertNotIn('data-task-record', body)
        self.assertNotIn('data-completion-report', body)

    def test_the_task_page_returns_to_the_same_inspector_and_a_missing_link_plant_is_detected(self):
        scene = self._scene_with_human()
        client = scene.app.test_client()
        page = client.get('/task/0001?filter=queue&offset=7&limit=7&from_attention=1').get_data(as_text=True)
        self.assertIn('Return to Attention review', page)
        self.assertIn('href="/attention/work_item:0001?filter=queue&amp;offset=7&amp;limit=7"', page)
        self.assertIn('Synthetic work order', page)
        with patch('web.views.navigation.task_url', return_value=None):
            planted = self._scene_with_human()
            body = planted.app.test_client().get('/attention/work_item:0001').get_data(as_text=True)
            self.assertIn('Available actions', body)   # the plant was reached: the inspector rendered
            self.assertNotIn('data-task-record', body)


class ReviewFilter(unittest.TestCase):
    def test_store_port_keeps_all_eight_acceptances_and_original_ranks(self):
        fixture = native.Admission()
        fixture.setUp()
        try:
            port = native.model.attention_port()
            all_items = port.queue(limit=0).items
            reviewed = port.queue(filter_key='review', limit=0).items
            self.assertEqual(sum(i.kind == 'review' for i in reviewed), 8)
            ranks = {i.item_id: (i.rank, i.store_tier) for i in all_items}
            self.assertTrue(all(ranks[i.item_id] == (i.rank, i.store_tier) for i in reviewed))
            self.assertEqual(len(port.queue(filter_key='done', limit=0).items), 8)
        finally:
            fixture.stack.close()

    def test_scoped_empty_filters_do_not_claim_nothing_needs_you(self):
        scene = Scene()
        for key in ('stale', 'restricted'):
            data = scene.app.test_client().get('/attention/?filter=' + key).data
            self.assertTrue(b'No rows match' in data)
            self.assertFalse(b'Nothing needs you' in data)

    def test_old_tier_predicate_plant_fails(self):
        from web.views.queue import FILTERS
        with patch.dict(FILTERS, review=lambda item: item.tier in (1, 2)):
            with self.assertRaises(AssertionError):
                self.test_store_port_keeps_all_eight_acceptances_and_original_ranks()


class ObjectiveRecovery(unittest.TestCase):
    def test_other_sources_and_missing_evidence_have_distinct_recovery(self):
        fixture = native.Admission()
        fixture.setUp()
        try:
            fixture.behind['0001']['agent_claimable'] = True
            fixture.facts[('objective', '1')]['objective_exists'] = False
            del fixture.facts[('objective', '2')]
            port = native.model.attention_port()
            refusals = {r.item_id: r for r in port.queue(limit=0).refusal_items}
            self.assertEqual(refusals['work_item:0001'].source_url, '/task/0001')
            self.assertFalse(refusals['work_item:0001'].needs_preparation)
            self.assertTrue('no longer available' in refusals['objective:1'].reason)
            self.assertTrue('could not be read' in refusals['objective:2'].reason)
            self.assertIsNone(refusals['objective:1'].source_url)
            from web.views.tests.test_act_door import an_app
            data = an_app(port).test_client().get('/attention/preparation/work_item:0001').data
            self.assertTrue(b'/task/0001' in data)
            self.assertFalse(b'this objective' in data)
            self.assertFalse(b'original intake' in data)
        finally:
            fixture.stack.close()

    def test_all_58_identities_have_read_only_recovery(self):
        fixture = native.Admission()
        fixture.setUp()
        try:
            port = native.model.attention_port()
            view = port.queue(limit=0)
            self.assertEqual(len(view.refusal_items), 58)
            self.assertEqual({r.item_id for r in view.refusal_items}, {'objective:' + str(i) for i in range(1, 59)})
            self.assertTrue(all(r.source_url == '/intake#objective-' + r.item_id.split(':')[1] for r in view.refusal_items))
            from web.views.tests.test_act_door import an_app
            client = an_app(port).test_client()
            data = client.get('/attention/').data
            self.assertEqual(data.count(b'data-refused-item='), 58)
            for refusal in view.refusal_items:
                response = client.get('/attention/preparation/' + refusal.item_id)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(b'data-preparation-source' in response.data)
                self.assertFalse(b'<form' in response.data)
            self.assertEqual((view.admitted, view.refused, view.blocked, view.total_open), (35, 58, 6, 99))
            self.assertFalse(any(i.kind == 'objective' for i in view.items))
        finally:
            fixture.stack.close()


class EvidenceOrigin(unittest.TestCase):
    def test_actual_runtime_port_and_fixture_have_distinct_labels(self):
        fixture = native.Admission()
        fixture.setUp()
        try:
            from web.views.tests.test_act_door import an_app
            client = an_app(native.model.attention_port()).test_client()
            for path in ('/attention/',):
                data = client.get(path).data
                self.assertTrue(b'Runtime store evidence' in data)
                self.assertFalse(b'fixture evidence' in data.lower())
        finally:
            fixture.stack.close()
        scene = Scene()
        for path in ('/attention/', '/attention/work_item:0429'):
            self.assertTrue(b'Synthetic fixture evidence' in scene.app.test_client().get(path).data)


class ReasonNormalization(unittest.TestCase):
    def test_server_rejects_the_same_normalized_short_reasons(self):
        with patch.object(native.rooms, 'dispatch', return_value={}) as dispatch:
            for reason in ('a' + ' ' * 10 + 'b', 'a' + '\u0085' * 10 + 'b', '🙂' * 6):
                with self.assertRaises(native.actions.ActionRefused):
                    native.actions.unaccept_work('queue', item_id='0429', reason=reason, operator='test-human')
            self.assertFalse(dispatch.called)
            native.actions.unaccept_work('queue', item_id='0429', reason='  A clear   withdrawal reason  ', operator='test-human')
            self.assertEqual(dispatch.call_args.kwargs['reason'], 'A clear withdrawal reason')


class IntakeObservation(unittest.TestCase):
    def test_supplied_runtime_observation_is_dated_and_not_invented_for_fixtures(self):
        scene = Scene()
        client = scene.app.test_client()
        self.assertNotIn(b'data-intake-observation', client.get('/attention/').data)
        scene.intake_observation = native.model._StoreAttentionPort.intake_observation
        response = client.get('/attention/')
        self.assertIn(b'data-intake-observation', response.data)
        self.assertIn(b'2026-09-12T16:33:22Z', response.data)
        self.assertIn(b'Attention Design', response.data)
        self.assertIn(b'does not monitor recovery', response.data)
        self.assertIn(b'Drop-folder intake was still running', response.data)


class DoneDisclosure(unittest.TestCase):
    """P3-01: the note-replaces-result truth is on screen before Mark done, and after undo."""

    def _human_port(self):
        from types import SimpleNamespace
        from web.views import QueueView
        fixture = native.Admission()
        fixture.setUp()
        try:
            view = native.model.attention_port().queue(limit=0)
        finally:
            fixture.stack.close()
        human = next(i for i in view.items if i.kind == 'human')
        self.assertTrue(human.native_options, 'fixture human row offers no Mark done; no denominator')
        def queue(**kwargs):
            return QueueView(items=(human,), checked_at=native.NOW, window=0, total_open=1, admitted=1,
                             refused_of=1, refused=0, blocked=0, deferred=0, filtered_total=1, offset=0,
                             limit=0, total_by_tier={'decide': 0, 'judge': 0, 'shape': 1})
        return human, SimpleNamespace(queue=queue, item=lambda typed: human if typed == human.item_id else None)

    def test_row_and_inspector_state_the_replacement_before_the_field(self):
        from web.views.tests.test_act_door import an_app
        human, port = self._human_port()
        client = an_app(port).test_client()
        body = client.get('/attention/').get_data(as_text=True)
        form_at = body.index('name="action" value="mark_done"')
        disclosure_at = body.index('replaces whatever result it held before', form_at)
        inverse_at = body.index('the earlier result text is not restored', form_at)
        field_at = body.index('name="text"', form_at)
        self.assertLess(disclosure_at, field_at, 'the disclosure must precede the note field')
        self.assertLess(inverse_at, field_at)
        inspector = client.get('/attention/' + human.item_id).get_data(as_text=True)
        self.assertIn('Available actions', inspector)
        self.assertIn('replaces whatever result it held before', inspector)
        self.assertIn('the earlier result text is not restored', inspector)

    def test_a_plant_that_drops_the_sentence_is_detected(self):
        from web.views.tests.test_act_door import an_app
        from dataclasses import replace as dc_replace
        human, port = self._human_port()
        quiet = dc_replace(human.native_options[0], does='Record what you did and mark this task complete.',
                           inverse_note='Reopen returns this task to unfinished work.')
        planted = replace(human, native_options=(quiet,))
        port.item = lambda typed: planted if typed == planted.item_id else None
        inspector = an_app(port).test_client().get('/attention/' + planted.item_id).get_data(as_text=True)
        # The plant was reached: the quiet words render on the Available actions row, so the
        # absence below is the sentence's and not an empty option list's.
        self.assertIn('Available actions', inspector)
        self.assertIn('Record what you did and mark this task complete.', inspector)
        self.assertIn('Reopen returns this task to unfinished work.', inspector)
        self.assertNotIn('replaces whatever result it held before', inspector)

    def test_undo_receipt_carries_the_retained_note_reason(self):
        with patch.object(native.rooms, 'dispatch', return_value={'id': '0410', 'state': 'inbox'}):
            out = native.actions.undo_done('attention', item={'id': '0410', 'kind': 'human'}, operator='test-human')
        self.assertEqual(out['verb'], 'reopen')
        self.assertIsNone(out['undo'])
        self.assertIn('stays as its stored result text', out['no_undo_reason'])
        self.assertIn('no inverse of its own', out['no_undo_reason'])


class BoundedRecommendationTitle(unittest.TestCase):
    """F-01 / P3-03: the row title is a handle; the proposal is one press away and never cut."""

    def test_the_bound_prefers_a_short_first_sentence_then_whole_words(self):
        from web.views.queue import bounded_title, TITLE_BOUND, ELLIPSIS
        short = 'Approve the renewal at the quoted rate.'
        self.assertEqual(bounded_title(short), short)
        self.assertEqual(bounded_title('  Approve\n the   renewal. '), 'Approve the renewal.')
        sentence_first = 'Approve the renewal at the quoted rate. ' + ('Then ' * 60)
        self.assertEqual(bounded_title(sentence_first), 'Approve the renewal at the quoted rate.')
        words = ('Consolidate the renewal quotes for the three suppliers into one comparison and '
                 'schedule a fifteen minute call with the account owner to confirm which clauses changed ' * 3)
        bounded = bounded_title(words)
        self.assertTrue(bounded.endswith(ELLIPSIS))
        self.assertLessEqual(len(bounded), TITLE_BOUND)
        self.assertTrue(words.startswith(bounded[:-1]))
        self.assertFalse(bounded[:-1].endswith(' '))
        self.assertTrue(words[len(bounded) - 1] == ' ', 'the cut must fall on a word boundary')
        unbroken = 'x' * 500
        self.assertEqual(bounded_title(unbroken), 'x' * (TITLE_BOUND - 1) + ELLIPSIS)
        # An abbreviation is not a sentence: the floor sends "Dr." to the word cut.
        abbreviated = 'Dr. Smith should approve the renewal before the broker deadline passes on Monday morning'
        self.assertNotEqual(bounded_title(abbreviated), 'Dr.')
        self.assertTrue(bounded_title(abbreviated).startswith('Dr. Smith should approve'))
        for text in (short, sentence_first, words, unbroken, abbreviated):
            self.assertLessEqual(len(bounded_title(text)), TITLE_BOUND)
            self.assertEqual(bounded_title(bounded_title(text)), bounded_title(text))

    def _port(self, proposal, counter='The cost may exceed the benefit.'):
        from types import SimpleNamespace
        from web.views import QueueView
        from web.views.queue import bounded_title
        fixture = native.Admission()
        fixture.setUp()
        try:
            view = native.model.attention_port().queue(limit=0)
        finally:
            fixture.stack.close()
        rec = next(i for i in view.items if i.kind == 'recommendation')
        self.assertTrue(rec.native_options, 'fixture recommendation offers no decision; no denominator')
        item = replace(rec, title=bounded_title(proposal), proposal=proposal, counterargument=counter)
        def queue(**kwargs):
            return QueueView(items=(item,), checked_at=native.NOW, window=0, total_open=1, admitted=1,
                             refused_of=1, refused=0, blocked=0, deferred=0, filtered_total=1, offset=0,
                             limit=0, total_by_tier={'decide': 0, 'judge': 1, 'shape': 0})
        return item, SimpleNamespace(queue=queue, item=lambda typed: item if typed == item.item_id else None)

    def test_the_store_port_bounds_a_recommendation_title_and_keeps_the_proposal(self):
        from web.views.queue import TITLE_BOUND
        long_text = ('Proposal: ' + 'move the weekly review to Tuesday so the numbers land before the call, ' * 20).strip()
        fixture = native.Admission()
        fixture.setUp()
        try:
            for row in fixture.rows:
                if row['source_type'] == 'recommendation':
                    row['title'] = long_text
            view = native.model.attention_port().queue(limit=0)
        finally:
            fixture.stack.close()
        recs = [i for i in view.items if i.kind == 'recommendation']
        self.assertEqual(len(recs), 3)
        for rec in recs:
            self.assertLessEqual(len(rec.title), TITLE_BOUND)
            self.assertEqual(rec.proposal, long_text)
        self.assertTrue(all(i.proposal == '' for i in view.items if i.kind != 'recommendation'))

    def test_the_row_carries_the_bound_and_the_full_text_and_the_inspector_carries_both(self):
        from web.views.tests.test_act_door import an_app
        full = ('Proposal: ' + 'move the weekly review to Tuesday so the numbers land before the call, ' * 17).strip()
        self.assertGreaterEqual(len(full), 1100)
        item, port = self._port(full)
        client = an_app(port).test_client()
        body = client.get('/attention/').get_data(as_text=True)
        self.assertIn('<b>' + item.title + '</b>', body)
        self.assertIn('data-att-proposal', body)
        self.assertIn('<summary class="att-link">Full proposal</summary>', body)
        self.assertIn(full, body)
        self.assertIn('aria-label="Approve: ' + item.title + '"', body)
        inspector = client.get('/attention/' + item.item_id).get_data(as_text=True)
        self.assertIn('<h1 class="att-title">' + item.title + '</h1>', inspector)
        self.assertIn('Proposal, in full', inspector)
        self.assertIn('data-att-proposal-full', inspector)
        self.assertIn(full, inspector)

    def test_a_short_proposal_gets_no_disclosure_and_a_long_argument_is_named_in_the_label(self):
        from web.views.tests.test_act_door import an_app
        short = 'Approve the renewal at the quoted rate.'
        item, port = self._port(short)
        body = an_app(port).test_client().get('/attention/').get_data(as_text=True)
        self.assertIn('<b>' + short + '</b>', body)
        self.assertNotIn('data-att-proposal', body, 'a title that already is the proposal needs no disclosure')
        long_counter = ('The comparison may take longer than the call it is meant to shorten, and the account '
                        'owner has already said the clauses did not change; the cost may exceed the benefit.')
        item, port = self._port(short, counter=long_counter)
        body = an_app(port).test_client().get('/attention/').get_data(as_text=True)
        self.assertIn('<summary class="att-link">Full argument against</summary>', body)
        self.assertIn('Argument against, in full: ' + long_counter, body)
        full = ('Proposal: ' + 'move the weekly review to Tuesday so the numbers land before the call, ' * 17).strip()
        item, port = self._port(full, counter=long_counter)
        body = an_app(port).test_client().get('/attention/').get_data(as_text=True)
        self.assertIn('<summary class="att-link">Full proposal and argument against</summary>', body)

    def test_the_row_states_the_argument_and_the_no_inverse_sentence_once_before_the_first_control(self):
        from web.views.tests.test_act_door import an_app
        from web.views.queue import TITLE_BOUND
        long_counter = ('The comparison may take longer than the call it is meant to shorten, and the account '
                        'owner has already said the clauses did not change; the cost may exceed the benefit.')
        full = ('Proposal: ' + 'move the weekly review to Tuesday so the numbers land before the call, ' * 17).strip()
        item, port = self._port(full, counter=long_counter)
        body = an_app(port).test_client().get('/attention/').get_data(as_text=True)
        row_at = body.index('data-queue-row="' + item.item_id + '"')
        counter_at = body.index('data-att-counter', row_at)
        counter_text = body[counter_at:body.index('</p>', counter_at)]
        self.assertIn('Argument against: ', counter_text)
        self.assertLessEqual(len(item.counterargument_summary), TITLE_BOUND)
        self.assertIn(item.counterargument_summary, counter_text)
        self.assertNotIn(long_counter, counter_text)
        inverse_at = body.index('data-att-inverse-note', row_at)
        first_form_at = body.index('<form class="att-act"', row_at)
        self.assertLess(counter_at, inverse_at)
        self.assertLess(inverse_at, first_form_at, 'the no-inverse sentence must precede the first control')
        self.assertEqual(body.count('Recommendation decisions have no inverse verb.'), 1)
        self.assertEqual(body.count('data-att-inverse-note'), 1)
        # The human Mark done aside is untouched: does and inverse note together, before the field.
        fixture = native.Admission()
        fixture.setUp()
        try:
            view = native.model.attention_port().queue(limit=0)
        finally:
            fixture.stack.close()
        human = next(i for i in view.items if i.kind == 'human')
        port.item = lambda typed: human if typed == human.item_id else None
        from web.views import QueueView
        port.queue = lambda **kw: QueueView(items=(human,), checked_at=native.NOW, window=0, total_open=1,
                                            admitted=1, refused_of=1, refused=0, blocked=0, deferred=0,
                                            filtered_total=1, offset=0, limit=0,
                                            total_by_tier={'decide': 0, 'judge': 0, 'shape': 1})
        body = an_app(port).test_client().get('/attention/').get_data(as_text=True)
        aside_at = body.index('replaces whatever result it held before')
        self.assertLess(aside_at, body.index('the earlier result text is not restored'))
        self.assertLess(aside_at, body.index('name="text"', aside_at))
        self.assertNotIn('data-att-inverse-note', body)

    def test_a_plant_that_drops_the_proposal_is_detected(self):
        from web.views.tests.test_act_door import an_app
        full = ('Proposal: ' + 'move the weekly review to Tuesday so the numbers land before the call, ' * 17).strip()
        item, port = self._port(full)
        planted = replace(item, proposal='')
        from web.views import QueueView
        def queue(**kwargs):
            return QueueView(items=(planted,), checked_at=native.NOW, window=0, total_open=1, admitted=1,
                             refused_of=1, refused=0, blocked=0, deferred=0, filtered_total=1, offset=0,
                             limit=0, total_by_tier={'decide': 0, 'judge': 1, 'shape': 0})
        port.queue = queue
        port.item = lambda typed: planted if typed == planted.item_id else None
        client = an_app(port).test_client()
        body = client.get('/attention/').get_data(as_text=True)
        self.assertIn('<b>' + planted.title + '</b>', body)
        self.assertNotIn('data-att-proposal', body)
        inspector = client.get('/attention/' + planted.item_id).get_data(as_text=True)
        self.assertIn('Argument against', inspector)
        self.assertNotIn('Proposal, in full', inspector)


class VisibleGate(unittest.TestCase):
    """F-02 / P3-04: the rule is visible text, the gated control answers, and both sides count alike."""

    def _page(self):
        from types import SimpleNamespace
        from web.views import QueueView
        from web.views.tests.test_act_door import an_app
        fixture = native.Admission()
        fixture.setUp()
        try:
            view = native.model.attention_port().queue(limit=0)
        finally:
            fixture.stack.close()
        items = (next(i for i in view.items if i.kind == 'human'),
                 next(i for i in view.items if i.kind == 'recommendation'))
        self.assertTrue(all(i.native_options for i in items), 'fixture rows offer no controls; no denominator')
        def queue(**kwargs):
            return QueueView(items=items, checked_at=native.NOW, window=0, total_open=2, admitted=2,
                             refused_of=2, refused=0, blocked=0, deferred=0, filtered_total=2, offset=0,
                             limit=0, total_by_tier={'decide': 0, 'judge': 1, 'shape': 1})
        port = SimpleNamespace(queue=queue, item=lambda typed: next((i for i in items if i.item_id == typed), None))
        return an_app(port).test_client().get('/attention/').get_data(as_text=True)

    def test_every_gated_control_has_a_visible_rule_tied_to_its_field_and_no_bare_disabled(self):
        import re
        body = self._page()
        forms = re.findall(r'<form class="att-act".*?</form>', body, re.S)
        gated = [f for f in forms if 'data-att-min="12"' in f]
        self.assertEqual(len(gated), 2, 'one Mark done and one Reject form expected')
        for form in gated:
            hint = re.search(r'<p class="att-hint" id="(att-hint-[^"]+)" data-att-hint\s+data-att-hint-base="([^"]+)">([^<]+)</p>', form)
            self.assertIsNotNone(hint, form[:200])
            self.assertIn('needs at least 12 characters.', hint.group(2))
            self.assertEqual(hint.group(2), hint.group(3))
            self.assertIn('aria-describedby="' + hint.group(1) + '"', form)
            self.assertLess(form.index('data-att-hint'), form.index('name="text"'), 'the rule precedes the field')
            self.assertIn('aria-disabled="true" data-att-gated="1"', form)
            self.assertNotRegex(form, r'<button[^>]*\sdisabled[\s>]')
        ungated = [f for f in forms if 'data-att-min' not in f]
        for form in ungated:
            self.assertNotIn('aria-disabled', form)
        # The Answer control (minimum 1) and the amend undo the page builds carry the same shape.
        from pathlib import Path
        controls = (Path(native.ROOT) / 'web' / 'templates' / 'attention' / 'controls.html').read_text(encoding='utf-8')
        self.assertIn('data-att-hint-base="Answer needs at least 1 character."', controls)
        self.assertIn('aria-describedby="att-hint-answer-{{ bare }}"', controls)
        inbox = (Path(native.ROOT) / 'web' / 'templates' / 'attention' / 'inbox.html').read_text(encoding='utf-8')
        self.assertIn("'Amend needs at least 1 character.'", inbox)
        self.assertIn("t.setAttribute('aria-describedby', h.id)", inbox)
        self.assertNotIn('b.disabled = true', inbox)

    def test_the_clients_whitespace_class_is_pythons_isspace_set(self):
        import re
        from pathlib import Path
        source = (Path(native.ROOT) / 'web' / 'templates' / 'attention' / 'inbox.html').read_text(encoding='utf-8')
        ranges = re.search(r'var PY_WS_RANGES = \[(.*?)\];', source, re.S)
        self.assertIsNotNone(ranges, 'the template no longer declares the whitespace ranges')
        pairs = [(int(a), int(b)) for a, b in re.findall(r'\[(\d+), (\d+)\]', ranges.group(1))]
        self.assertGreaterEqual(len(pairs), 8)
        client = {chr(c) for a, b in pairs for c in range(a, b + 1)}
        python = {chr(c) for c in range(0x110000) if chr(c).isspace()}
        self.assertEqual(client, python)
        self.assertIn('Array.from((value || \'\').replace(PY_WS_EDGES, \'\')).length', source)
        # The template gates with aria-disabled and answers a gated press; the shared task-page
        # gate in console.js carries the same class for its words rule.
        self.assertIn("btn.setAttribute('aria-disabled', n < min ? 'true' : 'false')", source)
        self.assertIn('if (gated) { refuseGated(f, gated); return; }', source)
        console = (Path(native.ROOT) / 'web' / 'static' / 'console.js').read_text(encoding='utf-8')
        self.assertIn('\\u0009-\\u000d\\u001c-\\u0020\\u0085\\u00a0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000', console)

    def test_the_server_counts_code_points_after_strip_and_names_the_act(self):
        with patch.object(native.rooms, 'dispatch', return_value={}) as dispatch:
            for text in ('\U0001F642' * 6, ' ' * 5 + 'twelve char' + '', ' twelve chars '[:12]):
                with self.assertRaises(native.actions.ActionRefused) as refused:
                    native.actions.mark_my_task_done('attention', item={'id': '0001', 'kind': 'human'},
                                                     summary=text, operator='test-human')
                message = str(refused.exception)
                self.assertIn('marking your own task done', message)
                self.assertNotIn('reopen', message)
                self.assertIn('you sent %d' % len(text.strip()), message)
            self.assertFalse(dispatch.called)
            native.actions.mark_my_task_done('attention', item={'id': '0001', 'kind': 'human'},
                                             summary=' ' + '\U0001F642' * 6 + 'abcdef 　', operator='test-human')
            self.assertEqual(dispatch.call_args.kwargs['summary'], '\U0001F642' * 6 + 'abcdef')
            with self.assertRaises(native.actions.ActionRefused) as refused:
                native.actions.recommend('attention', item={'id': '1', 'kind': 'recommendation'}, accept=False,
                                         operator='test-human', reason='\U0001F642' * 6)
            self.assertIn('rejecting a recommendation', str(refused.exception))
            self.assertNotIn('reopen', str(refused.exception))


class SharedDoneDisclosure(unittest.TestCase):
    """RB-01: the shared card's Mark my task done says, before the act, what the note does."""

    MACRO_PAGE = ('{% import "macros.html" as m %}'
                  '{{ m.reason_box("queue", action, item, "x-" ~ item.id, label, "placeholder", "hint") }}')

    def _render(self, action, label='Mark my task done', loader=None):
        from pathlib import Path
        from flask import Flask, render_template_string
        from jinja2 import ChoiceLoader, DictLoader
        app = Flask(__name__, template_folder=str(Path(native.ROOT) / 'web' / 'templates'))
        app.jinja_env.globals['csrf_for'] = lambda room: 'synthetic-token'
        # The room renders with these on (web/app.py); the hermetic render must too.
        app.jinja_env.trim_blocks = True
        app.jinja_env.lstrip_blocks = True
        if loader is not None:
            app.jinja_loader = ChoiceLoader([loader, app.jinja_loader])
        with app.app_context():
            return render_template_string(self.MACRO_PAGE, action=action, label=label,
                                          item=dict(id='0001', kind='human', title='Renew the office insurance'))

    def test_mark_done_discloses_above_the_field_and_other_acts_do_not(self):
        from web.attention_options import DONE_DISCLOSURE, DONE_INVERSE_NOTE
        body = self._render('mark_done')
        self.assertEqual(body.count('data-done-disclosure'), 1)
        self.assertIn(DONE_DISCLOSURE + ' ' + DONE_INVERSE_NOTE, body)
        self.assertLess(body.index('data-done-disclosure'), body.index('<textarea'))
        self.assertIn('name="action" value="mark_done"', body)
        for action, label in (('send_back', 'Send back'), ('unaccept', 'Unaccept')):
            other = self._render(action, label)
            self.assertNotIn('data-done-disclosure', other, action)
            self.assertIn('name="action" value="' + action + '"', other, 'the plant-free control rendered')

    def test_the_words_mirror_the_attention_option_and_the_card_is_the_one_caller(self):
        from pathlib import Path
        from web.attention_options import DONE_DISCLOSURE, DONE_INVERSE_NOTE
        source = (Path(native.ROOT) / 'web' / 'templates' / 'macros.html').read_text(encoding='utf-8')
        self.assertIn('data-done-disclosure>' + DONE_DISCLOSURE + ' ' + DONE_INVERSE_NOTE + '</p>', source)
        self.assertEqual(source.count("reason_box(room, 'mark_done'"), 1)
        templates = Path(native.ROOT) / 'web' / 'templates'
        callers = [p.name for p in templates.rglob('*.html') if 'm.card(' in p.read_text(encoding='utf-8')]
        self.assertEqual(callers, ['queue.html'], 'the card, and so the shared Mark my task done, renders in the Queue room only')
        others = [p.name for p in templates.rglob('*.html')
                  if "'mark_done'" in p.read_text(encoding='utf-8') and p.name != 'macros.html']
        self.assertEqual(others, [], others)

    def test_a_plant_that_strips_the_disclosure_is_reached_and_detected(self):
        from pathlib import Path
        from jinja2 import DictLoader
        source = (Path(native.ROOT) / 'web' / 'templates' / 'macros.html').read_text(encoding='utf-8')
        lines = [line for line in source.split('\n') if 'data-done-disclosure' not in line]
        self.assertEqual(len(lines), len(source.split('\n')) - 1)
        body = self._render('mark_done', loader=DictLoader({'macros.html': '\n'.join(lines)}))
        self.assertIn('name="action" value="mark_done"', body)   # the planted macro rendered
        self.assertNotIn('data-done-disclosure', body)


class UnavailableRecovery(unittest.TestCase):
    def test_unknown_and_excluded_routes_preserve_availability_boundaries(self):
        scene = Scene()
        client = scene.app.test_client()
        for path in ('/attention/work_item:unknown', '/attention/preparation/objective:unknown'):
            response = client.get(path + '?filter=review&offset=7&limit=7&return=https://untrusted.invalid')
            self.assertEqual(response.status_code, 404)
            self.assertIn(b'data-attention-unavailable', response.data)
            self.assertIn(b'does not establish whether the source exists', response.data)
            self.assertIn(b'Return to Attention', response.data)
            self.assertNotIn(b'untrusted.invalid', response.data)
            self.assertNotIn(b'<form', response.data)
        response = client.get('/attention/objective:10')
        self.assertEqual(response.status_code, 404)
        self.assertIn(b'Read the preparation or handling reason', response.data)
        self.assertNotIn(scene.refusals[9].title.encode(), response.data)
        self.assertNotIn(b'data-completion-report', response.data)
        # A port with no refusal extension remains supported and reveals nothing extra.
        scene.refusal = None
        self.assertIn(b'does not establish whether the source exists', client.get('/attention/objective:10').data)


if __name__ == '__main__':
    unittest.main()
