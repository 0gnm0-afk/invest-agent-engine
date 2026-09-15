import unittest
from copy import deepcopy

from invest_agent.trading.buy_policy import (
    approve_buy,
    buy_draft,
    buy_fill,
    trigger_buy,
)
from invest_agent.trading.risk_budget import (
    equity_snapshot,
    review_budget,
    risk_config,
    strategy_group,
)


def group():
    return strategy_group({"strategy_group_id": "s", "name": "swing", "included_account_ids": ["a"], "excluded_account_ids": ["isa", "life"]})


def config():
    return risk_config({"risk_budget_config_id": "budget", "strategy_group_id": "s",
                        "per_position_risk_pct": "0.015", "portfolio_open_risk_pct": "0.06"})


def snapshot(equity="100000", cash="100000"):
    return equity_snapshot(group(), {"equity_snapshot_id": "snap", "snapshot_type": "COMPLETED_SESSION",
                           "data_as_of": "2026-09-11", "expected_sessions": {"KR": "2026-09-11", "US": "2026-09-11"},
                           "accounts": [{"account_id": "a", "currency": "KRW", "net_liquidation_value": equity,
                                         "available_cash": {"KRW": cash}, "cash_complete": True, "source": "manual-test",
                                         "data_as_of": "2026-09-11", "expected_session": "2026-09-11"}]})


def plan(qty="10", **changes):
    value = {"buy_plan_id": "b", "strategy_group_id": "s", "account_alias": "a", "symbol": "TEST",
             "market": "KR", "currency": "KRW", "plan_kind": "INITIAL_ENTRY", "quantity_step": "1",
             "adopted_stop_price": "90", "trigger_mode": "DAILY_CLOSE_BREACH", "total_planned_quantity": qty,
             "execution_conditions": "user reviewed structure", "tranches": [
                 {"tranche_id": "b1", "sequence": 1, "trigger_type": "LIMIT_AT_OR_BELOW", "trigger_price": "100",
                  "risk_entry_price": "100", "planned_quantity": qty, "trigger_basis": "support"}], **changes}
    return buy_draft(value)


def position(symbol="TEST", **changes):
    return {"position_id": "p" + symbol, "strategy_group_id": "s", "account_alias": "a", "market": "KR",
            "symbol": symbol, "currency": "KRW", "current_quantity": "10", "average_cost": "80",
            "current_market_price": "100", "current_protection_price": "90", "market_data_session": "2026-09-11", **changes}


def approve(p, positions=None, plans=None, sells=None, snap=None, cfg=None):
    return approve_buy(p, group(), cfg or config(), snap or snapshot(), positions or [], plans or [], sells or [],
                       actor="user", at="2026-09-12T00:00:00+00:00")


class BudgetTests(unittest.TestCase):
    def test_missing_reservation_fx_is_unknown_not_zero_and_cannot_size_new_plan(self):
        missing = plan(buy_plan_id='usd', market='US', currency='USD', symbol='FOREIGN')
        missing['status'] = 'READY'
        known = plan(buy_plan_id='kr')
        known['status'] = 'READY'
        for state in ('READY', 'ACTIVE', 'PARTIALLY_FILLED', 'NEEDS_REAPPROVAL'):
            missing['status'] = state
            with self.subTest(state=state):
                result = review_budget(group(), config(), snapshot(), [], [missing, known], new_plan=plan(buy_plan_id='new'))
                self.assertIsNone(result['reserved_planned_risk'])
                self.assertEqual(result['known_reserved_risk_subtotal'], '100')
                self.assertEqual(result['unknown_risk_reservations'], ['usd'])
                self.assertEqual(result['reserved_cash_by_currency'], {'USD': '1000', 'KRW': '1000'})
                self.assertIsNone(result['projected_portfolio_open_risk'])
                self.assertIsNone(result['remaining_portfolio_risk_capacity'])
                self.assertIsNone(result['maximum_quantities'])
                self.assertFalse(result['approval_allowed'])
                row = next(r for r in result['instruments'] if r['symbol'] == 'FOREIGN')
                self.assertIsNone(row['reserved_planned_risk'])
                self.assertIsNone(row['projected_open_risk'])

    def test_missing_new_plan_fx_cannot_leave_existing_total_as_projected_total(self):
        result = review_budget(group(), config(), snapshot(), [position()], [],
                               new_plan=plan(currency='USD', market='US'))
        self.assertEqual(result['current_portfolio_open_risk'], '100')
        self.assertIsNone(result['projected_portfolio_open_risk'])
        self.assertIsNone(result['new_plan_nominal_risk'])
        self.assertIsNone(result['maximum_quantities'])
        self.assertIn('FX_MISSING', result['block_reasons'])

    def test_R4_01_02_only_included_accounts_and_no_nlv_double_count(self):
        raw = snapshot()
        raw["accounts"][0]["equity_components"] = {"cash": "999999"}
        raw["accounts"].append({"account_id": "isa", "currency": "KRW", "net_liquidation_value": "999999"})
        self.assertEqual(equity_snapshot(group(), raw)["strategy_equity"], "100000")
        g = group(); g["included_account_ids"].append("second")
        second = deepcopy(raw["accounts"][0]); second.update(account_id="second", net_liquidation_value="20000")
        raw["accounts"].append(second)
        self.assertEqual(equity_snapshot(g, raw)["strategy_equity"], "120000")

    def test_R4_03_04_missing_ratio_is_unconfigured_and_blocks(self):
        c = risk_config({})
        self.assertIsNone(c["per_position_risk_pct"])
        self.assertEqual(c["risk_budget_status"], "UNCONFIGURED")
        result = approve(plan(), cfg=c)
        self.assertIn("RISK_CONFIG_MISSING", result["block_reasons"])
        self.assertEqual(result["status"], "MANUAL_REQUIRED")

    def test_R4_05_06_unknown_and_breached_are_not_zero(self):
        for change in ({"current_protection_price": None}, {"current_market_price": "89"}, {"breach_unresolved": True}):
            result = review_budget(group(), config(), snapshot(), [position(**change)], [], plan(symbol="NEW"))
            self.assertIsNone(result["current_portfolio_open_risk"])
            self.assertIsNone(result["positions"][0]["position_open_risk"])
            self.assertFalse(result["approval_allowed"])

    def test_R4_07_08_09_profit_pnl_is_not_offset_against_open_risk(self):
        result = review_budget(group(), config(), snapshot(), [position(), position("OTHER", average_cost="120")], [])
        self.assertEqual(result["current_portfolio_open_risk"], "200")
        self.assertEqual(result["positions"][0]["stop_execution_pnl"], "100")
        self.assertEqual(result["positions"][1]["stop_execution_pnl"], "-300")

    def test_R4_10_reservations_including_reapproval(self):
        p = approve(plan())
        for state in ("READY", "ACTIVE", "PARTIALLY_FILLED", "NEEDS_REAPPROVAL"):
            p["status"] = state
            result = review_budget(group(), config(), snapshot(), [], [p])
            self.assertEqual(result["reserved_planned_risk"], "100")
        for state in ("DRAFT", "CANCELLED", "COMPLETED"):
            p["status"] = state
            self.assertEqual(review_budget(group(), config(), snapshot(), [], [p])["reserved_planned_risk"], "0")

    def test_R4_15_16_fx_and_staleness_block(self):
        p = position(currency="USD")
        result = review_budget(group(), config(), snapshot(), [p], [])
        self.assertFalse(result["approval_allowed"])
        self.assertIsNone(result["current_portfolio_open_risk"])
        p = position(market_data_session="2026-09-10")
        result = review_budget(group(), config(), snapshot(), [p], [])
        self.assertEqual(result["known_open_risk_subtotal"], "100")
        self.assertFalse(result["approval_allowed"])

    def test_R4_17_18_existing_over_budget_blocks_unrelated_new_risk(self):
        p = position(current_quantity="200")
        result = review_budget(group(), config(), snapshot(), [p], [], plan(symbol="NEW"))
        self.assertIn("OVER_BUDGET_REVIEW_REQUIRED", result["block_reasons"])
        self.assertEqual(result["positions"][0]["position_open_risk"], "2000")
        result = review_budget(group(), config(), snapshot(), [position(str(i), current_quantity="150") for i in range(5)], [])
        self.assertEqual(result["remaining_portfolio_risk_capacity"], "-1500.00")
        self.assertFalse(result['approval_allowed'])
        self.assertIn('OVER_BUDGET_REVIEW_REQUIRED', result['block_reasons'])

    def test_probability_and_kelly_metadata_cannot_change_nominal_sizing(self):
        expected = review_budget(group(), config(), snapshot(), [], [], plan())
        for probability in ('0.01', '0.99'):
            metadata = {'probability': probability, 'kelly_fraction': probability, 'confidence': probability}
            result = review_budget({**group(), **metadata}, {**config(), **metadata}, snapshot(), [], [],
                                   {**plan(), **metadata})
            self.assertEqual(result['maximum_quantities'], expected['maximum_quantities'])
            self.assertEqual(result['new_plan_nominal_risk'], expected['new_plan_nominal_risk'])
            self.assertEqual(result['approval_allowed'], expected['approval_allowed'])

    def test_R4_19_20_overweight_is_not_a_hardcap(self):
        p = plan("250", adopted_stop_price="99")
        result = approve(p)
        self.assertIn("OVERWEIGHT_APPROVAL_REQUIRED", result["block_reasons"])
        p.update(overweight_approval=True, overweight_reason="chosen", relative_superiority_basis="user thesis",
                 overweight_approved_at="2026-09-12T00:00:00+00:00")
        self.assertEqual(approve(p)["status"], "READY")

    def test_R4_21_costs_unset_and_maximum_lot_floor(self):
        result = review_budget(group(), config(), snapshot(cash="251"), [], [], plan())
        self.assertIsNone(result["adjusted_risk"])
        self.assertTrue(result["nominal_risk_available"])
        self.assertEqual(result["maximum_quantities"][0]["max_approvable_quantity"], "2")


class BuyTests(unittest.TestCase):
    def test_R3_01_02_before_after_first_fill(self):
        original = plan()
        changed = deepcopy(original); changed["total_planned_quantity"] = "20"; changed["tranches"][0]["planned_quantity"] = "20"
        self.assertEqual(buy_draft(changed, original)["total_planned_quantity"], "20")
        ready = approve(original)
        filled, _ = buy_fill(ready, None, "b1", {"execution_id": "e", "actual_quantity": "1", "actual_price": "100", "actual_at": "2026-09-12T00:00:00+00:00"})
        changed = deepcopy(filled); changed["total_planned_quantity"] = "20"; changed["tranches"][0]["planned_quantity"] = "20"
        with self.assertRaises(ValueError):
            buy_draft(changed, filled)

    def test_R3_03_initial_remaining_is_allowed_during_loss(self):
        ready = approve(plan())
        filled, pos = buy_fill(ready, None, "b1", {"execution_id": "e", "actual_quantity": "1", "actual_price": "100", "actual_at": "2026-09-12T00:00:00+00:00"})
        pos.update(current_market_price="95", market_data_session="2026-09-11")
        checked = approve(filled, positions=[pos])
        self.assertEqual(checked["status"], "READY")
        self.assertEqual(trigger_buy(filled, "b1", "95", checked)["tranches"][0]["status"], "ACTION_REQUIRED")

    def test_R3_04_05_06_pyramid_loss_suspends_without_override(self):
        p = plan(plan_kind="PYRAMID_ADD", position_id="pTEST", relative_superiority_basis="new structure")
        ready = approve(p, positions=[position()])
        self.assertEqual(ready["status"], "READY")
        checked = approve(ready, positions=[position(current_market_price="79")])
        self.assertIn("LOSS_POSITION_ADD_BLOCKED", checked["block_reasons"])
        self.assertEqual(trigger_buy(ready, "b1", "79", checked)["tranches"][0]["status"], "SUSPENDED")

    def test_R3_07_08_pending_plan_and_different_stop_block(self):
        initial = approve(plan())
        p = plan(buy_plan_id="add", plan_kind="PYRAMID_ADD", position_id="pTEST", relative_superiority_basis="new structure")
        self.assertIn("PRIOR_BUY_PLAN_OPEN", approve(p, positions=[position()], plans=[initial])["block_reasons"])
        p["adopted_stop_price"] = "89"
        self.assertIn("PROTECTION_MUST_BE_ADOPTED_TOGETHER", approve(p, positions=[position()])["block_reasons"])

    def test_R3_10_11_12_trigger_not_fill_partial_and_gap_suspension(self):
        p = plan()
        ready = approve(p)
        result = trigger_buy(ready, "b1", "99", ready)
        self.assertEqual(result["tranches"][0]["actual_fills"], [])
        filled, pos = buy_fill(result, None, "b1", {"execution_id": "e", "actual_quantity": "2", "actual_price": "99", "actual_at": "2026-09-12T00:00:00+00:00"})
        self.assertEqual(pos["current_quantity"], "2")
        self.assertEqual(filled["tranches"][0]["remaining_quantity"], "8")
        result = trigger_buy(ready, "b1", "105", ready)
        self.assertIn("EXECUTION_OUTSIDE_PLAN", result["block_reasons"])

    def test_R3_13_16_sixth_and_exit_conflict(self):
        self.assertIn("PORTFOLIO_POSITION_LIMIT", approve(plan(symbol="NEW"), positions=[position(str(i)) for i in range(5)])["block_reasons"])
        p = plan(plan_kind="PYRAMID_ADD", position_id="pTEST", relative_superiority_basis="thesis")
        self.assertIn("ACTIVE_EXIT_PLAN", approve(p, positions=[position()], sells=[{"position_id": "pTEST", "status": "ACTIVE"}])["block_reasons"])

    def test_R3_17_other_reservations_consume_cash(self):
        other = approve(plan(symbol="OTHER", buy_plan_id="other"))
        result = approve(plan(), plans=[other], snap=snapshot(cash="1500"))
        self.assertIn("INSUFFICIENT_CASH", result["block_reasons"])

    def test_R3_19_actual_excess_is_preserved(self):
        ready = approve(plan())
        result, pos = buy_fill(ready, None, "b1", {"execution_id": "e", "actual_quantity": "12", "actual_price": "110", "actual_at": "2026-09-12T00:00:00+00:00"})
        self.assertEqual(pos["current_quantity"], "12")
        self.assertEqual(pos["average_cost"], "110")
        self.assertIn("EXECUTION_OUTSIDE_PLAN", result["block_reasons"])

    def test_actual_unapproved_fill_does_not_adopt_draft_stop(self):
        result, pos = buy_fill(plan(), None, "b1", {"execution_id": "e", "actual_quantity": "1", "actual_price": "100", "actual_at": "2026-09-12T00:00:00+00:00"})
        self.assertEqual(pos["current_quantity"], "1")
        self.assertIsNone(pos["current_protection_price"])
        self.assertEqual(result["status"], "MANUAL_REQUIRED")


if __name__ == "__main__":
    unittest.main()
