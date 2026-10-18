"""Reproducible offline portfolio demonstration; every input is invented."""
import argparse
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import json
import math
import secrets
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def market_inputs():
    from invest_agent.research.synthetic_market import fixture
    snapshot, groups = fixture()
    days = []
    day = date(2026, 10, 16)
    while len(days) < 380:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day -= timedelta(days=1)
    days.reverse()
    mapping = {}
    for n, stock in enumerate(snapshot['series']):
        market = stock['market']
        symbol = str(990001+n) if market == 'KR' else 'DEMO'+str(n-4)
        mapping[(market,stock['symbol'])] = symbol
        stock.update(symbol=symbol, name=('가상 기업 ' if market=='KR' else 'Invented Company ')+str(n+1),
                     isin_code='SYNTHETIC'+str(n), exchange='KOSPI' if market=='KR' else 'NASDAQ',
                     market_cap_as_of=days[-1], collected_at=days[-1]+'T23:00:00+00:00')
        for i, bar in enumerate(stock['bars']):
            bar['date'] = days[i]
            if bar['close'] is None:
                bar['close'] = bar['open']
        if n in (0,5):
            # Construct a contraction/breakout path, then run the real screener.
            values=[35+i*.5 for i in range(49)]+[60+i*.5 for i in range(300)]
            for amplitude in (8,4,1.5):
                values += [210+amplitude*math.cos(i*2*math.pi/9) for i in range(10)]
            values.append(220.)
            scale=100 if market=='KR' else 1
            stock['bars']=[dict(date=days[i],open=(c-.15)*scale,high=(c+.4)*scale,
                low=(c-.4)*scale,close=c*scale,volume=200000 if 369<=i<379 else 4000000 if i==379 else 1000000)
                for i,c in enumerate(values)]
            stock['bars'][-1].update(open=215*scale,low=214.8*scale,high=220.5*scale)
        stock['benchmark'] = '^KS11' if market=='KR' else '^GSPC'
    for item in groups:
        item['symbol'] = mapping[(item['market'],item['symbol'])]
        item.update(group_code='DEMO-SECTOR',group_name='가상 산업 · Synthetic Industry')
    # All demo companies are classified; source is a generated taxonomy.
    for s in snapshot['series']:
        if not any(g['market']==s['market'] and g['symbol']==s['symbol'] for g in groups):
            groups.append(dict(market=s['market'],symbol=s['symbol'],group_code='DEMO-SECTOR',
                               group_name='가상 산업 · Synthetic Industry',group_state='matched',evidence=[]))
    for market, key in [('KR','^KS11'),('US','^GSPC')]:
        b=snapshot['benchmarks'].pop('SYNTH_INDEX_'+market)
        b.update(market=market,provider='synthetic',currency='KRW' if market=='KR' else 'USD')
        for i, bar in enumerate(b['bars']): bar['date']=days[i]
        snapshot['benchmarks'][key]=b
    snapshot.update(sessions={'KR':days,'US':days},fetched_at=days[-1]+'T23:00:00+00:00',
                    universe=[{k:v for k,v in s.items() if k!='bars'} for s in snapshot['series']])
    return snapshot, groups


def financial_result(stock):
    from financial_model import fact
    from financial_focus import summarize
    normal=dict(provider='DART' if stock['market']=='KR' else 'SEC',currency=stock['currency'],
                standard='SYNTHETIC consolidated example',identity={},annual=[],quarterly=[])
    for year in range(2019,2026):
        for q in range(5):
            end=f'{year}-12-31' if q==0 else f'{year}-{q*3:02d}-'+('31' if q in (1,4) else '30')
            start=f'{year}-01-01' if not q else f'{year}-{(q-1)*3+1:02d}-01'
            amount=1_000_000 if stock['market']=='KR' else 1000
            def cell(value, instant=False, unit=None):
                return fact(value,unit=unit or stock['currency'],start=None if instant else start,
                            end=end,filed=f'{year+1}-02-15',sources=['synthetic-generator'])
            values={k:cell(v*amount*(1+.07*(year-2019)),k in ('assets','liabilities','total_equity','current_assets','current_liabilities'))
                    for k,v in dict(sales=100,operating=20,income=14,assets=120,liabilities=45,
                                    total_equity=75,current_assets=50,current_liabilities=25).items()}
            values.update(basic_eps=cell(2+.2*(year-2019),unit=stock['currency']+'/share'),
                          eps=cell(1.9+.19*(year-2019),unit=stock['currency']+'/share'),
                          shares=cell(50000-500*(year-2019),instant=True,unit='shares'),
                          cash_dividends=cell(2*amount),buybacks=cell(3*amount),
                          dividend={**cell(.4+.02*(year-2019),unit=stock['currency']+'/share'),'dividend_basis':'paid'})
            normal['annual' if q==0 else 'quarterly'].append(dict(date=end,label=end,values=values))
    result=summarize(normal,stock)
    result.update(key=stock['market']+':'+stock['symbol'],snapshot_id='synthetic-v1',
                  collected_at='2026-10-16T23:00:00+00:00',sources=[],synthetic=True)
    result['limitations'].insert(0,'모든 재무 수치는 생성한 예시이며 실제 공시가 아닙니다.')
    def label(value):
        if isinstance(value,dict):
            if value.get('source_type')=='company_reported':value['source_type']='synthetic'
            for child in value.values():label(child)
        elif isinstance(value,list):
            for child in value:label(child)
    label(result)
    return dict(state='complete',result=result,reused=True,config=dict(dart_ready=False,sec_ready=False))


class DemoHistory:
    def __init__(self, stocks):
        self.saved=SimpleNamespace(stocks=stocks,markets={})
    def view(self,stock,frame):
        from chart_history import aggregate
        return {**stock,'bars':aggregate(stock['bars'],frame),'timeframe':frame,
                'history':{'state':'complete','source':'synthetic','kind':'synthetic',
                    'first':stock['bars'][0]['date'],'last':stock['bars'][-1]['date'],
                    'daily_count':len(stock['bars']),'stored_count':len(stock['bars']),
                    'collected_at':stock['collected_at'],'can_retry':False,'reason':None},'synthetic':True}
    def ensure(self,*args,**kwargs):
        raise ValueError('offline_demo_has_no_history_provider')


def build(root):
    from invest_agent.trading.market import screen
    from priority_review import build as review
    from sector_signals import build as signals
    from sector_focus import build as focus, screen_groups
    snapshot,groups=market_inputs()
    stocks={s['market']+':'+s['symbol']:s for s in snapshot['series']}
    observation={'observations':[dict(market=m,group_code='DEMO-SECTOR',group_name='가상 산업 · Synthetic Industry')
                                 for m in ('KR','US')],'market_data_complete':False}
    sector=signals(snapshot,groups,observation)['sectors']
    selected=review(screen(snapshot),snapshot,{'strategy':'pdf_momentum','watchlist':{}})
    entries=[e for group in selected['markets'].values() for e in group['entries']]
    market=dict(state='succeeded',processing_state='completed',quality={'state':'complete'},errors=[],
                session_dates={m:s[-1] for m,s in snapshot['sessions'].items()},
                entries=entries,sectors=sector,focus=focus(snapshot,groups,sector),
                screen_groups=screen_groups(entries,snapshot['universe']),candidate_count=len(entries),
                entry_candidate_count=sum(e.get('phase')=='entry' for e in entries),
                limitations=['Synthetic example; not live market coverage.'])
    positions=[]
    for key in ('KR:990001','US:DEMO1'):
        s=stocks[key];price=s['bars'][-1]['close']
        positions.append(dict(market=s['market'],symbol=s['symbol'],quote_symbol=s['symbol'],name=s['name'],
                              quantity='10',price=str(price),average_cost=str(price*.9),currency=s['currency'],
                              isin_code=s['isin_code'],asset_type='stock'))
    bundle=dict(query_state='received',as_of='2026-10-16T23:00:00+00:00',attempted_at='2026-10-16T23:00:00+00:00',
                source_mode='synthetic',stale=True,accounts_complete=True,base_currency='KRW',
                fx_to_base={'KRW':'1','USD':'1400'},valuation={'base_currency':'KRW','fx_to_base':{'KRW':'1','USD':'1400'}},
                accounts=[dict(alias='demo-account',cash={'KRW':'10000000','USD':'10000'},cash_complete=True,
                               holdings_complete=True,positions=positions)],limitations=['가상 계좌 · 실제 잔고가 아닙니다.'],risk=None)
    results={k:financial_result(s) for k,s in stocks.items()}
    deps={'histories':DemoHistory(stocks),'financials':SimpleNamespace(read=lambda k:deepcopy(results[k]))}
    from trailing_warnings import TrailingStore
    moment=datetime(2026,10,16,23,0,tzinfo=timezone.utc)
    warnings=TrailingStore(root/'trailing-warnings.sqlite3').update(bundle,stocks,market['session_dates'],moment)
    return dict(generated_at=moment.isoformat(),synthetic=True,market=market,accounts=bundle,trailing=warnings),stocks,deps


def seed(root,data,stocks,deps):
    from analysis_service import AnalysisService,digest
    from chart_workspace import WorkspaceStore,defaults
    from analysis_collaboration import identity
    from trade_cycle import TradeCycleStore
    store=WorkspaceStore(root/'chart-workspaces.sqlite3')
    service=AnalysisService(store,deps['histories'],deps['financials'],lambda:(data,stocks),root)
    existing=service.rows('analysis_cases')
    if existing and TradeCycleStore(store.path).list()['cycles']:
        return
    key='US:DEMO1';s=stocks[key];bars=s['bars']
    state=defaults()
    state['drawings']=[dict(id='demo-support',kind='trend',points=[dict(time=bars[-60]['date'],price=bars[-60]['low']),
        dict(time=bars[-10]['date'],price=bars[-10]['low'])],color='#36b59b',width=2,extend=False)]
    if not existing:
        store.shared(key,revision=0,state=state)
        detail=service.dispatch('create',{'stock_key':key,'test_record':False,'title':'합성 기업 분석 예시'},'http://127.0.0.1:8765')
        case=detail['case']
    else:
        case=existing[0]
    snap=service.capture(case)
    service.dispatch('record',dict(analysis_key=case['id'],snapshot_id=snap['id'],kind='report',
        title='합성 기업 분석 · 저장된 근거에서 판단까지',author='llm',
        body='## 예시의 목적\n이 보고서는 실제 투자 분석이 아닌 기능 설명용 합성 기록입니다.\n\n'
             '## 관찰과 가설\n생성한 가격 자료의 추세 전환을 관찰합니다. 차트의 선은 가설이며 주문을 의미하지 않습니다.\n\n'
             '## 검증과 반례\n스냅샷 ID로 같은 차트·재무 자료를 다시 읽을 수 있습니다. 현재 화면이 바뀌어도 이 기록은 보존됩니다.\n\n'
             '## 다음 행동\n차트 선을 수정하고 저장한 뒤 새로고침해 보세요. 매매 사이클에서 36개 배분과 3개 경로를 비교할 수 있습니다.'),
        'http://127.0.0.1:8765',principal='collaboration')
    def step(price,condition,compare):
        price=round(price*bars[-1]['close']/96,2)
        return dict(confirmation_price=str(price),assumed_fill_price=str(price),condition=condition,
                    trigger=dict(kind='price',comparator=compare,evaluation='daily_close'))
    plan=dict(title='합성 3분할 시나리오',currency='USD',budget='10000',account_alias='demo-account',
              initial_holding={'quantity':10,'average_price':str(round(bars[-1]['close']*.9,2))},
              buy_steps=[step(97,'합성 진입','gte'),step(94,'합성 추가 관찰','lte'),step(90,'합성 추가 관찰','lte')],
              sell_steps=[step(105,'합성 축소','gte'),step(112,'합성 축소','gte'),step(120,'합성 정리','gte')],
              stop=step(85,'합성 무효화','lte'),scenarios=dict(
                  bullish={'events':[{'kind':'buy','step':i} for i in (1,2,3)]+[{'kind':'sell','step':i} for i in (1,2,3)]},
                  neutral={'events':[{'kind':'buy','step':1},{'kind':'mark','price':str(round(bars[-1]['close']*1.02,2))}]},
                  bearish={'events':[{'kind':'buy','step':1},{'kind':'buy','step':2},{'kind':'stop'}]}))
    TradeCycleStore(store.path).save_draft(key,plan,source=dict(analysis_key=case['id'],snapshot_id=snap['id'],
        snapshot_sha256=digest(snap),identity=identity(snap),as_of=data['accounts']['as_of'],test_record=False))


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--state-dir',type=Path,default=ROOT/'runtime/demo')
    parser.add_argument('--build-only',action='store_true')
    args=parser.parse_args(argv)
    root=args.state_dir.resolve();root.mkdir(parents=True,exist_ok=True)
    data,stocks,deps=build(root);seed(root,data,stocks,deps)
    if args.build_only:
        print(json.dumps({'synthetic':True,'stocks':len(stocks),'candidates':data['market']['candidate_count']}));return
    from dashboard import handler_for,ThreadingHTTPServer
    caps={role:secrets.token_urlsafe(32) for role in ('collaboration','risk')}
    server=ThreadingHTTPServer(('127.0.0.1',args.port),handler_for(data,stocks,ROOT/'dashboard',secrets.token_urlsafe(32),
        root/'chart-workspaces.sqlite3',analysis_capabilities=caps,analysis_dependencies=deps,offline=True))
    endpoint=ROOT/'runtime/dashboard/analysis-endpoint.json';endpoint.parent.mkdir(parents=True,exist_ok=True)
    endpoint.write_text(json.dumps({'url':f'http://127.0.0.1:{server.server_port}','analysis_capabilities':caps}),encoding='utf-8')
    print(f'SYNTHETIC DEMO http://127.0.0.1:{server.server_port} | state: {root}',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()


if __name__=='__main__':main()
