"""Offline, reproducible four-tranche exit research. Never mutates operating stores.

Use prepare, optimize, then evaluate. Selection is committed to disk BEFORE test
performance is calculated. Input histories reuse the workbench's audited loader.
"""
from __future__ import annotations
import argparse
import hashlib
import itertools
import json
import math
from pathlib import Path
import sqlite3
import sys
from datetime import datetime, timezone
import numpy as np
import pandas as pd


FAMILIES = ['classic','slope_guard','delayed','price_ladder','support_break','slope_protect']
HOLDS = ['none','early5','structure10','golden5']
METRICS = ['return','return_robust','hold_delta','giveback_pp','giveback_fraction',
           'missed','defense','mdd','sales','sold','success5','success20','success60',
           'expect5','expect20','expect60','cost','capture']
FEATURES = ['c','o','h','l','v','m5','m10','m20','s5','s5prev','s20','gap','gapprev',
            'lo5','lo10','hi5','lowrise','cprev','m5prev','m10prev','m20prev','peak63']


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
    return h.hexdigest()


def write_json(path, data):
    def nullable(v):
        if isinstance(v,dict): return {str(k):nullable(x) for k,x in v.items()}
        if isinstance(v,(list,tuple)): return [nullable(x) for x in v]
        if isinstance(v,np.generic): return nullable(v.item())
        if isinstance(v,float) and math.isnan(v): return None
        return v
    Path(path).write_text(json.dumps(nullable(data), ensure_ascii=False, indent=2, allow_nan=False),encoding='utf-8')


def read_json(path): return json.loads(Path(path).read_bytes())


def features(bars):
    df = pd.DataFrame(bars)
    c = df['close'].astype(float)
    out = {k:df[v].to_numpy(float) for k,v in [('c','close'),('o','open'),('h','high'),('l','low'),('v','volume')]}
    for n in (5,10,20,60): out['m'+str(n)] = c.rolling(n).mean().to_numpy()
    for n in (5,20): out['s'+str(n)] = out['m'+str(n)]-pd.Series(out['m'+str(n)]).shift(1 if n==5 else 5).to_numpy()
    out['s5prev'] = pd.Series(out['s5']).shift(1).to_numpy()
    out['gap'] = (out['m10']-out['m5'])/out['m10']
    out['gapprev'] = pd.Series(out['gap']).shift(1).to_numpy()
    for n in (5,10): out['lo'+str(n)] = df['low'].rolling(n).min().shift(1).to_numpy()
    out['hi5'] = df['high'].rolling(5).max().shift(1).to_numpy()
    out['peak63'] = c.rolling(63).max().to_numpy()
    out['lowrise'] = (df['low'].rolling(3).min()>df['low'].shift(3).rolling(3).min()).to_numpy()
    for k in ('c','m5','m10','m20'): out[k+'prev'] = pd.Series(out[k]).shift(1).to_numpy()
    q = ((out['m5']>out['m10']) & (out['m10']>out['m20']) & (out['m20']>out['m60'])
         & (out['s20']>0) & (c/c.shift(63)>=1.2).to_numpy()
         & (c>=0.9*c.rolling(63).max()).to_numpy())
    out['entry'] = pd.Series(q).rolling(3).sum().eq(3).to_numpy()
    return out,df['date'].to_numpy(dtype='U10')


def audit_real_records(root, mother):
    path=root/'runtime/chart-workspaces.sqlite3'
    result={'path':str(path),'database_sha256_before':sha(path),'cycle_count':0,'fill_count':0}
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'trade_cycles' in tables:
            result['cycle_count']=db.execute('SELECT count(*) FROM trade_cycles').fetchone()[0]
            events=[json.loads(r[0]) for r in db.execute('SELECT payload FROM trade_cycle_events')]
            result['fill_count']=sum(e.get('kind')=='fill' for e in events)
    result['legacy_diaries']=[{'path':str(p),'sha256':sha(p)} for p in sorted((mother/'vault/20_매매일지/일지').glob('*.md'))]
    result['usable_buy_cycles']=0
    result['reason']='No recorded fills in cycle DB; legacy three sale diaries explicitly lack complete buy dates/cost/quantity. Do not fabricate 20:30:50 purchases.'
    result['database_sha256_after']=sha(path)
    assert result['database_sha256_before']==result['database_sha256_after']
    return result


def prepare(root, out, protocol_path):
    from saved_us_history import SavedMarketHistories
    protocol=read_json(protocol_path)
    if (out/'data_audit.json').exists(): raise FileExistsError('prepared_output_is_immutable')
    out.mkdir(parents=True,exist_ok=True)
    write_json(out/'protocol.json',protocol)
    saved=SavedMarketHistories(root/'runtime')
    if any(h.error for h in saved.markets.values()): raise ValueError('audited_history_unavailable')
    report={'created_at':datetime.now(timezone.utc).isoformat(),'protocol_sha256':sha(out/'protocol.json'),
            'records':[],'real_buys':audit_real_records(root,Path(read_json(root/'config.json')['mother'])),
            'pointers':{},'candidate_universe':len(saved.stocks),'exclusions':{},'universe_bias':'CURRENT SURVIVORS ONLY'}
    selected=[]
    for m in ('KR','US'):
        p=root/'runtime'/f'{m.lower()}-history/active.json'
        report['pointers'][m]={'path':str(p),'sha256':sha(p),'value':read_json(p)}
        rows=[s for s in saved.stocks.values() if s['market']==m]
        rows.sort(key=lambda s:hashlib.sha256(('MA-EXIT4-v1|'+m+':'+s['symbol']).encode()).hexdigest())
        selected+=rows[:protocol['sample_per_market']]
    H=protocol['horizon']; T=H+protocol['followup']; chunks={k:[] for k in FEATURES}; meta=[]
    market_returns={'KR':[],'US':[]}; audit=[]
    for ix,stock in enumerate(selected):
        m=stock['market']; symbol=stock['symbol']; packet=saved.packet(stock)
        p=saved.markets[m].root/'series'/ (hashlib.sha256(symbol.encode()).hexdigest()+'.json')
        record={'market':m,'symbol':symbol,'source':str(p),'sha256_before':sha(p),'history':packet['history']}
        bars=packet.get('series',{}).get('bars',[])
        if not bars:
            record['excluded']='no_verified_bars'; audit.append(record); continue
        # Warmup data is used only for causal indicators, not as study entries.
        bars=[b for b in bars if '2008-01-01'<=b['date']<=protocol['data_end']]
        f,dates=features(bars)
        market_returns[m].append(pd.Series(pd.Series(f['c']).pct_change().to_numpy(),index=dates))
        n=len(dates); next_allowed=0; used=0
        longgap=np.r_[False,np.diff(dates.astype('datetime64[D]')).astype(int)>10]
        quality=packet['history'].get('quality',{})
        invalid=set(quality.get('excluded_dates',[]))
        # Keep pre-2009 calendar caveats outside the research window. Exclude
        # cycles touching actual missing/invalid sessions, and disclose attrition.
        gaps=sorted(set(quality.get('missing_sessions',[]))|invalid)
        for signal in np.flatnonzero(f['entry']):
            start=signal+1; end=start+T-1
            if start<next_allowed or end>=n or dates[start]<protocol['start']: continue
            if not (f['v'][start]>0 and f['h'][start]>f['l'][start]): continue
            split='train' if dates[end]<=protocol['train_end'] else (
                'validation' if dates[start]>protocol['train_end'] and dates[end]<=protocol['validation_end'] else (
                'test' if dates[start]>protocol['validation_end'] else None))
            if split is None: continue
            if any(dates[max(0,start-65)]<=g<=dates[end] for g in gaps):
                report['exclusions']['quality_gap_cycle']=report['exclusions'].get('quality_gap_cycle',0)+1
                next_allowed=start+H; continue
            if longgap[start:start+T].any():
                report['exclusions']['long_suspension_cycle']=report['exclusions'].get('long_suspension_cycle',0)+1
                next_allowed=start+H; continue
            for k in FEATURES: chunks[k].append(f[k][start:start+T])
            ma20=f['m20'][start-1]; s20=f['s20'][start-1]
            meta.append({'market':m,'symbol':symbol,'split':split,'start':dates[start],
                         'end':dates[start+H-1],'followup_end':dates[end],'year':int(dates[start][:4]),
                         'quarter':str(pd.Period(dates[start],freq='Q')),
                         'entry_price':float(f['o'][start]),'source':str(p)})
            used+=1; next_allowed=start+H
        record['cycles']=used; record['bars_research_warmup']=len(bars)
        record['sha256_after']=sha(p); assert record['sha256_before']==record['sha256_after']
        audit.append(record)
        if (ix+1)%50==0: print(f'prepare {ix+1}/{len(selected)}, cycles={len(meta)}',flush=True)
    regimes={}
    for m,rows in market_returns.items():
        daily=pd.concat(rows,axis=1).mean(axis=1).sort_index()
        idx=(1+daily.fillna(0)).cumprod(); ma=idx.rolling(200).mean()
        trend=np.where((idx>ma)&(ma>ma.shift(20)),'up',np.where((idx<ma)&(ma<ma.shift(20)),'down','sideways'))
        regimes[m]=dict(zip(idx.index,pd.Series(trend,index=idx.index).shift(1).fillna('unknown')))
    for row in meta: row['regime']=regimes[row['market']].get(row['start'],'unknown')
    np.savez_compressed(out/'episodes.npz',**{k:np.asarray(v,dtype=np.float64) for k,v in chunks.items()})
    pd.DataFrame(meta).to_csv(out/'episodes.csv',index=False)
    report['records']=audit; report['cycles']=len(meta); report['selected_symbols']=len(selected)
    report['split_counts']=pd.DataFrame(meta).groupby(['split','market']).size().to_dict()
    report['split_counts']={str(k):int(v) for k,v in report['split_counts'].items()}
    report['episodes_sha256']=sha(out/'episodes.npz'); report['metadata_sha256']=sha(out/'episodes.csv')
    report['regime_definition']='Current-sample equal-weight daily return proxy, 200-session SMA and20-session SMA direction; previous completed date only in report grouping; not official market index.'
    write_json(out/'data_audit.json',report)
    print(json.dumps({k:v for k,v in report.items() if k in ('cycles','split_counts','selected_symbols','exclusions')},ensure_ascii=False),flush=True)


def conditions(d,family):
    c,a,b,e=d['c'],d['m5'],d['m10'],d['m20']; down=d['s20']<=0
    below5=c<a; cross=a<b; below20=c<e
    if family=='classic': xs=[below5,cross,a<e,b<e]
    elif family=='slope_guard': xs=[below5&(d['s5']<0),cross,below20&down,(b<e)&down]
    elif family=='delayed': xs=[cross,a<e,b<e,below20&down]
    elif family=='price_ladder': xs=[below5,c<b,below20,below20&down]
    elif family=='support_break': xs=[cross,below20,down,(c<d['lo10'])&down]
    elif family=='slope_protect': xs=[below5,cross&(d['s5']<0),below20&down,(c<d['lo10'])&down]
    else: raise ValueError(family)
    return np.stack(xs,axis=2)


def recovery(d,policy):
    c=d['c']; up=d['s20']>0
    if policy=='none': return np.zeros_like(c,dtype=bool)
    if policy=='early5':
        return up & (c>d['m5']) & ((d['s5']>=0)|((d['s5']>d['s5prev'])&(d['gap']<d['gapprev'])))
    if policy=='structure10':
        return up & ((c>d['m10'])|((c>d['hi5'])&(d['lowrise']>0)))
    if policy=='golden5': return up & (d['m5']>d['m10']) & (c>d['m5'])
    raise ValueError(policy)


def timing(d,policy,H=126,lag=1):
    family,hold,confirm=policy
    n=len(d['c']); cond=conditions(d,family)[:,:H]; rec=recovery(d,hold)[:,:H]
    if confirm==2:
        cond[:,1:]=cond[:,1:]&cond[:,:-1].copy(); cond[:,0]=False
    stage=np.zeros(n,dtype=int); pending=np.full(n,-1,dtype=int); held=np.zeros(n,dtype=bool)
    expiry=np.full(n,-1,dtype=int); sells=np.full((n,4),-1,dtype=np.int32)
    holds=np.zeros(n,dtype=int); resumes=np.zeros(n,dtype=int); maxhold=10 if hold=='structure10' else 5
    for t in range(H):
        tradable=(d['v'][:,t]>0)&(d['h'][:,t]>d['l'][:,t])
        execute=(pending>=0)&(pending<=t)&tradable&(stage<4)
        ii=np.flatnonzero(execute); sells[ii,stage[ii]]=t; stage[ii]+=1; pending[ii]=-1
        active=(stage>0)&(stage<4)&(pending<0)
        hard=(d['c'][:,t]<d['lo5'][:,t])|((d['c'][:,t]<d['m20'][:,t])&(d['s20'][:,t]<=0))
        was=held.copy(); release=held&(hard|(t>=expiry))
        held[release]=False; resumes[release]+=1
        edge=rec[:,t] & (~rec[:,t-1] if t else True)
        arm=active&edge&(~held)&(~release)&(~hard)
        held[arm]=True; expiry[arm]=t+maxhold; holds[arm]+=1
        valid=(stage<4)&(pending<0)&(~held)
        trigger=cond[np.arange(n),t,np.minimum(stage,3)]
        # A hard breakdown releases a paused tranche even if its originally
        # assigned signal never arrives; no skipped or repeated slot.
        fire=valid&(trigger|(release&hard))
        pending[fire]=t+lag
    return sells,holds,resumes


def ratio_candidates(protocol,refine=False):
    w={tuple(x) for x in protocol['coarse_weights']}
    if refine:
        w|={tuple(10*x for x in c) for c in itertools.product(range(1,8),repeat=4) if sum(c)==10}
    return sorted(w)


def policy_id(p): return f'{p[0]}__{p[1]}__c{p[2]}'
def strategy_id(p,w): return policy_id(p)+'__'+ '-'.join(map(str,w))


def cycle_metrics(d,meta,sells,weights,protocol,cost_scale=1,H=None,trajectory=False):
    H=H or protocol['horizon']; n=len(meta); w=np.asarray(weights,dtype=float)/100
    market=meta['market'].to_numpy(); bc=np.array([protocol['base_cost_bps'][m]['buy'] for m in market])*cost_scale/10000
    sc=np.array([protocol['base_cost_bps'][m]['sell'] for m in market])*cost_scale/10000
    entry=d['o'][:,0]*(1+bc); close=d['c'][:,:H]; base=close/entry[:,None]
    wealth=base.copy(); missed=np.zeros(n); defense=np.zeros(n); cost=bc/(1+bc)
    expect=np.zeros((n,3)); success=np.zeros((n,3)); denominator=np.zeros(n)
    contributions=np.zeros((n,4)); valid=sells>=0
    for k,q in enumerate(w):
        ix=np.maximum(sells[:,k],0); gross=d['o'][np.arange(n),ix]; net=gross*(1-sc)
        past=np.arange(H)[None,:]>=ix[:,None]; past&=valid[:,k,None]
        wealth+=q*past*(net[:,None]-close)/entry[:,None]
        contributions[:,k]=q*valid[:,k]*(net-close[:,-1])/entry
        # Extrema evaluated only over the remaining COMMON cycle, no future
        # extension after early exits. At fill time use fill open, not prior bars.
        future=np.where(past,close,np.nan)
        high=np.max(np.where(past,close,-np.inf),axis=1)
        low=np.min(np.where(past,close,np.inf),axis=1)
        missed+=q*np.where(valid[:,k],np.maximum(high-gross,0)/entry,0)
        defense+=q*np.where(valid[:,k],np.maximum(gross-low,0)/entry,0)
        cost+=q*valid[:,k]*gross*sc/entry
        denominator+=q*valid[:,k]
        for j,h in enumerate((5,20,60)):
            jj=np.minimum(ix+h,d['c'].shape[1]-1)
            futurec=d['c'][np.arange(n),jj]
            expect[:,j]+=q*valid[:,k]*(net-futurec)/entry
            success[:,j]+=q*valid[:,k]*(net>futurec)
    returns=wealth[:,-1]-1; hold=base[:,-1]-1
    peak=np.maximum(1,np.max(base,axis=1)); giveback=peak-wealth[:,-1]
    highwater=np.maximum.accumulate(np.column_stack([np.ones(n),wealth]),axis=1)
    dd=np.max(1-np.column_stack([np.ones(n),wealth])/highwater,axis=1)
    fraction=np.divide(giveback,peak-1,out=np.full(n,np.nan),where=peak>=1.1)
    capture=np.divide(returns,peak-1,out=np.full(n,np.nan),where=peak>=1.1)
    success=np.divide(success,denominator[:,None],out=np.full_like(success,np.nan),where=denominator[:,None]>0)
    a=np.column_stack([returns,np.clip(returns,-1,3),returns-hold,giveback,fraction,missed,defense,dd,
                       valid.sum(axis=1),denominator,success,expect,cost,capture])
    assert a.shape==(n,len(METRICS))
    if not np.allclose(contributions.sum(axis=1),returns-hold,atol=1e-10): raise AssertionError('contribution_identity')
    return (a,contributions,wealth) if trajectory else a


def aggregate(a,meta,mask):
    frame=pd.DataFrame(a[mask],columns=METRICS); frame['key']=meta.loc[mask,'market'].to_numpy()+':'+meta.loc[mask,'symbol'].to_numpy()
    frame['market']=meta.loc[mask,'market'].to_numpy()
    return frame.groupby(['market','key'])[METRICS].mean().groupby(level=0).mean().mean().to_numpy()


def ranks(values,weights=(.35,.35,.30)):
    # reward returns, low MDD, and low forgone upside; never multiply
    # overlapping 'defense potential' and 'return advantage' as independent gains.
    df=pd.DataFrame({'r':values[:,1],'d':-values[:,7],'u':-values[:,5]})
    return df.rank(pct=True,method='average').to_numpy()@np.asarray(weights)


def load(out):
    protocol=read_json(out/'protocol.json'); audit=read_json(out/'data_audit.json')
    if sha(out/'episodes.npz')!=audit['episodes_sha256'] or sha(out/'episodes.csv')!=audit['metadata_sha256']:
        raise ValueError('prepared_input_changed')
    return dict(np.load(out/'episodes.npz')),pd.read_csv(out/'episodes.csv'),protocol


def optimize(out):
    d,meta,p=load(out)
    # Do not expose test OHLC arrays to any optimization calculation.
    mask=meta['split'].isin(['train','validation']).to_numpy(); d={k:v[mask] for k,v in d.items()}; meta=meta.loc[mask].reset_index(drop=True)
    train=(meta['split']=='train').to_numpy(); val=(meta['split']=='validation').to_numpy()
    allpolicies=list(itertools.product(FAMILIES,HOLDS,[1,2])); cache={}
    coarse=[]
    for i,pol in enumerate(allpolicies):
        cache[pol]=timing(d,pol,p['horizon'])[0]
        for w in ratio_candidates(p):
            a=cycle_metrics(d,meta,cache[pol],w,p); coarse.append((pol,w,aggregate(a,meta,train)))
        if i%8==0: print(f'coarse timing {i+1}/{len(allpolicies)}',flush=True)
    vals=np.stack([x[2] for x in coarse]); score=ranks(vals)
    selected_policies=[]
    for i in np.argsort(-score,kind='stable'):
        if coarse[i][0] not in selected_policies: selected_policies.append(coarse[i][0])
        if len(selected_policies)==6: break
    rows=[]; trainarr=[]; valarr=[]
    for pol in selected_policies:
        for w in ratio_candidates(p,True):
            a=cycle_metrics(d,meta,cache[pol],w,p)
            rows.append({'id':strategy_id(pol,w),'policy':list(pol),'weights':list(w)})
            trainarr.append(aggregate(a,meta,train)); valarr.append(aggregate(a,meta,val))
        print('refined',policy_id(pol),flush=True)
    trainarr=np.stack(trainarr); valarr=np.stack(valarr)
    ts=ranks(trainarr); vs=ranks(valarr); combined=.4*ts+.6*vs
    for i,r in enumerate(rows):
        r.update(training_score=float(ts[i]),validation_score=float(vs[i]),selection_score=float(combined[i]))
        r['complexity']=int(r['policy'][1]!='none')*3+int(r['policy'][2]==2)+int(r['policy'][0]!='classic')
    order=sorted(range(len(rows)),key=lambda i:(-round(combined[i],8),rows[i]['complexity'],rows[i]['id']))
    finalists=[]; seen=set()
    for i in order:
        pol=tuple(rows[i]['policy'])
        if pol not in seen: finalists.append(rows[i]); seen.add(pol)
        if len(finalists)==5: break
    sensitivity=[]
    for weights in p['sensitivity_weights']:
        s=.4*ranks(trainarr,weights)+.6*ranks(valarr,weights); best=int(np.argmax(s))
        sensitivity.append({'objective_weights':weights,'winner':rows[best]['id'],
                            'recommended_rank':int(1+sum(s>s[order[0]]))})
    # Rolling development-only performance, never final test used for tuning.
    walk=[]
    for fitend,start,end in [('2013-12-31','2014-01-01','2016-12-31'),('2016-12-31','2017-01-01','2018-12-31'),('2018-12-31','2019-01-01','2020-12-31')]:
        fm=(meta['followup_end']<=fitend).to_numpy(); vm=((meta['start']>=start)&(meta['followup_end']<=end)).to_numpy()
        aggfit=[]; aggval=[]
        for r in finalists:
            a=cycle_metrics(d,meta,cache[tuple(r['policy'])],r['weights'],p)
            aggfit.append(aggregate(a,meta,fm)); aggval.append(aggregate(a,meta,vm))
        k=int(np.argmax(ranks(np.stack(aggfit))))
        walk.append({'fit_end':fitend,'evaluate_start':start,'evaluate_end':end,'selected':finalists[k]['id'],
                     'n':int(vm.sum()),'selected_result':dict(zip(METRICS,np.asarray(aggval[k]).tolist())),
                     'note':'Development-only diagnostic on the final candidate set; not an independently nested out-of-sample test.'})
    pd.DataFrame([{**{k:v for k,v in r.items() if k not in ('policy','weights')},
                   **{'train_'+k:float(trainarr[i,j]) for j,k in enumerate(METRICS)},
                   **{'validation_'+k:float(valarr[i,j]) for j,k in enumerate(METRICS)}} for i,r in enumerate(rows)]).to_csv(out/'development_ranking.csv',index=False)
    frozen={'created_at':datetime.now(timezone.utc).isoformat(),'protocol_sha256':sha(out/'protocol.json'),
            'data_audit_sha256':sha(out/'data_audit.json'),'coarse_candidates':len(coarse),
            'refined_candidates':len(rows),'top5':finalists,'recommended_id':finalists[0]['id'],
            'sensitivity':sensitivity,'walk_forward_development':walk,'selection_basis':'train+validation ONLY; test not evaluated',
            'refined_policies':[list(x) for x in selected_policies]}
    write_json(out/'selection_frozen.json',frozen)
    (out/'selection_frozen.sha256').write_text(sha(out/'selection_frozen.json'),encoding='ascii')
    print(json.dumps({'frozen':str(out/'selection_frozen.json'),'recommended':finalists[0],'counts':[len(coarse),len(rows)]},ensure_ascii=False),flush=True)


def bootstrap_paired(a,meta,column,seed=20261016,B=2000,null=0.0):
    """Two marginal cluster CIs, retaining paired strategy-vs-baseline rows."""
    rng=np.random.default_rng(seed); result={}
    for cluster in ('symbol','quarter'):
        frame=pd.DataFrame({'v':a[:,column],'market':meta['market'],'cluster':meta[cluster]})
        if cluster=='symbol':
            groups=frame.groupby(['market','cluster'])['v'].mean(); marketvals=[g.to_numpy() for _,g in groups.groupby(level=0)]
            draws=np.zeros(B)
            for vals in marketvals:
                draws+=np.nanmean(vals[rng.integers(0,len(vals),(B,len(vals)))],axis=1)/len(marketvals)
            estimate=float(np.mean([np.nanmean(v) for v in marketvals])); clusters=len(groups)
        else:
            # Quarter block means retain contemporaneous cross-stock dependence;
            # unlike symbol weights, this deliberately balances time blocks.
            groups=frame.groupby(['cluster','market'])['v'].mean().groupby(level=0).mean()
            vals=groups.to_numpy()
            # Circular four-quarter blocks preserve adjacent entry-period
            # dependence from overlapping126-session holdings.
            starts=rng.integers(0,len(vals),(B,math.ceil(len(vals)/4)))
            indices=((starts[:,:,None]+np.arange(4))%len(vals)).reshape(B,-1)[:,:len(vals)]
            draws=np.nanmean(vals[indices],axis=1)
            estimate=float(np.nanmean(vals)); clusters=len(vals)
        se=float(np.std(draws,ddof=1)); pval=float(.5*math.erfc((estimate-null)/(max(se,1e-15)*math.sqrt(2))))
        result[cluster]={'estimate':estimate,'ci95':np.quantile(draws,[.025,.975]).tolist(),
                         'clusters':clusters,'one_sided_p_normal_cluster_approximation':pval,
                         'null_value':null,
                         'finite_replicates':int(np.isfinite(draws).sum()),
                         'block_quarters':4 if cluster=='quarter' else None}
    return result


def holm(ps):
    order=np.argsort(ps); adjusted=np.zeros(len(ps)); prev=0
    for j,i in enumerate(order): prev=max(prev,min(1,(len(ps)-j)*ps[i])); adjusted[i]=prev
    return adjusted.tolist()


def signal_statistics(d,meta,p):
    c=d['c']; definitions={
        'close_below5':(c<d['m5'])&(d['cprev']>=d['m5prev']),
        '5_below10':(d['m5']<d['m10'])&(d['m5prev']>=d['m10prev']),
        '5_below20':(d['m5']<d['m20'])&(d['m5prev']>=d['m20prev']),
        '10_below20':(d['m10']<d['m20'])&(d['m10prev']>=d['m20prev'])}
    records=[]; firsts={}; H=p['horizon']
    for name,trig in definitions.items():
        got=trig[:,:H].any(axis=1); first=np.argmax(trig[:,:H],axis=1); firsts[name]=np.where(got,first,-1)
        for i in np.flatnonzero(got):
            t=first[i]; current=c[i,t]; peak=max(d['peak63'][i,0],np.max(c[i,:t+1])); future=c[i,t+1:t+61]
            rec={'signal':name,'symbol':meta.iloc[i]['symbol'],'market':meta.iloc[i]['market'],
                 'cycle_start':meta.iloc[i]['start'],'offset':int(t),'drop_from_past_peak':current/peak-1,
                 'ma20_up':bool(d['s20'][i,t]>0),'max_up60':max(0,float(future.max()/current-1)),
                 'max_down60':min(0,float(future.min()/current-1)),
                 'recover_old_high60':bool(future.max()>=peak),'new_high60':bool(future.max()>peak),
                 'further_drop5pct60':bool(future.min()<=current*.95),'rebound5pct60':bool(future.max()>=current*1.05),
                 'terminal20':float(c[i,t+20]/current-1)}
            records.append(rec)
    raw=pd.DataFrame(records); raw.to_csv(p['_out']/'signal_events_test.csv',index=False)
    summary=[]
    for keys,g in raw.groupby(['signal','ma20_up']):
        for field in ['drop_from_past_peak','max_up60','max_down60','recover_old_high60','new_high60','further_drop5pct60','rebound5pct60','terminal20']:
            group=g.groupby(['market','symbol'])[field].mean().groupby(level=0).mean()
            summary.append({'signal':keys[0],'ma20_up':bool(keys[1]),'field':field,'n':len(g),
                            'symbols':int(g.groupby(['market','symbol']).ngroups),'mean_balanced':float(group.mean()),
                            'median':float(g[field].median()),'p10':float(g[field].astype(float).quantile(.1)),'p90':float(g[field].astype(float).quantile(.9))})
    pd.DataFrame(summary).to_csv(p['_out']/'signal_summary_test.csv',index=False)
    order=[]
    for i in range(len(meta)):
        events=sorted([(int(a[i]),k) for k,a in firsts.items() if a[i]>=0])
        order.append({'cycle_start':meta.iloc[i]['start'],'symbol':meta.iloc[i]['symbol'],
                      'order':' > '.join(k for _,k in events),
                      'gaps':','.join(str(events[j][0]-events[j-1][0]) for j in range(1,len(events))),
                      **{k:int(v[i]) for k,v in firsts.items()}})
    pd.DataFrame(order).to_csv(p['_out']/'signal_order_test.csv',index=False)
    return summary


def evaluate(out):
    frozen=read_json(out/'selection_frozen.json'); frozen_hash=sha(out/'selection_frozen.json')
    if frozen_hash!=(out/'selection_frozen.sha256').read_text(): raise ValueError('selection_changed_after_freeze')
    d,meta,p=load(out)
    if sha(out/'protocol.json')!=frozen['protocol_sha256'] or sha(out/'data_audit.json')!=frozen['data_audit_sha256']: raise ValueError('frozen_input_changed')
    test=(meta['split']=='test').to_numpy(); d={k:v[test] for k,v in d.items()}; meta=meta.loc[test].reset_index(drop=True)
    p['_out']=out; testall=np.ones(len(meta),dtype=bool)
    results=[]; base_sells=np.full((len(meta),4),-1,dtype=int)
    base=cycle_metrics(d,meta,base_sells,[25]*4,p)
    write_json(out/'baseline_test.json',dict(zip(METRICS,aggregate(base,meta,testall).tolist())))
    metrics_by_id={}; timing_by_id={}; ablations=[]
    for rank,r in enumerate(frozen['top5'],1):
        pol=tuple(r['policy']); sells,holds,resumes=timing(d,pol,p['horizon'])
        a,contrib,wealth=cycle_metrics(d,meta,sells,r['weights'],p,trajectory=True)
        metrics_by_id[r['id']]=a; timing_by_id[r['id']]=sells
        agg=aggregate(a,meta,testall); ci=bootstrap_paired(a,meta,METRICS.index('hold_delta'))
        ciret=bootstrap_paired(a,meta,0); cisucc=bootstrap_paired(a,meta,METRICS.index('success20'),null=.5)
        rec={**r,'pretest_rank':rank,'test_n':len(meta),'test_symbols':int(meta.groupby(['market','symbol']).ngroups),
             'test':dict(zip(METRICS,agg.tolist())),'ci_delta_hold':ci,'ci_return':ciret,'ci_success20':cisucc,
             'distribution':{k:{'mean_raw':float(np.nanmean(a[:,j])),'median_raw':float(np.nanmedian(a[:,j])),
                 'p05':float(np.nanquantile(a[:,j],.05)),'p95':float(np.nanquantile(a[:,j],.95))} for j,k in enumerate(METRICS)},
             'hold_episodes':int((holds>0).sum()),'resume_episodes':int((resumes>0).sum()),
             'complete4_fraction':float(np.mean((sells>=0).sum(axis=1)==4))}
        details=meta.copy()
        for j,k in enumerate(METRICS): details[k]=a[:,j]
        for j in range(4): details['sell'+str(j+1)+'_offset']=sells[:,j]; details['stage'+str(j+1)+'_delta_hold']=contrib[:,j]
        details['hold_count']=holds; details['resume_count']=resumes
        details.to_csv(out/f'test_cycles_rank{rank}.csv',index=False)
        stage=[]
        for k in range(4):
            exists=sells[:,k]>=0
            stage.append({'stage':k+1,'weight':r['weights'][k],'fills':int(exists.sum()),
                          'mean_offset_when_filled':float(np.mean(sells[exists,k])) if exists.any() else None,
                          'delta_hold_balanced':float(aggregate(np.repeat(contrib[:,k,None],len(METRICS),axis=1),meta,testall)[0])})
        rec['stage_contributions']=stage
        groups=[]
        for group in ('market','regime','year'):
            for value in sorted(meta[group].unique()):
                mask=(meta[group]==value).to_numpy()
                groups.append({'group':group,'value':str(value),'n':int(mask.sum()),
                               **dict(zip(METRICS,aggregate(a,meta,mask).tolist()))})
        rec['subgroups']=groups; rec['robustness']=[]
        for name,scale,lag,horizon in [('zero_cost',0,1,126),('double_cost',2,1,126),('one_session_late',1,2,126),('horizon63',1,1,63)]:
            ss=timing(d,pol,horizon,lag)[0]
            aa=cycle_metrics(d,meta,ss,r['weights'],p,cost_scale=scale,H=horizon)
            rec['robustness'].append({'scenario':name,**dict(zip(METRICS,aggregate(aa,meta,testall).tolist()))})
        if pol[1]!='none':
            unpaused=timing(d,(pol[0],'none',pol[2]),p['horizon'])[0]
            aa=cycle_metrics(d,meta,unpaused,r['weights'],p)
            delta=a[:,0]-aa[:,0]; held=holds>0
            rec['hold_ablation']={'same_rule_without_hold_delta_return':float(aggregate(np.repeat(delta[:,None],len(METRICS),axis=1),meta,testall)[0]),
                                 'hold_episodes':int(held.sum()),'hold_hurt_gt5pp':int((held&(delta<-.05)).sum()),
                                 'hold_helped_gt5pp':int((held&(delta>.05)).sum()),'worst_harm_pp':float(delta.min()),
                                 'median_on_hold_episodes':float(np.median(delta[held])) if held.any() else None}
            ablations.append({'id':r['id'],**rec['hold_ablation']})
        results.append(rec); print('test evaluated',rank,r['id'],flush=True)
    # Benchmarks keep same entry, execution constraints, and unfinished holdings.
    baseline_rows=[]
    for name,pol,w in [('equal_classic',('classic','none',1),[25]*4),('example_10_10_30_50',('classic','none',1),[10,10,30,50]),('example_10_20_30_40',('classic','none',1),[10,20,30,40]),('example_20_20_30_30',('classic','none',1),[20,20,30,30])]:
        aa=cycle_metrics(d,meta,timing(d,pol,p['horizon'])[0],w,p)
        baseline_rows.append({'name':name,**dict(zip(METRICS,aggregate(aa,meta,testall).tolist()))})
    for cluster in ('symbol','quarter'):
        adjusted=holm([r['ci_delta_hold'][cluster]['one_sided_p_normal_cluster_approximation'] for r in results])
        for r,v in zip(results,adjusted): r['ci_delta_hold'][cluster]['holm_p_5']=v
    chosen=metrics_by_id[frozen['recommended_id']]; paired=[]
    for r in results[1:]:
        delta=chosen[:,0]-metrics_by_id[r['id']][:,0]
        aa=np.repeat(delta[:,None],len(METRICS),axis=1)
        paired.append({'other':r['id'],'recommended_minus_other_return':bootstrap_paired(aa,meta,0)})
    # Remove top1% opportunity cycles consistently from both strategy/baseline;
    # diagnostic only, never use this test view to select a new strategy.
    peak=d['c'][:,:p['horizon']].max(axis=1)/d['o'][:,0]-1
    trim=peak<=np.quantile(peak,.99)
    extreme={'removed_top1pct_peak_cycles':int((~trim).sum()),'recommended':dict(zip(METRICS,aggregate(chosen,meta,trim).tolist())),
             'hold':dict(zip(METRICS,aggregate(base,meta,trim).tolist()))}
    sig=signal_statistics(d,meta,p)
    report={'selection_sha256_before':frozen_hash,'selection_sha256_after':sha(out/'selection_frozen.json'),
            'evaluated_at':datetime.now(timezone.utc).isoformat(),'rank_order':'FROZEN DEVELOPMENT RANK; never sorted by final test returns',
            'test_start':str(meta['start'].min()),'test_last_cycle_end':str(meta['end'].max()),
            'data_end':p['data_end'],'results':results,'baseline_comparisons':baseline_rows,
            'paired_finalists':paired,'extreme_sensitivity':extreme,'hold_ablations':ablations,
            'note':'No statistically independent superiority claim from selecting the most favorable test metric.'}
    write_json(out/'final_results.json',report)
    print('results saved',out/'final_results.json',flush=True)


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('command',choices=['prepare','optimize','evaluate'])
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1]); parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--protocol',type=Path,default=Path(__file__).with_name('ma_exit4_protocol.json'))
    args=parser.parse_args()
    if args.command=='prepare': prepare(args.root,args.out,args.protocol)
    elif args.command=='optimize': optimize(args.out)
    else: evaluate(args.out)


if __name__=='__main__': main()
