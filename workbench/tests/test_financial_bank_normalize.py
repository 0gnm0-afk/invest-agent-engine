"""Focused DART period, share-class, cash and annual EPS supplement regressions."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from financial_model import dart_normalize,dart_share_values,summarize,latest_dart_fact,fact

IDENTITY=dict(corp_code='001',stock_code='000001',corp_name='Example',acc_mt='12')
TAGS={'eps':'ifrs-full_DilutedEarningsLossPerShare','basic':'ifrs-full_BasicEarningsLossPerShare',
      'income':'ifrs-full_ProfitLossAttributableToOwnersOfParent','sales':'ifrs-full_Revenue',
      'cash':'ifrs-full_CashAndCashEquivalents','cfo':'ifrs-full_CashFlowsFromUsedInOperatingActivities',
      'equity':'ifrs-full_EquityAttributableToOwnersOfParent'}

def report(year,quarter=4,**extra):
    end=f'{year}-{quarter*3:02d}-'+('31' if quarter in (1,4) else '30')
    return dict(year=year,quarter=quarter,end=end,prefix=f'{year}-Q{quarter}',fs=dict(status='000',list=[]),shares=dict(status='000',list=[]),**extra)

def row(report,key,value,**extra):
    y,q=report['year'],report['quarter'];term=y-1960
    suffix='' if q==4 else ' 반기' if q==2 else f' {q}분기'
    result=dict(corp_code='001',currency='KRW',bsns_year=str(y),reprt_code={1:'11013',2:'11012',3:'11014',4:'11011'}[q],
        sj_div='CF' if key=='cfo' else 'BS' if key in ('cash','equity') else 'IS',account_id=TAGS[key],account_nm=key,
        rcept_no=f'{y+1}0315000001' if q==4 else f'{y}{q*3+2:02d}15000001',thstrm_nm=f'제 {term} 기'+suffix,
        thstrm_amount=str(value))
    result.update(extra);return result

def source(*reports,**extra):return dict(identity=IDENTITY,reports=list(reports),**extra)
def result(data):
    normalized=dart_normalize(data)
    calculated=summarize(normalized,dict(bars=[dict(date='2026-09-30',close=100,high=101,low=99,volume=10)]))
    return normalized,calculated,{c['id']:c for c in calculated['summary']}
def shares(rep,name='보통주',count='90'):
    return dict(corp_code='001',se=name,stlm_dt=rep['end'],distb_stock_co=count,istc_totqy='100',tesstk_co='10',rcept_no='20260814000001')


class DartNormalization(unittest.TestCase):
    def test_comparative_year_and_latest_filing_are_not_current_year(self):
        older=report(2022);older['fs']['list']=[row(older,'eps',120)]
        newer=report(2024);newer['fs']['list']=[row(newer,'eps',200,frmtrm_nm='제 63 기',frmtrm_amount='150',bfefrmtrm_nm='제 62 기',bfefrmtrm_amount='101')]
        n=dart_normalize(source(newer,older));byyear={r['fy']:r for r in n['annual']}
        self.assertEqual(byyear[2024]['values']['eps']['value'],200)
        self.assertEqual(byyear[2022]['values']['eps']['value'],101)
        self.assertEqual(byyear[2022]['values']['eps']['report_column'],'bfefrmtrm_amount')
        self.assertEqual(byyear[2022]['values']['eps']['previous_disclosures'][0]['value'],120)

    def test_unconfirmed_comparative_period_is_not_invented(self):
        rep=report(2025);rep['fs']['list']=[row(rep,'eps',10,frmtrm_nm='제 62 기',frmtrm_amount='8')]
        self.assertEqual([r['fy'] for r in dart_normalize(source(rep))['annual']],[2025])

    def test_prior_quarter_income_and_cumulative_cashflow_are_distinct(self):
        q1=report(2025,1);q1['fs']['list']=[row(q1,'sales',40),row(q1,'cfo',100)]
        q2=report(2026,2);q2['fs']['list']=[row(q2,'sales',60,thstrm_add_amount='110',frmtrm_q_nm='제 65 기 반기',frmtrm_q_amount='50',frmtrm_add_amount='90'),row(q2,'cfo',500,frmtrm_q_nm='제 65 기 반기',frmtrm_q_amount='300')]
        previous=next(r for r in dart_normalize(source(q1,q2))['quarterly'] if r['date']=='2025-06-30')
        self.assertEqual(previous['values']['sales']['value'],50)
        self.assertEqual(previous['values']['cfo']['value'],200)
        self.assertEqual(previous['values']['cfo']['start'],'2025-04-01')

    def test_cashflow_ending_cash_only_not_beginning_or_bank_deposits(self):
        rep=report(2026,2);rep['fs']['list']=[row(rep,'eps',10),row(rep,'cash',999,account_nm='현금 및 예치금')]
        rep['fs']['list'][1]['account_id']='cash_deposits'
        for label,value in [('기초의 현금 및 현금성자산',50),('반기말의 현금 및 현금성자산',85)]:
            candidate=row(rep,'cash',value);candidate.update(sj_div='CF',account_nm=label)
            rep['fs']['list'].append(candidate)
        cash=dart_normalize(source(rep))['quarterly'][-1]['values']['cash']
        self.assertEqual(cash['value'],85);self.assertIsNone(cash['start'])

    def test_shares_survive_financial_013_and_keep_their_actual_date(self):
        annual=report(2025);annual['fs']['list']=[row(annual,'eps',10)]
        newer=report(2026,1);newer['fs']=dict(status='013');newer['shares']['list']=[shares(newer,'의결권이 있는 보통주')]
        _,r,cells=result(source(annual,newer))
        self.assertEqual(cells['shares']['value'],90);self.assertEqual(cells['shares']['end'],'2026-03-31')
        self.assertEqual(r['financial_as_of'],'2025-12-31');self.assertEqual(cells['market_cap']['value'],9000)

    def test_ambiguous_share_class_and_dash_are_not_zero_or_common(self):
        rep=report(2025);rep['fs']['list']=[row(rep,'eps',10)];rep['shares']['list']=[shares(rep,'의결권 있는 주식')]
        self.assertIsNone(dart_normalize(source(rep))['share_history'][0]['values']['shares']['value'])
        rep['shares']['list']=[shares(rep,'보통주','-')]
        self.assertIsNone(dart_normalize(source(rep))['share_history'][0]['values']['shares']['value'])

    def test_bank_applicability_and_unknown_preferred_block_common_pb(self):
        rep=report(2025);rep['fs']['list']=[row(rep,'eps',10),row(rep,'equity',1000)]
        bank=row(rep,'sales',50);bank.update(account_id='ifrs-full_InterestRevenueExpense',account_nm='순이자손익');rep['fs']['list'].append(bank)
        rep['shares']['list']=[shares(rep,'의결권 있는 보통주'),shares(rep,'의결권 없는 우선주','-')]
        _,_,cells=result(source(rep))
        self.assertIsNone(cells['pb']['value']);self.assertIn('보통주자본',cells['pb']['label'])
        self.assertIsNone(cells['all_market_cap']['value']);self.assertEqual(cells['market_cap']['value'],9000)
        for key in ('fcf','pfcf','current','quick','evsales'):
            self.assertEqual(cells[key]['status'],'not_applicable');self.assertIsNone(cells[key]['value'])

    def test_unknown_preferred_zero_requires_same_filing_and_date_total(self):
        for change,expected in (({},0),({'distb_stock_co':'95'},None),
                                ({'distb_stock_co':'-'},None),({'stlm_dt':'2024-12-31'},None),
                                ({'rcept_no':'20250814000001'},None)):
            with self.subTest(change=change):
                rep=report(2025)
                total=shares(rep,'합계');total.update(change)
                rep['shares']['list']=[shares(rep),shares(rep,'우선주','-'),total]
                preferred=dart_share_values(rep,'001')['preferred_shares']
                self.assertEqual(preferred['value'],expected)
                self.assertEqual(rep['shares']['list'][1]['distb_stock_co'],'-')
                if expected==0:
                    self.assertEqual(preferred['reported_value'],'-')
                    self.assertIn('합계',preferred['formula'])
                    self.assertEqual([v['value'] for v in preferred['inputs']],[90,90])

    def test_same_filing_conflict_remains_missing_after_duplicate(self):
        target={}
        for value in (10,11,10):latest_dart_fact(target,'eps',fact(value,filed='2026-01-01',receipt='same'))
        self.assertIsNone(target['eps']['value'])


class AnnualSupplement(unittest.TestCase):
    def data(self):
        rep=report(2025);rep['fs']['list']=[row(rep,'eps',300,frmtrm_nm='제 64 기',frmtrm_amount='290',bfefrmtrm_nm='제 63 기',bfefrmtrm_amount='280')]
        supplements=[]
        for year in range(2020,2026):
            receipt=f'{year+1}0315000001'
            rows=[dict(corp_code='001',se='(연결)주당순이익(원)',stock_knd='-',stlm_dt=f'{year}-12-31',rcept_no=receipt,thstrm=str(100+20*(year-2020))),
                  dict(corp_code='001',se='주당 현금배당금(원)',stock_knd='보통주',stlm_dt=f'{year}-12-31',rcept_no=receipt,thstrm='3')]
            supplements.append(dict(year=year,quarter=4,end=f'{year}-12-31',prefix=f'{year}-Q4',endpoint='alotMatter',source_id=f'{year}-Q4-dividends.json',raw=dict(status='000',list=rows)))
        return source(rep,annual_supplements=supplements)

    def test_reported_series_cagr_does_not_overwrite_diluted_eps(self):
        _,r,cells=result(self.data())
        self.assertEqual(cells['annual_eps']['value'],300)
        self.assertEqual(r['annual'][-1]['values']['reported_eps']['value'],200)
        # A common endpoint is not evidence of an unchanged accounting basis.
        self.assertIsNone(cells['eps_cagr5']['value'])
        self.assertIn('회계 기준',cells['eps_cagr5']['reason'])
        self.assertEqual(cells['eps_cagr5']['eps_basis'],'reported')
        self.assertEqual(cells['eps_cagr5']['label'],'공시 EPS 5년 CAGR')
        self.assertEqual(cells['eps_cagr5']['status'],'unavailable')

    def test_incomplete_reported_series_cannot_be_mixed_to_fill_cagr(self):
        data=self.data();data['annual_supplements']=[r for r in data['annual_supplements'] if r['year']!=2022]
        _,_,cells=result(data);self.assertIsNone(cells['eps_cagr5']['value'])

    def test_annual_dividend_fallback_labels_period_and_uses_same_year_eps(self):
        data=self.data();latest=report(2026,1);latest['fs']['list']=[row(latest,'eps',80)];data['reports'].append(latest)
        _,_,cells=result(data)
        self.assertEqual(cells['dividend']['value'],3)
        self.assertIn('최근 연간',cells['dividend']['label'])
        self.assertEqual(cells['yield']['value'],.03)
        self.assertEqual(cells['payout']['value'],.01)


if __name__=='__main__':unittest.main()
