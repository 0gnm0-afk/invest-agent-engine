"""Annual DART dividend-table supplements, with original response columns intact.

The dividend table's ordinary '(연결)주당순이익' does not distinguish basic
from diluted EPS. Keep it as reported_eps; never relabel it as either one.
The collector only persists raw responses. Candidate normalization is pure.
"""
import math
import re
from datetime import date


GUIDE_URL = 'https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS002&apiId=2019005'
EPS_LABELS = {'(연결)주당순이익(원)', '(연결)주당순손실(원)', '(연결)주당순이익(손실)(원)'}
DPS_LABELS = {'주당현금배당금(원)'}
COMMON_STOCK = {'보통주', '보통주식', '의결권있는보통주', '의결권이있는보통주',
                '의결권있는보통주식', '의결권이있는보통주식'}
EPS_TAGS = {'ifrs-full_BasicEarningsLossPerShare', 'ifrs-full_DilutedEarningsLossPerShare'}
EPS_ACCOUNTS = {'기본주당이익', '기본주당이익(손실)', '희석주당이익', '희석주당이익(손실)'}


def _label(value):
    return re.sub(r'\s+', '', str(value or ''))


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip().replace(',', '')
    if not re.fullmatch(r'(?:[+-]?\d+(?:\.\d+)?|\(\d+(?:\.\d+)?\))', text):
        return None
    result = float(text.replace('(', '-').replace(')', ''))
    return result if math.isfinite(result) else None


def only_common_stock(source,year):
    reports=[r for r in source.get('reports',[]) if r.get('year')==year and r.get('quarter')==4 and r.get('shares',{}).get('status')=='000']
    for report in reports:
        rows=report['shares'].get('list',[])
        if any(str(r.get('corp_code'))!=str(source['identity']['corp_code']) for r in rows):continue
        common=[r for r in rows if _label(r.get('se')) in COMMON_STOCK]
        totals=[r for r in rows if _label(r.get('se'))=='합계']
        if len(common)!=1 or len(totals)!=1:continue
        a,b=[_number(r.get('istc_totqy')) for r in [common[0],totals[0]]]
        if a is not None and a>0 and a==b:return report.get('shares_source_id') or report['prefix']+'-shares.json'
    return None


def annual_supplement_candidates(source, *, as_of=None):
    """Return all valid candidates, including old filings and conflicting rows.

    Caller selects the latest filing, detects ties, and retains replaced facts.
    No input mutation, zero filling, CFS/OFS fallback or EPS-basis inference.
    """
    cutoff = (as_of or date.today()).isoformat()
    corp = str(source['identity']['corp_code'])
    output = []
    for report in source.get('annual_supplements', []):
        if report.get('endpoint') != 'alotMatter' or report.get('quarter') != 4:
            continue
        year = report.get('year')
        if not isinstance(year, int) or isinstance(year, bool) or not 2015 <= year <= 9999:
            continue
        end = f'{year}-12-31'
        if report.get('end') != end or end >= cutoff:
            continue
        raw = report.get('raw', {})
        if raw.get('status') != '000':
            continue
        rows = raw.get('list', [])
        if any(str(row.get('corp_code')) != corp for row in rows):
            raise ValueError('DART_SUPPLEMENT_IDENTITY_MISMATCH')
        source_id = report.get('source_id') or f'{year}-Q4-dividends.json'
        for row in rows:
            if row.get('stlm_dt') != end:
                continue
            label = _label(row.get('se'))
            stock = _label(row.get('stock_knd'))
            if label in EPS_LABELS and stock in COMMON_STOCK | {'', '-'}:
                key = 'reported_eps'
            elif label in DPS_LABELS and stock in COMMON_STOCK | {'','-'}:
                key = 'dividend'
            else:
                continue
            receipt = str(row.get('rcept_no') or '')
            if not re.fullmatch(r'\d{14}', receipt):
                continue
            filed = receipt[:4] + '-' + receipt[4:6] + '-' + receipt[6:8]
            try:
                date.fromisoformat(filed)
            except ValueError:
                continue
            if filed > cutoff or filed <= end:
                continue
            for offset, column in enumerate(('thstrm', 'frmtrm', 'lwfr')):
                value = _number(row.get(column))
                fy = year - offset
                stock_source=only_common_stock(source,fy) if key=='dividend' else None
                confirmed_zero=False
                if key=='dividend':
                    if stock not in COMMON_STOCK and not stock_source:continue
                    dividend_rows=[x for x in rows if _label(x.get('se')) in DPS_LABELS]
                    numeric={_number(x.get(column)) for x in dividend_rows}-{None}
                    cash_totals=[x for x in rows if _label(x.get('se'))=='현금배당금총액(백만원)']
                    # Exact reported dashes in BOTH applicable rows, with a
                    # separately verified sole common class, denote no dividend.
                    if value is None and str(row.get(column)).strip()=='-' and stock_source and not numeric and cash_totals and all(str(x.get(column)).strip()=='-' for x in cash_totals):
                        value=0;confirmed_zero=True
                    if stock not in COMMON_STOCK and value is not None and numeric and numeric!={value}:continue
                if value is None:continue
                comparison_note = ('배당에 관한 사항의 연간 공시값. 서로 다른 제출본은 회계정책 변경·'
                                   '과거 비교수치 재작성·주식수 조정 기준이 다를 수 있습니다.')
                item = dict(key=key, fy=fy, value=value, unit='KRW/share',
                            start=f'{fy}-01-01', end=f'{fy}-12-31', filed=filed,
                            sources=[source_id]+([stock_source] if stock_source else []), receipt=receipt, account=row.get('se'),
                            tag='alotMatter.' + column, reported_value=row[column],
                            report_year=year, report_column=column, source_endpoint='alotMatter',
                            stock_class=row.get('stock_knd'), period_type='annual',
                            comparative=offset > 0, formula='사업보고서 배당에 관한 사항 · 연간 공시 원값',
                            comparison_note=comparison_note, accounting_basis_verified=False,
                            url='https://dart.fss.or.kr/dsaf001/main.do?rcpNo=' + receipt)
                if key == 'reported_eps':
                    item.update(eps_basis='reported', scope='consolidated',
                                eps_basis_note='연결 공시 EPS · 이 표에서 기본/희석 구분을 제공하지 않음')
                else:
                    item.update(dividend_basis='annual_common_cash', scope='common_stock',
                                dividend_basis_note='해당 사업연도 보통주 주당 현금배당금 · 최근 12개월 배당과 구분')
                if key=='dividend':item.update(confirmed_zero=confirmed_zero,stock_class_evidence=stock_source)
                output.append(item)
    return output


def _available(source, *, as_of):
    """Coverage for request planning only; the model owns fact selection."""
    values = {}
    for candidate in annual_supplement_candidates(source, as_of=as_of):
        field = 'eps' if candidate['key'] == 'reported_eps' else 'dividend'
        rank = (candidate['filed'], candidate['receipt'])
        slot = (candidate['fy'], field)
        previous = values.get(slot)
        if previous is None or rank > previous[0]:
            values[slot] = (rank, {candidate['value']})
        elif rank == previous[0]:
            previous[1].add(candidate['value'])
    available = {slot for slot, (_, candidates) in values.items() if len(candidates) == 1}
    # Full statements already have explicitly identified basic/diluted EPS.
    # Comparative annual columns can cover years lacking their own API response.
    for report in source.get('reports', []):
        if report.get('quarter') != 4 or report.get('fs', {}).get('status') != '000':
            continue
        year = report['year']
        rows = [row for row in report['fs'].get('list', [])
                if row.get('sj_div') in ('IS', 'CIS') and row.get('currency') == 'KRW'
                and (row.get('account_id') in EPS_TAGS or _label(row.get('account_nm')) in EPS_ACCOUNTS)]
        for offset, column in enumerate(('thstrm_amount', 'frmtrm_amount', 'bfefrmtrm_amount')):
            candidates = {_number(row.get(column)) for row in rows}
            candidates.discard(None)
            if candidates:
                available.add((year - offset, 'eps'))
    return available


def annual_supplement_years(source, *, as_of=None):
    """Six annual observations ending with the latest available annual report."""
    today = as_of or date.today()
    years = [r['year'] for r in source.get('reports', [])
             if r.get('quarter') == 4 and r.get('end', '9999') < today.isoformat()
             and (r.get('fs', {}).get('status') == '000' or r.get('shares', {}).get('status') == '000')]
    years.extend(r['year'] for r in source.get('annual_supplements', [])
                 if r.get('quarter') == 4 and r.get('raw', {}).get('status') == '000'
                 and r.get('end', '9999') < today.isoformat())
    latest = max(years, default=today.year - 1)
    return list(range(max(2015, latest - 5), latest + 1))


def collect_annual_supplements(collector, source, *, years=None, as_of=None):
    """Selected-company, missing-year collection; reuse any supplied raw inputs.

    A recent report supplies up to three annual columns, so covered years are
    skipped. An optional-source failure leaves core statements usable and has a
    sanitized diagnostic; no all-company scan or background fetch is performed.
    """
    from financial_sources import SourceError
    today = as_of or date.today()
    if source.get('provider') != 'DART':
        return source
    identity = source['identity']
    if identity.get('acc_mt', '12') != '12':
        raise ValueError('DART_NON_DECEMBER_YEAR_REQUIRES_PERIOD_MAPPING')
    targets = set(annual_supplement_years(source, as_of=today) if years is None else years)
    if any(not isinstance(y, int) or isinstance(y, bool) or not 2015 <= y < today.year for y in targets):
        raise ValueError('DART_SUPPLEMENT_INVALID_YEAR')
    result = dict(source, annual_supplements=list(source.get('annual_supplements', [])),
                  annual_supplement_errors=list(source.get('annual_supplement_errors', [])))
    attempted = {r.get('year') for r in result['annual_supplements'] if r.get('endpoint') == 'alotMatter'}
    for year in sorted(targets, reverse=True):
        available = _available(result, as_of=today)
        if all((year, field) in available for field in ('eps', 'dividend')) or year in attempted:
            continue
        name = f'{year}-Q4-dividends.json'
        try:
            raw = collector.dart('alotMatter', name, corp_code=identity['corp_code'],
                                 bsns_year=str(year), reprt_code='11011')
        except SourceError as exc:
            result['annual_supplement_errors'].append(dict(year=year, source_id=name, error=str(exc)))
            continue
        result['annual_supplements'].append(dict(year=year, quarter=4, end=f'{year}-12-31',
                                                prefix=f'{year}-Q4', endpoint='alotMatter', source_id=name, raw=raw))
        if raw.get('status') == '013':
            result['annual_supplement_errors'].append(dict(
                year=year, source_id=name, error='DART_013',
                reason='해당 회사·사업연도 배당사항 조회 결과 없음. EPS/DPS를 0으로 대체하지 않음'))
        # Validate company identity immediately, before another request is made.
        annual_supplement_candidates(result, as_of=today)
    available = _available(result, as_of=today)
    result['annual_supplement_missing'] = [
        dict(year=year, fields=[field for field in ('eps', 'dividend') if (year, field) not in available],
             reason='유효한 연결 EPS 또는 보통주 연간 현금배당 공시값 미확보')
        for year in sorted(targets) if any((year, field) not in available for field in ('eps', 'dividend'))]
    return result
