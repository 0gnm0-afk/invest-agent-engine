"""Build a selected company's financial summary on explicit request only."""
import argparse
import json
import time
from pathlib import Path
from chart_history import HistoryStore
from financial_store import FinancialStore, ROOT


def selected_stock(market,symbol,histories):
    candidates=[market+':'+symbol]
    if market=='KR' and '.' not in symbol:candidates += ['KR:'+symbol+'.KS','KR:'+symbol+'.KQ']
    keys=[k for k in candidates if k in histories.saved.stocks]
    if len(keys)!=1:raise ValueError('저장 시세에서 종목을 유일하게 찾지 못했습니다: '+market+':'+symbol)
    key=keys[0];stock=histories.saved.stocks[key]
    stock={**stock,**histories.view(stock,'1d')}
    # Explicit company/issue mapping verified from Samsung's official listing page.
    if key=='KR:005930.KS':
        preferred=histories.saved.stocks.get('KR:005935.KS')
        if preferred:
            p=histories.view(preferred,'1d');bars=p.get('bars',[])
            stock['preferred_price']=dict(symbol='005935.KS',currency='KRW',bar=bars[-1] if bars else None,
                mapping_source='https://www.samsung.com/global/ir/stock-information/listing-Info/')
    return key,stock


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('market',choices=['KR','US']);parser.add_argument('symbol')
    mode=parser.add_mutually_exclusive_group();mode.add_argument('--refresh',action='store_true');mode.add_argument('--saved',action='store_true');mode.add_argument('--recalculate',action='store_true',help='저장 공시·시세를 검증하고 외부 조회 없이 새 계산본 저장');mode.add_argument('--supplement',action='store_true',help='저장 입력을 보존하고 누락된 연간 EPS·배당 공시만 보완 수집')
    args=parser.parse_args();store=FinancialStore()
    if args.recalculate or args.supplement:
        candidates=[args.market+':'+args.symbol]
        if args.market=='KR' and '.' not in args.symbol:candidates += ['KR:'+args.symbol+'.KS','KR:'+args.symbol+'.KQ']
        keys=[key for key in candidates if (store.folder(key)/'latest.json').exists()]
        if len(keys)!=1:raise ValueError('저장 재무 종목을 유일하게 찾지 못했습니다')
        key=keys[0];stock=None
    else:
        histories=HistoryStore(ROOT/'runtime/chart-history.sqlite3')
        key,stock=selected_stock(args.market,args.symbol,histories)
    before=store.read(key).get('result')
    before_id=before.get('snapshot_id') if before else None
    result=store.recalculate_saved(key,supplement=args.supplement) if args.recalculate or args.supplement else store.read(key) if args.saved else store.start(key,stock,refresh=args.refresh)
    stage=None
    while result['state']=='running':
        current=result.get('attempt',{}).get('stage')
        if current!=stage:print(current,flush=True);stage=current
        time.sleep(1);result=store.read(key)
    value=result.get('result')
    print(json.dumps(dict(key=key,state=result['state'],snapshot=value.get('snapshot_id') if value else None,
                         path=str(store.folder(key)/'snapshots'/value['snapshot_id']/'result.json') if value else None,
                         error=result.get('attempt',{}).get('error'),reused=bool(value and before_id==value.get('snapshot_id'))),ensure_ascii=False))
    if result['state'] in ('failed','interrupted') or not value:raise SystemExit(1)

if __name__=='__main__':main()
