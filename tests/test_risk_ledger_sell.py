import sqlite3
import tempfile
import unittest
from pathlib import Path

import test_sell_policy
from invest_agent.trading.risk_ledger import RiskLedger
from invest_agent.trading.store import Store
from test_risk_contract import adoption, observation, position, request


class LedgerSellTests(unittest.TestCase):
    def test_sell_and_linked_position_events_resolve_authoritative_strategy(self):
        self.ledger.save_sell_plan(self.plan, request('save-ref', strategy_group_id='forged',
                                                     adopted_candidate_id='forged'), actor='user')
        saved = self.ledger.audit()[-1]
        self.assertEqual(saved['strategy_group_id'], 'swing')
        self.assertEqual(saved['related_sell_plan_id'], 'exit')
        self.assertIsNone(saved['related_candidate_id'])
        self.ledger.approve_sell_plan('exit', request('approve-ref', explicit_user_confirmation=True), actor='user')
        self.ledger.confirm_trend('p', 'CONFIRMED_BROKEN', request('trend-ref', explicit_user_confirmation=True), actor='user')
        event = self.ledger.audit()[-1]
        self.assertEqual(event['related_sell_plan_id'], 'exit')
        self.assertEqual(event['related_sell_plan_ids'], ['exit'])
        self.fill()
        event = self.ledger.audit()[-1]
        self.assertEqual(event['strategy_group_id'], 'swing')
        self.assertEqual(event['position_id'], 'p')
        self.assertEqual(event['tranche_id'], 'initial_reduction')
        self.assertEqual(event['related_sell_plan_id'], 'exit')
        self.assertEqual(self.ledger.audit()[2], saved)

    def test_draft_creation_time_survives_approval_and_revision(self):
        created = '2026-09-12T00:00:00+00:00'
        approved = '2026-09-12T01:00:00+00:00'
        revised = '2026-09-12T02:00:00+00:00'
        draft = self.ledger.save_sell_plan(self.plan, request('draft-time', occurred_at=created), actor='user')
        self.assertEqual(draft['tranches'][0]['created_at'], created)
        self.assertEqual(draft['tranches'][0]['status'], 'PLANNED')
        ready = self.ledger.approve_sell_plan('exit', request('approve-time', occurred_at=approved,
                                             explicit_user_confirmation=True), actor='user')
        self.assertEqual(ready['tranches'][0]['created_at'], created)
        self.assertEqual(ready['tranches'][0]['updated_at'], approved)
        result = self.ledger.save_sell_plan(self.plan, request('revision-time', occurred_at=revised), actor='user')
        self.assertEqual(result['tranches'][0]['created_at'], created)
        self.assertEqual(result['tranches'][0]['updated_at'], revised)
        self.assertEqual(result['history'][-1]['tranches'][0]['updated_at'], approved)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name))
        self.addCleanup(self.store.close)
        self.ledger = RiskLedger(self.store)
        self.ledger.register_position(position(), request("p"), actor="user")
        self.ledger.adopt("p", adoption(), actor="user")
        fixture = test_sell_policy.SellPolicyTests()
        fixture.setUp()
        self.plan = fixture.plan

    def ready(self):
        self.ledger.save_sell_plan(self.plan, request("save"), actor="user")
        return self.ledger.approve_sell_plan("exit", request("approve", explicit_user_confirmation=True), actor="user")

    def active(self):
        self.ready()
        self.ledger.confirm_trend("p", "CONFIRMED_BROKEN", request("break", explicit_user_confirmation=True), actor="user")

    def fill(self, ident="fill", **kwargs):
        value = {"execution_id": "exec1", "actual_quantity": "1", "actual_price": "119",
                 "actual_at": "2026-09-12T00:01:00+00:00", **kwargs}
        return self.ledger.record_sell_fill("exit", "initial_reduction", value, request(ident), actor="user")

    def test_atomic_fill_persisted_and_execution_idempotent_across_restart(self):
        self.active()
        self.fill()
        self.assertEqual(self.ledger.get_entity("position", "p")["current_quantity"], "9")
        result = self.fill("retry")
        self.assertEqual(result["initial_reduction"]["remaining_planned_quantity"], "1")
        with self.assertRaisesRegex(ValueError, "different fill"):
            self.fill("changed", actual_quantity="2")
        self.assertEqual(len([e for e in self.ledger.audit() if e["event_type"] == "SELL_FILL_RECORDED"]), 1)
        # A fresh ledger instance must use the same persisted execution key.
        fresh = RiskLedger(self.store)
        self.assertEqual(fresh.get_entity("position", "p")["current_quantity"], "9")

    def test_confirmation_intermediate_states_are_atomic_audited_and_not_repeated(self):
        self.active()
        at = "2026-09-12T01:00:00+00:00"
        result = self.ledger.observe_sell("exit", observation(close="114", low="113"),
                                          request("observe", occurred_at=at))
        tranche = result["tranches"][0]
        transitions = tranche["state_transitions"]
        self.assertEqual([t["to"] for t in transitions][-2:], ["CONFIRMED_BREACH", "ACTION_REQUIRED"])
        self.assertTrue(all(t["occurred_at"] == at for t in transitions[-2:]))
        self.assertEqual(tranche["updated_at"], at)
        repeated = self.ledger.observe_sell("exit", observation(close="114", low="113"), request("repeat"))
        self.assertEqual(repeated["tranches"][0]["state_transitions"], transitions)
        event = next(e for e in self.ledger.audit() if e["event_id"] == "observe")
        self.assertEqual(event["new_value"]["tranches"][0]["state_transitions"], transitions)
        self.assertEqual(tranche["actual_fills"], [])
        self.assertEqual(self.ledger.get_entity("position", "p")["current_quantity"], "10")

    def test_manual_reconfirmation_preserves_partial_execution(self):
        self.active()
        self.ledger.record_sell_fill("exit", "middle", {
            "execution_id": "middle-fill", "actual_quantity": "1", "actual_price": "114",
            "actual_at": "2026-09-12T00:01:00+00:00"}, request("middle-fill"), actor="user")
        result = self.ledger.confirm_sell_tranche("exit", "middle",
            request("manual", explicit_user_confirmation=True), actor="user")
        self.assertEqual(result["tranches"][0]["status"], "PARTIALLY_EXECUTED")
        self.assertEqual(result["tranches"][0]["remaining_planned_quantity"], "2")
        self.assertEqual(len(result["tranches"][0]["actual_fills"]), 1)

    def test_failed_second_projection_rolls_back_fill_and_audit(self):
        self.active()
        count = len(self.ledger.audit())
        self.store.db.executescript("""CREATE TRIGGER reject_position BEFORE UPDATE ON risk_entities
          WHEN NEW.kind='position' BEGIN SELECT RAISE(ABORT, 'injected'); END;""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.fill()
        self.assertEqual(len(self.ledger.audit()), count)
        self.assertEqual(self.ledger.get_entity("sell_plan", "exit")["initial_reduction"]["actual_fills"], [])
        self.assertEqual(self.ledger.get_entity("position", "p")["current_quantity"], "10")

    def test_protection_conflict_marks_reapproval_and_retains_old_prices(self):
        self.active()
        self.ledger.adopt("p", adoption("raise", "112"), actor="user")
        plan = self.ledger.get_entity("sell_plan", "exit")
        self.assertEqual(plan["status"], "NEEDS_REAPPROVAL")
        self.assertEqual(plan["tranches"][-1]["trigger_price"], "110")
        self.assertEqual(self.ledger.get_entity("position", "p")["initial_stop_price"], "90")
        self.assertEqual(len(self.ledger.audit()[-1]["changes"]), 2)

    def test_raise_past_proactive_tranche_requires_reapproval_without_repricing(self):
        self.plan.update(plan_kind='PROACTIVE_PROFIT_TAKE', initial_reduction_quantity='0')
        self.plan['tranches'] = [self.plan['tranches'][0]]
        self.plan.pop('final_protection_tranche_id')
        self.ready()
        self.ledger.activate_proactive('exit', request('activate', explicit_user_confirmation=True), actor='user')
        before = self.ledger.get_entity('sell_plan', 'exit')
        self.ledger.adopt('p', adoption('raise-mid', '116'), actor='user')
        after = self.ledger.get_entity('sell_plan', 'exit')
        self.assertEqual(after['status'], 'NEEDS_REAPPROVAL')
        self.assertEqual(after['tranches'], before['tranches'])
        self.assertEqual(self.ledger.get_entity('position', 'p')['current_quantity'], '10')

    def test_observation_does_not_repeat_protection_adoption_alert(self):
        first = self.ledger.observe("p", observation(), request("o1"))
        second = self.ledger.observe("p", observation(), request("o2"))
        self.assertTrue(first["notification_required"])
        self.assertFalse(second["notification_required"])

    def test_manual_confirmation_and_resolution_persist_but_do_not_sell(self):
        self.ledger.confirm_breach("p", request("manual", explicit_user_confirmation=True), actor="user")
        self.assertEqual(self.ledger.observe("p", observation(), request("o1"))["state"], "CONFIRMED_BREACH")
        self.ledger.resolve_breach("p", request("resolve", explicit_user_confirmation=True), actor="user")
        self.assertEqual(self.ledger.observe("p", observation(), request("o2"))["state"], "SAFE")
        self.assertEqual(self.ledger.get_entity("position", "p")["current_quantity"], "10")

    def test_cancel_remaining_preserves_actual_fill_and_requires_reapproval(self):
        self.active()
        self.fill()
        result = self.ledger.cancel_sell("exit", request("cancel", explicit_user_confirmation=True), actor="user", tranche_id="middle")
        self.assertEqual(result["status"], "NEEDS_REAPPROVAL")
        self.assertEqual(result["initial_reduction"]["actual_fills"][0]["actual_quantity"], "1")
        self.assertEqual(self.ledger.get_entity("position", "p")["current_quantity"], "9")

    def test_llm_plan_or_fill_cannot_change_positions(self):
        with self.assertRaises(ValueError):
            self.ledger.save_sell_plan(self.plan, request("llm"), actor="llm")
        self.active()
        with self.assertRaises(ValueError):
            self.ledger.record_sell_fill("exit", "initial_reduction", {}, request("fill"), actor="llm")
        self.assertEqual(self.ledger.get_entity("position", "p")["current_quantity"], "10")

    def test_gap_observation_does_not_create_fills(self):
        self.active()
        result = self.ledger.observe_sell("exit", observation(close="106", low="105"), request("gap"))
        self.assertEqual(result["action_required_quantity"], "10")
        self.assertTrue(result["multiple_breach"])
        self.assertEqual(self.ledger.get_entity("position", "p")["current_quantity"], "10")
        self.assertTrue(all(t["actual_fills"] == [] for t in result["tranches"]))

    def test_external_quantity_change_requires_reapproval_without_rewriting_plan(self):
        self.ready()
        self.ledger.reconcile_position("p", {"current_quantity": "12", "average_cost": "85", "source_ref": "broker-snapshot"},
                                       request("sync"), actor="broker")
        plan = self.ledger.get_entity("sell_plan", "exit")
        self.assertEqual(plan["status"], "NEEDS_REAPPROVAL")
        self.assertEqual(plan["position_quantity_at_approval"], "10")
        with self.assertRaises(ValueError):
            self.ledger.approve_sell_plan("exit", request("approve2", explicit_user_confirmation=True), actor="user")

    def test_revision_preserves_old_actual_fills_in_history(self):
        self.active()
        self.fill()
        self.plan["initial_reduction_quantity"] = "1"
        self.ledger.save_sell_plan(self.plan, request("revise"), actor="user")
        result = self.ledger.approve_sell_plan("exit", request("approve2", explicit_user_confirmation=True), actor="user")
        self.assertEqual(result["position_quantity_at_approval"], "9")
        self.assertEqual(result["history"][-1]["initial_reduction"]["actual_fills"][0]["actual_quantity"], "1")

    def test_proactive_activation_and_trend_break_priority(self):
        self.plan.update(plan_kind="PROACTIVE_PROFIT_TAKE", initial_reduction_quantity="0")
        self.ready()
        active = self.ledger.activate_proactive("exit", request("activate", explicit_user_confirmation=True), actor="user")
        self.assertEqual(active["status"], "ACTIVE")
        self.ledger.confirm_trend("p", "CONFIRMED_BROKEN", request("break", explicit_user_confirmation=True), actor="user")
        self.assertEqual(self.ledger.get_entity("sell_plan", "exit")["status"], "PAUSED")

    def test_manual_tranche_and_cancellation_do_not_fill(self):
        self.plan["tranches"][0]["confirmation_basis"] = "MANUAL_CONFIRMATION"
        self.active()
        manual = self.ledger.confirm_sell_tranche("exit", "middle", request("manual", explicit_user_confirmation=True), actor="user")
        self.assertEqual(manual["tranches"][0]["status"], "ACTION_REQUIRED")
        observed = self.ledger.observe_sell("exit", observation(), request("obs"))
        self.assertIn("middle", observed["breached_tranche_ids"])
        self.ledger.cancel_sell("exit", request("cancel", explicit_user_confirmation=True), actor="user")
        self.assertEqual(self.ledger.get_entity("position", "p")["current_quantity"], "10")


if __name__ == "__main__":
    unittest.main()
