"""Exercise the actual receipt functions without importing application startup."""
import ast
from contextlib import nullcontext
from pathlib import Path
from threading import Event, RLock, Thread
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from web.tests import test_attention_native_options as native


def receipt_functions():
    path = Path(__file__).resolve().parents[1] / 'app.py'
    parsed = ast.parse(path.read_text(encoding='utf-8'))
    selected = [node for node in parsed.body if isinstance(node, ast.FunctionDef)
                and node.name in ('_receipts', '_push_receipt', '_perform_with_receipt')]
    if len(selected) != 3:
        raise AssertionError('actual receipt functions not found')
    clock = SimpleNamespace(now=100.0)
    namespace = dict(time=SimpleNamespace(time=lambda: clock.now),
                     RECEIPT_SECONDS=7.0, _RECEIPTS=[], _RECEIPT_LOCK=RLock(),
                     _ACCEPTANCE_LOCK=RLock(), nullcontext=nullcontext)
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), 'exec'), namespace)
    return namespace, clock


def acceptance(item_id):
    with patch.object(native.rooms, 'dispatch', return_value={}):
        return native.actions.accept_work('queue', item={'id': item_id}, operator='fixture-human')


def withdrawal(item_id):
    with patch.object(native.rooms, 'dispatch', return_value={'was_accepted_by': 'fixture-human'}):
        return native.actions.unaccept_work('queue', item_id=item_id,
            reason='A considered synthetic withdrawal', operator='fixture-human')


class SharedReceipts(unittest.TestCase):
    def test_snapshot_does_not_change_during_later_withdrawal(self):
        state, _ = receipt_functions()
        state['_push_receipt'](acceptance('0429'))
        snapshot = state['_receipts']()
        state['_push_receipt'](withdrawal('0429'))
        self.assertEqual(snapshot[0]['undo']['id'], '0429')
        self.assertIsNone(state['_receipts']()[1]['undo'])

    def test_overlapping_withdrawal_then_acceptance_publish_in_transition_order(self):
        state, _ = receipt_functions()
        state['_push_receipt'](acceptance('0429'))
        completed, release, contender = Event(), Event(), Event()
        performed, errors = [], []
        outputs = {'unaccept': withdrawal('0429'), 'accept_work': acceptance('0429')}

        class ObservedLock:
            def __init__(self):
                self.lock = RLock()
            def __enter__(self):
                contender.set()
                self.lock.acquire()
            def __exit__(self, *exc):
                self.lock.release()

        state['_ACCEPTANCE_LOCK'] = ObservedLock()
        def perform(room, action, item_id, text):
            performed.append(action)
            if action == 'unaccept':
                completed.set()
                if not release.wait(3):
                    raise AssertionError('test did not release completed withdrawal')
            return outputs[action]
        state['_perform'] = perform
        def invoke(action):
            try:
                state['_perform_with_receipt']('queue', action, '0429', '')
            except Exception as exc:
                errors.append(type(exc).__name__)
        first = Thread(target=invoke, args=('unaccept',))
        second = Thread(target=invoke, args=('accept_work',))
        first.start()
        try:
            self.assertTrue(completed.wait(3))
            contender.clear()
            second.start()
            self.assertTrue(contender.wait(3))
            self.assertEqual(performed, ['unaccept'])
        finally:
            release.set()
            first.join(3)
            if second.ident is not None:
                second.join(3)
        self.assertFalse(first.is_alive() or second.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(performed, ['unaccept', 'accept_work'])
        rows = state['_receipts']()
        self.assertEqual(len(rows), 3)
        self.assertEqual([r['undo']['id'] for r in rows if r['undo']], ['0429'])
        self.assertTrue(rows[0]['undo'])

    def test_read_and_push_share_lock_without_lost_receipt(self):
        state, _ = receipt_functions()
        entered, release, reading = Event(), Event(), Event()
        class PausedList(list):
            def append(self, receipt):
                entered.set()
                if not release.wait(3):
                    raise AssertionError('test did not release append')
                super().append(receipt)
        state['_RECEIPTS'] = PausedList()
        class ObservedLock:
            def __init__(self):
                self.lock = RLock()
            def __enter__(self):
                reading.set()
                self.lock.acquire()
            def __exit__(self, *exc):
                self.lock.release()
        state['_RECEIPT_LOCK'] = ObservedLock()
        seen, errors = [], []
        out = acceptance('0429')
        def push():
            try:
                state['_push_receipt'](out)
            except Exception as exc:
                errors.append(type(exc).__name__)
        def read():
            try:
                seen.extend(state['_receipts']())
            except Exception as exc:
                errors.append(type(exc).__name__)
        writer, reader = Thread(target=push), Thread(target=read)
        writer.start()
        try:
            self.assertTrue(entered.wait(3))
            reading.clear()
            reader.start()
            self.assertTrue(reading.wait(3))
            self.assertEqual(seen, [])
        finally:
            release.set()
            writer.join(3)
            if reader.ident is not None:
                reader.join(3)
        self.assertFalse(writer.is_alive() or reader.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(seen), 1)
        self.assertEqual(len(state['_receipts']()), 1)

    def test_withdrawal_spends_only_this_acceptance_and_later_accept_is_unique(self):
        state, clock = receipt_functions()
        push, read = state['_push_receipt'], state['_receipts']
        push(acceptance('0429'))
        push(acceptance('0427'))
        push(dict(verb='answer', receipt='Other typed source with the same bare ID',
                  undo=dict(action='amend', qid='0429', label='Amend')))
        before = [(r['text'], r['at']) for r in state['_RECEIPTS']]
        push(withdrawal('0429'))
        self.assertEqual([(r['text'], r['at']) for r in state['_RECEIPTS'][:3]], before)
        self.assertIsNone(state['_RECEIPTS'][0]['undo'])
        self.assertIn('withdrawn', state['_RECEIPTS'][0]['no_undo_reason'].lower())
        self.assertEqual(state['_RECEIPTS'][1]['undo']['id'], '0427')
        self.assertEqual(state['_RECEIPTS'][2]['undo']['action'], 'amend')
        push(acceptance('0429'))
        current = [r for r in read() if r['undo'] and r['undo'].get('action') == 'unaccept'
                   and r['undo'].get('id') == '0429']
        self.assertEqual(len(current), 1)
        self.assertEqual(len(read()), 5)
        clock.now += 8
        self.assertEqual(read(), [])

    def test_newer_acceptance_retires_an_older_display_inverse(self):
        state, _ = receipt_functions()
        state['_push_receipt'](acceptance('0429'))
        state['_push_receipt'](acceptance('0429'))
        self.assertEqual(sum(bool(r['undo']) for r in state['_receipts']()), 1)

    def test_refusal_produces_no_receipt_to_consume_the_inverse(self):
        state, _ = receipt_functions()
        state['_push_receipt'](acceptance('0429'))
        with patch.object(native.rooms, 'dispatch', side_effect=native.actions.ActionRefused('Synthetic refusal')):
            with self.assertRaises(native.actions.ActionRefused):
                native.actions.unaccept_work('queue', item_id='0429',
                    reason='A meaningful retained refusal reason', operator='fixture-human')
        self.assertEqual(len(state['_receipts']()), 1)
        self.assertEqual(state['_receipts']()[0]['undo']['id'], '0429')


if __name__ == '__main__':
    unittest.main()
