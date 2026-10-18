"""Actual earnings comparisons, consistent EPS bases, and missing-period regressions."""
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from financial_model import fact, growth, actual_periods, actual_ttm, summarize, trailing, dart_normalize, sec_normalize


def period(year, quarter=0, *, sales=100, operating=20, income=10, eps=1, basic_eps=1.1):
    month=quarter*3 if quarter else 12
    end=f'{year}-{month:02d}-'+('31' if month in (3,12) else '30')
    start=f'{year}-{((quarter-1)*3+1) if quarter else 1:02d}-01'
    values={key:fact(value,unit='USD/share' if key in ('eps','basic_eps') else 'USD',start=start,end=end)
            for key,value in dict(sales=sales,operating=operating,income=income,eps=eps,basic_eps=basic_eps).items()}
    return dict(date=end,label=f'{year} Q{quarter}',values=values)


def normal():
    return dict(provider='SEC',currency='USD',annual=[period(y,eps=4,basic_eps=4.4) for y in range(2020,2026)],
                quarterly=[period(y,q) for y in range(2024,2027) for q in range(1,5) if y<2026 or q<=2])


def summary(data):
    result=summarize(data,dict(bars=[dict(date='2026-09-30',close=100,high=101,low=99,volume=100)]))
    return result,{cell['id']:cell for cell in result['summary']}


class GrowthRules(unittest.TestCase):
    def test_positive_change_and_zero_result(self):
        self.assertAlmostEqual(growth(fact(150,unit='USD'),fact(100,unit='USD'))['value'],.5)
        self.assertEqual(growth(fact(0,unit='USD'),fact(100,unit='USD'))['value'],-1)

    def test_profit_and_loss_states_never_misleading_percentages(self):
        cases=[(-5,10,'적자전환'),(5,-10,'흑자전환'),(-5,-10,'적자축소'),(-20,-10,'적자확대'),
               (-10,-10,'적자지속'),(0,-10,'적자축소'),(5,0,'비교불가'),(None,10,'비교불가')]
        for current,previous,label in cases:
            with self.subTest(current=current,previous=previous):
                result=growth(fact(current,unit='USD'),fact(previous,unit='USD'))
                self.assertIsNone(result['value']);self.assertEqual(result['display_text'],label)

    def test_cagr_requires_positive_endpoints_and_exact_years(self):
        self.assertAlmostEqual(growth(fact(133.1),fact(100),years=3)['value'],.1)
        self.assertIsNone(growth(fact(-1),fact(-2),years=3)['value'])
        data=normal();data['annual']=data['annual'][-3:]
        _,cells=summary(data)
        self.assertIsNone(cells['sales_cagr3']['value'])

    def test_unit_and_basis_changes_block_comparison(self):
        cases=[(fact(2,unit='USD'),fact(1,unit='KRW')),
               (fact(2,eps_basis='basic'),fact(1,eps_basis='diluted')),
               (fact(2,comparison_key='bank_net'),fact(1,comparison_key='gross'))]
        for a,b in cases:
            result=growth(a,b);self.assertIsNone(result['value']);self.assertEqual(result['display_text'],'비교불가')
            self.assertIsNone(growth(a,b,years=3)['value'])

    def test_missing_intermediate_quarter_does_not_shift_yoy(self):
        data=normal();data['quarterly']=[r for r in data['quarterly'] if r['date']!='2025-12-31']
        data['quarterly'][-1]['values']['operating']['value']=40
        result,cells=summary(data)
        self.assertEqual(cells['operating_growth_4']['value'],1)
        self.assertEqual(result['quarterly'][-1]['growth']['operating']['value'],1)
        self.assertIsNone(cells['operating_ttm_growth']['value'])

    def test_all_four_metrics_have_actual_growth_summaries(self):
        result,cells=summary(normal())
        self.assertEqual(cells['operating']['value'],80)
        for key in ('sales','operating','income','eps'):
            for suffix in ('growth_1','growth_4','ttm_growth','cagr3','cagr5'):
                self.assertIn(key+'_'+suffix,cells)
            self.assertIn(key,result['annual'][-1]['growth'])
        self.assertFalse(any('forecast' in key for key in cells))


class EpsBasisRules(unittest.TestCase):
    def test_basic_selected_per_series_without_mixing_and_inputs_preserved(self):
        data=normal();data['annual'][-1]['values']['eps']['value']=None
        data['annual'][-2]['values']['basic_eps']['value']=None
        data['annual'][-3]['values']['eps']['value']=None
        before=copy.deepcopy(data)
        result,cells=summary(data)
        self.assertEqual(result['eps_basis'],dict(annual='basic',quarterly='diluted'))
        self.assertEqual(cells['annual_eps']['value'],4.4)
        self.assertAlmostEqual(cells['pe_annual']['value'],100/4.4)
        self.assertIsNone(result['annual'][-2]['values']['display_eps']['value'])
        self.assertIsNone(result['annual'][-1]['growth']['eps']['value'])
        self.assertEqual(data,before)

    def test_basic_quarter_ttm_and_pe_use_same_basis(self):
        data=normal();data['quarterly'][-1]['values']['eps']['value']=None
        result,cells=summary(data)
        self.assertEqual(result['eps_basis']['quarterly'],'basic')
        self.assertAlmostEqual(cells['eps']['value'],4.4)
        self.assertAlmostEqual(cells['pe']['value'],100/4.4)
        self.assertEqual(cells['pe']['eps_basis'],'basic')
        self.assertEqual(cells['eps_growth_4']['eps_basis'],'basic')
        self.assertEqual(cells['latest_quarter_eps']['value'],1.1)

    def test_recent_visible_coverage_takes_priority_over_old_history(self):
        data=normal()
        for row in data['annual'][:-3]:
            row['values']['eps']['value']=None
        data['annual'][-1]['values']['basic_eps']['value']=None
        result,_=summary(data)
        self.assertEqual(result['eps_basis']['annual'],'diluted')

    def test_equal_recent_coverage_prefers_diluted(self):
        data=normal();data['annual'][-1]['values']['eps']['value']=None
        data['annual'][-2]['values']['basic_eps']['value']=None
        result,_=summary(data)
        self.assertEqual(result['eps_basis']['annual'],'diluted')

    def test_missing_q4_is_not_synthesized_from_annual_eps(self):
        data=normal()
        for row in data['quarterly']:
            if row['date'].endswith('-12-31'):
                row['values']['eps']['value']=None;row['values']['basic_eps']['value']=None
        result,cells=summary(data)
        self.assertIsNone(cells['eps']['value'])
        self.assertEqual(cells['annual_eps']['value'],4)
        self.assertIsNone(next(r for r in result['quarterly'] if r['date']=='2025-12-31')['values']['display_eps']['value'])

    def test_year_end_direct_value_obeys_quarterly_basis(self):
        data=normal();data['quarterly']=[r for r in data['quarterly'] if r['date']<='2025-12-31']
        data['quarterly'][-1]['values']['eps']['value']=None
        data['annual'][-1]['values']['basic_eps']['value']=5
        result,cells=summary(data)
        self.assertEqual(result['eps_basis'],dict(annual='diluted',quarterly='basic'))
        self.assertEqual(cells['eps']['value'],5)
        self.assertEqual(cells['annual_eps']['value'],4)
        self.assertEqual(cells['eps']['eps_basis'],'basic')

    def test_negative_pe_is_loss_label_not_numeric_multiple(self):
        data=normal()
        for row in data['quarterly']:
            row['values']['eps']['value']=-1
        data['annual'][-1]['values']['eps']['value']=-4
        _,cells=summary(data)
        for key in ('pe','pe_annual'):
            self.assertIsNone(cells[key]['value']);self.assertEqual(cells[key]['display_text'],'적자')

    def test_ttm_rejects_mixed_eps_and_revenue_bases(self):
        rows=actual_periods(normal())['quarterly'][-4:]
        rows[-1]['values']['display_eps']['eps_basis']='basic'
        self.assertIsNone(trailing(rows,'display_eps')['value'])
        for row in rows:
            row['values']['sales']['comparison_key']='gross'
        rows[-1]['values']['sales']['comparison_key']='net'
        self.assertIsNone(trailing(rows,'sales')['value'])


class RevenueIntegration(unittest.TestCase):
    def test_sec_same_end_uses_latest_anchor_start_and_matching_accounts(self):
        old=dict(start='2025-01-02',end='2025-12-31',val=90,filed='2026-02-01',form='10-K',accn='old')
        new=dict(old,start='2025-01-01',val=100,filed='2026-03-01',accn='new')
        old_income=dict(old,val=9);new_income=dict(new,val=10)
        source=dict(identity=dict(name='X',cik='1'),facts={'facts':{'us-gaap':{
            'Revenues':{'units':{'USD':[new,old]}},
            'NetIncomeLoss':{'units':{'USD':[old_income,new_income]}},
            'OperatingIncomeLoss':{'units':{'USD':[dict(old,val=8,filed='2026-04-01',accn='operating')]}}}}})
        result=sec_normalize(source)['annual'][0]['values']
        self.assertEqual(result['sales']['value'],100)
        self.assertEqual(result['income']['value'],10)
        self.assertIsNone(result['operating']['value'])
        self.assertEqual(result['operating']['start'],'2025-01-01')
        self.assertIn('시작일 불일치',result['operating']['reason'])
        source['facts']['facts']['us-gaap']['Revenues']['units']['USD']=[old,new]
        self.assertEqual(sec_normalize(source)['annual'][0]['values']['sales']['value'],100)

    def test_sec_same_latest_filing_start_conflict_is_missing(self):
        a=dict(start='2025-01-01',end='2025-12-31',val=100,filed='2026-03-01',form='10-K',accn='same')
        source=dict(identity=dict(name='X',cik='1'),facts={'facts':{'us-gaap':{
            'Revenues':{'units':{'USD':[a,dict(a,start='2025-01-02',val=90)]}}}}})
        result=sec_normalize(source)
        self.assertIsNone(result['annual'][0]['values']['sales']['value'])
        self.assertIn('시작일 충돌',result['warnings'][0])

    def test_sec_nearby_earnings_only_dates_do_not_duplicate_revenue_quarters(self):
        revenue=dict(start='2025-06-01',end='2025-08-31',val=100,filed='2025-10-01',form='10-Q',accn='revenue')
        exact=dict(revenue,val=10)
        nearby=dict(revenue,start='2025-06-02',end='2025-09-01',val=11,accn='nearby')
        separate=dict(revenue,start='2025-09-01',end='2025-11-30',val=12,accn='separate')
        source=dict(identity=dict(name='X',cik='1'),facts={'facts':{'us-gaap':{
            'Revenues':{'units':{'USD':[revenue]}},
            'NetIncomeLoss':{'units':{'USD':[nearby,exact,separate]}}}}})
        result=sec_normalize(source)
        self.assertEqual([row['date'] for row in result['quarterly']],['2025-08-31','2025-11-30'])
        self.assertEqual(result['quarterly'][0]['values']['income']['value'],10)
        self.assertEqual(result['quarterly'][1]['values']['income']['value'],12)
        self.assertIn('겹치는',result['warnings'][0])

    def test_revenue_denominator_metadata_is_visible_on_ratios_and_inputs(self):
        data=normal()
        metadata=dict(basis='bank_net_revenue',comparison_key='bank_net_revenue',
                      account_label='매출(순영업수익)',revenue_note='순영업수익 기준')
        for rows in (data['annual'],data['quarterly']):
            for row in rows:
                row['values']['sales'].update(metadata)
        _,cells=summary(data)
        for key in ('ps','evsales','gross_margin','operating_margin','net_margin'):
            self.assertEqual(cells[key]['basis'],'bank_net_revenue')
            self.assertEqual(cells[key]['inputs'][1]['comparison_key'],'bank_net_revenue')
            self.assertIn('순영업수익 기준',cells[key]['label'])

    def test_dart_revenue_basis_survives_q4_and_blocks_changed_definition(self):
        def report(q,bank):
            row=dict(corp_code='1',sj_div='IS',currency='KRW',rcept_no='20260301000001',
                     account_id='custom' if bank else 'ifrs-full_Revenue',
                     account_nm='총영업이익' if bank else '매출액',
                     thstrm_amount='400' if q==4 else '100',thstrm_add_amount='300')
            rows=[row]
            if bank:
                rows.append(dict(row,account_id='ifrs-full_InterestRevenueExpense',account_nm='순이자손익',thstrm_amount='10'))
            return dict(year=2025,quarter=q,end='2025-12-31' if q==4 else '2025-09-30',prefix=str(q),
                        fs=dict(status='000',list=rows),shares={})
        data=dict(identity=dict(corp_code='1',stock_code='1'),reports=[report(3,True),report(4,True)])
        result=dart_normalize(data)
        cell=result['quarterly'][-1]['values']['sales']
        self.assertEqual(cell['value'],100);self.assertEqual(cell['comparison_key'],'bank_net_revenue')
        self.assertEqual(cell['account_label'],'매출(순영업수익)')
        data['reports'][0]=report(3,False)
        changed=dart_normalize(data)['quarterly'][-1]['values']['sales']
        self.assertIsNone(changed['value']);self.assertIn('기준',changed['reason'])

    def test_sec_earnings_periods_survive_missing_revenue(self):
        yearly=dict(start='2025-01-01',end='2025-12-31',val=20,filed='2026-02-01',form='10-K',accn='x')
        quarter=dict(start='2026-01-01',end='2026-03-31',val=8,filed='2026-05-01',form='10-Q',accn='y')
        source=dict(identity=dict(name='Financial Co',cik='1'),facts={'facts':{'us-gaap':{
            'OperatingIncomeLoss':{'units':{'USD':[yearly,quarter]}}}}})
        result=sec_normalize(source)
        self.assertEqual(len(result['annual']),1);self.assertEqual(len(result['quarterly']),1)
        self.assertEqual(result['quarterly'][0]['values']['operating']['value'],8)
        rendered,cells=summary(result)
        self.assertIsNone(cells['sales']['value'])
        self.assertEqual(len(rendered['quarterly']),1)

    def test_sec_quarter_subtraction_rejects_revenue_definition_change(self):
        q1=dict(start='2025-01-01',end='2025-03-31',val=30,filed='2025-05-01',form='10-Q',accn='q1')
        ytd=dict(start='2025-01-01',end='2025-06-30',val=100,filed='2025-08-01',form='10-Q',accn='q2')
        source=dict(identity=dict(name='Bank',cik='1'),facts={'facts':{'us-gaap':{
            'Revenues':{'units':{'USD':[q1]}},
            'RevenuesNetOfInterestExpense':{'units':{'USD':[ytd]}}}}})
        result=sec_normalize(source)
        q2=next(r for r in result['quarterly'] if r['date']=='2025-06-30')['values']['sales']
        self.assertIsNone(q2['value']);self.assertIn('기준',q2['reason'])


if __name__=='__main__':unittest.main()
