"""Daily observation-only trend screen. All thresholds are explicit inputs."""
from __future__ import annotations

import math
from statistics import mean


def validate_profile(profile: dict):
    if profile.get("status") != "review_only":
        raise ValueError("Only review_only screening profiles are supported")
    for market in ("KR", "US"):
        rule = profile[market]
        for key in ("ma_period", "slope_window", "transition_window", "rs_period", "activity_window"):
            value = rule[key]
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"Invalid {market}.{key}")
        for key in ("min_rs", "min_activity_ratio", "min_average_value"):
            value = rule[key]
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
                raise ValueError(f"Invalid {market}.{key}")
        if rule["min_activity_ratio"] < 0 or rule["min_average_value"] < 0:
            raise ValueError("Activity thresholds cannot be negative")


def check_bars(bars: list[dict], expected_sessions: list[str]):
    if not bars:
        raise ValueError("no_bars")
    dates = [b["date"] for b in bars]
    if dates != sorted(set(dates)):
        raise ValueError("duplicate_or_unordered_dates")
    if dates[-1] != expected_sessions[-1]:
        raise ValueError("stale_last_session")
    expected = [d for d in expected_sessions if d >= dates[0]]
    if dates != expected:
        raise ValueError("missing_or_non_session_bars")
    for bar in bars:
        for key in ("open", "high", "low", "close", "volume"):
            value = bar[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError("invalid_bar_value")
        if bar["low"] <= 0 or not bar["low"] <= min(bar["open"], bar["close"]) <= max(bar["open"], bar["close"]) <= bar["high"]:
            raise ValueError("invalid_ohlc")


def usable_tail(bars: list[dict], sessions: list[str]) -> list[dict]:
    """Use only a contiguous valid suffix, never fill or compress missing bars."""
    dates=[b["date"] for b in bars]
    if dates != sorted(set(dates)):
        raise ValueError("duplicate_or_unordered_dates")
    by_date={b["date"]:b for b in bars}
    tail=[]
    for day in reversed(sessions):
        bar=by_date.get(day)
        if bar is None:
            break
        try:
            check_bars([bar],[day])
        except ValueError:
            break
        tail.append(bar)
    if not tail:
        raise ValueError("stale_or_invalid_last_session")
    return list(reversed(tail))


def evaluate(series: dict, benchmark: dict, rule: dict, sessions: list[str]) -> dict:
    identity = {k: series[k] for k in ("market", "symbol", "currency", "benchmark")}
    if series.get("name"):
        identity["name"] = series["name"]
    try:
        bars = usable_tail(series["bars"],sessions)
        benchmark_bars = usable_tail(benchmark["bars"],sessions)
        check_bars(bars, sessions)
        check_bars(benchmark_bars, sessions)
        closes = [b["close"] for b in bars]
        p, w, t = rule["ma_period"], rule["slope_window"], rule["transition_window"]
        r, a = rule["rs_period"], rule["activity_window"]
        if len(bars) < max(p + w + t, r + 1, a + 1):
            raise ValueError("insufficient_history")
        ma = [None if i < p - 1 else mean(closes[i-p+1:i+1]) for i in range(len(closes))]
        slopes = [None if i < p + w - 1 else ma[i] / ma[i-w] - 1 for i in range(len(ma))]
        crossing = []
        for i in range(len(bars)-t, len(bars)):
            current_slope, prior_slope = slopes[i], slopes[i-1]
            if current_slope is not None and prior_slope is not None and current_slope > 0 and prior_slope <= 0:
                crossing.append(i)
        bench = {b["date"]: b["close"] for b in benchmark_bars}
        rs = (closes[-1] / closes[-r-1]) / (bench[bars[-1]["date"]] / bench[bars[-r-1]["date"]]) - 1
        values = [b["close"] * b["volume"] for b in bars]
        avg_value = mean(values[-a:])
        prior_avg = mean(values[-a-1:-1])
        if prior_avg <= 0:
            raise ValueError("zero_activity_baseline")
        ratio = values[-1] / prior_avg
        last_slope, last_ma = slopes[-1], ma[-1]
        if last_slope is None or last_ma is None:
            raise ValueError("insufficient_history")
        checks = {"ma_rising": last_slope > 0, "recent_turn": bool(crossing), "above_ma": closes[-1] > last_ma,
                  "relative_strength": rs >= rule["min_rs"], "liquidity": avg_value >= rule["min_average_value"],
                  "activity": ratio >= rule["min_activity_ratio"]}
        return {**identity, "state": "candidate" if all(checks.values()) else "not_selected", "as_of": bars[-1]["date"],
                "checks": checks, "failed_conditions": [k for k,v in checks.items() if not v],
                "ma": ma[-1], "ma_slope": slopes[-1], "turn_date": bars[crossing[-1]]["date"] if crossing else None,
                "relative_strength": rs, "average_value": avg_value, "activity_ratio": ratio, "close": closes[-1],
                "trading_value_basis": "close_times_volume_proxy", "price_basis": series["price_basis"], "authority": "observation_only",
                "valid_history_start":bars[0]["date"], "history_truncated":len(bars)<len(series["bars"])}
    except (ValueError, KeyError, IndexError, ZeroDivisionError) as exc:
        return {**identity, "state": "unavailable", "reason": str(exc), "authority": "observation_only"}


def screen(snapshot: dict) -> dict:
    from .universe_gate import evaluate as gate, audit
    from .sperandeo import analyze, sort_key
    from .taver import analyze as location
    rows = []
    metadata = {(r['market'], r['symbol']): r for r in snapshot.get('universe', [])}
    for original in snapshot['series']:
        item = {**metadata.get((original['market'], original['symbol']), {}), **original}
        identity = {k:item[k] for k in ('market','symbol','currency','benchmark','name','price_basis') if k in item}
        try:
            bars = usable_tail(item['bars'], snapshot['sessions'][item['market']])
        except (ValueError, KeyError, IndexError) as exc:
            rows.append({**identity, 'state':'unavailable', 'reason':str(exc), 'universe':gate(item, [])})
            continue
        universe = gate(item, bars)
        row = {**identity, 'state':'not_selected', 'as_of':bars[-1]['date'], 'close':bars[-1]['close'],
               'authority':'observation_only', 'universe':universe, 'candidate_reasons':[],
               'valid_history_start':bars[0]['date'], 'history_truncated':len(bars)<len(item['bars'])}
        if any(r.startswith('unavailable_') for r in universe['universe_rejection_reasons']):
            row['state'] = 'unavailable'
        if universe['universe_pass']:
            structure = analyze(bars)
            row['sperandeo'] = structure
            if structure['candidate']:
                row.update(state='candidate', candidate_origin='sperandeo',
                           candidate_reasons=structure['candidate_reasons'], taver=location(bars,item['market'],structure))
        if row['state'] == 'candidate' or item['symbol'] in snapshot.get('held_symbols', []):
            from .technical_context import build
            context = build(item, snapshot, structure=row.get('sperandeo'))
            row['technical_context'] = context
            if context['state'] == 'available':
                row['sperandeo'], row['taver'] = context['sperandeo'], context['taver']
        rows.append(row)
    rows.sort(key=sort_key)
    for error in snapshot["errors"]:
        rows.append({**error, "state": "unavailable"})
    previous = {(r["market"], r["symbol"]): r for r in snapshot.get("previous_screen", []) if r.get("candidate_origin") == "sperandeo" or "sperandeo" in r}
    observed={(r['market'],r['symbol']) for r in rows}
    for key,before in previous.items():
        if key not in observed and (before['state']=='candidate' or before['state']=='unavailable' and before.get('previous_candidate')):
            rows.append({'market':key[0],'symbol':key[1],'state':'unavailable','reason':'previous_candidate_not_observed','candidate_origin':'sperandeo'})
    for row in rows:
        before = previous.get((row["market"], row["symbol"]))
        row['previous_candidate']=before is not None and (before['state']=='candidate' or before['state']=='unavailable' and bool(before.get('previous_candidate')))
        if before and row['state']=='unavailable' and row['previous_candidate']:
            row['candidate_origin']='sperandeo'
        row["change"] = "unknown" if before is None or row["state"] == "unavailable" or before["state"] == "unavailable" else (
            "new" if row["state"] == "candidate" and before["state"] != "candidate" else
            "released" if row["state"] != "candidate" and before["state"] == "candidate" else "unchanged")
        if before and row['change']!='unknown':
            if before.get('benchmark')!=row.get('benchmark'):
                row['change']='unknown'
            previous_date=before.get('as_of') or snapshot.get('previous_session_dates',{}).get(row['market'])
            current_date=row.get('as_of')
            if previous_date and current_date:
                if current_date<previous_date:
                    row['change']='unknown'
                elif current_date==previous_date and row['change'] in ('new','released'):
                    row['change']='revised'
    return {"schema_version": 1, "source": snapshot["source"], "profile_status": "review_only", "rows": rows,
            "candidate_path": "universe_sperandeo_taver_v1", "universe_audit": audit(rows),
            "candidate_count": sum(r["state"] == "candidate" for r in rows),
            "unavailable_count": sum(r["state"] == "unavailable" for r in rows),
            "unavailable_scope_count":len(snapshot["coverage"].get("scope_errors",[])), "coverage": snapshot["coverage"],
            "fetched_at": snapshot["fetched_at"], "session_dates": {k:v[-1] for k,v in snapshot["sessions"].items()}}
