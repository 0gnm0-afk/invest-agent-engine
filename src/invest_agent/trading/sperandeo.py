"""MVP downside trendline / upside 1-2-3 observations, never order levels."""
from .structure_math import atr, ratio

SPERANDEO_WINDOWS = (63, 126, 252)
SPERANDEO_PRIMARY_WINDOW = 126
PIVOT_LEFT = 2
PIVOT_RIGHT = 2
MIN_ANCHOR_GAP = 3
APPROACH_ATR_MULTIPLE = 0.5
BREAKOUT_PRICE = 'close'
EPSILON_RELATIVE = 1e-10
LABELS = {'S0': '하락 추세 진행', 'S1': '하락 추세선 접근', 'S2': '하락 추세선 돌파',
          'S3': '저점 재시험·최저점 갱신 실패', 'S4': '1-2-3 전환 완성'}


def pivots(bars, key):
    # Inclusive neighbours; plateau ties choose the earliest eligible pivot.
    result = []
    for i in range(PIVOT_LEFT, len(bars)-PIVOT_RIGHT):
        v = bars[i][key]
        neighbours = bars[i-PIVOT_LEFT:i+PIVOT_RIGHT+1]
        extreme = max(b[key] for b in neighbours) if key == 'high' else min(b[key] for b in neighbours)
        if v == extreme and not any(b[key] == v for b in bars[i-PIVOT_LEFT:i]):
            result.append(i)
    return result


def _structure(bars, reference_date=None):
    base = {'data_state': 'no_valid_trendline', 'stage': None, 'analysis_as_of': bars[-1]['date'],
            'stage_entered_at': None, 'bars_in_stage': None}
    a = max(range(len(bars)), key=lambda i: bars[i]['high'])
    if a >= len(bars)-1:
        return {**base, 'reason': 'no_bars_after_anchor_a'}
    low = min(range(a+1, len(bars)), key=lambda i: (bars[i]['low'], -i))
    latest_extreme = low
    # A completed breakout retains its observed reference through an equal-low
    # retest. Otherwise the most-recent-minimum tie would erase every equal test.
    if reference_date:
        prior = next((i for i in range(a+1,len(bars)) if bars[i]['date']==reference_date), None)
        if prior is not None and bars[prior]['low'] == bars[low]['low']:
            low = prior
    eps = max(b['high'] for b in bars)*EPSILON_RELATIVE
    b = None
    for index in reversed(pivots(bars, 'high')):
        if not a + MIN_ANCHOR_GAP <= index < low or bars[index]['high'] >= bars[a]['high']:
            continue
        slope = (bars[index]['high']-bars[a]['high'])/(index-a)
        if all(bars[j]['high'] <= bars[a]['high']+slope*(j-a)+eps for j in range(a, low+1)):
            b = index
            break
    if b is None:
        return {**base, 'reason': 'no_non_intersecting_confirmed_anchor_b'}
    line = lambda i: bars[a]['high']+slope*(i-a)
    atrs = atr(bars)
    latest = bars[-1]
    n = len(bars)-1
    output = {**base, 'data_state': 'available', 'reason': None,
        'anchor_high_a_date': bars[a]['date'], 'anchor_high_a_price': bars[a]['high'],
        'anchor_high_b_date': bars[b]['date'], 'anchor_high_b_price': bars[b]['high'],
        'anchor_b_confirmed_at': bars[b+PIVOT_RIGHT]['date'],
        'reference_low_date': bars[low]['date'], 'reference_low_price': bars[low]['low'],
        'latest_extreme_low_date': bars[latest_extreme]['date'],
        'slope_per_bar': slope, 'trendline_value_latest': line(n),
        'latest_close': latest['close'], 'latest_high': latest['high'],
        'distance_to_line_pct': ratio(latest['close']-line(n), line(n)),
        'distance_to_line_atr': ratio(latest['close']-line(n), atrs[-1]),
        'atr14': atrs[-1]}
    # A pivot cannot confer a signal before its right-hand candles complete.
    known_at = max(low, b+PIVOT_RIGHT)
    breakout = next((i for i in range(max(low+1, known_at), len(bars)) if bars[i]['close'] > line(i)), None)
    stage, entered = 'S0', known_at
    if breakout is None:
        def approaching(i):
            distance = ratio(line(i)-bars[i]['close'], atrs[i])
            return bars[i]['close'] < line(i) and (bars[i]['high'] >= line(i) or
                        distance is not None and distance <= APPROACH_ATR_MULTIPLE)
        stage = 'S1' if approaching(n) else 'S0'
        entered = n
        while entered > known_at and approaching(entered-1) == approaching(n):
            entered -= 1
    else:
        stage, entered = 'S2', breakout
        output.update(breakout_date=bars[breakout]['date'], breakout_close=bars[breakout]['close'],
            trendline_value_at_breakout=line(breakout), bars_since_breakout=n-breakout,
            breakout_margin_pct=ratio(bars[breakout]['close']-line(breakout), line(breakout)),
            breakout_margin_atr=ratio(bars[breakout]['close']-line(breakout), atrs[breakout]))
        retest = next((i for i in pivots(bars, 'low') if i > breakout and bars[i]['low'] >= bars[low]['low']-eps), None)
        if retest is not None:
            confirmed = retest+PIVOT_RIGHT
            stage, entered = 'S3', confirmed
            c = max(range(breakout, retest), key=lambda i: bars[i]['high'])
            output.update(retest_pivot_date=bars[retest]['date'], retest_confirmed_at=bars[confirmed]['date'],
                retest_low=bars[retest]['low'],
                retest_type='equal_low_test' if abs(bars[retest]['low']-bars[low]['low']) <= eps else 'higher_low_test',
                retest_vs_reference_pct=ratio(bars[retest]['low']-bars[low]['low'], bars[low]['low']),
                retest_vs_reference_atr=ratio(bars[retest]['low']-bars[low]['low'], atrs[retest]),
                completion_level=bars[c]['high'], completion_source_date=bars[c]['date'])
            completion = next((i for i in range(confirmed, len(bars)) if bars[i]['close'] > bars[c]['high']), None)
            if completion is not None:
                stage, entered = 'S4', completion
                output.update(completion_date=bars[completion]['date'], completion_close=bars[completion]['close'],
                              bars_since_completion=n-completion)
    output.update(stage=stage, stage_label=LABELS[stage], stage_entered_at=bars[entered]['date'], bars_in_stage=n-entered)
    return output


def window(bars, period):
    if len(bars) < period:
        return {'window': period, 'data_state': 'unavailable_insufficient_bars', 'stage': None,
                'available_bars': len(bars), 'analysis_as_of': bars[-1]['date'] if bars else None,
                'stage_entered_at': None, 'bars_in_stage': None}
    selected = bars[-period:]
    invalidations = []
    # Evaluate historical prefixes only to retain observed failed attempts.
    # Current A/L/B are always recomputed on the full current window.
    previous = {}
    for end in range(MIN_ANCHOR_GAP+PIVOT_RIGHT+2, len(selected)+1):
        bar = selected[end-1]
        if previous.get('stage') in ('S2', 'S3') and bar['low'] < previous['reference_low_price']*(1-EPSILON_RELATIVE):
            invalidations.append({'invalidation_date': bar['date'], 'invalidation_reason': 'new_lower_low',
                                  'reference_low_date': previous['reference_low_date']})
        reference = previous.get('reference_low_date') if previous.get('stage') in ('S2','S3','S4') else None
        previous = _structure(selected[:end], reference)
    current = previous
    return {'window': period, **current, 'invalidated': bool(invalidations),
            'invalidation_date': invalidations[-1]['invalidation_date'] if invalidations else None,
            'invalidation_reason': 'new_lower_low' if invalidations else None, 'invalidations': invalidations}


def analyze(bars):
    windows = {str(p): window(bars, p) for p in SPERANDEO_WINDOWS}
    reasons = [f'sperandeo_{p}_{v["stage"]}' for p, v in windows.items() if v['stage'] in ('S1','S2','S3','S4')]
    return {'version': 'v1', 'primary_window': SPERANDEO_PRIMARY_WINDOW, 'windows': windows,
            'candidate': bool(reasons), 'candidate_reasons': reasons}


def sort_key(row):
    active = [(p, w) for p, w in row.get('sperandeo', {}).get('windows', {}).items()
              if w.get('stage') in ('S1','S2','S3','S4')]
    from datetime import date
    newest = max((date.fromisoformat(w['stage_entered_at']).toordinal() for _, w in active), default=0)
    priority = min(({'126': 0, '63': 1, '252': 2}[p] for p, _ in active), default=3)
    return (-newest, priority, row['symbol'])
