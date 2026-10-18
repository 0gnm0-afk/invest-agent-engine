"""Actual common shareholder distributions, never authorizations or estimates."""
import re
from copy import deepcopy
from financial_model import fact,calc


def reporting_currency(source):
    if 'us-gaap' not in source['facts']['facts'] and 'ifrs-full' in source['facts']['facts']:
        from financial_sec_ifrs import reporting_currency as ifrs_currency
        return source.get('reporting_currency') or ifrs_currency(source)
    units=set()
    for tag in ('Revenues','RevenueFromContractWithCustomerExcludingAssessedTax','SalesRevenueNet','RevenuesNetOfInterestExpense'):
        for unit,rows in source['facts']['facts'].get('us-gaap',{}).get(tag,{}).get('units',{}).items():
            if re.fullmatch('[A-Z]{3}',unit) and any(r.get('form') in ('10-K','10-K/A','20-F','20-F/A') for r in rows):units.add(unit)
    if not units:return 'USD'  # Existing USD-only fixtures and unsupported taxonomies.
    if len(units)!=1:raise ValueError('SEC_REPORTING_CURRENCY_AMBIGUOUS')
    return next(iter(units))


def attach_returns(values,metrics,currency):
    from financial_focus import compatible
    def match(a,b):
        reason=compatible(a,b,same_period=True)
        if reason=='동일 기간 원값의 공시 버전 불일치' and any(c.get('confirmed_zero') for c in (a,b)):
            return None  # Explicit historical non-payment covers this exact period.
        return reason
    a=values.setdefault('cash_dividends',fact(unit=currency,reason='보통주 현금배당 지급 총액 미확보'))
    b=values.setdefault('buybacks',fact(unit=currency,reason='실제 보통주 매입액 미확보'))
    total=calc('보통주 현금배당 지급액 + 실제 자사주 매입액',[a,b],lambda x,y:x+y,currency)
    reason=match(a,b)
    if reason:total.update(value=None,reason=reason)
    if any(c.get('value') is not None and c['value']<0 for c in (a,b)):
        total.update(value=None,reason='지급·매입액의 부호/정의 확인 필요')
    # The common-holder denominator must retain its actual attribution.
    income=next((values[k] for k in ('common_income','parent_income','income') if values.get(k,{}).get('value') is not None and
                 values[k].get('comparison_key')!='total_net_income'),fact(unit=currency,reason='보통주/지배주주 귀속 순이익 미확보'))
    rate=calc('(보통주 현금배당 + 자사주 매입액) ÷ 순이익',[total,income],lambda x,y:x/y,'ratio',positive=(1,))
    reasons=[match(c,income) for c in (a,b)]
    if any(reasons):rate.update(value=None,reason=next(r for r in reasons if r))
    elif income.get('value') is not None and income['value']<=0:rate.update(value=None,status='not_computable',display_text='산출 불가',reason='순이익이 0 이하인 기간입니다. 환원 금액은 별도 표시합니다.')
    values['shareholder_return']=total
    for key,label in [('cash_dividends','현금배당 총액'),('buybacks','자사주 매입액'),('shareholder_return','주주환원 합계')]:
        values[key].update(id=key,label=label,kind='money')
    rate.update(id='shareholder_payout',label='총주주환원율',kind='percent',definition='(보통주 현금배당 지급액 + 실제 자사주 매입액) ÷ 같은 기간 순이익',
                denominator_scope='보통주 귀속 우선, 지배주주 순이익 차선',source_type='calculated')
    metrics['shareholder_payout']=rate


def apply_explicit_zeros(source,annual,quarter,currency):
    evidence=list(source.get('explicit_distribution_zeros',[]))
    # A reported gross payment of zero over a containing period proves zero
    # in its subperiods. This rule never uses net flows or a missing value.
    for tag,fields in [('PaymentsForRepurchaseOfCommonStock',['buybacks']),('PaymentsOfDividendsCommonStock',['cash_dividends','dividend'])]:
        for r in source.get('facts',{}).get('facts',{}).get('us-gaap',{}).get(tag,{}).get('units',{}).get(currency,[]):
            if r.get('val')==0 and r.get('start') and r.get('filed','')<=__import__('datetime').date.today().isoformat():
                evidence.append(dict(start=r['start'],end=r['end'],fields=fields,filed=r['filed'],accession=r['accn'],source_id='companyfacts.json',
                    formula='상위 기간의 공시 총지급액 0 확인',evidence=dict(rule='reported_zero_gross_payment',tag=tag,original=r)))
    for e in sorted(evidence,key=lambda e:e['filed'],reverse=True):
        for target in (annual,quarter):
            for end,values in target.items():
                anchor=next((values[k] for k in ('sales','income','net_income') if values.get(k,{}).get('start')),None)
                if not anchor or not e['start']<=anchor['start']<=end<=e['end']:continue
                for key in e['fields']:
                    if values.get(key,{}).get('value') is not None:continue
                    values[key]=fact(0,unit=currency+'/share' if key=='dividend' else currency,start=anchor['start'],end=end,
                        filed=e['filed'],accession=e['accession'],sources=[e['source_id']],confirmed_zero=True,
                        formula=e.get('formula','공시에서 해당 기간 지급/매입 없음 명시'),evidence=e['evidence'],**({'dividend_basis':'paid'} if key=='dividend' else {}))
