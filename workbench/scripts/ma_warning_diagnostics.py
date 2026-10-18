"""Prespecified diagnostic views only; never change the frozen candidates."""
import argparse
from pathlib import Path
import numpy as np
import pandas as pd
from ma_warning_research import (read,write,sha,indicators,detect_events,prepared_stock,build_rows,
    load_market,signal_summary,NAMES,SIGNALS,test,distribution)


def parent_and_state(out):
    frozen=read(out/'selection_frozen.json'); results={}
    for market in ('KR','US'):
        a,events,p=load_market(out,market,2); selected=frozen['markets'][market]['roles']; rows=[]
        firstevents=events.sort_values(['peak_date','event']).drop_duplicates('parent',keep='first')
        keep=np.isin(a['event'],firstevents['event']); subset={k:v[keep] for k,v in a.items()}
        for role,saved in selected.items():
            r=saved['candidate']
            if r is None: continue
            dedup,ix=signal_summary(subset,firstevents,r['j'],r['target'],60)
            state,_=signal_summary(a,events,r['j'],r['target'],60,kind='first_state')
            primary,_=signal_summary(a,events,r['j'],r['target'],60)
            # Restricting labels to a recovery event is NOT done here: both
            # recovered and nonrecovered legs retain their known observation.
            rows.append({'role':role,'signal':r['signal'],'target':r['target'],'primary':primary,
                         'first_leg_per_parent':dedup,'first_confirmed_state':state})
        results[market]={'all_legs':len(events),'parents':events['parent'].nunique(),
                         'first_legs':len(firstevents),'rows':rows}
        # Chronology only within realized -20% legs; not a predictive sample.
        retro=pd.read_csv(out/f'{market}_retrospective_target_events.csv')
        basic=['close_below_ma5__c1','close_below_ma10__c1','close_below_ma20__c1',
               'ma5_below_ma10__c1','ma5_below_ma20__c1','ma10_below_ma20__c1']
        sequences=[]
        for eid,g in retro[(retro['target']==20)&retro['signal'].isin(basic)].groupby('event'):
            g=g.sort_values(['offset','signal']); sequences.append({'event':int(eid),
                'order':' > '.join(g['signal']),'offsets':','.join(map(str,g['offset'])),
                'simultaneous_pairs':int((g['offset'].diff()==0).sum())})
        pd.DataFrame(sequences).to_csv(out/f'{market}_retrospective_signal_order.csv',index=False)
        results[market]['most_common_orders']=pd.Series([r['order'] for r in sequences]).value_counts().head(5).to_dict()
    write(out/'parent_state_diagnostics.json',results)


def prepare_peak_sensitivity(root,primary,out):
    from saved_us_history import SavedHistory
    if (out/'data_audit.json').exists(): raise FileExistsError('secondary_data_already_exists')
    p=read(primary/'protocol.json'); frozen=read(primary/'selection_frozen.json'); sample=pd.read_csv(primary/'sample.csv')
    out.mkdir(parents=True,exist_ok=True); write(out/'protocol.json',p)
    sample[sample['reserved']].to_csv(out/'sample.csv',index=False)
    audit={'created_at':'after frozen primary selection; sensitivity only','lookback':p['peak_sensitivity_lookback'],
           'protocol_sha256':sha(out/'protocol.json'),'primary_audit_sha256':sha(primary/'data_audit.json'),
           'source_records':[],'exclusions':{}}
    for market in ('KR','US'):
        h=SavedHistory(root/'runtime'/f'{market.lower()}-history/active.json',market)
        syms=pd.read_csv(primary/f'{market}_symbols.csv'); chosen=syms[syms['reserved']]
        mf=pd.read_csv(primary/f'{market}_market_proxy.csv'); chunks={}; events=[]; failures={}
        for count,(_,row) in enumerate(chosen.iterrows()):
            symbol=row['symbol']; stock={**h.stocks[market+':'+symbol],
                'core':bool(row['core']),'reserved':True,'current_largecap':bool(row['current_largecap'])}
            bars,q,record=prepared_stock(root,h,stock,p); audit['source_records'].append(record)
            if len(bars)<400: continue
            f,dates=indicators(bars); es=detect_events(f['close'],p['peak_sensitivity_lookback']); stock['_quality']=q
            a,rows,excluded=build_rows(f,dates,es,stock,int(row['symbol_id']),mf,p,len(events))
            for k,v in a.items(): chunks.setdefault(k,[]).append(v)
            events+=rows
            for k,v in excluded.items(): failures[k]=failures.get(k,0)+v
            if (count+1)%100==0: print('peak126',market,count+1,flush=True)
        np.savez_compressed(out/f'{market}_risk.npz',**{k:np.concatenate(v) for k,v in chunks.items()})
        pd.DataFrame(events).to_csv(out/f'{market}_events.csv',index=False)
        syms.to_csv(out/f'{market}_symbols.csv',index=False)
        audit[market]={'risk_sha256':sha(out/f'{market}_risk.npz'),'events_sha256':sha(out/f'{market}_events.csv'),'events':len(events)}
        audit['exclusions'][market]=failures
    write(out/'data_audit.json',audit); test(out,primary)


if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--root',type=Path,required=True); parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--secondary',type=Path); args=parser.parse_args()
    if not (args.out/'final_results.json').exists(): raise ValueError('final_primary_evaluation_required_first')
    parent_and_state(args.out)
    if args.secondary: prepare_peak_sensitivity(args.root,args.out,args.secondary)
