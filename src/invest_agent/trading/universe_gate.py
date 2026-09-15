"""Frozen local-currency hard gate. No provider calls and no inferred metadata."""
from collections import Counter
from math import isfinite
from statistics import mean

US_EXCHANGE_FAMILIES = ('NYSE', 'NASDAQ')
US_MIN_MARKET_CAP_USD = 2_000_000_000
US_MIN_AVG_TRADED_VALUE_20D_USD = 20_000_000
KR_MARKET = 'KOSPI'
KR_MIN_AVG_TRADED_VALUE_20D_KRW = 2_000_000_000
LIQUIDITY_LOOKBACK_COMPLETED_BARS = 20
# Explicit provider metadata only: Yahoo chart / FDR listing / Toss exchange.
EXCHANGE_FAMILIES = {'NYQ': 'NYSE', 'NYSE': 'NYSE', 'NYS': 'NYSE',
    'NMS': 'NASDAQ', 'NGM': 'NASDAQ', 'NCM': 'NASDAQ', 'NAS': 'NASDAQ', 'NSQ': 'NASDAQ', 'NASDAQ': 'NASDAQ',
    'KSC': 'KOSPI', 'KOSPI': 'KOSPI', 'KSQ': 'KOSDAQ', 'KOSDAQ': 'KOSDAQ', 'KONEX': 'KONEX'}


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value) and value >= 0


def evaluate(series, bars):
    market = series.get('market')
    currency = 'USD' if market == 'US' else 'KRW' if market == 'KR' else None
    raw = series.get('raw_exchange_code') or series.get('exchange') or series.get('listing_market')
    family = EXCHANGE_FAMILIES.get(str(raw).upper())
    cap = series.get('market_cap')
    reasons = []
    if (market == 'US' and family not in US_EXCHANGE_FAMILIES or
        market == 'KR' and family != KR_MARKET or market not in ('US', 'KR')):
        reasons.append('ineligible_exchange' if raw else 'unavailable_exchange')
    if series.get('instrument_type') not in (None, 'EQUITY') or series.get('tradable') is False:
        reasons.append('ineligible_instrument_or_not_tradable')
    if series.get('currency') != currency:
        reasons.append('unavailable_liquidity_currency')
    if market == 'US':
        if not number(cap) or series.get('market_cap_currency') != 'USD' or not series.get('market_cap_as_of'):
            reasons.append('unavailable_market_cap')
        elif cap < US_MIN_MARKET_CAP_USD:
            reasons.append('market_cap_below_minimum')
    recent = bars[-LIQUIDITY_LOOKBACK_COMPLETED_BARS:]
    reliable_value = series.get('traded_value_basis') == 'provider_daily_local_currency'
    values = [b.get('traded_value') if reliable_value else
              b['close'] * b['volume'] if number(b.get('close')) and number(b.get('volume')) else None for b in recent]
    average = None
    if len(recent) < LIQUIDITY_LOOKBACK_COMPLETED_BARS:
        reasons.append('unavailable_insufficient_liquidity_bars')
    elif not all(number(v) for v in values):
        reasons.append('unavailable_liquidity_inputs')
    else:
        average = mean(values)
        minimum = US_MIN_AVG_TRADED_VALUE_20D_USD if market == 'US' else KR_MIN_AVG_TRADED_VALUE_20D_KRW
        if average < minimum:
            reasons.append('liquidity_below_minimum')
    return {'market': market, 'normalized_exchange_family': family, 'raw_exchange_code': raw,
        'market_cap': cap, 'market_cap_currency': series.get('market_cap_currency'),
        'market_cap_as_of': series.get('market_cap_as_of'), 'avg_traded_value_20d': average,
        'avg_traded_value_currency': currency, 'liquidity_completed_bars': len(recent),
        'liquidity_as_of': recent[-1]['date'] if recent else None,
        'trading_value_basis': 'provider_daily_local_currency' if reliable_value else 'close_times_volume_proxy',
        'universe_pass': not reasons, 'universe_rejection_reasons': reasons}


def audit(rows):
    result = {}
    for market in ('KR', 'US'):
        subset = [r for r in rows if r.get('market') == market]
        counts = Counter(reason for r in subset for reason in
                         r.get('universe', {}).get('universe_rejection_reasons', [r.get('reason', 'unavailable')]))
        result[market] = {'input_count': len(subset),
            'passed_count': sum(r.get('universe', {}).get('universe_pass', False) for r in subset),
            'rejection_counts': dict(sorted(counts.items()))}
    return result
