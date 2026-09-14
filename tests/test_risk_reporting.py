import json
import tempfile
import unittest
from pathlib import Path

from invest_agent.trading.protection import adopt_protection, monitor
from invest_agent.trading.risk_budget import reduction_scenarios, review_budget
from invest_agent.trading.risk_ledger import RiskLedger
from invest_agent.trading.risk_reporting import freeze, render
from invest_agent.trading.runner import PipelineRunner, writable_store
from test_buy_budget import config, group, plan, snapshot
from test_buy_budget import position as budget_position
from test_risk_contract import adoption, observation, position, request


class RiskReportingTests(unittest.TestCase):
    def test_instrument_review_shows_plan_scoped_approval_without_inheritance(self):
        approved = {**plan(), 'status': 'COMPLETED', 'approved_by_user': True, 'overweight_approval': True,
                    'overweight_reason': 'user reason', 'relative_superiority_basis': 'user comparison',
                    'overweight_approved_at': '2026-09-11T00:00:00+00:00'}
        draft = {**approved, 'buy_plan_id': 'new', 'status': 'DRAFT', 'approved_by_user': False}
        foreign = {**approved, 'buy_plan_id': 'foreign', 'strategy_group_id': 'other'}
        p = budget_position()
        budget = review_budget(group(), config(), snapshot(), [p], [approved, draft, foreign])
        row = budget['instruments'][0]
        self.assertEqual(row['overweight_approval_by_plan'], [
            {'buy_plan_id': approved['buy_plan_id'], 'status': 'COMPLETED', 'approved': True},
            {'buy_plan_id': 'new', 'status': 'DRAFT', 'approved': False}])
        self.assertFalse(row['manual_required'])
        p['current_protection_price'] = None
        budget = review_budget(group(), config(), snapshot(), [p], [approved, draft])
        self.assertTrue(budget['instruments'][0]['manual_required'])
        self.assertIn('POSITION_RISK_UNAVAILABLE', budget['instruments'][0]['review_reasons'])
        report = '\n'.join(render({'strategy': [group()], 'budgets': [budget]}))
        self.assertIn('계획별 오버웨이트 승인', report)
        self.assertIn("'buy_plan_id': 'new', 'status': 'DRAFT', 'approved': False", report)
        self.assertIn('POSITION_RISK_UNAVAILABLE', report)
        self.assertIn('새 추가매수에 적용하지 않습니다', report)

    def test_candidate_role_distance_and_giveback_are_informational(self):
        p = {**position(), 'current_market_price': '100', 'current_protection_price': '80'}
        candidates = [{'candidate_id': 'low', 'position_id': 'p', 'candidate_price': '90', 'timeframe': 'daily',
                       'recommended_role': '중간 보호선', 'basis_description': 'swing', 'counterevidence': 'recovery',
                       'data_as_of': '2026-09-11'},
                      {'candidate_id': 'high', 'position_id': 'p', 'candidate_price': '110'}]
        m = {'position_id': 'p', 'state': 'SAFE', 'data_as_of': '2026-09-11'}
        policy = {'position': [p], 'monitor': [m], 'candidate': candidates}
        report = '\n'.join(render(policy))
        self.assertIn('| low | 90 | daily | 중간 보호선 | 10 | 10 | 10.0 | swing | recovery | 2026-09-11 |', report)
        self.assertIn('| high | 110 | 미확인 | 미확인 | -10 | 0 | 0 |', report)
        self.assertEqual(p['current_protection_price'], '80')
        self.assertNotIn('adopted', candidates[0])
        m.update(state='DATA_UNAVAILABLE', missing_data=['stale'])
        report = '\n'.join(render(policy))
        self.assertIn('| low | 90 | daily | 중간 보호선 | 미확인 | 미확인 | 미확인 |', report)

    def test_reduction_scenario_does_not_claim_fresh_or_complete_risk(self):
        p = budget_position()
        p['market_data_session'] = '2026-09-01'
        budget = review_budget(group(), config(), snapshot(), [p], [])
        sell = {'sell_plan_id': 's', 'position_id': p['position_id'], 'approved_by_user': True, 'status': 'ACTIVE',
                'tranches': [{'tranche_id': 't', 'remaining_planned_quantity': '3', 'status': 'ARMED'}]}
        self.assertTrue(budget['stale_data_components'])
        scenario = reduction_scenarios(budget, [sell])[0]
        self.assertEqual(scenario['state'], 'STALE_DATA')
        self.assertIsNone(scenario['risk_reduction'])
        self.assertIsNone(scenario['position_risk_after'])
        self.assertIsNone(scenario['portfolio_risk_after_including_buy_reservations'])
        self.assertEqual(budget['positions'][0]['position_open_risk'], '100')
        budget['stale_data_components'] = []
        budget['projected_portfolio_open_risk'] = None
        scenario = reduction_scenarios(budget, [sell])[0]
        self.assertEqual(scenario['state'], 'PARTIAL')
        self.assertEqual(scenario['risk_reduction'], '30')
        self.assertIsNone(scenario['portfolio_risk_after_including_buy_reservations'])

    def test_report_priority_uses_sell_actions_trend_and_candidates(self):
        names = ['general', 'raise', 'weak', 'approach', 'missing', 'multiple', 'action', 'confirmed']
        positions = [{**position(), 'position_id': name, 'symbol': name, 'current_protection_price': '90'} for name in names]
        positions[2]['proposed_medium_trend_state'] = 'WEAKENING'
        monitors = [{'position_id': name, 'state': {'approach': 'APPROACHING', 'missing': 'DATA_UNAVAILABLE',
                     'confirmed': 'CONFIRMED_BREACH'}.get(name, 'SAFE')} for name in names]
        def sell(name, **extra):
            return {'position_id': name, 'sell_plan_id': name, 'status': 'ACTIVE', 'approved_by_user': True,
                    'plan_kind': 'TREND_BREAK_EXIT', **extra}
        sells = [sell('multiple', multiple_breach=True, breached_tranche_ids=['a', 'b'],
                      action_required_quantity='7', current_remaining_quantity='10', pending_planned_quantity='9'),
                 sell('action', initial_reduction={'status': 'ACTION_REQUIRED', 'remaining_planned_quantity': '3'}),
                 sell('general', status='CANCELLED', initial_reduction={'status': 'ACTION_REQUIRED'})]
        policy = {'position': positions, 'monitor': monitors, 'sell_plan': sells,
                  'candidate': [{'position_id': 'raise', 'candidate_id': 'r', 'candidate_price': '95'}]}
        report = '\n'.join(render(policy))
        headings = [line.split(' · ')[0].removeprefix('### ') for line in report.splitlines() if line.startswith('### ')]
        self.assertEqual(headings, ['action', 'confirmed', 'multiple', 'missing', 'approach', 'weak', 'raise', 'general'])
        self.assertIn("| True | ['a', 'b'] | 7 | 10 | 9 |", report)
        # An action stays urgent even if its input is stale or multiple lines were breached.
        sells[1]['data_status'] = 'STALE_DATA'
        sells[1]['multiple_breach'] = True
        self.assertLess('\n'.join(render(policy)).index('### action'), '\n'.join(render(policy)).index('### missing'))
        self.assertEqual(positions[0]['current_quantity'], position()['current_quantity'])

    def test_partial_sale_scenarios_preserve_actual_risk_and_buy_reservations(self):
        pending = plan()
        pending['status'] = 'READY'
        budget = review_budget(group(), config(), snapshot(), [budget_position()], [pending])
        sell = {'sell_plan_id': 'exit', 'position_id': 'pTEST', 'approved_by_user': True, 'status': 'ACTIVE',
                'tranches': [{'tranche_id': 'part', 'remaining_planned_quantity': '3', 'status': 'ARMED'},
                             {'tranche_id': 'last', 'remaining_planned_quantity': '7', 'all_remaining': True, 'status': 'ARMED'}]}
        scenarios = reduction_scenarios(budget, [sell])
        self.assertEqual(scenarios[0]['risk_reduction'], '30')
        self.assertEqual(scenarios[0]['position_risk_after'], '70')
        self.assertEqual(scenarios[0]['portfolio_risk_after_including_buy_reservations'], '170')
        self.assertEqual(scenarios[1]['quantity'], '10')
        self.assertEqual(scenarios[1]['portfolio_risk_after_including_buy_reservations'], '100')
        self.assertEqual(budget['current_portfolio_open_risk'], '100')
        self.assertEqual(budget['reserved_planned_risk'], '100')
        report = '\n'.join(render({'strategy': [group()], 'budgets': [budget], 'sell_plan': [sell]}))
        self.assertIn('| exit | part | 3 | 30 | 70 | 170 | AVAILABLE |', report)
        sell['approved_by_user'] = False
        self.assertEqual(reduction_scenarios(budget, [sell]), [])
        sell['approved_by_user'] = True
        budget['positions'][0]['position_open_risk'] = None
        self.assertIsNone(reduction_scenarios(budget, [sell])[0]['risk_reduction'])

    def test_computed_position_risk_pnl_reservations_and_weights_are_rendered(self):
        pending = plan()
        pending['status'] = 'READY'
        budget = review_budget(group(), config(), snapshot(), [budget_position()], [pending])
        row = budget['instruments'][0]
        self.assertEqual(row['current_position_weight_pct'], '0.01')
        self.assertEqual(row['projected_position_weight_pct'], '0.02')
        self.assertEqual(row['projected_open_risk'], '200')
        report = '\n'.join(render({'strategy': [group()], 'budgets': [budget]}))
        self.assertIn('| KR | TEST | 1500.000 | 100 | 100 | 200 | 0.01 | 0.02 | 0 |', report)
        self.assertIn('| KR | TEST | 10 | 100 | 80 | 90 | 100 | 100 | AVAILABLE |', report)
        self.assertIn('환율 결측', report)
        self.assertIn('지연 데이터', report)

    def test_r1_approach_warning_is_rendered_without_prediction(self):
        p = adopt_protection(position(), adoption(), actor='user')
        p['current_market_price'] = '92'
        m = {**monitor(p, observation(close='92', low='91')), 'position_id': p['position_id']}
        report = '\n'.join(render({'position': [p], 'monitor': [m]}))
        self.assertIn('APPROACHING', report)
        self.assertIn('확인할 필요', report)
        self.assertNotIn('가능성이 높', report)
        self.assertIn('현재 하방 노출', report)
        self.assertIn('보호선 체결 가정 손익', report)

    def test_account_reconciliation_errors_are_visible(self):
        value = {'strategy': [{'strategy_group_id': 's'}], 'account_reconciliation': [
            {'strategy_group_id': 's', 'state': 'partial', 'changed_positions': ['p'],
             'errors': ['HOLDINGS_INCOMPLETE:a']} ]}
        report = '\n'.join(render(value))
        self.assertIn('전략계좌 보유 대조', report)
        self.assertIn('HOLDINGS_INCOMPLETE:a', report)

    def test_snapshot_is_independent_of_later_adoptions(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with writable_store(root) as store:
                ledger = RiskLedger(store)
                ledger.register_position(position(), request("p"), actor="user")
                ledger.adopt("p", adoption(), actor="user")
                frozen = freeze(store)
                ledger.adopt("p", adoption("raise", "95"), actor="user")
                self.assertEqual(frozen["position"][0]["current_protection_price"], "90")
                self.assertEqual(freeze(store)["position"][0]["current_protection_price"], "95")
                self.assertIn("90", "\n".join(render(frozen)))

    def test_morning_resume_uses_frozen_policy_in_actual_report(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / "morning.json"
            config.write_text(json.dumps({"schema_version": 1, "backup_on_completion": False}), encoding="utf-8")
            with writable_store(root) as store:
                ledger = RiskLedger(store)
                ledger.register_position(position(), request("p"), actor="user")
                ledger.adopt("p", adoption(), actor="user")
            runner = PipelineRunner(root)
            started = runner.run_morning(config, "2026-09-12", stop_after="snapshot")
            with writable_store(root) as store:
                ledger = RiskLedger(store)
                ledger.adopt("p", adoption("raise", "95"), actor="user")
            runner.resume(started["run_id"])
            report = next((root / "reports").rglob("report.md")).read_text(encoding="utf-8")
            self.assertIn("R1~R4 사용자 채택 정책", report)
            self.assertIn("| 90 | 90 |", report)
            self.assertNotIn("| 90 | 95 |", report)


if __name__ == "__main__":
    unittest.main()
