import copy
import json
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import sector_observation as so
import sector_basis as sb


def stock(market='KR'):
    return {'market':market,'currency':'KRW' if market=='KR' else 'USD','provider':'Toss OpenAPI candles',
            'source_url':'https://openapi.tossinvest.com/api/v1/candles',
            'price_basis':'toss_adjusted_ohlc_no_order_authority','collection':{'adjusted':True}}


def kospi():
    return {'market':'KR','currency':'KRW','provider':'Toss OpenAPI candles',
            'source_url':'https://openapi.tossinvest.com/api/v1/market-indicators/KOSPI/candles',
            'provider_symbol':'KOSPI','instrument_type':'INDEX','price_basis':'toss_index_points_observation_only'}


class BasisTest(unittest.TestCase):
    def test_current_wics_report_cannot_fall_back_to_unverified_legacy_basis(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'wics-public.json').write_text('{}')
            with self.assertRaisesRegex(ValueError,'requires_verified_calculation_basis'):
                so.build(root,root,root,root,'2026-09-25T08:00:00+09:00')

    def test_official_session_and_adjusted_volume_are_distinct_fields(self):
        basis,issue=sb.series_basis(stock('US'))
        self.assertIsNone(issue)
        self.assertEqual(basis['price_session'],'US_regular_official_close')
        self.assertIn('divided_by',basis['volume_adjustment'])
        self.assertIn('off_session',basis['volume_session'])

    def test_market_specific_adjustment_semantics(self):
        kr,_=sb.series_basis(stock('KR'))
        us,_=sb.series_basis(stock('US'))
        self.assertIn('KRW_rounded',kr['price_adjustment'])
        self.assertIn('floored',kr['volume_adjustment'])
        self.assertIn('USD_unrounded',us['price_adjustment'])
        self.assertIn('nearest_integer',us['volume_adjustment'])

    def test_unknown_provider_raw_price_and_wrong_currency_rejected(self):
        for key,value in [('provider','NH'),('source_url','https://example.invalid'),('currency','USD'),
                          ('price_basis','toss_raw_ohlc_no_order_authority'),('collection',{'adjusted':False}),
                          ('collection',{'adjusted':'true'})]:
            row=stock();row[key]=value
            with self.subTest(key=key):self.assertIsNotNone(sb.series_basis(row)[1])

    def test_kospi_and_integrated_prices_not_marked_compatible(self):
        self.assertEqual(sb.comparison_issue(sb.series_basis(stock())[0],sb.series_basis(kospi())[0]),
                         'benchmark_price_session_mismatch')

    def test_stock_and_spy_same_regular_adjusted_basis(self):
        self.assertIsNone(sb.comparison_issue(sb.series_basis(stock('US'))[0],sb.series_basis(stock('US'))[0]))

    def test_cross_market_reference_and_unverified_basis_rejected(self):
        self.assertEqual(sb.comparison_issue(sb.series_basis(stock())[0],sb.series_basis(stock('US'))[0]),
                         'benchmark_market_mismatch')
        self.assertEqual(sb.comparison_issue(None,None),'benchmark_basis_unverified')

    def test_doc_hash_freshness_and_approval_config_are_enforced(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'sources').mkdir();doc=root/'sources/faq.md'
            doc.write_text('frozen official fixture',encoding='utf-8')
            meta={'url':sb.FAQ_URL,'final_url':sb.FAQ_URL,'status':200,'sha256':so.sha(doc),
                  'observed_at':'2026-09-25T07:00:00+09:00'}
            so.write(doc.with_suffix('.md.meta.json'),meta)
            config={'contract':sb.CONTRACT,'kr_reference':'saved_universe_equal_weight',
                    'documents':[{'file':'faq.md','url':sb.FAQ_URL,'sha256':meta['sha256']}]}
            so.write(root/'calculation-basis.json',config)
            self.assertIsNotNone(sb.load_basis(root,so.checked,so.load,{},'2026-09-25T08:00:00+09:00',so.age_issue))
            with self.assertRaisesRegex(ValueError,'not_current'):
                sb.load_basis(root,so.checked,so.load,{},'2026-09-25T06:00:00+09:00',so.age_issue)
            doc.write_text('tampered',encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'hash_mismatch'):
                sb.load_basis(root,so.checked,so.load,{},'2026-09-25T08:00:00+09:00',so.age_issue)


class ReferenceTest(unittest.TestCase):
    def setUp(self):
        self.sessions=[(date(2025,1,1)+timedelta(days=i)).isoformat() for i in range(140)]
        self.members=[{'market':'KR','symbol':'A','group_state':'matched'},
                      {'market':'KR','symbol':'B','group_state':'unmatched'}]
        self.series={('KR',symbol):{**stock(),'bars':[{'date':d,'close':100*(1+rate)**i,'volume':100}
                        for i,d in enumerate(self.sessions)]} for symbol,rate in [('A',.01),('B',-.005)]}
        self.index={**kospi(),'bars':[{'date':d,'close':100*1.02**i} for i,d in enumerate(self.sessions)]}

    def calc(self,reference=None):
        return so.group_metrics(self.members[:1],self.series,self.sessions,self.index,
                                validate_basis=True,reference=reference)

    def test_unmatched_sector_stock_is_in_whole_market_reference(self):
        reference=so.korean_market_reference(self.members,self.series,self.sessions)
        self.assertEqual(reference['horizons']['63']['used'],2)
        self.assertAlmostEqual(reference['horizons']['63']['return'],1.0025**63-1)
        result=self.calc(reference)['horizons']['63']
        self.assertAlmostEqual(result['relative_strength'],1.01**63/1.0025**63-1)
        self.assertIsNone(result['benchmark_issue'])

    def test_kospi_reference_cannot_silently_be_used_in_strict_calculation(self):
        result=self.calc()['horizons']['63']
        self.assertIsNone(result['relative_strength'])
        self.assertIsNotNone(result['return'])
        self.assertIsNotNone(result['benchmark_return'])
        self.assertEqual(result['benchmark_issue'],'benchmark_price_session_mismatch')

    def test_reference_fixed_cohort_varies_by_horizon_and_preserves_exclusions(self):
        del self.series[('KR','B')]['bars'][-100]
        reference=so.korean_market_reference(self.members,self.series,self.sessions)
        self.assertEqual(reference['horizons']['63']['used'],2)
        self.assertEqual(reference['horizons']['126']['used'],1)
        self.assertEqual(reference['horizons']['126']['exclusions']['B'],'missing_session_or_short_history')
        self.assertAlmostEqual(self.calc(reference)['horizons']['126']['relative_strength'],0)

    def test_wrong_reference_period_rejected(self):
        reference=so.korean_market_reference(self.members,self.series,self.sessions)
        reference['horizons']['63']['start']='2020-01-01'
        result=self.calc(reference)['horizons']['63']
        self.assertIsNone(result['relative_strength'])
        self.assertEqual(result['benchmark_issue'],'market_reference_period_mismatch')

    def test_raw_series_excluded_from_all_metrics_without_modifying_input(self):
        self.series[('KR','A')]['collection']['adjusted']=False
        before=copy.deepcopy(self.series)
        result=self.calc()
        self.assertEqual(result['horizons']['63']['exclusions']['A'],'adjusted_price_and_volume_required')
        self.assertIsNone(result['activity']['mean_ratio'])
        self.assertEqual(self.series,before)

    def test_already_split_adjusted_price_volume_not_adjusted_twice(self):
        # A 2-for-1 split: supplier adjusted old 100/1000 into 50/2000;
        # new actual 50/2000 must produce flat return and activity ratio 1.
        self.series[('KR','A')]['bars']=[{'date':d,'close':50,'volume':2000} for d in self.sessions]
        before=copy.deepcopy(self.series)
        result=self.calc()
        self.assertEqual(result['horizons']['63']['return'],0)
        self.assertEqual(result['activity']['mean_ratio'],1)
        self.assertEqual(self.series,before)

    def test_reference_rejects_other_market_members(self):
        self.members[1]['market']='US'
        with self.assertRaisesRegex(ValueError,'cannot_mix_markets'):
            so.korean_market_reference(self.members,self.series,self.sessions)


if __name__=='__main__':unittest.main()
