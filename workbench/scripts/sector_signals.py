"""Completed-session sector changes; pure local calculation, no collection or orders.

Price/volume proxy shares are not cash flows. Each replay date chooses its own
fixed lookback cohort, never a cohort selected using later price availability.
"""
import hashlib
import html
import math
import statistics as st
from collections import Counter, defaultdict
from display_names import stock_label
from pathlib import Path

from sector_basis import series_basis, comparison_issue
from sector_observation import bar_index, equal_weight_index, number, write

DEFAULTS = {
    'comparison_days': 5, 'activity_baseline_days': 20, 'rs_days': 20,
    'trend_days': 20, 'history_days': 63, 'min_members': 5,
    'min_coverage': .8, 'activity_change': .10, 'share_change': .05,
    'share_change_floor': .0001, 'rs_change': .005, 'price_weakness': -.005,
    'surge_level': 1.5, 'concentration_level': .5,
    'concentration_change': .10, 'concentration_min_members': 10,
    'narrow_breadth': .5, 'cohort_change_warning': .10,
    'leaders': 5,
}
LABELS = {'strengthening': '관심 강화', 'strong_cooling': '강세 유지·활동 둔화',
          'surge_weak': '거래 급증·가격 약세', 'mixed': '혼조',
          'steady': '뚜렷한 변화 없음', 'unavailable': '판정 보류'}
ORDER = {'strengthening': 0, 'surge_weak': 1, 'strong_cooling': 2,
         'mixed': 3, 'steady': 4, 'unavailable': 5}


def settings(overrides=None):
    c = dict(DEFAULTS)
    if overrides:
        if set(overrides) - set(c):
            raise ValueError('unknown_sector_state_setting')
        c.update(overrides)
    for k, v in c.items():
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            raise ValueError('invalid_sector_state_setting:' + k)
    for k in ('comparison_days', 'activity_baseline_days', 'rs_days', 'trend_days',
              'history_days', 'min_members', 'concentration_min_members', 'leaders'):
        if not isinstance(c[k], int) or c[k] < 1:
            raise ValueError('positive_integer_required:' + k)
    for k in ('min_coverage', 'share_change_floor', 'concentration_level',
              'concentration_change', 'narrow_breadth', 'cohort_change_warning'):
        if not 0 < c[k] <= 1:
            raise ValueError('fraction_required:' + k)
    if any(c[k] <= 0 for k in ('activity_change', 'share_change', 'rs_change', 'surge_level')) or c['price_weakness'] >= 0:
        raise ValueError('invalid_sector_state_threshold')
    return c


def divide(a, b):
    return a / b if b is not None and b > 0 else None


def change(a, b):
    r = divide(a, b)
    return r - 1 if r is not None else None


def classify(m, c):
    """State is an explanation of observed changes, never a buy/sell score."""
    required = ('activity_now', 'activity_change', 'share_change', 'share_delta',
                'rs_now', 'rs_delta', 'return_recent', 'trend_distance', 'trend_slope')
    if m.get('issues') or any(m.get(k) is None for k in required):
        return 'unavailable'
    activity_up = m['activity_change'] >= c['activity_change']
    share_up = m['share_change'] >= c['share_change'] and m['share_delta'] >= c['share_change_floor']
    rs_up = m['rs_delta'] >= c['rs_change']
    # Price weakness takes precedence over a merely improving relative return.
    if activity_up and m['activity_now'] >= c['surge_level'] and m['return_recent'] <= c['price_weakness']:
        return 'surge_weak'
    if activity_up and share_up and rs_up:
        return 'strengthening'
    if (m['activity_change'] <= -c['activity_change'] and m['rs_now'] > 0
            and m['trend_distance'] > 0 and m['trend_slope'] > 0):
        return 'strong_cooling'
    material = (abs(m['activity_change']) >= c['activity_change'] or
                (abs(m['share_change']) >= c['share_change'] and abs(m['share_delta']) >= c['share_change_floor']) or
                abs(m['rs_delta']) >= c['rs_change'])
    return 'mixed' if material else 'steady'


def explain(m, c):
    if m['state']=='unavailable':
        return '자료 또는 비교 조건이 부족해 방향 판단을 보류합니다.'
    def direction(value, threshold, up, down):
        return up if value>=threshold else down if value<=-threshold else '큰 변화 없음'
    a=m['activity_change']; rs=m['rs_delta']
    level='평소 이상' if m['activity_now']>=1 else '평소 미만'
    activity_text=level+'·'+direction(a,c['activity_change'],'활동 강화','활동 둔화')
    share=direction(m['share_change'],c['share_change'],'거래 비중 상승','거래 비중 하락') if abs(m['share_delta'])>=c['share_change_floor'] else '거래 비중 큰 변화 없음'
    relative=direction(rs,c['rs_change'],'상대강도 개선','상대강도 약화')
    if m['state']=='strong_cooling':
        return '가격 추세는 강세 유지, 거래활동은 둔화 · '+level
    if m['state']=='surge_weak':
        return '거래가 급증해도 가격은 하락 · 매도 압력 가능성 관찰'
    return activity_text+' / '+share+' / '+relative


def prepare(snapshot, market, as_of, c):
    sessions = [d for d in snapshot['sessions'][market] if d <= as_of]
    if sessions != sorted(set(sessions)):
        raise ValueError('invalid_sector_state_sessions')
    universe = [r for r in snapshot['universe'] if r['market'] == market]
    rows = {}
    for r in snapshot['series']:
        if r['market'] != market:
            continue
        if r['symbol'] in rows:
            raise ValueError('duplicate_sector_state_series')
        rows[r['symbol']] = r
    prepared = {}
    for u in universe:
        symbol = u['symbol']
        if symbol in prepared:
            raise ValueError('duplicate_sector_state_universe')
        r = rows.get(symbol)
        basis, issue = series_basis(r)
        if basis and (basis['market'] != market or basis.get('price_adjustment') == 'cash_index_points'):
            issue = 'stock_price_basis_required'
        if r and any(r.get(k) != u.get(k) for k in ('symbol', 'market', 'isin_code')):
            issue = 'identity_mismatch'
        # Exclude future bars before validation so future missing/invalid bars
        # cannot change an earlier decision, including its eligibility cohort.
        clipped = {**r, 'bars': [b for b in r['bars'] if b['date'] <= as_of]} if r else None
        bars = clipped['bars'] if clipped else []
        seen = set(); invalid_dates = []
        session_set = set(sessions)
        previous = None
        for bar in bars:
            date = bar['date']
            if date in seen or date not in session_set or (previous is not None and date < previous):
                invalid_dates.append(date)
            seen.add(date); previous = date
        prepared[symbol] = ({bar['date']:bar for bar in bars}, issue,
                            min(invalid_dates) if invalid_dates else None)
    return sessions, universe, prepared


def cohort(prepared, dates):
    eligible, exclusions = {}, {}
    for symbol, (index, issue, invalid_since) in prepared.items():
        if not issue and invalid_since is not None and dates and invalid_since <= dates[-1]:
            issue = 'duplicate_unsorted_or_non_session_bar'
        if not issue and any(d not in index for d in dates):
            issue = 'missing_session_or_short_history'
        bars = [index[d] for d in dates] if not issue else []
        if not issue and not all(number(b.get('close'), True) and number(b.get('volume')) for b in bars):
            issue = 'invalid_price_or_volume'
        if issue:
            exclusions[symbol] = issue
        else:
            eligible[symbol] = bars
    return eligible, exclusions


def activity(bars, b, n):
    ratios = []
    for i in range(len(bars) - 2*n, len(bars)):
        avg = st.fmean(x['volume'] for x in bars[i-b:i])
        if avg <= 0:
            return None
        ratios.append(bars[i]['volume'] / avg)
    return ratios


def top_share(values, k=3):
    return divide(sum(sorted(values, reverse=True)[:k]), sum(values))


def metric(members, eligible, totals, reference, c):
    n, b, h, t = (c[k] for k in ('comparison_days', 'activity_baseline_days', 'rs_days', 'trend_days'))
    curves = [[x['close'] for x in eligible[s]] for s in members]
    curve = equal_weight_index(curves)
    acts = [activity(eligible[s], b, n) for s in members]
    usable = [x for x in acts if x is not None]
    daily = [st.median(x[i] for x in usable) for i in range(2*n)] if usable else None
    stock_values = {s: [x['close']*x['volume'] for x in eligible[s]] for s in members}
    proxy = [sum(v[i] for v in stock_values.values()) for i in range(len(totals))]
    now = st.fmean(daily[-n:]) if daily else None
    prev = st.fmean(daily[:n]) if daily else None
    share_now = divide(sum(proxy[-n:]), sum(totals[-n:]))
    share_prev = divide(sum(proxy[-2*n:-n]), sum(totals[-2*n:-n]))
    rs_now = (curve[-1]/curve[-h-1])/(reference[-1]/reference[-h-1])-1 if curve and reference else None
    rs_prev = (curve[-n-1]/curve[-h-n-1])/(reference[-n-1]/reference[-h-n-1])-1 if curve and reference else None
    current_values = {s: sum(v[-n:]) for s, v in stock_values.items()}
    baseline_values = {s: sum(v[-b-n:-n]) for s, v in stock_values.items()}
    concentration = top_share(list(current_values.values()))
    prior_concentration = top_share(list(baseline_values.values()))
    stock_returns = {s: eligible[s][-1]['close']/eligible[s][-n-1]['close']-1 for s in members}
    breadth = st.fmean(v > 0 for v in stock_returns.values()) if members else None
    prev_breadth = st.fmean(eligible[s][-n-1]['close'] > eligible[s][-2*n-1]['close'] for s in members) if members else None
    slope = curve[-1]/st.fmean(curve[-t:])-1 if curve else None
    m = {'activity_now': now, 'activity_previous': prev, 'activity_change': change(now, prev) if now is not None else None,
         'activity_today': daily[-1] if daily else None, 'activity_used': len(usable),
         'activity_excluded': [s for s,a in zip(members,acts) if a is None],
         'share_now': share_now, 'share_previous': share_prev,
         'share_change': change(share_now, share_prev) if share_now is not None else None,
         'share_delta': share_now-share_prev if share_now is not None and share_prev is not None else None,
         'rs_now': rs_now, 'rs_previous': rs_prev, 'rs_delta': rs_now-rs_prev if rs_now is not None else None,
         'return_recent': curve[-1]/curve[-n-1]-1 if curve else None,
         'trend_distance': slope, 'trend_slope': st.fmean(curve[-t:])/st.fmean(curve[-t-n:-n])-1 if curve else None,
         'breadth': breadth, 'breadth_previous': prev_breadth,
         'top3_share': concentration, 'top3_previous': prior_concentration,
         'top3_delta': concentration-prior_concentration if concentration is not None and prior_concentration is not None else None,
         'proxy_total_recent': sum(current_values.values()), 'proxy_market_recent': sum(totals[-n:])}
    m['concentrated'] = bool(len(members) >= c['concentration_min_members'] and breadth < c['narrow_breadth']
        and concentration is not None and (concentration >= c['concentration_level'] or (m['top3_delta'] is not None and m['top3_delta'] >= c['concentration_change'])))
    m['sudden_activity'] = bool(m['activity_today'] is not None and m['activity_today'] >= c['surge_level']
                                and (now is None or now < c['surge_level']))
    leaders = sorted(members, key=lambda s: (-current_values[s], s))[:c['leaders']]
    weak = sorted((s for s in members if stock_returns[s] < 0), key=lambda s: (stock_returns[s], s))[:c['leaders']]
    def stock(s):
        return {'symbol': s, 'return_recent': stock_returns[s], 'proxy_share': divide(current_values[s], sum(current_values.values())),
                'equal_weight_return_contribution': stock_returns[s]/len(members)}
    return m, [stock(s) for s in leaders], [stock(s) for s in weak], {s:stock(s) for s in members}


def build(snapshot, groups, observation, overrides=None, *, as_of=None):
    c = settings(overrides)
    result = {'schema': 1, 'config': c, 'network_calls': 0, 'markets': {}, 'sectors': [],
              'classification_applied_at': observation.get('applied_at'),
              'limitations': ['current_classification_and_universe_not_point_in_time_backtest',
                  'close_times_adjusted_volume_is_proxy_not_actual_turnover_or_net_inflow',
                  'completed_daily_bars_only_no_intraday_signal', 'rule_observation_not_profitability_validation']}
    n, b, h, t = (c[k] for k in ('comparison_days', 'activity_baseline_days', 'rs_days', 'trend_days'))
    required = max(b+2*n, h+n+1, t+n, b+n, 2*n+1)
    result['required_sessions'] = required
    group_lookup = {(r['market'],r['symbol']): r for r in groups}
    for market in ('KR', 'US'):
        cutoff = as_of or snapshot['sessions'][market][-1]
        sessions, universe, prepared = prepare(snapshot, market, cutoff, c)
        definitions = [r for r in observation['observations'] if r['market'] == market]
        baskets = {d['group_code']: sorted(r['symbol'] for r in groups if r['market'] == market
                   and r['group_state'] == 'matched' and r['group_code'] == d['group_code']) for d in definitions}
        outputs = {d['group_code']: {'market': market, 'group_code': d['group_code'], 'group_name': d['group_name'],
                   'history': [], 'background': {k:d[k] for k in ('horizons','trend','breadth_sma60') if k in d}}
                   for d in definitions}
        # Background numbers from a later report must not leak into historical calls.
        if as_of:
            for v in outputs.values():
                v.pop('background', None)
        previous_members = {}
        for end in range(max(0, len(sessions)-c['history_days']), len(sessions)):
            dates = sessions[max(0,end-required+1):end+1]
            eligible, exclusions = cohort(prepared, dates)
            if len(dates) < required:
                exclusions = {s:'insufficient_market_sessions' for s in prepared}
                eligible = {}
            # All calculations for this date and both windows use the same eligible cohort.
            totals = [sum(bars[i]['close']*bars[i]['volume'] for bars in eligible.values()) for i in range(len(dates))]
            if market == 'KR':
                reference = equal_weight_index([[x['close'] for x in bars] for bars in eligible.values()])
                benchmark_issue = None if reference else 'reference_unavailable'
            else:
                bench = snapshot.get('benchmarks',{}).get('^GSPC')
                basis, benchmark_issue = series_basis(bench)
                stock_basis, _ = series_basis(next((r for r in snapshot['series'] if r['market']==market and r['symbol'] in eligible), None))
                benchmark_issue = benchmark_issue or comparison_issue(stock_basis, basis)
                clipped = {**bench,'bars':[x for x in bench['bars'] if x['date'] <= sessions[end]]} if bench else None
                idx, err = bar_index(clipped, sessions[:end+1])
                benchmark_issue = benchmark_issue or err
                if not benchmark_issue and any(d not in idx or not number(idx[d].get('close'),True) for d in dates):
                    benchmark_issue = 'benchmark_missing_session_or_invalid_close'
                reference = [idx[d]['close'] for d in dates] if not benchmark_issue else None
            for code, out in outputs.items():
                members = [s for s in baskets[code] if s in eligible]
                if len(dates) >= required:
                    m, leaders, weak, stock_metrics = metric(members, eligible, totals, reference, c)
                else:
                    m, leaders, weak, stock_metrics = {}, [], [], {}
                issues = []
                if len(members) < c['min_members']: issues.append('구성 종목 부족')
                coverage = len(members)/len(baskets[code]) if baskets[code] else 0
                market_coverage = len(eligible)/len(universe) if universe else 0
                if coverage < c['min_coverage']: issues.append('섹터 유효 자료 비율 부족')
                if market_coverage < c['min_coverage']: issues.append('시장 유효 자료 비율 부족')
                if len(dates) < required: issues.append('관측 기간 부족')
                if benchmark_issue: issues.append('시장 기준 가격 결측/기준 불일치')
                if members and m.get('activity_used',0)/len(members) < c['min_coverage']: issues.append('거래활동 유효 자료 부족')
                m.update(issues=issues, date=sessions[end], lookback_start=dates[0],
                         current_start=dates[-n] if len(dates)>=n else None,
                         previous_start=dates[-2*n] if len(dates)>=2*n else None,
                         used=len(members), matched=len(baskets[code]), coverage=coverage,
                         market_used=len(eligible), market_target=len(universe), market_coverage=market_coverage,
                         cohort_hash=hashlib.sha256('|'.join(members).encode()).hexdigest(),
                         market_cohort_hash=hashlib.sha256('|'.join(sorted(eligible)).encode()).hexdigest())
                prior_members = previous_members.get(code)
                changes = len(set(members)^set(prior_members))/max(1,len(set(members)|set(prior_members))) if prior_members is not None else 0
                m['cohort_changed'] = prior_members is not None and set(members)!=set(prior_members)
                m['cohort_change_fraction'] = changes
                if changes > c['cohort_change_warning']: issues.append('비교 구성 급변: 상태 전환 해석 보류')
                previous_members[code] = members
                m['state'] = classify(m,c)
                m['label'] = LABELS[m['state']]
                m['summary'] = explain(m,c)
                out['history'].append(m)
                if end == len(sessions)-1:
                    out.update(current=m, members=members, leaders=leaders, weak_contributors=weak, stock_metrics=stock_metrics,
                        exclusions={s:exclusions.get(s,'not_in_current_universe') for s in baskets[code] if s not in eligible})
            if end == len(sessions)-1:
                result['markets'][market] = {'as_of':sessions[end], 'used':len(eligible), 'target':len(universe),
                    'unclassified_used':sum(group_lookup.get((market,s),{}).get('group_state') != 'matched' for s in eligible),
                    'excluded_reasons':dict(Counter(exclusions.values())), 'exclusions':exclusions,
                    'reference':'대상 전체 동일비중' if market=='KR' else 'SPY 프록시',
                    'currency':'KRW' if market=='KR' else 'USD',
                    'source_complete':snapshot.get('data_complete', observation.get('market_data_complete'))}
        for out in outputs.values():
            if not out['history']:
                out.update(current={'state':'unavailable','label':LABELS['unavailable'],'issues':['완료 세션 없음']}, members=[],leaders=[],weak_contributors=[],exclusions={})
            hist = out['history']
            transitions = [{'date':v['date'], 'from':hist[i-1]['label'], 'to':v['label'],
                            'cohort_changed':v['cohort_changed']} for i,v in enumerate(hist) if i and v['state']!=hist[i-1]['state']]
            out['transitions'] = transitions
            out['changed_today'] = bool(len(hist)>1 and hist[-1]['state']!=hist[-2]['state'])
            out['previous_label'] = hist[-2]['label'] if len(hist)>1 else '비교 이력 없음'
            out['state_since'] = transitions[-1]['date'] if transitions else (hist[0]['date'] if hist else None)
            out['state_since_is_lower_bound'] = not bool(transitions)
            result['sectors'].append(out)
    return result


def attach_candidates(result, review, groups, chart_result):
    """Attach full independent candidate lists, without changing their membership."""
    names = {(r['market'],r['symbol']):r.get('name') or r['symbol'] for r in groups}
    classifications = {(r['market'],r['symbol']):r.get('group_code') for r in groups if r['group_state']=='matched'}
    charts = {(r['market'],r['symbol']):r for r in chart_result['rows']}
    for sector in result['sectors']:
        market = sector['market']
        entries = review['markets'][market]['entries']
        sector['candidates'] = [{k:e[k] for k in ('symbol','name','origins','reasons','selected','selected_by') if k in e}
                                for e in entries if classifications.get((market,e['symbol']))==sector['group_code']]
        by_symbol = {e['symbol']:e for e in entries}
        for row in [*sector['leaders'], *sector['weak_contributors'], *sector['candidates']]:
            symbol = row['symbol']
            row.update(sector.get('stock_metrics',{}).get(symbol,{}))
            row['name'] = names.get((market,symbol),symbol)
            row['origins'] = by_symbol.get(symbol,{}).get('origins',[])
            chart = charts.get((market,symbol),{})
            row['chart'] = chart.get('path') if chart.get('state')=='available' else None
            row['chart_status'] = 'available' if row['chart'] else chart.get('reason','우선 차트 대상 아님')
    return result


def esc(v):
    return html.escape(str(v), quote=True)


def fmt(v, unit='%'):
    if v is None: return '자료 없음'
    if unit == '%': return f'{v*100:.1f}%'
    if unit == '%p': return f'{v*100:+.2f}%p'
    return f'{v:.2f}배'


def filename(s):
    return 'sector-'+s['market']+'-'+hashlib.sha256(str(s['group_code']).encode()).hexdigest()[:12]+'.html'


def reasons(s):
    m = s['current']
    lines = [f"거래활동 {fmt(m.get('activity_previous'),'배')} → {fmt(m.get('activity_now'),'배')}",
             f"추정 거래 비중 {fmt(m.get('share_previous'))} → {fmt(m.get('share_now'))}",
             f"20일 상대강도 변화 {fmt(m.get('rs_delta'),'%p')} · 최근 수익률 {fmt(m.get('return_recent'))}",
             f"상승 참여 {fmt(m.get('breadth_previous'))} → {fmt(m.get('breadth'))}"]
    if m.get('return_recent',0) is not None and m.get('return_recent',0)<0:
        lines.append('최근 절대 가격은 하락: 상대강도 개선과 구분해서 확인')
    if m.get('cohort_changed'): lines.append('직전 관측과 유효 구성 변경: 상세 분모 확인')
    if m.get('sudden_activity'): lines.append('당일 거래 급변: 지속 강화 여부는 별도 확인')
    if m.get('concentrated'): lines.append('상위 3종목 거래 비중 '+fmt(m.get('top3_share'))+' · 상승 참여가 좁음')
    if s['current']['state']=='surge_weak': lines.append('매도 압력 가능성 관찰 · 실제 순매도 확인 아님')
    return lines + m.get('issues',[])


CSS = '''body{font-family:system-ui,sans-serif;background:#f4f6fa;color:#15253d;margin:0;line-height:1.6}
main{max-width:1240px;margin:auto;padding:26px}a{color:#1553b7}nav{margin-bottom:18px}.muted{color:#516179}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px}.card{background:white;border:1px solid #d7deea;border-top:5px solid #7a8ba3;border-radius:12px;padding:20px}
.strengthening{border-top-color:#137b68}.surge_weak{border-top-color:#c44b40}.strong_cooling{border-top-color:#366dc3}
.badge{display:inline-block;background:#eef2f8;padding:3px 9px;border-radius:20px;font-size:13px;margin:3px}.changed{background:#fff0c2}
h1{font-size:30px}h2{margin-top:30px}h3{margin:8px 0}ul{padding-left:20px}.notice{background:#fff3d6;padding:12px;border-radius:8px}
button{background:white;border:1px solid #bac7db;border-radius:18px;padding:8px 12px;margin:4px;cursor:pointer}button[aria-pressed=true]{background:#183a62;color:white}
table{border-collapse:collapse;width:100%;background:white;font-size:14px}th,td{padding:10px;border-bottom:1px solid #dce2ec;text-align:left}details{margin:20px 0}summary{cursor:pointer;font-weight:bold}
.scroll{overflow:auto}svg{width:100%;height:auto;background:white;border-radius:8px}.chart-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:16px}
.chart-grid h3{font-size:16px}footer{margin-top:32px;font-size:13px;color:#516179}[hidden]{display:none!important}
'''


def shell(title, body, script=''):
    return '<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+esc(title)+'</title><style>'+CSS+'</style></head><body><main>'+body+'</main>'+script+'</body></html>'


def cards(result, prefix='', limit=None):
    body = ''
    for market in ('KR','US'):
        info=result['markets'].get(market,{})
        body+=f'<section><h2>{"한국" if market=="KR" else "미국"} · {esc(info.get("as_of","자료 없음"))}</h2>'
        body+=f'<p class="muted">완료 일봉 · 유효 {info.get("used",0)}/{info.get("target",0)}종목 · 분모 내 미분류 {info.get("unclassified_used",0)}종목</p><div class="grid">'
        rows=sorted((s for s in result['sectors'] if s['market']==market),key=lambda s:(not s['changed_today'], ORDER[s['current']['state']],s['group_name']))
        if limit: rows=rows[:limit]
        for s in rows:
            m=s['current']; state=m['state']
            body+=f'<article class="card {state}" data-state="{state}" data-changed="{str(s["changed_today"]).lower()}" data-concentrated="{str(bool(m.get("concentrated"))).lower()}">'
            body+=f'<span class="badge">{esc(m["label"])}</span>'
            if s['changed_today']: body+='<span class="badge changed">상태 변경</span>'
            if m.get('concentrated'): body+='<span class="badge">소수 종목 집중</span>'
            body+=f'<h3><a href="{prefix+filename(s)}">{esc(s["group_name"])}</a></h3>'
            body+='<p><strong>'+esc(m.get('summary','자료 부족'))+'</strong></p>'
            body+=f'<div class="muted">{esc(s["previous_label"])} → {esc(m["label"])}</div><ul>'
            body+=''.join('<li>'+esc(x)+'</li>' for x in reasons(s))+'</ul>'
            leaders=', '.join(stock_label(r,market) if market == 'KR' else r['symbol'] for r in s['leaders']) or '자료 없음'
            body+=f'<p class="muted">거래 주도: {esc(leaders)}</p><a href="{prefix+filename(s)}">추이·종목·후보 확인 →</a></article>'
        body+='</div><p class="empty" hidden>해당 조건의 섹터가 없습니다.</p></section>'
    return body


def chart(history, key, title, unit='%'):
    valid=[(i,r.get(key)) for i,r in enumerate(history) if r.get(key) is not None]
    if not valid:return '<div><h3>'+esc(title)+'</h3><p>관측 이력 없음</p></div>'
    values=[v for _,v in valid]; low=min(values); high=max(values)
    pad=max((high-low)*.1, .01 if unit=='%' else .1); low-=pad; high+=pad
    def xy(i,v):return f'{70+i/max(1,len(history)-1)*490:.1f},{155-(v-low)/(high-low)*120:.1f}'
    segments=[]; points=[]; prior=-2
    for i,v in valid:
        if i!=prior+1 and points:segments.append(points);points=[]
        points.append(xy(i,v));prior=i
    if points:segments.append(points)
    lines=''.join('<polyline fill="none" stroke="#2567b4" stroke-width="2.5" points="'+' '.join(p)+'"/>' for p in segments)
    dots=''.join(f'<circle cx="{xy(i,v).split(",")[0]}" cy="{xy(i,v).split(",")[1]}" r="2.7" fill="#2567b4"><title>{esc(history[i]["date"]+": "+fmt(v,unit)+" / "+history[i]["label"])}</title></circle>' for i,v in valid)
    return f'<div><h3>{esc(title)}</h3><svg viewBox="0 0 600 200" role="img" aria-label="{esc(title)}"><text x="5" y="35" font-size="13">{esc(fmt(high,unit))}</text><text x="5" y="155" font-size="13">{esc(fmt(low,unit))}</text>{lines}{dots}<text x="70" y="184" font-size="12">{history[0]["date"]}</text><text x="480" y="184" font-size="12">{history[-1]["date"]}</text></svg></div>'


def stock_table(rows, kind, market=None):
    if not rows:return '<p>해당 종목 없음</p>'
    text='<div class="scroll"><table><thead><tr><th>종목</th><th>최근 수익률</th><th>섹터 내 추정 거래 비중</th><th>후보 경로·근거</th><th>차트</th></tr></thead><tbody>'
    for r in rows:
        origins=' · '.join({'sperandeo':'스페란데오','taver':'테이버','pdf_momentum':'추세, 조정, 돌파','base_watch':'바닥·눌림 횡보'}.get(v,v) for v in r.get('origins',[])) or '패턴 후보 아님'
        reason='; '.join(r.get('reasons',[])) if kind=='candidate' else ''
        link='<a href="../'+esc(r['chart'])+'">저장 차트</a>' if r.get('chart') else esc(r.get('chart_status','우선 차트 대상 아님'))
        text+=f'<tr><td>{esc(stock_label(r,market))}</td><td>{fmt(r.get("return_recent"))}</td><td>{fmt(r.get("proxy_share"))}</td><td>{esc(origins)}<br>{esc(reason)}</td><td>{link}</td></tr>'
    return text+'</tbody></table></div>'


def render(result, output):
    output=Path(output)
    c=result['config']
    notice=('현재 분류·현재 제공 대상의 완료 일봉 관찰입니다. 추정 거래 비중은 종가×수정 거래량 기반이며 실제 순유입액이 아닙니다. '
            '원자료 일부 결측·미분류와 NH 보완분의 섹터 집계 제외를 유지합니다. 장중 신호·수익성 검증이 아닙니다.')
    body='<nav><a href="../report.html">일일 보고서</a> · <a href="metrics.html">전체 수치·분모</a> · <a href="signals.json">계산·상태 원자료</a></nav><h1>어느 섹터의 관심이 달라졌나</h1>'
    body+=f'<p>최근 {c["comparison_days"]}거래일과 직전 {c["comparison_days"]}거래일 비교 · 상태 변경 먼저, 그다음 상태별 표시 · 합성 점수 없음</p><p class="notice">{notice}</p><p class="muted">자료 기준일은 아래 국가별 표시를 따릅니다. 화면을 열어도 새 시세를 수집하지 않습니다. 분류 적용 시각: {esc(result.get("classification_applied_at"))}</p>'
    options=[('all','전체'),('changed','상태 변경'),('concentrated','소수 종목 집중'),*LABELS.items()]
    body+='<div id="filters" aria-label="상태 필터">'+''.join(f'<button type="button" data-filter="{k}" aria-pressed="{str(k=="all").lower()}">{v}</button>' for k,v in options)+'</div>'
    body+=cards(result)
    body+='<footer>활동 둔화만으로 약세를 판정하지 않습니다. 각 카드에서 가격·확산·집중과 관측 기준을 함께 확인하세요.</footer>'
    script='''<script>document.querySelectorAll('[data-filter]').forEach(b=>b.addEventListener('click',()=>{document.querySelectorAll('[data-filter]').forEach(x=>x.setAttribute('aria-pressed',String(x===b)));document.querySelectorAll('[data-state]').forEach(x=>x.hidden=!(b.dataset.filter==='all'||(b.dataset.filter==='changed'?x.dataset.changed==='true':b.dataset.filter==='concentrated'?x.dataset.concentrated==='true':x.dataset.state===b.dataset.filter)));document.querySelectorAll('section').forEach(s=>s.querySelector('.empty').hidden=!!s.querySelector('[data-state]:not([hidden])'));}));</script>'''
    (output/'observation.html').write_text(shell('섹터 상태 · 변화 관찰',body,script),encoding='utf-8')
    for s in result['sectors']:
        m=s['current']; hist=s['history']
        body='<nav><a href="observation.html">← 섹터 상태</a> · <a href="../report.html">일일 보고서·후보</a> · <a href="metrics.html">기존 전체 수치</a></nav>'
        body+=f'<h1>{esc(s["group_name"])} · {esc(m["label"])}</h1><p>기준 {esc(m.get("date","자료 없음"))} · 현재 상태 {esc(s["state_since"])}{ "부터 관측(그 이전 미확인)" if s["state_since_is_lower_bound"] else "부터"}</p>'
        body+='<p><strong>'+esc(m.get('summary','자료 부족'))+'</strong></p>'
        if m.get('concentrated'):body+='<span class="badge">소수 종목 집중</span>'
        body+='<ul>'+''.join('<li>'+esc(x)+'</li>' for x in reasons(s))+'</ul>'
        body+=f'<p class="notice">{notice}</p><div class="chart-grid">'
        for key,title,unit in [('activity_now','평소 대비 거래활동 · 최근 구간 평균','배'),('share_now','시장 내 추정 거래 비중','%'),('rs_now','시장 대비 상대강도','%'),('return_recent','최근 구간 절대 수익률','%'),('breadth','최근 상승 참여','%'),('top3_share','상위 3종목 추정 거래 비중','%')]:
            body+=chart(hist,key,title,unit)
        body+='</div><h2>상태 변경 기록</h2>'
        body+='<ul>'+''.join('<li>'+esc(r['date']+' · '+r['from']+' → '+r['to']+(' · 구성 변경 동반' if r['cohort_changed'] else ''))+'</li>' for r in reversed(s['transitions']))+'</ul>' if s['transitions'] else '<p>표시 기간 내 상태 변경 없음</p>'
        body+='<h2>거래 주도 종목</h2><p>추정 거래대금 순서입니다. 기존 패턴 후보가 아닌 종목도 포함합니다.</p>'+stock_table(s['leaders'],'leader',s['market'])
        body+='<h2>가격 약세 기여 종목</h2><p>최근 구간 개별 수익률이 낮은 순서입니다. 동일비중 기초금액 기준 기여 참고이며 복리 섹터 지수의 정확한 기여도 분해가 아닙니다.</p>'+stock_table(s['weak_contributors'],'weak',s['market'])
        body+='<h2>이 섹터의 독립 패턴 후보</h2><p>전체 후보를 보존합니다. 차트는 기존 우선 선정 종목에 제공됩니다.</p>'+stock_table(s.get('candidates',[]),'candidate',s['market'])
        body+='<details><summary>계산 기간·분모·판정 기준·일별 근거</summary>'
        body+=f'<p>계산 시작 {esc(m.get("lookback_start"))} · 직전 구간 시작 {esc(m.get("previous_start"))} · 최근 구간 시작 {esc(m.get("current_start"))}. 섹터 {m.get("used",0)}/{m.get("matched",0)}, 시장 {m.get("market_used",0)}/{m.get("market_target",0)}종목. 매 관측일 두 비교 구간은 같은 구성으로 계산합니다. 날짜 사이 구성 변경은 별도 표시합니다.</p>'
        body+='<p>기본 판정: 활동 변화 ±'+fmt(c['activity_change'])+'; 비중 변화 '+fmt(c['share_change'])+' 및 '+fmt(c['share_change_floor'],'%p')+' 이상; RS 변화 '+fmt(c['rs_change'],'%p')+' 이상. 거래 급증 '+fmt(c['surge_level'],'배')+' 이상 및 가격 '+fmt(c['price_weakness'])+' 이하. 강세 유지는 RS 양수·지수 추세선 위·추세선 상승을 함께 요구합니다.</p>'
        body+='<p>설정·종목별 제외 이유·현재 구성은 <a href="signals.json">signals.json</a>에 보존합니다. 임계값은 관찰용 초기 정책이며 수익률 최적화 결과가 아닙니다.</p><div class="scroll"><table><thead><tr><th>일자</th><th>상태</th><th>활동</th><th>비중</th><th>RS</th><th>참여</th><th>사용 종목</th></tr></thead><tbody>'
        body+=''.join(f'<tr><td>{r["date"]}</td><td>{esc(r["label"])}</td><td>{fmt(r.get("activity_now"),"배")}</td><td>{fmt(r.get("share_now"))}</td><td>{fmt(r.get("rs_now"))}</td><td>{fmt(r.get("breadth"))}</td><td>{r["used"]}/{r["matched"]}</td></tr>' for r in reversed(hist))+'</tbody></table></div></details>'
        (output/filename(s)).write_text(shell(s['group_name']+' · 섹터 상세',body),encoding='utf-8')
    write(output/'signals.json',result)


def verify_saved(output, expected):
    """Verify semantics plus the exact deterministic rendered pages."""
    import json
    import tempfile
    output=Path(output)
    saved=json.loads((output/'signals.json').read_text(encoding='utf-8'))
    if saved != expected: raise ValueError('sector_signals_replay_mismatch')
    with tempfile.TemporaryDirectory() as tmp:
        render(expected,Path(tmp))
        for name in ['observation.html',*[filename(s) for s in expected['sectors']]]:
            if (Path(tmp)/name).read_bytes() != (output/name).read_bytes():
                raise ValueError('sector_state_page_replay_mismatch:'+name)
    return dict(Counter(s['current']['state'] for s in expected['sectors']))
