"""R2 pure sell-plan transitions, called by the persistent risk ledger.

Trigger evaluation marks review actions only; inventory changes require fills.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal

from .portfolio import number
from .protection import normal_range

PROPOSED_TRENDS = {"INTACT", "WEAKENING", "BREAK_CANDIDATE", "RECOVERY_CANDIDATE", "UNAVAILABLE"}
PLAN_KINDS = {"TREND_BREAK_EXIT", "PROACTIVE_PROFIT_TAKE"}
DIRECTIONS = {"AT_OR_BELOW", "AT_OR_ABOVE", "MANUAL"}
BASES = {"INTRADAY", "DAILY_CLOSE", "WEEKLY_CLOSE", "MANUAL_CONFIRMATION"}


def transition_tranche(tranche: dict, state: str, at: str | None, reason: str) -> None:
    """Retain intermediate states in the ledger's atomic old/new projection audit."""
    if tranche.get("status") != state:
        tranche.setdefault("state_transitions", []).append(
            {"from": tranche.get("status"), "to": state, "occurred_at": at, "reason": reason})
        tranche["status"] = state
    tranche["updated_at"] = at


def proposed_trend(value: dict) -> dict:
    required = {"proposed_state", "evidence_for", "evidence_against", "important_levels",
                "data_as_of", "missing_data", "review_reason"}
    if required - value.keys() or value["proposed_state"] not in PROPOSED_TRENDS:
        raise ValueError("Invalid proposed medium trend; user confirmation is separate")
    if {"probability", "confidence", "confidence_score"} & value.keys():
        raise ValueError("Numeric trend confidence is not part of the contract")
    return deepcopy(value)


def approve_sell(plan: dict, position: dict, *, actor: str) -> dict:
    if actor != "user" or plan.get("approved_by_user") is not True or not plan.get("approved_at"):
        raise ValueError("Sell plan requires explicit user approval")
    if plan.get("plan_kind") not in PLAN_KINDS:
        raise ValueError("Unknown sell plan kind")
    quantity = number(position["current_quantity"], positive=True)
    step = number(plan["quantity_step"], positive=True)
    initial = number(plan.get("initial_reduction_quantity", "0"))
    tranches = deepcopy(plan.get("tranches", []))
    if not tranches:
        raise ValueError("At least one remaining sell tranche is required")
    total = initial
    ids, sequences = set(), set()
    for t in tranches:
        if not t.get("tranche_id") or t["tranche_id"] in ids or t["sequence"] in sequences:
            raise ValueError("Duplicate or missing tranche identity/sequence")
        ids.add(t["tranche_id"]); sequences.add(t["sequence"])
        amount = number(t["planned_quantity"], positive=True)
        if amount % step:
            raise ValueError("Quantity does not match order lot")
        if t.get("trigger_direction") not in DIRECTIONS or t.get("confirmation_basis") not in BASES:
            raise ValueError("Every tranche requires direction and confirmation basis")
        if t["trigger_direction"] != "MANUAL":
            number(t["trigger_price"], positive=True)
        if not t.get("trigger_basis"):
            raise ValueError("Trigger rationale is required")
        total += amount
        t.update(remaining_planned_quantity=str(amount), status="PLANNED", actual_fills=[],
                 candidate_id=t.get("candidate_id"), created_at=t.get("created_at"), updated_at=plan["approved_at"])
    if initial % step:
        raise ValueError("Initial reduction does not match order lot")
    if plan["plan_kind"] == "TREND_BREAK_EXIT":
        if not 0 < initial < quantity or total != quantity:
            raise ValueError("Initial reduction plus remaining tranches must equal current inventory")
        final = max(tranches, key=lambda t: t["sequence"])
        if (final["tranche_id"] != plan.get("final_protection_tranche_id") or
                final["trigger_direction"] != "AT_OR_BELOW" or final.get("all_remaining") is not True):
            raise ValueError("Last tranche must protect all remaining inventory")
    elif initial or total >= quantity:
        raise ValueError("Proactive plan must be a partial sale with no initial trend-break reduction")
    return {**deepcopy(plan), "tranches": tranches, "status": "READY",
            "position_quantity_at_approval": str(quantity),
            "initial_reduction": {"planned_quantity": str(initial), "remaining_planned_quantity": str(initial),
                                  "status": "PLANNED", "actual_fills": [],
                                  "created_at": plan["approved_at"], "updated_at": plan["approved_at"]}}


def adopt_trend(position: dict, plans: list[dict], state: str, *, actor: str,
                at: str | None = None) -> tuple[dict, list[dict]]:
    if actor != "user" or state not in {"INTACT", "WEAKENING", "CONFIRMED_BROKEN", "RECOVERED"}:
        raise ValueError("Only user may adopt a medium trend state")
    pos, updated = deepcopy(position), deepcopy(plans)
    previous = pos.get("adopted_medium_trend_state")
    pos["adopted_medium_trend_state"] = state
    if state == "CONFIRMED_BROKEN":
        matching = False
        for plan in updated:
            if plan["plan_kind"] == "PROACTIVE_PROFIT_TAKE" and plan["status"] not in {"COMPLETED", "CANCELLED"}:
                plan["status"] = "PAUSED"
            elif plan["plan_kind"] == "TREND_BREAK_EXIT" and plan["status"] == "READY":
                if number(plan["position_quantity_at_approval"]) == number(pos["current_quantity"]):
                    plan["status"] = "ACTIVE"
                    transition_tranche(plan["initial_reduction"], "ACTION_REQUIRED", at, "USER_CONFIRMED_TREND_BREAK")
                    for tranche in plan["tranches"]:
                        transition_tranche(tranche, "ARMED", at, "TREND_EXIT_ACTIVATED")
                    matching = True
                else:
                    plan["status"] = "MANUAL_REQUIRED"
            elif plan["plan_kind"] == "TREND_BREAK_EXIT" and plan["status"] == "ACTIVE":
                matching = True
        pos["sell_plan_status"] = "ACTIVE" if matching else "MANUAL_REQUIRED"
    elif state == "RECOVERED" and previous != "CONFIRMED_BROKEN":
        for plan in updated:
            if plan["plan_kind"] == "TREND_BREAK_EXIT" and plan["status"] in {"DRAFT", "READY"}:
                plan["status"] = "INACTIVE_RECOVERED"
    return pos, updated


def evaluate_sell(plan: dict, position: dict, observation: dict) -> dict:
    result = deepcopy(plan)
    if result["status"] != "ACTIVE":
        return result
    daily = observation.get("daily") or {}
    if (daily.get("complete") is not True or not observation.get("expected_session") or
            daily.get("date") != observation["expected_session"]):
        result["data_status"] = "STALE_DATA"
        return result
    result["data_status"] = "AVAILABLE"
    at = observation.get("observed_at", daily["date"])
    breached = []
    reduction = result["initial_reduction"]
    action_quantity = (number(reduction["remaining_planned_quantity"])
                       if reduction["status"] in {"ACTION_REQUIRED", "PARTIALLY_EXECUTED"} else Decimal(0))
    for tranche in result["tranches"]:
        if tranche["status"] in {"EXECUTED", "CANCELLED", "SUPERSEDED"}:
            continue
        direction, basis = tranche["trigger_direction"], tranche["confirmation_basis"]
        if direction == "MANUAL" or basis == "MANUAL_CONFIRMATION":
            if tranche["status"] in {"ACTION_REQUIRED", "PARTIALLY_EXECUTED"}:
                breached.append(tranche["tranche_id"])
                action_quantity += number(tranche["remaining_planned_quantity"])
            continue
        price = number(tranche["trigger_price"], positive=True)
        def crossed(value, price=price, direction=direction):
            if value is None:
                return False
            n = number(value, positive=True)
            return n <= price if direction == "AT_OR_BELOW" else n >= price
        touched = crossed(daily.get("low" if direction == "AT_OR_BELOW" else "high"))
        week = observation.get("weekly") or {}
        confirmed = ((basis == "INTRADAY" and touched) or
                     (basis == "DAILY_CLOSE" and crossed(daily.get("close"))) or
                     (basis == "WEEKLY_CLOSE" and week.get("complete") is True and
                      week.get("last_session") == observation["expected_session"] and
                      week.get("date") == observation["expected_session"] and
                      crossed(week.get("close"))))
        if confirmed or tranche["status"] in {"ACTION_REQUIRED", "PARTIALLY_EXECUTED", "CONFIRMED_BREACH"}:
            if tranche["status"] != "PARTIALLY_EXECUTED":
                if tranche["status"] not in {"ACTION_REQUIRED", "CONFIRMED_BREACH"}:
                    transition_tranche(tranche, "CONFIRMED_BREACH", at, "ADOPTED_TRIGGER_CONFIRMED")
                transition_tranche(tranche, "ACTION_REQUIRED", at, "CONFIRMED_BREACH_REQUIRES_USER_ACTION")
            breached.append(tranche["tranche_id"])
            action_quantity += number(tranche["remaining_planned_quantity"])
        elif touched:
            transition_tranche(tranche, "PROVISIONAL_BREACH", at, "TOUCH_AWAITS_CONFIRMATION")
        else:
            ref = position.get('approach_range')
            if ref is None:
                ref = observation.get("approach_range")
            ref = number(ref, positive=True) if ref is not None else normal_range(
                observation.get("bars", []), atr_available=observation.get('atr_available') is not False)[0]
            distance = (number(daily["close"]) - price) if direction == "AT_OR_BELOW" else (price - number(daily["close"]))
            state = "APPROACHING" if ref is not None and 0 < distance <= ref else "ARMED"
            transition_tranche(tranche, state, at, "PRICE_OBSERVATION")
        tranche["updated_at"] = observation.get("observed_at", daily["date"])
    current = number(position["current_quantity"])
    final_breached = result.get("final_protection_tranche_id") in breached
    result.update(breached_tranche_ids=breached, multiple_breach=len(breached) > 1,
                  action_required_quantity=str(current if final_breached else min(current, action_quantity)),
                  current_remaining_quantity=str(current),
                  pending_planned_quantity=str(sum((number(t["remaining_planned_quantity"]) for t in result["tranches"]
                                                   if t["status"] not in {"CANCELLED", "SUPERSEDED"}), Decimal(0))))
    return result


def sell_fill(plan: dict, position: dict, tranche_id: str, fill: dict, *, actor: str) -> tuple[dict, dict]:
    if actor not in {"user", "broker"}:
        raise ValueError("Fill must be an actual user/broker record")
    for key in ("execution_id", "actual_at"):
        if not fill.get(key):
            raise ValueError("Actual execution identity and timestamp required")
    qty = number(fill["actual_quantity"], positive=True)
    number(fill["actual_price"], positive=True)
    result, pos = deepcopy(plan), deepcopy(position)
    all_tranches = [result["initial_reduction"], *result["tranches"]]
    if any(f["execution_id"] == fill["execution_id"] for t in all_tranches for f in t["actual_fills"]):
        raise ValueError("Duplicate execution must be resolved by ledger idempotency")
    tranche = result["initial_reduction"] if tranche_id == "initial_reduction" else next(
        (t for t in result["tranches"] if t["tranche_id"] == tranche_id), None)
    if tranche is None:
        raise ValueError("Unknown sell tranche")
    tranche["actual_fills"].append(deepcopy(fill))
    remaining = number(tranche["remaining_planned_quantity"]) - qty
    tranche.update(remaining_planned_quantity=str(max(remaining, Decimal(0))),
                   status="PARTIALLY_EXECUTED" if remaining > 0 else "EXECUTED")
    inventory = number(pos["current_quantity"]) - qty
    # Preserve the actual out-of-plan fill, including reconciliation deficits.
    pos["current_quantity"] = str(max(inventory, Decimal(0)))
    if inventory < 0:
        pos["inventory_reconciliation_deficit"] = str(-inventory)
        result["status"] = "NEEDS_REAPPROVAL"
    elif inventory == 0:
        result["status"] = "COMPLETED"
    elif remaining < 0:
        result["status"] = "NEEDS_REAPPROVAL"
    elif result["plan_kind"] == "PROACTIVE_PROFIT_TAKE" and all(
        t["status"] in {"EXECUTED", "CANCELLED", "SUPERSEDED"} for t in result["tranches"]
    ):
        result["status"] = "COMPLETED"
    tranche["updated_at"] = fill["actual_at"]
    return result, pos
