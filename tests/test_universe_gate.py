import copy
import unittest
from invest_agent.trading.universe_gate import evaluate, EXCHANGE_FAMILIES
from invest_agent.trading.market import usable_tail, screen
from test_sperandeo import fixture, snapshot


class UniverseTests(unittest.TestCase):
    def test_listing_preserves_raw_usd_cap_not_thousands_display(self):
        from unittest.mock import patch, Mock
        from invest_agent.trading.universe import listing_rows
        raw={'stocks':[{'symbolCode':'SYNTH','stockNameEng':'Synthetic Corp', 'marketValue':'2,000,000',
              'marketValueRaw':'2000000000','currencyType':{'name':'USD'},'stockExchangeType':{'code':'NSQ'},
              'localTradedAt':'2026-09-11T16:00:00-04:00','tradableStatus':'tradable'}], 'totalCount':1}
        with patch('requests.get',return_value=Mock(json=lambda:raw)):
            result=listing_rows('NASDAQ')
        self.assertEqual(result[0]['MarketCap'],2_000_000_000)
        self.assertEqual(result[0]['Exchange'],'NSQ')
        self.assertEqual(result[0]['Currency'],'USD')
        self.assertEqual(result[0]['CapAsOf'],raw['stocks'][0]['localTradedAt'])
        raw['stocks'][0].pop('marketValueRaw')
        with patch('requests.get',return_value=Mock(json=lambda:raw)):
            self.assertIsNone(listing_rows('NASDAQ')[0]['MarketCap'])
    def test_us_boundaries_exchange_and_missing_cap(self):
        bars=fixture();
        for b in bars: b.update(close=100,volume=200_000)
        source=snapshot(bars)['series'][0]
        for code in ('NYQ','NYSE','NMS','NGM','NCM','NASDAQ'):
            with self.subTest(exchange=code):
                self.assertTrue(evaluate({**source,'raw_exchange_code':code},bars)['universe_pass'])
        for edit,reason in [({'market_cap':2e9-1},'market_cap_below_minimum'),
                ({'market_cap':None},'unavailable_market_cap'),({'market_cap_currency':'KRW'},'unavailable_market_cap'),
                ({'raw_exchange_code':'OTC'},'ineligible_exchange'),({'raw_exchange_code':'ASE'},'ineligible_exchange'),
                ({'tradable':False},'ineligible_instrument_or_not_tradable'),({'instrument_type':'ETF'},'ineligible_instrument_or_not_tradable')]:
            with self.subTest(edit=edit):
                self.assertIn(reason,evaluate({**source,**edit},bars)['universe_rejection_reasons'])
        self.assertTrue(evaluate({**source,'market_cap':3e12},bars)['universe_pass'])
        bars[-1]['volume']-=1
        self.assertIn('liquidity_below_minimum',evaluate(source,bars)['universe_rejection_reasons'])

    def test_kr_boundaries_no_cap_floor_and_currency(self):
        bars=fixture()
        for b in bars:b.update(close=100_000,volume=20_000)
        source={'market':'KR','currency':'KRW','raw_exchange_code':'KOSPI','market_cap':None}
        self.assertTrue(evaluate(source,bars)['universe_pass'])
        for exchange in ('KOSDAQ','KONEX'):
            self.assertFalse(evaluate({**source,'raw_exchange_code':exchange},bars)['universe_pass'])
        bars[-1]['volume']-=1
        self.assertFalse(evaluate(source,bars)['universe_pass'])
        self.assertIn('unavailable_liquidity_currency',evaluate({**source,'currency':'USD'},bars)['universe_rejection_reasons'])

    def test_completed_20_bar_contract(self):
        for market,exchange,currency in [('KR','KOSPI','KRW'),('US','NYSE','USD')]:
            b=fixture(); s=snapshot(b)['series'][0]
            s.update(market=market,raw_exchange_code=exchange,currency=currency)
            self.assertIn('unavailable_insufficient_liquidity_bars',evaluate(s,b[-19:])['universe_rejection_reasons'])
            days=[v['date'] for v in b]
            future=dict(b[-1],date='2099-01-01',volume=1e20)
            self.assertEqual(evaluate(s,usable_tail(b+[future],days)),evaluate(s,b))
            s['traded_value_basis']='provider_daily_local_currency'
            for row in b:row['traded_value']=2e9 if market=='KR' else 20e6
            self.assertTrue(evaluate(s,b)['universe_pass'])
            b[-1]['traded_value']=None
            self.assertIn('unavailable_liquidity_inputs',evaluate(s,b)['universe_rejection_reasons'])

    def test_audit_and_metadata_not_symbol_guessed(self):
        data=snapshot(fixture()); data['series'][0]['raw_exchange_code']=None
        result=screen(data)
        self.assertEqual(result['universe_audit']['US']['passed_count'],0)
        self.assertEqual(result['universe_audit']['US']['rejection_counts']['unavailable_exchange'],1)
        self.assertEqual(EXCHANGE_FAMILIES['NMS'],'NASDAQ')
