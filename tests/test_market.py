import unittest
from datetime import datetime, timedelta, timezone
from typing import ClassVar

from invest_agent.trading.charts import completed_week_bars
from invest_agent.trading.market import evaluate
from invest_agent.trading.market_provider import completed_sessions


def series(closes):
    return {"market":"US", "symbol":"TEST", "currency":"USD", "benchmark":"INDEX", "price_basis":"synthetic",
            "bars":[{"date":(datetime(2025,1,1, tzinfo=timezone.utc)+timedelta(days=i)).date().isoformat(),"open":c,"high":c+1,"low":c-1,"close":c,"volume":100} for i,c in enumerate(closes)]}


class MarketTests(unittest.TestCase):
    rule: ClassVar[dict] = {"ma_period":5,"slope_window":2,"transition_window":5,"rs_period":5,"activity_window":3,"min_rs":0,"min_activity_ratio":0,"min_average_value":0}

    def evaluate(self, closes):
        item = series(closes)
        return evaluate(item,series([100]*len(closes)),self.rule,[b["date"] for b in item["bars"]])

    def test_turn_vs_flat_vs_already_rising(self):
        turn = self.evaluate([100-i for i in range(15)]+[86,87,90,94,98])
        self.assertEqual(turn["state"],"candidate")
        self.assertGreater(turn["ma_slope"],0)
        self.assertEqual(self.evaluate([100]*20)["state"],"not_selected")
        rising = self.evaluate(list(range(100,120)))
        self.assertEqual(rising["state"],"not_selected")
        self.assertIn("recent_turn",rising["failed_conditions"])

    def test_missing_date_is_not_silently_compressed(self):
        item=series([100]*20)
        sessions=[b["date"] for b in item["bars"]]
        item["bars"].pop(15)
        self.assertEqual(evaluate(item,series([100]*20),self.rule,sessions)["state"],"unavailable")

    def test_rs_uses_same_dates_and_ratio(self):
        closes=list(range(100,120))
        result=self.evaluate(closes)
        self.assertAlmostEqual(result["relative_strength"],119/114-1)

    def test_old_invalid_bar_truncates_history_without_filling(self):
        item=series(list(range(100,140)))
        sessions=[b["date"] for b in item["bars"]]
        item["bars"][5]["close"]=None
        result=evaluate(item,series([100]*40),self.rule,sessions)
        self.assertEqual(result["state"],"not_selected")
        self.assertTrue(result["history_truncated"])
        self.assertEqual(result["valid_history_start"],sessions[6])

    def test_completed_sessions_excludes_open_us_session(self):
        as_of=datetime(2026,9,11,13,35,tzinfo=timezone.utc)
        self.assertEqual(completed_sessions("US",as_of)[-1],"2026-09-10")
        kr=completed_sessions("KR",as_of)
        self.assertEqual(kr[-1],"2026-09-11")
        self.assertNotIn("2026-06-03",kr)
        self.assertNotIn("2026-07-17",kr)

    def test_us_holiday_and_early_close(self):
        self.assertEqual(completed_sessions("US",datetime(2026,7,3,23,tzinfo=timezone.utc))[-1],"2026-07-02")
        self.assertEqual(completed_sessions("US",datetime(2025,11,28,18,31,tzinfo=timezone.utc))[-1],"2025-11-28")

    def test_weekly_excludes_incomplete_week(self):
        bars=series([100]*20)["bars"]
        result=completed_week_bars(bars,"2025-01-20")
        self.assertLess(result[-1]["date"],"2025-01-20")


if __name__ == "__main__":
    unittest.main()
