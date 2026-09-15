import tempfile
import unittest
from pathlib import Path

from invest_agent.trading.account_sync import account_equity, reconcile
from invest_agent.trading.risk_ledger import RiskLedger
from invest_agent.trading.risk_monitoring import evaluate, persist
from invest_agent.trading.risk_reporting import freeze
from invest_agent.trading.runner import writable_store
from test_buy_budget import config, group, position, snapshot
from test_risk_contract import request
from test_risk_monitoring import market

AT = '2026-09-12T00:00:00+00:00'


def account(quantity='12'):
    return {'state': 'loaded', 'payload': {'source': 'synthetic', 'as_of': '2026-09-11T23:00:00+00:00',
            'max_age_hours': 24, 'accounts': [{'alias': 'a', 'holdings_complete': True,
            'positions': [{'symbol': 'TEST', 'market': 'KR', 'currency': 'KRW',
                           'quantity': quantity, 'average_cost': '81'}]}]}}


class AccountSyncTests(unittest.TestCase):
    def test_equity_adapts_only_selected_accounts_and_explicit_available_cash(self):
        value = account()['payload']
        value['accounts'][0].update(net_liquidation_value='100000', cash={'KRW': '99999'}, cash_complete=True)
        value['accounts'].append({'alias': 'isa', 'net_liquidation_value': '9000000'})
        result = account_equity(group(), value, {'KR': '2026-09-11'}, AT)
        self.assertEqual(result['strategy_equity'], '100000')
        self.assertIsNone(result['available_cash'])
        self.assertEqual(result['snapshot_type'], 'INTRADAY_PROVISIONAL')
        value['accounts'][0]['available_cash'] = {'KRW': '500'}
        self.assertEqual(account_equity(group(), value, {'KR': '2026-09-11'}, AT)['available_cash'], {'KRW': '500'})

    def test_verified_nh_assets_are_equity_not_buying_power(self):
        value = account()['payload']
        value['broker_allocation'] = {'state': 'available', 'currency': 'KRW', 'as_of': value['as_of'],
            'checks': 'exact_component_sums_zero_debt_no_foreign_unsettled', 'confirmation_hash': 'verified',
            'rows': [{'account': 'a', 'kind': 'broker_holding', 'value_krw': '900'},
                     {'account': 'a', 'kind': 'residual_assets_not_buying_power', 'value_krw': '100'}]}
        result = account_equity(group(), value, {'KR': '2026-09-11'}, AT)
        self.assertEqual(result['strategy_equity'], '1000')
        self.assertIsNone(result['available_cash'])
        value['broker_allocation']['state'] = 'unavailable'
        self.assertIsNone(account_equity(group(), value, {'KR': '2026-09-11'}, AT))

    def test_morning_equity_snapshot_is_persisted_and_cash_stays_unavailable(self):
        with tempfile.TemporaryDirectory() as directory, writable_store(Path(directory)) as store:
            ledger = RiskLedger(store)
            ledger.configure_strategy(group(), request('g', explicit_user_confirmation=True), actor='user')
            value = account()
            value['payload']['accounts'][0]['net_liquidation_value'] = '100000'
            frozen = freeze(store)
            result = evaluate(frozen, market(), AT, value)
            persist(store, 'equity-run', frozen, result, AT)
            _, _, saved = ledger._budget_inputs('s')
            self.assertEqual(saved['strategy_equity'], '100000')
            self.assertIsNone(saved['available_cash'])
            count = len(ledger.entities('equity_snapshot'))
            frozen = freeze(store)
            persist(store, 'same-account', frozen, evaluate(frozen, market(), AT, value), AT)
            self.assertEqual(len(ledger.entities('equity_snapshot')), count)

    def policy(self):
        return {'strategy': [group()], 'position': [{**position(), 'source': 'synthetic'}],
                'buy_plan': [{'buy_plan_id': 'b', 'position_id': 'pTEST', 'status': 'READY'}]}

    def test_observed_inventory_keeps_stop_and_requires_reapproval(self):
        original = self.policy()
        result = reconcile(original, account(), AT)
        self.assertEqual(result['position'][0]['current_quantity'], '12')
        self.assertEqual(result['position'][0]['current_protection_price'], '90')
        self.assertEqual(result['buy_plan'][0]['status'], 'NEEDS_REAPPROVAL')
        self.assertEqual(original['position'][0]['current_quantity'], '10')

    def test_new_position_never_adopts_imported_stop_and_excludes_other_account(self):
        value = account()
        value['payload']['accounts'][0]['positions'][0]['adopted_stop'] = {'price': '99'}
        value['payload']['accounts'].append({'alias': 'isa', 'positions': [{'bad': 'row'}]})
        result = reconcile({'strategy': [group()]}, value, AT)
        self.assertEqual(len(result['position']), 1)
        self.assertIsNone(result['position'][0]['current_protection_price'])
        self.assertEqual(result['account_reconciliation'][0]['state'], 'available')

    def test_missing_stale_and_source_mismatch_do_not_erase_inventory(self):
        missing = account(); missing['payload']['accounts'][0]['positions'] = []
        missing['payload']['accounts'][0]['holdings_complete'] = False
        stale = account(); stale['payload']['as_of'] = '2026-01-01T00:00:00+00:00'
        mixed = account(); mixed['payload']['source'] = 'broker_export'
        for value in (missing, stale, mixed):
            with self.subTest(value=value):
                result = reconcile(self.policy(), value, AT)
                self.assertEqual(result['position'][0]['current_quantity'], '10')
                self.assertTrue(result['strategy_state'][0]['account_reconciliation_errors'])

    def test_snapshot_before_fill_cannot_undo_fill(self):
        policy = self.policy()
        policy['buy_plan'][0]['tranches'] = [{'actual_fills': [{'actual_at': '2026-09-11T23:30:00+00:00'}]}]
        result = reconcile(policy, account('2'), AT)
        self.assertEqual(result['position'][0]['current_quantity'], '10')
        self.assertTrue(result['account_reconciliation'][0]['errors'])

    def test_initial_reduction_and_archived_fills_block_older_balance(self):
        fill = {'actual_at': '2026-09-11T23:30:00+00:00'}
        for version in ({'initial_reduction': {'actual_fills': [fill]}},
                        {'history': [{'initial_reduction': {'actual_fills': [fill]}}]},
                        {'history': [{'tranches': [{'actual_fills': [fill]}]}]}):
            for quantity in ('9', '0'):
                with self.subTest(version=version, quantity=quantity):
                    policy = self.policy()
                    policy['position'][0]['current_quantity'] = quantity
                    policy['sell_plan'] = [{'position_id': 'pTEST', 'status': 'COMPLETED', **version}]
                    result = reconcile(policy, account('10'), AT)
                    self.assertEqual(len(result['position']), 1)
                    self.assertEqual(result['position'][0]['current_quantity'], quantity)
                    self.assertIn('HOLDING_RECONCILIATION_REQUIRED:a:TEST',
                                  result['strategy_state'][0]['account_reconciliation_errors'])

    def test_old_balance_cannot_undo_confirmed_zero_but_new_balance_can_show_new_holding(self):
        policy = self.policy()
        policy['position'][0].update(current_quantity='0', account_data_as_of='2026-09-11T23:30:00+00:00')
        result = reconcile(policy, account('10'), AT)
        self.assertEqual(len(result['position']), 1)
        self.assertEqual(result['position'][0]['current_quantity'], '0')
        latest = account('2')
        latest['payload']['as_of'] = '2026-09-11T23:45:00+00:00'
        result = reconcile(policy, latest, AT)
        self.assertEqual(len(result['position']), 2)
        self.assertEqual(result['position'][0]['current_quantity'], '0')
        self.assertEqual(result['position'][1]['current_quantity'], '2')
        self.assertIsNone(result['position'][1]['current_protection_price'])

    def test_weekend_uses_latest_completed_session_not_elapsed_hours(self):
        result = reconcile(self.policy(), account(), '2026-09-14T00:00:00+00:00', {'KR': '2026-09-11'})
        self.assertEqual(result['position'][0]['current_quantity'], '12')
        stale = reconcile(self.policy(), account(), '2026-09-15T00:00:00+00:00', {'KR': '2026-09-14'})
        self.assertEqual(stale['position'][0]['current_quantity'], '10')

    def test_complete_zero_balance_closes_without_inventing_fill(self):
        value = account(); value['payload']['accounts'][0]['positions'] = []
        result = reconcile(self.policy(), value, AT)
        self.assertEqual(result['position'][0]['current_quantity'], '0')
        self.assertEqual(result['position'][0]['closure_evidence'], 'COMPLETE_ACCOUNT_BALANCE')
        self.assertEqual(result['buy_plan'][0]['status'], 'NEEDS_REAPPROVAL')
        self.assertNotIn('actual_fills', result['position'][0])
        policy = self.policy()
        policy['buy_plan'][0]['tranches'] = [{'actual_fills': [{'actual_at': '2026-09-11T23:30:00+00:00'}]}]
        stale = reconcile(policy, value, AT)
        self.assertEqual(stale['position'][0]['current_quantity'], '10')

    def test_morning_persists_new_position_and_missing_account_blocks_budget(self):
        with tempfile.TemporaryDirectory() as directory, writable_store(Path(directory)) as store:
            ledger = RiskLedger(store)
            def req(ident):
                return request(ident, explicit_user_confirmation=True)
            ledger.configure_strategy(group(), req('group'), actor='user')
            ledger.configure_risk(config(), req('config'), actor='user')
            ledger.save_equity('s', snapshot(), req('equity'), actor='user')
            frozen = freeze(store)
            result = evaluate(frozen, market(), AT, account())
            persist(store, 'first', frozen, result, AT)
            self.assertEqual(ledger.entities('position')[0]['current_quantity'], '12')
            self.assertIsNone(ledger.entities('position')[0]['current_protection_price'])
            frozen = freeze(store)
            result = evaluate(frozen, market(), AT, {'state': 'missing'})
            persist(store, 'missing', frozen, result, AT)
            self.assertIn('ACCOUNT_MISSING_OR_DUPLICATE:a', ledger.budget_status('s')['block_reasons'])
