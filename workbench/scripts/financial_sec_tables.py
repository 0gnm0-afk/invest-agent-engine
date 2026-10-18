"""Verified GAAP earnings tables and explicit historical non-distributions."""
import re
from datetime import date
from bs4 import BeautifulSoup


def text(node):return re.sub(r'[\s\u200b]+',' ',node.get_text(' ',strip=True)).strip()


def quarter_facts(raw,end,filing,source_id,currency='USD',issuer=''):
    soup=BeautifulSoup(raw,'html.parser');target=date.fromisoformat(end);out=[]
    for ti,table in enumerate(soup.find_all('table')):
        low=text(table).lower()
        if not re.search(r'three months ended',low) or 'basic' not in low or 'diluted' not in low:continue
        if 'non-gaap' in low or 'reconciliation' in low:continue
        trs=table.find_all('tr');header=' '.join(text(tr) for tr in trs[:12])
        if not re.search(target.strftime('%B')+r'\s+'+str(target.day)+r'\b',header,re.I):continue
        years=re.findall(r'\b20\d{2}\b',header)
        if len(years) in (4,6) and years[:len(years)//2]==years[len(years)//2:]:
            n=len(years)//2;indices=list(range(n))
            if len(set(years[:n]))!=n:continue
        elif len(years)==2 and len(set(years))==2:indices=[0,1]
        elif len(years)==5 and years[0]==years[1]==years[3] and years[2]==years[4]:indices=[0,2]
        else:continue
        if str(target.year) not in [years[i] for i in indices]:continue
        if len(years)==5:
            dates=re.findall(r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})',header,re.I)
            if len(dates)!=5 or any((dates[i][0].lower(),int(dates[i][1]))!=(target.strftime('%B').lower(),target.day) for i in (0,2,3,4)):continue
        if (ypos:=low.find('year ended'))>=0 and ypos<low.find('three months ended'):continue
        heading=header+' '+' '.join(text(p) for p in table.find_all_previous(['p','div'],limit=8))
        scale=1000 if re.search(r'in thousands',heading,re.I) else 1e6 if re.search(r'in millions',heading,re.I) else None
        normalized_issuer=re.sub('[^a-z]','',issuer.lower())
        in_eps=False
        for ri,tr in enumerate(trs):
            cells=[text(c) for c in tr.find_all(['td','th'],recursive=False)]
            label=next((c for c in cells if re.search('[A-Za-z]',c)),None)
            if not label:continue
            l=label.lower();norm=re.sub('[^a-z]','',l)
            if re.search(r'weighted|shares used',l):in_eps=False
            if re.search(r'(?:income|loss|earnings).*per\s+(?:(?:common|ordinary)\s+)?share',l):in_eps=True
            tag=None
            if in_eps:
                if re.search(r'\bbasic\b',l) and not re.search(r'\bdiluted\b',l):tag='EarningsPerShareBasic'
                elif re.search(r'\bdiluted\b',l) and not re.search(r'\bbasic\b',l):tag='EarningsPerShareDiluted'
            if not tag:
                tag={'revenue':'Revenues','totalrevenue':'Revenues','totalrevenues':'Revenues','revenues':'Revenues',
                     'operatingincome':'OperatingIncomeLoss','operatingloss':'OperatingIncomeLoss',
                     'incomefromoperations':'OperatingIncomeLoss','lossfromoperations':'OperatingIncomeLoss',
                     'netincome':'NetIncomeLoss','netloss':'NetIncomeLoss',
                     'netincomeandcomprehensiveincomeincludingnoncontrollinginterests':'ProfitLoss'}.get(norm)
                if normalized_issuer and norm in ('netincomeattributableto'+normalized_issuer,'netincomeandcomprehensiveincomeattributableto'+normalized_issuer):tag='NetIncomeLoss'
            if not tag:continue
            is_eps=tag.startswith('EarningsPerShare')
            if not is_eps and scale is None:continue
            nums=' '.join(cells[cells.index(label)+1:]).replace('$','').replace(',','')
            vals=re.findall(r'\(\s*\d+(?:\.\d+)?\s*\)|-?\d+(?:\.\d+)?|[—–-]',nums)
            if len(vals)!=len(years) or any('%' in c for c in cells):continue
            for index in indices:
                year=years[index]
                token=vals[index];value=0.0 if token in ('—','–','-') else float(re.sub(r'\s+','',token).replace('(','-').replace(')',''))
                period_end=date(int(year),target.month,target.day).isoformat()
                start=date(int(year),target.month-2,1).isoformat() if target.month>=3 and target.day in (30,31) else None
                # Off-calendar fiscal quarter boundaries require explicit dates.
                if start is None:continue
                out.append(dict(start=start,end=period_end,val=value if is_eps else value*scale,tag=tag,unit=currency+'/shares' if is_eps else currency,
                    filed=filing['filingDate'],accn=filing['accessionNumber'],form=filing['form'],source_id=source_id,
                    evidence=dict(rule='explicit_gaap_quarter_table',table=ti,row=ri,label=label,header=header,scale=1 if is_eps else scale)))
    keys={(x['end'],x['tag']) for x in out}
    return [items[0] for key in sorted(keys) if (items:=[x for x in out if (x['end'],x['tag'])==key]) and len({x['val'] for x in items})==1]


def distribution_zeros(raw,filing,source_id):
    soup=BeautifulSoup(raw,'html.parser');out=[];end=filing.get('reportDate') or filing.get('filingDate')
    if not end:return []
    # A never-paid statement is historical evidence, unlike a future intention.
    for p in soup.find_all(['p','div']):
        t=text(p)
        if len(t)>2500:continue
        if re.search(r'\bwe have (?:never|not) (?:(?:declared (?:or|and) )?paid|paid (?:or|and) declared) (?:any )?(?:cash )?dividends',t,re.I) and not re.search(r'other than|except|apart from',t,re.I):
            out.append(dict(start='1900-01-01',end=filing['filingDate'],fields=['cash_dividends','dividend'],filed=filing['filingDate'],
                accession=filing['accessionNumber'],source_id=source_id,evidence=t))
            break
    whole=text(soup)
    m=re.search(r'Item\s+16\.?E\.?\s*\.?\s*PURCHASES OF EQUITY SECURITIES BY THE ISSUER AND AFFILIATED PURCHASERS\s+None\.',whole,re.I)
    if m:
        e=date.fromisoformat(end)
        start=date(e.year-1,e.month,e.day).isoformat()
        from datetime import timedelta
        start=(date.fromisoformat(start)+timedelta(days=1)).isoformat()
        out.append(dict(start=start,end=end,fields=['buybacks'],filed=filing['filingDate'],accession=filing['accessionNumber'],source_id=source_id,evidence=m[0]))
    return out


