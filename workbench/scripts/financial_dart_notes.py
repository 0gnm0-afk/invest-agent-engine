"""Read explicit interest expense from the selected financial statement's notes.

No cash interest, net finance expense, pension interest, or lease-only substitute.
The original XML and the table/column/scale are retained with every accepted value.
"""
import io
import re
import zipfile
from copy import deepcopy
from bs4 import BeautifulSoup


def clean(text):
    return re.sub(r'\s+', '', str(text or '')).replace('\u3000','')


def amount(text):
    text=clean(text).replace(',','').replace('−','-')
    if text in ('-','—','–'):return 0.0  # A dash in a numeric financial table, not an absent API field.
    if not re.fullmatch(r'\(?-?\d+(?:\.\d+)?\)?',text):return None
    return float(text.replace('(','-').replace(')',''))


def note_interest(raw,report,scope,source_id):
    z=zipfile.ZipFile(io.BytesIO(raw))
    receipt=str(report['fs']['list'][0]['rcept_no'])
    if receipt+'.xml' not in z.namelist():return []
    soup=BeautifulSoup(z.read(receipt+'.xml'),'xml')
    section=None;candidates=[]
    # Track document section, rather than infer consolidation from a company name.
    for node in soup.find_all(['TITLE','TABLE']):
        text=clean(node.get_text(' ',strip=True))
        if node.name=='TITLE':
            if re.match(r'3\.연결재무제표주석',text):section='CFS'
            elif re.match(r'5\.재무제표주석',text):section='OFS'
            elif re.match(r'\d+\.(?:연결재무제표|재무제표|요약재무정보|배당에관한사항|기타재무에관한사항)$',text):section=None
            continue
        if section!=scope or node.find('TABLE'):continue
        # Only expense notes with a named financial-expense total or explicit ICR inputs.
        if '이자비용' not in text:continue
        reconciliation=('영업활동에서창출된현금' in text and '(2)조정' in text) or (bool(re.search(r'영업(?:활동)?(?:으로부터|에서)?창출된현금',text)) and
                        ('당기순이익' in text or '당기순손익' in text) and
                        any(x in text for x in ('조정','현금의유출이없는비용','비현금항목')))
        if not reconciliation and any(x in text for x in ('상각후원가측정','상각후원가로측정','확정급여','사외적립')):continue
        before=[]
        for prev in node.find_all_previous(['P','TITLE','TABLE'],limit=8):
            if prev.name=='TABLE' and prev.find('TABLE'):continue
            before.append(clean(prev.get_text(' ',strip=True)))
        unit_text=text[:200]+' '.join(before[:4])
        if not reconciliation and not any(x in text+' '.join(before[:2]) for x in ('금융비용','금융원가','이자보상배율')):continue
        match=re.search(r'단위[:：]?(백만원|천원|원)',unit_text)
        if not match:continue
        scale={'원':1,'천원':1000,'백만원':1000000}[match[1]]
        table_rows=[]
        for tr in node.find_all('TR'):
            if tr.find_parent('TABLE') is not node:continue
            cells=tr.find_all(['TD','TH','TE'],recursive=False)
            table_rows.append([clean(c.get_text(' ',strip=True)) for c in cells])
        header=' '.join(' '.join(r) for r in table_rows[:4])
        for idx,cells in enumerate(table_rows):
            if not cells:continue
            labels=('이자비용','이자비용(금융원가)','이자비용(금융비용)')
            label_index=next((i for i,c in enumerate(cells) if re.sub(r'^(?:\d+[.)]|[-·])','',c) in labels),None)
            if label_index is None:continue
            label=cells[label_index];numeric_cells=cells[label_index+1:]
            vals=[amount(x) for x in numeric_cells]
            if not vals or any(x is None for x in vals):continue
            if any(x in text for x in ('순금융손익','순금융원가')) and all(x<=0 for x in vals):vals=[-x for x in vals]
            q=report['quarter']; direct=cumulative=previous=previous_direct=previous_cumulative=None
            # Modern tagged one-column tables encode both the period and consolidation.
            tagged=node.find_all(attrs={'ACONTEXT':True})
            contexts=[x.get('ACONTEXT','') for x in tagged if clean(x.get_text()) in numeric_cells]
            current=any(re.match(r'CFY'+str(report['year'])+r'd',x) for x in contexts)
            past=any(re.match(r'PFY',x) for x in contexts)
            if q==4:
                if len(vals)>=2 and (('당기' in header and '전기' in header) or re.search(r'제\d+기',header)):
                    direct=vals[0];previous=vals[1]
                elif len(vals)==1 and current and not past:direct=vals[0]
                elif len(vals)==1 and past and not current:previous=vals[0]
            else:
                if len(vals)==4 and '3개월' in header and '누적' in header:
                    direct,cumulative,previous_direct,previous_cumulative=vals
                elif len(vals)==2 and current and not past and '3개월' in header and '누적' in header:
                    direct,cumulative=vals
                elif len(vals)==2 and ('당반기' in header or '당분기' in header or '당기' in header):
                    cumulative=vals[0];previous_cumulative=vals[1]
                    if q==1:direct=cumulative;previous_direct=previous_cumulative
                elif len(vals)==1 and current and not past:
                    ctx=' '.join(contexts)
                    if re.search(r'd(?:Q'+str(q)+r'|HYQ|Q[13]Q|FQQ|TQQ)(?:_|$)',ctx):direct=vals[0]
                    elif re.search(r'd(?:YTD|H1|HYA|Q[13]A|FQA|TQA)(?:_|$)',ctx):cumulative=vals[0]
            if direct is None and cumulative is None and previous is None:continue
            candidates.append(dict(direct=None if direct is None else direct*scale,
                cumulative=None if cumulative is None else cumulative*scale,
                previous=None if previous is None else previous*scale,
                previous_direct=None if previous_direct is None else previous_direct*scale,
                previous_cumulative=None if previous_cumulative is None else previous_cumulative*scale,source_id=source_id,
                table_index=len(candidates),row=idx,label=label,scale=scale,header=header,scope=scope,
                presentation='profit_reconciliation' if reconciliation else 'finance_note'))
    return [c for c in candidates if c['presentation']=='finance_note'] or candidates


def statement_periods(raw,scope):
    z=zipfile.ZipFile(io.BytesIO(raw));name=next((n for n in z.namelist() if re.fullmatch(r'\d+\.xml',n)),None)
    if not name:return []
    soup=BeautifulSoup(z.read(name),'xml')
    for table in soup.find_all('TABLE'):
        text=clean(table.get_text(' ',strip=True))
        if len(text)>1000 or not re.match(r'(?:연결)?(?:포괄)?손익계산서',text):continue
        if text.startswith('연결')!=(scope=='CFS'):continue
        periods=re.findall(r'(20\d{2})[.년/-](\d{1,2})[.월/-](\d{1,2})[.일]?부터(20\d{2})[.년/-](\d{1,2})[.월/-](\d{1,2})[.일]?까지',text)
        if periods:return [dict(start=f'{int(a):04d}-{int(b):02d}-{int(c):02d}',end=f'{int(d):04d}-{int(e):02d}-{int(f):02d}') for a,b,c,d,e,f in periods]
    return []


def enrich_dart(collector,source):
    """Fetch notes for missing explicit interest only, preserving failed supplementation."""
    source=deepcopy(source);scope=source.get('statement_scope','CFS');source.setdefault('enrichment_errors',[]);source.setdefault('format_issues',[])
    for report in source['reports']:
        rows=report['fs'].get('list',[])
        if not rows:continue
        receipt=str(rows[0]['rcept_no']);name=receipt+'-notes.zip'
        try:
            raw=collector.fetch('https://opendart.fss.or.kr/api/document.xml',name,
                dict(crtfc_key=collector.config['DART_API_KEY'],rcept_no=receipt),binary=True)
            periods=statement_periods(raw,scope)
            report['declared_periods']=periods
            if periods and periods[0]['end']!=report['end']:
                actual=periods[0]['end'];actual_q=int(actual[5:7])//3
                if report['quarter']!=4 and actual_q in (1,2,3):
                    report['original_quarter']=report['quarter'];report['original_year']=report['year']
                    report.update(year=int(actual[:4]),quarter=actual_q,end=actual)
                else:
                    report['period_mapping_error']='과거 결산월 변경: 비12월 연간/연말 중간공시는 기간 매핑 미지원'
                    continue
            for p in periods:
                if not p['end'].endswith(('03-31','06-30','09-30','12-31')):continue
                year=int(p['end'][:4]);quarter=int(p['end'][5:7])//3
                source.setdefault('report_periods',[]).append(dict(**p,year=year,quarter=quarter,source_id=name,
                    period_kind='annual' if report['quarter']==4 else 'cumulative'))
            if any(r.get('account_id') in ('ifrs-full_InterestExpense','dart_InterestExpenseFinanceExpense') and r.get('sj_div') in ('IS','CIS') for r in rows):continue
            candidates=note_interest(raw,dict(report,quarter=report.get('original_quarter',report['quarter'])),scope,name)
        except Exception as exc:
            source['enrichment_errors'].append(dict(source_id=name,error=type(exc).__name__));continue
        signatures={(c['direct'],c['cumulative'],c['previous']) for c in candidates}
        if not signatures:
            # Do not classify ordinary absent data or network failures as a parser issue.
            z=zipfile.ZipFile(io.BytesIO(raw));body=z.read(receipt+'.xml') if receipt+'.xml' in z.namelist() else b''
            doc=BeautifulSoup(body,'xml')
            if not any('주석' in t.get_text() for t in doc.find_all('TITLE')) and any(n.endswith('_00761.xml' if scope=='CFS' else '_00760.xml') for n in z.namelist()):
                source['format_issues'].append(dict(code='notes_in_auditor_attachment',source_id=name,
                    message='새로운 공시 형식입니다. 코드 수정이 필요합니다. 정정 본문과 첨부 감사보고서 주석 연결을 지원하지 않습니다.'))
            else:
                section=None;unparsed=False
                for n in doc.find_all(['TITLE','P','TABLE']):
                    t=clean(n.get_text(' ',strip=True))
                    if n.name=='TITLE':
                        if re.match(r'3\.연결재무제표주석',t):section='CFS'
                        elif re.match(r'5\.재무제표주석',t):section='OFS'
                        elif re.match(r'[4-9]\.',t):section=None
                    if section!=scope or n.name!='TABLE' or n.find('TABLE'):continue
                    labels=[clean(x.get_text()) for x in n.find_all(['TD','TE'])]
                    context=' '.join(clean(x.get_text()) for x in n.find_all_previous(['P','TITLE'],limit=4))
                    if '이자비용' in labels and any(word in t+context for word in ('금융비용','금융원가')) and not any(word in t for word in ('확정급여','사외적립','리스부채')):
                        unparsed=True;break
                if unparsed:source['format_issues'].append(dict(code='unmapped_interest_note',source_id=name,
                    message='새로운 공시 형식입니다. 코드 수정이 필요합니다. 금융비용 주석의 이자비용 기간·단위·표 구조를 검증하지 못했습니다.'))
            continue
        # Independent rounded tables may coexist; accept only a unique consistent series.
        direct={c['direct'] for c in candidates if c['direct'] is not None}
        cumulative={c['cumulative'] for c in candidates if c['cumulative'] is not None}
        previous={c['previous'] for c in candidates if c['previous'] is not None}
        previous_direct={c['previous_direct'] for c in candidates if c['previous_direct'] is not None}
        previous_cumulative={c['previous_cumulative'] for c in candidates if c['previous_cumulative'] is not None}
        if any(len(x)>1 for x in (direct,cumulative,previous,previous_direct,previous_cumulative)):
            source['enrichment_errors'].append(dict(source_id=name,error='interest_note_conflict'));continue
        template=next((r for r in rows if r.get('sj_div') in ('IS','CIS')),None)
        if not template:continue
        row={**template,'account_id':'ifrs-full_InterestExpense','account_nm':'이자비용','ord':'supplement',
             'thstrm_amount':next(iter(direct),None),'thstrm_add_amount':next(iter(cumulative),None),
             'frmtrm_amount':next(iter(previous),None),'frmtrm_q_amount':next(iter(previous_direct),None),
             'frmtrm_add_amount':next(iter(previous_cumulative),None),
             'bfefrmtrm_amount':None,'_source_id':name,'_note_evidence':candidates,
             '_formula':'공시 주석의 명시 이자비용 · 단위 환산'}
        report['fs']['list'].append(row)
    return source
