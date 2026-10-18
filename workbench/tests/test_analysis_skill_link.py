"""Offline contracts for the qualitative skill handoff and evidence paging."""
import copy
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from urllib.parse import parse_qs, urlsplit


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from analysis_service import AnalysisService, packed
from chart_workspace import WorkspaceStore


SKILL = 'stock-qualitative-analysis'
ORIGIN = 'http://127.0.0.1:1234'


class AnalysisSkillLinkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = WorkspaceStore(self.root / 'chart-workspaces.sqlite3')
        dates = ['2026-09-21', '2026-09-22', '2026-09-23', '2026-09-24',
                 '2026-09-25', '2026-09-28', '2026-09-29', '2026-09-30']
        self.bars = [dict(date=day, open=100 + i, high=103 + i,
                          low=99 + i, close=102 + i, volume=1000 + i)
                     for i, day in enumerate(dates)]
        self.stock = dict(symbol='NVDA', market='US', currency='USD',
                          bars=self.bars, price_basis='adjusted')
        self.accounts = {'as_of': '2026-09-30', 'accounts': [], 'risk': {}}
        self.financial = {
            'state': 'saved',
            'result': {'snapshot_id': 'financial-fixture',
                       'summary': [{'value': i} for i in range(43)],
                       'sources': [{'url': f'local:fixture/{i}'} for i in range(12)]},
        }
        histories = SimpleNamespace(
            saved=SimpleNamespace(stocks={}),
            view=lambda stock, frame: copy.deepcopy({
                **stock, 'timeframe': frame, 'history': {'state': 'complete'},
            }),
        )
        financials = SimpleNamespace(read=lambda key: copy.deepcopy(self.financial))
        self.service = AnalysisService(
            self.store, histories, financials,
            lambda: ({'accounts': self.accounts, 'market': {'state': 'saved'}},
                     {'US:NVDA': self.stock}), self.root,
        )
        self.detail = self.create()
        self.case = self.detail['case']
        self.cid = self.case['id']
        self.sid = self.detail['snapshots'][0]['id']

    def create(self, **overrides):
        arguments = dict(stock_key='US:NVDA', title='성장 가설 검토')
        arguments.update(overrides)
        return self.service.dispatch('create', arguments, ORIGIN)

    def read(self, section='chart', **overrides):
        arguments = dict(analysis_key=self.cid, snapshot_id=self.sid,
                         section=section)
        arguments.update(overrides)
        return self.service.dispatch('read', arguments, ORIGIN)

    def decoded_prompt(self, detail):
        url = urlsplit(detail['links']['codex_url'])
        self.assertEqual((url.scheme, url.netloc), ('codex', 'new'))
        arguments = parse_qs(url.query)
        self.assertEqual(arguments['path'], [str(self.root)])
        self.assertEqual(arguments['prompt'], [detail['links']['prompt']])
        return arguments['prompt'][0]

    def test_new_case_deeplink_requests_skill_and_report_for_same_key(self):
        prompt = self.decoded_prompt(self.detail)
        for token in ('$' + SKILL, self.cid, 'US:NVDA', 'analysis_read',
                      'snapshot_id', 'analysis_save_record'):
            self.assertIn(token, prompt)
        self.assertEqual(self.case['qualitative_skill'], SKILL)
        self.assertEqual(self.detail['qualitative_skill'], SKILL)
        self.assertEqual(self.detail['qualitative_state'], 'ready')
        self.assertEqual(self.service.case(self.cid)['qualitative_skill'], SKILL)
        self.assertEqual(self.detail['items'], [])
        self.assertEqual(len(self.detail['snapshots']), 1)

    def test_validation_case_does_not_request_real_analysis_or_report(self):
        detail = self.create(test_record=True)
        prompt = self.decoded_prompt(detail)
        self.assertTrue(detail['case']['test_record'])
        self.assertIn(detail['case']['id'], prompt)
        self.assertIn('검증', prompt)
        self.assertIn('analysis_read', prompt)
        self.assertNotIn('$' + SKILL, prompt)
        self.assertNotIn('analysis_save_record', prompt)
        self.assertEqual(detail['items'], [])

    def test_copy_keeps_existing_chart_recheck_role(self):
        links = self.detail['links']
        copied = links['copy_text']
        self.assertIn(self.cid, copied)
        self.assertIn('이 분석건의 현재 차트를 다시 봐줘.', copied)
        self.assertIn(links['dashboard_url'], copied)
        self.assertIn(links['reopen_command'], copied)
        self.assertNotIn('$' + SKILL, copied)
        self.assertNotIn('analysis_save_record', copied)
        self.assertNotIn('token', copied)

    def test_legacy_case_is_available_without_rewriting_saved_case(self):
        legacy = {**self.case, 'qualitative_skill': None}
        serialized = packed(legacy)
        with closing(self.store.connect()) as db, db:
            db.execute('UPDATE analysis_cases SET payload=? WHERE id=?',
                       (serialized, self.cid))
        snapshot_before = self.service.snapshot(self.cid, self.sid)
        detail = self.service.dispatch('detail', {'analysis_key': self.cid}, ORIGIN)
        self.assertIsNone(detail['case']['qualitative_skill'])
        self.assertEqual(detail['qualitative_skill'], SKILL)
        self.assertEqual(detail['qualitative_state'], 'ready')
        self.assertIn('$' + SKILL, self.decoded_prompt(detail))
        with closing(self.store.connect()) as db:
            saved = db.execute('SELECT payload FROM analysis_cases WHERE id=?',
                               (self.cid,)).fetchone()[0]
        self.assertEqual(saved, serialized)
        self.assertEqual(self.service.snapshot(self.cid, self.sid), snapshot_before)

    def test_chart_pages_reconstruct_exact_saved_bars(self):
        expected = self.service.snapshot(self.cid, self.sid)['chart']['bars']
        collected = []
        for offset, next_offset in [(0, 3), (3, 6), (6, None)]:
            with self.subTest(offset=offset):
                page = self.read(offset=offset, limit=3)
                self.assertEqual(page['offset'], offset)
                self.assertEqual(page['next_offset'], next_offset)
                self.assertEqual(page['total'], len(expected))
                self.assertEqual(page['snapshot_id'], self.sid)
                self.assertEqual(page['chart']['bars'], expected[offset:offset + 3])
                collected.extend(page['chart']['bars'])
        self.assertEqual(collected, expected)

    def test_chart_at_end_or_beyond_returns_empty_terminal_page(self):
        for offset in (len(self.bars), len(self.bars) + 5):
            with self.subTest(offset=offset):
                page = self.read(offset=offset, limit=3)
                self.assertEqual(page['chart']['bars'], [])
                self.assertEqual(page['offset'], offset)
                self.assertEqual(page['total'], len(self.bars))
                self.assertIsNone(page['next_offset'])

    def test_empty_chart_is_a_terminal_page(self):
        self.stock['bars'] = []
        detail = self.create(title='빈 가격 자료')
        sid = detail['snapshots'][0]['id']
        page = self.read(analysis_key=detail['case']['id'], snapshot_id=sid,
                         offset=0, limit=3)
        self.assertEqual(page['chart']['bars'], [])
        self.assertEqual(page['offset'], 0)
        self.assertEqual(page['total'], 0)
        self.assertIsNone(page['next_offset'])

    def test_paging_same_snapshot_does_not_mix_newer_prices_or_financials(self):
        frozen = self.service.snapshot(self.cid, self.sid)
        self.stock['bars'][-1]['close'] = 900
        self.financial['result']['summary'][0]['value'] = 999
        current = self.service.dispatch('read', {'analysis_key': self.cid}, ORIGIN)
        self.assertNotEqual(current['snapshot_id'], self.sid)
        self.assertNotEqual(current['component_hashes']['chart'],
                            frozen['component_hashes']['chart'])
        self.assertNotEqual(current['component_hashes']['financials'],
                            frozen['component_hashes']['financials'])
        page = self.read(offset=6, limit=3)
        self.assertEqual(page['chart']['bars'], frozen['chart']['bars'][6:])
        financial_page = self.read(section='financials', offset=0, limit=20)
        self.assertEqual(financial_page['financials']['result']['summary'],
                         frozen['financials']['result']['summary'][:20])
        self.assertEqual(self.service.snapshot(self.cid, self.sid), frozen)

    def test_financial_pages_keep_their_existing_complete_array_contract(self):
        for section, field, size in [('financials', 'summary', 20),
                                     ('financial_sources', 'sources', 5)]:
            with self.subTest(section=section):
                expected = self.financial['result'][field]
                items, offset = [], 0
                for _ in range(len(expected) + 1):
                    page = self.read(section=section, offset=offset, limit=200)
                    rows = page['financials']['result'][field]
                    self.assertEqual(rows, expected[offset:offset + size])
                    self.assertEqual(page['total'], len(expected))
                    items.extend(rows)
                    offset = page['next_offset']
                    if offset is None:
                        break
                else:
                    self.fail('Financial paging did not terminate')
                self.assertEqual(items, expected)

    def test_saved_report_returns_links_without_persisting_origin_in_record(self):
        args = dict(analysis_key=self.cid, snapshot_id=self.sid, kind='report',
                    title='합성 자료 검토', body='실제 투자 분석이 아닌 테스트 본문',
                    author='llm', llm_opinion='검증 의견', user_response={},
                    sources=[dict(label='합성 출처', url='local:fixture',
                                  as_of='2026-09-30')])
        saved = self.service.dispatch('record', args, ORIGIN)
        self.assertEqual(saved['snapshot_id'], self.sid)
        self.assertEqual(saved['body'], args['body'])
        self.assertEqual(saved['sources'], args['sources'])
        self.assertEqual(saved['user_response'], {})
        self.assertEqual(saved['links']['analysis_key'], self.cid)
        self.assertEqual(saved['links']['dashboard_url'],
                         self.detail['links']['dashboard_url'])
        raw_markdown = (self.root / saved['markdown_path']).read_bytes()
        self.assertEqual(hashlib.sha256(raw_markdown).hexdigest(),
                         saved['markdown_sha256'])
        self.assertIn(self.sid, raw_markdown.decode('utf-8'))
        stored = self.service.item(self.cid, saved['id'])
        self.assertNotIn('links', stored)
        self.assertEqual(stored, {key: value for key, value in saved.items()
                                  if key not in ('links', 'document')})
        history = self.service.dispatch('detail', {'analysis_key': self.cid},
                                        'http://127.0.0.1:9876')
        self.assertEqual(history['items'], [stored])
        self.assertEqual(history['qualitative_state'], 'records_available')
        self.assertEqual(history['qualitative_skill'], SKILL)
        self.assertTrue(history['links']['dashboard_url'].startswith(
            'http://127.0.0.1:9876/'))
        self.assertIn('analysis=' + self.cid, history['links']['dashboard_url'])


if __name__ == '__main__':
    unittest.main()
