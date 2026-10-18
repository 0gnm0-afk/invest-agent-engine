import io
import sys
import unittest
import zipfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from financial_business import eligibility,coverage
from financial_dart_notes import note_interest,statement_periods
from financial_sec_filing import untagged_annual,quarter_eps,inline_facts,balance_equation
from financial_revenue import select_sec_revenue
from financial_focus import compatible,ratio,summarize
from financial_model import latest_dart_fact,fact


class BusinessCoverage(unittest.TestCase):
    def test_trust_and_spac_excluded_but_bank_holding_is_business(self):
        self.assertEqual(eligibility({'sic':'6189','entityType':'operating'},'SEC')['status'],'unsupported')
        self.assertEqual(eligibility({'sic':'6770'},'SEC')['status'],'unsupported')
        self.assertEqual(eligibility({'sic':'6022','name':'Bank Holding'},'SEC')['status'],'eligible')
        self.assertEqual(eligibility({'corp_name':'테스트스팩'},'DART')['status'],'unsupported')

    def test_bdc_taxonomy_even_without_sic(self):
        facts={'facts':{'us-gaap':{'InvestmentCompanyNetAssetsFromOperationsIncreaseDecrease':{}}}}
        self.assertEqual(eligibility({'sic':''},'SEC',facts)['status'],'unsupported')

    def test_revenue_including_tax_is_available_without_excluding_tax(self):
        x=dict(start='2025-01-01',end='2025-12-31',form='10-K',filed='2026-03-01',accn='a',val=100)
        result=select_sec_revenue({'RevenueFromContractWithCustomerIncludingAssessedTax':{'units':{'USD':[x]}}},cutoff='2020-01-01',as_of='2026-10-08')
        self.assertEqual(result[0]['val'],100)
        self.assertIn('including_assessed_tax',result[0]['comparison_key'])

    def test_only_unchanged_values_share_filing_lineage(self):
        target={};base=dict(unit='KRW',end='2025-12-31',start='2025-01-01')
        for receipt,value in [('20260301000000',50),('20260302000000',50)]:
            latest_dart_fact(target,'a',fact(value,receipt=receipt,filed=receipt[:8],**base))
        old=fact(5,receipt='20260301000000',**base)
        self.assertIsNone(compatible(target['a'],old,same_period=True))
        latest_dart_fact(target,'a',fact(70,receipt='20260303000000',filed='20260303',**base))
        self.assertIn('버전',compatible(target['a'],old,same_period=True))

    def test_zero_denominator_is_undefined_not_missing_source(self):
        a=fact(10,unit='KRW',end='2025-12-31');b=fact(0,unit='KRW',end='2025-12-31')
        c=ratio(dict(current_assets=a,current_liabilities=b),'current_ratio')
        self.assertEqual(c['status'],'not_computable');self.assertIsNone(c['value'])

    def test_dividend_only_cannot_be_successful_focus_report(self):
        with self.assertRaisesRegex(ValueError,'OFFICIAL_PERIODS'):
            summarize(dict(provider='DART',annual=[dict(values={'dividend':fact(10)})],quarterly=[]),{})


class DartNotes(unittest.TestCase):
    def archive(self,text):
        b=io.BytesIO()
        with zipfile.ZipFile(b,'w') as z:z.writestr('20260814000001.xml','<ROOT>'+text+'</ROOT>')
        return b.getvalue()

    def report(self):return dict(year=2026,quarter=2,fs={'list':[{'rcept_no':'20260814000001'}]})

    def test_signed_expense_nested_finance_table_and_scope(self):
        table='<TABLE><TR><TD>(단위:천원)</TD></TR><TR><TD>구분</TD><TD>당반기 3개월</TD><TD>누적</TD><TD>전반기 3개월</TD><TD>누적</TD></TR><TR><TD>- 이자비용</TD><TD>(20)</TD><TD>(35)</TD><TD>(10)</TD><TD>(25)</TD></TR><TR><TD>순금융원가</TD></TR></TABLE>'
        raw=self.archive('<TITLE>3. 연결재무제표 주석</TITLE><TABLE><TR><TD><P>금융수익과 금융비용</P>'+table+'</TD></TR></TABLE><TITLE>5. 재무제표 주석</TITLE><P>금융수익과 금융비용</P>'+table.replace('(20)','(999)'))
        xs=note_interest(raw,self.report(),'CFS','source.zip')
        self.assertEqual(len(xs),1);self.assertEqual(xs[0]['direct'],20000);self.assertEqual(xs[0]['cumulative'],35000)

    def test_period_change_is_read_from_explicit_dates(self):
        raw=self.archive('<TABLE><TR><TD>연결 포괄손익계산서 제35기 2023.07.01 부터 2023.12.31 까지 제34기 2022.07.01 부터 2023.06.30 까지</TD></TR></TABLE>')
        periods=statement_periods(raw,'CFS')
        self.assertEqual(periods,[dict(start='2023-07-01',end='2023-12-31'),dict(start='2022-07-01',end='2023-06-30')])

    def test_cash_interest_or_lease_only_is_not_total_interest(self):
        raw=self.archive('<TITLE>3. 연결재무제표 주석</TITLE><TABLE><TR><TD>(단위:원) 금융비용 당반기 전반기</TD></TR><TR><TD>리스부채 관련 이자비용</TD><TD>30</TD><TD>20</TD></TR></TABLE>')
        self.assertEqual(note_interest(raw,self.report(),'CFS','source.zip'),[])

    def test_profit_reconciliation_expense_is_not_cash_interest_paid(self):
        raw=self.archive('<TITLE>3. 연결재무제표 주석</TITLE><TABLE><TR><TD>(단위:원) 영업활동에서 창출된 현금 (2)조정 당반기 전반기</TD></TR><TR><TD>이자비용</TD><TD>35</TD><TD>25</TD></TR><TR><TD>이자지급</TD><TD>999</TD><TD>888</TD></TR><TR><TD>사외적립자산의 감소</TD><TD>1</TD><TD>2</TD></TR></TABLE>')
        xs=note_interest(raw,self.report(),'CFS','source.zip')
        self.assertEqual(len(xs),1);self.assertEqual(xs[0]['cumulative'],35)
        self.assertIsNone(xs[0]['direct']);self.assertEqual(xs[0]['presentation'],'profit_reconciliation')


class SecOriginal(unittest.TestCase):
    filing=dict(form='10-K',filingDate='2026-03-30',accessionNumber='a')

    def test_untagged_issuer_scope_and_per_share_units(self):
        def block(name):
            return '<p>'+name+'</p><p>Consolidated Statements of Operations and Comprehensive Loss</p><p>Years Ended December 31, 2025, 2024</p><p>(in thousands, except per share data)</p><table><tr><td>2025</td><td>2024</td></tr><tr><td>Total revenues</td><td>100</td><td>90</td></tr><tr><td>Basic and diluted loss per share</td><td>(0.66)</td><td>(1.35)</td></tr></table>'
        rows=untagged_annual(block('Test Holdings, Inc.')+block('Other Subsidiary'),{'name':'Test Holdings, Inc.'},self.filing,'a.html')
        self.assertEqual(len(rows),6)
        self.assertEqual(next(x['val'] for x in rows if x['tag']=='Revenues'),100000)
        self.assertEqual(next(x['val'] for x in rows if x['tag']=='EarningsPerShareBasic'),-.66)

    def test_quarter_eps_requires_gaap_period_and_explicit_basis(self):
        raw='<table><tr><td>Three Months Ended March 31</td><td>2026</td><td>2025</td><td>Year Ended</td><td>2026</td><td>2025</td></tr><tr><td>Net income per share</td></tr><tr><td>Basic</td><td>0.34</td><td>0.25</td><td>1.0</td><td>0.8</td></tr><tr><td>Diluted</td><td>0.33</td><td>0.24</td><td>0.9</td><td>0.7</td></tr></table>'
        xs=quarter_eps(raw,'2026-03-31',self.filing,'earnings.html')
        self.assertEqual([(x['eps_basis'],x['val']) for x in xs],[('basic',.34),('diluted',.33)])
        self.assertEqual(quarter_eps(raw.replace('Net income','Non-GAAP net income'),'2026-03-31',self.filing,'a'),[])
        self.assertEqual(quarter_eps(raw,'2026-06-30',self.filing,'a'),[])


if __name__=='__main__':unittest.main()
