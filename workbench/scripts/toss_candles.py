"""Read-only paginated KR/US daily candles; authentication is caller supplied."""
import math
import re
from datetime import datetime


def _route(symbol, market, provider_symbol):
    if market == 'KR' and symbol in ('^KS11', '^KQ11'):
        code = 'KOSPI' if symbol == '^KS11' else 'KOSDAQ'
        return f'/api/v1/market-indicators/{code}/candles', code, True, None
    if market == 'US' and symbol == '^GSPC':
        return '/api/v1/candles', provider_symbol or 'SPY', False, 'SPY'
    if market == 'KR':
        if not re.fullmatch(r'[0-9A-Z]{6}\.(KS|KQ)', symbol):
            raise ValueError('Expected an explicit KR listing symbol')
        return '/api/v1/candles', provider_symbol or symbol.split('.')[0], False, None
    if market == 'US':
        code = provider_symbol or symbol
        if not isinstance(code, str) or not re.fullmatch(r'[A-Za-z0-9.\-]+', code):
            raise ValueError('Expected an explicit US provider symbol')
        return '/api/v1/candles', code, False, None
    raise ValueError('Unsupported market')


def daily_series(get, symbol, market, sessions, *, benchmark=None, provider_symbol=None,
                 adjusted=True, max_pages=10, stop_on_or_before=None, page_size=200):
    if not sessions or sessions != sorted(set(sessions)):
        raise ValueError('Expected ordered unique completed sessions')
    if not isinstance(adjusted, bool) or not isinstance(max_pages, int) or isinstance(max_pages, bool) or not 1 <= max_pages <= 40:
        raise ValueError('Invalid candle request limits')
    if (not isinstance(page_size, int) or isinstance(page_size, bool) or not 1 <= page_size <= 200
            or stop_on_or_before is not None and stop_on_or_before not in sessions):
        raise ValueError('Invalid incremental candle boundary')
    endpoint, code, index, benchmark_proxy = _route(symbol, market, provider_symbol)
    params = {'interval': '1d', 'count': page_size}
    if not index:
        params.update(symbol=code, adjusted=str(adjusted).lower())
    expected_currency = 'KRW' if market == 'KR' else 'USD'
    bars = {}
    cursors = set()
    pages = []
    finished = False
    for _ in range(max_pages):
        envelope = get(endpoint, dict(params))
        payload = envelope.get('result') if isinstance(envelope, dict) else None
        if not isinstance(payload, dict) or not isinstance(payload.get('candles'), list):
            raise ValueError('Invalid candle envelope')
        days = []
        for candle in payload['candles']:
            stamp = datetime.fromisoformat(candle['timestamp'])
            if stamp.tzinfo is None:
                raise ValueError('Candle timestamp requires a timezone')
            # Daily bars are session-labelled; do not shift midnight across zones.
            day = stamp.date().isoformat()
            days.append(day)
            if not index and candle.get('currency') != expected_currency:
                raise ValueError('Unexpected candle currency')
            if day < sessions[0] or day > sessions[-1]:
                continue
            values = {}
            for target, source in [('open', 'openPrice'), ('high', 'highPrice'), ('low', 'lowPrice'),
                                   ('close', 'closePrice'), ('volume', 'volume')]:
                raw = candle[source]
                if isinstance(raw, bool) or raw is None:
                    raise ValueError('Invalid candle value')
                value = float(raw)
                if not math.isfinite(value) or value < 0:
                    raise ValueError('Invalid candle value')
                values[target] = value
            bar = {'date': day, **values}
            if day in bars and bars[day] != bar:
                raise ValueError('Conflicting overlapping candle pages')
            bars[day] = bar
        if days != sorted(set(days), reverse=True):
            raise ValueError('Candle page must contain unique descending dates')
        pages.append({'before': params.get('before'), 'returned_count': len(days)})
        cursor = payload.get('nextBefore')
        if not cursor or days and min(days) <= (stop_on_or_before or sessions[0]):
            finished = True
            break
        if not days or not isinstance(cursor, str) or cursor in cursors:
            raise ValueError('Candle cursor does not progress')
        if params.get('before') and datetime.fromisoformat(cursor) >= datetime.fromisoformat(params['before']):
            raise ValueError('Candle cursor does not move backwards')
        cursors.add(cursor)
        params['before'] = cursor
    if not finished:
        raise ValueError('Candle page budget exhausted before history boundary')
    result = {
        'symbol': symbol,
        'provider_symbol': code,
        'market': market,
        'currency': expected_currency,
        'benchmark': benchmark,
        'bars': [bars[d] for d in sorted(bars)],
        'provider': 'Toss OpenAPI candles',
        'source_url': 'https://openapi.tossinvest.com' + endpoint,
        'instrument_type': 'INDEX' if index else 'BENCHMARK_PROXY' if benchmark_proxy else 'EQUITY',
        'price_basis': 'toss_index_points_observation_only' if index else
            'toss_adjusted_ohlc_no_order_authority' if adjusted else 'toss_raw_ohlc_no_order_authority',
        'collection': {
            'pages': pages,
            'adjusted': None if index else adjusted,
            'first_requested_session': sessions[0],
            'last_requested_session': sessions[-1],
        },
    }
    if benchmark_proxy:
        result.update(benchmark_proxy=benchmark_proxy,
                      benchmark_proxy_reason='Toss OpenAPI does not expose a US broad-market index candle')
    return result
