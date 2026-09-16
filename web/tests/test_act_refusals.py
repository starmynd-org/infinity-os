"""Actual HTTP handler and inverse guard bodies, without app startup or a database."""
import ast
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
import traceback
import unittest
from unittest.mock import patch

from flask import Flask, jsonify, request
from web.tests import test_attention_native_options as native
from web.tests.test_shared_receipts import receipt_functions
from web import guard
from swarm_engine import transitions


class SyntheticContext:
    def __init__(self, row):
        self.row = row
        self.reads = 0
        self.writes = 0
    def one(self, sql, params):
        self.reads += 1
        return self.row
    def execute(self, *args, **kwargs):
        self.writes += 1
        raise AssertionError('refusal reached an update')
    def thread(self, *args, **kwargs):
        self.writes += 1
        raise AssertionError('refusal reached the audit writer')
    def scalar(self, *args, **kwargs):
        raise AssertionError('early inverse refusal reached the identity lookup')


def handler_app():
    path = Path(__file__).resolve().parents[1] / 'app.py'
    parsed = ast.parse(path.read_text(encoding='utf-8'))
    create = next(node for node in parsed.body if isinstance(node, ast.FunctionDef)
                  and node.name == 'create_app')
    act = next(node for node in create.body if isinstance(node, ast.FunctionDef)
               and node.name == 'act')
    act.decorator_list = []
    state, _ = receipt_functions()
    app = Flask(__name__)
    app.testing = True
    state.update(app=app, request=request, jsonify=jsonify, guard=guard,
                 rooms=native.rooms, actions=native.actions, store=native.store,
                 _engine_transitions=transitions, traceback=traceback)
    exec(compile(ast.Module(body=[act], type_ignores=[]), str(path), 'exec'), state)
    app.add_url_rule('/<room>/act', 'act', state['act'], methods=['POST'])
    return app, state


class ActRefusals(unittest.TestCase):
    def setUp(self):
        self.app, self.state = handler_app()
        self.client = self.app.test_client()
        self.guard = patch.object(guard, 'guard_write').start()
        self.room = patch.object(native.rooms, 'assert_room_can_act').start()
        self.logs = patch.object(self.app.logger, 'error').start()
        self.addCleanup(patch.stopall)
        self.state['_perform'] = self.unexpected_call

    def unexpected_call(self, *args):
        raise AssertionError('a pre-action refusal reached the action')

    def post(self, room='queue'):
        return self.client.post('/' + room + '/act', data={
            'action': 'unaccept', 'id': '0429', 'text': 'A considered synthetic withdrawal'})

    def assert_no_receipt(self):
        self.assertEqual(self.state['_receipts'](), [])

    def check_inverse(self, row, message):
        ctx = SyntheticContext(row)
        with self.assertRaises(transitions.VerbError) as expected:
            transitions.unaccept_work(SyntheticContext(row), id='0429',
                reason='A considered synthetic withdrawal', by='fixture-human')
        def perform(room, action, item_id, text):
            return transitions.unaccept_work(ctx, id=item_id, reason=text, by='fixture-human')
        self.state['_perform'] = perform
        response = self.post()
        body = response.get_json()
        self.assertEqual((response.status_code, body['kind'], body['ok']), (400, 'refused', False))
        self.assertIn(message, body['error'])
        self.assertEqual(body['error'], str(expected.exception))
        self.assertEqual((ctx.reads, ctx.writes), (1, 0))
        self.assert_no_receipt()
        self.logs.assert_not_called()

    def test_missing_task_is_a_zero_write_client_refusal(self):
        self.check_inverse(None, 'no such task: 0429')

    def test_already_withdrawn_is_a_zero_write_client_refusal(self):
        self.check_inverse(dict(state='done', accepted_at=None, accepted_by=None),
                           'is not accepted, so there is no acceptance to withdraw')

    def test_existing_action_refusal_mapping_is_unchanged(self):
        def perform(*args):
            raise native.actions.ActionRefused('A meaningful synthetic refusal')
        self.state['_perform'] = perform
        response = self.post()
        self.assertEqual((response.status_code, response.get_json()['kind']), (400, 'refused'))
        self.assertEqual(response.get_json()['error'], 'A meaningful synthetic refusal')
        self.assert_no_receipt()

    def test_guard_refuses_before_action(self):
        self.guard.side_effect = guard.WriteRefused('Synthetic request refused')
        response = self.post()
        self.assertEqual((response.status_code, response.get_json()['kind']), (403, 'write-refusal'))
        self.room.assert_not_called()
        self.assert_no_receipt()

    def test_room_refuses_before_action(self):
        self.room.side_effect = native.rooms.RoomRefusal('Synthetic room refused')
        response = self.post('study')
        self.assertEqual((response.status_code, response.get_json()['kind']), (403, 'room-refusal'))
        self.assert_no_receipt()

    def test_unknown_verb_stays_not_implemented(self):
        def perform(*args):
            raise native.store.UnknownTransition('synthetic unknown verb')
        self.state['_perform'] = perform
        response = self.post()
        self.assertEqual((response.status_code, response.get_json()['kind']), (501, 'unknown-verb'))
        self.assert_no_receipt()

    def test_unexpected_runtime_error_remains_server_fault(self):
        def perform(*args):
            raise RuntimeError('synthetic unexpected fault')
        self.state['_perform'] = perform
        response = self.post()
        self.assertEqual((response.status_code, response.get_json()['kind']), (500, 'error'))
        self.logs.assert_called_once()
        self.assert_no_receipt()

    def test_success_still_publishes_once_and_reads_cursor(self):
        self.state['_perform'] = lambda *args: dict(verb='unaccept work', receipt='Synthetic success',
            subject_type='work_item', subject_id='0429', no_undo_reason='Already withdrawn')
        @contextmanager
        def read():
            yield SimpleNamespace(scalar=lambda sql: 12)
        with patch.object(native.store, 'read', read):
            response = self.post()
        body = response.get_json()
        self.assertEqual((response.status_code, body['ok'], body['celebrate_from']), (200, True, 12))
        self.assertEqual(len(self.state['_receipts']()), 1)
        self.logs.assert_not_called()


if __name__ == '__main__':
    unittest.main()
