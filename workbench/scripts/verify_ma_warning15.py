"""Independent saved-data checks; no network or operating imports."""
import argparse,re
from pathlib import Path
from urllib.parse import unquote
import numpy as np
import pandas as pd
from ma_warning_research import read,write,sha,load_market,matching_covariates,distribution


def verify(out,report):
    frozen=read(out/'selection_frozen.json'); source=Path(frozen['source']); audit=read(source/'data_audit.json'); checks={}
    records=audit['source_records']; failures=[r['path'] for r in records if sha(r['path'])!=r['sha256']]
    assert not failures,failures; checks['raw_source_hashes_unchanged']=len(records)
    assert sha(audit['old_audit_path'])==audit['old_audit_sha256']
    assert sha(audit['cap_snapshot'])==audit['cap_snapshot_sha256']
    old=read(audit['old_audit_path']); oldset={(r['market'],r['symbol']) for r in old['records']}
    sample=pd.read_csv(source/'sample.csv'); newset=set(zip(sample.market,sample.symbol))
    assert not oldset&newset; checks['prior800_overlap']=0
    assert sha(out/'selection_frozen.json')==(out/'selection_frozen.sha256').read_text()
    assert frozen['protocol_sha256']==sha(out/'protocol.json')
    assert frozen['source_audit_sha256']==sha(source/'data_audit.json')
    final=read(out/'final_results.json'); assert final['selection_hash_before']==final['selection_hash_after']==sha(out/'selection_frozen.json')
    checks['frozen_selection_unchanged']=True
    count=0; scalars=0; matched_count=0; target_late=0; subgroup=[]
    sourcelook={(r['market'],r['symbol']):r for r in records}
    for market in ('KR','US'):
        events=pd.read_csv(source/f'{market}_events.csv'); assert events.event.is_unique
        assert events.loc[events.split==2,'reserved'].all() and not events.loc[events.split!=2,'reserved'].any()
        assert not set(events.loc[events.split==2,'symbol_id'])&set(events.loc[events.split!=2,'symbol_id'])
        for cohort in ('largecap','core'):
            a,ev,_=load_market(source,market,2,cohort); metadata=ev.set_index('event')
            cuts=np.array(frozen['markets'][market][cohort]['vol_cuts']); keys=matching_covariates(a,cuts)
            for kind in ('first','first_state'):
                saved=read(out/f'{market}_s2_{cohort}_{kind}.json'); archive=np.load(out/f'{market}_s2_{cohort}_{kind}_matches.npz')
                for r in saved['rows']:
                    j=r['signal_index']; ix=np.flatnonzero((((a[kind]>>j)&1)>0)&(a['delay15']!=-2))
                    assert len(ix)==r['n'] and len(np.unique(a['event'][ix]))==len(ix)
                    assert np.array_equal(ix,archive[f'j{j}_treated']); count+=len(ix)
                    target_late+=int(((((a[kind]>>j)&1)>0)&(a['delay15']==-2)).sum())
                    ci=archive[f'j{j}_controls']; ti=ix
                    for k in range(ci.shape[1]):
                        ok=ci[:,k]>=0; t=ti[ok]; c=ci[ok,k]; matched_count+=len(t)
                        assert (a['symbol'][t]!=a['symbol'][c]).all()
                        assert (a['event'][t]!=a['event'][c]).all()
                        assert (((a['state'][c]>>j)&1)==0).all() and (a['delay15'][c]!=-2).all()
                        assert (keys[t,:-1]==keys[c,:-1]).all()
                        assert (np.abs(a['dd'][t]-a['dd'][c])<=.005+1e-12).all()
                        assert (np.abs(a['market_ret20'][t]-a['market_ret20'][c])<=.03+1e-12).all()
                        ratio=a['vol'][c]/a['vol'][t]; assert ((ratio>=.8)&(ratio<=1.25)).all()
            # Independent scalar raw-price replay for10 deterministic rows/cohort/market.
            choices=np.linspace(0,len(a['event'])-1,10,dtype=int)
            for i in choices:
                e=metadata.loc[int(a['event'][i])]; raw=read(sourcelook[(market,e.symbol)]['path'])
                packet=raw.get('data',raw); bars=packet['series']['bars'] if 'series' in packet else packet['bars']
                bydate={b['date']:b for b in bars}; ds=str(np.datetime64(int(a['date'][i]),'D')); ns=str(np.datetime64(int(a['next_date'][i]),'D'))
                assert ns>ds; peak=bydate[e.peak_date]['close']; close=bydate[ds]['close']; nb=bydate[ns]
                assert np.isclose(peak,e.peak_price) and np.isclose(1-close/peak,a['dd'][i])
                if np.isfinite(a['exec_dd'][i]): assert np.isclose(1-nb['open']/peak,a['exec_dd'][i])
                scalars+=1
            # Descriptive regime/volatility diagnostics for fixed candidates only.
            for z in final['markets'][market][cohort]['records']:
                c=z['candidate']; ix=np.flatnonzero((((a[c['kind']]>>c['j'])&1)>0)&(a['delay15']!=-2))
                for typ,groups in [('regime',a['regime']),('vol_quintile',np.searchsorted(cuts,a['vol']))]:
                    for group in np.unique(groups[ix]):
                        ii=ix[groups[ix]==group]; y=(a['delay15'][ii]>0)&(a['delay15'][ii]<=60)
                        subgroup.append({'market':market,'cohort':cohort,'candidate':c['id'],'type':typ,'group':int(group),
                            'n':len(ii),'symbols':len(np.unique(a['symbol'][ii])),'precision':float(y.mean()),
                            'median_exec_dd':distribution(a['exec_dd'][ii])['median']})
    pd.DataFrame(subgroup).to_csv(out/'fixed_candidate_subgroups.csv',index=False)
    checks.update(warning_rows_verified=count,matched_pairs_verified=matched_count,raw_scalar_replays=scalars,
                  post_target_warning_rows_excluded=target_late,subject_split_disjoint=True)
    missing=[]
    for link in re.findall(r'\]\(([^)]+)\)',report.read_text(encoding='utf-8')):
        if link.startswith(('http:','https:','#')): continue
        path=(report.parent/unquote(link.strip('<>'))).resolve()
        if path.name in ('verification.json','manifest.json'): continue
        if not path.exists(): missing.append(str(path))
    assert not missing,missing; checks['report_local_links_valid']=True
    write(out/'verification.json',{'status':'PASS','checks':checks,'scope':'offline research artifacts; operating systems never mutated'})
    files=[p for p in out.rglob('*') if p.is_file() and p.name!='manifest.json']
    files += [report]+[Path(__file__).with_name(n) for n in ('ma_warning_research.py','ma_warning15_research.py','ma_warning15_diagnostics.py','ma_warning15_report.py','verify_ma_warning15.py')]
    write(out/'manifest.json',{'files':[{'path':str(p),'sha256':sha(p)} for p in files]})
    print(checks,flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--out',type=Path,required=True); p.add_argument('--report',type=Path,required=True)
    a=p.parse_args(); verify(a.out,a.report)
