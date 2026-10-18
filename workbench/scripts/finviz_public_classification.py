"""Parse saved, publicly displayed Finviz map/screener classification. Never execute JS."""
import json
import re
from collections import defaultdict
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup


def parse_alias_quote(data, symbol, provider_symbol, exchange):
    """Accept aliases only when Finviz itself links the exact source ticker/exchange."""
    soup=BeautifulSoup(data,'html.parser')
    if not soup.title or not soup.title.get_text().startswith(symbol+' - '):
        raise ValueError('alias_quote_symbol_unconfirmed')
    sectors,industries,exchanges,identities=set(),set(),set(),set()
    for a in soup.select('a[href]'):
        url=urlparse(a['href'])
        for f in parse_qs(url.query).get('f',[''])[0].split(','):
            if f.startswith('sec_'):sectors.add(a.get_text(' ',strip=True))
            if f.startswith('ind_'):industries.add(a.get_text(' ',strip=True))
            if f.startswith('exch_'):exchanges.add({'exch_nasd':'NASDAQ','exch_nyse':'NYSE'}.get(f,f))
        if url.scheme=='https' and url.hostname=='www.google.com' and url.path.startswith('/finance/quote/'):
            identities.add(url.path.removeprefix('/finance/quote/'))
    if identities!={provider_symbol+':'+exchange} or exchanges!={exchange}:
        raise ValueError('alias_quote_identity_unconfirmed')
    if len(sectors)!=1 or len(industries)!=1:raise ValueError('alias_quote_classification_conflict')
    return {'symbol':symbol,'provider_symbol':provider_symbol,'exchange':exchange,
            'sector':sectors.pop(),'industry':industries.pop(),'name':soup.title.get_text()}


def parse_map(data):
    text=data.decode('utf-8')
    # Only consume the data object literal. Never eval/import/execute the bundle.
    matches=list(re.finditer(r'\b\w+\.exports\s*=\s*(?=\{name:"Root")',text))
    if len(matches)!=1:
        raise ValueError('finviz_map_root_missing_or_ambiguous')
    tail=text[matches[0].end():]
    converted=re.sub(r'("(?:\\.|[^"\\])*")|([A-Za-z_$][\w$]*)(?=\s*:)',
                     lambda m:m.group(1) or json.dumps(m.group(2)),tail)
    tree,_=json.JSONDecoder().raw_decode(converted)
    if set(tree)!= {'name','children'} or tree['name']!='Root':
        raise ValueError('finviz_map_schema')
    rows=[]
    for sector in tree['children']:
        if set(sector)!={'name','children'}:raise ValueError('finviz_sector_schema')
        for industry in sector['children']:
            if set(industry)!={'name','children'}:raise ValueError('finviz_industry_schema')
            for leaf in industry['children']:
                if not {'name','description'}.issubset(leaf) or set(leaf)-{'name','description','value'}:
                    raise ValueError('finviz_leaf_schema')
                if not all(isinstance(v,str) and v for v in (leaf['name'],leaf['description'],sector['name'],industry['name'])):
                    raise ValueError('finviz_empty_classification')
                rows.append({'symbol':leaf['name'],'name':leaf['description'],
                             'sector':sector['name'],'industry':industry['name']})
    if not rows:raise ValueError('finviz_map_empty')
    return rows


def parse_screener(data):
    soup=BeautifulSoup(data,'html.parser')
    tables=soup.select('table.screener_table')
    if len(tables)!=1:raise ValueError('finviz_screener_table_missing')
    table=tables[0]
    header=[c.get_text(' ',strip=True) for c in table.select('tr')[0].find_all(['td','th'],recursive=False)]
    if header[:6]!=['No.','Ticker','Company','Sector','Industry','Country']:
        raise ValueError('finviz_screener_header_changed')
    rows=[]
    for tr in table.select('tr')[1:]:
        cells=tr.find_all('td',recursive=False)
        if not cells:continue
        if len(cells)!=len(header):raise ValueError('finviz_screener_row_shape')
        symbol=cells[1].get('data-boxover-ticker')
        if not symbol:raise ValueError('finviz_screener_ticker_missing')
        links={parse_qs(urlparse(a['href']).query).get('t',[''])[0] for a in cells[1].select('a[href]')}
        if links!={symbol}:raise ValueError('finviz_screener_ticker_conflict')
        rows.append({'symbol':symbol,'name':cells[2].get_text(' ',strip=True),
                     'sector':cells[3].get_text(' ',strip=True),'industry':cells[4].get_text(' ',strip=True)})
    return rows


def load_public(root, checked, load, inputs, allowed_sectors):
    """Check source chain and preserve source-specific conflicts for the caller."""
    manifest_path=root/'finviz-public.json'
    if not manifest_path.exists():return {}
    manifest=load(checked(manifest_path,None,inputs))
    sources=root/'sources'
    by_symbol=defaultdict(list)
    for entry in manifest['documents']:
        path=(sources/entry['file']).resolve()
        if not path.is_relative_to(sources.resolve()):raise ValueError('source_path_outside_root')
        meta=load(checked(path.with_suffix(path.suffix+'.meta.json'),None,inputs))
        checked(path,meta['sha256'],inputs)
        url=urlparse(meta['url'])
        final=urlparse(meta['final_url'])
        if (url.scheme!='https' or url.hostname!='finviz.com' or meta['status']!=200 or
                final.scheme!='https' or final.hostname!='finviz.com'):
            raise ValueError('finviz_source_domain_or_status')
        if entry['kind']=='full_market_map':
            page=(sources/entry['page']).resolve()
            if not page.is_relative_to(sources.resolve()):raise ValueError('source_path_outside_root')
            page_meta=load(checked(page.with_suffix(page.suffix+'.meta.json'),None,inputs))
            checked(page,page_meta['sha256'],inputs)
            page_url=urlparse(page_meta['url'])
            if (page_url.scheme!='https' or page_url.hostname!='finviz.com' or
                    page_url.path!='/map.ashx' or parse_qs(page_url.query).get('t')!=['sec_all']):
                raise ValueError('full_market_page_required')
            soup=BeautifulSoup(page.read_bytes(),'html.parser')
            links=soup.select('link[data-chunk-id="map_base_sec_all"]')
            if len(links)!=1 or links[0].get('href')!=url.path:
                raise ValueError('map_page_bundle_link_mismatch')
            records=parse_map(path.read_bytes())
        elif entry['kind']=='screener':
            if not url.path.startswith('/screener'):raise ValueError('screener_url_required')
            requested=set(parse_qs(url.query).get('t',[''])[0].split(','))
            records=parse_screener(path.read_bytes())
            if any(r['symbol'] not in requested for r in records):
                raise ValueError('screener_unrequested_ticker_no_implicit_alias')
        elif entry['kind']=='alias_quote':
            if url.path not in ('/quote.ashx','/stock') or parse_qs(url.query).get('t')!=[entry['symbol']]:
                raise ValueError('alias_quote_url_mismatch')
            records=[parse_alias_quote(path.read_bytes(),entry['symbol'],entry['provider_symbol'],entry['exchange'])]
        else:raise ValueError('unsupported_finviz_document')
        for r in records:
            if r['sector'] not in allowed_sectors:raise ValueError('unknown_finviz_sector')
            identity=('finviz_published_cross_provider_ticker_and_exchange_link_no_isin'
                      if entry['kind']=='alias_quote' else 'exact_unique_US_universe_ticker_source_has_no_exchange_or_isin')
            by_symbol[r.get('provider_symbol',r['symbol'])].append({**r,'source':entry['kind'],'url':meta['url'],
                'path':str(path),'sha256':meta['sha256'],'observed_at':meta['observed_at'],
                'identity_method':identity})
    # Alias evidence must also agree with the saved canonical map/screener records.
    for records in list(by_symbol.values()):
        for r in list(records):
            if r['source']=='alias_quote':
                records.extend(by_symbol.get(r['symbol'],[]))
    return dict(by_symbol)


def select_classification(records, legacy, exchange, unique, applied, age_issue, *, classification_only=False):
    proofs=list(records)
    if legacy:proofs.append(legacy[2])
    if not unique:return None,'finviz_universe_ticker_ambiguous',proofs
    if len({r['path'] for r in records})!=len(records):
        return None,'finviz_duplicate_source_ticker',proofs
    if len({(r['sector'],r['industry']) for r in records})>1:
        return None,'finviz_direct_sources_conflict',proofs
    for r in proofs:
        issue=age_issue(r['observed_at'],applied)
        if issue:return None,issue,proofs
    if any(r.get('exchange',exchange)!=exchange for r in records):
        return None,'finviz_exchange_conflict',proofs
    if not classification_only and records and any(r['industry'].startswith('Closed-End Fund') or r['industry']=='Exchange Traded Fund' for r in records):
        return None,'finviz_security_type_conflict',proofs
    sector=records[0]['sector'] if records else None
    if legacy:
        value,reason,_=legacy
        if reason:return None,reason,proofs
        if value['exchange']!=exchange:return None,'finviz_exchange_conflict',proofs
        if sector and value['group_name']!=sector:return None,'finviz_direct_sources_conflict',proofs
        sector=value['group_name']
    return sector,None if sector else 'finviz_public_evidence_absent',proofs
