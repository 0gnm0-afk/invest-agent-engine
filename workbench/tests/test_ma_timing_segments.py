import unittest,sys,math
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from ma_timing_segments import flags,transactions,segments,ledger,PAIRS,BUYS,SELLS
from test_ma_reentry import fixture


def scalar_flags(z):
    out={k:np.zeros(len(z['close']),bool) for k in BUYS+SELLS};last=None;start=0
    def gt(a,b):return a-b>1e-12*max(abs(a),abs(b))
    for t,c in enumerate(z['close']):
        if t==0 or z['segment'][t]!=z['segment'][t-1]:last=None;start=t
        ma={k:math.fsum(z['close'][t-k+1:t+1])/k if t-start>=k-1 else math.nan for k in (5,10,20)}
        vals={'C5':(c,ma[5]),'G5_10':(ma[5],ma[10]),'G10_20':(ma[10],ma[20]),'D5_20':(ma[20],ma[5]),'D10_20':(ma[20],ma[10])}
        state=gt(ma[20],ma[5]) and gt(ma[20],ma[10])
        if last:
            old,oldstate=last
            for k,(a,b) in vals.items():
                pa,pb=old[k];out[k][t]=math.isfinite(pa) and math.isfinite(pb) and gt(a,b) and not gt(pa,pb)
            out['DA'][t]=math.isfinite(old['D10_20'][0]) and state and not oldstate
        last=vals,state
    return out


def scalar_ledger(z,f,pair,fraction,sc,bc,initial_held=False,start='2016-01-01'):
    buy,sell=pair.split('__');begin=None;pending=None;held=initial_held;events=[]
    q=0.;cash=0.;path=np.full(len(z['close']),np.nan)
    def canfill(t):return t+1<len(z['close']) and z['pos'][t+1]==z['pos'][t]+1 and z['segment'][t+1]==z['segment'][t]
    for t in range(len(z['close'])):
        if begin is None:
            if t and z['eligible'][t-1] and z['date'][t-1]>=start and canfill(t-1):
                begin=t;cash=0. if initial_held else fraction;q=(1-cash)/z['open'][t]
                if f[sell if held else buy][t-1]:pending=('sell' if held else 'buy',t-1)
            else:continue
        if pending:
            kind,sig=pending;pending=None
            if kind=='sell':cash+=q*fraction*z['open'][t]*(1-sc);q*=1-fraction;held=False
            else:q+=cash/(z['open'][t]*(1+bc));cash=0.;held=True
            events.append(dict(kind=kind,signal=sig,fill=t))
        path[t]=q*z['close'][t]+cash
        if f[sell if held else buy][t] and canfill(t):pending=('sell' if held else 'buy',t)
    return events,begin,path


class TimingTests(unittest.TestCase):
    def fixture(self):
        z=fixture(np.exp(np.cumsum(np.random.default_rng(18).normal(0,.03,500)))*100)
        return z,flags(z)

    def test_all_nine_flags_and_cash_paths_independent(self):
        z,f=self.fixture();ind=scalar_flags(z)
        for k in f:np.testing.assert_array_equal(f[k],ind[k])
        for pair in PAIRS:
            for fraction in (1.,.5):
                for initial in (True,False):
                    ev,b,_=transactions(z,f,pair,initial_held=initial)
                    ee,bb,pp=scalar_ledger(z,ind,pair,fraction,.0035,.0005,initial)
                    self.assertEqual(ev,ee);self.assertEqual(b,bb)
                    w=ledger(z,ev,b,fraction,.0035,.0005,initial_held=initial)
                    np.testing.assert_allclose(w['path'],pp,equal_nan=True,atol=1e-12)

    def test_segment_partition_reconstructs_underlying_and_full_timing(self):
        z,f=self.fixture()
        for pair in PAIRS:
            ev,b,_=transactions(z,f,pair);rows=segments(z,ev,b,'KR','test',pair,{},[.2,.3,.4])
            self.assertTrue(rows[0]['initial']);self.assertFalse(rows[-1]['completed'])
            np.testing.assert_allclose(np.prod([1+r['price_return'] for r in rows]),z['close'][-1]/z['open'][b])
            w=ledger(z,ev,b,1.)
            np.testing.assert_allclose(np.prod([1+r['price_return'] for r in rows if r['phase']=='hold']),w['path'][-1])
            for a,c in zip(rows,rows[1:]):self.assertEqual(a['end_i'],c['start_i']);self.assertNotEqual(a['phase'],c['phase'])

    def test_initial_avoidance_with_no_buy_is_retained(self):
        z=fixture();f={k:np.zeros(10,bool) for k in BUYS+SELLS};ev,b,_=transactions(z,f,PAIRS[0]);self.assertEqual(ev,[])
        r=segments(z,ev,b,'KR','x',PAIRS[0],{},[.2,.3,.4]);self.assertEqual(len(r),1);self.assertTrue(r[0]['initial']);self.assertFalse(r[0]['completed'])
        self.assertTrue(np.all(ledger(z,ev,b,1.)['path'][b:]==1.))

    def test_simultaneous_signal_follows_actual_state(self):
        z=fixture();f={k:np.zeros(10,bool) for k in BUYS+SELLS}
        f['C5'][[1,3,5]]=True;f['D5_20'][[1,3,5]]=True
        ev,b,a=transactions(z,f,'C5__D5_20');self.assertEqual([e['kind'] for e in ev],['buy','sell','buy']);self.assertEqual(a['simultaneous'],3)

    def test_cap_loss_does_not_change_selected_stock_timing(self):
        z=fixture();z['eligible'][2:]=False;f={k:np.zeros(10,bool) for k in BUYS+SELLS}
        f['C5'][[2,6]]=True;f['D5_20'][4]=True
        ev,b,_=transactions(z,f,'C5__D5_20');self.assertEqual(len(ev),3)
        rows=segments(z,ev,b,'KR','x','C5__D5_20',{},[.2,.3,.4]);self.assertTrue(all(not r['eligible'] for r in rows))

    def test_missing_bar_does_not_force_phase_transition(self):
        z=fixture();z['pos'][4:]+=1;z['segment'][4:]=1
        f={k:np.zeros(10,bool) for k in BUYS+SELLS};f['C5'][[3,6]]=True
        ev,b,a=transactions(z,f,'C5__D5_20');self.assertEqual(ev[0]['fill'],7);self.assertEqual(a['unfilled'],1)

    def test_prefix_causality(self):
        z,f=self.fixture();pair='G10_20__DA';ev,b,_=transactions(z,f,pair);whole=ledger(z,ev,b,.5,.0035,.0005)['path']
        for n in [50,100,250]:
            zz={k:v[:n] for k,v in z.items()};ff=flags(zz);ee,bb,_=transactions(zz,ff,pair)
            np.testing.assert_allclose(ledger(zz,ee,bb,.5,.0035,.0005)['path'],whole[:n],equal_nan=True)

    def test_full_sell_cost_product_identity(self):
        z,f=self.fixture();ev,b,_=transactions(z,f,PAIRS[0]);gross=ledger(z,ev,b,1.)
        net=ledger(z,ev,b,1.,.0035,.0005)
        self.assertAlmostEqual(net['path'][-1],gross['path'][-1]*(1-.0035)**net['sells']/(1+.0005)**net['buys'])

if __name__=='__main__':unittest.main()
