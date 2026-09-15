"""Chart-only weekly history. Never passed to screening or LLM fact packets."""
import hashlib
import json
from datetime import date, timedelta
from .chart_history import weekly_bars
from .structure_math import sma
from .taver import TAVER_MA_PERIODS_KR, TAVER_MA_PERIODS_US

DISPLAY_WEEKS = 140

def periods(market):
    return (5, 10, 20) + {'KR': TAVER_MA_PERIODS_KR, 'US': TAVER_MA_PERIODS_US}[market]

def required_weeks(market):
    return DISPLAY_WEEKS + max(periods(market)) - 1

def calculate(weeks, market):
    lines = {f'W SMA {p}': sma(weeks, p) for p in periods(market)}
    offset = max(0, len(weeks)-DISPLAY_WEEKS)
    return {'lines': lines, 'history': {
        'state': 'available' if len(weeks) >= required_weeks(market) else 'insufficient_history',
        'required_weeks': required_weeks(market), 'available_weeks': len(weeks),
        'display_weeks': min(len(weeks), DISPLAY_WEEKS),
        'lines_at_display_start': {k: bool(v) and v[offset] is not None for k,v in lines.items()}}}

def load(item, last_session, root, fallback, *, live=False):
    """One request per unique selected company; as-of cache is separate from snapshots.

    Yahoo daily quotes are aggregated locally to avoid using an unfinished weekly
    candle. Split-adjusted historical quotes are display-only, never risk inputs.
    """
    needed = required_weeks(item['market'])
    if len(fallback) >= needed or not live:
        return fallback, {'source': 'snapshot', 'state': 'available'}
    first = (date.fromisoformat(last_session)-timedelta(weeks=needed+2)).isoformat()
    identity = {'market':item['market'], 'symbol':item['symbol'], 'first':first, 'last':last_session, 'version':2, 'provider':item.get('provider', 'Yahoo chart')}
    key = hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
    path = root/'chart-weekly-cache'/f'{key}.json'
    try:
        if path.exists():
            record=json.loads(path.read_text('utf-8'))
            if record.get('identity') == identity:
                return record['weeks'], {'source':'chart_weekly_cache', 'state':'available', **identity}
        from .market_provider import fetch_yahoo
        from .market import check_bars
        # Boundary-only sessions are used by the provider to construct its date range.
        if item['market'] == 'KR' and item.get('provider', '').startswith('Toss'):
            # The public package never auto-imports a user's authenticated helper.
            # Preserve the supplied bars rather than changing their provider/basis.
            raise ValueError('private_provider_not_in_public_package')
        else:
            raw=fetch_yahoo(item['symbol'],item['market'],[first,last_session], chart_history=True)
        bars=[b for b in raw['bars'] if first <= b['date'] <= last_session and b.get('complete') is not False]
        # Validate quote values and ordering without fabricating missing observations.
        check_bars(bars,[b['date'] for b in bars])
        if bars[-1]['date'] != last_session:
            raise ValueError('stale_chart_history')
        weeks=weekly_bars(bars,last_session)
        record={'identity':identity, 'weeks':weeks}
        path.parent.mkdir(parents=True,exist_ok=True)
        temp=path.with_suffix('.tmp')
        temp.write_text(json.dumps(record),encoding='utf-8'); temp.replace(path)
        return weeks, {'source':raw.get('provider', 'Yahoo chart') + ' daily quotes aggregated to completed weeks', 'state':'available',
                       'price_basis':raw['price_basis'], **identity}
    except (ImportError, OSError, ValueError, KeyError, IndexError, TypeError) as exc:
        return fallback, {'source':'snapshot_fallback', 'state':'unavailable_long_history', 'error_type':type(exc).__name__, **identity}
