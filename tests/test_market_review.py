import copy
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import Mock, patch

from invest_agent.research.market_review import analyze, run
from invest_agent.research.synthetic_market import fixture
from invest_agent.research import priority_review as priority
from invest_agent.research.sector_metrics import group_metrics, equal_weight_index


class MarketReviewTests(unittest.TestCase):
    def test_equal_weight_is_daily_rebalanced_not_endpoint_average(self):
        # 100->200->100 and 100->100->200: daily mean returns .5 and .25.
        self.assertEqual(equal_weight_index([[100,200,100],[100,100,200]]),[100,150,187.5])

    def test_metrics_and_missing_benchmark_are_explicit(self):
        days = [str(i).zfill(3) for i in range(127)]
        bars = [dict(date=d,close=100+i,volume=10 if i<126 else 20) for i,d in enumerate(days)]
        series = {('US','SYNTH'):{'price_basis':'synthetic','bars':bars}}
        members = [{'market':'US','symbol':'SYNTH'},{'market':'US','symbol':'MISSING'}]
        result = group_metrics(members,series,days,{'price_basis':'synthetic','bars':[dict(b,close=100) for b in bars]})
        self.assertAlmostEqual(result['horizons']['63']['relative_strength'],226/163-1)
        self.assertEqual(result['horizons']['63']['used'],1)
        self.assertEqual(result['horizons']['63']['exclusions'],{'MISSING':'series_missing'})
        self.assertEqual(result['breadth_sma60']['fraction'],1)
        self.assertEqual(result['activity']['median_ratio'],2)
        missing = group_metrics(members,series,days,None)
        self.assertIsNone(missing['horizons']['63']['relative_strength'])
        self.assertEqual(missing['horizons']['63']['benchmark_issue'],'series_missing')

    def test_source_boundaries_and_duplicate_identity(self):
        for field, value in [('source','live')]:
            snapshot, groups = fixture()
            snapshot[field]=value
            with self.assertRaises(ValueError): analyze(snapshot,groups)
        snapshot, groups = fixture()
        snapshot['series'].append(copy.deepcopy(snapshot['series'][0]))
        with self.assertRaises(ValueError): analyze(snapshot,groups)
        snapshot, groups = fixture()
        snapshot['series'][0]['price_basis']='raw'
        with self.assertRaises(ValueError): analyze(snapshot,groups)
        snapshot, groups = fixture()
        groups.append(copy.deepcopy(groups[0]))
        with self.assertRaises(ValueError): analyze(snapshot,groups)

    def test_candidates_order_inputs_quality_and_missing_classification(self):
        snapshot, groups = fixture()
        before=copy.deepcopy((snapshot,groups))
        with patch.object(socket.socket,'connect',side_effect=AssertionError('network_forbidden')):
            result=analyze(snapshot,groups)
        self.assertEqual((snapshot,groups),before)
        self.assertEqual(result['screen']['candidate_count'],8)
        self.assertEqual(result['screen']['unavailable_count'],2)
        self.assertEqual(result['quality']['state'],'partial')
        self.assertEqual(result['quality']['valid_count'],8)
        baseline=result['screen']['rows']
        enriched=result['context']['rows']
        self.assertEqual([{k:v for k,v in row.items() if k!='sector_context'} for row in enriched],baseline)
        missing=[r for r in enriched if r['symbol'].endswith('_04')]
        self.assertTrue(all(r['state']=='candidate' and r['sector_context']['state']=='unavailable' for r in missing))
        for group in result['priority']['markets'].values():
            self.assertEqual((group['candidate_count'],group['selected_count']),(4,2))
            self.assertTrue(all(e['selection_reason'] for e in group['entries']))
        for obs in result['sectors']['observations']:
            self.assertNotIn('SYNTH_'+obs['market']+'_05',obs['horizons']['63']['members'])

    def test_zero_limit_does_not_remove_candidates_and_replay_is_equal(self):
        snapshot,groups=fixture()
        first=analyze(snapshot,groups,0)
        self.assertEqual(first,analyze(snapshot,groups,0))
        self.assertEqual(first['screen']['candidate_count'],8)
        self.assertEqual(sum(v['selected_count'] for v in first['priority']['markets'].values()),0)
        for config in ({'limit_per_market':True},{'limit_per_market':-1},{'sort_fields':['unknown']}):
            with self.assertRaises(ValueError): priority.settings(config)

    def test_confirmation_overlay_never_draws_before_confirmation_or_after_breach(self):
        bars=[{'date':str(i)} for i in range(10)]
        line={'state':'available','anchor_a_date':'1','anchor_a_price':20,'anchor_b_date':'4','anchor_b_price':17,
              'confirmed_at':'6','slope_per_bar':-1,'first_price_breach_date':'8'}
        result={'primary_window':126,'windows':{'126':{'upper_trendline':line,'lower_trendline':{'state':'no_valid_anchors'}}}}
        axes=Mock()
        priority.confirmed_overlay(axes,bars,0,result)
        self.assertEqual(axes.plot.call_args.args[0],[6,7,8])
        line['confirmed_at']='8'
        axes.reset_mock()
        priority.confirmed_overlay(axes,bars,0,result)
        self.assertEqual(axes.plot.call_args.args[0],[8])
        self.assertEqual(axes.plot.call_args.kwargs['marker'],'D')

    def test_chart_failure_isolated_and_no_exception_payload_exported(self):
        snapshot,groups=fixture()
        result=analyze(snapshot,groups)
        with tempfile.TemporaryDirectory() as directory:
            charts=priority.charts(result['priority'],result['context'],snapshot,Path(directory),
                                   draw=Mock(side_effect=OSError('sensitive error payload')))
        self.assertEqual(charts['failed'],4)
        self.assertTrue(all(row['reason']=='OSError' for row in charts['rows']))

    def test_end_to_end_network_blocked_links_images_and_overwrite_protection(self):
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)/'demo'
            with patch.object(socket.socket,'connect',side_effect=AssertionError('network_forbidden')), \
                    patch.object(socket,'create_connection',side_effect=AssertionError('network_forbidden')):
                result=run(output)
            self.assertEqual(result['charts'],{'requested':4,'available':4,'failed':0})
            self.assertEqual(result['state'],'partial')
            for row in json.loads((output/'charts.json').read_text(encoding='utf-8'))['rows']:
                for key in ('path','image','metadata'):
                    self.assertTrue((output/row[key]).is_file())
                self.assertTrue((output/row['image']).read_bytes().startswith(b'\x89PNG\r\n\x1a\n'))
            before=(output/'report.html').read_bytes()
            with self.assertRaises(ValueError): run(output)
            self.assertEqual((output/'report.html').read_bytes(),before)


if __name__=='__main__':
    unittest.main()
