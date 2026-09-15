import copy
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from invest_agent.trading.runner import PipelineRunner, writable_store


class HistoryTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.runner=PipelineRunner(self.root/'instance')
        (self.root/'config.json').write_text(json.dumps({'schema_version':1,'market_snapshot':'market.json'}),encoding='utf-8')

    def market(self,closes):
        rule={'ma_period':5,'slope_window':2,'transition_window':5,'rs_period':5,'activity_window':3,
              'min_rs':0,'min_activity_ratio':0,'min_average_value':0}
        from test_sperandeo import fixture
        bars=fixture('S2' if closes[-1]>100 else 'S0')
        shift=len(closes)-21
        for bar in bars:
            bar['date']=(datetime.fromisoformat(bar['date'])+timedelta(days=shift)).date().isoformat()
        return {'schema_version':1,'source':'synthetic','fetched_at':datetime.now(timezone.utc).isoformat(),
            'profile':{'status':'review_only','KR':rule,'US':copy.deepcopy(rule)},
            'series':[{'market':'US','symbol':'SYNTH','currency':'USD','benchmark':'INDEX','price_basis':'synthetic','bars':bars,
                       'raw_exchange_code':'NMS','market_cap':2e9,'market_cap_currency':'USD','market_cap_as_of':bars[-1]['date']}],
            'benchmarks':{},'errors':[],'sessions':{'US':[b['date'] for b in bars]},
            'coverage':{'scope':'configured_universe','requested':1,'received':1,'market_wide':False}}

    def run_day(self,market,day,stop=None):
        (self.root/'market.json').write_text(json.dumps(market),encoding='utf-8')
        with patch('invest_agent.trading.charts.generate',return_value=[]):
            return self.runner.run_morning(self.root/'config.json',day,stop)

    def market_result(self,run):
        return next(a['payload'] for a in run['artifacts'] if a['step']=='market')

    def test_new_release_and_same_day_dedup(self):
        self.run_day(self.market([100]*20),'2026-09-10')
        market=self.market([100]*20+[101])
        new=self.run_day(market,'2026-09-11')
        self.assertEqual(self.market_result(new)['screen']['rows'][0]['change'],'new')
        self.assertEqual(self.run_day(market,'2026-09-11')['run_id'],new['run_id'])
        released=self.run_day(self.market([100]*20+[101,90]),'2026-09-12')
        self.assertEqual(self.market_result(released)['screen']['rows'][0]['change'],'released')
        report=next((self.root/'instance/reports').glob('*/'+released['run_id']+'/report.md')).read_text(encoding='utf-8')
        self.assertIn('조건 해제',report)
        self.assertIn(new['run_id'],report)

    def test_missing_candidate_is_unknown_not_released(self):
        market=self.market([100]*20+[101]);self.run_day(market,'2026-09-10')
        market['series']=[];market['coverage']['received']=0
        current=self.run_day(market,'2026-09-11')
        row=self.market_result(current)['screen']['rows'][0]
        self.assertEqual(row['change'],'unknown')
        self.assertEqual(row['reason'],'previous_candidate_not_observed')
        self.assertTrue(row['previous_candidate'])
        self.assertEqual(current['state'],'partial')
        still_missing=self.run_day(market,'2026-09-12')
        self.assertEqual(self.market_result(still_missing)['screen']['rows'][0]['change'],'unknown')
        self.assertTrue(self.market_result(still_missing)['screen']['rows'][0]['previous_candidate'])

    def test_same_session_repeat_and_revision_not_new_trading_day(self):
        market=self.market([100]*20+[101]);self.run_day(market,'2026-09-10')
        repeated=self.run_day(market,'2026-09-11')
        self.assertEqual(self.market_result(repeated)['screen']['rows'][0]['change'],'unchanged')
        revised=self.run_day(self.market([100]*20+[90]),'2026-09-12')
        self.assertEqual(self.market_result(revised)['screen']['rows'][0]['change'],'revised')

    def test_changed_profile_and_older_session_are_not_comparable(self):
        self.run_day(self.market([100]*20+[101]),'2026-09-10')
        older=self.run_day(self.market([100]*20),'2026-09-11')
        self.assertEqual(self.market_result(older)['screen']['rows'][0]['change'],'unknown')
        market=self.market([100]*20+[101]);market['profile']['US']['min_rs']=0.01
        current=self.run_day(market,'2026-09-12')
        self.assertEqual(self.market_result(current)['comparison']['state'],'unavailable')

    def test_corrupt_prior_artifact_and_code_change_are_skipped(self):
        prior=self.run_day(self.market([100]*20),'2026-09-10')
        with writable_store(self.root / 'instance') as store, store.db:
            store.db.execute("UPDATE artifacts SET payload_hash='wrong' WHERE run_id=? AND step_key='market'",(prior['run_id'],))
        current=self.run_day(self.market([100]*20+[101]),'2026-09-11')
        self.assertEqual(self.market_result(current)['comparison']['skipped_invalid_artifacts'],1)
        with writable_store(self.root / 'instance') as store, store.db:
            store.db.execute("UPDATE runs SET code_version='previous-code' WHERE run_id=?",(current['run_id'],))
        later=self.run_day(self.market([100]*20+[101,102]),'2026-09-12')
        self.assertEqual(self.market_result(later)['comparison']['state'],'unavailable')

    def test_resume_uses_frozen_previous_rows_even_if_prior_changes(self):
        prior=self.run_day(self.market([100]*20),'2026-09-10')
        stopped=self.run_day(self.market([100]*20+[101]),'2026-09-11',stop='snapshot')
        with writable_store(self.root / 'instance') as store, store.db:
            store.db.execute("UPDATE artifacts SET payload_json='{}' WHERE run_id=? AND step_key='market'",(prior['run_id'],))
        (self.root/'market.json').unlink()
        with patch('invest_agent.trading.charts.generate',return_value=[]):
            resumed=self.runner.resume(stopped['run_id'])
        self.assertEqual(self.market_result(resumed)['screen']['rows'][0]['change'],'new')
