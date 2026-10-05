import copy
import json
import sys
import unittest
from pathlib import Path
from invest_agent.scenarios.risk_adapter import project_plan, assemble, shared_hash, validate_input, read_plan, save_review, account_projection
from invest_agent.scenarios.portfolio_scenarios import compare
from test_risk_portfolio import fixture


def shared_fixture():
    shared = json.loads((Path(__file__).parent/'fixtures/collaboration-v1.json').read_text(encoding='utf-8'))
    # Synthetic transport envelope. This is not evidence of A's database roundtrip.
    snapshot = {'id': 'sn_synthetic', 'stock_key': 'US:ALPHA', 'chart': {'price_basis': 'raw'}}
    plan = shared['plan']
    record = {'id': 'it_synthetic', 'case_id': 'an_synthetic', 'kind': 'plan_version', 'plan_id': 'pl_synthetic',
              'plan_version': 1, 'plan': plan, 'plan_sha256': shared_hash(plan), 'snapshot_id': snapshot['id'],
              'snapshot_sha256': shared_hash(snapshot), 'identity': {'market': 'US', 'symbol': 'ALPHA',
              'stable_id': 'SYNTHETIC-US', 'currency': 'USD', 'price_basis': 'raw'}}
    return {'contract_version': 'collaboration.v1', 'analysis_key': 'an_synthetic', 'mode': 'adopted',
            'plan_record': record, 'plan_id': record['plan_id'], 'plan_version': 1, 'plan_sha256': record['plan_sha256'],
            'snapshot': snapshot, 'snapshot_id': snapshot['id'], 'snapshot_sha256': record['snapshot_sha256'],
            'adoption': {'action': 'adopt', 'plan_record_id': record['id'], 'plan_version': 1},
            'blockers': ['scenario.s1.fx_to_base', 'scenario.s1.cost', 'condition.c2.manual_assessment_required']}


def integration_fixture():
    envelope = shared_fixture()
    snapshot = fixture()['snapshot']
    snapshot['positions'][1].update(account='synthetic-us', stable_id='SYNTHETIC-US')
    snapshot['cash'][1]['account'] = 'synthetic-us'
    horizon = '2026-10-30T16:00:00+09:00'
    assumptions = {'id': 'derived-example', 'version': 1, 'horizon': horizon, 'fx_to_base': {'KRW': '1', 'USD': '1100'},
                   'cost_policy': 'excluded', 'allocation_basis': 'initial', 'path_observations': {}, 'fills': {}}
    for day in ('2026-10-06', '2026-10-30'):
        assumptions['path_observations'][day] = {'snapshot_id': 'synthetic-'+day, 'as_of': day+'T06:00:00+09:00',
          'currency': 'USD', 'frame': '1d', 'price_basis': 'raw', 'complete': True, 'session': day,
          'expected_session': day, 'max_age_seconds': 86400}
    assumptions['fills']['c1'] = {'path_date': '2026-10-06', 'at': '2026-10-06T22:30:00+09:00',
                                'assumption': '가상 체결가격 89 USD 별도 가정'}
    return envelope, snapshot, assumptions


class SharedContract(unittest.TestCase):
    def setUp(self):
        f = fixture()
        self.calculation = compare(f['snapshot'], f['scenario'])
        self.frozen = {'expected_revision': 3, 'account_snapshot': f['snapshot'], 'scenario': f['scenario']}

    def test_partial_common_plan_preserves_missing(self):
        e, s, a = integration_fixture()
        p = project_plan(e, s, scenario_id='s1', assumptions={'horizon': a['horizon']})
        self.assertEqual(p['price']['price'], '80')
        self.assertTrue(p['response_blockers'])
        self.assertIn('scenario.s1.cost', p['record_blockers'])

    def test_shared_plan_supplies_calculation_without_mutation(self):
        e, s, a = integration_fixture()
        before = copy.deepcopy((e, s, a))
        p = project_plan(e, s, scenario_id='s1', assumptions=a)
        self.assertEqual(p['blockers']+p['response_blockers'], [])
        scenario = assemble([p], a)
        scenario['prices']['demo/KR'] = {'price': '800', 'horizon': a['horizon']}
        result = compare(s, scenario)
        self.assertEqual(result['response']['total'], '1062500.0')
        self.assertEqual(result['plan_refs'][0]['record_id'], 'it_synthetic')
        self.assertEqual(before, (e, s, a))

    def test_stable_id_not_ticker_guess(self):
        e, s, a = integration_fixture()
        s['positions'][1]['stable_id'] = None
        p = project_plan(e, s, scenario_id='s1', assumptions=a)
        self.assertIsNone(p['position_id'])
        self.assertTrue(p['blockers'])

    def test_corruption_and_inactive_plan_rejected(self):
        e = shared_fixture()
        e['plan_record']['plan']['conditions'][0]['price'] = '1'
        with self.assertRaisesRegex(ValueError, 'hash'):
            validate_input(e)
        e = shared_fixture()
        e['adoption'] = None
        with self.assertRaisesRegex(ValueError, 'adoption'):
            validate_input(e)
        e['mode'] = 'hypothetical'
        validate_input(e)

    def test_read_does_not_capture_or_adopt(self):
        calls = []
        def rpc(op, args):
            calls.append((op, args))
            return shared_fixture()
        read_plan(rpc, 'an_synthetic', 'it_synthetic', mode='adopted')
        self.assertEqual([c[0] for c in calls], ['plan_input'])

    def test_risk_save_and_reread_do_not_adopt(self):
        calls = []
        def rpc(op, args):
            calls.append((op, args))
            return {'contract_version': 'collaboration.v1', 'verified': True, 'record': {'id': 'it_review'}} if op == 'risk_review_save' else {'records': [{'id': 'it_review'}]}
        save_review(rpc, shared_fixture(), self.calculation, self.frozen, title='검토', body='부분 계산')
        self.assertEqual([c[0] for c in calls], ['risk_review_save', 'collaboration_read'])
        self.assertEqual(calls[0][1]['expected_revision'], 3)

    def test_conflict_not_retried_and_unverified_save_not_success(self):
        calls = []
        def rpc(op, args):
            calls.append(op)
            raise RuntimeError('revision_conflict')
        with self.assertRaises(RuntimeError):
            save_review(rpc, shared_fixture(), self.calculation, self.frozen, title='검토', body='부분 계산')
        self.assertEqual(len(calls), 1)

    def test_save_requires_frozen_reproducible_inputs(self):
        with self.assertRaisesRegex(ValueError, 'frozen_calculation_inputs_required'):
            save_review(lambda *_: self.fail('must not save'), shared_fixture(), self.calculation,
                        {'expected_revision': 3}, title='검토', body='결과만 저장 금지')

    def test_accounts_project_native_values_not_narrative_or_converted_duplicate(self):
        bundle = {'schema_version': 1, 'source': 'synthetic', 'as_of': '2026-10-05T06:00:00+09:00',
          'max_age_hours': '24', 'base_currency': 'KRW', 'accounts_complete': True, 'fx_to_base': {'KRW': '1'},
          'accounts': [{'alias': 'a', 'holdings_complete': True, 'cash_complete': False,
           'cash': {}, 'observed_deposits': {'US': {'fc_dca': '100', 'krw_dca': '100000'}},
           'positions': [{'symbol': 'CMA', 'market': 'KR', 'instrument_class_hint': 'CMA_note_from_broker_label', 'observed_evaluation_krw': '5000'}]}]}
        projected = account_projection(bundle, evaluated_at=bundle['as_of'])
        self.assertFalse(projected['complete'])
        self.assertEqual(len(projected['cash']), 1)
        self.assertEqual(projected['positions'], [])
        self.assertEqual(projected['cash'][0]['kind'], 'cma')
        self.assertFalse(projected['cash'][0]['disjoint'])


if __name__ == '__main__': unittest.main()
