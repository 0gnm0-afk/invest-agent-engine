import copy
from datetime import date, timedelta
import unittest
from invest_agent.trading import sperandeo as sp
from invest_agent.trading.market import screen, usable_tail


def fixture(stage='S2', equal=False):
    # A=0 high150, B=20 high130, L=50 low85, line=150-index.
    bars=[]
    for i in range(63):
        high=150-i-(0 if i in (0,20) else 4)
        low=high-2
        close=high-1
        if i==50: low=85
        if i>=51:
            high,low,close=89,87,88
        bars.append(dict(date=(date(2025,1,1)+timedelta(days=i)).isoformat(),open=close,high=high,low=low,close=close,volume=1_000_000))
    def put(i,low,high,close): bars[i].update(open=close,low=low,high=high,close=close)
    if stage=='S0':
        for i in range(51,63): put(i,85.5,86,85.7)
    if stage=='S1':
        for i in range(51,63): put(i,85.5,86,85.7)
        put(62,85.5,88,86.8)
    if stage in ('S2','S3','S4'):
        put(55,94,98,97)  # line95, first close breakout
        for i in range(56,63): put(i,94,98,96)
    if stage in ('S3','S4'):
        put(57,92,97,94)
        put(58,85 if equal else 90,95,93)
        put(59,92,96,94)
        put(60,93,97,95)
    if stage=='S4': put(61,97,101,100)
    return bars


def snapshot(bars, **changes):
    item=dict(market='US',symbol='SYNTH',currency='USD',benchmark='INDEX',price_basis='synthetic',bars=bars,
              raw_exchange_code='NMS',market_cap=2e9,market_cap_currency='USD',market_cap_as_of=bars[-1]['date'])
    item.update(changes)
    return dict(series=[item],universe=[],benchmarks={},profile={},errors=[],source='synthetic',fetched_at=bars[-1]['date'],
                sessions={'US':[b['date'] for b in bars]},coverage={'scope':'configured_universe','scope_errors':[]})


class SperandeoTests(unittest.TestCase):
    def test_legacy_positive_removed_even_with_universe_pass(self):
        from invest_agent.trading.market import evaluate
        closes=[100-i for i in range(15)]+[86,87,90,94,98]
        b=[dict(date=(date(2025,1,1)+timedelta(days=i)).isoformat(),open=c,high=c+1,low=c-1,close=c,volume=1_000_000) for i,c in enumerate(closes)]
        data=snapshot(b)
        rule={'ma_period':5,'slope_window':2,'transition_window':5,'rs_period':5,'activity_window':3,
              'min_rs':0,'min_activity_ratio':0,'min_average_value':0}
        benchmark={'bars':[dict(x,open=100,close=100,high=101,low=99) for x in b]}
        legacy=evaluate(data['series'][0],benchmark,rule,data['sessions']['US'])
        self.assertEqual(legacy['state'],'candidate')
        result=screen(data)['rows'][0]
        self.assertTrue(result['universe']['universe_pass'])
        self.assertEqual(result['state'],'not_selected')
        self.assertEqual(result['candidate_reasons'],[])
        self.assertNotIn('taver',result)
    def test_stages_and_coordinates(self):
        for stage in ('S0','S1','S2','S3','S4'):
            with self.subTest(stage=stage):
                result=sp.window(fixture(stage),63)
                self.assertEqual(result['stage'],stage)
                self.assertEqual(result['anchor_high_a_price'],150)
                self.assertEqual(result['anchor_high_b_price'],130)
                self.assertEqual(result['slope_per_bar'],-1)
                self.assertEqual(result['trendline_value_latest'],88)

    def test_equal_and_higher_retest(self):
        for equal in (True,False):
            w=sp.window(fixture('S3',equal),63)
            self.assertEqual(w['stage'],'S3')
            self.assertEqual(w['retest_type'],'equal_low_test' if equal else 'higher_low_test')
            self.assertGreater(w['retest_confirmed_at'],w['retest_pivot_date'])

    def test_lower_low_invalidates(self):
        b=fixture('S3'); b[-1].update(low=80)
        w=sp.window(b,63)
        self.assertTrue(w['invalidated'])
        self.assertEqual(w['invalidation_date'],b[-1]['date'])
        self.assertNotIn(w.get('stage'),('S3','S4'))

    def test_pivot_confirmation_and_completion_no_backdating(self):
        b=fixture('S4'); b[59].update(high=102,close=101,open=101)
        before=sp._structure(b[:60])
        self.assertEqual(before['stage'],'S2')
        b[60].update(high=103,close=102,open=102)
        after=sp._structure(b[:61])
        self.assertEqual(after['stage'],'S4')
        self.assertEqual(after['completion_date'],b[60]['date'])

    def test_no_line_not_s0(self):
        b=fixture(); b[19]['high']=140
        # Every prospective anchor is blocked by an intervening higher bar.
        for i in range(1,51): b[i]['high']=150-i/10
        w=sp.window(b,63)
        self.assertEqual(w['data_state'],'no_valid_trendline')
        self.assertIsNone(w['stage'])

    def test_windows_candidate_determinism_and_missing(self):
        b=fixture('S3'); a=sp.analyze(b)
        self.assertEqual(a,sp.analyze(copy.deepcopy(b)))
        self.assertTrue(a['candidate'])
        self.assertEqual(a['windows']['126']['data_state'],'unavailable_insufficient_bars')
        self.assertEqual(a['windows']['252']['data_state'],'unavailable_insufficient_bars')
        self.assertFalse(sp.analyze(fixture('S0'))['candidate'])
        # Earlier major highs change the longer windows, leaving 63 independent.
        prefix=[dict(x,date=(date(2024,6,1)+timedelta(days=i)).isoformat(),open=200,high=201,low=199,close=200) for i,x in enumerate((b*4)[:189])]
        many=sp.analyze(prefix+b)
        self.assertEqual(many['windows']['63']['stage'],'S3')
        self.assertTrue(any(v.get('stage')!='S3' for k,v in many['windows'].items() if k!='63'))

    def test_candidate_gate_legacy_and_taver_independence(self):
        b=fixture('S2'); data=snapshot(b)
        a=screen(data)['rows'][0]
        self.assertEqual(a['state'],'candidate')
        self.assertEqual(a['candidate_origin'],'sperandeo')
        self.assertIn('taver',a)
        self.assertFalse(a['taver']['near_target_ma'])
        self.assertEqual(a['candidate_reasons'],['sperandeo_63_S2'])
        data['profile']={'US':{'min_rs':1e100,'min_activity_ratio':1e100}}
        self.assertEqual(screen(data)['rows'][0]['state'],'candidate')
        data['series'][0]['market_cap']=1
        self.assertEqual(screen(data)['rows'][0]['state'],'not_selected')
        self.assertNotIn('sperandeo',screen(data)['rows'][0])
        flat=fixture('S0')
        r=screen(snapshot(flat))['rows'][0]
        self.assertEqual(r['state'],'not_selected')
        self.assertNotIn('taver',r)

    def test_completed_suffix_not_future_or_fill(self):
        b=fixture(); days=[v['date'] for v in b]
        future=dict(b[-1],date='2099-01-01',close=1000,high=1001,open=1000)
        self.assertEqual(usable_tail(b+[future],days),b)
        missing=copy.deepcopy(b); missing[-2]['close']=None
        self.assertEqual(len(usable_tail(missing,days)),1)

    def test_sort_recency_then_window_no_stage_score(self):
        def row(symbol,window,stage,day):
            return {'symbol':symbol,'sperandeo':{'windows':{window:{'stage':stage,'stage_entered_at':day}}}}
        values=[row('B','63','S4','2025-01-02'),row('A','126','S1','2025-01-02'),row('C','252','S2','2025-01-03')]
        self.assertEqual([r['symbol'] for r in sorted(values,key=sp.sort_key)],['C','A','B'])
