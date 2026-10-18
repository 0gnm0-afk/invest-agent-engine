"""Compare nine MA timing pairs on deterministic invented prices (not market returns)."""
from pathlib import Path
import json,sys
sys.path.insert(0,str(Path(__file__).resolve().parent/'workbench/scripts'))
import numpy as np
import pandas as pd
from ma_timing_segments import flags,transactions,ledger,PAIRS
from ma_reentry_research import mdd

def main():
    n=500;c=100*np.exp(np.cumsum(np.random.default_rng(18).normal(0,.025,n)))
    dates=pd.bdate_range('2020-01-01',periods=n)
    z=dict(close=c,open=c.copy(),high=c*1.01,low=c*.99,date=dates.strftime('%Y-%m-%d').to_numpy(),
           day=dates.to_numpy('datetime64[D]').astype('int64'),pos=np.arange(n),segment=np.zeros(n,int),
           eligible=np.ones(n,bool),sigma=np.full(n,.3),tr_factor=np.ones(n))
    f=flags(z);results=[]
    for pair in PAIRS:
        events,begin,_=transactions(z,f,pair)
        result=ledger(z,events,begin,1.,.0035,.0005)
        results.append(dict(pair=pair,events=len(events),final=float(result['path'][-1]),mdd=float(mdd(result['path']))))
    print(json.dumps(dict(synthetic=True,seed=18,description='Invented prices; no investment performance claim',results=results),indent=2,allow_nan=False))
if __name__=='__main__':main()
