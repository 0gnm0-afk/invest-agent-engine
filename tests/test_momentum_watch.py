"""Synthetic calculator regressions extracted from the private integration suite."""
import copy
from datetime import date,timedelta
import unittest
from invest_agent.trading import momentum_entry as entry,momentum_watch as watch
from invest_agent.research.synthetic_watch import vcp,cup,spr

def observe(b,rs=95,market=False,structure=None):
    strict=entry.analyze(b,rs,{'supportive':market},structure)
    return watch.analyze(b,strict,structure),strict

class WatchTests(unittest.TestCase):
    def test_prebreakout_and_market_caution(self):
        result,strict=observe(vcp()[:-1])
        self.assertFalse(strict['main_candidate']);self.assertTrue(result['signals'])
        self.assertTrue(all(s['phase'] in ('watch','ready') for s in result['signals']))
        self.assertTrue(all(s['breakout_date'] is None and s['entry_zone'] is None for s in result['signals']))
        self.assertTrue(all(s['warnings'] for s in result['signals']))
        self.assertTrue(all(s['known_at']<vcp()[-2]['date'] for s in result['signals']))

    def test_entry_is_preserved_and_separate(self):
        result,strict=observe(vcp(),market=True,structure=spr(vcp()))
        self.assertEqual(result['phase'],'entry')
        self.assertEqual(len([s for s in result['signals'] if s['phase']=='entry']),len(strict['active_events']))
        result,strict=observe(vcp(),market=False)
        self.assertNotIn('entry',[s['phase'] for s in result['signals']])

    def test_not_every_strong_stock_is_watch(self):
        b=vcp()[:290];result,_=observe(b)
        self.assertFalse(result['signals'])
        for rs in (None,20):self.assertFalse(observe(vcp()[:-1],rs)[0]['signals'])

    def test_broken_low_and_chased_price_not_promoted(self):
        b=vcp()[:-1];b[-1]['low']=180
        self.assertFalse(observe(b)[0]['signals'])
        b=vcp();b[-1].update(close=250,high=251)
        self.assertFalse(observe(b)[0]['signals'])

    def test_watch_with_rs_between_70_and_80(self):
        result,strict=observe(vcp()[:-1],rs=75)
        self.assertTrue(result['signals']);self.assertEqual(result['phase'],'watch')
        self.assertFalse(strict['main_candidate'])

    def test_s3_confirmed_and_intact_only(self):
        b=vcp()[:-1];structure=spr(b);w=structure['windows']['126'];w.update(stage='S3',retest_low=205,completion_level=219)
        result,_=observe(b,rs=65,structure=structure)
        self.assertTrue(any(s['kind']=='sperandeo_s3' for s in result['signals']))
        for change in ({'stage':'S2'},{'retest_confirmed_at':'2099-01-01'},{'retest_low':215},{'retest_confirmed_at':b[-40]['date']}):
            altered=copy.deepcopy(structure);altered['windows']['126'].update(change)
            self.assertFalse(any(s['kind']=='sperandeo_s3' for s in observe(b,rs=65,structure=altered)[0]['signals']))

    def test_config_and_insufficient(self):
        for config in ({'rs_min':101},{'max_pivot_gap':.01},{'s3_max_age':True},{'other':1}):
            with self.assertRaises(ValueError):watch.settings(config)
        self.assertFalse(observe(vcp()[:200])[0]['signals'])

    def test_future_and_incomplete_not_used(self):
        b=vcp()[:-1];strict=entry.analyze(b,95,{'supportive':False});expected=watch.analyze(b,strict)
        f=copy.deepcopy(b[-1]);f.update(date='2099-01-01',open=900,close=1000,high=1100,low=800)
        self.assertEqual(expected,watch.analyze(b+[f],strict,as_of=b[-1]['date']))
        f['complete']=False
        self.assertEqual(expected,watch.analyze(b+[f],strict))

if __name__=='__main__':unittest.main()
