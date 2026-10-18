"""Nine locked MA timing pairs: exhaustive alternating segments and capital paths."""
from __future__ import annotations
import argparse,csv,gzip,math
from pathlib import Path
from ma_reentry_research import ROOT,read,write,sha,load_arrays,less,fill_index,inception,BASE,STRESS,mdd
import numpy as np
import pandas as pd

SOURCE=ROOT/'runtime/ma-reentry-20261017'
BUYS=['C5','G5_10','G10_20'];SELLS=['D5_20','D10_20','DA']
PAIRS=[b+'__'+s for b in BUYS for s in SELLS]


def flags(z):
    out={k:np.zeros(len(z['close']),bool) for k in BUYS+SELLS}
    for sg in np.unique(z['segment']):
        ix=np.flatnonzero(z['segment']==sg);c=z['close'][ix]
        ma={k:pd.Series(c).rolling(k).mean().to_numpy() for k in (5,10,20)}
        for name,a,b in [('C5',c,ma[5]),('G5_10',ma[5],ma[10]),('G10_20',ma[10],ma[20]),
                         ('D5_20',ma[20],ma[5]),('D10_20',ma[20],ma[10])]:
            above=less(b,a);priorvalid=np.r_[False,np.isfinite(a[:-1])&np.isfinite(b[:-1])]
            out[name][ix]=above & ~np.r_[False,above[:-1]] & priorvalid
        state=less(ma[5],ma[20])&less(ma[10],ma[20])
        out['DA'][ix]=state & ~np.r_[False,state[:-1]] & np.r_[False,np.isfinite(ma[20][:-1])]
    return out


def transactions(z,f,pair,start='2016-01-01',initial_held=False,delay=1):
    inc=inception(z,start)
    if inc is None:return [],None,{}
    initial_signal,begin=inc;buy,sell=pair.split('__');held=initial_held;next_close=initial_signal
    events=[];skipped=0;simultaneous=0;irrelevant=0
    for t in np.flatnonzero((f[buy]|f[sell])&(np.arange(len(z['close']))>=initial_signal)):
        t=int(t)
        if t<next_close:continue
        if f[buy][t] and f[sell][t]:simultaneous+=1
        if not (f[sell][t] if held else f[buy][t]):irrelevant+=1;continue
        j=fill_index(z,t,delay)
        if j is None:skipped+=1;continue
        kind='sell' if held else 'buy'
        events.append(dict(kind=kind,signal=t,fill=j));held=not held;next_close=j
    return events,begin,dict(unfilled=skipped,simultaneous=simultaneous,irrelevant=irrelevant)


def segments(z,events,begin,market,symbol,pair,regime,cuts,initial_held=False):
    phase='hold' if initial_held else 'avoid';a=begin;sig=None;rows=[]
    boundaries=[(e['fill'],e) for e in events]+[(len(z['close'])-1,None)]
    sc,bc=BASE[market]
    for end,event in boundaries:
        completed=event is not None;stop=end if completed else end+1
        price=z['open'][a];endprice=z['open'][end] if completed else z['close'][end]
        # A zero-duration initial boundary is retained with explicit left truncation.
        lows=np.r_[price,z['low'][a:stop],endprice];highs=np.r_[price,z['high'][a:stop],endprice]
        closes=np.r_[price,z['close'][a:stop],endprice]
        ret=endprice/price-1;eligible=bool(z['eligible'][sig]) if sig is not None else False
        cap=z['cap'][sig] if sig is not None else np.nan;rank=z['rank'][sig] if sig is not None else np.nan
        group='initial' if sig is None else 'outside' if not eligible else (
            ('rank1_50' if rank<=50 else 'rank51_100' if rank<=100 else 'rank101_200') if market=='KR' else
            ('mega' if cap>=200e9 else 'large' if cap>=10e9 else 'mid'))
        dt=z['date'][sig] if sig is not None else '';sigma=z['sigma'][sig] if sig is not None else np.nan
        vol='missing' if not np.isfinite(sigma) else 'Q'+str(np.searchsorted(cuts,sigma,side='right')+1)
        period='oos' if dt>='2023-04-01' else 'train' if dt and dt<='2022-12-31' and completed and z['date'][end]<='2022-12-31' else 'boundary'
        rows.append(dict(market=market,symbol=symbol,pair=pair,segment=len(rows),phase=phase,
            start_signal_i=sig,end_signal_i=event['signal'] if event else None,start_i=a,end_i=end,
            start_signal=dt,start_date=z['date'][a],end_date=z['date'][end],end_signal=z['date'][event['signal']] if event else '',
            initial=sig is None,completed=completed,eligible=eligible,group=group,cap=cap,rank=rank,
            start_price=price,end_price=endprice,price_return=ret,log_return=math.log1p(ret),
            sessions=int(z['pos'][end]-z['pos'][a]),days=int(z['day'][end]-z['day'][a]),
            adverse=max(0.,1-min(lows)/price),favorable=max(0.,max(highs)/price-1),
            close_mdd=float(np.max(1-closes/np.maximum.accumulate(closes))),
            gain50=.5*((1-sc)/(1+bc)*price/endprice-1) if phase=='avoid' and sig is not None and completed else np.nan,
            gain100=((1-sc)/(1+bc)*price/endprice-1) if phase=='avoid' and sig is not None and completed else np.nan,
            vol=vol,sigma=sigma,regime=regime.get(dt,'unknown'),period=period,
            year=int(dt[:4]) if dt else int(z['date'][a][:4]),
            quarter=dt[:4]+'Q'+str((int(dt[5:7])-1)//3+1) if dt else 'initial',
            gap_spanning=z['segment'][a]!=z['segment'][end],stale_end=z['date'][-1]<'2026-10-15'))
        if event:
            phase='hold' if event['kind']=='buy' else 'avoid';a=end;sig=event['signal']
    return rows


def ledger(z,events,begin,fraction,sc=0.,bc=0.,income=False,interest=0.,market='KR',initial_held=False):
    n=len(z['close']);d=z['day'];factor=np.ones(n)
    if income:
        factor=z['tr_factor']/z['tr_factor'][begin] if market=='US' else np.exp(.04*(d-d[begin])/365.25)
        if not np.all(np.isfinite(factor[begin:])&(factor[begin:]>0)):raise ValueError('income_factor_missing')
    c=z['close']*factor;o=z['open']*factor
    cash=0. if initial_held else fraction;q=(1-cash)/o[begin]
    path=np.full(n,np.nan);equity=np.full(n,np.nan);holding=np.zeros(n,bool);held=initial_held
    cursor=begin;cash_anchor=d[begin];cost=0.;buys=0;sells=0
    for e in events+[dict(kind='end',fill=n,signal=None)]:
        t=e['fill'];sl=slice(cursor,t)
        equity[sl]=q*c[sl];path[sl]=equity[sl]+cash*np.exp(interest*(d[sl]-cash_anchor)/365.25);holding[sl]=held
        if t==n:break
        cash*=math.exp(interest*(d[t]-cash_anchor)/365.25);cash_anchor=d[t]
        if e['kind']=='sell':
            proceeds=q*fraction*o[t];cash+=proceeds*(1-sc);q*=1-fraction;cost+=proceeds*sc;sells+=1;held=False
        else:
            charge=cash*bc/(1+bc);q+=cash/(o[t]*(1+bc));cash=0.;cost+=charge;buys+=1;held=True
        cursor=t
    cash*=math.exp(interest*(d[-1]-cash_anchor)/365.25)
    bh=c/o[begin];bh[:begin]=np.nan
    return dict(path=path,bh=bh,holding=holding,equity=equity,cash=cash,q=q,cost=cost,buys=buys,sells=sells,
                mean_exposure=float(np.mean(equity[begin:]/path[begin:])),hold_fraction=float(holding[begin:].mean()))


class Stream:
    def __init__(self,path):self.handle=gzip.open(path,'wt',encoding='utf-8',newline='');self.writer=None;self.n=0
    def add(self,rows):
        if not rows:return
        if self.writer is None:self.writer=csv.DictWriter(self.handle,fieldnames=list(rows[0]));self.writer.writeheader()
        self.writer.writerows(rows);self.n+=len(rows)
    def close(self):self.handle.close()


def run(out,source=SOURCE):
    out.mkdir(parents=True,exist_ok=True)
    if (out/'paths.csv.gz').exists():raise FileExistsError('immutable run output exists')
    (out/'protocol.json').write_bytes((ROOT/'scripts/ma_timing_protocol.json').read_bytes())
    meta=read(source/'prepared.json');proxies={m:pd.read_csv(source/f'{m}_market_proxy.csv') for m in ('KR','US')}
    streams={m:Stream(out/f'{m}_segments.csv.gz') for m in ('KR','US')};paths=Stream(out/'paths.csv.gz');trades=Stream(out/'trades.csv.gz')
    cohorts={};audits=[]
    for m in ('KR','US'):
        dates=proxies[m].date.to_numpy(dtype='U10')
        for period,start in [('full','2016-01-01'),('oos','2023-04-01')]:
            first=np.flatnonzero(dates>=start)[0]
            cohorts[m,period]=dict(dates=dates[first+1:],signal=dates[first],n=0,stale=0,values={})
    for num,rec in enumerate(meta['stocks']):
        m=rec['market'];symbol=rec['symbol'];assert sha(rec['path'])==rec['sha256']
        z=load_arrays(rec['path']);f=flags(z);regime=dict(zip(proxies[m].date,proxies[m].regime));cuts=meta['markets'][m]['vol_cuts']
        for period,start in [('full','2016-01-01'),('oos','2023-04-01')]:
            inc=inception(z,start)
            if inc is None:continue
            _,begin=inc;years=(z['day'][-1]-z['day'][begin])/365.25;co=cohorts[m,period]
            member=z['date'][inc[0]]==co['signal']
            if member:
                co['n']+=1;co['stale']+=int(z['date'][-1]<'2026-10-15')
                lookup=np.searchsorted(z['date'],co['dates'],side='right')-1
                assert np.min(lookup)>=begin
            for pair in PAIRS:
                events,_,audit=transactions(z,f,pair,start)
                alternatives={'initial_held':transactions(z,f,pair,start,True)[0],'delay2':transactions(z,f,pair,start,False,2)[0]}
                audits.append(dict(market=m,symbol=symbol,pair=pair,period=period,**audit))
                if period=='full':
                    streams[m].add(segments(z,events,begin,m,symbol,pair,regime,cuts))
                    trades.add([dict(market=m,symbol=symbol,pair=pair,trade=k,**e,
                                     signal_date=z['date'][e['signal']],fill_date=z['date'][e['fill']],price=z['open'][e['fill']],
                                     eligible=z['eligible'][e['signal']]) for k,e in enumerate(events)])
                for fraction in (1.,.5):
                    for mode in ('gross','base','stress','income','interest4','initial_held','delay2'):
                        sc,bc=(0.,0.) if mode=='gross' else STRESS[m] if mode=='stress' else BASE[m]
                        ee=alternatives.get(mode,events)
                        w=ledger(z,ee,begin,fraction,sc,bc,mode=='income',.04 if mode=='interest4' else 0.,m,mode=='initial_held')
                        final=w['path'][-1];bh=w['bh'][-1];ex=w['mean_exposure']
                        passive=ex*w['bh']+(1-ex)
                        rows=dict(market=m,symbol=symbol,pair=pair,fraction=fraction,period=period,mode=mode,
                            start=z['date'][begin],end=z['date'][-1],years=years,final=final,hold_final=bh,relative=final/bh-1,
                            cagr=final**(1/years)-1 if years>=1 else np.nan,hold_cagr=bh**(1/years)-1 if years>=1 else np.nan,
                            mdd=mdd(w['path']),hold_mdd=mdd(w['bh']),buys=w['buys'],sells=w['sells'],
                            trades_per_year=(w['buys']+w['sells'])/years if years>0 else np.nan,
                            hold_fraction=w['hold_fraction'],mean_exposure=ex,cost=w['cost'],terminal_cash=w['cash'],
                            passive_final=passive[-1],passive_mdd=mdd(passive),fixed_cohort=member,
                            stale=z['date'][-1]<'2026-10-15',eligible_fraction=float(z['eligible'][begin:].mean()))
                        paths.add([rows])
                        if member:
                            key=pair+'__'+str(int(fraction*100))+'__'+mode
                            aligned=w['path'][lookup];co['values'][key]=co['values'].get(key,0.)+aligned
                            if pair==PAIRS[0] and fraction==1 and mode in ('base','income'):
                                k='hold_income' if mode=='income' else 'hold';co['values'][k]=co['values'].get(k,0.)+w['bh'][lookup]
                            if mode=='base':
                                pas=passive[lookup];co['values'][key+'_passive']=co['values'].get(key+'_passive',0.)+pas
                                stress=aligned.copy();stressbh=w['bh'][lookup].copy()
                                if z['date'][-1]<'2026-10-15':
                                    after=co['dates']>z['date'][-1];stress[after]=w['cash'];stressbh[after]=0.
                                co['values'][key+'_stalezero']=co['values'].get(key+'_stalezero',0.)+stress
                                if pair==PAIRS[0] and fraction==1:co['values']['hold_stalezero']=co['values'].get('hold_stalezero',0.)+stressbh
        if (num+1)%100==0:print('timing',num+1,'/',len(meta['stocks']),'segments',sum(s.n for s in streams.values()),flush=True)
    for s in list(streams.values())+[paths,trades]:s.close()
    coinfo=[]
    for (m,period),v in cohorts.items():
        pd.DataFrame({'date':v['dates'],**{k:x/v['n'] for k,x in v['values'].items()}}).to_csv(out/f'{m}_{period}_cohort.csv',index=False)
        coinfo.append(dict(market=m,period=period,n=v['n'],stale=v['stale'],signal_date=v['signal']))
    pd.DataFrame(audits).to_csv(out/'execution_audit.csv.gz',index=False,compression='gzip')
    write(out/'simulation.json',dict(protocol_sha256=sha(out/'protocol.json'),source_prepared=str((source/'prepared.json').resolve()),
        source_sha256=sha(source/'prepared.json'),segments={m:s.n for m,s in streams.items()},paths=paths.n,trades=trades.n,cohorts=coinfo))
    print('timing simulation done',paths.n,trades.n,flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);a=p.parse_args();run(a.out)
