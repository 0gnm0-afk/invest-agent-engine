"""Fixed-cohort sector calculations adapted to explicit synthetic series only."""
import math
import statistics

def number(value, positive=False):
    return (not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
            and (value > 0 if positive else value >= 0))


def bar_index(series, sessions):
    if series is None:
        return {}, 'series_missing'
    if series.get('price_basis') != 'synthetic':
        return {}, 'synthetic_basis_required'
    bars = series.get('bars', [])
    if any(b.get('complete') is False for b in bars):
        return {}, 'incomplete_bar'
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


def group_metrics(members, series, sessions, benchmark):
    indexes = {r['symbol']: bar_index(series.get((r['market'], r['symbol'])), sessions) for r in members}
    bench, bench_issue = bar_index(benchmark, sessions)
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
        rs_issue = reason
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
                         'basis': 'volume_today_divided_by_prior20_mean_no_today_in_denominator',
                         'mean_ratio': statistics.fmean(ratios) if ratios else None,
                         'median_ratio': statistics.median(ratios) if ratios else None,
                         'ratios': dict(zip(activity_used, ratios))}}

