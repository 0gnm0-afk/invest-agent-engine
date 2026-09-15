import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from invest_agent.trading.risk_ledger import RiskLedger
from invest_agent.trading.risk_monitoring import evaluate, observation_for, persist
from invest_agent.trading.risk_reporting import freeze
from invest_agent.trading.runner import writable_store
from test_buy_budget import config, group, snapshot
from test_risk_contract import adoption, position, request


def market(close=90, date="2026-09-11"):
    return {"source": "synthetic", "sessions": {"KR": [date]}, "series": [
        {"market": "KR", "symbol": "TEST", "currency": "KRW", "price_basis": "synthetic",
         "bars": [{"date": date, "open": 100, "high": 101, "low": 89, "close": close, "volume": 100}]}]}


class MorningObservationTests(unittest.TestCase):
    def test_morning_audit_maps_multiple_strategies_and_conflict_is_not_applied(self):
        for ident in ('swing', 'second'):
            self.ledger.configure_strategy({**group(), 'strategy_group_id': ident},
                request('group-' + ident, explicit_user_confirmation=True), actor='user')
            self.ledger.configure_risk({**config(), 'strategy_group_id': ident},
                request('config-' + ident, explicit_user_confirmation=True), actor='user')
            raw = {**snapshot(), 'equity_snapshot_id': 'snap-' + ident}
            self.ledger.save_equity(ident, raw, request('equity-' + ident, explicit_user_confirmation=True), actor='user')
        self.ledger.register_position({**position(), 'position_id': 'p2', 'strategy_group_id': 'second',
                                       'source': 'synthetic'}, request('p2'), actor='user')
        at = '2026-09-12T00:00:00+00:00'
        frozen = freeze(self.store)
        result = evaluate(frozen, market(), at)
        persist(self.store, 'multi', frozen, result, at)
        event = self.ledger.audit()[-1]
        contexts = {c['strategy_group_id']: c for c in event['observed_strategy_contexts']}
        self.assertEqual(contexts['swing']['position_ids'], ['p'])
        self.assertEqual(contexts['second']['position_ids'], ['p2'])
        self.assertEqual(contexts['swing']['risk_budget_snapshot_id'], 'snap-swing')
        self.assertEqual(contexts['second']['risk_budget_snapshot_id'], 'snap-second')
        self.assertIsNone(event['strategy_group_id'])
        self.assertIsNone(event['risk_budget_snapshot_id'])
        self.assertEqual(event['observation_application_state'], 'APPLIED')
        self.assertIn({'kind': 'position', 'entity_id': 'p2'}, event['applied_entity_refs'])
        count = len(self.ledger.audit())
        persist(self.store, 'multi', frozen, result, at)
        self.assertEqual(len(self.ledger.audit()), count)
        frozen = freeze(self.store)
        result = evaluate(frozen, market(), at)
        self.ledger.adopt('p', adoption('changed', '95'), actor='user')
        persist(self.store, 'conflict', frozen, result, at)
        event = self.ledger.audit()[-1]
        self.assertEqual(event['observation_application_state'], 'VERSION_CONFLICT')
        self.assertEqual(event['applied_entity_refs'], [])
        self.assertEqual(len(event['observed_strategy_contexts']), 2)
        self.assertEqual(self.ledger.get_entity('position', 'p')['current_protection_price'], '95')

    def test_user_approach_setting_survives_restart_and_overrides_default(self):
        command = {'operation': 'configure_approach', 'actor': 'user', 'position_id': 'p', 'approach_range': '10',
                   'request': request('approach', explicit_user_confirmation=True)}
        before = self.ledger.get_entity('position', 'p')
        for actor in ('llm', 'broker', 'engine'):
            with self.assertRaises(ValueError):
                self.ledger.execute_command({**command, 'actor': actor})
        with self.assertRaises(ValueError):
            self.ledger.execute_command({**command, 'approach_range': '0'})
        after = self.ledger.execute_command(command)
        event_count = len(self.ledger.audit())
        self.ledger.execute_command(command)
        self.assertEqual(len(self.ledger.audit()), event_count)
        self.assertEqual(after['initial_stop_price'], before['initial_stop_price'])
        self.assertEqual(after['current_protection_price'], before['current_protection_price'])
        self.assertEqual(after['protection_version'], before['protection_version'])
        self.assertEqual(RiskLedger(self.store).get_entity('position', 'p')['approach_range'], '10')
        at = '2026-09-12T00:00:00+00:00'
        value = market(close=100)
        value['series'][0]['bars'][0]['low'] = 99
        result = evaluate(freeze(self.store), value, at)
        self.assertEqual(result['monitor'][0]['reference_range_source'], 'USER_SETTING')
        self.assertEqual(result['monitor'][0]['state'], 'APPROACHING')
        self.assertEqual(self.ledger.audit()[-1]['event_type'], 'APPROACH_SETTING_CHANGED')
        self.ledger.execute_command({**command, 'approach_range': None,
                                     'request': request('reset-approach', explicit_user_confirmation=True)})
        result = evaluate(freeze(self.store), value, at)
        self.assertEqual(result['monitor'][0]['approach_status'], 'UNAVAILABLE')
        self.assertTrue(result['monitor'][0]['manual_required'])
        self.assertEqual(self.ledger.audit()[-1]['old_value']['approach_range'], '10')
        self.assertIsNone(self.ledger.audit()[-1]['new_value']['approach_range'])

    def test_atr_unavailable_uses_raw_tr_median_in_morning_monitor(self):
        dates = [date(2026, 8, 14) + timedelta(days=i) for i in range(29)]
        sessions = [d.isoformat() for d in dates if d.weekday() < 5]
        value = market(close=92)
        value['sessions']['KR'] = sessions
        series = value['series'][0]
        series['bars'] = [{'date': day, 'open': 92, 'high': 93, 'low': 91, 'close': 92, 'volume': 100}
                          for day in sessions]
        frozen = freeze(self.store)
        at = '2026-09-12T00:00:00+00:00'
        self.assertEqual(evaluate(frozen, value, at)['monitor'][0]['reference_range_source'], 'ATR14_WILDER')
        series['atr_available'] = False
        result = evaluate(frozen, value, at)
        m = result['monitor'][0]
        self.assertEqual(m['reference_range_source'], 'MEDIAN_TR20')
        self.assertEqual(m['reference_range'], '2')
        self.assertEqual(m['state'], 'APPROACHING')
        self.assertEqual(m['distance_to_protection'], '2')
        persist(self.store, 'fallback', frozen, result, at)
        self.assertEqual(self.ledger.get_entity('monitor', 'p')['reference_range_source'], 'MEDIAN_TR20')
        self.assertEqual(self.ledger.get_entity('position', 'p')['current_quantity'], '10')
        series['bars'] = series['bars'][-20:]
        m = evaluate(frozen, value, at)['monitor'][0]
        self.assertEqual(m['approach_status'], 'UNAVAILABLE')
        self.assertEqual(m['reference_range_source'], 'UNAVAILABLE')
        self.assertTrue(m['manual_required'])
        self.assertEqual(m['distance_to_protection'], '2')

    def test_raw_risk_series_is_used_without_relabeling_adjusted_screen_series(self):
        value = market(close=70)
        value['series'][0].update(symbol='005930.KS', price_basis='toss_adjusted_ohlc_no_order_authority')
        raw = market(close=100)['series'][0]
        raw.update(symbol='005930.KS', price_basis='toss_raw_ohlc_no_order_authority')
        value['risk_series'] = [raw]
        p = {**position(), 'symbol': '005930', 'source': 'synthetic'}
        observed = observation_for(p, value, '2026-09-12T00:00:00+00:00')
        self.assertEqual(observed['daily']['close'], 100)
        self.assertEqual(value['series'][0]['bars'][0]['close'], 70)
        value['risk_series'] = []
        missing = observation_for(p, value, '2026-09-12T00:00:00+00:00')
        self.assertEqual(missing['unavailable_reason'], 'PRICE_BASIS_UNAVAILABLE')
        self.assertNotIn('daily', missing)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.context = writable_store(Path(self.temp.name))
        self.store = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        self.ledger = RiskLedger(self.store)
        self.ledger.register_position({**position(), "source": "synthetic"}, request("p"), actor="user")
        self.ledger.adopt("p", adoption(), actor="user")

    def test_completed_market_breach_updates_ledger_without_execution(self):
        frozen = freeze(self.store)
        result = evaluate(frozen, market(), "2026-09-12T00:00:00+00:00")
        self.assertEqual(result["monitor"][0]["state"], "CONFIRMED_BREACH")
        saved = persist(self.store, "run", frozen, result, "2026-09-12T00:00:00+00:00")
        self.assertEqual(saved["persistence"]["state"], "APPLIED")
        self.assertTrue(self.ledger.get_entity("position", "p")["breach_unresolved"])
        self.assertEqual(self.ledger.get_entity("position", "p")["current_quantity"], "10")
        count = len(self.ledger.audit())
        again = persist(self.store, "run", frozen, result, "2026-09-12T00:00:00+00:00")
        self.assertEqual(again, saved)
        self.assertEqual(len(self.ledger.audit()), count)

    def test_concurrent_adoption_preserved_and_conflict_reported(self):
        frozen = freeze(self.store)
        result = evaluate(frozen, market(), "2026-09-12T00:00:00+00:00")
        self.ledger.adopt("p", adoption("raise", "95"), actor="user")
        saved = persist(self.store, "run", frozen, result, "2026-09-12T00:00:00+00:00")
        self.assertEqual(saved["persistence"]["state"], "VERSION_CONFLICT")
        self.assertEqual(self.ledger.get_entity("position", "p")["current_protection_price"], "95")
        self.assertIsNone(self.ledger.get_entity("monitor", "p"))

    def test_stale_bars_cannot_confirm_new_breach(self):
        value = market()
        value["sessions"]["KR"].append("2026-09-14")
        result = evaluate(freeze(self.store), value, "2026-09-15T00:00:00+00:00")
        self.assertEqual(result["monitor"][0]["state"], "DATA_UNAVAILABLE")
        self.assertFalse(result["position"][0]["breach_unresolved"])

    def test_synthetic_data_cannot_drive_real_holdings(self):
        p = position()
        observed = observation_for(p, market(), "2026-09-12T00:00:00+00:00")
        self.assertEqual(observed["unavailable_reason"], "SYNTHETIC_REAL_SOURCE_MISMATCH")
        self.assertNotIn("daily", observed)

    def test_explicit_short_week_completion(self):
        value = market(date="2026-09-10")
        value["week_last_sessions"] = {"KR": "2026-09-10"}
        p = self.ledger.get_entity("position", "p")
        obs = observation_for(p, value, "2026-09-11T00:00:00+00:00")
        self.assertTrue(obs["weekly"]["complete"])

    def test_invalid_observation_preserves_confirmed_breach_and_deduplicates(self):
        at = "2026-09-12T00:00:00+00:00"
        frozen = freeze(self.store)
        persist(self.store, "confirmed", frozen, evaluate(frozen, market(), at), at)
        frozen = freeze(self.store)
        broken = market()
        del broken["series"][0]["symbol"]
        result = evaluate(frozen, broken, at)
        status = result["monitor"][0]
        self.assertEqual(status["state"], "CONFIRMED_BREACH")
        self.assertEqual(status["risk_status"], "UNAVAILABLE")
        self.assertIsNone(status["current_downside_exposure"])
        self.assertTrue(status["manual_required"])
        self.assertTrue(status["notification_required"])
        persist(self.store, "failed-data", frozen, result, at)
        self.assertTrue(self.ledger.get_entity("position", "p")["breach_unresolved"])
        again = evaluate(freeze(self.store), broken, at)
        self.assertFalse(again["monitor"][0]["notification_required"])
        self.assertEqual(again["position"][0]["current_quantity"], "10")

    def test_invalid_observation_does_not_invent_a_breach(self):
        broken = market()
        del broken["series"][0]["symbol"]
        result = evaluate(freeze(self.store), broken, "2026-09-12T00:00:00+00:00")
        self.assertEqual(result["monitor"][0]["state"], "DATA_UNAVAILABLE")
        self.assertFalse(result["position"][0]["breach_unresolved"])


if __name__ == "__main__":
    unittest.main()
