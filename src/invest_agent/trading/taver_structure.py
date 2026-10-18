"""As-of structural observations, not a reconstruction of discretionary drawings.

All fits use completed bars at/before as_of. Historical line distances are
retrospective descriptions of today's fit, never historical entry signals.
"""
from copy import deepcopy
import math
import numpy as np

DEFAULTS = {
    'windows': [252, 378, 504, 756, 1260], 'primary_window': 504,
    'pivot_radius': 5, 'min_anchor_span': 100, 'near_atr': 1.5,
    'max_distance_pct': .08, 'min_rise': .15, 'min_r2': .35,
    'min_pullback_atr': 2., 'deep_break_atr': 3., 'recent_bars': 20,
    'short_window': 63, 'min_short_r2': .2,
}


def settings(overrides=None):
    cfg = deepcopy(DEFAULTS)
    if set(overrides or {}) - set(cfg):
        raise ValueError('unknown_taver_structure_settings')
    cfg.update(overrides or {})
    if (not isinstance(cfg['windows'], list) or not cfg['windows'] or
        any(type(w) is not int or w < 126 for w in cfg['windows']) or
        sorted(set(cfg['windows'])) != cfg['windows'] or cfg['primary_window'] not in cfg['windows']):
        raise ValueError('invalid_taver_windows')
    for k in ('pivot_radius','min_anchor_span','recent_bars','short_window'):
        if type(cfg[k]) is not int or cfg[k] < 1:
            raise ValueError('invalid_taver_setting:'+k)
    if cfg['short_window'] < 3 or cfg['short_window'] >= min(cfg['windows']) or cfg['pivot_radius']*2 >= min(cfg['windows']):
        raise ValueError('invalid_taver_short_window')
    for k in ('near_atr','max_distance_pct','min_rise','min_r2','min_pullback_atr','deep_break_atr','min_short_r2'):
        if type(cfg[k]) not in (float,int) or not math.isfinite(cfg[k]) or cfg[k] <= 0:
            raise ValueError('invalid_taver_setting:'+k)
    if cfg['min_r2'] > 1 or cfg['min_short_r2'] > 1 or cfg['deep_break_atr'] < cfg['near_atr']:
        raise ValueError('invalid_taver_thresholds')
    return cfg


def regression(values):
    y = np.asarray(values, dtype=float)
    x = np.arange(len(y), dtype=float)
    slope, intercept = np.polyfit(x, y, 1)
    residual = y-(intercept+slope*x)
    total = float(np.sum((y-y.mean())**2))
    r2 = max(0., 1-float(np.sum(residual**2))/total) if total else 0.
    return float(intercept), float(slope), r2


def spaced(indices, gap=20):
    result = []
    for i in indices:
        if not result or i-result[-1] >= gap:
            result.append(int(i))
    return result


def line_metrics(bars, intercept, slope, volatility, cfg, upper_offset=None):
    n = len(bars)
    values = intercept + slope*np.arange(n)
    close = np.array([b['close'] for b in bars])
    low = np.array([b['low'] for b in bars])
    signed = close-values
    recent = signed[-cfg['recent_bars']:]
    below = np.where(recent < 0)[0]
    distance = float(signed[-1]/volatility)
    value = float(values[-1])
    position = ('below' if signed[-1] < 0 else 'reclaimed' if len(below) else
                'wick_breach' if np.any(low[-cfg['recent_bars']:] < values[-cfg['recent_bars']:]) else 'above')
    near = abs(distance) <= cfg['near_atr'] and value > 0 and abs(close[-1]/value-1) <= cfg['max_distance_pct']
    return {'intercept':float(intercept), 'slope_per_bar':float(slope),
            'start':bars[0]['date'], 'end':bars[-1]['date'], 'known_at':bars[-1]['date'],
            'lower_latest':value, 'upper_latest':value+upper_offset if upper_offset is not None else None,
            'upper_offset':upper_offset, 'distance_atr':distance,
            'distance_pct':float(close[-1]/value-1) if value>0 else None,
            'channel_position':float(signed[-1]/upper_offset) if upper_offset else None,
            'position':position, 'near':bool(near),
            'recent_close_below_bars':int(len(below)),
            'recent_max_breach_atr':float(max(0., -min(recent)/volatility)),
            'window_close_below_fraction':float(np.mean(signed < 0)),
            'retrospective_close_below_dates':[b['date'] for b,d in zip(bars,signed) if d < 0],
            'excluded_bars':0, 'basis':'as_of_fit_not_historical_signals'}


def fit_window(bars, window, volatility, cfg):
    if len(bars) < window:
        return {'state':'insufficient_history','required_bars':window,'available_bars':len(bars)}
    bars = bars[-window:]
    close = np.array([b['close'] for b in bars])
    lows = np.array([b['low'] for b in bars])
    highs = np.array([b['high'] for b in bars])
    x = np.arange(window)
    intercept,slope,r2 = regression(close)
    rise = slope*(window-1)/float(close[0])
    low_offset = float(np.quantile(lows-(intercept+slope*x), .10))
    high_offset = float(np.quantile(highs-(intercept+slope*x), .90))
    width = high_offset-low_offset
    lower_touches = spaced(np.where(abs(lows-(intercept+low_offset+slope*x)) <= volatility)[0])
    upper_touches = spaced(np.where(abs(highs-(intercept+high_offset+slope*x)) <= volatility)[0])
    channel = line_metrics(bars,intercept+low_offset,slope,volatility,cfg,width)
    channel.update(method='linear_ols_residual_10_90',r2=r2, rise_fraction=rise,
                   lower_touch_dates=[bars[i]['date'] for i in lower_touches],
                   upper_touch_dates=[bars[i]['date'] for i in upper_touches])
    channel['shape_valid'] = bool(slope>0 and rise>=cfg['min_rise'] and r2>=cfg['min_r2'] and
        len(lower_touches)>=3 and len(upper_touches)>=3 and width>=2*volatility and
        lower_touches[-1]-lower_touches[0]>=cfg['min_anchor_span'])
    # Independently fit a rising support through confirmed swing lows. Select on
    # whole-window fit (violations/touches/span), never latest-price proximity.
    radius = cfg['pivot_radius']
    pivots = [i for i in range(radius,window-radius) if lows[i] < min(lows[i-radius:i]) and lows[i] <= min(lows[i+1:i+radius+1])]
    sampled = sorted(set(pivots[int(i)] for i in np.linspace(0,len(pivots)-1,min(24,len(pivots))))) if pivots else []
    options = []
    for a in sampled:
        for b in sampled:
            if b-a < max(cfg['min_anchor_span'],window//3) or lows[b] <= lows[a]:
                continue
            m = float((lows[b]-lows[a])/(b-a)); c = float(lows[a]-m*a)
            if c<=0 or m*(window-1)/float(close[0])<cfg['min_rise']:
                continue
            residual = close-(c+m*x)
            violations = float(np.mean(residual < -volatility))
            touches = spaced(np.where(abs(lows-(c+m*x))<=volatility)[0])
            if violations>.15 or len(touches)<3:
                continue
            options.append(((violations,-len(touches),-(b-a),a,b),a,b,c,m,touches))
    support = {'state':'no_valid_anchors','shape_valid':False,'confirmed_pivots':len(pivots),'sampled_pivots':len(sampled)}
    if options:
        _,a,b,c,m,touches = min(options,key=lambda o:o[0])
        support = line_metrics(bars,c,m,volatility,cfg)
        support.update(state='available',shape_valid=bool(rise>=cfg['min_rise'] and r2>=cfg['min_r2']),
                       method='confirmed_low_pair',r2=r2,rise_fraction=m*(window-1)/float(close[0]),
                       anchors=[{'date':bars[i]['date'],'price':float(lows[i]),'confirmed_at':bars[i+radius]['date']} for i in (a,b)],
                       lower_touch_dates=[bars[i]['date'] for i in touches],
                       confirmed_pivots=len(pivots),sampled_pivots=len(sampled))
    short = bars[-cfg['short_window']:]
    si,ss,sr = regression([b['close'] for b in short])
    short_line = {'start':short[0]['date'],'end':short[-1]['date'],'intercept':si,'slope_per_bar':ss,'r2':sr,
                  'descending':ss<0 and sr>=cfg['min_short_r2'], 'known_at':bars[-1]['date']}
    pullback = float((max(highs[-cfg['short_window']:])-close[-1])/volatility)
    for kind,line in [('EME',channel),('CEG',support)]:
        checks = {'rising_shape':line['shape_valid'], 'near_lower':bool(line.get('near')),
                  'pullback':pullback>=cfg['min_pullback_atr'],
                  'no_deep_recent_break':line.get('recent_max_breach_atr',math.inf)<=cfg['deep_break_atr'],
                  'short_decline':kind=='EME' or short_line['descending']}
        line['checks'] = checks
        line['failed_conditions'] = [k for k,v in checks.items() if not v]
        line['candidate'] = all(checks.values())
    return {'state':'available','required_bars':window,'available_bars':len(bars),'start':bars[0]['date'],
            'end':bars[-1]['date'],'channel':channel,'support':support,'short':short_line,'pullback_atr':pullback}


def analyze(bars, overrides=None, *, as_of=None):
    cfg = settings(overrides)
    bars = [b for b in bars if b.get('complete',True) and (as_of is None or b['date']<=as_of)]
    result = {'version':1,'candidate':False,'state':'insufficient_history','available_bars':len(bars),
              'as_of':bars[-1]['date'] if bars else None,'config':cfg,'windows':{},'warnings':[],
              'assumption':'linear daily-price approximation; no event exclusions; not manual drawing or profitability validation'}
    if len(bars)<15:
        return result
    tr = [max(bars[i]['high']-bars[i]['low'],abs(bars[i]['high']-bars[i-1]['close']),abs(bars[i]['low']-bars[i-1]['close'])) for i in range(len(bars)-14,len(bars))]
    volatility = sum(tr)/14
    if volatility <= 0:
        return {**result,'state':'zero_atr'}
    result['atr14'] = volatility
    for w in cfg['windows']:
        result['windows'][str(w)] = fit_window(bars,w,volatility,cfg)
    available = [w for w in cfg['windows'] if len(bars)>=w]
    if not available:
        return result
    # Fixed primary; fallback only for unavailable history, not to find a match.
    primary = cfg['primary_window'] if cfg['primary_window'] in available else min(available)
    current = result['windows'][str(primary)]
    result.update(state='available',primary_window=primary, primary=current,
                  candidate=bool(current['channel']['candidate'] or current['support']['candidate']),
                  patterns=[kind for kind,key in [('EME','channel'),('CEG','support')] if current[key]['candidate']])
    if primary!=cfg['primary_window']:
        result['warnings'].append('primary_history_fallback')
    if len(bars)<max(cfg['windows']):
        result['warnings'].append('older_trend_history_incomplete')
    votes = {str(w):bool(result['windows'][str(w)]['channel']['candidate'] or result['windows'][str(w)]['support']['candidate']) for w in available}
    result['window_candidates'] = votes
    if len(set(votes.values()))>1:
        result['warnings'].append('window_sensitive')
    # Extrapolate the trend before the recent 126 sessions; this is an old-path
    # contrast, not an alternate admission rule or fair value.
    if len(bars)>=primary+126:
        old=bars[-primary-126:-126]
        oi,os,orr=regression([b['close'] for b in old])
        value=oi+os*(primary+126-1)
        result['older_path']={'start':old[0]['date'],'fit_end':old[-1]['date'],'end':bars[-1]['date'],
            'intercept':oi,'slope_per_bar':os,'r2':orr,'value_latest':value,
            'distance_atr':(bars[-1]['close']-value)/volatility,'known_at':bars[-1]['date']}
    else:
        result['older_path']={'state':'insufficient_history','required_bars':primary+126,'available_bars':len(bars)}
    result['reason'] = 'near_rising_structure_with_pullback' if result['candidate'] else 'primary_shape_position_or_pullback_not_met'
    return result
