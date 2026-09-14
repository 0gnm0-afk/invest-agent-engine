import unittest

from invest_agent.trading.sell_policy import (
    adopt_trend,
    approve_sell,
    evaluate_sell,
    proposed_trend,
    sell_fill,
)


class SellPolicyTests(unittest.TestCase):
    def test_approach_uses_user_range_before_observation_and_atr_fallback(self):
        pos, plans = adopt_trend(self.position, [self.ready()], 'CONFIRMED_BROKEN', actor='user')
        observation = {'expected_session': '2026-09-11', 'atr_available': False,
                       'daily': {'complete': True, 'date': '2026-09-11', 'low': '116', 'close': '117'},
                       'bars': [{'complete': True, 'high': '118', 'low': '116', 'close': '117'} for _ in range(21)]}
        result = evaluate_sell(plans[0], pos, observation)
        self.assertEqual(result['tranches'][0]['status'], 'APPROACHING')
        self.assertEqual(result['tranches'][0]['actual_fills'], [])
        observation['bars'] = observation['bars'][-20:]
        self.assertEqual(evaluate_sell(plans[0], pos, observation)['tranches'][0]['status'], 'ARMED')
        pos['approach_range'] = '1'
        observation['approach_range'] = '100'
        self.assertEqual(evaluate_sell(plans[0], pos, observation)['tranches'][0]['status'], 'ARMED')
        pos['approach_range'] = '2'
        self.assertEqual(evaluate_sell(plans[0], pos, observation)['tranches'][0]['status'], 'APPROACHING')
        self.assertEqual(pos['current_quantity'], '10')

    def setUp(self):
        self.position = {"position_id": "p", "current_quantity": "10", "average_cost": "80"}
        self.plan = {"sell_plan_id": "exit", "position_id": "p", "plan_kind": "TREND_BREAK_EXIT",
                     "approved_by_user": True, "approved_at": "2026-09-12T00:00:00+00:00",
                     "quantity_step": "1", "initial_reduction_quantity": "2",
                     "final_protection_tranche_id": "final", "tranches": [
                         {"tranche_id": "middle", "sequence": 1, "planned_quantity": "3", "trigger_price": "115",
                          "trigger_direction": "AT_OR_BELOW", "confirmation_basis": "DAILY_CLOSE", "trigger_basis": "support"},
                         {"tranche_id": "final", "sequence": 2, "planned_quantity": "5", "trigger_price": "110",
                          "trigger_direction": "AT_OR_BELOW", "confirmation_basis": "DAILY_CLOSE",
                          "trigger_basis": "final support", "all_remaining": True}]}

    def ready(self):
        return approve_sell(self.plan, self.position, actor="user")

    def test_weakening_does_not_activate_and_candidate_cannot_confirm(self):
        pos, plans = adopt_trend(self.position, [self.ready()], "WEAKENING", actor="user")
        self.assertEqual(plans[0]["status"], "READY")
        self.assertEqual(plans[0]["initial_reduction"]["status"], "PLANNED")
        with self.assertRaises(ValueError):
            adopt_trend(pos, plans, "CONFIRMED_BROKEN", actor="llm")
        with self.assertRaises(ValueError):
            proposed_trend({"proposed_state": "CONFIRMED_BROKEN"})

    def test_confirm_without_plan_is_manual_and_valid_plan_only_marks_action(self):
        pos, _ = adopt_trend(self.position, [], "CONFIRMED_BROKEN", actor="user")
        self.assertEqual(pos["sell_plan_status"], "MANUAL_REQUIRED")
        pos, plans = adopt_trend(self.position, [self.ready()], "CONFIRMED_BROKEN", actor="user")
        self.assertEqual(pos["current_quantity"], "10")
        self.assertEqual(plans[0]["initial_reduction"]["status"], "ACTION_REQUIRED")

    def test_quantity_mismatch_blocks_approval(self):
        self.plan["initial_reduction_quantity"] = "3"
        with self.assertRaises(ValueError):
            self.ready()

    def test_gap_final_protection_targets_all_without_fill(self):
        pos, plans = adopt_trend(self.position, [self.ready()], "CONFIRMED_BROKEN", actor="user")
        result = evaluate_sell(plans[0], pos, {"expected_session": "2026-09-11",
                              "daily": {"complete": True, "date": "2026-09-11", "low": "105", "close": "106"}})
        self.assertTrue(result["multiple_breach"])
        self.assertEqual(result["action_required_quantity"], "10")
        self.assertEqual(result["tranches"][0]["actual_fills"], [])
        self.assertEqual(pos["current_quantity"], "10")

    def test_partial_actual_fill_and_recovery_preserve_inventory(self):
        pos, plans = adopt_trend(self.position, [self.ready()], "CONFIRMED_BROKEN", actor="user")
        result, pos = sell_fill(plans[0], pos, "initial_reduction",
                               {"execution_id": "e", "actual_quantity": "1", "actual_price": "119",
                                "actual_at": "2026-09-12T00:01:00+00:00"}, actor="user")
        self.assertEqual(pos["current_quantity"], "9")
        self.assertEqual(result["initial_reduction"]["remaining_planned_quantity"], "1")
        self.assertEqual(result["initial_reduction"]["status"], "PARTIALLY_EXECUTED")
        recovered, plans = adopt_trend(pos, [result], "RECOVERED", actor="user")
        self.assertEqual(recovered["current_quantity"], "9")
        self.assertEqual(plans[0]["status"], "ACTIVE")

    def test_preconfirmation_recovery_preserves_inactive_plan(self):
        _, plans = adopt_trend(self.position, [self.ready()], "RECOVERED", actor="user")
        self.assertEqual(plans[0]["status"], "INACTIVE_RECOVERED")

    def test_weekly_confirmation_requires_matching_completed_week_date(self):
        self.plan["tranches"][0]["confirmation_basis"] = "WEEKLY_CLOSE"
        pos, plans = adopt_trend(self.position, [self.ready()], "CONFIRMED_BROKEN", actor="user")
        obs = {"expected_session": "2026-09-11", "daily": {
            "date": "2026-09-11", "complete": True, "low": "114", "close": "116"},
            "weekly": {"date": "2026-09-04", "last_session": "2026-09-11",
                       "complete": True, "close": "114"}}
        result = evaluate_sell(plans[0], pos, obs)
        self.assertEqual(result["tranches"][0]["status"], "PROVISIONAL_BREACH")
        obs["weekly"]["date"] = "2026-09-11"
        result = evaluate_sell(result, pos, obs)
        self.assertEqual(result["tranches"][0]["status"], "ACTION_REQUIRED")
        self.assertEqual([x["to"] for x in result["tranches"][0]["state_transitions"]][-2:],
                         ["CONFIRMED_BREACH", "ACTION_REQUIRED"])

    def test_fresh_observation_clears_stale_flag_preserves_partial_fill(self):
        pos, plans = adopt_trend(self.position, [self.ready()], "CONFIRMED_BROKEN", actor="user")
        plan, pos = sell_fill(plans[0], pos, "middle", {
            "execution_id": "e", "actual_quantity": "1", "actual_price": "114",
            "actual_at": "2026-09-12T00:00:00+00:00"}, actor="user")
        stale = evaluate_sell(plan, pos, {})
        self.assertEqual(stale["data_status"], "STALE_DATA")
        fresh = evaluate_sell(stale, pos, {"expected_session": "2026-09-11", "daily": {
            "date": "2026-09-11", "complete": True, "low": "113", "close": "114"}})
        self.assertEqual(fresh["data_status"], "AVAILABLE")
        self.assertEqual(fresh["tranches"][0]["status"], "PARTIALLY_EXECUTED")
        self.assertEqual(fresh["tranches"][0]["remaining_planned_quantity"], "2")
        self.assertEqual(pos["current_quantity"], "9")


if __name__ == "__main__":
    unittest.main()
