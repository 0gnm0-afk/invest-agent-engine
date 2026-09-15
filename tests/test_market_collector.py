import copy
import sqlite3
import unittest
from unittest.mock import patch

import test_morning as fixtures
from invest_agent.trading.runner import PipelineRunner


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.MorningTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.write('screen.json', {'collector_id': 'private-test', 'collector_version': 'v1', 'profile': self.f.market['profile']})
        self.f.config.pop('market_snapshot')
        self.f.config['screen_config'] = 'screen.json'
        self.f.write('morning.json', self.f.config)
        self.calls = []
        def collector(request, now):
            with sqlite3.connect(self.f.root / 'instance/state.sqlite3') as db:
                state = db.execute("SELECT state FROM step_attempts WHERE step_key='market' ORDER BY attempt_no DESC LIMIT 1").fetchone()[0]
            self.calls.append((request, state))
            return copy.deepcopy(self.f.market)
        collector.collector_id = 'private-test'
        collector.collector_version = 'v1'
        self.collector = collector

    def start(self, collector=None, stop_after=None):
        with patch('invest_agent.trading.charts.generate', return_value=[]):
            return PipelineRunner(self.f.root/'instance', market_collector=collector).run_morning(self.f.root/'morning.json', '2026-09-12', stop_after)

    def test_collector_runs_inside_durable_market_attempt_with_holdings(self):
        run = self.start(self.collector)
        self.assertEqual(run['state'], 'partial')
        self.assertEqual(self.calls[0][1], 'running')
        self.assertEqual(self.calls[0][0]['additional_universe'][0]['symbol'], 'HOLD')

    def test_missing_collector_fails_without_artifact_and_can_resume(self):
        with patch('invest_agent.trading.market_provider.collect') as fallback:
            run = self.start()
            self.assertEqual(run['state'], 'failed')
            self.assertNotIn('market', {a['step'] for a in run['artifacts']})
            fallback.assert_not_called()
        (self.f.root/'screen.json').unlink()
        with patch('invest_agent.trading.charts.generate', return_value=[]):
            resumed = PipelineRunner(self.f.root/'instance', market_collector=self.collector).resume(run['run_id'])
        self.assertEqual(resumed['state'], 'partial')
        self.assertEqual(len(self.calls), 1)

    def test_changed_collector_version_is_not_called(self):
        self.collector.collector_version = 'v2'
        run = self.start(self.collector)
        self.assertEqual(run['state'], 'failed')
        self.assertEqual(self.calls, [])

    def test_completed_market_resumes_without_credentials_or_recollection(self):
        run = self.start(self.collector, 'market')
        self.assertEqual(run['state'], 'interrupted')
        with patch('invest_agent.trading.charts.generate', return_value=[]):
            resumed = PipelineRunner(self.f.root/'instance').resume(run['run_id'])
        self.assertEqual(resumed['state'], 'partial')
        self.assertEqual(len(self.calls), 1)


if __name__ == '__main__': unittest.main()
