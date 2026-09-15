"""R3 preapproved buy tranches, immutable expansion boundary after first fill."""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal

from .portfolio import number
from .protection import TRIGGER_MODES
from .risk_budget import RESERVING, plan_reservation, review_budget

KINDS = {"INITIAL_ENTRY", "PYRAMID_ADD", "REENTRY"}
TRIGGERS = {"LIMIT_AT_OR_BELOW", "BREAKOUT_AT_OR_ABOVE", "PRICE_RANGE", "MANUAL_CONFIRMATION"}


def validate_buy(plan: dict) -> list[str]:
    errors = []
    if plan.get("plan_kind") not in KINDS or plan.get("market") not in {"KR", "US"}:
        raise ValueError("Unsupported buy kind or market")
    if plan.get("adopted_stop_price") is None:
        errors.append("STOP_MISSING")
    if plan.get('trigger_mode') not in TRIGGER_MODES:
        errors.append('TRIGGER_MODE_MISSING')
    stop = number(plan["adopted_stop_price"], positive=True) if plan.get("adopted_stop_price") is not None else None
    if not plan.get("tranches"):
        return [*errors, "QUANTITY_MISMATCH"]
    lot = number(plan["quantity_step"], positive=True)
    seen, sequence = set(), set()
    total = Decimal(0)
    for t in plan["tranches"]:
        if not t.get("tranche_id") or t["tranche_id"] in seen or t["sequence"] in sequence:
            raise ValueError("Unique tranche IDs and sequences required")
        seen.add(t["tranche_id"]); sequence.add(t["sequence"])
        if not isinstance(t.get('trigger_basis'), str) or not t['trigger_basis'].strip():
            errors.append('TRIGGER_BASIS_MISSING')
        quantity = number(t["planned_quantity"])
        total += quantity
        if quantity % lot:
            errors.append("QUANTITY_MISMATCH")
        if t.get("trigger_type") not in TRIGGERS:
            raise ValueError("Unsupported buy trigger")
        kind = t["trigger_type"]
        upper = t.get("trigger_price") if kind == "LIMIT_AT_OR_BELOW" else (
            t.get("price_range_upper") if kind == "PRICE_RANGE" else t.get("max_entry_price"))
        if upper is None or t.get("risk_entry_price") is None:
            errors.append("RISK_ENTRY_PRICE_MISSING")
            continue
        upper = number(upper, positive=True)
        price = number(t["risk_entry_price"], positive=True)
        if upper != price:
            errors.append("RISK_ENTRY_PRICE_MISMATCH")
        if stop is not None and price <= stop:
            errors.append("INVALID_STOP")
        if kind == "PRICE_RANGE" and (t.get("price_range_lower") is None or number(t["price_range_lower"], positive=True) > upper):
            errors.append("INVALID_PRICE_RANGE")
        if kind == "BREAKOUT_AT_OR_ABOVE" and (t.get("trigger_price") is None or number(t["trigger_price"], positive=True) > upper):
            errors.append("INVALID_PRICE_RANGE")
    if total != number(plan["total_planned_quantity"]) or total <= 0:
        errors.append("QUANTITY_MISMATCH")
    return sorted(set(errors))


def buy_draft(value: dict, old: dict | None = None, *, at: str | None = None) -> dict:
    fields = {"buy_plan_id", "strategy_group_id", "position_id", "account_alias", "symbol", "market", "currency", "plan_kind",
              "adopted_stop_price", "stop_candidate_id", "trigger_mode", "total_planned_quantity", "quantity_step",
              "overweight_approval", "overweight_reason", "relative_superiority_basis", "overweight_approved_at",
              "execution_conditions", "tranches"}
    plan = {k: deepcopy(v) for k, v in value.items() if k in fields}
    for key in ("buy_plan_id", "strategy_group_id", "account_alias", "symbol", "currency"):
        if not plan.get(key):
            raise ValueError(f"Missing {key}")
    prior = {t["tranche_id"]: t for t in (old or {}).get("tranches", [])}
    if old and any(plan.get(k) != old.get(k) for k in ("buy_plan_id", "strategy_group_id", "account_alias", "market", "symbol", "currency", "plan_kind", "position_id")):
        raise ValueError("Plan identity cannot change")
    for t in plan.get("tranches", []):
        before = prior.get(t["tranche_id"], {})
        fills = deepcopy(before.get("actual_fills", []))
        filled = sum((number(f["actual_quantity"]) for f in fills), Decimal(0))
        planned = number(t["planned_quantity"])
        if planned < filled:
            raise ValueError("Cannot remove already executed quantity")
        requested_cancel = t.get("status") == "CANCELLED"
        t.update(actual_fills=fills, remaining_quantity=str(Decimal(0) if requested_cancel else planned - filled),
                 status="CANCELLED" if requested_cancel else "FILLED" if planned == filled else "PLANNED")
        t.update(created_at=before.get('created_at', at if not before else None), updated_at=at,
                 state_transitions=deepcopy(before.get('state_transitions', [])))
    if old and old.get("locked_at_first_fill"):
        if {t["tranche_id"] for t in plan["tranches"]} != set(prior):
            raise ValueError("Locked plan cannot add/remove tranche identities; cancel remaining quantity")
        if number(plan["adopted_stop_price"]) < number(old["adopted_stop_price"]):
            raise ValueError("Locked stop cannot decrease")
        if number(plan["total_planned_quantity"]) > number(old["total_planned_quantity"]):
            raise ValueError("Locked quantity cannot increase")
        for t in plan["tranches"]:
            before = prior[t["tranche_id"]]
            if number(t["planned_quantity"]) > number(before["planned_quantity"]):
                raise ValueError("Locked tranche quantity cannot increase")
            if before["status"] == "CANCELLED" and t["status"] != "CANCELLED":
                raise ValueError("Cancelled tranche cannot be restored")
            for field in ("risk_entry_price", "max_entry_price", "price_range_upper"):
                if t.get(field) is not None and (before.get(field) is None or number(t[field]) > number(before[field])):
                    raise ValueError("Locked entry ceiling cannot increase")
            if before.get("price_range_lower") is not None and number(t.get("price_range_lower", "0")) < number(before["price_range_lower"]):
                raise ValueError("Locked price range cannot expand")
        old_cash, old_risk = plan_reservation(old)
        cash, risk = plan_reservation(plan)
        if cash > old_cash or risk > old_risk:
            raise ValueError("Locked plan cannot increase reserved cash or risk")
    plan.update(status="NEEDS_REAPPROVAL" if old and old.get("status") in RESERVING else "DRAFT",
                approved_by_user=False, locked_at_first_fill=(old or {}).get("locked_at_first_fill"),
                history=[*(old or {}).get("history", []), *([{k: v for k, v in old.items() if k != "history"}] if old else [])])
    if old and old.get("approved_reservation"):
        # Reconstruct remaining commitment after confirmed fills. Older records
        # may still carry their original full-size approval reservation.
        cash, risk = plan_reservation(old)
        plan["approved_reservation"] = {'cash': str(cash), 'risk': str(risk)}
        # A revision is not approval or a release of the existing commitment.
        plan['total_reserved_cash'] = str(cash)
        plan['total_nominal_planned_risk'] = str(risk)
    plan['total_remaining_quantity'] = str(sum((number(t['remaining_quantity']) for t in plan.get('tranches', [])), Decimal(0)))
    # Actual executions and their original post-trade review survive revisions.
    # Never accept these authority-bearing fields from the submitted draft.
    for field in ('execution_deviation', 'execution_deviations', 'post_trade_review', 'post_trade_status',
                  'risk_budget_snapshot_id'):
        if old and field in old:
            plan[field] = deepcopy(old[field])
    plan["block_reasons"] = validate_buy(plan)
    return plan


def approve_buy(plan: dict, group: dict, config: dict, snapshot: dict, positions: list[dict], plans: list[dict],
                sell_plans: list[dict], *, actor: str, at: str) -> dict:
    if actor != "user":
        raise ValueError("Only user can approve buy quantities")
    result = deepcopy(plan)
    # Evaluate this proposed revision, not its old still-reserved commitment.
    result["status"] = "DRAFT"
    errors = validate_buy(result)
    held = [p for p in positions if p.get("strategy_group_id") == group["strategy_group_id"] and
            p.get("market") == plan["market"] and p["symbol"] == plan["symbol"] and number(p["current_quantity"]) > 0]
    target = next((p for p in held if p["position_id"] == plan.get("position_id")), None)
    if plan["account_alias"] not in group["included_account_ids"]:
        errors.append("ACCOUNT_NOT_IN_STRATEGY")
    if plan["plan_kind"] in {"INITIAL_ENTRY", "REENTRY"} and held and not plan.get("locked_at_first_fill"):
        errors.append("EXISTING_POSITION_REQUIRES_PYRAMID")
    if plan["plan_kind"] == "PYRAMID_ADD":
        if not target or target.get("current_market_price") is None:
            errors.append("POSITION_DATA_MISSING")
        else:
            if number(target["current_market_price"]) <= number(target["average_cost"]):
                errors.append("LOSS_POSITION_ADD_BLOCKED")
            if target.get("current_protection_price") is None:
                errors.append("STOP_MISSING")
            elif plan.get("adopted_stop_price") is not None and number(plan["adopted_stop_price"]) != number(target["current_protection_price"]):
                errors.append("PROTECTION_MUST_BE_ADOPTED_TOGETHER")
        if not plan.get("relative_superiority_basis"):
            errors.append("ADD_RATIONALE_MISSING")
    for other in plans:
        if other["buy_plan_id"] != plan["buy_plan_id"] and other.get("strategy_group_id") == group["strategy_group_id"] and (
            other["market"], other["symbol"]) == (plan["market"], plan["symbol"]) and other["status"] in RESERVING and any(
                number(t["remaining_quantity"]) > 0 for t in other["tranches"]):
            errors.append("PRIOR_BUY_PLAN_OPEN")
    held_ids = {p["position_id"] for p in held}
    if any(p.get("adopted_medium_trend_state") == "CONFIRMED_BROKEN" or p.get("breach_unresolved") for p in held):
        errors.append("ACTIVE_EXIT_PLAN")
    if any(s["position_id"] in held_ids and s["status"] not in {"DRAFT", "CANCELLED", "COMPLETED", "INACTIVE_RECOVERED"} for s in sell_plans):
        errors.append("ACTIVE_EXIT_PLAN")
    if not errors or not any(e in errors for e in ("STOP_MISSING", "INVALID_STOP", "RISK_ENTRY_PRICE_MISSING", "RISK_ENTRY_PRICE_MISMATCH", "QUANTITY_MISMATCH")):
        checked = review_budget(group, config, snapshot, positions, plans, result)
        errors.extend(checked["block_reasons"])
        result["risk_review"] = checked
        result["projected_position_weight_pct"] = checked.get("projected_position_weight_pct")
    result.update(status="MANUAL_REQUIRED" if errors else "READY", block_reasons=sorted(set(errors)),
                  approved_by_user=not errors, approved_at=at if not errors else None,
                  risk_budget_snapshot_id=snapshot.get("equity_snapshot_id"), cash_snapshot_id=snapshot.get("equity_snapshot_id"),
                  position_quantity_at_approval=target["current_quantity"] if target else "0",
                  position_average_cost_at_approval=target["average_cost"] if target else None,
                  position_market_price_at_approval=target.get("current_market_price") if target else None)
    if not errors:
        cash, risk = plan_reservation(result)
        result.update(total_reserved_cash=str(cash), total_nominal_planned_risk=str(risk),
                      total_remaining_quantity=str(sum((number(t["remaining_quantity"]) for t in result["tranches"]), Decimal(0))),
                      approved_reservation={"cash": str(cash), "risk": str(risk)})
        for t in result["tranches"]:
            if t["status"] not in {"FILLED", "CANCELLED"}:
                t["status"] = "ARMED"
            t['updated_at'] = at
    return result


def trigger_buy(plan: dict, tranche_id: str, price: str, checked: dict, *, manual_confirmed: bool = False,
                at: str | None = None) -> dict:
    result = deepcopy(plan)
    if plan["status"] not in {"READY", "ACTIVE", "PARTIALLY_FILLED"}:
        raise ValueError("Buy plan is not active or ready")
    t = next((t for t in result["tranches"] if t["tranche_id"] == tranche_id), None)
    if t is None or t["status"] in {"CANCELLED", "FILLED", "SUPERSEDED"}:
        raise ValueError("No pending buy tranche")
    def transition(state: str, reason: str):
        if t['status'] != state:
            t.setdefault('state_transitions', []).append({'from': t['status'], 'to': state,
                                                          'occurred_at': at, 'reason': reason})
            t['status'] = state
        t['updated_at'] = at
    if checked["block_reasons"]:
        transition('SUSPENDED', 'TRIGGER_REVALIDATION_BLOCKED')
        result["block_reasons"] = checked["block_reasons"]
        if "LOSS_POSITION_ADD_BLOCKED" not in result["block_reasons"]:
            result["status"] = "NEEDS_REAPPROVAL"
        return result
    observed_price = number(price, positive=True)
    kind = t["trigger_type"]
    if observed_price > number(t["risk_entry_price"]):
        transition('SUSPENDED', 'EXECUTION_OUTSIDE_PLAN')
        result["block_reasons"] = ["EXECUTION_OUTSIDE_PLAN"]
        return result
    met = ((kind == "LIMIT_AT_OR_BELOW" and observed_price <= number(t["trigger_price"])) or
           (kind == "BREAKOUT_AT_OR_ABOVE" and observed_price >= number(t["trigger_price"])) or
           (kind == "PRICE_RANGE" and number(t["price_range_lower"]) <= observed_price <= number(t["price_range_upper"])) or
           (kind == "MANUAL_CONFIRMATION" and manual_confirmed))
    if met and t['status'] != 'ACTION_REQUIRED':
        transition('CONDITION_MET', 'APPROVED_PRICE_CONDITION_MET')
        transition('ACTION_REQUIRED', 'USER_EXECUTION_REQUIRED_NO_ORDER')
    elif not met:
        transition('ARMED', 'WAITING_FOR_PRICE_CONDITION')
    t["condition_met"] = met
    result["status"] = "ACTIVE" if met else result["status"]
    result["block_reasons"] = []
    return result


def buy_fill(plan: dict, position: dict | None, tranche_id: str, fill: dict) -> tuple[dict, dict]:
    result = deepcopy(plan)
    t = next((t for t in result["tranches"] if t["tranche_id"] == tranche_id), None)
    if t is None:
        raise ValueError("Unknown buy tranche")
    qty, price = number(fill["actual_quantity"], positive=True), number(fill["actual_price"], positive=True)
    if not fill.get("execution_id") or not fill.get("actual_at"):
        raise ValueError("Actual execution ID/time required")
    adopted = plan.get("approved_by_user") is True or bool(
        plan.get("locked_at_first_fill") and position and position.get("initial_stop_price") is not None
    )
    adopted_stop = plan.get("adopted_stop_price") if adopted else None
    pos = deepcopy(position) if position else {
        "position_id": plan.get("position_id") or f"position:{plan['buy_plan_id']}", "account_alias": plan["account_alias"],
        "strategy_group_id": plan["strategy_group_id"], "market": plan["market"], "symbol": plan["symbol"],
        "currency": plan["currency"], "current_quantity": "0", "average_cost": "0",
        "initial_stop_price": adopted_stop, "current_protection_price": adopted_stop,
        "trigger_mode": plan.get("trigger_mode") if adopted else None, "protection_version": 1 if adopted else 0,
        "adopted_medium_trend_state": None, "proposed_medium_trend_state": "UNAVAILABLE"}
    old_q = number(pos["current_quantity"])
    pos.update(current_quantity=str(old_q + qty), average_cost=str((old_q * number(pos["average_cost"]) + qty * price) / (old_q + qty)))
    planned_remaining = number(t["remaining_quantity"])
    remaining = planned_remaining - qty
    ceiling = number(t["risk_entry_price"]) if t.get("risk_entry_price") is not None else None
    outside = ceiling is None or price > ceiling or remaining < 0 or t["status"] in {"CANCELLED", "SUPERSEDED"}
    t["actual_fills"].append(deepcopy(fill))
    t.update(remaining_quantity=str(max(Decimal(0), remaining)), status="PARTIALLY_FILLED" if remaining > 0 else "FILLED")
    t['updated_at'] = fill['actual_at']
    result.update(position_id=pos["position_id"], locked_at_first_fill=result.get("locked_at_first_fill") or fill["actual_at"],
                  status="PARTIALLY_FILLED" if any(number(t["remaining_quantity"]) > 0 for t in result["tranches"]) else "COMPLETED")
    if outside:
        result["block_reasons"] = sorted({*result.get("block_reasons", []), "EXECUTION_OUTSIDE_PLAN"})
    unit = ceiling - number(adopted_stop) if ceiling is not None and adopted_stop is not None else None
    planned_risk = unit * min(qty, planned_remaining) if unit is not None and unit > 0 else None
    actual_risk = (price - number(adopted_stop)) * qty if adopted_stop is not None and price > number(adopted_stop) else None
    result["execution_deviation"] = {"execution_id": fill['execution_id'], 'actual_at': fill['actual_at'],
                                     'tranche_id': tranche_id, 'currency': plan['currency'],
                                     'planned_price': str(ceiling) if ceiling is not None else None,
                                     'actual_price': str(price), 'planned_remaining_quantity': str(planned_remaining),
                                     'actual_quantity': str(qty), 'quantity_difference': str(qty - planned_remaining),
                                     'planned_comparable_quantity': str(min(qty, planned_remaining)),
                                     'planned_nominal_risk_for_fill': str(planned_risk) if planned_risk is not None else None,
                                     'nominal_risk_difference': str(actual_risk - planned_risk) if actual_risk is not None and planned_risk is not None else None,
                                     "price_difference": str(price - ceiling) if ceiling is not None else None,
                                     "quantity_over_remaining": str(max(-remaining, Decimal(0))),
                                     "actual_nominal_risk": str(actual_risk) if actual_risk is not None else None}
    result.setdefault('execution_deviations', []).append(deepcopy(result['execution_deviation']))
    if not adopted:
        result["status"] = "MANUAL_REQUIRED"
        result["block_reasons"] = sorted({*result.get("block_reasons", []), "EXECUTION_OUTSIDE_PLAN"})
        cash = risk = None
    else:
        cash, risk = plan_reservation(result)
    result.update(total_reserved_cash=str(cash) if cash is not None else None, total_nominal_planned_risk=str(risk) if risk is not None else None,
                  total_remaining_quantity=str(sum((number(t["remaining_quantity"]) for t in result["tranches"]), Decimal(0))))
    return result, pos
