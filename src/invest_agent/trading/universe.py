"""Discover public exchange listings in a bounded child process."""
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone


def listing_rows(exchange):
    if exchange == 'KRX':
        import FinanceDataReader as f
        return json.loads(f.StockListing(exchange).to_json(orient='records',force_ascii=False))
    # Same Naver exchange listing source used by installed FDR. FDR drops cap;
    # retain the raw USD amount, currency and provider timestamp before projection.
    import requests
    rows = []
    for page in range(1,101):
        response = requests.get('https://api.stock.naver.com/stock/exchange/'+exchange+'/marketValue',
                                params={'page':page,'pageSize':100},timeout=(5,15))
        response.raise_for_status()
        data = response.json()
        for item in data['stocks']:
            raw_cap = item.get('marketValueRaw')
            cap = int(raw_cap) if isinstance(raw_cap,str) and raw_cap.isdigit() else None
            rows.append({'Symbol':item['symbolCode'],'Name':item['stockNameEng'],
                'MarketCap':cap,'Currency':item.get('currencyType',{}).get('name'),
                'Exchange':item.get('stockExchangeType',{}).get('code'),
                'CapAsOf':item.get('localTradedAt'),
                'tradable':item.get('tradableStatus') == 'tradable',
                'listing_observed_at':datetime.now(timezone.utc).isoformat()})
        if len(rows) >= data['totalCount']:
            return rows
        if not data['stocks']:
            raise ValueError('incomplete_exchange_listing')
    raise ValueError('exchange_listing_page_limit')


def discover(markets: list[str], limit_per_exchange: int | None = None) -> tuple[list[dict],dict]:
    if limit_per_exchange is not None and (isinstance(limit_per_exchange,bool) or not isinstance(limit_per_exchange,int) or limit_per_exchange < 1):
        raise ValueError("limit_per_exchange must be positive or null")
    if not markets or any(m not in ("KR","US") for m in markets):
        raise ValueError("Invalid universe markets")
    exchanges = (["KRX"] if "KR" in markets else []) + (["NASDAQ","NYSE"] if "US" in markets else [])
    code = "from invest_agent.trading.universe import listing_rows; import sys,json; print(json.dumps(listing_rows(sys.argv[1]),ensure_ascii=False))"
    universe, metadata = [], {}
    for exchange in exchanges:
        try:
            process = subprocess.run([sys.executable,"-c",code,exchange],capture_output=True,text=True,encoding="utf-8",timeout=45,
                                     env={**os.environ,"PYTHONIOENCODING":"utf-8"}, check=False)
            if process.returncode:
                raise ValueError("Listing process failed")
            rows = json.loads(process.stdout)
            fields=("Code","Market","Name") if exchange=="KRX" else ("Symbol","Name")
            if not isinstance(rows,list) or not rows or any(
                not isinstance(row,dict) or any(not isinstance(row.get(k),str) or not row[k] for k in fields) for row in rows):
                raise ValueError("Listing rows empty or invalid")
        except (OSError,subprocess.TimeoutExpired,ValueError,UnicodeError) as exc:
            metadata[exchange]={"state":"unavailable","reason":"listing_unavailable","error_type":type(exc).__name__,
                                "listed":None,"eligible_by_listing_heuristic":None,"requested":0,"limit":limit_per_exchange}
            continue
        selected = []
        for row in rows:
            if exchange == "KRX":
                symbol = row["Code"]
                if row["Market"] not in ("KOSPI",) or not re.fullmatch(r"\d{5}0",symbol):
                    continue
                selected.append({"market":"KR","symbol":symbol+(".KS" if row["Market"]=="KOSPI" else ".KQ"),
                                 "benchmark":"^KS11", "name":row["Name"], "raw_exchange_code":row["Market"]})
            else:
                name = row["Name"]
                if re.search(r"\b(warrant|rights|preferred|units|etf|fund)\b",name,re.IGNORECASE):
                    continue
                selected.append({"market":"US","symbol":row["Symbol"].replace(".","-"),"benchmark":"^GSPC","name":name,
                                 "raw_exchange_code":row.get('Exchange'), 'listing_exchange_family':exchange,
                                 "market_cap":row.get('MarketCap'), "market_cap_currency":row.get('Currency'),
                                 "market_cap_as_of":row.get('CapAsOf'), 'tradable':row.get('tradable'),
                                 'listing_observed_at':row.get('listing_observed_at')})
        take = selected if limit_per_exchange is None else selected[:limit_per_exchange]
        metadata[exchange] = {"state":"available","listed":len(rows),"eligible_by_listing_heuristic":len(selected),"requested":len(take),
                              "ordering":"provider_market_cap_order", "limit":limit_per_exchange}
        universe.extend(take)
    unique = {(r["market"],r["symbol"]):r for r in universe}
    return list(unique.values()),metadata
