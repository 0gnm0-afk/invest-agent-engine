import copy
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from invest_agent.trading.morning import market_component
from invest_agent.trading.runner import PipelineRunner


class MorningTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.runner=PipelineRunner(self.root/"instance")
        self.as_of=datetime.now(timezone.utc).isoformat()
        rule={"ma_period":2,"slope_window":1,"transition_window":1,"rs_period":1,"activity_window":1,
              "min_rs":0,"min_activity_ratio":0,"min_average_value":0}
        dates=[(datetime(2025,1,6, tzinfo=timezone.utc)+timedelta(days=i)).date().isoformat() for i in range(20)]
        bars=[{"date":day,"open":100,"high":101,"low":99,"close":100,"volume":100} for day in dates]
        self.market={"source":"synthetic","fetched_at":self.as_of,"profile":{"status":"review_only","KR":rule,"US":rule},
                     "series":[{"market":"US","symbol":"HOLD","currency":"USD","benchmark":"INDEX","price_basis":"synthetic","bars":bars,"raw_exchange_code":"NYQ",
                                "market_cap":2e9,"market_cap_currency":"USD","market_cap_as_of":dates[-1]}],
                     "benchmarks":{"INDEX":{"bars":copy.deepcopy(bars)}},"sessions":{"US":dates},"errors":[],
                     "coverage":{"scope":"configured_universe","requested":1,"received":1,"market_wide":False}}
        self.account={"schema_version":1,"source":"synthetic","as_of":self.as_of,"max_age_hours":24,"base_currency":"USD","fx_to_base":{"USD":"1"},
                      "accounts":[{"alias":"test","cash":{"USD":"1000"},"positions":[{"symbol":"HOLD","quote_symbol":"HOLD","market":"US","benchmark":"INDEX",
                      "currency":"USD","quantity":"10","price":"100","average_cost":"80","adopted_stop":{"adoption_ref":"synthetic","price_basis":"executable_raw","price":"90"}}]}]}
        self.write("market.json",self.market)
        self.write("account.json",self.account)
        self.config={"schema_version":1,"market_snapshot":"market.json","account_snapshot":"account.json","valuation_inputs":[]}
        self.write("morning.json",self.config)

    def write(self,name,value):
        (self.root/name).write_text(json.dumps(value),encoding="utf-8")

    def start(self,stop_after=None):
        with patch("invest_agent.trading.charts.generate",return_value=[]):
            return self.runner.run_morning(self.root/"morning.json","2026-09-11",stop_after)

    def payload(self,run,step):
        return next(a["payload"] for a in run["artifacts"] if a["step"]==step)

    def test_one_report_and_all_held_stocks_even_when_not_candidates(self):
        result=self.start()
        self.assertEqual(result["state"],"partial")  # Missing sourced valuation, not an empty success.
        self.assertEqual([s["step_key"] for s in result["steps"]],["snapshot","market","portfolio","valuation","report"])
        market=self.payload(result,"market")
        self.assertEqual(market["screen"]["candidate_count"],0)
        self.assertEqual(market["snapshot"]["held_symbols"],["HOLD"])
        self.assertEqual(self.payload(result,"portfolio")["result"]["positions"][0]["symbol"],"HOLD")
        self.assertEqual(self.payload(result,"valuation")["rows"][0]["state"],"unavailable")
        report=next((self.root/"instance").glob("reports/*/*/report.md")).read_text(encoding="utf-8")
        self.assertIn("HOLD",report)
        self.assertIn("synthetic",report)
        self.assertIn("보유 전수",report)
        self.assertIn("계좌 합산 비중·계획 대조",report)
        self.assertIn("손절가 기준 손익(비용 전)",report)

    def test_resume_after_market_does_not_reread_inputs(self):
        stopped=self.start("market")
        for name in ("morning.json","market.json","account.json"):
            (self.root/name).unlink()
        with patch("invest_agent.trading.charts.generate",return_value=[]):
            resumed=self.runner.resume(stopped["run_id"])
        self.assertEqual(resumed["state"],"partial")
        self.assertEqual(len(resumed["steps"]),5)
        self.assertEqual(self.payload(resumed,"portfolio")["result"]["equity_base"],"2000")

    def test_matching_day_reuses_run_and_refresh_preserves_history(self):
        first=self.start()
        second=self.start()
        self.assertEqual(first["run_id"],second["run_id"])
        self.assertEqual(len(second["steps"]),5)
        with patch("invest_agent.trading.charts.generate",return_value=[]):
            fresh=self.runner.run_morning(self.root/"morning.json","2026-09-11",refresh=True)
        self.assertNotEqual(first["run_id"],fresh["run_id"])
        self.assertEqual(len(self.runner.status()["runs"]),2)

    def test_plan_snapshot_is_frozen_on_resume_and_changes_new_run_identity(self):
        from invest_agent.trading.plans import PlanLedger
        from invest_agent.trading.runner import writable_store
        fixture=Path(__file__).resolve().parents[1]/"examples"/"plan_fixture.json"
        plan=json.loads(fixture.read_text(encoding="utf-8"))
        plan["adopted_at"]=(datetime.now(timezone.utc)-timedelta(days=1)).isoformat()
        with writable_store(self.root/"instance") as store:
            PlanLedger(store).record(plan)
        first=self.start("market")
        with writable_store(self.root/"instance") as store:
            PlanLedger(store).record_event({"schema_version":1,"source":"synthetic","source_ref":"test",
                "event_id":"close-plan","plan_id":plan["plan_id"],"kind":"close","occurred_at":self.as_of})
        with patch("invest_agent.trading.charts.generate",return_value=[]):
            self.runner.resume(first["run_id"])
        reports=self.root/"instance"/"reports"
        old=next(reports.glob("*/"+first["run_id"]+"/report.md")).read_text(encoding="utf-8")
        self.assertIn("저장된 분할 계획",old)
        self.assertIn("synthetic-swing-1",old)
        second=self.start()
        self.assertNotEqual(first["run_id"],second["run_id"])
        new=next(reports.glob("*/"+second["run_id"]+"/report.md")).read_text(encoding="utf-8")
        self.assertNotIn("저장된 분할 계획",new)

    def test_sourced_input_bands_reach_single_report(self):
        from test_valuation import ValuationTests
        value=ValuationTests().bundle()
        value["symbol"]="HOLD"
        self.write("valuation.json",value)
        self.config["valuation_inputs"]=["valuation.json"]
        self.write("morning.json",self.config)
        result=self.start()
        self.assertEqual(result["state"],"succeeded")
        row=self.payload(result,"valuation")["rows"][0]
        self.assertEqual(row["state"],"available")
        self.assertEqual(row["input_source"],"synthetic")
        self.assertIn("120",row["price_band"]["median"])

    def test_market_failure_keeps_account_and_report(self):
        (self.root/"market.json").write_text("invalid json")
        result=self.start()
        self.assertEqual(self.payload(result,"market")["state"],"unavailable")
        self.assertEqual(self.payload(result,"portfolio")["state"],"available")
        self.assertTrue(next((self.root/"instance").glob("reports/*/*/report.md")).exists())

    def test_bad_account_keeps_market_and_known_holding_identity(self):
        self.account["accounts"][0]["positions"][0]["price"]="not-a-price"
        self.write("account.json",self.account)
        result=self.start()
        self.assertEqual(self.payload(result,"market")["state"],"available")
        p=self.payload(result,"portfolio")
        self.assertEqual(p["state"],"unavailable")
        self.assertEqual(p["positions"][0]["symbol"],"HOLD")

    def test_chart_failure_preserves_tables(self):
        with patch("invest_agent.trading.charts.generate",side_effect=OSError("disk")):
            result=self.runner.run_morning(self.root/"morning.json","2026-09-11")
        self.assertEqual(result["state"],"partial")
        self.assertEqual(self.payload(result,"report")["charts"]["state"],"unavailable")
        self.assertEqual(self.payload(result,"portfolio")["state"],"available")

    def test_live_request_unions_held_identifiers(self):
        snapshot={"market_input":{"state":"loaded","payload":{"profile":self.market["profile"],"universe":[]}},
                  "account_input":{"payload":self.account},"market_mode":"live","evaluated_at":self.as_of}
        with patch("invest_agent.trading.market_provider.collect",return_value=self.market) as collect:
            market_component(snapshot)
        self.assertEqual(collect.call_args.args[0]["additional_universe"],[{"market":"US","symbol":"HOLD","benchmark":"INDEX"}])


if __name__ == "__main__": unittest.main()
