import unittest

from invest_agent.trading.portfolio import broker_allocation


class BrokerAllocationTests(unittest.TestCase):
    def setUp(self):
        self.account = {'as_of': '2026-09-12T00:00:00+00:00', 'accounts': [
            {'alias': 'sample', 'positions': [{'symbol': 'EXAMPLE'}]}]}
        self.entry = {'state': 'available', 'as_of': self.account['as_of'], 'currency': 'KRW',
            'authority': 'valuation_weights_only_no_buying_power', 'confirmation_hash': 'example', 'source_hash': 'example',
            'total_krw': '100', 'rows': [{'account': 'sample', 'symbol': 'EXAMPLE', 'value_krw': '60', 'weight': '999'},
                                       {'account': 'sample', 'symbol': None, 'value_krw': '40'}]}
        self.account['broker_allocation'] = self.entry

    def test_weights_recomputed_and_cash_not_inferred(self):
        result = broker_allocation(self.account)
        self.assertEqual(result['rows'][0]['weight'], '0.6')
        self.assertNotIn('cash_base', result)
        self.assertNotIn('equity_base', result)

    def test_inconsistent_total_rejected(self):
        self.entry['total_krw'] = '101'
        self.assertEqual(broker_allocation(self.account)['state'], 'unavailable')

    def test_missing_or_duplicate_position_rejected(self):
        self.entry['rows'][0]['symbol'] = None
        self.assertEqual(broker_allocation(self.account)['state'], 'unavailable')

    def test_wrong_observation_rejected(self):
        self.entry['as_of'] = 'old'
        self.assertEqual(broker_allocation(self.account)['state'], 'unavailable')
