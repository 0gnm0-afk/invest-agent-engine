"""Display-only Taver averages; no screening or policy inputs.

C51: market sets; C55/X01: additional 15-day short line.
Weekly 10/30 are the existing technical_context settings, not new Taver rules.
"""
from datetime import date, timedelta
from .structure_math import sma
from .taver import TAVER_MA_PERIODS_KR, TAVER_MA_PERIODS_US

DISPLAY_BARS = 126
WEEKLY_PERIODS = (10, 30)

def daily_periods(market):
    return (5, 10, 15, 20) + {'KR': TAVER_MA_PERIODS_KR, 'US': TAVER_MA_PERIODS_US}[market]

def required_daily_bars(market):
    return DISPLAY_BARS + max(daily_periods(market)) - 1

def weekly_bars(bars, last_session):
    groups = {}
    for bar in bars:
        day = date.fromisoformat(bar['date'])
        monday = day - timedelta(days=day.weekday())
        groups.setdefault(monday, []).append(bar)
    output = []
    for monday, group in sorted(groups.items()):
        friday = (monday + timedelta(days=4)).isoformat()
        # Omit the initial potentially partial week, and the unfinished final week.
        if monday.isoformat() < bars[0]['date'] or friday > last_session:
            continue
        output.append({'date': group[-1]['date'], 'available_on': friday,
            'open': group[0]['open'], 'high': max(b['high'] for b in group),
            'low': min(b['low'] for b in group), 'close': group[-1]['close'],
            'volume': sum(b['volume'] for b in group)})
    return output

def calculate(bars, market):
    weeks = weekly_bars(bars, bars[-1]['date']) if bars else []
    lines = {f'D SMA {p}': sma(bars, p) for p in daily_periods(market)}
    offset = max(0, len(bars) - DISPLAY_BARS)
    start = bars[offset]['date'] if bars else ''
    week_count = sum(w['available_on'] <= start for w in weeks)
    for p in WEEKLY_PERIODS:
        values = sma(weeks, p)
        j, current, aligned = 0, None, []
        for bar in bars:
            while j < len(weeks) and weeks[j]['available_on'] <= bar['date']:
                current = values[j]
                j += 1
            aligned.append(current)
        lines[f'W SMA {p}'] = aligned
    required = required_daily_bars(market)
    return {'lines': lines, 'weekly_bars': weeks, 'history': {
        'state': 'available' if len(bars) >= required and week_count >= max(WEEKLY_PERIODS) else 'insufficient_history',
        'required_daily_bars': required, 'available_daily_bars': len(bars),
        'required_weeks_at_display_start': max(WEEKLY_PERIODS),
        'available_weeks_at_display_start': week_count,
        'display_bars': min(DISPLAY_BARS, len(bars)), 'display_start': start,
        'lines_at_display_start': {k: bool(v) and v[offset] is not None for k,v in lines.items()}}}
