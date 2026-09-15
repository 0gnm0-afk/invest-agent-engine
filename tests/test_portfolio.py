import unittest
from datetime import datetime, timezone

from invest_agent.trading.portfolio import review, size_preview


class PortfolioTests(unittest.TestCase):
    def bundle(self):
        return {"schema_version":1,"source":"synthetic","as_of":"2026-09-11T00:00:00+00:00","max_age_hours":24,
                "base_currency":"KRW","fx_to_base":{"KRW":"1","USD":"1400"},"accounts":[{"alias":"test","cash":{"KRW":"1000000"},
                "positions":[{"symbol":"TEST","currency":"USD","quantity":"10","price":"100","average_cost":"80",
                "adopted_stop":{"price":"90","adoption_ref":"synthetic-1","price_basis":"executable_raw"}}]}]}
    def test_weight_fx_stop_and_profit_protection(self):
        result=review(self.bundle(),datetime(2026,9,11,1,tzinfo=timezone.utc))
        self.assertEqual(result["equity_base"],"2400000")
        self.assertEqual(result["known_stop_exposure_base"],"140000")
        self.assertEqual(result["positions"][0]["stop_pnl_before_costs_base"],"140000")
        self.assertTrue(result["stop_exposure_complete"])
    def test_missing_stop_and_fx_are_not_zero_risk(self):
        bundle=self.bundle(); bundle["accounts"][0]["positions"][0].pop("adopted_stop")
        self.assertFalse(review(bundle)["stop_exposure_complete"])
        bundle["fx_to_base"].pop("USD")
        result=review(bundle)
        self.assertIsNone(result["equity_base"])
        self.assertIsNone(result["positions"][0]["weight"])
    def request(self):
        return {"entry":"100","stop":"90","existing_quantity":"0","current_price":"100","average_cost":"0",
                "available_cash":"1000","position_loss_budget":"100","other_portfolio_risk":"0","portfolio_loss_budget":"200",
                "position_value_cap":"1000","slippage_per_share":"0","cost_per_share":"0","quantity_step":"1",
                "split_fractions":["0.5","0.5"],"snapshot_stale":False,"open_orders_reconciled":True}
    def test_sizing_and_split_conservation(self):
        result=size_preview(self.request())
        self.assertEqual(result["quantity"],"10")
        self.assertEqual(result["split_quantities"],["5","5"])
        self.assertEqual(result["post_add_stop_exposure"],"100")
    def test_loss_add_and_unknown_orders_blocked(self):
        request=self.request(); request.update(existing_quantity="10",average_cost="110")
        self.assertEqual(size_preview(request)["state"],"blocked")
        request=self.request(); request["open_orders_reconciled"]=False
        self.assertEqual(size_preview(request)["state"],"blocked")


if __name__ == "__main__": unittest.main()
