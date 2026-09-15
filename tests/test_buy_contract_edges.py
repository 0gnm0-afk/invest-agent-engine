import unittest
from copy import deepcopy

from invest_agent.trading.buy_policy import buy_draft, buy_fill, trigger_buy
from invest_agent.trading.risk_reporting import render
from test_buy_budget import approve, plan, position, snapshot


class BuyContractEdgeTests(unittest.TestCase):
    def test_loss_add_cannot_be_disguised_as_new_entry_reentry_or_prior_fill(self):
        holding = position(current_market_price='95', average_cost='100')
        before = deepcopy(holding)
        for kind in ('INITIAL_ENTRY', 'REENTRY', 'PYRAMID_ADD'):
            with self.subTest(kind=kind):
                candidate = plan(plan_kind=kind, position_id=holding['position_id'],
                    relative_superiority_basis='new thesis', locked_at_first_fill='invented-prior-fill',
                    approved_by_user=True)
                self.assertIsNone(candidate['locked_at_first_fill'])
                self.assertFalse(candidate['approved_by_user'])
                result = approve(candidate, positions=[holding])
                expected = 'LOSS_POSITION_ADD_BLOCKED' if kind == 'PYRAMID_ADD' else 'EXISTING_POSITION_REQUIRES_PYRAMID'
                self.assertIn(expected, result['block_reasons'])
                self.assertEqual(result['status'], 'MANUAL_REQUIRED')
                self.assertEqual(result['tranches'][0]['actual_fills'], [])
                self.assertEqual(holding, before)

    def test_fill_comparison_preserves_each_partial_and_overfill_without_inventing_risk(self):
        ready = approve(plan())
        first, pos = buy_fill(ready, None, 'b1', {'execution_id': 'partial', 'actual_quantity': '2',
            'actual_price': '101', 'actual_at': '2026-09-11T06:00:00+00:00'})
        self.assertEqual(first['execution_deviation']['planned_nominal_risk_for_fill'], '20')
        self.assertEqual(first['execution_deviation']['actual_nominal_risk'], '22')
        self.assertEqual(first['execution_deviation']['nominal_risk_difference'], '2')
        second, pos = buy_fill(first, pos, 'b1', {'execution_id': 'excess', 'actual_quantity': '10',
            'actual_price': '102', 'actual_at': '2026-09-11T06:01:00+00:00'})
        self.assertEqual(second['execution_deviation']['planned_remaining_quantity'], '8')
        self.assertEqual(second['execution_deviation']['quantity_over_remaining'], '2')
        self.assertEqual(second['execution_deviation']['planned_nominal_risk_for_fill'], '80')
        self.assertEqual(second['execution_deviation']['actual_nominal_risk'], '120')
        self.assertEqual(second['execution_deviation']['nominal_risk_difference'], '40')
        self.assertEqual(len(second['execution_deviations']), 2)
        self.assertEqual(pos['current_quantity'], '12')
        report = '\n'.join(render({'buy_plan': [second]}))
        self.assertIn('| excess |', report)
        self.assertIn('| 80 | 120 | 40 |', report)
        below, _ = buy_fill(ready, None, 'b1', {'execution_id': 'below', 'actual_quantity': '1',
            'actual_price': '89', 'actual_at': '2026-09-11T06:02:00+00:00'})
        self.assertIsNone(below['execution_deviation']['actual_nominal_risk'])

    def test_all_open_sell_states_block_pyramid_and_closed_states_do_not(self):
        candidate = plan(plan_kind='PYRAMID_ADD', position_id='pTEST', relative_superiority_basis='reviewed')
        for state in ('READY', 'ACTIVE', 'PAUSED', 'NEEDS_REAPPROVAL', 'MANUAL_REQUIRED'):
            with self.subTest(state=state):
                result = approve(candidate, positions=[position()], sells=[{'position_id': 'pTEST', 'status': state}])
                self.assertIn('ACTIVE_EXIT_PLAN', result['block_reasons'])
                self.assertEqual(result['status'], 'MANUAL_REQUIRED')
        for state in ('DRAFT', 'CANCELLED', 'COMPLETED', 'INACTIVE_RECOVERED'):
            with self.subTest(state=state):
                result = approve(candidate, positions=[position()], sells=[{'position_id': 'pTEST', 'status': state}])
                self.assertEqual(result['status'], 'READY')

    def two_tranches(self):
        value = plan()
        value['tranches'][0]['planned_quantity'] = '5'
        value['tranches'].append({**deepcopy(value['tranches'][0]), 'tranche_id': 'b2', 'sequence': 2})
        return buy_draft(value)

    def locked(self):
        return buy_fill(approve(self.two_tranches()), None, 'b1', {
            'execution_id': 'actual', 'actual_quantity': '1', 'actual_price': '100',
            'actual_at': '2026-09-11T06:00:00+00:00'})[0]

    def test_pre_fill_can_change_price_and_add_tranche(self):
        old = plan()
        changed = deepcopy(old)
        changed['tranches'][0].update(trigger_price='99', risk_entry_price='99')
        changed['tranches'].append({**deepcopy(changed['tranches'][0]), 'tranche_id': 'b2', 'sequence': 2})
        changed['total_planned_quantity'] = '20'
        result = buy_draft(changed, old)
        self.assertEqual(len(result['tranches']), 2)
        self.assertEqual(result['tranches'][0]['risk_entry_price'], '99')

    def test_first_fill_blocks_new_tranche_even_if_total_risk_unchanged(self):
        old = self.locked()
        changed = deepcopy(old)
        changed['tranches'][1]['planned_quantity'] = '4'
        changed['tranches'].append({**deepcopy(changed['tranches'][1]), 'tranche_id': 'b3', 'sequence': 3, 'planned_quantity': '1'})
        with self.assertRaisesRegex(ValueError, 'Locked plan cannot add/remove'):
            buy_draft(changed, old)

    def test_locked_cancelled_tranche_cannot_be_restored(self):
        old = self.locked()
        changed = deepcopy(old)
        changed['tranches'][1]['status'] = 'CANCELLED'
        cancelled = buy_draft(changed, old)
        revived = deepcopy(cancelled)
        revived['tranches'][1]['status'] = 'PLANNED'
        with self.assertRaisesRegex(ValueError, 'Cancelled tranche cannot be restored'):
            buy_draft(revived, cancelled)

    def test_breakout_above_ceiling_is_suspended_without_fill(self):
        value = plan()
        value['tranches'][0].update(trigger_type='BREAKOUT_AT_OR_ABOVE', trigger_price='99', max_entry_price='100')
        ready = approve(value)
        self.assertEqual(ready['status'], 'READY')
        result = trigger_buy(ready, 'b1', '101', ready)
        self.assertEqual(result['tranches'][0]['status'], 'SUSPENDED')
        self.assertEqual(result['tranches'][0]['actual_fills'], [])
        self.assertIn('EXECUTION_OUTSIDE_PLAN', result['block_reasons'])

    def test_sixth_symbol_requires_observed_zero_not_a_sell_plan(self):
        holdings = [position(str(i)) for i in range(5)]
        candidate = plan(symbol='NEW')
        selling = [{'position_id': holdings[0]['position_id'], 'status': 'ACTIVE'}]
        self.assertIn('PORTFOLIO_POSITION_LIMIT', approve(candidate, positions=holdings, sells=selling)['block_reasons'])
        holdings[0]['current_quantity'] = '0'
        self.assertEqual(approve(candidate, positions=holdings)['status'], 'READY')

    def test_planned_sales_do_not_finance_new_purchase(self):
        holdings = [position('OLD')]
        result = approve(plan(symbol='NEW'), positions=holdings, snap=snapshot(cash='0'),
                         sells=[{'position_id': 'pOLD', 'status': 'ACTIVE', 'expected_proceeds': '100000'}])
        self.assertIn('INSUFFICIENT_CASH', result['block_reasons'])


if __name__ == '__main__':
    unittest.main()
