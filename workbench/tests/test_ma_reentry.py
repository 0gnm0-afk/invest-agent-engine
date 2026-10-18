import unittest, sys
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from ma_reentry_research import signal_arrays, cycles, wealth, cycle_rows, progression, fill_index, mdd, trading_segments
from ma_reentry_statistics import km, crossed_bootstrap
from verify_ma_reentry import independent_signals, scalar_replay


def fixture(prices=None):
    c = np.array(prices if prices is not None else [100, 100, 90, 80, 90, 100, 110, 100, 100, 100], float)
    n = len(c); dates = pd.bdate_range('2020-01-01', periods=n)
    z = dict(close=c, open=c.copy(), high=c * 1.01, low=c * .99, date=dates.strftime('%Y-%m-%d').to_numpy(),
             day=dates.to_numpy('datetime64[D]').astype('int64'), pos=np.arange(n), segment=np.zeros(n, int),
             eligible=np.ones(n, bool), sigma=np.full(n, .3), cap=np.full(n, 3e9), rank=np.ones(n), tr_factor=np.ones(n))
    for k in ['A', 'B', 'sellA', 'sellB', 'C', 'G', 'E']: z[k] = np.zeros(n, bool)
    return z


class ResearchTests(unittest.TestCase):
    def test_formula_matches_independent_cash_ledger(self):
        z=fixture(); cs=[dict(t=0,s=1,r=2,b=3)]
        w=wealth(z,cs,1,.0035,.0005)
        q=.5/100 + (.5*(1-.0035))/(80*(1+.0005))
        self.assertAlmostEqual(w['final'],q*100)
        self.assertAlmostEqual(w['path'][2],.5/100*90+.5*(1-.0035))
        row=cycle_rows(z,cs,'KR','x','AC',{},[.2,.4,.6])[0]
        self.assertAlmostEqual(row['net_share'],q*100-1)
        self.assertAlmostEqual(row['net_pnl'],(q*80-.8))

    def test_half_current_holdings_compound(self):
        z=fixture([100,100,80,80,100,100,80,80,100,100])
        cs=[dict(t=0,s=1,r=2,b=3),dict(t=4,s=5,r=6,b=7)]
        w=wealth(z,cs,1)
        self.assertAlmostEqual(w['final'],1.125**2)

    def test_open_cycle_mark_cash_not_forced_buy(self):
        z=fixture();w=wealth(z,[dict(t=0,s=1,r=None,b=None)],1)
        self.assertEqual(w['cash'],.5); self.assertAlmostEqual(w['path'][3],.9)

    def test_same_day_rebound_is_ignored(self):
        z=fixture();z['sellA'][1]=True;z['C'][1]=True;z['C'][3]=True
        cs,_,_=cycles(z,'AC');self.assertEqual(cs,[dict(t=1,s=2,r=3,b=4)])

    def test_ignored_sales_do_not_reduce_remaining_half(self):
        z=fixture();z['sellA'][[1,2,3,6]]=True;z['C'][[4,8]]=True
        cs,_,a=cycles(z,'AC');self.assertEqual([x['t'] for x in cs],[1,6]);self.assertEqual(a['ignored_sales'],2)

    def test_new_sell_on_rebuy_close_allowed(self):
        z=fixture();z['sellA'][[1,5]]=True;z['C'][[4,8]]=True
        cs,_,_=cycles(z,'AC');self.assertEqual(cs[1]['t'],cs[0]['b'])

    def test_ongoing_state_is_not_new_entry(self):
        c=np.r_[np.arange(1,31),np.arange(29,0,-1)]
        f=signal_arrays(c);self.assertEqual(f['sellA'].sum(),1);self.assertEqual(f['sellB'].sum(),1)

    def test_signals_prefix_invariant(self):
        c=np.random.default_rng(12).lognormal(4,.1,200)
        whole=signal_arrays(c)
        for n in [25,40,100]:
            for k,v in signal_arrays(c[:n]).items(): np.testing.assert_array_equal(v,whole[k][:n])

    def test_equality_no_false_transition(self):
        for v in signal_arrays(np.ones(80)*.1).values(): self.assertFalse(v.any())

    def test_gap_not_filled_and_open_cash_survives(self):
        z=fixture();z['pos'][4:]+=2;z['segment'][4:]=1
        self.assertIsNone(fill_index(z,3))
        z['sellA'][1]=True;z['C'][3]=True;z['C'][6]=True
        cs,_,a=cycles(z,'AC');self.assertEqual(cs[0]['b'],7);self.assertEqual(a['unfilled_buys'],1)
        self.assertEqual(cycle_rows(z,cs,'KR','x','AC',{},[.2,.4,.6])[0]['gap_spanning'],True)

    def test_unfilled_terminal_rebound(self):
        z=fixture();z['sellA'][1]=True;z['C'][-1]=True
        cs,_,_=cycles(z,'AC');self.assertIsNone(cs[0]['b']);self.assertEqual(cs[0]['r'],9)

    def test_buy_day_intraday_low_not_future_excursion(self):
        z=fixture();z['low'][4]=1
        r=cycle_rows(z,[dict(t=0,s=1,r=3,b=4)],'KR','x','AC',{},[.2,.4,.6])[0]
        self.assertLess(r['trade_low_dd'],.3)

    def test_no_future_membership(self):
        z=fixture();z['eligible'][:5]=False;z['sellA'][[1,6]]=True;z['C'][8]=True
        cs,begin,_=cycles(z,'AC');self.assertEqual(begin,6);self.assertEqual(cs[0]['t'],6)

    def test_earlier_rebound_union(self):
        z=fixture();z['sellB'][1]=True;z['C'][6]=True;z['G'][3]=True;z['E']=z['C']|z['G']
        self.assertEqual(cycles(z,'BE')[0][0]['b'],4)

    def test_interest_and_income_are_separate(self):
        z=fixture();cs=[dict(t=0,s=1,r=5,b=6)]
        w=wealth(z,cs,1);self.assertGreater(wealth(z,cs,1,interest=.04)['final'],w['final'])
        z['tr_factor']=np.linspace(1,2,10)
        self.assertGreater(wealth(z,cs,1,income=True,market='US')['bh'][-1],w['bh'][-1])

    def test_A_B_episode_not_future_condition(self):
        z=fixture();z['sellA'][1]=True;z['A'][1:5]=True;z['B'][3:5]=True
        p=progression(z,'KR','x')[0];self.assertEqual(p['status'],'later_B');self.assertEqual(p['sessions'],2)

    def test_drawdown_includes_endowed_capital(self):
        self.assertAlmostEqual(mdd([.9,.8,1.1,1]),.2)

    def test_independent_random_paths_all_strategies(self):
        z=fixture(np.exp(np.cumsum(np.random.default_rng(42).normal(0,.03,600)))*100)
        z.update(signal_arrays(z['close']))
        flags=independent_signals(z)
        for k in flags: np.testing.assert_array_equal(z[k],flags[k])
        for strategy in ['AC','AG','AE','BC','BG','BE']:
            cs,b,_=cycles(z,strategy);expected,bb,v=scalar_replay(z,flags,strategy,'KR')
            self.assertEqual(cs,expected);self.assertEqual(b,bb)
            np.testing.assert_allclose(wealth(z,cs,b,.0035,.0005)['path'],v,equal_nan=True)

    def test_right_censoring_not_failure(self):
        g=pd.DataFrame(dict(wait_signal=[2,4,6],completed=[True,False,True]))
        r=km(g);self.assertEqual(r['median'],6);self.assertAlmostEqual(r['survival_5'],2/3)

    def test_cluster_constant_invariant(self):
        g=pd.DataFrame(dict(symbol=['a','a','b','b'],quarter=['q1','q2','q1','q2']))
        r=crossed_bootstrap(g,np.ones(4)*.03,reps=100)
        np.testing.assert_allclose(r['ci95'],[.03,.03])

    def test_long_holiday_is_not_missing_session(self):
        dates=pd.to_datetime(['2017-09-28','2017-09-29','2017-10-10','2017-10-11'])
        g=pd.DataFrame(dict(date=dates,open=100.,close=100.,high=101.,low=99.,volume=100.))
        seg,qa=trading_segments(g,pd.DatetimeIndex(dates))
        self.assertEqual(len(seg),1);self.assertEqual(qa['calendar_only_breaks_merged'],1)
        g.loc[2,'volume']=0
        seg,_=trading_segments(g,pd.DatetimeIndex(dates));self.assertEqual(len(seg),2)

if __name__=='__main__': unittest.main()
