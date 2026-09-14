"""Read-only public OHLCV collection with explicit calendar and coverage."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pandas as pd
import requests

from .market import validate_profile

# Explicit 2026 exchange closures missing from exchange_calendars 4.13.2.
# June 3: https://www.samsungpop.com/ux/kor/customer/notice/notice/noticeViewContent.do?MenuSeqNo=23996
# July 17: https://securities.miraeasset.com/bbs/board/message/view.do?categoryId=1539&messageId=2340801
KR_CLOSURES = {"2026-06-03", "2026-07-17"}


def completed_sessions(market: str, as_of: datetime) -> list[str]:
    if as_of.tzinfo is None:
        raise ValueError("as_of must have timezone")
    cal = xcals.get_calendar("XKRX" if market == "KR" else "XNYS")
    from .chart_history import required_daily_bars
    start = (as_of - timedelta(days=550)).date().isoformat()
    end = (as_of + timedelta(days=1)).date().isoformat()
    schedule = cal.schedule.loc[start:end]
    completed = schedule[schedule["close"] + pd.Timedelta(minutes=30) <= pd.Timestamp(as_of)]
    if completed.empty:
        raise ValueError("No completed sessions")
    sessions = [s.date().isoformat() for s in completed.index if market != "KR" or s.date().isoformat() not in KR_CLOSURES]
    if len(sessions) < required_daily_bars(market):
        # Extend the calendar boundary, not the observed prices; preserve the legacy window when sufficient.
        earlier = cal.schedule.loc[:end]
        earlier = earlier[earlier['close'] + pd.Timedelta(minutes=30) <= pd.Timestamp(as_of)]
        sessions = [s.date().isoformat() for s in earlier.index if market != 'KR' or s.date().isoformat() not in KR_CLOSURES][-required_daily_bars(market):]
    if len(sessions) < required_daily_bars(market):
        raise ValueError('Insufficient calendar history')
    return sessions


def fetch_yahoo(symbol: str, market: str, sessions: list[str], *, chart_history: bool = False) -> dict:
    zone = ZoneInfo("Asia/Seoul" if market == "KR" else "America/New_York")
    start = datetime.fromisoformat(sessions[0]).replace(tzinfo=zone)
    end = datetime.fromisoformat(sessions[-1]).replace(tzinfo=zone) + timedelta(days=1)
    response = requests.get("https://query1.finance.yahoo.com/v8/finance/chart/" + requests.utils.quote(symbol, safe=""),
        params={"period1": str(int(start.timestamp())), "period2": str(int(end.timestamp())), "interval": "1d", "events": "splits", "includePrePost": "false"},
        headers={"User-Agent": "Mozilla/5.0"}, timeout=(5, 15))
    response.raise_for_status()
    raw = response.json()["chart"]["result"][0]
    if raw.get("events", {}).get("splits") and not chart_history:
        raise ValueError("split_in_window_requires_price_basis_review")
    quote = raw["indicators"]["quote"][0]
    rows = []
    for i, stamp in enumerate(raw.get("timestamp", [])):
        day = datetime.fromtimestamp(stamp, zone).date().isoformat()
        if day > sessions[-1]:
            continue
        row = {"date": day, **{k: quote[k][i] for k in ("open", "high", "low", "close", "volume")}}
        rows.append(row)
    return {"symbol": symbol, "market": market, "currency": raw["meta"]["currency"], "bars": rows,
            "price_basis": "yahoo_historical_quote_chart_only" if chart_history else "yahoo_quote_ohlc_no_order_authority", "provider": "Yahoo chart", "source_url": response.url,
            "instrument_type": raw["meta"].get("instrumentType"), "exchange": raw["meta"].get("exchangeName")}


def collect(config: dict, as_of: datetime | None = None) -> dict:
    as_of = as_of or datetime.now(timezone.utc)
    validate_profile(config["profile"])
    listings = None
    if config.get("universe_mode") == "exchange_listings":
        from .universe import discover
        universe, listings = discover(config["markets"], config.get("limit_per_exchange"))
    else:
        universe = config["universe"]
    if len({(r["market"],r["symbol"]) for r in universe}) != len(universe):
        raise ValueError("Configured universe contains duplicate symbols")
    merged={(r['market'],r['symbol']):dict(r) for r in universe}
    for r in config.get('additional_universe',[]):
        key=(r['market'],r['symbol'])
        merged[key]={**r,**merged.get(key,{})}
    universe=list(merged.values())
    if (not universe and not listings) or len({(r["market"],r["symbol"]) for r in universe}) != len(universe):
        raise ValueError("Universe must be nonempty and unique")
    if any(r["market"] not in ("KR","US") for r in universe):
        raise ValueError("Unsupported market")
    markets = sorted({r["market"] for r in universe})
    scope_errors=[{"scope":exchange,"stage":"listing","reason":row["reason"]}
                  for exchange,row in (listings or {}).items() if row.get("state")=="unavailable"]
    sessions={}
    for market in markets:
        try:
            sessions[market]=completed_sessions(market,as_of)
        except (ValueError,KeyError,IndexError) as exc:
            scope_errors.append({"scope":market,"stage":"calendar","reason":"completed_sessions_unavailable","error_type":type(exc).__name__})
    benchmark_markets: dict[str, str] = {}
    for item in universe:
        if item["benchmark"] in benchmark_markets and benchmark_markets[item["benchmark"]] != item["market"]:
            raise ValueError("Benchmark cannot cross markets")
        benchmark_markets[item["benchmark"]] = item["market"]
    benchmarks, series, errors = {}, [], []
    for symbol, market in benchmark_markets.items():
        if market not in sessions:
            continue
        try:
            benchmarks[symbol] = fetch_yahoo(symbol, market, sessions[market])
        except (requests.RequestException, ValueError, KeyError, IndexError, TypeError):
            pass  # Each dependent security gets benchmark_unavailable, never a fabricated RS.
    with ThreadPoolExecutor(max_workers=4) as pool:
        for item in universe:
            if item["market"] not in sessions:
                errors.append({"market":item["market"],"symbol":item["symbol"],"reason":"completed_sessions_unavailable"})
        pending = {pool.submit(fetch_yahoo, item["symbol"], item["market"], sessions[item["market"]]): item for item in universe if item["market"] in sessions}
        for future in as_completed(pending):
            item = pending[future]
            try:
                value = future.result()
                if value["instrument_type"] != "EQUITY":
                    raise ValueError("not_equity")
                series.append({**item, **value, "benchmark": item["benchmark"]})
            except (requests.RequestException, ValueError, KeyError, IndexError, TypeError) as exc:
                errors.append({"market":item["market"],"symbol":item["symbol"],"reason":type(exc).__name__+": provider_unavailable_or_ineligible"})
    return {"schema_version": 1, "source": "live_public", "fetched_at": as_of.isoformat(), "sessions": sessions,
            "calendar": {"library": xcals.__version__, "KR_extra_closures": sorted(KR_CLOSURES)},
            "profile": config["profile"], "series": sorted(series,key=lambda r:(r["market"],r["symbol"])),
            "benchmarks": benchmarks, "errors": errors,
            "universe": universe,
            "coverage": {"scope": config.get("scope", "configured_universe"), "requested":len(universe), "received":len(series),
                         "listings":listings,"scope_errors":scope_errors,
                         "market_wide":bool(listings and not scope_errors and config.get("limit_per_exchange") is None)}}
