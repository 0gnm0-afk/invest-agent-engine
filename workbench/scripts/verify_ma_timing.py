"""Verify exhaustive segment accounting and independent daily state/capital replay."""
import argparse,sys,re
from pathlib import Path
from ma_timing_segments import ROOT,SOURCE,PAIRS,read,write,sha,load_arrays,flags,transactions,ledger,BASE
import numpy as np
import pandas as pd
sys.path.insert(0,str(ROOT/'tests'))
from test_ma_timing_segments import scalar_flags,scalar_ledger


def verify(out,report=None):
    sim=read(out/'simulation.json');meta=read(sim['source_prepared']);assert sha(sim['source_prepared'])==sim['source_sha256']
    assert sim['protocol_sha256']==sha(out/'protocol.json')==sha(ROOT/'scripts/ma_timing_protocol.json')
    records={(r['market'],r['symbol']):r for r in meta['stocks']}
    for r in meta['input_hashes']:assert sha(r['path'])==r['sha256']
    for r in records.values():assert sha(r['path'])==r['sha256']
    paths=pd.read_csv(out/'paths.csv.gz',dtype={'symbol':str});trades=pd.read_csv(out/'trades.csv.gz',dtype={'symbol':str})
    assert not paths.duplicated(['market','symbol','pair','fraction','period','mode']).any()
    assert not trades.duplicated(['market','symbol','pair','trade']).any()
    assert (trades.signal_date<trades.fill_date).all()
    paired=paths.pivot(index=['market','symbol','pair','fraction','period'],columns='mode',values='final')
    assert (paired['gross']+1e-9>=paired['base']).all()
    assert (paired['base']+1e-9>=paired['stress']).all()
    assert (paired['interest4']+1e-9>=paired['base']).all()
    result=read(out/'results.json');sample=[];nsegments=0;replays=0;prefixes=0;scalar_flags_checked=0;all_chain_checks=0;table_checks=0;action_checks=0
    for market in ('KR','US'):
        seg=pd.read_csv(out/f'{market}_segments.csv.gz',dtype={'symbol':str})
        nsegments+=len(seg)
        assert not seg.duplicated(['symbol','pair','segment']).any()
        assert (seg.price_return>-1).all() and (seg.sessions>=0).all()
        np.testing.assert_allclose(seg.price_return,seg.end_price/seg.start_price-1,atol=1e-12)
        ordinary=seg[~seg.initial];assert (ordinary.start_signal<ordinary.start_date).all()
        ended=seg[seg.completed];assert (ended.end_signal<ended.end_date).all()
        full=paths[(paths.market==market)&(paths.period=='full')]
        pathlookup={(r.symbol,r.pair,r.fraction,r.mode):r for r in full.itertuples()}
        for (symbol,pair),g in seg.groupby(['symbol','pair'],sort=False):
            g=g.sort_values('segment')
            assert g.initial.sum()==1 and g.initial.iloc[0]
            assert (~g.completed).sum()==1 and not g.completed.iloc[-1]
            assert (g.end_i.to_numpy()[:-1]==g.start_i.to_numpy()[1:]).all()
            assert (g.phase.to_numpy()[:-1]!=g.phase.to_numpy()[1:]).all()
            hold=pathlookup[symbol,pair,1.,'gross'].hold_final
            np.testing.assert_allclose(np.exp(g.log_return.sum()),hold,rtol=1e-9)
            gross=pathlookup[symbol,pair,1.,'gross'].final
            np.testing.assert_allclose(np.exp(g.loc[g.phase=='hold','log_return'].sum()),gross,rtol=1e-9)
            net=pathlookup[symbol,pair,1.,'base'];sc,bc=BASE[market]
            np.testing.assert_allclose(net.final,gross*(1-sc)**net.sells/(1+bc)**net.buys,rtol=1e-9)
            all_chain_checks+=1
        names=sorted(seg.symbol.unique());chosen={names[i] for i in np.linspace(0,len(names)-1,10,dtype=int)}
        chosen|=set(ordinary.assign(magnitude=lambda x:x.price_return.abs()).nlargest(8,'magnitude').symbol)
        for symbol in sorted(chosen):
            z=load_arrays(records[market,symbol]['path']);actualflags=flags(z);ind=scalar_flags(z)
            for k in ind:np.testing.assert_array_equal(ind[k],actualflags[k]);scalar_flags_checked+=len(ind[k])
            for pair in PAIRS:
                for period,start in [('full','2016-01-01'),('oos','2023-04-01')]:
                    for frac in (1.,.5):
                        expected,begin,values=scalar_ledger(z,ind,pair,frac,*BASE[market],start=start)
                        if begin is None:continue
                        primary,b,_=transactions(z,actualflags,pair,start)
                        assert expected==primary and begin==b
                        daily=ledger(z,primary,b,frac,*BASE[market])['path']
                        np.testing.assert_allclose(daily,values,equal_nan=True,rtol=1e-9)
                        p=paths[(paths.market==market)&(paths.symbol==symbol)&(paths.pair==pair)&(paths.fraction==frac)&(paths.period==period)&(paths['mode']=='base')].iloc[0]
                        np.testing.assert_allclose(p.final,values[-1],rtol=1e-9)
                        if period=='full' and frac==1.:
                            t=trades[(trades.market==market)&(trades.symbol==symbol)&(trades.pair==pair)]
                            assert len(t)==len(expected)
                            for e,r in zip(expected,t.itertuples()):
                                assert e['kind']==r.kind and e['signal']==r.signal and e['fill']==r.fill
                                assert z['pos'][r.fill]-z['pos'][r.signal]==1
                                assert np.isclose(z['open'][r.fill],r.price);action_checks+=1
                        if period=='full':
                            cut=len(z['close'])//2;zz={k:v[:cut] for k,v in z.items()};ff=scalar_flags(zz)
                            ee,bb,vv=scalar_ledger(zz,ff,pair,frac,*BASE[market]);np.testing.assert_allclose(vv,values[:cut],equal_nan=True,rtol=1e-9);prefixes+=1
                        replays+=1
            sample.append(dict(market=market,symbol=symbol))
        for row in [r for r in result['summaries'] if r['market']==market]:
            x=seg[(seg.pair==row['pair'])&(seg.phase==row['phase'])&(~seg.initial)&seg.completed]
            if row['period']!='all_membership':x=x[x.eligible]
            if row['period'] in ('train','oos'):x=x[x.period==row['period']]
            assert len(x)==row['n'];assert np.isclose(x.price_return.mean(),row['price_return_mean']);table_checks+=1
        del seg
    for row in result['cohorts']:
        if row['mode'] in ('base_passive','base_stalezero'):continue
        x=paths[(paths.market==row['market'])&(paths.pair==row['pair'])&(paths.fraction==row['fraction'])&
                (paths.period==row['period'])&(paths['mode']==row['mode'])&paths.fixed_cohort]
        np.testing.assert_allclose(x.final.mean(),row['final'],rtol=1e-9)
        np.testing.assert_allclose(x.hold_final.mean(),row['hold_final'],rtol=1e-9);table_checks+=1
    missing=[]
    if report:
        for s in re.findall(r'\]\(([^)]+)\)',report.read_text(encoding='utf-8')):
            if s.startswith(('http:','https:','#')):continue
            p=(report.parent/s.strip('<>')).resolve()
            if p.name in ('verification.json','manifest.json'):continue
            if not p.exists():missing.append(str(p))
        assert not missing,missing
    receipt=dict(status='PASS',raw_hashes=len(meta['input_hashes']),array_hashes=len(records),segment_rows=nsegments,
        stock_pair_chain_checks=all_chain_checks,independent_signal_flags=scalar_flags_checked,
        independent_capital_paths=replays,prefix_paths=prefixes,independent_actions=action_checks,
        summary_and_cohort_checks=table_checks,sample=sample,
        scope='All interval price chains and full-sale cost chains; sampled independent new signals/state/cash. Does not prove liquidity, missing delisted returns or actual KR dividends.')
    write(out/'verification.json',receipt)
    files=[p for p in out.iterdir() if p.is_file() and p.name!='manifest.json']
    files += [ROOT/'scripts'/n for n in ['ma_timing_protocol.json','ma_timing_segments.py','ma_timing_statistics.py','verify_ma_timing.py','ma_timing_report.py']]
    files += [ROOT/'tests/test_ma_timing_segments.py']
    if report:files.append(report)
    write(out/'manifest.json',dict(files=[dict(path=str(p),sha256=sha(p)) for p in files if p.exists()],input_manifest=sim['source_prepared']))
    print({k:v for k,v in receipt.items() if k not in ('sample','scope')},flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);p.add_argument('--report',type=Path);a=p.parse_args();verify(a.out,a.report)
