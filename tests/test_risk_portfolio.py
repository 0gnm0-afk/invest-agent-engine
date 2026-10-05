import copy
import json
import sys
import unittest
from pathlib import Path
from invest_agent.scenarios.portfolio_scenarios import compare
from invest_agent.scenarios.risk_conditions import evaluate


def fixture():
    return json.loads((Path(__file__).parent / 'fixtures/risk_portfolio.json').read_text(encoding='utf-8'))


class Scenarios(unittest.TestCase):
    def setUp(self):
        f = fixture()
        self.s, self.c = f['snapshot'], f['scenario']

    def run_case(self):
        return compare(self.s, self.c)

    def test_hand_calculation_and_immutability(self):
        original = copy.deepcopy((self.s, self.c))
        r = self.run_case()
        self.assertEqual(r['no_response']['baseline_total'], '1125000')
        self.assertEqual(r['no_response']['total'], '1013000')
        self.assertEqual(r['no_response']['change'], '-112000')
        self.assertEqual(r['response']['total'], '1068000.0')
        self.assertEqual(r['response']['change'], '-57000.0')
        self.assertEqual(r['response']['remaining_quantities']['demo/US'], '5.0')
        self.assertEqual(r['response']['cash_changes'][0]['after'], '550.0')
        self.assertEqual(original, (self.s, self.c))
        self.assertEqual(r, self.run_case())

    def test_costs_are_explicit(self):
        self.c['response']['cost_policy'] = 'explicit'
        self.c['response']['steps'][0]['fill']['fee'] = '2'
        self.assertEqual(self.run_case()['response']['total'], '1065800.0')
        self.c['response']['steps'][0]['fill']['fee'] = None
        self.assertEqual(self.run_case()['response']['state'], 'deferred')

    def test_no_silent_cost_policy(self):
        del self.c['response']['cost_policy']
        self.assertIn('cost_assumption_required', self.run_case()['response']['missing'])

    def test_override_replaces_common_shock(self):
        self.c['common_change_pct'] = '-0.5'
        self.assertEqual(self.run_case()['no_response']['total'], '1013000')
        del self.c['prices']['demo/KR']
        self.assertEqual(self.run_case()['no_response']['total'], '1010000.0')

    def test_price_or_change_not_both(self):
        self.c['prices']['demo/US']['change_pct'] = '-0.2'
        r = self.run_case()['no_response']
        self.assertIsNone(r['total'])
        self.assertIn('demo/US:choose_price_or_change', r['missing'])

    def test_horizons_require_explicit_joint_assumption(self):
        self.c['prices']['demo/US']['horizon'] = '2027-01-01T00:00:00+09:00'
        self.assertIsNone(self.run_case()['no_response']['total'])
        self.c['simultaneous_horizon'] = True
        self.assertEqual(self.run_case()['no_response']['total'], '1013000')

    def test_missing_fx_is_not_zero(self):
        del self.s['fx']['USD']
        r = self.run_case()['no_response']
        self.assertEqual(r['state'], 'partial')
        self.assertEqual(r['known_subtotal'], '23000')
        self.assertEqual(r['known_baseline'], '25000')
        self.assertIsNone(r['total'])

    def test_partial_account_never_full_equity(self):
        self.s['complete'] = False
        r = self.run_case()
        self.assertIsNone(r['no_response']['total'])
        self.assertIsNone(r['response']['total'])

    def test_missing_future_assumption_does_not_erase_current_account(self):
        self.c['fx_to_base']['USD'] = None
        r = self.run_case()
        self.assertEqual(r['baseline']['total'], '1125000')
        self.assertIsNone(r['no_response']['total'])

    def test_two_steps_use_remaining_not_initial_twice(self):
        response = self.c['response']
        point = copy.deepcopy(response['path'][0])
        point.update(id='close-2', at='2026-10-07T06:00:00+09:00')
        point['observations']['demo/US'].update(as_of=point['at'], snapshot_id='second', session='2026-10-06', expected_session='2026-10-06')
        response['path'].append(point)
        step = copy.deepcopy(response['steps'][0])
        step.update(id='sell-half-remaining', path_id='close-2', quantity={'basis': 'remaining', 'value': '0.5'})
        step['fill']['at'] = '2026-10-07T22:30:00+09:00'
        response['steps'].append(step)
        r = self.run_case()['response']
        self.assertEqual(r['remaining_quantities']['demo/US'], '2.50')
        self.assertEqual(r['cash_changes'][0]['after'], '775.00')
        self.assertEqual(r['total'], '1095500.00')

    def test_cash_cma_overlap_excluded(self):
        self.s['cash'][0]['disjoint'] = False
        r = self.run_case()['no_response']
        self.assertNotIn('cash/KRW', r['included'])
        self.assertIsNone(r['total'])

    def test_stale_inputs(self):
        for location, key in [(self.s, 'as_of'), (self.s['positions'][1], 'price_as_of'), (self.s['fx']['USD'], 'as_of')]:
            old = location[key]
            location[key] = '2026-09-01T00:00:00+09:00'
            self.assertIsNone(self.run_case()['no_response']['total'])
            location[key] = old

    def test_missing_price_path_and_fill(self):
        for owner, key in [(self.c['response'], 'path'), (self.c['response']['steps'][0], 'fill')]:
            old = owner.pop(key)
            self.assertEqual(self.run_case()['response']['state'], 'deferred')
            owner[key] = old

    def test_trigger_price_is_not_fill(self):
        r = self.run_case()['response']['ledger'][0]
        self.assertEqual((r['condition_price'], r['fill_price']), ('89', '90'))
        self.c['response']['steps'][0]['fill']['price'] = '70'
        self.assertEqual(self.run_case()['response']['total'], '958000.0')

    def test_oversell_and_duplicate_step(self):
        s = self.c['response']['steps'][0]
        s['quantity'] = {'basis': 'shares', 'value': '11'}
        self.assertIn('quantity_oversold', self.run_case()['response']['missing'])
        self.c['response']['steps'].append(copy.deepcopy(s))
        self.assertIn('duplicate_response_step', self.run_case()['response']['missing'])

    def test_not_triggered_does_not_sell(self):
        self.c['response']['steps'][0]['condition']['threshold'] = '80'
        r = self.run_case()
        self.assertEqual(r['response']['total'], r['no_response']['total'])
        self.assertEqual(r['response']['ledger'][0]['state'], 'not_triggered')

    def test_plan_version_and_purpose_overlap(self):
        self.c['response']['steps'][0]['plan_ref']['version'] = 2
        self.assertIn('response_plan_version_mismatch', self.run_case()['response']['missing'])
        self.c['response']['steps'][0]['plan_ref']['version'] = 1
        step = copy.deepcopy(self.c['response']['steps'][0])
        step.update(id='other-purpose', plan_ref={'id': 'other', 'version': 1})
        self.c['plan_refs'].append(step['plan_ref'])
        self.c['response']['steps'].append(step)
        self.assertIn('purpose_allocations_overlap', self.run_case()['response']['missing'])

    def test_buy_does_not_invent_cash(self):
        self.c['response']['steps'][0]['side'] = 'buy'
        self.assertIn('insufficient_scenario_cash', self.run_case()['response']['missing'])

    def add_second_holding_response(self, *, separate_account=False):
        position = copy.deepcopy(self.s['positions'][1])
        position.update(id='demo/US2', symbol='BETA')
        if separate_account:
            position['account'] = 'separate-synthetic-account'
            self.s['cash'].append({'id': 'cash/USD2', 'account': position['account'],
                'kind': 'cash', 'currency': 'USD', 'amount': '500', 'disjoint': True,
                'source_ref': 'fixture:separate-cash'})
        self.s['positions'].append(position)
        self.c['prices']['demo/US2'] = copy.deepcopy(self.c['prices']['demo/US'])
        response = self.c['response']
        response['steps'][0]['fill']['at'] = '2026-10-10T22:30:00+09:00'
        observation = copy.deepcopy(response['path'][0]['observations']['demo/US'])
        observation.update(as_of='2026-10-07T06:00:00+09:00',
            snapshot_id='second-holding-close', session='2026-10-06', expected_session='2026-10-06')
        response['path'].append({'id': 'close-2', 'at': observation['as_of'],
                                 'observations': {'demo/US2': observation}})
        step = copy.deepcopy(response['steps'][0])
        step.update(id='buy-second', position_id='demo/US2', path_id='close-2', side='buy')
        step['fill']['at'] = '2026-10-07T22:30:00+09:00'
        response['steps'].append(step)

    def test_other_holding_future_proceeds_cannot_fund_earlier_buy(self):
        self.add_second_holding_response()
        original = copy.deepcopy((self.s, self.c))
        result = self.run_case()['response']
        self.assertEqual(result['state'], 'deferred')
        self.assertIsNone(result['total'])
        self.assertIn('cash_flow_fill_order_or_overlap', result['missing'])
        self.assertEqual(original, (self.s, self.c))

    def test_other_holding_later_buy_can_use_earlier_proceeds(self):
        self.add_second_holding_response()
        self.c['response']['steps'][1]['fill']['at'] = '2026-10-11T22:30:00+09:00'
        result = self.run_case()['response']
        self.assertEqual(result['state'], 'complete')
        self.assertEqual(result['remaining_quantities']['demo/US'], '5.0')
        self.assertEqual(result['remaining_quantities']['demo/US2'], '15.0')
        self.assertEqual(result['cash_changes'][0]['after'], '100.0')

    def test_independent_account_fill_does_not_share_cash_or_order(self):
        self.add_second_holding_response(separate_account=True)
        result = self.run_case()['response']
        self.assertEqual(result['state'], 'complete')
        cash = {row['account']: row['after'] for row in result['cash_changes']}
        self.assertEqual(cash['separate-synthetic-account'], '50.0')
        self.assertEqual(cash[self.s['positions'][1]['account']], '550.0')

    def test_invalid_numbers_and_duplicates(self):
        for value in [True, 'NaN', 'Infinity', '-1', None]:
            self.s['positions'][0]['quantity'] = value
            self.assertIsNone(self.run_case()['no_response']['total'])
        self.s['positions'].append(copy.deepcopy(self.s['positions'][0]))
        self.assertIn('duplicate_component_identity', self.run_case()['no_response']['missing'])


class Conditions(unittest.TestCase):
    def setUp(self):
        f = fixture()['scenario']['response']
        self.c = f['steps'][0]['condition']
        self.o = f['path'][0]['observations']['demo/US']
        self.o['evaluated_at'] = f['path'][0]['at']

    def test_explicit_boundary(self):
        self.o['price'] = '90'
        self.assertFalse(evaluate(self.c, self.o)['matched'])
        self.c['operator'] = 'lte'
        self.assertTrue(evaluate(self.c, self.o)['matched'])

    def test_completed_bars_and_expected_session(self):
        for key, value in [('complete', False), ('session', '2026-10-04'), ('currency', 'KRW'), ('price_basis', 'split_adjusted'), ('frame', '1w')]:
            old = self.o[key]
            self.o[key] = value
            self.assertIsNone(evaluate(self.c, self.o)['matched'])
            self.o[key] = old

    def test_qualitative_and_missing_are_not_clear(self):
        self.c.update(kind='qualitative', text='성장 논리 훼손')
        self.assertEqual(evaluate(self.c, self.o)['reason'], 'qualitative_review_required')
        self.c.update(kind='price', threshold=None)
        self.assertIsNone(evaluate(self.c, self.o)['matched'])

    def test_sloped_channel_uses_revision_and_session(self):
        self.c.update(kind='drawing', drawing={'id': 'ch', 'revision': 1, 'geometry_version': 'drawing-tools-linear-v1',
          'edge': 'line_price', 'price_scale': 'linear', 'workspace': {'revision': 1, 'state': {'drawings': [
            {'id': 'ch', 'kind': 'channel', 'points': [{'time': '2026-10-01', 'price': 80}, {'time': '2026-10-05', 'price': 100}, {'time': '2026-10-01', 'price': 110}]}]}}})
        self.o.update(drawing_revision=1, chart={'timeframe': '1d', 'price_basis': 'raw', 'bars': [
          {'date': '2026-10-01'}, {'date': '2026-10-02'}, {'date': '2026-10-05'}]})
        r = evaluate(self.c, self.o)
        self.assertEqual(r['threshold'], '100.0')
        self.assertTrue(r['matched'])
        self.o['drawing_revision'] = 2
        self.assertEqual(evaluate(self.c, self.o)['reason'], 'drawing_revision_changed')


if __name__ == '__main__':
    unittest.main()
