"""Raw scalar replay and artifact invariants for descriptive drawdown statistics."""
import argparse,re,sys,math
from pathlib import Path
from urllib.parse import unquote
from ma_dd_describe import read,write,sha,prepare_kr,us_stock,features,segment_indices,SIGNALS
import numpy as np,pandas as pd

def verify(root,out,report):
 result=read(out/'results.json');assert result['protocol_sha256']==sha(out/'protocol.json')==sha(root/'scripts/ma_dd_protocol.json')
 receipt={'protocol_frozen':True};raw_count=0;cross_count=0;scalar_count=0;cap_count=0;volcells=0
 identity=read(out/'us_population.json')['source_identity'];pointer=Path(identity['history_pointer'])
 assert sha(pointer)==identity['pointer_sha256']
 assert sha(pointer.parent/read(pointer)['collection']/'universe.json')==identity['raw_universe_sha256']
 assert sha(out/'raw/us/tickers.json')==identity['SEC_tickers_sha256']
 corroboration=read(out/'kr_quote_corroboration.json');assert corroboration['status']=='PASS'
 for source in corroboration['sources']:assert sha(source['path'])==source['sha256']
 for market in ('KR','US'):
  audit=read(out/f'{market}_audit.json');assert sha(out/f'{market}_events.csv.gz')==audit['events_sha256']
  assert sha(out/f'{market}_risk_vol.npz')==audit['risk_sha256']
  for method,expected in audit['methods_sha256'].items(): assert sha(out/(f'{market}_events.csv.gz' if method=='primary' else f'{market}_{method}.csv.gz'))==expected
  if market=='KR':
   for x in read(out/'kr_sources.json')['files']: assert sha(x['path'])==x['sha256'];raw_count+=1
  else:
   for x in audit['stocks']:
    assert sha(x['price_path'])==x['price_sha256'] and sha(x['shares_path'])==x['shares_sha256'];raw_count+=2
  events=pd.read_csv(out/f'{market}_events.csv.gz',dtype={'symbol':str});events=events[(events['rank'].between(1,200))] if market=='KR' else events[events.cap_group.isin(['mega','large','mid'])]
  first=events[events.repeat==0]
  assert not first.duplicated(['symbol','segment','leg','signal']).any()
  assert (events.dd<=0).all() and (events.elapsed>=0).all() and (events.peak_date<=events.date).all()
  np.testing.assert_allclose(events.dd,100*(events.close/events.peak-1),atol=1e-9);cross_count+=len(first)
  if market=='US':
   assert (events.shares_filed<events.date).all() and (events.shares_end<=events.date).all() and events.shares_age.between(0,180).all()
   assert (events.loc[events.cap_group=='mega','cap']>=200e9).all()
   assert events.loc[events.cap_group=='large','cap'].between(10e9,200e9,inclusive='left').all()
   assert events.loc[events.cap_group=='mid','cap'].between(2e9,10e9,inclusive='left').all()
  cuts=result['markets'][market]['cuts'];vol=np.load(out/f'{market}_risk_vol.npz')['vol']
  for j,key in enumerate(('sigma','atrpct')): np.testing.assert_allclose(cuts[key],np.quantile(vol[:,j],[.25,.5,.75,.9]))
  table=result['markets'][market]['rows']
  for row in [x for x in table if x['condition']=='all']:
   for key in ('sigma','atrpct'):
    cells=[x for x in table if x['group']==row['group'] and x['signal']==row['signal']]
    assert row['n']==sum(x['n'] for x in cells if x['condition'] in [key+'_Q'+str(k) for k in range(1,5)]+[key+'_missing'])
    ns=[next(x['n'] for x in cells if x['condition']==key+'_geQ'+str(q)) for q in (50,75,90)]
    assert ns[0]>=ns[1]>=ns[2];volcells+=1
  # Small deterministic sample of whole symbol histories, independent scalar formulas.
  symbols=sorted(first.symbol.unique());chosen=[symbols[i] for i in np.linspace(0,len(symbols)-1,10,dtype=int)]
  if market=='KR': pool,calendar=prepare_kr(out);pool={symbol:g for symbol,g,_ in pool}
  else:
   import exchange_calendars as xc
   calendar=xc.get_calendar('XNYS',start='2015-01-01',end='2026-10-16').sessions.tz_localize(None)
   rec={x['symbol']:x for x in read(out/'us_acquisition.json') if x['status']=='ready'}
  for symbol in chosen:
   g=pool[symbol] if market=='KR' else us_stock(out,rec[symbol])[0]
   segments,_=segment_indices(g,calendar)
   candidates=first[first.symbol==symbol]
   for _,row in candidates.iloc[np.linspace(0,len(candidates)-1,min(4,len(candidates)),dtype=int)].iterrows():
    sub=g.iloc[segments[int(row.segment)]].reset_index(drop=True);dates=sub.date.dt.strftime('%Y-%m-%d').to_numpy()
    t=int(np.flatnonzero(dates==row.date)[0]);pi=int(np.flatnonzero(dates==row.peak_date)[0]);c=sub.close.to_numpy(float)
    assert t-pi==row.elapsed and np.isclose(sub.high.iloc[pi],row.peak) and np.isclose(c[t],row.close)
    j=SIGNALS.index(row.signal)
    def pair(at): return (c[at],c[at-4:at+1].mean()) if j==0 else (c[at-4:at+1].mean(),c[at-(9 if j==1 else 19):at+1].mean())
    a,b=pair(t);pa,pb=pair(t-1)
    assert a<b+1e-10*abs(b) and pa>=pb-1e-10*abs(pb)
    if t>=21: assert np.isclose(np.std(np.diff(np.log(c[t-21:t])),ddof=1)*np.sqrt(252),row.sigma)
    if t>=14:
     h=sub.high.to_numpy(float);l=sub.low.to_numpy(float);tr=[h[0]-l[0]]+[max(h[k]-l[k],abs(h[k]-c[k-1]),abs(l[k]-c[k-1])) for k in range(1,t)]
     atr=np.mean(tr[:14])
     for x in tr[14:]: atr=(13*atr+x)/14
     assert np.isclose(atr/c[t-1],row.atrpct)
    if market=='US':
     shares=read(out/'raw/us'/symbol/'shares.json')['units']['shares'];facts=[x for x in shares if x.get('val',0)>0 and x['filed']<row.date and x['end']<=row.date]
     best=sorted(facts,key=lambda x:(x['end'],x['filed']))[-1];ch=read(out/'raw/us'/symbol/'chart_daily.json')['chart']['result'][0]
     from datetime import datetime,timezone
     splits=[(datetime.fromtimestamp(e['date'],timezone.utc).date().isoformat(),e['numerator']/e['denominator']) for e in ch.get('events',{}).get('splits',{}).values()]
     rawprice=c[t]*math.prod(r for d,r in splits if d>row.date)
     eventshares=best['val']*math.prod(r for d,r in splits if best['end']<d<=row.date)
     assert np.isclose(rawprice*eventshares,row.cap);cap_count+=1
    scalar_count+=1
 missing=[]
 for link in re.findall(r'\]\(([^)]+)\)',report.read_text(encoding='utf-8')):
  if link.startswith(('https:','http:','#')): continue
  p=(report.parent/unquote(link.strip('<>'))).resolve()
  if p.name in ('verification.json','manifest.json'): continue
  if not p.exists(): missing.append(str(p))
 assert not missing,missing
 receipt.update(status='PASS',raw_hashes=raw_count,first_cross_rows_checked=cross_count,raw_scalar_replays=scalar_count,
                independently_reconstructed_us_caps=cap_count,volatility_cell_count_checks=volcells,local_report_links='PASS',
                external_KR_ohlcv_values_matched=corroboration['values'],frozen_population_identity='PASS')
 write(out/'verification.json',receipt)
 paths=[report]+[p for p in out.glob('*.json') if p.name!='manifest.json']
 paths += [p for p in out.iterdir() if p.is_file() and p.suffix in ('.csv','.png')]
 paths += [root/'scripts'/n for n in ('ma_dd_describe.py','ma_dd_report.py','verify_ma_dd.py','acquire_us_dd.py','acquire_kr_dd.py','corroborate_kr_dd.py')]
 write(out/'manifest.json',{'protocol_sha256':sha(out/'protocol.json'),'files':[{'path':str(p),'sha256':sha(p)} for p in paths],
                         'raw_sources':'Raw price/share/annual-file hashes are in KR_audit.json,US_audit.json,kr_sources.json; originals retained.'})
 print(receipt,flush=True)

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1]);p.add_argument('--out',type=Path,required=True);p.add_argument('--report',type=Path,required=True);a=p.parse_args();verify(a.root,a.out,a.report)
