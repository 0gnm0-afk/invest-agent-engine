import copy
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from invest_agent.trading.contracts import digest
from invest_agent.trading.policy_review import inspect
from invest_agent.trading.portfolio import review
from invest_agent.trading.runner import PipelineRunner
from invest_agent.trading.scenarios import evaluate


class ScenarioTests(unittest.TestCase):
    def bundle(self):
        return {'schema_version':1,'source':'synthetic','as_of':datetime.now(timezone.utc).isoformat(),
            'max_age_hours':24,'base_currency':'USD','fx_to_base':{'USD':'1'},
            'accounts':[{'alias':'paper','cash':{'USD':'1000'},'positions':[
                {'market':'US','symbol':'SYNTH','currency':'USD','quantity':'5','price':'100','average_cost':'80',
                 'adopted_stop':{'price':'90','adoption_ref':'paper-stop','price_basis':'executable_raw'}}]}],
            'risk_capacity':{'reconciled':True,'source_ref':'synthetic-capacity','covered_plan_event_counts':{},
                'reserved_loss_base':'0','instruments':{'US:SYNTH':{'reserved_loss_base':'0','reserved_value_base':'0'}},
                'accounts':{'paper':{'orderable_cash':{'USD':'1000'},'available_to_sell':{'SYNTH':'5'}}}}}

    def request(self):
        return {'scenario_id':'add','account_alias':'paper','market':'US','symbol':'SYNTH','currency':'USD','side':'buy',
            'entry_price':'100','quantity_step':'1','split_fractions':['0.5','0.5'],
            'cost_per_share':'0','slippage_per_share':'0','position_loss_budget_base':'100',
            'portfolio_loss_budget_base':'150','position_value_cap_base':'1000'}

    def inputs(self,bundle,requests=None):
        return {'schema_version':1,'source':'synthetic','account_snapshot_hash':digest(bundle),
                'scenarios':requests or [self.request()]}

    def evaluate(self,bundle,requests=None,plans=()):
        result=review(bundle);result['policy_review']=inspect(bundle,result)
        return evaluate(bundle,result,self.inputs(bundle,requests),plans)['rows'][0]

    def test_added_quantity_risk_weight_and_splits_from_account(self):
        row=self.evaluate(self.bundle())
        self.assertEqual(row['state'],'preview')
        self.assertEqual(row['quantity'],'5')
        self.assertEqual(row['split_quantities'],['2','3'])
        self.assertEqual(row['post_portfolio_stop_exposure_base'],'100')
        self.assertTrue(row['basic_weight_review_required'])
        self.assertTrue(row['quantity_is_upper_bound'])

    def test_reservations_constrain_risk_and_cash_without_double_subtraction(self):
        bundle=self.bundle()
        bundle['risk_capacity']['accounts']['paper']['orderable_cash']['USD']='200'
        bundle['risk_capacity']['reserved_loss_base']='30'
        bundle['risk_capacity']['instruments']['US:SYNTH']['reserved_loss_base']='30'
        row=self.evaluate(bundle)
        self.assertEqual(row['quantity'],'2')
        self.assertEqual(row['committed_portfolio_stop_exposure_base'],'100')

    def test_fx_converts_base_budgets_and_costs_reduce_quantity(self):
        bundle=self.bundle();request=self.request()
        bundle['base_currency']='KRW';bundle['fx_to_base']={'USD':'1400','KRW':'1'}
        for key in ['position_loss_budget_base','portfolio_loss_budget_base','position_value_cap_base']:
            request[key]=str(int(request[key])*1400)
        request['cost_per_share']='1';request['slippage_per_share']='1'
        row=self.evaluate(bundle,[request])
        self.assertEqual(row['quantity'],'4')
        self.assertEqual(row['post_portfolio_stop_exposure_base'],'137200')
        self.assertEqual(row['cash_required'],'408')

    def test_changed_account_hash_and_changed_ledger_are_not_reused(self):
        bundle=self.bundle();inputs=self.inputs(bundle)
        bundle['accounts'][0]['cash']['USD']='900'
        result=review(bundle);result['policy_review']=inspect(bundle,result)
        with self.assertRaisesRegex(ValueError,'snapshot_changed'): evaluate(bundle,result,inputs)
        plans=[{'plan_id':'p','event_count':1,'source':'synthetic'}]
        row=self.evaluate(bundle,plans=plans)
        self.assertEqual(row['state'],'needs_input')
        self.assertEqual(row['reason'],'reservation_snapshot_changed')

    def test_reduce_does_not_require_entry_stops_or_loss_budgets(self):
        bundle=self.bundle();bundle['accounts'][0]['positions'][0].pop('adopted_stop')
        req=self.request();req.update(side='sell',quantity='3')
        for key in ['position_loss_budget_base','portfolio_loss_budget_base','position_value_cap_base']: req.pop(key)
        row=self.evaluate(bundle,[req])
        self.assertEqual(row['state'],'preview')
        self.assertEqual(row['post_quantity'],'2')
        self.assertEqual(row['estimated_realized_pnl'],'60')
        bundle['risk_capacity']['accounts']['paper']['available_to_sell']['SYNTH']='2'
        self.assertEqual(self.evaluate(bundle,[req])['state'],'blocked')

    def test_missing_capacity_and_losing_position_do_not_create_buy_quantity(self):
        bundle=self.bundle();bundle.pop('risk_capacity')
        self.assertEqual(self.evaluate(bundle)['state'],'needs_input')
        bundle=self.bundle();bundle['accounts'][0]['positions'][0]['average_cost']='110'
        self.assertEqual(self.evaluate(bundle)['state'],'blocked')

    def test_other_accounts_same_instrument_consume_instrument_budget(self):
        bundle=self.bundle()
        other=copy.deepcopy(bundle['accounts'][0]);other['alias']='other';other['cash']['USD']='0'
        bundle['accounts'].append(other)
        self.assertEqual(self.evaluate(bundle)['state'],'blocked')

    def test_invalid_one_alternative_does_not_hide_another(self):
        bundle=self.bundle();bad=self.request();bad['scenario_id']='bad';bad.pop('position_value_cap_base')
        result=review(bundle);result['policy_review']=inspect(bundle,result)
        rows=evaluate(bundle,result,self.inputs(bundle,[bad,self.request()]))['rows']
        self.assertEqual([r['state'] for r in rows],['needs_input','preview'])

    def test_morning_snapshot_report_and_resume_freeze_scenarios(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);bundle=self.bundle()
            for name,value in [('account',bundle),('scenarios',self.inputs(bundle)),
                ('config',{'schema_version':1,'account_snapshot':'account.json','scenario_inputs':'scenarios.json'})]:
                (root/(name+'.json')).write_text(json.dumps(value),encoding='utf-8')
            runner=PipelineRunner(root/'instance')
            first=runner.run_morning(root/'config.json','2026-09-11',stop_after='portfolio')
            (root/'scenarios.json').write_text('invalid',encoding='utf-8')
            with patch('invest_agent.trading.charts.generate',return_value=[]):
                resumed=runner.resume(first['run_id'])
                new=runner.run_morning(root/'config.json','2026-09-11')
            report=next((root/'instance/reports').glob('*/'+resumed['run_id']+'/report.md')).read_text(encoding='utf-8')
            self.assertIn('추가매수·축소 시나리오',report)
            self.assertIn('독립 대안',report)
            portfolio=next(a['payload']['result'] for a in resumed['artifacts'] if a['step']=='portfolio')
            self.assertEqual(portfolio['scenarios']['rows'][0]['quantity'],'5')
            self.assertNotEqual(resumed['run_id'],new['run_id'])
            newportfolio=next(a['payload'] for a in new['artifacts'] if a['step']=='portfolio')
            self.assertEqual(newportfolio['result']['scenarios']['state'],'unavailable')
            self.assertEqual(newportfolio['result']['equity_base'],'1500')
