import copy
import tempfile
import unittest
from datetime import date,timedelta
from pathlib import Path
from unittest.mock import patch

from invest_agent.trading.technical_context import build
from invest_agent.trading.market import screen
from invest_agent.trading.charts import generate


class TechnicalContextTests(unittest.TestCase):
    def fixture(self):
        days=[(date(2025,1,1)+timedelta(days=i)).isoformat() for i in range(380)]
        bars=[dict(date=d,open=100+i/10,high=102+i/10,low=99+i/10,close=101+i/10,volume=1000+i) for i,d in enumerate(days)]
        s={'symbol':'HELD','market':'US','currency':'USD','benchmark':'INDEX','bars':bars,
           'price_basis':'synthetic','raw_exchange_code':'OTC','market_cap':1,'market_cap_currency':'USD'}
        m={'source':'synthetic','series':[s], 'sessions':{'US':days},'benchmarks':{'INDEX':{'bars':bars}},
           'profile':{'US':{'rs_period':63,'ma_period':200}},'errors':[], 'fetched_at':days[-1],
           'coverage':{'scope':'configured_universe'},'held_symbols':['HELD']}
        return s,m

    def test_held_gate_rejected_still_has_complete_common_facts(self):
        s,m=self.fixture(); result=screen(m); row=result['rows'][0]
        self.assertFalse(row['universe']['universe_pass'])
        self.assertNotEqual(row['state'],'candidate')
        ctx=row['technical_context']
        self.assertEqual(ctx,build(s,m))
        self.assertEqual(set(ctx['sperandeo']['windows']),{'63','126','252'})
        self.assertEqual(ctx['taver']['ma_periods'],[50,100,200])
        self.assertEqual(ctx['relative_strength']['value'],0)
        self.assertTrue(ctx['weekly_bars'])
        self.assertTrue(all(x['value'] is not None for x in ctx['moving_averages']))
        self.assertEqual(ctx['range_52w']['state'],'available')

    def test_incomplete_bar_does_not_change_completed_facts(self):
        s,m=self.fixture(); before=build(s,m)
        s=copy.deepcopy(s); m=copy.deepcopy(m)
        day=(date.fromisoformat(s['bars'][-1]['date'])+timedelta(days=1)).isoformat()
        s['bars'].append(dict(date=day,open=1,close=2,high=9999,low=.1,volume=9999,complete=False))
        m['sessions']['US'].append(day)
        after=build(s,m)
        self.assertEqual(before,after)

    def test_missing_holding_keeps_unavailable_fields(self):
        ctx=build({'symbol':'MISSING','market':'US'}, {})
        self.assertEqual(ctx['state'],'unavailable')
        self.assertTrue(ctx['missing'])
        self.assertIn('sperandeo',ctx)
        self.assertIn('weekly_bars',ctx)

    def test_daily_and_weekly_charts_for_gate_rejected_holding(self):
        _,m=self.fixture()
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            def draw(bars,path,title,period,structure,*,chart_data=None,weekly_data=None):
                self.assertTrue(chart_data is not None or weekly_data is not None)
                path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(b'chart-fixture')
            with patch('invest_agent.trading.charts.draw',side_effect=draw):
                refs=generate(m,root,Path('charts'))
            self.assertEqual({r['timeframe'] for r in refs},{'daily','weekly'})
            self.assertTrue(all(r['sha256'] for r in refs))


if __name__=='__main__': unittest.main()
