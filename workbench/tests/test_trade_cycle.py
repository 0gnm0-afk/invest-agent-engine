"""Isolated arithmetic, persistence and explicit browser-fill safety checks."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from analysis_collaboration import Forbidden
from chart_workspace import Conflict
from trade_cycle import TradeCycleStore, calculate, combination_id, progress


def plan():
    def step(price, condition, comparator):
        return dict(confirmation_price=str(price), assumed_fill_price=str(price), condition=condition,
                    trigger=dict(kind='price', comparator=comparator, evaluation='daily_close'))
    return dict(title='합성 3분할 비교', currency='USD', budget='1000', account_alias='synthetic',
                initial_holding=dict(quantity=0, average_price='0'),
                buy_steps=[step(10, '합성 진입 1', 'gte'), step(8, '합성 진입 2', 'lte'), step(5, '합성 진입 3', 'lte')],
                sell_steps=[step(12, '합성 축소 1', 'gte'), step(15, '합성 축소 2', 'gte'), step(20, '합성 축소 3', 'gte')],
                stop=step(4, '가정 최종 무효화', 'lte'),
                scenarios=dict(
                    bullish=dict(events=[dict(kind='buy', step=1), dict(kind='buy', step=2), dict(kind='buy', step=3),
                                          dict(kind='sell', step=1), dict(kind='sell', step=2), dict(kind='sell', step=3)]),
                    neutral=dict(events=[dict(kind='buy', step=1), dict(kind='sell', step=1), dict(kind='buy', step=2),
                                         dict(kind='mark', price='11')]),
                    bearish=dict(events=[dict(kind='buy', step=1), dict(kind='buy', step=2), dict(kind='stop')])) )


COMBO = combination_id((20, 30, 50), (20, 30, 50))


def fill(kind, quantity, price, step=None, at='2026-10-13T01:00:00+00:00'):
    result = dict(kind=kind, quantity=quantity, price=str(price), at=at)
    if step is not None:
        result['step'] = step
    return result


class ArithmeticTests(unittest.TestCase):
    def test_108_paths_real_cash_basis_and_last_integer_remainder(self):
        result = calculate(plan())
        self.assertEqual((len(result['combinations']), result['evaluated_result_count']), (36, 108))
        item = next(row for row in result['combinations'] if row['id'] == COMBO)
        up = item['outcomes']['bullish']
        # Initial budget, never a percentage of the diminishing cash.
        self.assertEqual([row['quantity'] for row in up['ledger'][:3]], [20, 37, 100])
        # First-sale holding basis =157; final tranche uses the 79-share remainder.
        self.assertEqual([row['quantity'] for row in up['ledger'][3:]], [31, 47, 79])
        self.assertEqual(up['sell_basis'], 157)
        self.assertEqual(Decimal(up['cash']), Decimal(2661))
        self.assertEqual(Decimal(up['total_pnl']), Decimal(1661))
        self.assertEqual(up['quantity'], 0)
        self.assertEqual(up['phase'], 'closed')
        down = item['outcomes']['bearish']
        self.assertEqual(down['ledger'][-1]['quantity'], 57)
        self.assertEqual(Decimal(down['cash']), Decimal(732))

    def test_sell_cancels_pending_buys_and_keeps_actual_position(self):
        result = calculate(plan())
        row = next(row for row in result['combinations'] if row['id'] == COMBO)['outcomes']['neutral']
        self.assertEqual(row['cancelled_buy_steps'], [2, 3])
        self.assertEqual(row['quantity'], 16)
        self.assertEqual(row['ledger'][2]['reason'], 'buy_cancelled_after_selling_started')
        self.assertEqual(Decimal(row['realized_pnl']), Decimal(8))
        self.assertEqual(Decimal(row['unrealized_pnl']), Decimal(16))
        self.assertEqual(Decimal(row['cash']), Decimal(848))

    def test_confirmation_and_execution_prices_do_not_blend(self):
        value = plan()
        value['buy_steps'][0]['confirmation_price'] = '11'
        value['scenarios']['neutral']['events'][0]['price'] = '12.50'
        item = next(row for row in calculate(value)['combinations'] if row['id'] == COMBO)
        event = item['outcomes']['neutral']['ledger'][0]
        self.assertEqual((event['quantity'], event['price'], event['confirmation_price']), (16, '12.50', '11'))

    def test_preexisting_holdings_partial_sell_freezes_quantity_and_uses_cost(self):
        value = plan()
        value['initial_holding'] = dict(quantity=11, average_price='7')
        actual = [fill('buy', 20, 10, 1), fill('sell', 6, 12, 1)]
        p = progress(value, COMBO, actual)
        self.assertEqual(p['state']['sell_basis'], 31)
        self.assertEqual(p['state']['quantity'], 25)
        self.assertEqual(p['state']['cancelled_buy_steps'], [2, 3])
        self.assertEqual([step['kind'] for step in p['next_steps']], ['sell', 'stop'])
        self.assertEqual(p['next_steps'][0]['planned_quantity'], 9)

    def test_deterministic_pareto_and_duplicate_grouping_preserve_all_rows(self):
        first, second = calculate(plan()), calculate(plan())
        self.assertEqual(first, second)
        self.assertEqual(set(first['representatives']), {'upside', 'defensive', 'balanced'})
        self.assertTrue(all(ident in first['pareto_ids'] for ident in first['representatives'].values()))
        value = plan()
        for scenario in value['scenarios'].values():
            scenario['events'] = [dict(kind='mark', price='10')]
        flat = calculate(value)
        self.assertEqual(flat['unique_count'], 1)
        self.assertEqual(len(flat['combinations']), 36)
        self.assertEqual(sum(row['duplicate_of'] is not None for row in flat['combinations']), 35)

    def test_incomplete_draft_stays_visible_without_inventing_prices(self):
        value = plan()
        value['budget'] = None
        value['buy_steps'][0]['assumed_fill_price'] = None
        value['scenarios']['bearish']['events'] = []
        result = calculate(value)
        self.assertIn('budget', result['needs_input'])
        self.assertIn('buy_steps.1.assumed_fill_price', result['needs_input'])
        self.assertIn('scenarios.bearish.events', result['needs_input'])
        self.assertEqual(result['evaluated_result_count'], 0)
        self.assertTrue(all(row['outcomes'] is None for row in result['combinations']))

    def test_path_and_nonfinite_input_validation(self):
        value = plan()
        value['budget'] = 'NaN'
        with self.assertRaises(ValueError):
            calculate(value)

    def test_preview_keeps_actual_fills_and_identifies_only_completed_ratio_locks(self):
        fills = [fill('buy', 20, 10, 1)]
        result = calculate(plan(), fills, COMBO)
        self.assertEqual(sum(row['adoptable'] for row in result['combinations']), 12)
        rejected = next(row for row in result['combinations'] if row['buy_ratios'] == [30, 20, 50])
        self.assertEqual(rejected['adoption_blockers'], ['completed_buy_step_1_ratio_locked'])
        self.assertEqual(rejected['outcomes']['bullish']['ledger'][0]['quantity'], 20)
        self.assertTrue(all(next(row for row in result['combinations'] if row['id'] == ident)['adoptable']
                            for ident in result['representatives'].values()))
        self.assertTrue(all(row['adoptable'] is None for row in calculate(plan(), fills)['combinations']))
        value = plan()
        value['scenarios']['bullish']['events'][0]['at'] = '2026-10-14'
        value['scenarios']['bullish']['events'][1]['at'] = '2026-10-13'
        with self.assertRaisesRegex(ValueError, 'chronological'):
            calculate(value)
        value = plan()
        value['scenarios']['bullish']['events'][0]['step'] = 2
        with self.assertRaises(ValueError):
            calculate(value)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'analysis.sqlite3'
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('CREATE TABLE existing_analysis (payload TEXT)')
            db.execute('INSERT INTO existing_analysis VALUES (?)', ('unrelated original bytes',))
        self.store = TradeCycleStore(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def draft(self, value=None):
        return self.store.save_draft('US:SYNTH', value or plan(), source=dict(snapshot_id='synthetic-snapshot'))

    def adopt(self, cycle):
        return self.store.adopt(cycle['cycle_id'], cycle['draft_version'], COMBO, cycle['revision'], principal='browser')

    def record(self, cycle, value):
        return self.store.record_fill(cycle['cycle_id'], value, cycle['revision'], principal='browser')

    def test_migration_drafts_and_original_adoption_are_preserved(self):
        draft = self.draft()
        first = self.adopt(draft)
        changed = plan()
        changed['sell_steps'][0]['assumed_fill_price'] = '13'
        newer = self.store.save_draft('US:SYNTH', changed, first['cycle_id'], first['revision'])
        self.assertEqual(newer['adopted']['version'], 1)
        adopted = self.adopt(newer)
        self.assertEqual(adopted['original_plan']['version'], 1)
        self.assertEqual(adopted['adopted']['version'], 2)
        self.assertEqual(adopted['drafts'][0], draft['draft'])
        self.assertEqual(self.store.list('US:SYNTH')['cycles'][0]['cycle_id'], first['cycle_id'])
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT payload FROM existing_analysis').fetchone()[0], 'unrelated original bytes')

    def test_browser_exact_revision_and_explicit_fills_only(self):
        draft = self.draft()
        with self.assertRaises(Forbidden):
            self.store.adopt(draft['cycle_id'], 1, COMBO, 1, principal='llm')
        with self.assertRaises(Conflict):
            self.store.adopt(draft['cycle_id'], 1, COMBO, 0, principal='browser')
        active = self.adopt(draft)
        self.assertEqual(active['fills'], [])
        self.assertEqual(active['state']['quantity'], 0)
        with self.assertRaises(Forbidden):
            self.store.record_fill(active['cycle_id'], fill('buy', 20, 10, 1), active['revision'], principal='llm')
        actual = self.record(active, fill('buy', 20, 10, 1))
        self.assertEqual(actual['state']['quantity'], 20)
        self.assertEqual(actual['state']['cash'], '800')
        self.assertEqual(len(actual['fills']), 1)
        self.assertEqual(actual['original_outcomes'], active['original_outcomes'])

    def test_cash_quantity_phase_and_final_exit_failures_are_atomic(self):
        active = self.adopt(self.draft())
        for invalid in [fill('buy', 101, 10, 1), fill('buy', 2, 10, 2), fill('sell', 1, 12, 1)]:
            with self.assertRaises(ValueError):
                self.record(active, invalid)
            self.assertEqual(self.store.read(active['cycle_id'])['revision'], active['revision'])
        active = self.record(active, fill('buy', 20, 10, 1))
        active = self.record(active, fill('sell', 4, 12, 1))
        with self.assertRaisesRegex(ValueError, 'cancelled'):
            self.record(active, fill('buy', 20, 10, 2))
        with self.assertRaisesRegex(ValueError, 'insufficient_quantity'):
            self.record(active, fill('sell', 17, 12, 2))
        with self.assertRaisesRegex(ValueError, 'all_remaining'):
            self.record(active, fill('stop', 15, 4))
        closed = self.record(active, fill('stop', 16, 4))
        self.assertEqual(closed['state']['phase'], 'closed')
        with self.assertRaisesRegex(ValueError, 'closed'):
            self.record(closed, fill('buy', 1, 10, 2))

    def test_pending_draft_can_save_but_cannot_be_adopted(self):
        value = plan()
        value['stop'] = None
        value['budget'] = None
        saved = self.draft(value)
        self.assertIsNone(saved['draft']['plan']['budget'])
        with self.assertRaisesRegex(ValueError, 'needs_input'):
            self.adopt(saved)

    def test_same_stock_account_reentry_requires_previous_cycle_close(self):
        first = self.adopt(self.draft())
        second = self.draft()
        with self.assertRaisesRegex(Conflict, 'active_stock_account'):
            self.adopt(second)
        first = self.record(first, fill('buy', 20, 10, 1))
        first = self.record(first, fill('stop', 20, 4))
        adopted = self.adopt(second)
        self.assertNotEqual(adopted['cycle_id'], first['cycle_id'])

    def test_concurrent_revision_accepts_one_fill_and_preserves_other(self):
        active = self.adopt(self.draft())
        def attempt(_):
            try:
                self.record(active, fill('buy', 20, 10, 1))
                return 'saved'
            except Conflict:
                return 'conflict'
        with ThreadPoolExecutor(max_workers=2) as executor:
            result = list(executor.map(attempt, range(2)))
        self.assertCountEqual(result, ['saved', 'conflict'])
        self.assertEqual(len(self.store.read(active['cycle_id'])['fills']), 1)

    def test_pending_ratios_can_change_after_fills_without_rewriting_accounting(self):
        active = self.adopt(self.draft())
        active = self.record(active, fill('buy', 20, 10, 1))
        original_state, original_outcomes = deepcopy(active['state']), deepcopy(active['original_outcomes'])
        revised = self.store.save_draft('US:SYNTH', plan(), active['cycle_id'], active['revision'])
        changed = combination_id((20, 50, 30), (50, 20, 30))
        adopted = self.store.adopt(revised['cycle_id'], revised['draft_version'], changed, revised['revision'], principal='browser')
        self.assertEqual(adopted['state'], original_state)
        self.assertEqual(adopted['fills'], active['fills'])
        self.assertEqual(adopted['original_outcomes'], original_outcomes)
        self.assertEqual(adopted['next_steps'][0]['planned_quantity'], 62)
        invalid = combination_id((30, 20, 50), (50, 20, 30))
        with self.assertRaisesRegex(ValueError, 'completed_ratios_immutable'):
            self.store.adopt(adopted['cycle_id'], adopted['draft_version'], invalid, adopted['revision'], principal='browser')
        sold = self.record(adopted, fill('sell', 10, 12, 1))
        swapped = combination_id((20, 50, 30), (50, 30, 20))
        updated = self.store.adopt(sold['cycle_id'], sold['draft_version'], swapped, sold['revision'], principal='browser')
        self.assertEqual(updated['state'], sold['state'])
        self.assertEqual(updated['next_steps'][0]['planned_quantity'], 6)

    def test_corruption_is_detected_and_original_starting_capital_cannot_change(self):
        active = self.adopt(self.draft())
        changed = plan()
        changed['budget'] = '2000'
        newer = self.store.save_draft('US:SYNTH', changed, active['cycle_id'], active['revision'])
        with self.assertRaisesRegex(ValueError, 'allocation_immutable'):
            self.adopt(newer)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE trade_cycle_drafts SET payload=replace(payload,'1000','9999') WHERE version=1")
        with self.assertRaisesRegex(ValueError, 'integrity_failed'):
            self.store.read(active['cycle_id'])

    def test_revised_remaining_path_saves_and_adopts_over_actual_fills(self):
        active = self.adopt(self.draft())
        active = self.record(active, fill('buy', 20, 10, 1))
        revised = plan()
        for scenario in revised['scenarios'].values():
            scenario['events'] = [event for event in scenario['events']
                                  if not (event['kind'] == 'buy' and event['step'] == 1)]
        with self.assertRaisesRegex(ValueError, 'step_out_of_order'):
            calculate(revised)
        draft = self.store.save_draft('US:SYNTH', revised, active['cycle_id'], active['revision'])
        self.assertEqual(draft['adopted']['version'], 1)
        projected = calculate(draft['draft']['plan'], draft['fills'], draft['adopted']['combination_id'])
        self.assertEqual(projected['evaluated_result_count'], 108)
        adopted = self.adopt(draft)
        self.assertEqual(adopted['adopted']['version'], 2)
        self.assertEqual(adopted['fills'], active['fills'])
        self.assertEqual(adopted['state'], active['state'])
        self.assertEqual(adopted['original_plan'], active['original_plan'])
        self.assertEqual(adopted['original_outcomes'], active['original_outcomes'])
        sold = self.record(adopted, fill('sell', 4, 12, 1))
        remaining = deepcopy(revised)
        remaining['scenarios'] = dict(
            bullish=dict(events=[dict(kind='sell', step=2), dict(kind='sell', step=3)]),
            neutral=dict(events=[dict(kind='mark', price='11')]),
            bearish=dict(events=[dict(kind='stop')]))
        updated = self.store.save_draft('US:SYNTH', remaining, sold['cycle_id'], sold['revision'])
        updated = self.adopt(updated)
        self.assertEqual(updated['state'], sold['state'])
        self.assertEqual(updated['original_plan'], active['original_plan'])
        self.assertEqual(updated['current_outcomes']['bullish']['quantity'], 0)


if __name__ == '__main__':
    unittest.main()
