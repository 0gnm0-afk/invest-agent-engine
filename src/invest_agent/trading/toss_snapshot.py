"""Read-only Toss export. Authentication stays in the caller's private helper.

Source: https://openapi.tossinvest.com/openapi-docs/latest/openapi.json (1.2.15).
Buying power is never substituted for gross account cash/equity.
"""
import hashlib
import hmac
from datetime import datetime, timezone

from .portfolio import number


def collect(api_get, max_age_hours=24, clock=None, *, alias_key: bytes):
    # Keep one private random key per instance. Never export it with snapshots.
    if not isinstance(alias_key, bytes) or len(alias_key) < 32:
        raise ValueError("A private alias key of at least 32 bytes is required")
    clock=clock or (lambda:datetime.now(timezone.utc))
    started=clock();errors=[]
    def call(path,params=None,seq=None):
        try:
            value=api_get(path,params,seq)
            if not isinstance(value,dict) or 'result' not in value: raise ValueError('Invalid envelope')
            return value['result']
        except (Exception,SystemExit) as exc:  # noqa: BLE001 -- Boundary records failures and isolates optional provider/component errors.
            errors.append({'endpoint':path,'error_type':type(exc).__name__,'reason':'read_unavailable'})
            return None
    bundle={'schema_version':1,'source':'broker_export','provider':'tossinvest-openapi',
        'as_of':started.isoformat(),'max_age_hours':str(number(max_age_hours,positive=True)),
        'base_currency':'KRW','fx_to_base':{'KRW':'1'},'accounts':[],'accounts_complete':False,
        'collection_errors':errors,'authority':'read_only_no_orders',
        'limitations':['gross_cash_balance_not_provided_by_selected_endpoints','stops_not_adopted','working_orders_not_reconciled']}
    accounts=call('/api/v1/accounts')
    if not isinstance(accounts,list):return bundle
    bundle['accounts_complete']=True
    raw_rows=[];seen=set()
    for account in accounts:
        try:
            seq=account['accountSeq'];account_no=account['accountNo']
            if not isinstance(account_no,str) or not account_no: raise ValueError('Invalid account identifier')
            if not isinstance(seq,int) or isinstance(seq,bool): raise ValueError('Invalid account sequence')  # noqa: TRY004 -- Preserve the existing ValueError validation contract for callers.
            alias='toss-'+hmac.new(alias_key, ('toss-account:'+account_no).encode(), hashlib.sha256).hexdigest()[:32]
            if alias in seen: raise ValueError('Duplicate account')
            seen.add(alias)
        except (ValueError,KeyError,TypeError):
            bundle['accounts_complete']=False;errors.append({'reason':'account_identity_unavailable'});continue
        row={'alias':alias,'cash':{},'cash_complete':False,'positions':[],'holdings_complete':False,
             'orderable_cash':{},'account_type':account.get('accountType'),'source_ref':'tossinvest-openapi/holdings'}
        bundle['accounts'].append(row)
        holdings=call('/api/v1/holdings',seq=seq)
        items=holdings.get('items') if isinstance(holdings,dict) else None
        if isinstance(items,list):
            row['holdings_complete']=True
            raw_rows.append((row,items))
        for currency in ('KRW','USD'):
            power=call('/api/v1/buying-power',{'currency':currency},seq)
            if isinstance(power,dict) and power.get('currency')==currency:
                try:row['orderable_cash'][currency]=str(number(power['cashBuyingPower']))
                except (ValueError,KeyError):errors.append({'reason':'buying_power_value_unavailable'})
    symbols=sorted({item['symbol'] for _,items in raw_rows for item in items
                    if isinstance(item,dict) and item.get('marketCountry')=='KR' and isinstance(item.get('symbol'),str)})
    segments={}
    for start in range(0,len(symbols),200):
        stocks=call('/api/v1/stocks',{'symbols':','.join(symbols[start:start+200])})
        if isinstance(stocks,list):
            segments.update({stock['symbol']:stock.get('market') for stock in stocks if isinstance(stock,dict) and 'symbol' in stock})
    needs_usd=any(item.get('currency')=='USD' for _,items in raw_rows for item in items if isinstance(item,dict))
    if needs_usd:
        fx=call('/api/v1/exchange-rate',{'baseCurrency':'USD','quoteCurrency':'KRW'})
        try:
            sampled=clock()
            if fx['baseCurrency']!='USD' or fx['quoteCurrency']!='KRW':raise ValueError('Unexpected FX pair')
            if not datetime.fromisoformat(fx['validFrom'])<=sampled<=datetime.fromisoformat(fx['validUntil']):
                raise ValueError('FX validity interval mismatch')
            bundle['fx_to_base']['USD']=str(number(fx['midRate'],positive=True))
            bundle['fx_observation']={k:fx[k] for k in ('validFrom','validUntil','baseCurrency','quoteCurrency')}
            bundle['fx_observation']['basis']='midRate_reference_only'
        except (TypeError,KeyError,ValueError):errors.append({'reason':'valid_usd_krw_fx_unavailable'})
    for account,items in raw_rows:
        for index,item in enumerate(items):
            pos={'symbol':item.get('symbol',f'unidentified-holding-{index+1}') if isinstance(item,dict) else f'unidentified-holding-{index+1}',
                 'market':item.get('marketCountry') if isinstance(item,dict) else None,
                 'currency':item.get('currency','unknown') if isinstance(item,dict) else 'unknown'}
            try:
                if not isinstance(pos['symbol'],str) or not pos['symbol']:raise ValueError('Missing symbol')
                if pos['market'] not in ('KR','US') or pos['currency'] not in ('KRW','USD'):raise ValueError('Unsupported holding')
                pos.update(quantity=str(number(item['quantity'])),price=str(number(item['lastPrice'],positive=True)),
                           average_cost=str(number(item['averagePurchasePrice'])),price_basis='broker_last_price_observation')
                if pos['market']=='US':pos.update(quote_symbol=pos['symbol'].replace('.','-'),benchmark='^GSPC')
                elif segments.get(pos['symbol']) in ('KOSPI','KOSDAQ'):
                    kospi=segments[pos['symbol']]=='KOSPI'
                    pos.update(quote_symbol=pos['symbol']+('.KS' if kospi else '.KQ'),benchmark='^KS11' if kospi else '^KQ11')
            except (TypeError,KeyError,ValueError):pos['data_unavailable_reason']='holding_values_unavailable'
            account['positions'].append(pos)
    bundle['collected_until']=clock().isoformat()
    return bundle
