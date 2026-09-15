"""Decimal-based portfolio scenarios. No broker writes or plan adoption."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import ROUND_FLOOR, Decimal, InvalidOperation


def number(value, *, positive=False) -> Decimal:
    if isinstance(value,bool):
        raise ValueError("Boolean is not a monetary value")  # noqa: TRY004 -- Preserve the existing ValueError validation contract for callers.
    try:
        parsed = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("Invalid monetary value") from exc
    if not parsed.is_finite() or parsed < 0 or (positive and parsed == 0):
        raise ValueError("Expected a finite nonnegative amount")
    return parsed


def review(bundle: dict, now: datetime | None = None) -> dict:
    from .cash_assets import is_cash, summarize
    now = now or datetime.now(timezone.utc)
    if bundle.get("schema_version") != 1 or bundle.get("source") not in ("synthetic", "broker_export"):
        raise ValueError("Expected a versioned broker snapshot or synthetic input")
    stamp = datetime.fromisoformat(bundle["as_of"])
    if stamp.tzinfo is None or now.tzinfo is None or stamp > now:
        raise ValueError("Invalid snapshot timestamp")
    max_age = number(bundle["max_age_hours"],positive=True)
    stale = Decimal(str((now-stamp).total_seconds())) > max_age*3600
    rates = {k:number(v,positive=True) for k,v in bundle["fx_to_base"].items()}
    if rates.get(bundle["base_currency"]) != 1:
        raise ValueError("Base currency FX must be 1")
    positions, errors = [], []
    if bundle.get('accounts_complete') is False:
        errors.append('account_list_incomplete')
    cash, value, exposure = Decimal(0), Decimal(0), Decimal(0)
    seen_accounts, seen_positions = set(), set()
    for account in bundle["accounts"]:
        alias = account["alias"]
        if not alias or alias in seen_accounts:
            raise ValueError("Duplicate/empty account alias")
        seen_accounts.add(alias)
        if account.get('cash_complete') is False:errors.append(f'{alias}: gross_cash_balance_unavailable')
        if account.get('holdings_complete') is False:errors.append(f'{alias}: holdings_snapshot_incomplete')
        for currency, amount in account["cash"].items():
            if currency not in rates:
                errors.append(f"{alias}: missing FX for {currency}")
            else:
                cash += number(amount)*rates[currency]
        for pos in account["positions"]:
            if is_cash(pos, bundle):
                continue
            key=(alias,pos["symbol"])
            if key in seen_positions:
                raise ValueError("Duplicate position; aggregate broker lots first")
            seen_positions.add(key)
            base={"account":alias,"symbol":pos["symbol"],"currency":pos["currency"],
                   'market':pos.get('market'),'quote_symbol':pos.get('quote_symbol'),'name':pos.get('name')}
            if pos.get('data_unavailable_reason'):
                positions.append({**base,'state':'unavailable','reason':pos['data_unavailable_reason']})
                errors.append(f"{alias}/{pos['symbol']}: holding_values_unavailable")
                continue
            qty, price, cost = number(pos["quantity"]),number(pos["price"],positive=True),number(pos["average_cost"])
            if pos["currency"] not in rates:
                positions.append({**base,"state":"unavailable","reason":"missing_fx"})
                errors.append(f"{alias}/{pos['symbol']}: missing FX")
                continue
            fx=rates[pos["currency"]]
            market_value=qty*price*fx
            value+=market_value
            row={**base,"quantity":str(qty),"market_value_base":str(market_value),"unrealized_pnl_base":str((price-cost)*qty*fx),
                 "state":"needs_stop", "stop_exposure_base":None}
            adopted=pos.get("adopted_stop")
            if adopted and not adopted.get('rule') and adopted.get("adoption_ref") and adopted.get("price_basis") == "executable_raw":
                stop=number(adopted["price"],positive=True)
                down=max(price-stop,Decimal(0))*qty*fx
                # R4: a breached, unsold position has unknown remaining risk.
                # The old max(..., 0) display incorrectly implied zero risk.
                breached = price <= stop and qty > 0
                if not breached:
                    exposure+=down
                row.update(state="stop_breached" if price<=stop else "review",stop_price=str(stop),
                           stop_distance_pct=str((price-stop)/price),stop_exposure_base=None if breached else str(down),
                           risk_status="BREACHED_UNRESOLVED" if breached else "AVAILABLE",
                           breach_shortfall_base=str(max(stop-price,Decimal(0))*qty*fx) if breached else None,
                           stop_pnl_before_costs_base=str((stop-cost)*qty*fx),adoption_ref=adopted["adoption_ref"])
            positions.append(row)
    cash_assets = summarize(bundle)
    values = cash_assets.pop('risk_position_values')
    complete_equity = cash_assets['valuation_complete']
    equity = Decimal(cash_assets['managed_equity_base']) if complete_equity else Decimal(0)
    cash = Decimal(cash_assets['cash_equivalent_value']) if complete_equity else Decimal(0)
    if complete_equity:
        errors = [e for e in errors if not e.endswith('gross_cash_balance_unavailable')]
    errors.extend(cash_assets['issues'])
    for row in positions:
        allocated = values.get((row['account'], row['symbol']))
        if allocated is not None:
            row['market_value_base'] = allocated
        row["weight"] = str(number(row["market_value_base"])/equity) if complete_equity and equity>0 and "market_value_base" in row else None
        row['position_market_value'] = row.get('market_value_base')
        row['portfolio_weight'] = row['weight']
        row['reference_weight'] = '0.20'
        row['difference_from_reference_weight'] = str(Decimal(row['weight']) - Decimal('0.20')) if row['weight'] is not None else None
    return {"schema_version":1,"source":bundle["source"],"as_of":bundle["as_of"],"base_currency":bundle["base_currency"],
            'cash_assets':cash_assets, 'managed_equity_base':cash_assets['managed_equity_base'],
            'cash_equivalent_value':cash_assets['cash_equivalent_value'], 'risk_asset_value_base':cash_assets['risk_asset_value_base'],
            'risk_asset_count':len(positions), 'verified_investable_cash':None,
            "equity_base":str(equity) if complete_equity else None,"cash_base":str(cash) if complete_equity else None,
            "known_stop_exposure_base":str(exposure),"stop_exposure_complete":all(r.get("stop_exposure_base") is not None for r in positions) and complete_equity,
            "stale":stale,"positions":positions,"errors":errors,"authority":"review_only_no_orders"}


def broker_allocation(account: dict) -> dict:
    """Validate display-only broker asset values; never create buying power."""
    entry = account.get('broker_allocation', {})
    unavailable = {'state': 'unavailable'}
    if entry.get('state') != 'available':
        return unavailable
    try:
        if entry['as_of'] != account['as_of'] or entry['currency'] != 'KRW' or entry['authority'] != 'valuation_weights_only_no_buying_power':
            return unavailable
        if not entry.get('confirmation_hash') or not entry.get('source_hash'):
            return unavailable
        allowed = {(a['alias'], p['symbol']) for a in account['accounts'] for p in a['positions']}
        allowed.update((a['alias'], None) for a in account['accounts'])
        total = number(entry['total_krw'], positive=True)
        rows, seen, combined = [], set(), Decimal(0)
        for row in entry['rows']:
            key = (row['account'], row['symbol'])
            if key not in allowed or key in seen:
                return unavailable
            seen.add(key)
            value = number(row['value_krw'])
            combined += value
            rows.append({**row, 'value_krw': str(value), 'weight': str(value / total)})
        if combined != total or not allowed.issubset(seen):
            return unavailable
        return {'state': 'available', 'total_krw': str(total), 'rows': rows, 'as_of': entry['as_of']}
    except (KeyError, TypeError, ValueError):
        return unavailable


def size_preview(request: dict) -> dict:
    """User-supplied caps constrain a hypothetical new/add tranche; no defaults."""
    result: dict={"authority":"scenario_only","state":"blocked","reasons":[]}
    entry,stop=number(request["entry"],positive=True),number(request["stop"],positive=True)
    if stop>=entry:
        result["reasons"].append("stop_must_be_below_entry")
        return result
    qty=number(request["existing_quantity"])
    price=number(request["current_price"],positive=True)
    cost=number(request["average_cost"])
    if qty>0 and price<cost:
        result["reasons"].append("unplanned_loss_add_blocked_Q02A; preadopted_plan_exception_not_implemented")
        return result
    if request.get("snapshot_stale") or not request.get("open_orders_reconciled"):
        result["reasons"].append("fresh_snapshot_and_reconciled_orders_required")
        return result
    # available_cash must already be net of broker reservations. Do not subtract again.
    cash=number(request["available_cash"])
    total_budget=number(request["position_loss_budget"])
    other_risk=number(request["other_portfolio_risk"])
    portfolio_budget=number(request["portfolio_loss_budget"])
    stress=number(request["slippage_per_share"])+number(request["cost_per_share"])
    existing_risk=max(price-stop,Decimal(0))*qty
    remaining=min(total_budget-existing_risk,portfolio_budget-other_risk-existing_risk)
    weight_cap=number(request["position_value_cap"])
    value_room=weight_cap-price*qty
    step=number(request["quantity_step"],positive=True)
    if remaining<=0 or value_room<=0:
        result["reasons"].append("no_remaining_risk_or_weight_budget")
        return result
    raw=min(cash/(entry+stress),remaining/(entry-stop+stress),value_room/entry)
    new_qty=(raw/step).to_integral_value(rounding=ROUND_FLOOR)*step
    if new_qty<=0:
        result["reasons"].append("below_quantity_step")
        return result
    fractions=[number(v,positive=True) for v in request["split_fractions"]]
    if not fractions or sum(fractions)!=1:
        raise ValueError("split_fractions must sum exactly to 1")
    parts=[(new_qty*f/step).to_integral_value(rounding=ROUND_FLOOR)*step for f in fractions]
    parts[-1]+=new_qty-sum(parts)
    return {"authority":"scenario_only","state":"preview","quantity":str(new_qty),"split_quantities":[str(p) for p in parts],
            "cash_required":str(new_qty*(entry+stress)),"post_add_quantity":str(qty+new_qty),
            "post_add_average_cost":str((cost*qty+entry*new_qty)/(qty+new_qty)),
            "post_add_stop_exposure":str(existing_risk+new_qty*(entry-stop+stress)),
            "warning":"Stop execution price is not guaranteed; this scenario does not adopt a plan or reserve orders."}
