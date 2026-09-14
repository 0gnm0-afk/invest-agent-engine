from __future__ import annotations

import copy
import unittest

from invest_agent.calculators import calculate_analysis


def input_payload() -> dict:
    return {
        "ticker": "TEST",
        "as_of": "2026-08-08",
        "currency": "KRW",
        "current_price": 100,
        "shares_outstanding": 10,
        "cash": 50,
        "debt": 20,
        "scenarios": {
            "bearish": {
                "fcff": [80, 80, 80, 80, 80],
                "discount_rate": 0.12,
                "terminal_growth_rate": 0.01,
            },
            "base": {
                "fcff": [100, 100, 100, 100, 100],
                "discount_rate": 0.10,
                "terminal_growth_rate": 0.02,
            },
            "bullish": {
                "fcff": [120, 120, 120, 120, 120],
                "discount_rate": 0.09,
                "terminal_growth_rate": 0.025,
            },
        },
        "historical_financials": [
            {"year": 2021, "cfo": 90, "capex": 50, "ebitda": 100},
            {"year": 2022, "cfo": 100, "capex": 50, "ebitda": 100},
            {"year": 2023, "cfo": 110, "capex": 50, "ebitda": 100},
        ],
        "c08_expected": {
            "bearish": {"fcff": 80, "ebitda": 100},
            "base": {"fcff": 80, "ebitda": 100},
            "bullish": {"fcff": 80, "ebitda": 100},
        },
        "peers": [
            {
                "ticker": "PEER1",
                "name": "Synthetic Peer",
                "ev_to_ebit": 8.0,
                "per": 10.0,
                "pbr": 1.2,
                "as_of": "2026-08-08",
            }
        ],
    }


class CalculatorTests(unittest.TestCase):
    def test_overstated_case_and_c02_values(self) -> None:
        result = calculate_analysis(input_payload())

        self.assertEqual(result["c08"]["scenarios"]["base"]["assessment"], "possibly_overstated")
        self.assertEqual(result["c08"]["scenarios"]["base"]["confidence"], "low")
        self.assertFalse(result["c08"]["scenarios"]["base"]["dcf_value_adjusted"])
        self.assertAlmostEqual(result["c02"]["scenarios"]["base"]["terminal_value"], 1275.0)
        self.assertAlmostEqual(
            result["c02"]["scenarios"]["base"]["equity_value"],
            result["c02"]["scenarios"]["base"]["enterprise_value"] + 30,
        )
        self.assertEqual(result["c02"]["peers"][0]["per"], 10.0)

    def test_understated_case(self) -> None:
        payload = input_payload()
        payload["c08_expected"] = {
            name: {"fcff": 30, "ebitda": 100}
            for name in ("bearish", "base", "bullish")
        }

        result = calculate_analysis(payload)

        self.assertEqual(result["c08"]["scenarios"]["base"]["assessment"], "possibly_understated")

    def test_unavailable_case(self) -> None:
        payload = copy.deepcopy(input_payload())
        payload["historical_financials"] = payload["historical_financials"][:2]

        result = calculate_analysis(payload)

        self.assertEqual(result["c08"]["scenarios"]["base"]["assessment"], "unavailable")
        self.assertEqual(result["c08"]["scenarios"]["base"]["confidence"], "unavailable")
        self.assertFalse(result["c08"]["scenarios"]["base"]["dcf_value_adjusted"])


if __name__ == "__main__":
    unittest.main()
