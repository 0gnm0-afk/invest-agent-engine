import unittest

from invest_agent.trading.risk_budget import equity_snapshot, fx_rate, review_budget
from invest_agent.trading.risk_monitoring import evaluate
from test_buy_budget import config, group, snapshot
from test_risk_monitoring import market


class RiskFreshnessTests(unittest.TestCase):
    def test_fx_latest_session_survives_weekend_but_stale_future_invalid_block(self):
        value = snapshot()
        value['created_at'] = '2026-09-14T00:00:00+00:00'
        value['fx'] = {'USD': {'fx_rate': '1300', 'fx_source': 'test', 'fx_as_of': '2026-09-11'}}
        self.assertEqual(str(fx_rate(value, 'USD')), '1300')
        for stamp in ('2026-09-10', '2026-09-15', 'invalid'):
            with self.subTest(stamp=stamp):
                value['fx']['USD']['fx_as_of'] = stamp
                self.assertIsNone(fx_rate(value, 'USD'))
                result = review_budget(group(), config(), value, [], [])
                self.assertFalse(result['approval_allowed'])
                self.assertIn('STALE_DATA:FX:USD', result['block_reasons'])

    def test_stale_fx_equity_is_unknown_not_zero(self):
        raw = snapshot()
        raw['accounts'][0]['currency'] = 'USD'
        raw['fx'] = {'USD': {'fx_rate': '1300', 'fx_source': 'test', 'fx_as_of': '2026-09-10'}}
        result = equity_snapshot(group(), raw)
        self.assertIsNone(result['strategy_equity'])
        self.assertIsNone(result['account_components'][0]['equity_value_base'])
        self.assertEqual(result['account_components'][0]['equity_value_native'], '100000')
        self.assertIn('STALE_DATA:FX:USD', result['errors'])

    def test_official_morning_requires_completed_session_without_relabeling_provisional(self):
        for kind in ('COMPLETED_SESSION', 'INTRADAY_PROVISIONAL', 'MANUAL'):
            with self.subTest(kind=kind):
                value = snapshot()
                value['snapshot_type'] = kind
                frozen = {'strategy': [group()], 'risk_config': [config()], 'budget_snapshots': [value]}
                result = evaluate(frozen, market(), '2026-09-12T00:00:00+00:00')
                budget = result['budgets'][0]
                self.assertEqual(budget['equity_snapshot_type'], kind)
                self.assertEqual(budget['official_daily_review_status'],
                                 'AVAILABLE' if kind == 'COMPLETED_SESSION' else 'UNAVAILABLE')
                self.assertEqual(budget['approval_allowed'], kind == 'COMPLETED_SESSION')
                self.assertEqual(value.get('errors'), [])
