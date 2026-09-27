"""Deterministic invented OHLCV, metadata and groups. No external input."""
from datetime import date, timedelta


def fixture():
    days = []
    day = date(2024, 1, 1)
    while len(days) < 380:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day += timedelta(days=1)
    series, groups, benchmarks = [], [], {}
    for market, scale, currency, exchange in (('KR', 100, 'KRW', 'KOSPI'), ('US', 1, 'USD', 'NMS')):
        benchmark = 'SYNTH_INDEX_' + market
        benchmarks[benchmark] = {'price_basis': 'synthetic', 'bars': [
            dict(date=d, open=100+i/100, high=102+i/100, low=99+i/100,
                 close=101+i/100, volume=1_000_000) for i, d in enumerate(days)]}
        for n in range(5):
            symbol = f'SYNTH_{market}_{n+1:02}'
            # Invented declining line followed by a confirmed breakout and retest.
            bars = []
            for i, d in enumerate(days):
                high, low, close = 170-i/40, 166-i/40, 168-i/40
                if 314 <= i < 317:
                    high, low, close = 145, 141, 143
                if i >= 317:
                    j = i-317
                    high = 150-j-(0 if j in (0, 20) else 4)
                    low, close = high-2, high-1
                    if j == 50:
                        low = 85
                    if j >= 51:
                        high, low, close = 89, 87, 88
                    if j >= 55:
                        high, low, close = 98, 94, 96
                    if j == 55:
                        close = 97
                    if n % 2 and j in (57, 58, 59, 60, 61):
                        low, high, close = {57:(92,97,94),58:(90,95,93),59:(92,96,94),
                                            60:(93,97,95),61:(97,101,100)}[j]
                multiplier = scale * (1 + n/10)
                bars.append(dict(date=d, open=close*multiplier, high=high*multiplier,
                                 low=low*multiplier, close=close*multiplier,
                                 volume=1_000_000 if i < 379 else 1_200_000+n*100_000))
            # An explicit invalid latest candle exercises quarantine (not interpolation).
            if n == 4:
                bars[-1]['close'] = None
            series.append(dict(market=market, symbol=symbol, name=f'Invented Company {market} {n+1}',
                               currency=currency, benchmark=benchmark, bars=bars,
                               price_basis='synthetic', provider='synthetic', raw_exchange_code=exchange,
                               market_cap=3_000_000_000, market_cap_currency=currency,
                               market_cap_as_of=days[-1]))
            # One valid candidate deliberately has no classification.
            if n != 3:
                groups.append(dict(market=market, symbol=symbol, group_code='SYNTH_GROUP_'+str(n % 2),
                                   group_name='Invented Sector '+str(n % 2), group_state='matched',
                                   group_taxonomy='SYNTHETIC', reason=None, evidence=[]))
    return dict(schema_version=1, source='synthetic', fetched_at=days[-1]+'T23:00:00+00:00',
                sessions={'KR':days, 'US':days}, series=series, benchmarks=benchmarks,
                profile={'KR':{'rs_period':63}, 'US':{'rs_period':63}}, errors=[],
                coverage={'scope':'synthetic_demo', 'requested':len(series), 'received':len(series),
                          'market_wide':False, 'scope_errors':[]}), groups
