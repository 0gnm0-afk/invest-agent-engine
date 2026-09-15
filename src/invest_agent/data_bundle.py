"""Normalized read-only market and financial data contract."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class SourceMetadata:
    source: str | None
    fetched_at: str
    as_of: str | None
    unit: str | None
    currency: str | None
    adjusted: bool | None
    consolidated: bool | None
    report_id: str | None
    error: str | None = None


@dataclass(frozen=True)
class AnnualFinancial:
    year: int
    statement_basis: str | None
    revenue: float | None
    ebitda: float | None
    ebit: float | None
    d_and_a: float | None
    cfo: float | None
    capex: float | None
    net_income: float | None
    equity: float | None
    cash: float | None
    debt: float | None


@dataclass(frozen=True)
class DailyPrice:
    date: str
    close: float
    volume: float
    trading_value: float


@dataclass(frozen=True)
class DataBundle:
    """One stock's normalized inputs, without valuation or investment judgement."""

    schema_version: str
    ticker: str
    name: str | None
    market: str | None
    currency: str
    quote: dict[str, float | str | None]
    annual_financials: tuple[AnnualFinancial, ...]
    daily_prices: tuple[DailyPrice, ...]
    sources: dict[str, SourceMetadata]
    missing_fields: tuple[str, ...] = field(default_factory=tuple)
    status: str = "ok"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
