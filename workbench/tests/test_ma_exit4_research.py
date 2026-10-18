import unittest
import numpy as np
import pandas as pd
import tempfile
from pathlib import Path
from ma_exit4_research import FEATURES, timing, cycle_metrics, METRICS, features, ratio_candidates, holm
from ma_exit4_research import write_json, read_json


def fixture(n=2,T=186):
    d={k:np.full((n,T),100.,dtype=float) for k in FEATURES}
    for k,x in [('m5',101),('m10',102),('m20',103),('s5',-1),('s5prev',-1),('s20',-1),('lo5',90),('lo10',90),('hi5',110),('h',105),('l',95),('v',1000)]: d[k][:]=x
    m=pd.DataFrame({'market':['US']*n,'symbol':[f'S{i}' for i in range(n)]})
    p={'horizon':126,'base_cost_bps':{'US':{'buy':10,'sell':10}},'coarse_weights':[[25]*4]}
    return d,m,p


class ExitTests(unittest.TestCase):
    def test_original_position_weights_and_four_slots(self):
        d,m,p=fixture(); sells,_,_=timing(d,('classic','none',1))
        np.testing.assert_array_equal(sells[0],[1,2,3,4])
        a=cycle_metrics(d,m,sells,[10,20,30,40],p)
        self.assertAlmostEqual(a[0,METRICS.index('sold')],1)
        self.assertEqual(a[0,METRICS.index('sales')],4)

    def test_no_future_information_in_signals(self):
        d,m,p=fixture(); d['m5'][:,:100]=105; d['m10'][:]=100; d['m20'][:]=110
        first=timing(d,('classic','early5',1))[0]
        for k in d: d[k][:,80:]*=2
        second=timing(d,('classic','early5',1))[0]
        np.testing.assert_array_equal(np.where(first<80,first,-1),np.where(second<80,second,-1))

    def test_indicators_are_causal(self):
        bars=[{'date':f'2020-{1+i//28:02d}-{1+i%28:02d}','open':100+i,'close':100+i,'high':101+i,'low':99+i,'volume':1000} for i in range(100)]
        f,_=features(bars)
        for b in bars[80:]: b['close']+=50
        g,_=features(bars)
        for k in FEATURES: np.testing.assert_allclose(f[k][:80],g[k][:80],equal_nan=True)

    def test_hold_then_breakdown_resumes_before_golden_cross(self):
        d,m,p=fixture(); d['m20'][:]=90; d['s20'][:]=1; d['lo5'][:]=98
        d['m5'][:,1:]=99; d['s5'][:,1:]=1; d['c'][:,4:]=95
        sells,holds,resumes=timing(d,('classic','early5',1))
        self.assertEqual(sells[0,0],1); self.assertEqual(sells[0,1],5)
        self.assertGreater(holds[0],0); self.assertGreater(resumes[0],0)
        self.assertTrue((d['m5']<d['m10']).all())

    def test_unfilled_holdings_are_marked_not_forced_sold(self):
        d,m,p=fixture(); sells=np.full((2,4),-1)
        a=cycle_metrics(d,m,sells,[25]*4,p)
        self.assertEqual(a[0,METRICS.index('sold')],0)
        self.assertAlmostEqual(a[0,0],100/100.1-1)
        self.assertAlmostEqual(a[0,METRICS.index('hold_delta')],0)

    def test_zero_volume_and_locked_bar_delays_execution(self):
        d,m,p=fixture(); d['v'][:,1]=0; d['h'][:,2]=d['l'][:,2]
        sells,_,_=timing(d,('classic','none',1))
        self.assertEqual(sells[0,0],3)
        self.assertTrue(np.all(np.diff(sells[0])>0))

    def test_two_close_confirmation_and_late_fill(self):
        d,m,p=fixture()
        self.assertEqual(timing(d,('classic','none',2))[0][0,0],2)
        self.assertEqual(timing(d,('classic','none',1),lag=2)[0][0,0],2)

    def test_gap_fill_cost_and_contribution_identity(self):
        d,m,p=fixture(); sells=np.tile([1,2,3,4],(2,1)); d['o'][:,1:5]=80
        a,contrib,wealth=cycle_metrics(d,m,sells,[25]*4,p,trajectory=True)
        self.assertAlmostEqual(a[0,0],80*.999/100.1-1)
        self.assertAlmostEqual(contrib[0].sum(),a[0,METRICS.index('hold_delta')])
        self.assertAlmostEqual(a[0,METRICS.index('mdd')],1-80*.999/100.1)

    def test_grid_positive_sums_100_and_holm_monotone(self):
        _,_,p=fixture(); ratios=ratio_candidates(p,True)
        self.assertEqual(len(ratios),85)
        self.assertTrue(all(sum(w)==100 and min(w)>0 for w in ratios))
        self.assertEqual(holm([.01,.04,.9]),[.03,.08,.9])

    def test_missing_or_out_of_order_conditions_never_skip_stage(self):
        d,m,p=fixture(); d['m5'][:]=105; d['m10'][:]=100; d['m20'][:]=110
        d['m5'][:,20:]=95
        sells,_,_=timing(d,('classic','none',1))
        np.testing.assert_array_equal(sells[0],[1,21,22,23])

    def test_missing_statistics_are_null_not_zero(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'x.json'; write_json(path,{'success':float('nan'),'return':np.float64(.1)})
            self.assertEqual(read_json(path),{'success':None,'return':.1})


if __name__=='__main__': unittest.main()
