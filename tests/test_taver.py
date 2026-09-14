import copy
import unittest
from unittest.mock import patch
from invest_agent.trading import taver
from invest_agent.trading.structure_math import sma, atr
from test_sperandeo import fixture, snapshot
from invest_agent.trading.market import screen


class TaverTests(unittest.TestCase):
    def structure(self, stages=('S1','S2','S4')):
        return {'primary_window':126,'windows':{str(p):{'stage':s} for p,s in zip((63,126,252),stages)}}

    def bars(self):
        from datetime import date,timedelta
        return [dict(date=(date(2025,1,1)+timedelta(days=i)).isoformat(),open=100,close=100,high=101,low=99,volume=1e6) for i in range(260)]

    def test_profiles_missing_and_sma(self):
        b=self.bars()
        for market,periods,primary in [('US',[50,100,200],200),('KR',[60,120,240],240)]:
            r=taver.analyze(b,market,self.structure())
            self.assertEqual(r['ma_periods'],periods)
            self.assertEqual(r['primary_ma_period'],primary)
            self.assertEqual(r['lines'][str(primary)]['ma_value_latest'],100)
            self.assertEqual(r,taver.analyze(copy.deepcopy(b),market,self.structure()))
            self.assertEqual(sma(b,primary)[-1],r['lines'][str(primary)]['ma_value_latest'])
        self.assertEqual(taver.analyze(b,None,self.structure())['data_state'],'unavailable_market_profile')
        self.assertEqual(taver.analyze(b[:63],'US',self.structure())['lines']['200']['data_state'],'unavailable_insufficient_bars')

    def test_locations_thresholds_and_missing_atr(self):
        cases=[(103,102,104,2,'above_far'),(101.5,101.1,102,2,'approaching_from_above'),
               (101,99,102,2,'at_ma_zone'),(100.8,100.5,101,2,'at_ma_zone'),(98,97,99,2,'below_ma'),
               (103,102,104,None,'unavailable_atr_distance'),(101,99,102,None,'at_ma_zone')]
        for close,low,high,a,expected in cases:
            with self.subTest(expected=expected):self.assertEqual(taver.location(close,low,high,100,a),expected)

    def test_events_use_daily_ma_no_lookahead(self):
        b=self.bars(); b[-4].update(open=102,close=102,high=103,low=101)
        b[-3].update(open=98,close=98,high=99,low=97)
        before=taver.analyze(b[:-2],'US',self.structure())['lines']['200']
        after=taver.analyze(b,'US',self.structure())['lines']['200']
        self.assertEqual(before['last_reclaim_date'],b[-4]['date'])
        self.assertEqual(before['last_loss_date'],b[-3]['date'])
        self.assertEqual(after['last_reclaim_date'],before['last_reclaim_date'])
        self.assertEqual(after['last_loss_date'],before['last_loss_date'])
        self.assertEqual(after['last_touch_date'],b[-1]['date'])

    def test_relations_confluence_nearest_and_non_filter(self):
        b=self.bars()
        for stage,relation in [('S0','downtrend_intact'),('S1','downtrend_intact'),('S2','reversal_in_progress'),('S3','reversal_in_progress'),('S4','reversal_confirmed'),(None,'unknown')]:
            r=taver.analyze(b,'US',self.structure(('S4',stage,'S0')))
            self.assertEqual(r['primary_sperandeo_relation'],relation)
            self.assertTrue(r['near_target_ma'])
            self.assertEqual(r['sperandeo_relation']['63'],'reversal_confirmed')
            self.assertNotEqual(r['nearest_ma_period'],r['primary_ma_period'])
        data=snapshot(fixture('S2'))
        for near in (True,False):
            with patch.object(taver,'analyze',return_value={'near_target_ma':near}):
                self.assertEqual(screen(data)['rows'][0]['state'],'candidate')
                self.assertEqual(screen(snapshot(fixture('S0')))['rows'][0]['state'],'not_selected')

    def test_atr_missing_keeps_facts(self):
        b=self.bars(); b[-1].update(open=110,close=110,high=111,low=109)
        with patch.object(taver,'atr',return_value=[None]*len(b)):
            r=taver.analyze(b,'US',self.structure())
        self.assertIsNone(r['nearest_ma_period'])
        self.assertTrue(r['lines']['50']['close_above_ma'])
        self.assertEqual(r['lines']['50']['location_state'],'unavailable_atr_distance')
