"""Official filing supplements with explicit issuer, period, scope and unit evidence."""
import re
from datetime import date
from decimal import Decimal
from bs4 import BeautifulSoup


def inline_facts(raw,cik,filing,source_id,currency='USD',taxonomy='us-gaap',resource_raw=None):
    soup=BeautifulSoup(raw,'html.parser');contexts={};out=[]
    resources=[BeautifulSoup(resource_raw,'html.parser'),soup] if resource_raw else [soup]
    for ctx in [c for doc in resources for c in doc.find_all('xbrli:context')]:
        ident=ctx.find('xbrli:identifier')
        if not ident or not ident.get_text().strip().isdigit() or int(ident.get_text())!=int(cik):continue
        members=ctx.find_all('xbrldi:explicitmember')
        common_only=bool(members) and all(m.get('dimension')=='us-gaap:StatementEquityComponentsAxis' and m.get_text().strip()=='us-gaap:CommonStockMember' for m in members)
        if taxonomy=='ifrs-full':
            common_only=bool(members) and all(m.get('dimension')=='ifrs-full:ComponentsOfEquityAxis' and m.get_text().strip()=='ifrs-full:TreasurySharesMember' for m in members)
        if ctx.find('xbrldi:typedmember') or members and not common_only:continue
        instant=ctx.find('xbrli:instant');start=ctx.find('xbrli:startdate');end=ctx.find('xbrli:enddate')
        if instant:contexts[ctx['id']]=dict(end=instant.get_text().strip(),common_only=common_only)
        elif start and end:contexts[ctx['id']]=dict(start=start.get_text().strip(),end=end.get_text().strip(),common_only=common_only)
    units={u.get('id'):u for doc in resources for u in doc.find_all('xbrli:unit')}
    for node in soup.find_all('ix:nonfraction'):
        name=node.get('name','');period=contexts.get(node.get('contextref'));unit=units.get(node.get('unitref'))
        if not period or not unit or node.get('xsi:nil')=='true':continue
        tag=name.split(':')[-1];mapping=None
        if taxonomy=='ifrs-full' and (not name.startswith('ifrs-full:') or period.get('common_only') and tag!='PurchaseOfTreasuryShares'):continue
        if taxonomy=='us-gaap' and period.get('common_only') and tag not in ('PaymentsOfDividendsCommonStock','PaymentsForRepurchaseOfCommonStock','CommonStockDividendsPerShareDeclared','CommonStockDividendsPerShareCashPaid','CommonStockSharesOutstanding'):continue
        if taxonomy=='us-gaap' and not name.startswith('us-gaap:'):
            # A custom name alone is insufficient. Require an exact, whole-row
            # semantic label and an entity-wide context with the correct unit.
            tr=node.find_parent('tr')
            label=next((c.get_text(' ',strip=True) for c in tr.find_all(['td','th'],recursive=False)
                        if re.search('[A-Za-z]',c.get_text()) and not c.find('ix:nonfraction')), '') if tr else ''
            normalized=re.sub('[^a-z]','',label.lower())
            aliases={'interestexpense':'InterestExpense','totalinterestexpense':'InterestExpense',
                     'totalcurrentassets':'AssetsCurrent','totalcurrentliabilities':'LiabilitiesCurrent',
                     'totalliabilities':'Liabilities','totalassets':'Assets',
                     'netincome':'NetIncomeLoss','netloss':'NetIncomeLoss',
                     'repurchaseofordinaryshares':'PaymentsForRepurchaseOfCommonStock','repurchasesofordinaryshares':'PaymentsForRepurchaseOfCommonStock',
                     'repurchaseofcommonstock':'PaymentsForRepurchaseOfCommonStock','repurchasesofcommonstock':'PaymentsForRepurchaseOfCommonStock',
                     'cashdividendspaidoncommonstock':'PaymentsOfDividendsCommonStock',
                     'operatingincome':'OperatingIncomeLoss','operatingloss':'OperatingIncomeLoss'}
            mapped=aliases.get(normalized)
            if not mapped:continue
            table=tr.find_parent('table') if tr else None
            table_text=table.get_text(' ',strip=True).lower() if table else ''
            if mapped=='InterestExpense' and (re.search(r'lease|pension|capitaliz|equityinvestment|taxpenalt|netinterest',tag,re.I) or
                    re.search(r'finance lease|pension|equity investment|related party',table_text)):
                continue
            mapping=dict(original_tag=name,label=label,rule='exact_entity_wide_row_label_v1')
            tag=mapped
        measures=[m.get_text().strip().split(':')[-1] for m in unit.find_all('xbrli:measure')]
        u=measures[0]+'/shares' if unit.find('xbrli:divide') and len(measures)==2 and measures[1]=='shares' else measures[0] if len(measures)==1 else None
        correction=None
        if tag in ('CommonStockDividendsPerShareDeclared','CommonStockDividendsPerShareCashPaid') and currency=='JPY' and u!=currency+'/shares':
            p=node.find_parent('p');label=p.get_text(' ',strip=True) if p else ''
            if p and len(p.find_all('ix:nonfraction'))==1 and re.fullmatch(r'Cash dividends (?:declared|paid)\s*\((?:¥|JPY)\s*'+re.escape(node.get_text().strip())+r'\s*per share\)',label,re.I):
                correction=dict(rule='explicit_per_share_text_currency',original_unit=u,corrected_unit='JPY/shares',label=label)
                u='JPY/shares'
        if u not in (currency,'shares',currency+'/shares') or mapping and u!=currency:continue
        text=re.sub(r'\s+','',node.get_text()).replace(',','')
        if text in ('—','–','-') and node.get('format','').lower().split(':')[-1] in ('zerodash','numdash','fixed-zero'):text='0'
        if not re.fullmatch(r'-?\d+(?:\.\d+)?',text):continue
        val=float(Decimal(text)*Decimal(10)**int(node.get('scale','0')))*(-1 if node.get('sign')=='-' else 1)
        out.append(dict(**period,val=val,unit=u,tag=tag,source_id=source_id,custom_mapping=mapping,
            accn=filing['accessionNumber'],filed=filing['filingDate'],form=filing['form'],
            context=node.get('contextref'),decimals=node.get('decimals'),**({'evidence':correction} if correction else {})))
    return out


def untagged_annual(raw,identity,filing,source_id,currency='USD'):
    """Conservative parser for pre-XBRL issuers' own consolidated annual tables."""
    soup=BeautifulSoup(raw,'html.parser')
    if soup.find('ix:nonfraction') or filing['form'] not in ('10-K','10-K/A','10-Q','10-Q/A','S-1','S-1/A','424B4'):return []
    canonical=lambda x:re.sub('[^a-z0-9]','',x.lower()).removesuffix('inc')
    issuer=canonical(identity['name']);out=[]
    aliases={'totalrevenues':'Revenues','incomefromoperations':'OperatingIncomeLoss',
       'lossfromoperations':'OperatingIncomeLoss','netlossandcomprehensiveloss':'NetIncomeLoss',
       'netincome':'NetIncomeLoss','netloss':'NetIncomeLoss','interestexpense':'InterestExpense',
       'totalassets':'Assets','totalliabilities':'Liabilities','totalequity':'StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest',
       'totalcurrentassets':'AssetsCurrent','totalcurrentliabilities':'LiabilitiesCurrent',
       'basicanddilutedlosspershare':'EarningsPerShareBasic','basicanddilutedearningspershare':'EarningsPerShareBasic'}
    for ti,table in enumerate(soup.find_all('table')):
        prev=[]
        for node in table.previous_elements:
            if not getattr(node,'name',None):continue
            if node.name=='table' or node.find_parent('table'):break
            if node.name in ('p','div','font') and not node.find('table') and len(node.get_text())<400:
                prev.append(re.sub(r'[\s\u200b]+',' ',node.get_text(' ',strip=True)))
                if len(prev)>=12:break
        if not any(canonical(p)==issuer for p in prev):continue  # Excludes attached subsidiary statements.
        heading=' '.join(prev);h=canonical(heading)
        if currency=='JPY' and 'japanese yen' not in heading.lower():continue
        balance='consolidatedbalancesheets' in h
        operations='consolidatedstatementsofoperations' in h
        if not (balance or operations) or 'in thousands' not in heading.lower():continue
        if operations and not re.search(r'\byears?\s+ended\b',heading,re.I):continue
        dates=re.search(r'(?:Years Ended )?(December|January|February|March|April|May|June|July|August|September|October|November)\s+(\d{1,2}),?(?:\s*(20\d{2}))?',heading+' '+' '.join(tr.get_text(' ',strip=True) for tr in table.find_all('tr')[:4]),re.I)
        if not dates:continue
        from datetime import datetime
        month=datetime.strptime(dates[1],'%B').month;day=int(dates[2])
        years=next(([int(x) for x in re.findall(r'\b20\d{2}\b',tr.get_text(' ',strip=True))] for tr in table.find_all('tr')[:4]
                    if re.search(r'\b20\d{2}\b',tr.get_text(' ',strip=True))),[])
        if not 1<=len(years)<=3 or years!=sorted(set(years),reverse=True):continue
        for ri,tr in enumerate(table.find_all('tr')):
            cells=[c.get_text(' ',strip=True) for c in tr.find_all(['td','th'],recursive=False)]
            label=next((c for c in cells if re.search('[A-Za-z]',c)),None)
            if not label:continue
            key=canonical(re.sub(r'\(\d+\)','',label));tag=aliases.get(key)
            if not tag:continue
            numeric=' '.join(cells[cells.index(label)+1:]).replace('$','').replace('¥','').replace(',','')
            tokens=re.findall(r'\(\s*\d+(?:\.\d+)?\s*\)|-?\d+(?:\.\d+)?|[—–-]',numeric)
            if len(tokens)!=len(years):continue
            for year,token in zip(years,tokens):
                val=0.0 if token in ('—','–','-') else float(re.sub(r'\s+','',token).replace('(','-').replace(')',''))
                eps='PerShare' in tag;end=date(year,month,day).isoformat()
                start=date(year-1 if month!=12 else year,month%12+1,1).isoformat()
                row=dict(end=end,val=val if eps else val*1000,tag=tag,unit=currency+'/shares' if eps else currency,
                    filed=filing['filingDate'],form=filing['form'],accn=filing['accessionNumber'],source_id=source_id,
                    evidence=dict(table=ti,row=ri,label=label,heading=heading,scale=1 if eps else 1000),
                    formula='공시 연결재무제표의 명시 행·연도·단위')
                if not balance:row['start']=start
                out.append(row)
                if eps:out.append(dict(row,tag='EarningsPerShareDiluted'))
    return out


def balance_equation(raw,facts):
    """Prove the same balance sheet's sole equity total before subtraction."""
    soup=BeautifulSoup(raw,'html.parser');out=[]
    for a in facts:
        if a['tag']!='Assets' or a.get('start') or a['unit']!='USD':continue
        rows=[x for x in facts if x.get('context')==a.get('context') and x['unit']=='USD']
        pick=lambda tag:next((x for x in rows if x['tag']==tag),None)
        e=pick('StockholdersEquity');t=pick('LiabilitiesAndStockholdersEquity')
        if not e or not t or t['val']!=a['val']:continue
        nodes=soup.find_all('ix:nonfraction',attrs={'name':'us-gaap:Assets','contextref':a['context']})
        table=next((n.find_parent('table') for n in nodes if n.find_parent('table')),None)
        if not table:continue
        text=table.get_text(' ',strip=True).lower()
        if re.search(r'noncontroll|non.controll|redeemable|temporary equity|mezzanine',text):continue
        tags={n.get('name') for n in table.find_all('ix:nonfraction',attrs={'contextref':a['context']})}
        if not {'us-gaap:StockholdersEquity','us-gaap:LiabilitiesAndStockholdersEquity'}<=tags:continue
        if not 0<=a['val']-e['val']:continue
        evidence=dict(assets=a['val'],equity=e['val'],balance_total=t['val'],context=a['context'],
                      scope='동일 연결 재무상태표에 별도 비지배/상환가능/중간자본 항목 없음')
        out.append(dict(a,tag='Liabilities',val=a['val']-e['val'],formula='동일 재무상태표 자산총계 − 명시 자본총계',evidence=evidence))
        out.append(dict(e,tag='StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest',
                        formula='공시 재무상태표의 유일한 자본총계 확인',evidence=evidence))
    return out


def quarter_eps(raw,end,filing,source_id):
    from financial_sec_tables import quarter_facts
    return [dict(x,eps_basis='basic' if x['tag']=='EarningsPerShareBasic' else 'diluted')
            for x in quarter_facts(raw,end,filing,source_id) if x['tag'].startswith('EarningsPerShare') and x['end']==end]


def enrich_quarter_eps(collector,source):
    from urllib.parse import urljoin
    records=source.get('filing_records',[]);cik=str(int(source['identity']['cik']))
    annuals=sorted([r for r in records if r['form'] in ('10-K','20-F') and r.get('reportDate')],key=lambda x:x['reportDate'],reverse=True)[:4]
    gaps,_=enrichment_gaps(source)
    needed={g['date'] for g in gaps if g.get('frame')=='quarterly' and g.get('field') in ('basic_eps','eps','sales','operating','income')}
    for annual in annuals:
        end=annual['reportDate']
        if end not in needed:continue
        filings=[r for r in records if (r['form']=='8-K' and '2.02' in r.get('items','') or r['form']=='6-K') and
                 end<r['filingDate']<=annual['filingDate'] and (date.fromisoformat(annual['filingDate'])-date.fromisoformat(r['filingDate'])).days<=60]
        for filing in sorted(filings,key=lambda x:x['filingDate'],reverse=True)[:8]:
            acc=filing['accessionNumber'];base=f'https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace("-","")}/'
            try:
                name=acc+'-eps-index.html';raw=collector.fetch(base+filing['primaryDocument'],name,sec=True,binary=True)
                soup=BeautifulSoup(raw,'html.parser')
                links={urljoin(base,a['href']) for tr in soup.find_all('tr') if re.search(r'99[.\-]\d',tr.get_text()) for a in tr.find_all('a',href=True)}
                for idx,link in enumerate(sorted(links)):
                    if not link.startswith(base) or not re.search(r'\.html?$',link,re.I):continue
                    name=acc+f'-eps-{idx}.html';body=collector.fetch(link,name,sec=True,binary=True)
                    from financial_sec_tables import quarter_facts
                    from financial_returns import reporting_currency
                    found=quarter_facts(body,end,filing,name,reporting_currency(source),source['identity']['name'])
                    source.setdefault('inline_facts',[]).extend(found)
                    eps=[x for x in found if x['tag'].startswith('EarningsPerShare')]
                    if not eps and re.search(r'(?:earnings|income|loss).*per\s+share',BeautifulSoup(body,'html.parser').get_text(' ',strip=True),re.I):
                        source.setdefault('format_issues',[]).append(dict(code='unmapped_quarter_eps_table',source_id=name,period_end=end,
                            message='새로운 공시 형식입니다. 코드 수정이 필요합니다. 실적 발표의 분기·기본/희석 EPS 열을 검증하지 못했습니다.'))
            except Exception as exc:source.setdefault('enrichment_errors',[]).append(dict(source_id=acc,error=type(exc).__name__))
    remaining,_=enrichment_gaps(source)
    unresolved={g['date'] for g in remaining if g.get('frame')=='quarterly' and g.get('field') in ('basic_eps','eps','sales','operating','income')}
    source['format_issues']=[i for i in source.get('format_issues',[]) if i['code']!='unmapped_quarter_eps_table' or i.get('period_end') in unresolved]
    return source


def enrichment_gaps(source):
    from financial_model import sec_normalize
    from financial_focus import summarize
    from financial_business import coverage
    try:
        normal=sec_normalize(source);r=coverage(summarize(normal,{}))
        gaps=r['data_quality']['missing']+r['data_quality']['supplemental_missing']
        if r['quarterly']:
            shown={row['date'] for row in r['quarterly']}
            for annual in r['annual']:
                if min(shown)<=annual['date']<=max(shown) and annual['date'] not in shown:
                    gaps.extend(dict(frame='quarterly',date=annual['date'],field=k,reason='해당 연말 분기 전체 실적 미확보') for k in ('sales','operating','income','basic_eps','eps'))
        return gaps,r['data_quality']['history_limited']
    except ValueError:
        return [dict(field='statements',date='')],True


def enrich_sec(collector,source):
    identity=source['identity'];cik=str(int(identity['cik']));out=[]
    source.setdefault('enrichment_errors',[]);source.setdefault('format_issues',[])
    taxonomy=source['facts']['facts'].get('us-gaap',{})
    if not taxonomy and 'ifrs-full' in source['facts']['facts']:
        from financial_sec_ifrs import enrich_ifrs
        return enrich_ifrs(collector,source)
    if not taxonomy:
        source['format_issues'].append(dict(code='unsupported_taxonomy',source_id='companyfacts.json',
            message='새로운 공시 형식입니다. 코드 수정이 필요합니다. 현재 미국 수집기는 US GAAP 매핑을 지원합니다.'))
        return source
    gaps,limited=enrichment_gaps(source)
    source['enrichment_plan']=dict(gaps=gaps,history_limited=limited,filings=[])
    records=source.get('filing_records',[])
    cutoff=str(date.today().year-6)+'-01-01'
    dates={x['date'] for x in gaps if x.get('date') and x.get('frame')=='quarterly'}
    quarter_gaps=any(x.get('frame')=='quarterly' for x in gaps)
    registrations=sorted([r for r in records if r['form'] in ('S-1','S-1/A','424B4') and r.get('filingDate','')>=cutoff],
                         key=lambda r:r['filingDate'],reverse=True)[:3] if limited else []
    targets=[]
    for filing in records:
        form=filing['form'];end=filing.get('reportDate','')
        annual=form in ('10-K','10-K/A','20-F','20-F/A')
        interim=form in ('10-Q','10-Q/A') or form in ('6-K','6-K/A') and end in dates
        # Annual originals also provide Q4 EPS and comparative balance sheets.
        relevant=any(d<=end and (date.fromisoformat(end)-date.fromisoformat(d)).days<=800 for d in dates) if end else False
        if end>=cutoff and (annual or interim and quarter_gaps and relevant):targets.append(filing)
    targets+=registrations
    for filing in targets:
        document=filing['primaryDocument'];acc=filing['accessionNumber']
        if not re.fullmatch(r'[A-Za-z0-9_.-]+',document):continue
        name=acc+'-statement.html'
        try:
            raw=collector.fetch(f'https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace("-","")}/{document}',name,sec=True,binary=True)
            from financial_returns import reporting_currency
            parsed=inline_facts(raw,cik,filing,name,reporting_currency(source))
            from financial_sec_tables import distribution_zeros
            source.setdefault('explicit_distribution_zeros',[]).extend(distribution_zeros(raw,filing,name))
            extra=untagged_annual(raw,identity,filing,name,reporting_currency(source))
            from financial_sec_distributions import preferred_paid,financing_zeros
            extra.extend(preferred_paid(raw,parsed))
            source['explicit_distribution_zeros'].extend(financing_zeros(raw,parsed,filing,name,reporting_currency(source)))
            out.extend(parsed);out.extend(balance_equation(raw,parsed));out.extend(extra)
            record=dict(accession=acc,form=filing['form'],source_id=name,accepted_facts=len(parsed)+len(extra))
            source['enrichment_plan']['filings'].append(record)
            soup=BeautifulSoup(raw,'html.parser')
            text=soup.get_text(' ',strip=True)
            if not parsed and not extra and re.search(r'consolidated\s+(?:balance\s+sheets|statements\s+of)',text,re.I):
                source['format_issues'].append(dict(code='unmapped_statement_layout_or_entity',source_id=name,
                    accession=acc,message='새로운 공시 형식입니다. 코드 수정이 필요합니다. 발행사·전신회사 범위 또는 재무표 구조를 자동 검증하지 못했습니다.'))
        except Exception as exc:
            # Transport failures are never presented as a new filing format.
            source['enrichment_errors'].append(dict(source_id=name,error=type(exc).__name__))
    from financial_sec_distributions import common_paid_from_total
    distribution_facts=[dict(x,tag=tag,unit=unit,source_id='companyfacts.json') for tag,concept in taxonomy.items()
        if tag in ('PaymentsOfOrdinaryDividends','PaymentsOfDividendsPreferredStockAndPreferenceStock')
        for unit,rows in concept.get('units',{}).items() for x in rows]
    out.extend(common_paid_from_total([*distribution_facts,*out]))
    existing={(tag,unit,x.get('start'),x['end'],x.get('accn')) for tag,concept in taxonomy.items()
              for unit,rows in concept.get('units',{}).items() for x in rows}
    out=[x for x in out if (x['tag'],x['unit'],x.get('start'),x['end'],x['accn']) not in existing]
    source['inline_facts']=[*source.get('inline_facts',[]),*out]
    return enrich_quarter_eps(collector,source)
