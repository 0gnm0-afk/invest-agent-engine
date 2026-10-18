"""Three crossings, online excursion peaks, pre-signal volatility. Descriptive only."""
from __future__ import annotations
import argparse,csv,gzip,hashlib,json,math,sys
from collections import Counter
from datetime import datetime,timezone,date
from pathlib import Path
_out_hint=Path(sys.argv[sys.argv.index('--out')+1]) if '--out' in sys.argv else Path(__file__).resolve().parents[1]/'runtime/ma-dd-describe-20261017'
if (_out_hint/'dependencies').exists(): sys.path.insert(0,str(_out_hint/'dependencies'))
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
SIGNALS=['close_cross_below_sma5','sma5_cross_below_sma10','sma5_cross_below_sma20']
METHODS=[('primary',.05,'high'),('rebound3',.03,'high'),('rebound8',.08,'high'),('close_peak',.05,'close'),('atr2',None,'high')]
FIELDS=['market','symbol','method','segment','leg','parent','signal','date','peak_date','peak','close','dd','elapsed',
        'sigma','atrpct','cap','cap_group','rank','previous_rank','shares_end','shares_filed','shares_age','repeat']

def read(p): return json.loads(Path(p).read_bytes())
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(p,value):
 def clean(x):
  if isinstance(x,dict): return {str(k):clean(v) for k,v in x.items()}
  if isinstance(x,(tuple,list)): return [clean(v) for v in x]
  if isinstance(x,np.ndarray): return clean(x.tolist())
  if isinstance(x,np.generic): return clean(x.item())
  if isinstance(x,float) and not math.isfinite(x): return None
  return x
 Path(p).write_text(json.dumps(clean(value),ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')


def features(g):
 c=g.close.to_numpy(float);h=g.high.to_numpy(float);l=g.low.to_numpy(float)
 ma={k:pd.Series(c).rolling(k).mean().to_numpy() for k in (5,10,20)}
 pairs=[(c,ma[5]),(ma[5],ma[10]),(ma[5],ma[20])]; cross=[]
 for a,b in pairs:
  diff=a-b;tol=1e-12*np.maximum(abs(a),abs(b))
  # Computational equality tolerance only, far below a quoted price tick.
  cross.append((diff < -tol)&np.r_[False,diff[:-1]>=-tol[:-1]])
 sigma=pd.Series(np.r_[np.nan,np.diff(np.log(c))]).rolling(20).std(ddof=1).shift(1).to_numpy()*np.sqrt(252)
 pc=np.r_[np.nan,c[:-1]];tr=np.nanmax(np.stack([h-l,abs(h-pc),abs(l-pc)]),axis=0)
 atr=np.full(len(c),np.nan)
 if len(c)>=14:
  atr[13]=np.mean(tr[:14])
  for t in range(14,len(c)): atr[t]=(13*atr[t-1]+tr[t])/14
 prior=np.r_[np.nan,atr[:-1]]
 return {'cross':np.column_stack(cross),'sigma':sigma,'atr':prior,'atrpct':prior/pc}


def excursions(g,f,rebound=.05,basis='high'):
 """No suffix values are read when emitting any row. Reset occurs after close."""
 c=g.close.to_numpy(float); h=g[basis].to_numpy(float);n=len(c)
 rows=[];seen=set();leg=0;parent=0;running=-np.inf;initialized=False;trough=c[0];ti=0;peak=h[0];pi=0
 for t in range(n):
  if h[t]>=running: running=h[t];parent+=1
  if not initialized:
   if c[t]<trough: trough=c[t];ti=t
   ready=(c[t]>=trough*(1+rebound)) if rebound is not None else (np.isfinite(f['atr'][t]) and c[t]-trough>=2*f['atr'][t])
   if not ready: continue
   initialized=True;peak=h[t];pi=t;trough=c[t];ti=t;leg+=1;seen=set()
  elif h[t]>=peak:
   peak=h[t];pi=t;trough=c[t];ti=t;leg+=1;seen=set()
  elif c[t]<trough: trough=c[t];ti=t
  if c[t]<peak:
   for j in np.flatnonzero(f['cross'][t]):
    repeated=int(j in seen); seen.add(int(j))
    rows.append({'t':t,'leg':leg,'parent':parent,'signal':int(j),'peak_index':pi,'peak':float(peak),
                 'dd':float(100*(c[t]/peak-1)),'elapsed':int(t-pi),'repeat':repeated})
  reset=(c[t]>=trough*(1+rebound)) if rebound is not None else (np.isfinite(f['atr'][t]) and c[t]-trough>=2*f['atr'][t])
  if reset and t>ti:
   peak=h[t];pi=t;trough=c[t];ti=t;leg+=1;seen=set()
 return rows


def segment_indices(g,calendar):
 pos=calendar.get_indexer(pd.DatetimeIndex(g.date));c=g.close.to_numpy(float);h=g.high.to_numpy(float);l=g.low.to_numpy(float);o=g.open.to_numpy(float)
 valid=(pos>=0)&np.isfinite(c)&np.isfinite(h)&np.isfinite(l)&np.isfinite(o)&(l>0)&(l<=np.minimum(o,c))&(h>=np.maximum(o,c))&(g.volume.to_numpy(float)>0)
 groups=[];current=[];last=-99
 days=g.date.to_numpy('datetime64[D]').astype(np.int64)
 for t in range(len(g)):
  gap=(t>0 and days[t]-days[t-1]>10)
  if not valid[t] or pos[t]!=last+1 or gap:
   if current: groups.append(np.array(current));current=[]
  if valid[t]: current.append(t);last=pos[t]
  else: last=-99
 if current: groups.append(np.array(current))
 return groups,dict(invalid_bars=int((~valid).sum()),segments=len(groups))


def historical_us_caps(chart,shares,dates,closes):
 spl=[]
 for e in chart.get('events',{}).get('splits',{}).values():
  day=datetime.fromtimestamp(e['date'],timezone.utc).date().isoformat(); ratio=e['numerator']/e['denominator']
  if ratio<=0: raise ValueError('invalid_split_ratio')
  spl.append((day,ratio))
 facts=[]
 for x in shares.get('units',{}).get('shares',[]):
  if x.get('val',0)>0 and x.get('end','')<=x.get('filed',''):
   x=dict(x);x['normalized']=x['val']*math.prod(r for d,r in spl if d>x['end']);facts.append(x)
 facts.sort(key=lambda x:(x['filed'],x['end'])); pointer=0;best=None
 caps=np.full(len(dates),np.nan);ages=np.full(len(dates),-1);ends=np.full(len(dates),'',dtype='U10');fileds=ends.copy()
 for t,d in enumerate(dates):
  while pointer<len(facts) and facts[pointer]['filed']<d:
   x=facts[pointer];pointer+=1
   if best is None or (x['end'],x['filed'])>=(best['end'],best['filed']): best=x
  if best:
   age=(date.fromisoformat(d)-date.fromisoformat(best['end'])).days
   if 0<=age<=180:
    caps[t]=closes[t]*best['normalized'];ages[t]=age;ends[t]=best['end'];fileds[t]=best['filed']
 groups=np.select([caps>=200e9,caps>=10e9,caps>=2e9],['mega','large','mid'],default='')
 return caps,groups,ages,ends,fileds


def us_stock(out,record):
 folder=out/'raw/us'/record['symbol'];packet=read(folder/'chart_daily.json');ch=packet['chart']['result'][0]
 assert ch['meta']['symbol'].replace('-','.')==record['symbol'].replace('-','.') and ch['meta']['currency']=='USD'
 q=ch['indicators']['quote'][0]; ds=[datetime.fromtimestamp(t,timezone.utc).date().isoformat() for t in ch['timestamp']]
 g=pd.DataFrame({'date':ds,**{k:q[k] for k in ('open','high','low','close','volume')}})
 assert g.date.is_unique and g.date.is_monotonic_increasing
 g=g[g.date<='2026-10-15'].reset_index(drop=True);dates=g.date.to_numpy();shares=read(folder/'shares.json')
 assert str(shares['cik']).zfill(10)==record['cik']
 caps,groups,ages,ends,fileds=historical_us_caps(ch,shares,dates,g.close.to_numpy(float))
 g=g.assign(cap=caps,cap_group=groups,rank=-1,previous_rank=-1,shares_age=ages,shares_end=ends,shares_filed=fileds)
 g['date']=pd.to_datetime(g.date)
 return g,{'market':'US','symbol':record['symbol'],'price_path':str(folder/'chart_daily.json'),'price_sha256':sha(folder/'chart_daily.json'),
           'shares_path':str(folder/'shares.json'),'shares_sha256':sha(folder/'shares.json'),
           'bars':len(g),'missing_or_stale_cap_bars':int((~np.isfinite(caps)).sum()),'first':str(g.date.min().date()),'last':str(g.date.max().date())}


def prepare_kr(out, *, audit_out=None):
 sys.path.insert(0,str(out/'dependencies'))
 raw=out/'raw/kr-marcap';frames=[];audit=[]
 for path in sorted(raw.glob('*.parquet')):
  x=pd.read_parquet(path,columns=['Date','Code','Market','Marcap','Close','Stocks']);x=x[(x.Market=='KOSPI')&x.Code.str.endswith('0')]
  bad=(x.Marcap<=0)|(x.Close<=0)|(x.Stocks<=0)|(~np.isclose(x.Marcap,x.Close*x.Stocks,rtol=1e-6,atol=1))
  audit.append({'path':str(path),'sha256':sha(path),'ordinary_KOSPI_rows':len(x),'cap_identity_failures':int(bad.sum())})
  frames.append(x.loc[~bad,['Date','Code','Marcap']])
 ranks=pd.concat(frames,ignore_index=True);assert not ranks.duplicated(['Date','Code']).any()
 ranks=ranks.sort_values(['Date','Marcap','Code'],ascending=[True,False,True]);ranks['rank']=ranks.groupby('Date').cumcount()+1
 calendar=pd.DatetimeIndex(sorted(ranks.Date.unique()));nxt=dict(zip(calendar[:-1],calendar[1:]));pr=ranks[['Date','Code','rank']].copy()
 pr['Date']=pr.Date.map(nxt);pr=pr.rename(columns={'rank':'previous_rank'})
 ranks=ranks.merge(pr,on=['Date','Code'],how='left');ranks['previous_rank']=ranks.previous_rank.fillna(-1).astype(int)
 chosen=set(ranks.loc[(ranks.Date>='2016-01-01')&(ranks['rank']<=200),'Code']);ranks=ranks[ranks.Code.isin(chosen)]
 frames=[]
 cols=['Date','Code','Name','Market','Open','High','Low','Close','Volume','Marcap','Stocks','Changes']
 for path in sorted(raw.glob('*.parquet')):
  x=pd.read_parquet(path,columns=cols);frames.append(x[x.Code.isin(chosen)])
 prices=pd.concat(frames,ignore_index=True);assert not prices.duplicated(['Date','Code']).any()
 prices=prices.merge(ranks[['Date','Code','rank','previous_rank']],on=['Date','Code'],how='left')
 prices[['rank','previous_rank']]=prices[['rank','previous_rank']].fillna(-1)
 prices=prices.rename(columns={'Date':'date','Open':'open','High':'high','Low':'low','Close':'close','Volume':'volume','Marcap':'cap'})
 source=[]
 for code,g in prices.groupby('Code',sort=True):
  g=g.sort_values('date').reset_index(drop=True);c=g.close.to_numpy(float);factor=np.ones(len(g));factor[1:]=(c[1:]-g.Changes.to_numpy(float)[1:])/c[:-1]
  invalid=(~np.isfinite(factor))|(factor<=0);factor[invalid]=1.
  adjustment=np.r_[np.cumprod(factor[:0:-1])[::-1],1.]
  for key in ('open','high','low','close'): g[key]=g[key]*adjustment
  g=g.assign(cap_group='KR_top200',shares_age=-1,shares_end='',shares_filed='')
  g.loc[invalid,['open','high','low','close']]=np.nan
  source.append((code,g,{'market':'KR','symbol':code,'bars':len(g),'factor_changes':int((abs(factor-1)>1e-8).sum()),
                       'invalid_factors':int(invalid.sum()),'last':str(g.date.max().date())}))
 write((audit_out or out)/'kr_sources.json',{'commit':(raw/'commit.txt').read_text(),'files':audit,'symbols':len(chosen),'calendar_sessions':len(calendar),
                             'rank_last':str(calendar.max().date()),'cap_unit':'KRW; verified Marcap == Close*Stocks'})
 return source,calendar


def prepare(root,out,market):
 out.mkdir(parents=True,exist_ok=True);p=read(root/'scripts/ma_dd_protocol.json')
 if (out/'protocol.json').exists(): assert sha(out/'protocol.json')==sha(root/'scripts/ma_dd_protocol.json')
 else: (out/'protocol.json').write_bytes((root/'scripts/ma_dd_protocol.json').read_bytes())
 if (out/f'{market}_events.csv.gz').exists(): raise FileExistsError('immutable prepared market already exists')
 if market=='KR': stocks,calendar=prepare_kr(out)
 else:
  import exchange_calendars as xcals
  calendar=xcals.get_calendar('XNYS',start='2015-01-01',end='2026-10-16').sessions.tz_localize(None)
  stocks=read(out/'us_acquisition.json');stocks=[r for r in stocks if r['status']=='ready']
 audit=[];fail=[];risks=[];handles={};writers={};counts=Counter()
 try:
  for method,_,_ in METHODS:
   handles[method]=gzip.open(out/(f'{market}_events.csv.gz' if method=='primary' else f'{market}_{method}.csv.gz'),'wt',newline='',encoding='utf-8',compresslevel=3)
   writers[method]=csv.DictWriter(handles[method],fieldnames=FIELDS);writers[method].writeheader()
  for number,item in enumerate(stocks):
   try:
    if market=='US': symbol=item['symbol'];g,record=us_stock(out,item)
    else: symbol,g,record=item
    segments,qa=segment_indices(g,calendar);record.update(qa);audit.append(record)
    for segment,ix in enumerate(segments):
     if len(ix)<6: continue
     sub=g.iloc[ix].reset_index(drop=True);f=features(sub);dates=sub.date.dt.strftime('%Y-%m-%d').to_numpy()
     meta={k:sub[k].to_numpy() for k in ('cap','cap_group','rank','previous_rank','shares_end','shares_filed','shares_age')};closes=sub.close.to_numpy(float)
     eligible=(meta['rank']<=200)&(meta['rank']>0) if market=='KR' else meta['cap_group']!=''
     # Prior-day membership is retained as a separate sensitivity, never replaces primary.
     previous=(meta['previous_rank']<=200)&(meta['previous_rank']>0) if market=='KR' else eligible
     valid=eligible&np.isfinite(f['sigma'])&np.isfinite(f['atrpct'])&(dates>='2016-01-01')
     risks.append(np.column_stack([f['sigma'][valid],f['atrpct'][valid]]).astype(np.float32))
     for method,rebound,basis in METHODS:
      for e in excursions(sub,f,rebound,basis):
       t=e['t'];counts[method+'_all_actual_crosses']+=1
       if dates[t]<'2016-01-01' or not (eligible[t] or (market=='KR' and previous[t])): continue
       row={'market':market,'symbol':symbol,'method':method,'segment':segment,'leg':e['leg'],'parent':e['parent'],
            'signal':SIGNALS[e['signal']],'date':dates[t],'peak_date':dates[e['peak_index']],'peak':e['peak'],'close':closes[t],
            'dd':e['dd'],'elapsed':e['elapsed'],'sigma':f['sigma'][t],'atrpct':f['atrpct'][t],
            **{k:v[t] for k,v in meta.items()},'repeat':e['repeat']}
       writers[method].writerow(row);counts[method+'_written']+=1
    if (number+1)%100==0: print('prepared',market,number+1,'/',len(stocks),dict(counts),flush=True)
   except Exception as e:
    fail.append({'symbol':item.get('symbol') if isinstance(item,dict) else item[0],'reason':type(e).__name__,'detail':str(e)[:160]})
 finally:
  for h in handles.values(): h.close()
 np.savez_compressed(out/f'{market}_risk_vol.npz',vol=np.concatenate(risks) if risks else np.empty((0,2)))
 write(out/f'{market}_audit.json',{'protocol_sha256':sha(out/'protocol.json'),'stocks':audit,'failures':fail,'counts':dict(counts),
        'events_sha256':sha(out/f'{market}_events.csv.gz'),'risk_sha256':sha(out/f'{market}_risk_vol.npz'),
        'methods_sha256':{m:sha(out/(f'{market}_events.csv.gz' if m=='primary' else f'{market}_{m}.csv.gz')) for m,_,_ in METHODS}})
 print('prepared market',market,'stocks',len(audit),'failures',len(fail),flush=True)


def summarize(g,bootstrap=False):
 n=len(g)
 if not n: return {'n':0,'stocks':0,'small_sample':True}
 dd=g.dd.to_numpy();elapsed=g.elapsed.to_numpy();sym=g.symbol.astype(str)
 result={'n':n,'stocks':sym.nunique(),'excursions':len(g[['symbol','segment','leg']].drop_duplicates()),
         'parents':len(g[['symbol','segment','parent']].drop_duplicates()),'small_sample':bool(n<100 or sym.nunique()<20),
         'mean':float(dd.mean()),'median':float(np.median(dd)),'q25':float(np.quantile(dd,.25)),
         'q75':float(np.quantile(dd,.75)),'q90':float(np.quantile(dd,.9)),'loss_p90':float(-np.quantile(dd,.1)),
         'elapsed_mean':float(elapsed.mean()),'elapsed_median':float(np.median(elapsed)),
         'elapsed_q25':float(np.quantile(elapsed,.25)),'elapsed_q75':float(np.quantile(elapsed,.75)),'elapsed_q90':float(np.quantile(elapsed,.9)),
         'equal_stock_mean':float(g.groupby(sym).dd.mean().mean())}
 if bootstrap:
  agg=g.groupby(sym).dd.agg(['sum','count']);rng=np.random.default_rng(20261017);k=len(agg)
  weights=rng.multinomial(k,np.full(k,1/k),size=1000);draw=(weights@agg['sum'].to_numpy())/(weights@agg['count'].to_numpy())
  result['stock_cluster_mean_ci95']=np.quantile(draw,[.025,.975]).tolist()
 return result


def primary_filter(g,market):
 return g[(g['rank']>0)&(g['rank']<=200)] if market=='KR' else g[g.cap_group.isin(['mega','large','mid'])]


def describe(out):
 results={'protocol_sha256':sha(out/'protocol.json'),'markets':{}}
 for market in ('KR','US'):
  audit=read(out/f'{market}_audit.json');assert sha(out/f'{market}_events.csv.gz')==audit['events_sha256']
  g=pd.read_csv(out/f'{market}_events.csv.gz',dtype={'symbol':str});g=primary_filter(g,market)
  unique=g[g.repeat==0].copy();unique['year']=unique.date.str[:4];vol=np.load(out/f'{market}_risk_vol.npz')['vol']
  cuts={key:np.quantile(vol[:,j],[.25,.5,.75,.9]).tolist() for j,key in enumerate(('sigma','atrpct'))}
  rows=[];years=[];sensitivity=[];pair=[]
  groups=['KR_top200'] if market=='KR' else ['US_total','mega','large','mid']
  for group in groups:
   sub=unique if group=='US_total' else unique[unique.cap_group==group]
   for signal in SIGNALS:
    s=sub[sub.signal==signal];rows.append({'group':group,'signal':signal,'condition':'all',**summarize(s,True)})
    for key,values in cuts.items():
     sv=s[np.isfinite(s[key])];bins=np.searchsorted(values[:3],sv[key],side='right')
     rows.append({'group':group,'signal':signal,'condition':key+'_missing',**summarize(s[~np.isfinite(s[key])])})
     for b in range(4): rows.append({'group':group,'signal':signal,'condition':key+'_Q'+str(b+1),**summarize(sv[bins==b])})
     for q,cut in zip((50,75,90),values[1:]):
      rows.append({'group':group,'signal':signal,'condition':key+'_geQ'+str(q),'threshold':cut,**summarize(s[s[key]>=cut])})
    for year,ss in s.groupby('year'): years.append({'group':group,'signal':signal,'year':year,**summarize(ss)})
   # Paired comparisons use only excursions with all three first crossings.
   wide=sub.pivot(index=['symbol','segment','leg'],columns='signal',values='dd').dropna()
   offsets=sub.pivot(index=['symbol','segment','leg'],columns='signal',values='elapsed').reindex(wide.index)
   seq=(offsets[SIGNALS[0]]<offsets[SIGNALS[1]])&(offsets[SIGNALS[1]]<offsets[SIGNALS[2]])
   for label,w in [('all_three_any_order',wide),('strict_1_2_3',wide[seq])]:
    if not len(w): continue
    pair.append({'group':group,'subset':label,'n':len(w),'stocks':w.index.get_level_values(0).nunique(),
      'signal_means':w.mean().to_dict(),'delta2minus1_mean':float((w[SIGNALS[1]]-w[SIGNALS[0]]).mean()),
      'delta3minus2_mean':float((w[SIGNALS[2]]-w[SIGNALS[1]]).mean())})
   orders=[]
   for times in offsets[SIGNALS].to_numpy():
    orders.append(' > '.join('+'.join(str(j+1) for j,t in enumerate(times) if t==day) for day in sorted(set(times))))
   pair.append({'group':group,'orders':dict(Counter(orders).most_common(6))})
  for method,_,_ in METHODS:
   filename=f'{market}_events.csv.gz' if method=='primary' else f'{market}_{method}.csv.gz'
   x=pd.read_csv(out/filename,dtype={'symbol':str});x=primary_filter(x,market);x=x[x.repeat==0]
   for group in groups:
    sub=x if group=='US_total' else x[x.cap_group==group]
    for signal in SIGNALS:
     s=sub[sub.signal==signal];sensitivity.append({'method':method,'group':group,'signal':signal,**summarize(s)})
  # De-duplication and previous-day Korean population sensitivities.
  firstlegs=unique.sort_values('date')[['symbol','segment','parent','leg']].drop_duplicates(['symbol','segment','parent'])
  keep=unique.merge(firstlegs,on=['symbol','segment','parent','leg'])
  for tag,data in [('all_crosses',g),('first_leg_per_parent',keep)]:
   for group in groups:
    ss=data if group=='US_total' else data[data.cap_group==group]
    for signal in SIGNALS: sensitivity.append({'method':tag,'group':group,'signal':signal,**summarize(ss[ss.signal==signal])})
  if market=='KR':
   allrows=pd.read_csv(out/f'{market}_events.csv.gz',dtype={'symbol':str});ss=allrows[(allrows.previous_rank>0)&(allrows.previous_rank<=200)&(allrows.repeat==0)]
   for signal in SIGNALS: sensitivity.append({'method':'previous_day_top200','group':'KR_top200','signal':signal,**summarize(ss[ss.signal==signal])})
  pd.DataFrame(rows).to_csv(out/f'{market}_summary.csv',index=False,encoding='utf-8-sig')
  pd.DataFrame(years).to_csv(out/f'{market}_annual.csv',index=False,encoding='utf-8-sig')
  pd.DataFrame(sensitivity).to_csv(out/f'{market}_sensitivity.csv',index=False,encoding='utf-8-sig')
  results['markets'][market]={'cuts':cuts,'vol_risk_rows':len(vol),'all_crosses':len(g),'first_crosses':len(unique),'repeats_dropped':int((g.repeat>0).sum()),
      'stocks':unique.symbol.nunique(),'first':unique.date.min(),'last':unique.date.max(),'rows':rows,'paired':pair,'sensitivity':sensitivity}
  print('described',market,len(unique),'events',unique.symbol.nunique(),'stocks',flush=True)
 write(out/'results.json',results)


if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('command',choices=['prepare','describe']);p.add_argument('--root',type=Path,default=ROOT);p.add_argument('--out',type=Path,required=True);p.add_argument('--market',choices=['KR','US']);a=p.parse_args()
 if a.command=='prepare': prepare(a.root,a.out,a.market)
 else: describe(a.out)
