import unittest
from datetime import datetime, timezone

from invest_agent.trading.policy_review import inspect
from invest_agent.trading.portfolio import review


class LegacyRiskCompatibilityTests(unittest.TestCase):
    def test_breached_old_adopted_stop_is_unknown_not_zero(self):
        bundle = {"schema_version": 1, "source": "synthetic", "as_of": datetime.now(timezone.utc).isoformat(),
                  "max_age_hours": 24, "base_currency": "KRW", "fx_to_base": {"KRW": "1"}, "accounts": [
                      {"alias": "a", "cash": {"KRW": "1000"}, "positions": [
                          {"symbol": "TEST", "market": "KR", "currency": "KRW", "quantity": "10", "price": "89", "average_cost": "80",
                           "adopted_stop": {"price": "90", "adoption_ref": "legacy-user", "price_basis": "executable_raw"}}]}]}
        result = review(bundle)
        row = result["positions"][0]
        self.assertIsNone(row["stop_exposure_base"])
        self.assertEqual(row["risk_status"], "BREACHED_UNRESOLVED")
        self.assertEqual(row["breach_shortfall_base"], "10")
        self.assertFalse(result["stop_exposure_complete"])
        checked = inspect(bundle, result)
        self.assertIn("adopted_stop_reached_execution_unknown", [i["reason"] for i in checked["issues"]])


if __name__ == "__main__":
    unittest.main()
