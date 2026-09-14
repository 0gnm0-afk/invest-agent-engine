"""R4 nominal risk and cash budgets. No inferred accounts, FX or risk ratios."""
from __future__ import annotations

from copy import deepcopy
from datetime import date
from decimal import ROUND_FLOOR, Decimal

from .portfolio import number

RESERVING = {"READY", "ACTIVE", "PARTIALLY_FILLED", "NEEDS_REAPPROVAL"}
ZERO = Decimal(0)


def reduction_scenarios(budget: dict, sell_plans: list[dict]) -> list[dict]:
    """Independent what-if rows using approved quantities; never mutate actual risk."""
    positions = {p['position_id']: p for p in budget.get('positions', [])}
    rows = []
    for plan in sell_plans:
        p = positions.get(plan['position_id'])
        if p is None or plan.get('approved_by_user') is not True or plan.get('status') in {'CANCELLED', 'COMPLETED', 'DRAFT', 'INACTIVE_RECOVERED'}:
            continue
        current_quantity = number(p['current_quantity'])
        if current_quantity <= 0:
            continue
        tranches = [{'tranche_id': 'initial_reduction', **plan.get('initial_reduction', {})}, *plan.get('tranches', [])]
        for tranche in tranches:
            if tranche.get('status') in {'EXECUTED', 'CANCELLED', 'SUPERSEDED'}:
                continue
            requested = number(tranche.get('remaining_planned_quantity', '0'))
            if requested <= 0:
                continue
            quantity = current_quantity if tranche.get('all_remaining') is True else min(requested, current_quantity)
            risk = p.get('position_open_risk')
            stale = bool(budget.get('stale_data_components'))
            # The frozen budget may retain last-known numeric risk for inspection.
            # Do not present a new actionable scenario from stale inputs.
            reduction = number(risk) * quantity / current_quantity if risk is not None and not stale else None
            total = budget.get('projected_portfolio_open_risk')
            rows.append({'sell_plan_id': plan['sell_plan_id'], 'position_id': p['position_id'],
                         'tranche_id': tranche['tranche_id'], 'quantity': str(quantity), 'currency': 'KRW',
                         'risk_reduction': str(reduction) if reduction is not None else None,
                         'position_risk_after': str(number(risk) - reduction) if reduction is not None else None,
                         'portfolio_risk_after_including_buy_reservations': str(number(total) - reduction) if total is not None and reduction is not None else None,
                         'authority': 'scenario_only',
                         'state': 'STALE_DATA' if stale else 'UNAVAILABLE' if reduction is None else 'PARTIAL' if total is None else 'AVAILABLE'})
    return rows


def strategy_group(value: dict) -> dict:
    included = value["included_account_ids"]
    excluded = value.get("excluded_account_ids", [])
    if not value.get("strategy_group_id") or not isinstance(included, list) or len(set(included)) != len(included):
        raise ValueError("Explicit unique strategy accounts required")
    if set(included) & set(excluded):
        raise ValueError("Included and excluded accounts overlap")
    result = deepcopy(value)
    result.update(base_currency="KRW", max_active_positions=5, target_base_weight_pct="0.20",
                  hard_max_position_weight_pct=None)
    return result


def risk_config(value: dict) -> dict:
    result = deepcopy(value)
    for field in ("per_position_risk_pct", "portfolio_open_risk_pct", "gap_buffer_pct", "round_trip_cost_pct"):
        v = value.get(field)
        if v is not None:
            parsed = number(v)
            if parsed > 1:
                raise ValueError("Risk ratios must be decimal fractions")
            result[field] = str(parsed)
        else:
            result[field] = None
    if value.get("enforcement_basis", "NOMINAL") != "NOMINAL":
        raise ValueError("Contract v1 enforces nominal risk only")
    result["enforcement_basis"] = "NOMINAL"
    result["risk_budget_status"] = ("ACTIVE" if all(result[k] is not None for k in
                                    ("per_position_risk_pct", "portfolio_open_risk_pct")) else "UNCONFIGURED")
    return result


def fx_issue(snapshot: dict, currency: str) -> str | None:
    if currency == "KRW":
        return None
    component = snapshot.get("fx", {}).get(currency)
    if not component or not all(component.get(k) for k in ("fx_rate", "fx_source", "fx_as_of")):
        return f"FX_MISSING:{currency}"
    try:
        observed = date.fromisoformat(component['fx_as_of'][:10])
        expected = max(date.fromisoformat(v) for v in snapshot['expected_sessions'].values())
        available_at = date.fromisoformat((snapshot.get('evaluated_at') or snapshot.get('created_at') or snapshot['data_as_of'])[:10])
    except (KeyError, TypeError, ValueError):
        return f"STALE_DATA:FX:{currency}"
    return f"STALE_DATA:FX:{currency}" if observed < expected or observed > available_at else None


def fx_rate(snapshot: dict, currency: str) -> Decimal | None:
    if currency == "KRW":
        return Decimal(1)
    component = snapshot.get("fx", {}).get(currency)
    if not component or fx_issue(snapshot, currency):
        return None
    return number(component["fx_rate"], positive=True)


def equity_snapshot(group: dict, raw: dict) -> dict:
    if raw.get("snapshot_type") not in {"COMPLETED_SESSION", "INTRADAY_PROVISIONAL", "MANUAL"}:
        raise ValueError("Explicit snapshot type required")
    for key in ("equity_snapshot_id", "data_as_of", "expected_sessions"):
        if not raw.get(key):
            raise ValueError(f"Missing {key}")
    accounts = {}
    for account in raw.get("accounts", []):
        if account["account_id"] in accounts:
            raise ValueError("Duplicate account snapshot")
        accounts[account["account_id"]] = account
    errors: list[str] = []
    components: list[dict] = []
    cash: dict[str, Decimal] = {}
    total = ZERO
    cash_complete = True
    if not group["included_account_ids"]:
        errors.append("STRATEGY_ACCOUNTS_UNSET")
    for ident in group["included_account_ids"]:
        account = accounts.get(ident)
        if account is None:
            errors.append(f"ACCOUNT_MISSING:{ident}")
            cash_complete = False
            continue
        currency = account["currency"]
        rate = fx_rate(raw, currency)
        if rate is None:
            errors.append(fx_issue(raw, currency) or f"FX_MISSING:{currency}")
        if not account.get("expected_session") or account.get("data_as_of", "")[:10] < account["expected_session"]:
            errors.append(f"STALE_DATA:{ident}")
        value = account.get("net_liquidation_value")
        method = "BROKER_NET_LIQUIDATION"
        if value is None:
            fields = ("cash", "long_position_market_value", "receivables", "payables", "liabilities", "accrued_interest_and_fees")
            parts = account.get("equity_components", {})
            if any(parts.get(k) is None for k in fields):
                errors.append(f"EQUITY_COMPONENT_MISSING:{ident}")
                value = None
            else:
                amounts = {k: number(parts[k]) for k in fields}
                value = sum((amounts[k] for k in fields[:3]), ZERO) - sum((amounts[k] for k in fields[3:]), ZERO)
            method = "NONOVERLAPPING_COMPONENTS"
        elif value is not None:
            value = number(value)
        base = value * rate if value is not None and rate is not None else None
        if base is not None:
            total += base
        components.append({"account_id": ident, "currency": currency,
                           "equity_value_native": str(value) if value is not None else None,
                           "fx_rate": str(rate) if rate is not None else None,
                           "equity_value_base": str(base) if base is not None else None,
                           "valuation_method": method, "data_as_of": account.get("data_as_of"),
                           "source": account.get("source")})
        available = account.get("available_cash")
        if available is None or account.get("cash_complete") is not True:
            cash_complete = False
        else:
            for ccy, amount in available.items():
                cash[ccy] = cash.get(ccy, ZERO) + number(amount)
    unknown_equity = any(not e.startswith("STALE_DATA:") for e in errors) or any(c['equity_value_base'] is None for c in components)
    return {**deepcopy(raw), "strategy_group_id": group["strategy_group_id"], "base_currency": "KRW",
            "strategy_equity": None if unknown_equity else str(total),
            "strategy_equity_status": "UNAVAILABLE" if unknown_equity else "AVAILABLE",
            "account_components": components, "fx_components": deepcopy(raw.get("fx", {})),
            "available_cash": {k: str(v) for k, v in cash.items()} if cash_complete else None,
            "errors": errors}


def plan_reservation(plan: dict) -> tuple[Decimal, Decimal]:
    if plan.get("status") == "NEEDS_REAPPROVAL" and plan.get("approved_by_user") is False and plan.get("approved_reservation"):
        frozen = plan["approved_reservation"]
        return number(frozen["cash"]), number(frozen["risk"])
    stop = number(plan["adopted_stop_price"], positive=True)
    cash = risk = ZERO
    for tranche in plan["tranches"]:
        if tranche.get("status") in {"CANCELLED", "FILLED", "SUPERSEDED"}:
            continue
        qty = number(tranche["remaining_quantity"])
        price = number(tranche["risk_entry_price"], positive=True)
        # Invalid revised plans retain the last approved reservation until cancellation.
        if price <= stop and qty > 0:
            frozen = plan.get("approved_reservation")
            if frozen:
                return number(frozen["cash"]), number(frozen["risk"])
            raise ValueError("Invalid long stop for reserved tranche")
        cash += price * qty
        risk += (price - stop) * qty
    return cash, risk


def review_budget(group: dict, config: dict, snapshot: dict, positions: list[dict],
                  plans: list[dict], new_plan: dict | None = None) -> dict:
    config = risk_config(config)
    errors = list(snapshot.get("errors", []))
    errors.extend(issue for currency in snapshot.get('fx', {}) if (issue := fx_issue(snapshot, currency)))
    if config["risk_budget_status"] != "ACTIVE":
        errors.append("RISK_CONFIG_MISSING")
    equity = snapshot.get("strategy_equity")
    equity = Decimal(equity) if equity is not None else None
    if equity is None or equity <= 0:
        errors.append("STRATEGY_EQUITY_UNAVAILABLE")
    included = set(group["included_account_ids"])
    current = [p for p in positions if p.get("strategy_group_id") == group["strategy_group_id"] and
               p["account_alias"] in included and number(p["current_quantity"]) > 0]
    rows: list[dict] = []
    instruments: dict[tuple[str, str], dict] = {}
    subtotal = ZERO
    unknown, breached = [], []
    for p in current:
        ident = p["position_id"]
        key = (p["market"], p["symbol"])
        instrument = instruments.setdefault(key, {"risk": ZERO, "value": ZERO, "complete": True})
        rate = fx_rate(snapshot, p["currency"])
        price = p.get("current_market_price")
        stop = p.get("current_protection_price")
        q = number(p["current_quantity"])
        row = {"position_id": ident, "market": p["market"], "symbol": p["symbol"],
               "current_quantity": str(q), "average_cost": p["average_cost"], "current_market_price": price,
               "current_protection_price": stop, "position_open_risk": None,
               "stop_execution_pnl": None, "risk_status": "AVAILABLE"}
        expected = snapshot.get("expected_sessions", {}).get(p["market"])
        if not expected or p.get("market_data_session") != expected:
            errors.append(f"STALE_DATA:{ident}")
        if rate is None:
            errors.append(f"FX_MISSING:{ident}")
            row["risk_status"] = "FX_MISSING"
        elif price is None:
            row["risk_status"] = "MARKET_PRICE_MISSING"
        else:
            price = number(price, positive=True)
            instrument["value"] += price * q * rate
            if stop is None:
                row["risk_status"] = "MISSING_PROTECTION"
            else:
                stop = number(stop, positive=True)
                row["stop_execution_pnl"] = str((stop - number(p["average_cost"])) * q * rate)
                row["distance_to_protection"] = str(price - stop)
                if price <= stop or p.get("breach_unresolved") is True:
                    row["risk_status"] = "BREACHED_UNRESOLVED"
                    row["breach_shortfall"] = str(max(stop - price, ZERO) * q * rate)
                    breached.append(ident)
                else:
                    risk = (price - stop) * q * rate
                    row["position_open_risk"] = str(risk)
                    subtotal += risk
                    instrument["risk"] += risk
        if row["position_open_risk"] is None:
            unknown.append(ident)
            instrument["complete"] = False
        rows.append(row)
    if unknown:
        errors.append("POSITION_RISK_UNAVAILABLE")
    reserved = ZERO
    by_instrument: dict[tuple[str, str], Decimal] = {}
    reserved_value: dict[tuple[str, str], Decimal] = {}
    cash_reserved: dict[str, Decimal] = {}
    unknown_reservations = []
    unknown_reserved_instruments = set()
    for plan in plans:
        if (plan.get("strategy_group_id") != group["strategy_group_id"] or plan.get("status") not in RESERVING or
                (new_plan and plan["buy_plan_id"] == new_plan["buy_plan_id"])):
            continue
        cash, risk = plan_reservation(plan)
        key = (plan["market"], plan["symbol"])
        # Native cash remains reserved even when its KRW conversion is unavailable.
        cash_reserved[plan["currency"]] = cash_reserved.get(plan["currency"], ZERO) + cash
        rate = fx_rate(snapshot, plan["currency"])
        if rate is None:
            errors.append(f"FX_MISSING:{plan['buy_plan_id']}")
            unknown_reservations.append(plan['buy_plan_id'])
            unknown_reserved_instruments.add(key)
            continue
        reserved += risk * rate
        key = (plan["market"], plan["symbol"])
        by_instrument[key] = by_instrument.get(key, ZERO) + risk * rate
        reserved_value[key] = reserved_value.get(key, ZERO) + cash * rate
    active = config["risk_budget_status"] == "ACTIVE" and equity is not None and equity > 0
    per_budget = equity * number(config["per_position_risk_pct"]) if equity is not None and active else None
    total_budget = equity * number(config["portfolio_open_risk_pct"]) if equity is not None and active else None
    current_over = total_budget is not None and per_budget is not None and (subtotal > total_budget or any(v["risk"] > per_budget for v in instruments.values()))
    if current_over:
        errors.append("OVER_BUDGET_REVIEW_REQUIRED")
    if total_budget is not None and per_budget is not None and (subtotal + reserved > total_budget or any(
        instruments.get(key, {"risk": ZERO})["risk"] + amount > per_budget for key, amount in by_instrument.items()
    )):
        errors.append("RISK_BUDGET_EXCEEDED")
    instrument_rows = []
    for key in sorted(set(instruments) | set(by_instrument) | unknown_reserved_instruments):
        holding = instruments.get(key, {"risk": ZERO, "value": ZERO, "complete": True})
        pending = by_instrument.get(key, ZERO)
        instrument_rows.append({"market": key[0], "symbol": key[1],
                                "current_open_risk": str(holding["risk"]) if holding["complete"] else None,
                                "reserved_planned_risk": None if key in unknown_reserved_instruments else str(pending),
                                "known_reserved_risk_subtotal": str(pending),
                                "projected_open_risk": str(holding["risk"] + pending) if holding["complete"] and key not in unknown_reserved_instruments else None,
                                "current_position_weight_pct": str(holding["value"] / equity) if equity and equity > 0 and holding["complete"] else None,
                                "projected_position_weight_pct": str((holding["value"] + reserved_value.get(key, ZERO)) / equity) if equity and equity > 0 and holding["complete"] and key not in unknown_reserved_instruments else None,
                                "risk_budget_amount": str(per_budget) if active else None,
                                "over_budget_amount": str(max(holding["risk"] + pending - per_budget, ZERO)) if per_budget is not None and holding["complete"] and key not in unknown_reserved_instruments else None})
    result = {"strategy_group_id": group["strategy_group_id"], "strategy_equity": snapshot.get("strategy_equity"),
              "risk_budget_snapshot_id": snapshot.get('equity_snapshot_id'),
              "base_currency": "KRW", "equity_snapshot_type": snapshot.get("snapshot_type"),
              "equity_data_as_of": snapshot.get("data_as_of"), "risk_budget_status": config["risk_budget_status"],
              "per_position_risk_pct": config["per_position_risk_pct"], "portfolio_open_risk_pct": config["portfolio_open_risk_pct"],
              "per_position_risk_budget_amount": str(per_budget) if per_budget is not None else None,
              "portfolio_risk_budget_amount": str(total_budget) if total_budget is not None else None,
              "known_open_risk_subtotal": str(subtotal), "current_portfolio_open_risk": None if unknown else str(subtotal),
              "reserved_planned_risk": None if unknown_reservations else str(reserved), "unknown_risk_positions": unknown,
              "known_reserved_risk_subtotal": str(reserved), "unknown_risk_reservations": unknown_reservations,
              "reserved_cash_by_currency": {ccy: str(value) for ccy, value in cash_reserved.items()},
              "breached_unresolved_positions": breached, "positions": rows, "active_instrument_count": len(instruments),
              "remaining_portfolio_risk_capacity": str(total_budget - subtotal - reserved) if total_budget is not None and not unknown else None,
              "risk_status": "OVER_BUDGET_REVIEW_REQUIRED" if current_over else "AVAILABLE",
              "adjusted_risk": None, "nominal_risk_available": not unknown}
    result.update(instruments=instrument_rows,
                  projected_portfolio_open_risk=str(subtotal + reserved) if not unknown else None,
                  portfolio_over_budget_amount=str(max(subtotal + reserved - total_budget, ZERO)) if total_budget is not None and not unknown else None)
    if new_plan is not None:
        key = (new_plan["market"], new_plan["symbol"])
        held = instruments.get(key, {"risk": ZERO, "value": ZERO, "complete": True})
        cash, risk = plan_reservation(new_plan)
        rate = fx_rate(snapshot, new_plan["currency"])
        if rate is None:
            errors.append("FX_MISSING")
        else:
            projected_position = held["risk"] + by_instrument.get(key, ZERO) + risk * rate
            projected_total = subtotal + reserved + risk * rate
            weight = (held["value"] + cash * rate) / equity if equity is not None and equity > 0 else None
            result.update(projected_position_open_risk=str(projected_position) if held["complete"] else None,
                          projected_portfolio_open_risk=str(projected_total) if not unknown else None,
                          projected_position_weight_pct=str(weight) if weight is not None else None,
                          new_plan_nominal_risk=str(risk * rate), new_plan_reserved_cash=str(cash))
            if per_budget is not None and total_budget is not None and (projected_position > per_budget or projected_total > total_budget):
                errors.append("RISK_BUDGET_EXCEEDED")
            if weight is not None and weight > Decimal("0.20") and not (
                new_plan.get("overweight_approval") is True and new_plan.get("overweight_reason") and
                new_plan.get("relative_superiority_basis") and new_plan.get("overweight_approved_at")
            ):
                errors.append("OVERWEIGHT_APPROVAL_REQUIRED")
            available = snapshot.get("available_cash")
            if available is None or new_plan["currency"] not in available:
                errors.append("CASH_UNAVAILABLE")
                remaining_cash = None
            else:
                remaining_cash = number(available[new_plan["currency"]]) - cash_reserved.get(new_plan["currency"], ZERO)
                if cash > remaining_cash:
                    errors.append("INSUFFICIENT_CASH")
            result["available_cash_to_plan"] = str(remaining_cash) if remaining_cash is not None else None
            if key not in instruments and len(instruments) >= 5:
                errors.append("PORTFOLIO_POSITION_LIMIT")
            if per_budget is not None and total_budget is not None and not unknown:
                remaining_position = per_budget - held["risk"] - by_instrument.get(key, ZERO)
                remaining_total = total_budget - subtotal - reserved
                result["remaining_position_risk_capacity"] = str(remaining_position)
                result["maximum_quantities"] = []
                for t in new_plan["tranches"]:
                    price = number(t["risk_entry_price"], positive=True)
                    unit = (price - number(new_plan["adopted_stop_price"])) * rate
                    lot = number(new_plan["quantity_step"], positive=True)
                    q_risk = max(ZERO, (min(remaining_position, remaining_total) / unit / lot).to_integral_value(rounding=ROUND_FLOOR) * lot)
                    q_cash = max(ZERO, (remaining_cash / price / lot).to_integral_value(rounding=ROUND_FLOOR) * lot) if remaining_cash is not None else None
                    result["maximum_quantities"].append({"tranche_id": t["tranche_id"], "max_quantity_by_risk": str(q_risk),
                                                         "max_quantity_by_cash": str(q_cash) if q_cash is not None else None,
                                                         "max_approvable_quantity": str(min(q_risk, q_cash)) if q_cash is not None else None})
            if config["gap_buffer_pct"] is not None and config["round_trip_cost_pct"] is not None:
                result["adjusted_risk"] = str((risk + cash * (number(config["gap_buffer_pct"]) + number(config["round_trip_cost_pct"]))) * rate)
    # Unknown conversions are not zero-risk reservations. Keep known subtotals,
    # but suppress totals/capacity/sizing that otherwise look fully computable.
    new_fx_missing = new_plan is not None and fx_rate(snapshot, new_plan["currency"]) is None
    if unknown_reservations or new_fx_missing:
        result.update(projected_portfolio_open_risk=None, portfolio_over_budget_amount=None,
                      maximum_quantities=None, risk_status="UNAVAILABLE")
        if unknown_reservations:
            result["remaining_portfolio_risk_capacity"] = None
        if new_plan and ((new_plan["market"], new_plan["symbol"]) in unknown_reserved_instruments or new_fx_missing):
            result.update(projected_position_open_risk=None, remaining_position_risk_capacity=None)
        if new_fx_missing:
            result.update(new_plan_nominal_risk=None, projected_position_weight_pct=None)
    result["block_reasons"] = sorted(set(errors))
    result['stale_data_components'] = sorted(e for e in set(errors) if e.startswith('STALE_DATA'))
    result['fx_missing_components'] = sorted(e for e in set(errors) if e.startswith('FX_MISSING'))
    result["approval_allowed"] = not errors
    result["official_daily_review_status"] = "AVAILABLE" if snapshot.get('snapshot_type') == 'COMPLETED_SESSION' and not errors else "UNAVAILABLE"
    result["manual_required"] = bool(errors)
    for row in instrument_rows:
        related = [p for p in plans if p.get('strategy_group_id') == group['strategy_group_id']
                   and (p['market'], p['symbol']) == (row['market'], row['symbol'])]
        row['overweight_approval_by_plan'] = [
            {'buy_plan_id': p['buy_plan_id'], 'status': p['status'],
             'approved': p.get('approved_by_user') is True and p.get('overweight_approval') is True
                         and bool(p.get('overweight_reason') and p.get('relative_superiority_basis') and p.get('overweight_approved_at'))}
            for p in related]
        # Portfolio-wide unknowns/limits also prevent this instrument's new risk
        # approval. This is a review flag, never an automatic sale instruction.
        row['manual_required'] = bool(errors) or row['projected_open_risk'] is None
        row['review_reasons'] = result['block_reasons']
    return result
