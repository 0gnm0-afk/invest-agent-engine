"""Connect normalized DataBundles to the C02, C08, and C11 calculations."""

from __future__ import annotations

from typing import Any

from .calculators import calculate_analysis
from .data_bundle import AnnualFinancial, DataBundle

LIQUIDITY_WINDOWS = (20, 60, 250)


def requested_peer_tickers(assumptions: dict[str, Any]) -> tuple[str, ...]:
    raw = assumptions.get("peers", [])
    if not isinstance(raw, list):
        raise ValueError("peers must be an array of ticker strings.")  # noqa: TRY004 -- Preserve the existing ValueError validation contract for callers.
    tickers: list[str] = []
    for index, value in enumerate(raw):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"peers[{index}] must be a non-empty ticker string.")
        ticker = value.strip()
        if ticker not in tickers:
            tickers.append(ticker)
    return tuple(tickers)


def _latest_financial(bundle: DataBundle) -> AnnualFinancial:
    if not bundle.annual_financials:
        raise ValueError(f"{bundle.ticker} has no annual financial data.")
    return max(bundle.annual_financials, key=lambda row: row.year)


def _required_bundle_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"DataProvider did not supply {field}.")  # noqa: TRY004 -- Preserve the existing ValueError validation contract for callers.
    return float(value)


def _multiple(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return numerator / denominator


def _peer_metrics(bundle: DataBundle) -> dict[str, Any]:
    latest = _latest_financial(bundle)
    market_cap = bundle.quote.get("market_cap")
    market_cap_value = (
        float(market_cap) if isinstance(market_cap, (int, float)) and not isinstance(market_cap, bool) else None
    )
    enterprise_value = (
        market_cap_value + latest.debt - latest.cash
        if market_cap_value is not None and latest.debt is not None and latest.cash is not None
        else None
    )
    return {
        "ticker": bundle.ticker,
        "name": bundle.name,
        "ev_to_ebit": _multiple(enterprise_value, latest.ebit),
        "per": _multiple(market_cap_value, latest.net_income),
        "pbr": _multiple(market_cap_value, latest.equity),
        "as_of": bundle.quote.get("as_of"),
    }


def calculate_c11(bundle: DataBundle) -> dict[str, Any]:
    """Return trading-value references without inferring position liquidity."""

    prices = sorted(bundle.daily_prices, key=lambda row: row.date)
    averages: dict[str, float | None] = {}
    missing_fields = [
        "bid_ask_spread",
        "order_book_depth",
        "planned_position_amount",
        "normal_liquidation_period",
        "stress_liquidation_period",
        "trading_cost",
        "market_impact",
    ]
    for window in LIQUIDITY_WINDOWS:
        key = f"{window}d"
        if len(prices) < window:
            averages[key] = None
            missing_fields.append(f"average_trading_value.{key}")
            continue
        values = [row.trading_value for row in prices[-window:]]
        averages[key] = sum(values) / window

    return {
        "scope": "reference_only",
        "status": "reference_only" if any(value is not None for value in averages.values()) else "unavailable",
        "formula": "mean(daily close * daily volume)",
        "as_of": prices[-1].date if prices else None,
        "currency": bundle.currency,
        "available_trading_days": len(prices),
        "average_trading_value": averages,
        "liquidity_assessment": "unavailable",
        "missing_fields": missing_fields,
        "evidence": [
            "20/60/250-trading-day averages are reference values only.",
            "Liquidation time, cost, and market impact require a planned position and order-book history.",
        ],
    }


def calculate_from_data_bundles(
    target: DataBundle,
    assumptions: dict[str, Any],
    peer_bundles: list[DataBundle] | tuple[DataBundle, ...] = (),
) -> dict[str, Any]:
    """Build calculator inputs from provider data; forecasts stay user-supplied."""

    if not isinstance(assumptions, dict):
        raise ValueError("Assumptions must be a JSON object.")  # noqa: TRY004 -- Preserve the existing ValueError validation contract for callers.
    requested_peers = requested_peer_tickers(assumptions)
    supplied_peers = {bundle.ticker: bundle for bundle in peer_bundles}
    missing_peers = [ticker for ticker in requested_peers if ticker not in supplied_peers]
    if missing_peers:
        raise ValueError(f"No DataBundle was supplied for peer(s): {', '.join(missing_peers)}")

    latest = _latest_financial(target)
    payload = {
        "ticker": target.ticker,
        "as_of": target.quote.get("as_of"),
        "currency": target.currency,
        "current_price": _required_bundle_number(target.quote.get("price"), "quote.price"),
        "shares_outstanding": _required_bundle_number(
            target.quote.get("shares_outstanding"), "quote.shares_outstanding"
        ),
        "cash": _required_bundle_number(latest.cash, f"annual_financials.{latest.year}.cash"),
        "debt": _required_bundle_number(latest.debt, f"annual_financials.{latest.year}.debt"),
        "scenarios": assumptions.get("scenarios"),
        "c08_expected": assumptions.get("c08_expected"),
        "historical_financials": [
            {
                "year": row.year,
                "cfo": row.cfo,
                "capex": row.capex,
                "ebitda": row.ebitda,
            }
            for row in sorted(target.annual_financials, key=lambda item: item.year)[-5:]
        ],
        "peers": [_peer_metrics(supplied_peers[ticker]) for ticker in requested_peers],
    }
    result = calculate_analysis(payload)
    result["c11"] = calculate_c11(target)
    serialized_target = target.to_dict()
    result["input_data"] = {
        "target_status": target.status,
        "target_missing_fields": list(target.missing_fields),
        "target_sources": serialized_target["sources"],
        "peer_statuses": [
            {"ticker": supplied_peers[ticker].ticker, "status": supplied_peers[ticker].status}
            for ticker in requested_peers
        ],
    }
    return result
