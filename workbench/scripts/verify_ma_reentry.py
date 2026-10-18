"""Independent scalar SMA/cycle/capital replay plus complete ledger checks."""
import argparse, math, re, sys
from pathlib import Path
from ma_reentry_research import (ROOT, SOURCE, BASE, STRATEGIES, read, write, sha, load_arrays,
                                 prepare_kr, us_stock, trading_segments)
import numpy as np
import pandas as pd


def independent_signals(z):
    c=z['close']; n=len(c); flags={k:np.zeros(n,bool) for k in ('sellA','sellB','C','G','E','A','B')}
    previous=None; start=0
    def below(a,b): return a-b < -1e-12*max(abs(a),abs(b))
    for t in range(n):
        if t==0 or z['segment'][t]!=z['segment'][t-1]: start=t;previous=None
        ma={k:math.fsum(c[t-k+1:t+1])/k if t-start>=k-1 else math.nan for k in (5,10,20)}
        A=below(ma[5],ma[20]) and below(ma[10],ma[20]);B=below(ma[5],ma[10]) and below(ma[10],ma[20])
        flags['A'][t]=A;flags['B'][t]=B
        if previous is not None:
            pm,pA,pB,pc=previous
            if math.isfinite(pm[20]) and math.isfinite(ma[20]):
                flags['sellA'][t]=A and not pA;flags['sellB'][t]=B and not pB
            flags['C'][t]=math.isfinite(pm[5]) and below(ma[5],c[t]) and not below(pm[5],pc)
            flags['G'][t]=math.isfinite(pm[10]) and below(ma[10],ma[5]) and not below(pm[10],pm[5])
            flags['E'][t]=flags['C'][t] or flags['G'][t]
        previous=(ma,A,B,c[t])
    return flags


def scalar_replay(z, flags, strategy, market, start='2016-01-01'):
    """Day loop processes opening trades BEFORE closing signals; no production cycle helper."""
    n=len(z['close']);begin=None;expect=[];q=0.;cash=0.;pending=None;active=None
    values=np.full(n,np.nan);sc,bc=BASE[market]
    def next_ok(t): return t+1<n and z['pos'][t+1]==z['pos'][t]+1 and z['segment'][t+1]==z['segment'][t]
    for t in range(n):
        if begin is None:
            if t>0 and z['eligible'][t-1] and z['date'][t-1]>=start and next_ok(t-1):
                begin=t;q=1/z['open'][t]
                # The initially endowed holding can execute the preceding close's sell signal.
                if flags['sell'+strategy[0]][t-1]: pending=('sell',t-1)
            else: continue
        if pending:
            kind,sig=pending;pending=None
            if kind=='sell':
                cash=q*.5*z['open'][t]*(1-sc);q*=.5
                active=dict(t=sig,s=t,r=None,b=None)
            else:
                q+=cash/(z['open'][t]*(1+bc));cash=0.
                active.update(r=sig,b=t);expect.append(active);active=None
        values[t]=q*z['close'][t]+cash
        if active is not None:
            if flags[strategy[1]][t] and t>active['t']:
                active['r']=t
                if next_ok(t): pending=('buy',t)
        elif z['eligible'][t] and flags['sell'+strategy[0]][t] and next_ok(t): pending=('sell',t)
    if active is not None: expect.append(active)
    return expect,begin,values


def verify(out, report=None):
    meta=read(out/'prepared.json');assert meta['protocol_sha256']==sha(out/'protocol.json')==sha(ROOT/'scripts/ma_reentry_protocol.json')
    array_paths={(r['market'],r['symbol']):r['path'] for r in meta['stocks']}
    hashes=0
    for x in meta['input_hashes']: assert sha(x['path'])==x['sha256'];hashes+=1
    for x in meta['stocks']: assert sha(x['path'])==x['sha256']
    g=pd.read_csv(out/'cycles.csv.gz',dtype={'symbol':str});p=pd.read_csv(out/'paths.csv.gz',dtype={'symbol':str})
    complete=g[g.completed]
    assert not g.duplicated(['market','symbol','strategy','cycle']).any()
    assert (g.signal_date<g.sell_date).all()
    assert (complete.rebound_date>complete.signal_date).all()
    assert (complete.buy_date>complete.rebound_date).all()
    assert (complete.rebound_date>=complete.sell_date).all()
    np.testing.assert_allclose(complete.gross_share,.5*(complete.sell/complete.buy-1),atol=1e-12)
    for market in ('KR','US'):
        x=complete[complete.market==market];sc,bc=BASE[market]
        np.testing.assert_allclose(x.net_share,.5*((1-sc)/(1+bc)*x.sell/x.buy-1),atol=1e-12)
        np.testing.assert_allclose(x.net_pnl,.5*((1-sc)/(1+bc)-x.buy/x.sell),atol=1e-12)
    for keys,x in g.groupby(['market','symbol','strategy']):
        x=x.sort_values('cycle')
        if len(x)>1: assert (x.signal_i.to_numpy()[1:]>=x.buy_i.to_numpy()[:-1]).all()
        assert (~x.completed).sum()<=1
    paired=p.pivot(index=['market','symbol','strategy','period'],columns='mode',values='final')
    assert (paired['gross']+1e-10>=paired['base']).all()
    assert (paired['base']+1e-10>=paired['stress']).all()
    assert (paired['interest4']+1e-10>=paired['base']).all()
    # Select deterministic whole histories with many cycles, plus first/last, from raw inputs.
    chosen={}
    for market in ('KR','US'):
        names=sorted(g.loc[g.market==market,'symbol'].unique())
        chosen[market]=[names[i] for i in np.linspace(0,len(names)-1,12,dtype=int)]
        tails=complete[complete.market==market].assign(magnitude=lambda x:x.net_share.abs()).nlargest(8,'magnitude').symbol.unique()
        chosen[market]=sorted(set(chosen[market])|set(tails))
    rawpool,kr_calendar=prepare_kr(SOURCE,audit_out=out)
    kr_raw={s:x for s,x,_ in rawpool if s in chosen['KR']}
    us_records={x['symbol']:x for x in read(SOURCE/'us_acquisition.json') if x['status']=='ready'}
    import exchange_calendars as xc
    us_calendar=xc.get_calendar('XNYS',start='2015-01-01',end='2026-10-16').sessions.tz_localize(None)
    count=0;paths=0;prices=0;signals=0;replayed=[]
    for market in ('KR','US'):
        for symbol in chosen[market]:
            z=load_arrays(array_paths[(market,symbol)])
            raw=kr_raw[symbol] if market=='KR' else us_stock(SOURCE,us_records[symbol])[0]
            seg,_=trading_segments(raw,kr_calendar if market=='KR' else us_calendar);ix=np.concatenate(seg)
            for k in ('open','high','low','close','cap','rank'):
                np.testing.assert_allclose(z[k],raw.iloc[ix][k].to_numpy(),equal_nan=True,atol=1e-10);prices+=len(ix)
            flags=independent_signals(z)
            for k in flags: np.testing.assert_array_equal(flags[k],z[k]);signals+=len(z[k])
            for strategy in STRATEGIES:
                expected,begin,values=scalar_replay(z,flags,strategy,market)
                actual=g[(g.market==market)&(g.symbol==symbol)&(g.strategy==strategy)]
                assert len(expected)==len(actual),(market,symbol,strategy,len(expected),len(actual))
                for e,(_,row) in zip(expected,actual.iterrows()):
                    for key,field in [('t','signal_i'),('s','sell_i'),('r','rebound_i'),('b','buy_i')]:
                        assert (pd.isna(row[field]) if e[key] is None else int(row[field])==e[key]),(market,symbol,strategy,key)
                    count+=1
                path=p[(p.market==market)&(p.symbol==symbol)&(p.strategy==strategy)&(p.period=='full')&(p['mode']=='base')].iloc[0]
                assert np.isclose(path.final,values[-1],rtol=1e-10)
                # Distant future modification cannot change earlier signals or executed prefix.
                cutoff=len(z['close'])//2
                zz={k:v[:cutoff] for k,v in z.items()};ff=independent_signals(zz)
                _,bb,prefix=scalar_replay(zz,ff,strategy,market)
                np.testing.assert_allclose(prefix,values[:cutoff],equal_nan=True,rtol=1e-10)
                paths+=1
            replayed.append(dict(market=market,symbol=symbol))
    result=read(out/'results.json');checks=0
    for r in result['summaries']:
        sub=g[(g.market==r['market'])&(g.strategy==r['strategy'])]
        if r['period']!='all': sub=sub[sub.period==r['period']]
        assert len(sub)==r['total'] and int(sub.completed.sum())==r['n']
        assert np.isclose(sub.loc[sub.completed,'net_share'].mean(),r['net_mean'])
        assert np.isclose(r['cheaper']+r['dearer']+r['equal'],1.)
        checks+=1
    # Verify cohort sums from per-stock terminal values, including cash/stale marks.
    for r in result['cohorts']:
        if r['mode']=='stale_zero': continue
        x=p[(p.market==r['market'])&(p.period==r['period'])&(p.strategy==r['strategy'])&(p['mode']==r['mode'])&p.fixed_cohort]
        assert np.isclose(x.final.mean(),r['final']) and np.isclose(x.hold_final.mean(),r['hold_final'])
        checks+=1
    missing=[]
    if report:
        for s in re.findall(r'\]\(([^)]+)\)',report.read_text(encoding='utf-8')):
            if s.startswith(('https:','http:','#')): continue
            target=(report.parent/s.strip('<>')).resolve()
            if target.name in ('verification.json','manifest.json'): continue
            if not target.exists(): missing.append(str(target))
        assert not missing,missing
    receipt=dict(status='PASS',raw_hashes=hashes,array_hashes=len(meta['stocks']),ledger_rows=len(g),
        independent_raw_price_cap_values=prices,independent_signal_flags=signals,independent_cycles=count,
        independent_stock_strategy_paths=paths,prefix_paths=paths,table_checks=checks,sample=replayed,
        limitations='Scalar replay checks implementation, not missing delisted returns, exchange open liquidity, actual taxes or KR dividends.')
    write(out/'verification.json',receipt)
    files=[p for p in out.iterdir() if p.is_file() and p.name!='manifest.json']
    files += [ROOT/'scripts'/x for x in ('ma_reentry_research.py','ma_reentry_statistics.py','ma_reentry_report.py','verify_ma_reentry.py','ma_reentry_protocol.json')]
    files += [ROOT/'tests/test_ma_reentry.py']
    if report: files.append(report)
    write(out/'manifest.json',dict(files=[dict(path=str(p),sha256=sha(p)) for p in files if p.exists()],raw_hash_manifest='prepared.json'))
    print({k:v for k,v in receipt.items() if k not in ('sample','limitations')},flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);p.add_argument('--report',type=Path)
    a=p.parse_args();verify(a.out,a.report)
