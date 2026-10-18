import unittest
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import numpy as np
import pandas as pd
from ma_warning_research import indicators,detect_events,forward_extreme,SIGNALS
from ma_warning_research import signal_summary,matching_covariates,control_bank,match_indices,holm


class WarningCoreTests(unittest.TestCase):
    def test_price_prefix_indicators_and_signals_do_not_see_future(self):
        rng=np.random.default_rng(9); close=100*np.exp(np.cumsum(rng.normal(0,.02,400)))
        bars=[dict(date=str(np.datetime64('2010-01-01')+np.timedelta64(i,'D')),open=x,high=x*1.01,low=x*.99,close=x,volume=1000) for i,x in enumerate(close)]
        a,_=indicators(bars)
        for b in bars[300:]: b['close']*=2
        b,_=indicators(bars)
        for key in ['ma5','ma10','ma20','slope5','slope10','slope20','vol','state','onset']:
            np.testing.assert_allclose(a[key][:300],b[key][:300],equal_nan=True)

    def test_same_uninterrupted_fall_has_one_event(self):
        c=np.r_[np.linspace(90,100,220),np.linspace(99,30,300)]
        events=detect_events(c)
        self.assertEqual(len(events),1); self.assertEqual(events[0]['peak_index'],219)
        self.assertEqual(events[0]['reset'],'right_censored')

    def test_peak_recovery_resets_and_false_alarms_are_retained(self):
        c=np.r_[np.linspace(90,100,220),99,101,100,102,np.full(80,102)]
        es=detect_events(c)
        self.assertEqual(es[0]['peak'],100); self.assertEqual(es[0]['reset'],'recovered')
        self.assertEqual(es[1]['peak'],101); self.assertEqual(es[1]['reset'],'recovered')
        self.assertEqual(es[0]['end_index'],221)

    def test_lower_local_record_can_rearm_below_old_peak(self):
        c=np.r_[np.linspace(90,100,220),np.linspace(99,60,70),np.linspace(61,85,65),84,83,82]
        es=detect_events(c)
        self.assertTrue(any(e['reset']=='lower_local_record' for e in es))
        self.assertTrue(es[-1]['peak']<100)
        self.assertEqual(es[0]['parent'],es[-1]['parent'])

    def test_events_are_online_prefix_invariant(self):
        c=np.r_[np.linspace(90,100,220),99,98,101,99,98,102,np.linspace(101,60,150)]
        full=detect_events(c); short=detect_events(c[:300])
        self.assertEqual([e for e in full if e['end_index']<300],[e for e in short if e['reset']!='right_censored'])

    def test_confirmation_onset_not_daily_persistence(self):
        bars=[dict(date=str(np.datetime64('2010-01-01')+np.timedelta64(i,'D')),open=x,close=x,high=x+1,low=x-1,volume=1) for i,x in enumerate(np.r_[np.linspace(50,100,220),np.linspace(99,70,40)])]
        f,_=indicators(bars); one=SIGNALS.index(('close_below_ma5',1)); two=one+1
        a=np.flatnonzero(f['onset'][220:,one]); b=np.flatnonzero(f['onset'][220:,two])
        self.assertEqual(len(a),1); self.assertEqual(len(b),1); self.assertEqual(b[0],a[0]+1)
        self.assertGreater(f['state'][220:,one].sum(),f['onset'][220:,one].sum())

    def test_forward_extremes_exclude_signal_day(self):
        x=np.array([999,1,2,3,4,5],float)
        a=forward_extreme(x,3); b=forward_extreme(x,3,'min')
        self.assertEqual(a[0],3); self.assertEqual(b[0],1)
        self.assertEqual(a[1],4); self.assertTrue(np.isnan(a[-1]))

    def test_already_hit_targets_and_gapped_execution_are_not_warning_success(self):
        a={'event':np.arange(4),'first':np.ones(4,dtype=np.uint32),'offset':np.array([1,2,1,9]),
           'symbol':np.arange(4),'quarter':np.full(4,84),'dd':np.array([.03,.10,.02,.09]),
           'exec_dd':np.array([.05,.12,.02,.25]),'delay10':np.array([4,-2,-1,1]),
           'recover60':np.array([False,False,True,False]),'rebound60':np.ones(4,bool),
           'up60':np.full(4,.1),'down60':np.full(4,-.1),'exec_up60':np.full(4,.1),
           'below_ma200':np.ones(4,bool),'long_gap':np.zeros(4,bool)}
        e=pd.DataFrame({'event':np.arange(4),'target10_offset':[5,2,-1,10],'symbol_id':np.arange(4),'peak_date':['2021-01-01']*4})
        r,idx=signal_summary(a,e,0,10,with_ci=False)
        self.assertEqual(r['n'],3); self.assertNotIn(1,idx)
        self.assertAlmostEqual(r['precision'],2/3); self.assertAlmostEqual(r['recall'],2/3)
        self.assertAlmostEqual(r['executable_recall'],1/3)
        self.assertEqual(r['target_events'],3)

    def test_matched_control_choice_is_independent_of_future_outcomes(self):
        n=50; a={'first':np.r_[np.uint32(1),np.zeros(n-1,dtype=np.uint32)],'state':np.r_[np.uint32(1),np.zeros(n-1,dtype=np.uint32)],
                 'offset':np.ones(n,int),'symbol':np.arange(n)%20,'event':np.arange(n),
                 'quarter':np.full(n,84),'regime':np.ones(n,int),'vol':np.full(n,.02),
                 'dd':np.full(n,.03),'market_ret20':np.full(n,.01),'date':np.arange(n)+18000,
                 'delay10':np.arange(n),'recover60':np.zeros(n,bool)}
        keys=matching_covariates(a,np.array([.01,.015,.025,.03])); eligible=np.ones(n,bool)
        bank=control_bank(a,keys,eligible); t,c=match_indices(a,0,eligible,keys,bank)
        a['delay10']=np.random.default_rng(1).permutation(a['delay10']); a['recover60'][:]=True
        t2,c2=match_indices(a,0,eligible,keys,bank)
        np.testing.assert_array_equal(t,t2); np.testing.assert_array_equal(c,c2)
        self.assertTrue(np.all(a['symbol'][c[0,c[0]>=0]]!=a['symbol'][0]))
        self.assertEqual(len(np.unique(a['event'][c[0,c[0]>=0]])),5)

    def test_holm_is_familywise_and_monotonic(self):
        self.assertEqual(holm([.01,.04]),[.02,.04])


if __name__=='__main__': unittest.main()
