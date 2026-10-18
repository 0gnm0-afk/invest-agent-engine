"""User-requested15% early-warning revision. Preserve all v1 artifacts."""
import argparse
from datetime import datetime,timezone
from pathlib import Path
import numpy as np
from ma_warning_research import (read,write,sha,load_market,SIGNALS,NAMES,matching_covariates,
    control_bank,match_indices,matched_statistics,signal_summary,holm)


def analyze15(source,out,market,split,cohort,kind,cuts=None,only=None):
    a,events,_=load_market(source,market,split,'largecap' if cohort=='largecap' else 'core')
    if cuts is None: cuts=np.quantile(a['vol'][np.isfinite(a['vol'])&(a['vol']>0)],[.2,.4,.6,.8])
    keys=matching_covariates(a,cuts); eligible=a['delay15']!=-2; bank=control_bank(a,keys,eligible)
    rows=[]; archive={}; indices=range(len(SIGNALS)) if only is None else sorted(set(only))
    for j in indices:
        r,ix=signal_summary(a,events,j,15,60,kind=kind)
        ti,ci=match_indices(a,j,eligible,keys,bank,first_kind=kind)
        assert np.array_equal(ti,ix)
        r['matched']=matched_statistics(a,ti,ci,15)
        r['candidate_id']=NAMES[j]+'__'+kind
        rows.append(r); archive[f'j{j}_treated']=ti.astype(np.int32); archive[f'j{j}_controls']=ci
    extra=[]
    for r in rows:
        for h in (20,):
            x,_=signal_summary(a,events,r['signal_index'],15,h,kind=kind,with_ci=False); extra.append(x)
    result={'market':market,'split':split,'cohort':cohort,'kind':kind,'vol_cuts':np.asarray(cuts).tolist(),
            'symbols':events['symbol_id'].nunique(),'events':len(events),'parents':events['parent'].nunique(),
            'target_event_counts':{str(t):int(((events[f'target{t}_offset']>0)&(events[f'target{t}_offset']<=126)).sum()) for t in (10,15,20)},
            'rows':rows,'sensitivity':extra}
    stem=f'{market}_s{split}_{cohort}_{kind}'
    write(out/(stem+'.json'),result); np.savez_compressed(out/(stem+'_matches.npz'),**archive)
    print('15% analyzed',stem,'nconditions',len(rows),flush=True)
    return result


def gates(r,p,training=None,adjusted_p=None):
    g=p['selection15']; m=r['matched']; errors=[]
    checks={'n':r['n']>=g['min_signals'],'symbols':r['symbols']>=g['min_symbols'],
        'common_support':m['coverage']>=g['min_match_coverage'],
        'signal_early':r['signal_dd']['median'] is not None and r['signal_dd']['median']<=g['max_median_signal_dd'],
        'execution_early':r['execution_dd']['median'] is not None and r['execution_dd']['median']<=g['max_median_exec_dd'],
        'precision':r['precision'] is not None and r['precision']>=g['min_precision'],
        'recall':r['recall'] is not None and r['recall']>=g['min_recall'],
        'executable_recall':r['executable_recall'] is not None and r['executable_recall']>=g['min_executable_recall'],
        'uplift_size':m.get('uplift') is not None and m['uplift']>=g['min_uplift'],
        'stock_ci':m['symbol_ci95'][0] is not None and m['symbol_ci95'][0]>0,
        'time_ci':m['quarter_ci95'][0] is not None and m['quarter_ci95'][0]>0}
    if training is not None: checks['train_uplift']=training>0
    if adjusted_p is not None: checks['holm_p']=adjusted_p<.05
    return [k for k,v in checks.items() if not v]


def score(r):
    return .35*(r['recall'] or 0)+.30*(r['precision'] or 0)+.20*np.clip((r['matched'].get('uplift') or 0)/.1,-1,1)+.15*(1-(r['execution_dd']['median'] or 0)/.15)


def select15(source,out,protocol):
    if (out/'selection_frozen.json').exists(): raise FileExistsError('v2_selection_is_immutable')
    out.mkdir(parents=True,exist_ok=True); p=read(protocol); write(out/'protocol.json',p)
    frozen={'created_at':datetime.now(timezone.utc).isoformat(),'source':str(source),
            'source_audit_sha256':sha(source/'data_audit.json'),'protocol_sha256':sha(out/'protocol.json'),
            'revision':'human requested primary15%; v1 preserved; no claim of virgin validation','markets':{}}
    for market in ('KR','US'):
        frozen['markets'][market]={}
        for cohort in ('largecap','core'):
            alltrain=[]; allval=[]; cuts=None
            for kind in ('first','first_state'):
                tr=analyze15(source,out,market,0,cohort,kind,cuts); cuts=np.array(tr['vol_cuts'])
                va=analyze15(source,out,market,1,cohort,kind,cuts)
                alltrain+=tr['rows']; allval+=va['rows']
            train={r['candidate_id']:r for r in alltrain}; candidates=[]
            for r in allval:
                t=train[r['candidate_id']]['matched'].get('uplift')
                candidates.append({'id':r['candidate_id'],'signal':r['signal'],'kind':r['kind'],'j':r['signal_index'],
                    'score':float(score(r)),'failures':gates(r,p,t if t is not None else -1),
                    'validation_n':r['n'],'validation_symbols':r['symbols'],
                    'validation_precision':r['precision'],'validation_recall':r['recall'],
                    'validation_exec_dd':r['execution_dd']['median'],'validation_uplift':r['matched'].get('uplift')})
            qualified=[c for c in candidates if not c['failures']]
            basic=[c for c in candidates if c['validation_n']>=p['selection15']['min_signals'] and c['validation_exec_dd'] is not None and c['validation_exec_dd']<=.06]
            pool=sorted(qualified,key=lambda r:(-r['score'],r['id']))+sorted([c for c in basic if c not in qualified],key=lambda r:(-r['score'],r['id']))
            chosen=pool[:3]
            frozen['markets'][market][cohort]={'qualified_count':len(qualified),'candidates':chosen,'all_candidates':candidates,'vol_cuts':cuts.tolist()}
    write(out/'selection_frozen.json',frozen); (out/'selection_frozen.sha256').write_text(sha(out/'selection_frozen.json'),encoding='ascii')
    print('v2 candidate selection frozen',sha(out/'selection_frozen.json'),flush=True)


def test15(out):
    f=read(out/'selection_frozen.json'); source=Path(f['source']); p=read(out/'protocol.json')
    before=sha(out/'selection_frozen.json')
    assert before==(out/'selection_frozen.sha256').read_text()
    assert sha(source/'data_audit.json')==f['source_audit_sha256'] and sha(out/'protocol.json')==f['protocol_sha256']
    result={'created_at':datetime.now(timezone.utc).isoformat(),'primary_target':15,'selection_hash_before':before,'markets':{}}
    for market,cohorts in f['markets'].items():
        result['markets'][market]={}
        for cohort,selection in cohorts.items():
            candidates=selection['candidates']; kinds=['first','first_state']; allrows=[]; meta=None
            for kind in kinds:
                # Full22-condition table is descriptive only. Only the frozen
                # three candidates below can receive the final validation label.
                a=analyze15(source,out,market,2,cohort,kind,np.array(selection['vol_cuts']))
                allrows+=a['rows']; meta=a
            rawp=[]; records=[]
            for c in candidates:
                r=next(r for r in allrows if r['candidate_id']==c['id']); m=r['matched']
                rawp.append(max(m.get('p_symbol',1),m.get('p_quarter',1))); records.append({'candidate':c,'test':r})
            adjusted=holm(rawp)
            for r,pv in zip(records,adjusted):
                errors=gates(r['test'],p,adjusted_p=pv)
                if r['candidate']['failures']: errors.append('not_qualified_at_selection')
                r.update(holm_p=pv,failures=errors,status='SUPPORTED EARLY WARNING' if not errors else 'NOT VALIDATED')
            result['markets'][market][cohort]={'records':records,'target_event_counts':meta['target_event_counts'] if meta else {},
                'events':meta['events'] if meta else 0,'symbols':meta['symbols'] if meta else 0,'parents':meta['parents'] if meta else 0}
    result['selection_hash_after']=sha(out/'selection_frozen.json'); write(out/'final_results.json',result)
    print('15% final test complete',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('command',choices=['select','test']); parser.add_argument('--source',type=Path)
    parser.add_argument('--out',type=Path,required=True); parser.add_argument('--protocol',type=Path,default=Path(__file__).with_name('ma_warning15_protocol.json')); args=parser.parse_args()
    if args.command=='select': select15(args.source,args.out,args.protocol)
    else: test15(args.out)
