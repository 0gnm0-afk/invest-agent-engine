import json
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import patch

from invest_agent.trading.market import screen
from invest_agent.trading.market_provider import collect
from invest_agent.trading.morning import market_component
from invest_agent.trading.runner import PipelineRunner
from invest_agent.trading.universe import discover


class IsolationTests(unittest.TestCase):
    rule: ClassVar[dict]={"ma_period":2,"slope_window":1,"transition_window":1,"rs_period":1,"activity_window":1,
          "min_rs":0,"min_activity_ratio":0,"min_average_value":0}
    config: ClassVar[dict]={"universe_mode":"exchange_listings","markets":["KR","US"],"limit_per_exchange":None,
            "profile":{"status":"review_only","KR":rule,"US":rule}}

    def listing(self,args,**kwargs):
        if args[-1]=='KRX': raise subprocess.TimeoutExpired('listing',45)
        return SimpleNamespace(returncode=0,stdout=json.dumps([{"Symbol":args[-1]+"_TEST","Name":"Synthetic Corporation"}]))

    def quote(self,symbol,market,sessions):
        return {"symbol":symbol,"market":market,"currency":"USD" if market=='US' else 'KRW',
                "price_basis":"synthetic","instrument_type":"EQUITY","bars":[
                    {"date":day,"open":100,"high":101,"low":99,"close":100,"volume":100} for day in sessions]}

    def collect(self,config=None,calendar=None):
        dates=['2026-09-07','2026-09-08','2026-09-09','2026-09-10']
        with patch('invest_agent.trading.universe.subprocess.run',side_effect=self.listing), \
             patch('invest_agent.trading.market_provider.completed_sessions',side_effect=calendar or (lambda *a:dates)), \
             patch('invest_agent.trading.market_provider.fetch_yahoo',side_effect=self.quote):
            return collect(config or self.config,datetime.now(timezone.utc))

    def test_timeout_preserves_other_exchanges(self):
        result=self.collect()
        self.assertEqual(len(result['series']),2)
        self.assertEqual(result['coverage']['listings']['KRX']['state'],'unavailable')
        self.assertFalse(result['coverage']['market_wide'])
        # Collection survives the KRX failure, but four-bar fixtures without
        # market-cap metadata cannot pass the current universe contract.
        screened=screen(result)
        self.assertEqual(screened['unavailable_count'],2)
        for row in screened['rows']:
            self.assertIn('unavailable_market_cap',row['universe']['universe_rejection_reasons'])
            self.assertIn('unavailable_insufficient_liquidity_bars',row['universe']['universe_rejection_reasons'])
        self.assertEqual(screen(result)['unavailable_scope_count'],1)

    def test_invalid_listing_is_not_an_empty_success(self):
        bad=[SimpleNamespace(returncode=1,stdout=''),SimpleNamespace(returncode=0,stdout='invalid'),
             SimpleNamespace(returncode=0,stdout='[]'),SimpleNamespace(returncode=0,stdout='[{"Name":null}]')]
        for response in bad:
            with self.subTest(response=response),patch('invest_agent.trading.universe.subprocess.run',return_value=response):
                rows,meta=discover(['KR'])
            self.assertEqual(rows,[])
            self.assertIsNone(meta['KRX']['listed'])
            self.assertEqual(meta['KRX']['state'],'unavailable')

    def test_all_listings_fail_but_held_symbol_is_still_collected(self):
        self.listing=lambda *a,**k: SimpleNamespace(returncode=1,stdout='')
        result=self.collect({**self.config,'additional_universe':[{'market':'KR','symbol':'HELD','benchmark':'INDEX'}]})
        self.assertEqual([s['symbol'] for s in result['series']],['HELD'])
        self.assertEqual(len(result['coverage']['scope_errors']),3)

    def test_all_listings_fail_report_is_unavailable_not_zero_candidates_success(self):
        self.listing=lambda *a,**k: SimpleNamespace(returncode=1,stdout='')
        result=self.collect()
        bundle={'market_input':{'state':'loaded','payload':result},'market_mode':'replay'}
        self.assertEqual(market_component(bundle)['state'],'unavailable')
        self.assertEqual(len(result['coverage']['scope_errors']),3)

    def test_calendar_failure_does_not_block_other_market(self):
        def calendar(market,*args):
            if market=='KR': raise ValueError('calendar missing')
            return ['2026-09-07','2026-09-08','2026-09-09','2026-09-10']
        config: ClassVar[dict]={**self.config,'additional_universe':[{'market':'KR','symbol':'HELD','benchmark':'INDEX'}]}
        result=self.collect(config,calendar)
        self.assertEqual(len(result['series']),2)
        self.assertEqual(result['errors'][0]['symbol'],'HELD')
        self.assertEqual(screen(result)['unavailable_scope_count'],2)

    def test_morning_report_names_missing_exchange_and_keeps_completed_market(self):
        result=self.collect()
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            (root/'market.json').write_text(json.dumps(result),encoding='utf-8')
            (root/'config.json').write_text(json.dumps({'schema_version':1,'market_snapshot':'market.json'}),encoding='utf-8')
            with patch('invest_agent.trading.charts.generate',return_value=[]):
                runner=PipelineRunner(root/'instance')
                run=runner.run_morning(root/'config.json','2026-09-11')
                standalone=runner.run_snapshot(result,{'mode':'live_public'},'2026-09-11')
            self.assertEqual(run['state'],'partial')
            self.assertEqual(standalone['state'],'partial')
            market=next(a['payload'] for a in run['artifacts'] if a['step']=='market')
            self.assertEqual(market['state'],'partial')
            self.assertEqual(len(market['screen']['rows']),2)
            report=next((root/'instance/reports').glob('*/'+run['run_id']+'/report.md')).read_text(encoding='utf-8')
            self.assertIn('탐색 범위 누락: KRX / listing',report)
            self.assertIn('후보 0건으로 해석하지 않습니다',report)
