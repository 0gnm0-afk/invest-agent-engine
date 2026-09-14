import copy
import unittest
from decimal import Decimal

from invest_agent.trading.holding_review import evaluate_position,proximity,attach
from invest_agent.trading.technical_context import build
import test_technical_context


class HoldingReviewTests(unittest.TestCase):
    def fixture(self):
        s,m=test_technical_context.TechnicalContextTests().fixture()
        ctx=build(s,m)
        p={'symbol':'HELD','market':'US','currency':'USD','quantity':'10','average_cost':'80'}
        policy={key:{'value':v,'approved_by_user':True,'source_ref':'synthetic_test_only'} for key,v in
                [('atr_period',14),('warning_atr_multiple','1'),('position_stop_risk_limit_pct','0.015'),('account_total_stop_risk_limit_pct','0.06')]}
        return p,ctx,policy

    def test_unadopted_and_unapproved_are_unavailable(self):
        p,c,policy=self.fixture(); r=evaluate_position(p,c,'10000','1',{})
        self.assertIsNone(r['risk_to_adopted_stop_amount'])
        self.assertEqual(r['risk_reason'],'adopted_protection_rule_missing')
        self.assertEqual(r['proximity']['proximity_state'],'unavailable')
        self.assertEqual(r['limit_evaluation_state'],'unavailable')
        self.assertEqual(r['recommended_protection_rules'],[])

    def test_adopted_ma_changes_value_not_rule(self):
        p,c,policy=self.fixture()
        p['adopted_stop']={'adoption_ref':'user_fixture','rule':{'line_type':'SMA','timeframe':'daily','period':20,'breach_basis':'daily_close'}}
        original=copy.deepcopy(p)
        r=evaluate_position(p,c,'10000','1',policy)
        self.assertTrue(r['risk_calculation_complete'])
        risk=(Decimal(r['current_reference_price'])-Decimal(r['adopted_stop_reference_price']))*10
        self.assertEqual(Decimal(r['risk_to_adopted_stop_amount']),risk)
        self.assertEqual(Decimal(r['risk_to_adopted_stop_pct_of_equity']),risk/10000)
        self.assertNotEqual(r['total_trade_pnl_at_stop'],r['risk_to_adopted_stop_amount'])
        changed=copy.deepcopy(c); changed['daily_bars'][-1]['close']+=1
        later=evaluate_position(p,changed,'10000','1',policy)
        self.assertNotEqual(r['adopted_stop_reference_price'],later['adopted_stop_reference_price'])
        self.assertEqual(p,original)
        self.assertEqual(r['adopted_stop'],later['adopted_stop'])
        self.assertTrue(r['excludes_costs'])
        self.assertIn('갭',r['execution_note'])

    def test_breached_is_not_normal_zero_risk(self):
        p,c,policy=self.fixture()
        p['adopted_stop']={'adoption_ref':'user_fixture','price':'150','price_basis':'executable_raw'}
        r=evaluate_position(p,c,'10000','1',policy)
        self.assertEqual(r['protection_state'],'adopted_protection_breached')
        self.assertEqual(r['weakness_state'],'adopted_protection_breached')
        self.assertEqual(r['proximity']['proximity_state'],'breached')
        self.assertIsNone(r['risk_to_adopted_stop_amount'])

    def test_atr_distance_and_no_auto_actions(self):
        p,c,policy=self.fixture();before=copy.deepcopy(p)
        r=proximity(c,Decimal('100'),Decimal('98'),policy)
        self.assertEqual(Decimal(r['atr_value']),Decimal('3'))
        self.assertEqual(Decimal(r['distance_to_stop_atr']),Decimal(2)/3)
        self.assertEqual(r['proximity_state'],'approaching')
        r=evaluate_position(p,c,'10000','1',policy)
        self.assertEqual(r['price_basis'],'latest_completed_daily_close')
        self.assertEqual(p,before)
        for key in ('orders','sell_quantity','action','new_stop'):self.assertNotIn(key,r)

    def test_partial_aggregate_excludes_missing_risk(self):
        p,c,policy=self.fixture()
        p['adopted_stop']={'adoption_ref':'user_fixture','price':'130','price_basis':'executable_raw'}
        q=copy.deepcopy(p);q['symbol']='NO-STOP';q.pop('adopted_stop')
        result={'managed_equity_base':'10000','positions':[{'account':'a','symbol':'HELD'},{'account':'a','symbol':'NO-STOP'}]}
        bundle={'accounts':[{'alias':'a','positions':[p,q]}],'fx_to_base':{'USD':'1'}}
        market={'screen':{'rows':[{'symbol':'HELD','technical_context':c},{'symbol':'NO-STOP','technical_context':c}]}}
        r=attach(result,bundle,market,policy)
        self.assertEqual(r['stop_risk_coverage'],{'calculated':1,'total':2})
        self.assertFalse(r['aggregate_complete'])
        self.assertEqual(r['positions_without_protection'],1)
        self.assertIsNone(r['account_stop_risk_pct'])
        self.assertEqual(r['limit_reason'],'aggregate_risk_incomplete')
        self.assertIsNone(r['positions'][1]['stop_exposure_base'])

    def test_reference_breach_is_distinct_from_weekly_close_confirmation(self):
        p,c,policy=self.fixture()
        p['adopted_stop']={'adoption_ref':'user_fixture','price':'138','price_basis':'executable_raw','breach_basis':'weekly_close'}
        c['weekly_bars'][-1]['close']=140
        c['daily_bars'][-1]['close']=137
        r=evaluate_position(p,c,'10000','1',policy)
        self.assertEqual(r['protection_state'],'maintained')
        self.assertEqual(r['proximity']['proximity_state'],'breached')
        self.assertIsNone(r['risk_to_adopted_stop_amount'])
        self.assertIn('awaiting_close_confirmation',r['risk_reason'])

    def test_approved_limits_and_full_aggregate_use_equity_not_weight(self):
        p,c,policy=self.fixture()
        p['adopted_stop']={'adoption_ref':'fixture','price':'100','price_basis':'executable_raw'}
        r=evaluate_position(p,c,'1000','2',policy)
        self.assertEqual(r['limit_evaluation_state'],'above_approved_limit')
        risk=(Decimal(r['current_reference_price'])-100)*10*2
        self.assertEqual(Decimal(r['risk_to_adopted_stop_amount']),risk)
        result={'managed_equity_base':'1000','positions':[{'account':'a','symbol':'HELD','portfolio_weight':'0.9'}]}
        bundle={'accounts':[{'alias':'a','positions':[p]}],'fx_to_base':{'USD':'2'}}
        market={'screen':{'rows':[{'symbol':'HELD','technical_context':c}]}}
        account=attach(result,bundle,market,policy)
        self.assertTrue(account['aggregate_complete'])
        self.assertEqual(Decimal(account['account_stop_risk_pct']),risk/1000)
        self.assertEqual(account['limit_evaluation_state'],'above_approved_limit')
        self.assertEqual(account['positions'][0]['portfolio_weight'],'0.9')


if __name__=='__main__':unittest.main()
