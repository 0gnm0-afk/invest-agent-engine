import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from invest_agent.trading.risk_ledger import RiskLedger
from invest_agent.trading.risk_reporting import freeze, render
from invest_agent.trading.store import Store
from test_buy_budget import config, group, plan, snapshot
from test_risk_contract import adoption, request


class LedgerBuyTests(unittest.TestCase):
    def test_missing_protection_mode_or_tranche_basis_blocks_without_defaults(self):
        cases = [('mode-none', None, 'support', 'TRIGGER_MODE_MISSING'),
                 ('mode-invalid', 'AUTOMATIC', 'support', 'TRIGGER_MODE_MISSING'),
                 ('basis-none', 'DAILY_CLOSE_BREACH', None, 'TRIGGER_BASIS_MISSING'),
                 ('basis-empty', 'DAILY_CLOSE_BREACH', '  ', 'TRIGGER_BASIS_MISSING')]
        for ident, mode, basis, reason in cases:
            with self.subTest(case=ident):
                value = plan(buy_plan_id=ident, trigger_mode=mode)
                value['tranches'][0]['trigger_basis'] = basis
                saved = self.ledger.save_buy_plan(value, self.req('save-' + ident), actor='user')
                self.assertIn(reason, saved['block_reasons'])
                reviewed = self.ledger.approve_buy_plan(ident, self.req('approve-' + ident), actor='user')
                self.assertEqual(reviewed['status'], 'MANUAL_REQUIRED')
                self.assertFalse(reviewed['approved_by_user'])
                self.assertIn(reason, reviewed['block_reasons'])
                with self.assertRaisesRegex(ValueError, 'not active or ready'):
                    self.ledger.trigger_buy_plan(ident, 'b1', {'price': '100', 'session': '2026-09-11'},
                                                 self.req('trigger-' + ident), actor='engine')
                triggered = self.ledger.get_entity('buy_plan', ident)
                self.assertNotEqual(triggered['tranches'][0]['status'], 'ACTION_REQUIRED')
                self.assertEqual(triggered['tranches'][0]['actual_fills'], [])
                self.assertEqual(triggered.get('trigger_mode'), mode)
        self.assertEqual(self.ledger.entities('position'), [])
        self.assertEqual(self.ledger.budget_status('s')['reserved_planned_risk'], '0')

    def test_locked_revision_preserves_actual_execution_and_existing_reservation(self):
        self.ready()
        filled = self.fill(price='101')
        original_event = self.ledger.audit()[-1]
        value = {**filled, 'total_planned_quantity': '8', 'execution_deviations': [],
                 'execution_deviation': {'actual_price': 'fake'}, 'post_trade_status': 'fake'}
        value['tranches'] = [{**filled['tranches'][0], 'planned_quantity': '8'}]
        revised = self.ledger.save_buy_plan(value, self.req('revise-filled'), actor='user')
        self.assertEqual(revised['status'], 'NEEDS_REAPPROVAL')
        self.assertEqual(revised['total_remaining_quantity'], '6')
        self.assertEqual(revised['total_reserved_cash'], '800')
        self.assertEqual(revised['total_nominal_planned_risk'], '80')
        self.assertEqual(self.ledger.budget_status('s')['reserved_planned_risk'], '80')
        for field in ('execution_deviation', 'execution_deviations', 'post_trade_review', 'post_trade_status'):
            self.assertEqual(revised[field], filled[field])
        self.assertEqual(revised['tranches'][0]['actual_fills'], filled['tranches'][0]['actual_fills'])
        self.assertEqual(revised['history'][-1]['total_remaining_quantity'], '8')
        self.assertEqual(self.ledger.get_entity('position', filled['position_id'])['current_quantity'], '2')
        self.assertEqual(self.ledger.get_entity('position', filled['position_id'])['average_cost'], '101')
        report = '\n'.join(render(freeze(self.store)))
        self.assertIn('실제 체결 비교', report)
        self.assertIn('| 100 | 101 | 1 | 10 | 2 |', report)
        self.assertNotIn('fake', report)
        self.assertEqual(next(e for e in self.ledger.audit() if e['event_id'] == 'fill'), original_event)

    def test_partial_buy_cancellation_preserves_creation_and_records_cancellation_time(self):
        self.ready()
        filled = self.fill()
        self.assertEqual(self.ledger.budget_status('s')['reserved_planned_risk'], '80')
        created = filled['tranches'][0]['created_at']
        at = '2026-09-12T02:00:00+00:00'
        cancelled = self.ledger.cancel_buy('b', {**self.req('cancel-time'), 'occurred_at': at},
                                           actor='user', tranche_id='b1')
        tranche = cancelled['tranches'][0]
        self.assertEqual(tranche['created_at'], created)
        self.assertEqual(tranche['updated_at'], at)
        self.assertEqual(tranche['cancelled_at'], at)
        self.assertEqual(tranche['actual_fills'], filled['tranches'][0]['actual_fills'])
        self.assertEqual(self.ledger.get_entity('position', filled['position_id'])['current_quantity'], '2')
        self.assertEqual(cancelled['total_remaining_quantity'], '0')
        self.assertEqual(cancelled['total_reserved_cash'], '0')
        self.assertEqual(cancelled['total_nominal_planned_risk'], '0')
        self.assertEqual(self.ledger.budget_status('s')['reserved_planned_risk'], '0')
        self.assertEqual(self.ledger.get_entity('strategy_state', 's')['cash_spent_since_snapshot']['KRW'], '200')
        self.assertEqual(self.ledger.audit()[-1]['old_value']['total_remaining_quantity'], '8')

    def test_audit_uses_actual_review_snapshot_and_preserves_approval_snapshot(self):
        self.ready()
        approved = next(e for e in self.ledger.audit() if e['event_id'] == 'approve')
        self.assertEqual(approved['risk_budget_snapshot_id'], 'snap')
        newer = snapshot(equity='200000')
        newer['equity_snapshot_id'] = 'newer'
        self.ledger.save_equity('s', newer, self.req('newer'), actor='user')
        result = self.fill()
        self.assertEqual(result['risk_budget_snapshot_id'], 'snap')
        self.assertEqual(result['post_trade_review']['risk_budget_snapshot_id'], 'newer')
        event = next(e for e in self.ledger.audit() if e['event_id'] == 'fill')
        self.assertEqual(event['risk_budget_snapshot_id'], 'newer')
        self.assertEqual(next(e for e in self.ledger.audit() if e['event_id'] == 'approve'), approved)

    def test_missing_post_trade_snapshot_does_not_claim_old_approval_reference(self):
        self.ready()
        with patch.object(self.ledger, '_budget_inputs', side_effect=ValueError('missing')):
            result = self.fill()
        self.assertEqual(result['risk_budget_snapshot_id'], 'snap')
        self.assertIsNone(self.ledger.audit()[-1]['risk_budget_snapshot_id'])
        self.assertEqual(result['post_trade_status'], 'MANUAL_REQUIRED')

    def test_caller_cannot_forge_risk_snapshot_audit_reference(self):
        self.ledger.save_buy_plan(plan(), self.req('save'), actor='user')
        self.ledger.approve_buy_plan('b', {**self.req('approve'), 'risk_budget_snapshot_id': 'invented'}, actor='user')
        self.assertEqual(self.ledger.audit()[-1]['risk_budget_snapshot_id'], 'snap')

    def test_reentry_uses_new_plan_and_position_without_reversing_completed_exit(self):
        self.ready()
        filled = self.fill(quantity='10')
        old_id = filled['position_id']
        self.ledger.position_market(old_id, {'price': '100', 'session': '2026-09-11', 'source': 'test completed quote'},
                                    self.req('market-observed'), actor='engine')
        risk_before_sale = self.ledger.budget_status('s')['current_portfolio_open_risk']
        self.assertEqual(risk_before_sale, '100')
        exit_plan = {'sell_plan_id': 'exit', 'position_id': old_id, 'plan_kind': 'TREND_BREAK_EXIT',
                     'quantity_step': '1', 'initial_reduction_quantity': '5', 'final_protection_tranche_id': 'last',
                     'tranches': [{'tranche_id': 'last', 'sequence': 1, 'planned_quantity': '5',
                        'trigger_price': '90', 'trigger_direction': 'AT_OR_BELOW', 'confirmation_basis': 'DAILY_CLOSE',
                        'trigger_basis': 'reviewed support', 'all_remaining': True}]}
        self.ledger.save_sell_plan(exit_plan, self.req('exit-save'), actor='user')
        self.ledger.approve_sell_plan('exit', self.req('exit-approve'), actor='user')
        self.ledger.confirm_trend(old_id, 'CONFIRMED_BROKEN', self.req('broken'), actor='user')
        self.assertEqual(self.ledger.budget_status('s')['current_portfolio_open_risk'], risk_before_sale)
        for tranche in ('initial_reduction', 'last'):
            self.ledger.record_sell_fill('exit', tranche, {'execution_id': 'sell-' + tranche,
                'actual_quantity': '5', 'actual_price': '110', 'actual_at': '2026-09-11T07:00:00+00:00'},
                self.req('sell-' + tranche), actor='broker')
            self.assertEqual(self.ledger.budget_status('s')['current_portfolio_open_risk'],
                             '50' if tranche == 'initial_reduction' else '0')
        self.ledger.confirm_trend(old_id, 'RECOVERED', self.req('recovered'), actor='user')
        closed = self.ledger.get_entity('sell_plan', 'exit')
        self.assertEqual(closed['status'], 'COMPLETED')
        self.assertEqual(self.ledger.get_entity('position', old_id)['current_quantity'], '0')
        with self.assertRaisesRegex(ValueError, 'new position identity'):
            self.ledger.save_buy_plan(plan(buy_plan_id='reentry', plan_kind='REENTRY', position_id=old_id),
                                      self.req('bad-reentry'), actor='user')
        self.ledger.save_buy_plan(plan(buy_plan_id='reentry', plan_kind='REENTRY', position_id='new-position'),
                                  self.req('new-reentry'), actor='user')
        self.assertEqual(self.ledger.get_entity('sell_plan', 'exit'), closed)
        self.assertEqual(self.ledger.get_entity('position', old_id)['current_quantity'], '0')
        self.assertIsNone(self.ledger.get_entity('position', 'new-position'))

    def test_protection_raise_reduces_reserved_risk_without_increasing_quantity(self):
        self.ready()
        filled = self.fill()
        ident = filled['position_id']
        before = self.ledger.get_entity('buy_plan', 'b')
        self.ledger.adopt(ident, adoption('raise', price='95'), actor='user')
        after = self.ledger.get_entity('buy_plan', 'b')
        self.assertEqual(after['total_planned_quantity'], before['total_planned_quantity'])
        self.assertEqual(after['total_remaining_quantity'], before['total_remaining_quantity'])
        self.assertEqual(after['tranches'], before['tranches'])
        self.assertEqual(after['total_nominal_planned_risk'], '40')
        self.assertEqual(before['total_nominal_planned_risk'], '80')
        position = self.ledger.get_entity('position', ident)
        self.assertEqual(position['initial_stop_price'], '90')
        self.assertEqual(position['current_protection_price'], '95')
        self.assertEqual(position['current_quantity'], '2')

    def test_llm_cannot_change_plan_tranches_approval_cancellation_or_protection(self):
        self.ready()
        filled = self.fill()
        before = self.ledger.get_entity('buy_plan', 'b')
        position = self.ledger.get_entity('position', filled['position_id'])
        count = len(self.ledger.audit())
        attempts = [
            lambda: self.ledger.save_buy_plan(plan(qty='99'), self.req('llm-save'), actor='llm'),
            lambda: self.ledger.approve_buy_plan('b', self.req('llm-approve'), actor='llm'),
            lambda: self.ledger.cancel_buy('b', self.req('llm-cancel'), actor='llm', tranche_id='b1'),
            lambda: self.ledger.adopt(filled['position_id'], adoption('llm-stop', price='95'), actor='llm'),
        ]
        for action in attempts:
            with self.assertRaises(ValueError):
                action()
        self.assertEqual(self.ledger.get_entity('buy_plan', 'b'), before)
        self.assertEqual(self.ledger.get_entity('position', filled['position_id']), position)
        self.assertEqual(len(self.ledger.audit()), count)

    def test_trigger_missing_snapshot_persists_block_and_preserves_reservation(self):
        before = self.ready()
        with patch.object(self.ledger, '_budget_inputs', side_effect=ValueError('Snapshot missing')):
            result = self.ledger.trigger_buy_plan('b', 'b1', {'price': '99', 'session': '2026-09-11'},
                                                   self.req('missing-trigger'), actor='engine')
        self.assertEqual(result['status'], 'NEEDS_REAPPROVAL')
        self.assertEqual(result['tranches'][0]['status'], 'SUSPENDED')
        self.assertIn('STRATEGY_EQUITY_UNAVAILABLE', result['block_reasons'])
        self.assertEqual(result['total_reserved_cash'], before['total_reserved_cash'])
        self.assertEqual(result['total_nominal_planned_risk'], before['total_nominal_planned_risk'])
        self.assertEqual(self.ledger.entities('position'), [])
        self.assertEqual(self.ledger.audit()[-1]['event_type'], 'BUY_TRIGGER_REVIEWED')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name))
        self.addCleanup(self.store.close)
        self.ledger = RiskLedger(self.store)
        self.ledger.configure_strategy(group(), self.req("group"), actor="user")
        self.ledger.configure_risk(config(), self.req("config"), actor="user")
        self.ledger.save_equity("s", snapshot(), self.req("equity"), actor="user")

    def req(self, ident):
        return request(ident, explicit_user_confirmation=True)

    def ready(self, value=None):
        value = value or plan()
        self.ledger.save_buy_plan(value, self.req("save"), actor="user")
        return self.ledger.approve_buy_plan(value["buy_plan_id"], self.req("approve"), actor="user")

    def fill(self, quantity="2", price="100", ident="fill"):
        return self.ledger.record_buy_fill("b", "b1", {"execution_id": "e1", "actual_quantity": quantity, "actual_price": price,
                                                       "actual_at": "2026-09-11T06:00:00+00:00"}, self.req(ident), actor="broker")

    def test_ready_reserves_and_actual_fill_updates_average_inventory_cash_once(self):
        self.ready()
        self.assertEqual(self.ledger.budget_status("s")["reserved_planned_risk"], "100")
        first = self.fill()
        self.fill(ident="retry")
        pos = self.ledger.get_entity("position", first["position_id"])
        self.assertEqual(pos["current_quantity"], "2")
        self.assertEqual(pos["average_cost"], "100")
        self.assertEqual(first["total_reserved_cash"], "800")
        self.assertEqual(self.ledger.get_entity("strategy_state", "s")["cash_spent_since_snapshot"]["KRW"], "200")
        self.assertEqual(len([e for e in self.ledger.audit() if e["event_type"] == "BUY_FILL_RECORDED"]), 1)

    def test_condition_transition_is_audited_without_fill_or_repeat(self):
        ready = self.ready()
        self.assertEqual(ready['tranches'][0]['created_at'], self.req('save')['occurred_at'])
        data = {'price': '99', 'session': '2026-09-11'}
        result = self.ledger.trigger_buy_plan('b', 'b1', data, self.req('trigger'), actor='engine')
        transitions = result['tranches'][0]['state_transitions']
        self.assertEqual([t['to'] for t in transitions], ['CONDITION_MET', 'ACTION_REQUIRED'])
        self.assertTrue(all(t['occurred_at'] == self.req('trigger')['occurred_at'] for t in transitions))
        self.assertEqual(self.ledger.entities('position'), [])
        repeated = self.ledger.trigger_buy_plan('b', 'b1', data, self.req('again'), actor='engine')
        self.assertEqual(repeated['tranches'][0]['state_transitions'], transitions)
        audit = next(e for e in self.ledger.audit() if e['event_type'] == 'BUY_TRIGGER_REVIEWED')
        self.assertEqual(audit['changes'][0]['new_value']['tranches'][0]['state_transitions'], transitions)

    def test_equity_drop_rechecks_before_trigger_preserves_quantity_and_reservation(self):
        ready = self.ready()
        lower = snapshot(equity="1000")
        lower["equity_snapshot_id"] = "lower"
        self.ledger.save_equity("s", lower, self.req("lower"), actor="user")
        result = self.ledger.trigger_buy_plan("b", "b1", {"price": "99", "session": "2026-09-11"}, self.req("trigger"), actor="engine")
        self.assertEqual(result["status"], "NEEDS_REAPPROVAL")
        self.assertIn("RISK_BUDGET_EXCEEDED", result["block_reasons"])
        self.assertEqual(result["total_planned_quantity"], ready["total_planned_quantity"])
        self.assertEqual(self.ledger.budget_status("s")["reserved_planned_risk"], "100")

    def test_equity_increase_does_not_expand_approved_plan(self):
        ready = self.ready()
        higher = snapshot(equity='200000')
        higher['equity_snapshot_id'] = 'higher'
        self.ledger.save_equity('s', higher, self.req('higher'), actor='user')
        result = self.ledger.trigger_buy_plan('b', 'b1', {'price': '99', 'session': '2026-09-11'},
                                               self.req('trigger-higher'), actor='engine')
        self.assertEqual(result['total_planned_quantity'], ready['total_planned_quantity'])
        self.assertEqual(result['total_remaining_quantity'], ready['total_remaining_quantity'])
        self.assertEqual(result['tranches'][0]['planned_quantity'], '10')
        self.assertEqual(result['total_nominal_planned_risk'], '100')
        self.assertEqual(result['tranches'][0]['status'], 'ACTION_REQUIRED')
        self.assertEqual(self.ledger.entities('position'), [])

    def test_cancel_releases_reserved_cash_and_risk(self):
        self.ready()
        result = self.ledger.cancel_buy("b", self.req("cancel"), actor="user")
        self.assertEqual(result["total_reserved_cash"], "0")
        self.assertEqual(self.ledger.budget_status("s")["reserved_planned_risk"], "0")

    def test_partial_loss_initial_plan_remains_allowed_and_new_plan_add_blocked(self):
        self.ready()
        result = self.fill()
        triggered = self.ledger.trigger_buy_plan("b", "b1", {"price": "95", "session": "2026-09-11"}, self.req("trigger"), actor="engine")
        self.assertEqual(triggered["tranches"][0]["status"], "ACTION_REQUIRED")
        value = plan(buy_plan_id="add", position_id=result["position_id"], plan_kind="PYRAMID_ADD", relative_superiority_basis="new thesis")
        self.ledger.save_buy_plan(value, self.req("add"), actor="user")
        rejected = self.ledger.approve_buy_plan("add", self.req("approve-add"), actor="user")
        self.assertIn("LOSS_POSITION_ADD_BLOCKED", rejected["block_reasons"])
        self.assertIn("PRIOR_BUY_PLAN_OPEN", rejected["block_reasons"])

    def test_protection_raise_reduces_reservation_not_quantity(self):
        self.ready()
        result = self.fill()
        self.ledger.adopt(result["position_id"], self.req("raise") | {
            "price": "95", "trigger_mode": "DAILY_CLOSE_BREACH", "manual_input": True,
            "adoption_reason": "higher support", "adopted_at": "2026-09-12T00:00:00+00:00"}, actor="user")
        updated = self.ledger.get_entity("buy_plan", "b")
        self.assertEqual(updated["total_nominal_planned_risk"], "40")
        self.assertEqual(updated["total_planned_quantity"], "10")

    def test_actual_excess_is_recorded_and_post_trade_breach_reported(self):
        self.ready()
        result = self.fill(quantity="200", price="110")
        self.assertEqual(self.ledger.get_entity("position", result["position_id"])["current_quantity"], "200")
        self.assertEqual(result["post_trade_status"], "POST_TRADE_RISK_BREACH")

    def test_llm_cannot_change_strategy_risk_or_fill(self):
        with self.assertRaises(ValueError):
            self.ledger.configure_risk(config(), self.req("bad"), actor="llm")
        self.ready()
        with self.assertRaises(ValueError):
            self.ledger.record_buy_fill("b", "b1", {}, self.req("bad2"), actor="llm")
        self.assertEqual(self.ledger.entities("position"), [])

    def test_unapproved_revision_retains_old_reservation_but_approval_checks_new_risk(self):
        self.ready()
        value = plan("200")
        self.ledger.save_buy_plan(value, self.req("revise"), actor="user")
        self.assertEqual(self.ledger.budget_status("s")["reserved_planned_risk"], "100")
        rejected = self.ledger.approve_buy_plan("b", self.req("reapprove"), actor="user")
        self.assertIn("RISK_BUDGET_EXCEEDED", rejected["block_reasons"])

    def test_strategy_membership_change_invalidates_old_equity(self):
        self.ready()
        self.ledger.configure_strategy(group(), self.req("change"), actor="user")
        self.assertIn("STRATEGY_EQUITY_RECONCILIATION_REQUIRED", self.ledger.budget_status("s")["block_reasons"])

    def test_new_cash_snapshot_requires_execution_reconciliation(self):
        self.ready()
        self.fill()
        newer = snapshot(cash="99800")
        newer["equity_snapshot_id"] = "cash-new"
        self.ledger.save_equity("s", newer, self.req("cash-new"), actor="user")
        state = self.ledger.get_entity("strategy_state", "s")
        self.assertEqual(state["cash_spent_since_snapshot"]["KRW"], "200")
        self.assertTrue(state["cash_reconciliation_required"])
        confirmed = snapshot(cash="99800")
        confirmed.update(equity_snapshot_id="cash-confirmed", included_execution_ids=[{"account_alias": "a", "execution_id": "e1"}])
        self.ledger.save_equity("s", confirmed, self.req("cash-confirmed"), actor="broker")
        state = self.ledger.get_entity("strategy_state", "s")
        self.assertEqual(state["cash_spent_since_snapshot"], {})
        self.assertFalse(state["cash_reconciliation_required"])
        self.assertEqual(self.ledger._budget_inputs("s")[2]["available_cash"]["KRW"], "99800")

    def test_actual_fill_survives_missing_equity_and_cash_state(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp))
            try:
                ledger = RiskLedger(store)
                ledger.configure_strategy(group(), self.req("group"), actor="user")
                ledger.save_buy_plan(plan(), self.req("save"), actor="user")
                result = ledger.record_buy_fill("b", "b1", {
                    "execution_id": "without-snapshot", "actual_quantity": "2", "actual_price": "100",
                    "actual_at": "2026-09-11T06:00:00+00:00"}, self.req("fill"), actor="broker")
                self.assertEqual(ledger.get_entity("position", result["position_id"])["current_quantity"], "2")
                self.assertEqual(result["post_trade_status"], "MANUAL_REQUIRED")
                self.assertIn("POST_TRADE_DATA_UNAVAILABLE", result["post_trade_review"]["block_reasons"])
                self.assertIsNone(ledger.get_entity("position", result["position_id"])["current_protection_price"])
            finally:
                store.close()

    def test_legacy_unreconciled_cash_survives_repeated_snapshots(self):
        state = self.ledger.get_entity("strategy_state", "s")
        state.pop("unreconciled_buy_executions", None)
        state["cash_spent_since_snapshot"] = {"KRW": "200"}
        self.ledger._commit("strategy_state", "s", state, self.req("legacy"),
                            actor="engine", event_type="LEGACY_TEST_STATE")
        for ident in ("new-1", "new-2"):
            value = snapshot()
            value["equity_snapshot_id"] = ident
            self.ledger.save_equity("s", value, self.req(ident), actor="broker")
            saved = self.ledger.get_entity("strategy_state", "s")
            self.assertEqual(saved["cash_spent_since_snapshot"], {"KRW": "200"})
            self.assertTrue(saved["cash_reconciliation_required"])
        value.update(equity_snapshot_id="manual-reconciled", cash_reconciliation_confirmed=True)
        self.ledger.save_equity("s", value, self.req("manual-reconciled"), actor="user")
        self.assertFalse(self.ledger.get_entity("strategy_state", "s")["cash_reconciliation_required"])

    def test_legacy_pending_buy_blocks_new_policy_until_release(self):
        import json
        from datetime import datetime, timedelta, timezone

        from legacy_fixture import seed_legacy_reservation
        old = json.loads((Path(__file__).resolve().parents[1] / "examples/plan_fixture.json").read_text(encoding="utf-8"))
        old.update(account_alias="a", adopted_at=(datetime.now(timezone.utc)-timedelta(days=1)).isoformat())
        self.ledger.record(old)
        event = {"schema_version": 1, "source": "synthetic", "source_ref": "legacy-test",
                 "plan_id": old["plan_id"], "occurred_at": datetime.now(timezone.utc).isoformat(),
                 "event_id": "legacy-buy", "kind": "reserve", "tranche_id": "B1", "quantity": "5"}
        seed_legacy_reservation(self.store, event)
        rejected = self.ready()
        self.assertIn("LEGACY_BUY_RESERVATIONS_UNRECONCILED", rejected["block_reasons"])
        self.assertNotEqual(rejected["status"], "READY")
        self.ledger.record_event({**event, "event_id": "legacy-release", "kind": "release",
                                  "reservation_id": "legacy-buy"})
        approved = self.ledger.approve_buy_plan("b", self.req("after-release"), actor="user")
        self.assertEqual(approved["status"], "READY")


if __name__ == "__main__":
    unittest.main()
