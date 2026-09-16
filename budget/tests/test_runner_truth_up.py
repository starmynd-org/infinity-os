"""Hermetic contract for C5-COMP-1: the runner's end charge composes with the guard's steps.

The guard (`budget/enforcer.py`) charges a live session in MID_STEP increments under refs
`<sid>:mid:<n>`. The runner (`engine/bin/swarm-run`) then charges the session's authoritative total
under `<sid>:<attempt>` with `--source run_json`. Those are the same money. Review 5 measured the
runner omitting `--truth-up` natively: a guarded $0.50 session ledgered $1.00.

No database. `budget_charge` is the real registered transition, called with an in-memory ledger
that answers exactly the three statements it issues. The runner's flag is read out of the
`swarm-run` beside this code root, so pointing ROUTINES_TEST_CODE_ROOT at a tree without the flag
turns `test_the_runner_end_charge_trues_up` and the composition test red by name.
"""
from decimal import Decimal
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(os.environ.get('ROUTINES_TEST_CODE_ROOT', str(Path(__file__).resolve().parents[2])))
sys.path.insert(0, str(ROOT))
from budget import enforcer, transitions

RUNNER = ROOT / 'engine' / 'bin' / 'swarm-run'
SESSION = 'fixture-session'


class Ledger:
    """Answers the three statements budget_charge issues, and refuses any other."""

    def __init__(self):
        self.rows = []

    def one(self, sql, params):
        text = ' '.join(sql.split())
        if text.startswith('SELECT COALESCE(sum(usd), 0)'):
            session, source, ref = params
            total = sum((r['usd'] for r in self.rows if r['session_id'] == session
                         and not (r['source'] == source and r['source_ref'] == ref)), Decimal(0))
            return {'usd': total}
        if text.startswith('INSERT INTO brain.budget_charge'):
            usd, _agent, _lane, _task, _run, session, source, ref, note = params[:9]
            if any(r['source'] == source and r['source_ref'] == ref for r in self.rows):
                return None
            row = {'id': len(self.rows) + 1, 'usd': Decimal(str(usd)), 'session_id': session,
                   'source': source, 'source_ref': ref, 'note': note, 'charged_at': 'fixture'}
            self.rows.append(row)
            return {'id': row['id'], 'usd': row['usd'], 'charged_at': row['charged_at']}
        if text.startswith('SELECT id, usd, charged_at FROM brain.budget_charge'):
            source, ref = params
            row = next(r for r in self.rows if r['source'] == source and r['source_ref'] == ref)
            return {'id': row['id'], 'usd': row['usd'], 'charged_at': row['charged_at']}
        raise AssertionError('budget_charge issued a statement this ledger does not model: ' + text[:120])

    def total(self):
        return sum((r['usd'] for r in self.rows), Decimal(0))


def runner_end_charge():
    """The runner's run_json charge command, from its opening to its closing redirect."""
    text = RUNNER.read_text(encoding='utf-8')
    start = text.index('CHARGE_LINE="$("$BUDGET" charge')
    return text[start:text.index('2>&1)"', start)]


def guard_steps(ledger, derived_usd):
    """What the guard charges for a session that has derived `derived_usd` so far."""
    step = enforcer.RunGuard.MID_STEP
    for n in range(1, int(Decimal(str(derived_usd)) / step) + 1):
        transitions.budget_charge(ledger, usd=step, source_ref=f'{SESSION}:mid:{n}', source='stream',
                                  session_id=SESSION, note=f'mid-run step {n}')


def end_charge(ledger, authoritative_usd, truth_up):
    return transitions.budget_charge(ledger, usd=authoritative_usd, source_ref=f'{SESSION}:1',
                                     source='run_json', session_id=SESSION, truth_up=truth_up)


class RunnerTruthUp(unittest.TestCase):
    def test_the_runner_end_charge_trues_up(self):
        command = runner_end_charge()
        self.assertIn('--source run_json', command)
        self.assertIn('--session "$CS_SID"', command)
        self.assertIn('--truth-up', command, 'C5-COMP-1: the end charge doubles the guard steps')

    def test_the_flag_check_sees_a_removal(self):
        """Not a tautology: the same predicate is false once the flag is taken out."""
        command = runner_end_charge().replace(' --truth-up', '')
        self.assertNotIn('--truth-up', command)

    def test_a_guarded_session_ledgers_its_authoritative_total_through_the_runner(self):
        ledger = Ledger()
        guard_steps(ledger, '0.50')
        end_charge(ledger, '0.50', truth_up='--truth-up' in runner_end_charge())
        self.assertEqual(ledger.total(), Decimal('0.50'))
        final = [r for r in ledger.rows if r['source'] == 'run_json']
        self.assertEqual(len(final), 1)
        self.assertEqual(final[0]['usd'], Decimal('0'))
        self.assertIn('truth-up', final[0]['note'])

    def test_defect_witness_without_truth_up_doubles(self):
        ledger = Ledger()
        guard_steps(ledger, '0.50')
        end_charge(ledger, '0.50', truth_up=False)
        self.assertEqual(ledger.total(), Decimal('1.00'))

    def test_a_rerun_of_the_end_charge_is_a_noop(self):
        ledger = Ledger()
        guard_steps(ledger, '0.50')
        end_charge(ledger, '0.50', truth_up=True)
        again = end_charge(ledger, '0.50', truth_up=True)
        self.assertFalse(again['charged'])
        self.assertEqual(ledger.total(), Decimal('0.50'))

    def test_a_session_below_one_step_charges_its_full_total(self):
        ledger = Ledger()
        guard_steps(ledger, '0.125')
        end_charge(ledger, '0.125', truth_up=True)
        self.assertEqual([r['source'] for r in ledger.rows], ['run_json'])
        self.assertEqual(ledger.total(), Decimal('0.125'))

    def test_an_evidenced_zero_still_writes_its_row(self):
        ledger = Ledger()
        end_charge(ledger, '0', truth_up=True)
        self.assertEqual(len(ledger.rows), 1)
        self.assertEqual(ledger.total(), Decimal('0'))

    def test_over_derivation_clamps_and_is_reported(self):
        ledger = Ledger()
        guard_steps(ledger, '0.75')
        result = end_charge(ledger, '0.50', truth_up=True)
        self.assertEqual(result['usd'], Decimal('0'))
        self.assertEqual(result['over_charged_usd'], Decimal('0.25'))
        self.assertEqual(ledger.total(), Decimal('0.75'))


if __name__ == '__main__':
    unittest.main()
