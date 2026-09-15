"""Deterministic C02 DCF and C08 cash-conversion calculations."""

from __future__ import annotations

import math
from typing import Any

SCENARIO_NAMES = ("bearish", "base", "bullish")


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite number.")  # noqa: TRY004 -- Preserve the existing ValueError validation contract for callers.
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field} must be a finite number.")
    return result


def _optional_number(value: Any, field: str) -> float | None:
    if value is None:
        return None
    return _number(value, field)


def _required_mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be a JSON object.")  # noqa: TRY004 -- Preserve the existing ValueError validation contract for callers.
    return value


def _compare_with_price(value_per_share: float, current_price: float) -> str:
    if math.isclose(value_per_share, current_price, rel_tol=1e-12, abs_tol=1e-12):
        return "near_current_price"
    return "above_current_price" if value_per_share > current_price else "below_current_price"


def _calculate_c02_scenario(
    raw: dict[str, Any],
    *,
    scenario_name: str,
    cash: float,
    debt: float,
    shares_outstanding: float,
    current_price: float,
) -> dict[str, Any]:
    raw_fcff = raw.get("fcff")
    if not isinstance(raw_fcff, list) or len(raw_fcff) != 5:
        raise ValueError(f"scenarios.{scenario_name}.fcff must contain exactly five annual values.")
    fcff = [
        _number(value, f"scenarios.{scenario_name}.fcff[{index}]")
        for index, value in enumerate(raw_fcff)
    ]
    discount_rate = _number(
        raw.get("discount_rate"), f"scenarios.{scenario_name}.discount_rate"
    )
    terminal_growth_rate = _number(
        raw.get("terminal_growth_rate"),
        f"scenarios.{scenario_name}.terminal_growth_rate",
    )
    if discount_rate <= 0:
        raise ValueError(f"scenarios.{scenario_name}.discount_rate must be greater than zero.")
    if terminal_growth_rate <= -1:
        raise ValueError(
            f"scenarios.{scenario_name}.terminal_growth_rate must be greater than -1."
        )
    if discount_rate <= terminal_growth_rate:
        raise ValueError(
            f"scenarios.{scenario_name}.discount_rate must exceed terminal_growth_rate."
        )

    discounted_fcff = [
        value / ((1 + discount_rate) ** year)
        for year, value in enumerate(fcff, start=1)
    ]
    terminal_value = fcff[-1] * (1 + terminal_growth_rate) / (
        discount_rate - terminal_growth_rate
    )
    discounted_terminal_value = terminal_value / ((1 + discount_rate) ** 5)
    enterprise_value = sum(discounted_fcff) + discounted_terminal_value
    equity_value = enterprise_value + cash - debt
    value_per_share = equity_value / shares_outstanding

    return {
        "fcff": fcff,
        "discount_rate": discount_rate,
        "terminal_growth_rate": terminal_growth_rate,
        "discounted_fcff": discounted_fcff,
        "five_year_fcff_present_value": sum(discounted_fcff),
        "terminal_value": terminal_value,
        "discounted_terminal_value": discounted_terminal_value,
        "enterprise_value": enterprise_value,
        "cash": cash,
        "debt": debt,
        "equity_value": equity_value,
        "shares_outstanding": shares_outstanding,
        "value_per_share": value_per_share,
        "current_price": current_price,
        "upside_downside_pct": (value_per_share / current_price - 1) * 100,
        "price_comparison": _compare_with_price(value_per_share, current_price),
    }


def _normalize_peers(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("peers must be a JSON array.")  # noqa: TRY004 -- Preserve the existing ValueError validation contract for callers.

    peers: list[dict[str, Any]] = []
    for index, raw in enumerate(value):
        peer = _required_mapping(raw, f"peers[{index}]")
        ticker = peer.get("ticker")
        if not isinstance(ticker, str) or not ticker.strip():
            raise ValueError(f"peers[{index}].ticker must be a non-empty string.")
        peers.append(
            {
                "ticker": ticker.strip(),
                "name": peer.get("name"),
                "ev_to_ebit": _optional_number(
                    peer.get("ev_to_ebit"), f"peers[{index}].ev_to_ebit"
                ),
                "per": _optional_number(peer.get("per"), f"peers[{index}].per"),
                "pbr": _optional_number(peer.get("pbr"), f"peers[{index}].pbr"),
                "as_of": peer.get("as_of"),
            }
        )
    return peers


def _unavailable(reason: str) -> dict[str, Any]:
    return {
        "dcf_cash_conversion_rate": None,
        "assessment": "unavailable",
        "confidence": "unavailable",
        "evidence": [reason],
        "dcf_value_adjusted": False,
    }


def _historical_conversion(
    value: Any,
) -> tuple[list[dict[str, Any]], str | None, list[int | str]]:
    if not isinstance(value, list):
        return [], "historical_financials is missing or is not an array.", []
    if len(value) < 3:
        return [], "Fewer than three historical years were supplied.", []

    selected = sorted(
        value,
        key=lambda row: row.get("year", 0) if isinstance(row, dict) else 0,
    )[-5:]
    conversions: list[dict[str, Any]] = []
    excluded_years: list[int | str] = []
    for index, raw in enumerate(selected):
        if not isinstance(raw, dict):
            excluded_years.append(f"index:{index}")
            continue
        year = raw.get("year")
        cfo = _optional_number(raw.get("cfo"), f"historical_financials[{index}].cfo")
        capex = _optional_number(raw.get("capex"), f"historical_financials[{index}].capex")
        ebitda = _optional_number(raw.get("ebitda"), f"historical_financials[{index}].ebitda")
        if year is None or cfo is None or capex is None or ebitda is None:
            excluded_years.append(year if isinstance(year, int) else f"index:{index}")
            continue
        if ebitda <= 0:
            return [], f"Historical year {year} has zero or negative EBITDA.", excluded_years
        if capex < 0:
            return (
                [],
                f"Historical year {year} CAPEX must be a positive cash outflow.",
                excluded_years,
            )
        conversions.append(
            {
                "year": year,
                "cash_conversion_rate": (cfo - capex) / ebitda,
            }
        )

    if len(conversions) < 3:
        return [], "Fewer than three valid historical years remain.", excluded_years
    return conversions, None, excluded_years


def _calculate_c08(
    historical_financials: Any,
    expected_by_scenario: Any,
) -> dict[str, Any]:
    historical, historical_error, excluded_years = _historical_conversion(
        historical_financials
    )
    if historical_error is not None:
        return {
            "formula": {
                "historical": "(CFO - CAPEX) / EBITDA",
                "dcf": "expected FCFF / expected EBITDA",
            },
            "historical": [],
            "historical_range": None,
            "excluded_years": excluded_years,
            "scenarios": {
                name: _unavailable(historical_error) for name in SCENARIO_NAMES
            },
        }

    rates = [row["cash_conversion_rate"] for row in historical]
    lower = min(rates)
    upper = max(rates)
    confidence = {3: "low", 4: "medium", 5: "high"}[len(historical)]
    expected = expected_by_scenario if isinstance(expected_by_scenario, dict) else {}
    scenario_results: dict[str, Any] = {}

    for name in SCENARIO_NAMES:
        raw = expected.get(name)
        if not isinstance(raw, dict):
            scenario_results[name] = _unavailable(
                f"c08_expected.{name} is missing or is not an object."
            )
            continue
        expected_fcff = _optional_number(
            raw.get("fcff"), f"c08_expected.{name}.fcff"
        )
        expected_ebitda = _optional_number(
            raw.get("ebitda"), f"c08_expected.{name}.ebitda"
        )
        if expected_fcff is None or expected_ebitda is None:
            scenario_results[name] = _unavailable(
                f"c08_expected.{name} has a missing FCFF or EBITDA value."
            )
            continue
        if expected_ebitda <= 0:
            scenario_results[name] = _unavailable(
                f"c08_expected.{name}.ebitda is zero or negative."
            )
            continue

        rate = expected_fcff / expected_ebitda
        if rate > upper:
            assessment = "possibly_overstated"
            relation = "above"
        elif rate < lower:
            assessment = "possibly_understated"
            relation = "below"
        else:
            assessment = "roughly_neutral"
            relation = "within"
        scenario_results[name] = {
            "dcf_cash_conversion_rate": rate,
            "assessment": assessment,
            "confidence": confidence,
            "evidence": [
                (f"DCF cash conversion {rate:.4f} is {relation} the historical range "
                 f"{lower:.4f} to {upper:.4f} from {len(historical)} years.")
            ]
            + (
                [f"Excluded historical years with missing values: {excluded_years}."]
                if excluded_years
                else []
            ),
            "dcf_value_adjusted": False,
        }

    return {
        "formula": {
            "historical": "(CFO - CAPEX) / EBITDA",
            "dcf": "expected FCFF / expected EBITDA",
        },
        "historical": historical,
        "historical_range": {
            "minimum": lower,
            "maximum": upper,
            "valid_years": len(historical),
        },
        "excluded_years": excluded_years,
        "scenarios": scenario_results,
    }


def calculate_analysis(payload: dict[str, Any]) -> dict[str, Any]:
    """Calculate C02 and C08 from explicit user inputs without forecasting."""

    if not isinstance(payload, dict):
        raise ValueError("Input must be a JSON object.")  # noqa: TRY004 -- Preserve the existing ValueError validation contract for callers.
    ticker = payload.get("ticker")
    if not isinstance(ticker, str) or not ticker.strip():
        raise ValueError("ticker must be a non-empty string.")

    current_price = _number(payload.get("current_price"), "current_price")
    shares_outstanding = _number(payload.get("shares_outstanding"), "shares_outstanding")
    cash = _number(payload.get("cash"), "cash")
    debt = _number(payload.get("debt"), "debt")
    if current_price <= 0:
        raise ValueError("current_price must be greater than zero.")
    if shares_outstanding <= 0:
        raise ValueError("shares_outstanding must be greater than zero.")
    if cash < 0 or debt < 0:
        raise ValueError("cash and debt cannot be negative.")

    raw_scenarios = _required_mapping(payload.get("scenarios"), "scenarios")
    scenario_results: dict[str, Any] = {}
    for name in SCENARIO_NAMES:
        raw = _required_mapping(raw_scenarios.get(name), f"scenarios.{name}")
        scenario_results[name] = _calculate_c02_scenario(
            raw,
            scenario_name=name,
            cash=cash,
            debt=debt,
            shares_outstanding=shares_outstanding,
            current_price=current_price,
        )

    return {
        "schema_version": "1.0",
        "ticker": ticker.strip(),
        "as_of": payload.get("as_of"),
        "currency": payload.get("currency"),
        "c02": {
            "method": "five_year_fcff_dcf",
            "scenarios": scenario_results,
            "peers": _normalize_peers(payload.get("peers")),
        },
        "c08": _calculate_c08(
            payload.get("historical_financials"), payload.get("c08_expected")
        ),
    }
