"""Issuer-independent regression contracts; arithmetic expectations are independent."""
import copy
from datetime import date
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import financial_model as m
from financial_revenue import select_sec_revenue

class FrozenDate(date):
    @classmethod
    def today(cls):return cls(2026,10,4)

def row(value,start='2025-01-01',end='2025-12-31',filed='2026-02-01',accn='new'):
    return dict(val=value,start=start,end=end,filed=filed,accn=accn,form='10-K')

def source(tags):
    return dict(provider='SEC',identity=dict(name='Anonymous issuer',cik='123'),facts=dict(cik=123,facts={'us-gaap':tags}))

def tag(rows,unit='USD'):return {'units':{unit:rows}}

class CommonPipeline(unittest.TestCase):
    def setUp(self):
        self.clock=patch.object(m,'date',FrozenDate);self.clock.start();self.addCleanup(self.clock.stop)

    def test_proven_revenue_alias_retains_latest_restatement(self):
        old=row(23480705,filed='2022-05-01',accn='old',start='2021-01-01',end='2021-12-31')
        revised=dict(old,val=21763181,filed='2023-05-01',accn='new')
        data={'Revenues':tag([old]),'RevenueFromContractWithCustomerExcludingAssessedTax':tag([old,revised])}
        self.assertEqual(select_sec_revenue(data,cutoff='2020-01-01',as_of='2026-10-04')[0]['val'],21763181)
        # A smaller component never acquires total-revenue identity merely by a newer filing.
        data['RevenueFromContractWithCustomerExcludingAssessedTax']=tag([dict(old,val=100),revised])
        self.assertEqual(select_sec_revenue(data,cutoff='2020-01-01',as_of='2026-10-04')[0]['val'],23480705)

    def test_restatement_lineage_blocks_unlinked_five_year_base(self):
        values=[]
        for year,value in [(2020,100),(2021,110),(2022,120)]:
            values.append(row(value,f'{year}-01-01',f'{year}-12-31','2023-02-01','old'))
        for year,value in [(2021,220),(2022,240),(2023,260)]:
            values.append(row(value,f'{year}-01-01',f'{year}-12-31','2024-02-01','restated'))
        for year,value in [(2023,260),(2024,280),(2025,300)]:
            values.append(row(value,f'{year}-01-01',f'{year}-12-31','2026-02-01','latest'))
        raw=source({'EarningsPerShareDiluted':tag(values,'USD/shares')});before=copy.deepcopy(raw)
        rows=m.sec_normalize(raw)['annual'];eps={r['date'][:4]:r['values']['eps'] for r in rows}
        self.assertIsNone(m.growth(eps['2025'],eps['2020'],years=5)['value'])
        self.assertAlmostEqual(m.growth(eps['2025'],eps['2022'],years=3)['value'],(300/240)**(1/3)-1)
        self.assertEqual(eps['2022']['revision_history'][0]['value'],120)
        self.assertEqual(raw,before)

    def test_paid_and_declared_dividends_stay_separate(self):
        spans=[('2025-01-01','2025-03-31'),('2025-04-01','2025-06-30'),('2025-07-01','2025-09-30'),('2025-10-01','2025-12-31')]
        paid=[row(v,a,b) for (a,b),v in zip(spans,[.115,.115,.115,.15])]
        declared=[dict(r,val=.2) for r in paid[1:]]
        raw=source({'Revenues':tag([row(100,a,b) for a,b in spans]),
            'CommonStockDividendsPerShareCashPaid':tag(paid,'USD/shares'),
            'CommonStockDividendsPerShareDeclared':tag(declared,'USD/shares')})
        rows=m.sec_normalize(raw)['quarterly']
        self.assertAlmostEqual(m.trailing(rows,'dividend')['value'],.495)
        self.assertEqual(m.trailing(rows,'dividend')['dividend_basis'],'paid')
        self.assertEqual(rows[-1]['values']['dividend_declared']['value'],.2)

    def test_period_difference_requires_actual_comparable_ytd(self):
        a=m.fact(30,unit='USD',start='2025-01-01',end='2025-06-30')
        b=m.fact(10,unit='USD',start='2025-01-01',end='2025-03-31')
        result=m.period_difference('YTD difference',a,b,'USD')
        self.assertEqual(result['value'],20);self.assertEqual(result['start'],'2025-04-01')
        for altered in (dict(a,period_coverage='unknown'),dict(a,start='2025-02-01'),dict(a,unit='KRW'),dict(a,accounting_basis_verified=False)):
            self.assertIsNone(m.period_difference('YTD difference',altered,b,'USD')['value'])

    def test_ttm_rejects_ytd_overlap_and_unit_mixing(self):
        spans=[('2025-01-01','2025-03-31'),('2025-04-01','2025-06-30'),('2025-07-01','2025-09-30'),('2025-10-01','2025-12-31')]
        rows=[dict(date=b,values={'sales':m.fact(10,unit='USD',start=a,end=b)}) for a,b in spans]
        self.assertEqual(m.trailing(rows,'sales')['value'],40)
        for changes in ({'start':'2025-01-01'},{'start':'2025-03-31'},{'unit':'KRW'}):
            bad=copy.deepcopy(rows);bad[1]['values']['sales'].update(changes)
            self.assertIsNone(m.trailing(bad,'sales')['value'])

    def test_short_year_is_not_ttm(self):
        cell=m.fact(10,unit='USD',start='2025-12-01',end='2025-12-31')
        rows=[dict(date=cell['end'],values={'sales':cell})]
        self.assertIsNone(m.actual_ttm(rows,rows,'sales')['value'])

    def test_missing_cause_survives_calculation(self):
        raw=m.fact(reason='unverified split',sources=['official-source'])
        result=m.calc('price / EPS',[m.fact(100),raw],lambda a,b:a/b)
        self.assertIsNone(result['value'])
        self.assertEqual(result['missing_inputs'][0]['reason'],'unverified split')
        self.assertEqual(result['missing_inputs'][0]['sources'],['official-source'])
        derived=m.calc('second calculation',[result],lambda x:x)
        self.assertEqual(derived['missing_inputs'][0]['causes'][0]['reason'],'unverified split')

    def test_official_decimal_precision_distinguishes_rounding(self):
        old=dict(row(27950687,'2024-01-01','2024-12-31','2025-02-01','old'),decimals=0)
        rounded=dict(old,val=27951000,filed='2026-02-01',accn='new',decimals=-3)
        raw=source({'Revenues':tag([old,rounded])})
        cell=m.sec_normalize(raw)['annual'][0]['values']['sales']
        self.assertEqual(cell['value'],27951000)
        self.assertTrue(cell['revision_history'][0]['explained_by_split_or_rounding'])
        self.assertNotIn('revision_basis',cell)
        # A real difference beyond declared rounding remains a revision.
        rounded['val']=27952000
        cell=m.sec_normalize(raw)['annual'][0]['values']['sales']
        self.assertFalse(cell['revision_history'][0]['explained_by_split_or_rounding'])

    def test_revised_annual_cannot_subtract_old_nine_month_value(self):
        a=m.fact(100,unit='USD',start='2025-01-01',end='2025-12-31',filed='2026-02-01',accession='new',revision_history=[{'value':90,'explained_by_split_or_rounding':False}])
        b=m.fact(60,unit='USD',start='2025-01-01',end='2025-09-30',filed='2025-11-01',accession='old')
        self.assertIsNone(m.period_difference('Q4',a,b,'USD')['value'])
        self.assertEqual(m.period_difference('Q4',a,dict(b,filed=a['filed'],accession=a['accession']),'USD')['value'],40)

    def test_statement_capital_does_not_mix_vintages(self):
        a=m.fact(100,unit='USD',end='2025-12-31',filed='2026-02-01',accession='a')
        b=m.fact(10,unit='USD',end='2025-12-31',filed='2026-02-01',accession='a')
        self.assertEqual(m.statement_calc('equity - preferred',[a,b],lambda x,y:x-y,'USD')['value'],90)
        self.assertIsNone(m.statement_calc('equity - preferred',[a,dict(b,accession='b')],lambda x,y:x-y,'USD')['value'])

if __name__=='__main__':unittest.main()
