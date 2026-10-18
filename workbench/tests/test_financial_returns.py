import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from financial_model import fact
from financial_returns import attach_returns,reporting_currency
from financial_sec_tables import quarter_facts,distribution_zeros
from financial_focus import summarize
from test_financial_focus import normal


class ReturnsTests(unittest.TestCase):
    def values(self,income=100):
        def c(v):return fact(v,unit='USD',start='2025-01-01',end='2025-12-31',filed='2026-02-01',accession='a',sources=['official'])
        return dict(cash_dividends=c(20),buybacks=c(30),income=c(income))

    def test_actual_cash_sum_and_same_period_rate(self):
        v=self.values();m={};attach_returns(v,m,'USD')
        self.assertEqual(v['shareholder_return']['value'],50);self.assertEqual(m['shareholder_payout']['value'],.5)
        self.assertEqual(m['shareholder_payout']['inputs'][1]['value'],100)

    def test_loss_preserves_cash_but_has_no_rate(self):
        for income in [0,-10]:
            v=self.values(income);m={};attach_returns(v,m,'USD')
            self.assertEqual(v['shareholder_return']['value'],50);self.assertEqual(m['shareholder_payout']['status'],'not_computable')

    def test_missing_is_not_zero_or_authorized_buyback(self):
        v=self.values();v['buybacks']['value']=None;v['authorized_buyback']=fact(500);m={};attach_returns(v,m,'USD')
        self.assertIsNone(v['shareholder_return']['value']);self.assertIsNone(m['shareholder_payout']['value'])

    def test_missing_income_preserves_amounts(self):
        v=self.values(None);m={};attach_returns(v,m,'USD')
        self.assertEqual(v['shareholder_return']['value'],50)
        self.assertIsNone(m['shareholder_payout']['value'])

    def test_three_comparative_years_and_q4_q3_q4_columns(self):
        f=dict(form='6-K',filingDate='2026-02-01',accessionNumber='a')
        head='<tr><td>Three Months Ended December 31, Year Ended December 31,</td></tr><tr>'+''.join('<td>'+y+'</td>' for y in ['2025','2024','2023']*2)+'</tr>'
        row='<tr><td>Net income per ordinary share-basic</td>'+''.join('<td>'+str(n)+'</td>' for n in range(1,7))+'</tr>'
        raw=('<table>'+head+row+row.replace('basic','diluted')+'</table>').encode()
        out=quarter_facts(raw,'2025-12-31',f,'x')
        self.assertEqual({x['end']:x['val'] for x in out if x['tag']=='EarningsPerShareBasic'}, {'2025-12-31':1,'2024-12-31':2,'2023-12-31':3})
        head='<tr><td>Three Months Ended December 31, September 30, December 31, Year Ended December 31, December 31,</td></tr><tr>'+''.join('<td>'+y+'</td>' for y in ['2025','2025','2024','2025','2024'])+'</tr>'
        row='<tr><td>Net income per common share-basic</td>'+''.join('<td>'+str(n)+'</td>' for n in range(1,6))+'</tr>'
        raw=('<table>'+head+row+row.replace('basic','diluted')+'</table>').encode()
        out=quarter_facts(raw,'2025-12-31',f,'x')
        self.assertEqual({x['end']:x['val'] for x in out if x['tag']=='EarningsPerShareBasic'}, {'2025-12-31':1,'2024-12-31':3})

    def test_scope_currency_period_and_filing_conflicts(self):
        for field,bad in [('unit','JPY'),('start','2025-04-01'),('accession','other'),('accounting_basis','separate')]:
            v=self.values();v['buybacks'][field]=bad;m={};attach_returns(v,m,'USD')
            self.assertIsNone(v['shareholder_return']['value'],field)

    def test_common_profit_precedes_parent_and_consolidated(self):
        v=self.values();v['common_income']=dict(v['income'],value=80);v['net_income']=dict(v['income'],value=200);m={};attach_returns(v,m,'USD')
        self.assertEqual(m['shareholder_payout']['value'],.625)

    def test_icr_absent_for_both_markets(self):
        for provider in ['DART','SEC']:
            result=summarize(normal(provider),{})
            self.assertNotIn('icr',[c['id'] for c in result['summary']])
            for frame in ['annual','quarterly']:
                for row in result[frame]:self.assertNotIn('icr',row['metrics'])

    def test_reporting_currency_is_not_assumed_usd(self):
        def src(units):return {'facts':{'facts':{'us-gaap':{'Revenues':{'units':units}}}}}
        self.assertEqual(reporting_currency(src({'JPY':[{'form':'20-F'}]})),'JPY')
        with self.assertRaises(ValueError):reporting_currency(src({'JPY':[{'form':'20-F'}],'USD':[{'form':'20-F'}]}))

    def test_dividend_currency_typo_requires_exact_original_label(self):
        from financial_sec_filing import inline_facts
        raw='''<xbrli:context id="c"><xbrli:entity><xbrli:identifier>1</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:startdate>2024-07-01</xbrli:startdate><xbrli:enddate>2025-06-30</xbrli:enddate></xbrli:period></xbrli:context><xbrli:unit id="u"><xbrli:divide><xbrli:unitnumerator><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unitnumerator><xbrli:unitdenominator><xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unitdenominator></xbrli:divide></xbrli:unit><p>Cash dividends declared (¥ <ix:nonfraction name="us-gaap:CommonStockDividendsPerShareDeclared" contextref="c" unitref="u">3</ix:nonfraction> per share)</p>'''
        f=dict(filingDate='2025-10-30',accessionNumber='a',form='20-F')
        rows=inline_facts(raw.encode(),'1',f,'x','JPY')
        self.assertEqual((rows[0]['unit'],rows[0]['val']),('JPY/shares',3))
        self.assertEqual(rows[0]['evidence']['original_unit'],'USD/shares')
        self.assertEqual(inline_facts(raw.replace('¥','$').encode(),'1',f,'x','JPY'),[])

    def test_dividend_exception_cannot_be_zero(self):
        f=dict(reportDate='2025-12-31',filingDate='2026-02-01',accessionNumber='a')
        self.assertEqual(distribution_zeros(b'<p>We have not paid cash dividends other than as described herein.</p>',f,'x'),[])
        good=distribution_zeros(b'<p>We have never paid cash dividends on our common stock.</p>',f,'x')
        self.assertEqual(good[0]['fields'],['cash_dividends','dividend'])
        inverse=distribution_zeros(b'<p>We have not paid or declared any dividends on our common stock.</p>',f,'x')
        self.assertEqual(inverse[0]['fields'],['cash_dividends','dividend'])

    def test_explicit_buyback_none_is_not_future_plan(self):
        f=dict(reportDate='2025-06-30',filingDate='2025-10-30',accessionNumber='a')
        good=distribution_zeros(b'<p>Item 16E. PURCHASES OF EQUITY SECURITIES BY THE ISSUER AND AFFILIATED PURCHASERS None.</p>',f,'x')
        self.assertEqual(good[0]['start'],'2024-07-01');self.assertEqual(good[0]['fields'],['buybacks'])
        self.assertEqual(distribution_zeros(b'<p>We do not intend to repurchase any shares.</p>',f,'x'),[])

    def test_ascending_years_and_eps_suffix(self):
        raw=b'''<p>In thousands, except per share amounts</p><table><tr><th>Three Months Ended December 31, Year Ended December 31,</th></tr><tr><td>2023</td><td>2024</td><td>2023</td><td>2024</td></tr><tr><td>Operating income</td><td>10</td><td>20</td><td>40</td><td>80</td></tr><tr><td>Net income per common share-basic</td><td>0.1</td><td>0.2</td><td>0.4</td><td>0.8</td></tr><tr><td>Net income per common share-diluted</td><td>0.09</td><td>0.19</td><td>0.39</td><td>0.79</td></tr></table>'''
        f=dict(form='8-K',filingDate='2025-02-01',accessionNumber='a')
        out=quarter_facts(raw,'2024-12-31',f,'x')
        self.assertEqual(next(x['val'] for x in out if x['end']=='2023-12-31' and x['tag']=='OperatingIncomeLoss'),10000)
        self.assertEqual(next(x['val'] for x in out if x['end']=='2024-12-31' and x['tag']=='EarningsPerShareDiluted'),.19)
        self.assertFalse(quarter_facts(raw.replace(b'Operating income',b'Non-GAAP Operating income'),'2024-12-31',f,'x'))

if __name__=='__main__':unittest.main()
