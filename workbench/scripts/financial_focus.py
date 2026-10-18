"""Focused official performance and solvency contract, shared by UI and MCP."""
from copy import deepcopy
from financial_model import VERSION as NORMALIZATION_VERSION, fact, calc, growth, comparable_period, days

VERSION = 'financial-focus-8'
LABELS = {'sales':'매출액', 'operating':'영업이익', 'income':'지배주주 순이익',
          'basic_eps':'기본 EPS', 'eps':'희석 EPS', 'operating_margin':'영업이익률',
          'net_margin':'순이익률', 'current_ratio':'유동비율', 'debt_ratio':'부채비율'}
DEFINITIONS = {
    'net_margin':('순이익 ÷ 매출액', '공시 순이익이 같은 기간 매출에서 차지하는 비중. 원값의 귀속 범위를 확인하세요.', 'percent'),
    'current_ratio':('유동자산 ÷ 유동부채', '동일 기말 유동부채 대비 유동자산', 'percent'),
    'operating_margin':('영업이익 ÷ 매출액', '동일 기간 영업이익이 매출에서 차지하는 비중', 'percent'),
    'debt_ratio':('총부채 ÷ 자본총계', '기말 총부채/자본총계. 종전 이자부부채/지배주주자본과 다른 정의', 'percent')}


def origin(cell):
    if cell.get('source_type') in ('company_reported','opendart_indicator'):
        return cell['source_type']
    derived=cell.get('inputs') or cell.get('splits') or str(cell.get('tag') or '').startswith('derived_')
    return 'company_reported' if cell.get('formula','').startswith('공시') and not derived else 'calculated'


def total_equity(values, currency):
    a,b=values.get('assets') or fact(),values.get('liabilities') or fact()
    result=calc('자산총계 − 부채총계',[a,b],lambda x,y:x-y,currency)
    signatures={(c.get('end'),c.get('filed'),c.get('accession') or c.get('receipt')) for c in (a,b)}
    if len(signatures)!=1 or not a.get('end') or not a.get('filed'):
        result.update(value=None,reason='같은 기말·공시의 자산총계와 부채총계 미확보')
    return result


def compatible(a, b, *, same_period=False):
    if not a or not b or a.get('value') is None or b.get('value') is None:
        return '필수 공시 원값 미확보'
    if a.get('unit') != b.get('unit'):
        return '통화·단위 불일치'
    for field in (('accounting_basis',) if same_period else ('accounting_basis','revision_basis')):
        if a.get(field) != b.get(field):
            return '회계 범위 또는 정정 공시 비교 기준 불일치'
    if any(c.get('period_coverage') == 'unknown' or c.get('accounting_basis_verified') is False for c in (a,b)):
        return '실제 기간 또는 회계 기준 미확인'
    if same_period and (a.get('start'),a.get('end')) != (b.get('start'),b.get('end')):
        return '서로 다른 기간의 원값은 비율로 결합하지 않음'
    if same_period and any(a.get(k) and b.get(k) and a[k]!=b[k] for k in ('receipt','accession')):
        lineages=[set([c.get('receipt'),c.get('accession'),*c.get('equivalent_filings',[])])-{None} for c in (a,b)]
        if not lineages[0]&lineages[1]:return '동일 기간 원값의 공시 버전 불일치'
    if same_period and a.get('period_disclosures') and b.get('period_disclosures'):
        left,right=a['period_disclosures'],b['period_disclosures']
        if len(left)!=len(right) or any((x.get('start'),x.get('end'))!=(y.get('start'),y.get('end')) or
                not set(x.get('filings',[]))&set(y.get('filings',[])) for x,y in zip(left,right)):
            return '분기 차감에 사용한 누적 원값의 공시 묶음 불일치'
    if a.get('comparison_key') != b.get('comparison_key') and not same_period:
        return '비교 정의 불일치'
    if not same_period:
        check=growth(a,b)
        if check.get('display_text') == '비교불가':
            # A zero base prevents a rate, but a verified absolute change remains valid.
            if a['value'] and b['value'] != 0:
                return check.get('reason')
            for field in ('eps_basis','comparison_key','dividend_basis'):
                if a.get(field)!=b.get(field):return '비교 기준 불일치'
            if a.get('start') and b.get('start') and abs(days(a['start'],a['end'])-days(b['start'],b['end']))>25:
                return '비교기간 길이 불일치'
    return None


def movement(cell, previous, *, reverse=False, ratio=False):
    result=deepcopy(cell)
    reason=compatible(cell,previous)
    delta=None if reason else cell['value']-previous['value']
    result['change']=dict(value=delta,unit='percentage_point' if ratio and cell.get('kind')=='percent' else cell.get('unit'),
                          compared_to=(previous or {}).get('end'),reason=reason,
                          formula='현재 − 비교기간',sources=sorted(set(cell.get('sources',[])+(previous or {}).get('sources',[]))),
                          previous=deepcopy(previous))
    direction=0 if delta is None or delta==0 else (1 if delta>0 else -1)*(-1 if reverse else 1)
    result['trend']='improved' if direction>0 else 'worsened' if direction<0 else 'neutral'
    result['change']['trend']=result['trend']
    result['source_type']=origin(result)
    return result


def ratio(values, key):
    formula,explanation,kind=DEFINITIONS[key]
    total_net=values.get('net_income')
    use_total=compatible(total_net,values.get('sales'),same_period=True) is None
    if not use_total and compatible(values.get('income'),values.get('sales'),same_period=True) is not None:
        use_total=(total_net or {}).get('value') is not None
    numerator,denominator={'net_margin':('net_income' if use_total else 'income','sales'),'current_ratio':('current_assets','current_liabilities'),'operating_margin':('operating','sales'),'debt_ratio':('liabilities','total_equity')}[key]
    a,b=values.get(numerator),values.get(denominator)
    denominator_tag=(b or {}).get('tag')
    cell=calc(formula,[a,b],lambda x,y:x/y,'ratio' if kind=='percent' else 'multiple',positive=(1,))
    reason=compatible(a,b,same_period=True)
    if reason:cell.update(value=None,reason=reason)
    elif b['value']<=0:
        cell.update(status='not_computable',display_text='산출 불가',reason='분모가 0 이하인 공시값: 이 비율의 정의상 산출하지 않습니다.')
    # Explicit direct ratios can only supersede calculations with a verified contract.
    direct=values.get(key)
    if direct and direct.get('value') is not None and direct.get('definition')==formula and direct.get('unit')==cell['unit'] and direct.get('source_type') in ('company_reported','opendart_indicator') and direct.get('definition_verified') is True and (direct.get('start'),direct.get('end'))==((a or {}).get('start'),(a or {}).get('end')):
        cell=deepcopy(direct)
    if key=='net_margin':
        cell['comparison_key']='net_margin:'+numerator+':'+str((b or {}).get('comparison_key'))
        if numerator=='income':explanation='지배주주/공시 순이익 ÷ 매출액. 분자의 귀속 범위는 공시 근거에서 확인.'
    if key=='operating_margin':cell['comparison_key']='margin:'+str((b or {}).get('comparison_key'))
    cell.update(id=key,label=LABELS[key],kind=kind,definition=formula,explanation=explanation,source_type=origin(cell))
    return cell


def summarize(normal, stock):
    """No forecasts, market-price multiples, thresholds, or synthetic missing values."""
    result=deepcopy(normal)
    for frame in ('annual','quarterly'):
        result[frame]=[row for row in result.get(frame,[]) if any(row['values'].get(k,{}).get('value') is not None
                      for k in ('sales','operating','income','net_income'))]
    if not any(row['values'].get(k,{}).get('value') is not None for frame in ('annual','quarterly')
               for row in result.get(frame,[]) for k in ('sales','operating','income','net_income')):
        raise ValueError('OFFICIAL_PERIODS_UNAVAILABLE')
    from financial_cohort import select_bundles
    for frame in ('annual','quarterly'):
        for row in result.get(frame,[]):select_bundles(row['values'])
    keys=['sales','operating','income']
    # A complete consolidated profit series is an explicit alternative scope,
    # never a per-cell substitution labelled as profit attributable to owners.
    all_rows=[row for f in ('annual','quarterly') for row in result.get(f,[])]
    use_total=(normal['provider']=='DART' or sum(r['values'].get('net_income',{}).get('value') is not None for r in all_rows)>sum(r['values'].get('income',{}).get('value') is not None for r in all_rows)) and any(r['values'].get('income',{}).get('value') is None and
        r['values'].get('net_income',{}).get('value') is not None for r in all_rows)
    labels=dict(LABELS)
    if normal.get('security_basis',{}):
        labels['basic_eps']='기본 EPS (보통주)'
        labels['eps']='희석 EPS (보통주)'
    if use_total:
        labels['income']='순이익 (전체)'
        for row in all_rows:
            row['values']['parent_income']=deepcopy(row['values'].get('income',fact()))
            row['values']['income']=deepcopy(row['values'].get('net_income',fact()))
            row['values']['income']['comparison_key']='total_net_income'
        result['income_scope']='total_net_income'
    if normal['provider']=='SEC':
        keys += [key for key in ('basic_eps','eps') if any(row['values'].get(key,{}).get('value') is not None
                 for frame in ('annual','quarterly') for row in result.get(frame,[]))]
    result['performance_metrics']=[dict(id=key,label=labels[key]) for key in keys]
    result['display_range']={'annual':5,'quarterly':12}
    for frame in ('annual','quarterly'):
        rows=sorted(result.get(frame,[]),key=lambda r:r['date'])
        # First attach period facts; compare only after every row has been prepared.
        for row in rows:
            v=row['values']
            if normal['provider']=='SEC' and v.get('total_equity',{}).get('tag')=='StockholdersEquity':
                # Parent equity is not total equity. Derive only from one balance sheet.
                v['total_equity']=total_equity(v,normal['currency'])
            for key in keys:
                v.setdefault(key,fact(unit=normal['currency']+('/share' if 'eps' in key else ''),end=row['date']))
                v[key].update(id=key,label=(v[key].get('account_label') or labels[key]) if key=='sales' else labels[key],kind='money',source_type=origin(v[key]))
            if normal['provider']=='SEC':
                v.setdefault('shares',fact(unit='shares',end=row['date']))
                v['shares'].update(id='shares',label='기말 유통주식수',kind='shares')
            row['metrics']={key:ratio(v,key) for key in DEFINITIONS}
            if normal['provider']=='SEC':
                from financial_returns import attach_returns
                attach_returns(v,row['metrics'],normal['currency'])
            if normal.get('industry_model')=='bank':
                for group,ids in ((v,('operating',)),(row['metrics'],('operating_margin','current_ratio'))):
                    for key in ids:
                        if group[key]['value'] is None:
                            group[key].update(status='not_applicable',display_text='업종 해당 없음',
                                reason='은행은 일반 사업회사의 영업이익·유동성 분류 정의를 적용하지 않습니다.')
            equity=v.get('total_equity') or fact(unit=normal['currency'],end=row['date'])
            row['warnings']=[dict(code='NON_POSITIVE_EQUITY',message='자본총계가 0 이하입니다.',evidence=deepcopy(equity))] if equity['value'] is not None and equity['value']<=0 else []
            row['growth']={};row['qoq']={}
            for key in keys:
                for target,quarter in (('growth',False),('qoq',True)):
                    previous=comparable_period(rows,row,quarter=quarter) if target=='growth' or frame=='quarterly' else None
                    prior_cell=previous['values'].get(key) if previous else None
                    cell=growth(v[key],prior_cell)
                    reason=compatible(v[key],prior_cell)
                    if reason:cell.update(value=None,display_text='비교불가',reason=reason)
                    cell.update(id=key+'_'+('yoy' if target=='growth' else 'qoq'),label=labels[key]+(' 증가율 (YoY)' if target=='growth' else ' 증가율 (QoQ)'),source_type='calculated',comparison='YoY' if target=='growth' else 'QoQ')
                    row[target][key]=cell
        original=deepcopy(rows)
        for row in rows:
            previous=comparable_period(original,row)
            row['metrics']={key:movement(cell,previous['metrics'][key] if previous else None,reverse=key=='debt_ratio',ratio=True) for key,cell in row['metrics'].items()}
            row['performance']={};row['performance_qoq']={}
            prior_quarter=comparable_period(original,row,quarter=True) if frame=='quarterly' else None
            # Display comparisons share the same period/basis validation as performance.
            display_keys=set(keys) | {'net_income','dividend','total_equity','liabilities','current_assets','current_liabilities'}
            if normal['provider']=='SEC':display_keys.update(('shares','cash_dividends','buybacks','shareholder_return'))
            for target,prior in (('value_movements',previous),('value_movements_qoq',prior_quarter)):
                row[target]={key:movement(row['values'][key],prior['values'].get(key) if prior else None,
                    reverse=key in {'liabilities','current_liabilities','shares'})
                    for key in sorted(display_keys) if key in row['values']}
            for key in keys:
                row['performance'][key]=movement(row['values'][key],previous['values'].get(key) if previous else None)
                row['performance_qoq'][key]=movement(row['values'][key],prior_quarter['values'].get(key) if prior_quarter else None)
                for target,quarter in (('growth',False),('qoq',True)):
                    prior=comparable_period(original,row,quarter=quarter) if target=='growth' or frame=='quarterly' else None
                    row[target][key]=movement(row[target][key],prior[target][key] if prior else None,ratio=True)
                    label=row[target][key].get('display_text')
                    if label in ('흑자전환','적자축소'):row[target][key]['trend']='improved'
                    elif label in ('적자전환','적자확대'):row[target][key]['trend']='worsened'
        result[frame]=rows[-result['display_range'][frame]:]
    frame='quarterly' if result['quarterly'] else 'annual'
    latest=result[frame][-1]
    result.update(schema=2,calculation_version=VERSION,normalization_version=NORMALIZATION_VERSION,
                  financial_as_of=latest['date'],summary_frame=frame,
                  summary=[*latest['metrics'].values(),*latest['growth'].values()],warnings=normal.get('warnings',[]),
                  coverage_metadata={f:dict(target=result['display_range'][f],available=len(result[f]),
                       first=result[f][0]['date'] if result[f] else None,last=result[f][-1]['date'] if result[f] else None)
                       for f in ('annual','quarterly')})
    result['limitations']=[x for x in result.get('limitations',[]) if '기본·희석 중 하나' not in x]+[
        '미국 기본·희석 EPS는 각각 직접 공시된 종류만 표시합니다. 합성 EPS·부족 기간 추정 없음.',
        '비율은 정의·기간·단위를 확인한 직접 제공값을 우선하며 그 외 확인된 원값으로 계산합니다.',
        'OpenDART 주요지표의 단위·누적 기준 미검증 값은 채택하지 않습니다.',
        '증감 색상은 비교기간 대비 변화이며 매수·매도 판정이 아닙니다.']
    return result
