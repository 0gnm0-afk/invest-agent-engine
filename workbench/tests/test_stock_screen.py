import sys
import copy
import unittest
from pathlib import Path
from datetime import date, timedelta
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from invest_agent.trading.market import screen
from stock_screen import screen_market


class ParallelTest(unittest.TestCase):
    def test_exact_engine_result_with_errors_and_mixed_markets(self):
        days = [(date(2025,1,1)+timedelta(days=n)).isoformat() for n in range(270)]
        series, universe = [], []
        for market,symbol,currency,price in [('US','TEST','USD',100),('KR','000000.KS','KRW',10000)]:
            metadata = {'market':market,'symbol':symbol,'name':'test','currency':currency,
                        'benchmark':'BENCH','price_basis':'fixture','market_cap':3e9,
                        'market_cap_currency':currency,'market_cap_as_of':days[-1],
                        'raw_exchange_code':'NYSE' if market=='US' else 'KOSPI','tradable':True}
            bars = [{'date':d,'open':price+i%17,'close':price+i%17,'high':price+i%17+1,
                     'low':price+i%17-1,'volume':1e9} for i,d in enumerate(days)]
            universe.append(metadata)
            series.append({**metadata,'bars':bars})
        value = {'series':series,'universe':universe,'source':'fixture','profile':{},
                 'sessions':{'KR':days,'US':days},'benchmarks':{},'errors':[{'market':'US','symbol':'FAIL','reason':'missing'}],
                 'coverage':{'scope_errors':[]},'fetched_at':'2026-09-25T00:00:00+00:00'}
        before = copy.deepcopy(value)
        self.assertEqual(screen_market(value,workers=2,chunk_size=1),screen(value))
        self.assertEqual(value,before)


if __name__=='__main__':
    unittest.main()
