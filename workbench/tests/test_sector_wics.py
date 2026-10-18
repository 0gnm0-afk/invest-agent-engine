import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import sector_observation as so
import wics_classification as wics
import test_sector_observation as fixtures


class WicsTest(unittest.TestCase):
    def setUp(self):
        self.record = {'code':'G30','name':'필수소비재','symbol':'030720',
                       'observed_at':'2026-09-25T07:00:00+09:00','classification_as_of':'2026-09-23'}
        self.row = {'CMP_CD':'030720','CMP_KOR':'동원수산','IDX_CD':'G30','SEC_CD':'G30','SEC_NM_KOR':'필수소비재'}
        self.payload = {'info':{'TRD_DT':'/Date(1790089200000)/','CNT':1},'list':[self.row]}
        self.applied = '2026-09-25T08:00:00+09:00'

    def test_component_date_is_korean_midnight_not_utc_previous_day(self):
        self.assertEqual(wics.components(self.payload,'G30','필수소비재','2026-09-23'),[self.row])
        with self.assertRaisesRegex(ValueError,'date_or_count'):
            wics.components(self.payload,'G30','필수소비재','2026-09-22')

    def test_truncated_components_cannot_claim_complete_response(self):
        self.payload['info']['CNT']=2
        with self.assertRaisesRegex(ValueError,'date_or_count'):
            wics.components(self.payload,'G30','필수소비재','2026-09-23')

    def test_source_sector_or_identity_conflict_is_not_relabelled(self):
        for key,value in [('SEC_CD','G25'),('IDX_CD','G25'),('SEC_NM_KOR','다른업종'),('CMP_CD','A030720')]:
            payload=copy.deepcopy(self.payload);payload['list'][0][key]=value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError,'identity_or_sector'):
                wics.components(payload,'G30','필수소비재','2026-09-23')

    def test_missing_direct_source_does_not_mean_external_unclassified(self):
        self.assertEqual(wics.select_wics([],self.applied,so.age_issue),(None,'wics_direct_evidence_absent'))

    def test_duplicate_even_identical_assignment_is_unmatched(self):
        self.assertEqual(wics.select_wics([self.record]*2,self.applied,so.age_issue)[1],
                         'wics_duplicate_or_conflicting_assignment')

    def test_old_component_date_not_refreshed_by_new_download(self):
        self.record['classification_as_of']='2026-08-01'
        self.assertEqual(wics.select_wics([self.record],self.applied,so.age_issue)[1],'stale_classification')

    def test_future_download_cannot_be_used_earlier(self):
        self.record['observed_at']='2026-09-26T08:00:00+09:00'
        self.assertEqual(wics.select_wics([self.record],self.applied,so.age_issue)[1],'future_observation')

    def test_future_taxonomy_cannot_be_used_with_earlier_components(self):
        self.record['context_observed_at']=['2026-09-26T08:00:00+09:00']
        self.assertEqual(wics.select_wics([self.record],self.applied,so.age_issue)[1],'future_observation')

    def test_company_observation_does_not_invent_effective_date(self):
        self.record['classification_as_of']=None
        self.assertIsNone(wics.select_wics([self.record],self.applied,so.age_issue)[1])

    def test_independent_agreement_allowed_but_conflict_blocks(self):
        self.record['path']='components'
        other={**self.record,'path':'company','classification_as_of':None}
        self.assertIsNone(wics.select_wics([self.record,other],self.applied,so.age_issue)[1])
        other.update(code='G25',name='경기관련소비재')
        self.assertEqual(wics.select_wics([self.record,other],self.applied,so.age_issue)[1],
                         'wics_duplicate_or_conflicting_assignment')

    def test_published_industry_hierarchy_and_exact_stock_identity(self):
        taxonomy=b'<table class="sector"><tr><td>30</td><td>Staples</td><td>3020</td><td>Food</td><td>302020</td><td>Food</td></tr></table>'
        hierarchy=wics.industry_hierarchy(taxonomy,{'G30':'Staples'})
        page=b'<input id="hd_cmp_cd" value="030720"><input id="hidCMP_CD" value="030720"><dt><b class="num">030720</b><span class="name">Company</span></dt><b class="num">123.45</b><dt class="line-left">KOSPI : Other</dt><dt class="line-left">WICS : Food</dt>'
        row=wics.company_industry(page,'030720',hierarchy)
        self.assertEqual((row['code'],row['industry_code']),('G30','302020'))
        for invalid in (page.replace(b'WICS',b'WI26'),page.replace(b'030720',b'030721'),page.replace(b'KOSPI',b'KOSDAQ'),page.replace(b'Food',b'Unknown')):
            with self.assertRaises(ValueError):wics.company_industry(invalid,'030720',hierarchy)

    def test_hierarchy_ambiguity_not_guessed(self):
        with self.assertRaisesRegex(ValueError,'ambiguous'):
            wics.industry_hierarchy(b'<table class="sector"><tr><td>302020</td><td>Food</td></tr><tr><td>302030</td><td>Food</td></tr></table>',{'G30':'Staples'})

    def test_only_direct_top_level_children_are_taxonomy(self):
        rows=[{'key':'G'+str(k),'title':str(k),'isIndex':True} for k in (10,15,20,25,30,35,40,45,50,55)]
        rows[0]['children']=[{'key':'G1010','title':'lower','isIndex':True}]
        self.assertEqual(len(wics.top_sectors([{'title':'WICS','children':rows}])),10)
        rows[0]['key']='G1010'
        with self.assertRaisesRegex(ValueError,'top_level'):
            wics.top_sectors([{'title':'WICS','children':rows}])


class WicsMappingTest(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.MappingTest()
        self.addCleanup(self.fixture.doCleanups)
        self.root=self.fixture.fixture()

    def grouped(self,records):
        with patch.object(so,'load_wics',return_value={'records':records}):
            return self.fixture.grouped(self.root)

    def test_wics_overrides_nh_basket_but_preserves_original_and_codes(self):
        before=copy.deepcopy(self.fixture.original)
        row=self.grouped({'005930':[{'code':'G45','name':'IT','observed_at':'2026-09-24T01:00:00+00:00',
                                   'classification_as_of':'2026-09-23'}]})
        self.assertEqual(row['group_taxonomy'],'KR_WICS_SECTOR')
        self.assertEqual(row['group_name'],'IT')
        self.assertEqual(row['nh_master_codes']['sUpCodeM'],'KGS09P')
        self.assertEqual(row['original_classification'],before[0])

    def test_no_wics_record_never_falls_back_to_nh_or_kind(self):
        row=self.grouped({})
        self.assertEqual(row['reason'],'wics_direct_evidence_absent')
        self.assertIsNone(row['group_name'])

    def test_original_type_conflict_still_blocks_wics(self):
        self.fixture.original[0].update(state='conflict',reason='security_type_conflict')
        self.assertEqual(self.grouped({})['reason'],'original_conflict:security_type_conflict')


if __name__=='__main__':unittest.main()
