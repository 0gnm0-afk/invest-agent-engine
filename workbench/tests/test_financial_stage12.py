import io
import sys
import unittest
import zipfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from financial_cohort import retain,select_bundles
from financial_model import fact,latest_dart_fact
from financial_focus import compatible,ratio
from financial_dart_notes import note_interest
from financial_sec_filing import inline_facts,untagged_annual,enrich_sec


class Stage12(unittest.TestCase):
    def cell(self,value,filing='A',**extra):
        return fact(value,unit='USD',start='2025-01-01',end='2025-03-31',filed='2025-05-01',accession=filing,**extra)

    def test_bundle_retains_changed_latest_but_never_mixes_ratio(self):
        old=self.cell(20);new=self.cell(30,'B')
        v={'sales':self.cell(100),'income':retain(new,[old,new])}
        select_bundles(v)
        self.assertEqual(v['income']['value'],20)
        self.assertEqual(v['income']['latest_disclosure']['value'],30)
        self.assertEqual(ratio(v,'net_margin')['value'],.2)

    def test_latest_complete_bundle_wins(self):
        v={k:retain(self.cell(n,'B'),[self.cell(n-1),self.cell(n,'B')]) for k,n in [('sales',100),('income',20)]}
        select_bundles(v);self.assertEqual(v['income']['value'],20)

    def test_conflicting_same_filing_is_not_arbitrarily_selected(self):
        v={'sales':self.cell(100),'income':retain(self.cell(30,'B'),[self.cell(20),self.cell(21)])}
        select_bundles(v);self.assertIsNone(ratio(v,'net_margin')['value'])

    def test_wrong_period_and_scope_cannot_become_bundle(self):
        wrong=self.cell(20);wrong['start']='2024-01-01'
        v={'sales':self.cell(100),'income':retain(self.cell(30,'B'),[wrong])}
        select_bundles(v);self.assertIsNone(ratio(v,'net_margin')['value'])
        a=self.cell(1,accounting_basis='CFS');b=self.cell(2,accounting_basis='OFS')
        self.assertIsNotNone(compatible(a,b,same_period=True))

    def test_revision_series_keys_are_not_cross_account_filing_identity(self):
        a=self.cell(1,revision_basis='sales-series');b=self.cell(2,revision_basis='income-series')
        self.assertIsNone(compatible(a,b,same_period=True))
        self.assertIsNotNone(compatible(a,b))

    def test_derived_quarters_keep_operand_filing_cohorts(self):
        from financial_model import period_difference
        total=self.cell(100);total.update(start='2025-01-01',end='2025-12-31')
        previous=self.cell(60,'Q');previous.update(start='2025-01-01',end='2025-09-30')
        a=period_difference('difference',total,previous,'USD')
        b=period_difference('difference',dict(total,value=20),dict(previous,value=10),'USD')
        self.assertIsNone(compatible(a,b,same_period=True))
        b['period_disclosures'][1]['filings']=['another-revision']
        self.assertIsNotNone(compatible(a,b,same_period=True))

    def test_dart_history_preserves_full_candidate_evidence(self):
        target={}
        for receipt,val in [('20240101000000',1),('20250101000000',2)]:
            latest_dart_fact(target,'income',fact(val,unit='KRW',start='2023-01-01',end='2023-12-31',receipt=receipt,filed=receipt[:8]))
        self.assertEqual({c['value'] for c in target['income']['disclosure_candidates']},{1,2})

    def test_note_comparative_quarter_and_ytd_are_separate(self):
        xml='<DOC><TITLE>3. 연결재무제표 주석</TITLE><P>금융비용 (단위: 천원)</P><TABLE><TR><TH>구분</TH><TH>당분기 3개월</TH><TH>누적</TH><TH>전분기 3개월</TH><TH>누적</TH></TR><TR><TD>이자비용</TD><TD>10</TD><TD>30</TD><TD>8</TD><TD>24</TD></TR></TABLE></DOC>'
        b=io.BytesIO()
        with zipfile.ZipFile(b,'w') as z:z.writestr('20251114000001.xml',xml)
        report={'year':2025,'quarter':3,'fs':{'list':[{'rcept_no':'20251114000001'}]}}
        c=note_interest(b.getvalue(),report,'CFS','note.zip')[0]
        self.assertEqual((c['direct'],c['cumulative'],c['previous_direct'],c['previous_cumulative']),(10000,30000,8000,24000))

    def test_custom_tag_requires_exact_semantics_entity_context_and_usd(self):
        raw='''<xbrli:context id="c"><xbrli:entity><xbrli:identifier>123</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:startDate>2025-01-01</xbrli:startDate><xbrli:endDate>2025-03-31</xbrli:endDate></xbrli:period></xbrli:context><xbrli:unit id="u"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit><table><tr><td>Total interest expense</td><td><ix:nonFraction name="issuer:Cost" contextRef="c" unitRef="u" scale="3">12</ix:nonFraction></td></tr></table>'''
        f=dict(accessionNumber='A',filingDate='2025-05-01',form='10-Q')
        x=inline_facts(raw,123,f,'a')[0];self.assertEqual(x['val'],12000);self.assertEqual(x['tag'],'InterestExpense');self.assertEqual(x['custom_mapping']['original_tag'],'issuer:Cost')
        for bad in ['Cash interest paid','Interest expense on leases','Net interest expense']:
            self.assertEqual(inline_facts(raw.replace('Total interest expense',bad),123,f,'a'),[])
        self.assertEqual(inline_facts(raw,999,f,'a'),[])
        self.assertEqual(inline_facts(raw.replace('iso4217:USD','iso4217:EUR'),123,f,'a'),[])
        self.assertEqual(inline_facts(raw.replace('issuer:Cost','issuer:FinanceLeaseInterestExpense'),123,f,'a'),[])
        self.assertEqual(inline_facts(raw.replace('<table>','<table><tr><td>Finance lease</td></tr>'),123,f,'a'),[])

    def test_profit_reconciliation_does_not_take_cash_interest_payment(self):
        xml='<DOC><TITLE>3. 연결재무제표 주석</TITLE><P>(단위: 천원)</P><TABLE><TR><TD>구분</TD><TD>당기</TD><TD>전기</TD></TR><TR><TD>영업에서 창출된 현금흐름 · 당기순이익 · 현금의 유출이 없는 비용등의 가산</TD></TR><TR><TD>이자비용</TD><TD>10</TD><TD>8</TD></TR><TR><TD>이자의 지급</TD><TD>90</TD><TD>80</TD></TR></TABLE></DOC>'
        b=io.BytesIO()
        with zipfile.ZipFile(b,'w') as z:z.writestr('20260301000001.xml',xml)
        r={'year':2025,'quarter':4,'fs':{'list':[{'rcept_no':'20260301000001'}]}}
        c=note_interest(b.getvalue(),r,'CFS','note.zip')[0]
        self.assertEqual((c['direct'],c['previous']),(10000,8000))

    def test_unsupported_taxonomy_has_actionable_diagnostic(self):
        source={'identity':{'cik':123},'facts':{'facts':{'unknown-standard':{}}}}
        result=enrich_sec(None,source)
        self.assertEqual(result['format_issues'][0]['code'],'unsupported_taxonomy')
        self.assertIn('코드 수정',result['format_issues'][0]['message'])

    def test_untagged_interim_cannot_be_mislabeled_as_annual(self):
        raw='<p>Test Inc.</p><p>Consolidated Statements of Operations</p><p>Six Months Ended June 30, 2025, 2024</p><p>(in thousands)</p><table><tr><td>2025</td><td>2024</td></tr><tr><td>Total revenues</td><td>100</td><td>90</td></tr></table>'
        f=dict(accessionNumber='A',filingDate='2025-08-01',form='10-Q')
        self.assertEqual(untagged_annual(raw,{'name':'Test Inc.'},f,'a'),[])

    def test_annual_gaps_do_not_expand_quarter_fetch_window(self):
        from unittest.mock import patch
        class Collector:
            def __init__(self):self.names=[]
            def fetch(self,url,name,**kwargs):self.names.append(name);return b'<html></html>'
        c=Collector();filings=[dict(form='10-Q',reportDate=end,filingDate=end,accessionNumber=acc,primaryDocument='a.htm')
            for end,acc in [('2021-09-30','old'),('2025-09-30','needed')]]
        source=dict(identity=dict(cik=123,name='Test Inc.'),facts={'facts':{'us-gaap':{'Assets':{}}}},filing_records=filings)
        gaps=[dict(frame='annual',date='2021-12-31',field='dividend'),dict(frame='quarterly',date='2025-09-30',field='interest_expense')]
        with patch('financial_sec_filing.enrichment_gaps',return_value=(gaps,False)),patch('financial_sec_filing.enrich_quarter_eps',side_effect=lambda c,s:s):
            enrich_sec(c,source)
        self.assertEqual(c.names,['needed-statement.html'])


if __name__=='__main__':unittest.main()
