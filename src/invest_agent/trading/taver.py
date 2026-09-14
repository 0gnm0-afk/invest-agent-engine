"""Long SMA location attached after Sperandeo selection; never a filter."""
from .structure_math import atr, sma, ratio

TAVER_MA_PERIODS_US = (50, 100, 200)
TAVER_PRIMARY_MA_US = 200
TAVER_MA_PERIODS_KR = (60, 120, 240)
TAVER_PRIMARY_MA_KR = 240
TAVER_ATR_PERIOD = 14
TAVER_APPROACH_ATR_MULTIPLE = 1.0
TAVER_TEST_ATR_MULTIPLE = 0.5
TAVER_RECENT_EVENT_LOOKBACK = 5
RELATIONS = {'S0':'downtrend_intact', 'S1':'downtrend_intact', 'S2':'reversal_in_progress',
             'S3':'reversal_in_progress', 'S4':'reversal_confirmed'}


def location(close, low, high, average, atr_value):
    distance = ratio(close-average, atr_value)
    if low <= average <= high or distance is not None and abs(distance) <= TAVER_TEST_ATR_MULTIPLE:
        return 'at_ma_zone'
    if distance is None:
        return 'unavailable_atr_distance'
    if close < average:
        return 'below_ma'
    return 'approaching_from_above' if distance <= TAVER_APPROACH_ATR_MULTIPLE else 'above_far'


def line(bars, period, atrs):
    output = {'period': period, 'ma_type':'SMA', 'analysis_as_of': bars[-1]['date'] if bars else None,
              'data_state':'unavailable_insufficient_bars', 'location_state':'unavailable_insufficient_bars'}
    if len(bars) < period:
        return output
    averages = sma(bars, period)
    n, latest, value = len(bars)-1, bars[-1], averages[-1]
    output.update(data_state='available', ma_value_latest=value, latest_close=latest['close'],
        latest_high=latest['high'], latest_low=latest['low'],
        distance_to_ma_pct=ratio(latest['close']-value,value), distance_to_ma_atr=ratio(latest['close']-value,atrs[-1]),
        latest_bar_intersects_ma=latest['low'] <= value <= latest['high'], close_above_ma=latest['close'] > value,
        location_state=location(latest['close'],latest['low'],latest['high'],value,atrs[-1]))
    events = {'touch':None,'reclaim':None,'loss':None}
    for i in range(max(period-1,len(bars)-TAVER_RECENT_EVENT_LOOKBACK),len(bars)):
        b, ma = bars[i], averages[i]
        if b['low'] <= ma <= b['high']:
            events['touch'] = i
        if i and averages[i-1] is not None:
            if bars[i-1]['close'] <= averages[i-1] and b['close'] > ma:
                events['reclaim'] = i
            if bars[i-1]['close'] >= averages[i-1] and b['close'] < ma:
                events['loss'] = i
    for event, i in events.items():
        output[f'last_{event}_date'] = bars[i]['date'] if i is not None else None
        output[f'bars_since_{event}'] = n-i if i is not None else None
    output.update(reclaimed_recently=events['reclaim'] is not None, lost_recently=events['loss'] is not None)
    return output


def analyze(bars, market, sperandeo):
    if market not in ('US','KR'):
        return {'version':'v1','data_state':'unavailable_market_profile','market_profile':None,'lines':{}}
    periods, primary = (TAVER_MA_PERIODS_US,TAVER_PRIMARY_MA_US) if market == 'US' else (TAVER_MA_PERIODS_KR,TAVER_PRIMARY_MA_KR)
    atrs = atr(bars,TAVER_ATR_PERIOD)
    lines = {str(p):line(bars,p,atrs) for p in periods}
    available = [v for v in lines.values() if v.get('distance_to_ma_atr') is not None]
    nearest = min(available,key=lambda v:(abs(v['distance_to_ma_atr']),v['period'])) if available else None
    near = any(v['location_state'] in ('approaching_from_above','at_ma_zone') or
               v.get('reclaimed_recently') and v.get('distance_to_ma_atr') is not None and
               abs(v['distance_to_ma_atr']) <= TAVER_APPROACH_ATR_MULTIPLE for v in lines.values())
    relations = {p:RELATIONS.get(v.get('stage'),'unknown') for p,v in sperandeo['windows'].items()}
    relation = relations.get(str(sperandeo['primary_window']),'unknown')
    confluence = {'downtrend_intact':'location_near_but_downtrend_intact',
        'reversal_in_progress':'location_near_and_reversal_in_progress',
        'reversal_confirmed':'location_near_and_reversal_confirmed','unknown':'location_near_trend_unknown'}
    return {'version':'v1','data_state':'available','market_profile':market,'ma_periods':list(periods),
        'primary_ma_period':primary,'lines':lines,'primary_ma_state':lines[str(primary)]['location_state'],
        'nearest_ma_period':nearest['period'] if nearest else None,
        'nearest_distance_atr':nearest['distance_to_ma_atr'] if nearest else None,'near_target_ma':bool(near),
        'recent_event_reasons':[f'taver_{p}_{event}_recently' for p,v in lines.items()
                                for event in ('reclaimed','lost') if v.get(f'{event}_recently')],
        'sperandeo_relation':relations,'primary_sperandeo_relation':relation,
        'confluence_state':confluence[relation] if near else 'no_current_long_ma_location'}
