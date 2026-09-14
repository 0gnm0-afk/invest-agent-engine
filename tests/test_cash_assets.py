import copy
import unittest
from datetime import datetime, timezone

from invest_agent.trading.cash_assets import classify
from invest_agent.trading.portfolio import review
from invest_agent.trading.morning import holdings
from invest_agent.trading.policy_review import inspect


class CashAssetTests(unittest.TestCase):
    def bundle(self):
        return {'schema_version':1, 'source':'synthetic', 'as_of':'2026-09-11T00:00:00+00:00',
                'max_age_hours':24, 'base_currency':'KRW', 'fx_to_base':{'KRW':'1','USD':'1400'},
                'accounts':[{'alias':'test', 'cash':{'KRW':'100','USD':'2'},
                    'cash_reconciliation':{'cash_excludes_cash_equivalents':True, 'source_ref':'fixture'},
                    'positions':[{'symbol':'CASH-PRODUCT','currency':'KRW','asset_type':'CMA','native_value':'900'},
                                 {'symbol':'S','market':'US','currency':'USD','quantity':'1','price':'10','average_cost':'8'}]}]}

    def review(self,b):
        return review(b,datetime(2026,9,11,1,tzinfo=timezone.utc))

    def test_cash_components_equity_and_slot_exclusion(self):
        b=self.bundle(); result=self.review(b)
        self.assertEqual(result['cash_equivalent_value'],'3800')
        self.assertEqual(result['managed_equity_base'],'17800')
        self.assertEqual(result['risk_asset_count'],1)
        self.assertEqual(len(result['cash_assets']['components']),3)
        self.assertEqual([p['symbol'] for p in holdings({'account_input':{'payload':b}})],['S'])
        self.assertEqual(len(inspect(b,result)['instruments']),1)
        self.assertIsNone(result['verified_investable_cash'])
        self.assertEqual(result['positions'][0]['reference_weight'],'0.20')
        self.assertFalse(result['stop_exposure_complete'])

    def test_missing_fx_is_unavailable_not_zero(self):
        b=self.bundle(); del b['fx_to_base']['USD']
        result=self.review(b)
        self.assertIsNone(result['managed_equity_base'])
        self.assertFalse(result['cash_assets']['valuation_complete'])
        foreign=next(c for c in result['cash_assets']['components'] if c['currency']=='USD')
        self.assertEqual(foreign['reason'],'missing_fx')
        self.assertIsNone(foreign['value_base'])
        self.assertEqual(result['cash_assets']['calculated_cash_component_subtotal'],'1000')

    def test_unverified_overlap_does_not_create_equity(self):
        b=self.bundle(); del b['accounts'][0]['cash_reconciliation']
        result=self.review(b)
        self.assertIsNone(result['equity_base'])
        self.assertEqual(result['cash_assets']['reconciliation'][0]['state'],'unavailable')
        self.assertIn('cash_CMA_overlap_unverified',str(result['errors']))

    def test_classification_priority_and_no_name_guess(self):
        self.assertFalse(classify({'symbol':'X','asset_type':'stock','name':'CMA'}, {'X':True})['cash_equivalent'])
        self.assertTrue(classify({'symbol':'X'}, {'X':True})['cash_equivalent'])
        self.assertTrue(classify({'instrument_class_hint':'CMA_note_from_broker_label'})['cash_equivalent'])
        self.assertFalse(classify({'symbol':'NH-UNKNOWN','name':'CMA'})['cash_equivalent'])


if __name__=='__main__': unittest.main()
