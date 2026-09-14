import copy
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import test_scenarios
from invest_agent.trading.capacity import reconcile
from invest_agent.trading.contracts import digest
from invest_agent.trading.policy_review import inspect
from invest_agent.trading.portfolio import review
from invest_agent.trading.runner import PipelineRunner
from invest_agent.trading.scenarios import evaluate


class CapacityTests(unittest.TestCase):
    def bundle(self):
        bundle=test_scenarios.ScenarioTests().bundle();bundle.pop('risk_capacity')
        return bundle

    def orders(self,bundle,working=(),cash='1000',sell='5'):
        return {'schema_version':1,'source':'synthetic','source_ref':'synthetic-orders',
            'as_of':bundle['as_of'],'account_snapshot_hash':digest(bundle),'orders_complete':True,
            'max_snapshot_skew_seconds':30,'accounts':[{'alias':'paper','balance_basis':'net_of_broker_working_orders',
            'orderable_cash':{'USD':cash},'available_to_sell':{'US:SYNTH':sell},'working_orders':list(working)}]}

    def order(self,side='buy',quantity='5',link=None,ident='o1'):
        value={'order_id':ident,'market':'US','symbol':'SYNTH','currency':'USD','side':side,'remaining_quantity':quantity,
               'price':'100','price_basis':'executable_raw','cost_per_share':'0','slippage_per_share':'0',
               'cash_reserved':str(int(quantity)*100),'adopted_stop':{'price':'90','adoption_ref':'paper-stop','price_basis':'executable_raw'}}
        if link:value['local_reservation_id']=link
        return value

    def plan(self,side='buy',quantity='5',inventory='5'):
        return {'plan_id':'p','source':'synthetic','account_alias':'paper','market':'US','symbol':'SYNTH','currency':'USD',
            'inventory_from_records':inventory,'event_count':1,'cost_per_share':'0','slippage_per_share':'0',
            'stop_price':'90','adoption_ref':'paper-stop','alerts':[],
            'tranches':[{'tranche_id':'t','side':side,'quantity':quantity,'price':'100'}],
            'reservations':{'r1':{'remaining':quantity,'tranche_id':'t'}}}

    def test_linked_order_is_not_subtracted_or_counted_twice(self):
        bundle=self.bundle()
        result=reconcile(bundle,self.orders(bundle,[self.order(link='r1')],cash='500'),[self.plan()])
        self.assertEqual(result['accounts']['paper']['orderable_cash']['USD'],'500')
        self.assertEqual(result['reserved_loss_base'],'50')
        self.assertEqual(result['coverage']['linked_reservations'],1)

    def test_unlinked_local_is_additional_to_net_broker_capacity(self):
        bundle=self.bundle()
        result=reconcile(bundle,self.orders(bundle,[self.order(quantity='2')],cash='800'),[self.plan()])
        self.assertEqual(result['accounts']['paper']['orderable_cash']['USD'],'300')
        self.assertEqual(result['reserved_loss_base'],'70')
        self.assertEqual(result['instruments']['US:SYNTH']['reserved_value_base'],'700')

    def test_partial_fill_and_stress_cash(self):
        bundle=self.bundle();bundle['accounts'][0]['positions'][0]['quantity']='2'
        order=self.order(quantity='3',link='r1');order['slippage_per_share']='1'
        result=reconcile(bundle,self.orders(bundle,[order],cash='700',sell='2'),[self.plan(quantity='3',inventory='2')])
        self.assertEqual(result['accounts']['paper']['orderable_cash']['USD'],'697')
        self.assertEqual(result['reserved_loss_base'],'33')

    def test_inventory_mismatch_duplicate_order_and_changed_link_fail(self):
        bundle=self.bundle()
        with self.assertRaisesRegex(ValueError,'inventory_not_reconciled'):
            reconcile(bundle,self.orders(bundle),[self.plan(inventory='4')])
        with self.assertRaisesRegex(ValueError,'duplicate_broker_order'):
            reconcile(bundle,self.orders(bundle,[self.order(),self.order()]))
        with self.assertRaisesRegex(ValueError,'contents_differ'):
            reconcile(bundle,self.orders(bundle,[self.order(quantity='4',link='r1')]),[self.plan()])

    def test_pending_sales_do_not_release_risk_and_capacity_is_conserved(self):
        bundle=self.bundle()
        result=reconcile(bundle,self.orders(bundle,[self.order(side='sell',quantity='2')],sell='3'),[self.plan(side='sell',quantity='1')])
        self.assertEqual(result['accounts']['paper']['available_to_sell']['SYNTH'],'2')
        self.assertEqual(result['reserved_loss_base'],'0')
        with self.assertRaisesRegex(ValueError,'sale_capacity_inconsistent'):
            reconcile(bundle,self.orders(bundle,[self.order(side='sell',quantity='2')],sell='5'))

    def test_missing_pending_stop_does_not_become_zero_risk_or_block_sale(self):
        bundle=self.bundle();bundle['accounts'][0]['positions'][0].pop('adopted_stop')
        result=reconcile(bundle,self.orders(bundle,[self.order(quantity='1')],cash='900'))
        self.assertIsNone(result['reserved_loss_base'])
        self.assertTrue(result['purchase_blockers'])
        req=test_scenarios.ScenarioTests().request();req.update(side='sell',quantity='2')
        reviewed=review(bundle);reviewed['policy_review']=inspect(bundle,reviewed)
        inputs=test_scenarios.ScenarioTests().inputs(bundle,[req])
        self.assertEqual(evaluate(bundle,reviewed,inputs,capacity=result)['rows'][0]['state'],'preview')

    def test_current_adopted_stop_is_used_without_modifying_original_plan(self):
        bundle=self.bundle();bundle['accounts'][0]['positions'][0]['adopted_stop']['price']='95'
        plan=self.plan();original=copy.deepcopy(plan)
        result=reconcile(bundle,self.orders(bundle),[plan])
        self.assertEqual(result['reserved_loss_base'],'25')
        self.assertEqual(plan,original)

    def test_hash_freshness_and_complete_account_coverage(self):
        bundle=self.bundle();orders=self.orders(bundle)
        bundle['accounts'][0]['cash']['USD']='900'
        with self.assertRaisesRegex(ValueError,'snapshot_mismatch'):reconcile(bundle,orders)
        orders=self.orders(bundle);orders['as_of']=(datetime.now(timezone.utc)-timedelta(minutes=2)).isoformat()
        with self.assertRaisesRegex(ValueError,'time_skew'):reconcile(bundle,orders)
        orders=self.orders(bundle);orders['orders_complete']=False
        with self.assertRaisesRegex(ValueError,'complete_working'):reconcile(bundle,orders)
        orders=self.orders(bundle);orders['accounts']=[]
        with self.assertRaisesRegex(ValueError,'accounts_incomplete'):reconcile(bundle,orders)

    def test_morning_produces_capacity_and_does_not_fallback_on_bad_orders(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);bundle=self.bundle();orders=self.orders(bundle,[self.order(quantity='2')],cash='800')
            values={'account':bundle,'orders':orders,'scenarios':test_scenarios.ScenarioTests().inputs(bundle),
                'config':{'schema_version':1,'account_snapshot':'account.json','order_snapshot':'orders.json','scenario_inputs':'scenarios.json'}}
            for name,value in values.items():(root/(name+'.json')).write_text(json.dumps(value),encoding='utf-8')
            runner=PipelineRunner(root/'instance')
            with patch('invest_agent.trading.charts.generate',return_value=[]):
                first=runner.run_morning(root/'config.json','2026-09-12')
                (root/'orders.json').write_text('invalid',encoding='utf-8')
                second=runner.run_morning(root/'config.json','2026-09-12')
            portfolio=next(a['payload']['result'] for a in first['artifacts'] if a['step']=='portfolio')
            self.assertEqual(portfolio['reconciliation']['state'],'available')
            self.assertEqual(portfolio['scenarios']['rows'][0]['quantity'],'3')
            report=next((root/'instance/reports').glob('*/'+first['run_id']+'/report.md')).read_text(encoding='utf-8')
            self.assertIn('미체결·로컬 예약 대조',report)
            failed=next(a['payload']['result'] for a in second['artifacts'] if a['step']=='portfolio')
            self.assertEqual(failed['reconciliation']['state'],'unavailable')
            self.assertEqual(failed['scenarios']['rows'][0]['state'],'needs_input')
            self.assertEqual(failed['equity_base'],'1500')

    def test_real_ledger_partial_fill_status_links_to_remaining_broker_order(self):
        from invest_agent.trading.plans import PlanLedger
        from invest_agent.trading.runner import writable_store
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            plan=json.loads((Path(__file__).resolve().parents[1]/'examples/plan_fixture.json').read_text(encoding='utf-8'))
            plan.update(account_alias='paper',symbol='SYNTH',adoption_ref='paper-stop',
                        adopted_at=(datetime.now(timezone.utc)-timedelta(days=1)).isoformat())
            stamp=datetime.now(timezone.utc).isoformat()
            with writable_store(root) as store:
                ledger=PlanLedger(store);ledger.record(plan)
                event={'schema_version':1,'source':'synthetic','source_ref':'capacity-test','plan_id':plan['plan_id'],
                       'occurred_at':stamp,'event_id':'r1','kind':'reserve','tranche_id':'B1','quantity':'5',
                       'context':{'account_alias':'paper','currency':'USD','price_basis':'executable_raw',
                           'snapshot_ref':'synthetic','as_of':stamp,'held_quantity':'0','orderable_cash':'1000',
                           'available_to_sell':'0','price':'100','average_cost':'0','broker_reflected_reservations':[],
                           'open_orders_reconciled':True}}
                from legacy_fixture import seed_legacy_reservation
                seed_legacy_reservation(store, event)
                ledger.record_event({'schema_version':1,'source':'synthetic','source_ref':'capacity-test',
                    'plan_id':plan['plan_id'],'occurred_at':stamp,'event_id':'f1','kind':'fill','reservation_id':'r1',
                    'quantity':'2','price':'100','execution_id':'synthetic-fill'})
                plans=ledger.list_plans(active_only=True)
            bundle=self.bundle();bundle['accounts'][0]['positions'][0]['quantity']='2'
            result=reconcile(bundle,self.orders(bundle,[self.order(quantity='3',link='r1')],cash='700',sell='2'),plans)
            self.assertEqual(result['reserved_loss_base'],'30')
            self.assertEqual(result['accounts']['paper']['orderable_cash']['USD'],'700')
            self.assertEqual(result['covered_plan_event_counts'],{plan['plan_id']:2})
