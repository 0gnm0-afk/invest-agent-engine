import copy
import math
import sys
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import sector_observation as so


class MetricsTest(unittest.TestCase):
    def setUp(self):
        self.sessions=[(date(2025,1,1)+timedelta(days=i)).isoformat() for i in range(140)]
        self.members=[{'market':'KR','symbol':s} for s in ('A','B')]
        self.series={('KR',s):{'bars':[{'date':d,'close':100*(1+rate)**i,'volume':20 if i==139 else 10}
                     for i,d in enumerate(self.sessions)]} for s,rate in [('A',.01),('B',-.005)]}
        self.benchmark={'bars':[{'date':d,'close':100*1.001**i} for i,d in enumerate(self.sessions)]}

    def calc(self):
        return so.group_metrics(self.members,self.series,self.sessions,self.benchmark)

    def test_daily_equal_weight_not_mean_period_returns(self):
        m=self.calc()['horizons']['63']
        expected=1.0025**63-1
        self.assertAlmostEqual(m['return'],expected)
        self.assertNotAlmostEqual(m['return'],((1.01**63-1)+(.995**63-1))/2)
        self.assertAlmostEqual(m['relative_strength'],1.0025**63/1.001**63-1)
        self.assertEqual(m['positive_fraction'],.5)

    def test_trend_and_activity_denominator_excludes_today(self):
        m=self.calc()
        self.assertEqual(m['activity']['mean_ratio'],2)
        self.assertEqual(m['breadth_sma60']['fraction'],.5)
        self.assertGreater(m['trend']['distance_sma60'],0)
        self.assertAlmostEqual(m['trend']['sma20_change5'],1.0025**5-1)

    def test_gap_excluded_per_horizon_without_fill(self):
        del self.series[('KR','B')]['bars'][-100]
        m=self.calc()
        self.assertEqual(m['horizons']['126']['used'],1)
        self.assertEqual(m['horizons']['63']['used'],2)
        self.assertEqual(m['activity']['used'],2)
        self.assertEqual(m['horizons']['126']['exclusions']['B'],'missing_session_or_short_history')

    def test_short_history_and_missing_price_preserve_counts(self):
        self.series[('KR','A')]['bars']=self.series[('KR','A')]['bars'][-64:]
        del self.series[('KR','B')]
        m=self.calc()
        self.assertEqual(m['horizons']['63']['used'],1)
        self.assertIsNone(m['horizons']['126']['return'])
        self.assertEqual(m['horizons']['126']['used']+m['horizons']['126']['excluded'],2)

    def test_zero_prior_volume_missing_and_zero_today_are_distinct(self):
        for bar in self.series[('KR','A')]['bars'][-21:-1]:bar['volume']=0
        self.series[('KR','B')]['bars'][-1]['volume']=0
        m=self.calc()['activity']
        self.assertEqual(m['mean_ratio'],0)
        self.assertEqual(m['used'],1)
        self.assertEqual(m['exclusions']['A'],'zero_prior20_average_volume')

    def test_missing_volume_does_not_destroy_price_metrics(self):
        del self.series[('KR','A')]['bars'][-1]['volume']
        m=self.calc()
        self.assertEqual(m['horizons']['126']['used'],2)
        self.assertEqual(m['activity']['used'],1)

    def test_invalid_prices_nan_negative_zero_are_excluded(self):
        for value in (0,-1,math.nan,math.inf,True):
            self.series[('KR','A')]['bars'][-1]['close']=value
            self.assertEqual(self.calc()['horizons']['63']['used'],1)

    def test_duplicate_and_future_bars_rejected(self):
        bars=self.series[('KR','A')]['bars']
        bars.append(copy.deepcopy(bars[-1]))
        self.assertEqual(self.calc()['horizons']['63']['exclusions']['A'],'duplicate_or_unsorted_dates')
        bars.pop();bars.append({'date':'2030-01-01','close':100,'volume':20})
        self.assertEqual(self.calc()['horizons']['63']['exclusions']['A'],'bar_outside_market_sessions')

    def test_benchmark_gap_nulls_rs_not_absolute_return(self):
        del self.benchmark['bars'][-20]
        m=self.calc()['horizons']['63']
        self.assertIsNone(m['relative_strength'])
        self.assertIsNotNone(m['return'])
        self.assertEqual(m['benchmark_issue'],'missing_session_or_short_history')

    def test_market_calendar_does_not_use_other_market_dates(self):
        self.sessions=self.sessions[:-1]
        self.assertIsNone(self.calc()['horizons']['63']['return'])

    def test_empty_sector_is_missing_not_zero(self):
        self.members=[]
        m=self.calc()
        self.assertIsNone(m['horizons']['63']['return'])
        self.assertIsNone(m['activity']['mean_ratio'])
        self.assertIsNone(m['breadth_sma60']['fraction'])
        self.assertTrue(m['activity']['small_sample'])

    def test_price_and_volume_inputs_not_mutated(self):
        before=copy.deepcopy(self.series)
        self.calc()
        self.assertEqual(before,self.series)


class MappingTest(unittest.TestCase):
    def page(self,sector='Technology',exchange='nasd',title='AAPL - Apple Inc'):
        return f'<title>{title}</title><a href="screener?v=111&f=sec_technology">{sector}</a><a href="screener?f=exch_{exchange}">Exchange</a>'

    def test_direct_finviz_sector(self):
        row,reason=so.finviz_classification(self.page(),'AAPL')
        self.assertIsNone(reason)
        self.assertEqual(row,{'group_name':'Technology','exchange':'NASDAQ'})

    def test_title_identity_and_multiple_sectors_rejected(self):
        self.assertEqual(so.finviz_classification(self.page(title='MSFT - Microsoft'),'AAPL')[1],'finviz_symbol_unconfirmed')
        page=self.page()+'<a href="screener?f=sec_financial">Financial</a>'
        self.assertIsNone(so.finviz_classification(page,'AAPL')[0])

    def test_indirect_label_text_is_not_direct_sector(self):
        self.assertIsNone(so.finviz_classification('<title>AAPL - Apple</title>Technology NASDAQ','AAPL')[0])

    def test_freshness_explicit_timezones(self):
        self.assertEqual(so.age_issue('2026-09-25T01:00:00+09:00','2026-09-24T15:00:00+00:00'),'future_observation')
        self.assertEqual(so.age_issue('2026-09-01T01:00:00+09:00','2026-09-24T15:00:00+00:00'),'stale_classification')
        with self.assertRaises(ValueError):so.stamp('2026-09-25')

    def fixture(self,code='KGS09P',label='코스피 전기·전자'):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root=Path(tmp.name)
        (root/'sources').mkdir();(root/'kr-probes').mkdir()
        records=[]
        for name in ('m_new_stock.h','m_new_stock.mst'):
            path=root/name;path.write_text('fixture',encoding='utf-8')
            records.append({'path':name,'sha256':so.sha(path),'fetched_at':'2026-09-24T00:00:00+00:00'})
        so.write(root/'sources-manifest.json',records)
        self.original=[{'market':'KR','exchange':'KOSPI','symbol':'005930.KS','provider_symbol':'005930',
                        'isin_code':'KR_TEST','name':'삼성전자','state':'matched','source_industry_name':'원 업종'}]
        self.master=[{'sCode':'005930','sMarket':'1','sUpCodeM':'KGS09P','sUpCodeL':'KGS18P','sUpCodeS':'','sKorName':'삼성전자'}]
        so.write(root/'kr-probes/KGS09P.json',{'expected_code':'KGS09P','url':'https://example.invalid',
                 'observed_at':'2026-09-24T01:00:00+00:00','inputs':{'iem_cd':'005930','market_cd':'KRX'},
                 'payload':{'Output_0':{'iem_cd':'005930','bstp_cls_code':code,'bstp_kor_isnm':label}}})
        return root

    def grouped(self,root):
        with patch.object(so,'parse_master',return_value=self.master):
            return so.group_rows(self.original,root,root,'2026-09-25T00:00:00+00:00',{})[0]

    def test_original_preserved_and_separate_group(self):
        root=self.fixture();before=copy.deepcopy(self.original)
        row=self.grouped(root)
        self.assertEqual(row['group_name'],'코스피 전기·전자')
        self.assertEqual(row['original_classification'],before[0])
        self.assertEqual(self.original,before)

    def test_nh_code_mismatch_never_falls_back_to_parent(self):
        row=self.grouped(self.fixture('KGS14P','코스피 금융'))
        self.assertEqual(row['reason'],'nh_master_quote_code_conflict')
        self.assertIsNone(row['group_name'])

    def test_original_type_conflict_blocks_mapping(self):
        root=self.fixture();self.original[0].update(state='conflict',reason='security_type_conflict')
        self.assertEqual(self.grouped(root)['reason'],'original_conflict:security_type_conflict')

    def test_blank_code_unmatched(self):
        root=self.fixture();self.master[0].update(sUpCodeM='',sUpCodeL='')
        self.assertEqual(self.grouped(root)['reason'],'nh_code_name_not_verified')

    def test_domestic_duplicate_identity_unmatched(self):
        root=self.fixture();self.master.append(copy.deepcopy(self.master[0]))
        self.assertEqual(self.grouped(root)['reason'],'nh_domestic_identity_absent_or_duplicate')

    def test_input_tamper_detected(self):
        root=self.fixture();(root/'m_new_stock.mst').write_text('tamper',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'input_hash_mismatch'):self.grouped(root)

    def parent_fixture(self):
        root=self.fixture('KGS14P','코스피 금융')
        self.master[0].update(sUpCodeM='KGS15P',sUpCodeL='KGS14P')
        middle=so.load(root/'kr-probes/KGS09P.json');middle['expected_code']='KGS15P'
        (root/'kr-probes/KGS09P.json').unlink()
        so.write(root/'kr-probes/KGS15P.json',middle)
        parent=copy.deepcopy(middle);parent['expected_code']='KGS14P'
        so.write(root/'kr-probes/KGS14P.json',parent)
        direct=copy.deepcopy(parent);direct['url']='https://api.nhplug.com:8443/krstock/quote/v1/currentPrice'
        (root/'kr-individual').mkdir();so.write(root/'kr-individual/005930.json',direct)
        return root

    def test_parent_requires_individual_direct_evidence(self):
        row=self.grouped(self.parent_fixture())
        self.assertEqual(row['group_code'],'KGS14P')
        self.assertEqual(row['nh_master_codes']['sUpCodeM'],'KGS15P')
        self.assertTrue(row['individual_review']['parent_confirmed'])
        self.assertIn('middle_label_unverified',row['warnings'][0])

    def test_parent_must_be_exact_master_parent(self):
        root=self.parent_fixture();self.master[0]['sUpCodeL']='KGS18P'
        self.assertEqual(self.grouped(root)['group_state'],'unmatched')

    def test_market_code_is_not_sector(self):
        root=self.parent_fixture();self.master[0]['sUpCodeL']='KGG01P'
        path=root/'kr-individual/005930.json';data=so.load(path)
        data['payload']['Output_0'].update(bstp_cls_code='KGG01P',bstp_kor_isnm='코스피');so.write(path,data)
        self.assertEqual(self.grouped(root)['group_state'],'unmatched')

    def test_parent_duplicate_or_wrong_individual_identity_blocks(self):
        root=self.parent_fixture();path=root/'kr-individual/005930.json';data=so.load(path)
        so.write(root/'kr-individual/duplicate.json',data)
        self.assertEqual(self.grouped(root)['group_state'],'unmatched')
        (root/'kr-individual/duplicate.json').unlink()
        data['payload']['Output_0']['iem_cd']='999999';so.write(path,data)
        self.assertEqual(self.grouped(root)['group_state'],'unmatched')

    def test_parent_label_conflict_and_stale_evidence_block(self):
        root=self.parent_fixture();path=root/'kr-individual/005930.json';data=so.load(path)
        data['payload']['Output_0']['bstp_kor_isnm']='코스피 다른명';so.write(path,data)
        self.assertEqual(self.grouped(root)['group_state'],'unmatched')
        data['payload']['Output_0']['bstp_kor_isnm']='코스피 금융'
        data['observed_at']='2026-09-01T00:00:00+00:00';so.write(path,data)
        self.assertEqual(self.grouped(root)['group_state'],'unmatched')

    def test_parent_does_not_override_original_type_conflict(self):
        root=self.parent_fixture();self.original[0].update(state='conflict',reason='security_type_conflict')
        self.assertEqual(self.grouped(root)['reason'],'original_conflict:security_type_conflict')


if __name__=='__main__':unittest.main()
