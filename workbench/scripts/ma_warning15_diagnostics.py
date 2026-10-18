"""Frozen15% candidates: peak sensitivity, parent dependence and trace rows."""
import argparse
from pathlib import Path
import numpy as np
import pandas as pd
from ma_warning_research import read,write,load_market,signal_summary
from ma_warning15_research import analyze15


def run(out,secondary):
    frozen=read(out/'selection_frozen.json'); source=Path(frozen['source']); results={}
    for market,cohorts in frozen['markets'].items():
        results[market]={}
        for cohort,selection in cohorts.items():
            a,events,_=load_market(source,market,2,cohort)
            first=events.sort_values(['peak_date','event']).drop_duplicates('parent')
            keep=np.isin(a['event'],first['event']); subset={k:v[keep] for k,v in a.items()}
            rows=[]; trace=[]; chron=[]
            for kind in ('first','first_state'):
                js=[c['j'] for c in selection['candidates'] if c['kind']==kind]
                if not js: continue
                sensdir=out/'peak126'; sensdir.mkdir(exist_ok=True)
                sens=analyze15(secondary,sensdir,market,2,cohort,kind,np.array(selection['vol_cuts']),only=js)
                for c in selection['candidates']:
                    if c['kind']!=kind: continue
                    parent,_=signal_summary(subset,first,c['j'],15,60,kind=kind)
                    primary,ix=signal_summary(a,events,c['j'],15,60,kind=kind)
                    rows.append({'id':c['id'],'parent_first':parent,'peak126':next(r for r in sens['rows'] if r['signal_index']==c['j'])})
                    for i in ix:
                        trace.append({'candidate':c['id'],'event':int(a['event'][i]),'symbol_id':int(a['symbol'][i]),
                            'signal_date':str(np.datetime64(int(a['date'][i]),'D')),'offset':int(a['offset'][i]),
                            'drawdown':float(a['dd'][i]),'next_open_drawdown':float(a['exec_dd'][i]),
                            'next_date':str(np.datetime64(int(a['next_date'][i]),'D')),'delay15':int(a['delay15'][i]),
                            'recovered60':bool(a['recover60'][i]),'long_gap':bool(a['long_gap'][i])})
            # Descriptive chronology includes only realized15% cases. Never a prediction sample.
            target=events[(events.target15_offset>0)&(events.target15_offset<=126)]
            for _,e in target.iterrows():
                for j in range(12):
                    ii=np.flatnonzero((a['event']==e.event)&(((a['first']>>j)&1)>0))
                    for i in ii:
                        chron.append({'event':int(e.event),'j':j,'offset':int(a['offset'][i]),'dd':float(a['dd'][i]),
                            'target_offset':int(e.target15_offset),'before_target':bool(a['offset'][i]<e.target15_offset)})
            cf=pd.DataFrame(chron); cf.to_csv(out/f'{market}_{cohort}_retrospective15.csv',index=False)
            orders=[]
            if len(cf):
                for eid,g in cf[(cf.j%2==0)&cf.before_target].groupby('event'):
                    g=g.sort_values(['offset','j']); orders.append({'event':eid,'order':'>'.join(g.j.astype(str)),
                        'offsets':','.join(g.offset.astype(str)),'simultaneous':int((g.offset.diff()==0).sum())})
            pd.DataFrame(orders).to_csv(out/f'{market}_{cohort}_signal_order15.csv',index=False)
            pd.DataFrame(trace).to_csv(out/f'{market}_{cohort}_candidate_trace.csv',index=False)
            results[market][cohort]={'events':len(events),'parents':len(first),'rows':rows,
                'common_orders':pd.Series([r['order'] for r in orders],dtype=str).value_counts().head(5).to_dict()}
    write(out/'diagnostics.json',results)


if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--out',type=Path,required=True); p.add_argument('--secondary',type=Path,required=True)
    args=p.parse_args(); run(args.out,args.secondary)
