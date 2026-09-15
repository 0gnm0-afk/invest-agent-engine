import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from invest_agent.trading.weekly_chart import load


class PublicBoundaryTests(unittest.TestCase):
    def test_private_chart_provider_keeps_snapshot_without_authentication(self):
        bars=[{'date':'2026-09-11','open':100,'high':101,'low':99,'close':100,'volume':1}]
        item={'market':'KR','symbol':'SYNTH','provider':'Toss private'}
        with tempfile.TemporaryDirectory() as directory, \
                patch('invest_agent.trading.market_provider.fetch_yahoo') as fetch:
            result, source=load(item,'2026-09-11',Path(directory),bars,live=True)
        self.assertEqual(result,bars)
        self.assertEqual(source['state'],'unavailable_long_history')
        fetch.assert_not_called()
