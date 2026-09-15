"""KOSPI/KOSDAQ DataProvider backed by the public Korea Stock MCP."""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any

from fastmcp import Client

from .data_bundle import AnnualFinancial, DailyPrice, DataBundle, SourceMetadata
from .supplements import fetch_daily_prices, fetch_dart_supplements

DEFAULT_MCP_URL = "https://korea-stock-analyzer-mcp-production.up.railway.app/mcp"
_TICKER_PATTERN = re.compile(r"^\d{6}$")


class ProviderError(RuntimeError):
    """Raised when a provider response cannot satisfy the public contract."""


def validate_korean_ticker(ticker: str) -> str:
    normalized = ticker.strip()
    if not _TICKER_PATTERN.fullmatch(normalized):
        raise ValueError("Korean stock ticker must be exactly six digits (for example, 005930).")
    return normalized


def _result_data(result: Any) -> dict[str, Any]:
    data = getattr(result, "data", None)
    if not isinstance(data, dict):
        raise ProviderError("Korea Stock MCP returned an unsupported response shape.")
    return data


def normalize_data_bundle(
    ticker: str,
    quote: dict[str, Any],
    financials: dict[str, Any],
    *,
    fetched_at: str,
    daily_prices: list[dict[str, Any]] | None = None,
    supplements: dict[int, dict[str, Any]] | None = None,
    supplement_error: str | None = None,
) -> DataBundle:
    """Map MCP payloads to the stable engine contract; missing never becomes zero."""

    normalized_ticker = validate_korean_ticker(ticker)
    currency = str(financials.get("unit") or "KRW")
    missing: list[str] = []
    supplements = supplements or {}

    quote_fields = {
        "price": quote.get("price"),
        "market_cap": quote.get("market_cap"),
        "shares_outstanding": quote.get("shares_outstanding"),
        "volume": quote.get("volume"),
        "high_52w": quote.get("high_52w"),
        "low_52w": quote.get("low_52w"),
        "as_of": quote.get("as_of"),
    }
    for field_name in ("price", "market_cap", "shares_outstanding"):
        if quote_fields[field_name] is None:
            missing.append(f"quote.{field_name}")

    annuals: list[AnnualFinancial] = []
    raw_years = financials.get("years")
    if not isinstance(raw_years, list):
        raise ProviderError("Korea Stock MCP financial response has no annual year list.")

    for raw in sorted(raw_years, key=lambda row: row.get("year", 0)):
        year = raw.get("year")
        if not isinstance(year, int):
            raise ProviderError("Korea Stock MCP returned an annual record without an integer year.")
        supplement = supplements.get(year, {})
        ebit = raw.get("operating_income")
        d_and_a = supplement.get("d_and_a")
        annual = AnnualFinancial(
            year=year,
            statement_basis=raw.get("fs_div"),
            revenue=raw.get("revenue"),
            ebitda=ebit + d_and_a if ebit is not None and d_and_a is not None else None,
            ebit=ebit,
            d_and_a=d_and_a,
            cfo=raw.get("cfo"),
            capex=raw.get("capex"),
            net_income=raw.get("net_income"),
            equity=raw.get("equity"),
            cash=supplement.get("cash"),
            debt=supplement.get("debt"),
        )
        annuals.append(annual)
        for field_name in (
            "revenue",
            "ebitda",
            "ebit",
            "d_and_a",
            "cfo",
            "capex",
            "net_income",
            "equity",
            "cash",
            "debt",
        ):
            if getattr(annual, field_name) is None:
                missing.append(f"annual_financials.{year}.{field_name}")

    if not annuals:
        missing.append("annual_financials")

    latest_year = annuals[-1].year if annuals else None
    latest_supplement = supplements.get(latest_year, {}) if latest_year is not None else {}
    basis = financials.get("statement_basis")
    normalized_daily_prices = tuple(
        DailyPrice(
            date=str(row["date"]),
            close=float(row["close"]),
            volume=float(row["volume"]),
            trading_value=float(row["trading_value"]),
        )
        for row in (daily_prices or [])
    )
    if len(normalized_daily_prices) < 250:
        missing.append("daily_prices.250_trading_days")

    sources = {
        "quote": SourceMetadata(
            source=quote.get("data_source"),
            fetched_at=fetched_at,
            as_of=quote.get("as_of"),
            unit=currency,
            currency=currency,
            adjusted=None,
            consolidated=None,
            report_id=None,
        ),
        "financials": SourceMetadata(
            source=financials.get("data_source"),
            fetched_at=fetched_at,
            as_of=f"{latest_year}-12-31" if latest_year is not None else None,
            unit=currency,
            currency=currency,
            adjusted=None,
            consolidated=True if basis == "CFS" else False if basis == "OFS" else None,
            report_id=None,
        ),
        "daily_prices": SourceMetadata(
            source="KRX via FinanceDataReader",
            fetched_at=fetched_at,
            as_of=normalized_daily_prices[-1].date if normalized_daily_prices else None,
            unit=currency,
            currency=currency,
            adjusted=None,
            consolidated=None,
            report_id=None,
            error=None if normalized_daily_prices else "No daily price rows returned.",
        ),
        "supplemental_financials": SourceMetadata(
            source="DART OpenAPI all accounts and XBRL via OpenDartReader",
            fetched_at=fetched_at,
            as_of=f"{latest_year}-12-31" if latest_year is not None else None,
            unit=currency,
            currency=currency,
            adjusted=None,
            consolidated=True if basis == "CFS" else False if basis == "OFS" else None,
            report_id=str(latest_supplement["report_id"])
            if latest_supplement.get("report_id")
            else None,
            error=supplement_error,
        ),
    }
    missing.extend(
        (
            "sources.quote.adjusted",
            "sources.quote.report_id",
            "sources.financials.report_id",
            "sources.daily_prices.adjusted",
            "sources.daily_prices.report_id",
            "sources.supplemental_financials.report_id",
        )
    )

    core_missing = {
        "quote.price",
        "quote.market_cap",
        "quote.shares_outstanding",
        "annual_financials",
    }
    analysis_missing = core_missing.intersection(missing) | {
        field for field in missing if field.endswith((".ebitda", ".cash", ".debt"))
    }
    if "daily_prices.250_trading_days" in missing:
        analysis_missing.add("daily_prices.250_trading_days")
    status = "partial" if analysis_missing else "complete"

    return DataBundle(
        schema_version="1.0",
        ticker=normalized_ticker,
        name=quote.get("name"),
        market=quote.get("market"),
        currency=currency,
        quote=quote_fields,
        annual_financials=tuple(annuals),
        daily_prices=normalized_daily_prices,
        sources=sources,
        missing_fields=tuple(dict.fromkeys(missing)),
        status=status,
    )


class KoreaStockMcpProvider:
    """Read-only adapter for the deployed public Korea Stock MCP server."""

    def __init__(
        self,
        server_url: str = DEFAULT_MCP_URL,
        dart_api_key: str | None = None,
    ) -> None:
        self.server_url = server_url
        self.dart_api_key = dart_api_key or os.getenv("DART_API_KEY")

    async def fetch(self, ticker: str, years: int = 5) -> DataBundle:
        normalized_ticker = validate_korean_ticker(ticker)
        if not 2 <= years <= 10:
            raise ValueError("years must be between 2 and 10.")

        async with Client(self.server_url) as client:
            quote_result = await client.call_tool("get_quote", {"ticker": normalized_ticker})
            financial_result = await client.call_tool(
                "get_financials", {"ticker": normalized_ticker, "years": years}
            )

        financial_data = _result_data(financial_result)
        raw_years = financial_data.get("years") or []
        year_values = [row["year"] for row in raw_years if isinstance(row.get("year"), int)]
        daily_prices = await fetch_daily_prices(normalized_ticker)
        supplements, supplement_error = await fetch_dart_supplements(
            normalized_ticker, year_values, self.dart_api_key
        )

        fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        return normalize_data_bundle(
            normalized_ticker,
            _result_data(quote_result),
            financial_data,
            fetched_at=fetched_at,
            daily_prices=daily_prices,
            supplements=supplements,
            supplement_error=supplement_error,
        )
