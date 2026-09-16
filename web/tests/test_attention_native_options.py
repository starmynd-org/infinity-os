"""Hermetic admission and write-door regression checks; no store connection permitted."""
import contextlib
import copy
from datetime import datetime, timezone
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
for directory in (ROOT, ROOT / 'queue', ROOT / 'engine'):
    sys.path.insert(0, str(directory))
test_env = dict(BRAIN_PG_DB='ios_attention_native_unit', BRAIN_PG_HOST='127.0.0.1',
                BRAIN_PG_PORT='1', BRAIN_SECRET_DIR=str(ROOT / '.no-test-secrets'),
                BRAIN_AFTER_COMMIT_HOOKS='0', ENGINE_AUTO_ACCEPT='0')
prior_env = {key: os.environ.get(key) for key in test_env}
os.environ.update(test_env)
import psycopg2
def forbidden_connection(*args, **kwargs):
    raise AssertionError('unit test attempted a database connection')
prior_connect = psycopg2.connect
psycopg2.connect = forbidden_connection
import store
from web import actions, model, rooms
from web.attention_options import for_row
from web.attention_native_door import perform
from human_queue import reads as Q
psycopg2.connect = prior_connect
for key, value in prior_env.items():
    if value is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = value

NOW = datetime(2026, 9, 12, tzinfo=timezone.utc)

def scene():
    rows, facts, behind = [], {}, {}
    for source, count, verb in [('work_item', 32, 'Mark my task done'),
                                ('recommendation', 3, 'Approve'), ('objective', 58, 'Accept objective')]:
        for index in range(count):
            sid = str(index + 1).zfill(4) if source == 'work_item' else str(index + 1)
            closed = source == 'work_item' and index >= 24
            row = dict(source_type=source, source_id=sid, work_item_id=sid if source == 'work_item' else None,
                       primary_verb='Accept work' if closed else verb, title=f'{source} fixture {index}',
                       tier='judge' if source == 'recommendation' else 'shape', created=NOW)
            rows.append(row)
            facts[(source, sid)] = dict(source_type=source, source_id=sid,
                decidable=closed, has_completed_result=closed, has_option=False, objective_exists=source == 'objective',
                work_state='done' if closed else 'inbox', recommendation_state='open',
                recommendation_text='A real proposal' if source == 'recommendation' else '',
                recommendation_counterargument='The cost may exceed the benefit.')
            if source == 'work_item':
                behind[sid] = dict(id=sid, state='done' if closed else 'inbox',
                                   actor_type=None, agent_claimable=False, claimed_by=None)
    return rows, facts, behind


class Admission(unittest.TestCase):
    def setUp(self):
        self.rows, self.facts, self.behind = scene()
        ranked = dict(now=NOW, totals=dict(open=93, blocked=6, deferred=0),
                      tiers={tier: {'items': [r for r in self.rows if r['tier'] == tier]}
                             for tier in ('decide', 'judge', 'shape')})
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(psycopg2, 'connect', forbidden_connection))
        self.stack.enter_context(patch.object(Q, 'queue', return_value=ranked))
        self.stack.enter_context(patch.object(rooms, '_row_behind', side_effect=lambda sid: self.behind.get(sid)))
        self.stack.enter_context(patch.object(rooms, 'agent_work_on', return_value={}))
        @contextlib.contextmanager
        def read(*args, **kwargs):
            yield SimpleNamespace(query=lambda sql, args: list(self.facts.values()))
        self.stack.enter_context(patch.object(store, 'read', read))
        self.stack.enter_context(patch.object(store, 'apply', side_effect=AssertionError('read wrote state')))

    def test_full_population_and_typed_options(self):
        view = model.attention_port().queue(limit=0)
        self.assertEqual((view.total_open, view.admitted, view.refused, view.blocked), (99, 35, 58, 6))
        self.assertEqual(sum(len(i.native_options) == 1 for i in view.items), 24)
        self.assertEqual(sum(len(i.native_options) == 2 for i in view.items), 3)
        self.assertEqual(sum(not i.native_options for i in view.items), 8)
        self.assertFalse(any(i.item_id.startswith('objective:') for i in view.items))

    def test_named_actions_survive_paging(self):
        first = model.attention_port().queue(limit=10)
        last = model.attention_port().queue(offset=30, limit=10)
        self.assertEqual((first.admitted, first.shown, first.hidden), (35, 10, 25))
        self.assertEqual((last.admitted, last.shown, last.hidden), (35, 5, 0))

    def test_missing_adapter_plant_is_detected(self):
        with patch('web.attention_options.for_row', return_value=()):
            self.assertEqual(model.attention_port().queue(limit=0).admitted, 8)
            with self.assertRaises(AssertionError):
                self.assertEqual(model.attention_port().queue(limit=0).admitted, 35)

    def test_same_guard_refuses_fleet_holder_and_prior_worker(self):
        row = self.rows[0]
        for change in ({'agent_claimable': True}, {'claimed_by': 'agent-holder'}):
            saved = dict(self.behind['0001'])
            self.behind['0001'].update(change)
            self.assertEqual(for_row(row, self.facts[('work_item', '0001')], operator='operator'), ())
            self.behind['0001'] = saved
        with patch.object(rooms, 'agent_work_on', return_value={'0001': {'worked': True, 'why': 'prior agent run'}}):
            self.assertEqual(for_row(row, self.facts[('work_item', '0001')], operator='operator'), ())

    def test_missing_row_and_read_failure_do_not_admit(self):
        self.behind.pop('0001')
        self.assertEqual(model.attention_port().queue(limit=0).admitted, 34)
        with patch.object(rooms, '_row_behind', side_effect=RuntimeError('store unavailable')):
            with self.assertRaises(RuntimeError):
                model.attention_port().queue(limit=0)

    def test_label_alone_and_empty_proposal_cannot_admit(self):
        facts = self.facts[('recommendation', '1')]
        facts['recommendation_text'] = '  '
        self.assertEqual(model.attention_port().queue(limit=0).admitted, 34)
        facts['recommendation_text'] = 'proposal'
        facts['recommendation_state'] = 'rejected'
        self.assertEqual(for_row(next(r for r in self.rows if r['source_type'] == 'recommendation'), facts,
                                 operator='operator'), ())


class Door(unittest.TestCase):
    def setUp(self):
        self.rows, self.facts, self.behind = scene()
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(psycopg2, 'connect', forbidden_connection))
        self.stack.enter_context(patch.object(rooms, '_row_behind', side_effect=lambda sid: self.behind.get(sid)))
        self.stack.enter_context(patch.object(rooms, 'agent_work_on', return_value={}))
        self.stack.enter_context(patch.object(store, 'registered', return_value={'done': {}, 'recommend accept': {}, 'recommend reject': {}}))
        self.writes = self.stack.enter_context(patch.object(store, 'apply', return_value={'id': '1'}))
        def lookup(typed):
            source, sid = typed.split(':', 1)
            row = next((r for r in self.rows if (r['source_type'], r['source_id']) == (source, sid)), None)
            if not row:
                return None
            return SimpleNamespace(readable=True, kind='human' if source == 'work_item' else source,
                native_options=for_row(row, self.facts[(source, sid)], operator='operator'))
        self.port = SimpleNamespace(item=lookup)

    def run_action(self, action, sid, source, text='A sufficiently detailed reason'):
        return perform(action, sid, text, source_type=source, port=self.port, operator='operator')

    def test_done_keeps_normal_guard_and_inverse(self):
        result = self.run_action('mark_done', '0001', 'work_item')
        self.assertEqual(self.writes.call_args.args, ('done',))
        self.assertEqual(result['undo']['action'], 'undo_done')
        self.assertEqual(self.writes.call_args.kwargs['agent'], 'operator')

    def test_proposal_decisions_use_human_login_and_rejection_reason(self):
        self.run_action('approve', '1', 'recommendation')
        self.assertEqual(self.writes.call_args.args, ('recommend accept',))
        self.assertTrue(self.writes.call_args.kwargs['as_operator'])
        result = self.run_action('reject', '2', 'recommendation')
        self.assertEqual(self.writes.call_args.args, ('recommend reject',))
        self.assertTrue(self.writes.call_args.kwargs['as_operator'])
        self.assertEqual(self.writes.call_args.kwargs['reason'], 'A sufficiently detailed reason')
        self.assertIsNone(result['undo'])
        self.assertEqual(result['subject_type'], 'recommendation')

    def test_wrong_arm_and_missing_type_refuse_before_lookup(self):
        for source in ('objective', 'work_item', None, ''):
            with self.assertRaises(actions.ActionRefused):
                self.run_action('approve', '1', source)
        self.writes.assert_not_called()

    def test_existing_queue_reject_without_text_keeps_its_guarded_contract(self):
        result = actions.recommend('queue', item={'id': '1', 'kind': 'recommendation'},
                                   accept=False, operator='operator')
        self.assertEqual(self.writes.call_args.args, ('recommend reject',))
        self.assertTrue(self.writes.call_args.kwargs['as_operator'])
        self.assertNotIn('reason', self.writes.call_args.kwargs)
        self.assertEqual(result['subject_type'], 'recommendation')

    def test_attention_reject_without_text_refuses_before_any_write(self):
        with self.assertRaises(actions.ActionRefused):
            self.run_action('reject', '1', 'recommendation', '')
        self.writes.assert_not_called()

    def test_stale_unreadable_missing_action_and_short_reason_refuse(self):
        for action, sid, source, text in [('approve', '999', 'recommendation', ''),
                                         ('reject', '1', 'recommendation', 'short'),
                                         ('mark_done', '0001', 'work_item', '')]:
            with self.assertRaises(actions.ActionRefused):
                self.run_action(action, sid, source, text)
        self.facts[('recommendation', '1')]['recommendation_state'] = 'accepted'
        with self.assertRaises(actions.ActionRefused):
            self.run_action('approve', '1', 'recommendation')
        self.port.item = lambda typed: SimpleNamespace(readable=False)
        with self.assertRaises(actions.ActionRefused):
            self.run_action('approve', '2', 'recommendation')
        self.writes.assert_not_called()

    def test_changed_guard_between_render_and_dispatch_refuses(self):
        original = self.port.item
        def racing_read(typed):
            row = original(typed)
            self.behind['0001']['claimed_by'] = 'new-holder'
            return row
        self.port.item = racing_read
        with self.assertRaises(rooms.ItemRefusal):
            self.run_action('mark_done', '0001', 'work_item')
        self.writes.assert_not_called()

    def test_done_option_discloses_result_replacement_and_undo_says_the_note_stays(self):
        # P3-01. The words are read off the option the door dispatches, not off a template.
        from web.attention_options import DONE_DISCLOSURE, DONE_INVERSE_NOTE
        option = self.port.item('work_item:0001').native_options[0]
        self.assertEqual(option.action, 'mark_done')
        self.assertIn(DONE_DISCLOSURE, option.does)
        self.assertIn('replaces whatever result it held before', option.does)
        self.assertEqual(option.inverse_note, DONE_INVERSE_NOTE)
        self.assertIn('not restored', option.inverse_note)
        result = actions.undo_done('attention', item={'id': '0001', 'kind': 'human'}, operator='operator')
        self.assertEqual(self.writes.call_args.args, ('reopen',))
        self.assertIsNone(result['undo'])
        self.assertEqual(result['no_undo_reason'], actions.REOPEN_KEEPS_NOTE)
        self.assertIn('stays as its stored result text', result['no_undo_reason'])

    def test_room_did_not_gain_arbitrary_verbs(self):
        self.assertEqual(rooms.ROOM_VERBS['attention'], frozenset({
            'answer', 'accept work', 'unaccept work', 'done', 'reopen', 'accept',
            'recommend accept', 'recommend reject'}))
        for verb in ('post', 'dismiss', 'queue draft options', 'note'):
            with self.assertRaises(rooms.RoomRefusal):
                rooms.assert_allowed('attention', verb)


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.testsRun:
        raise SystemExit('Native Attention checks: no cases ran')
    print(f'Native Attention checks: {result.testsRun - len(result.failures) - len(result.errors)}/{result.testsRun} passed; database connections permitted: 0')
    raise SystemExit(0 if result.wasSuccessful() else 1)
