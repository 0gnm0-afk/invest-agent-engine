"""Synthetic calculator regressions extracted from the private integration suite."""
import copy
from datetime import date,timedelta
import unittest
from invest_agent.trading import momentum_entry as m
from invest_agent.research.synthetic_watch import series,vcp,cup,spr

class EngineTests(unittest.TestCase):
    def test_three_patterns_have_prebreakout_pivots(self):
        b=vcp();patterns=m.setups(b,len(b)-1,m.settings())
        self.assertIn('vcp',[p['kind'] for p in patterns]);self.assertIn('range_breakout',[p['kind'] for p in patterns])
        result=m.analyze(b,95,{'supportive':True})
        self.assertTrue(result['main_candidate'],result)
        self.assertTrue(all(e['known_at']<e['breakout_date'] for e in result['active_events']))
        self.assertIn('cup_handle',[p['kind'] for p in m.setups(cup(),len(cup())-1,m.settings())])
        self.assertTrue(m.analyze(cup(),95,{'supportive':True})['main_candidate'])
        b=vcp();b[-1].update(open=212.,low=211.95,close=214.,high=214.3)
        self.assertIn('vcp',[e['kind'] for e in m.analyze(b,95,{'supportive':True})['active_events']])

    def test_s4_only_recent_confirmed_entry(self):
        b=vcp();r=m.analyze(b,95,{'supportive':True},spr(b))
        self.assertTrue(r['sperandeo_candidate'],r)
        for stage in ('S0','S1','S2','S3'):
            structure=spr(b);structure['windows']['126']['stage']=stage
            self.assertFalse(m.analyze(b,95,{'supportive':True},structure)['sperandeo_candidate'])
        structure=spr(b);structure['windows']['126']['retest_confirmed_at']='2099-01-01'
        self.assertFalse(m.analyze(b,95,{'supportive':True},structure)['sperandeo_candidate'])

    def test_s4_can_qualify_without_main_pattern(self):
        b=vcp()
        for bar in b[:-1]:bar['volume']=10000
        r=m.analyze(b,95,{'supportive':True},spr(b))
        self.assertFalse(r['main_candidate'])
        self.assertTrue(r['sperandeo_candidate'])
        self.assertEqual({e['route'] for e in r['active_events']},{'sperandeo'})

    def test_future_and_incomplete_do_not_change_results(self):
        b=vcp();asof=b[-1]['date'];expected=m.analyze(b,95,{'supportive':True})
        future=copy.deepcopy(b[-1]);future.update(date='2099-01-01',open=900,close=1000,high=1100,low=800,volume=1e12)
        self.assertEqual(expected,m.analyze(b+[future],95,{'supportive':True},as_of=asof))
        future['complete']=False
        self.assertEqual(expected,m.analyze(b+[future],95,{'supportive':True}))

    def test_weak_volume_and_upper_wick_not_entries(self):
        for field,value in [('volume',1000),('high',240)]:
            b=vcp();b[-1][field]=value
            r=m.analyze(b,95,{'supportive':True},spr(b));self.assertFalse(r['candidate'])

    def test_setup_alone_not_entry(self):
        b=vcp()[:-1];r=m.analyze(b,95,{'supportive':True})
        self.assertFalse(r['main_candidate']);self.assertTrue(r['forming_setups'])

    def test_old_breakout_failed_pivot_and_structure_excluded(self):
        base=vcp();setup=m.setups(base,len(base)-1,m.settings())[0]
        for closes in ([220,220.2,220.3],[setup['pivot']-.1],[220]):
            b=copy.deepcopy(base)
            for c in closes:
                new=copy.deepcopy(b[-1]);new.update(date=(date.fromisoformat(b[-1]['date'])+timedelta(days=1)).isoformat(),open=c-.1,high=c+.4,low=c-.4,close=c,volume=1000);b.append(new)
            if closes==[220]:b[-1]['low']=setup['structure_stop']-.1
            event=m.entry_event(b,len(base)-1,setup,m.settings());self.assertFalse(event['entry_pass'],event)

    def test_no_chasing_or_wide_stop(self):
        b=vcp();setup=m.setups(b,len(b)-1,m.settings())[0]
        b[-1].update(close=250,high=251)
        self.assertFalse(m.entry_event(b,len(b)-1,setup,m.settings())['checks']['not_extended'])
        b=vcp();setup['structure_stop']=150
        self.assertFalse(m.entry_event(b,len(b)-1,setup,m.settings())['checks']['bounded_structure_risk'])

    def test_leadership_market_and_trend_are_required(self):
        self.assertFalse(m.analyze(vcp(),70,{'supportive':True})['main_candidate'])
        self.assertFalse(m.analyze(vcp(),95,{'supportive':False})['main_candidate'])
        self.assertFalse(m.analyze(vcp(),None,{'supportive':True})['main_candidate'])
        b=series([300-i*.3 for i in range(320)])
        self.assertFalse(m.features(b)['template_pass'])
        self.assertFalse(m.market_context(b)['supportive'])

    def test_flat_v_shape_and_short_history_are_not_cup(self):
        b=cup();
        for i in range(250,310):b[i].update(open=174,close=174,high=174.4,low=173.6)
        b[280].update(open=140,close=140,high=140.4,low=139.6)
        self.assertNotIn('cup_handle',[p['kind'] for p in m.setups(b,len(b)-1,m.settings())])
        self.assertEqual(m.analyze(b[:200],95,{'supportive':True})['state'],'insufficient_history')

    def test_settings_and_bar_validation(self):
        for cfg in ({'unknown':1},{'market_filter':1},{'max_entry_age':True},{'rs_min':101},{'volume_multiple':.5},{'dry_volume_ratio':1}):
            with self.assertRaises(ValueError):m.settings(cfg)
        b=vcp();b[-1]['close']=float('nan')
        with self.assertRaises(ValueError):m.analyze(b,95,{'supportive':True})
        b=vcp();b[-1]['date']=b[-2]['date']
        with self.assertRaises(ValueError):m.completed(b)

if __name__=='__main__':unittest.main()
