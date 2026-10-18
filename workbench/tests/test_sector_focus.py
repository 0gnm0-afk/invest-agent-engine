import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import sector_focus as f
from test_sector_signals import fixture


class FocusTests(unittest.TestCase):
    def metric(self, **kw):
        return dict(dict(issues=[],growth=.25,previous_growth=.05,acceleration=.20,
             activity_now=.9,return_recent=.01,participation=.8,persistent_days=4,share_change=.1),**kw)

    def test_early_below_normal_is_not_excluded(self):
        self.assertEqual(f.classify(self.metric(),f.DEFAULTS),('building','평소 아래서 개선'))

    def test_level_is_not_acceleration(self):
        self.assertEqual(f.classify(self.metric(activity_now=3,acceleration=-.1),f.DEFAULTS)[0],'slowing')

    def test_single_day_and_price_weak_and_market_wide_are_distinct(self):
        self.assertEqual(f.classify(self.metric(persistent_days=1),f.DEFAULTS)[0],'watch')
        self.assertEqual(f.classify(self.metric(return_recent=-.05),f.DEFAULTS)[0],'price_weak')
        self.assertEqual(f.classify(self.metric(share_change=-.01),f.DEFAULTS)[0],'watch')
        self.assertEqual(f.classify(self.metric(issues=['stale']),f.DEFAULTS)[0],'unavailable')

    def test_formula_uses_same_cohort_and_percentage_points(self):
        # Manufacture exact relative volumes .8, 1.0, 1.5 in the three windows.
        bars=[dict(close=10,volume=100) for _ in range(20)]
        for target in [.8]*5+[1]*5+[1.5]*5:
            bars.append(dict(close=10,volume=sum(b['volume'] for b in bars[-20:])/20*target))
        m,_=f.metrics(['A'],{'A':bars},[2000]*35,5,20,f.DEFAULTS)
        self.assertAlmostEqual(m['growth'],.5)
        self.assertAlmostEqual(m['previous_growth'],.25)
        self.assertAlmostEqual(m['acceleration'],.25)
        self.assertEqual(m['persistent_days'],5)

    def test_size_scaling_does_not_change_attention(self):
        snap,g,o=fixture(45);first=f.build(snap,g,o['observations'])
        for row in snap['series']:
            if row['market']=='KR':
                for bar in row['bars']:bar['volume']*=100000
        second=f.build(snap,g,o['observations'])
        for a,b in zip(first['items'],second['items']):
            for key in ('growth','acceleration','participation','state'):
                self.assertEqual(a['current'].get(key),b['current'].get(key))

    def test_small_group_and_classification_gaps_not_promoted(self):
        snap,g,o=fixture(45)
        for r in g[:17]:r['group_state']='unmatched'
        result=f.build(snap,g,o['observations'])
        self.assertFalse(result['markets']['KR']['selected'])
        self.assertTrue(all(r['current']['state']=='unavailable' for r in result['items'] if r['market']=='KR'))

    def test_future_and_missing_later_bar_do_not_change_past(self):
        snap,g,o=fixture(55);before=f.build(snap,g,o['observations'])
        day=snap['sessions']['US'][-8]
        for row in snap['series']:
            for bar in row['bars']:
                if bar['date']>day:bar['volume']=10**15
        snap['series'][0]['bars'].pop()
        after=f.build(snap,g,o['observations'])
        for a,b in zip(before['items'],after['items']):
            self.assertEqual([r for r in a['history'] if r['date']<=day],[r for r in b['history'] if r['date']<=day])

    def test_zero_baseline_short_history_and_invalid_basis(self):
        snap,g,o=fixture(12);result=f.build(snap,g,o['observations'])
        self.assertTrue(all(r['current']['state']=='unavailable' for r in result['items']))
        snap,g,o=fixture(45)
        for row in snap['series']:
            for bar in row['bars']:bar['volume']=0
        self.assertTrue(all(r['current']['state']=='unavailable' for r in f.build(snap,g,o['observations'])['items']))
        snap,g,o=fixture(45)
        for row in snap['series']:row['provider']='unverified'
        self.assertTrue(all(r['current']['state']=='unavailable' for r in f.build(snap,g,o['observations'])['items']))

    def test_direct_industry_evidence_only_no_kr_inference(self):
        snap,g,o=fixture(45)
        for row in g:
            row['evidence']=[dict(industry='Small group',sector=row['group_name'])]
        items=f.baskets(g,o['observations'])
        self.assertEqual(len([r for r in items if r['level']=='industry']),2)
        self.assertTrue(all(r['market']=='US' for r in items if r['level']=='industry'))
        g[-1]['evidence'].append(dict(industry='Other',sector=g[-1]['group_name']))
        self.assertEqual(sum(len(r['members']) for r in f.baskets(g,o['observations']) if r['level']=='industry'),19)

    def test_currency_separation_top_three_missing_last_and_original_members(self):
        entries=[dict(market=m,symbol=str(i),origins=['pdf_momentum','base_watch']) for m in ('KR','US') for i in range(5)]
        original=copy.deepcopy(entries)
        uni=[dict(market=e['market'],symbol=e['symbol'],market_cap=int(e['symbol'])*100,
             market_cap_currency='KRW' if e['market']=='KR' else 'USD',market_cap_as_of='2026-09-24') for e in entries]
        groups=f.screen_groups(entries,uni)
        for group in groups:
            if group['route'] in ('sperandeo','downtrend_approach'):self.assertEqual(group['top'],[])
            else:self.assertEqual((group['top'],group['missing_cap']),(['4','3','2'],1))
        self.assertEqual([{k:v for k,v in e.items() if k!='display_cap'} for e in entries],original)
        uni[-1]['market_cap_currency']='KRW'
        group=next(g for g in f.screen_groups(entries,uni) if g['market']=='US' and g['route']=='pdf_momentum')
        self.assertEqual(group['top'],['3','2','1'])

    def test_cache_reuse_and_tamper_detection(self):
        snap,g,o=fixture(40)
        with tempfile.TemporaryDirectory() as t:
            result=f.cached(Path(t),'run',snap,g,o['observations'],'source')
            with patch.object(f,'build',side_effect=AssertionError('recalculated')):
                self.assertEqual(result,f.cached(Path(t),'run',snap,g,o['observations'],'source'))
            file=next(Path(t).glob('runtime/sector-focus/*/focus.json'));file.write_text('{}')
            with self.assertRaisesRegex(ValueError,'hash_mismatch'):f.cached(Path(t),'run',snap,g,o['observations'],'source')

    def test_zero_activity_member_is_excluded_without_blocking_sufficient_cohort(self):
        bars=[dict(close=10,volume=100) for _ in range(35)]
        halted=[dict(close=10,volume=100 if i<20 else 0) for i in range(35)]
        series={str(i):copy.deepcopy(bars if i<9 else halted) for i in range(10)}
        m,_=f.metrics(list(series),series,[10000]*35,5,20,f.DEFAULTS)
        self.assertEqual(m['issues'],[])
        self.assertEqual(m['activity_used'],9)
        self.assertEqual(m['activity_exclusions'],{'9':'comparison_activity_zero'})
        self.assertEqual(m['activity_now'],1)
        self.assertEqual(m['growth'],0)
        for i in range(7,10):series[str(i)]=halted
        m,_=f.metrics(list(series),series,[10000]*35,5,20,f.DEFAULTS)
        self.assertTrue(m['issues'])
        self.assertEqual(m['activity_used'],7)

    def test_invalid_settings(self):
        for c in ({'extra':1},{'window':0},{'min_coverage':2},{'min_acceleration':float('nan')},{'home_limit':True}):
            with self.assertRaises(ValueError):f.settings(c)


if __name__=='__main__':unittest.main()
