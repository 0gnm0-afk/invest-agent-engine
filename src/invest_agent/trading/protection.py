"""R1 deterministic protection monitoring; candidate values never become stops.

Prices use the existing executable-raw convention. The caller supplies market
calendar session dates, not calendar-day age thresholds. No broker writes.
"""
from __future__ import annotations

import itertools
from copy import deepcopy
from decimal import Decimal
from statistics import median

from .portfolio import number

TRIGGER_MODES = {
    "INTRADAY_TOUCH", "DAILY_CLOSE_BREACH", "WEEKLY_CLOSE_BREACH",
    "MANUAL_CONFIRMATION",
}
CANDIDATE_TYPES = {
    "SWING_LOW", "HORIZONTAL_SUPPORT", "BREAKOUT_RETEST", "MOVING_AVERAGE",
    "TRENDLINE", "PRICE_STRUCTURE", "USER_DEFINED", "OTHER",
}
APPROACH_MESSAGE = (
    "채택 보호선까지의 거리가 최근 1일 정상 변동 범위 이내로 좁혀졌습니다. "
    "다음 거래일에 이탈 여부를 확인할 필요가 있습니다."
)


def validate_candidate(candidate: dict) -> dict:
    required = {
        "candidate_id", "position_id", "candidate_type", "timeframe",
        "candidate_price", "basis_description", "anchor_points_or_source_levels",
        "invalidation_rationale", "counterevidence", "suggested_trigger_mode",
        "data_as_of", "missing_data",
    }
    if required - candidate.keys():
        raise ValueError("Incomplete protection candidate")
    if candidate["candidate_type"] not in CANDIDATE_TYPES:
        raise ValueError("Unsupported candidate type")
    if candidate["suggested_trigger_mode"] not in TRIGGER_MODES:
        raise ValueError("Unsupported trigger mode")
    number(candidate["candidate_price"], positive=True)
    return {**deepcopy(candidate), "authority": "candidate"}


def adopt_protection(position: dict, request: dict, *, actor: str) -> dict:
    """Pure transition; the ledger must persist the old/new values atomically."""
    if actor != "user" or request.get("explicit_user_confirmation") is not True:
        raise ValueError("Protection adoption requires explicit user confirmation")
    for key in ("adoption_reason", "adopted_at", "data_as_of"):
        if not request.get(key):
            raise ValueError(f"Missing {key}")
    if not request.get("adopted_candidate_id") and request.get("manual_input") is not True:
        raise ValueError("Candidate reference or manual_input is required")
    mode = request.get("trigger_mode")
    if mode not in TRIGGER_MODES:
        raise ValueError("Explicit trigger mode is required")
    price = number(request["price"], positive=True)
    previous = position.get("current_protection_price")
    if previous is not None and price < number(previous, positive=True) and not (
        request.get("risk_expansion_override") is True and request.get("override_reason")):
        raise ValueError("Protection reduction requires risk expansion override and reason")
    result = deepcopy(position)
    if result.get("initial_stop_price") is None:
        result["initial_stop_price"] = str(price)
    adoption_fields = {
        "adoption_reason", "adopted_at", "data_as_of", "adopted_candidate_id",
        "manual_input", "trigger_mode", "risk_expansion_override", "override_reason",
    }
    result.update({key: deepcopy(value) for key, value in request.items() if key in adoption_fields})
    result.update(current_protection_price=str(price), version=position.get("version", 0) + 1)
    result["protection_version"] = position.get("protection_version", 0) + 1
    return result


def normal_range(bars: list[dict], *, atr_available: bool = True) -> tuple[Decimal | None, str]:
    """Wilder ATR(14); median of 20 complete TRs if ATR cannot be supplied.

    At least one preceding close is required; no fabricated first-day TR.
    If the upstream ATR calculation is unavailable, use the complete TR median.
    """
    ranges: list[Decimal] = []
    for previous, bar in itertools.pairwise(bars):
        if not (previous.get("complete") is True and bar.get("complete") is True):
            ranges = []
            continue
        high, low = number(bar["high"], positive=True), number(bar["low"], positive=True)
        close = number(previous["close"], positive=True)
        if high < low:
            raise ValueError("OHLC high below low")
        ranges.append(max(high - low, abs(high - close), abs(low - close)))
    if len(ranges) >= 14 and atr_available:
        atr = sum(ranges[:14], Decimal(0)) / 14
        for value in ranges[14:]:
            atr = (atr * 13 + value) / 14
        return atr, "ATR14_WILDER"
    if len(ranges) >= 20:
        return median(ranges[-20:]), "MEDIAN_TR20"
    return None, "UNAVAILABLE"


def monitor(position: dict, observation: dict, *, previous: dict | None = None) -> dict:
    """Observe one market session without manufacturing execution or a stop.

    observation carries expected_session, daily(date/complete/OHLC), optional
    weekly(date/complete/close/last_session), session_low and realtime_available.
    Unresolved confirmations stay latched until an explicit resolution event.
    """
    previous = previous or {}
    daily = observation.get("daily") or {}
    result: dict = {
        "state": "UNSET", "risk_status": "UNAVAILABLE", "manual_required": True,
        "data_as_of": daily.get("date"), "market": position.get("market"),
        "expected_session": observation.get("expected_session"),
        "realtime_available": observation.get("realtime_available") is True,
        "distance_to_protection": None, "distance_pct": None,
        "current_downside_exposure": None, "stop_execution_pnl": None,
        "approach_status": "UNAVAILABLE", "missing_data": [],
        "notification_required": False,
    }
    price = daily.get("close") if daily.get("complete") is True else None
    stop = position.get("current_protection_price")
    if stop is None:
        return result
    stop = number(stop, positive=True)
    quantity = number(position["current_quantity"])
    if price is not None:
        price = number(price, positive=True)
        distance = price - stop
        result.update(distance_to_protection=str(distance), distance_pct=str(distance / price),
                      current_downside_exposure=str(max(distance, Decimal(0)) * quantity),
                      stop_execution_pnl=str((stop - number(position["average_cost"])) * quantity))
    expected = observation.get("expected_session")
    fresh = bool(expected and daily.get("date") == expected and daily.get("complete") is True)
    mode = position.get("trigger_mode")
    if quantity == 0 or position.get("breach_resolved") is True:
        result.update(state="RESOLVED", manual_required=False)
    elif not fresh or price is None:
        result.update(state="DATA_UNAVAILABLE")
        result["missing_data"].append("STALE_DATA" if daily else "DAILY_DATA_MISSING")
    elif mode not in TRIGGER_MODES:
        result.update(state="DATA_UNAVAILABLE")
        result["missing_data"].append("TRIGGER_MODE_MISSING")
    else:
        result.update(state="SAFE", risk_status="AVAILABLE", manual_required=False)
        low = observation.get("session_low", daily.get("low"))
        touched = low is not None and number(low, positive=True) <= stop
        closed_below = price <= stop
        weekly = observation.get("weekly") or {}
        week_confirmed = (weekly.get("complete") is True and
                          weekly.get("last_session") == expected and
                          weekly.get("date") == expected and
                          number(weekly["close"], positive=True) <= stop)
        confirmed = ((mode == "INTRADAY_TOUCH" and touched) or
                     (mode == "DAILY_CLOSE_BREACH" and closed_below) or
                     (mode == "WEEKLY_CLOSE_BREACH" and week_confirmed))
        if position.get("manual_breach_confirmed") is True:
            confirmed = True
        if confirmed:
            result["state"] = "CONFIRMED_BREACH"
        elif touched or closed_below:
            result["state"] = "REVIEW_REQUIRED" if mode == "MANUAL_CONFIRMATION" else "PROVISIONAL_BREACH"
        ref = position.get('approach_range')
        if ref is None:
            ref = observation.get("approach_range")
        if ref is not None:
            ref, source = number(ref, positive=True), "USER_SETTING"
        else:
            ref, source = normal_range(observation.get("bars", []),
                                       atr_available=observation.get('atr_available') is not False)
        result.update(reference_range=str(ref) if ref is not None else None, reference_range_source=source)
        if ref is None:
            result["manual_required"] = True
        else:
            approaching = 0 < price - stop <= ref
            result["approach_status"] = "APPROACHING" if approaching else "SAFE"
            if result["state"] == "SAFE" and approaching:
                result.update(state="APPROACHING", message=APPROACH_MESSAGE)
    if (previous.get("state") == "CONFIRMED_BREACH" and result["state"] != "RESOLVED"):
        result["state"] = "CONFIRMED_BREACH"
    if result["state"] in {"CONFIRMED_BREACH", "PROVISIONAL_BREACH", "REVIEW_REQUIRED"}:
        result["manual_required"] = True
    levels = {"UNSET": 0, "RESOLVED": 0, "SAFE": 0, "APPROACHING": 1,
              "REVIEW_REQUIRED": 2, "PROVISIONAL_BREACH": 2, "CONFIRMED_BREACH": 3,
              "DATA_UNAVAILABLE": 0}
    result["protection_version"] = position.get("protection_version", position.get("version"))
    result["notification_required"] = bool(
        levels[result["state"]] > levels.get(previous.get("state") or "UNSET", 0)
        or previous.get("protection_version") != result["protection_version"]
        or (result["missing_data"] and result["missing_data"] != previous.get("missing_data"))
        or (result["state"] == "CONFIRMED_BREACH" and expected != previous.get("expected_session"))
    )
    return result
