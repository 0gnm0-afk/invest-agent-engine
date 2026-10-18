"""Final-test-only explanatory diagnostics; NEVER used to tune selection."""
import argparse
import json
from pathlib import Path
import sys
import numpy as np
import pandas as pd
from ma_exit4_research import (FEATURES, METRICS, features, sha, read_json, write_json,
    load, timing, recovery, bootstrap_paired, cycle_metrics, aggregate)


def event_summary(frame,groupcols,fields):
    rows=[]
    for key,g in frame.groupby(groupcols):
        key=key if isinstance(key,tuple) else (key,)
        rec=dict(zip(groupcols,key)); rec.update(n=len(g),symbols=int(g.groupby(['market','symbol']).ngroups))
        for field in fields:
            arr=np.repeat(g[field].to_numpy(float)[:,None],len(METRICS),axis=1)
            ci=bootstrap_paired(arr,g.reset_index(drop=True),0,B=1000,null=.5 if pd.api.types.is_bool_dtype(g[field]) else 0.)
            rec[field]={'mean_symbol_balanced':ci['symbol']['estimate'],'ci95_symbol':ci['symbol']['ci95'],
                        'mean_quarter_balanced':ci['quarter']['estimate'],'ci95_quarter':ci['quarter']['ci95'],
                        'median_raw':float(g[field].median())}
        rows.append(rec)
    return rows


def contexts(out):
    audit=read_json(out/'data_audit.json'); records=[]; freqs=[]
    for ix,r in enumerate(audit['records']):
        if sha(r['source'])!=r['sha256_before']: raise ValueError('source_changed')
        v=read_json(r['source'])['data']; quality=r['history'].get('quality',{})
        excluded=set(quality.get('excluded_dates',[])); gaps=set(quality.get('missing_sessions',[]))|excluded
        bars=[b for b in v['bars'] if '2008-01-01'<=b['date']<='2026-09-30' and b['date'] not in excluded]
        if len(bars)<200: continue
        f,dates=features(bars); c=f['c']; N=len(c)
        recent=pd.Series(f['entry']).rolling(63,min_periods=1).max().shift(1).fillna(0).to_numpy()>0
        s=pd.Series(c); sideways=((s.rolling(63).max()/s.rolling(63).min()<=1.20)&
                    ((s/s.shift(63)-1).abs()<=.1)).to_numpy()&(np.abs(f['s20']/f['m20'])<=.01)
        labels=np.where(recent,'post_trend',np.where(sideways,'sideways','other'))
        signals={
            'close_below5':(c<f['m5'])&(f['cprev']>=f['m5prev']),
            '5_below10':(f['m5']<f['m10'])&(f['m5prev']>=f['m10prev']),
            '5_below20':(f['m5']<f['m20'])&(f['m5prev']>=f['m20prev']),
            '10_below20':(f['m10']<f['m20'])&(f['m10prev']>=f['m20prev'])}
        allowed=(dates>='2021-01-01')&(np.arange(N)+60<N)
        for context in ['post_trend','sideways']:
            mask=allowed&(labels==context); denom=int(mask.sum())
            if not denom: continue
            for name,trig in signals.items():
                freqs.append({'market':r['market'],'symbol':r['symbol'],'context':context,'signal':name,
                              'context_days':denom,'events':int((mask&trig).sum()),
                              'events_per100days':float(100*(mask&trig).sum()/denom)})
                last=-100
                for t in np.flatnonzero(mask&trig):
                    # Decluster events within a symbol/signal/context by20 sessions.
                    if t-last<20: continue
                    if any(dates[max(0,t-65)]<=g<=dates[t+60] for g in gaps): continue
                    if (np.diff(dates[t:t+61].astype('datetime64[D]')).astype(int)>10).any(): continue
                    last=t; future=c[t+1:t+61]; peak=f['peak63'][t]
                    records.append({'market':r['market'],'symbol':r['symbol'],'quarter':str(pd.Period(str(dates[t]),freq='Q')),
                                    'context':context,'signal':name,'date':str(dates[t]),
                                    'recover_high60':bool(future.max()>=peak),'fall5pct60':bool(future.min()<=c[t]*.95),
                                    'terminal20':float(c[t+20]/c[t]-1),'max_up60':float(future.max()/c[t]-1),
                                    'max_down60':float(future.min()/c[t]-1)})
        if (ix+1)%100==0: print('context diagnostic',ix+1,flush=True)
    frame=pd.DataFrame(records); frame.to_csv(out/'context_events_test.csv',index=False)
    freq=pd.DataFrame(freqs); freq.to_csv(out/'context_signal_frequency_test.csv',index=False)
    summaries=event_summary(frame,['context','signal'],['recover_high60','fall5pct60','terminal20'])
    frequency=freq.groupby(['context','signal','market'])['events_per100days'].mean().groupby(level=[0,1]).mean()
    for r in summaries: r['events_per100days']=float(frequency.loc[r['context'],r['signal']])
    write_json(out/'context_summary_test.json',summaries)


def stage_and_recovery(out):
    d,meta,p=load(out); mask=(meta['split']=='test').to_numpy(); d={k:v[mask] for k,v in d.items()}; meta=meta.loc[mask].reset_index(drop=True)
    frozen=read_json(out/'selection_frozen.json'); rec=frozen['top5'][0]; sells,_,_=timing(d,tuple(rec['policy']))
    stages=[]
    for k in range(4):
        rows=[]
        for i in np.flatnonzero(sells[:,k]>=0):
            t=sells[i,k]; sellcost=p['base_cost_bps'][meta.iloc[i]['market']]['sell']/10000
            net=d['o'][i,t]*(1-sellcost)
            rows.append({**meta.iloc[i].to_dict(),'stage':k+1,'success20':bool(net>d['c'][i,t+20]),
                         'expected20':float((net-d['c'][i,t+20])/(d['o'][i,0]*(1+p['base_cost_bps'][meta.iloc[i]['market']]['buy']/10000))),
                         'sold_offset':int(t)})
        frame=pd.DataFrame(rows); frame.to_csv(out/f'recommended_stage{k+1}_events.csv',index=False)
        if len(frame): stages+=event_summary(frame,['stage'],['success20','expected20'])
    write_json(out/'stage_statistics_test.json',stages)
    # This supplementary early-recovery candidate is chosen solely from the
    # already saved development ranking, not from any test result.
    ranking=pd.read_csv(out/'development_ranking.csv')
    earlyrows=ranking[ranking['id'].str.contains('__early5__')].sort_values(['selection_score','id'],ascending=[False,True])
    extras={}
    chosen=cycle_metrics(d,meta,sells,rec['weights'],p)
    hold=cycle_metrics(d,meta,np.full_like(sells,-1),[25]*4,p)
    allmask=np.ones(len(meta),dtype=bool)
    diff=np.repeat((hold[:,7]-chosen[:,7])[:,None],len(METRICS),axis=1)
    extras['mdd_reduction_ci']=bootstrap_paired(diff,meta,0)
    extras['expect20_ci']=bootstrap_paired(chosen,meta,METRICS.index('expect20'))
    equal=cycle_metrics(d,meta,sells,[25]*4,p)
    extras['equal_weights_same_timing']=dict(zip(METRICS,aggregate(equal,meta,allmask).tolist()))
    delta=np.repeat((chosen[:,0]-equal[:,0])[:,None],len(METRICS),axis=1)
    extras['optimized_minus_equal_return_ci']=bootstrap_paired(delta,meta,0)
    low=hold[:,0]>np.quantile(hold[:,0],.01)
    extras['exclude_worst1pct_hold_cycles']={'removed':int((~low).sum()),
        'chosen':dict(zip(METRICS,aggregate(chosen,meta,low).tolist())),
        'hold':dict(zip(METRICS,aggregate(hold,meta,low).tolist()))}
    if len(earlyrows):
        best=earlyrows.iloc[0]; policyid,weightid=best['id'].rsplit('__',1)
        family,holdpolicy,confirm=policyid.split('__'); pol=(family,holdpolicy,int(confirm[1:])); weights=list(map(int,weightid.split('-')))
        ss,hc,rc=timing(d,pol); aa=cycle_metrics(d,meta,ss,weights,p)
        ss0=timing(d,(pol[0],'none',pol[2]))[0]; aa0=cycle_metrics(d,meta,ss0,weights,p)
        harm=aa[:,0]-aa0[:,0]; delta=np.repeat(harm[:,None],len(METRICS),axis=1)
        extras['best_early_development_candidate']={'id':best['id'],'selection_score':float(best['selection_score']),
            'test':dict(zip(METRICS,aggregate(aa,meta,allmask).tolist())),
            'no_hold':dict(zip(METRICS,aggregate(aa0,meta,allmask).tolist())),
            'early_hold_delta_return_ci':bootstrap_paired(delta,meta,0),
            'hold_episodes':int((hc>0).sum()),'hurt_gt5pp':int(((hc>0)&(harm<-.05)).sum()),
            'helped_gt5pp':int(((hc>0)&(harm>.05)).sum()),'worst_harm_pp':float(harm.min())}
        pd.DataFrame({**{k:meta[k] for k in ['market','symbol','start']},'hold_count':hc,'hold_return':aa[:,0],'no_hold_return':aa0[:,0],'hold_effect':harm}).to_csv(out/'early_hold_ablation_test.csv',index=False)
    write_json(out/'supplementary_test.json',extras)
    early=recovery(d,'early5'); golden=(d['m5']>d['m10'])&(d['m5prev']<=d['m10prev'])
    weak=(d['c']<d['m5'])|(d['m5']<d['m10']); rows=[]
    for i in range(len(meta)):
        earlier=False; completed=False
        for t in range(p['horizon']):
            if earlier and golden[i,t]: completed=True
            if earlier and not completed and early[i,t] and d['m5'][i,t]<d['m10'][i,t]:
                nextgold=np.flatnonzero(golden[i,t+1:])
                rows.append({**meta.iloc[i].to_dict(),'early_offset':t,'golden_after_sessions':int(nextgold[0]+1) if len(nextgold) else -1,
                             'terminal20':float(d['c'][i,t+20]/d['c'][i,t]-1),
                             'positive20':bool(d['c'][i,t+20]>d['c'][i,t]),
                             'fall5pct60':bool(d['c'][i,t+1:t+61].min()<d['c'][i,t]*.95)})
                break
            earlier=earlier or weak[i,t]
    frame=pd.DataFrame(rows); frame.to_csv(out/'early_recovery_events_test.csv',index=False)
    if len(frame):
        frame['category']='pre_golden_early_recovery'
        summary=event_summary(frame,['category'],['positive20','terminal20','fall5pct60'])
        summary[0]['golden_never_in_available_followup']=int((frame['golden_after_sessions']<0).sum())
        summary[0]['median_lead_sessions_when_eventually_crossed']=float(frame.loc[frame['golden_after_sessions']>0,'golden_after_sessions'].median())
        write_json(out/'early_recovery_summary_test.json',summary)


if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--out',type=Path,required=True); args=parser.parse_args()
    if not (args.out/'final_results.json').exists(): raise ValueError('run_only_after_frozen_test')
    stage_and_recovery(args.out); contexts(args.out)
