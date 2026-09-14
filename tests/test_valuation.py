import unittest
from decimal import Decimal

from invest_agent.trading.valuation import quantile_type7, reference_bands


class ValuationTests(unittest.TestCase):
    def bundle(self):
        rows=[]
        for i in range(36):
            year,month=2023+i//12,1+i%12
            import calendar
            stamp=f"{year}-{month:02d}-{calendar.monthrange(year,month)[1]}"
            rows.append({"price_date":stamp,"is_month_end_close":True,"eps_known_at":f"{year}-{month:02d}-01", "close":"100","ttm_diluted_eps":"10",
                         "price_basis":"split_basis_1","eps_basis":"split_basis_1","source_ref":"synthetic_history"})
        return {"schema_version":1,"source":"synthetic","symbol":"TEST","as_of":"2026-09-11","currency":"KRW","monthly_history":rows,
                "forecast":{"period_type":"NTM","estimated_at":"2026-09-01","source_ref":"synthetic_forecast","diluted_eps":"12","common_net_income":"1200000"}}
    def test_exact_two_bands(self):
        result=reference_bands(self.bundle())
        self.assertEqual(result["state"],"available")
        self.assertEqual(Decimal(result["price_band"]["median"]),120)
        self.assertEqual(Decimal(result["market_cap_band"]["median"]),12000000)
    def test_type7_interpolation(self):
        self.assertEqual(quantile_type7([Decimal(x) for x in (1,2,3,4)],Decimal("0.25")),Decimal("1.75"))
    def test_future_known_eps_reduces_eligible_count(self):
        b=self.bundle(); b["monthly_history"][0]["eps_known_at"]="2026-01-01"
        self.assertEqual(reference_bands(b)["state"],"unavailable")
    def test_fy1_never_substitutes_ntm(self):
        b=self.bundle(); b["forecast"]["period_type"]="FY1"
        self.assertEqual(reference_bands(b)["state"],"unavailable")
    def test_missing_earnings_does_not_invent_market_cap(self):
        b=self.bundle(); b["forecast"]["common_net_income"]=None
        result=reference_bands(b)
        self.assertEqual(result["state"],"partial")
        self.assertIsNone(result["market_cap_band"])


if __name__=="__main__": unittest.main()
