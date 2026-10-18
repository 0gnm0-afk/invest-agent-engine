"""Numeric equivalent of DrawingTools timeline/index on the current linear chart."""
from bisect import bisect_right
from datetime import date, timedelta
import math

VERSION = 'drawing-tools-linear-v1'


def advance(d, frame):
    if frame == '1mo':
        return date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return d + timedelta(days=7 if frame == '1w' else 1)


def timeline(bars, frame):
    if not bars:
        return []
    result = [dict(b) for b in bars]
    last = date.fromisoformat(bars[-1].get('last_session', bars[-1]['date']))
    # JS setUTCFullYear maps leap day to March 1 in non-leap target years.
    try: end = last.replace(year=last.year + 5)
    except ValueError: end = date(last.year + 5, 3, 1)
    cursor = advance(date.fromisoformat(bars[-1]['date']), frame)
    while cursor <= end:
        if frame != '1d' or cursor.weekday() < 5:
            result.append({'date': cursor.isoformat(), 'projected': True})
        cursor = advance(cursor, frame)
    if frame != '1d':
        for i in range(len(bars)-1, len(result)):
            result[i]['last_session'] = (boundary(result, i, frame)-timedelta(days=1)).isoformat()
    return result


def boundary(bars, i, frame):
    return date.fromisoformat(bars[i+1]['date']) if i+1 < len(bars) else advance(date.fromisoformat(bars[i]['date']), frame)


def logical(day, bars, frame):
    if not bars or day < bars[0]['date'] or day > bars[-1].get('last_session', bars[-1]['date']):
        return None
    i = bisect_right([b['date'] for b in bars], day)-1
    start = date.fromisoformat(bars[i]['date'])
    fraction = (date.fromisoformat(day)-start).days/(boundary(bars, i, frame)-start).days
    return i+fraction if frame == '1d' else i-.4+.8*fraction


def geometry(workspace, chart, context):
    frame = chart['timeframe']; bars = chart['bars']; slots = timeline(bars, frame)
    last = bars[-1].get('last_session', bars[-1]['date']) if bars else None
    result = []
    for d in workspace['state']['drawings']:
        points = [{**p, 'logical': logical(p['time'], slots, frame), 'future': last is not None and p['time'] > last} for p in d['points']]
        row = {**d, 'points': points, 'meaning': 'geometry_only_no_inferred_trade_intent', 'calculations': {}, 'limitations': []}
        c = row['calculations']; kind = d['kind']; p = [v['price'] for v in points]; x = [v['logical'] for v in points]
        if any(v['future'] for v in points): row['limitations'].append('future_calendar_slots_not_actual_sessions_holidays_not_applied')
        if context.get('price_scale', 'linear') != 'linear':
            row['limitations'].append('unsupported_price_transform'); result.append(row); continue
        if any(v is None for v in x): row['limitations'].append('anchor_outside_supported_timeline')
        if kind == 'horizontal': c['level'] = p[0]
        elif kind == 'riskreward':
            risk = abs(p[0]-p[1]); reward = abs(p[2]-p[0])
            c.update(entry=p[0], stop=p[1], target=p[2], risk_per_share=risk, reward_per_share=reward, ratio=reward/risk, direction='long' if p[1]<p[0] else 'short', formula='abs(target-entry)/abs(entry-stop)')
            row['meaning'] = 'explicit_riskreward_scenario_not_order'
            row['limitations'].append('price_distance_only_costs_slippage_gap_excluded')
        elif kind in ('box', 'measure'):
            c.update(low=min(p), high=max(p), delta=p[1]-p[0], change_pct=(p[1]-p[0])/p[0]*100,
                     calendar_days=abs((date.fromisoformat(points[1]['time'])-date.fromisoformat(points[0]['time'])).days),
                     bar_interval=abs(math.floor(x[1]+.5)-math.floor(x[0]+.5)) if None not in x else None,
                     formula='price_delta=b-a; bar_interval=abs(JS_round(logical_b)-JS_round(logical_a))')
        elif None not in x and x[0] != x[1]:
            slope=(p[1]-p[0])/(x[1]-x[0]); intercept=p[0]-slope*x[0]
            c.update(price_per_logical_bar=slope, intercept=intercept, formula='price(x)=p0+(p1-p0)*(x-x0)/(x1-x0)')
            at = context.get('evaluate_at') or last
            at_x = logical(at, slots, frame) if at else None
            c.update(evaluate_at=at, evaluated_logical=at_x, line_price=None if at_x is None else intercept+slope*at_x)
            if kind == 'channel':
                offset=p[2]-(intercept+slope*x[2]); c.update(channel_offset=offset, channel_width=abs(offset), other_line_price=None if at_x is None else c['line_price']+offset)
        else: row['limitations'].append('degenerate_projection')
        result.append(row)
    return {'version': VERSION, 'timeframe': frame, 'price_scale': context.get('price_scale','linear'),
            'coordinate_basis': 'actual_display_bars; intra_week_month=i-0.4+0.8*fraction; daily=i+fraction',
            'price_basis': chart.get('price_basis'), 'last_actual_session': last,
            'limitations': ['existing_anchors_are_not_automatically_rescaled_after_corporate_actions', 'future_calendar_not_exchange_calendar'], 'drawings': result}
