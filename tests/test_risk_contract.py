import sqlite3
import tempfile
import unittest
from contextlib import closing
from copy import deepcopy
from pathlib import Path

import test_plans
from invest_agent.trading.backup import create, restore, verify
from invest_agent.trading.plans import PlanLedger
from invest_agent.trading.protection import adopt_protection, monitor, normal_range
from invest_agent.trading.risk_ledger import RiskLedger
from invest_agent.trading.store import Store


def request(ident, **values):
    return {"event_id": ident, "reason": "user test decision", "occurred_at": "2026-09-12T00:00:00+00:00",
            "data_as_of": "2026-09-11", **values}


def position():
    return {"position_id": "p", "strategy_group_id": "swing", "account_alias": "test",
            "market": "KR", "symbol": "TEST", "currency": "KRW",
            "current_quantity": "10", "average_cost": "80"}


def adoption(ident="adopt", price="90", mode="DAILY_CLOSE_BREACH", **values):
    return request(ident, price=price, trigger_mode=mode, manual_input=True,
                   explicit_user_confirmation=True, adoption_reason="structure invalidation",
                   adopted_at="2026-09-12T00:00:00+00:00", **values)


def observation(close="100", low="99", **values):
    return {"daily": {"date": "2026-09-11", "close": close, "low": low, "complete": True},
            "expected_session": "2026-09-11", "approach_range": "2", **values}


class R1Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name))
        self.addCleanup(self.store.close)
        self.ledger = RiskLedger(self.store)
        self.ledger.register_position(position(), request("position"), actor="user")

    def test_R1_01_unset_is_unknown(self):
        result = monitor(position(), observation())
        self.assertEqual(result["state"], "UNSET")
        self.assertIsNone(result["current_downside_exposure"])
        self.assertTrue(result["manual_required"])

    def test_R1_02_candidate_does_not_adopt(self):
        candidate = {"candidate_id": "c", "position_id": "p", "candidate_type": "SWING_LOW",
                     "timeframe": "daily", "candidate_price": "90", "basis_description": "low",
                     "anchor_points_or_source_levels": [90], "invalidation_rationale": "break",
                     "counterevidence": [], "suggested_trigger_mode": "DAILY_CLOSE_BREACH",
                     "data_as_of": "2026-09-11", "missing_data": []}
        self.ledger.candidate(candidate, request("candidate"), actor="llm")
        event = self.ledger.audit()[-1]
        self.assertEqual(event['strategy_group_id'], 'swing')
        self.assertEqual(event['related_candidate_id'], 'c')
        self.ledger.candidate({**candidate, 'candidate_id': 'c2', 'strategy_group_id': 'forged'},
                              request('candidate2', adopted_candidate_id='forged'), actor='llm')
        self.assertEqual(self.ledger.audit()[-1]['strategy_group_id'], 'swing')
        self.assertEqual(self.ledger.audit()[-1]['related_candidate_id'], 'c2')
        self.assertIsNone(self.ledger.get_entity("position", "p")["current_protection_price"])
        with self.assertRaises(ValueError):
            self.ledger.adopt("p", adoption(), actor="llm")
        with self.assertRaises(ValueError):
            self.ledger.candidate({**candidate, "candidate_price": "recent low"}, request("bad"), actor="llm")
        self.ledger.adopt('p', adoption('from-candidate', adopted_candidate_id='c'), actor='user')
        self.assertEqual(self.ledger.audit()[-1]['related_candidate_id'], 'c')

    def test_R1_03_07_initial_stop_preserved(self):
        p = self.ledger.adopt("p", adoption(), actor="user")
        self.assertEqual(p["initial_stop_price"], p["current_protection_price"])
        p = self.ledger.adopt("p", adoption("raise", "95"), actor="user")
        self.assertEqual(p["initial_stop_price"], "90")
        self.assertEqual(p["current_protection_price"], "95")
        self.assertEqual(self.ledger.audit()[-1]["old_value"]["current_protection_price"], "90")

    def test_R1_04_05_provisional_and_close(self):
        p = adopt_protection(position(), adoption(), actor="user")
        self.assertEqual(monitor(p, observation(low="89"))["state"], "PROVISIONAL_BREACH")
        self.assertEqual(monitor(p, observation(close="90", low="89"))["state"], "CONFIRMED_BREACH")

    def test_R1_06_approach_message_and_missing_range(self):
        p = adopt_protection(position(), adoption(), actor="user")
        result = monitor(p, observation(close="92", low="91"))
        self.assertEqual(result["state"], "APPROACHING")
        self.assertIn("확인할 필요", result["message"])
        missing = observation(); del missing["approach_range"]
        result = monitor(p, missing)
        self.assertEqual(result["approach_status"], "UNAVAILABLE")
        self.assertEqual(result["distance_to_protection"], "10")
        self.assertTrue(result["manual_required"])

    def test_R1_08_downward_override(self):
        self.ledger.adopt("p", adoption(), actor="user")
        with self.assertRaises(ValueError):
            self.ledger.adopt("p", adoption("down", "85"), actor="user")
        p = self.ledger.adopt("p", adoption("down", "85", risk_expansion_override=True,
                                             override_reason="explicit expanded risk"), actor="user")
        self.assertEqual(p["initial_stop_price"], "90")
        self.assertEqual(self.ledger.audit()[-1]["event_type"], "RISK_EXPANSION_OVERRIDE")

    def test_R1_09_10_no_realtime_claim_or_repeated_notification(self):
        p = self.ledger.adopt("p", adoption(mode="INTRADAY_TOUCH"), actor="user")
        obs = observation(low="89")
        first = monitor(p, obs)
        self.assertFalse(first["realtime_available"])
        self.assertTrue(first["notification_required"])
        repeated = monitor(p, obs, previous=first)
        self.assertFalse(repeated["notification_required"])
        recovered_price = monitor(p, observation(), previous=repeated)
        self.assertEqual(recovered_price["state"], "CONFIRMED_BREACH")
        self.assertFalse(recovered_price["notification_required"])
        new_day = deepcopy(obs)
        new_day["expected_session"] = new_day["daily"]["date"] = "2026-09-14"
        self.assertTrue(monitor(p, new_day, previous=repeated)["notification_required"])

    def test_weekly_manual_and_stale(self):
        p = adopt_protection(position(), adoption(mode="WEEKLY_CLOSE_BREACH"), actor="user")
        obs = observation(close="89", low="88")
        self.assertEqual(monitor(p, obs)["state"], "PROVISIONAL_BREACH")
        # Caller calendar supplies actual final session, including holiday-short weeks.
        obs["weekly"] = {"date": "2026-09-11", "last_session": "2026-09-11", "complete": True, "close": "89"}
        self.assertEqual(monitor(p, obs)["state"], "CONFIRMED_BREACH")
        obs["expected_session"] = "2026-09-14"
        self.assertEqual(monitor(p, obs)["state"], "DATA_UNAVAILABLE")
        p["trigger_mode"] = "MANUAL_CONFIRMATION"
        self.assertEqual(monitor(p, observation(low="89"))["state"], "REVIEW_REQUIRED")

    def test_atomic_append_only_audit_and_idempotency(self):
        self.ledger.adopt("p", adoption(), actor="user")
        self.ledger.adopt("p", adoption(), actor="user")
        self.assertEqual(len(self.ledger.audit()), 2)
        with self.assertRaises(ValueError):
            self.ledger.adopt("p", adoption(price="91"), actor="user")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.db.execute("DELETE FROM risk_events")
        self.store.db.rollback()
        self.assertEqual(len(self.ledger.audit()), 2)

    def test_atr_is_decimal_and_uses_completed_bars(self):
        bars = [{"high": "101", "low": "99", "close": "100", "complete": True} for _ in range(21)]
        value, source = normal_range(bars)
        self.assertEqual(str(value), "2")
        self.assertEqual(source, "ATR14_WILDER")
        self.assertEqual(normal_range(bars, atr_available=False)[1], "MEDIAN_TR20")

    def test_adoption_cannot_overwrite_inventory_or_initial_record(self):
        p = self.ledger.adopt("p", adoption(), actor="user")
        revised = adopt_protection(p, adoption("raise", "95", current_quantity="999",
                                              initial_stop_price="1"), actor="user")
        self.assertEqual(revised["current_quantity"], "10")
        self.assertEqual(revised["initial_stop_price"], "90")


class RiskMigrationTests(unittest.TestCase):
    def test_v2_plan_fill_reservation_and_backup_restore_preserve_exact_records(self):
        fixture = test_plans.PlanTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.legacy_reserve()
        original_fill = fixture.fill()
        fixture.call('record_event', original_fill)
        expected_status = fixture.status()
        self.assertEqual(expected_status['inventory_from_records'], '2')
        self.assertEqual(expected_status['tranches'][0]['reserved_quantity'], '3')
        def records(db):
            return {table: [tuple(row) for row in db.execute(f'SELECT * FROM {table} ORDER BY rowid')]
                    for table in ('adoptions', 'plan_events')}
        store = Store(fixture.root)
        expected_records = records(store.db)
        # Represent the actual v2 layout: legacy tables remain, no risk tables.
        store.db.executescript('DROP TABLE risk_events; DROP TABLE risk_entities; PRAGMA user_version=2;')
        store.close()
        store = Store(fixture.root)
        self.assertEqual(store.db.execute('PRAGMA user_version').fetchone()[0], 3)
        self.assertEqual(records(store.db), expected_records)
        self.assertEqual(PlanLedger(store).status(fixture.plan['plan_id']), expected_status)
        self.assertEqual(RiskLedger(store).entities('position'), [])
        self.assertEqual(RiskLedger(store).entities('buy_plan'), [])
        self.assertEqual(RiskLedger(store).audit(), [])
        store.close()
        pre_migration = next((fixture.root / 'backups').glob('state-before-v3-*.sqlite3'))
        with closing(sqlite3.connect(pre_migration)) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 2)
            self.assertEqual(records(db), expected_records)
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        archive = create(fixture.root)['archive']
        self.assertEqual(verify(archive)['state'], 'verified')
        target = fixture.root / 'restored'
        restore(archive, target)
        restored = Store(target)
        try:
            self.assertEqual(records(restored.db), expected_records)
            ledger = PlanLedger(restored)
            self.assertEqual(ledger.status(fixture.plan['plan_id']), expected_status)
            ledger.record_event({**original_fill, 'event_id': 'retry-existing-fill'})
            self.assertEqual(records(restored.db), expected_records)
            with self.assertRaisesRegex(ValueError, 'R3_R4_REQUIRED'):
                ledger.record_event(fixture.reserve('new-reserve', tranche='B2',
                    context=fixture.context(held='2', cost='100', price='110')))
            ledger.record_event(fixture.fill(ident='historical-fill', quantity='1', execution='exec-2'))
            result = ledger.record_event(fixture.event('release', 'release-rest', reservation_id='r1', quantity='2'))
            self.assertEqual(result['inventory_from_records'], '3')
            self.assertEqual(result['tranches'][0]['reserved_quantity'], '0')
            self.assertEqual(RiskLedger(restored).entities('position'), [])
        finally:
            restored.close()

    def test_v2_backup_preserves_legacy_records_and_repeat_is_noop(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = Store(root)
            store.db.executescript("DROP TABLE risk_events; DROP TABLE risk_entities; PRAGMA user_version=2;")
            store.db.execute("INSERT INTO runs VALUES ('old','key','2026-09-11','succeeded','{}','{}','code','created','updated',NULL)")
            store.db.commit(); store.close()
            store = Store(root)
            self.assertEqual(store.db.execute("PRAGMA user_version").fetchone()[0], 3)
            self.assertEqual(store.run("old")["state"], "succeeded")
            self.assertEqual(store.db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(RiskLedger(store).entities("position"), [])
            store.close()
            backups = list((root / "backups").glob("state-before-v3-*.sqlite3"))
            self.assertEqual(len(backups), 1)
            Store(root).close()
            self.assertEqual(len(list((root / "backups").glob("*.sqlite3"))), 1)


if __name__ == "__main__":
    unittest.main()
