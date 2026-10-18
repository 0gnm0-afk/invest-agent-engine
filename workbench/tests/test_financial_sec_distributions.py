import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from financial_sec_distributions import financing_zeros,preferred_paid,common_paid_from_total


class Distributions(unittest.TestCase):
    def test_zero_requires_exhaustive_reconciled_financing_rows(self):
        f=dict(filingDate='2026-02-01',accessionNumber='a')
        anchor=dict(tag='NetCashProvidedByUsedInFinancingActivities',unit='USD',context='c',start='2025-01-01',end='2025-12-31',val=30)
        def node(n):return '<ix:nonfraction contextref="c" unitref="usd">'+str(n)+'</ix:nonfraction>'
        raw=('<table><tr><td>Financing activities:</td></tr><tr><td>Drawdown of credit lines and loan facilities</td><td>'+node(50)+'</td></tr><tr><td>Repayment of credit lines and loan facilities</td><td>('+node(20)+')</td></tr><tr><td>Net cash provided by financing activities</td><td>'+node(30)+'</td></tr></table>').encode()
        result=financing_zeros(raw,[anchor],f,'x','USD')
        self.assertEqual(result[0]['fields'],['cash_dividends','dividend','buybacks'])
        self.assertEqual(financing_zeros(raw,[dict(anchor,val=29)],f,'x','USD'),[])
        self.assertEqual(financing_zeros(raw.replace(b'Drawdown of credit lines and loan facilities',b'Other financing activities'),[anchor],f,'x','USD'),[])
        self.assertEqual(financing_zeros(raw,[dict(anchor,unit='JPY')],f,'x','USD'),[])
        self.assertEqual(financing_zeros(raw.replace(b'contextref="c"',b'contextref="subsidiary"'),[anchor],f,'x','USD'),[])

    def test_cash_total_less_paid_preferred_not_declared_common(self):
        b=dict(unit='USD',filed='2026-02-01',accn='a',source_id='filing',start='2025-01-01',end='2025-12-31')
        facts=[dict(b,tag='PaymentsOfOrdinaryDividends',val=192133),dict(b,tag='DividendsPreferredStockCash',val=11868),dict(b,tag='DividendsCommonStockCash',val=180190)]
        self.assertEqual(common_paid_from_total(facts),[])
        raw=b'<p>During 2025, we declared and paid dividends on our outstanding shares of Series A Preferred Stock of $687.52 per share.</p>'
        out=common_paid_from_total([*facts,*preferred_paid(raw,facts)])
        self.assertEqual(out[0]['val'],180265)
        self.assertEqual(out[0]['evidence']['total']['val'],192133)

    def test_preferred_payments_need_complete_nonoverlapping_period(self):
        b=dict(unit='USD',filed='2026-02-01',accn='a',source_id='filing')
        total=dict(b,start='2025-01-01',end='2025-06-30',tag='PaymentsOfOrdinaryDividends',val=50)
        q1=dict(b,start='2025-01-01',end='2025-03-31',tag='PaymentsOfDividendsPreferredStockAndPreferenceStock',val=3)
        q2=dict(q1,start='2025-04-01',end='2025-06-30')
        self.assertEqual(common_paid_from_total([total,q1]),[])
        self.assertEqual(common_paid_from_total([total,q1,q2])[0]['val'],44)
        self.assertEqual(common_paid_from_total([total,q1,dict(q2,unit='JPY')]),[])

if __name__=='__main__':unittest.main()
