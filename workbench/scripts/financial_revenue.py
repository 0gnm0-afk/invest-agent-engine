"""Conservative revenue selection with an explicit comparable accounting basis.

DART: https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS003&apiId=2019020
SEC: https://www.sec.gov/search-filings/edgar-application-programming-interfaces
US-GAAP duration concepts: https://xbrl.fasb.org/us-gaap/2025/elts/us-gaap-2025.xsd
IBK's statement separates total operating income, impairment and net operating
income (2022 Annual Report, p. 114):
https://research.ibk.co.kr/fup/research/magazine/yearly/2023051011114457756349411159458.pdf

Do not use a component (interest/fee income), an arbitrary substring match, or
post-impairment operating profit as revenue. Callers must propagate metadata
through annual-minus-YTD, TTM and ratios, and compare only equal comparison_key.
"""
import math
import re
from datetime import date


_META = ('basis', 'account_label', 'comparison_key', 'revenue_note', 'revenue_components')
_REPORTED = dict(basis='reported_revenue', account_label='매출', comparison_key='reported_revenue',
                 revenue_note='공시 매출/영업수익 총액')
_BANK = dict(basis='bank_net_revenue', account_label='매출(순영업수익)', comparison_key='bank_net_revenue',
             revenue_note='금융업 공시 순영업수익 총액. 이자비용 등을 차감한 기준으로 일반기업 매출과 다름')
_BANK_SUM = dict(_BANK, comparison_key='bank_net_interest_plus_noninterest',
                 revenue_note='같은 공시·기간의 순이자손익 + 비이자손익. 일반기업 매출과 다름')
_INSURANCE = dict(basis='insurance_revenue', account_label='매출(보험수익)', comparison_key='insurance_revenue',
                  revenue_note='공시 보험수익 기준. 투자손익 등과 합산한 총영업수익을 뜻하지 않음')


def revenue_metadata(selection):
    """Metadata safe to spread into a normalized fact; never copies its value."""
    return {key: selection[key] for key in _META if key in selection}


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(str(value).replace(',', '').replace('(', '-').replace(')', ''))
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _name(value):
    # Whitespace varies across DART filers; meaningful words are never stripped.
    return re.sub(r'\s+', '', str(value or ''))


def _dart_pick(rows, tags=(), names=()):
    """First explicit alias, rejecting conflicts in both quarterly and YTD cells."""
    for field, aliases in (('account_id', tags), ('account_nm', names)):
        for alias in aliases:
            matches = [row for row in rows if _name(row.get(field)) == _name(alias)]
            if not matches:
                continue
            signatures = {(_number(row.get('thstrm_amount')), _number(row.get('thstrm_add_amount')),
                           row.get('rcept_no'), row.get('corp_code'), row.get('bsns_year'), row.get('reprt_code'))
                          for row in matches}
            if len(signatures) != 1:
                return None, '동일 매출 계정의 중복 공시값/기간이 충돌함'
            if _number(matches[0].get('thstrm_amount')) is None:
                return None, '선택된 매출 계정의 당기값 미확보'
            return dict(matches[0]), None
    return None, None


def select_dart_revenue(rows, currency='KRW'):
    """Return {row, basis, account_label, comparison_key, reason}; row may be None.

    Input must be one company's one consolidated/separate report, as returned
    by fnlttSinglAcntAll. InsuranceRevenue is never selected for a bank merely
    because its consolidated insurance subsidiary reports that component.
    """
    rows = [r for r in rows if r.get('sj_div') in ('IS', 'CIS') and r.get('currency') == currency]
    row, reason = _dart_pick(rows, ('ifrs-full_Revenue', 'ifrs_Revenue'),
                            ('매출액', '수익(매출액)', '매출', '영업수익', '총영업수익', '영업수익합계', '총수익', '수익'))
    if row or reason:
        return dict(row=row, reason=reason, **_REPORTED)
    bank = any(r.get('account_id') in ('ifrs-full_InterestRevenueExpense', 'ifrs_InterestRevenueExpense')
               or _name(r.get('account_nm')) in ('순이자손익', '순이자이익', '순이자수익') for r in rows)
    if bank:
        row, reason = _dart_pick(rows, names=('총영업이익', '순영업수익', '총순영업수익', '순영업수익합계'))
        if row or reason:
            return dict(row=row, reason=reason, **_BANK)
        interest, interest_reason = _dart_pick(rows, ('ifrs-full_InterestRevenueExpense', 'ifrs_InterestRevenueExpense'),
                                               ('순이자손익', '순이자이익', '순이자수익'))
        noninterest, noninterest_reason = _dart_pick(rows, names=('비이자손익', '비이자이익', '비이자수익합계'))
        if interest_reason or noninterest_reason:
            return dict(row=None, reason=interest_reason or noninterest_reason, **_BANK_SUM)
        if interest and noninterest:
            context = ('rcept_no', 'corp_code', 'bsns_year', 'reprt_code', 'currency')
            if any(interest.get(k) != noninterest.get(k) for k in context):
                return dict(row=None, reason='순이자/비이자손익의 공시·기간 불일치', **_BANK_SUM)
            row = dict(interest, account_id='derived_BankNetRevenue', account_nm='순이자손익 + 비이자손익')
            for field in ('thstrm_amount', 'thstrm_add_amount'):
                a, b = _number(interest.get(field)), _number(noninterest.get(field))
                row[field] = a + b if a is not None and b is not None else None
            components = [dict(account=r.get('account_nm'), tag=r.get('account_id'),
                               value=_number(r.get('thstrm_amount')), cumulative_value=_number(r.get('thstrm_add_amount')))
                          for r in (interest, noninterest)]
            return dict(row=row, reason=None, revenue_components=components, **_BANK_SUM)
        return dict(row=None, reason='금융업 총수익/순영업수익 미확보; 단일 이자·수수료·보험수익은 전체 매출로 채택하지 않음', **_BANK)
    row, reason = _dart_pick(rows, ('ifrs-full_InsuranceRevenue', 'ifrs_InsuranceRevenue'), ('보험수익',))
    return dict(row=row, reason=reason or (None if row else '공시 매출 총액에 해당하는 명확한 계정 미확보'),
                **(_INSURANCE if row or reason else _REPORTED))


def _sec_rows(taxonomy, tag, cutoff, as_of, unit):
    rows = []
    for row in taxonomy.get(tag, {}).get('units', {}).get(unit, []):
        if (row.get('form') not in ('10-K', '10-Q', '10-K/A', '10-Q/A', '20-F', '20-F/A', '6-K', '6-K/A', '8-K')
                or not row.get('start') or not row.get('end') or not row.get('filed')
                or row['end'] < cutoff or row['filed'] > as_of or _number(row.get('val')) is None):
            continue
        try:
            if date.fromisoformat(row['start']) > date.fromisoformat(row['end']):
                continue
        except (ValueError, TypeError):
            continue
        rows.append(dict(row, tag=tag))
    return rows


def _sec_latest(rows):
    """One fact per exact span; a latest-filing conflict is missing, not first-row wins."""
    spans = {}
    for row in rows:
        spans.setdefault((row['start'], row['end']), []).append(row)
    result = {}
    for span, values in spans.items():
        rank = max((r['filed'], r.get('accn', '')) for r in values)
        latest = [r for r in values if (r['filed'], r.get('accn', '')) == rank]
        if len({_number(r['val']) for r in latest}) == 1:
            result[span] = latest[0]
        else:
            result[span] = dict(latest[0], val=None, reason='동일 최신 공시의 계정값 충돌',
                                conflicting_values=sorted({_number(r['val']) for r in latest}))
    return result


def select_sec_revenue(taxonomy, *, cutoff, as_of, unit='USD'):
    """Return SEC duration facts with revenue metadata, deduplicated by exact span.

    Total revenue takes precedence over revenue from customer contracts, which
    can exclude interest, leases and insurance. Filing recency only ranks facts
    within a comparable total. A banking tag does not make every total net.
    """
    ordinary_tags = ('Revenues', 'SalesRevenueNet', 'RevenueFromContractWithCustomerExcludingAssessedTax')
    net_tag = 'RevenuesNetOfInterestExpense'
    banking = bool(taxonomy.get('InterestIncomeExpenseNet') or taxonomy.get(net_tag))
    # A filer may migrate a total to the customer-contract concept and later
    # restate that same period using only the new concept. Prove equivalence
    # from the latest filing that published BOTH values for that exact span;
    # never infer it from the issuer name or from equal values in another year.
    contract_tag=ordinary_tags[-1]
    paired={}
    contract_rows=_sec_rows(taxonomy,contract_tag,cutoff,as_of,unit)
    for total_tag in ordinary_tags[:2]:
        total_rows=_sec_rows(taxonomy,total_tag,cutoff,as_of,unit)
        for total in total_rows:
            matches=[r for r in contract_rows if (r['start'],r['end'],r['filed'],r.get('accn'))==
                     (total['start'],total['end'],total['filed'],total.get('accn')) and r.get('accn')]
            if not matches:continue
            span=(total['start'],total['end']);rank=(total['filed'],total.get('accn',''))
            equal=all(_number(r['val'])==_number(total['val']) for r in matches)
            if span not in paired or rank>paired[span][0]:paired[span]=(rank,equal)
            elif rank==paired[span][0]:paired[span]=(rank,paired[span][1] and equal)
    ordinary = {}
    for priority, tag in enumerate(ordinary_tags):
        for span, row in _sec_latest(_sec_rows(taxonomy, tag, cutoff, as_of, unit)).items():
            same_total=paired.get(span,(None,False))[1]
            rank = (priority < 2 or same_total, row['filed'], row.get('accn', ''), -priority)
            if span not in ordinary or rank > ordinary[span][0]:
                metadata=dict(_REPORTED)
                if priority==2 and same_total:
                    metadata['revenue_note']='동일 기간·동일 공시에서 총수익과 일치한 계정의 최신 제출값'
                ordinary[span] = (rank, dict(row, **metadata))
    # This is a separate valid standard total-revenue concept. Do not merge its
    # tax basis into an excluding-tax series or replace a stronger total.
    for span,row in _sec_latest(_sec_rows(taxonomy,'RevenueFromContractWithCustomerIncludingAssessedTax',cutoff,as_of,unit)).items():
        if span not in ordinary:
            ordinary[span]=((),dict(row,**dict(_REPORTED,comparison_key='reported_revenue_including_assessed_tax',
                revenue_note='세금 포함 기준의 공시 고객계약 매출')))
    if not banking:
        return [ordinary[k][1] for k in sorted(ordinary)]

    totals = _sec_latest(_sec_rows(taxonomy, 'Revenues', cutoff, as_of, unit))
    net = _sec_latest(_sec_rows(taxonomy, net_tag, cutoff, as_of, unit))
    interest = _sec_latest(_sec_rows(taxonomy, 'InterestIncomeExpenseNet', cutoff, as_of, unit))
    noninterest = _sec_latest(_sec_rows(taxonomy, 'NoninterestIncome', cutoff, as_of, unit))
    result = {}
    for span, row in totals.items():
        alias = net.get(span)
        same_net = (alias and row.get('val') is not None and row.get('val') == alias.get('val')
                    and row.get('accn') and (row['filed'], row['accn']) == (alias['filed'], alias.get('accn')))
        result[span] = dict(row, **(_BANK if same_net else _REPORTED))
    for span, row in net.items():
        result.setdefault(span, dict(row, **_BANK))
    for span in interest.keys() & noninterest.keys():
        if span in result:
            continue
        a, b = interest[span], noninterest[span]
        if a.get('val') is None or b.get('val') is None or not a.get('accn') or (a['filed'], a['accn']) != (b['filed'], b.get('accn')):
            continue
        components = [dict(tag=r['tag'], value=_number(r['val']), accession=r.get('accn')) for r in (a, b)]
        result[span] = dict(a, tag='derived_BankNetRevenue', val=_number(a['val']) + _number(b['val']),
                            revenue_components=components, **_BANK_SUM)
    return [result[k] for k in sorted(result)]
