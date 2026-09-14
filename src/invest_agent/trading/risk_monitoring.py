"""Deterministic morning observations, persisted only against unchanged input versions."""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta

from .buy_policy import approve_buy
from .market import check_bars
from .protection import monitor
from .risk_budget import RESERVING, review_budget
from .risk_ledger import RiskLedger
from .sell_policy import evaluate_sell


def observation_for(position: dict, market: dict, evaluated_at: str) -> dict:
    name = position["market"]
    if market.get("source") == "live_public":
        from .market_provider import completed_sessions
        sessions = completed_sessions(name, datetime.fromisoformat(evaluated_at))
    else:
        sessions = market.get("sessions", {}).get(name, [])
    expected = sessions[-1] if sessions else None
    result: dict = {"expected_session": expected, "realtime_available": False, "bars": []}
    def same_symbol(value):
        return value.removesuffix('.KS').removesuffix('.KQ') if name == 'KR' else value
    matches = [s for s in market.get("risk_series", [])
               if s["market"] == name and same_symbol(s["symbol"]) == same_symbol(position["symbol"])]
    if not matches:
        matches = [s for s in market.get("series", [])
                   if s["market"] == name and same_symbol(s["symbol"]) == same_symbol(position["symbol"])]
    if len(matches) != 1 or expected is None:
        return result
    series = matches[0]
    if market.get("source") == "synthetic" and position.get("source") != "synthetic":
        result["unavailable_reason"] = "SYNTHETIC_REAL_SOURCE_MISMATCH"
        return result
    if series.get("currency") != position["currency"]:
        result["unavailable_reason"] = "CURRENCY_MISMATCH"
        return result
    if series.get("price_basis") not in {"synthetic", "synthetic_unadjusted", "executable_raw", "yahoo_quote_ohlc_no_order_authority", "toss_raw_ohlc_no_order_authority"}:
        result["unavailable_reason"] = "PRICE_BASIS_UNAVAILABLE"
        return result
    bars = [deepcopy(b) for b in series.get("bars", []) if expected and b["date"] <= expected and b.get("complete", True)]
    try:
        check_bars(bars, sessions)
    except (ValueError, KeyError, TypeError):
        result["unavailable_reason"] = "INVALID_OR_STALE_BARS"
        return result
    for bar in bars:
        bar["complete"] = True
    result.update(bars=bars, daily=bars[-1])
    # Preserve an explicit indicator availability failure, not a forecast or
    # trading setting. The monitor still computes its fallback from raw bars.
    if series.get('atr_available') is False:
        result['atr_available'] = False
    day = date.fromisoformat(expected)
    monday = day - timedelta(days=day.weekday())
    if market.get("source") == "live_public":
        import exchange_calendars as xcals

        from .market_provider import KR_CLOSURES
        calendar = xcals.get_calendar("XKRX" if name == "KR" else "XNYS")
        week_sessions = [s.date().isoformat() for s in calendar.sessions_in_range(monday.isoformat(), (monday + timedelta(days=6)).isoformat())
                         if name != "KR" or s.date().isoformat() not in KR_CLOSURES]
        last = week_sessions[-1] if week_sessions else None
    else:
        last = market.get("week_last_sessions", {}).get(name, (monday + timedelta(days=4)).isoformat())
    if expected == last:
        week = [b for b in bars if monday.isoformat() <= b["date"] <= expected]
        result["weekly"] = {"date": expected, "last_session": last, "complete": True,
                            "close": week[-1]["close"]}
    return result


def evaluate(frozen: dict, market: dict, evaluated_at: str, account_input: dict | None = None) -> dict:
    from .account_sync import reconcile
    expected_sessions = {name: sessions[-1] for name, sessions in market.get('sessions', {}).items() if sessions}
    if market.get('source') == 'live_public':
        from .market_provider import completed_sessions
        expected_sessions = {name: completed_sessions(name, datetime.fromisoformat(evaluated_at))[-1] for name in ('KR', 'US')}
    result = reconcile(frozen, account_input, evaluated_at, expected_sessions) if account_input is not None else deepcopy(frozen)
    result["evaluated_at"] = evaluated_at
    previous = {m["position_id"]: m for m in frozen.get("monitor", [])}
    monitors, observations = [], {}
    for position in result.get("position", []):
        ident = position["position_id"]
        try:
            observed = observation_for(position, market, evaluated_at)
            status = monitor(position, observed, previous=previous.get(ident))
        except (ValueError, KeyError, TypeError) as exc:
            observed = {"expected_session": None}
            # A failed observation is missing data, not evidence that an earlier
            # confirmed breach was resolved. Reuse R1's latch and empty metrics.
            status = monitor(position, observed, previous=previous.get(ident))
            status["missing_data"].append(type(exc).__name__)
            status["notification_required"] = bool(
                status["notification_required"]
                or status["missing_data"] != previous.get(ident, {}).get("missing_data")
            )
            if all(status.get(key) == previous.get(ident, {}).get(key) for key in
                   ("state", "protection_version", "expected_session", "missing_data")):
                status["notification_required"] = False
        status["position_id"] = ident
        if observed.get("unavailable_reason"):
            status.setdefault("missing_data", []).append(observed["unavailable_reason"])
        if observed.get("daily"):
            position.update(current_market_price=str(observed["daily"]["close"]), market_data_session=observed["daily"]["date"])
        else:
            position["market_data_session"] = None
        position["breach_unresolved"] = status["state"] == "CONFIRMED_BREACH"
        monitors.append(status)
        observations[ident] = observed
    result["monitor"] = monitors
    positions = {p["position_id"]: p for p in result.get("position", [])}
    result["sell_plan"] = [evaluate_sell(p, positions[p["position_id"]], observations.get(p["position_id"], {}))
                           if p["position_id"] in positions else p for p in result.get("sell_plan", [])]
    configs = {c["strategy_group_id"]: c for c in result.get("risk_config", [])}
    snapshots = {s["strategy_group_id"]: s for s in result.get("budget_snapshots", [])}
    budgets = []
    for group in result.get("strategy", []):
        ident = group["strategy_group_id"]
        snapshot = snapshots.get(ident)
        if not snapshot:
            budgets.append({"strategy_group_id": ident, "risk_budget_status": "UNAVAILABLE", "manual_required": True,
                            "block_reasons": ["STRATEGY_EQUITY_UNAVAILABLE"]})
            continue
        # Daily-review checks must not mutate the immutable source snapshot.
        snapshot = deepcopy(snapshot)
        snapshot['evaluated_at'] = evaluated_at
        if snapshot.get('snapshot_type') != 'COMPLETED_SESSION':
            snapshot.setdefault('errors', []).append('COMPLETED_SESSION_EQUITY_REQUIRED')
        for name in snapshot.get("expected_sessions", {}):
            if market.get("source") == "live_public":
                from .market_provider import completed_sessions
                available_sessions = completed_sessions(name, datetime.fromisoformat(evaluated_at))
            else:
                available_sessions = market.get("sessions", {}).get(name, [])
            if available_sessions:
                latest = available_sessions[-1]
                snapshot["expected_sessions"][name] = latest
                if snapshot.get("data_as_of", "")[:10] < latest:
                    snapshot.setdefault("errors", []).append("STALE_DATA:EQUITY_SNAPSHOT")
        # Current market session controls freshness, never a stale equity input's own expectation.
        for p in result.get("position", []):
            expected = observations.get(p["position_id"], {}).get("expected_session")
            if expected:
                snapshot.setdefault("expected_sessions", {})[p["market"]] = expected
                if snapshot.get("data_as_of", "")[:10] < expected:
                    snapshot.setdefault("errors", []).append("STALE_DATA:EQUITY_SNAPSHOT")
        for index, plan in enumerate(result.get("buy_plan", [])):
            if plan["strategy_group_id"] != ident or plan["status"] not in RESERVING:
                continue
            checked = approve_buy(plan, group, configs.get(ident, {}), snapshot, result.get("position", []),
                                  result.get("buy_plan", []), result.get("sell_plan", []), actor="user", at=evaluated_at)
            if checked["block_reasons"]:
                revised = deepcopy(plan)
                revised.update(status="NEEDS_REAPPROVAL", block_reasons=checked["block_reasons"], risk_review=checked.get("risk_review"))
                result["buy_plan"][index] = revised
        budgets.append(review_budget(group, configs.get(ident, {}), snapshot, result.get("position", []), result.get("buy_plan", [])))
    result["budgets"] = budgets
    return result


def persist(store, run_id: str, frozen: dict, evaluated: dict, evaluated_at: str) -> dict:
    ledger = RiskLedger(store)
    old = ledger.get_entity("morning_observation", run_id)
    if old:
        return old["result"]
    conflicts = []
    for kind in ("position", "monitor", "sell_plan", "buy_plan", "strategy", "risk_config", "strategy_state"):
        original = {r.get("position_id") if kind in {"position", "monitor"} else r.get("sell_plan_id") if kind == "sell_plan"
                    else r.get("buy_plan_id") if kind == "buy_plan" else r.get("strategy_group_id"): r for r in frozen.get(kind, [])}
        current = ledger.entities(kind)
        current_ids = {r.get("position_id") if kind in {"position", "monitor"} else r.get("sell_plan_id") if kind == "sell_plan"
                       else r.get("buy_plan_id") if kind == "buy_plan" else r.get("strategy_group_id"): r for r in current}
        if set(original) != set(current_ids) or any(current_ids[k].get("version") != row.get("version") for k, row in original.items() if k in current_ids):
            conflicts.append(kind)
    evaluated = deepcopy(evaluated)
    evaluated["persistence"] = {"state": "VERSION_CONFLICT" if conflicts else "APPLIED", "changed_entities": conflicts}
    updates = []
    if not conflicts:
        for item in evaluated.get('equity_updates', []):
            existing = ledger.get_entity('equity_snapshot', item['equity_snapshot_id'])
            if existing is None:
                updates.append(('equity_snapshot', item['equity_snapshot_id'], item))
        for kind in ("position", "monitor", "sell_plan", "buy_plan", "strategy_state"):
            for item in evaluated.get(kind, []):
                ident = item["position_id"] if kind in {"position", "monitor"} else item["sell_plan_id"] if kind == "sell_plan" else item["strategy_group_id"] if kind == "strategy_state" else item["buy_plan_id"]
                updates.append((kind, ident, item))
    request = {"event_id": f"morning:{run_id}", "reason": "Completed-session morning observation; no orders",
               "occurred_at": evaluated_at, "data_as_of": evaluated_at, "input_run_id": run_id}
    saved = ledger._commit("morning_observation", run_id, {"result": evaluated}, request, actor="engine",
                           event_type="MORNING_POLICY_OBSERVED", updates=updates)
    return saved["result"]
