import copy
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from display_format import money, price_text, display_analysis
from watch_review import table

class CurrencyDisplay(unittest.TestCase):
    def test_currency_precision_and_missing(self):
        for value,currency,expected in [(1234.5,'KRW','1,235'),(-1234.5,'KRW','-1,235'),
            (1234.5,'USD','1,234.50'),(0,'KRW','0'),(0,'USD','0.00'),(-.001,'USD','0.00'),
            (None,'KRW','미확정')]:
            self.assertEqual(money(value,currency),expected)

    def test_prose_keeps_percent_dates_and_ratios(self):
        self.assertEqual(price_text('하단 1234.50 / 상단 2000.00 · 하단 거리 3.5% · 2.00배 · 2026-09-30','KRW'),
            '하단 1,235 / 상단 2,000 · 하단 거리 3.5% · 2.00배 · 2026-09-30')

    def test_detail_does_not_mutate_or_round_ratios(self):
        source={'pivot':1234.5,'features':{'ma':{'20':1200.5}},'structure_risk_pct':.03456,'volume':1234.5,'close':None}
        original=copy.deepcopy(source)
        result=display_analysis(source,'KRW')
        self.assertEqual(result['pivot'],'1,235')
        self.assertEqual(result['features']['ma']['20'],'1,201')
        self.assertEqual(result['structure_risk_pct'],.03456)
        self.assertIsNone(result['close'])
        self.assertEqual(result['volume'],1234.5)
        self.assertEqual(source,original)

    def test_report_market_currency_and_input_preserved(self):
        signal={'phase':'watch','pivot':1234.5,'structure_stop':1100.5,'pivot_gap_pct':.01,'structure_risk_pct':.03,'pending':[],'warnings':[],'label':'관찰'}
        entry={'market':'KR','symbol':'test','name':'표시검사','selected':True,'phase':'watch','origins':['pdf_momentum'],'entry_signal':signal}
        before=copy.deepcopy(entry)
        self.assertIn('1,235<br>',table([entry],{}))
        self.assertEqual(entry,before)
        entry['market']='US'
        self.assertIn('1,234.50<br>',table([entry],{}))

if __name__=='__main__':unittest.main()
