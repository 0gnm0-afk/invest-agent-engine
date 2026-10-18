"""Explicit financial-statement scope. A missing API response is not a company type."""
import re


def eligibility(identity, provider, facts=None):
    name=str(identity.get('name') or identity.get('corp_name') or '')
    sic=str(identity.get('sic') or '')
    kind=None
    if provider=='SEC':
        kind={'6189':'자산유동화 신탁증권','6722':'투자펀드','6726':'투자신탁','6770':'사업결합 전 특수목적회사'}.get(sic)
        taxonomy=(facts or {}).get('facts',{}).get('us-gaap',{})
        if not kind and (identity.get('entityType')=='investment' or
                         'InvestmentCompanyNetAssetsFromOperationsIncreaseDecrease' in taxonomy):
            kind='투자회사·펀드'
    elif re.search(r'기업인수목적|스팩',name):
        kind='사업결합 전 특수목적회사'
    return dict(status='unsupported' if kind else 'eligible',kind=kind or '사업회사',
                reason=f'{kind}으로 분류되어 사업회사 재무정보를 반환할 수 없습니다.' if kind else None,
                evidence=dict(provider=provider,sic=sic,name=name))


def unsupported(source):
    return dict(schema=2,provider=source['provider'],identity=source['identity'],eligibility=source['eligibility'],
                annual=[],quarterly=[],summary=[],performance_metrics=[],financial_as_of=None,
                coverage_metadata={},limitations=[source['eligibility']['reason']],warnings=[],
                name=source['identity'].get('name') or source['identity'].get('corp_name'))


def coverage(result):
    """Separate unsupported ratios, incomplete source data, and short history."""
    # The DART dividend supplement is explicitly annual. Do not mislabel its
    # absence in quarterly cells as twelve independent collection failures.
    annual_dividend_only=(result.get('provider')=='DART' and
        any(r.get('values',{}).get('dividend',{}).get('dividend_basis')=='annual_common_cash' for r in result.get('annual',[])) and
        not any(r.get('values',{}).get('dividend',{}).get('value') is not None for r in result.get('quarterly',[])))
    if annual_dividend_only:
        for row in result.get('quarterly',[]):
            row['values'].setdefault('dividend',{'value':None,'unit':'KRW/share'}).update(
                status='annual_only',display_text='연간 공시',reason='DART 사업연도 배당 공시입니다. 배당 차트에서 연간 내역을 확인하세요.')
    missing=[]
    for frame in ('annual','quarterly'):
        for row in result.get(frame,[]):
            for group,keys in [('values',['sales','operating','income']),('metrics',['operating_margin','net_margin','debt_ratio','current_ratio'])]:
                for key in keys:
                    cell=row[group].get(key,{})
                    if cell.get('value') is None and cell.get('status') not in ('not_applicable','not_computable'):
                        missing.append(dict(frame=frame,date=row['date'],field=key,reason=cell.get('reason')))
    supplemental=[]
    for frame in ('annual','quarterly'):
        for row in result.get(frame,[]):
            keys=(['basic_eps','eps','shares','cash_dividends','buybacks','shareholder_return'] if result['provider']=='SEC' else ['basic_eps','eps'])+['dividend']
            for key in keys:
                cell=row['values'].get(key,{})
                if cell.get('value') is None and cell.get('status') not in ('not_applicable','not_computable','annual_only'):supplemental.append(dict(frame=frame,date=row['date'],field=key,reason=cell.get('reason')))
            if result['provider']=='SEC':
                cell=row['metrics'].get('shareholder_payout',{})
                if cell.get('value') is None and cell.get('status')!='not_computable':
                    supplemental.append(dict(frame=frame,date=row['date'],field='shareholder_payout',reason=cell.get('reason')))
    result['data_quality']=dict(state='partial' if missing or supplemental else 'complete',missing=missing,
                               core_complete=not missing,supplemental_missing=supplemental,annual_dividend_only=annual_dividend_only,
                               history_limited=any(len(result.get(f,[]))<n for f,n in [('annual',5),('quarterly',12)]))
    return result
