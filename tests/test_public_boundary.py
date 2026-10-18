import sys
import tempfile
from types import ModuleType
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from invest_agent.trading.weekly_chart import load


class PublicBoundaryTests(unittest.TestCase):
    def test_private_chart_provider_keeps_snapshot_without_authentication(self):
        bars=[{'date':'2026-09-11','open':100,'high':101,'low':99,'close':100,'volume':1}]
        item={'market':'KR','symbol':'SYNTH','provider':'Toss private'}
        provider=ModuleType('toss_market_client')
        provider.TossMarketClient=Mock(side_effect=AssertionError('private provider must not run'))
        candles=ModuleType('toss_candles')
        candles.daily_series=Mock(side_effect=AssertionError('private candles must not run'))
        with patch.dict(sys.modules, {'toss_market_client':provider, 'toss_candles':candles}), \
                tempfile.TemporaryDirectory() as directory, \
                patch('invest_agent.trading.market_provider.fetch_yahoo') as fetch:
            result, source=load(item,'2026-09-11',Path(directory),bars,live=True)
        self.assertEqual(result,bars)
        self.assertEqual(source['state'],'unavailable_long_history')
        fetch.assert_not_called()
        provider.TossMarketClient.assert_not_called()
        candles.daily_series.assert_not_called()
