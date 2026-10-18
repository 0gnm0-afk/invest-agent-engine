import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import numpy as np,pandas as pd
from ma_dd_describe import features,excursions,historical_us_caps,segment_indices

def bars(c):
 c=np.asarray(c,float);return pd.DataFrame({'date':pd.date_range('2015-01-01',periods=len(c),freq='B'),'close':c,'open':c,'high':c*1.01,'low':c*.99,'volume':100})

class DrawdownTests(unittest.TestCase):
 def test_crosses_are_onsets_not_below_days(self):
  g=bars(np.r_[np.linspace(80,120,40),np.linspace(119,80,40)]);f=features(g)
  self.assertEqual(f['cross'][:,0].sum(),1)
  self.assertEqual(f['cross'][:,1].sum(),1)
  self.assertEqual(f['cross'][:,2].sum(),1)
 def test_signal_day_prices_do_not_enter_prior_volatility(self):
  g=bars(np.r_[np.linspace(80,120,80),100]);a=features(g);g.loc[80,['high','low','close']]=[300,20,40];b=features(g)
  self.assertEqual(a['sigma'][80],b['sigma'][80]);self.assertEqual(a['atrpct'][80],b['atrpct'][80])
 def test_wilder_seed_and_prior_day_ratio(self):
  g=bars(np.full(40,100));f=features(g)
  self.assertAlmostEqual(f['atr'][14],2);self.assertAlmostEqual(f['atrpct'][20],.02)
 def test_prefix_future_changes_do_not_move_peak_or_event(self):
  g=bars(np.r_[np.linspace(80,120,40),np.linspace(119,90,30),np.linspace(91,130,50)])
  full=excursions(g,features(g));prefix=g.iloc[:75];early=excursions(prefix,features(prefix))
  self.assertEqual([r for r in full if r['t']<75],early)
 def test_repeat_crosses_are_flagged_inside_same_decline(self):
  g=bars(np.r_[np.linspace(80,120,40),119,118,117,116,115,114,113]);f=features(g);f['cross'][:]=False
  f['cross'][[42,44,46],0]=True
  rows=[r for r in excursions(g,f) if r['signal']==0]
  self.assertEqual([r['repeat'] for r in rows],[0,1,1]);self.assertEqual(len({r['leg'] for r in rows}),1)
 def test_rebound_can_start_new_local_leg_below_remote_high(self):
  g=bars(np.r_[np.linspace(80,120,40),np.linspace(119,80,20),np.linspace(81,90,10),89,88]);f=features(g);f['cross'][:]=False
  f['cross'][45,0]=True;f['cross'][-1,0]=True
  rows=excursions(g,f);self.assertEqual(len(rows),2);self.assertNotEqual(rows[0]['leg'],rows[1]['leg']);self.assertLess(rows[1]['peak'],rows[0]['peak'])
 def test_split_normalized_historical_cap_and_no_current_shares_backfill(self):
  ch={'events':{'splits':{'x':{'date':1577923200,'numerator':4,'denominator':1}}}}
  facts={'units':{'shares':[{'end':'2019-11-01','filed':'2019-11-10','val':100},{'end':'2020-01-03','filed':'2020-01-10','val':500}]}}
  ds=np.array(['2019-11-10','2019-11-11','2020-01-10','2020-01-11'])
  cap,_,age,_,_=historical_us_caps(ch,facts,ds,np.full(4,25.))
  self.assertTrue(np.isnan(cap[0]));self.assertEqual(cap[1],10000);self.assertEqual(cap[2],10000);self.assertEqual(cap[3],12500)
 def test_stale_shares_not_carried_forward_forever(self):
  facts={'units':{'shares':[{'end':'2018-01-01','filed':'2018-01-10','val':100}]}}
  cap,*_=historical_us_caps({},facts,np.array(['2020-01-01']),np.array([100.]));self.assertTrue(np.isnan(cap[0]))
 def test_uniform_future_split_adjustment_does_not_change_statistics(self):
  g=bars(np.r_[np.linspace(80,120,40),np.linspace(119,90,30),np.linspace(91,130,50)]);f=features(g)
  scaled=g.copy();scaled[['open','high','low','close']]*=.02;sf=features(scaled)
  np.testing.assert_array_equal(f['cross'],sf['cross']);np.testing.assert_allclose(f['atrpct'],sf['atrpct'],equal_nan=True)
  a=excursions(g,f);b=excursions(scaled,sf);self.assertEqual([(x['t'],x['leg']) for x in a],[(x['t'],x['leg']) for x in b])
  np.testing.assert_allclose([x['dd'] for x in a],[x['dd'] for x in b],atol=1e-10)
 def test_unconditional_cross_is_retained_without_sigma_history(self):
  g=bars([100,102,104,106,108,107,106,105,104,103]);f=features(g);r=excursions(g,f)
  self.assertTrue(any(np.isnan(f['sigma'][x['t']]) for x in r));self.assertTrue(any(x['signal']==0 for x in r))
 def test_missing_sessions_and_untradeable_bars_split_segments(self):
  g=bars(np.full(30,100));cal=pd.DatetimeIndex(g.date);g=g.drop(index=10).reset_index(drop=True);g.loc[20,'volume']=0
  seg,qa=segment_indices(g,cal);self.assertEqual(len(seg),3);self.assertEqual(qa['invalid_bars'],1)

if __name__=='__main__':unittest.main()
