from __future__ import annotations

import unittest
from datetime import date, timedelta

from invest_agent.analysis_pipeline import calculate_from_data_bundles
from invest_agent.data_bundle import (
    AnnualFinancial,
    DailyPrice,
    DataBundle,
    SourceMetadata,
)
from invest_agent.reporting import render_markdown_report


def source() -> SourceMetadata:
    return SourceMetadata(
        source="synthetic",
        fetched_at="2026-08-08T00:00:00+00:00",
        as_of="2025-12-31",
        unit="KRW",
        currency="KRW",
        adjusted=None,
        consolidated=True,
        report_id="TEST",
    )


def annual(year: int, *, cash: float, debt: float) -> AnnualFinancial:
    return AnnualFinancial(
        year=year,
        statement_basis="CFS",
        revenue=1000,
        ebitda=100,
        ebit=80,
        d_and_a=20,
        cfo=90,
        capex=40,
        net_income=50,
        equity=500,
        cash=cash,
        debt=debt,
    )


def bundle(ticker: str, *, market_cap: float, cash: float, debt: float) -> DataBundle:
    metadata = source()
    first_day = date(2025, 1, 1)
    daily_prices = tuple(
        DailyPrice(
            date=(first_day + timedelta(days=index)).isoformat(),
            close=1,
            volume=index + 1,
            trading_value=index + 1,
        )
        for index in range(250)
    )
    return DataBundle(
        schema_version="1.0",
        ticker=ticker,
        name=f"Company {ticker}",
        market="KOSPI",
        currency="KRW",
        quote={
            "price": 100,
            "market_cap": market_cap,
            "shares_outstanding": 10,
            "as_of": "2026-08-08",
        },
        annual_financials=(
            annual(2023, cash=cash, debt=debt),
            annual(2024, cash=cash, debt=debt),
            annual(2025, cash=cash, debt=debt),
        ),
        daily_prices=daily_prices,
        sources={"financials": metadata},
        status="complete",
    )


class AnalysisPipelineTests(unittest.TestCase):
    def test_provider_data_flows_into_calculator_and_peer_multiples(self) -> None:
        assumptions = {
            "scenarios": {
                name: {
                    "fcff": [100, 100, 100, 100, 100],
                    "discount_rate": 0.1,
                    "terminal_growth_rate": 0.02,
                }
                for name in ("bearish", "base", "bullish")
            },
            "c08_expected": {
                name: {"fcff": 50, "ebitda": 100}
                for name in ("bearish", "base", "bullish")
            },
            "peers": ["005930"],
        }
        target = bundle("000660", market_cap=1000, cash=30, debt=20)
        peer = bundle("005930", market_cap=1000, cash=100, debt=200)

        result = calculate_from_data_bundles(target, assumptions, [peer])

        base = result["c02"]["scenarios"]["base"]
        self.assertAlmostEqual(base["equity_value"], base["enterprise_value"] + 10)
        self.assertEqual(result["c08"]["scenarios"]["base"]["assessment"], "roughly_neutral")
        self.assertEqual(result["c02"]["peers"][0]["ev_to_ebit"], 13.75)
        self.assertEqual(result["c02"]["peers"][0]["per"], 20.0)
        self.assertEqual(result["c02"]["peers"][0]["pbr"], 2.0)
        self.assertEqual(result["c11"]["scope"], "reference_only")
        self.assertEqual(result["c11"]["average_trading_value"]["20d"], 240.5)
        self.assertEqual(result["c11"]["average_trading_value"]["60d"], 220.5)
        self.assertEqual(result["c11"]["average_trading_value"]["250d"], 125.5)
        self.assertEqual(result["c11"]["liquidity_assessment"], "unavailable")
        self.assertEqual(result["input_data"]["target_status"], "complete")

        report = render_markdown_report(result)
        self.assertIn("## C02", report)
        self.assertIn("## C08", report)
        self.assertIn("## C11", report)
        self.assertIn("240.50", report)
        self.assertIn("### 데이터 출처", report)
        self.assertIn("synthetic", report)
        self.assertIn("매수·매도 지시가 아니다", report)


if __name__ == "__main__":
    unittest.main()
