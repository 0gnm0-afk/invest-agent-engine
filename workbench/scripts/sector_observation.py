"""Offline sector observations. No collector, account, candidate, or network calls.

See 20_MVP/섹터지표_정의_2026-09-25.md for calculation and coverage contracts.
"""
import argparse
import csv
import hashlib
import html
import json
import math
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup
from master_parser import parse_master
from finviz_public_classification import load_public, select_classification
from wics_classification import load_wics, select_wics
from sector_basis import load_basis, series_basis, comparison_issue
from sector_external_review import load_reviews, review_category

PROJECT = Path(__file__).resolve().parents[1]
SECTORS = ('Basic Materials', 'Communication Services', 'Consumer Cyclical',
           'Consumer Defensive', 'Energy', 'Financial', 'Healthcare', 'Industrials',
           'Real Estate', 'Technology', 'Utilities')
BENCHMARKS = {'KR': '^KS11', 'US': '^GSPC'}
EXCHANGES = {'KOSPI': 'KR', 'NASDAQ': 'US', 'NYSE': 'US'}


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def stamp(value):
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        raise ValueError('timezone_required')
    return dt


def age_issue(observed, applied):
    age = (stamp(applied) - stamp(observed)).total_seconds() / 86400
    return 'future_observation' if age < 0 else 'stale_classification' if age > 7 else None


def classification_age_issue(observed, applied):
    """Direct taxonomy evidence does not expire; impossible dates still block."""
    try:
        return 'future_observation' if stamp(observed) > stamp(applied) else None
    except (TypeError, ValueError):
        return 'invalid_classification_timestamp'


def classification_freshness(records, applied):
    dates = [d for r in records for d in
             [r.get('observed_at'), *r.get('context_observed_at', []),
              r['classification_as_of']+'T00:00:00+09:00' if r.get('classification_as_of') else None] if d]
    observed = [r['observed_at'] for r in records if r.get('observed_at')]
    as_of = [r['classification_as_of'] for r in records if r.get('classification_as_of')]
    invalid = any(classification_age_issue(d, applied) for d in dates)
    age = max(((stamp(applied)-stamp(d)).total_seconds()/86400 for d in dates), default=None) if not invalid else None
    return dict(state='unverified' if invalid or age is None else 'refresh_due' if age > 7 else 'recent',
                oldest_observed_at=min(observed, key=stamp) if observed and not invalid else None,
                latest_observed_at=max(observed, key=stamp) if observed and not invalid else None,
                source_as_of=min(as_of) if as_of else None, max_age_days=age, evaluated_at=applied)


def checked(path, expected, inputs):
    path = Path(path).resolve()
    actual = sha(path)
    if expected is not None and actual != expected:
        raise ValueError('input_hash_mismatch:' + str(path))
    inputs[str(path)] = actual
    return path


def finviz_classification(data, symbol):
    soup = BeautifulSoup(data, 'html.parser')
    title = soup.title.get_text() if soup.title else ''
    if not title.startswith(symbol + ' - '):
        return None, 'finviz_symbol_unconfirmed'
    sectors, exchanges = set(), set()
    for a in soup.select('a[href]'):
        filters = parse_qs(urlparse(a['href']).query).get('f', [''])[0].split(',')
        if any(f.startswith('sec_') for f in filters):
            sectors.add(a.get_text(' ', strip=True))
        for f in filters:
            if f.startswith('exch_'):
                exchanges.add({'exch_nasd': 'NASDAQ', 'exch_nyse': 'NYSE'}.get(f, f))
    if len(sectors) != 1 or len(exchanges) != 1 or not sectors.issubset(SECTORS):
        return None, 'finviz_classification_or_exchange_conflict'
    return {'group_name': sectors.pop(), 'exchange': exchanges.pop()}, None


def verified_parent(entry, symbol, records, probes, applied):
    """A per-stock direct observation can establish a parent, never name the unknown middle."""
    if len(records)!=1:
        return None, 'nh_individual_absent_or_duplicate'
    _,record=records[0]
    value=record['payload'].get('Output_0',{})
    parent=entry['sUpCodeL']
    if (record.get('url')!='https://api.nhplug.com:8443/krstock/quote/v1/currentPrice' or
            record.get('inputs')!={'market_cd':'KRX','iem_cd':symbol} or value.get('iem_cd')!=symbol):
        return None,'nh_individual_identity_conflict'
    issue=age_issue(record['observed_at'],applied)
    if issue:return None,issue
    if not parent or not parent.startswith('KGS') or value.get('bstp_cls_code')!=parent:
        return None,'nh_individual_parent_not_confirmed'
    label=value.get('bstp_kor_isnm')
    if not isinstance(label,str) or not label.startswith('코스피 '):
        return None,'nh_individual_label_missing'
    if not probes:return None,'nh_parent_label_not_verified'
    for _,probe in probes:
        v=probe['payload'].get('Output_0',{})
        if (probe.get('expected_code')!=parent or v.get('bstp_cls_code')!=parent or
                v.get('bstp_kor_isnm')!=label or v.get('iem_cd')!=probe['inputs']['iem_cd'] or
                probe['inputs'].get('market_cd')!='KRX'):
            return None,'nh_parent_label_conflict'
        issue=age_issue(probe['observed_at'],applied)
        if issue:return None,issue
    return {'code':parent,'label':label},None


def group_rows(original, evidence, nh_root, applied, inputs):
    """Preserve source rows verbatim; derive a separate, evidence-backed group."""
    nh_manifest = load(checked(nh_root / 'sources-manifest.json', None, inputs))
    files = {Path(x['path']).name: x for x in nh_manifest}
    for name in ('m_new_stock.h', 'm_new_stock.mst'):
        checked(nh_root / files[name]['path'], files[name]['sha256'], inputs)
    master = parse_master((nh_root / files['m_new_stock.mst']['path']).read_bytes(),
                          (nh_root / files['m_new_stock.h']['path']).read_text(encoding='utf-8-sig'))
    by_symbol = defaultdict(list)
    for row in master:
        if row['sMarket'] == '1':
            by_symbol[row['sCode']].append(row)
    code_probes = defaultdict(list)
    for path in sorted((evidence / 'kr-probes').glob('*.json')):
        value = load(checked(path, None, inputs))
        code_probes[value['expected_code']].append((path, value))
    individual = defaultdict(list)
    for path in sorted((evidence / 'kr-individual').glob('*.json')):
        value = load(checked(path, None, inputs))
        individual[value['inputs']['iem_cd']].append((path,value))
    us = {}
    for path in sorted((evidence / 'sources').glob('finviz-*.html')):
        if path.stem == 'finviz-map':
            continue
        meta_path = checked(path.with_suffix('.json'), None, inputs)
        meta = load(meta_path)
        checked(path, meta['sha256'], inputs)
        symbol = path.stem.removeprefix('finviz-')
        value, reason = finviz_classification(path.read_bytes(), symbol)
        us[symbol] = (value, reason, {**meta, 'path': str(path.resolve()),
                                    'identity_method': 'exact_ticker_and_published_exchange_no_isin'})
    public = load_public(evidence, checked, load, inputs, SECTORS)
    wics = load_wics(evidence, checked, load, inputs)
    external_reviews = load_reviews(evidence, checked, load, inputs, applied, classification_age_issue)
    symbol_counts = Counter(r['provider_symbol'] for r in original if r['market']=='US')
    result = []
    for original_row in original:
        market, symbol = original_row['market'], original_row['provider_symbol']
        row = {'market': market, 'exchange': original_row['exchange'], 'symbol': original_row['symbol'],
               'provider_symbol': symbol, 'isin_code': original_row['isin_code'],
               'name': original_row['name'], 'original_classification': original_row,
               'group_taxonomy': ('KR_WICS_SECTOR' if wics else 'KR_NH_KOSPI_INDUSTRY') if market == 'KR' else 'US_FINVIZ_SECTOR',
               'group_code': None, 'group_name': None, 'group_state': 'unmatched',
               'reason': None, 'evidence': [], 'applied_at': applied,
               'taxonomy_version': None, 'source_effective_from': None,
               'historical_classification_available': False, 'mapping_version': 'sector-observation-v1'}
        result.append(row)
        if market == 'US' and symbol in external_reviews:
            row['external_review'] = external_reviews[symbol]
            sector, issue, proofs = select_classification(public.get(symbol, []), us.get(symbol),
                row['exchange'], symbol_counts[symbol] == 1, applied, classification_age_issue, classification_only=True)
            row['external_finviz_sector'] = sector if issue is None else None
            row['external_finviz_issue'] = issue
            row['external_finviz_evidence'] = proofs
        if original_row['state'] == 'conflict':
            row['reason'] = 'original_conflict:' + original_row['reason']
            continue
        if market == 'US':
            sector, reason, proofs = select_classification(public.get(symbol,[]),us.get(symbol),
                row['exchange'],symbol_counts[symbol]==1,applied,classification_age_issue)
            row['evidence'] = proofs
            if public:
                row['mapping_version'] = 'sector-observation-v2-finviz-public'
            elif reason == 'finviz_public_evidence_absent':
                reason = 'finviz_direct_evidence_not_acquired'
            if reason:
                row['reason'] = reason
                continue
            row.update(group_code=sector, group_name=sector, group_state='matched')
        else:
            candidates = by_symbol[symbol]
            if wics:
                row['mapping_version'] = 'sector-observation-v5-wics-company-evidence'
                if len(candidates) == 1:
                    row['nh_master_codes'] = {k: candidates[0][k] for k in ('sUpCodeM', 'sUpCodeL', 'sUpCodeS')}
                proofs = wics['records'].get(symbol, [])
                row['evidence'] = proofs
                selected, reason = select_wics(proofs, applied, classification_age_issue)
                if reason:
                    row['reason'] = reason
                else:
                    row.update(group_code=selected['code'], group_name=selected['name'], group_state='matched',
                               classification_as_of=selected['classification_as_of'])
                continue
            if len(candidates) != 1:
                row['reason'] = 'nh_domestic_identity_absent_or_duplicate'
                continue
            entry = candidates[0]
            code = entry['sUpCodeM'] or entry['sUpCodeL']
            row['nh_master_codes'] = {k: entry[k] for k in ('sUpCodeM', 'sUpCodeL', 'sUpCodeS')}
            row['evidence'] = [{**files['m_new_stock.mst'], 'identity_method': 'KOSPI_exact_short_code_no_isin',
                                'symbol': symbol, 'source_name': entry['sKorName']}]
            reason = age_issue(files['m_new_stock.mst']['fetched_at'], applied)
            probes = code_probes[code]
            labels = set()
            if not probes:
                reason = reason or 'nh_code_name_not_verified'
            for path, probe in probes:
                value = probe['payload'].get('Output_0', {})
                row['evidence'].append({'url': probe['url'], 'path': str(path.resolve()),
                                        'sha256': sha(path), 'observed_at': probe['observed_at'],
                                        'probe_symbol': probe['inputs']['iem_cd'],
                                        'published_code': value.get('bstp_cls_code'),
                                        'published_name': value.get('bstp_kor_isnm')})
                if (value.get('iem_cd') != probe['inputs']['iem_cd'] or
                        probe['inputs'].get('market_cd') != 'KRX'):
                    reason = 'nh_probe_identity_conflict'
                elif value.get('bstp_cls_code') != code:
                    reason = 'nh_master_quote_code_conflict'
                label = value.get('bstp_kor_isnm')
                if not isinstance(label, str) or not label.startswith('코스피 '):
                    reason = reason or 'nh_label_missing_or_market_conflict'
                else:
                    labels.add(label)
                reason = reason or age_issue(probe['observed_at'], applied)
            if len(labels) != 1:
                reason = reason or 'nh_label_missing_or_conflict'
            if reason in ('nh_master_quote_code_conflict','nh_code_name_not_verified') and individual[symbol]:
                parent,issue=verified_parent(entry,symbol,individual[symbol],code_probes[entry['sUpCodeL']],applied)
                row['individual_review']={'parent_confirmed':parent is not None,'issue':issue,
                                          'original_mapping_issue':reason}
                for path,value in individual[symbol]:
                    row['evidence'].append({'path':str(path.resolve()),'sha256':sha(path),
                        'url':value['url'],'observed_at':value['observed_at'],
                        'published_code':value['payload'].get('Output_0',{}).get('bstp_cls_code'),
                        'published_name':value['payload'].get('Output_0',{}).get('bstp_kor_isnm'),
                        'identity_method':'individual_exact_KRX_ticker_no_isin'})
                # A newer quote does not make a stale master usable.
                if parent and not age_issue(files['m_new_stock.mst']['fetched_at'],applied):
                    code=parent['code'];labels={parent['label']};reason=None
                    row['mapping_version']='sector-observation-v3-verified-parent'
                    row['warnings']=['nh_middle_label_unverified_using_individually_verified_parent']
                    for path,probe in code_probes[code]:
                        row['evidence'].append({'path':str(path.resolve()),'sha256':sha(path),
                            'url':probe['url'],'observed_at':probe['observed_at'],
                            'published_code':code,'published_name':parent['label']})
            if reason:
                row['reason'] = reason
                continue
            row.update(group_code=code, group_name=labels.pop(), group_state='matched')
    for row in result:
        if row['group_taxonomy'] in ('KR_WICS_SECTOR', 'US_FINVIZ_SECTOR'):
            row['classification_freshness'] = classification_freshness(row['evidence'], applied)
    return result


def number(value, positive=False):
    return (not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
            and (value > 0 if positive else value >= 0))


def bar_index(series, sessions):
    if series is None:
        return {}, 'series_missing'
    bars = series.get('bars', [])
    dates = [b['date'] for b in bars]
    if dates != sorted(set(dates)):
        return {}, 'duplicate_or_unsorted_dates'
    session_set = set(sessions)
    if any(d not in session_set for d in dates):
        return {}, 'bar_outside_market_sessions'
    return {b['date']: b for b in bars}, None


def window(index, sessions, count, field='close'):
    if len(sessions) < count:
        return None, 'insufficient_market_sessions'
    dates = sessions[-count:]
    if any(d not in index for d in dates):
        return None, 'missing_session_or_short_history'
    values = [index[d].get(field) for d in dates]
    if not all(number(v, positive=field == 'close') for v in values):
        return None, 'invalid_' + field
    return values, None


def equal_weight_index(prices):
    if not prices:
        return None
    result = [100.0]
    for i in range(1, len(prices[0])):
        daily = statistics.fmean(p[i] / p[i - 1] - 1 for p in prices)
        result.append(result[-1] * (1 + daily))
    return result


def metric_counts(used, excluded):
    return {'used': len(used), 'excluded': len(excluded), 'members': used,
            'exclusions': excluded, 'small_sample': len(used) < 5}


def group_metrics(members, series, sessions, benchmark, *, validate_basis=False, reference=None):
    indexes = {r['symbol']: bar_index(series.get((r['market'], r['symbol'])), sessions) for r in members}
    bases = {}
    if validate_basis:
        for member in members:
            symbol = member['symbol']
            basis, issue = series_basis(series.get((member['market'], symbol)))
            if basis and basis['market'] != member['market']:
                issue = 'basis_market_mismatch'
            if basis and basis.get('price_adjustment') == 'cash_index_points':
                issue = 'stock_price_basis_required'
            bases[symbol] = basis
            if issue:
                indexes[symbol] = ({}, issue)
    bench, bench_issue = bar_index(benchmark, sessions)
    benchmark_basis, benchmark_basis_issue = series_basis(benchmark) if validate_basis else (None, None)
    if reference:
        benchmark_basis, benchmark_basis_issue = reference['basis'], None
    horizons = {}
    for horizon in (63, 126):
        prices, used, excluded = [], [], {}
        for symbol, (index, issue) in indexes.items():
            values, reason = window(index, sessions, horizon + 1)
            if issue or reason:
                excluded[symbol] = issue or reason
            else:
                prices.append(values)
                used.append(symbol)
        curve = equal_weight_index(prices)
        values, reason = window(bench, sessions, horizon + 1)
        reason = bench_issue or reason
        br = values[-1] / values[0] - 1 if not reason else None
        sr = curve[-1] / curve[0] - 1 if curve else None
        if reference:
            ref = reference['horizons'][str(horizon)]
            br = ref['return']
            reason = 'market_reference_unavailable' if br is None else None
            if (ref['end'] != sessions[-1] or
                    ref['start'] != (sessions[-horizon-1] if len(sessions)>horizon else None)):
                reason = 'market_reference_period_mismatch'
        rs_issue = reason
        if validate_basis:
            rs_issue = rs_issue or benchmark_basis_issue
            for symbol in used:
                rs_issue = rs_issue or comparison_issue(bases[symbol], benchmark_basis)
        metric = {**metric_counts(used, excluded), 'start': sessions[-horizon-1] if len(sessions)>horizon else None,
                  'end': sessions[-1], 'return': sr, 'benchmark_return': br,
                  'benchmark_issue': rs_issue, 'relative_strength': (1 + sr) / (1 + br) - 1
                  if sr is not None and br is not None and not rs_issue else None,
                  'positive_count': sum(p[-1] > p[0] for p in prices),
                  'positive_fraction': sum(p[-1] > p[0] for p in prices)/len(prices) if prices else None,
                  'index': [{'date': d, 'value': v} for d, v in zip(sessions[-horizon-1:], curve)] if curve else []}
        horizons[str(horizon)] = metric
    curve = [r['value'] for r in horizons['126']['index']]
    trend = {'used': horizons['126']['used'], 'basis': '126_session_fixed_cohort_daily_equal_weight_index',
             'distance_sma20': None, 'distance_sma60': None, 'sma20_change5': None}
    if curve:
        sma20, sma60 = statistics.fmean(curve[-20:]), statistics.fmean(curve[-60:])
        trend.update(distance_sma20=curve[-1]/sma20-1, distance_sma60=curve[-1]/sma60-1,
                     sma20_change5=sma20/statistics.fmean(curve[-25:-5])-1)
    used, excluded, above = [], {}, 0
    activity_used, activity_excluded, ratios = [], {}, []
    for symbol, (index, issue) in indexes.items():
        values, reason = window(index, sessions, 60)
        if issue or reason:
            excluded[symbol] = issue or reason
        else:
            used.append(symbol)
            above += values[-1] > statistics.fmean(values)
        values, reason = window(index, sessions, 21, 'volume')
        if issue or reason:
            activity_excluded[symbol] = issue or reason
        elif statistics.fmean(values[:-1]) == 0:
            activity_excluded[symbol] = 'zero_prior20_average_volume'
        else:
            activity_used.append(symbol)
            ratios.append(values[-1]/statistics.fmean(values[:-1]))
    return {'horizons': horizons, 'trend': trend,
            'breadth_sma60': {**metric_counts(used, excluded), 'above_count': above,
                             'fraction': above/len(used) if used else None},
            'activity': {**metric_counts(activity_used, activity_excluded),
                         'basis': ('provider_adjusted_' if validate_basis else '')+
                                  'volume_today_divided_by_prior20_mean_no_today_in_denominator',
                         'mean_ratio': statistics.fmean(ratios) if ratios else None,
                         'median_ratio': statistics.median(ratios) if ratios else None,
                         'ratios': dict(zip(activity_used, ratios))}}


def korean_market_reference(members, series, sessions):
    if any(r['market'] != 'KR' for r in members):
        raise ValueError('KR_reference_cannot_mix_markets')
    whole = group_metrics(members, series, sessions, None, validate_basis=True)
    basis = next((series_basis(series.get(('KR', r['symbol'])))[0] for r in members
                  if series_basis(series.get(('KR', r['symbol'])))[1] is None), None)
    reference = {'code':'KR_SAVED_UNIVERSE_DAILY_EQUAL_WEIGHT', 'target':len(members),
                 'name':'저장 한국 대상 종목 전체 동일비중', 'basis':basis, 'horizons':whole['horizons']}
    # The reference index is an absolute curve; it has no reference of its own.
    for metric in reference['horizons'].values():
        for key in ('benchmark_return','benchmark_issue','relative_strength'):
            metric.pop(key)
    return reference


def align_groups(groups, universe, applied):
    """Reuse only exact identities; preserve additions and identity changes as unknown."""
    def key(row):
        return row.get('exchange', row.get('listing_exchange_family')), row['provider_symbol']
    previous = {key(row): row for row in groups}
    result = []
    changes = {'added': [], 'identity_changed': [], 'absent_from_current_listing':
               [list(k) for k in sorted(set(previous) - set(universe))]}
    for k, item in universe.items():
        old = previous.get(k)
        same = old is not None and all(old.get(x) == item.get(x) for x in ('market', 'symbol', 'isin_code'))
        if same:
            result.append(old)
            continue
        reason = 'classification_not_acquired_for_current_identity' if old else 'new_listing_classification_not_acquired'
        changes['identity_changed' if old else 'added'].append(list(k))
        result.append({k: item.get(k) for k in ('market', 'symbol', 'provider_symbol', 'isin_code', 'name')} | {
            'exchange': item['listing_exchange_family'], 'group_taxonomy':
            'KR_WICS_SECTOR' if item['market'] == 'KR' else 'US_FINVIZ_SECTOR',
            'group_code': None, 'group_name': None, 'group_state': 'unmatched', 'reason': reason,
            'evidence': [], 'applied_at': applied, 'original_classification': None,
            'prior_identity': old, 'historical_classification_available': False})
    return result, changes


def build(market_root, classification_root, evidence, nh_root, applied, *, allow_universe_changes=False, basis_rechecks=None, snapshot=None):
    inputs = {}
    basis_contract = load_basis(evidence, checked, load, inputs, applied, age_issue, rechecks=basis_rechecks)
    if (evidence/'wics-public.json').exists() and basis_contract is None:
        raise ValueError('WICS_observation_requires_verified_calculation_basis')
    market_manifest = load(checked(market_root / 'manifest.json', None, inputs))
    for name, expected in market_manifest.items():
        checked(market_root / name, expected, inputs)
    # The optional snapshot has already been read by the caller; hashes above
    # are checked even on reuse. Avoid holding a second full-universe parse.
    if snapshot is None:
        snapshot = load(market_root / 'snapshot.json')
    quality = load(market_root / 'summary.json')
    original_manifest = load(checked(classification_root / 'manifest.json', None, inputs))
    original_path = checked(classification_root / 'classification.json',
                            original_manifest['output_hashes']['classification.json'], inputs)
    original = load(original_path)['rows']
    def key(r): return r.get('exchange', r.get('listing_exchange_family')), r['provider_symbol']
    universe = {key(r): r for r in snapshot['universe']}
    if len(universe) != len(snapshot['universe']) or len({key(r) for r in original}) != len(original):
        raise ValueError('duplicate_universe_or_classification')
    if not allow_universe_changes and set(universe) != {key(r) for r in original}:
        raise ValueError('classification_universe_mismatch')
    for r in ([] if allow_universe_changes else original):
        if any(r.get(k) != universe[key(r)].get(k) for k in ('market','symbol','isin_code')):
            raise ValueError('classification_identity_mismatch')
    groups = group_rows(original, evidence, nh_root, applied, inputs)
    changes = None
    if allow_universe_changes:
        groups, changes = align_groups(groups, universe, applied)
    from classification_refresh import apply as apply_refresh
    groups = apply_refresh(groups, snapshot, checked, inputs, applied, classification_age_issue, SECTORS)
    from adr_metadata import apply_groups as apply_adr_groups
    groups = apply_adr_groups(groups, snapshot, inputs, applied)
    from user_classification import apply as apply_user_classification
    groups = apply_user_classification(groups, snapshot)
    wics = load_wics(evidence, checked, load, inputs)
    series = {}
    for row in snapshot['series']:
        k = row['market'], row['symbol']
        if k in series:
            raise ValueError('duplicate_series_identity')
        if key(row) not in universe or any(row.get(x) != universe[key(row)].get(x) for x in ('market','symbol','isin_code')):
            raise ValueError('price_series_identity_mismatch')
        series[k] = row
    observations, coverage, market_info = [], {}, {}
    for market in ('KR', 'US'):
        sessions = snapshot['sessions'][market]
        if not sessions or sessions != sorted(set(sessions)):
            raise ValueError('invalid_market_calendar')
        selected = [r for r in groups if r['market'] == market]
        matched = [r for r in selected if r['group_state'] == 'matched']
        missing_prices = [r['symbol'] for r in selected if (market, r['symbol']) not in series]
        coverage[market] = {'target': len(selected), 'matched': len(matched), 'unmatched': len(selected)-len(matched),
                            'matched_fraction': len(matched)/len(selected) if selected else None,
                            'unmatched_reasons': dict(Counter(r['reason'] for r in selected if r['group_state']!='matched')),
                            'price_series_available': len(selected)-len(missing_prices),
                            'price_series_missing': missing_prices,
                            'matched_price_missing': sum((market,r['symbol']) not in series for r in matched),
                            'classification_complete': len(matched)==len(selected),
                            'user_defined_other': sum(r.get('group_taxonomy')=='US_USER_OTHER' for r in matched)}
        reviewed = [r for r in selected if r['group_state'] != 'matched' and 'external_review' in r]
        if reviewed:
            coverage[market]['external_review_categories'] = dict(Counter(review_category(r) for r in reviewed))
            coverage[market]['external_reviewed_count'] = len(reviewed)
            confirmed = sum(r.get('group_taxonomy')=='US_FINVIZ_SECTOR' for r in matched)+sum(bool(r.get('external_finviz_sector')) for r in reviewed)
            coverage[market]['finviz_sector_confirmed_total'] = confirmed
            coverage[market]['finviz_sector_unconfirmed_total'] = len(selected)-confirmed
        benchmark = snapshot['benchmarks'].get(BENCHMARKS[market])
        if benchmark and benchmark['market'] != market:
            raise ValueError('benchmark_market_mismatch')
        market_info[market] = {'completed_session': sessions[-1], 'session_count': len(sessions),
                               'calendar_source': 'saved_snapshot.sessions.'+market,
                               'benchmark': {k:v for k,v in (benchmark or {}).items() if k!='bars'},
                               'benchmark_missing': benchmark is None,
                               'series_price_bases': sorted({r['price_basis'] for r in snapshot['series'] if r['market']==market})}
        reference = None
        if basis_contract and market == 'KR':
            reference = korean_market_reference(selected, series, sessions)
            market_info[market]['rs_reference'] = reference
            context = group_metrics([], {}, sessions, benchmark)['horizons']
            market_info[market]['context_benchmark'] = {'symbol':'^KS11', 'name':'KOSPI 참고 전용',
                'reason':'KRX_regular_index_not_comparable_to_integrated_stock_daily_session',
                'horizons':{h:{'return':m['benchmark_return'],'issue':m['benchmark_issue'],
                               'start':m['start'],'end':m['end']} for h,m in context.items()}}
        grouped = defaultdict(list)
        for row in matched:
            grouped[(row['group_code'], row['group_name'])].append(row)
        if market == 'US':
            for name in SECTORS:
                grouped.setdefault((name,name), [])
        elif wics:
            for code, name in wics['sectors'].items():
                grouped.setdefault((code,name), [])
        for (code, name), members in sorted(grouped.items()):
            observations.append({'market':market, 'group_code':code, 'group_name':name,
                                 'as_of':sessions[-1], 'matched_members':len(members),
                                 'classification_coverage':('user_defined_other_not_official_sector' if code=='USER_OTHER_US' else 'verified_subset_not_complete_sector_universe'),
                                 **group_metrics(members,series,sessions,benchmark,
                                                 validate_basis=basis_contract is not None, reference=reference)})
    report = {'schema':2 if basis_contract else 1, 'applied_at':applied, 'network_calls':0,
              'processing_state':'completed_with_limitations', 'market_source_state':quality['state'],
              'market_data_complete':quality['data_complete'], 'market_processing_state':quality['processing_state'],
              'source_exceptions':quality['exceptions'], 'markets':market_info, 'coverage':coverage,
              'kr_classification_basis': 'FnGuide_WICS_top_level_direct_components'
                  if (evidence/'wics-public.json').exists() else 'legacy_NH_mixed_level_observation_baskets',
              'us_classification_basis': 'verified_public_sector_maps_and_stockanalysis_adr' if snapshot.get('adr_metadata') else 'public_full_market_map_and_residual_screener_with_verified_alias_links'
                  if (evidence/'finviz-public.json').exists() else 'individual_quote_convenience_sample',
              'limitations':['current_classification_of_historical_prices_not_point_in_time_backtest',
                            'current_provider_universe_survivorship_and_incomplete_sector_coverage',
                            'map_screener_identity_exact_ticker_no_independent_exchange_or_isin_verification',
                            'daily_equal_weight_rebalanced_price_return_not_investable_total_return',
                            'KR_Toss_integrated_extended_session_prices_vs_KRX_KOSPI_index',
                            'US_SPY_proxy_not_full_NYSE_NASDAQ_market_or_cash_SP500',
                            'adjusted_price_and_volume_provider_semantics_not_fully_verified',
                            'no_net_inflow_no_composite_score_no_automatic_signal'],
              'observations':observations}
    report['metric_coverage']={}
    if changes is not None:
        report['classification_universe_changes'] = changes
    report['calculation_basis'] = basis_contract
    if basis_contract:
        report['input_basis_audit'] = {}
        for market in ('KR','US'):
            checked_series = [(r['symbol'], *series_basis(r)) for r in snapshot['series'] if r['market']==market]
            report['input_basis_audit'][market] = {'validated':sum(issue is None for _,_,issue in checked_series),
                'rejected':{symbol:issue for symbol,_,issue in checked_series if issue},
                'contract':next((basis for _,basis,issue in checked_series if not issue), None),
                'benchmark_contract':series_basis(snapshot['benchmarks'].get(BENCHMARKS[market]))[0]}
        report['limitations'].remove('adjusted_price_and_volume_provider_semantics_not_fully_verified')
        report['limitations'].remove('KR_Toss_integrated_extended_session_prices_vs_KRX_KOSPI_index')
        report['limitations'].remove('daily_equal_weight_rebalanced_price_return_not_investable_total_return')
        report['limitations'].extend(['provider_adjustments_not_independent_corporate_action_factor_audit',
                                     'provider_daily_volume_not_all_US_venues_or_net_inflow',
                                     'KR_reference_saved_universe_equal_weight_not_official_KOSPI',
                                     'daily_equal_weight_rebalanced_adjusted_close_return_not_certified_total_return'])
    for market in ('KR','US'):
        totals={}
        for name in ('63','126','breadth_sma60','activity'):
            metrics=[r['horizons'][name] if name in ('63','126') else r[name]
                     for r in observations if r['market']==market]
            totals[name]={'classified_members':coverage[market]['matched'],
                          'used':sum(r['used'] for r in metrics),
                          'excluded':sum(r['excluded'] for r in metrics),
                          'exclusion_reasons':dict(Counter(reason for r in metrics for reason in r['exclusions'].values()))}
        report['metric_coverage'][market]=totals
    return report, groups, inputs


def fmt(value, percent=True):
    return '—' if value is None else f'{value*100:.2f}%' if percent else f'{value:.2f}×'


def table_rows(report):
    for r in report['observations']:
        a,b = r['horizons']['63'],r['horizons']['126']
        yield [r['market'],r['group_name'],r['matched_members'],f"{a['used']}/{b['used']}",
               fmt(a['return']),fmt(b['return']),fmt(a['relative_strength']),fmt(b['relative_strength']),
               fmt(r['trend']['distance_sma20']),fmt(r['trend']['distance_sma60']),fmt(r['trend']['sma20_change5']),
               f"{a['positive_count']}/{a['used']}",f"{b['positive_count']}/{b['used']}",
               f"{r['breadth_sma60']['above_count']}/{r['breadth_sma60']['used']}",
               fmt(r['activity']['mean_ratio'],False),fmt(r['activity']['median_ratio'],False),r['activity']['used'],
               '소수 표본(<5)' if min(a['used'],b['used'],r['activity']['used'])<5 else '부분 대응']


HEADERS = ['시장','관찰 그룹','분류수','사용3/6M','수익3M','수익6M','RS3M','RS6M',
           'SMA20거리','SMA60거리','SMA20변화5일','상승3M 분자/분모','상승6M 분자/분모',
           'SMA60상회 분자/분모','활동평균','활동중앙','활동수','한계']


def render(report):
    intro = ['# 로컬 섹터 관찰표 — 검증된 일부 구성', '',
             '시장 기준 KR '+report['markets']['KR']['completed_session']+' / US '+report['markets']['US']['completed_session'],
             ('기준시장: KR 저장 대상 종목 전체 동일비중 / US SPY(저장 ^GSPC의 ETF 프록시). '
              if report.get('calculation_basis') else '기준시장: KR KOSPI / US SPY(저장 ^GSPC의 ETF 프록시). ')+
             '분류 적용 '+report['applied_at'],
             f"원자료 {report['market_source_state']}·data_complete={str(report['market_data_complete']).lower()} 유지. 현재 분류로 과거 가격을 재구성한 관찰이며 당시 투자 가능 성과가 아닙니다.",
             ('한국은 FnGuide 공식 WICS 대분류 10개를 적용합니다. 구성자료 밖의 종목은 FnGuide 종목별 WICS 소분류와 공식 계층표로 확인합니다. 미국 Finviz와 별도 체계이며 원 KIND/NH 업종은 보존합니다.'
              if report.get('kr_classification_basis')=='FnGuide_WICS_top_level_direct_components' else
              '한국은 과거 NH 중/대분류 혼합 관찰 바구니입니다. 단일 외부 표준 동일성을 검증한 분류가 아닙니다.'),
             ('공식 Toss FAQ 기준: 수정가격·역비율 수정거래량을 사용하며 다시 조정하지 않습니다. 미국 종가는 정규장, 거래량은 시간외를 포함할 수 있는 공급자 집계입니다. 배당 재투자 총수익률·전 거래소 거래량이 아닙니다. 한국 기준도 같은 통합 일봉으로 계산하고 KOSPI는 참고값으로만 표시합니다.'
              if report.get('calculation_basis') else
              '과거 계산: 한국 Toss 통합가격과 KRX 지수의 세션 차이 및 조정 의미 미검증 한계가 있습니다.'),
             '가격·거래활동은 자금 순유입 또는 자동 매수 신호가 아닙니다. 미국 분류는 '+
             ('Finviz 공개 Full Market 지도·누락 종목 스크리너·직접 연결이 검증된 별칭을 사용합니다. 지도/스크리너의 정확한 ticker 대응이며 거래소·ISIN 전수 확인은 아닙니다.'
              if report['us_classification_basis'].startswith('public_') else '개별 페이지의 편의 표본입니다.')+
             ' 저장된 공급자 유니버스 범위이며 실제 섹터 전체 구성을 보증하지 않습니다.', '']
    for market,c in report['coverage'].items():
        intro.append(f"- {market}: 대상 {c['target']}, 관찰 집계 편입 {c['matched']}, 집계 미포함 {c['unmatched']}, 일봉 {c['price_series_available']}, 일봉 부재 {len(c['price_series_missing'])}, 편입 중 일봉 부재 {c['matched_price_missing']}")
        if c.get('external_review_categories'):
            intro.append(f"  Finviz 종목별 섹터 확인 {c['finviz_sector_confirmed_total']}, Finviz 미확인 {c['finviz_sector_unconfirmed_total']}. 섹터 확인과 집계 편입은 별개입니다.")
            labels = {'finviz_confirmed_but_observation_excluded':'Finviz 분류 확인·집계 제외',
                      'alternate_provider_confirmed_Finviz_unconfirmed':'타 제공자 분류 확인·Finviz 미확인',
                      'checked_sources_have_no_verified_sector':'확인한 출처에서 분류 근거 미확보'}
            intro.append('  미국 미매칭 재확인: '+', '.join(labels[k]+' '+str(v) for k,v in c['external_review_categories'].items())+
                         '. 미매칭은 외부 미분류를 뜻하지 않습니다. 종목별 출처·게시 섹터·조회 결과는 unmatched.json의 external_review/external_finviz_sector에 보존했습니다.')
        for name,metric in report['metric_coverage'][market].items():
            labels={'63':'3개월','126':'6개월','breadth_sma60':'SMA60 참여','activity':'거래활동'}
            intro.append(f"  {labels[name]}: 사용 {metric['used']}/{metric['classified_members']}, 제외 {metric['excluded']} — "+
                         ', '.join(f'{key}: {value}' for key,value in metric['exclusion_reasons'].items()))
        reference=report['markets'][market].get('rs_reference')
        if reference:
            for h,metric in reference['horizons'].items():
                intro.append(f"  한국 기준 {h}세션: 수익 {fmt(metric['return'])}, 사용 {metric['used']}/{reference['target']}, 제외 {metric['excluded']}. WICS 미매칭도 가격 적격이면 포함.")
            context=report['markets'][market]['context_benchmark']
            intro.append('  KOSPI 참고 전용(RS 분모 아님): 3개월 '+fmt(context['horizons']['63']['return'])+
                         ' / 6개월 '+fmt(context['horizons']['126']['return']))
    intro += ['', '3/6M=나라별 63/126세션. 기간별 고정 구성의 일별 동일비중 수익률을 복리 연결합니다. RS=(1+섹터수익률)/(1+기준수익률)−1.',
              '추세=126세션 구성 지수의 SMA20·60 거리 및 SMA20의 5세션 변화. 활동=당일 거래량/직전20세션 평균, 그 종목별 비율의 동일비중 평균·중앙값.',
              '분모는 지표별 유효 종목입니다. 결측을 0으로 채우지 않습니다. <5는 해석 주의 표시이며 필터가 아닙니다.',
              '원 업종·종목별 근거/충돌은 groups.json, 지표별 구성·제외 사유·일별 지수·기준수익률은 observation.json, 전수 미매칭은 unmatched.json에 있습니다.', '']
    rows = list(table_rows(report))
    md = '\n'.join(intro+['| '+' | '.join(HEADERS)+' |','|'+'|'.join(['---']*len(HEADERS))+'|']+
                   ['| '+' | '.join(str(v) for v in row)+' |' for row in rows])+'\n'
    body = '<h1>로컬 섹터 관찰표</h1>'+''.join('<p>'+html.escape(line.lstrip('#- '))+'</p>' for line in intro[2:] if line)
    table = '<div class="scroll"><table><thead><tr>'+''.join('<th>'+html.escape(h)+'</th>' for h in HEADERS)+'</tr></thead><tbody>'
    table += ''.join('<tr>'+''.join('<td>'+html.escape(str(v))+'</td>' for v in row)+'</tr>' for row in rows)+'</tbody></table></div>'
    page = '<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>섹터 관찰표</title><style>body{font:15px system-ui;margin:24px;color:#182535;background:#f7f9fc}p{max-width:1150px;line-height:1.5}.scroll{overflow:auto;background:white;border:1px solid #bbc7d5}table{border-collapse:collapse;white-space:nowrap}th,td{padding:9px;border:1px solid #dbe3ed;text-align:right}th{background:#e4edf7}td:nth-child(2){text-align:left}tr:nth-child(even){background:#f2f6fa}</style>'+body+table+'</html>'
    return md,page


def export(output, roots, applied):
    if output.exists():
        raise ValueError('new_output_required')
    report, groups, inputs = build(*roots,applied)
    output.mkdir(parents=True)
    write(output/'observation.json',report)
    write(output/'groups.json',groups)
    write(output/'unmatched.json',[r for r in groups if r['group_state']!='matched'])
    md,page = render(report)
    (output/'observation.md').write_text(md,encoding='utf-8')
    (output/'observation.html').write_text(page,encoding='utf-8')
    with (output/'observation.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.writer(stream)
        writer.writerow(HEADERS)
        writer.writerows(table_rows(report))
    write(output/'manifest.json',{'schema':1,'roots':[str(p.resolve()) for p in roots],'applied_at':applied,
                                  'input_hashes':inputs,'code_hashes':{str(p.resolve()):sha(p) for p in
                                  (Path(__file__),Path(__file__).with_name('master_parser.py'),
                                   Path(__file__).with_name('finviz_public_classification.py'),
                                   Path(__file__).with_name('wics_classification.py'),
                                   Path(__file__).with_name('sector_external_review.py'),
                                   Path(__file__).with_name('sector_basis.py'))},
                                  'output_hashes':{p.name:sha(p) for p in output.iterdir()}})
    return report


def verify(output):
    manifest=load(output/'manifest.json')
    for path,expected in {**manifest['input_hashes'],**manifest['code_hashes']}.items():
        checked(Path(path),expected,{})
    for name,expected in manifest['output_hashes'].items():
        checked(output/name,expected,{})
    report,groups,inputs=build(*[Path(p) for p in manifest['roots']],manifest['applied_at'])
    if (report!=load(output/'observation.json') or groups!=load(output/'groups.json') or inputs!=manifest['input_hashes']):
        raise ValueError('offline_replay_mismatch')
    md,page=render(report)
    if md!=(output/'observation.md').read_text(encoding='utf-8') or page!=(output/'observation.html').read_text(encoding='utf-8'):
        raise ValueError('render_replay_mismatch')
    return {'verified':True,'network_calls':0,'groups':len(report['observations']),
            'universe_rows':len(groups),'input_files':len(inputs),'coverage':report['coverage']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--market-root',type=Path)
    parser.add_argument('--classification-root',type=Path)
    parser.add_argument('--evidence',type=Path)
    parser.add_argument('--nh-root',type=Path)
    parser.add_argument('--as-of')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--verify',action='store_true')
    args=parser.parse_args()
    if not args.output.resolve().is_relative_to(PROJECT/'runtime') or args.output.resolve()==PROJECT/'runtime':
        parser.error('private_runtime_subfolder_required')
    if args.verify:
        print(json.dumps(verify(args.output),ensure_ascii=False))
    else:
        roots=[args.market_root,args.classification_root,args.evidence,args.nh_root]
        if not all(roots) or not args.as_of:
            parser.error('all four roots and explicit --as-of required')
        stamp(args.as_of)
        report=export(args.output,roots,args.as_of)
        print(json.dumps({'state':report['processing_state'],'coverage':report['coverage']},ensure_ascii=False))


if __name__=='__main__':
    main()
