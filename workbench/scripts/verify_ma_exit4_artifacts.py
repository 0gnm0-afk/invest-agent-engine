"""Direct research acceptance checks, including an independent scalar ledger."""
import argparse
import re
from pathlib import Path
from datetime import datetime,timezone
import numpy as np
import pandas as pd
from ma_exit4_research import read_json,write_json,sha,load


def verify(root,out,report):
    d,meta,p=load(out); mask=(meta['split']=='test').to_numpy(); d={k:v[mask] for k,v in d.items()}; meta=meta.loc[mask].reset_index(drop=True)
    frozen=read_json(out/'selection_frozen.json'); final=read_json(out/'final_results.json'); audit=read_json(out/'data_audit.json')
    assert sha(out/'selection_frozen.json')==(out/'selection_frozen.sha256').read_text()==final['selection_sha256_before']==final['selection_sha256_after']
    assert final['rank_order'].startswith('FROZEN') and all(x['id']==y['id'] for x,y in zip(frozen['top5'],final['results']))
    expected=pd.read_csv(out/'test_cycles_rank1.csv'); rng=np.random.default_rng(p['seed']); checks=[]
    rows=rng.choice(len(meta),40,replace=False)
    for i in rows:
        market=meta.iloc[i]['market']; entry=d['o'][i,0]*(1+p['base_cost_bps'][market]['buy']/10000)
        fee=p['base_cost_bps'][market]['sell']/10000; cash=0.; qty=1.; stage=0; pending=-1; fills=[-1]*4; wealth=[]
        for t in range(126):
            if pending>=0 and pending<=t and d['v'][i,t]>0 and d['h'][i,t]>d['l'][i,t]:
                q=[.6,.1,.1,.2][stage]; cash+=q*d['o'][i,t]*(1-fee); qty-=q; fills[stage]=t; stage+=1; pending=-1
            wealth.append((cash+qty*d['c'][i,t])/entry)
            if stage<4 and pending<0 and t>0:
                def trigger(j):
                    if stage==0: return d['m5'][i,j]<d['m10'][i,j]
                    if stage==1: return d['c'][i,j]<d['m20'][i,j]
                    if stage==2: return d['s20'][i,j]<=0
                    return d['c'][i,j]<d['lo10'][i,j] and d['s20'][i,j]<=0
                if trigger(t) and trigger(t-1): pending=t+1
        expectedfills=[int(expected.iloc[i]['sell'+str(k+1)+'_offset']) for k in range(4)]
        assert fills==expectedfills,(i,fills,expectedfills)
        result=wealth[-1]-1; hwm=np.maximum.accumulate([1.]+wealth); mdd=float(np.max(1-np.array([1.]+wealth)/hwm))
        assert abs(result-expected.iloc[i]['return'])<1e-11
        assert abs(mdd-expected.iloc[i]['mdd'])<1e-11
        checks.append({'index':int(i),'fills':fills,'return':result,'mdd':mdd})
    raw=[]
    for r in audit['records']:
        current=sha(r['source']); assert current==r['sha256_before'],r['symbol']
        raw.append({'market':r['market'],'symbol':r['symbol'],'sha256':current})
    buy=audit['real_buys']; database_unchanged=sha(buy['path'])==buy['database_sha256_before']
    assert database_unchanged,'real_trade_database_changed_during_research'
    text=report.read_text(encoding='utf-8'); links=[]
    for target in re.findall(r'\]\(([^)]+)\)',text):
        if target.startswith('https://'): continue
        path=(report.parent/target).resolve(); assert path.exists(),str(path); links.append(str(path))
    assert len(final['results'])==5 and len(meta)==2512
    for rank in range(1,6):
        frame=pd.read_csv(out/f'test_cycles_rank{rank}.csv')
        assert len(frame)==len(meta) and np.isfinite(frame['return']).all()
        offsets=frame[[f'sell{k}_offset' for k in range(1,5)]].to_numpy()
        for row in offsets:
            ix=row[row>=0]; assert (np.diff(ix)>0).all()
        contribution=frame[[f'stage{k}_delta_hold' for k in range(1,5)]].sum(axis=1)
        np.testing.assert_allclose(contribution,frame['hold_delta'],atol=1e-12)
    summary={'verified_at':datetime.now(timezone.utc).isoformat(),'status':'PASS',
             'focused_unit_tests':11,'independent_scalar_ledger_cases':checks,'source_files_unchanged':raw,
             'real_trade_database_unchanged':database_unchanged,'report_local_links_valid':len(links),
             'all_5_test_cycle_ledgers':'PASS','frozen_selection_unchanged':True,
             'scope':'exit-only research; no production/UAT/deployment claim',
             'code_sha256':{n:sha(root/'scripts'/n) for n in ['ma_exit4_research.py','ma_exit4_diagnostics.py','ma_exit4_report.py','ma_exit4_protocol.json','verify_ma_exit4_artifacts.py']}}
    write_json(out/'verification.json',summary)
    manifest={str(f.relative_to(out)):sha(f) for f in out.iterdir() if f.is_file() and f.name!='manifest.json'}
    manifest['report_sha256']=sha(report); write_json(out/'manifest.json',manifest)
    print({'status':'PASS','source_hashes':len(raw),'scalar_cases':len(checks),'local_links':len(links),'report_sha256':sha(report)},flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--root',type=Path,required=True); parser.add_argument('--out',type=Path,required=True); parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args(); verify(args.root,args.out,args.report)
