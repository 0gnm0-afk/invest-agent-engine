import copy
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from invest_agent.research.watch_review import analyze, fixture, run


class WatchReviewTests(unittest.TestCase):
    def test_cases_explain_observation_entry_and_false_positives(self):
        snapshot = fixture()
        before = copy.deepcopy(snapshot)
        result = analyze(snapshot)
        self.assertEqual(snapshot, before)
        cases = {c['id']: c for c in result['cases']}
        self.assertIn(cases['forming']['phase'], ('watch', 'ready'))
        self.assertFalse(cases['forming']['entry']['candidate'])
        self.assertEqual(cases['entry']['phase'], 'entry')
        self.assertTrue(cases['entry']['entry']['candidate'])
        self.assertFalse(cases['market_missing']['entry']['candidate'])
        self.assertEqual(cases['market_missing']['entry']['market']['state'], 'unavailable')
        for name in ('bottom', 'pullback'):
            self.assertEqual(cases[name]['phase'], 'watch')
            self.assertFalse(cases[name]['entry']['candidate'])
            self.assertTrue(all(not s['entry_pass'] for s in cases[name]['base']['signals']))
        for name in ('slide', 'selloff'):
            self.assertEqual(cases[name]['phase'], 'excluded')
            self.assertFalse(cases[name]['base']['signals'])

    def test_live_source_and_duplicate_case_are_rejected(self):
        snapshot = fixture()
        snapshot['source'] = 'live'
        with self.assertRaisesRegex(ValueError, 'synthetic_source_required'):
            analyze(snapshot)
        snapshot = fixture()
        snapshot['cases'].append(copy.deepcopy(snapshot['cases'][0]))
        with self.assertRaisesRegex(ValueError, 'duplicate_case_identity'):
            analyze(snapshot)

    def test_network_blocked_demo_and_output_protection(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / 'demo'
            with patch.object(socket.socket, 'connect', side_effect=AssertionError('network_forbidden')):
                result = run(output)
            self.assertEqual(json.loads((output / 'review.json').read_text(encoding='utf-8')), result)
            page = (output / 'report.html').read_text(encoding='utf-8')
            self.assertIn('시장 근거 결측', page)
            self.assertIn('not_directional_range', page)
            original = {p.name: p.read_bytes() for p in output.iterdir()}
            with self.assertRaises(FileExistsError):
                run(output)
            self.assertEqual(original, {p.name: p.read_bytes() for p in output.iterdir()})
        with self.assertRaisesRegex(ValueError, 'output_must_be_outside_source_tree'):
            run(Path(__file__).resolve().parents[1] / 'generated-demo')
