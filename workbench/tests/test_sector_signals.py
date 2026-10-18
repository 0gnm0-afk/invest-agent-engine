import copy
import datetime
import sys
import tempfile
import unittest
from pathlib import Path
from bs4 import BeautifulSoup

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import sector_signals as signals


def fixture(count=100):
    dates=[(datetime.date(2026,1,1)+datetime.timedelta(days=i)).isoformat() for i in range(count)]
    snapshot={'universe':[],'series':[],'sessions':{'KR':dates,'US':dates},'benchmarks':{}}
    groups=[]; observations=[]
    for market in ('KR','US'):
        for g in ('A','B'):
            observations.append({'market':market,'group_code':g,'group_name':g})
            for j in range(10):
                symbol=f'{market}{g}{j}'
                row={'market':market,'symbol':symbol,'isin_code':symbol,'name':symbol}
                snapshot['universe'].append(row.copy())
                groups.append(dict(row,group_code=g,group_name=g,group_state='matched'))
                row.update(provider='Toss OpenAPI candles',source_url='https://openapi.tossinvest.com/api/v1/candles',
                    currency='KRW' if market=='KR' else 'USD',price_basis='toss_adjusted_ohlc_no_order_authority',collection={'adjusted':True},
                    bars=[{'date':d,'close':100*(1.002 if g=='A' else 1.0005)**i,'volume':1000+j*10} for i,d in enumerate(dates)])
                snapshot['series'].append(row)
        if market=='US':
            bench=copy.deepcopy(snapshot['series'][-1]);bench['bars']=[{'date':d,'close':100*1.0008**i,'volume':1000} for i,d in enumerate(dates)]
            snapshot['benchmarks']['^GSPC']=bench
    return snapshot,groups,{'observations':observations,'market_data_complete':False}


class StateTests(unittest.TestCase):
    def metric(self, **kw):
        m=dict(activity_now=1.3,activity_change=.625,share_change=.10,share_delta=.01,
            rs_now=.05,rs_delta=.01,return_recent=.02,trend_distance=.02,trend_slope=.01,issues=[])
        return dict(m,**kw)

    def test_level_and_direction_are_separate(self):
        self.assertEqual(signals.classify(self.metric(activity_now=2,activity_change=2/3-1),signals.DEFAULTS),'strong_cooling')
        self.assertEqual(signals.classify(self.metric(activity_now=1.3,activity_change=1.3/.8-1),signals.DEFAULTS),'strengthening')

    def test_price_weakness_has_priority(self):
        self.assertEqual(signals.classify(self.metric(activity_now=2,return_recent=-.03),signals.DEFAULTS),'surge_weak')

    def test_declining_activity_alone_is_not_bearish(self):
        self.assertEqual(signals.classify(self.metric(activity_change=-.3,trend_distance=-.1),signals.DEFAULTS),'mixed')

    def test_neutral_and_missing(self):
        self.assertEqual(signals.classify(self.metric(activity_change=.01,share_change=.01,share_delta=.00001,rs_delta=.001),signals.DEFAULTS),'steady')
        self.assertEqual(signals.classify(self.metric(rs_now=None),signals.DEFAULTS),'unavailable')
        self.assertEqual(signals.classify(self.metric(issues=['coverage']),signals.DEFAULTS),'unavailable')

    def test_bad_config_fails(self):
        for config in ({'unknown':5},{'comparison_days':0},{'history_days':1.5},{'min_coverage':2},{'rs_change':float('nan')}):
            with self.assertRaises(ValueError):signals.settings(config)


class ReplayTests(unittest.TestCase):
    def test_prefix_exact_and_future_changes_do_not_leak(self):
        snap,groups,obs=fixture()
        full=signals.build(snap,groups,obs)
        day=snap['sessions']['US'][-8]
        prefix=signals.build(snap,groups,obs,as_of=day)
        for actual in prefix['sectors']:
            expected=next(s for s in full['sectors'] if (s['market'],s['group_code'])==(actual['market'],actual['group_code']))
            self.assertEqual(actual['current'],next(r for r in expected['history'] if r['date']==day))
        mutated=copy.deepcopy(snap)
        for row in mutated['series']:
            for bar in row['bars']:
                if bar['date']>day:bar.update(close=-500,volume=10**15)
        self.assertEqual(prefix,signals.build(mutated,groups,obs,as_of=day))

    def test_missing_later_bar_does_not_remove_past_member(self):
        snap,groups,obs=fixture();day=snap['sessions']['KR'][-8]
        before=signals.build(snap,groups,obs,as_of=day)
        snap['series'][0]['bars'].pop()
        full=signals.build(snap,groups,obs)
        self.assertEqual(before,signals.build(snap,groups,obs,as_of=day))
        self.assertNotIn('KRA0',full['sectors'][0]['members'])
        self.assertIn('KRA0',full['sectors'][0]['exclusions'])

    def test_duplicate_future_bar_does_not_contaminate_history(self):
        snap,groups,obs=fixture();day=snap['sessions']['KR'][-8]
        prefix=signals.build(snap,groups,obs,as_of=day)
        snap['series'][0]['bars'].append(copy.deepcopy(snap['series'][0]['bars'][-1]))
        full=signals.build(snap,groups,obs)
        self.assertEqual(prefix['sectors'][0]['current'],next(r for r in full['sectors'][0]['history'] if r['date']==day))

    def test_proxy_currency_and_unclassified_denominator(self):
        snap,groups,obs=fixture()
        groups[0]['group_state']='unmatched'
        result=signals.build(snap,groups,obs)
        self.assertEqual(result['markets']['KR']['unclassified_used'],1)
        self.assertLess(sum(s['current']['share_now'] for s in result['sectors'] if s['market']=='KR'),1)
        baseline=[s for s in result['sectors'] if s['market']=='KR']
        for row in snap['series']:
            if row['market']=='US':
                for bar in row['bars']:bar['volume']*=10000
        self.assertEqual(baseline,[s for s in signals.build(snap,groups,obs)['sectors'] if s['market']=='KR'])

    def test_concentration_is_an_independent_flag(self):
        snap,groups,obs=fixture()
        for row in snap['series'][:10]:
            for i,bar in enumerate(row['bars']):
                bar['close']=100*.998**i
                if row['symbol'] in ('KRA0','KRA1','KRA2'):bar['volume']*=100
        result=signals.build(snap,groups,obs)
        self.assertTrue(result['sectors'][0]['current']['concentrated'])
        self.assertIn(result['sectors'][0]['current']['state'],signals.LABELS)

    def test_insufficient_history_and_zero_baseline_are_visible(self):
        snap,groups,obs=fixture(12)
        result=signals.build(snap,groups,obs)
        self.assertTrue(all(s['current']['state']=='unavailable' for s in result['sectors']))
        snap,groups,obs=fixture()
        for row in snap['series'][:10]:
            for bar in row['bars']:bar['volume']=0
        result=signals.build(snap,groups,obs)
        self.assertEqual(result['sectors'][0]['current']['state'],'unavailable')
        self.assertEqual(len(result['sectors'][0]['current']['activity_excluded']),10)

    def test_candidate_preservation_and_escaped_output(self):
        snap,groups,obs=fixture()
        obs['observations'][0]['group_name']='<script>bad()</script>'
        groups[0]['name']='<img src=x onerror=bad()>'
        result=signals.build(snap,groups,obs)
        review={'markets':{m:{'entries':[{'symbol':m+'A0','origins':['sperandeo','taver'],'reasons':['both'],'selected':True,'selected_by':['taver']}]} for m in ('KR','US')}}
        original=copy.deepcopy(review)
        signals.attach_candidates(result,review,groups,{'rows':[]})
        self.assertEqual(review,original)
        self.assertEqual(result['sectors'][0]['candidates'][0]['origins'],['sperandeo','taver'])
        with tempfile.TemporaryDirectory() as tmp:
            signals.render(result,Path(tmp))
            page=(Path(tmp)/signals.filename(result['sectors'][0])).read_text(encoding='utf-8')
            self.assertNotIn('<script>bad()',page)
            self.assertNotIn('<img src=x',page)
            soup=BeautifulSoup(page,'html.parser')
            self.assertEqual(len(soup.select('svg')),6)
            self.assertEqual(sum(signals.verify_saved(Path(tmp),result).values()),4)


if __name__=='__main__':unittest.main()
