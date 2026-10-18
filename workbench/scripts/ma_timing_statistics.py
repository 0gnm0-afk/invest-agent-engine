"""Segment distributions and chronological wealth comparisons, no fitted winner."""
import argparse
from pathlib import Path
from ma_timing_segments import read,write,sha,PAIRS,mdd,SOURCE
from ma_reentry_statistics import crossed_bootstrap,km
import numpy as np
import pandas as pd

SEED=20261018


def summarize_segments(g,inference=False):
    out=dict(n=len(g),stocks=g.symbol.nunique(),gap=int(g.gap_spanning.sum()))
    if not len(g):return out
    r=g.price_return
    out.update(positive=float((r>1e-12).mean()),negative=float((r< -1e-12).mean()),
               equal=float((r.abs()<=1e-12).mean()),positive_mean=float(r[r>1e-12].mean()),negative_mean=float(r[r< -1e-12].mean()),
               equal_stock_return=float(g.groupby('symbol').price_return.mean().mean()),
               long60=float((g.sessions>=60).mean()),drawdown10=float((g.adverse>=.1).mean()),
               drawdown20=float((g.adverse>=.2).mean()),giveback5=float(((g.adverse>=.05)&(g.price_return>=0)).mean()),
               giveback5_conditional=float((g.loc[g.adverse>=.05,'price_return']>=0).mean()))
    for k in ('price_return','log_return','sessions','days','adverse','favorable','close_mdd','gain50','gain100'):
        v=g[k].dropna().to_numpy()
        if not len(v):continue
        out[k+'_mean']=float(v.mean())
        for label,q in [('p05',.05),('q25',.25),('median',.5),('q75',.75),('p95',.95)]:out[k+'_'+label]=float(np.quantile(v,q))
    if inference:out['return_inference']=crossed_bootstrap(g,g.price_return,family=36)
    return out


def read_segments(out,market):
    g=pd.read_csv(out/f'{market}_segments.csv.gz',dtype={'symbol':str})
    return g


def analyze(out):
    sim=read(out/'simulation.json');assert sim['protocol_sha256']==sha(out/'protocol.json')
    summaries=[];conditions=[];annual=[];censor=[];year_out=[];counts=[]
    for m in ('KR','US'):
        g=read_segments(out,m)
        for pair in PAIRS:
            for phase in ('hold','avoid'):
                sub=g[(g.pair==pair)&(g.phase==phase)]
                main=sub[(~sub.initial)&sub.completed&sub.eligible]
                allcomplete=sub[(~sub.initial)&sub.completed]
                counts.append(dict(market=m,pair=pair,phase=phase,total=len(sub),initial=int(sub.initial.sum()),
                    terminal=int((~sub.completed).sum()),eligible_complete=len(main),all_complete=len(allcomplete),
                    excluded_cap=int((~allcomplete.eligible).sum())))
                for period in ('all','train','oos','all_membership'):
                    x=allcomplete if period=='all_membership' else main if period=='all' else main[main.period==period]
                    if len(x):summaries.append(dict(market=m,pair=pair,phase=phase,period=period,**summarize_segments(x,period=='oos')))
                eligible=sub[(~sub.initial)&sub.eligible]
                surv=km(eligible.rename(columns={'sessions':'wait_signal'})) if len(eligible) else {}
                terminal=sub[~sub.completed]
                censor.append(dict(market=m,pair=pair,phase=phase,terminal=len(terminal),
                    terminal_return_mean=float(terminal.price_return.mean()),terminal_days_mean=float(terminal.days.mean()),
                    terminal_stale=int(terminal.stale_end.sum()),**{'km_'+k:v for k,v in surv.items()}))
                for year,x in main.groupby('year'):
                    annual.append(dict(market=m,pair=pair,phase=phase,year=int(year),**summarize_segments(x)))
                for period in ('all','oos'):
                    x=main if period=='all' else main[main.period=='oos']
                    for dim in ('group','vol','regime'):
                        for group,cell in x.groupby(dim):
                            conditions.append(dict(market=m,pair=pair,phase=phase,period=period,dimension=dim,group=group,**summarize_segments(cell)))
                for year in sorted(main.year.unique()):
                    x=main[main.year!=year]
                    year_out.append(dict(market=m,pair=pair,phase=phase,omitted_year=int(year),n=len(x),
                                         price_return_mean=float(x.price_return.mean()),log_return_mean=float(x.log_return.mean())))
            print('segment statistics',m,pair,flush=True)
        del g
    for name,rows in [('segment_summary',summaries),('conditions',conditions),('annual_segments',annual),('censoring',censor),('segment_counts',counts),('leave_one_year_out',year_out)]:
        pd.DataFrame([{k:v for k,v in r.items() if not isinstance(v,dict)} for r in rows]).to_csv(out/(name+'.csv'),index=False)
    pathrows=[];p=pd.read_csv(out/'paths.csv.gz',dtype={'symbol':str})
    for keys,sub in p.groupby(['market','pair','fraction','period','mode']):
        old=sub[sub.years>=1];row=dict(zip(['market','pair','fraction','period','mode'],keys))
        row.update(stocks=len(sub),one_year=len(old),mean_relative=float(sub.relative.mean()),median_relative=float(sub.relative.median()),
            win=float((sub.relative>0).mean()),p05_relative=float(sub.relative.quantile(.05)),p95_relative=float(sub.relative.quantile(.95)),
            cagr_diff=float((old.cagr-old.hold_cagr).mean()),mean_mdd=float(sub.mdd.mean()),hold_mean_mdd=float(sub.hold_mdd.mean()),
            mdd_improvement=float((sub.hold_mdd-sub.mdd).mean()),mean_hold_fraction=float(sub.hold_fraction.mean()),
            mean_exposure=float(sub.mean_exposure.mean()),median_trades_per_year=float(old.trades_per_year.median()),
            mean_trades_per_year=float(old.trades_per_year.mean()),end_cash_fraction=float((sub.terminal_cash>1e-12).mean()),
            mean_cash_weight=float((sub.terminal_cash/sub.final).mean()),eligible_fraction=float(sub.eligible_fraction.mean()),
            mean_passive_relative=float((sub.final/sub.passive_final-1).mean()),median_passive_relative=float((sub.final/sub.passive_final-1).median()))
        pathrows.append(row)
    pd.DataFrame(pathrows).to_csv(out/'path_summary.csv',index=False)
    co=[];ca=[];concentration=[]
    for m in ('KR','US'):
        for period in ('full','oos'):
            frame=pd.read_csv(out/f'{m}_{period}_cohort.csv');dates=pd.to_datetime(frame.date);years=(dates.iloc[-1]-dates.iloc[0]).days/365.25
            for pair in PAIRS:
                for frac in (1.,.5):
                    for mode in ('gross','base','stress','income','interest4','initial_held','delay2','base_passive','base_stalezero'):
                        key=pair+'__'+str(int(frac*100))+'__'+mode
                        v=frame[key].to_numpy();b=frame['hold_income' if mode=='income' else 'hold_stalezero' if mode=='base_stalezero' else 'hold'].to_numpy()
                        rv=np.diff(np.log(np.r_[1.,v]));rb=np.diff(np.log(np.r_[1.,b]));delta=rv-rb
                        row=dict(market=m,pair=pair,fraction=frac,period=period,mode=mode,start=frame.date.iloc[0],end=frame.date.iloc[-1],years=years,
                            final=v[-1],hold_final=b[-1],relative=v[-1]/b[-1]-1,cagr=v[-1]**(1/years)-1,hold_cagr=b[-1]**(1/years)-1,
                            mdd=mdd(v),hold_mdd=mdd(b),up_capture=float(rv[rb>0].sum()/rb[rb>0].sum()),
                            down_capture=float(rv[rb<0].sum()/rb[rb<0].sum()),annual_log_relative=float(np.log(v[-1]/b[-1])/years))
                        if mode=='base':
                            q=pd.Series(delta).groupby(dates.dt.to_period('Q')).sum().to_numpy();rng=np.random.default_rng(SEED)
                            boot=rng.choice(q,size=(4000,len(q)),replace=True).sum(axis=1)/years
                            row['ci95_low'],row['ci95_high']=np.quantile(boot,[.025,.975])
                            row['family_low'],row['family_high']=np.quantile(boot,[.025/36,1-.025/36]);row['quarters']=len(q)
                            pv=1.;pb=1.;year_logs=[]
                            for year,ix in frame.groupby(dates.dt.year).groups.items():
                                j=max(ix);r=v[j]/pv-1;br=b[j]/pb-1
                                ca.append(dict(market=m,pair=pair,fraction=frac,period=period,year=int(year),strategy_return=r,hold_return=br,difference=r-br,
                                               log_relative=mathlog(v[j]/pv)-mathlog(b[j]/pb)))
                                year_logs.append((int(year),mathlog(v[j]/pv)-mathlog(b[j]/pb)));pv=v[j];pb=b[j]
                            for year,value in year_logs:concentration.append(dict(market=m,pair=pair,fraction=frac,period=period,omitted_year=year,
                                relative_without_year=float(np.exp(sum(x[1] for x in year_logs)-value)-1)))
                        co.append(row)
    pd.DataFrame(co).to_csv(out/'cohort_summary.csv',index=False);pd.DataFrame(ca).to_csv(out/'cohort_annual.csv',index=False)
    pd.DataFrame(concentration).to_csv(out/'cohort_leave_year_out.csv',index=False)
    write(out/'results.json',dict(protocol_sha256=sim['protocol_sha256'],summaries=summaries,paths=pathrows,cohorts=co,counts=counts,censoring=censor))
    print('timing statistics complete',len(summaries),len(pathrows),len(co),flush=True)


def mathlog(x):return float(np.log(x))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);a=p.parse_args();analyze(a.out)
