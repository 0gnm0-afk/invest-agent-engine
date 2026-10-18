"""Standard IFRS concepts and verified foreign-issuer statement attachments."""
import re
from copy import deepcopy
from datetime import date
from bs4 import BeautifulSoup

IFRS_MAP={
 'sales':['Revenue','RevenueFromContractsWithCustomers'],
 'income':['ProfitLossAttributableToOwnersOfParent'],
 'common_income':['ProfitLossFromContinuingOperationsAttributableToOrdinaryEquityHoldersOfParentEntity'],
 'net_income':['ProfitLoss'],'gross':['GrossProfit'],'operating':['ProfitLossFromOperatingActivities'],
 'pretax':['ProfitLossBeforeTax'],'tax':['IncomeTaxExpenseContinuingOperations'],
 'eps':['DilutedEarningsLossPerShare'],'basic_eps':['BasicEarningsLossPerShare'],
 'liabilities':['Liabilities'],'assets':['Assets'],'equity':['EquityAttributableToOwnersOfParent'],
 'total_equity':['Equity'],'cash':['CashAndCashEquivalents'],
 'current_assets':['CurrentAssets'],'current_liabilities':['CurrentLiabilities'],
 'inventory':['Inventories'],'receivables':['CurrentTradeReceivables'],
 'debt_short':['CurrentBorrowings'],'debt_long':['NoncurrentBorrowings'],
 'cfo':['CashFlowsFromUsedInOperatingActivities'],
 'capex':['PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities'],
 'da':['AdjustmentsForDepreciationDepletionAndAmortisationExpense'],
 'shares':['NumberOfSharesOutstanding'],'issued_shares':['NumberOfSharesIssuedAndFullyPaid'],
 'weighted_shares':['AdjustedWeightedAverageShares'],'basic_weighted_shares':['WeightedAverageShares'],
 'dividend_declared':['DividendsRecognisedAsDistributionsToOwnersPerShare'],
 'dividend_paid':['DividendsPaidOrdinarySharesPerShare'],
 # Ordinary-share distributions have a verified scope; total DividendsPaid is
 # deliberately not assumed to exclude preferred/non-controlling holders.
 'cash_dividends':['DividendsPaidToOrdinaryShareholders'],
 'buybacks':['PurchaseOfTreasuryShares']}


def reporting_currency(source):
    """Distinguish the comparative reporting currency from convenience USD."""
    tax=source['facts']['facts']['ifrs-full'];rows=[]
    for tag in IFRS_MAP['sales']:
        for unit,values in tax.get(tag,{}).get('units',{}).items():
            if not re.fullmatch('[A-Z]{3}',unit):continue
            rows.extend((unit,v) for v in values if v.get('form') in ('20-F','20-F/A','40-F','40-F/A')
                        and v.get('start') and 330<=(date.fromisoformat(v['end'])-date.fromisoformat(v['start'])).days+1<=380)
    if not rows:raise ValueError('SEC_IFRS_REPORTING_CURRENCY_UNVERIFIED')
    rank=max((v.get('filed',''),v.get('accn','')) for _,v in rows)
    coverage={}
    for unit,v in rows:
        if (v.get('filed',''),v.get('accn',''))==rank:coverage.setdefault(unit,set()).add((v['start'],v['end']))
    most=max(map(len,coverage.values()));winners=[u for u,spans in coverage.items() if len(spans)==most]
    if len(winners)!=1:raise ValueError('SEC_REPORTING_CURRENCY_AMBIGUOUS')
    return winners[0]


def revenue_rows(tax,cutoff,as_of,currency):
    from financial_revenue import select_sec_revenue
    aliases={'Revenues':'Revenue','RevenueFromContractWithCustomerExcludingAssessedTax':'RevenueFromContractsWithCustomers'}
    out=select_sec_revenue({k:tax[v] for k,v in aliases.items() if v in tax},cutoff=cutoff,as_of=as_of,unit=currency)
    return [dict(row,tag=aliases[row['tag']]) for row in out]


def compact(text):return re.sub(r'[^a-z]','',text.lower())
def issuer_key(text):
    return compact(re.sub(r'\b(?:company|limited|corporation|incorporated|ltd|co|inc|plc|a[ /]+s)\b','',text.lower()))


def statement_tables(raw,identity,filing,source_id,currency):
    """English consolidated statements: explicit unit, dates and amount columns."""
    soup=BeautifulSoup(raw,'html.parser');out=[]
    if soup.find('ix:nonfraction'):return out
    document_text=soup.get_text(' ',strip=True)
    issuer=issuer_key(identity['name'])
    if len(issuer)<8 or issuer not in issuer_key(document_text[:6000]):return out
    currency_names={'TWD':r'New Taiwan Dollars|NT\$','DKK':r'Danish kroner|DKK',
                    'EUR':r'euros|EUR','GBP':r'pounds sterling|GBP','JPY':r'Japanese yen|JPY','USD':r'U\.S\. dollars|US dollars|USD'}
    name='(?:'+currency_names.get(currency,re.escape(currency))+')'
    for ti,table in enumerate(soup.find_all('table')):
        trs=table.find_all('tr',recursive=False)
        if not trs and table.find('tbody'):trs=table.find('tbody').find_all('tr',recursive=False)
        grid=[]
        for tr in trs:
            row=[]
            for c in tr.find_all(['th','td'],recursive=False):
                span=int(c.get('colspan','1'))
                if not 1<=span<=300:row=[];break
                row.extend([c.get_text(' ',strip=True)]+['']*(span-1))
            grid.append(row)
        if len(grid)<5 or table.find('table'):continue
        # The closest statement heading rules out segment/related-party tables.
        before=[str(t).strip() for t in table.find_all_previous(string=True,limit=24) if str(t).strip()]
        prefix=' '.join(reversed(before[:12]))
        heading=re.search(r'CONSOLIDATED (?:BALANCE SHEETS|STATEMENTS OF (?:COMPREHENSIVE INCOME|INCOME|PROFIT|CASH FLOWS|FINANCIAL POSITION))',prefix,re.I)
        condensed=re.search(r'(?:CONDENSED |APPENDIX \d+: )(?:INCOME STATEMENT|BALANCE SHEET|CASH FLOW STATEMENT)',prefix,re.I)
        if not heading and not (condensed and re.search(r'consolidated financial statements.{0,300}IAS 34',document_text,re.I)):continue
        header=' '.join(' '.join(row) for row in grid[:6])
        unit_text=prefix+' '+header
        if not re.search(name,unit_text,re.I):continue
        scale=1e3 if re.search(r'(?:in thousands|'+name+r' thousands)',unit_text,re.I) else 1e6 if re.search(r'(?:in millions|'+name+r' millions?)',unit_text,re.I) else None
        if scale is None:continue
        header=' '.join(' '.join(row) for row in grid[:6])
        is_balance=bool(re.search('BALANCE SHEETS?|FINANCIAL POSITION',prefix,re.I))
        dates=re.findall(r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),?\s+(20\d{2})',header,re.I)
        year_row=next((row for row in grid[:6] if sum(bool(re.fullmatch(r'20\d{2}',c)) for c in row)>=2),None)
        amount_row=next((row for row in grid[:6] if sum(c.lower()=='amount' for c in row)>=2),None)
        indices=[i for i,c in enumerate(amount_row or year_row or []) if c.lower()=='amount' or re.fullmatch(r'20\d{2}',c)]
        spans=[]
        # Explicit Q/H labels and day-month-year dates in IAS 34 condensed statements.
        short_row=next((row for row in grid[:6] if sum(bool(re.fullmatch(r'(?:Q[1-4]|H[12]|9M|FY) 20\d{2}',c)) for c in row)>=2),None)
        short_dates=next((row for row in grid[:6] if sum(bool(re.fullmatch(r'\d{1,2} [A-Za-z]{3} 20\d{2}',c)) for c in row)>=2),None)
        if not indices and not is_balance and short_row:
            from calendar import monthrange
            indices=[i for i,c in enumerate(short_row) if re.fullmatch(r'(?:Q[1-4]|H[12]|9M|FY) 20\d{2}',c)]
            for i in indices:
                kind,year=short_row[i].split();year=int(year);end_month=int(kind[1])*3 if kind[0]=='Q' else int(kind[1])*6 if kind[0]=='H' else 9 if kind=='9M' else 12
                start_month=end_month-2 if kind[0]=='Q' else end_month-5 if kind[0]=='H' else 1
                spans.append(dict(start=date(year,start_month,1).isoformat(),end=date(year,end_month,monthrange(year,end_month)[1]).isoformat()))
        elif not indices and is_balance and short_dates:
            from datetime import datetime
            indices=[i for i,c in enumerate(short_dates) if re.fullmatch(r'\d{1,2} [A-Za-z]{3} 20\d{2}',c)]
            spans=[dict(end=datetime.strptime(short_dates[i],'%d %b %Y').date().isoformat()) for i in indices]
        elif is_balance:
            if len(indices)!=len(dates):continue
            spans=[dict(end=date(int(y),list(('January','February','March','April','May','June','July','August','September','October','November','December')).index(m.title())+1,int(d)).isoformat()) for m,d,y in dates]
        else:
            if not year_row:continue
            years=[int(c) for c in year_row if re.fullmatch(r'20\d{2}',c)]
            periods=re.findall(r'(Three|Six|Nine|Twelve) Months Ended\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})',header,re.I)
            if not periods:
                periods=[('Twelve',m,d) for m,d in re.findall(r'Years? Ended\s+(December)\s+(31)',header,re.I)]
                if not periods and filing.get('reportDate','').endswith('-12-31') and re.search(r'YEARS? ENDED DECEMBER 31',document_text[:6000],re.I):periods=[('Twelve','December','31')]
            if not periods or len(years)!=len(indices) or len(years)%len(periods):continue
            for i,y in enumerate(years):
                duration,m,d=periods[i//(len(years)//len(periods))];months={'three':3,'six':6,'nine':9,'twelve':12}[duration.lower()]
                month=list(('January','February','March','April','May','June','July','August','September','October','November','December')).index(m.title())+1
                if month-months<0:break
                spans.append(dict(start=date(y,month-months+1,1).isoformat(),end=date(y,month,int(d)).isoformat()))
            if len(spans)!=len(indices):continue
        if not spans or max(s['end'] for s in spans)!=filing.get('reportDate'):continue
        section='';attribution=''
        aliases={'netrevenue':'Revenue','revenue':'Revenue','revenues':'Revenue','totalrevenue':'Revenue','totalrevenues':'Revenue','netsales':'Revenue',
            'incomefromoperations':'ProfitLossFromOperatingActivities','operatingincome':'ProfitLossFromOperatingActivities',
            'operatingprofit':'ProfitLossFromOperatingActivities','grossprofit':'GrossProfit','profitbeforeincometaxes':'ProfitLossBeforeTax','netincomefortheperiod':'ProfitLoss','netincome':'ProfitLoss','profitfortheperiod':'ProfitLoss',
            'netprofit':'ProfitLoss','basicearningspershare':'BasicEarningsLossPerShare','dilutedearningspershare':'DilutedEarningsLossPerShare',
            'totalcurrentassets':'CurrentAssets','totalcurrentliabilities':'CurrentLiabilities','totalliabilities':'Liabilities',
            'totalassets':'Assets','totalequity':'Equity','equityattributabletoshareholdersoftheparent':'EquityAttributableToOwnersOfParent',
            'cashandcashequivalents':'CashAndCashEquivalents','inventories':'Inventories',
            'netcashgeneratedbyoperatingactivities':'CashFlowsFromUsedInOperatingActivities',
            'netcashfromoperatingactivities':'CashFlowsFromUsedInOperatingActivities','netcashgeneratedfromoperatingactivities':'CashFlowsFromUsedInOperatingActivities',
            'treasurystockacquired':'PurchaseOfTreasuryShares','repurchaseofownshares':'PurchaseOfTreasuryShares'}
        for ri,row in enumerate(grid):
            label=next((c for c in row if re.search('[A-Za-z]',c)), '')
            clean=compact(re.sub(r'\([^)]*\)','',label))
            if clean=='assets':section='assets'
            elif clean.startswith('liabilities'):section='liabilities'
            if clean.startswith('netincomeattributable'):attribution='income'
            elif clean.startswith('totalcomprehensive') or clean.startswith('othercomprehensive'):attribution='other'
            tag=aliases.get(clean)
            if clean=='total' and section=='assets':tag='Assets';section=''
            if clean=='shareholdersoftheparent' and attribution=='income':tag='ProfitLossAttributableToOwnersOfParent'
            # A cash-flow dividend line is consolidated. Adopt it as an ordinary
            # distribution only if it reconciles to four disclosed parent payouts.
            if clean=='cashdividends':tag='DividendsPaidClassifiedAsFinancingActivities'
            if not tag:continue
            for index,span in zip(indices,spans):
                if index>=len(row):continue
                boundary=indices[indices.index(index)+1] if indices.index(index)+1<len(indices) else len(row)
                if amount_row:
                    percent=next((i for i in range(index+1,min(boundary,len(amount_row))) if amount_row[i]=='%'),None)
                    if percent is not None:boundary=percent
                token=re.sub(r'[\s,$]','',' '.join(row[index:boundary]));token=token.replace('(','-').replace(')','')
                if token in ('-','—','–'):value=0.0
                elif re.fullmatch(r'-?\d+(?:\.\d+)?',token):value=float(token)
                else:continue
                if tag in ('DividendsPaidClassifiedAsFinancingActivities','PurchaseOfTreasuryShares'):value=abs(value)
                eps=tag in IFRS_MAP['basic_eps']+IFRS_MAP['eps']
                out.append(dict(**span,val=value if eps else value*scale,unit=currency+'/shares' if eps else currency,
                    tag=tag,source_id=source_id,accn=filing['accessionNumber'],filed=filing['filingDate'],form=filing['form'],
                    evidence=dict(rule='ifrs_consolidated_statement_amount_column_v1',heading=prefix,table=ti,row=ri,column=index,label=label),
                    formula='공시 연결 재무제표의 명시 기간·통화·단위 원값'))
    return out


def enrich_ifrs(collector,source):
    from financial_sec_filing import inline_facts
    source=deepcopy(source);currency=reporting_currency(source);source['reporting_currency']=currency
    identity=source['identity'];cik=str(int(identity['cik']));records=source.get('filing_records',[])
    cutoff=str(date.today().year-6)+'-01-01'
    annuals=sorted([r for r in records if r['form'] in ('20-F','20-F/A','40-F','40-F/A') and r.get('reportDate','')>=cutoff],key=lambda r:r['filingDate'],reverse=True)[:5]
    quarters=sorted([r for r in records if r['form'] in ('6-K','6-K/A') and r.get('reportDate','')>=cutoff
        and (re.search(r'financial|(?:^|[-_])fs(?:x|[-_.])|earnings|results|quarter|q[1-4]',r.get('primaryDocument',''),re.I)
        or r.get('reportDate','').endswith(('-03-31','-06-30','-09-30')) and not re.search('revenue|monthend|repurchase|buyback',r.get('primaryDocument',''),re.I))],key=lambda r:(r['reportDate'],bool(re.search(r'financial|(?:^|[-_])fs(?:x|[-_.])',r.get('primaryDocument',''),re.I)),r['filingDate']),reverse=True)
    by_period={}
    for filing in quarters:by_period.setdefault(filing['reportDate'],filing)
    quarters=list(by_period.values())[:12]
    out=[];source['format_issues']=[];source['enrichment_errors']=[]
    source['enrichment_plan']=dict(taxonomy='ifrs-full',filings=[])
    for filing in annuals+quarters:
        document=filing.get('primaryDocument','');acc=filing['accessionNumber']
        if not re.fullmatch('[A-Za-z0-9_.-]+',document):continue
        base=f'https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace("-","")}/'
        name=acc+'-ifrs.html'
        try:
            raw=collector.fetch(base+document,name,sec=True,binary=True)
            parsed=inline_facts(raw,cik,filing,name,currency,taxonomy='ifrs-full')
            parsed+=statement_tables(raw,identity,filing,name,currency)
            if not parsed:
                soup=BeautifulSoup(raw,'html.parser');links=[]
                for a in soup.find_all('a',href=True):
                    href=a['href']
                    context=(a.find_parent('tr') or a.parent).get_text(' ',strip=True)
                    financial=bool(re.search(r'annual report|(?:consolidated )?financial statements',context,re.I))
                    if re.fullmatch(r'[A-Za-z0-9_.-]+\.htm[l]?',href,re.I) and href!=document and href not in links and (filing['form'].startswith('6-K') or financial):links.append(href)
                for href in links[:3]:
                    extra_name=acc+'-'+href
                    extra=collector.fetch(base+href,extra_name,sec=True,binary=True)
                    parsed+=inline_facts(extra,cik,filing,extra_name,currency,taxonomy='ifrs-full',resource_raw=raw)
                    parsed+=statement_tables(extra,identity,filing,extra_name,currency)
                    if parsed:break
            out.extend(parsed)
            source['enrichment_plan']['filings'].append(dict(accession=acc,source_id=name,accepted_facts=len(parsed)))
            if not parsed and (filing['form'].startswith(('20-F','40-F')) or re.search(r'consolidated financial statements|consolidated statements of',BeautifulSoup(raw,'html.parser').get_text(' ',strip=True),re.I)):source['format_issues'].append(dict(code='unmapped_ifrs_statement',source_id=name,message='새로운 공시 형식입니다. 코드 수정이 필요합니다. IFRS 공시의 연결 범위·통화·기간·표 구조를 검증하지 못했습니다.'))
        except Exception as exc:source['enrichment_errors'].append(dict(source_id=name,error=type(exc).__name__))
    source['inline_facts']=out
    source['security_basis']=dict(per_share='ordinary_share',shares='ordinary_shares',reporting_currency=currency,
        notice='재무제표는 공시 통화·보통주 기준입니다. ADR 가격의 통화·예탁 비율과 구분합니다.')
    return source
