"""Company labels for presentation only; symbols remain the data keys."""
import re

PREFERRED = {"005930": "삼성전자", "005930.KS": "삼성전자",
             "NVO": "노보노디스크", "NVDA": "엔비디아"}


def company_names(rows):
    names = {}
    for row in rows:
        name = row.get("name") or row.get("company_name")
        if not isinstance(name, str) or not name.strip():
            continue
        for key in (row.get("symbol"), row.get("quote_symbol")):
            if key and name != key:
                names[key] = " ".join(name.split())
    return {**names, **PREFERRED}


def company_label(row, names=None):
    symbol = row.get("quote_symbol") or row.get("symbol")
    return (names or {}).get(symbol) or company_names([row]).get(symbol) or row.get("name") or symbol or "기업명 미확인"


def name_in_prose(text, names):
    # Use only the companies relevant to this analysis; leave technical labels intact.
    names = {k:v for k,v in names.items() if len(k)>1 and k not in {"MA","SMA","EMA","ATR","RS","EPS","PER","USD","KRW"}}
    if not names:
        return text
    pattern = r"(?<![A-Za-z0-9_.])(" + "|".join(re.escape(k) for k in sorted(names,key=len,reverse=True)) + r")(?![A-Za-z0-9_]|[.][A-Za-z0-9])"
    return re.sub(pattern, lambda m:names[m[0]], text)
