import importlib.util
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('portfolio_demo', ROOT/'examples/portfolio_scenario_demo.py')
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)


class PublicPortfolioDemo(unittest.TestCase):
    def test_offline_plan_to_report_and_missing_fx(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(socket.socket, 'connect', side_effect=AssertionError('network forbidden')):
            out = Path(temp)/'result'
            result = demo.run(out)
            self.assertEqual(result['baseline']['total'], '1125000')
            self.assertEqual(result['response']['total'], '1062500.0')
            self.assertEqual(result['response']['remaining_quantities']['demo/US'], '5.0')
            self.assertEqual(result['plan_refs'][0]['mode'], 'hypothetical')
            self.assertIsNone(json.loads((out/'plan-input.json').read_text(encoding='utf-8'))['adoption'])
            partial = json.loads((out/'missing-fx-result.json').read_text(encoding='utf-8'))
            self.assertIsNone(partial['no_response']['total'])
            self.assertEqual(partial['no_response']['known_subtotal'], '23000')
            report = (out/'report.html').read_text(encoding='utf-8')
            self.assertIn('1,062,500', report)
            self.assertIn('미확정', report)
            with self.assertRaisesRegex(ValueError, 'output_must_be_new'):
                demo.run(out)

    def test_repository_output_rejected(self):
        with self.assertRaisesRegex(ValueError, 'outside_repository'):
            demo.run(ROOT/'unexpected-demo-output')


if __name__ == '__main__':
    unittest.main()
