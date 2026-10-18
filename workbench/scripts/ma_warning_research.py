"""Independent KR/US drawdown-warning study. Offline research, no trade rules.

Protocol fixed in Git BEFORE computations. Commands prepare -> select -> test.
Raw audited price loader is reused; operating universe/trades are never imported.
"""
from __future__ import annotations
import argparse
from collections import defaultdict
from datetime import datetime,timezone
import hashlib
import json
import math
from pathlib import Path
import numpy as np
import pandas as pd

BASES=['close_below_ma5','close_below_ma10','close_below_ma20','ma5_below_ma10',
       'ma5_below_ma20','ma10_below_ma20','ma5_slope_nonpositive','ma10_slope_nonpositive',
       'ma20_slope_nonpositive','close_below_ma20_and_ma20_down','ma5_below_ma10_and_ma20_down']
SIGNALS=[(b,k) for b in BASES for k in (1,2)]
NAMES=[f'{b}__c{k}' for b,k in SIGNALS]
TARGETS=[10,20,15]


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
    return h.hexdigest()


def read(path): return json.loads(Path(path).read_bytes())


def write(path,value):
    def clean(x):
        if isinstance(x,dict): return {str(k):clean(v) for k,v in x.items()}
        if isinstance(x,(tuple,list)): return [clean(v) for v in x]
        if isinstance(x,np.ndarray): return clean(x.tolist())
        if isinstance(x,np.generic): return clean(x.item())
        if isinstance(x,float) and math.isnan(x): return None
        return x
    Path(path).write_text(json.dumps(clean(value),ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')


def digest(text): return int(hashlib.sha256(text.encode()).hexdigest(),16)


def indicators(bars):
    df=pd.DataFrame(bars); c=df['close'].astype(float); f={k:df[k].to_numpy(float) for k in ['open','high','low','close','volume']}
    for k in (5,10,20,200): f[f'ma{k}']=c.rolling(k).mean().to_numpy()
    for k in (5,10,20): f[f'slope{k}']=pd.Series(f[f'ma{k}']).diff(3).to_numpy()
    f['vol']=np.log(c).diff().rolling(20).std(ddof=1).to_numpy()
    f['ret63']=(c/c.shift(63)-1).to_numpy()
    raw=[c.to_numpy()<f['ma5'],c.to_numpy()<f['ma10'],c.to_numpy()<f['ma20'],
         f['ma5']<f['ma10'],f['ma5']<f['ma20'],f['ma10']<f['ma20'],
         f['slope5']<=0,f['slope10']<=0,f['slope20']<=0,
         (c.to_numpy()<f['ma20'])&(f['slope20']<=0),(f['ma5']<f['ma10'])&(f['slope20']<=0)]
    state=[]; onset=[]
    for a in raw:
        two=a&np.r_[False,a[:-1]]
        for cond in (a,two):
            state.append(cond); onset.append(cond&~np.r_[False,cond[:-1]])
    f['state']=np.stack(state,axis=1); f['onset']=np.stack(onset,axis=1)
    return f,df['date'].to_numpy(dtype='U10')


def detect_events(close,lookback=63):
    """Online peak excursion. Prefix extension never changes an earlier reset."""
    c=np.asarray(close); record=pd.Series(c).rolling(lookback).max().to_numpy()
    parent=np.cumsum(c>=np.maximum.accumulate(c))
    start=max(200,lookback); pi=start-lookback+1+int(np.argmax(c[start-lookback+1:start+1])); peak=c[pi]
    events=[]; active=None
    for t in range(start,len(c)):
        if active is None:
            if c[t]>=peak: peak=c[t]; pi=t; continue
            active=dict(peak_index=pi,peak=float(peak),start_index=t,parent=int(parent[pi]))
        if c[t]>=peak:
            events.append({**active,'end_index':t,'reset':'recovered'})
            active=None; peak=c[t]; pi=t
        elif t-pi>=lookback and c[t]>=record[t]:
            events.append({**active,'end_index':t,'reset':'lower_local_record'})
            active=None; peak=c[t]; pi=t
    if active is not None: events.append({**active,'end_index':len(c),'reset':'right_censored'})
    return events


def forward_extreme(a,h,kind='max'):
    s=pd.Series(a).shift(-1).iloc[::-1].rolling(h,min_periods=h)
    return (s.max() if kind=='max' else s.min()).iloc[::-1].to_numpy()


def market_state(return_series):
    daily=pd.concat(return_series,axis=1).mean(axis=1).sort_index().fillna(0)
    idx=(1+daily).cumprod(); ma=idx.rolling(200).mean(); slope=ma.diff(20)
    regime=np.where((idx>ma)&(slope>0),1,np.where((idx<ma)&(slope<0),-1,0))
    frame=pd.DataFrame({'date':idx.index,'index':idx.to_numpy(),'regime':regime,'ret20':idx.pct_change(20).to_numpy()})
    return frame


def choose_sample(root,p):
    from saved_us_history import SavedHistory
    old=read(root/'runtime/ma-exit4-20261016-v1/data_audit.json')
    oldset={(r['market'],r['symbol']) for r in old['records']}
    latest=read(root/'runtime/latest.json'); run=read(Path(latest['output'])/'run.json')
    cap_path=Path(run['market_source'])/'snapshot.json'; snapshot=read(cap_path)
    histories={m:SavedHistory(root/'runtime'/f'{m.lower()}-history/active.json',m) for m in ('KR','US')}
    samples=[]; audit={'old_audit_path':str(root/'runtime/ma-exit4-20261016-v1/data_audit.json'),
        'old_audit_sha256':sha(root/'runtime/ma-exit4-20261016-v1/data_audit.json'),
        'cap_snapshot':str(cap_path),'cap_snapshot_sha256':sha(cap_path),'cap_snapshot_fetched_at':snapshot.get('fetched_at'),
        'pointers':{},'raw_universe_counts':{},'source_records':[]}
    for m,h in histories.items():
        if h.error or not h.enabled: raise ValueError('audited_raw_history_unavailable')
        audit['pointers'][m]={'path':str(h.pointer),'sha256':sha(h.pointer),'value':read(h.pointer)}
        audit['raw_universe_counts'][m]=len(h.stocks)
        eligible=[s for s in h.stocks.values() if (m,s['symbol']) not in oldset]
        eligible.sort(key=lambda s:digest('MA-WARN-v1|'+m+':'+s['symbol']))
        main={s['symbol'] for s in eligible[:600]}
        caps=[s for s in snapshot['series'] if s['market']==m and s.get('is_common_share') is True
              and isinstance(s.get('market_cap'),(float,int)) and s['market_cap']>0
              and m+':'+s['symbol'] in h.stocks]
        caps.sort(key=lambda s:(-s['market_cap'],s['symbol'])); ranks={s['symbol']:i+1 for i,s in enumerate(caps[:100])}
        symbols=sorted(main|{s for s in ranks if (m,s) not in oldset})
        for symbol in symbols:
            s=h.stocks[m+':'+symbol]
            samples.append({**s,'core':symbol in main,'current_largecap':symbol in ranks,'current_cap_rank':ranks.get(symbol),
                            'reserved':digest('MA-WARN-HOLDOUT-v1|'+m+':'+symbol)%10<3})
    return histories,samples,audit


def prepared_stock(root,h,stock,p):
    packet=h.packet(stock); series=packet.get('series',{}); q=packet['history'].get('quality',{})
    bars=[b for b in series.get('bars',[]) if p['warmup_start']<=b['date']<=p['data_end']]
    source=h.root/'series'/ (hashlib.sha256(stock['symbol'].encode()).hexdigest()+'.json')
    record={'market':stock['market'],'symbol':stock['symbol'],'path':str(source),'sha256':sha(source),
            'history':packet['history'],'provider':series.get('provider'),'basis':series.get('price_basis')}
    return bars,q,record


def build_rows(f,dates,events,stock,symbol_id,marketframe,p,event_id_start):
    n=len(dates); dateints=dates.astype('datetime64[D]').astype(np.int32)
    m=marketframe.set_index('date').reindex(dates)
    regime=m['regime'].fillna(0).to_numpy(np.int8); mret=m['ret20'].fillna(0).to_numpy(float)
    fields=defaultdict(list); metadata=[]; excluded=defaultdict(int)
    c=f['close']; largegap=np.r_[False,np.diff(dateints)>10]
    highs={h:forward_extreme(f['high'],h) for h in p['horizons']}
    lows={h:forward_extreme(f['low'],h,'min') for h in p['horizons']}
    highclose={h:forward_extreme(c,h) for h in p['horizons']}
    # Isolated missing/invalid dates are measurement failures. Long calendar
    # interruptions are retained and flagged, not silently excluded as bad trades.
    allbad=set(stock['_quality'].get('excluded_dates',[]))|set(stock['_quality'].get('missing_sessions',[]))
    bad=[]
    for s in allbad:
        j=int(np.searchsorted(dates,s))
        if 0<j<n and dateints[j]-dateints[j-1]>10: continue
        bad.append(s)
    for e in events:
        pi=e['peak_index']; last=pi+186
        if last>=n or dates[pi]<p['study_start']: continue
        split=0 if dates[last]<=p['train_end'] else (
              1 if dates[pi]>p['train_end'] and dates[last]<=p['validation_end'] else (
              2 if dates[pi]>p['validation_end'] else -1))
        if split<0 or (stock['reserved']!=(split==2)): continue
        if any(dates[max(0,pi-200)]<=b<=dates[last] for b in bad): excluded['isolated_quality_event']+=1; continue
        end=min(e['end_index'],pi+187); times=np.arange(e['start_index'],min(pi+127,e['end_index']))
        if not len(times): continue
        eid=event_id_start+len(metadata); peak=e['peak']; offsets=times-pi
        target_idx={}
        for target in TARGETS:
            hits=np.flatnonzero(c[e['start_index']:end]<=peak*(1-target/100))
            target_idx[target]=int(e['start_index']+hits[0]) if len(hits) else -1
        state=f['state'][times]; onset=f['onset'][times]
        first=np.zeros(len(times),dtype=np.uint32); firststate=np.zeros(len(times),dtype=np.uint32)
        left=np.uint32(0); onset_counts=[]
        for j in range(len(SIGNALS)):
            x=np.flatnonzero(onset[:,j]); y=np.flatnonzero(state[:,j]); onset_counts.append(len(x))
            if len(x): first[x[0]]|=np.uint32(1<<j)
            if len(y):
                firststate[y[0]]|=np.uint32(1<<j)
                if y[0]==0 and not onset[0,j]: left|=np.uint32(1<<j)
        statebits=np.zeros(len(times),dtype=np.uint32)
        for j in range(len(SIGNALS)): statebits|=(state[:,j].astype(np.uint32)<<j)
        tradable=(f['volume'][times+1]>0)&(f['high'][times+1]>f['low'][times+1])
        execdd=np.where(tradable,1-f['open'][times+1]/peak,np.nan)
        parent=f'{symbol_id}:{e["parent"]}'
        rec={'event':eid,'symbol_id':symbol_id,'market':stock['market'],'symbol':stock['symbol'],
             'core':stock['core'],'current_largecap':stock['current_largecap'],'reserved':stock['reserved'],
             'split':split,'peak_date':str(dates[pi]),'peak_price':peak,'first_underwater':str(dates[times[0]]),
             'observed_end':str(dates[e['end_index']]) if e['end_index']<=last else None,'reset':e['reset'] if e['end_index']<=last else 'beyond_label_window',
             'parent':parent,'below_ma200_at_peak':bool(c[pi]<f['ma200'][pi]),
             'long_gap':bool(largegap[max(0,pi-20):last+1].any()),'left_state_bits':int(left),
             'onset_counts':json.dumps(onset_counts),
             **{f'target{z}_offset':target_idx[z]-pi if target_idx[z]>=0 else -1 for z in TARGETS}}
        metadata.append(rec)
        data={'event':np.full(len(times),eid,np.int32),'symbol':np.full(len(times),symbol_id,np.int16),
              'split':np.full(len(times),split,np.int8),'date':dateints[times],
              'quarter':((pd.DatetimeIndex(dates[times]).year-2000)*4+(pd.DatetimeIndex(dates[times]).quarter-1)).to_numpy(np.int16),
              'offset':offsets.astype(np.int16),'dd':1-c[times]/peak,'vol':f['vol'][times],
              'regime':regime[times],'market_ret20':mret[times],'below_ma200':c[times]<f['ma200'][times],
              'core':np.full(len(times),stock['core'],bool),'largecap':np.full(len(times),stock['current_largecap'],bool),
              'first':first,'first_state':firststate,'state':statebits,'exec_dd':execdd,
              'next_open':np.where(tradable,f['open'][times+1],np.nan),'next_date':dateints[times+1],
              'long_gap':largegap[times+1],
              'recovery_delay':np.full(len(times),e['end_index'],int)-times if e['reset']=='recovered' and e['end_index']<=last else np.full(len(times),-1,int)}
        for target,hit in target_idx.items():
            data[f'delay{target}']=np.where(times>=hit,-2,hit-times).astype(np.int16) if hit>=0 else np.full(len(times),-1,np.int16)
        for h in p['horizons']:
            data[f'up{h}']=highs[h][times]/c[times]-1; data[f'down{h}']=lows[h][times]/c[times]-1
            data[f'rebound{h}']=highclose[h][times]>=c[times]*1.05
            data[f'recover{h}']=(data['recovery_delay']>0)&(data['recovery_delay']<=h)
            data[f'terminal{h}']=c[times+h]/c[times]-1
            data[f'exec_up{h}']=highs[h][times]/np.where(tradable,f['open'][times+1],np.nan)-1
        for k,v in data.items(): fields[k].append(v)
    return {k:np.concatenate(v) for k,v in fields.items()},metadata,dict(excluded)


def prepare(root,out,protocol,lookback=None):
    p=read(protocol)
    if (out/'data_audit.json').exists(): raise FileExistsError('prepared_study_immutable')
    out.mkdir(parents=True,exist_ok=True); write(out/'protocol.json',p)
    histories,samples,audit=choose_sample(root,p); pd.DataFrame([{k:v for k,v in s.items() if k!='bars'} for s in samples]).to_csv(out/'sample.csv',index=False)
    audit.update(created_at=datetime.now(timezone.utc).isoformat(),protocol_sha256=sha(out/'protocol.json'),
                 threshold_review_sha256=sha(root/'docs/ma-warning-thresholds-2026-10-16.md'),
                 previous_sample_overlap=0,lookback=lookback or p['peak_lookback'],exclusions={})
    for market,h in histories.items():
        stocks=[s for s in samples if s['market']==market]; returns=[]
        for i,s in enumerate(stocks):
            bars,q,record=prepared_stock(root,h,s,p)
            audit['source_records'].append(record)
            if bars and s['core'] and not s['reserved']:
                prices=pd.Series([b['close'] for b in bars],index=[b['date'] for b in bars]); returns.append(prices.pct_change())
        mf=market_state(returns); mf.to_csv(out/f'{market}_market_proxy.csv',index=False)
        chunks=defaultdict(list); events=[]; failures=defaultdict(int)
        for sid,s in enumerate(stocks):
            bars,q,record=prepared_stock(root,h,s,p)
            if len(bars)<400: failures['short_or_unavailable_history']+=1; continue
            f,dates=indicators(bars); es=detect_events(f['close'],lookback or p['peak_lookback']); s['_quality']=q
            a,rows,excluded=build_rows(f,dates,es,s,sid,mf,p,len(events))
            for k,v in a.items(): chunks[k].append(v)
            events+=rows
            for k,v in excluded.items(): failures[k]+=v
            if (sid+1)%100==0: print(f'{market} prepare {sid+1}/{len(stocks)} events={len(events)}',flush=True)
        arrays={k:np.concatenate(v) for k,v in chunks.items()}; np.savez_compressed(out/f'{market}_risk.npz',**arrays)
        pd.DataFrame(events).to_csv(out/f'{market}_events.csv',index=False)
        stocksout=pd.DataFrame([{k:v for k,v in s.items() if k not in ('bars','_quality')} for s in stocks]); stocksout['symbol_id']=np.arange(len(stocksout))
        stocksout.to_csv(out/f'{market}_symbols.csv',index=False)
        audit['exclusions'][market]=dict(failures)
        audit[market]={'sampled':len(stocks),'main_panel':int(stocksout['core'].sum()),'reserved_main':int((stocksout['core']&stocksout['reserved']).sum()),
                       'events':len(events),'risk_rows':len(arrays['dd']),
                       'risk_sha256':sha(out/f'{market}_risk.npz'),'events_sha256':sha(out/f'{market}_events.csv')}
        print(market,audit[market],flush=True)
    write(out/'data_audit.json',audit)


def load_market(out,market,split,cohort='core'):
    audit=read(out/'data_audit.json'); p=read(out/'protocol.json')
    if sha(out/'protocol.json')!=audit['protocol_sha256']: raise ValueError('protocol_changed')
    if sha(out/f'{market}_risk.npz')!=audit[market]['risk_sha256']: raise ValueError('data_changed')
    a=dict(np.load(out/f'{market}_risk.npz')); events=pd.read_csv(out/f'{market}_events.csv')
    mask=(a['split']==split)&a['core'] if cohort=='core' else (a['split']==split)&a['largecap']
    a={k:v[mask] for k,v in a.items()}
    events=events[(events['split']==split)&events['core']] if cohort=='core' else events[(events['split']==split)&events['current_largecap']]
    return a,events.reset_index(drop=True),p


def interval(y,symbol,quarter,B=2000,seed=2026101602):
    y=np.asarray(y,float); valid=np.isfinite(y); y=y[valid]; symbol=np.asarray(symbol)[valid]; quarter=np.asarray(quarter)[valid]
    if not len(y): return {'estimate':None,'symbol_ci95':[None,None],'quarter_ci95':[None,None],'symbols':0,'quarters':0}
    rng=np.random.default_rng(seed); result={'estimate':float(y.mean())}
    for name,groups in [('symbol',symbol),('quarter',quarter)]:
        u,idx=np.unique(groups,return_inverse=True); sums=np.bincount(idx,weights=y); counts=np.bincount(idx); k=len(u)
        if name=='symbol':
            weights=rng.multinomial(k,np.full(k,1/k),size=B)
            draws=(weights@sums)/(weights@counts)
        else:
            starts=rng.integers(0,k,(B,math.ceil(k/4))); ix=((starts[:,:,None]+np.arange(4))%k).reshape(B,-1)[:,:k]
            draws=sums[ix].sum(axis=1)/counts[ix].sum(axis=1)
        result[name+'_ci95']=np.quantile(draws,[.025,.975]).tolist(); result[name+'s']=k
    return result


def age_bucket(offset): return np.select([offset<=5,offset<=20,offset<=60],[0,1,2],default=3).astype(np.int8)


def matching_covariates(a,cuts):
    volbin=np.searchsorted(cuts,a['vol']).astype(np.int8); dd_bin=np.floor(a['dd']/.01).astype(np.int16)
    age=age_bucket(a['offset'])
    return np.column_stack([a['quarter'],a['regime'],age,volbin,dd_bin]).astype(np.int32)


def control_bank(a,keys,eligible):
    bank=defaultdict(list)
    scheduled=(a['offset']-1)%5==0
    for i in np.flatnonzero(eligible&scheduled&np.isfinite(a['vol'])&(a['vol']>0)):
        bank[tuple(keys[i])].append(int(i))
    for key,rows in bank.items():
        idx=np.asarray(rows,dtype=np.int32)
        if len(idx)>512:
            hashes=(a['date'][idx].astype(np.int64)*1000003+a['symbol'][idx].astype(np.int64)*15485863)%2147483647
            idx=idx[np.argsort(hashes,kind='stable')[:512]]
        bank[key]=idx
    return bank


def match_indices(a,j,eligible,keys,bank,first_kind='first'):
    """Covariates/current states only. Future outcome values NEVER read here."""
    treated=np.flatnonzero(((a[first_kind]>>j)&1).astype(bool)&eligible)
    controls=np.full((len(treated),5),-1,dtype=np.int32)
    state=((a['state']>>j)&1).astype(bool)
    for row,t in enumerate(treated):
        key=keys[t]; candidates=[]
        for delta in (-1,0,1):
            k=tuple(np.r_[key[:-1],key[-1]+delta]); idx=bank.get(k)
            if idx is not None: candidates.append(idx)
        if not candidates or not np.isfinite(a['vol'][t]) or a['vol'][t]<=0: continue
        pool=np.concatenate(candidates)
        vr=a['vol'][pool]/a['vol'][t]
        good=(~state[pool])&(a['symbol'][pool]!=a['symbol'][t])&(a['event'][pool]!=a['event'][t])
        good&=(np.abs(a['dd'][pool]-a['dd'][t])<=.005)&(vr>=.8)&(vr<=1.25)
        good&=np.abs(a['market_ret20'][pool]-a['market_ret20'][t])<=.03
        pool=pool[good]
        if not len(pool): continue
        distance=np.abs(a['dd'][pool]-a['dd'][t])/.005+np.abs(np.log(a['vol'][pool]/a['vol'][t]))/np.log(1.25)
        distance+=np.abs(a['market_ret20'][pool]-a['market_ret20'][t])/.03
        order=np.argsort(distance,kind='stable'); seen=set(); chosen=[]
        for k in order:
            ii=int(pool[k]); event=int(a['event'][ii])
            if event in seen: continue
            seen.add(event); chosen.append(ii)
            if len(chosen)==5: break
        controls[row,:len(chosen)]=chosen
    return treated,controls


def matched_statistics(a,treated,controls,target,h=60,B=2000):
    matched=(controls>=0).any(axis=1); tx=treated[matched]; cx=controls[matched]; ok=cx>=0; safe=np.maximum(cx,0)
    if not len(tx): return {'n':0,'coverage':0.,'uplift':None,'symbol_ci95':[None,None],'quarter_ci95':[None,None],'p_symbol':1.,'p_quarter':1.}
    count=ok.sum(axis=1); delay=a[f'delay{target}']; y=((delay>0)&(delay<=h)).astype(float)
    ty=y[tx]; cy=y[safe]; cm=(cy*ok).sum(axis=1)/count; delta=ty-cm; mean=float(delta.mean())
    rng=np.random.default_rng(2026101602); ns=int(a['symbol'].max())+1
    influence=np.bincount(a['symbol'][tx],weights=ty-ty.mean(),minlength=ns)
    controlmean=float(cm.mean())
    for k in range(5):
        weight=np.where(ok[:,k],-(cy[:,k]-controlmean)/count,0)
        influence+=np.bincount(a['symbol'][safe[:,k]],weights=weight,minlength=ns)
    influence/=len(tx)
    signs=rng.choice([-1,1],size=(B,ns)); draws=mean+signs@influence
    se=max(float(np.std(draws,ddof=1)),1e-15); pci=interval(delta,a['symbol'][tx],a['quarter'][tx],B)
    # Quarter resampling uses matched pair contrasts. Both sides are in the same
    # quarter by construction, preserving common-time shocks in that contrast.
    quarter_ci=pci['quarter_ci95']; qse=max((quarter_ci[1]-quarter_ci[0])/(2*1.96),1e-15)
    used=controls[controls>=0]; uniques,repeats=np.unique(used,return_counts=True)
    ctrl_dd=(a['dd'][safe]*ok).sum(axis=1)/count; ctrl_vol=(a['vol'][safe]*ok).sum(axis=1)/count
    ctrl_mkt=(a['market_ret20'][safe]*ok).sum(axis=1)/count
    return {'n':len(tx),'coverage':float(matched.mean()),'treated_probability':float(ty.mean()),
            'control_probability':controlmean,'uplift':mean,'symbol_ci95':np.quantile(draws,[.025,.975]).tolist(),
            'quarter_ci95':quarter_ci,'p_symbol':float(.5*math.erfc(mean/(se*math.sqrt(2)))),
            'p_quarter':float(.5*math.erfc(mean/(qse*math.sqrt(2)))),
            'symbols_treated':len(np.unique(a['symbol'][tx])),'symbols_both_sides':int(np.sum(influence!=0)),
            'quarters':len(np.unique(a['quarter'][tx])),'unique_controls':len(uniques),'max_control_reuse':int(repeats.max()),
            'mean_abs_dd_gap':float(np.abs(a['dd'][tx]-ctrl_dd).mean()),'max_abs_dd_gap':float(np.max(np.abs(a['dd'][tx,None]-a['dd'][safe])[ok])),
            'mean_vol_ratio':float((ctrl_vol/a['vol'][tx]).mean()),'mean_abs_market_ret20_gap':float(np.abs(a['market_ret20'][tx]-ctrl_mkt).mean())}


def distribution(x):
    x=np.asarray(x,float); x=x[np.isfinite(x)]
    if not len(x): return {'n':0,'mean':None,'median':None,'p10':None,'p90':None}
    return {'n':len(x),'mean':float(x.mean()),'median':float(np.median(x)),'p10':float(np.quantile(x,.1)),'p90':float(np.quantile(x,.9))}


def signal_summary(a,events,j,target,h=60,kind='first',with_ci=True):
    delay=a[f'delay{target}']; valid=delay!=-2
    ix=np.flatnonzero(((a[kind]>>j)&1).astype(bool)&valid)
    assert len(np.unique(a['event'][ix]))==len(ix),'same_event_duplicate_signal'
    hit=(delay[ix]>0)&(delay[ix]<=h); execok=np.isfinite(a['exec_dd'][ix])
    caught_events=events[(events[f'target{target}_offset']>0)&(events[f'target{target}_offset']<=126)]
    offsets=events.set_index('event')[f'target{target}_offset']
    toff=offsets.reindex(a['event'][ix]).to_numpy(); earlier=(toff>a['offset'][ix])&(toff<=126)&(toff>0)
    executable=earlier&execok&(a['offset'][ix]+1<=toff)&(a['exec_dd'][ix]<target/100)
    caught=set(a['event'][ix[earlier]]); realcaught=set(a['event'][ix[executable]])
    ey=caught_events['event'].isin(caught).to_numpy(float); ry=caught_events['event'].isin(realcaught).to_numpy(float)
    peaktime=pd.to_datetime(caught_events['peak_date']); quarters=(peaktime.dt.year-2000)*4+peaktime.dt.quarter-1
    negative=events[(events[f'target{target}_offset']<0)|(events[f'target{target}_offset']>126)]
    allwarn=set(a['event'][ix]); fpr=float(negative['event'].isin(allwarn).mean()) if len(negative) else None
    result={'signal':NAMES[j],'signal_index':j,'target':target,'horizon':h,'kind':kind,'n':len(ix),
            'symbols':len(np.unique(a['symbol'][ix])),'target_events':len(caught_events),'non_target_events':len(negative),
            'signal_dd':distribution(a['dd'][ix]),'execution_dd':distribution(a['exec_dd'][ix]),
            'precision':float(hit.mean()) if len(ix) else None,'false_discovery':float(1-hit.mean()) if len(ix) else None,
            'recall':float(ey.mean()) if len(ey) else None,'executable_recall':float(ry.mean()) if len(ry) else None,
            'false_positive_rate_event126':fpr,'lead_to_target':distribution(delay[ix][hit]),
            'remaining_price_margin':distribution(target/100-a['exec_dd'][ix[hit]]),
            'next_open_already_at_target_fraction':float(np.mean(a['exec_dd'][ix][execok]>=target/100)) if execok.any() else None,
            'unexecutable_next_open_fraction':float(1-execok.mean()) if len(ix) else None,
            'recovery_probability':float(a[f'recover{h}'][ix].mean()) if len(ix) else None,
            'rebound5_probability':float(a[f'rebound{h}'][ix].mean()) if len(ix) else None,
            'further_up':distribution(a[f'up{h}'][ix]),'further_down':distribution(a[f'down{h}'][ix]),
            'false_alarm_missed_upside':distribution(np.maximum(0,a[f'exec_up{h}'][ix[~hit]])),
            'below_ma200_signal_fraction':float(a['below_ma200'][ix].mean()) if len(ix) else None,
            'long_gap_execution_cases':int(a['long_gap'][ix].sum()),
            'recall_by_peak_horizon':{}}
    for eh in (20,60,126):
        targets=events[(events[f'target{target}_offset']>0)&(events[f'target{target}_offset']<=eh)]
        result['recall_by_peak_horizon'][str(eh)]={'target_events':len(targets),'recall':float(targets['event'].isin(caught).mean()) if len(targets) else None,
                                                  'executable_recall':float(targets['event'].isin(realcaught).mean()) if len(targets) else None}
    if with_ci:
        result['precision_ci']=interval(hit,a['symbol'][ix],a['quarter'][ix])
        result['recall_ci']=interval(ey,caught_events['symbol_id'],quarters)
        result['executable_recall_ci']=interval(ry,caught_events['symbol_id'],quarters)
        result['recovery_ci']=interval(a[f'recover{h}'][ix],a['symbol'][ix],a['quarter'][ix])
    return result,ix


def analyze(out,market,split,cuts=None,cohort='core',only=None):
    a,events,p=load_market(out,market,split,cohort)
    if cuts is None:
        vol=a['vol'][np.isfinite(a['vol'])&(a['vol']>0)]; cuts=np.quantile(vol,[.2,.4,.6,.8])
    if not len(a['dd']) or not len(events): return {'market':market,'split':split,'cohort':cohort,'rows':[],'vol_cuts':cuts.tolist()}
    keys=matching_covariates(a,cuts); rows=[]; archive={}
    indices=range(len(SIGNALS)) if only is None else sorted(set(only))
    for target in (10,20):
        eligible=a[f'delay{target}']!=-2; bank=control_bank(a,keys,eligible)
        for j in indices:
            rec,ix=signal_summary(a,events,j,target,60)
            ti,ci=match_indices(a,j,eligible,keys,bank)
            assert np.array_equal(ix,ti)
            rec['matched']=matched_statistics(a,ti,ci,target)
            rows.append(rec); archive[f'j{j}_t{target}_treated']=ti.astype(np.int32); archive[f'j{j}_t{target}_controls']=ci
        print(f'{market} split={split} cohort={cohort} target={target} signals={len(list(indices))}',flush=True)
    path=out/f'{market}_s{split}_{cohort}'
    np.savez_compressed(str(path)+'_matches.npz',**archive)
    side=[]
    for j in indices:
        for target in (10,20,15):
            for h in (20,60):
                if target in (10,20) and h==60: continue
                rec,_=signal_summary(a,events,j,target,h,with_ci=False); side.append(rec)
        for target in (10,20):
            rec,_=signal_summary(a,events,j,target,60,kind='first_state',with_ci=False); rec['diagnostic']='FIRST CONFIRMED STATE, not necessarily a new onset'; side.append(rec)
    summary={'market':market,'split':split,'cohort':cohort,'vol_cuts':cuts.tolist(),
             'events':len(events),'symbols':events['symbol_id'].nunique(),'parents':events['parent'].nunique(),
             'below_ma200_peak_fraction':float(events['below_ma200_at_peak'].mean()),
             'long_gap_events':int(events['long_gap'].sum()),'rows':rows,'sensitivity':side}
    write(str(path)+'_summary.json',summary)
    return summary


def passed_gates(r,g,train_uplift=None,final_p=None):
    failures=[]; m=r.get('matched',{})
    tests={'min_validation_signals':r['n']>=g['min_validation_signals'],'min_symbols':r['symbols']>=g['min_symbols'],
           'match_coverage':m.get('coverage',0)>=g['min_match_coverage'],
           'signal_timing':r['signal_dd']['median'] is not None and r['signal_dd']['median']<=g.get('max_median_signal_drawdown',1),
           'execution_timing':r['execution_dd']['median'] is not None and r['execution_dd']['median']<=g['max_median_execution_drawdown'],
           'precision':r['precision'] is not None and r['precision']>=g['min_precision'],
           'recall':r['recall'] is not None and r['recall']>=g['min_recall'],
           'symbol_uplift_ci':m.get('symbol_ci95',[None])[0] is not None and m['symbol_ci95'][0]>0,
           'time_uplift_ci':m.get('quarter_ci95',[None])[0] is not None and m['quarter_ci95'][0]>0}
    if train_uplift is not None: tests['train_uplift']=train_uplift>0
    if final_p is not None: tests['holm_p']=final_p<.05
    return [k for k,v in tests.items() if not v]


def select(out):
    if (out/'selection_frozen.json').exists(): raise FileExistsError('selection_is_immutable')
    p=read(out/'protocol.json'); frozen={'created_at':datetime.now(timezone.utc).isoformat(),
        'protocol_sha256':sha(out/'protocol.json'),'audit_sha256':sha(out/'data_audit.json'),'markets':{},
        'basis':'development symbols only; KR/US independent; reserved-symbol outcomes never loaded'}
    for market in ('KR','US'):
        train=analyze(out,market,0); val=analyze(out,market,1,np.asarray(train['vol_cuts']))
        roles={}
        for role in ('early','exit'):
            g=p['selection_'+role]; target=int(g['target']*100)
            tr={r['signal']:r for r in train['rows'] if r['target']==target}; candidates=[]
            for r in val['rows']:
                if r['target']!=target: continue
                training_uplift=tr[r['signal']]['matched'].get('uplift')
                fails=passed_gates(r,g,training_uplift if training_uplift is not None else -1)
                candidates.append({'signal':r['signal'],'j':r['signal_index'],'target':target,'failures':fails,
                    'validation_n':r['n'],'validation_precision':r['precision'],'validation_recall':r['recall'],
                    'median_execution_dd':r['execution_dd']['median'],'validation_uplift':r['matched'].get('uplift')})
            qualified=[r for r in candidates if not r['failures']]
            pool=qualified or [r for r in candidates if r['validation_n']>=200 and r['median_execution_dd'] is not None]
            if role=='early': pool.sort(key=lambda r:(r['median_execution_dd'],-(r['validation_uplift'] or -9),-(r['validation_recall'] or 0),r['signal']))
            else: pool.sort(key=lambda r:(-(r['validation_precision'] or 0),r['median_execution_dd'],-(r['validation_recall'] or 0),r['signal']))
            roles[role]={'qualified_count':len(qualified),'candidate':pool[0] if pool else None,
                         'status':'development_gates_passed' if qualified else 'DESCRIPTIVE ONLY: no candidate passed predeclared gates',
                         'all_candidates':candidates}
        frozen['markets'][market]={'vol_cuts':train['vol_cuts'],'roles':roles,
                                  'train_summary_sha256':sha(out/f'{market}_s0_core_summary.json'),
                                  'validation_summary_sha256':sha(out/f'{market}_s1_core_summary.json')}
    write(out/'selection_frozen.json',frozen); (out/'selection_frozen.sha256').write_text(sha(out/'selection_frozen.json'),encoding='ascii')
    print(json.dumps({m:v['roles'] for m,v in frozen['markets'].items()},ensure_ascii=False)[:4000],flush=True)


def holm(values):
    order=np.argsort(values); adjusted=np.zeros(len(values)); previous=0.
    for k,i in enumerate(order): previous=max(previous,min(1,(len(values)-k)*values[i])); adjusted[i]=previous
    return adjusted.tolist()


def retrospective(a,events,market,out):
    records=[]
    for target in (10,20):
        hit=events[(events[f'target{target}_offset']>0)&(events[f'target{target}_offset']<=126)]
        es=set(hit['event']); boundaries=np.r_[0,np.flatnonzero(np.diff(a['event']))+1,len(a['event'])]
        eventrows={int(a['event'][s]):np.arange(s,e) for s,e in zip(boundaries[:-1],boundaries[1:]) if a['event'][s] in es}
        for _,e in hit.iterrows():
            ix=eventrows[int(e['event'])]; seq=[]
            for j in range(len(SIGNALS)):
                found=ix[((a['first'][ix]>>j)&1).astype(bool)]
                if len(found):
                    t=found[0]; seq.append((int(a['offset'][t]),NAMES[j]))
                    records.append({'market':market,'event':int(e['event']),'symbol':e['symbol'],'target':target,
                        'peak_date':e['peak_date'],'signal':NAMES[j],'offset':int(a['offset'][t]),
                        'drawdown':float(a['dd'][t]),'before_target':bool(a['offset'][t]<e[f'target{target}_offset']),
                        'target_offset':int(e[f'target{target}_offset'])})
    frame=pd.DataFrame(records); frame.to_csv(out/f'{market}_retrospective_target_events.csv',index=False)
    return len(frame)


def test(out,frozen_from=None):
    origin=frozen_from or out; frozen=read(origin/'selection_frozen.json'); fhash=sha(origin/'selection_frozen.json')
    if fhash!=(origin/'selection_frozen.sha256').read_text(): raise ValueError('selection_changed')
    if sha(out/'protocol.json')!=frozen['protocol_sha256']: raise ValueError('protocol_changed')
    report={'created_at':datetime.now(timezone.utc).isoformat(),'selection_origin':str(origin),'selection_sha256_before':fhash,
            'lookback':read(out/'data_audit.json')['lookback'],'markets':{},'recommendation_rule':'Never retune after viewing final results'}
    for market in ('KR','US'):
        fm=frozen['markets'][market]; only=None if frozen_from is None else [r['candidate']['j'] for r in fm['roles'].values() if r['candidate']]
        mainresult=analyze(out,market,2,np.asarray(fm['vol_cuts']),only=only)
        a,events,p=load_market(out,market,2)
        roles={}; rawps=[]
        for role,saved in fm['roles'].items():
            candidate=saved['candidate']
            if candidate is None: roles[role]={'status':'NO CANDIDATE'}; rawps.append(1); continue
            r=next(r for r in mainresult['rows'] if r['signal']==candidate['signal'] and r['target']==candidate['target'])
            rawps.append(max(r['matched'].get('p_symbol',1),r['matched'].get('p_quarter',1)))
            roles[role]={'signal':candidate['signal'],'target':candidate['target'],'development_status':saved['status'],'test_result':r}
        adjusted=holm(rawps)
        for (role,r),pv in zip(roles.items(),adjusted):
            r['holm_p_within_market_two_roles']=pv
            if 'test_result' in r:
                failures=passed_gates(r['test_result'],p['selection_'+role],final_p=pv)
                if fm['roles'][role]['qualified_count']==0: failures.append('no_development_qualified_candidate')
                r['test_gate_failures']=failures; r['status']='VALIDATED CANDIDATE' if not failures else 'NOT VALIDATED'
        large=analyze(out,market,2,np.asarray(fm['vol_cuts']),cohort='largecap',only=[r['candidate']['j'] for r in fm['roles'].values() if r['candidate']])
        # Descriptive regime and volatility strata for frozen candidates only.
        subgroups=[]
        for role,r in roles.items():
            if 'test_result' not in r: continue
            j=NAMES.index(r['signal']); target=r['target']
            for key,labels in [('regime',[-1,0,1]),('below_ma200',[False,True])]:
                for value in labels:
                    b={k:v[a[key]==value] for k,v in a.items()}
                    # Recall of a subgroup needs an event landmark; only report
                    # signal conditional precision/timing here, not a spurious
                    # full-universe recall denominator.
                    ix=np.flatnonzero(((b['first']>>j)&1).astype(bool)&(b[f'delay{target}']!=-2))
                    delay=b[f'delay{target}'][ix]; yy=(delay>0)&(delay<=60)
                    subgroups.append({'role':role,'group':key,'value':str(value),'n':len(ix),'symbols':len(np.unique(b['symbol'][ix])),
                                      'precision_ci':interval(yy,b['symbol'][ix],b['quarter'][ix]),'execution_dd':distribution(b['exec_dd'][ix])})
            cuts=np.asarray(fm['vol_cuts']); q=np.searchsorted(cuts,a['vol'])
            for value in range(5):
                mask=q==value; ix=np.flatnonzero(mask&(((a['first']>>j)&1).astype(bool))&(a[f'delay{target}']!=-2))
                delay=a[f'delay{target}'][ix]; yy=(delay>0)&(delay<=60)
                subgroups.append({'role':role,'group':'volatility_quintile_training_cutoffs','value':str(value+1),'n':len(ix),
                                  'symbols':len(np.unique(a['symbol'][ix])),'precision_ci':interval(yy,a['symbol'][ix],a['quarter'][ix]),
                                  'execution_dd':distribution(a['exec_dd'][ix])})
        retro_count=retrospective(a,events,market,out) if frozen_from is None else 0
        report['markets'][market]={'roles':roles,'main_summary_file':f'{market}_s2_core_summary.json',
                                  'largecap_summary_file':f'{market}_s2_largecap_summary.json','subgroups':subgroups,
                                  'retrospective_rows':retro_count,'main_events':mainresult['events'],'main_symbols':mainresult['symbols']}
    report['selection_sha256_after']=sha(origin/'selection_frozen.json'); write(out/'final_results.json',report)
    print('Final separate-market results saved',out/'final_results.json',flush=True)


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('command',choices=['prepare','select','test'])
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1]); parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--protocol',type=Path,default=Path(__file__).with_name('ma_warning_protocol.json'))
    parser.add_argument('--lookback',type=int)
    parser.add_argument('--frozen-from',type=Path)
    args=parser.parse_args()
    if args.command=='prepare': prepare(args.root,args.out,args.protocol,args.lookback)
    elif args.command=='select': select(args.out)
    else: test(args.out,args.frozen_from)


if __name__=='__main__': main()
