import copy
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from invest_agent.trading.portfolio import review
from invest_agent.trading.portfolio_changes import compare
from invest_agent.trading.runner import PipelineRunner, writable_store


class ChangeTests(unittest.TestCase):
    def bundle(self,price='100',age=2):
        return {'schema_version':1,'source':'synthetic','as_of':(datetime.now(timezone.utc)-timedelta(hours=age)).isoformat(),
            'max_age_hours':24,'base_currency':'USD','fx_to_base':{'USD':'1'},
            'accounts':[{'alias':'paper','cash':{'USD':'1000'},'positions':[
                {'market':'US','symbol':'SYNTH','currency':'USD','quantity':'5','price':price,'average_cost':'80',
                 'adopted_stop':{'price':'90','adoption_ref':'synthetic-stop-v1','price_basis':'executable_raw'}}]}]}

    def baseline(self,bundle):
        return {**review(bundle),'state':'available','run_id':'prior','report_date':'2026-09-11'}

    def change(self,current,prior):
        return compare(review(current),self.baseline(prior))['rows'][0]['change']

    def test_closer_breach_persistent_and_rebound_are_distinct(self):
        cases=[('100','95','closer_to_stop'),('95','89','new_stop_breach'),
               ('89','88','breach_persists'),('89','95','above_stop_again_execution_unknown'),
               ('95','100','farther_from_stop')]
        for old,new,expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(self.change(self.bundle(new,1),self.bundle(old,2)),expected)

    def test_stop_change_is_not_a_price_breach_or_distance_signal(self):
        prior=self.bundle();current=self.bundle('95',1)
        current['accounts'][0]['positions'][0]['adopted_stop']={'price':'96','adoption_ref':'new-stop','price_basis':'executable_raw'}
        before=copy.deepcopy(current)
        self.assertEqual(self.change(current,prior),'stop_adoption_changed')
        self.assertEqual(current,before)

    def test_stale_missing_and_older_data_do_not_clear_alarm(self):
        self.assertEqual(self.change(self.bundle('100',30),self.bundle('89',31)),'current_data_stale')
        self.assertEqual(self.change(self.bundle('100',1),self.bundle('89',30)),'previous_data_stale')
        self.assertEqual(self.change(self.bundle('100',3),self.bundle('89',2)),'older_snapshot')
        current=self.bundle('100',1);current['accounts'][0]['positions'][0].pop('adopted_stop')
        self.assertEqual(self.change(current,self.bundle('89',2)),'current_stop_or_price_unavailable')

    def test_repeat_and_same_timestamp_revision(self):
        prior=self.bundle();current=copy.deepcopy(prior)
        self.assertEqual(self.change(current,prior),'same_observation')
        current['accounts'][0]['positions'][0]['price']='89'
        self.assertEqual(self.change(current,prior),'same_time_revision')

    def test_missing_or_zero_holding_does_not_infer_a_sale(self):
        prior=self.bundle('89',2);current=self.bundle('100',1)
        current['accounts'][0]['positions']=[]
        self.assertEqual(self.change(current,prior),'not_observed_no_execution_inference')
        current=self.bundle('89',1);current['accounts'][0]['positions'][0]['quantity']='0'
        self.assertEqual(self.change(current,prior),'zero_quantity_observation_no_execution_inference')

    def test_new_source_or_unidentified_market_is_not_a_proven_change(self):
        current=self.bundle('89',1);current['accounts'][0]['positions'][0].pop('market')
        self.assertEqual(self.change(current,self.bundle('100',2)),'identity_unconfirmed')
        self.assertEqual(compare(review(self.bundle()),{})['rows'][0]['change'],'no_baseline')
        reviewed=review(self.bundle());reviewed['source']='broker_export'
        self.assertEqual(compare(reviewed,self.baseline(self.bundle()))['state'],'no_baseline')

    def test_morning_freezes_prior_account_and_renders_changes_after_resume(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            (root/'config.json').write_text(json.dumps({'schema_version':1,'account_snapshot':'account.json'}),encoding='utf-8')
            runner=PipelineRunner(root/'instance')
            def run(bundle,day,stop=None):
                (root/'account.json').write_text(json.dumps(bundle),encoding='utf-8')
                with patch('invest_agent.trading.charts.generate',return_value=[]):
                    return runner.run_morning(root/'config.json',day,stop)
            prior=run(self.bundle('100',2),'2026-09-11')
            current=self.bundle('89',1)
            stopped=run(current,'2026-09-12','snapshot')
            with writable_store(root / 'instance') as store, store.db:
                store.db.execute("UPDATE artifacts SET payload_hash='corrupt' WHERE run_id=? AND step_key='portfolio'",(prior['run_id'],))
            (root/'account.json').unlink()
            with patch('invest_agent.trading.charts.generate',return_value=[]):
                resumed=runner.resume(stopped['run_id'])
            result=next(a['payload']['result'] for a in resumed['artifacts'] if a['step']=='portfolio')
            self.assertEqual(result['changes']['rows'][0]['change'],'new_stop_breach')
            report=next((root/'instance/reports').glob('*/'+resumed['run_id']+'/report.md')).read_text(encoding='utf-8')
            self.assertIn('새 손절선 이탈 관측',report)
            again=run(current,'2026-09-12')
            result=next(a['payload']['result'] for a in again['artifacts'] if a['step']=='portfolio')
            self.assertEqual(result['changes']['state'],'no_baseline')
