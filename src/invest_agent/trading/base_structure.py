"""Price-base observations independent of momentum trend and RS gates."""
from math import isfinite
from statistics import mean
from . import momentum_entry as entry
from .momentum_watch import signal

DEFAULTS = {'max_depth': .22, 'max_drift': .08, 'max_drift_share': .35, 'max_recent_decline': .03, 'max_risk': .20,
            'floor_tolerance': .03, 'min_decline': .15, 'min_advance': .20,
            'min_pullback': .04, 'max_pullback': .20, 'max_extension': .02}
WINDOWS = (15, 20, 30, 40, 60, 90)
LABELS = {'bottom_base': '하락 후 바닥 횡보', 'pullback_base': '상승 후 눌림 횡보'}

def settings(overrides=None):
    cfg = {**DEFAULTS, **(overrides or {})}
    if set(cfg) != set(DEFAULTS): raise ValueError('unknown_base_setting')
    for key, value in cfg.items():
        if type(value) not in (int, float) or not isfinite(value) or not 0 < value < 1:
            raise ValueError('invalid_base_setting:' + key)
    if cfg['min_pullback'] >= cfg['max_pullback']: raise ValueError('invalid_pullback_range')
    return cfg

def rank(s):
    # Closest intact floor first, then more observed support history. Not a return score.
    return (s['structure_risk_pct'], -s['metrics']['base_bars'], s['kind'])

def analyze(raw, overrides=None, as_of=None, market=None):
    cfg = settings(overrides); bars = entry.completed(raw, as_of)
    result = {'signals': [], 'windows': [], 'settings': cfg}
    if len(bars) < 142:
        result['reason'] = 'insufficient_base_history'; return result
    end = len(bars)-1; current = bars[-1]; c = current['close']
    recent_drift = mean(b['close'] for b in bars[-5:])/mean(b['close'] for b in bars[-10:-5])-1
    for size in WINDOWS:
        start = end-size
        if start < 126: continue
        base = bars[start:end]; prior = bars[start-126:start]
        lo = min(b['low'] for b in base); hi = max(b['high'] for b in base)
        mid = size//2; left = base[:mid]; right = base[mid:]
        drift = mean(b['close'] for b in base[-5:])/mean(b['close'] for b in base[:5])-1
        depth = 1-lo/hi; risk = 1-lo/c
        drift_share = abs(mean(b['close'] for b in base[-5:])-mean(b['close'] for b in base[:5]))/(hi-lo) if hi>lo else 1.
        first_low = min(b['low'] for b in left); last_low = min(b['low'] for b in right)
        # Require separated tests with a meaningful rebound between them.
        tolerance = min(cfg['floor_tolerance'], depth/4)
        touches = [i for i,b in enumerate(base) if b['low'] <= lo*(1+tolerance)]
        tests = [(a,b) for a in touches for b in touches if b-a >= 5
                 and max(x['high'] for x in base[a:b+1]) >= lo*(1+2*tolerance)]
        peak_i = max(range(len(prior)), key=lambda i: prior[i]['high'])
        peak = prior[peak_i]['high']
        decline = 1-mean(b['close'] for b in base[:5])/peak
        advance = peak/min(b['low'] for b in prior[:peak_i+1])-1
        # A historic larger peak must not hide a more recent rise/pullback cycle.
        pullback_peaks = []
        for lookback in (20,40,63,126):
            pi = max(range(126-lookback,126),key=lambda i:prior[i]['high'])
            ph = prior[pi]['high']
            rise = ph/min(b['low'] for b in prior[:pi+1])-1
            drop = 1-mean(b['close'] for b in base[:5])/ph
            if pi >= 63 and rise >= cfg['min_advance'] and cfg['min_pullback'] <= drop <= cfg['max_pullback']:
                pullback_peaks.append((pi,ph,rise,drop))
        pullback = bool(pullback_peaks)
        bottom = (decline >= cfg['min_decline'] and peak_i <= 115
                  and mean(b['close'] for b in prior[-5:]) < mean(b['close'] for b in prior[-20:-15]))
        if pullback:
            peak_i,peak,advance,decline = max(pullback_peaks,key=lambda x:x[0])
        checks = {
            'preceding_move': pullback or bottom,
            'bounded_range': .02 <= depth <= cfg['max_depth'],
            'sideways_closes': abs(drift) <= cfg['max_drift'],
            'not_directional_range': drift_share <= cfg['max_drift_share'],
            'recent_stabilization': recent_drift >= -cfg['max_recent_decline'],
            'floor_stable': last_low >= first_low*(1-cfg['floor_tolerance']),
            'range_not_expanding': entry.depth(right) <= entry.depth(left)*1.2,
            'repeated_support': bool(tests),
            'floor_intact': current['low'] >= lo and c > lo,
            'bounded_floor_distance': 0 < risk <= cfg['max_risk'],
            'not_chased': c <= hi*(1+cfg['max_extension']),
        }
        metrics = {'base_bars': size, 'depth': depth, 'close_drift': drift, 'drift_share_of_range': drift_share, 'recent_5v5_change': recent_drift,
                   'first_half_low': first_low, 'last_half_low': last_low,
                   'prior_peak': peak, 'prior_peak_date': prior[peak_i]['date'],
                   'preceding_decline': decline, 'advance_to_prior_peak': advance,
                   'support_test_dates': [base[i]['date'] for i in touches],
                   'separated_test_pairs': len(tests)}
        result['windows'].append({'base_bars': size, 'checks': checks,
                                  'rejections': [k for k,v in checks.items() if not v], 'metrics': metrics})
        if not all(checks.values()): continue
        kind = 'pullback_base' if pullback else 'bottom_base'
        setup = {'kind': kind, 'label': LABELS[kind], 'base_start': base[0]['date'],
                 'base_end': base[-1]['date'], 'known_at': base[-1]['date'],
                 'pivot': hi, 'structure_stop': lo, 'metrics': metrics, 'approximation': True,
                 'source_basis': 'base_watch_operational_assumptions'}
        warnings = ['횡보 하단은 관찰 기준이며 이탈·갭 하락 가능성은 남아 있습니다.']
        if not (market or {}).get('supportive'): warnings.append('시장 추세 미충족/결측')
        if c > hi: warnings.append('범위 상단 소폭 상회: 거래량·돌파 유지 미확인')
        s = signal(setup, bars, 'watch', 'base_watch',
                   ['횡보 하단 지지 유지', '상단 돌파·거래량·돌파 후 유지 확인'], warnings)
        s['range_upside_pct'] = max(0, hi/c-1)
        s['range_reward_risk'] = s['range_upside_pct']/risk
        s['range_ratio_basis'] = 'current_close_to_range_roof_over_current_close_to_floor_not_expected_return'
        result['signals'].append(s)
    result['signals'].sort(key=rank)
    return result
