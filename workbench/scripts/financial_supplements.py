"""Narrow official-filing supplements for fields absent from SEC companyfacts."""
import re
from datetime import date
from bs4 import BeautifulSoup


def inline_shares(raw,cik,filing,source_id):
    soup=BeautifulSoup(raw,'html.parser');contexts={};output=[]
    for ctx in soup.find_all('xbrli:context'):
        ident=ctx.find('xbrli:identifier');instant=ctx.find('xbrli:instant')
        members=ctx.find_all('xbrldi:explicitmember')
        if not ident or not instant or not ident.get_text().strip().isdigit() or int(ident.get_text())!=int(cik):continue
        # Only total common stock, never a share class, subsidiary or stock plan dimension.
        if any(m.get('dimension')!='us-gaap:StatementEquityComponentsAxis' or m.get_text()!='us-gaap:CommonStockMember' for m in members):continue
        if ctx.find('xbrldi:typedmember'):continue
        contexts[ctx['id']]=instant.get_text()
    for node in soup.find_all('ix:nonfraction',attrs={'name':'us-gaap:CommonStockSharesOutstanding'}):
        end=contexts.get(node.get('contextref'))
        unit=soup.find('xbrli:unit',id=node.get('unitref'))
        measures=unit.find_all('xbrli:measure') if unit else []
        if (not end or not unit or unit.find('xbrli:divide') or len(measures)!=1
                or measures[0].get_text().strip().lower().split(':')[-1]!='shares'):continue
        text=node.get_text().replace(',','').strip()
        if not re.fullmatch(r'\d+(\.\d+)?',text):continue
        value=float(text)*10**int(node.get('scale','0'))
        output.append(dict(end=end,val=value,accn=filing['accessionNumber'],filed=filing['filingDate'],form=filing['form'],source_id=source_id,
                           context=node.get('contextref'),decimals=node.get('decimals'),tag='CommonStockSharesOutstanding'))
    return output


def sec_supplement(collector,source):
    identity=source['identity'];cik=str(int(identity['cik']));facts=source['facts']['facts'].get('us-gaap',{})
    shares=facts.get('CommonStockSharesOutstanding',{}).get('units',{}).get('shares',[])
    ends={r['end'] for r in shares};cutoff=str(date.today().year-6)+'-01-01'
    revenue=[r for tag in ('Revenues','RevenueFromContractWithCustomerExcludingAssessedTax','RevenueFromContractWithCustomerIncludingAssessedTax','SalesRevenueNet') for r in facts.get(tag,{}).get('units',{}).get('USD',[]) if r.get('end','')>=cutoff]
    need={r['end'] for r in revenue}-ends
    recent=identity['filings']['recent'];records=[dict(zip(recent,values)) for values in zip(*recent.values())]
    # Fetch older filing metadata only when needed; these are issuer metadata, not all-company financials.
    for item in identity['filings'].get('files',[]):
        if item.get('filingTo','')>=cutoff and min((r.get('reportDate') or '9999' for r in records),default='9999')>cutoff:
            name=item['name']
            if not re.fullmatch(r'CIK\d+-submissions-\d+\.json',name):continue
            older=collector.fetch('https://data.sec.gov/submissions/'+name,name,sec=True)
            records.extend(dict(zip(older,values)) for values in zip(*older.values()))
    extras=[]
    for filing in sorted(records,key=lambda x:x['filingDate'],reverse=True):
        end=filing.get('reportDate','')
        if filing['form'] not in ('10-Q','10-Q/A') or end<cutoff or end not in need:continue
        accession=filing['accessionNumber'];document=filing['primaryDocument']
        if not re.fullmatch(r'[A-Za-z0-9_.-]+',document):continue
        name=accession+'-shares.html'
        url=f'https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace("-","")}/{document}'
        raw=collector.fetch(url,name,sec=True,binary=True)
        found=inline_shares(raw,cik,filing,name);extras.extend(found);need-={r['end'] for r in found}
    source['filing_records']=records
    source['inline_shares']=extras
    return source


def nvidia_supplement(collector,source):
    if int(source['identity']['cik'])!=1045810:return source
    source['splits']=[]
    for when,ratio,url in [('2021-07-20',4,'https://investor.nvidia.com/files/doc_downloads/doc_faq/08/Investor-NVIDIA-2021-Stock-Split-FAQ-08.9.2021.pdf'),('2024-06-10',10,'https://investor.nvidia.com/files/doc_downloads/2024/06/nvidia-2024-stock-split_faq_investors.pdf')]:
        name='split-'+when+'.pdf';collector.fetch(url,name,binary=True)
        source['splits'].append(dict(date=when,ratio=ratio,url=url,source_id=name))
    records=source.get('filing_records', [])
    targets={r['filingDate'] for r in records if r['form']=='10-K' and r.get('reportDate','')>=str(date.today().year-6)+'-01-01'}
    newest_date=max((r['filingDate'] for r in records if r['form']=='10-Q'),default='')
    from datetime import datetime
    annual_dates=list(targets)
    targets={r['filingDate'] for r in records if r['form']=='8-K' and any(0 <= (date.fromisoformat(d)-date.fromisoformat(r['filingDate'])).days <= 60 for d in annual_dates) and '2.02' in r.get('items','')}
    targets.add(newest_date)
    selected=[]; published_by_url={}
    from urllib.parse import urljoin
    for filing in records:
        if filing['form']!='8-K' or filing['filingDate'] not in targets:continue
        base='https://www.sec.gov/Archives/edgar/data/1045810/'+filing['accessionNumber'].replace('-','')+'/'
        name=filing['accessionNumber']+'-earnings-index.html'
        raw=collector.fetch(base+filing['primaryDocument'],name,sec=True,binary=True)
        soup=BeautifulSoup(raw,'html.parser')
        for tr in soup.find_all('tr'):
            if '99.1' not in tr.get_text() or 'Press Release' not in tr.get_text():continue
            anchors=[a for a in tr.find_all('a',href=True) if 'Press Release' in a.get_text()]
            if anchors:
                link=urljoin(base,anchors[0]['href']);selected.append(link);published_by_url[link]=filing['filingDate']
                break
    newest=next((u for u in selected if published_by_url[u]==newest_date),None)
    source['ir_eps']=[];source['guidance']=[]
    for i,link in enumerate(selected):
        if not link.startswith('https://www.sec.gov/Archives/edgar/data/1045810/'):continue
        name=f'ir-earnings-{i}.html';raw=collector.fetch(link,name,sec=True,binary=True)
        s=BeautifulSoup(raw,'html.parser');text=s.get_text(' ',strip=True)
        # GAAP summary table is expressly labelled; select only its diluted EPS row.
        end=re.search(r'(?:quarter|year) ended (January|February|March|April|May|June|July|August|September|October|November|December) (\d{1,2}),? (20\d{2})',text,re.I)
        if end:
            from datetime import datetime
            end_date=datetime.strptime(' '.join(end.groups()),'%B %d %Y').date().isoformat()
        else:end_date=None
        published=re.search(r'(20\d{2})/(\d{2})/(\d{2})',str(s.find('time') or ''))
        pubnode=s.select_one('.date, .datefield, .press-release-date, .module_date-time')
        pubtext=pubnode.get_text(' ',strip=True) if pubnode else ''
        # Date is also available in article metadata on Q4 IR pages.
        meta=s.find('meta',attrs={'property':'article:published_time'})
        pub=meta.get('content','')[:10] if meta else None
        if not pub:
            match=re.search(r'(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},\s+20\d{2}',text)
            if match:
                from email.utils import parsedate_to_datetime
                from datetime import datetime
                cleaned=match[0].replace('.','')
                for fmt in ('%b %d, %Y','%B %d, %Y'):
                    try:pub=datetime.strptime(cleaned,fmt).date().isoformat();break
                    except ValueError:pass
        pub = published_by_url.get(link) or pub
        for table in s.find_all('table'):
            tt=table.get_text(' ',strip=True)
            if 'GAAP' not in tt[:250] or 'Non-GAAP' in tt[:150] or 'Diluted earnings per share' not in tt:continue
            for tr in table.find_all('tr'):
                cells=[c.get_text(' ',strip=True) for c in tr.find_all(['td','th'])]
                if cells and cells[0].strip().rstrip('*')=='Diluted earnings per share':
                    vals=[float(x.replace('$','').replace(',','').strip()) for x in cells[1:] if re.fullmatch(r'\$?\s*\d+(?:\.\d+)?',x)]
                    if vals and end_date and pub:
                        source['ir_eps'].append(dict(end=end_date,val=vals[0],filed=pub,source_id=name,url=link,tag='GAAP diluted EPS · company earnings release'))
                    break
            break
        if link==newest:
            match=re.search(r'outlook for the (\w+) quarter of fiscal (20\d{2}) is as follows.*?Revenue is expected to be \$([\d.]+) billion, plus or minus (\d+)%',text,re.I)
            if match and pub:
                source['guidance'].append(dict(period=f'FY {match[2]} {match[1]} quarter',standard='US GAAP · 회사 매출 가이던스',published=pub,url=link,
                                               text=f'매출 {float(match[3]):,.2f} B USD ±{match[4]}%. 회사 발표이며 실적·애널리스트 추정과 별도.',source_id=name))
    return source

def samsung_preferred_price(collector,stock):
    if stock.get('market')!='KR' or stock.get('symbol','').split('.')[0]!='005930':return
    from toss_market_client import TossMarketClient
    from toss_candles import daily_series
    import hashlib,json
    from datetime import datetime,timezone
    end=stock.get('bars', [{}])[-1].get('date') if stock.get('bars') else None
    if not end:return
    url='https://www.samsung.com/global/ir/stock-information/listing-Info/'
    raw=collector.fetch(url,'samsung-listing.html',binary=True)
    text=BeautifulSoup(raw,'html.parser').get_text(' ',strip=True)
    if '005930' not in text or '005935' not in text:raise ValueError('preferred_listing_identity_unverified')
    logs=[]
    client=TossMarketClient(observer=logs.append)
    price=daily_series(client,'005935.KS','KR',[end],max_pages=1)
    if len(price['bars'])!=1 or price['bars'][0]['date']!=end:raise ValueError('preferred_same_session_missing')
    name='preferred-price.json';body=json.dumps(dict(price=price,requests=logs),ensure_ascii=False).encode('utf-8')
    (collector.folder/name).write_bytes(body)
    collector.sources.append(dict(id=name,url='https://openapi.tossinvest.com/api/v1/candles',fetched_at=datetime.now(timezone.utc).isoformat(),sha256=hashlib.sha256(body).hexdigest(),bytes=len(body)))
    stock['preferred_price']=dict(symbol='005935.KS',currency='KRW',bar=price['bars'][0],mapping_source=url)
