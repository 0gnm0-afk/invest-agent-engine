"""Synthetic calculator regressions extracted from the private integration suite."""
import copy
from datetime import date,timedelta
import unittest
from invest_agent.trading import base_structure as base
from invest_agent.research.synthetic_watch import bars

class BaseTests(unittest.TestCase):
    def test_falling_long_trend_bottom_is_observable(self):
        r=base.analyze(bars());self.assertTrue(r['signals'])
        self.assertEqual(r['signals'][0]['kind'],'bottom_base')
        for s in r['signals']:
            self.assertEqual(s['phase'],'watch');self.assertFalse(s['entry_pass'])
            self.assertIsNone(s['breakout_date']);self.assertIsNone(s['entry_zone'])
            self.assertGreater(s['metrics']['separated_test_pairs'],0)
            self.assertLess(s['known_at'],bars()[-1]['date'])
            self.assertAlmostEqual(s['range_reward_risk'],(s['pivot']-104)/(104-s['structure_stop']))

    def test_rising_then_pullback_is_separate_pattern(self):
        r=base.analyze(bars('pullback'));self.assertTrue(r['signals'])
        self.assertTrue(any(s['kind']=='pullback_base' for s in r['signals']))

    def test_recent_pullback_not_hidden_by_older_higher_peak(self):
        b=bars('pullback');b[160].update(open=400,close=400,high=401,low=399)
        self.assertTrue(any(s['kind']=='pullback_base' for s in base.analyze(b)['signals']))

    def test_broken_floor_and_chase_rejected(self):
        for price in (90,130):
            b=bars();b[-1].update(open=price,close=price,high=price+1,low=price-1)
            self.assertFalse(base.analyze(b)['signals'])

    def test_no_preceding_move_or_continued_decline(self):
        b=bars()
        for x in b[:-40]:x.update(open=104,close=104,high=105,low=103)
        self.assertFalse(base.analyze(b)['signals'])
        b=bars()
        for i,x in enumerate(b[-100:]):
            c=180-i;x.update(open=c,close=c,high=c+1,low=c-1)
        self.assertFalse(base.analyze(b)['signals'])

    def test_future_incomplete_and_current_extremes_do_not_set_floor(self):
        b=bars();r=base.analyze(b)
        f=copy.deepcopy(b[-1]);f.update(date='2099-01-01',low=1,high=1000)
        self.assertEqual(r,base.analyze(b+[f],as_of=b[-1]['date']))
        f['complete']=False;self.assertEqual(r,base.analyze(b+[f]))
        b[-1]['high']=300
        self.assertEqual([s['pivot'] for s in r['signals']],[s['pivot'] for s in base.analyze(b)['signals']])

    def test_slow_directional_slide_is_not_a_flat_base(self):
        b=bars()
        for i,x in enumerate(b[-40:]):
            c=110-.35*i+.7*(i%5);x.update(open=c,close=c,high=c+.8,low=c-.8)
        r=base.analyze(b)
        self.assertFalse(r['signals'])
        self.assertTrue(any('not_directional_range' in x['rejections'] for x in r['windows']))

    def test_return_to_floor_during_selloff_is_not_stabilized(self):
        from math import sin,pi
        b=bars()
        for i,x in enumerate(b[-40:]):
            c=100+16*sin(pi*i/39);x.update(open=c,close=c,high=c+1,low=c-1)
        r=base.analyze(b);self.assertFalse(r['signals'])
        self.assertTrue(all('recent_stabilization' in x['rejections'] for x in r['windows']))

    def test_config_and_short_input(self):
        for cfg in ({'max_risk':True},{'max_depth':float('nan')},{'other':1},{'min_pullback':.3}):
            with self.assertRaises(ValueError):base.settings(cfg)
        self.assertFalse(base.analyze(bars()[:100])['signals'])

if __name__=='__main__':unittest.main()
