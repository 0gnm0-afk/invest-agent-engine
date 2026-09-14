import copy
import unittest
from datetime import datetime, timezone

from invest_agent.trading.morning import portfolio_component
from invest_agent.trading.policy_review import inspect
from invest_agent.trading.portfolio import review


class PolicyTests(unittest.TestCase):
    def bundle(self):
        now=datetime.now(timezone.utc).isoformat()
        pos={'market':'US','symbol':'SYNTH','currency':'USD','quantity':'2','price':'100','average_cost':'80',
             'adopted_stop':{'price':'90','adoption_ref':'stop-v1','price_basis':'executable_raw'}}
        return {'schema_version':1,'source':'synthetic','as_of':now,'max_age_hours':24,'base_currency':'USD',
                'fx_to_base':{'USD':'1'},'accounts':[
                    {'alias':'a','cash':{'USD':'300'},'positions':[copy.deepcopy(pos)]},
                    {'alias':'b','cash':{'USD':'300'},'positions':[copy.deepcopy(pos)]}]}

    def inspect(self,bundle,plans=()):
        return inspect(bundle,review(bundle),plans)

    def test_same_instrument_is_aggregated_across_accounts_without_hard_cap(self):
        result=self.inspect(self.bundle())
        self.assertEqual(result['held_instrument_count'],1)
        self.assertEqual(result['instruments'][0]['weight'],'0.4')
        self.assertTrue(result['instruments'][0]['above_basic_weight'])
        self.assertFalse(result['basic_policy']['weight_is_hard_cap'])
        self.assertEqual(result['authority'],'observation_only_no_gate_approval')

    def test_missing_identity_or_fx_does_not_claim_complete_weight(self):
        bundle=self.bundle();bundle['accounts'][0]['positions'][0].pop('market')
        result=self.inspect(bundle)
        self.assertIsNone(result['held_instrument_count'])
        self.assertIsNone(result['instruments'][0]['weight'])
        bundle=self.bundle();bundle['accounts'][0]['positions'][0]['currency']='KRW'
        self.assertIsNone(self.inspect(bundle)['instruments'][0]['weight'])

    def test_count_excludes_zero_holdings_and_reports_six_unique_instruments(self):
        bundle=self.bundle()
        pos=bundle['accounts'][0]['positions'][0]
        bundle['accounts'][0]['positions']=[{**pos,'symbol':f'SYNTH{i}'} for i in range(5)]
        bundle['accounts'][1]['positions'].append({**pos,'symbol':'ZERO','quantity':'0.0'})
        result=self.inspect(bundle)
        self.assertEqual(result['held_instrument_count'],6)
        self.assertTrue(any(i['reason']=='more_than_five_held_instruments' for i in result['issues']))

    def test_plan_difference_and_source_mismatch_are_visible_without_changing_stop(self):
        bundle=self.bundle();before=copy.deepcopy(bundle)
        plan={'plan_id':'p','source':'user_adopted','account_alias':'a','market':'US','symbol':'SYNTH','currency':'USD',
              'inventory_from_records':'3','stop_price':'85','adoption_ref':'old-stop','alerts':[]}
        result=self.inspect(bundle,[plan])
        reasons=result['plan_checks'][0]['reasons']
        self.assertIn('plan_account_source_mismatch',reasons)
        self.assertIn('recorded_inventory_differs_from_account',reasons)
        self.assertIn('current_stop_differs_from_original_plan_recalculation_required',reasons)
        self.assertEqual(bundle,before)
        self.assertEqual(result['plan_checks'][0]['Q02B'],'not_evaluated_no_exception_granted')

    def test_equal_plan_is_not_automatic_reconciliation_or_GDD_adoption(self):
        bundle=self.bundle()
        plan={'plan_id':'p','source':'synthetic','account_alias':'a','market':'US','symbol':'SYNTH','currency':'USD',
              'inventory_from_records':'2','stop_price':'90','adoption_ref':'stop-v1','alerts':[]}
        result=self.inspect(bundle,[plan])['plan_checks'][0]
        self.assertEqual(result['state'],'needs_input')
        self.assertEqual(result['reasons'],['broker_orders_and_fills_not_reconciled','GDD_adoption_not_connected'])

    def test_morning_portfolio_integrates_without_target_and_keeps_profit_protection(self):
        bundle=self.bundle()
        result=portfolio_component({'account_input':{'state':'loaded','payload':bundle},
                                    'evaluated_at':datetime.now(timezone.utc).isoformat(),'plan_records':[]})
        self.assertEqual(result['state'],'available')
        self.assertEqual(result['result']['policy_review']['instruments'][0]['weight'],'0.4')
        self.assertEqual(result['result']['positions'][0]['stop_pnl_before_costs_base'],'20')
