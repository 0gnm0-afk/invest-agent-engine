"""Financial facts with per-value provenance; missing never means zero."""
import math
import re
from datetime import date, timedelta
from financial_revenue import select_dart_revenue, select_sec_revenue, revenue_metadata
from financial_dart_supplement import annual_supplement_candidates, COMMON_STOCK

VERSION = 'financial-quality-common-7'

def number(value):
    if value is None or value in ('', '-', 'N/A') or isinstance(value, bool):
        return None
    try:
        v = float(str(value).replace(',', '').replace('(', '-').replace(')', ''))
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None

def fact(value=None, *, unit='', end=None, start=None, filed=None, sources=(), formula='공시 원값', reason=None, **extra):
    value = number(value)
    return dict(value=value, unit=unit, end=end, start=start, filed=filed, sources=list(sources), formula=formula,
                reason=reason or ('공시 태그/기간/단위 미확보' if value is None else None), **extra)

def calc(formula, inputs, fn, unit='', positive=()):
    cells = [x or fact() for x in inputs]
    valid = all(x['value'] is not None for x in cells) and all(cells[i]['value'] > 0 for i in positive)
    value = None
    if valid:
        try:
            value = fn(*[x['value'] for x in cells])
        except (ZeroDivisionError, ValueError, OverflowError):
            pass
    return fact(value, unit=unit, end=max((x.get('end') or '' for x in cells), default='') or None,
                start=min((x['start'] for x in cells if x.get('start')), default=None),
                filed=max((x.get('filed') or '' for x in cells), default='') or None,
                sources=sorted(set(s for x in cells for s in x.get('sources',[]))), formula=formula,
                reason=None if value is not None else '필수 입력 결측 또는 분모가 0/음수',
                missing_inputs=[dict(index=i,reason=x.get('reason'),formula=x.get('formula'),sources=x.get('sources',[]),causes=x.get('missing_inputs',[]))
                                for i,x in enumerate(cells) if x['value'] is None],
                inputs=[dict(value=x['value'],unit=x['unit'],end=x.get('end'),start=x.get('start'),formula=x.get('formula'),
                             reason=x.get('reason'),sources=x.get('sources',[]),filed=x.get('filed'),
                             **{key:x[key] for key in ('tag','receipt','accession','dividend_basis','accounting_basis','revision_basis','revision_history') if key in x},
                             **({'eps_basis':x['eps_basis']} if x.get('eps_basis') else {}),**revenue_metadata(x)) for x in cells])

def revenue_calc(formula, inputs, fn, unit):
    cells=[cell or fact() for cell in inputs]
    result=calc(formula,cells,fn,unit)
    if len({cell.get('comparison_key') for cell in cells})>1:
        result.update(value=None,reason='매출 집계 기준이 달라 기간 실적을 합산/차감하지 않음')
    result.update(revenue_metadata(cells[0]))
    return result

def statement_calc(formula, inputs, fn, unit, positive=()):
    """Combine stock/equity balances only from the same statement vintage."""
    cells=[cell or fact() for cell in inputs]
    result=calc(formula,cells,fn,unit,positive)
    signatures={(c.get('end'),c.get('filed'),c.get('accession') or c.get('receipt')) for c in cells}
    if len(signatures)!=1 or any(not c.get('end') or not c.get('filed') for c in cells):
        result.update(value=None,reason='기말 또는 공시 버전이 다른 자본·주식수 입력을 혼합하지 않음',status='incomparable')
    else:
        for key in ('accession','receipt'):
            if cells[0].get(key):result[key]=cells[0][key]
    return result

def period_difference(formula, total, previous, unit, *, revenue=False):
    """An additive quarter needs the same YTD start and comparable input basis."""
    a,b=total or fact(),previous or fact()
    result=(revenue_calc if revenue else calc)(formula,[a,b],lambda x,y:x-y,unit)
    reason=None
    if any(c.get('period_coverage')=='unknown' or c.get('accounting_basis_verified') is False for c in (a,b)):
        reason='차감 입력의 실제 기간 또는 회계 기준 미확인'
    elif a.get('unit')!=b.get('unit') or a.get('accounting_basis')!=b.get('accounting_basis') or a.get('dividend_basis')!=b.get('dividend_basis'):
        reason='차감 입력의 단위 또는 회계·배당 기준 불일치'
    elif any(any(not r.get('explained_by_split_or_rounding') for r in c.get('revision_history',[])) for c in (a,b)) and (a.get('filed'),a.get('accession'))!=(b.get('filed'),b.get('accession')):
        reason='재작성된 누적 값과 다른 공시 버전의 누적 값을 차감하지 않음'
    elif not a.get('start') or a.get('start')!=b.get('start') or not a.get('end') or not b.get('end'):
        reason='누적 실적의 시작일 또는 기간 연결 근거 미확보'
    elif not a['start']<=b['end']<a['end'] or not 70<=days(b['end'],a['end'])-1<=105:
        reason='차감 결과가 연속된 하나의 분기 기간이 아님'
    if reason:result.update(value=None,reason=reason,status='incomparable')
    result['period_disclosures']=[dict(start=c.get('start'),end=c.get('end'),
        filings=sorted(set([c.get('receipt'),c.get('accession'),*c.get('equivalent_filings',[])])-{None})) for c in (a,b)]
    if b.get('end'):result['start']=(date.fromisoformat(b['end'])+timedelta(days=1)).isoformat()
    for key in ('dividend_basis','accounting_basis'):
        if a.get(key) is not None:result[key]=a[key]
    return result

DART_MAP = {
 'sales':('IS', ['ifrs-full_Revenue','ifrs_Revenue'], ['매출액','수익(매출액)']),
 'income':('IS',['ifrs-full_ProfitLossAttributableToOwnersOfParent'],['지배기업 소유주지분','지배기업의 소유주에게 귀속되는 당기순이익']),
 'net_income':('IS',['ifrs-full_ProfitLoss'],['당기순이익','분기순이익','반기순이익']),
 'gross':('IS',['ifrs-full_GrossProfit'],['매출총이익']),
 'operating':('IS',['dart_OperatingIncomeLoss'],['영업이익','영업이익(손실)']),
 'pretax':('IS',['ifrs-full_ProfitLossBeforeTax'],['법인세비용차감전순이익']),
 'tax':('IS',['ifrs-full_IncomeTaxExpenseContinuingOperations'],['법인세비용(수익)','법인세비용']),
 'eps':('IS',['ifrs-full_DilutedEarningsLossPerShare'],['희석주당이익','희석주당이익(손실)']),
 'basic_eps':('IS',['ifrs-full_BasicEarningsLossPerShare'],['기본주당이익','기본주당이익(손실)']),
 'liabilities':('BS',['ifrs-full_Liabilities'],['부채총계']),
 'interest_expense':('IS',['ifrs-full_InterestExpense','dart_InterestExpenseFinanceExpense'],['이자비용']),
 'assets':('BS',['ifrs-full_Assets'],['자산총계']),
 'equity':('BS',['ifrs-full_EquityAttributableToOwnersOfParent'],['지배기업 소유주지분']),
 'total_equity':('BS',['ifrs-full_Equity'],['자본총계']),
 'hybrid_equity':('BS',['dart_HybridBonds'],['신종자본증권']),
 'cash':('BS',['ifrs-full_CashAndCashEquivalents'],['현금및현금성자산']),
 'current_assets':('BS',['ifrs-full_CurrentAssets'],['유동자산']),
 'current_liabilities':('BS',['ifrs-full_CurrentLiabilities'],['유동부채']),
 'inventory':('BS',['ifrs-full_Inventories'],['재고자산']),
 'receivables':('BS',['ifrs-full_CurrentTradeReceivables'],['매출채권']),
 'debt_short':('BS',['ifrs-full_ShorttermBorrowings'],['단기차입금']),
 'debt_current':('BS',['ifrs-full_CurrentPortionOfLongtermBorrowings'],['유동성장기부채','유동성장기차입금']),
 'debt_long':('BS',['ifrs-full_LongtermBorrowings'],['장기차입금']),
 'bonds':('BS',[],['사채']),
 'cfo':('CF',['ifrs-full_CashFlowsFromUsedInOperatingActivities'],['영업활동현금흐름']),
 'capex':('CF',['ifrs-full_PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities'],['유형자산의 취득']),
 'da':('CF',['ifrs-full_AdjustmentsForDepreciationDepletionAndAmortisationExpense'],['감가상각비와 무형자산상각비']),
}

def dart_period_name(value):
    match=re.search(r'제\s*(\d+)\s*기',str(value or ''))
    if not match:return None
    quarter=re.search(r'([1-4])\s*분기',str(value))
    return int(match[1]),int(quarter[1]) if quarter else 2 if '반기' in str(value) else 0

def dart_comparative_period(row, report, column, offset, quarter):
    """Comparative values need an explicit matching fiscal-term label, never position alone."""
    codes={1:'11013',2:'11012',3:'11014',4:'11011'}
    if report.get('original_quarter'):return False
    if report.get('declared_periods'):
        target=f"{report['year']-offset}-{quarter*3:02d}-"+('31' if quarter in (1,4) else '30')
        if not any(p['end']==target for p in report['declared_periods'][1:]):return False
    if str(row.get('bsns_year'))!=str(report['year']) or row.get('reprt_code')!=codes[report['quarter']]:return False
    current=dart_period_name(row.get('thstrm_nm'));previous=dart_period_name(row.get(column))
    expected=0 if quarter==4 else quarter
    return bool(current and previous and current[0]-previous[0]==offset and previous[1]==expected and
                current[1]==(0 if report['quarter']==4 else report['quarter']))

def latest_dart_fact(target, key, cell):
    from financial_cohort import retain
    old=target.get(key,{})
    candidates=[old,*old.get('disclosure_candidates',[]),cell]
    _latest_dart_fact(target,key,cell)
    target[key]=retain(target[key],candidates)


def _latest_dart_fact(target, key, cell):
    """Latest filing wins for the actual period; preserve replaced values as evidence."""
    old=target.get(key)
    rank=lambda value:(value.get('filed') or '',value.get('receipt') or '')
    if old and old.get('conflicting_values') and rank(cell)==rank(old):
        target[key]=dict(old,conflicting_values=sorted(set(old['conflicting_values']+[cell['value']])-{None}));return
    if old is None or (old['value'] is None and cell['value'] is not None):target[key]=cell;return
    if cell['value'] is None:return
    if cell['value']==old['value']:
        common=sorted(set([*old.get('equivalent_filings',[]),*cell.get('equivalent_filings',[]),old.get('receipt'),cell.get('receipt')])-{None})
        old['equivalent_filings']=common;cell['equivalent_filings']=common
    if rank(cell)<rank(old):return
    if rank(cell)==rank(old) and cell['value']!=old['value']:
        target[key]=dict(cell,value=None,status='unavailable',reason='동일 공시·회계기간 계정값 충돌',
                         conflicting_values=[old['value'],cell['value']]);return
    if cell['value']!=old['value']:
        cell=dict(cell,previous_disclosures=old.get('previous_disclosures',[])+[
            {k:old.get(k) for k in ('value','filed','receipt','sources','report_column','report_year')}],
            comparison_note='같은 회계기간의 최신 공시 비교값 채택. 과거 제출값과 다르며 재작성·정정 효과를 포함할 수 있습니다.')
    elif old.get('previous_disclosures'):
        cell=dict(cell,previous_disclosures=old['previous_disclosures'],comparison_note=old.get('comparison_note'))
    target[key]=cell

def dart_share_values(report, corp_code):
    rows=report.get('shares',{}).get('list',[]);end=report['end']
    if any(r.get('corp_code') not in (None,corp_code) for r in rows):raise ValueError('DART_SHARES_IDENTITY_MISMATCH')
    clean=lambda value:re.sub(r'\s+','',str(value or ''))
    aliases={'common':COMMON_STOCK,
             'preferred':{'우선주','우선주식','의결권없는우선주','의결권없는우선주식'}}
    values={}
    for kind,labels in aliases.items():
        selected=[r for r in rows if clean(r.get('se')) in labels and r.get('stlm_dt')==end]
        signatures={(r.get('distb_stock_co'),r.get('istc_totqy'),r.get('tesstk_co'),r.get('rcept_no')) for r in selected}
        row=selected[0] if len(signatures)==1 else {}
        receipt=row.get('rcept_no');filed=f'{receipt[:4]}-{receipt[4:6]}-{receipt[6:8]}' if receipt else None
        fields=[('shares','distb_stock_co'),('issued_shares','istc_totqy'),('treasury_shares','tesstk_co')] if kind=='common' else [('preferred_shares','distb_stock_co')]
        for key,field in fields:
            values[key]=fact(row.get(field),unit='shares',end=end,filed=filed,sources=[report.get('shares_source_id',report['prefix']+'-shares.json')],
                tag=field,receipt=receipt,account=row.get('se'),formula='공시 보통주 유통주식수' if key=='shares' else '주식종류별 공시 원값',
                reason=None if row else '해당 기말의 명시 보통주/우선주 종류별 수량 미확보',status=None if row else 'unavailable',stock_class_present=bool(selected))
        if kind=='common' and all(values[k]['value'] is not None for k in ('shares','issued_shares','treasury_shares')):
            if values['shares']['value']!=values['issued_shares']['value']-values['treasury_shares']['value']:
                values['shares'].update(value=None,status='unavailable',reason='공시 유통주식수와 발행주식수−자기주식 불일치')
    common,preferred=values['shares'],values['preferred_shares']
    totals=[r for r in rows if clean(r.get('se'))=='합계' and r.get('stlm_dt')==end]
    signatures={(r.get('distb_stock_co'),r.get('rcept_no')) for r in totals}
    total=totals[0] if len(signatures)==1 else {}
    # A dash stays unknown unless the same filing's total proves no non-common shares remain.
    if (preferred.get('stock_class_present') and preferred['value'] is None and
            common['value'] is not None and common['value']>=0 and common.get('receipt') and
            common['receipt']==preferred.get('receipt')==total.get('rcept_no') and
            number(total.get('distb_stock_co'))==common['value']):
        total_fact=fact(total['distb_stock_co'],unit='shares',end=end,filed=common.get('filed'),
            sources=common['sources'],formula='공시 합계 유통주식수')
        derived=calc('동일 공시·동일 기말 합계 유통주식수 − 보통주 유통주식수',
            [total_fact,common],lambda a,b:a-b,'shares')
        values['preferred_shares']=dict(preferred,**derived,status='derived',
            reported_value=next(r.get('distb_stock_co') for r in rows
                if clean(r.get('se')) in aliases['preferred'] and r.get('stlm_dt')==end),
            comparison_note='우선주 원공시 수량은 미기재. 같은 공시의 합계와 보통주 유통주식수가 같아 잔여 수량 0을 확인했습니다.')
    return values

def dart_normalize(source):
    company=source['identity']; annual_cells={};quarter_cells={};cumulative={};share_history=[];warnings=[]
    cutoff=date.today().year-6; explicit_quarters=set();bank=False
    def put(store,period,key,cell):latest_dart_fact(store.setdefault(period,{}),key,cell)
    def select(rows,key):
        sj,ids,names=DART_MAP[key]
        candidates=[r for r in rows if r.get('sj_div') in (('IS','CIS') if sj=='IS' else (sj,)) and r.get('currency')=='KRW']
        if key in ('eps','basic_eps'):
            # Some issuers use a standard total EPS tag for a continuing-operations
            # line. The account's explicit scope takes precedence over that tag.
            candidates=[r for r in candidates if not any(scope in re.sub(r'\s+','',r.get('account_nm',''))
                                                        for scope in ('계속영업','중단영업','우선주'))]
            combined=[r for r in candidates if re.fullmatch(
                r'(보통주)?기본(?:및희석|\(희석\))주당(?:순)?(이익|손실|이익\(손실\))',re.sub(r'\s+','',r.get('account_nm','')))]
            if combined:return combined
        selected=[]
        for field,aliases in (('account_id',ids),('account_nm',names)):
            for alias in aliases:
                selected=[r for r in candidates if r.get(field)==alias]
                if selected:break
            if selected:break
        if key=='cash' and not any(number(r.get('thstrm_amount')) is not None for r in selected):
            candidates=[r for r in rows if r.get('sj_div')=='CF' and r.get('currency')=='KRW']
            selected=[r for r in candidates if r.get('account_id') in ('ifrs-full_CashAndCashEquivalentsAtEndOfPeriod','dart_CashAndCashEquivalentsAtEndOfPeriod','dart_CashAndCashEquivalentsAtEndOfPeriodCf')]
            if not selected:
                selected=[r for r in candidates if re.fullmatch(r'(기말|반기말|분기말|연도말|보고기간말)(의)?현금및현금성자산',re.sub(r'\s+','',r.get('account_nm','')))]
        return selected
    for report in sorted(source['reports'],key=lambda r:(r['year'],r['quarter'])):
        if report.get('period_mapping_error'):
            warnings.append(report['prefix']+': '+report['period_mapping_error']);continue
        y,q,end=report['year'],report['quarter'],report['end'];prefix=report['prefix']
        shares=dart_share_values(report,company['corp_code'])
        share_history.append(dict(date=end,values=shares))
        if report['fs'].get('status')!='000':continue
        explicit_quarters.add((y,q));rows=report['fs']['list']
        if any(r.get('corp_code')!=company['corp_code'] for r in rows):raise ValueError('DART_IDENTITY_MISMATCH')
        revenue=select_dart_revenue(rows)
        bank=bank or revenue.get('basis')=='bank_net_revenue' or any(r.get('account_id') in ('ifrs-full_InterestRevenueExpense','ifrs_InterestRevenueExpense') and r.get('sj_div') in ('IS','CIS') for r in rows)
        for key,(sj,_,_) in DART_MAP.items():
            selected=([revenue['row']] if revenue.get('row') else []) if key=='sales' else select(rows,key)
            row=selected[0] if selected else {};metadata=revenue_metadata(revenue) if key=='sales' else {}
            instant=sj=='BS';unit='KRW/share' if 'eps' in key else 'KRW'
            def cell(column,target_y,target_q,*,cumulative_value=False):
                target_end=f'{target_y}-{target_q*3:02d}-'+('31' if target_q in (1,4) else '30')
                receipt=row.get('rcept_no');filed=f'{receipt[:4]}-{receipt[4:6]}-{receipt[6:8]}' if receipt else None
                vals={str(r.get(column)) for r in selected}
                amount=row.get(column) if len(vals)==1 else None
                start=None if instant else f'{target_y}-01-01' if target_q==4 or sj=='CF' or cumulative_value else f'{target_y}-{(target_q-1)*3+1:02d}-01'
                period_meta={}
                if not instant:
                    period_kind='annual' if target_q==4 else 'cumulative' if sj=='CF' or cumulative_value else 'quarterly'
                    periods=[p for p in source.get('report_periods',[]) if p.get('year')==target_y and p.get('quarter')==target_q
                             and p.get('end')==target_end and p.get('source_id') and p.get('start')
                             and p.get('period_kind','annual' if target_q==4 else None)==period_kind]
                    if periods:
                        starts={p['start'] for p in periods}
                        start=next(iter(starts)) if len(starts)==1 else None
                        period_meta=dict(period_coverage='verified' if start else 'unknown',period_sources=[p['source_id'] for p in periods])
                    else:
                        period_label=row.get(column.replace('_amount','_nm').replace('_add_nm','_nm'))
                        term=dart_period_name(period_label)
                        if target_q==4 and term and term[0]==1:
                            # Incorporation does not prove the business' reporting
                            # start (predecessor operations may exist). Do not invent it.
                            start=None
                            period_meta=dict(period_coverage='unknown',period_note='제1기 실적의 실제 시작일 미확보; 12개월 연간으로 비교하지 않음')
                return fact(amount,unit=unit,end=target_end,start=start,filed=filed,sources=[row.get('_source_id',prefix+'-statements.json')],
                    tag=row.get('account_id'),account=row.get('account_nm'),receipt=receipt,report_year=y,report_column=column,
                    period_name=row.get(column.replace('_amount','_nm').replace('_add_nm','_nm')),
                    comparative=target_y!=y,formula=row.get('_formula','공시 비교기간 원값' if target_y!=y else '공시 원값'),
                    accounting_basis=source.get('statement_scope','CFS'),note_evidence=row.get('_note_evidence'),**metadata,**period_meta)
            current=cell('thstrm_amount',y,q)
            put(annual_cells if q==4 else quarter_cells,y if q==4 else (y,q),key,current)
            if not instant:
                put(cumulative,(y,q),key,cell('thstrm_add_amount' if sj=='IS' and report.get('original_quarter',q) in (2,3) else 'thstrm_amount',y,q,cumulative_value=True))
            if key=='sales' and row.get('account_id')=='derived_BankNetRevenue':
                # This helper sums only current/current-YTD components; inherited comparison fields are not totals.
                continue
            # Annual comparisons are annual amounts, never the current report year's values.
            if q==4:
                for offset,column in ((1,'frmtrm'),(2,'bfefrmtrm')):
                    if y-offset>=cutoff and dart_comparative_period(row,report,column+'_nm',offset,4):
                        put(annual_cells,y-offset,key,cell(column+'_amount',y-offset,4))
                        if not instant:put(cumulative,(y-offset,4),key,cell(column+'_amount',y-offset,4,cumulative_value=True))
            elif y-1>=cutoff:
                if instant and row.get('sj_div')=='BS' and dart_comparative_period(row,report,'frmtrm_nm',1,4):
                    put(annual_cells,y-1,key,cell('frmtrm_amount',y-1,4))
                elif dart_comparative_period(row,report,'frmtrm_q_nm',1,q):
                    put(quarter_cells,(y-1,q),key,cell('frmtrm_q_amount',y-1,q))
                    if not instant:
                        put(cumulative,(y-1,q),key,cell('frmtrm_add_amount' if sj=='IS' and q in (2,3) else 'frmtrm_q_amount',y-1,q,cumulative_value=True))
    for candidate in annual_supplement_candidates(source):
        if candidate['fy']<cutoff:continue
        key=candidate['key'];cell=dict(candidate);cell.pop('key');fy=cell.pop('fy')
        cell=fact(**cell)
        put(annual_cells,fy,key,cell)
    warnings.extend(source.get('annual_supplement_errors',[]))
    warnings.extend(source.get('period_supplement_errors',[]))
    shares_by_end={row['date']:row['values'] for row in share_history}
    core={'sales','income','operating','net_income','eps','basic_eps','reported_eps'}
    annual_cells={y:v for y,v in annual_cells.items() if any(v.get(k,{}).get('value') is not None for k in core)}
    for (y,q),values in list(quarter_cells.items()):
        for key,(sj,_,_) in DART_MAP.items():
            if (sj=='CF' or key=='interest_expense' and values.get(key,{}).get('value') is None) and q>1:
                values[key]=period_difference('당기 누적 현금흐름 − 직전 분기 누적',cumulative.get((y,q),{}).get(key),cumulative.get((y,q-1),{}).get(key),'KRW')
                values[key]['start']=f'{y}-{(q-1)*3+1:02d}-01'
    for y,annual_values in annual_cells.items():
        if (y,4) not in explicit_quarters and not any(cumulative.get((y,3),{}).get(k,{}).get('value') is not None and annual_values.get(k,{}).get('value') is not None for k in ('sales','income','operating')):continue
        values={k:dict(v) for k,v in annual_values.items() if k in DART_MAP and DART_MAP[k][0]=='BS'}
        for key,(sj,_,_) in DART_MAP.items():
            if sj=='BS':continue
            if 'eps' in key:
                values[key]=fact(unit='KRW/share',end=f'{y}-12-31',start=f'{y}-10-01',reason='4분기 EPS 별도 공시 미확보: 연간 EPS−9개월 EPS는 가중주식수 차이로 계산하지 않음')
            else:
                values[key]=period_difference('연간 실적 − 9개월 누적 실적',annual_values.get(key),cumulative.get((y,3),{}).get(key),'KRW',revenue=key=='sales')
                values[key]['start']=f'{y}-10-01'
        quarter_cells[(y,4)]=values
    def finish(values,end):
        values=dict(values);values.update(shares_by_end.get(end,{}))
        values['debt']=calc('단기차입금 + 유동성장기부채 + 장기차입금 + 사채 (리스 별도)',[values.get(k) for k in ('debt_short','debt_current','debt_long','bonds')],lambda *a:sum(a),'KRW')
        return values
    annual=[dict(date=f'{y}-12-31',label=f'FY {y}',fy=y,fq=0,values=finish(v,f'{y}-12-31')) for y,v in sorted(annual_cells.items())]
    quarterly=[dict(date=(end:=f'{y}-{q*3:02d}-'+('31' if q in (1,4) else '30')),label=f'{y} Q{q}',fy=y,fq=q,values=finish(v,end)) for (y,q),v in sorted(quarter_cells.items())]
    return dict(provider='DART',name=company.get('corp_name'),standard='K-IFRS · '+('별도' if source.get('statement_scope')=='OFS' else '연결'),currency='KRW',annual=annual,quarterly=quarterly,
                share_history=share_history,industry_model='bank' if bank else 'general',
                identity=dict(corp_code=company['corp_code'],symbol=company['stock_code']),warnings=warnings,
                limitations=['명시된 회계기간의 최신 공시 당기·비교값을 채택합니다. 과거 제출값과 재작성 차이가 있을 수 있습니다.',
                            '4분기 EPS는 별도 확정값 미확보 시 결측. 연간·누적 EPS 차감 금지.',
                            '보통주 유통주식수는 최신 확인 공시일을 따로 표시하며 EPS의 기간 가중평균주식수와 다릅니다.',
                            '배당 공시의 연결 EPS는 기본·희석 구분이 없어 별도 공시 EPS 계열로 보존합니다.'])
SEC_MAP = {
 'sales':['RevenueFromContractWithCustomerExcludingAssessedTax','Revenues','SalesRevenueNet','RevenueFromContractWithCustomerIncludingAssessedTax'],
 'income':['NetIncomeLoss'], 'common_income':['NetIncomeLossAvailableToCommonStockholdersBasic'],
 'net_income':['ProfitLoss'], 'gross':['GrossProfit'], 'operating':['OperatingIncomeLoss'],
 'pretax':['IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest'],
 'tax':['IncomeTaxExpenseBenefit'], 'eps':['EarningsPerShareDiluted'], 'basic_eps':['EarningsPerShareBasic'],
 'liabilities':['Liabilities'], 'interest_expense':['InterestExpense'], 'interest_nonoperating':['InterestExpenseNonoperating'],
 'assets':['Assets'], 'equity':['StockholdersEquity'], 'total_equity':['StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest','StockholdersEquity'],
 'cash':['CashAndCashEquivalentsAtCarryingValue'], 'current_assets':['AssetsCurrent'],
 'current_liabilities':['LiabilitiesCurrent'], 'inventory':['InventoryNet'], 'receivables':['AccountsReceivableNetCurrent'],
 'debt_short':['ShortTermBorrowings'], 'debt_current':['LongTermDebtCurrent'],
 'debt_long':['LongTermDebtNoncurrent'], 'debt_current_total':['DebtCurrent'],
 'cfo':['NetCashProvidedByUsedInOperatingActivities'], 'capex':['PaymentsToAcquirePropertyPlantAndEquipment','PaymentsToAcquireProductiveAssets'],
 'da':['DepreciationDepletionAndAmortization','DepreciationDepletionAndAmortizationPropertyPlantAndEquipment'],
 'shares':['CommonStockSharesOutstanding'], 'weighted_shares':['WeightedAverageNumberOfDilutedSharesOutstanding'],
 'basic_weighted_shares':['WeightedAverageNumberOfSharesOutstandingBasic'],
 'issued_shares':['CommonStockSharesIssued'], 'treasury_shares':['TreasuryStockCommonShares'],
 'preferred_shares':['PreferredStockSharesOutstanding'], 'preferred_issued_shares':['PreferredStockSharesIssued'],
 'preferred_treasury_shares':['TreasuryStockPreferredShares'],
 'preferred_equity':['PreferredStockIncludingAdditionalPaidInCapitalNetOfDiscount'],
 'common_equity':['CommonStockholdersEquity'],
 'cash_due':['CashAndDueFromBanks'], 'cash_including_restricted':['CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents'],
 'cash_dividends':['PaymentsOfDividendsCommonStock'],
 'buybacks':['PaymentsForRepurchaseOfCommonStock','StockRepurchasedDuringPeriodValue','StockRepurchasedAndRetiredDuringPeriodValue'],
 'dividend_declared':['CommonStockDividendsPerShareDeclared'],
 'dividend_paid':['CommonStockDividendsPerShareCashPaid']}
INSTANT = {'liabilities','assets','equity','total_equity','cash','current_assets','current_liabilities','inventory','receivables','debt_short','debt_current','debt_long','debt_current_total','shares',
           'issued_shares','treasury_shares','preferred_shares','preferred_issued_shares','preferred_treasury_shares','preferred_equity','common_equity','cash_due','cash_including_restricted'}

def days(start,end):
    return (date.fromisoformat(end)-date.fromisoformat(start)).days+1

def per_share(key):
    return key in ('eps','basic_eps','dividend','dividend_paid','dividend_declared')

def sec_normalize(source):
    data=source['facts']['facts']; identity=source['identity']
    from financial_returns import reporting_currency
    currency=reporting_currency(source)
    if source['facts'].get('cik') is not None and str(source['facts']['cik']).lstrip('0')!=str(identity['cik']).lstrip('0'):
        raise ValueError('SEC_FACTS_IDENTITY_MISMATCH')
    namespace='us-gaap' if 'us-gaap' in data else 'ifrs-full' if 'ifrs-full' in data else None
    if namespace is None:raise ValueError('SEC_IFRS_TAXONOMY_REQUIRES_MAPPING')
    from financial_sec_ifrs import IFRS_MAP,revenue_rows
    mapping=SEC_MAP if namespace=='us-gaap' else IFRS_MAP
    if source.get('inline_facts'):
        # A normalized source remains immutable; supplement rows are explicit
        # inputs, never implicit filesystem or network reads during calculation.
        import copy
        data=copy.deepcopy(data)
        for row in source['inline_facts']:
            concept=data[namespace].setdefault(row['tag'],{})
            concept.setdefault('units',{}).setdefault(row['unit'],[]).append(row)
    annual={}; quarter={}; selected={}; histories={}; cutoff=str(date.today().year-6)+'-01-01'
    for key,tags in mapping.items():
        unit=currency+'/shares' if per_share(key) else 'shares' if 'shares' in key else currency
        pool={};history=[]
        for priority,tag in enumerate(tags):
            units=data[namespace].get(tag,{}).get('units',{})
            selected_unit=unit
            if key.startswith('dividend') and unit not in units:
                alternatives=[u for u in units if re.fullmatch('[A-Z]{3}/shares',u)]
                if len(alternatives)==1:selected_unit=alternatives[0]
            for original in units.get(selected_unit,[]):
                r=dict(original,reported_unit=selected_unit)
                if r.get('form') not in ('10-K','10-Q','10-K/A','10-Q/A','S-1','S-1/A','424B4','20-F','20-F/A','40-F','40-F/A','6-K','6-K/A','8-K') or r.get('end','') < cutoff or r.get('filed','')>date.today().isoformat():
                    continue
                if key in INSTANT and r.get('start'): continue
                if key not in INSTANT and not r.get('start'): continue
                history.append(dict(r,tag=tag))
                span=(r.get('start'),r['end'])
                rank=(r.get('filed',''),r.get('accn',''),-priority)
                if span not in pool or rank>pool[span][0]:
                    pool[span]=(rank,dict(r,tag=tag))
                elif rank==pool[span][0] and number(r.get('val'))!=number(pool[span][1].get('val')):
                    pool[span]=(rank,dict(pool[span][1],val=None,reason='동일 최신 공시의 계정값 충돌',conflict=True))
        selected[key]=[x[1] for x in pool.values()]
        histories[key]=history
    selected['sales']=(select_sec_revenue(data[namespace],cutoff=cutoff,as_of=date.today().isoformat(),unit=currency) if namespace=='us-gaap'
                       else revenue_rows(data[namespace],cutoff,date.today().isoformat(),currency))
    for r in source.get('inline_shares', []):
        if not r.get('end') or not r.get('filed') or r['end']<cutoff or r['filed']>date.today().isoformat():continue
        old = next((x for x in selected['shares'] if x['end'] == r['end']), None)
        rank=lambda x:(x.get('filed',''),x.get('accn',''))
        if old is None or rank(r)>=rank(old):
            candidate=dict(r)
            if old and rank(r)==rank(old) and (old.get('conflict') or number(old.get('val'))!=number(r.get('val'))):
                candidate.update(val=None,conflict=True,reason='동일 기말·최신 공시 원천 간 보통주 수량 충돌')
            selected['shares'] = [x for x in selected['shares'] if x['end'] != r['end']] + [candidate]
    # Splits verified separately against official IR; no price-derived split guesses.
    split_events={}
    for event in source.get('splits',[]):
        when=event.get('date');ratio=number(event.get('ratio'))
        if not when:continue
        normalized=dict(event,ratio=ratio if ratio is not None and ratio>0 else None)
        if when in split_events and split_events[when]['ratio']!=normalized['ratio']:
            split_events[when]=dict(normalized,ratio=None,reason='동일 효력일 공식 분할비율 충돌')
        else:split_events.setdefault(when,normalized)
    splits=[split_events[k] for k in sorted(split_events)]
    def to_fact(r,key):
        unit=(r.get('reported_unit',currency+'/shares').replace('/shares','/share') if per_share(key) else 'shares' if 'shares' in key else currency)
        value=number(r.get('val')); applied=[];reason=r.get('reason')
        if value is not None and (per_share(key) or 'shares' in key):
            for event in splits:
                # Adopt latest filed restatements, then apply only splits effective after that filing.
                basis=r['filed']
                if basis<event['date']<=date.today().isoformat():
                    if event.get('ratio') is None:
                        value=None;reason='공식 주식분할 비율 충돌/미확인으로 주당·주식수 기준을 연결할 수 없음';break
                    value=value*event['ratio'] if 'shares' in key else value/event['ratio']
                    applied.append(event)
        metadata={}
        if key in ('dividend','dividend_paid','dividend_declared'):metadata['dividend_basis']='paid' if key=='dividend_paid' or r['tag']=='CommonStockDividendsPerShareCashPaid' else 'declared'
        if key=='buybacks':metadata['repurchase_basis']='cash_paid' if r['tag']=='PaymentsForRepurchaseOfCommonStock' else 'reported_repurchase_cost'
        if key in ('eps','basic_eps'):metadata['eps_basis']='basic' if key=='basic_eps' else 'diluted'
        for field in ('revision_history','revision_basis','evidence','custom_mapping','confirmed_zero'):
            if field in r:metadata[field]=r[field]
        if namespace=='ifrs-full':metadata.update(accounting_basis='IFRS',reported_tag='ifrs-full:'+r['tag'],per_share_basis='ordinary_share' if per_share(key) else None)
        metadata['equivalent_filings']=sorted({x.get('accn') for x in histories.get(key,[]) if
            x.get('start')==r.get('start') and x['end']==r['end'] and number(x.get('val'))==number(r.get('val')) and x.get('accn')})
        return fact(value,unit=unit,end=r['end'],start=r.get('start'),filed=r['filed'],sources=sorted(set([r.get('source_id','companyfacts.json'),*r.get('extra_sources',[])]+[e['source_id'] for e in applied if e.get('source_id')])),
                    tag=r['tag'],accession=r.get('accn'),form=r.get('form'),reported_value=r['val'],
                    formula=r.get('formula','공시 원값')+(' · 공식 주식분할 조정' if applied else ''),reason=reason,splits=applied,**metadata,**revenue_metadata(r))
    # Preserve actual restatements and connect annual periods only through
    # filings that reported their currently selected values together. A changed
    # middle year cannot silently bridge an unrevised five-year base to the
    # post-restatement series. This is value lineage, not an inferred accounting
    # standard or a company-name exception.
    for key in ('sales','income','net_income','operating','eps','basic_eps'):
        annual_rows={(r['start'],r['end']):r for r in selected.get(key,[]) if 330<=days(r['start'],r['end'])<=380}
        if not annual_rows:continue
        def tolerance(row):
            value=number(row.get('val'))
            if value is None:return 0
            # Honor supplied XBRL decimals, never a guessed money tolerance.
            decimals=row.get('decimals')
            if isinstance(decimals,(str,int)) and re.fullmatch(r'-?\d+',str(decimals)) and -12<=int(decimals)<=12:
                step=10**-int(decimals)
            elif str(decimals).upper()=='INF':step=0
            elif key in ('eps','basic_eps'):
                digits=len(str(value).partition('.')[2].rstrip('0'))
                step=10**-max(2,digits)
            else:return 0
            for event in splits:
                if row['filed']<event['date']<=date.today().isoformat() and event.get('ratio'):
                    step/=event['ratio']
            return step/2
        parents={span:span for span in annual_rows};filings={};unexplained=False
        def root(span):
            while parents[span]!=span:
                parents[span]=parents[parents[span]];span=parents[span]
            return span
        selected_cells={span:to_fact(r,key) for span,r in annual_rows.items()}
        seen_history={span:set() for span in annual_rows}
        for old in histories[key]:
            span=(old.get('start'),old['end']);current=annual_rows.get(span)
            if not current or old['tag']!=current['tag']:continue
            a,b=to_fact(old,key),selected_cells[span]
            if a['value'] is None or b['value'] is None:continue
            equal=abs(a['value']-b['value'])<=tolerance(old)+tolerance(current)+1e-12
            if equal and old.get('accn'):
                filings.setdefault((old['filed'],old['accn']),set()).add(span)
            if (old['filed'],old.get('accn',''))<(current['filed'],current.get('accn','')) and a['value']!=b['value']:
                signature=(old['filed'],old.get('accn'),old.get('val'))
                if signature not in seen_history[span]:
                    current.setdefault('revision_history',[]).append(dict(value=old['val'],value_on_current_split_basis=a['value'],
                        filed=old['filed'],accession=old.get('accn'),tag=old['tag'],explained_by_split_or_rounding=equal))
                    seen_history[span].add(signature)
                unexplained=unexplained or not equal
        if unexplained:
            for spans in filings.values():
                ordered=sorted(spans)
                for other in ordered[1:]:parents[root(other)]=root(ordered[0])
            for span,row in annual_rows.items():row['revision_basis']='same-filing-value-lineage:'+root(span)[1]
    period_facts={'annual':{},'quarterly':{}}; warnings=[]
    def remember(frame,key,cell,direct):
        period_facts[frame].setdefault(cell['end'],{}).setdefault(key,[]).append((cell,direct))
    for key,rows in selected.items():
        if key in INSTANT: continue
        for r in rows:
            duration=days(r['start'],r['end'])
            frame='annual' if 330<=duration<=380 else 'quarterly' if 70<=duration<=105 else None
            if frame:
                remember(frame,key,to_fact(r,key),True)
        # Additive cash-flow and income fields only. EPS and weighted shares never subtract.
        if key not in ('eps','basic_eps','weighted_shares','basic_weighted_shares'):
            for r in rows:
                span=days(r['start'],r['end'])
                if not 150<=span<=380: continue
                prev=[p for p in rows if p['start']==r['start'] and 70<=days(p['end'],r['end'])-1<=105]
                if not prev: continue
                p=max(prev,key=lambda p:p['end'])
                cell=period_difference('동일 회계연도 누적 실적 − 직전 누적 실적',to_fact(r,key),to_fact(p,key),to_fact(r,key)['unit'],revenue=key=='sales')
                cell['start']=(date.fromisoformat(p['end'])+timedelta(days=1)).isoformat()
                remember('quarterly',key,cell,False)
    def materialize(frame):
        periods=period_facts[frame]; target={}
        core=('sales','operating','income','net_income','eps','basic_eps')
        revenue_ends=[end for end,fields in periods.items() if fields.get('sales')]
        def rank(candidate):
            cell,direct=candidate
            # A directly reported quarter takes precedence over a cumulative difference.
            return (direct,cell.get('filed') or '',cell.get('accession') or '')
        for end,fields in sorted(periods.items()):
            anchor_key=next((key for key in core if fields.get(key)),None)
            if not anchor_key:
                continue
            if anchor_key!='sales':
                nearby=[other for other in revenue_ends if abs((date.fromisoformat(end)-date.fromisoformat(other)).days)<=20]
                if nearby:
                    warnings.append(f'SEC {frame} {end}: 가까운 매출 공시기간 {min(nearby)}과 겹치는 별도 손익기간 제외')
                    continue
            candidates=fields[anchor_key]; latest_rank=max(map(rank,candidates))
            starts={cell.get('start') for cell,direct in candidates if rank((cell,direct))==latest_rank}
            start=next(iter(starts)) if len(starts)==1 else None
            if start is None:
                warnings.append(f'SEC {frame} {end}: 동일 최신 공시의 시작일 충돌로 기간 실적 결측')
            values={}
            for key,options in fields.items():
                matching=[candidate for candidate in options if start and candidate[0].get('start')==start]
                if matching:
                    values[key]=max(matching,key=rank)[0]
                    from financial_cohort import retain
                    history=[to_fact(r,key) for r in histories.get(key,[]) if r.get('start')==start and r['end']==end
                             and r.get('tag')==values[key].get('tag')]
                    values[key]=retain(values[key],history)
                else:
                    exemplar=max(options,key=rank)[0]
                    values[key]=fact(unit=exemplar['unit'],start=start,end=end,
                        filed=exemplar.get('filed'),sources=exemplar.get('sources',[]),
                        reason='선택된 재무기간과 계정 시작일 불일치' if start else '동일 최신 공시의 재무기간 시작일 충돌',
                        period_start_candidates=sorted({cell.get('start') for cell,_ in options if cell.get('start')}),
                        **revenue_metadata(exemplar))
            target[end]=values
        return target
    annual=materialize('annual');quarter=materialize('quarterly')
    for ir in source.get('ir_eps', []):
        key='basic_eps' if ir.get('eps_basis')=='basic' else 'eps'
        cell = to_fact(dict(ir, form='company IR', accn=ir.get('accn')), key)
        end = ir['end']
        starts={value.get('start') for value in quarter.get(end,{}).values() if value.get('start')}
        if len(starts)!=1:
            continue
        start=next(iter(starts))
        if ir.get('start') and ir['start']!=start:
            continue
        cell['start'] = start
        old=quarter.get(end,{}).get(key)
        if end in quarter and (old is None or (old.get('filed') or '') <= ir['filed']):
            if old and old.get('filed')==ir['filed'] and (old.get('source_conflict') or old.get('value') is not None and old['value']!=cell['value']):
                cell.update(value=None,reason='동일 제출일 원천 간 직접 공시 EPS 충돌',source_conflict=True)
            quarter[end][key] = cell
    for dividend in source.get('annual_dividends',[]):
        values=annual.get(dividend.get('end'))
        if not values:continue
        starts={c.get('start') for c in values.values() if c.get('start')}
        if len(starts)!=1:continue
        cell=fact(dividend.get('val'),unit=currency+'/share',start=next(iter(starts)),end=dividend['end'],
                  filed=dividend['filed'],sources=[dividend['source_id']],formula='공식 연간보고서의 명시 과거 배당 공시',
                  dividend_basis=dividend.get('dividend_basis'),evidence=dividend.get('evidence'))
        if values.get('dividend',{}).get('value') is None:
            values['dividend']=cell
    for target in (annual,quarter):
        for end,values in target.items():
            for key in INSTANT:
                rs=[r for r in selected.get(key,[]) if r['end']==end]
                values[key]=to_fact(max(rs,key=lambda r:r['filed']),key) if rs else fact(unit='shares' if 'shares' in key else currency,end=end)
                from financial_cohort import retain
                values[key]=retain(values[key],[to_fact(r,key) for r in histories.get(key,[]) if r['end']==end and not r.get('start')
                    and r.get('tag')==values[key].get('tag')])
            for outstanding,issued,treasury in (('shares','issued_shares','treasury_shares'),('preferred_shares','preferred_issued_shares','preferred_treasury_shares')):
                a,b=values[issued],values[treasury]
                if values[outstanding]['value'] is None and all(c['value'] is not None for c in (a,b)) and a.get('accession') and a.get('accession')==b.get('accession') and a['filed']==b['filed']:
                    derived=calc('동일 공시·기말 발행주식수 − 자기주식수',[a,b],lambda x,y:x-y,'shares')
                    if derived['value'] is not None and derived['value']<0:
                        derived.update(value=None,reason='자기주식수가 발행주식수보다 큼')
                    values[outstanding]=derived
            for key in SEC_MAP.keys()-INSTANT:
                missing_reason=('연결 전체 순이익 태그 미확보; 지배주주 순이익으로 대체하지 않음' if key=='net_income'
                                else '해당 기간 직접 공시 EPS 미확보; 누적 EPS 차감 금지' if key in ('eps','basic_eps')
                                else '해당 기간·계정의 공식 원값 미확보')
                unit=currency+'/share' if per_share(key) else 'shares' if 'shares' in key else currency
                values.setdefault(key,fact(unit=unit,end=end,reason=missing_reason,field=key))
            if values.get('debt_current_total',{}).get('value') is not None:
                values['debt']=calc('공시 유동부채 중 이자부부채 + 비유동 장기부채',[values.get('debt_current_total'),values.get('debt_long')],lambda a,b:a+b,currency)
            else:
                values['debt']=calc('단기차입금 + 유동성장기부채 + 비유동 장기부채',[values.get(k) for k in ('debt_short','debt_current','debt_long')],lambda *a:sum(a),currency)
    # Keep both dividend series. Select one for each frame by recent coverage,
    # never assemble a TTM from whichever tag won in each individual quarter.
    for target,count in ((annual,3),(quarter,4)):
        recent=[target[end] for end in sorted(target)[-count:]]
        counts={basis:sum(v.get('dividend_'+basis,{}).get('value') is not None for v in recent) for basis in ('declared','paid')}
        basis='paid' if counts['paid']>counts['declared'] else 'declared'
        for values in target.values():
            selected_dividend=values.get('dividend_'+basis)
            if selected_dividend and selected_dividend['value'] is not None:
                values['dividend']=dict(selected_dividend)
            else:
                values.setdefault('dividend',fact(unit=currency+'/share',dividend_basis=basis,reason='선택한 동일 계열의 주당배당 공시 미확보'))
    from financial_returns import apply_explicit_zeros
    apply_explicit_zeros(source,annual,quarter,currency)
    def rows(target,kind):
        return [dict(date=e,label=('연간 ' if kind=='annual' else '분기 ')+e,fy=int(e[:4]),fq=None,values=v) for e,v in sorted(target.items())]
    sic=str(identity.get('sic',''))
    industry='bank' if 'RevenuesNetOfInterestExpense' in data[namespace] or sic in ('6021','6022','6029','6035','6036','6712') else 'insurance' if sic in ('6311','6321','6324','6331','6351','6361','6399') else 'general'
    return dict(provider='SEC',name=identity['name'],identity=dict(cik=str(identity['cik']),symbol=source.get('symbol')),currency=currency,standard=('US GAAP' if namespace=='us-gaap' else 'IFRS')+' · 연결',security_basis=source.get('security_basis'),
                industry_model=industry,
                annual=rows(annual,'annual'),quarterly=rows(quarter,'quarterly'),warnings=warnings+source.get('official_supplement_errors',[]),
                guidance=source.get('guidance',[]),limitations=['회계기간 종료일 기준입니다. 기업의 회계연도는 달력연도와 다를 수 있습니다.',
                             'EPS는 연간/분기별로 기본·희석 중 하나의 직접 공시 계열을 사용. 4분기 직접 태그 미확보 시 결측.',
                             'SEC 동일 기간은 최신 제출 비교값 우선. 과거 시점 백테스트용 데이터가 아닙니다.',
                             '기말 유통주식수와 기간 가중평균 희석주식수는 구분합니다. 백만 주 단위 공시의 원자료 반올림 정밀도를 보존합니다.'],splits=splits)

def trailing(rows,key,index=None):
    end_index=len(rows) if index is None else index
    part=rows[max(0,end_index-4):end_index]
    unit=next((r['values'][key]['unit'] for r in part if key in r['values']), '')
    if len(part)!=4 or any(not 65<=days(a['date'],b['date'])-1<=110 for a,b in zip(part,part[1:])):
        return fact(unit=unit,reason='연속 4개 분기 미확보')
    cells=[r['values'].get(key) or fact() for r in part]
    for field in ('unit','eps_basis','comparison_key','dividend_basis','accounting_basis'):
        bases={cell.get(field) for cell in cells}
        if len(bases)>1:
            return fact(unit=unit,reason='TTM 구성 분기의 단위·EPS·매출·배당·회계 기준 불일치')
    if any(c.get('start') and c.get('end') and not 70<=days(c['start'],c['end'])<=105 for c in cells):
        return fact(unit=unit,reason='TTM 구성에 분기가 아닌 누적/연간 실적이 포함됨')
    if any(b.get('start') and a.get('end') and days(a['end'],b['start'])!=2 for a,b in zip(cells,cells[1:])):
        return fact(unit=unit,reason='TTM 구성 분기의 실제 기간이 겹치거나 비어 있음')
    if any(cell.get('period_coverage')=='unknown' or cell.get('accounting_basis_verified') is False for cell in cells):
        return fact(unit=unit,reason='TTM 구성 분기의 기간/회계 기준 미확인')
    result=calc('연속 최근 4개 분기 합계 (TTM)',cells,lambda *v:sum(v),unit)
    for field in ('eps_basis','comparison_key','basis','account_label','revenue_note','revenue_components','dividend_basis','accounting_basis'):
        if cells[-1].get(field) is not None:
            result[field]=cells[-1][field]
    if key in ('eps','basic_eps','display_eps'):
        result.update(status='derived',eps_aggregation='quarter_sum',
                      comparison_note='직접 공시한 분기 EPS의 합계입니다. 분기마다 가중평균주식수가 달라 공식 연간/TTM EPS와 일치하지 않을 수 있습니다.')
    return result

def comparable_period(rows, current, years=1, quarter=False):
    """Match actual period ends, never a shifted array position after a missing filing."""
    expected=91 if quarter else years*365.25
    tolerance=25 if quarter else 20
    matches=[r for r in rows if r['date']<current['date'] and
             abs((date.fromisoformat(current['date'])-date.fromisoformat(r['date'])).days-expected)<=tolerance]
    return min(matches,key=lambda r:abs((date.fromisoformat(current['date'])-date.fromisoformat(r['date'])).days-expected)) if matches else None

def growth(current, previous, *, years=None):
    """Ratios require comparable units/bases and positive bases; losses get state labels."""
    a=current or fact(); b=previous or fact()
    formula='(현재 ÷ 비교기간 − 1)' if years is None else '(최근 연간 ÷ 과거 연간)^(1/연수) − 1'
    result=calc(formula,[a,b],lambda x,y:x/y-1 if years is None else (x/y)**(1/years)-1,'ratio',positive=(0,1) if years else (1,))
    result['kind']='percent'
    av,bv=a['value'],b['value']
    reason=None; label=None
    if av is None or bv is None:
        reason='비교할 공시 실적 미확보'; label='비교불가'
    elif a.get('unit')!=b.get('unit'):
        reason='비교기간 통화/단위 불일치'; label='비교불가'
    elif a.get('eps_basis')!=b.get('eps_basis'):
        reason='기본 EPS와 희석 EPS의 비교 금지'; label='비교불가'
    elif a.get('comparison_key')!=b.get('comparison_key'):
        reason='비교기간 매출 집계 기준 불일치'; label='비교불가'
    elif a.get('dividend_basis')!=b.get('dividend_basis') or a.get('accounting_basis')!=b.get('accounting_basis'):
        reason='배당 또는 회계 기준 불일치'; label='비교불가'
    elif a.get('revision_basis')!=b.get('revision_basis'):
        reason='재작성 전후 연간 값의 동일 공시 기준 연결 미확인'; label='비교불가'
    elif any(c.get('accounting_basis_verified') is False or c.get('period_coverage')=='unknown' for c in (a,b)):
        reason='비교기간의 실제 기간 또는 회계 기준 미확인'; label='비교불가'
    elif years and any(c.get('previous_disclosures') and not c.get('accounting_basis_verified') for c in (a,b)):
        reason='재작성 값의 비교 가능한 회계 기준 연결 근거 미확보'; label='비교불가'
    elif years and any(c.get('start') and c.get('end') and not 330<=days(c['start'],c['end'])<=380 for c in (a,b)):
        reason='CAGR 기준 실적이 완전한 12개월 회계연도가 아님'; label='비교불가'
    elif years and a.get('end') and b.get('end') and abs(days(b['end'],a['end'])-1-years*365.25)>20:
        reason='CAGR 기준연도 간격 불일치'; label='비교불가'
    elif all(c.get('start') and c.get('end') for c in (a,b)) and abs(days(a['start'],a['end'])-days(b['start'],b['end']))>25:
        reason='비교 실적의 기간 길이 불일치'; label='비교불가'
    elif av<0<=bv:
        reason='양수 또는 0 실적에서 손실로 전환'; label='적자전환' if bv>0 else '비교불가'
    elif bv<0:
        reason='비교기간이 적자이므로 성장률을 계산하지 않음'
        label='흑자전환' if av>0 else '적자축소' if av>bv else '적자확대' if av<bv else '적자지속'
    elif bv==0 or (years and av<=0):
        reason='0 기저 또는 CAGR의 양수 조건 미충족'; label='비교불가'
    if label:
        result.update(value=None,reason=reason,display_text=label)
    if a.get('eps_basis'):
        result['eps_basis']=a['eps_basis']
    result.update(revenue_metadata(a))
    return result

def actual_periods(normal):
    """Choose one EPS basis per time series and attach same-period prior-year facts."""
    prepared=dict(normal); basis={}
    for frame in ('annual','quarterly'):
        rows=[dict(row,values=dict(row['values'])) for row in normal[frame]]
        rows.sort(key=lambda row:row['date'])
        visible=rows[-(3 if frame=='annual' else 8):]
        counts={key:sum(r['values'].get(key,{}).get('value') is not None for r in visible) for key in ('eps','basic_eps')}
        selected='basic_eps' if counts['basic_eps']>counts['eps'] else 'eps'
        basis[frame]='basic' if selected=='basic_eps' else 'diluted'
        for row in rows:
            raw=row['values'].get(selected) or fact(unit=normal['currency']+'/share',end=row['date'])
            row['values']['display_eps']=dict(raw,eps_basis=basis[frame])
        for row in rows:
            previous=comparable_period(rows,row)
            row['growth']={key:growth(row['values'].get('display_eps' if key=='eps' else key),
                previous['values'].get('display_eps' if key=='eps' else key) if previous else None)
                for key in ('sales','operating','income','eps')}
        prepared[frame]=rows
    prepared['eps_basis']=basis
    return prepared

def actual_ttm(rows, annual, key, index=None):
    """Use direct fiscal-year facts at year-end, otherwise require four adjacent quarters."""
    end_index=len(rows) if index is None else index
    if end_index<=0:
        return fact(reason='연속 4개 분기 미확보')
    current=rows[end_index-1]
    year=next((r for r in annual if r['date']==current['date']),None)
    eps_basis=current['values'].get('display_eps',{}).get('eps_basis') if key=='eps' else None
    source_key=('basic_eps' if eps_basis=='basic' else 'eps') if key=='eps' else key
    direct=year['values'].get(source_key) if year else None
    if direct and direct.get('value') is not None:
        if direct.get('period_coverage')=='unknown' or direct.get('start') and not 330<=days(direct['start'],direct['end'])<=380:
            result=fact(unit=direct.get('unit',''),end=direct.get('end'),start=direct.get('start'),sources=direct.get('sources',[]),reason='완전한 12개월 실적이 아니거나 실제 기간 미확인: TTM으로 표시하지 않음')
        else:result=dict(direct)
    else:
        result=trailing(rows,'display_eps' if key=='eps' else key,end_index)
    if eps_basis:
        result['eps_basis']=eps_basis
    return result

def summarize(normal, stock):
    normal=actual_periods(normal)
    annual=normal['annual']; quarters=normal['quarterly']; currency=normal['currency']
    if not quarters or not annual:
        raise ValueError('OFFICIAL_PERIODS_UNAVAILABLE')
    current=quarters[-1]; v=current['values']; year=annual[-1]; qrows=quarters
    bank=normal.get('industry_model')=='bank' or v.get('sales',{}).get('basis')=='bank_net_revenue'
    insurer=normal.get('industry_model')=='insurance'
    bank_inapplicable={'fcf','pfcf','ev','evsales','evebitda','current','quick','debt_equity','long_debt_equity','gross_margin','roic'}
    t={key:actual_ttm(qrows,annual,key) for key in ('sales','income','net_income','gross','operating','cfo','capex','eps','dividend','da')}
    eps_label={frame:('기본' if basis=='basic' else '희석')+' EPS' for frame,basis in normal['eps_basis'].items()}
    items=[]
    def add(id,label,cell,group='실적',kind=None):
        if (bank or insurer) and id in bank_inapplicable:
            cell=dict(cell,value=None,status='not_applicable',display_text='업종 비적용',
                      reason='금융업의 예금·대출·보험부채·금융자산 구조에는 일반기업의 유동성·산업부채·FCF·EV·매출원가 기준 지표를 적용하지 않습니다.')
        if cell.get('eps_aggregation')=='quarter_sum':
            label=label.replace('TTM','분기 합산 TTM')
        items.append(dict(cell,id=id,label=label,group=group,kind=kind or cell.get('kind') or ('money' if cell['unit'].startswith(currency) else 'number')))
    def ratio(id,label,a,b,group='가치',percent=False):
        if b and b.get('basis') in ('bank_net_revenue','insurance_revenue'):
            label+=' ('+('순영업수익' if b['basis']=='bank_net_revenue' else '보험수익')+' 기준)'
        cell=calc(label,[a,b],lambda x,y:x/y,'ratio',positive=(1,))
        if b:
            cell.update(revenue_metadata(b))
        if b and b.get('eps_basis'):
            cell['eps_basis']=b['eps_basis']
        if b and b.get('eps_aggregation'):
            cell['eps_aggregation']=b['eps_aggregation'];cell['comparison_note']=b.get('comparison_note')
        if id=='pb':
            label='P/B · 보통주자본 기준'
            cell.update(formula='보통주 시가총액 / 보통주 귀속 자본',equity_basis='common_equity')
        if id in ('pe','pe_annual') and b and b.get('value') is not None and b['value']<=0:
            cell.update(display_text='적자' if b['value']<0 else '비교불가',reason='EPS가 음수여서 P/E를 계산하지 않음' if b['value']<0 else 'EPS가 0이어서 P/E를 계산하지 않음')
        add(id,label,cell,group,'percent' if percent else 'multiple')
    bars=[b for b in stock.get('bars',[]) if all(number(b.get(k)) is not None for k in ('close','high','low','volume'))]
    bars.sort(key=lambda b:b['date']); last=bars[-1] if bars else {}; price_date=last.get('date')
    def market(value,formula,unit=currency):
        return fact(value,unit=unit,end=price_date,sources=['saved-price.json'],formula=formula)
    price=market(last.get('close'),'저장된 완료 일봉 종가 · 공급자 수정주가')
    share_records=[r for r in normal.get('share_history',[]) if r['values'].get('shares',{}).get('value') is not None and (not price_date or r['date']<=price_date)]
    share_record=max(share_records,key=lambda r:r['date']) if share_records else None
    shares=share_record['values']['shares'] if share_record else v.get('shares',fact(unit='shares'))
    if shares['value'] is not None and shares.get('end')!=current['date']:
        shares=dict(shares,comparison_note='최신 수량이 아니라 가장 최근에 수량을 확인한 공시 기말입니다. 이후 주식수 변동을 추정하지 않습니다.')
    cap=calc('저장 종가 × 최근 공시 보통주 유통주식수',[price,shares],lambda a,b:a*b,currency)
    # Price predating financial balance cannot be presented as current valuation.
    if not price_date or price_date<current['date']:
        cap=fact(unit=currency,reason='가격 기준일이 최신 재무기간보다 이전')
        price_for_valuation=fact(unit=currency,reason='가격/재무 시점 불일치')
    else: price_for_valuation=price
    preferred_shares=share_record['values'].get('preferred_shares') if share_record else v.get('preferred_shares')
    multi_class=any((cell or {}).get('value') is not None and cell['value']>0
                    for cell in (preferred_shares,v.get('preferred_issued_shares'),v.get('preferred_equity')))
    preferred_unknown=normal['provider']=='DART' and (preferred_shares or {}).get('stock_class_present') and preferred_shares.get('value') is None
    enterprise_cap=fact(unit=currency,reason='우선주 종류가 공시되었으나 수량이 미확보되어 전체 시가총액을 확정하지 않음') if preferred_unknown else fact(unit=currency,reason='우선주 시가총액·보통주 귀속 자본 미확보: 보통주만으로 전체 기업 배수 계산 불가') if multi_class else cap
    preferred_price=stock.get('preferred_price',{})
    pb=preferred_price.get('bar') or {}
    if multi_class and preferred_price.get('currency')==currency and pb.get('date')==price_date:
        pp=fact(pb.get('close'),unit=currency,end=price_date,sources=['saved-price.json'],formula='공식 종목 대응 우선주 저장 종가')
        preferred_cap=calc('우선주 저장 종가 × 우선주 기말 유통주식수',[pp,preferred_shares],lambda a,b:a*b,currency)
        enterprise_cap=calc('보통주 시가총액 + 우선주 시가총액 (종류별 가격×유통주식수)',[cap,preferred_cap],lambda a,b:a+b,currency)
    fcf=calc('TTM 영업현금흐름 − TTM 공시 자본지출 (유형/생산자산 태그 기준, 금융상환 제외)',[t['cfo'],t['capex']],lambda a,b:a-b,currency)
    common_equity=v.get('common_equity')
    if not common_equity or common_equity.get('value') is None:
        if multi_class or preferred_unknown:
            common_equity=statement_calc('지배주주자본 − 우선주 장부금액',[v.get('equity'),v.get('preferred_equity')],lambda a,b:a-b,currency)
        else:
            common_equity=v.get('equity')
        if v.get('hybrid_equity',{}).get('value') is not None:
            parent=common_equity
            common_equity=statement_calc('지배주주자본 중 다른 주식종류 조정 후 − 신종자본증권',[parent,v['hybrid_equity']],lambda a,b:a-b,currency)
    ev=calc('시가총액 + 이자부 부채 − 현금및현금성자산 (단기투자자산 제외)',[enterprise_cap,v.get('debt'),v.get('cash')],lambda a,b,c:a+b-c,currency)
    sales_label=t['sales'].get('account_label') or v.get('sales',{}).get('account_label') or '매출'
    for id,label,cell in [('sales',sales_label+' · TTM',t['sales']),('operating','영업이익 · TTM',t['operating']),('income','지배주주 순이익 · TTM',t['income']),('eps',eps_label['quarterly']+' · TTM',t['eps']),('annual_eps',eps_label['annual']+' · 최근 연간',year['values']['display_eps']),('latest_quarter_eps',eps_label['quarterly']+' · 최근 분기',v['display_eps']),('cfo','영업현금흐름 · TTM',t['cfo']),('fcf','FCF · 자체 산식 TTM',fcf)]:add(id,label,cell)
    add('market_cap','보통주 시가총액',cap,'가치');add('all_market_cap','전체 주식 시가총액',enterprise_cap,'가치');add('ev','기업가치 · EV',ev,'가치')
    ratio('pe','P/E · 실적 TTM',price_for_valuation,t['eps'])
    ratio('pe_annual','P/E · 최근 연간',price_for_valuation,year['values']['display_eps'])
    ratio('ps','P/S · 보통주시총 기준' if bank else 'P/S · TTM',cap if bank else enterprise_cap,t['sales']);ratio('pb','P/B',cap,common_equity)
    ratio('pc','P/Cash · 보통주시총 기준' if bank else 'P/Cash',cap if bank else enterprise_cap,v.get('cash'));ratio('pfcf','P/FCF',enterprise_cap,fcf)
    ratio('evsales','EV/Sales',ev,t['sales'])
    ebitda=calc('TTM 영업이익 + TTM 감가상각·무형자산상각',[t['operating'],t['da']],lambda a,b:a+b,currency)
    ratio('evebitda','EV/EBITDA',ev,ebitda)
    book=calc('보통주 귀속 자본 ÷ 같은 기말 보통주 유통주식수',[common_equity,v.get('shares')],lambda a,b:a/b,currency+'/share',positive=(1,))
    add('book','주당 순자산',book,'가치')
    add('cash_share','주당 현금',calc('현금 ÷ 보통주 유통주식수',[v.get('cash'),shares],lambda a,b:a/b,currency+'/share',positive=(1,)) if not multi_class else fact(unit=currency+'/share',reason='우선주 포함 현금 귀속 분리 미확보'),'가치')
    for id,label,num,den in [('gross_margin','매출총이익률',t['gross'],t['sales']),('operating_margin','영업이익률',t['operating'],t['sales']),('net_margin','지배주주 순이익률',t['income'],t['sales'])]:ratio(id,label,num,den,'수익·재무',True)
    prior=next((r for r in reversed(qrows[:-1]) if 330<=days(r['date'],current['date'])-1<=380),None)
    for id,label,nkey,bkey in [('roa','ROA · 평균자산','net_income','assets'),('roe','ROE · 평균자본','income','equity')]:
        avg=calc('현재·전년동기 기말 평균',[v.get(bkey),prior['values'].get(bkey) if prior else None],lambda a,b:(a+b)/2,currency)
        ratio(id,label,t[nkey],avg,'수익·재무',True)
    ratio('current','유동비율',v.get('current_assets'),v.get('current_liabilities'),'수익·재무')
    quick=calc('현금 + 매출채권 (기타 당좌자산 제외)',[v.get('cash'),v.get('receivables')],lambda a,b:a+b,currency)
    ratio('quick','당좌비율 · 보수적',quick,v.get('current_liabilities'),'수익·재무')
    ratio('debt_equity','이자부부채/자본',v.get('debt'),v.get('equity'),'수익·재무')
    ratio('long_debt_equity','장기부채/자본',v.get('debt_long'),v.get('equity'),'수익·재무')
    add('shares','보통주 유통주식수 · 최근 확인',shares,'주식·배당','shares')
    dividend=t['dividend'];payout_eps=t['eps'];dividend_label='주당배당 · TTM';yield_label='배당수익률 · TTM';payout_label='배당성향 · TTM EPS'
    if dividend['value'] is None:
        dividend_year=next((row for row in reversed(annual) if row['values'].get('dividend',{}).get('value') is not None and row['date']<=current['date']),None)
        if dividend_year:
            dividend=dict(dividend_year['values']['dividend'],comparison_note='확정된 최근 연간 주당배당입니다. TTM·예상 배당으로 환산하지 않습니다.')
            payout_eps=dividend_year['values'].get('display_eps')
            dividend_label='주당배당 · 최근 연간';yield_label='배당수익률 · 최근 연간';payout_label='배당성향 · 연간 EPS'
    if dividend.get('dividend_basis') in ('paid','declared'):
        dividend_label=('지급 ' if dividend['dividend_basis']=='paid' else '선언 ')+dividend_label
    add('dividend',dividend_label,dividend,'주식·배당')
    ratio('yield',yield_label,dividend,price_for_valuation,'주식·배당',True)
    ratio('payout',payout_label,dividend,payout_eps,'주식·배당',True)
    for key,label in [('sales',sales_label),('operating','영업이익'),('income','지배주주 순이익'),('eps','EPS')]:
        value_key='display_eps' if key=='eps' else key
        for lag,title in [(1,'직전분기 대비'),(4,'전년동기 대비')]:
            previous=comparable_period(qrows,current,quarter=lag==1)
            add(f'{key}_growth_{lag}',label+' '+title,growth(v.get(value_key),previous['values'].get(value_key) if previous else None),'성장','percent')
        previous=comparable_period(qrows,current)
        previous_ttm=actual_ttm(qrows,annual,key,qrows.index(previous)+1) if previous else None
        add(key+'_ttm_growth',label+' TTM 전년 대비',growth(t[key],previous_ttm),'성장','percent')
        for years in (3,5):
            previous=comparable_period(annual,year,years=years)
            cagr=growth(year['values'].get(value_key),previous['values'].get(value_key) if previous else None,years=years)
            cagr_label=label+f' {years}년 CAGR'
            if key=='eps' and (year['values'].get(value_key,{}).get('value') is None or not previous or previous['values'].get(value_key,{}).get('value') is None):
                latest_year=int(year['date'][:4]);reported={int(row['date'][:4]):row['values'].get('reported_eps') for row in annual}
                if all(reported.get(y) and reported[y]['value'] is not None for y in range(latest_year-years,latest_year+1)):
                    cagr=growth(reported[latest_year],reported[latest_year-years],years=years)
                    cagr_label=f'공시 EPS {years}년 CAGR'
                    cagr.update(eps_basis='reported',status='unavailable' if cagr['value'] is None else 'comparison_warning',
                        comparison_note='전기간 배당관련 공시의 연결 EPS라는 동일 원천 계열을 사용합니다. 기본·희석 구분은 명시되지 않았으며 기간 중 회계정책 변경·재작성 효과가 포함될 수 있습니다. XBRL 기본·희석 EPS와 혼합하지 않습니다.')
            add(f'{key}_cagr{years}',cagr_label,cagr,'성장','percent')
    add('price','저장 종가',price,'시세')
    add('previous_close','전일 종가',market(bars[-2]['close'] if len(bars)>1 else None,'직전 저장 완료봉 종가'),'시세')
    for n in (20,50,200):
        avg=market(sum(b['close'] for b in bars[-n:])/n if len(bars)>=n else None,f'{n}개 완료봉 종가 산술평균')
        add('sma'+str(n),f'SMA{n} 이격',calc('종가 / SMA − 1',[price,avg],lambda a,b:a/b-1,'ratio',positive=(1,)),'시세','percent')
    for n,label in [(1,'당일'),(5,'1주'),(21,'1개월'),(63,'3개월'),(126,'6개월'),(252,'1년'),(756,'3년'),(1260,'5년'),(2520,'10년')]:
        prev=market(bars[-n-1]['close'] if len(bars)>n else None,f'{n}거래봉 전 종가')
        add('perf'+str(n),label+' 수익률',calc('종가 / N거래봉 전 종가 − 1',[price,prev],lambda a,b:a/b-1,'ratio',positive=(1,)),'시세','percent')
    for key,fn in [('high52',max),('low52',min)]:
        add(key,'52주 '+('최고' if key=='high52' else '최저'),market(fn(b['high' if key=='high52' else 'low'] for b in bars[-252:]) if len(bars)>=252 else None,'최근 252 완료봉 범위'),'시세')
    add('volume','거래량',market(last.get('volume'),'최근 완료봉 거래량','shares'),'시세','shares')
    av=market(sum(b['volume'] for b in bars[-21:-1])/20 if len(bars)>=21 else None,'직전 20봉 평균 거래량','shares')
    add('avg_volume','평균거래량 · 20일',av,'시세','shares');ratio('relative_volume','상대거래량',market(last.get('volume'),'최근 완료봉 거래량','shares'),av,'시세')
    trs=[max(b['high']-b['low'],abs(b['high']-a['close']),abs(b['low']-a['close'])) for a,b in zip(bars,bars[1:])]
    atr=sum(trs[:14])/14 if len(trs)>=14 else None
    for tr in trs[14:]:atr=(atr*13+tr)/14
    add('atr','ATR · 14 Wilder',market(atr,'TR의 14기간 Wilder 평활'),'시세')
    changes=[b['close']-a['close'] for a,b in zip(bars,bars[1:])];rsi=None
    if len(changes)>=14:
        gain=sum(max(x,0) for x in changes[:14])/14;loss=sum(max(-x,0) for x in changes[:14])/14
        for x in changes[14:]:gain=(gain*13+max(x,0))/14;loss=(loss*13+max(-x,0))/14
        rsi=100-100/(1+gain/loss) if loss else 100 if gain else 50
    add('rsi','RSI · 14 Wilder',market(rsi,'상승/하락 종가 변화 14기간 Wilder','index'),'시세','number')
    for id,label,reason in [('float','유동주식수','비유동 보유분 자료 미확보'),('insider','내부자 지분/변화','내부자 소유권 공시 별도 집계 미구현'),('institution','기관 지분/변화','기관 보유보고서 별도 집계 미구현'),('short','공매도 잔고/비율','공식 공매도 잔고 원자료 미확보'),('beta','베타','동일 기간 시장 기준수익률 미연결'),('roic','ROIC','영업투하자본·세율 조정 근거 미확보')]:
        add(id,label,fact(reason=reason),'별도 자료','number')
    coverage=dict(annual_years=[int(row['date'][:4]) for row in annual],reported_eps_years=[int(row['date'][:4]) for row in annual if row['values'].get('reported_eps',{}).get('value') is not None],
                  quarterly_periods=[row['date'] for row in quarters],shares_latest_end=shares.get('end') if shares.get('value') is not None else None,
                  notes=['당기·전기·전전기 명시기간 확인 후 최신 공시값 채택','공시 EPS는 기본·희석 EPS와 별도 계열'] if normal['provider']=='DART' else [])
    return dict(schema=1,calculation_version=VERSION,**normal,summary=items,ttm=t,price_as_of=price_date,financial_as_of=current['date'],coverage_metadata=coverage,
                price_provider=stock.get('provider'))
