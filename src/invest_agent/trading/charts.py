"""Local chart images drawn from the same frozen snapshot used by the screen."""
from __future__ import annotations

import hashlib
from datetime import date, timedelta
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
from matplotlib import pyplot as plt
from matplotlib.patches import Rectangle
from matplotlib import font_manager

_TITLE_FONT = next((name for name in ("Malgun Gothic", "AppleGothic", "NanumGothic", "DejaVu Sans")
                    if any(f.name == name for f in font_manager.fontManager.ttflist)), "DejaVu Sans")

from .market import check_bars, usable_tail


def completed_week_bars(bars: list[dict], last_session: str) -> list[dict]:
    groups: dict[date, list[dict]] = {}
    for bar in bars:
        day = date.fromisoformat(bar["date"])
        monday = day - timedelta(days=day.weekday())
        groups.setdefault(monday, []).append(bar)
    output = []
    for monday, group in sorted(groups.items()):
        # Conservative: exclude a week until its calendar Friday has passed.
        # A Thursday before a Friday holiday will remain explicitly omitted.
        if monday + timedelta(days=4) > date.fromisoformat(last_session):
            continue
        output.append({"date": group[-1]["date"], "open":group[0]["open"], "high":max(b["high"] for b in group),
                       "low":min(b["low"] for b in group), "close":group[-1]["close"], "volume":sum(b["volume"] for b in group)})
    return output


def draw(bars: list[dict], path: Path, title: str, ma_period: int, structure: dict | None = None, *, chart_data=None, weekly_data=None):
    from .structure_math import sma
    from .chart_history import DISPLAY_BARS, WEEKLY_PERIODS
    lines = ({k:v for k,v in chart_data['lines'].items() if k.startswith('D ')} if chart_data
             else weekly_data['lines'] if weekly_data else {f'W SMA {p}': sma(bars, p) for p in WEEKLY_PERIODS})
    offset = max(0, len(bars)-(DISPLAY_BARS if chart_data else 140))
    shown = bars[offset:]
    fig, (price, volume) = plt.subplots(2, 1, figsize=(13, 7), sharex=True, gridspec_kw={"height_ratios":[4,1]})
    try:
        for i, bar in enumerate(shown):
            color = "#008575" if bar["close"] >= bar["open"] else "#d44b4b"
            price.vlines(i,bar["low"],bar["high"],color=color,linewidth=1)
            height = max(abs(bar["close"]-bar["open"]), bar["close"]*0.00015)
            price.add_patch(Rectangle((i-.3,min(bar["open"],bar["close"])),.6,height,color=color))
            volume.bar(i,bar["volume"],color=color,width=.7)
        for label, values in lines.items():
            price.plot(range(len(shown)), values[offset:], label=label,
                       linestyle='--' if label.startswith('W ') else '-', linewidth=1.2)
        if chart_data and chart_data['history']['state'] != 'available':
            h = chart_data['history']
            price.text(.01,.98,f"History: {h['available_daily_bars']}/{h['required_daily_bars']} daily; "
                       f"{h['available_weeks_at_display_start']}/{h['required_weeks_at_display_start']} weeks at left",
                       transform=price.transAxes, ha='left',va='top',fontsize=8,color='#657980')
        if weekly_data and weekly_data['history']['state'] != 'available':
            h = weekly_data['history']
            price.text(.01,.98,f"Weekly history: {h['available_weeks']}/{h['required_weeks']} required",
                       transform=price.transAxes,ha='left',va='top',fontsize=8,color='#657980')
        if chart_data and 'trendlines' in chart_data:
            trend_overlay(price, bars, offset, chart_data['trendlines'])
        elif structure:
            overlay(price, bars, offset, structure, ma_period)
        price.set_title(title, fontfamily=_TITLE_FONT)
        price.legend(loc="upper left", prop={"family":_TITLE_FONT})
        price.grid(alpha=.15)
        volume.set_ylabel("Volume")
        ticks = list(range(0,len(shown),max(1,len(shown)//8)))
        volume.set_xticks(ticks, [shown[i]["date"] for i in ticks],rotation=25,ha="right")
        fig.tight_layout()
        path.parent.mkdir(parents=True,exist_ok=True)
        temporary = path.with_suffix(".tmp")
        fig.savefig(temporary,format="png",dpi=125)
        temporary.replace(path)
    finally:
        plt.close(fig)


def generate(snapshot: dict, root: Path, folder: Path, limit: int = 20) -> list[dict]:
    from .market import screen
    result = screen(snapshot)
    from .report_names import company_names, company_label
    names = company_names([*snapshot["series"], *result["rows"]])
    by_symbol = {r["symbol"]:r for r in result["rows"]}
    wanted = {r["symbol"] for r in result["rows"] if r["state"] == "candidate"}
    held=set(snapshot.get("held_symbols",[]))
    wanted.update(held)
    if snapshot["coverage"]["scope"] == "connection_sample_not_marketwide":
        wanted.update(r["symbol"] for r in result["rows"] if r["state"] != "unavailable")
    references = []
    # Held stocks have priority and never consume the new-candidate chart cap.
    selected=[s for s in snapshot["series"] if s["symbol"] in held]
    ranked = [r['symbol'] for r in result['rows'] if r['state']=='candidate']
    ranked += sorted(wanted-held-set(ranked))
    by_series = {s['symbol']:s for s in snapshot['series']}
    selected += [by_series[s] for s in ranked if s in by_series and s not in held][:limit]
    from .chart_history import calculate
    from . import weekly_chart, trendlines
    seen = set()
    for item in selected:
        key = (item["market"], item["symbol"])
        if key in seen:
            continue
        seen.add(key)
        bars=usable_tail(item["bars"],snapshot["sessions"][item["market"]])
        check_bars(bars,snapshot["sessions"][item["market"]])
        chart_data = calculate(bars, item["market"])
        chart_data["trendlines"] = trendlines.analyze(bars)
        weeks, weekly_source = weekly_chart.load(item, bars[-1]['date'], root, chart_data['weekly_bars'],
                                                 live=snapshot.get('source') == 'live_public')
        weekly_data = weekly_chart.calculate(weeks, item['market'])
        name = hashlib.sha256((item["market"]+item["symbol"]).encode()).hexdigest()[:16]
        row = by_symbol.get(item['symbol'], {})
        structure = row if row.get('sperandeo') and row.get('taver', {}).get('primary_ma_period') else None
        daily_ma = row['taver']['primary_ma_period'] if structure else snapshot['profile'][item['market']]['ma_period']
        import json
        metadata = root / folder / f"{name}_history.json"
        metadata.parent.mkdir(parents=True, exist_ok=True)
        metadata.write_text(json.dumps({**chart_data['history'], 'weekly':weekly_data['history'], 'weekly_source':weekly_source}, indent=2), encoding='utf-8')
        (root / folder / f"{name}_trendlines.json").write_text(
            json.dumps(chart_data['trendlines'], ensure_ascii=False, indent=2), encoding='utf-8')
        for period, period_bars, ma in (("daily",bars,daily_ma),
                                 ("weekly",weeks,10)):
            if not period_bars:
                continue
            relative = folder / f"{name}_{period}.png"
            try:
                draw(period_bars, root / relative, f"{company_label(item, names)} | {period} | {period_bars[-1]['date']} | {item['currency']} | observation only",ma, structure if period == 'daily' else None, chart_data=chart_data if period == 'daily' else None, weekly_data=weekly_data if period == 'weekly' else None)
            except (ValueError, OSError, RuntimeError):
                continue  # Optional rendering must not erase calculated candidate rows.
            references.append({"path":str(relative),"symbol":item["symbol"],"timeframe":period,
                               "as_of":period_bars[-1]["date"],"sha256":hashlib.sha256((root/relative).read_bytes()).hexdigest()})
    return references


def overlay(price, bars, offset, row, primary_ma):
    from .structure_math import sma
    dates = {b['date']:i for i,b in enumerate(bars)}
    windows = row['sperandeo']['windows']
    selected = ['126'] if windows['126']['data_state']=='available' else []
    selected += [p for p,w in windows.items() if p!='126' and w.get('stage') in ('S1','S2','S3','S4')]
    colors = {'126':'#b66a00','63':'#8a5bc7','252':'#657980'}
    groups = {}
    for p in selected:
        w = windows[p]
        key = tuple(w.get(k) for k in ('anchor_high_a_date','anchor_high_a_price','anchor_high_b_date','anchor_high_b_price',
                                      'reference_low_date','breakout_date','retest_pivot_date','completion_level'))
        groups.setdefault(key, []).append(p)
    for group in groups.values():
        p = group[0]
        prefix = '/'.join(group)
        w = windows[p]
        a = dates[w['anchor_high_a_date']]
        start = max(a,offset)
        xs = list(range(start,len(bars)))
        price.plot([i-offset for i in xs], [w['anchor_high_a_price']+w['slope_per_bar']*(i-a) for i in xs],
                   color=colors[p], linestyle='--', linewidth=1.2, label=" / ".join(f"{period}D {windows[period]['stage']}" for period in group))
        for label, dk, vk in [('A','anchor_high_a_date','anchor_high_a_price'),('B','anchor_high_b_date','anchor_high_b_price'),
                ('L','reference_low_date','reference_low_price'),('break','breakout_date','breakout_close'),
                ('R','retest_pivot_date','retest_low')]:
            if w.get(dk) in dates and dates[w[dk]]>=offset:
                x,y=dates[w[dk]]-offset,w[vk]
                price.scatter([x],[y],s=18,color=colors[p])
                price.annotate(prefix+label,(x,y),xytext=(3,8 if label!='L' else -14),textcoords='offset points',fontsize=7,color=colors[p])
        if w.get('completion_level') is not None:
            x=max(offset,dates[w['completion_source_date']])-offset
            price.hlines(w['completion_level'],x,len(bars)-offset-1,color=colors[p],linestyle=':',label=p+'D C')


def trend_overlay(price, bars, offset, result):
    dates={b['date']:i for i,b in enumerate(bars)}
    window=result['windows']['126']
    for key,label,color in [('upper_trendline','하락 추세 저항선','#b66a00'),
                            ('lower_trendline','상승 추세 지지선','#008575')]:
        line=window[key]
        if line['state']!='available':
            continue
        a=dates[line['anchor_a_date']]
        # Stop at the first post-confirmation price breach: do not draw through later candles.
        end=dates[line['first_price_breach_date']] if line['first_price_breach_date'] else len(bars)-1
        xs=list(range(max(offset,a),end+1))
        price.plot([i-offset for i in xs],
                   [line['anchor_a_price']+line['slope_per_bar']*(i-a) for i in xs],
                   color=color,linestyle='--',linewidth=1.2,label=label)
        for anchor in ('a','b'):
            i=dates[line[f'anchor_{anchor}_date']]
            if i>=offset:
                price.scatter([i-offset],[line[f'anchor_{anchor}_price']],s=18,color=color)
