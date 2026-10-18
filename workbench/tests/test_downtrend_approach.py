import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import downtrend_approach as d
import priority_review
import momentum_review
import watch_review
from test_momentum_entry import fixture, series


def example():
    bars=series([80.]*270+[96.]*51)
    # Three descending pivots; the upstream regression is 102 at 320.
    for i,price in [(280,110.),(290,108.),(300,106.)]:
        bars[i].update(open=price-1,close=price-1,high=price,low=price-2)
    bars[-1].update(open=101.,close=101.5,high=101.8,low=100.5)
    return bars


class DowntrendTests(unittest.TestCase):
    def test_confirmed_line_and_exact_boundary(self):
        bars=example();r=d.analyze(bars);self.assertEqual(r['state'],'approach')
        sig=r['signals'][0];line=sig['trendline']
        self.assertEqual(line['anchor_a_date'],bars[280]['date'])
        self.assertEqual(line['anchor_b_date'],bars[300]['date'])
        self.assertAlmostEqual(line['value_latest'],102)
        self.assertLessEqual(line['gap_atr'],.5)
        self.assertFalse(sig['entry_pass']);self.assertIsNone(sig['breakout_date'])
        self.assertEqual(sig['phase'],'watch')
        bars[-1].update(open=102,close=102,high=102,low=101)
        self.assertTrue(d.analyze(bars)['signals'])

    def test_far_broken_and_retraced_are_not_approach(self):
        far=example();far[-1].update(open=96,close=96,high=97,low=95)
        self.assertFalse(d.analyze(far)['signals'])
        broken=example();broken[-1].update(open=103,close=103,high=104,low=102)
        self.assertFalse(d.analyze(broken)['signals'])
        retraced=example();retraced[310].update(open=107,close=107,high=108,low=106)
        # Original A/B cannot come back as a fresh signal after a close breakout.
        result=d.analyze(retraced)
        self.assertFalse(any(s['trendline']['anchor_b_date']==retraced[300]['date'] for s in result['signals']))

    def test_future_incomplete_and_missing_inputs(self):
        bars=example();before=d.analyze(bars)
        future=dict(bars[-1],date='2099-01-01',high=999)
        self.assertEqual(before,d.analyze(bars+[future],as_of=bars[-1]['date']))
        future['complete']=False
        self.assertEqual(before,d.analyze(bars+[future]))
        bars[-1]['close']=None
        self.assertFalse(d.analyze(bars)['signals'])
        self.assertFalse(d.analyze(example()[:292])['signals'])

    def test_daily_integration_preserves_existing_routes_and_cache(self):
        screen,snapshot=fixture()
        bars=example()
        # Use the same completed calendar for every stock and benchmark.
        for row in screen['rows']:row['as_of']=bars[-1]['date']
        screen['session_dates']={m:bars[-1]['date'] for m in ('KR','US')}
        for item in snapshot['series']:item['bars']=copy.deepcopy(bars)
        for m in ('KR','US'):snapshot['sessions'][m]=[b['date'] for b in bars]
        for item in snapshot['benchmarks'].values():item['bars']=copy.deepcopy(bars)
        options=dict(strategy='pdf_momentum',watchlist=dict(enabled=True,base_structure={}))
        before=priority_review.build(screen,snapshot,options)
        after=priority_review.build(screen,snapshot,{**options,'watchlist':{**options['watchlist'],d.ROUTE:{}}})
        self.assertEqual(after['entry_candidate_count'],before['entry_candidate_count'])
        for m in ('KR','US'):
            self.assertEqual(after['markets'][m][d.ROUTE]['candidate_count'],2)
            for route in ('pdf_momentum','sperandeo','base_watch'):
                self.assertEqual(before['markets'][m][route],after['markets'][m][route])
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder)
            def draw(bars,path,entry):path.write_bytes(b'fixture-png')
            charts=priority_review.charts(after,screen,snapshot,out,draw)
            self.assertEqual(charts['failed'],0)
            self.assertEqual(charts['available'],4)
            momentum_review.write_outputs(out,after)
            momentum_review.write(out/'priority-review.json',after)
            momentum_review.write(out/'priority-charts.json',charts)
            watch_review.render({'state':'completed'},out)
            self.assertIn(d.LABEL,(out/'report.html').read_text(encoding='utf-8'))
        removed=d.merge(after,[],d.settings())
        for m in ('KR','US'):
            self.assertEqual(removed['markets'][m]['entries'],before['markets'][m]['entries'])
        original=copy.deepcopy(before)
        with tempfile.TemporaryDirectory() as folder:
            projected=d.cached(folder,before,snapshot,'fixture')
            with patch.object(d,'scan',side_effect=AssertionError('should_reuse')):
                self.assertEqual(projected,d.cached(folder,before,snapshot,'fixture'))
            self.assertEqual(projected,after)
        self.assertEqual(before,original)
        # Common universe gate must remain authoritative.
        screen['rows'][0]['universe']['universe_pass']=False
        gated=priority_review.build(screen,snapshot,{**options,'watchlist':{d.ROUTE:{}}})
        self.assertEqual(gated['markets']['KR'][d.ROUTE]['candidate_count'],1)

    def test_wilder_atr_and_invalid_settings(self):
        bars=series([100.]*20)
        self.assertAlmostEqual(d.atr14(bars),.8)
        for value in (True,0,float('nan')):
            with self.assertRaises(ValueError):d.settings({'approach_atr':value})

if __name__=='__main__':unittest.main()
