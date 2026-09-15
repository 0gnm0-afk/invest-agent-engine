from __future__ import annotations

import unittest

from invest_agent.korea_stock_provider import (
    normalize_data_bundle,
    validate_korean_ticker,
)
from invest_agent.supplements import extract_dart_supplement, extract_xbrl_d_and_a


def quote(ticker: str, name: str, market: str) -> dict:
    return {
        "ticker": ticker,
        "name": name,
        "market": market,
        "price": 100_000,
        "as_of": "2026-08-07",
        "market_cap": 10_000_000_000,
        "shares_outstanding": 100_000,
        "high_52w": 120_000,
        "low_52w": 70_000,
        "volume": 123_456,
        "data_source": "KRX via FinanceDataReader",
    }


def financials(ticker: str) -> dict:
    return {
        "ticker": ticker,
        "statement_basis": "CFS",
        "unit": "KRW",
        "years": [
            {
                "year": 2024,
                "fs_div": "CFS",
                "revenue": 1_000,
                "operating_income": 100,
                "net_income": 80,
                "equity": 500,
                "cfo": 120,
                "capex": 50,
            }
        ],
        "data_source": "DART annual filing",
    }


class KoreaStockProviderTests(unittest.TestCase):
    def test_two_tickers_share_the_same_contract(self) -> None:
        fixtures = (
            ("000660", "SK하이닉스", "KOSPI"),
            ("247540", "에코프로비엠", "KOSDAQ"),
        )
        bundles = [
            normalize_data_bundle(
                ticker,
                quote(ticker, name, market),
                financials(ticker),
                fetched_at="2026-08-08T12:00:00+00:00",
            )
            for ticker, name, market in fixtures
        ]

        self.assertEqual([bundle.ticker for bundle in bundles], ["000660", "247540"])
        self.assertEqual(
            bundles[0].to_dict().keys(),
            bundles[1].to_dict().keys(),
        )
        self.assertEqual(bundles[0].annual_financials[0].ebit, 100)

    def test_unavailable_values_remain_none_and_are_listed(self) -> None:
        bundle = normalize_data_bundle(
            "000660",
            quote("000660", "SK하이닉스", "KOSPI"),
            financials("000660"),
            fetched_at="2026-08-08T12:00:00+00:00",
        )

        annual = bundle.annual_financials[0]
        self.assertIsNone(annual.ebitda)
        self.assertIsNone(annual.cash)
        self.assertIn("annual_financials.2024.ebitda", bundle.missing_fields)
        self.assertIn("annual_financials.2024.cash", bundle.missing_fields)
        self.assertEqual(bundle.status, "partial")

    def test_rejects_non_six_digit_ticker(self) -> None:
        with self.assertRaises(ValueError):
            validate_korean_ticker("SKHYNIX")

    def test_dart_supplement_builds_ebitda_inputs_without_defaults(self) -> None:
        rows = [
            {
                "fs_div": "CFS",
                "sj_div": "BS",
                "account_id": "ifrs-full_CashAndCashEquivalents",
                "account_nm": "현금및현금성자산",
                "thstrm_amount": "30,000",
            },
            {
                "fs_div": "CFS",
                "sj_div": "CF",
                "account_id": "dart_AdjustmentsForDepreciationAndAmortisationExpense",
                "account_nm": "감가상각비 및 무형자산상각비",
                "thstrm_amount": "4,000",
            },
            {
                "fs_div": "CFS",
                "sj_div": "BS",
                "account_id": "ifrs-full_ShorttermBorrowings",
                "account_nm": "단기차입금",
                "thstrm_amount": "10,000",
            },
            {
                "fs_div": "CFS",
                "sj_div": "BS",
                "account_id": "ifrs-full_LongtermBorrowings",
                "account_nm": "장기차입금",
                "thstrm_amount": "20,000",
            },
        ]

        supplement = extract_dart_supplement(rows)
        self.assertEqual(supplement["statement_basis"], "CFS")
        self.assertEqual(supplement["d_and_a"], 4_000)
        self.assertEqual(supplement["cash"], 30_000)
        self.assertEqual(supplement["debt"], 30_000)

    def test_zero_debt_is_not_treated_as_missing(self) -> None:
        supplement = extract_dart_supplement(
            [
                {
                    "fs_div": "CFS",
                    "sj_div": "BS",
                    "account_id": "ifrs-full_ShorttermBorrowings",
                    "account_nm": "단기차입금",
                    "thstrm_amount": "0",
                }
            ]
        )
        self.assertEqual(supplement["debt"], 0)

    def test_xbrl_prefers_current_consolidated_reported_d_and_a(self) -> None:
        xml = b"""<?xml version="1.0" encoding="UTF-8"?>
        <xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance"
          xmlns:xbrldi="http://xbrl.org/2006/xbrldi"
          xmlns:ifrs-full="http://xbrl.ifrs.org/taxonomy/2024-03-27/ifrs-full"
          xmlns:dart="http://dart.fss.or.kr/taxonomy/2024-06-30/dart">
          <xbrli:context id="consolidated">
            <xbrli:entity><xbrli:identifier scheme="test">1</xbrli:identifier>
              <xbrli:segment>
                <xbrldi:explicitMember dimension="ifrs-full:ConsolidatedAndSeparateFinancialStatementsAxis">ifrs-full:ConsolidatedMember</xbrldi:explicitMember>
                <xbrldi:explicitMember dimension="ifrs-full:CarryingAmountAxis">dart:ReportedAmountMember</xbrldi:explicitMember>
              </xbrli:segment>
            </xbrli:entity>
            <xbrli:period><xbrli:startDate>2025-01-01</xbrli:startDate><xbrli:endDate>2025-12-31</xbrli:endDate></xbrli:period>
          </xbrli:context>
          <xbrli:context id="separate">
            <xbrli:entity><xbrli:identifier scheme="test">1</xbrli:identifier>
              <xbrli:segment>
                <xbrldi:explicitMember dimension="ifrs-full:ConsolidatedAndSeparateFinancialStatementsAxis">ifrs-full:SeparateMember</xbrldi:explicitMember>
                <xbrldi:explicitMember dimension="ifrs-full:CarryingAmountAxis">dart:ReportedAmountMember</xbrldi:explicitMember>
              </xbrli:segment>
            </xbrli:entity>
            <xbrli:period><xbrli:startDate>2025-01-01</xbrli:startDate><xbrli:endDate>2025-12-31</xbrli:endDate></xbrli:period>
          </xbrli:context>
          <ifrs-full:DepreciationAndAmortisationExpense contextRef="separate" unitRef="KRW">999</ifrs-full:DepreciationAndAmortisationExpense>
          <ifrs-full:DepreciationAndAmortisationExpense contextRef="consolidated" unitRef="KRW">123</ifrs-full:DepreciationAndAmortisationExpense>
          <ifrs-full:DepreciationExpense contextRef="consolidated" unitRef="KRW">70</ifrs-full:DepreciationExpense>
          <ifrs-full:AmortisationExpense contextRef="consolidated" unitRef="KRW">30</ifrs-full:AmortisationExpense>
        </xbrli:xbrl>"""

        self.assertEqual(extract_xbrl_d_and_a(xml, 2025, "CFS"), 123)
        split_only = xml.replace(
            b"DepreciationAndAmortisationExpense", b"IgnoredCombinedFact"
        )
        self.assertEqual(extract_xbrl_d_and_a(split_only, 2025, "CFS"), 100)


if __name__ == "__main__":
    unittest.main()
